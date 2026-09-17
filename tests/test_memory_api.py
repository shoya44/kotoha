"""画面から記憶を見て直すAPIの検証。一時DBだけを使う。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

_TMP = tempfile.TemporaryDirectory(prefix="kotoha memory api ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db  # noqa: E402
from kotoha.serve import web  # noqa: E402

NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
    "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)"
)
OLD = "2020-01-01T00:00:00Z"
FAR = "2099-01-01T00:00:00Z"


def tearDownModule():
    _TMP.cleanup()


class MemoryApiTests(unittest.TestCase):
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

    def add(self, text="おぼえたこと", layer="semantic", kind="fact",
            pinned=0, expires=FAR, key=None):
        cur = self.conn.execute(
            NODE_SQL,
            (layer, kind, text, "2026-03-01", OLD, OLD, expires, pinned, key or text),
        )
        self.conn.commit()
        return cur.lastrowid

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    # --- 一覧 ---

    def test_lists_each_kind_separately(self):
        self.add("意味のほう", layer="semantic", kind="fact")
        self.add("できごとのほう", layer="episode", kind="event")
        self.add("まもるもの", layer="semantic", kind="fact", pinned=1, key="p")

        semantic = self.get("/api/memories?kind=semantic").json()["memories"]
        episode = self.get("/api/memories?kind=episode").json()["memories"]
        pinned = self.get("/api/memories?kind=pinned").json()["memories"]

        self.assertEqual({m["text"] for m in semantic}, {"意味のほう", "まもるもの"})
        self.assertEqual([m["text"] for m in episode], ["できごとのほう"])
        self.assertEqual([m["text"] for m in pinned], ["まもるもの"])

    def test_unknown_list_is_refused(self):
        self.assertEqual(self.get("/api/memories?kind=everything").status_code, 400)

    def test_list_is_capped(self):
        for index in range(web.MEMORY_LIST_LIMIT + 5):
            self.add(f"記憶{index}", key=f"k{index}")
        got = self.get("/api/memories?kind=semantic").json()["memories"]
        self.assertEqual(len(got), web.MEMORY_LIST_LIMIT)

    # --- 詳細 ---

    def test_detail_brings_tags_and_sources(self):
        node = self.add()
        self.conn.execute("INSERT INTO memory_tags(node_id, tag) VALUES (?,?)", (node, "仕事"))
        db.insert_message(self.conn, 1, "user", "もとの発言")
        self.conn.execute(
            "INSERT INTO memory_sources(node_id, message_id) VALUES (?,?)", (node, 1)
        )
        self.conn.commit()
        body = self.get(f"/api/memories/{node}").json()
        self.assertEqual(body["memory"]["text"], "おぼえたこと")
        self.assertEqual(body["tags"], ["仕事"])
        self.assertEqual(body["sources"], [1])

    def test_detail_of_a_missing_memory(self):
        self.assertEqual(self.get("/api/memories/999").status_code, 404)

    # --- 書き換え ---

    def edit(self, node, text):
        return self.client.put(
            f"/api/memories/{node}", json={"text": text}, headers=self.headers
        )

    def test_edit_changes_the_text(self):
        node = self.add()
        self.assertEqual(self.edit(node, "書き直した").status_code, 200)
        self.assertEqual(self.get(f"/api/memories/{node}").json()["memory"]["text"], "書き直した")

    def test_edit_extends_the_expiry(self):
        """手で直したものは、整理で直したときと同じく期限が延びる。"""
        node = self.add(expires="2026-01-02T00:00:00Z")
        self.edit(node, "書き直した")
        expires = self.get(f"/api/memories/{node}").json()["memory"]["expires_at"]
        self.assertGreater(expires, "2026-09-01")

    def test_edit_resets_the_shortcut(self):
        node = self.add()
        self.conn.execute(
            "INSERT INTO memory_tags(node_id, tag, use_count) VALUES (?,?,?)", (node, "仕事", 3)
        )
        self.conn.commit()
        self.edit(node, "書き直した")
        count = self.conn.execute(
            "SELECT use_count FROM memory_tags WHERE node_id = ?", (node,)
        ).fetchone()["use_count"]
        self.assertEqual(count, 0)

    def test_edit_refuses_empty_text(self):
        node = self.add()
        self.assertEqual(self.edit(node, "  ").status_code, 400)
        self.assertEqual(self.get(f"/api/memories/{node}").json()["memory"]["text"], "おぼえたこと")

    def test_edit_refuses_a_too_long_text(self):
        node = self.add()
        self.assertEqual(self.edit(node, "あ" * (web.MEMORY_TEXT_LIMIT + 1)).status_code, 400)

    def test_edit_of_a_missing_memory(self):
        self.assertEqual(self.edit(999, "なにか").status_code, 404)

    # --- 保護 ---

    def pin(self, node, value):
        return self.client.put(
            f"/api/memories/{node}/pinned", json={"pinned": value}, headers=self.headers
        )

    def test_pin_and_unpin(self):
        node = self.add()
        self.pin(node, True)
        self.assertEqual(self.get(f"/api/memories/{node}").json()["memory"]["pinned"], 1)
        self.pin(node, False)
        self.assertEqual(self.get(f"/api/memories/{node}").json()["memory"]["pinned"], 0)

    def test_pinned_memory_survives_the_forgetting(self):
        node = self.add(expires="2020-01-02T00:00:00Z")
        self.pin(node, True)
        db.run_maintenance(self.conn)
        self.assertEqual(self.get(f"/api/memories/{node}").status_code, 200)

    # --- 削除 ---

    def test_delete_removes_it_with_its_links(self):
        node = self.add()
        self.conn.execute("INSERT INTO memory_tags(node_id, tag) VALUES (?,?)", (node, "仕事"))
        self.conn.commit()
        self.assertEqual(self.client.delete(f"/api/memories/{node}", headers=self.headers).status_code, 200)
        self.assertEqual(self.get(f"/api/memories/{node}").status_code, 404)
        left = self.conn.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE node_id = ?", (node,)
        ).fetchone()[0]
        self.assertEqual(left, 0)

    def test_delete_works_even_when_pinned(self):
        """画面から消すときは、CUI の /forget と同じく保護も無視する。"""
        node = self.add(pinned=1)
        self.client.delete(f"/api/memories/{node}", headers=self.headers)
        self.assertEqual(self.get(f"/api/memories/{node}").status_code, 404)

    def test_delete_of_a_missing_memory(self):
        self.assertEqual(self.client.delete("/api/memories/999", headers=self.headers).status_code, 404)

    # --- 認証 ---

    def test_every_memory_endpoint_needs_a_token(self):
        node = self.add()
        self.assertEqual(self.client.get("/api/memories").status_code, 401)
        self.assertEqual(self.client.get(f"/api/memories/{node}").status_code, 401)
        self.assertEqual(self.client.put(f"/api/memories/{node}", json={"text": "x"}).status_code, 401)
        self.assertEqual(self.client.put(f"/api/memories/{node}/pinned", json={"pinned": True}).status_code, 401)
        self.assertEqual(self.client.delete(f"/api/memories/{node}").status_code, 401)


if __name__ == "__main__":
    unittest.main()
