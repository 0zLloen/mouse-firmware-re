# Compx nRF52 2.4 GHz Passive Sniffer (nRF52840 USB Dongle)

Passive 2.4 GHz sniffer firmware and host decoder for the Compx nRF52 mouse ↔ receiver link documented in [`../radio-protocol.md`](../radio-protocol.md).

## Required Hardware

- A **Nordic nRF52840 USB Dongle (`PCA10059`, ~$10)** (or an `nRF52840-DK`).
  - Why nRF52840? Both stock chips trigger **Nordic Erratum 245** (16-bit CRC with whitening on and address included, [`radio-protocol.md`](../radio-protocol.md#22-on-air-frame) §2.2). Using another nRF52840 with the exact same `NRF_RADIO` registers matches the CRC and whitening automatically.
  - Standard 802.11 Wi-Fi adapters (e.g. Ralink/Realtek/MediaTek USB sticks) and HCI Bluetooth adapters **cannot** be used: their basebands cannot lock onto raw 2 Mbit/s GFSK packets with custom address/whitening registers.

## How It Works

- **`src/main.c`**:
  - Programs `NRF_RADIO` with the exact Compx PHY registers from [`radio-protocol.md`](../radio-protocol.md#21-register-values-of-the-link) §2.1 (`MODE=4` `Ble_2Mbit`, fast ramp-up, 8-bit preamble, 8-bit `LFLEN`, `MAXLEN=64`, big-endian payload, whitening seed `0x40`, 16-bit CRC with `SKIPADDR=0`, logical address 1 = `BASE1 + 0xB1`).
  - Uses zero-gap continuous RX (`SHORTS = READY_START | END_START | ADDRESS_RSSISTART`) with double-buffered `PACKETPTR` swapped in `EVENTS_ADDRESS`, so an ACK ~70 µs after a mouse packet is captured without software turnaround lag.
  - Runs `TIMER2` free at 1 MHz and connects two PPI channels:
    - `PPI CH10`: `RADIO.EVENTS_ADDRESS → TIMER2.TASKS_CAPTURE[0]` (`ts_us`)
    - `PPI CH11`: `USBD.EVENTS_SOF → TIMER2.TASKS_CAPTURE[1]` (`sof_us`)
  - Plugging the sniffer dongle into the **same USB host** as the `258a:2031` receiver gives exact packet arrival offset within the host's 1 ms USB frame: `(ts_us - sof_us) % 1000`.
- **`compx_sniffer.py`** (standard library only on Linux):
  - Configures the dongle over `/dev/ttyACM0`, optionally pulling your mouse's `BASE1` directly from the attached `258a:2031` receiver (`0x8A`) or a private FICR dump (redacted from terminal output by default).
  - Decodes connect requests/answers, keep-alives, mouse/notification/relay records, ACK turnaround gaps, timing bits (`00/40/80/C0`), `< 70 µs` SOF blind-window intrusions, and sequence `62 → 0` wrap duplicates.

## Build & Flash (Zephyr / nRF Connect SDK)

From a Zephyr workspace (or `zmkfirmware/zmk-build-arm:4.1` container):

```bash
west build -b nrf52840dongle/nrf52840 platforms/compx-nrf52/sniffer -- -DCONF_FILE=prj.conf
# Press the sideways reset button on the PCA10059 (red LED pulses = Nordic USB DFU bootloader)
nrfutil pkg generate --hw-version 52 --sd-req=0x00 --application build/zephyr/zephyr.hex --application-version 1 sniffer.zip
nrfutil dfu usb-serial -pkg sniffer.zip -p /dev/ttyACM0
```

## Capture Recipes ([`radio-protocol.md` §8](../radio-protocol.md#8-hardware-checks))

```bash
# Offline decoder self-test (no hardware required)
python3 platforms/compx-nrf52/sniffer/compx_sniffer.py --selftest

# 1. Connect race (§3.3): camp on connect channel 3 (2403 MHz; also try 49 or 78) and power-cycle the mouse
python3 platforms/compx-nrf52/sniffer/compx_sniffer.py --port /dev/ttyACM0 --id-from-receiver --mode connect --ch 3

# 2. Still-mouse link drops (§4.5): plug sniffer into the same PC as 258a:2031, follow link, watch sof+<70us drift
python3 platforms/compx-nrf52/sniffer/compx_sniffer.py --port /dev/ttyACM0 --id-from-receiver --mode follow --seconds 60
```

> **Privacy note**: raw capture files (`--raw-out`) contain your unit's 4-byte RF ID (`FICR DEVICEID[0]`) in connect requests. Never commit raw captures.
