"""Standalone D3D12 image build, invoked only by recomp/build_windows.py --image.

The shared D3D9 parser/HLSL emitter runs as a build tool on the build host. On a
Mac, additionally compile all new C/C++ units to COFF with declarations only.
Those tool COFF objects are never linked; their STL declarations are not an ABI.
On Windows, the tool and image are rebuilt with the real SDK/CRT/STL headers.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
TARGET = 'x86_64-pc-windows-msvc'


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'recomp' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def execute(argv, log):
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(list(map(str, argv)), stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        print('\n'.join(log.read_text(errors='replace').splitlines()[-12:]))
        raise RuntimeError(f'{log.name}: exit {result.returncode}')


def build(args, output):
    output.mkdir(parents=True, exist_ok=True)
    for name in ('objects.rsp', 'report.json', 'symbol-closure.json', 'hook-coverage.json'):
        (output / name).unlink(missing_ok=True)
    # Never leave a previous translated program available after a failed rebuild.
    for name in ('fe_vs.hlsl', 'fe_ps.hlsl', 'nfsmw-image.exe'):
        (output / name).unlink(missing_ok=True)
    shaders = ROOT / 'host/shaders'
    units = [shaders / 'sm_hlsl.cpp', HERE / 'translate.cpp', HERE / 'image.c']
    compiler = args.compiler
    extra = shlex.split(os.environ.get('RECOMP_CFLAGS', ''))
    tool = output / ('shader-hlsl.exe' if os.name == 'nt' else 'shader-hlsl')
    report = dict(target=TARGET, sdk_headers=args.sdk_headers, standalone=True, units=[],
                  shader_pair='fe/t0_world_p0_vs_1_1 + fe/t0_world_p0_ps_1_1')
    try:
        for stage in ('vs', 'ps'):
            binary = args.image_shader_dir / f't0_world_p0_{stage}_1_1.bin'
            if not binary.is_file():
                raise RuntimeError(f'missing retail bytecode: {binary}; supply --image-shader-dir')
        execute([compiler, '--driver-mode=g++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                 '-D_CRT_SECURE_NO_WARNINGS',
                 *extra, '-I', shaders, shaders / 'sm_parse.cpp', *units[:2], '-o', tool], output / 'tool.log')
        for stage in ('vs', 'ps'):
            execute([tool, args.image_shader_dir / f't0_world_p0_{stage}_1_1.bin', output / f'fe_{stage}.hlsl'],
                    output / f'{stage}.log')
        image_objects = []
        for unit in units:
            obj = output / (unit.stem + '.obj')
            common = [compiler, '-target', TARGET, '-O2', '-Wall', '-Wextra', '-Werror',
                      '-D_CRT_SECURE_NO_WARNINGS', *extra]
            if unit.suffix == '.cpp':
                common += ['-std=c++17', '-I', shaders]
                if not args.sdk_headers:
                    common += ['-I', HERE / 'compile']
            else:
                common += ['-std=c11']
            if not args.sdk_headers:
                common += ['-ffreestanding', '-DRECOMP_WINDOWS_COMPILE_ONLY']
            execute([*common, '-c', unit, '-o', obj], output / (unit.stem + '.log'))
            if obj.read_bytes()[:2] != b'\x64\x86':
                raise RuntimeError(f'not x64 COFF: {obj.name}')
            report['units'].append(dict(source=unit.relative_to(ROOT).as_posix(), bytes=obj.stat().st_size,
                                        role='image' if unit.suffix == '.c' else 'build-tool-only'))
            if unit.suffix == '.c':
                image_objects.append(obj)
        symbols, coverage = module('windows_symbols'), module('hook_coverage')
        if not symbols.audit(compiler, [], image_objects, output, sdk_headers=args.sdk_headers):
            raise RuntimeError('image symbol closure failed')
        closure = json.loads((output / 'symbol-closure.json').read_text())
        dependencies = coverage.image_dependencies(closure['desktop'], symbols.DESKTOP)
        coverage.write(dependencies, output / 'hook-coverage.json')
        report.update(compiled=len(units), total=len(units), native_dependencies=len(dependencies))
        (output / 'objects.rsp').write_text(''.join(f'"{obj}"\n' for obj in image_objects), encoding='utf-8')
        print(f'COMPILED image {len(units)}/{len(units)}; native dependencies {len(dependencies)}; unknown 0')
        print('Shaders: retail fe SM1.1 pair -> fe_vs.hlsl / fe_ps.hlsl; native run pending')
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
        print(f'IMAGE BUILD FAIL: {error}')
        (output / 'objects.rsp').unlink(missing_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return int('error' in report)
