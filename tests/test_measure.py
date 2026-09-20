"""計測の検証。**測ってから決める**ための数字が、ちゃんと溜まること。

機嫌がどれくらい動いているか、想起した記憶がどれくらい「使った」と申告
されるか。どちらも、いまのDBには残っていなかった。決める前に数える。
"""

import unittest

from kotoha import config, notify
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("measure")

from kotoha.memory import db  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class MoodRateTests(DbCase):
    def reply_with(self, raw):
        original = chat.llm.chat
        chat.llm.chat = lambda prompt, max_tokens=None: raw
        self.addCleanup(setattr, chat.llm, "chat", original)

    def test_nothing_is_counted_before_the_first_change(self):
        self.assertIsNone(db.mood_rate(self.conn))

    def test_a_change_is_counted_with_the_turns_around_it(self):
        self.reply_with("おかえりー [MOOD: 機嫌がいい]")
        chat.run_turn(self.conn, "ただいま")
        self.reply_with("うん")
        chat.run_turn(self.conn, "そう")
        chat.run_turn(self.conn, "ふうん")
        changed, turns = db.mood_rate(self.conn)
        self.assertEqual(changed, 1)
        self.assertEqual(turns, 3)

    def test_the_same_mood_twice_counts_twice(self):
        """来た回数をそのまま数える。解釈はあとでする。"""
        self.reply_with("ん [MOOD: 機嫌がいい]")
        chat.run_turn(self.conn, "やあ")
        chat.run_turn(self.conn, "やあ")
        self.assertEqual(db.mood_rate(self.conn)[0], 2)


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, config, "DEBUG", config.DEBUG)
        self.path = notify.LOG_PATH.parent / "retrieve.log"
        if self.path.exists():
            self.path.unlink()

    def test_nothing_is_written_while_it_is_quiet(self):
        config.DEBUG = False
        chat.trace_recall([{"id": 1}], [], [1])
        self.assertFalse(self.path.exists())

    def test_what_was_recalled_and_what_was_claimed(self):
        config.DEBUG = True
        chat.trace_recall([{"id": 1}], [{"id": 2}], [1])
        written = self.path.read_text(encoding="utf-8")
        self.assertIn("想起 [1, 2]", written)
        self.assertIn("申告 [1]", written)
