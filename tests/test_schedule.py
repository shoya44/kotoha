"""仕事の予定。外にも記憶にも聞きに行かず、曜日と日付だけで決める。

土日祝は休み、平日は9:00〜17:30。全体会議（帰社日）は年7回。
"""

import unittest
from datetime import date

from kotoha.talk import schedule


class WorkdayTests(unittest.TestCase):
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
        self.assertTrue(schedule.today(date(2026, 9, 18))["meeting"])
        self.assertTrue(schedule.today(date(2027, 3, 19))["meeting"])
        self.assertFalse(schedule.today(date(2026, 9, 17))["meeting"])

    def test_every_meeting_day_falls_on_a_workday(self):
        """休みの日に帰社日が置かれていたら、写し間違いを疑う。"""
        for stamp in sorted(schedule.MEETINGS):
            with self.subTest(stamp=stamp):
                day = date.fromisoformat(stamp)
                self.assertTrue(schedule.today(day)["working"], stamp)


class BlockTests(unittest.TestCase):
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
        said = self.said(date(2026, 9, 18))
        self.assertIn("全体会議", said)
        self.assertIn("09:00", said)

    def test_nothing_at_all_is_not_an_error(self):
        self.assertEqual(schedule.block(None), "")


class CalendarAgeTests(unittest.TestCase):
    def test_the_holidays_have_not_run_out(self):
        """**このテストが落ちたら、内閣府の一覧から翌年ぶんを写す。**

        https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv
        `talk/schedule.py` の HOLIDAYS と UNTIL を見直す。
        """
        self.assertGreaterEqual(
            schedule.UNTIL, date.today(),
            "祝日の一覧が尽きた。内閣府の一覧から写し直す",
        )

    def test_the_meetings_have_not_run_out(self):
        """**このテストが落ちたら、来年度の帰社日を聞いて入れ直す。**"""
        self.assertGreaterEqual(
            date.fromisoformat(max(schedule.MEETINGS)), date.today(),
            "全体会議の日程が尽きた。来年度ぶんを入れる",
        )


class LineTests(unittest.TestCase):
    """毎回のプロンプトに添える1行。**土日も平日も、必ず何か返す。**

    block（朝のひとこと用）と違い、こちらは黙らない。ことはが今日を
    知っているための事実なので、「普通の平日」も伝える値打ちがある。
    """

    def said(self, day):
        return schedule.line(schedule.today(day))

    def test_a_plain_weekday_says_the_working_hours(self):
        said = self.said(date(2026, 9, 24))
        self.assertIn("仕事", said)
        self.assertIn("17:30", said)

    def test_the_weekend_is_a_day_off(self):
        self.assertEqual(self.said(date(2026, 9, 19)), "今日: 休み")

    def test_a_holiday_says_which_one(self):
        said = self.said(date(2026, 9, 21))
        self.assertIn("休み", said)
        self.assertIn("敬老の日", said)

    def test_the_meeting_day_says_both(self):
        said = self.said(date(2026, 9, 18))
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
        for day in (date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 24)):
            with self.subTest(day=day):
                said = self.said(day)
                for pushy in ("しろ", "すること", "ように", "手短"):
                    self.assertNotIn(pushy, said)
