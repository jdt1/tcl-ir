# Third-party notices

The USB framing, handshake, pulse-compression, and learning algorithms in
`src/ir_control.py` are based on protocol research and MIT-licensed reference
code from [deadboy18/Ocrustar-USB-IR](https://github.com/deadboy18/Ocrustar-USB-IR).

The repository is an independent interoperability project and is not affiliated
with Ocrustar, ElkSmart, or the reference implementation's authors.

The experimental Bluetooth backend uses Microsoft Windows Runtime APIs and
standard Bluetooth/USB HID usage definitions. The feasibility research and
Android reconnection limitations were informed by the MIT-licensed
[abhishek-raj/windows-ble-hid](https://github.com/abhishek-raj/windows-ble-hid)
project. Its application code is not bundled here. Primary API and input-mapping
references are recorded in `FINDINGS.md`.
