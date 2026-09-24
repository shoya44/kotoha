"""裏で回りつづけるもの。時計と見張り。

60秒ごとに一度だけ目を開けて、頃合いになったものを片づける。会話と同じ
順番待ちに並ぶものと、並ばせないものがある。**Ollamaへの往復のように
時間のかかるものを並ばせると、そのあいだ会話が止まる。**
"""

import threading
import time
from datetime import datetime, timedelta

from .. import config, notify
from ..memory import consolidate, db, diary, embed, habits, remind, retrieve, review
from ..talk import chat, coding, garbage, living, llm, myself, presence, quake, schedule, upkeep, weather
from . import hub
from .announce import announce, can_speak, collecting, flush_held

# 会話と巡回が共有する順番待ち。同時にDBを触らせないための1本。
turn_lock = threading.Lock()

# 時計のスレッド名。「取り込んだだけでは始まらない」ことを試験が見張る印。
JOBS_THREAD_NAME = "kotoha-jobs"
_thread = None


def _unprocessed_turns(conn) -> int:
    last = int(db.get_state(conn, db.LAST_PROCESSED_MESSAGE_ID, "0") or 0)
    row = conn.execute(
        "SELECT COUNT(DISTINCT turn_id) AS n FROM messages WHERE id > ? AND extractable = 1",
        (last,),
    ).fetchone()
    return row["n"]


def _someone_waiting() -> bool:
    """話しかけた人が順番待ちに並んでいるか。並んでいれば、巡回は手を止める。

    巡回は会話と同じ順番待ちを持ったまま Gemini を呼ぶ。日記・夜の整理・
    習慣・声かけが同じ分に重なると、そのあいだ会話は「考え中…」のまま
    数分止まった（2026-09-24）。段と段の間でこれを見て、残りは次の巡回へ譲る。
    """
    return hub.talking()


def run_periodic_jobs(conn, outside=None) -> None:
    """整理・バックアップ・忘却を、頃合いになったものだけ回す。

    順番に意味がある。バックアップは忘却より先に取らないと、
    消えた直後の状態しか残らない。前段の失敗で後段を止めない。

    **話しかけた人が待っていれば、そこで切り上げる。** Gemini を呼ぶ段の前で
    見る。切り上げても、頃合いの印はそれぞれの段が自分で付けるので、次の
    巡回（60秒後）でそこから続く。
    """
    myself.heartbeat(conn)   # 生きている印。止まれば、次に起きたとき長さが分かる
    presence.sample(conn)
    conn.commit()            # 切り上げても、ここまでは残す
    unprocessed = _unprocessed_turns(conn)
    idle = db.overdue(conn, db.LAST_CONVERSATION_AT, config.IDLE_SECONDS)
    # 会話が途切れてからにする。整理は会話と同じ順番待ちに並ぶので、話している
    # 最中に走ると返答が数秒止まる。通話だとそのまま黙り込んで聞こえる。
    if unprocessed > 0 and idle and not _someone_waiting():
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
    if _someone_waiting():
        return
    maybe_diary(conn)
    if review.due(conn) and not _someone_waiting():
        try:
            review.run(conn)
        except Exception as error:
            notify.log(f"夜の整理で失敗: {error!r}")
    if habits.due(conn) and not _someone_waiting():
        try:
            habits.reflect(conn)
        except Exception as error:
            notify.log(f"習慣の振り返りで失敗: {error!r}")
    if _someone_waiting():
        return
    # 朝の一言、頼まれごと、見守り、暇なときの声かけ。どれも滅多に鳴らない。
    # この巡回のあいだは鳴らさずに預かる。朝の一言と頼まれごとが同じ分に
    # 重なることがあり、2通に分けると同じ人から立て続けに届く。
    with collecting():
        maybe_reminders(conn)
        maybe_briefing(conn, outside)
        maybe_lookout(conn)
        maybe_coding(conn)
        maybe_reach_out(conn)
        maybe_afterthought(conn)
    try:
        flush_held(conn)
    except Exception as error:
        notify.log(f"まとめて言えなかった: {error!r}")
    # 時間帯や機嫌で姿が変わる。変わっていなくても送るが、宛先は実体1つだけ。
    hub.refresh(conn)
    hub.maybe_move(conn)


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
    if not can_speak():
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
                with turn_lock, llm.hurried():
                    announce(conn, closing, plain=plain)
        except Exception as error:
            notify.log(f"見張りで失敗: {error!r}")


def maybe_reach_out(conn) -> None:
    """暇なとき、ことはのほうから声をかける。

    間が空いていること、時間帯、前回からの間隔。3つとも満たしたときだけ。
    APIを1回使うので、頻繁には出さない。
    """
    if living.quiet(conn) or not (config.REACH_OUT_ENABLED and can_speak()):
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
    announce(conn, chat.REACH_OUT_CLOSING, casual=True)


def maybe_afterthought(conn) -> None:
    """会話中に思い出しかけて出なかったことを、間が空いてから言う。

    人が風呂で「そういえば」と思い出すのと同じ。その場では出なかった記憶
    （近さが床のすぐ下）を想起が1つ預けていて、会話が途切れてしばらくしたら
    口に出す。1日1回。声をかけてよい時間帯は暇なときの声かけと同じ。
    """
    if living.quiet(conn) or not (config.AFTERTHOUGHT_ENABLED and can_speak()):
        return
    if not db.get_state(conn, db.AFTERTHOUGHT_ID):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if db.done_today(conn, db.LAST_AFTERTHOUGHT_ON, today):
        return
    if not db.overdue(conn, db.LAST_CONVERSATION_AT, config.AFTERTHOUGHT_AFTER_MINUTES * 60):
        return
    hour = datetime.now().hour
    if not config.REACH_OUT_FROM_HOUR <= hour < config.REACH_OUT_TO_HOUR:
        return
    row = retrieve.take_afterthought(conn)
    if row is None:
        conn.commit()
        return
    db.mark_today(conn, db.LAST_AFTERTHOUGHT_ON, today)
    db.set_state(conn, db.LAST_AFTERTHOUGHT_ID, row["id"])
    conn.commit()
    announce(conn, chat.AFTERTHOUGHT_CLOSING.format(memory=chat._mem_line(row)), keep=False, casual=True)


def maybe_coding(conn) -> None:
    """頼んでいた Claude Code の仕事が終わったら、一声かける。

    様子の見張りそのもの（どれが動いていて、どれが返事待ちか）は言えなくても
    続ける。言えるようになったときに、溜まったぶんをまとめて言われても困る。
    """
    if not config.CODING_ENABLED:
        return
    for one in coding.finished(conn):
        if not can_speak():
            continue
        announce(conn, f"{one.place} で頼んでいた Claude Code の作業が終わって、"
                       "返事を待っている。一行で知らせる。",
                 plain=f"{one.place} の Claude Code、終わったみたい")


def maybe_lookout(conn) -> None:
    """根を詰めすぎ・夜更かしに気づいたら、一声かける。

    計測はもともと巡回でしている。使っていなかっただけ。
    """
    if living.quiet(conn) or not (config.LOOKOUT_ENABLED and can_speak()):
        return
    hour = datetime.now().hour
    # 夜更かし。日付をまたぐので、その晩ごとに一度だけ。
    if config.LOOKOUT_LATE_HOUR <= hour < config.LOOKOUT_MORNING_HOUR:
        night = (datetime.now() - timedelta(hours=config.LOOKOUT_MORNING_HOUR)).strftime("%Y-%m-%d")
        if not db.done_today(conn, db.LAST_LATE_NIGHT_ON, night):
            db.mark_today(conn, db.LAST_LATE_NIGHT_ON, night)
            announce(conn, f"いま{hour}時。まだ起きて何かしている。"
                           "寝るように、一行で。責めない。", casual=True)
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
                   "休むように、一行で。責めない。", casual=True)


def _seen_at_pc() -> str:
    """相手がいまPCの前に居るか。見えていることを聞かないための1行。

    「起きてる？」と聞く前に、PCが動いていれば起きている。分からなければ
    何も言わない（分からないことを、あるように渡さない）。
    """
    idle = presence.idle_seconds()
    if idle is None:
        return ""
    if idle < 5 * 60:
        return "PCはいま動いている（相手は席に居る）。"
    return f"PCは{int(idle // 60)}分前から触られていない。"


def maybe_reminders(conn) -> None:
    """預かっていた頼まれごとを、時刻が来たら口に出す。

    本人に頼まれたもの（chain=0）は、言えなければ定型でも伝えて畳む。
    自分で入れた追いかけ（chain>0）は、言えなければ黙って畳む。追いかけを
    定型で「の時間だよ」と言っても仕方がないし、毎分やり直す道でもない。
    """
    if not can_speak():
        return
    for row in remind.due(conn):
        chain = row["chain"] or 0
        if chain:
            closing = (f"「{row['text']}」。前に自分で決めた、もう一度言う時刻になった"
                       f"（追いかけの{chain}回目）。まだ返事がない。{_seen_at_pc()}"
                       "一行で。")
        else:
            closing = (f"前に「{row['text']}」を思い出させてほしいと頼まれていた。"
                       f"その時刻になった。{_seen_at_pc()}一行で伝える。")
        spoken = announce(conn, closing,
                          plain="" if chain else f"{row['text']}の時間だよ",
                          remind_ids=[row["id"]], remind_texts=[row["text"]], chain=chain)
        if spoken or chain:
            remind.done(conn, row["id"])
            conn.commit()


# 日記が書けなかったときの間と回数。**一度の失敗でその日を捨てない。**
# 503が一度来ただけで、夜の整理・翌朝の一言・会話に添える日記が丸一日欠けた
# （2026-09-22）。1時間おきに3回まで。それでも駄目ならその日は諦める
# （毎分試して枠を食い潰さないため）。翌日にはさかのぼって拾う。
DIARY_RETRY_SECONDS = 3600
DIARY_TRIES = 3


def _diary_failed(conn, today: str) -> int:
    """しくじりを1つ数えて、今日で何度目かを返す。日付が変われば数え直す。"""
    day, _, count = (db.get_state(conn, db.DIARY_FAILS) or "").partition(":")
    tries = (int(count) if day == today and count.isdigit() else 0) + 1
    db.set_state(conn, db.DIARY_FAILS, f"{today}:{tries}")
    db.set_state(conn, db.DIARY_FAILED_AT, db.now_utc())
    conn.commit()
    return tries


def maybe_diary(conn) -> None:
    """日付が変わったら、前の日の日記を1件。寝ていた日は起きてから順に。

    1日に1度だけ見る（印は先に付ける）。何日も寝ていたぶんは、1回の巡回に
    1日ずつ書く。まとめて書くと、起きた朝にAPIを何度も続けて叩く。
    """
    if not config.DIARY_ENABLED:
        return
    now = datetime.now()
    if now.hour < config.DIARY_HOUR:
        return
    today = now.strftime("%Y-%m-%d")
    if db.done_today(conn, db.LAST_DIARY_ON, today):
        return
    if not db.overdue(conn, db.DIARY_FAILED_AT, DIARY_RETRY_SECONDS):
        return                                          # しくじった直後。間を空ける
    days = diary.missing_days(conn, now.date())
    if not days:
        db.mark_today(conn, db.LAST_DIARY_ON, today)
        return
    try:
        diary.write(conn, days[0])
    except Exception as error:
        tries = _diary_failed(conn, today)
        if tries >= DIARY_TRIES:
            db.mark_today(conn, db.LAST_DIARY_ON, today)   # 今日はもう試さない
            notify.log(f"日記が書けなかった（{days[0]}）: {error!r}。{tries}回目なので今日は諦める")
        else:
            notify.log(f"日記が書けなかった（{days[0]}）: {error!r}。"
                       f"{DIARY_RETRY_SECONDS // 60}分後にもう一度")
        return
    if len(days) == 1:
        db.mark_today(conn, db.LAST_DIARY_ON, today)


def briefing_due(conn) -> bool:
    """朝のひとことを、これから言いそうか。**DBを読むだけ。**

    見当が外れても、外に取りに行ったものを捨てるだけで害はない。
    出すかどうかを本当に決めるのは maybe_briefing のほう。
    """
    if not (config.BRIEFING_ENABLED and can_speak()):
        return False
    now = datetime.now()
    if db.done_today(conn, db.LAST_BRIEFING_ON, now.strftime("%Y-%m-%d")):
        return False
    return config.BRIEFING_HOUR <= now.hour < config.BRIEFING_HOUR + config.BRIEFING_GRACE_HOURS


def briefing_outside(conn):
    """朝の材料のうち、外へ取りに行くぶんだけ先に集める。

    **ここは順番待ちに並ばない。** 空模様と地震はそれぞれ最大8秒待つ。
    ロックを持ったまま外を待つと、そのあいだ会話が丸ごと止まる。外が遅い
    朝に話しかけて数十秒黙られるのは、待たせ方として人間らしくない。
    記憶整理がロックの中でGeminiを呼ぶのは「順番待ち」として意図した形だが、
    DBを触らない外向きの取り寄せまで並ばせる理由はない。
    """
    if not briefing_due(conn):
        return None
    return weather.today(), quake.night()


def maybe_briefing(conn, outside=None) -> None:
    """朝いちばんの一言。その日まだ出していなければ、一度だけ。

    8時にPCが寝ていたら、起きたときに出す。ただし遅れすぎたら黙る。
    夕方に「おはよう」と言われても困る。
    """
    if not (config.BRIEFING_ENABLED and can_speak()):
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
    # **3つを1通にまとめる。** 別々に鳴らすと、減らしたい通知が朝から3通になる。
    # 渡したものには必ず触れさせる（BRIEFING_CLOSING）。無いものは渡さない。
    # keep=False: その日の天気やゴミを長期記憶に溜めない。画面には残る。
    # 外の2つは、ロックの外で取ってあれば使う（briefing_outside）。無ければここで取る。
    sky, shake = outside if outside else (weather.today(), quake.night())
    weather.remember(conn, sky, today)   # 昼になっても、外がどうかは知っている
    extra = "\n".join(part for part in (
        weather.block(sky),
        garbage.block(garbage.today()),
        remind.morning_block(remind.today(conn)),
        diary.morning_block(conn),
        schedule.block(schedule.today()),
        quake.block(shake),
        upkeep.block(upkeep.stale()),
    ) if part)
    announce(conn, chat.BRIEFING_CLOSING, extra=extra, keep=False)


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
            pages = embed.missing_diary(conn, config.EMBED_BATCH, diary.SILENT)
            if not rows and not pages:
                _wake_embedder()
                return
            if rows:
                made = embed.embed(
                    [r["text"] for r in rows], timeout=config.EMBED_BUILD_TIMEOUT_SECONDS
                )
                embed.store(conn, zip((r["id"] for r in rows), made))
                embed.link_similar(conn)
            if pages:
                # 日記も同じ道で座標に。「先月どうだった？」で、その頃の日記が引ける。
                made = embed.embed(
                    [r["text"] for r in pages], timeout=config.EMBED_BUILD_TIMEOUT_SECONDS
                )
                embed.store_diary(conn, zip((r["id"] for r in pages), made))
        except embed.EmbedError:
            pass  # 声と同じで、無くても会話は続けられる。


def _with_lock() -> None:
    """整理・バックアップ・忘却・声かけ。会話と同じ順番待ちに並ぶ。

    **外へ取りに行くものは、並ぶ前に済ませておく。** 朝の空模様と地震は
    それぞれ最大8秒待つ。持ったまま待つと、そのあいだ会話が丸ごと止まる。
    """
    with db.session() as conn:
        outside = briefing_outside(conn)
    # 順番待ちを持っているあいだの Gemini は、短い上限で1回だけ（llm.hurried）。
    with turn_lock, llm.hurried(), db.session() as conn:
        run_periodic_jobs(conn, outside)


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


def start_background() -> None:
    """時計を回し始める。**脳が起きたときに、一度だけ。**

    取り込んだ時点で回していたころは、トレイのプロセスでも同じ時計が回り、
    前面アプリの数えが毎分2つ増え、記憶整理が二度走っていた。呼ぶのは
    serve.web の lifespan だけ。
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_bg_loop, name=JOBS_THREAD_NAME, daemon=True)
    _thread.start()
    threading.Thread(target=_checkup_once, name="kotoha-checkup", daemon=True).start()


def _checkup_once() -> None:
    """起きてしばらくしてから、一度だけ体調を見る（talk/myself.checkup）。"""
    time.sleep(myself.CHECKUP_GRACE_SECONDS)
    try:
        with db.session() as conn:
            myself.checkup(conn)
    except Exception as error:
        notify.log(f"起きたあとの体調見で失敗: {error!r}")
