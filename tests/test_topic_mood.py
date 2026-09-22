"""段2: さっきまでの話題が続くこと。機嫌が段階的に薄れること。時計を止めて確かめる。"""

import unittest
from datetime import datetime

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("topic_mood")

from kotoha.memory import db  # noqa: E402
from kotoha.talk import chat, figure  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class MoodFadeTests(DbCase):
    """1時間で半分。すねていても、そのうち普通に戻る。話していれば進まない。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "MOOD_HALF_LIFE_HOURS", config.MOOD_HALF_LIFE_HOURS)
        config.MOOD_HALF_LIFE_HOURS = 1.0

    def sulk(self):
        db.set_state(self.conn, db.MOOD, "すねている")
        db.set_state(self.conn, db.MOOD_WHY, "返事がなかったから")
        db.set_state(self.conn, db.MOOD_AT, db.now_utc())
        self.conn.commit()

    def test_it_fades_in_steps_and_then_returns_to_the_time_of_day(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.sulk()
            self.assertEqual(chat.current_mood(self.conn, 12), "すねている")
            self.assertEqual(chat.mood_note(self.conn), "")
            tick.advance(minutes=70)
            self.assertEqual(chat.current_mood(self.conn, 13), "すねている")
            self.assertEqual(chat.mood_note(self.conn), "薄れてきた")
            tick.advance(minutes=60)
            self.assertEqual(chat.mood_note(self.conn), "だいぶ薄れた")
            tick.advance(minutes=60)
            self.assertEqual(chat.current_mood(self.conn, 15), figure.prior(15))
            self.assertEqual(chat.mood_reason(self.conn, 15), "")

    def test_the_prompt_says_how_faded_it_is(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.sulk()
            tick.advance(minutes=70)
            prompt = chat.build_prompt(self.conn, "ごめんね", [], [], [])
            line = next(l for l in prompt.split("\n") if l.startswith("今の機嫌:"))
            self.assertIn("すねている", line)
            self.assertIn("きっかけ: 返事がなかったから", line)
            self.assertIn("薄れてきた", line)

    def test_talking_resets_the_clock(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.sulk()
            tick.advance(minutes=100)
            turn_id = db.start_turn(self.conn, "user", "ねえ")
            chat._finish(self.conn, turn_id, "なに", [], "slow")     # 機嫌のタグは無い
            self.assertEqual(chat.mood_note(self.conn), "")
            tick.advance(minutes=100)
            self.assertEqual(chat.current_mood(self.conn, 15), "すねている")

    def test_no_mood_ever_means_no_intensity(self):
        self.assertEqual(chat.mood_intensity(self.conn), 0.0)
        self.assertEqual(chat.current_mood(self.conn, 12), figure.prior(12))


class TopicTests(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "TOPIC_KEEP_HOURS", config.TOPIC_KEEP_HOURS)
        config.TOPIC_KEEP_HOURS = 24
        self.addCleanup(setattr, chat.llm, "chat", chat.llm.chat)

    def reply_with(self, raw):
        chat.llm.chat = lambda prompt, max_tokens=None: raw

    def test_the_tag_is_parsed_and_stripped(self):
        clean, topic = chat.parse_topic("うん、そうだね。[TOPIC: 引っ越しの話]")
        self.assertEqual((clean, topic), ("うん、そうだね。", "引っ越しの話"))
        self.assertEqual(chat.parse_topic("うん。")[1], None)
        self.assertEqual(chat.parse_topic("じゃあね。[TOPIC: なし]")[1], "")

    def test_a_turn_remembers_the_topic_and_the_screen_never_sees_it(self):
        self.reply_with("大変そうだね。[TOPIC: 引っ越しの話]")
        turn = chat.run_turn(self.conn, "土曜は引っ越しの手伝い")
        self.assertEqual(turn.reply, "大変そうだね。")
        self.assertEqual(db.get_state(self.conn, db.TOPIC), "引っ越しの話")
        stored = self.conn.execute(
            "SELECT text FROM messages WHERE role = 'assistant'").fetchone()["text"]
        self.assertNotIn("TOPIC", stored)

    def test_no_tag_keeps_the_topic(self):
        self.reply_with("大変そうだね。[TOPIC: 引っ越しの話]")
        chat.run_turn(self.conn, "土曜は引っ越しの手伝い")
        self.reply_with("ふーん")
        chat.run_turn(self.conn, "疲れた")
        self.assertEqual(db.get_state(self.conn, db.TOPIC), "引っ越しの話")

    def test_none_ends_the_topic(self):
        self.reply_with("大変そうだね。[TOPIC: 引っ越しの話]")
        chat.run_turn(self.conn, "土曜は引っ越しの手伝い")
        self.reply_with("おやすみ。[TOPIC: なし]")
        chat.run_turn(self.conn, "寝るね")
        self.assertIsNone(chat.current_topic(self.conn))
        prompt = chat.build_prompt(self.conn, "おはよ", [], [], [])
        self.assertFalse(any(l.startswith("さっきまでの話題:") for l in prompt.splitlines()))

    def test_the_prompt_carries_it_with_how_long_ago(self):
        with Clock(datetime(2026, 9, 22, 21, 0)) as tick:
            self.reply_with("大変そうだね。[TOPIC: 引っ越しの話]")
            chat.run_turn(self.conn, "土曜は引っ越しの手伝い")
            tick.advance(hours=10)
            prompt = chat.build_prompt(self.conn, "おはよ", [], [], [])
            self.assertIn("さっきまでの話題: 引っ越しの話（10時間前から）", prompt)

    def test_it_is_forgotten_after_a_day(self):
        with Clock(datetime(2026, 9, 22, 21, 0)) as tick:
            self.reply_with("大変そうだね。[TOPIC: 引っ越しの話]")
            chat.run_turn(self.conn, "土曜は引っ越しの手伝い")
            tick.advance(hours=25)
            self.assertIsNone(chat.current_topic(self.conn))

    def test_her_own_words_may_set_it(self):
        self.reply_with("そういえば、引っ越しどうだった？[TOPIC: 引っ越しの話]")
        chat.speak(self.conn, "一声かける")
        self.assertEqual(db.get_state(self.conn, db.TOPIC), "引っ越しの話")

    def test_a_long_topic_is_cut(self):
        _, topic = chat.parse_topic("[TOPIC: " + "あ" * 80 + "]")
        self.assertEqual(len(topic), chat.TOPIC_CHARS)


if __name__ == "__main__":
    unittest.main()
