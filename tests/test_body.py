"""届け先の検証。**姿が見えていればふきだし、でなければスマホ。**

どちらか一方しか通らないこと、そして「席に居るか」を測るのがことはの
口を開く瞬間だけであることを見ている。外へは一度も出ない。
"""

import json
import unittest

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("body")

from kotoha import notify  # noqa: E402
from kotoha.serve import announce, hub, web  # noqa: E402
from kotoha.talk import presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FakeLoop:
    def call_soon_threadsafe(self, call, argument):
        call(argument)


class FakeQueue:
    def __init__(self, limit=hub.QUEUE_LIMIT):
        self.items = []
        self.limit = limit

    def full(self):
        return len(self.items) >= self.limit

    def get_nowait(self):
        return self.items.pop(0)

    def put_nowait(self, payload):
        self.items.append(json.loads(payload))


class BodyCase(DbCase):
    def setUp(self):
        super().setUp()
        hub.reset()
        self.addCleanup(hub.reset)
        self.queues = {}
        self.pushed = []
        self.addCleanup(setattr, notify, "push", notify.push)
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        self.addCleanup(setattr, presence, "idle_seconds", presence.idle_seconds)
        self.idle = 10.0
        presence.idle_seconds = lambda: self.idle
        self.addCleanup(setattr, config, "BODY_AWAY_MINUTES", config.BODY_AWAY_MINUTES)
        self.addCleanup(setattr, config, "PUSH_WHEN_EMBODIED", config.PUSH_WHEN_EMBODIED)
        config.BODY_AWAY_MINUTES = 5
        config.PUSH_WHEN_EMBODIED = False

    def connect(self, name):
        queue = FakeQueue()
        self.queues[name] = queue
        return hub.join(name, queue, FakeLoop())

    def heard(self, name):
        return [i["text"] for i in self.queues[name].items if i["type"] == "say"]


class ReachTests(BodyCase):
    def test_nobody_home(self):
        self.assertFalse(announce.reaches_the_person())

    def test_a_screen_is_being_looked_at(self):
        """会話画面に実体があるなら、見られている。席の判定は要らない。"""
        self.connect("web-1")
        self.idle = 9999.0
        self.assertTrue(announce.reaches_the_person())

    def test_the_dot_needs_someone_at_the_desk(self):
        self.connect(hub.DESKTOP)
        self.idle = 60.0
        self.assertTrue(announce.reaches_the_person())
        self.idle = 5 * 60 + 1
        self.assertFalse(announce.reaches_the_person())

    def test_unknown_counts_as_away(self):
        """測れないなら、居ないほうに倒す。黙って見落とすより鳴らす。"""
        self.connect(hub.DESKTOP)
        presence.idle_seconds = lambda: None
        self.assertFalse(announce.reaches_the_person())


class DeliverTests(BodyCase):
    def test_the_screen_hears_it_and_the_phone_stays_quiet(self):
        self.connect("web-1")
        announce._deliver("おかえり", [])
        self.assertEqual(self.heard("web-1"), ["おかえり"])
        self.assertEqual(self.pushed, [])

    def test_the_phone_rings_when_nobody_is_there(self):
        announce._deliver("おかえり", [])
        self.assertEqual(self.pushed, ["おかえり"])

    def test_the_phone_rings_when_the_desk_is_empty(self):
        self.connect(hub.DESKTOP)
        self.idle = 60 * 60
        announce._deliver("おかえり", [])
        self.assertEqual(self.pushed, ["おかえり"])
        self.assertEqual(self.heard(hub.DESKTOP), [])   # 戻っても溜まっていない

    def test_both_when_asked_for(self):
        self.connect("web-1")
        config.PUSH_WHEN_EMBODIED = True
        announce._deliver("おかえり", [])
        self.assertEqual(self.heard("web-1"), ["おかえり"])
        self.assertEqual(self.pushed, ["おかえり"])


class CanSpeakTests(BodyCase):
    def test_a_body_is_enough(self):
        """通知を切っていても、姿が出ているなら話しかけてくる。"""
        self.assertFalse(announce.can_speak())
        self.connect(hub.DESKTOP)
        self.assertTrue(announce.can_speak())


class PresenceApiTests(BodyCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def test_the_token_is_checked(self):
        self.assertEqual(self.client.post("/api/presence/here", json={"vessel": "x"}).status_code, 401)
        self.assertEqual(self.client.get("/api/presence/stream?vessel=x").status_code, 401)

    def test_the_token_may_come_in_the_address(self):
        """EventSource も sendBeacon も、こちらから見出しを足せない。"""
        response = self.client.post("/api/presence/bye?token=testtoken", json={"vessel": "x"})
        self.assertEqual(response.status_code, 204)

    def test_a_stream_needs_a_name(self):
        response = self.client.get("/api/presence/stream", headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_calling_a_vessel_that_is_not_there(self):
        response = self.client.post("/api/presence/here", json={"vessel": "web-ghost"},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 409)

    # ⚠️ 流れっぱなしの道（SSE）は、TestClient では閉じ切れず止まる。ここでは
    # 断り方（401・400）までを見て、**繋いだあとの受け渡しは hub 側で確かめる**
    # （tests/test_hub.py）。実際に流れるかは、画面をつないで確かめる。

    def test_calling_moves_the_body(self):
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        response = self.client.post("/api/presence/here",
                                    json={"vessel": "web-1", "reason": "呼ばれた"},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["body"], "web-1")
        self.assertEqual(hub.body(), "web-1")

    def test_bye_only_means_not_watching(self):
        """裏に回っただけ。**居場所は動かさない**ので、戻ればそこに居る。"""
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.client.post("/api/presence/here", json={"vessel": "web-1"}, headers=self.headers)
        self.client.post("/api/presence/bye", json={"vessel": "web-1"}, headers=self.headers)
        self.assertEqual(hub.body(), "web-1")
        self.assertFalse(hub.watching())

    def test_the_phone_rings_while_she_is_not_watched(self):
        """iPhoneにしまわれているあいだの言葉は、通知で届く。"""
        self.connect("web-1")
        hub.forget("web-1")
        announce._deliver("おかえり", [])
        self.assertEqual(self.pushed, ["おかえり"])


if __name__ == "__main__":
    unittest.main()
