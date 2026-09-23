"""世代付きバックアップの検証。一時DBだけを使い、本番DBには触れない。"""

import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("backup")

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


class BackupScheduleTests(DbCase):
    """自動バックアップの実行判定。"""

    def setUp(self):
        super().setUp()
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
        # 巡回には声かけが含まれる。テストが本物のGeminiを叩いて
        # 持ち主の電話を鳴らさないよう、ここで止める。
        self.addCleanup(setattr, config, "REACH_OUT_ENABLED", config.REACH_OUT_ENABLED)
        config.REACH_OUT_ENABLED = False

    def test_backup_keeps_what_the_forgetting_removes(self):
        """忘却より先に取らないと、消えた直後の状態しか残らない。"""
        from kotoha.serve import jobs, web

        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)",
            ("semantic", "fact", "消える記憶", "2020-01-01", "2020-01-01T00:00:00Z",
             "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z", 0, "doomed"),
        )
        self.conn.commit()

        jobs.run_periodic_jobs(self.conn)

        remaining = self.conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        self.assertEqual(remaining, 0)  # 本体からは忘却されている

        copies = sorted(db.backup_dir().glob("kotoha_*.sqlite3"))
        self.assertEqual(len(copies), 1)
        saved = sqlite3.connect(copies[0])
        self.addCleanup(saved.close)
        rescued = saved.execute("SELECT text FROM memory_nodes").fetchone()
        self.assertIsNotNone(rescued, "忘却後にバックアップを取ると取り戻せなくなる")
        self.assertEqual(rescued[0], "消える記憶")

    def test_a_failed_backup_is_written_down(self):
        """**控えが取れていないことに、要るときまで気づけないのが一番困る。**"""
        from kotoha import notify
        from kotoha.serve import jobs, web

        self.addCleanup(setattr, notify, "log", notify.log)
        logged = []
        notify.log = logged.append

        def broken(conn):
            raise OSError("保存先が無い")

        self.addCleanup(setattr, db, "run_backup", db.run_backup)
        db.run_backup = broken
        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)",
            ("semantic", "fact", "消える記憶", "2020-01-01", "2020-01-01T00:00:00Z",
             "2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z", 0, "doomed"),
        )
        self.conn.commit()

        jobs.run_periodic_jobs(self.conn)

        self.assertTrue(any("バックアップ" in one for one in logged), logged)
        remaining = self.conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        self.assertEqual(remaining, 0, "保存先の不調で忘却まで止めない")


class ConsolidationTimingTests(DbCase):
    """整理を始める頃合い。巡回は会話と同じ順番待ちに並ぶ。"""

    def setUp(self):
        super().setUp()
        # 巡回には声かけが含まれる。テストが本物のGeminiを叩いて
        # 持ち主の電話を鳴らさないよう、ここで止める。
        self.addCleanup(setattr, config, "REACH_OUT_ENABLED", config.REACH_OUT_ENABLED)
        config.REACH_OUT_ENABLED = False

        from kotoha.serve import jobs, web

        self.jobs = jobs
        original = jobs.consolidate.run
        self.runs = []
        jobs.consolidate.run = lambda conn: self.runs.append(conn)
        self.addCleanup(setattr, jobs.consolidate, "run", original)

    def add_turns(self, count, last_spoken_at):
        for turn in range(1, count + 1):
            db.insert_message(self.conn, turn, "user", "はなしかけ")
            db.insert_message(self.conn, turn, "assistant", "へんじ")
        db.set_state(self.conn, "last_conversation_at", last_spoken_at)
        # 忘却やバックアップに気を取られないようにしておく
        db.set_state(self.conn, "last_forget_at", db.now_utc())
        db.set_state(self.conn, "last_backup_at", db.now_utc())
        self.conn.commit()

    def test_not_while_still_talking(self):
        """会話中に整理が走ると、その数秒ぶん返答が止まって聞こえる。"""
        self.add_turns(config.CONSOLIDATE_TURNS + 5, db.now_utc())
        self.jobs.run_periodic_jobs(self.conn)
        self.assertEqual(self.runs, [])

    def test_runs_once_the_talking_stops(self):
        idle = datetime.now(timezone.utc) - timedelta(seconds=config.IDLE_SECONDS + 60)
        self.add_turns(2, idle.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.jobs.run_periodic_jobs(self.conn)
        self.assertEqual(len(self.runs), 1)

    def test_nothing_to_do_when_everything_is_processed(self):
        idle = datetime.now(timezone.utc) - timedelta(seconds=config.IDLE_SECONDS + 60)
        self.add_turns(2, idle.strftime("%Y-%m-%dT%H:%M:%SZ"))
        last = self.conn.execute("SELECT MAX(id) FROM messages").fetchone()[0]
        db.set_state(self.conn, "last_processed_message_id", last)
        self.conn.commit()
        self.jobs.run_periodic_jobs(self.conn)
        self.assertEqual(self.runs, [])


class RoundTests(unittest.TestCase):
    """裏の巡回がひと回りすること。ここが落ちると、何も起きないまま静かになる。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()
        # 一周には声かけと想起が含まれる。**本物の通知とOllamaを叩かせない。**
        # ここを開けたまま走らせて、持ち主の電話を実際に鳴らしたことがある。
        for name in ("PUSH_ENABLED", "EMBED_ENABLED"):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, False)

        from kotoha import notify
        from kotoha.serve import jobs

        self.jobs, self.notify = jobs, notify
        self.addCleanup(setattr, notify, "log", notify.log)
        self.logged = []
        notify.log = self.logged.append

    def test_one_round_does_not_fall_over(self):
        """名前の付け替えで巡回が丸ごと止まったことがある。実際に一周させて確かめる。"""
        self.jobs.one_round()
        self.assertEqual(self.logged, [])

    def test_a_failure_is_written_down(self):
        """黙って飲み込まない。落ちたことは残す。"""
        original = self.jobs.ROUNDS
        self.addCleanup(setattr, self.jobs, "ROUNDS", original)

        def broken():
            raise RuntimeError("こわれた")

        self.jobs.ROUNDS = (("巡回", broken),) + original[1:]
        self.jobs.one_round()
        self.assertTrue(any("巡回で失敗" in line for line in self.logged))

    def test_the_rest_keeps_going(self):
        original = self.jobs.ROUNDS
        self.addCleanup(setattr, self.jobs, "ROUNDS", original)
        ran = []

        def broken():
            raise RuntimeError("こわれた")

        self.jobs.ROUNDS = (("巡回", broken), ("見張り", lambda: ran.append(1)))
        self.jobs.one_round()
        self.assertEqual(ran, [1])


if __name__ == "__main__":
    unittest.main()


class TurnNumberTests(unittest.TestCase):
    """往復の番号を、2人が同時に取りに来ても取り合わないこと。

    **2026-09-18に一度落ちた。** 番号を見る文と書き込む文が分かれていて、
    そのあいだにもう一方が書き込むと同じ番号になり、UNIQUE(turn_id, role)
    で弾かれた。頼まれごとが定型に落ち、同じ用件が2通届いた。
    """

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()

    def test_numbers_do_not_collide_when_two_write_at_once(self):
        import threading

        start = threading.Barrier(7)   # 6人 + 合図を出すこちら
        failures = []

        def write(n):
            conn = db.connect()
            try:
                start.wait(timeout=5)
                for i in range(4):
                    db.start_turn(conn, "assistant", f"{n}-{i}")
                    conn.commit()
            except Exception as error:      # noqa: BLE001 見たいのは落ちたこと自体
                failures.append(error)
            finally:
                conn.close()

        hands = [threading.Thread(target=write, args=(n,)) for n in range(6)]
        for hand in hands:
            hand.start()
        start.wait(timeout=5)
        for hand in hands:
            hand.join(timeout=10)

        self.assertEqual(failures, [], "同じ番号を取り合って落ちた")

        conn = db.connect()
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT turn_id FROM messages").fetchall()
        numbers = [r["turn_id"] for r in rows]
        self.assertEqual(len(numbers), 24)
        self.assertEqual(len(set(numbers)), 24, "同じ番号が二度使われた")

    def test_the_number_used_comes_back(self):
        conn = db.connect()
        self.addCleanup(conn.close)
        first = db.start_turn(conn, "user", "はなしかけ")
        db.insert_message(conn, first, "assistant", "へんじ")
        conn.commit()
        pair = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE turn_id = ?",
                            (first,)).fetchone()["n"]
        self.assertEqual(pair, 2, "同じ往復の2件が揃わない")


class WatchLockTests(unittest.TestCase):
    """見張りが何か言うときは、他の書き手と順番を分け合うこと。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()

        from kotoha import notify
        from kotoha.serve import jobs

        self.jobs = jobs
        for name, value in (("PUSH_ENABLED", True), ("ONESIGNAL_APP_ID", "app-1"),
                            ("ONESIGNAL_API_KEY", "key-1")):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, notify, "log", notify.log)
        notify.log = lambda message: None

        # 落ちたことにして、必ず何か言わせる。
        self.addCleanup(setattr, jobs, "tool_probes", jobs.tool_probes)
        jobs.tool_probes = lambda: {"音声エンジン": lambda: False}

        self.addCleanup(setattr, jobs, "presence", jobs.presence)

        class NoDisks:
            @staticmethod
            def disks():
                return []

        jobs.presence = NoDisks

    def test_it_takes_its_turn_before_speaking(self):
        held = []
        original = self.jobs.announce
        self.addCleanup(setattr, self.jobs, "announce", original)
        self.jobs.announce = lambda *a, **k: held.append(self.jobs.turn_lock.locked())

        conn = db.connect()
        db.set_state(conn, db.UP_PREFIX + "音声エンジン", "1")   # 前回は動いていた
        conn.commit()
        conn.close()

        self.jobs.run_watch_jobs()

        self.assertEqual(held, [True], "順番待ちに並ばずに書き込んでいる")


class SpareBackupTests(DbCase):
    """控えのもう1本。**ディスク1枚と記憶の運命を切り離す。**"""

    def setUp(self):
        super().setUp()
        self.spare = Path(tempfile.mkdtemp(prefix="kotoha spare "))
        self.addCleanup(shutil.rmtree, self.spare, True)
        original = config.BACKUP_DIR
        config.BACKUP_DIR = str(self.spare)
        self.addCleanup(setattr, config, "BACKUP_DIR", original)

    def test_the_spare_gets_its_own_copy(self):
        dest = db.run_backup()
        self.assertIsNotNone(dest)
        self.assertTrue((self.spare / dest.name).exists())

    def test_a_missing_spare_does_not_lose_the_one_next_door(self):
        blocker = self.spare / "blocker"
        blocker.write_text("外付けが外れている、のかわり", encoding="utf-8")
        config.BACKUP_DIR = str(blocker / "backups")
        dest = db.run_backup()
        self.assertIsNotNone(dest)
        self.assertTrue(dest.exists())

    def test_no_setting_means_nothing_extra(self):
        config.BACKUP_DIR = ""
        self.assertIsNone(db.spare_dir())


class MigrationTests(DbCase):
    """版を数える足場。次にスキーマを触る日まで、中身は空のまま。"""

    def test_a_fresh_db_lands_on_the_latest_version(self):
        db.migrate(self.conn)
        latest = max((v for v, _ in db.MIGRATIONS), default=0)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], latest)

    def test_only_the_missing_ones_are_applied(self):
        original = db.MIGRATIONS
        self.addCleanup(setattr, db, "MIGRATIONS", original)
        ran = []
        db.MIGRATIONS = [
            (1, ["CREATE TABLE IF NOT EXISTS step_one(x)"]),
            (2, ["CREATE TABLE IF NOT EXISTS step_two(x)"]),
        ]
        self.conn.execute("PRAGMA user_version = 1")
        db.migrate(self.conn)
        tables = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("step_one", tables)      # 当たっている版は飛ばす
        self.assertIn("step_two", tables)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(ran, [])



class SpareIsNotAFolderTests(DbCase):
    """置き場がファイルを指していたら、そう書く。FileExistsError では何を直せばよいか読めない。"""

    def test_a_file_in_the_way_is_named(self):
        from kotoha import notify
        self.addCleanup(setattr, notify, "log", notify.log)
        logged = []
        notify.log = logged.append
        blocker = Path(tempfile.mkdtemp(prefix="kotoha spare ")) / "not-a-folder"
        self.addCleanup(shutil.rmtree, blocker.parent, True)
        blocker.write_text("ここはファイル", encoding="utf-8")
        self.addCleanup(setattr, config, "BACKUP_DIR", config.BACKUP_DIR)
        config.BACKUP_DIR = str(blocker)
        dest = db.run_backup()
        self.assertTrue(dest.exists())                     # 隣の1本は取れている
        self.assertTrue(any("フォルダーではない" in line for line in logged), logged)


class JournalTests(unittest.TestCase):
    """読む側と書く側が互いを待たない（WAL）。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()

    def test_the_journal_is_wal(self):
        conn = db.connect()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_a_reader_does_not_block_a_writer(self):
        reader = db.connect()
        self.addCleanup(reader.close)
        db.init(reader)
        cursor = reader.execute("SELECT * FROM messages")   # 読みかけのまま
        writer = db.connect()
        self.addCleanup(writer.close)
        db.set_state(writer, "probe", "1")
        writer.commit()
        cursor.fetchall()
        self.assertEqual(db.get_state(reader, "probe"), "1")
