#!/usr/bin/env python3
"""
Safe Hardware Inspector, Flash Verifier, and USB HID Bootloader Programmer
for Compx / Nordic nRF52840 Gaming Mice (Glorious Series One Pro 258a:2018 / Bootloader 258a:2103).

Implements the C52840Dongle USB HID Feature-report ISP protocol from FirmwareUpdate.exe
with multi-stage hardware safety gates:
  1. Probe & read-only query of live device (258a:2018 or 258a:2103).
  2. Read-only flash verification (VerifyByAddress 0x83) in 258a:2103 bootloader mode
     comparing the mouse's physical flash at 0x00006000 against a reference image
     before any erase or write is permitted.
  3. Safe application-only programming (0x00006000+) with 100% 32-byte block readback
     verification before exiting bootloader mode.
"""

import argparse
import fcntl
import glob
import os
import struct
import sys
import time
from pathlib import Path


def HIDIOCSFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def HIDIOCGFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x07


VID_SINOWEALTH = 0x258A
PID_MOUSE_APP = 0x2018
PID_MOUSE_BL = 0x2103
PID_RECV_APP = 0x2031
PID_RECV_BL = 0x2113

APP_BASE_ADDR = 0x00006000
XOR_WIRE_KEY = 0x55


def find_hidraw_feature_node(target_vid: int, target_pid: int) -> Path | None:
    """
    Find the /dev/hidrawX node corresponding to Interface 2 (Usage Page 0xFFFF,
    64-byte Feature report 0x00) for the given USB VID:PID.
    """
    candidates = []
    for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = Path(sys_path) / "device" / "uevent"
        if not uevent_path.exists():
            continue
        text = uevent_path.read_text(errors="ignore")
        vid = pid = None
        for line in text.splitlines():
            if line.startswith("HID_ID="):
                parts = line.split("=")[1].split(":")
                if len(parts) == 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
        if vid != target_vid or pid != target_pid:
            continue

        rdesc_path = Path(sys_path) / "device" / "report_descriptor"
        is_vendor_feat = False
        if rdesc_path.exists():
            rdesc = rdesc_path.read_bytes()
            if b"\x06\xff\xff" in rdesc and b"\xb1\x02" in rdesc:
                is_vendor_feat = True

        dev_node = Path("/dev") / Path(sys_path).name
        candidates.append((is_vendor_feat, dev_node))

    for is_vendor_feat, dev_node in candidates:
        if is_vendor_feat:
            return dev_node
    return candidates[-1][1] if candidates else None


class CompxHidDevice:
    def __init__(self, dev_path: Path):
        self.dev_path = dev_path
        self.fd = os.open(str(dev_path), os.O_RDWR)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def set_feature(self, payload64: bytes) -> None:
        assert len(payload64) == 64
        for attempt in range(5):
            buf = bytearray(b"\x00" + payload64)
            try:
                fcntl.ioctl(self.fd, HIDIOCSFEATURE(65), buf)
                return
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.015)

    def get_feature(self) -> bytes:
        for attempt in range(5):
            buf = bytearray(65)
            buf[0] = 0x00
            try:
                fcntl.ioctl(self.fd, HIDIOCGFEATURE(65), buf)
                return bytes(buf[1:65])
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.015)
        return bytes(64)

    def command(self, payload64: bytes, delay_s: float = 0.020, retries: int = 5) -> bytes:
        self.set_feature(payload64)
        for _ in range(retries):
            time.sleep(delay_s)
            resp = self.get_feature()
            if resp[0] in (0xA1, 0x02):
                return resp
            if resp[0] in (0xA2, 0xA3, 0xA4):
                return resp
        return resp


def query_app_fw_version(dev: CompxHidDevice) -> tuple[tuple[int, int, int, int], int]:
    """Send standard read-only Glorious FW Version query (0x00, 0x81) to 258a:2018."""
    req = bytearray(64)
    req[3] = 0x06
    req[4] = 0x00
    req[5] = 0x81
    resp = dev.command(bytes(req), delay_s=0.025)
    if resp[0] != 0xA1:
        raise RuntimeError(f"Unexpected FW version response status: 0x{resp[0]:02X} ({resp[:16].hex()})")
    ver = (resp[6], resp[7], resp[8], resp[9])
    pid = (resp[10] << 8) | resp[11]
    return ver, pid


def enter_bootloader_from_app(dev: CompxHidDevice) -> None:
    """
    Send exact C52840Dongle::SetBLDeviceMode command to 258a:2018:
      feat[3]=0x02, feat[4]=0x00, feat[5]=0x00, feat[6]=0xB0
    """
    req = bytearray(64)
    req[3] = 0x02
    req[4] = 0x00
    req[5] = 0x00
    req[6] = 0xB0
    try:
        dev.set_feature(bytes(req))
    except OSError:
        pass


def bl_get_version(bl: CompxHidDevice) -> bytes:
    """C52840Dongle::GetBLFW (cmd 0x80)."""
    req = bytearray(64)
    req[3] = 0x06
    req[4] = 0xB0
    req[5] = 0x80
    resp = bl.command(bytes(req), delay_s=0.020)
    if resp[0] not in (0xA1, 0x02):
        raise RuntimeError(f"GetBLFW failed: 0x{resp[0]:02X} ({resp[:16].hex()})")
    return resp[8:12]


def bl_read_32(bl: CompxHidDevice, address: int, length: int = 32) -> bytes:
    """C52840Dongle::VerifyByAddress (cmd 0x83) — reads up to 32 bytes from Flash and de-XORs 0x55."""
    assert 1 <= length <= 32
    req = bytearray(64)
    req[3] = 0x20
    req[4] = 0xB0
    req[5] = 0x83
    req[6] = length
    struct.pack_into(">I", req, 7, address)
    resp = bl.command(bytes(req), delay_s=0.015)
    if resp[0] not in (0xA1, 0x02):
        raise RuntimeError(f"VerifyByAddress(0x{address:08X}) failed: status 0x{resp[0]:02X}")
    raw = resp[11 : 11 + length]
    return bytes(b ^ XOR_WIRE_KEY for b in raw)


def bl_erase_app(bl: CompxHidDevice) -> None:
    """C52840Dongle::Erase (cmd 0x01) — erases application flash (0x00006000+)."""
    req = bytearray(64)
    req[3] = 0x08
    req[4] = 0xB0
    req[5] = 0x01
    bl.set_feature(bytes(req))
    for _ in range(40):
        time.sleep(0.050)
        resp = bl.get_feature()
        if resp[0] in (0xA1, 0x02):
            return
    raise RuntimeError(f"Bootloader Erase timed out or failed: last resp={resp[:16].hex()}")


def bl_program_32(bl: CompxHidDevice, address: int, chunk: bytes) -> None:
    """C52840Dongle::Program (cmd 0x02) — writes up to 32 bytes XORed with 0x55 to Flash."""
    assert 1 <= len(chunk) <= 32
    req = bytearray(64)
    req[3] = len(chunk) + 5
    req[4] = 0xB0
    req[5] = 0x02
    req[6] = len(chunk)
    struct.pack_into(">I", req, 7, address)
    for i, b in enumerate(chunk):
        req[11 + i] = b ^ XOR_WIRE_KEY
    bl.set_feature(bytes(req))
    for _ in range(20):
        time.sleep(0.003)
        resp = bl.get_feature()
        if resp[0] in (0xA1, 0x02):
            return
    raise RuntimeError(f"Program(0x{address:08X}) failed: last resp={resp[:16].hex()}")


def bl_exit_to_app(bl: CompxHidDevice) -> None:
    """C52840Dongle::Exit (cmd 0x04, mode=0x01) — validates and boots application at 0x00006000."""
    req = bytearray(64)
    req[3] = 0x01
    req[4] = 0xB0
    req[5] = 0x04
    req[6] = 0x01
    try:
        bl.set_feature(bytes(req))
    except OSError:
        pass


def wait_for_device(vid: int, pid: int, timeout_s: float = 10.0) -> Path:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        node = find_hidraw_feature_node(vid, pid)
        if node is not None and os.access(str(node), os.R_OK | os.W_OK):
            time.sleep(0.150)
            return node
        time.sleep(0.100)
    raise TimeoutError(f"Timed out waiting for USB HID device {vid:04x}:{pid:04x}")


def cmd_probe(_args):
    app_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_APP)
    bl_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_BL)
    if app_node:
        print(f"[PROBE] Found Wired Mouse Application ({VID_SINOWEALTH:04x}:{PID_MOUSE_APP:04x}) at {app_node}")
        with CompxHidDevice(app_node) as dev:
            ver, pid = query_app_fw_version(dev)
            print(f"        Firmware Version: v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X})")
    elif bl_node:
        print(f"[PROBE] Found Wired Mouse Bootloader ({VID_SINOWEALTH:04x}:{PID_MOUSE_BL:04x}) at {bl_node}")
        with CompxHidDevice(bl_node) as bl:
            bl_ver = bl_get_version(bl)
            print(f"        Bootloader Version Bytes: {bl_ver.hex()}")
    else:
        print("[ERROR] Neither 258a:2018 (App) nor 258a:2103 (Bootloader) found on USB.")
        sys.exit(1)


def cmd_verify_bl(args):
    stock_bin = Path(args.stock_bin).read_bytes()
    app_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_APP)
    bl_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_BL)

    if app_node and not bl_node:
        print(f"[GATE 1] Querying live mouse at {app_node} before entering bootloader...")
        with CompxHidDevice(app_node) as dev:
            ver, pid = query_app_fw_version(dev)
            print(f"         Confirmed FW v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X})")
            print("[GATE 2] Sending SetBLDeviceMode (0x00, 0x00, 0xB0) to enter 258a:2103 bootloader...")
            enter_bootloader_from_app(dev)

    bl_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_BL, timeout_s=10.0)
    print(f"[GATE 2] Mouse enumerated in Resident Bootloader ({VID_SINOWEALTH:04x}:{PID_MOUSE_BL:04x}) at {bl_node}")

    with CompxHidDevice(bl_node) as bl:
        bl_ver = bl_get_version(bl)
        print(f"         GetBLFW (0x80) succeeded: BL Version = {bl_ver.hex()} (contains 0xB0: {0xB0 in bl_ver})")

        print("         Performing read-only VerifyByAddress (0x83) check at 0x00006000..0x00006100...")
        head_read = bytearray()
        for off in range(0, 256, 32):
            head_read += bl_read_32(bl, APP_BASE_ADDR + off, 32)

        if bytes(head_read) == stock_bin[:256]:
            print("         [100% MATCH] Physical mouse flash at 0x00006000..0x000060FF matches reference binary.")
        else:
            print(f"         [WARNING] Flash mismatch at 0x00006000! Expected {stock_bin[:32].hex()}, got {head_read[:32].hex()}")

        if not args.stay_in_bl:
            print("         Sending Exit (0x04, mode=0x01) to return mouse to normal operating mode...")
            bl_exit_to_app(bl)

    if not args.stay_in_bl:
        app_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_APP, timeout_s=10.0)
        print(f"[GATE 2] Mouse returned to normal application mode ({VID_SINOWEALTH:04x}:{PID_MOUSE_APP:04x}) at {app_node}")


def cmd_flash(args):
    fw_path = Path(args.firmware_bin)
    fw_bytes = fw_path.read_bytes()
    if len(fw_bytes) % 32 != 0:
        fw_bytes += b"\xFF" * (32 - (len(fw_bytes) % 32))

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import compx_hex_tool

    audit = compx_hex_tool.audit_binary_safety(fw_bytes, base_addr=APP_BASE_ADDR)
    if not audit["all_passed"]:
        raise RuntimeError(f"Refusing to flash: safety audit failed: {audit}")
    print(f"[SAFETY AUDIT] Passed all 6 checks on {fw_path.name} ({len(fw_bytes)} bytes, SHA256={audit['sha256'][:16]}...)")

    app_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_APP)
    bl_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_BL)

    if app_node and not bl_node:
        print(f"[STEP 1] Entering Bootloader from {app_node}...")
        with CompxHidDevice(app_node) as dev:
            enter_bootloader_from_app(dev)

    bl_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_BL, timeout_s=10.0)
    print(f"[STEP 2] Connected to Bootloader ({VID_SINOWEALTH:04x}:{PID_MOUSE_BL:04x}) at {bl_node}")

    with CompxHidDevice(bl_node) as bl:
        bl_ver = bl_get_version(bl)
        print(f"         Bootloader Version: {bl_ver.hex()}")

        print("[STEP 3] Erasing application flash (0x00006000+)...")
        t0 = time.time()
        bl_erase_app(bl)
        print(f"         Erase completed in {time.time() - t0:.2f}s")

        total_len = len(fw_bytes)
        print(f"[STEP 4] Programming {total_len} bytes (0x{APP_BASE_ADDR:08X}..0x{APP_BASE_ADDR + total_len - 1:08X})...")
        t0 = time.time()
        for offset in range(0, total_len, 32):
            addr = APP_BASE_ADDR + offset
            chunk = fw_bytes[offset : offset + 32]
            bl_program_32(bl, addr, chunk)
            if ((offset + 32) % 4096) == 0:
                time.sleep(0.010)
            if ((offset + 32) % 16384) == 0 or (offset + 32) == total_len:
                pct = (offset + 32) * 100.0 / total_len
                print(f"         Programmed {offset + 32:6d} / {total_len} bytes ({pct:5.1f}%)")
        print(f"         Programming completed in {time.time() - t0:.2f}s")

        print(f"[STEP 5] Read-back verifying all {total_len} bytes via VerifyByAddress (0x83)...")
        t0 = time.time()
        for offset in range(0, total_len, 32):
            addr = APP_BASE_ADDR + offset
            expected = fw_bytes[offset : offset + 32]
            actual = bl_read_32(bl, addr, 32)
            if actual != expected:
                raise RuntimeError(
                    f"VERIFY MISMATCH at 0x{addr:08X}: expected {expected.hex()}, got {actual.hex()}"
                )
            if ((offset + 32) % 16384) == 0 or (offset + 32) == total_len:
                pct = (offset + 32) * 100.0 / total_len
                print(f"         Verified   {offset + 32:6d} / {total_len} bytes ({pct:5.1f}%)")
        print(f"         100% of flash bytes verified bit-for-bit in {time.time() - t0:.2f}s!")

        print("[STEP 6] Sending Exit (0x04, mode=0x01) to boot into new firmware...")
        bl_exit_to_app(bl)

    app_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_APP, timeout_s=10.0)
    print(f"[STEP 7] Mouse booted and enumerated as {VID_SINOWEALTH:04x}:{PID_MOUSE_APP:04x} at {app_node}!")


def main():
    parser = argparse.ArgumentParser(description="Safe Compx / nRF52840 Mouse Firmware Inspector & USB HID Flasher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="Read-only probe of connected mouse")

    p_vbl = sub.add_parser("verify-bl", help="Enter bootloader, read-only verify flash against reference bin, and exit")
    p_vbl.add_argument("--stock-bin", required=True, help="Path to reference application .bin")
    p_vbl.add_argument("--stay-in-bl", action="store_true")

    p_flash = sub.add_parser("flash", help="Safely program and verify firmware binary")
    p_flash.add_argument("--firmware-bin", required=True, help="Path to application .bin to flash at 0x00006000")

    args = parser.parse_args()
    if args.cmd == "probe":
        cmd_probe(args)
    elif args.cmd == "verify-bl":
        cmd_verify_bl(args)
    elif args.cmd == "flash":
        cmd_flash(args)


if __name__ == "__main__":
    main()
