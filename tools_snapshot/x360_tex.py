"""Convert X360 NFS:MW textures (TPK packs) into PC-ready DDS files.

Reads the X360 files extracted by zz_extract.py (x360/files), finds every TPK texture, and for each:
  - takes its pixel data (uncompressed TPK: data chunk + TextureInfo offset; compressed TPK: the JDLZ/HUFF blob,
    whose tail is TextureInfo + platform record),
  - decodes the Xenon D3DFORMAT (XDK d3d9types.h: format = v & 0x3F, endian = (v >> 6) & 3, tiled = bit 8),
  - untiles tiled surfaces (XGAddress2DTiledOffset) and undoes the GPU endian swap,
  - writes the top mip level as DDS (DXT1/DXT3/DXT5 or A8R8G8B8), optionally with a PNG preview.
Mip levels below the top one aren't written (X360 packs small mips into a tail); a loader regenerates them, or
  --mips writes a full chain: level 0 is the X360 data as is, the smaller levels are box-filtered from it and
  re-encoded (DXT1/3/5 with a bounding-box encoder, A8R8G8B8 as is).

usage: x360_tex.py <x360 files dir> <out dir> [--only NAME_SUBSTR ...] [--png] [--larger texture_compare.tsv] [--vinyls]
                  [--mips] [--pc-alpha <PC game dir>] [--tone-match <PC game dir>] [--same-size-better texture_compare.tsv]
                  [--frontend-iso <X360 ISO>] [--extend-set <existing HD set>]
                  [--frontend-files]  (Setup's already extracted six scenes)
  --vinyls: include CARS/*/VINYLS.BIN (10,874 textures, ~44 GB as 32-bit DDS; skipped by default).
  --larger: convert only textures that are bigger on X360 than on PC (from texture_compare.py's output).
  --pc-alpha: where the PC texture keeps a different mask in alpha (specular, blend, cut-out), X360 colour + the PC
    alpha upscaled to the X360 size, as DXT5 (the X360 colour blocks kept as they are).
  --tone-match: re-grade X360 textures whose tones differ from the PC texture (same content) to the PC's tones; the
    ungraded version goes to <out>/x360_tone/ (NFS_X360_TEXTURES_TONE=x360). Applied after --pc-alpha.
  --same-size-better: convert only the UI textures (GLOBAL/FRONTEND/LANGUAGES) that have the PC's size on X360 but a
    better format: 32-bit over PC DXT/P8, DXT5 over PC DXT3. The runtime still checks each against the PC texture.
    Reads FRONTB.LZC and the other file-level JDLZ packs too.
Needs numpy, pillow for --png; uses bounded native HUFF/JDLZ decoders.
  --frontend-iso: also read the six FRONTEND/PLATFORMS LZCs directly from the disc (no extract writes).
    Requires --larger and --tone-match/--pc-alpha; extends the older comparison TSV with the scenes' actual sizes.
  --extend-set: copy an existing generated HD set into a NEW output folder, then append only the frontend ISO's
    missing hashes. Existing DDS/tone variants stay byte-identical; the <x360 files> positional input is unused.
"""
import os
import re
import shutil
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from texture_compare import huff, jdlz, unblock  # noqa: E402

FRONTEND_SCENES = {"CAREER_SAFEHOUSE": 57, "PLATFORMCRIB": 54, "CAR_LOT": 34,
                   "CUSTOMIZATION_SHOP": 24, "CUSTOMIZATION_SHOP_BACKROOM": 24, "NEXTGENSKY": 0}


def unpack_file(d):
    # File-level JDLZ is also used by HD platform scenes, not just same-size UI packs.
    # PLATFORMCRIB expands to 34,034,720 bytes, beyond the embedded-block decoder's default limit.
    if d[:4] == b"JDLZ":
        d = jdlz(d, max_size=128 << 20)
    return unblock(d) if d[:4] == b"\x22\x11\x44\x55" else d


def frontend_sources(iso):
    """Read exactly six named ZDIR entries with the vendored bounded disc reader."""
    from setup_kit import xdvdfs
    def path_hash(path):
        h = 0xFFFFFFFF
        for c in path.upper().replace("/", "\\").encode("ascii"):
            h = (h * 33 + c) & 0xFFFFFFFF
        return h
    with open(iso, "rb") as f:
        base = xdvdfs.find_base(f)
        f.seek(base + 32 * xdvdfs.SECTOR + 20)
        sector, size = struct.unpack("<II", f.read(8))
        files = {p.upper(): (off, n) for p, off, n in xdvdfs.walk(f, base, sector, size)}
        off, n = files["NFS/ZDIR.BIN"]
        f.seek(off)
        zdir = f.read(n)
        entries = {struct.unpack_from("<I", zdir, p)[0]: struct.unpack_from("<6I", zdir, p)
                   for p in range(0, n, 24)}
        for scene in FRONTEND_SCENES:
            path = f"FRONTEND/PLATFORMS/{scene}.LZC"
            _, archive, sector, _, n, _ = entries[path_hash(path)]
            off, size = files[f"NFS/ZZDATA{archive}.BIN"]
            if sector * 2048 + n > size:
                raise ValueError(f"{path}: archive bounds")
            f.seek(off + sector * 2048)
            raw = f.read(n)
            if len(raw) != n or raw[:4] != b"JDLZ":
                raise ValueError(f"{path}: missing JDLZ scene")
            yield path, raw


def frontend_inventory(sources, pc_dir):
    """Per-scene enlarged textures, independent of the old global comparison TSV. Missing scenes fail closed."""
    sources = list(sources)
    present = {os.path.basename(path).rsplit(".", 1)[0].upper() for path, _ in sources}
    if present != set(FRONTEND_SCENES):
        raise ValueError(f"missing frontend scenes: {sorted(set(FRONTEND_SCENES) - present)}")
    pc_paths = {fn.upper(): os.path.join(pc_dir, "FRONTEND", "PLATFORMS", fn)
                for fn in os.listdir(os.path.join(pc_dir, "FRONTEND", "PLATFORMS"))}
    result = {}
    for path, raw in sources:
        scene = os.path.basename(path).rsplit(".", 1)[0].upper()
        with open(pc_paths[scene + ".BIN"], "rb") as f:
            pc = {t[1]: t for t in textures_in(f.read(), 0x80)}
        xbox = {t[1]: t for t in textures_in(unpack_file(raw))}
        larger = {h: (pc[h], t) for h, t in xbox.items() if h in pc and t[2] * t[3] > pc[h][2] * pc[h][3]}
        if len(larger) != FRONTEND_SCENES[scene]:
            raise ValueError(f"{scene}: expected {FRONTEND_SCENES[scene]} enlarged textures, got {len(larger)}")
        result[scene] = larger
    return result

GPU_FMT = {2: ("L8", 1, 1), 6: ("A8R8G8B8", 1, 4), 18: ("DXT1", 4, 8), 19: ("DXT3", 4, 16), 20: ("DXT5", 4, 16),
           4: ("R5G6B5", 1, 2), 3: ("A1R5G5B5", 1, 2), 15: ("A4R4G4B4", 1, 2)}
ENDIAN_NONE, ENDIAN_8IN16, ENDIAN_8IN32, ENDIAN_16IN32 = 0, 1, 2, 3


def xenon_format(v):
    return v & 0x3F, (v >> 6) & 3, (v >> 8) & 1


def tiled_offset(x, y, width, bpb_log2):
    """XGAddress2DTiledOffset: offset (in blocks) of block (x, y) in a tiled surface `width` blocks wide.
    bpb_log2 = log2(bytes per block)."""
    aligned = (width + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (bpb_log2 + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << bpb_log2
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + bpb_log2)) + ((y & 1) << 4)
    return (((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63) + ((y & 16) << 7) +
            (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> bpb_log2


_untile_cache = {}


def untile(data, wb, hb, bpb):
    """Rearrange a tiled surface of wb x hb blocks (bpb bytes each) into linear order."""
    log2 = bpb.bit_length() - 1
    key = (wb, hb, log2)
    idx = _untile_cache.get(key)
    if idx is None:
        ys, xs = np.mgrid[0:hb, 0:wb]
        xs, ys = xs.ravel().astype(np.int64), ys.ravel().astype(np.int64)
        aligned = (wb + 31) & ~31
        macro = ((xs >> 5) + (ys >> 5) * (aligned >> 5)) << (log2 + 7)
        micro = ((xs & 7) + ((ys & 6) << 2)) << log2
        off = macro + ((micro & ~15) << 1) + (micro & 15) + ((ys & 8) << (3 + log2)) + ((ys & 1) << 4)
        idx = (((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((ys & 16) << 7) +
               (((((ys & 8) >> 2) + (xs >> 3)) & 3) << 6)) >> log2
        _untile_cache[key] = idx
    blocks = np.frombuffer(data, np.uint8)
    need = (int(idx.max()) + 1) * bpb
    if len(blocks) < need:
        blocks = np.concatenate([blocks, np.zeros(need - len(blocks), np.uint8)])
    blocks = blocks[:need].reshape(-1, bpb)
    return blocks[idx].tobytes()


def unswap(data, endian):
    a = np.frombuffer(data, np.uint8)
    n = len(a)
    if endian == ENDIAN_8IN16:
        a = a[:n - n % 2].reshape(-1, 2)[:, ::-1]
    elif endian == ENDIAN_8IN32:
        a = a[:n - n % 4].reshape(-1, 4)[:, ::-1]
    elif endian == ENDIAN_16IN32:
        a = a[:n - n % 4].reshape(-1, 2, 2)[:, ::-1, :]
    return a.tobytes()


def encode_dxt(name, img):
    """RGBA uint8 (h, w, 4) -> DXT1/DXT3/DXT5 blocks. Endpoints are the block's colour bounding box, inset by 1/16
    like the usual range-fit encoders; indices pick the nearest palette entry."""
    h, w = img.shape[:2]
    bh, bw = (h + 3) // 4, (w + 3) // 4
    pad = np.zeros((bh * 4, bw * 4, 4), np.uint8)
    pad[:h, :w] = img
    if h < bh * 4: pad[h:, :w] = img[-1:, :, :]
    if w < bw * 4: pad[:, w:] = pad[:, w - 1:w]
    blk = pad.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, 4).astype(np.int32)
    rgb, a = blk[:, :, :3], blk[:, :, 3]
    lo, hi = rgb.min(1), rgb.max(1)
    inset = (hi - lo) // 16
    lo, hi = np.clip(lo + inset, 0, 255), np.clip(hi - inset, 0, 255)
    def to565(c): return ((c[:, 0] * 31 + 127) // 255) << 11 | ((c[:, 1] * 63 + 127) // 255) << 5 | ((c[:, 2] * 31 + 127) // 255)
    def from565(v): return np.stack([((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31], 1)
    c0, c1 = to565(hi), to565(lo)
    hi_first = np.maximum(c0, c1), np.minimum(c0, c1)
    cut = (a < 128) if name == "DXT1" else np.zeros_like(a, bool)
    three = cut.any(1)                                 # DXT1 blocks with transparent pixels: three-colour mode (c0 <= c1)
    c0 = np.where(three, hi_first[1], hi_first[0])
    c1 = np.where(three, hi_first[0], hi_first[1])
    p0, p1 = from565(c0), from565(c1)
    four = c0 > c1                                     # equal endpoints: three-colour mode, index 0 still exact
    pal = np.stack([p0, p1, np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2),
                    np.where(four[:, None], (p0 + 2 * p1) // 3, 0)], 1)                    # (n, 4, 3)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3)                          # (n, 16, 4)
    if name == "DXT1":
        d[:, :, 3] = np.where((~four)[:, None], 1 << 30, d[:, :, 3])  # index 3 is transparent black in three-colour mode
    sel = d.argmin(2).astype(np.uint32)
    sel = np.where(cut, 3, sel)
    codes = np.zeros(len(blk), np.uint32)
    for i in range(16): codes |= sel[:, i] << np.uint32(2 * i)
    colour = np.stack([c0 & 255, c0 >> 8, c1 & 255, c1 >> 8, codes & 255, (codes >> 8) & 255, (codes >> 16) & 255, codes >> 24], 1).astype(np.uint8)
    if name == "DXT1":
        return colour.tobytes()
    if name == "DXT3":
        q = (a + 8) // 17
        ab = np.stack([(q[:, 2 * i] | (q[:, 2 * i + 1] << 4)) for i in range(8)], 1).astype(np.uint8)
        return np.concatenate([ab, colour], 1).tobytes()
    a0, a1 = a.max(1), a.min(1)                        # DXT5: a0 > a1 -> 8-entry interpolated palette
    same = a0 == a1
    a1 = np.where(same & (a0 > 0), a0 - 1, a1)
    a0 = np.where(same & (a0 == 0), 1, a0)
    tab = np.stack([a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)], 1)
    asel = np.abs(a[:, :, None] - tab[:, None, :]).argmin(2).astype(np.uint64)
    acode = np.zeros(len(blk), np.uint64)
    for i in range(16): acode |= asel[:, i] << np.uint64(3 * i)
    ab = np.stack([a0, a1] + [((acode >> np.uint64(8 * i)) & np.uint64(255)).astype(np.int32) for i in range(6)], 1).astype(np.uint8)
    return np.concatenate([ab, colour], 1).tobytes()


def mip_chain(name, w, h, data):
    """Full mip chain after the top level: box-filtered from the decoded top level, re-encoded."""
    if name not in ("DXT1", "DXT3", "DXT5", "A8R8G8B8"):
        return [], 1
    img = decode_preview(name, w, h, data).astype(np.float32)
    out, levels = [], 1
    while w > 1 or h > 1:
        nw, nh = max(1, w // 2), max(1, h // 2)
        img = img[:nh * (h // nh), :nw * (w // nw)].reshape(nh, h // nh, nw, w // nw, 4).mean((1, 3))
        w, h = nw, nh
        px = np.clip(img + 0.5, 0, 255).astype(np.uint8)
        out.append(px[:, :, [2, 1, 0, 3]].tobytes() if name == "A8R8G8B8" else encode_dxt(name, px))
        levels += 1
    return out, levels


def dds(name, w, h, data, mips=None):
    """DDS with one mip level, or with `mips` (the levels after the top one). name: DXT1/DXT3/DXT5/A8R8G8B8/L8."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000
    if name.startswith("DXT"):
        pitch = max(1, (w + 3) // 4) * (8 if name == "DXT1" else 16) * max(1, (h + 3) // 4)
        pf = struct.pack("<II4sIIIII", 32, 0x4, name.encode(), 0, 0, 0, 0, 0)
        flags |= 0x80000
    elif name == "L8":
        pitch = w
        pf = struct.pack("<II4sIIIII", 32, 0x20000, b"\0\0\0\0", 8, 0xFF, 0, 0, 0)
        flags |= 0x8
    else:
        pitch = w * 4
        pf = struct.pack("<II4sIIIII", 32, 0x41, b"\0\0\0\0", 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
        flags |= 0x8
    count, caps = 1, 0x1000
    if mips:
        count, flags, caps = 1 + len(mips), flags | 0x20000, 0x401008      # MIPMAPCOUNT; COMPLEX | MIPMAP | TEXTURE
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, pitch, 0, count) + b"\0" * 44 + pf
    head += struct.pack("<IIIII", caps, 0, 0, 0, 0)
    return head + data + b"".join(mips or [])


def decode_preview(name, w, h, data):
    """RGBA uint8 array for a PNG preview (DXT1/3/5 and A8R8G8B8/L8)."""
    if name == "A8R8G8B8":
        a = np.frombuffer(data[:w * h * 4], np.uint8).reshape(h, w, 4)
        return a[:, :, [2, 1, 0, 3]]
    if name == "L8":
        a = np.frombuffer(data[:w * h], np.uint8).reshape(h, w)
        return np.dstack([a, a, a, np.full_like(a, 255)])
    bw, bh = (w + 3) // 4, (h + 3) // 4
    bpb = 8 if name == "DXT1" else 16
    blk = np.frombuffer(data[:bw * bh * bpb], np.uint8).reshape(bh * bw, bpb)
    col = blk[:, bpb - 8:]
    c0 = col[:, 0].astype(np.uint32) | (col[:, 1].astype(np.uint32) << 8)
    c1 = col[:, 2].astype(np.uint32) | (col[:, 3].astype(np.uint32) << 8)

    def rgb(c):
        return np.stack([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], 1)
    p0, p1 = rgb(c0).astype(np.int32), rgb(c1).astype(np.int32)
    four = (c0 > c1) | (name != "DXT1")
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)                                  # (n, 4, 3)
    bits = col[:, 4:8].astype(np.uint32)
    codes = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    sel = np.stack([(codes >> (2 * i)) & 3 for i in range(16)], 1)       # (n, 16)
    rgbpx = np.take_along_axis(pal, sel[:, :, None].repeat(3, 2), 1)     # (n, 16, 3)
    alpha = np.full((len(blk), 16), 255, np.int32)
    if name == "DXT1":
        alpha[(~four)[:, None].repeat(16, 1) & (sel == 3)] = 0
    elif name == "DXT3":
        ab = blk[:, :8]
        alpha = np.stack([((ab[:, i // 2] >> (4 * (i % 2))) & 15) * 17 for i in range(16)], 1).astype(np.int32)
    else:
        a0, a1 = blk[:, 0].astype(np.int32), blk[:, 1].astype(np.int32)
        ab = blk[:, 2:8].astype(np.uint64)
        acode = sum(ab[:, i] << np.uint64(8 * i) for i in range(6))
        asel = np.stack([((acode >> np.uint64(3 * i)) & np.uint64(7)).astype(np.int32) for i in range(16)], 1)
        # 8-entry alpha palette: a0 > a1 -> 6 interpolated values; else 4 interpolated + 0 + 255
        interp7 = [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        interp5 = [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [np.zeros_like(a0), np.full_like(a0, 255)]
        tab = np.where((a0 > a1)[:, None], np.stack([a0, a1] + interp7, 1), np.stack([a0, a1] + interp5, 1))
        alpha = np.take_along_axis(tab, asel, 1)
    px = np.concatenate([rgbpx, alpha[:, :, None]], 2).astype(np.uint8)   # (n, 16, 4)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def top_level(fmt_value, w, h, data):
    """Return (dds format name, linear top-level bytes) or None when the format isn't handled."""
    gfmt, endian, tiled = xenon_format(fmt_value)
    if gfmt not in GPU_FMT:
        return None
    name, bdim, bpb = GPU_FMT[gfmt]
    wb, hb = max(1, (w + bdim - 1) // bdim), max(1, (h + bdim - 1) // bdim)
    if tiled:
        # tiled surfaces are stored with width/height padded to 32 blocks
        awb, ahb = (wb + 31) & ~31, (hb + 31) & ~31
        lin = untile(data, awb, ahb, bpb)
        rows = np.frombuffer(lin, np.uint8).reshape(ahb, awb * bpb)[:hb, :wb * bpb]
        lin = rows.tobytes()
    else:
        pitch = max(wb * bpb, 256) if wb * bpb < 256 else (wb * bpb + 255) & ~255
        need = pitch * hb
        if len(data) < need:
            data = data + b"\0" * (need - len(data))
        rows = np.frombuffer(data[:need], np.uint8).reshape(hb, pitch)[:, :wb * bpb]
        lin = rows.tobytes()
    lin = unswap(lin, endian)
    if name in ("R5G6B5", "A1R5G5B5", "A4R4G4B4"):
        return None   # rare; not needed for the HD set
    return name, lin


def pixel_base(d, data, root, align=0x1000):
    """Start of the pixel area in a TPK data chunk: after the 0x11 padding, which ends aligned relative to the pack's
    root chunk: 0x1000 on X360 (textures are 4 KB aligned once the pack is loaded; the file itself isn't, so an
    absolute alignment is wrong for most packs), 0x80 on PC. The first pixel byte can itself be 0x11: round down."""
    start, end = data[0], data[0] + data[1]
    q = start
    while q < end and d[q] == 0x11:
        q += 1
    return max(start, root + ((q - root) & ~(align - 1)))


def textures_in(d, align=0x1000, palettes=None):
    """Yield (name, hash, w, h, platform format, pixel bytes) for every texture in a (de-blocked) file. X360 by
    default (Xenon D3DFORMAT); align=0x80 reads PC files (FourCC / D3DFORMAT, linear data, mips after the top)."""
    tag4 = struct.pack("<I", 0x33310004)
    for root in re.finditer(re.escape(struct.pack("<I", 0xB3300000)), d):
        p = root.start()
        rsz, = struct.unpack_from("<I", d, p + 4)
        end = p + 8 + rsz
        if end > len(d):
            continue
        q, infos, plats, table, data = p + 8, None, None, None, None
        while q + 8 <= end:
            cid, sz = struct.unpack_from("<II", d, q)
            if cid == 0xB3310000 or cid == 0xB3320000:
                r = q + 8
                while r + 8 <= q + 8 + sz:
                    c2, s2 = struct.unpack_from("<II", d, r)
                    if c2 == 0x33310004: infos = d[r + 8:r + 8 + s2]
                    elif c2 == 0x33310005: plats = d[r + 8:r + 8 + s2]
                    elif c2 == 0x33310003: table = d[r + 8:r + 8 + s2]
                    elif c2 in (0x33320002, 0x33320003): data = (r + 8, s2)
                    r += 8 + s2
            q += 8 + sz
        if infos and plats and data and len(infos) % 124 == 0 and len(plats) == len(infos) // 124 * 32:
            for k in range(len(infos) // 124):
                e, pl = infos[k * 124:(k + 1) * 124], plats[k * 32:(k + 1) * 32]
                h_, = struct.unpack_from("<I", e, 0x24)
                off, size = struct.unpack_from("<II", e, 0x30)[0], struct.unpack_from("<I", e, 0x38)[0]
                w, ht = struct.unpack_from("<HH", e, 0x44)
                fmt, = struct.unpack_from("<I", pl, 0x14)
                name = e[12:36].split(b"\0")[0].decode("latin-1")
                base = pixel_base(d, data, p, align)
                if palettes is not None and fmt == 41:
                    poff, = struct.unpack_from("<I", e, 0x34)
                    psize, = struct.unpack_from("<I", e, 0x3C)
                    if psize == 1024:
                        palettes[h_] = d[base + poff:base + poff + psize]
                yield name, h_, w, ht, fmt, d[base + off:base + off + size]
        if table and data:
            # Compressed pack: the table's offsets describe the unpacked layout, not the file, so walk the
            # blobs in the data chunk instead; each blob's tail is its own TextureInfo + platform record.
            base, stop = pixel_base(d, data, p, align), data[0] + data[1]
            consumed = base
            for m in re.compile(b"JDLZ\x02\x10|HUFF\x01\x10").finditer(d, base, stop):
                at = m.start()
                if at < consumed:
                    continue
                b = jdlz(d, at) if d[at] == ord("J") else huff(d, at)
                consumed = at + struct.unpack_from("<I", d, at + 12)[0] + (16 if d[at] == ord("H") else 0)
                if len(b) < 156:
                    continue
                e, pl = b[-156:-32], b[-32:]
                h_, = struct.unpack_from("<I", e, 0x24)
                w, ht = struct.unpack_from("<HH", e, 0x44)
                fmt, = struct.unpack_from("<I", pl, 0x14)
                name = e[12:36].split(b"\0")[0].decode("latin-1")
                yield name, h_, w, ht, fmt, b[:-156]


# ---- tone match against the PC texture (--tone-match <pc dir>) ----
# Many X360 world textures are the PC art at 2x, regraded (pavement ~20% brighter, foliage ~30% darker: the X360
# lighting compensates). Matched per channel to the PC texture's tones, they keep the PC look with the X360 detail.
PC_FMT = {0x31545844: "DXT1", 0x33545844: "DXT3", 0x35545844: "DXT5", 21: "A8R8G8B8"}


def pc_textures(pc_dir, wanted):
    """hash -> (w, h, RGBA top level) for the wanted {hash: (w, h)}, from the PC game's packs: the copy at the size the
    game uses (texture_compare.tsv), which is what the runtime validates against."""
    out = {}
    for dp, _, fs in os.walk(pc_dir):
        for fn in sorted(fs):
            if not fn.upper().endswith((".BIN", ".BUN", ".LZC")) or fn.upper() == "VINYLS.BIN":
                continue
            d = open(os.path.join(dp, fn), "rb").read()
            palettes = {}
            for name, h_, w, ht, fmt, pix in textures_in(d, 0x80, palettes):
                kind = PC_FMT.get(fmt)
                if wanted.get(h_) == (w, ht) and h_ not in out and (kind or fmt == 41) and w >= 4 and ht >= 4:
                    try:
                        if fmt == 41:
                            palette = np.frombuffer(palettes[h_], np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
                            rgba = palette[np.frombuffer(pix[:w * ht], np.uint8).reshape(ht, w)]
                        else:
                            rgba = decode_preview(kind, w, ht, pix)
                        out[h_] = (w, ht, rgba)
                    except (ValueError, KeyError):
                        pass                                   # short data (a pack we don't read right): skip
    return out


def downsample(img, w, h):
    """Box filter an RGBA image to w x h (integer factors per side)."""
    fy, fx = img.shape[0] // h, img.shape[1] // w
    return img[:h * fy, :w * fx].reshape(h, fy, w, fx, 4).astype(np.float32).mean((1, 3))


def upscale(a, w, h):
    """Upscale a single channel (h0, w0) to (h, w) (integer factors): bilinear (pixel centres aligned, edges clamped),
    plus each source pixel's residual spread over its block, so a box filter back to (h0, w0) gives the source again.
    Plain bilinear blurs fine masks (specular noise) by ~8 of 255 at the PC size; this keeps them as they were while
    the edges stay smooth."""
    def axis(n0, n):
        t = np.clip((np.arange(n) + 0.5) * n0 / n - 0.5, 0, n0 - 1)
        i0 = np.floor(t).astype(np.int64)
        return i0, np.minimum(i0 + 1, n0 - 1), (t - i0).astype(np.float32)
    h0, w0 = a.shape
    y0, y1, fy = axis(h0, h)
    x0, x1, fx = axis(w0, w)
    a = a.astype(np.float32)
    rows = a[y0] * (1 - fy)[:, None] + a[y1] * fy[:, None]
    up = rows[:, x0] * (1 - fx) + rows[:, x1] * fx
    residual = a - up.reshape(h0, h // h0, w0, w // w0).mean((1, 3))
    up += np.repeat(np.repeat(residual, h // h0, 0), w // w0, 1)
    return np.clip(up + 0.5, 0, 255).astype(np.uint8)


def with_alpha(kind, w, h, lin, alpha):
    """(kind, top level) with its alpha replaced by `alpha` (h, w): A8R8G8B8 stays as is, DXT1/3/5 become DXT5. The
    X360 colour blocks are kept bit for bit (BC3 colour always decodes in four-colour mode, the same as DXT1 with
    c0 > c1); only DXT1 three-colour blocks (c0 <= c1) are re-encoded from their decoded colours."""
    if kind == "A8R8G8B8":
        px = np.frombuffer(lin, np.uint8).reshape(h, w, 4).copy()
        px[:, :, 3] = alpha
        return kind, px.tobytes()
    img = decode_preview(kind, w, h, lin).copy()
    img[:, :, 3] = alpha
    blocks = np.frombuffer(encode_dxt("DXT5", img), np.uint8).reshape(-1, 16).copy()
    bpb = 8 if kind == "DXT1" else 16
    colour = np.frombuffer(lin, np.uint8).reshape(-1, bpb)[:, bpb - 8:]
    keep = np.ones(len(colour), bool)
    if kind == "DXT1":
        c0 = colour[:, 0].astype(np.uint32) | colour[:, 1].astype(np.uint32) << 8
        c1 = colour[:, 2].astype(np.uint32) | colour[:, 3].astype(np.uint32) << 8
        keep = c0 > c1
    blocks[keep, 8:] = colour[keep]
    return "DXT5", blocks.tobytes()


def pc_alpha(kind, w, h, lin, pc, force=False):
    """(kind, top level) with the PC texture's alpha, upscaled to the X360 size, or None. Many PC world textures keep a
    mask in alpha (specular strength, blend weights, cut-outs) where the X360 version is opaque DXT1 or has its own,
    regraded alpha; the PC shaders read that mask, so the X360 colour only works with the PC alpha. Only when the alpha
    channels differ beyond the runtime's limit (16) and the PC texture has an alpha channel at all."""
    pw, ph, ref = pc
    if w % pw or h % ph or w < pw or h < ph or kind not in ("DXT1", "DXT3", "DXT5", "A8R8G8B8"):
        return None
    if not force and ref[:, :, 3].min() == 255:
        return None
    x = decode_preview(kind, w, h, lin)
    if not force and np.abs(downsample(x, pw, ph)[:, :, 3] - ref[:, :, 3].astype(np.float32)).mean() <= 16:
        return None
    return with_alpha(kind, w, h, lin, upscale(ref[:, :, 3], w, h))


def tone_match(kind, w, h, lin, pc):
    """X360 top level re-graded to the PC texture's tones, or None. A per-channel lookup table from histogram matching
    at the PC size; kept only when it brings the mean RGB difference under the runtime's limit with margin (10 vs 12)
    and alpha already matches, so different art stays rejected."""
    pw, ph, ref = pc
    if w % pw or h % ph or w < pw or h < ph:
        return None
    x = decode_preview(kind, w, h, lin)
    small, ref = downsample(x, pw, ph), ref.astype(np.float32)
    if np.abs(small[:, :, :3] - ref[:, :, :3]).mean() <= 12 or np.abs(small[:, :, 3] - ref[:, :, 3]).mean() > 16:
        return None                                            # already accepted, or alpha differs: leave it
    lut = np.zeros((3, 256), np.float32)
    for c in range(3):
        src_q = np.quantile(small[:, :, c], np.linspace(0, 1, 257))
        dst_q = np.quantile(ref[:, :, c], np.linspace(0, 1, 257))
        lut[c] = np.interp(np.arange(256), src_q, dst_q)
    graded = x.astype(np.float32)
    for c in range(3):
        graded[:, :, c] = lut[c][x[:, :, c]]
    if np.abs(downsample(graded, pw, ph)[:, :, :3] - ref[:, :, :3]).mean() > 10:
        return None
    px = np.clip(graded + 0.5, 0, 255).astype(np.uint8)
    return px[:, :, [2, 1, 0, 3]].tobytes() if kind == "A8R8G8B8" else encode_dxt(kind, px)


def main():
    args = sys.argv[1:]
    src, out = args[0], args[1]
    only = [a.upper() for a in args[args.index("--only") + 1:]] if "--only" in args else []
    only = [o for o in only if not o.startswith("--")]
    png = "--png" in args
    larger, pc_size = None, {}
    if "--larger" in args:
        larger = set()
        tsv = args[args.index("--larger") + 1]
        for line in list(open(tsv))[1:]:
            f = line.rstrip("\n").split("\t")
            if f[2] and f[5] and int(f[5]) * int(f[6]) > int(f[2]) * int(f[3]):
                larger.add(int(f[0], 16))
                pc_size[int(f[0], 16)] = (int(f[2]), int(f[3]))
    better = None
    if "--same-size-better" in args:
        better = {}
        # Button and keycap art differs by design between the platforms (the PC's L1/R1 are arrows in a ring, the X360's
        # same-hash art is LT/RT), and the per-texture similarity check lets it through, so none of it comes from the
        # X360 through this set: the PC keycaps (PC_KEY_*), the PS2-named console glyphs (the X360 puts Xbox art under
        # those names) and the analog-stick/d-pad glyphs. padprompts.c draws the console glyphs itself.
        glyph_names = {"L1", "R1", "L2", "R2", "L3", "R3", "CROSS", "CIRCLE", "SQUARE", "TRIANGLE", "START", "SELECT",
                       "BACK", "A", "B", "X", "Y", "LT", "RT", "LB", "RB", "BUTTON_RING", "DPAD", "DPAD_UP", "DPAD_DOWN",
                       "DPAD_LEFT", "DPAD_RIGHT", "DPAD_LEFTRIGHT", "DPAD_UPDOWN", "LEFT_ANALOG", "RIGHT_ANALOG"}
        glyph_prefixes = ("PC_KEY_", "DPAD_", "LEFT_ANALOG", "RIGHT_ANALOG")
        for line in list(open(args[args.index("--same-size-better") + 1]))[1:]:
            f = line.rstrip("\n").split("\t")
            if len(f) < 10 or not f[2] or not f[5] or (f[2], f[3]) != (f[5], f[6]) or \
                    not f[9].upper().startswith(("GLOBAL/", "FRONTEND/", "LANGUAGES/")):
                continue
            pcf, xf = f[4], f[7]
            if f[1].upper() in glyph_names or f[1].upper().startswith(glyph_prefixes):
                continue
            if (xf in ("A8R8G8B8", "LIN_A8R8G8B8") and pcf in ("DXT1", "DXT3", "DXT5", "P8")) or \
                    (xf == "DXT5" and pcf == "DXT3"):
                better[int(f[0], 16)] = (int(f[2]), int(f[3]))
        pc_size.update(better)
    pc_dir = next((args[args.index(o) + 1] for o in ("--tone-match", "--pc-alpha") if o in args), None)
    fe_sources, fe_inventory, fe_alpha = [], {}, set()
    if "--frontend-iso" in args or "--frontend-files" in args:
        if larger is None or not pc_dir or better is not None:
            raise ValueError("--frontend-iso requires --larger and --tone-match/--pc-alpha (not --same-size-better)")
        if "--frontend-iso" in args:
            fe_sources = list(frontend_sources(args[args.index("--frontend-iso") + 1]))
        else:
            fe_sources = [(f"FRONTEND/PLATFORMS/{scene}.LZC",
                           open(os.path.join(src, "FRONTEND", "PLATFORMS", scene + ".LZC"), "rb").read())
                          for scene in FRONTEND_SCENES]
        fe_inventory = frontend_inventory(fe_sources, pc_dir)
        for scene in fe_inventory.values():
            for h_, (pc, xbox) in scene.items():
                larger.add(h_)
                pc_size[h_] = pc[2:4]
                if pc[4] in (0x33545844, 0x35545844, 21, 41):
                    fe_alpha.add(h_)
    base_rows, extending = {}, "--extend-set" in args
    if extending:
        if not fe_inventory:
            raise ValueError("--extend-set requires --frontend-iso")
        base = args[args.index("--extend-set") + 1]
        with open(os.path.join(base, "index.tsv")) as f:
            next(f)
            for line in f:
                base_rows[int(line.split("\t", 1)[0], 16)] = line
        # A fresh destination protects the existing generated set and its tone variants.
        shutil.copytree(base, out)
        pc_size = {h: pc[2:4] for scene in fe_inventory.values() for h, (pc, _) in scene.items()
                   if h not in base_rows}
    # The same hash can appear in several packs at different sizes: keep the largest copy that covers the PC size with a
    # power-of-two factor per side (the runtime box-filters another aspect down to the PC size before validating), then
    # the one with the PC's aspect. Preferring the PC aspect first kept PC-sized copies of textures whose X360 version
    # is 2x on one side only (SGN_MW_LITALY_BRICKPIZZ 64x256 over 64x512).
    best, rows = {}, dict(base_rows)

    def score(h_, w, ht):
        pw, ph = pc_size.get(h_, (0, 0))
        fx, fy = (w // pw, ht // ph) if pw and ph else (0, 0)
        covers = fx and fy and w == fx * pw and ht == fy * ph and fx & (fx - 1) == 0 and fy & (fy - 1) == 0
        return (1 if covers else 0, w * ht if covers else 0, 1 if fx == fy else 0, w * ht)
    pc_ref = {}
    if pc_dir:
        ref_dir = os.path.join(pc_dir, "FRONTEND", "PLATFORMS") if extending else pc_dir
        pc_ref = pc_textures(ref_dir, pc_size)
        if fe_inventory:
            needed = {h for scene in fe_inventory.values() for h in scene} - base_rows.keys()
            if needed - pc_ref.keys():
                raise ValueError(f"missing frontend PC references: {sorted(needed - pc_ref.keys())}")
        print(f"{len(pc_ref)} PC reference textures")
    os.makedirs(out, exist_ok=True)
    done, skipped, graded, alpha = 0, 0, 0, 0
    index = open(os.path.join(out, "index.tsv"), "w")
    index.write("hash\tname\twidth\theight\tformat\tsource\n")
    def inputs():
        if extending:
            yield from fe_sources
            return
        for dp, _, fs in os.walk(src):
            if os.path.relpath(dp, src).split(os.sep)[0].upper() == "NFS":
                continue
            for fn in sorted(fs):
                if not fn.upper().endswith((".BIN", ".BUN", ".LZC")):
                    continue
                if fn.upper() == "VINYLS.BIN" and "--vinyls" not in args:
                    continue
                path = os.path.join(dp, fn)
                with open(path, "rb") as f:
                    yield os.path.relpath(path, src), f.read()
        yield from fe_sources
    for source, raw in inputs():
        d = unpack_file(raw)
        for name, h_, w, ht, fmt, pix in textures_in(d):
            if name == "CSHOP_TOOLBOX_NORMAL":
                continue                              # X360-only; no corresponding PC scene texture/material binding
            if only and not any(o in name.upper() for o in only):
                continue
            if larger is not None and h_ not in larger:
                continue
            if h_ in base_rows:
                continue
            if extending and h_ not in pc_size:
                continue                                  # only scene-enlarged hashes, independent of old global TSV
            if better is not None and (better.get(h_) != (w, ht) or
                                       not source.upper().startswith(("GLOBAL", "FRONTEND", "LANGUAGES"))):
                continue
            sc = score(h_, w, ht)
            if h_ in best and best[h_] >= sc:
                continue
            r = top_level(fmt, w, ht, pix)
            if not r:
                skipped += 1
                continue
            kind, lin = r
            fname = f"{h_:08x}_{re.sub(r'[^A-Za-z0-9_]', '_', name)}"
            variant = os.path.join(out, "x360_tone", fname + ".dds")
            if os.path.exists(variant):
                os.remove(variant)                         # a better copy of the hash replaces an earlier one
            if h_ in pc_ref and "--pc-alpha" in args:
                # The Init hook also rejects DXT1 for PC alpha-capable formats, even for an opaque P8 palette.
                a = pc_alpha(kind, w, ht, lin, pc_ref[h_], force=h_ in fe_alpha and kind == "DXT1")
                if a is not None:
                    (kind, lin), alpha = a, alpha + 1
            if h_ in pc_ref and "--tone-match" in args:
                g = tone_match(kind, w, ht, lin, pc_ref[h_])
                if g is not None:
                    # the X360 tones are kept next to the set for NFS_X360_TEXTURES_TONE=x360: the runtime
                    # validates the graded copy against the PC texture, then loads this one
                    os.makedirs(os.path.dirname(variant), exist_ok=True)
                    mips = mip_chain(kind, w, ht, lin)[0] if "--mips" in args else None
                    open(variant, "wb").write(dds(kind, w, ht, lin, mips))
                    lin, graded = g, graded + 1
            mips = mip_chain(kind, w, ht, lin)[0] if "--mips" in args else None
            open(os.path.join(out, fname + ".dds"), "wb").write(dds(kind, w, ht, lin, mips))
            if png:
                from PIL import Image
                Image.fromarray(decode_preview(kind, w, ht, lin), "RGBA").save(os.path.join(out, fname + ".png"))
            if h_ not in best:
                done += 1
            best[h_] = sc
            rows[h_] = f"{h_:08x}\t{name}\t{w}\t{ht}\t{kind}\t{source}\n"
    for h_ in sorted(rows):
        index.write(rows[h_])
    index.close()
    if fe_inventory:
        with open(os.path.join(out, "frontend_scenes.tsv"), "w") as proof:
            proof.write("scene\tenlarged\temitted\n")
            for scene, textures in fe_inventory.items():
                emitted = sum(h_ in rows for h_ in textures)
                if emitted != len(textures):
                    raise ValueError(f"{scene}: generated {emitted}/{len(textures)} enlarged textures")
                proof.write(f"{scene}\t{len(textures)}\t{emitted}\n")
    print(f"wrote {done} textures, skipped {skipped} (unhandled formats)" + (f", {alpha} with the PC alpha" if "--pc-alpha" in args else "") +
          (f", {graded} tone-matched to the PC" if "--tone-match" in args else ""))


if __name__ == "__main__":
    main()
