#!/usr/bin/env python3
"""Per-thread Win32 errors through production host_import and threads.c; no game.

Compiles the actual import dispatcher, error/ReadFile import bodies, file read
helpers and complete pthread/GIL implementation. Only unrelated subsystems and
the clock/audio source are doubles; a fixed clock makes import handovers exact.
Pinned original storage/ReadFile fragments are embedded, never read from git.
Measured before/after: 623 checks, 70 -> 0 failures; 32 forced import handovers,
11 read cases (invalid/stale/write-only/directory, data/EOF, snapshot, audio).
Independent storage/read reversions produce 64/22 failures respectively; reading a
write-only handle as ERROR_INVALID_HANDLE (6, the prior behaviour) produces 1 failure.
Successful reads/EOF preserve the caller's error; new pthreads start at zero.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD_READ = '''    if (!strcmp(fn, "ReadFile")) {                // (h, buf, n, read*, overlapped)
        int fd = handle_fd(arg(c, 0));
        X360MusicHandle *music = handle_music(arg(c, 0));
        ssize_t r = music ? (ssize_t)x360music_read(music, G + arg(c, 1), arg(c, 2)) : fd < 0 ? -1 : file_read(arg(c,0), G + arg(c, 1), arg(c, 2));
        if (arg(c, 3)) M32(arg(c, 3)) = r < 0 ? 0 : (uint32_t)r;
        void threads_note_io(void);
        threads_note_io();                        // wakes the game thread's loading poll (threads.c, SleepEx)
        RET(r >= 0, 5);
    }
'''

PRE = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdarg.h>
#include "threads.c"
#include "x360audio.h"
uint8_t *G;
int diag_on, boot_trace;
uint64_t diag_idle_ns[IDLE_N];
volatile uint64_t perf_nframes;
uint64_t host_ticks(void) { return 10000000; }
uint64_t host_filetime(void) { return 0; }
uint32_t galloc(uint32_t n) { (void)n; abort(); }
uint32_t call_guest(Cpu *c, uint32_t f, int n, const uint32_t *a) {
    (void)c; (void)f; (void)n; (void)a; abort();
}
void diag_event(const char *f, ...) { (void)f; }
void diag_call_ring(const char *f, int n, const uint32_t *a, uint32_t v, uint32_t ra) {
    (void)f; (void)n; (void)a; (void)v; (void)ra;
}
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; *off=0; return NULL; }
static void die(const char *f, ...) { (void)f; abort(); }
#define UNUSED_IMPORT(f) int f(Cpu *c, const char *s) { (void)c; (void)s; abort(); }
UNUSED_IMPORT(d3dx) UNUSED_IMPORT(d3dx_effect) UNUSED_IMPORT(win32_d3d9)
UNUSED_IMPORT(win32_dsound) UNUSED_IMPORT(win32_dinput) UNUSED_IMPORT(win32_ws2)
UNUSED_IMPORT(win32_user32)
int win32_heap(Cpu *c, const char *s) { (void)c; (void)s; return 0; }
const char *server_import_names[256];
uint32_t server_import_calls[256];
const char *server_import(uint32_t slot, unsigned *i) { (void)slot; (void)i; abort(); }
static unsigned import_calls;
enum { GET=4, SET=8, READ=12, TLS_GET=16, TLS_SET=20, MUTEX_RELEASE=24 };
static const struct { uint32_t slot; const char *name; } speed_imports[] = {
    {GET,"KERNEL32.dll!GetLastError"}, {SET,"KERNEL32.dll!SetLastError"},
    {READ,"KERNEL32.dll!ReadFile"}, {TLS_GET,"KERNEL32.dll!TlsGetValue"},
    {TLS_SET,"KERNEL32.dll!TlsSetValue"}, {MUTEX_RELEASE,"KERNEL32.dll!ReleaseMutex"}
};
static const unsigned speed_import_count = sizeof speed_imports / sizeof *speed_imports;
#define HANDLE_BASE 0x1000u
static X360MusicHandle music_source;
static X360MusicHandle *handle_music(uint32_t h) { return h==0x8000 ? &music_source : NULL; }
size_t x360music_read(X360MusicHandle *h, void *dst, size_t n) {
    if (n>h->size-(size_t)h->pos) n=h->size-(size_t)h->pos;
    memcpy(dst,h->data+h->pos,n); h->pos+=(int64_t)n; return n;
}
'''

POST = r'''
static unsigned checks, failures, handovers, io_cases;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures<9) fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)
static uint32_t api(Cpu *c, unsigned slot, unsigned n, const uint32_t *a) {
    uint32_t sp=c->esp;
    // host_import snapshots four arguments even for zero-argument imports.
    for (unsigned i=0;i<8;++i) M32(sp-32+4*i)=0;
    for (unsigned i=n;i;--i) { c->esp-=4; M32(c->esp)=a[i-1]; }
    c->esp-=4; M32(c->esp)=0x007DB7C7;
    host_import(c,slot); CHECK(c->esp==sp); return c->eax;
}
static void set(Cpu *c, uint32_t e) { api(c,SET,1,&e); }
static uint32_t get(Cpu *c) { return api(c,GET,0,NULL); }
static uint32_t read_file(Cpu *c, uint32_t h, int out) {
    uint32_t a[]={h,0x30000,3,out?0x31000:0,0};
    M32(0x31000)=0xfeedface; return api(c,READ,5,a);
}
static void *other_thread(void *p) {
    unsigned mode=(unsigned)(uintptr_t)p;
    self=&kobj[2]; guest_worker=1;
    Cpu c={.esp=0x20000,.fs_base=0x22000}; gil_acquire();
    CHECK(get(&c)==0); // a new pthread must start with its own zero error
    set(&c,1234); CHECK(get(&c)==1234);
    uint32_t a[]={1,0xabcdef}; api(&c,TLS_SET,2,a);
    CHECK(get(&c)==1234); // TlsSetValue preserves the error
    CHECK(api(&c,TLS_GET,1,a)==0xabcdef); CHECK(get(&c)==0);
    if (mode) set(&c,1234);
    gil_release(); return NULL;
}
int main(void) {
    G=calloc(1,0x40000); assert(G);
    kobj[1].type=kobj[2].type=K_THREAD; kobj[1].tid=1; kobj[2].tid=2;
    self=&kobj[1]; Cpu c={.esp=0x10000,.fs_base=0x12000}; gil_acquire();
    CHECK(get(&c)==0);
    for (unsigned i=0;i<32;++i) {
        unsigned read_failure=i&1, mode=(i>>1)&1;
        uint32_t expected=read_failure?6:288;
        if (read_failure) {
            set(&c,777); errno=ENOSPC;
            CHECK(read_file(&c,0xffffffff,1)==0); CHECK(M32(0x31000)==0);
        } else {
            uint32_t bad=0xdeadbeef; CHECK(api(&c,MUTEX_RELEASE,1,&bad)==0);
        }
        pthread_t t; assert(!pthread_create(&t,NULL,other_thread,(void *)(uintptr_t)mode));
        // Queue the other pthread's real GIL ticket before entering GetLastError.
        for (;;) {
            pthread_mutex_lock(&gm); int ready=next_ticket-serving>1;
            pthread_mutex_unlock(&gm); if (ready) break; sched_yield();
        }
        held_since=0; // clock double: force only this import's normal 2 ms slice
        CHECK(get(&c)==expected); ++handovers;
        // Join outside the guest lock, as the other pthread may still be returning.
        gil_release(); assert(!pthread_join(t,NULL)); gil_acquire();
    }
    // Real OS errors, including invalid handles with a misleading ambient errno.
    for (unsigned i=0;i<3;++i) {
        set(&c,777); errno=i==0?0:i==1?ENOSPC:ENOENT;
        CHECK(read_file(&c,0xffffffff,i!=2)==0); CHECK(get(&c)==6);
        if (i!=2) CHECK(M32(0x31000)==0); ++io_cases;
    }
    int fd=open("/dev/null",O_WRONLY); assert(fd>=0); fds[0]=fd+1; file_info[0].access=0x40000000u;
    set(&c,777); CHECK(read_file(&c,HANDLE_BASE,1)==0);
    CHECK(get(&c)==5); CHECK(M32(0x31000)==0); ++io_cases; close(fd); file_info[0].access=0x80000000u;
    fd=open(".",O_RDONLY); assert(fd>=0); fds[0]=fd+1;
    set(&c,777); CHECK(read_file(&c,HANDLE_BASE,1)==0);
    CHECK(get(&c)==5); CHECK(M32(0x31000)==0); ++io_cases; close(fd);
    char path[]="lasterror-XXXXXX"; fd=mkstemp(path); assert(fd>=0); unlink(path);
    assert(write(fd,"ABC",3)==3); assert(lseek(fd,0,SEEK_SET)==0); fds[0]=fd+1;
    set(&c,777); CHECK(read_file(&c,HANDLE_BASE,1)==1);
    CHECK(M32(0x31000)==3 && !memcmp(G+0x30000,"ABC",3)); CHECK(get(&c)==777); ++io_cases;
    CHECK(read_file(&c,HANDLE_BASE,1)==1); CHECK(M32(0x31000)==0); CHECK(get(&c)==777); ++io_cases;
    file_info[0].snapshot=1; file_info[0].position=0;
    CHECK(read_file(&c,HANDLE_BASE,0)==1); CHECK(file_info[0].position==3); CHECK(get(&c)==777); ++io_cases;
    close(fd); // stale descriptor exercises pread errno mapping
    CHECK(read_file(&c,HANDLE_BASE,1)==0); CHECK(M32(0x31000)==0); CHECK(get(&c)==6); ++io_cases;
    static const uint8_t audio[]={1,2,3}; music_source.data=audio; music_source.size=3;
    set(&c,777); CHECK(read_file(&c,0x8000,1)==1);
    CHECK(M32(0x31000)==3 && !memcmp(G+0x30000,audio,3)); CHECK(get(&c)==777); ++io_cases;
    CHECK(read_file(&c,0x8000,1)==1); CHECK(M32(0x31000)==0); CHECK(get(&c)==777); ++io_cases;
    printf("lasterror: %u checks / %u failures; %u forced import handovers; %u read cases\n",checks,failures,handovers,io_cases);
    gil_release(); free(G); return failures?1:0;
}
'''


def function(source, name):
    match = re.search(rf'^[^\n]*\b{name}\([^\n]*\) \{{.*?^\}}', source, re.M | re.S)
    # Anchor at the function's line, not a preceding declaration/comment.
    assert match, name
    return match.group(0)


def fixture(boot):
    storage = re.search(r'^(?:__thread )?uint32_t last_error;', boot, re.M)
    assert storage
    file_state = boot.split('static int fds[256];', 1)[1].split('static int file_is_save(', 1)[0]
    read = boot.split('    if (!strcmp(fn, "ReadFile"))', 1)[1].split('    if (!strcmp(fn, "WriteFile"))', 1)[0]
    # Keep the exact two production error imports, without unrelated win32 APIs.
    errors = '\n'.join(line for line in boot.splitlines() if 'if (!strcmp(fn, "GetLastError"))' in line or 'if (!strcmp(fn, "SetLastError"))' in line)
    return (PRE + storage.group(0) + '\nstatic int fds[256];' + file_state +
            '\n' + function(boot, 'file_error') + '\n' + function(boot, 'file_read') +
            '\nstatic int win32(Cpu *c, const char *fn) {\n' + errors +
            '\n    if (!strcmp(fn, "ReadFile"))' + read + '    return 0;\n}\n' +
            function(boot, 'host_import') + '\n' + POST)


def main():
    boot = (ROOT / 'runtime/boot.c').read_text()
    variants = [('current', boot)]
    if '--current-only' not in sys.argv:
        shared = re.sub(r'^(?:__thread )?uint32_t last_error;', 'uint32_t last_error;', boot, flags=re.M)
        old_read = re.sub(r'    if \(!strcmp\(fn, "ReadFile"\)\).*?(?=    if \(!strcmp\(fn, "WriteFile"\)\))', lambda _: OLD_READ, boot, count=1, flags=re.S)
        old = re.sub(r'^(?:__thread )?uint32_t last_error;', 'uint32_t last_error;', old_read, flags=re.M)
        noaccess = re.sub(r'^[^\n]*write-only handle: ERROR_ACCESS_DENIED\n', '', boot, count=1, flags=re.M)
        assert noaccess != boot
        variants += [('pinned-original', old), ('shared-storage', shared), ('unmapped-read', old_read), ('write-only-as-6', noaccess)]
    with tempfile.TemporaryDirectory(prefix='lasterror-') as td:
        tmp = Path(td)
        for label, source in variants:
            # A pinned storage control also needs the matching declaration.
            header = (ROOT / 'runtime/host.h').read_text()
            if label in ('pinned-original', 'shared-storage'):
                header = header.replace('extern __thread uint32_t last_error;', 'extern uint32_t last_error;')
            (tmp / 'host.h').write_text(header)
            threads = (ROOT / 'runtime/threads.c').read_text()
            (tmp / 'threads.c').write_text(threads)
            (tmp / 'fixture.c').write_text(fixture(source))
            exe = tmp / label
            p = subprocess.run(['clang', '-std=c11', '-O2', '-g', '-w', '-pthread',
                                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                '-I'+str(tmp), '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                                '-I'+str(ROOT/'recomp/out'), str(tmp/'fixture.c'), '-o', str(exe)],
                               capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-4500:]
            env = {k: v for k, v in os.environ.items() if k not in ('BOOT_TEST_GILYIELD', 'BOOT_WATCH', 'BOOT_DIAG_WATCH')}
            env['ASAN_OPTIONS'] = 'detect_leaks=0'
            p = subprocess.run([str(exe)], cwd=tmp, env=env, capture_output=True, text=True, timeout=45)
            print(label+': '+p.stdout.strip(), flush=True)
            expected = 0 if label == 'current' else 1
            assert p.returncode == expected, (p.returncode, p.stderr[-2500:])
            assert 'Sanitizer' not in p.stderr, p.stderr[-2500:]


if __name__ == '__main__':
    main()
