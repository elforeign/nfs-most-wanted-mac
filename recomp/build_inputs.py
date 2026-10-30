"""Content fingerprints for production builds; no translator or emulator imports."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    try:
        with Path(path).open('rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        return h.hexdigest()
    except FileNotFoundError:
        return 'missing'


def production(name):
    p = Path(name)
    return (p.parts[0] == 'recomp' and len(p.parts) == 2
            and p.suffix in ('.py', '.h', '.c', '.tsv', '.txt')
            and not p.stem.endswith('_test') and not p.stem.startswith('test_'))


def toolchain():
    result = {}
    for command in (['clang', '--version'], ['ar', '-V'], ['xcrun', '--show-sdk-path'], ['xcrun', '--show-sdk-version']):
        p = subprocess.run(command, capture_output=True, text=True)
        # A failing xcodebuild/xcrun writes a timestamped result-bundle line; keep the error, not the volatile parts.
        stderr = re.sub(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ \S+\[\d+:\d+\] ', '', p.stderr, flags=re.M)
        stderr = re.sub(r'ResultBundle_[^\s/]+\.xcresult', 'ResultBundle.xcresult', stderr)
        result[' '.join(command)] = [p.returncode, p.stdout, stderr]
    for key in ('RECOMP_CFLAGS', 'RECOMP_LISTING', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET',
                'DEVELOPER_DIR', 'CPATH', 'C_INCLUDE_PATH', 'LIBRARY_PATH'):
        result[key] = os.environ.get(key, '')
    # The system git launcher injects xcrun's defaults into hook environments.
    # An ordinary gate and its merge hook must describe the same effective SDK
    # and search paths; explicit overrides still participate in the key.
    result['SDKROOT'] = result['SDKROOT'] or result['xcrun --show-sdk-path'][1].strip()
    result['CPATH'] = result['CPATH'] or '/usr/local/include'
    result['LIBRARY_PATH'] = result['LIBRARY_PATH'] or '/usr/local/lib'
    result['python'] = sys.version
    result['capstone'] = importlib.metadata.version('capstone')
    for name in ('clang', 'ar'):
        found = subprocess.run(['xcrun', '--find', name], capture_output=True, text=True)
        selected = found.stdout.strip() if found.returncode == 0 else ''
        invoked = shutil.which(name) or ''
        result[name + '-binary'] = [selected, digest(selected) if selected else 'missing',
                                    invoked, digest(invoked) if invoked else 'missing']
    # A same-version package repair can replace the decoder library's bytes.
    dist = importlib.metadata.distribution('capstone')
    result['capstone-files'] = {str(p): digest(dist.locate_file(p)) for p in dist.files or []
                                if str(p).endswith(('.py', '.so', '.dylib'))}
    return result


def key(files, config):
    records = [(str(name), digest(path)) for name, path in sorted(files)]
    return hashlib.sha256(json.dumps([records, config], sort_keys=True).encode()).hexdigest()


def external_key(root=ROOT, compiler=None):
    """External production inputs must invalidate even the gate's early cache."""
    root = Path(root)
    exe = Path(os.environ.get('NFS_SPEED_EXE') or root.parent / 'Need For Speed Most Wanted Black Edition/speed.exe')
    server = Path(os.environ.get('NFS_SERVER_DLL') or exe.parent / 'server.dll')
    files = [('speed.exe', exe), ('server.dll', server)]
    if os.environ.get('RECOMP_LISTING') == 'ghidra':
        files += [(n, root.parent / 'decomp' / n) for n in ('asm/speed_text.asm', 'functions.tsv')]
    return key(files, compiler if compiler is not None else toolchain())


def recomp_key(root=ROOT, exe=None, server=None, skip_server=False, compiler=None):
    root = Path(root)
    exe = Path(exe or os.environ.get('NFS_SPEED_EXE') or
               root.parent / 'Need For Speed Most Wanted Black Edition/speed.exe')
    server = Path(server or os.environ.get('NFS_SERVER_DLL') or exe.parent / 'server.dll')
    files = [(str(p.relative_to(root)), p) for p in (root / 'recomp').iterdir()
             if p.is_file() and production(str(p.relative_to(root)))]
    files += [('speed.exe', exe)]
    if not skip_server:
        files += [('server.dll', server)]
    if os.environ.get('RECOMP_LISTING') == 'ghidra':
        files += [(n, root.parent / 'decomp' / n) for n in ('asm/speed_text.asm', 'functions.tsv')]
    return key(files, dict(toolchain=compiler if compiler is not None else toolchain(), skip_server=skip_server))


def outputs(root=ROOT):
    out = Path(root) / 'recomp/out'
    paths = [out / n for n in ('speed.a', 'funcs.h')]
    paths += list((out / 'server').glob('*.h')) + list((out / 'server').glob('*.a'))
    return {str(p.relative_to(out)): digest(p) for p in paths}


def fresh(stamp, fingerprint, artifacts):
    try:
        saved = json.loads(Path(stamp).read_text())
    except (OSError, ValueError):
        return False
    return ('missing' not in artifacts.values() and
            saved == dict(key=fingerprint, outputs=artifacts))


def record(stamp, fingerprint, artifacts):
    stamp = Path(stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    temp = stamp.with_suffix('.pending')
    temp.write_text(json.dumps(dict(key=fingerprint, outputs=artifacts), sort_keys=True) + '\n')
    temp.replace(stamp)


def runtime_key(root=ROOT, compiler=None):
    root = Path(root)
    paths = [p for p in (root / 'runtime').iterdir() if p.suffix in ('.c', '.m', '.mm', '.cpp', '.h')
             and '_test' not in p.stem and not p.stem.startswith('test_')]
    paths += [root / 'runtime/build.sh', root / 'runtime/env.sh']
    paths += list((root / 'runtime/gen').glob('*.inc'))
    paths += list((root / 'recomp').glob('*.h')) + list((root / 'recomp/out').glob('*.h'))
    paths += list((root / 'recomp/out/server').glob('*.h')) + list((root / 'recomp/out/server').glob('*.a'))
    paths += [root / 'recomp/out/speed.a', root / 'recomp/build_inputs.py']
    for folder in ('host/audio/build', 'host/shaders/build', 'third_party/ffmpeg/lib'):
        paths += list((root / folder).glob('*.a'))
    for folder in ('host/audio', 'host/shaders', 'media', 'third_party/ffmpeg/include'):
        paths += list((root / folder).rglob('*.h'))
    # Production can include files named *_test or *.inc (the live carpaint
    # diagnostic and the generated music manifest). Follow quoted includes
    # instead of assuming their suffix/name makes them test-only.
    pending, seen = list(paths), set()
    search = [root / p for p in ('recomp', 'recomp/out', 'third_party/ffmpeg/include')]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path.suffix not in ('.c', '.m', '.mm', '.cpp', '.h', '.inc') or not path.is_file():
            continue
        for name in re.findall(r'^\s*#\s*(?:include|import)\s*"([^"]+)"', path.read_text(), re.M):
            candidates = [Path(os.path.normpath(folder / name)) for folder in [path.parent, *search]]
            pending.append(next((p for p in candidates if p.is_file()), candidates[0]))
    files = [(str(p.relative_to(root)), p) for p in seen]
    return key(files, dict(toolchain=compiler if compiler is not None else toolchain(),
                           public=os.environ.get('NFS_PUBLIC', '0')))


def main():
    if sys.argv[1:] == ['--external-key']:
        print(external_key())
        return 0
    if sys.argv[1:2] == ['--changed']:
        names = subprocess.check_output(['git', 'diff', '--name-only', sys.argv[2], '--', 'recomp'], text=True)
        names += '\n' + subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', 'recomp'], text=True)
        return 0 if any(production(n) for n in names.splitlines() if n) else 1
    if sys.argv[1:] == ['--runtime-key']:
        print(runtime_key())
        return 0
    mode, target, fingerprint = sys.argv[1:]
    target = Path(target).resolve()
    stamp = ROOT / 'runtime/gen' / ('build-' + hashlib.sha256(str(target).encode()).hexdigest()[:12] + '.json')
    artifacts = {'boot': digest(target)}
    if mode == '--runtime-fresh':
        return 0 if fresh(stamp, fingerprint, artifacts) else 1
    if mode != '--runtime-record':
        raise SystemExit('unknown build fingerprint mode')
    record(stamp, fingerprint, artifacts)
    return 0


if __name__ == '__main__':
    sys.exit(main())
