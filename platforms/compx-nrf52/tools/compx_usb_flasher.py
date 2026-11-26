#!/usr/bin/env python3
"""
USB HID bootloader prober, verifier and programmer for Compx / Nordic nRF52840 mice
(Glorious Series One Pro: mouse app 258a:2018 / bootloader 258a:2103; receiver app 258a:2031 /
bootloader 258a:2113, read-only probe/dump/verify-bl via --target receiver, flash refused). Linux:
hidraw, standard library only. Windows and macOS: the PyPI package hidapi (pip install hidapi;
untested there). On Linux --backend hidapi uses hidapi's hidraw module, never its libusb one.

Implements the C52840Dongle USB HID Feature-report ISP protocol from FirmwareUpdate.exe:
  probe      read-only: report which mode the mouse is in and its version.
  verify-bl  read-only: enter the bootloader, read back the whole reference image with
             VerifyByAddress (0x83) and compare. Sends Exit only on a full match; on a
             mismatch or error the mouse stays in the bootloader and the exit code is 1.
  flash      program an Intel HEX image whose lowest address is 0x6000:
               1. offline checks: the safety audit (compx_hex_tool.audit_binary_safety), and the
                  image must end at or below 0x36800 (PROGRAM_TOP, below)
               2. refuse unless GetBLFW is b0 01 00 00, the only bootloader tested
               3. Erase (it changes no flash on this bootloader), program and verify everything
                  except the first 32-byte block, then the vector-table block at 0x6000
               4. Exit
  dump       backup: enter the bootloader and save what VerifyByAddress returns for 0x6000 up to
             where it is refused (0x4D800) to an Intel HEX file; run it before flash.

How the Series One Pro bootloader works (its code was read from a unit on 2026-11-19,
platforms/compx-nrf52/bootloader-protocol.md section 3): Program and VerifyByAddress act on the
address + 0x47800 (BANK_OFFSET), a second copy of the application at 0x4D800..0x94FFF. The running
application at 0x6000 is only replaced at Exit, which copies the second copy over it and then resets.
So an interrupted flash leaves the old application untouched, and an interrupted Exit is finished by the
bootloader at the next power-up. But the second copy overlaps the settings pages: an image byte at
0x6000 + n is stored at 0x4D800 + n, so an image reaching 0x36800 would overwrite the 0x7E000 page,
0x43800 the stock profile page 0x8B000, 0x44800 the macro directory 0x8C000. Hence PROGRAM_TOP. dump
reads the second copy; Exit after it copies the whole bank (every page was read), which rewrites the
application with identical bytes. The vector-table-last order is kept but no longer needed.

The app -> bootloader entry packet is the official updater's (see enter_bootloader_from_app) and
was tested on a retail unit. If the mouse does not re-enumerate as 258a:2103, use the official
updater or the open-directional-dpi patch's boot chord (verified 2026-11-13; the ZMK board's
is untested), then run this tool again (it uses an already-present 258a:2103 directly).
"""

import argparse
import errno
import glob
import hashlib
import os
import struct
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: hidapi backend only
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compx_hex_tool  # noqa: E402


def HIDIOCSFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def HIDIOCGFEATURE(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x07


VID_SINOWEALTH = 0x258A
PID_MOUSE_APP = 0x2018
PID_MOUSE_BL = 0x2103
PID_RECEIVER_APP = 0x2031
PID_RECEIVER_BL = 0x2113

APP_BASE_ADDR = compx_hex_tool.MOUSE_APP_BASE
RECEIVER_APP_BASE = compx_hex_tool.RECEIVER_APP_BASE
FLASH_END = 0x00100000  # nRF52840: 1 MiB
RECEIVER_FLASH_END = 0x00040000  # nRF52820 (inferred): 256 KiB
READ_TOP = 0x0004D800  # VerifyByAddress answers 0xA2 from here up (retail Series One Pro, 2026-11-13)
BANK_OFFSET = 0x47800  # Program / VerifyByAddress address + this = flash written / read (bootloader code, 2026-11-19)
PROGRAM_TOP = 0x7E000 - BANK_OFFSET  # 0x36800: higher image bytes would land on the 0x7E000 settings page
TESTED_BL_VERSION = bytes.fromhex("b0010000")  # GetBLFW of the only bootloader flashed so far (2026-11-13)
BLOCK = 32
XOR_WIRE_KEY = 0x55  # USB wire only; .hex files are plain
STATUS_OK = (0xA1, 0x02)
STATUS_ERR = (0xA2, 0xA3, 0xA4)
# errnos a SET may return when the device resets/detaches while handling it
RESET_ERRNOS = (errno.ENODEV, errno.EPIPE, errno.ESHUTDOWN, errno.EPROTO, errno.EIO, errno.ETIMEDOUT)
# Seen on a retail Series One Pro (2026-11-13): SetBLDeviceMode -> EPROTO, Exit -> ETIMEDOUT.

TARGETS = {
    "mouse": {
        "label": "Wired mouse",
        "app_pid": PID_MOUSE_APP,
        "bl_pid": PID_MOUSE_BL,
        "app_base": APP_BASE_ADDR,
        "flash_end": FLASH_END,
    },
    "receiver": {
        "label": "2.4 GHz receiver",
        "app_pid": PID_RECEIVER_APP,
        "bl_pid": PID_RECEIVER_BL,
        "app_base": RECEIVER_APP_BASE,
        "flash_end": RECEIVER_FLASH_END,
    },
}


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


class HidrawTransport:
    """Linux /dev/hidrawN through HIDIOC[SG]FEATURE, standard library only. The default on Linux."""

    def find(self, vid: int, pid: int) -> Path | None:
        return find_hidraw_feature_node(vid, pid)

    def ready(self, node: Path) -> bool:
        return os.access(str(node), os.R_OK | os.W_OK)

    def open(self, node: Path):
        return os.open(str(node), os.O_RDWR)

    def close(self, fd) -> None:
        os.close(fd)

    def set_feature(self, fd, payload64: bytes) -> None:
        fcntl.ioctl(fd, HIDIOCSFEATURE(65), bytearray(b"\x00" + payload64))

    def get_feature(self, fd) -> bytes:
        buf = bytearray(65)
        fcntl.ioctl(fd, HIDIOCGFEATURE(65), buf)
        return bytes(buf[1:65])


class HidapiTransport:
    """
    cython-hidapi (PyPI hidapi): `hid` on Windows and macOS, `hidraw` on Linux. Its errors carry no errno,
    so they are raised as EIO, one of RESET_ERRNOS: after SetBLDeviceMode and Exit, as with hidraw's EPROTO
    and ETIMEDOUT, waiting for the new PID decides whether the reset happened.
    """

    def __init__(self, hid):
        self.hid = hid

    def find(self, vid: int, pid: int) -> str | None:
        """
        The collection with Usage Page 0xFFFF, Usage 0x00: the app's Feature interface 2 (hardware.md 2.2).
        Interface 1 also has a 0xFFFF collection, with Usage 0x01 and input reports only. hidapi lists every
        top-level collection; on Windows each has its own path. Raises if more than one device matches.
        The bootloader (258a:2103) has the same pair on its only interface (sysfs report descriptor, 2026-11-18).
        """
        found = self.hid.enumerate(vid, pid)
        paths = sorted({d["path"] for d in found if (d["usage_page"], d["usage"]) == (0xFFFF, 0x00)})
        if len(paths) > 1:
            raise RuntimeError(f"{len(paths)} devices {vid:04x}:{pid:04x} with a vendor Feature interface: "
                               "connect only one")
        return paths[0].decode() if paths else None

    def ready(self, node: str) -> bool:
        try:
            self.close(self.open(node))
            return True
        except OSError:
            return False

    def open(self, node: str):
        dev = self.hid.device()
        try:
            dev.open_path(node.encode())
        except OSError:
            raise OSError(errno.EIO, f"cannot open {node}: {dev.error()}") from None
        return dev

    def close(self, dev) -> None:
        dev.close()

    def set_feature(self, dev, payload64: bytes) -> None:
        if dev.send_feature_report(bytes([0]) + payload64) < 0:  # returns -1, does not raise
            raise OSError(errno.EIO, f"send_feature_report: {dev.error()}")

    def get_feature(self, dev) -> bytes:
        try:
            report = bytes(dev.get_feature_report(0, 65))  # report ID 0 first, then the 64 bytes
        except OSError:
            report = b""
        if len(report) < 65:
            raise OSError(errno.EIO, f"get_feature_report returned {len(report)} bytes: {dev.error()}")
        return report[1:65]


def hidapi_module():
    """cython-hidapi's hidraw backend on Linux (its `hid` module there is libusb), else its `hid` module."""
    try:
        if sys.platform.startswith("linux"):
            import hidraw as hid
        else:
            import hid
    except ImportError:
        raise SystemExit("the hidapi backend needs the PyPI package hidapi: pip install hidapi") from None
    if not hasattr(hid, "device"):
        raise SystemExit(f"{hid.__name__} is not the PyPI package hidapi (pip uninstall hid; pip install hidapi)")
    return hid


transport = HidrawTransport()  # main() sets the --backend one


class CompxHidDevice:
    def __init__(self, node):
        self.node = node
        self.handle = transport.open(node)

    def close(self):
        if self.handle is not None:
            transport.close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def set_feature(self, payload64: bytes, retries: int = 5) -> None:
        """Send one Feature report. Use retries=1 for commands that are not idempotent."""
        assert len(payload64) == 64
        for attempt in range(retries):
            try:
                transport.set_feature(self.handle, payload64)
                return
            except OSError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.015)

    def get_feature(self) -> bytes:
        for attempt in range(5):
            try:
                return transport.get_feature(self.handle)
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
    """Send standard read-only Glorious FW Version query (0x00, 0x81) to app mode (258a:2018 or 258a:2031)."""
    req = bytearray(64)
    req[3], req[4], req[5] = 0x06, 0x00, 0x81
    resp = dev.command(bytes(req), delay_s=0.025)
    if resp[0] != 0xA1:
        raise RuntimeError(f"Unexpected FW version response status: 0x{resp[0]:02X} ({resp[:16].hex()})")
    return (resp[6], resp[7], resp[8], resp[9]), (resp[10] << 8) | resp[11]


def query_receiver_link_state(dev: CompxHidDevice) -> int:
    """Read-only receiver-local link state query (0x00, 0x89) on 258a:2031: 1 = linked, 0 = unlinked."""
    req = bytearray(64)
    req[5] = 0x89
    resp = dev.command(bytes(req), delay_s=0.010)
    if resp[0] != 0xA1:
        raise RuntimeError(f"Unexpected link state response status: 0x{resp[0]:02X} ({resp[:16].hex()})")
    return resp[6]


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
    App -> bootloader: "set device mode" (cmd 0x00, length 1) with mode 0xB0. Verified from the IL of
    FirmwareUpdate.exe 1.0.8229.18185 (C52840Dongle::SetBLDeviceMode): buf[3]=deviceID (0 for the
    wired mouse, Config.ini [Debug] DeviceID), buf[4]=0x01, buf[7]=0xB0, rest 0; buf[0] is the
    report ID, so feat[i] = buf[i+1]. The caller detects failure by waiting for 258a:2103.
    """
    req = bytearray(64)
    req[3], req[6] = 0x01, 0xB0
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
    """
    C52840Dongle::Erase (cmd 0x01), sent once, exactly as the updater does. On the Series One Pro bootloader it
    only drops the pending page buffer and changes no flash (bootloader code, 2026-11-19): Program is a
    read-modify-write of whole 4 KB pages, so no erase is needed.
    """
    sent_error = None
    try:
        bl.set_feature(bytes(_bl_request(0x08, 0x01)), retries=1)
    except OSError as e:
        sent_error = e
    resp = bl.poll_status(0.050, 40)
    if resp[0] not in STATUS_OK:
        raise RuntimeError(f"Erase failed or timed out (SET error: {sent_error}): last resp={resp[:16].hex()}")


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


def wait_for_device(vid: int, pid: int, timeout_s: float = 10.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        node = transport.find(vid, pid)
        if node is not None and transport.ready(node):
            time.sleep(0.150)
            return node
        time.sleep(0.100)
    raise TimeoutError(f"Timed out waiting for USB HID device {vid:04x}:{pid:04x}")


def _target_cfg(args) -> tuple[str, dict]:
    name = getattr(args, "target", "mouse") or "mouse"
    return name, TARGETS[name]


def enter_bootloader(target: str = "mouse", allow_receiver_bl: bool = False):
    """Return the bootloader node, switching an attached app-mode device into its bootloader."""
    cfg = TARGETS[target]
    app_pid, bl_pid = cfg["app_pid"], cfg["bl_pid"]
    bl_node = transport.find(VID_SINOWEALTH, bl_pid)
    if bl_node:
        return bl_node
    app_node = transport.find(VID_SINOWEALTH, app_pid)
    if not app_node:
        raise RuntimeError(
            f"Neither {VID_SINOWEALTH:04x}:{app_pid:04x} (app) nor {VID_SINOWEALTH:04x}:{bl_pid:04x} (bootloader) found"
        )
    if target == "receiver" and not allow_receiver_bl:
        raise RuntimeError(
            f"Refusing to switch receiver ({VID_SINOWEALTH:04x}:{app_pid:04x}) into untested bootloader "
            f"{VID_SINOWEALTH:04x}:{bl_pid:04x} without --allow-receiver-bl (receiver has no physical recovery chord)"
        )
    with CompxHidDevice(app_node) as dev:
        ver, pid = query_app_fw_version(dev)
        print(f"[ENTER] FW v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X}) at {app_node}; sending SetBLDeviceMode")
        enter_bootloader_from_app(dev)
    try:
        return wait_for_device(VID_SINOWEALTH, bl_pid, timeout_s=10.0)
    except TimeoutError:
        listed = transport.hid.enumerate(VID_SINOWEALTH, bl_pid) if isinstance(transport, HidapiTransport) else []
        if listed:  # it did re-enumerate; find() wants usage 0xFFFF/0x00, which this bootloader did not list
            usages = ", ".join(sorted({f"0x{d['usage_page']:04X}/0x{d['usage']:02X}" for d in listed}))
            end_hex = f"0x{cfg['app_base'] + BLOCK:x}"
            raise RuntimeError(
                f"The {cfg['label'].lower()} is in its bootloader ({VID_SINOWEALTH:04x}:{bl_pid:04x}), but hidapi "
                f"lists no collection with Usage Page 0xFFFF, Usage 0x00 that opens (listed: {usages}). Nothing was "
                f"written. To return to the application, run `compx_usb_flasher.py --target {target} dump --out "
                f"exit.hex --end {end_hex}` on Linux (default hidraw backend), and report the usages above in an issue."
            ) from None
        raise RuntimeError(
            f"{cfg['label']} did not re-enumerate as {VID_SINOWEALTH:04x}:{bl_pid:04x}. Enter the "
            "bootloader with the official updater, then run this command again."
        ) from None


def read_back(bl: CompxHidDevice, image: bytes, start: int = 0, label: str = "Verified",
              app_base: int = APP_BASE_ADDR) -> int | None:
    """Compare flash with image[start:] block by block; return the first mismatching offset or None."""
    for offset in range(start, len(image), BLOCK):
        expected = image[offset : offset + BLOCK]
        if bl_read_32(bl, app_base + offset, len(expected)) != expected:
            return offset
        done = offset + len(expected)
        if done % 16384 == 0 or done == len(image):
            print(f"         {label} {done:6d} / {len(image)} bytes")
    return None


def load_image(hex_path: str, app_base: int = APP_BASE_ADDR) -> bytes:
    image, _sla, _records = compx_hex_tool.load_app_hex(Path(hex_path), app_base)
    return image


def cmd_probe(args) -> int:
    target, cfg = _target_cfg(args)
    app_pid, bl_pid = cfg["app_pid"], cfg["bl_pid"]
    app_node = transport.find(VID_SINOWEALTH, app_pid)
    bl_node = transport.find(VID_SINOWEALTH, bl_pid)
    if app_node:
        print(f"[PROBE] {cfg['label']} application ({VID_SINOWEALTH:04x}:{app_pid:04x}) at {app_node}")
        with CompxHidDevice(app_node) as dev:
            ver, pid = query_app_fw_version(dev)
            print(f"        Firmware Version: v{ver[0]}.{ver[1]}.{ver[2]}.{ver[3]} (PID=0x{pid:04X})")
            if target == "receiver":
                linked = query_receiver_link_state(dev)
                print(f"        Mouse Link State (0x89): {'linked (1)' if linked else 'unlinked (0)'}")
    elif bl_node:
        print(f"[PROBE] {cfg['label']} bootloader ({VID_SINOWEALTH:04x}:{bl_pid:04x}) at {bl_node}")
        with CompxHidDevice(bl_node) as bl:
            print(f"        Bootloader Version Bytes: {bl_get_version(bl).hex()}")
    else:
        print(f"[ERROR] Neither {VID_SINOWEALTH:04x}:{app_pid:04x} (App) nor {VID_SINOWEALTH:04x}:{bl_pid:04x} "
              "(Bootloader) found on USB.")
        return 1
    return 0


def cmd_verify_bl(args) -> int:
    target, cfg = _target_cfg(args)
    app_base, app_pid, bl_pid = cfg["app_base"], cfg["app_pid"], cfg["bl_pid"]
    image = load_image(args.reference_hex, app_base)
    end = app_base + len(image)
    bl_node = enter_bootloader(target, allow_receiver_bl=getattr(args, "allow_receiver_bl", False))
    print(f"[VERIFY] Bootloader at {bl_node}")
    with CompxHidDevice(bl_node) as bl:
        print(f"         GetBLFW: {bl_get_version(bl).hex()}")
        print(f"         Reading back 0x{app_base:08X}..0x{end - 1:08X} ({len(image)} bytes)...")
        bad = read_back(bl, image, label="Matched", app_base=app_base)
        if bad is not None:
            print(f"[MISMATCH] at 0x{app_base + bad:08X}; staying in bootloader ({VID_SINOWEALTH:04x}:{bl_pid:04x}).")
            return 1
        print(f"[MATCH] 0x{app_base:08X}..0x{end - 1:08X} ({len(image)} bytes) equals {args.reference_hex}")
        if args.stay_in_bl:
            return 0
        print("         Sending Exit (0x04, mode=0x01)...")
        bl_exit_to_app(bl)
    app_node = wait_for_device(VID_SINOWEALTH, app_pid, timeout_s=10.0)
    print(f"[VERIFY] {cfg['label']} back in application mode at {app_node}")
    return 0


def cmd_flash(args) -> int:
    target, _cfg = _target_cfg(args)
    if target != "mouse":
        raise RuntimeError(
            "Refusing to flash receiver: receiver bootloader (258a:2113) Erase extent and bank offset have not "
            "been verified on hardware yet (no tool here flashes the receiver)"
        )
    image = load_image(args.firmware_hex, APP_BASE_ADDR)
    image += b"\xFF" * (-len(image) % BLOCK)
    audit = compx_hex_tool.audit_binary_safety(image, base_addr=APP_BASE_ADDR)
    if not audit["all_passed"]:
        failed = [k for k, v in audit["checks"].items() if not v]
        raise RuntimeError(f"Refusing to flash: safety audit failed: {failed}")
    end = APP_BASE_ADDR + len(image)
    if end > PROGRAM_TOP:
        raise RuntimeError(f"Refusing to flash: image ends at 0x{end:08X}, above 0x{PROGRAM_TOP:08X}; the "
                           "bootloader stores it at +0x47800, where it would overwrite the settings pages")
    print(f"[AUDIT] {Path(args.firmware_hex).name}: {len(image)} bytes, SHA256={audit['sha256'][:16]}..., all checks passed")

    bl_node = enter_bootloader("mouse")
    print(f"[FLASH] Bootloader at {bl_node}")
    with CompxHidDevice(bl_node) as bl:
        blfw = bl_get_version(bl)
        print(f"        GetBLFW: {blfw.hex()}")
        if blfw != TESTED_BL_VERSION:
            raise RuntimeError(f"Refusing to flash: untested bootloader (GetBLFW {blfw.hex()}, tested "
                               f"{TESTED_BL_VERSION.hex()}); nothing was erased or written. The mouse stays in "
                               "its bootloader (258a:2103); to return to the application, run the official "
                               "updater, or `compx_usb_flasher.py dump --out exit.hex --end 0x6020` (reads 32 "
                               "bytes, then sends Exit)")

        print("[1/4] Erase (on this bootloader: drops its page buffer, writes nothing)...")
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
    target, cfg = _target_cfg(args)
    app_base, flash_end, app_pid = cfg["app_base"], cfg["flash_end"], cfg["app_pid"]
    start = app_base if args.start is None else args.start
    end = flash_end if args.end is None else args.end
    out = Path(args.out)
    if start < app_base or start % BLOCK or not start < end <= flash_end:
        raise ValueError(f"need 0x{app_base:X} <= start < end <= 0x{flash_end:X}, start 32-byte aligned")
    if out.exists():
        raise ValueError(f"{out} exists; refusing to overwrite a backup")
    data = bytearray()
    bl_node = enter_bootloader(target, allow_receiver_bl=getattr(args, "allow_receiver_bl", False))
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
        if start != app_base:  # no vector table here: drop the derived start address
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
    app_node = wait_for_device(VID_SINOWEALTH, app_pid, timeout_s=10.0)
    print(f"[DUMP] {cfg['label']} back in application mode at {app_node}")
    return 0


def main() -> int:
    global transport
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    linux = sys.platform.startswith("linux")
    parser.add_argument("--backend", choices=("hidraw", "hidapi") if linux else ("hidapi",),
                        default="hidraw" if linux else "hidapi", help="USB HID access (default: %(default)s)")
    parser.add_argument("--target", choices=TARGETS, default="mouse",
                        help="mouse (258a:2018/2103, base 0x6000) or receiver (258a:2031/2113, read-only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="Read-only probe of the connected device")
    p = sub.add_parser("verify-bl", help="Enter bootloader, read back and compare the full reference image")
    p.add_argument("--reference-hex", required=True, help="Reference Intel HEX (lowest address = app base)")
    p.add_argument("--stay-in-bl", action="store_true", help="Do not send Exit even on a full match")
    p.add_argument("--allow-receiver-bl", action="store_true",
                   help="Required to switch receiver 258a:2031 into untested bootloader 258a:2113")
    p = sub.add_parser("flash", help="Program and verify a mouse Intel HEX image (0x6000 up to at most 0x36800)")
    p.add_argument("--firmware-hex", required=True, help="Intel HEX to flash, e.g. the vendor .hex for rollback")
    p = sub.add_parser("dump", help="Read-only backup of flash (default app base..flash end) to an Intel HEX file")
    p.add_argument("--out", required=True, help="Output .hex (never overwritten)")
    p.add_argument("--start", type=lambda s: int(s, 0), default=None)
    p.add_argument("--end", type=lambda s: int(s, 0), default=None, help="Exclusive end address")
    p.add_argument("--stay-in-bl", action="store_true", help="Do not send Exit afterwards")
    p.add_argument("--allow-receiver-bl", action="store_true",
                   help="Required to switch receiver 258a:2031 into untested bootloader 258a:2113")
    args = parser.parse_args()
    if args.backend == "hidapi":
        transport = HidapiTransport(hidapi_module())
    cmds = {"probe": cmd_probe, "verify-bl": cmd_verify_bl, "flash": cmd_flash, "dump": cmd_dump}
    return cmds[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
