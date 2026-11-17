"""ProStreet added cars carry their own weight and wheel positions (tools_snapshot/ps_attrib.py).

One number: the largest error of what the game will use for three cars a player can own (NSX, DBR9, MCLARENF1)
against the real car, over body mass, wheelbase and track. The values are read back from the PHYSICS.BIN and
WHEELFIT.BIN of a real private generation; a car without them drives on its donor's values, which are then judged.
Limit 3 %.
Measured: 0.7 % (NSX rear track 1.540 m against 1.530 m), exit 0.
Broken (--broken donor: no sidecars written, the state before ps_attrib): 46.2 % (DBR9 at the donor's 1710 kg), exit 1.
The fields kept from the donor (suspension height and travel, five cameras) must equal the donor's.
Exit 3 without the private ProStreet and Most Wanted installs.
"""
import argparse
import os
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'tools')]
from setup_kit.car_convert import CAMERA_FIELDS, CAMERA_LINKS, FitAttributes, hash32
from setup_kit.input_paths import input_path

# Real cars: kg low, kg high, wheelbase m, front track m, rear track m (None: no agreed figure).
REAL = {
    'NSX': (1370, 1410, 2.530, 1.510, 1.530),        # 1997 coupe, maker's press kit
    'DBR9': (1100, 1170, 2.741, None, None),         # GT1 minimum to the commonly listed weight
    'MCLARENF1': (1138, 1140, 2.718, 1.568, 1.472),  # road car
}
LIMIT = 3.0


def donor_values(native, donor):
    key = hash32(donor.lower())
    chassis = native.linked_key('pvehicle', key, 'chassis')
    return (native.floats('pvehicle', key, ('MASS',))[0],
            native.floats('chassis', chassis, ('RIDE_HEIGHT', 'TRAVEL', 'TRACK_WIDTH', 'WHEEL_BASE')),
            tuple(v for link in CAMERA_LINKS for v in native.floats(
                'camerainfo', native.linked_key('ecar', key, link), CAMERA_FIELDS)))


def error(value, low, high=None):
    high = low if high is None else high
    return 0.0 if low <= value <= high else 100 * min(abs(value / low - 1), abs(value / high - 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broken', choices=('donor',))
    args = parser.parse_args()
    source = input_path('Need for Speed ProStreet')
    mw = Path(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition'))
    if not ((source / 'GLOBAL/ATTRIBUTES.BIN').is_file() and (mw / 'GLOBAL/attributes.bin').is_file()
            and all((source / 'CARS' / car / 'GEOMETRY.BIN').is_file() for car in REAL)):
        print('SKIP: private ProStreet / Most Wanted installs absent')
        return 3
    import private_cars as pc
    import ps_attrib
    if args.broken == 'donor':
        ps_attrib.sidecars = lambda source, reference: {}
    native = FitAttributes((mw / 'GLOBAL/attributes.bin').read_bytes())
    worst, kept, lines = 0.0, True, []
    with tempfile.TemporaryDirectory(prefix='ps-attrib-') as scratch:
        root = Path(scratch).resolve() / 'private-cars'
        data = pc.build(root, mw / 'CARS', dict(P=source / 'CARS'), adapter=lambda row: pc.converter(row)
                        if row['source'] in {'P/' + car for car in REAL} else (None, 'not selected for check'))
        pc.verify(root)
        for car, (low, high, wheelbase, front, rear) in REAL.items():
            row = next(r for r in data['cars'] if r['source'] == 'P/' + car)
            if row['status'] != 'converted':
                print(f'FAIL {car} conversion:', row.get('reason'))
                return 1
            pack = root / data['generation'] / 'additions' / car
            mass, chassis, cameras = donor_values(native, row['donor'])
            used = 'donor ' + row['donor']
            if (pack / 'PHYSICS.BIN').is_file():
                mass, used = struct.unpack_from('<f', (pack / 'PHYSICS.BIN').read_bytes(), 16)[0], 'own'
            if (pack / 'WHEELFIT.BIN').is_file():
                fit = (pack / 'WHEELFIT.BIN').read_bytes()
                own = struct.unpack_from('<7f', fit, 164)
                kept = kept and own[:4] == chassis[:4] and struct.unpack_from('<40f', fit, 220) == cameras
                chassis = own
            errors = [error(mass, low, high), error(chassis[6], wheelbase)]
            errors += [error(chassis[4 + i], real) for i, real in enumerate((front, rear)) if real]
            worst = max(worst, *errors)
            lines.append(f'{car} ({used}): {mass:.0f} kg, wheelbase {chassis[6]:.3f} m, track {chassis[4]:.3f}/'
                         f'{chassis[5]:.3f} m, worst error {max(errors):.1f} %')
    print('\n'.join(lines))
    if worst > LIMIT or not kept:
        print(f'FAIL ProStreet attributes: largest error {worst:.1f} % (limit {LIMIT} %); donor-kept fields equal: {kept}')
        return 1
    print(f'PASS ProStreet attributes: largest error {worst:.1f} % (limit {LIMIT} %) on {len(REAL)} cars')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
