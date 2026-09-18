import os
import threading

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..memory import db, remind
from ..talk import chat, llm, presence
from . import admin, jobs, voice

STATIC_DIR = Path(__file__).resolve().parent / "static"
# start.bat はこの終了コードを見て起動し直す。42以外は普通の終了として扱われる。
RESTART_EXIT_CODE = 42
# 通話中の端末はこの間隔より短く生存を知らせる。途絶えたら通話は終わったとみなす。
CALL_STALE_SECONDS = 120
# 記憶本文の上限。整理が作るときのエピソード側に合わせてある。
MEMORY_TEXT_LIMIT = 400

app = FastAPI(title="kotoha")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _check_token(request: Request) -> None:
    token = request.headers.get("X-Kotoha-Token", "")
    if not config.WEB_TOKEN or token != config.WEB_TOKEN:
        raise HTTPException(status_code=401, detail="トークンが無効")













































@app.get("/")
def index():
    """入口。台本のURLに更新時刻を足して、古いものを握らせない。"""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        stamp = int((STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/OneSignalSDKWorker.js")
def onesignal_worker():
    """OneSignalが根元に置くよう求めるファイル。中身は読み込みの1行だけ。"""
    return Response(
        'importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");',
        media_type="application/javascript",
    )


@app.get("/api/push")
def push_settings(request: Request):
    """画面に、通知を出せる状態かどうかと、つなぎ先を教える。"""
    _check_token(request)
    return {"appId": config.ONESIGNAL_APP_ID if config.PUSH_ENABLED else ""}


@app.get("/api/history")
def history(request: Request, limit: int = config.WEB_HISTORY_LIMIT, after: int = 0):
    """会話の履歴。`after` を渡すと、その番号より後ろだけを古い順に返す。

    開いたままの画面が、ことはのほうからの発言に気づくための道。全部を
    読み直させると、そのたびに画面を組み直すことになる。
    """
    _check_token(request)
    with db.session() as conn:
        if after:
            rows = conn.execute(
                "SELECT id, role, text, created_at FROM messages "
                "WHERE id > ? ORDER BY id LIMIT ?", (after, limit),
            ).fetchall()
            return JSONResponse([dict(r) for r in rows])
        rows = conn.execute(
            "SELECT id, role, text, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return JSONResponse([dict(r) for r in reversed(rows)])


@app.post("/api/chat")
def api_chat(request: Request, payload: dict):
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    with jobs.turn_lock, db.session() as conn:
        try:
            turn = chat.run_turn(conn, text)
        except llm.LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
        # いま増えたぶんまで画面の目印を進める。これが無いと、次の見に行きで
        # 自分が送ったばかりの往復をもう一度拾って、二重に並ぶ。
        last_id = conn.execute("SELECT MAX(id) AS id FROM messages").fetchone()["id"]
    answer = {"reply": turn.reply, "mode": turn.mode, "last_id": last_id}
    if turn.kept:
        # 預かったことを画面にも出す。ことはの言葉は変えず、印だけ足す。
        answer["kept"] = [{"due_at": when.strftime(remind.STAMP), "text": what}
                          for when, what in turn.kept]
    return answer


def _call_owner(conn):
    """いま通話している端末。見張りが途絶えた記録は無効として扱う。"""
    owner = db.get_state(conn, db.CALL_OWNER)
    if not owner:
        return None
    if db.overdue(conn, db.CALL_SEEN_AT, CALL_STALE_SECONDS):
        return None
    return owner


@app.get("/api/call")
def call_state(request: Request, device: str = ""):
    """通話を続けてよいか確かめる。持ち主が入れ替わっていれば false。"""
    _check_token(request)
    with db.session() as conn:
        owner = _call_owner(conn)
        if owner and owner == device:
            db.set_state(conn, db.CALL_SEEN_AT, db.now_utc())
            conn.commit()
        return {"calling": bool(owner), "mine": owner == device if owner else False}


@app.post("/api/call")
def call_claim(request: Request, payload: dict):
    """通話を始める。先に話していた端末があれば、その端末は次の確認でやめる。"""
    _check_token(request)
    device = (payload.get("device") or "").strip()
    if not device:
        raise HTTPException(status_code=400, detail="端末の指定がない")
    with db.session() as conn:
        previous = _call_owner(conn)
        db.set_state(conn, db.CALL_OWNER, device)
        db.set_state(conn, db.CALL_SEEN_AT, db.now_utc())
        conn.commit()
        return {"calling": True, "mine": True, "took_over": bool(previous and previous != device)}


@app.delete("/api/call")
def call_release(request: Request):
    """通話を終わらせる。自分の端末でも、置いてきた端末でも同じ。"""
    _check_token(request)
    with db.session() as conn:
        released = bool(_call_owner(conn))
        db.set_state(conn, db.CALL_OWNER, "")
        db.set_state(conn, db.CALL_SEEN_AT, "")
        conn.commit()
        return {"calling": False, "mine": False, "released": released}


@app.post("/api/restart")
def api_restart(request: Request):
    """外出先から立て直すための最後の手段。start.bat が起動し直す。"""
    _check_token(request)
    # 先に応答を返しきってから落とす。DBへの書き込みはその都度コミットしてある。
    threading.Timer(0.4, lambda: os._exit(RESTART_EXIT_CODE)).start()
    return {"restarting": True}


MEMORY_LISTS = {
    "semantic": ("layer = 'semantic'", "confirmed_at DESC"),
    "episode": ("layer = 'episode'", "occurred_at DESC"),
    "pinned": ("pinned = 1", "confirmed_at DESC"),
}
MEMORY_LIST_LIMIT = 40
# 預かりは滅多に溜まらない。溜まっていたら、それ自体が知らせるべきこと。
REMINDER_LIST_LIMIT = 50


@app.get("/api/memories")
def memories_list(request: Request, kind: str = "semantic"):
    _check_token(request)
    if kind not in MEMORY_LISTS:
        raise HTTPException(status_code=400, detail="その一覧はありません")
    where, order = MEMORY_LISTS[kind]
    with db.session() as conn:
        rows = conn.execute(
            f"SELECT id, layer, kind, text, occurred_at, confirmed_at, expires_at, pinned "
            f"FROM memory_nodes WHERE {where} ORDER BY {order} LIMIT ?",
            (MEMORY_LIST_LIMIT,),
        ).fetchall()
        return {"memories": [dict(r) for r in rows]}


@app.get("/api/memories/{node_id}")
def memory_detail(request: Request, node_id: int):
    _check_token(request)
    with db.session() as conn:
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="その記憶はありません")
        tags = [
            r["tag"] for r in
            conn.execute("SELECT tag FROM memory_tags WHERE node_id = ? ORDER BY tag", (node_id,))
        ]
        sources = [
            r["message_id"] for r in
            conn.execute("SELECT message_id FROM memory_sources WHERE node_id = ?", (node_id,))
        ]
        return {"memory": dict(row), "tags": tags, "sources": sources}


@app.put("/api/memories/{node_id}")
def memory_update(request: Request, node_id: int, payload: dict):
    """本文を直す。整理の updates と同じく、確認日時を進めて期限も延ばす。"""
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="本文が空")
    if len(text) > MEMORY_TEXT_LIMIT:
        raise HTTPException(status_code=400, detail=f"{MEMORY_TEXT_LIMIT}字までにしてください")
    with db.session() as conn:
        changed = conn.execute(
            f"UPDATE memory_nodes SET text = ?, confirmed_at = ?, {db.EXTEND_EXPIRES} WHERE id = ?",
            (text, db.now_utc(), *db.extend_args(), node_id),
        ).rowcount
        if not changed:
            raise HTTPException(status_code=404, detail="その記憶はありません")
        # 手で直したものは、想起の近道をいったん解く。
        conn.execute("UPDATE memory_tags SET use_count = 0 WHERE node_id = ?", (node_id,))
        conn.commit()
        return {"saved": True}


@app.put("/api/memories/{node_id}/pinned")
def memory_pin(request: Request, node_id: int, payload: dict):
    _check_token(request)
    with db.session() as conn:
        if not db.set_pinned(conn, node_id, bool(payload.get("pinned"))):
            raise HTTPException(status_code=404, detail="その記憶はありません")
        return {"pinned": bool(payload.get("pinned"))}


@app.delete("/api/memories/{node_id}")
def memory_delete(request: Request, node_id: int):
    """保護されていても消す。CUI の /forget と同じ扱い。"""
    _check_token(request)
    with db.session() as conn:
        if not db.forget_node(conn, node_id):
            raise HTTPException(status_code=404, detail="その記憶はありません")
        return {"deleted": True}


@app.post("/api/reminders/snooze")
def remind_snooze(request: Request, payload: dict):
    """通知の「あとで」から呼ばれる。同じ用件を、少し先へ置き直す。"""
    _check_token(request)
    ids = [i for i in payload.get("ids") or [] if isinstance(i, int)]
    if not ids:
        raise HTTPException(status_code=400, detail="番号がない")
    with db.session() as conn:
        moved, due = remind.snooze(conn, ids, config.SNOOZE_MINUTES)
    if not moved:
        raise HTTPException(status_code=404, detail="その頼まれごとはありません")
    return {"moved": moved, "due_at": due.strftime(remind.STAMP)}


@app.get("/api/reminders")
def reminders_list(request: Request):
    """預かっているもの。近い順に。言い終わったものは出さない。"""
    _check_token(request)
    with db.session() as conn:
        rows = remind.pending(conn, REMINDER_LIST_LIMIT)
        return {"reminders": [dict(r) for r in rows]}


@app.delete("/api/reminders/{reminder_id}")
def reminder_drop(request: Request, reminder_id: int):
    """預かったものを取り消す。言い終わったものは消せない（もう予定ではない）。"""
    _check_token(request)
    with db.session() as conn:
        if not remind.drop(conn, reminder_id):
            raise HTTPException(status_code=404, detail="その頼まれごとはありません")
        conn.commit()
    return {"dropped": True}


@app.get("/api/machine")
def machine_view(request: Request):
    """いまのPCの様子。見るだけで、ここからは何も変えない。

    会話のときに使っている値をそのまま並べる。道具が生きているかは、
    止まっていると1秒待たされるので最後に置く。
    """
    _check_token(request)
    with db.session() as conn:
        rows = [{"label": label, "value": value} for label, value in presence.snapshot(conn)]
        owner = _call_owner(conn)
        rows.append({"label": "通話", "value": owner or "していない"})
    for label, probe in jobs.tool_probes().items():
        rows.append({"label": label, "value": "動いている" if probe() else "止まっている"})
    return {"rows": rows}


@app.get("/api/prompts")
def prompts_read(request: Request):
    """ことばの元になる3つの文。保存すると次の発言から効く。"""
    _check_token(request)
    return {"prompts": [
        {
            "name": name,
            "label": label,
            "text": admin.read_prompt(name),
            "has_backup": admin.prompt_backup(name).exists(),
        }
        for name, label in admin.PROMPTS.items()
    ]}


@app.put("/api/prompts/{name}")
def prompts_write(request: Request, name: str, payload: dict):
    _check_token(request)
    try:
        admin.write_prompt(name, payload.get("text") or "")
    except admin.AdminError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"saved": True}


@app.post("/api/prompts/{name}/revert")
def prompts_revert(request: Request, name: str):
    _check_token(request)
    try:
        text = admin.revert_prompt(name)
    except admin.AdminError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"text": text}


@app.get("/api/settings")
def settings_read(request: Request):
    _check_token(request)
    return {"settings": admin.read_settings()}


@app.put("/api/settings")
def settings_write(request: Request, payload: dict):
    """.env に書いてから読み直す。プロセスは動いたまま新しい値になる。"""
    _check_token(request)
    values = payload.get("values")
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="値がない")
    try:
        admin.write_settings(values)
    except admin.AdminError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"settings": admin.read_settings()}


@app.post("/api/speak")
def api_speak(request: Request, payload: dict):
    """返答を声にする。対話とは独立していて、失敗しても会話は続く。"""
    _check_token(request)
    if not config.VOICE_ENABLED:
        raise HTTPException(status_code=503, detail="読み上げは無効")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    try:
        wav = voice.speak(text)
    except voice.VoiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return Response(content=wav, media_type="audio/wav")
