#!/usr/bin/env python3
"""Audit real installed FFmpeg sources, plus isolated compiler/archive controls.

Original build: exit 1, 388 sources / 2 GPL headers (arm64 AC-3 C + NEON).
Fixed build: exit 0, 386 sources / 0 GPL headers. Controls: GPL C and assembly
2/2 rejected, missing dependency/stale object/archive set 3/3 rejected, no libraries exit 3.
Re-added real arm64 AC-3 GPL initializer: exit 1, 387 sources / 1 GPL header.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'media/check_ffmpeg_sources.py'


def audit(root, expected):
    result = subprocess.run([sys.executable, '-I', str(CHECK), '--root', str(root)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == expected, result.stdout[-2000:] + result.stderr
    return result


def main():
    real = subprocess.run([sys.executable, '-I', str(CHECK)], capture_output=True, text=True, timeout=60)
    if real.returncode:
        print(real.stdout[-1000:] + real.stderr)
        return real.returncode
    print(real.stdout.splitlines()[-1])
    if not all(shutil.which(tool) for tool in ('clang', 'ar')):
        print('SKIP: compiler/archive controls need clang and ar')
        return 3
    with tempfile.TemporaryDirectory(prefix='ffmpeg-sources-') as folder:
        root = Path(folder)
        # Compile the actual excluded upstream file and append it to a copy of
        # the exact installed archive. No development archive is modified.
        real_root = root / 'real-control'
        real_build = real_root / 'third_party/src/ffmpeg-7.1.1'
        shutil.copytree(ROOT / 'third_party/src/ffmpeg-7.1.1', real_build)
        shutil.copytree(ROOT / 'third_party/ffmpeg/lib', real_root / 'third_party/ffmpeg/lib')
        obj = 'libavcodec/aarch64/ac3dsp_init_aarch64.o'
        compiled = subprocess.run(['make', '-s', obj], cwd=real_build,
                                  capture_output=True, text=True, timeout=60)
        assert compiled.returncode == 0, compiled.stderr[-2000:]
        appended = subprocess.run(['ar', 'q', str(real_root / 'third_party/ffmpeg/lib/libavcodec.a'),
                                  str(real_build / obj)], capture_output=True, text=True, timeout=30)
        assert appended.returncode == 0, appended.stderr
        broken_real = audit(real_root, 1)
        assert 'GPL source compiled: libavcodec/aarch64/ac3dsp_init_aarch64.c' in broken_real.stderr
        assert '387 compiled sources, 1 GPL headers' in broken_real.stdout
        print('real GPL object re-added: rejected (387 sources, 1 GPL header)')
        shutil.rmtree(real_root)
        audit(root, 3)
        build = root / 'third_party/src/ffmpeg-7.1.1'
        objdir = build / 'libprobe/aarch64'
        objdir.mkdir(parents=True)
        libdir = root / 'third_party/ffmpeg/lib'
        libdir.mkdir(parents=True)
        archive = libdir / 'libprobe.a'
        objects = []
        for name, body in (('init.c', 'int probe(void) { return 42; }\n'), ('neon.S', '.text\n')):
            source = objdir / name
            source.write_text('/* GNU Lesser General Public License */\n' + body)
            obj = source.with_suffix('.o').relative_to(build)
            result = subprocess.run(['clang', '-c', '-MMD', '-MF', str(obj.with_suffix('.d')),
                                     '-MT', str(obj), str(source.relative_to(build)), '-o', str(obj)],
                                    cwd=build, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            objects.append(build / obj)
        built_archive = build / 'libprobe/libprobe.a'
        result = subprocess.run(['ar', 'qc', str(built_archive), *(str(p) for p in objects)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        shutil.copyfile(built_archive, archive)
        good = audit(root, 0)
        assert '2 compiled sources, 0 GPL headers' in good.stdout
        extra = build / 'libextra/libextra.a'
        extra.parent.mkdir()
        extra.write_bytes(b'!<arch>\n')
        assert 'archive sets differ' in audit(root, 1).stderr
        extra.unlink()
        for name in ('init.c', 'neon.S'):
            source = objdir / name
            original = source.read_text()
            # Deliberately include both phrases: Lesser must not mask a GPL notice.
            source.write_text(original.replace('Lesser General Public License',
                              'Lesser General Public License; GNU\n * General Public License'))
            broken = audit(root, 1)
            assert f'GPL source compiled: libprobe/aarch64/{name}' in broken.stderr
            source.write_text(original)
        dep = objects[0].with_suffix('.d')
        saved = dep.read_bytes()
        dep.unlink()
        assert 'source inventory' in audit(root, 1).stderr
        dep.write_bytes(saved)
        objects[0].write_bytes(b'stale object')
        assert 'matching build object, found 0' in audit(root, 1).stderr
    print('ffmpeg_sources_test: PASS; GPL C/S 2/2, missing/stale/archive set 3/3 rejected; absent libraries exit 3')
    return 0


if __name__ == '__main__':
    sys.exit(main())
