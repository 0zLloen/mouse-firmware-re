/*
 * Passive 2.4 GHz Sniffer for the Compx nRF52 Mouse <-> Receiver Link
 * Target: Nordic nRF52840 USB Dongle (PCA10059, Zephyr board nrf52840dongle/nrf52840)
 *
 * Configures NRF_RADIO with the exact PHY registers from platforms/compx-nrf52/radio-protocol.md
 * (MODE=Ble_2Mbit, 8-bit preamble, 8-bit LFLEN, MAXLEN=64, big-endian payload, whitening seed 0x40,
 * 16-bit CRC with SKIPADDR=0 so Nordic Erratum 245 matches the stock chips automatically).
 *
 * Zero-gap continuous RX:
 *   - SHORTS = READY_START | END_START | ADDRESS_RSSISTART
 *   - Double-buffered PACKETPTR swapped inside EVENTS_ADDRESS (after START latches the current buffer)
 *     so an ACK arriving ~70 us after an uplink packet is captured without software turnaround lag.
 *
 * 1 us timestamps + host USB SOF correlation via PPI:
 *   - TIMER2 free-running at 1 MHz (32-bit)
 *   - PPI CH10: RADIO->EVENTS_ADDRESS -> TIMER2->TASKS_CAPTURE[0]  (packet arrival time ts_us)
 *   - PPI CH11: USBD->EVENTS_SOF      -> TIMER2->TASKS_CAPTURE[1]  (latest USB SOF time sof_us)
 *   Plug the sniffer into the same USB host as the 258a:2031 receiver to measure exact packet
 *   position within the host's 1 ms USB frame: sof_offset = (ts_us - sof_us) % 1000.
 *
 * USB CDC-ACM line commands (ASCII, LF/CR terminated):
 *   id <8-hex-digits>     Set BASE1 (u32, e.g. "id 11223344", from receiver/mouse 0x8A)
 *   ch <0..80>            Park on fixed channel 2400 + ch MHz (connect channels: 3, 49, 78)
 *   row <0..3> [0..3]     Park on HOP_TABLE[row][idx]
 *   follow [ch]           Park on connect ch (default 3); on 3-byte connect answer jump to HOP[row][0]
 *                         and hop through the 4x4 table if quiet > 2.5 ms
 *   stop                  Disable radio RX
 *   status                Print current BASE1, channel, mode, rx/drop counters
 *
 * Output format (one ASCII line per received frame):
 *   PKT <ts_us> <sof_us> <ch> <rssi_dbm> <crc_ok> <len> <hex_payload>
 */

#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/irq.h>
#include <zephyr/kernel.h>

#include <nrfx.h>

#define MAX_PAYLOAD_LEN 64
#define RING_SIZE       64  /* must be a power of 2 */
#define RING_MASK       (RING_SIZE - 1)

/* Compx hop table: 4 rows x 4 channels (FREQUENCY = 2400 + n MHz), radio-protocol.md 2.3 */
static const uint8_t HOP_TABLE[16] = {
	0x33, 0x1B, 0x4C, 0x04,
	0x30, 0x18, 0x49, 0x08,
	0x34, 0x1A, 0x4D, 0x02,
	0x2F, 0x16, 0x48, 0x07,
};

enum sniffer_mode {
	MODE_STOPPED = 0,
	MODE_FIXED   = 1,
	MODE_FOLLOW  = 2,
};

struct pkt_entry {
	uint32_t ts_us;
	uint32_t sof_us;
	uint8_t  ch;
	uint8_t  rssi_neg;
	uint8_t  crc_ok;
	uint8_t  len;
	uint8_t  payload[MAX_PAYLOAD_LEN];
};

static struct pkt_entry ring[RING_SIZE];
static volatile uint32_t ring_head;
static volatile uint32_t ring_tail;
static volatile uint32_t rx_count;
static volatile uint32_t drop_count;

/* Double-buffered DMA buffers: [LENGTH][PAYLOAD 0..63] */
static uint8_t rx_dma[2][1 + MAX_PAYLOAD_LEN] __aligned(4);
static volatile uint8_t cur_dma_idx;

static volatile uint32_t g_base1 = 0x11223344u;
static volatile uint8_t  g_channel = 3;
static volatile uint8_t  g_connect_ch = 3;
static volatile enum sniffer_mode g_mode = MODE_STOPPED;
static volatile bool     g_follow_linked;
static volatile uint8_t  g_hop_row;
static volatile uint8_t  g_hop_idx;
static volatile uint32_t g_last_good_us;
static volatile uint32_t g_follow_missed;

/* CDC-ACM RX command buffer */
static char cmd_buf[96];
static volatile size_t cmd_len;
static volatile bool cmd_ready;

static const struct device *cdc_dev;

static void cdc_write_str(const char *s)
{
	while (*s) {
		uart_poll_out(cdc_dev, (unsigned char)*s++);
	}
}

static uint32_t timer2_now(void)
{
	NRF_TIMER2->TASKS_CAPTURE[2] = 1;
	return NRF_TIMER2->CC[2];
}

static void radio_stop(void)
{
	NRF_RADIO->INTENCLR = 0xFFFFFFFFu;
	NRF_RADIO->SHORTS = 0;
	NRF_RADIO->EVENTS_DISABLED = 0;
	NRF_RADIO->TASKS_DISABLE = 1;
	while (!NRF_RADIO->EVENTS_DISABLED) {
	}
}

static void radio_tune_and_start(uint8_t ch)
{
	radio_stop();
	g_channel = ch;
	cur_dma_idx = 0;

	NRF_RADIO->FREQUENCY = ch;
	NRF_RADIO->BASE1 = g_base1;
	NRF_RADIO->PACKETPTR = (uint32_t)rx_dma[0];

	NRF_RADIO->EVENTS_READY = 0;
	NRF_RADIO->EVENTS_ADDRESS = 0;
	NRF_RADIO->EVENTS_END = 0;
	NRF_RADIO->EVENTS_RSSIEND = 0;

	NRF_RADIO->SHORTS = RADIO_SHORTS_READY_START_Msk |
			    RADIO_SHORTS_END_START_Msk |
			    RADIO_SHORTS_ADDRESS_RSSISTART_Msk;
	NRF_RADIO->INTENSET = RADIO_INTENSET_ADDRESS_Msk | RADIO_INTENSET_END_Msk;
	NRF_RADIO->TASKS_RXEN = 1;
}

static void radio_init_phy(void)
{
	/* Ensure HFXO is running for accurate 2 Mbit/s RX and 1 MHz TIMER2 timestamps */
	if ((NRF_CLOCK->HFCLKSTAT & CLOCK_HFCLKSTAT_SRC_Msk) !=
	    (CLOCK_HFCLKSTAT_SRC_Xtal << CLOCK_HFCLKSTAT_SRC_Pos)) {
		NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
		NRF_CLOCK->TASKS_HFCLKSTART = 1;
		while (!NRF_CLOCK->EVENTS_HFCLKSTARTED) {
		}
	}

	/* Full RADIO power cycle resets DATAWHITEIV to 0x40, matching stock firmware */
	NRF_RADIO->POWER = 0;
	k_busy_wait(100);
	NRF_RADIO->POWER = 1;

	/* Exact Compx PHY settings (platforms/compx-nrf52/radio-protocol.md 2.1) */
	NRF_RADIO->MODE        = RADIO_MODE_MODE_Ble_2Mbit;  /* 4 */
	NRF_RADIO->MODECNF0   |= RADIO_MODECNF0_RU_Fast;     /* 1: 40 us ramp-up */
	NRF_RADIO->TXPOWER     = RADIO_TXPOWER_TXPOWER_Pos4dBm;
	NRF_RADIO->PREFIX0     = 0xB3B2B1B0u;
	NRF_RADIO->PREFIX1     = 0xB7B6B5B4u;
	NRF_RADIO->BASE0       = 0x01234567u;
	NRF_RADIO->BASE1       = g_base1;
	NRF_RADIO->TXADDRESS   = 1u;
	NRF_RADIO->RXADDRESSES = 0x02u;       /* logical address 1: BASE1 + 0xB1 */
	NRF_RADIO->PCNF0       = 0x00000008u; /* LFLEN=8, S0=0, S1=0, PLEN=8-bit */
	NRF_RADIO->PCNF1       = 0x03040040u; /* MAXLEN=64, STATLEN=0, BALEN=4, ENDIAN=Big, WHITEEN=1 */
	NRF_RADIO->CRCCNF      = 0x00000002u; /* 16-bit CRC, SKIPADDR=0 (matches Erratum 245) */
	NRF_RADIO->CRCINIT     = 0x0000FFFFu;
	NRF_RADIO->CRCPOLY     = 0x00011021u;
}

static void timer_and_ppi_init(void)
{
	/* Free-running 32-bit 1 MHz timer (16 MHz / 2^4 = 1 MHz) */
	NRF_TIMER2->TASKS_STOP = 1;
	NRF_TIMER2->TASKS_CLEAR = 1;
	NRF_TIMER2->MODE = TIMER_MODE_MODE_Timer;
	NRF_TIMER2->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
	NRF_TIMER2->PRESCALER = 4;
	NRF_TIMER2->TASKS_START = 1;

	/* PPI CH10: RADIO EVENTS_ADDRESS -> TIMER2 CAPTURE[0] */
	NRF_PPI->CH[10].EEP = (uint32_t)&NRF_RADIO->EVENTS_ADDRESS;
	NRF_PPI->CH[10].TEP = (uint32_t)&NRF_TIMER2->TASKS_CAPTURE[0];

	/* PPI CH11: USBD EVENTS_SOF -> TIMER2 CAPTURE[1] */
	NRF_PPI->CH[11].EEP = (uint32_t)&NRF_USBD->EVENTS_SOF;
	NRF_PPI->CH[11].TEP = (uint32_t)&NRF_TIMER2->TASKS_CAPTURE[1];

	NRF_PPI->CHENSET = (1u << 10) | (1u << 11);
}

static void radio_isr(const void *arg)
{
	ARG_UNUSED(arg);

	if (NRF_RADIO->EVENTS_ADDRESS) {
		NRF_RADIO->EVENTS_ADDRESS = 0;
		/* START already latched rx_dma[cur_dma_idx]; point next END->START at the other buffer. */
		NRF_RADIO->PACKETPTR = (uint32_t)rx_dma[cur_dma_idx ^ 1u];
	}

	if (NRF_RADIO->EVENTS_END) {
		NRF_RADIO->EVENTS_END = 0;

		uint8_t idx = cur_dma_idx;
		cur_dma_idx = idx ^ 1u;

		uint32_t ts = NRF_TIMER2->CC[0];
		uint32_t sof = NRF_TIMER2->CC[1];
		uint8_t crc_ok = (NRF_RADIO->CRCSTATUS & RADIO_CRCSTATUS_CRCSTATUS_Msk) ? 1u : 0u;
		uint8_t rssi = (uint8_t)(NRF_RADIO->RSSISAMPLE & 0x7Fu);
		uint8_t len = rx_dma[idx][0];
		if (len > MAX_PAYLOAD_LEN) {
			len = MAX_PAYLOAD_LEN;
		}

		rx_count++;
		uint32_t next_head = (ring_head + 1u) & RING_MASK;
		if (next_head != ring_tail) {
			struct pkt_entry *e = &ring[ring_head];
			e->ts_us = ts;
			e->sof_us = sof;
			e->ch = g_channel;
			e->rssi_neg = rssi;
			e->crc_ok = crc_ok;
			e->len = len;
			if (len > 0) {
				memcpy(e->payload, &rx_dma[idx][1], len);
			}
			ring_head = next_head;
		} else {
			drop_count++;
		}

		if (g_mode == MODE_FOLLOW && crc_ok) {
			g_last_good_us = ts;
			g_follow_missed = 0;
			/* Connect answer: 3 bytes [row, frame_lo, frame_hi] on a connect channel */
			if (!g_follow_linked && len == 3 && rx_dma[idx][1] < 4) {
				g_hop_row = rx_dma[idx][1];
				g_hop_idx = 0;
				g_follow_linked = true;
				/* Retune immediately to HOP[row][0] */
				radio_tune_and_start(HOP_TABLE[4u * g_hop_row]);
			}
		}
	}
}

static void cdc_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);
	while (uart_irq_update(dev) && uart_irq_rx_ready(dev)) {
		uint8_t c;
		while (uart_fifo_read(dev, &c, 1) == 1) {
			if (cmd_ready) {
				continue;
			}
			if (c == '\r' || c == '\n') {
				if (cmd_len > 0) {
					cmd_buf[cmd_len] = '\0';
					cmd_ready = true;
				}
			} else if (cmd_len + 1 < sizeof(cmd_buf)) {
				cmd_buf[cmd_len++] = (char)c;
			}
		}
	}
}

static void print_status(void)
{
	char line[128];
	const char *m = (g_mode == MODE_FIXED) ? "fixed" :
			(g_mode == MODE_FOLLOW) ? (g_follow_linked ? "follow-linked" : "follow-connect") :
			"stopped";
	snprintf(line, sizeof(line),
		 "STATUS mode=%s base1=0x%08lX ch=%u freq=%uMHz rx=%lu drop=%lu\n",
		 m, (unsigned long)g_base1, g_channel, 2400u + g_channel,
		 (unsigned long)rx_count, (unsigned long)drop_count);
	cdc_write_str(line);
}

static void handle_command(char *line)
{
	while (*line && isspace((unsigned char)*line)) {
		line++;
	}
	if (strncmp(line, "id ", 3) == 0) {
		unsigned long v = strtoul(line + 3, NULL, 16);
		g_base1 = (uint32_t)v;
		if (g_mode != MODE_STOPPED) {
			radio_tune_and_start(g_channel);
		}
		print_status();
	} else if (strncmp(line, "ch ", 3) == 0) {
		unsigned long ch = strtoul(line + 3, NULL, 0);
		if (ch <= 100) {
			g_mode = MODE_FIXED;
			g_follow_linked = false;
			radio_tune_and_start((uint8_t)ch);
			print_status();
		} else {
			cdc_write_str("ERR ch out of range (0..100)\n");
		}
	} else if (strncmp(line, "row ", 4) == 0) {
		char *p = line + 4;
		unsigned long r = strtoul(p, &p, 0);
		unsigned long idx = (*p) ? strtoul(p, NULL, 0) : 0;
		g_mode = MODE_FIXED;
		g_follow_linked = false;
		radio_tune_and_start(HOP_TABLE[4u * (r & 3u) + (idx & 3u)]);
		print_status();
	} else if (strncmp(line, "follow", 6) == 0) {
		unsigned long ch = (line[6] == ' ') ? strtoul(line + 7, NULL, 0) : g_connect_ch;
		if (ch > 100) {
			ch = 3;
		}
		g_connect_ch = (uint8_t)ch;
		g_mode = MODE_FOLLOW;
		g_follow_linked = false;
		g_last_good_us = timer2_now();
		g_follow_missed = 0;
		radio_tune_and_start(g_connect_ch);
		print_status();
	} else if (strcmp(line, "stop") == 0) {
		g_mode = MODE_STOPPED;
		g_follow_linked = false;
		radio_stop();
		print_status();
	} else if (strcmp(line, "status") == 0) {
		print_status();
	} else {
		cdc_write_str("ERR cmds: id <hex32> | ch <0..100> | row <0..3> [0..3] | follow [ch] | stop | status\n");
	}
}

static void emit_packet(const struct pkt_entry *e)
{
	char line[64 + 2 * MAX_PAYLOAD_LEN];
	int pos = snprintf(line, sizeof(line), "PKT %lu %lu %u -%u %u %u ",
			   (unsigned long)e->ts_us, (unsigned long)e->sof_us,
			   e->ch, e->rssi_neg, e->crc_ok, e->len);
	for (uint8_t i = 0; i < e->len && pos + 3 < (int)sizeof(line); i++) {
		static const char HEX[] = "0123456789abcdef";
		line[pos++] = HEX[e->payload[i] >> 4];
		line[pos++] = HEX[e->payload[i] & 0x0F];
	}
	line[pos++] = '\n';
	line[pos] = '\0';
	cdc_write_str(line);
}

int main(void)
{
	cdc_dev = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);
	if (!device_is_ready(cdc_dev)) {
		return 0;
	}

	uart_irq_callback_user_data_set(cdc_dev, cdc_isr, NULL);
	uart_irq_rx_enable(cdc_dev);

	timer_and_ppi_init();
	radio_init_phy();

	IRQ_CONNECT(RADIO_IRQn, 0, radio_isr, NULL, 0);
	irq_enable(RADIO_IRQn);

	/* Wait briefly for host terminal/script to open CDC-ACM (DTR optional) */
	k_msleep(500);
	cdc_write_str("READY compx-nrf52-sniffer\n");
	print_status();

	while (1) {
		if (cmd_ready) {
			handle_command(cmd_buf);
			cmd_len = 0;
			cmd_ready = false;
		}

		while (ring_tail != ring_head) {
			struct pkt_entry e = ring[ring_tail];
			ring_tail = (ring_tail + 1u) & RING_MASK;
			emit_packet(&e);
		}

		/* In follow mode, track Compx 4x4 channel hops after >2.5 ms silence */
		if (g_mode == MODE_FOLLOW && g_follow_linked) {
			uint32_t now = timer2_now();
			if ((uint32_t)(now - g_last_good_us) > 2500u) {
				g_last_good_us = now;
				g_hop_idx = (g_hop_idx + 1u) & 3u;
				if (g_hop_idx == 0) {
					g_hop_row = (g_hop_row + 1u) & 3u;
				}
				g_follow_missed++;
				if (g_follow_missed >= 128u) {
					/* Lost link (~256 ms): return to connect channel */
					g_follow_linked = false;
					g_follow_missed = 0;
					radio_tune_and_start(g_connect_ch);
				} else {
					radio_tune_and_start(HOP_TABLE[4u * g_hop_row + g_hop_idx]);
				}
			}
		}

		k_usleep(50);
	}
	return 0;
}
