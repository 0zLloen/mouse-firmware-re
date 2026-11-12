#!/usr/bin/env python3
"""
Compx nRF52840 Firmware Image Tool (offline Intel HEX decoder / encoder / safety audit)
For Glorious Forge Series One Pro Wireless (CM2852-BAMF / CM2856)

The vendor .hex files are plain Intel HEX. The 0x55 XOR exists only on the USB
wire (Program / VerifyByAddress payloads, see compx_usb_flasher.py), never in the file.

Subcommands:
  decode   <hex> -o <bin>   Intel HEX -> raw binary (lowest address must be the app base)
  encode   <bin> -o <hex>   raw binary -> Intel HEX at the app base
  audit    <hex>            decode and run the safety checks
  selftest <hex>            audit + bit-exact encode/decode roundtrip

Safety checks (audit_binary_safety):
  1. Image starts exactly at the application base (0x6000 mouse, 0x7000 receiver),
     where the bootloader looks for the vector table.
  2. Image ends at or below 0x7E000 (bootloader at 0x0..base, stock pairing/profile pages
     at 0x88000+ stay untouched by Program; the Erase extent is defined by the bootloader).
  3. Initial SP is 8-byte aligned and inside SRAM (0x20000000 < SP <= 0x2003FFF0).
  4. Reset_Handler is a Thumb address after the 0x100-byte vector table and inside the image.
  5. Heuristic only: the image contains the re-entry magic value 0xA55AA55A as a 32-bit literal.
     The SRAM address 0x2003FFFC is not checked: GCC emits it as base 0x2003F000 + offset 0xFFC.
"""

import argparse
import hashlib
import struct
import sys
from pathlib import Path

MOUSE_APP_BASE = 0x00006000
RECEIVER_APP_BASE = 0x00007000
TARGETS = {"mouse": MOUSE_APP_BASE, "receiver": RECEIVER_APP_BASE}
MOUSE_SAFE_LIMIT = 0x0007E000
VECTOR_TABLE_SIZE = 0x100  # nRF52840: 16 system + 48 IRQ entries
BOOTLOADER_MAGIC_VAL = 0xA55AA55A

# record type -> required byte count (None = any)
_RECORD_LEN = {0x00: None, 0x01: 0, 0x02: 2, 0x03: 4, 0x04: 2, 0x05: 4}


def parse_compx_hex(hex_src):
    """Parse Intel HEX from a Path or from HEX text.

    Returns (bin, min_addr, max_addr, sla_entry, record_count). Gaps between records
    are filled with 0xFF (erased flash). Raises ValueError on any malformed input:
    bad lines, bad lengths or checksums, unknown record types, overlapping data,
    missing EOF or data after EOF.
    """
    text = hex_src.read_text(encoding="ascii") if isinstance(hex_src, Path) else hex_src
    ext_addr = 0
    sla_entry = None
    mem = {}
    records = 0
    eof = False

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if eof:
            raise ValueError(f"line {lineno}: data after EOF record")
        if not line.startswith(":"):
            raise ValueError(f"line {lineno}: does not start with ':'")
        try:
            raw = bytes.fromhex(line[1:])
        except ValueError:
            raise ValueError(f"line {lineno}: not hexadecimal") from None
        if len(raw) < 5 or len(raw) != raw[0] + 5:
            raise ValueError(f"line {lineno}: record length does not match byte count")
        if sum(raw) & 0xFF:
            raise ValueError(f"line {lineno}: checksum mismatch")
        records += 1
        byte_count, addr16, rec_type = raw[0], (raw[1] << 8) | raw[2], raw[3]
        data = raw[4 : 4 + byte_count]
        if rec_type not in _RECORD_LEN:
            raise ValueError(f"line {lineno}: unknown record type 0x{rec_type:02X}")
        want = _RECORD_LEN[rec_type]
        if want is not None and byte_count != want:
            raise ValueError(f"line {lineno}: record type 0x{rec_type:02X} needs {want} data bytes")

        if rec_type == 0x00:
            for i, b in enumerate(data):
                addr = ext_addr + addr16 + i
                if addr in mem:
                    raise ValueError(f"line {lineno}: address 0x{addr:08X} written twice")
                mem[addr] = b
        elif rec_type == 0x01:
            eof = True
        elif rec_type == 0x02:  # Extended Segment Address (GNU objcopy / Zephyr)
            ext_addr = ((data[0] << 8) | data[1]) << 4
        elif rec_type == 0x04:  # Extended Linear Address
            ext_addr = ((data[0] << 8) | data[1]) << 16
        elif rec_type == 0x05:  # Start Linear Address
            sla_entry = struct.unpack(">I", data)[0]
        # 0x03 (Start Segment Address, CS:IP) carries no flash data: accepted and ignored

    if not eof:
        raise ValueError("missing EOF record (truncated file?)")
    if not mem:
        raise ValueError("no data records")

    min_addr, max_addr = min(mem), max(mem) + 1
    out = bytearray(b"\xFF" * (max_addr - min_addr))
    for addr, val in mem.items():
        out[addr - min_addr] = val
    return bytes(out), min_addr, max_addr, sla_entry, records


def encode_compx_hex(bin_data: bytes, base_addr: int = MOUSE_APP_BASE, sla_entry: int = None) -> str:
    """Encode a raw ARM binary as Intel HEX (16-byte data records, type 04 + optional 05).

    Without sla_entry, the Start Linear Address is taken from the vector table's Reset_Handler.
    """
    if base_addr % 16:
        raise ValueError("base_addr must be 16-byte aligned")

    def make_record(addr16: int, rec_type: int, payload: bytes) -> str:
        body = bytes([len(payload), (addr16 >> 8) & 0xFF, addr16 & 0xFF, rec_type]) + payload
        return ":" + body.hex().upper() + f"{(-sum(body)) & 0xFF:02X}"

    lines = []
    current_upper = None
    for offset in range(0, len(bin_data), 16):
        abs_addr = base_addr + offset
        upper = abs_addr >> 16
        if upper != current_upper:
            current_upper = upper
            lines.append(make_record(0x0000, 0x04, struct.pack(">H", upper)))
        lines.append(make_record(abs_addr & 0xFFFF, 0x00, bin_data[offset : offset + 16]))

    if sla_entry is None and len(bin_data) >= 8:
        sla_entry = struct.unpack_from("<I", bin_data, 4)[0]
    if sla_entry is not None:
        lines.append(make_record(0x0000, 0x05, struct.pack(">I", sla_entry)))

    lines.append(":00000001FF")
    return "\r\n".join(lines) + "\r\n"


def audit_binary_safety(bin_data: bytes, base_addr: int = MOUSE_APP_BASE, app_base: int = MOUSE_APP_BASE) -> dict:
    """Check the safety invariants of an nRF52840 application image linked at base_addr."""
    end_addr = base_addr + len(bin_data)
    sp_init, reset_handler = struct.unpack_from("<II", bin_data.ljust(8, b"\xFF"))
    reset_addr = reset_handler & ~1

    checks = {
        "vector_table_at_app_base": base_addr == app_base,
        "ends_below_0x7E000": end_addr <= MOUSE_SAFE_LIMIT,
        "valid_initial_sp_in_sram": 0x20000000 < sp_init <= 0x2003FFF0 and sp_init % 8 == 0,
        "valid_thumb2_reset_handler": (
            (reset_handler & 1) == 1 and base_addr + VECTOR_TABLE_SIZE <= reset_addr < end_addr
        ),
        # Heuristic: a literal pool entry, not proof that re-entry code is reachable.
        "contains_bootloader_magic_0xA55AA55A": struct.pack("<I", BOOTLOADER_MAGIC_VAL) in bin_data,
    }
    return {
        "base_addr": f"0x{base_addr:08X}",
        "end_addr": f"0x{end_addr:08X}",
        "size_bytes": len(bin_data),
        "initial_sp": f"0x{sp_init:08X}",
        "reset_handler": f"0x{reset_handler:08X}",
        "sha256": hashlib.sha256(bin_data).hexdigest(),
        "checks": checks,
        "all_passed": all(checks.values()),
    }


def load_app_hex(hex_path: Path, app_base: int = MOUSE_APP_BASE):
    """Decode a .hex and require that its lowest address is the application base."""
    bin_data, min_addr, _max, sla, records = parse_compx_hex(hex_path)
    if min_addr != app_base:
        raise ValueError(f"{hex_path}: lowest address 0x{min_addr:08X}, expected app base 0x{app_base:08X}")
    return bin_data, sla, records


def _print_audit(path, records, sla, report) -> None:
    print(f"File:          {path}")
    print(f"HEX Records:   {records}")
    print(f"Address Range: {report['base_addr']} .. {report['end_addr']} ({report['size_bytes']} bytes)")
    print(f"SLA Entry:     0x{sla:08X}" if sla is not None else "SLA Entry:     None")
    print(f"Initial SP:    {report['initial_sp']}")
    print(f"Reset Handler: {report['reset_handler']}")
    print(f"Decoded SHA256:{report['sha256']}")
    print("Safety Checks:")
    for k, v in report["checks"].items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compx nRF52840 Intel HEX tool (plain Intel HEX files)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--target", choices=TARGETS, default="mouse", help="mouse (base 0x6000) or receiver (0x7000)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decode", parents=[common], help="Intel HEX -> raw .bin starting at the app base")
    p.add_argument("hex_file", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p = sub.add_parser("encode", parents=[common], help="raw .bin -> Intel HEX at the app base")
    p.add_argument("bin_file", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p = sub.add_parser("audit", parents=[common], help="Decode and safety-audit a .hex file")
    p.add_argument("hex_file", type=Path)
    p = sub.add_parser("selftest", parents=[common], help="Audit plus bit-exact encode/decode roundtrip")
    p.add_argument("hex_file", type=Path)
    args = parser.parse_args()
    app_base = TARGETS[args.target]

    if args.cmd == "decode":
        bin_data, _sla, records = load_app_hex(args.hex_file, app_base)
        args.output.write_bytes(bin_data)
        print(f"{args.hex_file} ({records} records) -> {args.output}: {len(bin_data)} bytes at 0x{app_base:08X}")
        return 0
    if args.cmd == "encode":
        bin_data = args.bin_file.read_bytes()
        args.output.write_text(encode_compx_hex(bin_data, app_base), encoding="ascii", newline="")
        print(f"{args.bin_file} -> {args.output}: {len(bin_data)} bytes at 0x{app_base:08X}")
        return 0

    bin_data, min_addr, max_addr, sla, records = parse_compx_hex(args.hex_file)
    report = audit_binary_safety(bin_data, min_addr, app_base)
    _print_audit(args.hex_file, records, sla, report)
    ok = report["all_passed"]
    if args.cmd == "selftest":
        rt_bin, rt_min, rt_max, rt_sla, _ = parse_compx_hex(encode_compx_hex(bin_data, min_addr, sla))
        same = (rt_bin, rt_min, rt_max) == (bin_data, min_addr, max_addr) and (sla is None or rt_sla == sla)
        print(f"  [{'PASS' if same else 'FAIL'}] roundtrip_encode_decode_bit_exact")
        ok = ok and same
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as e:
        sys.exit(f"error: {e}")
