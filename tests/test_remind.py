"""頼まれごとの預かりと、見守り。外へは一度も出ない。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config

_TMP = tempfile.TemporaryDirectory(prefix="kotoha remind ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import notify  # noqa: E402
from kotoha.memory import db, remind  # noqa: E402
from kotoha.serve import web  # noqa: E402
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


class KeepingTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

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


class FiringTests(unittest.TestCase):
    """時刻が来たら、会話として言い、そのまま通知になる。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
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
        web.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["歯医者の時間だよー"])

    def test_it_does_not_tell_you_twice(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        web.maybe_reminders(self.conn)
        web.maybe_reminders(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_is_still_owed_when_it_could_not_speak(self):
        """言えなかったものを、済んだことにしない。"""
        def mute(conn, closing, extra="", keep=True):
            raise RuntimeError("だめ")

        chat.speak = mute
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        web.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["歯医者の時間だよ"])      # 定型で伝える
        self.assertEqual(remind.pending(self.conn), [])           # そのうえで畳む


class LookoutTests(unittest.TestCase):
    """根の詰めすぎと、夜更かし。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        for name, value in (("LOOKOUT_ENABLED", True), ("LOOKOUT_SIT_HOURS", 3),
                            ("LOOKOUT_LATE_HOUR", 2), ("LOOKOUT_MORNING_HOUR", 5)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"),
                            (chat, "speak"), (presence, "streak"), (web, "datetime")):
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
        web.datetime = Clock(datetime.now().replace(hour=hour, minute=30))

    def test_it_speaks_up_after_too_long_at_one_thing(self):
        self.at(15)
        presence.streak = lambda conn: ("ブラウザ", 3.4)
        web.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("ブラウザ", self.told[0])

    def test_a_short_stretch_is_left_alone(self):
        self.at(15)
        presence.streak = lambda conn: ("ブラウザ", 2.9)
        web.maybe_lookout(self.conn)
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
        web.maybe_lookout(self.conn)
        web.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_notices_a_late_night(self):
        self.at(3)
        web.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("寝る", self.told[0])

    def test_it_says_it_once_a_night(self):
        self.at(3)
        web.maybe_lookout(self.conn)
        self.at(4)
        web.maybe_lookout(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_the_evening_is_not_a_late_night(self):
        self.at(23)
        web.maybe_lookout(self.conn)
        self.assertEqual(self.pushed, [])

    def test_switched_off_says_nothing(self):
        config.LOOKOUT_ENABLED = False
        self.at(3)
        presence.streak = lambda conn: ("ブラウザ", 5.0)
        web.maybe_lookout(self.conn)
        self.assertEqual(self.pushed, [])


class StreakTests(unittest.TestCase):
    """同じアプリを続けている時間の数え方。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def began(self, when):
        db.set_state(self.conn, presence.STREAK_FROM_KEY, when)

    def test_staying_on_one_app_keeps_the_count_running(self):
        presence._mark_streak(self.conn, "ブラウザ")
        self.began("2026-09-17T00:00:00Z")          # 始まりを昔にずらす
        presence._mark_streak(self.conn, "ブラウザ")
        self.assertEqual(db.get_state(self.conn, presence.STREAK_FROM_KEY),
                         "2026-09-17T00:00:00Z")    # 触られていない

    def test_switching_apps_starts_the_count_over(self):
        presence._mark_streak(self.conn, "ブラウザ")
        self.began("2026-09-17T00:00:00Z")
        presence._mark_streak(self.conn, "エディタ")
        self.assertNotEqual(db.get_state(self.conn, presence.STREAK_FROM_KEY),
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


if __name__ == "__main__":
    unittest.main()
