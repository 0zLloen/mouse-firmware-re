# Reverse-engineering scripts (Series One Pro mouse and 2.4 GHz receiver)

The scripts behind the receiver relay, RF reply length and wireless polling findings for this mouse. They are kept so the results can be checked again, not maintained as tools: CI only compiles them.

- The emulation and disassembly scripts read your own copy of the official `.hex` files (see `device.yaml`, `firmware`: `XVI.zip` inside the official updater, password `XVI`). No vendor image is in this repository.
- Their output is derived from vendor firmware. Never commit it (`CONTRIBUTING.md`, *Never commit*). `.gitignore` here ignores `*.txt`; write listings outside the repository anyway.
- They need `capstone` (disassembly) or `unicorn` (emulation), which the repository tools do not use. Versions used on 2026-11-13: capstone 5.0.7, unicorn 2.1.4.

```bash
python3 -m venv ~/re-venv && ~/re-venv/bin/pip install capstone==5.0.7 unicorn==2.1.4
```

## Scripts

| Script | Touches a device | What it does |
| :--- | :--- | :--- |
| `rfemu.py <mouse .hex>` | No | Runs the stock mouse's RF request and reply code (`CM2856_Mouse_USB_v1.00.00_20220810.hex`) in Unicorn and asserts what it prints: how a request arriving over the radio reaches the feature buffer `0x20004F28`, and how `0x15D74` sends the reply back as `body[3] + 6` bytes in 8-byte chunks, reading past the 64-byte buffer when `body[3] > 0x3A`. The last part applies the 64-byte cap of the open-directional-dpi patch to the emulated image. Refuses any other image. |
| `disasm.py dis\|xref\|track <.hex> ...` | No | Capstone helpers: a linear listing with PC-relative literal values, branch and literal cross-references to an address, and a heuristic that finds RAM accesses into an address range. `--target receiver` for the receiver image. |
| `rx_query.py` | Read-only | Stock version (`0x81`) and `0x83` queries to the receiver `258a:2031`, receiver-local (`body[2] = 0x00`) and relayed (`body[2] = 0x02`). |
| `rx_checks.py` | Read-only | Receiver link state (`0x89`), an open-directional-dpi request without the relay envelope, and a relayed version query, each with its answer time. |
| `rx_stamp.py` | Read-only | The relayed version query with `body[3]` = `0x06`, `0x0A`, `0x0E`, to see how many reply bytes come back and where the `DP01` stamp lands. |
| `rf_save_probe.py <open-directional-dpi checkout>` | **Writes** the mouse's `0x7E000` profile page | One `SAVE_FLASH` through the receiver (as `odd_profile.py save`), printing each change of the receiver's answer for 1.5 s. |
| `rx_link_watch.py <seconds> [poll_ms]` | Read-only | Polls the receiver-local link state (`0x89`) every 3 ms and reads the input reports on interface 0. Prints the link drops (`01 → 00 → 01`) with their length, split by what the mouse was doing in the 300 ms before: moving, scrolling, buttons only, or still. Nothing is relayed to the mouse. |
| `periph_scan.py <.hex>` | No | Standard library only. Lists the nRF52 peripheral, FICR and UICR addresses an image holds as 4-byte aligned literals, and flags the peripherals an nRF52820 does not have. Used to tell which chip the receiver image was built for ([`receiver.md`](../receiver.md) 1.1). |
| `wl_capture.py <seconds>` | Read-only | Motion reports on interface 0 of the receiver and of the wired mouse: report rate while moving, and summed up, down and sideways counts. |
| `battery_emu.py <mouse .hex>` | No | Unicorn. Battery tick `0xE1BC` with scripted ADC counts and charge-pin levels, the counts-to-percent tables, the battery query, and the sleep handler `0xAB44`. Asserts what it prints. |
| `led_emu.py <mouse .hex>` | No | Unicorn. LED init and LED-off register writes, what the engine `0xB328` draws in each state, every stored effect (all dark), the five button chords with the config bytes they change, the group 2 host commands. |
| `macro_emu.py <mouse .hex>` | No | Unicorn. Button action types through `0x14244`, the repeat engine, the group 4 macro commands, playback of a test macro in all three modes, the allocator with a macro larger than one page. Asserts what it prints. |
| `compx_radio.py` | No | Standard library only. Encoder/decoder for the 2.4 GHz link ([`radio-protocol.md`](../../../platforms/compx-nrf52/radio-protocol.md)): nRF52 air framing from register values (address, CRC, whitening), packet header, records, connect and relay helpers. Run it for its self-check, which re-encodes frames from `compx_coemu.py` and compares stored bits (a regression check of the encoder, not a capture). |
| `compx_coemu.py <mouse .hex> <receiver .hex>` | No | Boots both official images in Unicorn and bridges their radios through `compx_radio.py`. Asserts: connect, keep-alive, a cursor move, a left click and a relayed version query, end to end. Options for the downlink sequence wrap (`--queries`), SOF phase, lost connect answers, clock drift, outages and motion (`--move`), and `--ms` runs that print the link episodes. Refuses other images. |

The hardware scripts use Linux hidraw and need write access to the device nodes (`../udev/`). Run them with only one Glorious mouse and receiver attached.

Never send the receiver its own commands `0x00` (mode; `0xB0` enters the receiver bootloader `258a:2113`) or `0x0A` (rewrites the RF ID, which unpairs the mouse). Receiver-local commands run only when `body[2] != 0x02` and `body[4] == 0x00`; none of these scripts sends either.

## Results

| Finding | Script | Label |
| :--- | :--- | :--- |
| The receiver relays a request to the mouse only when `body[2] = 0x02`. It copies back `body[3] + 6` bytes of the reply and answers `0xA0` while the reply is pending. Receiver-local `0x81` (version) and `0x89` (link state) answer; `0x83` and a request with `body[4] != 0` answer `0xA3`. | `rx_query.py`, `rx_checks.py`, `rx_stamp.py` | **Verified** on a retail receiver and mouse, 2026-11-13 |
| Receiver-local commands run only when `body[2] != 0x02` and `body[4] == 0x00` (`CM2856_Receiver_USB_v1.00.00_20220810.hex @ 0x0000C9B6`); the handler switch at `0x0000C73C` knows `0x81` version, `0x89` link state, `0x8A` RF ID, `0x00` mode and `0x0A` RF ID write. | `disasm.py` on the receiver image | **Inferred** (disassembly) |
| During `SAVE_FLASH` over the radio the receiver answers `0xA4` after about 270 ms, then the real reply arrives after about 770 ms. | `rf_save_probe.py` | **Verified**, 2026-11-13 |
| The stock mouse sends `body[3] + 6` reply bytes over the radio, re-read for every chunk at `0x15D74`; a 64-byte cap ends every reply after at most 8 chunks. | `rfemu.py` | **Inferred** (emulation of the stock image) |
| The link flag drops 1.19 times a second while the mouse is still (233 drops in 195.4 s, median 32 ms) and 0.03 times a second while it moves (3 in 98.5 s); 300 s of normal use. No drop in 63.3 s of steady scrolling with the mouse still (a 75 s run). | `rx_link_watch.py 300`, `rx_link_watch.py 75` | **Verified**, 2026-11-18 |
| The receiver image names no peripheral that an nRF52820 lacks and has none of the nRF52840 `SystemInit()` errata literals; the mouse image has both. | `periph_scan.py` on both images | **Verified** (read from the official files), 2026-11-18 |
| Battery, LED, button-action and macro behaviour of the stock image, including the stock bugs (three chords overwrite DPI fields, brightness "get" writes, a macro must fit one page). | `battery_emu.py`, `led_emu.py`, `macro_emu.py` | **Inferred** (emulation of the stock image; [`firmware-subsystems.md`](../firmware-subsystems.md)) |
| Polling: 1,000 Hz on the cable, about 963 Hz through the receiver while moving. | `wl_capture.py` | **Verified**, 2026-11-13 |
| Wireless direction gain: up/down count ratio 0.938 with a flat profile, 0.570 with the up direction (`N_270`) at 0.5x, loaded with `odd_profile.py load`. | `wl_capture.py` | **Verified**, 2026-11-13 |
| Both images interoperate in co-emulation from the paired state: connect request `05 20` + `DEVICEID[0]`, answer `03 row e8 03`, 1 ms keep-alive, type-1 motion and click records to USB interface 0, relayed version query answered in 5.4 ms. This holds only when the first connect answer is lost on air (the default); otherwise the emulated pair never links ([`radio-protocol.md`](../../../platforms/compx-nrf52/radio-protocol.md) 3.3). | `compx_coemu.py` | **Verified (emulation)** ([`radio-protocol.md`](../../../platforms/compx-nrf52/radio-protocol.md) 6) |
| A still mouse whose clock runs fast against the host's USB SOF loses the link (its keep-alive drifts into the start of the receiver's frame, and the timing bits only move it earlier); a moving mouse keeps it. The receiver's link flag then drops for 37–39 ms (or about 340 ms when the first reconnect fails). | `compx_coemu.py --ms T --drift-ppm P [--move 1000]` | **Verified (emulation)**; on hardware the still-only drops and their 32–40 ms length are **Verified** (rows above), the cause **Inferred** |

## Regenerating the disassembly

From your own official files (the receiver `.hex` is in the wireless updater package):

```bash
~/re-venv/bin/python disasm.py dis --target receiver CM2856_Receiver_USB_v1.00.00_20220810.hex > ~/re/receiver.txt
~/re-venv/bin/python disasm.py dis CM2856_Mouse_USB_v1.00.00_20220810.hex 0x15D74 0x15DC4
~/re-venv/bin/python disasm.py xref --target receiver CM2856_Receiver_USB_v1.00.00_20220810.hex 0xC73C
~/re-venv/bin/python disasm.py track --age 400 CM2856_Mouse_USB_v1.00.00_20220810.hex 0x2000361C 0x20003640
~/re-venv/bin/python rfemu.py CM2856_Mouse_USB_v1.00.00_20220810.hex
```

The receiver image decodes to 32,260 bytes at `0x7000` (SHA-256 `cc356a18a7b8ae6811d431956f9aa48dec25b76bd77b92bb4e1881868faefd44`), the mouse image to 93,328 bytes at `0x6000` (SHA-256 `e50510e0eb213e69d62a088d6f5b792fb88b0f3dc56d3c9c21dd0ca8b6e892be`); `compx_hex_tool.py decode` prints the same sizes, and `sha256sum` of its output gives the same hashes. The full receiver listing matched the one used for the findings above byte for byte (2026-11-13).
