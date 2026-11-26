# Glorious Series One Pro — Draft ZMK Board (Untested on Hardware)

[ZMK](https://zmk.dev) is an open-source firmware for keyboards and mice, built on Zephyr. It is not part of the original Glorious firmware and shares no code with it: this folder is a draft of a full replacement for the stock image. The firmware that has run on this mouse is the stock image and [open-directional-dpi](https://github.com/0zLloen/open-directional-dpi), which patches the stock image and keeps everything else as it is.

> **Status:** builds on ZMK main (Zephyr 4.1, HWMv2); **untested on hardware** (never flashed to a mouse).
> Pins, peripherals, and the PAW3370 / BAMF register sequence are Inferred from disassembly of `CM2856_Mouse_USB_v1.00.00_20220810.hex`.
>
> **PAW3370 / BAMF driver:** an in-tree Zephyr `INPUT` driver (`paw3370.c` + `dts/bindings/pixart,paw3370.yaml`) implements the stock SPI Mode 3 init sequence, CPI/LOD/angle-snap configuration, and 12-byte `Motion_Burst` (`0x16`) reader wired into `zmk,input-listener`. Axis orientation (`0x5B = 0x40` in init, no software swap/invert by default) and timing match the stock firmware disassembly, but motion tracking has not yet been tested on a physical mouse.
>
> Replacing the stock firmware still loses the stock `258a:2031` 2.4 GHz receiver (ZMK uses USB HID here, or standard BLE if enabled; a custom `NRF_RADIO` driver would be needed for the stock receiver).

## Layout

`boards/glorious/glorious_series_one_pro/` is a HWMv2 board directory (use this folder as `BOARD_ROOT`):

- `board.yml`, `Kconfig.glorious_series_one_pro`, `Kconfig.defconfig`, `glorious_series_one_pro_defconfig`
- `glorious_series_one_pro.dts`: pins, SPI2, regulators (`CONFIG_REGULATOR`), EC11 wheel wired to `zmk,keymap-sensors`, `&uicr nfct-pins-as-gpios`, flash partitions (app `0x6000`–`0x367FF`: the bootloader stores an image at +`0x47800` before copying it into place, so a longer one would overwrite the settings at `0x7E000`; see `platforms/compx-nrf52/bootloader-protocol.md` section 3)
- USB identity `258a:2018`, product string `Series One PRO (ZMK)` (stock is `Series One PRO Wireless`), so hosts can tell it from stock firmware
- `glorious_series_one_pro.keymap`: five mouse buttons, wheel scrolling, Left + Right + DPI combo → `&compx_bl`
- `bootloader_reentry.c` (built by `CMakeLists.txt`): the `&compx_bl` behavior and the `PRE_KERNEL_1` boot chord, both writing `0xA55AA55A` to `0x2003FFFC` and resetting into the Compx bootloader (`258a:2103`). The boot chord only runs when the MCU comes out of reset; plugging in USB does not reset a running battery-powered mouse.
- `paw3370.c` and `dts/bindings/pixart,paw3370.yaml` (built by `CMakeLists.txt`): Zephyr `INPUT` driver for the PixArt PAW3370 / Glorious BAMF sensor on SPI2 (`P0.04`/`P0.08`/`P0.11`/`P0.12`, `MOTION` IRQ on `P0.06`), feeding `mouse_input_listener` (`zmk,input-listener`).

ZMK v0.3 (Zephyr 3.5, HWMv1) is not supported.

## Build and check

From a ZMK main west workspace (or the `zmkfirmware/zmk-build-arm:4.1` container):

```bash
west build -s app -b glorious_series_one_pro -- -DBOARD_ROOT=/path/to/zmk-board
readelf -sW build/zephyr/zmk.elf | grep -E 'glorious_enter_compx_bootloader|glorious_boot_chord_recovery_check|compx_bl_api|paw3370_init|paw3370_work_handler'
python3 /path/to/platforms/compx-nrf52/tools/compx_hex_tool.py audit build/zephyr/zmk.hex
```

All three recovery symbols (`glorious_enter_compx_bootloader`, `glorious_boot_chord_recovery_check`, `compx_bl_api`) and the sensor driver symbols (`paw3370_init`, `paw3370_work_handler`) must be present. Flash with `compx_usb_flasher.py flash --firmware-hex build/zephyr/zmk.hex` once the mouse is in the bootloader.
