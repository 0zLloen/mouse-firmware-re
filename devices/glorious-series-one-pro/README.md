# Glorious Series One Pro Wireless — Firmware Notes

Status: identified, both official images extracted and disassembled, USB bootloader protocol exercised on a retail unit (dump, flash of a patched image, stock rollback; 2026-11-13). The 2.4 GHz receiver is mapped from its image and read-only probes. Nothing has been done over SWD.

Findings use the labels defined in [`CONTRIBUTING.md`](../../CONTRIBUTING.md#evidence-rules). [`device.yaml`](device.yaml) has the structured summary: USB identities, chips, firmware hashes, flash layout, recovery.

| File | What it covers |
| :--- | :--- |
| [`hardware.md`](hardware.md) | USB interfaces and report rates, Feature-report dispatch and the radio link on the mouse side (idle, link loss, sleep), PCB, GPIO pin map, sensor driver, DPI and button map in RAM, DPI shift, 1 ms tick, onboard profiles |
| [`receiver.md`](receiver.md) | The `258a:2031` receiver: relaying, its own commands (two of them dangerous), config page |
| [`firmware-updater.md`](firmware-updater.md) | Official packages and hashes, how they nest, the bootloader entry path |
| [`flash-layout-and-swd.md`](flash-layout-and-swd.md) | 1 MB flash map, USB read-back of a retail unit, what still needs SWD, passive SWD procedure |
| [`flashing-feasibility.md`](flashing-feasibility.md) | ZMK vs. patching stock, and the safety layers of a flash |
| [`../../platforms/compx-nrf52/bootloader-protocol.md`](../../platforms/compx-nrf52/bootloader-protocol.md) | The Compx USB HID bootloader commands |
| [`../../platforms/compx-nrf52/radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md) | The 2.4 GHz link between mouse and receiver: PHY, addressing and pairing, link layer, every record, a co-emulation of both images, and what open firmware would need |
| [`zmk-board/`](zmk-board/README.md) | Draft ZMK board (builds; no PAW3370 driver, so no motion) |
| [`re-scripts/`](re-scripts/README.md) | The capstone/Unicorn scripts and read-only probes behind the receiver and radio findings (not run in CI) |
| [`udev/`](udev/70-glorious-series-one-pro.rules) | Non-root hidraw access for the mouse, the receiver and the bootloader |

The only firmware other than stock run on this mouse so far is [open-directional-dpi](https://github.com/0zLloen/open-directional-dpi), a patch of the stock image (flashed and verified 2026-11-13).

## Open questions

Each with what would settle it:

- **Does `Erase` reach `0x88000`+ (pairing, profiles)?** **Inferred** not: the mouse still linked to its receiver after at least five `Erase` runs (2026-11-13). Settle: read `0x88000` over SWD before and after one `Erase`.
- **The bootloader's own code (`0x0`–`0x5FFF`)**: whether it clears the magic word, stays resident with `0xFF` at `0x6000`, or checks the image on `Exit`. Settle: one read-only `VerifyByAddress` at `0x0000` over USB (a one-off the owner approves; the flasher refuses it), or SWD.
- **`APPROTECT` and the SWD pad order.** Settle: the passive continuity check and the read-only `APPROTECTSTATUS` read in [`flash-layout-and-swd.md`](flash-layout-and-swd.md) section 5.
- **Boot chord of the ZMK board** (Left + Right + DPI at reset): untested on hardware; the board was never flashed. Settle: flash it, power the mouse fully down, hold the chord, connect USB, and expect `258a:2103`. The open-directional-dpi patch has its own chord, which is **Verified**: held while the mouse powered up on the cable, it enumerated as `258a:2103` (build `21ea252`, 2026-11-13).
- **GPIO pin map**: **Inferred**, except the three boot-chord pins `P1.15`, `P1.13` and `P1.00`, which read low with Left + Right + DPI held (**Verified** by the patch's boot chord, 2026-11-13; the test does not tell the three apart). Settle the rest: continuity probing.
- **The 1 ms tick** ([`hardware.md`](hardware.md) 4.2) over 2.4 GHz: **Settled / Verified** (2026-11-17). With the mouse awake and still (`0` motion reports on interface 0), reading the open-directional-dpi clock (`GET_CAPABILITIES` report bytes `28..31`, payload `20..23`) over `258a:2031` across 8 samples (`6.794 s`) counted `6,793` ticks (`999.9` ticks/s; consecutive 1.000 s intervals counted `1,000` ticks/s), matching the wired 1000.0 Hz rate.
- **Why relayed requests fail while the mouse is still** ([`receiver.md`](receiver.md) section 3): **Partially settled / Verified** (2026-11-17). With `power/control = on` and `runtime_status = active` on `258a:2031`, a still, awake mouse **does** answer relayed requests (`0x81`, `0x85`, `0x86`, group 3 `0x80`, and `GET_CAPABILITIES` in `8`–`24 ms`), but receiver-local `0x89` (`link state`, polled every 3 ms) shows brief **`40.0 ms` link drops (`01 → 00 → 01`)** every ~`0.5`–`1.8 s`. A request sent during a `40.0 ms` `00` window returns `0xA4` immediately (`4.0 ms`), and a request in flight when a drop starts returns `0xA4` after `128`–`248 ms`; a single retry ~`60 ms` later succeeds. The drops come almost only while the mouse is still (**Verified**, 2026-11-18, `rx_link_watch.py`, 300 s of normal use): 233 drops in 195.4 s still (1.19 per second, median 32 ms) against 3 in 98.5 s moving, and none in 63.3 s of steady scrolling with the mouse still ([`receiver.md`](receiver.md) section 3). Why the link drops while still remains open. Co-emulation of both images found a mechanism that drops a still mouse's link (clock drift against the host's USB SOF that the receiver's timing bits cannot correct while only the 1 ms keep-alive is sent), a candidate for these drops: [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md#45-phase-control-ack-timing-bits) 4.5.
- **DPI shift on hardware** ([`hardware.md`](hardware.md) 4.1, emulation only). Settle: assign DPI shift (for example 400) to a side button, which changes the stored stock button map, with Glorious Core or with the group 3 key assignment (`feat[4] = 3`, `feat[5] = 0`, `feat[6..15]` = profile, button code, `0`, `07 01 05 hi lo hi lo`), hold it, and read the open-directional-dpi `sensor_dpi` (`GET_CAPABILITIES` report bytes `26..27`, payload `18..19`, a build after `56afbb4`): expect the shift DPI while held and the stage DPI after release.
- **Physical buttons for indices 2..4**: **Settled / Verified** (2026-11-18). With the factory button map (read back unmodified on 2026-11-17), pressing Middle, Back and Forward in that order, twice, set bits 2, 3 and 4 of byte 0 of the boot-mouse report (mouse buttons 3, 4, 5; hidraw on interface 0 of `258a:2031`). The factory map sends mouse button `i + 1` for index `i`, so Middle, Back and Forward are indices 2, 3 and 4, as the disassembly says. Their pins (`P1.10`, `P0.09`, `P0.10`) stay **Inferred**: the test shows the index, not the pin.
- **Does this unit have a profile-switch button** (action type 8, [`hardware.md`](hardware.md) 4.3)? **Settled / Verified no** (2026-11-17): relayed group 0 `0x85` returned active profile `1`, `0x86` returned profile count `3`, and group 3 command `0x80` across all 48 slots (profiles 1..3, layers 0..1, button codes `0x01`..`0x05`, `0x14`, `0x10`, `0x11`) returned the unmodified factory map (`01 01 01..05`, `07 01 06` for `0x14`, `01 01 10..11` for the wheel) with no `0x08` action.
- **Receiver MCU part**: **Inferred** an nRF52820 (2026-11-18): the image names no peripheral that chip lacks, has no nRF52840 start-up code and keeps its magic word at the top of 32 KB of RAM ([`receiver.md`](receiver.md) 1.1). An online search found no photo or teardown of this receiver, and its FCC filing shows the mouse only. Settle: open the receiver and read the chip marking.
- **Receiver bootloader** `258a:2113`: never seen; its `Erase` extent and whether it keeps the config page `0x38000` are unknown. Nothing here flashes the receiver.
- **Wireless package flow**: **Settled / Verified** (2026-11-18, IL of `FirmwareUpdate.exe`): the wireless package updates the receiver alone and does not need the mouse plugged in; nothing counts devices ([`firmware-updater.md`](firmware-updater.md) 4.3).
- **`bcdDevice` of `258a:2103`**: not recorded. Settle: `lsusb -v -d 258a:2103` the next time the mouse is in its bootloader.

## Sources

- Official package `v1.0.0.0_20220810_Series_One_ProFirmware.zip` (URL and SHA-256 in [`device.yaml`](device.yaml); HTTP 403 since at least 2026-11-13).
- FCC ID `2AXP2-SP22`, internal photos: <https://fccid.io/2AXP2SP22>
- A retail unit on v1.0.0.0, Linux hidraw, 2026-11-12/13. Disassembly with capstone 5.0.7 and GNU objdump, emulation with Unicorn 2.1.4.
