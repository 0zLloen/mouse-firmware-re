# Glorious Series One Pro 2.4 GHz Receiver (`258a:2031`)

How the receiver handles the vendor Feature reports on interface 2: which requests it relays to the mouse, which it answers itself, and the two local commands that change it. The mouse side of the radio link is in [`hardware.md`](hardware.md) section 2.3.

Labels follow [`CONTRIBUTING.md`](../../CONTRIBUTING.md#evidence-rules). Firmware addresses are load addresses in `CM2856_Receiver_USB_v1.00.00_20220810.hex` (receiver v1.00.00, SHA-256 in [`device.yaml`](device.yaml)). The scripts behind these results are in [`re-scripts/`](re-scripts/README.md).

> **Never send the receiver its own commands `0x00` or `0x0A`** (a request with `body[2] != 0x02`, `body[4] = 0x00` and `body[5] = 0x00` or `0x0A`). `0x00` with mode `0xB0` puts the receiver into its bootloader; `0x0A` rewrites the stored RF ID, which unpairs the mouse. Relayed requests (`body[2] = 0x02`) are safe from this: they never reach the receiver's own handlers.

## 1. Identity and image

| Item | Value | Evidence |
| :--- | :--- | :--- |
| Application | `258a:2031`, bcdDevice `0x0100`, product `Series One PRO Wireless`; three HID interfaces with the same report descriptors as the wired mouse (91 / 163 / 20 bytes) | **Verified** (`lsusb -v` and sysfs, 2026-11-12/13) |
| Bootloader | `258a:2113` | **Inferred** (`FirmwareUpdate.exe` constant and the wireless `Config.ini` `BLPID = 2113`; never seen on USB) |
| Official image | `0x00007000`–`0x0000EE03` (32,260 B), 2,020 HEX records, initial SP `0x200026E0`, `Reset_Handler` `0x000074C1` | **Verified** (`compx_hex_tool.py decode --target receiver`, 2026-11-13; SP and `Reset_Handler` are the first two little-endian words of the decoded image) |
| MCU | nRF52 series with USB. The updater calls it an nRF52840 (`C52840Dongle`, `Config.ini` `ChipName = 00052840`), but the image uses only 32 KB of RAM (stack top `0x200026E0`, magic word at `0x20007FFC`) and no flash above `0x38FFF`, which also fits a 256 KB / 32 KB part such as the nRF52820. | **Inferred**; a photo of the receiver PCB would settle it |

## 2. Request routing

The host writes a 64-byte Feature report (report ID `0`) and polls it back with GET_REPORT. The receiver decides from the request itself (`@ 0x0000C9B6`, buffer `0x2000016C`):

| Request | What happens | Evidence |
| :--- | :--- | :--- |
| `body[2] = 0x02` | Relayed to the mouse (section 3). If the link is down (`0x20000056 = 0`) it answers `0xA4` at once (`@ 0x0000C9E8`). | **Verified** (relay, `rx_query.py`, `rx_checks.py`, 2026-11-13); address **Inferred** |
| `body[2] != 0x02`, `body[4] = 0x00` | A receiver-local command: the switch at `0x0000C73C` on `body[5]` (section 4). `body[0]` reads `0xA1` afterwards unless the handler set another status. | **Verified** for `0x81` and `0x89`; the switch **Inferred** |
| `body[2] != 0x02`, `body[4] != 0x00` | `0xA3`, no handler runs (`@ 0x0000C9C4`). | **Verified** (`rx_checks.py`, 2026-11-13) |

`body[4]` is the command group on the mouse too: the stock mouse rejects `body[4] >= 5` before any handler ([`hardware.md`](hardware.md) section 2.3). The open-directional-dpi protocol puts its sequence number (`5..255`) there, so its requests can reach neither the receiver's own handlers nor a stock mouse command, even without the relay envelope.

## 3. Relay to the mouse

All **Verified** on a retail receiver and mouse, 2026-11-13, unless marked:

- The receiver sends `body[3] + 6` bytes of the request over the radio in 8-byte chunks (**Inferred** for the chunking: receiver and mouse disassembly, and `rfemu.py` on the mouse image).
- While the reply is pending, GET_REPORT returns `body[0] = 0xA0`. A relayed version query answers in about 8 ms.
- It copies back as many reply bytes as the **request** asked for (`body[3] + 6`), not as many as the mouse sent. The rest of the 64 bytes is stale. The stock version query (`body[3] = 0x06`) therefore returns 12 bytes; with `body[3] = 0x0A` it returns 16.
- `0xA4` means the mouse cannot be reached. It can be transient: a `SAVE_FLASH` of the open-directional-dpi patch erases a flash page, which stalls the mouse's radio. The receiver then answers `0xA4` after about 270 ms, and the real reply still arrives after about 770 ms (`rf_save_probe.py`). Hosts poll to a deadline and treat only a final `0xA4` as an unreachable mouse.
- While the mouse sends no reports (still, or only scrolling) the receiver answers `0xA4`, so requests do not reach it (2026-11-13, open-directional-dpi `21ea252`).
- With build `21ea252` a `SAVE_FLASH` of a changed profile answered in 402 ms, and an identical second one in 24 ms with no page erase and no `0xA4`: the patch skips an unchanged save. A profile saved through the receiver survived switching the mouse off and on, and its curve applied over the radio (up/down count ratio 0.681 with up at 0.5x, against 0.938 flat in the earlier `wl_capture.py` test).
- The mouse sends `reply[3] + 6` bytes back. An open-directional-dpi reply keeps its status (`0xA1`..`0xA4`) in byte 3, so the stock radio code would send more than 64 bytes for it; the patch caps the length at 64 (**Inferred**: emulation, `rfemu.py`).

## 4. Receiver-local commands

`body[2] != 0x02`, `body[4] = 0x00`, command in `body[5]`, data from `body[6]`. Switch at `0x0000C73C`; config RAM copy at `0x20000148`.

| `body[5]` | Name | What it does | Safe to send | Evidence |
| :---: | :--- | :--- | :---: | :--- |
| `0x81` | Version | Replies `body[3] = 0x04`, `body[6..11] = 01 00 00 00 20 31` (v1.0.0.0, PID `0x2031`) (`@ 0x0000C762`) | Yes | **Verified** reply, 2026-11-13 |
| `0x89` | Link state | `body[6]` = `0x20000056`: `01` while the mouse is linked (`@ 0x0000C7E6`) | Yes | **Verified** (`01` with the mouse on), 2026-11-13 |
| `0x8A` | RF ID read | `body[6..9]` = config bytes `+2..+5` (`@ 0x0000C800`). Unit-specific: redact it in anything you publish. | Read-only | **Inferred** (not sent) |
| `0x00` | Set mode | Mode in `body[6]`. `0xB0` stores `0xA55AA55A` at `0x20007FFC` (`@ 0x0000C7A8`) and resets (`0x00007AF0`, `AIRCR`): the receiver bootloader `258a:2113`. `0xC0` resets; `0xA0` copies 9 bytes into radio state (not traced). | **No** | **Inferred** (not sent) |
| `0x0A` | RF ID write | Stores `body[6..9]` at config `+2` and sets the save flag (`@ 0x0000C7EE`); the main loop then rewrites the config page (section 5). Unpairs the mouse. | **No** | **Inferred** (not sent) |
| other | — | `0xA3`, the rest of the request echoed (`@ 0x0000C75C`). `0x83` (battery) is one of them. | Yes | **Verified** for `0x83`, 2026-11-13 |

The application-to-bootloader packet of the wired mouse (`body[3] = 0x01`, `body[5] = 0x00`, `body[6] = 0xB0`; [`bootloader-protocol.md`](../../platforms/compx-nrf52/bootloader-protocol.md) section 1) is therefore **not** relayed when sent to `258a:2031` with `body[2] = 0x00`: the receiver runs it itself and enters its own bootloader. No tool in this repository sends anything to `258a:2113`.

## 5. Config page `0x00038000`

- `0x000097BC` loads `0x24` bytes from `0x00038000` into `0x20000148`; `0x0000C6FC` writes them back when the save flag (bit 1 of `0x20000000`) is set: CRC over `0x22` bytes stored at `+0x22`, page erase, `0x24`-byte write. The 4-byte RF ID is at `+2`. (**Inferred**, disassembly.)
- It holds the pairing with the mouse and is not in the official image. Treat it like the mouse's `0x88000` page: never erase or publish it.
- Whether the receiver bootloader's `Erase` reaches `0x38000` is **Unknown**. The receiver has never been flashed here.

## 6. Open questions

- The receiver bootloader (`258a:2113`): never seen enumerating; its `Erase` extent and address limits are untested.
- The exact MCU part (section 1).
- Mode values other than `0xB0` (`0xA0`, `0xC0`) and the link-state values other than `01`.
