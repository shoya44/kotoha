import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import chat, config, consolidate, db, llm, voice

STATIC_DIR = Path(__file__).resolve().parent / "static"

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
    unprocessed = _unprocessed_turns(conn)
    idle = db.seconds_since(db.get_state(conn, "last_conversation_at")) > config.IDLE_SECONDS
    if unprocessed >= config.CONSOLIDATE_TURNS or (unprocessed > 0 and idle):
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


threading.Thread(target=_bg_loop, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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
