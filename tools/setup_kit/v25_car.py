"""Decode ProStreet PC list v29 solids and read Most Wanted material templates."""
import struct

import numpy as np

from . import car_convert as mw
from .car_convert import require, u32, payload, children, find, name_hash


# Deliberate static approximations for source lighting absent from MW Ford GT.
LIGHT_FALLBACK = {name_hash('CHASSIS'): name_hash('BOTTOM'),
                  name_hash('ENGINE'): name_hash('INTERIOR'),
                  0x7EB8F611: name_hash('BRAKELIGHTGLASS')}

PS_DAMAGE_NORMAL = name_hash('DAMAGE_N')


def normal_for_mw(draw, material_name):
    """Respect clean paint's source gate instead of treating damage as static detail.

    The source vertex writer stores 0 for DAMAGE_N and 1 for clean paint
    in the short at +06 of each 32-byte vertex.
    Our static MW effect has no corresponding damage scalar. Native MW BODY
    slots all repeat the paint texture, while badge/tyre/detail use slot 1.
    The map itself is already signed-RGB DXT1, so swizzling cannot fix this role.
    """
    normal = draw['normal']
    if normal is not None and material_name.split(b'\0')[0] in mw.PAINT_NAMES:
        require(normal == PS_DAMAGE_NORMAL and u32(draw['source_record'], 40) & 0x10000,
                'PS paint normal gate not decoded')
        return None
    return normal


def source_model(s):
    """Decode and range-check PS fields; no MW material assumptions here."""
    require(u32(s) == 0x80134010 and u32(s, 4) + 8 == len(s), 'PS solid envelope')
    h = payload(s, 0x134011, 16)
    require(len(h) >= 188 and struct.unpack_from('<H', h, 12)[0] == 25, 'PS v25 solid required')
    require(not any(h[160:184]), 'PS unknown solid extension')
    tail = h[184:]; require(b'\0' in tail, 'PS name terminator')
    name = tail.split(b'\0')[0].decode('ascii')
    require(len(h) == (184 + len(name) + 4) // 4 * 4 and u32(h, 16) == name_hash(name), 'PS name/hash/size')
    tx = payload(s, 0x134012); ls = payload(s, 0x134013)
    require(len(tx) == h[25] * 8 and len(ls) == h[26] * 8, 'PS texture/light references')
    textures = [a for a, _ in struct.iter_unpack('<II', tx)]
    lights = [a for a, _ in struct.iter_unpack('<II', ls)]
    slots = payload(s, 0x134015); ns = u32(slots)
    require(0 < ns <= 16 and len(slots) >= 4 + ns * 4, 'PS shader slots')
    labels = []
    for i in range(ns):
        at = 4 + ns * 4 + u32(slots, 4 + i * 4)
        require(at < len(slots) and b'\0' in slots[at:], 'PS shader string offset')
        labels.append(slots[at:].split(b'\0')[0].decode('ascii'))
    require(set(labels) <= {'DIFFUSE', 'NORMALMAP', 'AMBIENT'}, 'unsupported PS binding role')
    mh = payload(s, 0x134900, 16)
    require(len(mh) == 56, 'PS mesh header must be 56 aligned bytes')
    n = u32(mh, 16); rb = payload(s, 0x134B02, 128)
    require(n > 0 and len(rb) == n * 128, 'PS material record count/size')
    mo, mz = find(s, 0x80134100)
    vbs = [s[(o + 135) & ~127:o + 8 + z] for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134B01]
    matnames = [s[o + 8:o + 8 + z] for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134C02]
    require(len(vbs) == n == len(matnames) and not any(any(x) for x in matnames), 'PS per-draw buffers/empty names')
    ib = payload(s, 0x134B03, 32); ix = np.frombuffer(ib[:len(ib) // 2 * 2], '<u2')
    draws, cursor, nverts, normal_textures = [], 0, 0, set()
    for i, vb in enumerate(vbs):
        r = rb[i * 128:(i + 1) * 128]
        first, count, size, descriptor = [u32(r, p) for p in (0, 44, 76, 80)]
        require(descriptor == 0x80200000 and len(vb) == size and size % 32 == 0 and size > 0, 'PS vertex descriptor/bytes')
        nv = size // 32
        bindings = struct.unpack_from('<5i', r, 8); nb = r[33]
        require(0 < nb <= 5 and all(0 <= x < ns for x in bindings[:nb]) and all(x == -1 for x in bindings[nb:]), 'PS binding slots')
        require(all(x < len(textures) for x in r[28:28 + nb]), 'PS binding texture range')
        li = u32(r, 48); require(li < len(lights), 'PS light index')
        require(first == cursor and count > 0 and count % 3 == 0 and first + count <= len(ix), 'PS draw index span')
        q = ix[first:first + count].astype(np.uint32)
        require(q.max() < nv, 'PS local draw index range')
        shorts = np.frombuffer(vb, '<i2').reshape(nv, 16)
        normed = np.maximum(shorts.astype(np.float32) / 32767., -1.)
        v = np.zeros((nv, 36), np.uint8); f = v.view('<f4').reshape(nv, 9)
        f[:, :3] = normed[:, :3] * 10.; f[:, 3:6] = normed[:, 8:11]
        lengths = np.linalg.norm(f[:, 3:6], axis=1)
        require(np.max(np.abs(lengths - 1)) < .004, 'PS normal length')
        f[:, 3:6] /= lengths[:, None]
        v[:, 24:28] = np.frombuffer(vb, np.uint8).reshape(nv, 32)[:, 12:16]
        f[:, 7:9] = normed[:, 4:6]
        lo, hi = (np.array(struct.unpack_from('<3f', h, p)) for p in (32, 48))
        require((f[:, :3] >= lo - .002).all() and (f[:, :3] <= hi + .002).all(), 'PS positions outside bounds')
        diffuse = [r[28 + j] for j in range(nb) if labels[bindings[j]] == 'DIFFUSE']
        normals = [textures[r[28 + j]] for j in range(nb) if labels[bindings[j]] == 'NORMALMAP']
        require(len(normals) <= 1, 'PS at most one normal binding required')
        normal_textures.update(normals)
        require(len(diffuse) == 1, 'PS one diffuse binding required')
        draws.append(dict(vertices=v, indices=q, texture=textures[diffuse[0]],
                          normal=normals[0] if normals else None, light=lights[li], source_record=r))
        cursor += count; nverts += nv
    require(len(ib) == (cursor * 2 + 3) // 4 * 4 and not any(ib[cursor * 2:]), 'PS index buffer rounding')
    require(u32(mh, 48) == nverts, 'PS mesh vertex total')
    # Three wide-kit source headers have stale triangle totals; draws are authoritative.
    markers = []
    for cid, o, z in children(s, 8):
        if cid == 0x13401A:
            raw = s[(o + 23) & ~15:o + 8 + z]
            require(len(raw) % 80 == 0, 'PS 80-byte markers')
            for at in range(0, len(raw), 80):
                marker = raw[at:at + 80]
                require(np.isfinite(np.frombuffer(marker[16:], '<f4')).all(), 'PS finite marker matrix')
                markers.append(marker)
    return dict(name=name, header=h, mesh=mh, draws=draws, markers=markers,
                normal_textures=normal_textures,
                stale_triangles=(u32(mh, 40) * 3 != cursor or u32(h, 20) * 3 != cursor))


def reference_models(d):
    result = {}
    for _, s in mw.solids(d):
        h = payload(s, 0x134011, 16); name = h[160:].split(b'\0')[0].decode()
        require(struct.unpack_from('<H', h, 12)[0] == 22 and len(payload(s, 0x134900, 16)) == 48, 'MW v22/48-byte template required')
        rb = payload(s, 0x134B02, 16); mo, mz = find(s, 0x80134100)
        names = [s[o + 8:o + 8 + z] for cid, o, z in children(s, mo + 8, mo + 8 + mz) if cid == 0x134C02]
        records = [rb[i:i + 104] for i in range(0, len(rb), 104)]
        require(len(records) == len(names), 'MW template names')
        lights = [h for h, _ in struct.iter_unpack('<II', payload(s, 0x134013))]
        textures = [h for h, _ in struct.iter_unpack('<II', payload(s, 0x134012))]
        result[name] = dict(header=h, mesh=payload(s, 0x134900, 16), records=records, names=names, lights=lights, textures=textures,
                            extras=[(0x13401A, payload(s, 0x13401A, 16))] if any(c == 0x13401A for c, _, _ in children(s, 8)) else [])
    return result


def templates(refs):
    result = {}
    for m in refs.values():
        for r, nm in zip(m['records'], m['names']):
            require(r[29] < len(m['lights']), 'MW light template')
            lh = m['lights'][r[29]]
            # Prefer the canonical paint role to a region-specific name.
            if lh not in result or nm.split(b'\0')[0] == b'BODY':
                result[lh] = (r, nm, [m['textures'][i] for i in r[24:29]])
    return result


def texture_slots(srcname, draw, material_name, defaults, source_textures):
    """Use the native replacement role for an authored brake OFF/ON pair.

    MW UpdateLightStateTextures replaces BRAKELIGHT_LEFT/RIGHT/CENTRE;
    a concrete atlas hash bypasses that per-side state selection.
    """
    diffuse = draw['texture']
    if draw['light'] == name_hash('BRAKELIGHT'):
        car, part = srcname.rsplit('_KIT', 1)
        kit, role = part.split('_', 1)
        base = f'{car}_KIT{kit}_BRAKELIGHT'
        pair = {name_hash(base + '_OFF'), name_hash(base + '_ON')}
        if diffuse in pair and all(h in source_textures for h in pair):
            side = role.rsplit('_', 1)[0].rsplit('_', 1)[-1]
            side = side if side in ('LEFT', 'RIGHT') else 'CENTRE'
            defaults = [name_hash('BRAKELIGHT_' + side)] * 5
        elif diffuse in source_textures:
            defaults = [diffuse] * 5
    elif diffuse in source_textures:
        defaults = [diffuse] * 5
    return mw.car_texture_slots(defaults, normal=normal_for_mw(draw, material_name))


def target_model(src, name, selected, table, source_textures):
    draws = [src['draws'][i] for i in selected]
    require(draws, 'empty MW model')
    textures, lights, records, names, vs, ix = [], [], [], [], [], []
    first, base = 0, 0
    for draw in draws:
        lh = LIGHT_FALLBACK.get(draw['light'], draw['light'])
        require(lh in table, f'PS light {draw["light"]:08X} lacks explicit MW mapping in {src["name"]}')
        raw, nm, defaults = table[lh]
        bindings = texture_slots(src['name'], draw, nm, defaults, source_textures)
        for val in bindings:
            if val not in textures: textures.append(val)
        if lh not in lights: lights.append(lh)
        r = bytearray(104); r[:60] = raw[:60]
        v = draw['vertices']; p = v.view('<f4').reshape(-1, 9)[:, :3]
        struct.pack_into('<6f', r, 0, *p.min(0), *p.max(0))
        r[24:29] = bytes(textures.index(hs) for hs in bindings); r[29] = lights.index(lh)
        struct.pack_into('<I', r, 48, 4)
        struct.pack_into('<III', r, 60, len(v), len(draw['indices']) // 3, first)
        struct.pack_into('<I', r, 92, len(draw['indices']))
        records.append(bytes(r)); names.append(nm); vs.append(v); ix.append(draw['indices'] + base)
        first += len(draw['indices']); base += len(v)
    require(base < 65536 and len(textures) < 256 and len(lights) < 256, 'MW model limits')
    h = bytearray(src['header'][:160]); struct.pack_into('<H', h, 12, 22)
    # PS's authoring scene lays LODs out every 2.2 units on Y. Positions,
    # bounds and socket translations are already in car-local coordinates.
    matrix = np.frombuffer(h[64:128], '<f4').reshape(4, 4)
    expected = np.eye(4, dtype='<f4'); expected[3, 1] = 'ABCDE'.index(src['name'][-1]) * 2.2
    require(np.allclose(matrix, expected, atol=1e-6, rtol=0), 'unsupported PS stock pivot')
    h[64:128] = np.eye(4, dtype='<f4').tobytes()
    # Fixed header runtime pointers; transform and source flags remain on-disk metadata.
    h[:12] = bytes(12); h[128:152] = bytes(24)
    struct.pack_into('<II', h, 16, name_hash(name), first // 3); h[25:28] = bytes([len(textures), len(lights), 0])
    verts = np.concatenate(vs); xyz = verts.view('<f4').reshape(-1, 9)[:, :3]
    struct.pack_into('<3f', h, 32, *xyz.min(0)); struct.pack_into('<3f', h, 48, *xyz.max(0))
    tail = name.encode() + b'\0'; h += tail + bytes(-(160 + len(tail)) % 4)
    mh = bytearray(48); struct.pack_into('<I', mh, 16, len(records)); struct.pack_into('<I', mh, 24, 1); struct.pack_into('<I', mh, 44, first)
    m = dict(name=name, header=bytes(h), mesh=bytes(mh), textures=textures, lights=lights,
             records=records, names=names, vertices=verts, indices=np.concatenate(ix), groups=len(draws), extras=[])
    mw.set_markers(m, src['markers'])
    return m


# Ford GT stock manifest only. No wind-tunnel, T0 morph, wide kit or damage aliases.
ALIASES = {'FRONT_BRAKE': 'BRAKE_FRONT', 'REAR_BRAKE': 'BRAKE_REAR',
           'FRONT_LEFT_WINDOW': 'WINDOW_FRONT_LEFT', 'FRONT_RIGHT_WINDOW': 'WINDOW_FRONT_RIGHT',
           'FRONT_WINDOW': 'WINDOW_FRONT', 'REAR_WINDOW': 'WINDOW_REAR',
           'LEFT_SIDE_MIRROR': 'SIDEMIRROR_LEFT', 'RIGHT_SIDE_MIRROR': 'SIDEMIRROR_RIGHT',
           'LEFT_HEADLIGHT': 'HEADLIGHT_LEFT', 'RIGHT_HEADLIGHT': 'HEADLIGHT_RIGHT',
           'LEFT_HEADLIGHT_GLASS': 'HEADLIGHT_GLASS_LEFT', 'RIGHT_HEADLIGHT_GLASS': 'HEADLIGHT_GLASS_RIGHT',
           'LEFT_BRAKELIGHT': 'BRAKELIGHT_LEFT', 'RIGHT_BRAKELIGHT': 'BRAKELIGHT_RIGHT',
           'LEFT_BRAKELIGHT_GLASS': 'BRAKELIGHT_GLASS_LEFT', 'RIGHT_BRAKELIGHT_GLASS': 'BRAKELIGHT_GLASS_RIGHT',
           'FRONT_TIRE': 'WHEEL_TIRE_FRONT', 'SPOILER': 'SPOILER_LIP'}
BODY_PARTS = ('BODY', 'BUMPER_FRONT', 'BUMPER_REAR', 'BUMPER_REAR_EXTRA', 'DOOR_LEFT', 'DOOR_RIGHT',
              'FENDER_FRONT_LEFT', 'FENDER_FRONT_RIGHT', 'SKIRT_LEFT', 'SKIRT_RIGHT', 'TRUNK', 'EXHAUST')


def triangle_components(draw):
    """Return whole-triangle components, welding exact source positions only.

    A material can contain disconnected lamp cones or roof/bumper trim. Vertex
    indices alone miss the welds at UV/normal seams. Do not round positions or
    join nearby surfaces: this decoder is evidence, never a geometry repair.
    """
    triangles = draw['indices'].reshape(-1, 3)
    xyz = draw['vertices'].view('<f4').reshape(-1, 9)[:, :3]
    parent = list(range(len(triangles)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    seen = {}
    for i, tri in enumerate(triangles):
        for vertex in tri:
            position = xyz[vertex].copy()
            position[position == 0] = 0  # +0/-0 are the same geometric weld.
            key = position.tobytes()
            if key in seen: parent[root(i)] = root(seen[key])
            else: seen[key] = i
    groups = {}
    for i in range(len(triangles)): groups.setdefault(root(i), []).append(i)
    return [np.asarray(group, dtype=np.intp) for group in groups.values()]
