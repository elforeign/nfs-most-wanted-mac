#!/usr/bin/env python3
"""Generates runtime/attrib_x360.inc: the X360 vault data the runtime lays over the PC Attrib vaults.
Each LAYOUT record is one array field of an existing collection, taken from the X360 ATTRIBUTES.BIN and byte-swapped
to little-endian: {class hash, collection key, byte offset in the collection's layout block, length, bytes}.
G6 (drag traffic): also writes the X360-only gameplay collections of the five drag-race vaults as little-endian
CollectionLoadData blobs (see gen_new_collections); the runtime appends them to the race vault when it loads.
Run (tools/gen_tables.py does): python -I tools_snapshot/attrib_x360.py <X360 GLOBAL dir> <PC GLOBAL dir> [out .inc, default runtime/gen/]
Reads the game data only. Used by runtime/attrib_x360.c."""
import os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'vaults'))
import compare_vaults as cv
from vaultlib import hash32

XBG, PCG = sys.argv[1:3]
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, '..', 'runtime', 'gen', 'attrib_x360.inc')

# (class, field, collections): arrays whose X360 contents replace the PC's (R22, asphalt tyre smoke emitter groups)
LAYOUT_ARRAYS = [('emittergroup', 'Emitters', ['fxtd_sl_asphalt', 'fxtd_sk_asphalt', 'fxtd_sk_asphalt_no_leaves',
                                                'fxtd_dr_asphalt_leaves'])]


def find(side, ck, key):
    for V in side.vaults:
        if V.file != 'ATTRIBUTES.BIN':
            continue
        for k, t, s, do in V.exports:
            if t == cv.T_COLL and V.u32(V.vlt, do) == key and V.u32(V.vlt, do + 4) == ck:
                return V, do
    raise SystemExit('collection not found')


NEW_VAULTS = ['1_7_3_drag', '3_7_3_drag', '4_7_4_drag', '7_7_1_r_drag', '10_7_3_drag']


class Blob:
    """Position-independent little-endian image: pointer words hold blob-relative offsets, listed in relocs."""
    def __init__(self):
        self.b = bytearray(); self.relocs = []; self.strs = {}

    def alloc(self, n, align=16):
        while len(self.b) % align:
            self.b.append(0)
        o = len(self.b); self.b += bytes(n)
        return o

    def string(self, s):
        if s not in self.strs:
            o = self.alloc(len(s) + 1, 1); self.b[o:o + len(s)] = s; self.strs[s] = o
        return self.strs[s]

    def ptr_word(self, V, dep, off, at):
        """The word at (dep, off) is a vault pointer fix-up: re-point it at a string copy inside the blob."""
        f = V.fix[(dep, off)]
        if f[0] == 'null':
            return
        assert f[0] == 'ptr', f
        bb = V.bufs[f[1]]; e = bb.index(b'\0', f[2])
        so = self.string(bytes(bb[f[2]:e]))
        struct.pack_into('<I', self.b, at, so); self.relocs.append(at)

    def words(self, V, dep, off, n, unit, at):
        """Copy n bytes of X360 data at (dep, off) to blob offset at, swapping `unit`-byte values."""
        src = V.bufs[dep]
        for k in range(0, n, unit if unit == 4 else 1):
            if (dep, off + k) in V.fix:
                assert unit == 4
                self.ptr_word(V, dep, off + k, at + k)
            elif unit == 4:
                self.b[at + k:at + k + 4] = src[off + k:off + k + 4][::-1]
            else:
                self.b[at + k] = src[off + k]


def gen_new_collections(xb, pc):
    pk = {(k[2], k[3]) for k in pc.colls}
    out = []
    for vname in NEW_VAULTS:
        XV = [v for v in xb.vaults if v.file == 'GAMEPLAY.BIN' and v.name == vname]
        PV = [v for v in pc.vaults if v.file == 'GAMEPLAY.BIN' and v.name == vname]
        assert len(XV) == 1 and len(PV) == 1, vname
        V, P = XV[0], PV[0]
        pkeys = [k for k, t, s, do in P.exports]
        blob = Blob(); colls = []
        for k, t, s, do in V.exports:
            if t != cv.T_COLL:
                continue
            cls = V.u32(V.vlt, do + 4)
            if (cls, V.u32(V.vlt, do)) in pk:
                continue
            assert cls == 0x5cea9d46, hex(cls)
            hdr = struct.unpack_from('>7I', V.vlt, do)
            key, _, parent, tres, x10, nent, ntyp = hdr
            lay = V.deref(0, do + 0x1c)
            types = struct.unpack_from('>%dI' % ntyp, V.vlt, do + 0x20)
            ld = blob.alloc(0x20 + 4 * ntyp + 12 * nent, 16)
            struct.pack_into('<7I', blob.b, ld, *hdr)
            struct.pack_into('<%dI' % ntyp, blob.b, ld + 0x20, *types)
            lsz = xb.classes[cls][0]
            lo = blob.alloc(lsz, 16)
            blob.words(V, lay[0], lay[1], lsz, 4, lo)
            struct.pack_into('<I', blob.b, ld + 0x1c, lo); blob.relocs.append(ld + 0x1c)
            eo = do + 0x20 + 4 * ntyp
            for i in range(nent):
                o = eo + 12 * i; at = ld + 0x20 + 4 * ntyp + 12 * i
                ek, = struct.unpack_from('>I', V.vlt, o)
                ti, = struct.unpack_from('>H', V.vlt, o + 8)
                fl = V.vlt[o + 10]
                dt = types[ti]; tn, tsz = xb.types[dt]
                struct.pack_into('<I', blob.b, at, ek)
                struct.pack_into('<HBB', blob.b, at + 8, ti, fl, V.vlt[o + 11])
                if fl & 0x20:                                  # by value: the 4-byte slot holds the value
                    if (0, o + 4) in V.fix:
                        blob.ptr_word(V, 0, o + 4, at + 4)
                    elif tsz == 4:
                        blob.words(V, 0, o + 4, 4, 4, at + 4)
                    else:
                        assert tsz == 1, (tn, tsz)
                        blob.b[at + 4:at + 8] = V.vlt[o + 4:o + 8]
                    continue
                p = V.deref(0, o + 4)
                assert p, (vname, hex(key), hex(ek))
                src = V.bufs[p[0]]
                if fl & 2:                                    # array: {alloc, count, elem size, flags} + elements
                    alloc, cnt, esz, enc = struct.unpack_from('>4H', src, p[1])
                    assert not enc & 0x8000 and esz == 4 and tsz == 4, (tn, esz, enc)
                    n = 8 + esz * alloc
                    d = blob.alloc(n, 16)
                    struct.pack_into('<4H', blob.b, d, alloc, cnt, esz, enc)
                    blob.words(V, p[0], p[1] + 8, n - 8, 4, d + 8)
                else:
                    assert tn in ('EA::Reflection::Float', 'EA::Reflection::Int32', 'GCollectionKey', 'EA::Reflection::Text',
                                  'Attrib::Types::Vector3', 'EA::Reflection::Bool'), tn
                    d = blob.alloc(tsz, 16)
                    blob.words(V, p[0], p[1], tsz, 1 if tn.endswith('Bool') else 4, d)
                struct.pack_into('<I', blob.b, at + 4, d); blob.relocs.append(at + 4)
            colls.append((key, parent, ld))
        # parents that are themselves new go first
        keys = {c[0] for c in colls}; done = []; left = list(colls)
        while left:
            for c in left:
                if c[1] not in keys or c[1] in {d[0] for d in done}:
                    done.append(c); left.remove(c); break
            else:
                raise SystemExit('parent cycle')
        out.append((vname, len(pkeys), pkeys[0], pkeys[-1], blob, done))
        print('%-14s PC exports %d -> +%d collections, blob %d bytes, %d relocs' % (vname, len(pkeys), len(done), len(blob.b), len(blob.relocs)))
    fps = {(o[1], o[2], o[3]) for o in out}
    assert len(fps) == len(out)
    for P in pc.vaults:                                        # a fingerprint must name exactly one vault
        if P.name not in NEW_VAULTS:
            ks = [k for k, t, s, do in P.exports]
            assert not ks or (len(ks), ks[0], ks[-1]) not in fps, P.name
    return out


def main():
    xb, pc = cv.Side(XBG, 'X360', '>'), cv.Side(PCG, 'PC', '<')
    sh = dict(xb.types); sh.update(pc.types); xb.parse(sh); pc.parse(sh)
    recs = []
    for cname, fname, colls in LAYOUT_ARRAYS:
        ck, fk = hash32(cname), hash32(fname)
        xd, pd = xb.classes[ck][1][fk], pc.classes[ck][1][fk]
        assert xd == pd, 'class field definition differs'          # same offset, size, max count on both platforms
        foff, maxc = xd[1], xd[3]
        for n in colls:
            key = hash32(n)
            blobs = []
            for side, (V, do) in ((xb, find(xb, ck, key)), (pc, find(pc, ck, key))):
                lay = V.deref(0, do + 0x1c)
                b = V.bufs[lay[0]]; o = lay[1] + foff
                alloc, cnt, esz, enc = struct.unpack_from(V.e + '4H', b, o)
                assert alloc == maxc and esz % 4 == 0 and not enc & 0x8000 and cnt <= alloc, (n, alloc, cnt, esz, enc)
                words = struct.unpack_from(V.e + '%dI' % (esz * alloc // 4), b, o + 8)
                blobs.append((alloc, cnt, esz, enc, words))
            xa, pa = blobs
            assert xa[0] == pa[0] and xa[2] == pa[2] and xa[3] == pa[3]
            data = struct.pack('<4H%dI' % len(xa[4]), xa[0], xa[1], xa[2], xa[3], *xa[4])
            print('%-28s PC %d -> X360 %d elements' % (n, pa[1], xa[1]))
            recs.append((ck, key, foff, data, n, cname, fname))
    new = gen_new_collections(xb, pc)
    with open(OUT, 'w') as f:
        f.write('// Generated by tools_snapshot/attrib_x360.py from the X360 ATTRIBUTES.BIN (little-endian). Do not edit.\n')
        f.write('static const struct { uint32_t cls, key, off, len; const uint8_t *data; } k_layout_patch[] = {\n')
        for i, r in enumerate(recs):
            f.write('    {0x%08Xu, 0x%08Xu, %d, %d, d%d},   // %s::%s (%s)\n' % (r[0], r[1], r[2], len(r[3]), i, r[5], r[6], r[4]))
        f.write('};\n')
    # arrays must precede the table: rewrite with data first
    body = open(OUT).read().split('\n', 1)
    with open(OUT, 'w') as f:
        f.write(body[0] + '\n')
        for i, r in enumerate(recs):
            f.write('static const uint8_t d%d[] = {%s};\n' % (i, ','.join(str(x) for x in r[3])))
        f.write(body[1])
        for i, (vname, n, k0, k1, blob, colls) in enumerate(new):
            f.write('static const uint8_t g6_blob%d[] = {%s};   // %s\n' % (i, ','.join(str(x) for x in blob.b), vname))
            f.write('static const uint16_t g6_reloc%d[] = {%s};\n' % (i, ','.join(str(x) for x in blob.relocs)))
            f.write('static const uint16_t g6_coll%d[] = {%s};   // CollectionLoadData offsets, parents first\n' % (i, ','.join(str(c[2]) for c in colls)))
        f.write('// drag-race vaults gaining the X360 collections: PC export count and first/last export key identify the vault\n')
        f.write('static const struct { uint32_t nexp, k0, k1, ncoll, nreloc, size; const uint8_t *blob; const uint16_t *reloc, *coll; const char *name; } k_g6[] = {\n')
        for i, (vname, n, k0, k1, blob, colls) in enumerate(new):
            f.write('    {%d, 0x%08Xu, 0x%08Xu, %d, %d, %d, g6_blob%d, g6_reloc%d, g6_coll%d, "%s"},\n' % (n, k0, k1, len(colls), len(blob.relocs), len(blob.b), i, i, i, vname))
        f.write('};\n')


main()
