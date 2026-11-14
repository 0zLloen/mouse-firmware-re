# Compx nRF52 2.4 GHz link: mouse ↔ USB receiver

How the Glorious Series One Pro Wireless mouse and its receiver `258a:2031` talk over 2.4 GHz: radio settings, addressing and pairing, link layer, every packet and record, and what open firmware on the mouse would need to talk to the **unchanged** stock receiver.

**Sources.** Two official images, `CM2856_Mouse_USB_v1.00.00_20220810.hex` (mouse, nRF52840, app at `0x6000`) and `CM2856_Receiver_USB_v1.00.00_20220810.hex` (receiver, app at `0x7000`). Addresses below are load addresses in those files, marked *M* (mouse) or *R* (receiver). Nothing in this document was observed on hardware.

**Why it is under `platforms/`.** Both images contain the same radio library: the same init routine (*M* `0x15A1C`, *R* `0xCA40`), the same 13-state machine with both roles in each image, the same RF test mode, and the same placeholder-style constants (`0x01234567`, prefixes `B0`..`B7`, a fixed hop table). That looks like a generic Compx library. Only this one mouse/receiver pair has been examined, though. The record types and the USB mapping in section 5 belong to the Series One Pro.

**Labels** ([`CONTRIBUTING.md`](../../CONTRIBUTING.md#evidence-rules)):
- **Verified** — a literal constant read from the image.
- **Verified (emulation)** — observed when both unmodified images ran together in [`compx_coemu.py`](../../devices/glorious-series-one-pro/re-scripts/compx_coemu.py), reproducible with the command given in section 6. This is weaker than **Verified** in `CONTRIBUTING.md`: it is not a hardware observation, and it holds only as far as the model is right (section 6 lists what it leaves out).
- **Inferred** — from disassembly.
- **Unknown** — not established.

**Notation.** `C[x]` is the byte (or word) at offset `x` of the mouse's radio state block `0x2000361C` (*M* `0x15A20`); `R[x]` the same for the receiver's radio state block `0x20000050` (*R* `0xCA44`). Neither is a config struct.

**Contents:** [1. Summary](#1-summary-for-implementers) · [2. PHY](#2-phy) · [3. Addressing, pairing, connect](#3-addressing-pairing-and-connect) · [4. Link layer](#4-link-layer) · [5. Records](#5-packets-and-records) · [6. Validation](#6-validation) · [7. Open firmware](#7-what-open-mouse-firmware-needs) · [8. Hardware checks](#8-hardware-checks-for-later-not-done)

---

## 1. Summary for implementers

| Item | Value | Label |
| :--- | :--- | :--- |
| Modulation | nRF52 `MODE = 4` (Ble_2Mbit), 2 Mbit/s GFSK, 1-byte preamble | **Verified** (constants), **Inferred** (meaning) |
| Address | 5 bytes: `BASE1` (4 bytes) + prefix `0xB1`. `BASE1` = the mouse's `FICR DEVICEID[0]`. | **Verified** (constants), **Inferred** (meaning) |
| Frame | `LENGTH` (8 bits, max 64), payload, CRC-16 (CCITT, init `0xFFFF`, address included), whitening on, MSB-first fields | **Verified** (constants), **Inferred** (meaning and bit order, 2.2); the CRC bits on air **Unknown** (Nordic erratum 245) |
| Channels | connect on 2403 / 2449 / 2478 MHz; linked on a fixed 4 × 4 hop table, the same for every pair | **Verified** (tables), **Inferred** (same for every pair, 2.3) |
| Roles | the mouse transmits; the receiver only answers with ACKs, and downlink data rides on those ACKs | **Inferred**, **Verified (emulation)** |
| Timing | mouse slots of 500 µs; idle keep-alive every 1 ms; receiver frame = USB SOF (1 ms); ACK about 70 µs after the end of the mouse packet | **Inferred**, **Verified (emulation)** |
| Payload | `[seq][next expected seq][records]` (4.2), record header `more<<7 \| len<<3 \| type` | **Inferred**, **Verified (emulation)** |
| Pairing | none over the air in the shipped flow: the receiver stores the mouse's `DEVICEID[0]` as its RF ID; the only write path in its firmware is USB command `0x0A` | **Inferred** |
| ESB? | not Nordic ESB on air (other modulation mode, length field and header, whitening); ESB-like in structure | **Inferred** |

Code: [`compx_radio.py`](../../devices/glorious-series-one-pro/re-scripts/compx_radio.py) encodes and decodes everything below, from register values down to on-air bits. Its self-check re-encodes frames from the co-emulation and compares them with stored bits: a regression check of the encoder, not an independent capture.

---

## 2. PHY

### 2.1 Register values of the link

The radio init (*M* `0x15A1C`, *R* `0xCA40`) writes the same PHY values on both sides. They differ only in where `BASE1` comes from, in `INTENSET`, and in a PPI channel that only the receiver sets up. Every value written is a literal in the image (**Verified** as constants); the two "not written" rows are **Inferred** (below). What each register means follows the nRF52840 Product Specification (**Inferred**).

| Register | Value | Meaning |
| :--- | :--- | :--- |
| `POWER` | 0, then 1 after a 10 × 64000-cycle delay | radio reset |
| `TXPOWER` | `0x04` | +4 dBm |
| `MODE` | `0x04` | Ble_2Mbit |
| `MODECNF0` | `\|= 1` | fast ramp-up (40 µs) |
| `FREQUENCY` | hop table entry (2.3) | 2400 + n MHz |
| `PREFIX0` / `PREFIX1` | `0xB3B2B1B0` / `0xB7B6B5B4` | prefixes of logical addresses 0..7 |
| `BASE0` | `0x01234567` | logical address 0, unused |
| `BASE1` | mouse: `FICR DEVICEID[0]` (`0x10000060`, *M* `0x15A88`); receiver: RF ID, config `+2` (*R* `0xCAB0`) | logical addresses 1..7 |
| `TXADDRESS` / `RXADDRESSES` | `1` / `0x02` | send and receive on logical address 1 only: `BASE1` + `0xB1` |
| `PCNF0` | `0x00000008` | LFLEN 8 bits, S0LEN 0, S1LEN 0, PLEN 8-bit preamble, CRCINC 0. The Product Specification says Ble_2Mbit needs a 16-bit preamble; both images use 8 bits anyway. |
| `PCNF1` | `0x03040040` | MAXLEN 64, STATLEN 0, BALEN 4, ENDIAN big, WHITEEN 1 |
| `CRCCNF` | `0x02` | 16-bit CRC, address included (SKIPADDR 0) |
| `CRCINIT` / `CRCPOLY` | `0xFFFF` / `0x11021` | CRC-16/CCITT-FALSE |
| `DATAWHITEIV` | not written; reset value `0x40` | whitening seed as for BLE channel 0, on every frequency |
| `TIFS` | not written | no hardware turnaround is used |
| `SHORTS` | `0x03` | READY→START, END→DISABLE |
| `INTENSET` | mouse `0x12` (ADDRESS, DISABLED); receiver `0x10` (DISABLED) | |
| PPI (receiver) | CH0: RADIO `EVENTS_ADDRESS` → `TIMER1` `TASKS_CAPTURE[3]` (*R* `0xCB40`–`0xCB54`) | the receiver timestamps each packet (4.5) |

**Inferred**: no store to `DATAWHITEIV` (`+0x554`) or `TIFS` (`+0x544`) exists in either image. A search for the literal and for base-plus-offset stores found none. `SHORTS` is only ever 0, 1 or 3, so `DISABLED_TXEN` / `DISABLED_RXEN` are never set. Turnaround is done in software: the receiver triggers `TXEN` from `TIMER0` 30 µs after a capture taken late in its RX `DISABLED` handler, so the real gap is somewhat longer. The mouse triggers `RXEN` from its RADIO interrupt after its own TX.

**MCUs.** The mouse image checks the nRF52840 part code (`FICR 0x10000130 == 0x08`, *M* `0x690E`, `0x7E2C`). The receiver image checks for the nRF52820 (`== 0x10` with revision 0) at *R* `0xA9F6`, `0xAA9A` and `0xDDA4` to choose nRF52820 pin masks. So the receiver firmware was built for an nRF52820, which fits its 32 KB of RAM (`receiver.md` section 1). **Inferred**

### 2.2 On-air frame

```
| preamble 8 bit | address 5 bytes          | LENGTH | PAYLOAD (LENGTH bytes) | CRC 16 bit |
|  alternating   | BASE1 LSByte..MSByte, B1 |   n    |  [b1][b2][records...]  |            |
                                            |<----------- whitened, MSbit first -------->|
```

All **Inferred** from the register values and the nRF52 RADIO semantics below. None of it has been captured on air.
- **Address:** sent `BASE1` least-significant byte first, then the prefix, each byte LSbit first. `PCNF1.ENDIAN` does not apply to the address. For `BASE1 = 0x11223344` the bytes on air are `44 33 22 11 B1`.
- **Preamble:** `0x55` or `0xAA`, chosen so that its last bit differs from the first address bit.
- **LENGTH and PAYLOAD:** MSbit first (`ENDIAN = big`). The RAM buffer is `[LENGTH][PAYLOAD]`.
- **CRC:** documented as CRC-16/CCITT-FALSE over the on-air bit sequence of address + LENGTH + PAYLOAD (a non-reflected register, fed in transmission order), sent MSbit first.
  - **Erratum 245** (nRF52840 and nRF52820) says the CRC is wrong when whitening is on and the address is included. That is exactly this configuration.
  - The stock pair works, so both chips compute the same thing. What they compute on air is **Unknown**.
  - `compx_radio.py` implements the documented algorithm. A non-Nordic receiver or SDR has to establish the real CRC from a capture, or ignore it. Open firmware on an nRF52 with the same registers gets the same behaviour as the stock chips.
- **Whitening:** polynomial x⁷ + x⁴ + 1, seed `0x40`. It covers LENGTH, PAYLOAD and CRC.
- **Air time** at 2 Mbit/s: 4 µs per byte, (9 + n) × 4 µs in total. The idle keep-alive (n = 2) takes 44 µs, a mouse report (n = 11) 80 µs, and the longest frame (n = 64) 292 µs.

Because both images program the same registers, any nRF52 with the same values interoperates, whatever the exact bit order. The bit-level description matters only for a non-Nordic sniffer or SDR. Example frames as bits: `compx_radio.VECTORS`.

### 2.3 Channels

| Use | `FREQUENCY` | MHz | Where | Label |
| :--- | :--- | :--- | :--- | :--- |
| Connect (search and answer) | 3, 49, 78 | 2403, 2449, 2478 | *M* `0x1A950`, *R* `0xEA90` | **Verified** |
| Hop row 0 | `0x33 0x1B 0x4C 0x04` | 2451, 2427, 2476, 2404 | *M* `0x1A966`, *R* `0xEAA6` | **Verified** |
| Hop row 1 | `0x30 0x18 0x49 0x08` | 2448, 2424, 2473, 2408 | | **Verified** |
| Hop row 2 | `0x34 0x1A 0x4D 0x02` | 2452, 2426, 2477, 2402 | | **Verified** |
| Hop row 3 | `0x2F 0x16 0x48 0x07` | 2447, 2422, 2472, 2407 | | **Verified** |

- **Inferred:** the hop table is a fixed 16-byte copy on both sides (*M* `0x983C` from `0x1A966`, *R* `0x9534` from `0xEAA6`). The ID argument the callers pass is ignored: the mouse's own `DEVICEID[0]` (*M* `0xD530`, `0x15FCE`), the RF ID (*R* `0x97EA`) or the receiver's own `DEVICEID[0]` (*R* `0xCE70`). So every pair uses the same channels. Section 4.4 describes how the channel is chosen.
- **Inferred:** the mouse also accepts a downlink type-0 record that rewrites a row of its table (5.3). The stock receiver never sends one.

### 2.4 Not ESB

**Inferred**, from the nRF Connect SDK `esb.c` / `esb.h`. Like Nordic Enhanced ShockBurst, one side transmits and gets ACKs that may carry data. The air format is different, though:
- ESB always sends S1 bits (packet ID, NO_ACK); this link has no S1 field. Its sequence and acknowledgement numbers travel as payload bytes instead.
- ESB keeps whitening off; this link has it on.
- ESB uses a 16-bit preamble at 2 Mbit/s; this link uses 8 bits.
- ESB turns around with the `DISABLED_TXEN` / `DISABLED_RXEN` shorts; this link does it in software with timers (2.1).

The bit rate and the 8-bit length field alone would not rule ESB out: ESB has a Ble_2Mbit option and uses an 8-bit length for payloads over 32 bytes.

So an ESB library cannot talk to this receiver. Raw RADIO access with the registers above can.

### 2.5 Other radio configurations in the images

**Inferred**. The link never uses any of these.
- **RF test** (Nordic `radio_test` template): *M* `0x16740` and *R* `0xD190`.
  - `PCNF1 = 0x010400FE` / `0x01040040`: whitening off.
  - Address: logical address 0 (`TXADDRESS = 0`, `RXADDRESSES = 0x01`) = `BASE0` `bitswap(0x01234567)` + prefix `bitrev(0xB0)` (*M* `0x167C6`–`0x167E8`, *R* `0xD216`–`0xD238`). `BASE1 = bitswap(0x89ABCDEF)` is written but unused.
  - `MODE`, `TXPOWER` and `FREQUENCY` come from a parameter block. *M* `0x15E08` / *R* `0xCCD8` are its constant-carrier test.
  - The receiver enters it only through its local mode command `0x00` / `0xA0` (`receiver.md` section 4).
- **Bluetooth Direct Test Mode**, mouse only: string `dtm\dtm_main.c` at *M* `0xB11C` (**Verified**). Main loop *M* `0xB058`, command handler `0xAC8C`, radio setup `0x15BC0` / `0x15CF4`.
  - The radio setup builds a BLE-style link from the parameter block `0x20003834`: `BASE0` / `PREFIX0` from an access address, CRC from the block, `FREQUENCY` = 2 + 2k, `MODE` 3–6 with Coded-PHY fields.
  - Both test modes are chosen by the test-mode byte `0x20006695` (dispatcher *M* `0x18A84`: 0 → `0x168C8` radio_test, 1 → `0xB058` DTM).

---

## 3. Addressing, pairing and connect

### 3.1 Address and pairing

- The link address is `BASE1` + prefix `0xB1`.
  - The mouse uses its own **`FICR DEVICEID[0]`**, which is factory-programmed into the nRF52840 and not stored in flash (*M* `0x15A88`, `0x15FC8`, `0x6C5C`).
  - The receiver uses the **RF ID** stored at config `+2` (flash `0x38000`, RAM `0x20000148`).
  - A receiver is therefore paired to a mouse when its RF ID equals the mouse's `DEVICEID[0]`. (**Inferred**)
- The mouse keeps nothing about the receiver in flash in the shipped flow. Its own config struct (`0x200038A8`, page `0x8A000`, load *M* `0xD4A4`, save `0x1469E`, CRC-16 at `+0x1E`) has a receiver-ID field at `+0x1A`, but only the unreachable reverse flow (3.4) writes it. **Inferred**
- **Disagreement with the device docs.** `flash-layout-and-swd.md` 4.2, `firmware-updater.md` and `device.yaml` call `0x88000` the mouse's 2.4 GHz pairing page. The v1.00.00 application never references `0x88000`: there is no literal or immediate `0x88000` in the image (**Verified**), and no computed use was found (**Inferred**). Its flash page constants are `0x8A000` (the struct above), `0x8B000` (profiles), `0x8C000`, and an erase loop over `0x90000`–`0xDFFFF` (*M* `0x1475C`). The link does not depend on `0x88000`. What, if anything, the bootloader or the factory stores there is **Unknown**: the page has never been read.
- **No over-the-air pairing in the shipped flow.** (**Inferred**)
  - The receiver's pairing flag (`0x20000063`) is read at *R* `0x7826` and `0xCDFC`, and only ever written with 0.
  - Its learn path is therefore dead. It would store the 4 ID bytes of a connect request as the RF ID: *R* `0xCDFC`–`0xCE22`.
  - The RF ID changes only through receiver-local command **`0x0A`** (*R* `0xC7EE`), `body[6..9]` little-endian. It is saved to `0x38000` on the next main-loop pass. It is loaded into `BASE1` at the next radio init (receiver replug), or after the receiver's next connect answer (*R* `0xCE74`). Until then the old link keeps running.
- **Re-pairing with stock tools** (**Inferred**, never run):
  1. read the mouse's ID over the cable with mouse command **`0x8A`** (group 0, *M* `0x14986`, returns `DEVICEID[0]`);
  2. write it to the receiver with `0x0A`;
  3. replug the receiver.

  Who does this in the factory, or in Glorious Core, is **Unknown**.
- **Mouse "pairing mode"** (*M* `0x1349C`) is reachable only from a button with action type `0xF0` (no factory button has one). It sets `BASE1 = 0x89ABCDEF` for up to 30 s (*M* `0x6C46`–`0x6C84`: the `TIMER1` 1 ms tick counts `C[0x28]` to 30000, then restores `BASE1`). The entry is the button-action dispatcher (*M* `0xA4D8` compares the action with `0xF0`, then `bl 0x1349C` at `0xA504`). The stock receiver never listens on that address, so with it the mode only takes the mouse offline. **Inferred**
- Config page `0x38000` layout (*R* `0x97BC` load, `0xC6FC` save; **Inferred**):

  | Offset | Content |
  | :--- | :--- |
  | `+0` | u16 `0x0024` |
  | `+2` | RF ID, u32 LE |
  | `+6..+0x21` | not referenced |
  | `+0x22` | CRC-16/CCITT-FALSE over `+0..+0x21` |

  The load path checks neither the length nor the CRC. Treat the page as unit-specific: never publish it or erase it.

### 3.2 Connect (search and answer)

| Step | Who | Bytes (RAM packet `[LENGTH][payload]`) | Where | Label |
| :--- | :--- | :--- | :--- | :--- |
| Connect request | mouse, unlinked (states 4–6), `BASE1` = own ID | `05 20 id0 id1 id2 id3` (`DEVICEID[0]` LE) | *M* `0x15F66` | **Verified (emulation)** |
| Connect answer | receiver, unlinked (states 1–3), `BASE1` = RF ID | `03 row f_lo f_hi`: hop row 0..3 (random, different from the last), USB frame period in µs (u16, ≤ 1000) | *R* `0xCE26`–`0xCE56` | **Verified (emulation)**, e.g. `03 01 e8 03` |

The request goes out in the mouse's state 4 (*M* `0x15F66`) and the answer arrives in its state 6 (*M* `0x15FAE`). The receiver's RX side is its state 2 (*R* `0xCDF0`).

- **Mouse** (**Inferred**, timing **Verified (emulation)**):
  - It sends the request on the next of the 3 connect channels, then listens for 2 ms (`C[0x20] × 4`), so it retries every 2.1 ms.
  - Any CRC-good frame in that window counts as the answer. If `LENGTH == 3`, the mouse takes the hop row and stores the frame period in `C[0x2C]`, which only the dead reverse flow reads.
  - Then: `BASE1` = own ID, channel = row entry 0, `TIMER1` cleared, state 7 (linked idle).
- **Receiver** (**Inferred**, timing **Verified (emulation)**):
  - It listens 2 ms on each connect channel in turn.
  - It answers any CRC-good 5-byte frame on its own address, and does not look at the payload: the address is the only filter.
  - It sends the answer **6 times**, 500 µs apart, on successive connect channels (*R* `0xCE58`).
  - Only after that does it go to hop row channel 0 and listen (state 10).
- **Emulation timing:** the answer started 296 µs after the request began.
- **Frame period field:** a running average `(old + new) / 2` of the SOF period measured with `TIMER3`. It is set to 1000 when it is 0 or above 1000, and frozen after 1000 SOFs (*R* `0xD9F4`–`0xDA40`). It is also the receiver's own `TIMER1` frame period while suspended. **Inferred**
- **First frame after connecting:** a frame of length 0 (`00`), because the mouse transmits its cleared TX buffer once. **Verified (emulation)**
- **Mouse link-up record:** the first ACK sets the mouse's link flag and queues a type-5 record `06 01` (5.2), which the receiver forwards to the host. **Verified (emulation)**

### 3.3 Connect race after the answer

By the code, the two sides start the hop sequence about 2.6 ms apart (**Inferred**, *M* `0x15FAE`–`0x1600E`, *R* `0xCE58`–`0xCE94`):
1. The mouse takes the **first** answer it hears and goes to hop channel 0 at once.
2. The receiver keeps repeating the answer for 2.5 ms (6 answers, 500 µs apart), then goes to channel 0 100 µs later.
3. By then the mouse has missed 2 ACKs there and moved to channel 1.
4. The receiver's miss counter `R[0xB]` is 0 after every link loss (*R* `0xDA5C`), so it hops after its 2nd SOF on a channel. That is 1–2 ms after arriving, always after the mouse's last packet there.
5. Both then hop every 2 ms with the receiver one channel behind, until both give up after 256 ms and search again.

**Verified (emulation)**, `compx_coemu.py --ms 1500 --sof-phase P --drop-answers D --move M`, first SOF `P` = 20.0–20.9 ms in 100 µs steps (after the receiver's radio init). "Linked" means the first ACK, which sets the mouse's link flag:

| Case | Linked |
| :--- | :--- |
| no frame lost (`D` = 0), mouse still | 0 of 10 phases |
| no frame lost, mouse moving (`M` = 1000: one count per ms from boot) | 0 of 10 phases |
| the first answer of each connect lost on air (`D` = 1), still | 10 of 10: 8 on the first connect (first ACK at 86.4 ms), 2 on the second (20.3 ms: 386.9 ms; 20.4 ms: 592.7 ms) |
| `D` = 1, moving | 10 of 10, all on the first connect (85.0–86.5 ms) |

- **With answer 1 lost** the mouse takes answer 4. That is the next one on its channel, 1.5 ms later, and puts the two sides close enough to meet on channel 1. In the two phases that needed a second connect, the first one left a half-link: at 20.4 ms the receiver's link flag was up from 86.4 ms, but the mouse never got an ACK, gave up, and searched again.
- **An artifact found and removed.** An earlier version of the emulation started USB SOFs before the receiver's radio init. That left `R[0xB]` = 1 and let 1 phase in 10 link without a lost answer.
- **The contradiction.** The real pair does link; this is **Verified** on a retail unit in the device docs. So either the mouse misses answer 1 on hardware for a reason the model lacks (radio ramp-up, the receiver's interrupt latency, or RF loss), or the race is resolved by something not yet found in the code. **Unknown**, and a hardware check (section 8).
- **For open firmware** this does not matter if it stays on hop channel 0 until the first ACK (section 7).

### 3.4 States

| State | Mouse (*M* RADIO ISR `0x15EA8`, TIMER0 `0x1883C`) | Receiver (*R* RADIO ISR `0xCD94`, TIMER0 `0xDC28`) |
| :---: | :--- | :--- |
| 1–3 | reverse role, unreachable | listen for a connect request, answer |
| 4–6 | send a connect request, listen, take the answer | reverse role, unreachable |
| 7–9 | linked: TX in a slot, RX window for the ACK | reverse role, unreachable |
| 10–12 | reverse role, unreachable | linked: listen each frame, ACK |

**Inferred:** every call of the state helpers (*M* `0x189EC`, *R* `0xDE28`) passes a constant state, and the "unreachable" states are entered only from each other. The reverse role is a test link with the receiver as master.

---

## 4. Link layer

### 4.1 Frames and slots

- **Receiver** (**Inferred**; **Verified (emulation)**):
  - Its frame is the USB frame. Each SOF calls the frame start *R* `0xD9F4` (from the USB event handler `0xE77C`, or from its own `TIMER1` while suspended, `0x77FA`).
  - The frame start disables the radio. Then `RXEN` follows 30 µs later (`TIMER0`) and, after the 40 µs ramp-up, it listens until the next SOF (window `TIMER0` 1000 µs).
  - It never transmits on its own: only an ACK, triggered by `TIMER0` 30 µs after the RX `DISABLED` event (*R* `0xD05C`–`0xD064` → `0xDDA0`). After the ACK it listens again (state 10 → 11).
  - **Usable window:** a mouse packet is heard only if it starts at least about 70 µs after SOF. Its ACK must also end before the next SOF.
- **Mouse** (**Inferred**; **Verified (emulation)**):
  - `TIMER0` runs at 1 MHz with `COMPARE0_CLEAR` (*M* `0x18AD0`, initial `CC0` 1000). At link-up the mouse sets `CC0` to 500 and clears the timer (*M* `0x16000` → `0x189EC`), and each linked slot rewrites `CC0` to 500, or 450 after the timing bits (*M* `0x188E0`–`0x188FE`, 4.5): 500 µs slots. Each slot it may transmit once and then listen for the ACK until the next slot (*M* `0x16012`–`0x16028`).
  - **Idle:** an empty keep-alive every second slot, i.e. every 1 ms.
  - **Reply pending:** after each host command the mouse sends keep-alives in every slot (500 µs) until the last reply chunk is out (`C[0xF]` and `C[9]` set at *M* `0x15214` / `0x1521C`; `C[0xF]` cleared at `0x15DBE` after the last chunk, then `C[9]` at `0x1894A` or `0x160C2`). **Inferred**; seen in emulation
  - **Data queued:** it transmits in every slot.
- **ACK turnaround:** the ACK starts 70 µs after the end of the mouse packet (30 µs timer + 40 µs ramp-up, plus interrupt latency on hardware). **Verified (emulation)**: keep-alive at `t`, ACK at `t + 114 µs`; 11-byte data packet at `t`, ACK at `t + 150 µs`.
- **Dead slot:** in one emulated phase, data sent in the slot that starts 24 µs after SOF was lost every time, because the receiver was re-arming. It went out again 500 µs later. **Verified (emulation)**

### 4.2 Packet header

| Byte | Uplink (mouse → receiver) | ACK (receiver → mouse) |
| :---: | :--- | :--- |
| `b1` | mouse uplink sequence `C[7]` | next uplink sequence the receiver expects, `R[7]` |
| `b2` | next downlink sequence the mouse expects, `C[8]` (0..62) | bits 0–5: sequence of the downlink record attached, `R[8]` (0..62); bits 6–7: timing (4.5) |
| `b3..` | records (5) | records (5) |

The mouse builds its packet at *M* `0x18936`–`0x1897A`. The receiver checks it at *R* `0xCF74`–`0xCFE2` and builds the ACK at `0xD018`–`0xD05A`. **Inferred**. **Verified (emulation)** for uplink and downlink sequencing, uplink retransmission, keep-alives not advancing the sequence, and the duplicate at the wrap; the full-ring and full-queue cases in 4.3 are disassembly only.

### 4.3 Sequence numbers, retransmission, duplicates

**Uplink.**
- The receiver stores a packet only if it has records (`LENGTH > 2`), `b1 == R[7]`, and its ring has room (16 slots of 65 bytes, 15 usable: full when `(w + 1) & 15 == r`). Then `R[7]++`.
- Every CRC-good packet is ACKed, stored or not, with `b1 = R[7]`. So a duplicate is ACKed but not stored again.
- The mouse frees its queue head and does `C[7]++` only if ACK `b1 == C[7] + 1`. Otherwise it takes over `C[7] = b1` and resends the head in the next slot.
- Keep-alives carry `C[7]` but are never stored, so the sequence does not advance.
- The mouse's uplink queue is the same kind of ring: 15 entries, each one packet's worth of records.
- A full receiver ring leaves `R[7]` unchanged, so the mouse simply resends. The mouse stores downlink payloads in its RX ring without a full check.

**Downlink.**
- The receiver attaches the head of its downlink queue (15 usable entries) to every ACK, with `R[8]` in `b2`.
- The mouse stores the ACK payload only if `LENGTH ≥ 3` and `b2 & 0x3F == C[8]`. Then `C[8] = (C[8] + 1) % 63`, and its next uplink reports the new `C[8]`.
- The receiver pops its head when uplink `b2 & 0x3F == R[8] + 1`. Otherwise it sets `R[8] = b2 & 0x3F` and resends the head.

**Duplicate at the wrap.** **Inferred**, **Verified (emulation)** with 40 relayed queries in a row.
- With `R[8] = 62`, the mouse answers 0. Since `0 != 63`, the receiver neither pops nor counts on: it sets `R[8] = 0` and sends the same record again as sequence 0, and the mouse accepts it a second time.
- In `compx_coemu.py --queries 40`, chunk 0 of a request came at 730.9 ms with sequence 62 and again at 731.9 ms with sequence 0, and the mouse accepted both. Every one of the 40 queries still got the right answer.
- After the first wrap, one record in every 62 distinct downlink records arrives twice. What the duplicate does (**Inferred**, *M* `0x133E0`–`0x133FE`, `0x151FA`):
  - a duplicated chunk that is not the last one of its request: nothing;
  - a duplicated single-chunk request (`body[3] + 6 ≤ 8`): it runs twice;
  - a duplicated **last chunk of a multi-chunk request**: the mouse zeroes its staging area after each completed request, so the duplicate completes a request whose bytes 0..7 are zero. Unless the real one is still pending (then it is dropped), the mouse runs group 0, command `0x00` ("set mode" 0, which resets the MCU if the mode byte `0x20003002` is `0xA0`), and that reply replaces the real one in the receiver's host buffer. The original request runs only once.
  - With 2-chunk requests, as in the emulation, the duplicate is always a first chunk.

**Mouse-side cancel.** A downlink type-7 record of length 1 with a value ≥ `0x39` cancels a pending reply and resets `C[8]` to 0 (*M* `0x13334`; `hardware.md` 2.3). The stock receiver never sends one.

### 4.4 Hopping and link loss

Both sides use the same rule (mouse *M* `0x1889A`–`0x188DC`, receiver *R* `0xDA48`–`0xDAF8` and `0xDD0C`–`0xDD9C`; **Inferred**, **Verified (emulation)**):

1. Channel = `HOP[4 × row + idx]`. After a connect, `row` comes from the answer and `idx` = 0.
2. **Miss.** On the mouse, a slot that ended without an ACK, counted at most once per 1 ms (every second slot). So with data in every slot it takes 4 missed exchanges, and an ACK with a CRC error is not counted at all, because it returns the mouse to state 7. On the receiver, `R[0xB]` counts SOFs since the last CRC-good packet or the last hop (incremented at *R* `0xDA50`, cleared at `0xD068` and `0xDA5C`). It reaches 2 at the second SOF after the last good packet, so **a single 1 ms frame without a CRC-good packet already makes the receiver hop**. If SOFs stop, the `TIMER0` RX window running out 1000 µs after `RXEN` counts as a miss instead (*R* `0xDD0C`); while the bus is suspended, `TIMER1` runs the frame start (*R* `0x77FA`). The co-emulation always delivers SOFs, so it never takes those two paths.
3. **2 misses** → `idx + 1`. **4 channels** → `row = (row + 1) & 3`, `idx = 0`, round counter + 1.
4. **32 rounds** (about 256 ms) → link lost.
   - **Mouse:** clears its sequences and searches (3.2).
   - **Receiver:**
     - clears its link flag `0x20000056`, its sequences and the round counter, and turns a pending relayed request into `0xA4`;
     - on the frame-start path (*R* `0xDA82`–`0xDAE2`) it also empties both queues and sends a keyboard release plus `04 06 00 …` to the host (5.5); the RX-window path (`0xDD38`–`0xDD7E`) sends only `04 06 00 …` and leaves the queues;
     - goes back to listening on the connect channels.
5. Any CRC-good packet resets the miss and round counters.

The rows are walked in order, wrapping, so after a loss each side keeps cycling through all 16 channels until the round counter runs out.

**Outages** (**Verified (emulation)**, `compx_coemu.py --ms 1500 --outage 500,L`, all frames dropped from 500 ms). Both sides hop at about the same rate during an outage:
- after outages of 5, 30, 100 and 200 ms the link never dropped, and the first frame after the outage was ACKed, on whatever channel both had reached;
- outages of 300 and 400 ms dropped the link about 255 ms in. At the default phase the first connect after it then hit the race of 3.3, and the link came back 305 / 310 ms after the outage ended. At phases 20.0, 20.5 and 20.8 ms it came back about 5 ms after the outage ended.

**Pacing rule.** The receiver hops after a single frame without a CRC-good packet (rule 2). The mouse must therefore get a packet through in every 1 ms frame, as the stock mouse does. **Verified (emulation)** (`--ms 600 --outage 450.5,1`): one lost keep-alive made the receiver hop at once. The mouse's next keep-alive on the old channel got no ACK, and the pair met again on the next channel 2 ms later.

### 4.5 Phase control (ACK timing bits)

- **What the receiver measures.** It clears `TIMER1` at each SOF and captures it at the mouse packet's `ADDRESS` event (PPI, 2.1), so `t` = address time within the frame. On receive, `ADDRESS` comes a few µs after the last address bit (a decode delay; Nordic staff quote 8–9 µs at 1 Mbit/s). `t` includes that delay. The co-emulation does not model it. It then sets ACK `b2` bits 7–6 (*R* `0xCFE4`–`0xD014`; **Inferred**):

  | `t` (µs after SOF) | bits |
  | :--- | :--- |
  | ≥ 650 | `0xC0` |
  | 500 ≤ t < 600 | `0x80` |
  | 150 ≤ t < 500 | `0x40` |
  | < 150, or 600 ≤ t < 650 | `0x00` |

- **What the mouse does with them** (*M* `0x188E0`–`0x18900`; **Inferred**). On `0x40` or `0xC0` it makes its next slot 450 µs instead of 500, moving 50 µs earlier. `0x80` and `0x00` change nothing.
- **No later correction from the bits.** The timing bits only ever move the mouse earlier. The mouse only makes a slot 450 µs when the current one is 500 µs, so two short slots never follow each other.
- **Long exchanges.** The mouse's own RADIO `ADDRESS` interrupt (*M* `0x674A`–`0x6778`) captures `TIMER0`. In a slot of parity 1 (`C[0xD]` set), if an `ADDRESS` event (its own TX or the ACK) comes 300 µs or more into the slot, it adds 530 µs to that slot so the exchange can finish. This moves the grid later, but only for uplinks of roughly 35 bytes or more; keep-alives and mouse reports never trigger it. **Inferred** (*M* `0x6756` capture, `0x6766` compare with 300, `0x6772` adds `0x212`)
- **Stable positions.** Keep-alive address time in `[~94, 150)` µs or `[500, 650)` µs after SOF, with the other slot 500 µs away. **Verified (emulation)**: the default run settled with bits `0x80` (address in `[500, 600)`).
- **Drift.** The receiver's frame follows the host's USB SOF, so the clock that matters is the host's, not the receiver's. The mouse's 500 µs slot follows the mouse crystal. **Verified (emulation)**, `compx_coemu.py --ms T --drift-ppm P [--move 1000]`, which changes the SOF period by `P` ppm. A positive `P` makes the mouse fast relative to SOF. Default phase and `--drop-answers 1`, so the pair can reconnect after a drop:

  | Mouse clock vs host SOF | Mouse | Run | Link drops after the first link | Mouse link flag up after the first link |
  | :--- | :--- | :--- | :--- | :--- |
  | 50 ppm slow | still | 8 s | 0 | 100 % |
  | exact | still | 8 s | 0 | 100 % |
  | 20 ppm fast | still | 10 s | 3 (at 5.3, 7.6 and 9.8 s; each about 340 ms down) | 91 % |
  | 50 ppm fast | still | 8 s | 2 (at 2.3 and 7.2 s) | 93 % |
  | 200 ppm fast | still | 3 s | 4 (every 0.73 s; each about 340 ms down) | 60 % |
  | 50 ppm fast | moving | 8 s | 0 | 100 % |
  | 200 ppm fast | moving | 3 s | 0 | 100 % |

  The 340 ms are 256 ms of hopping plus a failed first connect (3.3). At phases where the first connect works the link comes back sooner.

- **Why the fast mouse loses the link** (**Inferred**, from the runs above):
  1. A fast mouse drifts earlier relative to SOF. After the bits have pulled it below 150 µs, they say nothing more, and it drifts into the 0–70 µs window where the receiver is not yet listening.
  2. Keep-alives use only one slot parity, so it then misses every ACK, hops, and loses the link 256 ms later.
  3. While moving, the other slot (500 µs later) still gets through and resets the miss counters.
- **Relation to the open question** in [`README.md`](../../devices/glorious-series-one-pro/README.md#open-questions): on hardware every relayed request failed "while the mouse was still or only scrolling" and went through while it moved. This mechanism is a candidate for part of that, and it predicts a dependence on the host (SOF accuracy) and the unit (crystal). It does not explain everything (**Inferred**):
  - **Scrolling.** A wheel change queues a type-1 record, which is resent until it gets through in the working slot and then resets the miss counters (4.4). So by these rules regular scrolling should keep the link up.
  - **Failure rate.** At 20–50 ppm the emulated still link was up most of the time, while on hardware every request failed.

  **Unknown** on hardware; the checks are in section 8.

---

## 5. Packets and records

### 5.1 Record format

- **Layout.** Payload bytes 3.. are records. Each record is a header byte `more << 7 | len << 3 | type` (len 0..15, type 0..7) followed by `len` data bytes.
- **One per type.** The mouse puts at most one record of each type into a packet (*M* `0x15E5C`). The receiver attaches one queue entry per ACK; an entry also holds at most one record per type (*R* `0xCD2C`).
- **Unknown types stop parsing.** On both sides an unknown type ends parsing, so later records in the same packet are ignored.

**Inferred**; **Verified (emulation)** for types 1, 5 and 7.

### 5.2 Uplink records (mouse → receiver)

| Type | Hdr | Data | Sent | Receiver → USB | Label |
| :---: | :---: | :--- | :--- | :--- | :--- |
| 1 mouse | `0x41` | `[buttons][wheel i8][X i16 LE][Y i16 LE][pan i8][00]` | while moving, at most once per report interval (factory 1 ms); at once on a button, wheel or pan change; never while still | interface 0, 8 bytes, no report ID, copied as is | **Verified (emulation)**: `00 00 04 00 ff ff 00 00` (move), `01 00 …` (left) |
| 2 keyboard | `0x42` | `[modifiers][00][key1..key6]` | on press and release of keyboard actions and macros | interface 1, report ID 3 | **Inferred** (producer *M* `0xBD0C`) |
| 3 consumer | `0x13` | `[usage lo][usage hi]` | press / release | interface 1, report ID 1 | **Inferred** |
| 4 system | `0x0C` | `[bits]`: 0 power down, 1 sleep, 2 wake | press / release | interface 1, report ID 2 | **Inferred** |
| 5 notification | `0x45` | 8 bytes: `01 stage+1 Xhi Xlo Yhi Ylo 0 0` DPI; `02 profile+1` profile; `04 profile+1 mask_hi mask_lo` type-`0x0B` buttons; `05 …` sensor calibration; `06 01` link up | on the event | interface 1, report ID 4 (usage page `0xFFA0`) | **Verified (emulation)** for `06 01`; others **Inferred** |
| 6 | `0x46` | 8 zero bytes | never (nothing sets its flag) | dropped | **Inferred** |
| 7 relay reply | `0xC7` / `0x47` | 8 raw reply bytes, **no offset byte**; `more` = 1 while more follow | one chunk per ACK, starting 2 ms after the command finished (*M* `0x15D74`) | appended to the reply buffer `0x200001EC`; on `more = 0`, `host_buf[3] + 6` bytes are copied to the feature buffer `0x2000016C` | **Verified (emulation)** |
| 7, len 1 | `0x0F` | `[v]` | never (the stock mouse always sends 8 bytes, *M* `0x15DA0`) | not appended: `v` becomes the reply write offset `R[0x10]`; `v ≥ 0x39` clears `R[0x10]`, `R[0xE]` (stops the relay pump) and `R[8]`. `more` is ignored, so the reply does not complete (*R* `0xC576`) | **Inferred** |

- **Type 1 fields.**
  - `buttons`: bit `n` = button code `n + 1` (Left, Right, Middle, Back, Forward).
  - `wheel` and `pan`: steps since the last record.
  - X and Y: a time-weighted share of the pending sensor counts, not the raw counts. At *M* `0x1319C`, on the 1 ms tick with the motion pin `P0.06` low, the mouse bursts the sensor and adds the counts to a carry (`0x20003038` / `0x2000303A`). It reports `sum × CC[0] / (CC[0] + Δ)`, with Δ = `TIMER1` `CC[3] − CC[2]` (*M* `0x13216`–`0x13258`; a sum of ±1 is not scaled), and the rest stays in the carry (`0x13282`–`0x13298`). **Inferred**
  - Source of the record: *M* `0x14CEC` from `0x200035E5`.
- **Motion that stays pending.** The carried remainder is reported only with the next movement. **Verified (emulation)** with the sensor stub: a lone movement was reported partly at once, and the rest only after the next movement.
- **Record lengths matter.** The receiver copies `len` bytes of each record with no bound (*R* `0xC4BA`, `0xC54C`). A type-1 record longer than 8 bytes overruns its 8-byte report buffer `0x2000000C` into radio state at `0x20000014`, and reply chunks are appended to the 64-byte reply buffer without a cap. **Inferred**
- **No battery record.** Battery and charging reach the host only as a relayed feature reply. **Inferred**
- **Uplink type 0** is never sent by the stock mouse.

### 5.3 Downlink records (receiver → mouse, on ACKs only)

| Type | Hdr | Data | Who queues it | Mouse action (*M* `0x13334`) | Label |
| :---: | :---: | :--- | :--- | :--- | :--- |
| 7 relay request | `0xCF` / `0x4F` | `[offset][8 request bytes]`; `more` = 1 while `body[3] + 6 > offset + 8` | pump *R* `0xCC78`, one chunk per frame | copied to staging `0x20004F68 + offset`. On `more = 0` the staging becomes the request (64 bytes, `0x20004F28`) unless one is pending, in which case it is dropped. | **Verified (emulation)** |
| 4 sleep | `0x0C` | `01` (the mouse ignores it) | main loop *R* `0x9570` → `0xCD2C`, once after USB suspend | sets the idle count `0x20003048` to the sleep limit − 1, so the mouse powers off 1–2 s later unless a report arrives first; no effect when sleep is off (`0xFFFF`) (`hardware.md` 2.3) | **Inferred** |
| 0 hop row | | `[row][channels…]` | never queued by the stock receiver | rewrites row `row` of the mouse's hop table (no bound on `row`) | **Inferred** |
| 7, len 1 | | `[v]` | never queued by the stock receiver | if `v ≥ 0x39`: cancel the pending reply and reset `C[8]` | **Inferred** |

**Inferred:** there is no downlink LED, DPI or config record, and the keyboard LED output report is ignored.

**Unhardened parser** (**Inferred**, *M* `0x133C0` passes `len − 1` to the copy at `0x6408`): a downlink type-7 record of length 0 makes the mouse copy `0xFFFFFFFF` bytes. The stock receiver always sends length 9. The type-0 row index is not bounded either.

### 5.4 A relayed feature request end to end

**Verified (emulation)**, version query, `body` = `00 00 02 06 00 81`:

1. **Host → receiver.** The host's SET_REPORT lands in `0x2000016C`. The HID event handler (*R* `0xA3F2`) sets the flag `0x2000000A` and `body[0] = 0xA0`, if the flag was clear. The main loop *R* `0xC9A4` relays the request because `body[2] = 0x02`; any other value makes it a receiver-local command (`receiver.md` section 2). If the link is up it copies the request to `0x200001AC` and starts the pump; if not it answers `0xA4` (`0xC9E8`).
2. **First chunk.** ACK `0c 0d 80 cf 00 a0 00 02 06 00 81 00 00` carries chunk offset 0, starting with the `0xA0` the handler wrote. The mouse acknowledges with `b2 = 1`.
3. **Second chunk.** ACK `0c 0d 81 4f 08 00 00 00 00 00 00 00 00` carries the last chunk, offset 8. The mouse acknowledges with `b2 = 2`.
4. **Mouse.** It handles the request (dispatcher `0x151AC`, `hardware.md` 2.3). Its reply then starts 2 ms later: `0b 0d 02 c7 a1 00 02 03 00 81 01 00`, then `0b 0e 02 47 00 00 20 18 00 00 00 00`.
5. **Receiver → host.** It copies `body[3] + 6` = 12 bytes: `a1 00 02 03 00 81 01 00 00 00 20 18`. GET_REPORT then returns it.

The whole exchange took 5.1–6.6 ms across the ten SOF phases 20.0–20.9 ms (5.4 ms at the default 20.3 ms). On hardware a relayed version query answers in about 8 ms (`receiver.md` section 3, **Verified** there).

### 5.5 Receiver → USB

**Inferred** (receiver disassembly); **Verified (emulation)** for interfaces 0 and 1 (link-up record).

- **Parsing.** The receiver parses one stored uplink packet per USB frame (main loop *R* `0xC470`, gated by the SOF flag `R[0xA]`). The sender `0xC838` hands the resulting reports to `app_usbd_hid_generic_in_report_set` (`0x8D00`) in the same main-loop pass. Interface 1 reports wait for a shared busy flag (`0x20000024`); interface 0 does not.
- **Latency.** In emulation, a record ACKed at `t` reached the HID class at the next SOF, 0.3 ms later in the emulated phase.
- **Interfaces:**

  | Interface | Endpoint | Descriptor | Contents |
  | :--- | :--- | :--- | :--- |
  | 0 | `0x81` | 91 bytes | boot mouse report, 8 bytes, no report ID |
  | 1 | `0x82` | 163 bytes | report IDs 1 consumer, 2 system, 3 keyboard, 4 vendor `0xFFA0`, 5 vendor `0xFFFF` |
  | 2 | `0x83` | 20 bytes | one 64-byte feature report, vendor page `0xFFFF` (the relay) |

- **Fixed report lengths.** Reports are 8 / 9 / 3 / 2 / 9 bytes whatever the record `len`.
- **Len-0 records.** A type-1 record of length 0 resends the last mouse report. Types 2..5 of length 0 send zeros.
- **On link loss** the receiver sends a keyboard release (frame-start path only) and an interface-1 report `04 06 00 …`.
- **Wake-up.** A type-1 record while the host has suspended the bus requests remote wake-up (`0xC876`).

---

## 6. Validation

**[`compx_coemu.py`](../../devices/glorious-series-one-pro/re-scripts/compx_coemu.py)** boots both official images from reset in Unicorn and runs them against each other. Their RADIO peripherals are connected through `compx_radio.py`:
- each transmitted frame is encoded to bits from the sender's registers;
- it is decoded with the receiver's registers: address match, de-whitening, CRC.

Setup: the receiver's config page holds the made-up mouse ID `0x11223344` (the paired state), and the first USB SOF comes at 20.3 ms, after the receiver's radio init.

The default run (`compx_coemu.py <mouse.hex> <receiver.hex>`) asserts the following, prints the times, and ends with `ok`:

1. **Connect.** The mouse sends `05 20 44 33 22 11` on a connect channel and the receiver answers `03 row e8 03`. The first answer of each connect is lost on air (`--drop-answers 1`, see 3.3). The first ACK follows on a hop channel of that row; in the default run the link came up on the second connect, at 386.9 ms.
2. **Keep-alive.** A 2-byte packet `02 C[7] C[8]` every 1000 µs once settled (950 µs while the timing bits adjust the slot), ACKed with `02 R[7] R[8]|timing`. The run asserts the spacing, not the bytes; the bytes are `02 00 00` / `02 00 80` before the link-up record and `02 01 00` / `02 01 80` after it.
3. **Cursor move.** 10 ms of motion: type-1 records, reaching USB interface 0 as `00 00 0X 00 ff ff 00 00` reports.
4. **Left click.** `P1.15` low: a record with buttons = `01`, then the release.
5. **Version query.** Relayed through both images: reply `a1 00 02 03 00 81 01 00 00 00 20 18` (5.4 ms at the default phase).

All ten phases 20.0–20.9 ms (`--sof-phase`) pass.

Which command produced which result:

| Result | Command (`compx_coemu.py <mouse.hex> <receiver.hex>` plus) |
| :--- | :--- |
| 3.3 connect table | `--ms 1500 --sof-phase P --drop-answers D --move M` |
| 4.3 wrap duplicate | `--queries 40 --trace` |
| 4.4 outages and one lost frame | `--ms 1500 --outage 500,L [--sof-phase P]`; `--ms 600 --outage 450.5,1 --trace` |
| 4.5 drift table | `--ms T --drift-ppm P [--move 1000]` |
| 5.4 reply times | default run with `--sof-phase P` |
| `compx_radio.VECTORS` | `--vectors` |

`--trace` prints every frame, with frames lost on air marked `LOST`.

**What the model leaves out**, which is why these results are not hardware facts:
- **CPU timing:** 64 instructions per µs; interrupt handlers take no time; the firmware's delay loops return at once.
- **Radio:** no noise, collisions or RSSI.
- **USB:** the receiver's USB device controller is replaced at the HID-class boundary, with a host that completes each IN report after 1 ms.
- **Sensor and ADC:** small stubs (PAW3370 register file and burst; a SAADC constant). The motion split of 5.2 depends on the stub.
- **No jitter:** both chips' clocks are exact apart from `--drift-ppm`, so a failing timing repeats on every retry.
- **No receive delay:** the RX `ADDRESS` event comes exactly 24 µs after the frame starts (2.2, 4.5).

---

## 7. What open mouse firmware needs

To impersonate the stock mouse on an nRF52840 against the unchanged receiver. **Inferred** throughout; nothing here has been tried on hardware.

1. **Identity.**
   - Use the chip's own `FICR DEVICEID[0]` as `BASE1`. A receiver already paired with this mouse then accepts the open firmware with no re-pairing, because the ID belongs to the chip, not to the firmware.
   - A different chip would need the receiver's RF ID rewritten (3.1).
2. **Radio.** Own the RADIO directly (no BLE stack or ESB library on it) and program the values of 2.1, including `MODE = 4`, 8-bit length, whitening and seed `0x40`. Fast ramp-up is needed for the turnaround below.
3. **Connect.**
   - Send `05 20` + ID on 2403 → 2449 → 2478 MHz in that order, listening about 2 ms after each.
   - Make the period per channel differ from 2000 µs; stock uses 2.1 ms. The receiver scans the same three channels in the same order, 2000 µs each (3.2). With an equal period the relative phase never changes, and the two sides meet in only about a third of starting phases. **Inferred**
   - On an answer `03 row …`, switch to `HOP[4 × row]`.
   - **Improvement over stock:** stay on that channel until the first ACK, for at least about 4 ms, instead of hopping after 2 misses. That avoids the race of 3.3 (the receiver arrives about 2.6 ms after its first answer).
   - **Find the SOF phase.** Until the first ACK the mouse cannot know where the receiver's frame starts, and a 1 ms keep-alive can sit in the unusable part of every frame (4.1). Send a keep-alive in every 500 µs slot until the first ACK: one of the two always lands in the usable window.
4. **Timing.**
   - Transmit at most once per 500 µs slot, and get a CRC-good packet through in **every** 1 ms frame: one empty frame already makes the receiver hop (4.4).
   - Be in RX within about 70 µs after your packet ends. `SHORTS` END→DISABLE plus DISABLED→RXEN with fast ramp-up gives about 44 µs.
   - Keep the packet start more than about 70 µs after SOF and finish packet + 70 µs + ACK before the next SOF.
   - Obey the timing bits: shorten the next slot by 50 µs on `0x40` or `0xC0`.
   - **Improvement over stock:** run the slot timer slow by more than the worst SOF error plus crystal error, so that drift always goes in the direction the bits can correct. USB full speed allows ±500 ppm on the frame and the crystal adds about ±40 ppm, so for example 500.5 µs (8008 ticks of a 16 MHz timer, +1000 ppm): at most about 1.5 µs of drift per 1 ms, far below the 50 µs per correction. Or send keep-alives in both slots. Either avoids the link loss of 4.5.
5. **Sequences.** Implement 4.3 exactly. Uplink: advance only on ACK `b1 == seq + 1`, otherwise adopt `b1` and resend. Downlink: accept iff `b2 & 0x3F == expected`, count modulo 63, report `expected` in `b2`. Expect one duplicated downlink record per 62 after the first wrap (4.3), and ignore a repeated last chunk instead of running a zeroed request.
6. **Records.**
   - Type 1 for motion and buttons, with 16-bit X/Y as in 5.2. Other types as needed.
   - Type 7 for the relay: reassemble `[offset][8]` downlink chunks; reply with 8-byte chunks without an offset byte, `reply[3] + 6` bytes in all, at most 64. Never send a 1-byte reply chunk: the receiver takes it as an offset or cancel record (5.2). The receiver copies only as many bytes as the **request** asked for.
   - Keep every record within its stock length (type 1 ≤ 8, keyboard ≤ 8, consumer ≤ 2, system ≤ 1, notification ≤ 8): the receiver does not check (5.2).
   - Type 4 (sleep) may be ignored or honoured.
7. **Loss and resync.** After 32 hop rounds without ACKs, search again (3.2). Following the receiver's channel rule of 4.4 keeps both sides in step after short outages.

**Still Unknown:**
- the exact on-air bits: bit order and, because of erratum 245, the CRC (it only matters for non-nRF tools);
- real ramp-up, interrupt latency and SOF jitter;
- how tolerant the receiver is of a packet that starts within its first 70 µs;
- whether the receiver behaves differently on a suspended bus beyond the sleep record and the `TIMER1` frame;
- what the factory tool writes with `0x0A`;
- the BLE path in the mouse (`0x20003003 == 2`), which was not examined.

---

## 8. Hardware checks for later (not done)

None of these has been run. Each needs the owner's go-ahead.

1. **Pairing model** (read-only): read the mouse's `0x8A` over the cable and the receiver's local `0x8A`. Expect the same 4 bytes.
2. **Sniff with an nRF52840 dongle** set up from 2.1:
   - `BASE1` = that ID, prefix `0xB1`, RX on the hop channels of 2.3, or on 2403/2449/2478 while the mouse searches.
   - Expect keep-alives `02 xx yy` every 1 ms and ACKs about 70 µs after each.
   - Expect the connect exchange of 3.2, and the timing bits of 4.5.
3. **Still-mouse link loss (4.5):**
   - log the receiver's link flag (`0x89`) once a second while the mouse is still, on two hosts;
   - with the sniffer, watch the keep-alive's position against SOF (timing bits `00` creeping earlier).
   - Prediction: drops only while still, and only where the mouse's clock is fast relative to that host's SOF.
4. **Downlink wrap (4.3):** send 40 read-only relayed version queries in a row and look for a wrong reply. With 2-chunk requests none is expected; a request whose duplicated chunk is its last one would return a bogus group-0 reply.
5. **Connect race (3.3):**
   - switch the mouse off and on and time until `0x89` reads `01`;
   - with the sniffer on the 3 connect channels, see which answer (1st, 4th, …) the mouse follows, and when the first ACK comes.
6. **Receiver MCU:** a PCB photo (`receiver.md` section 1). The image points to an nRF52820 (2.1).
7. **Bits on air:** an SDR capture of a keep-alive, compared with `compx_radio.air_bits()`. It settles the bit order and the CRC under erratum 245 (2.2). Only a non-Nordic tool needs this.
