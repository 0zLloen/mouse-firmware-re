# `mouse-firmware-re` — Gaming Mouse Firmware Reverse Engineering & Modding Archive

Open documentation, bootloader protocol specifications, flash memory layouts, and safe non-destructive USB inspection/flashing utilities for commercial gaming mice.

---

## Repository Structure

```text
mouse-firmware-re/
├── platforms/
│   └── compx-nrf52/                       # Compx / Sinowealth Nordic nRF52840 OEM platform (VID 0x258a)
│       ├── bootloader-protocol.md         # Resident 24 KB USB HID ISP Bootloader (258a:2103 / 258a:2113) protocol
│       └── tools/
│           ├── compx_hex_tool.py          # Intel HEX <-> binary converter, XOR-0x55 codec, and 6-check safety auditor
│           └── compx_usb_flasher.py       # Linux hidraw read-only probe, flash verifier, and application programmer
└── devices/
    └── glorious-series-one-pro/           # Glorious Forge Series One Pro Wireless (FCC ID: 2AXP2-SP22, CM2852-BAMF)
        ├── hardware.md                    # MCU (nRF52840-QIAA-D0), PAW3370 SPI2 bus, and GPIO pinout from FW disassembly
        ├── firmware-updater.md            # Analysis of official Glorious updater archives, SHA-256 hashes, and XVI.zip
        ├── flash-layout-and-swd.md        # 1 MB flash layout, pairing page (0x00088000), and passive SWD check procedure
        ├── flashing-feasibility.md        # Comparison of ZMK replacement vs. surgical stock firmware binary patching
        ├── udev/
        │   └── 99-glorious-series-one-pro.rules
        └── zmk-board/                     # Draft ZMK / Zephyr board definition derived from stock FW disassembly (untested)
```

---

## Repository Policies

1. **No Proprietary Firmware Binaries Committed**:
   - Do **not** commit vendor `.exe`, `.dll`, `.bin`, or `.hex` firmware dumps.
   - Document official package URLs, version numbers, and SHA-256 checksums so researchers can verify and extract their own local copies.
2. **Evidence Classification**:
   - Every hardware and protocol claim is explicitly labeled as **Verified** (tested on physical hardware or extracted directly from official binaries), **Inferred** (deduced from disassembly or FCC photos), or **Unknown** (requires physical probing).
3. **Bootloader & Calibration Preservation**:
   - Tools in this repository never execute mass-erase (`APPROTECT` recovery / `ERASEALL`) and strictly restrict writes to the application partition (`0x00006000`–`0x0007DFFF` on Compx nRF52840 mice), preserving both the resident 24 KB USB HID bootloader (`0x00000000`–`0x00005FFF`) and the factory 2.4 GHz radio pairing page (`0x00088000`).
