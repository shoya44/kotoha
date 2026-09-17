"""忘却と記憶の寿命の検証。一時DBだけを使い、本番DBには触れない。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha forget ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import consolidate, db, retrieve  # noqa: E402

NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
    "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)"
)
OLD = "2020-01-01T00:00:00Z"
EXPIRED = "2020-01-02T00:00:00Z"
FAR = "2099-01-01T00:00:00Z"


def tearDownModule():
    _TMP.cleanup()


class MemoryLifetimeTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add(self, layer="semantic", kind="fact", expires=FAR, pinned=0, key="k"):
        cur = self.conn.execute(
            NODE_SQL, (layer, kind, "記憶", "2020-01-01", OLD, OLD, expires, pinned, key)
        )
        self.conn.commit()
        return cur.lastrowid

    def expires_of(self, node_id):
        return self.conn.execute(
            "SELECT expires_at FROM memory_nodes WHERE id = ?", (node_id,)
        ).fetchone()["expires_at"]

    def alive(self):
        return {r["id"] for r in self.conn.execute("SELECT id FROM memory_nodes")}

    # --- 忘却 ---

    def test_expired_unpinned_is_deleted(self):
        target = self.add(expires=EXPIRED, key="expired")
        self.assertEqual(db.run_maintenance(self.conn), 1)
        self.assertNotIn(target, self.alive())

    def test_pinned_and_valid_memories_survive(self):
        pinned = self.add(expires=EXPIRED, pinned=1, key="pinned")
        valid = self.add(expires=FAR, key="valid")
        endless = self.add(expires=None, key="endless")
        db.run_maintenance(self.conn)
        self.assertEqual(self.alive(), {pinned, valid, endless})

    def test_maintenance_resets_stale_tag_counts(self):
        node = self.add(key="tagged")
        self.conn.execute(
            "INSERT INTO memory_tags(node_id, tag, use_count, last_used_at) VALUES (?,?,?,?)",
            (node, "仕事", 3, OLD),
        )
        self.conn.commit()
        db.run_maintenance(self.conn)
        count = self.conn.execute(
            "SELECT use_count FROM memory_tags WHERE node_id = ?", (node,)
        ).fetchone()["use_count"]
        self.assertEqual(count, 0)

    def just_expired(self):
        return self.conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 second') AS t"
        ).fetchone()["t"]

    def test_memory_expired_earlier_today_is_deleted(self):
        """保存形式と比較形式のずれで同日中の期限切れを取りこぼす退行を防ぐ。"""
        target = self.add(expires=self.just_expired(), key="just")
        self.assertEqual(db.run_maintenance(self.conn), 1)
        self.assertNotIn(target, self.alive())

    def test_memory_expired_earlier_today_is_not_retrieved(self):
        self.add(expires=self.just_expired(), pinned=1, key="just")
        self.assertEqual(retrieve.pinned_only(self.conn), [])

    # --- 実行タイミング（未記録でも取りこぼさない） ---

    def test_seconds_since_treats_missing_record_as_long_ago(self):
        self.assertEqual(db.seconds_since(None), float("inf"))
        self.assertEqual(db.seconds_since(""), float("inf"))
        self.assertEqual(db.seconds_since("壊れた日付"), float("inf"))
        self.assertGreater(db.seconds_since(OLD), 0)

    def test_maintenance_is_due_on_a_fresh_database(self):
        """last_forget_at 未記録のまま忘却が一度も走らない退行を防ぐ。"""
        last = db.get_state(self.conn, "last_forget_at")
        self.assertIsNone(last)
        self.assertGreater(db.seconds_since(last), config.MAINTENANCE_SECONDS)

    def test_maintenance_records_its_own_timestamp(self):
        db.run_maintenance(self.conn)
        last = db.get_state(self.conn, "last_forget_at")
        self.assertIsNotNone(last)
        self.assertLess(db.seconds_since(last), config.MAINTENANCE_SECONDS)

    # --- 延命（想起された記憶は生き延びる） ---

    def assert_extended(self, node_id, days):
        expires = self.expires_of(node_id)
        expected = self.conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) AS t", (f"+{days} days",)
        ).fetchone()["t"]
        self.assertEqual(expires[:13], expected[:13])  # 秒単位のずれは許容

    def test_usage_extends_expiry_per_layer(self):
        episode = self.add(layer="episode", kind="event", expires=EXPIRED, key="ep")
        semantic = self.add(expires=EXPIRED, key="se")
        db.update_usage(self.conn, [episode, semantic], turn_id=1)
        self.conn.commit()
        self.assert_extended(episode, config.EPISODE_DAYS)
        self.assert_extended(semantic, config.SEMANTIC_DAYS)

    def test_usage_never_shortens_or_revives_expiry(self):
        far = self.add(expires=FAR, key="far")
        endless = self.add(expires=None, key="endless")
        db.update_usage(self.conn, [far, endless], turn_id=1)
        self.conn.commit()
        self.assertEqual(self.expires_of(far), FAR)
        self.assertIsNone(self.expires_of(endless))

    def test_reconfirm_extends_expiry(self):
        node = self.add(expires=EXPIRED, key="reconfirm")
        consolidate._validate_and_save(self.conn, {"reconfirm_ids": [node]}, [])
        self.conn.commit()
        self.assert_extended(node, config.SEMANTIC_DAYS)

    def test_update_extends_expiry(self):
        node = self.add(expires=EXPIRED, key="update")
        db.insert_message(self.conn, 1, "user", "訂正します")
        self.conn.commit()
        messages = self.conn.execute("SELECT id, created_at FROM messages").fetchall()
        consolidate._validate_and_save(
            self.conn,
            {"updates": [{"id": node, "text": "新しい内容", "source_message_ids": [messages[0]["id"]]}]},
            messages,
        )
        self.conn.commit()
        self.assert_extended(node, config.SEMANTIC_DAYS)


if __name__ == "__main__":
    unittest.main()
