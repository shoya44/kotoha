"""記憶の強さ。使うと強くなり、放っておくと薄れ、薄れきったら消える。

人の忘却曲線に寄せた、いちばん簡単な形。**強さは1つの数**で、日ごとに
半分ずつ減る（半減期は層で違う。出来事は速く、意味はゆっくり）。思い出す
たびに足され、間が空いてから思い出したほど大きく足される（間隔反復）。

消える時刻は強さから導いて `expires_at` に書く。だから忘却の道と生存の
判定は前のままで、index もそのまま効く。強さが効く先は**想起の順位**で、
弱い記憶は強い手がかりがないと出てこない。「思い出せそうで出ない」は
ここから生まれる。

半減期は「初めての記憶の寿命」（EPISODE_DAYS / SEMANTIC_DAYS）から導く。
新しい記憶（強さ1）が床（STRENGTH_FLOOR）に落ちるまでの日数がそれになる。
設定の意味を変えずに、中身だけ連続になる。
"""

import math
from datetime import datetime, timedelta, timezone

from .. import clock, config

STAMP = "%Y-%m-%dT%H:%M:%SZ"
FRESH = 1.0


def _days_to_floor() -> float:
    """強さ1が床まで落ちるのに、半減期いくつぶんかかるか。"""
    return math.log2(FRESH / config.STRENGTH_FLOOR)


def half_life(layer: str) -> float:
    days = config.EPISODE_DAYS if layer == "episode" else config.SEMANTIC_DAYS
    return days / _days_to_floor()


def _parse(value):
    try:
        return datetime.strptime(value, STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def current(strength: float, strength_at, layer: str, now=None) -> float:
    """いまの強さ。記録した時刻から、半減期ぶんずつ薄れている。"""
    at = _parse(strength_at)
    if at is None:
        return strength
    now = now or clock.utc_now()
    days = max(0.0, (now - at).total_seconds() / 86400)
    return strength * 0.5 ** (days / half_life(layer))


def fades_at(strength: float, layer: str, at=None) -> str:
    """その強さが床を切る時刻。expires_at に書く形。"""
    at = at or clock.utc_now()
    if strength <= config.STRENGTH_FLOOR:
        return at.strftime(STAMP)
    days = half_life(layer) * math.log2(strength / config.STRENGTH_FLOOR)
    return (at + timedelta(days=days)).strftime(STAMP)


def gain(days_since_last_use: float) -> float:
    """思い出したときに足すぶん。間が空いてから思い出したほど大きい。"""
    return config.STRENGTH_GAIN * (1 + math.log1p(max(0.0, days_since_last_use)))


def weight(strength_now: float) -> float:
    """想起の順位に掛ける重み。新しい記憶（強さ1）で1、薄れるほど軽い。

    薄れた記憶は、意味の近さが床ぎりぎりでは出てこない。強い手がかりなら出る。
    """
    return min(1.0, 0.7 + 0.3 * strength_now)


def reinforce(conn, node_id: int, now=None) -> float:
    """思い出した。強さを足し、消える時刻を引き直す。新しい強さを返す。

    消えない記憶（expires_at が NULL）は、強さだけ動かして時刻は触らない。
    """
    # 秒で切る。書く時刻（秒）と、そこから導く時刻がずれないように。
    now = (now or clock.utc_now()).replace(microsecond=0)
    row = conn.execute(
        "SELECT layer, strength, strength_at, last_used_at, expires_at FROM memory_nodes "
        "WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return 0.0
    before = current(row["strength"], row["strength_at"] or row["last_used_at"], row["layer"], now)
    last = _parse(row["last_used_at"])
    idle = (now - last).total_seconds() / 86400 if last else 0.0
    after = min(config.STRENGTH_MAX, before + gain(idle))
    stamp = now.strftime(STAMP)
    if row["expires_at"] is None:
        conn.execute("UPDATE memory_nodes SET strength = ?, strength_at = ? WHERE id = ?",
                     (after, stamp, node_id))
    else:
        conn.execute("UPDATE memory_nodes SET strength = ?, strength_at = ?, expires_at = ? "
                     "WHERE id = ?", (after, stamp, fades_at(after, row["layer"], now), node_id))
    return after


def of_rows(rows, now=None) -> dict:
    """行の並びから {id: いまの強さ}。想起で並べ替えるときに使う。"""
    now = now or clock.utc_now()
    return {r["id"]: current(r["strength"], r["strength_at"] or r["confirmed_at"], r["layer"], now)
            for r in rows}


def backfill(conn) -> int:
    """列を足した直後、既存の記憶に強さを入れる。期限から逆算する。

    残り日数が寿命の何割かで強さを置く。期限どおりに消えるので、挙動は
    変わらない。NULL（消えない記憶）は強さ1のまま。
    """
    now = clock.utc_now().replace(microsecond=0)
    rows = conn.execute(
        "SELECT id, layer, expires_at, last_used_at FROM memory_nodes WHERE strength_at IS NULL"
    ).fetchall()
    for r in rows:
        strength = FRESH
        until = _parse(r["expires_at"])
        if until is not None:
            left = (until - now).total_seconds() / 86400
            strength = max(config.STRENGTH_FLOOR,
                           config.STRENGTH_FLOOR * 2 ** (max(0.0, left) / half_life(r["layer"])))
        conn.execute("UPDATE memory_nodes SET strength = ?, strength_at = ? WHERE id = ?",
                     (min(strength, config.STRENGTH_MAX), now.strftime(STAMP), r["id"]))
    return len(rows)


def touch_new(layer: str, at=None) -> str:
    """作ったばかりの記憶の expires_at。強さ1が床に落ちる時刻。"""
    return fades_at(FRESH, layer, at)

