"""Plan, apply, verify, or undo the Windows Bluetooth Class of Device override.

Default: read-only plan. --apply and --restore require an elevated Python.
This changes system-wide Classic Bluetooth identity, not BLE GAP Appearance or
the supported profiles. It never restarts Bluetooth or reboots the PC.

    python tools/bluetooth_identity.py keyboard
    python tools/bluetooth_identity.py keyboard --apply
    # After a separately arranged Bluetooth restart/reboot:
    python tools/bluetooth_identity.py keyboard --verify
    python tools/bluetooth_identity.py --restore
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REGISTRY_PATH = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters"
NAMES = ("COD Major", "COD Type")
# Unshifted field values from Microsoft's bthdef.h. Keyboard 0x10 << 2
# becomes CoD bit 6; remote-control category 0x03 << 2 becomes bits 2/3.
PROFILES = {"keyboard": {"COD Major": 5, "COD Type": 0x10},
            "remote": {"COD Major": 5, "COD Type": 0x03}}
BACKUP = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "tcl-ir" / "bluetooth-identity-backup.json"


class Registry:
    def __init__(self):
        import winreg
        self.api = winreg

    def read(self) -> dict:
        reg = self.api
        values = {}
        with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, REGISTRY_PATH, 0, reg.KEY_QUERY_VALUE) as key:
            for name in NAMES:
                try:
                    value, kind = reg.QueryValueEx(key, name)
                except FileNotFoundError:
                    value, kind = None, reg.REG_DWORD
                if kind != reg.REG_DWORD:
                    raise RuntimeError(f"{name} has an unexpected registry type; no changes made")
                values[name] = value
        return values

    def write(self, values: dict):
        validate_values(values)
        reg = self.api
        with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, REGISTRY_PATH, 0, reg.KEY_SET_VALUE) as key:
            for name in NAMES:
                if values[name] is None:
                    try:
                        reg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                else:
                    reg.SetValueEx(key, name, 0, reg.REG_DWORD, values[name])


def validate_values(values):
    if not isinstance(values, dict) or set(values) != set(NAMES):
        raise ValueError("backup must contain only COD Major and COD Type")
    if any(value is not None and (type(value) is not int or not 0 <= value <= 0xFFFFFFFF)
           for value in values.values()):
        raise ValueError("backup values must be DWORD integers or null for an absent value")


def load_backup(path: Path) -> dict:
    backup = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(backup, dict) or backup.get("version") != 1 or backup.get("computer") != platform.node():
        raise ValueError("backup belongs to another computer or format")
    validate_values(backup.get("original"))
    return backup["original"]


def write_checked(store, desired, before):
    try:
        store.write(desired)
        if store.read() != desired:
            raise RuntimeError("registry write readback did not match")
    except Exception:
        # A failure between the two DWORD writes must not leave a partial change.
        store.write(before)
        raise


def apply_identity(store, path: Path, identity: str):
    before = store.read()
    if path.exists():
        original = load_backup(path)
        if before != original and before not in PROFILES.values():
            raise RuntimeError("registry changed outside this tool; refusing to overwrite it")
    else:
        validate_values(before)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite the original snapshot on a repeated apply.
        with path.open("x", encoding="utf-8") as file:
            json.dump({"version": 1, "computer": platform.node(), "original": before}, file, indent=2)
    write_checked(store, PROFILES[identity], before)


def restore_identity(store, path: Path):
    original = load_backup(path)
    before = store.read()
    if before != original and before not in PROFILES.values():
        raise RuntimeError("registry changed outside this tool; refusing to overwrite it")
    write_checked(store, original, before)
    # Retain the original backup for inspection and idempotent restoration.


async def verify_identity(identity):
    from src.bluetooth_hid import BluetoothRemote
    result = await BluetoothRemote(logging.getLogger("identity")).probe()
    actual = result.get("classic_device_class")
    expected = (PROFILES[identity]["COD Major"] << 8) | (PROFILES[identity]["COD Type"] << 2)
    result["expected_device_bits"] = f"0x{expected:04X}"
    result["matches"] = actual is not None and (int(actual, 16) & 0x1FFC) == expected
    result["note"] = "Classic identity only; TV discovery, BLE Appearance and pairing still require separate tests"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("identity", choices=PROFILES, nargs="?", default="keyboard")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="back up and write the two DWORDs; no restart")
    actions.add_argument("--restore", action="store_true", help="restore the exact original values; no restart")
    actions.add_argument("--verify", action="store_true", help="read the live radio identity and compare it")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "win32":
            raise RuntimeError("requires Windows")
        if args.verify:
            result = asyncio.run(verify_identity(args.identity))
            print(json.dumps(result, indent=2))
            return 0 if result["matches"] else 2
        if (args.apply or args.restore) and not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("apply/restore requires an elevated Python process; nothing changed")
        store = Registry()
        if args.apply:
            apply_identity(store, BACKUP, args.identity)
        elif args.restore:
            restore_identity(store, BACKUP)
        result = {"action": "applied" if args.apply else "restored" if args.restore else "read-only plan",
                  "registry": "HKLM\\" + REGISTRY_PATH, "current_values": store.read(),
                  "backup": str(BACKUP), "desired_values": PROFILES[args.identity],
                  "restart_performed": False,
                  "note": "System-wide Classic identity override. Bluetooth restart/reboot may be needed; "
                          "this disconnects Bluetooth keyboards/headphones. BLE Appearance is unchanged."}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
