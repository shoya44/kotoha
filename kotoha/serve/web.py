import os
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config, notify
from ..memory import consolidate, db, embed
from ..talk import chat, llm, presence
from . import admin, voice

STATIC_DIR = Path(__file__).resolve().parent / "static"
# start.bat はこの終了コードを見て起動し直す。42以外は普通の終了として扱われる。
RESTART_EXIT_CODE = 42
# 通話中の端末はこの間隔より短く生存を知らせる。途絶えたら通話は終わったとみなす。
CALL_STALE_SECONDS = 120
# 記憶本文の上限。整理が作るときのエピソード側に合わせてある。
MEMORY_TEXT_LIMIT = 400

app = FastAPI(title="kotoha")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_turn_lock = threading.Lock()


def _check_token(request: Request) -> None:
    token = request.headers.get("X-Kotoha-Token", "")
    if not config.WEB_TOKEN or token != config.WEB_TOKEN:
        raise HTTPException(status_code=401, detail="トークンが無効")


def _unprocessed_turns(conn) -> int:
    last = int(db.get_state(conn, "last_processed_message_id", "0") or 0)
    row = conn.execute(
        "SELECT COUNT(DISTINCT turn_id) AS n FROM messages WHERE id > ? AND extractable = 1",
        (last,),
    ).fetchone()
    return row["n"]


def run_periodic_jobs(conn) -> None:
    """整理・バックアップ・忘却を、頃合いになったものだけ回す。

    順番に意味がある。バックアップは忘却より先に取らないと、
    消えた直後の状態しか残らない。前段の失敗で後段を止めない。
    """
    presence.sample(conn)
    unprocessed = _unprocessed_turns(conn)
    idle = db.seconds_since(db.get_state(conn, "last_conversation_at")) > config.IDLE_SECONDS
    # 会話が途切れてからにする。整理は会話と同じ順番待ちに並ぶので、話している
    # 最中に走ると返答が数秒止まる。通話だとそのまま黙り込んで聞こえる。
    if unprocessed > 0 and idle:
        try:
            consolidate.run(conn)
        except Exception:
            pass  # 整理の失敗で忘却まで止めない。
    if db.seconds_since(db.get_state(conn, "last_backup_at")) > config.BACKUP_INTERVAL_SECONDS:
        try:
            db.run_backup(conn)
        except Exception:
            pass  # 保存先の不調で忘却まで止めない。
    if db.seconds_since(db.get_state(conn, "last_forget_at")) > config.MAINTENANCE_SECONDS:
        db.run_maintenance(conn)
    # 暇なときの声かけ。会話が5時間途切れた前提なので、ここで待たせても困らない。
    maybe_reach_out(conn)


def _wake_embedder() -> None:
    """眠ったモデルを起こしておく。

    しばらく話さないとモデルはGPUから降りる。次の1回は読み込みで1.7秒ほど
    かかり、会話側の短い待ち上限に間に合わず、その回だけ想起が効かなくなる。
    会話を待たせないここで、長めの上限で起こしておく。
    """
    if embed.available():
        return
    try:
        embed.embed(["おはよう"], timeout=config.EMBED_BUILD_TIMEOUT_SECONDS)
    except embed.EmbedError:
        pass  # 起きないなら次の巡回でまた試す。


WATCHED = ("音声エンジン", "Ollama")


def _tool_probes():
    from ..launcher import aivis_is_up, ollama_is_up

    return {"音声エンジン": aivis_is_up, "Ollama": ollama_is_up}


def run_watch_jobs() -> None:
    """管理人としての見張り。落ちたときと、空きが減ったときだけ知らせる。

    会話の順番待ちには並ばせない。止まっている相手を確かめるのに1秒ずつ
    かかるので、ここで並ぶと毎分そのぶん会話が止まる。
    """
    if not notify.ready():
        return
    conn = db.connect()
    try:
        for label, probe in _tool_probes().items():
            key = f"up:{label}"
            before = db.get_state(conn, key)
            now = "1" if probe() else "0"
            # 立ち上がりでは知らせない。落ちた瞬間だけ。
            if before == "1" and now == "0":
                notify.push("ことは", f"{label}が止まったみたい")
            db.set_state(conn, key, now)
        for letter, free, _used in presence.disks():
            key = f"disk:{letter}"
            low = "1" if free < config.DISK_WARN_GB else "0"
            if db.get_state(conn, key) == "0" and low == "1":
                notify.push("ことは", f"{letter}ドライブの空き、{free:.0f}GBしかないよ")
            db.set_state(conn, key, low)
        conn.commit()
    except Exception as error:
        notify.log(f"見張りで失敗: {error!r}")
    finally:
        conn.close()


def maybe_reach_out(conn) -> None:
    """暇なとき、ことはのほうから声をかける。

    間が空いていること、時間帯、前回からの間隔。3つとも満たしたときだけ。
    APIを1回使うので、頻繁には出さない。
    """
    if not (config.REACH_OUT_ENABLED and notify.ready()):
        return
    idle = db.seconds_since(db.get_state(conn, "last_conversation_at"))
    if idle < config.REACH_OUT_AFTER_HOURS * 3600:
        return
    if db.seconds_since(db.get_state(conn, "last_reach_out_at")) < (
            config.REACH_OUT_INTERVAL_HOURS * 3600):
        return
    hour = datetime.now().hour
    if not config.REACH_OUT_FROM_HOUR <= hour < config.REACH_OUT_TO_HOUR:
        return
    db.set_state(conn, "last_reach_out_at", db.now_utc())
    conn.commit()
    try:
        text = chat.reach_out(conn)
    except Exception as error:
        notify.log(f"声をかけられなかった: {error!r}")
        return
    if text:
        notify.push("ことは", text)


def run_vector_jobs() -> None:
    """記憶を意味の座標に変えて貯める。会話の順番待ちには並ばせない。

    変換はOllamaへの往復で時間がかかる。順番待ちに入れると、そのあいだ
    会話が止まる。DBへの読み書きは短いので、別につないで回す。
    Ollamaが止まっていれば何も作らず、次の巡回でやり直すだけ。
    """
    if not config.EMBED_ENABLED:
        return
    conn = db.connect()
    try:
        rows = embed.missing(conn, config.EMBED_BATCH)
        if not rows:
            _wake_embedder()
            return
        made = embed.embed(
            [r["text"] for r in rows], timeout=config.EMBED_BUILD_TIMEOUT_SECONDS
        )
        embed.store(conn, zip((r["id"] for r in rows), made))
        embed.link_similar(conn)
    except embed.EmbedError:
        pass  # 声と同じで、無くても会話は続けられる。
    finally:
        conn.close()


def _bg_loop() -> None:
    """時計: アイドル整理と日次メンテナンスを裏で回す。"""
    while True:
        time.sleep(config.BACKGROUND_INTERVAL_SECONDS)
        try:
            with _turn_lock:
                conn = db.connect()
                try:
                    run_periodic_jobs(conn)
                finally:
                    conn.close()
        except Exception:
            pass
        try:
            run_vector_jobs()
        except Exception:
            pass
        try:
            run_watch_jobs()
        except Exception:
            pass


threading.Thread(target=_bg_loop, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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
def history(request: Request, limit: int = config.WEB_HISTORY_LIMIT):
    _check_token(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT role, text, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return JSONResponse([dict(r) for r in reversed(rows)])
    finally:
        conn.close()


@app.post("/api/chat")
def api_chat(request: Request, payload: dict):
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    with _turn_lock:
        conn = db.connect()
        try:
            reply, mode = chat.run_turn(conn, text)
        except llm.LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
        finally:
            conn.close()
    return {"reply": reply, "mode": mode}


def _call_owner(conn):
    """いま通話している端末。見張りが途絶えた記録は無効として扱う。"""
    owner = db.get_state(conn, "call_owner")
    if not owner:
        return None
    if db.seconds_since(db.get_state(conn, "call_seen_at")) > CALL_STALE_SECONDS:
        return None
    return owner


@app.get("/api/call")
def call_state(request: Request, device: str = ""):
    """通話を続けてよいか確かめる。持ち主が入れ替わっていれば false。"""
    _check_token(request)
    conn = db.connect()
    try:
        owner = _call_owner(conn)
        if owner and owner == device:
            db.set_state(conn, "call_seen_at", db.now_utc())
            conn.commit()
        return {"calling": bool(owner), "mine": owner == device if owner else False}
    finally:
        conn.close()


@app.post("/api/call")
def call_claim(request: Request, payload: dict):
    """通話を始める。先に話していた端末があれば、その端末は次の確認でやめる。"""
    _check_token(request)
    device = (payload.get("device") or "").strip()
    if not device:
        raise HTTPException(status_code=400, detail="端末の指定がない")
    conn = db.connect()
    try:
        previous = _call_owner(conn)
        db.set_state(conn, "call_owner", device)
        db.set_state(conn, "call_seen_at", db.now_utc())
        conn.commit()
        return {"calling": True, "mine": True, "took_over": bool(previous and previous != device)}
    finally:
        conn.close()


@app.delete("/api/call")
def call_release(request: Request):
    """通話を終わらせる。自分の端末でも、置いてきた端末でも同じ。"""
    _check_token(request)
    conn = db.connect()
    try:
        released = bool(_call_owner(conn))
        db.set_state(conn, "call_owner", "")
        db.set_state(conn, "call_seen_at", "")
        conn.commit()
        return {"calling": False, "mine": False, "released": released}
    finally:
        conn.close()


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


@app.get("/api/memories")
def memories_list(request: Request, kind: str = "semantic"):
    _check_token(request)
    if kind not in MEMORY_LISTS:
        raise HTTPException(status_code=400, detail="その一覧はありません")
    where, order = MEMORY_LISTS[kind]
    conn = db.connect()
    try:
        rows = conn.execute(
            f"SELECT id, layer, kind, text, occurred_at, confirmed_at, expires_at, pinned "
            f"FROM memory_nodes WHERE {where} ORDER BY {order} LIMIT ?",
            (MEMORY_LIST_LIMIT,),
        ).fetchall()
        return {"memories": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/memories/{node_id}")
def memory_detail(request: Request, node_id: int):
    _check_token(request)
    conn = db.connect()
    try:
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
    finally:
        conn.close()


@app.put("/api/memories/{node_id}")
def memory_update(request: Request, node_id: int, payload: dict):
    """本文を直す。整理の updates と同じく、確認日時を進めて期限も延ばす。"""
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="本文が空")
    if len(text) > MEMORY_TEXT_LIMIT:
        raise HTTPException(status_code=400, detail=f"{MEMORY_TEXT_LIMIT}字までにしてください")
    conn = db.connect()
    try:
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
    finally:
        conn.close()


@app.put("/api/memories/{node_id}/pinned")
def memory_pin(request: Request, node_id: int, payload: dict):
    _check_token(request)
    conn = db.connect()
    try:
        if not db.set_pinned(conn, node_id, bool(payload.get("pinned"))):
            raise HTTPException(status_code=404, detail="その記憶はありません")
        return {"pinned": bool(payload.get("pinned"))}
    finally:
        conn.close()


@app.delete("/api/memories/{node_id}")
def memory_delete(request: Request, node_id: int):
    """保護されていても消す。CUI の /forget と同じ扱い。"""
    _check_token(request)
    conn = db.connect()
    try:
        if not db.forget_node(conn, node_id):
            raise HTTPException(status_code=404, detail="その記憶はありません")
        return {"deleted": True}
    finally:
        conn.close()


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
