#!/usr/bin/env python3
"""Compare every Attrib collection of the PC v1.3 and X360 retail vault packs (ATTRIBUTES.BIN, GAMEPLAY.BIN,
FE_ATTRIB.BIN), endian-aware. Writes attrib_collections.tsv, attrib_field_diffs.tsv, attrib_classes.tsv.
Usage: compare_vaults.py PC_GLOBAL_DIR X360_GLOBAL_DIR OUT_DIR   (read-only on the game data)
Vendored from the parity scripts for tools/gen_tables.py: no built-in paths; the generators import Side/atom_eq."""
import math, os, struct, sys
from collections import Counter, defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vaultlib import Vault, read_vpak, hash32
import names as namesmod

PCG = XBG = OUT = None   # set from the command line by __main__
FILES = [("ATTRIBUTES.BIN", "attributes.bin"), ("GAMEPLAY.BIN", "gameplay.bin"), ("FE_ATTRIB.BIN", "FE_ATTRIB.bin")]

T_DB, T_CLASS, T_COLL = 0xcbbc628f, 0x5e970cbc, 0x8e112eb7  # ExpN type hashes (seen in every vault)

PRIM = {
    'EA::Reflection::Double': 'd', 'EA::Reflection::UInt16': 'H', 'EA::Reflection::UInt64': 'Q',
    'EA::Reflection::Bool': 'B', 'EA::Reflection::Char': 'b', 'EA::Reflection::Int32': 'i',
    'EA::Reflection::Float': 'f', 'EA::Reflection::Int16': 'h', 'EA::Reflection::UInt32': 'I',
    'EA::Reflection::UInt8': 'B', 'EA::Reflection::Int8': 'b', 'EA::Reflection::Int64': 'q',
    'Attrib::Key': 'I', 'Attrib::Types::Symbol': 'I', 'type_bStringHash': 'I', 'GCollectionKey': 'I',
    'Attrib::Types::Vector2': '2f', 'Attrib::Types::Vector3': '3f', 'Attrib::Types::Vector4': '4f',
    'Attrib::Types::Quaternion': '4f', 'Attrib::Types::FloatColour': '4f', 'Attrib::Types::Matrix': '16f',
}


class Side:
    def __init__(self, gdir, plat, e):
        self.plat, self.e = plat, e
        self.types = {}      # hash -> (name, size)
        self.classes = {}    # key -> (layoutSize, defs{key:(type,off,size,maxc,flags,align)}, file, vault)
        self.colls = {}      # (file, vault, class, key) -> dict
        self.strings = set()
        self.vaults = []
        for canon, pcname in FILES:
            p = os.path.join(gdir, pcname if plat == 'PC' else canon)
            if not os.path.exists(p):
                p = os.path.join(gdir, canon)
            for name, b, v, bo, vo in read_vpak(p):
                V = Vault(name, b, v, e, bo, vo)
                V.file = canon
                self.vaults.append(V)
                self._strings(V)
                for k, t, s, do in V.exports:
                    if t == T_DB:
                        self._db(V, do)

    def parse(self, shared_types):
        # type tables are per-vault lists (PC and X360 'db' list different subsets of 88); type hashes are
        # name hashes, so the union of both platforms' tables is used for decoding
        self.own_types = dict(self.types)
        self.types = shared_types
        for V in self.vaults:
            for k, t, s, do in V.exports:
                if t == T_CLASS:
                    self._class(V, do)
        for V in self.vaults:
            for k, t, s, do in V.exports:
                if t == T_COLL:
                    self._coll(V, do)

    def _strings(self, V):
        b = V.bin[8:V.stre_end] if V.stre_end else b''
        for s in b.split(b'\0'):
            if 1 < len(s) < 100:
                self.strings.add(s.decode('latin1'))

    def _db(self, V, do):
        nt = V.u32(V.vlt, do)
        tp = V.deref(0, do + 12)
        off = tp[1]
        for i in range(nt):
            n = V.cstr(tp[0], off); off += len(n) + 1
            self.types[hash32(n)] = (n, V.u32(V.vlt, do + 16 + 4 * i))

    def _class(self, V, do):
        v = V.vlt
        key = V.u32(v, do); nd = V.u32(v, do + 8) & 0xFFFF; lsz = V.u32(v, do + 0x10) & 0xFFFF  # u16 in a u32 slot (LE low half)
        dp = V.deref(0, do + 0xc)
        defs = {}
        if dp:
            b = V.bufs[dp[0]]
            for i in range(nd):
                o = dp[1] + 16 * i
                dk, dt = struct.unpack_from(V.e + 'II', b, o)
                off, sz, mc = struct.unpack_from(V.e + 'HHH', b, o + 8)
                defs[dk] = (dt, off, sz, mc, b[o + 14], b[o + 15])
        self.classes.setdefault(key, (lsz, defs, V.file, V.name))

    def _coll(self, V, do):
        v, e = V.vlt, V.e
        key, cls, parent, tres, x10, nent, ntyp = struct.unpack_from(e + '7I', v, do)
        lay = V.deref(0, do + 0x1c)
        types = struct.unpack_from(e + '%dI' % ntyp, v, do + 0x20)
        fields = {}
        cdef = self.classes.get(cls)
        if cdef and lay:
            for dk, (dt, off, sz, mc, fl, al) in cdef[1].items():
                if fl & 2 and not fl & 8:
                    if fl & 1:
                        fields[dk] = ('L[]', dt, self.array(V, lay[0], lay[1] + off, dt))
                    else:
                        fields[dk] = ('L', dt, [self.elem(V, dt, lay[0], lay[1] + off)])
        eo = do + 0x20 + 4 * ntyp
        for i in range(nent):
            o = eo + 12 * i
            k, = struct.unpack_from(e + 'I', v, o)
            ti, = struct.unpack_from(e + 'H', v, o + 8)
            fl = v[o + 10]
            dt = types[ti] if ti < len(types) else 0
            if fl & 0x20:
                fields[k] = ('E', dt, [self.elem(V, dt, 0, o + 4)])
            else:
                p = V.deref(0, o + 4)
                if p is None:
                    fields[k] = ('E?', dt, [('unresolved',)])
                elif fl & 2:
                    fields[k] = ('E[]', dt, self.array(V, p[0], p[1], dt))
                else:
                    fields[k] = ('E', dt, [self.elem(V, dt, p[0], p[1])])
        self.colls[(V.file, V.name, cls, key)] = dict(parent=parent, fields=fields, off=V.vlt_off + do, flags=fl if nent else None)

    def array(self, V, dep, off, dt):
        b = V.bufs[dep]
        alloc, cnt, esz, enc = struct.unpack_from(V.e + '4H', b, off)
        base = off + 8 + (8 if enc & 0x8000 else 0)
        if esz == 0:
            return [('arr_indirect', cnt)] + [self.elem(V, 'raw', dep, base + 4 * i, 4) for i in range(cnt)]
        return [self.elem(V, dt, dep, base + esz * i, esz) for i in range(cnt)]

    def elem(self, V, dt, dep, off, size=None):
        b = V.bufs[dep]
        tn, tsz = self.types.get(dt, ('?%08x' % dt, 4)) if dt != 'raw' else ('raw', 4)
        if size is None:
            size = tsz
        e = V.e
        if tn in PRIM:
            f = PRIM[tn]
            if struct.calcsize(f) <= len(b) - off:
                return ('v', struct.unpack_from(e + f, b, off))
        if tn == 'EA::Reflection::Text':
            return ('v', (self.ptr(V, dep, off),))
        if tn == 'Attrib::StringKey':
            h64, h32 = struct.unpack_from(e + 'QI', b, off)
            return ('v', (h64, h32, self.ptr(V, dep, off + 12)))
        if tn == 'Attrib::RefSpec':
            return ('v', struct.unpack_from(e + 'II', b, off))
        # generic record: 4-byte words, pointer sites resolved
        words = []
        for k in range(0, size - size % 4, 4):
            if (dep, off + k) in V.fix:
                words.append(('p', self.ptr(V, dep, off + k)))
            else:
                words.append(('r', bytes(b[off + k:off + k + 4])))
        for k in range(size - size % 4, size):
            words.append(('b', b[off + k]))
        return ('g', tuple(words))

    def ptr(self, V, dep, off):
        f = V.fix.get((dep, off))
        if f is None:
            return ('noptr', struct.unpack_from(V.e + 'I', V.bufs[dep], off)[0])
        if f[0] == 'null':
            return None
        if f[0] == 'exp':
            return ('exp', f[2])
        return V.cstr(f[1], f[2])[:200]


def feq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return a == b


def atom_eq(a, b):
    """a from PC (LE), b from X360 (BE). Returns (equal, how)."""
    if a[0] != b[0]:
        return False, 'kind'
    if a[0] == 'v':
        return (len(a[1]) == len(b[1]) and all(feq(x, y) for x, y in zip(a[1], b[1]))), 'typed'
    if a[0] == 'g':
        if len(a[1]) != len(b[1]):
            return False, 'len'
        how = 'typed'
        xs, ys = list(a[1]), list(b[1])
        for i in range(len(xs) - 1):  # embedded 64-bit values (e.g. StringKey hash64 in CopCountRecord)
            if xs[i][0] == ys[i][0] == xs[i + 1][0] == ys[i + 1][0] == 'r' and xs[i][1] != ys[i][1][::-1] \
                    and xs[i][1] + xs[i + 1][1] == (ys[i][1] + ys[i + 1][1])[::-1]:
                ys[i], ys[i + 1] = ('r', xs[i][1][::-1]), ('r', xs[i + 1][1][::-1]); how = 'swap64'
        for x, y in zip(xs, ys):
            if x[0] != y[0]:
                return False, 'kind'
            if x[0] == 'r':
                if x[1] == y[1][::-1]:
                    continue
                if x[1] == y[1][1::-1] + y[1][3:1:-1]:
                    how = 'swap16'; continue
                if x[1] == y[1]:
                    how = 'raw-bytes'; continue
                return False, 'word'
            elif x[1] != y[1]:
                return False, 'word'
        return True, how
    return a == b, 'other'


def fmt_atom(a, e):
    if a[0] == 'v':
        return ','.join(fmt_num(x) for x in a[1])
    if a[0] == 'g':
        out = []
        for w in a[1]:
            if w[0] == 'r':
                u = int.from_bytes(w[1], 'little' if e == '<' else 'big')
                f = struct.unpack('<f', u.to_bytes(4, 'little'))[0]
                out.append(fmt_num(f) if (u and 1e-6 < abs(f) < 1e9) else str(u) if u < 0x100000 else '0x%08x' % u)
            else:
                out.append(fmt_num(w[1]))
        return '{' + ' '.join(out) + '}'
    return str(a)


def fmt_num(x):
    if isinstance(x, float):
        return '%.6g' % x
    if isinstance(x, int) and x > 0xFFFFF:
        return '0x%x' % x
    return repr(x) if not isinstance(x, int) else str(x)


def main():
    pc, xb = Side(PCG, 'PC', '<'), Side(XBG, 'X360', '>')
    shared = dict(xb.types); shared.update(pc.types)
    pc.parse(shared); xb.parse(shared)
    print('type names only in PC db:', sorted(set(n for n, _ in pc.own_types.values()) - set(n for n, _ in xb.own_types.values())))
    print('type names only in X360 db:', sorted(set(n for n, _ in xb.own_types.values()) - set(n for n, _ in pc.own_types.values())))
    N = namesmod.build(pc.strings | xb.strings | {V.name for V in pc.vaults + xb.vaults})
    nm = lambda h: N.get(h, '0x%08x' % h) if isinstance(h, int) else str(h)
    tn = lambda side, h: side.types.get(h, ('0x%08x' % h if isinstance(h, int) else str(h), 0))[0]
    os.makedirs(OUT, exist_ok=True)
    # classes
    with open(os.path.join(OUT, 'attrib_classes.tsv'), 'w') as f:
        f.write('class_hash\tclass\tstatus\tpc_layout\tx360_layout\tpc_defs\tx360_defs\tdef_diffs\n')
        for ck in sorted(set(pc.classes) | set(xb.classes), key=lambda h: nm(h)):
            a, b = pc.classes.get(ck), xb.classes.get(ck)
            if not a or not b:
                f.write('0x%08x\t%s\t%s\t\t\t\t\t\n' % (ck, nm(ck), 'PC-only' if a else 'X360-only')); continue
            dd = []
            for dk in sorted(set(a[1]) | set(b[1])):
                x, y = a[1].get(dk), b[1].get(dk)
                if x != y:
                    dd.append('%s:%s->%s' % (nm(dk), x and (tn(pc, x[0]),) + x[1:], y and (tn(xb, y[0]),) + y[1:]))
            st = 'identical' if not dd and a[0] == b[0] else 'differs'
            f.write('0x%08x\t%s\t%s\t%d\t%d\t%d\t%d\t%s\n' % (ck, nm(ck), st, a[0], b[0], len(a[1]), len(b[1]), ' | '.join(dd)))
    # collections
    stats = Counter(); hows = Counter()
    with open(os.path.join(OUT, 'attrib_collections.tsv'), 'w') as fc, \
            open(os.path.join(OUT, 'attrib_field_diffs.tsv'), 'w') as fd:
        fc.write('file\tvault\tclass_hash\tclass\tcoll_hash\tcollection\tstatus\tpc_parent\tx360_parent\tpc_fields\tx360_fields\tdiff_fields\tpc_vlt_off\tx360_vlt_off\n')
        fd.write('file\tvault\tclass\tcollection\tfield_hash\tfield\ttype\twhere\tkind\tpc_value\tx360_value\n')
        for key in sorted(set(pc.colls) | set(xb.colls), key=lambda k: (k[0], k[1], nm(k[2]), nm(k[3]))):
            a, b = pc.colls.get(key), xb.colls.get(key)
            file, vault, ck, kk = key
            row = [file, vault, '0x%08x' % ck, nm(ck), '0x%08x' % kk, nm(kk)]
            if not a or not b:
                s = a or b
                st = 'PC-only' if a else 'X360-only'
                stats[(file, st)] += 1
                fc.write('\t'.join(row + [st, nm(s['parent']) if a else '', nm(s['parent']) if b else '',
                                          str(len(a['fields'])) if a else '', str(len(b['fields'])) if b else '', '',
                                          '0x%x' % a['off'] if a else '', '0x%x' % b['off'] if b else '']) + '\n')
                continue
            diffs = []
            if a['parent'] != b['parent']:
                diffs.append(('<parent>', '', '', 'parent', nm(a['parent']), nm(b['parent'])))
            for fk in sorted(set(a['fields']) | set(b['fields']), key=nm):
                x, y = a['fields'].get(fk), b['fields'].get(fk)
                if not x or not y:
                    s = x or y
                    diffs.append(('0x%08x' % fk, nm(fk), tn(pc if x else xb, s[1]), s[0],
                                  'field PC-only' if x else 'field X360-only',
                                  ';'.join(fmt_atom(t, '<') for t in x[2])[:400] if x else '',
                                  ';'.join(fmt_atom(t, '>') for t in y[2])[:400] if y else ''))
                    continue
                ok = len(x[2]) == len(y[2])
                if ok:
                    for p_, q_ in zip(x[2], y[2]):
                        eq, how = atom_eq(p_, q_)
                        hows[(how if eq else 'diff:' + how) + ('' if how == 'typed' else ' ' + tn(pc, x[1]))] += 1
                        if not eq:
                            ok = False; break
                if not ok:
                    kind = 'value' if len(x[2]) == len(y[2]) else 'count %d->%d' % (len(x[2]), len(y[2]))
                    pv, xv = [], []
                    for i in range(max(len(x[2]), len(y[2]))):
                        p_ = x[2][i] if i < len(x[2]) else None
                        q_ = y[2][i] if i < len(y[2]) else None
                        if p_ is None or q_ is None or not atom_eq(p_, q_)[0]:
                            pre = '[%d]' % i if max(len(x[2]), len(y[2])) > 1 else ''
                            pv.append(pre + (fmt_atom(p_, '<') if p_ else '-'))
                            xv.append(pre + (fmt_atom(q_, '>') if q_ else '-'))
                    diffs.append(('0x%08x' % fk, nm(fk), tn(pc, x[1]), x[0], kind, ';'.join(pv)[:600], ';'.join(xv)[:600]))
            st = 'identical' if not diffs else 'differs'
            stats[(file, st)] += 1
            fc.write('\t'.join(row + [st, nm(a['parent']), nm(b['parent']), str(len(a['fields'])), str(len(b['fields'])),
                                      ','.join(d[1] or d[0] for d in diffs)[:500], '0x%x' % a['off'], '0x%x' % b['off']]) + '\n')
            for d in diffs:
                fd.write('\t'.join([file, vault, nm(ck), nm(kk)] + list(d[:2]) + [d[2], d[3], d[4], d[5], d[6]]) + '\n')
    for k in sorted(stats):
        print(*k, stats[k], sep='\t')
    print('atom compare modes:', dict(hows))
    print('types PC/X360:', len(pc.types), len(xb.types), 'classes:', len(pc.classes), len(xb.classes),
          'names resolved:', len(N))


if __name__ == '__main__':
    PCG, XBG, OUT = sys.argv[1:4]
    main()
