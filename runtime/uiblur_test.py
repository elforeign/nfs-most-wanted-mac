#!/usr/bin/env python3
"""Full-resolution UI sharpness regression: real Init + production swap + Metal.

The added-mip control must fail against UI-off at quality drop 2. The production
path must keep >=90% of UI-off edge energy for both the main-menu title and HUD
backing, drawn at 3440x2160 using recorded UI quads and linear/mip-linear sampling.
Guarded 3440x2160 game title crop: edge energy UI-off 0.01640455,
broken 0.00514851 (0.3138x, FAIL), fixed 0.01622991 (0.9894x, PASS).
Upper speedometer crop: UI-off 0.00785581, broken 0.00220222 (0.2803x,
FAIL), fixed 0.00820911 (1.0450x, PASS), with world detail held at 0.
The HUD replay uses the minimap ring; speedometer game crops cover that art too.
Private PC/X360 assets absent -> exit 3.

--cpu checks actual recompiled uploads without requiring a Metal device.
--output DIR keeps the full-resolution replay crops for inspection.
--game-crops takes six full-size PNGs: broken/off/fixed menu, then broken/off/fixed
race; it checks those captured crops with the same sharpness threshold.
"""
import argparse
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# Opposite corners of the white "main menu" glyph quads: FE position x/y,
# atlas u/v. The title's shadow is excluded to measure its own edges.
TITLE = (
    (.418906420, -.406458348, .756835938, .030273438, .497656375, -.477708250, .813476562, .069335938),
    (.503281415, -.406458348, .721679688, .030273438, .551093876, -.477708250, .756835938, .069335938),
    (.551093876, -.380208284, .413085938, .071289062, .584843874, -.477708250, .438476562, .124023438),
    (.584843874, -.406458348, .854492188, .030273438, .641093850, -.477708250, .895507812, .069335938),
    (.669218838, -.406458348, .756835938, .030273438, .747968912, -.477708250, .813476562, .069335938),
    (.753593862, -.406458348, .930664062, .030273438, .795781374, -.477708250, .961914062, .069335938),
    (.795781374, -.406458348, .854492188, .030273438, .852031350, -.477708250, .895507812, .069335938),
    (.852031350, -.406458348, .813476562, .030273438, .908281326, -.477708250, .854492188, .069335938),
)
# Two rotated METER_BACKING quads from the minimap, including half-texel UVs.
# Its WorldViewProj scales x by .75 and y by 43/48 at this resolution.
HUD = (
    ((-1.39422190, -.656420708, .00390625, .00390625),
     (-1.00768459, -1.01729596, .99609375, .00390625),
     (-.737028122, -.501912773, .99609375, .99609375),
     (-1.12356544, -.141037554, .00390625, .99609375)),
    ((-.734009862, -.542475462, .00390625, .00390625),
     (-1.09548724, -.138054967, .99609375, .00390625),
     (-1.39880264, -.620024681, .99609375, .99609375),
     (-1.03732526, -1.02444518, .00390625, .99609375)),
)
CASES = (
    ('title', 'FRONTEND/FrontB.lzc', 0xE0463AFA, TITLE, (2300, 1470, 760, 160), (2160 / 3440 * 4 / 3, 1)),
    ('hud', 'GLOBAL/HUDTEXRACE.BIN', 0x20865C0A, HUD, (0, 1180, 800, 940), (.75, 43 / 48)),
)


def run(cmd, expected=(0,)):
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode in expected, f'{cmd[0]} exit {p.returncode}:\n{p.stdout[-2000:]}\n{p.stderr[-3000:]}'
    assert 'AddressSanitizer' not in p.stderr and 'runtime error:' not in p.stderr, p.stderr[-3000:]
    return p


def build(tmp):
    guest = (ROOT / 'recomp/out/speed_41.c').read_text()
    original = re.search(r'void f_006DDD90_orig\(Cpu \*restrict c\) \{.*?\n\}', guest, re.S)[0]
    src = (ROOT / 'runtime/x360_textures.m').read_text()
    tail = src.split('    uint32_t ww = hx->ww, hh = hx->hh, mips = hx->mips, fmt = hx->fmt;', 1)[1]
    tail = '    uint32_t ww = hx->ww, hh = hx->hh, mips = hx->mips, fmt = hx->fmt;' + tail
    wrapper = ('\nstatic void replay_swap(Cpu *c, HdTex *hx, int is_later) {\n'
               'uint32_t ti=c->ecx, pi=M32(ti), raw=0, size=0;\n'
               'if (0) { hd_lookup: abort(); }\n' + tail)
    (tmp / 'uiblur_generated.h').write_text(original + wrapper)
    exe = tmp / 'uiblur'
    run(['clang', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
         '-DRECOMP_ABICHECK', '-fobjc-arc', '-I', str(ROOT / 'recomp'), '-I', str(tmp),
         str(ROOT / 'runtime/uiblur_test.m'), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe)])
    return exe


def fixture(game, rel, wanted, path):
    # The bounded native pack decoders are shared with asset generation.
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    from x360_tex import textures_in, unpack_file
    from texture_compare import arrays
    data = unpack_file((game / rel).read_bytes())
    record = next(e for e, _ in arrays(data) if struct.unpack_from('<I', e, 0x24)[0] == wanted)
    palettes = {}
    tex = next(t for t in textures_in(data, 0x80, palettes) if t[1] == wanted)
    _, _, w, h, fmt, pixels = tex
    base = struct.unpack_from('<I', record, 0x40)[0]
    assert record[0x4E] == 1 and len(pixels) >= base
    pal = palettes.get(wanted, b'')
    path.write_bytes(struct.pack('<6I', w, h, fmt, record[0x4E], base, len(pal)) + pixels[:base] + pal)
    return w, h


def vertices(path, quads, crop, scale):
    values = []
    for q in quads:
        if len(q) == 8:
            x0, y0, u0, v0, x1, y1, u1, v1 = q
            q = [(x0, y0, u0, v0), (x1, y0, u1, v0), (x1, y1, u1, v1), (x0, y1, u0, v1)]
        for i in (0, 1, 2, 0, 2, 3):
            x, y, u, v = q[i]
            values.extend((x * scale[0], y * scale[1], u, v))
    path.write_bytes(struct.pack('<5I', len(values) // 4, *crop) + struct.pack(f'<{len(values)}f', *values))


def edge_energy(pixels):
    # Squared first differences penalize broad/smeared edges. Normalize by
    # foreground intensity so minor DXT alpha/colour changes do not decide it.
    y = pixels[..., :3].astype(np.float64).mean(axis=2) / 255
    if y.sum() < 1e-8: return 0.0
    return (np.square(np.diff(y, axis=0)).sum() + np.square(np.diff(y, axis=1)).sum()) / y.sum()


def compare(name, images):
    reference = images['pc'][..., :3]
    assert reference.max() > 128 and reference.sum() > 100 * 255, 'empty/dark UI-off crop'
    energies = {k: edge_energy(v) for k, v in images.items()}
    good, bad = energies['fixed'] / energies['pc'], energies['broken'] / energies['pc']
    print(f'{name} edge energy: UI-off={energies["pc"]:.8f}, fixed={energies["fixed"]:.8f} ({good:.4f}), added-mip control={energies["broken"]:.8f} ({bad:.4f})')
    assert good >= .90, f'{name}: production UI lost sharpness ({good:.4f} < .90)'
    assert bad < .90, f'{name}: deliberately broken upload was not rejected ({bad:.4f})'


def game_crops(paths):
    for name, files, box in (('title capture', paths[:3], (2300, 1470, 3060, 1630)),
                             ('speedometer capture', paths[3:], (2850, 1380, 3370, 1620))):
        images = {}
        for mode, path in zip(('broken', 'pc', 'fixed'), files):
            with Image.open(path) as im:
                assert im.size == (3440, 2160), f'{path}: needs a full-resolution PNG'
                images[mode] = np.asarray(im.convert('RGB').crop(box))
        compare(name, images)
    print('PASS: full-resolution game crops; both broken controls rejected')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--game-crops', nargs=6, type=Path, metavar='PNG')
    args = parser.parse_args()
    if args.game_crops: return game_crops(args.game_crops)
    game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    hd = ROOT.parent / 'x360/hd_textures_ui'
    if any(not (game / rel).is_file() or not list(hd.glob(f'{h:08x}_*.dds')) for _, rel, h, _, _, _ in CASES):
        print('SKIP: needs the PC packs and same-size X360 UI DDS set')
        return 3
    with tempfile.TemporaryDirectory(prefix='uiblur-') as directory:
        tmp = Path(directory)
        exe = build(tmp)
        skipped = False
        for name, rel, h, quads, crop, scale in CASES:
            pc = tmp / f'{name}.pc'
            w, height = fixture(game, rel, h, pc)
            dds = next(hd.glob(f'{h:08x}_*.dds'))
            verts = tmp / f'{name}.vertices'; vertices(verts, quads, crop, scale)
            images = {}
            for mode in ('pc', 'fixed', 'broken'):
                out = tmp / f'{name}_{mode}.rgba'
                p = run([str(exe), str(pc), str(dds), str(verts), '-' if args.cpu else str(out), mode, '2'], (0, 3))
                print(f'{name} {mode}: {p.stdout.strip()}', flush=True)
                match = re.search(r'upload (\d+)x(\d+) levels=(\d+)', p.stdout)
                dims = tuple(map(int, match.groups()))
                assert dims[:2] == ((w // 4, height // 4) if mode == 'broken' else (w, height)), dims
                if mode != 'broken': assert dims[2] == 1, dims
                if p.returncode == 3: skipped = True; continue
                if args.cpu: continue
                images[mode] = np.frombuffer(out.read_bytes(), np.uint8).reshape(crop[3], crop[2], 4)
                if args.output:
                    args.output.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(images[mode]).save(args.output / f'{name}_{mode}.png')
            if images:
                compare(name, images)
        if skipped: return 3
    print('PASS: full-resolution UI' if not args.cpu else 'PASS: native Init upload policy; Metal replay not requested')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
