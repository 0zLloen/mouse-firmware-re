#!/usr/bin/env python3
"""
USB HID bootloader prober, verifier and programmer for Compx / Nordic nRF52840 mice
(Glorious Series One Pro: app 258a:2018, resident bootloader 258a:2103). Linux hidraw only.

Implements the C52840Dongle USB HID Feature-report ISP protocol from FirmwareUpdate.exe:
  probe      read-only: report which mode the mouse is in and its version.
  verify-bl  read-only: enter the bootloader, read back the whole reference image with
             VerifyByAddress (0x83) and compare. Sends Exit only on a full match; on a
             mismatch or error the mouse stays in the bootloader and the exit code is 1.
  flash      program an Intel HEX image whose lowest address is 0x6000:
               1. offline safety audit (compx_hex_tool.audit_binary_safety)
               2. Erase, then confirm 0x6000 reads back erased
               3. program and verify everything except the first 32-byte block
               4. program and verify the vector-table block at 0x6000 last
               5. Exit
             An interruption before step 4 leaves 0x6000 erased, so the bootloader stays
             resident. flash does not compare against a reference image before erasing.
  dump       read-only backup: enter the bootloader and save flash (default 0x6000..0xFFFFF,
             including the pairing/calibration pages) to an Intel HEX file; run it first.

The app -> bootloader entry packet (enter_bootloader_from_app) is unverified: if the mouse
does not re-enumerate as 258a:2103, put it in bootloader mode with the official updater and
run this tool again (it uses an already-present 258a:2103 directly).
"""

import argparse
import errno
import fcntl
import glob
import hashlib
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compx_hex_tool  # noqa: E402


def HIDIOCSFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def HIDIOCGFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x07


VID_SINOWEALTH = 0x258A
PID_MOUSE_APP = 0x2018
PID_MOUSE_BL = 0x2103

APP_BASE_ADDR = compx_hex_tool.MOUSE_APP_BASE
FLASH_END = 0x00100000  # nRF52840: 1 MiB
BLOCK = 32
XOR_WIRE_KEY = 0x55  # USB wire only; .hex files are plain
STATUS_OK = (0xA1, 0x02)
STATUS_ERR = (0xA2, 0xA3, 0xA4)
# errnos a SET may return when the device resets/detaches while handling it
RESET_ERRNOS = (errno.ENODEV, errno.EPIPE, errno.ESHUTDOWN, errno.EPROTO, errno.EIO)


def find_hidraw_feature_node(target_vid: int, target_pid: int, sysfs: str = "/sys/class/hidraw") -> Path | None:
    """
    Return the /dev/hidrawN node of the vendor Feature interface (Usage Page 0xFFFF with a
    Feature item) for VID:PID, or None. Raises if more than one such device is attached.
    """
    matches = []
    for sys_path in sorted(glob.glob(f"{sysfs}/hidraw*"), key=lambda p: int(p.rsplit("hidraw", 1)[1])):
        uevent_path = Path(sys_path) / "device" / "uevent"
        if not uevent_path.exists():
            continue
        vid = pid = None
        for line in uevent_path.read_text(errors="ignore").splitlines():
            if line.startswith("HID_ID="):
                parts = line.split("=")[1].split(":")
                if len(parts) == 3:
                    vid, pid = int(parts[1], 16), int(parts[2], 16)
        if vid != target_vid or pid != target_pid:
            continue
        rdesc_path = Path(sys_path) / "device" / "report_descriptor"
        rdesc = rdesc_path.read_bytes() if rdesc_path.exists() else b""
        if b"\x06\xff\xff" in rdesc and b"\xb1\x02" in rdesc:
            matches.append(Path("/dev") / Path(sys_path).name)

    if len(matches) > 1:
        raise RuntimeError(
            f"{len(matches)} devices {target_vid:04x}:{target_pid:04x} with a vendor Feature interface "
            f"({', '.join(map(str, matches))}): connect only one"
        )
    return matches[0] if matches else None


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

    def set_feature(self, payload64: bytes, retries: int = 5) -> None:
        """Send one Feature report. Use retries=1 for commands that are not idempotent."""
        assert len(payload64) == 64
        for attempt in range(retries):
            try:
                fcntl.ioctl(self.fd, HIDIOCSFEATURE(65), bytearray(b"\x00" + payload64))
                return
            except OSError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.015)

    def get_feature(self) -> bytes:
        for attempt in range(5):
            buf = bytearray(65)
            try:
                fcntl.ioctl(self.fd, HIDIOCGFEATURE(65), buf)
                return bytes(buf[1:65])
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.015)

    def poll_status(self, delay_s: float, tries: int) -> bytes:
        """GET_FEATURE until the status byte is OK or an error code."""
        resp = b""
        for _ in range(tries):
            time.sleep(delay_s)
            resp = self.get_feature()
            if resp[0] in STATUS_OK or resp[0] in STATUS_ERR:
                return resp
        return resp

    def command(self, payload64: bytes, delay_s: float = 0.020, retries: int = 5) -> bytes:
        """Idempotent request/response command (queries and reads)."""
        self.set_feature(payload64)
        return self.poll_status(delay_s, retries)


def _bl_request(length: int, opcode: int) -> bytearray:
    req = bytearray(64)
    req[3], req[4], req[5] = length, 0xB0, opcode
    return req


def query_app_fw_version(dev: CompxHidDevice) -> tuple[tuple[int, int, int, int], int]:
    """Send standard read-only Glorious FW Version query (0x00, 0x81) to 258a:2018."""
    req = bytearray(64)
    req[3], req[4], req[5] = 0x06, 0x00, 0x81
    resp = dev.command(bytes(req), delay_s=0.025)
    if resp[0] != 0xA1:
        raise RuntimeError(f"Unexpected FW version response status: 0x{resp[0]:02X} ({resp[:16].hex()})")
    return (resp[6], resp[7], resp[8], resp[9]), (resp[10] << 8) | resp[11]


def _send_reset_command(dev: CompxHidDevice, req: bytes, what: str) -> None:
    """Send a command after which the device resets. Only reset-style errors are tolerated."""
    try:
        dev.set_feature(req, retries=1)
    except OSError as e:
        if e.errno not in RESET_ERRNOS:
            raise
        print(f"         {what}: SET returned {errno.errorcode.get(e.errno, e.errno)} (expected if the device reset)")


def enter_bootloader_from_app(dev: CompxHidDevice) -> None:
    """
    Unverified app -> bootloader packet (feat[3]=0x02, feat[6]=0xB0, rest 0). The docs record a
    conflicting layout; neither has been checked against FirmwareUpdate.exe or hardware.
    The caller detects failure by waiting for 258a:2103.
    """
    req = bytearray(64)
    req[3], req[6] = 0x02, 0xB0
    _send_reset_command(dev, bytes(req), "SetBLDeviceMode")


def bl_get_version(bl: CompxHidDevice) -> bytes:
    """C52840Dongle::GetBLFW (cmd 0x80)."""
    resp = bl.command(bytes(_bl_request(0x06, 0x80)), delay_s=0.020)
    if resp[0] not in STATUS_OK:
        raise RuntimeError(f"GetBLFW failed: 0x{resp[0]:02X} ({resp[:16].hex()})")
    return resp[8:12]


def bl_read_32(bl: CompxHidDevice, address: int, length: int = BLOCK) -> bytes:
    """C52840Dongle::VerifyByAddress (cmd 0x83): read up to 32 bytes of flash (wire XOR 0x55)."""
    assert 1 <= length <= BLOCK
    req = _bl_request(0x20, 0x83)
    req[6] = length
    struct.pack_into(">I", req, 7, address)
    resp = bl.command(bytes(req), delay_s=0.015)
    if resp[0] not in STATUS_OK:
        raise RuntimeError(f"VerifyByAddress(0x{address:08X}) failed: status 0x{resp[0]:02X}")
    return bytes(b ^ XOR_WIRE_KEY for b in resp[11 : 11 + length])


def bl_erase_app(bl: CompxHidDevice) -> None:
    """C52840Dongle::Erase (cmd 0x01), sent once. Its extent is defined by the bootloader (unknown)."""
    sent_error = None
    try:
        bl.set_feature(bytes(_bl_request(0x08, 0x01)), retries=1)
    except OSError as e:
        sent_error = e
    resp = bl.poll_status(0.050, 40)
    if resp[0] not in STATUS_OK:
        raise RuntimeError(f"Erase failed or timed out (SET error: {sent_error}): last resp={resp[:16].hex()}")
    if bl_read_32(bl, APP_BASE_ADDR) != b"\xFF" * BLOCK:
        raise RuntimeError("Erase not confirmed: 0x00006000 does not read back as 0xFF")


def bl_program_32(bl: CompxHidDevice, address: int, chunk: bytes) -> None:
    """
    C52840Dongle::Program (cmd 0x02): write up to 32 bytes (wire XOR 0x55). Not resent blindly:
    after a SET error the block is read back; it is resent only if it is still fully erased.
    """
    assert 1 <= len(chunk) <= BLOCK
    req = _bl_request(len(chunk) + 5, 0x02)
    req[6] = len(chunk)
    struct.pack_into(">I", req, 7, address)
    req[11 : 11 + len(chunk)] = bytes(b ^ XOR_WIRE_KEY for b in chunk)

    for attempt in range(2):
        try:
            bl.set_feature(bytes(req), retries=1)
        except OSError as e:
            current = bl_read_32(bl, address, len(chunk))
            if current == chunk:
                return
            if attempt == 0 and current == b"\xFF" * len(chunk):
                continue
            raise RuntimeError(f"Program(0x{address:08X}) SET failed ({e}); block now reads {current.hex()}") from e
        resp = bl.poll_status(0.003, 20)
        if resp[0] in STATUS_OK:
            return
        raise RuntimeError(f"Program(0x{address:08X}) failed: last resp={resp[:16].hex()}")


def bl_exit_to_app(bl: CompxHidDevice) -> None:
    """C52840Dongle::Exit (cmd 0x04, mode=0x01): boot the application at 0x00006000."""
    req = _bl_request(0x01, 0x04)
    req[6] = 0x01
    _send_reset_command(bl, bytes(req), "Exit")


def wait_for_device(vid: int, pid: int, timeout_s: float = 10.0) -> Path:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        node = find_hidraw_feature_node(vid, pid)
        if node is not None and os.access(str(node), os.R_OK | os.W_OK):
            time.sleep(0.150)
            return node
        time.sleep(0.100)
    raise TimeoutError(f"Timed out waiting for USB HID device {vid:04x}:{pid:04x}")


def enter_bootloader() -> Path:
    """Return the bootloader node, switching an attached app-mode mouse into the bootloader."""
    bl_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_BL)
    if bl_node:
        return bl_node
    app_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_APP)
    if not app_node:
        raise RuntimeError("Neither 258a:2018 (app) nor 258a:2103 (bootloader) found")
    with CompxHidDevice(app_node) as dev:
        ver, pid = query_app_fw_version(dev)
        print(f"[ENTER] FW v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X}) at {app_node}; sending SetBLDeviceMode")
        enter_bootloader_from_app(dev)
    try:
        return wait_for_device(VID_SINOWEALTH, PID_MOUSE_BL, timeout_s=10.0)
    except TimeoutError:
        raise RuntimeError(
            "Mouse did not re-enumerate as 258a:2103. The entry packet is unverified: enter the "
            "bootloader with the official updater, then run this command again."
        ) from None


def read_back(bl: CompxHidDevice, image: bytes, start: int = 0, label: str = "Verified") -> int | None:
    """Compare flash with image[start:] block by block; return the first mismatching offset or None."""
    for offset in range(start, len(image), BLOCK):
        expected = image[offset : offset + BLOCK]
        if bl_read_32(bl, APP_BASE_ADDR + offset, len(expected)) != expected:
            return offset
        done = offset + len(expected)
        if done % 16384 == 0 or done == len(image):
            print(f"         {label} {done:6d} / {len(image)} bytes")
    return None


def load_image(hex_path: str) -> bytes:
    image, _sla, _records = compx_hex_tool.load_app_hex(Path(hex_path), APP_BASE_ADDR)
    return image


def cmd_probe(_args) -> int:
    app_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_APP)
    bl_node = find_hidraw_feature_node(VID_SINOWEALTH, PID_MOUSE_BL)
    if app_node:
        print(f"[PROBE] Wired mouse application ({VID_SINOWEALTH:04x}:{PID_MOUSE_APP:04x}) at {app_node}")
        with CompxHidDevice(app_node) as dev:
            ver, pid = query_app_fw_version(dev)
            print(f"        Firmware Version: v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X})")
    elif bl_node:
        print(f"[PROBE] Wired mouse bootloader ({VID_SINOWEALTH:04x}:{PID_MOUSE_BL:04x}) at {bl_node}")
        with CompxHidDevice(bl_node) as bl:
            print(f"        Bootloader Version Bytes: {bl_get_version(bl).hex()}")
    else:
        print("[ERROR] Neither 258a:2018 (App) nor 258a:2103 (Bootloader) found on USB.")
        return 1
    return 0


def cmd_verify_bl(args) -> int:
    image = load_image(args.reference_hex)
    end = APP_BASE_ADDR + len(image)
    bl_node = enter_bootloader()
    print(f"[VERIFY] Bootloader at {bl_node}")
    with CompxHidDevice(bl_node) as bl:
        print(f"         GetBLFW: {bl_get_version(bl).hex()}")
        print(f"         Reading back 0x{APP_BASE_ADDR:08X}..0x{end - 1:08X} ({len(image)} bytes)...")
        bad = read_back(bl, image, label="Matched")
        if bad is not None:
            print(f"[MISMATCH] at 0x{APP_BASE_ADDR + bad:08X}; staying in bootloader (258a:2103).")
            return 1
        print(f"[MATCH] 0x{APP_BASE_ADDR:08X}..0x{end - 1:08X} ({len(image)} bytes) equals {args.reference_hex}")
        if args.stay_in_bl:
            return 0
        print("         Sending Exit (0x04, mode=0x01)...")
        bl_exit_to_app(bl)
    app_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_APP, timeout_s=10.0)
    print(f"[VERIFY] Mouse back in application mode at {app_node}")
    return 0


def cmd_flash(args) -> int:
    image = load_image(args.firmware_hex)
    image += b"\xFF" * (-len(image) % BLOCK)
    audit = compx_hex_tool.audit_binary_safety(image, base_addr=APP_BASE_ADDR)
    if not audit["all_passed"]:
        failed = [k for k, v in audit["checks"].items() if not v]
        raise RuntimeError(f"Refusing to flash: safety audit failed: {failed}")
    print(f"[AUDIT] {Path(args.firmware_hex).name}: {len(image)} bytes, SHA256={audit['sha256'][:16]}..., all checks passed")

    bl_node = enter_bootloader()
    print(f"[FLASH] Bootloader at {bl_node}")
    with CompxHidDevice(bl_node) as bl:
        print(f"        GetBLFW: {bl_get_version(bl).hex()}")

        print("[1/4] Erase (extent defined by the bootloader)...")
        bl_erase_app(bl)

        print(f"[2/4] Programming 0x{APP_BASE_ADDR + BLOCK:08X}..0x{APP_BASE_ADDR + len(image) - 1:08X}...")
        for offset in range(BLOCK, len(image), BLOCK):
            bl_program_32(bl, APP_BASE_ADDR + offset, image[offset : offset + BLOCK])
            if (offset + BLOCK) % 4096 == 0:
                time.sleep(0.010)
        bad = read_back(bl, image, start=BLOCK)
        if bad is not None:
            raise RuntimeError(f"VERIFY MISMATCH at 0x{APP_BASE_ADDR + bad:08X}; vector table not written")

        print(f"[3/4] Programming vector table block 0x{APP_BASE_ADDR:08X} last...")
        bl_program_32(bl, APP_BASE_ADDR, image[:BLOCK])
        if bl_read_32(bl, APP_BASE_ADDR) != image[:BLOCK]:
            raise RuntimeError("VERIFY MISMATCH in vector table block; staying in bootloader")

        print("[4/4] All bytes verified; sending Exit (0x04, mode=0x01)...")
        bl_exit_to_app(bl)

    app_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_APP, timeout_s=10.0)
    print(f"[FLASH] Mouse enumerated as {VID_SINOWEALTH:04x}:{PID_MOUSE_APP:04x} at {app_node}")
    return 0


def cmd_dump(args) -> int:
    """Read-only backup: read flash through VerifyByAddress into an Intel HEX file."""
    start, end, out = args.start, args.end, Path(args.out)
    if start < APP_BASE_ADDR or start % BLOCK or not start < end <= FLASH_END:
        raise ValueError(f"need 0x{APP_BASE_ADDR:X} <= start < end <= 0x{FLASH_END:X}, start 32-byte aligned")
    if out.exists():
        raise ValueError(f"{out} exists; refusing to overwrite a backup")
    data = bytearray()
    bl_node = enter_bootloader()
    with CompxHidDevice(bl_node) as bl:
        print(f"[DUMP] Bootloader at {bl_node}, GetBLFW: {bl_get_version(bl).hex()}")
        for addr in range(start, end, BLOCK):
            try:
                data += bl_read_32(bl, addr, min(BLOCK, end - addr))
            except RuntimeError as e:
                print(f"[DUMP] Read refused at 0x{addr:08X} ({e}); keeping what was read")
                break
            if (addr + BLOCK - start) % 0x10000 == 0:
                print(f"       read up to 0x{addr + BLOCK:08X}")
        text = compx_hex_tool.encode_compx_hex(bytes(data), base_addr=start)
        if start != APP_BASE_ADDR:  # no vector table here: drop the derived start address
            text = "".join(line for line in text.splitlines(True) if not line.startswith(":04000005"))
        out.write_text(text)
        used = [start + o for o in range(0, len(data), 0x1000) if data[o : o + 0x1000].strip(b"\xFF")]
        print(f"[DUMP] Wrote {out}: 0x{start:08X}..0x{start + len(data) - 1:08X} ({len(data)} bytes), "
              f"SHA256={hashlib.sha256(data).hexdigest()}")
        print("       non-blank 4 KB pages: " + (" ".join(f"0x{p:05X}" for p in used) or "none"))
        if args.stay_in_bl:
            return 0
        print("         Sending Exit (0x04, mode=0x01)...")
        bl_exit_to_app(bl)
    app_node = wait_for_device(VID_SINOWEALTH, PID_MOUSE_APP, timeout_s=10.0)
    print(f"[DUMP] Mouse back in application mode at {app_node}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compx nRF52840 mouse USB HID bootloader tool (Linux hidraw)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="Read-only probe of the connected mouse")
    p = sub.add_parser("verify-bl", help="Enter bootloader, read back and compare the full reference image")
    p.add_argument("--reference-hex", required=True, help="Reference Intel HEX (lowest address 0x6000)")
    p.add_argument("--stay-in-bl", action="store_true", help="Do not send Exit even on a full match")
    p = sub.add_parser("flash", help="Program and verify an Intel HEX image (lowest address 0x6000)")
    p.add_argument("--firmware-hex", required=True, help="Intel HEX to flash, e.g. the vendor .hex for rollback")
    p = sub.add_parser("dump", help="Read-only backup of flash (default 0x6000..0xFFFFF) to an Intel HEX file")
    p.add_argument("--out", required=True, help="Output .hex (never overwritten)")
    p.add_argument("--start", type=lambda s: int(s, 0), default=APP_BASE_ADDR)
    p.add_argument("--end", type=lambda s: int(s, 0), default=FLASH_END, help="Exclusive end address")
    p.add_argument("--stay-in-bl", action="store_true", help="Do not send Exit afterwards")
    args = parser.parse_args()
    cmds = {"probe": cmd_probe, "verify-bl": cmd_verify_bl, "flash": cmd_flash, "dump": cmd_dump}
    return cmds[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
