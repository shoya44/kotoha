"""プロンプトに渡す時刻・経過・様子の検証。一時DBだけを使い、LLMは呼ばない。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha chat ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import chat, db  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def ago(**delta):
    moment = datetime.now(timezone.utc) - timedelta(**delta)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class ElapsedPhraseTests(unittest.TestCase):
    def test_missing_or_broken_record(self):
        self.assertEqual(chat.elapsed_phrase(None), "初めての会話")
        self.assertEqual(chat.elapsed_phrase(""), "初めての会話")
        self.assertEqual(chat.elapsed_phrase("2026-09-17"), "不明")

    def test_scales_from_seconds_to_years(self):
        cases = [
            (dict(seconds=5), "たった今"),
            (dict(minutes=3), "3分前"),
            (dict(hours=5), "5時間前"),
            (dict(hours=30), "昨日"),
            (dict(days=3), "3日前"),
            (dict(days=10), "1週間前"),
            (dict(days=60), "2か月前"),
            (dict(days=400), "1年以上前"),
        ]
        for delta, expected in cases:
            with self.subTest(**delta):
                self.assertEqual(chat.elapsed_phrase(ago(**delta)), expected)

    def test_utc_record_is_not_read_as_a_timezone_offset(self):
        """UTCの記録をそのまま渡すと時差ぶんの間隔に見える退行を防ぐ。"""
        self.assertEqual(chat.elapsed_phrase(ago(seconds=1)), "たった今")


class SituationTests(unittest.TestCase):
    def test_every_hour_has_a_situation(self):
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertTrue(chat.situation(hour))

    def test_boundaries_match_the_avatar_groups(self):
        """static/index.html の getAvatarGroup() と区切りを揃える。"""
        groups = {hour: chat.situation(hour) for hour in range(24)}
        self.assertEqual(groups[6], groups[10])
        self.assertNotEqual(groups[5], groups[6])
        self.assertEqual(groups[21], groups[1])  # 夜更かしは日付をまたぐ
        self.assertNotEqual(groups[1], groups[2])
        self.assertEqual(groups[2], groups[5])


class TimeBlockTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def time_lines(self):
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        return [
            line for line in prompt.split("\n")
            if line.startswith(("現在:", "前回の会話:", "今のことは:"))
        ]

    def test_prompt_carries_all_three_lines(self):
        self.assertEqual(len(self.time_lines()), 3)

    def test_weekday_is_japanese(self):
        current = self.time_lines()[0]
        self.assertIn(chat.WEEKDAYS[datetime.now().weekday()] + "曜日", current)

    def test_first_conversation_then_just_now(self):
        self.assertIn("初めての会話", self.time_lines()[1])
        db.set_state(self.conn, "last_conversation_at", db.now_utc())
        self.conn.commit()
        self.assertIn("たった今", self.time_lines()[1])


if __name__ == "__main__":
    unittest.main()
