"""ゴミの日。曜日と第何週だけで決まるので、外にも記憶にも聞きに行かない。

足立区のカレンダー（足立1〜4丁目ほかの地区）に書かれている決まり:
  プラスチック 週1回【土】／資源 週1回【火】
  燃やすごみ 週2回【月・木】／燃やさないごみ 月2回【第2・第4 金】
"""

import unittest
from datetime import date

from kotoha.talk import garbage


class ScheduleTests(unittest.TestCase):
    def test_the_days_that_come_every_week(self):
        # 2026-04-06(月) から1週間ぶん
        expected = {
            date(2026, 4, 6): ["燃やすごみ"],      # 月
            date(2026, 4, 7): ["資源"],            # 火
            date(2026, 4, 8): [],                  # 水
            date(2026, 4, 9): ["燃やすごみ"],      # 木
            date(2026, 4, 11): ["プラスチック"],   # 土
            date(2026, 4, 12): [],                 # 日
        }
        for day, items in expected.items():
            with self.subTest(day=day):
                self.assertEqual(garbage.today(day), items)

    def test_the_second_and_fourth_friday(self):
        self.assertEqual(garbage.today(date(2026, 4, 10)), ["燃やさないごみ"])   # 第2金
        self.assertEqual(garbage.today(date(2026, 4, 24)), ["燃やさないごみ"])   # 第4金

    def test_the_other_fridays_have_nothing(self):
        self.assertEqual(garbage.today(date(2026, 4, 3)), [])    # 第1金
        self.assertEqual(garbage.today(date(2026, 4, 17)), [])   # 第3金

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
    def test_the_calendar_has_not_run_out(self):
        """**このテストが落ちたら、区の新しいカレンダーに差し替える。**

        曜日はそのままのことが多いが、確かめずに言い続けるほうが困る。
        `talk/garbage.py` の WEEKLY / MONTHLY / UNTIL を見直す。
        """
        self.assertGreaterEqual(
            garbage.UNTIL, date.today(),
            "ゴミのカレンダーの有効期間が切れた。区の新しいものに差し替える",
        )
