#!/usr/bin/env python3
"""Port-authored tangent-space normal maps for the four car textures whose `_N` maps only the console ships (TREAD, TREADR,
DOORLINE, DAMAGE0), made from the player's PC files alone: GLOBAL/GLOBALB.BUN (the diffuses) and CARS/997S, CARS/ELISE
GEOMETRY.BIN (the UV-to-metre scale of each family on an ownable car). No console file is read.

Each recipe builds a height field h (mm) on the PC diffuse's UV layout and converts it with n = normalize(-dh/dx, -dh/dy, 1)
(x along +u, y along +v = image down: the runtime's derivative frame, green not flipped), x and y in mm from the measured
scale. Physical values and assumptions ([I]) sit beside each recipe.
  TREAD / TREADR: dark connected channels of the PC diffuse are the grooves (its painted highlights are not embossed);
    walls rounded by a 0.5 texel blur; depth 10/32 in = 7.9375 mm (a common new road tyre depth) [I: the depicted tyre's
    depth is unknown]. TREADR keeps its own layout (a TREAD map would not line up with its diffuse).
  DOORLINE: the diffuse alpha's stroke peaks (per row, along u, the axis across the seam) are seam centres; a 3.5 mm
    aperture (a published OEM nominal panel gap) with a 45 degree V of 1.75 mm depth [I: the gap gives width, not depth];
    transparent padding stays neutral.
  DAMAGE0: 20 um maximum abrasion depth (inside the 5-50 um clear coat range) [I]. Over the narrowest groove the atlas
    resolves (one texel each side) its slope is below one step of an 8-bit normal, so the recipe emits no map: damage keeps
    the PC shading (the console's dent detail stays open).
Writes a C table: int16 snorm (x, y) per texel (z = sqrt(1 - x^2 - y^2) at upload); `--stub`, or a PC folder without
these inputs, writes the empty table (Port then keeps PC shading for the four families).
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import x360_tex as xt  # noqa: E402
from texture_compare import jdlz, huff  # noqa: E402

FAMILIES = ("TREAD", "TREADR", "DOORLINE", "DAMAGE0")
SCALE_CAR = {"TREAD": "997S", "TREADR": "ELISE", "DOORLINE": "997S", "DAMAGE0": "997S"}   # ownable cars carrying each
TREAD_DEPTH_MM = 10 / 32 * 25.4          # 7.9375
DOOR_APERTURE_MM, DOOR_DEPTH_MM = 3.5, 1.75
SCUFF_DEPTH_MM = 0.020
NORMAL8_STEP = 1 / 127.5                  # one level of an 8-bit normal channel


class MissingInput(Exception):
    pass


def name_hash(name):                      # bStringHash
    h = 0xFFFFFFFF
    for ch in name.encode("ascii"):
        h = (h * 33 + ch) & 0xFFFFFFFF
    return h


def children(d, start, end):
    while start + 8 <= end:
        cid, size = struct.unpack_from("<II", d, start)
        if size > end - start - 8:
            raise ValueError("chunk %08x crosses its parent at %#x" % (cid, start))
        yield cid, start, size
        start += 8 + size


def walk(d, start=0, end=None):
    for cid, off, size in children(d, start, len(d) if end is None else end):
        yield cid, off, size
        if cid & 0x80000000:
            yield from walk(d, off + 8, off + 8 + size)


def payload(d, cid, align=1):
    o, z = next((o, z) for c, o, z in walk(d) if c == cid)
    return d[(o + 8 + align - 1) & -align:o + 8 + z]


def solids(d):
    """The solids of a PC car GEOMETRY.BIN: its 0x134004 index of (key, offset, stored, size, flags, 0)."""
    top = next((o, z) for c, o, z in children(d, 8, 8 + struct.unpack_from("<I", d, 4)[0]) if c == 0x80134001)
    o, z = next((o, z) for c, o, z in walk(d, top[0] + 8, top[0] + 8 + top[1]) if c == 0x134004)
    for _, off, stored, size, flags, _ in struct.iter_unpack("<6I", d[o + 8:o + 8 + z]):
        raw = d[off:off + stored]
        if flags == 0x100:
            raw = jdlz(raw) if raw[:4] == b"JDLZ" else huff(raw, 0)
        elif flags:
            raise ValueError("solid flags %#x" % flags)
        yield raw


def pc_diffuses(pc):
    try:
        d, pal, out = (pc / "GLOBAL/GLOBALB.BUN").read_bytes(), {}, {}
    except OSError as e:
        raise MissingInput(str(e))
    for name, h_, w, ht, fmt, pix in xt.textures_in(d, 0x80, pal):
        if name in FAMILIES and name not in out:
            if fmt == 41:
                rgba = np.frombuffer(pal[h_], np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]][
                    np.frombuffer(pix[:w * ht], np.uint8).reshape(ht, w)]
            else:
                rgba = xt.decode_preview(xt.PC_FMT[fmt], w, ht, pix)
            out[name] = rgba.astype(np.float64) / 255.0
    missing = [n for n in FAMILIES if n not in out]
    if missing:
        raise MissingInput("%s not in GLOBALB.BUN" % ", ".join(missing))
    return out


def uv_scale(pc, car, family):
    """Area-weighted median |dP/du|, |dP/dv| (m per UV unit) over the family's triangles in the car's KIT00 LOD A solids."""
    key = name_hash(family)
    du, dv, wt = [], [], []
    try:
        geometry = (pc / "CARS" / car / "GEOMETRY.BIN").read_bytes()
    except OSError as e:
        raise MissingInput(str(e))
    for s in solids(geometry):
        name = payload(s, 0x134011, 16)[160:].split(b"\0")[0].decode()
        if "_KIT00_" not in name or not name.endswith("_A"):
            continue
        tx = [x[0] for x in struct.iter_unpack("<II", payload(s, 0x134012))]
        rb = payload(s, 0x134b02, 16)
        recs = [rb[o:o + 104] for o in range(0, len(rb), 104)]
        if not any(tx[r[24]] == key for r in recs):
            continue
        mo, mz = next((o, z) for c, o, z in walk(s) if c == 0x80134100)
        vbs = [s[o + 8:o + 8 + z].lstrip(b"\x11") for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134b01]
        if len(vbs) != 1:
            continue
        nv = sum(struct.unpack_from("<I", r, 60)[0] for r in recs)
        v = np.frombuffer(vbs[0][:nv * 36], np.uint8).reshape(nv, 36)
        P, UV = v[:, :12].copy().view("<f4").astype(np.float64), v[:, 28:36].copy().view("<f4").astype(np.float64)
        ib = np.frombuffer(payload(s, 0x134b03, 16), "<u2")
        for r in recs:
            if tx[r[24]] != key:
                continue
            first, count = struct.unpack_from("<I", r, 68)[0], struct.unpack_from("<I", r, 92)[0]
            t = ib[first:first + count].reshape(-1, 3).astype(np.int64)
            e1, e2 = P[t[:, 1]] - P[t[:, 0]], P[t[:, 2]] - P[t[:, 0]]
            d1, d2 = UV[t[:, 1]] - UV[t[:, 0]], UV[t[:, 2]] - UV[t[:, 0]]
            det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
            ok = np.abs(det) > 1e-8
            det = np.where(ok, det, 1.0)[:, None]
            du.append(np.linalg.norm((e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det, axis=1)[ok])
            dv.append(np.linalg.norm((e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / det, axis=1)[ok])
            wt.append(0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)[ok])
    if not du:
        raise MissingInput("no %s triangles on %s" % (family, car))
    du, dv, wt = np.concatenate(du), np.concatenate(dv), np.concatenate(wt)

    def median(a):
        o = np.argsort(a)
        c = np.cumsum(wt[o])
        return float(a[o][np.searchsorted(c, c[-1] / 2)])
    return median(du), median(dv)


def blur(a, sigma, axis, wrap=True):
    r = max(1, int(3 * sigma + 0.5))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    if wrap:
        return sum(w * np.roll(a, i, axis) for i, w in zip(range(-r, r + 1), k))
    p = [(0, 0)] * a.ndim
    p[axis] = (r, r)
    e = np.pad(a, p, mode="edge")
    return sum(w * np.take(e, range(r + i, r + i + a.shape[axis]), axis) for i, w in zip(range(-r, r + 1), k))


def upscale(a, s):
    return np.kron(a, np.ones((s, s)))


def normals(h, dx, dy, wrap=True):
    """(nx, ny) of n = normalize(-dh/dx, -dh/dy, 1), central differences (wrapped or clamped)."""
    if wrap:
        gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / (2 * dx)
        gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / (2 * dy)
    else:
        gy, gx = np.gradient(h, dy, dx)
    inv = 1 / np.sqrt(gx * gx + gy * gy + 1)
    return -gx * inv, -gy * inv


def components(mask, minimum):
    """mask with connected (4-neighbour, wrapped) regions smaller than `minimum` texels removed: specks of rubber grain."""
    hgt, wid = mask.shape
    label = np.zeros(mask.shape, np.int32)
    keep = np.zeros(mask.shape, bool)
    n = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if label[y0, x0]:
            continue
        n += 1
        label[y0, x0] = n
        stack, cells = [(y0, x0)], []
        while stack:
            y, x = stack.pop()
            cells.append((y, x))
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                yy, xx = yy % hgt, xx % wid
                if mask[yy, xx] and not label[yy, xx]:
                    label[yy, xx] = n
                    stack.append((yy, xx))
        if len(cells) >= minimum:
            for c in cells:
                keep[c] = True
    return keep


def tread(rgba, scale, up=2):
    """Grooves: texels darker than their 4-texel neighbourhood by more than the Otsu split of that local contrast, in
    connected channels of >= 16 texels (the tyre's mottled grain and its painted highlights are not grooves)."""
    lum = rgba[..., :3].mean(2)
    lum = blur(blur(lum, 0.6, 0), 0.6, 1)
    local = lum - blur(blur(lum, 4.0, 0), 4.0, 1)
    hist, edges = np.histogram(local, 64)
    mids, p = (edges[:-1] + edges[1:]) / 2, hist / hist.sum()
    best, split = -1, mids[0]
    for i in range(1, 64):
        w0, w1 = p[:i].sum(), p[i:].sum()
        if w0 and w1:
            between = w0 * w1 * ((p[:i] * mids[:i]).sum() / w0 - (p[i:] * mids[i:]).sum() / w1) ** 2
            if between > best:
                best, split = between, mids[i]
    split = min(split, -local.std())
    groove = components(local < split, 16).astype(np.float64)
    m = upscale(groove, up)
    m = blur(blur(m, 0.5 * up, 0), 0.5 * up, 1)          # rounded walls [I]: half a PC texel
    h = -TREAD_DEPTH_MM * m
    ht, wd = rgba.shape[:2]
    return normals(h, scale[0] * 1000 / (wd * up), scale[1] * 1000 / (ht * up)), groove


def doorline(rgba, scale, up=4):
    """Seam centres: per row, local maxima of the alpha stroke along u (>= half the row's peak); a V of half-width 1.75 mm."""
    a = rgba[..., 3]
    ht, wd = a.shape
    dx = scale[0] * 1000 / (wd * up)
    au = upscale(a, up)
    h = np.zeros(au.shape)
    half = DOOR_APERTURE_MM / 2
    for region in range(0, wd, 16):                       # four 16-texel treatments, each kept separately
        for y in range(ht):
            row = a[y, region:region + 16]
            if row.max() <= 0:
                continue
            pk = [x for x in range(16) if row[x] >= 0.5 * row.max() and row[x] >= 0.25 and
                  (x == 0 or row[x] >= row[x - 1]) and (x == 15 or row[x] > row[x + 1])]
            xs = (np.arange(16 * up) + 0.5) / up           # texel units within the region
            prof = np.zeros(16 * up)
            for x in pk:
                prof = np.maximum(prof, np.clip(1 - np.abs(xs - (x + 0.5)) * (dx * up) / half, 0, 1))
            h[y * up:(y + 1) * up, region * up:(region + 16) * up] = -DOOR_DEPTH_MM * prof
    h = blur(h, 0.25 / dx, 1, wrap=False)                 # 0.25 mm edge rounding [I]
    nx, ny = normals(h, dx, scale[1] * 1000 / (ht * up), wrap=False)
    pad = au <= 0                                         # transparent padding: neutral
    nx[pad], ny[pad] = 0, 0
    return (nx, ny), None


def damage0_slope(rgba, scale):
    """Steepest slope of a 20 um scuff the atlas can resolve: depth over one texel (the narrower axis's texel)."""
    ht, wd = rgba.shape[:2]
    texel = min(scale[0] * 1000 / wd, scale[1] * 1000 / ht)
    return SCUFF_DEPTH_MM / texel


RECIPES = {"TREAD": tread, "TREADR": tread, "DOORLINE": doorline, "DAMAGE0": damage0_slope}


def build(pc):
    diff = pc_diffuses(pc)
    maps, info = {}, {}
    for fam in FAMILIES:
        scale = uv_scale(pc, SCALE_CAR[fam], fam)
        info[fam] = scale
        if fam == "DAMAGE0":
            if fam in RECIPES:
                info["slope"] = RECIPES[fam](diff[fam], scale)
        elif fam in RECIPES:
            (nx, ny), _ = RECIPES[fam](diff[fam], scale)
            maps[fam] = (nx, ny)
    return maps, info


def table(maps, info):
    lines = ["// Generated by tools_snapshot/car_normals_port.py from the player's PC files (no console input). Do not edit.",
             "#define CAR_NORMALS_PORT 1"]
    for fam in FAMILIES:
        su, sv = info[fam]
        lines.append("// %s: %.4f m per u, %.4f m per v (%s KIT00 LOD A)" % (fam, su, sv, SCALE_CAR[fam]))
    slope = info.get("slope", 0.0)
    lines.append("#define CAR_NORMALS_PORT_DAMAGE0_SLOPE %.6ff   // 20 um scuff over one texel; 8-bit step %.6f: %s" %
                 (slope, NORMAL8_STEP, "neutral (no map)" if slope < NORMAL8_STEP else "mapped"))
    for fam in FAMILIES:
        if fam not in maps:
            continue
        nx, ny = maps[fam]
        q = np.stack([np.rint(np.clip(nx, -1, 1) * 32767), np.rint(np.clip(ny, -1, 1) * 32767)], 2).astype(np.int16)
        vals = q.reshape(-1)
        body = ",\n".join(",".join(str(int(v)) for v in vals[i:i + 32]) for i in range(0, len(vals), 32))
        lines.append("static const int16_t car_normals_port_%s[%d] = {\n%s};" % (fam.lower(), len(vals), body))
    rows = []
    for fam in FAMILIES:
        if fam in maps:
            hgt, wid = maps[fam][0].shape
            rows.append('    {"%s", %d, %d, car_normals_port_%s},' % (fam, wid, hgt, fam.lower()))
        else:
            rows.append('    {"%s", 0, 0, 0},' % fam)
    lines.append("static const CarNormalsPortMap car_normals_port_maps[4] = {\n%s\n};" % "\n".join(rows))
    return "\n".join(lines) + "\n"


STUB = ("// Generated by tools/gen_tables.py with no PC game folder or car inputs: no port car normal maps. Do not edit.\n"
        "#define CAR_NORMALS_PORT 0\n#define CAR_NORMALS_PORT_DAMAGE0_SLOPE 0.0f\n"
        "static const CarNormalsPortMap car_normals_port_maps[4] = "
        "{{\"TREAD\", 0, 0, 0}, {\"TREADR\", 0, 0, 0}, {\"DOORLINE\", 0, 0, 0}, {\"DAMAGE0\", 0, 0, 0}};\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pc", type=Path)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.stub:
        a.out.write_text(STUB)
        return
    if not a.pc:
        ap.error("--pc or --stub")
    try:
        a.out.write_text(table(*build(a.pc)))
    except MissingInput as e:
        print("car_normals_port: %s: empty table" % e)
        a.out.write_text(STUB)


if __name__ == "__main__":
    main()
