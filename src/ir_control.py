"""Learn and transmit IR signals with an ElkSmart/Ocrustar D552 blaster.

The USB framing and pulse compression are based on the independently
reverse-engineered Ocrustar protocol documented by deadboy18/Ocrustar-USB-IR.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import usb.core
import usb.util

from ir_probe import bulk_endpoints, find_device, handshake


DEVICE_D552_SIGNATURE = bytes((0x70, 0x01))
RCA_FREQUENCY_HZ = 38_000
LEARN_ON = bytes((0xFE,) * 4)
LEARN_OFF = bytes((0xFD,) * 4)
TRANSMIT_PREAMBLE = bytes((0xFF,) * 4)


def mangle_byte(value: int) -> int:
    value &= 0xFF
    reversed_bits = 0
    for _bit in range(8):
        reversed_bits = (reversed_bits << 1) | (value & 1)
        value >>= 1
    return (~reversed_bits) & 0xFF


def encode_timing(value_us: int, escape_ff: bool = False) -> list[int]:
    """Encode one mark/space duration the way the vendor app does.

    Durations up to 2032 us become a single byte of 16 us ticks. Longer
    durations are sent as a little-endian 7-bit varint of the raw microsecond
    value (not ticks). The vendor app only replaces a 0xFF byte with 0xFE in
    the two header pairs, which ``escape_ff`` reproduces.
    """
    if value_us <= 1:
        return [max(value_us, 0)]
    if value_us <= 2032:
        return [int(value_us / 16.0 + 0.5)]

    encoded = []
    remaining = value_us
    while True:
        value = remaining & 0x7F
        remaining >>= 7
        if remaining:
            value |= 0x80
        if escape_ff and value == 0xFF:
            value = 0xFE
        encoded.append(value)
        if not remaining:
            return encoded


def compress_pulses(timings: list[int]) -> bytes:
    if not timings:
        raise ValueError("an IR signal must contain timing values")
    if len(timings) % 2:
        timings = [*timings, 10_000]

    pairs = [
        (max(timings[index], 0), max(timings[index + 1], 0))
        for index in range(0, len(timings), 2)
    ]
    common = [pair for pair, _count in Counter(pairs).most_common(2)]
    if len(common) == 1:
        common.append(common[0])
    common.sort(key=lambda pair: pair[0] + pair[1])
    short_pair, long_pair = common

    output: list[int] = []
    for value in (*long_pair, *short_pair):
        output.extend(encode_timing(value, escape_ff=True))
    output.extend((0xFF, 0xFF, 0xFF))

    for pair in pairs:
        if pair == short_pair:
            output.append(0x00)
        elif pair == long_pair:
            output.append(0x01)
        else:
            output.extend(encode_timing(pair[0]))
            output.extend(encode_timing(pair[1]))
    return bytes(output)


def frame_checksum(frame: bytes | bytearray) -> int:
    checksum = sum(frame[:62])
    raw = (checksum & 0xF0) | ((checksum >> 8) & 0x0F)
    return mangle_byte(raw)


def encode_d552(frequency_hz: int, timings: list[int]) -> list[bytes]:
    if not 20_000 <= frequency_hz <= 100_000:
        raise ValueError("carrier frequency must be between 20000 and 100000 Hz")
    if any(value < 0 for value in timings):
        raise ValueError("timings must be positive mark/space durations")
    if sum(timings) >= 1_000_000:
        raise ValueError("IR signal duration must be less than one second")

    payload = compress_pulses(timings)
    encoded_frequency = frequency_hz + 0x7FFFF
    message = bytearray(TRANSMIT_PREAMBLE)
    message.extend(
        (
            mangle_byte(encoded_frequency >> 8),
            mangle_byte(encoded_frequency >> 16),
            mangle_byte(encoded_frequency),
            mangle_byte(len(payload) >> 8),
            mangle_byte(len(payload)),
        )
    )
    message.extend(payload)

    frames = []
    for offset in range(0, len(message), 62):
        frame = bytearray(message[offset : offset + 62])
        if len(frame) == 62:
            frame.append(frame_checksum(frame))
        frames.append(bytes(frame))
    return frames


def decode_learned_signal(payload: bytes) -> list[int]:
    timings = []
    carry = 0
    for value in payload:
        if value == 0xFF:
            carry += 0xFF0
        else:
            timings.append(value * 16 + carry)
            carry = 0
    return timings


def encode_rca(address: int, command: int) -> list[int]:
    """Encode a 24-bit RCA command as alternating mark/space timings."""
    if not 0 <= address <= 0x0F:
        raise ValueError("RCA address must be between 0x0 and 0xF")
    if not 0 <= command <= 0xFF:
        raise ValueError("RCA command must be between 0x00 and 0xFF")

    data = address
    data |= command << 4
    data |= ((~address) & 0x0F) << 12
    data |= ((~command) & 0xFF) << 16

    timings = [4_000, 4_000]
    for bit in range(24):
        timings.extend((500, 2_000 if data & (1 << bit) else 1_000))
    timings.extend((500, 8_000))
    return timings


def is_usb_timeout(error: usb.core.USBError) -> bool:
    # 10060 is WSAETIMEDOUT, surfaced by libusb on Windows/WinUSB.
    return error.errno in (60, 110, 116, 10060) or getattr(error, "backend_error_code", None) == -7


def read_packet(endpoint_in, timeout: int) -> bytes | None:
    try:
        return bytes(endpoint_in.read(16_384, timeout=timeout))
    except usb.core.USBError as error:
        if is_usb_timeout(error):
            return None
        raise


def learn(endpoint_in, endpoint_out, timeout_seconds: int) -> list[int]:
    endpoint_out.write(LEARN_ON, timeout=500)
    deadline = time.monotonic() + timeout_seconds
    expected_length = None
    payload = bytearray()
    try:
        while time.monotonic() < deadline:
            packet = read_packet(endpoint_in, 300)
            if not packet:
                continue
            if expected_length is None:
                if len(packet) < 6 or packet[:4] != LEARN_ON:
                    print(f"Ignoring unexpected USB packet: {packet.hex(' ').upper()}")
                    continue
                expected_length = int.from_bytes(packet[4:6], "big")
                payload.extend(packet[6:])
            else:
                payload.extend(packet)
            if expected_length is not None and len(payload) >= expected_length:
                return decode_learned_signal(bytes(payload[:expected_length]))
    finally:
        endpoint_out.write(LEARN_OFF, timeout=500)
    raise TimeoutError("no IR signal was learned before the timeout")


def transmit(endpoint_in, endpoint_out, frequency_hz: int, timings: list[int]) -> bytes:
    frames = encode_d552(frequency_hz, timings)
    for frame in frames:
        written = endpoint_out.write(frame, timeout=500)
        if written != len(frame):
            raise RuntimeError(f"short USB write: {written}/{len(frame)} bytes")
        time.sleep(0.002)

    time.sleep(0.05)
    acknowledgement = read_packet(endpoint_in, 500)
    if acknowledgement is None:
        raise RuntimeError("IR blaster did not acknowledge the transmit command")
    if acknowledgement[:4] != TRANSMIT_PREAMBLE:
        raise RuntimeError(f"unexpected transmit response: {acknowledgement.hex(' ')}")
    return acknowledgement


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--learn", metavar="OUTPUT.json", type=Path)
    actions.add_argument("--send", metavar="INPUT.json", type=Path)
    actions.add_argument(
        "--send-rca",
        nargs=2,
        metavar=("ADDRESS", "COMMAND"),
        type=lambda value: int(value, 0),
        help="send a 24-bit RCA command, for example: --send-rca 0x0F 0x74",
    )
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    device = find_device(0x0195)
    if device is None:
        print("IR blaster 045C:0195 was not found", file=sys.stderr)
        return 1

    try:
        endpoint_in, endpoint_out = bulk_endpoints(device)
        response = handshake(endpoint_in, endpoint_out)
        if response[4:6] != DEVICE_D552_SIGNATURE:
            signature = response[4:6].hex().upper()
            raise RuntimeError(f"expected D552 signature 7001, received {signature}")

        if args.learn:
            print("Learning: point the remote at the dongle and press one button...")
            timings = learn(endpoint_in, endpoint_out, args.timeout)
            document = {"frequency_hz": 38_000, "timings_us": timings}
            args.learn.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            print(f"Learned {len(timings)} timings and saved {args.learn}")
        elif args.send:
            document = json.loads(args.send.read_text(encoding="utf-8"))
            frequency_hz = int(document["frequency_hz"])
            timings = [int(value) for value in document["timings_us"]]
            acknowledgement = transmit(endpoint_in, endpoint_out, frequency_hz, timings)
            print(
                f"Transmitted {len(timings)} timings at {frequency_hz} Hz; "
                f"device acknowledgement={acknowledgement.hex(' ').upper()}"
            )
        else:
            address, command = args.send_rca
            timings = encode_rca(address, command)
            acknowledgement = transmit(endpoint_in, endpoint_out, RCA_FREQUENCY_HZ, timings)
            print(
                f"Transmitted RCA address=0x{address:02X} command=0x{command:02X} "
                f"at {RCA_FREQUENCY_HZ} Hz; "
                f"device acknowledgement={acknowledgement.hex(' ').upper()}"
            )
    except (OSError, RuntimeError, TimeoutError, ValueError, usb.core.USBError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    finally:
        usb.util.dispose_resources(device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
