"""自分の様子。止まっていた長さ、中身の変わり、設定の変わりに、ことは自身が気づく。

渡すのは状況の行の1行だけ。鳴らさない。触れるかどうかは人格に任せる。
"""

import unittest
from datetime import datetime

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("myself")

from kotoha.memory import db  # noqa: E402
from kotoha.talk import chat, myself  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


NOON = datetime(2026, 9, 22, 12, 0)


class MyselfCase(DbCase):
    def setUp(self):
        super().setUp()
        # 中身の印は差し替えて、テストの py が書き換わっても揺れないようにする。
        self._fingerprint = myself.fingerprint
        myself.fingerprint = lambda root=None: "same"
        self.addCleanup(setattr, myself, "fingerprint", self._fingerprint)
        self._voice = config.VOICE_ENABLED
        config.VOICE_ENABLED = True
        self.addCleanup(setattr, config, "VOICE_ENABLED", self._voice)

    def line(self):
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        found = [l for l in prompt.split("\n") if l.startswith("自分の様子")]
        return found[0] if found else ""


class WakeTests(MyselfCase):
    def test_first_boot_says_nothing_but_leaves_marks(self):
        """初めては比べる相手が無い。印だけ残す。"""
        with Clock(NOON):
            self.assertEqual(myself.wake(self.conn), "")
        self.assertTrue(db.get_state(self.conn, db.SELF_CODE))
        self.assertTrue(db.get_state(self.conn, db.LAST_ALIVE_AT))
        self.assertEqual(self.line(), "")

    def test_long_stop_is_noticed_in_hours(self):
        with Clock(NOON) as clock:
            myself.wake(self.conn)
            myself.heartbeat(self.conn)
            clock.advance(hours=3)
            self.assertIn("3時間", myself.wake(self.conn))
            self.assertIn("止まっていて", self.line())
            self.assertIn("たった今", self.line())

    def test_short_nap_is_not_worth_mentioning(self):
        """トレイの上げ直しや、起こし直しの数分では言わない。"""
        with Clock(NOON) as clock:
            myself.wake(self.conn)
            myself.heartbeat(self.conn)
            clock.advance(minutes=myself.NAP_MINUTES - 1)
            self.assertEqual(myself.wake(self.conn), "")
            self.assertEqual(self.line(), "")

    def test_heartbeat_decides_where_the_stop_starts(self):
        """止まっていた長さは、最後の生きている印から数える。起きた時刻からではない。"""
        with Clock(NOON) as clock:
            myself.wake(self.conn)
            clock.advance(hours=5)
            myself.heartbeat(self.conn)
            clock.advance(hours=2)
            self.assertIn("2時間", myself.wake(self.conn))

    def test_changed_code_is_noticed_without_knowing_what(self):
        with Clock(NOON):
            myself.wake(self.conn)
            myself.fingerprint = lambda root=None: "different"
            note = myself.wake(self.conn)
        self.assertIn("中身が少し変わっていた", note)
        self.assertIn("何が変わったかは分からない", note)
        self.assertNotIn("止まっていて", note)

    def test_same_code_next_boot_is_quiet(self):
        with Clock(NOON):
            myself.wake(self.conn)
            myself.fingerprint = lambda root=None: "different"
            myself.wake(self.conn)
            self.assertEqual(myself.wake(self.conn), "")

    def test_settings_changed_across_restart(self):
        with Clock(NOON):
            myself.wake(self.conn)
            config.VOICE_ENABLED = False
            self.assertIn("声が止められた", myself.wake(self.conn))

    def test_note_fades_after_a_day(self):
        with Clock(NOON) as clock:
            myself.wake(self.conn)
            myself.heartbeat(self.conn)
            clock.advance(hours=3)
            myself.wake(self.conn)
            self.assertTrue(self.line())
            clock.advance(hours=myself.NOTE_KEEP_HOURS + 1)
            self.assertEqual(self.line(), "")


class SettingsChangedTests(MyselfCase):
    def test_sheet_change_is_noticed_without_restart(self):
        with Clock(NOON):
            myself.wake(self.conn)
            config.VOICE_ENABLED = False
            self.assertEqual(myself.settings_changed(self.conn), "声が止められた")
            self.assertIn("設定が変わった: 声が止められた", self.line())

    def test_unwatched_or_unchanged_settings_say_nothing(self):
        with Clock(NOON):
            myself.wake(self.conn)
            self.assertEqual(myself.settings_changed(self.conn), "")
            self.assertEqual(self.line(), "")

    def test_turning_back_on_is_worded_the_other_way(self):
        with Clock(NOON):
            config.VOICE_ENABLED = False
            myself.wake(self.conn)
            config.VOICE_ENABLED = True
            self.assertEqual(myself.settings_changed(self.conn), "声が入れられた")


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_changes_with_content(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            before = myself.fingerprint(root)
            (root / "a.py").write_text("x = 2\n", encoding="utf-8")
            self.assertNotEqual(before, myself.fingerprint(root))
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(before, myself.fingerprint(root))

    def test_watched_keys_exist_in_config(self):
        """見張る名前が config に無ければ、いつまでも False で「変わらない」。"""
        for key in myself.WATCHED:
            self.assertTrue(hasattr(config, key[len("KOTOHA_"):]), key)


if __name__ == "__main__":
    unittest.main()
