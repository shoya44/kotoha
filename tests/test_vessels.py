"""器の登録と、観測できた事実だけを会話に渡す境界。"""
import unittest
from unittest.mock import patch

from tests.support import use_temp_db

_TMP = use_temp_db("vessels")
from tests.test_hub import HubCase  # noqa: E402
from kotoha.memory import db, vessels  # noqa: E402
from kotoha.serve import hub, web  # noqa: E402
from kotoha import config  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class VesselsTests(HubCase):
    def test_registration_survives_reconnect(self):
        profile = vessels.save(self.conn, "web-1", {"kind": "iphone", "label": "iPhone"})
        self.conn.commit()
        self.connect("web-1")
        self.assertEqual(hub.snapshot()["profile"], profile)
        hub.forget("web-1")
        self.connect("web-1")
        self.assertEqual(hub.snapshot()["profile"], profile)

    def test_move_preserves_mood_and_topic(self):
        for key in (db.MOOD, db.TRUST, db.TOPIC):
            db.set_state(self.conn, key, "unchanged")
        vessels.save(self.conn, "web-1", {"kind": "iphone", "label": "iPhone"})
        self.conn.commit()
        self.connect("desktop")
        self.connect("web-1")
        hub.claim("web-1", "呼ばれた")
        hub.forget("web-1")
        context = hub.context()
        self.assertIn("iPhone", context)
        self.assertIn("移る前: PC", context)
        self.assertIn("不在の理由は不明", context)
        for key in (db.MOOD, db.TRUST, db.TOPIC):
            self.assertEqual(db.get_state(self.conn, key), "unchanged")

    def test_api_validates_and_requires_token(self):
        from fastapi.testclient import TestClient
        with patch.object(config, "WEB_TOKEN", "testtoken"):
            client = TestClient(web.app)
            self.assertEqual(client.post("/api/vessel", json={}).status_code, 401)
            headers = {"X-Kotoha-Token": "testtoken"}
            self.assertEqual(client.post("/api/vessel", headers=headers,
                                        json={"vessel": "../bad", "kind": "pc", "label": "PC"}).status_code, 400)
            response = client.post("/api/vessel", headers=headers,
                                   json={"vessel": "web-1", "kind": "tablet", "label": "居間"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/vessel?vessel=web-1", headers=headers).json()["label"], "居間")


if __name__ == "__main__":
    unittest.main()
