"""裏で回りつづけるもの。時計と見張り。

60秒ごとに一度だけ目を開けて、頃合いになったものを片づける。会話と同じ
順番待ちに並ぶものと、並ばせないものがある。**Ollamaへの往復のように
時間のかかるものを並ばせると、そのあいだ会話が止まる。**
"""

import threading
import time
from datetime import datetime, timedelta

from .. import config, notify
from ..memory import consolidate, db, embed, remind
from ..talk import chat, presence, weather
from .announce import announce, collecting, flush_held

# 会話と巡回が共有する順番待ち。同時にDBを触らせないための1本。
turn_lock = threading.Lock()


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
        except Exception as error:
            # 整理の失敗で忘却まで止めない。ただし黙っては済ませない。
            notify.log(f"記憶整理で失敗: {error!r}")
    if db.overdue(conn, db.LAST_BACKUP_AT, config.BACKUP_INTERVAL_SECONDS):
        try:
            db.run_backup(conn)
        except Exception as error:
            # 保存先の不調で忘却まで止めない。ただし黙っては済ませない。
            # 控えが取れていないことに、要るときまで気づけないのが一番困る。
            notify.log(f"バックアップで失敗: {error!r}")
    if db.overdue(conn, db.LAST_FORGET_AT, config.MAINTENANCE_SECONDS):
        db.run_maintenance(conn)
    # 朝の一言、頼まれごと、見守り、暇なときの声かけ。どれも滅多に鳴らない。
    # この巡回のあいだは鳴らさずに預かる。朝の一言と頼まれごとが同じ分に
    # 重なることがあり、2通に分けると同じ人から立て続けに届く。
    with collecting():
        maybe_reminders(conn)
        maybe_briefing(conn)
        maybe_lookout(conn)
        maybe_reach_out(conn)
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


def tool_probes():
    from ..launcher import aivis_is_up, ollama_is_up

    return {"音声エンジン": aivis_is_up, "Ollama": ollama_is_up}


def run_watch_jobs() -> None:
    """管理人としての見張り。落ちたときと、空きが減ったときだけ知らせる。

    **確かめるあいだは順番待ちに並ばない。** 止まっている相手を確かめるのに
    1秒ずつかかるので、ここで並ぶと毎分そのぶん会話が止まる。並ぶのは、
    実際に何か言うときだけ。言うと会話が1件増えるので、そこは他の書き手と
    順番を分け合う必要がある。
    """
    if not notify.ready():
        return
    with db.session() as conn:
        try:
            said = []
            for label, probe in tool_probes().items():
                key = db.UP_PREFIX + label
                before = db.get_state(conn, key)
                now = "1" if probe() else "0"
                # 立ち上がりでは知らせない。落ちた瞬間だけ。
                if before == "1" and now == "0":
                    said.append((f"{label}が止まったことに気づいた。一行で知らせる。",
                                 f"{label}が止まったみたい"))
                db.set_state(conn, key, now)
            for letter, free, _used in presence.disks():
                key = db.DISK_PREFIX + letter
                low = "1" if free < config.DISK_WARN_GB else "0"
                if db.get_state(conn, key) == "0" and low == "1":
                    said.append((f"{letter}ドライブの空きが{free:.0f}GBまで減っている。"
                                 "一行で知らせる。",
                                 f"{letter}ドライブの空き、{free:.0f}GBしかないよ"))
                db.set_state(conn, key, low)
            conn.commit()
            for closing, plain in said:
                with turn_lock:
                    announce(conn, closing, plain=plain)
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


def _with_lock() -> None:
    """整理・バックアップ・忘却・声かけ。会話と同じ順番待ちに並ぶ。"""
    with turn_lock, db.session() as conn:
        run_periodic_jobs(conn)


ROUNDS = (("巡回", _with_lock), ("ベクトル", run_vector_jobs), ("見張り", run_watch_jobs))


def one_round() -> None:
    """ひと回り。どれかが落ちても残りは続ける。

    **落ちたことは必ず書き残す。** 黙って飲み込むと、巡回そのものが
    止まっていても誰も気づかない（実際に一度そうなった）。
    """
    for name, work in ROUNDS:
        try:
            work()
        except Exception as error:
            notify.log(f"{name}で失敗: {error!r}")


def _bg_loop() -> None:
    """時計: 60秒ごとに目を開けて、頃合いのものだけ片づける。"""
    while True:
        time.sleep(config.BACKGROUND_INTERVAL_SECONDS)
        one_round()


threading.Thread(target=_bg_loop, daemon=True).start()
