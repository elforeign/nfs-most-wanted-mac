"""CPU proof of headlight_profile.c and headlight_profile.h (no private inputs).

Production: 410/410 pass, 25/50 m = 16.16/4.04 lx, ratio 4.
Compiled inverse-square mutation: 17/410 failures, ratio 2 (404/202 lx).
Compiled cut-off mutation: 67/410 failures, including above-horizon light.
The same physical assertions execute against all three compiled implementations;
each broken implementation must fail its named physical check, not compilation.
Autodiscovered by runtime/offline_tests.sh.
"""
import copy
import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent


class V(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double), ('z', C.c_double)]


class Lamp(C.Structure):
    _fields_ = [('position', V), ('right', V), ('up', V), ('forward', V), ('enabled', C.c_int)]


class Profile(C.Structure):
    _fields_ = [('horizontal_count', C.c_size_t), ('vertical_count', C.c_size_t),
               ('horizontal_deg', C.POINTER(C.c_double)), ('vertical_deg', C.POINTER(C.c_double)),
               ('candela', C.POINTER(C.c_double))]


def proof(lib, label):
    for name, args in [('hl_candela', [C.c_double, C.c_double]),
                       ('hl_cutoff_deg', [C.c_double]), ('hl_distance_fade', [C.c_double]),
                       ('hl_illuminance', [C.c_double] * 3),
                       ('hl_lamp_lux', [C.POINTER(Lamp), V, V]),
                       ('hl_pair_lux', [C.POINTER(Lamp), V, V])]:
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, C.c_double
    intensity, lux = lib.hl_candela, lib.hl_illuminance
    failures, checks = [], 0

    def check(ok, name):
        nonlocal checks
        checks += 1
        if not ok:
            failures.append(name)

    def close(a, b):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)

    # Independent numerical requirements: UN R112 section 6.2.4, Class B.
    check(intensity(1.15, -.57) >= 10100, '75R >= 10100 cd')
    check(intensity(-3.43, .57) <= 350, 'B50L <= 350 cd')
    check(intensity(-9, -1.72) >= 1700, '25L >= 1700 cd')
    check(intensity(9, -1.72) >= 1700, '25R >= 1700 cd')
    check(intensity(1.72, -.86) >= 10100, '50R >= 10100 cd')
    check(intensity(0, -.86) >= 5100, '50V >= 5100 cd')
    check(intensity(-3.43, -.57) <= 10600, '75L <= 10600 cd')
    check(intensity(-3.43, -.86) <= 13200, '50L <= 13200 cd')
    check(intensity(3.43, -.3) > 10000 and intensity(-3.43, -.3) == 0, 'RHT asymmetry')
    for h in (-45, -9, -3.43, 0, 1.15, 3.43, 9, 45):
        for v in (.000001, .3, .57, 1, 7):
            check(intensity(h, v) == 0, 'above horizon dark')
    check(intensity(-3.43, -.569999) == 0, 'left cut-off dark')
    check(intensity(-3.43, -.570001) > 0, 'below left cut-off lit')
    check(close(lib.hl_cutoff_deg(-9), -.57), 'nominal left aim')
    check(close(lib.hl_cutoff_deg(1), -.57 + math.tan(math.pi/12)), '15 degree kick-up')
    check(lib.hl_cutoff_deg(9) == 0, 'right kick-up capped at horizon')

    profile = Profile.in_dll(lib, 'hl_low_beam')
    nx, ny = profile.horizontal_count, profile.vertical_count
    check(nx == 13 and ny == 9, 'grid dimensions')
    hx, vy = profile.horizontal_deg, profile.vertical_deg
    check(all(hx[i] < hx[i+1] for i in range(nx-1)), 'horizontal knots increase')
    check(all(vy[i] < vy[i+1] for i in range(ny-1)), 'vertical knots increase')
    for j in range(ny):
        for i in range(nx):
            cd = profile.candela[j*nx+i]
            check(math.isfinite(cd) and cd >= 0, 'nonnegative finite table')
            expected = cd if vy[j] <= lib.hl_cutoff_deg(hx[i]) else 0
            check(close(intensity(hx[i], vy[j]), expected), 'grid knot / cutoff')
    # Independent four-corner interpolation at unequal weights. Every cell
    # entirely below the left cut-off qualifies, including the domain edges.
    for j in range(4):
        for i in range(nx-1):
            u, t = .23, .61
            h, v = hx[i]*(1-u)+hx[i+1]*u, vy[j]*(1-t)+vy[j+1]*t
            expected = sum(profile.candela[(j+dy)*nx+i+dx] * wx * wy
                           for dx, wx in ((0, 1-u), (1, u))
                           for dy, wy in ((0, 1-t), (1, t)))
            check(close(intensity(h, v), expected), 'bilinear interior')
    for h, v in [(-46, -1), (46, -1), (0, -5.01), (0, 7.01),
                 (math.nan, -1), (0, math.inf), (math.inf, 0)]:
        check(intensity(h, v) == 0, 'invalid / outside grid')

    e25, e50 = lux(10100, 25, 1), lux(10100, 50, 1)
    check(close(e25, 16.16), '25 m = 16.16 lux')
    check(close(e50, 4.04), '50 m = 4.04 lux')
    check(e50 > 0 and close(e25/e50, 4), '25/50 inverse-square ratio')
    check(close(lux(10100, 25, .5), 8.08), 'receiver cosine halves lux')
    check(lux(10100, 25, 0) == 0, 'tangent zero')
    check(lux(10100, 25, -1) == 0, 'backface zero')
    check(lux(0, 25, 1) == 0, 'zero source')
    for r, fade in [(0, 1), (25, 1), (99, 1), (100, 1), (105, .75),
                    (110, .5), (115, .25), (120, 0), (121, 0)]:
        check(close(lib.hl_distance_fade(r), fade), '100-120 m linear fade')
        if r:
            check(close(lux(10100, r, 1), 10100*fade/r**2), 'fade separate from inverse square')
    for r in (-1, math.inf, math.nan):
        check(lib.hl_distance_fade(r) == 0, 'invalid fade input')
    for cd, r, c in [(-1, 25, 1), (math.nan, 25, 1), (math.inf, 25, 1),
                     (10100, 0, 1), (10100, -1, 1), (10100, math.nan, 1),
                     (10100, math.inf, 1), (10100, 25, math.nan), (10100, 25, 2)]:
        check(lux(cd, r, c) == 0, 'invalid illuminance input')

    def ray(h, v):
        h, v = math.radians(h), math.radians(v)
        return V(math.sin(h)*math.cos(v), math.sin(v), math.cos(h)*math.cos(v))

    def scaled(v, k):
        return V(v.x*k, v.y*k, v.z*k)

    lamp = Lamp(V(0, 0, 0), V(1, 0, 0), V(0, 1, 0), V(0, 0, 1), 1)
    q = ray(1.15, -.86)  # Safely below the cut-off after floating point transforms.
    receiver, normal = scaled(q, 25), scaled(q, -1)
    one = lib.hl_lamp_lux(C.byref(lamp), receiver, normal)
    check(close(one, intensity(1.15, -.86)/625), 'marker angles and SI distance')
    pair = (Lamp * 2)(lamp, lamp)
    two = lib.hl_pair_lux(pair, receiver, normal)
    check(two > 0 and close(two, 2*one), 'two lamps add')
    pair[1].enabled = 0
    pair[1].position.x = math.nan  # Disabled marker may be absent/uninitialized.
    check(close(lib.hl_pair_lux(pair, receiver, normal), two/2), 'one disabled halves sum')
    pair[0].enabled = 0
    check(lib.hl_pair_lux(pair, receiver, normal) == 0, 'both disabled zero')
    check(lib.hl_pair_lux(None, receiver, normal) == 0, 'null pair zero')
    for h, v in [(1.15, -.86), (-9, -1.72), (9, -1.72), (-3.43, .57), (3.43, .3)]:
        q = ray(h, v)
        for r in (25, 50):
            p, n = scaled(q, r), scaled(q, -1)
            check(close(lib.hl_lamp_lux(C.byref(lamp), p, n), intensity(h, v)/r**2),
                  'world marker lookup')
    check(lib.hl_lamp_lux(C.byref(lamp), receiver, scaled(normal, -1)) == 0, 'world backface zero')
    check(close(lib.hl_lamp_lux(C.byref(lamp), receiver,
                              V(normal.x*.5 + math.cos(math.radians(1.15))*math.sqrt(.75),
                                normal.y*.5,
                                normal.z*.5 - math.sin(math.radians(1.15))*math.sqrt(.75))), one*.5),
          'world tilted normal cosine')
    # A rigid rotation and translation must preserve both angles and lux.
    def transform(v):
        return V(v.z, v.x, v.y)

    moved = copy.deepcopy(lamp)
    moved.position = V(200, -31, 900)
    moved.right, moved.up, moved.forward = transform(lamp.right), transform(lamp.up), transform(lamp.forward)
    p = transform(receiver)
    p = V(p.x+200, p.y-31, p.z+900)
    check(close(lib.hl_lamp_lux(C.byref(moved), p, transform(normal)), one), 'translated rotated markers')
    for p in (V(0, 0, 0), V(0, 0, -25), V(0, math.nan, 25), scaled(ray(0, -1), 121)):
        check(lib.hl_lamp_lux(C.byref(lamp), p, normal) == 0, 'degenerate / rear / distant receiver')
    for field, value in [('forward', V(0, 0, 2)), ('up', V(1, 0, 0)),
                         ('position', V(math.inf, 0, 0)), ('right', V(0, 0, 0))]:
        bad = copy.deepcopy(lamp)
        setattr(bad, field, value)
        check(lib.hl_lamp_lux(C.byref(bad), receiver, normal) == 0, 'invalid marker basis')
    check(lib.hl_lamp_lux(None, receiver, normal) == 0, 'null lamp zero')
    check(lib.hl_lamp_lux(C.byref(lamp), receiver, V(0, 0, 2)) == 0, 'invalid normal zero')
    print(f'{label}: {checks-len(failures)}/{checks} pass, {len(failures)} failures; '
          f'25m={e25:.6f} lx 50m={e50:.6f} lx ratio={e25/e50:.6f}')
    if failures:
        print('  failed: ' + ', '.join(sorted(set(failures))))
    return failures


def main():
    original = (RT / 'headlight_profile.c').read_text()
    variants = [('production', None, None, None),
                ('broken falloff', '/ (r * r)', '/ r', '25/50 inverse-square ratio'),
                ('broken cutoff', 'if (v > hl_cutoff_deg(h)) return 0.0;',
                 'if (v > hl_cutoff_deg(h)) v = -0.86;', 'above horizon dark')]
    with tempfile.TemporaryDirectory(prefix='headlight-profile-') as directory:
        for label, before, after, required_failure in variants:
            source = original
            if before is not None:
                assert source.count(before) == 1, (label, 'mutation anchor changed')
                source = source.replace(before, after)
            path = Path(directory) / (label.replace(' ', '_') + '.c')
            path.write_text(source)
            binary = path.with_suffix('.dylib')
            built = subprocess.run(['clang', '-std=c11', '-shared', '-fPIC', '-O1',
                                    '-Wall', '-Wextra', '-Werror', '-I', str(RT),
                                    str(path), '-o', str(binary)], capture_output=True, text=True)
            assert built.returncode == 0, built.stderr[-3000:]
            failures = proof(C.CDLL(str(binary)), label)
            if required_failure is None:
                assert not failures, failures
            else:
                assert required_failure in failures, (label, failures)


if __name__ == '__main__':
    main()
