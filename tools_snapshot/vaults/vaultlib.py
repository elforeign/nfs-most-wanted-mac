"""Minimal reader for NFS:MW Attrib vault packs (VPAK: ATTRIBUTES.BIN, GAMEPLAY.BIN, FE_ATTRIB.BIN), both endiannesses.

Layout confirmed against the PC decomp (PC decomp, speed_00452580.c):
  - Attrib::Vault::Vault @ 00455d70: vlt chunks {u32 id, u32 size incl. header}: DepN, StrN, DatN, ExpN, PtrN.
  - Attrib::Vault::Initialize @ 00457670: PtrN entries {u32 offset, u16 type, u16 depIndex, u32 value}, 12 bytes;
    type 2 = switch fixup base to dep[index], 3 = ptr = dep[index].base + value, 1/5 = NULL, 4 = export ref by hash,
    0 = end. ExpN: u32 count at +8, entries {key, typeHash, ?, size, dataOffset(vlt-relative)} 20 bytes.
  - Attrib::Collection::Collection @ 00457f90: CollectionLoadData {key, class, parent, tableReserve, ?, numEntries,
    numTypes, layoutPtr} + types[numTypes] + entries {u32 key, u32 data, u16 typeIndex, u8 flags(0x20 = by value), u8}.
  - Attrib::ClassPrivate::ClassPrivate @ 0045c250: ClassLoadData {key, collReserve, u16 numDefs, u16, defsPtr,
    u16 layoutSize, ...}; definitions 16 bytes {key, type, u16 offset, u16 size, u16 maxCount, u8 flags, u8 align}.
  - Attrib::hash32 @ 004517c0 / StringHash32 @ 004519d0: Bob Jenkins lookup2, init 0xABCDEF00 (checked below).
The VPAK directory itself is little-endian on both platforms; vault contents are big-endian on X360.
"""
import struct


def _mix(a, b, c):
    M = 0xFFFFFFFF
    a = (a - b - c) & M; a ^= c >> 13
    b = (b - c - a) & M; b ^= (a << 8) & M
    c = (c - a - b) & M; c ^= b >> 13
    a = (a - b - c) & M; a ^= c >> 12
    b = (b - c - a) & M; b ^= (a << 16) & M
    c = (c - a - b) & M; c ^= b >> 5
    a = (a - b - c) & M; a ^= c >> 3
    b = (b - c - a) & M; b ^= (a << 10) & M
    c = (c - a - b) & M; c ^= b >> 15
    return a, b, c


def hash32(s, init=0xABCDEF00):
    k = s.encode('latin1') if isinstance(s, str) else s
    if not k:
        return 0
    n = len(k); a = b = 0x9E3779B9; c = init; i = 0
    while n - i >= 12:
        a = (a + int.from_bytes(k[i:i + 4], 'little')) & 0xFFFFFFFF
        b = (b + int.from_bytes(k[i + 4:i + 8], 'little')) & 0xFFFFFFFF
        c = (c + int.from_bytes(k[i + 8:i + 12], 'little')) & 0xFFFFFFFF
        a, b, c = _mix(a, b, c); i += 12
    c = (c + n) & 0xFFFFFFFF
    r = k[i:]; L = len(r)
    for j in range(min(L, 11) - 1, -1, -1):
        v = r[j]
        if j >= 8:
            c = (c + (v << (8 * (j - 8 + 1)))) & 0xFFFFFFFF
        elif j >= 4:
            b = (b + (v << (8 * (j - 4)))) & 0xFFFFFFFF
        else:
            a = (a + (v << (8 * j))) & 0xFFFFFFFF
    a, b, c = _mix(a, b, c)
    return c


def read_vpak(path):
    d = open(path, 'rb').read()
    if d[:4] == b'JDLZ':
        raise ValueError('compressed; pass the .BIN')
    assert d[:4] == b'VPAK', path
    n, so, ss = struct.unpack_from('<III', d, 4)
    out = []
    for i in range(n):
        no, bs, vs, bo, vo = struct.unpack_from('<5I', d, 0x10 + i * 20)
        name = d[so + no:d.index(b'\0', so + no)].decode()
        out.append((name, d[bo:bo + bs], d[vo:vo + vs], bo, vo))
    return out


class Vault:
    def __init__(self, name, binb, vlt, e, bin_off=0, vlt_off=0):
        self.name, self.bin, self.vlt, self.e = name, binb, vlt, e
        self.bin_off, self.vlt_off = bin_off, vlt_off
        self.ch = {}
        o = 0
        while o + 8 <= len(vlt):
            cid = vlt[o:o + 4]
            sz = struct.unpack_from(e + 'I', vlt, o + 4)[0]
            if e == '<':
                cid = cid[::-1]
            self.ch[cid.decode('latin1')] = (o, sz)
            if sz == 0:
                break
            o += sz
        self.bufs = [vlt, binb]  # dep index 0 = *.vlt, 1 = *.bin (DepN order)
        o, _ = self.ch['DepN']
        nd = self.u32(vlt, o + 8)
        self.deps = list(struct.unpack_from(e + '%dI' % nd, vlt, o + 12))
        # bin strings chunk (StrE) extent
        self.stre_end = self.u32(binb, 4) if binb[:4] in (b'StrE', b'ErtS') else 0
        # pointer fixups: (dep, offset) -> ('ptr', dep, off) | ('null',) | ('exp', hash)
        self.fix = {}
        o, sz = self.ch['PtrN']
        p = o + 8; base = 0
        while p + 12 <= o + sz:
            off = self.u32(vlt, p); typ, idx = struct.unpack_from(e + 'HH', vlt, p + 4); val = self.u32(vlt, p + 8)
            if typ == 2:
                base = idx
            elif typ == 3:
                self.fix[(base, off)] = ('ptr', idx, val)
            elif typ in (1, 5):
                self.fix[(base, off)] = ('null',)
            elif typ == 4:
                self.fix[(base, off)] = ('exp', idx, val)
            else:
                break
            p += 12
        o, _ = self.ch['ExpN']
        n = self.u32(vlt, o + 8)
        self.exports = []
        for i in range(n):
            k, t, x, s, do = struct.unpack_from(e + '5I', vlt, o + 12 + i * 20)
            self.exports.append((k, t, s, do))

    def u32(self, b, o):
        return struct.unpack_from(self.e + 'I', b, o)[0]

    def u16(self, b, o):
        return struct.unpack_from(self.e + 'H', b, o)[0]

    def cstr(self, dep, off):
        b = self.bufs[dep]
        j = b.find(b'\0', off)
        return b[off:j].decode('latin1')

    def deref(self, dep, off):
        """Resolve a pointer stored at (dep, off): returns (dep, offset) or None."""
        f = self.fix.get((dep, off))
        if f and f[0] == 'ptr':
            return f[1], f[2]
        return None


HT = {}  # type hash -> name
for _n in ('Attrib::Database', 'Attrib::Class', 'Attrib::Collection'):
    HT[hash32(_n)] = _n
