import asyncio
import json
import os
import secrets
import threading

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import config, notify
from ..memory import db, remind
from ..talk import chat, llm, presence
from . import admin, hub, jobs, voice

STATIC_DIR = Path(__file__).resolve().parent / "static"
# 記憶本文の上限。層ごとに、整理が作るときと同じにしてある（consolidate）。
# 分けないと、手で直したときだけ整理が絶対に作らない長さの意味記憶ができる。
MEMORY_TEXT_LIMITS = {"episode": 400, "semantic": 200}
MEMORY_TEXT_LIMIT = MEMORY_TEXT_LIMITS["episode"]


@asynccontextmanager
async def lifespan(app):
    """脳として動き出すときに、はじめて仕事を始める。

    **取り込んだだけでは何も始めない。** ここを import の副作用にしていた
    あいだ、定数ひとつのために脳を取り込んだトレイの中でも巡回が回り、
    ことはの脳が2つあった。同じ預かりを2つのプロセスが読み、同じ
    頼まれごとが2通届いた。脳はひとつで、それは uvicorn が serve する
    このプロセスだけ。
    """
    hub.wake()               # 上がったばかりの脳には、まだどの器も繋がっていない
    jobs.start_background()  # 60秒ごとの時計は、ここから回り始める
    yield


app = FastAPI(title="kotoha", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _check_token(request: Request, allow_query: bool = False) -> None:
    """合言葉の照合。既定は見出し（ヘッダー）だけを見る。

    allow_query を立てるのは、**見出しを付けられない道**のときだけ。
    EventSource も sendBeacon も、こちらから見出しを足せない。
    """
    token = request.headers.get("X-Kotoha-Token", "")
    if not token and allow_query:
        token = request.query_params.get("token", "")
    # 合わせ方で時間が変わらない比べ方。Tailscale の中とはいえ、ただなので。
    if not config.WEB_TOKEN or not secrets.compare_digest(token, config.WEB_TOKEN):
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


@app.post("/api/chat/stream")
async def api_chat_stream(request: Request, payload: dict):
    """言い終わった文から先に返す。**器は取り次ぐだけ。**

    まとめて返す /api/chat と、判断も記憶も同じものを通る。違うのは渡す
    順番だけで、通話では最初の声が出るまでが丸ごと短くなる。

    生成は1本の糸の中だけで回す。**DBのつなぎは、作った糸でしか触れない。**
    出来たぶんは輪（イベントループ）へ渡して、こちらは配るだけにする。
    """
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    hub.claim(str(payload.get("vessel") or ""), "話しかけられた")
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def hand(item):
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def work():
        try:
            with jobs.turn_lock, db.session() as conn:
                for part in chat.stream_turn(conn, text):
                    if "done" in part:
                        turn = part["done"]
                        hand({"done": {
                            "reply": turn.reply, "mode": turn.mode,
                            "rest": part["rest"],
                            "last_id": conn.execute(
                                "SELECT MAX(id) AS id FROM messages").fetchone()["id"],
                            "kept": [{"due_at": when.strftime(remind.STAMP), "text": what,
                                      "repeat": repeat}
                                     for when, what, repeat in turn.kept],
                            "dropped": [{"id": i, "text": what, "repeat": repeat}
                                        for i, what, repeat in turn.dropped],
                        }})
                    else:
                        hand(part)
        except llm.LLMError as error:
            hand({"error": str(error)})
        except Exception as error:      # 黙って途切れさせない
            notify.log(f"流しながらの会話で失敗: {error!r}")
            hand({"error": "うまく言えなかった"})
        finally:
            hand(None)

    threading.Thread(target=work, name="kotoha-chat-stream", daemon=True).start()

    async def lines():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield json.dumps(item, ensure_ascii=False) + "\n"
        # 話したあとは機嫌が動く。姿もそこで1回だけ合わせる。
        hub.refresh(said_ago=0)

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/api/chat")
def api_chat(request: Request, payload: dict):
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    # **話しかけた器へ、先に実体を移す。** 生成には数秒かかるので、返事より
    # あとに移すと、そのあいだ別の器が喋っているように見える。
    hub.claim(str(payload.get("vessel") or ""), "話しかけられた")
    with jobs.turn_lock, db.session() as conn:
        try:
            turn = chat.run_turn(conn, text)
        except llm.LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
        # いま増えたぶんまで画面の目印を進める。これが無いと、次の見に行きで
        # 自分が送ったばかりの往復をもう一度拾って、二重に並ぶ。
        last_id = conn.execute("SELECT MAX(id) AS id FROM messages").fetchone()["id"]
    # 話したあとは機嫌が動く。姿もそこで1回だけ合わせる。
    hub.refresh(said_ago=0)
    answer = {"reply": turn.reply, "mode": turn.mode, "last_id": last_id}
    if turn.kept:
        # 預かったことを画面にも出す。ことはの言葉は変えず、印だけ足す。
        answer["kept"] = [{"due_at": when.strftime(remind.STAMP), "text": what,
                           "repeat": repeat}
                          for when, what, repeat in turn.kept]
    if turn.dropped:
        answer["dropped"] = [{"id": i, "text": what, "repeat": repeat}
                             for i, what, repeat in turn.dropped]
    return answer


@app.get("/api/call")
def call_state(request: Request, vessel: str = ""):
    """通話を続けてよいか。**実体が移っていれば、もう自分のものではない。**"""
    _check_token(request)
    who = hub.calling()
    return {"calling": bool(who), "mine": bool(who) and who == vessel}


@app.post("/api/call")
def call_claim(request: Request, payload: dict):
    """通話を始める。姿ごとこちらへ来るので、前の器の通話はそこで終わる。"""
    _check_token(request)
    vessel = (payload.get("vessel") or "").strip()
    if not vessel:
        raise HTTPException(status_code=400, detail="器の指定がない")
    started, took_over = hub.start_call(vessel)
    if not started:
        raise HTTPException(status_code=409, detail="その器は繋がっていない")
    return {"calling": True, "mine": True, "took_over": took_over}


@app.delete("/api/call")
def call_release(request: Request):
    """通話を終わらせる。自分の器でも、置いてきた器でも同じ。"""
    _check_token(request)
    return {"calling": False, "mine": False, "released": hub.end_call()}


@app.get("/api/presence/stream")
async def presence_stream(request: Request, vessel: str = ""):
    """脳からの言づてを流し続ける道（SSE）。器はこれを開いたまま待つ。

    **繋がっていること自体が「そこに居る」の証拠**になる。切れれば脳が
    その場で気づくので、鮮度を測る必要がない。
    """
    _check_token(request, allow_query=True)
    if not vessel:
        raise HTTPException(status_code=400, detail="器の名前が無い")
    queue = asyncio.Queue(maxsize=hub.QUEUE_LIMIT)
    joined = hub.join(vessel, queue, asyncio.get_running_loop())
    # 繋がった器は、まだ何も知らない。いまの姿を1回渡しておく。
    await asyncio.to_thread(hub.refresh)

    async def events():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=hub.PING_SECONDS)
                except asyncio.TimeoutError:
                    # 無言のままだと途中の何かに切られる。生きている合図を流す。
                    yield ": ping\n\n"
                    continue
                yield f"data: {payload}\n\n"
        finally:
            hub.leave(joined)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store",
        # 溜め込まれると、すぐ届くはずのものが数十秒遅れる。
        "X-Accel-Buffering": "no",
    })


@app.post("/api/presence/here")
async def presence_here(request: Request):
    """実体をこちらへ。呼び出し（通知から開いた・看板を押した・通話へ渡す）。"""
    _check_token(request, allow_query=True)
    payload = await _lenient_json(request)
    vessel = str(payload.get("vessel") or "")
    reason = str(payload.get("reason") or "呼ばれた")
    if not hub.claim(vessel, reason):
        raise HTTPException(status_code=409, detail="その器は繋がっていない")
    return {"body": hub.body()}


@app.post("/api/presence/bye")
async def presence_bye(request: Request):
    """見えなくなった。**切断を待たずに実体を手放すための道。**

    iPhoneのPWAは背景に回っても繋がりが残ることがある。待っていると
    「まだ居る」と思い込んで、Pushが鳴らなくなる。
    """
    _check_token(request, allow_query=True)
    payload = await _lenient_json(request)
    vessel = str(payload.get("vessel") or "")
    if vessel:
        hub.forget(vessel)
    return Response(status_code=204)


async def _lenient_json(request: Request) -> dict:
    """本文を緩く読む。sendBeacon は種類を選べないことがある。"""
    try:
        body = await request.body()
        if not body:
            return {}
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


@app.post("/api/restart")
def api_restart(request: Request):
    """外出先から立て直すための最後の手段。kotoha.bat が起動し直す。"""
    _check_token(request)
    # 先に応答を返しきってから落とす。DBへの書き込みはその都度コミットしてある。
    threading.Timer(0.4, lambda: os._exit(config.RESTART_EXIT_CODE)).start()
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
    with db.session() as conn:
        row = conn.execute("SELECT layer FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="その記憶はありません")
        limit = MEMORY_TEXT_LIMITS.get(row["layer"], MEMORY_TEXT_LIMIT)
        if len(text) > limit:
            raise HTTPException(status_code=400, detail=f"{limit}字までにしてください")
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
    where = hub.body()
    rows.append({"label": "姿", "value": where or "出していない"})
    rows.append({"label": "通話", "value": hub.calling() or "していない"})
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
    """返答を声にする。対話とは独立していて、失敗しても会話は続く。

    **声の表情を決めるのはここ**（脳の側）で、器は出来たwavを鳴らすだけ。
    `plain: true` を付けた読み上げだけ、機嫌を乗せない素の声で返す。

    通話では `[MOOD:]` が返答の**最後**に来るので、先に鳴る文には前のターンの
    機嫌が乗る。新しい機嫌は次のターンから声になる。
    """
    _check_token(request)
    if not config.VOICE_ENABLED:
        raise HTTPException(status_code=503, detail="読み上げは無効")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    mood = ""
    if not payload.get("plain"):
        with db.session() as conn:
            mood = chat.current_mood(conn)
    try:
        wav = voice.speak(text, mood)
    except voice.VoiceError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return Response(content=wav, media_type="audio/wav")
