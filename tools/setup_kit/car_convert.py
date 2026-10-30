"""Convert locally supplied PC car assets into MW v22 geometry and v5 TPKs.

Shared is a source GLOBAL directory, or a mapping containing ``carbon_global``,
``attributes`` and ``mw_root`` paths. No asset values are bundled here. Lamp
material transcriptions remain excluded. Classified neutral illumination and
source rim-finish coefficients are retained alongside MW material roles. Structural success does not approve appearance.

The reader validates all binary ranges before decoding. Outputs use independent
resource identities and include SOLIDMAP.BIN and WHEELFIT.BIN. Input digests are
rechecked before publication; the report records source and output SHA-256s.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import struct
from collections.abc import Mapping

import numpy as np

from setup_kit import huff

MAX_FILE = 512 << 20
MAX_ENTRY = 128 << 20


class ConversionError(ValueError):
    """Invalid, unsupported or inconsistent local asset data."""


class FormatError(ConversionError):
    """A binary record is outside its container or has an invalid shape."""


class MissingStockPartError(ConversionError):
    """The required stock geometry cannot be assembled."""


class NamespaceCollisionError(ConversionError):
    """Two resources would acquire the same identity."""


class InputChangedError(ConversionError):
    """A conversion input changed while it was being read."""


def require(ok, message):
    if not ok:
        raise (NamespaceCollisionError if 'collision' in message else FormatError)(message)


def span(data, offset, size, label='binary range'):
    require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset, label)
    return data[offset:offset + size]


def words(data, offset, fmt='<I'):
    return struct.unpack(fmt, span(data, offset, struct.calcsize(fmt)))


def u32(data, offset=0):
    return words(data, offset)[0]


def children(data, start=0, end=None):
    end = len(data) if end is None else end
    require(0 <= start <= end <= len(data), 'invalid chunk range')
    while start < end:
        require(end - start >= 8, 'truncated chunk header')
        cid, size = words(data, start, '<II')
        require(size <= end - start - 8, 'chunk crosses parent')
        yield cid, start, size
        start += 8 + size


def walk(data, start=0, end=None, depth=0):
    require(depth < 64, 'chunk nesting limit')
    for cid, offset, size in children(data, start, end):
        yield cid, offset, size
        if cid & 0x80000000:
            yield from walk(data, offset + 8, offset + 8 + size, depth + 1)


def find(data, cid):
    found = [(o, z) for c, o, z in walk(data) if c == cid]
    require(len(found) == 1, f'missing or duplicate chunk {cid:08x}')
    return found[0]


def payload(data, cid, align=1):
    offset, size = find(data, cid)
    start = (offset + 8 + align - 1) & -align
    require(start <= offset + 8 + size, 'chunk alignment exceeds payload')
    return span(data, start, offset + 8 + size - start)


def index(data, cid=0x134004):
    expected = 0x80134000 if cid == 0x134004 else 0xB3300000
    require(u32(data) == expected, 'pack root type')
    end = 8 + u32(data, 4)
    kind = 0x80134001 if cid == 0x134004 else 0xB3310000
    # Streaming solid bytes following the header are opaque compressed data.
    # Only the indexed ranges, rather than chunk recursion, describe that area.
    top = next(((o, z) for c, o, z in children(data, 8, end) if c == kind), None)
    require(top is not None, 'missing pack header')
    offset, size = top
    header_end = offset + 8 + size
    rows = [(o, z) for c, o, z in walk(data, offset + 8, offset + 8 + size) if c == cid]
    require(len(rows) == 1, 'missing or duplicate pack index')
    offset, size = rows[0]
    require(size > 0 and size % 24 == 0 and size // 24 <= 65536, 'invalid pack index size')
    entries = list(struct.iter_unpack('<6I', span(data, offset + 8, size)))
    require(len({r[0] for r in entries}) == len(entries), 'duplicate indexed resource')
    hash_kind = 0x134003 if cid == 0x134004 else 0x33310002
    tables = [(o, z) for c, o, z in walk(data, top[0] + 8, header_end) if c == hash_kind]
    require(len(tables) == 1, 'missing or duplicate hash table')
    ho, hz = tables[0]
    require(hz == len(entries) * 8, 'pack hash table count')
    require([u32(data, ho + 8 + i * 8) for i in range(len(entries))] == [e[0] for e in entries],
            'pack hash table differs from index')
    if cid == 0x134004:
        infos = [(o, z) for c, o, z in walk(data, top[0] + 8, header_end) if c == 0x134002]
        require(len(infos) == 1, 'missing or duplicate solid-list info')
        at, length = infos[0]
        info = span(data, at + 8, length)
        require(u32(info, 8) == 29 and u32(info, 12) == len(entries), 'solid-list version/count')
    require(sum(e[3] for e in entries) <= MAX_FILE, 'pack expansion limit')
    ranges = []
    for _, at, stored, unpacked, flags, zero in entries:
        require(at >= header_end and stored > 0 and 0 < unpacked <= MAX_ENTRY, 'index entry size')
        span(data, at, stored, 'index outside file')
        require(at + stored <= end and flags in (0, 0x100, 0x200) and zero == 0, 'invalid index entry')
        ranges.append((at, at + stored))
    ranges.sort()
    require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'overlapping index entries')
    return entries


def decompress(data):
    try:
        if data[:4] == b'JDLZ':
            return huff.jdlz(data, max_size=MAX_ENTRY)
        if data[:4] == b'HUFF':
            return huff.decode(data)
        if data[:4] == b'RAWW':
            require(span(data, 4, 4) == b'\x01\x10\0\0', 'RAWW version')
            size, stored = words(data, 8, '<II')
            require(size == stored and size <= MAX_ENTRY and len(data) == 16 + size, 'RAWW size')
            return span(data, 16, size)
    except ValueError as exc:
        raise FormatError(str(exc)) from exc
    raise FormatError('unsupported compression envelope')


def unblock(data):
    output, pos, section, first, spans = bytearray(), 0, 0, None, []
    def complete():
        end = 0
        for start, size in sorted(spans):
            require(start == end, 'missing or overlapping streaming blocks')
            end += size
    while pos < len(data):
        if len(data) - pos < 24:
            require(not any(data[pos:]), 'truncated streaming header')
            break
        magic, usize, bsize, dst, rel, zero = words(data, pos, '<6I')
        if magic != 0x55441122:
            require(magic == 0, 'invalid streaming gap')
            span(data, pos + 8, usize)
            pos += 8 + usize
            continue
        require(24 <= bsize <= len(data) - pos and 0 < usize <= MAX_ENTRY and zero == 0, 'streaming block range')
        if rel == 0:
            complete()
            section, first, spans = len(output), pos, []
        else:
            require(first is not None and pos - first == rel, 'streaming relative offset')
        require(section + dst + usize <= MAX_FILE, 'streaming output limit')
        packed = span(data, pos + 24, bsize - 24)
        part = decompress(packed) if packed[:4] in (b'RAWW', b'JDLZ', b'HUFF') else span(packed, 0, usize)
        require(len(part) == usize, 'streaming decoded size')
        at = section + dst
        output.extend(bytes(max(0, at + usize - len(output))))
        output[at:at + usize] = part
        spans.append((dst, usize))
        pos += bsize
    complete()
    return bytes(output)


def unpack_entry(data, entry):
    _, at, stored, size, flags, zero = entry
    require(0 < size <= MAX_ENTRY and zero == 0, 'unsupported index entry')
    raw = span(data, at, stored, 'index outside file')
    if flags == 0x200:
        raw = unblock(raw)
    elif flags == 0x100:
        raw = decompress(raw)
    else:
        require(flags == 0, 'unsupported index flags')
    require(len(raw) == size, 'unpacked size differs from index')
    return raw


def solids(data):
    return [(e[0], unpack_entry(data, e)) for e in index(data)]


MW_PAINT_BINDINGS = ('SKIN1', 'SKIN1B')


def chunk(cid, data):
    return struct.pack('<II', cid, len(data)) + data


def aligned_chunk(cid, pos, data, align=16):
    return chunk(cid, b'\x11' * (-(pos + 8) % align) + data)


def car_texture_slots(defaults, *, diffuse=None, normal=None):
    require(len(defaults) == 5, 'MW five texture slots required')
    slots = list(defaults)
    if diffuse is not None: slots[0] = diffuse
    if normal is not None: slots[1] = normal
    return slots


def carbon_mesh(s):
    mh = payload(s, 0x134900, 16)
    require(len(mh) == 52, 'Carbon mesh header must be 52 aligned bytes')
    n = u32(mh, 16)
    rb = payload(s, 0x134b02, 16)
    require(n > 0 and len(rb) == n * 144, 'Carbon material count/size')
    recs = [rb[i * 144:(i + 1) * 144] for i in range(n)]
    mesh_o, mesh_z = find(s, 0x80134100)
    vbs, names = [], []
    for cid, o, z in children(s, mesh_o + 8, mesh_o + 8 + mesh_z):
        if cid == 0x134b01:
            vbs.append(s[(o + 23) & ~15:o + 8 + z])
        elif cid == 0x134c02:
            names.append(s[o + 8:o + 8 + z])
    require(len(names) == n, 'material name count')
    ib = payload(s, 0x134b03, 16)
    ib_size = u32(mh, 44) * 2
    require(len(ib) == (ib_size + 3) // 4 * 4 and not any(ib[ib_size:]), 'Carbon index count/padding')
    ib = ib[:ib_size]
    ix = np.frombuffer(ib, '<u2')
    groups, prev = [], None
    for r in recs:
        effect = u32(r, 48)
        require(effect in (0, 4, 5), f'Carbon effect {effect} not decoded')
        if effect != prev:
            groups.append([effect, 0])
            prev = effect
        groups[-1][1] += u32(r, 64)
    require(len(groups) == len(vbs), 'vertex effect group count')
    vertices, group_base = [], []
    base = 0
    for (effect, nv), vb in zip(groups, vbs):
        stride = 36 if effect == 0 else 48
        require(len(vb) == nv * stride, 'Carbon vertex size/count')
        a = np.frombuffer(vb, np.uint8).reshape(nv, stride)

        if effect in (0, 4):
            v = a[:, :36].copy()
        else:
            v = np.concatenate((a[:, :12], a[:, 24:36], a[:, 20:24], a[:, 12:20]), axis=1)
        vertices.append(v)
        group_base.append(base)
        base += nv
    require(base < 65536, 'MW u16 vertex limit')
    new_ix, new_recs = [], []
    group, used, prev, first = -1, 0, None, 0
    for r in recs:
        effect, nv, tri, old_first, count = [u32(r, p) for p in (48, 64, 96, 100, 124)]
        if effect != prev:
            group += 1
            used, prev = 0, effect
        q = ix[old_first:old_first + count].astype(np.uint32)
        require(count == tri * 3 and len(q) == count and count > 0, 'Carbon triangle range')
        require(q.min() >= used and q.max() < used + nv, 'Carbon draw vertex range')
        out = bytearray(104)
        out[:60] = r[:60]
        out[24:29] = bytes(car_texture_slots(r[24:29], diffuse=r[24], normal=r[25]))
        struct.pack_into('<I', out, 48, 4)
        struct.pack_into('<III', out, 60, nv, tri, first)
        struct.pack_into('<I', out, 92, count)
        new_recs.append(bytes(out))
        new_ix.append(q + group_base[group])
        used += nv
        first += count
    require(first == len(ix), 'Carbon material indices do not cover mesh')
    head = bytearray(mh[:48])
    struct.pack_into('<I', head, 24, 1)
    return bytes(head), b''.join(new_recs), np.concatenate(new_ix).astype('<u2').tobytes(), np.concatenate(vertices).tobytes(), names, len(groups)


def name_hash(name):
    h = 0xffffffff
    for ch in name.encode('ascii'):
        h = (h * 33 + ch) & 0xffffffff
    return h


def normal_resource_aliases(slot):
    roles = {f'{slot}_ENGINE_N': 'INTERIOR_N', 'CHASSIS_N': 'MISC_N',
             'TIRE_STYLE01_N': 'TIRE_N', 'UNIVERSAL_TIRE_N': 'TIRE_N',
             'TREAD_N': 'TIRE_N', 'DAMAGE_N': 'TREAD_N', 'SPHERIZE': 'NEON'}
    return {name_hash(source): (name_hash(f'{slot}_{target}'), f'{slot}_{target}')
            for source, target in roles.items()}


def model(s):
    mh, records, indices, vertices, names, groups = carbon_mesh(s)
    hd = bytearray(payload(s, 0x134011, 16))


    struct.pack_into('<I', hd, 20, len(indices) // 6)
    hd = bytes(hd)
    return dict(header=hd, name=hd[160:].split(b'\0')[0].decode(), mesh=mh,
                records=[records[i:i + 104] for i in range(0, len(records), 104)],
                indices=np.frombuffer(indices, '<u2').astype(np.uint32),
                vertices=np.frombuffer(vertices, np.uint8).reshape(-1, 36).copy(), names=names,
                textures=[h for h, _ in struct.iter_unpack('<II', payload(s, 0x134012))],
                lights=[h for h, _ in struct.iter_unpack('<II', payload(s, 0x134013))],
                extras=[(cid, s[(o + 23) & ~15:o + 8 + z] if cid == 0x13401a else s[o + 8:o + 8 + z])
                        for cid, o, z in children(s, 8) if cid not in
                        (0x134011, 0x134012, 0x134013, 0x80134100)], groups=groups)


def bake_driver_positions(models):
    """Bake Carbon's ROOF seat mount: MW renders DRIVER in car coordinates.

    Carbon 007D9E20 selects DRIVER_POSITION; 007DE460 multiplies that complete
    row-vector matrix by the car matrix. MW 0074E160 has no corresponding mount.
    Unmounted source drivers already use car coordinates and stay byte-exact.
    """
    for name, driver in models.items():
        if '_DRIVER_' not in name:
            continue
        roof = name.replace('_DRIVER_', '_ROOF_')
        anchor = models.get(roof, models.get(roof.rsplit('_', 1)[0] + '_A'))
        if anchor is None:
            continue
        mounts = [r[16:80] for r in marker_records(anchor)
                  if u32(r) == name_hash('DRIVER_POSITION')]
        if not mounts:
            continue
        require(all(m == mounts[0] for m in mounts), 'ambiguous driver seat mount')
        matrix = np.frombuffer(mounts[0], '<f4').reshape(4, 4).astype(np.float64)
        require(np.isfinite(matrix).all() and
                np.allclose(matrix[:, 3], [0, 0, 0, 1], atol=1e-6, rtol=0),
                'invalid driver seat matrix')
        linear = matrix[:3, :3]
        determinant = np.linalg.det(linear)
        require(abs(determinant) > 1e-8, 'singular driver seat matrix')
        vertices = driver['vertices'].copy()
        v = vertices.view('<f4').reshape(-1, 9)
        v[:, :3] = np.einsum('ij,jk->ik', v[:, :3].astype(np.float64), linear) + matrix[3, :3]
        normals = np.einsum('ij,jk->ik', v[:, 3:6].astype(np.float64), np.linalg.inv(linear).T)
        lengths = np.linalg.norm(normals, axis=1)
        nonzero = lengths > 1e-12
        normals[nonzero] /= lengths[nonzero, None]
        v[:, 3:6] = normals
        require(np.isfinite(v[:, :6]).all(), 'nonfinite mounted driver')
        driver['vertices'] = vertices
        if determinant < 0:
            driver['indices'] = driver['indices'].reshape(-1, 3)[:, [0, 2, 1]].reshape(-1).copy()
        header = bytearray(driver['header'])
        struct.pack_into('<3f', header, 32, *v[:, :3].min(0))
        struct.pack_into('<3f', header, 48, *v[:, :3].max(0))
        driver['header'] = bytes(header)
        records = []
        for raw in driver['records']:
            record = bytearray(raw)
            first, count = u32(raw, 68), u32(raw, 92)
            points = v[driver['indices'][first:first + count], :3]
            struct.pack_into('<6f', record, 0, *points.min(0), *points.max(0))
            records.append(bytes(record))
        driver['records'] = records


def native_wheel_model(s):
    h = payload(s, 0x134011, 16)
    mesh, rb = payload(s, 0x134900, 16), payload(s, 0x134b02, 16)
    require(len(mesh) == 48 and len(rb) == u32(mesh, 16) * 104, 'native wheel mesh layout')
    require(all(u32(rb, i + 48) == 4 for i in range(0, len(rb), 104)), 'native wheel effect')
    return dict(header=h, name=h[160:].split(b'\0')[0].decode(), mesh=mesh,
                records=[rb[i:i + 104] for i in range(0, len(rb), 104)],
                names=[s[o + 8:o + 8 + z] for cid, o, z in walk(s) if cid == 0x134c02],
                indices=np.frombuffer(payload(s, 0x134b03, 16)[:u32(mesh, 44) * 2], '<u2').astype(np.uint32),
                vertices=np.frombuffer(payload(s, 0x134b01, 128), np.uint8).reshape(-1, 36).copy(),
                textures=[h for h, _ in struct.iter_unpack('<II', payload(s, 0x134012))],
                lights=[h for h, _ in struct.iter_unpack('<II', payload(s, 0x134013))], extras=[], groups=1)


def marker_records(m):
    records = []
    for cid, raw in m['extras']:
        if cid == 0x13401a:
            require(len(raw) % 80 == 0, 'marker record size')
            records.extend(raw[i:i + 80] for i in range(0, len(raw), 80))
    return records


def set_markers(m, records):
    require(len(records) < 256, 'MW marker count limit')
    h = bytearray(m['header']); h[27] = len(records)
    struct.pack_into('<I', h, 128, 0)
    m['header'] = bytes(h)
    m['extras'] = [(cid, raw) for cid, raw in m['extras'] if cid != 0x13401a]
    if records:
        m['extras'].append((0x13401a, b''.join(records)))


def remember_rear_plate(m, records):
    rows = list(dict.fromkeys(r for r in records if u32(r) in REAR_PLATE_MARKERS))
    if len(rows) > 1:


        rows.sort(key=lambda r: struct.unpack_from('<f', r, 64)[0])
        x = [struct.unpack_from('<f', r, 64)[0] for r in rows]
        require(x[0] < 0 and x[1] - x[0] > 1e-5, 'ambiguous rear plate mount')
        rows = rows[:1]
    require(len(rows) <= 1, 'ambiguous rear plate mount')
    if rows:
        matrix = np.frombuffer(rows[0][16:80], '<f4').reshape(4, 4)
        require(np.isfinite(matrix).all() and np.allclose(matrix[:, 3], [0, 0, 0, 1]) and
                np.allclose(matrix[:3, :3] @ matrix[:3, :3].T, np.eye(3), atol=1e-5) and
                abs(np.linalg.det(matrix[:3, :3])) > .99999, 'rear plate mount must be rigid affine')
        m['rear_plate'] = rows[0]


def rear_plate_model(m, reference):
    candidates = []
    for _, solid in solids(reference):
        h = payload(solid, 0x134011, 16)
        name = h[160:].split(b'\0')[0].decode()
        if '_KIT00_BODY_' not in name and '_BASE_' not in name and name != m['name']:
            continue
        rb = payload(solid, 0x134b02, 16)
        names = [solid[o + 8:o + 8 + z] for cid, o, z in walk(solid) if cid == 0x134c02]
        for i, nm in enumerate(names):
            if nm.split(b'\0')[0] == b'LICENSE_PLATE':
                raw = rb[i * 104:(i + 1) * 104]


                if u32(raw, 48) != 4 or u32(raw, 64) != 2 or u32(raw, 92) != 6:
                    continue
                score = (name != m['name'], name[-1] != m['name'][-1], name[-1] != 'A', '_BASE_' in name)
                candidates.append((score, solid, raw, nm))
    require(candidates, 'source rear plate mount has no MW plate draw donor')
    _, solid, raw, nm = min(candidates, key=lambda x: x[0])
    require(u32(raw, 48) == 4 and u32(raw, 64) == 2 and u32(raw, 92) == 6,
            'MW plate donor must be a two-triangle car draw')
    ix = np.frombuffer(payload(solid, 0x134b03, 16), '<u2')[u32(raw, 68):u32(raw, 68) + 6]
    used = np.unique(ix); require(len(used) == 4, 'MW plate donor must have four corners')
    vb = np.frombuffer(payload(solid, 0x134b01, 128), np.uint8).reshape(-1, 36)[used].copy()
    v = vb.view('<f4').reshape(-1, 9)
    p, uv = v[:, :3].astype(np.float64), v[:, 7:9]
    center = p.mean(0)
    up = p[uv[:, 1] < .5].mean(0) - p[uv[:, 1] > .5].mean(0)
    up /= np.linalg.norm(up)
    across = p[uv[:, 0] < .5].mean(0) - p[uv[:, 0] > .5].mean(0)
    across -= up * np.dot(up, across); across /= np.linalg.norm(across)
    frame = np.array([up, across, np.cross(up, across)])
    mount = np.frombuffer(m['rear_plate'][16:80], '<f4').reshape(4, 4).astype(np.float64)
    rotation = frame.T @ mount[:3, :3]
    if np.linalg.det(rotation) < 0:
        ix = ix.reshape(-1, 3)[:, [0, 2, 1]].reshape(-1)
    v[:, :3] = (p - center) @ rotation + mount[3, :3]
    v[:, 3:6] = v[:, 3:6].astype(np.float64) @ rotation
    textures = [hs for hs, _ in struct.iter_unpack('<II', payload(solid, 0x134012))]
    lights = [hs for hs, _ in struct.iter_unpack('<II', payload(solid, 0x134013))]
    bindings = [textures[i] for i in raw[24:29]]
    require(bindings == [name_hash('LICENSEPLATE')] * 5 and
            lights[raw[29]] == name_hash('LICENSEPLATE'), 'MW plate material role')
    r = bytearray(raw); r[24:30] = bytes(6)
    struct.pack_into('<6f', r, 0, *v[:, :3].min(0), *v[:, :3].max(0))
    struct.pack_into('<III', r, 60, 4, 2, 0)
    h = bytearray(m['header']); h[25:28] = bytes([1, 1, 0])
    struct.pack_into('<I', h, 20, 2)
    struct.pack_into('<3f', h, 32, *v[:, :3].min(0)); struct.pack_into('<3f', h, 48, *v[:, :3].max(0))
    return dict(m, header=bytes(h), records=[bytes(r)], names=[nm], vertices=vb,
                indices=np.searchsorted(used, ix).astype(np.uint32),
                textures=[bindings[0]], lights=[lights[raw[29]]], extras=[])


def assemble(name, parts, marker_parts=None):
    result = dict(parts[0]); result['name'] = name
    textures, lights, recs, names, vertices, indices = [], [], [], [], [], []
    base, first = 0, 0
    for part in parts:
        for h in part['textures']:
            if h not in textures: textures.append(h)
        for h in part['lights']:
            if h not in lights: lights.append(h)
        for raw, matname in zip(part['records'], part['names']):
            r = bytearray(raw)
            for i in range(5): r[24 + i] = textures.index(part['textures'][r[24 + i]])
            if r[29] != 255: r[29] = lights.index(part['lights'][r[29]])
            struct.pack_into('<I', r, 68, u32(r, 68) + first)
            recs.append(bytes(r))

            nm = matname.split(b'\0')[0]
            if nm in (b'BODY_LEFT', b'BODY_RIGHT', b'BODY_REAR'): matname = b'BODY\0\0\0\0'
            names.append(matname)
        vertices.append(part['vertices']); indices.append(part['indices'] + base)
        base += len(part['vertices']); first += len(part['indices'])
    require(base < 65536 and len(textures) < 256 and len(lights) < 256, 'assembled MW limits')
    h = bytearray(result['header'][:160]); rawname = name.encode() + b'\0'
    struct.pack_into('<II', h, 16, name_hash(name), first // 3)
    h[25], h[26] = len(textures), len(lights)
    lo = np.min([struct.unpack_from('<3f', p['header'], 32) for p in parts], axis=0)
    hi = np.max([struct.unpack_from('<3f', p['header'], 48) for p in parts], axis=0)
    struct.pack_into('<3f', h, 32, *lo); struct.pack_into('<3f', h, 48, *hi)
    h += rawname + bytes(-(160 + len(rawname)) % 4)
    mh = bytearray(result['mesh']); struct.pack_into('<I', mh, 16, len(recs)); struct.pack_into('<I', mh, 44, first)
    result.update(header=bytes(h), mesh=bytes(mh), textures=textures, lights=lights, records=recs,
                  names=names, vertices=np.concatenate(vertices), indices=np.concatenate(indices))

    target_matrix = np.frombuffer(result['header'][64:128], '<f4').reshape(4, 4).astype(np.float64)
    markers = []
    for part in (parts if marker_parts is None else marker_parts):
        source_matrix = np.frombuffer(part['header'][64:128], '<f4').reshape(4, 4).astype(np.float64)
        transform = source_matrix @ np.linalg.inv(target_matrix)
        for record in marker_records(part):
            if not np.allclose(transform, np.eye(4), atol=1e-7, rtol=0):
                record = bytearray(record)
                matrix = np.frombuffer(record[16:80], '<f4').reshape(4, 4).astype(np.float64)
                record[16:80] = (matrix @ transform).astype('<f4').tobytes()
                record = bytes(record)
            if record not in markers:
                markers.append(record)
    set_markers(result, markers)
    return result


def build_model(m, pos):
    parts, at = [], pos + 8
    for cid, raw, align in [(0x134011, m['header'], 16),
                            (0x134012, b''.join(struct.pack('<II', h, 0) for h in m['textures']), 1),
                            (0x134013, b''.join(struct.pack('<II', h, 0) for h in m['lights']), 1)]:
        part = aligned_chunk(cid, at, raw, align); parts.append(part); at += len(part)
    mesh = bytearray()
    for cid, raw, al in [(0x134900, m['mesh'], 16), (0x134b02, b''.join(m['records']), 16),
                         (0x134b03, m['indices'].astype('<u2').tobytes(), 16),
                         (0x134b01, m['vertices'].tobytes(), 128)]:
        mesh += aligned_chunk(cid, at + 8 + len(mesh), raw, al)
    for nm in m['names']: mesh += chunk(0x134c02, nm)
    part = chunk(0x80134100, mesh); parts.append(part); at += len(part)
    for cid, raw in m['extras']:
        part = aligned_chunk(cid, at, raw, 16 if cid == 0x13401a else 1); parts.append(part); at += len(part)
    return chunk(0x80134010, b''.join(parts))


PAINT_NAMES = {b'BODY', b'BODY_LEFT', b'BODY_RIGHT', b'BODY_REAR', b'BODY_TOP', b'BODY_MISC'}


def map_vertex_lighting(models):
    """Repair classified rim RGB and conditionally supply neutral body illumination.

    MW car VS multiplies diffuse/specular/environment by saturate(2*COLOR.x).
    CCX exports zero for its whole painted body (and most exterior draws), so
    copying that field blacks out even correctly bound paint. Detect this from
    stock BODY_A paint, not a car-name list. The paint gate protects authored
    non-rim shading; classified zero-RGB rim vertices need repair independently.
    This is a compatibility default, not a reconstruction of Carbon's lighting.
    """
    stock = next((m for n, m in models.items() if n.endswith('_KIT00_BODY_A')), None)
    paint, base = [], 0
    for r, name in zip(stock['records'], stock['names']) if stock is not None else ():
        count = u32(r, 60)
        if bytes(name).split(b'\0')[0] in PAINT_NAMES:
            paint.append(stock['vertices'][base:base + count, 24:28])
        base += count
    neutral_body = bool(paint) and not any(v.any() for v in paint)
    changed = 0
    for m in models.values():
        base = 0
        for r, name in zip(m['records'], m['names']):
            count = u32(r, 60)
            colours = m['vertices'][base:base + count, 24:28]
            if bytes(name).startswith(b'RIM_'):
                # Lower LODs merge black lip vertices into lit spoke draws.
                # Alpha is opacity, not evidence of vertex illumination.
                dark = ~colours[:, :3].any(axis=1)
                colours[dark, :3] = 255
                changed += int(dark.sum())
            elif neutral_body and not colours.any():
                colours[:] = 255
                changed += count
            base += count
    return changed


def converted_models(d, mw=None, slot=None, texture_aliases=None, material_roles=None):
    models = {m['name']: m for _, s in solids(d) for m in [model(s)]}
    if mw is not None:
        bake_driver_positions(models)
        map_vertex_lighting(models)
    if slot is not None:
        require(slot and slot.isascii() and slot.replace('_', '').isalnum(), 'invalid MW slot name')
        source = next(iter(models)).split('_')[0]
        require(all(n.startswith(source + '_') for n in models), 'mixed source car prefixes')
        models = {slot + n[len(source):]: assemble(slot + n[len(source):], [m])
                  for n, m in models.items()}
    for m in models.values():
        m['textures'] = [(texture_aliases or {}).get(h, (h, None))[0] for h in m['textures']]
        if mw is not None:


            m['lights'] = [(material_roles or {}).get(h, h) for h in m['lights']]
    original = dict(models)
    assembled = []
    if mw is not None:
        refs = {payload(s, 0x134011, 16)[160:].split(b'\0')[0].decode(): s for _, s in solids(mw)}
        refnames = refs.keys()
        car = next(iter(models)).split('_')[0]
        tires = [f'{car}_KIT00_FRONT_TIRE_{lod}' for lod in 'ABCD']
        if not any(n in original for n in tires):
            require(all(n in refs for n in tires), 'missing source and MW stock wheel set')
            for n in tires:
                models[n] = native_wheel_model(refs[n])

                aliases = normal_resource_aliases(car)
                models[n]['textures'] = [aliases.get(h, (h, None))[0] for h in models[n]['textures']]
                assembled.append(n)
        for lod in 'ABCD':
            roof, base = f'{car}_KIT00_ROOF_{lod}', f'{car}_BASE_{lod}'
            if roof in original and base in refnames:
                anchors = [original[n] for n in (base, roof) if n in original]
                models[base] = assemble(base, [original[roof]], marker_parts=anchors); assembled.append(base)
            target = f'{car}_KIT00_BODY_{lod}'
            keys = [f'{car}_KIT00_{p}_{lod}' for p in ('BODY', 'FRONT_BUMPER', 'REAR_BUMPER', 'SKIRT', 'EXHAUST')]
            doors = [n for side in ('LEFT', 'RIGHT')
                     if (n := f'{car}_KIT00_DOOR_{side}_{lod}') in original]
            if target in original:


                keys = [k for k in keys if k in original]


                keys += doors
                if base in original: keys.append(base)
                for end in ('FRONT', 'REAR'):
                    badge = f'{car}_KIT00_{end}_BUMPER_BADGING_SET_{lod}'
                    if badge in original: keys.append(badge)
                models[target] = assemble(target, [original[k] for k in keys]); assembled.append(target)
        # INTERIORHI is a replacement, not another layer of the same cabin.
        # Route each requested interior LOD to the most detailed authored LOD
        # no more detailed than that request; never borrow the donor's cabin.
        for lod in 'ABCD':
            target = f'{car}_KIT00_INTERIOR_{lod}'
            detailed = f'{car}_KIT00_INTERIORHI_{lod}'
            if detailed in original:
                models[target] = assemble(target, [original[detailed]])
                assembled.append(target)
        for end in ('FRONT', 'REAR'):
            for lod in 'AB':
                brake, rotor = f'{car}_KIT00_{end}_BRAKE_{lod}', f'{car}_KIT00_{end}_ROTOR_{lod}'
                if brake in original and rotor in original:
                    models[brake] = assemble(brake, [original[brake], original[rotor]]); assembled.append(brake)

        for name in sorted(refnames - models.keys()):
            source = name
            for i in range(1, 6): source = source.replace(f'_KIT{i:02d}_', f'_KITW{i:02d}_')
            if source in original:
                keys = [source]
                if '_BODY_' in source:
                    keys += [source.replace('_BODY_', '_DOOR_LEFT_'), source.replace('_BODY_', '_DOOR_RIGHT_')]
                    keys = [k for k in keys if k in original]
                models[name] = assemble(name, [original[k] for k in keys]); assembled.append(name)

        reference_markers = {}
        for name, s in refs.items():
            records = []
            for cid, off, size in walk(s):
                if cid == 0x13401a:
                    raw = s[(off + 23) & ~15:off + 8 + size]
                    require(len(raw) % 80 == 0, 'MW reference marker record size')
                    records.extend(raw[i:i + 80] for i in range(0, len(raw), 80))
            reference_markers[name] = records
        required_lights = {name_hash(n) for n in (
            'LEFT_HEADLIGHT', 'RIGHT_HEADLIGHT', 'LEFT_BRAKELIGHT',
            'RIGHT_BRAKELIGHT', 'CENTRE_BRAKELIGHT', 'LEFT_REVERSE', 'RIGHT_REVERSE')}
        allowed = {u32(r) for rows in reference_markers.values() for r in rows} | required_lights
        for name, m in models.items():
            if '_BODY_' in name:
                remember_rear_plate(m, marker_records(m))
            candidates = []
            for raw in marker_records(m):
                if u32(raw) == name_hash('EXHAUST_FX'):
                    raw = bytearray(raw)
                    side = 'LEFT_EXHAUST' if struct.unpack_from('<f', raw, 68)[0] >= 0 else 'RIGHT_EXHAUST'
                    struct.pack_into('<I', raw, 0, name_hash(side)); raw = bytes(raw)
                if u32(raw) in allowed and raw not in candidates:
                    candidates.append(raw)
            if name in reference_markers:
                selected = []
                for ref in reference_markers[name]:
                    matches = [r for r in candidates if u32(r) == u32(ref)]
                    if matches:
                        point = np.array(struct.unpack_from('<3f', ref, 64))
                        raw = min(matches, key=lambda r: np.linalg.norm(np.array(struct.unpack_from('<3f', r, 64)) - point))
                        selected.append(raw); candidates.remove(raw)
                candidates = selected + [r for r in candidates if u32(r) in required_lights]
            set_markers(m, candidates)
    return models, assembled


def write_geometry(d, models, assemblies=0, *, plate_reference=None):
    require(u32(d) == 0x80134000, 'solid-list envelope')
    if plate_reference is not None:
        models = dict(models)
        for name, m in models.items():
            if '_BODY_' in name and 'rear_plate' in m and not any(
                    nm.split(b'\0')[0] == b'LICENSE_PLATE' for nm in m['names']):
                models[name] = assemble(name, [m, rear_plate_model(m, plate_reference)], marker_parts=[m])
    original_count = len(index(d))

    ho, hz = next((o, z) for c, o, z in children(d, 8, 8 + u32(d, 4)) if c == 0x80134001)
    entries = sorted((name_hash(n), m) for n, m in models.items())
    headparts = []
    for cid, off, size in children(d, ho + 8, ho + 8 + hz):
        raw = d[off + 8:off + 8 + size]
        if cid == 0x134002:
            raw = bytearray(raw); struct.pack_into('<I', raw, 12, len(entries))
        elif cid == 0x134003: raw = b''.join(struct.pack('<II', h, 0) for h, _ in entries)
        elif cid == 0x134004: raw = bytes(24 * len(entries))
        headparts.append(chunk(cid, raw))
    header = chunk(0x80134001, b''.join(headparts))
    io, iz = find(header, 0x134004); out = bytearray(chunk(0, b'') + header); rewritten = []
    for hs, m in entries:
        pad = -(8 + len(out)) % 128
        if pad:
            if pad < 8: pad += 128
            out += chunk(0, bytes(pad - 8))
        pos = 8 + len(out); s = build_model(m, pos)
        rewritten.append(struct.pack('<6I', hs, pos, len(s), len(s), 0, 0)); out += s
    out[8 + io + 8:8 + io + 8 + iz] = b''.join(rewritten)
    lo, _ = find(header, 0x134002); struct.pack_into('<I', out, 8 + lo + 8 + 0x64, len(out) + 8)
    return chunk(0x80134000, out), dict(solids=len(entries), original_solids=original_count,
                                      output_effect_groups_before_join=sum(m['groups'] for m in models.values()), assemblies=assemblies)


def texture_blobs(d):
    return [(e[0], unpack_entry(d, e)) for e in index(d, 0x33310003)]


def slot_texture_aliases(d, source, slot):
    aliases = {}


    for role in MW_PAINT_BINDINGS:
        target = f'{slot}_{role}'
        aliases[name_hash(f'{source}_{role}')] = (name_hash(target), target)
    for hs, b in texture_blobs(d):
        r = b[-148:-24]
        name = r[89:89 + r[88]].split(b'\0')[0].decode('ascii')
        if name_hash(name) != hs and name.startswith(source + '_'):


            candidates = [f'{source}_KIT{k:02d}_{kind}_{state}'
                          for k in range(100) for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS')
                          for state in ('OFF', 'ON')]
            matches = [n for n in candidates if n.startswith(name) and name_hash(n) == hs]
            require(len(matches) == 1, f'truncated texture role not decoded: {name} {hs:08X}')
            name = matches[0]
        if name.startswith(source + '_'):
            # The source TIRE atlas colours the rim. MW's implicit _TIRE_N
            # lookup would put the rubber normal map on these flat rim draws.
            # RIM_BLUR is an unused, requested stock-wheel image slot here;
            # explicit sidewall normals keep their separate TIRE_N binding.
            target = f'{slot}_RIM_BLUR' if name == source + '_TIRE' else slot + name[len(source):]
            aliases[hs] = (name_hash(target), target)
    for source_role, target_role in (('TIRE_STYLE01', 'TREAD'), ('TIRE_STYLE02', 'RIM')):
        target = f'{slot}_{target_role}'
        aliases[name_hash(source_role)] = (name_hash(target), target)
    return aliases


def source_rim_rows(data, mw_data):
    """Keep a separate finish using source diffuse/alpha and specular power.

    The 236-byte source's DiffuseMin/Max (+64/+74), alpha (+84/+88)
    and SpecularPower (+8C) match MW +30..5B. Later source fields include
    shader masks without MW equivalents: retain MW's silver lighting frame
    instead of reinterpreting those masks as colour coefficients.
    """
    rows = {}
    for cid, off, size in walk(data):
        if cid != 0x135200:
            continue
        r = data[off + 8:off + 8 + size]
        if bytes(r[20:52]).split(b'\0')[0] != b'MAGMATTE':
            continue
        require(size == 236 and u32(r, 8) == 3 and u32(r, 12) == name_hash('MAGMATTE'),
                'source rim material layout')
        require(np.isfinite(np.frombuffer(r[0x64:0x90], '<f4')).all(), 'nonfinite source rim finish')
        candidates = [mw_data[o+8:o+8+z] for c,o,z in walk(mw_data)
                      if c == 0x135200 and z == 168 and u32(mw_data,o+20) == name_hash('MAGSILVER')]
        require(len(candidates) == 1, 'MW rim material absent or duplicate')
        name = 'CARBON_MAGMATTE'
        row = bytearray(candidates[0])
        row[:8] = bytes(8)
        struct.pack_into('<I', row, 12, name_hash(name))
        row[20:48] = name.encode().ljust(28, b'\0')
        row[0x30:0x5c] = r[0x64:0x90]
        require(not rows, 'duplicate source rim material')
        rows[name_hash(name)] = bytes(row)
    return rows


def append_material_rows(geometry, rows):
    if not rows:
        return geometry
    tail = b''.join(chunk(0x135200, row) for row in rows.values())
    geometry = bytearray(geometry)
    info, _ = find(geometry, 0x134002)
    struct.pack_into('<II', geometry, info + 8 + 0x68, len(geometry), len(tail))
    return bytes(geometry) + tail


def geometry_texture_hashes(d):
    return {h for _, s in solids(d)
            for h, _ in struct.iter_unpack('<II', payload(s, 0x134012))}


def convert_textures(d, mw, extras=None, aliases=None, *, exclude=(), source_sampler_state=False,
                     namespace=None, renames=None):
    exclude = set(exclude)
    refs = {h: (b[-156:-32], b[-32:], b[:-156]) for h, b in texture_blobs(mw) if h not in exclude}
    hp = bytearray(payload(d, 0x33310001)); struct.pack_into('<I', hp, 0, 5)
    source = {}
    for hs, b in texture_blobs(d):
        require(len(b) >= 148, 'truncated v8 texture')
        r, pl = b[-148:-24], b[-24:]
        require(u32(r, 12) == hs and u32(r, 28) == len(b) - 148, 'v8 texture tail hash/size')
        source[hs] = (r, pl, b[:-148])
    source.update(extras or {})
    renamed = {}
    for hs, (r, pl, pixels) in source.items():
        if hs in (aliases or {}):
            hs, name = aliases[hs]
            r = bytearray(r); name = name.encode() + b'\0'
            require(len(name) <= 255, 'texture alias name too long')
            r = r[:89] + name
            r[88] = len(name); struct.pack_into('<I', r, 12, hs)
        require(hs not in renamed or (renamed[hs][0][28:48], renamed[hs][1][12:16], renamed[hs][2]) ==
                (r[28:48], pl[12:16], pixels), 'conflicting texture aliases')
        renamed[hs] = (r, pl, pixels)
    source = renamed
    source = {h: value for h, value in source.items() if h not in exclude}
    origin = {}
    if namespace:
        slot, prefix = namespace
        private = {}
        for hs, (r, pl, pixels) in source.items():
            name = bytes(r[89:89 + r[88] - 1]).decode('ascii')
            if name.startswith(slot + '_'):
                target = prefix + name[len(slot):]
                r = bytearray(r[:89]) + target.encode('ascii') + b'\0'
                r[88] = len(target) + 1; struct.pack_into('<I', r, 12, name_hash(target))
                origin[name_hash(target)] = hs
                hs = name_hash(target)
            require(hs not in private, 'texture namespace collision')
            private[hs] = (r, pl, pixels)
        source = private
        if renames is not None:
            renames.extend(sorted((old, new) for new, old in origin.items()))
    refs = {h: v for h, v in refs.items() if h not in origin.values()} | {
        new: refs[old] for new, old in origin.items() if old in refs}
    fallbacks = sorted(set(refs) - source.keys())
    blobs, hashes, placement = [], [], 0
    for hs in sorted(source.keys() | refs.keys()):
        if hs in source:
            r, pl, pixels = source[hs]; nl = r[88]
            require(1 <= nl <= len(r) - 89 and r[89 + nl - 1] == 0, 'v8 texture name length')
            name, fmt = r[89:89 + nl - 1], pl[12:16]
            require(fmt in (b'DXT1', b'DXT3', b'DXT5'), 'unsupported Carbon texture format')
            require(r[46] == {b'DXT1': 0x22, b'DXT3': 0x24, b'DXT5': 0x26}[fmt], 'texture format mismatch')
            old, platform, _ = refs[hs] if hs in refs else next(
                (v for v in refs.values() if v[1][20:24] == fmt), next(iter(refs.values())))
            rec = bytearray(old); rec[12:36] = name[:23].ljust(24, b'\0'); rec[36:44] = r[12:20]
            rec[48:76] = r[20:48]
            if source_sampler_state:


                rec[79:88] = r[51:60]
            struct.pack_into('<I', rec, 56, len(pixels)); struct.pack_into('<I', rec, 64, len(pixels))
            platform = bytearray(platform); platform[20:24] = fmt
        else:
            rec, platform, pixels = refs[hs]; rec = bytearray(rec)


        struct.pack_into('<I', rec, 48, placement); placement += len(pixels)
        struct.pack_into('<I', rec, 52, 0xffffffff)
        blobs.append((hs, pixels + rec + platform)); hashes.append(struct.pack('<II', hs, 0))


    def header(table):
        return chunk(0xB3310000, chunk(0x33310001, hp) + chunk(0x33310002, b''.join(hashes)) +
                     chunk(0x33310003, table))
    prefix = chunk(0, bytes(48))
    hh = header(bytes(24 * len(blobs)))
    header_pad_size = -(8 + len(prefix) + len(hh)) % 128
    if header_pad_size < 8:
        header_pad_size += 128
    gap = chunk(0, bytes(header_pad_size - 8))
    data_head = chunk(0x33320001, payload(d, 0x33320001))

    pixel_chunk_pos = 8 + len(prefix) + len(hh) + len(gap) + 8 + len(data_head)
    pad = -(pixel_chunk_pos + 8) % 128
    pixel_base = pixel_chunk_pos + 8 + pad
    table, stored_data = bytearray(), bytearray()
    for h, blob in blobs:
        stored_data += bytes(-len(stored_data) % 128)
        table += struct.pack('<6I', h, pixel_base + len(stored_data), len(blob), len(blob), 0, 0)
        stored_data += blob
    hh = header(table)
    body = data_head + chunk(0x33320002, b'\x11' * pad + stored_data)
    return chunk(0xB3300000, prefix + hh + gap + chunk(0xB3320000, body)), len(blobs)


def check_output_path(out, protected):
    candidate = str(Path(out).resolve()).casefold().rstrip(os.sep)
    for root in protected:
        root = str(Path(root).resolve()).casefold().rstrip(os.sep)
        require(candidate != root and not candidate.startswith(root + os.sep),
                f'output is inside protected source tree: {root}')


def write_outputs(out, files, protected):
    check_output_path(out, protected)
    # Pin every ancestor. O_NOFOLLOW on just the leaf permits a swapped parent
    # symlink to redirect publication after the protected-tree check.
    path = Path(os.path.abspath(out))
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    staged = []
    try:
        for component in path.parts[1:]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        for name, data in files.items():
            require(Path(name).name == name, 'output filename must be a basename')
            temporary = '.carbon-car-' + secrets.token_hex(12) + '.tmp'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            staged.append((temporary, name))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, name in staged:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        for temporary, _ in staged:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


REAR_PLATE_MARKERS = {name_hash('LICENSE_PLATE_REAR'), name_hash('REAR_LICENSE_PLATE')}


def material_role_map(data):
    """Read source identities from local v4 light records, never a source-ID table.

    135200 payloads are 236 bytes: hash at +0C, NUL name at +14 (32 bytes).
    Names must hash back to the stored identity. Only the MW role policy is
    fixed here; source coefficients and shader parameters are not copied.
    """
    targets = dict(DAMAGE='HOSES', DOORLINE='DIABLOHP', CARBONFIBER='CARBONFIBER2',
                   MAGLIP='CHROME', GRILLCHROME='CHROME')
    targets = {k: name_hash(v) for k, v in targets.items()}
    targets['MAGMATTE'] = name_hash('CARBON_MAGMATTE')
    roles = {}
    for cid, off, size in walk(data):
        if cid != 0x135200:
            continue
        require(size == 236, 'Carbon material row size')
        name = text_at(data, off + 28, off + 60)
        hs = u32(data, off + 20)
        require(hs == name_hash(name), 'Carbon material name/hash mismatch')
        if name in targets:
            require(hs not in roles, 'duplicate Carbon material role')
            roles[hs] = targets[name]
    return roles


def _mix(a, b, c):
    M = 4294967295
    a = a - b - c & M
    a ^= c >> 13
    b = b - c - a & M
    b ^= a << 8 & M
    c = c - a - b & M
    c ^= b >> 13
    a = a - b - c & M
    a ^= c >> 12
    b = b - c - a & M
    b ^= a << 16 & M
    c = c - a - b & M
    c ^= b >> 5
    a = a - b - c & M
    a ^= c >> 3
    b = b - c - a & M
    b ^= a << 10 & M
    c = c - a - b & M
    c ^= b >> 15
    return (a, b, c)


def hash32(s, init=2882400000):
    k = s.encode('latin1') if isinstance(s, str) else s
    if not k:
        return 0
    n = len(k)
    a = b = 2654435769
    c = init
    i = 0
    while n - i >= 12:
        a = a + int.from_bytes(k[i:i + 4], 'little') & 4294967295
        b = b + int.from_bytes(k[i + 4:i + 8], 'little') & 4294967295
        c = c + int.from_bytes(k[i + 8:i + 12], 'little') & 4294967295
        (a, b, c) = _mix(a, b, c)
        i += 12
    c = c + n & 4294967295
    r = k[i:]
    L = len(r)
    for j in range(min(L, 11) - 1, -1, -1):
        v = r[j]
        if j >= 8:
            c = c + (v << 8 * (j - 8 + 1)) & 4294967295
        elif j >= 4:
            b = b + (v << 8 * (j - 4)) & 4294967295
        else:
            a = a + (v << 8 * j) & 4294967295
    (a, b, c) = _mix(a, b, c)
    return c


def text_at(data, offset, limit=None):
    limit = len(data) if limit is None else limit
    require(0 <= offset < limit <= len(data), 'string offset outside range')
    end = data.find(b'\0', offset, limit)
    require(end >= offset, 'unterminated string')
    try:
        return data[offset:end].decode('ascii')
    except UnicodeDecodeError as exc:
        raise FormatError('non-ASCII resource name') from exc


def read_vpak(data):
    require(span(data, 0, 4) == b'VPAK', 'expected VPAK attributes')
    count, strings, string_size = words(data, 4, '<III')
    require(len(data) >= 16 and count * 20 <= len(data) - 16, 'VPAK directory outside file')
    require(0 <= strings <= len(data) and string_size <= len(data) - strings,
            'VPAK string table outside file')
    entries, ranges = [], [(0, 16 + count * 20), (strings, strings + string_size)]
    total = 16 + count * 20 + string_size
    require(total <= len(data), 'VPAK aggregate size exceeds input')
    for i in range(count):
        no, bs, vs, bo, vo = words(data, 16 + i * 20, '<5I')
        require(no < string_size and bs > 0 and vs > 0, 'VPAK entry shape')
        for offset, size in ((bo, bs), (vo, vs)):
            require(0 <= offset <= len(data) and size <= len(data) - offset, 'VPAK section range')
        total += bs + vs
        require(total <= len(data), 'VPAK aggregate size exceeds input')
        entries.append((no, bo, bs, vo, vs))
        ranges.extend(((bo, bo + bs), (vo, vo + vs)))
    ranges.sort()
    require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'overlapping VPAK sections')
    names, decoded = set(), []
    for no, bo, bs, vo, vs in entries:
        name = text_at(data, strings + no, strings + string_size)
        require(name and name not in names, 'duplicate or empty VPAK name')
        names.add(name)
        decoded.append((name, bo, bs, vo, vs))
    return [(name, data[bo:bo + bs], data[vo:vo + vs]) for name, bo, bs, vo, vs in decoded]


class WheelVault:
    """Bounded little-endian attribute chunks and pointer relocations."""
    def __init__(self, binary, vault):
        self.buffers = (vault, binary)
        self.chunks, self.fixups = {}, {}
        pos = 0
        while pos < len(vault):
            tag, size = words(vault, pos, '<4sI')
            if size == 0:
                require(not any(vault[pos:]), 'invalid VLT terminator')
                break
            require(size >= 8, 'VLT chunk size')
            span(vault, pos, size, 'VLT chunk crosses section')
            tag = tag[::-1]
            require(tag not in self.chunks, 'duplicate VLT chunk')
            self.chunks[tag] = (pos, size)
            pos += size
        for tag in (b'DepN', b'PtrN', b'ExpN'):
            require(tag in self.chunks, 'missing VLT directory chunk')
        dep = self.chunk(b'DepN')
        count = u32(dep, 8)
        span(dep, 12, count * 4, 'dependency table range')
        require(count >= 2, 'missing BIN/VLT dependencies')
        ptr = self.chunk(b'PtrN')
        count, remainder = divmod(len(ptr) - 8, 12)
        # The end relocation has only offset/type/dependency (eight zeros),
        # whereas ordinary relocations additionally store a value dword.
        require(remainder in (0, 8) and not any(ptr[8 + count * 12:]), 'pointer table stride/terminator')
        base = 0
        for pos in range(8, 8 + count * 12, 12):
            off, typ, idx, val = words(ptr, pos, '<IHHI')
            if typ == 0:
                require(not any(ptr[pos:]), 'pointer terminator data')
                break
            if typ == 2:
                require(idx < len(self.buffers), 'pointer base dependency')
                base = idx
            elif typ in (1, 3, 4, 5):
                span(self.buffers[base], off, 4, 'pointer site outside dependency')
                require((base, off) not in self.fixups, 'duplicate pointer fixup')
                if typ == 3:
                    require(idx < len(self.buffers), 'pointer target dependency')
                    span(self.buffers[idx], val, 0, 'pointer target outside dependency')
                    self.fixups[base, off] = (idx, val)
                else:
                    self.fixups[base, off] = None
            else:
                raise FormatError('unknown pointer relocation type')
        self.modern = b'Vers' in self.chunks
        exp = self.chunk(b'ExpN')
        count = u32(exp, 8)
        stride = 16 if self.modern else 20
        span(exp, 12, count * stride, 'export table outside chunk')
        self.exports = []
        for pos in range(12, 12 + count * stride, stride):
            row = words(exp, pos, '<4I' if self.modern else '<5I')
            span(vault, row[-1], 12, 'export outside VLT')
            self.exports.append((row[0], row[1], row[-1]))

    def chunk(self, tag):
        at, size = self.chunks[tag]
        return span(self.buffers[0], at, size)

    def pointer(self, dep, offset):
        span(self.buffers[dep], offset, 4, 'pointer location')
        return self.fixups.get((dep, offset))


WHEEL_FIELDS = (('TireOffsets', 'f', 16), ('FECompressions', 'f', 2),
                ('TireSkidWidth', 'f', 4), ('KitWheelOffsetFront', 'B', 6),
                ('KitWheelOffsetRear', 'B', 6))


def wheel_fields(data, car):
    definitions, collections = None, {}
    ecar = hash32('ecar')
    for _, binary, raw in read_vpak(data):
        v = WheelVault(binary, raw)
        for key, typ, off in v.exports:
            if typ == 0x5E970CBC and key == ecar:
                require(definitions is None, 'duplicate wheel class')
                count = u32(raw, off + 8) & 0xFFFF
                target = v.pointer(0, off + 12)
                require(target is not None, 'wheel class definitions unresolved')
                dep, at = target
                rows = list(struct.iter_unpack('<IIHHHBB', span(v.buffers[dep], at, count * 16)))
                require(len({r[0] for r in rows}) == count, 'duplicate wheel definition')
                definitions = {r[0]: r[1:] for r in rows}
            elif typ == 0x8E112EB7 and u32(raw, off + 4) == ecar:
                key = u32(raw, off)
                require(key not in collections, 'duplicate wheel collection')
                span(raw, off, 32, 'collection header outside VLT')
                collections[key] = (v, off)
    require(definitions is not None, 'wheel class missing')

    def field(key, name, seen=()):
        require(key not in seen and len(seen) < 32, 'wheel inheritance cycle or depth')
        require(key in collections and hash32(name) in definitions, 'missing wheel field or collection')
        v, off = collections[key]
        raw = v.buffers[0]
        _, at, size, maximum, flags, align = definitions[hash32(name)]
        require(size in (1, 4, 16) and maximum > 0, 'unsupported wheel element size')
        target = None
        if flags & 2:
            target = v.pointer(0, off + 28)
            if target:
                target = (target[0], target[1] + at)
        else:
            nt = words(raw, off + 24, '<H')[0] if v.modern else u32(raw, off + 24)
            ne = u32(raw, off + 20)
            span(raw, off + 32, nt * 4, 'collection type array')
            start = off + ((32 + nt * 4 + 7) & ~7) if v.modern else off + 32 + nt * 4
            entries = span(raw, start, ne * 12, 'collection entries range')
            matches = [i for i in range(ne) if u32(entries, i * 12) == hash32(name)]
            require(len(matches) <= 1, 'duplicate collection field')
            if matches:
                e = start + 12 * matches[0]
                require(raw[e + 10] & 2, 'wheel field is not an array')
                target = v.pointer(0, e + 4)
                require(target is not None, 'unresolved wheel array')
        if target is None:
            parent = u32(raw, off + 8)
            require(parent != 0, 'wheel field absent')
            return field(parent, name, seen + (key,))
        dep, pos = target
        alloc, count, esz, enc = words(v.buffers[dep], pos, '<4H')
        require(0 < count <= alloc <= maximum and esz == size, 'wheel array shape')
        pos += 8 + (8 if enc & 0x8000 else 0)
        fmt = 'B' if size == 1 else 'f'
        require(count * size <= 64, 'wheel array exceeds sidecar field limit')
        return words(v.buffers[dep], pos, '<' + str(count * size // struct.calcsize(fmt)) + fmt)

    out = {}
    for name, fmt, count in WHEEL_FIELDS:
        values = field(hash32(car.lower()), name)
        if name.startswith('KitWheelOffset') and len(values) < count:
            values += (0,) * (count - len(values))
        require(len(values) == count, 'wheel array count differs from sidecar schema')
        out[name] = values
    validate_wheels(out)
    return out


def validate_wheels(fields):
    require(all(math.isfinite(v) and abs(v) <= 20 for name, fmt, _ in WHEEL_FIELDS if fmt == 'f'
                for v in fields[name]), 'wheel values outside finite metre range')
    require(all(.1 <= fields['TireOffsets'][i] <= 1 for i in (3, 7, 11, 15)), 'wheel radius range')
    require(all(.1 <= v <= 1 for v in fields['TireSkidWidth']), 'wheel width range')


# WFT2 extends the v1 wheel prefix with values, never pointers or source layouts.
# Order is also the runtime contract: 11 floats, byte-as-u32, 8 floats,
# 6 floats, then five cameras each with HEIGHT/FOV/ANGLE/LAG float[2].
RENDER_FIELDS = ('RideHeight', 'BodyRoll', 'BodyDive', 'BodySquat', 'ReflectionOffset')
CHASSIS_FIELDS = ('RIDE_HEIGHT', 'TRAVEL', 'TRACK_WIDTH', 'WHEEL_BASE', 'FRONT_AXLE')
TIRE_FIELDS = ('RIM_SIZE', 'SECTION_WIDTH', 'ASPECT_RATIO')
CAMERA_LINKS = ('CameraInfo_Close', 'CameraInfo_Far', 'CameraInfo_Hood', 'CameraInfo_Bumper', 'CameraInfo_Drift')
CAMERA_FIELDS = ('HEIGHT', 'FOV', 'ANGLE', 'LAG')


class FitAttributes:
    """Read typed values with inheritance across class and collection exports."""
    def __init__(self, data):
        self.defs, self.collections = {}, {}
        for _, binary, raw in read_vpak(data):
            vault = WheelVault(binary, raw)
            for key, typ, off in vault.exports:
                if typ == 0x5E970CBC:
                    count = u32(raw, off + 8) & 0xFFFF
                    target = vault.pointer(0, off + 12)
                    require(target is not None, 'fit class definitions unresolved')
                    dep, at = target
                    rows = struct.iter_unpack('<IIHHHBB', span(vault.buffers[dep], at, count * 16))
                    require(key not in self.defs, 'duplicate fit class')
                    self.defs[key] = {r[0]: r[1:] for r in rows}
                elif typ == 0x8E112EB7:
                    identity = (u32(raw, off + 4), u32(raw, off))
                    require(identity not in self.collections, 'duplicate fit collection')
                    self.collections[identity] = (vault, off)

    def raw(self, cls, key, name, seen=()):
        cls = hash32(cls) if isinstance(cls, str) else cls
        require(key not in seen and len(seen) < 32, 'fit parent cycle/depth')
        require((cls, key) in self.collections, 'fit collection missing')
        vault, off = self.collections[cls, key]
        raw = vault.buffers[0]
        _, at, size, maximum, flags, _ = self.defs[cls][hash32(name) if isinstance(name, str) else name]
        target = None
        if flags & 2:
            target = vault.pointer(0, off + 28)
            if target:
                target = (target[0], target[1] + at)
        else:
            nt = words(raw, off + 24, '<H')[0] if vault.modern else u32(raw, off + 24)
            start = off + ((32 + nt * 4 + 7) & ~7) if vault.modern else off + 32 + nt * 4
            entries = span(raw, start, u32(raw, off + 20) * 12, 'fit entries range')
            matches = [i for i in range(len(entries) // 12) if u32(entries, i * 12) == hash32(name)]
            require(len(matches) <= 1, 'duplicate fit field')
            if matches:
                entry = start + matches[0] * 12
                target = (0, entry + 4) if raw[entry + 10] & 0x20 else vault.pointer(0, entry + 4)
                require(target is not None, 'fit pointer unresolved')
        if target is None:
            parent = u32(raw, off + 8)
            require(parent != 0, 'fit field absent: ' + str(name))
            return self.raw(cls, parent, name, seen + (key,))
        dep, pos = target
        if flags & 1:
            alloc, count, esz, enc = words(vault.buffers[dep], pos, '<4H')
            require(0 < count <= alloc <= maximum and esz == size, 'fit array shape')
            pos += 8 + (8 if enc & 0x8000 else 0)
            size *= count
        return bytes(span(vault.buffers[dep], pos, size, 'fit value range'))

    def floats(self, cls, key, names):
        raw = b''.join(self.raw(cls, key, name) for name in names)
        require(len(raw) % 4 == 0, 'fit float alignment')
        values = words(raw, 0, '<' + str(len(raw) // 4) + 'f')
        require(all(math.isfinite(v) and abs(v) <= 10000 for v in values), 'fit finite value range')
        return values

    def linked_key(self, cls, key, name):
        raw = self.raw(cls, key, name)
        require(len(raw) >= 12 and len(raw) % 12 == 0, 'fit reference shape')
        require(u32(raw) == hash32('camerainfo' if name in CAMERA_LINKS else name), 'fit reference class')
        return u32(raw, 4)


def render_fit(data, car):
    attrib = FitAttributes(data)
    # Wheel-only vaults remain valid v1 inputs. A full car vault must contain
    # all linked classes; an incomplete full vault is an error.
    if set(attrib.defs) == {hash32('ecar')} and len(attrib.defs[hash32('ecar')]) == len(WHEEL_FIELDS):
        return b''
    key = hash32(car.lower())
    render = attrib.floats('ecar', key, RENDER_FIELDS)
    spokes = attrib.raw('ecar', key, 'WheelSpokeCount')
    chassis = attrib.floats('chassis', attrib.linked_key('pvehicle', key, 'chassis'), CHASSIS_FIELDS)
    tires = attrib.floats('tires', attrib.linked_key('pvehicle', key, 'tires'), TIRE_FIELDS)
    cameras = tuple(v for link in CAMERA_LINKS for v in attrib.floats(
        'camerainfo', attrib.linked_key('ecar', key, link), CAMERA_FIELDS))
    require((len(render), len(spokes), len(chassis), len(tires), len(cameras)) == (11, 1, 8, 6, 40), 'fit schema counts')
    return struct.pack('<11fI54f', *render, spokes[0], *chassis, *tires, *cameras)


def wheel_sidecar(data, car, donor, *, fit=False):
    fields = wheel_fields(data, car)
    out = struct.pack('<4I', 0x31544657, 1, hash32(car.lower()), hash32(donor.lower()))
    for name, fmt, count in WHEEL_FIELDS:
        out += struct.pack('<' + str(count) + fmt, *fields[name])
    require(len(out) == 116, 'wheel sidecar size')
    tail = render_fit(data, car) if fit else b''
    if tail:
        out = out[:4] + struct.pack('<I', 2) + out[8:] + tail
    return out


def collision_sidecar(data, car, donor):
    """Translate the compatible packed bounds/point-cloud collection.

    Both PC layouts use 48-byte bounds, signed millimetre dimensions/pivots,
    then 16-byte cloud headers and float4 points. Runtime back-pointers
    are regenerated after loading; authored bytes stay exact.
    """
    rows = []
    for cid, off, size in walk(data):
        if cid == 0x3B901:
            at = (off + 23) & ~15
            raw = bytes(span(data, at, off + 8 + size - at))
            if u32(raw) == hash32(car): rows.append(raw)
    if not rows:
        require(not any(cid == 0x8003B900 for cid, _, _ in walk(data)), 'source collision collection absent')
        return None
    require(len(rows) == 1, 'duplicate source collision collection')
    raw = bytearray(rows[0]); count = u32(raw, 4)
    require(0 < count <= 128 and u32(raw, 8) == 0, 'source collision header')
    clouds = 16 + count * 48
    nclouds = u32(span(raw, clouds, 16))
    require(nclouds <= 128, 'source point-cloud count')
    for i in range(count):
        at = 16 + i * 48
        bound = span(raw, at, 48)
        dims = words(bound, 16, '<3h')
        children, cloud, first = bound[22], words(bound, 23, '<b')[0], words(bound, 30, '<h')[0]
        require(all(x >= 0 for x in dims) and max(dims) > 0, 'collision half dimensions')
        require((first == -1 and children == 0) or (first >= 0 and first + children <= count), 'collision child range')
        require(-1 <= cloud < nclouds and u32(bound, 44) == 0, 'collision cloud/back-pointer')
    pos = clouds + 16
    for _ in range(nclouds):
        header = span(raw, pos, 16); points = u32(header)
        require(4 <= points <= 4096 and u32(header, 12) == 0, 'collision cloud shape')
        values = words(span(raw, pos + 16, points * 16), 0, '<' + str(points * 4) + 'f')
        require(all(math.isfinite(v) and abs(v) < 1000 for v in values), 'collision points')
        pos += 16 + points * 16
    require(pos == len(raw), 'collision collection extent')
    return struct.pack('<5I', 0x31544C43, 1, hash32(car.lower()), hash32(donor.lower()), len(raw)) + raw


def validate_model(m, *, source=False):
    h, mesh = m['header'], m['mesh']
    require(len(h) >= 164 and len(mesh) == 48 and words(h, 12, '<H')[0] == 22, 'solid header version/size')
    name = text_at(h, 160)
    require(name == m['name'] and u32(h, 16) == name_hash(name), 'solid name/hash mismatch')
    require(len(m['textures']) == h[25] and len(m['lights']) == h[26], 'resource table counts')
    require(len(m['records']) == len(m['names']) == u32(mesh, 16) > 0, 'material table counts')
    v, ix = m['vertices'], m['indices']
    require(v.ndim == 2 and v.shape[1] == 36 and 0 < len(v) < 65536, 'vertex shape')
    floats = v.copy().view('<f4').reshape(-1, 9)
    require(np.isfinite(floats[:, :6]).all() and np.isfinite(floats[:, 7:]).all(), 'nonfinite vertex fields')
    lo, hi = words(h, 32, '<3f'), words(h, 48, '<3f')
    require(np.isfinite(lo).all() and np.isfinite(hi).all() and (np.array(lo) <= hi).all(), 'solid bounds')
    require((floats[:, :3] >= np.array(lo) - .002).all() and
            (floats[:, :3] <= np.array(hi) + .002).all(), 'vertex outside solid bounds')
    matrix = np.frombuffer(span(h, 64, 64), '<f4').reshape(4, 4)
    require(np.isfinite(matrix).all() and abs(np.linalg.det(matrix.astype(float))) > 1e-12, 'invalid solid transform')
    require(len(ix) > 0 and len(ix) == u32(mesh, 44) == u32(h, 20) * 3 and ix.max() < len(v), 'mesh index totals')
    require(np.abs(np.linalg.norm(floats[ix, 3:6], axis=1) - 1).max() < .001, 'referenced normal is not unit length')
    first = base = 0
    for r, nm in zip(m['records'], m['names']):
        require(len(r) == 104 and u32(r, 48) == 4, 'draw record size/effect')
        nv, nt, fi = words(r, 60, '<III')
        count = u32(r, 92)
        require(nv > 0 and fi == first and count == nt * 3 > 0 and count <= len(ix) - fi, 'draw index range')
        require(all(t < len(m['textures']) for t in r[24:29]) and
                (r[29] == 255 or r[29] < len(m['lights'])), 'draw resource indices')
        q = ix[fi:fi + count]
        require(q.min() >= base and q.max() < base + nv <= len(v), 'draw vertex range')
        require(b'\0' in nm and len(nm) <= 256, 'material name')
        first += count
        base += nv
    require(first == len(ix) and base == len(v), 'draws do not cover mesh')
    markers = marker_records(m)
    # Source marker arrays can be present with a zero cached header count.
    # Assembly rewrites that count from the checked 80-byte records.
    require(len(markers) < 256 and (source or len(markers) == h[27]), 'marker count')
    for record in markers:
        require(np.isfinite(np.frombuffer(span(record, 16, 64), '<f4')).all(), 'nonfinite marker transform')
    return m


def validate_geometry(data, carbon=False, *, input_pack=False):
    end = u32(data, 4) + 8
    require(u32(data) == 0x80134000 and 8 < end <= len(data), 'geometry root range')
    for cid, off, size in children(data, end):
        if cid == 0x135200 and not carbon:
            r = span(data, off + 8, size)
            require(size == 168 and u32(r, 8) == 3 and
                    u32(r, 12) == name_hash('CARBON_MAGMATTE'), 'unexpected light material')
            info, _ = find(data, 0x134002)
            require(struct.unpack_from('<II', data, info + 8 + 0x68) ==
                    (end, len(data) - end), 'material streaming range')
        else:
            require(cid == 0 and not any(span(data, off + 8, size)), 'unexpected geometry trailer')
    result = {}
    for hs, solid in solids(data):
        require(u32(solid) == 0x80134010 and u32(solid, 4) + 8 == len(solid), 'indexed solid envelope')
        list(walk(solid))
        h = payload(solid, 0x134011, 16)
        span(h, 0, 164)
        name = text_at(h, 160)
        require(name and u32(h, 16) == hs == name_hash(name), 'indexed solid name/hash')
        for cid, count in ((0x134012, h[25]), (0x134013, h[26])):
            require(len(payload(solid, cid)) == count * 8, 'solid resource table size')
        m = model(solid) if carbon else native_wheel_model(solid)
        if not carbon:
            m['extras'] = [(cid, span(solid, (off + 23) & ~15, off + 8 + size - ((off + 23) & ~15))
                                  if cid == 0x13401a else span(solid, off + 8, size))
                           for cid, off, size in children(solid, 8)
                           if cid not in (0x134011, 0x134012, 0x134013, 0x80134100)]
        validate_model(m, source=carbon or input_pack)
        require(name not in result, 'duplicate model name')
        result[name] = m
    return result


def validate_texture(r, pl, pixels, version, hs):
    require(len(r) >= (89 if version == 8 else 124) and len(pl) == (24 if version == 8 else 32), 'texture tail size')
    if version == 8:
        nl = r[88]
        require(0 < nl <= len(r) - 89 and r[89 + nl - 1] == 0, 'texture name range')
        text_at(r, 89, 89 + nl)
        hoff, szoff, topoff, dimoff, logoff, fmtoff, poff = 12, 28, 36, 40, 44, 46, 12
    else:
        text_at(r, 12, 36)
        hoff, szoff, topoff, dimoff, logoff, fmtoff, poff = 36, 56, 64, 68, 72, 74, 20
    require(u32(r, hoff) == hs and u32(r, szoff) == len(pixels), 'texture hash/image size')
    width, height = words(r, dimoff, '<HH')
    require(0 < width <= 16384 and 0 < height <= 16384 and
            r[logoff] <= 14 and r[logoff + 1] <= 14 and
            width == 1 << r[logoff] and height == 1 << r[logoff + 1], 'texture dimensions')
    fmt = span(pl, poff, 4)
    require(fmt in (b'DXT1', b'DXT3', b'DXT5'), 'unsupported texture format')
    require(r[fmtoff] == {b'DXT1': 0x22, b'DXT3': 0x24, b'DXT5': 0x26}[fmt], 'texture format mismatch')
    expected = ((width + 3) // 4) * ((height + 3) // 4) * (8 if fmt == b'DXT1' else 16)
    require(u32(r, topoff) == expected == len(pixels), 'texture pixel range/top mip size')


def validate_textures(data, version):
    end = u32(data, 4) + 8
    require(8 < end <= len(data), 'texture pack root size')
    for cid, off, size in children(data, end):
        require(cid == 0 and not any(span(data, off + 8, size)), 'unexpected texture trailer')
    require(u32(payload(data, 0x33310001)) == version, 'texture pack version')
    entries = index(data, 0x33310003)
    table = payload(data, 0x33310002)
    require(len(table) == len(entries) * 8, 'texture hash table length')
    require([u32(table, i * 8) for i in range(len(entries))] == [e[0] for e in entries], 'texture hash table differs from index')
    logical, hashes = [], set()
    for hs, blob in texture_blobs(data):
        tail, plat = (148, 24) if version == 8 else (156, 32)
        require(len(blob) >= tail, 'truncated texture tail')
        r, pl, pixels = blob[-tail:-plat], blob[-plat:], blob[:-tail]
        validate_texture(r, pl, pixels, version, hs)
        if version == 5:
            logical.append((u32(r, 48), u32(r, 48) + len(pixels)))
        hashes.add(hs)
    logical.sort()
    require(all(a[1] <= b[0] for a, b in zip(logical, logical[1:])), 'logical texture overlap')
    return hashes


def unpack_file(data):
    if data[:4] in (b'JDLZ', b'HUFF', b'RAWW'):
        data = decompress(data)
    return unblock(data) if data[:4] == b'\x22\x11\x44\x55' else data


def texture_arrays(data, version):
    """Yield checked array records, platform records and pixel chunk extents."""
    for cid, root, size in walk(data):
        if cid != 0xB3300000:
            continue
        cs = list(walk(data, root + 8, root + 8 + size))
        get = lambda kind: [(o, z) for c, o, z in cs if c == kind]
        infos, platforms, pixels = get(0x33310004), get(0x33310005), get(0x33320002) + get(0x33320003)
        if not infos:
            continue
        require(len(infos) == len(platforms) == len(pixels) == 1, 'texture array chunks')
        off, length = infos[0]
        pp, pz = platforms[0]
        start, size = pixels[0]
        end, q = off + 8 + length, off + 8
        base = start + 8
        while base < start + 8 + size and data[base] == 0x11:
            base += 1
        base = max(start + 8, root + ((base - root) & ~127))
        k = 0
        while q < end:
            if version == 8:
                require(end - q >= 89, 'truncated global texture record')
                nl = data[q + 88]
                length = (89 + nl + 3) // 4 * 4
                require(nl > 0, 'global texture name length')
            else:
                length = 124
            require(length <= end - q, 'global texture record crosses chunk')
            stride = 24 if version == 8 else 32
            require((k + 1) * stride <= pz, 'global texture platform range')
            r = span(data, q, length)
            pl = span(data, pp + 8 + k * stride, stride)
            yield r, pl, base, start + 8 + size
            q += length
            k += 1
        require(k * (24 if version == 8 else 32) == pz, 'global platform count')


def global_extras(data, wanted):
    out = {}
    for r, pl, base, end in texture_arrays(data, 8):
        hs = u32(r, 12)
        if hs in wanted:
            off, top = u32(r, 20), u32(r, 36)
            require(base + off + top <= end, 'global texture pixels outside chunk')
            pixels = span(data, base + off, top)
            # Array records may contain lower mips; only the top mip is stored.
            r = bytearray(r)
            struct.pack_into('<I', r, 28, top)
            validate_texture(r, pl, pixels, 8, hs)
            require(hs not in out, 'duplicate shared texture')
            out[hs] = (bytes(r), pl, pixels)
    return out


def shared_resources(read, root):
    images, materials = set(), {0xD6D6080A, 0x22719FA9}
    for rel in ('GLOBAL/GLOBALA.BUN', 'GLOBAL/GLOBALB.LZC', 'GLOBAL/INGAMEA.BUN',
                'GLOBAL/INGAMEB.BUN', 'GLOBAL/DYNTEX.BIN', 'CARS/TEXTURES.BIN', 'CARS/WHEELS/TEXTURES.BIN'):
        path = root / rel
        if not path.is_file():
            continue
        raw = unpack_file(read(path))
        images.update(shared_texture_hashes(raw))
        for cid, off, size in walk(raw):
            if cid == 0x135200:
                require(size == 168, 'MW material row size')
                materials.add(u32(raw, off + 20))
    dynamic = {'DAMAGE0_N'}
    dynamic.update(f'WINDOW_{s}' for s in ('FRONT', 'REAR', 'LEFT_FRONT', 'LEFT_REAR', 'RIGHT_FRONT', 'RIGHT_REAR'))
    for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS'):
        dynamic.update(f'{kind}_{side}' for side in (('LEFT', 'RIGHT', 'CENTRE') if kind.startswith('BRAKE') else ('LEFT', 'RIGHT')))
    return images, materials, set(map(name_hash, dynamic))


def material_resources(source, source_tex, donor_tex, car, slot, shared_raw, shared_images, dynamic):
    aliases = slot_texture_aliases(source_tex, car, slot)
    local = {h for h, _ in texture_blobs(source_tex)}
    wanted = geometry_texture_hashes(source)
    for kit in range(6):
        hs = name_hash(f'{car}_KIT{kit:02d}_HEADLIGHT_GLASS_OFF')
        if hs in wanted and hs not in local:
            aliases[hs] = (name_hash('WINDOW_MASK'), 'WINDOW_MASK')
    for name in ('UNIVERSAL_TIRE_N', 'TREAD_N'):
        if name_hash(name) in wanted:
            aliases[name_hash(name)] = (name_hash('TIRE_STYLE01_N'), 'TIRE_STYLE01_N')
    for _, solid in solids(source):
        ts = [h for h, _ in struct.iter_unpack('<II', payload(solid, 0x134012))]
        rb = payload(solid, 0x134b02, 16)
        names = [solid[o + 8:o + 8 + z].split(b'\0')[0] for cid, o, z in walk(solid) if cid == 0x134c02]
        for at, role in enumerate(names):
            diffuse = ts[rb[at * 144 + 24]]
            fallback = {b'GRILL_STOCK': 'GRILL_02', b'FOGLIGHT_GLASS': 'WINDOW_FRONT'}.get(role)
            if fallback and diffuse not in local and diffuse not in shared_images:
                aliases[diffuse] = (name_hash(fallback), fallback)
    translated = {aliases.get(h, (h, None))[0] for h in wanted}
    present = {aliases.get(h, (h, None))[0] for h in local} | {h for h, _ in texture_blobs(donor_tex)}
    needed = translated - shared_images - dynamic - {name_hash(f'{slot}_{r}') for r in MW_PAINT_BINDINGS} - present
    extras = global_extras(shared_raw, needed | (wanted & set(map(name_hash, ('TIRE_STYLE01', 'TIRE_STYLE02')))) |
                           set(map(name_hash, ('TIRE_STYLE01_N', 'DOORLINE_N'))))
    # The shared grill name has different pixels in the two installations:
    # source DXT5 has holes; MW's same-hash DXT1 atlas is fully opaque.
    grill = name_hash('GRILL_02')
    if grill in wanted or any(aliases.get(h, (h, None))[0] == grill for h in wanted):
        require(name_hash(car + '_ENGINE') not in local, 'grill streaming slot occupied')
        sourced = global_extras(shared_raw, {grill})
        require(grill in sourced, 'source grill pixels absent')
        extras.update(sourced)
        target = (name_hash(slot + '_ENGINE'), slot + '_ENGINE')
        for h in wanted:
            if aliases.get(h, (h, None))[0] == grill: aliases[h] = target
        aliases[grill] = target
    # Same-hash MW carbon fibre is DXT1 and loses the source alpha atlas.
    # DRIVER is a requested car image slot; refuse to displace authored art.
    fibre = name_hash('CARBONFIBRE')
    if fibre in wanted:
        target_name = slot + '_DRIVER'
        target_hash = name_hash(target_name)
        occupied = {aliases.get(h, (h, None))[0] for h in local | wanted}
        occupied.update(h for h, _ in texture_blobs(donor_tex))
        require(name_hash(car + '_DRIVER') not in local | wanted and
                target_hash not in occupied, 'carbon fibre streaming slot occupied')
        sourced = global_extras(shared_raw, {fibre})
        require(fibre in sourced, 'source carbon fibre pixels absent')
        extras.update(sourced)
        aliases[fibre] = (target_hash, target_name)
    aliases.update(normal_resource_aliases(slot))
    return aliases, extras


def isolate(models, reference, donor, identity, image_map):
    prefix = 'NFSAD_' + identity
    images, renamed, mapping = dict(image_map), {}, []
    for name, original in models.items():
        require(name.startswith(donor + '_'), 'foreign solid prefix')
        m = dict(original)
        target = prefix + name[len(donor):]
        hs = name_hash(target)
        if target in renamed or hs in {b for _, b in mapping}:
            raise NamespaceCollisionError('solid namespace collision')
        h = bytearray(m['header'][:160])
        struct.pack_into('<I', h, 16, hs)
        rawname = target.encode('ascii') + b'\0'
        h += rawname + bytes(-(160 + len(rawname)) % 4)
        m.update(name=target, header=bytes(h), textures=[images.get(v, v) for v in m['textures']])
        renamed[target] = m
        mapping.append((name_hash(name), hs))
    covered = {a for a, _ in mapping}
    for name in reference:
        require(name.startswith(donor + '_'), 'foreign donor prefix')
        old = name_hash(name)
        if old not in covered:
            mapping.append((old, name_hash(prefix + name[len(donor):])))
    mapping += list(images.items())
    if len({a for a, _ in mapping}) != len(mapping) or len({b for _, b in mapping}) != len(mapping):
        raise NamespaceCollisionError('solid/image namespace collision')
    if any(new in {old for old, _ in mapping} for _, new in mapping):
        raise NamespaceCollisionError('namespace overlaps donor resources')
    mapping.sort()
    sidecar = struct.pack('<4I', 0x31504D53, 2 if images else 1, name_hash(identity), len(mapping))
    sidecar += b''.join(struct.pack('<II', *row) for row in mapping)
    return renamed, sidecar


def check_unchanged(hashes):
    for path, expected in hashes.items():
        if not path.is_file() or file_hash(path) != expected:
            raise InputChangedError('input hash changed during conversion: ' + path.name)


def file_hash(path):
    digest = hashlib.sha256()
    require(path.stat().st_size <= MAX_FILE, 'input file exceeds hash limit')
    total = 0
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1 << 20), b''):
            total += len(data)
            require(total <= MAX_FILE, 'input grew beyond hash limit')
            digest.update(data)
    return digest.hexdigest()


def convert(car_dir, mw_donor_dir, shared, out_dir):
    """Convert a car; return counts, exact input/output hashes and material policy.

    Paths are local. ``shared`` is the source GLOBAL directory or a mapping of
    dependency paths. All outputs are prepared and audited before writing.
    Callers must separately approve the returned output hashes for activation.
    """
    try:
        car_dir, mw_donor_dir, out_dir = (Path(p).expanduser().resolve() for p in (car_dir, mw_donor_dir, out_dir))
        car, donor = car_dir.name.upper(), mw_donor_dir.name.upper()
        require(all(re.fullmatch(r'[A-Z0-9_]{1,15}', n) for n in (car, donor)), 'invalid car or donor identity')
        if isinstance(shared, Mapping):
            require('carbon_global' in shared, 'shared mapping needs carbon_global')
            global_path = Path(shared['carbon_global']).resolve()
            attributes = Path(shared.get('attributes', global_path.parent / 'attributes.bin')).resolve()
            mw_root = Path(shared.get('mw_root', mw_donor_dir.parent.parent)).resolve()
        else:
            global_dir = Path(shared).expanduser().resolve()
            global_path, attributes, mw_root = global_dir / 'GLOBALB.LZC', global_dir / 'attributes.bin', mw_donor_dir.parent.parent
        protected = (car_dir.parent.parent if car_dir.parent.name.casefold() == 'cars' else car_dir,
                     mw_root, car_dir, mw_donor_dir, global_path.parent, attributes.parent)
        check_output_path(out_dir, protected)
        # Output ancestors can replace an input directory just as descendants can.
        require(not any(str(root).casefold().startswith(str(out_dir).casefold().rstrip(os.sep) + os.sep)
                        for root in protected), 'output contains protected input tree')
        hashes, buffers = {}, {}
        def read(path):
            nonlocal protected
            path = path.resolve()
            # Symlinked inputs protect their actual containing directories too.
            protected += (path.parent,)
            check_output_path(out_dir, protected)
            if path not in buffers:
                require(path.stat().st_size <= MAX_FILE, 'input file exceeds limit')
                before = file_hash(path)
                with path.open('rb') as stream:
                    data = stream.read(MAX_FILE + 1)
                require(len(data) <= MAX_FILE, 'input file exceeds limit')
                if hashlib.sha256(data).hexdigest() != before:
                    raise InputChangedError('input changed while reading')
                hashes[path], buffers[path] = before, data
            return buffers[path]
        source, source_tex = (read(car_dir / n) for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))
        mw, mw_tex = (read(mw_donor_dir / n) for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))
        validate_geometry(source, carbon=True)
        refs = validate_geometry(mw, input_pack=True)
        validate_textures(source_tex, 8)
        validate_textures(mw_tex, 5)
        shared_raw = unpack_file(read(global_path))
        images, materials, dynamic = shared_resources(read, mw_root)
        aliases, extras = material_resources(source, source_tex, mw_tex, car, donor, shared_raw, images, dynamic)
        aliases, extras = supply_absent_lamp_images(source_tex, car, donor, aliases, extras)
        models, assembled = converted_models(source, mw, donor if car != donor else None, aliases,
                                             material_role_map(shared_raw))
        stock = [f'{donor}_BASE_{lod}' for lod in 'ABCD'] + [f'{donor}_KIT00_BODY_{lod}' for lod in 'ABCD']
        stock += [f'{donor}_KIT00_{end}_BRAKE_A' for end in ('FRONT', 'REAR')]
        missing = sorted(set(stock) - models.keys())
        if missing:
            raise MissingStockPartError('missing stock parts: ' + ', '.join(missing))
        # The plate writer supplies only donor geometry at source marker mounts.
        geometry, stats = write_geometry(source, models, len(assembled), plate_reference=mw)
        models = validate_geometry(geometry)
        renames = []
        textures, nt = convert_textures(source_tex, mw_tex, extras, aliases, source_sampler_state=True,
                                        namespace=(donor, 'NFSAD_' + car), renames=renames)
        available = validate_textures(textures, 5) | images | dynamic
        available |= {name_hash(f'{donor}_{r}') for r in MW_PAINT_BINDINGS}
        models, solidmap = isolate(models, refs, donor, car, renames)
        used = {h for m in models.values() for h in m['lights']}
        rim_rows = {}
        if name_hash('CARBON_MAGMATTE') in used:
            rim_rows = source_rim_rows(shared_raw, unpack_file(read(mw_root / 'GLOBAL/GLOBALB.LZC')))
            require(name_hash('CARBON_MAGMATTE') in rim_rows, 'source rim material absent')
        materials |= rim_rows.keys()
        for m in models.values():
            validate_model(m)
            require(set(m['textures']) <= available, 'unresolved geometry texture')
            require(set(m['lights']) <= materials, 'unresolved MW material')
        geometry, _ = write_geometry(geometry, models)
        geometry = append_material_rows(geometry, rim_rows)
        validate_geometry(geometry)
        wheels = wheel_sidecar(read(attributes), car, donor, fit=True)
        files = {'GEOMETRY.BIN': geometry, 'TEXTURES.BIN': textures, 'SOLIDMAP.BIN': solidmap, 'WHEELFIT.BIN': wheels}
        from .car_paint import metadata
        files['PAINT.BIN'], paint = metadata(shared_raw,
            lambda: unpack_file(read(mw_root / 'GLOBAL/GLOBALB.LZC')), car, donor, read(attributes))
        collision = collision_sidecar(shared_raw, car, donor)
        if collision is not None: files['COLLISION.BIN'] = collision
        check_unchanged(hashes)
        write_outputs(out_dir, files, protected)
        check_unchanged(hashes)
    except ConversionError:
        raise
    except (OSError, ValueError, KeyError, IndexError, StopIteration, struct.error, UnicodeError, np.linalg.LinAlgError) as exc:
        raise FormatError('invalid conversion input: ' + str(exc)) from exc
    return dict(car=car, donor=donor, namespace='NFSAD_' + car, converter_revision=1,
                default_paint=paint,
                solids=stats['solids'], assemblies=stats['assemblies'], textures=nt,
                inputs={str(p): h for p, h in sorted(hashes.items())},
                outputs={n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
                material_policy='MW roles; lamp parameters excluded; classified neutral vertex illumination')


def supply_absent_lamp_images(textures, source, slot, aliases, extras):
    (aliases, extras) = (dict(aliases), dict(extras))
    images = {h: (b[-148:-24], b[-24:], b[:-148]) for (h, b) in texture_blobs(textures)}
    for kit in range(6):
        for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS'):
            off = f'{source}_KIT{kit:02d}_{kind}_OFF'
            on = f'{source}_KIT{kit:02d}_{kind}_ON'
            if name_hash(off) in images or name_hash(on) not in images:
                continue
            (r, platform, pixels) = images[name_hash(on)]
            r = bytearray(r[:89])
            raw = off.encode() + b'\x00'
            r[88] = len(raw)
            struct.pack_into('<I', r, 12, name_hash(off))
            extras[name_hash(off)] = (bytes(r) + raw, platform, pixels)
            target = f'{slot}_KIT{kit:02d}_{kind}_OFF'
            aliases[name_hash(off)] = (name_hash(target), target)
    return (aliases, extras)


def shared_texture_hashes(data):
    images = {u32(r, 36) for r, _, _, _ in texture_arrays(data, 5)}
    for cid, root, size in walk(data):
        if cid != 0xB3300000:
            continue
        cs = list(walk(data, root + 8, root + 8 + size))
        tables = [(o, z) for c, o, z in cs if c == 0x33310003]
        pixels = [(o, z) for c, o, z in cs if c in (0x33320002, 0x33320003)]
        if not tables:
            continue
        require(len(tables) == len(pixels) == 1, 'shared streaming texture chunks')
        off, length = tables[0]
        require(length % 24 == 0 and length // 24 <= 65536, 'shared texture index stride')
        entries = list(struct.iter_unpack('<6I', span(data, off + 8, length)))
        require(sum(e[3] for e in entries) <= MAX_FILE, 'shared texture expansion limit')
        declared = {e[0] for e in entries}
        start, size = pixels[0]
        start, end = start + 8, start + 8 + size
        consumed = start
        for match in re.compile(b'JDLZ\x02\x10|HUFF\x01\x10').finditer(data, start, end):
            at = match.start()
            if at < consumed:
                continue
            require(end - at >= 16, 'truncated shared compression header')
            stored = u32(data, at + 12) + (16 if data[at:at + 4] == b'HUFF' else 0)
            require(16 <= stored <= end - at, 'shared compressed texture range')
            blob = decompress(span(data, at, stored))
            require(len(blob) >= 156, 'shared texture tail size')
            hs = u32(blob, len(blob) - 120)
            require(hs in declared, 'shared texture hash missing from index')
            images.add(hs)
            consumed = at + stored
    return images
