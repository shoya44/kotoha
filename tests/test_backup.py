"""世代付きバックアップの検証。一時DBだけを使い、本番DBには触れない。"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha backup ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class BackupTests(unittest.TestCase):
    def setUp(self):
        folder = db.backup_dir()
        if folder.exists():
            for old in folder.glob("*"):
                old.unlink()
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()

    def open_db(self):
        conn = db.connect()
        self.addCleanup(conn.close)
        db.init(conn)
        return conn

    def generations(self):
        return sorted(p.name for p in db.backup_dir().glob("kotoha_*.sqlite3"))

    def test_missing_database_is_not_an_error(self):
        self.assertIsNone(db.run_backup())

    def test_creates_a_readable_copy(self):
        conn = self.open_db()
        db.insert_message(conn, 1, "user", "ひかえめな挨拶")
        conn.commit()
        dest = db.run_backup()
        self.assertTrue(dest.exists())
        copied = sqlite3.connect(dest)
        self.addCleanup(copied.close)
        text = copied.execute("SELECT text FROM messages").fetchone()[0]
        self.assertEqual(text, "ひかえめな挨拶")

    def test_records_the_time_when_given_a_connection(self):
        conn = self.open_db()
        self.assertIsNone(db.get_state(conn, "last_backup_at"))
        db.run_backup(conn)
        self.assertLess(db.seconds_since(db.get_state(conn, "last_backup_at")), 60)

    def test_manual_backup_does_not_reset_the_timer(self):
        """CUI の backup コマンドで自動バックアップの間隔が狂わないようにする。"""
        conn = self.open_db()
        db.set_state(conn, "last_backup_at", "2020-01-01T00:00:00Z")
        conn.commit()
        db.run_backup()  # conn を渡さない = 手動実行
        self.assertEqual(db.get_state(conn, "last_backup_at"), "2020-01-01T00:00:00Z")

    def test_old_generations_are_removed(self):
        self.open_db()
        folder = db.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        old = datetime.now() - timedelta(days=30)
        for day in range(config.BACKUP_KEEP + 3):
            stamp = (old + timedelta(days=day)).strftime("%Y%m%d_%H%M%S")
            (folder / f"kotoha_{stamp}.sqlite3").write_bytes(b"")
        db.run_backup()
        kept = self.generations()
        self.assertEqual(len(kept), config.BACKUP_KEEP)
        self.assertEqual(kept[-1], max(kept))  # 新しい世代が残る

    def test_unrelated_files_are_left_alone(self):
        self.open_db()
        folder = db.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        keeper = folder / "たいせつなメモ.txt"
        keeper.write_text("消さないで", encoding="utf-8")
        for day in range(config.BACKUP_KEEP + 3):
            (folder / f"kotoha_2020010{day % 10}_00000{day % 10}.sqlite3").write_bytes(b"")
        db.run_backup()
        self.assertTrue(keeper.exists())


class BackupScheduleTests(unittest.TestCase):
    """自動バックアップの実行判定。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def due(self):
        elapsed = db.seconds_since(db.get_state(self.conn, "last_backup_at"))
        return elapsed > config.BACKUP_INTERVAL_SECONDS

    def test_due_on_a_fresh_database(self):
        self.assertTrue(self.due())

    def test_not_due_right_after_a_backup(self):
        db.set_state(self.conn, "last_backup_at", db.now_utc())
        self.conn.commit()
        self.assertFalse(self.due())

    def test_due_again_after_the_interval(self):
        past = datetime.now(timezone.utc) - timedelta(
            seconds=config.BACKUP_INTERVAL_SECONDS + 60
        )
        db.set_state(self.conn, "last_backup_at", past.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.conn.commit()
        self.assertTrue(self.due())


class PeriodicJobOrderTests(unittest.TestCase):
    """バックアップと忘却の順序。"""

    def setUp(self):
        folder = db.backup_dir()
        if folder.exists():
            for old in folder.glob("*"):
                old.unlink()
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def test_backup_keeps_what_the_forgetting_removes(self):
        """忘却より先に取らないと、消えた直後の状態しか残らない。"""
        from kotoha.serve import web

        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)",
            ("semantic", "fact", "消える記憶", "2020-01-01", "2020-01-01T00:00:00Z",
             "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z", 0, "doomed"),
        )
        self.conn.commit()

        web.run_periodic_jobs(self.conn)

        remaining = self.conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        self.assertEqual(remaining, 0)  # 本体からは忘却されている

        copies = sorted(db.backup_dir().glob("kotoha_*.sqlite3"))
        self.assertEqual(len(copies), 1)
        saved = sqlite3.connect(copies[0])
        self.addCleanup(saved.close)
        rescued = saved.execute("SELECT text FROM memory_nodes").fetchone()
        self.assertIsNotNone(rescued, "忘却後にバックアップを取ると取り戻せなくなる")
        self.assertEqual(rescued[0], "消える記憶")


if __name__ == "__main__":
    unittest.main()
