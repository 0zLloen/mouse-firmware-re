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
│       ├── radio-protocol.md              # 2.4 GHz mouse-receiver link (258a:2031): PHY, pairing, link layer, records,
│       │                                  #   co-emulation of both images, what open firmware needs
│       └── tools/
│           ├── compx_hex_tool.py          # Plain Intel HEX decode/encode and offline safety audit
│           ├── compx_usb_flasher.py       # Probe, full read-back verifier, and .hex programmer (Linux hidraw;
│           │                              #   Windows/macOS through the PyPI package hidapi, untested)
│           └── test_tools.py              # Offline checks for both tools (mock bootloader, no hardware)
└── devices/
    ├── _template/                         # Copy to devices/<vendor>-<model>/: README.md skeleton + device.yaml
    └── glorious-series-one-pro/           # Glorious Forge Series One Pro Wireless (FCC ID: 2AXP2-SP22, CM2852-BAMF)
        ├── README.md                      # Status, file list and open questions
        ├── device.yaml                    # Structured summary: USB IDs, chips, firmware hashes, flash layout, recovery
        ├── hardware.md                    # MCU, USB interfaces and report rates, Feature-report dispatch and 2.4 GHz link
        │                                  #   (mouse side), PAW3370 SPI2 bus, GPIO pinout from FW disassembly
        ├── receiver.md                    # 2.4 GHz receiver 258a:2031: relaying, its own commands (two dangerous), config page
        ├── firmware-updater.md            # Analysis of official Glorious updater archives, SHA-256 hashes, and XVI.zip
        ├── flash-layout-and-swd.md        # 1 MB flash layout, pairing page (0x00088000), and passive SWD check procedure
        ├── flashing-feasibility.md        # Comparison of ZMK replacement vs. surgical stock firmware binary patching
        ├── re-scripts/                    # RE scripts behind the receiver and RF findings (capstone/Unicorn, hardware
        │                                  #   probes; CI only compiles them)
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
   - Never publish an image that contains vendor code, such as a patched stock image, anywhere: not in the repository, a release, an issue or a discussion. Describe how to build it from the user's own copy of the official image instead.
2. **Evidence Classification**:
   - Every claim is labelled as defined in [CONTRIBUTING.md](CONTRIBUTING.md#evidence-rules):
     - **Verified**: observed directly on a real unit (USB enumeration, a read-back, a measurement), or read literally from an official file or photo (a hash, a record address, a string, a constant, a chip marking).
     - **Inferred**: your interpretation of disassembly, decompilation, USB captures or photos: what a pin does, what a command means, how a bootloader behaves.
     - **Unknown**: not established yet; say what would settle it.
3. **Bootloader & Calibration Preservation**:
   - Tools in this repository never execute mass-erase (`APPROTECT` recovery / `ERASEALL`). `Program` writes are limited to `0x00006000`–`0x0004D7FF` on the mouse (the top `VerifyByAddress` can read back; `compx_usb_flasher.py` refuses larger images), so they never touch the resident 24 KB USB HID bootloader (`0x00000000`–`0x00005FFF`) or the factory 2.4 GHz pairing page (`0x00088000`).
   - The flasher has to send the bootloader's parameterless `Erase`. On the Series One Pro it clears `0x1E000` up to at least `0x4D7FF` and does **not** clear the `0x7E000` settings page (both verified); whether it reaches the `0x88000`+ pairing/profile pages is **Unknown**.

---

## Quick Start (Glorious Series One Pro)

```bash
cd platforms/compx-nrf52/tools
python3 test_tools.py                                   # offline checks, no hardware
python3 compx_hex_tool.py audit  CM2856_Mouse_USB_v1.00.00_20220810.hex
python3 compx_hex_tool.py decode CM2856_Mouse_USB_v1.00.00_20220810.hex -o CM2856_Mouse_USB_v1.00.00_20220810.bin
python3 compx_usb_flasher.py probe
python3 compx_usb_flasher.py dump --out backup.hex          # read-only backup of 0x6000-0x4D7FF; keep it private
python3 compx_usb_flasher.py verify-bl --reference-hex CM2856_Mouse_USB_v1.00.00_20220810.hex
python3 compx_usb_flasher.py flash --firmware-hex CM2856_Mouse_USB_v1.00.00_20220810.hex   # stock rollback
```

Run `dump` before any `flash`: the vendor package is no longer downloadable, so a firmware version you have not archived is lost once it is overwritten. The backup covers `0x6000`–`0x4D7FF` only; the settings, pairing and profile pages above it are not readable over USB.

The vendor `.hex` files are plain Intel HEX; the `0x55` XOR exists only on the USB wire. `flash` refuses any `.hex` whose lowest address is not `0x6000` or that ends above `0x4D800`, and any bootloader whose `GetBLFW` is not `b0 01 00 00` (the only one tested), before it erases anything. It programs the vector-table block at `0x6000` last, and sends `Exit` only after every byte reads back correctly. Install `devices/glorious-series-one-pro/udev/70-glorious-series-one-pro.rules` for non-root hidraw access. Windows and macOS (untested): `pip install hidapi`, then only `probe` until the bootloader's report descriptor is recorded; see [CONTRIBUTING.md](CONTRIBUTING.md#6-read-back-and-backup).

Firmware for this mouse: [open-directional-dpi](https://github.com/0zLloen/open-directional-dpi) patches the stock image (per-direction sensitivity). It was flashed with `compx_usb_flasher.py` and verified on a retail unit, over the cable and through the 2.4 GHz receiver (2026-11-13). The draft ZMK board in `zmk-board/` builds but has no sensor driver yet.
