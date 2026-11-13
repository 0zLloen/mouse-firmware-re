# Glorious CORE v1 & Series One Pro Official Firmware Updater Analysis

## 1. Official Software & Firmware Package Provenance

All official installers, manifests, and firmware archives were inspected offline without executing any updater against the physical hardware.

### 1.1 Origins, Versions, and SHA-256 Hashes

| Artifact / File Name | Version / Date | Size (Bytes) | SHA-256 Hash | Origin / Container |
| :--- | :--- | :--- | :--- | :--- |
| `GloriousCore.zip` | `1.1.36` | `164,988,882` | `31327ec4deadbf672b05b78c48a826270f361216cdac7134f74379a9f5a0190a` | Glorious Legacy Software (`https://www.gloriousgaming.com/pages/legacy-software`) |
| `GloriousCore_Setup_1.1.36.exe` | `1.1.36` | `165,458,232` | `7f68626ebf7d61659629808d48936d5f404921a4728624f146e99acbc07e4f9d` | Inside `GloriousCore.zip` (Inno Setup 6.0.5 archive) |
| `manifest.json` (AWS S3 bucket index) | `2022–2024` | `460,572` | `596fb696e34fe1c0a09465ac15b405caa7e0d9e5b851fb539b1fa7f42724026a` | `https://gloriouscore.nyc3.digitaloceanspaces.com` / S3 firmware bucket listing |
| `v1.0.0.0_20220810_Series_One_ProFirmware.zip` | `v1.0.0.0` (`2022-08-10`) | `2,775,849` | `c9db15ef2ec11f8d44508ae16e0f98d22a60ba83a727163f7a0b7b1c5dd87814` | Official Glorious Series One Pro firmware package (`DevFw/v1.0.0.0_20220810_Series_One_ProFirmware.zip`) |
| `v1.0.0.0_20220810_Series_One_ProFirmware.exe` | `v1.0.0.0` | `1,487,064` | `e9e6c4fbd14c935cd31a2daa4992d20ac328bfd272b35e59233f09789b3c2576` | Self-extracting archive inside `Series_One_ProFirmware.zip` (mouse updater) |
| `v1.0.0.0_20220810_Series_One_ProFirmwareWireless.exe` | `v1.0.0.0` | `1,415,528` | `5f8d1eb132ad14a19eea9368ddd7e94b7b8aae6f40d6dcc6311410213f3e6df5` | Self-extracting archive inside `Series_One_ProFirmware.zip` (receiver updater) |
| `FirmwareUpdate.exe` (.NET Framework v4.0) | `1.0.8229.18185` | `1,540,960` | `37ae7be701ba36ec987ec86d58b07819834aab0385ac4345138f49e9d9511ea5` | Extracted from both `.exe` updaters |
| `XVI.zip` (Wired payload, password `XVI`) | `2022-08-10` | `204,549` | `1da4a8a8ff3ba1613f21a46fd44e11b0a7aea2e52cfe494253fbc1283cc78877` | Embedded in `v1.0.0.0_20220810_Series_One_ProFirmware.exe` |
| `XVI.zip` (Wireless payload, password `XVI`) | `2022-08-10` | `133,111` | `bb3321d71b38fc85528fa977e29741189037b1c8c5644b4481a5860e55de825f` | Embedded in `v1.0.0.0_20220810_Series_One_ProFirmwareWireless.exe` |
| `CM2856_Mouse_USB_v1.00.00_20220810.hex` | `v1.00.00` | `262,553` | `2c5c1672ca0415a8f557c8a1dda3b71390fb2b97a226f3fc3cde78e01d188a92` | Wired `XVI.zip` only (mouse application, Intel HEX) |
| `CM2856_Receiver_USB_v1.00.00_20220810.hex` | `v1.00.00` | `90,792` | `e60c5dbc853b3f07323efa9571e609813ae649eea9f92b67160f5a85ef0f103e` | Wireless `XVI.zip` only (receiver application, Intel HEX) |
| `SnxHidLib.DLL` | — | `154,112` | `cb5e3d8e691771389b47d8bb0b2c3bb2e22cdd9865084b231dcfcd3c4c3e462e` | Next to `FirmwareUpdate.exe` in both `.exe` and inside both `XVI.zip` |
| `Config.ini` (wired) | — | `789` | `b15c58d2a4ea4d6d3a82eb8babf068b001a2a20909aa380458c2ad03b420bb0e` | Wired `XVI.zip` (`[Nordic]` `FWFile` = mouse `.hex`, `BLPID = 2103`, `NewPID = 2018`) |
| `Config.ini` (wireless) | — | `792` | `b47144d09e00ebe504fa769e72ece963d7b6ceb980a465c0fcb281d3e05b9e0c` | Wireless `XVI.zip` (`[Nordic]` `FWFile` = receiver `.hex`, `BLPID = 2113`, `NewPID = 2031`) |
| `log4net.config` | — | `2,618` | `bd09bcba14a63525deb48d610652fd73444ed50f4cb5ce1f200f25692129e0cb` | Both `XVI.zip` |
| `DX_264_Dongle_v0.03.09.05_20200521.hex` | `v0.03.09.05` | `20,863` | `1fe9d0d2aa22d40d350ec367deac7fb4668f2910ebba496b598bf249932c5567` | Both `XVI.zip`; for the `[Sonix]` updater type (`SN32F26x`, `264a:1026`), not used by this mouse |
| `DX_Receiver_810_v0.03.09_20200521.hex` | `v0.03.09` | `69,192` | `5e86305886ab5e0f7ac930d8199c45d1b0232fe8d4cf8698a16fb332cdc08e8c` | Both `XVI.zip`; `[Sonix]` updater type, not used by this mouse |

---

> **Availability (checked 2026-11-13).** `GloriousCore.zip` still downloads from `https://downloads.gloriousgamingservices.com/download/GloriousCore.zip` (SHA-256 above), but it does **not** contain the Series One Pro updater: Glorious Core fetches it on demand. Its update manifest `https://gloriouscore.nyc3.digitaloceanspaces.com/Glorious_Core/Version.json` (still public) points the Series One Pro (`0x258A`/`0x2018`, version 1.0.0.0) to `https://nyc3.digitaloceanspaces.com/gloriouscore/Glorious_Core/v1.0.0.0_20220810_Series_One_ProFirmware.zip`, which now returns HTTP 403, as does every other firmware package in that bucket; the Wayback Machine has no copy. Keep your own copy of the package and check it against the SHA-256 above. The sizes of the Series One Pro package and everything in it were re-measured with `stat` and the archive listings on that date; the earlier values were wrong.

## 2. Extraction & Packaging Chain

1. **Glorious CORE v1 Backend (`GloriousCore_Setup_1.1.36.exe`)**:
   - Extracted via `innoextract`. Contains an Electron frontend and Node.js backend (`app/resources/app/backend/DeviceApi.node`).
   - In `DeviceApi.node`, the Series One Pro is identified by `{VID: 0x258A, PID: 0x2018}` (wired) and `{VID: 0x258A, PID: 0x2031}` (wireless receiver) and routes firmware updates to the external standalone Compx updater binaries inside `DevFw/v1.0.0.0_20220810_Series_One_ProFirmware.zip`.
2. **Compx Self-Extracting Wrapper (`Series_One_ProFirmware*.exe`)**:
   - Each contains only `FirmwareUpdate.exe` (.NET), `SnxHidLib.DLL` and a password-protected ZIP archive `XVI.zip` (`7z l`, 2026-11-13).
   - The wired `XVI.zip` holds the mouse `.hex`, the wireless one the receiver `.hex`; each also holds `Config.ini`, `log4net.config`, `SnxHidLib.DLL` and two Sonix dongle images for another updater type (table above).
   - Decompiling `FirmwareUpdate.Form1::UnZipUpdateFile()` confirms the hardcoded ZIP password is **`"XVI"`** (`fastZip.Password = "XVI"`).
3. **Intel HEX Payload & USB Wire Obfuscation (`HexHelper::LoadHexFile` & `C52840Dongle::Program`)**:
   - `CM2856_Mouse_USB_v1.00.00_20220810.hex` and `CM2856_Receiver_USB_v1.00.00_20220810.hex` are standard, unencrypted Intel HEX files containing raw ARM Thumb-2 machine code (mouse: `Initial_SP = 0x2000AF50`, `Reset_Handler = 0x000063DD`; receiver: `0x200026E0`, `0x000074C1`).
   - When `C52840Dongle::Program` transmits 32-byte flash chunks to the bootloader over USB HID Feature reports (`buf[6] = 0x02`), it XORs every payload byte on the wire with **`0x55`** (`buf[12 + i] = data[i] ^ 0x55`), and the bootloader on the nRF52840 XORs each byte with `0x55` before writing it to NVMC flash!
   - During verification (`C52840Dongle::VerifyByAddress`, `buf[6] = 0x83`), the bootloader reads back 32 bytes from flash, XORs them with `0x55`, and returns them over USB so `FirmwareUpdate.exe` can compare them byte-for-byte against `hexData ^ 0x55`.

---

## 3. Firmware Image Layout & Cryptographic Validation Analysis

### 3.1 Are Firmware Images Encrypted, Signed, Compressed, or Checksummed?

- **Encryption**: **NO.** The `.hex` files are plain Intel HEX. The only obfuscation is a single-byte XOR (`^ 0x55`) applied to `Program`/`VerifyByAddress` payloads on the USB wire.
- **Cryptographic Signature**: **NO.** Neither `FirmwareUpdate.exe`, the `.hex` files, nor the bootloader performs RSA, ECDSA, Ed25519, or HMAC signature verification.
- **Compression**: **NO** on-chip compression. The `.hex` files unpack directly to raw ARM Cortex-M4F Thumb-2 machine code (`mouse_fw.bin` = `93,328` bytes; `receiver_fw.bin` = `32,260` bytes).
- **Checksums / Validation**:
  1. Standard 8-bit two's-complement Intel HEX per-line checksum in the `.hex` file (verified by `HexHelper::LoadHexFile`: **100% of all 5,837 records in `CM2856_Mouse_USB_v1.00.00_20220810.hex` and 2,020 records in `CM2856_Receiver_USB_v1.00.00_20220810.hex` pass checksum validation**).
  2. Readback verification (`C52840Dongle::VerifyByAddress`, command `0x83`): `FirmwareUpdate.exe` reads back every 32-byte block from `[startAddr .. startAddr + len]` and compares it byte-for-byte with the transmitted buffer before sending the `Exit` (`0x04`) command.

### 3.2 Do Updates Replace the Application, Bootloader, or Both?

- **Application-Only Replacement (Verified)**:
  - **Mouse (`CM2856_Mouse_USB_v1.00.00_20220810.hex`)**:
    - Extended Linear Address (`04`): `0x0000`
    - Start of Data (`00`): **`0x00006000`**
    - End of Data (`00`): **`0x0001CC8F`** (`93,328` bytes = `0x16C90` bytes)
    - Entry Point (`05` Start Linear Address): **`0x00006201`** (Keil `__main` scatter-loader; the vector table's `Reset_Handler` word at `0x00006004` is `0x000063DD`).
    - The update data starts at `0x00006000`, so `Program` never writes the resident **24 KB Compx USB HID Bootloader at `0x00000000`–`0x00005FFF`**. `Erase` does not clear it either: it keeps `0x00006000`–`0x0001DFFF`, and the bootloader kept working through several flashes (Verified on a retail mouse, 2026-11-13).
  - **Wireless Receiver (`CM2856_Receiver_USB_v1.00.00_20220810.hex`)**:
    - Start of Data (`00`): **`0x00007000`**
    - End of Data (`00`): **`0x0000EE03`** (`32,260` bytes = `0x7E04` bytes)
    - Entry Point (`05` Start Linear Address): **`0x00007201`** (`Reset_Handler` at `0x00007004` is `0x000074C1`).
    - The update data starts at `0x00007000`, so `Program` never writes the resident **28 KB Receiver Bootloader at `0x00000000`–`0x00006FFF`**. The receiver's `Erase` extent is **Unknown** (never flashed here).

### 3.3 Mouse Flash & SRAM Map (Inferred from Firmware Disassembly)

Disassembly of `0x0000BD80`–`0x0000BF30` (flash page erase/write wrapper) and `0x00014500`–`0x00014AD0` (`Source\usb_protocol.c`) reveals the exact flash and SRAM partitioning:

| Address Range | Size | Purpose | Evidence Reference |
| :--- | :--- | :--- | :--- |
| `0x00000000` – `0x00005FFF` | `24 KiB` | **Compx Resident USB HID Bootloader (`258a:2103`)** | App `.hex` starts at `0x00006000`; never programmed by the updater |
| `0x00006000` – `0x0001CC8F` | `91.1 KiB` (`93,328 B`) | **Mouse Application Firmware (`CM2856_Mouse_USB`)** | Vector table at `0x00006000` (`Initial_SP = 0x2000AF50`) |
| `0x0001CC90` – `0x0007DFFF` | `388.9 KiB` | Unused application flash, available for custom firmware growth | Not in the `.hex`. `Erase` does **not** blank `0x6000`–`0x1DFFF` (Verified 2026-11-13): leftovers of an earlier image survive there; `compx_usb_flasher.py` flashes and verifies only up to `0x4D7FF` (`VerifyByAddress` answers `0xA2` from `0x4D800`) |
| `0x0007E000` – `0x0007FFFF` | `8 KiB` | Reserved for custom-firmware settings (not used by stock) | Repository convention. The open-directional-dpi patch saves its profile at `0x7E000`; it survived two full flashes, so `Erase` does not reach it (Verified 2026-11-13) |
| `0x00080000` – `0x00087FFF` | `32 KiB` | Guard band, unused by stock as far as the disassembly shows | — |
| `0x00088000` – `0x00088FFF` | `4 KB` | **System Config Page `0x88`** (`32` bytes + CRC-16 at `+0x1E`, factory/RF pairing data) | Read/written at `0x14700`–`0x14770` (`page 0x88`) |
| `0x0008B000` – `0x0008BFFF` | `4 KB` | **Active Profile Config Page `0x8B`** (`0x266` = `614` bytes, CRC-16 at `+0x264`, factory defaults at `0x0001A3B8`) | Read/written at `0x14668`–`0x146B4` (`page 0x8B`) |
| `0x0008C000` – `0x0008CFFF` | `4 KB` | **Extended Config Page `0x8C`** (`0x4CB` = `1227` bytes, CRC-16 at `+0x4C9`) | Read/written at `0x146B8`–`0x14700` (`page 0x8C`) |
| `0x00090000` – `0x000DFFFF` | `320 KB` | **Per-Button Macro & Profile Pages (`0x90..0xDF`)** | Erased/written by HID commands `0x07`–`0x09` |
| `0x20000000` – `0x2000AF4F` | `~43.8 KiB` | Stock `RW`/`ZI` + stack (initial SP `0x2000AF50`; exact `RW`/`ZI` extent not measured) | Keil scatter map at `0x0001AAE4` |
| `0x2000AF50` – `0x2003FFFB` | `~212 KiB` | Not used by stock as far as the scatter map shows (open-directional-dpi keeps its state at `0x2003F000` on that assumption) | Inferred |
| **`0x2003FFFC` – `0x2003FFFF`** | **`4 Bytes`** | **Bootloader Entry Magic Word (`0xA55AA55A`)** | Written at `0x00014850` before `NVIC_SystemReset()` (4.1) |

> **Preservation Rule**: Flash pages `0x00088000` (`page 0x88`), `0x0008B000` (`page 0x8B`), and `0x0008C000` (`page 0x8C`) store radio pairing and calibration/profile structures. Custom firmware keeps its code/rodata in `0x00006000`–`0x0004D7FF` (the USB-verifiable part of `0x00006000`–`0x0007DFFF`; `compx_usb_flasher.py` refuses larger images) and its own settings in `0x0007E000`–`0x0007FFFF`, so `Program` never writes the stock pages at `0x00088000`+. Whether the bootloader's `Erase` leaves them intact is **Unknown** (see 4.2). **Inferred** that it does: the mouse still linked to its receiver after at least five `Erase` runs on 2026-11-13 (the flash logs of that day and the receiver relay tests after them). The pages are not readable over USB, so this is not checked directly.

---

## 4. Reverse-Engineered Compx USB HID Bootloader Protocol

All communication with both the application (`258a:2018` / `258a:2031`) and the bootloader (`258a:2103` / `258a:2113`) uses **64-byte HID Feature Reports on Interface 2 (`Usage Page 0xFFFF`, Report ID `0x00`)**.

In Windows HID / Linux `hidraw` / WebHID, the buffer passed to `HidD_SetFeature` / `ioctl(HIDIOCSFEATURE)` is **65 bytes**: `buf[0] = 0x00` (Report ID), followed by the 64-byte payload `buf[1..64]`.

### 4.1 Step 1 — Switching from Application Mode to Bootloader Mode

> **Verified (2026-11-13).** `C52840Dongle::SetBLDeviceMode` in the IL of `FirmwareUpdate.exe` 1.0.8229.18185 builds `buf[3] = deviceID` (`0` for the wired mouse, `Config.ini` `[Debug] DeviceID`), `buf[4] = 0x01`, `buf[7] = 0xB0`, everything else `0x00` (`buf[0]` is the report ID). That is "set device mode" (command `0x00`, length 1) with mode `0xB0`. Sent by `platforms/compx-nrf52/tools/compx_usb_flasher.py` to a retail mouse on v1.0.0.0, it switched the mouse to `258a:2103` (`Glorious bootloader`). An earlier version of this section gave `buf[6] = 0x06, buf[7] = 0xB0, buf[8] = 0x04, buf[9] = 0x00` as this packet; that was wrong. `0xB0 0x04` is the bootloader's own `Exit` command.

```
buf[0] = 0x00  // Report ID
buf[3] = 0x00  // deviceID (wired mouse)
buf[4] = 0x01  // length
buf[7] = 0xB0  // mode: bootloader (command buf[6] = 0x00 "set device mode")
```

Disassembly of the stock firmware shows how the application then enters the bootloader (Inferred; the whole path also runs in Unicorn in open-directional-dpi's `build_and_verify_patch.py` test 4, 2026-11-13):

- **Dispatch.** `0x000151AC` takes command group `body[4] = 0` to `0x00014788`, which takes command `body[5] = 0x00` ("set device mode") to `0x00014816`. See [`hardware.md`](hardware.md) section 2.3.
- **Magic word and reset**:
       ```asm
       0x14824: cmp  r0, #0xB0           ; r0 = mode, body[6]
       0x14826: beq  0x1484C
       ...
       0x1484C: ldr  r1, [pc, #0x174]    ; r1 = 0x2003FFFC (literal at 0x149C4, top word of the 256 KB SRAM)
       0x1484E: ldr  r0, [pc, #0x170]    ; r0 = 0xA55AA55A (literal at 0x149C0, bootloader magic word)
       0x14850: str  r0, [r1]            ; *(volatile uint32_t *)0x2003FFFC = 0xA55AA55A
       0x14852: bl   0x71B8              ; NVIC_SystemReset(): dsb, SCB->AIRCR = 0x05FA0004
       ```
     - The reset follows the store directly; there is no timer in between. An earlier version of this section placed the store at `0x00014832`, where the bytes are a `bne` of the mode compare chain.
   - Upon reset, the resident Compx Bootloader (`0x00000000`–`0x00005FFF`) presumably checks `*(uint32_t *)0x2003FFFC` (Inferred: the bootloader has never been read; whether it clears the word is Unknown). Verified: the mouse then enumerates as **`258a:2103`** (iProduct `Glorious bootloader`) and accepts flashing commands.

### 4.2 Step 2 — Bootloader Command Packet Structure (`258a:2103` & `258a:2113`)

Once enumerated as `258a:2103` (Wired Mouse Bootloader) or `258a:2113` (Wireless Receiver Bootloader), `C52840Dongle` communicates via `SetFeature` (`CPMXHIDDevice::SetFeature`) and `GetFeature` (`CPMXHIDDevice::GetFeature` after a retry delay of `15 ms`):

| Command Name | `buf[3]` | `buf[4]` (Len) | `buf[5]` | `buf[6]` (Cmd) | `buf[7..11]` | `buf[12..64]` | Response / Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`GetBLFW`** | `0x00` | `0x06` | `0xB0` | `0x80` | `0x00..` | `0x00..` | `GetFeature` returns `resp[1] == 0xA1` (OK) and 4-byte BL version at `resp[9..12]` (must contain `0xB0`). |
| **`Erase`** | `0x00` | `0x08` | `0xB0` | `0x01` | `0x00..` | `0x00..` | **Verified** on the mouse: clears `0x0001E000` up to at least `0x0004D7FF` but **keeps `0x00006000`–`0x0001DFFF`**, including the old vector table. It does **not** reach `0x7E000`: a 28-byte profile saved there by the open-directional-dpi patch survived two full flashes (Verified, 2026-11-13); whether it reaches the `0x88000`+ pairing/profile pages is **Unknown** (not readable over USB); the receiver was not tested. |
| **`Program`** | `0x00` | `len + 5` | `0xB0` | `0x02` | `buf[7] = len` (`<= 32`)<br>`buf[8..11] = addr` (Big-Endian `uint32`) | Up to 32 bytes of `data ^ 0x55` (`buf[12 .. 12+len-1]`) | Bootloader XORs each byte with `0x55` and writes to `addr`. Host sleeps `10 ms` every `4096` (`0x1000`) bytes. |
| **`VerifyByAddress`** | `0x00` | `0x20` | `0xB0` | `0x83` | `buf[7] = len` (`<= 32`)<br>`buf[8..11] = addr` (Big-Endian `uint32`) | `0x00..` | `GetFeature` returns `resp[1] == 0xA1` and `len` bytes at `resp[12 .. 12+len-1]` (XORed with `0x55`). |
| **`Exit`** | `0x00` | `0x01` | `0xB0` | `0x04` | `buf[7] = mode` (`0x01` = boot app) | `0x00..` | Resets into the application at `0x00006000` (Verified; the SET fails with `ETIMEDOUT` as the device resets). Any validation it performs is **Unknown**. |

- **Status Codes in `resp[1]` (`CPMXHIDDevice::SetFeature` / `GetFeature`)**:
  - `0xA1` or `0x02`: Command Succeeded (`Result_Succeed`)
  - `0xA2`: Command Failed (`Result_Fail`)
  - `0xA3`: Unknown Command (`Result_UnkownCommand`)
  - `0xA4`: Out of Range (`Result_OutOfRange`)
- These are the updater's names for bootloader replies. In application mode the receiver `258a:2031` uses `0xA0` for "reply pending" and `0xA4` for "the mouse cannot be reached" ([`receiver.md`](receiver.md), Verified 2026-11-13).

### 4.3 Wired Mouse vs. Wireless Receiver Update Procedures

Each package carries one `Config.ini` whose `[Nordic]` section names one image and one device (Verified, read from the files):
- **`v1.0.0.0_20220810_Series_One_ProFirmware.exe` (mouse)**:
  - `XVI.zip` holds `CM2856_Mouse_USB_v1.00.00_20220810.hex`; `Config.ini`: `BLPID = 2103`, `NewPID = 2018`.
  - Updates `258a:2018` -> `258a:2103` (`C52840Dongle(0x258A, 0x2018, 0x2103, ..., isDongle=false)`, decompiled `Form1::InitDevice()`).
- **`v1.0.0.0_20220810_Series_One_ProFirmwareWireless.exe` (receiver)**:
  - `XVI.zip` holds `CM2856_Receiver_USB_v1.00.00_20220810.hex` and no mouse image; `Config.ini`: `BLPID = 2113`, `NewPID = 2031`.
  - So it updates only the receiver (`258a:2031` -> `258a:2113`). An earlier version of this section said it updates the mouse and the receiver in turn and needs both plugged in (`deviceList.Count == 2`); that is **Unknown** until `Form1::StartUpdateThread()` is re-read. Nothing points to over-the-air (RF) flashing.

---

## 5. Conclusion: Can the Official Updater Safely Install Custom Firmware?

**Yes over USB, with care.** A patched image was flashed through the bootloader and booted on a retail unit (2026-11-13, `compx_usb_flasher.py`, not the official updater). The rules below still apply:

1. **No Cryptographic Lockout**: Neither `FirmwareUpdate.exe` nor the protocol shows any signature check; the payload is only XORed with `0x55` on the USB wire. A valid ARM Cortex-M4F image linked at **`0x00006000`** and packaged as a plain Intel HEX file (like `CM2856_Mouse_USB_v1.00.00_20220810.hex`) should be accepted, programmed, verified and booted by the stock bootloader.
2. **Resident Bootloader Preservation**: `Program` (`0x02`) writes start at the application base (`>= 0x00006000`), so the 24 KB bootloader at `0x00000000`–`0x00005FFF` is not written. `Erase` (`0x01`) clears `0x1E000` up to at least `0x4D7FF`, keeps `0x6000`–`0x1DFFF` and does not reach the `0x7E000` settings page (Verified 2026-11-13). Whether it reaches the `0x88000`+ pairing and profile pages is Unknown (Inferred not: the receiver link survived at least five `Erase` runs).
3. **Mandatory Bootloader Re-Entry Mechanism in Custom Firmware**:
   - Because the bootloader normally jumps straight to `0x00006000` when a valid application header/vector table is present unless `*(volatile uint32_t *)0x2003FFFC == 0xA55AA55A`, **any custom firmware flashed via USB MUST include**:
     1. **Boot-Chord Hardware Fallback** (implemented in the open-directional-dpi patch and the ZMK board; **untested on hardware** in both): On startup (inside `SYS_INIT(PRE_KERNEL_1)` before initializing USB or sensors), sample the physical Left + Right + DPI buttons (`P1.15`, `P1.13`, `P1.00`). If held low at power-on/reset, immediately write `0xA55AA55A` to `*(volatile uint32_t *)0x2003FFFC` and call `NVIC_SystemReset()`.
     2. **Runtime re-entry**: a button combo or a USB command that writes `0xA55AA55A` to `0x2003FFFC` and resets. The ZMK board uses a Left+Right+DPI combo (`&compx_bl`); a USB Feature-report command would need a vendor HID interface on interface 2 that answers the stock entry packet (`body[3] = 0x01, body[6] = 0xB0`, section 4.1).
   - The boot chord is sampled only when the MCU comes out of reset. Plugging in USB does **not** reset a running, battery-powered mouse (VBUS only wakes it from System OFF). To use it: disconnect the battery (or let the mouse fully power down), hold Left+Right+DPI, then connect USB.
   - Once the mouse enumerates as `258a:2103`, restore stock firmware with `python3 platforms/compx-nrf52/tools/compx_usb_flasher.py flash --firmware-hex CM2856_Mouse_USB_v1.00.00_20220810.hex`.
