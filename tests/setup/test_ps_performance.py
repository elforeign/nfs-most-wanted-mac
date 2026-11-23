"""Compare ProStreet source projections with the native MW stock layouts.

Donor-only baseline: 0/31 source matches (exit 1), 31/31 donor matches.
Plain source projection: 31/31 source matches. Off: 31/31 donor matches.
The sibling test checks the retail-derived records.

Round-two control -> fixed native stock bars and their inputs:
F1 speed 0.95970 -> 0.90783 (109.25107 -> 107.33191 m/s).
F1 handling 0.00000 -> 0.00000 (authored 25, retail minimum 36).
GTO65 acceleration 0.48750 -> 0.33750 (stock nitrous 2.5 -> 0 s).
The same check fails with the round-two layouts and uncapped estimate.
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
from setup_kit import car_performance, ps_attrib
from setup_kit.car_convert import FitAttributes, FormatError, hash32
from setup_kit.input_paths import input_path
from test_car_performance import FIELDS, Record, layout, ref

DONOR_SLOTS = {
    'transmission': (0x6c,),
    'induction': (8, 0xc, 0x14),
    'chassis': (0x68,),
    'brakes': (0, 4),
}


def rating_inputs(lib, source, mw, rows, work):
    def stock_layout(car, cls):
        row = next(row for row in rows if row['origin']['folder'] == car)
        donor = row['donors']['model']
        retail = hash32(donor.lower())
        data = ps_attrib.performance(source, mw, car, donor)
        record = Record()
        with (work / 'rating.BIN').open('w+b') as handle:
            handle.write(data)
            handle.flush()
            assert lib.car_performance_read(handle.fileno(), hash32(car.lower()), retail, ct.byref(record)) == 0
        record.source = 1
        if cls == 'pvehicle':
            body = ct.create_string_buffer(layout(mw, cls, retail, 0x50))
            handling = ct.create_string_buffer(16)
            lib.car_performance_body(ct.byref(record), body, handling)
            return handling.raw
        link, size = {'engine': (0, 0x60), 'nos': (6, 0x20)}[cls]
        stock = layout(mw, cls, ref(mw, retail, cls, '<'), size)
        projected = ct.create_string_buffer(stock)
        lib.car_performance_layout(ct.byref(record), link, False, stock, stock, projected)
        return projected.raw

    # These inputs feed the retail speed clamp, handling bar and acceleration run.
    engine = stock_layout('MCLARENF1', 'engine')
    cutoff = lib.car_performance_clamp_top_speed(engine, 109.251068)
    handling = struct.unpack_from('<f', stock_layout('MCLARENF1', 'pvehicle'), 8)[0]
    nitrous = struct.unpack_from('<f', stock_layout('GTO65', 'nos'), 0x10)[0]
    print(f'Rating inputs: F1 top speed {cutoff:.5f} m/s, F1 handling {handling:g}, GTO65 nitrous {nitrous:g} s')
    assert abs(cutoff - 240.1 * 0.44703) < 0.00001
    assert handling == 25
    assert nitrous == 0


def reference_torque(source, vehicle, idle, limit):
    # This reader uses the stock vehicle's layout and relocation sites directly.
    vault, record = source.collections[hash32('vehicle'), vehicle]
    dep, at = vault.pointer(0, record + 28)
    start = at + 0x68
    x0, x1, y0, y1 = struct.unpack_from('<4f', vault.buffers[dep], start)
    points = struct.unpack_from('<I', vault.buffers[dep], start + 20)[0]

    def vector(relative):
        idx, pos = vault.pointer(dep, start + relative)
        return struct.unpack_from(f'<{points}f', vault.buffers[idx], pos)

    knots, heights, curvature = vector(16), vector(24), vector(32)
    spacing = (limit - idle) / 8
    values = []
    for index in range(9):
        rpm = idle + spacing * index
        if rpm >= x1:
            normalized = heights[-1]
        elif rpm <= x0:
            normalized = heights[0]
        else:
            time = (rpm - x0) / (x1 - x0)
            right = next(i for i, knot in enumerate(knots) if time < knot)
            left = right - 1
            interval = knots[right] - knots[left]
            t = (time - knots[left]) / interval
            normalized = heights[left] + t * (heights[right] - heights[left])
            normalized -= (interval * interval * t * (1 - t) *
                           ((2 - t) * curvature[left] + (1 + t) * curvature[right]) / 6)
        value = y0 + normalized * (y1 - y0)
        values.append(value * rpm / x0 if 0 < rpm < x0 else value)
    return struct.pack('<9f', *values)


def native_inputs(lib, source, mw, row, record, enabled):
    car, donor = row['origin']['folder'].lower(), row['donors']['model'].lower()
    own, retail = hash32(car), hash32(donor)
    if (hash32('pvehicle'), own) not in source.collections:
        own = hash32('dlcbp_' + car)
    vehicle = ref(source, own, 'vehicle', '<')

    def raw(name):
        return source.raw('vehicle', vehicle, name)

    donor_inputs, source_inputs, live_inputs = [], [], []

    old = layout(mw, 'pvehicle', retail, 0x50)
    actual = ct.create_string_buffer(old)
    donor_handling = struct.pack('<4H', 2, 2, 4, 0) + mw.raw('pvehicle', retail, 'HandlingRating')
    handling = ct.create_string_buffer(donor_handling)
    if enabled:
        lib.car_performance_body(ct.byref(record), actual, handling)
    source_inputs.extend((raw('TENSOR_SCALE') + old[16:0x38] + raw('MASS') + old[0x3c:],
                          donor_handling[:8] + source.raw('pvehicle', own, 'HandlingRating')))
    donor_inputs.extend((old, donor_handling))
    live_inputs.extend((actual.raw[:len(old)], handling.raw[:16]))

    for i, (cls, size, fields) in enumerate(FIELDS):
        old = layout(mw, cls, ref(mw, retail, cls, '<'), size)
        actual = ct.create_string_buffer(old)
        if enabled:
            lib.car_performance_layout(ct.byref(record), i, False, old, old, actual)
            refs = mw.raw('pvehicle', retail, cls)
            tiers = list(struct.iter_unpack('<3I', refs))
            indices = (ct.c_uint * 3)()
            assert lib.car_performance_tiers(ct.byref(record), i, len(tiers), indices) == len(tiers)
            assert list(indices) == [0, 1, 2]
            for _, endpoint_key, _ in tiers[1:]:
                endpoint = layout(mw, cls, endpoint_key, size)
                installed = ct.create_string_buffer(endpoint)
                lib.car_performance_layout(ct.byref(record), i, True, old, endpoint, installed)
                for offset in DONOR_SLOTS.get(cls, ()):
                    assert installed.raw[offset:offset + 4] == endpoint[offset:offset + 4], (car, cls, offset)
        expected = bytearray(old)
        for name, (offset, _) in fields.items():
            if cls == 'engine' and name == 'TORQUE':
                idle = struct.unpack('<f', raw('IDLE'))[0]
                limit = struct.unpack('<f', raw('MAX_RPM'))[0]
                expected[:8] = struct.pack('<4H', 9, 9, 4, 0)
                value = reference_torque(source, vehicle, idle, limit)
            elif name == 'GEAR_RATIO':
                gears = raw(name)
                expected[:8] = struct.pack('<4H', 9, len(gears) // 4, 4, 0)
                value = gears.ljust(36, b'\0')
            elif name == 'GEAR_EFFICIENCY':
                expected[0x40:0x48] = struct.pack('<4H', 9, 9, 4, 0)
                value = raw(name)[:36]
            elif name == 'ENGINE_BRAKING':
                expected[0x3c:0x44] = struct.pack('<4H', 3, 3, 4, 0)
                value = raw(name)
            elif name == 'DIFFERENTIAL':
                expected[0x2c:0x34] = struct.pack('<4H', 3, 3, 4, 0)
                value = raw(name)
            elif name == 'BRAKES':
                value = struct.pack('<2f', *(v * 0.25 for v in struct.unpack('<2f', raw(name))))
            elif name == 'STEERING':
                value = raw(name)[:4]
            elif name in ('TORQUE_CONVERTER', 'VACUUM', 'SPOOL', 'PSI', 'AERO_COEFFICIENT', 'BRAKE_LOCK'):
                continue
            else:
                value = raw(name)
            expected[offset:offset + len(value)] = value
        if cls == 'engine':
            expected[0x2c:0x34] = struct.pack('<4H', 2, 2, 4, 0)
            onset, cutoff = struct.unpack('<2f', raw('SPEED_LIMITER'))
            struct.pack_into('<2f', expected, 0x34, onset, cutoff - onset)
        donor_inputs.append(old)
        source_inputs.append(expected)
        live_inputs.append(actual.raw[:size])

    # The source has no stocked nitrous. Native installed tiers keep donor kits.
    try:
        source_nos = raw('NOS')
    except FormatError as exc:
        assert str(exc) == 'fit field absent: NOS', car
    else:
        nos_class, nos_key, _ = struct.unpack('<3I', source_nos)
        assert nos_class == hash32('nos') and layout(source, 'nos', nos_key, 32) == bytes(32), car
    nos_tiers = list(struct.iter_unpack('<3I', mw.raw('pvehicle', retail, 'nos')))
    old = layout(mw, 'nos', nos_tiers[0][1], 0x20)
    actual = ct.create_string_buffer(old)
    if enabled:
        assert record.version == 1
        indices = (ct.c_uint * 3)()
        count = lib.car_performance_tiers(ct.byref(record), 6, len(nos_tiers), indices)
        assert list(indices[:count]) == ([0, 0, 1] if len(nos_tiers) == 2 else list(range(len(nos_tiers))))
        lib.car_performance_layout(ct.byref(record), 6, False, old, old, actual)
        for index in indices[1:count]:
            endpoint = layout(mw, 'nos', nos_tiers[index][1], 0x20)
            installed = ct.create_string_buffer(endpoint)
            lib.car_performance_layout(ct.byref(record), 6, True, old, endpoint, installed)
            assert installed.raw[:0x20] == endpoint, car
    donor_inputs.append(old)
    source_inputs.append(bytes(0x20))
    live_inputs.append(actual.raw[:0x20])
    return live_inputs == source_inputs, live_inputs == donor_inputs


def main():
    source_path = input_path('Need for Speed ProStreet/GLOBAL/ATTRIBUTES.BIN')
    mw_root = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    donor_path = mw_root / 'GLOBAL/attributes.bin'
    if not (source_path.is_file() and donor_path.is_file()):
        print('SKIP: ProStreet PC or MW attributes unavailable')
        return 3
    source_bytes, donor_bytes = source_path.read_bytes(), donor_path.read_bytes()
    source, mw = FitAttributes(source_bytes), FitAttributes(donor_bytes)
    rows = [r for r in json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_bytes())
            if r['origin']['game'] == 'prostreet']
    assert len(rows) == len({r['origin']['folder'] for r in rows}) == 31

    with tempfile.TemporaryDirectory(prefix='ps-performance-') as td:
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
        rating_inputs(lib, source, mw, rows, work)
        on, off, wrong = [], [], []
        for row in rows:
            car, donor = row['origin']['folder'], row['donors']['model']
            record = Record()
            if car in car_performance.SIBLINGS:
                output = ps_attrib.source_performance(source, mw, car, donor)
            else:
                output = ps_attrib.sidecars(source_bytes, donor_bytes, car, donor)['PERFORMANCE.BIN']
            if output:
                with (work / 'PERFORMANCE.BIN').open('w+b') as handle:
                    handle.write(output)
                    handle.flush()
                    assert lib.car_performance_read(handle.fileno(), hash32(car.lower()),
                                                    hash32(donor.lower()), ct.byref(record)) == 0, car
            os.environ['NFS_ADDCAR_PERFORMANCE'] = 'off'
            assert not lib.car_performance_enabled()
            source_match, donor_match = native_inputs(lib, source, mw, row, record, False)
            if donor_match and not source_match:
                off.append(car)
            del os.environ['NFS_ADDCAR_PERFORMANCE']
            assert lib.car_performance_enabled()
            record.source = int(bool(record.version))
            source_match, _ = native_inputs(lib, source, mw, row, record, bool(record.version))
            (on if source_match else wrong).append(car)
        print(f'ProStreet inputs: {len(on)}/31 plain source matches, {len(off)}/31 Off donor matches')
        if wrong:
            print('Source mismatch: ' + ', '.join(wrong))
        return int(len(on) != 31 or len(off) != 31)


if __name__ == '__main__':
    raise SystemExit(main())
