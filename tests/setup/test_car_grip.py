"""GRP1 proof, pinned original: 13984ab747d35795ffd6ac721ddbb155794792b3.

Sparse-inline regression: predecessor 1/2 format counters pass; fixed 2/2.

Original donor references fail 25/25 complete shared-field tier goldens and
24/25 dynamic-grip goldens. GRP1 passes 25/25, all 425 fields round-trip.
The retained-donor control repeats those failures; shifted-key and promoted
unresolved-semantics controls must fail. Synthetic checks run without assets;
exit 3 then marks only the private 16-car comparison unavailable.
"""
import copy
import ctypes as ct
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_convert as cc
from setup_kit import car_grip as grip


class Field(ct.Structure):
    _fields_ = [('key', ct.c_uint32), ('semantics', ct.c_uint32),
                ('count', ct.c_uint32), ('values', ct.c_float * 40)]


class Tier(ct.Structure):
    _fields_ = [('key', ct.c_uint32), ('fields', Field * 17)]


class Grip(ct.Structure):
    _fields_ = [('present', ct.c_uint32), ('source', ct.c_uint32),
                ('donor', ct.c_uint32), ('tier_count', ct.c_uint32),
                ('upgrade_max', ct.c_uint32), ('tiers', Tier * 8)]


def compile_reader(tmp, text=None, name='reader'):
    module = ROOT / 'runtime/car_grip.c'
    if text is not None:
        module = tmp / (name + '.c')
        module.write_text(text)
    lib = tmp / (name + '.dylib')
    cmd = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1',
           '-shared', '-fPIC', '-I' + str(ROOT / 'runtime'), str(module),
           str(ROOT / 'runtime/car_physics.c'), '-o', str(lib)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    dll = ct.CDLL(str(lib))
    dll.car_grip_load.argtypes = [ct.c_char_p, ct.c_char_p, ct.c_char_p, ct.c_int, ct.POINTER(Grip)]
    dll.car_grip_load.restype = ct.c_int
    dll.car_grip_read.argtypes = [ct.c_int, ct.c_char_p, ct.c_char_p, ct.c_int, ct.POINTER(Grip)]
    dll.car_grip_read.restype = ct.c_int
    dll.car_grip_field.argtypes = [ct.POINTER(Grip), ct.c_uint, ct.c_uint32]
    dll.car_grip_field.restype = ct.POINTER(Field)
    return dll


def synthetic(modern=True):
    """Two inherited collections, layout arrays and sparse numeric fields."""
    h = cc.hash32
    raw, binary, pointers = bytearray(2048), bytearray(2048), {}
    pv, pc, parent, child = 64, 128, 256, 640
    for off, cls, key, ancestor in ((pv, 'pvehicle', 10, 0), (pc, 'pvehicle', h('source'), 10),
                                    (parent, 'tires', 20, 0), (child, 'tires', 21, 20)):
        struct.pack_into('<3I', raw, off, key, h(cls), ancestor)
    # pvehicle child inherits both the full reference array and upgrade count.
    struct.pack_into('<I', raw, pv + 20, 2)
    struct.pack_into('<IIHBB', raw, pv + 32, h('tires'), 0, 0, 2, 0)
    struct.pack_into('<IIHBB', raw, pv + 44, h('tires_upgrades'), 3, 0, 0x40 if modern else 0x20, 0)
    pointers[0, pv + 36] = (1, 1024)
    struct.pack_into('<4H3I', binary, 1024, 1, 1, 12, 0, h('tires'), 21, 0)
    definitions, expected, sparse = {}, {}, []
    at, extra = 0, 1200
    for i, (key, count, maximum, semantics) in enumerate(grip.FIELDS):
        n = count or (7 if key != 0xD15B2BDE else 10)
        values = tuple((i + 1) * .125 + j * .0625 for j in range(n))
        encoded = struct.pack('<' + str(n) + 'f', *values)
        expected[key] = encoded
        array = count == 0 or count == 4
        flags = ((7 if count == 4 or key == 0xD15B2BDE else 5) if array else 6)
        typ, size = (0x4CB36381, 8) if count == 2 else (0x3C16EC5E, 4)
        definitions[key] = (typ, at if flags & 2 else 0, size, maximum, flags, 2)
        payload = (struct.pack('<4H', n, n, 4, 0) if array else b'') + encoded
        if flags & 2:
            binary[at:at + len(payload)] = payload
            at += len(payload)
        else:
            entry = parent + 32 + len(sparse) * 12
            struct.pack_into('<IIHBB', raw, entry, key, 0, 0, 2, 0)
            pointers[0, entry + 4] = (1, extra)
            binary[extra:extra + len(payload)] = payload
            sparse.append(key)
            extra += len(payload)
    struct.pack_into('<I', raw, parent + 20, len(sparse))
    pointers[0, parent + 28] = (1, 0)
    vault = SimpleNamespace(buffers=(raw, binary), modern=modern,
                            pointer=lambda dep, pos: pointers.get((dep, pos)))
    attrib = cc.FitAttributes.__new__(cc.FitAttributes)
    attrib.defs = {h('tires'): definitions, h('pvehicle'): {
        h('tires'): (0, 0, 12, 8, 1, 2), h('tires_upgrades'): (0, 0, 4, 1, 0, 2)}}
    attrib.collections = {(h(cls), key): (vault, off) for cls, key, off in (
        ('pvehicle', 10, pv), ('pvehicle', h('source'), pc), ('tires', 20, parent), ('tires', 21, child))}
    return attrib, expected


def inline_regression():
    """Production serializer: modern 0x40 and legacy 0x20 both retain counter 3.

    Predecessor: modern rejected, legacy passed (1/2); fixed: 2/2 pass.
    """
    passed = 0
    for modern in (True, False):
        a, _ = synthetic(modern)
        blob = grip.metadata(b'', 'source', 'donor', attrib=a)
        assert cc.u32(blob, 28) == 3, ('inline counter', modern)
        passed += 1
    print(f'Sparse inline counters: {passed}/2 PASS (modern 0x40, legacy 0x20)')


def fields(blob):
    pos = 40
    for t in range(cc.u32(blob, 24)):
        pos += 8
        for _ in range(17):
            key, semantics, count, reserved = struct.unpack_from('<4I', blob, pos)
            yield t, key, semantics, count, pos, blob[pos+16:pos+16+count*4]
            pos += 16 + count * 4
    assert pos == len(blob)


def load(dll, path, source='source', donor='donor', mode=1):
    out = Grip()
    ct.memset(ct.byref(out), 0xA5, ct.sizeof(out))
    rc = dll.car_grip_load(os.fsencode(path) if path is not None else None,
                           source.encode(), donor.encode(), mode, ct.byref(out))
    if rc or not out.present:
        assert bytes(out) == bytes(ct.sizeof(out)), 'partial output escaped admission'
    return rc, out


def round_trip(dll, path, blob, source, donor):
    path.write_bytes(blob)
    rc, out = load(dll, path, source, donor)
    assert rc == 0 and out.present
    assert (out.source, out.donor, out.tier_count, out.upgrade_max) == struct.unpack_from('<2I', blob, 12) + (
        cc.u32(blob, 24), cc.u32(blob, 28))
    checked = 0
    for t, key, semantics, count, _, raw in fields(blob):
        f = next(f for f in out.tiers[t].fields if f.key == key)
        assert (f.semantics, f.count) == (semantics, count)
        assert ct.string_at(ct.addressof(f.values), count * 4) == raw
        available = dll.car_grip_field(ct.byref(out), t, key)
        assert bool(available) == bool(semantics)
        checked += 1
    for t in range(out.tier_count):
        assert out.tiers[t].key == cc.u32(blob, 44 + sum(
            8 + sum(16 + n * 4 for tt, _, _, n, _, _ in fields(blob) if tt == i) for i in range(t)))
    assert not dll.car_grip_field(ct.byref(out), out.tier_count, cc.hash32('DYNAMIC_GRIP'))
    assert not dll.car_grip_field(ct.byref(out), 0, 0)
    return checked


def validation(tmp, dll):
    a, expected = synthetic()
    blob = grip.metadata(b'', 'source', 'donor', attrib=a)
    path = tmp / 'GRIP.BIN'
    assert round_trip(dll, path, blob, 'source', 'donor') == 17
    for _, key, _, _, _, raw in fields(blob):
        assert raw == expected[key]
    # Record order is not the mapping; exercise a complete field permutation.
    records = [blob[pos:pos+16+count*4] for _, _, _, count, pos, _ in fields(blob)]
    reordered = blob[:48] + b''.join(reversed(records))
    assert round_trip(dll, path, reordered, 'source', 'donor') == 17
    assert load(dll, tmp / 'absent')[0] == 0
    assert load(dll, None)[0] == 0
    assert grip.metadata(b'not a vault', 'source', 'donor', mode='donor') is None
    assert grip.metadata(b'', 'source', 'donor', attrib=SimpleNamespace(defs={})) is None
    bad = copy.deepcopy(a); bad.defs[cc.hash32('tires')].pop(next(iter(expected)))
    try:
        grip.metadata(b'', 'source', 'donor', attrib=bad)
    except cc.FormatError:
        pass
    else:
        raise AssertionError('partial schema accepted')
    # Source-side malformed arrays, inherited cycles, invalid references, and
    # nonfinite values must fail conversion as well as runtime admission.
    source_bad = 0
    for dep, pos, fmt, value in ((0, 648, '<I', 21), (0, 112, '<I', 9),
                                 (1, 1026, '<H', 0), (1, 1032, '<I', 0),
                                 (1, 0, '<f', float('nan')), (1, 1202, '<H', 41)):
        bad = copy.deepcopy(a)
        vault = bad.collections[cc.hash32('tires'), 20][0]
        struct.pack_into(fmt, vault.buffers[dep], pos, value)
        try:
            grip.metadata(b'', 'source', 'donor', attrib=bad)
        except cc.FormatError:
            source_bad += 1
        else:
            raise AssertionError(('bad source accepted', dep, pos))
    mutations = [blob[:i] for i in range(len(blob))] + [blob + b'\0', blob + bytes(grip.MAX_BYTES)]
    for off in range(0, 40, 4):
        b = bytearray(blob); struct.pack_into('<I', b, off, 0xFFFFFFFF); mutations.append(b)
    for _, key, semantics, count, pos, _ in fields(blob):
        for off, value in ((pos, 0), (pos+4, 2), (pos+8, 0), (pos+8, 41), (pos+12, 1)):
            b = bytearray(blob); struct.pack_into('<I', b, off, value); mutations.append(b)
        for j in range(count):
            for value in (float('nan'), float('inf'), -float('inf'), 1000001):
                b = bytearray(blob); struct.pack_into('<f', b, pos+16+j*4, value); mutations.append(b)
    b = bytearray(blob); struct.pack_into('<I', b, 40, 1); mutations.append(b)
    b = bytearray(blob); struct.pack_into('<I', b, 44, 0); mutations.append(b)
    # Duplicate field IDs, including a duplicated field with the same count.
    b = bytearray(blob); struct.pack_into('<I', b, list(fields(blob))[4][4], cc.u32(blob, 48)); mutations.append(b)
    for b in mutations:
        path.write_bytes(b)
        assert load(dll, path)[0] == -1, ('accepted malformed', len(b))
    # Foreign identities, future semantics, invalid modes and filesystem types.
    path.write_bytes(blob)
    for source, donor, mode in [('foreign', 'donor', 1), ('source', 'foreign', 1),
                                ('', 'donor', 1), ('source', 'donor', 2)]:
        assert load(dll, path, source, donor, mode)[0] == -1
    link = tmp / 'link'; link.symlink_to(path)
    fifo = tmp / 'fifo'; os.mkfifo(fifo)
    for p in (link, fifo, tmp):
        assert load(dll, p)[0] == -1
    path.write_bytes(b'bad')
    for p in (path, link, fifo, tmp, tmp / 'absent', None):
        assert load(dll, p, mode=0)[0] == 0
    out = Grip()
    assert dll.car_grip_read(-1, b'source', b'donor', 1, ct.byref(out)) == -1
    assert bytes(out) == bytes(ct.sizeof(out))
    assert dll.car_grip_read(-1, b'source', b'donor', 0, ct.byref(out)) == 0
    # A compiled validation mutant demonstrates the unsupported-semantics guard.
    original = (ROOT / 'runtime/car_grip.c').read_text()
    mutant = original.replace('semantics == expected', '(semantics == expected || semantics == 1)')
    assert mutant != original
    broken = compile_reader(tmp, mutant, 'promoted')
    b = bytearray(blob); struct.pack_into('<I', b, 52, 1); path.write_bytes(b)
    assert load(dll, path)[0] == -1
    assert load(broken, path)[0] == 0, 'compiled mutant was not exercised'
    mutant = original.replace('field->key = key;', 'field->key = key ^ 1u;')
    assert mutant != original
    shifted = compile_reader(tmp, mutant, 'shifted')
    path.write_bytes(blob)
    rc, out = load(shifted, path)
    assert rc == 0
    shifted_failures = sum(not shifted.car_grip_field(ct.byref(out), 0, cc.hash32(name))
                           for name, _ in grip.SHARED_FIELDS)
    assert shifted_failures == 9
    print(f'synthetic: 34 keyed fields pass; {len(mutations)} malformed + 7 identity/type cases rejected; '
          f'{source_bad + 1} bad sources rejected; 6 donor bypasses; '
          'promoted-semantics mutant detected 1/1; shifted-key mutant detected 9/9')


def private_comparisons(tmp, dll):
    source = Path(os.environ.get('NFS_CARBON', Path.home() / 'Downloads/NFSC')) / 'GLOBAL/attributes.bin'
    donor = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'GLOBAL/ATTRIBUTES.BIN'
    if (not source.is_file() or not donor.is_file() or
            not (ROOT / 'tools_snapshot/test_driver_seats.py').is_file()):
        print('SKIP: private tyre tier comparisons need source/donor vaults and fixture manifest')
        return 3
    # Pin inputs and original selection behavior; never consult a moving branch.
    sb, db = source.read_bytes(), donor.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
    assert hashlib.sha256(db).hexdigest() == '3d0362f2c052b2f93576852f504cb7806b14eefbf83b8531d45dc1d430ec6337'
    a, d = cc.FitAttributes(sb), cc.FitAttributes(db)
    assert (len(a.defs[cc.hash32('tires')]), len(d.defs[cc.hash32('tires')])) == (17, 9)
    assert set(d.defs[cc.hash32('tires')]) == {cc.hash32(n) for n, _ in grip.SHARED_FIELDS}
    # Reuse the established manifest rather than introducing another car table.
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    from test_driver_seats import CASES
    def refs(attrib, name):
        return list(struct.iter_unpack('<3I', attrib.raw('pvehicle', cc.hash32(name.lower()), 'tires')))
    failures = dynamic_failures = tiers = checked = inherited = 0
    for car, slot in CASES:
        blob = grip.metadata(sb, car, slot, attrib=a)
        path = tmp / 'GRIP.BIN'
        checked += round_trip(dll, path, blob, car, slot)
        path.write_bytes(blob)
        _, decoded = load(dll, path, car, slot)
        source_refs, donor_refs = refs(a, car), refs(d, slot)
        assert decoded.upgrade_max == cc.u32(a.raw('pvehicle', cc.hash32(car.lower()), 'tires_upgrades'))
        for t, (cls, key, flags) in enumerate(source_refs):
            assert decoded.tiers[t].key == key and cls == cc.hash32('tires') and flags == 0
            # ORIGINAL behavior: cloned donor pvehicle keeps its donor tyre refs.
            dk = donor_refs[t][1]
            different = False
            for name, count in grip.SHARED_FIELDS:
                expected = a.raw('tires', key, name)
                actual = dll.car_grip_field(ct.byref(decoded), t, cc.hash32(name)).contents
                assert actual.count == count
                assert ct.string_at(ct.addressof(actual.values), count * 4) == expected
                different |= expected != d.raw('tires', dk, name)
            failures += different
            dynamic_failures += a.raw('tires', key, 'DYNAMIC_GRIP') != d.raw('tires', dk, 'DYNAMIC_GRIP')
            inherited += cc.u32(a.collections[cls, key][0].buffers[0], a.collections[cls, key][1]+8) != 0
            tiers += 1
        if car == '300C':
            f = dll.car_grip_field(ct.byref(decoded), 0, cc.hash32('DYNAMIC_GRIP')).contents
            assert bytes(f.values)[:8] == struct.pack('<2f', 1.3, 1.3)
            assert d.raw('tires', donor_refs[0][1], 'DYNAMIC_GRIP') == struct.pack('<2f', 1.6, 1.6)
    assert (tiers, failures, dynamic_failures, checked) == (25, 25, 24, 425)
    print(f'original donor baseline FAIL: {failures}/{tiers} shared tiers, {dynamic_failures}/{tiers} dynamic; '
          f'GRP1 PASS: {checked} fields, {tiers} tiers, {inherited} inherited; retained-donor control FAIL: {failures}/{tiers}')
    return 0


def main():
    scratch = ROOT / '.metal-cache/grip-g1'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        tmp = Path(directory)
        dll = compile_reader(tmp)
        inline_regression()
        validation(tmp, dll)
        return private_comparisons(tmp, dll)


if __name__ == '__main__':
    raise SystemExit(main())
