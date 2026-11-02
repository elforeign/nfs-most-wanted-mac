"""Synthetic Windows host proof, discovered by runtime/offline_tests.sh.

Good: native fixture exits 0; wrapping-range mutation exits 1; unsorted-import
mutation exits 1; missing-host-symbol mutation is rejected (1 unknown).
Desktop boundary/SDK model: old audit rejects 8; corrected audit rejects 0.
Coverage: missing inline/compiled bodies rejected; dotfile mutant rejected.
Traffic fallback: pre-fix coverage raises missing traffic_detail_on; fixed returns
Off, and one newly declared dependency without a Windows body still fails.
The fixture uses fake Windows APIs and synthetic guest code only, never a game.
"""
from pathlib import Path
import importlib.util
import shutil
import struct
import subprocess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / 'runtime/win'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'recomp' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def desktop_layout(obj):
    """Valid COFF with ASCII data immediately after the bounded .drectve.

    Whole-file regex scanners consume that data as part of the final alias.
    Relocate the section so this fixture is independent of clang's layout.
    """
    data = bytearray(obj.read_bytes())
    count = struct.unpack_from('<H', data, 2)[0]
    start = 20 + struct.unpack_from('<H', data, 16)[0]
    for offset in range(start, start + count * 40, 40):
        if data[offset:offset+8].rstrip(b'\0') != b'.drectve':
            continue
        size, pointer = struct.unpack_from('<II', data, offset + 16)
        content = data[pointer:pointer+size]
        data[pointer:pointer+size] = b' ' * size
        struct.pack_into('<I', data, offset + 20, len(data))
        data.extend(content + b'X')
    obj.write_bytes(data)


def check_desktop_model(compiler, folder, audit):
    objects = []
    for k in range(6):
        name = f'f_{0x112230 + k:08X}'
        source = folder / f'boundary{k}.c'
        source.write_text(f'void {name}_orig(void) {{}}\nvoid {name}(void);\n'
                          f'#pragma comment(linker, "/alternatename:{name}={name}_orig")\n'
                          f'void entry{k}(void) {{ {name}(); }}\n')
        obj = source.with_suffix('.obj')
        run([compiler, '-target', 'x86_64-pc-windows-msvc', '-c', source, '-o', obj])
        desktop_layout(obj)
        objects.append(obj)
    sdk = folder / 'sdk.c'
    sdk.write_text('short _dsign(double); int puts(const char *);\n'
                   'int sdk_probe(double x) { return _dsign(x) + puts("fixture"); }\n')
    sdkobj = sdk.with_suffix('.obj')
    run([compiler, '-target', 'x86_64-pc-windows-msvc', '-c', sdk, '-o', sdkobj])
    objects.append(sdkobj)
    old_desktop = {k: v for k, v in audit.DESKTOP.items() if k not in ('_dsign', 'puts')}
    with patch.object(audit, 'directives', lambda data: [data.decode('latin1')]), \
            patch.object(audit, 'DESKTOP', old_desktop):
        assert not audit.audit(compiler, objects, [], folder)
        import json
        broken = json.loads((folder / 'symbol-closure.json').read_text())
        assert len(broken['unknown']) == 8, broken
    assert audit.audit(compiler, objects, [], folder)
    assert audit.DESKTOP['_dsign'] == audit.DESKTOP['puts'] == 'ucrt/libcmt/vcruntime'
    print('desktop boundary + SDK model: old rules 8 unknown; corrected rules 0')


def check_traffic_feature(folder):
    coverage = load('hook_coverage')
    out = folder / 'traffic-inline'
    out.mkdir()
    header = 'int traffic_detail_on(void);\n' + ''.join(
        f'void {n}(Cpu *c);\nvoid {n}_orig(Cpu *c);\n' for n in sorted(coverage.hooks()))
    wrappers = ''.join(f'__attribute__((weak)) void {name}(Cpu *c) '
                       f'{{ {name}_orig(c); }}\n' for name in sorted(coverage.hooks()))
    (out/'funcs.h').write_text(header)
    (out/'unit.c').write_text(wrappers + 'void probe(void) { traffic_detail_on(); }\n')
    rows = coverage.generate(out)
    row = next(r for r in rows if r['symbol'] == 'traffic_detail_on')
    assert row['windows'] == dict(bodies=['runtime/win/features.c'], resolution='host')
    # Compile the selected Windows source, including the fallback, as native C.
    # A headless fail-stop here would exit 2 instead of preserving the Off path.
    probe = folder / 'traffic.c'
    probe.write_text('#include "host.h"\n#include <stdlib.h>\n'
                     '_Noreturn void win_fail(const char *s,uint32_t a) '
                     '{ (void)s;(void)a;exit(2); }\n'
                     'int traffic_detail_on(void);\n'
                     'int main(void) { return traffic_detail_on()!=0; }\n')
    exe = folder / 'traffic'
    run(['clang', '-DNFS_WIN_TEST', '-I', WIN, '-I', ROOT/'recomp',
         WIN/'features.c', probe, '-o', exe])
    run([exe])
    # Declare and call a *new* inline dependency without a Windows body. The
    # ordinary coverage generator must still reject it by its exact name.
    (out/'funcs.h').write_text(header + 'int undeclared_feature(void);\n')
    (out/'unit.c').write_text(wrappers + 'void probe(void) { undeclared_feature(); }\n')
    # Supply the Mac body so this specifically reaches Windows validation.
    actual = {h: coverage.source_bodies(h) for h in ('mac', 'windows')}
    actual['mac']['undeclared_feature'] = ['runtime/synthetic.c']
    with patch.object(coverage, 'source_bodies', side_effect=lambda host: actual[host]):
        try:
            coverage.generate(out)
        except ValueError as error:
            assert str(error) == 'windows: inline dependency has no host body: undeclared_feature'
        else:
            raise AssertionError('undeclared Windows dependency accepted')
    print('traffic Windows fallback: returns Off; new undeclared dependency rejected')


def check_coverage(folder):
    coverage, driver, inputs = (load(n) for n in ('hook_coverage', 'build_windows', 'build_inputs'))
    root = folder / 'coverage'
    recomp, runtime = root / 'recomp', root / 'runtime'
    out, win = recomp / 'out', runtime / 'win'
    out.mkdir(parents=True)
    win.mkdir(parents=True)
    names = {'f_00112233', 'f_00112244'}
    (recomp / 'hooks.txt').write_text('00112233\n00112244\n')
    (recomp / 'runtime.h').write_text('uint32_t feature(void);\n')
    (runtime / 'build.sh').write_text('clang mac.c\n')
    (runtime / 'mac.c').write_text('void f_00112233(Cpu *c) {}\n'
                                  'void f_00112244(Cpu *c) {}\n'
                                  'uint32_t feature(void) {}\nint diag_on;\n')
    (win / 'host.c').write_text('void f_00112233(Cpu *c) {}\n'
                              'uint32_t feature(void) {}\nint diag_on;\n')
    (out / 'funcs.h').write_text('uint32_t feature(void);\nextern int diag_on;\n' + ''.join(
        f'void {n}(Cpu *c);\nvoid {n}_orig(Cpu *c);\n' for n in sorted(names)))
    source = ''.join(f'void {n}_orig(Cpu *c) {{ if (diag_on) feature(); }}\n'
                     f'__attribute__((weak)) void {n}(Cpu *c) {{ {n}_orig(c); }}\n'
                     for n in sorted(names))
    (out / 'unit.c').write_text(source)
    planted = out / '._x.c'
    planted.write_bytes(b'AppleDouble\x00')
    (win / '._host.c').write_bytes(b'not C\x00')
    with patch.object(coverage, 'HERE', recomp), patch.object(coverage, 'ROOT', root):
        rows = coverage.generate(out)
        assert len(rows) == 4
        assert len(coverage.host_sources('windows')) == 1
        assert coverage.visible(out.glob('*.c')) == [out / 'unit.c']
        own = {r['symbol'] for r in rows if r['windows']['resolution'] == 'host'}
        staged, found = driver.stage_hooks(source, names, own)
        assert set(found) == names
        assert 'void f_00112244(Cpu *c) { f_00112244_orig(c); }' in staged
        assert '/alternatename:f_00112233=f_00112233_orig' in staged
        defined = own | {'f_00112244_orig', 'f_00112244'}
        coverage.check_definitions(rows, 'windows', defined, originals=True)
        mac_nm = ('00000001 (__TEXT,__text) external _feature\n'
                  '00000002 (__TEXT,__text) weak external _f_00112233\n'
                  '                 (undefined) external _absent\n')
        assert coverage.mac_definitions(mac_nm) == {'feature'}
        for missing in ('feature', 'f_00112244_orig', 'f_00112244'):
            try:
                coverage.check_definitions(rows, 'windows', defined - {missing}, originals=True)
            except ValueError:
                pass
            else:
                raise AssertionError('missing body accepted: ' + missing)
        # The old glob sees the planted binary; selection and coverage must not.
        assert planted in list(out.glob('*.c')) and planted not in inputs.source_glob(out, '*.c')
        assert not inputs.production('recomp/._x.c')
        (win / 'host.c').write_text('int diag_on;\n')
        try:
            coverage.generate(out)
        except ValueError as error:
            assert 'feature' in str(error)
        else:
            raise AssertionError('missing inline body accepted')
    print('coverage: host/original selection, 3 missing compiled bodies, missing inline body, dotfiles checked')


def run(argv, code=0):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
    assert p.returncode == code, f'{argv[0]} exit {p.returncode}, expected {code}: {p.stdout[-1800:]} {p.stderr[-1800:]}'
    return p.stdout


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C compiler')
        return 3
    with tempfile.TemporaryDirectory(prefix='windows-host-') as scratch:
        folder = Path(scratch)
        check_traffic_feature(folder)
        base = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-DNFS_WIN_TEST', '-I', WIN,
                '-I', ROOT / 'recomp', '-pthread']
        sources = [WIN / n for n in ('portable.c', 'memory.c', 'loader.c', 'core.c',
                                     'kernel.c', 'imports.c', 'startup.c', 'threads.c', 'sync.c')]
        fixture = WIN / 'fixture.h'
        for label in ('good', 'wrap', 'unsorted'):
            units = list(sources)
            if label != 'good':
                text = (WIN / 'portable.c').read_text()
                if label == 'wrap':
                    text = text.replace('size > WIN_GUEST_SIZE - address', '0')
                    text = text.replace('(uint64_t)address + size + page - 1', '(uint32_t)(address + size) + (uint64_t)page - 1')
                else:
                    text = text.replace('(i && table[i-1].slot >= table[i].slot)', '0')
                mutant = folder / (label + '.c')
                mutant.write_text(text)
                units[0] = mutant
            exe = folder / label
            run([*base, *units, '-x', 'c', fixture, '-o', exe])
            output = run([exe], 0 if label == 'good' else 1)
            print(f'{label}: {output.strip()}')
            if label == 'good':
                for mode, code, expected in (('first', 10, 'first import'),
                                             ('unsupported', 2, 'unsupported import'),
                                             ('unknown', 2, 'unknown IAT slot')):
                    output = run([exe, mode], code)
                    assert expected in output, (mode, output)
                print('stub calls: first=10, unsupported=2, unknown=2; no guessed stack return')
        # Exercise actual COFF closure, not just the audit's set arithmetic.
        audit = load('windows_symbols')
        source = folder / 'closure.c'
        source.write_text('void missing_host(void); void entry(void) { missing_host(); }\n')
        obj = folder / 'closure.obj'
        run([compiler, '-target', 'x86_64-pc-windows-msvc', '-c', source, '-o', obj])
        assert not audit.audit(compiler, [obj], [], folder)
        print('closure mutation: rejected missing_host (1 unknown)')
        check_desktop_model(compiler, folder, audit)
        check_coverage(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
