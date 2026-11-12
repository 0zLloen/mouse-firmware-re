#!/usr/bin/env python3
"""
Offline checks for compx_hex_tool.py and compx_usb_flasher.py. No hardware is touched:
the flasher's os/fcntl/time/device lookup are replaced by a mock bootloader.
Run: python3 test_tools.py   (cross-checks against the `intelhex` package if installed)
"""

import errno
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compx_hex_tool as hx  # noqa: E402
import compx_usb_flasher as fl  # noqa: E402

BASE = 0x6000


def make_image(size=0x1000, sp=0x20008000, reset=BASE + 0x201, magic=True) -> bytes:
    img = bytearray((i * 7 + 3) & 0xFF for i in range(size))
    struct.pack_into("<II", img, 0, sp, reset)
    if magic:
        struct.pack_into("<I", img, 0x400, hx.BOOTLOADER_MAGIC_VAL)
    return bytes(img)


def rec(addr16, rtype, data=b""):
    body = bytes([len(data), addr16 >> 8, addr16 & 0xFF, rtype]) + data
    return ":" + body.hex().upper() + f"{-sum(body) & 0xFF:02X}"


def expect_error(text, fragment):
    try:
        hx.parse_compx_hex(text)
    except ValueError as e:
        assert fragment in str(e), (fragment, str(e))
        return
    raise AssertionError(f"accepted malformed hex ({fragment})")


def test_hex_tool():
    img = make_image()
    text = hx.encode_compx_hex(img, BASE)
    data, lo, hi, sla, _ = hx.parse_compx_hex(text)
    assert (data, lo, hi, sla) == (img, BASE, BASE + len(img), BASE + 0x201)

    lines = text.splitlines()
    eof = ":00000001FF"
    expect_error("\n".join(lines[:-1]), "missing EOF")
    expect_error("\n".join(lines[:5] + [lines[5][1:]] + lines[6:]), "does not start with ':'")
    expect_error(lines[1][:-4] + lines[1][-2:] + "\n" + eof, "length")
    expect_error(lines[1][:-2] + "00\n" + eof, "checksum")
    expect_error(rec(0, 0x06, b"\x00") + "\n" + eof, "unknown record type")
    expect_error(rec(0, 0x04, b"\x00") + "\n" + eof, "needs 2")
    expect_error(rec(0, 0x00, b"\x01\x02") + "\n" + rec(1, 0x00, b"\x03") + "\n" + eof, "written twice")
    expect_error(text + rec(0, 0x00, b"\x01") + "\n", "after EOF")

    # type 02 (Extended Segment Address, GNU objcopy): segment 0x1000 -> 0x10000
    data, lo, *_ = hx.parse_compx_hex(rec(0, 0x02, b"\x10\x00") + "\n" + rec(0x10, 0x00, b"\xAA\xBB") + "\n" + eof)
    assert (data, lo) == (b"\xAA\xBB", 0x10010)

    ok = lambda b, base=BASE, app=BASE: hx.audit_binary_safety(b, base, app)["all_passed"]  # noqa: E731
    assert ok(img)
    assert not ok(img, base=0x7000)  # vector table not at the mouse app base
    assert ok(make_image(reset=0x7201), base=0x7000, app=hx.RECEIVER_APP_BASE)
    assert not ok(make_image(sp=0x20000000))
    assert not ok(make_image(sp=0x20008004))
    assert not ok(make_image(reset=BASE + 1))  # inside the vector table
    assert not ok(make_image(magic=False))
    assert not ok(make_image(size=0x79000))  # ends above 0x7E000

    try:
        from intelhex import IntelHex
    except ImportError:
        print("intelhex not installed: cross-check skipped")
        return
    ih = IntelHex()
    ih.frombytes(img, offset=BASE)
    ih.start_addr = {"EIP": BASE + 0x201}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.hex"
        ih.write_hex_file(str(p))
        data, lo, hi, sla, _ = hx.parse_compx_hex(p)
        assert (data, lo, sla) == (img, BASE, BASE + 0x201)
        p.write_text(text)
        assert IntelHex(str(p)).tobinstr() == img
    print("intelhex cross-check passed")


class MockDevice:
    """Compx mouse in app (258a:2018) or bootloader (258a:2103) mode, at the ioctl level."""

    def __init__(self, mode="bl", old=b"", die_after=None, glitch_on=None, honor_entry=True):
        self.mode, self.flash = mode, bytearray(b"\xFF" * 0x100000)
        self.flash[BASE : BASE + len(old)] = old
        self.die_after, self.glitch_on, self.honor_entry = die_after, glitch_on, honor_entry
        self.programs, self.ioctls, self.resp, self.clock = [], 0, bytes(64), 0.0

    def node(self, _vid, pid):
        return {("app", 0x2018): Path("/dev/mock-app"), ("bl", 0x2103): Path("/dev/mock-bl")}.get((self.mode, pid))

    def open(self, path, _flags):
        if Path(path) not in (self.node(0, 0x2018), self.node(0, 0x2103)):
            raise FileNotFoundError(path)
        return 1000

    def ioctl(self, _fd, req, buf):
        self.ioctls += 1
        assert len(buf) == 65 and buf[0] == 0
        if self.mode == "gone":
            raise OSError(errno.ENODEV, "gone")
        if req == fl.HIDIOCGFEATURE(65):
            buf[1:65] = self.resp
            return 0
        assert req == fl.HIDIOCSFEATURE(65)
        self.on_set(bytes(buf[1:]))
        return 0

    def ok(self, payload=b""):
        r = bytearray(64)
        r[0] = 0xA1
        r[11 : 11 + len(payload)] = payload
        self.resp = bytes(r)

    def on_set(self, f):
        if self.mode == "app":
            if f[3] == 0x02 and f[6] == 0xB0 and self.honor_entry:
                self.mode = "bl"
                raise OSError(errno.EPIPE, "reset")
            if f[5] == 0x81:
                r = bytearray(64)
                r[0], r[6:12] = 0xA1, bytes([1, 0, 0, 0, 0x20, 0x18])
                self.resp = bytes(r)
            return
        assert f[4] == 0xB0
        op, n, addr = f[5], f[6], struct.unpack_from(">I", f, 7)[0]
        if op == 0x80:
            r = bytearray(64)
            r[0], r[8:12] = 0xA1, b"\xB0\x01\x00\x00"
            self.resp = bytes(r)
        elif op == 0x01:
            self.flash[BASE:0x80000] = b"\xFF" * (0x80000 - BASE)
            self.ok()
        elif op == 0x02:
            if self.die_after is not None and len(self.programs) == self.die_after:
                self.mode = "gone"
                raise OSError(errno.ENODEV, "unplugged")
            self.programs.append(addr)
            for i in range(n):
                self.flash[addr + i] &= f[11 + i] ^ 0x55  # NOR flash: program only clears bits
            self.ok()
            if len(self.programs) == self.glitch_on:
                raise OSError(errno.ETIMEDOUT, "status stage timeout")
        elif op == 0x83:
            self.ok(bytes(b ^ 0x55 for b in self.flash[addr : addr + n]))
        elif op == 0x04:
            self.mode = "app"
            raise OSError(errno.EPIPE, "reset")

    def install(self):
        def sleep(s):
            self.clock += s

        fl.os = SimpleNamespace(open=self.open, close=lambda fd: None, access=lambda *a: True, O_RDWR=2, R_OK=4, W_OK=2)
        fl.fcntl = SimpleNamespace(ioctl=self.ioctl)
        fl.time = SimpleNamespace(sleep=sleep, time=lambda: self.clock)
        fl.find_hidraw_feature_node = self.node
        return self


def run(fn, **kw):
    try:
        return fn(SimpleNamespace(**kw))
    except (RuntimeError, ValueError, OSError) as e:
        return e


def test_flasher(tmp: Path):
    img = make_image(size=0x1010)
    padded = img + b"\xFF" * (-len(img) % 32)
    good = tmp / "good.hex"
    good.write_text(hx.encode_compx_hex(img, BASE))
    old = make_image(size=0x3000, sp=0x2000AF50)

    m = MockDevice(mode="app", old=old).install()
    assert run(fl.cmd_flash, firmware_hex=str(good)) == 0
    assert m.mode == "app" and m.flash[BASE : BASE + len(padded)] == padded
    assert m.programs[0] == BASE + 32 and m.programs[-1] == BASE, "vector table must be programmed last"
    assert len(m.programs) == len(set(m.programs)) == len(padded) // 32

    m = MockDevice(mode="bl", old=old, die_after=40).install()
    assert isinstance(run(fl.cmd_flash, firmware_hex=str(good)), OSError)
    assert m.flash[BASE : BASE + 32] == b"\xFF" * 32, "interrupted flash must leave 0x6000 erased"

    m = MockDevice(mode="bl", glitch_on=5).install()
    assert run(fl.cmd_flash, firmware_hex=str(good)) == 0
    assert len(m.programs) == len(set(m.programs)), "a SET error must not cause a blind resend"

    for bad in (hx.encode_compx_hex(make_image(reset=0x7201), 0x7000), hx.encode_compx_hex(make_image(magic=False), BASE)):
        (tmp / "bad.hex").write_text(bad)
        m = MockDevice(mode="bl").install()
        assert isinstance(run(fl.cmd_flash, firmware_hex=str(tmp / "bad.hex")), (RuntimeError, ValueError))
        assert m.ioctls == 0, "rejected images must not reach the device"

    corrupt = bytearray(padded)
    corrupt[0x0F00] ^= 1  # far beyond the first 256 bytes
    m = MockDevice(mode="bl", old=bytes(corrupt)).install()
    assert run(fl.cmd_verify_bl, reference_hex=str(good), stay_in_bl=False) == 1
    assert m.mode == "bl", "mismatch must keep the mouse in the bootloader"

    m = MockDevice(mode="app", old=padded).install()
    assert run(fl.cmd_verify_bl, reference_hex=str(good), stay_in_bl=False) == 0 and m.mode == "app"

    m = MockDevice(mode="app", honor_entry=False).install()
    err = run(fl.cmd_flash, firmware_hex=str(good))
    assert isinstance(err, RuntimeError) and "official updater" in str(err)

    m = MockDevice(mode="app", old=old).install()
    m.flash[0x88000:0x88020] = bytes(range(32))  # pairing page must be captured
    out = tmp / "backup.hex"
    assert run(fl.cmd_dump, out=str(out), start=BASE, end=0x100000, stay_in_bl=False) == 0 and m.mode == "app"
    data, lo, *_ = hx.parse_compx_hex(out)
    assert lo == BASE and data == bytes(m.flash[BASE:0x100000]), "dump must equal flash"
    assert isinstance(run(fl.cmd_dump, out=str(out), start=BASE, end=0x100000, stay_in_bl=False), ValueError)
    assert not any(m.programs), "dump must never program"


def test_interface_selection(tmp: Path):
    import importlib

    find = importlib.reload(fl).find_hidraw_feature_node  # undo MockDevice.install patches
    vendor = bytes.fromhex("06ffff0900a10109001500250175089540b102c0")

    def node(n, rdesc, pid="2018"):
        d = tmp / f"hidraw{n}" / "device"
        d.mkdir(parents=True)
        (d / "uevent").write_text(f"HID_ID=0003:0000258A:0000{pid}\n")
        (d / "report_descriptor").write_bytes(rdesc)

    node(2, b"\x05\x01\x09\x02")
    assert find(0x258A, 0x2018, str(tmp)) is None, "no fallback to a non-vendor interface"
    node(10, vendor)
    assert find(0x258A, 0x2018, str(tmp)) == Path("/dev/hidraw10")
    node(9, vendor)
    try:
        find(0x258A, 0x2018, str(tmp))
        raise AssertionError("two mice must be refused")
    except RuntimeError:
        pass


if __name__ == "__main__":
    test_hex_tool()
    with tempfile.TemporaryDirectory() as d:
        test_flasher(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_interface_selection(Path(d))
    print("all checks passed")
