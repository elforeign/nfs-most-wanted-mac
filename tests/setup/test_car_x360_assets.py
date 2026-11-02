"""Optional local PC/X360 parity proof; exit 3 without the selected private inputs.

Good: 3 cars, 1226 solids and 31 textures. Broken linear-tiling decoder fails
the first car's DXT byte comparison, exit 1; missing environment exits 3.
Set NFS_CARBON_PC and NFS_CARBON_X360 to source roots (X360 accepts an ISO),
and NFS_CARBON_CARS to space-separated directory names. No roster is bundled.
Geometry tolerances: bounds/positions 1e-6, SHORT4N 1.6e-5, UV 0.0015.
Pixel threshold: each image's RGB mean absolute difference <=4/255 after a
32x32 BOX reduction. Convert to RGB before reduction: alpha is independent.
DXT top bytes must also match exactly; BC5's reconstructed blue/opaque alpha
are explicit policy, not recovered PC channels. Raw vertex counts can differ
because the platforms index different deduplicated buffers; indexed positions,
draw and triangle counts are compared without that exemption.
"""
import os
from collections import Counter
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_sources as cs, car_convert as cc, car_x360 as cx
from setup_kit.x360 import Source

RGB_MAD = 4.0


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def compare_car(car, pc_root, source, logical):
    def read(kind):
        return cs._read(source, logical[cs.path_hash(f'CARS/{car}/{kind}.BIN')])
    directory = pc_root / car
    xsolids = cc.solids(read('GEOMETRY'))
    psolids = dict(cc.solids((directory / 'GEOMETRY.BIN').read_bytes()))
    check({h for h, _ in xsolids} == psolids.keys(), f'{car}: solid identities differ')
    totals = dict(solids=0, draws=0, triangles=0, xvertices=0, pcvertices=0, markers=0)
    error = np.zeros(4)
    for hs, raw in xsolids:
        x, p = cx.source_model(raw), cc.model(psolids[hs])
        check(x['name'] == p['name'], f'{car}: solid name differs')
        check(x['mesh'] == p['mesh'], f'{car}: normalized mesh header differs')
        check(x['textures'] == p['textures'] and x['lights'] == p['lights'], f'{car}: component resources differ')
        check(len(x['indices']) == len(p['indices']), f'{car}/{x["name"]}: triangle total differs')
        check(len(x['records']) == len(p['records']), f'{car}: draw total differs')
        check([cc.u32(r, 64) for r in x['records']] == [cc.u32(r, 64) for r in p['records']],
              f'{car}: per-draw triangles differ')
        check([r[24:30] + r[56:60] for r in x['records']] == [r[24:30] + r[56:60] for r in p['records']],
              f'{car}: draw resources/flags differ')
        def bounds(m):
            return np.array(cc.words(m['header'], 32, '<3f') + cc.words(m['header'], 48, '<3f'))
        bdiff = np.abs(bounds(x) - bounds(p)).max()
        xv, pv = x['vertices'][x['indices']], p['vertices'][p['indices']]
        a, b = xv.copy().view('<f4').reshape(-1, 9), pv.copy().view('<f4').reshape(-1, 9)
        err = np.array([bdiff, np.abs(a[:, :3] - b[:, :3]).max(),
                        np.abs(a[:, 3:6] - b[:, 3:6]).max(), np.abs(a[:, 7:] - b[:, 7:]).max()])
        check((err <= [1e-6, 1e-6, 1.6e-5, .0015]).all(), f'{car}/{x["name"]}: component error {err}')
        check(np.array_equal(xv[:, 24:28], pv[:, 24:28]), f'{car}: indexed colours differ')
        error = np.maximum(error, err)
        check(cc.marker_records(x) == cc.marker_records(p), f'{car}: markers differ')
        # PC can carry additional target metadata absent from the X360 pack.
        check(not (Counter(x['extras']) - Counter(p['extras'])), f'{car}: shared ancillary bytes differ')
        totals['markers'] += len(cc.marker_records(x))
        totals['solids'] += 1
        totals['draws'] += len(x['records'])
        totals['triangles'] += len(x['indices']) // 3
        totals['xvertices'] += len(x['vertices'])
        totals['pcvertices'] += len(p['vertices'])
    print(f'{car}: ' + ' '.join(f'{k}={v}' for k, v in totals.items()) +
          f' max_error(bounds,position,normal,uv)={error.tolist()}', flush=True)
    textures = cc.texture_blobs(read('TEXTURES'))
    reference = dict(cc.texture_blobs((directory / 'TEXTURES.BIN').read_bytes()))
    check({h for h, _ in textures} == reference.keys(), f'{car}: texture identities differ')
    errors, formats, dxn_alpha = [], {}, []
    for hs, raw in textures:
        r, pl, pixels = cx.texture_entry(hs, raw)
        p = reference[hs]
        pr, ppl, pp = p[-148:-24], p[-24:], p[:-148]
        w, h = cc.words(r, 40, '<HH')
        pw, ph = cc.words(pr, 40, '<HH')
        check((w, h) == (pw, ph), f'{car}: texture dimensions differ')
        fmt = cc.u32(raw[-32:], 20)
        formats[f'{fmt:08X}'] = formats.get(f'{fmt:08X}', 0) + 1
        kind, original_top = cx.linear_top(fmt, w, h, raw[:-156])
        if kind.startswith('DXT'):
            check(original_top == pp and pixels == pp, f'{car}: DXT top bytes differ')
        a = cx.decode_image(pl[12:16].decode('ascii'), w, h, pixels)
        b = cx.decode_image(ppl[12:16].decode('ascii'), pw, ph, pp)
        def reduce(image):
            return np.asarray(Image.fromarray(image[:, :, :3]).resize((32, 32), Image.Resampling.BOX)).astype(float)
        mad = float(np.abs(reduce(a) - reduce(b)).mean())
        check(mad <= RGB_MAD, f'{car}/{cc.text_at(r, 89)}: RGB MAD {mad} > {RGB_MAD}')
        errors.append(mad)
        if kind == 'BC5':
            dxn_alpha.append(float(np.abs(a[:, :, 3].astype(float) - b[:, :, 3]).mean()))
    print(f'{car}: textures={len(textures)} formats={formats} max_RGB_MAD={max(errors):.6f}/{RGB_MAD}' +
          f' BC5_alpha_MAD={dxn_alpha} (opaque-alpha policy)', flush=True)
    return totals, formats


def main():
    pc, xenon, cars = (os.environ.get(k) for k in ('NFS_CARBON_PC', 'NFS_CARBON_X360', 'NFS_CARBON_CARS'))
    if not pc or not xenon or not cars or not Path(pc).is_dir() or not Path(xenon).exists():
        print('SKIP: supply NFS_CARBON_PC, NFS_CARBON_X360 and NFS_CARBON_CARS for local parity')
        return 3
    pc_root = Path(pc)
    if (pc_root / 'CARS').is_dir():
        pc_root /= 'CARS'
    selected = cars.split()
    if any(not (pc_root / car / 'GEOMETRY.BIN').is_file() or
           not (pc_root / car / 'TEXTURES.BIN').is_file() for car in selected):
        print('SKIP: selected PC car inputs are absent')
        return 3
    source = Source(xenon)
    try:
        logical = cs._logical(source, cs._physical(source))
        if any(cs.path_hash(f'CARS/{car}/{kind}.BIN') not in logical
               for car in selected for kind in ('GEOMETRY', 'TEXTURES')):
            print('SKIP: selected X360 car inputs are absent')
            return 3
        for car in selected:
            compare_car(car, pc_root, source, logical)
    finally:
        source.close()
    print(f'PASS: {len(selected)} cars; per-solid/per-draw triangles, bounds, indexed components and RGB')
    return 0


if __name__ == '__main__':
    if '--broken-tiling' in sys.argv:
        cx._tile_indices = lambda w, h, b: np.arange(w * h)
    raise SystemExit(main())
