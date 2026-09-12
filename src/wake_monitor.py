"""Wake the TCL TV when Windows turns the interactive session display back on.

The monitor is event-driven: it registers a hidden window for
GUID_SESSION_DISPLAY_STATUS and waits for WM_POWERBROADCAST messages.  It does
not capture keys or mouse movements.  GetLastInputInfo is queried only when a
display-on event arrives, to reject automatic/background display wakes.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from display_wake import recover_display


DISPLAY_OFF = 0
DISPLAY_ON = 1
DISPLAY_DIMMED = 2

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000

GUID_SESSION_DISPLAY_STATUS = "2b84c20e-ad23-4ddf-93db-05ffbd7efca5"
MUTEX_NAME = "Local\\tcl-ir-wake-monitor"


class WakeController:
    """Pure state machine for deciding whether a display-on event may send IR."""

    def __init__(self, min_off_seconds: float, recent_input_seconds: float, cooldown_seconds: float):
        if min_off_seconds < 0 or recent_input_seconds < 0 or cooldown_seconds < 0:
            raise ValueError("timing values must be non-negative")
        self.min_off_seconds = min_off_seconds
        self.recent_input_seconds = recent_input_seconds
        self.cooldown_seconds = cooldown_seconds
        self.off_since: float | None = None
        self.last_trigger: float | None = None

    def display_changed(self, state: int, now: float, input_age: float | None) -> tuple[bool, str]:
        if state == DISPLAY_OFF:
            if self.off_since is None:
                self.off_since = now
                return False, "armed by display-off"
            return False, "duplicate display-off"

        if state == DISPLAY_DIMMED:
            return False, "display dimmed"

        if state != DISPLAY_ON:
            return False, f"unknown display state {state}"

        if self.off_since is None:
            return False, "display-on without a preceding display-off"

        off_seconds = max(0.0, now - self.off_since)
        self.off_since = None

        if off_seconds < self.min_off_seconds:
            return False, f"display was off for only {off_seconds:.1f}s"

        if input_age is None:
            return False, "last-input time unavailable"
        if input_age > self.recent_input_seconds:
            return False, f"last input was {input_age:.1f}s ago"

        if self.last_trigger is not None:
            cooldown_left = self.cooldown_seconds - (now - self.last_trigger)
            if cooldown_left > 0:
                return False, f"cooldown has {cooldown_left:.1f}s remaining"

        # Record before attempting USB transmission. A failed attempt must not
        # become a loop of power-toggle retries.
        self.last_trigger = now
        return True, f"display returned after {off_seconds:.1f}s; input age {input_age:.1f}s"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", GUID),
        ("DataLength", wt.DWORD),
        ("Data", ctypes.c_ubyte * 1),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def default_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "tcl-ir" / "wake-monitor.log"


def configure_logging(path: Path, verbose: bool) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tcl_ir.wake_monitor")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger


def last_input_age_seconds(user32: ctypes.WinDLL, kernel32: ctypes.WinDLL) -> float | None:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    # LASTINPUTINFO and GetTickCount share a wrapping 32-bit tick domain.
    elapsed_ms = (int(kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def send_power(repo_root: Path, logger: logging.Logger) -> bool:
    python_exe = Path(sys.executable).with_name("python.exe")
    if not python_exe.exists():
        python_exe = Path(sys.executable)
    command = [str(python_exe), str(repo_root / "tv.py"), "power"]
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.error("power command failed to start: %s", error)
        return False

    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode == 0:
        logger.info("power command succeeded: %s", output or "no output")
        return True
    logger.error("power command exited %d: %s", result.returncode, output or "no output")
    return False


def display_state_from_message(lparam: int, expected_guid: GUID) -> int | None:
    if not lparam:
        return None
    setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
    if setting.PowerSetting != expected_guid or setting.DataLength < ctypes.sizeof(wt.DWORD):
        return None
    data_address = lparam + POWERBROADCAST_SETTING.Data.offset
    return int.from_bytes(ctypes.string_at(data_address, ctypes.sizeof(wt.DWORD)), "little")


def run_monitor(args: argparse.Namespace, logger: logging.Logger) -> int:
    if sys.platform != "win32":
        logger.error("wake-monitor requires Windows")
        return 1

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HANDLE),
            ("hIcon", wt.HANDLE),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HANDLE),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]

    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wt.DWORD

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [
        wt.DWORD,
        wt.LPCWSTR,
        wt.LPCWSTR,
        wt.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wt.HWND,
        wt.HANDLE,
        wt.HANDLE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterPowerSettingNotification.argtypes = [wt.HANDLE, ctypes.POINTER(GUID), wt.DWORD]
    user32.RegisterPowerSettingNotification.restype = wt.HANDLE
    user32.UnregisterPowerSettingNotification.argtypes = [wt.HANDLE]
    user32.UnregisterPowerSettingNotification.restype = wt.BOOL
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wt.BOOL
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyWindow.restype = wt.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.TranslateMessage.restype = wt.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT

    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        logger.error("could not create single-instance mutex (error %d)", ctypes.get_last_error())
        return 1
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        logger.error("another wake-monitor instance is already running")
        kernel32.CloseHandle(mutex)
        return 2

    controller = WakeController(args.min_off_seconds, args.recent_input_seconds, args.cooldown_seconds)
    target_guid = GUID.parse(GUID_SESSION_DISPLAY_STATUS)
    repo_root = Path(__file__).resolve().parents[1]
    hwnd_holder: list[int] = []
    recovery_lock = threading.Lock()
    last_recovery_at = float("-inf")

    def handle_wake(trigger_ir: bool, reason: str) -> None:
        try:
            if trigger_ir:
                if args.dry_run:
                    logger.warning("DRY RUN: would send TCL power")
                elif send_power(repo_root, logger):
                    logger.info("waiting %.1fs for TV startup", args.tv_startup_seconds)
                    time.sleep(args.tv_startup_seconds)
            try:
                result = recover_display()
                logger.info("display recovery succeeded: %s", result)
            except (OSError, RuntimeError):
                logger.exception("display recovery failed")
        finally:
            recovery_lock.release()

    @WNDPROC
    def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
        nonlocal last_recovery_at
        try:
            if message == WM_POWERBROADCAST:
                if wparam == PBT_POWERSETTINGCHANGE:
                    state = display_state_from_message(lparam, target_guid)
                    if state is not None:
                        input_age = last_input_age_seconds(user32, kernel32) if state == DISPLAY_ON else None
                        had_display_off = state == DISPLAY_ON and controller.off_since is not None
                        now = time.monotonic()
                        trigger, reason = controller.display_changed(state, now, input_age)
                        state_name = {DISPLAY_OFF: "off", DISPLAY_ON: "on", DISPLAY_DIMMED: "dimmed"}.get(
                            state, str(state)
                        )
                        logger.info("display=%s action=%s reason=%s", state_name, "trigger" if trigger else "none", reason)
                        recent_input_wake = (
                            had_display_off
                            and input_age is not None
                            and input_age <= args.recent_input_seconds
                        )
                        recovery_cooled_down = now - last_recovery_at >= args.recovery_cooldown_seconds
                        if recent_input_wake and recovery_cooled_down and recovery_lock.acquire(blocking=False):
                            last_recovery_at = now
                            threading.Thread(
                                target=handle_wake,
                                args=(trigger, reason),
                                name="tcl-ir-display-recovery",
                                daemon=True,
                            ).start()
                        elif recent_input_wake and not recovery_cooled_down:
                            logger.info("display recovery suppressed by %.1fs cooldown", args.recovery_cooldown_seconds)
                        elif recent_input_wake:
                            logger.info("display recovery already in progress")
                    return 1
                if wparam == PBT_APMRESUMEAUTOMATIC:
                    logger.info("system resume: automatic")
                    return 1
                if wparam == PBT_APMRESUMESUSPEND:
                    logger.info("system resume: user initiated")
                    return 1
            elif message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            logger.exception("unhandled window-message error")
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    hinstance = kernel32.GetModuleHandleW(None)
    class_name = f"TclIrWakeMonitor-{os.getpid()}"
    window_class = WNDCLASSW(lpfnWndProc=window_proc, hInstance=hinstance, lpszClassName=class_name)
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        error = ctypes.get_last_error()
        logger.error("RegisterClassW failed (error %d)", error)
        kernel32.CloseHandle(mutex)
        return 1

    hwnd = user32.CreateWindowExW(0, class_name, "TCL IR Wake Monitor", 0, 0, 0, 0, 0, None, None, hinstance, None)
    if not hwnd:
        error = ctypes.get_last_error()
        logger.error("CreateWindowExW failed (error %d)", error)
        kernel32.CloseHandle(mutex)
        return 1
    hwnd_holder.append(hwnd)

    notification = user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(target_guid), DEVICE_NOTIFY_WINDOW_HANDLE)
    if not notification:
        error = ctypes.get_last_error()
        logger.error("RegisterPowerSettingNotification failed (error %d)", error)
        user32.DestroyWindow(hwnd)
        kernel32.CloseHandle(mutex)
        return 1

    def request_stop(_signum: int, _frame: object) -> None:
        if hwnd_holder:
            user32.PostMessageW(hwnd_holder[0], WM_CLOSE, 0, 0)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    logger.info(
        "started mode=%s min_off=%.1fs recent_input=%.1fs cooldown=%.1fs log=%s",
        "dry-run" if args.dry_run else "enabled",
        args.min_off_seconds,
        args.recent_input_seconds,
        args.cooldown_seconds,
        args.log_file,
    )

    message = wt.MSG()
    exit_code = 0
    while True:
        result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
        if result == 0:
            break
        if result == -1:
            logger.error("GetMessageW failed (error %d)", ctypes.get_last_error())
            exit_code = 1
            break
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))

    user32.UnregisterPowerSettingNotification(notification)
    kernel32.CloseHandle(mutex)
    logger.info("stopped")
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="log a wake decision without transmitting IR")
    parser.add_argument(
        "--min-off-seconds",
        type=float,
        default=610.0,
        help="minimum display-off time before a display-on event may send Power (default 610)",
    )
    parser.add_argument(
        "--recent-input-seconds",
        type=float,
        default=5.0,
        help="require user input within this many seconds of display-on (default 5)",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=60.0,
        help="minimum time between power attempts (default 60)",
    )
    parser.add_argument(
        "--recovery-cooldown-seconds",
        type=float,
        default=30.0,
        help="minimum time between Windows display recoveries (default 30)",
    )
    parser.add_argument(
        "--tv-startup-seconds",
        type=float,
        default=8.0,
        help="wait after IR Power before recovering the HDMI display (default 8)",
    )
    parser.add_argument("--log-file", type=Path, default=default_log_path(), help="path to the rotating log file")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)
    for name in (
        "min_off_seconds",
        "recent_input_seconds",
        "cooldown_seconds",
        "recovery_cooldown_seconds",
        "tv_startup_seconds",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = configure_logging(args.log_file, args.verbose)
    return run_monitor(args, logger)


if __name__ == "__main__":
    raise SystemExit(main())
