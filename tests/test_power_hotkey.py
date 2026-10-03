import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from power_hotkey import PowerChord


class PowerChordTests(unittest.TestCase):
    def test_all_keys_required_and_repeats_ignored(self):
        chord = PowerChord()
        self.assertFalse(chord.update(0xAD, True, 0))
        self.assertFalse(chord.update(0xAE, True, 0))
        self.assertTrue(chord.update(0xAF, True, 0))
        self.assertFalse(chord.update(0xAF, True, 10))
        chord.update(0xAF, False, 11)
        self.assertFalse(chord.update(0xAF, True, 12))

    def test_full_release_rearms_with_cooldown(self):
        chord = PowerChord()
        self.assertFalse(chord.update(0xAF, True, 0))
        self.assertTrue(chord.update(0xAE, True, 0))
        for key in (0xAE, 0xAF):
            chord.update(key, False, 1)
        for key in (0xAE, 0xAF):
            self.assertFalse(chord.update(key, True, 2))
        for key in (0xAE, 0xAF):
            chord.update(key, False, 3)
        chord.update(0xAD, True, 4)
        chord.update(0xAE, True, 4)
        self.assertTrue(chord.update(0xAF, True, 4))

    def test_slow_sequence_does_not_trigger(self):
        chord = PowerChord()
        self.assertFalse(chord.update(0xAE, True, 0))
        self.assertFalse(chord.update(0xAE, False, 0.01))
        self.assertFalse(chord.update(0xAF, True, 4))

    def test_brief_opposite_pulses_trigger_in_either_order(self):
        for first, second in ((0xAE, 0xAF), (0xAF, 0xAE)):
            chord = PowerChord()
            self.assertFalse(chord.update(first, True, 0))
            self.assertFalse(chord.update(first, False, 0.01))
            self.assertFalse(chord.update(second, True, 0.08))
            self.assertTrue(chord.update(second, False, 0.09))
            self.assertFalse(chord.update(first, True, 0.15))
            self.assertFalse(chord.update(first, False, 0.16))
            self.assertFalse(chord.update(second, True, 0.2))
            self.assertFalse(chord.update(second, False, 0.21))

    def test_recorded_combined_press(self):
        chord = PowerChord()
        events = [(0xAE, True, 50.784), (0xAE, False, 50.792),
                  (0xAF, True, 50.954), (0xAF, False, 50.962)]
        self.assertEqual([chord.update(*event) for event in events],
                         [False, False, False, True])

    def test_recorded_rapid_alternating_taps_rejected(self):
        chord = PowerChord()
        events = [(0xAE, True, 3.070), (0xAE, False, 3.176),
                  (0xAF, True, 3.292), (0xAF, False, 3.396),
                  (0xAE, True, 3.450), (0xAE, False, 3.538),
                  (0xAF, True, 3.638), (0xAF, False, 3.748)]
        self.assertFalse(any(chord.update(*event) for event in events))

    def test_both_pulses_must_be_short(self):
        for first_duration, second_duration in ((0.08, 0.008), (0.008, 0.08)):
            chord = PowerChord()
            events = [(0xAE, True, 0), (0xAE, False, first_duration),
                      (0xAF, True, 0.15), (0xAF, False, 0.15 + second_duration)]
            self.assertFalse(any(chord.update(*event) for event in events))

    def test_same_button_pulses_do_not_trigger(self):
        chord = PowerChord()
        for now in (0, 0.1, 0.2):
            self.assertFalse(chord.update(0xAE, True, now))
            self.assertFalse(chord.update(0xAE, False, now + 0.01))


if __name__ == '__main__':
    unittest.main()
