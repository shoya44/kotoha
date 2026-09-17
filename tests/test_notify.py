"""通知と、ことはからの声かけの検証。OneSignalにも Gemini にも接続しない。"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha notify ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import notify  # noqa: E402
from kotoha.memory import db, remind  # noqa: E402
from kotoha.serve import web  # noqa: E402
from kotoha.talk import chat, presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class PushTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("PUSH_ENABLED", True), ("PUSH_SHOW_TEXT", True),
                            ("ONESIGNAL_APP_ID", "app-1"), ("ONESIGNAL_API_KEY", "key-1"),
                            ("PUSH_OPEN_URL", "")):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, notify, "log", notify.log)
        self.logged = []
        notify.log = self.logged.append
        self.sent = []

    def use(self, status=200, error=None):
        def post(url, **kwargs):
            self.sent.append(kwargs.get("json"))
            if error:
                raise error
            return httpx.Response(status, text="{}")

        client = notify._http()
        self.addCleanup(setattr, client, "post", client.post)
        client.post = post

    def test_it_sends_the_text(self):
        self.use()
        self.assertTrue(notify.push("ことは", "おかえり"))
        self.assertEqual(self.sent[0]["contents"]["en"], "おかえり")
        self.assertEqual(self.sent[0]["app_id"], "app-1")

    def test_the_text_can_be_held_back(self):
        """会話の中身を外のサーバーに渡したくないときのため。"""
        config.PUSH_SHOW_TEXT = False
        self.use()
        notify.push("ことは", "眠れないの？")
        self.assertNotIn("眠れない", self.sent[0]["contents"]["en"])

    def test_nothing_is_sent_without_the_settings(self):
        config.ONESIGNAL_API_KEY = ""
        self.use()
        self.assertFalse(notify.push("ことは", "おかえり"))
        self.assertEqual(self.sent, [])

    def test_switched_off_sends_nothing(self):
        config.PUSH_ENABLED = False
        self.use()
        self.assertFalse(notify.push("ことは", "おかえり"))
        self.assertEqual(self.sent, [])

    def test_a_refusal_is_written_down(self):
        self.use(status=400)
        self.assertFalse(notify.push("ことは", "おかえり"))
        self.assertTrue(self.logged)

    def test_a_network_failure_does_not_escape(self):
        self.use(error=httpx.ConnectError("圏外"))
        self.assertFalse(notify.push("ことは", "おかえり"))

    def test_the_key_is_not_written_down(self):
        """記録に鍵が混ざると、ログを人に見せられなくなる。"""
        self.use(status=401)
        notify.push("ことは", "おかえり")
        self.assertNotIn("key-1", " ".join(self.logged))


class ReachOutTests(unittest.TestCase):
    """暇なときの声かけ。3つとも満たしたときだけ。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        for name, value in (("REACH_OUT_ENABLED", True), ("REACH_OUT_AFTER_HOURS", 5),
                            ("REACH_OUT_INTERVAL_HOURS", 6),
                            ("REACH_OUT_FROM_HOUR", 0), ("REACH_OUT_TO_HOUR", 24)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, notify, "ready", notify.ready)
        self.addCleanup(setattr, notify, "push", notify.push)
        self.addCleanup(setattr, notify, "log", notify.log)
        self.addCleanup(setattr, chat, "speak", chat.speak)
        notify.log = lambda text: None   # 本物のログに書き込まない
        notify.ready = lambda: True
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        # 本物のGeminiを叩かせない。
        chat.speak = lambda conn, closing, extra="", keep=True: "そういえばあれ、どうなった？"

    def quiet_for(self, hours):
        long_ago = f"{datetime.now().year - 1}-01-01T00:00:00Z"
        db.set_state(self.conn, "last_conversation_at", long_ago if hours else db.now_utc())
        self.conn.commit()

    def test_it_speaks_up_after_a_long_quiet(self):
        self.quiet_for(10)
        web.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, ["そういえばあれ、どうなった？"])

    def test_it_stays_quiet_right_after_talking(self):
        self.quiet_for(0)
        web.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, [])

    def test_it_does_not_speak_up_twice_in_a_row(self):
        self.quiet_for(10)
        web.maybe_reach_out(self.conn)
        web.maybe_reach_out(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_keeps_out_of_the_night(self):
        self.quiet_for(10)
        config.REACH_OUT_FROM_HOUR = (datetime.now().hour + 2) % 24
        config.REACH_OUT_TO_HOUR = (datetime.now().hour + 3) % 24
        web.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, [])

    def test_switched_off_says_nothing(self):
        config.REACH_OUT_ENABLED = False
        self.quiet_for(10)
        web.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, [])

    def test_a_failure_to_write_does_not_escape(self):
        chat.speak = self.explode
        self.quiet_for(10)
        web.maybe_reach_out(self.conn)     # 例外が出ないこと
        self.assertEqual(self.pushed, [])


    @staticmethod
    def explode(conn, closing, extra="", keep=True):
        raise ZeroDivisionError("わざと")


class WatchTests(unittest.TestCase):
    """落ちた瞬間だけ知らせる。動いているあいだは黙っている。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()
        self.addCleanup(setattr, notify, "ready", notify.ready)
        self.addCleanup(setattr, notify, "push", notify.push)
        self.addCleanup(setattr, notify, "log", notify.log)
        self.addCleanup(setattr, presence, "disks", presence.disks)
        self.addCleanup(setattr, chat, "speak", chat.speak)
        self.addCleanup(setattr, chat, "remember", chat.remember)
        notify.log = lambda text: None   # 本物のログに書き込まない
        notify.ready = lambda: True
        # 見張りも announce を通る。本物のGeminiは叩かせない。
        chat.speak = lambda conn, closing, extra="", keep=True: ""
        chat.remember = lambda conn, text, ids=(), mood="", keep=True: None
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        presence.disks = lambda: []
        self.up = True
        self.addCleanup(setattr, web, "_tool_probes", web._tool_probes)
        web._tool_probes = lambda: {"音声エンジン": lambda: self.up}

    def test_it_says_nothing_while_things_are_running(self):
        web.run_watch_jobs()
        web.run_watch_jobs()
        self.assertEqual(self.pushed, [])

    def test_it_speaks_when_something_falls_over(self):
        web.run_watch_jobs()          # 動いている状態を覚える
        self.up = False
        web.run_watch_jobs()
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("音声エンジン", self.pushed[0])

    def test_it_does_not_repeat_itself(self):
        web.run_watch_jobs()
        self.up = False
        web.run_watch_jobs()
        web.run_watch_jobs()
        self.assertEqual(len(self.pushed), 1)

    def test_the_first_look_is_not_an_alarm(self):
        """起動直後から止まっていただけで騒がない。"""
        self.up = False
        web.run_watch_jobs()
        self.assertEqual(self.pushed, [])

    def test_a_shrinking_disk_is_mentioned_once(self):
        self.addCleanup(setattr, config, "DISK_WARN_GB", config.DISK_WARN_GB)
        config.DISK_WARN_GB = 20
        presence.disks = lambda: [("C", 100.0, 50)]
        web.run_watch_jobs()
        presence.disks = lambda: [("C", 5.0, 99)]
        web.run_watch_jobs()
        web.run_watch_jobs()
        self.assertEqual(len(self.pushed), 1)
        self.assertIn("Cドライブ", self.pushed[0])


class TogetherTests(unittest.TestCase):
    """立て続けに鳴らさない。同じ巡回で出たものは、1通にまとめて言う。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        self.addCleanup(setattr, config, "NOTIFY_GAP_MINUTES", config.NOTIFY_GAP_MINUTES)
        config.NOTIFY_GAP_MINUTES = 10
        for name in ("ready", "push", "log"):
            self.addCleanup(setattr, notify, name, getattr(notify, name))
        self.addCleanup(setattr, chat, "speak", chat.speak)
        self.addCleanup(setattr, chat, "remember", chat.remember)
        self.addCleanup(setattr, web, "_collecting", False)
        notify.log = lambda text: None      # 本物のログに書き込まない
        notify.ready = lambda: True
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        # 本物のGeminiは叩かせない。渡された指示をそのまま返して中身を見る。
        self.asked = []
        chat.speak = lambda conn, closing, extra="", keep=True: (
            self.asked.append((closing, extra, keep)) or "うん、わかった")
        chat.remember = lambda conn, text, ids=(), mood="", keep=True: None

    def pass_of(self, *closings):
        """1回の巡回のまね。announce はこの間、鳴らさずに預かる。"""
        web._collecting = True
        try:
            for closing in closings:
                web.announce(self.conn, closing)
        finally:
            web._collecting = False
        return web.flush_held(self.conn)

    def test_two_things_in_one_round_become_one_message(self):
        self.pass_of("空きが減ったと伝える。", "歯医者の時間だと伝える。")
        self.assertEqual(len(self.pushed), 1)
        closing = self.asked[0][0]
        self.assertIn("空きが減った", closing)
        self.assertIn("歯医者", closing)

    def test_one_thing_is_asked_for_as_it_is(self):
        """1つだけのときは、まとめる指示で言葉を濁さない。"""
        self.pass_of("歯医者の時間だと伝える。")
        self.assertEqual(self.asked[0][0], "歯医者の時間だと伝える。")
        self.assertEqual(self.pushed, ["うん、わかった"])

    def test_the_next_one_waits_for_the_gap(self):
        self.pass_of("ひとつめ。")
        self.assertEqual(len(self.pushed), 1)
        self.pass_of("ふたつめ。")
        self.assertEqual(len(self.pushed), 1)    # まだ間が空いていない
        self.assertEqual(len(web._held(self.conn)), 1)

    def test_it_speaks_again_once_the_gap_has_passed(self):
        self.pass_of("ひとつめ。")
        db.set_state(self.conn, "last_notify_at", f"{datetime.now().year - 1}-01-01T00:00:00Z")
        self.conn.commit()
        self.pass_of("ふたつめ。")
        self.assertEqual(len(self.pushed), 2)

    def test_what_was_held_survives_a_restart(self):
        """預かりはDBに置く。落ちても、頼まれごとが消えない。"""
        self.pass_of("ひとつめ。")
        self.pass_of("ふたつめ。")
        other = db.connect()
        try:
            self.assertEqual(len(web._held(other)), 1)
        finally:
            other.close()

    def test_switching_the_gap_off_speaks_at_once(self):
        config.NOTIFY_GAP_MINUTES = 0
        web.announce(self.conn, "ひとつめ。")
        web.announce(self.conn, "ふたつめ。")
        self.assertEqual(len(self.pushed), 2)

    def test_it_does_not_hold_more_than_it_can_say(self):
        self.pass_of("さいしょ。")
        for i in range(web.HELD_LIMIT + 3):
            web.announce(self.conn, f"{i}番目。")
        self.assertEqual(len(web._held(self.conn)), web.HELD_LIMIT)

    def test_a_broken_note_is_not_carried_around(self):
        db.set_state(self.conn, db.HELD_ANNOUNCEMENTS, "こわれている")
        self.conn.commit()
        self.assertEqual(web._held(self.conn), [])

    def test_a_held_errand_is_not_asked_for_twice(self):
        """預かられても「言えた」扱いにする。でないと毎分積み増してしまう。"""
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        self.pass_of("さいしょ。")              # ここで間を埋める
        web._collecting = True
        try:
            web.maybe_reminders(self.conn)
            web.maybe_reminders(self.conn)
        finally:
            web._collecting = False
        self.assertEqual(len(web._held(self.conn)), 1)
        self.assertEqual(remind.pending(self.conn), [])

    def test_the_round_sends_what_was_held(self):
        """巡回の配線。預かったものが、次の巡回で出ていく。"""
        for name, value in (("PRESENCE_ENABLED", False), ("BRIEFING_ENABLED", False),
                            ("LOOKOUT_ENABLED", False), ("REACH_OUT_ENABLED", False),
                            ("BACKUP_INTERVAL_SECONDS", 10 ** 9),
                            ("MAINTENANCE_SECONDS", 10 ** 9)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        web._collecting = True
        try:
            web.announce(self.conn, "預かったこと。")
        finally:
            web._collecting = False
        web.run_periodic_jobs(self.conn)
        self.assertEqual(self.pushed, ["うん、わかった"])
        self.assertEqual(web._held(self.conn), [])


class SnoozeButtonTests(unittest.TestCase):
    """頼まれごとの通知にだけ「あとで」を付ける。用件はURLに載せない。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)
        for name, value in (("SNOOZE_MINUTES", 30),
                            ("PUSH_OPEN_URL", "https://pc.example.ts.net"),
                            ("NOTIFY_GAP_MINUTES", 0)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        for name in ("ready", "push", "log"):
            self.addCleanup(setattr, notify, name, getattr(notify, name))
        self.addCleanup(setattr, chat, "speak", chat.speak)
        notify.log = lambda text: None
        notify.ready = lambda: True
        self.sent = []
        notify.push = lambda title, body, *a, **k: self.sent.append(
            (body, k.get("buttons"), k.get("url"))) or True
        chat.speak = lambda conn, closing, extra="", keep=True: "歯医者の時間だよー"

    def fire(self, text="歯医者"):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), text)
        self.conn.commit()
        web.maybe_reminders(self.conn)

    def test_an_errand_gets_a_later_button(self):
        self.fire()
        buttons = self.sent[0][1]
        self.assertEqual(len(buttons), 1)
        self.assertIn("30分後", buttons[0]["text"])

    def test_the_link_carries_only_the_number(self):
        """通知はOneSignalを通る。用件そのものは渡さない。"""
        self.fire("心療内科の予約")
        url = self.sent[0][1][0]["url"]
        self.assertNotIn("心療内科", url)
        self.assertIn("snooze=", url)

    def test_other_notices_have_no_button(self):
        web.announce(self.conn, "空きが減ったと伝える。")
        self.assertIsNone(self.sent[0][1])

    def test_switching_it_off_removes_the_button(self):
        config.SNOOZE_MINUTES = 0
        self.fire()
        self.assertIsNone(self.sent[0][1])

    def test_without_a_url_there_is_nowhere_to_press(self):
        config.PUSH_OPEN_URL = ""
        self.fire()
        self.assertIsNone(self.sent[0][1])

    def test_the_notice_itself_opens_where_it_can_be_moved(self):
        """iPhoneは通知にボタンを出せない。開いた画面で押せるよう、番号を渡す。"""
        self.fire()
        self.assertRegex(self.sent[0][2], r"remind=\d+$")

    def test_other_notices_open_the_usual_place(self):
        web.announce(self.conn, "空きが減ったと伝える。")
        self.assertEqual(self.sent[0][2], "")

    def test_errands_said_together_are_moved_together(self):
        """まとめて言ったぶんは、まとめて置き直せるようにする。"""
        config.NOTIFY_GAP_MINUTES = 10
        for text in ("歯医者", "ゴミ出し"):
            remind.add(self.conn, datetime.now() - timedelta(minutes=1), text)
        self.conn.commit()
        web._collecting = True
        try:
            web.maybe_reminders(self.conn)
        finally:
            web._collecting = False
        web.flush_held(self.conn)
        url = self.sent[0][1][0]["url"]
        self.assertRegex(url, r"snooze=\d+,\d+")


if __name__ == "__main__":
    unittest.main()
