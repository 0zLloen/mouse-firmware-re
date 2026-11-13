#!/usr/bin/env python3
"""
Read-only: listen to the motion reports on interface 0 of the receiver (258a:2031) and of the
wired mouse (258a:2018) for SECONDS, then print per device the report rate while moving and the
summed up, down and sideways counts.
Usage: python3 wl_capture.py SECONDS
"""
import os, select, struct, sys, time
if len(sys.argv) != 2:
    sys.exit(__doc__)
nodes = {}
for h in sorted(os.listdir('/sys/class/hidraw')):
    d = f'/sys/class/hidraw/{h}/device'
    u = open(f'{d}/uevent').read().upper()
    if os.path.realpath(f'{d}/..').endswith(':1.0') and '0000258A' in u:
        for pid in ('2031', '2018'):
            if f'0000{pid}' in u: nodes[os.open(f'/dev/{h}', os.O_RDONLY | os.O_NONBLOCK)] = pid
stats = {fd: dict(t=[], up=0, down=0, side=0) for fd in nodes}
end = time.monotonic() + float(sys.argv[1])
while time.monotonic() < end:
    for fd in select.select(list(nodes), [], [], 0.1)[0]:
        while True:
            try: r = os.read(fd, 64)
            except BlockingIOError: break
            if len(r) < 6: continue
            dx, dy = struct.unpack_from('<hh', r, 2)
            if dx or dy:
                s = stats[fd]; s['t'].append(time.monotonic())
                s['up'] += -dy if dy < 0 else 0; s['down'] += dy if dy > 0 else 0; s['side'] += abs(dx)
for fd, pid in nodes.items():
    s = stats[fd]; t = s['t']
    if len(t) < 50: print(f'{pid}: {len(t)} motion reports'); continue
    iv = [(b - a) * 1000 for a, b in zip(t, t[1:])]; moving = [x for x in iv if x < 20]
    print(f'{pid}: {len(t)} motion reports, {len(moving) / (sum(moving) / 1000):.0f} reports/s while moving, '
          f'gaps > 2 ms: {sum(1 for x in iv if x > 2)} | up {s["up"]} down {s["down"]} up/down {s["up"] / max(1, s["down"]):.3f} sideways {s["side"]}')
