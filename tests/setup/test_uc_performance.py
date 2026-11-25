"""Undercover source projection to native layout comparison, with the donor-only control.

Donor-only: 0/14 source matches, 14/14 donor matches (exit 1).
Plain source projection: 14/14 source matches. Off: 14/14 donor matches.
Production: 7/14 source matches; seven retail sibling overrides pinned separately.
CLS55 at 2077.431907 RPM: source 409.541029 lb-ft; broken nine-sample
linear control 394.653288 lb-ft (error 14.887741, tolerance 0.0001).
Use --linear-control to prove the off-grid torque check rejects the old path.
"""

import ctypes as ct
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_performance, uc_attrib
from setup_kit.car_convert import FitAttributes, hash32, read_vpak
from setup_kit.input_paths import input_path
from test_car_performance import FIELDS, Record, layout, ref
from test_ps_performance import torque_hook_library


def original_vault(data):
    """Read the reviewed source VPAK with the terminal padding dword removed."""
    for name, binary, vault in read_vpak(data):
        pos = 0
        for _ in range(16):
            tag, size = struct.unpack_from('<4sI', vault, pos)
            if tag == b'NrtP':
                assert pos + size == len(vault) and vault[-4:] == bytes(4)
                yield name, binary, vault[:pos + 4] + struct.pack('<I', size - 4) + vault[pos + 8:-4]
                break
            assert size >= 8
            pos += size
        else:
            raise AssertionError('relocation chunk absent')


def spline(vault, dep, address, value, engine=False):
    data = vault.buffers[dep]
    begin, end, low, high = struct.unpack_from('<4f', data, address)
    n = struct.unpack_from('<I', data, address + 24)[0]
    vectors = []
    for site in range(address + 20, address + 37, 8):
        bank, offset = vault.fixups[dep, site]
        vectors.append(struct.unpack_from(f'<{n}f', vault.buffers[bank], offset))
    x, y, curvature = vectors
    t = min(1, max(0, (value - begin) / (end - begin)))
    j = next((j for j in range(1, n) if t < x[j]), n - 1)
    width = x[j] - x[j - 1]
    w = (t - x[j - 1]) / width
    height = (y[j - 1] + w * (y[j] - y[j - 1]) -
              width * width / 6 * w * (1 - w) *
              ((2 - w) * curvature[j - 1] + (1 + w) * curvature[j]))
    result = low + (high - low) * height
    if engine:
        if value < begin:
            return (low + (high - low) * y[0]) * value / begin
        if value > end:
            return low + (high - low) * y[-1]
        return result
    return min(high, max(low, result))


DONOR = {'TORQUE_CONVERTER', 'VACUUM', 'SPOOL', 'PSI', 'AERO_COEFFICIENT', 'BRAKE_LOCK'}
FIELD_KEYS = {'GEAR_EFFICIENCY': 0x5250EB92, 'SPOOL_TIME_UP': 0xAC1C0D2F, 'SPOOL_TIME_DOWN': 0x1F158097}


def expected_layout(source, stock_key, cls, fields, old):
    # Source class is the pvehicle's first car_tuning reference.
    kind = 0xF1E1032A

    def raw(name):
        return source.raw(kind, stock_key, FIELD_KEYS.get(name, name))

    def sample(name, position, value):
        vault, dep, address, _, _, _ = source.field_site(kind, stock_key, name)
        return spline(vault, dep, address + position * 48, value, name == 'TORQUE')

    if stock_key == source.linked_key('pvehicle', hash32('aud_s5_stk_08'), 'car_tuning'):
        if cls == 'brakes':
            assert sample('BRAKES', 0, 0) == sample('BRAKES', 1, 0) == 7000
            assert abs(sample('BRAKES', 0, 100)-7286.419678) < 0.0001
            assert sample('EBRAKE', 0, 0) == 30000
        elif cls == 'tires':
            assert sample('STEERING', 0, 0) == 1
    expected = bytearray(old)
    for name, (offset, count) in fields.items():
        if name in DONOR:
            continue
        if name == 'TORQUE':
            idle, = struct.unpack('<f', raw('IDLE'))
            limit, = struct.unpack('<f', raw('MAX_RPM'))
            value = struct.pack('<9f', *(sample(name, 0, idle + i * (limit - idle) / 8)
                                        for i in range(9)))
            struct.pack_into('<4H', expected, 0, 9, 9, 4, 0)
        elif name == 'GEAR_RATIO':
            gears = raw(name)[:36]
            struct.pack_into('<4H', expected, 0, 9, len(gears) // 4, 4, 0)
            value = gears.ljust(36, b'\0')
        elif name == 'GEAR_EFFICIENCY':
            value = raw(name)[:36]
            count = len(value) // 4
            value += old[offset + 4 * count:offset + 36]
            struct.pack_into('<4H', expected, 0x40, 9, 9, 4, 0)
        elif name in ('ENGINE_BRAKING', 'DIFFERENTIAL'):
            value = raw(name)
            struct.pack_into('<4H', expected, offset - 8, count, count, 4, 0)
        elif name == 'BRAKES':
            value = struct.pack('<2f', sample(name, 0, 0) / 4, sample(name, 1, 0) / 4)
        elif name in ('EBRAKE', 'STEERING'):
            value = struct.pack('<f', sample(name, 0, 0) / (10 if name == 'EBRAKE' else 1))
        else:
            value = raw(name)
        expected[offset:offset + len(value)] = value
    if cls == 'engine':
        onset, cutoff = struct.unpack('<2f', raw('SPEED_LIMITER'))
        struct.pack_into('<4H2f', expected, 0x2c, 2, 2, 4, 0, onset, cutoff - onset)
    return expected


def compare(lib, source, native, row, record, enabled, verify_source=True):
    car, donor = row['origin']['folder'].lower(), row['donors']['model'].lower()
    retail = hash32(donor)
    own = hash32(car)
    if car == 'lam_gal_sup_08':
        assert (hash32('pvehicle'), own) not in source.collections
        assert (hash32('ecar'), own) in source.collections
        own = hash32('lam_gal_560_09')
    kind, stock_key, tail = struct.unpack_from('<3I', source.raw('pvehicle', own, 0xF1E1032A))
    assert (kind, tail) == (0xF1E1032A, 0)

    old = layout(native, 'pvehicle', retail, 0x50)
    donor_rating = struct.pack('<4H', 2, 2, 4, 0) + native.raw('pvehicle', retail, 'HandlingRating')
    actual, rating = ct.create_string_buffer(old), ct.create_string_buffer(donor_rating)
    if enabled:
        lib.car_performance_body(ct.byref(record), actual, rating)
    live = [actual.raw[:len(old)], rating.raw[:len(donor_rating)]]
    donor_inputs = [old, donor_rating]
    source_inputs = [source.raw(kind, stock_key, 'TENSOR_SCALE') + old[16:0x38] +
                     source.raw(kind, stock_key, 'MASS') + old[0x3c:], donor_rating]

    for link, (cls, size, fields) in enumerate(FIELDS):
        old = layout(native, cls, ref(native, retail, cls, '<'), size)
        actual = ct.create_string_buffer(old)
        expected = expected_layout(source, stock_key, cls, fields, old)
        if enabled:
            lib.car_performance_layout(ct.byref(record), link, False, old, old, actual)
            endpoints = list(struct.iter_unpack('<3I', native.raw('pvehicle', retail, cls)))
            indices = (ct.c_uint * 3)()
            assert lib.car_performance_tiers(ct.byref(record), link, len(endpoints), indices) == len(endpoints)
            assert list(indices[:len(endpoints)]) == list(range(len(endpoints)))
            for _, endpoint_key, _ in endpoints[1:]:
                endpoint = layout(native, cls, endpoint_key, size)
                installed = ct.create_string_buffer(endpoint)
                lib.car_performance_layout(ct.byref(record), link, True, old, endpoint, installed)
                for field in fields.keys() & DONOR:
                    offset, count = fields[field]
                    end = offset + 4 * count
                    assert installed.raw[offset:end] == endpoint[offset:end], (car, cls, field)
            if cls == 'engine' and verify_source:
                _, cutoff = struct.unpack('<2f', source.raw(kind, stock_key, 'SPEED_LIMITER'))
                assert abs(lib.car_performance_clamp_top_speed(actual, 200) - cutoff * 0.44703) < 1e-5
        live.append(actual.raw[:size])
        donor_inputs.append(old)
        source_inputs.append(expected)

    # No source stock boost or capacity. CPF4 preserves the stock and both
    # equipped nitrous references for this entire roster.
    nos_refs = native.raw('pvehicle', retail, 'nos')
    assert len(nos_refs) == 36
    old = layout(native, 'nos', ref(native, retail, 'nos', '<'), 0x20)
    for field, offset in (('TORQUE_BOOST', 4), ('NOS_CAPACITY', 0x10)):
        assert source.raw(kind, stock_key, field) == old[offset:offset + 4] == bytes(4)
    return live == source_inputs, live == donor_inputs


def discarded_curves(source, stock_key):
    """Count nonconstant source curves whose speed dependence is not retained.

    The native layout comparison pins front/rear /4, handbrake /10, and
    steering at zero mph. Rate and range retain the donor behavior entirely.
    Counts describe serialized data, not source-game dynamic consumers.
    """
    counts = []
    for name, count in (('BRAKES', 2), ('EBRAKE', 1), ('STEERING', 1),
                        ('STEERING_SPEED', 1), ('STEERING_RANGE', 1)):
        vault, dep, address, size, _, _ = source.field_site(0xF1E1032A, stock_key, name)
        assert size == 48 * count
        nonconstant = False
        for i in range(count):
            at = address + 48 * i
            low, high = struct.unpack_from('<2f', vault.buffers[dep], at + 8)
            n = struct.unpack_from('<I', vault.buffers[dep], at + 24)[0]
            bank, off = vault.fixups[dep, at + 28]
            ys = struct.unpack_from(f'<{n}f', vault.buffers[bank], off)
            bank, off = vault.fixups[dep, at + 36]
            second = struct.unpack_from(f'<{n}f', vault.buffers[bank], off)
            nonconstant |= low != high and (len(set(ys)) > 1 or any(second))
        counts.append(int(nonconstant))
    return counts


def torque_checks(lib, hook, source, native, row, record, linear_control):
    car, donor = row['origin']['folder'], row['donors']['model']
    own = hash32('lam_gal_560_09' if car == 'LAM_GAL_SUP_08' else car.lower())
    kind, key, _ = struct.unpack_from('<3I', source.raw('pvehicle', own, 0xF1E1032A))
    vault, dep, at, _, _, _ = source.field_site(kind, key, 'TORQUE')
    idle, maximum = (struct.unpack('<f', source.raw(kind, key, name))[0]
                     for name in ('IDLE', 'MAX_RPM'))
    old = layout(native, 'engine', ref(native, hash32(donor.lower()), 'engine', '<'), 0x60)
    engine = ct.create_string_buffer(old)
    lib.car_performance_layout(ct.byref(record), 0, False, old, old, engine)
    nine = struct.unpack_from('<9f', engine.raw, 8)
    sample_error = max(abs(actual - spline(vault, dep, at, idle + (maximum-idle)*i/8, True))
                       for i, actual in enumerate(nine))
    hook.probe_bind(ct.byref(record))
    rpms = [ct.c_float(idle + (maximum-idle)*i/257).value for i in range(1, 257)]
    if car == 'MER_CLS_55_08':
        rpms.append(2077.431907)
    worst = 0
    for rpm in rpms:
        expected = spline(vault, dep, at, rpm, True)
        actual = ct.c_float()
        assert lib.car_performance_torque(ct.byref(record), engine, rpm, ct.byref(actual)) == 1
        hooked = hook.probe_torque(engine, rpm, 0)
        assert hook.probe_torque(engine, rpm, 1) == hooked == actual.value
        position = (rpm-idle)/(maximum-idle)*8
        left = min(int(position), 7)
        linear = nine[left] + (nine[left+1]-nine[left])*(position-left)
        worst = max(worst, abs((linear if linear_control else hooked)-expected))
        if car == 'MER_CLS_55_08' and rpm == rpms[-1]:
            print(f'CLS55 @ {rpm:.6f} RPM: source {expected:.6f}, '
                  f'hook {hooked:.6f}, linear control {linear:.6f} lb-ft')
            assert abs(expected-409.541029) <= 0.0001
            assert abs(linear-394.653288) <= 0.0001
            assert abs((linear if linear_control else hooked)-409.541029) <= 0.0001
    # Uniform MW kit gain and changed RPM envelope still reach the spline hook.
    kit = bytearray(engine.raw[:0x60])
    struct.pack_into('<9f', kit, 8, *(v+16 for v in nine))
    struct.pack_into('<f', kit, 0x54, maximum*2)
    struct.pack_into('<f', kit, 0x5c, idle*2)
    for rpm in rpms:
        assert abs(hook.probe_torque(bytes(kit), rpm*2, 1) -
                   (spline(vault, dep, at, rpm, True)+16)) <= 0.0001
    assert hook.probe_torque(engine, idle, 3) == -123.25
    hook.probe_release(1)
    assert hook.probe_torque(engine, idle, 0) == -123.25
    assert abs(hook.probe_torque(engine, idle, 2)-nine[0]) <= 0.0001
    hook.probe_release(2)
    assert hook.probe_torque(engine, idle, 2) == -123.25
    return sample_error, worst, len(rpms), discarded_curves(source, key)


def main(donor_only=False, linear_control=False):
    source_path = input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    mw_root = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    donor_path = mw_root / 'GLOBAL/attributes.bin'
    if not (source_path.is_file() and donor_path.is_file()):
        print('SKIP: Undercover PC or MW attributes unavailable')
        return 3
    source_bytes, donor_bytes = source_path.read_bytes(), donor_path.read_bytes()
    source = FitAttributes(source_bytes, vaults=original_vault(source_bytes))
    native = FitAttributes(donor_bytes)
    rows = [r for r in json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_bytes())
            if r['origin']['game'] == 'undercover']
    approved = {row['id'] for row in rows}
    assert approved == set(range(145, 160)) - {147}

    with tempfile.TemporaryDirectory(prefix='uc-performance-') as td:
        work = Path(td)
        binary = work / 'performance.dylib'
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-dynamiclib',
                        str(ROOT / 'runtime/car_performance.c'), '-o', str(binary)], check=True)
        lib = ct.CDLL(str(binary))
        lib.car_performance_read.argtypes = [ct.c_int, ct.c_uint32, ct.c_uint32, ct.POINTER(Record)]
        lib.car_performance_body.argtypes = [ct.POINTER(Record), ct.c_void_p, ct.c_void_p]
        lib.car_performance_tiers.argtypes = [ct.POINTER(Record), ct.c_uint, ct.c_uint, ct.POINTER(ct.c_uint)]
        lib.car_performance_layout.argtypes = [ct.POINTER(Record), ct.c_uint, ct.c_int,
                                              ct.c_void_p, ct.c_void_p, ct.c_void_p]
        lib.car_performance_clamp_top_speed.argtypes = [ct.c_void_p, ct.c_float]
        lib.car_performance_clamp_top_speed.restype = ct.c_float
        lib.car_performance_torque.argtypes = [ct.POINTER(Record), ct.c_void_p, ct.c_float,
                                               ct.POINTER(ct.c_float)]
        hook = torque_hook_library(work)
        siblings = {'DOD_VIP_SRT_08', 'FOR_SHL_GT_08', 'FOR_SHL_TER_08',
                    'LAM_GAL_560_09', 'LEX_IS_F_08', 'MER_SLR_722_07', 'LAM_GAL_SUP_08'}
        assert {r['origin']['folder'] for r in rows} & car_performance.SIBLINGS.keys() == siblings
        on, off, wrong, production, overridden = [], [], [], [], []
        sample_worst = worst = points = 0
        discarded, alias_discarded = [0]*5, [0]*5
        for row in rows:
            car, donor = row['origin']['folder'], row['donors']['model']
            record = Record()
            if not donor_only:
                output = uc_attrib.source_performance(source_bytes, donor_bytes, car, donor)
                with (work / 'PERFORMANCE.BIN').open('w+b') as handle:
                    handle.write(output)
                    handle.flush()
                    assert lib.car_performance_read(handle.fileno(), hash32(car.lower()),
                                                    hash32(donor.lower()), ct.byref(record)) == 0, car
                assert record.version == 4 and 392 <= len(output) <= 452
                record.source = 2
                sample_error, error, count, dropped = torque_checks(
                    lib, hook, source, native, row, record, linear_control)
                sample_worst, worst = max(sample_worst, sample_error), max(worst, error)
                points += count
                if car == 'LAM_GAL_SUP_08':
                    alias_discarded = dropped
                else:
                    discarded = [a+b for a, b in zip(discarded, dropped)]
                actual = uc_attrib.performance(source_bytes, donor_bytes, car, donor)
                production_record = Record()
                with (work / 'PRODUCTION.BIN').open('w+b') as handle:
                    handle.write(actual)
                    handle.flush()
                    assert lib.car_performance_read(handle.fileno(), hash32(car.lower()),
                                                    hash32(donor.lower()), ct.byref(production_record)) == 0
                production_record.source = 2
                matched, _ = compare(lib, source, native, row, production_record, True, verify_source=car not in siblings)
                if matched:
                    production.append(car)
                if car in siblings:
                    assert actual == car_performance.sibling_record(donor_bytes, car, donor)
                    assert production_record.version == 3 and not production_record.torque_count
                    overridden.append(car)
                else:
                    assert actual == output
            os.environ['NFS_ADDCAR_PERFORMANCE'] = 'off'
            assert not lib.car_performance_enabled()
            source_match, donor_match = compare(lib, source, native, row, record, False)
            if donor_match and not source_match:
                off.append(car)
            del os.environ['NFS_ADDCAR_PERFORMANCE']
            assert lib.car_performance_enabled()
            source_match, _ = compare(lib, source, native, row, record, bool(record.version))
            (on if source_match else wrong).append(car)
        print(f'Undercover inputs: {len(on)}/14 plain source matches, {len(off)}/14 Off donor matches')
        if not donor_only:
            print(f'Production: {len(production)}/14 source matches; {len(overridden)}/7 sibling overrides pinned')
            print(f'Torque: 126 samples max {sample_worst:.9f} lb-ft; '
                  f'{points} off-grid max {worst:.9f} lb-ft')
            print(f'Discarded nonconstant curves (brakes pairs/ebrake/steering/rate/range): '
                  f'{discarded}/13 direct; {alias_discarded}/1 alias')
            assert set(overridden) == siblings
            assert set(production) == {r['origin']['folder'] for r in rows} - siblings
            assert sample_worst <= 0.0001 and worst <= 0.0001 and points == 3585
            assert discarded == [13, 13, 3, 13, 8] and alias_discarded == [1, 1, 0, 1, 1]
        if wrong:
            print('Source mismatch: ' + ', '.join(wrong))
        return int(len(on) != 14 or len(off) != 14)


if __name__ == '__main__':
    raise SystemExit(main('--donor-only' in sys.argv, '--linear-control' in sys.argv))
