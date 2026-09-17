import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config, notify
from ..memory import consolidate, db, embed, remind
from ..talk import chat, llm, presence, weather
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
    last = int(db.get_state(conn, db.LAST_PROCESSED_MESSAGE_ID, "0") or 0)
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
    idle = db.overdue(conn, db.LAST_CONVERSATION_AT, config.IDLE_SECONDS)
    # 会話が途切れてからにする。整理は会話と同じ順番待ちに並ぶので、話している
    # 最中に走ると返答が数秒止まる。通話だとそのまま黙り込んで聞こえる。
    if unprocessed > 0 and idle:
        try:
            consolidate.run(conn)
        except Exception:
            pass  # 整理の失敗で忘却まで止めない。
    if db.overdue(conn, db.LAST_BACKUP_AT, config.BACKUP_INTERVAL_SECONDS):
        try:
            db.run_backup(conn)
        except Exception:
            pass  # 保存先の不調で忘却まで止めない。
    if db.overdue(conn, db.LAST_FORGET_AT, config.MAINTENANCE_SECONDS):
        db.run_maintenance(conn)
    # 朝の一言、頼まれごと、見守り、暇なときの声かけ。
    # どれも滅多に鳴らないので、ここで待たせてよい。
    # この巡回のあいだは鳴らさずに預かる。朝の一言と頼まれごとが同じ分に
    # 重なることがあり、2通に分けると同じ人から立て続けに届く。
    global _collecting
    _collecting = True
    try:
        maybe_reminders(conn)
        maybe_briefing(conn)
        maybe_lookout(conn)
        maybe_reach_out(conn)
    finally:
        _collecting = False
    try:
        flush_held(conn)
    except Exception as error:
        notify.log(f"まとめて言えなかった: {error!r}")


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


def _tool_probes():
    from ..launcher import aivis_is_up, ollama_is_up

    return {"音声エンジン": aivis_is_up, "Ollama": ollama_is_up}


# 巡回のあいだ立てる印。ここが True の間、announce は鳴らさずに預かる。
# 触るのは裏の巡回だけで、会話（/api/chat）はここを通らない。
_collecting = False

# 預かったものはDBに置く。落ちても消えない（置き場は db.HELD_ANNOUNCEMENTS）。
# 預かったことを呼び出し側へ伝える印。空文字（言えなかった）とは区別する。
HELD = "あとでまとめて言う"
# 長く溜め込んでも困る。一度に言える量には限りがある。
HELD_LIMIT = 8


def _held(conn):
    try:
        items = json.loads(db.get_state(conn, db.HELD_ANNOUNCEMENTS) or "[]")
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _too_soon(conn) -> bool:
    """前に鳴らしてから、まだ間が空いていない。"""
    if config.NOTIFY_GAP_MINUTES <= 0:
        return False
    return not db.overdue(conn, db.LAST_NOTIFY_AT, config.NOTIFY_GAP_MINUTES * 60)


def _with_ids(name, ids) -> str:
    """開く先に番号だけを足す。用件は載せない。通知の道はOneSignalを通る。"""
    joiner = "&" if "?" in config.PUSH_OPEN_URL else "?"
    return f"{config.PUSH_OPEN_URL}{joiner}{name}=" + ",".join(str(i) for i in ids)


def _snooze_ready(ids) -> bool:
    return bool(ids and config.SNOOZE_MINUTES > 0 and config.PUSH_OPEN_URL)


def _snooze_buttons(ids):
    """通知そのものに付ける「あとで」。

    ⚠️ Safari は通知のボタンに対応していない。iPhoneのPWAには出ないので、
    こちらは Chrome で見たときのための道。iPhone では開いた画面で押す。
    """
    if not _snooze_ready(ids):
        return None
    return [{"id": "snooze", "text": f"{config.SNOOZE_MINUTES}分後にもう一度",
             "url": _with_ids("snooze", ids)}]


def _say(conn, items) -> str:
    """預かったぶんも含めて、ひと続きの言葉にして鳴らす。

    2つを2通に分けると、同じ人から立て続けに届く。1通にまとめるのは
    体裁の問題ではなく、向こうに居るのが1人だからで、言い方はことはに任せる。
    """
    closings = [i["closing"] for i in items if i.get("closing")]
    if len(closings) > 1:
        reasons = "\n".join(f"- {c}" for c in closings)
        closing = ("いくつか言うことがある。次のことを、ひと続きの短い言葉にまとめて言う。"
                   "箇条書きにはしない。\n" + reasons)
    else:
        closing = closings[0] if closings else ""
    extra = "\n".join(i["extra"] for i in items if i.get("extra"))
    keep = any(i.get("keep", True) for i in items)
    text = ""
    try:
        text = chat.speak(conn, closing, extra, keep)
    except Exception as error:
        notify.log(f"言えなかった: {error!r}")
    if not text:
        plains = [i["plain"] for i in items if i.get("plain")]
        if plains:
            text = "。".join(plains)
            chat.remember(conn, text, keep=keep)   # 定型でも、言った以上は残す
    if text:
        db.set_state(conn, db.LAST_NOTIFY_AT, db.now_utc())
        conn.commit()
        ids = [i for item in items for i in item.get("remind_ids") or ()]
        # 開く先にも番号を載せる。ボタンの出ない iPhone では、開いた画面に出す。
        url = _with_ids("remind", ids) if _snooze_ready(ids) else ""
        notify.push("ことは", text, buttons=_snooze_buttons(ids), url=url)
    return text


def announce(conn, closing: str, plain: str = "", extra: str = "",
             keep: bool = True, remind_ids=()) -> str:
    """ことはのほうから何か言う。言ったことが、そのまま通知になる。

    通知はすべてここを通す。言わずに鳴らすことはしない。開いても何も
    無い通知ほど不親切なものはないし、あとから会話を辿れなくなる。

    前の通知から間が空いていなければ、ここでは鳴らさずに預かる。巡回が
    間をおいてから、溜まったぶんをまとめて1通にして言う。預かったときは
    HELD を返すので、呼び出し側から見れば「言えた」と同じ扱いでよい。

    plain は、文を作れなかったときに代わりに言わせる一言。見張りのように
    「黙るくらいなら定型でも伝えたい」用がある。声かけのように、言えない
    なら黙っていればよいものは空のままでよい。
    """
    if not notify.ready():
        return ""
    if _collecting or _too_soon(conn):
        items = _held(conn)
        if len(items) >= HELD_LIMIT:
            notify.log(f"預かりきれないので古いぶんを捨てた: {items[0].get('plain') or ''}"[:120])
            items = items[1:]
        items.append({"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                      "remind_ids": list(remind_ids)})
        db.set_state(conn, db.HELD_ANNOUNCEMENTS, json.dumps(items, ensure_ascii=False))
        conn.commit()
        return HELD
    return _say(conn, [{"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                        "remind_ids": list(remind_ids)}])


def flush_held(conn) -> str:
    """預かったぶんを、間が空いてからまとめて言う。

    先に置き場を空にする。ここで失敗しても、同じものを抱えたまま毎分
    やり直すことにはしない。
    """
    if not notify.ready():
        return ""
    items = _held(conn)
    if not items or _too_soon(conn):
        return ""
    db.set_state(conn, db.HELD_ANNOUNCEMENTS, "[]")
    conn.commit()
    return _say(conn, items)


def run_watch_jobs() -> None:
    """管理人としての見張り。落ちたときと、空きが減ったときだけ知らせる。

    会話の順番待ちには並ばせない。止まっている相手を確かめるのに1秒ずつ
    かかるので、ここで並ぶと毎分そのぶん会話が止まる。
    """
    if not notify.ready():
        return
    with db.session() as conn:
        try:
            for label, probe in _tool_probes().items():
                key = db.UP_PREFIX + label
                before = db.get_state(conn, key)
                now = "1" if probe() else "0"
                # 立ち上がりでは知らせない。落ちた瞬間だけ。
                if before == "1" and now == "0":
                    announce(conn, f"{label}が止まったことに気づいた。一行で知らせる。",
                             plain=f"{label}が止まったみたい")
                db.set_state(conn, key, now)
            for letter, free, _used in presence.disks():
                key = db.DISK_PREFIX + letter
                low = "1" if free < config.DISK_WARN_GB else "0"
                if db.get_state(conn, key) == "0" and low == "1":
                    announce(conn,
                             f"{letter}ドライブの空きが{free:.0f}GBまで減っている。"
                             "一行で知らせる。",
                             plain=f"{letter}ドライブの空き、{free:.0f}GBしかないよ")
                db.set_state(conn, key, low)
            conn.commit()
        except Exception as error:
            notify.log(f"見張りで失敗: {error!r}")


def maybe_reach_out(conn) -> None:
    """暇なとき、ことはのほうから声をかける。

    間が空いていること、時間帯、前回からの間隔。3つとも満たしたときだけ。
    APIを1回使うので、頻繁には出さない。
    """
    if not (config.REACH_OUT_ENABLED and notify.ready()):
        return
    if not db.overdue(conn, db.LAST_CONVERSATION_AT, config.REACH_OUT_AFTER_HOURS * 3600):
        return
    if not db.overdue(conn, db.LAST_REACH_OUT_AT, config.REACH_OUT_INTERVAL_HOURS * 3600):
        return
    hour = datetime.now().hour
    if not config.REACH_OUT_FROM_HOUR <= hour < config.REACH_OUT_TO_HOUR:
        return
    db.set_state(conn, db.LAST_REACH_OUT_AT, db.now_utc())
    conn.commit()
    announce(conn, chat.REACH_OUT_CLOSING)


def maybe_lookout(conn) -> None:
    """根を詰めすぎ・夜更かしに気づいたら、一声かける。

    計測はもともと巡回でしている。使っていなかっただけ。
    """
    if not (config.LOOKOUT_ENABLED and notify.ready()):
        return
    hour = datetime.now().hour
    # 夜更かし。日付をまたぐので、その晩ごとに一度だけ。
    if config.LOOKOUT_LATE_HOUR <= hour < config.LOOKOUT_MORNING_HOUR:
        night = (datetime.now() - timedelta(hours=config.LOOKOUT_MORNING_HOUR)).strftime("%Y-%m-%d")
        if not db.done_today(conn, db.LAST_LATE_NIGHT_ON, night):
            db.mark_today(conn, db.LAST_LATE_NIGHT_ON, night)
            announce(conn, f"いま{hour}時。まだ起きて何かしている。"
                           "寝るように、一行で。責めない。")
            return
    # 根の詰めすぎ。声をかけたら数え直すので、続けても間隔が空く。
    found = presence.streak(conn)
    if not found:
        return
    app, hours = found
    if hours < config.LOOKOUT_SIT_HOURS:
        return
    presence.reset_streak(conn)
    conn.commit()
    announce(conn, f"{app}を{int(hours)}時間ぶっ続けで触っている。"
                   "休むように、一行で。責めない。")


def maybe_reminders(conn) -> None:
    """預かっていた頼まれごとを、時刻が来たら口に出す。"""
    if not notify.ready():
        return
    for row in remind.due(conn):
        spoken = announce(conn, f"前に「{row['text']}」を思い出させてほしいと頼まれていた。"
                                "その時刻になった。一行で伝える。",
                          plain=f"{row['text']}の時間だよ", remind_ids=[row["id"]])
        if spoken:
            remind.done(conn, row["id"])
            conn.commit()


def maybe_briefing(conn) -> None:
    """朝いちばんの一言。その日まだ出していなければ、一度だけ。

    8時にPCが寝ていたら、起きたときに出す。ただし遅れすぎたら黙る。
    夕方に「おはよう」と言われても困る。
    """
    if not (config.BRIEFING_ENABLED and notify.ready()):
        return
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if db.done_today(conn, db.LAST_BRIEFING_ON, today):
        return
    if now.hour < config.BRIEFING_HOUR:
        return
    if now.hour >= config.BRIEFING_HOUR + config.BRIEFING_GRACE_HOURS:
        db.mark_today(conn, db.LAST_BRIEFING_ON, today)   # 今日はもう見送る
        return
    db.mark_today(conn, db.LAST_BRIEFING_ON, today)
    # 空模様が取れなくても挨拶はする。外が落ちて朝が消えるのは違う。
    # keep=False: その日の天気を長期記憶に溜めない。画面には残る。
    announce(conn, chat.BRIEFING_CLOSING, extra=weather.block(weather.today()), keep=False)


def run_vector_jobs() -> None:
    """記憶を意味の座標に変えて貯める。会話の順番待ちには並ばせない。

    変換はOllamaへの往復で時間がかかる。順番待ちに入れると、そのあいだ
    会話が止まる。DBへの読み書きは短いので、別につないで回す。
    Ollamaが止まっていれば何も作らず、次の巡回でやり直すだけ。
    """
    if not config.EMBED_ENABLED:
        return
    with db.session() as conn:
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


def _bg_loop() -> None:
    """時計: アイドル整理と日次メンテナンスを裏で回す。"""
    while True:
        time.sleep(config.BACKGROUND_INTERVAL_SECONDS)
        try:
            with _turn_lock, db.session() as conn:
                run_periodic_jobs(conn)
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
def history(request: Request, limit: int = config.WEB_HISTORY_LIMIT):
    _check_token(request)
    with db.session() as conn:
        rows = conn.execute(
            "SELECT role, text, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return JSONResponse([dict(r) for r in reversed(rows)])


@app.post("/api/chat")
def api_chat(request: Request, payload: dict):
    _check_token(request)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="入力が空")
    with _turn_lock, db.session() as conn:
        try:
            reply, mode = chat.run_turn(conn, text)
        except llm.LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
    return {"reply": reply, "mode": mode}


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


@app.post("/api/remind/snooze")
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
    for label, probe in _tool_probes().items():
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
