"""Illumination regression: old 54/54 zero-lit synthetic vertices; fixed 0/54.
Classified rims: old CCX 1728 zero-RGB vertices; fixed 0; stock-paint R8 2402 and 997TT 8200 -> 0 (catalog check).
Bare stock-lit/missing-stock fixture: old 3 failures (2 dark each), fixed 0; finish: old 0 rows -> fixed 1.
Missing source paint attributes: old 1/1 conversion errors; fixed 1/1 donor retained, 1 log line.

Local car format proof using original synthetic bytes, with no bundled assets.

Plan: exercise the extracted pack/assembly writers with independently built
geometry, TPK and VPAK fixtures; audit outputs and input digests; reject malformed
ranges and resource identities; compare optional installed assets independently.
Good: 9 full conversions, 3 DXT formats, 4 wheel layouts and 2 decodes.
Broken: 39/39 controls rejected; five required mutants are included:
bad offset, truncated chunk, missing stock part, namespace collision, changed
input hash after conversion. Source diffuse finish fields are read locally.
The optional parity test uses NFS_CARBON_PC and NFS_GAME; absent assets skip only
that case. Classified neutral illumination rejects the previous omission;
reference material differences must reduce to the excluded lamp rows.

Review regressions: good 4 written hashes, 6 decoded material roles, 1 valid
VPAK and 1 pinned-ancestor swap; broken 3 VPAKs copy zero payload bytes,
7 NUL paths give named errors, 1 pre-open ancestor swap is rejected, 4 material
record/missing-source controls are rejected, and 1 one-byte writer mutant is detected.
Old code: repeated VPAK copied 2,097,152 bytes; ancestor swap wrote into inputs;
7/7 NUL controls escaped the named wrapper; 2/2 corrupt/missing material records
were accepted using fixed source IDs.
Local decoded evidence: GLOBALB has 162 light records of 236 bytes, hash +0C
and name +14. The six matching name/hash records start at decoded chunk offsets
0D58, 2AE4, 2DC0, 3284, 477C, 4870. Their identities are now read at conversion
time, with no source hash or shader-parameter table. Written installed-car parity
passes for 848 solids / 21 textures / 13 unchanged inputs; the writer mutant
fails that same public-file parity check.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_convert as cc
from setup_kit import car_paint as paint
from setup_kit import huff


def pack(cid, raw):
    return struct.pack('<II', cid, len(raw)) + raw


def aligned(cid, pos, raw, boundary):
    return pack(cid, b'\x11' * (-(pos + 8) % boundary) + raw)


def nh(name):
    h = 0xFFFFFFFF
    for b in name.encode('ascii'):
        h = (h * 33 + b) & 0xFFFFFFFF
    return h


def triangle(name, carbon, effect=0, texture_role='DIFFUSE'):
    header = bytearray(160)
    struct.pack_into('<H', header, 12, 22)
    struct.pack_into('<II', header, 16, nh(name), 1)
    header[25:28] = bytes((1, 1, 0))
    struct.pack_into('<3f', header, 48, 1, 1, 0)
    struct.pack_into('<16f', header, 64, *(1. if i % 5 == 0 else 0. for i in range(16)))
    rawname = name.encode() + b'\0'
    header += rawname + bytes(-(160 + len(rawname)) % 4)
    material = bytearray(144 if carbon else 104)
    struct.pack_into('<I', material, 48, effect if carbon else 4)
    if carbon:
        for off, value in ((64, 3), (96, 1), (100, 0), (124, 3)):
            struct.pack_into('<I', material, off, value)
    else:
        struct.pack_into('<III', material, 60, 3, 1, 0)
        struct.pack_into('<I', material, 92, 3)
    meshhead = bytearray(52 if carbon else 48)
    struct.pack_into('<I', meshhead, 16, 1)
    struct.pack_into('<I', meshhead, 24, 1)
    struct.pack_into('<I', meshhead, 44, 3)
    vb = bytearray()
    for x, y in ((0., 0.), (1., 0.), (0., 1.)):
        native = struct.pack('<6f4B2f', x, y, 0., 0., 0., 1., 255, 255, 255, 255, x, y)
        if not carbon or effect == 0:
            vb += native
        elif effect == 4:
            vb += native + bytes(12)
        else:
            vb += native[:12] + native[28:36] + native[24:28] + native[12:24] + bytes(12)
    chunks = bytearray()
    for cid, raw, boundary in ((0x134011, header, 16),
                               (0x134012, struct.pack('<II', nh(name.split('_')[0] + '_' + texture_role), 0), 1),
                               (0x134013, struct.pack('<II', nh('HOSES'), 0), 1)):
        chunks += aligned(cid, 8 + len(chunks), raw, boundary)
    meshpos = 8 + len(chunks)
    mesh = bytearray()
    ib = struct.pack('<3H', 0, 1, 2) + (bytes(2) if carbon else b'')
    for cid, raw, boundary in ((0x134900, meshhead, 16), (0x134B02, material, 16),
                               (0x134B03, ib, 16), (0x134B01, vb, 16 if carbon else 128)):
        mesh += aligned(cid, meshpos + 8 + len(mesh), raw, boundary)
    mesh += pack(0x134C02, b'BODY\0\0\0\0')
    return pack(0x80134010, chunks + pack(0x80134100, mesh))


def stock_names(car):
    return ([f'{car}_BASE_{lod}' for lod in 'ABCD'] +
            [f'{car}_KIT00_BODY_{lod}' for lod in 'ABCD'] +
            [f'{car}_KIT00_{end}_BRAKE_A' for end in ('FRONT', 'REAR')] +
            [f'{car}_KIT00_FRONT_TIRE_{lod}' for lod in 'ABCD'])


def geometry(car, carbon, omit=(), effect=0, texture_role='DIFFUSE'):
    names = sorted(set(stock_names(car)) - set(omit), key=nh)
    info = bytearray(112)
    struct.pack_into('<II', info, 8, 29, len(names))
    def head(table):
        return pack(0x80134001, pack(0x134002, info) +
                    pack(0x134003, b''.join(struct.pack('<II', nh(n), 0) for n in names)) + pack(0x134004, table))
    header = head(bytes(24 * len(names)))
    body = bytearray(pack(0, b'') + header)
    table = bytearray()
    for name in names:
        padding = -(8 + len(body)) % 128
        if padding:
            if padding < 8:
                padding += 128
            body += pack(0, bytes(padding - 8))
        pos = len(body) + 8
        solid = triangle(name, carbon, effect, texture_role)
        table += struct.pack('<6I', nh(name), pos, len(solid), len(solid), 0, 0)
        body += solid
    body[8:8 + len(header)] = head(table)
    return pack(0x80134000, body)


def texture(car, version=8, fmt=b'DXT1', role='DIFFUSE'):
    name = car + '_' + role
    hs = nh(name)
    pixels = bytes(range(8 if fmt == b'DXT1' else 16))
    if version == 8:
        record, platform = bytearray(124), bytearray(24)
        struct.pack_into('<I', record, 12, hs)
        struct.pack_into('<I', record, 28, len(pixels))
        struct.pack_into('<I', record, 36, len(pixels))
        struct.pack_into('<HH', record, 40, 4, 4)
        record[44:47] = bytes((2, 2, {b'DXT1': 0x22, b'DXT3': 0x24, b'DXT5': 0x26}[fmt]))
        record[88] = len(name) + 1
        record[89:90 + len(name)] = name.encode() + b'\0'
        platform[12:16] = fmt
    else:
        record, platform = bytearray(124), bytearray(32)
        record[12:36] = name.encode().ljust(24, b'\0')
        struct.pack_into('<I', record, 36, hs)
        struct.pack_into('<I', record, 52, 0xFFFFFFFF)
        struct.pack_into('<I', record, 56, len(pixels))
        struct.pack_into('<I', record, 64, len(pixels))
        struct.pack_into('<HH', record, 68, 4, 4)
        record[72:75] = bytes((2, 2, {b'DXT1': 0x22, b'DXT3': 0x24, b'DXT5': 0x26}[fmt]))
        platform[20:24] = fmt
    blob = pixels + record + platform
    def header(table):
        return pack(0xB3310000, pack(0x33310001, struct.pack('<I', version) + bytes(28)) +
                    pack(0x33310002, struct.pack('<II', hs, 0)) + pack(0x33310003, table))
    prefix = pack(0, bytes(48))
    hd = header(bytes(24))
    datahead = pack(0x33320001, bytes(8))
    # Streaming offsets are absolute; all pixel starts are aligned to 128.
    pos = 8 + len(prefix) + len(hd) + 8 + len(datahead) + 8
    pad = -pos % 128
    table = struct.pack('<6I', hs, pos + pad, len(blob), len(blob), 0, 0)
    return pack(0xB3300000, prefix + header(table) + pack(0xB3320000, datahead + pack(0x33320002, b'\x11' * pad + blob)))


def attributes(car='CAR', modern=False, layout=False, stock=None):
    def vchunk(tag, data):
        return tag[::-1] + struct.pack('<I', len(data) + 8) + data
    values = {'TireOffsets': (0., 0., 0., .3) * 4, 'FECompressions': (0., 0.),
              'TireSkidWidth': (.2,) * 4, 'KitWheelOffsetFront': (0,) * 6, 'KitWheelOffsetRear': (0,) * 6}
    count = 5 + (stock is not None)
    binary, pointers, defs = bytearray(count * 16), [], []
    for i, (name, fmt, n) in enumerate(cc.WHEEL_FIELDS):
        at = len(binary)
        size = 16 if name == 'TireOffsets' else struct.calcsize(fmt)
        elements = n * struct.calcsize(fmt) // size
        binary += struct.pack('<4H', elements, elements, size, 0) + struct.pack('<' + str(n) + fmt, *values[name])
        defs.append(struct.pack('<IIHHHBB', cc.hash32(name), 0, at, size, elements, 2 if layout else 0, 4))
        pointers.append(at)
    if stock is not None:
        defs.append(struct.pack('<IIHHHBB', 0xAC97D3D1, 0, len(binary), 4, 1, 2 if layout else 0, 4))
        binary += struct.pack('<I', stock)
    binary[:count * 16] = b''.join(defs)
    dep = vchunk(b'DepN', struct.pack('<3I', 2, 0, 0))
    vers = vchunk(b'Vers', struct.pack('<I', 3)) if modern else b''
    ptrsize = 16 + 12 * (2 if layout else 6)
    expsize = 12 + 2 * (16 if modern else 20)
    datstart = len(vers) + len(dep) + ptrsize + expsize + 8
    classoff, coloff = datstart, datstart + 24
    classdata = bytearray(24)
    struct.pack_into('<I', classdata, 8, count)
    collection = bytearray(32)
    struct.pack_into('<II', collection, 0, cc.hash32(car.lower()), cc.hash32('ecar'))
    struct.pack_into('<I', collection, 20, 0 if layout else count)
    if layout:
        # Definitions use BIN-relative offsets; the layout pointer is its base.
        rows = [(classoff + 12, 3, 1, 0), (coloff + 28, 3, 1, 0)]
    else:
        for name, _, _ in cc.WHEEL_FIELDS:
            collection += struct.pack('<IIHBB', cc.hash32(name), 0, 0, 2, 0)
        rows = [(classoff + 12, 3, 1, 0)] + [(coloff + 32 + i * 12 + 4, 3, 1, at) for i, at in enumerate(pointers)]
        if stock is not None:
            collection += struct.pack('<IIHBB', 0xAC97D3D1, stock, 0, 0x20, 0)
    ptr = vchunk(b'PtrN', b''.join(struct.pack('<IHHI', *r) for r in rows) + bytes(8))
    if modern:
        exports = struct.pack('<I', 2) + struct.pack('<4I', cc.hash32('ecar'), 0x5E970CBC, 24, classoff)
        exports += struct.pack('<4I', 1, 0x8E112EB7, len(collection), coloff)
    else:
        exports = struct.pack('<I', 2) + struct.pack('<5I', cc.hash32('ecar'), 0x5E970CBC, 0, 24, classoff)
        exports += struct.pack('<5I', 1, 0x8E112EB7, 0, len(collection), coloff)
    vault = vers + dep + ptr + vchunk(b'ExpN', exports) + vchunk(b'DatN', classdata + collection)
    strings = b'synthetic\0'
    bo = 36 + len(strings)
    vo = bo + len(binary)
    return b'VPAK' + struct.pack('<3I', 1, 36, len(strings)) + struct.pack('<5I', 0, len(binary), len(vault), bo, vo) + strings + binary + vault


def assert_written_hashes(test, directory, expected):
    """Parity is measured from published files, including the writer's work."""
    actual = {name: cc.file_hash(directory / name) for name in expected}
    test.assertEqual(actual, expected, 'published output hashes')


class CarConversion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.car = self.root / 'source/CARS/CAR'
        self.donor = self.root / 'mw/CARS/DONOR'
        self.shared = self.root / 'source/GLOBAL'
        self.out = self.root / 'output'
        for p in (self.car, self.donor, self.shared, self.root / 'mw/GLOBAL'):
            p.mkdir(parents=True)
        (self.car / 'GEOMETRY.BIN').write_bytes(geometry('CAR', True))
        (self.donor / 'GEOMETRY.BIN').write_bytes(geometry('DONOR', False))
        (self.car / 'TEXTURES.BIN').write_bytes(texture('CAR'))
        (self.donor / 'TEXTURES.BIN').write_bytes(texture('DONOR', 5))
        (self.shared / 'GLOBALB.LZC').write_bytes(pack(0, b''))
        (self.shared / 'attributes.bin').write_bytes(attributes())
        material = bytearray(168)
        struct.pack_into('<I', material, 12, nh('HOSES'))
        (self.root / 'mw/GLOBAL/GLOBALA.BUN').write_bytes(pack(0x135200, material))

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, out=None):
        return cc.convert(self.car, self.donor, self.shared, out or self.out)

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for base in (self.root / 'source', self.root / 'mw') for p in base.rglob('*') if p.is_file()}

    def test_deterministic_complete_conversion(self):
        before = self.snapshot()
        a = self.convert()
        b = self.convert(self.root / 'output2')
        self.assertEqual(a['outputs'], b['outputs'])
        assert_written_hashes(self, self.out, a['outputs'])
        assert_written_hashes(self, self.root / 'output2', b['outputs'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(a['solids'], 14)
        self.assertEqual(len(a['outputs']), 5)
        self.assertEqual(len(a['inputs']), 7)
        geo = (self.out / 'GEOMETRY.BIN').read_bytes()
        models = cc.validate_geometry(geo)
        self.assertEqual(set(models), {n.replace('DONOR_', 'NFSAD_CAR_', 1) for n in stock_names('DONOR')})
        for name, model in models.items():
            # A source BASE plus BODY is an assembly even when the source
            # omits separate bumper/door parts at this LOD.
            count = 6 if '_BODY_' in name else 3
            self.assertEqual(len(model['vertices']), count)
            self.assertEqual(list(model['indices']), list(range(count)))
        self.assertEqual(cc.validate_textures((self.out / 'TEXTURES.BIN').read_bytes(), 5), {nh('NFSAD_CAR_DIFFUSE')})
        sm = (self.out / 'SOLIDMAP.BIN').read_bytes()
        self.assertEqual(struct.unpack_from('<4I', sm), (0x31504D53, 2, nh('CAR'), 15))
        rows = list(struct.iter_unpack('<II', sm[16:]))
        self.assertEqual(rows, sorted(rows))
        self.assertIn((nh('DONOR_DIFFUSE'), nh('NFSAD_CAR_DIFFUSE')), rows)
        wheel_bytes = (self.out / 'WHEELFIT.BIN').read_bytes()
        expected = struct.pack('<4I', 0x31544657, 1, cc.hash32('car'), cc.hash32('donor'))
        expected += struct.pack('<16f2f4f6B6B', *((0., 0., 0., .3) * 4 + (0., 0.) + (.2,) * 4 + (0,) * 12))
        self.assertEqual(wheel_bytes, expected)
        print('good: 2 deterministic conversions; 14 solids, 42 vertices/indices, 1 texture, 2 sidecars; 7 inputs unchanged')

    def test_writer_byte_mutant(self):
        result = self.convert()
        assert_written_hashes(self, self.out, result['outputs'])
        original = cc.write_outputs
        def broken_writer(out, files, protected):
            original(out, files, protected)
            path = out / 'WHEELFIT.BIN'
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 1
            path.write_bytes(raw)
        with mock.patch.object(cc, 'write_outputs', broken_writer):
            broken = self.convert(self.root / 'broken-writer')
        with self.assertRaises(AssertionError):
            assert_written_hashes(self, self.root / 'broken-writer', broken['outputs'])
        print('good: 4 written hashes match; broken: 1/1 one-byte writer mutant detected')

    def test_source_default_paint_output(self):
        key, swatch = nh('GLOSS_L1_COLOR01'), nh('SWATCH_COLOR01')
        target = {key: dict(name='GLOSS_L1_COLOR01', rgb=(10, 20, 30))}
        stock = dict(source_key=swatch, finish='GLOSS', rgb=(10, 20, 30))
        # The shared customization reader and palette are tested separately;
        # this full conversion proves publication of its sidecar and hash.
        (self.root / 'mw/GLOBAL/GLOBALB.LZC').write_bytes(pack(0, b''))
        with mock.patch.object(paint, 'stock_customization', return_value=stock), \
             mock.patch.object(paint, 'palette', return_value=target):
            result = self.convert()
        data = (self.out / 'PAINT.BIN').read_bytes()
        self.assertEqual(struct.unpack('<8I', data)[:7],
                         (0x31545044, 2, nh('CAR'), nh('DONOR'), swatch, key, 2))
        self.assertEqual(result['outputs']['PAINT.BIN'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['default_paint']['swatch'], 'GLOSS_L1_COLOR01')

    def test_missing_source_paint_attributes_retains_donor(self):
        selection = nh('STOCK_CAR')
        attrib = attributes(layout=True, stock=selection)
        self.assertEqual(cc.u32(cc.FitAttributes(attrib).raw('ecar', cc.hash32('car'), 0xAC97D3D1)), selection)
        (self.shared / 'attributes.bin').write_bytes(attrib)
        row = bytearray(104)
        row[8:18] = b'STOCK_CAR\0'
        struct.pack_into('<IIff', row, 40, nh('GLOSS'), nh('SWATCH_COLOR01'), 1., 1.)
        values = [('RED', 255), ('GREEN', 0), ('BLUE', 0), ('PART_NAME_SELECTOR', 2),
                  ('PART_NAME_BASE_HASH', nh('SWATCH')), ('PART_NAME_OFFSETS', 0xFFFF0000)]
        tables = [(0x34604, struct.pack('<BBH', 0, 0, 0)),
                  (0x34605, b''.join(struct.pack('<II', nh(n), v) for n, v in values)),
                  (0x34606, b'COLOR01\0'), (0x3460B, struct.pack('<I', nh('PAINT'))),
                  (0x3460C, struct.pack('<7H', 6, *range(6)))]
        complete = pack(0x30250, row) + b''.join(pack(cid, raw) for cid, raw in tables)
        self.assertEqual(paint.stock_customization(complete, attrib, 'CAR')['source_key'], nh('SWATCH_COLOR01'))
        source = pack(0x30250, row) + b''.join(pack(cid, raw) for cid, raw in tables if cid != 0x34605)
        (self.shared / 'GLOBALB.LZC').write_bytes(source)
        # This wheel-only vault adds just the stock link, not the optional render-fit schema.
        log = io.StringIO()
        with mock.patch.object(cc, 'render_fit', return_value=b''), contextlib.redirect_stderr(log):
            result = self.convert()
        data = (self.out / 'PAINT.BIN').read_bytes()
        self.assertEqual(struct.unpack('<8I', data)[:7],
                         (0x31545044, 2, nh('CAR'), nh('DONOR'), 0, 0, 0))
        self.assertEqual(result['outputs']['PAINT.BIN'], hashlib.sha256(data).hexdigest())
        self.assertIn('donor retained', result['default_paint']['reason'])
        self.assertEqual(len(log.getvalue().splitlines()), 1)
        self.assertIn('missing paint-part table', log.getvalue())
        self.assertIn('donor retained', log.getvalue())

    def test_named_path_errors(self):
        args = [self.car, self.donor, self.shared, self.out]
        for i in range(4):
            bad = args.copy()
            bad[i] = str(bad[i]) + '\0'
            with self.subTest(path=i), self.assertRaises(cc.FormatError):
                cc.convert(*bad)
        for key in ('carbon_global', 'attributes', 'mw_root'):
            shared = {'carbon_global': self.shared / 'GLOBALB.LZC', key: 'bad\0path'}
            with self.subTest(key=key), self.assertRaises(cc.FormatError):
                cc.convert(self.car, self.donor, shared, self.out)
        self.assertFalse(self.out.exists())
        print('broken: 7/7 embedded-NUL paths produce named errors')

    def test_parent_symlink_swap(self):
        # Swap an ancestor precisely before its open, after validation. The
        # existing destination leaf makes the old leaf-only open write inputs.
        for when in ('before', 'after'):
            parent = self.root / ('parent-' + when)
            parent.mkdir()
            out = parent / 'leaf'
            out.mkdir()
            destination = self.car / 'leaf'
            destination.mkdir(exist_ok=True)
            moved = parent.with_name(parent.name + '-moved')
            real_open = os.open
            swapped = False
            def intercepted(path, flags, *args, **kwargs):
                nonlocal swapped
                is_component = path == parent.name and kwargs.get('dir_fd') is not None
                is_old_open = Path(path) == out
                if not swapped and (is_component or is_old_open):
                    swapped = True
                    if when == 'after' and is_component:
                        fd = real_open(path, flags, *args, **kwargs)
                        parent.rename(moved)
                        parent.symlink_to(self.car, target_is_directory=True)
                        return fd
                    parent.rename(moved)
                    parent.symlink_to(self.car, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)
            before = self.snapshot()
            with mock.patch.object(cc.os, 'open', intercepted):
                if when == 'before':
                    with self.assertRaises(OSError):
                        cc.write_outputs(out, {'proof.bin': b'proof'}, (self.car,))
                else:
                    cc.write_outputs(out, {'proof.bin': b'proof'}, (self.car,))
            self.assertTrue(swapped)
            self.assertEqual(self.snapshot(), before)
            self.assertFalse((destination / 'proof.bin').exists())
            if when == 'after':
                self.assertEqual((moved / 'leaf/proof.bin').read_bytes(), b'proof')
        print('good: pinned ancestor survives swap; broken: 1/1 pre-open symlink swap rejected')

    def test_vpak_validates_before_copy(self):
        class Observed(bytes):
            copied = 0
            def __getitem__(self, key):
                value = super().__getitem__(key)
                if isinstance(key, slice) and len(value) >= 1024:
                    self.copied += len(value)
                return value
        count = 32
        strings = b''.join(f'n{i}\0'.encode() for i in range(count))
        start = 16 + count * 20 + len(strings)
        offsets, at = [], 0
        for i in range(count):
            offsets.append(at)
            at += len(f'n{i}') + 1
        directory = b''.join(struct.pack('<5I', no, 32768, 32768, start, start + 32768) for no in offsets)
        raw = b'VPAK' + struct.pack('<3I', count, 16 + count * 20, len(strings)) + directory + strings + bytes(65536)
        data = Observed(raw)
        with self.assertRaises(cc.FormatError):
            cc.read_vpak(data)
        self.assertEqual(data.copied, 0, 'invalid repeated ranges copied before validation')
        # Overlap whose aggregate still fits; late out-of-bounds section too.
        for offset in (start + 500, len(raw)):
            bad = bytearray(raw)
            struct.pack_into('<I', bad, 4, 1)
            struct.pack_into('<5I', bad, 16, 0, 1024, 1024, start, offset)
            observed = Observed(bad)
            with self.assertRaises(cc.FormatError):
                cc.read_vpak(observed)
            self.assertEqual(observed.copied, 0)
        self.assertEqual(len(cc.read_vpak(attributes())), 1)
        print('good: valid VPAK; broken: 3/3 invalid directories rejected with zero payload bytes copied')

    def test_material_ids_from_local_records(self):
        names = ('DAMAGE', 'DOORLINE', 'MAGMATTE', 'CARBONFIBER', 'MAGLIP', 'GRILLCHROME')
        targets = ('HOSES', 'DIABLOHP', 'CARBON_MAGMATTE', 'CARBONFIBER2', 'CHROME', 'CHROME')
        rows = bytearray()
        for name in names:
            row = bytearray(236)
            struct.pack_into('<I', row, 12, nh(name))
            row[20:20 + len(name) + 1] = name.encode() + b'\0'
            rows += pack(0x135200, row)
        expected = {nh(n): nh(t) if t else 0x22719FA9 for n, t in zip(names, targets)}
        self.assertEqual(cc.material_role_map(rows), expected)
        self.assertEqual(cc.material_role_map(pack(0, b'')), {})
        # No record means no source identity; a missing optional role cannot
        # silently reappear from the former fixed private table.
        partial = cc.material_role_map(rows[244:])
        self.assertNotIn(nh('DAMAGE'), partial)
        source = geometry('CAR', True)
        for source_hash, target_hash in expected.items():
            edited = bytearray(source)
            for cid, off, size in cc.walk(edited):
                if cid == 0x134013:
                    struct.pack_into('<I', edited, off + 8, source_hash)
            models, _ = cc.converted_models(edited, geometry('CAR', False), material_roles=expected)
            self.assertTrue(all(m['lights'] == [target_hash] for m in models.values()))
        for at in (20, 28):
            bad = bytearray(rows)
            bad[at] ^= 1
            with self.assertRaises(cc.FormatError):
                cc.material_role_map(bad)
        print('good: 6/6 decoded material roles used; broken: 2/2 record hash/name mutants rejected')

    def test_material_record_integrity(self):
        row = bytearray(236)
        struct.pack_into('<I', row, 12, nh('DAMAGE'))
        row[20:27] = b'DAMAGE\0'
        source = bytearray((self.car / 'GEOMETRY.BIN').read_bytes())
        for cid, off, _ in cc.walk(source):
            if cid == 0x134013:
                struct.pack_into('<I', source, off + 8, nh('DAMAGE'))
        (self.car / 'GEOMETRY.BIN').write_bytes(source)
        (self.shared / 'GLOBALB.LZC').write_bytes(pack(0x135200, row))
        self.convert()
        row[12] ^= 1
        for raw, message in ((pack(0x135200, row), 'material name/hash mismatch'),
                             (pack(0, b''), 'unresolved MW material')):
            (self.shared / 'GLOBALB.LZC').write_bytes(raw)
            with self.subTest(message=message), self.assertRaisesRegex(cc.FormatError, message):
                self.convert()

    def test_required_mutants(self):
        # Each control corrupts a separately regenerated input or publication step.
        original = (self.car / 'GEOMETRY.BIN').read_bytes()
        bad = bytearray(original)
        off, _ = cc.find(bad, 0x134004)
        struct.pack_into('<I', bad, off + 12, len(bad) + 1)
        (self.car / 'GEOMETRY.BIN').write_bytes(bad)
        with self.assertRaisesRegex(cc.FormatError, 'index outside file'):
            self.convert()
        (self.car / 'GEOMETRY.BIN').write_bytes(original[:-1])
        with self.assertRaises(cc.FormatError):
            self.convert()
        (self.car / 'GEOMETRY.BIN').write_bytes(geometry('CAR', True, omit=('CAR_BASE_A',)))
        with self.assertRaises(cc.MissingStockPartError):
            self.convert()
        (self.car / 'GEOMETRY.BIN').write_bytes(original)
        # A source image and solid would acquire the same namespace hash.
        (self.car / 'GEOMETRY.BIN').write_bytes(geometry('CAR', True, texture_role='BASE_A'))
        (self.car / 'TEXTURES.BIN').write_bytes(texture('CAR', role='BASE_A'))
        with self.assertRaises(cc.NamespaceCollisionError):
            self.convert()
        (self.car / 'GEOMETRY.BIN').write_bytes(original)
        (self.car / 'TEXTURES.BIN').write_bytes(texture('CAR'))
        real_write = cc.write_outputs
        def changed(out, files, protected):
            real_write(out, files, protected)
            p = self.car / 'TEXTURES.BIN'
            p.write_bytes(p.read_bytes() + b'x')
        with mock.patch.object(cc, 'write_outputs', changed), self.assertRaises(cc.InputChangedError):
            self.convert()
        print('broken: 5/5 required mutants rejected (offset, truncation, stock, namespace, input hash)')

    def test_wheel_vpak_bounds_and_layouts(self):
        expected = cc.wheel_sidecar(attributes(), 'CAR', 'DONOR')
        for modern in (False, True):
            for layout in (False, True):
                self.assertEqual(cc.wheel_sidecar(attributes(modern=modern, layout=layout), 'CAR', 'DONOR'), expected)
        original = attributes(modern=True)
        mutants = []
        for at, value in ((8, len(original) + 1), (12, len(original)), (16, 10000),
                          (20, len(original)), (24, len(original)), (28, len(original)), (32, len(original))):
            bad = bytearray(original)
            struct.pack_into('<I', bad, at, value)
            mutants.append(bad)
        mutants += [original[:n] for n in (0, 3, 15, 35, len(original) - 1)]
        for bad in mutants:
            with self.subTest(size=len(bad)), self.assertRaises(cc.FormatError):
                cc.wheel_sidecar(bad, 'CAR', 'DONOR')
        # Corrupt the pointer target and VLT parent extent after the VPAK is valid.
        _, binary, vault = cc.read_vpak(original)[0]
        vo = struct.unpack_from('<I', original, 32)[0]
        v = cc.WheelVault(binary, vault)
        ptr, _ = v.chunks[b'PtrN']
        bad = bytearray(original)
        struct.pack_into('<I', bad, vo + ptr + 16, len(binary) + 1)
        with self.assertRaises(cc.FormatError):
            cc.wheel_sidecar(bad, 'CAR', 'DONOR')
        print('good: 4 wheel layouts; broken: 13/13 VPAK/relocation mutants rejected')

    def test_vertex_layouts_and_dxt_formats(self):
        for effect in (0, 4, 5):
            (self.car / 'GEOMETRY.BIN').write_bytes(geometry('CAR', True, effect=effect))
            report = self.convert(self.root / f'effect{effect}')
            self.assertEqual(report['solids'], 14)
        for fmt in (b'DXT1', b'DXT3', b'DXT5'):
            data = texture('CAR', fmt=fmt)
            cc.validate_textures(data, 8)
            out, n = cc.convert_textures(data, texture('DONOR', 5))
            self.assertEqual(n, 2)
            cc.validate_textures(out, 5)
        print('good: 3 vertex layouts and 3 DXT formats')



    def test_classified_vertex_illumination(self):
        data = bytearray(geometry('CAR', True))
        for _, off, size, _, _, _ in cc.index(data):
            solid = data[off:off + size]
            vb, _ = cc.find(solid, 0x134B01)
            vb = off + ((vb + 23) & ~15)
            for i in range(3):
                data[vb + i * 36 + 24:vb + i * 36 + 28] = bytes(4)
        (self.car / 'GEOMETRY.BIN').write_bytes(data)
        self.convert()
        good = cc.validate_geometry((self.out / 'GEOMETRY.BIN').read_bytes())
        with mock.patch.object(cc, 'map_vertex_lighting', return_value=0):
            self.convert()
        bad = cc.validate_geometry((self.out / 'GEOMETRY.BIN').read_bytes())
        def dark(models):
            return sum(int((m['vertices'][:, 26] == 0).sum()) for m in models.values())
        self.assertEqual(dark(good), 0)
        self.assertEqual(dark(bad), 54)
        for n, m in good.items():
            self.assertEqual(m['vertices'][:, :24].tobytes(), bad[n]['vertices'][:, :24].tobytes())
            self.assertEqual(m['vertices'][:, 28:].tobytes(), bad[n]['vertices'][:, 28:].tobytes())
        # Missing stock paint evidence must leave black details alone.
        models = {'CAR_BASE_A': bad['NFSAD_CAR_BASE_A']}
        original = models['CAR_BASE_A']['vertices'].tobytes()
        self.assertEqual(cc.map_vertex_lighting(models), 0)
        self.assertEqual(models['CAR_BASE_A']['vertices'].tobytes(), original)
        print(f'illumination: old {dark(bad)} zero-lit vertices; fixed {dark(good)}; non-colour bytes exact')

    def test_rim_rgb_preserves_alpha_and_rubber(self):
        def record(count):
            r = bytearray(104)
            struct.pack_into('<I', r, 60, count)
            return bytes(r)
        paint = cc.np.zeros((3,36),dtype='u1')
        wheel = cc.np.zeros((7,36),dtype='u1')
        wheel[:,24:28] = [[0,0,0,0],[0,0,0,255],[10,20,30,255],[0,1,0,127],
                          [0,0,0,255],[10,10,10,255],[0,0,0,255]]
        before = wheel.copy()
        models = {'CAR_KIT00_BODY_A':dict(vertices=paint,records=[record(3)],names=[b'BODY']),
                  'CAR_KIT00_FRONT_TIRE_D':dict(vertices=wheel,records=[record(4),record(3)],
                                               names=[b'RIM_KIT00_GUN_METAL',b'TIRE_KIT00_TREAD'])}
        self.assertEqual(cc.map_vertex_lighting(models),5)
        self.assertEqual(wheel[:2,24:27].tobytes(),bytes([255])*6)
        self.assertEqual(wheel[:,27].tobytes(),before[:,27].tobytes())
        self.assertEqual(wheel[2:].tobytes(),before[2:].tobytes())
        self.assertEqual(cc.map_vertex_lighting(models),0)
        # The stock-paint gate must protect black body detail, independently of rims.
        for lit in (False, True):
            for stock_present in (False, True):
                with self.subTest(lit_paint=lit, stock_present=stock_present):
                    wheel[:] = before
                    paint[:] = 0
                    if lit: paint[:,24:28] = 255
                    detail = cc.np.zeros((2,36),dtype='u1')
                    fixture = {'CAR_FRONT_TIRE_A': models['CAR_KIT00_FRONT_TIRE_D'],
                               'CAR_DETAIL_A':dict(vertices=detail,records=[record(2)],names=[b'BOTTOM'])}
                    if stock_present: fixture['CAR_KIT00_BODY_A'] = models['CAR_KIT00_BODY_A']
                    expected = 7 if stock_present and not lit else 2
                    self.assertEqual(cc.map_vertex_lighting(fixture), expected)
                    self.assertEqual(wheel[:2,24:27].tobytes(), bytes([255])*6)
                    self.assertEqual(wheel[:,27].tobytes(), before[:,27].tobytes())
                    self.assertEqual(wheel[2:].tobytes(), before[2:].tobytes())
                    self.assertEqual(detail.any(), stock_present and not lit)
        print('rim fixture: old stock-lit/missing-stock 2 dark -> fixed 0; alpha/rubber/body AO exact')

    def test_source_rim_finish(self):
        source = bytearray(236); struct.pack_into('<II',source,8,3,nh('MAGMATTE'))
        source[20:29] = b'MAGMATTE\0'
        struct.pack_into('<11f',source,0x64,*range(1,12))
        native = bytearray(168); struct.pack_into('<II',native,8,3,nh('MAGSILVER'))
        native[20:30] = b'MAGSILVER\0'
        struct.pack_into('<30f',native,0x30,*range(31,61))
        raw = pack(0x135200,source); mw = pack(0x135200,native)
        row = cc.source_rim_rows(raw,mw)[nh('CARBON_MAGMATTE')]
        self.assertEqual(row[0x30:0x5c],source[0x64:0x90])
        self.assertEqual(row[0x5c:],native[0x5c:])
        self.assertNotEqual(nh('CARBON_MAGMATTE'),nh('MAGSILVER'))
        for broken in (raw+raw,raw[:20]+bytes(4)+raw[24:]):
            with self.assertRaises(cc.FormatError): cc.source_rim_rows(broken,mw)
        print('finish: old 0 distinct rows; fixed 1; 44 source bytes, 76 MW lighting bytes exact; 2 mutants rejected')

    def test_mesh_and_texture_audit_mutants(self):
        original = geometry('CAR', True)
        hs, off, size, _, _, _ = cc.index(original)[0]
        solid = original[off:off + size]
        material, _ = cc.find(solid, 0x134B02)
        material = (material + 23) & ~15
        indices, _ = cc.find(solid, 0x134B03)
        indices = (indices + 23) & ~15
        vertices, _ = cc.find(solid, 0x134B01)
        vertices = (vertices + 23) & ~15
        mutants = []
        for at, fmt, value in ((material + 24, '<B', 5), (indices, '<H', 65535),
                                (vertices, '<f', float('nan')), (vertices + 12, '<f', 2.),
                                (material + 124, '<I', 6)):
            data = bytearray(original)
            struct.pack_into(fmt, data, off + at, value)
            mutants.append(data)
        hashes, _ = cc.find(original, 0x134003)
        data = bytearray(original)
        struct.pack_into('<I', data, hashes + 8, hs ^ 1)
        mutants.append(data)
        for data in mutants:
            (self.car / 'GEOMETRY.BIN').write_bytes(data)
            with self.assertRaises(cc.FormatError):
                self.convert()
        (self.car / 'GEOMETRY.BIN').write_bytes(original)
        original = texture('CAR')
        e = cc.index(original, 0x33310003)[0]
        tail = e[1] + e[2] - 148
        for at, fmt, value in ((tail + 28, '<I', 7), (tail + 88, '<B', 255),
                                (tail + 44, '<B', 31), (e[1] + e[2] - 12, '<I', 0)):
            data = bytearray(original)
            struct.pack_into(fmt, data, at, value)
            (self.car / 'TEXTURES.BIN').write_bytes(data)
            with self.assertRaises(cc.FormatError):
                self.convert()
        self.assertFalse(self.out.exists(), 'failed audits must not create outputs')
        print('broken: 10/10 mesh/resource/index/texture audit mutants rejected before publication')

    def test_input_paths_and_symlink_outputs(self):
        before = self.snapshot()
        for out in (self.car, self.shared / 'output', self.donor / 'out', self.root):
            with self.subTest(out=out), self.assertRaises(cc.FormatError):
                self.convert(out)
        redirect = self.root / 'redirect'
        redirect.symlink_to(self.car, target_is_directory=True)
        with self.assertRaises(cc.FormatError):
            self.convert(redirect)
        self.out.mkdir()
        (self.out / 'GEOMETRY.BIN').symlink_to(self.car / 'GEOMETRY.BIN')
        self.convert()
        self.assertFalse((self.out / 'GEOMETRY.BIN').is_symlink())
        self.assertEqual(before, self.snapshot())
        # Inputs may themselves point into an otherwise unrelated output tree.
        redirected = self.root / 'input-target'
        redirected.mkdir()
        target = redirected / 'GEOMETRY.BIN'
        target.write_bytes((self.car / 'GEOMETRY.BIN').read_bytes())
        (self.car / 'GEOMETRY.BIN').unlink()
        (self.car / 'GEOMETRY.BIN').symlink_to(target)
        with self.assertRaises(cc.FormatError):
            self.convert(redirected)
        self.assertEqual(target.read_bytes(), geometry('CAR', True))
        print('good: symlink output replaced safely; broken: 6/6 protected paths rejected')


    def test_indexed_compression_and_padding(self):
        original = geometry('CAR', True)
        entries = cc.index(original)
        io, _ = cc.find(original, 0x134004)
        for kind in ('HUFF', 'RAWW'):
            data = bytearray(original[:entries[0][1]])
            rewritten = bytearray()
            for hs, off, stored, size, _, zero in entries:
                solid = original[off:off + stored]
                if kind == 'HUFF':
                    packed, flags = huff.fixture(solid), 0x100
                else:
                    raw = b'RAWW\x01\x10\0\0' + struct.pack('<II', size, size) + solid
                    packed = struct.pack('<6I', 0x55441122, size, 24 + len(raw), 0, 0, 0) + raw
                    flags = 0x200
                rewritten += struct.pack('<6I', hs, len(data), len(packed), size, flags, zero)
                data += packed
            struct.pack_into('<I', data, 4, len(data) - 8)
            data[io + 8:io + 8 + len(rewritten)] = rewritten
            # A checked outer padding chunk is legal on input and is omitted
            # by the deterministic writer.
            data += pack(0, bytes(40))
            (self.car / 'GEOMETRY.BIN').write_bytes(data)
            self.assertEqual(self.convert(self.root / kind)['solids'], 14)
        malformed = bytearray(data)
        malformed[-1] = 1
        (self.car / 'GEOMETRY.BIN').write_bytes(malformed)
        with self.assertRaisesRegex(cc.FormatError, 'trailer'):
            self.convert()
        print('good: 2 compressed indexed conversions with outer padding; broken: 1/1 trailer mutant rejected')

    def test_compression_bounds(self):
        payload = b'synthetic data'
        raw = b'RAWW\x01\x10\0\0' + struct.pack('<II', len(payload), len(payload)) + payload
        block = struct.pack('<6I', 0x55441122, len(payload), 24 + len(raw), 0, 0, 0) + raw
        self.assertEqual(cc.unblock(block), payload)
        self.assertEqual(cc.decompress(huff.fixture(payload)), payload)
        broken = [raw[:-1], raw[:12], block[:-1]]
        for data in broken:
            with self.assertRaises(cc.FormatError):
                cc.unblock(data) if data[:4] == block[:4] else cc.decompress(data)
        bad = bytearray(block)
        struct.pack_into('<I', bad, 12, cc.MAX_FILE)
        with self.assertRaises(cc.FormatError):
            cc.unblock(bad)
        print('good: RAWW/HUFF decoded; broken: 4/4 compression mutants rejected')


class StockPaintSelection(unittest.TestCase):
    """Selection: 5/5 cases; donor-only control rejected 1/1.
    Generated identity: 2/2 fixtures pass; display-label decoder mutant fails 1/1 test.
    """

    def test_generated_part_identity_ignores_display_label(self):
        for brand, selector, suffix, expected in [
                ('GLOSS', 1, ('L1', 'COLOR17'), 'GLOSS_L1_COLOR17'),
                ('SWATCH', 2, ('COLOR41',), 'SWATCH_COLOR41')]:
            strings = bytearray(b'WRONG_DISPLAY_LABEL\0')
            indices = []
            for text in suffix:
                strings += bytes(-len(strings) % 4)
                indices.append(len(strings) // 4)
                strings += text.encode() + b'\0'
            offsets = indices[0] | ((indices[1] if len(indices) == 2 else 0xFFFF) << 16)
            attrs = [('RED', 219), ('GREEN', 0), ('BLUE', 255), ('NAME_OFFSET', 0),
                     ('PART_NAME_SELECTOR', selector), ('PART_NAME_OFFSETS', offsets),
                     ('BRAND_NAME' if selector == 1 else 'PART_NAME_BASE_HASH', nh(brand))]
            source = b''.join(pack(cid, data) for cid, data in [
                (0x34604, struct.pack('<BBH', 0, 0, 0)),
                (0x34605, b''.join(struct.pack('<II', nh(n), v) for n, v in attrs)),
                (0x34606, bytes(strings)), (0x3460B, struct.pack('<I', nh('PAINT'))),
                (0x3460C, struct.pack('<' + str(len(attrs)+1) + 'H', len(attrs), *range(len(attrs))))])
            self.assertEqual(paint.palette(source, carbon=True),
                             {nh(expected): dict(name=expected, rgb=(219, 0, 255))})

    def test_finish_precedes_nearer_colour(self):
        target = {10: dict(name='GLOSS_L1_COLOR01', rgb=(0, 0, 0)),
                  20: dict(name='METAL_L1_COLOR01', rgb=(10, 10, 10))}
        stock = dict(finish='METALLIC', rgb=(0, 0, 0))
        self.assertEqual(paint.resolve(stock, target)[:2], (20, 2))

    def test_nearest_colour_and_stable_tie(self):
        target = {20: dict(name='GLOSS_L1_COLOR02', rgb=(12, 10, 10)),
                  10: dict(name='GLOSS_L1_COLOR01', rgb=(8, 10, 10)),
                  5: dict(name='GLOSS_L1_COLOR03', rgb=(255, 255, 255))}
        stock = dict(finish='GLOSS', rgb=(10, 10, 10))
        for rows in (target, dict(reversed(list(target.items())))):
            mapped, method, why = paint.resolve(stock, rows)
            self.assertEqual((mapped, method), (10, 2))
            self.assertIn('squared distance=4', why)
        with mock.patch.object(paint, 'resolve', return_value=(0, 0, 'donor')):
            with self.assertRaises(AssertionError):
                self.assertEqual(paint.resolve(stock, target)[:2], (10, 2))

    def test_missing_finish_retains_donor(self):
        self.assertEqual(paint.resolve(None, {})[:2], (0, 0))
        stock = dict(finish='METALLIC', rgb=(0, 0, 0))
        target = {10: dict(name='GLOSS_L1_COLOR01', rgb=(0, 0, 0))}
        self.assertEqual(paint.resolve(stock, target)[:2], (0, 0))

    def test_missing_selection_does_not_read_target(self):
        for key in (0, 0xFFFFFFFF):
            record = bytearray(208)
            record[:4] = b'CAR\0'
            struct.pack_into('<I', record, 204, key)
            source = pack(0x34600, bytes(8) + record)
            read_target = mock.Mock(side_effect=AssertionError('unneeded target read'))
            for car in ('CAR', 'ABSENT'):
                blob, report = paint.metadata(source, read_target, car, 'DONOR')
                self.assertEqual(struct.unpack('<8I', blob)[5:7], (0, 0))
                self.assertIsNone(report['swatch'])
            read_target.assert_not_called()


class InstalledCarParity(unittest.TestCase):
    def test_optional_installed_car(self):
        source_root = os.environ.get('NFS_CARBON_PC')
        if not source_root:
            self.skipTest('NFS_CARBON_PC is unset; synthetic conversion proof still runs')
        source_root = Path(source_root).expanduser().resolve()
        mw_root = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')).expanduser().resolve()
        if not (mw_root / 'CARS').is_dir():
            self.skipTest('MW car folder is absent')
        oracle_dir = ROOT / 'tools_snapshot'
        if not (oracle_dir / 'carbon_car.py').is_file():
            self.skipTest('local reference converter is absent')
        source_cars = source_root / 'CARS'
        self.assertTrue(source_cars.is_dir(), 'NFS_CARBON_PC must name a PC installation')
        donors = {p.name.upper(): p for p in (mw_root / 'CARS').iterdir() if p.is_dir() and
                  all((p / n).is_file() for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))}
        choices = sorted((p for p in source_cars.iterdir() if p.is_dir() and p.name.upper() in donors and
                          all((p / n).is_file() for n in ('GEOMETRY.BIN', 'TEXTURES.BIN'))), key=lambda p: p.name.upper())
        self.assertTrue(choices, 'no common source/donor car exists')
        sys.path.insert(0, str(oracle_dir))
        try:
            import carbon_car as oracle
            import carbon_namespace as names
            import wheel_attrib as wheels
            wheel_reader = wheels.Wheels(source_root / 'GLOBAL/attributes.bin', 'carbon')
            car = donor = None
            for candidate in choices:
                if wheels.hash32(candidate.name.lower()) not in wheel_reader.collections:
                    continue
                reference = donors[candidate.name.upper()]
                models, _ = cc.converted_models((candidate / 'GEOMETRY.BIN').read_bytes(),
                                                (reference / 'GEOMETRY.BIN').read_bytes())
                stock = set(stock_names(candidate.name.upper())[:10])
                if stock <= models.keys():
                    car, donor = candidate, reference
                    break
            self.assertIsNotNone(car, 'no common car has stock assemblies and wheel attributes')
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                # The default reference contains the excluded material treatment.
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.dict(os.environ, {'NFS_GAME': str(mw_root), 'NFS_CARBON_LAMPGLASS': 'on'}):
                        default = names.convert(car, donor, work / 'default', revision=2)
                    self.assertFalse(default.get('missing_stock'), default)
                    with mock.patch.dict(os.environ, {'NFS_GAME': str(mw_root), 'NFS_CARBON_LAMPGLASS': 'off'}):
                        baseline = names.convert(car, donor, work / 'baseline', revision=2)
                    self.assertFalse(baseline.get('missing_stock'), baseline)
                    result = cc.convert(car, donor, source_root / 'GLOBAL', work / 'public')
                assert_written_hashes(self, work / 'public', result['outputs'])
                classifications = {}
                for name in result['outputs']:
                    actual = cc.file_hash(work / 'public' / name)
                    self.assertEqual(actual, result['outputs'][name], f'{name}: written bytes differ from report')
                    expected = hashlib.sha256((work / 'baseline' / name).read_bytes()).hexdigest()
                    self.assertEqual(actual, expected, f'{name}: unclassified difference from material-neutral reference')
                    default_hash = hashlib.sha256((work / 'default' / name).read_bytes()).hexdigest()
                    classifications[name] = 'byte-identical' if actual == default_hash else 'excluded lamp material rows/identities'
                    if name != 'GEOMETRY.BIN':
                        self.assertEqual(actual, default_hash, f'{name}: unexpected difference from default reference')
                # Independently prove the default geometry differs only in lamp
                # resource hashes and its two trailing material chunks.
                reference = (work / 'default/GEOMETRY.BIN').read_bytes()
                plain = (work / 'baseline/GEOMETRY.BIN').read_bytes()
                ref_models = {}
                reverse = {oracle.name_hash(v): h for h, v in oracle.CARBON_LAMP_MATERIALS.items()}
                for hs, solid in oracle.solids(reference):
                    data = bytearray(solid)
                    off, size = oracle.find(data, 0x134013)
                    for at in range(off + 8, off + 8 + size, 8):
                        h = oracle.u32(data, at)
                        struct.pack_into('<I', data, at, reverse.get(h, h))
                    ref_models[hs] = bytes(data)
                self.assertEqual(ref_models, dict(oracle.solids(plain)), 'geometry changes exceed material identities')
                tail = reference[8 + oracle.u32(reference, 4):]
                lamp_rows = [oracle.u32(tail, o + 20) for c,o,z in oracle.children(tail) if c == 0x135200
                             and oracle.u32(tail, o + 20) in reverse]
                self.assertEqual(set(lamp_rows), set(reverse))
                for path, expected in result['inputs'].items():
                    self.assertEqual(cc.file_hash(Path(path)), expected)
                print('real-car parity:', car.name, result['solids'], 'solids,', result['textures'], 'textures,',
                      len(result['inputs']), 'inputs unchanged;', classifications, result['outputs'])
        finally:
            sys.path.remove(str(oracle_dir))


if __name__ == '__main__':
    unittest.main(verbosity=2)
