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

    def test_a_repeat_rides_in_the_third_slot(self):
        clean, found = remind.parse("わかった。[REMIND: 2026-09-18 22:00|薬を飲んだか聞く|毎日]")
        self.assertEqual(clean, "わかった。")
        self.assertEqual(found[0][1:], ("薬を飲んだか聞く", "毎日"))

    def test_no_repeat_means_once(self):
        _, found = remind.parse("うん。[REMIND: 2026-09-18 09:00|歯医者]")
        self.assertIsNone(found[0][2])

    def test_a_repeat_it_does_not_know_means_once(self):
        """知らない言葉で妙な繰り返しを抱え込まない。用件は預かる。"""
        _, found = remind.parse("うん。[REMIND: 2026-09-18 09:00|歯医者|隔週]")
        self.assertEqual(found[0][1:], ("歯医者", None))


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


class RepeatTests(DbCase):
    """毎日・平日。言い終わるたびに、次の日のぶんを入れ直す。"""

    def test_every_day_comes_back_tomorrow(self):
        due = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
        remind.add(self.conn, due, "薬", "毎日")
        self.conn.commit()
        remind.done(self.conn, remind.pending(self.conn)[0]["id"])
        self.conn.commit()
        held = remind.pending(self.conn)
        self.assertEqual([(r["due_at"], r["repeat"]) for r in held],
                         [((due + timedelta(days=1)).strftime(remind.STAMP), "毎日")])

    def test_weekdays_skip_the_weekend(self):
        """金曜の次は月曜。祝日も飛ばす（2026-09-21〜23 は敬老の日・休日・秋分の日）。"""
        self.assertEqual(remind.next_due(datetime(2026, 9, 18, 7, 30), "平日",
                                         now=datetime(2026, 9, 18, 8, 0)),
                         datetime(2026, 9, 24, 7, 30))

    def test_next_time_is_never_in_the_past(self):
        """3日寝ていても、次は明日。1日ずつ畳み直さない。"""
        now = datetime(2026, 9, 21, 23, 0)
        self.assertEqual(remind.next_due(datetime(2026, 9, 18, 22, 0), "毎日", now),
                         datetime(2026, 9, 22, 22, 0))

    def test_once_stays_once(self):
        remind.add(self.conn, datetime(2026, 9, 18, 9, 0), "歯医者")
        self.conn.commit()
        remind.done(self.conn, remind.pending(self.conn)[0]["id"])
        self.conn.commit()
        self.assertEqual(remind.pending(self.conn), [])

    def test_folding_what_went_by_still_keeps_tomorrow(self):
        """寝ていて過ぎた日も、明日はまた来る。"""
        remind.add(self.conn, datetime.now() - timedelta(days=3), "薬", "毎日")
        self.conn.commit()
        remind.due(self.conn)                      # ここで畳まれる
        held = remind.pending(self.conn)
        self.assertEqual(len(held), 1)
        self.assertGreater(held[0]["due_at"], datetime.now().strftime(remind.STAMP))

    def test_taking_it_back_ends_the_repeat(self):
        remind.add(self.conn, datetime(2026, 9, 18, 22, 0), "薬", "毎日")
        self.conn.commit()
        remind.drop(self.conn, remind.pending(self.conn)[0]["id"])
        self.assertEqual(remind.pending(self.conn), [])


class FlexibleRepeatTests(DbCase):
    """週末だけ、月水金、休日。言葉は揃えて持ち、次に当たる日を探す。"""

    def test_words_are_normalized(self):
        cases = {"週末": "土日", "月水金": "月水金", "金水月": "月水金", "毎週月曜・水曜": "月水",
                 "日曜日": "日", "月火水木金土日": "毎日", "土日祝": "休日", "仕事の日": "平日",
                 "平日": "平日", "なんとなく": None, "": None}
        for word, want in cases.items():
            with self.subTest(word=word):
                self.assertEqual(remind.normalize_repeat(word), want)

    def test_a_weekday_set_rides_in_the_tag(self):
        _, found = remind.parse("[REMIND: 2026-09-22 21:00|ゴミ出し|月水金]")
        self.assertEqual(found[0][2], "月水金")

    def test_mon_wed_fri_goes_to_the_next_of_those(self):
        """9/22（火）の次は 9/23（水）。9/23 の次は 9/25（金）、その次は 9/28（月）。"""
        now = datetime(2026, 9, 22, 21, 30)
        self.assertEqual(remind.next_due(datetime(2026, 9, 22, 21, 0), "月水金", now),
                         datetime(2026, 9, 23, 21, 0))
        self.assertEqual(remind.next_due(datetime(2026, 9, 25, 21, 0), "月水金",
                                         datetime(2026, 9, 25, 22, 0)),
                         datetime(2026, 9, 28, 21, 0))

    def test_weekend_goes_to_saturday(self):
        self.assertEqual(remind.next_due(datetime(2026, 9, 22, 9, 0), "週末",
                                         datetime(2026, 9, 22, 10, 0)),
                         datetime(2026, 9, 26, 9, 0))

    def test_days_off_include_holidays(self):
        """9/23 は秋分の日。休日は祝日にも当たる。"""
        self.assertEqual(remind.next_due(datetime(2026, 9, 22, 9, 0), "休日",
                                         datetime(2026, 9, 22, 10, 0)),
                         datetime(2026, 9, 23, 9, 0))

    def test_the_set_is_kept_after_it_fires(self):
        remind.add(self.conn, datetime(2026, 9, 22, 21, 0), "ゴミ出し", "月水金")
        self.conn.commit()
        remind.done(self.conn, remind.pending(self.conn)[0]["id"])
        self.assertEqual(remind.pending(self.conn)[0]["repeat"], "月水金")


class DropTagTests(DbCase):
    """会話の中でことはが判断して取り消す。本人に頼まれたぶんだけ。"""

    def test_parse_pulls_ids(self):
        clean, ids = remind.parse_drop("わかった、もう言わないね。[DROP: #3, 5]")
        self.assertEqual(clean, "わかった、もう言わないね。")
        self.assertEqual(ids, [3, 5])

    def test_unreadable_ids_are_dropped_quietly(self):
        self.assertEqual(remind.parse_drop("[DROP: x]")[1], [])

    def test_block_shows_the_number_to_point_at(self):
        remind.add(self.conn, datetime(2026, 9, 22, 21, 0), "薬", "毎日")
        self.conn.commit()
        self.assertIn("#1 2026-09-22 21:00 薬（毎日）", remind.block(self.conn))

    def test_only_what_the_owner_asked_can_be_dropped(self):
        remind.add(self.conn, datetime(2026, 9, 22, 21, 0), "薬", "毎日")
        remind.add(self.conn, datetime(2026, 9, 22, 21, 30), "薬", chain=1)
        self.conn.commit()
        gone = remind.drop_many(self.conn, [1, 2, 99])
        self.assertEqual(gone, [(1, "薬", "毎日")])
        self.assertEqual([r["chain"] for r in remind.pending(self.conn)], [1])


class FollowUpTests(DbCase):
    """自分で入れる「もう一度」。返事が来たら終わり。上限で手を引く。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, notify, "log", notify.log)
        notify.log = lambda text: None

    def test_she_can_put_in_another_one(self):
        self.assertTrue(remind.follow_up(self.conn, 0, datetime(2026, 9, 18, 7, 5), "起こす"))
        row = remind.pending(self.conn)[0]
        self.assertEqual((row["text"], row["chain"]), ("起こす", 1))

    def test_an_answer_ends_the_chase_but_not_the_errand(self):
        remind.add(self.conn, datetime(2026, 9, 18, 9, 0), "歯医者")
        remind.follow_up(self.conn, 0, datetime(2026, 9, 18, 7, 5), "起こす")
        self.conn.commit()
        self.assertEqual(remind.answered(self.conn), 1)
        self.assertEqual([r["text"] for r in remind.pending(self.conn)], ["歯医者"])

    def test_it_stops_at_the_limit(self):
        self.assertFalse(remind.follow_up(self.conn, remind.CHAIN_LIMIT,
                                          datetime(2026, 9, 18, 7, 5), "起こす"))
        self.assertEqual(remind.pending(self.conn), [])

    def test_later_counts_as_an_answer(self):
        """通知の「あとで」を押したなら、聞こえている。"""
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        first = remind.due(self.conn)[0]["id"]
        remind.done(self.conn, first)
        remind.follow_up(self.conn, 0, datetime.now() + timedelta(minutes=5), "歯医者、まだ？")
        self.conn.commit()
        remind.snooze(self.conn, [first], 30)
        self.assertEqual([r["chain"] for r in remind.pending(self.conn)], [0])

    def test_the_morning_does_not_list_her_own_chases(self):
        due = datetime.now().replace(hour=23, minute=59)
        remind.follow_up(self.conn, 0, due, "起こす")
        self.conn.commit()
        self.assertEqual(remind.today(self.conn), [])


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
        chat.speak = lambda conn, closing, extra="", keep=True, chain=None: "歯医者の時間だよー"

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

    def test_a_chase_is_said_as_a_chase(self):
        heard = []
        chat.speak = lambda conn, closing, extra="", keep=True, chain=None: (
            heard.append((closing, chain)) or "ねえ、起きてる？")
        remind.follow_up(self.conn, 1, datetime.now() - timedelta(minutes=1), "起こす")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["ねえ、起きてる？"])
        self.assertIn("2回目", heard[0][0])
        self.assertEqual(heard[0][1], 2)
        self.assertEqual(remind.pending(self.conn), [])

    def test_a_chase_it_could_not_say_is_folded_quietly(self):
        """追いかけを定型で言っても仕方がない。毎分やり直す道でもない。"""
        def mute(conn, closing, extra="", keep=True, chain=None):
            raise RuntimeError("だめ")

        chat.speak = mute
        remind.follow_up(self.conn, 0, datetime.now() - timedelta(minutes=1), "起こす")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, [])
        self.assertEqual(remind.pending(self.conn), [])

    def test_an_errand_does_not_wait_for_the_gap(self):
        """「7時に起こして」を7時10分に言っても仕方がない。"""
        self.addCleanup(setattr, config, "NOTIFY_GAP_MINUTES", config.NOTIFY_GAP_MINUTES)
        config.NOTIFY_GAP_MINUTES = 10
        db.set_state(self.conn, db.LAST_NOTIFY_AT, db.now_utc())
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "起こす")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, ["歯医者の時間だよー"])

    def test_it_is_still_owed_when_it_could_not_speak(self):
        """言えなかったものを、済んだことにしない。"""
        def mute(conn, closing, extra="", keep=True, chain=None):
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
        chat.speak = lambda conn, closing, extra="", keep=True, chain=None: (
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
        self.assertEqual(answer["kept"], [{"due_at": "2026-09-18 09:00", "text": "歯医者",
                                           "repeat": None}])

    def test_a_repeat_comes_back_with_the_mark(self):
        """毎日か一度きりかは、印で見分けられないと確かめようがない。"""
        self.reply_with("いいよ。[REMIND: 2026-09-18 22:00|薬を飲んだか聞く|毎日]")
        answer = self.send("毎晩22時に薬飲んだか聞いて")
        self.assertEqual(answer["kept"][0]["repeat"], "毎日")

    def test_a_drop_from_the_talk_comes_back_with_the_mark(self):
        remind.add(self.conn, datetime(2026, 9, 22, 21, 0), "薬を飲んだか聞く", "毎日")
        self.conn.commit()
        self.reply_with("そっか、じゃあもう聞かないね。[DROP: 1]")
        answer = self.send("薬のはもういいよ")
        self.assertEqual(answer["reply"], "そっか、じゃあもう聞かないね。")
        self.assertEqual(answer["dropped"], [{"id": 1, "text": "薬を飲んだか聞く", "repeat": "毎日"}])
        self.assertEqual(remind.pending(self.conn), [])

    def test_an_ordinary_reply_has_no_mark(self):
        self.reply_with("ふーん、そうなんだ。")
        self.assertNotIn("kept", self.send("今日は寒いね"))

    def test_a_broken_tag_keeps_nothing(self):
        """読めない時刻は預からない。画面にも出さない。"""
        self.reply_with("うん。[REMIND: あした|歯医者]")
        self.assertNotIn("kept", self.send())
        self.assertEqual(remind.pending(self.conn), [])


class OwnFollowUpTests(DbCase):
    """頼まれごとを言う回だけ、自分で「もう一度」を入れてよい。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, chat.llm, "chat", chat.llm.chat)
        self.addCleanup(setattr, notify, "log", notify.log)
        notify.log = lambda text: None
        chat.llm.chat = lambda prompt, max_tokens=None: (
            "起きてー。[REMIND: 2026-09-18 07:05|起こす、2回目]")

    def test_while_reminding_she_may_chase(self):
        said = chat.speak(self.conn, "起こす時刻になった。", chain=0)
        self.assertEqual(said, "起きてー。")
        row = remind.pending(self.conn)[0]
        self.assertEqual((row["due_at"], row["chain"]), ("2026-09-18 07:05", 1))

    def test_the_chase_wording_is_in_the_prompt(self):
        seen = []
        chat.llm.chat = lambda prompt, max_tokens=None: seen.append(prompt) or "起きてー。"
        chat.speak(self.conn, "起こす時刻になった。", chain=0)
        self.assertIn("もう一度言う時刻", seen[0])
        chat.speak(self.conn, "ひまだから声をかける。")
        self.assertNotIn("もう一度言う時刻", seen[1])

    def test_otherwise_she_makes_no_plans_for_herself(self):
        chat.speak(self.conn, "ひまだから声をかける。")
        self.assertEqual(remind.pending(self.conn), [])

    def test_a_reply_from_the_person_ends_the_chase(self):
        remind.follow_up(self.conn, 0, datetime(2026, 9, 18, 7, 5), "起こす")
        self.conn.commit()
        chat.llm.chat = lambda prompt, max_tokens=None: "おはよ。"
        chat.run_turn(self.conn, "起きたよ")
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


class MorningTests(DbCase):
    """朝いちばんに、その日ぶんの頼まれごとを予告すること。"""

    def add(self, when: str, text: str):
        remind.add(self.conn, datetime.strptime(when, remind.STAMP), text)
        self.conn.commit()

    def test_todays_errands_come_back_in_order(self):
        now = datetime(2026, 9, 18, 7, 0)
        self.add("2026-09-18 18:00", "夕方の薬")
        self.add("2026-09-18 09:00", "歯医者")
        rows = remind.today(self.conn, now)
        self.assertEqual([r["text"] for r in rows], ["歯医者", "夕方の薬"])

    def test_other_days_are_left_out(self):
        now = datetime(2026, 9, 18, 7, 0)
        self.add("2026-09-19 09:00", "あしたの用")
        self.add("2026-09-17 09:00", "きのうの用")
        self.assertEqual(remind.today(self.conn, now), [])

    def test_what_has_already_passed_is_not_announced_again(self):
        """朝7時に、6時半のぶんを今から予告しても仕方がない。"""
        now = datetime(2026, 9, 18, 7, 0)
        self.add("2026-09-18 06:30", "もう過ぎた用")
        self.assertEqual(remind.today(self.conn, now), [])

    def test_what_was_already_said_is_left_out(self):
        now = datetime(2026, 9, 18, 7, 0)
        self.add("2026-09-18 09:00", "歯医者")
        remind.done(self.conn, remind.today(self.conn, now)[0]["id"])
        self.conn.commit()
        self.assertEqual(remind.today(self.conn, now), [])

    def test_the_time_is_handed_over_with_the_errand(self):
        now = datetime(2026, 9, 18, 7, 0)
        self.add("2026-09-18 09:00", "歯医者")
        said = remind.morning_block(remind.today(self.conn, now))
        self.assertIn("09:00", said)
        self.assertIn("歯医者", said)

    def test_nothing_to_say_when_there_is_nothing(self):
        self.assertEqual(remind.morning_block([]), "")
