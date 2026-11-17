"""Added Carbon cars: tail-lamp bulbs must draw at Carbon's authored material level.

Carbon's BRAKELIGHT light material has DiffuseMin/Max 3.0 / 6.0 (GLOBALB 135200 +64 / +74); MW's row
of the same name has 1.0 / 1.0 (+30 / +40). Carbon's BRAKELIGHT_OFF images are authored for its level
(mean 9.7-19.4 of 255; MW's own cars 64-99), so bound to MW's row the tail lamps draw dark.
One number: DiffuseMin / DiffuseMax of the row every source BRAKELIGHT draw binds after conversion.
  fixed converter:    3.0 / 6.0 on MUSTANGSHLBYO, 240SX, MURCIELAGO640 (read from Carbon's own row)
  previous converter: 1.0 / 1.0 (no source light table: the draws keep MW's BRAKELIGHT row; fails)
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

CARS = ('MUSTANGSHLBYO', '240SX', 'MURCIELAGO640')
BRAKELIGHT = cc.name_hash('BRAKELIGHT')


def rows(raw, size):
    return {cc.u32(raw, o + 20): raw[o + 8:o + 8 + z] for c, o, z in cc.walk(raw) if c == 0x135200 and z == size}


def levels(geo, mw_rows):
    """({(DiffuseMin, DiffuseMax)}, draws) over draws bound to the brake-bulb material, under
    either name (MW's BRAKELIGHT, or the converter's source-level copy of it)."""
    end = cc.u32(geo, 4) + 8
    trailing = {cc.u32(geo, o + 20): geo[o + 8:o + 8 + z] for c, o, z in cc.walk(geo, end)
                if c == 0x135200 and z == 168} if end < len(geo) else {}
    bulb = {BRAKELIGHT, cc.name_hash('CARBON_BRAKELIGHT')}
    found, draws = set(), 0
    for _, s in cc.solids(geo):
        lights = [h for h, _ in struct.iter_unpack('<II', cc.payload(s, 0x134013))]
        rb = cc.payload(s, 0x134b02, 16)
        for at in range(0, len(rb), 104):
            i = rb[at + 29]
            if i == 255 or lights[i] not in bulb: continue
            row = trailing.get(lights[i]) or mw_rows[lights[i]]
            found.add(struct.unpack_from('<f', row, 0x30) + struct.unpack_from('<f', row, 0x40))
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
    source_row = rows(unpack_file((carbon / 'GLOBAL/GLOBALB.LZC').read_bytes()), 236)[BRAKELIGHT]
    want = struct.unpack_from('<f', source_row, 0x64) + struct.unpack_from('<f', source_row, 0x74)
    mw_level = struct.unpack_from('<f', mw_rows[BRAKELIGHT], 0x30) + struct.unpack_from('<f', mw_rows[BRAKELIGHT], 0x40)
    assert want != mw_level, f'premise: Carbon and MW BRAKELIGHT levels match {want}'
    reference = (mw / 'CARS/997S/GEOMETRY.BIN').read_bytes()
    failures = 0
    for car in CARS:
        source = (carbon / 'CARS' / car / 'GEOMETRY.BIN').read_bytes()
        convert = lambda: cc.convert_geometry(source, reference, '997S', material_root=mw,
                                              source_material_root=carbon)[0]
        now, n = levels(convert(), mw_rows)
        with mock.patch.object(cc, 'CARBON_SOURCE_LIGHTS', {}):
            was, n_was = levels(convert(), mw_rows)
        print(f'{car}: {n} brake bulb draws, DiffuseMin/Max {sorted(now)} '
              f'(previous converter {n_was} draws {sorted(was)}; Carbon {want})')
        if now != {want} or n != n_was or not n:
            failures += 1
            print(f'FAIL {car}: tail-lamp bulbs do not carry Carbon\'s level')
        if was != {mw_level}:
            failures += 1
            print(f'FAIL {car}: the previous converter no longer shows the defect')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
