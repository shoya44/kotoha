"""ことはの日記。夜に1日を1件にまとめ、朝と会話で見返す。外へは出ない。"""

import unittest
from datetime import date, datetime, timedelta, timezone

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("diary")

from kotoha import notify  # noqa: E402
from kotoha.memory import db, diary, remind  # noqa: E402
from kotoha.serve import jobs, web  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def _say(conn, turn_id, role, text, at: datetime):
    """その時刻（ローカル）に話したことにする。messages はUTCで持つ。"""
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT INTO messages(turn_id, role, text, created_at) VALUES (?,?,?,?)",
                 (turn_id, role, text, stamp))


class Pen:
    """llm の代役。何を渡されたかを覚えておく。"""

    def __init__(self, text="今日は昼まで寝てた。"):
        self.text = text
        self.prompts = []

    def chat(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.text


class WritingTests(DbCase):
    def setUp(self):
        super().setUp()
        self.pen = Pen()
        self.addCleanup(setattr, diary, "llm", diary.llm)
        diary.llm = self.pen
        self.day = date(2026, 9, 21)

    def test_a_day_with_talk_becomes_one_entry(self):
        _say(self.conn, 1, "user", "起きた", datetime(2026, 9, 21, 12, 3))
        _say(self.conn, 1, "assistant", "もう昼だよ", datetime(2026, 9, 21, 12, 4))
        self.conn.commit()
        self.assertEqual(diary.write(self.conn, self.day), "今日は昼まで寝てた。")
        self.assertEqual(diary.entry(self.conn, self.day)["text"], "今日は昼まで寝てた。")
        self.assertIn("12:03 相手: 起きた", self.pen.prompts[0])
        self.assertIn("2026-09-21（月曜日）", self.pen.prompts[0])

    def test_the_day_is_cut_at_local_midnight(self):
        """23:50 の会話はその日。翌 0:10 は翌日。UTCで切ると両方ずれる。"""
        _say(self.conn, 1, "user", "まだ起きてる", datetime(2026, 9, 21, 23, 50))
        _say(self.conn, 2, "user", "日付変わった", datetime(2026, 9, 22, 0, 10))
        self.conn.commit()
        stuff = diary.material(self.conn, self.day)
        self.assertIn("まだ起きてる", stuff)
        self.assertNotIn("日付変わった", stuff)

    def test_what_was_said_as_a_reminder_is_material_too(self):
        remind.add(self.conn, datetime(2026, 9, 21, 21, 0), "薬を飲んだか聞く", "毎日")
        self.conn.execute("UPDATE reminders SET done_at = ? WHERE id = 1",
                         (datetime(2026, 9, 21, 21, 0).astimezone(timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),))
        self.conn.commit()
        self.assertIn("21:00 薬を飲んだか聞く", diary.material(self.conn, self.day))

    def test_a_silent_day_costs_nothing(self):
        self.assertEqual(diary.write(self.conn, self.day), diary.SILENT)
        self.assertEqual(self.pen.prompts, [])
        self.assertIsNotNone(diary.entry(self.conn, self.day))

    def test_one_entry_per_day(self):
        diary.write(self.conn, self.day)
        self.assertEqual(diary.write(self.conn, self.day), "")
        self.assertEqual(len(diary.recent(self.conn)), 1)

    def test_an_empty_answer_is_an_error_not_an_entry(self):
        _say(self.conn, 1, "user", "やあ", datetime(2026, 9, 21, 9, 0))
        self.conn.commit()
        self.pen.text = "  "
        with self.assertRaises(RuntimeError):
            diary.write(self.conn, self.day)
        self.assertIsNone(diary.entry(self.conn, self.day))

    def test_long_days_are_clipped(self):
        for i in range(40):
            _say(self.conn, i, "user", "あ" * 300, datetime(2026, 9, 21, 9, i))
        self.conn.commit()
        stuff = diary.material(self.conn, self.day)
        self.assertLess(len(stuff), diary.MATERIAL_CHARS + 200)
        self.assertIn("省略", stuff)


class NightlyJobTests(DbCase):
    """日付が変わったら前の日を1件。寝ていた日は1回の巡回に1日ずつ。"""

    def setUp(self):
        super().setUp()
        for name, value in (("DIARY_ENABLED", True), ("DIARY_HOUR", 0)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, notify, "log", notify.log)
        self.logged = []
        notify.log = lambda text: self.logged.append(text)
        self.pen = Pen()
        self.addCleanup(setattr, diary, "llm", diary.llm)
        diary.llm = self.pen
        self.addCleanup(setattr, jobs, "datetime", jobs.datetime)

    def at(self, moment):
        class Clock:
            @staticmethod
            def now():
                return moment
        jobs.datetime = Clock

    def test_yesterday_is_written_after_midnight(self):
        _say(self.conn, 1, "user", "おやすみ", datetime(2026, 9, 21, 23, 0))
        self.conn.commit()
        self.at(datetime(2026, 9, 22, 0, 5))
        jobs.maybe_diary(self.conn)
        self.assertEqual(diary.entry(self.conn, date(2026, 9, 21))["text"], "今日は昼まで寝てた。")

    def test_it_looks_once_a_day(self):
        self.at(datetime(2026, 9, 22, 0, 5))
        jobs.maybe_diary(self.conn)
        jobs.maybe_diary(self.conn)
        self.assertEqual(len(diary.recent(self.conn)), 1)

    def test_it_waits_for_the_hour(self):
        config.DIARY_HOUR = 3
        self.at(datetime(2026, 9, 22, 1, 0))
        jobs.maybe_diary(self.conn)
        self.assertEqual(diary.recent(self.conn), [])

    def test_days_asleep_are_caught_up_one_per_round(self):
        """9/18 まで書いてあって 3日寝ていたら、9/19 から古い順に1日ずつ。"""
        self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                          ("2026-09-18", "x", db.now_utc()))
        self.conn.commit()
        self.at(datetime(2026, 9, 22, 9, 0))
        jobs.maybe_diary(self.conn)
        self.assertEqual([r["day"] for r in diary.recent(self.conn)], ["2026-09-19", "2026-09-18"])
        jobs.maybe_diary(self.conn)
        jobs.maybe_diary(self.conn)
        self.assertEqual(diary.recent(self.conn)[0]["day"], "2026-09-21")

    def test_the_first_day_ever_is_just_yesterday(self):
        """まっさらなDBで、先週までさかのぼって「話していない」を並べない。"""
        self.at(datetime(2026, 9, 22, 9, 0))
        jobs.maybe_diary(self.conn)
        self.assertEqual([r["day"] for r in diary.recent(self.conn)], ["2026-09-21"])

    def test_a_failure_is_logged_and_not_retried_right_away(self):
        _say(self.conn, 1, "user", "やあ", datetime(2026, 9, 21, 9, 0))
        self.conn.commit()
        self.pen.text = ""
        self.at(datetime(2026, 9, 22, 0, 5))
        with Clock(datetime(2026, 9, 22, 0, 5)):
            jobs.maybe_diary(self.conn)
            jobs.maybe_diary(self.conn)
        self.assertEqual(len(self.pen.prompts), 1)
        self.assertTrue(any("日記が書けなかった" in line for line in self.logged))

    def test_a_failure_is_retried_after_an_hour(self):
        """503が一度来ただけで、その日の日記も夜の整理も丸一日欠けていた。"""
        _say(self.conn, 1, "user", "やあ", datetime(2026, 9, 21, 9, 0))
        self.conn.commit()
        self.pen.text = ""
        self.at(datetime(2026, 9, 22, 0, 5))
        with Clock(datetime(2026, 9, 22, 0, 5)) as clock:
            jobs.maybe_diary(self.conn)
            clock.advance(minutes=30)
            jobs.maybe_diary(self.conn)                 # まだ間を空けている
            self.assertEqual(len(self.pen.prompts), 1)
            clock.advance(minutes=31)
            self.pen.text = "書けた。"
            jobs.maybe_diary(self.conn)
        self.assertEqual(len(self.pen.prompts), 2)
        self.assertEqual(diary.entry(self.conn, date(2026, 9, 21))["text"], "書けた。")

    def test_it_gives_up_for_the_day_after_a_few_tries(self):
        """際限なく試して枠を食い潰さない。翌日にはさかのぼって拾う。"""
        _say(self.conn, 1, "user", "やあ", datetime(2026, 9, 21, 9, 0))
        self.conn.commit()
        self.pen.text = ""
        self.at(datetime(2026, 9, 22, 0, 5))
        with Clock(datetime(2026, 9, 22, 0, 5)) as clock:
            for _ in range(jobs.DIARY_TRIES + 2):
                jobs.maybe_diary(self.conn)
                clock.advance(hours=1, minutes=1)
        self.assertEqual(len(self.pen.prompts), jobs.DIARY_TRIES)
        self.assertTrue(any("今日は諦める" in line for line in self.logged))
        self.assertEqual(db.get_state(self.conn, db.LAST_DIARY_ON), "2026-09-22")

    def test_off_means_off(self):
        config.DIARY_ENABLED = False
        self.at(datetime(2026, 9, 22, 0, 5))
        jobs.maybe_diary(self.conn)
        self.assertEqual(diary.recent(self.conn), [])


class LookingBackTests(DbCase):
    """会話にはここ数日ぶん、朝には昨日のぶん。"""

    def _entry(self, day, text):
        self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                          (day, text, db.now_utc()))
        self.conn.commit()

    def test_the_talk_sees_the_last_few_days_oldest_first(self):
        for day in ("2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21"):
            self._entry(day, f"{day[-2:]}日のこと")
        block = diary.block(self.conn, datetime(2026, 9, 22, 10, 0))
        self.assertNotIn("18日", block)
        self.assertLess(block.index("19日"), block.index("21日"))
        self.assertIn("同じことを毎日は言わない", block)

    def test_today_is_not_in_the_diary_yet(self):
        self._entry("2026-09-22", "今日のぶん")
        self.assertEqual(diary.block(self.conn, datetime(2026, 9, 22, 10, 0)), "")

    def test_the_morning_looks_at_yesterday(self):
        self._entry("2026-09-21", "昼まで寝てた。")
        self.assertIn("昼まで寝てた。", diary.morning_block(self.conn, datetime(2026, 9, 22, 7, 0)))

    def test_a_silent_yesterday_is_not_brought_up(self):
        self._entry("2026-09-21", diary.SILENT)
        self.assertEqual(diary.morning_block(self.conn, datetime(2026, 9, 22, 7, 0)), "")

    def test_the_prompt_carries_the_diary(self):
        self._entry((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"), "昨日は夜更かし。")
        prompt = chat.build_prompt(self.conn, "おはよ", [], [], [])
        self.assertIn("昨日は夜更かし。", prompt)

    def test_the_fast_path_does_not_carry_it(self):
        self._entry((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"), "昨日は夜更かし。")
        prompt = chat.build_prompt(self.conn, "おはよ", [], [], [], fast=True)
        self.assertNotIn("昨日は夜更かし。", prompt)


class DiaryApiTests(DbCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        super().setUp()
        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)

    def test_newest_first(self):
        for day in ("2026-09-20", "2026-09-21"):
            self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                              (day, day, db.now_utc()))
        self.conn.commit()
        response = self.client.get("/api/diary", headers={"X-Kotoha-Token": "testtoken"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["day"] for r in response.json()["diary"]],
                         ["2026-09-21", "2026-09-20"])

    def test_needs_the_token(self):
        response = self.client.get("/api/diary", headers={"X-Kotoha-Token": "wrong"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class HonestDiaryTests(DbCase):
    """日記に、自分がしたことを書かせない。

    「朝ごはん食べた？」と聞いただけの日に「朝ごはんを作ってあげた」と書いた
    ことがある。ルール文は Gemini にしか読めないので、消えていないかを見張る。
    """

    def test_the_rule_is_in_the_prompt(self):
        pen = Pen()
        self.addCleanup(setattr, diary, "llm", diary.llm)
        diary.llm = pen
        _say(self.conn, 1, "assistant", "朝ごはん食べたのー？", datetime(2026, 9, 21, 9, 0))
        self.conn.commit()
        diary.write(self.conn, date(2026, 9, 21))
        self.assertIn("自分がしたこと", pen.prompts[0])
        self.assertIn("PCの中", pen.prompts[0])
