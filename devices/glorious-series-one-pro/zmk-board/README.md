# Glorious Series One Pro — Draft ZMK Board Definition (Untested on Hardware)

> **Status: Untested on Physical Hardware (Derived from Disassembly)**
> These ZMK / Zephyr board files (`glorious_series_one_pro.dts`, `glorious_series_one_pro_defconfig`, `bootloader_reentry.c`) were constructed from the GPIO and SPI2 pin assignments extracted by disassembling the official `CM2856_Mouse_USB_v1.00.00_20220810.hex` firmware. They have **not** been flashed or tested as a full ZMK replacement on physical hardware.
>
> Note that replacing the stock Compx firmware with ZMK will lose compatibility with the stock `258a:2031` 2.4 GHz wireless receiver unless paired with a separate ZMK ESB receiver dongle.
