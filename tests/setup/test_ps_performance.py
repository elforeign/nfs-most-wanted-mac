"""Compare ProStreet source projections with the native MW stock layouts.

Donor-only baseline: 0/31 source matches (exit 1), 31/31 donor matches.
Plain source projection: 31/31 source matches; production sidecars: 21/31.
Ten explicit sibling overrides retain their retail-derived records. Off: 31/31 donors.
Off-grid torque: maximum error 0.000030329 lb-ft over 7936 points (limit 0.0001).
Broken linear nine-sample control: 37.04590 lb-ft, DBR9 at 2088.716 RPM.
The existing 279-point sample comparison is retained.

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


def reference_torque(source, vehicle, idle, limit, rpms=None):
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
    for rpm in (rpms if rpms is not None else [idle + spacing * i for i in range(9)]):
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
    return values if rpms is not None else struct.pack('<9f', *values)


def native_inputs(lib, source, mw, row, record, enabled, *, verify_kits=True):
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
                for offset in DONOR_SLOTS.get(cls, ()) if verify_kits else ():
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
        assert record.version in (1, 3, 4)
        indices = (ct.c_uint * 3)()
        count = lib.car_performance_tiers(ct.byref(record), 6, len(nos_tiers), indices)
        assert list(indices[:count]) == ([0, 0, 1] if len(nos_tiers) == 2 else list(range(len(nos_tiers))))
        lib.car_performance_layout(ct.byref(record), 6, False, old, old, actual)
        for index in indices[1:count]:
            endpoint = layout(mw, 'nos', nos_tiers[index][1], 0x20)
            installed = ct.create_string_buffer(endpoint)
            lib.car_performance_layout(ct.byref(record), 6, True, old, endpoint, installed)
            if verify_kits:
                assert installed.raw[:0x20] == endpoint, car
    donor_inputs.append(old)
    source_inputs.append(bytes(0x20))
    live_inputs.append(actual.raw[:0x20])
    return live_inputs == source_inputs, live_inputs == donor_inputs



def torque_hook_library(work):
    # Compile the production binding, teardown and guest hook verbatim. The
    # original-function sentinel proves that fallback preserves the full Cpu.
    header = (ROOT / 'runtime/car_performance_attrib_impl.h').read_text()
    prefix = header[header.index('typedef struct PerformanceAllocation'):header.index('static void performance_link')]
    harness = r'''
#include "runtime.h"
#include "car_performance.h"
#include <assert.h>
#include <stdlib.h>
uint8_t memory[4096], *G=memory;
static void fail(const char *message) { (void)message; abort(); }
static uint32_t gmalloc(uint32_t size) { (void)size; return 0x900; }
static void gfree(uint32_t address) { (void)address; }
void f_00672FE0_orig(Cpu *c) {
    c->eax=0x12345678;
    x87_push(c,-123.25);
    c->esp+=4;
}
PRODUCTION_PREFIX
void probe_bind(const CarPerformance *record) {
    performance_release(1);
    performance_release(2);
    performance_alloc(1,16);
    performance_bind_torque(1,0x300,record);
    performance_bind_torque(2,0x700,record);
}
void probe_release(unsigned vault) { performance_release(vault); }
float probe_torque(const void *layout,float rpm,unsigned mode) {
    memset(memory,0,sizeof memory);
    memcpy(G+0x300,layout,0x60);
    memcpy(G+0x400,layout,0x60);
    memcpy(G+0x700,layout,0x60);
    Cpu cpu={.esp=0x100,.top=3,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
    Cpu *c=&cpu;
    M32(0x104)=0x200; MF32(0x108)=rpm;
    M32(0x208)=mode==1 ? 0x400 : mode==2 ? 0x700 : mode==3 ? 0x800 : 0x300;
    if (mode==1) {
        M32(0x204)=0x500; M8(0x210)=1;
        M32(0x510)=0x600; M32(0x618)=0x300;
    }
    Cpu original=cpu, expected=cpu;
    f_00672FE0_orig(&expected);
    f_00672FE0(c);
    if (ST(0)==-123.25) assert(!memcmp(&cpu,&expected,sizeof cpu));
    else {
        assert(cpu.esp==original.esp+4 && cpu.top==2);
        original.esp=cpu.esp; original.top=cpu.top; original.st[2]=cpu.st[2];
        assert(!memcmp(&cpu,&original,sizeof cpu));
    }
    return (float)ST(0);
}
'''.replace('PRODUCTION_PREFIX', prefix)
    path = work / 'torque_hook.c'
    path.write_text(harness)
    binary = work / 'torque_hook.dylib'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-dynamiclib',
                    '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'runtime'), str(path),
                    str(ROOT / 'runtime/car_performance.c'), '-o', str(binary)], check=True)
    lib = ct.CDLL(str(binary))
    lib.probe_bind.argtypes = [ct.POINTER(Record)]
    lib.probe_release.argtypes = [ct.c_uint]
    lib.probe_torque.argtypes = [ct.c_void_p, ct.c_float, ct.c_uint]
    lib.probe_torque.restype = ct.c_float
    return lib


def malformed_splines(lib, work, data, car, donor):
    def changed(offset, fmt, *values):
        bad = bytearray(data)
        struct.pack_into(fmt, bad, offset, *values)
        return bad
    count = struct.unpack_from('<I', data, 328)[0]
    cases = [data[:-1], data + bytes(4), changed(0, '<4s', b'CPF5'),
             changed(4, '<I', len(data)-4), changed(328, '<I', 1), changed(328, '<I', 65),
             changed(312, '<f', float('nan')), changed(316, '<f', 0),
             changed(320, '<2f', 2, 1), changed(332, '<f', .1),
             changed(336, '<f', 0), changed(332+4*(count-1), '<f', .9),
             changed(332+4*count, '<f', float('inf')),
             changed(332+8*count, '<f', float('nan'))]
    for bad in cases:
        with (work / 'malformed.BIN').open('w+b') as handle:
            handle.write(bad)
            handle.flush()
            assert lib.car_performance_read(handle.fileno(), hash32(car.lower()),
                                            hash32(donor.lower()), ct.byref(Record())) == -1
    return len(cases)

def main():
    assert sys.argv[1:] in ([], ['--linear-control'])
    linear_control = bool(sys.argv[1:])
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
        hook = torque_hook_library(work)
        lib.car_performance_torque.argtypes = [ct.POINTER(Record), ct.c_void_p, ct.c_float,
                                               ct.POINTER(ct.c_float)]
        siblings = {'997GT2', '997GT3', 'CTSV', 'IS350', 'LANCEREVOX',
                    'MUSTANG03', 'RS4', 'S3', 'S4', 'TTN'}
        assert {r['origin']['folder'] for r in rows if r['origin']['folder'] in car_performance.SIBLINGS} == siblings
        on, off, production, overridden = [], [], [], []
        worst, broken_worst, sample_worst = (0, '', 0), (0, '', 0), 0
        points = samples = 0
        for row in rows:
            car, donor = row['origin']['folder'], row['donors']['model']
            record = Record()
            output = ps_attrib.source_performance(source, mw, car, donor)
            def read(data):
                result = Record()
                with (work / 'PERFORMANCE.BIN').open('w+b') as handle:
                    handle.write(data)
                    handle.flush()
                    assert lib.car_performance_read(handle.fileno(), hash32(car.lower()),
                                                   hash32(donor.lower()), ct.byref(result)) == 0, car
                result.source = 1
                return result
            record = read(output)
            assert record.version == 4
            hook.probe_bind(ct.byref(record))
            vehicle = source.linked_key('pvehicle', ps_attrib.pvehicle_key(source, car), 'vehicle')
            idle, maximum = (struct.unpack('<f', source.raw('vehicle', vehicle, n))[0]
                             for n in ('IDLE', 'MAX_RPM'))
            old = layout(mw, 'engine', ref(mw, hash32(donor.lower()), 'engine', '<'), 0x60)
            engine = ct.create_string_buffer(old)
            lib.car_performance_layout(ct.byref(record), 0, False, old, old, engine)
            nine = struct.unpack_from('<9f', engine.raw, 8)
            reference = reference_torque(source, vehicle, idle, maximum,
                                         [idle + (maximum - idle) * i / 8 for i in range(9)])
            sample_worst = max(sample_worst, *(abs(a-b) for a,b in zip(nine, reference)))
            samples += 9
            # Prime denominator excludes every sample and covers the full RPM range.
            rpms = [ct.c_float(idle + (maximum-idle)*i/257).value for i in range(1, 257)]
            for rpm, expected in zip(rpms, reference_torque(source, vehicle, idle, maximum, rpms)):
                actual = ct.c_float()
                assert lib.car_performance_torque(ct.byref(record), engine, rpm, ct.byref(actual)) == 1
                hooked = hook.probe_torque(engine, rpm, 0)
                modified = hook.probe_torque(engine, rpm, 1)
                assert hooked == modified == actual.value
                position = (rpm-idle)/(maximum-idle)*8
                left = min(int(position), 7)
                linear = nine[left] + (nine[left+1]-nine[left])*(position-left)
                observed = linear if linear_control else hooked
                worst = max(worst, (abs(observed-expected), car, rpm))
                broken_worst = max(broken_worst, (abs(linear-expected), car, rpm))
                points += 1
            # A uniform kit gain must survive between knots and through a
            # modified instance; the idle/max envelope can move with the kit.
            kit = bytearray(engine.raw[:0x60])
            struct.pack_into('<9f', kit, 8, *(v+16 for v in nine))
            struct.pack_into('<f', kit, 0x54, maximum*2)
            struct.pack_into('<f', kit, 0x5c, idle*2)
            for rpm, expected in zip(rpms, reference_torque(source, vehicle, idle, maximum, rpms)):
                assert abs(hook.probe_torque(bytes(kit), rpm*2, 1)-(expected+16)) <= 0.0001
            assert hook.probe_torque(engine, idle, 3) == -123.25  # Unbound retail layout.
            hook.probe_release(1)
            assert hook.probe_torque(engine, idle, 0) == -123.25
            assert abs(hook.probe_torque(engine, idle, 2)-nine[0]) <= 0.0001
            hook.probe_release(2)
            assert hook.probe_torque(engine, idle, 2) == -123.25
            legacy = bytearray(output[:312])
            struct.pack_into('<4sI', legacy, 0, b'CPF1', 312)
            legacy_record = read(legacy)
            assert not legacy_record.torque_count
            hook.probe_bind(ct.byref(legacy_record))
            assert hook.probe_torque(engine, idle, 0) == -123.25
            refused = malformed_splines(lib, work, output, car, donor)
            actual_sidecars = ps_attrib.sidecars(source_bytes, donor_bytes, car, donor)
            actual_record = read(actual_sidecars['PERFORMANCE.BIN'])
            matched, _ = native_inputs(lib, source, mw, row, actual_record, True,
                                      verify_kits=car not in siblings)
            if matched:
                production.append(car)
            if car in siblings:
                assert actual_sidecars['PERFORMANCE.BIN'] == car_performance.sibling_record(
                    mw, car, donor, wheels=actual_sidecars['WHEELFIT.BIN']), car
                assert actual_record.version == 3 and not actual_record.torque_count
                overridden.append(car)
            else:
                assert actual_sidecars['PERFORMANCE.BIN'] == output, car
            os.environ['NFS_ADDCAR_PERFORMANCE'] = 'off'
            assert not lib.car_performance_enabled()
            source_match, donor_match = native_inputs(lib, source, mw, row, record, False)
            if donor_match and not source_match:
                off.append(car)
            del os.environ['NFS_ADDCAR_PERFORMANCE']
            assert lib.car_performance_enabled()
            record.source = int(bool(record.version))
            source_match, _ = native_inputs(lib, source, mw, row, record, bool(record.version))
            assert source_match, car
            on.append(car)
        print(f'ProStreet inputs: {len(on)}/31 plain source matches, '
              f'{len(production)}/31 production source matches, {len(off)}/31 Off donor matches; '
              f'{len(overridden)}/10 sibling overrides pinned')
        print(f'Wire: {refused} malformed spline cases/car refused; 31 legacy fallbacks and vault lifetimes passed')
        print(f'Torque: {samples} samples max {sample_worst:.9f} lb-ft; '
              f'{points} off-grid max {worst[0]:.9f} lb-ft ({worst[1]} @ {worst[2]:.3f} RPM); '
              f'broken linear {broken_worst[0]:.5f} lb-ft ({broken_worst[1]} @ {broken_worst[2]:.3f} RPM)')
        assert set(overridden) == siblings and set(production) == {r['origin']['folder'] for r in rows} - siblings
        assert len(on) == len(off) == 31 and samples == 279 and sample_worst <= 0.0001
        assert points == 7936 and worst[0] <= 0.0001
        assert abs(broken_worst[0]-37.04590) < 0.0001 and broken_worst[1] == 'DBR9'
        assert abs(broken_worst[2]-2088.716) < 0.001
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
