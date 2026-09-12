import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wake_monitor import DISPLAY_DIMMED, DISPLAY_OFF, DISPLAY_ON, WakeController, parse_args  # noqa: E402


class WakeControllerTests(unittest.TestCase):
    def make_controller(self) -> WakeController:
        return WakeController(min_off_seconds=90, recent_input_seconds=5, cooldown_seconds=60)

    def test_initial_display_on_does_not_trigger(self):
        trigger, reason = self.make_controller().display_changed(DISPLAY_ON, now=100, input_age=0)
        self.assertFalse(trigger)
        self.assertIn("without", reason)

    def test_long_off_then_recent_input_triggers_once(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        trigger, _ = controller.display_changed(DISPLAY_ON, now=195, input_age=0.4)
        self.assertTrue(trigger)
        trigger, _ = controller.display_changed(DISPLAY_ON, now=196, input_age=0.2)
        self.assertFalse(trigger)

    def test_short_off_period_is_ignored_and_disarmed(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=150, input_age=0.2)
        self.assertFalse(trigger)
        self.assertIn("only 50.0s", reason)
        trigger, _ = controller.display_changed(DISPLAY_ON, now=200, input_age=0.1)
        self.assertFalse(trigger)

    def test_background_wake_without_recent_input_is_ignored(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=195, input_age=20)
        self.assertFalse(trigger)
        self.assertIn("20.0s", reason)

    def test_missing_input_time_fails_safe(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=195, input_age=None)
        self.assertFalse(trigger)
        self.assertIn("unavailable", reason)

    def test_cooldown_blocks_a_second_complete_cycle(self):
        controller = WakeController(min_off_seconds=10, recent_input_seconds=5, cooldown_seconds=60)
        controller.display_changed(DISPLAY_OFF, now=0, input_age=None)
        self.assertTrue(controller.display_changed(DISPLAY_ON, now=20, input_age=0)[0])
        controller.display_changed(DISPLAY_OFF, now=21, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=32, input_age=0)
        self.assertFalse(trigger)
        self.assertIn("cooldown", reason)

        controller.display_changed(DISPLAY_OFF, now=80, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=91, input_age=0)
        self.assertTrue(trigger, reason)

    def test_duplicate_off_preserves_first_timestamp(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        controller.display_changed(DISPLAY_OFF, now=150, input_age=None)
        trigger, reason = controller.display_changed(DISPLAY_ON, now=195, input_age=0)
        self.assertTrue(trigger, reason)

    def test_dim_does_not_arm_or_disarm(self):
        controller = self.make_controller()
        controller.display_changed(DISPLAY_DIMMED, now=50, input_age=None)
        self.assertFalse(controller.display_changed(DISPLAY_ON, now=100, input_age=0)[0])
        controller.display_changed(DISPLAY_OFF, now=100, input_age=None)
        controller.display_changed(DISPLAY_DIMMED, now=150, input_age=None)
        self.assertTrue(controller.display_changed(DISPLAY_ON, now=195, input_age=0)[0])

    def test_default_waits_for_tv_standby_before_ir(self):
        self.assertEqual(parse_args([]).min_off_seconds, 610)


if __name__ == "__main__":
    unittest.main()
