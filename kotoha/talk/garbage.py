"""ゴミの日。曜日と第何週だけで決まるので、こちらで数えて渡す。

**記憶に覚えさせない。** 想起はタグと意味の近さで選ぶので当たり外れがあり、
朝いちばんに必ず思い出される保証がない。ゴミの日は外すと困る。決まりきった
ものは、こちらで数えて言葉だけを向こうに任せる。天気ブロックと同じ形。

足立区「資源とごみの収集カレンダー」令和8年（2026年）4月〜令和9年（2027年）3月
対象地区: 足立1〜4丁目／綾瀬1〜7丁目／加平1丁目／弘道1、2丁目／
          千住1〜3丁目／千住仲町／西綾瀬1〜4丁目
https://www.city.adachi.tokyo.jp/documents/75227/004.pdf

**引っ越したときと、翌年度のカレンダーが出たときは、ここを見直す。**
期限切れは tests/test_garbage.py が教える。
"""

from datetime import date

# 毎週のもの。曜日は月曜=0。
WEEKLY = {
    0: "燃やすごみ",
    1: "資源",
    3: "燃やすごみ",
    5: "プラスチック",
}

# その月の第何週かで決まるもの。曜日 → (品目, 出る週)
MONTHLY = {4: ("燃やさないごみ", (2, 4))}

# 年末年始だけは曜日どおりではなく、区の広報で別に知らせる期間。
BREAKS = (((12, 24), (12, 31)), ((1, 1), (1, 10)))

# このカレンダーが使える最後の日。
UNTIL = date(2027, 3, 31)


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
