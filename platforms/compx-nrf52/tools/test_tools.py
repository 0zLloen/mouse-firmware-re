#!/usr/bin/env python3
"""
Offline checks for compx_hex_tool.py and compx_usb_flasher.py. No hardware is touched:
the flasher's os/fcntl/time/device lookup, or a fake hidapi module, talk to a mock bootloader.
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
BACKEND = "hidraw"  # test_flasher runs once per backend


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
    try:
        ok(make_image(reset=0x7201), base=0x7000, app=hx.RECEIVER_APP_BASE)
        raise AssertionError("receiver audit must refuse: its limits are unknown")
    except ValueError:
        pass
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
    """
    Compx mouse in app (258a:2018) or bootloader (258a:2103) mode, at the ioctl level, modelled on a
    retail Series One Pro (2026-11-13): GetBLFW b0 01 00 00; Erase keeps 0x6000..0x1DFFF and 0x7E000 up
    (0x4D800..0x7DFFF is unreadable, assumed erased); VerifyByAddress answers 0xA2 from 0x4D800 up;
    Program is read-modify-write.
    """

    def __init__(self, mode="bl", old=b"", die_after=None, glitch_on=None, honor_entry=True, blfw=b"\xB0\x01\x00\x00"):
        self.mode, self.flash = mode, bytearray(b"\xFF" * 0x100000)
        self.flash[BASE : BASE + len(old)] = old
        self.die_after, self.glitch_on, self.honor_entry, self.blfw = die_after, glitch_on, honor_entry, blfw
        self.programs, self.ops, self.ioctls, self.resp, self.clock = [], [], 0, bytes(64), 0.0

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

    def ok(self, payload=b"", status=0xA1):
        r = bytearray(64)
        r[0] = status
        r[11 : 11 + len(payload)] = payload
        self.resp = bytes(r)

    def on_set(self, f):
        if self.mode == "app":
            if f[3] == 0x01 and f[6] == 0xB0 and not any(f[4:6]) and self.honor_entry:
                self.mode = "bl"
                raise OSError(errno.EPIPE, "reset")
            if f[5] == 0x81:
                r = bytearray(64)
                r[0], r[6:12] = 0xA1, bytes([1, 0, 0, 0, 0x20, 0x18])
                self.resp = bytes(r)
            return
        assert f[4] == 0xB0
        op, n, addr = f[5], f[6], struct.unpack_from(">I", f, 7)[0]
        self.ops.append(op)
        if op == 0x80:
            r = bytearray(64)
            r[0], r[8:12] = 0xA1, self.blfw
            self.resp = bytes(r)
        elif op == 0x01:
            self.flash[0x1E000:0x7E000] = b"\xFF" * (0x7E000 - 0x1E000)  # the old image head and 0x7E000 stay
            self.ok()
        elif op == 0x02:
            if self.die_after is not None and len(self.programs) == self.die_after:
                self.mode = "gone"
                raise OSError(errno.ENODEV, "unplugged")
            self.programs.append(addr)
            for i in range(n):
                self.flash[addr + i] = f[11 + i] ^ 0x55  # read-modify-write: exact bytes, rest of page kept
            self.ok()
            if len(self.programs) == self.glitch_on:
                raise OSError(errno.ETIMEDOUT, "status stage timeout")
        elif op == 0x83:
            if addr >= 0x4D800:  # the hardware limit (verified 2026-11-13), not fl.READ_TOP
                self.ok(status=0xA2)
            else:
                self.ok(bytes(b ^ 0x55 for b in self.flash[addr : addr + n]))
        elif op == 0x04:
            self.mode = "app"
            raise OSError(errno.EPIPE, "reset")

    def install(self, backend=None):
        def sleep(s):
            self.clock += s

        fl.time = SimpleNamespace(sleep=sleep, time=lambda: self.clock)
        if (backend or BACKEND) == "hidraw":
            fl.os = SimpleNamespace(open=self.open, close=lambda fd: None, access=lambda *a: True, O_RDWR=2, R_OK=4, W_OK=2)
            fl.fcntl = SimpleNamespace(ioctl=self.ioctl)
            fl.find_hidraw_feature_node = self.node
            fl.transport = fl.HidrawTransport()
        else:
            fl.os = fl.fcntl = fl.find_hidraw_feature_node = None  # the hidapi path must not touch hidraw
            fl.transport = fl.HidapiTransport(FakeHid(self))
        return self


class FakeHid:
    """
    The cython-hidapi 0.15 calls the flasher makes, over MockDevice: one entry per top-level collection
    (each with its own path, as on Windows), bytes paths, send_feature_report returns -1 on failure,
    get_feature_report raises OSError without errno. device() returns self: one device at a time.
    """

    def __init__(self, mock):
        self.m, self.bl_open_fails, self.bl_usage = mock, 0, 0x00

    def path(self, interface, usage_page, usage):
        return f"{self.m.mode}-if{interface}-{usage_page:04x}-{usage:02x}".encode()

    def enumerate(self, vid=0, pid=0):
        if (vid, pid) != (0x258A, {"app": 0x2018, "bl": 0x2103}.get(self.m.mode)):
            return []
        cols = [(0, 0xFFFF, self.bl_usage)] if self.m.mode == "bl" else [  # app: hardware.md 2.2
            (0, 0x0001, 0x02), (1, 0x000C, 0x01), (1, 0x0001, 0x80), (1, 0x0001, 0x06), (1, 0xFFA0, 0x01),
            (1, 0xFFFF, 0x01), (2, 0xFFFF, 0x00)]
        return [{"path": self.path(*c), "interface_number": c[0], "usage_page": c[1], "usage": c[2]} for c in cols]

    def device(self):
        return self

    def open_path(self, path):
        if self.m.mode == "bl" and self.bl_open_fails:
            self.bl_open_fails -= 1
            raise OSError("open failed")
        if path != self.path(2 if self.m.mode == "app" else 0, 0xFFFF, 0x00):
            raise OSError("open failed")

    def close(self):
        pass

    def error(self):
        return "mock hidapi error"

    def send_feature_report(self, buf):
        try:
            self.m.ioctl(None, fl.HIDIOCSFEATURE(65), bytearray(buf))
        except OSError:
            return -1
        return len(buf)

    def get_feature_report(self, report_id, max_length):
        assert (report_id, max_length) == (0, 65)
        buf = bytearray(65)
        try:
            self.m.ioctl(None, fl.HIDIOCGFEATURE(65), buf)
        except OSError:
            raise OSError("read error") from None
        return list(buf)


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
    m.flash[0x7E000:0x7E020] = bytes(range(32))  # the patch's saved profile page
    assert run(fl.cmd_flash, firmware_hex=str(good)) == 0
    assert m.mode == "app" and m.flash[BASE : BASE + len(padded)] == padded
    assert m.flash[0x7E000:0x7E020] == bytes(range(32)), "Erase must not reach 0x7E000"
    assert m.programs[:2] == [BASE, BASE + 32] and m.programs[-1] == BASE, "blank 0x6000 first, vector table last"
    assert len(m.programs) == len(padded) // 32 + 1 and len(set(m.programs)) == len(padded) // 32

    m = MockDevice(mode="bl", old=old, die_after=40).install()
    assert isinstance(run(fl.cmd_flash, firmware_hex=str(good)), OSError)
    assert m.flash[BASE : BASE + 32] == b"\xFF" * 32, "interrupted flash must leave 0x6000 blank (Erase keeps it)"
    m = MockDevice(mode="bl", old=old, die_after=0).install()
    assert isinstance(run(fl.cmd_flash, firmware_hex=str(good)), OSError)
    assert m.flash[BASE : BASE + 32] == old[:32], "before the 0xFF write the old vector table is still there"

    big = tmp / "big.hex"
    big.write_text(hx.encode_compx_hex(make_image(size=fl.READ_TOP - BASE + 32), BASE))
    m = MockDevice(mode="bl").install()
    err = run(fl.cmd_flash, firmware_hex=str(big))
    assert isinstance(err, RuntimeError) and "read back" in str(err) and m.ioctls == 0
    big.write_text(hx.encode_compx_hex(make_image(size=fl.READ_TOP - BASE), BASE))
    m = MockDevice(mode="bl").install()
    assert run(fl.cmd_flash, firmware_hex=str(big)) == 0, "an image ending exactly at 0x4D800 is readable"

    m = MockDevice(mode="bl", old=old, blfw=b"\xB0\x02\x00\x00").install()
    err = run(fl.cmd_flash, firmware_hex=str(good))
    assert isinstance(err, RuntimeError) and "untested bootloader" in str(err)
    assert m.ops == [0x80], "an untested bootloader gets nothing but GetBLFW"
    assert run(fl.cmd_dump, out=str(tmp / "exit.hex"), start=BASE, end=0x6020, stay_in_bl=False) == 0
    assert m.mode == "app" and 0x02 not in m.ops, "the way out the refusal names: read 32 bytes, then Exit"

    m = MockDevice(mode="bl", glitch_on=5).install()
    assert run(fl.cmd_flash, firmware_hex=str(good)) == 0
    assert len(m.programs) == len(set(m.programs)) + 1, "a SET error must not cause a blind resend"
    fifth = [i for i, op in enumerate(m.ops) if op == 0x02][4]
    assert m.ops[fifth + 1] == 0x83, "the failed SET is read back"

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
    out = tmp / "backup.hex"
    assert run(fl.cmd_dump, out=str(out), start=BASE, end=0x100000, stay_in_bl=False) == 0 and m.mode == "app"
    data, lo, hi, *_ = hx.parse_compx_hex(out)
    assert (lo, hi) == (BASE, fl.READ_TOP) and data == bytes(m.flash[BASE:hi]), "dump stops where reads are refused"
    assert isinstance(run(fl.cmd_dump, out=str(out), start=BASE, end=0x100000, stay_in_bl=False), ValueError)
    assert not any(m.programs), "dump must never program"


def test_hidapi_selection(tmp: Path):
    m = MockDevice(mode="app").install("hidapi")
    fl.transport.hid.bl_open_fails = 3  # the new 258a:2103 is listed before it opens (udev ACL, driver install)
    assert run(fl.cmd_dump, out=str(tmp / "a.hex"), start=BASE, end=0x6020, stay_in_bl=True) == 0 and m.mode == "bl"

    m = MockDevice(mode="app").install("hidapi")
    fl.transport.hid.bl_usage = 0x01  # a bootloader without the app's usage pair: not found, so not opened
    err = run(fl.cmd_dump, out=str(tmp / "b.hex"), start=BASE, end=0x6020, stay_in_bl=False)
    assert isinstance(err, RuntimeError) and "is in its bootloader" in str(err) and "0xFFFF/0x01" in str(err), err
    assert m.mode == "bl" and not (tmp / "b.hex").exists()

    m = MockDevice(mode="app").install("hidapi")
    try:
        fl.transport.get_feature(SimpleNamespace(get_feature_report=lambda *a: [0] * 9, error=lambda: ""))
        raise AssertionError("a short feature report must be an error")
    except OSError:
        pass
    h = fl.transport.hid
    assert fl.transport.find(0x258A, 0x2018) == "app-if2-ffff-00", "not interface 1's 0xFFFF input collection"
    one = FakeHid.enumerate(h, 0x258A, 0x2018)
    h.enumerate = lambda vid, pid: one + [dict(d, path=d["path"] + b"#2") for d in one]
    try:
        fl.transport.find(0x258A, 0x2018)
        raise AssertionError("two mice must be refused")
    except RuntimeError:
        pass

    # --backend hidapi: hidapi's hidraw module on Linux (its `hid` there is libusb), `hid` elsewhere
    linux = sys.platform.startswith("linux")
    saved, argv = {k: sys.modules.get(k) for k in ("hid", "hidraw")}, sys.argv
    sys.modules["hidraw" if linux else "hid"], sys.modules["hid" if linux else "hidraw"] = FakeHid(m), None
    sys.argv = ["compx_usb_flasher.py", "--backend", "hidapi", "probe"]
    try:
        assert fl.main() == 0 and isinstance(fl.transport, fl.HidapiTransport) and m.mode == "app"
        if linux:
            sys.modules["hidraw"] = None
            try:
                fl.main()
                raise AssertionError("Linux must not fall back to the libusb `hid` module")
            except SystemExit as e:
                assert "pip install hidapi" in str(e)
    finally:
        sys.argv = argv
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


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
    for BACKEND in ("hidraw", "hidapi"):
        with tempfile.TemporaryDirectory() as d:
            test_flasher(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_hidapi_selection(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_interface_selection(Path(d))
    print("all checks passed")
