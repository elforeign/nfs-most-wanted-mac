"""Assemble ProStreet stock models in the Most Wanted car format."""
import re
import struct

import numpy as np

from . import car_convert as c, v25_car as v25

# Both packs carry generic plate markers on bodies that shipped without road plates.
UNPLATED_BODIES = {'DBR9', 'GTRPROTO'}
STOCK_ROLE = re.compile(r'^(?:BASE|BODY|BUMPER_(?:FRONT|REAR)(?:_EXTRA)?|DOOR_(?:REAR_)?(?:LEFT|RIGHT)|FENDER_FRONT_LEFT|FENDER_FRONT_RIGHT|SKIRT_LEFT|SKIRT_RIGHT|TRUNK|EXHAUST|HOOD|ENGINE|SEAT_LEFT|SEAT_RIGHT|DRIVER|BRAKE_FRONT|BRAKE_REAR|BRAKEROTOR_FRONT|BRAKEROTOR_REAR|WHEEL_TIRE_FRONT|WHEEL_TIRE_REAR|SPOILER|SPOILER_LIP|UNIVERSAL_SPOILER_BASE|SIDEMIRROR_(?:LEFT|RIGHT)|WINDOW_.+|HEADLIGHT_.+|BRAKELIGHT_.+|REVERSE_.+)$')
# Names verified in PS GLOBAL/GLOBALB.BUN 135200 records (hash +0C,
# name +14). Explicit static approximations, like v25_car.LIGHT_FALLBACK.
LIGHT_ALIASES = {0x56681DDE: c.name_hash('HOSES'),  # DAMAGE
                 0xCC772AFB: 0xDCD01662,          # DOORLINE
                 0x721AFDDC: 0xB57AB98E,          # CARBONFIBER
                 c.name_hash('MAGLIP'): c.name_hash('CHROME'),
                 c.name_hash('GRILLCHROME'): c.name_hash('CHROME'),
                 c.name_hash('MAGMATTE'): c.name_hash('MAGSILVER'),
                 c.name_hash('HEADLIGHTCHROME'): c.name_hash('CHROME'),
                 c.name_hash('REGPAINTBLACK'): c.name_hash('DULLPLASTIC')}
# MW has no brake rotor part: its FRONT/REAR_BRAKE solid draws caliper and disc
# (part names 008b4054/008b3fa0). A rotor solid of its own is never requested, so
# the disc joins the brake solid of its LOD, as v25_car does for its slots.
ROTOR_ROLES = {'BRAKEROTOR_FRONT': 'FRONT_BRAKE', 'BRAKEROTOR_REAR': 'REAR_BRAKE'}
# Factory BRAKEDISC draws use these atlases, including embedded/low-LOD discs.
# ProStreet keeps them in InGameA; Undercover's adapter stages its GlobalB.
ROTOR_IMAGES = frozenset(c.name_hash('ROTOR_' + style) for style in ('00', '01', '02', '05'))
# MW reads only the wheel marker's Y offset (0075F5D0), under its own part name.
BRAKE_SOCKET_ROLES = {'WHEEL_TIRE_FRONT': 'FRONT_BRAKE', 'WHEEL_TIRE_REAR': 'REAR_BRAKE'}
# A stock tyre draw binds one of these GLOBALB images, never a car-pack one.
TYRE_IMAGES = frozenset(c.name_hash(n) for n in ('TIRE_STYLE01', 'TIRE_STYLE02'))
# The metal palette supplies both wheel lips and body trim.
METAL_SWATCH = c.name_hash('METAL_SWATCH')
METAL_ROLES = frozenset(c.name_hash(n) for n in ('MAGLIP', 'CHROME', 'GRILLCHROME', 'ALUMINUM'))
HEADLIGHT_CHROME = c.name_hash('HEADLIGHTCHROME')
CHROME = c.name_hash('CHROME')
REAR_REFLECTOR_NAME = 'PS_REAR_REFLECTOR'
REAR_REFLECTOR = c.name_hash(REAR_REFLECTOR_NAME)


def stock_name(name, car):
    prefix = car + '_KIT00_'
    if not name.startswith(prefix) or name[-2:] not in ('_A', '_B', '_C', '_D', '_E'): return False
    role = name[len(prefix):-2]
    return not re.search(r'(?:^|_)(?:T[0-9]+|DAMAGE|DECAL|WINDTUNNEL|DRAG|EVO|ROLL_CAGE)(?:_|$)', role)


def decode_sources(raw, car):
    c.require(c.u32(raw) == 0x80134000 and c.u32(raw, 4) + 8 == len(raw), 'PS list envelope')
    offset, size = c.pack_header(raw, 0x80134001)
    info = c.payload(raw[offset:offset + 8 + size], 0x134002)
    c.require(len(info) == 144 and c.u32(info, 8) == 29, 'PS list version 29 required')
    entries = c.index(raw); hashes = [e[0] for e in entries]
    c.require(c.u32(info, 12) == len(entries) and hashes == sorted(set(hashes)), 'PS index count/order')
    sources = {}
    for e in entries:
        context = f'hash={e[0]:08X} offset={e[1]:#x} stored={e[2]} unpacked={e[3]} flags={e[4]:#x}'
        try:
            s = c.unpack_entry(raw, e)
            h = c.payload(s, 0x134011, 16)
            name = h[184:].split(b'\0')[0].decode('ascii')
            context += f' solid={name}'
            c.require(e[0] == c.name_hash(name) and name.startswith(car + '_'), 'PS hash/name/prefix')
            if not stock_name(name, car): continue
            model = v25.source_model(s)
            c.require(e[0] == c.name_hash(name) and name not in sources, 'PS hash/name uniqueness')
            c.require(name.startswith(car + '_'), f'foreign car prefix {name}')
            sources[name] = model
        except (ValueError, AssertionError, IndexError, KeyError, StopIteration, struct.error) as exc:
            raise c.FormatError(f'{context}: {type(exc).__name__}: {exc or "required chunk absent"}') from exc
    return sources


def stock_sources(sources, car):
    selected = {}
    prefix = car + '_KIT00_'
    for name, src in sources.items():
        if not stock_name(name, car): continue
        role, lod = name[len(prefix):].rsplit('_', 1)
        if re.search(r'(?:^|_)(?:T[0-9]+|DAMAGE|DECAL|WINDTUNNEL|DRAG|EVO|ROLL_CAGE)(?:_|$)', role): continue
        c.require(STOCK_ROLE.fullmatch(role), f'unclassified stock role {name}')
        selected[name] = src
    for role in ('BODY', 'BASE', 'HOOD', 'WHEEL_TIRE_FRONT', 'WHEEL_TIRE_REAR', 'BRAKE_FRONT', 'BRAKE_REAR'):
        c.require(f'{prefix}{role}_A' in selected, f'missing stock source {role}/A')
    return selected


def output_models(geo):
    result = v25.reference_models(geo)
    for _, s in c.solids(geo):
        name = c.payload(s, 0x134011, 16)[160:].split(b'\0')[0].decode()
        result[name].update(name=name, groups=1)
        result[name]['extras'] = [
            (cid, s[(off + 23) & ~15:off + 8 + size] if cid == 0x13401a else s[off + 8:off + 8 + size])
            for cid, off, size in c.children(s, 8) if cid not in (0x134011, 0x134012, 0x134013, 0x80134100)]
        result[name]['vertices'] = np.frombuffer(c.payload(s, 0x134B01, 128), np.uint8).reshape(-1, 36)
        result[name]['indices'] = np.frombuffer(c.payload(s, 0x134B03, 16), '<u2').astype(np.uint32)
    return result


def stock_matrix(src):
    matrix = np.frombuffer(src['header'][64:128], '<f4').reshape(4, 4).astype(np.float64)
    scene_y = 'ABCDE'.index(src['name'][-1]) * 2.2
    c.require(np.isfinite(matrix).all() and np.array_equal(matrix[:, 3], [0, 0, 0, 1])
              and abs(np.linalg.det(matrix[:3, :3])) > 1e-8
              and np.array_equal(matrix[3, [0, 2]], [0, 0])
              and (abs(matrix[3, 1]) < 1e-6 or abs(matrix[3, 1] - scene_y) < 1e-6),
              f'unsupported stock pivot {src["name"]}: {matrix.ravel().tolist()}')
    matrix[3, :3] = 0
    return matrix


def rear_reflector(srcname, draw):
    if draw['light'] != HEADLIGHT_CHROME:
        return False
    car, _, part = srcname.partition('_KIT00_')
    return part.startswith('BRAKELIGHT_') and draw['texture'] in (
        c.name_hash(f'{car}_KIT00_BRAKELIGHT_OFF'), c.name_hash(f'{car}_KIT00_BRAKELIGHT_ON'))


def seat_c2_tail_lenses(src):
    """Use the Sting Ray's bezel openings for the opaque red faces."""
    if not re.fullmatch(r'CORVETTE67_KIT00_BRAKELIGHT_(?:LEFT|RIGHT)_[A-D]', src['name']):
        return src
    reflector, red = src['draws']
    c.require(rear_reflector(src['name'], reflector) and red['light'] == c.name_hash('DULLPLASTIC')
              and red['texture'] == reflector['texture'] and red['normal'] is None,
              'unexpected C2 tail lamp materials')
    components = v25.triangle_components(reflector)
    distant = src['name'].endswith('_D')
    c.require(len(components) == (4 if distant else 2), 'unexpected C2 tail lamp count')
    xyz = reflector['vertices'].view('<f4').reshape(-1, 9)[:, :3]
    triangles = reflector['indices'].reshape(-1, 3)
    vertices, indices, base = [], [], 0
    for component in components:
        corners = xyz[triangles[component]]
        x = corners[:, :, 0]
        # Long faces run from the open end of each chrome cup to its back.
        # Their rearward vertices weld to the inside edge of the bezel.
        lip = np.unique(corners[(np.ptp(x, axis=1) > .04)[:, None] & (x < (x.min() + x.max()) / 2)], axis=0)
        c.require(len(lip) == (9 if distant else 18), 'unexpected C2 bezel opening')
        center = lip.mean(axis=0)
        yz = lip[:, 1:] - center[1:]
        order = np.argsort(np.arctan2(yz[:, 1], yz[:, 0]))[::-1]
        lip = lip[order]
        rim = lip - center
        normal = np.cross(rim, np.roll(rim, -1, axis=0)).sum(axis=0)
        face = np.zeros((len(lip) + 1, 36), np.uint8)
        f = face.view('<f4').reshape(-1, 9)
        f[0, :3] = center
        f[1:, :3] = lip
        # Match the reviewed float32 reduction order across NumPy BLAS builds.
        xx, yy, zz = normal * normal
        f[:, 3:6] = normal / np.sqrt((xx + zz) + yy)
        face[:, 24:28] = 255
        # The OFF and ON atlases share the red disc centred at (0.5, 0.5).
        f[:, 7:9] = .5 + (f[:, 1:3] - center[1:]) * [8., -8.]
        perimeter = np.arange(1, len(lip) + 1, dtype=np.uint32)
        indices.append(np.column_stack((np.zeros_like(perimeter), perimeter, np.roll(perimeter, -1))).ravel() + base)
        vertices.append(face)
        base += len(face)
    cover = dict(red, vertices=np.concatenate(vertices), indices=np.concatenate(indices))
    return dict(src, draws=[reflector, red, cover])


def reflector_row(source_global, mw_global):
    def read(data, size, key):
        row, = (data[o + 8:o + 8 + z] for cid, o, z in c.walk(data)
                if cid == 0x135200 and z == size and c.u32(data, o + 20) == key)
        c.require(c.u32(row, 8) == 3, 'reflector lighting version')
        return row

    source = read(source_global, 284, HEADLIGHT_CHROME)
    target = bytearray(read(mw_global, 168, CHROME))
    target[:8] = bytes(8)
    struct.pack_into('<I', target, 12, REAR_REFLECTOR)
    target[20:48] = REAR_REFLECTOR_NAME.encode().ljust(28, b'\0')
    # The later row adds a second specular lobe and environment clamp/vinyl
    # controls. Carry diffuse, specular lobe one and the environment endpoints.
    target[0x30:0x5c] = source[0x64:0x90]
    target[0x5c:0x7c] = source[0x98:0xb8]
    target[0x7c:0x80] = source[0xe4:0xe8]
    target[0x80:0xa0] = source[0xf0:0x110]
    c.require(np.isfinite(np.frombuffer(target[0x30:], '<f4')).all(), 'nonfinite reflector lighting')
    return bytes(target)


def remember_plate_mount(model, markers):
    # The Undercover Viper socket has a uniform 0.9999 authoring scale.
    # Normalize near-unit uniform scale for the MW rigid attachment.
    mounts = []
    for raw in markers:
        if c.u32(raw) not in c.REAR_PLATE_MARKERS:
            continue
        row = bytearray(raw)
        axes = np.frombuffer(row, '<f4', offset=16).reshape(4, 4)[:3, :3]
        scale = np.linalg.norm(axes[0].astype(np.float64))
        if abs(scale - 1) <= .000101:
            axes /= scale
        mounts.append(bytes(row))
    c.remember_rear_plate(model, mounts)


def car_local_source(src):
    """Keep authored car-local geometry; the header basis is authoring metadata."""
    stock_matrix(src)
    # Both sides already occupy their own side of the car. Applying the
    # header's mirror again folds right panels onto left panels. Wheel/driver
    # authoring rotations likewise must not rotate their stored vertices twice.
    result = dict(src, draws=[dict(d, light=LIGHT_ALIASES.get(d['light'], d['light'])) for d in src['draws']],
                  markers=list(src['markers']))
    # The imported builder checks the LOD authoring offset, then emits identity.
    expected = np.eye(4, dtype='<f4'); expected[3, 1] = 'ABCDE'.index(src['name'][-1]) * 2.2
    header = bytearray(src['header']); header[64:128] = expected.tobytes(); result['header'] = bytes(header)
    return result


def make_models(selected, car, refs, texture_hashes, body_roles=(), *, metal_images=frozenset(), reflector=False):
    table = v25.templates(refs)
    if reflector:
        table[REAR_REFLECTOR] = table[CHROME]
    models, manifest = {}, {}
    aliases = {value: key for key, value in v25.ALIASES.items()}
    # MW names rear side glass REAR_LEFT/RIGHT_WINDOW (FORDGT has none to alias).
    aliases.update(BASE='BASE', WHEEL_TIRE_REAR='REAR_TIRE', SPOILER='SPOILER',
                   WINDOW_REAR_LEFT='REAR_LEFT_WINDOW', WINDOW_REAR_RIGHT='REAR_RIGHT_WINDOW', **ROTOR_ROLES)
    groups = {}
    for srcname, src in sorted(selected.items()):
        role, lod = srcname[len(car + '_KIT00_'):].rsplit('_', 1)
        body = role in v25.BODY_PARTS or role in ('DOOR_REAR_LEFT', 'DOOR_REAR_RIGHT', 'BUMPER_FRONT_EXTRA') or role in body_roles
        target_role = 'BODY' if body else 'INTERIOR' if role in ('ENGINE', 'SEAT_LEFT', 'SEAT_RIGHT') else aliases.get(role, role)
        name = f'{car}_BASE_{lod}' if role == 'BASE' else f'{car}_KIT00_{target_role}_{lod}'
        # No reference-bound partition: every selected triangle occurs once.
        normalized = car_local_source(src)
        for raw, draw in zip(src['draws'], normalized['draws']):
            if raw['texture'] in metal_images and raw['light'] not in METAL_ROLES:
                draw['texture'] = None
            if reflector and rear_reflector(srcname, raw):
                draw['light'] = REAR_REFLECTOR
        if role in BRAKE_SOCKET_ROLES:
            rows = []
            for raw in normalized['markers']:
                row = bytearray(raw)
                if c.u32(row) in (c.name_hash('BRAKE_FRONT'), c.name_hash('BRAKE_REAR')):
                    struct.pack_into('<I', row, 0, c.name_hash(BRAKE_SOCKET_ROLES[role]))
                rows.append(bytes(row))
            normalized['markers'] = rows
        m = v25.target_model(normalized, srcname, range(len(src['draws'])), table, texture_hashes | metal_images)
        # Static upload flags come from a known MW stock slot, never PS flags.
        refname = f'FORDGT_BASE_{lod}' if role == 'BASE' else f'FORDGT_KIT00_{target_role}_{lod}'
        ref = refs.get(refname, refs['FORDGT_KIT00_BODY_A'])
        mesh = bytearray(m['mesh']); mesh[8:16] = ref['mesh'][8:16]; m['mesh'] = bytes(mesh)
        groups.setdefault(name, []).append(m)
        manifest.setdefault(name, []).append(srcname)
    for name, parts in groups.items():
        model = c.assemble(name, parts)
        if '_KIT00_BODY_' in name and car not in UNPLATED_BODIES:
            remember_plate_mount(model, c.marker_records(model))
        models[name] = model
    return models, manifest


def rotor_extras(data, selected, local):
    wanted = {d['texture'] for s in selected.values() for d in s['draws']} & ROTOR_IMAGES - local
    if not wanted: return {}
    images = c.global_extras(data, wanted)
    c.require(wanted <= images.keys(), f'PS rotor images missing: {[f"{h:08X}" for h in sorted(wanted - images.keys())]}')
    return images


def metal_extras(data, selected, local):
    wanted = {d['texture'] for model in selected.values()
              for d in model['draws'] if d['light'] in METAL_ROLES} & {METAL_SWATCH} - local
    if not wanted: return {}
    images = c.global_extras(data, wanted)
    c.require(wanted <= images.keys(), 'global metal palette missing')
    return images


def validate_output(geo, tex, selected, manifest, reference, source_tex, reference_tex, *, extras=None, aliases=None,
                    reflector=False):
    c.validate_geometry(geo, material_names=(REAR_REFLECTOR_NAME,) if reflector else ())
    c.validate_textures(tex, 5)
    actual = output_models(geo)
    c.require(set(actual) == set(manifest) and actual, 'stock manifest differs')
    c.require(sorted(n for names in manifest.values() for n in names) == sorted(selected),
              'source stock manifest must cover every selected solid exactly once')
    local = {h for h, _ in c.texture_blobs(tex)}
    refs = v25.reference_models(reference)
    templates = v25.templates(refs)
    local_hashes = {h for h, _ in c.texture_blobs(source_tex)}
    source_hashes = local_hashes | (extras or {}).keys()
    allowed = local | {h for m in refs.values() for h in m['textures']}
    for name, names in manifest.items():
        m = actual[name]
        # The role follows the car name, which can itself hold underscores.
        refkey = 'FORDGT_' + re.search(r'_((?:KIT00|BASE)_.+)$', name).group(1)
        template = refs.get(refkey, refs['FORDGT_KIT00_BODY_A'])
        c.require(m['mesh'][8:16] == template['mesh'][8:16], f'MW mesh type/flags changed: {name}')
        expected = [(srcname, d) for srcname in names for d in selected[srcname]['draws']]
        car, separator, _ = name.partition('_KIT00_BODY_')
        if separator and car not in UNPLATED_BODIES:
            remember_plate_mount(m, [r for n in names for r in selected[n]['markers']])
        plate = c.rear_plate_model(m, reference) if 'rear_plate' in m else None
        c.require(len(m['records']) == len(expected) + bool(plate), f'source draw count changed: {name}')
        for r, (srcname, draw) in zip(m['records'], expected):
            if draw['light'] == c.name_hash('BRAKEDISC'):
                c.require(draw['texture'] in ROTOR_IMAGES and draw['texture'] in source_hashes,
                          f'source rotor atlas missing: {name}')
            light = LIGHT_ALIASES.get(draw['light'], draw['light'])
            light = v25.LIGHT_FALLBACK.get(light, light)
            wanted_light = REAR_REFLECTOR if reflector and rear_reflector(srcname, draw) else light
            c.require(m['lights'][r[29]] == wanted_light, f'source material role changed: {name}')
            _, material_name, defaults = templates[light]
            diffuse = draw['texture']
            if diffuse in source_hashes and (diffuse != METAL_SWATCH or diffuse in local_hashes
                                             or draw['light'] in METAL_ROLES):
                defaults = [diffuse] * 5
            normal = v25.normal_for_mw(draw, material_name)
            slots = c.car_texture_slots(defaults, normal=normal)
            wanted = [(aliases or {}).get(h, (h, None))[0] for h in slots]
            c.require([m['textures'][i] for i in r[24:29]] == wanted, f'source material bindings changed: {name}')
        # Independent comparison: source vertices and sockets are already
        # car-local. No authoring basis is applied to their stored coordinates.
        cursor = 0; markers = set()
        for srcname in names:
            src = selected[srcname]
            stock_matrix(src)
            for d in src['draws']:
                v = d['vertices']; actual_v = m['vertices'][cursor:cursor + len(v)]
                c.require(np.array_equal(actual_v, v), f'source vertex bytes/transform changed: {name}')
                cursor += len(v)
            for raw in src['markers']:
                row = bytearray(raw)
                role = srcname.rsplit('_KIT00_', 1)[1][:-2]
                if role in BRAKE_SOCKET_ROLES and c.u32(row) in (c.name_hash('BRAKE_FRONT'), c.name_hash('BRAKE_REAR')):
                    struct.pack_into('<I', row, 0, c.name_hash(BRAKE_SOCKET_ROLES[role]))
                markers.add(bytes(row))
        indices, base = [], 0
        for _, d in expected:
            indices.append(d['indices'] + base); base += len(d['vertices'])
        if plate:
            r = m['records'][-1]
            c.require(m['names'][-1] == plate['names'][0] and m['lights'][r[29]] == plate['lights'][0]
                      and [m['textures'][i] for i in r[24:29]] == plate['textures'] * 5
                      and np.array_equal(m['vertices'][cursor:], plate['vertices']),
                      f'MW rear plate draw changed: {name}')
            cursor += len(plate['vertices'])
            indices.append(plate['indices'] + base)
        c.require(cursor == len(m['vertices']), f'extra output vertices: {name}')
        c.require(np.array_equal(m['indices'], np.concatenate(indices)), f'source triangles changed: {name}')
        c.require(m['header'][64:128] == np.eye(4, dtype='<f4').tobytes(), f'output solid basis changed: {name}')
        rows = c.marker_records(m)
        c.require(set(rows) == markers and len(rows) == len(markers), f'source marker bytes/count changed: {name}')
        c.require(m['header'][27] == len(rows), f'marker header count: {name}')
        c.require(all(m['textures'][i] in allowed for r in m['records'] for i in r[24:29]), f'unresolved material texture: {name}')
    source_pixels = {(aliases or {}).get(h, (h, None))[0]: b[:-148] for h, b in c.texture_blobs(source_tex)}
    global_pixels = {(aliases or {}).get(h, (h, None))[0]: value[2] for h, value in (extras or {}).items()}
    fallback_pixels = {h: b[:-156] for h, b in c.texture_blobs(reference_tex)}
    for h, blob in c.texture_blobs(tex):
        if h in global_pixels: c.require(blob[:-156] == global_pixels[h], f'global pixels changed: {h:08X}')
        elif h in source_pixels: c.require(blob[:-156] == source_pixels[h], f'local pixels changed: {h:08X}')
        else: c.require(h in fallback_pixels and blob[:-156] == fallback_pixels[h], f'fallback pixels changed: {h:08X}')
    for h, (record, _, _) in (extras or {}).items():
        if h in ROTOR_IMAGES:
            target = (aliases or {}).get(h, (h, None))[0]
            blob = dict(c.texture_blobs(tex))[target]
            c.require(blob[-156:-32][79:88] == record[51:60], f'rotor sampler state changed: {h:08X}')


def convert_one(raw, texraw, ref, reftex, global_data, rotor_data, mw_global, car, body_roles, *, decode=None):
    """Assemble and audit the stock model, then apply its private texture identities."""
    c.require(c.u32(c.payload(texraw, 0x33310001)) == 8, 'ProStreet TPK v8 required')
    sources = (decode or decode_sources)(raw, car)  # decode: another game's reader of the same model fields
    selected = stock_sources(sources, car)
    if decode is None and car == 'CORVETTE67':
        selected = {name: seat_c2_tail_lenses(src) for name, src in selected.items()}
    source_hashes = {h for h, _ in c.texture_blobs(texraw)}
    rotors = rotor_extras(rotor_data, selected, source_hashes)
    metal_images = metal_extras(global_data, selected, source_hashes)
    reflector = decode is None and any(rear_reflector(n, d) for n, m in selected.items() for d in m['draws'])
    # Without its own image a tyre draw would show the template's first rubber draw, an unrelated atlas.
    tyres = ({d['texture'] for s in selected.values() for d in s['draws']} & TYRE_IMAGES) - source_hashes
    models, manifest = make_models(selected, car, v25.reference_models(ref), source_hashes | tyres | rotors.keys(),
                                   body_roles, metal_images=metal_images.keys(), reflector=reflector)
    bound = {m['textures'][r[25]] for m in models.values() for r in m['records']}
    normals = set().union(*(s['normal_textures'] for s in sources.values()))
    bound_normals = normals & bound
    deferred = normals & {v25.PS_DAMAGE_NORMAL}
    wanted = bound_normals | deferred | tyres
    extras = c.global_extras(global_data, wanted - source_hashes)
    c.require(wanted - tyres <= source_hashes | extras.keys(),
              f'PS normal images missing: {[f"{h:08X}" for h in sorted(wanted - tyres - source_hashes - extras.keys())]}')
    c.require(tyres <= extras.keys(), f'PS tyre images missing: {[f"{h:08X}" for h in sorted(tyres - extras.keys())]}')
    extras.update(rotors)
    extras.update(metal_images)
    aliases = {h: value for h, value in c.normal_resource_aliases(car).items() if h in wanted}
    for m in models.values(): m['textures'] = [aliases.get(h, (h, None))[0] for h in m['textures']]
    geo, _ = c.write_geometry(raw, models, len(models), plate_reference=ref)
    # Each image keeps its own sorting and alpha state; the template's first record of a
    # format is arbitrary (its first DXT3 is a blended lamp glass, which hid every rim).
    tex, _ = c.convert_textures(texraw, reftex, extras, aliases, source_sampler_state=True)
    if reflector:
        row = reflector_row(global_data, mw_global)
        geo = c.append_material_rows(geo, {REAR_REFLECTOR: row})
    validate_output(geo, tex, selected, manifest, ref, texraw, reftex,
                    extras=extras, aliases=aliases, reflector=reflector)
    # Truncated labels need the source hash to distinguish their private identities.
    labels = dict(aliases)
    records = [(h, b[-148:-24]) for h, b in c.texture_blobs(texraw)] + [(h, v[0]) for h, v in extras.items()]
    for h, record in records:
        name = bytes(record[89:89 + record[88]]).split(b'\0')[0].decode('ascii')
        if h not in labels and c.name_hash(name) != h:
            labels[h] = (h, f'{name}_{h:08X}')
    renames = []
    tex, _ = c.convert_textures(texraw, reftex, extras, labels, source_sampler_state=True,
                                namespace=(car, 'NFSAD_' + car), renames=renames, preserve_donor_defaults=True)
    c.validate_textures(tex, 5)
    return geo, tex, renames, reflector
