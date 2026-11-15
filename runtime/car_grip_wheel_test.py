"""car_grip_wheel.h ordinary wheel seam: 12/12 decoded hand goldens PASS;
10/10 compiled omitted-overlay/selection mutants FAIL their designated golden.
G3 yaw oracles: 4 counts + one friction boost PASS; both remain uncalled by
production shim. G2 sliding speed ramp and circle saturation checked separately.
Overlay byte preservation covers STEERING/YAW_SPEED, counts and in-place use.
Source equations: 006EE920 ordinary demand/cap/feedback; 006DF480 lateral
curve construction; 006EFAC0 yaw table interpolation. No guest memory or assets.
"""
import ctypes as C
import math
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from car_grip_ordinary_test import (ROOT, Vector, Demand, Ordinary, Result, build, close,
                                    demand_input, finish_input)


class Sliding(C.Structure):
    _fields_ = [(n, C.c_float) for n in ('load', 'lateral_velocity', 'forward_velocity',
                'wheel_slip', 'speed', 'dynamic_grip', 'grip_multiplier')] + [
                (n, C.c_int) for n in ('speed_sensitive', 'forward_rotation', 'locked')]


class Overlay(C.Structure):
    _fields_ = [(n, C.c_float * 2) for n in ('dynamic_grip', 'static_grip', 'grip_scale')] + [
        ('yaw_control', C.c_float * 4), ('yaw_count', C.c_size_t)]


class Tyre(C.Structure):
    _fields_ = [('grip', Overlay), ('steering', C.c_float * 2), ('yaw_speed', C.c_float)]


class Wheel(C.Structure):
    _fields_ = [('axle', C.c_uint), ('sliding', Sliding), ('demand', Demand),
                ('finish', Ordinary), ('curve_force_per_grip', C.c_float)]


class Tuning(C.Structure):
    _fields_ = [(n, C.c_float) for n in ('low_speed', 'high_speed', 'low_boost', 'high_boost',
                'max_bonus', 'ebrake_threshold', 'angle_threshold')]


class Friction(C.Structure):
    _fields_ = [(n, C.c_float) for n in ('slip_radians', 'ebrake', 'speed', 'limit',
                'attitude_component', 'blend')] + [('rear', C.c_int)]


def compile_wheel(tmp, name, text=None):
    src = tmp / (name + '_wheel.c')
    src.write_text(text if text is not None else (ROOT / 'runtime/car_grip_wheel.c').read_text())
    lib = build(tmp, name, extra=(src, ROOT / 'runtime/car_grip_steering.c'))
    lib.car_grip_wheel_overlay.argtypes = [C.POINTER(Tyre), C.POINTER(Overlay), C.POINTER(Tyre)]
    lib.car_grip_wheel_ordinary.argtypes = [C.POINTER(Overlay), C.POINTER(Wheel), C.POINTER(Result)]
    lib.car_grip_force_sliding.argtypes = [C.POINTER(Sliding), C.POINTER(Vector)]
    lib.car_grip_steering_yaw_limit.argtypes = [C.POINTER(C.c_float), C.c_size_t, C.c_float,
                                              C.c_int, C.c_float, C.POINTER(C.c_float)]
    lib.car_grip_steering_yaw_friction.argtypes = [C.POINTER(Friction), C.POINTER(Tuning), C.POINTER(C.c_float)]
    return lib


def overlay():
    return Overlay((C.c_float * 2)(1.3, 1.5), (C.c_float * 2)(1.5, 2),
                   (C.c_float * 2)(.5, 2), (C.c_float * 4)(0, 2, 4, 8), 4)


def wheel(axle=0, prior=.5, locked=0, speed=30, sensitive=0, gain=1):
    s = Sliding(1000, 3, 1, 4, speed, 9, 1, sensitive, 1, 0)
    d = demand_input(prior_saturation=prior, lateral_gain=1, wheel_torque=500,
                     road_acceleration=2, locked=locked)
    f = finish_input(locked=locked, speed=speed, speed_gain=gain)
    return Wheel(axle, s, d, f, 200)


def run(lib, source, value):
    before = bytes(source), bytes(value)
    out = Result()
    assert lib.car_grip_wheel_ordinary(C.byref(source), C.byref(value), C.byref(out)) == 0
    assert before == (bytes(source), bytes(value)), 'inputs mutated'
    return (out.force.lateral, out.force.longitudinal, out.cap_scale, out.saturation,
            out.stored_slip, out.feedback, out.angular_acceleration, out.launch)


def fixtures():
    # Dynamic force=(-3,4)/5 * mu*1000, then same-sign Fx <= torque/r=1000.
    cases = {}
    cases['dynamic_front'] = (overlay(), wheel(), (-780, 1000, 1, 1, 4, 20, 4, 0))
    cases['dynamic_rear'] = (overlay(), wheel(axle=1), (-900, 1000, 1, 1, 4, 20, 4, 0))
    cases['static_front'] = (overlay(), wheel(prior=1), (-100, 990, 1, 1, 4, 20, 4, 0))
    cases['static_rear'] = (overlay(), wheel(axle=1, prior=1), (-400, 990, 1, 1, 4, 20, 4, 0))
    cases['speed_ramp'] = (overlay(), wheel(speed=13.4109, sensitive=1, gain=.85),
                            (-663, 884, 1, 1, 4, 20, 4, 0))
    cases['new_lock'] = (overlay(), wheel(speed=13.4109, sensitive=1, gain=.85, locked=1),
                          (-663, 884, 1, 1, 4, 20, 0, 0))
    locked = wheel(locked=1); locked.sliding.locked = 1
    cases['old_lock'] = (overlay(), locked, (-780, 1000, 1, 1, 4, 20, 0, 0))
    source = overlay(); source.dynamic_grip[0] = 3
    data = wheel(locked=1); data.demand.wheel_torque = data.finish.wheel_torque = 2000
    cases['static_cap'] = (source, data, (-900, 1200, .5, .5, 4, 20, 0, 0))
    for axle, expected in ((0, (-100, 1030, 1, 1, 4, 20, 4, 0)),
                            (1, (-400, 1030, 1, 1, 4, 20, 4, 0))):
        data = wheel(axle=axle, prior=1); data.sliding.forward_velocity = data.finish.forward_velocity = 10
        data.finish.slip_angle_turns = .25; data.finish.angle_correction = .2
        cases['angle_' + str(axle)] = (overlay(), data, expected)
    source = overlay(); source.dynamic_grip[0] = 0
    cases['zero_dynamic'] = (source, wheel(), (0, 0, 1, 1, 4, 20, 4, 0))
    data = wheel(); data.demand.lateral_gain = 1.26
    cases['native_yaw_gain'] = (overlay(), data, (-982.8, 1000, 1, 1, 4, 20, 4, 0))
    return cases


def overlay_check(lib):
    native = Tyre(overlay(), (C.c_float * 2)(3, 4), 5)
    source = overlay(); source.yaw_count = 2; source.yaw_control[2] = math.nan
    out = Tyre()
    before = bytes(native), bytes(source)
    assert lib.car_grip_wheel_overlay(C.byref(native), C.byref(source), C.byref(out)) == 0
    assert before == (bytes(native), bytes(source))
    expected = (1.3, 1.5, 1.5, 2, .5, 2, 0, 2, 0, 0, 2, 3, 4, 5)
    actual = (*out.grip.dynamic_grip, *out.grip.static_grip, *out.grip.grip_scale,
              *out.grip.yaw_control, out.grip.yaw_count, *out.steering, out.yaw_speed)
    ok = close(actual, expected)
    # In-place use is required for owned native/source shadows, including alias
    # of source with the destination grip member.
    assert lib.car_grip_wheel_overlay(C.byref(out), C.byref(out.grip), C.byref(out)) == 0
    return ok


def yaw_oracle(lib):
    out = C.c_float()
    for count, expected in ((1, 0), (2, 1), (3, 2), (4, 3)):
        source = overlay(); source.yaw_count = count
        assert lib.car_grip_steering_yaw_limit(source.yaw_control, source.yaw_count,
                                              15, 1, 30, C.byref(out)) == 0
        assert math.isclose(out.value, expected)
    # g=.2, bonus=.2*.2*3=.12, result=1+(.4+.12)*.5=1.26.
    friction = Friction(.2, 0, 15, 3, -.4, .5, 1)
    tuning = Tuning(0, 30, .1, .3, 10, .5, 1)
    assert lib.car_grip_steering_yaw_friction(C.byref(friction), C.byref(tuning), C.byref(out)) == 0
    assert math.isclose(out.value, 1.26, rel_tol=1e-6)
    print('G3 independent yaw interpolation/friction oracles: 5/5 PASS')


def speed_oracle(lib):
    # Sweep the G2 speed ramp boundaries with front static capacity high enough
    # to keep pure dynamic force; includes pre-lock / post-lock independence.
    count = 0
    for speed in (0, 13.4109, 17.8812, 22.3515, 30):
        for sensitive, rotation, old_lock in ((0, 1, 0), (1, 1, 0), (1, 0, 0), (1, 1, 1)):
            g = 1
            if sensitive and rotation and not old_lock:
                # Independently evaluate linear ramp between 30 and 50 mph.
                g = .85 + .15 * min(1, max(0, (speed - 13.4109) / (22.3515 - 13.4109)))
            data = wheel(speed=speed, sensitive=sensitive, gain=g, locked=1)
            data.sliding.forward_rotation = rotation; data.sliding.locked = old_lock
            data.finish.gear = 1
            source = overlay(); source.static_grip[0] = 10
            expected = Vector(); copied = Sliding.from_buffer_copy(bytes(data.sliding))
            copied.dynamic_grip = source.dynamic_grip[0]
            assert lib.car_grip_force_sliding(C.byref(copied), C.byref(expected)) == 0
            actual = run(lib, source, data)
            # At low speed mu cancels; otherwise |Fx| is clamped to torque/r.
            assert close(actual[:2], (expected.lateral, min(expected.longitudinal, 1000)))
            count += 1
    print(f'G2 dynamic oracle / speed predicate sweep: {count}/{count} PASS')


def rejects(lib, source, value):
    out = Result.from_buffer_copy(b'\x55' * C.sizeof(Result))
    rc = lib.car_grip_wheel_ordinary(C.byref(source), C.byref(value), C.byref(out))
    return rc == -1 and bytes(out) == bytes(Result())


def invalid(lib):
    count = 0
    for field in ('dynamic_grip', 'static_grip', 'grip_scale', 'yaw_control'):
        for index in range(4 if field == 'yaw_control' else 2):
            for bad in (math.nan, math.inf, -math.inf) + (() if field == 'yaw_control' else (-1,)):
                source = overlay(); getattr(source, field)[index] = bad
                assert rejects(lib, source, wheel()); count += 1
    for size in (0, 5, 100):
        source = overlay(); source.yaw_count = size
        assert rejects(lib, source, wheel()); count += 1
    for component, field in (('sliding', 'load'), ('sliding', 'lateral_velocity'), ('sliding', 'forward_velocity'),
                             ('sliding', 'wheel_slip'), ('sliding', 'speed'), ('sliding', 'grip_multiplier'),
                             ('demand', 'locked'), ('demand', 'radius'), ('demand', 'inertia'),
                             ('demand', 'wheel_torque'), ('demand', 'angular_acceleration'),
                             ('demand', 'road_acceleration'), ('finish', 'speed_gain')):
        data = wheel(); obj = getattr(data, component)
        setattr(obj, field, 1 if field == 'locked' else getattr(obj, field) + .25)
        assert rejects(lib, overlay(), data), (component, field); count += 1
    for bad in (-1, 2, 100):
        data = wheel(); data.axle = bad
        assert rejects(lib, overlay(), data); count += 1
    for bad in (-1, math.nan, math.inf):
        data = wheel(); data.curve_force_per_grip = bad
        assert rejects(lib, overlay(), data); count += 1
    source = overlay(); source.grip_scale[0] = 3e38
    data = wheel(); data.curve_force_per_grip = 3e38
    assert rejects(lib, source, data); count += 1
    for native, source in ((None, overlay()), (Tyre(), None), (Tyre(), Overlay())):
        out = Tyre.from_buffer_copy(b'\x55' * C.sizeof(Tyre))
        assert lib.car_grip_wheel_overlay(C.byref(native) if native else None,
                C.byref(source) if source else None, C.byref(out)) == -1
        assert bytes(out) == bytes(Tyre()); count += 1
    assert lib.car_grip_wheel_overlay(None, None, None) == -1
    out = Result()
    assert lib.car_grip_wheel_ordinary(None, None, C.byref(out)) == -1
    assert lib.car_grip_wheel_ordinary(None, None, None) == -1
    print(f'Overlay/snapshot/nonfinite/null/overflow admission: {count+3} rejected and cleared')


def controls(tmp, cases):
    text = (ROOT / 'runtime/car_grip_wheel.c').read_text()
    mutations = [
        ('dynamic', 'sliding.dynamic_grip = source->dynamic_grip[in->axle];', 'sliding.dynamic_grip = 1;', 'dynamic_front'),
        ('static', 'finish.static_grip = source->static_grip[in->axle];', 'finish.static_grip = 1;', 'static_cap'),
        ('curve_scale', '* source->grip_scale[in->axle];', '* 1;', 'static_front'),
        ('angle_scale', 'finish.grip_scale = source->grip_scale[in->axle];', 'finish.grip_scale = 1;', 'angle_0'),
        ('axle', 'sliding.dynamic_grip = source->dynamic_grip[in->axle];', 'sliding.dynamic_grip = source->dynamic_grip[0];', 'dynamic_rear'),
        ('yaw_overlay', 'saved.grip = replacement;', 'saved.grip = replacement; saved.grip.yaw_control[1] = 9;', 'overlay'),
        ('preserve_steering', 'saved.grip = replacement;', 'saved.grip = replacement; saved.steering[0] = 0;', 'overlay'),
        ('preserve_yaw_speed', 'saved.grip = replacement;', 'saved.grip = replacement; saved.yaw_speed = 0;', 'overlay'),
        ('yaw_count', 'saved.grip = replacement;', 'saved.grip = replacement; saved.grip.yaw_count = 4;', 'overlay'),
        ('inactive_yaw', 'saved.grip.yaw_control[i] = 0;', 'saved.grip.yaw_control[i] = replacement.yaw_control[i];', 'overlay'),
    ]
    for name, old, new, witness in mutations:
        assert text.count(old) == 1, (name, text.count(old))
        lib = compile_wheel(tmp, name, text.replace(old, new))
        if witness == 'overlay': assert not overlay_check(lib), name
        else:
            source, value, expected = cases[witness]
            assert not close(run(lib, source, value), expected), name
    print(f'Compiled omitted-overlay/selection mutants: {len(mutations)}/{len(mutations)} FAIL golden; normal PASS')


def main():
    with tempfile.TemporaryDirectory(prefix='grip-wheel-') as directory:
        tmp = Path(directory); lib = compile_wheel(tmp, 'normal'); cases = fixtures()
        for name, (source, value, expected) in cases.items():
            actual = run(lib, source, value)
            assert close(actual, expected), (name, actual, expected)
        print(f'Decoded independent wheel goldens: {len(cases)}/{len(cases)} PASS')
        assert overlay_check(lib)
        print('Four-field overlay / native steering and yaw-speed preservation / aliasing PASS')
        yaw_oracle(lib); speed_oracle(lib); invalid(lib); controls(tmp, cases)
    print('PASS: dormant ordinary wheel adapter')


if __name__ == '__main__': main()
