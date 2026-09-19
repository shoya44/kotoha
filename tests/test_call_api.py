"""通話の受け渡しの検証。実際にマイクは開かない。

通話は**姿のあるところでしか始まらない**。だから持ち主を別に持たず、実体の
居場所がそのまま通話の居場所になる。ここで見るのは、その約束が守られること
——**実体が移れば通話は終わる**——の1点。
"""

import json
import unittest

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("call")

from kotoha.serve import hub, web  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FakeLoop:
    def call_soon_threadsafe(self, call, argument):
        call(argument)


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, payload):
        self.items.append(json.loads(payload))


class CallApiTests(DbCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        hub.reset()
        self.addCleanup(hub.reset)
        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def connect(self, name):
        return hub.join(name, FakeQueue(), FakeLoop())

    def state(self, vessel):
        return self.client.get("/api/call", params={"vessel": vessel},
                               headers=self.headers).json()

    def claim(self, vessel):
        return self.client.post("/api/call", json={"vessel": vessel}, headers=self.headers)

    def release(self):
        return self.client.delete("/api/call", headers=self.headers).json()

    def test_nobody_is_calling_at_first(self):
        self.connect("web-1")
        self.assertEqual(self.state("web-1"), {"calling": False, "mine": False})

    def test_calling_makes_it_mine(self):
        self.connect("web-1")
        self.assertEqual(self.claim("web-1").json()["mine"], True)
        self.assertEqual(self.state("web-1"), {"calling": True, "mine": True})

    def test_a_vessel_that_is_not_connected_cannot_call(self):
        """姿を出していないところで通話は始まらない。"""
        self.assertEqual(self.claim("web-ghost").status_code, 409)

    def test_calling_brings_her_along(self):
        """**通話を始めると、実体ごとこちらへ来る。**"""
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.assertEqual(hub.body(), hub.DESKTOP)
        self.claim("web-1")
        self.assertEqual(hub.body(), "web-1")

    def test_a_later_vessel_takes_over(self):
        self.connect("web-1")
        self.connect("web-2")
        self.claim("web-1")
        self.assertTrue(self.claim("web-2").json()["took_over"])
        self.assertEqual(self.state("web-2")["mine"], True)
        self.assertEqual(self.state("web-1"), {"calling": True, "mine": False})

    def test_calling_twice_from_the_same_vessel_is_not_a_takeover(self):
        self.connect("web-1")
        self.claim("web-1")
        self.assertFalse(self.claim("web-1").json()["took_over"])

    def test_release_ends_it_for_everyone(self):
        self.connect("web-1")
        self.claim("web-1")
        self.assertTrue(self.release()["released"])
        self.assertEqual(self.state("web-1"), {"calling": False, "mine": False})

    def test_release_from_another_vessel_also_works(self):
        """PCを通話中のまま置いてきても、iPhoneから切れる。"""
        self.connect("web-1")
        self.claim("web-1")
        self.client.delete("/api/call", headers=self.headers)
        self.assertEqual(self.state("web-1")["mine"], False)

    def test_moving_her_ends_the_call(self):
        """**実体が移れば通話は終わる。** 向こうで話しているのに、こちらの
        マイクが開いたままなのはおかしい。"""
        self.connect(hub.DESKTOP)
        self.connect("web-1")
        self.claim("web-1")
        hub.claim(hub.DESKTOP, "話しかけられた")
        self.assertEqual(self.state("web-1"), {"calling": False, "mine": False})

    def test_losing_the_vessel_ends_the_call(self):
        """通話していた画面が閉じたら、通話も消える。"""
        self.connect("web-1")
        self.claim("web-1")
        hub.forget("web-1")
        self.assertIsNone(hub.calling())

    def test_a_vessel_without_a_name(self):
        self.assertEqual(self.client.post("/api/call", json={}, headers=self.headers)
                         .status_code, 400)

    def test_the_token_is_checked(self):
        self.assertEqual(self.client.get("/api/call").status_code, 401)
        self.assertEqual(self.client.post("/api/call", json={"vessel": "x"}).status_code, 401)
        self.assertEqual(self.client.delete("/api/call").status_code, 401)


if __name__ == "__main__":
    unittest.main()
