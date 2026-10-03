"""Send TCL Power when Volume Down and Volume Up are held together."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
from pathlib import Path
import queue
import sys
import threading
import time

from wake_monitor import configure_logging, default_log_path, send_power


MEDIA_KEYS = frozenset((0xAE, 0xAF))


class PowerChord:
    """Recognize overlapping keys or two short, completed media pulses.

    This keyboard's combined press produces ~8 ms pulses; ordinary taps last
    much longer. Wait for both releases before accepting the pulse fallback.
    """

    def __init__(self, cooldown: float = 3.0):
        self.down: set[int] = set()
        self.latched = False
        self.last_trigger = float("-inf")
        self.cooldown = cooldown
        self.started: dict[int, float] = {}
        self.last_pulse: tuple[int, float] | None = None

    def update(self, key: int, pressed: bool, now: float) -> bool:
        if key not in MEDIA_KEYS:
            return False
        pulse_pair = False
        if pressed:
            if key not in self.down:
                self.started[key] = now
            else:
                # A repeating held key cannot be a short pulse.
                self.started.pop(key, None)
                self.last_pulse = None
            self.down.add(key)
        else:
            started = self.started.pop(key, None)
            self.down.discard(key)
            if started is not None and 0 <= now - started <= 0.035:
                pulse_pair = (
                    self.last_pulse is not None
                    and self.last_pulse[0] != key
                    and 0 <= started - self.last_pulse[1] <= 0.25
                )
                self.last_pulse = (key, started)
            else:
                self.last_pulse = None
        trigger = False
        if (self.down == MEDIA_KEYS or pulse_pair) and not self.latched:
            self.latched = True
            self.last_pulse = None
            if now - self.last_trigger >= self.cooldown:
                self.last_trigger = now
                trigger = True
        if not self.down:
            if self.latched:
                self.last_pulse = None
            self.latched = False
        return trigger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='log the chord without sending IR')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('this listener requires Windows')
    logger = configure_logging(default_log_path().with_name('power-hotkey.log'), False)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

    class KeyboardEvent(ctypes.Structure):
        _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD),
                    ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', ctypes.c_size_t)]

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = wt.HANDLE
    user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    mutex = kernel32.CreateMutexW(None, False, 'Local\\tcl-ir-power-hotkey')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        logger.error('another power-hotkey listener is already running')
        kernel32.CloseHandle(mutex)
        return 2

    pending: queue.Queue[None] = queue.Queue(maxsize=1)
    busy = threading.Event()
    chord = PowerChord()

    def transmit() -> None:
        while True:
            pending.get()
            try:
                if args.dry_run:
                    logger.info('DRY RUN: chord detected; would send TCL Power')
                else:
                    send_power(Path(__file__).resolve().parents[1], logger)
            except Exception:
                logger.exception('power transmission failed')
            finally:
                busy.clear()

    threading.Thread(target=transmit, daemon=True, name='tcl-power-hotkey').start()

    @callback_type
    def keyboard_hook(code: int, message: int, data: int) -> int:
        try:
            if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                event = ctypes.cast(data, ctypes.POINTER(KeyboardEvent)).contents
                # Media software may synthesize volume keys; accept those too.
                if event.vkCode in MEDIA_KEYS:
                    logger.info("media key=%s pressed=%s flags=%s", hex(event.vkCode), message in (0x100, 0x104), hex(event.flags))
                    if chord.update(event.vkCode, message in (0x100, 0x104), time.monotonic()):
                        if not busy.is_set():
                            busy.set()
                            pending.put_nowait(None)
        except Exception:
            logger.exception('keyboard callback failed')
        return user32.CallNextHookEx(None, code, message, data)

    hook = user32.SetWindowsHookExW(13, keyboard_hook, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        kernel32.CloseHandle(mutex)
        raise ctypes.WinError(ctypes.get_last_error())
    logger.info('listening for Volume Down + Volume Up (overlap or two pulses <=35ms, within 250ms); dry_run=%s', args.dry_run)
    try:
        message = wt.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == 0:
                return 0
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        user32.UnhookWindowsHookEx(hook)
        kernel32.CloseHandle(mutex)


if __name__ == '__main__':
    raise SystemExit(main())
