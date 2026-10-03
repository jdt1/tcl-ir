"""Visible Windows keyboard-event diagnostic. No keys are saved to disk."""

import ctypes
import ctypes.wintypes as wt
from datetime import datetime
import queue
import sys
import tkinter as tk
from tkinter.scrolledtext import ScrolledText


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows is required')
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
    user32.GetKeyNameTextW.argtypes = [ctypes.c_long, wt.LPWSTR, ctypes.c_int]
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE
    events = queue.SimpleQueue()
    held = set()
    names = {0xAD: 'VOLUME MUTE', 0xAE: 'VOLUME DOWN', 0xAF: 'VOLUME UP'}

    @callback_type
    def hook_callback(code, message, data):
        if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
            event = ctypes.cast(data, ctypes.POINTER(KeyboardEvent)).contents
            events.put((datetime.now().strftime('%H:%M:%S.%f')[:-3],
                        int(event.vkCode), int(event.scanCode), int(event.flags),
                        message in (0x100, 0x104)))
        return user32.CallNextHookEx(None, code, message, data)

    root = tk.Tk()
    root.title('Live Windows keypresses')
    root.geometry('960x600')
    tk.Label(root, text='Press any keys, including volume buttons. Events appear even when another app is focused.',
             anchor='w', padx=12, pady=10).pack(fill='x')
    tk.Label(root, text='Display only • nothing saved • normal key actions continue • close this window to stop',
             anchor='w', padx=12).pack(fill='x')
    held_label = tk.Label(root, text='Held: none', anchor='w', padx=12, pady=10)
    held_label.pack(fill='x')
    output = ScrolledText(root, font=('Consolas', 11), wrap='none', state='disabled')
    output.pack(fill='both', expand=True, padx=12, pady=8)

    def key_name(vk, scan, flags):
        if vk in names:
            return names[vk]
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetKeyNameTextW((scan << 16) | ((flags & 1) << 24), buffer, 128)
        return buffer.value or f'VK {vk:02X}'

    def poll():
        lines = []
        while not events.empty():
            stamp, vk, scan, flags, pressed = events.get()
            name = key_name(vk, scan, flags)
            repeated = pressed and vk in held
            if pressed:
                held.add(vk)
            else:
                held.discard(vk)
            action = 'REPEAT' if repeated else ('DOWN' if pressed else 'UP')
            source = 'injected' if flags & 0x10 else 'physical'
            lines.append(f'{stamp}  {action:6}  {name:22} VK=0x{vk:02X}  scan=0x{scan:02X}  {source}\n')
        if lines:
            held_label.config(text='Held: ' + (', '.join(names.get(vk, f'VK {vk:02X}') for vk in sorted(held)) or 'none'))
            output.config(state='normal')
            output.insert('end', ''.join(lines))
            if int(output.index('end-1c').split('.')[0]) > 1000:
                output.delete('1.0', '201.0')
            output.see('end')
            output.config(state='disabled')
        root.after(25, poll)

    def clear():
        output.config(state='normal')
        output.delete('1.0', 'end')
        output.config(state='disabled')

    tk.Button(root, text='Clear events', command=clear).pack(pady=(0, 10))
    hook = user32.SetWindowsHookExW(13, hook_callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        root.destroy()
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        root.after(25, poll)
        root.lift()
        root.mainloop()
    finally:
        user32.UnhookWindowsHookEx(hook)


if __name__ == '__main__':
    main()
