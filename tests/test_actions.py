"""頼まれごとの検証。実際にプロセスを起こしたり止めたりはしない。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha actions ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db  # noqa: E402
from kotoha.talk import actions, chat, presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class RunTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, config, "ACTIONS_ENABLED", config.ACTIONS_ENABLED)
        config.ACTIONS_ENABLED = True
        self.addCleanup(setattr, actions, "log", actions.log)
        self.logged = []
        actions.log = self.logged.append
        self.addCleanup(actions.ACTIONS.update, dict(actions.ACTIONS))
        self.done = []
        actions.ACTIONS["音声エンジンを起こす"] = lambda: self.done.append("起こした") or "起こした"

    def test_a_listed_action_runs(self):
        self.assertEqual(actions.run("音声エンジンを起こす"), "起こした")
        self.assertEqual(self.done, ["起こした"])

    def test_an_unlisted_action_does_nothing(self):
        """ことはが好きなことを書けてしまうと、表にした意味がない。"""
        self.assertEqual(actions.run("PCを初期化する"), "")
        self.assertEqual(self.done, [])

    def test_switched_off_does_nothing(self):
        config.ACTIONS_ENABLED = False
        self.assertEqual(actions.run("音声エンジンを起こす"), "")
        self.assertEqual(self.done, [])

    def test_a_failure_does_not_escape(self):
        """操作の失敗で会話を落とさない。"""
        actions.ACTIONS["音声エンジンを起こす"] = lambda: 1 / 0
        self.assertEqual(actions.run("音声エンジンを起こす"), "")

    def test_everything_is_written_down(self):
        actions.run("音声エンジンを起こす")
        actions.run("知らないこと")
        self.assertEqual(len(self.logged), 2)

    def test_the_offer_lists_what_can_be_done(self):
        text = actions.offer()
        for name in actions.names():
            self.assertIn(name, text)

    def test_the_offer_is_empty_when_switched_off(self):
        config.ACTIONS_ENABLED = False
        self.assertEqual(actions.offer(), "")


class ParseTests(unittest.TestCase):
    def test_the_tag_is_taken_off_the_reply(self):
        clean, todo = chat.parse_action("起こしとくね [DO: 音声エンジンを起こす]")
        self.assertEqual(clean, "起こしとくね")
        self.assertEqual(todo, "音声エンジンを起こす")

    def test_an_unknown_name_is_dropped(self):
        clean, todo = chat.parse_action("やっとくね [DO: PCを初期化する]")
        self.assertIsNone(todo)
        self.assertNotIn("DO", clean)

    def test_a_reply_without_a_tag_is_untouched(self):
        clean, todo = chat.parse_action("おかえり")
        self.assertEqual(clean, "おかえり")
        self.assertIsNone(todo)


class GateTests(unittest.TestCase):
    """機械の話のときだけ、中身とできることを渡す。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        self.addCleanup(setattr, presence, "details", presence.details)
        presence.details = lambda: "Cドライブ 空き98GB"

    def build(self, text, fast=False):
        return chat.build_prompt(self.conn, text, [], [], [], fast=fast)

    def test_a_machine_question_gets_the_details(self):
        self.assertIn("Cドライブ 空き98GB", self.build("容量どれくらい残ってる？"))

    def test_ordinary_talk_does_not(self):
        """毎回渡すと150字ほど食う。ほとんどの会話では要らない。"""
        self.assertNotIn("Cドライブ", self.build("ただいま"))

    def test_the_short_path_stays_short(self):
        self.assertNotIn("Cドライブ", self.build("容量どれくらい残ってる？", fast=True))

    def test_what_can_be_done_comes_with_it(self):
        self.assertIn("頼まれたらできること", self.build("音声エンジン止めて"))

    def test_words_that_are_not_about_the_machine(self):
        for text in ("おかえり", "今日はしんどい", "ごはん食べた？"):
            self.assertFalse(presence.asked_about_machine(text), text)

    def test_words_that_are(self):
        for text in ("容量やばい", "PC重い", "Ollama起こして", "メモリ足りてる？"):
            self.assertTrue(presence.asked_about_machine(text), text)


if __name__ == "__main__":
    unittest.main()
