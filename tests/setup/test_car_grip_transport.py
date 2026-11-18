"""GRP1 conversion transport: inherited counts, typed endian values and fallback.

Original converter fragments are SHA-256 pinned below, not read from history.
Original-function swap (--original): 0/8 tests pass, 38 failing subcases,
exit 1. Current: 9/9 tests pass, exit 0, including four pinned-original
publication controls; 28 malformed and four absent-metadata replacements.
All 34 independently keyed fields per synthetic conversion match their exact
counts/bytes, including nine CCX samples and R32's two final zero samples.
The asset check also converts pinned PC and BE CCX/R32 metadata through
all four writers; geometry/resource inputs remain independent synthetic data.
The independent synthetic schema has two inherited endpoints and upgrade
counter three. CCX's nine samples and R32's ten (including two final zeros)
are checked against the separately pinned authored vault when available.
No runtime admission or source-only semantic promotion is asserted.
"""
import contextlib
import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools_snapshot'), str(Path(__file__).parent)]
from setup_kit import car_convert as cc, car_grip as grip, car_x360_inputs as ci
from setup_kit import car_sources as cs
from setup_kit.input_paths import input_path
from setup_kit.x360 import Source
import carbon_namespace as private
import carbon_batch
import carbon_repair
import test_car_convert as pc_fixture
import test_car_x360_inputs as x_fixture

ORIGINAL_DECODED = r'''def _convert_decoded(*, car, donor, source, source_tex, models, textures, shared_raw,
                     attributes, mw_donor_dir, mw_root, out_dir, tracked, platform,
                     source_inputs, source_check, code, policy, lighting,
                     shared_textures=None, source_aliases=None, runtime_bindings=(),
                     runtime_materials=(), audio_pack=None):
    """One assembly, resource, sidecar and audited writer path for both platforms."""
    from . import car_lighting, car_paint, car_physics
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
        outputs={n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
        lighting=dict(rows=sorted(rows), dynamic=selected.dynamic, unsupported=selected.unsupported),
        material_policy='MW roles and MW-derived lamp rows; classified neutral vertex illumination')
'''
ORIGINAL_DECODED_SHA = '3e7b3aa0df7cc4638160a4413ccd8fd2e7423d23b7f7cb7b20d75d2246b95c31'

ORIGINAL_PRIVATE = r'''def convert(source, reference, destination, *, revision=2):
    import carbon_batch
    source, reference, destination, protected = cc.conversion_paths(source, reference, destination)
    cc.require(revision in (1, 2), 'unknown added-car conversion revision')
    namespace = 'NFSAD_' + source.name.upper() if revision >= 2 else None
    result = carbon_batch.convert_one(source, reference, destination, added=revision >= 2, namespace=namespace)
    if result.get('missing_stock'):
        return result
    geometry = (destination / 'GEOMETRY.BIN').read_bytes()
    textures = (destination / 'TEXTURES.BIN').read_bytes()
    geometry, textures, sidecar = isolate(geometry, textures, reference.name.upper(), source.name.upper(),
                                         (reference / 'GEOMETRY.BIN').read_bytes() if revision >= 2 else None,
                                         result.pop('texture_namespace', ()))
    from carbon_repair import wheel_sidecar, collision_sidecar
    files = {'GEOMETRY.BIN': geometry, 'TEXTURES.BIN': textures, 'SOLIDMAP.BIN': sidecar}
    if revision >= 2:
        files['WHEELFIT.BIN'] = wheel_sidecar(source, reference.name.upper())
        files['COLLISION.BIN'] = collision_sidecar(source, reference.name.upper())
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        from setup_kit.car_physics import metadata as physics_metadata
        attributes = source.parents[1] / 'GLOBAL/attributes.bin'
        if attributes.is_file():
            physics = physics_metadata(attributes.read_bytes(), source.name, reference.name)
            if physics is not None: files['PHYSICS.BIN'] = physics
        from setup_kit.car_paint import metadata
        from setup_kit.car_convert import unpack_file
        files['PAINT.BIN'], result['default_paint'] = metadata(
            unpack_file((source.parents[1] / 'GLOBAL/GLOBALB.LZC').read_bytes()),
            lambda: unpack_file((reference.parents[1] / 'GLOBAL/GLOBALB.LZC').read_bytes()),
            source.name.upper(), reference.name.upper(),
            (source.parents[1] / 'GLOBAL/attributes.bin').read_bytes()
            if (source.parents[1] / 'GLOBAL/attributes.bin').is_file() else None)
    cc.write_outputs(destination, files, protected)
    result['namespace'] = 'NFSAD_' + source.name.upper()
    return result
'''
ORIGINAL_PRIVATE_SHA = '505025b12d513a9e742852d015e190740712344fd09917ee4690f63f49bb3a2b'

ORIGINAL_X360 = r'''def convert_x360(bundle, mw_donor_dir, out_dir, *, mw_root=None, lighting=None):
    """Convert a live D-normalized Bundle through the common audited writer.

    Keep its Source open until return. Bundle dependencies, including additional
    noncatalogued reads, participate in the returned source hashes. Neither
    source recognition nor conversion confers output/appearance approval.
    """
    from . import car_x360_inputs as ci, car_sources as cs
    try:
        require(isinstance(bundle, ci.Bundle) and isinstance(bundle._reader, ci.LogicalReader),
                'X360 conversion needs a normalized X360 bundle')
        mw_donor_dir, out_dir = (Path(p).expanduser().resolve() for p in (mw_donor_dir, out_dir))
        mw_root = Path(mw_root).expanduser().resolve() if mw_root is not None else mw_donor_dir.parent.parent
        car, donor = bundle.car, mw_donor_dir.name.upper()
        require(all(re.fullmatch(r'[A-Z0-9_]{1,15}', n) for n in (car, donor)), 'invalid car or donor identity')
        source_path = bundle._reader.source.path.resolve()
        tracked = _Inputs(out_dir, (source_path if source_path.is_dir() else source_path.parent, mw_root, mw_donor_dir))
        code = _Inputs(out_dir, ())
        policy, options = _converter_policy('x360', lighting, code)
        def check_source():
            require(bundle.inputs == bundle._reader.inputs, 'X360 omitted or altered dependency')
            require(bundle.inputs.keys() == bundle._reader.expected.keys(), 'X360 dependency closure incomplete')
            require(all(row['size'] == bundle._reader.expected[key]['size'] and
                        row['sha256'] == bundle._reader.expected[key]['sha256']
                        for key, row in bundle.inputs.items()), 'X360 dependency fingerprint differs')
            required = [f'CARS/{car}/GEOMETRY.BIN', f'CARS/{car}/TEXTURES.BIN',
                        'GLOBAL/GLOBALB.LZC', 'GLOBAL/GLOBALB.BUN', 'GLOBAL/ATTRIBUTES.BIN', 'CARS/TEXTURES.BIN']
            require(all(cs.path_hash(n) in bundle.inputs for n in required), 'X360 dependency closure incomplete')
            require(hashlib.sha256(bundle.geometry).hexdigest() == bundle.inputs[
                cs.path_hash(f'CARS/{car}/GEOMETRY.BIN')]['sha256'], 'X360 bundle geometry differs from input')
            bundle.check_unchanged()
        check_source()
        require(bundle.models and all(n.startswith(car + '_') for n in bundle.models), 'X360 model car identity')
        require(not bundle.textures.keys() & bundle.shared_textures.keys(), 'X360 duplicate local/shared texture')
        require(all(not m.get('x360_opaque') and not any(cid in ci.EXCLUDED_STREAMS for cid, _ in m['extras'])
                    for m in bundle.models.values()), 'X360 opaque stream in output extras')
        for hs, entry in (bundle.textures | bundle.shared_textures).items():
            validate_texture(*entry, 8, hs)
        return _convert_decoded(car=car, donor=donor, source=bundle.geometry, source_tex=None,
            models=bundle.models, textures=bundle.textures, shared_raw=bundle.global_data,
            attributes=bundle.attributes, mw_donor_dir=mw_donor_dir, mw_root=mw_root, out_dir=out_dir,
            tracked=tracked, platform='x360', source_inputs=lambda: copy.deepcopy(bundle.inputs),
            source_check=check_source, code=code, policy=policy, lighting=options,
            shared_textures=bundle.shared_textures, source_aliases=bundle.texture_aliases,
            runtime_bindings=bundle.runtime_bindings, runtime_materials=bundle.runtime_materials)
    except ConversionError:
        raise
    except (OSError, ValueError, KeyError, IndexError, StopIteration, struct.error, UnicodeError, np.linalg.LinAlgError) as exc:
        raise FormatError('invalid conversion input: ' + str(exc)) from exc
'''
ORIGINAL_X360_SHA = '4e665bb59ee322e92f5b1f122569fb4f61eeb363e6d854552df32bcdb4afd9c7'

# Independent field identities/shapes; no production FIELDS iteration builds
# expected coverage, counts, samples or semantic flags.
TYRES = (
    ('SECTION_WIDTH', 2, 1, 6), ('YAW_CONTROL', 4, 4, 7),
    ('DYNAMIC_GRIP', 2, 1, 6), ('RIM_SIZE', 2, 1, 6),
    ('YAW_SPEED', 1, 1, 6), ('ASPECT_RATIO', 2, 1, 6),
    ('STATIC_GRIP', 2, 1, 6), ('GRIP_SCALE', 2, 1, 6),
    ('STEERING', 1, 1, 6), ('DRIFT_COUNTER_STEER_REDUCTION', 1, 1, 6),
    (0x7CAFC471, 7, 40, 5), ('DRIFT_COUNTER_STEER_BUILDUP', 1, 1, 6),
    ('DRIFT_MAINTAIN_SPEED_FORCE', 1, 1, 6), ('DRIFT_YAW_CONTROL', 9, 65535, 5),
    (0xD15B2BDE, 10, 10, 7), ('DRIFT_LATERAL_FORCE', 1, 1, 6),
    ('DRIFT_ADDITIONAL_YAW_CONTROL', 6, 65535, 5),
)
CCX_YAW = (.2, .45, .55, .85, 1, 1.2, 1.4, 1.5, 1.65)
R32_YAW = (.2, .3, .4, .5, .6, .85, 1, 1, 0, 0)


def source_attributes(endian='<', fault=None):
    """Independent mixed layout/sparse VPAK; both endpoints inherit all fields.

    Existing dimension/render fixtures supply only unrelated conversion inputs.
    The schema and values below supply the complete tyre transport oracle.
    """
    h = cc.hash32
    if fault == 'absent':
        return x_fixture.attrs(endian, layout=True), []
    base = cc.FitAttributes(x_fixture.attrs('<', layout=True))
    schemas = copy.deepcopy(base.defs)
    rows = []
    for (cls, key), (vault, off) in base.collections.items():
        if cls == h('tires'):
            continue
        values = {field: grip.raw_field(base, cls, key, field) for field in schemas[cls]}
        if cls == h('pvehicle'):
            # The car inherits both references and the independent counter.
            rows.append((cls, key, h('vehicle_parent'), {}))
            key = h('vehicle_parent')
            values[h('tires')] = struct.pack('<6I', h('tires'), 101, 0, h('tires'), 201, 0)
            values[h('tires_upgrades')] = struct.pack('<I', 3)
        rows.append((cls, key, 0, values))
    schemas[h('pvehicle')][h('tires')] = (0, 0, 12, 8, 1, 4)
    schemas[h('pvehicle')][h('tires_upgrades')] = (0, 0, 4, 1, 0, 4)
    tyre_defs, expected = {}, []
    at = 0
    for name, n, capacity, flags in TYRES:
        field = h(name) if isinstance(name, str) else name
        size = 8 if n == 2 and capacity == 1 else 4
        tyre_defs[field] = (0x4CB36381 if size == 8 else 0x3C16EC5E,
                            at if flags & 2 else 0, size, capacity, flags, 2)
        if flags & 2:
            at += (8 if flags & 1 else 0) + size * capacity
    schemas[h('tires')] = tyre_defs
    for endpoint, yaw in enumerate((CCX_YAW, R32_YAW)):
        values = {}
        for i, (name, n, _, _) in enumerate(TYRES):
            field = h(name) if isinstance(name, str) else name
            samples = yaw if name == 'DRIFT_YAW_CONTROL' else tuple(
                (i + 1) * .125 + endpoint * .5 + j * .0625 for j in range(n))
            values[field] = struct.pack('<' + 'f' * len(samples), *samples)
        expected.append(dict(values))
        if endpoint == 1:
            if fault == 'missing': values.pop(h('DRIFT_YAW_CONTROL'))
            if fault == 'empty': values[h('DRIFT_YAW_CONTROL')] = b''
            if fault == 'nan': values[h('DRIFT_YAW_CONTROL')] = struct.pack('<f', float('nan'))
            if fault == 'oversize': values[h('DRIFT_YAW_CONTROL')] = bytes(41 * 4)
        parent, child = 100 + endpoint * 100, 101 + endpoint * 100
        rows += [(h('tires'), parent, 0, values), (h('tires'), child, parent, {})]
    if fault == 'schema':
        schemas[h('tires')][h('UNKNOWN_FIELD')] = (0x3C16EC5E, at, 4, 1, 6, 2)
    if fault == 'reference':
        next(v for c, k, p, v in rows if k == h('vehicle_parent'))[h('tires')] = struct.pack('<6I', h('tires'), 101, 0, h('tires'), 201, 1)
    if fault == 'cycle':
        rows[-1] = (h('tires'), 201, 201, {})
    def vc(tag, data):
        return (tag if endian == '>' else tag[::-1]) + struct.pack(endian + 'I', len(data) + 8) + data
    prefix = vc(b'Vers', bytes(8)) + vc(b'DepN', struct.pack(endian + '3I', 2, 0, 0))
    start = len(prefix) + 8
    binary, body, exports, fixups = bytearray(), bytearray(), [], []
    for cls, defs in schemas.items():
        pos = len(binary)
        binary += b''.join(struct.pack(endian + 'IIHHHBB', field, *spec) for field, spec in defs.items())
        off = start + len(body)
        header = bytearray(24)
        struct.pack_into(endian + 'I', header, 8, len(defs))
        body += header
        exports.append((cls, 0x5E970CBC, 24, off))
        fixups.append((off + 12, 3, 1, pos))
    for cls, key, parent, values in rows:
        off = start + len(body)
        header = bytearray(32)
        sparse = [f for f in values if not schemas[cls][f][4] & 2]
        struct.pack_into(endian + '3I', header, 0, key, cls, parent)
        struct.pack_into(endian + 'I', header, 20, len(sparse))
        body += header + bytes(12 * len(sparse))
        exports.append((key, 0x8E112EB7, 32 + 12 * len(sparse), off))
        layout_size = max((s[1] + (8 if s[4] & 1 else 0) + s[2] * s[3]
                           for s in schemas[cls].values() if s[4] & 2), default=0)
        layout = len(binary)
        if values and layout_size:
            binary += bytes(layout_size)
            fixups.append((off + 28, 3, 1, layout))
        for field, value in values.items():
            _, offset, size, _, flags, _ = schemas[cls][field]
            # All tyre/pvehicle fields are 32-bit typed; wheel bytes remain bytes.
            unit = 1 if cls == h('ecar') and size == 1 else 4
            raw = b''.join(value[i:i+unit][::-1] for i in range(0, len(value), unit)) if endian == '>' else value
            payload = (struct.pack(endian + '4H', len(raw)//size, len(raw)//size, size, 0) if flags & 1 else b'') + raw
            if flags & 2:
                binary[layout+offset:layout+offset+len(payload)] = payload
            else:
                entry = off + 32 + sparse.index(field) * 12
                struct.pack_into(endian + 'IIHBB', body, entry-start, field, 0, 0, 2 if flags & 1 else 0, 0)
                fixups.append((entry+4, 3, 1, len(binary)))
                binary += payload
    vault = prefix + vc(b'DatN', body)
    vault += vc(b'ExpN', struct.pack(endian + 'I', len(exports)) + b''.join(struct.pack(endian + '4I', *row) for row in exports))
    vault += vc(b'PtrN', b''.join(struct.pack(endian + 'IHHI', *row) for row in fixups) + bytes(8))
    names = b'synthetic\0'
    bo = 36 + len(names)
    data = b'VPAK' + struct.pack('<3I', 1, 36, len(names))
    data += struct.pack('<5I', 0, len(binary), len(vault), bo, bo+len(binary)) + names + binary + vault
    return data, expected


def assert_record(test, blob, expected):
    test.assertEqual(struct.unpack_from('<10I', blob),
                     (0x31505247, 1, len(blob), cc.hash32('synth'), cc.hash32('donor'), 1, 2, 3, 17, 0))
    pos = 40
    shared = {cc.hash32(name) for name, _, _, _ in TYRES[:9]}
    for ordinal, oracle in enumerate(expected):
        test.assertEqual(struct.unpack_from('<2I', blob, pos), (ordinal, 101 + ordinal * 100))
        pos += 8
        decoded = {}
        for _ in range(17):
            field, semantic, count, reserved = struct.unpack_from('<4I', blob, pos)
            test.assertNotIn(field, decoded)
            test.assertEqual((semantic, reserved), (int(field in shared), 0))
            decoded[field] = blob[pos+16:pos+16+4*count]
            pos += 16+4*count
        test.assertEqual(decoded, oracle)
    test.assertEqual(pos, len(blob))


@contextlib.contextmanager
def original_function(module, fragment, digest):
    assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    namespace = {}
    exec(compile(fragment, '<pinned-original>', 'exec'), vars(module), namespace)
    name = fragment.split('def ', 1)[1].split('(', 1)[0]
    original = namespace[name]

    def decoded_with_stock_writer(*args, writer=None, **kwargs):
        assert writer is None
        return original(*args, **kwargs)

    with mock.patch.object(module, name, decoded_with_stock_writer if name == '_convert_decoded' else original):
        yield


class GripTransport(unittest.TestCase):
    def setUp(self):
        self.fixture = pc_fixture.CarConversion()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        f = self.fixture
        target = f.car.with_name('SYNTH')
        f.car.rename(target)
        f.car = target
        (f.car/'GEOMETRY.BIN').write_bytes(pc_fixture.geometry('SYNTH', True))
        (f.car/'TEXTURES.BIN').write_bytes(pc_fixture.texture('SYNTH'))
        self.expected = None

    def pc(self, fault=None):
        raw, self.expected = source_attributes(fault=fault)
        (self.fixture.shared/'attributes.bin').write_bytes(raw)
        return self.fixture.convert()

    def xenon(self, fault=None, attributes=None):
        f = self.fixture
        raw, self.expected = source_attributes('>', fault) if attributes is None else (attributes, None)
        car = f.car.name
        geometry = (f.car/'GEOMETRY.BIN').read_bytes()
        textures = (f.car/'TEXTURES.BIN').read_bytes()
        files = {f'CARS/{car}/GEOMETRY.BIN': geometry, f'CARS/{car}/TEXTURES.BIN': textures,
                 'GLOBAL/GLOBALB.LZC': b'', 'GLOBAL/GLOBALB.BUN': b'',
                 'GLOBAL/ATTRIBUTES.BIN': raw, 'CARS/TEXTURES.BIN': b''}
        root = f.root/'x360'
        x_fixture.write_files(root, files)
        expected = [dict(path_hash=cs.path_hash(n), size=len(v), sha256=hashlib.sha256(v).hexdigest()) for n,v in files.items()]
        source = Source(root)
        try:
            reader = ci.LogicalReader(source, expected)
            for name in files: reader.read(name)
            attributes = ci.normalize_attributes(raw, car)
            bundle = ci.Bundle(car, geometry, cc.validate_geometry(geometry, carbon=True),
                cc.pc_texture_entries(textures), {}, {}, {}, b'', {}, {}, frozenset(),
                attributes, {}, b'', b'', None, {}, None, b'', dict(reader.inputs), reader)
            return cc.convert_x360(bundle, f.donor, f.out)
        finally:
            source.close()

    def private(self, fault=None, revision=2, attributes=None):
        f = self.fixture
        raw, self.expected = source_attributes(fault=fault) if attributes is None else (attributes, None)
        (f.shared/'attributes.bin').write_bytes(raw)
        geo, tex = (f.donor/n for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))
        def converted(source, reference, destination, **kwargs):
            destination.mkdir(parents=True, exist_ok=True)
            (destination/'GEOMETRY.BIN').write_bytes(geo.read_bytes())
            (destination/'TEXTURES.BIN').write_bytes(tex.read_bytes())
            return dict(missing_stock=False)
        with mock.patch.object(carbon_batch, 'convert_one', converted), \
             mock.patch.object(private, 'isolate', side_effect=lambda g,t,*a: (g,t,b'map')), \
             mock.patch.object(carbon_repair, 'wheel_sidecar', return_value=b'wheel'), \
             mock.patch.object(carbon_repair, 'collision_sidecar', return_value=b'collision'):
            return private.convert(f.car, f.donor, f.out, revision=revision)

    def private_v1(self, fault=None):
        return self.private(fault, revision=1)

    def assert_conversion(self, convert):
        report = convert()
        path = self.fixture.out/'GRIP.BIN'
        self.assertTrue(path.is_file(), 'complete GRIP.BIN absent from conversion')
        blob = path.read_bytes()
        assert_record(self, blob, self.expected)
        if 'outputs' in report:
            self.assertEqual(report['outputs']['GRIP.BIN'], hashlib.sha256(blob).hexdigest())
        return blob

    def test_pc_transport(self):
        self.assert_conversion(self.pc)

    def test_x360_transport(self):
        self.assert_conversion(self.xenon)

    def test_private_transport(self):
        self.assert_conversion(self.private)

    def test_private_revision_one_transport(self):
        self.assert_conversion(self.private_v1)

    def test_original_conversion_controls(self):
        for method, module, fragment, digest in (
                (self.pc, cc, ORIGINAL_DECODED, ORIGINAL_DECODED_SHA),
                (self.private, private, ORIGINAL_PRIVATE, ORIGINAL_PRIVATE_SHA),
                (self.private_v1, private, ORIGINAL_PRIVATE, ORIGINAL_PRIVATE_SHA),
                (self.xenon, cc, ORIGINAL_DECODED, ORIGINAL_DECODED_SHA)):
            with self.subTest(path=method.__name__):
                # Every field/count/identity oracle depends on actual publication.
                with contextlib.ExitStack() as stack:
                    stack.enter_context(original_function(module, fragment, digest))
                    if method == self.xenon:
                        stack.enter_context(original_function(cc, ORIGINAL_X360, ORIGINAL_X360_SHA))
                    with self.assertRaisesRegex(AssertionError, 'complete GRIP.BIN absent'):
                        self.assert_conversion(method)
                self.assert_conversion(method)
                (self.fixture.out/'GRIP.BIN').unlink()

    def test_malformed_omits_entire_record_and_removes_stale(self):
        for method in (self.pc, self.xenon, self.private, self.private_v1):
            for fault in ('missing', 'empty', 'nan', 'oversize', 'schema', 'cycle', 'reference'):
                with self.subTest(path=method.__name__, fault=fault):
                    self.assert_conversion(method)
                    report = method(fault)
                    self.assertFalse((self.fixture.out/'GRIP.BIN').exists(), 'invalid metadata retained grip')
                    self.assertNotIn('GRIP.BIN', report.get('outputs', {}))
                    self.assertTrue((self.fixture.out/'GEOMETRY.BIN').is_file())

    def test_absent_metadata_removes_stale(self):
        for method in (self.pc, self.xenon, self.private, self.private_v1):
            with self.subTest(path=method.__name__):
                self.assert_conversion(method)
                result = method('absent')
                self.assertNotIn('GRIP.BIN', result.get('outputs', {}))
                self.assertFalse((self.fixture.out/'GRIP.BIN').exists())

    def test_optional_removal_never_follows_symlink(self):
        f = self.fixture
        self.assert_conversion(self.pc)
        path = f.out/'GRIP.BIN'
        path.unlink()
        protected = f.car/'GEOMETRY.BIN'
        before = protected.read_bytes()
        path.symlink_to(protected)
        result = self.pc('absent')
        self.assertNotIn('GRIP.BIN', result['outputs'])
        self.assertFalse(path.is_symlink())
        self.assertEqual(protected.read_bytes(), before)

    def test_actual_authored_counts(self):
        path = input_path('NFSC')/'GLOBAL/attributes.bin'
        if not path.is_file():
            self.skipTest('private source vault absent; synthetic conversion proof still runs')
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b')
        attrib = cc.FitAttributes(raw)
        be = None
        images = list(input_path('NFSC_X360').glob('*.iso'))
        if images:
            source = Source(images[0])
            try:
                logical = cs._logical(source, cs._physical(source))
                be = cs._read(source, logical[cs.path_hash('GLOBAL/ATTRIBUTES.BIN')])
            finally:
                source.close()
            self.assertEqual(hashlib.sha256(be).hexdigest(),
                             '24cc0188954147db87c8889363d6d0338a6dac40ab5d3b2780873cd1173b904d')
        for car, oracle in (('CCX', CCX_YAW), ('R32', R32_YAW)):
            f = self.fixture
            target = f.car.with_name(car)
            f.car.rename(target)
            f.car = target
            (f.car/'GEOMETRY.BIN').write_bytes(pc_fixture.geometry(car, True))
            (f.car/'TEXTURES.BIN').write_bytes(pc_fixture.texture(car))
            (f.shared/'attributes.bin').write_bytes(raw)
            report = f.convert()
            self.assertIn('GRIP.BIN', report['outputs'])
            blob = (f.out/'GRIP.BIN').read_bytes()
            self.assertEqual(report['outputs']['GRIP.BIN'], hashlib.sha256(blob).hexdigest())
            # Independent value/shape parser; count is not schema capacity.
            pos = 40
            refs = list(struct.iter_unpack('<3I', attrib.raw('pvehicle', cc.hash32(car.lower()), 'tires')))
            self.assertEqual(cc.u32(blob, 24), len(refs))
            for ordinal, (cls, key, flags) in enumerate(refs):
                v, off = attrib.collections[cls, key]
                self.assertNotEqual(cc.u32(v.buffers[0], off+8), 0)
                self.assertEqual(struct.unpack_from('<2I', blob, pos), (ordinal, key))
                pos += 8
                for _ in range(17):
                    field, semantic, count, reserved = struct.unpack_from('<4I', blob, pos)
                    value = blob[pos+16:pos+16+count*4]
                    if field == 0xC51EB1A1 and ordinal == 0:
                        self.assertEqual(count, len(oracle))
                        self.assertEqual(value, struct.pack('<'+'f'*len(oracle), *oracle))
                    self.assertEqual(value, grip.raw_field(attrib, cls, key, field))
                    pos += 16+count*4
            self.assertEqual(pos, len(blob))
            if be is not None:
                report = self.xenon(attributes=be)
                self.assertEqual((f.out/'GRIP.BIN').read_bytes(), blob)
                self.assertEqual(report['outputs']['GRIP.BIN'], hashlib.sha256(blob).hexdigest())
            for revision in (1, 2):
                self.private(revision=revision, attributes=raw)
                self.assertEqual((f.out/'GRIP.BIN').read_bytes(), blob)


if __name__ == '__main__':
    if '--original' in sys.argv:
        sys.argv.remove('--original')
        with contextlib.ExitStack() as stack:
            stack.enter_context(original_function(cc, ORIGINAL_DECODED, ORIGINAL_DECODED_SHA))
            stack.enter_context(original_function(cc, ORIGINAL_X360, ORIGINAL_X360_SHA))
            stack.enter_context(original_function(private, ORIGINAL_PRIVATE, ORIGINAL_PRIVATE_SHA))
            names = [name for name in unittest.defaultTestLoader.getTestCaseNames(GripTransport)
                     if name != 'test_original_conversion_controls']
            suite = unittest.TestSuite(GripTransport(name) for name in names)
            result = unittest.TextTestRunner().run(suite)
            sys.exit(not result.wasSuccessful())
    unittest.main()
