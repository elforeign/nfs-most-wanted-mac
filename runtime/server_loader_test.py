"""











"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'recomp'))
import recomp


def optional_archive():
    with tempfile.TemporaryDirectory(prefix='server-absent-') as td:
        tmp = Path(td)
        test = tmp/'test.c'
        test.write_text(r'''#include "server_loader.h"
#include <stdio.h>
int main(void) {
    unsigned failed = 0;
    failed += server_load(NULL, "missing") != 0;
    failed += server_handle() != 0;
    failed += server_proc(SERVER_BASE, 1) != 0;
    failed += server_free(NULL, SERVER_BASE) != 0;
    failed += server_dispatch(NULL, SERVER_BASE + 0x10b0) != 0;
    failed += server_import(SERVER_BASE, NULL) != NULL;
    failed += server_thread_attach(NULL) != 0;
    server_thread_detach(NULL);
    printf("archive absent: 7 checks, %u failures\n", failed);
    return failed != 0;
}
''')
        source = (ROOT/'runtime/server_loader.c').read_text()
        for name, text in [('normal', source), ('broken', source.replace('(void)c; (void)path; return 0;', '(void)c; (void)path; return SERVER_BASE;'))]:
            loader = tmp/'loader.c'; loader.write_text(text)
            exe = tmp/name
            p = subprocess.run(['clang', '-O1', '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                                str(test), str(loader), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-2000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            assert p.returncode == (0 if name == 'normal' else 1), p.stdout
            print(name, p.stdout.strip(), flush=True)


def main():
    optional_archive()
    dll = Path(os.environ.get('NFS_SERVER_DLL') or Path(recomp.EXE).with_name('server.dll'))
    archive = ROOT / 'recomp/out/server/server.a'
    if not dll.exists() or not archive.exists():
        print('SKIP: needs owner server.dll and generated server.a')
        return 3
    with tempfile.TemporaryDirectory(prefix='server-loader-') as td:
        tmp = Path(td)
        (tmp/'server.dll').symlink_to(dll)
        corrupt = bytearray(dll.read_bytes()); corrupt[-1] ^= 1
        (tmp/'corrupt.dll').write_bytes(corrupt)
        (tmp/'localize.csv').write_text('LAN test\n')
        # Keep all production boot logic through host_import; omit only the
        # deterministic race setup and executable's main (never invoked here).
        boot = (ROOT / 'runtime/boot.c').read_text().split('// ---------------- deterministic race start')[0]
        harness = tmp / 'harness.c'
        boot = boot.replace('void dispatch(Cpu *c, uint32_t t) {', 'static void before_dispatch(Cpu *, uint32_t);\nvoid dispatch(Cpu *c, uint32_t t) {\n    before_dispatch(c, t);')
        source = (ROOT / 'runtime/server_loader.c').read_text()
        ws = (ROOT / 'runtime/ws2_32.c').read_text()
        threads = (ROOT / 'runtime/threads.c').read_text()
        release = 't->type = K_FREE; // module unload releases the abandoned thread handle'
        assert threads.count(release) == 1, 'module-handle release mutation must apply exactly once'
        # Test-only visibility into the real allocator, with no runtime API.
        threads += '\nunsigned test_handle_count(void) { unsigned n=0; for(unsigned i=1;i<MAX_KOBJ;++i) n+=kobj[i].type!=K_FREE; return n; }\n'
        variants = {
            'normal': {},
            'attach_failure': {'loader': source.replace('uint32_t attached = entry(c, 1);', 'uint32_t attached = entry(c, 1); attached = 0;')},
            'bss': {'loader': source.replace('memset(G + server_image_base, 0, server_reserved_end - server_image_base);', '(void)0;', 1)},
            'stop': {'loader': source.replace('call_guest(c, 0x02001000u, 0, NULL);', '(void)0;')},
            'relocation': {'loader': source.replace('+= server_image_base - server_preferred_base;', '+= 0;')},
            'iat': {'loader': source.replace('M32(server_imports[i].slot) = SERVER_THUNK_BASE + 4*i;', 'if (i != 128) M32(server_imports[i].slot) = SERVER_THUNK_BASE + 4*i;')},
            'entry': {'loader': source.replace('uint32_t attached = entry(c, 1);', 'uint32_t attached = 1;')},
            'byte_order': {'ws': ws.replace('__builtin_bswap32(arg(c, 0))', 'arg(c, 0)').replace('__builtin_bswap16((uint16_t)arg(c, 0))', '(uint16_t)arg(c, 0)')},
            'fdset': {'ws': ws.replace('if (M32(set + 4 + 4*i) == arg(c, 0)) RET(1, 2);', 'if (M32(set + 4 + 4*i) == arg(c, 0)) RET(0, 2);')},
            'filetime': {'boot': boot.replace('11644473600ull', '0ull')},
            'thread_notify': {'threads': threads.replace('t->module_attached = server_thread_attach(t->cpu);', 't->module_attached = 0;')},
            'drain': {'loader': source.replace('if (!threads_drain_module(server_image_base, server_reserved_end)) {', 'if (0) {')},
            'handle_release': {'threads': threads.replace(release, '(void)0; // broken: abandon the handle')},
        }
        for name, changes in variants.items():
            loader = tmp / (name + '.c'); loader.write_text(changes.get('loader', source))
            ws_file = tmp / 'ws.c'; ws_file.write_text(changes.get('ws', ws))
            thread_file = tmp / 'threads.c'; thread_file.write_text(changes.get('threads', threads))
            harness.write_text(changes.get('boot', boot) + '\n' + (ROOT / 'runtime/server_loader_test.c').read_text())
            exe = tmp / name
            cmd = ['clang', '-O1', '-g', '-w', '-DNFS_SERVER_LINKED=1', '-I'+str(ROOT/'runtime'),
                   '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'recomp/out'), str(harness), str(loader),
                   str(ROOT/'runtime/heap.c'), str(thread_file), str(ws_file), str(archive),
                   '-Wl,-dead_strip', '-o', str(exe)]
            if name == 'attach_failure':
                cmd.insert(1, '-DEXPECT_ATTACH_FAILURE=1')
            if name in ('normal', 'handle_release'):
                cmd.insert(1, '-DTEST_REHOST_HANDLES=1')
            p = subprocess.run(cmd, capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-5000:]
            p = subprocess.run([str(exe), str(tmp), str(dll), str(tmp/'server.cfg'), str(tmp/'corrupt.dll')], capture_output=True, text=True, timeout=60)
            print(name, 'exit', p.returncode, p.stdout.strip(), flush=True)
            if name in ('normal', 'handle_release'):
                growth = 0 if name == 'normal' else 32
                assert any(line.startswith('rehost handles: 32 ') and line.endswith('growth='+str(growth))
                           for line in p.stdout.splitlines()), ('missing rehost proof', name, p.stdout, p.stderr[-2000:])
            if name in ('normal', 'attach_failure'):
                assert p.returncode == 0, p.stderr[-5000:]
            else:
                assert p.returncode == 1, p.stderr[-5000:]
                print(p.stderr[-500:].strip(), flush=True)
        print('PASS: native attach/export/unload, shims/threads, eleven broken controls')
    return 0

if __name__ == '__main__':
    sys.exit(main())
