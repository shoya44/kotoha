"""ゴミの日。曜日と第何週だけで決まるので、こちらで数えて渡す。

**記憶に覚えさせない。** 想起はタグと意味の近さで選ぶので当たり外れがあり、
朝いちばんに必ず思い出される保証がない。ゴミの日は外すと困る。決まりきった
ものは、こちらで数えて言葉だけを向こうに任せる。天気ブロックと同じ形。

**暦そのものはここに書かない。** どの地区のカレンダーかは、住んでいる場所を
そのまま指すので、公開されたリポジトリに置けるものではない。中身は
`data/calendars/garbage.json`（git の外）にあり、ここは読んで数えるだけ。

無ければ、ゴミの話は何も出ない。知らないことを、あるように言わないため。
書き方は README を見る。直したあとはサーバーを起こし直す。
"""

import json
from datetime import date

from .. import config

CALENDAR_PATH = config.CALENDAR_DIR / "garbage.json"


def _load():
    """暦を読む。無ければ「知らない」として空で返す。

    JSONが壊れているときも空にする。**黙って半分だけ言うより、言わないほうが
    害が小さい。** 壊れていることは、ゴミの話が消えることで気づく。
    """
    try:
        raw = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}, (), None
    weekly = {int(k): v for k, v in (raw.get("weekly") or {}).items()}
    monthly = {int(k): (v[0], tuple(v[1])) for k, v in (raw.get("monthly") or {}).items()}
    breaks = tuple((tuple(start), tuple(end)) for start, end in (raw.get("breaks") or ()))
    until = date.fromisoformat(raw["until"]) if raw.get("until") else None
    return weekly, monthly, breaks, until


# 毎週のもの（曜日は月曜=0）、第何週かで決まるもの、年末年始、使える最後の日。
WEEKLY, MONTHLY, BREAKS, UNTIL = _load()


def _in_break(day: date) -> bool:
    return any(
        (start[0], start[1]) <= (day.month, day.day) <= (end[0], end[1])
        for start, end in BREAKS
    )


def _nth(day: date) -> int:
    """その月で何回目のその曜日か。1日から数えて7日ごと。"""
    return (day.day - 1) // 7 + 1


def today(day: date = None):
    """その日に出すもの。年末年始は決まらないので None を返す。

    空のリストは「今日は無い」、None は「ここでは決められない」。
    呼ぶ側はこの2つを分けて扱う。
    """
    day = day or date.today()
    if _in_break(day):
        return None
    found = []
    weekly = WEEKLY.get(day.weekday())
    if weekly:
        found.append(weekly)
    monthly = MONTHLY.get(day.weekday())
    if monthly and _nth(day) in monthly[1]:
        found.append(monthly[0])
    return found


def block(items) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。"""
    if items is None:
        return "今日のゴミ:\n  年末年始なので日程が変わる。区のお知らせを見るように言う"
    if not items:
        return ""
    return "今日のゴミ:\n  " + "、".join(items)
