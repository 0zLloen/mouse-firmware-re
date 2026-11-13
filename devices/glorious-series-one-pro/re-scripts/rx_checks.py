#!/usr/bin/env python3
"""
Read-only, through the 2.4 GHz receiver (258a:2031, interface 2), with the time to each answer:
 a) the receiver-local link state (0x89)
 b) an open-directional-dpi GET_CAPABILITIES without the relay envelope (body[2] = 0x01): the
    receiver must not relay it, and body[4] = seq != 0 keeps it away from the receiver-local handlers (0xA3)
 c) the stock version query relayed to the mouse (body[2] = 0x02)
"""
import fcntl, os, struct, sys, time
from pathlib import Path
node = next((f'/dev/{h.name}' for h in sorted(Path('/sys/class/hidraw').glob('hidraw*'))
             if '0000258A:00002031' in (h / 'device/uevent').read_text().upper() and os.path.realpath(h / 'device/..').endswith(':1.2')), None)
if node is None:
    sys.exit('no 258a:2031 receiver (interface 2) found')
fd = os.open(node, os.O_RDWR)
ioc = lambda nr: (3 << 30) | (65 << 16) | (ord('H') << 8) | nr
def crc16(d):
    c = 0xFFFF
    for b in d:
        c ^= b << 8
        for _ in range(8): c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c
def xfer(body, label, settle=lambda r: r[0] != 0xA0):
    fcntl.ioctl(fd, ioc(6), bytearray(b'\0' + bytes(body).ljust(64, b'\0')))
    t0 = time.monotonic()
    for _ in range(100):
        time.sleep(0.002)
        buf = bytearray(65); fcntl.ioctl(fd, ioc(7), buf); r = bytes(buf[1:])
        if settle(r): break
    print(f'{label:46} {(time.monotonic() - t0) * 1000:5.1f} ms  {r[:16].hex(" ")}')
    return r
# a) receiver-local link state (target 0, group 0, cmd 0x89)
xfer([0, 0, 0, 0, 0, 0x89], 'a) receiver link state (local)')
# b) open-directional-dpi GET_CAPABILITIES as specified (byte 2 = 0x01): must stay local and answer 0xA3
req = bytearray(64); req[0:3] = b'DP\x01'; req[4] = 0x47; req[5] = 0xDF; req[6] = 0x01
struct.pack_into('<H', req, 62, crc16(req[:62]))
xfer(req, 'b) ODD GET_CAPABILITIES, byte2=0x01 (not relayed)')
# c) stock version query relayed to the mouse (target 2): timing, 16 bytes back
xfer([0, 0, 2, 0x06, 0x00, 0x81], 'c) stock version query via radio (target 0x02)')
