/*
 * PixArt PAW3370 / Glorious BAMF Optical Mouse Sensor — Zephyr Input Driver
 *
 * Implements the SPI Mode 3 register protocol, power-up calibration sequence,
 * CPI / LOD / angle-snapping configuration, and 12-byte Motion_Burst (0x16)
 * reader documented in devices/glorious-series-one-pro/hardware.md section 4.
 *
 * Untested on hardware: written from static analysis of CM2856_Mouse_USB v1.00.00
 * and the PAW3370 register model, and compile-verified on ZMK main (Zephyr 4.1).
 *
 * SPDX-License-Identifier: MIT
 */

#define DT_DRV_COMPAT pixart_paw3370

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/dt-bindings/input/input-event-codes.h>
#include <zephyr/input/input.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>

#ifdef CONFIG_INPUT_LOG_LEVEL
LOG_MODULE_REGISTER(paw3370, CONFIG_INPUT_LOG_LEVEL);
#else
LOG_MODULE_REGISTER(paw3370);
#endif

/* Bank-0 registers */
#define PAW3370_REG_PRODUCT_ID       0x00u
#define PAW3370_REG_MOTION           0x02u
#define PAW3370_REG_DELTA_X_L        0x03u
#define PAW3370_REG_DELTA_X_H        0x04u
#define PAW3370_REG_DELTA_Y_L        0x05u
#define PAW3370_REG_DELTA_Y_H        0x06u
#define PAW3370_REG_MOTION_BURST     0x16u
#define PAW3370_REG_CAL_STATUS       0x20u
#define PAW3370_REG_LOD_THRESH       0x2Bu
#define PAW3370_REG_POWER_UP_RESET   0x3Au
#define PAW3370_REG_PERFORMANCE      0x40u
#define PAW3370_REG_LOD_CONFIG       0x42u
#define PAW3370_REG_RES_STEP_MODE    0x4Du
#define PAW3370_REG_RESOLUTION_X     0x4Eu
#define PAW3370_REG_ANGLE_SNAP       0x56u
#define PAW3370_REG_GAIN_HIGH_DPI    0x5Au
#define PAW3370_REG_BANK_SELECT      0x7Fu

#define PAW3370_WRITE_BIT            0x80u
#define PAW3370_RESET_CMD            0x5Au
#define PAW3370_MOTION_BIT           0x80u
#define PAW3370_BURST_LEN            12u

/* SPI timings (hardware.md section 4) */
#define PAW3370_T_SRAD_US            20u
#define PAW3370_T_SRAD_MOTBR_US      5u
#define PAW3370_T_SWW_US             180u
#define PAW3370_T_SRW_US             20u

/* CPI bounds */
#define PAW3370_CPI_MIN              50u
#define PAW3370_CPI_STEP50_MAX       10000u
#define PAW3370_CPI_MAX              19000u
#define PAW3370_CPI_HIGH_GAIN_MIN    5000u

/* Max consecutive burst reads per work invocation before yielding */
#define PAW3370_MAX_BURSTS_PER_WORK  16u

struct paw3370_reg_val {
	uint8_t reg;
	uint8_t val;
};

/*
 * Standard PAW3370 banked register tuning table applied after OTP trim copy
 * (derived from CM2856_Mouse_USB_v1.00.00_20220810.hex @ 0x171B0..0x1746A).
 */
static const struct paw3370_reg_val paw3370_init_table[] = {
	/* Bank 0 defaults */
	{ 0x7F, 0x00 }, { 0x55, 0x00 }, { 0x4D, 0x50 }, { 0x4E, 0x3B },
	{ 0x4F, 0x46 }, { 0x54, 0x34 }, { 0x77, 0x24 },
	/* Bank 5 */
	{ 0x7F, 0x05 }, { 0x44, 0xA8 }, { 0x46, 0x15 }, { 0x4A, 0x14 },
	{ 0x51, 0x10 }, { 0x53, 0x0C }, { 0x55, 0xC9 }, { 0x5B, 0xEA },
	{ 0x61, 0x13 }, { 0x62, 0x0B }, { 0x64, 0x18 }, { 0x6D, 0x86 },
	{ 0x7D, 0x85 }, { 0x7E, 0x03 },
	/* Bank 6 */
	{ 0x7F, 0x06 }, { 0x60, 0xB0 }, { 0x61, 0x00 }, { 0x6D, 0x29 },
	{ 0x6E, 0x23 }, { 0x7E, 0x40 },
	/* Bank 7 */
	{ 0x7F, 0x07 }, { 0x42, 0x15 },
	/* Bank 8 */
	{ 0x7F, 0x08 }, { 0x42, 0x28 }, { 0x43, 0x32 },
	/* Bank 9 */
	{ 0x7F, 0x09 }, { 0x40, 0x03 },
	/* Bank 10 */
	{ 0x7F, 0x0A }, { 0x4A, 0x28 }, { 0x4C, 0x28 }, { 0x49, 0x00 },
	{ 0x4F, 0x02 },
	/* Bank 12 */
	{ 0x7F, 0x0C }, { 0x40, 0x90 }, { 0x41, 0x50 }, { 0x42, 0x0C },
	{ 0x43, 0xA8 }, { 0x44, 0x47 }, { 0x45, 0x01 }, { 0x4D, 0x4F },
	{ 0x4E, 0x1B }, { 0x54, 0x00 }, { 0x55, 0x60 }, { 0x56, 0x60 },
	{ 0x58, 0x30 }, { 0x59, 0x63 },
	/* Bank 13 */
	{ 0x7F, 0x0D }, { 0x4B, 0x23 }, { 0x4C, 0x40 }, { 0x4E, 0x6B },
	{ 0x5E, 0xC3 }, { 0x4F, 0x02 },
	/* Bank 16 */
	{ 0x7F, 0x10 }, { 0x45, 0x1E }, { 0x46, 0xF0 }, { 0x48, 0x0F },
	{ 0x49, 0x88 }, { 0x4C, 0x15 }, { 0x4F, 0x00 }, { 0x51, 0x6F },
	{ 0x52, 0x90 }, { 0x54, 0x64 }, { 0x55, 0xF0 }, { 0x5C, 0x40 },
	{ 0x61, 0xEE }, { 0x62, 0xE5 },
	/* Bank 20 */
	{ 0x7F, 0x14 }, { 0x53, 0x0C }, { 0x4A, 0x67 }, { 0x6D, 0x20 },
	{ 0x6E, 0x00 }, { 0x73, 0x83 }, { 0x74, 0x00 }, { 0x7A, 0x16 },
	{ 0x63, 0x14 }, { 0x62, 0x14 },
	/* Bank 0 + start internal oscillator calibration */
	{ 0x7F, 0x00 }, { 0x5B, 0x40 }, { 0x61, 0xAD }, { 0x51, 0xEA },
	{ 0x19, 0x9F },
};

struct paw3370_config {
	struct spi_dt_spec spi;
	struct gpio_dt_spec irq_gpio;
	uint16_t cpi;
	uint8_t lod_mm;
	bool angle_snap;
	bool swap_xy;
	bool invert_x;
	bool invert_y;
	uint16_t evt_type;
	uint16_t x_code;
	uint16_t y_code;
};

struct paw3370_data {
	const struct device *dev;
	struct gpio_callback irq_cb;
	struct k_work work;
	uint16_t cpi;
};

static int paw3370_reg_read(const struct device *dev, uint8_t reg, uint8_t *val)
{
	const struct paw3370_config *cfg = dev->config;
	uint8_t tx_byte = reg & 0x7Fu;
	uint8_t rx_byte = 0;

	const struct spi_buf tx_buf = { .buf = &tx_byte, .len = 1 };
	const struct spi_buf_set tx_set = { .buffers = &tx_buf, .count = 1 };
	const struct spi_buf rx_buf = { .buf = &rx_byte, .len = 1 };
	const struct spi_buf_set rx_set = { .buffers = &rx_buf, .count = 1 };

	int err = spi_write_dt(&cfg->spi, &tx_set);
	if (err == 0) {
		k_busy_wait(PAW3370_T_SRAD_US);
		err = spi_read_dt(&cfg->spi, &rx_set);
	}
	(void)spi_release_dt(&cfg->spi);
	k_busy_wait(PAW3370_T_SRW_US);

	if (err == 0) {
		*val = rx_byte;
	}
	return err;
}

static int paw3370_reg_write(const struct device *dev, uint8_t reg, uint8_t val)
{
	const struct paw3370_config *cfg = dev->config;
	uint8_t tx_bytes[2] = { (uint8_t)(reg | PAW3370_WRITE_BIT), val };

	const struct spi_buf tx_buf = { .buf = tx_bytes, .len = sizeof(tx_bytes) };
	const struct spi_buf_set tx_set = { .buffers = &tx_buf, .count = 1 };

	int err = spi_write_dt(&cfg->spi, &tx_set);
	(void)spi_release_dt(&cfg->spi);
	k_busy_wait(PAW3370_T_SWW_US);
	return err;
}

static int paw3370_burst_read(const struct device *dev, uint8_t buf[PAW3370_BURST_LEN])
{
	const struct paw3370_config *cfg = dev->config;
	uint8_t cmd = PAW3370_REG_MOTION_BURST;

	const struct spi_buf tx_buf = { .buf = &cmd, .len = 1 };
	const struct spi_buf_set tx_set = { .buffers = &tx_buf, .count = 1 };
	const struct spi_buf rx_buf = { .buf = buf, .len = PAW3370_BURST_LEN };
	const struct spi_buf_set rx_set = { .buffers = &rx_buf, .count = 1 };

	int err = spi_write_dt(&cfg->spi, &tx_set);
	if (err == 0) {
		k_busy_wait(PAW3370_T_SRAD_MOTBR_US);
		err = spi_read_dt(&cfg->spi, &rx_set);
	}
	(void)spi_release_dt(&cfg->spi);
	return err;
}

static int paw3370_set_cpi(const struct device *dev, uint16_t cpi)
{
	const struct paw3370_config *cfg = dev->config;
	struct paw3370_data *data = dev->data;
	uint16_t clamped = CLAMP(cpi, PAW3370_CPI_MIN, PAW3370_CPI_MAX);
	uint8_t step_reg;
	uint8_t step_val;
	int err;

	if (clamped > PAW3370_CPI_STEP50_MAX) {
		step_reg = 0xD0u; /* 100 CPI steps */
		step_val = (uint8_t)((clamped / 100u) - 1u);
	} else {
		step_reg = 0x50u; /* 50 CPI steps */
		step_val = (uint8_t)((clamped / 50u) - 1u);
	}

	err = paw3370_reg_write(dev, PAW3370_REG_RES_STEP_MODE, step_reg);
	if (err < 0) {
		return err;
	}
	err = paw3370_reg_write(dev, PAW3370_REG_RESOLUTION_X, step_val);
	if (err < 0) {
		return err;
	}
	err = paw3370_reg_write(dev, PAW3370_REG_ANGLE_SNAP, cfg->angle_snap ? 0x84u : 0x04u);
	if (err < 0) {
		return err;
	}
	err = paw3370_reg_write(dev, PAW3370_REG_GAIN_HIGH_DPI,
				(clamped >= PAW3370_CPI_HIGH_GAIN_MIN) ? 0x90u : 0x10u);
	if (err == 0) {
		data->cpi = clamped;
	}
	return err;
}

static int paw3370_set_lod(const struct device *dev, uint8_t lod_mm)
{
	uint8_t lod_cfg = (lod_mm == 2u) ? 0x0Du : 0x0Cu;
	uint8_t lod_thr = (lod_mm == 2u) ? 0x18u : 0x0Au;
	int err = paw3370_reg_write(dev, PAW3370_REG_LOD_CONFIG, lod_cfg);

	if (err < 0) {
		return err;
	}
	return paw3370_reg_write(dev, PAW3370_REG_LOD_THRESH, lod_thr);
}

static int paw3370_power_up_init(const struct device *dev)
{
	const struct paw3370_config *cfg = dev->config;
	uint8_t trim_a = 0, trim_b = 0, status = 0, dummy = 0, pid = 0;
	int err;

	/* Allow power rail + oscillator startup (t_PU = 55 ms) */
	k_msleep(55);

	/* Pulse CSN low -> high to synchronize the SPI port before soft reset */
	(void)paw3370_reg_read(dev, PAW3370_REG_PRODUCT_ID, &dummy);
	k_msleep(1);

	/* Soft reset */
	err = paw3370_reg_write(dev, PAW3370_REG_POWER_UP_RESET, PAW3370_RESET_CMD);
	if (err < 0) {
		return err;
	}
	k_msleep(10);

	/* Pre-calibration setup and OTP trim read (bank 0x0E -> bank 0x14) */
	static const struct paw3370_reg_val pre_otp[] = {
		{ 0x7F, 0x12 }, { 0x47, 0x00 },
		{ 0x7F, 0x00 }, { 0x18, 0x00 }, { 0x40, 0x80 }, { 0x55, 0x01 },
	};
	for (size_t i = 0; i < ARRAY_SIZE(pre_otp); i++) {
		err = paw3370_reg_write(dev, pre_otp[i].reg, pre_otp[i].val);
		if (err < 0) {
			return err;
		}
	}
	k_msleep(2);

	err = paw3370_reg_write(dev, PAW3370_REG_BANK_SELECT, 0x0Eu);
	if (err == 0) {
		err = paw3370_reg_write(dev, 0x43u, 0x1Du);
	}
	if (err == 0) {
		err = paw3370_reg_read(dev, 0x46u, &trim_a);
	}
	if (err == 0) {
		err = paw3370_reg_write(dev, 0x43u, 0x1Eu);
	}
	if (err == 0) {
		err = paw3370_reg_read(dev, 0x46u, &trim_b);
	}
	if (err == 0) {
		err = paw3370_reg_write(dev, PAW3370_REG_BANK_SELECT, 0x14u);
	}
	if (err == 0) {
		err = paw3370_reg_write(dev, 0x6Au, trim_a);
	}
	if (err == 0) {
		err = paw3370_reg_write(dev, 0x6Cu, trim_b);
	}
	if (err < 0) {
		return err;
	}

	/* Apply banked performance tuning table */
	for (size_t i = 0; i < ARRAY_SIZE(paw3370_init_table); i++) {
		err = paw3370_reg_write(dev, paw3370_init_table[i].reg, paw3370_init_table[i].val);
		if (err < 0) {
			return err;
		}
	}

	/* Wait for internal calibration (reg 0x20 == 0x0F, up to 100 ms) */
	for (int i = 0; i < 100; i++) {
		err = paw3370_reg_read(dev, PAW3370_REG_CAL_STATUS, &status);
		if (err < 0) {
			return err;
		}
		if (status == 0x0Fu) {
			break;
		}
		k_msleep(1);
	}
	if (status != 0x0Fu) {
		LOG_WRN("PAW3370 calibration status 0x%02x (expected 0x0f)", status);
	}

	static const struct paw3370_reg_val post_cal[] = {
		{ 0x19, 0x10 }, { 0x61, 0xD5 }, { 0x40, 0x00 }, { 0x7F, 0x00 },
	};
	for (size_t i = 0; i < ARRAY_SIZE(post_cal); i++) {
		err = paw3370_reg_write(dev, post_cal[i].reg, post_cal[i].val);
		if (err < 0) {
			return err;
		}
	}

	/* Clear initial motion / delta registers (0x02..0x06, stock 0x174BC..0x174DA) */
	(void)paw3370_reg_read(dev, PAW3370_REG_MOTION, &dummy);
	(void)paw3370_reg_read(dev, PAW3370_REG_DELTA_X_L, &dummy);
	(void)paw3370_reg_read(dev, PAW3370_REG_DELTA_X_H, &dummy);
	(void)paw3370_reg_read(dev, PAW3370_REG_DELTA_Y_L, &dummy);
	(void)paw3370_reg_read(dev, PAW3370_REG_DELTA_Y_H, &dummy);

	if (paw3370_reg_read(dev, PAW3370_REG_PRODUCT_ID, &pid) == 0) {
		LOG_INF("PAW3370/BAMF product ID: 0x%02x", pid);
	}

	err = paw3370_set_lod(dev, cfg->lod_mm);
	if (err < 0) {
		return err;
	}
	return paw3370_set_cpi(dev, cfg->cpi);
}

static void paw3370_work_handler(struct k_work *work)
{
	struct paw3370_data *data = CONTAINER_OF(work, struct paw3370_data, work);
	const struct device *dev = data->dev;
	const struct paw3370_config *cfg = dev->config;
	uint8_t burst[PAW3370_BURST_LEN];

	for (uint32_t i = 0; i < PAW3370_MAX_BURSTS_PER_WORK; i++) {
		int err = paw3370_burst_read(dev, burst);
		if (err < 0) {
			LOG_ERR("burst read failed: %d", err);
			break;
		}

		if (burst[0] & PAW3370_MOTION_BIT) {
			int16_t dx = (int16_t)sys_get_le16(&burst[2]);
			int16_t dy = (int16_t)sys_get_le16(&burst[4]);

			if (cfg->swap_xy) {
				int16_t tmp = dx;
				dx = dy;
				dy = tmp;
			}
			if (cfg->invert_x) {
				dx = (dx == INT16_MIN) ? INT16_MAX : (int16_t)(-dx);
			}
			if (cfg->invert_y) {
				dy = (dy == INT16_MIN) ? INT16_MAX : (int16_t)(-dy);
			}

			if (dx != 0 || dy != 0) {
				if (dx != 0) {
					input_report(dev, cfg->evt_type, cfg->x_code, dx,
						     dy == 0, K_FOREVER);
				}
				if (dy != 0) {
					input_report(dev, cfg->evt_type, cfg->y_code, dy,
						     true, K_FOREVER);
				}
			}
		}

		/* Stop looping once MOTION deasserts */
		if (gpio_pin_get_dt(&cfg->irq_gpio) <= 0) {
			break;
		}
	}

	/* If MOTION is still active after the batch cap, yield and continue */
	if (gpio_pin_get_dt(&cfg->irq_gpio) > 0) {
		k_work_submit(&data->work);
		return;
	}

	(void)gpio_pin_interrupt_configure_dt(&cfg->irq_gpio, GPIO_INT_EDGE_TO_ACTIVE);

	/* Guard against an edge arriving right before re-enabling the interrupt */
	if (gpio_pin_get_dt(&cfg->irq_gpio) > 0) {
		(void)gpio_pin_interrupt_configure_dt(&cfg->irq_gpio, GPIO_INT_DISABLE);
		k_work_submit(&data->work);
	}
}

static void paw3370_irq_handler(const struct device *port, struct gpio_callback *cb,
				gpio_port_pins_t pins)
{
	ARG_UNUSED(port);
	ARG_UNUSED(pins);
	struct paw3370_data *data = CONTAINER_OF(cb, struct paw3370_data, irq_cb);
	const struct paw3370_config *cfg = data->dev->config;

	(void)gpio_pin_interrupt_configure_dt(&cfg->irq_gpio, GPIO_INT_DISABLE);
	k_work_submit(&data->work);
}

static int paw3370_init(const struct device *dev)
{
	const struct paw3370_config *cfg = dev->config;
	struct paw3370_data *data = dev->data;
	int err;

	data->dev = dev;
	k_work_init(&data->work, paw3370_work_handler);

	if (!spi_is_ready_dt(&cfg->spi)) {
		LOG_ERR("SPI bus not ready");
		return -ENODEV;
	}
	if (!gpio_is_ready_dt(&cfg->irq_gpio)) {
		LOG_ERR("MOTION IRQ GPIO not ready");
		return -ENODEV;
	}

	err = gpio_pin_configure_dt(&cfg->irq_gpio, GPIO_INPUT);
	if (err < 0) {
		LOG_ERR("failed to configure MOTION GPIO: %d", err);
		return err;
	}

	err = paw3370_power_up_init(dev);
	if (err < 0) {
		LOG_ERR("PAW3370 power-up init failed: %d", err);
		return err;
	}

	gpio_init_callback(&data->irq_cb, paw3370_irq_handler, BIT(cfg->irq_gpio.pin));
	err = gpio_add_callback(cfg->irq_gpio.port, &data->irq_cb);
	if (err < 0) {
		LOG_ERR("failed to add MOTION GPIO callback: %d", err);
		return err;
	}

	err = gpio_pin_interrupt_configure_dt(&cfg->irq_gpio, GPIO_INT_EDGE_TO_ACTIVE);
	if (err < 0) {
		LOG_ERR("failed to enable MOTION GPIO IRQ: %d", err);
		return err;
	}

	/* Drain any motion already pending at boot */
	if (gpio_pin_get_dt(&cfg->irq_gpio) > 0) {
		(void)gpio_pin_interrupt_configure_dt(&cfg->irq_gpio, GPIO_INT_DISABLE);
		k_work_submit(&data->work);
	}

	return 0;
}

#define PAW3370_SPI_OP                                                                             \
	(SPI_WORD_SET(8) | SPI_TRANSFER_MSB | SPI_MODE_CPOL | SPI_MODE_CPHA |                      \
	 SPI_OP_MODE_MASTER | SPI_HOLD_ON_CS | SPI_LOCK_ON)

#define PAW3370_DEFINE(n)                                                                          \
	static const struct paw3370_config paw3370_cfg_##n = {                                     \
		.spi = SPI_DT_SPEC_INST_GET(n, PAW3370_SPI_OP, 0),                                 \
		.irq_gpio = GPIO_DT_SPEC_INST_GET(n, irq_gpios),                                   \
		.cpi = DT_INST_PROP(n, cpi),                                                       \
		.lod_mm = DT_INST_PROP(n, lod_mm),                                                 \
		.angle_snap = DT_INST_PROP(n, angle_snap),                                         \
		.swap_xy = DT_INST_PROP(n, swap_xy),                                               \
		.invert_x = DT_INST_PROP(n, invert_x),                                             \
		.invert_y = DT_INST_PROP(n, invert_y),                                             \
		.evt_type = DT_INST_PROP(n, evt_type),                                             \
		.x_code = DT_INST_PROP(n, x_input_code),                                           \
		.y_code = DT_INST_PROP(n, y_input_code),                                           \
	};                                                                                         \
	static struct paw3370_data paw3370_data_##n;                                               \
	DEVICE_DT_INST_DEFINE(n, paw3370_init, NULL, &paw3370_data_##n, &paw3370_cfg_##n,          \
			      POST_KERNEL, CONFIG_INPUT_INIT_PRIORITY, NULL);

DT_INST_FOREACH_STATUS_OKAY(PAW3370_DEFINE)
