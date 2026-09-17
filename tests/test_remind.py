"""頼まれごとの預かりと、見守り。外へは一度も出ない。"""

import unittest
from datetime import datetime, timedelta, timezone

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("remind")

from kotoha import notify  # noqa: E402
from kotoha.memory import db, remind  # noqa: E402
from kotoha.serve import jobs, web  # noqa: E402
from kotoha.talk import chat, presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


class TagTests(unittest.TestCase):
    """文に混ぜたタグを拾い、画面には出さない。"""

    def test_it_takes_the_tag_out_of_the_reply(self):
        clean, found = remind.parse("はいはい。[REMIND: 2026-09-18 09:00|歯医者]")
        self.assertEqual(clean, "はいはい。")
        self.assertEqual(found[0][1], "歯医者")
        self.assertEqual(found[0][0], datetime(2026, 9, 18, 9, 0))

    def test_a_reply_without_one_is_untouched(self):
        clean, found = remind.parse("おはよー。")
        self.assertEqual(clean, "おはよー。")
        self.assertEqual(found, [])

    def test_several_can_be_asked_at_once(self):
        clean, found = remind.parse(
            "わかった。[REMIND: 2026-09-18 09:00|歯医者][REMIND: 2026-09-18 18:00|ゴミ]")
        self.assertEqual(clean, "わかった。")
        self.assertEqual(len(found), 2)

    def test_a_time_it_cannot_read_is_dropped(self):
        """妙な予定を抱え込むより、黙って捨てるほうがまし。"""
        clean, found = remind.parse("うん。[REMIND: あした|歯医者]")
        self.assertEqual(clean, "うん。")
        self.assertEqual(found, [])

    def test_the_tag_never_reaches_the_screen(self):
        """読めない時刻でも、タグそのものは消す。"""
        clean, _ = remind.parse("うん。[REMIND: でたらめ|なにか]")
        self.assertNotIn("REMIND", clean)


class KeepingTests(DbCase):
    def setUp(self):
        super().setUp()
    def test_it_speaks_when_the_time_comes(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        self.assertEqual([r["text"] for r in remind.due(self.conn)], ["歯医者"])

    def test_it_stays_quiet_until_then(self):
        remind.add(self.conn, datetime.now() + timedelta(hours=1), "歯医者")
        self.conn.commit()
        self.assertEqual(remind.due(self.conn), [])

    def test_what_went_by_while_it_slept_is_folded_away(self):
        """PCを閉じていた間に過ぎたぶんを、まとめて言われても困る。"""
        remind.add(self.conn, datetime.now() - timedelta(days=3), "一昨日の用事")
        self.conn.commit()
        self.assertEqual(remind.due(self.conn), [])
        self.assertEqual(remind.pending(self.conn), [])   # 畳んである

    def test_it_only_says_it_once(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        row = remind.due(self.conn)[0]
        remind.done(self.conn, row["id"])
        self.conn.commit()
        self.assertEqual(remind.due(self.conn), [])

    def test_it_can_be_taken_back(self):
        remind.add(self.conn, datetime.now() + timedelta(hours=1), "やっぱりいい用事")
        self.conn.commit()
        self.assertTrue(remind.drop(self.conn, remind.pending(self.conn)[0]["id"]))
        self.assertEqual(remind.pending(self.conn), [])

    def test_it_can_say_what_it_is_holding(self):
        remind.add(self.conn, datetime(2026, 9, 18, 9, 0), "歯医者")
        self.conn.commit()
        self.assertIn("歯医者", remind.block(self.conn))

    def test_holding_nothing_adds_nothing_to_the_prompt(self):
        self.assertEqual(remind.block(self.conn), "")


class SnoozeTests(DbCase):
    """通知の「あとで」。同じ用件を、少し先へ置き直す。"""

    def setUp(self):
        super().setUp()
    def spoken(self, text="歯医者"):
        """一度言い終わった状態（done）にしてから返す。"""
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), text)
        self.conn.commit()
        row = remind.due(self.conn)[0]
        remind.done(self.conn, row["id"])
        self.conn.commit()
        return row["id"]

    def test_it_says_the_same_thing_again_later(self):
        moved, due = remind.snooze(self.conn, [self.spoken()], 30)
        self.assertEqual(moved, 1)
        held = remind.pending(self.conn)
        self.assertEqual([r["text"] for r in held], ["歯医者"])
        self.assertEqual(held[0]["due_at"], due.strftime(remind.STAMP))

    def test_it_moves_everything_that_was_said_together(self):
        ids = [self.spoken("歯医者"), self.spoken("ゴミ出し")]
        moved, _ = remind.snooze(self.conn, ids, 30)
        self.assertEqual(moved, 2)
        self.assertEqual(sorted(r["text"] for r in remind.pending(self.conn)),
                         ["ゴミ出し", "歯医者"])

    def test_a_number_that_is_not_there_moves_nothing(self):
        moved, _ = remind.snooze(self.conn, [999], 30)
        self.assertEqual(moved, 0)
        self.assertEqual(remind.pending(self.conn), [])

    def test_the_old_one_stays_folded(self):
        """置き直しても、元のぶんが二重に鳴ることはない。"""
        remind.snooze(self.conn, [self.spoken()], 30)
        self.assertEqual(len(remind.pending(self.conn)), 1)


class FiringTests(DbCase):
    """時刻が来たら、会話として言い、そのまま通知になる。"""

    def setUp(self):
        super().setUp()
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"),
                            (chat, "speak")):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
        notify.ready = lambda: True
        notify.log = lambda text: None
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        chat.speak = lambda conn, closing, extra="", keep=True: "歯医者の時間だよー"

    def test_it_tells_you_at_the_time(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["歯医者の時間だよー"])

    def test_it_does_not_tell_you_twice(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        jobs.maybe_reminders(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_is_still_owed_when_it_could_not_speak(self):
        """言えなかったものを、済んだことにしない。"""
        def mute(conn, closing, extra="", keep=True):
            raise RuntimeError("だめ")

        chat.speak = mute
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["歯医者の時間だよ"])      # 定型で伝える
        self.assertEqual(remind.pending(self.conn), [])           # そのうえで畳む


class LookoutTests(DbCase):
    """根の詰めすぎと、夜更かし。"""

    def setUp(self):
        super().setUp()
        for name, value in (("LOOKOUT_ENABLED", True), ("LOOKOUT_SIT_HOURS", 3),
                            ("LOOKOUT_LATE_HOUR", 2), ("LOOKOUT_MORNING_HOUR", 5)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"),
                            (chat, "speak"), (presence, "streak"), (jobs, "datetime")):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
        notify.ready = lambda: True
        notify.log = lambda text: None
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        self.told = []
        chat.speak = lambda conn, closing, extra="", keep=True: (
            self.told.append(closing) or "ちょっと休みなよー")
        presence.streak = lambda conn: None

    def at(self, hour):
        jobs.datetime = Clock(datetime.now().replace(hour=hour, minute=30))

    def test_it_speaks_up_after_too_long_at_one_thing(self):
        self.at(15)
        presence.streak = lambda conn: ("ブラウザ", 3.4)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("ブラウザ", self.told[0])

    def test_a_short_stretch_is_left_alone(self):
        self.at(15)
        presence.streak = lambda conn: ("ブラウザ", 2.9)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(self.pushed, [])

    def test_it_does_not_nag_every_minute(self):
        """一度言ったら数え直す。同じことを毎分言わない。"""
        self.at(15)
        counted = {"n": 0}

        def streak(conn):
            return ("ブラウザ", 3.4) if counted["n"] == 0 else ("ブラウザ", 0.1)

        presence.streak = streak
        self.addCleanup(setattr, presence, "reset_streak", presence.reset_streak)
        presence.reset_streak = lambda conn: counted.__setitem__("n", 1)
        jobs.maybe_lookout(self.conn)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_notices_a_late_night(self):
        self.at(3)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("寝る", self.told[0])

    def test_it_says_it_once_a_night(self):
        self.at(3)
        jobs.maybe_lookout(self.conn)
        self.at(4)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_the_evening_is_not_a_late_night(self):
        self.at(23)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(self.pushed, [])

    def test_switched_off_says_nothing(self):
        config.LOOKOUT_ENABLED = False
        self.at(3)
        presence.streak = lambda conn: ("ブラウザ", 5.0)
        jobs.maybe_lookout(self.conn)
        self.assertEqual(self.pushed, [])


class StreakTests(DbCase):
    """同じアプリを続けている時間の数え方。"""

    def setUp(self):
        super().setUp()
    def began(self, when):
        db.set_state(self.conn, db.FRONT_STREAK_FROM, when)

    def test_staying_on_one_app_keeps_the_count_running(self):
        presence._mark_streak(self.conn, "ブラウザ")
        self.began("2026-09-17T00:00:00Z")          # 始まりを昔にずらす
        presence._mark_streak(self.conn, "ブラウザ")
        self.assertEqual(db.get_state(self.conn, db.FRONT_STREAK_FROM),
                         "2026-09-17T00:00:00Z")    # 触られていない

    def test_switching_apps_starts_the_count_over(self):
        presence._mark_streak(self.conn, "ブラウザ")
        self.began("2026-09-17T00:00:00Z")
        presence._mark_streak(self.conn, "エディタ")
        self.assertNotEqual(db.get_state(self.conn, db.FRONT_STREAK_FROM),
                            "2026-09-17T00:00:00Z")

    def test_it_measures_the_hours(self):
        presence._mark_streak(self.conn, "ブラウザ")
        self.began((datetime.now(timezone.utc) - timedelta(hours=4)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"))
        app, hours = presence.streak(self.conn)
        self.assertEqual(app, "ブラウザ")
        self.assertAlmostEqual(hours, 4.0, places=1)

    def test_nothing_measured_yet_is_not_a_streak(self):
        self.assertIsNone(presence.streak(self.conn))


class SnoozeApiTests(unittest.TestCase):
    """通知の「あとで」を押して開いた画面から呼ばれる入口。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        for name, value in (("WEB_TOKEN", "testtoken"), ("SNOOZE_MINUTES", 30)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def kept(self, text="歯医者"):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), text)
        self.conn.commit()
        return remind.pending(self.conn)[-1]["id"]

    def test_it_puts_the_errand_further_off(self):
        response = self.client.post("/api/reminders/snooze",
                                    json={"ids": [self.kept()]}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["moved"], 1)
        self.assertEqual([r["text"] for r in remind.pending(self.conn)], ["歯医者", "歯医者"])

    def test_an_unknown_number_is_a_miss(self):
        response = self.client.post("/api/reminders/snooze",
                                    json={"ids": [999]}, headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_nothing_to_move_is_refused(self):
        response = self.client.post("/api/reminders/snooze", json={"ids": []}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_rubbish_is_not_taken_as_a_number(self):
        response = self.client.post("/api/reminders/snooze",
                                    json={"ids": ["'; DROP TABLE reminders; --"]},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_a_wrong_token_is_refused(self):
        response = self.client.post("/api/reminders/snooze", json={"ids": [self.kept()]},
                                    headers={"X-Kotoha-Token": "wrong"})
        self.assertEqual(response.status_code, 401)


class KeptAnswerTests(DbCase):
    """預かったことを、画面にも渡す。ことはの言葉は変えない。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        super().setUp()
        original = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", original)
        self.addCleanup(setattr, chat.llm, "chat", chat.llm.chat)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def reply_with(self, raw):
        chat.llm.chat = lambda prompt, max_tokens=None: raw

    def send(self, text="明日の朝9時に歯医者"):
        response = self.client.post("/api/chat", json={"text": text}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_what_was_kept_comes_back_with_the_reply(self):
        self.reply_with("仕方ないなー。[REMIND: 2026-09-18 09:00|歯医者]")
        answer = self.send()
        self.assertEqual(answer["reply"], "仕方ないなー。")      # 言葉は変わらない
        self.assertEqual(answer["kept"], [{"due_at": "2026-09-18 09:00", "text": "歯医者"}])

    def test_an_ordinary_reply_has_no_mark(self):
        self.reply_with("ふーん、そうなんだ。")
        self.assertNotIn("kept", self.send("今日は寒いね"))

    def test_a_broken_tag_keeps_nothing(self):
        """読めない時刻は預からない。画面にも出さない。"""
        self.reply_with("うん。[REMIND: あした|歯医者]")
        self.assertNotIn("kept", self.send())
        self.assertEqual(remind.pending(self.conn), [])


class ReminderListApiTests(DbCase):
    """画面から預かりものを見て、取り消す。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        super().setUp()
        original = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", original)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def keep(self, text="歯医者", hours=1):
        remind.add(self.conn, datetime.now() + timedelta(hours=hours), text)
        self.conn.commit()
        return remind.pending(self.conn)[-1]["id"]

    def listed(self):
        response = self.client.get("/api/reminders", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()["reminders"]

    def test_it_shows_what_is_kept(self):
        self.keep("歯医者")
        rows = self.listed()
        self.assertEqual([r["text"] for r in rows], ["歯医者"])
        self.assertIn("due_at", rows[0])
        self.assertIn("id", rows[0])

    def test_the_nearest_comes_first(self):
        self.keep("あと", hours=5)
        self.keep("さき", hours=1)
        self.assertEqual([r["text"] for r in self.listed()], ["さき", "あと"])

    def test_nothing_kept_is_an_empty_list(self):
        self.assertEqual(self.listed(), [])

    def test_what_was_already_said_is_not_listed(self):
        """言い終わったものは、もう予定ではない。"""
        one = self.keep("歯医者", hours=-1)
        remind.done(self.conn, one)
        self.conn.commit()
        self.assertEqual(self.listed(), [])

    def test_it_can_be_taken_back(self):
        one = self.keep("やっぱりいい用事")
        response = self.client.delete(f"/api/reminders/{one}", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.listed(), [])

    def test_taking_back_something_that_is_not_there(self):
        response = self.client.delete("/api/reminders/999", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(
            self.client.get("/api/reminders", headers={"X-Kotoha-Token": "wrong"}).status_code, 401)
        self.assertEqual(
            self.client.delete("/api/reminders/1", headers={"X-Kotoha-Token": "wrong"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
