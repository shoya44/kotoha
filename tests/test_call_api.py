"""通話の受け渡しと再起動の検証。実際にプロセスは落とさない。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config

_TMP = tempfile.TemporaryDirectory(prefix="kotoha call ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db  # noqa: E402
from kotoha.serve import web  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class CallApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()

        original = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", original)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def state(self, device):
        return self.client.get(
            "/api/call", params={"device": device}, headers=self.headers
        ).json()

    def claim(self, device):
        return self.client.post(
            "/api/call", json={"device": device}, headers=self.headers
        ).json()

    def release(self):
        return self.client.delete("/api/call", headers=self.headers).json()

    def test_nobody_is_calling_at_first(self):
        self.assertEqual(self.state("pc"), {"calling": False, "mine": False})

    def test_claiming_makes_it_mine(self):
        self.assertEqual(self.claim("pc")["mine"], True)
        self.assertEqual(self.state("pc"), {"calling": True, "mine": True})

    def test_a_later_device_takes_over(self):
        """あとから通話を始めた端末が持ち主になる。"""
        self.claim("pc")
        took = self.claim("iphone")
        self.assertTrue(took["took_over"])
        self.assertEqual(self.state("iphone")["mine"], True)
        # 前の端末は次の確認で自分のものでないと分かる
        self.assertEqual(self.state("pc"), {"calling": True, "mine": False})

    def test_claiming_twice_from_the_same_device_is_not_a_takeover(self):
        self.claim("pc")
        self.assertFalse(self.claim("pc")["took_over"])

    def test_release_ends_it_for_everyone(self):
        self.claim("pc")
        self.assertTrue(self.release()["released"])
        self.assertEqual(self.state("pc"), {"calling": False, "mine": False})

    def test_release_from_another_device_also_works(self):
        """PCを通話中のまま置いてきても、iPhoneから切れる。"""
        self.claim("pc")
        self.client.delete("/api/call", headers=self.headers)
        self.assertEqual(self.state("pc")["mine"], False)

    def test_a_forgotten_call_expires(self):
        """PCが落ちたまま記録が残っても、時間がたてば通話中ではなくなる。"""
        self.claim("pc")
        conn = db.connect()
        db.set_state(conn, "call_seen_at", "2020-01-01T00:00:00Z")
        conn.commit()
        conn.close()
        self.assertEqual(self.state("pc"), {"calling": False, "mine": False})

    def set_seen(self, seconds_ago):
        """まだ有効な範囲で、見張りの時刻を過去へずらす。"""
        moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = db.connect()
        db.set_state(conn, "call_seen_at", stamp)
        conn.commit()
        conn.close()
        return stamp

    def seen(self):
        conn = db.connect()
        try:
            return db.get_state(conn, "call_seen_at")
        finally:
            conn.close()

    def test_checking_in_keeps_the_call_alive(self):
        self.claim("pc")
        stamp = self.set_seen(60)
        self.state("pc")  # 自分の確認なので見張りが進む
        self.assertNotEqual(self.seen(), stamp)

    def test_another_devices_check_does_not_keep_it_alive(self):
        self.claim("pc")
        stamp = self.set_seen(60)
        self.state("iphone")  # 持ち主でないので触らない
        self.assertEqual(self.seen(), stamp)

    def test_claim_needs_a_device(self):
        response = self.client.post("/api/call", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_all_call_endpoints_need_a_token(self):
        self.assertEqual(self.client.get("/api/call").status_code, 401)
        self.assertEqual(self.client.post("/api/call", json={"device": "x"}).status_code, 401)
        self.assertEqual(self.client.delete("/api/call").status_code, 401)


class RestartApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        original = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", original)

        # 本当に落とさず、呼ばれたことだけ控える
        self.exits = []
        original_timer = web.threading.Timer

        def fake_timer(delay, func):
            self.exits.append(delay)
            return type("Stub", (), {"start": lambda self: None})()

        web.threading.Timer = fake_timer
        self.addCleanup(setattr, web.threading, "Timer", original_timer)
        self.client = TestClient(web.app)

    def test_restart_answers_before_going_down(self):
        response = self.client.post("/api/restart", headers={"X-Kotoha-Token": "testtoken"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"restarting": True})
        self.assertEqual(len(self.exits), 1)  # 応答のあとに落とす予約が入る

    def test_restart_needs_a_token(self):
        self.assertEqual(self.client.post("/api/restart").status_code, 401)
        self.assertEqual(self.exits, [])

    def test_exit_code_matches_the_batch_file(self):
        """start.bat はこの番号を見て起動し直す。"""
        batch = (Path(__file__).resolve().parents[1] / "start.bat").read_text(encoding="utf-8")
        self.assertIn(f"errorlevel {web.RESTART_EXIT_CODE}", batch)


if __name__ == "__main__":
    unittest.main()
