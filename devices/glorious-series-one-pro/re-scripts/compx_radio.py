#!/usr/bin/env python3
"""
Encoder/decoder for the Compx 2.4 GHz link of the Series One Pro mouse and its receiver 258a:2031
(platforms/compx-nrf52/radio-protocol.md). Standard library only.

Layers:
  air      nRF52 RADIO framing as both images configure it: preamble, 5-byte address (BASE + PREFIX),
           LENGTH, PAYLOAD, CRC-16; whitening over LENGTH..CRC. air_bits() / parse_air() take the
           register values for BALEN, MAXLEN, ENDIAN, WHITEEN/DATAWHITEIV, CRCCNF (LEN 1..3, SKIPADDR),
           CRCPOLY/CRCINIT and the logical address; they assume LFLEN 8, no S0/S1, an 8-bit preamble,
           STATLEN 0 and CRCINC 0, as on this link.
  packet   PAYLOAD = [b1][b2][records...]  (uplink: seq, next expected downlink seq; downlink ACK:
           next expected uplink seq, downlink seq | timing bits)
  records  one header byte  more<<7 | len<<3 | type, then len data bytes

The CRC follows the nRF52 documentation. Nordic erratum 245 says the hardware CRC is wrong when whitening
is on and the address is included, as on this link, so the CRC bits on air may differ (Unknown).
Between nRF52 chips with the same registers this does not matter.

Run it to execute the self-check: python3 compx_radio.py
The test vectors are frames from the Unicorn co-emulation of both stock images (compx_coemu.py),
with the made-up device ID 0x11223344 instead of a real unit's.
"""
import struct

# Register values both stock images write (CM2856 v1.00.00, mouse 0x15A1C, receiver 0xCA40).
LINK = dict(MODE=4, PCNF0=0x00000008, PCNF1=0x03040040, BASE0=0x01234567, PREFIX0=0xB3B2B1B0,
            PREFIX1=0xB7B6B5B4, TXADDRESS=1, RXADDRESSES=0x02, CRCCNF=2, CRCINIT=0xFFFF,
            CRCPOLY=0x11021, DATAWHITEIV=0x40, TXPOWER=4)
PAIR_CHANNELS = (3, 49, 78)  # FREQUENCY values (2400 + n MHz), mouse 0x1A950 / receiver 0xEA90
HOP_TABLE = bytes.fromhex('331b4c0430184908341a4d022f164807')  # 4 rows x 4, mouse 0x1A966 / rx 0xEAA6


def link_config(rf_id):
    """The link registers with BASE1 = the mouse's FICR DEVICEID[0] (= the receiver's stored RF ID)."""
    return dict(LINK, BASE1=rf_id)


def hop_channel(row, idx):
    return HOP_TABLE[4 * (row & 3) + (idx & 3)]


# ---- air layer -------------------------------------------------------------------------------

def _bits(byte, msb_first):
    return [(byte >> (7 - i if msb_first else i)) & 1 for i in range(8)]


def _byte(bits, msb_first):
    return sum(b << (7 - i if msb_first else i) for i, b in enumerate(bits))


def address(cfg, logical):
    """On-air address bytes (time order) for logical address 0..7: BASE bytes LSByte first, then PREFIX."""
    balen = (cfg['PCNF1'] >> 16) & 7
    base = cfg['BASE0'] if logical == 0 else cfg['BASE1']
    prefix = ((cfg['PREFIX0'] if logical < 4 else cfg['PREFIX1']) >> (8 * (logical & 3))) & 0xFF
    # nRF52: the BALEN most significant bytes of BASEn are used; they and the prefix go out LSbit first
    used = (base >> (8 * (4 - balen))) & ((1 << (8 * balen)) - 1)
    return bytes((used >> (8 * i)) & 0xFF for i in range(balen)) + bytes([prefix])


def crc(cfg, bits):
    """CRC over on-air bits (MSB-first shift register), as CRCCNF/CRCPOLY/CRCINIT configure it."""
    n = cfg['CRCCNF'] & 3
    poly, reg, top = cfg['CRCPOLY'] & ((1 << 8 * n) - 1), cfg['CRCINIT'] & ((1 << 8 * n) - 1), 1 << (8 * n - 1)
    for b in bits:
        fb = bool(reg & top) ^ b
        reg = (reg << 1) & ((1 << 8 * n) - 1)
        if fb:
            reg ^= poly
    return reg


def whiten(cfg, bits):
    """BLE-style whitening, x^7 + x^4 + 1; DATAWHITEIV bit 6 is LFSR position 0, bit 0 is position 6."""
    iv = cfg['DATAWHITEIV'] | 0x40
    pos = [(iv >> (6 - k)) & 1 for k in range(7)]
    out = []
    for b in bits:
        o = pos[6]
        out.append(b ^ o)
        pos = [o] + pos[:6]
        pos[4] ^= o
    return out


def air_bits(cfg, packet, logical=None):
    """Bits on air for RAM packet [LENGTH][PAYLOAD] (S0/S1 not used here), preamble first."""
    assert (cfg['PCNF0'] & 0xF) == 8 and not cfg['PCNF0'] & 0x070F0F00, 'only LFLEN 8, no S0/S1, PLEN 8 bit, CRCINC 0'
    assert not cfg['PCNF1'] & 0xFF00 and 1 <= (cfg['CRCCNF'] & 3) <= 3, 'only STATLEN 0 and a CRC'
    big = bool(cfg['PCNF1'] & (1 << 24))
    maxlen = cfg['PCNF1'] & 0xFF
    n = min(packet[0], maxlen)
    addr = [b for x in address(cfg, cfg['TXADDRESS'] if logical is None else logical) for b in _bits(x, False)]
    body = [b for x in bytes([n]) + bytes(packet[1:1 + n]) for b in _bits(x, big)]
    c = crc(cfg, (addr if not cfg['CRCCNF'] & 0x100 else []) + body)
    nc = cfg['CRCCNF'] & 3
    tail = [(c >> (8 * nc - 1 - i)) & 1 for i in range(8 * nc)]
    pdu = body + tail
    if cfg['PCNF1'] & (1 << 25):
        pdu = whiten(cfg, pdu)
    pre = [0, 1] * 4 if addr[0] == 0 else [1, 0] * 4  # alternating; its last bit differs from the first address bit
    return pre + addr + pdu


def parse_air(cfg, bits):
    """Receive side: returns (logical address or None, RAM packet bytes, crc_ok)."""
    big = bool(cfg['PCNF1'] & (1 << 24))
    balen = (cfg['PCNF1'] >> 16) & 7
    alen = 8 * (balen + 1)
    addr = bits[8:8 + alen]
    abytes = bytes(_byte(addr[i:i + 8], False) for i in range(0, alen, 8))
    match = next((la for la in range(8) if cfg['RXADDRESSES'] & (1 << la) and address(cfg, la) == abytes), None)
    if match is None:
        return None, b'', False
    pdu = bits[8 + alen:]
    if cfg['PCNF1'] & (1 << 25):
        pdu = whiten(cfg, pdu)
    n = _byte(pdu[:8], big)
    n = min(n, cfg['PCNF1'] & 0xFF)
    nc = cfg['CRCCNF'] & 3
    body = pdu[:8 * (1 + n)]
    rx_crc = sum(b << (8 * nc - 1 - i) for i, b in enumerate(pdu[8 * (1 + n):8 * (1 + n + nc)]))
    ok = crc(cfg, (addr if not cfg['CRCCNF'] & 0x100 else []) + body) == rx_crc
    return match, bytes(_byte(body[i:i + 8], big) for i in range(0, len(body), 8)), ok


def airtime_us(cfg, packet):
    """Time on air at 2 Mbit/s (MODE 1 or 4): 4 us per byte."""
    return len(air_bits(cfg, packet)) / 2


# ---- packet / record layer -------------------------------------------------------------------

def record(rtype, data, more=False):
    assert 0 <= rtype < 8 and len(data) < 16
    return bytes([(more << 7) | (len(data) << 3) | rtype]) + bytes(data)


def records(buf):
    """Split a record area into (type, data, more)."""
    out, i = [], 0
    while i < len(buf):
        h = buf[i]
        n = (h >> 3) & 15
        out.append((h & 7, bytes(buf[i + 1:i + 1 + n]), bool(h >> 7)))
        i += 1 + n
    return out


def uplink(seq, dl_next, recs=b''):
    """Mouse data / keep-alive packet as it sits in RAM: [LENGTH][seq][next expected downlink seq][records].
    dl_next is the last accepted downlink sequence + 1, mod 63 (the receiver pops on dl_next == its seq + 1)."""
    return bytes([2 + len(recs), seq & 0xFF, dl_next % 63]) + recs


def ack(ul_next, dl_seq, timing=0, recs=b''):
    """Receiver ACK: [LENGTH][next expected uplink seq][downlink seq | timing bits][records]."""
    return bytes([2 + len(recs), ul_next & 0xFF, (dl_seq % 63) | (timing & 0xC0)]) + recs


def connect_request(device_id):
    """Mouse search state 4: [5][0x20][FICR DEVICEID[0], little-endian]."""
    return bytes([5, 0x20]) + struct.pack('<I', device_id)


def connect_answer(row, frame_us):
    """Receiver answer: [3][hop row][frame period in receiver TIMER ticks, u16 LE]."""
    return bytes([3, row]) + struct.pack('<H', frame_us)


def timing_bits(t_address_us):
    """Receiver ACK byte 2 bits 7..6 from the mouse packet's ADDRESS time within the 1 ms frame
    (receiver 0xCFE4-0xD014). The mouse shortens its next 500 us slot to 450 us on 0x40 and 0xC0."""
    t = t_address_us
    return 0xC0 if t >= 650 else 0x80 if 500 <= t < 600 else 0x40 if 150 <= t < 500 else 0


def relay_chunks(body):
    """Receiver relay of a 64-byte feature request: body[3] + 6 bytes, one type-7 record per 8 bytes,
    data = [offset] + 8 bytes (receiver 0xCC78)."""
    n = body[3] + 6
    return [record(7, bytes([off]) + bytes(body[off:off + 8]).ljust(8, b'\0'), n > off + 8) for off in range(0, n, 8)]


def describe(packet, downlink=False, search=False):
    """One-line summary. search=True: the packet belongs to the connect exchange (states 4-6 / 1-3)."""
    n = packet[0]
    if search and not downlink and n == 5 and packet[1] == 0x20:
        return f'connect request id={struct.unpack_from("<I", packet, 2)[0]:#010x}'
    if search and downlink and n == 3:
        return f'connect answer row={packet[1]} frame={struct.unpack_from("<H", packet, 2)[0]}'
    if n < 2:
        return 'short ' + packet.hex()
    b1, b2 = packet[1], packet[2]
    head = (f'ACK next_ul={b1} dl_seq={b2 & 0x3F} timing={b2 & 0xC0:#04x}' if downlink
            else f'UL seq={b1} next_dl={b2}')
    return head + ''.join(f' | t{t}{"+" if m else ""}:{d.hex()}' for t, d, m in records(packet[3:1 + n]))


# ---- self-check ------------------------------------------------------------------------------

# (sender, FREQUENCY, RAM packet, on-air bits in time order as hex, preamble first). The RAM packets are
# frames the stock images handed their radios, and the other side received, in a default
# `compx_coemu.py --vectors` run (made-up device ID 0x11223344). The air bits are this encoder's own
# output for them: a regression check of the encoder, not an independent capture. Connect request,
# connect answer, keep-alive and its ACK, link-up notification (type 5), motion (type 1), the two
# relay-request chunks of a version query (type 7 on ACKs) and the two reply chunks.
VECTORS = [
    ('mouse', 3, '052044332211', '5522cc44888d076d79f0dafd8d4b'),
    ('rx', 3, '0303e803', '5522cc44888d014ed5c036f5'),
    ('mouse', 47, '020000', '5522cc44888d004d3de4e4'),
    ('rx', 48, '020080', '5522cc44888d004dbd756c'),
    ('mouse', 48, '0b0000450601000000000000', '5522cc44888d094d3d86feed52faa16f3959b15d'),
    ('mouse', 48, '0b01004100000200ffff0000', '5522cc44888d094c3d82f8ec50fa5e903959c2d1'),
    ('rx', 48, '0c0d80cf00a000020600810000', '5522cc44888d0e40bd0cf84c52f8a76fb85983bcf1'),
    ('rx', 48, '0c0d814f080000000000000000', '5522cc44888d0e40bc8cf0ec52faa16f3959836535'),
    ('mouse', 48, '0b0d02c7a100020300810100', '5522cc44888d09403f0459ec50f9a1ee38599edb'),
    ('mouse', 48, '0b0e02470000201800000000', '5522cc44888d09433f84f8ec72e2a16f3959f11f'),
]


def _selfcheck():
    cfg = link_config(0x11223344)
    # CRC engine is plain CRC-16/CCITT-FALSE on MSB-first bits
    assert crc(cfg, [b for x in b'123456789' for b in _bits(x, True)]) == 0x29B1
    # whitening is an involution and its first 7 output bits for IV 0x40 are 0000001 (BLE channel 0)
    assert whiten(cfg, whiten(cfg, [1, 0, 1, 1] * 9)) == [1, 0, 1, 1] * 9
    assert whiten(cfg, [0] * 7) == [0, 0, 0, 0, 0, 0, 1]
    assert address(cfg, 1) == bytes.fromhex('44332211b1')
    assert address(cfg, 0) == bytes.fromhex('67452301b0')
    for pkt in (uplink(0, 0), connect_request(0x11223344), ack(5, 7, 0x40, record(4, b'\x01')),
                uplink(9, 3, record(1, bytes(7)) + record(7, bytes(8), True))):
        bits = air_bits(cfg, pkt)
        assert len(bits) == 8 * (1 + 5 + 1 + pkt[0] + 2)
        assert parse_air(cfg, bits) == (1, pkt, True), pkt.hex()
        bad = bits[:]
        bad[60] ^= 1
        assert parse_air(cfg, bad)[2] is False
    assert parse_air(link_config(0x11223345), air_bits(cfg, uplink(0, 0)))[0] is None
    assert records(record(7, b'\x08' + bytes(8), True) + record(4, b'\x01')) == [(7, b'\x08' + bytes(8), True), (4, b'\x01', False)]
    assert [hop_channel(r, 0) for r in range(4)] == [0x33, 0x30, 0x34, 0x2F]
    assert timing_bits(120) == 0 and timing_bits(620) == 0 and timing_bits(300) == 0x40 and timing_bits(700) == 0xC0
    for d, freq, ram, airhex in VECTORS:
        bits = [int(c) for h in airhex for c in f'{int(h, 16):04b}']
        pkt = bytes.fromhex(ram)
        assert air_bits(cfg, pkt)[:len(bits)] == bits, (d, ram)
        assert parse_air(cfg, bits) == (1, pkt, True)
    print(f'compx_radio self-check OK ({len(VECTORS)} co-emulation vectors)')


if __name__ == '__main__':
    _selfcheck()
