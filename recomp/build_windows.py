"""Compile existing generated game C to Windows x64 COFF, without an SDK.

Run with --jobs N; output defaults to .metal-cache/windows. Never translates or
changes recomp/out. The SDK-free header mode is a compile proof, not a runnable
Windows runtime. --sdk-headers uses installed target CRT headers instead.

Mac generation emits a strong f_X_orig and a weak f_X forwarding wrapper.
COFF staging generates a concrete forwarding body when Windows has no host
implementation, or /alternatename:f_X=f_X_orig when it does. A strong runtime
f_X wins and the original body stays independently addressable. Pass ALL objects
directly to the linker; archive extraction of fallback-only members is not proved.
"""
import argparse
import ast
import collections
import concurrent.futures
import functools
import importlib.util
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TARGET = 'x86_64-pc-windows-msvc'


def load_inputs():
    spec = importlib.util.spec_from_file_location('build_inputs', HERE / 'build_inputs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def load_coverage():
    spec = importlib.util.spec_from_file_location('hook_coverage', HERE / 'hook_coverage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flags():
    # Share the Mac policy without importing the translator or its dependencies.
    tree = ast.parse((HERE / 'recomp.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'CFLAGS'
                                                for n in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('recomp.py CFLAGS not found')


def hooks():
    return load_coverage().hooks()


def stage_hooks(source, allowed, host_bodies=None):
    found = []
    def replace(match):
        name = match[1]
        if name not in allowed or name in found:
            raise ValueError(f'unexpected/duplicate hook wrapper: {name}')
        found.append(name)
        if host_bodies is not None and name not in host_bodies:
            return f'void {name}(Cpu *c) {{ {name}_orig(c); }}'
        return f'#pragma comment(linker, "/alternatename:{name}={name}_orig")'
    staged = load_coverage().WRAPPER.sub(replace, source)
    if '__attribute__((weak))' in staged:
        raise ValueError('unrecognized weak wrapper; regenerate or update the COFF staging rule')
    return staged, found


def command(compiler, extra=(), sdk_headers=False):
    return [compiler, '-target', TARGET, *flags(),
            *([] if sdk_headers else ['-ffreestanding', '-DRECOMP_WINDOWS_COMPILE_ONLY']),
            *extra, '-I', str(HERE), '-I', str(HERE / 'out')]


def classify(log):
    for line in log.splitlines():
        if 'error:' not in line:
            continue
        if 'file not found' in line:
            kind = 'missing-header'
        elif 'undeclared function' in line or 'undeclared identifier' in line:
            kind = 'missing-declaration'
        elif 'unsupported' in line or 'not supported' in line:
            kind = 'unsupported-target-feature'
        else:
            kind = 'compiler-error'
        return kind, line
    return 'compiler-exit', log.splitlines()[0] if log else 'no diagnostic'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler', default='clang')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 6))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sdk-headers', action='store_true')
    parser.add_argument('--host-layer', action='store_true', help='include headless host and audit symbol closure')
    parser.add_argument('--image', action='store_true', help='build the standalone D3D12 translated-shader image proof')
    parser.add_argument('--image-shader-dir', type=Path, default=ROOT / 'host/shaders/testdata/fe')
    args = parser.parse_args()
    output = (args.output or ROOT / '.metal-cache' / ('windows-image' if args.image else 'windows')).resolve()
    if not output.is_relative_to(ROOT) or output == HERE / 'out' or (HERE / 'out') in output.parents:
        parser.error('output must be inside the worktree and outside recomp/out')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if args.image:
        if args.host_layer:
            parser.error('--image and --host-layer are separate executables')
        spec = importlib.util.spec_from_file_location('build_image', ROOT / 'runtime/win/image/build_image.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build(args, output)
    coverage = load_coverage()
    units = coverage.visible((HERE / 'out').glob('*.c'))
    if not units:
        parser.error('no generated C units: generate recomp/out first')
    source_dir, objects, logs = (output / name for name in ('source', 'objects', 'logs'))
    for directory in (source_dir, objects, logs):
        directory.mkdir(parents=True, exist_ok=True)
    # Invalidate the previous result before any staging or compiler failure.
    (output / 'report.json').unlink(missing_ok=True)
    (output / 'symbol-closure.json').unlink(missing_ok=True)
    (output / 'objects.rsp').unlink(missing_ok=True)
    for old in coverage.visible(objects.glob('*.obj')):
        old.unlink()
    rows = coverage.generate(HERE / 'out')
    coverage.write(rows, output / 'hook-coverage.json')
    host_bodies = {r['symbol'] for r in rows if r['windows']['resolution'] == 'host'}
    allowed, seen = hooks(), set()
    staged_units = []
    for unit in units:
        source, names = stage_hooks(unit.read_text(encoding='utf-8'), allowed, host_bodies)
        if seen.intersection(names):
            raise ValueError(f'duplicate hook bodies in {unit.name}')
        seen.update(names)
        staged = source_dir / unit.name
        with staged.open('w', newline='\n', encoding='utf-8') as stream:
            stream.write(source)
        staged_units.append(staged)
    if seen != allowed:
        raise ValueError(f'hooks.txt/generated output mismatch: missing {sorted(allowed - seen)}')
    generated_stems = {unit.stem for unit in staged_units}
    if args.host_layer:
        host_dir = ROOT / 'runtime/win'
        sources = [(f'win_{p.name}', p) for p in coverage.host_sources('windows')]
        sources.append(('win_import_table.c', ROOT / 'runtime/imports.c'))
        for name, source in sources:
            staged = source_dir / name
            with staged.open('w', newline='\n', encoding='utf-8') as stream:
                stream.write(source.read_text(encoding='utf-8'))
            staged_units.append(staged)
        # Audio is the first shared C++ host component. Keep the original paths
        # so its local includes and the media interface resolve identically.
        staged_units.extend([ROOT / 'host/audio/dsound_host.cpp', host_dir / 'audio_output.cpp'])
    extra = shlex.split(os.environ.get('RECOMP_CFLAGS', ''))
    base = command(args.compiler, [*extra, '-I', str(ROOT / 'runtime/win')], args.sdk_headers)
    fingerprint = load_inputs().toolchain(target=TARGET, compiler=args.compiler)
    print(f'{TARGET}: {len(staged_units)} units, {len(seen)} hook fallbacks, {args.jobs} jobs', flush=True)

    def compile_one(unit):
        obj, log = objects / (unit.stem + '.obj'), logs / (unit.stem + '.log')
        cpp = []
        if unit.suffix == '.cpp':
            cpp = ['-std=c++17', '-fno-exceptions', '-fno-rtti', '-DNFS_WIN_AUDIO',
                   '-D_CRT_SECURE_NO_WARNINGS']
            if not args.sdk_headers:
                cpp += ['-I', str(ROOT / 'runtime/win/audio_compile')]
        argv = [*base, *cpp, '-c', str(unit), '-o', str(obj)]
        with log.open('w') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT)
        success = result.returncode == 0 and obj.is_file()
        if success and obj.read_bytes()[:2] != struct.pack('<H', 0x8664):
            success = False
        entry = dict(unit=unit.name, ok=success, returncode=result.returncode,
                     bytes=obj.stat().st_size if success else 0, command=argv)
        if not success:
            entry['error_kind'], entry['first_error'] = classify(log.read_text())
            obj.unlink(missing_ok=True)
        print(f'{unit.name}: {"OK" if success else "FAIL"} {entry["bytes"]} bytes', flush=True)
        return entry

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as executor:
        results = list(executor.map(compile_one, staged_units))
    ok = sum(r['ok'] for r in results)
    kinds = dict(collections.Counter(r['error_kind'] for r in results if not r['ok']))
    report = dict(target=TARGET, toolchain=fingerprint, sdk_headers=args.sdk_headers,
                  compiled=ok, total=len(results), object_bytes=sum(r['bytes'] for r in results),
                  hooks=len(seen), first_error_classes=kinds, units=results)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    # Use direct objects, not a static archive: see the extraction contract above.
    (output / 'objects.rsp').write_text(''.join(f'"{objects / (u.stem + ".obj")}"\n'
                                              for u in staged_units) if ok == len(results) else '')
    print(f'COMPILED {ok}/{len(results)}; object bytes {report["object_bytes"]}; first-error classes {kinds}')
    for failure in [r for r in results if not r['ok']][:3]:
        print(f'{failure["unit"]}: {failure["first_error"][:500]}')
    if args.host_layer and ok == len(results):
        spec = importlib.util.spec_from_file_location('windows_symbols', HERE / 'windows_symbols.py')
        symbols = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(symbols)
        generated = [objects / (u.stem + '.obj') for u in staged_units if u.stem in generated_stems]
        host = [objects / (u.stem + '.obj') for u in staged_units if u.stem not in generated_stems]
        combined = symbols.read_symbols(symbols.find_nm(args.compiler), [*generated, *host])
        if args.sdk_headers:
            # Exact archive definitions, not a namespace/prefix exemption for
            # SDK-specific Microsoft STL ABI dependencies of the shared mixer.
            spec = importlib.util.spec_from_file_location('windows_audio_symbols', HERE / 'windows_audio_symbols.py')
            audio_symbols = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(audio_symbols)
            symbols.DESKTOP.update(audio_symbols.native_cpp_symbols(
                symbols.find_nm(args.compiler), set(combined['remaining']) - symbols.DESKTOP.keys()))
        try:
            coverage.check_definitions(rows, 'windows', set(combined['defined']), originals=True)
        except ValueError:
            (output / 'objects.rsp').unlink(missing_ok=True)
            raise
        if not symbols.audit(args.compiler, generated, host, output, args.sdk_headers):
            (output / 'objects.rsp').unlink(missing_ok=True)
            return 1
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
