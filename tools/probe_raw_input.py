"""Bounded Windows Raw Input probe for consumer controls and function keys.

Logs to stdout; ordinary typing is neither decoded nor logged. Does not send IR.
"""

import argparse
import ctypes as c
import ctypes.wintypes as w
from datetime import datetime
import os


class Header(c.Structure):
    _fields_ = [('kind', w.DWORD), ('size', w.DWORD),
                ('device', w.HANDLE), ('wparam', c.c_size_t)]


class Device(c.Structure):
    _fields_ = [('page', w.USHORT), ('usage', w.USHORT),
                ('flags', w.DWORD), ('target', w.HWND)]


class DeviceEntry(c.Structure):
    _fields_ = [('device', w.HANDLE), ('kind', w.DWORD)]


class Keyboard(c.Structure):
    _fields_ = [('scan', w.USHORT), ('flags', w.USHORT), ('reserved', w.USHORT),
                ('vk', w.USHORT), ('message', w.UINT), ('extra', w.ULONG)]


class HookEvent(c.Structure):
    _fields_ = [('vk', w.DWORD), ('scan', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', c.c_size_t)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('--seconds must be between 1 and 300')
    u = c.WinDLL('user32', use_last_error=True)
    k = c.WinDLL('kernel32', use_last_error=True)
    hid = c.WinDLL('hid', use_last_error=True)
    proc_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
    hook_type = c.WINFUNCTYPE(c.c_ssize_t, c.c_int, c.c_size_t, c.c_ssize_t)

    class WindowClass(c.Structure):
        _fields_ = [('style', w.UINT), ('proc', proc_type), ('clsExtra', c.c_int),
                    ('wndExtra', c.c_int), ('instance', w.HINSTANCE), ('icon', w.HICON),
                    ('cursor', w.HANDLE), ('brush', w.HBRUSH),
                    ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]

    def api(dll, name, restype, *argtypes):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = restype, argtypes
        return fn

    api(k, 'GetModuleHandleW', w.HMODULE, w.LPCWSTR)
    api(u, 'RegisterClassW', w.ATOM, c.POINTER(WindowClass))
    api(u, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p)
    api(u, 'DefWindowProcW', c.c_ssize_t, w.HWND, w.UINT, c.c_size_t, c.c_ssize_t)
    api(u, 'RegisterRawInputDevices', w.BOOL, c.POINTER(Device), w.UINT, w.UINT)
    api(u, 'GetRawInputDeviceList', w.UINT, c.POINTER(DeviceEntry), c.POINTER(w.UINT), w.UINT)
    api(u, 'GetRawInputDeviceInfoW', w.UINT, w.HANDLE, w.UINT, c.c_void_p, c.POINTER(w.UINT))
    api(u, 'GetRawInputData', w.UINT, w.HANDLE, w.UINT, c.c_void_p, c.POINTER(w.UINT), w.UINT)
    api(u, 'GetMessageW', c.c_int, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
    api(u, 'DispatchMessageW', c.c_ssize_t, c.POINTER(w.MSG))
    api(u, 'SetTimer', c.c_size_t, w.HWND, c.c_size_t, w.UINT, c.c_void_p)
    api(u, 'KillTimer', w.BOOL, w.HWND, c.c_size_t)
    api(u, 'DestroyWindow', w.BOOL, w.HWND)
    api(u, 'PostQuitMessage', None, c.c_int)
    api(u, 'SetWindowsHookExW', w.HANDLE, c.c_int, hook_type, w.HINSTANCE, w.DWORD)
    api(u, 'CallNextHookEx', c.c_ssize_t, w.HANDLE, c.c_int, c.c_size_t, c.c_ssize_t)
    api(u, 'UnhookWindowsHookEx', w.BOOL, w.HANDLE)
    api(hid, 'HidP_MaxUsageListLength', w.ULONG, c.c_int, w.USHORT, c.c_void_p)
    api(hid, 'HidP_GetUsages', w.LONG, c.c_int, w.USHORT, w.USHORT,
        c.POINTER(w.USHORT), c.POINTER(w.ULONG), c.c_void_p, c.c_void_p, w.ULONG)

    def log(message):
        print(datetime.now().isoformat(timespec='milliseconds'), message, flush=True)

    def info(handle, command, wide=False):
        size = w.UINT()
        if u.GetRawInputDeviceInfoW(handle, command, None, c.byref(size)) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        buf = c.create_unicode_buffer(size.value + 1) if wide else c.create_string_buffer(size.value)
        if u.GetRawInputDeviceInfoW(handle, command, buf, c.byref(size)) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        return buf.value if wide else buf

    names, preparsed = {}, {}

    def device_name(handle):
        if handle not in names:
            names[handle] = f'D{len(names) + 1}'
            path = info(handle, 0x20000007, True) if handle else 'no device handle'
            log(f'DEVICE {names[handle]} {path}')
        return names[handle]

    count = w.UINT()
    if u.GetRawInputDeviceList(None, c.byref(count), c.sizeof(DeviceEntry)) == 0xFFFFFFFF:
        raise c.WinError(c.get_last_error())
    devices = (DeviceEntry * count.value)()
    if u.GetRawInputDeviceList(devices, c.byref(count), c.sizeof(DeviceEntry)) == 0xFFFFFFFF:
        raise c.WinError(c.get_last_error())
    for entry in devices[:count.value]:
        if entry.kind != 0:
            device_name(entry.device)

    counts = {'hid': 0, 'keyboard_media': 0, 'other_keyboard': 0, 'hook_media': 0}
    usage_names = {0xE2: 'MUTE', 0xE9: 'VOL_UP', 0xEA: 'VOL_DOWN'}

    def raw_input(handle):
        size = w.UINT()
        header_size = c.sizeof(Header)
        if u.GetRawInputData(handle, 0x10000003, None, c.byref(size), header_size) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        data = c.create_string_buffer(size.value)
        if u.GetRawInputData(handle, 0x10000003, data, c.byref(size), header_size) == 0xFFFFFFFF:
            raise c.WinError(c.get_last_error())
        header = Header.from_buffer(data)
        name = device_name(header.device)
        if header.kind == 1:
            event = Keyboard.from_buffer(data, header_size)
            if event.vk in (0xAD, 0xAE, 0xAF) or 0x70 <= event.vk <= 0x87:
                counts['keyboard_media'] += 1
                log(f'RAW_KEY {name} vk={event.vk:02X} scan={event.scan:02X} flags={event.flags:02X}')
            else:
                counts['other_keyboard'] += 1
        elif header.kind == 2:
            report_size = int.from_bytes(data.raw[header_size:header_size + 4], 'little')
            report_count = int.from_bytes(data.raw[header_size + 4:header_size + 8], 'little')
            if header.device not in preparsed:
                preparsed[header.device] = info(header.device, 0x20000005)
            prep = preparsed[header.device]
            capacity = max(1, hid.HidP_MaxUsageListLength(0, 0x0C, prep))
            for i in range(report_count):
                offset = header_size + 8 + i * report_size
                report = data.raw[offset:offset + report_size]
                usages = (w.USHORT * capacity)()
                usage_count = w.ULONG(capacity)
                status = hid.HidP_GetUsages(0, 0x0C, 0, usages, c.byref(usage_count),
                                           prep, c.create_string_buffer(report), len(report))
                decoded = ','.join(usage_names.get(x, f'0x{x:04X}') for x in usages[:usage_count.value]) if status == 0x110000 else f'parse_status=0x{status & 0xFFFFFFFF:08X}'
                counts['hid'] += 1
                log(f'RAW_HID {name} bytes={report.hex(" ")} active=[{decoded}]')

    @proc_type
    def window_proc(hwnd, message, wp, lp):
        try:
            if message == 0xFF:
                raw_input(lp)
            elif message == 0x113:
                u.PostQuitMessage(0)
                return 0
        except Exception as error:
            log(f'ERROR {error}')
        return u.DefWindowProcW(hwnd, message, wp, lp)

    @hook_type
    def keyboard_hook(code, message, data):
        if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
            event = c.cast(data, c.POINTER(HookEvent)).contents
            if event.vk in (0xAD, 0xAE, 0xAF):
                counts['hook_media'] += 1
                log(f'HOOK vk={event.vk:02X} pressed={message in (0x100, 0x104)} flags={event.flags:02X}')
        return u.CallNextHookEx(None, code, message, data)

    instance = k.GetModuleHandleW(None)
    class_name = f'TclRawInputProbe-{os.getpid()}'
    wc = WindowClass(proc=window_proc, instance=instance, name=class_name)
    if not u.RegisterClassW(c.byref(wc)):
        raise c.WinError(c.get_last_error())
    hwnd = u.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, None, None, instance, None)
    if not hwnd:
        raise c.WinError(c.get_last_error())
    registered = (Device * 2)(Device(0x0C, 1, 0x100, hwnd), Device(1, 6, 0x100, hwnd))
    hook = None
    try:
        if not u.RegisterRawInputDevices(registered, len(registered), c.sizeof(Device)):
            raise c.WinError(c.get_last_error())
        hook = u.SetWindowsHookExW(13, keyboard_hook, instance, 0)
        if not hook or not u.SetTimer(hwnd, 1, args.seconds * 1000, None):
            raise c.WinError(c.get_last_error())
        log(f'READY: consumer HID + raw keyboard + media hook; duration={args.seconds}s')
        msg = w.MSG()
        while True:
            result = u.GetMessageW(c.byref(msg), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                raise c.WinError(c.get_last_error())
            u.DispatchMessageW(c.byref(msg))
    finally:
        if hook:
            u.UnhookWindowsHookEx(hook)
        u.KillTimer(hwnd, 1)
        u.DestroyWindow(hwnd)
        log(f'STOPPED counts={counts}')


if __name__ == '__main__':
    main()
