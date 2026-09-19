"""持っている暦の期限を、ことは自身に言わせること。

テストでも見張っているが、**テストは誰も自動では走らせない**。落ちるのは
手で叩いたときだけで、それは持ち主に届く道ではない。
"""

import unittest
from datetime import date, timedelta

from kotoha.talk import schedule, upkeep


class DeadlineTests(unittest.TestCase):
    def test_it_watches_all_three(self):
        names = [name for name, _until, _source in upkeep.deadlines()]
        self.assertEqual(len(names), 3)
        for word in ("ゴミ", "祝日", "全体会議"):
            with self.subTest(word=word):
                self.assertTrue(any(word in name for name in names))

    def test_the_holiday_deadline_comes_from_the_table(self):
        """手で書くと、表を足したのにこちらを直し忘れる。"""
        self.assertEqual(schedule.UNTIL, date.fromisoformat(max(schedule.HOLIDAYS)))


class NoticeTests(unittest.TestCase):
    def meeting_end(self):
        return date.fromisoformat(max(schedule.MEETINGS))

    def monday_before(self, until, days):
        """その期限の days 日前ごろの月曜。"""
        day = until - timedelta(days=days)
        return day - timedelta(days=day.weekday())

    def test_nothing_is_said_while_it_is_far_off(self):
        day = self.monday_before(self.meeting_end(), 90)
        self.assertEqual(upkeep.stale(day), [])

    def test_it_warns_on_a_monday_when_the_end_is_near(self):
        day = self.monday_before(self.meeting_end(), 20)
        names = [one["name"] for one in upkeep.stale(day)]
        self.assertIn("全体会議の日程", names)

    def test_it_keeps_quiet_on_other_days_before_the_end(self):
        """30日のあいだ毎朝言われるとうるさい。予告は週に一度でいい。"""
        monday = self.monday_before(self.meeting_end(), 20)
        for other in range(1, 7):
            day = monday + timedelta(days=other)
            if day > self.meeting_end():
                continue
            with self.subTest(day=day):
                self.assertEqual(upkeep.stale(day), [])

    def test_once_it_has_run_out_it_is_said_every_morning(self):
        """うるさいのが正しい。黙ると会議を落とす。"""
        for after in (1, 2, 3, 40):
            day = self.meeting_end() + timedelta(days=after)
            with self.subTest(day=day):
                names = [one["name"] for one in upkeep.stale(day)]
                self.assertIn("全体会議の日程", names)

    def test_everything_runs_out_in_the_end(self):
        far = max(until for _name, until, _source in upkeep.deadlines())
        self.assertEqual(len(upkeep.stale(far + timedelta(days=1))), 3)


class BlockTests(unittest.TestCase):
    def test_nothing_to_say_while_everything_is_in_date(self):
        self.assertEqual(upkeep.block([]), "")

    def test_a_coming_end_says_how_long_is_left(self):
        said = upkeep.block([{"name": "祝日の一覧", "source": "内閣府の一覧",
                              "left": 12, "over": False}])
        self.assertIn("あと12日", said)
        self.assertIn("内閣府の一覧", said)

    def test_an_end_that_has_passed_says_so(self):
        said = upkeep.block([{"name": "祝日の一覧", "source": "内閣府の一覧",
                              "left": -3, "over": True}])
        self.assertIn("切れている", said)
        self.assertNotIn("あと", said)
