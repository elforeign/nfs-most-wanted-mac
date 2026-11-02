"""Dependency closure proof, with synthetic data by default.

Original PC-only failure: wheel_fields on a BE VPAK rejects with
``VLT chunk crosses section``; validate_geometry on the same bundle rejects
the 80-byte mesh header. The adapter must resolve that exact bundle.
Use --assets for independent PC/ISO/extracted logical comparisons; without
NFS_CARBON_PC, NFS_CARBON_X360 and NFS_CARBON_CARS that mode exits 3.
No source data is embedded. Fixtures use non-symmetric typed components.
Good: 17 synthetic tests. Broken endian projection: 9 errors, exit 1;
broken packed-top origin: 12 subcase failures, exit 1. The real shared proof
compares 11 selected logical files (72,388,472 bytes), 162 material identities,
155 paint swatches, 257 array textures, and 302 indexed shared textures.
Classified layout/content differences: BE attribute sections inside LE VPAK;
32/24-byte texture platforms, tiling, packed top mips, BGRA/BC5/L8 formats;
26 windshield specular-power scalars at +8C (0 versus 1); 16/8-pixel mask
resolution with identical decoded white; BC5 reconstructed Z/opaque alpha and
lossy DXT5 normalization (native PC RGB MAD <=1.809; codec MAD <=3.916).
PC duplicate shared records remain visible in the reference comparison.
All 93 collision collections decode; one unselected collection has 6 versus
10 bounds. Common geometry components agree on 1,937 solids; the cataloged PC
source has one additional E-detail body solid (332 triangles). D's strict
equal-identity asset test consequently still rejects that car, without a pin
or a change to D. The normalized dependency proof does not drop source solids.
No source coefficients, shader parameters or opaque streams become MW data.
"""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import car_sources as cs, car_convert as cc, car_x360 as cx
from setup_kit import car_x360_inputs as ci, car_paint, car_physics
from setup_kit.x360 import Source
import test_car_x360 as fixtures
import test_car_sources as source_fixtures


def chunk(kind, value):
    return struct.pack('<II', kind, len(value)) + value


def attrs(endian='>', layout=False, inherit=False):
    """Independent source builder: typed BIN, modern VLT, explicit relocation.

    Unlike the adapter's output projection, source directories follow DatN,
    collections have type tables, and fields can inherit or use BIN layouts.
    """
    key, parent = cc.hash32('synth'), cc.hash32('base')
    camera = cc.hash32('camera')
    chassis, tires = cc.hash32('chassis_test'), cc.hash32('tires_test')
    def floats(*v): return struct.pack(endian + str(len(v)) + 'f', *v)
    def ref(cls, identity): return struct.pack(endian + '3I', cc.hash32(cls), identity, 0)
    ecar = []
    for name, fmt, n in cc.WHEEL_FIELDS:
        if name == 'TireOffsets': values = (.1, -.2, .3, .4) * 4
        elif fmt == 'B': values = tuple(range(n))
        else: values = tuple(.2 + i * .01 for i in range(n))
        ecar.append((name, struct.pack(endian + str(n) + fmt, *values), 16 if name == 'TireOffsets' else struct.calcsize(fmt)))
    for n, count in zip(cc.RENDER_FIELDS, (1, 2, 2, 2, 4)):
        ecar.append((n, floats(*(.125 * (i + 1) for i in range(count))), 0))
    ecar += [('WheelSpokeCount', b'\x07', 0)]
    ecar += [(n, ref('camerainfo', camera), 0) for n in cc.CAMERA_LINKS]
    ecar += [(0xAC97D3D1, struct.pack(endian + 'I', cc.name_hash('STOCK_SYNTH')), 0)]
    rows = [('ecar', parent if inherit else key, ecar)]
    rows.append(('camerainfo', camera, [(n, floats(.25, .5), 0) for n in cc.CAMERA_FIELDS]))
    vehicle = [('chassis', ref('chassis', chassis) * 2, 0), ('tires', ref('tires', tires), 0)]
    vehicle += [('MASS', floats(1234.5), 0), ('TENSOR_SCALE', floats(1.25, 2.5, 3.75, 0), 0)]
    rows.append(('pvehicle', key, vehicle))
    rows.append(('chassis', chassis, [(n, floats(*(.25 * (i + 1) for i in range(count))), 0)
                                     for n, count in zip(cc.CHASSIS_FIELDS, (2, 2, 1, 1, 2))]))
    rows.append(('tires', tires, [(n, floats(.125, .25), 0) for n in cc.TIRE_FIELDS]))
    binary, definitions, values = bytearray(), {}, {}
    for cls, identity, fields in rows:
        defs, entries = [], []
        for name, value, esz in fields:
            hs = cc.hash32(name) if isinstance(name, str) else name
            at = len(binary)
            n = len(value) // esz if esz else 1
            if esz:
                binary += struct.pack(endian + '4H', n, n, esz, 0)
            binary += value
            defs.append((hs, 0, at if layout else 0, esz or len(value), n,
                         (1 if esz else 0) | (2 if layout else 0), 4))
            entries.append((hs, at, bool(esz)))
        definitions[cls] = defs
        values[cls, identity] = entries
    if inherit:
        values['ecar', key] = []
    def vc(tag, raw):
        return (tag if endian == '>' else tag[::-1]) + struct.pack(endian + 'I', len(raw) + 8) + raw
    prefix = vc(b'Vers', bytes(8)) + vc(b'DepN', struct.pack(endian + '3I', 2, 0, 0))
    datstart = len(prefix) + 8
    body, exports, fixups = bytearray(), [], [(0, 2, 0, 0)]
    for cls, defs in definitions.items():
        at = len(binary)
        binary += b''.join(struct.pack(endian + 'IIHHHBB', *r) for r in defs)
        off = datstart + len(body)
        h = bytearray(24)
        struct.pack_into(endian + 'I', h, 8, len(defs))
        body += h
        fixups.append((off + 12, 3, 1, at))
        exports.append((cc.hash32(cls), 0x5E970CBC, 24, off))
    for (cls, identity), entries in values.items():
        off = datstart + len(body)
        h = bytearray(40)
        struct.pack_into(endian + '3I', h, 0, identity, cc.hash32(cls), parent if inherit and identity == key and cls == 'ecar' else 0)
        struct.pack_into(endian + 'I', h, 20, 0 if layout else len(entries))
        struct.pack_into(endian + 'H', h, 24, 1)
        struct.pack_into(endian + 'I', h, 32, 123)
        body += h
        if layout:
            if not (inherit and identity == key and cls == 'ecar'):
                fixups.append((off + 28, 3, 1, 0))
        else:
            for hs, at, array in entries:
                fixups.append((datstart + len(body) + 4, 3, 1, at))
                body += struct.pack(endian + 'IIHBB', hs, 0, 0, 2 if array else 0, 0)
        exports.append((identity, 0x8E112EB7, len(body) + datstart - off, off))
    vault = prefix + vc(b'DatN', body)
    vault += vc(b'ExpN', struct.pack(endian + 'I', len(exports)) + b''.join(struct.pack(endian + '4I', *r) for r in exports))
    vault += vc(b'PtrN', b''.join(struct.pack(endian + 'IHHI', *r) for r in fixups) + bytes(12))
    names = b'synthetic\0'
    bo = 36 + len(names)
    return (b'VPAK' + struct.pack('<3I', 1, 36, len(names)) +
            struct.pack('<5I', 0, len(binary), len(vault), bo, bo + len(binary)) + names + binary + vault)


def renamed_blob(name, fmt=0x1A200152):
    _, blob, linear = fixtures.texture_blob(fmt, 128, 128)
    r = bytearray(blob[-156:-32])
    hs = cc.name_hash(name)
    struct.pack_into('<I', r, 12, hs)
    r[88:] = bytes((len(name) + 1,)) + (name.encode() + b'\0').ljust(35, b'\0')
    return hs, blob[:-156] + r + blob[-32:], linear


def array_pack(name='SHARED_DIFFUSE', fmt=0x1A200152):
    hs, blob, linear = renamed_blob(name, fmt)
    r, pl, pixels = bytearray(blob[-156:-32]), blob[-32:], blob[:-156]
    # Variable-size source records and a nonzero image offset expose both
    # record-stride and root-relative-address mistakes.
    del r[(89 + r[88] + 3) // 4 * 4:]
    struct.pack_into('<I', r, 20, 4096)
    metadata = chunk(0x33310001, struct.pack('<I', 8))
    metadata += chunk(0x33310004, r) + chunk(0x33310005, pl)
    header = chunk(0xB3310000, metadata)
    start = 8 + len(header) + 8
    padding = b'\x11' * (-start % 4096)
    pack = chunk(0xB3300000, header + chunk(0x33320003, padding + bytes(4096) + pixels))
    return hs, pack, linear


def paint_chunks():
    values = [('RED', 255), ('GREEN', 0), ('BLUE', 0), ('PART_NAME_SELECTOR', 2),
              ('PART_NAME_BASE_HASH', cc.name_hash('SWATCH')), ('PART_NAME_OFFSETS', 0xFFFF0000)]
    row = bytearray(104)
    row[8:20] = b'STOCK_SYNTH\0'
    struct.pack_into('<IIff', row, 40, cc.name_hash('GLOSS'), cc.name_hash('SWATCH_COLOR01'), .75, .5)
    return (chunk(0x30250, row) + chunk(0x34604, struct.pack('<BBH', 0, 0, 0)) +
            chunk(0x34605, b''.join(struct.pack('<II', cc.name_hash(n), v) for n, v in values)) +
            chunk(0x34606, b'COLOR01\0') + chunk(0x3460B, struct.pack('<I', cc.name_hash('PAINT'))) +
            chunk(0x3460C, struct.pack('<7H', 6, *range(6))))


def global_chunks():
    row = bytearray(236)
    struct.pack_into('<II', row, 8, 3, cc.name_hash('SYNTH_MATERIAL'))
    row[20:35] = b'SYNTH_MATERIAL\0'
    result = chunk(0x135200, row) + paint_chunks()
    bound = bytearray(48)
    struct.pack_into('<3h', bound, 16, 100, 200, 300)
    struct.pack_into('<b', bound, 23, -1)
    struct.pack_into('<h', bound, 30, -1)
    raw = struct.pack('<4I', cc.hash32('SYNTH'), 1, 0, 0) + bound + bytes(16)
    result += cc.aligned_chunk(0x3B901, len(result), raw, 16)
    return result


def files_and_recognition():
    raw, _ = fixtures.solid()
    raw = bytearray(raw)
    local, blob, _ = renamed_blob('SYNTH_DIFFUSE')
    shared, array, _ = array_pack()
    off, _ = cc.find(raw, 0x134012)
    struct.pack_into('<4I', raw, off + 8, local, 0, shared, 0)
    off, _ = cc.find(raw, 0x134013)
    struct.pack_into('<II', raw, off + 8, cc.name_hash('SYNTH_MATERIAL'), 0)
    header = cc.payload(raw, 0x134011, 16)
    geometry = fixtures.indexed_pack([(cc.u32(header, 16), raw)])
    global_data = global_chunks() + array
    tires = [renamed_blob(n)[:2] for n in ('TIRE_STYLE01', 'TIRE_STYLE02', 'TIRE_STYLE01_N', 'DOORLINE_N')]
    files = {'CARS/SYNTH/GEOMETRY.BIN': geometry,
             'CARS/SYNTH/TEXTURES.BIN': fixtures.indexed_pack([(local, blob)], texture=True),
             'GLOBAL/GLOBALB.LZC': global_data, 'GLOBAL/GLOBALB.BUN': global_data,
             'GLOBAL/ATTRIBUTES.BIN': attrs(),
             'CARS/TEXTURES.BIN': fixtures.indexed_pack(tires, texture=True)}
    shared_rows, car_rows = [], []
    for name, data in files.items():
        row = dict(path_hash=cs.path_hash(name), size=len(data), sha256=hashlib.sha256(data).hexdigest())
        (car_rows if name.startswith('CARS/SYNTH/') else shared_rows).append(row)
    recognition = dict(game='carbon', platform='x360', shared=shared_rows,
                       cars=[dict(car_hash=cs.path_hash('CARS/SYNTH'), status='importable', files=car_rows)])
    return files, recognition


def write_files(root, files):
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


class AttributeTests(unittest.TestCase):
    def test_pc_entry_rejects_original_bundle(self):
        files, _ = files_and_recognition()
        with self.assertRaisesRegex(cc.FormatError, 'VLT chunk crosses section'):
            cc.wheel_fields(files['GLOBAL/ATTRIBUTES.BIN'], 'SYNTH')
        with self.assertRaisesRegex(cc.FormatError, '52'):
            cc.validate_geometry(files['CARS/SYNTH/GEOMETRY.BIN'], carbon=True)

    def test_projection_matches_independent_pc(self):
        for layout in (False, True):
            for inherit in (False, True):
                with self.subTest(layout=layout, inherit=inherit):
                    x = attrs(layout=layout, inherit=inherit)
                    pc = attrs('<', layout=layout, inherit=inherit)
                    normalized = ci.normalize_attributes(x, 'SYNTH')
                    self.assertEqual(cc.wheel_fields(normalized, 'SYNTH'), cc.wheel_fields(pc, 'SYNTH'))
                    self.assertEqual(cc.render_fit(normalized, 'SYNTH'), cc.render_fit(pc, 'SYNTH'))
                    self.assertEqual(car_physics.metadata(normalized, 'SYNTH', 'DONOR'),
                                     car_physics.metadata(pc, 'SYNTH', 'DONOR'))
                    self.assertEqual(car_paint.stock_customization(paint_chunks(), normalized, 'SYNTH'),
                                     car_paint.stock_customization(paint_chunks(), attrs('<', layout=True, inherit=inherit), 'SYNTH'))
        print('good: 4 source layouts; wheel, render, paint and physics match independent LE fixtures')

    def test_attribute_mutations(self):
        original = attrs()
        _, binary, vault = cc.read_vpak(original)[0]
        bo, vo = cc.words(original, 28, '<II')
        v = ci.XenonVault(binary, vault)
        ptr, _ = v.chunks[b'PtrN']
        exp, _ = v.chunks[b'ExpN']
        definitions = next((off for k, typ, off in v.exports if typ == 0x5E970CBC))
        cases = [(4, '>I', 1), (28, '<I', len(original)),
                 (vo + 4, '<I', 16), (vo + exp + 8, '>I', 0x1000000),
                 (vo + exp + 24, '>I', len(vault) + 4)]
        cases += [(vo + ptr + 8 + 12 + 6, '>H', 2),
                 (vo + ptr + 8 + 12 + 8, '>I', len(binary) + 4),
                 (vo + definitions + 8, '>I', 65535),
                 (bo, '>H', 0), (bo + 2, '>H', 9)]
        for off, fmt, value in cases:
            with self.subTest(offset=off), self.assertRaises((cc.FormatError, cs.SourceError)):
                ci.normalize_attributes(fixtures.changed(original, off, fmt, value), 'SYNTH')
        for n in (0, 15, 35, len(original) - 1):
            with self.subTest(length=n), self.assertRaises(cc.FormatError):
                ci.normalize_attributes(original[:n], 'SYNTH')

    def test_endian_decoder_control(self):
        original = ci._little
        with mock.patch.object(ci, '_little', lambda raw, unit=4: raw):
            try:
                normalized = ci.normalize_attributes(attrs(), 'SYNTH')
                good = cc.wheel_fields(attrs('<'), 'SYNTH')
                self.assertNotEqual(cc.wheel_fields(normalized, 'SYNTH'), good)
            except cc.FormatError:
                pass
        self.assertIs(ci._little, original)


class TextureTests(unittest.TestCase):
    def test_array_record_addressing(self):
        hs, pack, linear = array_pack()
        # A prefixed chunk makes the pack's root nonzero and non-aligned.
        data = chunk(0, bytes(20)) + pack
        entry = ci.array_entries(data)[hs]
        self.assertEqual(ci.shared_linear_top(*entry), ('DXT1', linear))
        r, pl, px = ci.shared_texture_entry(*entry)
        self.assertEqual(px, linear)
        cc.validate_texture(r, pl, px, 8, hs)

    def test_array_mutations(self):
        hs, original, _ = array_pack()
        r = cc.find(original, 0x33310004)[0] + 8
        pl = cc.find(original, 0x33310005)[0] + 8
        for off, fmt, value in [(r + 20, '<I', len(original)), (r + 28, '<I', len(original)),
                                (r + 36, '<I', 0), (r + 40, '>H', 128),
                                (r + 12, '<I', hs ^ 1), (r + 88, 'B', 255),
                                (pl + 20, '<I', 0x1A200054)]:
            with self.subTest(offset=off), self.assertRaises(cc.FormatError):
                entries = ci.array_entries(fixtures.changed(original, off, fmt, value))
                ci.shared_texture_entry(*entries[hs])
        for size in (0, 7, len(original) - 1):
            with self.subTest(size=size), self.assertRaises(cc.FormatError):
                ci.array_entries(original[:size])

    def test_tiled_bgra_and_luminance(self):
        for fmt, bpp, width, height in ((0x18280186, 4, 32, 32), (0x28000102, 1, 128, 128)):
            name = 'SYNTH_PIXELS'
            r, pl = bytearray(124), bytearray(32)
            struct.pack_into('<I', r, 12, cc.name_hash(name))
            struct.pack_into('<HH', r, 40, width, height)
            r[44:47] = bytes((width.bit_length() - 1, height.bit_length() - 1, 0x20 if bpp == 4 else 0x28))
            r[50], r[88] = 1, len(name) + 1
            r[89:89 + len(name) + 1] = name.encode() + b'\0'
            struct.pack_into('<I', pl, 20, fmt)
            expected = ((np.arange(width * height * bpp, dtype=np.uint32) * 17 + 3) % 256).astype(np.uint8).tobytes()
            # Scalar address oracle, not the adapter's vectorized function.
            offsets = [fixtures.tile_offset(x, y, width, bpp) for y in range(height) for x in range(width)]
            pixels = bytearray(((max(offsets) + 1) * bpp + 4095) // 4096 * 4096)
            for i, off in enumerate(offsets):
                value = expected[i * bpp:(i + 1) * bpp]
                pixels[off * bpp:(off + 1) * bpp] = value[::-1] if bpp == 4 else value
            struct.pack_into('<I', r, 28, len(pixels))
            struct.pack_into('<I', r, 36, len(pixels))
            entry = bytes(r), bytes(pl), bytes(pixels)
            self.assertEqual(ci.shared_linear_top(*entry)[1], expected)
            normalized = ci.shared_texture_entry(*entry)
            cc.validate_texture(*normalized, 8, cc.name_hash(name))
            with mock.patch.object(cx, '_tile_indices', lambda w, h, b: np.arange(w * h)):
                self.assertNotEqual(ci.shared_linear_top(*entry)[1], expected)
            with self.assertRaises(cc.FormatError):
                ci.shared_linear_top(r, pl, pixels[:-1])

    def test_packed_top_offsets(self):
        for fmt in (0x1A200152, 0x1A200153, 0x1A200154, 0x1A200171):
            for w, h in ((16, 16), (32, 16), (16, 64)):
                with self.subTest(fmt=hex(fmt), dimensions=(w, h)):
                    _, blob, _ = renamed_blob('PACKED_TOP', fmt)
                    r, pl = bytearray(blob[-156:-32]), blob[-32:]
                    bpp = 8 if fmt == 0x1A200152 else 16
                    wb, hb = w // 4, h // 4
                    # Independent packed location in pixel coordinates.
                    ox, oy = (0, 4) if w > h else (4, 0)
                    pitch = (wb + 31) & ~31
                    expected = ((np.arange(wb * hb * bpp, dtype=np.uint32) * 29 + 71) % 256).astype(np.uint8).tobytes()
                    pixels = bytearray(pitch * 32 * bpp)
                    for y in range(hb):
                        for x in range(wb):
                            at = fixtures.tile_offset(x + ox, y + oy, pitch, bpp) * bpp
                            i = (y * wb + x) * bpp
                            pixels[at:at + bpp] = np.frombuffer(expected[i:i + bpp], np.uint8).reshape(-1, 2)[:, ::-1].tobytes()
                    struct.pack_into('<I', r, 28, len(pixels))
                    struct.pack_into('<I', r, 36, len(pixels))
                    struct.pack_into('<HH', r, 40, w, h)
                    r[44:46] = bytes((w.bit_length() - 1, h.bit_length() - 1))
                    r[50] = 1
                    self.assertEqual(ci.shared_linear_top(r, pl, pixels)[1], expected)
                    # The original top-at-origin decoder demonstrably fails.
                    self.assertNotEqual(cx.linear_top(fmt, w, h, pixels)[1], expected)


class BundleTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.metal-cache').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='x360-inputs-', dir=ROOT / '.metal-cache')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files, self.recognition = files_and_recognition()
        write_files(self.root, self.files)
        self.source = Source(self.root)
        self.addCleanup(self.source.close)

    def resolve(self):
        return ci.resolve(self.source, 'SYNTH', 'DONOR', recognition=self.recognition)

    def replace_input(self, name, data):
        self.files[name] = data
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        for row in self.recognition['shared'] + self.recognition['cars'][0]['files']:
            if row['path_hash'] == cs.path_hash(name):
                row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def second_texture(self, name):
        geometry = bytearray(self.files['CARS/SYNTH/GEOMETRY.BIN'])
        off, _ = cc.find(geometry, 0x134012)
        struct.pack_into('<I', geometry, off + 16, cc.name_hash(name))
        self.replace_input('CARS/SYNTH/GEOMETRY.BIN', bytes(geometry))

    def test_full_dependency_contract(self):
        bundle = self.resolve()
        self.assertEqual(len(bundle.models), 1)
        self.assertEqual(len(bundle.textures), 1)
        self.assertEqual(len(bundle.shared_textures), 5)
        self.assertEqual(len(bundle.inputs), 6)
        self.assertEqual(len(bundle.wheel_fit), 380)
        self.assertEqual(len(bundle.physics), 36)
        self.assertEqual(len(bundle.collision), 100)
        self.assertEqual(bundle.paint['finish'], 'GLOSS')
        self.assertEqual(bundle.paint['source_swatch'], 'SWATCH_COLOR01')
        self.assertEqual(bundle.runtime_bindings, {})
        self.assertFalse(any(cid in (0xB3300000, 0x33310005) for cid, _, _ in cc.walk(bundle.global_data)))
        for m in bundle.models.values():
            self.assertNotIn('x360_opaque', m)
            self.assertFalse(any(cid in cx.OPAQUE_CHUNKS for cid, _ in m['extras']))
        geometry, stats = cc.write_geometry(bundle.geometry, bundle.models)
        self.assertEqual(stats['solids'], 1)
        self.assertFalse(any(cid in cx.OPAQUE_CHUNKS for cid, _, _ in cc.walk(geometry)))
        self.assertEqual(len(cc.validate_geometry(geometry)), 1)
        bundle.check_unchanged()
        again = self.resolve()
        self.assertEqual(bundle.inputs, again.inputs)
        self.assertEqual(bundle.attributes, again.attributes)
        print('good: original rejected bundle resolves; 6 logical files, 4 sidecar/selection dependencies')

    def test_hash_changes_before_and_after(self):
        bundle = self.resolve()
        path = self.root / 'GLOBAL/ATTRIBUTES.BIN'
        changed = bytearray(path.read_bytes()); changed[-1] ^= 1
        path.write_bytes(changed)
        with self.assertRaises(cc.InputChangedError): bundle.check_unchanged()
        with self.assertRaises(cc.InputChangedError): self.resolve()

    def test_unapproved_and_missing_source(self):
        self.recognition['platform'] = 'pc'
        with self.assertRaises(cc.FormatError): self.resolve()
        self.recognition['platform'] = 'x360'
        self.recognition['cars'][0]['status'] = 'unknown'
        with self.assertRaises(cc.FormatError): self.resolve()
        self.recognition['cars'][0]['status'] = 'importable'
        self.recognition['shared'].pop()
        with self.assertRaises(cc.FormatError): self.resolve()

    def test_opaque_leak_control(self):
        with mock.patch.object(cx, 'OPAQUE_CHUNKS', frozenset()), self.assertRaisesRegex(cc.FormatError, 'opaque'):
            self.resolve()

    def test_range_truncation(self):
        reader = ci.LogicalReader(self.source, self.recognition['shared'])
        identity = cs.path_hash('GLOBAL/ATTRIBUTES.BIN')
        entry = reader.logical[identity]
        for off, size in ((-1, 1), (entry[2], 1), (0, entry[2] + 1)):
            with self.assertRaises(cs.SourceError): cs._read(self.source, entry, off, size)
        reader.logical[identity] = entry[0], 1, entry[2]
        with self.assertRaises(cs.SourceError): reader.read('GLOBAL/ATTRIBUTES.BIN')

    def test_additional_dependency_is_snapshotted(self):
        self.second_texture('DRIVER_PLAYER')
        self.replace_input('GLOBAL/INGAMEA.BUN', array_pack('DRIVER_PLAYER')[1])
        self.source = Source(self.root)
        self.addCleanup(self.source.close)
        bundle = self.resolve()
        hs = cc.name_hash('DRIVER_PLAYER')
        identity = cs.path_hash('GLOBAL/INGAMEA.BUN')
        self.assertIn(hs, bundle.shared_textures)
        self.assertEqual(len(bundle.inputs), 7)
        self.assertFalse(bundle.inputs[identity]['catalogued'])
        self.assertEqual(bundle.inputs[identity]['sha256'], hashlib.sha256(self.files['GLOBAL/INGAMEA.BUN']).hexdigest())
        (self.root / 'GLOBAL/INGAMEA.BUN').write_bytes(self.files['GLOBAL/INGAMEA.BUN'][:-1])
        with self.assertRaises(cc.InputChangedError): bundle.check_unchanged()

    def test_runtime_binding_and_missing_mask_alias(self):
        self.second_texture('WINDOW_RIGHT_FRONT')
        bundle = self.resolve()
        self.assertEqual(bundle.runtime_bindings, {cc.name_hash('WINDOW_RIGHT_FRONT'): 'WINDOW_RIGHT_FRONT'})
        self.assertNotIn(cc.name_hash('WINDOW_RIGHT_FRONT'), bundle.shared_textures)
        self.second_texture('SYNTH_KIT00_HEADLIGHT_GLASS_OFF')
        global_data = global_chunks() + array_pack('WINDOW_MASK')[1]
        for name in ('GLOBAL/GLOBALB.LZC', 'GLOBAL/GLOBALB.BUN'):
            self.replace_input(name, global_data)
        bundle = self.resolve()
        self.assertEqual(bundle.texture_aliases, {cc.name_hash('SYNTH_KIT00_HEADLIGHT_GLASS_OFF'):
                                                (cc.name_hash('WINDOW_MASK'), 'WINDOW_MASK')})
        self.assertIn(cc.name_hash('WINDOW_MASK'), bundle.shared_textures)

    def test_shared_metadata_corruptions(self):
        data = global_chunks()
        material = cc.find(data, 0x135200)[0] + 8
        collision = (cc.find(data, 0x3B901)[0] + 23) & ~15
        attrs_off = cc.find(data, 0x34605)[0] + 8
        for off, fmt, value in ((material + 8, '>I', 3), (material + 12, '<I', 0),
                                (attrs_off + 4, '<I', 256)):
            with self.subTest(offset=off), self.assertRaises(cc.FormatError):
                ci.shared_chunks(fixtures.changed(data, off, fmt, value))
        for off, fmt, value in ((collision + 4, '>I', 1), (collision + 8, '<I', 1),
                                (collision + 16 + 16, '<h', -1), (collision + 16 + 30, '<h', 2)):
            with self.subTest(offset=off), self.assertRaises(cc.ConversionError):
                normalized, _, _ = ci.shared_chunks(fixtures.changed(data, off, fmt, value))
                cc.collision_sidecar(normalized, 'SYNTH', 'DONOR')
        # Preserve the source container's promise that a collection must exist.
        empty = paint_chunks() + chunk(0x8003B900, b'')
        normalized, _, _ = ci.shared_chunks(empty)
        with self.assertRaisesRegex(cc.ConversionError, 'collection absent'):
            cc.collision_sidecar(normalized, 'SYNTH', 'DONOR')

    def test_iso_archive_and_extracted_logical_bytes(self):
        archived = source_fixtures.archive_files(source_fixtures.fake_xex(), self.files)
        # An unrelated file must never be read into the conversion bundle.
        archived['MOVIES/UNRELATED.BIN'] = bytes(8192)
        iso = self.root / 'source.iso'
        iso.write_bytes(source_fixtures.tiny_iso(archived))
        source = Source(iso)
        self.addCleanup(source.close)
        inventory = cs._logical(source, cs._physical(source))
        for name, raw in self.files.items():
            entry = inventory[cs.path_hash(name)]
            self.assertEqual(cs._read(source, entry), raw)
            # Exercise bounded subranges as well as whole logical files.
            self.assertEqual(cs._read(source, entry, 1, min(len(raw) - 1, 31)), raw[1:32])
        from_iso = ci.resolve(source, 'SYNTH', 'DONOR', recognition=self.recognition)
        from_folder = self.resolve()
        self.assertEqual(from_iso.inputs, from_folder.inputs)
        self.assertEqual(from_iso.attributes, from_folder.attributes)
        self.assertEqual(from_iso.shared_textures, from_folder.shared_textures)
        self.assertEqual(from_iso.wheel_fit, from_folder.wheel_fit)
        with iso.open('r+b') as handle:
            entry = inventory[cs.path_hash('GLOBAL/ATTRIBUTES.BIN')]
            handle.seek(source.files[entry[0]].offset + entry[1] + 100)
            handle.write(b'changed')
        with self.assertRaises(cc.InputChangedError): from_iso.check_unchanged()


def asset_main():
    pc, xenon, cars = (os.environ.get(n) for n in ('NFS_CARBON_PC', 'NFS_CARBON_X360', 'NFS_CARBON_CARS'))
    if not pc or not xenon or not cars or not Path(pc).is_dir() or not Path(xenon).exists():
        print('SKIP: supply NFS_CARBON_PC, NFS_CARBON_X360 and NFS_CARBON_CARS')
        return 3
    from PIL import Image
    from collections import Counter
    pc = Path(pc)
    recognition = cs.recognise(xenon)
    source = Source(xenon)
    try:
        logical = cs._logical(source, cs._physical(source))
        raw_attributes = cs._read(source, logical[cs.path_hash('GLOBAL/ATTRIBUTES.BIN')])
        try:
            cc.wheel_fields(raw_attributes, cars.split()[0])
        except cc.FormatError as error:
            print('original PC-only shared entry rejects real bundle:', error)
        else:
            raise AssertionError('PC entry unexpectedly accepted source VLT')
        xglobal = cc.unpack_file(cs._read(source, logical[cs.path_hash('GLOBAL/GLOBALB.LZC')]))
        pglobal = cc.unpack_file((pc / 'GLOBAL/GLOBALB.LZC').read_bytes())
        pattributes = (pc / 'GLOBAL/attributes.bin').read_bytes()
        xs, xm, xp = ci.shared_chunks(xglobal)
        ps, pm, pp = ci.shared_chunks(pglobal)
        assert xp == pp and len(xp) > 0, 'paint palette differs'
        assert xm.keys() == pm.keys(), 'shared material identities differ'
        changes = Counter()
        for hs in xm:
            offsets = tuple(i for i in range(0, len(xm[hs]), 4) if xm[hs][i:i + 4] != pm[hs][i:i + 4])
            if offsets:
                # Classify scalar differences; never rewrite source values.
                changes[offsets] += 1
                for off in offsets:
                    assert math_finite_pair(xm[hs], pm[hs], off), 'nonfinite material difference'
        print(f'shared: materials={len(xm)} scalar_difference_offsets={dict(changes)} paint_swatches={len(xp)}')
        source_names = {cc.hash32(p.name): p.name for p in (pc / 'CARS').iterdir() if p.is_dir()}
        collections = {}
        for label, data in (('x', xglobal), ('p', pglobal)):
            rows = {}
            for cid, off, size in cc.walk(data):
                if cid == 0x3B901:
                    start = (off + 23) & ~15
                    value = data[start:off + 8 + size]
                    identity = cc.u32(value)
                    assert identity not in rows and identity in source_names, 'unresolved collision identity'
                    assert cc.collision_sidecar(data, source_names[identity], 'DONOR'), 'invalid shared collision'
                    rows[identity] = value
            collections[label] = rows
        assert collections['x'].keys() == collections['p'].keys(), 'collision identities differ'
        collision_differences = [(source_names[h], cc.u32(v, 4), cc.u32(collections['p'][h], 4),
                                 len(v), len(collections['p'][h]))
                                for h, v in collections['x'].items() if v != collections['p'][h]]
        print(f'collision: checked={len(collections["x"])} differing_source_bounds(name,x_count,pc_count,x_bytes,pc_bytes)={collision_differences}')
        kinds = (0x30250, 0x34604, 0x34605, 0x34606, 0x3460B, 0x3460C)
        for kind in kinds:
            values = lambda d: [d[o + 8:o + 8 + z] for c, o, z in cc.walk(d) if c == kind]
            assert values(xglobal) == values(pglobal), f'paint/customization table {kind:x} differs'
        xarray = ci.array_entries(xglobal)
        extra_arrays = ci.array_entries(cc.unpack_file(cs._read(source, logical[cs.path_hash('GLOBAL/INGAMEA.BUN')])))
        # Validate/linearize every shared format, including tiled BGRA and L8,
        # independently of which images the selected cars reference.
        formats = Counter()
        for entry in xarray.values():
            name, top = ci.shared_linear_top(*entry)
            assert top, 'empty shared image'
            formats[f'{cc.u32(entry[1], 20):08x}'] += 1
        print(f'shared texture layouts={dict(formats)}; arrays={len(xarray)}')
        xshared = dict(cc.texture_blobs(cs._read(source, logical[cs.path_hash('CARS/TEXTURES.BIN')])))
        pshared = dict(cc.texture_blobs((pc / 'CARS/TEXTURES.BIN').read_bytes()))
        assert xshared.keys() == pshared.keys(), 'shared indexed texture identities differ'
        exact, native_mad = 0, 0.
        for hs, blob in xshared.items():
            r = blob[-156:-32]
            pr, ppl, ppixels = pshared[hs][-148:-24], pshared[hs][-24:], pshared[hs][:-148]
            w, h = cc.words(r, 40, '<HH')
            assert (w, h) == cc.words(pr, 40, '<HH'), 'shared indexed dimensions differ'
            kind, top = cx.linear_top(cc.u32(blob[-32:], 20), w, h, blob[:-156])
            if kind.startswith('DXT'):
                assert top == ppixels, 'shared DXT top bytes differ'
                exact += 1
            else:
                a = cx.decode_image(kind, w, h, top)[:, :, :3].astype(float)
                pc_kind = 'A8R8G8B8' if cc.u32(ppl, 12) == 21 else ppl[12:16].decode('ascii')
                b = cx.decode_image(pc_kind, w, h, ppixels)[:, :, :3].astype(float)
                error = float(np.abs(a - b).mean())
                native_mad = max(native_mad, error)
                assert error <= 4., 'shared indexed native RGB differs'
        print(f'CARS shared: identities/dimensions={len(xshared)} exact_DXT_tops={exact} BGRA_to_PC_max_RGB_MAD={native_mad:.6f}')
        with tempfile.TemporaryDirectory(prefix='x360-selected-', dir=ROOT / '.metal-cache') as scratch:
            extracted = Path(scratch)
            selected = {}
            for car in cars.split():
                for kind in ('GEOMETRY', 'TEXTURES'):
                    name = f'CARS/{car}/{kind}.BIN'
                    selected[name] = cs._read(source, logical[cs.path_hash(name)])
            names = list(cs.SHARED_NAMES.values()) + ['GLOBAL/INGAMEA.BUN']
            for name in names:
                selected[name] = cs._read(source, logical[cs.path_hash(name)])
            write_files(extracted, selected)
            folder = Source(extracted)
            try:
                flogical = cs._logical(folder, cs._physical(folder))
                selected_bytes = 0
                for name, raw in selected.items():
                    identity = cs.path_hash(name)
                    assert cs._read(folder, flogical[identity]) == raw, 'ISO/extracted logical bytes differ'
                    selected_bytes += len(raw)
                print(f'ISO/extracted: logical_files={len(selected)} bytes={selected_bytes} exact')
                pc_shared_blobs = dict(cc.texture_blobs((pc / 'CARS/TEXTURES.BIN').read_bytes()))
                for car in cars.split():
                    bundle = ci.resolve(source, car, 'DONOR', recognition=recognition)
                    extracted_bundle = ci.resolve(folder, car, 'DONOR', recognition=recognition)
                    assert bundle.inputs == extracted_bundle.inputs, 'ISO/extracted fingerprints differ'
                    assert bundle.geometry == extracted_bundle.geometry, 'ISO/extracted geometry differs'
                    assert bundle.textures == extracted_bundle.textures, 'ISO/extracted car textures differ'
                    assert bundle.shared_textures == extracted_bundle.shared_textures, 'ISO/extracted shared textures differ'
                    assert bundle.attributes == extracted_bundle.attributes, 'ISO/extracted typed dependencies differ'
                    assert bundle.wheels == cc.wheel_fields(pattributes, car), 'wheel values differ from PC'
                    assert bundle.render_fit == cc.render_fit(pattributes, car), 'render fit differs from PC'
                    assert bundle.physics == car_physics.metadata(pattributes, car, 'DONOR'), 'physics differs from PC'
                    assert bundle.collision == cc.collision_sidecar(pglobal, car, 'DONOR'), 'collision differs from PC'
                    assert bundle.paint == car_paint.stock_customization(pglobal, pattributes, car), 'paint selection differs from PC'
                    pc_solids = dict(cc.solids((pc / f'CARS/{car}/GEOMETRY.BIN').read_bytes()))
                    x_solids = {cc.u32(m['header'], 16): m for m in bundle.models.values()}
                    pc_only = [cc.model(pc_solids[hs]) for hs in pc_solids.keys() - x_solids.keys()]
                    x_only = [x_solids[hs] for hs in x_solids.keys() - pc_solids.keys()]
                    print(f'{car}: independent geometry intersection={len(pc_solids.keys() & x_solids.keys())} ' +
                          f'PC_only={[(m["name"],len(m["indices"])//3) for m in pc_only]} ' +
                          f'X360_only={[(m["name"],len(m["indices"])//3) for m in x_only]}')
                    for hs in pc_solids.keys() & x_solids.keys():
                        xmodel, pmodel = x_solids[hs], cc.model(pc_solids[hs])
                        assert xmodel['mesh'] == pmodel['mesh'], 'common mesh totals differ'
                        assert xmodel['textures'] == pmodel['textures'] and xmodel['lights'] == pmodel['lights'], 'common resource tables differ'
                        xv = xmodel['vertices'][xmodel['indices']]
                        pv = pmodel['vertices'][pmodel['indices']]
                        a = xv.copy().view('<f4').reshape(-1, 9)
                        b = pv.copy().view('<f4').reshape(-1, 9)
                        assert a.shape == b.shape, 'common indexed triangle counts differ'
                        assert np.allclose(a[:, :3], b[:, :3], atol=1e-6, rtol=0), 'common indexed positions differ'
                        assert np.allclose(a[:, 3:6], b[:, 3:6], atol=1.6e-5, rtol=0), 'common indexed normals differ'
                        assert np.allclose(a[:, 7:], b[:, 7:], atol=.0015, rtol=0), 'common indexed UVs differ'
                        assert np.array_equal(xv[:, 24:28], pv[:, 24:28]), 'common indexed colors differ'
                    wanted = bundle.shared_textures.keys()
                    pc_images = pc_texture_candidates(pglobal, wanted)
                    other = cc.unpack_file((pc / 'GLOBAL/INGAMEA.BUN').read_bytes())
                    pc_images.update(pc_texture_candidates(other, wanted - pc_images.keys()))
                    for hs in wanted - pc_images.keys():
                        blob = pc_shared_blobs[hs]
                        pc_images[hs] = [(blob[-148:-24], blob[-24:], blob[:-148])]
                    pixel_differences, max_mad = [], 0.
                    for hs, (r, pl, pixels) in bundle.shared_textures.items():
                        w, h = cc.words(r, 40, '<HH')
                        a = cx.decode_image(pl[12:16].decode('ascii'), w, h, pixels)
                        original = xarray.get(hs, extra_arrays.get(hs))
                        if original is not None:
                            native_kind, native_top = ci.shared_linear_top(*original)
                        else:
                            source_blob = dict(cc.texture_blobs(selected['CARS/TEXTURES.BIN']))[hs]
                            native_kind, native_top = cx.linear_top(cc.u32(source_blob[-32:], 20), w, h, source_blob[:-156])
                        native = cx.decode_image(native_kind, w, h, native_top)
                        reduce = lambda d: np.asarray(Image.fromarray(d[:, :, :3]).resize((32, 32), Image.Resampling.BOX)).astype(float)
                        codec_mad = float(np.abs(reduce(a) - reduce(native)).mean())
                        assert codec_mad <= 4., f'shared normalization RGB error: {cc.text_at(r, 89)} MAD={codec_mad}'
                        comparisons = []
                        for pr, ppl, ppixels in pc_images[hs]:
                            pw, ph = cc.words(pr, 40, '<HH')
                            assert w * ph == h * pw, 'shared texture aspect ratio differs'
                            b = cx.decode_image(ppl[12:16].decode('ascii'), pw, ph, ppixels)
                            mad = float(np.abs(reduce(native) - reduce(b)).mean())
                            encoded_mad = float(np.abs(reduce(a) - reduce(b)).mean())
                            comparisons.append((mad, ppl, ppixels, pw, ph, encoded_mad))
                        assert comparisons, f'shared dimensions differ: {cc.text_at(r, 89)}'
                        mad, ppl, ppixels, pw, ph, encoded_mad = min(comparisons, key=lambda row: row[0])
                        if (w, h) != (pw, ph):
                            print(f'{car}: shared resolution {cc.text_at(r, 89)} X360={w}x{h} PC={pw}x{ph} RGB_MAD={mad:.6f}')
                        if len(pc_images[hs]) > 1:
                            print(f'{car}: PC duplicate texture {cc.text_at(r, 89)} candidates={len(pc_images[hs])} matched_dimensions={len(comparisons)} MADs={[round(row[0],6) for row in comparisons]}')
                        max_mad = max(max_mad, mad)
                        if pixels != ppixels:
                            pixel_differences.append((cc.text_at(r, 89), native_kind, round(mad, 6), round(codec_mad, 6), round(encoded_mad, 6)))
                        assert mad <= 4., f'shared RGB mismatch: {cc.text_at(r, 89)} MAD={mad}'
                    used = {h for m in bundle.models.values() for h in m['textures']}
                    available = bundle.textures.keys() | bundle.shared_textures.keys() | bundle.runtime_bindings.keys() | bundle.texture_aliases.keys()
                    assert used <= available, 'incomplete texture dependency closure'
                    assert not any('x360_opaque' in m for m in bundle.models.values()), 'opaque stream leak'
                    bundle.check_unchanged()
                    print(f'{car}: solids={len(bundle.models)} car_images={len(bundle.textures)} shared_images={len(wanted)} runtime={len(bundle.runtime_bindings)} aliases={len(bundle.texture_aliases)} inputs={len(bundle.inputs)}')
                    print(f'{car}: wheel/render/paint/collision/physics exact PC; shared max_native_RGB_MAD={max_mad:.6f}; differences(name,native,PC_MAD,codec_MAD,encoded_PC_MAD)={pixel_differences}')
            finally:
                folder.close()
    finally:
        source.close()
    print('PASS: complete selected dependency bundles, same-source logical bytes, independent PC shared components')
    return 0


def math_finite_pair(x, p, off):
    import math
    return all(math.isfinite(cc.words(d, off, '<f')[0]) for d in (x, p))


def pc_texture_candidates(data, wanted):
    """Keep duplicate PC records visible; never silently choose a hash winner."""
    out = {}
    for r, pl, base, end in cc.texture_arrays(data, 8):
        hs = cc.u32(r, 12)
        if hs not in wanted:
            continue
        offset, top = cc.u32(r, 20), cc.u32(r, 36)
        assert base + offset + top <= end, 'PC reference pixel range'
        pixels = data[base + offset:base + offset + top]
        r = bytearray(r)
        struct.pack_into('<I', r, 28, top)
        entry = bytes(r), pl, pixels
        cc.validate_texture(*entry, 8, hs)
        out.setdefault(hs, []).append(entry)
    return out


if __name__ == '__main__':
    if '--broken-endian' in sys.argv:
        sys.argv.remove('--broken-endian')
        ci._little = lambda raw, unit=4: raw
    if '--broken-packed-top' in sys.argv:
        sys.argv.remove('--broken-packed-top')
        ci.packed_top_origin = lambda w, h, block: (0, 0)
    if '--assets' in sys.argv:
        raise SystemExit(asset_main())
    unittest.main()
