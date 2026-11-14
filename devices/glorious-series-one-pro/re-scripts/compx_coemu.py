#!/usr/bin/env python3
"""
Co-emulation of the stock Series One Pro mouse and its 2.4 GHz receiver: both official images boot
in Unicorn at the same time, their RADIO peripherals are modelled, and every frame one side transmits
is encoded to on-air bits with compx_radio.py from that side's own register values, then decoded with
the other side's registers (address match, de-whitening, CRC). Needs unicorn; not run in CI.

  python3 compx_coemu.py CM2856_Mouse_USB_v1.00.00_20220810.hex CM2856_Receiver_USB_v1.00.00_20220810.hex
      [--trace] [--vectors] [--sof-phase US] [--drop-answers N] [--drift-ppm PPM] [--outage START_MS,LEN_MS]
      [--move US] [--queries N] [--ms N]

Default run, set up in the paired state (receiver config page 0x38000 holds the mouse's FICR DEVICEID[0],
here the made-up 0x11223344). It asserts the connect request and answer, the link coming up, the 1 ms
keep-alive spacing, a cursor move and a left click reaching USB interface 0, and a relayed version query
from the receiver's host buffer to the mouse and back; it prints the times and ends with "ok".
The first answer of each connect is lost on air by default (--drop-answers): when the mouse takes the
first answer, the receiver joins the hop channels about 2.6 ms later and stays one channel behind, and
the emulated pair never links (radio-protocol.md 3.3). --ms runs (with --move, --drift-ppm, --outage)
print the link episodes instead; they produced the tables in radio-protocol.md 3.3, 4.4 and 4.5.

What is modelled, and what is not:
  - CPU: 64 instructions per simulated microsecond; interrupt handlers take no simulated time, and run
    between main-thread slices of at most 20 us. The firmware's busy-wait delay loops return at once.
  - RADIO: ramp-up 40 us (MODECNF0.RU fast), 2 Mbit/s air time, SHORTS, DISABLED from DISABLE in any
    state (the firmware spins on it), ADDRESS/END/CRC events, PPI. No noise, no collisions, no RSSI.
  - TIMER, RTC, PPI, GPIO, NVIC enables, CLOCK, NVMC erase, RNG. The PAW3370 sensor (legacy SPI2,
    burst register 0x16, motion pin P0.06) and the SAADC are small stubs.
  - USB: the receiver's USB device controller is not modelled. It is "configured", a 1 ms SOF calls the
    frame start 0xD9F4 as the USB event handler 0xE77C would, a host feature request is written into
    the receiver's buffer 0x2000016C with the flag 0x2000000A (as the SET_REPORT handler does), and
    input reports are taken at app_usbd_hid_generic_in_report_set (0x8D00), which the host completes
    1 ms later (busy flag 0x20000024).
Output is derived from vendor firmware: keep it out of the repository, except the protocol frames
with the made-up device ID that compx_radio.py carries as test vectors.
"""
import argparse
import hashlib
import heapq
import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_XPSR)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool  # noqa: E402
import compx_radio as cr  # noqa: E402

MOUSE_SHA256 = 'e50510e0eb213e69d62a088d6f5b792fb88b0f3dc56d3c9c21dd0ca8b6e892be'
RX_SHA256 = 'cc356a18a7b8ae6811d431956f9aa48dec25b76bd77b92bb4e1881868faefd44'
MOUSE_ID, RX_ID = 0x11223344, 0xA1B2C3D4  # made-up FICR DEVICEID[0] values

IPS = 64
STOP = 0xFFF00
P = 0x40000000
CLOCK, RADIO, GPIOTE, SAADC, TIMER0, TIMER1, TIMER2, RTC0, RNG = P, P + 0x1000, P + 0x6000, P + 0x7000, P + 0x8000, P + 0x9000, P + 0xA000, P + 0xB000, P + 0xD000
RTC1, NVMC, PPI, TIMER3, TIMER4, RTC2, SPI2 = P + 0x11000, P + 0x1E000, P + 0x1F000, P + 0x1A000, P + 0x1B000, P + 0x24000, P + 0x23000
IRQ = {CLOCK: 0, RADIO: 1, GPIOTE: 6, SAADC: 7, TIMER0: 8, TIMER1: 9, TIMER2: 10, RTC0: 11, RTC1: 17, TIMER3: 26,
       TIMER4: 27, RTC2: 36, **{P + 0x14000 + 0x1000 * i: 20 + i for i in range(6)}}


class Timer:
    def __init__(self, chip, base):
        self.c, self.b, self.running, self.t0, self.cnt0, self.gen = chip, base, False, 0.0, 0, 0

    def rate(self):
        return 16 / (1 << (self.c.r32(self.b + 0x510) & 0xF))

    def mask(self):
        return (0xFFFF, 0xFF, 0xFFFFFF, 0xFFFFFFFF)[self.c.r32(self.b + 0x508) & 3]

    def ticks(self, now):  # whole ticks since t0; the epsilon absorbs float error at exact compare times
        return int((now - self.t0) * self.rate() + 1e-6)

    def count(self, now):
        return (self.cnt0 + self.ticks(now)) & self.mask() if self.running else self.cnt0

    def task(self, off, now):
        if off == 0x00 and not self.running:
            self.running, self.t0 = True, now
        elif off in (0x04, 0x10):
            self.cnt0, self.running = self.count(now), False
        elif off == 0x0C:
            self.cnt0, self.t0 = 0, now
        elif 0x40 <= off < 0x58:  # CAPTURE[n]
            self.c.w32(self.b + 0x540 + off - 0x40, self.count(now))
            return
        else:
            return
        self.resched(now)

    def resched(self, now):
        self.gen += 1
        if not self.running:
            return
        cnt = self.count(now)
        d = min(((self.c.r32(self.b + 0x540 + 4 * n) & self.mask()) - cnt) & self.mask() or self.mask() + 1 for n in range(6))
        self.c.sim.sched(self.t0 + (self.ticks(now) + d) / self.rate(), self.fire, self.gen)

    def fire(self, now, gen):
        if gen != self.gen or not self.running:
            return
        cnt, shorts = self.count(now), self.c.r32(self.b + 0x200)
        for n in range(6):
            if self.c.r32(self.b + 0x540 + 4 * n) & self.mask() == cnt:
                self.c.event(self.b + 0x140 + 4 * n, now)
                if shorts & (1 << n):
                    self.cnt0, self.t0 = 0, now
                if shorts & (1 << (n + 8)):
                    self.cnt0, self.running = cnt, False
        self.resched(now)


class Rtc:
    def __init__(self, chip, base):
        self.c, self.b, self.running, self.t0, self.cnt0, self.gen = chip, base, False, 0.0, 0, 0

    def tick(self):
        return 1e6 / 32768 * ((self.c.r32(self.b + 0x508) & 0xFFF) + 1)

    def count(self, now):
        return (self.cnt0 + (int((now - self.t0) / self.tick()) if self.running else 0)) & 0xFFFFFF

    def task(self, off, now):
        if off == 0 and not self.running:
            self.running, self.t0 = True, now
        elif off == 4:
            self.cnt0, self.running = self.count(now), False
        elif off == 8:
            self.cnt0, self.t0 = 0, now
        else:
            return
        self.resched(now)

    def resched(self, now):
        self.gen += 1
        en = self.c.inten.get((self.b, 0x304), 0) | self.c.inten.get((self.b, 0x340), 0)
        if not self.running or not en & 0xF0000:
            return
        cnt = self.count(now)
        d = min(((self.c.r32(self.b + 0x540 + 4 * n) - cnt) & 0xFFFFFF) or 0x1000000 for n in range(4) if en & (0x10000 << n))
        whole = self.t0 + ((cnt - self.cnt0) % 0x1000000 + d) * self.tick() + 0.01
        self.c.sim.sched(whole, self.fire, self.gen)

    def fire(self, now, gen):
        if gen != self.gen:
            return
        for n in range(4):
            if self.c.r32(self.b + 0x540 + 4 * n) & 0xFFFFFF == self.count(now):
                self.c.event(self.b + 0x140 + 4 * n, now)
        self.resched(now + 1)


class Gpio:
    """OUT tracking; IN levels default high (pull-ups: buttons released, motion pin idle)."""

    def __init__(self, chip):
        self.c, self.out, self.level, self.on_out = chip, {0: 0, 1: 0}, {}, []
        for port in (0, 1):
            base = 0x50000000 + 0x300 * port
            chip.read_hooks[base + 0x510] = lambda now, p=port: 0xFFFFFFFF & ~sum(
                1 << pin for (q, pin), lv in self.level.items() if q == p and not lv)
            for off, op in ((0x504, 'w'), (0x508, 's'), (0x50C, 'c')):
                chip.write_hooks[base + off] = lambda v, now, p=port, op=op: self.write(p, op, v)

    def write(self, port, op, v):
        old = self.out[port]
        self.out[port] = new = v if op == 'w' else old | v if op == 's' else old & ~v
        self.c.sim.sched(self.c.sim.now, lambda t: self.c.w32(0x50000504 + 0x300 * port, new))
        for cb in self.on_out:
            cb(port, old, new)


class Paw3370:
    """PAW3370 on legacy SPI2, CSN P0.04: a register file, motion burst 0x16, motion pin P0.06 (active low)."""

    def __init__(self, chip, gpio):
        self.c, self.gpio = chip, gpio
        self.reg = {0x00: 0x47, 0x20: 0x0F}  # 0x20 = 0x0F ends the init poll at 0x1748E
        self.idx = self.addr = self.dx = self.dy = self.bursts = 0
        self.burst = b''
        gpio.level[(0, 6)] = 1
        chip.write_hooks[SPI2 + 0x51C] = self.txd
        gpio.on_out.append(lambda port, old, new: setattr(self, 'idx', 0) if port == 0 and old & 0x10 and not new & 0x10 else None)

    def move(self, dx, dy):
        self.dx, self.dy = self.dx + dx, self.dy + dy
        self.gpio.level[(0, 6)] = 0

    def txd(self, b, now):
        b, rx = b & 0xFF, 0
        if self.idx == 0:
            self.addr = b
            if b == 0x16:
                self.bursts += 1
                self.burst = struct.pack('<BBhhBBBBBB', 0x80 if self.dx or self.dy else 0, 0xBF, self.dx, self.dy, 0x40, 0x80, 0x90, 0x10, 0, 0x60)
                self.dx = self.dy = 0
                self.gpio.level[(0, 6)] = 1
        elif self.addr & 0x80:
            self.reg[self.addr & 0x7F] = b
        elif self.addr == 0x16:
            rx = self.burst[self.idx - 1] if self.idx <= len(self.burst) else 0
        else:
            rx = self.reg.get(self.addr, 0)
        self.idx += 1
        self.c.w32(SPI2 + 0x518, rx)
        self.c.w32(SPI2 + 0x108, 1)


class Saadc:
    def __init__(self, chip, value=800):
        self.c, self.value = chip, value
        chip.periph[SAADC] = self

    def task(self, off, now):
        if off == 0x00:
            self.c.event(SAADC + 0x100, now)
        elif off == 0x04:
            n = max(1, self.c.r32(SAADC + 0x630))
            self.c.w(self.c.r32(SAADC + 0x62C), struct.pack('<h', self.value) * n)
            self.c.w32(SAADC + 0x634, n)
            self.c.event(SAADC + 0x10C, now)
            self.c.event(SAADC + 0x104, now)

    def write(self, off, v, now):
        pass


REG = dict(MODE=0x510, PCNF0=0x514, PCNF1=0x518, BASE0=0x51C, BASE1=0x520, PREFIX0=0x524, PREFIX1=0x528,
           TXADDRESS=0x52C, RXADDRESSES=0x530, CRCCNF=0x534, CRCPOLY=0x538, CRCINIT=0x53C, DATAWHITEIV=0x554)


class Radio:
    def __init__(self, chip):
        self.c, self.state, self.gen = chip, 'off', 0
        chip.periph[RADIO] = self
        chip.w32(RADIO + 0x554, 0x40)  # DATAWHITEIV reset value; neither image writes it
        chip.sim.radios.append(self)

    def cfg(self):
        return {k: self.c.r32(RADIO + o) for k, o in REG.items()}

    def write(self, off, v, now):
        pass

    def ev(self, off, now):
        self.c.event(RADIO + off, now)
        sh = self.c.r32(RADIO + 0x200)
        for event, bit, task in ((0x100, 0, 0x08), (0x10C, 1, 0x10), (0x10C, 5, 0x08), (0x110, 2, 0x00), (0x110, 3, 0x04)):
            if off == event and sh & (1 << bit):
                self.task(task, now)

    def later(self, dt, fn):
        g = self.gen
        self.c.sim.sched(self.c.sim.now + dt, lambda t: fn(t) if g == self.gen else None)

    def task(self, off, now):
        if off in (0x00, 0x04) and self.state == 'off':  # TXEN / RXEN
            self.gen += 1
            tx = off == 0
            self.state = 'txru' if tx else 'rxru'

            def ready(t):
                self.state = 'txidle' if tx else 'rxidle'
                self.ev(0x100, t)
            self.later(40 if self.c.r32(RADIO + 0x650) & 1 else 130, ready)
        elif off == 0x08 and self.state == 'txidle':  # START
            self.state, self.gen = 'tx', self.gen + 1
            pp = self.c.r32(RADIO + 0x504)
            pkt = self.c.rd(pp, 1 + min(self.c.r8(pp), self.c.r32(RADIO + 0x518) & 0xFF))
            bits = cr.air_bits(self.cfg(), pkt)
            self.c.sim.transmit(self, now, self.c.r32(RADIO + 0x508), self.c.r32(RADIO + 0x510), bits, pkt)
            self.later(24, lambda t: self.ev(0x104, t))

            def end(t):
                self.state = 'txidle'
                self.ev(0x10C, t)
            self.later(len(bits) / 2, end)
        elif off == 0x08 and self.state == 'rxidle':
            self.state = 'rx'
        elif off == 0x10:  # DISABLE
            was, self.state, self.gen = self.state, 'off', self.gen + 1
            if was.startswith('tx'):
                self.later(4, lambda t: self.ev(0x110, t))
            else:  # from RX, or already disabled: the firmware spins on DISABLED after DISABLE (0x18AAC)
                self.ev(0x110, now)
        elif off == 0x14:  # RSSISTART
            self.c.w32(RADIO + 0x548, 60)
            self.ev(0x11C, now)

    def receive(self, t0, freq, mode, bits):
        if self.state != 'rx' or self.c.r32(RADIO + 0x508) != freq or self.c.r32(RADIO + 0x510) != mode:
            return
        la, pkt, ok = cr.parse_air(self.cfg(), bits)
        if la is None:
            return
        g = self.gen

        def addr(t):
            if g == self.gen and self.state == 'rx':
                self.c.w32(RADIO + 0x408, la)
                self.ev(0x104, t)

        def end(t):
            if g == self.gen and self.state == 'rx':
                self.c.w(self.c.r32(RADIO + 0x504), pkt)
                self.c.w32(RADIO + 0x400, int(ok))
                self.state = 'rxidle'
                self.ev(0x130 if ok else 0x134, t)
                self.ev(0x10C, t)
        self.c.sim.sched(t0 + 24, addr)
        self.c.sim.sched(t0 + len(bits) / 2, end)


class Chip:
    SETCLR = {0x304: 0x304, 0x308: 0x304, 0x344: 0x340, 0x348: 0x340}

    def __init__(self, name, img, base, sim, ram, deviceid, wfe, delays):
        self.name, self.sim, self.vtor = name, sim, base
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.mem_map(0, 0x100000)
        uc.mem_write(0, b'\xff' * 0x100000)
        uc.mem_write(base, img)
        uc.mem_write(STOP, b'\xfe\xe7' * 4)
        for a in delays:  # busy-wait delay loops -> bx lr
            uc.mem_write(a, b'\x70\x47')
        uc.mem_map(0x10000000, 0x2000)
        uc.mem_write(0x10000000, b'\xff' * 0x2000)
        uc.mem_write(0x10000060, struct.pack('<II', deviceid, 0x0BADF00D))  # FICR DEVICEID
        uc.mem_write(0x10000130, struct.pack('<II', 8, 3))  # errata part / revision words
        uc.mem_write(0x10001200, struct.pack('<IIII', 0x12, 0x12, 0xFFFFFFFF, 0xFFFFFFFE))  # UICR PSELRESET, NFCPINS
        uc.mem_write(0x10001304, struct.pack('<I', 5))  # UICR REGOUT0
        uc.mem_map(0x20000000, ram)
        uc.mem_map(0x40000000, 0x30000)
        uc.mem_map(0x50000000, 0x1000)
        uc.mem_map(0xE0000000, 0x100000)
        self.timers = {b: Timer(self, b) for b in (TIMER0, TIMER1, TIMER2, TIMER3, TIMER4)}
        self.rtcs = {b: Rtc(self, b) for b in (RTC0, RTC1, RTC2)}
        self.periph, self.read_hooks, self.write_hooks = {}, {}, {}
        self.pending, self.idle, self.inten, self.nvic, self.chen, self.rng = set(), False, {}, [0, 0], 0, 0x12345
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, acc, a, sz, v, _: self.periph_write(a, v), begin=0x40000000, end=0x5FFFFFFF)
        uc.hook_add(UC_HOOK_MEM_READ, self._read, begin=0x40000000, end=0x5FFFFFFF)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._nvic, begin=0xE000E100, end=0xE000E1FF)

        def wfe_hook(u, a, size, _):  # SDK idle: WFE; SEV; WFE -> sleep until an interrupt
            u.reg_write(UC_ARM_REG_PC, (wfe + 6) | 1)
            self.idle = True
            u.emu_stop()
        uc.hook_add(UC_HOOK_CODE, wfe_hook, begin=wfe, end=wfe)
        uc.reg_write(UC_ARM_REG_SP, self.r32(base))
        uc.reg_write(UC_ARM_REG_PC, self.r32(base + 4))
        self.w32(CLOCK + 0x40C, 0x10001)  # HFCLKSTAT, LFCLKSTAT: running
        self.w32(CLOCK + 0x418, 0x10001)
        self.w32(NVMC + 0x400, 1)
        self.w32(NVMC + 0x408, 1)

    def rd(self, a, n=1): return bytes(self.uc.mem_read(a, n))
    def r8(self, a): return self.rd(a)[0]
    def r32(self, a): return struct.unpack('<I', self.rd(a, 4))[0]
    def w(self, a, b): self.uc.mem_write(a, bytes(b))
    def w32(self, a, v): self.uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))

    def later_w32(self, a, v):  # a store inside a write hook would be overwritten by the store being hooked
        self.sim.sched(self.sim.now, lambda t: self.w32(a, v))

    def event(self, addr, now):
        self.w32(addr, 1)
        for ch in range(20):
            if self.chen & (1 << ch) and self.r32(PPI + 0x510 + 8 * ch) == addr:
                for tep in (self.r32(PPI + 0x514 + 8 * ch), self.r32(PPI + 0x910 + 4 * ch)):
                    if tep:
                        self.w32(tep, 1)
                        self.periph_write(tep, 1)
        base, bit = addr & ~0xFFF, ((addr & 0xFFF) - 0x100) // 4
        if base in IRQ and 0 <= bit < 32 and self.inten.get((base, 0x304), 0) & (1 << bit):
            self.pending.add(IRQ[base])
            self.idle = False

    def _nvic(self, uc, acc, addr, size, value, _):
        i = (addr - 0xE000E100) // 4
        if i in (0, 1):
            self.nvic[i] |= value
        elif i in (32, 33):
            self.nvic[i - 32] &= ~value

    def periph_write(self, addr, value):
        now, base, off = self.sim.now, addr & ~0xFFF, addr & 0xFFF
        if off in self.SETCLR and base < 0x50000000 and base != PPI:
            key = (base, self.SETCLR[off])
            v = self.inten[key] = self.inten.get(key, 0) | value if off in (0x304, 0x344) else self.inten.get(key, 0) & ~value
            for o in (key[1], key[1] + 4, key[1] + 8):
                self.later_w32(base + o, v)
        if base == PPI and off in (0x500, 0x504, 0x508):
            self.chen = value if off == 0x500 else self.chen | value if off == 0x504 else self.chen & ~value
            self.later_w32(PPI + 0x500, self.chen)
        if addr in self.write_hooks:
            self.write_hooks[addr](value, now)
        if off < 0x100 and value == 1:
            if base in self.timers:
                self.timers[base].task(off, now)
            elif base in self.rtcs:
                self.rtcs[base].task(off, now)
            elif base == CLOCK and off in (0x00, 0x08, 0x10):
                self.w32(CLOCK + {0x00: 0x100, 0x08: 0x104, 0x10: 0x10C}[off], 1)
            elif base == RNG and off == 0:
                self.rng = (self.rng * 1103515245 + 12345) & 0x7FFFFFFF
                self.w32(RNG + 0x508, (self.rng >> 16) & 0xFF)
                self.w32(RNG + 0x100, 1)
            elif base in self.periph:
                self.periph[base].task(off, now)
        elif base in self.timers and 0x540 <= off < 0x558:
            self.sim.sched(now, self.timers[base].resched)
        elif base in self.rtcs and (0x540 <= off < 0x550 or off in self.SETCLR):
            self.sim.sched(now, self.rtcs[base].resched)
        elif base == NVMC and off == 0x508:  # ERASEPAGE
            self.uc.mem_write(value & ~0xFFF, b'\xff' * 0x1000)
        elif base in self.periph:
            self.periph[base].write(off, value, now)

    def _read(self, uc, acc, addr, size, value, _):
        v = self.read_hooks[addr](self.sim.now) if addr in self.read_hooks else \
            self.rtcs[addr & ~0xFFF].count(self.sim.now) if addr & ~0xFFF in self.rtcs and addr & 0xFFF == 0x504 else None
        if v is not None:
            self.w32(addr & ~3, v)

    def call(self, fn, *args):
        """Run fn (an interrupt handler or a plain function) to completion on top of the main thread."""
        uc = self.uc
        regs = list(range(UC_ARM_REG_R0, UC_ARM_REG_R0 + 13)) + [UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_XPSR]
        saved = [uc.reg_read(r) for r in regs]
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM_REG_SP, (saved[13] - 0x80) & ~7)
        uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        uc.emu_start(fn | 1, STOP, count=2_000_000)
        pc, ret = uc.reg_read(UC_ARM_REG_PC), uc.reg_read(UC_ARM_REG_R0)
        for r, v in zip(regs, saved):
            uc.reg_write(r, v)
        if pc & ~1 != STOP:
            raise RuntimeError(f'{self.name}: {fn:#x} did not return (pc={pc:#x})')
        return ret

    def take_irqs(self):
        while self.pending and not self.uc.reg_read(UC_ARM_REG_PRIMASK) & 1:
            irq = min(self.pending)
            self.pending.discard(irq)
            if self.nvic[irq // 32] & (1 << (irq % 32)):
                self.call(self.r32(self.vtor + 0x40 + 4 * irq) & ~1)

    def run_main(self, us):
        if self.idle or int(us * IPS) < 1:
            return
        try:
            self.uc.emu_start(self.uc.reg_read(UC_ARM_REG_PC) | 1, 0xFFFFFFFF, count=int(us * IPS))
        except UcError as e:
            raise RuntimeError(f'{self.name}: {e} at pc={self.uc.reg_read(UC_ARM_REG_PC):#x}')


class Sim:
    def __init__(self):
        self.now, self.q, self.seq, self.chips, self.radios, self.air = 0.0, [], 0, [], [], []
        self.drop = lambda t, name, freq, pkt: False  # testbench: frames lost on air

    def transmit(self, src, t, freq, mode, bits, pkt):
        lost = self.drop(t, src.c.name, freq, pkt)  # called once per frame: it may count
        self.air.append((t, src.c.name, freq, pkt, bits, lost))
        if not lost:
            for r in self.radios:
                if r is not src:
                    r.receive(t, freq, mode, bits)

    def sched(self, t, fn, *args):
        heapq.heappush(self.q, (max(t, self.now), self.seq, fn, args))
        self.seq += 1

    def run(self, until, slice_us=20):
        while self.now < until:
            nxt = min(self.q[0][0] if self.q else until, until, self.now + slice_us)
            for c in self.chips:
                c.run_main(nxt - self.now)
            self.now = nxt
            while self.q and self.q[0][0] <= self.now:
                t, _, fn, args = heapq.heappop(self.q)
                fn(self.now, *args)
            for c in self.chips:
                c.take_irqs()


RX_IFACE = {0xE9B0: 0, 0xE9E8: 1, 0xEA20: 2}  # receiver app_usbd_hid_generic instances


def build(mouse_img, rx_img, sof_phase=20_300.0, sof_period=1000.0):
    sim = Sim()
    m = Chip('mouse', mouse_img, 0x6000, sim, 0x40000, MOUSE_ID, wfe=0x11878, delays=(0x1A960, 0x1A980))
    r = Chip('rx', rx_img, 0x7000, sim, 0x10000, RX_ID, wfe=0xAB94, delays=(0xE860, 0xEAA0, 0xEB90))
    sim.chips += [m, r]
    m.gpio, r.gpio = Gpio(m), Gpio(r)
    m.paw, m.adc = Paw3370(m, m.gpio), Saadc(m)
    Radio(m), Radio(r)
    # paired: config page 0x38000 = [0x0024][RF ID = mouse FICR DEVICEID[0]] ... CRC-16/CCITT-FALSE at +0x22
    page = bytearray(b'\xff' * 0x24)
    page[0:6] = struct.pack('<HI', 0x24, MOUSE_ID)
    page[0x22:] = struct.pack('<H', cr.crc(dict(CRCCNF=2, CRCPOLY=0x11021, CRCINIT=0xFFFF), [b for x in page[:0x22] for b in cr._bits(x, True)]))
    r.w(0x38000, page)
    r.reports = []

    def sof(t):
        if r.uc.reg_read(UC_ARM_REG_PRIMASK) & 1:
            return sim.sched(t + 2, sof)
        r.w(0x20000020, b'\x01')  # USB configured
        r.call(0xD9F4)
        r.idle = False
        sim.sched(t + sof_period, sof)
    sim.sched(sof_phase, sof)

    def in_report(uc, a, size, _):
        inst, ptr, n = (uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        r.reports.append((sim.now, RX_IFACE.get(inst, inst), bytes(uc.mem_read(ptr, n))))
        uc.reg_write(UC_ARM_REG_R0, 0)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        sim.sched(sim.now + 1000, lambda t: r.w(0x20000024, b'\x00'))
    r.uc.hook_add(UC_HOOK_CODE, in_report, begin=0x8D00, end=0x8D00)
    return sim, m, r


def host_request(sim, r, body):
    """A host SET_REPORT on interface 2: the 64 bytes land in 0x2000016C, then the HID event handler (R 0xA3F2)
    sets the flag 0x2000000A and byte 0 = 0xA0 if the flag was clear. Returns the GET_REPORT reader."""
    r.w(0x2000016C, bytes(body).ljust(64, b'\0'))
    if not r.r8(0x2000000A):
        r.w(0x2000000A, b'\x01')
        r.w(0x2000016C, b'\xa0')
    r.idle = False
    return lambda: r.rd(0x2000016C, 64)


def frames(sim, t0=0, t1=float('inf'), lost=False):
    """(time, sender, FREQUENCY, RAM packet) of the frames sent in [t0, t1); lost=True includes frames lost on air."""
    return [(t, n, f, pkt) for t, n, f, pkt, _, lo in sim.air if t0 <= t < t1 and (lost or not lo)]


def show(sim, t0=0, t1=float('inf')):
    for t, n, f, pkt, _, lo in sim.air:
        if t0 <= t < t1:
            print(f'{t / 1000:9.3f} ms {n:5s} {f:3d} {"LOST" if lo else "    "} {pkt.hex():<34s} '
                  f'{cr.describe(pkt, n == "rx", f in cr.PAIR_CHANNELS)}')


def load(path, sha):
    img = compx_hex_tool.load_app_hex(Path(path), 0x6000 if sha == MOUSE_SHA256 else 0x7000)[0]
    if hashlib.sha256(img).hexdigest() != sha:
        sys.exit(f'{path}: not the official v1.00.00 image; the addresses here are for that build only')
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mouse_hex')
    ap.add_argument('receiver_hex')
    ap.add_argument('--trace', action='store_true', help='print every frame')
    ap.add_argument('--vectors', action='store_true', help='print frames as compx_radio.VECTORS entries')
    ap.add_argument('--sof-phase', type=float, default=20_300.0, help='time of the first USB SOF, us (after the receiver radio init)')
    ap.add_argument('--drift-ppm', type=float, default=0.0, help='receiver SOF period error, ppm')
    ap.add_argument('--ms', type=float, default=None,
                    help='only run this long, no asserts; prints the link episodes (first ACK, drops, time up)')
    ap.add_argument('--move', type=float, default=0, help='move the sensor +1 X count every N us from boot (0: still)')
    ap.add_argument('--outage', default=None, help='START_MS,LEN_MS: drop all frames in that window')
    ap.add_argument('--drop-answers', type=int, default=1,
                    help='lose the first N connect answers of each connect (default 1; with 0 the emulated pair does '
                         'not link, radio-protocol.md 3.3)')
    ap.add_argument('--queries', type=int, default=1, help='relayed version queries to send back to back')
    a = ap.parse_args()
    sim, m, r = build(load(a.mouse_hex, MOUSE_SHA256), load(a.receiver_hex, RX_SHA256), a.sof_phase, 1000 * (1 + a.drift_ppm * 1e-6))
    outage = tuple(float(x) * 1000 for x in a.outage.split(',')) if a.outage else (0, 0)
    answers = []

    def drop(t, name, freq, pkt):
        if name == 'rx' and freq in cr.PAIR_CHANNELS:  # count answers per connect (a gap of > 10 ms starts a new one)
            answers[:] = [x for x in answers if t - x < 10_000] + [t]
            if len(answers) <= a.drop_answers:
                return True
        return outage[0] <= t < outage[0] + outage[1]
    sim.drop = drop
    if a.move:
        def tick(t):
            m.paw.move(1, 0)
            sim.sched(t + a.move, tick)
        sim.sched(a.move, tick)
    if a.ms is not None:
        return run_ms(sim, m, r, a)
    demo(sim, m, r, a)


def run_ms(sim, m, r, a):
    """Run a.ms milliseconds sampling the mouse's link flag C[6] every 1 ms; print the link episodes."""
    flags = []
    for ms in range(int(a.ms)):
        sim.run(ms * 1000.0 + 1000)
        flags.append(m.r8(0x2000361C + 6))
    if a.trace:
        show(sim)
    acks = [t for t, n, f, pkt in frames(sim) if n == 'rx' and f not in cr.PAIR_CHANNELS]
    ups = [i for i in range(1, len(flags)) if flags[i] and not flags[i - 1]]
    downs = [i for i in range(1, len(flags)) if flags[i - 1] and not flags[i]]
    first = acks[0] / 1000 if acks else None
    up_pct = 100 * sum(flags[ups[0]:]) / (len(flags) - ups[0]) if ups else 0
    print(f'first ACK {first and round(first, 3)} ms; mouse link flag up at {ups} ms, down at {downs} ms '
          f'({len(downs)} drops); up {up_pct:.1f} % of the time after the first link; '
          f'connect answers sent {sum(1 for t, n, f, p in frames(sim, lost=True) if n == "rx" and f in cr.PAIR_CHANNELS)}')


def demo(sim, m, r, a):
    C, RC = 0x2000361C, 0x20000050
    # 1) connect: request on a pairing channel, answer, then keep-alives on the hop table
    while not (m.r8(C + 6) and r.r8(RC + 6) and r.r8(0x20000056)) and sim.now < 1_000_000:
        sim.run(sim.now + 1000)
    assert m.r8(C + 6) == 1 and r.r8(RC + 6) == 1 and r.r8(0x20000056) == 1, 'link did not come up in 1 s'
    t_link = next(t for t, n, f, pkt in frames(sim) if n == 'rx' and f not in cr.PAIR_CHANNELS)
    sim.run(t_link + 10_000)
    req = next(pkt for t, n, f, pkt in frames(sim) if n == 'mouse' and f in cr.PAIR_CHANNELS)
    ans = [(t, f, pkt) for t, n, f, pkt in frames(sim, 0, t_link) if n == 'rx' and f in cr.PAIR_CHANNELS][-1]  # delivered
    assert req == cr.connect_request(MOUSE_ID), req.hex()
    assert ans[2][0] == 3 and ans[2][2:4] == b'\xe8\x03', ans[2].hex()  # [3][row][1000 us]
    row = ans[2][1]
    keep = frames(sim, t_link - 5000, sim.now)
    assert all(f in cr.HOP_TABLE[4 * row:4 * row + 4] for t, n, f, pkt in keep if f not in cr.PAIR_CHANNELS)
    ka = [t for t, n, f, pkt in keep if n == 'mouse' and pkt[0] == 2 and t > t_link]
    gaps = [b - a_ for a_, b in zip(ka, ka[1:])]  # 950 us while the timing bits settle the slot (4.5), then 1000
    assert gaps and all(min(abs(g - 1000), abs(g - 950)) < 1 for g in gaps) and abs(gaps[-1] - 1000) < 1, gaps
    print(f'link: request {req.hex()} on {next(f for t, n, f, p in frames(sim) if p == req)}, answer {ans[2].hex()} '
          f'(row {row}, last answer at {ans[0] / 1000:.3f} ms; the first {a.drop_answers} of each connect lost on air), '
          f'first ACK at {t_link / 1000:.3f} ms; keep-alive every 1000 us')
    # 2) cursor move, 3) left click (P1.15 low), 4) relayed version query
    t = sim.now
    for k in range(10):  # 10 ms of motion, +4/-1 counts per ms
        sim.sched(t + 1000 * k + 137, lambda tt: m.paw.move(4, -1))
    sim.run(t + 15_000)
    m.gpio.level[(1, 15)] = 0
    sim.run(t + 50_000)
    m.gpio.level[(1, 15)] = 1
    sim.run(t + 85_000)
    reports = [(tt, i, b) for tt, i, b in r.reports if tt >= t]
    mouse = [b for tt, i, b in reports if i == 0]
    xy = [struct.unpack_from('<hh', b, 2) for b in mouse]
    assert sum(x for x, y in xy) > 20 and sum(y for x, y in xy) < -5 and all(x >= 0 >= y for x, y in xy), xy
    assert any(b[0] == 1 for b in mouse) and mouse[-1][0] == 0, mouse
    ups = [pkt for tt, n, f, pkt in frames(sim, t) if n == 'mouse' and pkt[0] > 2]
    assert all(cr.records(p[3:])[0][0] == 1 for p in ups), 'only type-1 records expected here'
    print('move + click: uplink records', sorted({p[3:].hex() for p in ups}), '-> USB interface 0 reports', [b.hex() for b in mouse])
    t = sim.now
    replies = []
    for _ in range(a.queries):
        get = host_request(sim, r, bytes([0x00, 0x00, 0x02, 0x06, 0x00, 0x81]))
        t_q = sim.now
        while get()[0] in (0x00, 0xA0) and sim.now < t_q + 50_000:
            sim.run(sim.now + 100)
        replies.append((sim.now - t_q, get()[:12]))
        sim.run(sim.now + 2000)
    for dt, rep in replies:
        assert rep == bytes.fromhex('a10002030081010000002018'), rep.hex()  # v1.0.0.0, PID 0x2018
    dl = [pkt for tt, n, f, pkt in frames(sim, t) if n == 'rx' and pkt[0] > 2]
    ul = [pkt for tt, n, f, pkt in frames(sim, t) if n == 'mouse' and pkt[0] > 2]
    print(f'relayed version query x{len(replies)}: reply {replies[0][1].hex()} after {replies[0][0] / 1000:.1f} ms; '
          f'downlink records {sorted({p[3:].hex() for p in dl})[:4]}, uplink records {sorted({p[3:].hex() for p in ul})[:4]}')
    if a.trace:
        show(sim)
    if a.vectors:
        seen = set()
        for tt, n, f, pkt, bits, lost in sim.air:
            key = (n, pkt[:1] + bytes(len(pkt) > 3 and pkt[3:4]))
            if key in seen or pkt[0] == 0 or lost:
                continue
            seen.add(key)
            nib = ''.join(f'{sum(b << (3 - k) for k, b in enumerate(bits[i:i + 4])):x}' for i in range(0, len(bits), 4))
            print(f"    ({n!r}, {f}, '{pkt.hex()}', '{nib}'),")
    print('ok')


if __name__ == '__main__':
    main()
