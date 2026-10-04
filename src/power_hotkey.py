"""Send TCL Power when raw HID reports both volume buttons held together."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
from pathlib import Path
import queue
import sys
import threading
import time

from raw_media import RawMediaListener, VOLUME_USAGES
from wake_monitor import configure_logging, default_log_path, send_power


class PowerChord:
    """One toggle per full chord, isolated by device/report, with a cooldown."""

    def __init__(self, cooldown: float = 3.0):
        self.latched: set[tuple[int, int]] = set()
        self.last_trigger = float('-inf')
        self.cooldown = cooldown

    def update(self, source: tuple[int, int], active: frozenset[int], now: float) -> bool:
        volume = active & VOLUME_USAGES
        if not volume:
            self.latched.discard(source)
        elif volume == VOLUME_USAGES and source not in self.latched:
            # Latch even during cooldown: continued holding must not trigger later.
            self.latched.add(source)
            if now - self.last_trigger >= self.cooldown:
                self.last_trigger = now
                return True
        return False

    def remove_device(self, device: int) -> None:
        self.latched = {source for source in self.latched if source[0] != device}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='log the chord without sending IR')
    parser.add_argument('--run-seconds', type=float, help='stop after this many seconds (dry-run only)')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('this listener requires Windows')
    if args.run_seconds is not None and (not args.dry_run or not 0 < args.run_seconds <= 300):
        parser.error('--run-seconds requires --dry-run and a value between 0 and 300')
    logger = configure_logging(default_log_path().with_name('power-hotkey.log'), False)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    ctypes.set_last_error(0)
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
                    logger.info('DRY RUN: raw HID chord detected; would send TCL Power')
                else:
                    send_power(Path(__file__).resolve().parents[1], logger)
            except Exception:
                logger.exception('power transmission failed')
            finally:
                busy.clear()

    def on_state(source: tuple[int, int], active: frozenset[int]) -> None:
        if chord.update(source, active, time.monotonic()):
            if not busy.is_set():
                logger.info('raw HID chord: both volume buttons active; device=%s report=%s', *source)
                busy.set()
                pending.put_nowait(None)
            else:
                logger.info('raw HID chord ignored: power transmission still in progress')

    try:
        threading.Thread(target=transmit, daemon=True, name='tcl-power-hotkey').start()
        logger.info('starting raw HID Volume Down + Volume Up; dry_run=%s', args.dry_run)
        RawMediaListener(logger).run(on_state, chord.remove_device, args.run_seconds)
        return 0
    except Exception:
        logger.exception('power-hotkey listener failed')
        return 1
    finally:
        kernel32.CloseHandle(mutex)
        logger.info('power-hotkey listener stopped')


if __name__ == '__main__':
    raise SystemExit(main())
