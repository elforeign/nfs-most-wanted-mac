"""Check the converted Carbon fields through the native MW layout writer.

The donor-only control uses the same donor layout bytes as the retail path.
The donor-only control failed source comparison for 44/44 variants (exit 1).
Default On matched 44/44, and Off matched the MW donor for 44/44 (exit 0).
For nitrous, fdf85535 matched 21/44 source pairs (exit 1). The corrected
mapping matches 44/44 Carbon pairs On and 44/44 MW donor pairs Off (exit 0).
For recharge, 654ff2a1 matched 23/44 source layouts (exit 1). The 21 failures
include 17 variants with zero donor refill times and positive source refill times.
The full nitrous layout matches 44/44 Carbon layouts On and 44/44 MW donor
layouts Off (exit 0).
With the old donor projection, upgraded refill matches 27/44 Carbon variants
and the full installed layout matches 25/44. The corrected projection matches
44/44 for both, with 88/88 installed endpoints On and 82/82 unchanged Off.
Guest ownership and the menu consumers are in car_performance_attrib_impl.h.
The native wire layout is declared in car_performance.h.
"""
from contextlib import closing
import ctypes as ct
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_sources, car_performance, car_x360_inputs
from setup_kit.car_convert import FitAttributes, hash32
from setup_kit.input_paths import input_path
from setup_kit.x360 import Source


class Record(ct.Structure):
    _fields_ = [('version', ct.c_uint32), ('words', ct.c_uint32 * 82), ('source', ct.c_uint)]


# MW guest offsets from the engine/transmission constructors, Fetch PerfStats
# 00678CE0, brakes 0069DA90, and steer 0069E820. The checks read the source
# fields independently of the sidecar's serialization order.
FIELDS = (
    ('engine', 0x60, {'TORQUE': (8, 9), 'IDLE': (0x5c, 1),
                      'MAX_RPM': (0x54, 1), 'RED_LINE': (0x58, 1),
                      'ENGINE_BRAKING': (0x44, 3), 'FLYWHEEL_MASS': (0x50, 1)}),
    ('transmission', 0x84, {'GEAR_RATIO': (8, 0), 'FINAL_GEAR': (0x80, 1),
                            'DIFFERENTIAL': (0x34, 3), 'GEAR_EFFICIENCY': (0x48, 9),
                            'TORQUE_CONVERTER': (0x6c, 1), 0x7381EE38: (0x70, 1),
                            0xCF5DD4E9: (0x74, 1), 0x8DC16371: (0x78, 1),
                            'SHIFT_SPEED': (0x7c, 1)}),
    ('induction', 0x1c, {'LOW_BOOST': (0, 1), 'HIGH_BOOST': (0x18, 1), 'VACUUM': (8, 1),
                        'SPOOL_TIME_DOWN': (4, 1), 'SPOOL': (0xc, 1),
                        'SPOOL_TIME_UP': (0x10, 1), 'PSI': (0x14, 1)}),
    ('chassis', 0x74, {'DRAG_COEFFICIENT': (0x70, 1), 'AERO_COEFFICIENT': (0x68, 1)}),
    ('brakes', 0x14, {'BRAKE_LOCK': (0, 2), 'BRAKES': (8, 2), 'EBRAKE': (0x10, 1)}),
    ('tires', 0x50, {'STEERING': (0x48, 1)}),
)

# The shared MW and Carbon nos schema is eight scalar floats.
NITROUS = {
    'NOS_DISENGAGE': 0, 'TORQUE_BOOST': 4, 'FLOW_RATE': 8, 'RECHARGE_MIN': 0x0c,
    'NOS_CAPACITY': 0x10, 'RECHARGE_MAX': 0x14,
    'RECHARGE_MAX_SPEED': 0x18, 'RECHARGE_MIN_SPEED': 0x1c,
}


def ref(attrib, vehicle, name, endian):
    return struct.unpack_from(endian + 'I', attrib.raw('pvehicle', vehicle, name), 4)[0]


def layout(attrib, cls, key, size):
    vault, off = attrib.collections[hash32(cls), key]
    target = vault.pointer(0, off + 28)
    assert target is not None
    dep, at = target
    return bytes(vault.buffers[dep][at:at + size])


def upgraded_nitrous(lib, record, source, own, endian, mw, retail, stock, enabled):
    source_keys = [key for _, key, _ in struct.iter_unpack(
        endian + '3I', source.raw('pvehicle', own, 'nos'))]
    donor_keys = [key for _, key, _ in struct.iter_unpack(
        '<3I', mw.raw('pvehicle', retail, 'nos'))]
    assert len(source_keys) in (1, 3) and len(donor_keys) in (2, 3)
    indices = list(range(len(donor_keys)))
    if enabled:
        projected = (ct.c_uint * 3)()
        count = lib.car_performance_tiers(ct.byref(record), len(FIELDS), len(donor_keys), projected)
        assert projected[:count] == ([0, 0, 1] if len(donor_keys) == 2 else indices)
        indices = projected[:count]
    matches = []
    kit_fields = ('TORQUE_BOOST', 'NOS_CAPACITY')
    for tier, index in enumerate(indices[1:], 1):
        endpoint = layout(mw, 'nos', donor_keys[index], 0x20)
        actual = ct.create_string_buffer(endpoint)
        expected = bytearray(endpoint)
        if enabled:
            lib.car_performance_layout(ct.byref(record), len(FIELDS), True, stock,
                                       ct.create_string_buffer(endpoint), actual)
            # A single source reference supplies stock refill for both MW kits.
            source_key = source_keys[tier] if len(source_keys) > 1 else source_keys[0]
            for name, offset in NITROUS.items():
                if len(source_keys) == 1 and name in kit_fields:
                    continue  # MW retains its kit choices when Carbon has only stock.
                expected[offset:offset + 4] = little(source, 'nos', source_key, name, endian)
        recharge = all(actual.raw[offset:offset + 4] == expected[offset:offset + 4]
                       for name, offset in NITROUS.items() if name not in kit_fields)
        matches.append((recharge, actual.raw[:len(endpoint)] == expected))
    return matches


def native_test(lib, raw, source, endian, mw, row, work):
    car, donor = row['origin']['folder'], row['donors']['model']
    own, retail = hash32(car.lower()), hash32(donor.lower())
    path = work / 'PERFORMANCE.BIN'
    data = car_performance.metadata(raw, car, donor, attrib=source, endian=endian)
    path.write_bytes(data)
    record = Record()
    with path.open('rb') as f:
        assert lib.car_performance_read(f.fileno(), own, retail, ct.byref(record)) == 0
        assert record.version
        assert lib.car_performance_read(f.fileno(), retail, own ^ 1, ct.byref(Record())) == -1
    for broken in (data[:-1], data[:16] + struct.pack('<f', float('nan')) + data[20:]):
        path.write_bytes(broken)
        with path.open('rb') as f:
            assert lib.car_performance_read(f.fileno(), own, retail, ct.byref(Record())) == -1

    old = layout(mw, 'pvehicle', retail, 0x50)
    expected = bytearray(old)
    expected[0x38:0x3c] = little(source, 'pvehicle', own, 'MASS', endian)
    expected[0:16] = little(source, 'pvehicle', own, 'TENSOR_SCALE', endian)
    actual = ct.create_string_buffer(old)
    rating = ct.create_string_buffer(16)
    if lib.car_performance_enabled():
        lib.car_performance_body(ct.byref(record), actual, rating)
    on = actual.raw[:len(old)] == expected
    off = actual.raw[:len(old)] == old
    if lib.car_performance_enabled():
        assert rating.raw[8:] == little(source, 'pvehicle', own, 'HandlingRating', endian)
    for i, (cls, size, fields) in enumerate(FIELDS):
        donor_key = ref(mw, retail, cls, '<')
        src_key = ref(source, own, cls, endian)
        old = layout(mw, cls, donor_key, size)
        stock = ct.create_string_buffer(old)
        actual = ct.create_string_buffer(old)
        if lib.car_performance_enabled():
            lib.car_performance_layout(ct.byref(record), i, False, stock, stock, actual)
        off &= actual.raw[:size] == old
        for name, (offset, count) in fields.items():
            value = little(source, cls, src_key, name, endian)
            assert not count or len(value) == 4 * count
            on &= actual.raw[offset:offset + len(value)] == value
            if name == 'GEAR_RATIO':
                on &= struct.unpack_from('<H', actual.raw, 2)[0] == len(value) // 4
        if cls == 'engine':
            limiter = source.raw(cls, src_key, 'SPEED_LIMITER', allow_empty=True)
            on &= struct.unpack_from('<H', actual.raw, 0x2e)[0] == len(limiter) // 4
    cls = 'nos'
    old = layout(mw, cls, ref(mw, retail, cls, '<'), 0x20)
    stock = ct.create_string_buffer(old)
    actual = ct.create_string_buffer(old)
    enabled = lib.car_performance_enabled() and lib.car_performance_class(hash32(cls))
    if enabled:
        lib.car_performance_layout(ct.byref(record), len(FIELDS), False, stock, stock, actual)
    key = ref(source, own, cls, endian)
    assert {hash32(name): offset for name, offset in NITROUS.items()} == {
        ident: definition[1] for ident, definition in mw.defs[hash32(cls)].items()
        if ident in source.defs[hash32(cls)]
    }
    matches = {name: actual.raw[offset:offset + 4] == little(source, cls, key, name, endian)
               for name, offset in NITROUS.items()}
    recharge_on = matches['NOS_DISENGAGE'] and all(
        same for name, same in matches.items() if name.startswith('RECHARGE_'))
    tiers = upgraded_nitrous(lib, record, source, own, endian, mw, retail, stock, enabled)
    return on, off, all(matches.values()), actual.raw[:len(old)] == old, recharge_on, tiers


def little(source, cls, key, field, endian):
    raw = source.raw(cls, key, field)
    return struct.pack('<' + 'f' * (len(raw) // 4), *struct.unpack(endian + 'f' * (len(raw) // 4), raw))


def main():
    carbon = input_path('NFSC/GLOBAL/attributes.bin')
    x360 = next(input_path('NFSC_X360').glob('*.iso'), None)
    mw_game = Path(os.environ.get('NFS_GAME') or ROOT.parent / 'Need For Speed Most Wanted Black Edition')
    donor = mw_game / 'GLOBAL/attributes.bin'
    if not (carbon.is_file() and x360 and donor.is_file()):
        print('SKIP: Carbon PC, X360 or MW attributes unavailable')
        return 3
    pc = carbon.read_bytes()
    with closing(Source(x360)) as source:
        logical = car_sources._logical(source, car_sources._physical(source))
        be = car_sources._read(source, logical[car_sources.path_hash('GLOBAL/ATTRIBUTES.BIN')])
    attrib = {'pc': (pc, FitAttributes(pc), '<'),
              'x360': (be, car_x360_inputs.XenonAttributes(be), '>')}
    mw = FitAttributes(donor.read_bytes())
    variants = [r for r in json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_bytes())
                if r['origin']['game'] == 'carbon']
    assert len(variants) == 44 and sum(r['origin']['platform'] == 'x360' for r in variants) == 14

    with tempfile.TemporaryDirectory(prefix='car-performance-') as td:
        work = Path(td)
        binary = work / 'performance.dylib'
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-dynamiclib',
                        str(ROOT / 'runtime/car_performance.c'), '-o', str(binary)], check=True)
        lib = ct.CDLL(str(binary))
        lib.car_performance_read.argtypes = [ct.c_int, ct.c_uint32, ct.c_uint32, ct.POINTER(Record)]
        lib.car_performance_body.argtypes = [ct.POINTER(Record), ct.c_void_p, ct.c_void_p]
        lib.car_performance_class.argtypes = [ct.c_uint32]
        lib.car_performance_tiers.argtypes = [ct.POINTER(Record), ct.c_uint, ct.c_uint,
                                              ct.POINTER(ct.c_uint)]
        lib.car_performance_layout.argtypes = [ct.POINTER(Record), ct.c_uint, ct.c_int,
                                               ct.c_void_p, ct.c_void_p, ct.c_void_p]
        os.environ['NFS_ADDCAR_PERFORMANCE'] = 'off'
        control = [native_test(lib, *attrib[r['origin']['platform']], mw, r, work) for r in variants]
        assert all(not own and donor and nos_donor and all(full for _, full in tiers)
                   for own, donor, _, nos_donor, _, tiers in control), control
        print(f'donor-only control: 0/{len(control)} match Carbon, {len(control)}/{len(control)} match MW')
        if '--original' in sys.argv:
            return int(not all(own for own, *_ in control))
        del os.environ['NFS_ADDCAR_PERFORMANCE']
        assert lib.car_performance_enabled()
        result = [native_test(lib, *attrib[r['origin']['platform']], mw, r, work) for r in variants]
        assert all(own and not donor for own, donor, *_ in result), result
        print(f'default On: {len(result)}/{len(result)} match Carbon')
        matches = sum(nos for _, _, nos, _, _, _ in result)
        recharge = sum(row[-2] for row in result)
        print(f'nitrous recharge On: {recharge}/{len(result)} match Carbon', flush=True)
        print(f'nitrous On: {matches}/{len(result)} match Carbon. '
              f'Off: {len(control)}/{len(control)} match MW', flush=True)
        assert recharge == matches == len(result), 'Carbon nitrous stock layout mismatch'
        tiers = [row[-1] for row in result]
        recharge_variants = sum(all(same for same, _ in levels) for levels in tiers)
        matched_variants = sum(all(full for _, full in levels) for levels in tiers)
        matched_levels = sum(full for levels in tiers for _, full in levels)
        total_levels = sum(map(len, tiers))
        off_levels = sum(len(row[-1]) for row in control)
        print(f'upgraded refill On: {recharge_variants}/{len(tiers)} variants match Carbon. '
              f'Full layouts: {matched_variants}/{len(tiers)} variants, '
              f'{matched_levels}/{total_levels} endpoints. '
              f'Off: {off_levels}/{off_levels} endpoints match MW', flush=True)
        assert recharge_variants == matched_variants == len(result), 'Carbon nitrous upgrade layout mismatch'
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
