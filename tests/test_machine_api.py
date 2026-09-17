"""画面から見る「PCの様子」の検証。実機のAPIも道具も叩かない。"""

import unittest
from unittest.mock import patch

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("machine api")

from kotoha.memory import db  # noqa: E402
from kotoha.serve import jobs, web  # noqa: E402
from kotoha.talk import presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class MachineApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

        original = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", original)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

        # 実機は見ない。道具の起動確認もしない（本物を叩くと1秒待たされる）。
        self.addCleanup(setattr, presence, "snapshot", presence.snapshot)
        presence.snapshot = lambda conn: [("起動してから", "6時間"), ("いま前面", "VS Code")]
        self.probes = patch.object(
            jobs, "tool_probes",
            return_value={"音声エンジン": lambda: True, "Ollama": lambda: False},
        )
        self.probes.start()
        self.addCleanup(self.probes.stop)

    def rows(self):
        response = self.client.get("/api/machine", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return {r["label"]: r["value"] for r in response.json()["rows"]}

    def test_it_shows_the_machine_and_the_tools(self):
        rows = self.rows()
        self.assertEqual(rows["起動してから"], "6時間")
        self.assertEqual(rows["いま前面"], "VS Code")
        self.assertEqual(rows["音声エンジン"], "動いている")
        self.assertEqual(rows["Ollama"], "止まっている")

    def test_it_says_when_nobody_is_calling(self):
        self.assertEqual(self.rows()["通話"], "していない")

    def test_it_names_the_device_on_a_call(self):
        self.client.post("/api/call", json={"device": "iPhone"}, headers=self.headers)
        self.assertEqual(self.rows()["通話"], "iPhone")

    def test_a_wrong_token_is_refused(self):
        response = self.client.get("/api/machine", headers={"X-Kotoha-Token": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_looking_changes_nothing(self):
        """見るだけの画面。開いても状態は動かない。"""
        before = dict(self.conn.execute("SELECT key, value FROM app_state").fetchall())
        self.rows()
        other = db.connect()
        try:
            after = dict(other.execute("SELECT key, value FROM app_state").fetchall())
        finally:
            other.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
