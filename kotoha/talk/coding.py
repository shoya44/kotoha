"""Claude Code の様子。**読むだけで、書かない。**

Claude Code はセッションごとの記録を `~/.claude/projects/<場所>/<id>.jsonl` に
残す。ことはが見るのは、その**最後の数行の種類と時刻**だけ。何を頼まれて何を
書いたかは読まない。前面アプリの名前しか見ないのと同じ線引きで、「さっきの
やつ終わった？」に答えるには足り、のぞき見にはならない範囲に絞ってある。

分かるのは2つ。いまどの場所で動いているか、動きが止まって返事を待っているか。
返事待ちになった瞬間は、巡回が一声かける材料にもなる（serve/jobs.py）。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .. import config
from ..memory import db

SESSIONS_DIR = Path.home() / ".claude" / "projects"
# 記録の末尾だけ読む。1本が数MBになるので、頭から読むと巡回のたびに引っかかる。
TAIL_BYTES = 64 * 1024
# これより古い記録は見ない。何日も前に閉じたセッションを「返事待ち」と言わない。
RECENT_MINUTES = 180
# 動いている印のまま、これだけ動きが無ければ止まったとみなす（閉じられた・落ちた）。
STALL_MINUTES = 30
# 一度に言うのは、いちばん新しいものから。
LIMIT = 2

WORKING, WAITING, STALLED = "working", "waiting", "stalled"


class Session(NamedTuple):
    id: str
    place: str          # 作業している場所（フォルダー名）
    state: str
    age_minutes: float  # 最後に記録が動いてからの時間


def enabled() -> bool:
    return config.CODING_ENABLED


def _tail_records(path: Path):
    """末尾の記録を、新しい順に返す。読めない行は飛ばす。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            source.seek(max(0, size - TAIL_BYTES))
            data = source.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]           # 途中から読んだ最初の行は、欠けている
    found = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def _state(records) -> str:
    """最後の発言から、いまの様子を読む。

    こちらの返事で終わっていれば（stop_reason が end_turn）返事待ち。道具を
    使った直後や、向こうの発言で終わっていれば、まだ動いている。
    """
    for record in records:
        kind = record.get("type")
        if kind == "assistant":
            message = record.get("message") or {}
            return WAITING if message.get("stop_reason") == "end_turn" else WORKING
        if kind == "user":
            return WORKING
    return ""


def _place(records) -> str:
    for record in records:
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd:
            return Path(cwd).name
    return ""


def sessions(now: float = None, root: Path = None):
    """最近動いたセッション。新しい順に、LIMIT 件まで。"""
    root = root or SESSIONS_DIR
    now = time.time() if now is None else now
    try:
        paths = list(root.glob("*/*.jsonl"))
    except OSError:
        return []
    found = []
    for path in paths:
        try:
            age = (now - path.stat().st_mtime) / 60
        except OSError:
            continue
        if age > RECENT_MINUTES:
            continue
        records = _tail_records(path)
        state = _state(records)
        if not state:
            continue
        if state == WORKING and age >= STALL_MINUTES:
            state = STALLED
        found.append(Session(path.stem, _place(records) or path.parent.name, state, age))
    found.sort(key=lambda one: one.age_minutes)
    return found[:LIMIT]


def _ago(minutes: float) -> str:
    if minutes < 1:
        return "たった今"
    if minutes < 60:
        return f"{int(minutes)}分前"
    return f"{int(minutes // 60)}時間前"


def describe(now: float = None, root: Path = None) -> str:
    """プロンプトに入れる1行。何も無ければ空。"""
    if not enabled():
        return ""
    parts = []
    for one in sessions(now, root):
        when = _ago(one.age_minutes)
        if one.state == WORKING:
            parts.append(f"{one.place} で Claude Code が作業中（{when}まで動いていた）")
        elif one.state == WAITING:
            parts.append(f"{one.place} の Claude Code は作業を終えて、返事を待っている（{when}から）")
        else:
            parts.append(f"{one.place} の Claude Code は途中で止まっているように見える（{when}から動きがない）")
    return "、".join(parts)


def _since(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def finished(conn, now: float = None, root: Path = None):
    """前に見たときは動いていて、いま返事待ちになったもの。

    **短い往復では言わない。** 会話のように1分おきに返事が来る使い方だと、
    そのたびに「終わったよ」と言われて煩い。動き始めてから CODING_MIN_MINUTES
    以上たったものだけを、頼まれた仕事が終わったとみなす。

    見た様子は app_state に残す（`coding:<id>` = 様子|見始めた時刻）。
    もう見ないセッションのぶんは、そのたびに片づける。
    """
    if not enabled():
        return []
    done = []
    current = sessions(now, root)
    stamp = db.now_utc()
    for one in current:
        key = db.CODING_PREFIX + one.id
        before, _, began = (db.get_state(conn, key) or "").partition("|")
        if before != one.state:
            db.set_state(conn, key, f"{one.state}|{stamp}")
        if before == WORKING and one.state == WAITING:
            started = _since(began)
            if started is None:
                continue
            worked = (datetime.now(timezone.utc) - started).total_seconds() / 60
            if worked >= config.CODING_MIN_MINUTES:
                done.append(one)
    db.forget_states(conn, db.CODING_PREFIX, [db.CODING_PREFIX + one.id for one in current])
    return done
