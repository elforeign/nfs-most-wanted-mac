#!/usr/bin/env python3
"""Offline A6 production path, native translated MicroTalk -> scratch PCM.
No game process, audio output, network, or writes to game/extracted assets.
32 streams per source by default, spread across all 13,562 indexed streams.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent
PC = ASSETS / 'Need For Speed Most Wanted Black Edition'
EXTRACT = ASSETS / 'assets_audio/x360_copspeech'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp


def run(args, expected=0, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != expected:
        print(result.stdout[-4000:] + result.stderr[-4000:])
        raise RuntimeError(f'{args[0]} failed: {result.returncode}')
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='nfsmw-copspeech-') as directory:
        scratch = Path(directory)
        pe = recomp.PE()
        image = bytearray(0x1000000)
        for _, _, va, size, raw in pe.sections:
            image[pe.base + va:pe.base + va + size] = pe.data[raw:raw + size]
        image_path = scratch / 'image.bin'
        image_path.write_bytes(image)
        source = (ROOT / 'recomp/out/speed_55.c').read_text()
        functions = {m[1]: m[0] for m in re.finditer(
            r'void f_([0-9A-F]{8})\(Cpu \*restrict c\) \{.*?\n}', source, re.S)}
        # Exact production decoder dependency closure; no decoder reimplementation.
        seen, todo = set(), ['00828773', '008287AE', '00828976', '00821C7C', '008259DB']
        while todo:
            address = todo.pop()
            if address in seen:
                continue
            seen.add(address)
            todo.extend(set(re.findall(r'f_([0-9A-F]{8})\(c\)', functions[address])) - seen)
        original = scratch / 'original.c'
        original.write_text('#include "runtime.h"\n' + '\n'.join(
            'void f_' + a + '(Cpu *c);' for a in sorted(seen)) + '\n' + '\n'.join(
            functions[a] for a in sorted(seen)))
        binary = scratch / 'test'
        timing = os.environ.get('NFS_COP_TEST_TIMING') == '1'
        test_source = ROOT / 'runtime/x360copspeech_test.c'
        baseline = len(sys.argv) == 3 and sys.argv[1] == '--baseline'
        if baseline:
            test_source = scratch / 'baseline_test.c'
            test_source.write_text((ROOT / 'runtime/x360copspeech_test.c').read_text().replace(
                '#include "x360copspeech.c"', '#include "' + str(Path(sys.argv[2]).resolve()) + '"'))
        run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-DX360AUDIO_TEST', '-O1', '-g', '-w',
             *(['-DCOP_TEST_BASELINE'] if baseline else []),
             *([] if timing else ['-fsanitize=address,undefined']), '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'),
             '-I', str(ROOT / 'recomp/out'), str(ROOT / 'runtime/x360audio.c'),
             str(test_source), str(original),
             str(ROOT / 'runtime/settings.c'), str(ROOT / 'runtime/offline_stubs.c'), '-lz', '-o', str(binary)])
        env = dict(os.environ, NFS_X360_AUDIO='off', NFS_X360_MUSIC='off')
        for key in list(env):
            if key == 'NFS_X360_COPSPEECH' or key.startswith('NFS_COP_TEST_'):
                env.pop(key, None)
        print(run([str(binary), 'settings'], env=env).stdout, end='')
        if baseline:
            result = run([str(binary), 'serve', str(image_path), str(EXTRACT), str(PC), '1', '0'],
                         expected=1, env=dict(env, NFS_X360_COPSPEECH='x360'))
            assert 'runtime decoder allocations: 2' in result.stdout and '!allocations' in result.stderr
            print(result.stdout, end='')
            print('old runtime: zero-proof-decode assertion rejected as expected')
            return
        count = int(os.environ.get('NFS_COP_TEST_STREAMS', '32'))
        assert 1 <= count <= 13562

        def serve(extract, want, streams=0, **values):
            result = run([str(binary), 'serve', str(image_path), str(extract), str(PC),
                          str(int(want)), str(streams)], env=dict(env, **values))
            print(result.stdout, end='')
            assert 'first_stream_decoded=n/a (offline test)' in result.stdout
            assert ('files=X360' if want else 'files=PC') in result.stdout
            assert not result.stderr, result.stderr[-2000:]
            if values.get('NFS_COP_TEST_ALLOC_FAIL'):
                assert 'production gmalloc_try returned 0; process survived' in result.stdout
            return result.stdout

        def checksum(path):
            value = 0
            with path.open('rb') as file:
                while data := file.read(1024 * 1024):
                    value = zlib.crc32(data, value)
            return value

        if timing:
            print('host timing: runtime -O1, without sanitizers; OS file cache warm')
            serve(EXTRACT, True, NFS_X360_COPSPEECH='x360')
            serve(EXTRACT, True, NFS_X360_COPSPEECH='x360', NFS_COP_TEST_WARM='1')
            return

        pc_files = [PC / 'SOUND/SPEECH' / ('copspeech.' + ext) for ext in ['big', 'csi', 'evt', 'idx']]
        pc_before = [checksum(path) for path in pc_files]

        quiet = run([str(binary), 'serve', str(image_path), str(EXTRACT), str(PC), '0', '0'],
                    env=dict(env, NFS_COP_TEST_QUIET='1', NFS_X360_COPSPEECH='pc'))
        assert 'x360copspeech:' not in quiet.stdout and not quiet.stderr
        print('PC outside BOOT_DIAG: zero decoder allocations/preflight')
        serve(EXTRACT, True)  # No switch: X360 is the default.
        serve(EXTRACT, False, NFS_X360_COPSPEECH='pc')
        production = serve(EXTRACT, True, count, NFS_X360_COPSPEECH='x360')
        rss = float(re.search(r'peak RSS: ([0-9.]+) MiB', production)[1])
        if count <= 32:
            assert rss < 160, f'production sparse RSS regression: {rss} MiB'
        print(f'production entry point, no oracle: peak RSS {rss:.3f} MiB')
        serve(EXTRACT, True, count, NFS_X360_COPSPEECH='x360', NFS_COP_TEST_PARITY='1')
        broken = run([str(binary), 'serve', str(image_path), str(EXTRACT), str(PC), '1', str(count)],
                     expected=1, env=dict(env, NFS_X360_COPSPEECH='x360',
                                          NFS_COP_TEST_PARITY='1', NFS_COP_TEST_OFFSET_BAD='1'))
        assert 'FAIL:' in broken.stdout and 'mismatches' in broken.stdout
        assert re.search(r'word boundaries, [1-9][0-9]* mismatches', broken.stdout)
        assert 'runtime error:' not in broken.stderr and 'AddressSanitizer' not in broken.stderr
        print('deliberately shifted production patch: ' + next(
            line for line in broken.stdout.splitlines() if line.startswith('parity:')) + '; expected FAIL')
        serve('-', True, NFS_X360_COPSPEECH='x360')  # Default speech-only extraction path.
        serve(EXTRACT, True, NFS_X360_COPSPEECH='x360', NFS_COP_TEST_ALLOC_FAIL='1')
        serve(EXTRACT, True, NFS_X360_COPSPEECH='x360', NFS_COP_TEST_WARM='1')
        serve(EXTRACT, True, NFS_COP_TEST_PREF='x360')
        serve(EXTRACT, False, NFS_X360_COPSPEECH='pc', NFS_COP_TEST_PREF='x360')
        fixture = scratch / 'extract/sOuNd/sPeEcH'
        fixture.mkdir(parents=True)
        for extension in ['big', 'csi', 'evt', 'idx']:
            path = fixture / ('cOpSpEeCh_En.' + extension)
            path.symlink_to(EXTRACT / 'SOUND/SPEECH' / ('COPSPEECH_EN.' + extension.upper()))
        serve(fixture.parents[1], True, NFS_X360_COPSPEECH='x360')
        for extension in ['big', 'csi', 'evt', 'idx']:
            path = fixture / ('cOpSpEeCh_En.' + extension)
            original_path = path.resolve()
            path.unlink()
            serve(fixture.parents[1], False, NFS_X360_COPSPEECH='x360')
            # Correct file size, damaged header; sparse BIG avoids a 456 MB copy.
            with path.open('wb') as out, original_path.open('rb') as src:
                header = bytearray(src.read(64))
                header[0] ^= 0xff
                out.write(header)
                out.truncate(original_path.stat().st_size)
            serve(fixture.parents[1], False, NFS_X360_COPSPEECH='x360')
            path.unlink()
            path.symlink_to(original_path)
        for name, expected in [('BIG', 0x1ab21210), ('CSI', 0xa8888398),
                               ('EVT', 0x2a5d2983), ('IDX', 0x7d298c2d)]:
            assert checksum(EXTRACT / 'SOUND/SPEECH' / ('COPSPEECH_EN.' + name)) == expected, 'source asset changed: ' + name
        assert pc_before == [checksum(path) for path in pc_files], 'PC source asset changed'
        print('PC path unchanged: four opens declined, handle untouched; all four PC source CRCs unchanged')
        print('source assets: original X360 CRCs unchanged after sparse reads')
        print(f'PASS: {len(seen)} original decoder/header/registration functions; default/saved/env/restart; '
              'all 4 missing and corrupted files keep the PC set; case/cursor/seek/EOF checks')


if __name__ == '__main__':
    main()
