"""実体の受け渡しの検証。器は偽物で、SSEもHTTPも通さない。

見ているのは1つだけ。**姿を出す場所が、いつでも1つに決まっていること。**
"""

import json
import unittest

from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("hub")

from kotoha.memory import db  # noqa: E402
from kotoha.serve import hub  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FakeLoop:
    """uvicornの輪の代わり。頼まれたその場で入れる。"""

    def call_soon_threadsafe(self, call, argument):
        call(argument)


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, payload):
        self.items.append(json.loads(payload))


class HubCase(DbCase):
    def setUp(self):
        super().setUp()
        hub.reset()
        self.addCleanup(hub.reset)
        self.loop = FakeLoop()
        self.queues = {}

    def connect(self, name):
        queue = FakeQueue()
        self.queues[name] = queue
        return hub.join(name, queue, self.loop)

    def events(self, name):
        return self.queues[name].items

    def last(self, name):
        items = self.events(name)
        return items[-1] if items else None


class ArrivingTests(HubCase):
    def test_the_first_vessel_becomes_the_body(self):
        self.connect(hub.DESKTOP)
        self.assertEqual(hub.body(), hub.DESKTOP)
        self.assertEqual(self.last(hub.DESKTOP), {"type": "here"})

    def test_opening_a_screen_does_not_move_her(self):
        """開いただけでは移らない。会話画面は「外出中」から始まる。"""
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.assertEqual(hub.body(), hub.DESKTOP)
        self.assertEqual(self.last("web-1"), {"type": "away", "where": "desktop"})
        self.assertEqual(self.last(hub.DESKTOP), {"type": "here"})

    def test_a_screen_alone_gets_the_body(self):
        """ドットが動いていなければ、開いた画面に実体化する。"""
        self.connect("web-1")
        self.assertEqual(hub.body(), "web-1")


class CallingTests(HubCase):
    def test_calling_moves_her_and_tells_everyone(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.assertTrue(hub.claim("web-1", "話しかけられた"))
        self.assertEqual(hub.body(), "web-1")
        self.assertEqual(self.last("web-1"), {"type": "here"})
        self.assertEqual(self.last(hub.DESKTOP), {"type": "away", "where": "web"})

    def test_a_vessel_that_is_not_connected_cannot_hold_her(self):
        self.connect(hub.DESKTOP)
        self.assertFalse(hub.claim("web-ghost"))
        self.assertEqual(hub.body(), hub.DESKTOP)

    def test_calling_the_body_again_changes_nothing(self):
        self.connect(hub.DESKTOP)
        before = len(self.events(hub.DESKTOP))
        self.assertTrue(hub.claim(hub.DESKTOP))
        self.assertEqual(len(self.events(hub.DESKTOP)), before)


class LeavingTests(HubCase):
    def test_she_goes_home_when_the_screen_closes(self):
        desktop = self.connect(hub.DESKTOP)
        web = self.connect("web-1")
        hub.claim("web-1")
        hub.leave(web)
        self.assertEqual(hub.body(), hub.DESKTOP)
        self.assertEqual(self.last(hub.DESKTOP), {"type": "here"})
        self.assertIsNotNone(desktop)

    def test_she_moves_to_whoever_is_left(self):
        self.connect("web-1")
        self.connect("web-2")
        hub.forget("web-1")
        self.assertEqual(hub.body(), "web-2")

    def test_nobody_left_means_no_body(self):
        vessel = self.connect(hub.DESKTOP)
        hub.leave(vessel)
        self.assertIsNone(hub.body())
        self.assertIsNone(hub.body_kind())
        self.assertFalse(hub.anyone())

    def test_bye_does_not_wait_for_the_connection_to_drop(self):
        """iPhoneのPWAは背景でも繋がりが残る。待つとPushが鳴らなくなる。"""
        self.connect("web-1")
        hub.forget("web-1")
        self.assertIsNone(hub.body())
        self.assertFalse(hub.anyone())

    def test_a_stale_connection_does_not_take_her_away(self):
        """bye のあとに切断が来ても、そのとき居る器を巻き添えにしない。"""
        old = self.connect("web-1")
        hub.forget("web-1")
        self.connect(hub.DESKTOP)
        hub.leave(old)
        self.assertEqual(hub.body(), hub.DESKTOP)


class SpeakingTests(HubCase):
    def test_only_the_body_hears(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.assertTrue(hub.say("おかえり"))
        self.assertEqual(self.last(hub.DESKTOP), {"type": "say", "text": "おかえり"})
        self.assertEqual(self.last("web-1"), {"type": "away", "where": "desktop"})

    def test_nothing_to_say_to_nobody(self):
        self.assertFalse(hub.say("おかえり"))
        self.assertFalse(hub.show("laptop", "idle"))

    def test_showing_a_picture(self):
        self.connect(hub.DESKTOP)
        self.assertTrue(hub.show("laptop", "idle"))
        self.assertEqual(self.last(hub.DESKTOP),
                         {"type": "act", "picture": "laptop", "act": "idle"})


class RememberingTests(HubCase):
    def test_the_place_is_written_down(self):
        """ことは自身に居場所を言わせるための写し。移ったときだけ書く。"""
        self.connect(hub.DESKTOP)
        with db.session() as conn:
            self.assertEqual(db.get_state(conn, db.BODY_WHERE), hub.DESKTOP)
        self.connect("web-1")
        hub.claim("web-1")
        with db.session() as conn:
            self.assertEqual(db.get_state(conn, db.BODY_WHERE), "web-1")

    def test_an_empty_place_when_nobody_is_left(self):
        vessel = self.connect(hub.DESKTOP)
        hub.leave(vessel)
        with db.session() as conn:
            self.assertEqual(db.get_state(conn, db.BODY_WHERE), "")

    def test_snapshot_shows_the_room(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        found = hub.snapshot()
        self.assertEqual(found["body"], hub.DESKTOP)
        self.assertEqual(found["kind"], "desktop")
        self.assertEqual(found["vessels"], [hub.DESKTOP, "web-1"])
        self.assertTrue(found["since"])


if __name__ == "__main__":
    unittest.main()
