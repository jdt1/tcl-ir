"""Experimental Windows HID-over-GATT TV peripheral.

This is a remote, not a client connecting to a TV control service. The TV must
pair with this PC and subscribe to its input reports. A successful notification
does not prove that the TV acted on it. No TV state is inferred or cached.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import uuid


@dataclass(frozen=True)
class Key:
    report_id: int
    value: int
    note: str = "unverified on TCL 43C61B"


KEYS = {
    "on": Key(2, 2, "System Wake Up; standby radio/reconnect support unverified"),
    "off": Key(2, 1, "System Sleep; TCL handling unverified"),
    "power": Key(1, 0x30, "Power toggle; TCL handling unverified"),
    "brightness_up": Key(1, 0x6F, "brightness increment; panel/backlight effect unverified"),
    "brightness_down": Key(1, 0x70, "brightness decrement; panel/backlight effect unverified"),
    "vol_up": Key(1, 0xE9),
    "vol_down": Key(1, 0xEA),
    "mute": Key(1, 0xE2),
    "home": Key(1, 0x223),
    "back": Key(1, 0x224),
    "settings": Key(1, 0x19F),
    "play_pause": Key(1, 0xCD),
    "up": Key(3, 0x52),
    "down": Key(3, 0x51),
    "left": Key(3, 0x50),
    "right": Key(3, 0x4F),
    "ok": Key(3, 0x28),
}
ALIASES = {
    "vol+": "vol_up", "vol-": "vol_down",
    "brightness+": "brightness_up", "brightness-": "brightness_down",
    "wake": "on", "sleep": "off", "enter": "ok", "select": "ok",
}

# HID Usage Tables: Consumer Control array (one uint16), System Control
# Sleep/Wake bits, and a report-mode keyboard for arrows/Enter. HOGP puts the
# report ID in descriptor 0x2908; the characteristic value excludes that byte.
REPORT_MAP = bytes.fromhex(
    "05 0C 09 01 A1 01 85 01 15 00 26 FF 03 19 00 2A FF 03 "
    "75 10 95 01 81 00 C0 "
    "05 01 09 80 A1 01 85 02 15 00 25 01 75 01 95 02 "
    "09 82 09 83 81 02 75 06 95 01 81 03 C0 "
    "05 01 09 06 A1 01 85 03 05 07 19 E0 29 E7 15 00 25 01 "
    "75 01 95 08 81 02 75 08 95 01 81 03 19 00 29 65 "
    "15 00 25 65 75 08 95 06 81 00 C0"
)
REPORT_LENGTHS = {1: 2, 2: 1, 3: 8}


def resolve_key(name: str) -> str:
    name = name.strip().lower()
    name = ALIASES.get(name, name.replace("-", "_"))
    if name not in KEYS:
        raise ValueError(f"unknown Bluetooth key {name!r}; use --list")
    return name


def key_report(name: str) -> tuple[int, bytes]:
    key = KEYS[resolve_key(name)]
    if key.report_id == 1:
        data = key.value.to_bytes(2, "little")
    elif key.report_id == 2:
        data = bytes([key.value])
    else:
        data = bytes([0, 0, key.value, 0, 0, 0, 0, 0])
    return key.report_id, data


def validate_send(keys: list[str], repeat: int, gap: float) -> list[str]:
    if not keys or len(keys) > 100:
        raise ValueError("provide between 1 and 100 keys")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= 100:
        raise ValueError("repeat must be an integer between 1 and 100")
    if not math.isfinite(gap) or not 0.05 <= gap <= 5:
        raise ValueError("gap must be between 0.05 and 5 seconds")
    names = [resolve_key(name) for name in keys]
    if len(names) * repeat > 100:
        raise ValueError("at most 100 key presses per request")
    if len(names) * repeat * (gap + 0.08) > 40:
        raise ValueError("sequence exceeds 40 seconds; use fewer keys or a shorter gap")
    # Do not automate repeated power commands while characterizing this TV.
    if repeat != 1 and any(name in ("on", "off", "power") for name in names):
        raise ValueError("power commands cannot use --repeat")
    return names


def radio_identity(address: int) -> dict:
    """Read the matching radio's system-owned identity without changing it."""
    import ctypes
    from ctypes import wintypes as wt

    class RadioInfo(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("address", ctypes.c_ulonglong),
                    ("name", wt.WCHAR * 248), ("device_class", wt.DWORD),
                    ("subversion", wt.USHORT), ("manufacturer", wt.USHORT)]

    class FindParams(ctypes.Structure):
        _fields_ = [("size", wt.DWORD)]

    bluetooth = ctypes.WinDLL("bthprops.cpl")
    kernel = ctypes.WinDLL("kernel32")
    bluetooth.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(FindParams), ctypes.POINTER(wt.HANDLE)]
    bluetooth.BluetoothFindFirstRadio.restype = wt.HANDLE
    bluetooth.BluetoothFindNextRadio.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HANDLE)]
    bluetooth.BluetoothFindRadioClose.argtypes = [wt.HANDLE]
    bluetooth.BluetoothGetRadioInfo.argtypes = [wt.HANDLE, ctypes.POINTER(RadioInfo)]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    params = FindParams(ctypes.sizeof(FindParams))
    radio = wt.HANDLE()
    search = bluetooth.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(radio))
    if not search:
        return {"radio_identity": "unavailable"}
    try:
        while True:
            try:
                info = RadioInfo()
                info.size = ctypes.sizeof(info)
                if bluetooth.BluetoothGetRadioInfo(radio, ctypes.byref(info)) == 0 and info.address == address:
                    return {"computer_name": info.name, "classic_device_class": f"0x{info.device_class:06X}",
                            "classic_major_class": (info.device_class >> 8) & 0x1F}
            finally:
                kernel.CloseHandle(radio)
            if not bluetooth.BluetoothFindNextRadio(search, ctypes.byref(radio)):
                break
    finally:
        bluetooth.BluetoothFindRadioClose(search)
    return {"radio_identity": "unavailable"}


def bt_uuid(short: int) -> uuid.UUID:
    return uuid.UUID(f"0000{short:04x}-0000-1000-8000-00805f9b34fb")


def respond_read(request, value: bytes, buffer_factory=bytes):
    # Report Map can span several ATT Read Blob requests on the default MTU.
    if request.offset > len(value):
        request.respond_with_protocol_error(0x07)  # Invalid Offset
    else:
        request.respond_with_value(buffer_factory(value[request.offset:request.offset + request.length]))


def select_client(clients, target: str | None):
    """Never broadcast input to multiple hosts or fall back from a chosen TV."""
    if target:
        matches = [client for client in clients if client.session.device_id.id == target]
        if len(matches) != 1:
            raise RuntimeError("selected TV is not subscribed; reconnect it in the TV's accessory settings")
        return matches[0]
    if len(clients) != 1:
        raise RuntimeError(
            f"expected one subscribed host, found {len(clients)}; pair/reconnect the TV, "
            "or select its device_id with --target"
        )
    return clients[0]


class BluetoothRemote:
    def __init__(self, logger: logging.Logger):
        # Import lazily so --list, --dry-run and unit tests need no WinRT.
        from winrt.windows.devices.bluetooth import BluetoothAdapter, BluetoothLEDevice
        from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothError
        from winrt.windows.devices.bluetooth import genericattributeprofile as gatt
        from winrt.windows.storage.streams import DataWriter
        self.Adapter = BluetoothAdapter
        self.Device = BluetoothLEDevice
        self.ConnectionStatus = BluetoothConnectionStatus
        self.Success = BluetoothError.SUCCESS
        self.gatt = gatt
        self.DataWriter = DataWriter
        self.logger = logger
        self.provider = None
        self.battery_provider = None
        self.reports = {}
        self.values = {n: bytes(length) for n, length in REPORT_LENGTHS.items()}
        self.events = []
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.suspended = False
        self.advertising_requested = False

    async def probe(self) -> dict:
        adapter = await self.Adapter.get_default_async()
        if adapter is None:
            raise RuntimeError("no Bluetooth adapter found")
        return {
            "adapter": adapter.device_id,
            "low_energy": adapter.is_low_energy_supported,
            "peripheral_role": adapter.is_peripheral_role_supported,
            "central_role": adapter.is_central_role_supported,
            **radio_identity(adapter.bluetooth_address),
        }

    def check(self, result, operation: str):
        if result.error != self.Success:
            raise RuntimeError(f"{operation}: {result.error.name}")
        return result

    def buffer(self, value: bytes):
        # Use a native IBuffer that can cross the Windows Bluetooth broker.
        # PyWinRT's implicit Python buffer wrapper caused E_ACCESSDENIED when
        # publishing static values on this PC. It is unsuitable for this IPC.
        writer = self.DataWriter()
        try:
            writer.write_bytes(value)
            return writer.detach_buffer()
        finally:
            writer.close()

    async def characteristic(self, service, short, properties, value=None):
        params = self.gatt.GattLocalCharacteristicParameters()
        params.characteristic_properties = properties
        params.read_protection_level = self.gatt.GattProtectionLevel.ENCRYPTION_REQUIRED
        params.write_protection_level = self.gatt.GattProtectionLevel.ENCRYPTION_REQUIRED
        if value is not None:
            params.static_value = self.buffer(value)
        result = self.check(
            await service.create_characteristic_async(bt_uuid(short), params), f"characteristic {short:04X}"
        )
        characteristic = result.characteristic
        return characteristic

    def request_handler(self, operation):
        # WinRT events arrive on worker threads. Take the deferral immediately,
        # then move the async request to the owning asyncio event loop.
        loop = asyncio.get_running_loop()

        async def handle(args, deferral):
            try:
                request = await args.get_request_async()
                if request is not None:
                    operation(request)
            except Exception:
                self.logger.exception("GATT request failed")
            finally:
                deferral.complete()

        def schedule(args, deferral):
            task = loop.create_task(handle(args, deferral))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        def handler(sender, args):
            deferral = args.get_deferral()
            loop.call_soon_threadsafe(schedule, args, deferral)

        self.events.append(handler)
        return handler

    async def start(self):
        facts = await self.probe()
        if not facts["low_energy"] or not facts["peripheral_role"]:
            raise RuntimeError("this adapter cannot act as a BLE peripheral")
        result = self.check(await self.gatt.GattServiceProvider.create_async(bt_uuid(0x1812)), "HID service")
        self.provider = result.service_provider
        service = self.provider.service
        prop = self.gatt.GattCharacteristicProperties
        await self.characteristic(service, 0x2A4A, prop.READ, bytes.fromhex("11 01 00 03"))
        await self.characteristic(service, 0x2A4B, prop.READ, REPORT_MAP)
        control = await self.characteristic(service, 0x2A4C, prop.WRITE_WITHOUT_RESPONSE)

        def write_control(request):
            data = bytes(request.value)
            if data:
                self.suspended = data[0] == 0
                self.logger.info("HID suspend=%s", self.suspended)
            if request.option == self.gatt.GattWriteOption.WRITE_WITH_RESPONSE:
                request.respond()

        control.add_write_requested(self.request_handler(write_control))
        for report_id in REPORT_LENGTHS:
            characteristic = await self.characteristic(service, 0x2A4D, prop.READ | prop.NOTIFY)
            characteristic.add_read_requested(self.request_handler(
                lambda request, n=report_id: respond_read(request, self.values[n], self.buffer)
            ))
            params = self.gatt.GattLocalDescriptorParameters()
            params.read_protection_level = self.gatt.GattProtectionLevel.ENCRYPTION_REQUIRED
            params.static_value = self.buffer(bytes([report_id, 1]))
            self.check(await characteristic.create_descriptor_async(bt_uuid(0x2908), params), "Report Reference")
            self.reports[report_id] = characteristic

        # A mains-powered remote exposes a constant full battery. The system
        # already supplies GAP/GATT/Device Information; do not recreate those.
        result = self.check(await self.gatt.GattServiceProvider.create_async(bt_uuid(0x180F)), "Battery service")
        self.battery_provider = result.service_provider
        await self.characteristic(self.battery_provider.service, 0x2A19, prop.READ, b"\x64")

        params = self.gatt.GattServiceProviderAdvertisingParameters()
        params.is_connectable = True
        params.is_discoverable = True
        self.provider.start_advertising(params)
        self.advertising_requested = True
        # ABORTED can be a transient startup status; require an actual STARTED.
        for _ in range(100):
            if self.provider.advertisement_status == self.gatt.GattServiceProviderAdvertisementStatus.STARTED:
                self.logger.info("HID advertising started")
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"HID advertising did not start: {self.provider.advertisement_status.name}")

    async def status(self) -> dict:
        hosts = {}
        for report_id, characteristic in self.reports.items():
            for client in characteristic.subscribed_clients:
                device_id = client.session.device_id.id
                host = hosts.setdefault(device_id, {"device_id": device_id, "reports": []})
                host["reports"].append(report_id)
        return {
            "advertising": self.provider.advertisement_status.name,
            "hosts": list(hosts.values()),
            "hid_suspended": self.suspended,
            "tv_power": "unknown",
            "tv_brightness": "unknown",
        }

    async def send(self, keys, repeat=1, gap=0.25, target=None) -> dict:
        names = validate_send(keys, repeat, gap)
        async with self.lock:
            # Validate all subscriptions before sending any key in a sequence.
            chosen = {}
            for report_id in {KEYS[name].report_id for name in names}:
                chosen[report_id] = select_client(list(self.reports[report_id].subscribed_clients), target)
            if len({client.session.device_id.id for client in chosen.values()}) != 1:
                raise RuntimeError("reports belong to different hosts; specify the TV with --target")
            device_id = next(iter(chosen.values())).session.device_id.id
            device = await self.Device.from_id_async(device_id)
            try:
                if device is None or device.connection_status != self.ConnectionStatus.CONNECTED:
                    raise RuntimeError("TV Bluetooth link is disconnected; no keys sent")
                sent = []
                for name in names:
                    report_id, data = key_report(name)
                    characteristic, client = self.reports[report_id], chosen[report_id]
                    for _ in range(repeat):
                        self.values[report_id] = data
                        try:
                            result = await characteristic.notify_value_async(self.buffer(data), client)
                            if result.status != self.gatt.GattCommunicationStatus.SUCCESS:
                                raise RuntimeError(f"{name} notification failed: {result.status.name}; not retried")
                            await asyncio.sleep(0.08)
                        finally:
                            release = bytes(REPORT_LENGTHS[report_id])
                            self.values[report_id] = release
                            result = await characteristic.notify_value_async(self.buffer(release), client)
                            if result.status != self.gatt.GattCommunicationStatus.SUCCESS:
                                raise RuntimeError(f"{name} release failed: {result.status.name}; not retried")
                        sent.append(name)
                        self.logger.info("notified %s; TV effect unverified", name)
                        await asyncio.sleep(gap)
                return {"notified": sent, "tv_effect": "unverified", "device_id": device_id}
            finally:
                if device is not None:
                    device.close()

    async def close(self):
        if self.provider is not None and self.advertising_requested:
            self.provider.stop_advertising()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
