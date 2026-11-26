#!/usr/bin/env python3
"""
Host-side controller and live protocol decoder for the Compx nRF52 2.4 GHz sniffer
(platforms/compx-nrf52/sniffer/src/main.c on an nRF52840 USB Dongle, PCA10059).

Standard library only on Linux (raw termios on /dev/ttyACM*; no pyserial required).
Can also read your unit's RF ID directly from the attached 258a:2031 receiver (0x8A) or a
private FICR dump without ever printing it to stdout.

Subcommands / modes:
  --selftest               Run offline self-test on synthetic PKT lines (no hardware needed)
  --port /dev/ttyACM0 ...  Connect to the sniffer dongle and decode packets live:
      --id <hex32> | --id-from-receiver | --ficr-bin <path>
      --mode follow | connect | ch
      --ch <0..100>        Fixed channel (default 3 = 2403 MHz for connect/follow)
      --seconds <N>        Stop after N seconds (default: until Ctrl-C)
      --raw-out <file>     Save raw PKT lines to a file (keep private: connect packets carry RF ID)
      --show-rf-id         Do not redact the 4-byte RF ID in connect requests

Examples:
  python3 compx_sniffer.py --selftest
  python3 compx_sniffer.py --port /dev/ttyACM0 --id-from-receiver --mode connect --ch 3
  python3 compx_sniffer.py --port /dev/ttyACM0 --id-from-receiver --mode follow --seconds 30
"""

import argparse
import fcntl
import os
import select
import struct
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path

PAIR_CHANNELS = (3, 49, 78)
HOP_TABLE = bytes.fromhex("331b4c0430184908341a4d022f164807")

TIMING_NAMES = {0x00: "00(hold)", 0x40: "40(-50us)", 0x80: "80(hold)", 0xC0: "C0(-50us)"}
UPLINK_TYPES = {1: "mouse", 2: "kbd", 3: "consumer", 4: "sys", 5: "notif", 6: "t6", 7: "relay_rsp"}
DOWNLINK_TYPES = {0: "hop_row", 4: "sleep", 7: "relay_req"}


@dataclass
class RawPacket:
    ts_us: int
    sof_us: int
    ch: int
    rssi_dbm: int
    crc_ok: bool
    length: int
    payload: bytes

    @property
    def sof_offset_us(self) -> int:
        return (self.ts_us - self.sof_us) & 0xFFFFFFFF if self.ts_us >= self.sof_us else (self.ts_us - self.sof_us) % 1000


def parse_pkt_line(line: str) -> RawPacket | None:
    parts = line.strip().split()
    if not parts or parts[0] != "PKT" or len(parts) < 7:
        return None
    ts_us = int(parts[1])
    sof_us = int(parts[2])
    ch = int(parts[3])
    rssi_dbm = int(parts[4])
    crc_ok = parts[5] == "1"
    length = int(parts[6])
    payload = bytes.fromhex(parts[7]) if len(parts) > 7 else b""
    return RawPacket(ts_us, sof_us, ch, rssi_dbm, crc_ok, length, payload)


def parse_records(data: bytes, is_downlink: bool) -> list[str]:
    out = []
    pos = 0
    names = DOWNLINK_TYPES if is_downlink else UPLINK_TYPES
    while pos < len(data):
        hdr = data[pos]
        more = (hdr >> 7) & 1
        rlen = (hdr >> 3) & 0xF
        rtype = hdr & 7
        body = data[pos + 1 : pos + 1 + rlen]
        pos += 1 + rlen
        tname = names.get(rtype, f"t{rtype}")
        if not is_downlink and rtype == 1 and len(body) >= 7:
            btn, whl = body[0], struct.unpack_from("<b", body, 1)[0]
            dx, dy = struct.unpack_from("<hh", body, 2)
            out.append(f"mouse(btn={btn:02x},w={whl},dx={dx},dy={dy})")
        elif not is_downlink and rtype == 5 and len(body) >= 2:
            out.append(f"notif({body.hex()})")
        elif rtype == 7:
            m = "+" if more else "."
            if is_downlink and len(body) >= 1:
                out.append(f"dl7{m}(off={body[0]:02x},{body[1:].hex()})")
            else:
                out.append(f"ul7{m}({body.hex()})")
        else:
            out.append(f"{tname}({body.hex()})")
        if rtype not in names:
            break
    return out


class StreamDecoder:
    """Stateful decoder pairing uplink packets with ACKs (~70-350 us later) and tracking anomalies."""

    def __init__(self, show_rf_id: bool = False):
        self.show_rf_id = show_rf_id
        self.last_uplink: RawPacket | None = None
        self.last_dl_seq: int | None = None
        self.stats = {
            "total": 0,
            "crc_bad": 0,
            "connect_req": 0,
            "connect_ans": 0,
            "uplink": 0,
            "ack": 0,
            "wrap_dup": 0,
            "near_sof_lt70": 0,
        }

    def decode(self, pkt: RawPacket) -> str:
        self.stats["total"] += 1
        sof_off = (pkt.ts_us - pkt.sof_us) % 1000
        if not pkt.crc_ok:
            self.stats["crc_bad"] += 1
            return f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm CRC_BAD len={pkt.length} {pkt.payload.hex()}"

        # Connect channels (2403 / 2449 / 2478 MHz)
        if pkt.ch in PAIR_CHANNELS:
            if pkt.length == 5 and pkt.payload[:1] == b"\x20":
                self.stats["connect_req"] += 1
                id_s = pkt.payload[1:5].hex() if self.show_rf_id else "<redacted>"
                return f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm CONN_REQ id={id_s}"
            if pkt.length == 3 and pkt.payload[0] < 4:
                self.stats["connect_ans"] += 1
                row = pkt.payload[0]
                frame_us = struct.unpack_from("<H", pkt.payload, 1)[0]
                return (
                    f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm "
                    f"CONN_ANS row={row} hop0={HOP_TABLE[4 * row]} frame={frame_us}us"
                )

        # Linked traffic: distinguish ACK from uplink by turnaround gap from last uplink on same channel
        is_ack = False
        gap_us = None
        if self.last_uplink is not None and self.last_uplink.ch == pkt.ch:
            dt = (pkt.ts_us - self.last_uplink.ts_us) & 0xFFFFFFFF
            # Air time of uplink is (9 + len) * 4 us (44..292 us), ACK starts ~70 us after end -> ~110..420 us
            if 80 <= dt <= 450:
                is_ack = True
                gap_us = dt

        if pkt.length == 0:
            self.last_uplink = pkt
            self.stats["uplink"] += 1
            return f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm UL_EMPTY(len=0)"

        if pkt.length < 2:
            return f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm SHORT len={pkt.length} {pkt.payload.hex()}"

        b1, b2 = pkt.payload[0], pkt.payload[1]
        recs = parse_records(pkt.payload[2:], is_downlink=is_ack)
        rec_s = " ".join(recs) if recs else "keepalive"

        if is_ack:
            self.stats["ack"] += 1
            dl_seq = b2 & 0x3F
            timing = b2 & 0xC0
            flags = []
            if pkt.length > 2:
                if self.last_dl_seq == 62 and dl_seq == 0:
                    self.stats["wrap_dup"] += 1
                    flags.append("WRAP_62->0_DUP!")
                self.last_dl_seq = dl_seq
            self.last_uplink = None
            flag_s = (" " + " ".join(flags)) if flags else ""
            return (
                f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us (+{gap_us:3d}us) ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm "
                f"ACK  exp_ul={b1:3d} dl={dl_seq:2d} tm={TIMING_NAMES[timing]} {rec_s}{flag_s}"
            )

        self.stats["uplink"] += 1
        self.last_uplink = pkt
        flags = []
        if sof_off < 70:
            self.stats["near_sof_lt70"] += 1
            flags.append("IN_BLIND_WINDOW(<70us)!")
        flag_s = (" " + " ".join(flags)) if flags else ""
        return (
            f"ts={pkt.ts_us:10d} sof+{sof_off:3d}us          ch={pkt.ch:2d} {pkt.rssi_dbm:4d}dBm "
            f"UL   ul={b1:3d} exp_dl={b2 & 0x3F:2d} {rec_s}{flag_s}"
        )


def read_rf_id_from_receiver() -> int:
    """Read 4-byte RF ID from attached 258a:2031 receiver via read-only local command 0x8A."""
    node = next(
        (
            f"/dev/{h.name}"
            for h in sorted(Path("/sys/class/hidraw").glob("hidraw*"))
            if "0000258A:00002031" in (h / "device/uevent").read_text().upper()
            and os.path.realpath(h / "device/..").endswith(":1.2")
        ),
        None,
    )
    if node is None:
        raise RuntimeError("No 258a:2031 receiver (interface 2) found for --id-from-receiver")
    ioc = lambda nr: (3 << 30) | (65 << 16) | (ord("H") << 8) | nr  # noqa: E731
    fd = os.open(node, os.O_RDWR)
    try:
        req = bytearray(65)
        req[4], req[6] = 0x04, 0x8A
        fcntl.ioctl(fd, ioc(6), req)
        time.sleep(0.015)
        buf = bytearray(65)
        fcntl.ioctl(fd, ioc(7), buf)
        if buf[1] != 0xA1:
            raise RuntimeError(f"Receiver 0x8A returned status 0x{buf[1]:02X}")
        return struct.unpack_from("<I", buf, 7)[0]
    finally:
        os.close(fd)


def open_serial_raw(port: str) -> int:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0  # iflag
    attrs[1] = 0  # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0  # lflag (raw, no echo)
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def selftest() -> int:
    dec = StreamDecoder(show_rf_id=False)
    lines = [
        # Connect request + answer on ch 3 (2403 MHz)
        "PKT 10000 9500 3 -42 1 5 2044332211",
        "PKT 10296 9500 3 -38 1 3 01e803",
        # Linked keep-alive + ACK on hop row 1 ch 0 (0x30 = 48)
        "PKT 20520 20000 48 -44 1 2 0000",
        "PKT 20634 20000 48 -39 1 2 0080",
        # Mouse motion uplink + ACK carrying downlink seq 62 (0x3E | timing 0x80 = 0xBE)
        "PKT 21520 21000 48 -43 1 11 00004101000400ffff0000",
        "PKT 21670 21000 48 -39 1 12 01be4f080000000000000000",
        # Next frame: duplicated seq 0 (0x00 | timing 0x80 = 0x80) -> wrap bug!
        "PKT 22520 22000 48 -43 1 2 0100",
        "PKT 22634 22000 48 -39 1 12 01804f080000000000000000",
        # Keep-alive drifted into <70 us blind window after SOF
        "PKT 23050 23000 48 -45 1 2 0101",
    ]
    out = [dec.decode(parse_pkt_line(ln)) for ln in lines]
    assert "CONN_REQ id=<redacted>" in out[0], out[0]
    assert "CONN_ANS row=1 hop0=48 frame=1000us" in out[1], out[1]
    assert "UL   ul=  0 exp_dl= 0 keepalive" in out[2], out[2]
    assert "ACK  exp_ul=  0 dl= 0 tm=80(hold)" in out[3] and "(+114us)" in out[3], out[3]
    assert "mouse(btn=01,w=0,dx=4,dy=-1)" in out[4], out[4]
    assert "dl=62" in out[5] and "dl7.(off=08," in out[5], out[5]
    assert "WRAP_62->0_DUP!" in out[7], out[7]
    assert "IN_BLIND_WINDOW(<70us)!" in out[8], out[8]
    assert dec.stats["connect_req"] == 1 and dec.stats["connect_ans"] == 1
    assert dec.stats["wrap_dup"] == 1 and dec.stats["near_sof_lt70"] == 1
    print("compx_sniffer.py selftest passed (9/9 frames decoded)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="Run offline decoder self-test")
    ap.add_argument("--port", default="/dev/ttyACM0", help="CDC-ACM device of the nRF52840 sniffer dongle")
    ap.add_argument("--id", help="BASE1 RF ID as hex u32 (e.g. 0x11223344)")
    ap.add_argument("--id-from-receiver", action="store_true", help="Read RF ID via read-only 0x8A from 258a:2031")
    ap.add_argument("--ficr-bin", type=Path, help="Read DEVICEID[0] from a private FICR dump")
    ap.add_argument("--mode", choices=("follow", "connect", "ch"), default="follow")
    ap.add_argument("--ch", type=int, default=3, help="Channel (0..100; connect channels are 3, 49, 78)")
    ap.add_argument("--seconds", type=float, default=0.0, help="Capture duration in seconds (0 = until Ctrl-C)")
    ap.add_argument("--raw-out", type=Path, help="Optional file to save raw PKT lines (keep private)")
    ap.add_argument("--show-rf-id", action="store_true", help="Print RF ID in connect requests (do NOT share)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    rf_id = None
    if args.id_from_receiver:
        rf_id = read_rf_id_from_receiver()
    elif args.ficr_bin:
        rf_id = struct.unpack_from("<I", args.ficr_bin.read_bytes(), 0x60)[0]
    elif args.id:
        rf_id = int(args.id, 16)

    fd = open_serial_raw(args.port)
    raw_f = args.raw_out.open("w", encoding="ascii") if args.raw_out else None
    dec = StreamDecoder(show_rf_id=args.show_rf_id)

    def send_cmd(cmd: str) -> None:
        os.write(fd, (cmd + "\n").encode("ascii"))
        time.sleep(0.05)

    try:
        if rf_id is not None:
            send_cmd(f"id {rf_id:08x}")
        if args.mode == "follow":
            send_cmd(f"follow {args.ch}")
        else:
            send_cmd(f"ch {args.ch}")

        buf = b""
        t0 = time.monotonic()
        while True:
            if args.seconds > 0 and (time.monotonic() - t0) >= args.seconds:
                break
            r, _, _ = select.select([fd], [], [], 0.1)
            if not r:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("ascii", errors="replace").strip()
                if not line:
                    continue
                if raw_f is not None:
                    raw_f.write(line + "\n")
                pkt = parse_pkt_line(line)
                if pkt is not None:
                    print(dec.decode(pkt))
                else:
                    # Redact base1 in STATUS lines unless --show-rf-id is set
                    if line.startswith("STATUS") and not args.show_rf_id:
                        parts = [p if not p.startswith("base1=") else "base1=<redacted>" for p in line.split()]
                        line = " ".join(parts)
                    print(f"[DONGLE] {line}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            send_cmd("stop")
        except OSError:
            pass
        os.close(fd)
        if raw_f is not None:
            raw_f.close()
        print("Stats:", dec.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
