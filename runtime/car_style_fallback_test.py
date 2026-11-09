"""Random STYLE resources: original 1320/5150 absent; fixed 0/5150, 28 packs.

Runs the C fallback against every installed addition, independently decoding
geometry indices and all two-digit KIT/KITW/STYLE HOOD/ROOF targets, LOD A-D.
Synthetic controls also cover BODY, SPOILER, multiword slots, authored styles,
absent stock LODs, duplicate targets, namespace isolation and idempotence.
Synthetic original 20/21 absent; fixed 0/21. Original 997GT3RS STYLE06 fails.
SetRandomParts (0075b220) selects BODY (17), SPOILER (2c), ROOF (3e), HOOD
(3f), FRONT_WHEEL (42), paint/vinyl colours (4c-52), WINDOW_TINT (83).
The installed donors' STYLE geometry uses HOOD only; slots decoded from the
retail slot-name table at 009044d8. Policy covers any slot with stock geometry.
Exit 3 without installed private packs; --root selects a scratch installation.
"""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests/setup'))
from setup_kit import car_convert as cc
import test_car_convert as fixture

# Original candidate-generation fragment: keep the actual KIT-only predecessor.
OLD = '''for (unsigned wide = 0; wide < 2; ++wide) for (unsigned kit = 0; kit < 100; ++kit) {
                    int length = snprintf(candidate, sizeof candidate, "%s_KIT%s%02u_%s", space,
                                          wide ? "W" : "", kit, suffix);'''
NEW = '''for (unsigned family = 0; family < 3; ++family) for (unsigned kit = 0; kit < 100; ++kit) {
                    const char *kind = family == 2 ? "STYLE" : family ? "KITW" : "KIT";
                    int length = snprintf(candidate, sizeof candidate, "%s_%s%02u_%s", space,
                                          kind, kit, suffix);'''

HARNESS = r'''
#include "car_kit_fallback.h"
#include <assert.h>
int main(int argc, char **argv) {
    assert(argc == 5);
    FILE *f = fopen(argv[2], "rb"); assert(f);
    uint32_t h[4]; assert(fread(h, 4, 4, f) == 4 && h[0] == 0x31504d53 && h[3] <= 8192);
    uint32_t *map = malloc(h[3]*8); assert(map);
    assert(fread(map, 8, h[3], f) == h[3]); fclose(f);
    f = fopen(argv[1], "rb"); assert(f);
    int repaired = car_kit_fallback(f, argv[4], map, h[3]);
    assert(repaired >= 0);
    assert(car_kit_fallback(f, argv[4], map, h[3]) == 0); fclose(f);
    f = fopen(argv[3], "wb"); assert(f);
    assert(fwrite(map, 8, h[3], f) == h[3]); fclose(f); free(map);
    printf("%d\n", repaired);
    return 0;
}
'''


def build(tmp, label, header):
    folder = tmp / label
    folder.mkdir()
    (folder / 'car_kit_fallback.h').write_text(header)
    (folder / 'check.c').write_text(HARNESS)
    binary = folder / 'check'
    result = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                             '-fsanitize=address,undefined', str(folder / 'check.c'), '-o', str(binary)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return binary


def resolve(binary, geometry, sidecar, namespace, tmp):
    output = tmp / 'resolved.bin'
    run = subprocess.run([str(binary), str(geometry), str(sidecar), str(output), namespace],
                         capture_output=True, text=True,
                         env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
    assert run.returncode == 0, run.stderr[-2000:]
    before = dict(struct.iter_unpack('<II', sidecar.read_bytes()[16:]))
    after = dict(struct.iter_unpack('<II', output.read_bytes()))
    assert before.keys() == after.keys()
    assert sum(before[k] != after[k] for k in before) == int(run.stdout)
    return before, after


def geometry_names(path):
    # Independent indexed name/hash decode; never infer presence from SOLIDMAP.
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        result = {}
        for hs, at, size, unpacked, flags, zero in cc.index(raw):
            assert size == unpacked and not flags and not zero
            header, length = next((o, z) for c, o, z in cc.children(raw, at + 8, at + size)
                                  if c == 0x134011)
            start = ((header + 23) & ~15) + 160
            end = raw.find(b'\0', start, header + 8 + length)
            assert end >= start
            name = raw[start:end].decode('ascii')
            assert fixture.nh(name) == hs
            result[hs] = name
        return result


def synthetic(tmp, current, legacy):
    names = [f'NFSAD_CAR_KIT00_{slot}_{lod}' for slot in ('HOOD', 'ROOF', 'BODY', 'SPOILER', 'ROOF_T0')
             for lod in 'ABCD']
    names += ['NFSAD_CAR_STYLE06_HOOD_B']
    with mock.patch.object(fixture, 'stock_names', return_value=names):
        geometry = fixture.geometry('NFSAD_CAR', False)
    geo, sidecar = tmp / 'synthetic.bin', tmp / 'synthetic-map.bin'
    geo.write_bytes(geometry)
    requests = {f'DONOR_STYLE06_{slot}_{lod}': f'NFSAD_CAR_STYLE06_{slot}_{lod}'
                for slot in ('HOOD', 'ROOF', 'BODY', 'SPOILER', 'ROOF_T0') for lod in 'ABCD'}
    requests.update({'DUPLICATE': 'NFSAD_CAR_STYLE06_HOOD_A',
                     'NO_LOD': 'NFSAD_CAR_STYLE06_HOOD_Z',
                     'FOREIGN': 'NFSAD_OTHER_STYLE06_HOOD_A', 'IMAGE': 'NFSAD_CAR_DIFFUSE'})
    rows = sorted((fixture.nh(k), fixture.nh(v)) for k, v in requests.items())
    sidecar.write_bytes(struct.pack('<4I', 0x31504d53, 2, fixture.nh('CAR'), len(rows)) +
                        b''.join(struct.pack('<II', *row) for row in rows))
    present = geometry_names(geo)
    for label, binary in (('current', current), ('original', legacy)):
        before, after = resolve(binary, geo, sidecar, 'NFSAD_CAR', tmp)
        missing = sum(after[fixture.nh(k)] not in present for k in requests
                      if k not in ('NO_LOD', 'FOREIGN', 'IMAGE'))
        print(f'synthetic {label}: {missing}/21 missing')
        assert missing == (0 if label == 'current' else 20)
        for key in ('NO_LOD', 'FOREIGN', 'IMAGE', 'DONOR_STYLE06_HOOD_B'):
            assert after[fixture.nh(key)] == before[fixture.nh(key)]
        if label == 'current':
            for key, target in requests.items():
                expected = target if fixture.nh(target) in present or key in ('NO_LOD', 'FOREIGN', 'IMAGE') else target.replace('_STYLE06_', '_KIT00_')
                assert after[fixture.nh(key)] == fixture.nh(expected)


def installed(root, tmp, current, legacy):
    pointer = root / 'current.json'
    if not pointer.is_file():
        print('SKIP: installed private car packs are absent')
        return 3
    generation = json.loads(pointer.read_text())['generation']
    assert re.fullmatch(r'g-[0-9a-f]{32}', generation)
    additions = root / generation / 'additions'
    packs = sorted(p for p in additions.iterdir() if p.is_dir())
    if not packs:
        print('SKIP: installed additions are absent')
        return 3
    totals = [0, 0]
    affected, targets = 0, 0
    for pack in packs:
        namespace = 'NFSAD_' + pack.name
        geometry, sidecar = pack / 'GEOMETRY.BIN', pack / 'SOLIDMAP.BIN'
        present = geometry_names(geometry)
        # Include multiword HOOD/ROOF suffixes actually present in each pack.
        suffixes = {f'{slot}_{lod}' for slot in ('HOOD', 'ROOF') for lod in 'ABCD'}
        suffixes.update(m[1] for name in present.values()
                        if (m := re.fullmatch(re.escape(namespace) + r'_(?:KITW?|STYLE)\d{2}_((?:HOOD|ROOF)(?:_.*)?)', name)))
        decoded = {fixture.nh(f'{namespace}_{family}{number:02}_{suffix}'): f'{family}{number:02}_{suffix}'
                   for family in ('KIT', 'KITW', 'STYLE') for number in range(100) for suffix in suffixes}
        results = []
        for binary in (current, legacy):
            before, after = resolve(binary, geometry, sidecar, namespace, tmp)
            selected = {k: v for k, v in before.items() if v in decoded}
            assert selected, pack.name
            missing = [(decoded[v], after[k]) for k, v in selected.items() if after[k] not in present]
            # Authored targets must never be replaced by fallback geometry.
            assert all(after[k] == v for k, v in before.items() if v in present)
            results.append(missing)
        targets += len(selected)
        affected += bool(results[1])
        for i in range(2):
            totals[i] += len(results[i])
        if pack.name == '997GT3RS':
            assert ('STYLE06_HOOD_A', 0x32102125) in results[1]
            print(f'997GT3RS STYLE06_HOOD_A: original missing, current {"missing" if results[0] else "present"}')
    print(f'installed {len(packs)} additions, {targets} HOOD/ROOF rows: '
          f'original {totals[1]} missing ({affected} cars), current {totals[0]} missing')
    assert totals[0] == 0, 'installed HOOD/ROOF targets still absent'
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'Library/Application Support/NFSMW-port/private-cars')
    args = parser.parse_args()
    assert hashlib.sha256(OLD.encode()).hexdigest() == '588f834d1e427444d4640971e453f31762e8fe3fa2d98925ad42d2de27a287a6'
    header = (ROOT / 'runtime/car_kit_fallback.h').read_text()
    original = header.replace(NEW, OLD)
    assert OLD in original
    with tempfile.TemporaryDirectory(prefix='car-style-') as td:
        tmp = Path(td)
        current, legacy = build(tmp, 'current', header), build(tmp, 'original', original)
        result = installed(args.root, tmp, current, legacy)
        synthetic(tmp, current, legacy)
        return result


if __name__ == '__main__':
    raise SystemExit(main())
