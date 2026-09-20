"""脳はひとつ。**取り込んだだけでは、何も始まらない。**

2026-09 まで jobs.py はモジュール直下で時計を回していた。定数ひとつの
ために serve.web を取り込んだトレイの中でも巡回が立ち、ことはの脳が
2つのプロセスで回っていた。前面アプリの数えは毎分2つ増え、記憶整理は
二度走り、同じ頼まれごとが2通届いた。

ここは、その形が戻っていないことを見張る。
"""

import importlib
import threading
import unittest

from tests.support import use_temp_db

_TMP = use_temp_db("one brain")

from kotoha.serve import jobs  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class ImportStartsNothingTests(unittest.TestCase):
    def test_importing_the_brain_does_not_start_the_clock(self):
        importlib.import_module("kotoha.serve.web")
        names = [t.name for t in threading.enumerate()]
        self.assertNotIn(jobs.JOBS_THREAD_NAME, names)

    def test_the_tray_does_not_reach_into_the_brain(self):
        """終了コードは config が持つ。トレイが脳を取り込む理由を残さない。"""
        from kotoha import config

        self.assertEqual(config.RESTART_EXIT_CODE, 42)
        source = (config.BASE_DIR / "kotoha" / "tray.py").read_text(encoding="utf-8")
        self.assertNotIn("serve.web", source)
