#!/usr/bin/env python3
"""Offline A8 proof, no game/device/Unicorn. Requires recomp/out and retail assets.
Run: $TOOLS_PY -I runtime/x360reverb_test.py
Scratch (including ten-second WAVs, float responses and failure logs) is retained and printed.
The three deliberately broken production merges must exit nonzero. See README for measured results.
"""
from pathlib import Path
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent
PC = ASSETS / 'Need For Speed Most Wanted Black Edition'
X = ASSETS / 'x360/files'
KEYS = [0xAAB5B207, 0x3A0F2F2C, 0x8F11660F, 0xBFFFF2FA]
NAMES = ['Alley', 'Hills', 'Simple_Tunnel', 'Simple_Tunnel_Sml']
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp


def run(args, *, env=None, broken=False):
    p = subprocess.run([str(a) for a in args], env=env, text=True, capture_output=True)
    assert (p.returncode != 0) == broken, f'exit={p.returncode}: {p.stdout[-2500:]} {p.stderr[-2500:]}'
    assert 'runtime error:' not in p.stderr and 'ERROR: AddressSanitizer' not in p.stderr, p.stderr[-2500:]
    return p


def entries(b):
    return {key: (b[off:off + size], size, size2, meta)
            for key, off, size, size2, meta in
            (struct.unpack_from('<5I', b, 16 + 20 * i) for i in range(struct.unpack_from('<I', b, 12)[0]))}


def variants(b):
    widths = {0: 4, 1: 8, 3: 4, 6: 4, 7: 8, 9: 4}
    counts = struct.unpack_from('>12I', b, 8)
    pos, result = 56, {}
    for ch in range(1, 7):
        start = pos
        for _ in range(counts[ch - 1]):
            pos += widths[b[pos]]
        pos += 12 * counts[ch + 5]
        if counts[ch - 1]:
            result[ch] = b[start:pos]
    assert pos == len(b) == struct.unpack_from('>I', b, 4)[0]
    return result


def selected(b, channels):
    v = variants(b)
    return v[max(ch for ch in v if ch <= channels)]


def translated(scratch):
    pe = recomp.PE()
    image = bytearray(0x1000000)
    for _, _, va, size, raw in pe.sections:
        at = pe.base + va
        image[at:at + size] = pe.data[raw:raw + size]
    (scratch / 'image.bin').write_bytes(image)
    # Hooked functions (recomp/hooks.txt) are emitted as f_XXXXXXXX_orig plus a weak f_XXXXXXXX wrapper; the test wants the
    # original under its plain name (here 00820DA3, wrapped for diagnostics by x360audio.c).
    source = re.sub(r'\bf_([A-F0-9]{8})_orig\b', r'f_\1', (ROOT / 'recomp/out/speed_55.c').read_text())
    bodies = dict(re.findall(r'void f_([A-F0-9]{8})\(Cpu \*restrict c\) (\{.*?\n})', source, re.S))
    # Include node processors called indirectly through graph objects, plus direct dependency closure.
    pending = ['00821537', '008215FC', '00820DA3', '008251F0'] + [
        a for a in bodies if 0x82444F <= int(a, 16) <= 0x824F60]
    used, stubs = set(), {'0081E9E7', '0081EAE9'}
    while pending:
        a = pending.pop()
        if a in used or a in stubs:
            continue
        assert a in bodies, a
        used.add(a)
        pending += re.findall(r'f_([A-F0-9]{8})\(c\)', bodies[a])
    assert {'008215FC', '00821108', '00820DA3'} <= used
    generated = '#include "runtime.h"\n#include <stdio.h>\n#include <stdlib.h>\n'
    generated += '\n'.join(f'void f_{a}(Cpu *c);' for a in sorted(used | stubs))
    generated += '\n' + '\n'.join(f'void f_{a}(Cpu *restrict c) {bodies[a]}' for a in sorted(used))
    generated += '\nvoid dispatch(Cpu *c, uint32_t target) { switch(target) {\n'
    generated += '\n'.join(f'case 0x{a}: f_{a}(c); return;' for a in sorted(used | stubs))
    generated += '\ndefault: fprintf(stderr,"unexpected DSP dispatch %08X\\n",target); exit(1); }}\n'
    original = scratch / 'original.c'
    original.write_text(generated)
    print(f'translated retail closure: {len(used)} functions (DSP, parser, mixer); PE constants loaded', flush=True)
    return original


def build(scratch, original, label='good', source=None):
    # Include the production loader in the harness, as x360music_test.c does. Mutations
    # change that same source at the actual merge point, leaving reference assets intact.
    harness = (ROOT / 'runtime/x360reverb_test.c').read_text()
    if source is not None:
        loader = scratch / f'{label}_loader.c'
        loader.write_text(source)
        harness = harness.replace('"x360audio.c"', f'"{loader}"')
    cfile, binary = scratch / f'{label}.c', scratch / label
    cfile.write_text(harness)
    p = run(['clang', '-O1', '-g', '-std=c11', '-D_DARWIN_C_SOURCE',
             '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
             '-I', ROOT / 'recomp', '-I', ROOT / 'runtime', cfile,
             ROOT / 'runtime/settings.c', ROOT / 'runtime/offline_stubs.c', original, '-o', binary])
    (scratch / f'{label}_build.log').write_text(p.stdout + p.stderr)
    return binary


def wav(path, a, rate):
    # Guest panner order (posaudio.h) is C FR SR SL FL LFE. Listening WAVs use
    # conventional FL FR C LFE SL SR; float proof files retain guest slot order.
    if a.shape[1] == 6:
        a = a[:, [4, 1, 0, 5, 3, 2]]
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(a.shape[1]); f.setsampwidth(2); f.setframerate(rate)
        assert np.max(np.abs(a)) < 1
        f.writeframes(np.rint(a * 32767).astype('<i2').tobytes())


def response(binary, scratch, label, fx, rate, channels):
    data = scratch / f'{label}.fx'; data.write_bytes(fx)
    raw = scratch / f'{label}_{rate}_{channels}ch.f32'
    p = run([binary, 'render', scratch / 'image.bin', data, raw, rate, channels])
    a = np.fromfile(raw, dtype='<f4').reshape(-1, channels)
    assert a.shape == (rate * 10, channels) and np.isfinite(a).all()
    energy = np.square(a.astype('float64')).sum(axis=1)
    total = energy.sum()
    assert total > 1e-8 and np.max(np.abs(a)) < 1
    assert energy[-rate:].sum() < total * 1e-10, f'{label}: tail not decayed'
    tail = np.cumsum(energy[::-1])[::-1]
    t60 = np.flatnonzero(tail <= total * 1e-6)[0] / rate
    first = [int(np.flatnonzero(np.abs(a[:, c]) > 1e-9)[0])
             if np.any(np.abs(a[:, c]) > 1e-9) else -1 for c in range(channels)]
    wav(raw.with_suffix('.wav'), a, rate)
    return a, (float(total), t60, first), p.stdout.strip()


def main():
    scratch = Path(tempfile.mkdtemp(prefix='nfsmw_reverb_'))
    print(f'scratch retained: {scratch}', flush=True)
    original = translated(scratch)
    binary = build(scratch, original)
    env = {k: v for k, v in os.environ.items() if k not in
           ['NFS_X360_AUDIO', 'NFS_X360_REVERB', 'NFS_X360_MUSIC', 'REVERB_TEST_PREF']}
    for extra, expected in [({}, 1), ({'NFS_X360_REVERB': 'pc'}, 0), ({'REVERB_TEST_PREF': 'pc'}, 0),
                            ({'NFS_X360_REVERB': 'x360'}, 1), ({'REVERB_TEST_PREF': 'x360'}, 1),
                            ({'NFS_X360_REVERB': 'pc', 'REVERB_TEST_PREF': 'x360'}, 0)]:
        run([binary, 'settings', expected], env=dict(env, **extra))
    print('settings: X360 default, pc/x360 env, saved value, env precedence, Audio order 65, restart passed', flush=True)
    pcpath, xpath = PC / 'GLOBAL/InGameMemoryFile.bin', X / 'GLOBAL/INGAMEMEMORYFILE.BIN'
    pb, xb = pcpath.read_bytes(), xpath.read_bytes()
    pc, ref = entries(pb), entries(xb)
    merged_paths = {}
    for mix, reverb in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        out = scratch / f'merged_{mix}_{reverb}.bin'
        p = run([binary, 'merge', pcpath, xpath, out, mix, reverb])
        (scratch / f'merge_{mix}_{reverb}.log').write_text(p.stdout + p.stderr)
        merged_paths[mix, reverb] = out
        m = entries(out.read_bytes())
        assert m.keys() == pc.keys()
        for key, (payload, size, size2, meta) in m.items():
            assert size == size2 == len(payload) and meta == pc[key][3]
            if reverb and key in KEYS:
                assert size == [468, 444, 420, 256][KEYS.index(key)]
            elif mix and size == 9996:
                expected = np.frombuffer(ref[key][0], dtype='>u4').astype('<u4').tobytes()
                assert payload == expected
            else:
                assert payload == pc[key][0]
        run([binary, 'serve', PC, out, int(mix or reverb), mix, reverb],
            env=dict(env, NFS_X360_AUDIO='on' if mix else 'off',
                     NFS_X360_REVERB='x360' if reverb else 'pc', NFS_X360_FILES=str(X)))
    merged = entries(merged_paths[0, 1].read_bytes())
    count = 0
    for key in KEYS[:3]:
        for channels in [1, 2, 4, 6]:
            expected = pc[key][0] if channels < 4 else ref[key][0]
            assert selected(merged[key][0], channels) == selected(expected, channels)
            count += 1
    assert merged[KEYS[3]][0] == ref[KEYS[3]][0]
    print(f'{count} exact selected-payload comparisons (1/2/4/6ch); small-tunnel X360 blob exact; all 25 directory entries validated', flush=True)
    # Missing and malformed extracts keep the PC file. Test the real open hook, not just the merge helper.
    fixture = scratch / 'extract/GLOBAL'; fixture.mkdir(parents=True)
    serve_env = dict(env, NFS_X360_AUDIO='off', NFS_X360_REVERB='x360', NFS_X360_FILES=str(fixture.parent))
    for label, bad in [('missing', None), ('truncated', xb[:-1]),
                       ('offset', xb[:20] + b'\xff' * 4 + xb[24:])]:
        if bad is not None:
            (fixture / 'INGAMEMEMORYFILE.BIN').write_bytes(bad)
        p = run([binary, 'serve', PC, merged_paths[0, 1], 0, 0, 1], env=serve_env)
        (scratch / f'fallback_{label}.log').write_text(p.stdout)
    # A present extract with damaged FX bytes must also fail validation before the guest sees it.
    for label, off, data in [('fx_length', 4, b'\xff' * 4), ('fx_node', 56, b'\xff'),
                             ('fx_edge', 56 + 80, b'\xff' * 4)]:
        damaged = bytearray(xb)
        directory = struct.unpack_from('<5I', xb, 16 + 20 * 5)
        assert directory[0] == KEYS[0]
        damaged[directory[1] + off:directory[1] + off + len(data)] = data
        (fixture / 'INGAMEMEMORYFILE.BIN').write_bytes(damaged)
        p = run([binary, 'serve', PC, merged_paths[0, 1], 0, 0, 1], env=serve_env)
        (scratch / f'fallback_{label}.log').write_text(p.stdout)
    print('loader: independent mix/reverb combinations, missing/truncated/invalid extract PC fallback passed', flush=True)
    results = []
    for rate in [44100, 48000]:
        for key, name in zip(KEYS, NAMES):
            pc_a, pc_stats, _ = response(binary, scratch, name + '_PC', pc[key][0], rate, 6)
            ref_a, ref_stats, _ = response(binary, scratch, name + '_X360', ref[key][0], rate, 6)
            merged_a, stats, _ = response(binary, scratch, name + '_merged', merged[key][0], rate, 6)
            assert np.array_equal(ref_a, merged_a), f'{name}: merged/reference sample mismatch'
            assert not np.array_equal(pc_a, merged_a), f'{name}: PC response unchanged'
            assert abs(pc_stats[0] - stats[0]) > 1e-5
            # Independently predicted channel onsets from the authored delays and retail prime quantization.
            # Guest slots are C/FR/SR/SL/FL/LFE: quad label 4 reaches FL at t=0; LFE stays silent.
            expected_first = ([1424, 1424, 4288, 2864, 0, -1] if rate == 44100 else
                              [1552, 1552, 4640, 3088, 0, -1]) if name in ['Hills', 'Simple_Tunnel'] else [0, 0, 0, 0, 0, -1]
            assert stats[2] == expected_first, f'{name}: incorrect channel timing {stats[2]}'
            if name == 'Alley':
                assert stats[1] > pc_stats[1] * 1.5
            elif name in ['Hills', 'Simple_Tunnel']:
                assert stats[1] < pc_stats[1] * 0.9
            else:
                assert stats[1] == pc_stats[1] and stats[0] < pc_stats[0] * 0.7
            # Exercise the native quad selector directly as well as its six-channel remapping.
            rq, _, _ = response(binary, scratch, name + '_X360', ref[key][0], rate, 4)
            mq, _, _ = response(binary, scratch, name + '_merged', merged[key][0], rate, 4)
            assert np.array_equal(rq, mq), f'{name}: quad sample mismatch'
            if name != 'Simple_Tunnel_Sml':
                for channels in [1, 2]:
                    pa, _, _ = response(binary, scratch, name + '_fallback_PC', pc[key][0], rate, channels)
                    ma, _, _ = response(binary, scratch, name + '_fallback_merged', merged[key][0], rate, channels)
                    assert np.array_equal(pa, ma), f'{name}: {channels}ch fallback mismatch'
            results.append(f'{name} {rate}: energy PC={pc_stats[0]:.9f} X360={stats[0]:.9f}; '
                           f'T60 PC={pc_stats[1]:.5f}s X360={stats[1]:.5f}s; first X360={stats[2]}')
            print(results[-1], flush=True)
    # Output routing proof uses the same production renderer but independent raw X360 labels.
    # All six output channels must match sample-for-sample, including silence/duplication/timing.
    source = (ROOT / 'runtime/x360audio.c').read_text()
    mutations = {
        'small_gain': ('if (k == 3) memcpy(out + pos, xp[k], len);',
                       'if (k == 3) { memcpy(out + pos, xp[k], len); out[pos+0xA2]=1; out[pos+0xA3]=0; out[pos+0xAA]=1; out[pos+0xAB]=0; }'),
        'tunnel_labels': ('++merged;', '''if (k == 2) {
            size_t p = pos + 56;
            static const unsigned w[] = {4,8,0,4,0,0,4,8,0,4};
            for (unsigned ch = 0; ch < 6; ++ch) {
                for (unsigned j = 0; j < be32(out + pos + 8 + ch*4); ++j) {
                    if (ch == 3) { if (out[p+1] == 1) out[p+1] = 4; else if (out[p+1] == 4) out[p+1] = 1; }
                    p += w[out[p]];
                }
                p += 12 * be32(out + pos + 32 + ch*4);
            }
        }
        ++merged;'''),
        'omit_quad': ('''store_be32(out + pos + 4, len);
                memcpy(out + pos + 8 + 3 * 4, xp[k] + 8 + 3 * 4, 4);
                memcpy(out + pos + 32 + 3 * 4, xp[k] + 32 + 3 * 4, 4);''',
                      'len -= extra; store_be32(out + pos + 4, len);'),
    }
    for label, (old, new) in mutations.items():
        assert source.count(old) == 1, (label, source.count(old))
        broken = build(scratch, original, label, source.replace(old, new))
        badpath = scratch / f'{label}.bin'
        run([broken, 'merge', pcpath, xpath, badpath, 0, 1])
        key = KEYS[3] if label == 'small_gain' else KEYS[2]
        badfx = scratch / f'{label}_proof.fx'; badfx.write_bytes(entries(badpath.read_bytes())[key][0])
        # A separate proof process must exit nonzero on the damaged rendered response.
        code = '''import numpy as np,sys
want=np.fromfile(sys.argv[1],dtype='<f4'); got=np.fromfile(sys.argv[2],dtype='<f4')
assert np.array_equal(want,got), f"sample mismatch: {np.count_nonzero(want != got)} samples, max abs {np.max(np.abs(want-got))}"
'''
        rendered = scratch / f'{label}_proof.f32'
        run([binary, 'render', scratch / 'image.bin', badfx, rendered, 48000, 6])
        reference = scratch / f'{NAMES[KEYS.index(key)]}_X360_48000_6ch.f32'
        p = run([sys.executable, '-I', '-c', code, reference, rendered], broken=True)
        (scratch / f'{label}_FAIL.log').write_text(p.stdout + p.stderr)
        print(f'BROKEN {label}: rejected, exit={p.returncode}; {p.stderr.strip().splitlines()[-1]}', flush=True)
    (scratch / 'results.txt').write_text('\n'.join(results) + '\nPASS; three broken mappings rejected\n')
    print(f'PASS: production DSP equality, finite bounded/decayed tails, energy changes, PC fallbacks; WAVs: {scratch}', flush=True)


if __name__ == '__main__':
    main()
