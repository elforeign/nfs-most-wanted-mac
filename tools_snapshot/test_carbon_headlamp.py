"""Added Carbon cars: headlamp covers must bind the row MW's own cars' covers bind.

MW's HEADLIGHTGLASS light material (GLOBALB 135200) has DiffuseMinA/MaxA 1.0 / 0.35 (+50 / +54): the cover
is 35 % opaque seen face-on and full at grazing angles. The runtime keys its console glass row and its
clear-cover shading on that name. The converter's lamp identity (CARBON_LAMP_HEAD) carries Carbon's
1.0 / 1.0 and is selected by the runtime only with NFS_CARBON_LAMPGLASS=on, so emitted by default the
covers drew fully opaque and without the cover shading.
One number: DiffuseMinA / DiffuseMaxA of the row every source HEADLIGHTGLASS draw binds after conversion.
  fixed converter:    1.0 / 0.35 on 997TT, MUSTANGSHLBYN, MURCIELAGO640 (MW's own row)
  previous converter: 1.0 / 1.0  (default-On lamp identity; fails)
Offline; private Carbon or MW packs absent: exit 3.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import os
import struct
import sys
from unittest import mock

HERE = _InputsPath(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import carbon_car as cc
from x360_tex import unpack_file

CARS = ('997TT', 'MUSTANGSHLBYN', 'MURCIELAGO640')
COVER = cc.name_hash('HEADLIGHTGLASS')


def rows(raw, size):
    return {cc.u32(raw, o + 20): raw[o + 8:o + 8 + z] for c, o, z in cc.walk(raw) if c == 0x135200 and z == size}


def covers(geo, mw_rows):
    """({(row name hash, DiffuseMinA, DiffuseMaxA)}, draws) over draws bound to the headlamp-cover
    material, under either name (MW's HEADLIGHTGLASS, or the converter's lamp identity)."""
    end = cc.u32(geo, 4) + 8
    trailing = {cc.u32(geo, o + 20): geo[o + 8:o + 8 + z] for c, o, z in cc.walk(geo, end)
                if c == 0x135200 and z == 168} if end < len(geo) else {}
    cover = {COVER, cc.name_hash('CARBON_LAMP_HEAD')}
    found, draws = set(), 0
    for _, s in cc.solids(geo):
        lights = [h for h, _ in struct.iter_unpack('<II', cc.payload(s, 0x134013))]
        rb = cc.payload(s, 0x134b02, 16)
        for at in range(0, len(rb), 104):
            i = rb[at + 29]
            if i == 255 or lights[i] not in cover: continue
            row = trailing.get(lights[i]) or mw_rows[lights[i]]
            found.add((lights[i],) + struct.unpack_from('<ff', row, 0x50))
            draws += 1
    return found, draws


def main():
    carbon = input_path('NFSC')
    mw = _InputsPath(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition'))
    if not all((carbon / 'CARS' / car / 'GEOMETRY.BIN').is_file() for car in CARS) or \
            not (mw / 'CARS/997S/GEOMETRY.BIN').is_file():
        print('SKIP: private Carbon or MW packs absent')
        return 3
    mw_rows = rows(unpack_file((mw / 'GLOBAL/GLOBALB.LZC').read_bytes()), 168)
    want = (COVER,) + struct.unpack_from('<ff', mw_rows[COVER], 0x50)
    assert want[1] != want[2], f'premise: MW HEADLIGHTGLASS alpha does not vary with view angle {want}'
    reference = (mw / 'CARS/997S/GEOMETRY.BIN').read_bytes()
    failures = 0
    for car in CARS:
        source = (carbon / 'CARS' / car / 'GEOMETRY.BIN').read_bytes()
        convert = lambda: cc.convert_geometry(source, reference, '997S', material_root=mw,
                                              source_material_root=carbon)[0]
        with mock.patch.dict(os.environ):
            os.environ.pop('NFS_CARBON_LAMPGLASS', None)
            now, n = covers(convert(), mw_rows)
            with mock.patch.object(cc, 'carbon_lampglass_on', lambda: True):
                was, n_was = covers(convert(), mw_rows)
        show = lambda found: sorted(f'{h:08X} {a:g} / {b:g}' for h, a, b in found)
        print(f'{car}: {n} headlamp cover draws, row and DiffuseMinA/MaxA {show(now)} '
              f'(previous converter {n_was} draws {show(was)}; MW {want[1]:g} / {want[2]:g})')
        if now != {want} or n != n_was or not n:
            failures += 1
            print(f'FAIL {car}: headlamp covers do not bind MW\'s HEADLIGHTGLASS row')
        if was != {(cc.name_hash('CARBON_LAMP_HEAD'), 1., 1.)}:
            failures += 1
            print(f'FAIL {car}: the previous converter no longer shows the defect')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
