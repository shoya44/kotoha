"""相手の習慣。日記が重なったところで週に一度固め、確かめ直されなければ薄れる。"""

import unittest
from datetime import datetime, timedelta, timezone

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("habits")

from kotoha.memory import db, diary, habits  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class Pen:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def chat(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.text


class HabitCase(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "DIARY_ENABLED", config.DIARY_ENABLED)
        config.DIARY_ENABLED = True
        self.addCleanup(setattr, habits, "llm", habits.llm)

    def days_of_diary(self, n, text="夜10時に話した。"):
        for back in range(1, n + 1):
            day = (datetime.now() - timedelta(days=back)).strftime(diary.DAY)
            self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                              (day, text, db.now_utc()))
        self.conn.commit()

    def habit(self, text, retired=False):
        now = db.now_utc()
        self.conn.execute("INSERT INTO habits(text, first_at, confirmed_at, retired_at) "
                          "VALUES (?,?,?,?)", (text, now, now, now if retired else None))
        self.conn.commit()


class WhenToReflectTests(HabitCase):
    def test_not_before_a_week_of_diary(self):
        self.days_of_diary(6)
        self.assertFalse(habits.due(self.conn))

    def test_silent_days_do_not_count(self):
        self.days_of_diary(10, diary.SILENT)
        self.assertFalse(habits.due(self.conn))

    def test_after_a_week_of_diary_it_is_time(self):
        self.days_of_diary(7)
        self.assertTrue(habits.due(self.conn))

    def test_once_a_week(self):
        self.days_of_diary(10)
        db.set_state(self.conn, db.LAST_HABITS_AT, db.now_utc())
        self.assertFalse(habits.due(self.conn))

    def test_off_with_the_diary(self):
        config.DIARY_ENABLED = False
        self.days_of_diary(10)
        self.assertFalse(habits.due(self.conn))


class ReflectingTests(HabitCase):
    def test_new_habits_are_learned(self):
        self.days_of_diary(10)
        habits.llm = Pen('{"habits": [{"text": "夜10時ごろに話しかけてくる"}]}')
        self.assertEqual(habits.reflect(self.conn), 1)
        self.assertEqual([r["text"] for r in habits.alive(self.conn)], ["夜10時ごろに話しかけてくる"])

    def test_the_diary_and_known_habits_are_shown(self):
        self.days_of_diary(3, "土曜は出かけた。")
        self.habit("土曜の夜は出かけがち")
        pen = Pen('{"habits": [{"id": 1, "text": "土曜の夜は出かけがち"}]}')
        habits.llm = pen
        habits.reflect(self.conn)
        self.assertIn("土曜は出かけた。", pen.prompts[0])
        self.assertIn("[id:1] 土曜の夜は出かけがち", pen.prompts[0])

    def test_a_habit_named_again_is_reconfirmed_not_duplicated(self):
        self.habit("土曜の夜は出かけがち")
        self.conn.execute("UPDATE habits SET confirmed_at = '2026-01-01T00:00:00Z'")
        self.conn.commit()
        habits.llm = Pen('{"habits": [{"id": 1, "text": "土曜の夜はだいたい出かける"}]}')
        habits.reflect(self.conn)
        rows = habits.alive(self.conn)
        self.assertEqual([(r["id"], r["text"]) for r in rows], [(1, "土曜の夜はだいたい出かける")])
        self.assertGreater(rows[0]["confirmed_at"], "2026-01-01T00:00:00Z")

    def test_what_is_not_named_fades(self):
        self.habit("朝は返事が遅い")
        self.habit("土曜の夜は出かけがち")
        habits.llm = Pen('{"habits": [{"id": 2, "text": "土曜の夜は出かけがち"}]}')
        habits.reflect(self.conn)
        self.assertEqual([r["id"] for r in habits.alive(self.conn)], [2])
        retired = self.conn.execute("SELECT retired_at FROM habits WHERE id = 1").fetchone()
        self.assertIsNotNone(retired["retired_at"])

    def test_an_unknown_id_is_a_new_habit(self):
        habits.llm = Pen('{"habits": [{"id": 99, "text": "夜更かしがち"}]}')
        habits.reflect(self.conn)
        self.assertEqual([r["text"] for r in habits.alive(self.conn)], ["夜更かしがち"])

    def test_no_more_than_the_limit(self):
        many = ", ".join(f'{{"text": "習慣{i}"}}' for i in range(10))
        habits.llm = Pen(f'{{"habits": [{many}]}}')
        self.assertEqual(habits.reflect(self.conn), habits.LIMIT)

    def test_a_bad_answer_is_an_error_but_the_mark_is_set(self):
        """失敗しても毎分やり直さない。次は1週間後。"""
        habits.llm = Pen("うーん")
        with self.assertRaises(ValueError):
            habits.reflect(self.conn)
        self.assertIsNotNone(db.get_state(self.conn, db.LAST_HABITS_AT))


class InTheTalkTests(HabitCase):
    def test_living_habits_are_in_the_prompt(self):
        self.habit("夜10時ごろに話しかけてくる")
        self.habit("朝は返事が遅い", retired=True)
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [])
        self.assertIn("夜10時ごろに話しかけてくる", prompt)
        self.assertNotIn("朝は返事が遅い", prompt)
        self.assertIn("たまに話題にしてよい", prompt)

    def test_nothing_known_means_nothing_said(self):
        self.assertEqual(habits.block(self.conn), "")

    def test_the_fast_path_does_not_carry_them(self):
        self.habit("夜10時ごろに話しかけてくる")
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [], fast=True)
        self.assertNotIn("夜10時ごろ", prompt)


if __name__ == "__main__":
    unittest.main()
