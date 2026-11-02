"""Synthetic Xenon adapter proof; no source assets are stored in these fixtures.

Good: 13 tests pass. Broken linear-tiling decoder: 4 failures, exit 1.
The auxiliary-stream leak control is also rejected by the common-output check.
Plan: exercise independent mixed-effect streams, aligned chunks, indexed packs,
all five pixel layouts and both BC4 endpoint modes; feed the common writers.
Corruption tests cover compression, endian, tiling, tails, identity and ranges.
Run with --broken-tiling to deliberately substitute a linear texture decoder.
Private cross-platform comparisons live in test_car_x360_assets.py (exit 3 when
its input environment is absent).
"""
import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_convert as cc, car_x360 as cx, huff

FORMAT_SPECS = {0x1A200152: ('DXT1', 8, 0x22), 0x1A200153: ('DXT3', 16, 0x24),
                0x1A200154: ('DXT5', 16, 0x26), 0x1A200171: ('BC5', 16, 0x27),
                0x18280086: ('A8R8G8B8', 4, 0x20)}


def chunk(cid, raw):
    return struct.pack('<II', cid, len(raw)) + raw


def aligned(cid, pos, raw, boundary):
    return chunk(cid, bytes([17]) * (-(pos + 8) % boundary) + raw)


def solid():
    """Four independently indexed draws, including repeated effects and markers."""
    name = 'SYNTH_KIT00_BODY_A'
    header = bytearray(160)
    struct.pack_into('<H', header, 12, 22)
    struct.pack_into('<II', header, 16, cc.name_hash(name), 8)
    header[25:28] = bytes((2, 1, 1))
    struct.pack_into('<3f', header, 32, -2, -3, 0)
    struct.pack_into('<3f', header, 48, 3, 2, 1)
    transform = struct.pack('<16f', *(1. if i % 5 == 0 else 0. for i in range(16)))
    header[64:128] = transform
    header += name.encode() + b'\0'
    header += bytes(-len(header) % 4)
    marker = struct.pack('<4I', cc.name_hash('SYNTH_MOUNT'), 0, 0, 0) + transform
    body = bytearray()
    for cid, raw, boundary in ((0x134011, header, 16),
                               (0x134012, struct.pack('<4I', 101, 0, 102, 0), 1),
                               (0x134013, struct.pack('<2I', 103, 0), 1),
                               (0x13401a, marker, 16)):
        body += aligned(cid, 8 + len(body), raw, boundary)
    mh, recs, buffers, expected = bytearray(80), bytearray(), [], []
    struct.pack_into('<H', mh, 8, 17)
    struct.pack_into('<I', mh, 12, 0x4180)
    struct.pack_into('<I', mh, 16, 4)
    struct.pack_into('<I', mh, 64, 8)
    struct.pack_into('<I', mh, 72, 16)
    for draw, effect in enumerate((2, 7, 7, 8)):
        vb = bytearray()
        for j, (x, y) in enumerate(((-2., -3.), (3., -3.), (3., 2.), (-2., 2.))):
            uv, colour = (j / 4., draw / 8.), bytes((10 + draw, 20 + j, 30, 255))
            position = struct.pack('>3f', x, y, float(draw & 1))
            packed_normal = struct.pack('>4h', 0, 0, 32767, 0)
            if effect == 2:
                vb += position + colour[::-1] + struct.pack('>2f', *uv) + packed_normal
            else:
                vb += position + struct.pack('>2f', *uv) + colour[::-1] + packed_normal
                if effect == 8:
                    vb += struct.pack('>4h', -32768, 0, 0, 32767)
            expected.append(struct.pack('<6f', x, y, float(draw & 1), 0., 0., 1.) + colour + struct.pack('<2f', *uv))
        r = bytearray(128)
        struct.pack_into('<4I', r, 0, draw * 6, effect, 104 + draw, 0x4180 + draw)
        struct.pack_into('<H', r, 16, 6)
        r[18] = 255
        r[19:24] = bytes((0, 1, 0, 1, 0))
        struct.pack_into('<II', r, 116, len(vb), 0x80280000 if effect == 8 else 0x80200000)
        recs += r
        buffers.append(vb)
    mesh, meshpos = bytearray(), 8 + len(body)
    for cid, raw, boundary in ((0x134900, mh, 16), (0x134b02, recs, 128),
                               (0x134b03, struct.pack('>24H', *((2, 0, 1, 3, 0, 2) * 4)), 32)):
        mesh += aligned(cid, meshpos + 8 + len(mesh), raw, boundary)
    for vb in buffers:
        mesh += aligned(0x134b01, meshpos + 8 + len(mesh), vb, 128)
    for label in (b'', b'SYNTH_MATERIAL\0', b'', b''):
        mesh += chunk(0x134c02, label)
    body += chunk(0x80134100, mesh)
    for cid, size in ((0x134017, 12), (0x134018, 8), (0x134019, 12)):
        body += chunk(cid, bytes(size))
    return chunk(0x80134010, body), b''.join(expected)


def indexed_pack(resources, texture=False, version=8):
    table_kind = 0x33310003 if texture else 0x134004
    def header(table):
        hashes = b''.join(struct.pack('<II', h, 0) for h, _ in resources)
        if texture:
            parts = chunk(0x33310001, struct.pack('<I', version)) + chunk(0x33310002, hashes)
        else:
            info = bytearray(112)
            struct.pack_into('<II', info, 8, 29, len(resources))
            parts = chunk(0x134002, info) + chunk(0x134003, hashes)
        return chunk(0xB3310000 if texture else 0x80134001, parts + chunk(table_kind, table))
    hh = header(bytes(24 * len(resources)))
    prefix = chunk(0, b'') + hh
    # Texture writers also retain this pack identity field.
    prefix += chunk(0xB3320000, chunk(0x33320001, struct.pack('<I', 123))) if texture else b''
    at = 8 + len(prefix) + (8 if texture else 0)
    table, body = bytearray(), bytearray()
    for hs, raw in resources:
        table += struct.pack('<6I', hs, at + len(body), len(raw), len(raw), 0, 0)
        body += raw
    prefix = prefix[:8] + header(table) + prefix[8 + len(hh):]
    return chunk(0xB3300000 if texture else 0x80134000,
                 prefix + (chunk(0x33320002, body) if texture else body))


def tile_offset(x, y, width, bpb):
    """Scalar address reference from the shared texture importer."""
    return cx._pixels().tiled_offset(x, y, width, bpb.bit_length() - 1)


def tiled_bytes(linear, width, height, bpb):
    wb, hb = (width + 3) // 4, (height + 3) // 4
    aw, ah = (wb + 31) & ~31, (hb + 31) & ~31
    # Allocate the address span, not merely the logical image's byte count.
    max_offset = max(tile_offset(x, y, aw, bpb) for y in range(ah) for x in range(aw))
    out = bytearray((max_offset + 1) * bpb)
    for y in range(hb):
        for x in range(wb):
            at = tile_offset(x, y, aw, bpb) * bpb
            block = linear[(y * wb + x) * bpb:(y * wb + x + 1) * bpb]
            out[at:at + bpb] = np.frombuffer(block, np.uint8).reshape(-1, 2)[:, ::-1].tobytes()
    return bytes(out)


def texture_blob(fmt, width=128, height=128):
    name = 'SYNTH_DIFFUSE'
    hs = cc.name_hash(name)
    kind, bpb, code = FORMAT_SPECS[fmt]
    if kind == 'A8R8G8B8':
        # Non-symmetric channels and row padding expose byte and pitch errors.
        linear = np.arange(width * height * 4, dtype=np.uint32).astype(np.uint8).tobytes()
        pitch = (width * 4 + 255) & ~255
        pixels = bytearray(pitch * height)
        a = np.frombuffer(linear, np.uint8).reshape(height, width * 4)
        for y in range(height):
            pixels[y * pitch:y * pitch + width * 4] = a[y].reshape(-1, 4)[:, ::-1].tobytes()
    else:
        n = ((width + 3) // 4) * ((height + 3) // 4) * bpb
        linear = ((np.arange(n, dtype=np.uint32) * 37 + np.arange(n, dtype=np.uint32) // bpb) & 255).astype(np.uint8).tobytes()
        pixels = tiled_bytes(linear, width, height, bpb)
    record, platform = bytearray(124), bytearray(32)
    struct.pack_into('<I', record, 12, hs)
    struct.pack_into('<I', record, 28, len(pixels))
    top = pitch * height if kind == 'A8R8G8B8' else ((width // 4 + 31) & ~31) * ((height // 4 + 31) & ~31) * bpb
    struct.pack_into('<I', record, 36, top)
    struct.pack_into('<HH', record, 40, width, height)
    record[44:47] = bytes((width.bit_length() - 1, height.bit_length() - 1, code))
    record[50] = 1
    record[88] = len(name) + 1
    record[89:90 + len(name)] = name.encode() + b'\0'
    struct.pack_into('<I', platform, 20, fmt)
    return hs, bytes(pixels + record + platform), linear


def changed(data, offset, fmt, value):
    out = bytearray(data)
    struct.pack_into(fmt, out, offset, value)
    return bytes(out)


class GeometryTests(unittest.TestCase):
    def test_components_and_rebasing(self):
        raw, expected = solid()
        m = cx.source_model(raw)
        self.assertEqual(m['vertices'].tobytes(), expected)
        self.assertEqual(m['indices'].tolist(), [i * 4 + j for i in range(4) for j in (2, 0, 1, 3, 0, 2)])
        self.assertEqual(m['names'], [b'\0', b'SYNTH_MATERIAL\0', b'\0', b'\0'])
        self.assertEqual(m['textures'], [101, 102])
        self.assertEqual(m['lights'], [103])
        self.assertEqual(cc.words(m['mesh'], 8, '<II'), (18, 0x4180))
        self.assertEqual(len(cc.marker_records(m)), 1)
        self.assertEqual([cid for cid, _ in m['x360_opaque']], [0x134017, 0x134018, 0x134019])
        self.assertFalse(any(cid in cx.OPAQUE_CHUNKS for cid, _ in m['extras']))
        for i, r in enumerate(m['records']):
            self.assertEqual(cc.words(r, 60, '<III'), (4, 2, i * 6))
            self.assertEqual(cc.u32(r, 56), 0x4180 + i)
            self.assertEqual(r[24:30], bytes((0, 1, 0, 1, 0, 255)))
        with self.assertRaisesRegex(cc.FormatError, '52'):
            cc.model(raw)

    def test_common_geometry_writer(self):
        raw, expected = solid()
        hs = cc.u32(cc.payload(raw, 0x134011, 16), 16)
        data = indexed_pack([(hs, raw)])
        models = cx.source_models(data)
        output, report = cc.write_geometry(data, models)
        decoded = cc.validate_geometry(output)
        self.assertEqual(report['solids'], 1)
        self.assertEqual(decoded['SYNTH_KIT00_BODY_A']['vertices'].tobytes(), expected)
        self.assertEqual(decoded['SYNTH_KIT00_BODY_A']['indices'].tolist(), models['SYNTH_KIT00_BODY_A']['indices'].tolist())
        def assert_safe(data):
            self.assertFalse(any(cid in (0x134017, 0x134018, 0x134019) for cid, _, _ in cc.walk(data)))
        assert_safe(output)
        with mock.patch.object(cx, 'OPAQUE_CHUNKS', frozenset()):
            leaked_models = cx.source_models(data)
        broken_output, _ = cc.write_geometry(data, leaked_models)
        with self.assertRaises(AssertionError):
            assert_safe(broken_output)

    def test_geometry_corruptions(self):
        raw, _ = solid()
        mh = cc.find(raw, 0x134900)[0]; mh = (mh + 23) & ~15
        rb = cc.find(raw, 0x134b02)[0]; rb = (rb + 135) & ~127
        ib = cc.find(raw, 0x134b03)[0]; ib = (ib + 39) & ~31
        vb = next(o for c, o, z in cc.walk(raw) if c == 0x134b01); vb = (vb + 135) & ~127
        hd = cc.find(raw, 0x134011)[0]; hd = (hd + 23) & ~15
        cases = dict(version=(mh + 8, '<H', 18), draws=(mh + 16, '<I', 5),
                     triangles=(mh + 64, '<I', 9), vertices=(mh + 72, '<I', 17),
                     effect=(rb + 4, '<I', 99), stride=(rb + 120, '<I', 0x80280000),
                     bytes=(rb + 116, '<I', 127), coverage=(rb + 128, '<I', 0),
                     count=(rb + 16, '<H', 5), index=(ib, '>H', 4),
                     position=(vb, '>f', 10000.), nan=(vb, '>f', float('nan')),
                     texture=(rb + 19, 'B', 2), light=(rb + 18, 'B', 1),
                     resources=(hd + 25, 'B', 3), identity=(hd + 16, '<I', 1))
        for label, (off, fmt, value) in cases.items():
            with self.subTest(label=label), self.assertRaises(cc.FormatError):
                cx.source_model(changed(raw, off, fmt, value))
        for length in (0, 7, len(raw) - 1):
            with self.subTest(length=length), self.assertRaises(cc.FormatError):
                cx.source_model(raw[:length])

    def test_wrong_index_endian_decoder(self):
        raw, _ = solid()
        original = np.frombuffer
        def broken(data, dtype=float, *args, **kwargs):
            return original(data, '<u2' if dtype == '>u2' else dtype, *args, **kwargs)
        with mock.patch.object(cx.np, 'frombuffer', broken), self.assertRaisesRegex(cc.FormatError, 'local vertex'):
            cx.source_model(raw)

    def test_signed_packed_normals(self):
        raw, _ = solid()
        start = next(o for c, o, z in cc.walk(raw) if c == 0x134b01)
        start = (start + 135) & ~127
        for packed, expected in (((-32768, 0, 0, 0), (-1., 0., 0.)),
                                 ((23170, -23170, 0, 0), (23170 / 32767., -23170 / 32767., 0.))):
            data = bytearray(raw)
            for i in range(4):
                struct.pack_into('>4h', data, start + i * 32 + 24, *packed)
            normals = cx.source_model(data)['vertices'].copy().view('<f4').reshape(-1, 9)[:4, 3:6]
            np.testing.assert_allclose(normals, np.tile(expected, (4, 1)), atol=1e-7, rtol=0)

    def test_compression_and_mutations(self):
        raw, expected = solid()
        def envelope(part):
            return b'RAWW\x01\x10\0\0' + struct.pack('<II', len(part), len(part)) + part
        split = len(raw) // 2
        blocks = []
        for dst, part in ((split, raw[split:]), (0, raw[:split])):
            packed = envelope(part)
            rel = sum(map(len, blocks))
            blocks.append(struct.pack('<6I', 0x55441122, len(part), 24 + len(packed), dst, rel, 0) + packed)
        data = b''.join(blocks)
        entry = (1, 0, len(data), len(raw), 0x200, 0)
        self.assertEqual(cx.source_model(cc.unpack_entry(data, entry))['vertices'].tobytes(), expected)
        for label, bad in (('destination', changed(data, 12, '<I', split + 1)),
                           ('relative', changed(data, len(blocks[0]) + 16, '<I', 1)),
                           ('raw-size', changed(data, 24 + 8, '<I', split - 1)),
                           ('truncation', data[:-1])):
            with self.subTest(label=label), self.assertRaises(cc.FormatError):
                cc.unpack_entry(bad, entry)
        packed = huff.fixture(raw)
        decoded = cc.unpack_entry(packed, (1, 0, len(packed), len(raw), 0x100, 0))
        self.assertEqual(decoded, raw)


class TextureTests(unittest.TestCase):
    def test_all_top_layouts(self):
        self.assertEqual(set(cx.FORMATS), set(FORMAT_SPECS))
        for fmt in FORMAT_SPECS:
            with self.subTest(fmt=hex(fmt)):
                for width, height in ((128, 128), (256, 64), (16, 8)):
                    hs, blob, expected = texture_blob(fmt, width, height)
                    kind, linear = cx.linear_top(fmt, width, height, blob[:-156])
                    self.assertEqual(linear, expected)
                    entry = cx.texture_entry(hs, blob)
                    cc.validate_texture(*entry, 8, hs)
                    self.assertEqual(entry[0][50], 1)
                    self.assertEqual(entry[1][12:16], b'DXT5' if kind in ('BC5', 'A8R8G8B8') else kind.encode())

    def test_tiling_mutant(self):
        hs, blob, expected = texture_blob(0x1A200152)
        with mock.patch.object(cx, '_tile_indices', lambda w, h, b: np.arange(w * h)):
            _, wrong = cx.linear_top(0x1A200152, 128, 128, blob[:-156])
        self.assertNotEqual(wrong, expected)

    def test_no_padding_zero_fill(self):
        hs, blob, _ = texture_blob(0x1A200152)
        with self.assertRaisesRegex(cc.FormatError, 'truncated'):
            cx.linear_top(0x1A200152, 128, 128, blob[:64])
        hs, blob, _ = texture_blob(0x18280086, 16, 8)
        with self.assertRaisesRegex(cc.FormatError, 'truncated'):
            cx.linear_top(0x18280086, 16, 8, blob[:16 * 8 * 4])

    def test_texture_corruptions(self):
        hs, blob, _ = texture_blob(0x1A200171)
        record, plat = len(blob) - 156, len(blob) - 32
        cases = dict(hash=(record + 12, '<I', 2), size=(record + 28, '<I', 0),
                     top=(record + 36, '<I', 16), width=(record + 40, '<H', 129),
                     log=(record + 44, 'B', 2), code=(record + 46, 'B', 0x26),
                     mips=(record + 50, 'B', 0), name=(record + 88, 'B', 255),
                     format=(plat + 20, '<I', 0x1A200170))
        for label, (offset, fmt, value) in cases.items():
            with self.subTest(label=label), self.assertRaises(cc.FormatError):
                cx.texture_entry(hs, changed(blob, offset, fmt, value))
        with self.assertRaises(cc.FormatError):
            cx.texture_entry(hs, blob[:-8])  # A PC-sized 148-byte tail must fail.

    def test_bc4_modes_channels_and_z(self):
        # Every selector in each endpoint mode, independently calculated values.
        selectors = sum((i % 8) << (3 * i) for i in range(16)).to_bytes(6, 'little')
        r, g = bytes((210, 0)) + selectors, bytes((0, 200)) + selectors
        pixels = cx.decode_image('BC5', 4, 4, r + g).reshape(16, 4)
        self.assertEqual(pixels[:8, 0].tolist(), [210, 0, 180, 150, 120, 90, 60, 30])
        self.assertEqual(pixels[:8, 1].tolist(), [0, 200, 40, 80, 120, 160, 0, 255])
        self.assertTrue((pixels[:, 3] == 255).all())
        self.assertEqual(pixels[4, 2], 255)
        self.assertEqual(pixels[0, 2], 128)
        self.assertFalse(np.array_equal(pixels, cx.decode_image('DXT5', 4, 4, r + g).reshape(16, 4)))
        equal = cx.decode_image('BC5', 4, 4, (bytes((128, 128)) + bytes(6)) * 2)
        self.assertEqual(equal[0, 0].tolist(), [128, 128, 255, 255])

    def test_drop_packed_mips(self):
        hs, blob, _ = texture_blob(0x1A200154)
        pixels, record, platform = blob[:-156], bytearray(blob[-156:-32]), blob[-32:]
        # Deliberately non-image lower mips: output may contain only the top.
        tail = bytes([171]) * 4096
        struct.pack_into('<I', record, 28, len(pixels) + len(tail))
        record[50] = 3
        packed = pixels + tail + record + platform
        entry = cx.texture_entry(hs, packed)
        self.assertEqual(entry[2], cx.texture_entry(hs, blob)[2])
        self.assertEqual(entry[0][50], 1)
        self.assertEqual(len(entry[2]), 128 * 128)

    def test_common_texture_writer(self):
        hs, blob, _ = texture_blob(0x1A200171, 16, 8)
        entry = cx.texture_entry(hs, blob)
        source = indexed_pack([(hs, blob)], texture=True)
        self.assertEqual(cx.source_textures(source)[hs], entry)
        # A synthetic v5 record supplies only the common writer's defaults.
        rec, plat = bytearray(124), bytearray(32)
        rec[12:36] = b'SYNTH_DIFFUSE\0'.ljust(24, b'\0')
        struct.pack_into('<I', rec, 36, hs)
        struct.pack_into('<I', rec, 56, len(entry[2]))
        struct.pack_into('<I', rec, 64, len(entry[2]))
        struct.pack_into('<HH', rec, 68, 16, 8)
        rec[72:75] = bytes((4, 3, 0x26))
        plat[20:24] = b'DXT5'
        reference = indexed_pack([(hs, entry[2] + rec + plat)], texture=True, version=5)
        # Feed the normalized v8 tuple through the existing writer unchanged.
        pc_source = indexed_pack([(hs, entry[2] + entry[0] + entry[1])], texture=True)
        with contextlib.redirect_stdout(io.StringIO()):
            output, count = cc.convert_textures(pc_source, reference)
        self.assertEqual(count, 1)
        self.assertEqual(cc.validate_textures(output, 5), {hs})
        self.assertEqual(cc.texture_blobs(output)[0][1][:-156], entry[2])


if __name__ == '__main__':
    if '--broken-tiling' in sys.argv:
        sys.argv.remove('--broken-tiling')
        cx._tile_indices = lambda w, h, b: np.arange(w * h)
    unittest.main()
