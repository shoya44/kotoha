"""持ち主の仕事の予定。曜日と日付だけで決まるので、こちらで数えて渡す。

**今日だけ違うときだけ言う。** 普通の平日に「今日は仕事だよ」と言われても
仕方がない。言葉が出るのは、平日なのに休みの日と、全体会議の日だけ。

祝日は内閣府が公開している一覧から写した。年に一度、翌年ぶんが増える。
https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv

**勤務時間と会議の日程はここに書かない。** 誰がいつ家に居ないかを、そのまま
書き出したものになる。中身は `data/calendars/work.json`（git の外）にあり、
ここは読んで数えるだけ。無ければ、勤務時間も会議も言わない。書き方は README を
見る。直したあとはサーバーを起こし直す。

祝日表の期限切れは tests/test_schedule.py が教える。
"""

import json
from datetime import date

from .. import config

WORK_PATH = config.CALENDAR_DIR / "work.json"


def _load():
    """勤め先のことを読む。無ければ「知らない」として空で返す。"""
    try:
        raw = json.loads(WORK_PATH.read_text(encoding="utf-8"))
        meetings = raw.get("meetings") or ()
        if isinstance(meetings, str):      # 1件だけを文字列で書いても、1文字ずつにしない
            meetings = (meetings,)
        return (str(raw.get("from") or ""), str(raw.get("to") or ""),
                frozenset(str(m) for m in meetings))
    except (OSError, ValueError, TypeError, AttributeError):
        return "", "", frozenset()


# 平日の勤務時間と、全体会議（帰社日）。
WORK_FROM, WORK_TO, MEETINGS = _load()

# 国民の祝日・休日。内閣府の一覧より（2026年4月以降）。
HOLIDAYS = {
    "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日",
    "2026-05-04": "みどりの日",
    "2026-05-05": "こどもの日",
    "2026-05-06": "休日",
    "2026-07-20": "海の日",
    "2026-08-11": "山の日",
    "2026-09-21": "敬老の日",
    "2026-09-22": "休日",
    "2026-09-23": "秋分の日",
    "2026-10-12": "スポーツの日",
    "2026-11-03": "文化の日",
    "2026-11-23": "勤労感謝の日",
    "2027-01-01": "元日",
    "2027-01-11": "成人の日",
    "2027-02-11": "建国記念の日",
    "2027-02-23": "天皇誕生日",
    "2027-03-21": "春分の日",
    "2027-03-22": "休日",
    "2027-04-29": "昭和の日",
    "2027-05-03": "憲法記念日",
    "2027-05-04": "みどりの日",
    "2027-05-05": "こどもの日",
    "2027-07-19": "海の日",
    "2027-08-11": "山の日",
    "2027-09-20": "敬老の日",
    "2027-09-23": "秋分の日",
    "2027-10-11": "スポーツの日",
    "2027-11-03": "文化の日",
    "2027-11-23": "勤労感謝の日",
}

# 祝日表が届いている最後の日。**表から導く。** 手で書くと、表を足したのに
# こちらを直し忘れる。
UNTIL = date.fromisoformat(max(HOLIDAYS))


def today(day: date = None) -> dict:
    """その日が休みか、仕事か、全体会議の日か。

    土日と祝日が休み。平日は勤務。全体会議はそれとは別に立つ。
    """
    day = day or date.today()
    stamp = day.isoformat()
    holiday = HOLIDAYS.get(stamp)
    weekend = day.weekday() >= 5
    return {
        "working": not (weekend or holiday),
        "holiday": holiday,
        "weekend": weekend,
        "meeting": stamp in MEETINGS,
        "from": WORK_FROM,
        "to": WORK_TO,
    }


def line(plan) -> str:
    """毎回のプロンプトに添える1行。ことはが今日を知っているための事実。

    **書き置きではなく、その日の事実を渡す。** 祝日も全体会議も日ごとに
    変わるので、人格の側に「平日9時から17時半の仕事」と静的に書くと、
    休みの日にも「仕事いってらっしゃい」と言ってしまう。

    指示は書かない。知っているだけにして、使い方は向こうに任せる。
    """
    # 勤務時間を知らないなら、時刻は言わない。知らないことを、あるように
    # 言わないため。暦を入れていない人には「今日: 仕事」だけが出る。
    hours = f"（{plan['to']}まで）" if plan.get("to") else ""
    if plan["meeting"]:
        return f"今日: 仕事{hours}／全体会議で出社"
    if plan["working"]:
        return f"今日: 仕事{hours}"
    if plan["holiday"]:
        return f"今日: 休み（{plan['holiday']}）"
    return "今日: 休み"


def block(plan) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。

    **土日と普通の平日は何も返さない。** 毎週そうなのだから、言う値打ちがない。
    """
    if not plan:
        return ""
    lines = []
    if plan.get("holiday") and not plan.get("weekend"):
        lines.append(f"  {plan['holiday']}で仕事は休み")
    if plan.get("meeting"):
        start = f"{plan['from']}から" if plan.get("from") else ""
        lines.append(f"  全体会議（帰社日）。{start}出社する")
    if not lines:
        return ""
    return "今日の予定:\n" + "\n".join(lines)
