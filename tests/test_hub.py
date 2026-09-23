"""実体の受け渡しの検証。器は偽物で、SSEもHTTPも通さない。

見ているのは1つだけ。**姿を出す場所が、いつでも1つに決まっていること。**
"""

import asyncio
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
    def __init__(self, limit=hub.QUEUE_LIMIT):
        self.items = []
        self.limit = limit

    def full(self):
        return len(self.items) >= self.limit

    def get_nowait(self):
        # 本物の asyncio.Queue と同じ形で空を言う。行列の外側（満ち・空）だけ
        # 似せて中身を別形にすると、本物でしか通らない道が試験から逃げる。
        if not self.items:
            raise asyncio.QueueEmpty
        return self.items.pop(0)

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
    def test_she_stays_where_she_was(self):
        """**裏に回っても居場所は動かない。** 戻ってくれば、そこに居る。"""
        self.connect(hub.DESKTOP)
        web = self.connect("web-1")
        hub.claim("web-1")
        hub.leave(web)
        self.assertEqual(hub.body(), "web-1")
        self.assertFalse(hub.watching())

    def test_coming_back_finds_her_there(self):
        self.connect("web-1")
        hub.forget("web-1")
        self.connect("web-1")
        self.assertEqual(hub.body(), "web-1")
        self.assertTrue(hub.watching())
        self.assertEqual(self.last("web-1"), {"type": "here"})

    def test_another_screen_does_not_inherit_her(self):
        """開いたままの画面があっても、そちらへは移らない。"""
        self.connect("web-1")
        self.connect("web-2")
        hub.forget("web-1")
        self.assertEqual(hub.body(), "web-1")
        self.assertNotIn({"type": "here"}, self.events("web-2"))

    def test_talking_brings_her_over(self):
        """動かすのは呼ばれたときだけ。"""
        self.connect("web-1")
        hub.forget("web-1")
        self.connect(hub.DESKTOP)
        hub.claim(hub.DESKTOP, "話しかけられた")
        self.assertEqual(hub.body(), hub.DESKTOP)

    def test_bye_does_not_wait_for_the_connection_to_drop(self):
        """iPhoneのPWAは背景でも繋がりが残る。待つとPushが鳴らなくなる。"""
        self.connect("web-1")
        hub.forget("web-1")
        self.assertFalse(hub.watching())
        self.assertFalse(hub.anyone())

    def test_a_stale_connection_is_not_mistaken_for_the_new_one(self):
        """bye のあとに切断が来ても、繋ぎ直したぶんを巻き添えにしない。"""
        old = self.connect("web-1")
        hub.forget("web-1")
        self.connect("web-1")
        hub.leave(old)
        self.assertTrue(hub.watching())


class SpeakingTests(HubCase):
    def test_only_the_body_hears(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.assertTrue(hub.say("おかえり"))
        self.assertEqual(self.last(hub.DESKTOP), {"type": "say", "text": "おかえり"})
        self.assertEqual(self.last("web-1"), {"type": "away", "where": "desktop"})

    def test_nothing_to_say_to_a_screen_that_is_not_there(self):
        """居場所はあっても、見ていなければ届かない（そのときは通知になる）。"""
        self.connect("web-1")
        hub.forget("web-1")
        self.assertFalse(hub.say("おかえり"))

    def test_nothing_to_say_to_nobody(self):
        self.assertFalse(hub.say("おかえり"))
        self.assertFalse(hub.show("laptop", "idle"))

    def test_showing_a_picture(self):
        self.connect(hub.DESKTOP)
        self.assertTrue(hub.show("laptop", "idle"))
        self.assertEqual(self.last(hub.DESKTOP),
                         {"type": "act", "picture": "laptop", "act": "idle", "fidgets_off": []})

    def test_the_face_of_the_reply_is_shown_while_talking(self):
        """返事の [FACE:] は、話した直後の絵になる。巡回（said_ago なし）では元に戻る。"""
        from kotoha.talk import chat
        self.connect(hub.DESKTOP)
        turn_id = db.start_turn(self.conn, "user", "ねえ聞いて")
        chat._finish(self.conn, turn_id, "あはは", [], "slow", face="笑う")
        hub.refresh(self.conn, said_ago=0)
        self.assertEqual(self.last(hub.DESKTOP)["picture"], "laugh")
        hub.refresh(self.conn)
        self.assertNotEqual(self.last(hub.DESKTOP)["act"], "talk")
        self.assertIn("fidgets_off", self.last(hub.DESKTOP))


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

    def test_the_place_stays_written_when_she_is_not_watched(self):
        vessel = self.connect(hub.DESKTOP)
        hub.leave(vessel)
        with db.session() as conn:
            self.assertEqual(db.get_state(conn, db.BODY_WHERE), hub.DESKTOP)

    def test_snapshot_shows_the_room(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        found = hub.snapshot()
        self.assertEqual(found["body"], hub.DESKTOP)
        self.assertEqual(found["kind"], "desktop")
        self.assertEqual(found["vessels"], [hub.DESKTOP, "web-1"])
        self.assertTrue(found["since"])




class QueueLimitTests(HubCase):
    """半分死んだ器が繋がったままでも、行列は伸びつづけない。"""

    def test_the_oldest_events_are_dropped(self):
        queue = FakeQueue(limit=3)
        vessel = hub.join("web", queue, self.loop)
        for i in range(6):
            vessel.send({"type": "say", "text": str(i)})
        self.assertEqual(len(queue.items), 3)
        self.assertEqual([e["text"] for e in queue.items], ["3", "4", "5"])

    def test_the_guard_path_has_the_names_it_needs(self):
        """通れない道でも、名前が無ければ死なないことを確かめておく。

        本番ではこの護りは発火しない（輪は1本の糸なので、「満ちている」と
        言われたときには必ず取り出せる）。それでも見に行くのは、NameErrorが
        裏の巡回を一度静かに止めた形そのものだから。空のとき本物の行列が
        投げるのと同じ例外を偽の行列にも投げさせ、護りの道を通す。
        """
        queue = FakeQueue(limit=0)      # 中身が無くても「満ちている」と言う
        vessel = hub.join("web", queue, self.loop)
        vessel.send({"type": "say", "text": "1"})
        self.assertEqual([e["text"] for e in queue.items], ["1"])


if __name__ == "__main__":
    unittest.main()
