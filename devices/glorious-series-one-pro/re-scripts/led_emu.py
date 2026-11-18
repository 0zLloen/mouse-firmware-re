#!/usr/bin/env python3
"""
LED system checks on the stock mouse image in Unicorn (firmware-subsystems.md 2). Needs unicorn; not run
in CI. Output is derived from vendor firmware: keep it out of the repository.

  python3 led_emu.py CM2856_Mouse_USB_v1.00.00_20220810.hex

.data is initialised by the image's own scatterload (0x64B0). It prints: the PWM register writes of the
LED init 0xD91C and of LED-off 0x18F94, what the engine 0xB328 draws for low battery, charging, the
battery display, the DPI colour and the pairing blink, the framebuffer over 400 engine ticks for every
stored effect (all dark), the five button chords through 0x14244 with the config bytes each one changes,
the group 2 host commands through the dispatcher, direct-colour mode 0xA0, and the writes of the reload
0xDA58 for effects 0 and 9.
"""
import sys, struct
from pathlib import Path
from unicorn import *
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool
H = sys.argv[1]
img = compx_hex_tool.load_app_hex(Path(H))[0]
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0, 0x100000); uc.mem_write(0x6000, img)
uc.mem_map(0x20000000, 0x40000)
uc.mem_map(0x40000000, 0x100000); uc.mem_map(0x50000000, 0x1000); uc.mem_map(0x10000000, 0x2000); uc.mem_map(0xE0000000, 0x100000)
STOP = 0xFFF00; uc.mem_write(STOP, b'\x00\xbf\x00\xbf')
rd = lambda a, n=1: bytes(uc.mem_read(a, n))
wr = uc.mem_write
r32 = lambda a: struct.unpack('<I', rd(a, 4))[0]
def call(fn, *args, sp=0x2003F000, count=2000000):
    for r, v in zip([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3], args): uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.reg_write(UC_ARM_REG_LR, STOP | 1)
    uc.emu_start(fn | 1, STOP, count=count)
    assert uc.reg_read(UC_ARM_REG_PC) & ~1 == STOP, hex(uc.reg_read(UC_ARM_REG_PC))
    return uc.reg_read(UC_ARM_REG_R0)
uc.reg_write(UC_ARM_REG_SP, 0x2003F000); uc.emu_start(0x64B1, 0x64C8)
# ---- init, engine, chords ----
log=[]
def hw(u, access, addr, size, value, _): log.append((addr, value))
uc.hook_add(UC_HOOK_MEM_WRITE, hw, begin=0x40000000, end=0x50001000)
CFG=0x200038C8
wr(CFG, img[0x1a3b8-0x6000:0x1a3b8-0x6000+0x266]); wr(0x20004B30, rd(CFG+0x5C,120))
def show(t):
    print(t); 
    for a,v in log: print(f'   [{a:#x}] <- {v:#x}')
    log.clear()
call(0xD91C); show('LED init 0xD91C')
print('init flag', rd(0x2000324C,1).hex(), 'copy 0x20004BF9:', rd(0x20004BF9,26).hex(' '))
print('fb', rd(0x20004D60,16).hex(' '))
FB=0x20004D60
def tick(): call(0xB328)
def fb(): return rd(FB+5,3).hex(' ')+' | '+rd(FB+9,3).hex(' ')
wr(0x20003076, b'\x50'); wr(0x20003077,b'\0')   # battery 80 %, not charging
for e in list(range(0,8))+[9]:
    wr(0x20004BF9, bytes([e])); wr(CFG+0x11, bytes([e])); call(0xDA58,1)
    seen=set()
    for i in range(400):
        call(0xD9EC); tick(); seen.add(fb())
    print('effect', e, 'fb values over 400 engine ticks:', seen)
# PWM handler
wr(FB+5, bytes([1,2,3])); wr(FB+9, bytes([4,5,6])); call(0xA3D0, 0)
print('seq0', rd(0x20003270,8).hex(' '), 'seq1', rd(0x20003278,8).hex(' '))
# status: low battery
for lvl,chg in ((3,0),(3,1),(40,1),(70,1),(95,1)):
    wr(0x20003076, bytes([lvl])); wr(0x20003077,bytes([chg])); wr(0x2000307A,b'\0'); wr(0x20003084,bytes(8)); wr(0x2000324F,b'\0'); wr(0x20003251,b'\0')
    out=[]
    for i in range(3000):
        call(0x18BA4, count=5000000) if False else None
        # advance 1 ms counters by hand: 0x20003084 += 5 per engine tick (engine runs every 5 ms)
        wr(0x20003084, struct.pack('<I', r32(0x20003084)+5)); tick()
        v=fb()
        if not out or out[-1][1]!=v: out.append((i*5,v))
    print('level',lvl,'charging',chg, out[:14], '...' if len(out)>14 else '')
# battery display action flag
wr(0x20003076, b'\x50'); wr(0x20003077,b'\0'); wr(0x20003250,b'\1')
for lvl in (10,40,70,95):
    wr(0x20003076, bytes([lvl])); wr(0x20003251,b'\1'); tick(); print('battery display on-phase level',lvl, fb())
wr(0x20003250,b'\0')
# DPI indicator
wr(0x20003023,b'\1'); wr(0x20003010,b'\0\0')
for st in range(4):
    wr(CFG+0x31, bytes([st])); tick(); print('lift flag set, stage', st, fb())
wr(0x20003023,b'\0'); tick(); print('lift flag clear', fb())
# pairing blink
wr(0x20003630,b'\1'); wr(0x20003650,b'\x10\0'); tick(); print('0x20003630=1 t=16', fb()); wr(0x20003650,struct.pack('<H',600)); tick(); print('t=600', fb()); wr(0x20003630,b'\0')
# direct mode through the host handler
def host(grp, cmd, payload=b''):
    b=bytearray(64); b[4]=grp; b[5]=cmd; b[6:6+len(payload)]=payload; wr(0x20004F28, bytes(b)); wr(0x200032D4,b'\1')
    wr(0x20003003, b'\1'); wr(0x200035DE, b'\0')
    call(0x151AC); return rd(0x20004F28,64)
r=host(2,3,bytes([10,20,30,40,50,60])); print('grp2 cmd3 reply', r[:12].hex(' '), 'copy[0]', rd(0x20004BF9,1).hex())
for br in (0xFF,0x80,0):
    wr(CFG+0xA, bytes([br])); tick(); print('direct, brightness', hex(br), fb())
wr(0x20003076, b'\x03'); tick(); print('direct with battery 3 %:', fb()); wr(0x20003076, b'\x50')
call(0xA3D0,0); print('seq0', rd(0x20003270,6).hex(' '), 'seq1', rd(0x20003278,6).hex(' '))
log.clear(); call(0x18F94); show('LED off 0x18F94'); print('init flag', rd(0x2000324C,1).hex())

# ---- host commands ----
CFG=0x200038C8
DEF=img[0x1a3b8-0x6000:0x1a3b8-0x6000+0x266]
def reset(profile=0):
    wr(CFG, DEF); wr(CFG+7, bytes([profile])); P=CFG+200*profile
    wr(0x20004B30, rd(P+0x5C,120)); wr(0x2000304C, bytes(0x20)); wr(0x2000324C, b'\1'); call(0xDA58,1); wr(0x20003000, bytes(8))
    return P
def diff(P, before):
    now=rd(CFG,0x266); return [(hex(i), f'{before[i]:02x}->{now[i]:02x}') for i in range(0x266) if now[i]!=before[i]]
print('speed table 0x1A6F2:', [struct.unpack_from('<3H', img, 0x1a6f2-0x6000+6*e) for e in range(9)])
names={0:'L',1:'R',2:'M',3:'Back',4:'Fwd',5:'DPI'}
for chord in ((0,2,5),(1,2,5),(2,3,5),(2,4,5),(0,1,2)):
  for prof in (0,2):
    P=reset(prof); before=rd(CFG,0x266)
    print('chord', '+'.join(names[b] for b in chord), 'profile', prof+1)
    for rep in range(3 if chord!=(1,2,5) else 6):
        for b in chord: call(0x14244, 1, b)
        print('   press', rep+1, 'cfg diffs (offset from 0x200038C8):', diff(P,before), '| copy', rd(0x20004BF9,8).hex(' '), '| speed idx', rd(0x20003051,1).hex(), 'save flags', rd(0x20003001,1).hex(), r32(0x20003004)&0xffff, 'reset-hold', rd(0x2000305E,3).hex())
        for b in chord: call(0x14244, 0, b)
        before2=rd(CFG,0x266)
    print('   after release reset-hold', rd(0x2000305E,3).hex())
# host commands
def host(grp, cmd, payload=b''):
    b=bytearray(64); b[4]=grp; b[5]=cmd; b[6:6+len(payload)]=payload; wr(0x20004F28, bytes(b)); wr(0x200032D4,b'\1')
    wr(0x20003003, b'\1'); wr(0x200035DE, b'\0')
    call(0x151AC); return rd(0x20004F28,64)
P=reset(0)
for p in (1,2,3): print('get effect profile',p, host(2,0x80,bytes([p,0]))[:32].hex(' '))
print('get effect profile 0', host(2,0x80,bytes([0,0]))[:12].hex(' '))
before=rd(CFG,0x266)
r=host(2,0x00,bytes([2,0, 4,0x11,0x22])+bytes(range(0x30,0x45))); print('set effect p2 z0 reply', r[:8].hex(' '), diff(P,before)[:6], '... n=',len(diff(P,before)), 'save', rd(0x20003001,1).hex())
print('  readback', host(2,0x80,bytes([2,0]))[:32].hex(' '), 'active copy', rd(0x20004BF9,8).hex(' '))
before=rd(CFG,0x266)
r=host(2,0x00,bytes([1,0xFF, 6,0x11,0x22])+bytes(range(0x30,0x45))); print('set effect p1 z=FF', diff(P,before)[:4], 'n=',len(diff(P,before)), 'active copy', rd(0x20004BF9,8).hex(' '), 'state6', rd(r32(0x200032A4+24),15).hex(' '))
print('get dpi colours p1', host(2,0x81,bytes([1]))[:26].hex(' '))
before=rd(CFG,0x266); host(2,0x01,bytes([1])+bytes(range(1,19))); print('set dpi colours p1', diff(P,before)[0], '..', diff(P,before)[-1])
before=rd(CFG,0x266); r=host(2,0x02,bytes([0,0x77])); print('set brightness idx0', r[:8].hex(' '), diff(P,before), 'save', rd(0x20003001,1).hex())
wr(0x20003001,b'\0'); before=rd(CFG,0x266); r=host(2,0x82,bytes([0,0x00])); print('0x82 idx0 body[7]=0 reply', r[:8].hex(' '), diff(P,before), 'save', rd(0x20003001,1).hex())
before=rd(CFG,0x266); r=host(2,0x82,bytes([1,0x33])); print('0x82 idx1 body[7]=33 reply', r[:8].hex(' '), diff(P,before))
print('0x83', host(2,0x83)[:8].hex(' '), '0x04', host(2,0x04)[:8].hex(' '))
wr(0x20003076,b'\x42'); wr(0x20003077,b'\1'); print('grp0 0x83', host(0,0x83)[:9].hex(' '))
print('grp0 0x03 body[6]=0 ->', host(0,0x03,b'\0')[:8].hex(' '), '0x2000324E', rd(0x2000324E,1).hex())
# direct mode suppresses status
host(2,3,bytes([10,20,30,40,50,60])); wr(0x20003076,b'\x03'); wr(0x20003077,b'\0'); wr(0x20003023,b'\1'); call(0xB328)
print('direct + battery 3% + lift: fb', rd(0x20004D65,3).hex(' '), '|', rd(0x20004D69,3).hex(' '))
# effect 0xA0 or 8 through reload
for e in (8,):
    P=reset(0); wr(P+0x11, bytes([e]))
    w=[]
    h=uc.hook_add(UC_HOOK_MEM_WRITE, lambda u,a,ad,s,v,_: w.append((ad,v)), begin=0, end=0x100000)
    call(0xDA58,1); uc.hook_del(h); print('reload with effect',e,'writes below 0x100000:', [(hex(a),v) for a,v in w])

# ---- direct mode, reload ----
CFG=0x200038C8
wr(CFG, img[0x1a3b8-0x6000:0x1a3b8-0x6000+0x266]); wr(0x2000324C,b'\1')
def host(grp, cmd, payload=b''):
    b=bytearray(64); b[4]=grp; b[5]=cmd; b[6:6+len(payload)]=payload; wr(0x20004F28, bytes(b)); wr(0x200032D4,b'\1')
    wr(0x20003003, b'\1'); wr(0x200035DE, b'\0'); call(0x151AC); return rd(0x20004F28,64)
call(0xDA58,1)
host(2,3,bytes([10,20,30,40,50,60])); wr(0x20003076,b'\x03'); wr(0x20003077,b'\0'); wr(0x20003023,b'\1'); call(0xB328)
print('direct + battery 3% + lift: fb', rd(0x20004D65,3).hex(' '), '|', rd(0x20004D69,3).hex(' '), 'P+0x11', rd(CFG+0x11,1).hex(), 'copy+26', rd(0x20004BF9+26,1).hex())
wr(0x200035DE,b'\1'); wr(CFG+0xB,b'\x80'); call(0xB328); print('usb flag=1, cfg[0xB]=0x80: fb', rd(0x20004D65,3).hex(' '))
for e in (0,9):
    wr(CFG+0x11, bytes([e])); w=[]
    h=uc.hook_add(UC_HOOK_MEM_WRITE, lambda u,a,ad,s,v,_: w.append((ad,v)), begin=0, end=0x100000)
    call(0xDA58,1); uc.hook_del(h); print('reload with effect',e,'writes below 0x100000:', [(hex(a),v) for a,v in w])
