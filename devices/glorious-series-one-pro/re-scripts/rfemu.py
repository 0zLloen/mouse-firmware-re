#!/usr/bin/env python3
"""
Emulate the stock mouse RF feature path (CM2856 mouse v1.00.00) with Unicorn.
Needs unicorn and your own official CM2856_Mouse_USB_v1.00.00_20220810.hex; not run in CI.
Usage: python3 rfemu.py CM2856_Mouse_USB_v1.00.00_20220810.hex

 1) RX: RF ring entry (type-7 records) -> 0x13334 -> feature buffer 0x20004F28 + flag 0x200032D4
 2) completion: 0x151F8 (as the open-directional-dpi patch's stock_usb_finish_response does)
 3) TX: 0x15D74 called once per received downlink (ctrl[0x13]=1), records collected from 0x20007744
 4) the same TX path with the reply length clamped to 64 bytes (an emulator-only image patch)
Every printed line is backed by an assert; the run ends with "ok".
"""
import binascii, hashlib, struct, sys
from pathlib import Path
from unicorn import *
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool
STOCK_SHA256 = 'e50510e0eb213e69d62a088d6f5b792fb88b0f3dc56d3c9c21dd0ca8b6e892be'  # decoded image, same as ddpi's STOCK_SHA256
if len(sys.argv) != 2:
    sys.exit(__doc__)
img = compx_hex_tool.load_app_hex(Path(sys.argv[1]))[0]
if hashlib.sha256(img).hexdigest() != STOCK_SHA256:
    sys.exit(f'{sys.argv[1]}: not the official mouse v1.00.00 image; the addresses here are for that build only')
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0, 0x100000); uc.mem_write(0x6000, img)
uc.mem_map(0x20000000, 0x40000)
STOP = 0xFFF00; uc.mem_write(STOP, b'\x00\xbf\x00\xbf')
uc.hook_add(UC_HOOK_CODE, lambda u, a, s, _: u.emu_stop() if a & ~1 == STOP else None)
CTRL, RING, FEAT, STAGE, FLAG, TXAGG = 0x2000361C, 0x20007334, 0x20004F28, 0x20004F68, 0x200032D4, 0x20007744
rd = lambda a, n=1: bytes(uc.mem_read(a, n))
wr = uc.mem_write

def call(fn, *args, sp=0x2003F000):
    for r, v in zip([UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3], args): uc.reg_write(r, v)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.reg_write(UC_ARM_REG_LR, STOP | 1)
    uc.emu_start(fn | 1, STOP, count=100000)

def rx_entry(records):
    # ring entry = copy of radio packet: [len][ack][seq|flags][records...]; parser starts at entry[3]
    body = bytes([0, 0]) + b''.join(records)
    e = bytes([len(body)]) + body
    assert len(e) <= 65
    w = rd(CTRL + 2)[0]
    wr(RING + 65 * w, e.ljust(65, b'\0'))
    wr(CTRL + 2, bytes([(w + 1) % 16]))
    call(0x13334)

def rec7(off, data, more):  # hdr = more<<7 | len<<3 | 7, len = 1 (offset) + data bytes, max 15; more=0 commits
    assert len(data) <= 14
    return bytes([(more << 7) | ((len(data) + 1) << 3) | 7, off]) + data

def ddpi_req(op, seq=0x42):
    b = bytearray(64); b[0:3] = b'DP\x01'; b[4] = seq; b[5] = 0xDF; b[6] = op
    struct.pack_into('<H', b, 62, binascii.crc_hqx(bytes(b[:62]), 0xFFFF))
    return bytes(b)

def send_request(req, per_rec=14, recs_per_pkt=3):
    recs = [rec7(o, req[o:o + per_rec], int(o + per_rec < 64)) for o in range(0, 64, per_rec)]
    for i in range(0, len(recs), recs_per_pkt): rx_entry(recs[i:i + recs_per_pkt])

def finish():  # what the patch helper does: push {r4,r5,r6,lr}; r4=FEAT; r5=FLAG; bx 0x151F9
    sp = 0x2003EFF0
    wr(sp, struct.pack('<4I', 0, 0, 0, STOP | 1))
    uc.reg_write(UC_ARM_REG_R4, FEAT); uc.reg_write(UC_ARM_REG_R5, FLAG)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.emu_start(0x151F8 | 1, STOP, count=1000)

def drain_reply(max_polls=64):
    out = []
    for _ in range(max_polls):
        if rd(CTRL + 0xF)[0] == 0: break           # reply no longer pending
        wr(CTRL + 0x10, b'\0')                     # tick countdown elapsed
        wr(CTRL + 0x13, b'\1')                     # a downlink packet was received
        call(0x15D74)
        n = rd(TXAGG)[0]
        if n: out.append(rd(TXAGG + 1, n)); wr(TXAGG, b'\0' * 65); wr(CTRL + 0xB, b'\0')  # = flush 0x1513A
    return out

if __name__ == '__main__':
    wr(0x20003003, b'\1'); wr(0x200035DE, b'\0')   # wireless mode, USB not configured
    req = ddpi_req(0x01)
    # 1) request transport
    send_request(req)
    assert rd(FLAG)[0] == 1 and rd(FEAT, 64) == req, rd(FEAT, 64).hex()
    assert rd(STAGE, 64) == bytes(64)
    print('RX: 64-byte request copied intact to 0x20004F28, flag=1, staging cleared')
    # 1b) a second request while flag still set is dropped
    send_request(ddpi_req(0x02, 0x43))
    assert rd(FEAT, 64) == req and rd(STAGE, 64) != bytes(64)
    print('RX: second request while flag=1 dropped (staging keeps it, not cleared)')
    wr(STAGE, bytes(64))
    # 1c) only header bytes forwarded -> rest zero
    wr(FLAG, b'\0'); rx_entry([rec7(0, req[:8], 0)])
    assert rd(FEAT, 8) == req[:8] and rd(FEAT + 8, 56) == bytes(56)
    print('RX: partial forward (8 bytes) -> body[8..63] are zero, CRC at [62..63]=0000')
    # 2) reply transport: body[3] decides the length
    for st in (0x00, 0x06, 0x3A, 0xA1, 0xA2, 0xA4):
        resp = bytearray(range(0x80, 0xC0)); resp[3] = st
        wr(FEAT, bytes(resp)); wr(FEAT + 64, bytes(range(0x40)) + bytes(range(0x40, 0x80)))  # mark trailing RAM
        wr(FLAG, b'\1'); finish()
        assert rd(FLAG)[0] == 0 and rd(CTRL + 0xF)[0] == 1 and rd(CTRL + 0x10)[0] == 2
        recs = drain_reply()
        hdrs = [r[0] for r in recs]
        assert all(len(r) == 9 and h & 7 == 7 and (h >> 3) & 15 == 8 for r, h in zip(recs, hdrs))
        data = b''.join(r[1:] for r in recs)
        assert data == rd(FEAT, len(data))
        more = [h >> 7 for h in hdrs]
        assert more == [1] * (len(recs) - 1) + [0]
        print(f'TX: body[3]=0x{st:02x} -> {len(recs)} chunks x 8 = {len(data)} bytes '
              f'(body[3]+6={st + 6}); bytes past 64 come from 0x20004F68.. : {len(data) > 64}')
    assert len(drain_reply()) == 0
    # 3) single-byte type-7 record (hdr 0x0F, value v): v+8 > 64 cancels a pending reply and zeroes ctrl[8]
    resp = bytearray(64); resp[3] = 0xA1; wr(FEAT, bytes(resp)); wr(FLAG, b'\1'); finish()
    wr(CTRL + 8, b'\x05'); rx_entry([bytes([0x0F, 0x38])])
    assert rd(CTRL + 0xF)[0] == 1 and rd(CTRL + 8)[0] == 5
    rx_entry([bytes([0x0F, 0x39])])
    assert rd(CTRL + 0xF)[0] == 0 and rd(CTRL + 8)[0] == 0
    print('RX: 1-byte type-7 record 0x39..0xFF cancels reply (ctrl[0xF]=0) and resets downlink seq ctrl[8]=0')
    # 4) proposed fix (emulator-only image patch): clamp reply length to min(body[3]+6, 64) in 0x15D74
    HELPER = 0x1CF00
    def bl(at, to):
        off = to - (at + 4); S = (off >> 24) & 1; i1 = (off >> 23) & 1; i2 = (off >> 22) & 1
        h1 = 0xF000 | (S << 10) | ((off >> 12) & 0x3FF)
        h2 = 0xD000 | ((1 - (i1 ^ S)) << 13) | ((1 - (i2 ^ S)) << 11) | ((off >> 1) & 0x7FF)
        return struct.pack('<HH', h1, h2)
    # ldrb r1,[r6,#3]; adds r1,#6; cmp r1,#64; it hi; movhi r1,#64; bx lr
    wr(HELPER, struct.pack('<6H', 0x78F1, 0x1D89, 0x2940, 0xBF88, 0x2140, 0x4770))
    assert rd(0x15D8C, 8) == bytes.fromhex('f17800f10802891d') and rd(0x15DB4, 4) == bytes.fromhex('f178891d')
    wr(0x15D8C, bl(0x15D8C, HELPER) + bytes.fromhex('00f10802'))
    wr(0x15DB4, bl(0x15DB4, HELPER))
    uc.ctl_remove_cache(0x15D74, 0x15DC4)  # drop already-translated blocks of 0x15D74
    for st, n in ((0x03, 2), (0x3A, 8), (0xA1, 8), (0xA4, 8)):
        resp = bytearray(range(0x80, 0xC0)); resp[3] = st
        wr(FEAT, bytes(resp)); wr(FLAG, b'\1'); finish()
        recs = drain_reply()
        assert len(recs) == n and b''.join(r[1:] for r in recs) == bytes(resp[:8 * n])
        assert [r[0] >> 7 for r in recs] == [1] * (n - 1) + [0]
        print(f'clamped TX: body[3]=0x{st:02x} -> {n} chunks, last more=0')
    print('ok')
