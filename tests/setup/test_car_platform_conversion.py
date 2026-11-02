"""Full-entry PC/X360 writer proof using generated data and independent readers.

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

BASELINE_REJECTION = 'Carbon mesh header must be 52 aligned bytes'


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

    def test_lighting_policy_both_platforms(self):
        name = 'HEADLIGHTGLASS'
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
            expected = {'car_convert.py','huff.py','car_lighting.py','car_paint.py',
                        'car_physics.py','car_sources.py','x360.py','xdvdfs.py'}
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


def asset_main():
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
    out = ROOT/'.metal-cache/cw-assets'
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
    return 0


if __name__ == '__main__':
    if '--assets' in sys.argv:
        raise SystemExit(asset_main())
    unittest.main()
