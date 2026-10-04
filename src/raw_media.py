"""Receive consumer-control HID reports without synthesized keyboard events."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import os
import signal


VOLUME_USAGES = frozenset((0xE9, 0xEA))


class RawHeader(c.Structure):
    _fields_ = [('kind', w.DWORD), ('size', w.DWORD),
                ('device', w.HANDLE), ('wparam', c.c_size_t)]


class RawDevice(c.Structure):
    _fields_ = [('page', w.USHORT), ('usage', w.USHORT),
                ('flags', w.DWORD), ('target', w.HWND)]


def split_hid_reports(payload: bytes) -> list[bytes]:
    """Split a RAWHID payload, validating sizes before reading reports."""
    if len(payload) < 8:
        raise ValueError('truncated RAWHID header')
    size = int.from_bytes(payload[:4], 'little')
    count = int.from_bytes(payload[4:8], 'little')
    if not size or size * count > len(payload) - 8:
        raise ValueError('invalid RAWHID report lengths')
    return [payload[8 + i * size:8 + (i + 1) * size] for i in range(count)]


class RawMediaListener:
    def __init__(self, logger):
        self.logger = logger
        self.user32 = c.WinDLL('user32', use_last_error=True)
        self.kernel32 = c.WinDLL('kernel32', use_last_error=True)
        self.hid = c.WinDLL('hid', use_last_error=True)
        self.preparsed = {}

        def api(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, argtypes

        api(self.user32, 'GetRawInputDeviceInfoW', w.UINT, w.HANDLE, w.UINT,
            c.c_void_p, c.POINTER(w.UINT))
        api(self.user32, 'GetRawInputData', w.UINT, w.HANDLE, w.UINT, c.c_void_p,
            c.POINTER(w.UINT), w.UINT)
        api(self.hid, 'HidP_MaxUsageListLength', w.ULONG, c.c_int, w.USHORT, c.c_void_p)
        api(self.hid, 'HidP_GetUsages', w.LONG, c.c_int, w.USHORT, w.USHORT,
            c.POINTER(w.USHORT), c.POINTER(w.ULONG), c.c_void_p, c.c_void_p, w.ULONG)

    def decode(self, device: int, report: bytes) -> frozenset[int] | None:
        """Decode with the device descriptor; None means an unrelated report."""
        if not report:
            raise ValueError('empty HID report')
        if device not in self.preparsed:
            size = w.UINT()
            info = self.user32.GetRawInputDeviceInfoW
            if info(device, 0x20000005, None, c.byref(size)) == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            prep = c.create_string_buffer(size.value)
            if info(device, 0x20000005, prep, c.byref(size)) == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            capacity = self.hid.HidP_MaxUsageListLength(0, 0x0C, prep)
            self.preparsed[device] = prep, capacity
        prep, capacity = self.preparsed[device]
        if not capacity:
            return None
        usages = (w.USHORT * capacity)()
        count = w.ULONG(capacity)
        status = self.hid.HidP_GetUsages(0, 0x0C, 0, usages, c.byref(count),
                                       prep, c.create_string_buffer(report), len(report))
        if status & 0xFFFFFFFF in (0xC0110004, 0xC011000A):
            # Usage not found / incompatible report ID. Do not clear held state.
            return None
        if status != 0x110000:
            raise RuntimeError(f'HidP_GetUsages failed: 0x{status & 0xFFFFFFFF:08X}')
        return frozenset(usages[:count.value]) & VOLUME_USAGES

    def read(self, handle, on_state):
        size = w.UINT()
        header_size = c.sizeof(RawHeader)
        get = self.user32.GetRawInputData
        if get(handle, 0x10000003, None, c.byref(size), header_size) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        buf = c.create_string_buffer(size.value)
        copied = get(handle, 0x10000003, buf, c.byref(size), header_size)
        if copied == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        if copied < header_size:
            raise ValueError('truncated raw input header')
        header = RawHeader.from_buffer(buf)
        if header.kind != 2 or not header.device:
            return
        for report in split_hid_reports(buf.raw[header_size:copied]):
            active = self.decode(header.device, report)
            if active is not None:
                # Separate physical devices and report IDs never form a chord.
                on_state((header.device, report[0]), active)

    def run(self, on_state, on_remove, seconds: float | None = None):
        u, k = self.user32, self.kernel32
        proc_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)

        class WindowClass(c.Structure):
            _fields_ = [('style', w.UINT), ('proc', proc_type), ('clsExtra', c.c_int),
                        ('wndExtra', c.c_int), ('instance', w.HINSTANCE), ('icon', w.HICON),
                        ('cursor', w.HANDLE), ('brush', w.HBRUSH),
                        ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

        def api(dll, name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, argtypes

        api(k, 'GetModuleHandleW', w.HMODULE, w.LPCWSTR)
        api(u, 'RegisterClassW', w.ATOM, c.POINTER(WindowClass))
        api(u, 'UnregisterClassW', w.BOOL, w.LPCWSTR, w.HINSTANCE)
        api(u, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p)
        api(u, 'DefWindowProcW', c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
        api(u, 'RegisterRawInputDevices', w.BOOL, c.POINTER(RawDevice), w.UINT, w.UINT)
        api(u, 'GetMessageW', c.c_int, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
        api(u, 'DispatchMessageW', c.c_ssize_t, c.POINTER(w.MSG))
        api(u, 'PostMessageW', w.BOOL, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
        api(u, 'SetTimer', c.c_size_t, w.HWND, c.c_size_t, w.UINT, c.c_void_p)
        api(u, 'KillTimer', w.BOOL, w.HWND, c.c_size_t)
        api(u, 'DestroyWindow', w.BOOL, w.HWND)
        api(u, 'PostQuitMessage', None, c.c_int)

        @proc_type
        def window_proc(hwnd, message, wp, lp):
            try:
                if message == 0xFF:  # WM_INPUT
                    self.read(lp, on_state)
                elif message == 0xFE and wp == 2:  # GIDC_REMOVAL
                    self.preparsed.pop(lp, None)
                    on_remove(lp)
                elif message in (0x10, 0x113):  # WM_CLOSE / bounded diagnostic timer
                    u.PostQuitMessage(0)
                    return 0
            except Exception:
                self.logger.exception('raw media input failed')
            return u.DefWindowProcW(hwnd, message, wp, lp)

        instance = k.GetModuleHandleW(None)
        class_name = f'TclRawMedia-{os.getpid()}'
        wc = WindowClass(proc=window_proc, instance=instance, name=class_name)
        if not u.RegisterClassW(c.byref(wc)):
            raise c.WinError(c.get_last_error())
        hwnd = None
        registered = False
        previous_signals = {}
        try:
            hwnd = u.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0,
                                     None, None, instance, None)
            if not hwnd:
                raise c.WinError(c.get_last_error())
            # INPUTSINK + DEVNOTIFY; no keyboard hook or keyboard registration.
            device = RawDevice(0x0C, 1, 0x2100, hwnd)
            if not u.RegisterRawInputDevices(c.byref(device), 1, c.sizeof(device)):
                raise c.WinError(c.get_last_error())
            registered = True
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_signals[signum] = signal.signal(
                    signum, lambda *_: u.PostMessageW(hwnd, 0x10, 0, 0))
            if seconds is not None and not u.SetTimer(hwnd, 1, max(1, int(seconds * 1000)), None):
                raise c.WinError(c.get_last_error())
            self.logger.info('raw HID consumer-control listener ready')
            msg = w.MSG()
            while True:
                result = u.GetMessageW(c.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise c.WinError(c.get_last_error())
                u.DispatchMessageW(c.byref(msg))
        finally:
            for signum, previous in previous_signals.items():
                signal.signal(signum, previous)
            if registered:
                device = RawDevice(0x0C, 1, 1, None)  # RIDEV_REMOVE
                u.RegisterRawInputDevices(c.byref(device), 1, c.sizeof(device))
            if hwnd:
                u.KillTimer(hwnd, 1)
                u.DestroyWindow(hwnd)
            u.UnregisterClassW(class_name, instance)
