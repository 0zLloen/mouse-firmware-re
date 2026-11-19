# Compx / Sinowealth nRF52840 Resident USB HID Bootloader Protocol (`258a:2103` / `258a:2113`)

Many wireless gaming mice built on the **Compx Technology + Nordic nRF52840** OEM platform (USB Vendor ID `0x258a`) keep a resident USB HID In-System Programming (ISP) bootloader at the bottom of flash. Its size is device-specific:

| Device | Bootloader PID | Bootloader | Application base | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| Glorious Series One Pro mouse | `258a:2103` | 24 KB, `0x00000000`–`0x00005FFF` (code up to `0x4FFF`, flag page `0x5000`) | `0x00006000` | **Verified** (read from a unit, 2026-11-19, section 3) |
| Glorious Series One Pro receiver | `258a:2113` | 28 KB, `0x00000000`–`0x00006FFF` | `0x00007000` | **Inferred** (lowest address of the official receiver `.hex`) |

Labels follow the repository policy in `README.md`. The bootloader is not in the updater `.hex` files. The **mouse** bootloader was read from a retail unit on 2026-11-19 (section 3), so its behaviour below is from its own code (**Inferred** from disassembly, checked on the unit where marked). The **receiver** bootloader has never been read.

---

## 1. Bootloader Entry via SRAM Magic Word (`0x2003FFFC = 0xA55AA55A`)

- **Inferred** (stock application disassembly, `devices/glorious-series-one-pro/firmware-updater.md` 4.1): the stock mouse application writes `0xA55AA55A` to `0x2003FFFC` (top word of its 256 KB SRAM, `CM2856_Mouse_USB_v1.00.00_20220810.hex @ 0x0001484C`) and resets; the mouse then enumerates as `258a:2103` (Verified). The receiver application writes the same word to `0x20007FFC` (top word of 32 KB, `CM2856_Receiver_USB_v1.00.00_20220810.hex @ 0x0000C7A8`, `devices/glorious-series-one-pro/receiver.md`) and should then enumerate as `258a:2113` (never seen). The mouse bootloader takes 64-byte HID Feature reports (`Usage Page 0xFFFF`, `Usage 0x00`, Report ID `0x00`) on its only interface, interface 0: bcdDevice `0x0200`, 500 mA, no serial string, 20-byte report descriptor `06ffff0900a10109001500250175089540b102c0`, byte for byte the descriptor of the application's interface 2 (**Verified**, `lsusb -v` and sysfs on a retail mouse, 2026-11-18). The receiver bootloader is assumed to do the same (never seen).
- The mouse bootloader honours the word only after a soft reset and clears it when it does (section 3.2). It also stays resident when its flag page marks no valid application, or when Back + Forward + DPI are held at power-up (section 3.2).
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
| **`GetBLFW`** | `0x06` | `0xB0` | `0x80` | `00..` | `00..` | Returns `feat[0] = 0xA1` (OK) and 4-byte bootloader version at `feat[8..11]`. **Verified**: `b0 01 00 00` on a retail mouse. The code writes `20 18 b0 01 00 00` at `feat[6..11]` (the application PID `0x2018` first; `0x37D6`). |
| **`Erase`** | `0x08` | `0xB0` | `0x01` | `00..` | `00..` | **Changes no flash** on the mouse bootloader: it only drops the pending page buffer (`0x37EC`, section 3.3). An earlier reading, that it cleared `0x1E000`–`0x4D7FF`, came from reading back the second copy, which was blank there. |
| **`Program`** | `len + 5` | `0xB0` | `0x02` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `data[0..len-1] ^ 0x55` | The bootloader XORs each byte with `0x55` (on the USB wire only; the `.hex` files are plain) and writes it at **`addr + 0x47800`**, the second copy, through a 4 KB read-modify-write page buffer (section 3.3). Accepted for `0x6000 <= addr < 0x4D800`, else `0xA2`. **Verified**: a 32-byte write lands exactly and the rest of the page is kept (2026-11-13); writing `0x43800`–`0x43A7F` changed flash `0x8B000`–`0x8B27F` (2026-11-19). The running application changes only at `Exit`. |
| **`VerifyByAddress`** | `0x20` | `0xB0` | `0x83` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `00..` | Writes the pending page buffer first, then reads `len` bytes at **`addr + 0x47800`** (the second copy), XORs them with `0x55` and returns them at `feat[11..]`. Same range as `Program`: **Verified** `0xA2` at `0x0000`, `0x1000`, `0x5000`, `0x5FE0` and from `0x4D800` up (2026-11-13/18). So the bootloader, the running application and `0x95000` up cannot be read through it. `compx_usb_flasher.py flash` refuses images ending above `0x36800` (section 3.4). |
| **`Exit`** | `0x01` | `0xB0` | `0x04` | `feat[6] = 0x01` | `00..` | Writes the pending page, **copies the second copy over the application** (`0x4D800` → `0x6000`, as many pages as `Program` / `VerifyByAddress` touched, counted from the first), marks the application valid in the flag page and resets (`0x388E`–`0x38CE`). It checks nothing about the image. **Verified**: the application booted after every flash; the SET fails with `ETIMEDOUT` or `EPROTO` as the device resets. |

Status byte `feat[0]` (**Inferred**, `CPMXHIDDevice` result codes): `0xA1`/`0x02` success, `0xA2` fail, `0xA3` unknown command, `0xA4` out of range. These are bootloader replies. In application mode the Glorious receiver `258a:2031` answers `0xA0` while a relayed request is pending and `0xA4` when the mouse cannot be reached (`devices/glorious-series-one-pro/receiver.md`).

---

## 3. Inside the Series One Pro mouse bootloader (read from a unit, 2026-11-19)

### 3.1 How it was read

- `VerifyByAddress` refuses the bootloader (section 2), and the unit's debug port is locked: `UICR.APPROTECT` reads `0xFFFFFF00`, i.e. enabled (**Verified**, see below). So neither USB nor SWD can read it.
- The application can read its own flash, so a debug build of the open-directional-dpi patch added a read-only memory-read command (opcode `0x7E`, `stock_ddpi_patch.c` built with `DDPI_DEBUG_PEEK`, never released). Flashed with `compx_usb_flasher.py`, it read the whole 1 MB of flash, FICR and UICR over the cable. The normal build was flashed back afterwards.
- Checks that the read is real (**Verified**): a second read of `0x0000`–`0x003F` and `0x5FC0`–`0x5FFF` an hour later matched; the application part matched the build on the mouse byte for byte; the NVMC/ACL registers `0x4001E800`–`0x4001E87F` are all zero, so no flash region is read-protected.
- The bootloader image (`0x0`–`0x5FFF`, SHA-256 `6398f97686b289deaa252e251600b88b6c9ca1d647bfad7323ca9fbfa863f2cc` on this unit) is vendor code and is not published. Initial SP `0x20004650`, `Reset_Handler` `0x000003C1`; code up to `0x4FFF`; the page `0x5000` is its flag page. The addresses below are in this image (**Inferred** from disassembly unless marked).

### 3.2 Power-up: boot the application or stay

The decision is `0x24E8` (returns 1 = stay in the bootloader):

1. If `POWER.RESETREAS` has the soft-reset bit and `0x2003FFFC` holds `0xA55AA55A`: clear the word and stay. This is the application's "enter bootloader" path.
2. Turn the LED pins (`P0.13`, `P0.15`, `P0.17`, `P0.20`, `P0.22`, `P0.24`) into outputs, set `P0.09`, `P0.10` and `P1.00` as pulled-up inputs, wait about 640 ms (10 × 64 ms, `0x2564`–`0x2574`), and stay if all three read low: **Back + Forward + DPI held at power-up** is the bootloader's own recovery chord. **Verified** (2026-11-19): with the switch off and the cable unplugged, holding the three while plugging the cable in brought up `258a:2103`.
3. If the flag page `0x5000` starts with `0x13572468`: boot the application (it checks no vector table or checksum).
4. If the copy-pending word `0x5008` is `0x5AA55AA5`, an `Exit` copy was interrupted: copy again (3.3), mark the application valid and boot it.
5. Otherwise stay.

The power switch does not reset the MCU while the cable is plugged in: USB keeps it powered (**Verified**, 2026-11-19: switching off and on with the cable in gave no USB reconnect). For the chord, unplug the cable with the switch off first.

### 3.3 Two copies of the application

- `Program` and `VerifyByAddress` add `0x47800` to the address (`0x380C`, `0x387E`) after checking `0x6000 <= addr < 0x4D800` (`0x1924`). So they work on a **second copy at `0x4D800`–`0x94FFF`**, and the running application at `0x6000` is not touched while flashing.
- Writes go through a 4 KB RAM page buffer (`0x200000D0`): the first write to a page loads it from flash, a write to another page (or `VerifyByAddress`, or `Exit`) writes the buffered page back (`0x1DE0`: erase the page, program 1,024 words, up to 4 tries each, no read-back). `Erase` only drops the buffer.
- `Exit` (`0x388E`–`0x38CE`): write the buffer, set flag state 2 (clears the valid marker, sets copy-pending), copy `0x4D800` → `0x6000` (`0x1868`), set flag state 3 (valid, copy done), reset. The copy length is `(highest page − lowest page touched + 1) × 4 KB` from the start of both copies, at most `0x47800`.
- Flag page `0x5000` (`0x1898`): `+0` valid marker `0x13572468`, `+4` "second copy written" `0xA5A55A5A`, `+8` copy-pending `0x5AA55AA5`. **Verified** on the unit after a flash: `68 24 57 13 5a 5a a5 a5 00 00 00 00`.
- **Verified** on the unit (2026-11-19): `0x4D800`–`0x653D7` held exactly the image running at `0x6000`.

What follows from this:

- **An interrupted flash cannot break the application.** Until `Exit` it is untouched and still marked valid; an `Exit` cut off during the copy is finished at the next power-up (3.2, step 4). The flasher's old precautions (blank `0x6000` first, vector table last) protected against a risk this bootloader does not have.
- **A `dump` is a read of the second copy**, which equals the application after any completed flash. Reading every page and then sending `Exit` makes the bootloader copy the whole bank (`0x47800` bytes), rewriting the application with identical bytes. That copy also takes the settings pages along (next point): **Verified** on the unit, copies of `0x7E000`, `0x8B000` and `0x8C000` sat at `0x36800`, `0x43800` and `0x44800`. They are harmless there.

### 3.4 The second copy overlaps the settings pages

`0x4D800`–`0x94FFF` contains the `0x7E000` custom-firmware settings page, the stock profile page `0x8B000`, the macro directory `0x8C000` and the first macro pages `0x90000`–`0x94FFF`. An image byte for `0x6000 + n` is stored at `0x4D800 + n`, so:

| Image reaching | Overwrites |
| :--- | :--- |
| `0x36800` | `0x7E000`, the open-directional-dpi profile (or ZMK settings) |
| `0x43800` | `0x8B000`, the stock profiles |
| `0x44800` | `0x8C000`, the macro directory |
| `0x48800` | macro data from `0x90000` |

- `compx_usb_flasher.py` and `compx_hex_tool.py audit` therefore refuse images ending above **`0x36800`** (194 KB), and the ZMK board's code partition ends there. Every image flashed so far ended below `0x1E000`.
- The same overlap makes the stock settings page writable on purpose: writing `Program` at `0x43800` changes flash `0x8B000`. It was used once, on 2026-11-19, to restore a backed-up settings page after a test. 640 bytes were read back equal, and the mouse booted with the restored settings (**Verified**).

### 3.5 Other flash, FICR and UICR on the unit (Verified, read 2026-11-19)

- `0x88000`: blank. There is no pairing page; the radio link uses the chip's `FICR DEVICEID[0]` ([`radio-protocol.md`](radio-protocol.md) 3.1). `0x90000` up: blank (no macros stored).
- UICR: `APPROTECT` `0xFFFFFF00` (enabled: SWD cannot read the chip; unlocking it erases everything, bootloader included), `PSELRESET[0..1]` `0x12` (`P0.18` is the reset pin), `NFCPINS` `0xFFFFFFFE` (NFC pins used as GPIO), `REGOUT0` erased (default), `NRFFW` / `CUSTOMER` erased.
- FICR: `INFO.PART` `0x52840`, `INFO.VARIANT` `AAC0`, 1024 KB flash, 256 KB RAM. The `DEVICEID` is unit-specific and not given here.
