import re
from datetime import datetime, timezone

from .. import config
from ..memory import db, retrieve
from . import presence
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
DEFAULT_MOOD = "ふつう"
MOOD_DECAY_SECONDS = 6 * 3600  # これを過ぎた機嫌は引きずらず「ふつう」に戻す。


def current_mood(conn) -> str:
    """保存された機嫌。時間が経ったものは引きずらない。"""
    label = db.get_state(conn, "mood")
    if label not in MOODS:
        return DEFAULT_MOOD
    if db.seconds_since(db.get_state(conn, "mood_at")) > MOOD_DECAY_SECONDS:
        return DEFAULT_MOOD
    return label


def situation(hour: int) -> str:
    """その時間のことはの様子。画面のアバターと言うことを一致させる。

    区切りは static/app.js の getAvatarGroup() と対応する。
    変更するときは両方を直すこと。
    """
    if 6 <= hour < 11:
        return "起きたばかりで、まだ少し眠い"
    if 11 <= hour < 14:
        return "家でのんびりしている"
    if 14 <= hour < 17:
        return "昼寝やおやつでだらけている"
    if 17 <= hour < 21:
        return "風呂や夕食をすませたあと"
    if hour >= 21 or hour < 2:
        return "夜更かし中で、ゲームかスマホを触っている"
    return "本当はもう寝ている時間"


def elapsed_phrase(last: str) -> str:
    """前回会話からの間隔。UTCの記録をそのまま渡すと時差で誤解されるため言葉にする。"""
    if not last:
        return "初めての会話"
    try:
        dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "不明"
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
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


def _read(name: str) -> str:
    path = config.PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _mem_line(r) -> str:
    """記憶1件の書き方。ラベルが本文と同じ量を占めていたので削ってある。

    層は種類から決まるので書かない（event なら出来事、ほかは意味記憶）。
    年も今年なら省く。id は [USED:] で返してもらうため、そのまま残す。
    """
    stamp = (r["occurred_at"] if r["layer"] == "episode" else r["confirmed_at"])[:10]
    date = stamp[5:] if stamp[:4] == str(datetime.now().year) else stamp
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


def parse_mood(text: str):
    """返答から機嫌を取り出す。知らないラベルは捨て、直前の機嫌を保つ。"""
    clean, body = _strip_tag(text, "MOOD")
    if body is None:
        return text, None
    return clean, body if body in MOODS else None


def _memory_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM memory_nodes WHERE expires_at IS NULL OR expires_at > "
        + db.NOW_SQL
    ).fetchone()[0]


def build_prompt(conn, user_text: str, recent, pinned, related, fast: bool = False) -> str:
    fixed = _read("fixed_rules.txt")
    persona = _read("persona.txt")
    now = datetime.now()
    mood = current_mood(conn)
    lines = [
        f"現在: {now:%Y-%m-%d %H:%M}（{WEEKDAYS[now.weekday()]}曜日）",
        f"前回の会話: {elapsed_phrase(db.get_state(conn, 'last_conversation_at'))}",
        f"今のことは: {situation(now.hour)}",
        f"今の機嫌: {mood}（{MOODS[mood]}）",
    ]
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
    time_block = "\n".join(lines)

    basic_block = ""
    if pinned:
        basic_block = "基本情報:\n" + "\n".join(_mem_line(r) for r in pinned)

    related_block = ""
    if related:
        related_block = "関連記憶:\n" + "\n".join(_mem_line(r) for r in related)

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
        basic_block,
        related_block,
        recent_block,
        f"今回の発言:\nユーザー: {user_text}",
    ]
    if fast:
        blocks.append(FAST_NOTICE)
    return "\n\n".join(b for b in blocks if b)


def _fetch_recent(conn, user_text: str):
    recent = db.fetch_recent(conn, config.RECENT_TURNS, config.RECENT_CHARS)
    if recent and recent[-1]["role"] == "user" and recent[-1]["text"] == user_text:
        recent = recent[:-1]
    return recent


def _finish(conn, turn_id: int, clean: str, ids, mode: str, mood: str = None):
    db.insert_message(conn, turn_id, "assistant", clean)
    db.set_state(conn, "last_conversation_at", db.now_utc())
    if mood:
        db.set_state(conn, "mood", mood)
        db.set_state(conn, "mood_at", db.now_utc())
    db.update_usage(conn, ids, turn_id)
    conn.commit()
    return clean, mode


def _record_pending(conn, ids: list) -> None:
    if not ids:
        return
    pending = db.get_pending_ids(conn)
    # 最新を先頭に、重複を排除して最大50件保持
    pending = list(dict.fromkeys(ids + pending))[:50]
    db.set_pending_ids(conn, pending)


def run_turn(conn, user_text: str):
    turn_id = db.next_turn_id(conn)
    db.insert_message(conn, turn_id, "user", user_text)
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
            clean, mood = parse_mood(clean)
            ids = [i for i in ids if i in allowed]
            _record_pending(conn, ids)
            return _finish(conn, turn_id, clean, ids, mode, mood)

    pinned, related = retrieve.retrieve(conn, user_text, recent_text)
    prompt = build_prompt(conn, user_text, recent, pinned, related)
    raw = llm.chat(prompt)
    clean, ids = parse_used_ids(raw)
    clean, mood = parse_mood(clean)
    _record_pending(conn, ids)
    return _finish(conn, turn_id, clean, ids, mode, mood)