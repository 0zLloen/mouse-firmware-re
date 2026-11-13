# <Vendor> <Model> — Firmware Notes

<!--
Copy devices/_template/ to devices/<vendor>-<model>/ (lower case, hyphens), fill in device.yaml
first, then this file. Delete these HTML comments and any section you have nothing for yet,
but keep "Open questions" and "Sources".

Every claim gets a label (CONTRIBUTING.md, "Evidence rules"):
  Verified - observed on a real unit, or read literally from an official file or photo
  Inferred - your interpretation of disassembly, decompilation, captures or photos
  Unknown  - not established; say what would settle it
Cite firmware addresses as `<file name> @ 0x0000xxxx` (load address, not file offset).
Never paste serial numbers, Bluetooth/RF addresses or pairing keys.
Worked example: devices/glorious-series-one-pro/
-->

Status: <one line: how far the research got, e.g. "identified and updater extracted; nothing written to the device">

Findings use the labels defined in the repository [`CONTRIBUTING.md`](../../CONTRIBUTING.md#evidence-rules).

## 1. Identification

| Item | Value | Evidence | How |
| :--- | :--- | :--- | :--- |
| Wired application | `vvvv:pppp`, bcdDevice `0x0000`, product `"..."` | **Verified** | `lsusb -v`, YYYY-MM-DD |
| Receiver application | | | |
| Bootloader | | | |
| FCC ID | | | label / manual |
| Firmware version reported by the device or vendor app | | | |

## 2. USB interfaces and HID descriptors

<!-- One block per interface: number, class/protocol, report descriptor length and hex,
     report IDs and what each carries. Decode with hid-decode (pip install hid-tools). -->

| Interface | Class / usage | Descriptor bytes | Report IDs | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| 0 | | | | |

## 3. Hardware and pinout

<!-- PCB name/revision, chip markings -> part numbers, crystal, battery, debug pads.
     Pin maps from firmware disassembly are Inferred until probed. -->

| Function | Pin / part | Evidence | Source |
| :--- | :--- | :--- | :--- |
| MCU | | | |
| Sensor | | | |
| SPI to sensor (CS / SCK / MISO / MOSI) | | | |
| Buttons, wheel, LEDs | | | |
| Debug pads (SWD/JTAG/ISP) | | | |

## 4. Flash and RAM layout

<!-- Start, end (inclusive), purpose, and whether tools must preserve it. Mark the regions that
     exist only on the device (bootloader, pairing keys, calibration): they are not in any
     vendor package, so a bad erase loses them for good. -->

| Range | Size | Purpose | Preserve | Evidence | Source |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `0x00000000`–`0x00000FFF` | | bootloader | yes | | |

## 5. Bootloader and update protocol

<!-- How the device enters the bootloader, its USB identity, the command set (packet layouts
     with byte offsets), any wire obfuscation, whether it can read flash back, and what is
     still Unknown (erase extent, address checks). If the bootloader is shared by other
     devices, put the protocol in platforms/<family>/ and link it here. -->

## 6. Firmware versions and hashes

<!-- Never commit the files. Record where you downloaded them and how they nest. -->

| File | Version | Size (bytes) | SHA-256 | Origin / container |
| :--- | :--- | :--- | :--- | :--- |
| | | | | |

Image format: <Intel HEX / raw bin / vendor container>; base address `0x...`; encrypted / signed / compressed: <yes/no + evidence>.

## 7. Recovery

<!-- Every way back to stock you know of, and whether it was actually tried on this device.
     Backups: how you made one, what it covers, and what it does not (e.g. the bootloader). -->

| Method | Tried? | Evidence | Notes |
| :--- | :--- | :--- | :--- |
| Official updater re-flash | | | |

## 8. Open questions

- <What is Unknown, and what would settle it>

## 9. Sources

- Vendor download page(s), with access date
- FCC ID / teardown photos (link, do not re-host)
- Tools and versions used (Ghidra x.y, Wireshark x.y, ...)
