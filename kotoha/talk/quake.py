"""寝ているあいだの地震。気づかなかった朝にだけ意味がある。

**普段は黙る。** 揺れなかった夜のほうが多いので、毎朝「地震はありません」と
言われても仕方がない。ここが言葉を返すのは、実際に揺れた朝だけ。

P2P地震情報のAPIから取る。鍵もアカウントも要らない。
https://api.p2pquake.net/v2/history
"""

from datetime import datetime, timedelta

import httpx

ENDPOINT = "https://api.p2pquake.net/v2/history"
TIMEOUT = 8.0
STAMP = "%Y/%m/%d %H:%M:%S"

# 住んでいるところ。ここで揺れたものだけを拾う。
PREF = "東京都"
# 震度。10刻みで、30が震度3。これ未満は寝ていて当然なので言わない。
FELT_FLOOR = 30
# 前の晩の何時から見るか。
FROM_HOUR = 22
# 一度に見る件数。夜のうちの数件が入っていればよい。
LIMIT = 20

SCALES = {10: "1", 20: "2", 30: "3", 40: "4",
          45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7"}

_client = None


def _http():
    """最初に使うときだけ作る。取り込み時に作ると設定の検査を邪魔する。"""
    global _client
    if _client is None:
        _client = httpx.Client(trust_env=False)
    return _client


def _felt_here(one) -> int:
    """ここでの震度。揺れていなければ 0。"""
    here = [p.get("scale") or 0 for p in (one.get("points") or [])
            if p.get("pref") == PREF]
    return max(here, default=0)


def night(now: datetime = None):
    """寝ているあいだに、ここで揺れたもの。取れなければ空。

    地震が取れないくらいで朝の挨拶をやめる理由はないので、
    呼ぶ側が空を受け取っても続けられるようにしてある。
    """
    now = now or datetime.now()
    since = (now - timedelta(days=1)).replace(
        hour=FROM_HOUR, minute=0, second=0, microsecond=0)
    try:
        response = _http().get(ENDPOINT, timeout=TIMEOUT,
                               params={"codes": 551, "limit": LIMIT})
        response.raise_for_status()
        rows = response.json()
    except Exception:
        return []
    found = []
    for one in rows:
        try:
            quake = one["earthquake"]
            when = datetime.strptime(quake["time"], STAMP)
        except (KeyError, TypeError, ValueError):
            continue        # 読めないものは黙って飛ばす
        if not since <= when <= now:
            continue
        scale = _felt_here(one)
        if scale < FELT_FLOOR:
            continue
        found.append({
            "when": when,
            "place": (quake.get("hypocenter") or {}).get("name") or "どこか",
            "scale": SCALES.get(scale, "?"),
        })
    return sorted(found, key=lambda one: one["when"])


def block(rows) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。"""
    if not rows:
        return ""
    items = "\n".join(
        f"  {one['when']:%H:%M} {one['place']} ここは震度{one['scale']}"
        for one in rows
    )
    return "寝ているあいだの地震:\n" + items
