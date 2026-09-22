"""段5: 気分は2つの数（快−不快・元気−疲れ）。相手への信頼は週の振り返りで動く。"""

import unittest
from datetime import datetime

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("mood_axes")

from kotoha.memory import db, diary, habits  # noqa: E402
from kotoha.talk import chat, figure  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class AxesTests(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "MOOD_HALF_LIFE_HOURS", config.MOOD_HALF_LIFE_HOURS)
        config.MOOD_HALF_LIFE_HOURS = 1.0

    def declare(self, label):
        db.set_state(self.conn, db.MOOD, label)
        db.set_state(self.conn, db.MOOD_WHY, "なんとなく")
        db.set_state(self.conn, db.MOOD_AT, db.now_utc())
        self.conn.commit()

    def test_every_label_has_a_point_and_the_table_matches_the_vocabulary(self):
        self.assertEqual(set(chat.MOOD_AXES), set(chat.MOODS))
        for v, a in chat.MOOD_AXES.values():
            self.assertTrue(-1 <= v <= 1 and -1 <= a <= 1)

    def test_no_declaration_means_the_time_of_day(self):
        self.assertEqual(chat.mood_axes(self.conn, 12), chat.MOOD_AXES[figure.prior(12)])
        self.assertEqual(chat.mood_axes(self.conn, 3), chat.MOOD_AXES[figure.SLEEPY_MOOD])

    def test_a_fresh_declaration_is_the_labels_point(self):
        with Clock(datetime(2026, 9, 22, 12, 0)):
            self.declare("すねている")
            v, a = chat.mood_axes(self.conn, 12)
            self.assertAlmostEqual(v, chat.MOOD_AXES["すねている"][0])
            self.assertAlmostEqual(a, chat.MOOD_AXES["すねている"][1])

    def test_it_slides_toward_the_time_of_day_as_it_fades(self):
        """半減期ぶんたてば、申告の点と下地の点のちょうど真ん中。"""
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.declare("すねている")
            tick.advance(hours=1)
            v, a = chat.mood_axes(self.conn, 13)
            self.assertAlmostEqual(v, chat.MOOD_AXES["すねている"][0] / 2)
            self.assertAlmostEqual(a, chat.MOOD_AXES["すねている"][1] / 2)
            self.assertEqual(chat.current_mood(self.conn, 13), "すねている")   # ラベルはまだ申告どおり

    def test_sleepiness_outside_its_hours_is_not_carried_into_the_axes(self):
        self.declare(figure.SLEEPY_MOOD)
        self.assertEqual(chat.mood_axes(self.conn, 12), chat.MOOD_AXES[figure.prior(12)])

    def test_words_follow_the_point(self):
        self.assertEqual(chat.mood_words((0.0, 0.0)), "")
        self.assertEqual(chat.mood_words((-0.6, 0.1)), "気分は沈み気味")
        self.assertEqual(chat.mood_words((-0.2, -0.5)), "気分は少し沈み気味、元気は低め")
        self.assertEqual(chat.mood_words((0.6, 0.3)), "気分は上向き、元気は少しある")
        self.assertEqual(chat.mood_words((0.0, -0.7)), "元気は低め")

    def test_the_prompt_carries_the_words_while_the_mood_fades(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.declare("すねている")
            tick.advance(minutes=70)
            prompt = chat.build_prompt(self.conn, "ごめんね", [], [], [])
            line = next(l for l in prompt.splitlines() if l.startswith("今の機嫌:"))
            self.assertIn("すねている", line)
            self.assertIn("薄れてきた", line)
            self.assertIn("気分は少し沈み気味", line)

    def test_a_flat_mood_adds_no_words(self):
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [])
        line = next(l for l in prompt.splitlines() if l.startswith("今の機嫌:"))
        self.assertNotIn("気分は", line)


class Pen:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def chat(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.text


class TrustTests(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "DIARY_ENABLED", config.DIARY_ENABLED)
        config.DIARY_ENABLED = True
        self.addCleanup(setattr, habits, "llm", habits.llm)
        for back in range(1, 4):
            day = datetime.now().strftime(diary.DAY)
            self.conn.execute("INSERT OR IGNORE INTO diary(day, text, created_at) VALUES (?,?,?)",
                              (f"2026-09-{10 + back:02d}", "頼まれごとをちゃんとやった。", db.now_utc()))
        self.conn.commit()

    def test_nothing_decided_yet_means_no_line(self):
        self.assertEqual(habits.trust_line(self.conn), "")
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [])
        self.assertFalse(any(l.startswith("相手への信頼:") for l in prompt.splitlines()))

    def test_the_reflection_sets_it_and_the_prompt_carries_it(self):
        habits.llm = Pen('{"habits": [], "trust": {"level": "高い", "why": "頼まれごとを守っている"}}')
        habits.reflect(self.conn)
        self.assertEqual(habits.trust_line(self.conn), "相手への信頼: 高い（頼まれごとを守っている）")
        self.assertIn("相手への信頼: 高い", chat.build_prompt(self.conn, "やあ", [], [], []))

    def test_the_previous_trust_is_shown_to_the_reflection(self):
        db.set_state(self.conn, db.TRUST, "低め")
        db.set_state(self.conn, db.TRUST_WHY, "返事が無い")
        self.conn.commit()
        pen = Pen('{"habits": [], "trust": {"level": "ふつう", "why": "返事が戻ってきた"}}')
        habits.llm = pen
        habits.reflect(self.conn)
        self.assertIn("相手への信頼: 低め（返事が無い）", pen.prompts[0])
        self.assertEqual(db.get_state(self.conn, db.TRUST), "ふつう")

    def test_an_unknown_level_keeps_the_previous(self):
        db.set_state(self.conn, db.TRUST, "高い")
        self.conn.commit()
        habits.llm = Pen('{"habits": [], "trust": {"level": "最高", "why": "x"}}')
        habits.reflect(self.conn)
        self.assertEqual(db.get_state(self.conn, db.TRUST), "高い")

    def test_a_missing_trust_keeps_the_previous(self):
        db.set_state(self.conn, db.TRUST, "ふつう")
        self.conn.commit()
        habits.llm = Pen('{"habits": []}')
        habits.reflect(self.conn)
        self.assertEqual(db.get_state(self.conn, db.TRUST), "ふつう")

    def test_the_reason_is_cut_short(self):
        habits.llm = Pen('{"habits": [], "trust": {"level": "ふつう", "why": "' + "あ" * 200 + '"}}')
        habits.reflect(self.conn)
        self.assertEqual(len(db.get_state(self.conn, db.TRUST_WHY)), habits.TRUST_WHY_CHARS)


if __name__ == "__main__":
    unittest.main()
