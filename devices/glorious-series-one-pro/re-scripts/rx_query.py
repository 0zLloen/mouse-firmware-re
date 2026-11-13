#!/usr/bin/env python3
"""
Read-only: the stock version (0x81) and 0x83 queries sent to the 2.4 GHz receiver (258a:2031,
interface 2), once receiver-local (body[2] = 0x00) and once relayed to the mouse (body[2] = 0x02).
Receiver-local 0x83 is not a receiver command: it answers 0xA3 with the command echoed.
"""
import fcntl, os, sys, time
from pathlib import Path
node = None
for h in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
    u = (h / 'device/uevent').read_text().upper()
    if '0000258A:00002031' in u and os.path.realpath(h / 'device/..').endswith(':1.2'): node = f'/dev/{h.name}'
if node is None:
    sys.exit('no 258a:2031 receiver (interface 2) found')
print('receiver feature node:', node)
fd = os.open(node, os.O_RDWR)
ioc = lambda nr: (3 << 30) | (65 << 16) | (ord('H') << 8) | nr
def query(body, label):
    fcntl.ioctl(fd, ioc(6), bytearray(b'\0' + bytes(body).ljust(64, b'\0')))
    for i in range(20):
        time.sleep(0.025)
        buf = bytearray(65); fcntl.ioctl(fd, ioc(7), buf); r = bytes(buf[1:])
        if r[5] == body[5] and r[0] in (0xA1, 0xA0, 0xA2, 0xA3, 0xA4):
            print(f'{label}: after {(i + 1) * 25} ms -> {r[:20].hex(" ")}'); return r
    print(f'{label}: no matching reply, last {r[:20].hex(" ")}')
for target in (0x00, 0x02):
    query([0, 0, target, 0x06, 0x00, 0x81], f'version  target 0x{target:02X}')
    query([0, 0, target, 0x02, 0x00, 0x83], f'battery  target 0x{target:02X}')
