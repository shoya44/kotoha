"""静かな同席。操作が確実な短い依頼だけを扱う。"""
from datetime import timedelta

from .. import clock
from ..memory import db

START = {"少し作業するね", "ちょっと作業するね", "しばらく集中するね", "静かにしてて"}
END = {"作業終わった", "作業終わったよ", "終わった", "終わったよ", "静かな時間を終わる"}
QUIET_MINUTES = 60


def quiet(conn):
    return (db.get_state(conn, db.QUIET_UNTIL) or "") > clock.utc()


def set_quiet(conn, enabled):
    until = (clock.utc_now() + timedelta(minutes=QUIET_MINUTES)).strftime(clock.STAMP) if enabled else ""
    db.set_state(conn, db.QUIET_UNTIL, until)
    conn.commit()
    return until


def accept(conn, text):
    text = text.strip().rstrip("。！!〜～")
    if text in START:
        set_quiet(conn, True)
    elif text in END and quiet(conn):
        set_quiet(conn, False)


def block(conn):
    if not quiet(conn):
        return ""
    return "相手に頼まれて静かに同席している。話しかけられたら普通に返す。こちらから雑談を促さない。静かな時間の期限（UTC）: " + db.get_state(conn, db.QUIET_UNTIL)
