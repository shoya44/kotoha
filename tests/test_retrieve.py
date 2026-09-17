"""タグ想起の並び順の検証。一時DBだけを使い、LLMは呼ばない。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha retrieve ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import db, retrieve  # noqa: E402

NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
    "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)"
)


def tearDownModule():
    _TMP.cleanup()


class TagOrderTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add(self, text, tag, use_count, confirmed_at, last_used_at):
        cur = self.conn.execute(
            NODE_SQL,
            ("semantic", "fact", text, "2026-01-01", confirmed_at,
             last_used_at, "2099-01-01T00:00:00Z", 0, text),
        )
        self.conn.execute(
            "INSERT INTO memory_tags(node_id, tag, use_count, last_used_at) VALUES (?,?,?,?)",
            (cur.lastrowid, tag, use_count, last_used_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def test_frequently_used_memory_comes_first(self):
        """use_count が書かれるだけで読まれない状態への退行を防ぐ。"""
        rare = self.add("たまにしか使わない", "仕事", 0,
                        "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        often = self.add("よく思い出す", "仕事", 3,
                         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        _, related = retrieve.retrieve(self.conn, "仕事の話をしよう")
        self.assertEqual([r["id"] for r in related][:2], [often, rare])

    def test_recently_used_memory_wins_at_equal_counts(self):
        older = self.add("前に使った", "趣味", 1,
                         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        newer = self.add("最近使った", "趣味", 1,
                         "2026-01-01T00:00:00Z", "2026-09-01T00:00:00Z")
        _, related = retrieve.retrieve(self.conn, "趣味の話")
        self.assertEqual([r["id"] for r in related][:2], [newer, older])

    def test_tag_dictionary_lists_distinct_tags(self):
        self.add("あ", "仕事", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.add("い", "仕事", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.assertEqual(retrieve.load_tag_dict(self.conn), ["仕事"])

    def test_no_tag_hit_falls_back_to_recent(self):
        self.add("関係ない話", "料理", 0, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        pinned, related = retrieve.retrieve(self.conn, "まったく別の話題")
        self.assertEqual(pinned, [])
        self.assertEqual(related, [])  # episode でも open_topic でもないので拾わない


if __name__ == "__main__":
    unittest.main()
