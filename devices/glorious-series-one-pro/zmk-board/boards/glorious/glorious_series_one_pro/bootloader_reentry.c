/*
 * Glorious Forge Series One Pro (CM2852-BAMF): routes back to the Compx USB bootloader.
 *
 * Writing 0xA55AA55A to 0x2003FFFC and resetting makes the resident 24 KB Compx
 * bootloader (0x0..0x5FFF) enumerate as USB HID 258a:2103 (firmware-updater.md 4.1,
 * from stock firmware disassembly; not yet confirmed on hardware with a ZMK image).
 *
 * Two routes into glorious_enter_compx_bootloader():
 *  1. &compx_bl behavior (default keymap: Left + Right + DPI combo) while ZMK runs.
 *  2. PRE_KERNEL_1 boot chord: Left + Right + DPI held while the MCU comes out of reset.
 *     Plugging in USB does not reset a running battery-powered mouse; the MCU has to
 *     power-cycle (battery disconnected) or be reset another way.
 *
 * The magic word needs no RAM reservation: it is written with interrupts locked
 * immediately before the reset, and the bootloader consumes it before Zephyr runs.
 *
 * SPDX-License-Identifier: MIT
 */

#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <cmsis_core.h>
#include <hal/nrf_gpio.h>

#define COMPX_BOOTLOADER_MAGIC_ADDR  ((volatile uint32_t *)0x2003FFFCu)
#define COMPX_BOOTLOADER_MAGIC_VALUE 0xA55AA55Au

/* Button GPIOs from stock firmware 0x0000C5B8 (hardware.md 3.2) */
#define PIN_BTN_LMB NRF_GPIO_PIN_MAP(1, 15)
#define PIN_BTN_RMB NRF_GPIO_PIN_MAP(1, 13)
#define PIN_BTN_DPI NRF_GPIO_PIN_MAP(1, 0)

FUNC_NORETURN void glorious_enter_compx_bootloader(void) {
    (void)irq_lock();
    *COMPX_BOOTLOADER_MAGIC_ADDR = COMPX_BOOTLOADER_MAGIC_VALUE;
    __DSB();
    NVIC_SystemReset();
}

static int glorious_boot_chord_recovery_check(void) {
    nrf_gpio_cfg_input(PIN_BTN_LMB, NRF_GPIO_PIN_PULLUP);
    nrf_gpio_cfg_input(PIN_BTN_RMB, NRF_GPIO_PIN_PULLUP);
    nrf_gpio_cfg_input(PIN_BTN_DPI, NRF_GPIO_PIN_PULLUP);

    /* Let the internal pull-ups settle. On nRF this is a CPU-cycle delay, usable pre-kernel. */
    k_busy_wait(20);

    /* Active-low switches: 0 means pressed */
    if (nrf_gpio_pin_read(PIN_BTN_LMB) == 0u && nrf_gpio_pin_read(PIN_BTN_RMB) == 0u &&
        nrf_gpio_pin_read(PIN_BTN_DPI) == 0u) {
        glorious_enter_compx_bootloader();
    }
    return 0;
}

SYS_INIT(glorious_boot_chord_recovery_check, PRE_KERNEL_1, 0);

#define DT_DRV_COMPAT glorious_behavior_compx_bootloader
#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)

#include <drivers/behavior.h>
#include <zmk/behavior.h>

static int compx_bl_pressed(struct zmk_behavior_binding *binding,
                            struct zmk_behavior_binding_event event) {
    glorious_enter_compx_bootloader();
}

static const struct behavior_driver_api compx_bl_api = {
    .binding_pressed = compx_bl_pressed,
    .locality = BEHAVIOR_LOCALITY_EVENT_SOURCE,
#if IS_ENABLED(CONFIG_ZMK_BEHAVIOR_METADATA)
    .get_parameter_metadata = zmk_behavior_get_empty_param_metadata,
#endif
};

BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,
                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT, &compx_bl_api);

#endif
