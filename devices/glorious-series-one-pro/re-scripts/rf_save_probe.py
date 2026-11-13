#!/usr/bin/env python3
"""
NOT read-only: sends one open-directional-dpi SAVE_FLASH through the 2.4 GHz receiver, which
rewrites the mouse's 0x7E000 profile page with the live profile (same as odd_profile.py save),
and prints every change of the receiver's answer for 1.5 s.
Usage: python3 rf_save_probe.py /path/to/open-directional-dpi   (uses its tools/odd_profile.py)
"""
import sys, time
from pathlib import Path
if len(sys.argv) != 2:
    sys.exit(__doc__)
sys.path.insert(0, str(Path(sys.argv[1]) / 'tools'))
import odd_profile as op
dev = op.find_device(None)
assert dev.relay
dev.seq = 5 + (dev.seq - 4) % 251
req = bytearray(64); req[0:3], req[4], req[5], req[6] = b"DP\x01", dev.seq, op.NAMESPACE, op.SAVE_FLASH
import struct; struct.pack_into("<H", req, 62, op.crc16(req[:62])); req[2], req[3] = 0x02, 0x3A
t0 = time.monotonic(); dev.set(bytes(req)); last = None
while time.monotonic() - t0 < 1.5:
    r = dev.get(); key = r[:8]
    if key != last: print(f'{(time.monotonic()-t0)*1000:7.1f} ms  {r[:16].hex(" ")}'); last = key
    time.sleep(0.002)
