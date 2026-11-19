#!/usr/bin/env python3
"""
Battery, charging and sleep-gate checks on the stock mouse image in Unicorn (firmware-subsystems.md 1).
Needs unicorn; not run in CI. Output is derived from vendor firmware: keep it out of the repository.

  python3 battery_emu.py CM2856_Mouse_USB_v1.00.00_20220810.hex

It runs the Keil scatter loader (0x6200 -> 0xE860) to get the initialised RAM, prints the two
counts-to-percent tables, then asserts what it prints:
  - the battery tick 0xE1BC with scripted ADC counts and P1.06 levels: table lookup, the 16-sample
    average, "never rises while discharging", the charge-pin debounce, the preset to 100 on a change;
  - the battery query (group 0, command 0x83) through the dispatcher 0x151AC;
  - the 1 s sleep handler 0xAB44: idle timeout, the battery-empty flag, no sleep while USB is configured.
Ends with "ok".
"""
import struct, sys
from pathlib import Path
from unicorn import *
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool
HEX = sys.argv[1]
img = compx_hex_tool.load_app_hex(Path(HEX))[0]
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0, 0x100000); uc.mem_write(0x6000, img)
uc.mem_map(0x20000000, 0x40000)
uc.mem_map(0x40000000, 0x100000); uc.mem_map(0x50000000, 0x1000); uc.mem_map(0xE0000000, 0x100000)
STOP = 0xFFF00; uc.mem_write(STOP, b'\x00\xbf\x00\xbf')
rd = lambda a, n=1: bytes(uc.mem_read(a, n))
wr = uc.mem_write
u16 = lambda a: struct.unpack('<H', rd(a, 2))[0]
u32 = lambda a: struct.unpack('<I', rd(a, 4))[0]
uc.reg_write(UC_ARM_REG_SP, 0x2000af50)
uc.emu_start(0x6200 | 1, 0xE860, count=5000000)
assert uc.reg_read(UC_ARM_REG_PC) == 0xE860, hex(uc.reg_read(UC_ARM_REG_PC))
def call(fn, *args, sp=0x2003F000, count=200000):
    for r, v in zip([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3], args): uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.reg_write(UC_ARM_REG_LR, STOP | 1)
    uc.emu_start(fn | 1, STOP, count=count)
    assert uc.reg_read(UC_ARM_REG_PC) == STOP, hex(uc.reg_read(UC_ARM_REG_PC))
    return uc.reg_read(UC_ARM_REG_R0)
B = 0x20003074
if True:
    print('struct', rd(B, 0x18).hex())
    for name, a in (('dis coarse', B+0x18), ('dis fine', B+0x2e), ('chg coarse', B+0xf8), ('chg fine', B+0xf8+0x16)):
        n = 11 if 'coarse' in name else 101
        print(name, hex(a), [u16(a+2*i) for i in range(n)])
    print('cfg 0x200038C8', rd(0x200038C8, 16).hex())

# ---- battery tick 0xE1BC ----
sample = [600]
def hook(u, a, s, _):
    if a == 0xC870:   # stub the blocking SAADC read
        u.reg_write(UC_ARM_REG_R0, sample[0]); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
uc.hook_add(UC_HOOK_CODE, hook, begin=0xC870, end=0xC870)
def pin(level): wr(0x50000810, struct.pack('<I', level << 6))   # P1 IN bit 6
def reset(v, chg_pin_low=False):
    wr(B, bytes([100, 255, 100, 0, 0, 0, 0, 0, 0, 0]))
    for i in range(16): wr(0x20004D20 + 2*i, struct.pack('<H', v))
    wr(0x200038C8, bytes.fromhex('660201a4010a'))   # factory config head: sleep 420 s, low threshold 10
    wr(0x20003048, b'\0\0'); pin(0 if chg_pin_low else 1)
def tick(v, n=1):
    sample[0] = v
    for _ in range(n): call(0xE1BC)
st = lambda: dict(raw=rd(B)[0], scaled=rd(B+1)[0], pct=rd(B+2)[0], chg=rd(B+3)[0], low=rd(B+7)[0], empty=rd(B+8)[0], avg=u16(B+0xc))

# 1) discharge mapping, steady state (16 identical samples)
print('discharge: counts -> percent')
for v in (500, 583, 584, 585, 603, 641, 679, 700, 736, 773, 774, 900):
    reset(v); tick(v, 16); s = st(); print(' ', v, s)
    exp = 0 if v < 584 else 100 if v >= 774 else max(i for i in range(101) if u16(0x200030A2+2*i) <= v)
    assert s['pct'] == s['raw'] == exp and s['chg'] == 0 and s['avg'] == v
    assert s['empty'] == (exp == 0) and s['low'] == (exp <= 10)
    assert s['scaled'] == (255 if exp >= 100 else 2*exp + exp//2 + exp//16)
    assert u16(0x20003048) == (420 if exp == 0 else 0)          # 0 % preloads the idle counter with the sleep timeout
# 2) not charging: reported percent never rises
reset(700); tick(700, 16); a = st()['pct']; tick(760, 40); s = st(); print('rise while discharging:', a, '->', s)
assert s['pct'] == a and s['raw'] > a
# 3) moving average of 16: a single outlier moves avg by 1/16
reset(700); tick(700, 16); tick(860, 1); assert st()['avg'] == 710; tick(700, 16); assert st()['avg'] == 700
# 4) charge pin low for 10 further equal ticks -> charging flag, percent follows the charge table both ways
reset(700, chg_pin_low=True)
for i in range(1, 13):
    tick(700); 
    if st()['chg']: break
print('charging flag set on tick', i, st()); assert i == 11 and st()['chg'] == 1
exp = max(k for k in range(101) if u16(0x20003182+2*k) <= 700); assert st()['pct'] == exp, exp
tick(783, 20); assert st()['pct'] == 100; tick(650, 20); s = st(); print('charging, falls too:', s); assert s['pct'] < 100
tick(500, 20); s = st(); print('charging, below table:', s, 'idle ctr', u16(0x20003048)); assert s['pct'] == 0 and s['empty'] == 1
# 5) unplug: after debounce percent is preset to 100, then pulled down by the discharge table on the same tick
reset(700, chg_pin_low=True); tick(700, 12); assert st()['chg'] == 1
pin(1); tick(700, 12); s = st(); print('after unplug:', s); assert s['chg'] == 0 and s['pct'] == 61

# ---- battery query and sleep gate ----
FEAT, FLAG = 0x20004F28, 0x200032D4
wr(0x20003003, b'\1'); wr(0x200035DE, b'\0')
for chg, pct in ((1, 57), (0, 100)):
    b = bytearray(64); b[3] = 2; b[4] = 0; b[5] = 0x83
    wr(FEAT, bytes(b)); wr(FLAG, b'\1'); wr(0x20003077, bytes([chg])); wr(0x20003076, bytes([pct]))
    try: call(0x151AC)
    except Exception as e: print('  (dispatcher did not return cleanly:', e, ')')
    r = rd(FEAT, 10); print('reply', r.hex(' ')); assert r[0] == 0xA1 and r[6] == chg and r[7] == pct
# sleep gate: stub the sleep entry 0xAB98 and timer stop 0x7870, count calls
slept = [0]
def hook2(u, a, s, _):
    if a == 0xAB98: slept[0] += 1
    u.reg_write(UC_ARM_REG_R0, 0); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
uc.hook_add(UC_HOOK_CODE, hook2, begin=0xAB98, end=0xAB98); uc.hook_add(UC_HOOK_CODE, hook2, begin=0x7870, end=0x7870)
def run(timeout, ctr, empty, usb):
    wr(0x200038CB, struct.pack('<H', timeout)); wr(0x20003048, struct.pack('<H', ctr)); wr(0x2000307C, bytes([empty])); wr(0x200035DE, bytes([usb]))
    slept[0] = 0; call(0xAB44); return slept[0], u16(0x20003048)
for args, exp in (((420, 418, 0, 0), (0, 419)), ((420, 419, 0, 0), (0, 420)), ((420, 420, 0, 0), (1, 421)), ((420, 420, 0, 1), (0, 421)),
                  ((0xFFFF, 5, 0, 0), (0, 5)), ((0xFFFF, 5, 1, 0), (1, 5)), ((0xFFFF, 5, 1, 1), (0, 5)), ((420, 0, 1, 0), (1, 1))):
    got = run(*args); print('timeout,ctr,empty,usb =', args, '-> sleep,ctr =', got); assert got == exp
print('ok')
