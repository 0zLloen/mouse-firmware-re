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

Addresses are in `CM2856_Mouse_USB_v1.00.00_20220810.hex` and **Inferred** from disassembly, most of them also run in Unicorn emulation ([`re-scripts/rfemu.py`](re-scripts/README.md), and open-directional-dpi's `build_and_verify_patch.py`), 2026-11-13. The receiver side is in [`receiver.md`](receiver.md).

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

- Data travels in type-7 records: header `more << 7 | len << 3 | 7`, where `len` counts one offset byte plus up to 14 data bytes and `more = 0` marks the last record.
- RX: a request that arrives while the pending flag is set is dropped. Bytes a request does not carry are zero. A 1-byte record with a value from `0x39` to `0xFF` cancels a pending reply and resets the downlink sequence.
- TX: `0x00015D74` sends the reply in 8-byte chunks and re-reads its length, `body[3] + 6`, for every chunk (`0x00015D8C`, `0x00015DB4`). With `body[3] > 0x3A` stock sends more than 64 bytes, and the extra bytes come from the staging area at `0x20004F68`. An open-directional-dpi reply keeps its status (`0xA1`..`0xA4`) in `body[3]`, so it would always overrun. The open-directional-dpi patch replaces both length reads with a 64-byte cap.
- **Verified** round trips through the receiver (2026-11-13): stock version query, and the open-directional-dpi requests and replies with the patch flashed ([`receiver.md`](receiver.md) section 3).

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
   - For **`50 <= DPI <= 10,000`**:
     - Writes `0x50` to register `0x4D` (sets 50 CPI step mode).
     - Computes `step = (DPI / 50) - 1` and writes `step` to register `0x4E` (`RESOLUTION_X`) and `0x4F` (`RESOLUTION_Y` when independent X/Y is enabled by setting bit 2 of `0x4D`).
   - For **`10,100 <= DPI <= 19,000`**:
     - Writes `0xD0` to register `0x4D` (sets 100 CPI step mode).
     - Computes `step = (DPI / 100) - 1` and writes `step` to register `0x4E` / `0x4F`.
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
| `P+0x2B` / `P+0x2D` | `u16` LE | Current DPI X / Y (persisted copy) |
| `P+0x30` / `P+0x31` | `u8` | Stage count (max 6) / stage index (0-based) |
| `P+0x32+4i` / `P+0x34+4i` | `u16` LE | Stage `i` X / Y DPI |
| `0x20004CE4` | 27 bytes | RAM copy of `P+0x2F..`; the stage index is at `0x20004CE6` |
| **`0x20003068`** / `0x2000306A` | `u16` | **Live DPI X / Y in DPI units.** Written by every DPI path (DPI button, host HID over USB and 2.4 GHz, boot, profile switch) before the sensor is programmed at `0x17860`, which uses X only (`0x4D`/`0x4E`: 50-DPI steps below 10,100 DPI, 100-DPI steps up to 19,000). |

- **Verified on hardware (2026-11-13):** the open-directional-dpi patch reads `0x20003068` on every sensor burst. Cycling the DPI button on a retail mouse reported 400 → 800 → 1600 → 3200 (that unit's stages), three times in a row.
- A held DPI-shift ("sniper") key programs the sensor without updating `0x20003068`.
- The stage index is not a reliable stand-in for the DPI. The factory defaults boot at 1200 DPI with index 2, and the DPI-adjust wheel and the host "set DPI" command change the DPI without changing the index.

### 4.2 Millisecond tick (Inferred from disassembly; rate on the cable Verified, 2026-11-13)

- `0x0000E894` calls `0x00018B3C` with `1000`, which sets up `TIMER1` (`0x40009000`): 32-bit timer, `PRESCALER = 4` (1 MHz), `CC[0] = 1000`, `SHORTS` `COMPARE0_CLEAR`, interrupt on `COMPARE0`. So it fires every 1 ms.
- The handler (`0x00006C3A` → `0x00018BA4`) increments several tick counters, among them `0x2000301A` (`0x00018BB6`).
- The main loop at `0x00013270` tests `0x2000301A`, clears it and calls the sensor burst `0x0001708C` (`0x00013278`), moving or not. The second burst call (`0x000131D8`) runs only while the motion pin `P0.06` is low.
- So the sensor is read about once per millisecond.
- **Verified on the cable (2026-11-13):** open-directional-dpi `21ea252` counts the sensor-burst calls and reports the count in `GET_CAPABILITIES` bytes `20..23`. Read once a second for 12 s with the mouse still, it rose 1000.0 per second. Its idle reset uses this count as its millisecond clock.
- Over 2.4 GHz it was not measured: while the mouse sends no reports the receiver answers `0xA4`, so the count cannot be read with the mouse still. **Inferred** the same (same `TIMER1` tick).

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
