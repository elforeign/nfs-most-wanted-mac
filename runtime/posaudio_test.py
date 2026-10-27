#!/usr/bin/env python3
"""Offline production panner tests. No game process or audio device.
Requires recomp/out from build_all.py. Uses the retail PE's constants and the
translated original table builder/lookup, not a second implementation of them.
The deliberately broken sine gain must fail power/continuity checks.
Engine sound: Retail byte-identical to the original for the player and an AI engine; Realistic shares for front-mid,
front, mid and rear engines in the hood (camera 0.45 m ahead) and chase cameras, power within 0.5 dB. Player's Rear Car Pos
bank voices: moved to the four wheels in Realistic (hood, chase, abeam), unchanged for Retail (also with
BOOT_DIAG on), another word, a stream caller, a stale update; power within 0.5 dB. Pass: 3473747 / 3408211 checks
(game / itu), 0 failed; broken variants each FAIL (itu): bad sine 523932, point source 8, 911 engine in front 1, power
+0.97 dB 23, Realistic leaking into Retail 7, AI point source 3, AI leaking into Retail (crash), angle self-check removed 3,
rear voices leaking into Retail 3, wheels at the car centre 2, rear voice power +1.1 dB 3.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp


def main():
    with tempfile.TemporaryDirectory(prefix='posaudio-') as tmp:
        scratch = Path(tmp)
        pe = recomp.PE()
        image = bytearray(0x1000000)
        for _, _, va, size, raw in pe.sections:
            at = pe.base + va
            image[at:at + size] = pe.data[raw:raw + size]
        (scratch / 'image.bin').write_bytes(image)
        all_code = (ROOT / 'recomp/out/speed_55.c').read_text()
        addresses = ['0081FB38', '0081FB87', '0081FE64', '00824349', '008243C5']
        bodies = []
        for address in addresses:
            m = re.search(r'void f_' + address + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n}', all_code, re.S)
            assert m, address
            body = m[0]
            if address == '0081FE64':
                body = body.replace('f_0081FE64(', 'f_0081FE64_orig(', 1)
            bodies.append(body)
        prototypes = '\n'.join('void f_' + a + '(Cpu *c);' for a in addresses)
        (scratch / 'original.c').write_text('#include "runtime.h"\n' + prototypes + '''
void f_0081BCCB(Cpu *c) { c->esp += 4; }
void f_0081BCD9(Cpu *c) { c->esp += 4; }
void f_0081E9E7(Cpu *c) { c->eax = 0xC00000; c->esp += 4; }
''' + '\n'.join(bodies))
        # Each mutation must FAIL the checks: a bad sine gain; Engine sound Realistic as a point source (no spread);
        # the 911 Turbo's engine in front; loudness not preserved; Realistic leaking into Retail.
        mutations = [None,
                     ('(float)sin(t * (M_PI / 2))', '(float)(0.5 * sin(t * (M_PI / 2)))'),
                     ('kEngineRadius = 0.5, kMinHalfWidth = 30, kDiffuse = 0.1', 'kEngineRadius = 0.01, kMinHalfWidth = 0, kDiffuse = 0'),
                     ('{"911turbo", ENG_REAR}', '{"911turbo", ENG_FRONT}'),
                     ('(float)sqrt(power *', '(float)sqrt(1.25 * power *'),
                     ('if (!posaudio_engine_realistic() || !eng.state', 'if (!eng.state',
                      '!(cat != 3 && posaudio_engine_realistic())', '!(cat != 3)'),
                     ('kCarRadius = 2.0, kCarMinHalf = 4, kCarDiffuse = 0.05', 'kCarRadius = 0.01, kCarMinHalf = 0, kCarDiffuse = 0'),
                     ('if (real) other_spread(category', 'if (1) other_spread(category'),
                     ('if (e > 3 + 60 / fmax(d0, 2.0)) {', 'if (0) {'),
                     ('    if (!posaudio_engine_realistic()) return 1;\n    double power = 0, sum[5]', '    double power = 0, sum[5]'),
                     ('kWheelTrack = 1.56, kWheelBase = 2.7', 'kWheelTrack = 0, kWheelBase = 0'),
                     ('kWheelDiffuse, g);\n            for (int i = 0; i < 5; ++i) sum[i] += (double)g[i] * g[i];', 'kWheelDiffuse, g);\n            for (int i = 0; i < 5; ++i) sum[i] += 1.3 * (double)g[i] * g[i];')]
        for mutation in mutations:
            broken = mutation is not None
            source = (ROOT / 'runtime/posaudio.c').read_text()
            if broken:
                for old, new in zip(mutation[::2], mutation[1::2]):
                    assert old in source, old
                    source = source.replace(old, new)
            (scratch / 'panner.c').write_text(source)
            binary = scratch / ('broken%d' % mutations.index(mutation) if broken else 'test')
            subprocess.run(['clang', '-O1', '-g', '-std=c11', '-D_DARWIN_C_SOURCE',
                            '-fsanitize=address,undefined', '-I', str(ROOT / 'runtime'),
                            '-I', str(ROOT / 'recomp'), str(ROOT / 'runtime/posaudio_test.c'),
                            str(scratch / 'panner.c'), str(scratch / 'original.c'), '-o', str(binary)], check=True)
            for layout in ['game', 'itu'] if not broken else ['itu']:
                result = subprocess.run([str(binary), str(scratch / 'image.bin')],
                                        env=dict(os.environ, NFS_SPEAKER_LAYOUT=layout),
                                        text=True, capture_output=True)
                print(('BROKEN%d ' % mutations.index(mutation) if broken else '') + layout + ': ' + result.stdout.strip())
                if result.stderr:
                    print(result.stderr[:1500])
                assert (result.returncode != 0) == broken, result.returncode
        # Every explicitly patched read/store must be emitted once. This also
        # catches silent loss when instruction/function discovery changes.
        counts = {a: 0 for a in recomp.POSITIONAL_MODE_READS}
        stores = {a: 0 for a in recomp.POSITIONAL_DEFAULT_STORES}
        for path in (ROOT / 'recomp/out').glob('speed_*.c'):
            text = path.read_text()
            for a in counts:
                counts[a] += len(re.findall(r'/\* '+f'{a:08X}'+r':[^\n]*\*/\n[^\n]*posaudio_mode_read', text))
            for a in stores:
                stores[a] += len(re.findall(r'/\* '+f'{a:08X}'+r':[^\n]*\*/\n[^\n]*posaudio_default_angle', text))
        assert all(n == 1 for n in counts.values()), counts
        assert all(n == 1 for n in stores.values()), stores
        print(f'translation coverage: {len(counts)} mode reads, {len(stores)} fixed-default stores')


if __name__ == '__main__':
    main()
