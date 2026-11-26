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
| MCU | nRF52 series with USB. The updater calls it an nRF52840 (`C52840Dongle`, `Config.ini` `ChipName = 00052840`), but the image uses only 32 KB of RAM (stack top `0x200026E0`, magic word at `0x20007FFC`) and no flash above `0x38FFF`, which also fits a 256 KB / 32 KB part such as the nRF52820. The image was not built for an nRF52840: it names no peripheral the nRF52820 lacks, and it has none of the nRF52840 `SystemInit()` errata literals that the mouse image has at `0x6B8C`–`0x6BA0` (NFCT `0x4000568C`, TEMP `0x4000C520`, QSPI `0x40029640`), and no GPIO P1 (section 1.1). | **Inferred**: probably an nRF52820; a photo of the receiver PCB would settle it |

### 1.1 Which chip (2026-11-18)

- **Verified** (read from the official images with [`re-scripts/periph_scan.py`](re-scripts/README.md)): the receiver image's literals name only POWER/CLOCK, RADIO, GPIOTE, TIMER0, TIMER1, TIMER3, RTC0, RTC1, WDT, NVMC, PPI, USBD, FICR and GPIO P0. All of these exist on the nRF52820. The mouse image, run through the same scan, names GPIO P1, NFCT, SAADC, PWM0, PWM1, SPIM2, QSPI and TIMER4, none of which the nRF52820 has.
- **Inferred**: the receiver is an nRF52820. It is the only nRF52 with USB and 32 KB of RAM (the nRF52833 has 128 KB, the nRF52840 256 KB), and both images put the bootloader magic word in the top word of their RAM (`0x2003FFFC` on the mouse, `0x20007FFC` here). An image built for an nRF52820 also runs on the larger parts, so this does not rule out an nRF52833 or nRF52840 on the board. The updater's `C52840Dongle` and `ChipName = 00052840` are shared with the mouse package and say nothing about the receiver.
- Online search, 2026-11-18. Nothing public shows this receiver's board:
  - The FCC filing `2AXP2-SP22` covers the mouse only; its internal photos (photos 9–14) show the mouse PCBs and the battery, no receiver. None of the 34 FCC IDs of grantee `2AXP2` (<https://fccid.io/2AXP2>) is named as a Series One Pro receiver.
  - Glorious sells no replacement: its "Wireless Mouse Receiver Kit (PRO Series)" is for the "V2 PRO mice" and is listed as "Not compatible with Forge PRO mice" (<https://www.gloriousgaming.com/products/wireless-mouse-receiver-kit-pro-series>).
  - `2AXP2-DK22` ("Dongle Kit V2", 2405–2476 MHz) is a different board for other mice.
  - Nordic's 2021 press release on another Glorious mouse, the Model O Wireless, names an nRF52840 for the mouse and an nRF52820 for its receiver (<https://www.nordicsemi.com/Nordic-news/2021/10/Glorious-Model-O-Wireless-mouse-and-PC-uses-nRF52840-and-nRF52820>). It does not mention the Series One Pro.
  - No teardown, firmware analysis or protocol notes for `258a:2031` / `258a:2113` were found.

## 2. Request routing

The host writes a 64-byte Feature report (report ID `0`) and polls it back with GET_REPORT. The receiver decides from the request itself (`@ 0x0000C9B6`, buffer `0x2000016C`):

| Request | What happens | Evidence |
| :--- | :--- | :--- |
| `body[2] = 0x02` | Relayed to the mouse (section 3). If the link is down (`0x20000056 = 0`) it answers `0xA4` at once (`@ 0x0000C9E8`). | **Verified** (relay, `rx_query.py`, `rx_checks.py`, 2026-11-13); address **Inferred** |
| `body[2] != 0x02`, `body[4] = 0x00` | A receiver-local command: the switch at `0x0000C73C` on `body[5]` (section 4). `body[0]` reads `0xA1` afterwards unless the handler set another status. | **Verified** for `0x81` and `0x89`; the switch **Inferred** |
| `body[2] != 0x02`, `body[4] != 0x00` | `0xA3`, no handler runs (`@ 0x0000C9C4`). | **Verified** (`rx_checks.py`, 2026-11-13) |

`body[4]` is the command group on the mouse too: the stock mouse rejects `body[4] >= 5` before any handler ([`hardware.md`](hardware.md) section 2.3). The open-directional-dpi protocol puts its sequence number (`5..255`) there, so its requests can reach neither the receiver's own handlers nor a stock mouse command, even without the relay envelope.

## 3. Relay to the mouse

How the request and reply travel over the radio, byte by byte: [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md#54-a-relayed-feature-request-end-to-end) 5.4.

All **Verified** on a retail receiver and mouse, 2026-11-13, unless marked:

- The receiver sends `body[3] + 6` bytes of the request over the radio in 8-byte chunks (**Inferred** for the chunking: receiver and mouse disassembly, and `rfemu.py` on the mouse image).
- While the reply is pending, GET_REPORT returns `body[0] = 0xA0`. A relayed version query answers in about 8 ms.
- It copies back as many reply bytes as the **request** asked for (`body[3] + 6`), not as many as the mouse sent. The rest of the 64 bytes is stale. The stock version query (`body[3] = 0x06`) therefore returns 12 bytes; with `body[3] = 0x0A` it returns 16.
- `0xA4` means the mouse cannot be reached. It can be transient: a `SAVE_FLASH` of the open-directional-dpi patch erases a flash page, which stalls the mouse's radio. The receiver then answers `0xA4` after about 270 ms, and the real reply still arrives after about 770 ms (`rf_save_probe.py`). Hosts poll to a deadline and treat only a final `0xA4` as an unreachable mouse; open-directional-dpi hosts then check the link (`0x89`, section 4) and send the request once more (host code, checked against fakes only; not yet run on hardware).
- While the mouse is awake and still (`0` motion reports on interface 0, receiver `power/control = on`, `runtime_status = active`), it **does** answer relayed requests (`0x81`, `0x85`, `0x86`, group 3 `0x80`, and `GET_CAPABILITIES` in `8`–`24 ms`), but receiver-local `0x89` (`link state`, polled every 3 ms) shows brief **`40.0 ms` link drops (`01 → 00 → 01`)** every ~`0.5`–`1.8 s` (**Verified**, 2026-11-17). A relayed request sent during a `40.0 ms` `00` window returns `0xA4` immediately (`4.0 ms`), and a multi-chunk request/reply interrupted by a drop returns `0xA4` after `128`–`248 ms`; retrying once ~`60 ms` later succeeds in `20.0 ms`. Why the link drops for `40.0 ms` every ~`0.5`–`1.8 s` while still is **Unknown**.
- The drops depend on motion (**Verified**, 2026-11-18, [`re-scripts/rx_link_watch.py`](re-scripts/README.md): `0x89` polled every 3 ms for 300 s of normal desktop use, with the input reports of interface 0 read alongside; `power/control = on`, `runtime_status = active`, every poll answered):

  | Mouse in the 300 ms before | Time | Drops | Rate | Length |
  | :--- | :--- | :--- | :--- | :--- |
  | Still (no report) | 195.4 s | 233 | 1.19 per second | 24–632 ms, median 32 ms |
  | Moving (X/Y reports) | 98.5 s | 3 | 0.03 per second | 88, 304 and 624 ms |
  | Buttons only | 6.0 s | 0 | | |
  | Scrolling only (second run, 75 s, wheel rolled steadily) | 63.3 s | 0 | 0 per second | |

  In the second run the 8.6 s counted as still (pauses in the scrolling) had 6 drops of 24–40 ms. So wheel reports keep the link up as motion reports do. A poll round trip takes about 4 ms plus the 3 ms pause, so lengths are good to about 8 ms (the 32 ms median is the `40.0 ms` above). A drop is counted under what the mouse did in the 300 ms before the flag fell, so the three "moving" drops may have begun just after the mouse stopped; they are also longer than the still ones. This is what the drift mechanism of [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md#45-phase-control-ack-timing-bits) 4.5 predicts (drops while still, none while moving or scrolling); it does not prove that mechanism.
- **On a second host there are no drops** (**Verified**, 2026-11-19, `rx_link_watch.py` on another PC with the same mouse and receiver): 60.0 s still, 0 drops; then 60 s of use (41.3 s moving, 6.8 s scrolling, 10.7 s buttons, 1.1 s still), 0 drops; every poll answered. So the drops depend on the host. This fits drift against the first host's SOF clock; a difference in the host's USB controller or power management is not ruled out.
- With the mouse on its USB cable (`258a:2018` enumerated next to the receiver), the receiver reports no link: `0x89` returned `00` and a relayed version query returned `0xA4` in 4 ms (**Verified**, 2026-11-18, `rx_checks.py`).
- With build `21ea252` a `SAVE_FLASH` of a changed profile answered in 402 ms, and an identical second one in 24 ms with no page erase and no `0xA4`: the patch skips an unchanged save. A profile saved through the receiver survived switching the mouse off and on, and its curve applied over the radio (up/down count ratio 0.681 with up at 0.5x, against 0.938 flat in the earlier `wl_capture.py` test).
- **Inferred** (receiver disassembly and Unicorn emulation of the receiver image, 2026-11-13), when `0xA4` comes and what happens to a request:
  - There is no per-request timeout. A relayed request gets `0xA4` only (a) at once, when the link flag `0x20000056` is already `0` (`@ 0x0000C9C8`–`0x0000C9EA`), or (b) when the receiver declares the link lost after 256 one-millisecond frames without a CRC-good mouse packet (`@ 0x0000DA48`–`0x0000DAB0`, and `0x0000DD0C`–`0x0000DD48` on receive-window timeouts). It hops after 2 missed frames, starts a new round after 4 hops and gives up after 32 rounds. The frame start `0x0000D9F4` runs on each USB SOF (`0x0000E77C`), or on the receiver's own `TIMER1` while it is suspended (`0x000077FA`).
  - No retry. The receiver copies the request once (`0x200001AC`, `@ 0x0000C9CE`) and pumps one 8-byte chunk per frame into its downlink queue (`0x0000CC78`). A link loss clears the relay, so the request is gone; the frame-timer path also empties both radio queues (`@ 0x0000DA82`–`0x0000DAA4`). A reply that arrives later still overwrites the `0xA4`: the uplink parser copies a finished reply into `0x2000016C` without checking for a pending request (`@ 0x0000C54C`–`0x0000C5A4`). That fits the `SAVE_FLASH` timing above (`0xA4` at about 270 ms, the reply at about 770 ms).
  - Downlink only rides on ACKs: the receiver transmits only as the ACK of a CRC-good mouse packet and attaches the head of its downlink queue (`@ 0x0000CF68`–`0x0000D064`). Keep-alives from the mouse also reset its miss counters (`0x0000D068`).
  - On USB suspend the receiver sends the mouse a 1-byte type-4 "sleep" record once (`@ 0x0000E792` sets bit 3 of `0x20000021`; the main loop at `0x00009570` queues it through `0x0000CD2C`). A still mouse then powers off 1–2 s later unless a report arrives in between ([`hardware.md`](hardware.md) section 2.3). Disassembly only; that USB event 2 is SUSPEND is taken from the nRF5 SDK event order.
- The mouse sends `reply[3] + 6` bytes back. An open-directional-dpi reply keeps its status (`0xA1`..`0xA4`) in byte 3, so the stock radio code would send more than 64 bytes for it; the patch caps the length at 64 (**Inferred**: emulation, `rfemu.py`).

## 4. Receiver-local commands

`body[2] != 0x02`, `body[4] = 0x00`, command in `body[5]`, data from `body[6]`. Switch at `0x0000C73C`; config RAM copy at `0x20000148`.

| `body[5]` | Name | What it does | Safe to send | Evidence |
| :---: | :--- | :--- | :---: | :--- |
| `0x81` | Version | Replies `body[3] = 0x04`, `body[6..11] = 01 00 00 00 20 31` (v1.0.0.0, PID `0x2031`) (`@ 0x0000C762`) | Yes | **Verified** reply, 2026-11-13 |
| `0x89` | Link state | `body[6]` = `0x20000056`: `01` while the mouse is linked, `00` without a link (`@ 0x0000C7E6`). `body[5]` is kept and `body[0]` becomes `A1` (`@ 0x0000C9F6`). open-directional-dpi hosts send it before each relayed request. | Yes | **Verified** (`01` and `00`), 2026-11-13/17 |
| `0x8A` | RF ID read | `body[6..9]` = config bytes `+2..+5` (`@ 0x0000C800`). Unit-specific: redact it in anything you publish. | Read-only | **Verified** (2026-11-26): answered `0xA1` in `4.0 ms`, matching both relayed mouse `0x8A` (`8.0 ms`) and the unit's `FICR DEVICEID[0]` byte for byte |
| `0x00` | Set mode | Mode in `body[6]`. `0xB0` stores `0xA55AA55A` at `0x20007FFC` (`@ 0x0000C7A8`) and resets (`0x00007AF0`, `AIRCR`): the receiver bootloader `258a:2113`. `0xA0` enters RF test mode (`radio_test`): copies 9 bytes (`body[7..15]`) to `0x2000027C` (`+0` selector, `+1` sub-mode `0..4`, `+2` `MODE`, `+3` `TXPOWER`, `+4` `FREQUENCY`/start, `+5` end, `+6` step), stops the normal radio and timers (`0xCBB4`, `0xDF48`, `0xDFB0`), calls `0xD190` when `body[7]` is `0`, `1` or `5`, skips USB SOF frame start (`0xE786`) and normal main-loop tasks (`0xA582`), and runs `0xDE68` in the main loop; any later `0x00` with mode `!= 0xA0` resets (`0xC78A → 0x7AF0`). `0xC0` resets (`0x7AF0`) when `body[7] == 0`, else no-op (`0xA1`). `0x00` is normal mode (no-op unless leaving `0xA0`). | **No** | **Inferred** (disassembly, 2026-11-26; not sent) |
| `0x0A` | RF ID write | Stores `body[6..9]` at config `+2` and sets the save flag (`@ 0x0000C7EE`); the main loop then rewrites the config page (section 5). Unpairs the mouse. | **No** | **Inferred** (not sent) |
| other | — | `0xA3`, the rest of the request echoed (`@ 0x0000C75C`). `0x83` (battery) is one of them. | Yes | **Verified** for `0x83`, 2026-11-13 |

The application-to-bootloader packet of the wired mouse (`body[3] = 0x01`, `body[5] = 0x00`, `body[6] = 0xB0`; [`bootloader-protocol.md`](../../platforms/compx-nrf52/bootloader-protocol.md) section 1) is therefore **not** relayed when sent to `258a:2031` with `body[2] = 0x00`: the receiver runs it itself and enters its own bootloader. No tool in this repository sends anything to `258a:2113`.

## 5. Config page `0x00038000`

- `0x000097BC` loads `0x24` bytes from `0x00038000` into `0x20000148`; `0x0000C6FC` writes them back when the save flag (bit 1 of `0x20000000`) is set: CRC over `0x22` bytes stored at `+0x22`, page erase, `0x24`-byte write. The 4-byte RF ID is at `+2`. (**Inferred**, disassembly.)
- It holds the pairing with the mouse and is not in the official image. Treat it like the mouse's `0x88000` page: never erase or publish it.
- Whether the receiver bootloader's `Erase` reaches `0x38000` is **Unknown**. The receiver has never been flashed here.

## 6. Open questions

- The receiver bootloader (`258a:2113`): never seen enumerating; its `Erase` extent and address limits are untested.
- The exact MCU part (section 1.1): probably an nRF52820; only the chip marking would settle it, and no photo of this receiver's board is public.
- Why the link drops for `40.0 ms` (`0x89`: `01 → 00 → 01`) every ~`0.5`–`1.8 s` while the mouse is still on the first host, and never on a second host (section 3). Host clock drift fits; a radio sniffer would prove it.
