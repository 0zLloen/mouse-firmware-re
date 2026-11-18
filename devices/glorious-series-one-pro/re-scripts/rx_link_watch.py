#!/usr/bin/env python3
"""
Read-only, through the 2.4 GHz receiver (258a:2031): poll the receiver-local link state (0x89) every
few milliseconds for <seconds> and log every drop (01 -> 00 -> 01), together with what the mouse was
doing, taken from the input reports on interface 0 (hidraw, 8 bytes: buttons, wheel, X, Y, pan, pad):
  moving     an X/Y report in the 300 ms before the sample
  scrolling  a wheel report in the 300 ms before the sample, no X/Y report
  buttons    a button change in the 300 ms before the sample, nothing else
  still      no report in the 300 ms before the sample
Nothing is relayed to the mouse and nothing is written: 0x89 is answered by the receiver itself.

Usage: rx_link_watch.py <seconds> [poll_ms]     (use the mouse normally, or hold it still)
"""
import fcntl, os, struct, sys, time
from pathlib import Path

WINDOW = 0.300
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
poll = (float(sys.argv[2]) if len(sys.argv) > 2 else 3) / 1000


def node(interface):
    return next((f'/dev/{h.name}' for h in sorted(Path('/sys/class/hidraw').glob('hidraw*'))
                 if '0000258A:00002031' in (h / 'device/uevent').read_text().upper()
                 and os.path.realpath(h / 'device/..').endswith(f':1.{interface}')), None)


if node(2) is None or node(0) is None:
    sys.exit('no 258a:2031 receiver (interfaces 0 and 2) found')
feat = os.open(node(2), os.O_RDWR)
inp = os.open(node(0), os.O_RDONLY | os.O_NONBLOCK)
ioc = lambda nr: (3 << 30) | (65 << 16) | (ord('H') << 8) | nr
req = bytearray(b'\0' + bytes([0, 0, 0, 0, 0, 0x89]).ljust(64, b'\0'))


def link():
    fcntl.ioctl(feat, ioc(6), req)
    for _ in range(20):
        buf = bytearray(65)
        fcntl.ioctl(feat, ioc(7), buf)
        if buf[1] == 0xA1 and buf[6] == 0x89:
            return buf[7]
        time.sleep(0.001)
    return None


last = dict(moving=-9.0, scrolling=-9.0, buttons=-9.0)
buttons = None
classes = ('moving', 'scrolling', 'buttons', 'still')
seconds = dict.fromkeys(classes, 0.0)
drops = {c: [] for c in classes}
bad = 0
t0 = prev_t = time.monotonic()
down_since = down_class = None
while (now := time.monotonic()) - t0 < secs:
    try:
        while True:
            r = os.read(inp, 64)
            b, wheel, x, y = r[0], r[1], *struct.unpack_from('<hh', r, 2)
            if x or y:
                last['moving'] = now
            if wheel or r[6]:
                last['scrolling'] = now
            if b != buttons:
                buttons, last['buttons'] = b, now
    except BlockingIOError:
        pass
    cls = next((c for c in classes[:3] if now - last[c] < WINDOW), 'still')
    seconds[cls] += now - prev_t
    prev_t = now
    state = link()
    if state is None:
        bad += 1
    elif state == 0 and down_since is None:
        down_since, down_class = now, cls
    elif state == 1 and down_since is not None:
        drops[down_class].append((down_since - t0, (now - down_since) * 1000))
        down_since = None
    time.sleep(poll)

print(f'{secs:.0f} s, poll {poll * 1000:.0f} ms, {bad} unanswered polls'
      + (f', link still down at the end (since {down_since - t0:.1f} s)' if down_since else ''))
for c in classes:
    d = [ms for _, ms in drops[c]]
    rate = f'{len(d) / seconds[c]:.2f}/s' if seconds[c] >= 1 else 'n/a'
    span = f', {min(d):.0f}..{max(d):.0f} ms, median {sorted(d)[len(d) // 2]:.0f} ms' if d else ''
    print(f'  {c:9} {seconds[c]:6.1f} s  {len(d):3d} drops  {rate}{span}')
