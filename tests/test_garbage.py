"""ゴミの日。曜日と第何週だけで決まるので、外にも記憶にも聞きに行かない。

**暦そのものは持ち主のもの**で、`data/calendars/garbage.json`（git の外）に
あります。ここで確かめるのは数え方なので、作り話の暦を入れて試します。
持ち主の暦が切れていないかだけは、実物があるときに見ます。
"""

import unittest
from datetime import date

from kotoha.talk import garbage

# 作り話の暦。火・金に燃やすごみ、水にプラスチック、土に資源、第1・第3木に
# 燃やさないごみ。**どこの区のものでもありません。**
WEEKLY = {1: "燃やすごみ", 2: "プラスチック", 4: "燃やすごみ", 5: "資源"}
MONTHLY = {3: ("燃やさないごみ", (1, 3))}
BREAKS = (((12, 24), (12, 31)), ((1, 1), (1, 10)))


class CalendarCase(unittest.TestCase):
    def setUp(self):
        for name, value in (("WEEKLY", WEEKLY), ("MONTHLY", MONTHLY), ("BREAKS", BREAKS)):
            self.addCleanup(setattr, garbage, name, getattr(garbage, name))
            setattr(garbage, name, value)


class ScheduleTests(CalendarCase):
    def test_the_days_that_come_every_week(self):
        # 2026-04-06(月) から1週間ぶん
        expected = {
            date(2026, 4, 6): [],                  # 月
            date(2026, 4, 7): ["燃やすごみ"],      # 火
            date(2026, 4, 8): ["プラスチック"],    # 水
            date(2026, 4, 9): [],                  # 木（第2なので月2回のぶんは無い）
            date(2026, 4, 10): ["燃やすごみ"],     # 金
            date(2026, 4, 11): ["資源"],           # 土
            date(2026, 4, 12): [],                 # 日
        }
        for day, items in expected.items():
            with self.subTest(day=day):
                self.assertEqual(garbage.today(day), items)

    def test_the_first_and_third_thursday(self):
        self.assertEqual(garbage.today(date(2026, 4, 2)), ["燃やさないごみ"])    # 第1木
        self.assertEqual(garbage.today(date(2026, 4, 16)), ["燃やさないごみ"])   # 第3木

    def test_the_other_thursdays_have_nothing(self):
        self.assertEqual(garbage.today(date(2026, 4, 9)), [])    # 第2木
        self.assertEqual(garbage.today(date(2026, 4, 23)), [])   # 第4木

    def test_the_new_year_break_is_not_decided_here(self):
        """年末年始だけは曜日どおりではない。知らないことを知らないと言う。"""
        self.assertIsNone(garbage.today(date(2026, 12, 28)))
        self.assertIsNone(garbage.today(date(2027, 1, 5)))
        self.assertIsNotNone(garbage.today(date(2026, 12, 21)))


class BlockTests(unittest.TestCase):
    def test_nothing_to_say_on_a_day_with_no_collection(self):
        self.assertEqual(garbage.block([]), "")

    def test_the_day_is_handed_over_as_words(self):
        said = garbage.block(["燃やすごみ"])
        self.assertIn("燃やすごみ", said)

    def test_two_kinds_are_handed_over_together(self):
        said = garbage.block(["資源", "燃やさないごみ"])
        self.assertIn("資源", said)
        self.assertIn("燃やさないごみ", said)

    def test_the_break_asks_the_ward_instead(self):
        self.assertIn("お知らせ", garbage.block(None))


class CalendarAgeTests(unittest.TestCase):
    """**持ち主の実物を見る。** 入れていないなら、見張るものが無い。"""

    @unittest.skipUnless(garbage.UNTIL, "data/calendars/garbage.json が無い")
    def test_the_calendar_has_not_run_out(self):
        """**このテストが落ちたら、区の新しいカレンダーに差し替える。**

        曜日はそのままのことが多いが、確かめずに言い続けるほうが困る。
        `data/calendars/garbage.json` を新しいものに差し替える。
        """
        self.assertGreaterEqual(
            garbage.UNTIL, date.today(),
            "ゴミのカレンダーの有効期間が切れた。区の新しいものに差し替える",
        )


class NoCalendarTests(unittest.TestCase):
    """暦を入れていない人には、ゴミの話は出ない。知らないことを言わない。"""

    def setUp(self):
        for name in ("WEEKLY", "MONTHLY", "BREAKS"):
            self.addCleanup(setattr, garbage, name, getattr(garbage, name))
        garbage.WEEKLY, garbage.MONTHLY, garbage.BREAKS = {}, {}, ()

    def test_nothing_is_collected_and_nothing_is_said(self):
        self.assertEqual(garbage.today(date(2026, 4, 6)), [])
        self.assertEqual(garbage.block(garbage.today(date(2026, 4, 6))), "")

