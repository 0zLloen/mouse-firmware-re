# Firmware Foundation Comparison & Safe Flashing Feasibility Report

## 1. Phase D — Evaluation of Firmware Foundations

We evaluated four candidate firmware architectures for custom firmware and binary extensions on the **Glorious Forge Series One Pro Wireless** (`CM2852-BAMF`, Nordic `nRF52840-QIAA-D0`, PixArt `PAW3370` / Glorious `BAMF` sensor):

1. **ZMK + Project UFA (Recommended Primary Open-Source Firmware Foundation)**
2. **Native Zephyr RTOS Firmware**
3. **QMK Firmware**
4. **Binary Patching of Stock Glorious (`CM2856_Mouse_USB`) Firmware**

### 1.1 Multi-Criteria Technical Matrix

| Evaluation Criterion | ZMK + Project UFA | Native Zephyr RTOS | QMK Firmware | Patched Stock Glorious FW (`CM2856`) |
| :--- | :--- | :--- | :--- | :--- |
| **1. nRF52840 Compatibility** | **Excellent.** First-class Zephyr/nRF52840 support (`nrf52840_qiaa`), mature `nrfx_usbd`, `settings/nvs`, and BLE/2.4G peripheral drivers. | **Excellent.** First-class upstream SoC support via Nordic `nrfx`. | **Poor / Experimental.** QMK's ChibiOS/nRF52 port is incomplete and lacks mature nRF52840 USB/power/radio integration. | **Native.** Built on Nordic nRF5 SDK specifically for this PCB. |
| **2. PAW3370 / BAMF Sensor Support** | **Blocker.** No PAW3370 driver exists in Zephyr, ZMK or known community modules (PAW3395/PMW3610 drivers do). One must be written from the stock SPI2 setup (`4 Mbps`, Mode 3) and register sequence (`0x1708C`–`0x18554`, Inferred). | **Blocker.** Same missing driver. | **Blocker.** QMK has no PAW3370 driver either. | **Native.** Full stock BAMF sensor calibration, LOD, and motion burst already present. |
| **3. GPIO & PCB Porting Effort** | **Low (Inferred, unprobed).** The `CM2852-BAMF` GPIO pinout was mapped from disassembly (`P1.15`, `P1.13`, `P1.10`, `P0.09`, `P0.10`, `P1.00`, wheel `P0.29`/`P0.02`, SPI2 `P0.04`/`P0.12`/`P0.11`/`P0.08`, IRQ `P0.06`). | **Low–Moderate.** Same devicetree pins, but requires writing custom mouse button/wheel/HID glue from scratch. | **High.** Requires new ChibiOS board definition + nRF52840 linker script at `0x6000`. | **Zero.** Already configured for `CM2852-BAMF`. |
| **4. USB HID Capabilities** | **Moderate.** Mouse + keyboard/consumer HID. ZMK has no vendor `Usage Page 0xFFFF` Feature-report interface; one would have to be added. | **High.** Full control over `usb_hid` descriptors and Feature report callbacks. | **Moderate.** Raw HID exists, but matching the 3-interface `glorious-classic` descriptor layout requires custom USB descriptors. | **Native.** Already exposes the exact `258a:2018` 3-interface layout expected by OpenMouse. |
| **5. 1,000 Hz Polling Feasibility** | **Inferred (`1 ms` interval).** Project UFA reports 1,000 Hz wired USB polling on nRF52840 mice (`CONFIG_USB_HID_POLL_INTERVAL_MS=1`). | **Inferred (`1 ms` interval).** Direct `nrfx_usbd` EP1 IN interrupt transfers at 1,000 Hz. | **Unproven on nRF52840.** | **Native.** Stock firmware runs at 1,000 Hz (`1 ms` USB / 2.4 GHz tick). |
| **6. Wireless Feasibility (`258a:2031`)** | **Moderate (Milestone 2).** Supports BLE natively and Gazell / ESB 2.4 GHz if both mouse and receiver (`258a:2031`) run ZMK, or if Compx's `NRF_RADIO` packet format (`0x00006711`) is replicated. | **Moderate.** Same as ZMK (requires custom ESB/Gazell or stock RF protocol implementation). | **Very Poor.** No 2.4 GHz dongle architecture for nRF52840. | **Immediate.** Stock 2.4 GHz proprietary RF stack (`0x00006711`) works out of the box with stock `258a:2031` receiver. |
| **7. Onboard Profile Storage** | **Excellent.** Zephyr `settings` / `nvs` subsystem mapped to dedicated flash pages `0x0007E000`–`0x0007FFFF` (preserving stock `0x00088000`+). | **Excellent.** Zephyr `nvs` / `settings`. | **Limited** on nRF52840 flash alongside Compx layout. | **High.** Can use unused flash page `0x0007E000` via stock flash driver (`0x0000BD80`). |
| **8. Custom Configuration Transport** | **Needs work.** Requires adding a vendor HID interface (see row 4) or using ZMK Studio's transport. | **Clean.** Same USB HID Feature report hook. | **Requires custom Raw HID bridging.** | **Feasible.** Can hook unused command ID `0xDF` in `Source\usb_protocol.c` (`0x0001451C`). |
| **9. Firmware Recovery Support** | **Software recovery (untested on hardware).** Linked at `0x00006000`; writes `0xA55AA55A` to `0x2003FFFC` and resets from the `LMB+RMB+DPI` combo (`&compx_bl`) or the same chord held at MCU reset. No USB command. | **Same mechanism possible.** Same `0x6000` offset and `0x2003FFFC` magic hook. | **Fragile** without custom early-boot assembly hook. | **Native.** Already implements `0x14824` (`*(uint32_t *)0x2003FFFC = 0xA55AA55A`). |
| **10. Long-Term Maintainability** | **Best.** Clean modular Zephyr/ZMK input pipeline, portable across any ZMK/Zephyr mouse, open-source license, reproducible CI builds. | **Good**, but duplicates mouse scaffolding that ZMK + UFA already maintain. | **Poor** for nRF52840 mice. | **Moderate** (binary patch tied to `CM2856 v1.00.00` addresses, though preserves stock 2.4 GHz wireless). |

---

## 2. Evidence-Based Recommendation

We recommend a **two-track engineering strategy**:

1. **Open-Source Firmware Replacement: ZMK + Project UFA (`zmk-board/`)**
   - **Why**: ZMK's `input_processor` and `zmk_pointing` subsystem supports `nRF52840` + PixArt SPI sensors at 1,000 Hz over wired USB (`CONFIG_USB_HID_POLL_INTERVAL_MS=1`). The board in `zmk-board/` builds on ZMK main, but tracking needs a PAW3370 driver that does not exist yet. Replacing the stock firmware also loses compatibility with the proprietary `258a:2031` Compx 2.4 GHz receiver unless paired with a separate Nordic ESB dongle.
2. **Surgical Stock Firmware Patching (payload at `0x0001CD00`)**
   - The open-directional-dpi patch redirects four `BL` call sites: `0x0000E874` (target `0x0000D4A0`, boot init), `0x0000E8CE` (target `0x000151AC`, USB protocol), and `0x000131D8` / `0x00013278` (target `0x0001708C`, sensor motion burst). `compx_hex_tool.py` and `compx_usb_flasher.py` can package and flash such a patched stock image at `0x00006000`, which should keep stock wired and 2.4 GHz behaviour (untested on hardware).
   - Rollback: `python3 platforms/compx-nrf52/tools/compx_usb_flasher.py flash --firmware-hex CM2856_Mouse_USB_v1.00.00_20220810.hex`.

---

## 3. Safe Flashing & Recovery Architecture

### 3.1 Safety Layers for Custom Firmware (`0x00006000`)

What protects the mouse when flashing custom firmware over the Compx USB HID bootloader (`258a:2103`), and how far each layer has been checked. Nothing here has been tried on hardware yet.

1. **Layer 1 — Image placement** (checked in a ZMK main / Zephyr 4.1 build):
   - The ZMK board uses `CONFIG_USE_DT_CODE_PARTITION=y` with `code_partition` at `0x00006000`, size `0x78000`, which yields `CONFIG_FLASH_LOAD_OFFSET=0x6000` and `CONFIG_FLASH_LOAD_SIZE=0x78000` (`480 KiB`, ending at `0x0007DFFF`).
   - The image never covers `0x00000000`–`0x00005FFF` (bootloader) or `0x00088000`+ (pairing, calibration, stock profiles), so `Program` never writes them. The bootloader's `Erase` does not clear `0x7E000` (verified); whether it reaches `0x88000`+ is Unknown.
2. **Layer 2 — Magic-word write** (checked in the build's disassembly):
   - `glorious_enter_compx_bootloader()` locks interrupts, stores `0xA55AA55A` at `0x2003FFFC` (emitted as base `0x2003F000` + offset `0xFFC`), and writes `SCB->AIRCR` with no stack use in between. No SRAM reservation is needed: the bootloader consumes the word before Zephyr runs.
3. **Layer 3 — Boot chord at `PRE_KERNEL_1`** (implemented in `zmk-board/.../bootloader_reentry.c`, present in the linked image; untested on hardware):
   - `glorious_boot_chord_recovery_check()` configures `P1.15` (Left), `P1.13` (Right) and `P1.00` (DPI) as pulled-up inputs, waits `20 us` (`k_busy_wait`), and enters the bootloader if all three read low.
   - It only runs when the MCU comes out of reset. Plugging in USB does not reset a running battery-powered mouse: disconnect the battery (or let it fully power down), hold the chord, then connect USB.
4. **Layer 4 — Runtime combo** (implemented and linked; untested on hardware):
   - The default keymap binds Left + Right + DPI pressed together to `&compx_bl`, which calls the same function.
   - **Not implemented:** re-entry from the official updater. ZMK exposes no vendor Feature-report interface and the stock entry packet is still Unknown, so `FirmwareUpdate.exe` cannot switch a ZMK mouse into the bootloader. Enter it with Layer 3 or 4 first; `compx_usb_flasher.py` then uses the enumerated `258a:2103` directly.
5. **Layer 5 — Offline audit and flash order** (`platforms/compx-nrf52/tools/`, covered by `test_tools.py`):
   - `compx_hex_tool.py` checks that the image starts exactly at `0x00006000` and ends at or below `0x0007E000`; that the initial SP is 8-byte aligned with `0x20000000 < SP <= 0x2003FFF0`; that `Reset_Handler` is a Thumb address after the `0x100`-byte vector table and inside the image; and, as a heuristic, that the magic value `0xA55AA55A` appears as a literal.
   - `compx_usb_flasher.py` programs and verifies everything else first, writes the vector-table block at `0x00006000` last, and sends `Exit` only after a full read-back. An interrupted flash leaves `0x6000` erased, so the bootloader stays resident (assuming it treats an erased vector table as "no application", which is Inferred).
