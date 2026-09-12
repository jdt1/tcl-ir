import sys
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from display_wake import recover_display  # noqa: E402


class RecoverDisplayTests(unittest.TestCase):
    @patch("display_wake.time.sleep")
    @patch("display_wake.restart_tcl_monitor")
    @patch("display_wake.tcl_display_is_active", return_value=True)
    @patch("display_wake.wake_display")
    def test_active_display_does_not_restart_monitor(self, _wake, _active, restart, _sleep):
        result = recover_display()
        restart.assert_not_called()
        self.assertIn("already active", result)

    @patch("display_wake.time.sleep")
    @patch("display_wake.restart_tcl_monitor", return_value="restart ok")
    @patch("display_wake.tcl_display_is_active", side_effect=[False, True])
    @patch("display_wake.wake_display")
    def test_inactive_display_is_restarted(self, wake, _active, restart, _sleep):
        result = recover_display()
        restart.assert_called_once_with()
        self.assertEqual(wake.call_count, 2)
        self.assertIn("reactivated", result)

    @patch("display_wake.time.sleep")
    @patch("display_wake.restart_tcl_monitor", return_value="restart ok")
    @patch("display_wake.tcl_display_is_active", side_effect=[False, False])
    @patch("display_wake.wake_display")
    def test_failed_restart_raises(self, _wake, _active, _restart, _sleep):
        with self.assertRaisesRegex(RuntimeError, "remains inactive"):
            recover_display()


if __name__ == "__main__":
    unittest.main()
