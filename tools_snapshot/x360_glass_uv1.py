"""R19 building glass: the X360 reflection V (glassreflect VS 09BC692C TEXCOORD1.y) for every PC glossywindow vertex.

usage: python -I x360_glass_uv1.py <X360 files dir> <PC game dir> <out .inc> [--work DIR]

The X360 glass vertex has two UVs (stream 0, 32 bytes BE: position f3, UV0 f2, UV1 f2, colour; stream 1, 8 bytes:
normal SHORT4N); the PC's (36 bytes: position f3, normal f3, colour, UV0 f2) has one, so the PC port has no source
for the reflection V. This tool decodes both STREAML2RA.BUN bundles and gives each PC glass vertex the UV1.y of its
uniquely corresponding X360 vertex: same solid name, X360 effect 15 (record +0x1C) vs PC effect 5 (record +0x30),
position within 1e-3, normals agreeing (dot > 0.99) and UV0 within 0.02 (the X360 UV0s sit ~1/1024 further out
than the PC's: a different texel inset, so UV1 is not an affine function of the PC UV0 for every buffer). A vertex
whose candidates disagree on UV1.y (a seam) or that has none stays unmatched (the runtime keeps .5*UV0.y there).
No camera-dependent fitting.

PC vertex buffers follow the PC loader (006D6820, chunk 134B01): each 134B01 belongs to the next mesh entry and to the
following entries with the same effect; the runtime finds a buffer by the FNV-1a 64 of its bytes (the D3D buffer the
loader fills is exactly that chunk payload), so any instance or reload of the same solid finds the same row.

Output: the distinct UV1.y values, one row per buffer (hash, vertices, first value) and a uint16 index per vertex
(0xFFFF = unmatched). Checks (fail = no output): unit normals on both sides, every PC buffer a whole number of
vertices, rows unique per hash (identical buffers with different UV1 rows are dropped and counted), and a negative
control: the X360 positions moved by 1 unit must match < 1 % of what the real positions match.
X360 HUFF/JDLZ blocks use Setup's bounded native decoders; only validated complete sections become a cache.
The recompiled HUFF oracle remains available in x360_auxlights.py for offline equivalence checks.
"""
import collections
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools_snapshot"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

def unpack_x360(src, work, exe):
    """Bounded streaming decode; publish the cache only after every block passes.

    Uses the same native decoder as Setup/texture conversion. Reordered blocks
    are written at their declared destinations, with sections aligned to 0x1000.
    The old C fast path left HUFF as zero and trusted sizes; no partial cache is
    reusable after a decode failure. exe stays in the API for existing callers.
    """
    from pathlib import Path
    import x360_auxlights as aux
    from setup_kit.huff import NativeHuff
    out = Path(work) / "x360_stream.bun"
    partial = Path(work) / "x360_stream.partial"
    native = NativeHuff(work)
    sections, length = [], 0
    try:
        with open(src, "rb") as source, partial.open("wb") as target:
            for group in aux.stream_groups(source):
                start = (length + 0xfff) & ~0xfff
                total = 0
                for pos, usize, bsize, dst in sorted(group, key=lambda b: b[3]):
                    if dst != total or not usize or dst + usize > 512 << 20:
                        raise ValueError("missing/overlapping glass stream block")
                    packed = aux.exact_read(source, pos + 24, bsize - 24)
                    data = (native.jdlz(packed) if packed[:4] == b"JDLZ" else
                            native(packed) if packed[:4] == b"HUFF" else packed[:usize])
                    if len(data) != usize:
                        raise ValueError("glass decoded size mismatch")
                    target.seek(start + dst)
                    target.write(data)
                    total += usize
                length = start + total
                sections.append([start, length])
            if not sections:
                raise ValueError("empty glass stream")
        partial.replace(out)
        return out.read_bytes(), sections
    finally:
        native.close()
        partial.unlink(missing_ok=True)


def walk(d, off, end, depth=0, out=None):
    out = [] if out is None else out
    while off + 8 <= end:
        cid, size = struct.unpack_from("<II", d, off)
        out.append((depth, cid, off, size))
        if cid & 0x80000000:
            walk(d, off + 8, off + 8 + size, depth + 1, out)
        off += 8 + size
    return out


def payload(d, off):
    """chunk payload start past the 0x11 alignment fill"""
    p = off + 8
    while d[p] == 0x11:
        p += 1
    return p


def solids(d, start, end):
    out, cur = [], None
    for depth, cid, off, size in walk(d, start, end):
        if cid == 0x134011:
            h = payload(d, off)
            cur = {"name": d[h + 0xa0:h + 0xc0].split(b"\0")[0].decode("latin1"), "seq": []}
            out.append(cur)
        elif cur is not None and cid in (0x134900, 0x134b02, 0x134b03):
            cur[cid] = (off, size)
        elif cur is not None and cid in (0x134b01, 0x134c02):
            cur["seq"].append((cid, off, size))
    return [s for s in out if 0x134b02 in s and 0x134900 in s]


def top(d, ranges):
    for a, b in ranges:
        off = a
        while off + 8 <= b:
            cid, size = struct.unpack_from("<II", d, off)
            if off + 8 + size > b:
                break
            if cid == 0x80134000:
                yield off, off + 8 + size
            off += 8 + size


def x360_glass(d, s):
    """vertices (n, 10): position, normal, UV0, UV1 of the solid's effect-15 draws"""
    n = struct.unpack_from("<I", d, payload(d, s[0x134900][0]) + 0x10)[0]
    b = (s[0x134b02][0] + 0x87) & ~0x7f
    recs = [d[b + i * 0x100:b + i * 0x100 + 0x100] for i in range(n)]
    groups, cur = [], None
    for cid, off, size in s["seq"]:
        if cid == 0x134c02:
            cur = []
            groups.append(cur)
        elif cur is not None:
            cur.append(off)
    assert len(groups) == n, f"{s['name']}: {len(groups)} stream groups for {n} draws"
    out = []
    for r, g in zip(recs, groups):
        if struct.unpack_from("<I", r, 0x1c)[0] != 15:
            continue
        sizes = {}
        for k in range(6):
            size, sid, fl = struct.unpack_from("<I", r, 0x58 + 32 * k)[0], r[0x5d + 32 * k], struct.unpack_from("<H", r, 0x5e + 32 * k)[0]
            sizes[sid] = (size, fl & 0x7fff)
            if fl & 0x8000:
                break
        assert sizes[0][1] == 32 and sizes[1][1] == 8 and len(g) == 2, f"{s['name']}: glass streams {sizes}"
        a0, a1 = (g[0] + 0x87) & ~0x7f, (g[1] + 0x87) & ~0x7f
        nv = sizes[0][0] // 32
        assert nv == sizes[1][0] // 8
        f = np.frombuffer(d[a0:a0 + nv * 32], ">f4").reshape(nv, 8)
        nrm = np.frombuffer(d[a1:a1 + nv * 8], ">i2").reshape(nv, 4)[:, :3] / 32767.0
        out.append(np.concatenate([f[:, 0:3], nrm, f[:, 3:7]], 1))
    return np.concatenate(out) if out else None


def pc_glass(d, s):
    """[(buffer bytes, vertices (n, 8): position, normal, UV0)] of the solid's effect-5 vertex buffers"""
    n = struct.unpack_from("<I", d, payload(d, s[0x134900][0]) + 0x10)[0]
    b = payload(d, s[0x134b02][0])
    eff = [struct.unpack_from("<I", d, b + i * 0x68 + 0x30)[0] for i in range(n)]
    out, e = [], 0
    for cid, off, size in s["seq"]:
        if cid != 0x134b01:
            continue
        if e >= n:
            break
        va = (off + 0x87) & ~0x7f
        nbytes = off + 8 + size - va
        first, e = e, e + 1
        while e < n and eff[e] == eff[e - 1]:
            e += 1
        if eff[first] != 5:
            continue
        assert nbytes % 36 == 0, f"{s['name']}: glass buffer of {nbytes} bytes"
        raw = d[va:va + nbytes]
        f = np.frombuffer(raw, "<f4").reshape(-1, 9)
        out.append((raw, np.concatenate([f[:, 0:6], f[:, 7:9]], 1)))
    return out


def fnv64(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def correspond(pv, xv, shift=0.0):
    """UV1.y per PC vertex (nan = none) and the reasons"""
    q = 1e-3
    table = collections.defaultdict(list)
    for v in xv:
        table[tuple(np.round((v[0:3] + shift) / q).astype(np.int64))].append(v)
    y, why = np.full(len(pv), np.nan, np.float32), collections.Counter()
    for j, p in enumerate(pv):
        c = [v for v in table.get(tuple(np.round(p[0:3] / q).astype(np.int64)), []) if np.dot(v[3:6], p[3:6]) > 0.99]
        if not c:
            why["no_position_normal"] += 1
            continue
        c = [v for v in c if abs(v[6] - p[6]) < 0.02 and abs(v[7] - p[7]) < 0.02]
        if not c:
            why["uv0_differs"] += 1
            continue
        ys = {float(v[9]) for v in c}
        if len(ys) > 1:
            why["seam_conflict"] += 1
            continue
        y[j] = ys.pop()
        why["matched"] += 1
    return y, why


def main():
    x360_dir, pc_dir, out = sys.argv[1:4]
    work = sys.argv[sys.argv.index("--work") + 1] if "--work" in sys.argv else os.path.dirname(os.path.abspath(out))
    os.makedirs(work, exist_ok=True)
    xd, secs = unpack_x360(os.path.join(x360_dir, "TRACKS", "STREAML2RA.BUN"), work, os.path.join(pc_dir, "speed.exe"))
    x360 = {}
    for a, b in top(xd, secs):
        for s in solids(xd, a, b):
            if s["name"] not in x360:
                v = x360_glass(xd, s)
                if v is not None:
                    x360[s["name"]] = v
    pd = open(os.path.join(pc_dir, "TRACKS", "STREAML2RA.BUN"), "rb").read()
    rows, why, conflicts, total, ctrl = {}, collections.Counter(), 0, 0, collections.Counter()
    pending = []
    for a, b in top(pd, [(0, len(pd))]):
        for s in solids(pd, a, b):
            for raw, pv in pc_glass(pd, s):
                pending.append((s["name"], raw, pv))
    allx = np.concatenate(list(x360.values()))
    allp = np.concatenate([pv for _, _, pv in pending])
    for v, what in ((allx, "X360"), (allp, "PC")):
        ln = np.linalg.norm(v[:, 3:6], axis=1)
        assert np.abs(ln - 1).max() < 0.01, f"{what} normals {ln.min():.3f}..{ln.max():.3f}"
    for name, raw, pv in pending:
        h = fnv64(raw)
        prev = rows.get(h)
        if prev is not None and prev[0] == name:
            continue                                    # another instance of the same solid
        if prev is None:
            total += len(pv)
        xv = x360.get(name)
        if xv is None:
            y, w = np.full(len(pv), np.nan, np.float32), collections.Counter(no_x360_solid=len(pv))
        else:
            y, w = correspond(pv, xv)
        if prev is None:
            why += w
        if xv is not None and sum(ctrl.values()) < 20000:
            ctrl += correspond(pv, xv, shift=1.0)[1]
        prev = rows.get(h)
        if prev is not None and not np.array_equal(prev[1], y, equal_nan=True):
            conflicts += 1                              # the same bytes in two solids, different UV1: keep neither
            why["matched"] -= int((~np.isnan(prev[1])).sum())
            why["duplicate_conflict"] += int((~np.isnan(prev[1])).sum())
            y = np.full(len(pv), np.nan, np.float32)
        rows[h] = (name, y)
    matched = why["matched"]
    assert ctrl["matched"] < 0.01 * max(1, sum(ctrl.values())), f"negative control matched {ctrl}"
    values = sorted({float(x) for _, y in rows.values() for x in y if not np.isnan(x)})
    assert len(values) < 0xFFFF
    index = {v: i for i, v in enumerate(values)}
    lines = ["/* Generated by tools_snapshot/x360_glass_uv1.py; do not edit. */",
             f"#define GLASS_UV1_VERTICES {total}u", f"#define GLASS_UV1_MATCHED {matched}u",
             f"#define GLASS_UV1_BUFFERS {len(rows)}u", f"#define GLASS_UV1_CONFLICTS {conflicts}u",
             f"#define GLASS_UV1_VALUES {len(values)}u",
             "/* unmatched: " + ", ".join(f"{k} {v}" for k, v in sorted(why.items()) if k != "matched") +
             f"; negative control (positions + 1): {ctrl['matched']} of {sum(ctrl.values())} */",
             "static const float glass_uv1_values[] = {"]
    for i in range(0, len(values), 8):
        lines.append("    " + ", ".join(f"{float(v).hex()}f" for v in values[i:i + 8]) + ",")
    lines.append("};")
    lines.append("static const struct { uint64_t hash; uint32_t vertices, first; } glass_uv1_table[] = {")
    stream, first = [], 0
    for h in sorted(rows):
        y = rows[h][1]
        lines.append(f"    {{0x{h:016X}ull, {len(y)}u, {first}u}},")
        stream += [0xFFFF if np.isnan(x) else index[float(x)] for x in y]
        first += len(y)
    lines.append("};")
    lines.append("static const uint16_t glass_uv1_index[] = {")
    for i in range(0, len(stream), 24):
        lines.append(",".join(str(x) for x in stream[i:i + 24]) + ",")
    lines.append("};")
    open(out, "w").write("\n".join(lines) + "\n")
    print(f"{matched}/{total} PC glass vertices matched ({100.0 * matched / max(1, total):.1f} %), {len(rows)} buffers, "
          f"{conflicts} conflicting duplicates, {len(values)} distinct UV1.y; unmatched {dict(why)}; control {dict(ctrl)}")


if __name__ == "__main__":
    main()
