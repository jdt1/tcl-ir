import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ir_control import (  # noqa: E402
    compress_pulses,
    decode_learned_signal,
    encode_d552,
    encode_rca,
    encode_timing,
    mangle_byte,
)


class IrControlEncodingTests(unittest.TestCase):
    def test_mangle_byte(self):
        self.assertEqual(mangle_byte(0x38), 0xE3)

    def test_timing_uses_sixteen_microsecond_ticks(self):
        self.assertEqual(encode_timing(560), [35])
        self.assertEqual(encode_timing(1690), [106])

    def test_long_timing_is_raw_microsecond_varint(self):
        # Matches the vendor app: above 2032 us the value is a 7-bit varint of
        # microseconds, not ticks. 4000 us -> 0xFA0 -> [0xA0, 0x1F].
        self.assertEqual(encode_timing(4000), [0xA0, 0x1F])
        self.assertEqual(encode_timing(2032), [127])
        self.assertEqual(encode_timing(2033), [0xF1, 0x0F])
        self.assertEqual(encode_timing(2303), [0xFF, 0x11])
        self.assertEqual(encode_timing(2303, escape_ff=True), [0xFE, 0x11])

    def test_compression_has_dictionary_separator(self):
        compressed = compress_pulses([560, 560, 560, 1690] * 4)
        self.assertIn(bytes((0xFF, 0xFF, 0xFF)), compressed)

    def test_decode_learned_overflow(self):
        self.assertEqual(decode_learned_signal(bytes((0xFF, 0x02))), [4112])

    def test_d552_frames_begin_with_transmit_preamble(self):
        frames = encode_d552(38_000, [9000, 4500, 560, 560] * 8)
        self.assertEqual(frames[0][:4], bytes((0xFF,) * 4))
        self.assertTrue(all(len(frame) <= 63 for frame in frames))

    def test_rca_encodes_address_command_and_inverses_lsb_first(self):
        timings = encode_rca(0x0F, 0x74)
        self.assertEqual(timings[:2], [4000, 4000])
        self.assertEqual(timings[-2:], [500, 8000])
        spaces = timings[3:-2:2]
        decoded = sum((space == 2000) << bit for bit, space in enumerate(spaces))
        self.assertEqual(decoded, 0x8B074F)

    def test_rca_rejects_out_of_range_values(self):
        with self.assertRaises(ValueError):
            encode_rca(0x10, 0x54)
        with self.assertRaises(ValueError):
            encode_rca(0x0F, 0x100)


if __name__ == "__main__":
    unittest.main()
