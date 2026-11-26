#!/usr/bin/env python3
"""
Offline analyzer for a read-only Stage 2 dump of the Glorious Series One Pro 2.4 GHz receiver
bootloader (258a:2113), produced by:
  compx_usb_flasher.py --target receiver dump --allow-receiver-bl --out rx_dump.hex

Standard library only. Never touches USB and never prints your RF ID unless --show-rf-id is passed.

What it determines from a read-only dump (without writing a single byte to the receiver):
  1. Readable VerifyByAddress range (min_addr..max_addr) and every non-blank 4 KB page.
  2. Byte-for-byte comparison against the official receiver image (CM2856_Receiver_USB_v1.00.00_20220810.hex),
     confirming whether VerifyByAddress reads back the stock application at 0x7000.
  3. Two-copy bank offset detection: scans every 4 KB page (and fallback 2-byte offsets) for the
     receiver's 0x38000 config page struct:
       +0x00: u16 LE = 0x0024
       +0x02: u32 LE = RF ID (optional match against --ficr-bin or --rf-id)
       +0x22: u16 LE = CRC-16/CCITT-FALSE over bytes +0x00..+0x21 (receiver @ 0xC6FC)
     If that struct appears at dump address A, then real flash 0x38000 maps to dump address A:
       BANK_OFFSET      = 0x38000 - A
       SAFE_PROGRAM_TOP = A  (any Program write at or above A would overwrite 0x38000)

Run self-test (no hardware or vendor files required):
  python3 rx_dump_analyze.py --selftest
"""

import argparse
import hashlib
import struct
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[3] / "platforms" / "compx-nrf52" / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import compx_hex_tool as hx  # noqa: E402

RX_APP_BASE = hx.RECEIVER_APP_BASE  # 0x7000
RX_CFG_FLASH_ADDR = 0x00038000
RX_CFG_MAGIC_LEN = 0x0024
RX_CFG_SIZE = 0x24


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), matching receiver @ 0xC6FC."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def is_valid_rx_config(chunk: bytes) -> bool:
    if len(chunk) < RX_CFG_SIZE:
        return False
    if struct.unpack_from("<H", chunk, 0)[0] != RX_CFG_MAGIC_LEN:
        return False
    rf_id = struct.unpack_from("<I", chunk, 2)[0]
    if rf_id in (0x00000000, 0xFFFFFFFF):
        return False
    stored_crc = struct.unpack_from("<H", chunk, 0x22)[0]
    return stored_crc == crc16_ccitt_false(chunk[:0x22])


def find_rx_config_hits(dump: bytes, min_addr: int, expected_rf_id: int | None = None) -> list[dict]:
    """Scan dump for valid 0x38000 receiver config structs (page-aligned first, then any 2-byte offset)."""
    hits = []
    seen = set()
    # Check 4 KB page starts first (most likely: bank offset is page- or half-page-aligned)
    for off in range(0, len(dump) - RX_CFG_SIZE + 1, 2):
        if dump[off] != 0x24 or dump[off + 1] != 0x00:
            continue
        chunk = dump[off : off + RX_CFG_SIZE]
        if not is_valid_rx_config(chunk):
            continue
        rf_id = struct.unpack_from("<I", chunk, 2)[0]
        if expected_rf_id is not None and rf_id != expected_rf_id:
            continue
        addr = min_addr + off
        if addr in seen:
            continue
        seen.add(addr)
        hits.append({
            "dump_addr": addr,
            "page_aligned": (addr & 0xFFF) == 0,
            "half_page_aligned": (addr & 0x7FF) == 0,
            "rf_id": rf_id,
            "rf_id_matches": (expected_rf_id is not None and rf_id == expected_rf_id),
            "bank_offset": RX_CFG_FLASH_ADDR - addr,
            "safe_program_top": addr,
        })
    hits.sort(key=lambda h: (not h["rf_id_matches"], not h["page_aligned"], not h["half_page_aligned"], h["dump_addr"]))
    return hits


def analyze_dump(dump_hex: Path, ref_hex: Path | None = None, expected_rf_id: int | None = None) -> dict:
    dump, lo, hi, _sla, recs = hx.parse_compx_hex(dump_hex)
    pages = [
        lo + off
        for off in range(0, len(dump), 0x1000)
        if dump[off : off + 0x1000].strip(b"\xFF")
    ]
    ref_cmp = None
    if ref_hex is not None:
        ref_bin, ref_lo, ref_hi, _rsla, _rrecs = hx.parse_compx_hex(ref_hex)
        if ref_lo != RX_APP_BASE:
            raise ValueError(f"reference {ref_hex} starts at 0x{ref_lo:08X}, expected 0x{RX_APP_BASE:08X}")
        overlap_end = min(hi, ref_hi)
        if lo <= ref_lo and overlap_end > ref_lo:
            seg_dump = dump[ref_lo - lo : overlap_end - lo]
            seg_ref = ref_bin[: overlap_end - ref_lo]
            first_diff = next((ref_lo + i for i, (a, b) in enumerate(zip(seg_dump, seg_ref)) if a != b), None)
            ref_cmp = {
                "compared_bytes": len(seg_ref),
                "full_ref_bytes": len(ref_bin),
                "exact_match": first_diff is None and len(seg_ref) == len(ref_bin),
                "first_diff_addr": first_diff,
            }

    cfg_hits = find_rx_config_hits(dump, lo, expected_rf_id=expected_rf_id)
    # Also report exact RF ID hits even if CRC were somehow stale/erased
    raw_id_hits = []
    if expected_rf_id is not None:
        needle = struct.pack("<I", expected_rf_id)
        pos = 0
        while True:
            idx = dump.find(needle, pos)
            if idx < 0:
                break
            raw_id_hits.append(lo + idx)
            pos = idx + 1

    return {
        "records": recs,
        "min_addr": lo,
        "max_addr": hi,
        "size_bytes": len(dump),
        "sha256": hashlib.sha256(dump).hexdigest(),
        "non_blank_pages": pages,
        "ref_cmp": ref_cmp,
        "cfg_hits": cfg_hits,
        "raw_id_hits": raw_id_hits,
    }


def _parse_expected_rf_id(args) -> int | None:
    if args.rf_id and args.ficr_bin:
        raise ValueError("pass either --rf-id or --ficr-bin, not both")
    if args.ficr_bin:
        ficr = Path(args.ficr_bin).read_bytes()
        if len(ficr) < 0x64:
            raise ValueError(f"{args.ficr_bin} is too small ({len(ficr)} B); expected FICR dump (>= 0x64 B)")
        return struct.unpack_from("<I", ficr, 0x60)[0]
    if args.rf_id:
        s = args.rf_id.strip().lower().replace(":", "").replace(" ", "")
        if s.startswith("0x"):
            return int(s, 16)
        if len(s) == 8:
            # Treat 8 raw hex chars without 0x as the 4 LE bytes from 0x8A (as GloriousPairingUtility prints them)
            return struct.unpack("<I", bytes.fromhex(s))[0]
        return int(s, 16)
    return None


def _print_report(res: dict, show_rf_id: bool = False) -> None:
    print(f"Dump range:      0x{res['min_addr']:08X}..0x{res['max_addr'] - 1:08X} ({res['size_bytes']} bytes, {res['records']} records)")
    print(f"Read ceiling:    0x{res['max_addr']:08X}")
    print(f"Dump SHA-256:    {res['sha256']}")
    pages_s = " ".join(f"0x{p:05X}" for p in res["non_blank_pages"]) or "none"
    print(f"Non-blank pages: {pages_s}")

    if res["ref_cmp"] is not None:
        rc = res["ref_cmp"]
        if rc["exact_match"]:
            print(f"Stock app match: EXACT MATCH ({rc['compared_bytes']} / {rc['full_ref_bytes']} bytes at 0x{RX_APP_BASE:05X})")
        else:
            diff = f"0x{rc['first_diff_addr']:08X}" if rc["first_diff_addr"] is not None else "truncated"
            print(f"Stock app match: MISMATCH (compared {rc['compared_bytes']} / {rc['full_ref_bytes']} B, first diff {diff})")

    if res["cfg_hits"]:
        print(f"Config struct (0x38000) matches: {len(res['cfg_hits'])}")
        for h in res["cfg_hits"]:
            rf_s = f"0x{h['rf_id']:08X}" if show_rf_id else "<redacted>"
            kind = "SINGLE-COPY (direct)" if h["bank_offset"] == 0 else f"TWO-COPY (BANK_OFFSET = +0x{h['bank_offset']:X})"
            print(
                f"  - dump_addr=0x{h['dump_addr']:08X}  rf_id={rf_s}  "
                f"aligned={'4K' if h['page_aligned'] else ('2K' if h['half_page_aligned'] else 'no')}  "
                f"layout={kind}  SAFE_PROGRAM_TOP=0x{h['safe_program_top']:08X}"
            )
    else:
        print("Config struct (0x38000) matches: none inside readable VerifyByAddress range")
        if res["max_addr"] <= RX_CFG_FLASH_ADDR:
            print(
                f"  Note: VerifyByAddress stopped at 0x{res['max_addr']:08X} (<= 0x{RX_CFG_FLASH_ADDR:08X}). "
                "Either the bootloader refuses reads at/above 0x38000 (single-copy), or a two-copy bank "
                "ends below 0x38000 (so Program cannot reach 0x38000)."
            )

    if res["raw_id_hits"]:
        addrs = ", ".join(f"0x{a:08X}" for a in res["raw_id_hits"])
        print(f"Raw RF ID occurrences (LE u32): {addrs}")


def selftest() -> int:
    test_rf_id = 0x11223344
    cfg = bytearray(RX_CFG_SIZE)
    struct.pack_into("<HI", cfg, 0, RX_CFG_MAGIC_LEN, test_rf_id)
    struct.pack_into("<H", cfg, 0x22, crc16_ccitt_false(bytes(cfg[:0x22])))
    assert is_valid_rx_config(bytes(cfg))

    fake_app = bytes((i * 13 + 7) & 0xFF for i in range(0x7E04))

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ref_path = tmp / "ref.hex"
        ref_path.write_text(hx.encode_compx_hex(fake_app, RX_APP_BASE))

        # Case 1: two-copy bootloader with bank offset +0x19000 (so 0x38000 appears at 0x1F000 in dump)
        bank_off = 0x19000
        dump1 = bytearray(b"\xFF" * (0x20000 - RX_APP_BASE))
        dump1[: len(fake_app)] = fake_app
        cfg_dump_addr = RX_CFG_FLASH_ADDR - bank_off  # 0x1F000
        dump1[cfg_dump_addr - RX_APP_BASE : cfg_dump_addr - RX_APP_BASE + RX_CFG_SIZE] = cfg
        p1 = tmp / "two_copy.hex"
        p1.write_text(hx.encode_compx_hex(bytes(dump1), RX_APP_BASE))
        r1 = analyze_dump(p1, ref_hex=ref_path, expected_rf_id=test_rf_id)
        assert r1["ref_cmp"]["exact_match"]
        assert len(r1["cfg_hits"]) == 1
        assert r1["cfg_hits"][0]["dump_addr"] == 0x1F000
        assert r1["cfg_hits"][0]["bank_offset"] == 0x19000
        assert r1["cfg_hits"][0]["safe_program_top"] == 0x1F000

        # Case 2: single-copy direct mapping where 0x38000 is readable directly at 0x38000
        dump2 = bytearray(b"\xFF" * (0x39000 - RX_APP_BASE))
        dump2[: len(fake_app)] = fake_app
        dump2[RX_CFG_FLASH_ADDR - RX_APP_BASE : RX_CFG_FLASH_ADDR - RX_APP_BASE + RX_CFG_SIZE] = cfg
        p2 = tmp / "single_copy.hex"
        p2.write_text(hx.encode_compx_hex(bytes(dump2), RX_APP_BASE))
        r2 = analyze_dump(p2, ref_hex=ref_path, expected_rf_id=test_rf_id)
        assert r2["ref_cmp"]["exact_match"]
        assert len(r2["cfg_hits"]) == 1
        assert r2["cfg_hits"][0]["dump_addr"] == RX_CFG_FLASH_ADDR
        assert r2["cfg_hits"][0]["bank_offset"] == 0
        assert r2["cfg_hits"][0]["safe_program_top"] == RX_CFG_FLASH_ADDR

        # Case 3: read refused at 0x38000 (config page not inside readable range)
        dump3 = bytearray(b"\xFF" * (0x38000 - RX_APP_BASE))
        dump3[: len(fake_app)] = fake_app
        p3 = tmp / "capped.hex"
        p3.write_text(hx.encode_compx_hex(bytes(dump3), RX_APP_BASE))
        r3 = analyze_dump(p3, ref_hex=ref_path, expected_rf_id=test_rf_id)
        assert r3["ref_cmp"]["exact_match"] and not r3["cfg_hits"]

    print("rx_dump_analyze.py selftest passed (3/3 cases)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump_hex", nargs="?", type=Path, help="Stage 2 dump Intel HEX from compx_usb_flasher.py")
    ap.add_argument("--reference-hex", type=Path, help="Official CM2856_Receiver_USB_v1.00.00_20220810.hex")
    ap.add_argument("--ficr-bin", type=Path, help="Private unit FICR dump (reads DEVICEID[0] at +0x60 without printing it)")
    ap.add_argument("--rf-id", help="Expected RF ID (0xXXXXXXXX u32 or 8 LE hex chars from 0x8A)")
    ap.add_argument("--show-rf-id", action="store_true", help="Print matched RF ID (do NOT share output publicly)")
    ap.add_argument("--selftest", action="store_true", help="Run offline self-test on synthetic dumps")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.dump_hex:
        ap.error("provide dump_hex or --selftest")
    expected_rf_id = _parse_expected_rf_id(args)
    res = analyze_dump(args.dump_hex, ref_hex=args.reference_hex, expected_rf_id=expected_rf_id)
    _print_report(res, show_rf_id=args.show_rf_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
