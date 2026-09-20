"""ことはが持っている暦の期限。切れる前に、自分から言う。

**テストでも見張っているが、テストは誰も自動では走らせない。** 落ちるのは
手で叩いたときだけで、それは持ち主に届く道ではない。届く道は朝のひとことしか
ないので、ことは自身に言わせる。

いちばん質が悪いのは全体会議で、日程が尽きると**間違ったことを言うのではなく
黙る**。画面を見ても通知を見ても間違いに見えないまま、会議を落としかねない。
"""

from datetime import date

from . import garbage, schedule

# 残りがこれを切ったら予告を始める。
NOTICE_DAYS = 30
# 予告する曜日。30日のあいだ毎朝言われるとうるさい（月曜=0）。
NOTICE_WEEKDAY = 0


def deadlines():
    """見張っているもの。名前・最後の日・どこからもらうか。

    **入れていない暦は見張らない。** ゴミも会議も data/calendars にあり、
    置いていない人には期限そのものが無い。無いものを「切れている」と
    言われるほうが困る。
    """
    found = []
    if garbage.UNTIL:
        found.append(("ゴミの収集カレンダー", garbage.UNTIL, "区の新しいカレンダー"))
    found.append(("祝日の一覧", schedule.UNTIL, "内閣府の一覧"))
    if schedule.MEETINGS:
        found.append(("全体会議の日程",
                      date.fromisoformat(max(schedule.MEETINGS)), "来年度の日程"))
    return tuple(found)


def stale(day: date = None):
    """見直しどきのもの。まだ先のものは返さない。"""
    day = day or date.today()
    found = []
    for name, until, source in deadlines():
        left = (until - day).days
        if left < 0:
            found.append({"name": name, "source": source, "left": left, "over": True})
        elif left <= NOTICE_DAYS and day.weekday() == NOTICE_WEEKDAY:
            found.append({"name": name, "source": source, "left": left, "over": False})
    return found


def block(rows) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。"""
    if not rows:
        return ""
    lines = [
        f"  {one['name']}が切れている。{one['source']}をもらうよう頼む"
        if one["over"] else
        f"  {one['name']}があと{one['left']}日で切れる。{one['source']}をもらうよう頼む"
        for one in rows
    ]
    return "持っている暦の期限:\n" + "\n".join(lines)
