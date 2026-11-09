"""Build and verify the optional, game-independent Setup libraries.

A missing, damaged or source-incompatible set returns to the source build. The
manifest is pinned by a separate resource in the signed Setup app. Its digest
is passed by Setup, never read from the writable extracted source or payload.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FFMPEG = 'third_party/dl/ffmpeg-7.1.1.tar.xz'
SHA = '733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1'
LEGAL = ('NOTICE', 'LICENSE', 'media/PREBUILT.md', 'media/licenses/LGPL-2.1.txt',
         'media/licenses/FFmpeg-notices.txt')
OUTPUTS = ('third_party/ffmpeg', 'host/audio/build', 'host/shaders/build')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    spec = importlib.util.spec_from_file_location('_prebuilt_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def recipe(root):
    paths = [root / n for n in (*LEGAL, 'tools/installer/deps.lock.json',
                                'tools/setup_kit/prebuilt.py', 'media/library_members.tsv')]
    for folder in ('host/audio', 'host/shaders', 'media'):
        paths += [p for p in (root / folder).iterdir() if p.suffix in ('.c', '.cpp', '.h', '.sh', '.py')
                  and not p.stem.endswith('_test')]
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(set(paths))}


def check_notices(root):
    """Publication gate: compiled libraries require accessible texts and sources."""
    for name in LEGAL:
        if not (root / name).is_file() or not (root / name).stat().st_size:
            raise ValueError('prebuilt libraries require ' + name)
    notice = (root / 'NOTICE').read_text()
    for phrase in ('compiled', 'LGPL version 2.1 or later', 'media/PREBUILT.md',
                   'Independent JPEG Group', 'ISC', 'MIT', 'zlib'):
        if phrase not in notice:
            raise ValueError('prebuilt NOTICE missing: ' + phrase)
    if 'END OF TERMS AND CONDITIONS' not in (root / 'media/licenses/LGPL-2.1.txt').read_text():
        raise ValueError('incomplete LGPL text')
    retained = (root / 'media/licenses/FFmpeg-notices.txt').read_text()
    for phrase in ('Michael Niedermayer', 'Permission to use, copy, modify', 'Permission is hereby granted',
                   'Independent JPEG Group', 'Mark Adler', 'AS IS'):
        if phrase not in retained:
            raise ValueError('missing retained FFmpeg notice: ' + phrase)
    lock = json.loads((root / 'tools/installer/deps.lock.json').read_text())
    pin = next(s for s in lock['sources'] if s['name'] == 'ffmpeg')
    if pin['sha256'] != SHA or pin['dest'] != FFMPEG or digest(root / FFMPEG) != SHA:
        raise ValueError('prebuilt source archive differs from the dependency pin')


def inventory(folder):
    result = {}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():
            raise ValueError('prebuilt symbolic link: ' + str(p))
        if p.is_file():
            result[p.relative_to(folder).as_posix()] = digest(p)
    return result


def validate(root, payload, expected_sha256=None):
    manifest = (payload / 'manifest.json').read_bytes()
    if expected_sha256 is not None and hashlib.sha256(manifest).hexdigest() != expected_sha256:
        raise ValueError('prebuilt manifest SHA-256 differs from Setup pin')
    check_notices(root)
    if payload.is_symlink() or (payload / 'files').is_symlink():
        raise ValueError('prebuilt payload is a symbolic link')
    data = json.loads(manifest)
    if data['schema'] != 1 or data['target'] != 'arm64-macos14.5' or data['source'] != SHA:
        raise ValueError('unsupported prebuilt target/source')
    if data['recipe'] != recipe(root):
        raise ValueError('prebuilt build recipe changed')
    if digest(payload / 'configure.txt') != data['configure']:
        raise ValueError('prebuilt configure line SHA-256 mismatch')
    files = inventory(payload / 'files')
    if not files or files != data['files']:
        raise ValueError('prebuilt SHA-256 mismatch or incomplete set')
    checker = load(root / 'media/check_libraries.py')
    archives = checker.manifest()
    if set(n for n in files if n.endswith('.a')) != set(archives):
        raise ValueError('prebuilt archive set differs')
    headers = [n for n in files if n.startswith('third_party/ffmpeg/include/') and n.endswith('.h')]
    if 'third_party/ffmpeg/include/libavformat/avformat.h' not in headers:
        raise ValueError('prebuilt headers missing')
    if set(files) != set(archives) | set(headers):
        raise ValueError('unexpected prebuilt file')
    for name, expected in archives.items():
        archive = payload / 'files' / name
        checker.check_archive(archive, expected)
        if any(prefix in archive.read_bytes() for prefix in (b'/Users/', b'/private/')):
            raise ValueError('prebuilt build-machine path in ' + name)
    return data


def install(root, target, log=lambda message: None, *, expected_sha256=None):
    """Verify the entire set before exposing any archive to the local app link."""
    payload = root / 'prebuilt'
    touched = []
    try:
        if not payload.is_dir():
            raise ValueError('optional prebuilt set is missing')
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or
                any(c not in '0123456789abcdef' for c in expected_sha256)):
            raise ValueError('optional prebuilt set has no trusted Setup pin')
        data = validate(root, payload, expected_sha256)
        # Recheck a private copy, closing source-copy races before changing outputs.
        with tempfile.TemporaryDirectory(prefix='.prebuilt-', dir=target.parent) as folder:
            temp = Path(folder)
            shutil.copytree(payload, temp / 'prebuilt')
            if validate(root, temp / 'prebuilt', expected_sha256) != data:
                raise ValueError('prebuilt changed during copy')
            for name in OUTPUTS:
                if not (target / name).resolve().is_relative_to(target.resolve()):
                    raise ValueError('prebuilt output escapes the build folder')
            for name in OUTPUTS:
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink():
                    raise ValueError('prebuilt output is a symbolic link')
                touched.append(destination)
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(temp / 'prebuilt/files' / name, destination)
        log('Verified prebuilt libraries installed (arm64, macOS 14.5).')
        return True
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        for destination in touched:
            shutil.rmtree(destination, ignore_errors=True)
        log('Building libraries from source: ' + str(error))
        return False


def build(root, output):
    """Run only in a fresh packaged source tree, never a development build tree."""
    check_notices(root)
    if (root / 'third_party/src').exists() or any((root / p).exists() for p in OUTPUTS):
        raise ValueError('prebuilt release build needs a fresh source tree')
    env = dict(os.environ, TOOLS_PY=sys.executable)
    for command in (['sh', 'media/build_ffmpeg.sh', '--release'],
                    ['sh', 'host/audio/build.sh', 'lib', '--release'],
                    ['sh', 'host/shaders/build.sh', 'lib', '--release']):
        subprocess.run(command, cwd=root, env=env, check=True)
    subprocess.run([sys.executable, '-I', 'media/check_ffmpeg_sources.py'], cwd=root, check=True)
    output.mkdir(parents=True, exist_ok=False)
    for name in load(root / 'media/check_libraries.py').manifest():
        to = output / 'files' / name
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, to)
    shutil.copytree(root / 'third_party/ffmpeg/include', output / 'files/third_party/ffmpeg/include')
    # Keep the actual configure invocation, with no build-machine home paths.
    config = (root / 'third_party/src/ffmpeg-7.1.1/config.h').read_text()
    line = next(s for s in config.splitlines() if s.startswith('#define FFMPEG_CONFIGURATION '))
    (output / 'configure.txt').write_text(line + '\n')
    data = dict(schema=1, target='arm64-macos14.5', source=SHA, recipe=recipe(root), configure=digest(output / 'configure.txt'),
                files=inventory(output / 'files'))
    (output / 'manifest.json').write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    validate(root, output)
    print('prebuilt release verified: 10 archives, source and build recipe pinned')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'check'))
    parser.add_argument('root', type=Path)
    parser.add_argument('payload', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            build(args.root.resolve(), args.payload.resolve())
        else:
            validate(args.root.resolve(), args.payload.resolve())
            print('prebuilt publication check: PASS')
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.CalledProcessError) as error:
        parser.exit(1, 'prebuilt publication check: FAIL: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
