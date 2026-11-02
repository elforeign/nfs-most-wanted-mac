"""Read locally supplied Xenon car solids and v8 textures for the common writer.

Metadata is little endian; vertex and index streams are big endian. Each draw
owns one vertex buffer. Returned models use the common 36-byte MW layout.
Textures retain only the validated top level: packed lower mips are discarded,
not interpreted as a linear chain. BC5 uses unsigned R/G, positive reconstructed
normal Z and opaque alpha; it cannot recover an authored third channel/alpha.
DXT blocks are preserved; BC5 and linear BGRA are encoded as DXT5 for the writer.
Platform auxiliary streams are retained under ``x360_opaque``, never ``extras``:
their layouts are not decoded yet and the PC loader interprets those chunk IDs.
"""
from functools import lru_cache
from pathlib import Path
import struct
import sys

import numpy as np

from . import car_convert as cc

FormatError = cc.FormatError
require = cc.require
OPAQUE_CHUNKS = frozenset((0x134017, 0x134018, 0x134019))


def source_model(data):
    """Decode one decompressed solid into ``car_convert.model``'s dictionary."""
    require(cc.u32(data) == 0x80134010 and cc.u32(data, 4) + 8 == len(data), 'solid envelope')
    mh = cc.payload(data, 0x134900, 16)
    require(len(mh) == 80 and cc.words(mh, 8, '<H')[0] == 17, 'X360 mesh header size/version')
    n, triangles, total_vertices = (cc.u32(mh, p) for p in (16, 64, 72))
    require(0 < n <= 65535 and 0 < total_vertices < 65536 and triangles > 0, 'X360 mesh totals')
    raw = cc.payload(data, 0x134b02, 128)
    require(len(raw) == n * 128, 'X360 material count/size')
    mo, mz = cc.find(data, 0x80134100)
    buffers, names = [], []
    for cid, off, size in cc.children(data, mo + 8, mo + 8 + mz):
        if cid == 0x134b01:
            at = (off + 135) & ~127
            buffers.append(cc.span(data, at, off + 8 + size - at, 'X360 vertex alignment'))
        elif cid == 0x134c02:
            # Retail builds strip these labels. An empty C string preserves
            # that absence in the common model; no material role is invented.
            names.append(cc.span(data, off + 8, size) if size else b'\0')
    require(len(buffers) == len(names) == n, 'X360 draw buffer/name count')
    ib = cc.payload(data, 0x134b03, 32)
    used_bytes = triangles * 6
    require(len(ib) == (used_bytes + 3) // 4 * 4 and not any(ib[used_bytes:]), 'X360 index count/padding')
    indices = np.frombuffer(ib[:used_bytes], '>u2').astype(np.uint32)
    vertices, out_indices, records = [], [], []
    first = base = 0
    for i, vb in enumerate(buffers):
        r = raw[i * 128:(i + 1) * 128]
        fi, effect, _, flags = cc.words(r, 0, '<4I')
        count = cc.words(r, 16, '<H')[0]
        byte_count, descriptor = cc.words(r, 116, '<II')
        require(effect in (2, 7, 8), f'X360 effect {effect} not decoded yet')
        stride = 40 if effect == 8 else 32
        require(descriptor == (0x80000000 | stride << 16), 'X360 vertex descriptor/stride')
        require(byte_count == len(vb) and byte_count > 0 and byte_count % stride == 0, 'X360 vertex byte count')
        nv = byte_count // stride
        require(fi == first and count > 0 and count % 3 == 0 and count <= len(indices) - fi,
                'X360 draw index coverage')
        q = indices[fi:fi + count]
        require(q.max() < nv, 'X360 local vertex index')
        a = np.frombuffer(vb, np.uint8).reshape(nv, stride)
        def floats(offset, number):
            return a[:, offset:offset + number * 4].copy().view('>f4').reshape(nv, number).astype('<f4')
        positions = floats(0, 3)
        uv = floats(16 if effect == 2 else 12, 2)
        normal = a[:, 24:32].copy().view('>i2').reshape(nv, 4).astype(np.float32)
        normal = np.maximum(normal[:, :3] / 32767., -1.).astype('<f4')
        require(np.isfinite(positions).all() and np.isfinite(uv).all(), 'X360 nonfinite vertex')
        out = np.empty((nv, 36), np.uint8)
        out[:, :12] = positions.view(np.uint8).reshape(nv, 12)
        out[:, 12:24] = normal.view(np.uint8).reshape(nv, 12)
        color = 12 if effect == 2 else 20
        out[:, 24:28] = a[:, color:color + 4][:, ::-1]
        out[:, 28:36] = uv.view(np.uint8).reshape(nv, 8)
        rec = bytearray(104)
        struct.pack_into('<6f', rec, 0, *positions.min(0), *positions.max(0))
        rec[24:29] = r[19:24]
        rec[29] = r[18]
        struct.pack_into('<I', rec, 48, 4)
        struct.pack_into('<I', rec, 56, flags)
        struct.pack_into('<III', rec, 60, nv, count // 3, first)
        struct.pack_into('<I', rec, 92, count)
        records.append(bytes(rec))
        vertices.append(out)
        out_indices.append(q + base)
        first += count
        base += nv
    require(first == triangles * 3 and base == total_vertices, 'X360 draw totals differ from header')
    header = bytearray(cc.payload(data, 0x134011, 16))
    cc.span(header, 0, 164)
    struct.pack_into('<I', header, 20, triangles)
    mesh = bytearray(48)
    # The common writer emits the PC mesh-info version and retains source flags.
    struct.pack_into('<II', mesh, 8, 18, cc.u32(mh, 12))
    struct.pack_into('<I', mesh, 16, n)
    struct.pack_into('<I', mesh, 24, 1)
    struct.pack_into('<I', mesh, 44, first)
    resources = []
    for cid, count in ((0x134012, header[25]), (0x134013, header[26])):
        table = cc.payload(data, cid)
        require(len(table) == count * 8, 'X360 resource table size')
        resources.append([h for h, _ in struct.iter_unpack('<II', table)])
    extras, opaque = [], []
    for cid, off, size in cc.children(data, 8):
        if cid in (0x134011, 0x134012, 0x134013, 0x80134100):
            continue
        start = (off + 23) & ~15 if cid == 0x13401a else off + 8
        raw = cc.span(data, start, off + 8 + size - start)
        (opaque if cid in OPAQUE_CHUNKS else extras).append((cid, raw))
    model = dict(header=bytes(header), name=cc.text_at(header, 160), mesh=bytes(mesh),
                 records=records, names=names, vertices=np.concatenate(vertices),
                 indices=np.concatenate(out_indices), groups=n,
                 textures=resources[0], lights=resources[1], extras=extras, x360_opaque=opaque)
    return cc.validate_model(model, source=True)


def source_models(data):
    """Decode an indexed geometry pack, checking each resource identity."""
    models = {}
    for hs, solid in cc.solids(data):
        model = source_model(solid)
        require(cc.u32(model['header'], 16) == hs and model['name'] not in models,
                'X360 indexed solid identity')
        models[model['name']] = model
    return models


# Full format words include the endian and tiled bits; unknown variants fail.
FORMATS = {0x1A200152: ('DXT1', 8, 0x22), 0x1A200153: ('DXT3', 16, 0x24),
           0x1A200154: ('DXT5', 16, 0x26), 0x1A200171: ('BC5', 16, 0x27),
           0x18280086: ('A8R8G8B8', 4, 0x20)}


@lru_cache(maxsize=1)
def _pixels():
    # The existing packaged pixel codec is shared with the texture importer.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools_snapshot'))
    import x360_tex
    return x360_tex


@lru_cache(maxsize=32)
def _tile_indices(wb, hb, bpb):
    ys, xs = np.mgrid[:hb, :wb]
    log = bpb.bit_length() - 1
    aligned = (wb + 31) & ~31
    macro = ((xs >> 5) + (ys >> 5) * (aligned >> 5)) << (log + 7)
    micro = ((xs & 7) + ((ys & 6) << 2)) << log
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((ys & 8) << (3 + log)) + ((ys & 1) << 4)
    return ((((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((ys & 16) << 7) +
             (((((ys & 8) >> 2) + (xs >> 3)) & 3) << 6)) >> log).ravel()


def linear_top(fmt, width, height, pixels):
    """Return (format name, unswapped linear top bytes), rejecting short input.

    Small tiled surfaces may share an allocation with packed lower mips; only
    addressed top-level blocks are read. No missing bytes are synthesized.
    """
    require(fmt in FORMATS, f'X360 texture format {fmt:#010x} not decoded yet')
    require(0 < width <= 4096 and 0 < height <= 4096, 'X360 texture dimensions')
    name, bpb, _ = FORMATS[fmt]
    if name == 'A8R8G8B8':
        pitch = (width * 4 + 255) & ~255
        require(len(pixels) >= pitch * height, 'X360 linear top truncated')
        a = np.frombuffer(pixels[:pitch * height], np.uint8).reshape(height, pitch)[:, :width * 4]
        return name, a.copy().reshape(-1, 4)[:, ::-1].tobytes()
    wb, hb = (width + 3) // 4, (height + 3) // 4
    offsets = _tile_indices(wb, hb, bpb)
    need = (int(offsets.max()) + 1) * bpb
    require(len(pixels) >= need, 'X360 tiled top truncated')
    blocks = np.frombuffer(pixels[:len(pixels) // bpb * bpb], np.uint8).reshape(-1, bpb)
    top = blocks[offsets].reshape(-1, 2)[:, ::-1]
    return name, top.tobytes()


def _bc4(blocks):
    a, b = blocks[:, 0].astype(np.int32), blocks[:, 1].astype(np.int32)
    seven = [((7 - i) * a + i * b) // 7 for i in range(1, 7)]
    five = [((5 - i) * a + i * b) // 5 for i in range(1, 5)] + [np.zeros_like(a), np.full_like(a, 255)]
    palette = np.where((a > b)[:, None], np.stack([a, b] + seven, 1), np.stack([a, b] + five, 1))
    bits = sum(blocks[:, i + 2].astype(np.uint64) << np.uint64(i * 8) for i in range(6))
    selectors = np.stack([((bits >> np.uint64(i * 3)) & 7).astype(np.intp) for i in range(16)], 1)
    return np.take_along_axis(palette, selectors, 1)


def decode_image(name, width, height, pixels):
    """RGBA image; BC5's missing Z is the positive unit-hemisphere solution."""
    require(0 < width <= 4096 and 0 < height <= 4096, 'linear image dimensions')
    require(name in ('DXT1', 'DXT3', 'DXT5', 'BC5', 'A8R8G8B8'), 'unsupported linear image')
    if name != 'BC5':
        bpb = 8 if name == 'DXT1' else 16
        size = width * height * 4 if name == 'A8R8G8B8' else ((width + 3) // 4) * ((height + 3) // 4) * bpb
        require(len(pixels) == size, 'linear image byte count')
        return _pixels().decode_preview(name, width, height, pixels)
    bw, bh = (width + 3) // 4, (height + 3) // 4
    require(len(pixels) == bw * bh * 16, 'BC5 byte count')
    blocks = np.frombuffer(pixels, np.uint8).reshape(-1, 16)
    rg = np.stack([_bc4(blocks[:, :8]), _bc4(blocks[:, 8:])], 2)
    xy = rg.astype(np.float64) / 127.5 - 1
    blue = np.rint((np.sqrt(np.maximum(0, 1 - (xy * xy).sum(2))) + 1) * 127.5)
    rgba = np.concatenate([rg, blue[:, :, None], np.full((*rg.shape[:2], 1), 255)], 2).astype(np.uint8)
    return rgba.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)[:height, :width]


def texture_entry(hs, blob):
    """Return a checked PC-v8 (record, platform, top pixels) writer tuple.

    Platform tail is 32 bytes on input and 24 on output. The common writer
    accepts DXT1/3/5; BC5 and BGRA therefore undergo explicit lossy DXT5 encoding.
    Exactly one mip is emitted, matching the common writer's upload policy.
    """
    require(len(blob) >= 156, 'X360 texture tail truncated')
    record, platform, pixels = blob[-156:-32], blob[-32:], blob[:-156]
    fmt = cc.u32(platform, 20)
    require(fmt in FORMATS, f'X360 texture format {fmt:#010x} not decoded yet')
    require(cc.u32(record, 12) == hs and cc.u32(record, 28) == len(pixels), 'X360 texture hash/image size')
    width, height = cc.words(record, 40, '<HH')
    require(0 < width <= 4096 and 0 < height <= 4096 and record[44] <= 12 and record[45] <= 12 and
            width == 1 << record[44] and height == 1 << record[45], 'X360 texture dimensions/logs')
    require(record[46] == FORMATS[fmt][2], 'X360 texture format/code mismatch')
    require(1 <= record[50] <= max(record[44], record[45]) + 1, 'X360 texture mip count')
    name_length = record[88]
    require(1 < name_length <= len(record) - 89 and record[89 + name_length - 1] == 0,
            'X360 texture name range')
    cc.text_at(record, 89, 89 + name_length)
    padded_top = (((width * 4 + 255) & ~255) * height if fmt == 0x18280086 else
                  (((width + 3) // 4 + 31) & ~31) * (((height + 3) // 4 + 31) & ~31) * FORMATS[fmt][1])
    require(cc.u32(record, 36) == padded_top <= len(pixels), 'X360 padded top size')
    name, top = linear_top(fmt, width, height, pixels)
    if name in ('BC5', 'A8R8G8B8'):
        top = _pixels().encode_dxt('DXT5', decode_image(name, width, height, top))
        name = 'DXT5'
    r, pl = bytearray(record), bytearray(24)
    struct.pack_into('<III', r, 20, 0, 0xFFFFFFFF, len(top))
    struct.pack_into('<I', r, 36, len(top))
    r[46] = {'DXT1': 0x22, 'DXT3': 0x24, 'DXT5': 0x26}[name]
    r[50] = 1
    pl[12:16] = name.encode('ascii')
    result = bytes(r), bytes(pl), top
    cc.validate_texture(*result, 8, hs)
    return result


def source_textures(data):
    """Decode every indexed texture into common-writer entries keyed by hash."""
    require(cc.u32(cc.payload(data, 0x33310001)) == 8, 'X360 texture pack version')
    return {hs: texture_entry(hs, blob) for hs, blob in cc.texture_blobs(data)}
