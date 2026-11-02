#!/usr/bin/env python3
"""Isolated library builds without recomp/out or test sources (exit 3 without FFmpeg headers).

Good: audio lib and shader lib --release build 3 archives / 4 members at 14.5,
despite MACOSX_DEPLOYMENT_TARGET=27.0. Broken audio early-exit removed: exit 1,
1 poisoned test compile; release target removed: 2/2 builders fail archive checks.
Default audio lib also builds 2 archives without tests. No player build commands change.
Bare copy without development headers: exit 3 / 1 skip, no build attempted.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build(root, script, *args):
    env = dict(os.environ, TOOLS_PY=sys.executable, MACOSX_DEPLOYMENT_TARGET='27.0')
    return subprocess.run(['sh', str(root / script), *args], env=env,
                          capture_output=True, text=True, timeout=120)


def main():
    headers = ROOT / 'third_party/ffmpeg/include'
    if sys.platform != 'darwin' or not shutil.which('clang++') or not (headers / 'libavcodec/avcodec.h').is_file():
        print('SKIP: needs macOS clang++ and the local FFmpeg development headers')
        return 3
    with tempfile.TemporaryDirectory(prefix='library-libmode-') as folder:
        bare = Path(folder) / 'bare/runtime/library_libmode_test.py'
        bare.parent.mkdir(parents=True)
        shutil.copyfile(__file__, bare)
        absent = subprocess.run([sys.executable, '-I', str(bare)], capture_output=True, text=True, timeout=10)
        assert absent.returncode == 3 and absent.stdout.startswith('SKIP:'), absent.stdout + absent.stderr
        root = Path(folder) / 'source'
        files = ['host/audio/build.sh', 'host/audio/dsound_host.cpp', 'host/audio/dsound_host.h',
                 'host/audio/peak_limiter.h', 'media/media.cpp', 'media/media.h', 'media/movie_api.h',
                 'media/check_libraries.py', 'media/library_members.tsv', 'host/shaders/build.sh',
                 'host/shaders/sm_parse.cpp', 'host/shaders/sm_msl.cpp']
        files += [str(p.relative_to(ROOT)) for p in (ROOT / 'host/shaders').glob('*.h')]
        for relative in files:
            dest = root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dest)
        shutil.copytree(headers, root / 'third_party/ffmpeg/include')
        assert not (root / 'recomp').exists()
        poison = root / 'host/audio/dstest.cpp'
        poison.write_text('#error library_mode_must_not_compile_tests\n')
        for script in ('host/audio/build.sh', 'host/shaders/build.sh'):
            result = build(root, script, 'lib', '--release')
            assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
            assert 'minimum macOS 14.5.0' in result.stdout, result.stdout
            # The library builder must still overwrite stale 27.0 objects on its next invocation.
            path = root / script
            original = path.read_text()
            target = '-arch arm64 -mmacosx-version-min=14.5'
            assert original.count(target) == 1
            path.write_text(original.replace(target, ''))
            result = build(root, script, 'lib', '--release')
            assert result.returncode == 1 and 'minimum macOS 27.0.0' in result.stderr, result.stderr[-2000:]
            # Failed shader builds retain diagnostics; discard only this fixture's work dirs.
            for work in (root / 'host/shaders/build').glob('work.*'):
                shutil.rmtree(work)
            path.write_text(original)
            result = build(root, script, 'lib', '--release')
            assert result.returncode == 0, result.stderr[-2000:]
        audio = root / 'host/audio/build.sh'
        original = audio.read_text()
        early_exit = '[ "$mode" != lib ] || exit 0'
        assert original.count(early_exit) == 1
        # The driver validates link input paths before compiling the poisoned test.
        # Empty archives suffice: lib mode itself never needs FFmpeg link inputs.
        deps = root / 'third_party/ffmpeg/lib'
        deps.mkdir()
        for name in ('avformat', 'avcodec', 'swscale', 'swresample', 'avutil'):
            (deps / f'lib{name}.a').write_bytes(b'!<arch>\n')
        audio.write_text(original.replace(early_exit, ':'))
        result = build(root, 'host/audio/build.sh', 'lib')
        assert result.returncode == 1 and 'library_mode_must_not_compile_tests' in result.stderr, result.stderr[-2000:]
        audio.write_text(original)
        result = build(root, 'host/audio/build.sh', 'lib')
        assert result.returncode == 0, result.stderr[-2000:]
        assert not (root / 'host/audio/build/dstest').exists()
        assert not (root / 'recomp/out').exists()
        assert len(list((root / 'host/audio/build').glob('*.a'))) == 2
        assert not list((root / 'host/shaders/build').glob('work.*'))
        print('lib mode: 3 release archives / 4 members at 14.5 without recomp/out; default audio lib: 2 archives')
        print('controls: early exit removed 1 test compile rejected; missing target 2/2 builders rejected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
