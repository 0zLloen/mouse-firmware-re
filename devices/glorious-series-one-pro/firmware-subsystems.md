# Glorious Series One Pro — Stock Firmware Subsystems: Battery, LEDs, Button Actions and Macros

Three parts of the stock mouse application that [`hardware.md`](hardware.md) only located: battery and power, the LEDs, and the button-action and macro system.

- Addresses are load addresses in `CM2856_Mouse_USB_v1.00.00_20220810.hex` (SHA-256 in [`device.yaml`](device.yaml)).
- Everything is **Inferred** from disassembly and Unicorn emulation (2026-11-19) unless marked. **Verified** here means read literally from the image (a constant, a table value, a string) unless hardware is named. The checks made on a retail mouse are in section 0.
- "Emulated" means the stock function was run in Unicorn, with RAM initialised by the image's own scatter loader. The runs are [`re-scripts/battery_emu.py`](re-scripts/README.md), `led_emu.py` and `macro_emu.py`; each takes your own copy of the official `.hex`.
- The work was split by subsystem and each part was then re-run and spot-checked against the disassembly. Where the parts met (the battery display flag, the charging LED) they agree.

Corrections this made to older pages are listed in section 4.

---

## 0. Checked on a retail mouse (2026-11-19)

Through a debug build of the patch that reads RAM while the mouse runs (the same build that read the bootloader, [`bootloader-protocol.md`](../../platforms/compx-nrf52/bootloader-protocol.md) 3.1), on the cable, with the owner pressing buttons and watching the LEDs. All **Verified**:

| Finding | What was seen |
| :--- | :--- |
| Charging indication (2.3) | Battery block `0x20003074`: percent 100, charging flag 1, average 797 counts. LED 0 framebuffer `00 FF 00` (channel 1 only), and the owner saw a **green** LED. So channel 1 is green. |
| Sensor burst bit 3 (2.3) | `0x20003023` was 1 while the mouse was held up off the desk and 0 within 50 ms of putting it down; sliding it on the desk left it 0. So bit 3 means **lifted**. While lifted, LED 1 was driven with the current stage's colour (`FF 26 26` for stage index 2 on this unit). |
| DPI button (3.2) | A press moved the stage index 2 → 3 (live DPI 3200). Holding it moved it once more (→ 0, 400 DPI), and 3.09 s into the hold the battery display flag `0x20003250` turned 1 (3.1). The display itself is not drawn while charging, as 2.3 says. |
| Chord bug (2.6) | One press of Middle + Forward + DPI (mask `0x34`) on profile 1, then the deferred save: the stored profile's effect `01 → 02`, speed `06 → 13`, DPI X `0x0640 → 0x0C02` (1600 → 3074), DPI Y `0x0640 → 0x0013` (1600 → 19), and zone-2 bytes `P+0x45` / `P+0x47` changed, exactly as emulated. After a real restart (cable unplugged with the switch off) the DPI came up from the stage table (3200), with the DPI fields rebuilt; the effect change stayed. The settings page was then restored from a backup through the bootloader. |
| Power switch | With the cable plugged in, switching off and on does not restart the MCU (no USB reconnect): USB keeps it powered. |

Not settled: the colour of channel 0 (the owner did not catch the lift colour) and of channel 2.

---

## 1. Battery, charging and power

### 1.1 Code map (`Source\battery.c`, string at `0xE1A4`)

| Function | Address | Called from |
| :--- | :--- | :--- |
| Battery init (SAADC setup, 16 samples, first percent, start timer) | `0xE088`–`0xE186` | main `0xE8A8` |
| Blocking single SAADC conversion, returns counts | `0xC870`–`0xC894` | `0xE0E0`, `0xE20A` only |
| Battery tick (app-timer handler) | `0xE1BC`–`0xE318` | pointer at `0xE19C` only |
| Battery stop (`TASKS_STOP`, `ENABLE = 0`, timer stop) | `0x7244`–`0x726C` | sleep preparation `0xABC6` only |

### 1.2 SAADC setup (`0xE088`–`0xE0C2`)

Register values are **Verified** as constants; their meaning is from the nRF52840 register map.

| Register | Value | Meaning |
| :--- | :--- | :--- |
| `CH[0].PSELP` `0x40007510` | `8` (`0xE09C`) | `AIN7` = **`P0.31`** |
| `CH[0].PSELN` `0x40007514` | `0` | not connected (single-ended) |
| `CH[0].CONFIG` `0x40007518` | `0x00021200` (literal at `0xE194`) | gain 1/4, reference VDD/4, `TACQ` 10 us, no pull resistors, no burst |
| `RESOLUTION` `0x400075F0` | `1` | 10 bit |
| `RESULT.PTR` / `MAXCNT` `0x4000762C` / `0x40007630` | `0x2000307E` / `1` | one sample per conversion |
| `OVERSAMPLE`, `SAMPLERATE`, calibration task | never written | no oversampling, task-triggered, no offset calibration |

- Gain 1/4 with reference VDD/4 makes full scale equal to VDD: `counts = 1024 * V(P0.31) / VDD`. **The firmware never converts to millivolts**; it compares averaged counts with tables.
- `0xC870` per conversion: `ENABLE = 1`, clear `EVENTS_END`, `TASKS_START`, `TASKS_SAMPLE`, busy-wait for `EVENTS_END`, `TASKS_STOP`, `ENABLE = 0`. The SAADC is off between samples.
- No GPIO is written in `0xC870` or `0xE1BC`, so the measurement path switches no divider. `P0.26` is driven high once at init (`0xC632`); whether it feeds the divider is **Unknown** (needs a meter on the PCB).
- Sample rate: a repeated app timer started with `0x199A` = 6,554 ticks (`0xE17C`). The 1 s timer uses `0x8000` ticks (`0x78C6`), so the battery tick is **200 ms**.
- Filter: 16-entry sliding window of `u16` at `0x20004D20`; average = sum >> 4, stored at `0x20003080`. The window is about 3.2 s. At boot it is filled with 16 back-to-back conversions (`0xE0E0`–`0xE0F6`). Emulated: one 860-count outlier among 700s moves the average to 710.
- The VDD level and the divider ratio on `P0.31` are **Unknown** (no `UICR.REGOUT0` literal in the image; `POWER.DCDCEN = 1` is written at `0x6F94`), so the tables below cannot be turned into volts without a meter.

### 1.3 Battery state block `0x20003074`

| Address | Meaning | Readers outside battery.c |
| :--- | :--- | :--- |
| `0x20003074` | percent from the table, this tick | none |
| `0x20003075` | reported percent scaled to 0..255 | none found |
| `0x20003076` | **reported percent** | battery query `0x148F8`, LED code `0xB356` / `0xB3BC` |
| `0x20003077` | **charging flag** (debounced) | battery query `0x148F2`, LED code `0xB3B8` |
| `0x20003078` / `0x20003079` | raw charge-pin state / debounce count | none |
| `0x2000307A` | low-battery blink phase 0..7 | LED code `0xB35E` |
| `0x2000307B` | "at or below the low threshold" flag (`0xE2E8`) | none found |
| `0x2000307C` | **battery-empty flag** (`0xE30E`) | `0xAB64`, `0xABF0`, `0x14D02` |
| `0x2000307E` / `0x20003080` | last ADC result / 16-sample average | none |

### 1.4 Counts to percent (`0xE246`–`0xE2C0`; boot copy `0xE112`–`0xE176`)

- Two lookup tables in initialised RAM, each a coarse table of 11 `u16` (0, 10, .. 100 %) followed by a fine table of 101 `u16` (0..100 %). Percent = the index `i` with `fine[i] <= average < fine[i+1]`; below `coarse[0]` gives 0, at or above `coarse[10]` gives 100.
- In flash they sit in the compressed Keil init data (the first discharge values are readable at `0x1C952`). The values below come from running the scatter loader; `battery_emu.py` prints all 101 fine values of each and checks the lookup against them.

| Table | RAM address | Coarse values (0, 10, .. 100 %) |
| :--- | :--- | :--- |
| Discharging (charging flag 0) | coarse `0x2000308C`, fine `0x200030A2` | 584, 603, 622, 641, 660, 679, 698, 717, 736, 755, 774 |
| Charging (flag 1) | coarse `0x2000316C`, fine `0x20003182` | 606, 623, 641, 659, 677, 694, 712, 730, 748, 766, 783 |

- Both are straight lines (about 1.9 and 1.77 counts per percent), not a Li-Po discharge curve.
- **Not charging:** the reported percent only falls (`0xE2DC`: a higher table value is ignored). Emulated: 61 % stayed 61 % while the average rose to a 92 % reading.
- **Charging:** the reported percent follows the charge table in both directions (`0xE2CE`). There is no clamp below 100 while charging; 100 needs an average of 783 counts or more.
- On every debounced change of the charging flag the reported percent is preset to 100 (`0xE206`) and recomputed in the same tick, so after unplugging it restarts from the discharge-table value (emulated: 700 counts gave 53 % charging and 61 % after unplug).

### 1.5 Charging detection

- **`P1.06`**, input with pull-up (`0xC614`), is the only charge input. It is read only at `0xE1C4` and `0xE1DC`. **Low = charging.**
- Debounce `0xE1D4`–`0xE206`: a change restarts a counter; after 10 further equal ticks the flag `0x20003077` flips. Emulated: set on tick 11, about 2.2 s.
- There is no second pin for "full" and no pin for "USB present". The hardware reading of 2026-11-13 (cable in, flag 0, 100 %) fits a charger status output that is low only while charging. The charger IC is **Unknown**.
- VBUS is not used by the battery code, only by the USB stack: `app_usbd` power events are enabled in the USB init `0x6F68`, and the event handler `0x19ACC` has "USB power detected" (`0x19B42`), "USB power removed" (`0x19B5C`) and "USB ready" (`0x19B90`).

### 1.6 `P1.04`

- Configured as input with pull-up at `0xC60A` and **never read**: it is not among the callers of the pin-read helpers (`0x11018`, `0x10FE0`, `0x11038`, `0x10FF8`), not in the sleep wake list (`0xABF6`–`0xAC68`) and not in GPIOTE. So in this build it selects nothing. What it is wired to is **Unknown** (trace it on the PCB). If the mouse has a power switch, it does not go through this firmware.

### 1.7 USB or 2.4 GHz

Mode byte `0x20003003`: `1` = radio, `0` = USB. Flag `0x200035DE` = USB configured.

- Boot always starts in radio mode: `0xE880` stores 1, then radio init `0x15A1C` and `TIMER0` start `0x18AD0`.
- `SET_CONFIGURATION(1)` sets `0x200035DE` (`0x17DE6`–`0x17DF2`). The main loop (`0x9A20`, called at `0xE8E0`) then stops the radio (`0x15B70`) and `TIMER0` (`0x18B24`) and stores mode 0 (`0x9A66`). The USB SOF event also stops a running radio (`0x19B02`–`0x19B0E`).
- This is why the receiver reports no link while the mouse is on the cable (**Verified** on hardware, 2026-11-18, [`receiver.md`](receiver.md) section 3).
- "USB power removed" (`0x19B5C`–`0x19B8C`) clears `0x200035DE`, stops USB, stores mode 1 and restarts the radio and `TIMER0`.
- A charger with no host gives VBUS but no `SET_CONFIGURATION`, so the mouse should stay on the radio (not tested).

### 1.8 Low battery, power-off and sleep

- Low threshold = config byte `0x200038CD` (factory `0x0A` = 10 %, **Verified** in the defaults at `0x1A3B8 + 5`). The LED indications are in section 2.3.
- **0 %** (`0xE30A`–`0xE316`) only sets the empty flag `0x2000307C` and copies the sleep timeout into the idle counter `0x20003048`. It does not power off by itself.
- The 1 s timer handler `0xAB44` enters sleep when the empty flag is set **or** the idle counter exceeds the timeout `u16 0x200038CB` (factory 420 s; the counter only runs while the timeout is not `0xFFFF`), **but only while USB is not configured** (`0xAB6A`). Emulated, 8 cases: sleep at count 421; the empty flag sleeps even with timeout `0xFFFF`; nothing sleeps while USB is configured.
- Sleep preparation `0xABC4`: battery stop, LEDs off (`0x18F94`), timers, radio and `TIMER0` stop, `TIMER1` stop, sensor shutdown, GPIOTE release, then the wake pins. **With the empty flag set it returns at `0xABF4`, before it arms the wake pins.** So a mouse that powered off at 0 % has no button, wheel or motion wake; per the nRF52840 specification only VBUS or a reset wakes it then (not from the image, not tested).
- The empty flag is also set while charging if the average is below 606 counts; with a charger and no host this would power the mouse off.
- `0xAB98`: in mode 1 it writes `CLOCK.TASKS_HFCLKSTOP` and `POWER.SYSTEMOFF = 1` (`0xABB6`). In any other mode it executes `svc 0x33` (`0xABA4`); that branch looks unreachable, since mode 0 implies USB configured, which blocks sleep.

### 1.9 What the host gets

- Battery query (group 0, command `0x83`, `0x148F2`): `body[6]` = charging flag, `body[7]` = reported percent. Emulated through `0x151AC`: `a1 00 00 02 00 83 00 64`, the reply captured on hardware (**Verified**, 2026-11-13).
- **No asynchronous battery notification was found**: nothing feeds battery values into the notification ring or the radio TX. A host must poll.

---

## 2. LED system

**Summary.** The firmware drives two RGB LEDs from two PWM peripherals. **In this build no code renders the stored lighting effects**: effect IDs, speeds and colours are stored, cycled by button chords and read and written by the host, but the engine `0xB328` only draws status indications (battery, pairing, DPI stage colour) or, in mode `0xA0`, two colours sent by the host. Three button chords write LED settings with a 3-zone loop into a 1-zone profile layout and overwrite DPI fields (section 2.6).

The source file name `Source\led.c` is a string at `0xD9D8` (**Verified**).

### 2.1 Pins and PWM

| LED | Channel | Pin | Framebuffer byte | PWM compare value |
| :--- | :--- | :--- | :--- | :--- |
| LED 0 (status) R / G / B | `PWM0` ch 0 / 1 / 2 | `P0.13` / `P0.15` / `P0.17` | `0x20004D67` / `66` / `65` | `0x20003270` / `72` / `74` |
| LED 1 (DPI colour) R / G / B | `PWM1` ch 0 / 1 / 2 | `P0.20` / `P0.22` / `P0.24` | `0x20004D6B` / `6A` / `69` | `0x20003278` / `7A` / `7C` |

- **Verified** (table values): the `nrf_drv_pwm` configs at `0x1A6D4` and `0x1A6E0` are `0D 0F 11 FF 07 07 00 00 FF 00 02 00` and `14 16 18 FF 07 07 00 00 FF 00 02 00`: pins, IRQ priority 7, `PRESCALER` 7 (125 kHz), up-counting, `COUNTERTOP` `0x00FF`, load mode 2 (individual). No pin has the "inverted" flag. So the PWM frequency is about 490 Hz with 8-bit duty.
- LED init `0xD91C` (called from main `0xE8AC` and from `0x9A7A`). Emulated register writes: `PSEL.OUT[0..2]` = 13, 15, 17 and 20, 22, 24, `PRESCALER` 7, `COUNTERTOP` `0xFF`, `DECODER` 2, `LOOP` 1, `INTEN` on `PWM0` only.
- Which colour is which is **Inferred** from use only: the first byte of every stored RGB triple goes to channel 0, the low-battery blink uses channel 0 only and "full" uses channel 1 only. Red/green/blue on the board is **Unknown** until a LED is watched or a pin probed.
- Polarity: every compare value is written as `value | 0x8000` (`0xA40C`–`0xA438`), the pins are not inverted, and "off" drives the pins low. So the LEDs look active-high.
- Only `PWM0` has an event handler, `0xA3D0`. It copies the framebuffer `0x20004D60` into both sequence buffers.
- **No LED power enable was found.** `P0.14` is configured as an output and driven **low** once in GPIO init (`0xC636`–`0xC64A`); `P0.26` is driven high (`0xC61A`–`0xC632`). No other code touches either pin, and neither LED-off nor sleep does. Their roles are **Unknown** (needs a probe).

### 2.2 Tick

- The `TIMER1` handler (1 ms, [`hardware.md`](hardware.md) 4.2) increments `0x2000324D` (`0x18BCE`).
- Main loop `0xE908` calls `0xDA3C`: when the LED driver is initialised and the count is 5 or more, it runs the engine `0xB328`. So the engine runs every 5 ms.

### 2.3 What the engine draws

It clears both LEDs, then (emulated; values are R G B):

| Condition | LED | Output |
| :--- | :--- | :--- |
| Battery `< 7 %` | 0 | Red `0x55`, 3 blinks of about 170 ms on / 170 ms off, then about 2.2 s off (`0xB358`–`0xB3B2`) |
| Charging, and the charge animation enabled (`0x2000324E`) | 0 | Breathing, 0 → 255 → 0 in 5 ms steps. Level `< 26`: red. `26..60`: `v, v·172/256, v·39/256`. `61..90`: `v, v·127/256, 0`. `> 90`: solid `00 FF 00` (`0xB3C8`–`0xB44A`) |
| Not charging, battery display on (`0x20003250`) | 0 | Blink every 500 ms. Level `< 26`: `7F 00 00`; `26..60`: `7F 56 14`; `61..90`: `7F 3F 00`; `91..100`: `00 7F 00` (`0xB44C`–`0xB49E`) |
| `0x20003630` set (RF control block `+0x14`; probably pairing or searching, not traced) | 0 | `00 FF 00` for 500 ms, off for 500 ms, replacing the rows above (`0xB4A0`–`0xB4C8`) |
| Sensor burst bit 3 set (`0x20003023`), 200 ms after it rises | 1 | Colour of the current DPI stage: 3 bytes at `P + 0x4A + 3 * stage index` (`0xB4CA`–`0xB500`) |
| Factory-reset hold (`0x2000305E`) | 0 | `00 FF 00` toggled every 200 ms, written in the PWM handler over anything else (`0xA3DC`–`0xA402`) |

- The meaning of sensor burst bit 3 is **Unknown** (lift status is the usual meaning in this sensor family).
- The charge animation enable `0x2000324E` is 1 after boot; host group 0 command `0x03` writes `body[6]` to it (`0x148EE`). It is not saved.
- The battery display flag `0x20003250` is toggled by button action type `0x0C` and by a 3 s hold of the DPI button (section 3.1). No factory button has that action.
- **Mode `0xA0`** (active effect byte `0x20004BF9 == 0xA0`): LED `i` shows `0x2000689E[3i..3i+2]` × brightness / 256, with brightness = config byte `0x200038D2 + [0x200035DE]` (index 0 when USB is not configured, 1 when it is; both factory `0xFF`). Every row above except the factory-reset blink is skipped. Emulated: colours `0A 14 1E` / `28 32 3C` at brightness `0x80` gave `05 0A 0F` / `14 19 1E`.

### 2.4 Stored effect settings (not rendered)

Per profile, 26 bytes at `P + 0x11` (`P = 0x200038C8 + 200 * profile`). `0xDA58(1)` copies them to `0x20004BF9` and fills a 15-byte state block picked by the effect byte. It is called from LED init, a profile switch, host group 2 command `0x00` and the effect chord.

| Offset | Factory (profiles 1 / 2 / 3) | Meaning |
| :--- | :--- | :--- |
| `P+0x11` | `01` / `02` / `05` | Effect ID |
| `P+0x12` | `00` | Host `body[9]`; no reader found |
| `P+0x13` | `0006` / `000C` / `0006` (u16 LE) | Speed value |
| `P+0x15` | `01` | No reader found |
| `P+0x16`..`P+0x2A` | `FF 00 00`, `00 00 FF`, `00 FF 00`, `FF 00 FF`, `FF FF 00`, `00 FF FF`, `FF FF FF` | Colours 1..7 |
| `P+0x4A`..`P+0x5B` | `FF FF 00`, `00 00 FF`, `FF 00 00`, `00 FF 00`, `FF 00 FF`, `00 FF FF` | DPI stage colours 1..6 (these are rendered, section 2.3) |
| `0x200038D2` / `0x200038D3` | `FF` / `FF` | Brightness, USB not configured / configured (global, used in mode `0xA0` only) |

- Factory values are **Verified** in the defaults table (`0x1A3C9`, `0x1A491`, `0x1A559`, `0x1A402`).
- The effect chord cycles IDs 0..8. Per-effect speed table at `0x1A6F2`, three u16 each (**Verified**): effect 0 `100/100/100`, 1 `10/6/4`, 2 `19/12/7`, 3 `12/6/3`, 4 `100/100/100`, 5 `12/6/3`, 6 `14/10/6`, 7 `150/100/50`, 8 `500/250/100`. Colour palette at `0x1A6BC` (red, blue, green, magenta, yellow, cyan, white).
- What each effect looked like is **Unknown**: the engine reads the effect byte only for the `== 0xA0` test. Emulated: 400 engine ticks with each of effects 0..7 and 9 left both LEDs dark. The names would have to come from Glorious Core or a sibling model's firmware.
- With effect 0 or 9 the state-block pointer is NULL, and `0xDA58` writes 15 bytes at addresses `0x0`..`0xE` (emulated). What that does on hardware (ignored, or a fault) is **Unknown**.

### 2.5 Host commands, group 2 (`body[4] = 2`, handler `0x156B8`)

All emulated through the dispatcher; none was sent to a mouse. `body[6]` = profile, 1-based; profile 0 does nothing and still answers `0xA1`. Neither profile nor zone is range-checked.

| `body[5]` | Request | Effect / reply |
| :--- | :--- | :--- |
| `0x00` set effect | `[6]` profile, `[7]` zone (0, or `0xFF` = all = zone 0), `[8]` effect, `[9]`, `[10]` speed, `[11..31]` 7 colours | Writes `P+0x11`, reloads (`0xDA58(1)`) and schedules a save. With `[8] == 0xA0` it only sets the active copy to `0xA0`: no reload, no save |
| `0x80` get effect | `[6]` profile, `[7]` zone | `[8]` effect, `[9]`, `[10]` speed low byte, `[11..31]` colours |
| `0x01` set DPI colours | `[6]` profile, `[7..24]` 6 × RGB | Writes `P+0x4A`, schedules a save |
| `0x81` get DPI colours | `[6]` profile | `[7..24]` |
| `0x02` set brightness | `[6]` index (0 / 1), `[7]` value | Writes `0x200038D2 + [6]`, schedules a save |
| `0x82` "get" brightness | `[6]` index, `[7]` | **Also writes** `[7]` to `0x200038D2 + [6]` (`0x157F4` is the same `strb` as the set path `0x157EC`), without a save, and echoes `[7]`. So it cannot read the value (a stock bug) |
| `0x03` direct colours | `[6..8]` LED 0 RGB, `[9..11]` LED 1 RGB | Sets mode `0xA0` in RAM and stores the colours. Not saved; ended by any reload |
| `0x83` | none | Answers `0xA1`, does nothing; other values answer `0xA3` |

### 2.6 Button chords (`0x14244`, on the pressed-button mask `0x2000305A`)

| Chord (mask) | Action | Code |
| :--- | :--- | :--- |
| L+R+M (`0x07`) | Starts the factory-reset hold; 5,000 ms later the defaults are restored. LED 0 blinks meanwhile | `0x1432C` |
| L+M+DPI (`0x25`) | Next colour (palette `0x1A6BC`), only when the effect is 4 or 5 | `0x14342` |
| R+M+DPI (`0x26`) | Brightness step `FF → 00 → 40 → 7F → C0 → FF` | `0x14476` |
| M+Back+DPI (`0x2C`) | Next speed | `0x144A4` |
| M+Forward+DPI (`0x34`) | Next effect, `(effect + 1) mod 9`, with that effect's first speed and default colours | `0x143DE` |

A chord returns without running the third button's own action and schedules a save.

> **Stock bug (emulated; Verified on a retail mouse for M+Forward+DPI, section 0): three chords overwrite DPI settings.** The colour, speed and effect chords loop over 3 zones of 26 bytes (`cmp #3` at `0x143C6`, `0x1445A`, `0x14500`), but a profile has one zone. Zones 1 and 2 land on `P+0x2B` and `P+0x45`:
> - M+Forward+DPI writes the effect ID to `P+0x2B` (DPI X low byte) and the speed to `P+0x2D` (DPI Y). Profile 1, one press: `P+0x2B` `B0 → 02`, `P+0x2D` `04B0 → 0013`. With new effect 3 it also overwrites the stage count, the stage index and the stages.
> - M+Back+DPI writes the speed to `P+0x2D` / `P+0x2E` and `P+0x47`.
> - L+M+DPI (effect 4 or 5, so factory profile 3) writes the colour over the stage count, the stage index and stage 1.
>
> The live DPI `0x20003068` is not touched. On hardware a restart rebuilt the DPI fields from the stage table, so no lasting DPI change was seen; a profile switch, which programs the sensor from `P+0x2B` (hardware.md 4.3), was not tried. The lighting effect and speed do change for good.

### 2.7 LEDs off

- `0x18F94` uninitialises both PWM instances and drives the six pins low. Its two callers are the sleep path (`0xABCA`) and `0x9A3A` in the main loop (USB configured and bit 3 of `0x200035E0` set; probably USB suspend, not traced).
- There is no separate "LEDs off for low battery" path.

---

## 3. Button actions and macros

### 3.1 Button events and layers

- `0x14244(state, index)` is the only entry to the action dispatcher for physical inputs. `index` `0..5` are the GPIO buttons (scan `0x16B0C`), `6` and `7` the wheel. It reads the action at `0x20004B30 + 15 * index + 1 + 7 * layer` and calls `0xA450(r0 = f0, r1 = state, r2 = index, r3 = pointer to f2..f6)`.
- The wheel is two virtual buttons: the quadrature decoder `0x16CB0` sends a press for index 6 or 7, and `0x1525C` sends the release 2 ticks later. So any action type can be bound to a wheel slot. Which direction is index 6 is **Unknown**.
- **Layer 1 is selected by a momentary shift action, type 9**: press sets `0x2000304E`, release clears it (`0xAA90`–`0xAA9E`). While it is set, a new press uses layer 1.
- Masks at `0x20003058` (pressed in layer 1) and `0x2000305A` (pressed in layer 0) send each release to the layer its press used (`0x14252`–`0x14292`). Emulated: with Forward = type 9, Back pressed while Forward is held sent the layer-1 key, and it was released correctly after Forward was let go first.
- Long hold (state 2) exists only for button index 5 (DPI), after 3,000 ticks. Its only effect: when the action is type 7 and no other button is down, it toggles the battery display flag `0x20003250` (`0xA814`–`0xA82A`; section 2.3). So holding the DPI button for 3 s should show the battery level on LED 0 (not tested).

### 3.2 Action format

`f0` type, `f1` number of bytes that follow, `f2..f6` parameters. The dispatcher does not read `f1`, but the host "get" command returns `f1 + 2` bytes (`0x14642`–`0x1464A`). The factory map has `f1 = 1` in every slot (**Verified**, `0x1A414`: `01 01 01`, `07 01 06`, `01 01 10`).

| `f0` | Handler | Action | Parameters |
| :--- | :--- | :--- | :--- |
| `0x00` | `0xA4CA` | none (disabled) | |
| `0x01` | `0xA50A` | mouse button / wheel | `f2` = `1..5`: button. `f2` = `0x10` / `0x11`: wheel `+1` / `-1`, on press only |
| `0x02` | `0xAAEA` | repeat ("fire") another action | `f2` nested type, `f3` nested `f2`, `f4` count (`0xFF` = while held), `f5..f6` period in ticks, big-endian |
| `0x03` | `0xA4CA` | no handler | |
| `0x04` | `0xA830` | keyboard key | `f2` modifier bits, `f3` key usage |
| `0x05` | `0xA9DA` | consumer key | `f2..f3` 16-bit usage, big-endian |
| `0x06` | `0xA9B6` | system control | `f2` = report byte (bit 0 power down, bit 1 sleep, bit 2 wake) |
| `0x07` | `0xA5F4` | DPI | `f2` sub-function, table below |
| `0x08` | `0xAA04` | profile switch | `f2` = 1 next, 2 previous, 3 next with wrap, 4 previous with wrap ([`hardware.md`](hardware.md) 4.3) |
| `0x09` | `0xAA90` | layer shift (hold) | |
| `0x0A` | `0xA5D6` | tilt / horizontal scroll | `f2` = `0x10` / `0x11`: `+1` / `-1` in report byte 6, on press only |
| `0x0B` | `0xAAA4` | report the button to the host only | sets / clears bit `index` of `0x2000305C` and queues notification type 4 |
| `0x0C` | `0xA4C0` | toggle the battery display on LED 0 (`0x20003250`) | |
| `0x10` | `0xA8FA` | macro, play N times | `f2..f3` macro id big-endian, `f4` number of plays |
| `0x11` | `0xA932` | macro, loop while held | `f2..f3` id |
| `0x12` | `0xA972` | macro, toggle (press starts a loop, next press stops it) | `f2..f3` id |
| `0xF0` | `0xA4DC` | radio pairing mode | |

Any other `f0` returns at `0xA4CA`. Types `0`, `1`, `2`, `4`–`0x0C` and `0x10`–`0x12` were run in Unicorn through `0x14244`.

Type 7 sub-functions (`f2`, jump table `0xA622`), on press unless noted:

| `f2` | Address | Action |
| :--- | :--- | :--- |
| 1 | `0xA62A` | stage up, stops at the last stage |
| 2 | `0xA6A8` | stage down, stops at stage 0 |
| 3 | `0xA6B8` | set stage `f4` (1-based). An out-of-range `f4` is still stored in the RAM index before the range check (`0xA6C4`–`0xA6CC`) |
| 4 | `0xA71A` | hold: while held, the wheel changes DPI X and Y by ±100, clamped to 0..16000 (`0xA55A`–`0xA5A0`), instead of scrolling. Release schedules a save |
| 5 | `0xA732` | DPI shift ([`hardware.md`](hardware.md) 4.1) |
| 6 | `0xA758` | stage up with wrap (the factory DPI button) |
| 7 | `0xA76E` | stage down with wrap |

Every type 7 press also queues notification type 1 (stage + 1, DPI X, DPI Y big-endian; `0xA77A`–`0xA7A2`).

### 3.3 How keys reach USB

- Keyboard: 8-byte report image at `0x20004BA8` (modifiers, reserved, 6 keys). Macros use a second image at `0x20004BB0`. `0xBD0C` merges both and the result goes out as report ID 3 (`0x14E02`–`0x14E34`). With all 6 key slots full, the modifiers change but no report is flagged.
- Consumer and system share the 3-byte buffer `0x20006CF3`: `[1, usage low, usage high]` (ID 1) or `[2, bits]` (ID 2), all zero on release.
- Emulated: type 4 `f2 = 02, f3 = 04` gave `02 00 04 00 00 00 00 00`; type 5 `f2..f3 = 01 23` gave `01 23 01`; type 6 `f2 = 02` gave `02 02`.
- Notifications: 8 entries of 8 bytes at `0x20004BB8`. Entry byte 0 is the type: 1 DPI, 2 profile, 4 type-`0x0B` button. The USB sender puts **report ID 4** in front (`0x14F86`, 9 bytes at `0x14FB4`); over the radio it is record type 5 (`0x15008`).

### 3.4 Repeat ("fire") engine

- 8 records of 12 bytes at `0x20004C84`, one per button index.
- `0x16614` runs from the `TIMER1` handler (`0x18C92`), so one tick is 1 ms. It presses for one tick every `period` ticks.
- Count `0xFF` never counts down; the record is cleared when the button is released. Other counts run to the end after the button is released.
- Emulated, `02 05 01 01 03 00 32` (left click, 3 times, period 50): button bit set at ticks 1, 51, 101 and cleared one tick later.

### 3.5 Macro storage

| Item | Address | Notes |
| :--- | :--- | :--- |
| Directory, RAM | `0x20004FA8`, `0x4CB` bytes | loaded from flash at boot (`0xD4FC`), rebuilt when the magic or CRC is wrong (`0xE778`) |
| Directory, flash | page `0x8C000` | written by the deferred save `0x14668` (`0x14722`–`0x14750`) |
| Macro data | `0x90000`–`0xDFFFF` | 80 pages of 4 KB |
| Page buffer | `0x2000568C`, 4 KB | one shared write buffer |

- Directory layout: `+0` `u32` magic `0x4CB`, `+4` `u32` free bytes (blank `0x50000`), `+8` 80-bit page bitmap (1 = free), `+0x14` `u32` macro count, `+0x18` `u8` default event delay (set to 5 at boot, `0xD554`), `+0x19` 100 entries of 12 bytes, `+0x4C9` CRC-16 (CCITT, init `0xFFFF`).
- Entry: `u16` id (`0xFFFF` = free), `u32` size, `u32` flash address, `u16` CRC of the first 10 bytes, all little-endian.
- A macro in flash is a 6-byte header (`u16` id, `u32` size) followed by `size` bytes of events.
- Limits: 100 directory entries, 80 pages, 20 macros playing at once.
- **Stock defect: a macro must fit one page (at most 4,090 event bytes).** The allocator marks only the first page as used (`0x9F96`). Emulated: a 5,000-byte macro is refused (`0xA2`) on a blank store; with pages 0 and 1 free and page 2 used it is accepted at `0x90000`, and the next macro is then placed at `0x91000`, inside it. Delete also frees one page bit only.

### 3.6 Macro commands (group 4, `body[4] = 4`, handler `0x149E8`)

Multi-byte fields are big-endian. `id` = `body[6..7]`. Failure is `0xA2`, an unknown command `0xA3`. Emulated end to end; none was sent to a mouse.

| `body[5]` | Request | Reply / effect |
| :--- | :--- | :--- |
| `0x80` | none | `body[6..9]` total size (`00 05 00 00`), `body[10..13]` free bytes |
| `0x01` | `id`, `body[8..11]` size | create. An existing macro with that id is deleted first |
| `0x81` | `id` | `body[8..11]` size; `0xA2` when the id does not exist |
| `0x02` | `id` | delete |
| `0x82` | | accepted, does nothing (`0x14A18`) |
| `0x03` | `id`, `body[8..11]` offset, `body[12]` length, `body[13..]` data | write event bytes at `offset` (`0x1A23C`) |
| `0x83` | `id`, `body[8..11]` offset, `body[12]` length | `body[13..]` data (`0x16250`) |

- Write is buffered: data is flashed only when `offset + length` reaches the macro size or the page end. So chunks must be sent in order and the last chunk must end exactly at the size.
- Neither read nor write checks `offset` or `length`. A 64-byte report holds 51 data bytes; a larger `body[12]` reads or writes past the request buffer, and a read with a large offset returns memory outside the macro.

### 3.7 Key assignment (group 3, handler `0x14540`)

- Group 3 has only `0x00` (set) and `0x80` (get) ([`hardware.md`](hardware.md) 4.1). Any other `body[5]` answers `0xA3`.
- `body[6] = 0` is ignored but still answered as success. A profile above 3 and a layer above 1 are not checked and would write outside the map.
- An assignment is the only link between a button and a macro: the action stores the macro id.

### 3.8 Macro playback

- Runtime table: 20 records of 26 bytes at `0x20005473` (active, button index, mode 1 count / 2 hold / 3 toggle, plays left, id, delay countdown, position, pointers, size).
- Engine `0xE32C`: called from the main loop (`0xE8FC`) when the `TIMER1` handler has incremented `0x2000306C`, so at most once per millisecond. Events are 5 ticks apart; a delay event of `N` makes the next event `5 + N` ticks later.
- Hold mode stops on release and toggle mode on the second press; the engine then clears the macro keyboard image. A macro that ends in play-N mode does not release keys it left down.
- A profile switch frees every record (`0x16A1C`) and clears the repeat records, both keyboard images and the button bytes (`0x18638`).

Event bytes (the first byte is the opcode; decode at `0xE3C6`–`0xE68E`):

| Opcode | Length | Effect |
| :--- | :--- | :--- |
| `0x01` | 2 | mouse button byte = arg |
| `0x02` / `0x03` | 2 | key down / key up, arg = usage |
| `0x04`, `0x05` | 2 | system control report `[2, arg]` (the two handlers are identical) |
| `0x06` | 3 | consumer report, usage little-endian (the button action stores it big-endian) |
| `0x07` | 3 | writes the same buffer but flags no report, so nothing is sent |
| `0x08` | 5 | mouse move: big-endian `i16` X, Y |
| `0x09` / `0x0A` | 2 | set / clear modifier bits |
| `0x10` | 2 | wheel byte = arg |
| `0x20` / `0x21` / `0x22` | 2 / 3 / 4 | extra delay, 8 / 16 / 24 bits big-endian, in ticks |
| `0x23` | 5 | extra delay, computed wrongly (`0xE446`–`0xE45C`); do not use |
| `0x00`, others | | ends the macro |

- A button set by opcode `0x01` is dropped by the next move or wheel event (`0x14AFE`–`0x14B52`), so a macro cannot drag.
- Emulated with a 34-byte macro (Shift+A, a 100-tick delay, key B, a move, a click, a wheel step, a consumer key) bound as `10 03 01 02 02`: reports at ticks 0, 5, 35, 40, 150, 155, ... with the expected contents, the second play from tick 195, the record freed at 385.

---

## 4. Corrections to older pages

| Older statement | Now |
| :--- | :--- |
| `P1.06` "charger / power status 1" | The only charging input, low = charging (1.5) |
| `P1.04` "power / mode switch input" | Configured but never read in this build (1.6) |
| `P0.14` "LED power enable" | Driven low once at init and never touched again; role Unknown (2.1) |
| LED pins handled at `0x18FA6`–`0x18FE8` and `0x16B8C`–`0x16BC6` | `0x18F94` is LED-off; `0x16B8C`–`0x16BC6` is button debounce. Init `0xD91C`, engine `0xB328`, PWM handler `0xA3D0` (2.1) |
| Profiles in pages `0x8B000` / `0x8C000` | All three profiles are in the one config at `0x8B000`; `0x8C000` is the macro directory (3.5) |
| Notifications "probably input report ID 5" | USB report ID 4; 5 is the radio record type (3.3) |
| Layer 1 of a button slot, selection unknown | A momentary shift action, type 9 (3.1) |
| The mouse powers off at 0 % (`0xE30E`) | `0xE30E` only sets a flag; the 1 s handler powers off up to 1 s later, never while USB is configured, and then without arming the wake pins (1.8) |
| Sleep when the idle count passes the timeout | Also only while USB is not configured (1.8) |
| Chords L+M+DPI, R+M+DPI, M+Back+DPI, M+Forward+DPI "change LED settings" | They do, and three of them also overwrite DPI fields in emulation (2.6) |

## 5. Unknown

- The VDD level and the divider on `P0.31`, so the real voltages of the battery tables. Settle: a meter on VDD and `P0.31`.
- What `P1.04`, `P0.14` and `P0.26` are wired to, and which charger IC drives `P1.06`. Settle: PCB tracing.
- How many LEDs are fitted, where they are, and the colour of each PWM pin. Settle: watch the LEDs.
- Whether the chord bug of 2.6 shows on a retail mouse, and what the write to address 0 (2.4) does there.
- The intended look of the stored effects; their renderers are not in this image.
- Whether Glorious Core writes macros in this event encoding and which opcodes it uses. Settle: a USB capture of the vendor software.
- Real macro timing (the tick is 1 ms only while the main loop keeps up), and which wheel direction is index 6.
- The purpose of group 4 command `0x82`, of macro runtime mode 4 and of action type 3.
- What wakes the mouse after a 0 % power-off, and whether a mouse on a charger with no host stays on the radio.
