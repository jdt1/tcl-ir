"""Ask Windows to turn the interactive session display output back on."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from pathlib import Path
import subprocess
import sys
import time


ES_DISPLAY_REQUIRED = 0x00000002
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
SMTO_ABORTIFHUNG = 0x0002
DISPLAY_DEVICE_ACTIVE = 0x00000001
TCL_MONITOR_PREFIX = "TCL2875"
CREATE_NO_WINDOW = 0x08000000


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


def wake_display() -> None:
    """Reset the display idle timer and broadcast the monitor-on command."""
    if sys.platform != "win32":
        raise RuntimeError("display wake requires Windows")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.SetThreadExecutionState.argtypes = [wt.DWORD]
    kernel32.SetThreadExecutionState.restype = wt.DWORD
    user32.SendMessageTimeoutW.argtypes = [
        wt.HWND,
        wt.UINT,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
        wt.UINT,
        wt.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_size_t

    if not kernel32.SetThreadExecutionState(ES_DISPLAY_REQUIRED):
        raise ctypes.WinError(ctypes.get_last_error())


def tcl_display_is_active() -> bool:
    """Return whether the TCL monitor is attached to an active desktop path."""
    if sys.platform != "win32":
        raise RuntimeError("display status requires Windows")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayDevicesW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DISPLAY_DEVICE), wt.DWORD]
    user32.EnumDisplayDevicesW.restype = wt.BOOL

    adapter_index = 0
    while True:
        adapter = DISPLAY_DEVICE(cb=ctypes.sizeof(DISPLAY_DEVICE))
        if not user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        if not adapter.StateFlags & DISPLAY_DEVICE_ACTIVE:
            continue

        monitor_index = 0
        while True:
            monitor = DISPLAY_DEVICE(cb=ctypes.sizeof(DISPLAY_DEVICE))
            if not user32.EnumDisplayDevicesW(adapter.DeviceName, monitor_index, ctypes.byref(monitor), 0):
                break
            monitor_index += 1
            if TCL_MONITOR_PREFIX in monitor.DeviceID.upper():
                return True
    return False


def restart_tcl_monitor() -> str:
    """Restart exactly one present TCL2875 monitor device using PnPUtil."""
    if sys.platform != "win32":
        raise RuntimeError("monitor restart requires Windows")

    script = (
        "$devices = @(Get-PnpDevice -Class Monitor -PresentOnly | "
        "Where-Object InstanceId -Like 'DISPLAY\\TCL2875*'); "
        "if ($devices.Count -ne 1) { "
        "Write-Error ('Expected one present TCL2875 monitor, found ' + $devices.Count); exit 2 }; "
        "& pnputil.exe /restart-device $devices[0].InstanceId; exit $LASTEXITCODE"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode:
        raise RuntimeError(f"TCL monitor restart exited {result.returncode}: {output or 'no output'}")
    return output or "TCL monitor restarted"


def recover_display(settle_seconds: float = 1.0) -> str:
    """Wake the display, restarting only TCL2875 if its desktop path stays inactive."""
    wake_display()
    time.sleep(settle_seconds)
    if tcl_display_is_active():
        return "TCL display already active after Windows display-on request"

    restart_output = restart_tcl_monitor()
    time.sleep(settle_seconds)
    wake_display()
    if not tcl_display_is_active():
        raise RuntimeError(f"TCL display remains inactive after restart: {restart_output}")
    return f"TCL display reactivated: {restart_output}"

    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(
        wt.HWND(HWND_BROADCAST),
        WM_SYSCOMMAND,
        SC_MONITORPOWER,
        -1,
        SMTO_ABORTIFHUNG,
        2000,
        ctypes.byref(result),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def main() -> int:
    try:
        result = recover_display()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
