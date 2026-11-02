#!/usr/bin/env python3
"""Native production HD swap + translated Init + production gpu_tex regression.

Pinned ORIGINAL 13984ab7: ticker 4/10 (10880/11016 bytes), bank 5/10
(21824/21896 bytes), completeness check exit 1. Fixed: 10/10, exact DDS
bytes through 1x1. Quality drops 1/2, malformed fallback, repeated release,
allocation rollback, poisoned free/ownership, and same-size UI policy are checked too. Private DDS absent -> exit 3.
History-free control: deny all git subprocesses with fixtures present;
ca4a7595 exits 1 at its fixed-commit lookup (1 attempt), embedded body exits 0
(0 attempts), retaining the same 4/10 and 5/10 broken upload controls.
--cpu omits Metal; default reads back every production GPU mip including <4.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import struct
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Only the original swap body consumed by uiblur_test.build; no repository history.
BASELINE = r'''
    uint32_t ww = hx->ww, hh = hx->hh, mips = hx->mips, fmt = hx->fmt;
    // Same-size swaps improve the format, so retain the PC mip policy. Init
    // drops quality mips (00982AE0) whenever a chain is present: adding a chain
    // to one-level UI art otherwise turns a 512x512 atlas into 128x128 at detail 0.
    uint32_t pc_mips = M8(ti + 0x4E);
    if (!is_later && ww == M16(ti + 0x44) && hh == M16(ti + 0x46) && pc_mips && mips > pc_mips)
        if (!(diag_on && getenv("BOOT_DIAG_UI_ADDED_MIPS"))) mips = pc_mips; // test only: reproduce the added-chain loss
    int bpb = fmt == 0x31545844 ? 8 : fmt == 21 ? 0 : 16;
    uint32_t pcfmt = M32(pi + 0x14);
    int pc_alpha = pcfmt == 0x33545844 || pcfmt == 0x35545844 || pcfmt == 21 || pcfmt == 0x29;
    if (!fmt || ww > 4096 || hh > 4096 || !strncmp((const char *)G + ti + 0x0c, "DUMMY_SKIN", 10) || (pc_alpha && fmt == 0x31545844)) {
        if (raw) gfree(raw);
        if (is_later) { hx->state = HD_KEEP; goto hd_lookup; }
        f_006DDD90_orig(c); return;
    }
    // Init sizes level i as base >> 2i, which ignores the 4x4 block minimum: keep only levels with both sides >= 4
    uint32_t levels = 0, w = ww, h = hh;
    while (levels < mips && (fmt == 21 || (w >= 4 && h >= 4))) {
        // Init quarters byte sizes: stop a private rectangular RGBA chain before
        // one side clamps to 1 and that arithmetic would truncate a texel.
        if (is_later && fmt == 21 && (ww * hh * 4 >> (2 * levels)) != w * h * 4) break;
        ++levels; w = w > 1 ? w / 2 : 1; h = h > 1 ? h / 2 : 1;
    }
    uint32_t base = fmt == 21 ? ww * hh * 4 : (ww / 4) * (hh / 4) * (uint32_t)bpb, total = 0;
    for (uint32_t l = 0; l < levels; ++l) total += base >> (2 * l);
    if (!raw) raw = hd_read(hx->path, &size);               // a texture decided on an earlier load
    if (!raw || 128 + (size_t)total > size || (is_later && (!later_header(G + raw, size) || !later_pin(hx, G + raw, size)))) { if (raw) gfree(raw); if (is_later) { hx->state = HD_KEEP; goto hd_lookup; } f_006DDD90_orig(c); return; }
    // NFS_X360_TEXTURES_TONE=x360: a texture the converter regraded to the PC's tones was validated above in those
    // tones (same content); the X360's own grading of it is loaded instead
    for (int i = 0; !is_later && i < nhd_tone; ++i) {
        if (hd_tone[i].hash != hx->hash) continue;
        uint32_t tsize, traw = hd_read(hd_tone[i].path, &tsize);
        const uint8_t *gh = G + traw, *head = G + raw;      // same height, width, pitch, mip count and format
        if (traw && !memcmp(gh + 12, head + 12, 20) && !memcmp(gh + 80, head + 80, 8) && 128 + (size_t)total <= tsize) {
            gfree(raw); raw = traw; ++hd_tone_loaded;
        } else if (traw) gfree(traw);
        break;
    }
    uint32_t buf = raw + 128;
    // swap in, run Init, restore
    uint32_t s70 = M32(ti + 0x70), s40 = M32(ti + 0x40), s44 = M32(ti + 0x44), s4c = M32(ti + 0x4c), sfmt = M32(pi + 0x14);
    M32(ti + 0x70) = buf; M32(ti + 0x40) = base;
    M16(ti + 0x44) = (uint16_t)ww; M16(ti + 0x46) = (uint16_t)hh; M8(ti + 0x4e) = (uint8_t)levels;
    M32(pi + 0x14) = fmt;
    x360_hd_creating = 1;                                  // the texture Init creates is tagged (the probe's hdtex)
    f_006DDD90_orig(c);
    x360_hd_creating = 0;
    ui_diag_upload(ti, pi, raw, size);
    M32(ti + 0x70) = s70; M32(ti + 0x40) = s40; M32(ti + 0x44) = s44; M32(ti + 0x4c) = s4c; M32(pi + 0x14) = sfmt;
    gfree(raw);
    ++hd_loaded; hd_bytes += total;
    if (is_later && diag_on) {
        uint32_t bit = 1u << later_rule(hx->hash);
        if (!(hd_later_applied_mask & bit)) { hd_later_applied_mask |= bit; ++hd_later_loaded; }
        diag_event("later: overlay %08X applied=%u %ux%u levels=%u", hx->hash, hd_later_loaded, ww, hh, levels);
    }
    if (diag_on && (hd_loaded <= 20 || hd_loaded % 100 == 0))
        diag_event("x360: HD texture %08X %ux%u -> %ux%u, %u levels (%u loaded, %u in X360 tones, %u KB)", M32(ti + 0x24), s44 & 0xFFFF, s44 >> 16, ww, hh, levels, hd_loaded, hd_tone_loaded, hd_bytes >> 10);
}
'''
BASE_SHA256 = '1e3b9de0f5ccc2fec937ee1d879c4da68567201c82e113c355458684a65b83da'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', action='store_true')
    args = parser.parse_args()
    folder = ROOT.parent / 'x360/hd_textures_mips_alpha'
    cases = [('6ebf6f28', 4, 11016), ('ed2bd84a', 5, 21896)]
    if any(not list(folder.glob(h + '_*.dds')) for h, _, _ in cases):
        print('SKIP: private compressed sign DDS fixtures absent')
        return 3
    spec = importlib.util.spec_from_file_location('uiblur', ROOT / 'runtime/uiblur_test.py')
    replay = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay)
    baseline = BASELINE.encode()
    assert hashlib.sha256(baseline).hexdigest() == BASE_SHA256, 'pinned baseline changed'
    render = (ROOT / 'runtime/render.m').read_text()
    gpu = render.split('RenderTex *gpu_tex(Obj *t) {', 1)[1].split('// Static vertex/index buffers', 1)[0]
    release = (ROOT / 'runtime/d3d9.m').read_text().split('static uint32_t obj_release(Obj *o) {', 1)[1].split('\nstatic void dev_bind', 1)[0]
    with tempfile.TemporaryDirectory(prefix='hd-tail-') as directory:
        root = Path(directory)
        skipped = False
        for mode, source in [('original', baseline.decode()), ('fixed', None)]:
            tmp = root / mode; tmp.mkdir()
            (tmp / 'hd_gpu_generated.h').write_text('RenderTex *gpu_tex(Obj *t) {' + gpu + '\nstatic uint32_t obj_release(Obj *o) {' + release)
            (tmp / 'hd_tail_mode.h').write_text('#define HAVE_HD_TAIL_HELPERS ' + str(int(mode == 'fixed')) + '\n')
            exe = replay.build(tmp, source, 'hd_mip_tail_test.m')
            if mode == 'fixed':
                # A legal non-power-of-two chain whose quarter-size prefix ends
                # before both dimensions reach the original 4x4 cutoff.
                header = bytearray(next(folder.glob('6ebf6f28_*.dds')).read_bytes()[:128])
                struct.pack_into('<I', header, 12, 48)
                struct.pack_into('<I', header, 20, 512 // 4 * (48 // 4) * 8)
                levels = []; w, height = 512, 48
                for l in range(10):
                    levels.append(bytes([l + 31]) * (((w+3)//4)*((height+3)//4)*8))
                    w, height = max(1, w//2), max(1, height//2)
                npot = tmp / 'npot.dds'; npot.write_bytes(header + b''.join(levels))
                for drop in range(3):
                    p = replay.run([str(exe), str(npot), str(drop), str(sum(map(len, levels))),
                                    'cpu' if args.cpu else 'gpu', '0'], (0, 3))
                    print(f'fixed non-power-of-two drop={drop}: {p.stdout.strip()}', flush=True)
                    skipped |= p.returncode == 3
            for h, old, payload in cases:
                dds = next(folder.glob(h + '_*.dds'))
                for drop in range(3):
                    p = replay.run([str(exe), str(dds), str(drop), str(payload),
                                    'cpu' if args.cpu or mode == 'original' else 'gpu', '0'], (0, 1, 3))
                    print(f'{mode} {h} drop={drop}: {p.stdout.strip()}', flush=True)
                    if mode == 'original':
                        assert p.returncode == 1 and f'levels={old-drop}/' in p.stdout, 'original must fail completeness'
                    else:
                        assert p.returncode in (0, 3), p.stdout
                        skipped |= p.returncode == 3
                if mode == 'fixed':
                    broken = tmp / f'{h}-truncated.dds'; broken.write_bytes(dds.read_bytes()[:-1])
                    p = replay.run([str(exe), str(broken), '0', str(payload), 'cpu', '1'])
                    print(f'truncated {h}: {p.stdout.strip()}', flush=True)
        # Existing replay checks both same-size one-level UI replacements at drop 2.
        p = replay.run([sys.executable,
                        '-I', str(ROOT / 'runtime/uiblur_test.py'), '--cpu'], (0, 3))
        print(p.stdout.strip())
        skipped |= p.returncode == 3
        if skipped: return 3
    print('PASS: original rejected; complete DDS ' + ('CPU parity' if args.cpu else 'CPU/GPU parity') + ', drops 0/1/2, truncated fallback, 32 releases per case')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
