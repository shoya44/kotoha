"""常駐の登録が古くなったときの直し。レジストリとタスクには触れない。"""

import unittest
from unittest.mock import Mock, patch

from kotoha import autostart


class RefreshTests(unittest.TestCase):
    """**登録していない人に登録しない。** するのは直しであって、勧誘ではない。"""

    def test_missing_registration_is_left_alone(self):
        with patch.object(autostart, "current", return_value=None), \
             patch.object(autostart, "enable") as enable:
            self.assertFalse(autostart.refresh())
        enable.assert_not_called()

    def test_current_registration_is_left_alone(self):
        with patch.object(autostart, "current", return_value=autostart.command()), \
             patch.object(autostart, "enable") as enable:
            self.assertFalse(autostart.refresh())
        enable.assert_not_called()

    def test_stale_registration_is_rewritten(self):
        stale = '"C:\\kotoha\\.venv\\Scripts\\pythonw.exe" "C:\\kotoha\\tray.pyw"'
        with patch.object(autostart, "current", return_value=stale), \
             patch.object(autostart, "enable") as enable:
            self.assertTrue(autostart.refresh())
        enable.assert_called_once()

    def test_locked_registry_does_not_stop_startup(self):
        with patch.object(autostart, "current", side_effect=OSError):
            self.assertFalse(autostart.refresh())


class WatchdogTests(unittest.TestCase):
    def test_task_pointing_at_the_old_place_is_noticed(self):
        with patch.object(autostart, "_task_xml",
                          return_value="<Arguments>\"C:\\kotoha\\tray.pyw\"</Arguments>"):
            self.assertTrue(autostart.watchdog_needs_fixing())

    def test_task_pointing_here_is_left_alone(self):
        with patch.object(autostart, "_task_xml",
                          return_value=f"<Arguments>\"{autostart.TRAY}\"</Arguments>"):
            self.assertFalse(autostart.watchdog_needs_fixing())

    def test_no_task_is_not_a_problem(self):
        with patch.object(autostart, "_task_xml", return_value=""):
            self.assertFalse(autostart.watchdog_needs_fixing())

    def test_unfixable_task_is_said_out_loud(self):
        """直せないときに黙ると、次にPCが落ちるまで誰も気づけない。"""
        said = []
        with patch.object(autostart, "refresh"), \
             patch.object(autostart, "watchdog_needs_fixing", return_value=True), \
             patch.object(autostart, "fix_watchdog", return_value=False):
            autostart.repair(say=said.append)
        self.assertEqual(len(said), 1)
        self.assertIn("rescue", said[0])

    def test_fixed_task_is_quiet(self):
        say = Mock()
        with patch.object(autostart, "refresh"), \
             patch.object(autostart, "watchdog_needs_fixing", return_value=True), \
             patch.object(autostart, "fix_watchdog", return_value=True):
            autostart.repair(say=say)
        say.assert_not_called()


if __name__ == "__main__":
    unittest.main()
