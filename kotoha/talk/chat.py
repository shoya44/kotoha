import re
from datetime import datetime, timezone
from typing import NamedTuple

from .. import clock, config, notify
from ..memory import db, diary, habits, remind, retrieve
from . import actions, coding, figure, presence, schedule
from . import llm, router

FAST_NOTICE = (
    "補足: 今は軽量モード。基本情報以外の過去記憶は渡されていない。"
    "回答に記憶の検索が必要なら、1行目に NEEDS_SEARCH だけを書いて返すこと。"
)


WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")

# 今日の調子。性格ではないので、persona.txt の人物像を上書きしない範囲にとどめる。
# 文言を変えるときは fixed_rules.txt のラベル一覧も揃えること。
MOODS = {
    "ふつう": "とくに変わったところはない",
    "機嫌がいい": "少し口数が多く、からかいが増える",
    "眠い": "返事が短く、語尾がゆるい",
    "疲れ気味": "テンションは低いが、話は聞く",
    "すねている": "そっけないが、本当は構ってほしい",
}
# ラベルの座標。快−不快（valence）と、元気−疲れ（arousal）。-1〜1。
# **語彙はラベルのまま**で、中身だけ連続にする。ラベルはこの点のどれかに一番近いもの。
MOOD_AXES = {
    "ふつう": (0.0, 0.0),
    "機嫌がいい": (0.6, 0.3),
    "眠い": (0.0, -0.7),
    "疲れ気味": (-0.2, -0.5),
    "すねている": (-0.6, 0.1),
}
# 何も分からないときの機嫌。時間帯ぶんの穴埋めは figure.MOOD_PRIOR が持つ。
DEFAULT_MOOD = "ふつう"
# 機嫌の薄れ方。「まだ強い」「薄れてきた」「だいぶ薄れた」の境。これを切ったら時間帯ぶんに戻る。
MOOD_LEVELS = ((0.7, ""), (0.35, "薄れてきた"), (0.15, "だいぶ薄れた"))
MOOD_GONE = 0.15
# きっかけの長さ。ひとことで足りる。長く書かれても、プロンプトを太らせない。
REASON_CHARS = 40


def mood_intensity(conn) -> float:
    """申告した機嫌の、いまの濃さ（1→0）。黙っているあいだ、半減期で薄れる。

    話しているあいだは時刻が進む（_finish）ので薄れない。6時間で急に消える
    のではなく、人と同じで、すねていてもそのうち普通に戻る。
    """
    hours = db.seconds_since(db.get_state(conn, db.MOOD_AT)) / 3600
    if hours == float("inf"):
        return 0.0
    return 0.5 ** (hours / config.MOOD_HALF_LIFE_HOURS)


def mood_note(conn) -> str:
    """濃さを言葉に。「薄れてきた」「だいぶ薄れた」。まだ強ければ空。"""
    level = mood_intensity(conn)
    for floor, note in MOOD_LEVELS:
        if level >= floor:
            return note
    return ""


def mood_axes(conn, hour: int = None):
    """いまの気分を2つの数で。(快−不快, 元気−疲れ)。

    申告したラベルの点から、時間帯の下地の点へ、黙っているあいだに半減期で
    戻っていく。ラベル（current_mood）は濃さが残るうちは申告どおりだが、
    言葉（mood_words）はこの点から出るので、「すねている」が夜更けに薄れる
    途中で「気分は少し沈み気味、元気は低め」と言える。5段の階段ではなく、
    連続した1本の道の上に居る。
    """
    hour = clock.now().hour if hour is None else hour
    base = MOOD_AXES[figure.prior(hour)]
    label = db.get_state(conn, db.MOOD)
    if label not in MOODS or not figure.keeps(hour, label):
        return base
    told = MOOD_AXES[label]
    level = mood_intensity(conn)
    return tuple(b + (t - b) * level for b, t in zip(base, told))


def mood_words(axes) -> str:
    """2つの数を言葉に。「少し沈み気味、元気は低め」のように。どちらも平らなら空。"""
    valence, arousal = axes
    parts = []
    if valence >= 0.35:
        parts.append("気分は上向き")
    elif valence <= -0.35:
        parts.append("気分は沈み気味")
    elif abs(valence) >= 0.15:
        parts.append("気分は少し" + ("上向き" if valence > 0 else "沈み気味"))
    if arousal >= 0.35:
        parts.append("元気はある")
    elif arousal <= -0.35:
        parts.append("元気は低め")
    elif abs(arousal) >= 0.15:
        parts.append("元気は少し" + ("ある" if arousal > 0 else "低め"))
    return "、".join(parts)


def current_mood(conn, hour: int = None) -> str:
    """いまの機嫌。**薄れきったものも、時間帯に合わないものも引きずらない。**

    ラベルは濃さが残るうちは申告どおり。連続した中身は mood_axes が持つ。

    申告が無ければ、その時間帯ぶんで埋める（`figure.prior`）。申告があっても、
    起きている時間に「眠い」は残さない（`figure.keeps`）。どちらの表も
    figure.py が持っていて、**絵と言葉で二重に持たない。**

    時刻は引数で受ける。渡さなければ今の時刻を見るが、渡せば時計に関係なく
    戻り値を確かめられる。
    """
    hour = clock.now().hour if hour is None else hour
    label = db.get_state(conn, db.MOOD)
    if label not in MOODS or not figure.keeps(hour, label):
        return figure.prior(hour)
    if mood_intensity(conn) < MOOD_GONE:
        return figure.prior(hour)
    return label


def mood_reason(conn, hour: int = None) -> str:
    """いまの機嫌になったきっかけ。**機嫌が薄れきっていれば、きっかけも無い。**

    理由の無い機嫌は、3〜4ターンに1回ころころ変わる（実測 36回/126ターン）。
    「返事がなかったから」が付いていれば、次の往復も同じ機嫌でいられるし、
    相手が謝ったら直る道もできる。
    """
    hour = clock.now().hour if hour is None else hour
    label = db.get_state(conn, db.MOOD)
    if current_mood(conn, hour) != label:
        return ""
    return db.get_state(conn, db.MOOD_WHY) or ""


def current_topic(conn):
    """さっきまでの話題と、いつからか。持ち越す時間を過ぎていれば None。

    往復ごとにプロンプトを組み直しても、話題は続く。直近の窓が切れても、
    翌日の「昨日の続きだけど」まで届く。
    """
    topic = db.get_state(conn, db.TOPIC)
    if not topic:
        return None
    since = db.get_state(conn, db.TOPIC_AT)
    if db.seconds_since(since) > config.TOPIC_KEEP_HOURS * 3600:
        return None
    return topic, since


def topic_line(conn) -> str:
    found = current_topic(conn)
    if not found:
        return ""
    topic, since = found
    return f"さっきまでの話題: {topic}（{elapsed_phrase(since)}から）"


def parse_topic(text: str):
    """返答から「いま何の話をしているか」を取り出す。(本文, 話題)。

    「なし」は話が終わった印で、空文字として返す（None は「タグが無い」）。
    """
    clean, body = _strip_tag(text, "TOPIC")
    if body is None:
        return text, None
    body = " ".join(body.split())[:TOPIC_CHARS]
    return clean, "" if body in ("なし", "無し", "-", "") else body


TOPIC_CHARS = 30


def situation(hour: int, now: datetime = None) -> str:
    """その時間のことはの様子。画面のアバターと言うことを一致させる。

    区切りと文言は figure.py が持っている。**絵と言葉で二重に持たない。**
    """
    return figure.situation(hour, now)


def elapsed_phrase(last: str) -> str:
    """前回会話からの間隔。UTCの記録をそのまま渡すと時差で誤解されるため言葉にする。"""
    if not last:
        return "初めての会話"
    try:
        dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "不明"
    seconds = (clock.utc_now() - dt).total_seconds()
    if seconds < 90:
        return "たった今"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}分前"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}時間前"
    days = hours / 24
    if days < 2:
        return "昨日"
    if days < 7:
        return f"{int(days)}日前"
    if days < 31:
        return f"{int(days // 7)}週間前"
    if days < 365:
        return f"{int(days // 30)}か月前"
    return "1年以上前"


# 声をかけてから、これだけ返事が無ければ「無視された」と数える。
UNANSWERED_SECONDS = 3600


def _utc(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def unanswered(recent, now: datetime = None) -> str:
    """声をかけたのに返事がなかったこと。**ことは自身に言わせるための1行。**

    人は、話しかけて無視されたのを覚えている。次に会ったとき水に流すか
    根に持つかは人格に任せるが、**あったことは渡す**。渡さなければ、
    向こうは何も無かった顔で始めるしかない。

    見るのは直近の会話だけなので、話しているうちに窓から出て自然に薄れる。
    朝のひとことのようにその日限りのもの（extractable=0）は数えない。
    「おはよう」に返事が無いのは、無視ではない。
    """
    now = now or clock.utc_now()
    rows = list(recent)
    turns_with_user = {r["turn_id"] for r in rows if r["role"] == "user"}
    ignored = []
    for i, row in enumerate(rows):
        if row["role"] != "assistant" or row["turn_id"] in turns_with_user:
            continue
        if not row["extractable"]:
            continue
        said = _utc(row["created_at"])
        if said is None:
            continue
        answered = next((_utc(r["created_at"]) for r in rows[i + 1:] if r["role"] == "user"), None)
        gap = ((answered or now) - said).total_seconds()
        if gap >= UNANSWERED_SECONDS:
            ignored.append(row)
    if not ignored:
        return ""
    last = ignored[-1]
    times = f"{len(ignored)}回" if len(ignored) > 1 else "1回"
    return (
        f"返事のなかった声かけ: {times}（最後は{elapsed_phrase(last['created_at'])}）。"
        "ことはのほうから話しかけたのに、返事はなかった。"
        "そのことは、まだ気にしていていい。すぐ水に流さなくていい"
    )


def where_she_is(conn) -> str:
    """いま姿を出している場所。**ことは自身に言わせるための1行。**

    正は脳のメモリ（serve/hub.py）で、ここで読むのはその写し。姿が
    どこにも出ていなければ空を返し、その行は載せない。
    """
    name = db.get_state(conn, db.BODY_WHERE)
    if not name:
        return ""
    if name == "desktop":
        return "いまはデスクトップの右下に姿を出している"
    return "いまは会話画面のほうに姿を出している"


def _read(name: str) -> str:
    """プロンプトを1枚読む。**同じ名前が data/prompts にあれば、そちらを使う。**

    人格には呼び名や人となりが書いてある。公開されたリポジトリに置ける
    ものではないので、git の外に置いた側を先に見る。置かなければ、配って
    あるひな形がそのまま使われる（呼び名は「あなた」）。
    """
    for path in (config.PERSONAL_PROMPTS_DIR / name, config.PROMPTS_DIR / name):
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def _mem_line(r) -> str:
    """記憶1件の書き方。ラベルが本文と同じ量を占めていたので削ってある。

    層は種類から決まるので書かない（event なら出来事、ほかは意味記憶）。
    年も今年なら省く。id は [USED:] で返してもらうため、そのまま残す。
    """
    stamp = (r["occurred_at"] if r["layer"] == "episode" else r["confirmed_at"])[:10]
    date = stamp[5:] if stamp[:4] == str(clock.now().year) else stamp
    return f"- [id:{r['id']}][{date}][{r['kind']}] {r['text']}"


def _tag_pattern(name: str) -> str:
    """内部制御タグの検出。閉じ括弧の欠落や全角括弧でも取りこぼさない。

    取りこぼすとタグがそのままユーザーに見えるため、中身は行末までを上限に
    緩く拾い、閉じ括弧は任意として扱う。
    """
    return rf"[\[［]\s*{name}\s*[:：]([^\]］\n]*)[\]］]?"


def _strip_tag(text: str, name: str):
    pattern = _tag_pattern(name)
    match = re.search(pattern, text)
    if not match:
        return text, None
    clean = re.sub(r"[ \t]*" + pattern + r"[ \t]*", "", text).strip()
    return clean, match.group(1).strip()


TAG_NAMES = ("USED", "MOOD", "TOPIC", "DO", "REMIND", "DROP")


def strip_tags(text: str) -> str:
    """内部タグを本文から落とす。**取りこぼしは、そのまま画面に出る。**

    値を取り出す側は形の揃ったものだけを拾う。妙な予定を抱え込まないための
    厳しさだが、そのぶん崩れたタグが本文に居残る。実際に [REMIND: ] が
    画面へ出た。ここは緩く拾って、最後にひと拭きする。タグが増えても、
    ここを通るかぎり漏れない。
    """
    for name in TAG_NAMES:
        text, _ = _strip_tag(text, name)
    return text.strip()


def parse_used_ids(text: str):
    clean, body = _strip_tag(text, "USED")
    if body is None:
        return text, []
    ids = []
    for i in body.split(","):
        try:
            ids.append(int(i.strip()))
        except ValueError:
            pass
    return clean, ids


def parse_action(text: str):
    """返答から「やってほしいこと」を取り出す。表にない名前は捨てる。"""
    clean, body = _strip_tag(text, "DO")
    if body is None:
        return text, None
    return clean, body if body in actions.ACTIONS else None


def parse_mood(text: str):
    """返答から機嫌と、そのきっかけを取り出す。(本文, 機嫌, きっかけ)。

    形は `[MOOD: すねている|返事がなかったから]`。「|」から先は無くてもよい。
    知らないラベルは捨て、直前の機嫌を保つ（きっかけも捨てる）。
    """
    clean, body = _strip_tag(text, "MOOD")
    if body is None:
        return text, None, ""
    label, _, why = body.replace("｜", "|").partition("|")
    label = label.strip()
    if label not in MOODS:
        return clean, None, ""
    return clean, label, " ".join(why.split())[:REASON_CHARS]


def _memory_count(conn) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM memory_nodes WHERE {db.alive_sql()}", (db.now_utc(),)
    ).fetchone()[0]


def build_prompt(conn, user_text: str, recent, pinned, related, fast: bool = False,
                 closing: str = "", diaries=()) -> str:
    fixed = _read("fixed_rules.txt")
    persona = _read("persona.txt")
    now = clock.now()
    mood = current_mood(conn, now.hour)
    why = mood_reason(conn, now.hour)
    lines = [
        f"現在: {now:%Y-%m-%d %H:%M}（{WEEKDAYS[now.weekday()]}曜日）",
        # 今日が仕事か休みかは、毎日変わる。書き置きにできないので毎回渡す。
        schedule.line(schedule.today(now.date())),
        f"前回の会話: {elapsed_phrase(db.get_state(conn, 'last_conversation_at'))}",
        f"今のことは: {situation(now.hour)}",
        f"今の機嫌: {mood}（{MOODS[mood]}）" + (f"。きっかけ: {why}" if why else "")
        + (f"。{mood_note(conn)}" if why and mood_note(conn) else "")
        + (f"。{mood_words(mood_axes(conn, now.hour))}" if mood_words(mood_axes(conn, now.hour)) else ""),
    ]
    trust = habits.trust_line(conn)
    if trust:
        lines.append(trust)
    topic = topic_line(conn)
    if topic:
        lines.append(topic)
    place = where_she_is(conn)
    if place:
        lines.append(place)
    ignored = unanswered(recent)
    if ignored:
        lines.append(ignored)
    # 自分がどれだけ覚えているかを、自分で言えるようにしておく。
    if not fast:
        lines.append(
            f"覚えていること: {_memory_count(conn)}件"
            f"（最後に整理したのは{elapsed_phrase(db.get_state(conn, 'last_consolidation_at'))}）"
        )
        machine = presence.describe(conn)
        if machine:
            # 「PCの様子」と書くと機械の計測値に見え、返答に使われにくい。
            # 相手を見て言ったこと、という顔にしておく。
            lines.append(f"相手の様子: {machine}")
        work = coding.describe()
        if work:
            lines.append(f"Claude Code の様子: {work}")
    time_block = "\n".join(lines)

    machine_block = ""
    if not fast and presence.asked_about_machine(user_text):
        # 毎回は渡さない。150字ほどあるうえ、ほとんどの会話では要らない。
        inside = presence.details()
        machine_block = "\n".join(
            part for part in (
                f"いま見えているPCの中身（聞かれたら隠さず答えてよい）: {inside}"
                if inside else "",
                actions.offer(),
            )
            if part
        )

    remind_block = remind.block(conn) if not fast else ""
    diary_block = diary.block(conn) if not fast else ""
    recalled_block = diary.recalled_block(diaries) if not fast else ""
    habit_block = habits.block(conn) if not fast else ""

    basic_block = ""
    if pinned:
        basic_block = "基本情報:\n" + "\n".join(_mem_line(r) for r in pinned)

    # 続きのある話は、それと分かる形で渡す。「関連記憶」に混ぜると、モデルには
    # 済んだ話と区別がつかず、人格にある「そういえばあれ、どうなった？」が出ない。
    topics = [r for r in related if r["kind"] == "open_topic"][:retrieve.OPEN_TOPIC_LIMIT]
    topic_ids = {r["id"] for r in topics}
    topic_block = ""
    if topics:
        topic_block = ("気になっていること（続きを聞いてもいい。毎回は聞かない）:\n"
                       + "\n".join(_mem_line(r) for r in topics))

    related_block = ""
    rest = [r for r in related if r["id"] not in topic_ids]
    if rest:
        related_block = "関連記憶:\n" + "\n".join(_mem_line(r) for r in rest)

    recent_block = ""
    if recent:
        lines = "\n".join(
            f"{'ユーザー' if r['role'] == 'user' else 'ことは'}: {r['text']}" for r in recent
        )
        recent_block = f"直近の会話:\n{lines}"

    blocks = [
        fixed,
        persona,
        time_block,
        machine_block,
        remind_block,
        diary_block,
        recalled_block,
        habit_block,
        basic_block,
        topic_block,
        related_block,
        recent_block,
        closing or f"今回の発言:\nユーザー: {user_text}",
    ]
    if fast:
        blocks.append(FAST_NOTICE)
    return "\n\n".join(b for b in blocks if b)


def _fetch_recent(conn, user_text: str):
    recent = db.fetch_recent(conn, config.RECENT_TURNS, config.RECENT_CHARS)
    if recent and recent[-1]["role"] == "user" and recent[-1]["text"] == user_text:
        recent = recent[:-1]
    return recent


def _finish(conn, turn_id: int, clean: str, ids, mode: str, mood: str = None, kept=(),
            why: str = "", dropped=(), topic: str = None):
    db.insert_message(conn, turn_id, "assistant", clean)
    db.set_state(conn, db.LAST_CONVERSATION_AT, db.now_utc())
    remind.answered(conn)      # 返事があった。追いかけはここで終わり
    if mood:
        db.set_state(conn, db.MOOD, mood)
        db.set_state(conn, db.MOOD_WHY, why)
    # 機嫌は変わったときだけ書かせるので、同じ機嫌が続いてもタグは来ない。
    # 話しているあいだは続いているものとして時刻を進める。6時間の薄れは、
    # 黙っている時間に効かせたい。
    db.set_state(conn, db.MOOD_AT, db.now_utc())
    if mood:
        db.count_mood_change(conn, turn_id)
    _remember_topic(conn, topic)
    db.update_usage(conn, ids, turn_id)
    conn.commit()
    return Turn(clean, mode, list(kept), list(dropped))


def _remember_topic(conn, topic) -> None:
    """話題は変わったときだけ書かせる。来なければ前のまま続く。「なし」で終わる。"""
    if topic is None:
        return
    db.set_state(conn, db.TOPIC, topic)
    db.set_state(conn, db.TOPIC_AT, db.now_utc() if topic else "")


class Turn(NamedTuple):
    """1回のやりとりの結果。画面はこれを見て、返事のほかに何を出すか決める。"""

    reply: str
    mode: str
    kept: list = []      # この回に預かった頼まれごと [(時刻, 用件, 繰り返し), …]
    dropped: list = []   # この回に取り消した頼まれごと [(id, 用件, 繰り返し), …]


def _keep_reminders(conn, clean: str):
    """頼まれごとを預かり、タグを外した文と、預かったものを返す。速い道でも同じ。

    ここで捨てると、本人は「覚えとくね」と言ったのに何も残らない。
    """
    clean, later = remind.parse(clean)
    for when, what, repeat in later:
        remind.add(conn, when, what, repeat)
    return clean, later


def _drop_reminders(conn, clean: str):
    """「もういい」と判断した取り消し。消せたものを返す。"""
    clean, ids = remind.parse_drop(clean)
    return clean, remind.drop_many(conn, ids) if ids else []


def trace_recall(pinned, related, used) -> None:
    """想起したものと、使ったと申告されたものを並べて残す（デバッグ時だけ）。

    記憶の寿命は `[USED:]` の自己申告だけで決まる。**読んで使ったのに申告が
    来なければ、その記憶は触れられていない扱いで静かに消える。** 申告が
    どれくらい来ているのかを、まず数えられるようにしておく。
    """
    recalled = [row["id"] for row in list(pinned) + list(related)]
    notify.trace("retrieve", f"想起 {recalled} / 申告 {list(used)}")


def _record_pending(conn, ids: list) -> None:
    if not ids:
        return
    pending = db.get_pending_ids(conn)
    # 最新を先頭に、重複を排除して最大50件保持
    pending = list(dict.fromkeys(ids + pending))[:50]
    db.set_pending_ids(conn, pending)


REACH_OUT_CLOSING = (
    "いまは話しかけられていない。しばらく間が空いたので、そちらから一声かける。\n"
    "一行だけ、短く。用がなくてもいい。記憶にある小さなことに触れてもいい。\n"
    "返事を求めすぎない。責めない。"
)

AFTERTHOUGHT_CLOSING = (
    "さっきの会話のとき、思い出しかけて出てこなかったことが、今になって浮かんだ:\n"
    "{memory}\n"
    "さっきの話に関係がありそうなら、「そういえば」と一行だけ。関係が薄いなら黙ってよい"
    "（何も書かない）。返事を求めすぎない。"
)

BRIEFING_CLOSING = (
    "朝いちばん。今日のことを短く伝える。\n"
    "日付にひとこと触れる。渡したものには全部触れる。落とさない。\n"
    "空模様があれば傘と服装まで、ゴミの日なら何の日か、頼まれごとがあれば時刻まで。\n"
    "昨日の日記があれば、そこから今日に繋がる一言（だらしなかったなら、軽く釘を刺す）。\n"
    "四行まで。読み上げや箇条書きにはしない。いつもの調子で。"
)


FOLLOW_UP_NOTICE = (
    "返事が要ることなのに来なかったら、もう一度言う時刻を [REMIND: 時刻|用件] で"
    "自分に入れてよい。何分後にするか、何度まで言うかは用件の重さで決める。"
    "影響が小さければ一度でいい。遅れたら困ることなら何度でも。"
    "もう言わないと決めたら入れない。そのときは、そう言ってもいい。"
)


def speak(conn, closing: str, extra: str = "", keep: bool = True, chain: int = None):
    """ことはのほうから口を開く。作った文は履歴に残し、それを返す。

    話しかけられていないので「今回の発言」が無い。そこだけ差し替えて、
    人格も記憶も時刻も、普段と同じものを渡す。

    chain が None でなければ頼まれごとを言っている最中で、**自分で自分に
    「もう一度」を入れてよい**（その段数が chain）。それ以外の独り言では、
    自分で予定を作らせない。
    """
    recent = db.fetch_recent(conn, config.RECENT_TURNS, config.RECENT_CHARS)
    seed = "\n".join(r["text"] for r in recent[-4:])
    pinned, related, diaries = retrieve.retrieve_all(conn, seed, "")
    if chain is not None:
        closing = f"{closing}\n{FOLLOW_UP_NOTICE}"
    if extra:
        closing = f"{extra}\n\n{closing}"
    prompt = build_prompt(conn, "", recent, pinned, related, closing=closing, diaries=diaries)
    raw = llm.chat(prompt)
    clean, ids = parse_used_ids(raw)
    clean, mood, why = parse_mood(clean)
    clean, topic = parse_topic(clean)
    clean, _ = parse_action(clean)   # 頼まれてもいないのに動かさない
    clean, later = remind.parse(clean)
    if chain is not None:            # 追いかけだけは、自分で入れてよい
        for when, what, _ in later:
            remind.follow_up(conn, chain, when, what)
    clean = strip_tags(clean)
    if not clean:
        return ""
    remember(conn, clean, ids, mood, keep, why, topic)
    return clean


def remember(conn, text: str, ids=(), mood: str = "", keep: bool = True, why: str = "",
             topic: str = None):
    """ことはの独り言を、会話と同じ場所に残す。開けば並んでいる。

    keep=False は、画面には残すが長期記憶には昇格させない。天気のように
    その日限りのものを毎朝1件ずつ溜めても、後から邪魔になるだけ。
    """
    turn_id = db.start_turn(conn, "assistant", text, extractable=1 if keep else 0)
    if mood:
        db.set_state(conn, db.MOOD, mood)
        db.set_state(conn, db.MOOD_WHY, why)
        db.set_state(conn, db.MOOD_AT, db.now_utc())
    _remember_topic(conn, topic)
    db.update_usage(conn, ids, turn_id)
    conn.commit()


# 言い終わりの印。ここまで来た文から先に渡す（serve/static/app.js と同じ区切り）。
SENTENCE_ENDS = "。．！？!?\n"


def ready_to_say(raw: str, said: int):
    """いま言い終わっている文と、そこまでの長さを返す。

    **タグの途中では切らない。** 揃ったタグは外し、開きっぱなしの `[` から
    先は手元に置く。[MOOD: …] が声になって出ると目も当てられない。
    """
    clean = strip_tags(raw)
    cut = clean.find("[")
    safe = clean[:cut] if cut >= 0 else clean
    end = max((safe.rfind(mark) for mark in SENTENCE_ENDS), default=-1)
    if end < said:
        return "", said
    return safe[said:end + 1], end + 1


def stream_turn(conn, user_text: str):
    """話しながら喋る。言い終わった文から順に渡し、最後にまとめを返す。

    渡すのは {"say": 文} と、最後の {"done": Turn}。**速い道では使わない。**
    Fast は1行目が NEEDS_SEARCH のことがあり、喋ってしまってからでは
    取り消せない。既定では Fast は切ってあるので、通話はここを通る。

    生成が落ちても、**言いかけたぶんは捨てない。** 1文字も来ていないとき
    だけ、まとめて受け取る道（投げ直しつき）へ落ちる。
    """
    turn_id = db.start_or_resume_turn(conn, user_text)
    conn.commit()
    recent = _fetch_recent(conn, user_text)
    recent_text = "\n".join(r["text"] for r in recent)
    pinned, related, diaries = retrieve.retrieve_all(conn, user_text, recent_text)
    prompt = build_prompt(conn, user_text, recent, pinned, related, diaries=diaries)

    raw, said = "", 0
    try:
        for piece in llm.stream(prompt):
            raw += piece
            ready, said = ready_to_say(raw, said)
            if ready:
                yield {"say": ready}
    except llm.LLMError:
        if not raw:
            raw = llm.chat(prompt)

    clean, ids = parse_used_ids(raw)
    clean, mood, why = parse_mood(clean)
    clean, topic = parse_topic(clean)
    clean, todo = parse_action(clean)
    clean, kept = _keep_reminders(conn, clean)
    clean, dropped = _drop_reminders(conn, clean)
    clean = strip_tags(clean)
    _record_pending(conn, ids)
    trace_recall(pinned, related, ids)
    done = _finish(conn, turn_id, clean, ids, "slow", mood, kept, why, dropped, topic)
    # まだ声にしていないぶん。食い違ったら黙って足さない（二度言うほうが困る）。
    rest = clean[said:] if clean[:said] == strip_tags(raw)[:said] else ""
    yield {"done": done, "rest": rest}
    # 頼まれごとは、返答を保存し終えてから。入れ直しならここで落ちる。
    if todo:
        actions.run(todo)


def run_turn(conn, user_text: str):
    turn_id = db.start_or_resume_turn(conn, user_text)
    conn.commit()

    recent = _fetch_recent(conn, user_text)
    recent_text = "\n".join(r["text"] for r in recent)
    mode = router.decide(conn, user_text)

    if mode == "fast":
        pinned = retrieve.pinned_only(conn)
        prompt = build_prompt(conn, user_text, recent, pinned, [], fast=True)
        raw = llm.chat(prompt)
        if raw.split("\n", 1)[0].strip() == "NEEDS_SEARCH":
            mode = "slow+fallback"
        else:
            allowed = {r["id"] for r in pinned}
            clean, ids = parse_used_ids(raw)
            clean, mood, why = parse_mood(clean)
            clean, topic = parse_topic(clean)
            clean, kept = _keep_reminders(conn, clean)
            clean, dropped = _drop_reminders(conn, clean)
            clean = strip_tags(clean)
            ids = [i for i in ids if i in allowed]
            _record_pending(conn, ids)
            return _finish(conn, turn_id, clean, ids, mode, mood, kept, why, dropped, topic)

    pinned, related, diaries = retrieve.retrieve_all(conn, user_text, recent_text)
    prompt = build_prompt(conn, user_text, recent, pinned, related, diaries=diaries)
    raw = llm.chat(prompt)
    clean, ids = parse_used_ids(raw)
    clean, mood, why = parse_mood(clean)
    clean, topic = parse_topic(clean)
    clean, todo = parse_action(clean)
    clean, kept = _keep_reminders(conn, clean)
    clean, dropped = _drop_reminders(conn, clean)
    clean = strip_tags(clean)
    _record_pending(conn, ids)
    trace_recall(pinned, related, ids)
    done = _finish(conn, turn_id, clean, ids, mode, mood, kept, why, dropped, topic)
    # 頼まれごとは、返答を保存し終えてから。入れ直しならここで落ちる。
    if todo:
        actions.run(todo)
    return done