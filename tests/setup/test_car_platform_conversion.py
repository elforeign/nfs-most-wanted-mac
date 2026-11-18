"""Full-entry PC/X360 writer proof using generated data and independent readers.

Setup sync: original SHA-checked rules fail 4/4 targeted checks (exit 1);
current passes 4/4 and the complete 18-test module (exit 0). Source brake
diffuse endpoints are 3/6 with power 10 on PC/X360; explicit amplitude
translation is preserved. Stripped material labels stream three source images
in requested slots; authored rear-brake omissions pass on both platforms.

Original baseline: 8ac38cb3, convert() text SHA256
43354b9c45189e6897c464539550e5bfbe5e8b29f7704f042bbaa307cfa73926.
Its full entry rejects D's normalized fixture with FormatError:
Carbon mesh header must be 52 aligned bytes. The same platform's complete
stock fixture must now write seven independently audited files.
Good: 14 full-entry tests; PC/X360 write seven files deterministically.
Broken: pinned original full entry fails both writer regressions (2/2 errors);
14 written-byte mutants, 6 omitted dependencies, 3 platform mix-ups and 2 opaque
stream controls reject. Stripped rim labels retain atlas-based illumination;
one-mip metadata is independently checked for all five texture formats.
Four broken policies fail 1/5/1/1 assertions (stripped labels/mips/dependency/lamps).
Lamp default-Off regression: reviewed code fails 2/2 PC/X360 output checks; fixed 0/2.
No conversion or recognized input is an appearance approval.
--assets exits 3 without inputs.
Texture-namespace dependency: omitting the shared helper fails 1/1 identity
test; including it passes the PC/X360 dependency assertions.
GRIP policy: SHA-pinned original (--original-grip-policy) fails 1/1 dependency
test (exit 1); current passes 14/14 tests (exit 0), including both platforms.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_convert as cc, car_sources as cs, car_x360 as cx
from setup_kit import car_x360_inputs as ci, car_lighting as cl
from setup_kit.x360 import Source
import test_car_convert as pc
import test_car_x360 as xenon
import test_car_x360_inputs as deps
import test_car_lighting as lighting_fixture

ORIGINAL_GRIP_POLICY = r'''def _converter_policy(platform, lighting, tracked, mac_audio=False):
    """Hash executable policy dependencies separately from local asset inputs."""
    from . import car_lighting, car_paint, car_physics, car_sources, x360, xdvdfs
    require(lighting is None or isinstance(lighting, Mapping), 'lighting options must be a mapping')
    options = dict(lighting or {})
    require(set(options) <= {'amplitudes', 'lamps', 'gains'}, 'unknown lighting policy option')
    settings = dict(amplitudes=options.get('amplitudes', False), lamps=options.get('lamps', False))
    gains = options.get('gains')
    if gains is not None:
        require(isinstance(gains, car_lighting.Gains), 'invalid lighting gains')
        gains.validate()
        settings['gains'] = vars(gains)
    modules = [Path(__file__), Path(huff.__file__), Path(car_texture_namespace.__file__), Path(car_lighting.__file__),
               Path(car_paint.__file__), Path(car_physics.__file__),
               Path(car_sources.__file__), Path(x360.__file__), Path(xdvdfs.__file__)]
    if mac_audio:
        from . import car_audio, car_audio_abi, car_audio_pack, car_audio_codec, car_audio_bank
        modules += [Path(m.__file__) for m in (car_audio, car_audio_abi, car_audio_pack, car_audio_codec, car_audio_bank)]
    if platform == 'x360':
        from . import car_x360, car_x360_inputs
        modules += [Path(m.__file__) for m in (car_x360, car_x360_inputs)]
        codec = Path(car_x360._pixels().__file__)
        modules += [codec, codec.with_name('texture_compare.py')]
    fingerprints = {p.name: hashlib.sha256(tracked.read(p)).hexdigest() for p in modules}
    identity = dict(revision=2, platform=platform, lighting=settings, numpy=np.__version__,
                    textures='top mip; source sampler state; BC5 positive Z/opaque alpha; BC5/BGRA lossy DXT5',
                    modules=fingerprints)
    identity['sha256'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return identity, options
'''
ORIGINAL_GRIP_POLICY_SHA = '8df6b59d8a48d300435f0740d3a77f8ab3931988d7ebcde38e7f0a3dc8bcc8eb'

BASELINE_REJECTION = 'Carbon mesh header must be 52 aligned bytes'


# SHA-checked fragments of the original Setup rules, executed only by the broken control.
ORIGINAL_SETUP_RULES = (
    ('map_vertex_lighting',
     'c6483101c0ecf39787f3e77cc86abfdb0a5264806b9194acb1cbf159d087e363',
     r'''def map_vertex_lighting(models, source_car=None):
    """Repair classified rim RGB and conditionally supply neutral body illumination.

    MW car VS multiplies diffuse/specular/environment by saturate(2*COLOR.x).
    CCX exports zero for its whole painted body (and most exterior draws), so
    copying that field blacks out even correctly bound paint. Detect this from
    stock BODY_A paint, not a car-name list. The paint gate protects authored
    non-rim shading; classified zero-RGB rim vertices need repair independently.
    This is a compatibility default, not a reconstruction of Carbon's lighting.
    """
    # Stripped labels retain the source texture identity. Classify paint by its
    # replaceable skin binding, and rim draws by the rim atlas on a tire solid.
    paint_images = {name_hash(f'{source_car}_{r}') for r in MW_PAINT_BINDINGS} if source_car else set()
    rim_image = name_hash(source_car + '_TIRE') if source_car else None
    def role(m, r, name):
        label = bytes(name).split(b'\0')[0]
        diffuse = m['textures'][r[24]] if not label and source_car else None
        paint = label in PAINT_NAMES or (not label and diffuse in paint_images)
        rim = label.startswith(b'RIM_') or (not label and '_TIRE_' in m['name'] and diffuse == rim_image)
        return paint, rim
    stock = next((m for n, m in models.items() if n.endswith('_KIT00_BODY_A')), None)
    paint, base = [], 0
    for r, name in zip(stock['records'], stock['names']) if stock is not None else ():
        count = u32(r, 60)
        if role(stock, r, name)[0]:
            paint.append(stock['vertices'][base:base + count, 24:28])
        base += count
    neutral_body = bool(paint) and not any(v.any() for v in paint)
    changed = 0
    for m in models.values():
        base = 0
        for r, name in zip(m['records'], m['names']):
            count = u32(r, 60)
            colours = m['vertices'][base:base + count, 24:28]
            if role(m, r, name)[1]:
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
'''),
    ('material_resources',
     '5971d26d7a7d5c566dfc16657950ad4cf67efa570235260a9e85c288008bdfa3',
     r'''def material_resources(source, source_tex, donor_tex, car, slot, shared_raw, shared_images, dynamic,
                       *, models=None, textures=None, shared_textures=None, source_aliases=None):
    wanted = (geometry_texture_hashes(source) if models is None else
              {h for m in models.values() for h in m['textures']})
    models = ({m['name']: m for _, s in solids(source) for m in [model(s)]}
              if models is None else models)
    textures = pc_texture_entries(source_tex) if textures is None else textures
    aliases = slot_texture_aliases(None, car, slot, decoded=textures)
    aliases.update(source_aliases or {})
    local = set(textures)
    def lookup(wanted):
        return (global_extras(shared_raw, wanted) if shared_textures is None else
                {h: shared_textures[h] for h in wanted if h in shared_textures})
    for kit in range(6):
        hs = name_hash(f'{car}_KIT{kit:02d}_HEADLIGHT_GLASS_OFF')
        if hs in wanted and hs not in local:
            aliases[hs] = (name_hash('WINDOW_MASK'), 'WINDOW_MASK')
    for name in ('UNIVERSAL_TIRE_N', 'TREAD_N'):
        if name_hash(name) in wanted:
            aliases[name_hash(name)] = (name_hash('TIRE_STYLE01_N'), 'TIRE_STYLE01_N')
    for m in models.values():
        for record, name in zip(m['records'], m['names']):
            role = name.split(b'\0')[0]
            diffuse = m['textures'][record[24]]
            fallback = {b'GRILL_STOCK': 'GRILL_02', b'FOGLIGHT_GLASS': 'WINDOW_FRONT'}.get(role)
            if fallback and diffuse not in local and diffuse not in shared_images:
                aliases[diffuse] = (name_hash(fallback), fallback)
    translated = {aliases.get(h, (h, None))[0] for h in wanted}
    present = {aliases.get(h, (h, None))[0] for h in local} | {h for h, _ in texture_blobs(donor_tex)}
    needed = translated - shared_images - dynamic - {name_hash(f'{slot}_{r}') for r in MW_PAINT_BINDINGS} - present
    extras = lookup(needed | (wanted & set(map(name_hash, ('TIRE_STYLE01', 'TIRE_STYLE02')))) |
                           set(map(name_hash, ('TIRE_STYLE01_N', 'DOORLINE_N'))))
    # The shared grill name has different pixels in the two installations:
    # source DXT5 has holes; MW's same-hash DXT1 atlas is fully opaque.
    grill = name_hash('GRILL_02')
    if grill in wanted or any(aliases.get(h, (h, None))[0] == grill for h in wanted):
        require(name_hash(car + '_ENGINE') not in local, 'grill streaming slot occupied')
        sourced = lookup({grill})
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
        sourced = lookup({fibre})
        require(fibre in sourced, 'source carbon fibre pixels absent')
        extras.update(sourced)
        aliases[fibre] = (target_hash, target_name)
    aliases.update(normal_resource_aliases(slot))
    return aliases, extras
'''),
    ('_convert_decoded',
     'd549cef8de02df4d5a3c88a2416d4edc66b25f5a35ce3345808a0be15fe92dcb',
     r'''def _convert_decoded(*, car, donor, source, source_tex, models, textures, shared_raw,
                     attributes, mw_donor_dir, mw_root, out_dir, tracked, platform,
                     source_inputs, source_check, code, policy, lighting,
                     shared_textures=None, source_aliases=None, runtime_bindings=(),
                     runtime_materials=(), audio_pack=None, grip=None):
    """One assembly, resource, sidecar and audited writer path for both platforms."""
    from . import car_grip, car_lighting, car_paint, car_physics
    mw, mw_tex = (tracked.read(mw_donor_dir / n) for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))
    refs = validate_geometry(mw, input_pack=True)
    validate_textures(mw_tex, 5)
    images, materials, dynamic = shared_resources(tracked.read, mw_root)
    # Source runtime identities must resolve to the corresponding MW binding.
    dynamic |= set(runtime_bindings)
    aliases, extras = material_resources(source, source_tex, mw_tex, car, donor, shared_raw, images, dynamic,
        models=models, textures=textures, shared_textures=shared_textures, source_aliases=source_aliases)
    aliases, extras = supply_absent_lamp_images(None, car, donor, aliases, extras, decoded=textures)
    used = {h for m in models.values() for h in m['lights']}
    roles = material_role_map(shared_raw)
    source_rows = _lighting_rows(shared_raw, 236)
    mw_rows = {}
    for path, raw in list(tracked.buffers.items()):
        if path.is_relative_to(mw_root) and path.parent.name == 'GLOBAL':
            for key, row in _lighting_rows(unpack_file(raw), 168).items():
                require(key not in mw_rows or mw_rows[key] == row, 'conflicting MW lighting rows')
                mw_rows[key] = row
    # Keep the existing specialized rim finish and dynamic paint/rim bindings.
    skipped = set(runtime_materials) | {name_hash('BODY'), name_hash('MAGSILVER'),
                                     name_hash('MAGMATTE'), name_hash('CARBON_MAGMATTE'),
                                     0xD6D6080A, 0x22719FA9}
    selected = car_lighting.policy(source_rows, mw_rows, used, namespace='NFSAD_' + car,
        roles=roles, dynamic=skipped, occupied=images | materials | set(textures) |
        {name_hash(n) for n in models}, **lighting)
    models, assembled = decoded_models(models, mw, donor if car != donor else None, aliases, selected.remap,
                                        source_car=car)
    stock = [f'{donor}_BASE_{lod}' for lod in 'ABCD'] + [f'{donor}_KIT00_BODY_{lod}' for lod in 'ABCD']
    stock += [f'{donor}_KIT00_{end}_BRAKE_A' for end in ('FRONT', 'REAR')]
    missing = sorted(set(stock) - models.keys())
    if missing:
        raise MissingStockPartError('missing stock parts: ' + ', '.join(missing))
    geometry, stats = write_geometry(source, models, len(assembled), plate_reference=mw)
    models = validate_geometry(geometry)
    renames = []
    textures_out, nt = convert_textures(source_tex, mw_tex, extras, aliases, source_sampler_state=True,
        namespace=(donor, 'NFSAD_' + car), renames=renames, decoded=textures)
    available = validate_textures(textures_out, 5) | images | dynamic
    available |= {name_hash(f'{donor}_{r}') for r in MW_PAINT_BINDINGS}
    models, solidmap = isolate(models, refs, donor, car, renames)
    used = {h for m in models.values() for h in m['lights']}
    rows = dict(selected.rows)
    if name_hash('CARBON_MAGMATTE') in used:
        rows.update(source_rim_rows(shared_raw, unpack_file(tracked.read(mw_root / 'GLOBAL/GLOBALB.LZC'))))
        require(name_hash('CARBON_MAGMATTE') in rows, 'source rim material absent')
    materials |= rows.keys()
    for m in models.values():
        validate_model(m)
        require(set(m['textures']) <= available, 'unresolved geometry texture')
        require(set(m['lights']) <= materials, 'unresolved MW material')
    geometry, _ = write_geometry(geometry, models)
    geometry = append_material_rows(geometry, rows)
    validate_geometry(geometry)
    wheels = wheel_sidecar(attributes, car, donor, fit=True)
    files = {'GEOMETRY.BIN': geometry, 'TEXTURES.BIN': textures_out, 'SOLIDMAP.BIN': solidmap, 'WHEELFIT.BIN': wheels}
    if audio_pack is not None:
        files['AUDIO.BIN'] = audio_pack
    physics = car_physics.metadata(attributes, car, donor)
    if physics is not None:
        files['PHYSICS.BIN'] = physics
    files['GRIP.BIN'] = car_grip.optional_metadata(attributes, car, donor) if platform == 'pc' else grip
    files['PAINT.BIN'], paint = car_paint.metadata(shared_raw,
        lambda: unpack_file(tracked.read(mw_root / 'GLOBAL/GLOBALB.LZC')), car, donor, attributes)
    collision = collision_sidecar(shared_raw, car, donor)
    if collision is not None:
        files['COLLISION.BIN'] = collision
    source_check()
    tracked.check()
    code.check()
    write_outputs(out_dir, files, tracked.protected + code.protected)
    source_check()
    tracked.check()
    code.check()
    normalized_source = source_inputs()
    from .car_sources import path_hash
    dependencies = {'source:' + str(key): dict(row) for key, row in normalized_source.items()}
    dependencies.update({'mw:' + path_hash(str(p.relative_to(mw_root))):
                         dict(size=len(tracked.buffers[p]), sha256=h)
                         for p, h in tracked.hashes.items() if p.is_relative_to(mw_root)})
    inputs = {str(p): h for p, h in sorted(tracked.hashes.items())}
    if platform == 'x360':
        inputs.update({'x360:' + str(key): row['sha256'] for key, row in normalized_source.items()})
    return dict(car=car, donor=donor, platform=platform, namespace='NFSAD_' + car, converter_revision=2,
        converter_policy=policy, approved=False, default_paint=paint,
        solids=stats['solids'], assemblies=stats['assemblies'], textures=nt,
        inputs=inputs, source_inputs=normalized_source, dependencies=dependencies,
        outputs={n: hashlib.sha256(data).hexdigest() for n, data in files.items() if data is not None},
        lighting=dict(rows=sorted(rows), dynamic=selected.dynamic, unsupported=selected.unsupported),
        material_policy='MW roles and MW-derived lamp rows; classified neutral vertex illumination')
'''),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def chunks(data, start=0, end=None):
    end = len(data) if end is None else end
    while start < end:
        assert end - start >= 8
        kind, size = struct.unpack_from('<II', data, start)
        assert size <= end - start - 8
        yield kind, start, size
        start += size + 8
    assert start == end


def contents(data, rows, kind, alignment=1):
    found = [(o, z) for c, o, z in rows if c == kind]
    assert len(found) == 1
    off, size = found[0]
    at = (off + 8 + alignment - 1) // alignment * alignment
    return data[at:off + 8 + size]


def entries(data, texture=False):
    """Read actual uncompressed output indices without converter validators."""
    kind = 0xB3310000 if texture else 0x80134001
    rows = list(chunks(data, 8, struct.unpack_from('<I', data, 4)[0] + 8))
    off, size = next((o, z) for c, o, z in rows if c == kind)
    header = list(chunks(data, off + 8, off + size + 8))
    table = contents(data, header, 0x33310003 if texture else 0x134004)
    result, spans = {}, []
    for hs, at, stored, unpacked, flags, zero in struct.iter_unpack('<6I', table):
        assert flags == zero == 0 and stored == unpacked and stored > 0
        assert hs not in result and at + stored <= len(data)
        spans.append((at, at + stored))
        result[hs] = (at, data[at:at + stored])
    spans.sort()
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    return result


def geometry_components(data):
    end = struct.unpack_from('<I',data,4)[0]+8
    trailers = list(chunks(data,end))
    if trailers:
        ho,hz = next((o,z) for c,o,z in chunks(data,8,end) if c == 0x80134001)
        info = contents(data,list(chunks(data,ho+8,ho+hz+8)),0x134002)
        assert struct.unpack_from('<II',info,0x68) == (end,len(data)-end)
        identities = set()
        for cid,off,size in trailers:
            assert cid == 0x135200 and size == 168
            row = data[off+8:off+8+size]
            assert struct.unpack_from('<I',row,8)[0] == 3
            key = struct.unpack_from('<I',row,12)[0]
            assert key not in identities and b'\0' in row[20:48]
            identities.add(key)
            assert np.isfinite(np.frombuffer(row[48:],'<f4')).all()
    result = {}
    for hs, (at, solid) in entries(data).items():
        # Chunk alignment is absolute within the written pack.
        top = list(chunks(data, at + 8, at + len(solid)))
        header = contents(data, top, 0x134011, 16)
        assert struct.unpack_from('<I', header, 16)[0] == hs
        name = header[160:].split(b'\0')[0].decode('ascii')
        mo, mz = next((o, z) for c, o, z in top if c == 0x80134100)
        mesh = list(chunks(data, mo + 8, mo + mz + 8))
        vertices = contents(data, mesh, 0x134b01, 128)
        indices = contents(data, mesh, 0x134b03, 16)
        records = contents(data, mesh, 0x134b02, 16)
        assert len(vertices) % 36 == 0 and len(records) % 104 == 0
        ix = np.frombuffer(indices, '<u2')
        vb = np.frombuffer(vertices, np.uint8).reshape(-1, 36)
        assert ix.max() < len(vb)
        assert len(ix) == struct.unpack_from('<I', header, 20)[0] * 3
        assert not {c for c, _, _ in top} & ci.EXCLUDED_STREAMS
        result[name] = dict(vertices=vb, indices=ix, records=records,
            lights=contents(data, top, 0x134013), textures=contents(data, top, 0x134012))
    return result


def texture_components(data):
    result = {}
    for hs, (_, blob) in entries(data, True).items():
        r, platform, pixels = blob[-156:-32], blob[-32:], blob[:-156]
        assert struct.unpack_from('<I', r, 36)[0] == hs
        assert struct.unpack_from('<I', r, 56)[0] == len(pixels)
        w, h = struct.unpack_from('<HH', r, 68)
        fmt = platform[20:24]
        assert fmt in (b'DXT1', b'DXT3', b'DXT5')
        assert len(pixels) == ((w+3)//4) * ((h+3)//4) * (8 if fmt == b'DXT1' else 16)
        result[hs] = (r, fmt, pixels)
    return result


def renamed_solid(raw, name):
    """Rebuild alignment after changing only the synthetic solid's identity."""
    body = bytearray()
    alignments = {0x134011: 16, 0x13401a: 16, 0x134900: 16,
                  0x134b02: 128, 0x134b03: 32, 0x134b01: 128}
    for kind, off, size in chunks(raw, 8):
        if kind == 0x80134100:
            mesh = bytearray()
            for cid, mo, mz in chunks(raw, off + 8, off + size + 8):
                align = alignments.get(cid, 1)
                start = (mo + 8 + align - 1) // align * align
                payload = raw[start:mo + 8 + mz]
                mesh += xenon.aligned(cid, 16 + len(body) + len(mesh), payload, align)
            body += xenon.chunk(kind, mesh)
        else:
            align = alignments.get(kind, 1)
            start = (off + 8 + align - 1) // align * align
            payload = raw[start:off + 8 + size]
            if kind == 0x134011:
                header = bytearray(payload[:160])
                struct.pack_into('<I', header, 16, cc.name_hash(name))
                payload = header + name.encode() + b'\0'
                payload += bytes(-len(payload) % 4)
            body += xenon.aligned(kind, 8 + len(body), payload, align)
    return xenon.chunk(0x80134010, body)


def mw_palette():
    name = 'GLOSS_L1_COLOR01'
    part = bytearray(14)
    struct.pack_into('<I', part, 0, cc.name_hash(name))
    part[4] = 0x4C
    values = [('RED', 255), ('GREEN', 0), ('BLUE', 0)]
    return b''.join(cc.chunk(cid, raw) for cid, raw in (
        (0x34604, part), (0x34605, b''.join(struct.pack('<II', cc.name_hash(n), v) for n,v in values)),
        (0x34606, name.encode() + b'\0'), (0x3460B, struct.pack('<I', cc.name_hash('PAINT'))),
        (0x3460C, struct.pack('<4H', 3, 0, 1, 2))))


class PlatformConversion(unittest.TestCase):
    def setUp(self):
        (ROOT / '.metal-cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='platform-', dir=ROOT / '.metal-cache')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files, self.recognition = deps.files_and_recognition()
        raw = next(iter(cc.solids(self.files['CARS/SYNTH/GEOMETRY.BIN'])))[1]
        self.expected = xenon.solid()[1]
        self.files['CARS/SYNTH/GEOMETRY.BIN'] = xenon.indexed_pack([
            (cc.name_hash(n), renamed_solid(raw, n)) for n in pc.stock_names('SYNTH')])
        self.refresh()
        self.donor = self.root / 'mw/CARS/DONOR'
        self.donor.mkdir(parents=True)
        (self.donor / 'GEOMETRY.BIN').write_bytes(pc.geometry('DONOR', False))
        (self.donor / 'TEXTURES.BIN').write_bytes(pc.texture('DONOR', 5))
        mw_global = self.root / 'mw/GLOBAL'
        mw_global.mkdir()
        material = bytearray(168)
        struct.pack_into('<II', material, 8, 3, cc.name_hash('SYNTH_MATERIAL'))
        material[20:35] = b'SYNTH_MATERIAL\0'
        (mw_global / 'GLOBALA.BUN').write_bytes(cc.chunk(0x135200, material))
        (mw_global / 'GLOBALB.LZC').write_bytes(mw_palette())
        self.pc = self.root / 'pc/CARS/SYNTH'
        self.pc.mkdir(parents=True)
        (self.pc / 'GEOMETRY.BIN').write_bytes(pc.geometry('SYNTH', True))
        (self.pc / 'TEXTURES.BIN').write_bytes(pc.texture('SYNTH'))
        self.pc_global = self.root / 'pc/GLOBAL'
        self.pc_global.mkdir()
        # Same logical typed data, using D's checked normalization for the PC fixture.
        (self.pc_global / 'attributes.bin').write_bytes(ci.normalize_attributes(deps.attrs(), 'SYNTH'))
        (self.pc_global / 'GLOBALB.LZC').write_bytes(deps.global_chunks())
        # PC geometry uses the ordinary HOSES role.
        hoses = bytearray(material)
        struct.pack_into('<I', hoses, 12, cc.name_hash('HOSES'))
        hoses[20:48] = b'HOSES\0'.ljust(28, b'\0')
        with (mw_global / 'GLOBALA.BUN').open('ab') as f:
            f.write(cc.chunk(0x135200, hoses))
        self.source = Source(self.root / 'x360')
        self.addCleanup(self.source.close)

    def refresh(self):
        deps.write_files(self.root / 'x360', self.files)
        for row in self.recognition['shared'] + self.recognition['cars'][0]['files']:
            name = next(n for n in self.files if cs.path_hash(n) == row['path_hash'])
            row.update(size=len(self.files[name]), sha256=digest(self.files[name]))

    def bundle(self):
        return ci.resolve(self.source, 'SYNTH', 'DONOR', recognition=self.recognition)

    def run_conversion(self, platform, output='out', **kwargs):
        if platform == 'pc':
            return cc.convert(self.pc, self.donor, self.pc_global, self.root / output, **kwargs)
        return cc.convert_x360(self.bundle(), self.donor, self.root / output, **kwargs)

    def snapshot(self):
        return {str(p.relative_to(self.root)): digest(p.read_bytes())
                for base in ('x360', 'mw', 'pc') for p in (self.root / base).rglob('*') if p.is_file()}

    def audit(self, directory, report):
        self.assertEqual(set(report['outputs']), {'GEOMETRY.BIN', 'TEXTURES.BIN', 'SOLIDMAP.BIN',
            'WHEELFIT.BIN', 'PAINT.BIN', 'PHYSICS.BIN', 'COLLISION.BIN'})
        actual = {p.name: p.read_bytes() for p in directory.iterdir()}
        self.assertEqual({n: digest(b) for n,b in actual.items()}, report['outputs'])
        models = geometry_components(actual['GEOMETRY.BIN'])
        textures = texture_components(actual['TEXTURES.BIN'])
        self.assertEqual(len(models), 14)
        self.assertFalse(report['approved'])
        mapping = actual['SOLIDMAP.BIN']
        magic, version, identity, count = struct.unpack_from('<4I', mapping)
        self.assertEqual((magic, version, identity), (0x31504D53, 2, cc.name_hash('SYNTH')))
        self.assertEqual(len(mapping), 16 + count * 8)
        pairs = list(struct.iter_unpack('<II', mapping[16:]))
        self.assertEqual(len({a for a,b in pairs}), count)
        self.assertEqual(len({b for a,b in pairs}), count)
        wheels = actual['WHEELFIT.BIN']
        self.assertEqual(len(wheels), 380)
        self.assertEqual(struct.unpack_from('<4I', wheels),
                         (0x31544657, 2, cc.hash32('synth'), cc.hash32('donor')))
        np.testing.assert_allclose(struct.unpack_from('<16f', wheels, 16), (.1, -.2, .3, .4)*4)
        self.assertEqual(struct.unpack_from('<I', wheels, 160)[0], 7)  # spoke count in render tail
        physics = actual['PHYSICS.BIN']
        self.assertEqual(len(physics), 36)
        self.assertEqual(struct.unpack_from('<4I', physics),
                         (0x31534850, 1, cc.hash32('synth'), cc.hash32('donor')))
        self.assertGreater(struct.unpack_from('<f', physics, 16)[0], 0)
        paint = struct.unpack('<8I', actual['PAINT.BIN'])
        self.assertEqual(paint[:4], (0x31545044, 2, cc.name_hash('SYNTH'), cc.name_hash('DONOR')))
        checksum = 0xFFFFFFFF
        for value in paint[:7]: checksum = ((checksum ^ value) * 16777619) & 0xFFFFFFFF
        self.assertEqual(paint[7], checksum)
        self.assertEqual(paint[5:7], (cc.name_hash('GLOSS_L1_COLOR01'), 2))
        collision = actual['COLLISION.BIN']
        self.assertEqual(struct.unpack_from('<4I', collision),
                         (0x31544C43, 1, cc.hash32('synth'), cc.hash32('donor')))
        self.assertEqual(struct.unpack_from('<I', collision, 16)[0], len(collision)-20)
        self.assertEqual(struct.unpack_from('<3h', collision, 52), (100, 200, 300))
        return models, textures

    def test_original_full_entry_rejection_and_x360_success(self):
        bundle = self.bundle()
        self.assertEqual(len(bundle.inputs), 6)
        with self.assertRaisesRegex(cc.FormatError, BASELINE_REJECTION):
            cc.convert(self.root/'x360/CARS/SYNTH', self.donor,
                       self.root/'x360/GLOBAL', self.root/'original')
        report = cc.convert_x360(bundle, self.donor, self.root/'out')
        self.audit(self.root/'out', report)
        self.assertEqual(report['source_inputs'], bundle.inputs)

    def test_both_platforms_deterministic_inputs_unchanged(self):
        before = self.snapshot()
        for platform in ('pc', 'x360'):
            with self.subTest(platform=platform):
                first = self.run_conversion(platform, platform+'1')
                second = self.run_conversion(platform, platform+'2')
                self.assertEqual(first['outputs'], second['outputs'])
                self.assertEqual(first['converter_policy'], second['converter_policy'])
                self.audit(self.root/(platform+'1'), first)
                self.audit(self.root/(platform+'2'), second)
                identity = dict(first['converter_policy'])
                expected = identity.pop('sha256')
                self.assertEqual(digest(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()), expected)
        self.assertEqual(before, self.snapshot())

    def test_independent_indexed_components(self):
        report = self.run_conversion('x360')
        models, _ = self.audit(self.root/'out', report)
        expected = np.frombuffer(self.expected, np.uint8).reshape(-1, 36)
        ix = np.array([i*4+j for i in range(4) for j in (2,0,1,3,0,2)])
        for name, m in models.items():
            copies = 2 if '_BODY_' in name else 1
            np.testing.assert_array_equal(m['vertices'], np.concatenate([expected]*copies))
            np.testing.assert_array_equal(m['indices'], np.concatenate([ix+i*16 for i in range(copies)]))
        # The independent reader must detect a genuine written component change.
        data = bytearray((self.root/'out/GEOMETRY.BIN').read_bytes())
        hs, (at, solid) = next(iter(entries(data).items()))
        top = list(chunks(data, at+8, at+len(solid)))
        mo,mz = next((o,z) for c,o,z in top if c == 0x80134100)
        off = next(o for c,o,z in chunks(data, mo+8, mo+mz+8) if c == 0x134b01)
        data[(off+135)//128*128] ^= 1
        with self.assertRaises(AssertionError):
            altered = geometry_components(data)
            for name,m in altered.items():
                np.testing.assert_array_equal(m['vertices'], models[name]['vertices'])

    def test_texture_policy_all_five_formats(self):
        for fmt in xenon.FORMAT_SPECS:
            with self.subTest(fmt=hex(fmt)):
                hs, blob, linear = deps.renamed_blob('SYNTH_DIFFUSE', fmt)
                blob = bytearray(blob)
                state = bytes((3,5,7,11,13,17,19,23,29))
                blob[len(blob)-156+51:len(blob)-156+60] = state
                self.files['CARS/SYNTH/TEXTURES.BIN'] = xenon.indexed_pack([(hs,blob)], texture=True)
                self.refresh()
                out = 'format-'+str(fmt)
                result = self.run_conversion('x360', out)
                _, textures = self.audit(self.root/out, result)
                r, written_fmt, pixels = textures[cc.name_hash('NFSAD_SYNTH_DIFFUSE')]
                self.assertEqual(r[78], 1)
                self.assertEqual(r[79:88],state)
                if fmt in (0x1A200152, 0x1A200153, 0x1A200154):
                    self.assertEqual(pixels, linear)
                    self.assertEqual(written_fmt.decode(), xenon.FORMAT_SPECS[fmt][0])
                else:
                    self.assertEqual(written_fmt, b'DXT5')
                    name = xenon.FORMAT_SPECS[fmt][0]
                    want = cx._pixels().encode_dxt('DXT5', cx.decode_image(name,128,128,linear))
                    self.assertEqual(pixels, want)
                    if name == 'BC5':
                        self.assertTrue(np.all(cx.decode_image(name,128,128,linear)[:,:,3] == 255))

    def test_pc_sampler_aliases_and_namespace_isolation(self):
        raw = bytearray((self.pc/'TEXTURES.BIN').read_bytes())
        at,blob = next(iter(entries(raw,True).values()))
        # The source record is v8 (148-byte tail), independent of MW's tail.
        state = bytes((2,3,5,7,11,13,17,19,23))
        raw[at+len(blob)-148+51:at+len(blob)-148+60] = state
        (self.pc/'TEXTURES.BIN').write_bytes(raw)
        result = self.run_conversion('pc')
        models,textures = self.audit(self.root/'out',result)
        target = cc.name_hash('NFSAD_SYNTH_DIFFUSE')
        self.assertEqual(textures[target][0][79:88],state)
        self.assertNotIn(cc.name_hash('DONOR_DIFFUSE'),textures)
        self.assertNotIn(cc.name_hash('SYNTH_DIFFUSE'),textures)
        for name,m in models.items():
            self.assertTrue(name.startswith('NFSAD_SYNTH_'))
            self.assertEqual(struct.unpack_from('<I',m['textures'])[0],target)

    def test_platform_mixups(self):
        with self.assertRaisesRegex(cc.FormatError, 'normalized X360 bundle'):
            cc.convert_x360(self.pc, self.donor, self.root/'bad')
        for name in ('GEOMETRY.BIN','TEXTURES.BIN'):
            original = self.files['CARS/SYNTH/'+name]
            self.files['CARS/SYNTH/'+name] = (self.pc/name).read_bytes()
            self.refresh()
            with self.subTest(name=name), self.assertRaises(cc.FormatError):
                self.bundle()
            self.files['CARS/SYNTH/'+name] = original
            self.refresh()

    def test_omitted_dependency_and_changed_source(self):
        for key in list(self.bundle().inputs):
            bundle = self.bundle()
            del bundle.inputs[key]
            with self.assertRaisesRegex(cc.FormatError, 'omitted or altered dependency'):
                cc.convert_x360(bundle, self.donor, self.root/'bad')
        bundle = self.bundle()
        path = self.root/'x360/GLOBAL/GLOBALB.BUN'
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(cc.InputChangedError):
            cc.convert_x360(bundle, self.donor, self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_noncatalogued_dependency_is_required(self):
        name = 'EXTRA_DRIVER_IMAGE'
        resources = []
        for hs,raw in cc.solids(self.files['CARS/SYNTH/GEOMETRY.BIN']):
            raw = bytearray(raw)
            off,_ = cc.find(raw,0x134012)
            struct.pack_into('<I',raw,off+16,cc.name_hash(name))
            resources.append((hs,raw))
        self.files['CARS/SYNTH/GEOMETRY.BIN'] = xenon.indexed_pack(resources)
        self.files['GLOBAL/INGAMEA.BUN'] = deps.array_pack(name)[1]
        self.refresh()
        self.source.close()
        self.source = Source(self.root / 'x360')
        self.addCleanup(self.source.close)
        bundle = self.bundle()
        key = cs.path_hash('GLOBAL/INGAMEA.BUN')
        self.assertFalse(bundle.inputs[key]['catalogued'])
        result = cc.convert_x360(bundle,self.donor,self.root/'extra')
        self.assertEqual(result['inputs']['x360:'+key],digest(self.files['GLOBAL/INGAMEA.BUN']))
        del bundle.inputs[key]
        del bundle._reader.inputs[key]
        with self.assertRaisesRegex(cc.FormatError,'closure incomplete'):
            cc.convert_x360(bundle,self.donor,self.root/'bad-extra')

    def test_quarantine_and_source_normalized_inputs_unchanged(self):
        bundle = self.bundle()
        before = copy.deepcopy(bundle.models)
        textures = copy.deepcopy(bundle.textures)
        cc.convert_x360(bundle, self.donor, self.root/'good')
        for name,m in before.items():
            np.testing.assert_array_equal(m['vertices'], bundle.models[name]['vertices'])
            self.assertEqual(m['header'],bundle.models[name]['header'])
        self.assertEqual(textures,bundle.textures)
        for where in ('extras','x360_opaque'):
            bundle = self.bundle()
            next(iter(bundle.models.values())).setdefault(where, []).append((0x134017,b'opaque'))
            with self.assertRaisesRegex(cc.FormatError, 'opaque stream'):
                cc.convert_x360(bundle,self.donor,self.root/'bad')

    def install_lighting(self, name):
        key = cc.name_hash(name)
        source_row = lighting_fixture.fixture(name)
        mw_row = lighting_fixture.fixture(name, False)
        source_global = cc.chunk(0x135200, source_row) + deps.global_chunks()[244:]
        self.files['GLOBAL/GLOBALB.LZC'] = source_global + deps.array_pack()[1]
        self.files['GLOBAL/GLOBALB.BUN'] = self.files['GLOBAL/GLOBALB.LZC']
        resources = []
        for hs, raw in cc.solids(self.files['CARS/SYNTH/GEOMETRY.BIN']):
            raw = bytearray(raw)
            off, _ = cc.find(raw, 0x134013)
            struct.pack_into('<I', raw, off+8, key)
            resources.append((hs,raw))
        self.files['CARS/SYNTH/GEOMETRY.BIN'] = xenon.indexed_pack(resources)
        self.refresh()
        (self.pc_global/'GLOBALB.LZC').write_bytes(source_global)
        raw = bytearray((self.pc/'GEOMETRY.BIN').read_bytes())
        for cid,off,size in cc.walk(raw):
            if cid == 0x134013: struct.pack_into('<I',raw,off+8,key)
        (self.pc/'GEOMETRY.BIN').write_bytes(raw)
        with (self.root/'mw/GLOBAL/GLOBALA.BUN').open('ab') as f:
            f.write(cc.chunk(0x135200,mw_row))
        return source_row, mw_row

    def test_lighting_policy_both_platforms(self):
        source_row, mw_row = self.install_lighting('HEADLIGHTGLASS')
        for platform in ('pc','x360'):
            result = self.run_conversion(platform, platform+'-lamp', lighting={'lamps':True})
            data = (self.root/(platform+'-lamp')/'GEOMETRY.BIN').read_bytes()
            end = struct.unpack_from('<I',data,4)[0]+8
            rows = list(chunks(data,end))
            self.assertEqual(len(rows),1)
            emitted = contents(data,rows,0x135200)
            self.assertEqual(emitted[48:],mw_row[48:])
            self.assertEqual(emitted[:8],bytes(8))
            lamp = cc.name_hash('CARBON_LAMP_HEAD')
            self.assertEqual(struct.unpack_from('<I',emitted,12)[0],lamp)
            for m in geometry_components(data).values():
                self.assertEqual(struct.unpack_from('<I',m['lights'])[0],lamp)
            off = self.run_conversion(platform, platform+'-off', lighting={'lamps':False})
            self.assertNotEqual(result['outputs']['GEOMETRY.BIN'],off['outputs']['GEOMETRY.BIN'])
            self.assertNotEqual(result['converter_policy']['sha256'],off['converter_policy']['sha256'])
            default = self.run_conversion(platform, platform+'-default')
            with self.subTest(platform=platform, policy='default-off'):
                self.assertEqual(default['outputs'], off['outputs'])
                self.assertEqual(default['converter_policy'], off['converter_policy'])
            with self.assertRaisesRegex(cc.FormatError,'explicit lighting gains'):
                self.run_conversion(platform,platform+'-bad-gains',lighting={'amplitudes':True})

    def test_source_brakelight_levels_both_platforms(self):
        source, mw = self.install_lighting('BRAKELIGHT')
        source = bytearray(source)
        struct.pack_into('<f', source, 0x64, 3.)
        struct.pack_into('<f', source, 0x74, 6.)
        struct.pack_into('<f', source, 0x8c, 10.)
        # Update only the checked source material row, leaving its other chunks.
        for path in (self.pc_global / 'GLOBALB.LZC',):
            path.write_bytes(cc.chunk(0x135200, source) + path.read_bytes()[244:])
        for key in ('GLOBAL/GLOBALB.LZC', 'GLOBAL/GLOBALB.BUN'):
            self.files[key] = cc.chunk(0x135200, source) + self.files[key][244:]
        self.refresh()
        # The default compatibility frame is the matching MW GLOBALB row.
        with (self.root / 'mw/GLOBAL/GLOBALB.LZC').open('ab') as f:
            f.write(cc.chunk(0x135200, mw))
        used = {cc.name_hash('CARBON_BRAKELIGHT')}
        row = cc.chunk(0x135200, source)
        native = cc.chunk(0x135200, mw)
        bad = bytearray(source)
        struct.pack_into('<f', bad, 0x64, float('nan'))
        for malformed in (b'', row + row, cc.chunk(0x135200, bad)):
            with self.assertRaises(cc.FormatError):
                cc.source_light_rows(malformed, native, used)
        for platform in ('pc', 'x360'):
            result = self.run_conversion(platform, platform + '-brake')
            data = (self.root / (platform + '-brake') / 'GEOMETRY.BIN').read_bytes()
            rows = {cc.u32(data, o+20): data[o+8:o+8+z]
                    for c, o, z in cc.walk(data) if c == 0x135200}
            identity = cc.name_hash('CARBON_BRAKELIGHT')
            self.assertEqual(set(rows), {identity})
            self.assertEqual(rows[identity][0x30:0x5c], source[0x64:0x90])
            self.assertEqual(rows[identity][0x5c:], mw[0x5c:])
            for model in cc.validate_geometry(data).values():
                self.assertEqual(model['lights'], [identity])
            self.assertEqual(result['lighting']['rows'], [identity])
            translated = self.run_conversion(platform, platform + '-brake-amplitudes',
                lighting={'amplitudes': True, 'gains': cl.Gains(.25, 1, 1)})
            expected = cl.policy({cc.name_hash('BRAKELIGHT'): bytes(source)},
                {cc.name_hash('BRAKELIGHT'): mw}, [cc.name_hash('BRAKELIGHT')],
                namespace='NFSAD_SYNTH', amplitudes=True, gains=cl.Gains(.25, 1, 1))
            self.assertEqual(translated['lighting']['rows'], sorted(expected.rows))
            self.assertNotIn(identity, translated['lighting']['rows'])
        print('source brake levels: PC/X360 3/6, power 10; explicit amplitude policy retained')

    def test_unexported_details_without_paint_gate(self):
        for label in (b'EXHAUST\0', b'\0'):
            vertices = np.zeros((4, 36), dtype=np.uint8)
            vertices[:, 24:28] = [[0, 0, 0, 0], [0, 0, 0, 255], [12, 23, 34, 255], [0, 0, 0, 0]]
            before = vertices.copy()
            record = bytearray(104)
            struct.pack_into('<I', record, 60, len(vertices))
            model = dict(name='SYNTH_DETAIL_A', vertices=vertices, textures=[cc.name_hash('SYNTH_MISC')],
                         records=[record], names=[label])
            self.assertEqual(cc.map_vertex_lighting({'SYNTH_DETAIL_A': model}, 'SYNTH'), 2)
            np.testing.assert_array_equal(vertices[[0, 3], 24:28], 255)
            np.testing.assert_array_equal(vertices[1:3], before[1:3])
            np.testing.assert_array_equal(vertices[:, :24], before[:, :24])
            np.testing.assert_array_equal(vertices[:, 28:], before[:, 28:])
        print('unexported details: labelled/stripped 2 zero colours -> 0; authored dark alpha retained')

    def test_source_authored_rear_brake_both_platforms(self):
        (self.pc / 'GEOMETRY.BIN').write_bytes(pc.geometry('SYNTH', True,
            omit=('SYNTH_KIT00_REAR_BRAKE_A',)))
        self.files['CARS/SYNTH/GEOMETRY.BIN'] = xenon.indexed_pack([
            (hs, raw) for hs, raw in cc.solids(self.files['CARS/SYNTH/GEOMETRY.BIN'])
            if hs != cc.name_hash('SYNTH_KIT00_REAR_BRAKE_A')])
        self.refresh()
        for platform in ('pc', 'x360'):
            self.run_conversion(platform, platform + '-no-rear')
            data = (self.root / (platform + '-no-rear') / 'GEOMETRY.BIN').read_bytes()
            models = cc.validate_geometry(data)
            self.assertNotIn('NFSAD_SYNTH_KIT00_REAR_BRAKE_A', models)
            self.assertIn('NFSAD_SYNTH_KIT00_FRONT_BRAKE_A', models)
        # An authored rear must still survive assembly, and front stays mandatory.
        decoded = cc.decoded_models
        def omit_front(*args, **kwargs):
            models, assembled = decoded(*args, **kwargs)
            models.pop('DONOR_KIT00_FRONT_BRAKE_A')
            return models, assembled
        with mock.patch.object(cc, 'decoded_models', omit_front):
            for platform in ('pc', 'x360'):
                with self.assertRaisesRegex(cc.MissingStockPartError, 'FRONT_BRAKE_A'):
                    self.run_conversion(platform, platform + '-missing-front')
        (self.pc / 'GEOMETRY.BIN').write_bytes(pc.geometry('SYNTH', True))
        def omit_rear(*args, **kwargs):
            models, assembled = decoded(*args, **kwargs)
            models.pop('DONOR_KIT00_REAR_BRAKE_A')
            return models, assembled
        with mock.patch.object(cc, 'decoded_models', omit_rear):
            with self.assertRaisesRegex(cc.MissingStockPartError, 'REAR_BRAKE_A'):
                self.run_conversion('pc', 'missing-authored-rear')
        print('rear brake: two source omissions accepted, missing front/authored rear refused')

    def test_source_image_slots_with_stripped_material_labels(self):
        def entry(name):
            raw = bytearray(next(iter(cc.pc_texture_entries(pc.texture('X')).values()))[0])
            struct.pack_into('<I', raw, 12, cc.name_hash(name))
            raw[88:] = bytes(len(raw) - 88)
            raw[88] = len(name) + 1
            raw[89:90+len(name)] = name.encode() + b'\0'
            return bytes(raw), bytes(12) + b'DXT1' + bytes(8), bytes(range(8))
        images = {cc.name_hash(n): entry(n) for n in ('GRILL_06', 'ROTOR5', 'SILVER_SLATS')}
        normal = cc.name_hash('TIRE_STYLE01_N')
        images[normal] = entry('TIRE_STYLE01_N')
        records = []
        for i in range(3):
            record = bytearray(104)
            record[24], record[25] = i, 3
            records.append(bytes(record))
        models = {'SYNTH_BODY_A': dict(textures=list(images), records=records, names=[b'\0']*3)}
        def resources(**kwargs):
            return cc.material_resources(None, None, pc.texture('DONOR', 5), 'SYNTH', 'DONOR',
                b'', set(), set(), models=models, textures=kwargs.pop('textures', {}),
                shared_textures=images, **kwargs)
        aliases, extras = resources()
        self.assertEqual(extras, images)
        for image, role in zip(('ROTOR5', 'GRILL_06', 'SILVER_SLATS'),
                               ('DOOR_HANDLE', 'SIDELIGHT', 'LOGO')):
            self.assertEqual(aliases[cc.name_hash(image)], (cc.name_hash('DONOR_'+role), 'DONOR_'+role))
        self.assertEqual(aliases[normal][1], 'DONOR_TIRE_N')
        occupied = cc.name_hash('SYNTH_DOOR_HANDLE')
        aliases, _ = resources(textures={occupied: entry('SYNTH_DOOR_HANDLE')})
        self.assertEqual(aliases[cc.name_hash('ROTOR5')][1], 'DONOR_SIDELIGHT')
        for role in ('SIDELIGHT', 'LOGO'):
            models['SYNTH_BODY_A']['textures'].append(cc.name_hash('SYNTH_'+role))
        with self.assertRaisesRegex(cc.FormatError, 'more source-only images'):
            resources(textures={occupied: entry('SYNTH_DOOR_HANDLE')})
        print('source images: three requested slots, stripped material labels, exact entries; occupancy refuses overflow')

    def test_stripped_labels_keep_classified_illumination(self):
        def triangle(name, texture_role, label, zero_alpha=False):
            m = cc.model(pc.triangle(name,True,texture_role=texture_role))
            m['names'] = [label+b'\0']
            m['vertices'][:,24:27] = 0
            m['vertices'][:,27] = 0 if zero_alpha else 255
            return m
        rim = triangle('SYNTH_KIT00_FRONT_TIRE_A','TIRE',b'RIM_SILVER')
        body = triangle('SYNTH_KIT00_BODY_A','SKIN1',b'BODY',True)
        rubber = triangle('SYNTH_KIT00_REAR_TIRE_A','RUBBER',b'RUBBER')
        stripped = {m['name']:copy.deepcopy(m) for m in (rim,body,rubber)}
        for m in stripped.values(): m['names'] = [b'\0']
        labelled = {m['name']:copy.deepcopy(m) for m in (rim,body,rubber)}
        cc.map_vertex_lighting(labelled)
        cc.map_vertex_lighting(stripped,'SYNTH')
        for name,m in stripped.items():
            np.testing.assert_array_equal(m['vertices'],labelled[name]['vertices'])
        self.assertTrue((stripped[rubber['name']]['vertices'][:,24:27] == 0).all())
        self.assertTrue((stripped[rim['name']]['vertices'][:,24:27] == 255).all())
        self.assertTrue((stripped[body['name']]['vertices'][:,24:28] == 255).all())

    def test_amplitude_policy_and_dependency_identity(self):
        for platform in ('pc','x360'):
            if platform == 'pc':
                raw = bytearray((self.pc/'GEOMETRY.BIN').read_bytes())
                for cid,off,size in cc.walk(raw):
                    if cid == 0x134013: struct.pack_into('<I',raw,off+8,cc.name_hash('SYNTH_MATERIAL'))
                (self.pc/'GEOMETRY.BIN').write_bytes(raw)
            result = self.run_conversion(platform,platform+'-amplitudes',
                lighting={'amplitudes':True,'gains':cl.Gains(.25,1,1)})
            self.assertTrue(result['lighting']['rows'])
            expected = {'car_convert.py','car_texture_namespace.py','huff.py','car_lighting.py','car_paint.py',
                        'car_physics.py','car_grip.py','car_sources.py','x360.py','xdvdfs.py'}
            if platform == 'x360': expected |= {'car_x360.py','car_x360_inputs.py','x360_tex.py','texture_compare.py'}
            self.assertEqual(set(result['converter_policy']['modules']),expected)
            self.assertEqual(len(result['dependencies']),len(result['inputs']))
            for key,row in result['source_inputs'].items():
                self.assertEqual(result['dependencies']['source:'+key],row)

    def test_stock_resource_and_post_write_controls(self):
        for mutation, error in (('stock',cc.MissingStockPartError),('resource',cc.FormatError)):
            bundle = self.bundle()
            if mutation == 'stock':
                del bundle.models['SYNTH_BASE_A']
            else:
                next(iter(bundle.models.values()))['textures'][0] = cc.name_hash('ABSENT_IMAGE')
            with self.subTest(mutation=mutation), self.assertRaises(error):
                cc.convert_x360(bundle,self.donor,self.root/'bad')
        bundle = self.bundle()
        original = cc.write_outputs
        def changed(out,files,protected):
            original(out,files,protected)
            path = self.root/'x360/GLOBAL/GLOBALB.BUN'
            path.write_bytes(path.read_bytes()+b'changed')
        with mock.patch.object(cc,'write_outputs',changed), self.assertRaises(cc.InputChangedError):
            cc.convert_x360(bundle,self.donor,self.root/'post-change')

    def test_written_mutants_every_output(self):
        for platform in ('pc', 'x360'):
            report = self.run_conversion(platform, platform+'-written')
            directory = self.root/(platform+'-written')
            for name in report['outputs']:
                path = directory/name
                original = path.read_bytes()
                path.write_bytes(original[:-1]+bytes([original[-1]^1]))
                with self.subTest(platform=platform,name=name), self.assertRaises(AssertionError):
                    self.audit(directory,report)
                path.write_bytes(original)


def driver_normals(models, name):
    """Independent inverse-transpose/normalization oracle for mounted normals."""
    driver = models[name]
    roof = name.replace('_DRIVER_','_ROOF_')
    anchor = models.get(roof,models.get(roof.rsplit('_',1)[0]+'_A'))
    matrices = [raw[i+16:i+80] for kind,raw in anchor['extras'] if kind == 0x13401a
                for i in range(0,len(raw),80) if struct.unpack_from('<I',raw,i)[0] == cc.name_hash('DRIVER_POSITION')] if anchor else []
    vertices = driver['vertices'].copy().view('<f4').reshape(-1,9)
    normals = vertices[:,3:6].astype(np.float64)
    ix = driver['indices']
    if matrices:
        linear = np.frombuffer(matrices[0],'<f4').reshape(4,4)[:3,:3].astype(float)
        # Solve L*n_out = n_in for each source row; independent of the writer's einsum.
        normals = np.linalg.solve(linear,normals.T).T
        normals /= np.linalg.norm(normals,axis=1)[:,None]
        if np.linalg.det(linear) < 0: ix = ix.reshape(-1,3)[:,[0,2,1]].reshape(-1)
    return normals.astype('<f4')[ix]


def asset_main(out=ROOT/'.metal-cache/cw-assets'):
    """Write and independently inspect local outputs; preserve a hash manifest."""
    names = ('NFS_CARBON_PC','NFS_CARBON_X360','NFS_CARBON_CARS','NFS_GAME')
    if any(not os.environ.get(n) for n in names):
        print('SKIP: supply local PC/X360/MW paths and selected cars')
        return 3
    pc_root, xroot, cars, mw = (os.environ[n] for n in names)
    pc_root, xroot, mw = (Path(p).expanduser().resolve() for p in (pc_root,xroot,mw))
    cars = cars.split()
    donors = os.environ.get('NFS_CARBON_DONORS',' '.join(cars)).split()
    if (not cars or len(cars) != len(donors) or not xroot.exists() or
        any(not (pc_root/'CARS'/car/'GEOMETRY.BIN').is_file() or
            not (mw/'CARS'/donor/'GEOMETRY.BIN').is_file() for car,donor in zip(cars,donors))):
        print('SKIP: selected local car/donor inputs are absent')
        return 3
    out.mkdir(parents=True,exist_ok=True)
    recognition = cs.recognise(xroot)
    source = Source(xroot)
    try:
        for car,donor in zip(cars,donors):
            bundle = ci.resolve(source,car,donor,recognition=recognition)
            reports, models = {}, {}
            native = {m['name']:m for _,raw in cc.solids((pc_root/'CARS'/car/'GEOMETRY.BIN').read_bytes()) for m in [cc.model(raw)]}
            for platform in ('pc','x360'):
                hashes = None
                for run in range(2):
                    directory = out/car/(platform+str(run))
                    result = (cc.convert(pc_root/'CARS'/car,mw/'CARS'/donor,pc_root/'GLOBAL',directory)
                        if platform == 'pc' else cc.convert_x360(bundle,mw/'CARS'/donor,directory))
                    actual = {p.name:digest(p.read_bytes()) for p in directory.iterdir()}
                    assert actual == result['outputs'], 'actual output hashes differ'
                    assert hashes is None or hashes == actual, 'nondeterministic written output'
                    hashes = actual
                    geo = geometry_components((directory/'GEOMETRY.BIN').read_bytes())
                    tex = texture_components((directory/'TEXTURES.BIN').read_bytes())
                    assert len(geo) == result['solids'] and len(tex) == result['textures']
                    assert not result['approved']
                    assert {'GEOMETRY.BIN','TEXTURES.BIN','SOLIDMAP.BIN','WHEELFIT.BIN','PAINT.BIN'} <= actual.keys()
                    for path, expected in result['inputs'].items():
                        if not path.startswith('x360:'): assert cc.file_hash(Path(path)) == expected
                    bundle.check_unchanged()
                    for filename,magic in (('SOLIDMAP.BIN',0x31504D53),('WHEELFIT.BIN',0x31544657),
                        ('PAINT.BIN',0x31545044),('PHYSICS.BIN',0x31534850),('COLLISION.BIN',0x31544C43)):
                        if filename in actual:
                            raw = (directory/filename).read_bytes()
                            assert struct.unpack_from('<I',raw)[0] == magic
                            if filename == 'SOLIDMAP.BIN':
                                assert len(raw) == 16+8*struct.unpack_from('<I',raw,12)[0]
                            elif filename == 'WHEELFIT.BIN': assert len(raw) in (116,380)
                            elif filename in ('PHYSICS.BIN','PAINT.BIN'): assert len(raw) == (36 if filename.startswith('PHYSICS') else 32)
                            else: assert len(raw) == 20+struct.unpack_from('<I',raw,16)[0]
                    reports[platform+str(run)] = result
                    models[platform] = geo
                print(car,platform,len(geo),'solids',len(tex),'textures',len(actual),'written hashes; deterministic',flush=True)
            (out/car/'reports.json').write_text(json.dumps(reports,sort_keys=True,indent=2)+'\n')
            # Compare indexed components, allowing only measured format precision.
            x,p = models['x360'],models['pc']
            assert x.keys() == p.keys(), 'written model identity sets differ: '+str(sorted(x.keys() ^ p.keys()))
            maxima = np.zeros(3)
            for name in x:
                a,b = x[name],p[name]
                av,bv = a['vertices'][a['indices']],b['vertices'][b['indices']]
                assert av.shape == bv.shape, name+': written indexed count differs'
                af,bf = av.copy().view('<f4').reshape(-1,9),bv.copy().view('<f4').reshape(-1,9)
                errors = np.array([np.abs(af[:,:3]-bf[:,:3]).max(),np.abs(af[:,3:6]-bf[:,3:6]).max(),
                                   np.abs(af[:,7:]-bf[:,7:]).max()])
                if '_DRIVER_' in name:
                    source_name = car + name[len('NFSAD_'+car):]
                    ax = driver_normals(bundle.models,source_name)
                    bp = driver_normals(native,source_name)
                    np.testing.assert_array_equal(af[:,3:6],ax)
                    np.testing.assert_array_equal(bf[:,3:6],bp)
                    np.testing.assert_array_equal(af[:,3:6]-bf[:,3:6],ax-bp)
                    print(name,'normal delta after independently verified seat bake',float(errors[1]),flush=True)
                    assert errors[0] <= 1e-6 and errors[2] <= .0015,name+': written position/UV error'
                else:
                    assert (errors <= [1e-6,1.6e-5,.0015]).all(), name+': written component error '+str(errors)
                assert np.array_equal(av[:,24:28],bv[:,24:28]),name+': written colours differ'
                maxima = np.maximum(maxima,errors)
            (out/car/'reports.json').write_text(json.dumps(reports,sort_keys=True,indent=2)+'\n')
            print(car,'written indexed position/normal/UV maxima',maxima.tolist(),flush=True)
    finally:
        source.close()
    print('CAR PLATFORM PARITY PASS: ' + ', '.join(cars))
    return 0


if __name__ == '__main__':
    if '--original-setup-rules' in sys.argv:
        sys.argv.remove('--original-setup-rules')
        for name, sha, body in ORIGINAL_SETUP_RULES:
            assert digest(body.encode()) == sha
            scope = {}
            exec(compile(body, '<original-setup-rules>', 'exec'), vars(cc), scope)
            setattr(cc, name, scope[name])
    if '--assets' in sys.argv:
        raise SystemExit(asset_main())
    if '--original-grip-policy' in sys.argv:
        sys.argv.remove('--original-grip-policy')
        assert digest(ORIGINAL_GRIP_POLICY.encode()) == ORIGINAL_GRIP_POLICY_SHA
        original = {}
        exec(compile(ORIGINAL_GRIP_POLICY, '<original-grip-policy>', 'exec'), vars(cc), original)
        with mock.patch.object(cc, '_converter_policy', original['_converter_policy']):
            suite = unittest.TestSuite([PlatformConversion('test_amplitude_policy_and_dependency_identity')])
            result = unittest.TextTestRunner().run(suite)
            raise SystemExit(not result.wasSuccessful())
    unittest.main()
