#!/usr/bin/env python3
"""
Button actions, the repeat ("fire") engine and macros on the stock mouse image in Unicorn
(firmware-subsystems.md 3). Needs unicorn; not run in CI. Output is derived from vendor firmware: keep it
out of the repository.

  python3 macro_emu.py CM2856_Mouse_USB_v1.00.00_20220810.hex

Flash erase / write (0xBE78 / 0xBED8) are replaced by memory writes, sensor and LED calls are stubbed.
It asserts what it prints: the group 3 get, action types 4, 5, 6, 9, 0xA, 0xB, 0xC, 7 and 2 through
0x14244, the group 4 macro commands (create, write in two chunks, read back, delete), playback of a
34-byte macro in the play-N, hold and toggle modes, and the allocator with a macro larger than one page.
Ends with "ok" and "ok2".
"""
import sys,struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool
H = sys.argv[1]
BASE=0x6000
data=compx_hex_tool.load_app_hex(Path(H),BASE)[0]
def b(a,n): return data[a-BASE:a-BASE+n]
img, BASE = data, BASE
import struct, sys
from unicorn import *
from unicorn.arm_const import *
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0, 0x100000); uc.mem_write(0, b'\xff'*0x100000); uc.mem_write(BASE, img)
uc.mem_map(0x20000000, 0x40000); uc.mem_map(0x40000000, 0x100000); uc.mem_map(0x10000000, 0x2000)
STOP = 0xFFF00; uc.mem_write(STOP, b'\x00\xbf\x00\xbf')
rd = lambda a, n=1: bytes(uc.mem_read(a, n)); wr = uc.mem_write
flashlog = []
def hook(u, a, s, _):
    a &= ~1
    if a == STOP: u.emu_stop()
    elif a == 0xbe78:   # page erase
        p = u.reg_read(UC_ARM_REG_R0) & ~0xfff; wr(p, b'\xff'*0x1000); flashlog.append(('erase', p))
        u.reg_write(UC_ARM_REG_R0, 0); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    elif a == 0xbed8:   # write
        d, s_, n = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        wr(d, rd(s_, n)); flashlog.append(('write', d, n))
        u.reg_write(UC_ARM_REG_R0, 0); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    elif a in (0x17860, 0xda58, 0x1349c, 0x94d8):  # sensor DPI write, LED reload, pairing: stub
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
uc.hook_add(UC_HOOK_CODE, hook)
def call(fn, *args):
    for r, v in zip([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3], args): uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, 0x2003F000); uc.reg_write(UC_ARM_REG_LR, STOP | 1)
    uc.emu_start(fn | 1, STOP, count=2000000)
    return uc.reg_read(UC_ARM_REG_R0)
CFG, MAP, FEAT, ST = 0x200038C8, 0x20004B30, 0x20004F28, 0x2000304C
KB, KBM, FL, FLM = 0x20004BA8, 0x20004BB0, 0x2000300C, 0x200032DE
wr(CFG, img[0x1a3b8-BASE:0x1a3b8-BASE+0x266]); call(0xd358); call(0xe778); call(0x17854, 5)
def feat(group, cmd, payload=b''):
    b = bytearray(64); b[0] = 0xA0; b[4] = group; b[5] = cmd; b[6:6+len(payload)] = payload
    wr(FEAT, bytes(b)); call({3: 0x14540, 4: 0x149E8}[group]); return rd(FEAT, 64)
def setkey(code, layer, act, prof=1):
    r = feat(3, 0, bytes([prof, code, layer]) + bytes(act).ljust(7, b'\0')); assert r[0] == 0xA0, r.hex()
press = lambda i: call(0x14244, 1, i); release = lambda i: call(0x14244, 0, i)
flag = lambda: rd(FL)[0]
def clr(): wr(FL, b'\0')

# --- group 3 get: factory slot, length byte f1
r = feat(3, 0x80, bytes([1, 0x14, 0])); print('get DPI btn:', r[:16].hex(' ')); assert r[9:12] == bytes([7, 1, 6])
# --- type 4 keyboard
setkey(4, 0, [4, 2, 0x02, 0x04]); clr(); press(3)
print('kbd press :', rd(KB, 8).hex(' '), 'flag', hex(flag())); assert rd(KB, 8) == bytes([2, 0, 4, 0, 0, 0, 0, 0]) and flag() & 8
clr(); release(3); print('kbd release:', rd(KB, 8).hex(' '), 'flag', hex(flag())); assert rd(KB, 8) == bytes(8) and flag() & 8
# --- type 5 consumer (f2:f3 big-endian usage)
setkey(4, 0, [5, 2, 0x01, 0x23]); clr(); press(3)
print('consumer press:', rd(0x20006CF3, 3).hex(' '), hex(flag())); assert rd(0x20006CF3, 3) == bytes([1, 0x23, 0x01]) and flag() & 0x10
clr(); release(3); assert rd(0x20006CF3, 3) == bytes([1, 0, 0]) and flag() & 0x10
# --- type 6 system control
setkey(4, 0, [6, 1, 0x02]); clr(); press(3)
print('system press:', rd(0x20006CF3, 2).hex(' '), hex(flag())); assert rd(0x20006CF3, 2) == bytes([2, 2]) and flag() & 0x20
clr(); release(3); assert rd(0x20006CF3, 2) == bytes([2, 0])
# --- type 9 layer shift on Forward; Back layer0 = key 'a', layer1 = key 'b'
setkey(5, 0, [9, 0]); setkey(4, 0, [4, 2, 0, 4]); setkey(4, 1, [4, 2, 0, 5])
press(3); a = rd(KB, 8); release(3)
press(4); assert rd(ST + 2)[0] == 1; press(3); b = rd(KB, 8)
release(4); assert rd(ST + 2)[0] == 0; held = rd(KB, 8); release(3); c = rd(KB, 8)
print('layer0 key', a.hex(' '), '| layer1 key', b.hex(' '), '| after shift released, key still', held.hex(' '), '| after key release', c.hex(' '))
assert a[2] == 4 and b[2] == 5 and held[2] == 5 and c == bytes(8)
setkey(5, 0, [1, 1, 5])
# --- type 0xA tilt, 0xB host key, 0xC toggle
setkey(4, 0, [0x0A, 1, 0x10]); clr(); t0 = rd(ST + 0xA)[0]; press(3); assert rd(ST + 0xA)[0] == t0 + 1 and flag() & 4; release(3)
setkey(4, 0, [0x0B, 0]); w = rd(0x2000304F)[0]; clr(); press(3)
print('type B notif entry:', rd(KB + 0x10 + 8 * w, 8).hex(' '), 'mask', rd(ST + 0x10, 2).hex(), hex(flag())); assert flag() & 0x40
release(3)
setkey(4, 0, [0x0C, 0]); t = rd(0x20003250)[0]; press(3); assert rd(0x20003250)[0] == t ^ 1; release(3); press(3); release(3); assert rd(0x20003250)[0] == t
# --- type 7 sub 3 (set stage f4), sub 1/2/7
setkey(4, 0, [7, 3, 3, 0, 4]); press(3); print('stage idx after sub3 f4=4:', rd(0x20004CE6)[0], 'dpi', struct.unpack('<H', rd(0x20003068, 2))[0]); assert rd(0x20004CE6)[0] == 3; release(3)
setkey(4, 0, [7, 1, 1]); press(3); release(3); assert rd(0x20004CE6)[0] == 3   # up stops at last (4 stages)
setkey(4, 0, [7, 1, 2]); [ (press(3), release(3)) for _ in range(5)]; assert rd(0x20004CE6)[0] == 0
setkey(4, 0, [7, 1, 7]); press(3); release(3); assert rd(0x20004CE6)[0] == 3   # down wraps
setkey(4, 0, [7, 1, 4]); press(3); assert rd(ST + 1)[0] == 1
d0 = struct.unpack('<H', rd(0x20003068, 2))[0]; press(6); release(6); d1 = struct.unpack('<H', rd(0x20003068, 2))[0]; release(3)
print('DPI adjust hold + wheel slot 6:', d0, '->', d1); assert d1 == d0 + 100 and rd(ST + 1)[0] == 0
# --- type 2 fire: nested type 1 button 1, 3 clicks, period 50
setkey(4, 0, [2, 5, 1, 1, 3, 0, 50]); press(3); release(3)
ev = []; last = rd(ST + 6)[0]
for tck in range(1, 400):
    call(0x16614); v = rd(ST + 6)[0]
    if v != last: ev.append((tck, v)); last = v
print('fire count=3 period=50 button-bit transitions (tick, mask):', ev)
assert [e[0] for e in ev] == [1, 2, 51, 52, 101, 102] and rd(0x20004C84 + 12 * 3)[0] == 0
setkey(4, 0, [2, 5, 1, 1, 0xFF, 0, 10]); press(3); n = 0
for tck in range(100):
    call(0x16614); v = rd(ST + 6)[0]; n += (v != last); last = v
release(3); assert n == 20 and rd(0x20004C84 + 36, 12) == bytes(12); print('fire 0xFF: 10 clicks in 100 ticks while held, stops on release')
# --- group 4 macros
r = feat(4, 0x80); print('free space reply:', r[:14].hex(' ')); assert r[6:14] == bytes.fromhex('0005000000050000') and r[3] == 0x0E
mac = bytes([9, 0x02, 2, 0x04, 0x20, 20, 3, 0x04, 0x0A, 0x02, 0x21, 0x00, 0x64, 2, 0x05, 3, 0x05, 8, 0, 5, 0xFF, 0xFB, 1, 1, 1, 0, 0x10, 0xFF, 6, 0xE9, 0x00, 6, 0, 0])
ID = 0x0102
r = feat(4, 1, struct.pack('>HI', ID, len(mac))); print('create:', hex(r[0])); assert r[0] == 0xA0
print('dir entry:', rd(0x20004FA8 + 0x19, 12).hex(' '), 'free', hex(struct.unpack('<I', rd(0x20004FA8 + 4, 4))[0]), 'bitmap', rd(0x20004FA8 + 8, 12).hex())
for off in range(0, len(mac), 20):
    ch = mac[off:off + 20]; n0 = len(flashlog)
    r = feat(4, 3, struct.pack('>HI', ID, off) + bytes([len(ch)]) + ch); assert r[0] == 0xA0
    print(f'write off {off} len {len(ch)} -> flash ops', flashlog[n0:])
print('flash 0x90000:', rd(0x90000, 6 + len(mac)).hex(' ')); assert rd(0x90000, 6) == struct.pack('<HI', ID, len(mac)) and rd(0x90006, len(mac)) == mac
r = feat(4, 0x81, struct.pack('>H', ID)); print('info:', r[:12].hex(' ')); assert r[8:12] == struct.pack('>I', len(mac))
r = feat(4, 0x83, struct.pack('>HI', ID, 4) + bytes([10])); print('read off 4 len 10:', r[:24].hex(' ')); assert r[13:23] == mac[4:14] and r[3] == 17
r = feat(4, 0x81, struct.pack('>H', 0x7777)); assert r[0] == 0xA2
r = feat(4, 1, struct.pack('>HI', 0x0200, 5000)); print('create 5000-byte macro on a store with only page 0 used:', hex(r[0])); assert r[0] == 0xA2
# --- play: type 0x10, id in f2:f3, f4 = plays
def run(n, stop=None):
    out = []; lastk = None
    for tck in range(n):
        if stop and tck == stop[0]: stop[1]()
        call(0xe32c); f = rd(FLM)[0]
        if f:
            out.append((tck, f, rd(KBM, 8).hex(), rd(0x200032DF, 6).hex(), rd(0x2000567C, 3).hex())); wr(FLM, b'\0')
            if f & 8: wr(0x200032DF, bytes(6))
        if rd(0x20005473)[0] == 0 and (not out or tck > out[-1][0] + 2): break
    return out, tck
setkey(4, 0, [0x10, 3, ID >> 8, ID & 0xFF, 2]); press(3); release(3)
print('runtime slot:', rd(0x20005473, 26).hex(' '))
out, end = run(2000)
for o in out: print('  tick %4d flag %02x kbd %s mouse(btn,wheel,x,y) %s cons/sys %s' % o)
print('2 plays ended at tick', end)
ticks = [o[0] for o in out]; assert ticks[:4] == [0, 5, 35, 40], ticks[:4]
setkey(4, 0, [0x11, 3, ID >> 8, ID & 0xFF, 0]); press(3); out, end = run(700, stop=(600, lambda: release(3)))
print('hold mode: events', len(out), 'slot freed at tick', end, 'last', out[-1]); assert 600 <= end <= 606
setkey(4, 0, [0x12, 3, ID >> 8, ID & 0xFF, 0]); press(3); release(3)
out, end = run(700, stop=(500, lambda: (press(3), release(3)))); print('toggle mode: stopped at', end); assert 500 <= end <= 506
r = feat(4, 2, struct.pack('>H', ID)); assert r[0] == 0xA0 and feat(4, 0x81, struct.pack('>H', ID))[0] == 0xA2
print('delete ok; free', hex(struct.unpack('<I', rd(0x20004FA8 + 4, 4))[0]), 'bitmap', rd(0x20004FA8 + 8, 12).hex())
print('ok')
# --- allocator behaviour for sizes above one page
r = feat(4, 1, struct.pack('>HI', 0x0300, 5000)); print('5000-byte create on blank store:', hex(r[0])); assert r[0] == 0xA2
for i in (1, 2, 3): assert feat(4, 1, struct.pack('>HI', i, 16))[0] == 0xA0
feat(4, 2, struct.pack('>H', 1)); feat(4, 2, struct.pack('>H', 2))
r = feat(4, 1, struct.pack('>HI', 0x0300, 5000)); print('5000-byte create with pages 0,1 free and 2 used:', hex(r[0]), 'bitmap', rd(0x20004FA8 + 8, 4).hex(), 'free', hex(struct.unpack('<I', rd(0x20004FA8 + 4, 4))[0]))
r = feat(4, 1, struct.pack('>HI', 4, 16)); e = [rd(0x20004FA8 + 0x19 + 12 * i, 12) for i in range(4)]
print('entries (id,size,addr):', [(hex(struct.unpack_from('<H', x)[0]), struct.unpack_from('<I', x, 2)[0], hex(struct.unpack_from('<I', x, 6)[0])) for x in e])
r = feat(3, 0, bytes([0, 4, 0, 1, 1, 1])); print('set key profile 0:', hex(r[0]))
print('ok2')

