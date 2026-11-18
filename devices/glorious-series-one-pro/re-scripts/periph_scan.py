#!/usr/bin/env python3
"""
Which nRF52 peripherals does an image name? Lists every 32-bit little-endian word at a 4-byte aligned
offset (where Keil and GCC put literal pools) that is an nRF52 peripheral, FICR or UICR address, grouped
by peripheral. Standard library only; reads your own copy of an official .hex.

What it is for: telling an nRF52840 build from one for a smaller part. The nRF52840 SystemInit() of the
Nordic MDK carries errata workarounds that name NFCT 0x4000568C, TEMP 0x4000C520, QSPI 0x40029640 and
UICR 0x10000404 within a few bytes of each other, and nRF52840 boards use GPIO P1 (0x50000300). The
nRF52820 has none of NFCT, QSPI, P1, SAADC, PWM, SPIM2/3, UARTE1, TIMER4 or RTC2.
A literal is evidence, not proof: data can look like an address, and code can build an address without one.

Usage: periph_scan.py <.hex>
"""
import struct, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'platforms/compx-nrf52/tools'))
import compx_hex_tool  # noqa: E402

NOT_ON_52820 = 'not on the nRF52820'
PERIPH = {
    0x40000: 'POWER / CLOCK', 0x40001: 'RADIO', 0x40002: 'UARTE0', 0x40003: 'SPI0 / TWI0', 0x40004: 'SPI1 / TWI1',
    0x40005: f'NFCT ({NOT_ON_52820})', 0x40006: 'GPIOTE', 0x40007: f'SAADC ({NOT_ON_52820})',
    0x40008: 'TIMER0', 0x40009: 'TIMER1', 0x4000A: 'TIMER2', 0x4000B: 'RTC0', 0x4000C: 'TEMP', 0x4000D: 'RNG',
    0x4000E: 'ECB', 0x4000F: 'AAR / CCM', 0x40010: 'WDT', 0x40011: 'RTC1', 0x40012: 'QDEC', 0x40013: 'COMP',
    0x40014: 'EGU0 / SWI0', 0x40015: 'EGU1 / SWI1', 0x40016: 'EGU2 / SWI2', 0x40017: 'EGU3 / SWI3',
    0x40018: 'EGU4 / SWI4', 0x40019: 'EGU5 / SWI5', 0x4001A: 'TIMER3', 0x4001B: f'TIMER4 ({NOT_ON_52820})',
    0x4001C: f'PWM0 ({NOT_ON_52820})', 0x4001D: f'PDM ({NOT_ON_52820})', 0x4001E: 'NVMC / ACL', 0x4001F: 'PPI',
    0x40020: 'MWU', 0x40021: f'PWM1 ({NOT_ON_52820})', 0x40022: f'PWM2 ({NOT_ON_52820})',
    0x40023: f'SPI2 / SPIM2 ({NOT_ON_52820})', 0x40024: f'RTC2 ({NOT_ON_52820})', 0x40025: f'I2S ({NOT_ON_52820})',
    0x40026: 'FPU', 0x40027: 'USBD', 0x40028: f'UARTE1 ({NOT_ON_52820})', 0x40029: f'QSPI ({NOT_ON_52820})',
    0x4002D: f'PWM3 ({NOT_ON_52820})', 0x4002F: f'SPIM3 ({NOT_ON_52820})', 0x50000: 'GPIO',
    0x5002A: f'CRYPTOCELL ({NOT_ON_52820})', 0x10000: 'FICR', 0x10001: 'UICR',
}

data, lo, _hi, _sla, _n = compx_hex_tool.parse_compx_hex(Path(sys.argv[1]))
found = {}
for off in range(-lo % 4, len(data) - 3, 4):
    value = struct.unpack_from('<I', data, off)[0]
    name = PERIPH.get(value >> 12) if value % 4 == 0 else None  # registers are word aligned
    if name == 'GPIO':
        name = f'GPIO P1 ({NOT_ON_52820})' if value & 0xF00 == 0x300 else 'GPIO P0'
    if name:
        found.setdefault(name, {}).setdefault(value, []).append(lo + off)
for name in sorted(found):
    print(name)
    for value, where in sorted(found[name].items()):
        print(f'  {value:#010x} @ ' + ' '.join(f'{a:#x}' for a in where[:6]) + (' ...' if len(where) > 6 else ''))
flagged = sorted(n for n in found if NOT_ON_52820 in n)
print('\nNamed here but ' + NOT_ON_52820 + ': ' + (', '.join(n.split(' (')[0] for n in flagged) or 'none'))
