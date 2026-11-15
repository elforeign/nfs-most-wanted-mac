"""Dormant source-assist contract; no game or guest memory access.

Proof: production 136/136 goldens, 649/649 mode/factor/rotation fixtures and
79/79 invalid-input checks PASS; separately compiled omitted-term controls for
all SIX equations FAIL their designated golden (6/6). Boundary/operand/mode
controls FAIL 8/8. Six hashes, six pinned fragments and seven authored fields PASS.
Source: FUN_006F8FA0, decoded instruction fragments SHA-256 pinned below, image
baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3.
CCX nine-sample C51 and R32 stock final two zeros are retained, not padded or
clamped away. Numerical tolerance 4e-6 relative / 2e-6 absolute; no x87 byte claim.
Missing private inputs: run every self-contained proof, then exit 3.
"""
import ctypes as ct
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit.car_convert import FitAttributes, hash32
from setup_kit.input_paths import input_path


class Mode(ct.Structure):
    _fields_ = [(n, ct.c_int) for n in ('driving_style', 'drift_disabled',
                 'alternate_drift', 'canyon', 'player_selector')]


class Factors(ct.Structure):
    _fields_ = [(n, ct.c_int) for n in ('a', 'b', 'd')] + [
        (n, ct.c_float) for n in ('speed_threshold', 'canyon_yaw', 'alternate_yaw',
        'player_yaw', 'maintain', 'lateral', 'power_gain', 'power_exponent', 'rotation_gain')]


class Vector(ct.Structure):
    _fields_ = [(n, ct.c_float) for n in ('x', 'y', 'z')]


ROAD = Mode(0, 0, 0, 0, 0)
DRIFT = Mode(2, 0, 0, 0, 0)
ALTERNATE = Mode(0, 0, 1, 0, 0)
CANYON = Mode(2, 0, 0, 1, 0)
PLAYER = Mode(2, 0, 0, 0, 1)

CCX_YAW = (.2, .45, .55, .85, 1, 1.2, 1.4, 1.5, 1.65)
R32_YAW = (.2, .3, .4, .5, .6, .85, 1, 1, 0, 0)
CCX_ADDITIONAL = (5.5, 5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4, 3.5)


def array(values):
    return (ct.c_float * len(values))(*values)


def ptr(mode):
    return ct.byref(mode)


def close(actual, expected):
    return math.isclose(actual, expected, rel_tol=4e-6, abs_tol=2e-6)


def compile_lib(tmp, name, code):
    src, lib = tmp / (name + '.c'), tmp / (name + '.dylib')
    src.write_text(code)
    p = subprocess.run(['clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-I' + str(ROOT / 'runtime'), str(src),
                        '-o', str(lib)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    dll = ct.CDLL(str(lib))
    mode, fl, fp = ct.POINTER(Mode), ct.c_float, ct.POINTER(ct.c_float)
    signatures = {
        'yaw_control': [fp, ct.c_size_t, fl],
        'additional_yaw': [mode, fl, fl, fl, fp, ct.c_size_t],
        'buildup': [mode, ct.c_int, fl, fl, fl, fl, fl],
        'reduction': [mode, fl, fl, fl, fl],
        'history': [mode, fl, fl],
        'yaw_torque': [mode, fl, ct.c_int, fl, fl, fl, fl, fl, fl],
        'maintain_speed': [mode, fl, fl, fl, fl, fl],
        'lateral': [mode, fl, fl, fl, fl],
    }
    for n, args in signatures.items():
        f = getattr(dll, 'car_grip_assist_' + n)
        f.argtypes, f.restype = args + [fp], ct.c_int
    dll.car_grip_assist_factors.argtypes = [mode, fl, ct.POINTER(Factors)]
    dll.car_grip_assist_rotate.argtypes = [mode, fl, ct.POINTER(Vector), ct.POINTER(Vector)]
    return dll


def scalar(dll, name, args, rc=0):
    out = ct.c_float(123)
    actual_rc = getattr(dll, 'car_grip_assist_' + name)(*args, ptr(out))
    assert actual_rc == rc, (name, actual_rc, rc)
    if rc: assert out.value == 0, (name, out.value)
    return out.value


def goldens(dll):
    cases = []

    def check(label, name, args, expected):
        actual = scalar(dll, name, args)
        assert close(actual, expected), (label, actual, expected)
        cases.append((label, name, args, expected))

    # Authored C51 count selects bins, not interpolation or count-1 scaling.
    for car, values in [('ccx', CCX_YAW), ('r32', R32_YAW)]:
        for i, expected in enumerate(values):
            for sign in (-1, 1):
                check(f'{car}-sample-{i}-{sign}', 'yaw_control',
                      (array(values), len(values), sign * (i + .25) / len(values)), expected)
    check('yaw-count-term', 'yaw_control', (array(CCX_YAW), 9, .375), .85)
    check('yaw-zero', 'yaw_control', (array(CCX_YAW), 9, 0), .2)
    check('yaw-single', 'yaw_control', (array((3,)), 1, .9), 3)
    check('yaw-bin-below', 'yaw_control', (array(CCX_YAW), 9, .11111110), .2)
    check('yaw-bin-above', 'yaw_control', (array(CCX_YAW), 9, .11111112), .45)

    check('additional-term', 'additional_yaw', (ptr(DRIFT), 5, .01, 2, array((4, 9)), 2), 3)
    check('additional-square', 'additional_yaw', (ptr(DRIFT), 2.5, .01, 2, array((4, 9)), 2), 2.25)
    check('additional-clamp', 'additional_yaw', (ptr(DRIFT), 40, .5, 2, array((4, 9)), 2), 11)
    check('additional-player', 'additional_yaw', (ptr(PLAYER), 5, .01, 2, array((4, 9)), 2), 1.35)
    check('additional-alternate', 'additional_yaw', (ptr(ALTERNATE), 7.5, .01, 2, array((4, 9)), 2), 2.125)
    check('additional-canyon', 'additional_yaw', (ptr(CANYON), 50, .01, 2, array((4, 9)), 2), 4.1)
    check('additional-integer-below', 'additional_yaw', (ptr(DRIFT), 10, .01388888, 2, array((4, 9)), 2), 6)
    check('additional-integer-above', 'additional_yaw', (ptr(DRIFT), 10, .01388890, 2, array((4, 9)), 2), 11)
    check('additional-last-bin', 'additional_yaw', (ptr(DRIFT), 10, 2, 2, array((4, 9)), 2), 11)
    check('additional-authored-ccx', 'additional_yaw',
          (ptr(DRIFT), 5, .125, .45, array(CCX_ADDITIONAL), 10), 1.325)

    check('buildup-term', 'buildup', (ptr(DRIFT), 1, .5, .1, .2, 1.8, .1), .38)
    overshoot = scalar(dll, 'buildup', (ptr(DRIFT), 1, .5, .1, .99, 1.8, .1))
    check('buildup-overshoot', 'buildup', (ptr(DRIFT), 1, .5, .1, .99, 1.8, .1), 1.17)
    check('buildup-next-tick', 'buildup', (ptr(DRIFT), 1, .5, .1, overshoot, 1.8, .1), 1)
    check('buildup-old-equality', 'buildup', (ptr(DRIFT), 1, .5, .1, 1, 1.8, .1), 1)
    check('buildup-inactive', 'buildup', (ptr(DRIFT), 0, .5, .1, .2, 1.8, .1), 0)
    check('buildup-zero-dt', 'buildup', (ptr(DRIFT), 1, .5, .1, .2, 1.8, 0), .2)
    check('buildup-reflection', 'buildup', (ptr(DRIFT), 1, -.5, -.1, .2, 1.8, .1), .38)
    for steering, slip in [(0, .1), (.5, 0), (-.5, .1)]:
        check(f'buildup-reset-{steering}-{slip}', 'buildup', (ptr(DRIFT), 1, steering, slip, .2, 1.8, .1), 0)
        check(f'reduction-identity-{steering}-{slip}', 'reduction', (ptr(DRIFT), steering, slip, .2, 5), 1)
    check('reduction-term', 'reduction', (ptr(DRIFT), .5, .1, .25, 5), 4)
    check('reduction-zero', 'reduction', (ptr(DRIFT), .5, .1, 0, 5), 5)
    check('reduction-one', 'reduction', (ptr(DRIFT), .5, .1, 1, 5), 1)
    check('reduction-overshoot', 'reduction', (ptr(DRIFT), .5, .1, overshoot, 5), .32)
    check('reduction-negative', 'reduction', (ptr(DRIFT), .5, .1, 2, 5), -3)

    for speed, expected in [(0, 75), (59.99, 89.9975), (60, 90), (60.01, 100),
                            (119.99, 100), (120, 110), (120.01, 110.005)]:
        check(f'history-{speed}', 'history', (ptr(DRIFT), speed, 100), expected)
    check('history-zero', 'history', (ptr(DRIFT), 0, 0), 0)
    # No gear/speed test is allowed to erase an admitted ordinary-road C51 term.
    check('torque-road', 'yaw_torque', (ptr(ROAD), 31, 1, 0, .5, 100, .4, .85, 1), 17)
    check('torque-drift-envelope', 'yaw_torque', (ptr(DRIFT), 11, 1, 0, .5, 100, 0, 2, 4), 25)
    check('torque-alternate-envelope', 'yaw_torque', (ptr(ALTERNATE), 31, 1, 0, .5, 100, .4, 2, 4), 10)
    check('torque-negative-divisor', 'yaw_torque', (ptr(DRIFT), 11, 1, 0, .5, 100, 1, 2, -4), -25)
    for mode, threshold in [(ROAD, 30), (DRIFT, 10)]:
        for speed, throttle, gear, expected in [(threshold, 0, 1, 0), (threshold+.01, 0, 1, 50),
                 (0, 1e-6, 1, 0), (0, 1.01e-6, 1, 50), (100, 1, 0, 0), (100, 0, -1, 50)]:
            check(f'torque-gate-{threshold}-{speed}-{throttle}-{gear}', 'yaw_torque',
                  (ptr(mode), speed, gear, throttle, .5, 100, 1, 2, 2), expected)

    check('maintain-term', 'maintain_speed', (ptr(DRIFT), 10, 20, .5, 0, 1100), 7700)
    check('maintain-alternate', 'maintain_speed', (ptr(ALTERNATE), 10, 20, .5, 0, 1100), 3080)
    check('maintain-canyon', 'maintain_speed', (ptr(CANYON), 10, 20, .5, 0, 1100), 10780)
    check('maintain-ebrake-equality', 'maintain_speed', (ptr(DRIFT), 10, 20, .5, .5, 1100), 7700)
    for speed, throttle, ebrake in [(20, .5, 0), (20.01, .5, 0), (10, 0, 0),
                                  (10, 1e-6, 0), (10, .5, .500001)]:
        check(f'maintain-gate-{speed}-{throttle}-{ebrake}', 'maintain_speed',
              (ptr(DRIFT), speed, 20, throttle, ebrake, 1100), 0)
    check('maintain-throttle-above', 'maintain_speed', (ptr(DRIFT), 10, 20, 2e-6, 0, 1100), .0308)

    # Independent evaluations at a=0,1 and a=.25/.125 pin BOTH pow operands.
    check('lateral-term', 'lateral', (ptr(DRIFT), 10, .1, 0, 45), -3780)
    check('lateral-full-throttle', 'lateral', (ptr(DRIFT), 10, .1, 1, 45), -1530)
    check('lateral-reflection', 'lateral', (ptr(DRIFT), 10, -.1, 0, 45), 3780)
    check('lateral-speed-square', 'lateral', (ptr(DRIFT), 20, .1, 0, 45), -15120)
    check('lateral-alternate', 'lateral', (ptr(ALTERNATE), 10, .1, 0, 45), -1512)
    check('lateral-canyon', 'lateral', (ptr(CANYON), 10, .1, 0, 45), -1809)
    check('lateral-power', 'lateral', (ptr(DRIFT), 10, .1, .125, 45),
          -45 * .775 * (21 - 20 * .125**.3) * .1 * 100)
    check('lateral-canyon-power', 'lateral', (ptr(CANYON), 10, .1, .25, 45), -2613.375)
    for slip in (0, 1e-6, -1e-6):
        check(f'lateral-slip-gate-{slip}', 'lateral', (ptr(DRIFT), 10, slip, 0, 45), 0)
    check('lateral-slip-above', 'lateral', (ptr(DRIFT), 10, 2e-6, 0, 45), -.0756)
    check('lateral-stationary', 'lateral', (ptr(DRIFT), 0, .1, 0, 45), 0)

    # Mode exclusions, even when non-road coefficients/flags are non-identity.
    for mode in [ROAD, Mode(1, 0, 0, 1, 1), Mode(2, 1, 0, 1, 2)]:
        check('road-additional', 'additional_yaw', (ptr(mode), 31, .1, .85, None, 0), .85)
        check('road-buildup', 'buildup', (ptr(mode), 1, .5, .1, .9, 9, .1), 0)
        check('road-reduction', 'reduction', (ptr(mode), .5, .1, .9, 5), 1)
        check('road-history', 'history', (ptr(mode), 31, 100), 100)
        check('road-maintain', 'maintain_speed', (ptr(mode), 31, 100, .5, 0, 9000), 0)
        check('road-lateral', 'lateral', (ptr(mode), 31, .1, 0, 9000), 0)
    print(f'Equation/table/boundary goldens: {len(cases)}/{len(cases)} PASS')
    return cases


def mode_and_rotation(dll):
    checks = 0
    # Exhaustive style x disable x alternate x canyon x player selector flags.
    for style in (0, 1, 2, 3):
        for disabled in (0, 1):
            for alternate in (0, 1):
                for canyon in (0, 1):
                    for player in (0, 1, 2, 3):
                        for speed in (0, 7.5, 15, 100, 150):
                            m = Mode(style, disabled, alternate, canyon, player)
                            f = Factors()
                            assert dll.car_grip_assist_factors(ptr(m), speed, ptr(f)) == 0
                            a = style == 2 and not disabled
                            b = not a and alternate
                            assert (f.a, f.b, f.d) == (a, b, a or b)
                            expected = (10 if a else 30, .7*(1-.5*min(speed/100, 1)) if canyon else 1,
                                        min(speed/15, 1) if b else 1, .45 if player in (1, 2) else 1,
                                        1.4 if canyon else .4 if b else 1,
                                        .05 if canyon else .4 if b else 1,
                                        200 if canyon else 20, .5 if canyon else .3, .8 if canyon else .6)
                            assert all(close(getattr(f, n), v) for (n, _), v in zip(f._fields_[3:], expected))
                            checks += 1
    # Quarter-turn fixtures independently establish sign, turns and WORLD Y.
    for mode, gain in [(DRIFT, .6), (CANYON, .8)]:
        for sign in (-1, 1):
            for v, expected in [(Vector(1, 2, 0), (0, 2, -sign)),
                                (Vector(0, 2, 1), (sign, 2, 0))]:
                out = Vector()
                assert dll.car_grip_assist_rotate(ptr(mode), sign * .25/gain, ptr(v), ptr(out)) == 0
                assert all(close(getattr(out, n), x) for n, x in zip(('x', 'y', 'z'), expected))
                checks += 1
    out, v = Vector(), Vector(1, 2, 3)
    assert dll.car_grip_assist_rotate(ptr(ROAD), .25, ptr(v), ptr(out)) == 0
    assert (out.x, out.y, out.z) == (1, 2, 3)
    print(f'Mode/factor/rotation fixtures: {checks+1}/{checks+1} PASS')


def mutants(tmp, code, cases):
    # One omitted term per field; each compiled mutation must FAIL an existing
    # independently expected golden, never just differ from the current helper.
    controls = [
        ('C51-count', 'count * fabs((double)slip)', 'fabs((double)slip)', 'yaw-count-term'),
        ('additional-term', 'fraction * fraction * values[index]', 'fraction * fraction * 0 * values[index]', 'additional-term'),
        ('buildup-term', 'old + (double)rate * dt', 'old + 0 * (double)rate * dt', 'buildup-term'),
        ('reduction-term', '(1 - (double)buildup) * coefficient + buildup',
         '0 * (1 - (double)buildup) * coefficient + buildup', 'reduction-term'),
        ('maintain-term', '(1.2 * history - speed)', '(0 * 1.2 * history - speed)', 'maintain-term'),
        ('lateral-term', '(f.power_gain * (1 - power) + 1)', '(0 * f.power_gain * (1 - power) + 1)', 'lateral-term'),
    ]
    # Additional controls expose precisely the source boundaries and operands.
    controls.extend([
        ('overshoot', 'old >= 1 ? 1 : old + (double)rate * dt',
         'fmin(1, old + (double)rate * dt)', 'buildup-overshoot'),
        ('power-operands', 'pow(throttle, f.power_exponent)', 'pow(f.power_exponent, throttle)', 'lateral-power'),
        ('history-equality', 'speed <= .6 * history', 'speed < .6 * history', 'history-60'),
        ('additional-square', 'fraction * fraction * values[index]', 'fraction * values[index]', 'additional-square'),
        ('ebrake-equality', 'ebrake > .5f', 'ebrake >= .5f', 'maintain-ebrake-equality'),
        ('throttle-equality', 'throttle > 1e-6f', 'throttle >= 1e-6f', 'torque-gate-30-0-1e-06-1'),
    ])
    for i, (name, before, after, label) in enumerate(controls):
        assert code.count(before) == 1, (name, code.count(before))
        broken = compile_lib(tmp, 'mutant' + str(i), code.replace(before, after, 1))
        _, fn, args, expected = next(c for c in cases if c[0] == label)
        actual = scalar(broken, fn, args)
        assert not close(actual, expected), (name, 'MUTANT SURVIVED', actual, expected)
    # Orthogonal rotation/mode controls use the same independent fixture runner.
    for name, before, after in [('rotation', '-s * in->x + c * in->z', 's * in->x + c * in->z'),
                                 ('mode', 'mode->driving_style == 2', 'mode->driving_style == 1')]:
        broken = compile_lib(tmp, name, code.replace(before, after, 1))
        try:
            mode_and_rotation(broken)
        except AssertionError:
            pass
        else:
            raise AssertionError(name + ' MUTANT SURVIVED')
    print(f'Compiled controls: 6/6 omitted-equation mutants FAIL; {len(controls)-6+2}/{len(controls)-6+2} boundary/operand/mode controls FAIL')


def invalid(dll, cases):
    checks = 0
    # Every scalar entry point rejects null output and nonfinite scalar inputs.
    for name in ('yaw_control', 'additional_yaw', 'buildup', 'reduction', 'history',
                 'yaw_torque', 'maintain_speed', 'lateral'):
        args = next(args for _, fn, args, _ in cases if fn == name)
        assert getattr(dll, 'car_grip_assist_' + name)(*args, None) == -1
        checks += 1
        for i, v in enumerate(args):
            if not isinstance(v, float): continue
            for bad in (math.nan, math.inf, -math.inf):
                bad_args = list(args); bad_args[i] = bad
                scalar(dll, name, bad_args, -1); checks += 1
    for values, count, slip in [(None, 1, 0), (array((1,)), 0, 0),
                                (array((1,)), 65536, 0), (array((math.nan,)), 1, 0),
                                (array((1,)), 1, 1), (array((1,)), 1, -1)]:
        scalar(dll, 'yaw_control', (values, count, slip), -1); checks += 1
    def reject(name, args):
        nonlocal checks
        scalar(dll, name, args, -1); checks += 1

    reject('buildup', (ptr(DRIFT), 2, .5, .1, 0, 1, .1))
    reject('buildup', (ptr(DRIFT), 1, .5, .1, 0, -1, .1))
    reject('buildup', (ptr(DRIFT), 1, .5, .1, 0, 1, -1))
    reject('additional_yaw', (ptr(DRIFT), 10, 1e10, 1, array((1,)), 1))
    reject('additional_yaw', (ptr(DRIFT), 10, 0, 1, None, 0))
    reject('lateral', (ptr(DRIFT), 10, .1, -.1, 1))
    reject('lateral', (ptr(DRIFT), 10, .1, 1.1, 1))
    reject('lateral', (ptr(DRIFT), -1, .1, .5, 1))
    reject('lateral', (ptr(DRIFT), 1e30, .1, .5, 1e30))
    reject('yaw_torque', (ptr(DRIFT), 11, 1, 0, .5, 100, 1, 1, 0))
    reject('yaw_torque', (ptr(DRIFT), 11, 1, 0, .5, 1e30, 1, 1e30, 1))
    for mode in [None, ptr(Mode(-1, 0, 0, 0, 0)), ptr(Mode(0, 2, 0, 0, 0)),
                 ptr(Mode(0, 0, 2, 0, 0)), ptr(Mode(0, 0, 0, 2, 0)), ptr(Mode(0, 0, 0, 0, -1))]:
        out = Factors(); ct.memset(ptr(out), 0x7f, ct.sizeof(out))
        assert dll.car_grip_assist_factors(mode, 0, ptr(out)) == -1
        assert bytes(out) == bytes(ct.sizeof(out)); checks += 1
    assert dll.car_grip_assist_factors(ptr(ROAD), 0, None) == -1; checks += 1
    for v in [None, ptr(Vector(math.nan, 0, 1)), ptr(Vector(0, math.inf, 1))]:
        out = Vector(1, 2, 3)
        assert dll.car_grip_assist_rotate(ptr(DRIFT), 0, v, ptr(out)) == -1
        assert (out.x, out.y, out.z) == (0, 0, 0); checks += 1
    assert dll.car_grip_assist_rotate(ptr(DRIFT), 0, ptr(Vector()), None) == -1; checks += 1
    # Inactive torque does not divide by zero.
    assert scalar(dll, 'yaw_torque', (ptr(ROAD), 100, 0, 1, .5, 100, 1, 2, 0)) == 0; checks += 1
    print(f'Invalid/null/nonfinite/overflow checks: {checks}/{checks} PASS')


class Image:
    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        n, opt = struct.unpack_from('<H12xH', data, pe+6)
        self.base = struct.unpack_from('<I', data, pe+52)[0]
        self.sections = [struct.unpack_from('<8sIIII', data, pe+24+opt+40*i) for i in range(n)]

    def read(self, address, count):
        for _, virtual, rva, raw, offset in self.sections:
            relative = address-self.base-rva
            if 0 <= relative and relative+count <= virtual:
                size = min(count, max(0, raw-relative))
                return self.data[offset+relative:offset+relative+size].ljust(count, b'\0')
        raise AssertionError('outside pinned image')


def source_proof():
    fragments = []

    def pin(address, hex_bytes, digest):
        data = bytes.fromhex(hex_bytes)
        assert hashlib.sha256(data).hexdigest() == digest
        fragments.append((address, data))

    # Decoded source fragments, not hashes of our implementation. These pin
    # the load/multiply order behind the six independent scalar fixtures.
    pin(0x6F95C6, '85c08944243cdb44243c7d06d805ec2e9c00d84c2420e8df541800',
        'f3b757014c95be926419eef3e169261be9b8cf953208910e61c05d43a06f0a6d')
    pin(0x6F9740, '750a6a04e8c7ecf7ff83c40483ff01d944242cd808d84c2440d84c2474d844243c740583ff027506d80d50569e00',
        'f8b1c228504d3ad9821b72e464ef00c62c877e8f00c844c9703d4c10dd4de8b6')
    pin(0x6F9436, '8b8d90000000d9417cd88bc0000000d885b4010000d99db4010000',
        'f681ef1a3c13d57d6de5a25ec57847e8892e0b4904eccc9f4ce892c62ef79cc9')
    pin(0x6F9784, 'd90578249c00d8a5b4010000d84c2424d885b4010000def9',
        'a58bd3e092daaa6a72ee9a1d274000bf69f7b833f39f2dd6f28c72ea2706bfd4')
    pin(0x6F9BE8, 'd985a40100008a44241284c0d80d404f9e0089742444897c2448d864241c894c244c7408d905e4509e00eb168a44241184c07408d905dc509e00eb06d90578249c008b8590000000d8888c0000008d5424445251d84b4c8d8c2480000000d8c9d91c2451ddd8',
        'e75191541988cd071ad3724335f15650c6d5f5719fd02a5c6bf285b82dde7985')
    pin(0x6F9D21, 'd9434cd9442424e8154e1800d82d78249c00b910000000be80789d00d84c24408dbc2484000000f3a5d80578249c00d9434cd80d244f9e00d805dc509e00dec9',
        'b8609c9893d83eb74dcf66fee77afda68df2e7ed5909d3e884453746a236ed37')
    header = (ROOT/'runtime/car_grip_assist.h').read_text()
    names = ('DRIFT_YAW_CONTROL', 'DRIFT_ADDITIONAL_YAW_CONTROL', 'DRIFT_COUNTER_STEER_BUILDUP',
             'DRIFT_COUNTER_STEER_REDUCTION', 'DRIFT_MAINTAIN_SPEED_FORCE', 'DRIFT_LATERAL_FORCE')
    hashes = (0xC51EB1A1, 0xF4BBE085, 0x7DB55397, 0x0D255C03, 0xC141848A, 0xECEFA5B3)
    for name, expected in zip(names, hashes):
        assert hash32(name) == expected
        assert int(re.search(r'#define CAR_GRIP_' + name + r'\s+(0x[0-9A-F]+)u', header)[1], 16) == expected
    # Pin packed binary32 authored fixtures separately from any live parser.
    source = ROOT.parent/'decomp_carbon/input/NFSC.exe'
    attrib = input_path('NFSC')/'GLOBAL/attributes.bin'
    if not source.is_file() or not attrib.is_file():
        print('SKIP: private image/attribute validation; 6 hashes + 6 pinned fragments PASS')
        return 3
    sb, ab = source.read_bytes(), attrib.read_bytes()
    assert hashlib.sha256(sb).hexdigest() == 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
    assert hashlib.sha256(ab).hexdigest() == 'b1c9ba06e476519db69ef3878a6b2796a0e7be90708355527601f3d58c129a4b'
    image = Image(sb)
    for address, data in fragments: assert image.read(address, len(data)) == data
    a = FitAttributes(ab)
    for car, expected in [('ccx', CCX_YAW), ('r32', R32_YAW)]:
        refs = list(struct.iter_unpack('<3I', a.raw('pvehicle', hash32(car), 'tires')))
        raw = a.raw('tires', refs[0][1], 'DRIFT_YAW_CONTROL')
        assert raw == struct.pack('<' + 'f'*len(expected), *expected)
    ccx_key = struct.unpack('<3I', a.raw('pvehicle', hash32('ccx'), 'tires'))[1]
    authored = [('DRIFT_ADDITIONAL_YAW_CONTROL', CCX_ADDITIONAL),
                ('DRIFT_COUNTER_STEER_BUILDUP', (1.8,)), ('DRIFT_COUNTER_STEER_REDUCTION', (5,)),
                ('DRIFT_MAINTAIN_SPEED_FORCE', (1100,)), ('DRIFT_LATERAL_FORCE', (45,))]
    for name, expected in authored:
        assert a.raw('tires', ccx_key, name) == struct.pack('<'+'f'*len(expected), *expected)
    print('Pinned source: 6/6 hashes, 6/6 decoded fragments, 7/7 authored fields PASS')
    return 0


def main():
    code = (ROOT/'runtime/car_grip_assist.c').read_text()
    with tempfile.TemporaryDirectory(prefix='grip-assist-') as tmp:
        tmp = Path(tmp)
        dll = compile_lib(tmp, 'assist', code)
        cases = goldens(dll)
        mode_and_rotation(dll)
        invalid(dll, cases)
        mutants(tmp, code, cases)
    return source_proof()


if __name__ == '__main__':
    raise SystemExit(main())
