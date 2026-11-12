# Glorious Series One Pro — Draft ZMK Board (Untested on Hardware)

> **Status:** builds on ZMK main (Zephyr 4.1, HWMv2) as of 2026-11-12; never flashed to a mouse.
> Pins and peripherals are Inferred from disassembly of `CM2856_Mouse_USB_v1.00.00_20220810.hex`.
>
> **Blocker:** no driver for the PAW3370 / BAMF sensor exists in Zephyr, ZMK, QMK or known community modules. The `pixart,paw3370` node and its input listener are disabled, so the image handles buttons, the wheel and bootloader re-entry but does not track motion. A driver has to be written from the stock register sequence (`hardware.md` section 4).
>
> Replacing the stock firmware also loses the stock `258a:2031` 2.4 GHz receiver unless a separate ZMK-compatible dongle is used.

## Layout

`boards/glorious/glorious_series_one_pro/` is a HWMv2 board directory (use this folder as `BOARD_ROOT`):

- `board.yml`, `Kconfig.glorious_series_one_pro`, `Kconfig.defconfig`, `glorious_series_one_pro_defconfig`
- `glorious_series_one_pro.dts`: pins, SPI2, regulators (`CONFIG_REGULATOR`), EC11 wheel wired to `zmk,keymap-sensors`, `&uicr nfct-pins-as-gpios`, flash partitions (app at `0x6000`, settings at `0x7E000`)
- `glorious_series_one_pro.keymap`: five mouse buttons, wheel scrolling, Left + Right + DPI combo → `&compx_bl`
- `bootloader_reentry.c` (built by `CMakeLists.txt`): the `&compx_bl` behavior and the `PRE_KERNEL_1` boot chord, both writing `0xA55AA55A` to `0x2003FFFC` and resetting into the Compx bootloader (`258a:2103`). The boot chord only runs when the MCU comes out of reset; plugging in USB does not reset a running battery-powered mouse.

ZMK v0.3 (Zephyr 3.5, HWMv1) is not supported.

## Build and check

From a ZMK main west workspace (or the `zmkfirmware/zmk-build-arm:4.1` container):

```bash
west build -s app -b glorious_series_one_pro -- -DBOARD_ROOT=/path/to/zmk-board
arm-zephyr-eabi-nm build/zephyr/zmk.elf | grep -E 'glorious_enter_compx_bootloader|glorious_boot_chord_recovery_check|compx_bl_api'
python3 /path/to/platforms/compx-nrf52/tools/compx_hex_tool.py audit build/zephyr/zmk.hex
```

All three symbols must be present before relying on the recovery paths. Flash with `compx_usb_flasher.py flash --firmware-hex build/zephyr/zmk.hex` once the mouse is in the bootloader.
