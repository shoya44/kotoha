"""固定化のバッチ切り出しの検証。一時DBだけを使い、LLMは呼ばない。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha consolidate ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha import consolidate, db  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FetchUnprocessedTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add_turn(self, turn_id, user_text="質問", assistant_text="返事"):
        db.insert_message(self.conn, turn_id, "user", user_text)
        db.insert_message(self.conn, turn_id, "assistant", assistant_text)
        self.conn.commit()

    def test_oversized_single_message_is_still_picked_up(self):
        """上限超えの1通で固定化が恒久停止する退行を防ぐ。"""
        self.add_turn(1, user_text="あ" * (config.BATCH_CHARS + 1000))
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertTrue(picked, "上限超えの1通でバッチが空になり、処理が進まなくなる")
        self.assertEqual(picked[0]["id"], 1)

    def test_progress_continues_after_an_oversized_message(self):
        """巨大メッセージを処理済みにすれば、次のバッチが前進する。"""
        self.add_turn(1, user_text="あ" * (config.BATCH_CHARS + 1000))
        self.add_turn(2, user_text="短い質問")
        first = consolidate.fetch_unprocessed(self.conn)
        db.set_state(self.conn, "last_processed_message_id", first[-1]["id"])
        self.conn.commit()
        second = consolidate.fetch_unprocessed(self.conn)
        self.assertTrue(second)
        self.assertGreater(second[0]["id"], first[-1]["id"])

    def test_char_limit_still_stops_the_batch(self):
        half = "い" * (config.BATCH_CHARS // 2 + 100)
        self.add_turn(1, user_text=half, assistant_text=half)
        self.add_turn(2)
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertEqual(len(picked), 1)  # 先頭1通のみ。2通目で上限に達する

    def test_turn_limit_still_stops_the_batch(self):
        for turn_id in range(1, config.BATCH_TURNS + 3):
            self.add_turn(turn_id)
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertEqual(len({r["turn_id"] for r in picked}), config.BATCH_TURNS)

    def test_nothing_to_do_returns_empty(self):
        self.assertEqual(consolidate.fetch_unprocessed(self.conn), [])


class ClipTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(consolidate._clip("短い本文"), "短い本文")

    def test_oversized_text_is_clipped(self):
        clipped = consolidate._clip("う" * (config.BATCH_CHARS + 5000))
        self.assertLess(len(clipped), config.BATCH_CHARS + 100)
        self.assertTrue(clipped.endswith("（以下省略）"))


if __name__ == "__main__":
    unittest.main()
