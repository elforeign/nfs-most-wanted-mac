#!/usr/bin/env python3
"""Source balance at the retail map writer -> mixer -> int16 boundary; no devices.

Original -> fixed, measured solo PCM: 0 -> -1.999917 / -2.999932 / -4.000039 dB.
993,473 checks / 0 failures; maximum channel dB error 0.001047015;
normalized channel-power share error 0.000016625. Protected categories:
110,592 exact PCM bytes. Real 3D angle copies: 9,720 exact halfword checks.
Wrong defaults, missing native rows, localized labels, broken Back, original
no-trim behavior, speech-as-effects, double/filter trim, wrong gain and angle
trim are deliberately broken controls; every one must exit 1. Failures:
25 / 17 / 36 / 5 / 26,142 / 7,599 / 26,140 / 4,536 / 8,712 / 6,192.
Default engine/effects: IDs 40020010/40020070, authored -4 dB, original Q15
20651 -> default Q15 16404, PCM -1.999744 dB versus the authored gain.
All nine saved pairs load through the production settings loader. Native rows
exercise cycling, preview, Back, Accept, destruction, rebasing and locks.
Four profiles with retired gameplay/sign keys and saved Music Off load through
the exact application preference parser: 0 failures. Strict-loader control:
8/8 loads rejected (exit 1), including four environment override cases.
Native Audio rows remain hidden in F10.
Baselines pin individual translated functions, never repository history.
"""
from pathlib import Path
import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp

# Small function fragments from the unmodified retail translation, normalized only
# for the hook's _orig name. The converter and mixer have no feature code.
BASELINES = {
    '004E9B30': ('14', 'b5d23590dbdf49025dab16e8e9a1fb13e2e8d9d65303849ff194e8705f6b4f8f'),
    '004E4120': ('14', 'f56eb70dc299f654f28745c3617108d87f8ed918bff4df3d7b9d26a444990348'),
    '004E4060': ('14', 'd2a0096d61dac87bfbf82a75b57ebc77b4c4af3f9670b7d1e44c0600dc964362'),
    '007C4B80': ('52', '62f3758e1ee14afc99e260c7f075006b36f22713168ec92c906cc13b52b54c03'),
    '0082279F': ('55', '08e61d923cbab2b516a60403debe74b934cea8da1b5aaf97d148000970c16083'),
    '00825957': ('55', '166233df8029a430e0beeec7206c4d99bda33a80a4e2779c33d08b9cdaef0dfc'),
}


def originals():
    bodies = []
    for addr, (chunk, digest) in BASELINES.items():
        code = (ROOT / f'recomp/out/speed_{chunk}.c').read_text()
        match = re.search(r'void f_' + addr + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n}', code, re.S)
        assert match, addr
        body = re.sub(r'void f_' + addr + r'(?:_orig)?\(', 'void f_' + addr + '(', match[0], count=1)
        assert hashlib.sha256(body.encode()).hexdigest() == digest, f'baseline changed: {addr}'
        if addr in ('004E9B30', '00825957'):
            body = body.replace(f'void f_{addr}(', f'void f_{addr}_orig(', 1)
        bodies.append(body)
    return '#include "runtime.h"\n' + '\n'.join(f'void f_{a}(Cpu *c);' for a in BASELINES) + '\n' + '\n'.join(bodies)


def parse(data, endian, debug=False):
    # The PC debug files were distributed with text-mode CRLF expansion inside
    # their binary records; their stored offsets address the unexpanded bytes.
    # Compiled maps are binary and are never normalized.
    if debug:
        data = data.replace(b'\r\n', b'\n')
    u = lambda at: struct.unpack_from(endian + 'I', data, at)[0]
    name = lambda at: data[at:at + 32].split(b'\0')[0].decode('ascii')
    for state in range(u(4)):
        start = u((48 if debug else 16) + state * 4)
        label = name(start + 32) if debug else ''
        if u(start + 16) == 0xffffffff:
            continue
        master = start + u(start + 16)
        at = master + (48 if debug else 16)
        output = start + u(start + 20)
        for _ in range(u(master)):
            controllers = u(at) >> 16 & 255
            record_name = name(at + 12) if debug else ''
            identity = u(at + 8)
            if debug:
                yield state, label, record_name, identity, None, None, None
            else:
                fmt = u(output)
                n = fmt & 15
                fields = [u(output + 4 + j * 4) for j in range(n)]
                three_d = sum(u(at + 12 + j * 4) & 0xe0000000 == 0x80000000 for j in range(controllers))
                yield state, label, record_name, identity, fmt, fields, three_d
                output += 4 + n * 4
            at += (44 if debug else 12) + controllers * (44 if debug else 4)


def fixture(pc, x360):
    result = bytearray()
    maps = 0
    names_by_id = {}
    for directory, endian in ((pc, '<'), (x360, '>')):
        paths = {p.name.upper(): p for p in directory.iterdir()}
        for base in ('MAPOUTPUT', 'MAPOUTPUT2CR', 'MAPOUTPUT2DR', 'MAPOUTPUTDRG'):
            binary = list(parse(paths[base + '.MXB'].read_bytes(), endian))
            named = list(parse(paths[base + '.DYN'].read_bytes(), '<', True))
            assert len(binary) == len(named)
            for b, d in zip(binary, named):
                state, label, name, identity, _, _, _ = d
                assert identity == b[3] and state == b[0]
                assert identity >> 16 & 255 == state
                # Decoded debug names supply the oracle independently of the
                # runtime's numeric ID masks. Speech shares MAIN with mixed NIS.
                if label in ('eMM_MAIN', 'eMM_MUSIC'):
                    cat = 0
                elif label == 'eMM_PLAYERCAR' and name in ('Single Ginsu', 'Single Model Tranny', 'Engine', 'Tranny'):
                    cat = 1
                else:
                    assert label in ('eMM_PLAYERCAR', 'eMM_AIRACECAR', 'eMM_COPCAR', 'eMM_TRAFFIC',
                                     'eMM_ENVIRONMENT', 'eMM_COLLISION', 'eMM_DRIVEBY', 'eMM_HELICOPTER', 'eMM_TRUCK'), label
                    cat = 2
                previous = names_by_id.setdefault(identity, cat)
                assert previous == cat, (hex(identity), label, name)
                fmt, fields, three_d = b[4:]
                assert all((v >> 26 & 31) < 30 for v in fields), (label, name)
                result.extend(struct.pack('<5I', identity, cat, fmt, len(fields), three_d))
                result.extend(struct.pack('<' + 'I' * len(fields), *fields))
            maps += 1
    print(f'category decode: {maps} maps, {len(names_by_id)} source IDs (PC + X360), exact debug/compiled ID agreement')
    return result


def main():
    pc = Path(recomp.EXE).parent / 'SOUND/MIXMAPS'
    x360 = Path(os.environ.get('NFS_X360_FILES', str(ROOT.parent / 'x360/files'))) / 'SOUND/MIXMAPS'
    if not Path(recomp.EXE).is_file() or not (ROOT / 'recomp/out/speed_14.c').is_file() or not pc.is_dir() or not x360.is_dir():
        print('SKIP: music_balance needs the retail PE, translated functions and PC/X360 mix maps')
        return 3
    with tempfile.TemporaryDirectory(prefix='music-balance-') as tmp:
        tmp = Path(tmp)
        image = bytearray(0x1000000)
        pe = recomp.PE()
        for _, _, va, size, raw in pe.sections:
            at = pe.base + va
            image[at:at + size] = pe.data[raw:raw + size]
        (tmp / 'image.bin').write_bytes(image)
        (tmp / 'fixture.bin').write_bytes(fixture(pc, x360))
        (tmp / 'original.c').write_text(originals())
        source = (ROOT / 'runtime/music_balance.c').read_text()
        # Old defaults, behavior and independent bad implementations must fail the
        # exact same PCM/category checks as the fixed implementation.
        mutations = {
            'wrong-default': ('.nchoices = 3, .def = 0,', '.nchoices = 3, .def = 1,'),
            'native-rows-missing': ('for (int i = 0; i < 2; ++i) {\n        Cpu q = *c;', 'for (int i = 0; i < 0; ++i) {\n        Cpu q = *c;'),
            'native-label-localized': ('M32(objects[i] + 0x1c) |= 2u;', 'M32(objects[i] + 0x1c) &= ~2u;'),
            'native-back-keeps-preview': ('event == 1 && message == 0x911ab364u', 'event == 1 && message == 0x911ab364u && 0'),
            'original-no-trim': ('int e = engine.value, f = effects.value;', 'return; int e = engine.value, f = effects.value;'),
            'speech-as-effects': ('switch (state) {', 'if (state == 0) return 2;\n    switch (state) {'),
            'double-trim': (' || (*mask & (1u << field))', ''),
            'filter-trim': ('if (((format >> 24) & 15) != 0) continue;', 'if (0) continue;'),
            'wrong-gain': ('11877359', '13326616'),
            'angle-trim': ('if ((destination & 0x80000000u) &&', 'if (0 && (destination & 0x80000000u) &&'),
        }
        for label, mutation in [('fixed', None), *mutations.items()]:
            code = source
            if mutation:
                old, new = mutation
                assert code.count(old) == (2 if label == "wrong-default" else 1), label
                code = code.replace(old, new)
            path = tmp / (label + '.c')
            path.write_text(code)
            binary = tmp / label
            build = subprocess.run(['clang', '-O1', '-g', '-std=c11', '-Wall', '-Wextra', '-Wno-unused-label',
                            '-Wno-unused-function', '-Wno-parentheses-equality', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'),
                            '-DMUSIC_BALANCE_SOURCE="' + str(path) + '"',
                            str(ROOT / 'runtime/music_balance_test.c'), str(ROOT / 'runtime/settings.c'), str(tmp / 'original.c'), '-o', str(binary)],
                           capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-4000:]
            run = subprocess.run([str(binary), str(tmp / 'image.bin'), str(tmp / 'fixture.bin')],
                                 text=True, capture_output=True)
            print(label + ': ' + run.stdout.strip())
            assert run.returncode == (1 if mutation else 0), (label, run.returncode, run.stderr[:1500])
            assert 'ERROR: AddressSanitizer' not in run.stderr and 'runtime error:' not in run.stderr, run.stderr[:1500]
            if not mutation:
                for e in range(3):
                    for f in range(3):
                        saved = subprocess.run([str(binary), 'saved', str(e), str(f)], capture_output=True, text=True)
                        assert saved.returncode == 0, (e, f, saved.stdout, saved.stderr)
                print('saved choices: all 9 pairs override defaults through settings_init')
        # The exact application parser must tolerate retired profile keys. The
        # strict-unknown-key control exits during loading and must be rejected.
        for file in ('gameplay_opts.c', 'maxperf_career.c', 'decal_depth.m', 'settings_port.c'):
            text = (ROOT / 'runtime' / file).read_text()
            for ident in ('gameplay.junkman', 'gameplay.maxperf_career', 'graphics.decal_depth'):
                assert '.id = "' + ident + '"' not in text, (file, ident)
        app = (ROOT / 'runtime/app.m').read_text()
        prefs = app.split('// BEGIN offline prefs:', 1)[1].split('\n', 1)[1].split('// END offline prefs', 1)[0]
        prelude = '#include <errno.h>\nconst char *userdata_dir(void) { return getenv("MIGRATION_USERDATA"); }\nstatic int durable_commit_with(FILE *f, const char *tmp, const char *path, int flags) { (void)flags; return fclose(f) == 0 && rename(tmp, path) == 0; }\n'
        for label, strict in (('tolerant', False), ('strict-unknown', True)):
            body = prefs
            if strict:
                old = '*eq = 0;'
                assert body.count(old) == 1
                body = body.replace(old, old + '\n            if (!settings_find(pref_trim(line))) exit(1);')
            (tmp / 'prefs.c').write_text(prelude + body)
            binary = tmp / label
            build = subprocess.run(['clang', '-w', '-O1', '-std=c11', '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'),
                    '-DAPP_PREF_SOURCE="' + str(tmp / 'prefs.c') + '"', str(ROOT / 'runtime/music_balance_test.c'),
                    str(ROOT / 'runtime/settings.c'), str(tmp / 'original.c'), '-o', str(binary)], capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-3000:]
            for saved, expected in (('off', 0), ('2', 0), ('3', 1), ('4', 2)):
                (tmp / 'app.txt').write_text('gameplay.junkman=retail\ngameplay.maxperf_career=retail\ngraphics.decal_depth=off\naudio.music_engine_trim=' + saved + '\naudio.music_effects_trim=off\n')
                env = dict(os.environ, MIGRATION_USERDATA=str(tmp))
                run = subprocess.run([str(binary), 'migration', str(expected)], env=env, capture_output=True, text=True)
                assert run.returncode == (1 if strict else 0), (label, saved, run.stdout, run.stderr)
            for override, expected in (('off', 0), ('2', 0), ('3', 1), ('4', 2)):
                env = dict(os.environ, MIGRATION_USERDATA=str(tmp), NFS_MUSIC_ENGINE_TRIM=override, NFS_MUSIC_EFFECTS_TRIM='off')
                run = subprocess.run([str(binary), 'migration-env', str(expected)], env=env, capture_output=True, text=True)
                assert run.returncode == (1 if strict else 0), (label, override, run.stdout, run.stderr)
            print(label + ': 8 profile/override loads ' + ('rejected' if strict else 'passed'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
