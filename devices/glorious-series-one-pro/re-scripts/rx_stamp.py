#!/usr/bin/env python3
"""
Read-only: the stock version query relayed through the 2.4 GHz receiver (258a:2031, interface 2)
with body[3] = 0x06, 0x0A and 0x0E. The receiver copies back body[3] + 6 bytes of the mouse's reply,
so body[3] = 0x0A is the shortest request that returns a patched mouse's "DP01" stamp at 12..15.
"""
import fcntl, os, sys, time
from pathlib import Path
node = None
for h in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
    u = (h / 'device/uevent').read_text().upper()
    if '0000258A:00002031' in u and os.path.realpath(h / 'device/..').endswith(':1.2'): node = f'/dev/{h.name}'
if node is None:
    sys.exit('no 258a:2031 receiver (interface 2) found')
fd = os.open(node, os.O_RDWR)
ioc = lambda nr: (3 << 30) | (65 << 16) | (ord('H') << 8) | nr
for ln in (0x06, 0x0A, 0x0E):
    fcntl.ioctl(fd, ioc(6), bytearray(b'\0' + bytes([0, 0, 2, ln, 0, 0x81]).ljust(64, b'\0')))
    for i in range(40):
        time.sleep(0.005)
        buf = bytearray(65); fcntl.ioctl(fd, ioc(7), buf); r = bytes(buf[1:])
        if r[0] != 0xA0 and r[5] == 0x81: break
    print(f'body[3]=0x{ln:02x}: {r[:24].hex(" ")}  [12:16]={r[12:16]!r}')
