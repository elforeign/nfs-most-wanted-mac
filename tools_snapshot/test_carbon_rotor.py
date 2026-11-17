"""Added Carbon cars: the brake disc must bind an image the game loads.

The game streams a car pack's images only under the names GetUsedCarTextureInfo (00751860) requests; MW's
shared files hold ROTOR1..4. R8 and 997GT3RS bind Carbon's ROTOR5 and CCX its ROTOR6, which the converter
stored in the pack under their own names: never requested, never loaded, and the alpha-tested disc did
not draw (the rim and caliper did).
One number: brake-disc draws (all LODs) whose image is neither in MW's shared files nor a requested name.
  fixed converter:    0 on R8, 997GT3RS, CCX (the source rotor rides the requested DOOR_HANDLE name)
  previous converter: 5 / 4 / 6 (fails)
Offline; private Carbon or MW packs absent: exit 3.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import os
import sys
from unittest import mock

HERE = _InputsPath(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import carbon_car as cc

CARS = (('R8', 'GALLARDO'), ('997GT3RS', '997S'), ('CCX', 'CARRERAGT'))
# Car image names of GetUsedCarTextureInfo, read from the decompiled function, not from the converter.
REQUESTED = ('BADGING', 'BADGING_EU', 'BADGING_N', 'DOOR_HANDLE', 'DRIVER', 'ENGINE', 'INTERIOR', 'INTERIOR_N',
             'LICENSE_PLATE', 'LOGO', 'MISC', 'MISC_N', 'NEON', 'RIM', 'RIM_BLUR', 'SIDELIGHT', 'SKIN1', 'SKIN1B',
             'SKIN2', 'SKIN3', 'SKIN4', 'TIRE', 'TIRE_N', 'TREAD', 'TREAD_N')
DISC = cc.name_hash('BRAKEDISC')


def unloadable(carbon, mw, slot, shared):
    """Brake-disc draws whose converted image the game would not load, and all brake-disc draws."""
    source = (carbon / 'GEOMETRY.BIN').read_bytes()
    aliases, _ = cc.material_resources(carbon, mw, source, (carbon / 'TEXTURES.BIN').read_bytes(), slot)
    requested = {cc.name_hash(f'{slot}_{role}') for role in REQUESTED}
    bad = draws = 0
    for _, solid in cc.solids(source):
        m = cc.model(solid)
        for r in m['records']:
            if r[29] == 255 or m['lights'][r[29]] != DISC: continue
            image = aliases.get(m['textures'][r[24]], (m['textures'][r[24]], None))[0]
            draws += 1
            bad += image not in shared and image not in requested
    return bad, draws


def main():
    carbon = input_path('NFSC')
    mw = _InputsPath(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition'))
    if not all((carbon / 'CARS' / car / 'GEOMETRY.BIN').is_file() and (mw / 'CARS' / donor / 'TEXTURES.BIN').is_file()
               for car, donor in CARS):
        print('SKIP: private Carbon or MW packs absent')
        return 3
    shared = cc.mw_shared_resources(str(mw))[0]
    assert cc.name_hash('ROTOR1') in shared and cc.name_hash('ROTOR5') not in shared and \
        cc.name_hash('ROTOR6') not in shared, 'premise: MW ships ROTOR1 and neither ROTOR5 nor ROTOR6'
    failures = 0
    for car, donor in CARS:
        args = (carbon / 'CARS' / car, mw / 'CARS' / donor, donor, shared)
        now, n = unloadable(*args)
        with mock.patch.object(cc, 'SOURCE_IMAGE_SLOTS', ()):
            was, n_was = unloadable(*args)
        print(f'{car}: {n} brake-disc draws, {now} bind an image the game does not load (previous converter {was})')
        if now or n != n_was or not n:
            failures += 1
            print(f'FAIL {car}: a brake disc binds an image the game never requests')
        if not was:
            failures += 1
            print(f'FAIL {car}: the previous converter no longer shows the defect')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
