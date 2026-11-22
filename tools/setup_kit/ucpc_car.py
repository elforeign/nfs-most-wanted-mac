"""Undercover PC car packs: reader for the ProStreet (v25) conversion path.

Undercover PC keeps ProStreet's solid version 25 and 32-byte vertex, with these
decoded differences (AUD_S5_STK_08 and FOR_GT_STK_06, the latter checked vertex
for vertex against the Xenon copy of the same solids):
- list version 30; every indexed solid and texture is RFPK-compressed;
- solid name at +200 (ProStreet +184); texture references are 12 bytes
  (image, binding role, 0);
- mesh chunks 134900 (72 bytes) / 134902 (256-byte materials) / 134901 / 134903,
  plus the technique table 134F01;
- vertex: position = shorts 0..2 / short 3, UV = shorts 4,5 / 1024 (ProStreet
  / 32767), colour bytes 12..15, normal = shorts 8..10 / 32767;
- textures: TPK v9, one 256-byte head (124-byte record, 32-byte platform record)
  followed by one linear DXT level.
The reader returns v25_car.source_model's dictionary so v25_batch converts it.
"""
import re
import struct

import numpy as np

from . import car_convert as mw, ps_namespace, uc_attrib, uc_batch, v25_batch, v25_car
from .car_convert import require, u32, payload, children, find, name_hash

ROLES = {'DIFFUSE', 'NORMAL', 'AMBIENT', 'OPACITY', 'SELFILLUMINATION'}


def source_model(s):
    """Decode one solid; positions, normals and markers stay in the solid's own basis."""
    require(u32(s) == 0x80134010 and u32(s, 4) + 8 == len(s), 'UC PC solid envelope')
    h = payload(s, 0x134011, 16)
    require(len(h) >= 204 and struct.unpack_from('<H', h, 12)[0] == 25, 'UC PC solid version 25')
    require(not any(h[160:200]) and b'\0' in h[200:], 'UC PC solid extension/name')
    name = h[200:].split(b'\0')[0].decode('ascii')
    require(len(h) == (200 + len(name) + 4) // 4 * 4 and u32(h, 16) == name_hash(name), 'UC PC name/hash/size')
    tx = payload(s, 0x134012); ls = payload(s, 0x134013)
    require(len(tx) == h[25] * 12 and len(ls) == h[26] * 8, 'UC PC reference counts')
    textures = {a for a, _, _ in struct.iter_unpack('<III', tx)}
    lights = [a for a, _ in struct.iter_unpack('<II', ls)]
    slots = payload(s, 0x134015); ns = u32(slots)
    require(0 < ns <= 16 and len(slots) >= 4 + ns * 4, 'UC PC shader slots')
    labels = []
    for i in range(ns):
        at = 4 + ns * 4 + u32(slots, 4 + i * 4)
        require(at < len(slots) and b'\0' in slots[at:], 'UC PC shader string offset')
        labels.append(slots[at:].split(b'\0')[0].decode('ascii'))
    require(set(labels) <= ROLES, 'UC PC shader role')
    mh = payload(s, 0x134900, 16); require(len(mh) == 72, 'UC PC mesh header 72 bytes')
    n = u32(mh, 16); rb = payload(s, 0x134902, 128)
    require(n > 0 and len(rb) == n * 256, 'UC PC material count/size')
    techniques = payload(s, 0x134F01, 128); nt = u32(techniques)
    require(0 < nt <= 256 and len(techniques) == 4 + nt * 4, 'UC PC technique table')
    mo, mz = find(s, 0x80134100)
    vbs = [s[(o + 135) & ~127:o + 8 + z] for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134901]
    names = [s[o + 8:o + 8 + z] for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134C02]
    require(len(vbs) == n == len(names) and not any(any(x) for x in names), 'UC PC per-draw buffers/empty names')
    ib = payload(s, 0x134903, 32); ix = np.frombuffer(ib[:len(ib) // 2 * 2], '<u2')
    lo, hi = (np.array(struct.unpack_from('<3f', h, p)) for p in (32, 48))
    require(np.isfinite(np.r_[lo, hi]).all() and (lo <= hi).all(), 'UC PC solid bounds')
    draws, cursor, nverts, normals = [], 0, 0, set()
    for i, vb in enumerate(vbs):
        r = rb[i * 256:(i + 1) * 256]
        first, end, count, size, descriptor = [u32(r, p) for p in (0, 4, 68, 108, 112)]
        require(descriptor == 0x80200000 and len(vb) == size and size > 0 and size % 32 == 0, 'UC PC vertex descriptor/bytes')
        nv = size // 32; nb = r[58]; li = r[59]; technique = r[97]
        require(technique < nt and u32(r, 132) == u32(techniques, 4 + technique * 4), 'UC PC technique hash/selector')
        ids = struct.unpack_from('<10i', r, 8)
        require(0 < nb <= 10 and all(0 <= x < ns for x in ids[:nb]) and all(x == -1 for x in ids[nb:]), 'UC PC binding slots')
        require(li < len(lights), 'UC PC light index')
        require(first == cursor and end == first + count and count > 0 and count % 3 == 0 and end <= len(ix), 'UC PC draw index span')
        q = ix[first:end].astype(np.uint32); require(q.max() < nv, 'UC PC local draw index range')
        shorts = np.frombuffer(vb, '<i2').reshape(nv, 16).astype(np.float32)
        require((shorts[:, 3] > 0).all(), 'UC PC position scale')
        v = np.zeros((nv, 36), np.uint8); f = v.view('<f4').reshape(nv, 9)
        f[:, :3] = shorts[:, :3] / shorts[:, 3:4]
        f[:, 3:6] = np.maximum(shorts[:, 8:11] / 32767., -1.)
        lengths = np.linalg.norm(f[:, 3:6], axis=1)
        require(np.max(np.abs(lengths - 1)) < .004, 'UC PC normal length')
        f[:, 3:6] /= lengths[:, None]
        v[:, 24:28] = np.frombuffer(vb, np.uint8).reshape(nv, 32)[:, 12:16]
        f[:, 7:9] = shorts[:, 4:6] / 1024.
        require((f[:, :3] >= lo - .002).all() and (f[:, :3] <= hi + .002).all(), 'UC PC positions outside bounds')
        # The role and image arrays are the material's own; the reference index
        # bytes can be stale (see uc_car.binding_indices), so they are not read.
        images = {labels[ids[j]]: u32(r, 180 + j * 4) for j in range(nb)}
        require(len(images) == nb and 'DIFFUSE' in images and images['DIFFUSE'] in textures, 'UC PC one diffuse binding')
        if 'NORMAL' in images: normals.add(images['NORMAL'])
        draws.append(dict(vertices=v, indices=q, texture=images['DIFFUSE'], normal=images.get('NORMAL'),
                          light=lights[li], source_record=r))
        cursor += count; nverts += nv
    require(len(ib) == (cursor * 2 + 3) // 4 * 4 and not any(ib[cursor * 2:]), 'UC PC index buffer rounding')
    require(u32(mh, 64) == nverts, 'UC PC mesh vertex total')
    markers = []
    for cid, o, z in children(s, 8):
        if cid == 0x13401A:
            raw = s[(o + 23) & ~15:o + 8 + z]; require(len(raw) % 80 == 0, 'UC PC 80-byte markers')
            for at in range(0, len(raw), 80):
                row = raw[at:at + 80]
                require(np.isfinite(np.frombuffer(row[16:], '<f4')).all(), 'UC PC finite marker matrix')
                markers.append(row)
    return dict(name=name, header=h, mesh=mh, draws=draws, markers=markers, normal_textures=normals,
                stale_triangles=(u32(mh, 48) * 3 != cursor or u32(h, 20) * 3 != cursor))


def solids(raw):
    """(hash, name, unpacked solid) for every indexed solid of a GEOMETRY.BIN."""
    require(u32(raw) == 0x80134000 and u32(raw, 4) + 8 == len(raw), 'UC PC list envelope')
    entries = uc_batch.pack_entries(raw, 0x134004)
    for hs, s in entries:
        name = payload(s, 0x134011, 16)[200:].split(b'\0')[0].decode('ascii')
        require(hs == name_hash(name), 'UC PC index/solid hash')
        yield hs, name, s


# Stock parts with no ProStreet role of their own: joined as its EXHAUST body part.
UNDERBODY = ('CHASSIS', 'MUFFLER_00', 'EXHAUST_TIPS_LEFT_00', 'EXHAUST_TIPS_RIGHT_00', 'EXHAUST_TIPS_CENTER_00')
# Authored at the origin; placed at a BASE_A socket (uc_car.converted_models).
MOUNTS = {'SEAT_LEFT': 'SEAT_LEFT', 'SEAT_RIGHT': 'SEAT_RIGHT', 'DRIVER': 'SEAT_LEFT'}
# Alternatives the stock car does not show.
OPTIONAL = re.compile(r'(?:EXHAUST_TIPS_(?:LEFT|RIGHT|CENTER)|MUFFLER)_0[1-9]|DRIVER_FEMALE')


# Global normals kept: the tyre's (the ProStreet path carries and requests it). Other draws keep pack normals only.
TYRE_NORMALS = frozenset(name_hash(n) for n in ('TIRE_STYLE01_N', 'TIRE_STYLE02_N'))


def car_local(src):
    """Bake the solid's rotation; every stock pivot of the stock additions has no translation."""
    matrix = np.frombuffer(src['header'][64:128], '<f4').reshape(4, 4).astype(np.float64)
    require(np.isfinite(matrix).all() and np.array_equal(matrix[:, 3], [0, 0, 0, 1]) and not matrix[3, :3].any()
            and np.linalg.det(matrix[:3, :3]) > 1e-8, f'UC PC stock pivot {src["name"]}')
    rotation = matrix[:3, :3]; result = dict(src); result['draws'] = []
    for d in src['draws']:
        vertices = d['vertices'].copy(); f = vertices.view('<f4').reshape(-1, 9)
        f[:, :3] = f[:, :3].astype(np.float64) @ rotation
        normal = f[:, 3:6].astype(np.float64) @ np.linalg.inv(rotation).T
        f[:, 3:6] = normal / np.linalg.norm(normal, axis=1)[:, None]
        result['draws'].append(dict(d, vertices=vertices))
    result['markers'] = []
    for raw in src['markers']:
        row = bytearray(raw)
        row[16:] = (np.frombuffer(raw[16:], '<f4').reshape(4, 4).astype(np.float64) @ matrix).astype('<f4').tobytes()
        result['markers'].append(bytes(row))
    header = bytearray(src['header']); header[64:128] = np.eye(4, dtype='<f4').tobytes()
    result['header'] = bytes(header)
    return result


def decode_sources(raw, car, local_images):
    """v25_batch.decode_sources for an Undercover PC pack: stock solids under ProStreet's role names."""
    known = set(v25_batch.LIGHT_ALIASES) | set(v25_car.LIGHT_FALLBACK)
    lights = {**uc_batch.LIGHT_ALIASES, **uc_batch.LIGHT_FALLBACK}
    prefix = car + '_KIT00_'
    sources, underbody = {}, {}
    for _, name, s in solids(raw):
        require(name.startswith(car + '_'), f'UC PC foreign solid {name}')
        if not v25_batch.stock_name(name, car): continue
        role, lod = name[len(prefix):].rsplit('_', 1)
        if OPTIONAL.fullmatch(role): continue
        model = car_local(source_model(s))
        for d in model['draws']:
            # ProStreet's reviewed finish choices first; Undercover-only materials after.
            if d['light'] not in known: d['light'] = lights.get(d['light'], d['light'])
            if d['normal'] not in local_images and d['normal'] not in TYRE_NORMALS: d['normal'] = None
        model['normal_textures'] = {d['normal'] for d in model['draws']} - {None}
        if role in UNDERBODY: underbody.setdefault(lod, []).append(model)
        else: sources[name] = model
    for lod, parts in underbody.items():
        name = f'{prefix}EXHAUST_{lod}'
        require(name not in sources, 'UC PC EXHAUST role already present')
        sources[name] = dict(parts[0], name=name, draws=[d for p in parts for d in p['draws']],
                             markers=list(dict.fromkeys(r for p in parts for r in p['markers'])),
                             normal_textures=set().union(*(p['normal_textures'] for p in parts)),
                             stale_triangles=any(p['stale_triangles'] for p in parts))
    # One tyre image a car: the game asks for one tread (ps_namespace.WHEEL_REQUESTS). DOD_CHR_BEE_07's
    # LOD C wheels bind TIRE_STYLE02 on 24 of 144 tyre triangles; they take the image LODs A and B show.
    styles = {name_hash(n): name_hash(n + '_N') for n in ('TIRE_STYLE01', 'TIRE_STYLE02')}
    shown = {d['texture'] for n, m in sources.items() if n.endswith('_A') for d in m['draws']} & styles.keys()
    if len(shown) == 1:
        style = next(iter(shown))
        for model in sources.values():
            for d in model['draws']:
                if d['texture'] in styles and d['texture'] != style:
                    d['texture'] = style
                    if d['normal'] in styles.values(): d['normal'] = styles[style]
            model['normal_textures'] = {d['normal'] for d in model['draws']} - {None}
    sockets = {u32(r): np.frombuffer(r[16:], '<f4').reshape(4, 4) for r in sources.get(prefix + 'BASE_A', {}).get('markers', [])}
    for name in list(sources):
        role = name[len(prefix):].rsplit('_', 1)[0]
        if role not in MOUNTS: continue
        mount = sockets.get(name_hash(MOUNTS[role]))
        if mount is None or not np.array_equal(mount[:3, :3], np.eye(3)):
            del sources[name]; continue  # never left at the origin
        sources[name] = uc_batch.translated_source(sources[name], mount[3, :3])
    return sources


def v8_textures(d):
    """The pack's images as the streamed v8 layout the Most Wanted texture writer reads (pixels, record, platform)."""
    require(u32(d) == 0xB3300000 and u32(d, 4) + 8 == len(d), 'UC PC TPK envelope')
    hp = bytearray(payload(d, 0x33310001)); require(u32(hp) == 9, 'UC PC TPK version 9')
    struct.pack_into('<I', hp, 0, 8)
    blobs = []
    for hs, blob in uc_batch.pack_entries(d, 0x33310003):
        require(len(blob) > 256 and not any(blob[156:256]), 'UC PC texture head')
        r, pl, pixels = bytearray(blob[:124]), blob[124:156], blob[256:]
        require(u32(r, 12) == hs and u32(r, 28) == len(pixels) and not any(pl[24:]) and
                (r[46], pl[12:16]) in ((0x22, b'DXT1'), (0x24, b'DXT3'), (0x26, b'DXT5')), 'UC PC texture record')
        width, height = struct.unpack_from('<HH', r, 40)
        require(len(pixels) == max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * (8 if r[46] == 0x22 else 16),
                'UC PC texture is one DXT level')
        name = bytes(r[89:89 + r[88]]).split(b'\0')[0].decode('ascii')
        # Stock headlight diffuse alpha is an emission mask, not coverage.
        # Glass keeps its own blended record; never infer a role from a prefix.
        if name.endswith('_KIT00_HEADLIGHT_ON') and name_hash(name) == hs:
            r[0x3A] = 0
        blobs.append((hs, pixels + r + pl[:24]))
    def header(table):
        return mw.chunk(0xB3310000, mw.chunk(0x33310001, hp) +
                        mw.chunk(0x33310002, b''.join(struct.pack('<II', h, 0) for h, _ in blobs)) +
                        mw.chunk(0x33310003, table))
    head = mw.chunk(0x33320001, payload(d, 0x33320001))
    base = 8 + len(header(bytes(24 * len(blobs)))) + 8 + len(head) + 8
    table, data = bytearray(), bytearray()
    for hs, blob in blobs:
        table += struct.pack('<6I', hs, base + len(data), len(blob), len(blob), 0, 0); data += blob
    return mw.chunk(0xB3300000, header(bytes(table)) + mw.chunk(0xB3320000, head + mw.chunk(0x33320002, bytes(data))))


def opaque_tyres(d):
    """Undercover's tyre images are DXT5 filed sorted and blended (record +38 = 1, +3A = 1; ProStreet's and
    MW's own tyre records hold 0, 0). The converter keeps each image's state, so they are filed opaque here."""
    d = bytearray(d); tyres = {name_hash(n) for n in ('TIRE_STYLE01', 'TIRE_STYLE02')}
    for cid, o, z in mw.walk(d):
        if cid != 0x33310004: continue
        q, end = o + 8, o + 8 + z
        while q < end:
            size = (89 + d[q + 88] + 3) // 4 * 4
            require(d[q + 88] > 0 and size <= end - q, 'UC PC global texture record range')
            if u32(d, q + 12) in tyres: d[q + 0x38] = d[q + 0x3A] = 0
            q += size
    return bytes(d)


def convert(car, donor, source, native):
    """Convert the selected Undercover PC snapshot."""
    raw = bytearray(source[f'CARS/{car}/GEOMETRY.BIN'])
    off, size = mw.pack_header(raw, 0x80134001)
    info, length = find(raw[off:off + 8 + size], 0x134002)
    version = off + info + 16
    require(length == 144 and u32(raw, version) == 30, 'UC PC list version 30')
    # The shared writer carries the list header into the MW v22 pack.
    struct.pack_into('<I', raw, version, 29)
    textures = v8_textures(source[f'CARS/{car}/TEXTURES.BIN'])
    local_images = {h for h, _ in mw.texture_blobs(textures)}
    shared = opaque_tyres(uc_batch.refpack(source['GLOBAL/GLOBALB.LZC']))
    template = ps_namespace.TEMPLATE
    bins = ps_namespace.stock_packs(
        car, donor, raw, textures, shared, shared,
        native[f'CARS/{donor}/GEOMETRY.BIN'], native[f'CARS/{donor}/TEXTURES.BIN'],
        native[f'CARS/{template}/GEOMETRY.BIN'], native[f'CARS/{template}/TEXTURES.BIN'], None,
        decode=lambda data, name: decode_sources(data, name, local_images))
    attributes = source['GLOBAL/ATTRIBUTES.BIN']
    bins['WHEELFIT.BIN'] = mw.wheel_sidecar(attributes, car, donor, vaults=uc_attrib.vaults(attributes))
    bins['PERFORMANCE.BIN'] = uc_attrib.performance(attributes, native['GLOBAL/ATTRIBUTES.BIN'], car, donor)
    return bins
