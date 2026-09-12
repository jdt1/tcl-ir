"""Send TCL 43C61B remote keys through the Ocrustar USB IR blaster.

The pulse data in codes/tcl_3407.json is an exact copy of what the Ocrustar
Android app transmits for its "TV-TCL 3407" profile, captured from the app's
own log output while it drove this same dongle.

Examples (run from the repo root with the virtual environment active):

    python tv.py vol+
    python tv.py vol- --repeat 3
    python tv.py power
    python tv.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import usb.core  # noqa: E402
import usb.util  # noqa: E402

from ir_control import DEVICE_D552_SIGNATURE, encode_rca, transmit  # noqa: E402
from ir_probe import bulk_endpoints, find_device, handshake  # noqa: E402

CODES_FILE = Path(__file__).resolve().parent / "codes" / "tcl_3407.json"
RCA_ADDRESS = 0x0F

# Extra keys not in the app profile, taken from the Flipper-IRDB TCL Google TV
# definitions that share address 0x0F (65C635K, 43S446, 40S615). These are
# generated with encode_rca instead of replayed, so they are candidates until
# seen working on the 43C61B.
EXTRA_RCA_KEYS = {
    "settings": 0xF3,
    "exit": 0x60,
    "guide": 0x58,
    "list": 0x86,
    "apps": 0x37,
    "play_pause": 0xA8,
    "pause": 0x98,
    "stop": 0xF8,
    "rewind": 0xB8,
    "forward": 0x38,
    "netflix": 0xF7,
    "youtube": 0x47,
    "red": 0x00,
    "green": 0x17,
    "yellow": 0x27,
    "blue": 0x1B,
    "0": 0x0C,
    "1": 0x8C,
    "2": 0x4C,
    "3": 0xCC,
    "4": 0x2C,
    "5": 0xAC,
    "6": 0x6C,
    "7": 0xEC,
    "8": 0x1C,
    "9": 0x9C,
}

ALIASES = {
    "vol+": "vol_up",
    "volup": "vol_up",
    "vol-": "vol_down",
    "voldown": "vol_down",
    "ch+": "ch_up",
    "chup": "ch_up",
    "ch-": "ch_down",
    "chdown": "ch_down",
    "source": "input",
    "enter": "ok",
    "select": "ok",
    "info": "display",
    # On this Google TV the profile's "menu" (0x10) is Home and "last" (0xE4) is Back.
    "home": "menu",
    "back": "last",
    "return": "last",
    "play": "play_pause",
}


def load_codes() -> dict:
    codes = json.loads(CODES_FILE.read_text(encoding="utf-8"))
    for name, command in EXTRA_RCA_KEYS.items():
        codes["keys"].setdefault(
            name,
            {
                "rca_command": f"0x{command:02X}",
                "frames": 1,
                "timings_us": encode_rca(RCA_ADDRESS, command),
                "candidate": True,
            },
        )
    return codes


def resolve_key(codes: dict, name: str) -> str:
    key = name.strip().lower()
    key = ALIASES.get(key, key.replace("-", "_"))
    if key not in codes["keys"]:
        known = ", ".join(sorted(codes["keys"]))
        raise ValueError(f"unknown key '{name}'. Known keys: {known}")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keys", nargs="*", help="one or more keys to send, e.g. vol+ vol- power mute")
    parser.add_argument("--repeat", type=int, default=1, help="send each key this many times (default 1)")
    parser.add_argument("--gap", type=float, default=0.25, help="seconds between sends (default 0.25)")
    parser.add_argument("--list", action="store_true", help="list the available keys and exit")
    args = parser.parse_args()

    codes = load_codes()
    if args.list:
        for name, entry in codes["keys"].items():
            note = "  (candidate from IRDB, not yet verified)" if entry.get("candidate") else ""
            print(f"{name:10} RCA {entry['rca_command']}  {entry['frames']} frame(s){note}")
        return 0
    if not args.keys:
        parser.error("give at least one key, or --list")

    try:
        names = [resolve_key(codes, key) for key in args.keys]
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    device = find_device(0x0195)
    if device is None:
        print("IR blaster 045C:0195 was not found; plug it into this PC and retry", file=sys.stderr)
        return 1

    try:
        endpoint_in, endpoint_out = bulk_endpoints(device)
        response = handshake(endpoint_in, endpoint_out)
        if response[4:6] != DEVICE_D552_SIGNATURE:
            raise RuntimeError(f"expected D552 signature 7001, received {response[4:6].hex().upper()}")

        frequency = int(codes["frequency_hz"])
        for name in names:
            timings = [int(value) for value in codes["keys"][name]["timings_us"]]
            for attempt in range(args.repeat):
                acknowledgement = transmit(endpoint_in, endpoint_out, frequency, timings)
                print(
                    f"sent {name} ({codes['keys'][name]['rca_command']}, "
                    f"{len(timings)} timings) ack={acknowledgement[:4].hex().upper()}"
                )
                if attempt + 1 < args.repeat or name is not names[-1]:
                    time.sleep(args.gap)
    except (OSError, RuntimeError, TimeoutError, ValueError, usb.core.USBError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    finally:
        usb.util.dispose_resources(device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
