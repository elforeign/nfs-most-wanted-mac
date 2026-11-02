#!/usr/bin/env python3
"""










"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD_CLOSE = r'''int threads_close(uint32_t h) {
    KObj *o = kget(h);
    if (!o) return 0;
    if (o->type != K_THREAD || o->signaled) o->type = K_FREE;
    return 1;
}'''
# Reviewed predecessor fragments: duplicates shared the source token; drain and
# the SNDREAL handle reap reclaimed the object whatever other handles remained.
PREDECESSORS = {
    'independent-close': [('    uint32_t dst = o ? khandle(o) : src;\n',
                           '    uint32_t dst = src; if (o) ++o->handle_refs;\n'),
                          ('    handle_obj[(h - KOBJ_BASE) / 4] = NULL;\n',
                           '    if (o->handle_refs == 1) handle_obj[(h - KOBJ_BASE) / 4] = NULL;\n')],
    'drain-duplicate': [('if (handle_obj[i] == t) threads_close(KOBJ_BASE + 4 * i);', 'thread_reclaim(t);')],
    'reap-duplicate': [('    threads_close(handle);\n', '    free(old->cpu);\n    memset(old, 0, sizeof *old);\n')],
}

PRE = r'''
#include <assert.h>
#include <stdlib.h>
#include <pthread.h>
static unsigned cpu_allocs, cpu_frees;
static void *tracked_calloc(size_t n, size_t s) {
    void *p = calloc(n, s); assert(p); ++cpu_allocs; return p;
}
static void tracked_free(void *p) { if (p) ++cpu_frees; free(p); }
#define calloc tracked_calloc
#define free tracked_free
#define pthread_attr_setdetachstate(a, s) pthread_attr_setdetachstate(a, PTHREAD_CREATE_JOINABLE)
'''

POST = r'''
#undef calloc
#undef free
#undef pthread_attr_setdetachstate
uint8_t *G;
int diag_on, boot_trace;
__thread uint32_t last_error;   // thread-local like host.h
uint64_t diag_idle_ns[IDLE_N];
volatile uint64_t perf_nframes;
static unsigned completed, calls, checks;
static uint32_t current_h, mode, entered, finish;
static const char *stage = "cycles";
#define CHECK(x) do { ++checks; if (!(x)) { printf("FAIL %s: %s\n", stage, #x); fflush(stdout); abort(); } } while (0)
uint64_t host_ticks(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
uint64_t host_filetime(void) { return 0; }
void diag_event(const char *fmt, ...) { (void)fmt; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; if(off)*off=0; return ""; }
uint32_t galloc(uint32_t n) {
    static uint32_t next = 0x100000;
    uint32_t p = next; next += n; assert(next < 0x800000); return p;
}
static uint32_t api(Cpu *c, const char *fn, unsigned n, const uint32_t *args) {
    uint32_t esp = c->esp;
    for (unsigned i=0; i<n; ++i) M32(esp+4+4*i)=args[i];
    CHECK(win32_threads(c, fn)); uint32_t result=c->eax; c->esp=esp; return result;
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    CHECK((fn==0x84b700 || fn==0x820bd7) && n==1 && args[0]==0x12345678);
    CHECK(self && self->cpu==c && M32(c->fs_base+0x24)==self->tid);
    ++calls; entered=1; changed();
    if (mode==1) while (!finish) { wait_change(0); thread_checkpoint(c); }
    if (mode==2) api(c,"ExitThread",1,(uint32_t[]){73});
    if (mode==3) CHECK(threads_close(current_h));
    if (mode==4) {
        CHECK(api(c,"DuplicateHandle",7,(uint32_t[]){0xffffffff,0xfffffffe,0xffffffff,0x90004,0,0,2}));
        uint32_t dup=M32(0x90004);
        CHECK(kget(dup)==self);
        CHECK(api(c,"GetExitCodeThread",2,(uint32_t[]){dup,0x90000}) && M32(0x90000)==259);
        CHECK(threads_close(dup));
    }
    return 42;
}
static unsigned objects(void) {
    unsigned n=0; for(unsigned i=1; i<MAX_KOBJ; ++i) n+=kobj[i].type!=K_FREE; return n;
}
static void report(void) {
    printf("cycles=%u/1024 objects=%u CPUs=%u calls=%u checks=%u\n",
           completed,objects(),cpu_allocs-cpu_frees,calls,checks);
}
static pthread_t create(Cpu *c, uint32_t flags) {
    current_h=api(c,"CreateThread",6,(uint32_t[]){0,0,0x84b700,0x12345678,flags,0});
    CHECK(current_h!=0); return kget(current_h)->th;
}
static uint32_t dup_of(Cpu *c, uint32_t h) {
    CHECK(api(c,"DuplicateHandle",7,(uint32_t[]){0xffffffff,h,0xffffffff,0x90004,0,0,2}));
    return M32(0x90004);
}
static void owns_finished(Cpu *c, uint32_t d) {
    M32(0x90000)=0;
    CHECK(api(c,"GetExitCodeThread",2,(uint32_t[]){d,0x90000})==1 && M32(0x90000)==42);
    CHECK(api(c,"WaitForSingleObject",2,(uint32_t[]){d,0})==0);
}
static void join_worker(pthread_t th) {
    gil_release(); int rc=pthread_join(th,NULL); gil_acquire(); CHECK(!rc);
}
int main(void) {
    atexit(report); G=calloc(1,16u<<20); CHECK(G);
    Cpu c={0}; c.esp=0x80000; gil_acquire();
    // Main holds the GIL across CreateThread -> CloseHandle, so every thread
    // must finish after its handle was closed, as in the retail fire-and-forget pattern.
    for (unsigned i=0; i<1024; ++i) {
        pthread_t th=create(&c,0); CHECK(threads_close(current_h));
        join_worker(th); ++completed;
    }
    CHECK(objects()==0 && cpu_allocs==cpu_frees && calls==1024 && nfree_stacks==1);
    // Close after completion: retain signaled state, waitability and exit code.
    pthread_t th=create(&c,0); uint32_t h=current_h;
    join_worker(th); CHECK(objects()==1 && cpu_allocs-cpu_frees==1);
    CHECK(api(&c,"GetExitCodeThread",2,(uint32_t[]){h,0x90000})==1 && M32(0x90000)==42);
    CHECK(api(&c,"WaitForSingleObject",2,(uint32_t[]){h,0})==0);
    CHECK(threads_close(h)); CHECK(!threads_close(h) && !kget(h));
    CHECK(objects()==0 && cpu_allocs==cpu_frees);
    // An already-running thread stays alive after close; its handle becomes invalid.
    mode=1; entered=finish=0; th=create(&c,0); h=current_h;
    while (!entered) wait_change(0);
    CHECK(api(&c,"GetExitCodeThread",2,(uint32_t[]){h,0x90000})==1 && M32(0x90000)==259);
    pthread_t ths[4]; Cpu *cpus[4]; uint32_t tids[4];
    CHECK(threads_list(ths,cpus,tids,4)==2); Cpu *held=cpus[1]; uint32_t saved_esp=held->esp;
    CHECK(threads_close(h)); CHECK(!kget(h) && !threads_close(h));
    CHECK(api(&c,"GetExitCodeThread",2,(uint32_t[]){h,0x90000})==0);
    CHECK(api(&c,"WaitForSingleObject",2,(uint32_t[]){h,0})==0xffffffff);
    CHECK(objects()==1 && cpu_allocs-cpu_frees==1);
    finish=1; changed(); join_worker(th);
    CHECK(objects()==0 && cpu_allocs==cpu_frees && held->esp==saved_esp);
    // ExitThread and self-close use the same reclamation path as a normal return.
    mode=2; th=create(&c,0); CHECK(threads_close(current_h)); join_worker(th);
    CHECK(objects()==0 && cpu_allocs==cpu_frees);
    mode=3; th=create(&c,0); join_worker(th); CHECK(objects()==0 && cpu_allocs==cpu_frees);
    // A thread can duplicate its pseudo-handle after the creator closed it.
    mode=4; th=create(&c,0); CHECK(threads_close(current_h)); join_worker(th);
    CHECK(objects()==0 && cpu_allocs==cpu_frees);
    // A concrete duplicate retains the completed thread until its final close.
    mode=0; th=create(&c,0); h=current_h;
    CHECK(api(&c,"DuplicateHandle",7,(uint32_t[]){0xffffffff,h,0xffffffff,0x90004,0,0,2}));
    uint32_t dup=M32(0x90004); CHECK(threads_close(h)); join_worker(th);
    CHECK(objects()==1 && cpu_allocs-cpu_frees==1);
    CHECK(api(&c,"GetExitCodeThread",2,(uint32_t[]){dup,0x90000}) && M32(0x90000)==42);
    CHECK(threads_close(dup)); CHECK(objects()==0 && cpu_allocs==cpu_frees);
    // CLOSE_SOURCE transfers the reference rather than creating a leak.
    th=create(&c,0); h=current_h;
    CHECK(api(&c,"DuplicateHandle",7,(uint32_t[]){0xffffffff,h,0xffffffff,0x90004,0,0,3}));
    dup=M32(0x90004); CHECK(threads_close(dup)); join_worker(th);
    CHECK(objects()==0 && cpu_allocs==cpu_frees);
    // Each handle closes once and independently: a second close of h fails and
    // cannot release the duplicate's reference.
    stage="independent-close"; th=create(&c,0); h=current_h; dup=dup_of(&c,h);
    CHECK(threads_close(h)); CHECK(!threads_close(h));
    CHECK(dup!=h && !kget(h) && kget(dup)); join_worker(th);
    owns_finished(&c,dup); CHECK(objects()==1);
    CHECK(threads_close(dup)); CHECK(!threads_close(dup) && objects()==0 && cpu_allocs==cpu_frees);
    // Module drain and the SNDREAL handle reap release only the abandoned handle;
    // a duplicate keeps wait and exit-code access until its own close.
    stage="drain-duplicate"; th=create(&c,0); h=current_h; dup=dup_of(&c,h); join_worker(th);
    CHECK(threads_drain_module(0x84b700,0x84b701));
    owns_finished(&c,dup); CHECK(!kget(h)); CHECK(threads_close(dup) && objects()==0 && cpu_allocs==cpu_frees);
    stage="reap-duplicate";
    h=api(&c,"CreateThread",6,(uint32_t[]){0,0,0x820bd7,0x12345678,0,0}); th=kget(h)->th;
    M32(0x9c22e8)=h; dup=dup_of(&c,h); join_worker(th);
    split_sound_worker_reap(0x820bd7);
    owns_finished(&c,dup); CHECK(!kget(h)); CHECK(threads_close(dup) && objects()==0 && cpu_allocs==cpu_frees);
    stage="lifecycle";
    // Closed suspended threads remain reachable by module drain/termination.
    mode=0; unsigned before=calls; th=create(&c,4); h=current_h;
    CHECK(threads_close(h)); CHECK(threads_drain_module(0x84b700,0x84b701));
    join_worker(th); CHECK(calls==before && objects()==0 && cpu_allocs==cpu_frees);
    // An open terminated handle retains its requested exit code until close.
    th=create(&c,4); h=current_h;
    CHECK(api(&c,"TerminateThread",2,(uint32_t[]){h,91})==1); join_worker(th);
    CHECK(api(&c,"GetExitCodeThread",2,(uint32_t[]){h,0x90000})==1 && M32(0x90000)==91);
    CHECK(threads_close(h)); CHECK(objects()==0 && cpu_allocs==cpu_frees && calls==before);
    // Non-thread objects still close and recycle normally.
    h=threads_event_new(1,1); CHECK(threads_close(h)); CHECK(!threads_close(h));
    CHECK(threads_event_new(0,0)==h); CHECK(threads_close(h));
    gil_release(); free(G); return 0;
}
'''


def run(source, label, tmp):
    assert source.count('#define MAIN_TEB 0x7FFDE000u') == 1
    source = source.replace('#define MAIN_TEB 0x7FFDE000u', '#define MAIN_TEB 0x800000u')
    (tmp/'threads.inc').write_text(source)
    (tmp/'fixture.c').write_text(PRE+'\n#include "threads.inc"\n'+POST)
    exe = tmp/label
    build = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-w', '-pthread',
                            '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                            '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
                            '-I'+str(ROOT/'recomp/out'), str(tmp/'fixture.c'), '-o', str(exe)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-3000:]
    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=90,
                            env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
    print(f'{label}: exit {result.returncode}; {result.stdout.strip()}', flush=True)
    return result


def main():
    source = (ROOT/'runtime/threads.c').read_text()
    with tempfile.TemporaryDirectory(prefix='threadhandles-') as td:
        tmp = Path(td)
        if '--premise' in sys.argv:
            result = run(source, 'original', tmp)
            assert result.returncode == 1 and 'cycles=511/1024 objects=511 CPUs=511' in result.stdout, result.stderr[-3000:]
            return
        old, count = re.subn(r'^int threads_close\(uint32_t h\) \{\n.*?^\}',
                             lambda _: OLD_CLOSE, source, flags=re.M | re.S)
        assert count == 1
        retirement = '    if (!t->handle_refs) thread_reclaim(t);'
        assert old.count(retirement) == 1
        old = old.replace(retirement, '')
        result = run(old, 'pinned-original', tmp)
        assert result.returncode == 1 and 'cycles=511/1024 objects=511 CPUs=511' in result.stdout, result.stderr[-3000:]
        for stage, edits in PREDECESSORS.items():
            broken_source = source
            for fixed, broken in edits:
                assert broken_source.count(fixed) == 1, stage
                broken_source = broken_source.replace(fixed, broken)
            result = run(broken_source, 'predecessor-' + stage, tmp)
            assert result.returncode != 0 and f'FAIL {stage}:' in result.stdout, result.stderr[-3000:]
        result = run(source, 'fixed', tmp)
        assert result.returncode == 0 and 'cycles=1024/1024 objects=0 CPUs=0' in result.stdout, result.stderr[-3000:]
        print('THREAD HANDLES PASS: predecessor exhausted 511 slots; 1024 fixed cycles and lifecycle checks clean')


if __name__ == '__main__':
    main()
