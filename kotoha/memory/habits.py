"""相手の習慣。何となく覚えていること。

脳と同じ順で作る。日々の出来事は日記（episode）に残り、何日か重なった
ところで、週に一度の振り返り（sleep でいう固着）が「大体この時間に来る」
「土曜の夜は出かけがち」を拾って、ここに固める。会話には毎回そっと添え、
たまに話題にする。

**確かめ直されなかった習慣は薄れる。** 振り返りで挙がらなかったものは
退く（retired_at）。人が「最近そうでもないな」と思うのと同じで、消しは
しないが、口には出さなくなる。
"""

import json
from datetime import datetime

from .. import config
from ..talk import llm
from . import db, diary

# 一度に持つ習慣の上限。多いと「何となく」ではなくなる。
LIMIT = 6
# 振り返りに読む日記の日数と、その間隔。
LOOKBACK_DAYS = 14
INTERVAL_DAYS = 7
# これより日記が少ないうちは、まだ「習慣」とは言えない。
MIN_DIARY_DAYS = 7
TEXT_LIMIT = 80


def alive(conn):
    """いま覚えている習慣。確かめた順が新しいものから。"""
    return conn.execute(
        "SELECT id, text, confirmed_at FROM habits WHERE retired_at IS NULL "
        "ORDER BY confirmed_at DESC LIMIT ?", (LIMIT,),
    ).fetchall()


def block(conn) -> str:
    """会話のプロンプトに載せる数行。無ければ空。"""
    rows = alive(conn)
    if not rows:
        return ""
    lines = "\n".join(f"  - {r['text']}" for r in rows)
    return ("相手の習慣（何となく覚えていること。合う場面でたまに話題にしてよい。"
            "毎回は出さない）:\n" + lines)


TRUST_LEVELS = ("高い", "ふつう", "低め")
TRUST_WHY_CHARS = 60


def trust_line(conn) -> str:
    """プロンプトに載せる1行。まだ振り返っていなければ空。"""
    level = db.get_state(conn, db.TRUST)
    if level not in TRUST_LEVELS:
        return ""
    why = db.get_state(conn, db.TRUST_WHY) or ""
    return f"相手への信頼: {level}" + (f"（{why}）" if why else "")


def _remember_trust(conn, spec) -> None:
    """振り返りの trust を書き置く。読めない形なら前のまま。"""
    if not isinstance(spec, dict) or spec.get("level") not in TRUST_LEVELS:
        return
    db.set_state(conn, db.TRUST, spec["level"])
    db.set_state(conn, db.TRUST_WHY, " ".join((spec.get("why") or "").split())[:TRUST_WHY_CHARS])


def due(conn) -> bool:
    """そろそろ振り返る頃か。日記が7日ぶん溜まっていて、前回から7日。"""
    if not config.DIARY_ENABLED:
        return False
    if not db.overdue(conn, db.LAST_HABITS_AT, INTERVAL_DAYS * 86400):
        return False
    count = conn.execute("SELECT COUNT(*) FROM diary WHERE text != ?", (diary.SILENT,)).fetchone()[0]
    return count >= MIN_DIARY_DAYS


def _prompt(conn, entries, current) -> str:
    from ..talk import chat
    head = chat._read("habits_system.txt")
    days = "\n".join(
        f"{r['day']}（{'月火水木金土日'[datetime.strptime(r['day'], diary.DAY).weekday()]}）: {r['text']}"
        for r in reversed(entries))
    known = "\n".join(f"[id:{r['id']}] {r['text']}" for r in current) or "（まだ無い）"
    trust = trust_line(conn) or "相手への信頼: （まだ決めていない）"
    return f"{head}\n\nいま覚えている習慣:\n{known}\n\n{trust}\n\n最近の日記:\n{days}\n\nJSON:"


def _as_json(raw: str):
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("習慣の振り返りをJSONとして読めなかった。")
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        raise ValueError("習慣の振り返りをJSONとして読めなかった。") from None


def reflect(conn) -> int:
    """日記を読み返して、習慣を固め直す。残った習慣の数を返す。

    挙がったものは確かめ直し（id あり）か新しく覚える（id なし）。
    挙がらなかったものは退く。印は先に付け、失敗しても毎分やり直さない。
    """
    db.set_state(conn, db.LAST_HABITS_AT, db.now_utc())
    conn.commit()
    entries = diary.recent(conn, LOOKBACK_DAYS)
    current = conn.execute("SELECT id, text FROM habits WHERE retired_at IS NULL").fetchall()
    data = _as_json(llm.chat(_prompt(conn, entries, current), max_tokens=config.DIARY_MAX_TOKENS))
    specs = data.get("habits") if isinstance(data, dict) else None
    if not isinstance(specs, list):
        raise ValueError("習慣の振り返りに habits が無い。")
    _remember_trust(conn, data.get("trust"))
    now = db.now_utc()
    known = {r["id"] for r in current}
    kept = set()
    for spec in specs[:LIMIT]:
        if not isinstance(spec, dict):
            continue
        text = " ".join((spec.get("text") or "").split())[:TEXT_LIMIT]
        if not text:
            continue
        hid = spec.get("id")
        if isinstance(hid, int) and hid in known and hid not in kept:
            conn.execute("UPDATE habits SET text = ?, confirmed_at = ? WHERE id = ?",
                         (text, now, hid))
            kept.add(hid)
        else:
            cursor = conn.execute(
                "INSERT INTO habits(text, first_at, confirmed_at) VALUES (?,?,?)",
                (text, now, now))
            kept.add(cursor.lastrowid)
    for hid in known - kept:
        conn.execute("UPDATE habits SET retired_at = ? WHERE id = ?", (now, hid))
    conn.commit()
    return len(kept)
