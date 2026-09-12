"""Detect an ElkSmart/Ocrustar USB IR blaster and optionally handshake.

The handshake initializes USB communication but does not transmit infrared.
"""

from __future__ import annotations

import argparse
import sys
import time

import libusb_package
import usb.core
import usb.util


USB_VID = 0x045C
SUPPORTED_PIDS = (0x0195, 0x02AA)
HELLO = bytes((0xFC,) * 4)
ACK = bytes((0xFA,) * 4)
DEVICE_SIGNATURES = {
    bytes((0x70, 0x01)): "D552",
    bytes((0x02, 0xAA)): "D226",
}


def find_device(pid: int | None = None):
    backend = libusb_package.get_libusb1_backend()
    if pid is not None:
        return usb.core.find(idVendor=USB_VID, idProduct=pid, backend=backend)

    for candidate in SUPPORTED_PIDS:
        device = usb.core.find(
            idVendor=USB_VID,
            idProduct=candidate,
            backend=backend,
        )
        if device is not None:
            return device
    return None


def bulk_endpoints(device):
    device.set_configuration()
    interface = device.get_active_configuration()[(0, 0)]
    endpoints = [
        endpoint
        for endpoint in interface
        if usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
    ]
    endpoint_in = next(
        (
            endpoint
            for endpoint in endpoints
            if usb.util.endpoint_direction(endpoint.bEndpointAddress)
            == usb.util.ENDPOINT_IN
        ),
        None,
    )
    endpoint_out = next(
        (
            endpoint
            for endpoint in endpoints
            if usb.util.endpoint_direction(endpoint.bEndpointAddress)
            == usb.util.ENDPOINT_OUT
        ),
        None,
    )
    if endpoint_in is None or endpoint_out is None:
        raise RuntimeError("bulk IN/OUT endpoints were not found")
    return endpoint_in, endpoint_out


def handshake(endpoint_in, endpoint_out) -> bytes:
    while True:
        try:
            endpoint_in.read(16_384, timeout=10)
        except usb.core.USBError:
            break

    for _attempt in range(3):
        endpoint_out.write(HELLO, timeout=500)
        for _poll in range(5):
            try:
                response = bytes(endpoint_in.read(16_384, timeout=200))
            except usb.core.USBError:
                continue
            if len(response) >= 6 and response[:4] == HELLO:
                endpoint_out.write(ACK, timeout=500)
                time.sleep(0.05)
                return response
    raise TimeoutError("IR blaster did not answer the USB handshake")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=lambda value: int(value, 0))
    parser.add_argument(
        "--handshake",
        action="store_true",
        help="perform the non-transmitting FC/FA USB handshake",
    )
    args = parser.parse_args()

    device = find_device(args.pid)
    if device is None:
        print(f"NOT FOUND: USB VID 0x{USB_VID:04X}")
        return 1

    print(
        f"FOUND: VID 0x{device.idVendor:04X} PID 0x{device.idProduct:04X} "
        f"bus={device.bus} address={device.address}"
    )

    try:
        endpoint_in, endpoint_out = bulk_endpoints(device)
        print(
            f"BULK ENDPOINTS: IN=0x{endpoint_in.bEndpointAddress:02X} "
            f"OUT=0x{endpoint_out.bEndpointAddress:02X}"
        )
        if args.handshake:
            response = handshake(endpoint_in, endpoint_out)
            signature = response[4:6].hex().upper()
            device_type = DEVICE_SIGNATURES.get(response[4:6], "unknown")
            print(
                f"HANDSHAKE OK: response={response.hex(' ').upper()} "
                f"signature={signature} type={device_type}"
            )
    except (RuntimeError, TimeoutError, usb.core.USBError) as error:
        print(f"USB ERROR: {error}", file=sys.stderr)
        return 2
    finally:
        usb.util.dispose_resources(device)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
