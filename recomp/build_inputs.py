"""Content fingerprints for production builds; no translator or emulator imports.

Setup counts six build stages (bundle/signing always runs separately). Before
stage reuse, first unchanged / X360-only / runtime-only updates run 2 / 6 / 6
stages (steady unchanged: 0). Runtime metadata dirties two output directories.
Keep source-copy identity separate from stage inputs, verify retained outputs,
and key native artifacts by target, compiler identity and effective arguments.
"""
import ast
import hashlib
import importlib.util
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


def package_files(names):
    """Same-version repairs to Python generators/decoders are input changes too."""
    result = {}
    for name in names:
        dist = importlib.metadata.distribution(name)
        result[name] = {str(p): digest(dist.locate_file(p)) for p in dist.files or []
                        if str(p).endswith(('.py', '.so', '.dylib', '.pyd', '.dll'))}
    return result


def production(name):
    p = Path(name)
    return (p.parts[0] == 'recomp' and len(p.parts) == 2
            and not p.name.startswith('.')
            and p.suffix in ('.py', '.h', '.c', '.tsv', '.txt')
            and not p.stem.endswith('_test') and not p.stem.startswith('test_'))


def source_glob(folder, pattern, recursive=False):
    folder = Path(folder)
    paths = folder.rglob(pattern) if recursive else folder.glob(pattern)
    return [p for p in paths if not any(part.startswith('.') for part in p.relative_to(folder).parts)]


def toolchain(target=None, compiler='clang'):
    if target and 'windows' in target:
        # Cross builds have no Apple SDK. In particular, do not run the xcrun
        # launcher or include its timestamped failures in a Windows fingerprint.
        p = subprocess.run([compiler, '--version'], capture_output=True, text=True)
        binary = shutil.which(compiler)
        return {'target': target, 'compiler': compiler,
                'compiler-binary': [binary, digest(binary) if binary else 'missing'],
                'version': [p.returncode, p.stdout],
                'RECOMP_CFLAGS': os.environ.get('RECOMP_CFLAGS', '')}
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
    result['capstone-files'] = package_files(['capstone'])['capstone']
    return result


def key(files, config):
    records = [(str(name), digest(path)) for name, path in sorted(files)]
    return hashlib.sha256(json.dumps([records, config], sort_keys=True).encode()).hexdigest()


def load_module(path):
    spec = importlib.util.spec_from_file_location('_build_' + Path(path).stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quoted_inputs(root, paths, search=()):
    """Follow local includes, including missing files and production *.inc files."""
    root = Path(root)
    pending, seen = list(paths), set()
    while pending:
        path = Path(os.path.normpath(pending.pop()))
        if path in seen:
            continue
        seen.add(path)
        if path.suffix not in ('.c', '.m', '.mm', '.cpp', '.h', '.inc') or not path.is_file():
            continue
        for name in re.findall(r'^\s*#\s*(?:include|import)\s*"([^"]+)"', path.read_text(), re.M):
            candidates = [Path(os.path.normpath(folder / name)) for folder in (path.parent, *search)]
            pending.append(next((p for p in candidates if p.is_file()), candidates[0]))
    return sorted(seen)


def python_inputs(root, paths):
    """Discover local Python imports without executing generators or the translator."""
    root = Path(root)
    pending, seen = list(paths), set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if path.suffix != '.py' or not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '', *[(node.module + '.' if node.module else '') + a.name for a in node.names]]
            for name in names:
                for base in (path.parent, root / 'recomp', root / 'tools', root / 'tools_snapshot',
                             root / 'tools_snapshot/vaults'):
                    module = base / name.replace('.', '/')
                    for candidate in (module.with_suffix('.py'), module / '__init__.py'):
                        if candidate.is_file():
                            pending.append(candidate)
    return sorted(seen)


def recomp_inputs(root=ROOT, skip_server=False):
    root = Path(root)
    paths = python_inputs(root, [root / 'recomp/build_all.py', root / 'recomp/build_inputs.py'])
    paths += [root / ('recomp/' + n) for n in ('runtime.h', 'hooks.txt', 'functions_v13.tsv')]
    if not skip_server:
        paths += [root / 'recomp/server_functions.tsv']
    # Only the observation sites in this C source are read by the generator;
    # its host includes and generated diagnostic body are runtime inputs.
    return quoted_inputs(root, paths, [root / 'recomp']) + [root / 'runtime/breaker_diag.c']


def executable_key(exe):
    """Use loaded code/data/layout, excluding resources, timestamps and overlay."""
    try:
        parts, fingerprint = load_module(ROOT / 'recomp/exe_fingerprint.py').describe(Path(exe).read_bytes())
        if all(parts.values()):
            return fingerprint
    except (OSError, ValueError):
        pass
    # Incomplete/malformed inputs still have distinct keys; the real loader
    # rejects them before producing an artifact.
    return digest(exe)


def external_key(root=ROOT, compiler=None):
    """External production inputs must invalidate even the gate's early cache."""
    root = Path(root)
    exe = Path(os.environ.get('NFS_SPEED_EXE') or root.parent / 'Need For Speed Most Wanted Black Edition/speed.exe')
    server = Path(os.environ.get('NFS_SERVER_DLL') or exe.parent / 'server.dll')
    files = [('speed.exe', exe), ('server.dll', server)]
    if os.environ.get('RECOMP_LISTING') == 'ghidra':
        files += [(n, root.parent / 'decomp' / n) for n in ('asm/speed_text.asm', 'functions.tsv')]
    return key(files, compiler if compiler is not None else toolchain())


def recomp_key(root=ROOT, exe=None, server=None, skip_server=False, compiler=None, env=None):
    root = Path(root)
    env = os.environ if env is None else env
    exe = Path(exe or env.get('NFS_SPEED_EXE') or
               root.parent / 'Need For Speed Most Wanted Black Edition/speed.exe')
    server = Path(server or env.get('NFS_SERVER_DLL') or exe.parent / 'server.dll')
    files = [(str(p.relative_to(root)), p) for p in recomp_inputs(root, skip_server)]
    if not skip_server:
        files += [('server.dll', server)]
    if env.get('RECOMP_LISTING') == 'ghidra':
        files += [(n, root.parent / 'decomp' / n) for n in ('asm/speed_text.asm', 'functions.tsv')]
    return key(files, dict(toolchain=compiler if compiler is not None else toolchain(), skip_server=skip_server,
                           executable=executable_key(exe)))


def outputs(root=ROOT):
    out = Path(root) / 'recomp/out'
    paths = [out / n for n in ('speed.a', 'funcs.h', 'breaker_diag.inc')]
    paths += source_glob(out / 'server', '*.h') + source_glob(out / 'server', '*.a')
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


def runtime_key(root=ROOT, compiler=None, public=None):
    root = Path(root)
    paths = [p for p in (root / 'runtime').iterdir() if p.suffix in ('.c', '.m', '.mm', '.cpp', '.h')
             and not p.name.startswith('.')
             and '_test' not in p.stem and not p.stem.startswith('test_')]
    paths += [root / 'runtime/build.sh', root / 'runtime/env.sh']
    paths += source_glob(root / 'runtime/gen', '*.inc')
    paths += source_glob(root / 'recomp', '*.h') + source_glob(root / 'recomp/out', '*.h')
    paths += source_glob(root / 'recomp/out/server', '*.h') + source_glob(root / 'recomp/out/server', '*.a')
    paths += [root / 'recomp/out/speed.a', root / 'recomp/build_inputs.py',
              root / 'recomp/hook_coverage.py', root / 'recomp/hooks.txt']
    for folder in ('host/audio/build', 'host/shaders/build', 'third_party/ffmpeg/lib'):
        paths += source_glob(root / folder, '*.a')
    for folder in ('host/audio', 'host/shaders', 'media', 'third_party/ffmpeg/include'):
        paths += source_glob(root / folder, '*.h', recursive=True)
    # Production can include files named *_test or *.inc (the live carpaint
    # diagnostic and the generated music manifest). Follow quoted includes
    # instead of assuming their suffix/name makes them test-only.
    search = [root / p for p in ('recomp', 'recomp/out', 'third_party/ffmpeg/include')]
    files = [(str(p.relative_to(root)), p) for p in quoted_inputs(root, paths, search)]
    return key(files, dict(toolchain=compiler if compiler is not None else toolchain(),
                           public=os.environ.get('NFS_PUBLIC', '0') if public is None else public))


def stage_inputs(name, root=ROOT, pc=None, x360=None, env=None):
    """Portable dependency discovery; build commands and output roots stay target-specific."""
    root = Path(root)
    if name == 'recompile':
        return recomp_inputs(root)
    if name == 'tables':
        generator = load_module(root / 'tools/gen_tables.py')
        zz = generator.zzdata0_path(x360, environment=env)
        sources = python_inputs(root, [root / 'tools/gen_tables.py', *[root / n for n in generator.GENERATORS]])
        # The auxiliary-light/glass generators compile a HUFF helper against
        # runtime.h, even when the translated speed_32.c bytes stay unchanged.
        sources = quoted_inputs(root, sources + [root / 'recomp/runtime.h'], [root / 'recomp'])
        return sorted(set(sources + generator.inputs(Path(pc), x360, zz)))
    if name == 'ffmpeg':
        return [root / n for n in ('media/build_ffmpeg.sh', 'media/check_ffmpeg_sources.py',
                                  'media/check_libraries.py', 'media/library_members.tsv',
                                  'third_party/dl/ffmpeg-7.1.1.tar.xz')]
    if name == 'audio':
        paths = [root / n for n in ('host/audio/build.sh', 'host/audio/dsound_host.cpp', 'media/media.cpp')]
        paths += source_glob(root / 'third_party/ffmpeg/include', '*.h', recursive=True)
        return quoted_inputs(root, paths)
    if name == 'shaders':
        return quoted_inputs(root, [root / ('host/shaders/' + n) for n in ('build.sh', 'sm_parse.cpp', 'sm_msl.cpp')])
    raise ValueError('unknown build stage: ' + name)


def stage_toolchain(name, compiler, env, target):
    # Setup's sanitized child environment is passed explicitly, never the UI's
    # inherited compiler flags. Flags inside drivers are hashed with the driver.
    identity = {k: v for k, v in compiler.items() if k not in
                ('packages', 'package-files', 'capstone-files', 'capstone', 'python', 'python-binary', 'tools',
                 'llvm_flags', 'RECOMP_CFLAGS', 'RECOMP_LISTING')}
    used = dict(recompile=('clang', 'ar'), tables=('clang', 'clang++', 'ld'),
                ffmpeg=('clang', 'clang++', 'ar', 'make', 'ld'), audio=('clang++', 'ar'),
                shaders=('clang++', 'ar'), runtime=('clang', 'ld'))[name]
    for group in ('versions', 'selected', 'developer_tools'):
        if group in identity:
            identity[group] = {k: v for k, v in identity[group].items() if k in used or k == 'xcrun'}
    if name in ('recompile', 'tables'):
        identity['python'] = compiler.get('python', sys.version)
        identity['python-binary'] = compiler.get('python-binary')
        packages = compiler.get('packages', {})
        identity['packages'] = ({'capstone': packages.get('capstone', compiler.get('capstone'))} if name == 'recompile' else packages)
        package_sources = compiler.get('package-files', {})
        identity['package-files'] = ({'capstone': package_sources.get('capstone', compiler.get('capstone-files'))}
                                     if name == 'recompile' else package_sources)
    variables = ('SDKROOT', 'MACOSX_DEPLOYMENT_TARGET', 'DEVELOPER_DIR', 'CPATH', 'C_INCLUDE_PATH',
                 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH')
    if name == 'recompile':
        variables += ('RECOMP_CFLAGS', 'RECOMP_LISTING')
    if name == 'ffmpeg':
        variables += ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'ASFLAGS')
    return dict(target=target, compiler=identity, environment={k: env.get(k, '') for k in variables})


def stage_key(name, root=ROOT, pc=None, x360=None, compiler=None, env=None,
              target='arm64-apple-darwin', arguments=()):
    """No PC/X360 tree digest or source-copy identity belongs in a stage key."""
    root = Path(root)
    env = os.environ if env is None else env
    config = stage_toolchain(name, compiler if compiler is not None else toolchain(target=target), env, target)
    config.update(schema=1, stage=name, arguments=list(arguments))
    if name in ('tables', 'runtime'):
        config['public'] = env.get('NFS_PUBLIC', '0')
    if name == 'recompile':
        # Pass explicit paths/config: Setup's parent environment is not its child environment.
        return recomp_key(root, exe=Path(pc) / 'speed.exe', server=Path(pc) / 'server.dll', compiler=config, env=env)
    if name == 'tables':
        generator = load_module(root / 'tools/gen_tables.py')
        config['inputs'] = generator.stamp_text(Path(pc), x360, generator.zzdata0_path(x360, environment=env))
        # The stamp includes the declared graph; follow imports/includes as well.
        paths = stage_inputs(name, root, pc, x360, env)
        files = [(str(p.relative_to(root)), p) for p in paths if p.is_relative_to(root)]
        return key(files, config)
    if name == 'runtime':
        # runtime_key already follows generated headers/tables and linked archives.
        config['public'] = env.get('NFS_PUBLIC', '0')
        return runtime_key(root, compiler=config, public=env.get('NFS_PUBLIC', '0'))
    return key([(str(p.relative_to(root)), p) for p in stage_inputs(name, root)], config)


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
