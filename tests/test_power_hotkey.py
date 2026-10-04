import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from power_hotkey import PowerChord
from raw_media import split_hid_reports

UP, DOWN = 0xE9, 0xEA
BOTH = frozenset((UP, DOWN))
SOURCE = (1, 3)


class PowerChordTests(unittest.TestCase):
    def test_recorded_raw_chord_when_hook_reported_same_key_twice(self):
        chord = PowerChord()
        # Actual HID states at 14:10:43, despite the hook reporting only Up.
        events = [(frozenset((UP,)), 43.304), (BOTH, 43.311),
                  (frozenset((UP,)), 44.206), (frozenset(), 44.214)]
        self.assertEqual([chord.update(SOURCE, active, now) for active, now in events],
                         [False, True, False, False])

    def test_rapid_separate_presses_never_trigger(self):
        chord = PowerChord()
        for i in range(100):
            for offset, active in ((0, frozenset((UP,))), (0.008, frozenset()),
                                   (0.01, frozenset((DOWN,))), (0.018, frozenset())):
                self.assertFalse(chord.update(SOURCE, active, i * 0.02 + offset))

    def test_full_release_required_even_after_cooldown(self):
        chord = PowerChord()
        self.assertTrue(chord.update(SOURCE, BOTH, 0))
        self.assertFalse(chord.update(SOURCE, BOTH, 10))
        self.assertFalse(chord.update(SOURCE, frozenset((UP,)), 11))
        self.assertFalse(chord.update(SOURCE, BOTH, 12))
        self.assertFalse(chord.update(SOURCE, frozenset(), 13))
        self.assertTrue(chord.update(SOURCE, BOTH, 14))

    def test_cooldown_does_not_trigger_later_while_held(self):
        chord = PowerChord()
        self.assertTrue(chord.update(SOURCE, BOTH, 0))
        chord.update(SOURCE, frozenset(), 0.5)
        self.assertFalse(chord.update(SOURCE, BOTH, 1))
        self.assertFalse(chord.update(SOURCE, BOTH, 5))
        chord.update(SOURCE, frozenset(), 6)
        self.assertTrue(chord.update(SOURCE, BOTH, 7))

    def test_distinct_devices_and_reports_do_not_form_chord(self):
        chord = PowerChord()
        self.assertFalse(chord.update((1, 3), frozenset((UP,)), 0))
        self.assertFalse(chord.update((2, 3), frozenset((DOWN,)), 0))
        self.assertFalse(chord.update((1, 4), frozenset((DOWN,)), 0))

    def test_other_report_does_not_rearm_and_unplug_clears_latch(self):
        chord = PowerChord()
        self.assertTrue(chord.update(SOURCE, BOTH, 0))
        chord.update((1, 4), frozenset(), 5)
        self.assertFalse(chord.update(SOURCE, BOTH, 6))
        chord.remove_device(1)
        self.assertTrue(chord.update(SOURCE, BOTH, 7))

    def test_other_consumer_buttons_do_not_prevent_volume_release(self):
        chord = PowerChord()
        self.assertTrue(chord.update(SOURCE, BOTH | {0xE2}, 0))
        chord.update(SOURCE, frozenset((0xE2,)), 1)
        self.assertTrue(chord.update(SOURCE, BOTH, 4))


class RawReportTests(unittest.TestCase):
    def test_batched_reports_keep_order(self):
        reports = [bytes.fromhex('03 e9 00 ea 00'), bytes.fromhex('03 00 00 00 00')]
        payload = (5).to_bytes(4, 'little') + (2).to_bytes(4, 'little') + b''.join(reports)
        self.assertEqual(split_hid_reports(payload), reports)

    def test_truncated_and_zero_length_reports_rejected(self):
        for payload in (b'', bytes(8), bytes.fromhex('05 00 00 00 01 00 00 00 03')):
            with self.assertRaises(ValueError):
                split_hid_reports(payload)


if __name__ == '__main__':
    unittest.main()
