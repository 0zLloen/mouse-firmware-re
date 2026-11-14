# Glorious Forge Series One Pro Wireless — Hardware & Firmware Architecture

## 1. Executive Summary

This document records the hardware and firmware architecture of the **Glorious Forge Series One Pro Wireless** gaming mouse (`FCC ID: 2AXP2-SP22`), combining passive Linux USB/HID enumeration, FCC internal teardown photographs, and static ARM Cortex-M4F Thumb-2 disassembly of the official stock firmware image (`CM2856_Mouse_USB_v1.00.00_20220810.hex`).

Findings use the labels defined in the repository `README.md`: **Verified** (observed on a live device, or read literally from an official file or from markings in FCC photos), **Inferred** (interpretation of disassembly, decompilation or photos), **Unknown** (needs physical probing).

---

## 2. Device Identification & USB/HID Architecture

### 2.1 USB Device Identifiers

| Mode / Component | USB VID:PID | USB Revision (`bcdDevice`) | Product String | Evidence Status |
| :--- | :--- | :--- | :--- | :--- |
| **Wired Mouse (Application)** | `258a:2018` | `0x0101` (`1.01`) | `Series One PRO Wireless` | **Verified** (sysfs `product` and `lsusb` on a retail mouse, wired, 2026-11-12; the same string as the receiver) |
| **Wireless 2.4 GHz Receiver (Application)** | `258a:2031` | `0x0100` (`1.00`) | `Series One PRO Wireless` | **Verified** (Live Linux `lsusb` Bus 001 Dev 036 + `receiver_fw.bin` @ `0x0000D0DC`) |
| **Wired Mouse (Bootloader)** | `258a:2103` | Unknown (not recorded) | `Glorious bootloader` (manufacturer `Glorious`) | **Verified** (`lsusb` after `compx_usb_flasher.py dump` sent the updater's SetBLDeviceMode packet, retail mouse, 2026-11-13) |
| **Wireless Receiver (Bootloader)** | `258a:2113` | Unknown | Unknown | **Inferred** (PID constant in `FirmwareUpdate.exe` CIL `Form1::InitDevice`; not yet seen on USB) |

- **USB Vendor ID `0x258a`**: Sino Wealth / Compx Technology OEM ID used across Glorious Classic (`glorious-classic`) mice.
- **Manufacturer String**: `"Glorious"` (UTF-16LE / ASCII in `CM2856_Mouse_USB_v1.00.00_20220810.hex` at `0x00019BA0` and `CM2856_Receiver_USB_v1.00.00_20220810.hex` at `0x0000D790`).

### 2.2 USB HID Interfaces & Report Descriptors (Verified via `/sys/class/hidraw`)

Passive inspection of `/sys/class/hidraw/hidraw4..6/device/report_descriptor` on the live system confirms three USB HID interfaces:

1. **Interface 0 (`input0` / `hidraw4`) — Boot Mouse (`bInterfaceProtocol = 0x02`; full-speed USB, the nRF52840 USBD has no high-speed mode)**
   - **Raw Descriptor (91 bytes)**:
     `05010902a1010901a10005091901290515002501750195058102750195038103050109381581257f7508950181060930093116008026ff7f751095028106050c0a38021581257f750895018106090015002501750895018106c0c0`
   - **Report Structure (8 bytes, no Report ID)**:
     - Byte `0`: Buttons 1–5 (`bits 0..4`), 3-bit padding (`bits 5..7`)
     - Byte `1`: Vertical Wheel (`int8`, `-127..127`)
     - Bytes `2..3`: X Delta (`int16` little-endian, `-32768..32767`)
     - Bytes `4..5`: Y Delta (`int16` little-endian, `-32768..32767`)
     - Byte `6`: Horizontal Tilt / AC Pan (`int8`, `-127..127`)
     - Byte `7`: Vendor padding byte (`uint8`)
   - **Report rate (Verified, 2026-11-13, open-directional-dpi patch flashed):** 1,000 reports/s on the cable and about 963 reports/s through the `258a:2031` receiver while the mouse moves, from hidraw report timestamps ([`re-scripts/wl_capture.py`](re-scripts/README.md)). Browser pointer events merge reports (a browser polling test showed 565 Hz for the same mouse), so they cannot measure polling.

2. **Interface 1 (`input1` / `hidraw5`) — Consumer, System Control, 6KRO Keyboard (with LED output report) & Vendor Notifications (163 bytes)**
   - **Raw Descriptor** (re-read from sysfs on a retail mouse on firmware v1.0.0.0, 2026-11-13; an earlier 147-byte capture here lacked the keyboard LED output report):
     `050c0901a101850119002a3c021500263c02950175108100c005010980a1018502198129831500250175019503810295058101c005010906a1018503050719e029e71500250175019508810295017508810395057501050819012905910295017503910395067508150025650507190029658100c006a0ff0901a10185040901150026ff00750895088102c006ffff0901a10185050901150026ff00750895088102c0`
   - **Report IDs**:
     - `0x01`: Consumer Control (16-bit usage `0x0000..0x023C`)
     - `0x02`: System Control (Power/Sleep/Wake)
     - `0x03`: Standard 8-byte modifier + 6KRO Keyboard report (for macro/keybind playback)
     - `0x04`: Vendor Usage Page `0xFFA0` (8-byte Input report)
     - `0x05`: Vendor Usage Page `0xFFFF` (8-byte Input report for asynchronous battery/DPI notifications)

3. **Interface 2 (`input2` / `hidraw6`) — Vendor Configuration Feature Channel (20 bytes)**
   - **Raw Descriptor**:
     `06ffff0900a10109001500250175089540b102c0`
   - **Decoded Structure**:
     - Usage Page `0xFFFF` (Vendor Defined), Usage `0x00`
     - **64-byte Feature Report (`b1 02`)**, no Report ID declared in descriptor (uses Report ID `0x00`, so HIDAPI / WebHID / Windows HID pass a 65-byte buffer `[0x00, b0..b63]`).

The receiver (`258a:2031`) enumerates with byte-identical report descriptors on all three interfaces (91 / 163 / 20 bytes; **Verified**, sysfs, 2026-11-13).

**Battery query (Verified, wired, 2026-11-13):** request `body[2] = target, body[3] = 0x02, body[4] = 0x00, body[5] = 0x83`; reply `a1 00 <target> 02 00 83 00 64` (`body[6]` charging flag, `body[7]` percent: not charging, 100 %, cable plugged in) for target `0x00` and `0x02`. The firmware-version query (`body[3] = 0x06, body[5] = 0x81`) is answered with `body[3] = 0x03`; the updater's own `GetFW` sends length `0x03`.

### 2.3 Application-mode Feature reports and the 2.4 GHz link (mouse side)

Addresses are in `CM2856_Mouse_USB_v1.00.00_20220810.hex` and **Inferred** from disassembly, most of them also run in Unicorn emulation ([`re-scripts/rfemu.py`](re-scripts/README.md), and open-directional-dpi's `build_and_verify_patch.py`), 2026-11-13. The receiver side is in [`receiver.md`](receiver.md). The radio itself (PHY, packets, link layer) is in [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md).

**Dispatcher.** USB and radio requests land in the same 64-byte buffer `0x20004F28` with a pending flag at `0x200032D4`. `0x000151AC` (called from `0x0000E8CE`) sets `body[0] = 0xA0` and switches on the command group `body[4]`:

- `body[4] >= 5` answers `0xA3` before any handler runs (**Verified** on a retail mouse, 2026-11-13).
- Groups `0`..`4` go to `0x00014788`, `0x00015290`, `0x000156B8`, `0x00014540` and `0x000149E8`. A handler that leaves `0xA0` in `body[0]` gets `0xA1`. `0x000151F8` then clears the pending flag.
- Group `0` switches on `body[5]`: `0x81` is the version query (layout above, **Verified**); `0x00` is "set device mode" with the mode in `body[6]`. Mode `0xB0` takes `0x00014816` → `0x00014824` (`cmp r0, #0xB0`) → `0x0001484C`, which stores `0xA55AA55A` at `0x2003FFFC` and calls `0x000071B8` (`NVIC_SystemReset()`). This is the path of the official updater's bootloader-entry packet ([`firmware-updater.md`](firmware-updater.md) 4.1).

**Radio link, mouse side.**

| Item | Address |
| :--- | :--- |
| RF control block | `0x2000361C` |
| RX ring | `0x20007334`, 16 entries of 65 bytes |
| Request/reply buffer, staging after it | `0x20004F28`, `0x20004F68` |
| Pending flag | `0x200032D4` |
| TX aggregate (one radio packet) | `0x20007744` |
| RX parser, reply completion, TX, flush | `0x00013334`, `0x000151F8`, `0x00015D74`, `0x0001513A` |

- Data travels in type-7 records: header `more << 7 | len << 3 | 7`, where `len` counts one offset byte plus up to 14 data bytes on the downlink (uplink reply chunks are 8 data bytes with no offset byte, [`radio-protocol.md`](../../platforms/compx-nrf52/radio-protocol.md#52-uplink-records-mouse--receiver) 5.2) and `more = 0` marks the last record.
- RX: a request that arrives while the pending flag is set is dropped. Bytes a request does not carry are zero. A 1-byte record with a value from `0x39` to `0xFF` cancels a pending reply and resets the downlink sequence.
- TX: `0x00015D74` sends the reply in 8-byte chunks and re-reads its length, `body[3] + 6`, for every chunk (`0x00015D8C`, `0x00015DB4`). With `body[3] > 0x3A` stock sends more than 64 bytes, and the extra bytes come from the staging area at `0x20004F68`. An open-directional-dpi reply keeps its status (`0xA1`..`0xA4`) in `body[3]`, so it would always overrun. The open-directional-dpi patch replaces both length reads with a 64-byte cap.
- **Verified** round trips through the receiver (2026-11-13): stock version query, and the open-directional-dpi requests and replies with the patch flashed ([`receiver.md`](receiver.md) section 3).

**Radio idle, link loss and sleep (mouse side).** **Inferred** from disassembly and a Unicorn model that runs the real radio and timer interrupt handlers against a scripted receiver (2026-11-13). Nothing here was checked on hardware.

- Linked with nothing to send, the mouse sends a 2-byte keep-alive `[02, uplink seq, downlink seq]` every 1 ms (every second 500 us `TIMER0` slot), with no time limit: radio state 7, `0x000188E0`–`0x0001897A`. In emulation: 2,999 packets in 3 s of idle, 1,000 us apart.
- Downlink data only arrives as the payload of the receiver's ACK to an uplink packet: the RX ring is written only in radio state 9, the receive window after the mouse's own TX (`0x00016044`–`0x00016084`). The keep-alives are enough to carry it: in emulation a relayed version query reached a still mouse and its 16-byte reply was back within about 6 ms. While a reply is pending the mouse sends in every 500 us slot.
- A CRC error on the ACK in state 9 returns to state 7 (`0x000160D6` → `0x000160DC`). The receiver-polling states 10–12 are reached only from pairing state 3 (`0x00015F3E`).
- When ACKs stop, the mouse retries every 1 ms and hops channels. After 32 hop rounds (about 256 ms) it drops the link (`0x000188C4`–`0x000188D0`) and searches with 5-byte connect requests on 3 pairing channels (states 4–6) until the receiver answers.
- Sleep is System OFF, not a radio idle mode. A 1 s app timer (handler `0x0000AB44`) counts seconds without a report in `0x20003048` while the config u16 at `0x200038CB` is not `0xFFFF` (factory value 420 s; `0xFFFF` = never). Any report while linked zeroes the count (`0x00014D0C`). The mouse powers off when the count passes that value, at 0 % battery (`0x0000E30E`), or 1–2 s after a type-4 "sleep" downlink record (`0x000133A4`), which the receiver sends when the host suspends it ([`receiver.md`](receiver.md) section 3).
- The sleep path `0x0000AB98` / `0x0000ABC4` stops the radio, `TIMER0` and `TIMER1`, arms GPIO sense on the six buttons, the motion pin and both wheel pins (`0x0000ABF6`–`0x0000AC68`), then enters System OFF. Only a button, the wheel or sensor motion wakes it, and waking is a reset, a reboot and a reconnect. A host request cannot wake it: the receiver can only talk to the mouse in ACKs to the mouse's own packets.

---

## 3. Physical Hardware & PCB Teardown Verification

### 3.1 FCC Filing (`2AXP2-SP22`) & OEM Platform

| Attribute | Value | Evidence Status |
| :--- | :--- | :--- |
| **FCC ID** | `2AXP2-SP22` (`https://fccid.io/2AXP2SP22`) | **Verified** (FCC ID filing & internal photos) |
| **OEM Designer / Manufacturer** | Compx Technology (Shenzhen) | **Verified** (PCB silkscreen `CM2852-BAMF` + firmware project strings `CM2856_Mouse_USB`) |
| **Main PCB Silkscreen** | `CM2852-BAMF 20220516` (`V1.0`) | **Verified** (FCC internal photos) |
| **Side-Button Daughterboard** | `CM2852-MINI-BL 20220516` | **Verified** (FCC internal photos) |
| **Main MCU** | **Nordic nRF52840-QIAA-D0** (`N52840 QIAAD0 2144MX`, 73-pin aQFN, 1 MB Flash, 256 KB RAM) | **Verified** (FCC internal macro photo of U1 package markings + firmware memory map) |
| **Optical Sensor** | **Glorious BAMF** (`BAMF 2152` marking), believed to be a PixArt **PAW3370** derivative (50–19,000 DPI, 400 IPS, 50G) | **Verified** marking; **Inferred** PAW3370 lineage (package shape + firmware register driver) |
| **MCU Clocks** | 32 MHz HFXO (`Y2`) + 32.768 kHz LFXO (`Y1`) | **Inferred** (FCC internal photos + clock init in firmware) |
| **RF Matching / Antenna** | 2.4 GHz PCB trace antenna (`ANT1`) matching network on `ANT` pin | **Inferred** (FCC internal photos) |
| **Battery** | 3.7 V Li-Po 300 mAh (`1.11 Wh`), 3-wire JST connector (Red `+`, Black `-`, Yellow `NTC`) | **Verified** (FCC internal photos) |
| **SDK / Toolchain** | Nordic nRF5 SDK + Keil MDK-ARM (`__main` scatter-loader at `0x00006201`) | **Verified** (Embedded debug strings `..\..\..\..\..\components\libraries\usbd\app_usbd.c`, `Source\usb_protocol.c`) |

### 3.2 GPIO Pin Mapping (Inferred from Stock Firmware Disassembly)

By disassembling the GPIO initialization routine (`0x0000C5B8`–`0x0000C648`), GPIO port table (`0x0001AC04`: `[0x50000000, 0x50000300]`), button/wheel scanner (`0x00016B30`–`0x00016CE0`, `0x00009614`–`0x00009690`), low-power wake config (`0x0000ABF8`–`0x0000AC58`), LED driver (`0x00018FA0`–`0x00018FF0`), battery monitor (`0x0000E1B0`–`0x0000E1F0`), and SPI2 sensor driver (`0x000184C8`–`0x0001855C`), the functional GPIO pins on the `CM2852-BAMF` PCB are mapped below. None has been probed on hardware. The three boot-chord pins (`P1.15`, `P1.13`, `P1.00`) are **Verified** to read low with Left + Right + DPI held: the open-directional-dpi patch enters the bootloader only when all three read low, and with the chord held at power-up it enumerated as `258a:2103` (build `21ea252`, 2026-11-13). The test does not tell the three apart, so which pin is which button is still from the disassembly:

| Peripheral / Function | nRF52840 Pin | GPIO Number | Direction / Config | Firmware Disassembly Reference | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PAW3370 SPI2 CSN** | **`P0.04`** | `4` | Output, Active Low (idle High) | `0x184EC` (`gpio_cfg`), `0x18522` (`gpio_pin_set`), `0x17540` (`gpio_pin_clear`) | **Inferred** |
| **PAW3370 SPI2 SCK** | **`P0.12`** | `12` | Output (`NRF_SPI2->PSEL.SCK = 12`) | `0x184FC` (`gpio_cfg`), `0x1852A` (`str r0, [0x40023508]`) | **Inferred** |
| **PAW3370 SPI2 MISO** | **`P0.11`** | `11` | Input, Pull-Up (`NRF_SPI2->PSEL.MISO = 11`) | `0x1850C` (`gpio_cfg`), `0x18530` (`str r0, [0x40023510]`) | **Inferred** |
| **PAW3370 SPI2 MOSI** | **`P0.08`** | `8` | Output (`NRF_SPI2->PSEL.MOSI = 8`) | `0x1851C` (`gpio_cfg`), `0x18536` (`str r0, [0x4002350C]`) | **Inferred** |
| **PAW3370 MOTION IRQ** | **`P0.06`** | `6` | Input, Active Low sense/wake | `0x0AC38` (`gpio_cfg_sense r0=6`) | **Inferred** |
| **Button 0 (Left Click / LMB)** | **`P1.15`** | `47` (`0x2F`) | Input, Pull-Up, Active Low | `0x0C5BE`, `0x09618`, `0x16B3C`, `0x0ABFC` | **Verified** in the boot chord (2026-11-13); which button: **Inferred** |
| **Button 1 (Right Click / RMB)** | **`P1.13`** | `45` (`0x2D`) | Input, Pull-Up, Active Low | `0x0C5C8`, `0x09620`, `0x16B44`, `0x0AC06` | **Verified** in the boot chord (2026-11-13); which button: **Inferred** |
| **Button 2 (Middle Click / MMB)** | **`P1.10`** | `42` (`0x2A`) | Input, Pull-Up, Active Low | `0x0C5D2`, `0x0962A`, `0x16B4E`, `0x0AC10` | **Inferred** |
| **Button 3 (Back / Side Btn 4)** | **`P0.09`** | `9` (`0x09`) | Input, Pull-Up, Active Low (NFC1 as GPIO) | `0x0C5DC`, `0x09634`, `0x16B58`, `0x0AC1A` | **Inferred** |
| **Button 4 (Forward / Side Btn 5)** | **`P0.10`** | `10` (`0x0A`) | Input, Pull-Up, Active Low (NFC2 as GPIO) | `0x0C5E6`, `0x0963E`, `0x16B62`, `0x0AC24` | **Inferred** |
| **Button 5 (DPI Cycle Button)** | **`P1.00`** | `32` (`0x20`) | Input, Pull-Up, Active Low | `0x0C5F0`, `0x09648`, `0x16B6C`, `0x0AC2E` | **Verified** in the boot chord (2026-11-13); which button: **Inferred** |
| **Scroll Wheel Encoder A** | **`P0.29`** | `29` (`0x1D`) | Input, Pull-Up | `0x0C600`, `0x0AC54`, `0x16CB2` | **Inferred** |
| **Scroll Wheel Encoder B** | **`P0.02`** | `2` (`0x02`) | Input, Pull-Up | `0x0C5F8`, `0x0AC3E`, `0x16CBA` | **Inferred** |
| **Charger / Power Status 1** | **`P1.06`** | `38` (`0x26`) | Input, Pull-Up | `0x0C614`, `0x0E1C4` (`Source\battery.c`) | **Inferred** |
| **Power / Mode Switch Input** | **`P1.04`** | `36` (`0x24`) | Input, Pull-Up | `0x0C60A` | **Inferred** |
| **Peripheral / Sensor Power En 1** | **`P0.26`** | `26` (`0x1A`) | Output | `0x0C61A` | **Inferred** |
| **Peripheral / LED Power En 2** | **`P0.14`** | `14` (`0x0E`) | Output | `0x0C636` | **Inferred** |
| **LED / Matrix Outputs** | **`P0.13`, `P0.15`, `P0.17`, `P0.20`, `P0.22`, `P0.24`** | `13, 15, 17, 20, 22, 24` | Output (PWM / GPIO indicator & RGB LEDs) | `0x18FA6`–`0x18FE8`, `0x16B8C`–`0x16BC6` | **Inferred** |

> **Important Note on `P0.09` and `P0.10` (NFC Pins)**:
> Because Side Buttons 4 and 5 are routed to `P0.09` (`NFC1`) and `P0.10` (`NFC2`), any Zephyr or ZMK board definition **must** set `&uicr { nfct-pins-as-gpios; };` so `NRF_UICR->NFCPINS` is configured for GPIO operation. `CONFIG_NFCT_PINS_AS_GPIOS` is deprecated in Zephyr 4.1 (ZMK main).

---

## 4. PAW3370 / Glorious BAMF Optical Sensor Driver Details (Inferred from disassembly)

Disassembly of `0x0001708C`–`0x00018558` in `CM2856_Mouse_USB_v1.00.00_20220810.hex` reveals the exact hardware interface and register protocol used by the Glorious BAMF (PixArt PAW3370) sensor:

1. **SPI Bus Configuration (`0x00018528`–`0x00018552`)**:
   - Peripheral: `NRF_SPI2` (`0x40023000`)
   - Frequency: `0x40000000` (**4 Mbps**, created via `movs r0, #8; lsls r0, r0, #27` at `0x1853E` written to `0x40023524` `FREQUENCY`)
   - Mode: `CONFIG = 0x06` (`CPOL = 1` Active Low, `CPHA = 1` Trailing, MSB first = **SPI Mode 3**) written to `0x40023554`
   - Enable: `ENABLE = 0x01` written to `0x40023500`

2. **SPI Register Read / Write Timing (`0x0001752C`–`0x00017690`, `0x00018490`–`0x000184C4`)**:
   - **Single Register Read (`0x18490`)**:
     - Assert CSN (`P0.04` low), transmit `reg & 0x7F`, wait `t_SRAD = 20 us` (inside `0x1752C` at `0x17572`), read 1 byte, deassert CSN (`P0.04` high), wait `20 us` (`0x1849A`).
   - **Single Register Write (`0x184B4`)**:
     - Assert CSN (`P0.04` low), transmit `reg | 0x80` and `val`, deassert CSN (`P0.04` high), wait `180 us` (`0xB4` at `0x184BE`).
   - **Motion Burst Read (`0x1708C` -> `0x184A6` -> `0x175B8`)**:
     - Assert CSN (`P0.04` low), transmit `0x16` (`MOTION_BURST` register), wait `t_SRAD_MOTBR = 5 us` (`0x175FC`), clock out **12 bytes (`0x0C`)**, deassert CSN (`P0.04` high).
     - Burst packet layout parsed at `0x170B8`–`0x17118`:
       - `burst[0]`: `MOTION` status byte (bit 7 `0x80` = motion occurred)
       - `burst[1]`: `OBSERVATION` byte
       - `burst[2..3]`: `DELTA_X` (`int16_t` little-endian: `ldrh r0, [sp, #2]`)
       - `burst[4..5]`: `DELTA_Y` (`int16_t` little-endian: `ldrh r0, [sp, #4]`)
       - `burst[6]`: `SQUAL`
       - `burst[7]`: `RAW_DATA_SUM`
       - `burst[8..9]`: `MAX_RAW_DATA` / `MIN_RAW_DATA`
       - `burst[10..11]`: `SHUTTER_UPPER` / `SHUTTER_LOWER`

3. **DPI Resolution & Register Programming (`0x00017860`–`0x00017960`)**:
   - `0x17860` takes the DPI X in `r0` and ignores `r1` (Y). It writes `0xD0` to register `0x4D` (100 CPI steps) when `10,100 <= DPI <= 19,000` (test at `0x17864`–`0x17870`), otherwise `0x50` (50 CPI steps).
   - It then writes `uxtb(DPI / step - 1)` to `0x4E` (`RESOLUTION_X`), then `0x56` and `0x5A`, and clears `0x20003016` / `0x20003018`. It never writes `0x4F`. The only other bank-0 writes to `0x4D`..`0x4F` are the power-up table in `0x170E8` (`0x171C2`–`0x171D2`: `0x4D = 0x50`, `0x4E = 0x3B`, `0x4F = 0x46`), run only by the sensor init `0x19584`, which then tail-calls `0x17860` (`0x195AA`). (Corrected 2026-11-13: this item said `0x4F` is written too; a scan of all 216 register writes through `0x184B4` and Unicorn show it is not.)
   - Out of range: DPI `0` writes `0x4E = 0xFF` (12,800 CPI) and DPI 20,000 writes `0x4E = 0x8F` (7,200 CPI). The DPI-adjust wheel can reach `0` (`0xA58A`–`0xA5A0`) and stops at 16,000; only a host can send more. In Unicorn, `0x17860(400)` wrote `0x4D = 0x50`, `0x4E = 0x07`, `0x56 = 0x04`, `0x5A = 0x10`.
   - Callers (10): the DPI stage buttons (`0xA652`, `0xA6E0`, `0xA7C4`), DPI shift press (`0xA74A`), DPI shift release and the DPI-adjust wheel (`0xA752`), host DPI commands 1/2/3 when the target profile is active (`0x15388`, `0x154A8`, `0x15556`), profile apply (`0x18790`) and the sensor init (`0x195AA`, a `b.w` passing `P+0x2B`). The sensor init `0x19584` runs at boot (`0xE8A0`) and from the health check `0x99E0` (main loop `0xE8E4`), which re-inits the sensor every 3000 ticks when the burst observation byte `0x20003021` is not `0xB7` or `0xBF`.
   - **High-DPI Gain Adjustment (`0x178EC`)**:
     - Writes `0x90` to register `0x5A` when `DPI >= 5,000`, or `0x10` when `DPI < 5,000`.
   - **Angle Snapping (`0x17924`)**:
     - Writes `0x84` (enabled) or `0x04` (disabled) to register `0x56`.
   - **Lift-Off Distance (LOD, `0x17964`–`0x1799C`)**:
     - Writes `0x0C` (1 mm) or `0x0D` (2 mm) to register `0x42`, and updates threshold in register `0x2B` (`0x0A` for 1 mm, `0x18` for 2 mm).

---

### 4.1 DPI Stages and the Active DPI in RAM

From two independent disassembly passes over `CM2856_Mouse_USB_v1.00.00_20220810.hex` (backward from the DPI register writer `0x17860`, forward from the DPI button and the host HID commands), both checked in Unicorn emulation (**Inferred**, except where marked):

| Address | Width | Meaning |
| :--- | :--- | :--- |
| `0x200038C8` | `0x266` bytes | RAM copy of the active profile config page (flash `0x8B000`, or the factory defaults at `0x1A3B8` when its length or CRC check fails, `0xD4A0`) |
| `0x200038CF` | `u8` | Active profile, 0-based (`0..2`); profile base `P = 0x200038C8 + 200 * profile` |
| `0x200038D0` | `u8` | Profile count (factory 3; section 4.3) |
| `P+0x2B` / `P+0x2D` | `u16` LE | Current DPI X / Y (persisted copy) |
| `P+0x30` / `P+0x31` | `u8` | Stage count (max 6) / stage index (0-based) |
| `P+0x32+4i` / `P+0x34+4i` | `u16` LE | Stage `i` X / Y DPI |
| `0x20004CE4` | 27 bytes | RAM copy of `P+0x2F..`; the stage index is at `0x20004CE6` |
| `P+0x5C` | 120 bytes | Button map: 8 slots of 15 bytes. Slot byte `0` is the button code (`01`..`05` = Left / Right / Middle / Back / Forward, `0x14` = DPI, `0x10` / `0x11` = wheel); layer `L` (`0`, `1`) is the 7-byte action at `+1+7L`: `f0` type, `f1` not read by the dispatcher, `f2` sub-function, `f3..f6` parameters |
| `0x20004B30` | 120 bytes | RAM copy of the active profile's button map (copied at `0x187AC`, `0x145FE` and in the config load); button actions read it |
| **`0x20003068`** / `0x2000306A` | `u16` | **Live DPI X / Y in DPI units**, as stock tracks it; not always the DPI the sensor runs at. Written by the DPI stage buttons, the DPI-adjust wheel and a profile switch (`0x18754`) just before they call `0x17860`, and by the host DPI commands just after it; `0x17860` uses X only. Not written by DPI shift (`0xA74A` / `0xA752`) or by the sensor init `0x19584`, which programs `P+0x2B`. Host DPI commands 1 and 2 aimed at an inactive profile write it without programming the sensor (below). (Corrected 2026-11-13: this row said every DPI path writes it.) |

- **Verified on hardware (2026-11-13):** the open-directional-dpi patch reads `0x20003068` on every sensor burst. Cycling the DPI button on a retail mouse reported 400 → 800 → 1600 → 3200 (that unit's stages), three times in a row.
- **DPI shift** ("sniper") is a button action, not a fixed key: type 7 sub-function 5, with the shift DPI X and Y big-endian in `f3..f4` and `f5..f6` of the slot. No button has it in the factory map: slots 0..4 are type 1 mouse buttons 1..5, slot 5 (DPI) is type 7 sub 6 (stage cycle with wrap), slots 6 and 7 the wheel.
- Path: the GPIO scan (`0x16B84`–`0x16C96`) calls `0x14244(state, index)` (`1` press, `0` release, `2` long hold), which picks the layer and calls the action dispatcher `0xA450`. Press: `0x17860(shift X)` at `0xA74A`. Release: `0x17860` at `0xA752` with the `0x20003068` value read at `0xA494`. A long hold writes no DPI. In Unicorn (slot 3, layer 0 set to `07 01 05 01 90 01 90`): press gave sensor 400 with `0x20003068` still 1200, release gave 1200.
- So a held DPI shift programs the sensor without updating `0x20003068`. No RAM variable holds a shift flag or the programmed DPI (Unicorn write trace of a press and a release). While shift is held, any other DPI write (DPI button, wheel, host command, the `0x19584` re-init) overrides it until the next press.
- Host DPI commands 1 (set stages) and 2 (set stage index) aimed at a profile that is not active write that profile's current-stage DPI into `0x20003068` and into the active profile's `P+0x2B` / `P+0x2D`, but do not program the sensor (`0x153A6`–`0x153F4`, `0x154E8`–`0x15522`; Unicorn). Whether Glorious Core ever sends them for an inactive profile is **Unknown**.
- open-directional-dpi builds after `56afbb4` therefore record the DPI inside `0x17860` (a `bl` at `0x17864`), which covers all 10 callers; `21ea252` and main `56afbb4` read `0x20003068`.
- **Key assignment** (host): command group 3 (`feat[4] = 3`, handler `0x00014540`), `feat[5] = 0x00` to set and `0x80` to get, `feat[6..15]` = profile (1-based), button code, layer, `f0..f6`. A set on the active profile also refreshes `0x20004B30`. In Unicorn, `feat[6..15] = 01 04 00 07 01 05 01 90 01 90` gave Back a 400-DPI shift (reply `0xA1`); pressing it gave 400, releasing it 1200. Not sent to a mouse.
- The stage index is not a reliable stand-in for the DPI. The factory defaults boot at 1200 DPI with index 2, and the DPI-adjust wheel and the host "set DPI" command change the DPI without changing the index.

### 4.2 Millisecond tick (Inferred from disassembly; rate on the cable Verified, 2026-11-13)

- `0x0000E894` calls `0x00018B3C` with `1000`, which sets up `TIMER1` (`0x40009000`): 32-bit timer, `PRESCALER = 4` (1 MHz), `CC[0] = 1000`, `SHORTS` `COMPARE0_CLEAR`, interrupt on `COMPARE0`. So it fires every 1 ms.
- The handler (`0x00006C3A` → `0x00018BA4`) increments several tick counters, among them `0x2000301A` (`0x00018BB6`).
- The main loop at `0x00013270` tests `0x2000301A`, clears it and calls the sensor burst `0x0001708C` (`0x00013278`), moving or not. The second burst call (`0x000131D8`) runs only while the motion pin `P0.06` is low.
- So the sensor is read about once per millisecond.
- **Verified on the cable (2026-11-13):** open-directional-dpi `21ea252` counts the sensor-burst calls and reports the count in `GET_CAPABILITIES` bytes `20..23`. Read once a second for 12 s with the mouse still, it rose 1000.0 per second. Its idle reset uses this count as its millisecond clock.
- Over 2.4 GHz it was not measured: relayed requests failed while the mouse was still ([`receiver.md`](receiver.md) section 3). **Inferred** the same: the only `TIMER1` task writes found by literal search are the init `0x00018B3C`, captures at `0x000131D4` / `0x0001327E`, a `TASKS_CLEAR` at `0x00015FEE` on each connect, and `STOP` / `SHUTDOWN` in `0x00018B8C`, whose only caller is the sleep path (`0x0000ABE2`). The IRQ handler `0x00006C00` only clears its events; the timer instance table at `0x0000AFCC` was not traced. So it runs while linked, retrying and searching, and stops only on the way into System OFF (section 2.3); a reconnect makes at most one tick late. The burst at `0x00013278` depends only on the tick flag. In the radio emulation the tick counted 3,000 ms in 3 s of idle.

### 4.3 Onboard profiles (Inferred from disassembly and Unicorn emulation, 2026-11-13; nothing checked on hardware)

- Three profiles in the config page (section 4.1): count `0x200038D0` (factory 3), active index `0x200038CF`. Factory profiles: DPI 1200 / 1600 / 3200, stages 400 / 800 / 1600 / 3200 in each.
- Only two code paths (three `strb` instructions) store the active index: button action type 8 (`0x0000AA2C` / `0x0000AA3C` in `0xA450`) and host group 0 command `0x05` (`0x00014916`). Boot (`0xD4A0`) and the factory resets (`0x14896`, `0x18C58`) replace it with the whole config.
- Action type 8 acts on press only: sub 1 next (stops at the last profile), 2 previous (stops at the first), 3 next with wrap, 4 previous with wrap.
- Host group 0 (`body[4] = 0`): `0x05` with `body[6] = n` (1-based) sets the active index to `n - 1`. It clamps to 2 without checking the count, and `n = 0` writes `0xFF` (a stock bug). `0x85` returns the active profile + 1 in `body[6]`; `0x86` returns the count and `0x06` sets it (1..3). All answer `0xA1` (emulated on the wired path; through the receiver they should be relayed like any stock command, not run).
- No default switch: in the factory map no button has action type 8, in any profile or layer. No button combination switches profiles either: all 63 combinations of the six buttons through `0x14244` left the active index unchanged. The combinations L+R+M, L+M+DPI, R+M+DPI, M+Back+DPI and M+Forward+DPI change LED settings (disassembly only).
- L+R+M held for 5 s (5,000 `TIMER1` ticks) restores the factory config, so the active profile becomes profile 1, and flags a save and reset. Releasing any of the three cancels it (`0x2000305E`).
- A switch calls `0x00018754` (only from `0xAA44` and `0x1491C`). It sets `0x20003068` / `0x2000306A` from `P+0x2B` / `P+0x2D`, programs the sensor once through `0x17860`, copies the stage table to `0x20004CE4` and the button map to `0x20004B30`, reloads the LED effect from `P+0x11` (`0xDA58`; factory effects 1 / 2 / 5) and clears macro and key state (`0x16A1C`, `0x18638`). The button action (type 8, `0xAA48`–`0xAA5A`) also queues a notification (type 2, value profile + 1, ring `0x20004BA8`; probably input report ID 5); host command `0x05` does not. The LED, macro and notification parts are from disassembly only.
- The save is deferred: `0x166EC(8)` sets `0x20003001 |= 1` and `0x20003004 = 300`, the `TIMER1` handler counts that down, and then `0x14668` (main loop `0xE8D2`) erases and writes page `0x8B000`, but only while `0x20003048 >= 2` (at least 2 s without a report, section 2.3) or byte `0x20003000` is non-zero (`0x14670`–`0x1467A`). With sleep set to `0xFFFF` the counter does not run (`0xAB4E`), so this gate then depends on `0x20003000` (Inferred). The DPI button uses the same path (`0x166EC(2)`). So with the factory sleep value the active profile should survive a power cycle.
- Group 3 command `0x80` (section 4.1) is a read-only query of one button's action: `body[6]` profile (1-based), `body[7]` button code, `body[8]` layer; the reply has `f0..` from `body[9]`. A reply with `body[9] = 0x08` means that button switches profiles. In Unicorn, profile 1, code `0x14`, layer 0 returned `07 01 06` and left the config unchanged.

## 5. Survey of Existing Open-Source Research (Phase A Questions 1–7)

| Question | Finding | Classification | Sources |
| :--- | :--- | :--- | :--- |
| **1. Existing ZMK/Zephyr/QMK port to Series One Pro?** | **None found.** Project UFA (`aroum/ufa`) ports ZMK to nRF52833/nRF52840 mice with PAW3395/PAW3950/PMW3610-class sensors; its confirmed targets are the VGN F1 MOBA, VXE R1 Pro and Zaopin z2 (PAW3395). It has no Glorious target and no PAW3370 support. | **Verified** (UFA README, 2026-11-12) | `https://github.com/aroum/ufa` |
| **2. PCB GPIO connections or SWD pads mapped?** | Prior public repos did not map the `CM2852-BAMF` GPIOs. The GPIO pinout was mapped from the stock firmware disassembly (Section 3.2 above, not probed). SWD pad candidates are visible on the PCB in FCC photos (see `flash-layout-and-swd.md`). | **Inferred** | Stock FW `CM2856_Mouse_USB_v1.00.00_20220810.hex` + `https://fccid.io/2AXP2SP22` |
| **3. Extracted original firmware?** | **Yes.** Extracted both `CM2856_Mouse_USB_v1.00.00_20220810.hex` (93,328 B app) and `CM2856_Receiver_USB_v1.00.00_20220810.hex` (32,260 B app) from the official Glorious updater archive. | **Verified** | `v1.0.0.0_20220810_Series_One_ProFirmware.zip` |
| **4. Published firmware dump, updater image, or bootloader analysis?** | No prior public bootloader analysis existed for Compx `258a:2103`/`258a:2113`. We decompiled `FirmwareUpdate.exe` and disassembled the ARM firmware to produce a complete specification (`firmware-updater.md`). | **Verified** | `FirmwareUpdate.exe` + `mouse_fw.bin` |
| **5. Reverse-engineered firmware update protocol?** | **Yes, exercised on hardware (2026-11-13).** 65-byte Feature report protocol (`0xB0` bootloader commands, `0x55` wire XOR, `0x2003FFFC = 0xA55AA55A` bootloader entry magic). The app-to-bootloader packet was read from `FirmwareUpdate.exe` and tested; `GetBLFW`, `Erase`, `Program`, `VerifyByAddress` and `Exit` were all used to flash and boot a patched image on a retail unit. | **Verified** | See `firmware-updater.md` and `../../platforms/compx-nrf52/bootloader-protocol.md` |
| **6. Compatible PAW3370/BAMF sensor driver?** | **No.** No driver for `pixart,paw3370` exists in Zephyr (3.5–4.5), ZMK (v0.3, main), QMK, or known community modules. One has to be written from the stock register/burst sequence at `0x1708C`–`0x18554`. This blocks a working ZMK port. | **Verified** (source search) | Stock FW `0x1708C`–`0x18554` |
| **7. Documented recovery or unbricking procedure?** | **USB recovery tested.** Through the resident Compx bootloader (`258a:2103`) a patched image was flashed and booted, and the official image was restored the same way (2026-11-13). With open-directional-dpi `21ea252` on the mouse, its boot chord (Left + Right + DPI held at power-up on the cable) and its `ENTER_BOOTLOADER` command (`0x0F`, argument `BLRS`) both entered the bootloader (`258a:2103`), and `compx_usb_flasher.py dump --out <new file> --end 0x6020` (32-byte read, then `Exit`) returned it to `258a:2018` without writing anything (2026-11-13). The ZMK board's boot chord and passive SWD inspection (`flash-layout-and-swd.md`, `flashing-feasibility.md`) are still untested. | **Verified** (USB, patch boot chord) | See `flash-layout-and-swd.md` |
