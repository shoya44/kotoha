"""頼まれたことを、その時刻まで預かっておく。

ズボラな彼女が、頼まれたことだけは覚えている。文の中に混ぜた
[REMIND: 2026-09-18 09:00|歯医者] を拾って、時刻が来たら口に出す。

言って終わりではない。**返事が要ることなら、もう一度言う時刻を自分で
入れる**（追いかけ）。何度言うか、いつ諦めるかは用件の重さで本人が決める。
こちらで決めるのは、際限なく続かないための上限だけ。
毎日・平日の繰り返しは、言い終わるたびに次の日のぶんを入れ直す。
"""

import re
from datetime import datetime, timedelta

from .. import notify
from ..talk import schedule
from . import db

TAG = re.compile(r"\[REMIND:\s*([^\]|]+?)\s*\|\s*([^\]|]+?)\s*(?:\|\s*([^\]]*?)\s*)?\]")
STAMP = "%Y-%m-%d %H:%M"
# 起動していなかった間に過ぎたものを、まとめて言われても困る。
LATE_LIMIT = timedelta(hours=12)
# 繰り返しの言い方。タグの3つ目に書く。知らない言葉は「一度きり」に落とす。
REPEATS = ("毎日", "平日")
# 追いかけの上限。**ここまで来たら、返事は無いものとして手を引く。**
# 何度言うかは本人が決めるが、言えるまで毎回API の枠を食う道は塞いでおく。
CHAIN_LIMIT = 6


def parse(text: str):
    """文から頼まれごとを取り出し、タグを消した文と一緒に返す。

    (時刻, 用件, 繰り返し) の並び。繰り返しは無ければ None。
    時刻の読み替え（「明日の9時」→ 絶対時刻）は向こうに任せている。
    いまが何時かはプロンプトに入っているので、そこで書かせるほうが確実。
    """
    found = []
    for when, what, repeat in TAG.findall(text):
        try:
            when = datetime.strptime(when, STAMP)
        except ValueError:
            continue        # 読めない時刻は黙って捨てる。妙な予定を残さない。
        found.append((when, what, repeat if repeat in REPEATS else None))
    return TAG.sub("", text).strip(), found


def add(conn, due: datetime, text: str, repeat: str = None, chain: int = 0) -> None:
    """預かる。chain は追いかけの何段目か（本人に頼まれたものは 0）。"""
    conn.execute(
        "INSERT INTO reminders(due_at, text, created_at, repeat, chain) VALUES (?,?,?,?,?)",
        (due.strftime(STAMP), text, db.now_utc(), repeat, chain),
    )


def next_due(due: datetime, repeat: str, now=None):
    """繰り返しの、次の時刻。「平日」は暦（土日・祝日）を見る。

    **もう過ぎた日は飛ばす。** 何日か寝ていたあとに1日ずつ畳み直すのは、
    畳んだ記録が日数ぶん並ぶだけで、誰の役にも立たない。
    """
    if repeat not in REPEATS:
        return None
    now = now or datetime.now()
    day = due + timedelta(days=1)
    for _ in range(400):              # 1年寝ていても抜ける
        if day > now and (repeat != "平日" or schedule.today(day.date())["working"]):
            break
        day += timedelta(days=1)
    return day


def pending(conn, limit: int = 5):
    """まだ来ていない頼まれごと。近い順に。"""
    return conn.execute(
        "SELECT id, due_at, text, repeat, chain FROM reminders WHERE done_at IS NULL "
        "ORDER BY due_at LIMIT ?", (limit,),
    ).fetchall()


def due(conn, now=None):
    """いま言うべきもの。遅れすぎたものは、黙って畳む。"""
    now = now or datetime.now()
    rows = conn.execute(
        "SELECT id, due_at, text, repeat, chain FROM reminders "
        "WHERE done_at IS NULL AND due_at <= ? ORDER BY due_at", (now.strftime(STAMP),),
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
    """言い終えた（畳んだ）。繰り返しなら、次の日のぶんをここで入れ直す。

    畳む道はどれも同じ。言えた日も、寝ていて過ぎた日も、明日はまた来る。
    """
    row = conn.execute("SELECT due_at, text, repeat FROM reminders WHERE id = ? "
                       "AND done_at IS NULL", (reminder_id,)).fetchone()
    conn.execute("UPDATE reminders SET done_at = ? WHERE id = ?",
                 (db.now_utc(), reminder_id))
    if not row or not row["repeat"]:
        return
    try:
        due = datetime.strptime(row["due_at"], STAMP)
    except ValueError:
        return
    add(conn, next_due(due, row["repeat"]), row["text"], row["repeat"])


def answered(conn) -> int:
    """返事があった。**追いかけはそこで終わり。** 本人に頼まれたぶんは残す。

    会話でも「あとで」でも同じ。返事が来たあとに「まだ？」と言いに行くのは、
    聞いていない人のすることになる。
    """
    cursor = conn.execute("DELETE FROM reminders WHERE done_at IS NULL AND chain > 0")
    return cursor.rowcount


def follow_up(conn, parent_chain: int, due: datetime, text: str) -> bool:
    """ことはが自分で入れる「もう一度言う」。上限に当たったら入れない。"""
    chain = parent_chain + 1
    if chain > CHAIN_LIMIT:
        notify.log(f"追いかけが上限に達したので手を引いた: {text[:40]}")
        return False
    add(conn, due, text, chain=chain)
    return True


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
        answered(conn)            # 「あとで」も返事のうち。追いかけは要らない
        conn.commit()
    return moved, due


def today(conn, now=None):
    """今日のうちに来る、まだ言っていない頼まれごと。朝いちばんの予告に使う。

    時刻が来れば改めて鳴るが、朝に一日ぶんが見えるのは別の値打ちがある。
    """
    now = now or datetime.now()
    return conn.execute(
        "SELECT id, due_at, text FROM reminders WHERE done_at IS NULL AND chain = 0 "
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
    items = "、".join(
        f"{r['due_at']} {r['text']}" + (f"（{r['repeat']}）" if r["repeat"] else "")
        + ("（自分で決めた追いかけ）" if r["chain"] else "")
        for r in rows)
    return f"預かっている頼まれごと: {items}"
