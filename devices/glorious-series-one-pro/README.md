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
- **The 1 ms tick** ([`hardware.md`](hardware.md) 4.2) over 2.4 GHz. On the cable the sensor is read 1000.0 times a second while the mouse is still (**Verified** 2026-11-13). Over the radio it was not measured, because relayed requests failed while the mouse was still (next question). **Inferred** the same: `TIMER1` runs in every awake radio state (linked, retrying, searching) and stops only on the way into System OFF (disassembly and emulation, 2026-11-13). Settle: once the mouse stays reachable while still, read the open-directional-dpi clock (`GET_CAPABILITIES` bytes `20..23`) once a second over 2.4 GHz; expect 1000 per second.
- **Why relayed requests fail while the mouse is still** ([`receiver.md`](receiver.md) section 3). On a retail mouse every request ended in `0xA4` while the mouse was still or only scrolling, and went through while it moved (**Verified** 2026-11-13). The cause is **Unknown**: in emulation a still, awake mouse keeps the link up with a 1 ms keep-alive, and the code-backed ways to become unreachable (System OFF, or a link loss of 256 ms or more) do not explain why scrolling did not help. Co-emulation of both images found a mechanism that drops a still mouse's link (clock drift against the host's USB SOF that the receiver's timing bits cannot correct while only the 1 ms keep-alive is sent). It may explain part of this, but not the scrolling case: [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md#45-phase-control-ack-timing-bits) 4.5. Settle, all host-side and read-only towards the mouse:
  1. Leave the mouse still for 5 s, 30 s and 3 min. Each time, query the receiver's link state (`0x89`), then send one relayed `GET_CAPABILITIES`, and note when `0xA4` appears. `00` with an immediate `0xA4`: the mouse is not linked (asleep or link lost). `01` with `0xA4` after about 256 ms: the link drops during the request. `01` and an answer: contradicts the earlier result.
  2. Repeat 1 while only scrolling, and once after a single click with no movement.
  3. Note the sleep time stored on the mouse (config u16 at offset 3, factory 420 s; set in Glorious Core).
  4. Note the receiver's runtime power management during the still period: `power/control` and `power/runtime_status` under `/sys/bus/usb/devices` for `258a:2031`. If `control` is `auto`, repeat with `on`. This tests the sleep record the receiver sends on USB suspend.
  5. If the mouse stays linked while still, read `GET_CAPABILITIES` bytes `20..23` once a second over 2.4 GHz (the 1 ms tick question above).
- **DPI shift on hardware** ([`hardware.md`](hardware.md) 4.1, emulation only). Settle: assign DPI shift (for example 400) to a side button, which changes the stored stock button map, with Glorious Core or with the group 3 key assignment (`feat[4] = 3`, `feat[5] = 0`, `feat[6..15]` = profile, button code, `0`, `07 01 05 hi lo hi lo`), hold it, and read the open-directional-dpi `sensor_dpi` (`GET_CAPABILITIES` bytes `18..19`, a build after `56afbb4`): expect the shift DPI while held and the stage DPI after release.
- **Physical buttons for indices 2..4** (`P1.10`, `P0.09`, `P0.10`; Middle, Back, Forward from disassembly). Settle: the factory map sends mouse button `i + 1` for index `i`, so press each button and read which button bit the boot-mouse report sets (`evtest` or hidraw).
- **Does this unit have a profile-switch button** (action type 8, [`hardware.md`](hardware.md) 4.3)? The factory map has none, but vendor software can map one. Settle on the cable, as a one-off the owner approves: group 3 command `0x80` (read-only by disassembly and emulation) for profiles 1..3, button codes `0x01`..`0x05`, `0x14`, `0x10` and `0x11`, layers 0 and 1; a reply with `body[9] = 0x08` means yes.
- **Receiver MCU part** (nRF52840 per the updater, 32 KB of RAM used). Settle: a photo of the receiver PCB.
- **Receiver bootloader** `258a:2113`: never seen; its `Erase` extent and whether it keeps the config page `0x38000` are unknown. Nothing here flashes the receiver.
- **Wireless package flow**: whether `FirmwareUpdate.exe` needs the mouse plugged in next to the receiver (`deviceList.Count == 2`). Settle: re-read `Form1::StartUpdateThread()`.
- **`bcdDevice` of `258a:2103`**: not recorded. Settle: `lsusb -v -d 258a:2103` the next time the mouse is in its bootloader.

## Sources

- Official package `v1.0.0.0_20220810_Series_One_ProFirmware.zip` (URL and SHA-256 in [`device.yaml`](device.yaml); HTTP 403 since at least 2026-11-13).
- FCC ID `2AXP2-SP22`, internal photos: <https://fccid.io/2AXP2SP22>
- A retail unit on v1.0.0.0, Linux hidraw, 2026-11-12/13. Disassembly with capstone 5.0.7 and GNU objdump, emulation with Unicorn 2.1.4.
