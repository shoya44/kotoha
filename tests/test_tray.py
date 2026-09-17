"""トレイ常駐の見張りの検証。Windowsの画面まわりには触れない。"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha tray ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

if sys.platform == "win32":
    from kotoha import tray
    from kotoha.serve.web import RESTART_EXIT_CODE


def tearDownModule():
    _TMP.cleanup()


class FakeProcess:
    """終了コードを決められる子プロセス。wait は呼ばれるまで待たない。"""

    def __init__(self, code=0):
        self.code = code
        self.terminated = False
        self.killed = False
        self._done = False

    def poll(self):
        return self.code if self._done else None

    def wait(self, timeout=None):
        self._done = True
        return self.code

    def terminate(self):
        self.terminated = True
        self._done = True

    def kill(self):
        self.killed = True
        self._done = True


@unittest.skipUnless(sys.platform == "win32", "トレイ常駐はWindows専用")
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        for name in ("RESPAWN_WAIT", "WATCH_WAIT"):
            self.addCleanup(setattr, tray, name, getattr(tray, name))
            setattr(tray, name, 0.01)
        self.addCleanup(setattr, tray, "log", tray.log)
        tray.log = lambda message: None
        self.spawned = []

    def make(self, codes=(), serving=False):
        """codes を順に返す子を作る見張り。尽きたら止まる。"""
        sup = tray.Supervisor()
        codes = list(codes)
        sup.serving = lambda: serving() if callable(serving) else serving

        def spawn():
            if not codes:
                sup.stopping.set()
                return FakeProcess(0)
            process = FakeProcess(codes.pop(0))
            self.spawned.append(process)
            return process

        sup.spawn = spawn
        return sup

    def run_loop(self, sup, seconds=2.0):
        thread = threading.Thread(target=sup._loop, daemon=True)
        thread.start()
        thread.join(timeout=seconds)
        sup.stopping.set()
        self.assertFalse(thread.is_alive(), "見張りが止まらない")

    def test_it_starts_the_server(self):
        sup = self.make(codes=[0])
        self.run_loop(sup)
        self.assertEqual(len(self.spawned), 1)

    def test_a_restart_signal_brings_it_straight_back(self):
        sup = self.make(codes=[RESTART_EXIT_CODE, 0])
        self.run_loop(sup)
        self.assertEqual(len(self.spawned), 2)

    def test_a_crash_is_also_brought_back(self):
        sup = self.make(codes=[1, 0])
        self.run_loop(sup)
        self.assertEqual(len(self.spawned), 2)

    def test_it_does_not_fight_a_server_someone_else_started(self):
        """start.bat から上がっているときに子を作ると、即終了して上げ直し続ける。"""
        sup = self.make(codes=[0], serving=True)
        thread = threading.Thread(target=sup._loop, daemon=True)
        thread.start()
        sup.stopping.wait(0.2)
        sup.stopping.set()
        thread.join(timeout=2)
        self.assertEqual(self.spawned, [])

    def test_it_takes_over_when_the_other_one_goes_away(self):
        gone = {"yet": False}
        sup = self.make(codes=[0], serving=lambda: not gone["yet"])
        thread = threading.Thread(target=sup._loop, daemon=True)
        thread.start()
        sup.stopping.wait(0.1)
        gone["yet"] = True
        thread.join(timeout=2)
        sup.stopping.set()
        self.assertEqual(len(self.spawned), 1)

    def test_a_failure_to_launch_does_not_stop_the_watch(self):
        sup = tray.Supervisor()
        sup.serving = lambda: False
        tries = {"n": 0}

        def spawn():
            tries["n"] += 1
            if tries["n"] >= 3:
                sup.stopping.set()
            raise OSError("起動できない")

        sup.spawn = spawn
        self.run_loop(sup)
        self.assertGreaterEqual(tries["n"], 3)

    def test_restart_only_touches_its_own_child(self):
        sup = tray.Supervisor()
        sup.serving = lambda: True          # 誰かが動かしているだけの状態
        sup.restart()                        # 例外が出ないこと
        self.assertIsNone(sup.process)

    def test_stop_terminates_its_own_child(self):
        sup = tray.Supervisor()
        sup.process = FakeProcess()
        sup.stop()
        self.assertTrue(sup.process.terminated)
        self.assertTrue(sup.stopping.is_set())

    def test_alive_covers_a_server_it_did_not_start(self):
        sup = tray.Supervisor()
        sup.serving = lambda: True
        self.assertTrue(sup.alive())
        self.assertFalse(sup.mine())


if __name__ == "__main__":
    unittest.main()
