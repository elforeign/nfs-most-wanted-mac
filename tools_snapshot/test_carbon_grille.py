"""Added Carbon cars: grille and hood draws must bind an image the game loads.

The game streams a car pack's images only under the names GetUsedCarTextureInfo (00751860) requests. 300C,
CHALLENGERN, CHARGER06, JAGUARXK and MUSTANGSHLBYO bind Carbon's shared GRILL_05 / GRILL_06 / SILVER_SLATS, and
CHALLENGER71, CHARGER69, CUDA and MUSTANGSHLBYO its HOOD_ENGINE, none of which MW ships; the converter stored them
in the pack under their own names: never requested, never loaded, so the pieces drew untextured (300C's grille
flat white).
One number per car: draws (all LODs) whose image is neither in MW's shared files nor a requested car name.
  fixed converter:    0 on all eight (each image rides a free requested name, pixels unchanged)
  previous converter: 322 / 164 / 149 / 5 / 58 / 54 / 36 / 55 (fails)
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

CARS = (('300C', 'CTS'), ('CHALLENGERN', 'MUSTANGGT'), ('CHARGER06', 'GTO'), ('JAGUARXK', 'DB9'),
        ('MUSTANGSHLBYO', 'CAMARO'), ('CHALLENGER71', 'CAMARO'), ('CHARGER69', 'CAMARO'), ('CUDA', 'CAMARO'))
# Car image names of GetUsedCarTextureInfo, read from the decompiled function, not from the converter.
REQUESTED = ('BADGING', 'BADGING_EU', 'BADGING_N', 'DOOR_HANDLE', 'DRIVER', 'ENGINE', 'INTERIOR', 'INTERIOR_N',
             'LICENSE_PLATE', 'LOGO', 'MISC', 'MISC_N', 'NEON', 'RIM', 'RIM_BLUR', 'SIDELIGHT', 'SKIN1', 'SKIN1B',
             'SKIN2', 'SKIN3', 'SKIN4', 'TIRE', 'TIRE_N', 'TREAD', 'TREAD_N')
LAMPS = ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS')


def unloadable(carbon, mw, slot, shared, dynamic):
    """Draws whose converted image the game would not load, and all draws."""
    source = (carbon / 'GEOMETRY.BIN').read_bytes()
    aliases, _ = cc.material_resources(carbon, mw, source, (carbon / 'TEXTURES.BIN').read_bytes(), slot)
    requested = {cc.name_hash(f'{slot}_{role}') for role in REQUESTED}
    requested.update(cc.name_hash(f'{slot}_KIT{k:02d}_{kind}_{state}')
                     for k in range(100) for kind in LAMPS for state in ('ON', 'OFF'))
    bad = draws = 0
    for _, solid in cc.solids(source):
        m = cc.model(solid)
        for r in m['records']:
            image = aliases.get(m['textures'][r[24]], (m['textures'][r[24]], None))[0]
            draws += 1
            bad += image not in shared and image not in dynamic and image not in requested
    return bad, draws


def main():
    carbon = input_path('NFSC')
    mw = _InputsPath(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition'))
    if not all((carbon / 'CARS' / car / 'GEOMETRY.BIN').is_file() and (mw / 'CARS' / donor / 'TEXTURES.BIN').is_file()
               for car, donor in CARS):
        print('SKIP: private Carbon or MW packs absent')
        return 3
    shared, _, dynamic = cc.mw_shared_resources(str(mw))
    assert not {cc.name_hash(n) for n in ('GRILL_05', 'GRILL_06', 'SILVER_SLATS', 'HOOD_ENGINE')} & shared, \
        'premise: MW ships none of GRILL_05, GRILL_06, SILVER_SLATS, HOOD_ENGINE'
    failures = 0
    for car, donor in CARS:
        args = (carbon / 'CARS' / car, mw / 'CARS' / donor, donor, shared, dynamic)
        now, n = unloadable(*args)
        with mock.patch.object(cc, 'SOURCE_IMAGE_SLOTS', ()):
            was, n_was = unloadable(*args)
        print(f'{car}: {n} draws, {now} bind an image the game does not load (previous converter {was})')
        if now or n != n_was or not n:
            failures += 1
            print(f'FAIL {car}: a draw binds an image the game never requests')
        if not was:
            failures += 1
            print(f'FAIL {car}: the previous converter no longer shows the defect')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
