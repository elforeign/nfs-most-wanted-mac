"""Synthetic CRT-prefix and adapter regression, discovered by offline_tests.sh.

Pinned original host_import: first-import fixture exits 10; adapters enabled exit
2 at GetVersionExA. Fixed fixture exits 0 (51 guest import calls); the verified
call-site checkpoint exits 12. Broken stdcall cleanup exits 2. Fatal pointer,
unknown-import and invalid-host-target controls also exit 2. All data is synthetic.
Use --desktop for the same fixture with actual Windows APIs and SDK/CRT headers;
this neither loads speed.exe nor claims entry into game main.
Review regressions: original aggregate heap check exit 1, LastError check exit 1,
contended lock check exit 2; each repaired check exits 0. Pinned adapter bodies
reproduce each original defect independently, without repository history.
Desktop wrapper integration: check-boot runs this script with --desktop.
Event adapter quota: old expectation 109 free imports exits 1; fixed 103 exits 0.
boot-prefix runs nfsmw-headless --boot-prefix <speed.exe> and accepts only
exit 12 together with the exact BOOT PREFIX PASS: 007C4144 line. Smoke keeps
its existing exit-10 check. Wrapper, SDK link and real-prefix runs are separate.

Prefix trace: generated speed_52.c f_007C4040 calls __chkstk (007C94B0),
GetVersionExA (007C4089), check_managed_app (007C42A0), __heap_init (007CD4B0),
__mtinit (007CCF60), empty __RTC_Initialize (007CCE50), __ioinit (007CCA20),
then GetCommandLineA (007C4144, return PC 007C414A).
Named decomp speed_007ca840.c: __heap_select chooses heap 1 for NT major >4;
__mtinitlocks/___crtInitCritSecAndSpinCount resolve the spin initializer;
__mtinit probes absent optional Fls* and uses existing TlsAlloc/Get/Set imports.
The debug allocator (__nh_malloc_dbg/__heap_alloc_dbg in speed_007b4020.c)
uses Enter/LeaveCriticalSection and HeapAlloc via __heap_alloc_base.
__ioinit gets zero inherited descriptors, three guest character handles, and
SetHandleCount. GetLastError/SetLastError/GetCurrentThreadId cover CRT TLS use.
The next frontier after the checkpoint is ___crtGetEnvironmentStringsA at
007CC800 (GetEnvironmentStringsW, conversion/free imports), before argv parsing.
"""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
WIN = ROOT / 'runtime/win'

# Pin the old dispatch body; the regression must reach the actual host_import.
ORIGINAL_IMPORT = r'''void host_import(Cpu *c, uint32_t slot) {
    const struct ImportSlot *entry=win_import_find(speed_imports, speed_import_count, slot);
    if (!entry) win_fail("unknown IAT slot", slot);
    if (win_first_import) win_import_stop(c, entry);
    gil_maybe_yield(c);
    const char *fn=entry->name;
    for (const char *p=fn; *p; ++p) if (*p=='!' || *p==':') fn=p+1;
    if ((!__builtin_strncmp(entry->name, "KERNEL32.dll!", 13) ||
         !__builtin_strncmp(entry->name, "WINMM.dll!", 10)) && win_kernel_import(c, fn)) return;
    win_import_stop(c, entry);
}
'''


# Pre-repair adapter bodies, applied individually to the same regression fixture.
ORIGINAL_HEAP_ALLOC = r'''static uint32_t heap_alloc(Cpu *c) {
    uint32_t handle=win_arg(c,0), flags=win_arg(c,1), size=win_arg(c,2);
    if (handle<HEAP_BASE || (handle&3) || (handle-HEAP_BASE)/4>=heap_count) return error(c,6,0);
    if (flags&~9u) return error(c,87,0);
    unsigned i=(handle-HEAP_BASE)/4;
    if (heaps[i].maximum && size>heaps[i].maximum) return error(c,8,0);
    uint32_t p=win_alloc(size?size:1);
    if (!p) return error(c,8,0);
    if (flags&8) memset(G+p,0,size);
    return p;
}
'''
ORIGINAL_LOCK_ENTER = r'''static uint32_t lock_enter(Cpu *c) {
    uint32_t p=win_arg(c,0), tid=M32(c->fs_base+0x24);
    unsigned i=lock_find(c,p);
    if (i==lock_count || (locks[i].depth && locks[i].owner!=tid) || locks[i].depth==UINT32_MAX)
        win_fail("EnterCriticalSection",p);
    locks[i].owner=tid; ++locks[i].depth;
    M32(p+4)=locks[i].depth-1; M32(p+8)=locks[i].depth; M32(p+12)=tid;
    return 0;
}
'''


def run(argv, code=0):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
    assert p.returncode == code, (f'{argv[0]} exit {p.returncode}, expected {code}: '
                                  f'{p.stdout[-2000:]} {p.stderr[-2000:]}')
    return p.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true')
    args = parser.parse_args()
    if args.desktop and os.name != 'nt':
        parser.error('--desktop requires native Windows SDK/CRT')
    compiler = shutil.which('clang')
    if not compiler:
        print('SKIP: no C compiler')
        return 3
    with tempfile.TemporaryDirectory(prefix='windows-boot-') as scratch:
        folder = Path(scratch)
        base = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-I', WIN, '-I', ROOT/'recomp', '-I', folder]
        if os.name == 'nt':
            base += ['-fms-runtime-lib=static']
        else:
            base += ['-fsanitize=address,undefined', '-DNFS_WIN_TEST', '-pthread']
            # Reuse the existing host fixture's fake OS surface, without its
            # import table or main. Native Windows uses real APIs instead.
            fake = (WIN/'fixture.h').read_text().split('const struct ImportSlot speed_imports[]')[0]
            assert 'void *VirtualAlloc' in fake and 'int main' not in fake
            (folder/'fake_win.inc').write_text(fake)
        units = [WIN/n for n in ('portable.c', 'memory.c', 'loader.c', 'core.c',
                                  'kernel.c', 'imports.c', 'startup.c', 'threads.c', 'sync.c')]
        source = folder/'fixture.c'
        source.write_text('#include "boot_fixture.h"\n')
        suffix = '.exe' if os.name == 'nt' else ''
        fixed = folder/('fixed'+suffix)
        run([*base, *units, source, '-o', fixed])
        output = run([fixed])
        assert 'BOOT FIXTURE PASS:' in output
        print('fixed: exit 0; ' + output.strip())
        startup = (WIN/'startup.c').read_text()
        for mode, begin, end, pinned, old_code in (
                ('heap-limit', 'static uint32_t heap_alloc(', 'static unsigned lock_find', ORIGINAL_HEAP_ALLOC, 1),
                ('heap-error', 'static uint32_t heap_alloc(', 'static unsigned lock_find', ORIGINAL_HEAP_ALLOC, 1),
                ('lock-wait', 'static uint32_t lock_enter(', 'static uint32_t lock_leave', ORIGINAL_LOCK_ENTER, 2)):
            run([fixed, mode])
            a, b = startup.index(begin), startup.index(end)
            broken_startup = folder/(mode+'.c')
            broken_startup.write_text(startup[:a]+pinned+startup[b:])
            broken_units = [broken_startup if p.name == 'startup.c' else p for p in units]
            executable = folder/(mode+suffix)
            run([*base, *broken_units, source, '-o', executable])
            output = run([executable, mode], old_code)
            assert ('failed line' if old_code == 1 else 'EnterCriticalSection') in output, output
            print(f'{mode}: pinned original exit {old_code}; fixed exit 0')
        for mode, code, expected in (
                ('first', 10, 'first import KERNEL32.dll!GetVersionExA'),
                ('unknown', 2, 'unsupported import KERNEL32.dll!GetEnvironmentStringsW'),
                ('bad-slot', 2, 'unknown IAT slot'),
                ('bad-stack', 2, 'GetVersionExA'),
                ('uncommitted-stack', 2, 'GetVersionExA'),
                ('bad-startup', 2, 'GetStartupInfoA'),
                ('bad-host', 2, 'InitializeCriticalSectionAndSpinCount'),
                ('missing-host', 2, 'unregistered host function'),
                ('host-end', 2, 'unknown dispatch target'),
                ('incomplete', 2, 'incomplete CRT prefix'),
                ('checkpoint', 12, 'BOOT PREFIX PASS: 007C4144')):
            output = run([fixed, mode], code)
            assert expected in output, (mode, output)
        print('controls: first=10; 9 fatal cases=2; synthetic checkpoint=12')
        # Compile the pinned original host_import with the exact same fixture.
        current = (WIN/'imports.c').read_text()
        assert current.count('void host_import(') == 1
        broken = folder/'original.c'
        broken.write_text(current[:current.index('void host_import(')] + ORIGINAL_IMPORT)
        old_units = [broken if p.name == 'imports.c' else p for p in units]
        original = folder/('original'+suffix)
        run([*base, *old_units, source, '-o', original])
        assert 'first import' in run([original, 'first'], 10)
        assert 'unsupported import KERNEL32.dll!GetVersionExA' in run([original], 2)
        print('pinned original: first=10; adapters enabled=2; fixed=0')
        mutant = folder/'cleanup.c'
        wrong = current.replace('win_ret(c,value,a->args);', 'win_ret(c,value,0);')
        assert wrong != current
        mutant.write_text(wrong)
        bad_units = [mutant if p.name == 'imports.c' else p for p in units]
        bad = folder/('cleanup'+suffix)
        run([*base, *bad_units, source, '-o', bad])
        assert 'fixture stdcall/register proof' in run([bad], 2)
        print('stdcall mutation: exit 2; exact esp failure detected')
        if os.name == 'nt':
            print('DESKTOP BOOT CHECK PASS: synthetic guest only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
