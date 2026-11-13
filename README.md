# `mouse-firmware-re` — Gaming Mouse Firmware Reverse Engineering & Modding Archive

Open documentation, bootloader protocol specifications, flash memory layouts, and safe non-destructive USB inspection/flashing utilities for commercial gaming mice.

---

## Repository Structure

```text
mouse-firmware-re/
├── CONTRIBUTING.md                        # How to reverse-engineer a mouse safely and submit findings
├── schema/device.schema.json              # JSON Schema for every devices/*/device.yaml (checked in CI)
├── .github/                               # Issue forms (new device, finding/correction), PR checklist, CI
├── platforms/
│   └── compx-nrf52/                       # Compx / Sinowealth Nordic nRF52840 OEM platform (VID 0x258a)
│       ├── bootloader-protocol.md         # Resident USB HID ISP bootloader protocol (mouse 258a:2103: 24 KB, app at 0x6000;
│       │                                  #   receiver 258a:2113: 28 KB, app at 0x7000)
│       └── tools/
│           ├── compx_hex_tool.py          # Plain Intel HEX decode/encode and offline safety audit
│           ├── compx_usb_flasher.py       # Linux hidraw probe, full read-back verifier, and .hex programmer
│           └── test_tools.py              # Offline checks for both tools (mock bootloader, no hardware)
└── devices/
    ├── _template/                         # Copy to devices/<vendor>-<model>/: README.md skeleton + device.yaml
    └── glorious-series-one-pro/           # Glorious Forge Series One Pro Wireless (FCC ID: 2AXP2-SP22, CM2852-BAMF)
        ├── device.yaml                    # Structured summary: USB IDs, chips, firmware hashes, flash layout, recovery
        ├── hardware.md                    # MCU (nRF52840-QIAA-D0), PAW3370 SPI2 bus, and GPIO pinout from FW disassembly
        ├── firmware-updater.md            # Analysis of official Glorious updater archives, SHA-256 hashes, and XVI.zip
        ├── flash-layout-and-swd.md        # 1 MB flash layout, pairing page (0x00088000), and passive SWD check procedure
        ├── flashing-feasibility.md        # Comparison of ZMK replacement vs. surgical stock firmware binary patching
        ├── udev/
        │   └── 70-glorious-series-one-pro.rules
        └── zmk-board/                     # Draft ZMK board (HWMv2, ZMK main); builds, but no PAW3370 driver exists yet
```

---

## Contributing

Reverse-engineered a mouse, or want to? [`CONTRIBUTING.md`](CONTRIBUTING.md) walks through it as a safety ladder, from reading USB descriptors to writing flash, and explains how to submit what you find:

- copy [`devices/_template/`](devices/_template/) to `devices/<vendor>-<model>/` and fill in `device.yaml` (validated against [`schema/device.schema.json`](schema/device.schema.json)) and `README.md`, then open a pull request;
- or, without a pull request, open a [New device report](https://github.com/0zLloen/mouse-firmware-re/issues/new?template=new-device.yml) or a [Finding or correction](https://github.com/0zLloen/mouse-firmware-re/issues/new?template=finding.yml) issue.

---

## Repository Policies

1. **No Proprietary Firmware Binaries Committed**:
   - Do **not** commit vendor `.exe`, `.dll`, `.bin`, or `.hex` firmware dumps.
   - Document official package URLs, version numbers, and SHA-256 checksums so researchers can verify and extract their own local copies.
2. **Evidence Classification**:
   - Every claim is labelled as defined in [CONTRIBUTING.md](CONTRIBUTING.md#evidence-rules):
     - **Verified**: observed directly on a real unit (USB enumeration, a read-back, a measurement), or read literally from an official file or photo (a hash, a record address, a string, a constant, a chip marking).
     - **Inferred**: your interpretation of disassembly, decompilation, USB captures or photos: what a pin does, what a command means, how a bootloader behaves.
     - **Unknown**: not established yet; say what would settle it.
3. **Bootloader & Calibration Preservation**:
   - Tools in this repository never execute mass-erase (`APPROTECT` recovery / `ERASEALL`). `Program` writes are limited to the application partition (`0x00006000`–`0x0007DFFF` on the mouse), so they never touch the resident 24 KB USB HID bootloader (`0x00000000`–`0x00005FFF`) or the factory 2.4 GHz pairing page (`0x00088000`).
   - The flasher has to send the bootloader's parameterless `Erase`, whose extent is defined by the bootloader and is **Unknown**. It may also clear the `0x7E000` settings page and the `0x88000`+ pairing/profile pages; measure that before relying on them surviving a reflash.

---

## Quick Start (Glorious Series One Pro)

```bash
cd platforms/compx-nrf52/tools
python3 test_tools.py                                   # offline checks, no hardware
python3 compx_hex_tool.py audit  CM2856_Mouse_USB_v1.00.00_20220810.hex
python3 compx_hex_tool.py decode CM2856_Mouse_USB_v1.00.00_20220810.hex -o CM2856_Mouse_USB_v1.00.00_20220810.bin
python3 compx_usb_flasher.py probe
python3 compx_usb_flasher.py verify-bl --reference-hex CM2856_Mouse_USB_v1.00.00_20220810.hex
python3 compx_usb_flasher.py flash --firmware-hex CM2856_Mouse_USB_v1.00.00_20220810.hex   # stock rollback
```

The vendor `.hex` files are plain Intel HEX; the `0x55` XOR exists only on the USB wire. `flash` refuses any `.hex` whose lowest address is not `0x6000`, programs the vector-table block at `0x6000` last, and sends `Exit` only after every byte reads back correctly. Install `devices/glorious-series-one-pro/udev/70-glorious-series-one-pro.rules` for non-root hidraw access.
