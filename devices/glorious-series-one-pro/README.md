# Glorious Series One Pro Wireless — Firmware Notes

Status: identified, both official images extracted and disassembled, USB bootloader protocol exercised on a retail unit (dump, flash of a patched image, stock rollback; 2026-11-13). The 2.4 GHz receiver is mapped from its image and read-only probes. Nothing has been done over SWD.

Findings use the labels defined in [`CONTRIBUTING.md`](../../CONTRIBUTING.md#evidence-rules). [`device.yaml`](device.yaml) has the structured summary: USB identities, chips, firmware hashes, flash layout, recovery.

| File | What it covers |
| :--- | :--- |
| [`hardware.md`](hardware.md) | USB interfaces and report rates, Feature-report dispatch and the radio link on the mouse side, PCB, GPIO pin map, sensor driver, DPI in RAM, 1 ms tick |
| [`receiver.md`](receiver.md) | The `258a:2031` receiver: relaying, its own commands (two of them dangerous), config page |
| [`firmware-updater.md`](firmware-updater.md) | Official packages and hashes, how they nest, the bootloader entry path |
| [`flash-layout-and-swd.md`](flash-layout-and-swd.md) | 1 MB flash map, USB read-back of a retail unit, what still needs SWD, passive SWD procedure |
| [`flashing-feasibility.md`](flashing-feasibility.md) | ZMK vs. patching stock, and the safety layers of a flash |
| [`../../platforms/compx-nrf52/bootloader-protocol.md`](../../platforms/compx-nrf52/bootloader-protocol.md) | The Compx USB HID bootloader commands |
| [`zmk-board/`](zmk-board/README.md) | Draft ZMK board (builds; no PAW3370 driver, so no motion) |
| [`re-scripts/`](re-scripts/README.md) | The capstone/Unicorn scripts and read-only probes behind the receiver and radio findings (not run in CI) |
| [`udev/`](udev/70-glorious-series-one-pro.rules) | Non-root hidraw access for the mouse, the receiver and the bootloader |

The only firmware other than stock run on this mouse so far is [open-directional-dpi](https://github.com/0zLloen/open-directional-dpi), a patch of the stock image (flashed and verified 2026-11-13).

## Open questions

Each with what would settle it:

- **Does `Erase` reach `0x88000`+ (pairing, profiles)?** **Inferred** not: the mouse still linked to its receiver after at least five `Erase` runs (2026-11-13). Settle: read `0x88000` over SWD before and after one `Erase`.
- **The bootloader's own code (`0x0`–`0x5FFF`)**: whether it clears the magic word, stays resident with `0xFF` at `0x6000`, or checks the image on `Exit`. Settle: one read-only `VerifyByAddress` at `0x0000` over USB (a one-off the owner approves; the flasher refuses it), or SWD.
- **`APPROTECT` and the SWD pad order.** Settle: the passive continuity check and the read-only `APPROTECTSTATUS` read in [`flash-layout-and-swd.md`](flash-layout-and-swd.md) section 5.
- **Boot chord** (Left + Right + DPI at reset) of the open-directional-dpi patch and the ZMK board: untested on hardware. Settle: power the patched mouse fully down, hold the chord, connect USB, and expect `258a:2103`.
- **GPIO pin map** (all **Inferred**). Settle: continuity probing, or the boot-chord test above for the three button pins.
- **The 1 ms tick** ([`hardware.md`](hardware.md) 4.2) is **Inferred**; the sensor-read rate while the mouse is still was not measured. Settle: a diagnostic build that reports a call counter.
- **Receiver MCU part** (nRF52840 per the updater, 32 KB of RAM used). Settle: a photo of the receiver PCB.
- **Receiver bootloader** `258a:2113`: never seen; its `Erase` extent and whether it keeps the config page `0x38000` are unknown. Nothing here flashes the receiver.
- **Wireless package flow**: whether `FirmwareUpdate.exe` needs the mouse plugged in next to the receiver (`deviceList.Count == 2`). Settle: re-read `Form1::StartUpdateThread()`.
- **`bcdDevice` of `258a:2103`**: not recorded. Settle: `lsusb -v -d 258a:2103` the next time the mouse is in its bootloader.

## Sources

- Official package `v1.0.0.0_20220810_Series_One_ProFirmware.zip` (URL and SHA-256 in [`device.yaml`](device.yaml); HTTP 403 since at least 2026-11-13).
- FCC ID `2AXP2-SP22`, internal photos: <https://fccid.io/2AXP2SP22>
- A retail unit on v1.0.0.0, Linux hidraw, 2026-11-12/13. Disassembly with capstone 5.0.7 and GNU objdump, emulation with Unicorn 2.1.4.
