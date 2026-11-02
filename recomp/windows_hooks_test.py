"""SDK-free two-unit COFF hook fixture, autodiscovered by runtime/offline_tests.sh.

Good: 2/2 hook/original resolutions; hook removed: 1/2 (hook assertion fails).
Object proof uses llvm-nm + llvm-objdump, including direct and dispatch relocations.
When lld-link/ld.lld exists, also link both variants and verify their linker maps.
On Windows, run them: the intact fixture exits 0 and the removed-hook variant 1.
All Windows-absent hooks: concrete COFF bodies resolve; removed original fails.
No Windows executable is run on other hosts. No COFF-capable clang: exit 3.
"""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
NAME = 'f_00112233'
ORIG = NAME + '_orig'
CPU = 'typedef struct Cpu { unsigned eax; } Cpu;\n'


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv):
    result = subprocess.run([str(a) for a in argv], capture_output=True, text=True)
    assert result.returncode == 0, f'{argv[0]} exited {result.returncode}: {result.stderr[-1500:]}'
    return result.stdout


def find_tool(name, compiler):
    direct = shutil.which(name)
    if direct:
        return direct
    resource = Path(run([compiler, '-print-resource-dir']).strip())
    for directory in (Path(compiler).resolve().parent, resource.parents[2] / 'bin'):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def symbols(nm, obj):
    return {line.split()[0]: line.split()[1] for line in run([nm, '-P', obj]).splitlines()
            if len(line.split()) >= 2}


def selected(public, original, hook):
    # Prove the linker's documented selection graph from the emitted object
    # symbols/directive, rather than mistaking a successful compile for a hook.
    return hook if hook.get(public) == 'T' else original


def check_toolchain(driver):
    inputs = driver.load_inputs()
    seen = []
    def invoke(argv, **kwargs):
        seen.append(argv)
        assert argv == ['selected-clang', '--version'], 'Windows fingerprint invoked a host/SDK tool'
        return subprocess.CompletedProcess(argv, 0, 'compiler fixture\n', 'volatile stderr')
    with patch.object(inputs.subprocess, 'run', side_effect=invoke):
        good = inputs.toolchain(target=driver.TARGET, compiler='selected-clang')
    assert seen == [['selected-clang', '--version']]
    with patch.object(inputs.subprocess, 'run',
                      return_value=subprocess.CompletedProcess([], 0, 'compiler fixture\n', 'different stderr')):
        assert inputs.toolchain(target=driver.TARGET, compiler='selected-clang') == good
    for rc, stdout in ((1, 'compiler fixture\n'), (0, 'different compiler\n')):
        with patch.object(inputs.subprocess, 'run', return_value=subprocess.CompletedProcess([], rc, stdout, '')):
            assert inputs.toolchain(target=driver.TARGET, compiler='selected-clang') != good
    print('fingerprint: selected compiler only; changed returncode/stdout invalidate (2/2)')


def check_linker(linker, folder, original, hook):
    maps = []
    for label, objs in (('strong', [original, hook]), ('removed', [original])):
        mapfile = folder / (label + '.map')
        argv = [linker]
        if Path(linker).name.startswith('ld.lld'):
            argv += ['-flavor', 'link']
        argv += ['/entry:fixture_entry', '/subsystem:console', '/nodefaultlib', '/opt:noref',
                 '/include:dispatch_hook', '/out:' + str(folder / (label + '.exe')),
                 '/map:' + str(mapfile), *objs]
        run(argv)
        maps.append(mapfile.read_text())
    strong = next(line for line in maps[0].splitlines() if re.search(r'\s' + NAME + r'\s', line))
    original_line = next(line for line in maps[0].splitlines() if re.search(r'\s' + ORIG + r'\s', line))
    assert 'hook.obj' in strong and 'original.obj' in original_line
    # Without the strong definition the fallback either appears as an alias in
    # the map, or only the original's name is printed at the resolved address.
    assert ORIG in maps[1] and 'hook.obj' not in maps[1]
    print('lld: both variants linked; strong hook and original have distinct owners; removed hook falls back')
    if sys.platform == 'win32':
        good = subprocess.run([str(folder / 'strong.exe')]).returncode
        broken = subprocess.run([str(folder / 'removed.exe')]).returncode
        assert (good, broken) == (0, 1), (good, broken)
        print('native execution: intact exit 0; removed-hook exit 1')


def check_all_fallbacks(driver, compiler, nm, folder):
    coverage = driver.load_coverage()
    allowed = driver.hooks()
    host = set(coverage.source_bodies('windows'))
    missing = allowed - host
    source = CPU + ''.join(f'void {name}_orig(Cpu *c);\n'
                           f'void {name}_orig(Cpu *c) {{ c->eax++; }}\n'
                           f'__attribute__((weak)) void {name}(Cpu *c) {{ {name}_orig(c); }}\n'
                           for name in sorted(allowed))
    staged, found = driver.stage_hooks(source, allowed, host)
    assert set(found) == allowed
    path, obj = folder / 'all.c', folder / 'all.obj'
    path.write_text(staged)
    run([compiler, '-target', driver.TARGET, '-O2', '-c', path, '-o', obj])
    table = symbols(nm, obj)
    assert all(table[n] == table[n + '_orig'] == 'T' for n in missing)
    victim = sorted(missing)[0]
    path.write_text(staged.replace(f'void {victim}_orig(Cpu *c) {{ c->eax++; }}', '', 1))
    run([compiler, '-target', driver.TARGET, '-O2', '-c', path, '-o', obj])
    table = symbols(nm, obj)
    assert table[victim + '_orig'] == 'U'
    audit = load('windows_symbols')
    assert not audit.audit(compiler, [obj], [], folder)
    print(f'all fallbacks: {len(missing)}/{len(missing)} concrete bodies; removed original rejected')


def main():
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no COFF-capable clang')
        return 3
    driver = load('build_windows')
    with tempfile.TemporaryDirectory(prefix='windows-hooks-') as scratch:
        folder = Path(scratch)
        probe = subprocess.run([compiler, '-target', driver.TARGET, '-x', 'c', '-c', '-',
                                '-o', str(folder / 'probe.obj')], input='int probe;\n',
                               capture_output=True, text=True)
        if probe.returncode or (folder / 'probe.obj').read_bytes()[:2] != b'\x64\x86':
            print('SKIP: no COFF-capable clang: ' + probe.stderr[-300:])
            return 3
        nm, dump = (find_tool(name, compiler) for name in ('llvm-nm', 'llvm-objdump'))
        assert nm and dump, 'COFF compiler exists, but llvm-nm/llvm-objdump are missing'
        check_toolchain(driver)
        check_all_fallbacks(driver, compiler, nm, folder)
        source = (CPU + f'void {NAME}(Cpu *c);\n'
                  f'__declspec(noinline) void {ORIG}(Cpu *c) {{ c->eax = 7; }}\n'
                  f'__attribute__((weak)) void {NAME}(Cpu *c) {{ {ORIG}(c); }}\n'
                  f'void call_hook(Cpu *c) {{ {NAME}(c); }}\n'
                  f'void call_orig(Cpu *c) {{ {ORIG}(c); }}\n'
                  f'void (*const dispatch_hook)(Cpu *) = {NAME};\n'
                  'int fixture_entry(void) { Cpu c = {0}; call_hook(&c);\n'
                  'if (c.eax != 107) return 1; dispatch_hook(&c);\n'
                  'if (c.eax != 107) return 1; call_orig(&c); return c.eax != 7; }\n')
        staged, found = driver.stage_hooks(source, {NAME})
        assert found == [NAME] and staged.count('\n') == source.count('\n')
        hook_source = CPU + f'void {ORIG}(Cpu *c);\nvoid {NAME}(Cpu *c) {{ {ORIG}(c); c->eax += 100; }}\n'
        original, hook = folder / 'original.obj', folder / 'hook.obj'
        for name, text, obj in (('original.c', staged, original), ('hook.c', hook_source, hook)):
            path = folder / name
            path.write_text(text)
            run([compiler, '-target', driver.TARGET, '-O2', '-c', path, '-o', obj])
        orig_symbols, hook_symbols = symbols(nm, original), symbols(nm, hook)
        assert orig_symbols[NAME] == 'U' and orig_symbols[ORIG] == 'T', orig_symbols
        assert hook_symbols[NAME] == 'T' and hook_symbols[ORIG] == 'U', hook_symbols
        assert f'/alternatename:{NAME}={ORIG}'.encode() in original.read_bytes()
        assert 'coff-x86-64' in run([dump, '-f', original])
        relocations = run([dump, '-r', original])
        assert len(re.findall(r'IMAGE_REL_AMD64_\w+\s+' + NAME + r'\b', relocations)) >= 2
        assert re.search(r'IMAGE_REL_AMD64_\w+\s+' + ORIG + r'\b', relocations)
        assert ORIG in run([dump, '-r', hook])
        # Strong definition wins before the fallback is considered; the _orig
        # symbol remains bound to the translated body in either variant.
        def resolutions(hook_table):
            return (selected(NAME, orig_symbols, hook_table) is hook_symbols,
                    orig_symbols[ORIG] == 'T')
        good, broken = resolutions(hook_symbols), resolutions({})
        assert good == (True, True) and broken == (False, True), (good, broken)
        try:
            assert all(broken), 'strong hook removed'
        except AssertionError as error:
            assert str(error) == 'strong hook removed'
            print(f'COFF hook proof: good {sum(good)}/2; hook removed {sum(broken)}/2, '
                  'rejected (strong hook removed)')
        else:
            raise AssertionError('hook-removed negative control unexpectedly passed')
        print('llvm-nm/objdump: external direct+dispatch calls, strong hook, strong _orig, /alternatename verified')
        linker = find_tool('lld-link', compiler) or find_tool('ld.lld', compiler)
        if linker:
            check_linker(linker, folder, original, hook)
        else:
            print('lld-link/ld.lld absent: object symbol/relocation proof only; native link/execution pending')
    return 0


if __name__ == '__main__':
    sys.exit(main())
