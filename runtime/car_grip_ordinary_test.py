"""Ordinary source slices (006EE920, source decomp lines 13609..13786).

Independent hand goldens: demand 9/9, finish 18/18 PASS; 26/26 compiled
omitted-term mutants FAIL their designated golden (normal code PASS).
G2 circle oracle: 8/8 agree before ordinary postforce/feedback differences.
No assets required. Source launch constants decoded as binary32 at
009E4F48=2, 009E5948=.999999, 009E4E9C=1e-6, 009E4ED0=.2,
009E4F38=10, 009E4F28=6. Source 006DF6D0 omega/sign integration stays native.
The goldens are explicit numbers/rational calculations, never UUT evaluations.
"""
import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class Vector(C.Structure):
    _fields_ = [('lateral', C.c_float), ('longitudinal', C.c_float)]


class Demand(C.Structure):
    _fields_ = [('sliding', Vector)] + [(n, C.c_float) for n in (
        'prior_saturation', 'lateral_velocity', 'curve_force', 'lateral_gain',
        'wheel_torque', 'radius', 'angular_acceleration', 'road_acceleration', 'inertia')] + [('locked', C.c_int)]


class Ordinary(C.Structure):
    _fields_ = [('demand', Vector)] + [(n, C.c_float) for n in (
        'load', 'static_grip', 'grip_multiplier', 'speed_gain', 'static_gain',
        'speed', 'throttle', 'brake', 'ebrake')] + [('gear', C.c_int), ('locked', C.c_int)] + [
        (n, C.c_float) for n in ('slip', 'slip_threshold', 'surface_lateral', 'surface_longitudinal',
        'lateral_velocity', 'forward_velocity', 'slip_angle_turns', 'angle_correction', 'grip_scale',
        'wheel_torque', 'radius', 'omega', 'angular_acceleration', 'road_acceleration',
        'previous_feedback', 'rolling_friction', 'surface_rolling', 'inertia', 'dt')]


class Result(C.Structure):
    _fields_ = [('force', Vector)] + [(n, C.c_float) for n in (
        'cap_scale', 'saturation', 'stored_slip', 'feedback', 'angular_acceleration')] + [('launch', C.c_int)]


class Limit(C.Structure):
    _fields_ = [('demand', Vector)] + [(n, C.c_float) for n in (
        'load', 'static_grip', 'grip_multiplier', 'speed_gain')] + [('axes', Vector), ('ellipse', C.c_int)]


class LimitResult(C.Structure):
    _fields_ = [('force', Vector), ('saturation', C.c_float)]


def build(tmp, name, text=None, extra=()):
    src = tmp / (name + '.c')
    src.write_text(text if text is not None else (ROOT / 'runtime/car_grip_force.c').read_text())
    target = tmp / (name + '.dylib')
    p = subprocess.run(['clang', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-I' + str(ROOT / 'runtime'), str(src),
                        *map(str, extra), '-o', str(target)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2500:]
    lib = C.CDLL(str(target))
    for name, inp, out in (('ordinary_demand', Demand, Vector), ('ordinary_finish', Ordinary, Result),
                            ('limit', Limit, LimitResult)):
        f = getattr(lib, 'car_grip_force_' + name)
        f.argtypes = [C.POINTER(inp), C.POINTER(out)]
        f.restype = C.c_int
    return lib


def close(actual, expected):
    return len(actual) == len(expected) and all(math.isclose(a, b, rel_tol=4e-6, abs_tol=2e-6)
                                                for a, b in zip(actual, expected))


def demand_input(**kw):
    d = Demand(Vector(-3000, 4000), 1, 3, 200, 1.5, 100, .5, 3, 1, 10, 0)
    for n, v in kw.items(): setattr(d, n, v)
    return d


def finish_input(**kw):
    d = Ordinary(demand=Vector(3000, 4000), load=1000, static_grip=1.5,
        grip_multiplier=1, speed_gain=1, static_gain=1, speed=30, throttle=0,
        brake=0, ebrake=0, gear=2, locked=1, slip=4, slip_threshold=100,
        surface_lateral=1, surface_longitudinal=1, lateral_velocity=3, forward_velocity=1,
        slip_angle_turns=0, angle_correction=0, grip_scale=1, wheel_torque=500, radius=.5,
        omega=10, angular_acceleration=3, road_acceleration=2, previous_feedback=20,
        rolling_friction=2, surface_rolling=.5, inertia=10, dt=.1)
    for n, v in kw.items(): setattr(d, n, v)
    return d


def evaluate(lib, kind, value):
    if kind == 'demand':
        out = Vector(99, 98)
        rc = lib.car_grip_force_ordinary_demand(C.byref(value), C.byref(out))
        result = (out.lateral, out.longitudinal)
    else:
        out = Result()
        rc = lib.car_grip_force_ordinary_finish(C.byref(value), C.byref(out))
        result = (out.force.lateral, out.force.longitudinal, out.cap_scale, out.saturation,
                  out.stored_slip, out.feedback, out.angular_acceleration, out.launch)
    assert rc == 0, (kind, rc)
    return result


def goldens():
    demands = {
        'adhesion': (demand_input(), (-300, 210)),
        'reverse': (demand_input(lateral_velocity=-3, wheel_torque=-100,
                                angular_acceleration=-3, road_acceleration=-1), (300, -210)),
        'zero_lateral': (demand_input(lateral_velocity=0), (300, 210)),
        'positive_clamp': (demand_input(prior_saturation=.5, wheel_torque=500), (-4500, 1000)),
        'negative_clamp': (demand_input(sliding=Vector(3000, -4000), prior_saturation=.5,
                                       wheel_torque=-500), (4500, -1000)),
        'opposite_torque': (demand_input(prior_saturation=.5, wheel_torque=-500), (-4500, 4000)),
        'zero_torque': (demand_input(prior_saturation=.5, wheel_torque=0), (-4500, 4000)),
        'smaller_sliding': (demand_input(prior_saturation=.5, wheel_torque=4000), (-4500, 4000)),
        'locked': (demand_input(locked=1, wheel_torque=500), (-4500, 1000)),
    }
    finishes = {
        'circle': (finish_input(), (900, 1200, .3, .3, 4, 20, 0, 0)),
        'circle_boundary': (finish_input(demand=Vector(900, 1200)), (900, 1200, 1, 1, 4, 20, 0, 0)),
        'gains': (finish_input(static_gain=.5, speed_gain=.8, grip_multiplier=.5),
                  (180, 240, .06, .0054, 4, 20, 0, 0)),
        'launch': (finish_input(speed=1, throttle=1), (180, 1440, .06, .00108, 40, 20, 0, 1)),
        'launch_uncapped': (finish_input(speed=1, throttle=1, demand=Vector(60, 80)),
                            (60, 80, 1, 1, 40, 20, 0, 1)),
        'threshold': (finish_input(slip_threshold=4), (900, 1200, .3, .027, 4, 20, 0, 0)),
        'negative_slip': (finish_input(slip=-4, slip_threshold=4), (900, 1200, .3, .027, -4, 20, 0, 0)),
        'zero_slip': (finish_input(slip=0, slip_threshold=0), (900, 1200, .3, .3, 0, 20, 0, 0)),
        'zero_threshold': (finish_input(slip_threshold=0), (900, 1200, .3, 0, 4, 20, 0, 0)),
        'surfaces': (finish_input(surface_lateral=.5, surface_longitudinal=.25),
                     (450, 300, .3, .3, 4, 20, 0, 0)),
        'reverse_damping': (finish_input(demand=Vector(300, 400), forward_velocity=-10, lateral_velocity=.25),
                            (75, 400, 1, 1, 4, 20, 0, 0)),
        'angle': (finish_input(demand=Vector(300, 400), forward_velocity=10,
                               slip_angle_turns=.25, angle_correction=.2, grip_scale=.5),
                  (300, 280, 1, 1, 4, 20, 0, 0)),
        'feedback': (finish_input(slip_threshold=4, surface_lateral=.5, surface_longitudinal=.25,
                                 lateral_velocity=.5, locked=0, previous_feedback=100),
                     (225, 300, .3, .027, 4, 225, 18.92582, 0)),
        'angle_feedback': (finish_input(slip_threshold=4, surface_lateral=.5, surface_longitudinal=.25,
                                        forward_velocity=10, slip_angle_turns=.25, angle_correction=.2,
                                        grip_scale=.5, locked=0, previous_feedback=100),
                           (450, 120, .3, .027, 4, 270, 23.30432, 0)),
        # cap=.06; fraction=.06*.2*(4*.06^2/4)=.0000432.
        # filtered=(500-1440*.5+20)/2=-100; a0=(-100-10)/10-40*f/.05.
        'launch_feedback': (finish_input(speed=1, throttle=1, slip_threshold=4, locked=0),
                            (180, 1440, .06, .0000432, 40, -100,
                             (-11-40*.0000432/.05)*(1-.0000432)+4*.0000432, 1)),
        'adhesion_feedback': (finish_input(demand=Vector(60, 80), locked=0),
                              (60, 80, 1, 1, 4, 20, 4, 0)),
        'unloaded': (finish_input(load=-1), (0, 0, 0, 0, 4, 20, 0, 0)),
        'deadband': (finish_input(load=0, demand=Vector(.0005, 0)),
                     (.0005, 0, 1, 1, 4, 20, 0, 0)),
    }
    return demands, finishes


def controls(tmp, demands, finishes):
    text = (ROOT / 'runtime/car_grip_force.c').read_text()
    mutations = [
        ('selection', 's->prior_saturation < 1 || s->locked', 's->prior_saturation < 0 || s->locked', 'demand', 'positive_clamp'),
        ('torque_clamp', 'sliding = longitudinal;', 'sliding = s->sliding.longitudinal;', 'demand', 'positive_clamp'),
        ('curve_sign', 's->lateral_velocity > 0 ? -s->curve_force : s->curve_force', 's->curve_force', 'demand', 'adhesion'),
        ('lateral_gain', 'lateral *= s->lateral_gain;', 'lateral *= 1;', 'demand', 'adhesion'),
        ('inertia', '* s->inertia;', '* 0;', 'demand', 'adhesion'),
        ('static_gain', '(double)s->static_gain *', '1.0 *', 'finish', 'gains'),
        ('launch_cap', 'capacity *= 0.2f;', 'capacity *= 1;', 'finish', 'launch'),
        ('launch_slip', 'stored_slip *= 10.0;', 'stored_slip *= 1;', 'finish', 'launch'),
        ('launch_fraction', 'if (launch) saturation *= 0.2f;', 'if (launch) saturation *= 1;', 'finish', 'launch'),
        ('launch_long', 'if (launch) longitudinal *= 6.0;', 'if (launch) longitudinal *= 1;', 'finish', 'launch'),
        ('threshold_square', 'threshold *= cap_scale * cap_scale;', 'threshold *= cap_scale;', 'finish', 'threshold'),
        ('original_slip', 'saturation *= threshold / fabs((double)s->slip);', 'saturation *= threshold / fabs(stored_slip);', 'finish', 'launch'),
        ('surface_lat', 'lateral *= s->surface_lateral;', 'lateral *= 1;', 'finish', 'surfaces'),
        ('surface_long', 'longitudinal *= s->surface_longitudinal;', 'longitudinal *= 1;', 'finish', 'surfaces'),
        ('signed_speed', 'if (s->forward_velocity <= 1.0f)', 'if (fabs(s->forward_velocity) <= 1.0f)', 'finish', 'reverse_damping'),
        ('damping', 'lateral *= fmin(1.0, fabs((double)s->lateral_velocity));', 'lateral *= 1;', 'finish', 'reverse_damping'),
        ('angle_scale', '(double)s->angle_correction / s->grip_scale', '(double)s->angle_correction', 'finish', 'angle'),
        ('angle_sine', 'sin((double)s->slip_angle_turns * 6.2831855f)', '0', 'finish', 'angle'),
        ('feedback_force', 'longitudinal * s->radius + feedback', '0 + feedback', 'finish', 'feedback'),
        ('angle_feedback_order', 'longitudinal * s->radius + feedback',
         's->demand.longitudinal * cap_scale * s->surface_longitudinal * s->radius + feedback',
         'finish', 'angle_feedback'),
        ('feedback_filter', '+ feedback) * 0.5;', '+ feedback) * 1;', 'finish', 'feedback'),
        ('rolling', '(double)s->rolling_friction *', '0.0 *', 'finish', 'feedback'),
        ('feedback_slip', 'stored_slip * saturation /', '0.0 * saturation /', 'finish', 'feedback'),
        ('feedback_mix', 'acceleration += ((double)s->road_acceleration / s->radius - acceleration) * saturation;', 'acceleration += 0;', 'finish', 'feedback'),
        ('lock_feedback', 'acceleration = 0;', 'acceleration = s->angular_acceleration;', 'finish', 'circle'),
        ('cap_deadband', 'magnitude > capacity && magnitude > 0.001f', 'magnitude > capacity && magnitude > 0.0f', 'finish', 'deadband'),
    ]
    for name, old, new, kind, witness in mutations:
        assert text.count(old) == 1, (name, text.count(old))
        mutant = build(tmp, name, text.replace(old, new))
        value, expected = (demands if kind == 'demand' else finishes)[witness]
        assert not close(evaluate(mutant, kind, value), expected), (name, witness)
    print(f'Compiled omitted-term mutants: {len(mutations)}/{len(mutations)} FAIL designated golden; normal PASS')


def boundaries(lib):
    # Strict source comparisons, including stored-slip mutation when uncapped.
    cases = [({'speed': 2}, 0), ({'speed': math.nextafter(2, 0)}, 0),
             ({'speed': 1.9999998}, 1), ({'speed': -2}, 1),
             ({'throttle': .999999}, 0), ({'throttle': .9999995}, 1),
             ({'brake': 1e-6}, 0), ({'ebrake': 1e-6}, 0),
             ({'brake': .0000009}, 1), ({'ebrake': .0000009}, 1),
             ({'gear': 1}, 0), ({'gear': -1}, 0), ({'gear': 3}, 1)]
    for fields, expected in cases:
        data = dict(speed=1, throttle=1); data.update(fields)
        assert evaluate(lib, 'finish', finish_input(**data))[-1] == expected, fields
    # Equality of slip threshold and original slip does not reduce feedback.
    assert close(evaluate(lib, 'finish', finish_input(demand=Vector(60, 80), slip_threshold=4)),
                 (60, 80, 1, 1, 4, 20, 0, 0))
    assert close(evaluate(lib, 'finish', finish_input(demand=Vector(300, 400), grip_scale=0)),
                 (300, 400, 1, 1, 4, 20, 0, 0))
    print(f'Launch strict boundaries: {len(cases)}/{len(cases)} PASS; slip equality / zero-scale damping PASS')


def oracle(lib):
    for lat, lon, gain in ((3000, 4000, 1), (-3000, -4000, 1), (0, 4000, 1),
                           (900, 1200, 1), (3, 4, .2), (0, 0, 1), (3000, -4000, .5), (4000, 3000, 2)):
        f = finish_input(demand=Vector(lat, lon), static_gain=gain)
        expected = LimitResult()
        data = Limit(f.demand, f.load, f.static_grip, f.grip_multiplier, f.speed_gain, Vector(gain, 1), 0)
        assert lib.car_grip_force_limit(C.byref(data), C.byref(expected)) == 0
        actual = evaluate(lib, 'finish', f)
        assert close(actual[:3], (expected.force.lateral, expected.force.longitudinal, expected.saturation))
    print('Independent existing G2 circle oracle: 8/8 PASS')


def invalid(lib):
    count = 0
    for cls, base, fun, output in ((Demand, demand_input(), lib.car_grip_force_ordinary_demand, Vector),
                                  (Ordinary, finish_input(), lib.car_grip_force_ordinary_finish, Result)):
        names = [n for n, t in cls._fields_ if t is C.c_float]
        names += ['sliding.lateral', 'sliding.longitudinal'] if cls is Demand else ['demand.lateral', 'demand.longitudinal']
        for name in names:
            for value in (math.nan, math.inf, -math.inf):
                inp = cls.from_buffer_copy(bytes(base))
                target = inp
                if '.' in name: prefix, field = name.split('.'); target = getattr(inp, prefix)
                else: field = name
                setattr(target, field, value)
                out = output.from_buffer_copy(b'\x55' * C.sizeof(output))
                assert fun(C.byref(inp), C.byref(out)) == -1, name
                assert bytes(out) == bytes(output()), name
                count += 1
        for name in (('radius', 'inertia', 'curve_force', 'lateral_gain', 'prior_saturation') if cls is Demand else
                     ('radius', 'inertia', 'dt', 'static_grip', 'grip_multiplier', 'speed_gain', 'static_gain',
                      'slip_threshold', 'surface_lateral', 'surface_longitudinal', 'grip_scale',
                      'rolling_friction', 'surface_rolling', 'throttle', 'brake', 'ebrake')):
            inp = cls.from_buffer_copy(bytes(base)); setattr(inp, name, -1)
            out = output()
            assert fun(C.byref(inp), C.byref(out)) == -1, name
            assert bytes(out) == bytes(output()); count += 1
        for name in ('radius', 'inertia') + (() if cls is Demand else ('dt',)):
            inp = cls.from_buffer_copy(bytes(base)); setattr(inp, name, 0)
            assert fun(C.byref(inp), C.byref(output())) == -1; count += 1
        for flag in (-1, 2):
            inp = cls.from_buffer_copy(bytes(base)); inp.locked = flag
            assert fun(C.byref(inp), C.byref(output())) == -1; count += 1
        assert fun(None, C.byref(output())) == -1
        assert fun(C.byref(base), None) == -1; count += 2
    bad = finish_input(forward_velocity=2, grip_scale=0)
    out = Result()
    assert lib.car_grip_force_ordinary_finish(C.byref(bad), C.byref(out)) == -1
    for bad in (finish_input(speed=1, throttle=1, slip=3e38),
                finish_input(demand=Vector(1, 1), forward_velocity=2, grip_scale=1e-38,
                             slip_angle_turns=.25, angle_correction=3e38)):
        out = Result.from_buffer_copy(b'\x55' * C.sizeof(Result))
        assert lib.car_grip_force_ordinary_finish(C.byref(bad), C.byref(out)) == -1
        assert bytes(out) == bytes(Result()); count += 1
    print(f'Invalid/nonfinite/null/overflow: {count+1} rejected and cleared')


def main():
    with tempfile.TemporaryDirectory(prefix='grip-ordinary-') as directory:
        tmp = Path(directory); lib = build(tmp, 'normal')
        demands, finishes = goldens()
        for kind, fixtures in (('demand', demands), ('finish', finishes)):
            for name, (value, expected) in fixtures.items():
                actual = evaluate(lib, kind, value)
                assert close(actual, expected), (kind, name, actual, expected)
            print(f'Independent {kind} goldens: {len(fixtures)}/{len(fixtures)} PASS')
        boundaries(lib); oracle(lib); invalid(lib); controls(tmp, demands, finishes)
    print('PASS: ordinary source force slices')


if __name__ == '__main__': main()
