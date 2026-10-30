"""Bind-pose coverage regression, measured from the retail M06b bundles.

Old validator: MCOP02 accepted, despite 449/3140 PC vertices (14.299363%)
more than 0.1 m from X360, max 0.478986382 m. PERP01: 0/2071, max
0.034346201 m. Fixed: MCOP02 rejected and kept byte-for-byte; PERP01 swapped.
--old-converter restores the missing guard and must fail the output check.
All 26 bundles: 14 MCOP fallbacks, 12 FCOP and 26 PERP X360 replacements.
Private inputs absent: synthetic boundaries run, then exit 3.
"""
import argparse
import os
from pathlib import Path
import re
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools_snapshot'))
import x360_nis_arrest as arrest


def synthetic():
    # One uncovered vertex in twenty is allowed; two are rejected. All normals
    # agree, and X360 -> PC distances are zero, exposing the old one-way check.
    v = np.zeros((3, 15), np.float32)
    v[:, 3] = v[:, 9] = 1
    draws = [(bytes(256), v, np.array([0, 1, 2]))]
    pc = np.repeat(v[:1], 20, axis=0)
    pc[-1, 0] = .2
    assert not arrest.validate(draws, (-1, -1, -1, 1, 1, 1), pc)
    pc[-2, 0] = .2
    bad = arrest.validate(draws, (-1, -1, -1, 1, 1, 1), pc)
    assert len(bad) == 1 and 'PC coverage 2/20' in bad[0], bad
    pc[-2:, 0] = .1
    assert not arrest.validate(draws, (-1, -1, -1, 1, 1, 1), pc)


def solid_bytes(data, solid):
    off, size = solid['solid']
    return data[off:off + 8 + size]


def triangle_count(data, solid):
    return struct.unpack_from('<I', data, solid[0x134011][0] + 8 + 28)[0]


def check_bundle(job):
    pc_path, x_path, old = job
    pc, xbox = pc_path.read_bytes(), x_path.read_bytes()
    ps, xs = arrest.solids(pc), arrest.solids(xbox)
    if 'M06b_' in pc_path.name:
        for name, expected in [('MCOP02', (449, 3140, .478986382)),
                               ('PERP01', (0, 2071, .034346201))]:
            draws = arrest.x360_mesh(xbox, xs[name])
            pv = arrest.pc_vertices(pc, ps[name])
            count, total, maximum = arrest.pc_coverage(draws, pv)
            assert (count, total) == expected[:2], (name, count, total)
            assert abs(maximum - expected[2]) < 1e-7, (name, maximum)
            bbox = struct.unpack_from('<3f4x3f', xbox, xs[name][0x134011][0] + 48)
            bad = arrest.validate(draws, bbox, pv)
            assert bool(bad) == name.startswith('MCOP'), (name, bad)
    if old:
        original = arrest.pc_coverage
        arrest.pc_coverage = lambda draws, pc_v: (0, len(pc_v), 0.)
    lines = []
    try:
        output, textures, swaps = arrest.convert_bundle(pc, xbox, lines.append)
    finally:
        if old:
            arrest.pc_coverage = original
    emitted = arrest.solids(output)
    fallbacks = 0
    wanted_textures = set()
    for name, solid in ps.items():
        if name.startswith('MCOP') or name == 'CUFFS01':
            assert solid_bytes(output, emitted[name]) == solid_bytes(pc, solid), f'{pc_path.name} {name}: PC solid replaced'
            if name.startswith('MCOP'):
                fallbacks += 1
                assert sum(f'{name}: PC fallback;' in line for line in lines) == 1, lines
        elif name.startswith('FCOP') or name == 'PERP01':
            assert triangle_count(output, emitted[name]) == triangle_count(xbox, xs[name]), (pc_path.name, name)
            assert solid_bytes(output, emitted[name]) != solid_bytes(pc, solid)
            off, size = emitted[name][0x134012]
            wanted_textures.update(struct.unpack_from(f'<{size // 4}I', output, off + 8)[::2])
    assert {arrest.alias(h) for h in textures} == wanted_textures
    assert swaps == 2 - fallbacks, (pc_path.name, swaps)
    # All original non-solid top-level chunks survive at their loader alignment.
    original_chunks = [(cid, off % 4096, pc[off:off + 8 + size])
                       for depth, cid, off, size in arrest.walk(pc, 0, len(pc))
                       if depth == 0 and cid not in (0, 0x80134000)]
    output_chunks = [(cid, off % 4096, output[off:off + 8 + size])
                     for depth, cid, off, size in arrest.walk(output, 0, len(output))
                     if depth == 0 and cid not in (0, 0x80134000)]
    assert output_chunks == original_chunks, pc_path.name
    return fallbacks, swaps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-converter', action='store_true')
    args = parser.parse_args()
    synthetic()
    pc = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'NIS'
    xbox = Path(os.environ.get('NFS_X360_FILES', ROOT.parent / 'x360/files')) / 'NIS'
    if not pc.is_dir() or not xbox.is_dir():
        print('SKIP: coverage boundaries pass; private arrest bundles absent')
        return 3
    xpaths = {p.name.upper(): p for p in xbox.iterdir()}
    paths = sorted(p for p in pc.iterdir() if re.fullmatch(r'Scene_Arrest[FM]\d+b?_BundleB\.bun', p.name, re.I))
    assert len(paths) == 26, len(paths)
    paths.sort(key=lambda p: 'M06b_' not in p.name)
    jobs = [(p, xpaths[p.name.upper()], args.old_converter) for p in paths]
    results = list(arrest.huff.ordered_map(check_bundle, jobs))
    assert sum(f for f, _ in results) == 14
    assert sum(s for _, s in results) == 38
    print('PASS: 26 bundles, 14 MCOP PC fallbacks, 12 FCOP + 26 PERP X360; original chunks/alignment and texture references checked')
    return 0


if __name__ == '__main__':
    sys.exit(main())
