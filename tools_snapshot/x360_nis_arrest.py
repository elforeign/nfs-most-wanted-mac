"""X360 arrest-scene characters for the PC: the PC NIS bundles rebuilt with the X360's higher-detail character meshes.

Usage: python -I x360_nis_arrest.py <X360 files> <PC game> <out dir>
Writes <out>/Scene_Arrest*_BundleB.bun (the PC bundle with compatible character meshes replaced) and
<out>/textures.tsv (the X360 textures those meshes use: hash, the alias the meshes name, X360 file), which
runtime/x360_textures.m registers and boot.c serves the bundles under NFS_X360_ARREST=x360.

The X360 solids (FCOP01/02, MCOP01/02, PERP01; CUFFS01 is the same) have ~3x the triangles, one vertex buffer per
draw split over two big-endian streams (48 bytes: position f3, uv f2, 4 unused, weights f3, palette slots f3; 16
bytes: normal and tangent as SHORT4N), 0x100-byte draw records (palette at +0x30, light material +0x2a (by hash into the PC's list), the
five texture slots +0x2b..+0x2f (diffuse, normal, height, specular, opacity: the PC's order), flags +0x24, first index +0x18,
index count +0x28) and their own texture lists (renamed art with a different UV layout, plus normal/specular maps). The PC's worldbone draws need one 60-byte vertex buffer per
solid (position f3, normal f3, colour 0, uv f2, weights f3, palette slots f3; 16-bit indices, BaseVertex 0, so
< 65536 vertices), 0x68-byte records (bbox, 5 texture slots +0x18, light material +0x1d, 16-bone palette +0x1e,
effect +0x30, flags +0x38, vertices +0x3c, triangles +0x40, first index +0x44, index count +0x5c) and a material
name per record. All five slots are kept (the X360 indices into the rebuilt list), so 006c6d10 caches the normal and
specular maps in 00982CB8/CC0 for runtime/materials.m's X360 worldbone shader; the PC's worldbone shader samples only
the diffuse. Tangents are not kept: the X360's are the uv-derivative frame (T = +d/du, w x cross(N, T) = +d/dv, median cos
0.82-0.997 over every normal-mapped draw), which the shader rebuilds per pixel.

Checks per solid (fail = no output): every X360 triangle kept, indices inside their draw, weights sum to 1,
palette slots integral and < 16, unit normals, normals agreeing with the PC mesh's at the nearest PC vertex
(median dot > 0.9), positions inside the X360 bbox. Negative controls run on each solid: weights/uv swapped and one
index out of range must both be rejected.
PC bind-pose coverage must also match: at most 5% of PC vertices may be more than 0.1 m from their nearest X360
vertex. Solids failing coverage retain their original PC mesh, textures and palettes, with one fallback log line.
"""
import os
import re
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x360_tex as tex
from setup_kit import huff


def walk(d, off, end, depth=0, out=None):
    out = [] if out is None else out
    while off + 8 <= end:
        cid, size = struct.unpack_from("<II", d, off)
        out.append((depth, cid, off, size))
        if cid & 0x80000000:
            walk(d, off + 8, off + 8 + size, depth + 1, out)
        off += 8 + size
    return out


def solids(d):
    """name -> {chunk id: (off, size), 'b01': [...], 'c02': [...], 'solid': (off, size)}"""
    out, cur, top = {}, None, None
    for depth, cid, off, size in walk(d, 0, len(d)):
        if cid == 0x80134000:
            top = (off, size)
        elif cid == 0x134011:
            cur = {"solid": top, "b01": [], "c02": [], 0x134011: (off, size)}
            out[d[off + 8 + 0xa8:off + 8 + 0xb8].split(b"\0")[0].decode()] = cur
        elif cur is not None and cid in (0x134012, 0x134013, 0x134900, 0x134b02, 0x134b03):
            cur[cid] = (off, size)
        elif cur is not None and cid == 0x134b01:
            cur["b01"].append((off, size))
        elif cur is not None and cid == 0x134c02:
            cur["c02"].append((off, size))
    return out


def a16(o):
    return (o + 0x17) & ~0xf


def x360_mesh(d, s):
    """The X360 solid's draws: [(record bytes, vertices (n, 15) PC layout, indices)]"""
    n = struct.unpack_from("<I", d, a16(s[0x134900][0]) + 0x10)[0]
    o, _ = s[0x134b02]
    recs = [d[((o + 0x87) & ~0x7f) + i * 0x100:][:0x100] for i in range(n)]
    io, isz = s[0x134b03]
    ia = (io + 0x27) & ~0x1f
    idx = np.frombuffer(d[ia:io + 8 + isz], ">u2")
    # 134C02 starts each draw's group; its 134B01s are its streams in order
    groups, cur = [], None
    for depth, cid, off, size in walk(d, s["solid"][0] + 8, s["solid"][0] + 8 + s["solid"][1]):
        if cid == 0x134c02:
            cur = []
            groups.append(cur)
        elif cid == 0x134b01:
            cur.append((off, size))
    assert len(groups) == n, f"{len(groups)} stream groups for {n} draws"
    draws = []
    for r, g in zip(recs, groups):
        streams = {}
        for k in range(6):
            size, sid, flags = struct.unpack_from("<IBxH", r, 0x58 + 32 * k)[0], r[0x5d + 32 * k], struct.unpack_from("<H", r, 0x5e + 32 * k)[0]
            streams[sid] = (size, flags & 0x7fff)
            if flags & 0x8000:
                break
        assert sorted(streams) == [0, 1] and streams[0][1] == 48 and streams[1][1] == 16, f"stream layout {streams}"
        (o0, z0), (o1, z1) = g
        s0 = d[(o0 + 0x87) & ~0x7f:][:streams[0][0]]
        s1 = d[(o1 + 0x87) & ~0x7f:][:streams[1][0]]
        nv = len(s0) // 48
        assert nv == len(s1) // 16
        f0 = np.frombuffer(s0, ">f4").reshape(nv, 12)
        nrm = np.frombuffer(s1, ">i2").reshape(nv, 8)[:, :3] / 32767.0
        v = np.zeros((nv, 15), np.float32)
        v[:, 0:3], v[:, 3:6], v[:, 7:9], v[:, 9:12], v[:, 12:15] = f0[:, 0:3], nrm, f0[:, 3:5], f0[:, 6:9], f0[:, 9:12]
        first, count = struct.unpack_from("<I", r, 0x18)[0], struct.unpack_from("<H", r, 0x28)[0]
        draws.append((r, v, idx[first:first + count].astype(np.int64)))
    return draws


def nearest(a, b):
    """index into b of the nearest point to each row of a (positions)"""
    j = np.empty(len(a), np.int64)
    for k in range(0, len(a), 512):
        j[k:k + 512] = ((a[k:k + 512, None, :] - b[None, :, :]) ** 2).sum(2).argmin(1)
    return j


def pc_vertices(d, s):
    o, size = s["b01"][0]
    start = (o + 0x87) & ~0x7f
    return np.frombuffer(d[start:start + (o + 8 + size - start) // 60 * 60], "<f4").reshape(-1, 15)


def pc_coverage(draws, pc_v):
    """PC vertices farther than 0.1 m from X360, total PC vertices, maximum distance in metres."""
    positions = np.concatenate([v[:, :3] for _, v, _ in draws])
    j = nearest(pc_v[:, :3], positions)
    distances = np.linalg.norm(pc_v[:, :3] - positions[j], axis=1)
    return int((distances > 0.1).sum()), len(pc_v), float(distances.max())


def coverage_report(coverage):
    missing, total, maximum = coverage
    return f"PC coverage {missing}/{total} ({100 * missing / total:.3f}%) beyond 0.1 m, max {maximum:.6f} m"


def validate(draws, bbox, pc_v):
    """Reasons the converted draws are wrong (empty: fine)."""
    bad = []
    for i, (r, v, ix) in enumerate(draws):
        if len(ix) % 3 or (len(ix) and (ix.min() < 0 or ix.max() >= len(v))):
            bad.append(f"draw {i}: index outside its {len(v)} vertices")
        w = v[:, 9:12].sum(1)
        if np.abs(w - 1).max() > 1e-3 or v[:, 9:12].min() < -1e-6:
            bad.append(f"draw {i}: weights sum {w.min():.3f}..{w.max():.3f}")
        sl = v[:, 12:15]
        if np.abs(sl - np.round(sl)).max() > 0 or sl.min() < 0 or sl.max() > 15:
            bad.append(f"draw {i}: palette slots {sl.min()}..{sl.max()}")
        ln = np.linalg.norm(v[:, 3:6], axis=1)
        if np.abs(ln - 1).max() > 0.01:
            bad.append(f"draw {i}: normal length {ln.min():.3f}..{ln.max():.3f}")
        lo, hi = np.array(bbox[:3]) - 1e-3, np.array(bbox[3:]) + 1e-3
        if (v[:, :3] < lo).any() or (v[:, :3] > hi).any():
            bad.append(f"draw {i}: position outside the bbox")
    allv = np.concatenate([v for _, v, _ in draws])
    j = nearest(allv[:, :3], pc_v[:, :3])
    dot = np.median((allv[:, 3:6] * pc_v[j, 3:6]).sum(1))
    if dot < 0.9:
        bad.append(f"normals disagree with the PC's (median dot {dot:.3f})")
    coverage = pc_coverage(draws, pc_v)
    if coverage[0] / coverage[1] > 0.05:
        bad.append(coverage_report(coverage))
    return bad


def pc_records(d, s):
    o, _ = s[0x134b02]
    n = struct.unpack_from("<I", d, a16(s[0x134900][0]) + 0x10)[0]
    return [d[a16(o) + i * 0x68:][:0x68] for i in range(n)]


def chunk(cid, payload):
    return struct.pack("<II", cid, len(payload)) + payload


def aligned(pos, align, data, extra=8):
    """chunk payload placing data at the loader's alignment for a chunk header at pos"""
    start = (pos + 8 + align - 1) & ~(align - 1)
    return b"\x11" * (start - pos - 8) + data


def build_mesh(pos, pc_d, pc_s, draws, names, texslot, light):
    """80134100 chunk at file offset pos: PC-layout mesh for the converted draws"""
    pc_recs = pc_records(pc_d, pc_s)
    effect = struct.unpack_from("<I", pc_recs[0], 0x30)[0]
    vb, ib, recs, base, first = [], [], [], 0, 0
    for (r, v, ix), slot, lm in zip(draws, texslot, light):
        rec = bytearray(0x68)
        rec[0:0x18] = r[0:0x18]
        rec[0x18:0x1d] = bytes(slot)
        rec[0x1d] = lm
        rec[0x1e:0x2e] = r[0x30:0x40]
        struct.pack_into("<I", rec, 0x30, effect)
        struct.pack_into("<I", rec, 0x38, struct.unpack_from("<I", r, 0x24)[0] & 0xffff)
        struct.pack_into("<III", rec, 0x3c, len(v), len(ix) // 3, first)
        struct.pack_into("<I", rec, 0x5c, len(ix))
        recs.append(bytes(rec))
        vb.append(v)
        ib.append(ix + base)
        base += len(v)
        first += len(ix)
    assert base < 0xffff, f"{base} vertices: over the PC's 16-bit index limit"
    o, sz = pc_s[0x134900]
    head = bytearray(pc_d[a16(o):o + 8 + sz][:48])
    struct.pack_into("<I", head, 0x10, len(draws))
    struct.pack_into("<I", head, 0x2c, first)
    vbytes = np.concatenate(vb).astype("<f4")
    vraw = bytearray(vbytes.tobytes())
    vraw = np.frombuffer(vraw, np.uint8).reshape(-1, 60).copy()
    vraw[:, 24:28] = 0                                     # vertex colour (the PC's are all 0: no specular)
    parts, p = [], pos + 8
    for cid, align, data in ((0x134900, 16, bytes(head)), (0x134b02, 16, b"".join(recs)),
                             (0x134b03, 16, np.concatenate(ib).astype("<u2").tobytes()),
                             (0x134b01, 128, vraw.tobytes())):
        c = chunk(cid, aligned(p, align, data))
        parts.append(c)
        p += len(c)
    for nm in names:
        b = nm.encode() + b"\0"
        c = chunk(0x134c02, b + b"\0" * (-len(b) % 4))
        parts.append(c)
        p += len(c)
    return chunk(0x80134100, b"".join(parts))


def alias(h):
    """The hash the converted solids use for X360 diffuse map h: the X360 art under a PC name hash can have another
    UV layout (HERO_HEAD: 512x256, head UVs moved), so it can't share the PC texture's slot."""
    return h ^ 0x58333630                                    # "X360"


def convert_solid(pc_d, pc_s, x_d, x_s, pos):
    """(new 80134000 chunk for file offset pos, report, X360 texture hashes) or raises on a failed check"""
    draws = x360_mesh(x_d, x_s)
    xo = x_s[0x134011][0]
    bbox = struct.unpack_from("<3f4x3f", x_d, xo + 8 + 40)
    pc_v = pc_vertices(pc_d, pc_s)
    bad = validate(draws, bbox, pc_v)
    if bad:
        raise ValueError("; ".join(bad))
    # negative controls: a swapped attribute and an out-of-range index must fail
    sw = [(r, v.copy(), ix) for r, v, ix in draws]
    sw[0][1][:, [7, 8, 9, 10]] = sw[0][1][:, [9, 10, 7, 8]]
    oob = [(r, v, ix.copy()) for r, v, ix in draws]
    oob[0][2][0] = len(oob[0][1])
    assert validate(sw, bbox, pc_v) and validate(oob, bbox, pc_v), "a broken mesh passed the checks"
    # texture list: every map the draws' five slots name; material names from the PC draw nearest each X360 draw
    xt_o, xt_sz = x_s[0x134012]
    xlist = struct.unpack_from(f"<{xt_sz // 4}I", x_d, xt_o + 8)[0::2]
    textures = []
    for r, _, _ in draws:
        for k in range(5):
            if xlist[r[0x2b + k]] not in textures:
                textures.append(xlist[r[0x2b + k]])
    texslot = [[textures.index(xlist[r[0x2b + k]]) for k in range(5)] for r, _, _ in draws]
    pc_recs = pc_records(pc_d, pc_s)
    pc_names = [pc_d[o + 8:o + 8 + sz].split(b"\0")[0].decode() for o, sz in pc_s["c02"]]
    owner = np.repeat(np.arange(len(pc_recs)), [struct.unpack_from("<I", r, 0x3c)[0] for r in pc_recs])
    names = []
    for r, v, _ in draws:
        j = nearest(v[::max(1, len(v) // 200), :3], pc_v[:, :3])
        names.append(pc_names[np.bincount(owner[j], minlength=len(pc_names)).argmax()])
    # light materials (134013): the X360 index, by hash, into the PC's list (the same set, in another order on
    # the male cops)
    lo, lsz = pc_s[0x134013]
    xo3, xsz3 = x_s[0x134013]
    pc_light = struct.unpack_from(f"<{lsz // 4}I", pc_d, lo + 8)[0::2]
    x_light = struct.unpack_from(f"<{xsz3 // 4}I", x_d, xo3 + 8)[0::2]
    light = [pc_light.index(x_light[r[0x2a]]) for r, _, _ in draws]
    # rebuild 80134000: 80134001 as is, then 80134010 = 134011 (patched), 134012 (new), 134013, mesh
    so, ssz = pc_s["solid"]
    kids = walk(pc_d, so + 8, so + 8 + ssz)
    first = [(c, o, sz) for dep, c, o, sz in kids if dep == 0]
    out = bytearray()
    for c, o, sz in first:
        if c != 0x80134010:
            out += pc_d[o:o + 8 + sz]
            continue
        inner, p = bytearray(), pos + 8 + len(out) + 8
        for dep, c2, o2, sz2 in walk(pc_d, o + 8, o + 8 + sz):
            if dep != 0:
                continue
            if c2 == 0x134011:
                h = bytearray(pc_d[o2:o2 + 8 + sz2])
                ntri = sum(len(ix) // 3 for _, _, ix in draws)
                struct.pack_into("<I", h, 8 + 28, ntri)
                h[8 + 33] = len(textures)
                h[8 + 40:8 + 68] = x_d[xo + 8 + 40:xo + 8 + 68]
                h[8 + 160:8 + 168] = x_d[xo + 8 + 160:xo + 8 + 168]
                b = bytes(h)
            elif c2 == 0x134012:
                b = chunk(0x134012, b"".join(struct.pack("<II", alias(t), 0) for t in textures))
            elif c2 == 0x80134100:
                b = build_mesh(p, pc_d, pc_s, draws, names, texslot, light)
            else:
                b = pc_d[o2:o2 + 8 + sz2]
            inner += b
            p += len(b)
        out += chunk(0x80134010, bytes(inner))
    new = chunk(0x80134000, bytes(out))
    ntri = sum(len(ix) // 3 for _, _, ix in draws)
    pc_tri = struct.unpack_from("<I", pc_d, pc_s[0x134011][0] + 8 + 28)[0]
    report = (f"{pc_tri}->{ntri} triangles, {len(pc_v)}->{sum(len(v) for _, v, _ in draws)} vertices, "
              f"{len(draws)} draws, textures {' '.join('%08x' % t for t in textures)}, "
              f"{sum(s[1] != s[0] for s in texslot)} draws with a normal map, broken controls rejected")
    return new, report, textures


def convert_bundle(pc_d, x_d, log):
    ps, xs = solids(pc_d), solids(x_d)
    swaps = {}
    for name, s in ps.items():
        x = xs.get(name)
        if not x:
            continue
        pt = struct.unpack_from("<I", pc_d, s[0x134011][0] + 8 + 28)[0]
        xt = struct.unpack_from("<I", x_d, x[0x134011][0] + 8 + 28)[0]
        if xt > pt:
            coverage = pc_coverage(x360_mesh(x_d, x), pc_vertices(pc_d, s))
            if coverage[0] / coverage[1] > 0.05:
                log(f"  {name}: PC fallback; {coverage_report(coverage)}")
            else:
                swaps[s["solid"][0]] = (name, s, x, coverage)
    out, used = bytearray(), set()
    for depth, cid, off, size in walk(pc_d, 0, len(pc_d)):
        if depth:
            continue
        if off in swaps:
            name, s, x, coverage = swaps[off]
            new, report, textures = convert_solid(pc_d, s, x_d, x, len(out))
            used.update(textures)
            log(f"  {name}: {report}; {coverage_report(coverage)}")
            out += new
            # later chunks keep the PC's offset mod 0x1000: the loaders find their data by aligning the chunk
            # address (EAGL anim banks 00449130 and scene entities 0044e530: (chunk + 0x17) & ~0xf; solids by
            # 0x10/0x80), so the PC's own padding chunks after this one only line up if the next chunk starts at
            # the PC's offset mod 0x1000 (padding to 0x1000 itself left the anim banks 4 bytes early)
            pad = (off + 8 + size - len(out)) % 0x1000
            if pad:
                pad = pad if pad >= 8 else pad + 0x1000
                out += chunk(0, b"\0" * (pad - 8))
        else:
            assert len(out) % 0x1000 == off % 0x1000, f"chunk {cid:08x} at {len(out):x}, PC {off:x}: alignment lost"
            out += pc_d[off:off + 8 + size]
    return bytes(out), used, len(swaps)


def convert_file(args):
    """"""
    pc_path, xp, output = args
    pc_d, x_d = open(pc_path, "rb").read(), open(xp, "rb").read()
    lines = [os.path.basename(pc_path)]
    new, used, n = convert_bundle(pc_d, x_d, lines.append)
    pc_hashes = {h for _, h, *_ in tex.textures_in(tex.unpack_file(pc_d), 0x80, {})}
    needed = {}
    for name, h, w, ht, fmt, pix in tex.textures_in(tex.unpack_file(x_d)):
        if h in used and h not in needed:
            needed[h] = (name, "NIS/" + os.path.basename(xp), w, ht)
    assert not {alias(h) for h in used} & pc_hashes, "an alias collides with a PC texture"
    with open(output, "wb") as f:
        f.write(new)
    return lines, needed, used, n


def main():
    x360_dir, pc_dir, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    pc_nis = os.path.join(pc_dir, "NIS")
    x_nis = {f.upper(): os.path.join(x360_dir, "NIS", f) for f in os.listdir(os.path.join(x360_dir, "NIS"))}
    jobs = [(os.path.join(pc_nis, fn), x_nis[fn.upper()], os.path.join(out, fn))
            for fn in sorted(os.listdir(pc_nis))
            if re.match(r"Scene_Arrest[FM]\d+b?_BundleB\.bun$", fn, re.I)]
    needed, total = {}, 0
    for lines, textures, used, n in huff.ordered_map(convert_file, jobs):
        for line in lines:
            print(line)
        total += n
        # every map the converted solids use: registered by the runtime from the X360 bundle, under its alias
        for h, texture in textures.items():
            needed.setdefault(h, texture)
        assert not used - needed.keys(), f"no texture for {used - needed.keys()}"
    with open(os.path.join(out, "textures.tsv"), "w") as f:
        f.write("hash\talias\tname\tfile\twidth\theight\n")
        for h, (name, file, w, ht) in sorted(needed.items()):
            f.write(f"{h:08x}\t{alias(h):08x}\t{name}\t{file}\t{w}\t{ht}\n")
    print(f"{total} solids converted; {len(needed)} X360 textures in textures.tsv")


if __name__ == "__main__":
    main()
