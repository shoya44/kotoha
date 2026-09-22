"""ことはの日記。1日を数行にまとめて、日付ごとに1件だけ残す。

記憶（memory_nodes）は「覚えておくこと」を拾う場所で、日記は「その日が
どんな日だったか」を残す場所。人は毎日を全部は覚えていないが、日記を
めくれば「先週もこうだった」とは言える。だらしなさを叱れるのは、そこに
続きが見えるからで、1日の会話だけでは言えない。

夜（日付が変わってから）に前の日のぶんを1件入れる。話さなかった日も
1件入れる。**空白の並びも、それ自体が様子のうち**だからで、そこには
API を使わない。
"""

from datetime import datetime, timedelta, timezone

from .. import config
from ..talk import llm
from . import db

DAY = "%Y-%m-%d"
STAMP = "%Y-%m-%dT%H:%M:%SZ"
SILENT = "この日は話していない。"
# 1日ぶんの材料の上限。長話の日でも、日記のためにこれ以上は読まない。
MATERIAL_CHARS = 6000
# 会話に添える日数。
RECENT_DAYS = 3
# 寝ていた日をさかのぼって書く上限。それより前は、もう「昨日」ではない。
CATCH_UP_DAYS = 7


def _utc_bounds(day):
    """ローカルの1日を、messages が持つUTCの時刻の範囲に直す。"""
    start = datetime.combine(day, datetime.min.time()).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return start.strftime(STAMP), end.strftime(STAMP)


def _local_clock(utc_text: str) -> str:
    return (datetime.strptime(utc_text, STAMP).replace(tzinfo=timezone.utc)
            .astimezone().strftime("%H:%M"))


def material(conn, day) -> str:
    """その日の会話と、言った頼まれごと。無ければ空。"""
    since, until = _utc_bounds(day)
    rows = conn.execute(
        "SELECT role, text, created_at FROM messages "
        "WHERE created_at >= ? AND created_at < ? ORDER BY id", (since, until),
    ).fetchall()
    lines, chars = [], 0
    for r in rows:
        who = "相手" if r["role"] == "user" else "ことは"
        line = f"{_local_clock(r['created_at'])} {who}: {r['text']}"
        if chars + len(line) > MATERIAL_CHARS:
            lines.append("…（長いので以下省略）")
            break
        chars += len(line)
        lines.append(line)
    said = conn.execute(
        "SELECT due_at, text, chain FROM reminders WHERE done_at >= ? AND done_at < ? "
        "ORDER BY due_at", (since, until),
    ).fetchall()
    parts = []
    if lines:
        parts.append("会話:\n" + "\n".join(lines))
    if said:
        parts.append("言った頼まれごと:\n" + "\n".join(
            f"{r['due_at'][11:]} {r['text']}" + ("（追いかけ）" if r["chain"] else "")
            for r in said))
    return "\n\n".join(parts)


def _prompt(day, stuff: str) -> str:
    from ..talk import chat            # 人格を同じ場所から読む（循環を避けて遅らせる）
    head = chat._read("diary_system.txt")
    persona = chat._read("persona.txt")
    weekday = "月火水木金土日"[day.weekday()]
    return (f"{head}\n\n{persona}\n\n日付: {day:%Y-%m-%d}（{weekday}曜日）\n\n"
            f"その日の材料:\n{stuff}\n\n日記:")


def write(conn, day) -> str:
    """その日の日記を1件入れて、本文を返す。もうあれば何もしない。"""
    if entry(conn, day):
        return ""
    stuff = material(conn, day)
    if stuff:
        text = llm.chat(_prompt(day, stuff), max_tokens=config.DIARY_MAX_TOKENS).strip()
        if not text:
            raise RuntimeError("日記が空で返ってきた")
    else:
        text = SILENT
    conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                 (day.strftime(DAY), text, db.now_utc()))
    conn.commit()
    return text


def entry(conn, day):
    return conn.execute("SELECT id, day, text FROM diary WHERE day = ?",
                        (day.strftime(DAY),)).fetchone()


def recent(conn, limit: int = 30):
    """新しい順に。画面と、会話に添えるぶん。"""
    return conn.execute("SELECT id, day, text, created_at FROM diary ORDER BY day DESC LIMIT ?",
                        (limit,)).fetchall()


def missing_days(conn, today):
    """昨日からさかのぼって、まだ書いていない日。古い順に返す。

    さかのぼるのは、いちばん新しい日記の翌日まで（上限つき）。日記を
    書き始めた日より前は「寝ていた日」ではないので、初日は昨日だけ。
    """
    yesterday = today - timedelta(days=1)
    newest = conn.execute("SELECT MAX(day) AS day FROM diary").fetchone()["day"]
    if not newest:
        return [] if entry(conn, yesterday) else [yesterday]
    days = []
    for back in range(1, CATCH_UP_DAYS + 1):
        day = today - timedelta(days=back)
        if day.strftime(DAY) <= newest:
            break
        if not entry(conn, day):
            days.append(day)
    return list(reversed(days))


def block(conn, now=None) -> str:
    """会話のプロンプトに載せる、ここ数日の日記。無ければ空。"""
    now = now or datetime.now()
    today = now.strftime(DAY)
    rows = [r for r in recent(conn, RECENT_DAYS + 1) if r["day"] < today][:RECENT_DAYS]
    if not rows:
        return ""
    lines = "\n".join(f"  {r['day'][5:].replace('-', '/')}: {r['text']}" for r in reversed(rows))
    return ("ことはの日記（ここ数日。だらしなさが続いていれば、うっとおしくない程度に"
            "一言言ってよい。同じことを毎日は言わない）:\n" + lines)


def morning_block(conn, now=None) -> str:
    """朝に振り返る、昨日の日記。無ければ空。"""
    now = now or datetime.now()
    row = entry(conn, (now - timedelta(days=1)).date())
    if not row or row["text"] == SILENT:
        return ""
    return f"昨日の日記（振り返って一言。読み上げない）:\n  {row['text']}"
