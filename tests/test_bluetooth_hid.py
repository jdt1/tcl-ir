import asyncio
from collections import defaultdict
import logging
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.bluetooth_hid import (
    BluetoothRemote, KEYS, REPORT_MAP, key_report, respond_read, select_client, validate_send,
)


def client(device_id):
    return NS(session=NS(device_id=NS(id=device_id)))


class HidProtocolTests(unittest.TestCase):
    def test_explicit_power_commands_do_not_encode_toggle(self):
        self.assertEqual(key_report("on"), (2, b"\x02"))
        self.assertEqual(key_report("off"), (2, b"\x01"))
        self.assertEqual(key_report("power"), (1, b"\x30\x00"))

    def test_hid_usages_are_not_android_keycode_numbers(self):
        self.assertEqual(key_report("brightness+"), (1, b"\x6f\x00"))
        self.assertEqual(key_report("brightness-"), (1, b"\x70\x00"))
        self.assertEqual(key_report("vol-"), (1, b"\xea\x00"))
        self.assertEqual(key_report("home"), (1, b"\x23\x02"))

    def test_descriptor_input_lengths_match_encoded_reports_without_id_prefix(self):
        # Decode HID short items independently of the report encoder.
        bits = defaultdict(int)
        report_id = size = count = 0
        i = 0
        while i < len(REPORT_MAP):
            prefix = REPORT_MAP[i]
            length = (0, 1, 2, 4)[prefix & 3]
            value = int.from_bytes(REPORT_MAP[i + 1:i + 1 + length], "little")
            tag = prefix & 0xFC
            if tag == 0x84:
                report_id = value
            elif tag == 0x74:
                size = value
            elif tag == 0x94:
                count = value
            elif tag == 0x80:
                bits[report_id] += size * count
            i += 1 + length
        self.assertEqual(dict(bits), {1: 16, 2: 8, 3: 64})
        for name in KEYS:
            report_id, data = key_report(name)
            self.assertEqual(len(data) * 8, bits[report_id], name)

    def test_report_map_reads_honor_att_offset_and_length(self):
        request = NS(offset=22, length=22, respond_with_value=Mock(), respond_with_protocol_error=Mock())
        respond_read(request, REPORT_MAP)
        request.respond_with_value.assert_called_once_with(REPORT_MAP[22:44])
        request.offset = len(REPORT_MAP) + 1
        respond_read(request, REPORT_MAP)
        request.respond_with_protocol_error.assert_called_once_with(7)

    def test_ambiguous_or_missing_target_never_broadcasts(self):
        tv, phone = client("tv"), client("phone")
        self.assertIs(select_client([tv, phone], "tv"), tv)
        for clients, target in (([], None), ([tv, phone], None), ([phone], "tv")):
            with self.assertRaises(RuntimeError):
                select_client(clients, target)

    def test_invalid_sequences_and_repeated_power_rejected(self):
        for keys, repeat, gap in ((["on"], 2, .25), (["off"], 2, .25),
                                  (["power"], 2, .25), (["vol-"], 1, float("nan")),
                                  (["vol-"], 0, .25), (["vol-", "unknown"], 1, .25)):
            with self.assertRaises(ValueError):
                validate_send(keys, repeat, gap)


class BluetoothSendTests(unittest.IsolatedAsyncioTestCase):
    def make_remote(self, status=0):
        remote = BluetoothRemote.__new__(BluetoothRemote)
        remote.logger = logging.getLogger("test")
        remote.lock = asyncio.Lock()
        remote.buffer = bytes
        remote.values = {1: b"\0\0", 2: b"\0", 3: bytes(8)}
        remote.ConnectionStatus = NS(CONNECTED=1)
        remote.gatt = NS(GattCommunicationStatus=NS(SUCCESS=0))
        device = NS(connection_status=1, close=Mock())
        remote.Device = NS(from_id_async=AsyncMock(return_value=device))
        remote.reports = {
            n: NS(subscribed_clients=[client("tv")], notify_value_async=AsyncMock(return_value=NS(status=status)))
            for n in (1, 2, 3)
        }
        return remote, device

    async def test_press_then_release_sent_to_only_selected_client(self):
        remote, device = self.make_remote()
        with patch("src.bluetooth_hid.asyncio.sleep", new_callable=AsyncMock):
            result = await remote.send(["vol-"])
        notifications = remote.reports[1].notify_value_async.call_args_list
        self.assertEqual([call.args[0] for call in notifications], [b"\xea\0", b"\0\0"])
        self.assertTrue(all(call.args[1] is remote.reports[1].subscribed_clients[0] for call in notifications))
        self.assertEqual(result["tv_effect"], "unverified")
        self.assertEqual(remote.values[1], b"\0\0")
        device.close.assert_called_once()

    async def test_failed_press_still_releases_and_never_retries(self):
        remote, device = self.make_remote()
        remote.reports[1].notify_value_async.side_effect = [OSError("link dropped"), NS(status=0)]
        with self.assertRaises(OSError):
            await remote.send(["power"])
        self.assertEqual(remote.reports[1].notify_value_async.await_count, 2)
        self.assertEqual(remote.reports[1].notify_value_async.call_args.args[0], b"\0\0")
        device.close.assert_called_once()

    async def test_disconnected_link_cannot_report_success(self):
        remote, device = self.make_remote()
        device.connection_status = 0
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await remote.send(["on"])
        remote.reports[2].notify_value_async.assert_not_called()

    async def test_all_report_targets_checked_before_first_press(self):
        remote, _ = self.make_remote()
        remote.reports[2].subscribed_clients = []
        with self.assertRaises(RuntimeError):
            await remote.send(["vol-", "on"])
        remote.reports[1].notify_value_async.assert_not_called()


if __name__ == "__main__":
    unittest.main()
