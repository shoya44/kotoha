"""通知と、ことはからの声かけの検証。OneSignalにも Gemini にも接続しない。"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import httpx

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha notify ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import notify  # noqa: E402
from kotoha.memory import db  # noqa: E402
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
        self.addCleanup(setattr, chat, "reach_out", chat.reach_out)
        notify.log = lambda text: None   # 本物のログに書き込まない
        notify.ready = lambda: True
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        chat.reach_out = lambda conn: "そういえばあれ、どうなった？"

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
        chat.reach_out = lambda conn: 1 / 0
        self.quiet_for(10)
        web.maybe_reach_out(self.conn)     # 例外が出ないこと
        self.assertEqual(self.pushed, [])


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
        notify.log = lambda text: None   # 本物のログに書き込まない
        notify.ready = lambda: True
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


if __name__ == "__main__":
    unittest.main()
