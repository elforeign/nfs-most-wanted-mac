#!/usr/bin/env python3
"""Real pthreads exercising production threads.c at -O2 with ThreadSanitizer.
Pinned pre-fix functions: 3/3 workloads exit 66 (3/4/2 race reports observed).
Fixed: 3/3 exit 0, zero warnings; 6,000 imports, 8,000 async posts, 6,000
recursive CS acquisitions and 2,000 semaphore transfers, plus timer/wait checks.
Diagnostic watchdog snapshots are outside this fixture's synchronization proof.
Each workload also forces one conflicting pair (relaxed-atomic handshakes,
no happens-before), so rejection no longer rests on scheduler overlap. Under
18-36 busy loops, 4-18 runs at once: old fixture 0/320 misses per control (failures
seen only in loaded suites); stress loops removed, old fixture rejected 1/3
controls and the forced steps alone 3/3, 60/60 runs each; full fixture
40/40 rejected per control, fixed 40/40 clean.
Late async post + contended CS regression: pinned predecessor 1/1 workload
times out at 10 s; fixed 1/1 completes 20 forced handovers with zero TSan warnings.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Original import check and wait loop, pinned rather than read from a moving ref.
OLD_MAYBE = r'''void gil_maybe_yield(Cpu *c) {
    if (npending) apply_pending_events();
    static int every = -1;
    if (every < 0) every = getenv("BOOT_TEST_GILYIELD") != NULL;
    if (next_ticket - serving > 1 && (every || host_ticks() - held_since > 2000000)) gil_yield(c);
}'''
OLD_WAIT = r'''static void wait_until(int (*pred)(void *arg, uint64_t *wake), void *arg, uint64_t deadline) {
    pthread_mutex_lock(&gm);
    gil_release_locked();
    for (;;) {
        if (npending || (self && (self->terminate || self->suspend > 0))) break;
        uint64_t wake = deadline;
        if (pred(arg, &wake)) break;
        uint64_t now = host_ticks();
        if (wake && now >= wake) break;
        if (wake) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            uint64_t ns = (uint64_t)ts.tv_nsec + (wake - now);
            ts.tv_sec += (time_t)(ns / 1000000000ull);
            ts.tv_nsec = (long)(ns % 1000000000ull);
            pthread_cond_timedwait(&obj_cv, &gm, &ts);
        } else {
            pthread_cond_wait(&obj_cv, &gm);
        }
    }
    gil_acquire_locked();
    pthread_mutex_unlock(&gm);
}'''
OLD_CS_ENTER = r'''static void cs_enter(Cpu *c, uint32_t cs) {
    uint32_t me = cur_tid();
    while (M32(cs + 12) && M32(cs + 12) != me) {
        wait_mark("EnterCriticalSection", cs, M32(cs + 12));
        wait_until(cs_free, (void *)(uintptr_t)cs, 0);
        wait_clear();
        thread_checkpoint(c);
    }
    M32(cs + 12) = me;
    M32(cs + 8) += 1;
}'''

FIXTURE = r'''
#include <assert.h>
#include <sched.h>
#include "threads.inc"
uint8_t *G;
int diag_on, boot_trace;
__thread uint32_t last_error;
uint64_t diag_idle_ns[IDLE_N];
volatile uint64_t perf_nframes;
uint64_t host_ticks(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
uint64_t host_filetime(void) { return 0; }
void diag_event(const char *f, ...) { (void)f; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; *off=0; return ""; }
uint32_t galloc(uint32_t n) { (void)n; abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    (void)c; (void)fn; (void)n; (void)a; abort();
}
enum { LOOPS=2000, CS=128, VALUE=256 };
static uint32_t event_h, sem_h;
static unsigned imports, consumed;
static pthread_cond_t async_cv = PTHREAD_COND_INITIALIZER;
static int async_ready;
static void attach(unsigned id) { self=&kobj[32+id]; guest_worker=1; }
static void *import_worker(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0};
    for (unsigned i=0; i<LOOPS; ++i) {
        gil_acquire(); ++imports;
        gil_maybe_yield(&c); gil_yield(&c); gil_release();
    }
    return NULL;
}
static void *event_producer(void *p) {
    (void)p;
    for (unsigned i=0; i<LOOPS*4; ++i) {
        threads_set_event_async(event_h); sched_yield();
    }
    return NULL;
}
static void *one_event(void *p) {
    (void)p; threads_set_event_async(event_h); return NULL;
}
// Forced interleavings: the stress loops above are rejected only when the scheduler
// overlaps them and TSan's shadow keeps the earlier access, so each control
// also orders one conflicting pair outright.
// The handshake flags are relaxed atomics, which TSan treats as carrying no
// happens-before edge; the only synchronization left is the production code's.
static void join(pthread_t t) { assert(!pthread_join(t,NULL)); }
static int forced_posted, forced_go, forced_seen;
static void spin_until(int *flag) { while (!__atomic_load_n(flag,__ATOMIC_RELAXED)) sched_yield(); }
static void *post_then_flag(void *p) {
    (void)p; threads_set_event_async(event_h);
    __atomic_store_n(&forced_posted,1,__ATOMIC_RELAXED); return NULL;
}
static void forced_post(pthread_t *t) {   // a post completed that this thread has not synchronized with
    forced_posted=0; assert(!pthread_create(t,NULL,post_then_flag,NULL)); spin_until(&forced_posted);
}
static uint64_t gil_queue(void) {
    pthread_mutex_lock(&gm); uint64_t q=next_ticket-serving; pthread_mutex_unlock(&gm); return q;
}
static uint32_t forced_h;
static void *forced_waiter(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0}; gil_acquire();
    assert(wait_handles(&c,1,&forced_h,0,0xffffffff)==0); gil_release(); return NULL;
}
static void *gil_queue_watch(void *p) {
    // Reports, without passing on happens-before, that the waiter left its
    // object wait and queued for the GIL after the holder's signal.
    (void)p; spin_until(&forced_go);
    while (gil_queue() < 2) sched_yield();
    __atomic_store_n(&forced_seen,1,__ATOMIC_RELAXED); return NULL;
}
// With the GIL held: a waiter checks an object's state, then the holder writes it.
// The holder's racing store is its first access to that shadow cell after the
// waiter's check; a read first (cs_enter's owner test) can evict the waiter's
// shadow entry, which lost 13 of 60 loaded runs when this step used a section.
static void forced_wait(Cpu *c) {
    pthread_t w, h; forced_go=forced_seen=0; forced_h=threads_event_new(1,0);
    assert(!pthread_create(&w,NULL,forced_waiter,(void *)1));
    assert(!pthread_create(&h,NULL,gil_queue_watch,NULL));
    while (gil_queue() < 2) sched_yield();
    gil_yield(c);                   // back once the waiter released the GIL inside its wait
    threads_event_set(forced_h,1);  // broadcast: the waiter re-checks and queues for the GIL
    __atomic_store_n(&forced_go,1,__ATOMIC_RELAXED); spin_until(&forced_seen);
    threads_event_set(forced_h,1);  // the holder's write after that re-check
    gil_release(); join(w); join(h); gil_acquire();
}
static int never_ready(void *p, uint64_t *wake) { (void)p; (void)wake; return 0; }
static void *stop_waiter(void *p) {
    attach((unsigned)(uintptr_t)p); gil_acquire();
    kobj[32].suspend=1; changed(); gil_release(); return NULL;
}
static void *cs_worker(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0}; gil_acquire();
    for (unsigned i=0; i<LOOPS; ++i) {
        cs_enter(&c, CS);
        assert(M32(CS+12)==cur_tid() && M32(CS+8)==1);
        // Exercise recursive ownership and contention across GIL handovers.
        cs_enter(&c, CS); assert(M32(CS+8)==2); cs_leave(CS);
        ++M32(VALUE); gil_yield(&c); cs_leave(CS); gil_yield(&c);
    }
    gil_release(); return NULL;
}
static void *cs_async_waiter(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0}; gil_acquire();
    // Finish the import drain before allowing the host to post a notification.
    gil_maybe_yield(&c);
    pthread_mutex_lock(&gm);
    async_ready=1; pthread_cond_signal(&async_cv);
    // Force both the late post and the blocked owner's GIL ticket to exist
    // before entering the production critical-section retry loop.
    while (!npending || next_ticket-serving < 2) {
        pthread_mutex_unlock(&gm); sched_yield(); pthread_mutex_lock(&gm);
    }
    pthread_mutex_unlock(&gm);
    assert(!kget(event_h)->signaled && M32(CS+12)==kobj[32].tid);
    puts("critical_async: late post queued; owner waiting for GIL"); fflush(stdout);
    cs_enter(&c,CS);
    assert(kget(event_h)->signaled && M32(CS+12)==cur_tid());
    ++M32(VALUE); cs_leave(CS); gil_release(); return NULL;
}
static void *sem_producer(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0}; gil_acquire();
    for (unsigned i=0; i<LOOPS; ++i) {
        ++kget(sem_h)->count; changed(); gil_yield(&c);
    }
    gil_release(); return NULL;
}
static void *sem_consumer(void *p) {
    attach((unsigned)(uintptr_t)p); Cpu c={0}; gil_acquire();
    for (unsigned i=0; i<LOOPS; ++i) {
        assert(wait_handles(&c,1,&sem_h,0,0xffffffff)==0); ++consumed;
    }
    gil_release(); return NULL;
}
int main(int argc, char **argv) {
    assert(argc==2); G=calloc(1,4096); assert(G);
    setenv("BOOT_TEST_GILYIELD","1",1);
    for (unsigned i=0; i<4; ++i) { kobj[32+i].type=K_THREAD; kobj[32+i].tid=i+1; }
    attach(0); Cpu c={0}; pthread_t t[4]; gil_acquire();
    event_h=threads_event_new(1,0);
    if (!strcmp(argv[1],"imports")) {
        forced_post(&t[0]); gil_maybe_yield(&c); join(t[0]);   // the import check reads the queue
        for (unsigned i=0; i<3; ++i) assert(!pthread_create(&t[i],NULL,import_worker,(void *)(uintptr_t)(i+1)));
        assert(!pthread_create(&t[3],NULL,event_producer,NULL));
        gil_release(); for (unsigned i=0; i<4; ++i) join(t[i]); gil_acquire();
        apply_pending_events(); assert(imports==LOOPS*3 && !npending && kget(event_h)->signaled);
        printf("imports: %u imports, %u asynchronous posts\n",imports,LOOPS*4);
    } else if (!strcmp(argv[1],"critical")) {
        forced_wait(&c);                            // the wait re-checks objects
        for (unsigned i=0; i<3; ++i) assert(!pthread_create(&t[i],NULL,cs_worker,(void *)(uintptr_t)(i+1)));
        gil_release(); for (unsigned i=0; i<3; ++i) join(t[i]); gil_acquire();
        assert(M32(VALUE)==LOOPS*3 && !M32(CS+12) && !M32(CS+8));
        printf("critical: %u recursive acquisitions / serialized updates\n",M32(VALUE));
    } else if (!strcmp(argv[1],"critical_async")) {
        for (unsigned i=0; i<20; ++i) {
            async_ready=0; threads_event_set(event_h,0); cs_enter(&c,CS);
            assert(!pthread_create(&t[0],NULL,cs_async_waiter,(void *)1));
            gil_release();
            pthread_mutex_lock(&gm);
            while (!async_ready) pthread_cond_wait(&async_cv,&gm);
            pthread_mutex_unlock(&gm);
            threads_set_event_async(event_h);
            gil_acquire();
            assert(kget(event_h)->signaled && M32(CS+12)==cur_tid());
            cs_leave(CS); gil_release(); join(t[0]); gil_acquire();
            assert(M32(VALUE)==i+1 && !M32(CS+12) && !M32(CS+8));
        }
        printf("critical_async: %u late-post/contention handovers completed\n",M32(VALUE));
    } else {
        assert(!strcmp(argv[1],"objects")); KObj *s; sem_h=knew(K_SEM,&s); s->max=LOOPS;
        assert(!pthread_create(&t[0],NULL,sem_consumer,(void *)1));
        assert(!pthread_create(&t[1],NULL,sem_producer,(void *)2));
        gil_release(); join(t[0]); join(t[1]); gil_acquire();
        assert(consumed==LOOPS && !s->count);
        // Timer wakeups and timeout must still work without any notification.
        KObj *timer; uint32_t th=knew(K_TIMER,&timer);
        timer->due=host_ticks()+2000000;
        assert(wait_handles(&c,1,&th,0,0xffffffff)==0);
        assert(wait_handles(&c,1,&event_h,0,2)==0x102);
        // All/any waits, auto-reset consumption and generation-filtered host posts.
        uint32_t auto_h=threads_event_new(0,1), hs[]={auto_h,event_h};
        assert(wait_handles(&c,2,hs,1,0)==0x102 && kget(auto_h)->signaled);
        uint64_t gen=threads_event_generation(event_h);
        threads_set_event_generation_async(event_h,gen+1);
        assert(wait_handles(&c,1,&event_h,0,0)==0x102);
        threads_set_event_generation_async(event_h,gen);
        assert(wait_handles(&c,2,hs,1,0)==0 && !kget(auto_h)->signaled);
        assert(wait_handles(&c,2,hs,0,0)==1);
        threads_event_set(event_h,0);
        forced_post(&t[0]);                         // the wait's drain reads the queue
        assert(wait_handles(&c,1,&event_h,0,0xffffffff)==0); join(t[0]);
        threads_event_set(event_h,0);
        assert(!pthread_create(&t[0],NULL,one_event,NULL));
        assert(wait_handles(&c,1,&event_h,0,0xffffffff)==0);
        gil_release(); join(t[0]); gil_acquire();
        assert(!pthread_create(&t[0],NULL,stop_waiter,(void *)1));
        wait_until(never_ready,NULL,0); assert(self->suspend==1); self->suspend=0;
        gil_release(); join(t[0]); gil_acquire();
        printf("objects: %u semaphore transfers; timer, timeout, all/any, generation, async wake, suspend checks\n",consumed);
    }
    gil_release(); free(G); return 0;
}
'''


def replace_function(src, name, replacement):
    pattern = rf'^(?:static )?void {name}\([^\n]*\) \{{\n.*?^\}}'
    result, count = re.subn(pattern, lambda _: replacement, src, flags=re.M | re.S)
    assert count == 1, (name, count)
    return result


def main():
    source = (ROOT / 'runtime/threads.c').read_text()
    baseline = replace_function(source, 'gil_maybe_yield', OLD_MAYBE)
    baseline = replace_function(baseline, 'wait_until', OLD_WAIT)
    baseline = baseline.replace('        apply_pending_events();\n',
                                '        if (npending) apply_pending_events();\n')
    predecessor = replace_function(source, 'cs_enter', OLD_CS_ENTER)
    env = {**os.environ, 'TSAN_OPTIONS': 'halt_on_error=0:abort_on_error=0:exitcode=66:symbolize=0'}
    with tempfile.TemporaryDirectory(prefix='threads-race-') as tmp:
        tmp = Path(tmp)
        for label, src, modes in [
                ('baseline', baseline, ('imports', 'critical', 'objects')),
                ('predecessor', predecessor, ('critical_async',)),
                ('fixed', source, ('imports', 'critical', 'objects', 'critical_async'))]:
            (tmp / 'threads.inc').write_text(src)
            (tmp / 'fixture.c').write_text(FIXTURE)
            exe = tmp / label
            build = subprocess.run(['clang', '-std=c11', '-O2', '-g', '-w', '-pthread',
                                    '-fsanitize=thread', '-I'+str(ROOT/'runtime'),
                                    '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'recomp/out'),
                                    str(tmp/'fixture.c'), '-o', str(exe)], capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-4000:]
            for mode in modes:
                try:
                    run = subprocess.run([str(exe), mode], env=env, capture_output=True,
                                         text=True, timeout=10 if mode == 'critical_async' else 90)
                except subprocess.TimeoutExpired as exc:
                    assert label == 'predecessor', (label, mode, exc)
                    assert b'late post queued; owner waiting for GIL' in (exc.stdout or b'')
                    assert b'ThreadSanitizer' not in (exc.stderr or b''), exc.stderr
                    print(f'{label}/{mode}: expected timeout at 10 s after late post / owner ticket')
                    continue
                warnings = run.stderr.count('WARNING: ThreadSanitizer: data race')
                print(f'{label}/{mode}: exit {run.returncode}, {warnings} TSan data races; {run.stdout.strip()}')
                if label == 'baseline':
                    assert run.returncode == 66 and warnings > 0, run.stderr[-4000:]
                else:
                    assert label != 'predecessor', 'pinned predecessor unexpectedly made progress'
                    assert run.returncode == 0 and 'ThreadSanitizer' not in run.stderr, run.stderr[-4000:]
        print('THREADS RACE PASS: 3/3 pre-fix workloads rejected; 3/3 fixed workloads clean at -O2')
        print('THREADS ASYNC CS PASS: predecessor stalled at 10 s; fixed completed 20/20 handovers at -O2')


if __name__ == '__main__':
    main()
