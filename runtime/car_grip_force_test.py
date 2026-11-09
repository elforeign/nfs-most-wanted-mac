"""Force-stage proof; original selection pinned to 13984ab747d35795ffd6ac721ddbb155794792b3.

Zero lateral ellipse-axis regression: predecessor rejects 0/2; fixed rejects 2/2
with cleared output; zero-axis circle control stays accepted.

Original donor mismatch: dynamic 43/50, static 46/50 source force goldens.
Decoded passes: 37 synthetic + 100 authored force cases; 19/19 compiled
removed-term controls fail their designated fixture; 73 invalid cases reject. The original oracle below is a transcription of the existing
solver slices, NOT an import-failure baseline or a moving-branch checkout.
Private comparisons exit 3 only after all self-contained checks have run.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import ctypes as ct
import hashlib
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class Vector(ct.Structure):
    _fields_ = [('lateral', ct.c_float), ('longitudinal', ct.c_float)]


class Sliding(ct.Structure):
    _fields_ = [(n, ct.c_float) for n in ('load', 'lateral_velocity', 'forward_velocity',
                'wheel_slip', 'speed', 'dynamic_grip', 'grip_multiplier')] + [
                (n, ct.c_int) for n in ('speed_sensitive', 'forward_rotation', 'locked')]


class Limit(ct.Structure):
    _fields_ = [('demand', Vector)] + [(n, ct.c_float) for n in
                ('load', 'static_grip', 'grip_multiplier', 'speed_gain')] + [
                ('axes', Vector), ('ellipse', ct.c_int)]


class Result(ct.Structure):
    _fields_ = [('force', Vector), ('saturation', ct.c_float)]


# Original scalar equations at the pinned revision: UpdateLoaded's sliding
# branch and circular static saturation; no new source-only ellipse exists.
ORIGINAL = r'''
#include "car_grip_force.h"
#include <math.h>
int original_sliding(const CarGripForceSliding *s, CarGripForceVector *out) {
    float load = fmaxf(0, s->load), mu = 1, scale = 0;
    float slip = sqrtf(s->wheel_slip*s->wheel_slip + s->lateral_velocity*s->lateral_velocity);
    float gain = 1;
    if (!s->locked && s->forward_rotation && s->speed_sensitive) {
        float t = (s->speed - 13.4109f) * 0.111849315f;
        gain = fminf(1, fmaxf(0, t)) * 0.14999998f + 0.85f;
    }
    if (slip > 1e-6f && (s->lateral_velocity != 0 || s->forward_velocity != 0)) {
        mu = s->dynamic_grip * s->grip_multiplier * gain;
        scale = mu / slip * load;
    }
    out->lateral = -scale * s->lateral_velocity;
    out->longitudinal = scale * s->wheel_slip;
    if (s->speed < 0.44703f && mu > 0.1f) {
        out->lateral *= 1 / mu;
        out->longitudinal *= 1 / mu;
    }
    return 0;
}
int original_limit(const CarGripForceLimit *s, CarGripForceResult *out) {
    float load = fmaxf(0, s->load);
    float norm = sqrtf(s->demand.lateral*s->demand.lateral + s->demand.longitudinal*s->demand.longitudinal);
    float cap = s->axes.lateral * s->grip_multiplier * s->static_grip * load * s->speed_gain;
    float scale = 1;
    if (norm > cap && norm > 0.001f) scale = cap / norm;
    out->force.lateral = scale * s->demand.lateral;
    out->force.longitudinal = scale * s->demand.longitudinal;
    out->saturation = scale;
    return 0;
}
'''


def compile_lib(tmp, name, text, original=False):
    src = tmp / (name + '.c')
    src.write_text(text)
    lib = tmp / (name + '.dylib')
    p = subprocess.run(['clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-I' + str(ROOT / 'runtime'), str(src),
                        '-o', str(lib)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    dll = ct.CDLL(str(lib))
    prefix = 'original_' if original else 'car_grip_force_'
    for name, arg, result in (('sliding', Sliding, Vector), ('limit', Limit, Result)):
        fun = getattr(dll, prefix + name)
        fun.argtypes = [ct.POINTER(arg), ct.POINTER(result)]
        fun.restype = ct.c_int
    if not original:
        dll.car_grip_force_drift_axes.argtypes = [ct.c_float, ct.c_float, ct.c_float,
                                                 ct.c_int, ct.POINTER(Vector)]
        dll.car_grip_force_drift_axes.restype = ct.c_int
    return dll


def close(actual, expected):
    return all(math.isclose(a, b, rel_tol=3e-6, abs_tol=2e-6) for a, b in zip(actual, expected))


def sliding(dll, value, original=False):
    out = Vector(99, 98)
    rc = getattr(dll, 'original_sliding' if original else 'car_grip_force_sliding')(
        ct.byref(value), ct.byref(out))
    assert rc == 0
    return (out.lateral, out.longitudinal)


def limit(dll, value, original=False):
    out = Result(Vector(99, 98), 97)
    rc = getattr(dll, 'original_limit' if original else 'car_grip_force_limit')(
        ct.byref(value), ct.byref(out))
    assert rc == 0
    return out.force.lateral, out.force.longitudinal, out.saturation


def axes(dll, coefficient, throttle, speed, rear):
    out = Vector(99, 98)
    assert dll.car_grip_force_drift_axes(coefficient, throttle, speed, rear, ct.byref(out)) == 0
    return out.lateral, out.longitudinal


def fixtures():
    # Hand-derived from 3:4:5 vectors, N = mu*load; independent of the C code.
    slip = {}
    slip['front'] = (Sliding(1000, 3, 10, 4, 30, 1.3, 1, 0, 1, 0), (-780, 1040))
    slip['rear_reverse'] = (Sliding(800, -4, -10, 3, 30, 1.5, .5, 0, 0, 0), (480, 360))
    slip['zero_load'] = (Sliding(0, 3, 10, 4, 30, 1.3, 1, 0, 1, 0), (0, 0))
    slip['negative_load'] = (Sliding(-1, 3, 10, 4, 30, 1.3, 1, 0, 1, 0), (0, 0))
    slip['zero_slip'] = (Sliding(1000, 0, 10, 0, 30, 1.3, 1, 0, 1, 0), (0, 0))
    slip['stationary'] = (Sliding(1000, 0, 0, 4, 30, 1.3, 1, 0, 1, 0), (0, 0))
    slip['low_speed'] = (Sliding(1000, 3, 10, 4, 0, 1.3, 1, 0, 1, 0), (-600, 800))
    slip['low_mu'] = (Sliding(1000, 3, 10, 4, 0, .05, 1, 0, 1, 0), (-30, 40))
    slip['speed_start'] = (Sliding(1000, 3, 10, 4, 13.4109, 1, 1, 1, 1, 0), (-510, 680))
    slip['speed_middle'] = (Sliding(1000, 3, 10, 4, 17.8812, 1, 1, 1, 1, 0), (-555, 740))
    slip['speed_end'] = (Sliding(1000, 3, 10, 4, 30, 1, 1, 1, 1, 0), (-600, 800))
    slip['locked'] = (Sliding(1000, 3, 10, 4, 13, 1, 1, 1, 1, 1), (-600, 800))
    slip['reverse_rotation'] = (Sliding(1000, 3, 10, 4, 13, 1, 1, 1, 0, 0), (-600, 800))
    slip['near_stationary'] = (Sliding(1000, 0, 1e-7, 4, 30, 1.3, 1, 0, 1, 0), (0, 0))
    caps = {}
    caps['circle'] = (Limit(Vector(3000, 4000), 1000, 1.5, 1, 1, Vector(1, 1), 0), (900, 1200, .3))
    caps['circle_boundary'] = (Limit(Vector(900, 1200), 1000, 1.5, 1, 1, Vector(1, 1), 0), (900, 1200, 1))
    caps['circle_gains'] = (Limit(Vector(3000, 4000), 1000, 1.5, .5, .8, Vector(.5, 1), 0), (180, 240, .06))
    caps['no_load'] = (Limit(Vector(3000, 4000), 0, 1.5, 1, 1, Vector(1, 1), 0), (0, 0, 0))
    caps['zero_static'] = (Limit(Vector(3000, 4000), 1000, 0, 1, 1, Vector(1, 1), 0), (0, 0, 0))
    caps['circle_epsilon'] = (Limit(Vector(.0005, 0), 1000, 0, 1, 1, Vector(1, 1), 0), (.0005, 0, 1))
    caps['ellipse_long'] = (Limit(Vector(0, 2000), 1000, 1, 1, 1, Vector(.4, 1.5), 1), (0, 1500, .75))
    caps['ellipse_lateral'] = (Limit(Vector(1000, 0), 1000, 1, 1, 1, Vector(.4, 1.5), 1), (400, 0, .4))
    caps['ellipse_reverse'] = (Limit(Vector(0, -2000), 1000, 1, 1, 1, Vector(.4, 1.5), 1), (0, -400, .2))
    caps['ellipse_boundary'] = (Limit(Vector(0, 1500), 1000, 1, 1, 1, Vector(.4, 1.5), 1), (0, 1500, 1))
    caps['ellipse_epsilon'] = (Limit(Vector(.0005, 0), 1000, 0, 1, 1, Vector(.4, 1.5), 1), (.0005, 0, 1))
    # Independent ellipse equation: (lat/400)^2 + (long/1500)^2 = 1.
    ratio = 1 / math.sqrt((3000 / 400)**2 + (4000 / 1500)**2)
    caps['ellipse_mixed'] = (Limit(Vector(3000, 4000), 1000, 1, 1, 1, Vector(.4, 1.5), 1),
                              (3000 * ratio, 4000 * ratio, ratio))
    # r=(clamp(speed/10))^2. Front lateral=2-1.8r; rear evaluated by hand.
    ax = {}
    ax['front_stopped'] = ((.4, 1, 0, 0), (2, 1.5))
    ax['front_middle'] = ((.4, 1, 5, 0), (1.55, 1.5))
    ax['front_fast'] = ((.4, 1, 10, 0), (.2, 1.5))
    ax['rear_stopped'] = ((.4, 1, 0, 1), (1.2, 1.5))
    ax['rear_middle'] = ((.4, 1, 5, 1), (1, 1.5))
    ax['rear_fast'] = ((.4, 1, 10, 1), (.4, 1.5))
    ax['rear_coast'] = ((.4, 0, 5, 1), (1.95, 1.5))
    ax['rear_partial'] = ((.4, .5, 5, 1), (1.428125, 1.5))
    ax['rear_floor'] = ((0, 1, 10, 1), (.001, 1.5))
    ax['rear_reverse'] = ((.4, 1, -10, 1), (1.2, 1.5))
    ax['rear_clamped'] = ((.4, 1, 20, 1), (.4, 1.5))
    return slip, caps, ax


def proof(tmp, dll, original):
    slips, caps, ax = fixtures()
    for name, (value, expected) in slips.items():
        assert close(sliding(dll, value), expected), (name, sliding(dll, value), expected)
    for name, (value, expected) in caps.items():
        assert close(limit(dll, value), expected), (name, limit(dll, value), expected)
    for name, (value, expected) in ax.items():
        assert close(axes(dll, *value), expected), (name, axes(dll, *value), expected)
    original_slip = [name for name, (value, expected) in slips.items()
                     if not close(sliding(original, value, True), expected)]
    original_caps = [name for name, (value, expected) in caps.items()
                     if not close(limit(original, value, True), expected)]
    assert original_slip == ['near_stationary'], original_slip
    assert set(original_caps) == {'ellipse_long', 'ellipse_boundary', 'ellipse_mixed'}, original_caps
    print(f'Original pinned slices: {len(original_slip)}/{len(slips)} sliding mismatches; '
          f'{len(original_caps)}/{len(caps)} limit mismatches ({", ".join(original_slip + original_caps)})')
    print(f'Decoded goldens PASS: {len(slips)} sliding, {len(caps)} limit, {len(ax)} axle/speed/throttle')
    # Ordinary matched slices preserve donor results away from decoded boundary
    # differences. This is a scalar-slice proof, not a complete vehicle run.
    same = 0
    for name, (value, _) in slips.items():
        if name not in original_slip:
            assert close(sliding(dll, value), sliding(original, value, True)); same += 1
    for name, (value, _) in caps.items():
        if name not in original_caps:
            assert close(limit(dll, value), limit(original, value, True)); same += 1
    print(f'Equivalent ordinary donor slices: {same}/{len(slips) + len(caps) - 4}')

    text = (ROOT / 'runtime/car_grip_force.c').read_text()
    # Each row deletes one implemented factor/condition and names its witness.
    mutations = [
        ('dynamic', 'double mu = (double)in->dynamic_grip *', 'double mu = 1.0 *', 'slip', 'front'),
        ('grip_gain', 'in->grip_multiplier * gain;', '1.0 * gain;', 'slip', 'rear_reverse'),
        ('load', 'double scale = mu * in->load / slip;', 'double scale = mu / slip;', 'slip', 'front'),
        ('slip_norm', 'mu * in->load / slip;', 'mu * in->load;', 'slip', 'front'),
        ('speed_gain', 'gain = 0.85f + 0.14999998f * t;', 'gain = 1.0 + 0.0 * t;', 'slip', 'speed_start'),
        ('speed_guard', 'in->forward_rotation && !in->locked', 'in->forward_rotation', 'slip', 'locked'),
        ('road_guard', 'road <= 1e-6', 'road < 0', 'slip', 'stationary'),
        ('low_speed', 'mu = 1.0;', 'mu = 0.0;', 'slip', 'low_speed'),
        ('static', '(double)in->static_grip *', '1.0 *', 'cap', 'circle'),
        ('static_gain', 'double cap = base * in->axes.lateral;', 'double cap = base;', 'cap', 'circle_gains'),
        ('static_speed', 'in->load * in->speed_gain;', 'in->load;', 'cap', 'circle_gains'),
        ('ellipse', 'if (!in->ellipse)', 'if (1)', 'cap', 'ellipse_long'),
        ('ellipse_axis', 'base * in->axes.longitudinal;', 'base;', 'cap', 'ellipse_long'),
        ('ellipse_fallback', 'target = lateral_cap;', 'target = longitudinal_cap;', 'cap', 'ellipse_reverse'),
        ('ramp_square', 'double ramp = t * t;', 'double ramp = t;', 'ax', 'front_middle'),
        ('source_coefficient', '3.0 * coefficient', '3.0', 'ax', 'rear_stopped'),
        ('throttle', 'coefficient * throttle * ramp', 'coefficient * ramp', 'ax', 'rear_partial'),
        ('rear_selection', 'rear ? back : front', 'rear ? front : back', 'ax', 'rear_fast'),
        ('floor', 'fmax(0.001f, rear ? back : front)', 'fmax(0.0f, rear ? back : front)', 'ax', 'rear_floor'),
    ]
    killed = 0
    for name, old, replacement, kind, witness in mutations:
        assert text.count(old) == 1, (name, text.count(old))
        mutant = compile_lib(tmp, name, text.replace(old, replacement))
        if kind == 'slip': value, expected = slips[witness]; actual = sliding(mutant, value)
        elif kind == 'cap': value, expected = caps[witness]; actual = limit(mutant, value)
        else: value, expected = ax[witness]; actual = axes(mutant, *value)
        assert not close(actual, expected), (name, witness, actual, expected)
        killed += 1
    print(f'Removed-term compiled controls: {killed}/{len(mutations)} designated fixtures fail')


def zero_axis_regression(dll):
    """Production limit: predecessor rejects 0/2 zero axes; fixed rejects 2/2."""
    rejected = 0
    for axis in (0.0, -0.0):
        value = Limit(Vector(0, 3000), 1000, 1, 1, 1, Vector(axis, 1.5), 1)
        out = Result(Vector(99, 98), 97)
        rc = dll.car_grip_force_limit(ct.byref(value), ct.byref(out))
        assert rc == -1 and bytes(out) == bytes(Result()), (axis, rc, out.force.longitudinal)
        rejected += 1
        value.ellipse = 0
        assert close(limit(dll, value), (0, 0, 0))
    print(f'Zero lateral ellipse axes: {rejected}/2 rejected and cleared; 2/2 circle controls PASS')


def invalid_proof(dll):
    count = 0
    normal = Sliding(1000, 3, 10, 4, 30, 1.3, 1, 0, 1, 0)
    for name, _ in Sliding._fields_:
        bad_values = (-1, 2) if name in ('speed_sensitive', 'forward_rotation', 'locked') else (math.nan, math.inf, -math.inf)
        if name in ('dynamic_grip', 'grip_multiplier'): bad_values += (-.1,)
        for value in bad_values:
            bad = Sliding.from_buffer_copy(bytes(normal)); setattr(bad, name, value)
            out = Vector(99, 98)
            assert dll.car_grip_force_sliding(ct.byref(bad), ct.byref(out)) == -1
            assert bytes(out) == bytes(Vector()); count += 1
    normal_cap = Limit(Vector(3000, 4000), 1000, 1.5, 1, 1, Vector(1, 1), 0)
    for name in ('load', 'static_grip', 'grip_multiplier', 'speed_gain', 'ellipse',
                 'demand.lateral', 'demand.longitudinal', 'axes.lateral', 'axes.longitudinal'):
        bad_values = (-1, 2) if name == 'ellipse' else (math.nan, math.inf, -math.inf)
        if name in ('static_grip', 'grip_multiplier', 'speed_gain', 'axes.lateral', 'axes.longitudinal'):
            bad_values += (-.1,)
        for value in bad_values:
            bad = Limit.from_buffer_copy(bytes(normal_cap))
            if '.' in name:
                first, second = name.split('.'); setattr(getattr(bad, first), second, value)
            else: setattr(bad, name, value)
            out = Result(Vector(99, 98), 97)
            assert dll.car_grip_force_limit(ct.byref(bad), ct.byref(out)) == -1
            assert bytes(out) == bytes(Result()); count += 1
    for values in ((math.nan, 1, 5, 1), (-1, 1, 5, 1), (.4, -.1, 5, 1),
                   (.4, 1.1, 5, 1), (.4, math.inf, 5, 1), (.4, 1, math.inf, 1),
                   (.4, 1, 5, 2)):
        out = Vector(99, 98)
        assert dll.car_grip_force_drift_axes(*values, ct.byref(out)) == -1
        assert bytes(out) == bytes(Vector()); count += 1
    for fun, obj in ((dll.car_grip_force_sliding, Vector), (dll.car_grip_force_limit, Result)):
        out = obj()
        assert fun(None, ct.byref(out)) == -1
        assert bytes(out) == bytes(obj())
        assert fun(None, None) == -1; count += 2
    assert dll.car_grip_force_drift_axes(.4, 1, 5, 1, None) == -1; count += 1
    # Finite inputs that overflow the float output must also fail closed.
    out = Vector(99, 98)
    large = Sliding(3e38, 3, 10, 4, 30, 3e38, 3e38, 0, 1, 0)
    assert dll.car_grip_force_sliding(ct.byref(large), ct.byref(out)) == -1
    assert bytes(out) == bytes(Vector()); count += 1
    print(f'Invalid/nonfinite/null/overflow admission: {count} cases PASS')



class Image:
    """Minimal bounded PE reader for pinned private table comparisons."""
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from('<I', data, 60)[0]
        assert data[pe:pe+4] == b'PE\0\0'
        count = struct.unpack_from('<H', data, pe + 6)[0]
        size = struct.unpack_from('<H', data, pe + 20)[0]
        self.base = struct.unpack_from('<I', data, pe + 52)[0]
        self.sections = [struct.unpack_from('<8sIIII', data, pe + 24 + size + i * 40)
                         for i in range(count)]

    def read(self, address, count):
        for _, virtual_size, rva, raw_size, offset in self.sections:
            relative = address - self.base - rva
            if 0 <= relative and relative + count <= virtual_size:
                available = min(count, max(0, raw_size - relative))
                return self.data[offset+relative:offset+relative+available].ljust(count, b'\0')
        raise AssertionError('table read outside image')

    def curves(self, address):
        result = []
        for pointer in struct.unpack('<7I', self.read(address, 28)):
            count, lo, hi, scale, values = struct.unpack('<5I', self.read(pointer, 20))
            assert count == 6
            result.append(((count, lo, hi, scale), self.read(values, count * 4)))
        return result


def curve_proof():
    source = ROOT.parent / 'decomp_carbon/input/NFSC.exe'
    donor = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent / 'Need For Speed Most Wanted Black Edition/speed.exe'))
    if not source.is_file() or not donor.is_file():
        print('SKIP: ordinary lateral-curve equivalence requires pinned source/donor images')
        return 3
    sb, db = source.read_bytes(), donor.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
    assert hashlib.sha256(db).hexdigest() == '80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c'
    source_curves = Image(sb).curves(0xA63554)
    donor_curves = Image(db).curves(0x8F8A70)
    assert source_curves == donor_curves
    # Changing a real ordinate must break equality; no table data is embedded.
    broken = list(donor_curves)
    descriptor, values = broken[1]
    changed = bytearray(values); changed[0] ^= 1
    broken[1] = descriptor, bytes(changed)
    assert sum(a != b for a, b in zip(source_curves, broken)) == 1
    print('Ordinary lateral curves: 7/7 descriptors, 42/42 ordinates equal; changed-ordinate control fails 1/7')
    return 0


def private_proof(dll, original):
    source = input_path('NFSC') / 'GLOBAL/attributes.bin'
    donor = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'GLOBAL/ATTRIBUTES.BIN'
    if not source.is_file() or not donor.is_file() or not (ROOT / 'tools_snapshot/test_driver_seats.py').is_file():
        print('SKIP: private force comparison requires both attribute vaults and fixture manifest')
        return 3
    sb, db = source.read_bytes(), donor.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
    assert hashlib.sha256(db).hexdigest() == '3d0362f2c052b2f93576852f504cb7806b14eefbf83b8531d45dc1d430ec6337'
    sys.path.insert(0, str(ROOT / 'tools'))
    from setup_kit import car_convert as cc
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    from test_driver_seats import CASES
    a, d = cc.FitAttributes(sb), cc.FitAttributes(db)
    tiers = passes = dynamic_failures = static_failures = equivalent = scale_differences = 0
    # Car/axle values are read from pinned private inputs, never embedded in code.
    for car, slot in CASES:
        refs = list(struct.iter_unpack('<3I', a.raw('pvehicle', cc.hash32(car.lower()), 'tires')))
        donor_refs = list(struct.iter_unpack('<3I', d.raw('pvehicle', cc.hash32(slot.lower()), 'tires')))
        for tier, (_, key, _) in enumerate(refs):
            donor_key = donor_refs[tier][1]
            sd = struct.unpack('<2f', a.raw('tires', key, 'DYNAMIC_GRIP'))
            ss = struct.unpack('<2f', a.raw('tires', key, 'STATIC_GRIP'))
            dd = struct.unpack('<2f', d.raw('tires', donor_key, 'DYNAMIC_GRIP'))
            ds = struct.unpack('<2f', d.raw('tires', donor_key, 'STATIC_GRIP'))
            sg = struct.unpack('<2f', a.raw('tires', key, 'GRIP_SCALE'))
            dg = struct.unpack('<2f', d.raw('tires', donor_key, 'GRIP_SCALE'))
            scale_differences += sum(x != y for x, y in zip(sg, dg))
            for axle in (0, 1):
                # Source golden from vector direction (-3/5,4/5), mu and load;
                # independent of both compiled implementations.
                load = 1000 if axle == 0 else 800
                expected = (-.6 * sd[axle] * load, .8 * sd[axle] * load)
                src = Sliding(load, 3, 10, 4, 30, sd[axle], 1, 0, 1, 0)
                old = Sliding.from_buffer_copy(bytes(src)); old.dynamic_grip = dd[axle]
                assert close(sliding(dll, src), expected); passes += 1
                dynamic_failures += not close(sliding(original, old, True), expected)
                assert close(sliding(dll, old), sliding(original, old, True)); equivalent += 1
                expected = (.6 * ss[axle] * load, .8 * ss[axle] * load, ss[axle] * load / 50000)
                src_cap = Limit(Vector(30000, 40000), load, ss[axle], 1, 1, Vector(1, 1), 0)
                old_cap = Limit.from_buffer_copy(bytes(src_cap)); old_cap.static_grip = ds[axle]
                assert close(limit(dll, src_cap), expected); passes += 1
                static_failures += not close(limit(original, old_cap, True), expected)
                assert close(limit(dll, old_cap), limit(original, old_cap, True)); equivalent += 1
            tiers += 1
    assert tiers == 25
    assert dynamic_failures > 0 and static_failures > 0
    print(f'Pinned original donor vs source force goldens: dynamic {dynamic_failures}/{2*tiers}, '
          f'static {static_failures}/{2*tiers} mismatches; source helpers {passes}/{4*tiers} PASS')
    print(f'Unchanged donor coefficients: {equivalent}/{4*tiers} ordinary force outputs equivalent')
    assert scale_differences == 31
    print(f'Original retained GRIP_SCALE differs on {scale_differences}/{2*tiers} axles; ordinary curve reuse needs source gain')
    return curve_proof()


def main():
    scratch = ROOT / '.metal-cache/grip-g2'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='force-', dir=scratch) as work:
        tmp = Path(work)
        dll = compile_lib(tmp, 'force', (ROOT / 'runtime/car_grip_force.c').read_text())
        original = compile_lib(tmp, 'original', ORIGINAL, True)
        zero_axis_regression(dll)
        proof(tmp, dll, original)
        invalid_proof(dll)
        return private_proof(dll, original)


if __name__ == '__main__':
    sys.exit(main())
