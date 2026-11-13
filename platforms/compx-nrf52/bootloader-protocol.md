# Compx / Sinowealth nRF52840 Resident USB HID Bootloader Protocol (`258a:2103` / `258a:2113`)

Many wireless gaming mice built on the **Compx Technology + Nordic nRF52840** OEM platform (USB Vendor ID `0x258a`) keep a resident USB HID In-System Programming (ISP) bootloader at the bottom of flash. Its size is device-specific:

| Device | Bootloader PID | Bootloader | Application base | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| Glorious Series One Pro mouse | `258a:2103` | 24 KB, `0x00000000`–`0x00005FFF` | `0x00006000` | **Inferred** (lowest address of the official mouse `.hex`) |
| Glorious Series One Pro receiver | `258a:2113` | 28 KB, `0x00000000`–`0x00006FFF` | `0x00007000` | **Inferred** (lowest address of the official receiver `.hex`) |

Labels follow the repository policy in `README.md`. The bootloader binary itself has never been read (it is not in the updater `.hex` files and SWD access has not been attempted), so everything about its internal behaviour is **Inferred** from the stock application and `FirmwareUpdate.exe`, or **Unknown**.

---

## 1. Bootloader Entry via SRAM Magic Word (`0x2003FFFC = 0xA55AA55A`)

- **Inferred** (stock application disassembly, `devices/glorious-series-one-pro/firmware-updater.md` 4.1): the stock mouse application writes `0xA55AA55A` to `0x2003FFFC` (top word of its 256 KB SRAM, `CM2856_Mouse_USB_v1.00.00_20220810.hex @ 0x0001484C`) and resets; the mouse then enumerates as `258a:2103` (Verified). The receiver application writes the same word to `0x20007FFC` (top word of 32 KB, `CM2856_Receiver_USB_v1.00.00_20220810.hex @ 0x0000C7A8`, `devices/glorious-series-one-pro/receiver.md`) and should then enumerate as `258a:2113` (never seen). Both bootloaders wait for 64-byte HID Feature reports on Interface 2 (`Usage Page 0xFFFF`, Report ID `0x00`).
- **Unknown**: whether the bootloader clears the magic word, and any other condition (e.g. an erased vector table at the application base) that keeps it resident.
- **Verified — app-to-bootloader packet** ("set device mode", command `0x00`, mode `0xB0`): `feat[2] = deviceID` (`0x00` for the wired mouse), `feat[3] = 0x01` (length), `feat[6] = 0xB0`, all other bytes `0x00`.
  - Read from the IL of `FirmwareUpdate.exe` 1.0.8229.18185 (SHA-256 `37ae7be7…`), `C52840Dongle::SetBLDeviceMode`: `buf[3] = deviceID; buf[4] = 1; buf[7] = 0xB0` with `buf[0]` the report ID. `deviceID` comes from `Config.ini` `[Debug] DeviceID`, which is `0` in the wired package.
  - Tested on a retail Series One Pro on firmware v1.0.0.0 (2026-11-13): the mouse re-enumerated as `258a:2103` (iManufacturer `Glorious`, iProduct `Glorious bootloader`) and answered `GetBLFW` with `b0 01 00 00`. The SET itself fails with `EPROTO` because the mouse resets while answering.
  - Two earlier write-ups (`feat[3] = 0x02, feat[6] = 0xB0` and `feat[5] = 0x06, feat[6] = 0xB0, feat[7] = 0x04`) were wrong.
  - In the same naming, the updater's other application-mode commands are `GetFW` (`feat[3] = 0x03, feat[5] = 0x81`) and `GetDeviceMode` (`feat[3] = 0x01, feat[5] = 0x80`).

---

## 2. Bootloader Feature Report Commands (`Report ID 0x00`, 64-Byte Payload)

When passing buffers to Linux `ioctl(HIDIOCSFEATURE(65))` or Windows `HidD_SetFeature`, byte `0` is the Report ID (`0x00`) and bytes `1..64` are the 64-byte payload (`feat[0..63]`). Command layouts are read from the `FirmwareUpdate.exe` IL (`C52840Dongle`); every command also carries `feat[2] = deviceID` (`0x00` for the wired mouse). All five were exercised on a retail mouse on 2026-11-13, ending in a verified flash of a patched image.

| Command | `feat[3]` (Len) | `feat[4]` | `feat[5]` (Opcode) | `feat[6..10]` | `feat[11..63]` | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`GetBLFW`** | `0x06` | `0xB0` | `0x80` | `00..` | `00..` | Returns `feat[0] = 0xA1` (OK) and 4-byte bootloader version at `feat[8..11]`. **Verified**: `b0 01 00 00` on a retail mouse. |
| **`Erase`** | `0x08` | `0xB0` | `0x01` | `00..` | `00..` | **Verified** on a retail mouse: it cleared `0x0001E000` up to at least `0x0004D7FF` (the readable top) but left `0x00006000`–`0x0001DFFF`, the previously flashed image, in place. It does **not** reach `0x7E000`: a 28-byte profile saved there by the open-directional-dpi patch survived two full flashes (Verified, 2026-11-13); whether it reaches the `0x88000`+ pairing/profile pages is **Unknown** (not readable over USB). It does **not** blank the vector table, so the flasher writes `0xFF` to `0x6000` first (`Program` is read-modify-write). Before that write the old image stays bootable only if it ended below `0x1E000` (stock, the open-directional-dpi patch); a larger one would boot with its tail erased. After it, `0x6000` reads `0xFF`, and whether the bootloader then stays resident is **Unknown**. |
| **`Program`** | `len + 5` | `0xB0` | `0x02` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `data[0..len-1] ^ 0x55` | The bootloader XORs each byte with `0x55` and writes it to flash at `addr`. The XOR applies on the USB wire only; the `.hex` files are plain. **Verified** read-modify-write: 32 bytes written over non-erased flash read back exactly as sent (bits went 0 → 1) and the rest of the 4 KB page was unchanged, so no prior erase is needed and the write order is free. |
| **`VerifyByAddress`** | `0x20` | `0xB0` | `0x83` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `00..` | Reads `len` bytes from flash at `addr`, XORs them with `0x55`, and returns them at `feat[11..11+len-1]`. **Verified** on a retail mouse: reads from `0x00006000` up to `0x0004D7FF` succeed and match the official image; from `0x0004D800` up the status is `0xA2`, so the pairing/profile pages at `0x88000`+ cannot be read over USB. Whether addresses below `0x6000` are refused is **Unknown** (not tried). `compx_usb_flasher.py flash` refuses images ending above `0x4D800`, because they cannot be read back. |
| **`Exit`** | `0x01` | `0xB0` | `0x04` | `feat[6] = 0x01` | `00..` | Resets into the application at the application base. **Verified** with a valid stock application present (the SET fails with `ETIMEDOUT` as the device resets). Any validation it performs is **Unknown**. |

Status byte `feat[0]` (**Inferred**, `CPMXHIDDevice` result codes): `0xA1`/`0x02` success, `0xA2` fail, `0xA3` unknown command, `0xA4` out of range. These are bootloader replies. In application mode the Glorious receiver `258a:2031` answers `0xA0` while a relayed request is pending and `0xA4` when the mouse cannot be reached (`devices/glorious-series-one-pro/receiver.md`).
