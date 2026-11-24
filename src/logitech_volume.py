"""Read the C52B receiver's physical volume buttons through its consumer collection."""

import struct
import time

from raw_media import VOLUME_USAGES


def volume_state(report):
    # Report 3 on this receiver is two little-endian Consumer Page usage IDs.
    if len(report) != 5 or report[0] != 3:
        return None
    return VOLUME_USAGES.intersection(struct.unpack_from('<HH', bytes(report), 1))


def listen(logger, on_state, on_remove, seconds=None):
    import hid

    deadline = time.monotonic() + seconds if seconds is not None else float('inf')
    last_error = None
    while time.monotonic() < deadline:
        device = hid.device()
        try:
            paths = [item['path'] for item in hid.enumerate(0x046D, 0xC52B)
                     if (item['usage_page'], item['usage']) == (0x0C, 1)]
            if len(paths) != 1:
                raise OSError(f'expected one C52B consumer-control interface, found {len(paths)}')
            device.open_path(paths[0])
            logger.info('Logitech USB volume-button listener ready')
            last_error = None
            while time.monotonic() < deadline:
                report = device.read(64, 500)
                state = volume_state(report)
                if state is not None:
                    on_state((1, 3), state)
        except OSError as error:
            if str(error) != last_error:
                logger.warning('waiting for Logitech volume buttons: %s', error)
                last_error = str(error)
        finally:
            device.close()
            on_remove(1)
        time.sleep(max(0, min(2, deadline - time.monotonic())))
