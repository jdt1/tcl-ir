# Bluetooth remote experiments on the TCL 43C61B

## Bluetooth remote prototype — 2026-11-23

Implemented `tv_bluetooth.py` and `src/bluetooth_hid.py` as a separate experimental
backend. The TV is the HID host and the PC advertises a new remote peripheral;
this does not impersonate or copy the original remote's Bluetooth bond. This
path uses the Windows stack and requires no additional hardware or TV LAN.

Initial test with the default Computer identity on THUIS, Windows `10.0.26200`, Python 3.13 in the existing `venv`:

| Check | Result |
| --- | --- |
| Present radio | MediaTek, USB VID `0489`, PID `E156` |
| Windows adapter capabilities | LE, central and peripheral all `True` |
| GATT HID service `0x1812` | Created successfully |
| Full HID provider | Advertising reached `STARTED` in a hidden `pythonw.exe` helper |
| Radio identity | Name `THUIS`, Classic Class of Device `0x2E4104`, major class `1` (Computer) |
| TV accessory-picker test | User reports **THUIS does not appear** |
| HID host subscriptions | None |
| Keyboard appearance advertisement | Rejected with `E_ACCESSDENIED` |
| TV key effects / brightness / standby wake | **Not tested; discovery prevented pairing** |

These results establish PC-side publication, not over-the-air reception by the
TV. Android TV's reference [InputDeviceCriteria](https://android.googlesource.com/platform/packages/apps/TvSettings/+/refs/heads/main/Settings/src/com/android/tv/settings/accessories/InputDeviceCriteria.java)
selects peripherals, and its [BluetoothDevicePairer](https://android.googlesource.com/platform/packages/apps/TvSettings/+/0f9906f9aa37bb67be394e1050d08131dc41378b/Settings/src/com/android/tv/settings/accessories/BluetoothDevicePairer.java)
chooses input/audio connectors by device class. Filtering of the Windows
computer identity is therefore a plausible explanation. We have not inspected
this TCL firmware or captured its discovery decisions.

Microsoft's [GATT server documentation](https://learn.microsoft.com/en-us/windows/apps/develop/devices-sensors/gatt-server)
documents peripheral advertising and reserves GAP, GATT, DIS and Scan Parameters
services. Its [advertisement publisher documentation](https://learn.microsoft.com/en-us/uwp/api/windows.devices.bluetooth.advertisement.bluetoothleadvertisementpublisher)
reserves Appearance, Class of Device and local-name advertisement sections.
The attempted keyboard-appearance override was rejected on this PC as documented.
A normal desktop GATT provider cannot simply rewrite the PC's system-owned GAP
identity. Renaming the PC or installing a BLE scanning library does not provide
that missing control. **Follow-up correction:** this app-level limitation does
not rule out the documented system-wide Classic CoD registry override below.

Implementation evidence: passing Python `bytes` directly as `StaticValue` to
characteristics or Report Reference descriptors made `StartAdvertising` fail with
`E_ACCESSDENIED`. A control using a native Windows `DataWriter.DetachBuffer()`
instead succeeded. The final implementation uses native buffers for all values
crossing the Windows Bluetooth broker, including notifications and read replies.
Constant attributes are served by Windows; dynamic input-report reads honor ATT
offsets. These buffer failures are distinct from the documented rejection of
keyboard-appearance overrides.

The prototype publishes standard HID input reports:

- Consumer usages for volume, navigation, settings, power toggle and brightness.
- Generic Desktop System Sleep (`0x82`) and System Wake Up (`0x83`) as separate
  `off`/`on` commands, rather than disguising the power toggle with new names.
- A report-mode keyboard for arrows/Enter. No keyboard capture is installed.

[Linux HID input mappings](https://github.com/torvalds/linux/blob/master/drivers/hid/hid-input.c)
and [Android Generic.kl](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/data/keyboards/Generic.kl)
provide the standard usage-to-key mapping. Android documents distinct
[sleep/wakeup semantics](https://developer.android.com/reference/android/view/KeyEvent#KEYCODE_WAKEUP),
but that does not establish this TV's handling or radio availability in standby.
Bluetooth accessory support alone supplies neither a brightness API nor state
feedback. Brightness up/down may be ignored or fail to control TCL's picture
backlight; absolute backlight control is not implemented or established.

The helper keeps the provider alive across CLI commands. The independent
[windows-ble-hid investigation](https://github.com/abhishek-raj/windows-ble-hid/blob/main/DEVELOPMENT.md)
reports Android reconnect/subscription problems after provider recreation, so
PC restart and long standby remain separate validation gates. This prototype
does not install autostart or change the IR wake monitor/hotkey.

**Practical outcome:** native Windows Bluetooth control is not working on this
TV with the current discovery flow. The prototype is available for further
compatibility work, but it must not replace the verified IR path yet. A USB
controlled ESP32/nRF52 acting as a real HID remote is a possible next experiment;
its discovery, input handling and standby wake also require actual TV tests.
Network control would be a different transport and remains outside this request.

## Changing the Windows Bluetooth identity — follow-up on 2026-11-23

**The Classic keyboard identity worked on THUIS.** Microsoft's
[Bluetooth registry documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-registry-entries)
allows overriding Classic Class of Device through two DWORDs under
`HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters`.
This corrects the earlier inference that the blocked app-level appearance
advertisement rules out changing Windows' device classification altogether.

The intended field values are:

| Identity | `COD Major` | `COD Type` | Expected major/minor bits of live CoD |
| --- | --- | --- | --- |
| Keyboard | `5` | `16` (`0x10`) | `0x0540` |
| Remote control | `5` | `3` | `0x050C` |

These are unshifted registry field values. Microsoft's
[bthdef.h](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/bthdef.h)
defines Peripheral as `5`, the keyboard mask as `0x10`, and remote control as
`3`; the major and minor fields shift by 8 and 2 bits. Preserve service bits
when comparing live results. Do not write a full `0x0540` into `COD Type`.

This setting affects every radio managed by the Microsoft Bluetooth stack.
It does not add a Classic HID server or change the BLE GAP Appearance field.
The [Bluetooth GAP specification](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-61/out/en/host/generic-access-profile.html)
defines Class of Device for BR/EDR discovery. Thus the proposed experiment may
get past a Classic class filter, but the TV could still select the Classic
transport rather than attach to the existing BLE HID provider. Discovery,
pairing, and actual input are three separate tests.

Classic HID is not a small extension to the existing WinRT remote. A local
Winsock probe on THUIS used protocol `0x0100`, packed 30-byte `SOCKADDR_BTH`,
and HID PSMs `0x11`/`0x13`. Socket creation succeeded but both binds failed with
`10050` (`WSAENETDOWN`); an RFCOMM control successfully bound and listened.
No peer was connected or sent data. Microsoft's [socket documentation](https://learn.microsoft.com/en-us/windows/win32/bluetooth/bluetooth-and-socket)
supports RFCOMM; its [driver-stack documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/bluetooth/bluetooth-driver-stack)
places direct L2CAP access behind BRBs in profile drivers. Thus merely changing
the device class cannot supply the Classic HID transport. The claim that socket
creation alone proves usable Windows L2CAP was also retracted by the reporter in
[CPython issue 148449](https://github.com/python/cpython/issues/148449).

There is also implementation uncertainty: the author of a
[Windows audio-device CoD override](https://github.com/snaphat/windows_bt_baseband_av_mod)
reports older Windows behavior that ignored `COD Major` with `COD Type` present.
That is not a measured result on this Windows 11 build. The tool uses the
documented values and requires checking the **live radio** after activation;
registry readback alone is insufficient.

Added `tools/bluetooth_identity.py` with a read-only default, explicit apply,
live verification and exact restore. Apply stores the initial presence/value
of only these two DWORDs, retains that snapshot when switching profiles, and
rolls back a partial write failure. It does not restart Bluetooth, reboot,
install drivers, change discoverability, or send any TV keys.

#### Live results on 23 November 2026

1. **Baseline.** Both registry overrides were absent. Windows reported Classic class `0x2E4104` (Computer). The TV's accessory picker did not show THUIS during the initial test.
2. **Keyboard identity.** After UAC approval, the helper saved the two original values and applied Major `5`, Type `16`. Restarting the MediaTek adapter was sufficient. Live class became `0x2E4540`, including the expected keyboard bits `0x0540`. The remote-control class was not tested.
3. **Classic pairing.** The TV found THUIS. The initial attempt showed a failure. After the user accepted the Windows pairing dialog, Windows listed the TV as paired over Classic with A2DP audio and AVRCP media-control services. The paired LE query returned no TV. The remote reported zero HID input subscriptions.
4. **LE-only discovery test.** The open Windows Bluetooth settings page kept Classic discovery active. A separate process calling `BluetoothEnableDiscovery(FALSE)` failed to clear it. Leaving the Bluetooth settings page did clear it after a short delay. We checked both `BluetoothIsDiscoverable` (false) and the HID provider status (`STARTED`) before the TV scan. Classic discovery briefly returned between two timed windows. In the second window, with the existing Classic bond retained, the user reported that the TV could not find the remote. No HID subscription appeared. This is a local status check and a TV observation. The TV's filtering logic and the advertising packets over the air were not captured.
5. **End of session.** The Bluetooth settings page was reopened, Classic discovery verified on, and the experimental LE helper stopped. The keyboard-class override and Classic pairing were retained. No Bluetooth TV keys were sent. Brightness, discrete sleep/wake, and standby reconnection remain unverified. IR volume and the previously demonstrated IR display-wake flow are still available.

To repeat the scan, leave Windows Bluetooth settings, start the helper, confirm Classic discovery is off and LE advertising is active, and open the TV's **Pair accessory** screen. Select a new result if one appears. Check `tv_bluetooth.py --status` for a subscribed input report before sending any key. Windows documents the discovery query and the lifetime of discovery changes. [BluetoothEnableDiscovery](https://learn.microsoft.com/en-us/windows/win32/api/bluetoothapis/nf-bluetoothapis-bluetoothenablediscovery)

The original registry snapshot stays under `%LOCALAPPDATA%\tcl-ir\bluetooth-identity-backup.json` on the test PC. To undo the keyboard identity, run `tools/bluetooth_identity.py --restore` from an elevated process under the same account, restart the adapter, and read back the live class.

A possible follow-up is **Bumble with a dedicated USB Bluetooth controller**.
The [Bumble Windows instructions](https://google.github.io/bumble/platforms/windows.html)
use WinUSB instead of the Microsoft Bluetooth driver for that controller. Its
[HID keyboard example](https://github.com/google/bumble/blob/main/examples/run_hid_device.py)
implements Classic HID with SDP and HID channels, and its
[keyboard configuration](https://github.com/google/bumble/blob/main/examples/hid_keyboard.json)
sets the device class. Bumble's [device implementation](https://github.com/google/bumble/blob/main/bumble/device.py)
also controls GAP service/advertising values. This can expose a coherent
keyboard identity and actual input profile instead of changing only an icon.
It needs a separate compatible controller or an intentionally repurposed radio;
the Windows stack and Bumble cannot own the same USB interface simultaneously.
The only currently present radio is in use by the keyboard, so it was not
rebound. No additional adapter has been verified with this TV.
