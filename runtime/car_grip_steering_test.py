"""Yaw/friction equivalence evidence; original equations pinned to 13984ab7.

Different donor coefficients: 172/200 limit and 344/400 friction mismatches;
manually supplied source coefficients pass 600/600 under both equations.
These goldens do not test metadata selection. Integration requires a production
selection check that fails on its predecessor when donor metadata is retained.
Synthetic 37/37 pass; 19/19 compiled omitted-term controls fail;
67 admission checks pass. Image receiver mutant fails 1/1; steering exclusion
differs on 14/30 fixtures. Private input comparisons exit 3 when inputs are absent,
after the self-contained proofs. No guest process runs.
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


class Tuning(ct.Structure):
    _fields_ = [(n, ct.c_float) for n in ('low_speed', 'high_speed', 'low_boost',
                'high_boost', 'max_bonus', 'ebrake_threshold', 'angle_threshold')]


class Friction(ct.Structure):
    _fields_ = [(n, ct.c_float) for n in ('slip_radians', 'ebrake', 'speed',
                'limit', 'attitude_component', 'blend')] + [('rear', ct.c_int)]


# Existing CalcYawControlLimit and YawFrictionBoost scalar equations at the
# pinned revision. The original car clone supplies donor values to this code.
ORIGINAL = r'''
#include "car_grip_steering.h"
#include <math.h>
int car_grip_steering_yaw_limit(const float *v, size_t n, float speed,
                               int transmission, float reference, float *out) {
    if (transmission && reference < 0) { *out = 0; return 0; }
    if (!transmission || n == 1) { *out = v[0]; return 0; }
    float fraction = reference == 0 ? 1 : fminf(fabsf(speed) / reference, 1);
    float pos = fraction * (n - 1);
    size_t i = (size_t)pos, j = i + 1 < n ? i + 1 : n - 1;
    *out = v[i] + (v[j] - v[i]) * (pos - i);
    return 0;
}
int car_grip_steering_yaw_friction(const CarGripYawFriction *s,
                                  const CarGripYawTuning *t, float *out) {
    float bonus = 0;
    if (!(s->ebrake > t->ebrake_threshold && fabsf(s->slip_radians) < t->angle_threshold)) {
        float gain = (t->high_boost - t->low_boost) *
            ((s->speed - t->low_speed) / (t->high_speed - t->low_speed)) + t->low_boost;
        bonus = fminf(t->max_bonus, gain * fabsf(s->slip_radians) * s->limit);
    }
    *out = s->rear ? 1 + (bonus + fabsf(s->attitude_component)) * s->blend : 1;
    return 0;
}
'''


def compile_lib(tmp, name, code):
    src, lib = tmp / (name + '.c'), tmp / (name + '.dylib')
    src.write_text(code)
    p = subprocess.run(['clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-I' + str(ROOT / 'runtime'), str(src),
                        '-o', str(lib)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    dll = ct.CDLL(str(lib))
    dll.car_grip_steering_yaw_limit.argtypes = [ct.POINTER(ct.c_float), ct.c_size_t,
                                               ct.c_float, ct.c_int, ct.c_float, ct.POINTER(ct.c_float)]
    dll.car_grip_steering_yaw_friction.argtypes = [ct.POINTER(Friction), ct.POINTER(Tuning),
                                                  ct.POINTER(ct.c_float)]
    return dll


def limit(dll, v=(0, 2, 4, 8), speed=15, transmission=1, reference=30):
    out = ct.c_float(123)
    values = (ct.c_float * len(v))(*v)
    rc = dll.car_grip_steering_yaw_limit(values, len(v), speed, transmission, reference, ct.byref(out))
    assert rc == 0
    return out.value


def friction(dll, s, t):
    out = ct.c_float(123)
    assert dll.car_grip_steering_yaw_friction(ct.byref(s), ct.byref(t), ct.byref(out)) == 0
    return out.value


def close(a, b):
    return math.isclose(a, b, rel_tol=3e-6, abs_tol=2e-6)


def synthetic(dll, original):
    # Piecewise-linear graph knots 0,10,20,30 m/s, ordinates 0,2,4,8.
    # Expected numbers are hand evaluations at knots, midpoints and boundaries.
    speeds = (-45, -30, -25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25, 30, 45)
    expected = (8, 8, 6, 4, 3, 2, 1, 0, 1, 2, 3, 4, 6, 8, 8)
    for speed, value in zip(speeds, expected):
        assert close(limit(dll, speed=speed), value)
        assert close(limit(original, speed=speed), value)
    one_sample = ((2,), 15, 1, 30, 2)
    two_samples = ((2, 4), 15, 1, 30, 3)
    three_samples = ((2, 4, 8), 15, 1, 30, 4)
    no_transmission = ((2, 4, 8), 15, 0, 30, 2)
    negative_reference = ((2, 4, 8), 15, 1, -1, 0)
    zero_reference = ((2, 4, 8), 15, 1, 0, 8)
    stationary_zero_reference = ((2, 4, 8), 0, 1, 0, 8)
    extra = [one_sample, two_samples, three_samples, no_transmission,
             negative_reference, zero_reference, stationary_zero_reference]
    for v, speed, trans, ref, expected_value in extra:
        assert close(limit(dll, v, speed, trans, ref), expected_value)
    # Nonzero low-speed/boost anchors expose omission of either endpoint.
    t = Tuning(10, 30, .2, .6, .35, .5, .25)
    ordinary = (Friction(.4, 0, 20, 1, .1, 1, 1), 1.26)
    reflected = (Friction(-.4, 0, 20, 1, -.1, 1, 1), 1.26)
    low_knot = (Friction(.4, 0, 10, 1, 0, 1, 1), 1.08)
    high_knot = (Friction(.4, 0, 30, 1, 0, 1, 1), 1.24)
    extrapolated = (Friction(.4, 0, 40, 1, 0, 1, 1), 1.32)
    negative_speed = (Friction(.4, 0, -10, 1, 0, 1, 1), .92)
    saturated = (Friction(1, 0, 30, 2, .1, 1, 1), 1.45)
    ebrake_bypass = (Friction(.2, .6, 20, 1, -.1, 1, 1), 1.1)
    ebrake_boundary = (Friction(.2, .5, 20, 1, .1, 1, 1), 1.18)
    angle_boundary = (Friction(.25, .6, 20, 1, .1, 1, 1), 1.2)
    zero_limit = (Friction(.4, 0, 20, 0, .1, 1, 1), 1.1)
    zero_blend = (Friction(.4, 0, 20, 1, .1, 0, 1), 1)
    partial_blend = (Friction(.4, 0, 20, 1, .1, .25, 1), 1.065)
    front_axle = (Friction(.4, 0, 20, 1, .1, 1, 0), 1)
    zero_slip = (Friction(0, 0, 20, 1, -.1, 1, 1), 1.1)
    fixtures = [ordinary, reflected, low_knot, high_knot, extrapolated,
                negative_speed, saturated, ebrake_bypass, ebrake_boundary,
                angle_boundary, zero_limit, zero_blend, partial_blend, front_axle, zero_slip]
    for s, expected_value in fixtures:
        assert close(friction(dll, s, t), expected_value), (tuple(getattr(s, n) for n, _ in s._fields_), expected_value)
        assert close(friction(original, s, t), expected_value)
    print('Synthetic: 22 yaw-limit + 15 friction goldens PASS; left/right and reverse-speed symmetry PASS')
    return t, fixtures


def mutants(tmp, code, dll, t, fixtures):
    # Each independent compiled control omits one supported term or boundary.
    sign_control = ('fabs((double)speed)', 'fmax(0, speed)', dict(speed=-15))
    position_control = ('fraction * (count - 1)', 'fmin(fraction, 0) * (count - 1)', dict())
    interpolation_control = ('(position - lower)', '0', dict())
    endpoint_control = ('fmin(1, fabs((double)speed) / reference_speed)',
                        'fmin(.5, fabs((double)speed) / reference_speed)', dict(speed=30))
    transmission_control = ('if (!transmission || count == 1)', 'if (count == 1)', dict(transmission=0))
    negative_control = ('if (transmission && reference_speed < 0) return 0;',
                        'if (transmission && reference_speed < 0) return result(values[0], out);',
                        dict(v=(1, 2, 4, 8), reference=-1))
    zero_control = ('reference_speed == 0 ? 1', 'reference_speed == 0 ? 0', dict(reference=0))
    limit_controls = [sign_control, position_control, interpolation_control,
                      endpoint_control, transmission_control, negative_control, zero_control]
    controls = 0
    for before, after, args in limit_controls:
        assert before in code
        broken = compile_lib(tmp, 'limit_mutant' + str(controls), code.replace(before, after, 1))
        assert not close(limit(broken, **args), limit(dll, **args))
        controls += 1
    friction_controls = [
        ('if (!in->rear)', 'if (0)', 13),
        ('in->ebrake > tuning->ebrake_threshold', 'in->ebrake >= tuning->ebrake_threshold', 8),
        ('< tuning->angle_threshold', '<= tuning->angle_threshold', 9),
        ('gain * fabs((double)in->slip_radians)', 'gain * 0', 0),
        (' * in->limit', ' * 1', 10),
        ('fmin(tuning->max_bonus, gain * fabs((double)in->slip_radians) * in->limit)',
         '(gain * fabs((double)in->slip_radians) * in->limit)', 6),
        ('fabs((double)in->attitude_component)', '0', 0),
        (' * in->blend', ' * 1', 12),
        ('((double)in->speed - tuning->low_speed)', '((double)in->speed)', 0),
        ('((double)tuning->high_speed - tuning->low_speed)', '((double)tuning->high_speed)', 0),
        ('double gain = tuning->low_boost +', 'double gain = 0 +', 0),
        ('((double)tuning->high_boost - tuning->low_boost)', '((double)tuning->high_boost)', 0),
    ]
    for before, after, index in friction_controls:
        assert before in code
        broken = compile_lib(tmp, 'friction_mutant' + str(controls), code.replace(before, after, 1))
        s, expected_value = fixtures[index]
        assert not close(friction(broken, s, t), expected_value), before
        controls += 1
    print(f'Compiled omitted-term/boundary controls: {controls}/{controls} fail designated goldens')


def admission(dll):
    count = 0
    for bad in (float('nan'), float('inf'), -float('inf')):
        for cls, target in ((Friction, 'in'), (Tuning, 'tuning')):
            for name, typ in cls._fields_:
                if typ != ct.c_float:
                    continue
                s, t = Friction(.4, 0, 20, 1, .1, 1, 1), Tuning(10, 30, .2, .6, .35, .5, .25)
                setattr(s if target == 'in' else t, name, bad)
                out = ct.c_float(123)
                assert dll.car_grip_steering_yaw_friction(ct.byref(s), ct.byref(t), ct.byref(out)) == -1 and out.value == 0
                count += 1
        for args in (([bad, 1], 2, 10, 1, 30), ([1, 2], 2, bad, 1, 30), ([1, 2], 2, 10, 1, bad)):
            v, n, speed, trans, ref = args
            out = ct.c_float(123)
            assert dll.car_grip_steering_yaw_limit((ct.c_float * len(v))(*v), n, speed, trans, ref, ct.byref(out)) == -1 and out.value == 0
            count += 1
    for n, trans in ((0, 1), (5, 1), (4, -1), (4, 2)):
        out = ct.c_float(123)
        assert dll.car_grip_steering_yaw_limit((ct.c_float * 4)(0, 1, 2, 3), n, 10, trans, 30, ct.byref(out)) == -1 and out.value == 0
        count += 1
    out = ct.c_float(123)
    assert dll.car_grip_steering_yaw_limit((ct.c_float * 4)(0, 1, 2, 3),
                4, 10, 1, -0.0, ct.byref(out)) == -1 and out.value == 0
    count += 1
    s, t = Friction(.4, 0, 20, 1, .1, 1, 1), Tuning(10, 30, .2, .6, .35, .5, .25)
    for pointer in ('in', 'tuning', 'out'):
        out = ct.c_float(123)
        assert dll.car_grip_steering_yaw_friction(None if pointer == 'in' else ct.byref(s),
                  None if pointer == 'tuning' else ct.byref(t), None if pointer == 'out' else ct.byref(out)) == -1
        if pointer != 'out': assert out.value == 0
        count += 1
    for name, bad in (('blend', -1), ('blend', 2), ('rear', -1), ('rear', 2)):
        copy = Friction.from_buffer_copy(s); setattr(copy, name, bad)
        out = ct.c_float(123)
        assert dll.car_grip_steering_yaw_friction(ct.byref(copy), ct.byref(t), ct.byref(out)) == -1 and out.value == 0
        count += 1
    for name, bad in (('high_speed', 10), ('high_speed', 0), ('max_bonus', -1), ('angle_threshold', -1)):
        copy = Tuning.from_buffer_copy(t); setattr(copy, name, bad)
        out = ct.c_float(123)
        assert dll.car_grip_steering_yaw_friction(ct.byref(s), ct.byref(copy), ct.byref(out)) == -1 and out.value == 0
        count += 1
    for which in ('values', 'out'):
        out = ct.c_float(123)
        assert dll.car_grip_steering_yaw_limit(None if which == 'values' else (ct.c_float * 1)(1),
                    1, 0, 0, 0, None if which == 'out' else ct.byref(out)) == -1
        if which != 'out': assert out.value == 0
        count += 1
    overflow = Friction(3e38, 0, 3e38, -3e38, 0, 1, 1)
    out = ct.c_float(123)
    assert dll.car_grip_steering_yaw_friction(ct.byref(overflow), ct.byref(t), ct.byref(out)) == -1 and out.value == 0
    count += 1
    print(f'Invalid/null/nonfinite/overflow admission: {count} PASS')


def private(dll, original):
    source = input_path('NFSC') / 'GLOBAL/attributes.bin'
    donor = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'GLOBAL/ATTRIBUTES.BIN'
    if not source.is_file() or not donor.is_file():
        print('SKIP: authored yaw comparisons require both pinned attribute vaults')
        return 3
    sb, db = source.read_bytes(), donor.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
    assert hashlib.sha256(db).hexdigest() == '3d0362f2c052b2f93576852f504cb7806b14eefbf83b8531d45dc1d430ec6337'
    sys.path.insert(0, str(ROOT / 'tools'))
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    from setup_kit import car_convert as cc
    from test_driver_seats import CASES
    a, d = cc.FitAttributes(sb), cc.FitAttributes(db)
    tiers = passes = failures = friction_failures = unchanged = 0
    # Initial matched-image tuning, independently checked by image_proof below.
    # At speed 12, the gain is 0.4; these demands stay below the bonus cap.
    t = Tuning(0, 30, 0, 1, .35, .5, 20 * math.pi / 180)
    for car, donor_car in CASES:
        refs = list(struct.iter_unpack('<3I', a.raw('pvehicle', cc.hash32(car.lower()), 'tires')))
        donor_refs = list(struct.iter_unpack('<3I', d.raw('pvehicle', cc.hash32(donor_car.lower()), 'tires')))
        for tier, (_, key, _) in enumerate(refs):
            v = struct.unpack('<4f', a.raw('tires', key, 'YAW_CONTROL'))
            dv = struct.unpack('<4f', d.raw('tires', donor_refs[tier][1], 'YAW_CONTROL'))
            for index, speed in enumerate((0, 5, 10, 15, 20, 25, 30, 45)):
                # Independent explicit knot/midpoint oracle, no helper-derived expected values.
                goldens = (v[0], (v[0]+v[1])/2, v[1], (v[1]+v[2])/2,
                           v[2], (v[2]+v[3])/2, v[3], v[3])
                golden = goldens[index]
                actual = limit(dll, v, speed)
                old = limit(original, dv, speed)
                assert close(actual, golden)
                assert close(limit(original, v, speed), golden); passes += 1
                failures += not close(old, golden)
                for sign in (-1, 1):
                    s = Friction(sign * .4, 0, 12, actual, sign * .1, .5, 1)
                    expected_value = 1.05 + .08 * golden
                    assert close(friction(dll, s, t), expected_value)
                    assert close(friction(original, s, t), expected_value); passes += 1
                    old_s = Friction.from_buffer_copy(s); old_s.limit = old
                    friction_failures += not close(friction(original, old_s, t), expected_value)
                    # Same donor inputs have the same existing stage result.
                    assert close(friction(dll, old_s, t), friction(original, old_s, t)); unchanged += 1
                assert close(limit(dll, dv, speed), old); unchanged += 1
            tiers += 1
    assert (tiers, passes, failures, friction_failures, unchanged) == (25, 600, 172, 344, 600)
    print(f'Equivalence evidence, {tiers} endpoints: different donor coefficients mismatch {failures}/200 limits, '
          f'{friction_failures}/400 friction cases; both equations pass {passes}/600 source cases; '
          f'donor stage equivalence {unchanged}/600; metadata selection untested')
    return 0


class Image:
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
        raise AssertionError('outside image')


def image_proof():
    source = ROOT.parent / 'decomp_carbon/input/NFSC.exe'
    donor = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent / 'Need For Speed Most Wanted Black Edition/speed.exe'))
    if not source.is_file() or not donor.is_file():
        print('SKIP: exclusion and tuning evidence requires pinned private images')
        return 3
    try:
        import capstone
    except ImportError:
        print('SKIP: static receiver proof requires the instruction decoder')
        return 3
    sb, db = source.read_bytes(), donor.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
    assert hashlib.sha256(db).hexdigest() == '80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c'
    s, d = Image(sb), Image(db)
    # Independently compare the on-image tuning cells used by the two consumers.
    sa = (0xAAA274, 0xA63818, 0xAAA278, 0xA63820, 0xA6381C, 0xA63824, 0xA63828, 0x9C9DFC)
    da = (0x9377F4, 0x8F8D38, 0x9377F8, 0x8F8D40, 0x8F8D3C, 0x8F8D44, 0x8F8D48, 0x89551C)
    assert all(s.read(a, 4) == d.read(b, 4) for a, b in zip(sa, da))
    sv = [struct.unpack('<f', s.read(a, 4))[0] for a in sa]
    assert all(close(a, b) for a, b in zip(sv, (0, 30, 0, 1, .35, .5, 20, math.pi/180)))
    dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)

    def receivers(code, start):
        result, receiver = [], None
        for ins in dis.disasm(code, start):
            if ins.mnemonic in ('mov', 'lea') and ins.op_str.startswith('ecx,'):
                receiver = ins.op_str
            if ins.mnemonic == 'call' and ins.op_str == '0x6a1900':
                result.append(receiver)
        return result

    expected = {
        0x6DFDE0: ('0xa639f0', '0xa63a04', '0xa63a2c', '0xa63a18'),
        0x6E0090: ('0xa63a40', '0xa63a2c', '0xa63a54'),
        0x6EF540: ('0xa639a0',),
    }
    sizes = {0x6DFDE0: 0x2AC, 0x6E0090: 0x89, 0x6EF540: 0x2D9}
    for start, addresses in expected.items():
        code = s.read(start, sizes[start])
        assert receivers(code, start) == ['ecx, ' + a for a in addresses]
    broken = bytearray(s.read(0x6DFDE0, sizes[0x6DFDE0]))
    # Change the first table receiver immediate, without altering instruction boundaries.
    broken[0x6DFDEE - 0x6DFDE0] ^= 1
    assert receivers(bytes(broken), 0x6DFDE0) != ['ecx, ' + a for a in expected[0x6DFDE0]]
    # The transmission's reference is the named speedometer maximum, not an
    # upgrade ordinal. Engine constructor fixes its table at this pinned image.
    assert struct.unpack('<I', d.read(0x8AB720 + 0x1C, 4))[0] == 0x692580
    # Excluded steering slice: wheel types 1/2 skip the tyre multiplier in the
    # source, while the original applies it. Explicit +/- half/full lock cases.
    differences = total = 0
    for multiplier in (.5, 1.5):
        for steer in (-1, -.5, 0, .5, 1):
            for kind in (0, 1, 2):
                maximum = 30 if kind != 2 else 45
                src = max(-45, min(45, maximum * steer * (multiplier if kind == 0 else 1)))
                old = max(-45, min(45, maximum * steer * multiplier))
                differences += not close(src, old)
                total += 1
    assert differences == 14 and total == 30
    print('Image evidence: 8/8 tuning cells equal; 8/8 steering-table calls use global tables; changed-receiver control fails 1/1')
    print('Excluded steering slice: 14/30 source-vs-original differences; no steering response imported')
    return 0


def main():
    scratch = ROOT / '.metal-cache/grip-g3'
    scratch.mkdir(parents=True, exist_ok=True)
    code = (ROOT / 'runtime/car_grip_steering.c').read_text()
    with tempfile.TemporaryDirectory(dir=scratch) as name:
        tmp = Path(name)
        dll = compile_lib(tmp, 'selected', code)
        original = compile_lib(tmp, 'original', ORIGINAL)
        t, fixtures = synthetic(dll, original)
        mutants(tmp, code, dll, t, fixtures)
        admission(dll)
        authored = private(dll, original)
        images = image_proof()
        return 3 if authored == 3 or images == 3 else 0


if __name__ == '__main__':
    sys.exit(main())
