# Nordic nRF52840 SWD Programming & Hardware Recovery Feasibility

## 1. Overview & Safety Policy

This document evaluates hardware-level Serial Wire Debug (SWD) programming and recovery for the **Glorious Forge Series One Pro Wireless** (`CM2852-BAMF` main PCB, Nordic **nRF52840-QIAA-D0** MCU).

> **CRITICAL SAFETY WARNING**:
> **Never execute `nrfjprog --recover`, `openocd -c "nrf52_recover"`, `CTRL-AP ERASEALL`, mass erase, or UICR erase without explicit approval and a verified full-chip backup.**
> Executing a CTRL-AP mass erase wipes the entire 1 MB internal flash (`0x00000000`–`0x000FFFFF`) AND `UICR` (`0x10001000`), which permanently destroys:
> 1. The **24 KB Compx USB HID Bootloader (`0x00000000`–`0x00005FFF`)**, which is **not** distributed inside the official `.hex` updater files (the `.hex` file only contains `0x00006000`–`0x0001CC8F`).
> 2. The **factory 2.4 GHz wireless receiver pairing & calibration page (`0x00088000`–`0x00088FFF`)**.

---

## 2. nRF52840-QIAA-D0 SWD Signals & PCB Test Point Analysis

### 2.1 MCU Package & Silicon Revision (marking Verified from FCC photos; ball assignments from the nRF52840 aQFN73 pinout)

- **Package**: **nRF52840-QIAA-D0** (`N52840 QIAAD0 2144MX`, 73-pin aQFN `7x7 mm`, manufactured week 44 of 2021; build code **D0 = production Revision 2**).
- **Physical SWD Pins on nRF52840-QIAA**:
  - **`SWDIO`**: dedicated ball **`AC24`**
  - **`SWDCLK`**: dedicated ball **`AA24`**
  - **`nRESET`**: Shared with **`P0.18`** (pin **`AC13`**, configurable via `UICR.PSELRESET[0..1]` at `0x10001200`–`0x10001204`)
  - **`VDD`**: `3.0 V – 3.3 V` regulated rail (`VDD` balls `A22, AD14, AD23, B1, W1`; `AD2` is `VBUS`, 5 V)
  - **`GND`**: Exposed center thermal pad (`VSS`) + battery negative (`B-` black wire on `J1`)

### 2.2 PCB Test Pads on `CM2852-BAMF` (Classified Evidence)

Inspection of the FCC internal photographs (`https://fccid.io/2AXP2SP22`) for the `CM2852-BAMF 20220516` PCB shows:

| Signal | PCB Location / Pad Candidate | Evidence Classification | Verification Method Required Before Connecting |
| :--- | :--- | :--- | :--- |
| **`GND`** | Battery connector `J1` Pin 1 (Black wire), USB-C shield tabs, exposed copper ground ring | **Inferred** | Confirm `0.0 Ohm` continuity to USB-C shell |
| **`VDD` (`VREF`)** | Regulated `3.0V–3.3V` rail on decoupling capacitors adjacent to `U1` (`nRF52840`) | **Inferred** | Measure with DMM while powered (`~3.0V–3.3V` DC); **do not** use raw `VBUS` (`5.0V`) or `VBAT` (`3.7–4.2V`) as `VTref` unless debugger supports level shifting |
| **`SWDIO` & `SWDCLK`** | Cluster of 4 unpopulated round copper test pads on the bottom/top layer near `U1` (adjacent to `Y2` 32 MHz crystal and the programming header footprint used at the Compx factory) | **Inferred (Unconfirmed Pad Order)** | **Unknown exact pad-to-signal ordering** until verified via passive DMM continuity/diode check or high-resolution multimeter probe |

> **Rule**: Do **not** assume the ordering of unlabelled circular test pads on `CM2852-BAMF`. Before connecting any SWD debugger, follow the passive multimeter verification procedure in Section 5.

---

## 3. Nordic `APPROTECT` (Readback Protection) & Silicon Revision Analysis

### 3.1 `N52840 QIAAD0` (Revision 2) vs. `QIAA-F0` (Revision 3)

1. **Silicon Revision Behavior**:
   - `QIAAD0` is Nordic nRF52840 production **Revision 2**, built prior to the late-2021 `QIAA-F0` (`Rev 3`) hardware+software `APPROTECT` redesign.
   - On `QIAAD0`, access port protection is controlled solely by `NRF_UICR->APPROTECT` (`0x10001208`):
     - `0xFFFFFFFF`: **Unprotected (`Disabled`)** — full non-destructive SWD read/write access to all flash (`0x00000000`–`0x000FFFFF`), RAM (`0x20000000`–`0x2003FFFF`), and UICR (`0x10001000`–`0x10001FFF`) is immediately available!
     - `0x00000000` (or `!= 0xFF`): **Protected (`Enabled`)** — the AHB-AP (`AP[0]`) is disabled at boot, while the Nordic Control Access Port (`CTRL-AP`, `AP[1]`) remains accessible.
2. **Does Compx Stock Firmware Enable `APPROTECT` in Software?**
   - **Verified: NO!**
   - We scanned the entire `CM2856_Mouse_USB_v1.00.00_20220810.hex` (`mouse_fw.bin`) and `CM2856_Receiver_USB_v1.00.00_20220810.hex` (`receiver_fw.bin`) for references to `0x10001000` (`NRF_UICR_BASE`), `0x10001208` (`UICR->APPROTECT`), and `0x40000558` (`APPROTECT.DISABLE`).
   - Neither firmware image contains any code that checks or writes `UICR->APPROTECT`.
   - Furthermore, the Compx USB HID Bootloader (`258a:2103`) actually exposes the **`VerifyByAddress` (`0x83`) USB command**, which reads back raw flash bytes (XORed with `0x55`) over USB!
   - Whether factory programming set `UICR->APPROTECT` in the UICR sector (`0x10001208`) during manufacturing is **Unknown** without passive SWD `AP[1]` register inspection (`APPROTECTSTATUS` at `AP[1]` offset `0x00C`), which is completely non-destructive.

---

## 4. Flash Layout, Bootloader Preservation, and Radio Pairing Data

### 4.1 Complete 1 MB Flash Map (`0x00000000` – `0x000FFFFF`)

```
0x00000000 +-------------------------------------------------------+
           | Compx Resident USB HID Bootloader (24 KB)             |
           | USB VID:PID = 258a:2103                               |
           | Presumably checks *(u32 *)0x2003FFFC == 0xA55AA55A    |
0x00006000 +-------------------------------------------------------+
           | Application Firmware Region (480 KiB, ..0x0007DFFF)   |
           | Stock CM2856_Mouse_USB occupies 0x00006000..0x0001CC8F|
           | Custom ZMK / Patched FW links at 0x00006000           |
0x0007E000 +-------------------------------------------------------+
           | Custom NVS / Extension Storage (8 KiB)                |
           | Dedicated flash pages 0x7E..0x7F (never touches stock)|
0x00080000 +-------------------------------------------------------+
           | Reserved Guard Band (32 KB)                           |
0x00088000 +-------------------------------------------------------+
           | Stock System & RF Pairing Page (Page 0x88, 4 KB)      |
           | 32-byte struct + CRC-16 at +0x1E (DO NOT OVERWRITE!)  |
0x0008B000 +-------------------------------------------------------+
           | Stock Active Profile Page (Page 0x8B, 4 KB)           |
           | 614-byte struct + CRC-16 at +0x264                    |
0x0008C000 +-------------------------------------------------------+
           | Stock Extended Config Page (Page 0x8C, 4 KB)          |
           | 1227-byte struct + CRC-16 at +0x4C9                   |
0x00090000 +-------------------------------------------------------+
           | Stock Macro & Button Profile Pages (Pages 0x90..0xDF) |
0x000E0000 +-------------------------------------------------------+
           | Unused Upper Flash (0x000E0000..0x000FFFFF, 128 KB)   |
0x00100000 +-------------------------------------------------------+
```

### 4.1a USB Read-Back of a Retail Unit (Verified, 2026-11-13)

`compx_usb_flasher.py dump` on a retail mouse (firmware v1.0.0.0), entering the bootloader with the updater's own SetBLDeviceMode packet:

- `VerifyByAddress` returns data for `0x00006000`–`0x0004D7FF` and status `0xA2` from `0x0004D800` up. **USB read-back cannot reach the `0x7E000` settings page or the `0x88000`+ pairing/profile pages**, so a USB-only backup does not protect them. Only SWD could read them.
- `0x00006000`–`0x0001CC8F` is byte-identical to `CM2856_Mouse_USB_v1.00.00_20220810.hex`.
- Non-blank data that the official image does not explain sits at `0x0001CD00`–`0x0001D68F` (about 2.4 KB, right after the stock image, where the open-directional-dpi patch links), `0x00036790`–`0x0003688F`, `0x00043790`–`0x00043A8F` and `0x00044790`–`0x00044D8F`. The block at `0x0001CD00` disassembles as Thumb code that starts by reading `NRF_P1->IN` (`0x50000810`), like the open-directional-dpi boot hook: it is an earlier patch build (**Verified**: the unit's owner had flashed one before restoring stock). It survived because `Erase` keeps `0x6000`–`0x1DFFF` and a stock image never writes past `0x1CC8F`. The other three blocks were cleared by the next `Erase` (`0x1E000` and up).
- The same day the open-directional-dpi patched image (`0x6000`–`0x1D9DF`) was flashed with `compx_usb_flasher.py flash`: `Erase`, `0xFF` written to `0x6000`, everything else programmed and read back, the vector table last, then `Exit`. The mouse booted it: the version reply carries the patch's `DP01` stamp and its configuration protocol answers. This verifies the USB write path; restoring the stock `.hex` the same way has not been done yet.
- The dump is not published (it is unit-specific); its SHA-256 for this unit is `a3b734ce3c5496fb3459a75791af55421facf20af5845313fd82f7ee100e72da`.

### 4.2 Why Preserving `0x00000000`–`0x00005FFF` and `0x00088000` is Essential

1. **Bootloader (`0x00000000`–`0x00005FFF`)**:
   - The official updater (`CM2856_Mouse_USB_v1.00.00_20220810.hex`) begins at `0x00006000`. It does **not** include the first 24 KB of flash (`0x00000000`–`0x00005FFF`).
   - However, notice that the bootloader's own `VerifyByAddress` (`0x83`) command accepts a 32-bit address (`buf[8..11]`). If `VerifyByAddress` does not reject addresses `< 0x00006000`, the bootloader (`0x00000000`–`0x00005FFF`) and pairing page (`0x00088000`–`0x00088FFF`) can even be backed up read-only in bootloader mode (`258a:2103`) or via SWD if `APPROTECT` is disabled!
2. **Radio Pairing Info (`0x00088000`)**:
   - Disassembly of `0x00014700`–`0x00014770` shows that page `0x88` (`0x00088000`) holds the 32-byte system configuration block protected by CRC-16 at offset `+0x1E`, used by the proprietary 2.4 GHz radio stack (`NRF_RADIO` ISR at `0x00006711`) to authenticate with the `258a:2031` wireless receiver.
   - Keeping custom firmware strictly within `0x00006000`–`0x0007FFFF` guarantees that switching back to `CM2856_Mouse_USB_v1.00.00_20220810.hex` immediately restores wireless receiver pairing without needing to re-pair the dongle.

---

## 5. Safe Non-Destructive SWD Inspection Procedure

If you choose to inspect the PCB with an SWD debugger (e.g., SEGGER J-Link, CMSIS-DAP, Raspberry Pi Debug Probe, or ST-Link v2 running OpenOCD / `pyocd`), follow this **strictly read-only** procedure:

### Step 1 — Power Off & Locate `GND` and `VDD`
1. Unplug the USB-C cable and switch the bottom power toggle to **OFF** (or disconnect the 3-pin JST battery plug `J1`).
2. Set a digital multimeter (DMM) to continuity/resistance mode.
3. Verify `GND` between the USB-C outer shield and `J1` Pin 1 (black wire).
4. Locate the 4 unpopulated test pads near `U1` (`nRF52840-QIAA-D0`). Check which pad has `0.0 Ohm` continuity to `GND`.
5. Measure resistance from the remaining test pads to `P0.18` (`nRESET`, pin `AC13`). Note that `SWDIO` (`AC24`) and `SWDCLK` (`AA24`) are dedicated debug pins and will show high impedance (`> 1 MOhm`) to `GND` and a standard ESD diode drop (`~0.55V–0.65V` in diode mode with red probe on `GND`).

### Step 2 — Read-Only `APPROTECTSTATUS` Check (Zero Flash Modification)
Connect only `GND`, `VTref` (`3.3V` regulated rail), `SWDIO`, and `SWDCLK` to a CMSIS-DAP or J-Link probe and run OpenOCD in **passive DAP inquiry mode** (never pass `nrf52_recover`!):

```bash
# Safe, read-only DAP IDCODE and CTRL-AP APPROTECTSTATUS check:
openocd -f interface/cmsis-dap.cfg -c "transport select swd; adapter speed 1000; swd newdap nrf52 cpu -expected-id 0x2ba01477; dap create nrf52.dap -chain-position nrf52.cpu; init; nrf52.dap apreg 1 0x0c; shutdown"
```

- **Interpretation of `apreg 1 0x0c` (`CTRL-AP->APPROTECTSTATUS`)**:
  - **`0x00000001`**: **Unprotected (`APPROTECT` disabled)!** You can immediately back up the entire 1 MB flash (`0x00000000`–`0x000FFFFF`) and UICR (`0x10001000`–`0x10001FFF`) non-destructively:
    ```bash
    openocd -f interface/cmsis-dap.cfg -f target/nrf52.cfg \
      -c "init; halt; dump_image backup_full_1mb.bin 0x00000000 0x100000; dump_image backup_uicr.bin 0x10001000 0x1000; resume; shutdown"
    ```
  - **`0x00000000`**: **Readback-protected (`APPROTECT` enabled in UICR).**
    - **DO NOT run `nrf52_recover` or `nrfjprog --recover`!** Unlocking via CTRL-AP `ERASEALL` would erase the 24 KB bootloader (`0x00000000`–`0x00005FFF`) and RF pairing page (`0x00088000`).
    - Instead, use the **USB HID Bootloader (`258a:2103`)** path documented in `flashing-feasibility.md`. It only programs `0x00006000`+, so the bootloader is never written; whether its `Erase` clears the pairing page is Unknown.
