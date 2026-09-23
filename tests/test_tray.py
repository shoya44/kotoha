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
    from kotoha.config import RESTART_EXIT_CODE


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
        """kotoha.bat console から上がっているときに子を作ると、即終了して上げ直し続ける。"""
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


@unittest.skipUnless(sys.platform == "win32", "トレイ常駐はWindows専用")
class StatusTests(unittest.TestCase):
    """様子は先に調べて持っておく。メニューを開くたびに待たされないように。"""

    def setUp(self):
        self.addCleanup(setattr, tray, "log", tray.log)
        tray.log = lambda message: None
        self.sup = tray.Supervisor()
        self.sup.serving = lambda: False
        self.status = tray.Status(self.sup)

    def probe_with(self, aivis, ollama):
        import kotoha.launcher as launcher

        self.addCleanup(setattr, launcher, "aivis_is_up", launcher.aivis_is_up)
        self.addCleanup(setattr, launcher, "ollama_is_up", launcher.ollama_is_up)
        launcher.aivis_is_up = lambda: aivis
        launcher.ollama_is_up = lambda: ollama
        self.status.probe()
        return self.status.lines

    def test_it_reports_each_part(self):
        lines = self.probe_with(True, False)
        self.assertEqual(len(lines), 3)
        self.assertIn("音声エンジン: 動いている", lines)
        self.assertIn("Ollama: 止まっている", lines)

    def test_a_server_someone_else_started_is_marked(self):
        self.sup.serving = lambda: True
        lines = self.probe_with(True, True)
        self.assertIn("別に上がっているもの", lines[0])

    def test_our_own_server_is_not_marked(self):
        self.sup.process = FakeProcess()
        lines = self.probe_with(True, True)
        self.assertNotIn("別に上がっているもの", lines[0])

    def test_it_tells_someone_when_it_changes(self):
        seen = []
        self.status.on_change = lambda: seen.append(True)
        self.probe_with(True, True)
        self.assertEqual(len(seen), 1)

    def test_a_failure_does_not_stop_the_watch(self):
        """様子を調べられなくても、常駐そのものは落とさない。"""
        import kotoha.launcher as launcher

        self.addCleanup(setattr, launcher, "aivis_is_up", launcher.aivis_is_up)
        launcher.aivis_is_up = lambda: 1 / 0
        self.addCleanup(setattr, tray, "STATUS_WAIT", tray.STATUS_WAIT)
        tray.STATUS_WAIT = 0.01
        thread = threading.Thread(target=self.status._loop, daemon=True)
        thread.start()
        self.status.stopping.wait(0.1)
        self.status.stop()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())




@unittest.skipUnless(sys.platform == "win32", "トレイはWindows専用")
class AlreadyRunningTests(unittest.TestCase):
    """終わりかけの前の常駐を「いる」と取り違えない。終えた3秒後のダブルクリックで何も出なかった。"""

    def setUp(self):
        from unittest import mock
        self.mock = mock
        patcher = mock.patch.object(tray.time, "sleep", lambda _s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_waits_for_a_tray_that_is_leaving(self):
        with self.mock.patch.object(tray, "_taken", side_effect=[True, True, False]):
            self.assertFalse(tray.already_running(wait=5))

    def test_a_tray_that_stays_is_running(self):
        with self.mock.patch.object(tray, "_taken", return_value=True):
            self.assertTrue(tray.already_running(wait=0))

    def test_nobody_there_starts_at_once(self):
        with self.mock.patch.object(tray, "_taken", return_value=False) as taken:
            self.assertFalse(tray.already_running())
        self.assertEqual(taken.call_count, 1)


@unittest.skipUnless(sys.platform == "win32", "トレイ常駐はWindows専用")
class FigureTests(unittest.TestCase):
    """しまった姿を、もう一度出せること。トレイごと入れ直す以外に道が無かった。"""

    def setUp(self):
        self.addCleanup(setattr, tray, "RESPAWN_WAIT", tray.RESPAWN_WAIT)
        tray.RESPAWN_WAIT = 0.01
        self.addCleanup(setattr, tray, "log", tray.log)
        tray.log = lambda message: None
        self.addCleanup(setattr, tray.config, "MASCOT_ENABLED", tray.config.MASCOT_ENABLED)
        tray.config.MASCOT_ENABLED = True
        self.spawned = []
        self.figure = tray.Figure()
        self.figure.spawn = self.spawn

    def spawn(self):
        process = FakeProcess(0)     # 「しまう」で自分から終わる
        self.spawned.append(process)
        return process

    def settle(self):
        thread = self.figure._thread
        if thread is not None:
            thread.join(timeout=2)

    def test_put_away_means_no_respawn(self):
        self.figure.start()
        self.settle()
        self.assertEqual(len(self.spawned), 1)
        self.assertFalse(self.figure.watching())

    def test_show_brings_it_back(self):
        self.figure.start()
        self.settle()
        self.assertTrue(self.figure.show())
        self.settle()
        self.assertEqual(len(self.spawned), 2)

    def test_show_does_nothing_while_it_is_out(self):
        self.figure.process = FakeProcess(0)      # まだ出ている
        self.assertFalse(self.figure.show())
        self.assertEqual(self.spawned, [])

    def test_switched_off_stays_off(self):
        tray.config.MASCOT_ENABLED = False
        self.assertFalse(self.figure.show())
        self.assertEqual(self.spawned, [])


@unittest.skipUnless(sys.platform == "win32", "トレイ常駐はWindows専用")
class ClickTests(unittest.TestCase):
    """シングルは姿、ダブルは会話画面。ダブルの前に来るシングルで姿を出さない。"""

    def setUp(self):
        self.addCleanup(setattr, tray, "log", tray.log)
        tray.log = lambda message: None
        for name in ("SetTimer", "KillTimer", "GetDoubleClickTime"):
            self.addCleanup(setattr, tray.user32, name, getattr(tray.user32, name))
        self.timers = []
        tray.user32.SetTimer = lambda hwnd, ident, ms, proc: self.timers.append(ident) or 1
        tray.user32.KillTimer = lambda hwnd, ident: self.timers.remove(ident) if ident in self.timers else 0
        tray.user32.GetDoubleClickTime = lambda: 500
        sup = tray.Supervisor()
        sup.serving = lambda: False
        self.tray = tray.Tray(sup, tray.Status(sup))
        self.done = []
        self.tray.open_chat = lambda: self.done.append("chat")
        self.tray.show_figure = lambda: self.done.append("figure")

    def click(self, lparam):
        self.tray._handle(None, tray.WM_TRAY, 0, lparam)

    def tick(self):
        self.tray._handle(None, tray.WM_TIMER, tray.TIMER_CLICK, 0)

    def test_a_single_click_waits_then_shows_the_figure(self):
        self.click(tray.WM_LBUTTONUP)
        self.assertEqual(self.done, [])              # まだ決めない
        self.assertEqual(self.timers, [tray.TIMER_CLICK])
        self.tick()
        self.assertEqual(self.done, ["figure"])
        self.assertEqual(self.timers, [])

    def test_a_double_click_opens_the_chat_and_cancels_the_single(self):
        self.click(tray.WM_LBUTTONUP)
        self.click(tray.WM_LBUTTONDBLCLK)
        self.assertEqual(self.done, ["chat"])
        self.assertEqual(self.timers, [])            # シングルの時計は止めた
        # Windows はダブルクリックのあとにも WM_LBUTTONUP を1つ送る。それで姿を出さない。
        self.click(tray.WM_LBUTTONUP)
        self.assertEqual(self.timers, [])            # シングルの時計を掛け直さない

    def test_a_stray_timer_is_not_a_click(self):
        self.tray._handle(None, tray.WM_TIMER, 99, 0)
        self.assertEqual(self.done, [])

    def test_the_menu_can_bring_the_figure_back(self):
        self.tray.command(tray.ID_FIGURE)
        self.assertEqual(self.done, ["figure"])


if __name__ == "__main__":
    unittest.main()
