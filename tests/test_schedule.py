"""仕事の予定。外にも記憶にも聞きに行かず、曜日と日付だけで決める。

土日祝は休み、平日は勤務。**勤務時間と会議の日程は持ち主のもの**で、
`data/calendars/work.json`（git の外）にあります。ここで確かめるのは
数え方なので、作り話の勤め先を入れて試します。持ち主のぶんが尽きて
いないかだけは、実物があるときに見ます。
"""

import unittest
from datetime import date

from kotoha.talk import schedule

# 作り話の勤め先。**誰の予定でもありません。**
WORK_FROM, WORK_TO = "10:00", "18:00"
MEETINGS = frozenset({"2026-05-15", "2026-06-19"})    # どちらも金曜


class WorkCase(unittest.TestCase):
    def setUp(self):
        for name, value in (("WORK_FROM", WORK_FROM), ("WORK_TO", WORK_TO),
                            ("MEETINGS", MEETINGS)):
            self.addCleanup(setattr, schedule, name, getattr(schedule, name))
            setattr(schedule, name, value)


class WorkdayTests(WorkCase):
    def test_a_plain_weekday_is_work(self):
        plan = schedule.today(date(2026, 9, 24))      # 木
        self.assertTrue(plan["working"])
        self.assertIsNone(plan["holiday"])

    def test_the_weekend_is_off(self):
        for day in (date(2026, 9, 19), date(2026, 9, 20)):   # 土日
            with self.subTest(day=day):
                self.assertFalse(schedule.today(day)["working"])

    def test_a_holiday_on_a_weekday_is_off(self):
        plan = schedule.today(date(2026, 9, 21))      # 月・敬老の日
        self.assertFalse(plan["working"])
        self.assertEqual(plan["holiday"], "敬老の日")

    def test_the_meeting_days_are_known(self):
        self.assertTrue(schedule.today(date(2026, 5, 15))["meeting"])
        self.assertTrue(schedule.today(date(2026, 6, 19))["meeting"])
        self.assertFalse(schedule.today(date(2026, 5, 14))["meeting"])


class BlockTests(WorkCase):
    """**今日だけ違うときだけ言う。** 毎週そうなことは、言う値打ちがない。"""

    def said(self, day):
        return schedule.block(schedule.today(day))

    def test_a_plain_weekday_says_nothing(self):
        self.assertEqual(self.said(date(2026, 9, 24)), "")

    def test_the_weekend_says_nothing(self):
        self.assertEqual(self.said(date(2026, 9, 19)), "")

    def test_a_holiday_is_worth_saying(self):
        self.assertIn("敬老の日", self.said(date(2026, 9, 21)))

    def test_the_meeting_day_is_worth_saying(self):
        said = self.said(date(2026, 5, 15))
        self.assertIn("全体会議", said)
        self.assertIn(WORK_FROM, said)

    def test_nothing_at_all_is_not_an_error(self):
        self.assertEqual(schedule.block(None), "")


class CalendarAgeTests(unittest.TestCase):
    def test_the_holidays_have_not_run_out(self):
        """**このテストが落ちたら、内閣府の一覧から翌年ぶんを写す。**

        https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv
        `talk/schedule.py` の HOLIDAYS を見直す（祝日は公開情報なのでコードにある）。
        """
        self.assertGreaterEqual(
            schedule.UNTIL, date.today(),
            "祝日の一覧が尽きた。内閣府の一覧から写し直す",
        )

    @unittest.skipUnless(schedule.MEETINGS, "data/calendars/work.json が無い")
    def test_the_meetings_have_not_run_out(self):
        """**このテストが落ちたら、来年度の帰社日を聞いて入れ直す。**"""
        self.assertGreaterEqual(
            date.fromisoformat(max(schedule.MEETINGS)), date.today(),
            "全体会議の日程が尽きた。来年度ぶんを入れる",
        )

    @unittest.skipUnless(schedule.MEETINGS, "data/calendars/work.json が無い")
    def test_every_meeting_day_falls_on_a_workday(self):
        """休みの日に帰社日が置かれていたら、写し間違いを疑う。"""
        for stamp in sorted(schedule.MEETINGS):
            with self.subTest(stamp=stamp):
                self.assertTrue(schedule.today(date.fromisoformat(stamp))["working"], stamp)


class LineTests(WorkCase):
    """毎回のプロンプトに添える1行。**土日も平日も、必ず何か返す。**

    block（朝のひとこと用）と違い、こちらは黙らない。ことはが今日を
    知っているための事実なので、「普通の平日」も伝える値打ちがある。
    """

    def said(self, day):
        return schedule.line(schedule.today(day))

    def test_a_plain_weekday_says_the_working_hours(self):
        said = self.said(date(2026, 9, 24))
        self.assertIn("仕事", said)
        self.assertIn(WORK_TO, said)

    def test_the_weekend_is_a_day_off(self):
        self.assertEqual(self.said(date(2026, 9, 19)), "今日: 休み")

    def test_a_holiday_says_which_one(self):
        said = self.said(date(2026, 9, 21))
        self.assertIn("休み", said)
        self.assertIn("敬老の日", said)

    def test_the_meeting_day_says_both(self):
        said = self.said(date(2026, 5, 15))
        self.assertIn("仕事", said)
        self.assertIn("全体会議", said)

    def test_it_never_goes_silent(self):
        """1行も返さない日があると、ことはは今日を知らないまま話す。"""
        day = date(2026, 4, 1)
        for _ in range(400):
            with self.subTest(day=day):
                self.assertTrue(self.said(day))
            day = date.fromordinal(day.toordinal() + 1)

    def test_it_does_not_tell_her_what_to_do(self):
        """渡すのは事実だけ。指示を混ぜると窮屈になる。"""
        for day in (date(2026, 5, 15), date(2026, 9, 21), date(2026, 9, 24)):
            with self.subTest(day=day):
                said = self.said(day)
                for pushy in ("しろ", "すること", "ように", "手短"):
                    self.assertNotIn(pushy, said)


class NoWorkCalendarTests(unittest.TestCase):
    """勤め先を入れていない人には、時刻も会議も出ない。"""

    def setUp(self):
        for name in ("WORK_FROM", "WORK_TO", "MEETINGS"):
            self.addCleanup(setattr, schedule, name, getattr(schedule, name))
        schedule.WORK_FROM, schedule.WORK_TO, schedule.MEETINGS = "", "", frozenset()

    def test_a_weekday_is_still_a_workday_without_the_hours(self):
        said = schedule.line(schedule.today(date(2026, 9, 24)))
        self.assertEqual(said, "今日: 仕事")

    def test_a_holiday_still_says_which_one(self):
        said = schedule.line(schedule.today(date(2026, 9, 21)))
        self.assertIn("敬老の日", said)
