# Compx / Sinowealth nRF52840 Resident USB HID Bootloader Protocol (`258a:2103` / `258a:2113`)

Many commercial wireless gaming mice built on the **Compx Technology + Nordic nRF52840** OEM platform (USB Vendor ID `0x258a`) include a **resident 24 KB USB HID In-System Programming (ISP) Bootloader** at flash address `0x00000000`–`0x00005FFF`.

---

## 1. Bootloader Entry via SRAM Magic Word (`0x2003FFFC = 0xA55AA55A`)

At reset, the resident bootloader inspects the top 4 bytes of the nRF52840's 256 KB SRAM (`0x2003FFFC`):
- If `*(volatile uint32_t *)0x2003FFFC == 0xA55AA55A`, the bootloader clears the magic word, initializes the nRF52840 USB controller as `258a:2103` (wired mouse bootloader) or `258a:2113` (wireless receiver bootloader), and awaits 64-byte HID Feature report commands on Interface 2 (`Usage Page 0xFFFF`, Report ID `0x00`).
- From a running stock application (`258a:2018`), sending Feature report `00 00 00 02 00 00 B0 ...` (`feat[3]=0x02, feat[4]=0x00, feat[5]=0x00, feat[6]=0xB0`) triggers the firmware to write `0xA55AA55A` to `0x2003FFFC` and call `NVIC_SystemReset()`.

---

## 2. Bootloader Feature Report Commands (`Report ID 0x00`, 64-Byte Payload)

When passing buffers to Linux `ioctl(HIDIOCSFEATURE(65))` or Windows `HidD_SetFeature`, byte `0` is the Report ID (`0x00`) and bytes `1..64` are the 64-byte payload (`feat[0..63]`):

| Command | `feat[3]` (Len) | `feat[4]` | `feat[5]` (Opcode) | `feat[6..10]` | `feat[11..63]` | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`GetBLFW`** | `0x06` | `0xB0` | `0x80` | `00..` | `00..` | Returns `feat[0] = 0xA1` (OK) and 4-byte bootloader version at `feat[8..11]` (e.g. `b0 01 00 00`). |
| **`Erase`** | `0x08` | `0xB0` | `0x01` | `00..` | `00..` | Erases the application flash partition (`>= 0x00006000`). Preserves `0x00000000`–`0x00005FFF`. |
| **`Program`** | `len + 5` | `0xB0` | `0x02` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `data[0..len-1] ^ 0x55` | Bootloader de-XORs each byte with `0x55` and writes to NVMC flash at `addr` (`>= 0x00006000`). |
| **`VerifyByAddress`** | `0x20` | `0xB0` | `0x83` | `feat[6] = len` (`1..32`)<br>`feat[7..10] = addr` (BE32) | `00..` | Reads `len` bytes from flash at `addr` (`>= 0x00006000`), XORs with `0x55`, and returns them at `feat[11..11+len-1]`. Returns status `0xA2` if `addr < 0x00006000`. |
| **`Exit`** | `0x01` | `0xB0` | `0x04` | `feat[6] = 0x01` | `00..` | Validates and boots the application vector table at `0x00006000`. |
