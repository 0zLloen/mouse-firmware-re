#!/usr/bin/env python3
"""
Capstone helpers for the official Compx .hex images (Thumb-2). Needs capstone; not run in CI.
The output is vendor-derived: keep it out of the repository (CONTRIBUTING.md, Never commit).

  dis   <hex> [start end]  linear disassembly, with the value of each PC-relative literal
  xref  <hex> addr...      BL/BLX/B.W branches to addr, and aligned 32-bit words equal to addr
                           (Thumb bit ignored), which also finds literal-pool constants
  track <hex> lo hi        heuristic: follow registers loaded from literals (plus add/sub/mov),
                           print memory accesses through them that land in [lo, hi)

Addresses are hex. --target receiver for the receiver image (base 0x7000); --age is how many
instructions a tracked register survives (40 for the receiver, 400 for the mouse RF code).
"""
import argparse
import struct
import sys
from pathlib import Path

import capstone
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "platforms/compx-nrf52/tools"))
import compx_hex_tool  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
md.detail = True


def w32(data, base, a):
    return struct.unpack_from("<I", data, a - base)[0] if base <= a <= base + len(data) - 4 else 0


def dis(data, base, start, end):
    a = start
    while a < end:
        for i in md.disasm(data[a - base : end - base], a):
            extra = ""
            if i.mnemonic.startswith("ldr") and "pc" in i.op_str and "[" in i.op_str:
                try:
                    la = ((i.address + 4) & ~3) + i.operands[1].mem.disp
                    extra = f"   ; [{la:#x}] = {w32(data, base, la):#010x}"
                except Exception:
                    pass
            print(f"{i.address:08x}: {i.bytes.hex():10s} {i.mnemonic:8s} {i.op_str}{extra}")
            a = i.address + i.size
        if a < end:  # capstone stops at data it cannot decode: skip one halfword
            print(f'{a:08x}: .short {struct.unpack_from("<H", data, a - base)[0]:#06x}')
            a += 2


def xref(data, base, targets):
    for off in range(0, len(data) - 3, 2):
        h1, h2 = struct.unpack_from("<HH", data, off)
        if (h1 & 0xF800) == 0xF000 and (h2 & 0x8000) and (h2 & 0x5000) != 0:
            s, imm10, j1, j2, imm11 = (h1 >> 10) & 1, h1 & 0x3FF, (h2 >> 13) & 1, (h2 >> 11) & 1, h2 & 0x7FF
            imm = (s << 24) | ((1 - (j1 ^ s)) << 23) | ((1 - (j2 ^ s)) << 22) | (imm10 << 12) | (imm11 << 1)
            if s:
                imm -= 1 << 25
            a = base + off
            t = a + 4 + imm
            kind = {0xD000: "bl", 0xC000: "blx", 0x9000: "b.w", 0x8000: "bcond?"}.get(h2 & 0xD000, "?")
            if kind == "blx":
                t &= ~3
            if t in targets:
                print(f"{a:#x} {kind} -> {t:#x}")
    for off in range(0, len(data) - 3, 4):
        v = struct.unpack_from("<I", data, off)[0]
        if (v & ~1) in targets:
            print(f"ptr at {base + off:#x} = {v:#x}")


def track(data, base, lo, hi, max_age):
    insns, a = [], base
    while a < base + len(data):
        n = 0
        for i in md.disasm(data[a - base :], a):
            insns.append(i)
            a, n = i.address + i.size, n + 1
            if n > 5000:
                break
        else:
            a += 2
    regs, age = {}, {}
    for i in insns:
        ops, m = i.operands, i.mnemonic
        for r in list(regs):
            age[r] += 1
            if age[r] > max_age:
                del regs[r]
        try:
            if m in ("ldr", "ldr.w") and len(ops) == 2 and ops[1].type == ARM_OP_MEM and ops[1].mem.base == ARM_REG_PC:
                regs[ops[0].reg] = w32(data, base, ((i.address + 4) & ~3) + ops[1].mem.disp)
                age[ops[0].reg] = 0
                continue
            if m in ("adds", "add", "add.w", "subs", "sub", "sub.w", "addw", "subw") and ops[-1].type == ARM_OP_IMM:
                src = ops[1].reg if len(ops) == 3 else ops[0].reg
                if src in regs:
                    regs[ops[0].reg] = regs[src] + (ops[-1].imm if m.startswith("add") else -ops[-1].imm)
                    age[ops[0].reg] = 0
                    continue
            if m in ("mov", "movs", "mov.w") and ops[1].type == ARM_OP_REG and ops[1].reg in regs:
                regs[ops[0].reg] = regs[ops[1].reg]
                age[ops[0].reg] = 0
                continue
            for o in ops:
                if o.type == ARM_OP_MEM and o.mem.base in regs and o.mem.index == 0:
                    ea = regs[o.mem.base] + o.mem.disp
                    if lo <= ea < hi:
                        print(f"{i.address:#x}: {m} {i.op_str}   ; ea={ea:#x}")
            if ops and ops[0].type == ARM_OP_REG and not m.startswith(("st", "cmp", "tst")):
                regs.pop(ops[0].reg, None)  # any other write to a tracked register ends tracking
            if "!" in i.op_str:  # writeback changes the base register
                for o in ops:
                    if o.type == ARM_OP_MEM:
                        regs.pop(o.mem.base, None)
        except Exception:  # capstone raises on some operand kinds; skip the instruction
            pass


def main():
    hexnum = lambda s: int(s, 16)  # noqa: E731
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("dis", "xref", "track"))
    ap.add_argument("hex_file", type=Path)
    ap.add_argument("addr", nargs="*", type=hexnum)
    ap.add_argument("--target", choices=compx_hex_tool.TARGETS, default="mouse")
    ap.add_argument("--age", type=int, default=40)
    args = ap.parse_args()
    base = compx_hex_tool.TARGETS[args.target]
    data = compx_hex_tool.load_app_hex(args.hex_file, base)[0]
    if args.cmd == "dis":
        start, end = args.addr if args.addr else (base, base + len(data))
        dis(data, base, start, end)
    elif args.cmd == "xref":
        xref(data, base, set(args.addr))
    else:
        lo, hi = args.addr
        track(data, base, lo, hi, args.age)


if __name__ == "__main__":
    main()
