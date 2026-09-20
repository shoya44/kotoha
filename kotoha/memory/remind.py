"""頼まれたことを、その時刻まで預かっておく。

ズボラな彼女が、頼まれたことだけは覚えている。文の中に混ぜた
[REMIND: 2026-09-18 09:00|歯医者] を拾って、時刻が来たら口に出す。
"""

import re
from datetime import datetime, timedelta

from .. import notify
from . import db

TAG = re.compile(r"\[REMIND:\s*([^\]|]+?)\s*\|\s*([^\]]+?)\s*\]")
STAMP = "%Y-%m-%d %H:%M"
# 起動していなかった間に過ぎたものを、まとめて言われても困る。
LATE_LIMIT = timedelta(hours=12)


def parse(text: str):
    """文から頼まれごとを取り出し、タグを消した文と一緒に返す。

    時刻の読み替え（「明日の9時」→ 絶対時刻）は向こうに任せている。
    いまが何時かはプロンプトに入っているので、そこで書かせるほうが確実。
    """
    found = []
    for when, what in TAG.findall(text):
        try:
            found.append((datetime.strptime(when, STAMP), what))
        except ValueError:
            continue        # 読めない時刻は黙って捨てる。妙な予定を残さない。
    return TAG.sub("", text).strip(), found


def add(conn, due: datetime, text: str) -> None:
    conn.execute(
        "INSERT INTO reminders(due_at, text, created_at) VALUES (?,?,?)",
        (due.strftime(STAMP), text, db.now_utc()),
    )


def pending(conn, limit: int = 5):
    """まだ来ていない頼まれごと。近い順に。"""
    return conn.execute(
        "SELECT id, due_at, text FROM reminders WHERE done_at IS NULL "
        "ORDER BY due_at LIMIT ?", (limit,),
    ).fetchall()


def due(conn, now=None):
    """いま言うべきもの。遅れすぎたものは、黙って畳む。"""
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT id, due_at, text FROM reminders WHERE done_at IS NULL AND due_at <= ? "
        "ORDER BY due_at", (now.strftime(STAMP),),
    ).fetchall()
    speak, stale = [], []
    for row in rows:
        try:
            when = datetime.strptime(row["due_at"], STAMP)
        except ValueError:
            stale.append(row["id"])          # 読めないものを抱え続けない
            continue
        if now - when > LATE_LIMIT:
            stale.append(row["id"])          # 寝ていた間に過ぎたぶん
        else:
            speak.append(row)
    for one in stale:
        done(conn, one)
    if stale:
        conn.commit()
        # 「黙って畳む」は仕様どおりだが、**言えなかったことを持ち主が知る道**
        # までは塞がない。していないことを、あるように振る舞わないための1行。
        notify.log(f"時刻を過ぎすぎた頼まれごとを畳んだ: {len(stale)}件")
    return speak


def done(conn, reminder_id: int) -> None:
    conn.execute("UPDATE reminders SET done_at = ? WHERE id = ?",
                 (db.now_utc(), reminder_id))


def drop(conn, reminder_id: int) -> bool:
    cursor = conn.execute("DELETE FROM reminders WHERE id = ? AND done_at IS NULL",
                          (reminder_id,))
    return cursor.rowcount > 0


def snooze(conn, ids, minutes: int):
    """一度言ったことを、もう一度あとで言う。用件はそのまま持ち越す。

    通知から頼まれる。畳んだあとの行からも読めるようにしてあるのは、
    言った時点で done になっているため。新しい1件として入れ直す。
    """
    due = datetime.now() + timedelta(minutes=minutes)
    moved = 0
    for one in ids:
        row = conn.execute("SELECT text FROM reminders WHERE id = ?", (one,)).fetchone()
        if not row:
            continue
        add(conn, due, row["text"])
        moved += 1
    if moved:
        conn.commit()
    return moved, due


def today(conn, now=None):
    """今日のうちに来る、まだ言っていない頼まれごと。朝いちばんの予告に使う。

    時刻が来れば改めて鳴るが、朝に一日ぶんが見えるのは別の値打ちがある。
    """
    now = now or datetime.now()
    return conn.execute(
        "SELECT id, due_at, text FROM reminders WHERE done_at IS NULL "
        "AND due_at LIKE ? AND due_at >= ? ORDER BY due_at",
        (now.strftime("%Y-%m-%d") + "%", now.strftime(STAMP)),
    ).fetchall()


def morning_block(rows) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。"""
    if not rows:
        return ""
    items = "\n".join(f"  {r['due_at'][11:]} {r['text']}" for r in rows)
    return "今日の頼まれごと:\n" + items


def block(conn) -> str:
    """プロンプトに載せる一行。無ければ空。"""
    rows = pending(conn)
    if not rows:
        return ""
    items = "、".join(f"{r['due_at']} {r['text']}" for r in rows)
    return f"預かっている頼まれごと: {items}"
