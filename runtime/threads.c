// threads.c - guest threads and Win32 synchronization objects.
//
// Each guest thread is a real pthread with its own Cpu, guest stack and TEB, but only one runs guest code at a
// time: the global guest lock (GIL, a fair ticket lock) is held while translated code runs and handed over at
// blocking calls (waits, Sleep, contended critical sections) and, time-sliced, at import calls. Translated code
// has no atomics (speed.exe has no lock-prefixed instructions), so this also sidesteps x86 vs. ARM memory
// ordering. All host-side state (heap, handles, message queue) is touched only with the GIL held.
#include <pthread.h>
#include <setjmp.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include "host.h"

// ---------------- GIL ----------------
static uint32_t cur_tid_diag(void);
static pthread_mutex_t gm = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gil_cv = PTHREAD_COND_INITIALIZER;   // GIL hand-over
static pthread_cond_t obj_cv = PTHREAD_COND_INITIALIZER;   // some object changed state
static uint64_t next_ticket, serving, change_seq;
static uint64_t held_since;
static __thread int guest_worker;  // a CreateThread thread (0 on the game's main thread): idle time is counted for main only

// BOOT_DIAG: who holds the GIL while the game thread waits for it. Each guest thread has a GilRec (its current
// import, threads.c/boot.c); gil_holder points at the holder's, and the game thread's open wait is published in
// gil_main_wait_*. diag.c's gilwatch thread samples the holder while the game thread waits (gilwait.log).
static __thread GilRec my_rec;
GilRec *volatile gil_holder;
volatile uint64_t gil_main_wait_since;         // host_ticks when the game thread started waiting (0: not waiting)
volatile void *gil_main_wait_site;             // its caller (pacing, present, gil_yield, ...)
static void gil_acquire_locked_at(void *site) {
    uint64_t t = next_ticket++;
    int watch = diag_on && !guest_worker && t != serving;
    if (watch) { gil_main_wait_site = site; gil_main_wait_since = host_ticks(); }
    while (t != serving) pthread_cond_wait(&gil_cv, &gm);
    held_since = host_ticks();
    if (diag_on) {
        my_rec.tid = cur_tid_diag();
        my_rec.th = pthread_self();
        my_rec.since = held_since;
        gil_holder = &my_rec;
        if (watch) gil_main_wait_since = 0;
    }
}
#define gil_acquire_locked() gil_acquire_locked_at(__builtin_return_address(0))
// The game thread's alertable SleepEx (THREAD_yield, bThreadYield 0064CF00: the 8 ms polls of the game's loading waits,
// e.g. TrackStreamer::WaitForCurrentLoadingToComplete 00730390, MiniMainLoop 00663C90) also ends once a background thread
// that read a file lets go of the GIL, i.e. finished that request (as Windows ends an alertable wait on an I/O
// completion; one that finished while the game thread was busy ends the next sleep at once, as a queued completion
// does) or after 1 ms, instead of always taking 8-10 ms per poll: each poll services one loading step, so a warp's or a HUD
// load's wait for 30 sections took 30 polls x 10 ms. NFS_SLEEP_IOWAKE=0: the fixed sleep (A/B).
static pthread_cond_t io_cv = PTHREAD_COND_INITIALIZER;
static uint64_t io_seq;
static __thread int io_note;       // this thread read a file since it last let go of the GIL
void threads_note_io(void) { if (guest_worker) io_note = 1; }
static int iowake_on(void) {
    static int on = -1;
    if (on < 0) on = !(getenv("NFS_SLEEP_IOWAKE") && !atoi(getenv("NFS_SLEEP_IOWAKE")));
    return on;
}
static void gil_release_locked(void) {
    ++serving;
    pthread_cond_broadcast(&gil_cv);
    if (io_note) { io_note = 0; ++io_seq; pthread_cond_broadcast(&io_cv); }
}
void gil_acquire(void) { pthread_mutex_lock(&gm); gil_acquire_locked_at(__builtin_return_address(0)); pthread_mutex_unlock(&gm); }
void gil_release(void) { pthread_mutex_lock(&gm); gil_release_locked(); pthread_mutex_unlock(&gm); }
static void changed(void) {   // with the GIL held: wake waiters to re-check their objects
    pthread_mutex_lock(&gm);
    ++change_seq;
    pthread_cond_broadcast(&obj_cv);
    pthread_mutex_unlock(&gm);
}
static void thread_checkpoint(Cpu *c);
// Lets other guest threads run if any are waiting for the GIL.
void gil_yield(Cpu *c) {
    pthread_mutex_lock(&gm);
    if (next_ticket - serving > 1) {
        uint64_t t0 = host_ticks();
        gil_release_locked(); gil_acquire_locked();
        if (!guest_worker) diag_idle_ns[IDLE_GIL] += host_ticks() - t0;
    }
    pthread_mutex_unlock(&gm);
    thread_checkpoint(c);
}
// Events signalled from host threads that don't hold the GIL (audio notifications): applied at the next import.
static uint32_t pending_init[64], *pending_events = pending_init;   // grows past 64 (a burst of notifications must not be dropped)
static int npending, pending_cap = 64;
void threads_set_event_async(uint32_t h) {
    pthread_mutex_lock(&gm);
    if (npending == pending_cap) {
        uint32_t *g = malloc((size_t)pending_cap * 2 * sizeof *g);
        if (g) {
            memcpy(g, pending_events, (size_t)npending * sizeof *g);
            if (pending_events != pending_init) free(pending_events);
            pending_events = g; pending_cap *= 2;
        }
    }
    if (npending < pending_cap) pending_events[npending++] = h;
    ++change_seq;                                   // wake waiters: wait_handles applies the queue before re-checking
    pthread_cond_broadcast(&obj_cv);
    pthread_mutex_unlock(&gm);
}
static void apply_pending_events(void);
// Called at every import: after 2 ms of holding the GIL with others waiting, hand it over.
void gil_maybe_yield(Cpu *c) {
    if (npending) apply_pending_events();
    static int every = -1;      // TEST (local): BOOT_TEST_GILYIELD=1 hands over at every import, as heavy load does
    if (every < 0) every = getenv("BOOT_TEST_GILYIELD") != NULL;
    if (next_ticket - serving > 1 && (every || host_ticks() - held_since > 2000000)) gil_yield(c);
}
// Releases the GIL until another object changes state or `deadline` (host_ticks ns, 0 = none) passes.
static void wait_change(uint64_t deadline) {
    pthread_mutex_lock(&gm);
    uint64_t seq = change_seq;
    gil_release_locked();
    while (seq == change_seq) {
        if (deadline) {
            uint64_t now = host_ticks();
            if (now >= deadline) break;
            // Wake at least every 2 ms: waitable timers become signaled without anyone calling changed().
            uint64_t until = deadline - now > 2000000 ? now + 2000000 : deadline;
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            uint64_t ns = (uint64_t)ts.tv_nsec + (until - now);
            ts.tv_sec += (time_t)(ns / 1000000000ull);
            ts.tv_nsec = (long)(ns % 1000000000ull);
            pthread_cond_timedwait(&obj_cv, &gm, &ts);
            if (host_ticks() >= until) break;
        } else {
            pthread_cond_wait(&obj_cv, &gm);
        }
    }
    gil_acquire_locked();
    pthread_mutex_unlock(&gm);
}

// ---------------- kernel objects ----------------
enum { K_FREE, K_EVENT, K_MUTEX, K_SEM, K_THREAD, K_TIMER };
typedef struct {
    int type;
    int manual, signaled;         // event, timer
    uint32_t count, max;          // semaphore
    uint32_t owner, recursion;    // mutex
    uint64_t due, period;         // timer (host ns)
    // thread
    uint32_t tid, start, param, exit_code, teb, stack_lo, stack_size;
    int suspend, terminate, running;
    Cpu *cpu;
    pthread_t th;
    jmp_buf exit_jb;
} KObj;
#define KOBJ_BASE 0x2000u         // to 0x2800: find handles start there (boot.c lists all the handle ranges)
#define MAX_KOBJ 512
static KObj kobj[MAX_KOBJ];
static uint32_t next_tid = 2;
static __thread KObj *self;       // NULL on the main thread
#define MAIN_TEB 0x7FFDE000u

static KObj *kget(uint32_t h) {
    uint32_t i = (h - KOBJ_BASE) / 4;
    return (h >= KOBJ_BASE && i < MAX_KOBJ && kobj[i].type) ? &kobj[i] : NULL;
}
static uint32_t knew(int type, KObj **out) {
    for (int i = 1; i < MAX_KOBJ; ++i)
        if (!kobj[i].type) {
            memset(&kobj[i], 0, sizeof kobj[i]);
            kobj[i].type = type;
            *out = &kobj[i];
            return KOBJ_BASE + 4 * (uint32_t)i;
        }
    fprintf(stderr, "boot: out of kernel objects\n");
    exit(1);
}
static uint32_t cur_tid(void) { return self ? self->tid : 1; }
// gil_watch never samples a holder with tid 1 (the game's main thread and threads without a guest thread), so
// those threads keep no current-import record: dispatch and host_import skip its four host_ticks and stores per
// call, ~1 ms/frame at 3,500 draws (A/B). gilwait.log is unchanged.
GilRec *gil_rec_self(void) { return self ? &my_rec : NULL; }

// Satisfies a wait on o for the current thread if possible (consuming it as Windows does).
static int try_take(KObj *o) {
    switch (o->type) {
    case K_EVENT: if (!o->signaled) return 0; if (!o->manual) o->signaled = 0; return 1;
    case K_MUTEX: if (o->owner && o->owner != cur_tid()) return 0; o->owner = cur_tid(); ++o->recursion; return 1;
    case K_SEM: if (!o->count) return 0; --o->count; return 1;
    case K_THREAD: return o->signaled;
    case K_TIMER:
        if (!o->due || host_ticks() < o->due) return 0;
        if (o->period) o->due += o->period; else if (!o->manual) o->due = 0;
        return 1;
    }
    return 0;
}
static int ready(KObj *o) {       // would try_take succeed (without consuming)?
    switch (o->type) {
    case K_EVENT: return o->signaled;
    case K_MUTEX: return !o->owner || o->owner == cur_tid();
    case K_SEM: return o->count > 0;
    case K_THREAD: return o->signaled;
    case K_TIMER: return o->due && host_ticks() >= o->due;
    }
    return 0;
}

// Releases the GIL until `pred` says the wait may be satisfied, `deadline` (host_ticks ns, 0 = none) passes, a
// host-set event is queued, or this thread is asked to suspend or terminate. pred runs under gm without the GIL:
// kernel objects (and critical sections in guest memory) only change with the GIL held, and every change is
// followed by changed(), which takes gm, so a waiter never misses one. Unlike wait_change, a waiter whose objects
// are still not ready keeps waiting without taking the GIL back: every GIL hand-over costs the game thread a
// thread wake-up. pred may lower *wake to the time a waitable timer becomes due.
// What each guest thread is blocked on, for hang reports (diag.c guest_state): written by the thread itself around
// its wait, read racily by the hang monitor (diagnostic only). Slot 0 is the main thread, slot i kobj[i].
typedef struct { const char *kind; uint32_t obj, owner, n; uint32_t h[4]; uint64_t since; } WaitRec;
static WaitRec wait_recs[MAX_KOBJ];
static WaitRec *my_wait(void) { return self ? &wait_recs[self - kobj] : &wait_recs[0]; }
static void wait_mark(const char *kind, uint32_t obj, uint32_t owner) {
    WaitRec *w = my_wait();
    w->obj = obj; w->owner = owner; w->n = 0; w->since = host_ticks(); w->kind = kind;
}
static void wait_clear(void) { my_wait()->kind = NULL; }
static void wait_until(int (*pred)(void *arg, uint64_t *wake), void *arg, uint64_t deadline) {
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
}
typedef struct { KObj **o; uint32_t n; int all; } WaitSet;
static int wait_set_ready(void *arg, uint64_t *wake) {
    WaitSet *w = arg;
    int any = 0, every = 1;
    for (uint32_t k = 0; k < w->n; ++k) {
        int r = ready(w->o[k]);
        any |= r; every &= r;
        if (w->o[k]->type == K_TIMER && w->o[k]->due && (!*wake || w->o[k]->due < *wake)) *wake = w->o[k]->due;
    }
    return w->all ? every : any;
}

// Waits on n handles. Returns WAIT_OBJECT_0 + i, WAIT_TIMEOUT (0x102) or WAIT_FAILED.
static uint32_t wait_handles(Cpu *c, uint32_t n, const uint32_t *h, int all, uint32_t ms) {
    uint64_t deadline = ms == 0xFFFFFFFF ? 0 : host_ticks() + (uint64_t)ms * 1000000ull;
    KObj *o[64];
    if (n == 0 || n > 64) { last_error = 87; return 0xFFFFFFFF; }
    for (uint32_t i = 0; i < n; ++i) {
        if (h[i] == 0xFFFFFFFE || h[i] == 0xFFFFFFFF) { last_error = 6; return 0xFFFFFFFF; }   // pseudo handles
        if (!(o[i] = kget(h[i]))) {
            if (boot_trace) fprintf(stderr, "      wait on unknown handle %08X\n", h[i]);
            last_error = 6;
            return 0xFFFFFFFF;
        }
    }
    for (;;) {
        // Events set by host threads (DirectSound notifications) are queued until a guest thread applies them. A waiter
        // must do it too: otherwise, with every guest thread waiting, the event stays unseen until the wait times out
        // (the 1.0 s stalls in soak races).
        if (npending) apply_pending_events();
        if (all) {
            uint32_t k = 0;
            while (k < n && ready(o[k])) ++k;
            if (k == n) { for (k = 0; k < n; ++k) try_take(o[k]); changed(); return 0; }
        } else {
            for (uint32_t k = 0; k < n; ++k) if (try_take(o[k])) { changed(); return k; }
        }
        if (ms == 0 || (deadline && host_ticks() >= deadline)) return 0x102;
        WaitSet ws = {o, n, all};
        wait_mark(all ? "WaitForMultipleObjects(all)" : n > 1 ? "WaitForMultipleObjects" : "WaitForSingleObject", h[0], 0);
        { WaitRec *w = my_wait(); w->n = n; for (uint32_t k = 0; k < n && k < 4; ++k) w->h[k] = h[k]; }
        wait_until(wait_set_ready, &ws, deadline);
        wait_clear();
        thread_checkpoint(c);
    }
}

// ---------------- threads ----------------
static void thread_exit_now(KObj *t, uint32_t code) {
    t->exit_code = code;
    longjmp(t->exit_jb, 1);
}
// After (re)acquiring the GIL: honor SuspendThread/TerminateThread aimed at this thread.
static void thread_checkpoint(Cpu *c) {
    (void)c;
    KObj *t = self;
    if (!t) return;
    if (t->terminate) thread_exit_now(t, t->exit_code);
    while (t->suspend > 0) { wait_change(0); if (t->terminate) thread_exit_now(t, t->exit_code); }
}

// Guest stacks (with their TEB) of finished threads, reused by the next thread of the same size: the bump heap never
// frees, and the game starts a thread for every load (each race <-> front end switch). Changed under the GIL.
static struct { uint32_t lo, size; } free_stacks[32];
static int nfree_stacks;

static void *thread_main(void *p) {
    KObj *t = p;
    self = t;
    guest_worker = 1;
    gil_acquire();
    if (!setjmp(t->exit_jb)) {
        while (t->suspend > 0) wait_change(0);
        if (!t->terminate) {
            uint32_t a = t->param;
            t->running = 1;
            if (boot_trace) fprintf(stderr, "  [thread %u] start %08X(%08X)\n", t->tid, t->start, t->param);
            t->exit_code = call_guest(t->cpu, t->start, 1, &a);
        }
    }
    t->running = 0;
    t->signaled = 1;
    if (nfree_stacks < 32) { free_stacks[nfree_stacks].lo = t->stack_lo; free_stacks[nfree_stacks++].size = t->stack_size; }
    if (boot_trace) fprintf(stderr, "  [thread %u] exit %u\n", t->tid, t->exit_code);
    changed();
    gil_release();
    return NULL;
}

static uint32_t thread_create(uint32_t stack, uint32_t start, uint32_t param, uint32_t flags, uint32_t *tid_out) {
    KObj *t;
    uint32_t h = knew(K_THREAD, &t);
    if (stack < 0x100000) stack = 0x100000;
    uint32_t lo = 0;
    for (int k = 0; k < nfree_stacks && !lo; ++k)
        if (free_stacks[k].size == stack) { lo = free_stacks[k].lo; free_stacks[k] = free_stacks[--nfree_stacks]; memset(G + lo, 0, stack + 0x2000); }
    if (!lo) lo = galloc(stack + 0x2000);
    uint32_t teb = (lo + stack + 0x1000 + 0xFFF) & ~0xFFFu;     // a page-aligned TEB above the stack
    uint32_t top = (lo + stack) & ~15u;
    t->tid = next_tid++;
    t->start = start;
    t->param = param;
    t->teb = teb;
    t->stack_lo = lo;
    t->stack_size = stack;
    t->suspend = (flags & 4) ? 1 : 0;                           // CREATE_SUSPENDED
    M32(teb + 0x00) = 0xFFFFFFFF;                               // SEH chain end
    M32(teb + 0x04) = top;
    M32(teb + 0x08) = lo;
    M32(teb + 0x18) = teb;
    M32(teb + 0x24) = t->tid;
    M32(teb + 0x30) = M32(MAIN_TEB + 0x30);                     // PEB
    t->cpu = calloc(1, sizeof(Cpu));
    t->cpu->fs_base = teb;
    t->cpu->esp = top - 16;
    t->cpu->cw = 0x027F;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, 64u << 20);                  // translated code recurses on the host stack
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);  // nothing joins: its resources go when it ends
    pthread_create(&t->th, &at, thread_main, t);
    pthread_attr_destroy(&at);
    if (boot_trace) fprintf(stderr, "  [thread] CreateThread(start %08X, param %08X, flags %X) -> tid %u\n", start, param, flags, t->tid);
    *tid_out = t->tid;
    return h;
}

static void apply_pending_events(void) {
    uint32_t ev[64];
    int n, any = 0;
    for (;;) {                                      // in chunks of 64; setting an event twice is the same as once
        pthread_mutex_lock(&gm);
        n = npending < 64 ? npending : 64;
        npending -= n;
        memcpy(ev, pending_events + npending, (size_t)n * sizeof *ev);
        pthread_mutex_unlock(&gm);
        if (!n) break;
        any = 1;
        for (int i = 0; i < n; ++i) { KObj *o = kget(ev[i]); if (o && o->type == K_EVENT) o->signaled = 1; }
    }
    if (any) changed();
}

uint32_t diag_thread_id(void) { return cur_tid(); }
static uint32_t cur_tid_diag(void) { return cur_tid(); }
// tid -> thread start address (0 = main or unknown), for gilwait.log
uint32_t threads_start_of(uint32_t tid) {
    for (int i = 1; i < MAX_KOBJ; ++i) if (kobj[i].type == K_THREAD && kobj[i].tid == tid) return kobj[i].start;
    return 0;
}
// For hang reports: what thread `tid` is blocked on right now ("" when it isn't in a wait).
void threads_wait_desc(uint32_t tid, char *buf, size_t n) {
    buf[0] = 0;
    WaitRec *w = NULL;
    if (tid == 1) w = &wait_recs[0];
    else for (int i = 1; i < MAX_KOBJ; ++i) if (kobj[i].type == K_THREAD && kobj[i].tid == tid) { w = &wait_recs[i]; break; }
    const char *kind = w ? w->kind : NULL;
    if (!kind) return;
    double secs = (double)(host_ticks() - w->since) / 1e9;
    if (!strcmp(kind, "EnterCriticalSection")) {
        snprintf(buf, n, "%s(%08X) owned by thread %u (recursion %u) for %.1f s", kind, w->obj, M32(w->obj + 12), M32(w->obj + 8), secs);
        return;
    }
    int k = snprintf(buf, n, "%s for %.1f s on", kind, secs);
    for (uint32_t i = 0; i < w->n && i < 4 && k > 0 && (size_t)k < n; ++i) {
        KObj *o = kget(w->h[i]);
        static const char *const tn[] = {"free", "event", "mutex", "semaphore", "thread", "timer"};
        k += snprintf(buf + k, n - (size_t)k, " %08X(%s%s%s)", w->h[i], o ? tn[o->type] : "?",
                      o && o->type == K_EVENT ? (o->signaled ? " set" : " unset") : "",
                      o && o->type == K_SEM ? (o->count ? " >0" : " 0") : "");
    }
}
static pthread_t main_thread;
static Cpu *main_cpu;
void threads_set_main_cpu(Cpu *c) { main_cpu = c; }
// For diagnostic reports: every guest thread (pthread, Cpu, thread id), main first.
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap) {
    int n = 0;
    if (n < cap) { ths[n] = main_thread; cpus[n] = main_cpu; tids[n] = 1; ++n; }
    for (int i = 1; i < MAX_KOBJ && n < cap; ++i)
        if (kobj[i].type == K_THREAD && kobj[i].running && kobj[i].cpu) { ths[n] = kobj[i].th; cpus[n] = kobj[i].cpu; tids[n] = kobj[i].tid; ++n; }
    return n;
}

void threads_init(void) {
    main_thread = pthread_self();
    gil_acquire();
    M32(MAIN_TEB + 0x24) = 1;
}

// ---------------- critical sections ----------------
// CRITICAL_SECTION: +0 DebugInfo, +4 LockCount, +8 RecursionCount, +12 OwningThread, +16 LockSemaphore, +20 SpinCount
static int cs_free(void *arg, uint64_t *wake) {
    (void)wake;
    uint32_t cs = (uint32_t)(uintptr_t)arg, owner = M32(cs + 12);
    return !owner || owner == cur_tid();
}
static void cs_enter(Cpu *c, uint32_t cs) {
    uint32_t me = cur_tid();
    while (M32(cs + 12) && M32(cs + 12) != me) {
        wait_mark("EnterCriticalSection", cs, M32(cs + 12));
        wait_until(cs_free, (void *)(uintptr_t)cs, 0);
        wait_clear();
        thread_checkpoint(c);
    }
    M32(cs + 12) = me;
    M32(cs + 8) += 1;
}
static void cs_leave(uint32_t cs) {
    if (M32(cs + 8) && --M32(cs + 8) == 0) { M32(cs + 12) = 0; changed(); }
}

// ---------------- imports ----------------
int win32_threads(Cpu *c, const char *fn) {
    if (!strcmp(fn, "InitializeCriticalSection")) { memset(G + arg(c, 0), 0, 24); M32(arg(c, 0) + 4) = 0xFFFFFFFF; RET(0, 1); }
    if (!strcmp(fn, "InitializeCriticalSectionAndSpinCount")) { memset(G + arg(c, 0), 0, 24); M32(arg(c, 0) + 4) = 0xFFFFFFFF; RET(1, 2); }
    if (!strcmp(fn, "DeleteCriticalSection")) RET(0, 1);
    if (!strcmp(fn, "EnterCriticalSection")) { cs_enter(c, arg(c, 0)); RET(0, 1); }
    if (!strcmp(fn, "TryEnterCriticalSection")) {
        uint32_t cs = arg(c, 0);
        if (M32(cs + 12) && M32(cs + 12) != cur_tid()) RET(0, 1);
        M32(cs + 12) = cur_tid(); M32(cs + 8) += 1;
        RET(1, 1);
    }
    if (!strcmp(fn, "LeaveCriticalSection")) { cs_leave(arg(c, 0)); RET(0, 1); }

    if (!strcmp(fn, "TlsAlloc")) {
        static uint32_t used[64];
        for (uint32_t i = 1; i < 64; ++i) if (!used[i]) { used[i] = 1; RET(i, 0); }
        RET(0xFFFFFFFF, 0);
    }
    if (!strcmp(fn, "TlsFree")) RET(1, 1);
    if (!strcmp(fn, "TlsSetValue")) { M32(c->fs_base + 0xE10 + 4 * (arg(c, 0) & 63)) = arg(c, 1); RET(1, 2); }
    if (!strcmp(fn, "TlsGetValue")) { last_error = 0; RET(M32(c->fs_base + 0xE10 + 4 * (arg(c, 0) & 63)), 1); }
    if (!strcmp(fn, "GetCurrentThreadId")) RET(cur_tid(), 0);
    if (!strcmp(fn, "GetCurrentThread")) RET(0xFFFFFFFE, 0);

    if (!strcmp(fn, "Sleep") || !strcmp(fn, "SleepEx")) {
        uint32_t ms = arg(c, 0);
        int n = fn[5] == 'E' ? 2 : 1;
        if (ms == 0) { gil_yield(c); RET(0, n); }
        if (ms == 0xFFFFFFFF) for (;;) wait_change(0);
        uint64_t t0 = host_ticks(), until = t0 + (uint64_t)ms * 1000000ull, t1;
        if (n == 2 && !self && iowake_on()) {     // the game thread's alertable sleep: also ends on a file read done
            if (until > t0 + 1000000) until = t0 + 1000000;   // and polls again after 1 ms at most: some steps
                                                                // between reads need a poll, with no I/O to wake on
            static uint64_t seen;                 // io_seq at the end of the last such sleep: a read finished since
            pthread_mutex_lock(&gm);              // (while the game thread was busy) ends this one at once
            uint64_t seq = seen;
            gil_release_locked();
            while (seq == io_seq) {
                uint64_t now = host_ticks();
                if (now >= until) break;
                struct timespec ts = {(time_t)((until - now) / 1000000000ull), (long)((until - now) % 1000000000ull)};
                pthread_cond_timedwait_relative_np(&io_cv, &gm, &ts);
            }
            seen = io_seq;
            t1 = host_ticks();
            gil_acquire_locked();
            pthread_mutex_unlock(&gm);
        } else {
            gil_release();
            // One clock read per check: reading it again after the test could pass `until` and underflow `left` into
            // an ~18e9 s nanosleep, parking the thread (Snd's mixer sleeps here: its ring then loops; audit2 #1).
            for (uint64_t now; (now = host_ticks()) < until;) {
                uint64_t left = until - now;
                struct timespec ts = {(time_t)(left / 1000000000ull), (long)(left % 1000000000ull)};
                nanosleep(&ts, NULL);
            }
            t1 = host_ticks();
            gil_acquire();
        }
        if (!self) diag_idle_ns[IDLE_SLEEP] += host_ticks() - t0;
        if (diag_on && !self) {                  // hitches: which game code sleeps on the game thread, and how long
            char who[160] = "";
            for (int k = 0, nres = 0; k < 24 && nres < 3; ++k) {
                uint32_t off = 0, a = M32(c->esp + 4u * (uint32_t)k);
                const char *sym = diag_symbol(a, &off);
                if (sym && off < 0x4000) { snprintf(who + strlen(who), sizeof who - strlen(who), " %s+0x%X", sym, off); ++nres; }
            }
            diag_event("sleep: game thread %s(%u) %.2f ms (wait %.2f, GIL %.2f) frame %llu flow %u from%s", fn, ms,
                       (host_ticks() - t0) / 1e6, (t1 - t0) / 1e6, (host_ticks() - t1) / 1e6,
                       (unsigned long long)perf_nframes, M32(0x00925E90u), who);
        }
        thread_checkpoint(c);
        RET(0, n);
    }
    if (!strcmp(fn, "SwitchToThread")) { gil_yield(c); RET(1, 0); }

    if (!strcmp(fn, "CreateEventA")) {            // (sa, manual, initial, name)
        KObj *o;
        uint32_t h = knew(K_EVENT, &o);
        o->manual = arg(c, 1) != 0;
        o->signaled = arg(c, 2) != 0;
        last_error = 0;
        RET(h, 4);
    }
    if (!strcmp(fn, "SetEvent") || !strcmp(fn, "ResetEvent") || !strcmp(fn, "PulseEvent")) {
        KObj *o = kget(arg(c, 0));
        if (!o || o->type != K_EVENT) { last_error = 6; RET(0, 1); }
        if (fn[0] == 'R') o->signaled = 0;
        else { o->signaled = 1; changed(); }
        if (fn[0] == 'P') { gil_yield(c); o->signaled = 0; }
        RET(1, 1);
    }
    if (!strcmp(fn, "CreateMutexA")) {            // (sa, initialOwner, name)
        KObj *o;
        uint32_t h = knew(K_MUTEX, &o);
        if (arg(c, 1)) { o->owner = cur_tid(); o->recursion = 1; }
        last_error = 0;                                         // not ERROR_ALREADY_EXISTS: we're the only instance
        RET(h, 3);
    }
    if (!strcmp(fn, "ReleaseMutex")) {
        KObj *o = kget(arg(c, 0));
        if (!o || o->type != K_MUTEX || o->owner != cur_tid()) { last_error = 288; RET(0, 1); }
        if (--o->recursion == 0) { o->owner = 0; changed(); }
        RET(1, 1);
    }
    if (!strcmp(fn, "CreateSemaphoreA")) {        // (sa, initial, max, name)
        KObj *o;
        uint32_t h = knew(K_SEM, &o);
        o->count = arg(c, 1);
        o->max = arg(c, 2);
        last_error = 0;
        RET(h, 4);
    }
    if (!strcmp(fn, "ReleaseSemaphore")) {        // (h, n, prev*)
        KObj *o = kget(arg(c, 0));
        if (!o || o->type != K_SEM || o->count + arg(c, 1) > o->max) { last_error = 298; RET(0, 3); }
        if (arg(c, 2)) M32(arg(c, 2)) = o->count;
        o->count += arg(c, 1);
        changed();
        RET(1, 3);
    }
    if (!strcmp(fn, "CreateWaitableTimerA")) {    // (sa, manual, name)
        KObj *o;
        uint32_t h = knew(K_TIMER, &o);
        o->manual = arg(c, 1) != 0;
        RET(h, 3);
    }
    if (!strcmp(fn, "SetWaitableTimer")) {        // (h, due*, period ms, apc, apcarg, resume)
        KObj *o = kget(arg(c, 0));
        if (!o || o->type != K_TIMER) RET(0, 6);
        int64_t due = (int64_t)M64(arg(c, 1));
        // negative: relative, in 100 ns units; positive: an absolute FILETIME (the game's TIMER thread re-arms its
        // 100 Hz timer with absolute times; treating them as "now" made it spin)
        uint64_t now_ft = host_filetime();
        uint64_t rel = due < 0 ? (uint64_t)(-due) : ((uint64_t)due > now_ft ? (uint64_t)due - now_ft : 0);
        o->due = host_ticks() + rel * 100ull + 1;
        o->period = (uint64_t)arg(c, 2) * 1000000ull;
        changed();                                              // waiters re-compute when to wake
        RET(1, 6);
    }
    if (!strcmp(fn, "WaitForSingleObject") || !strcmp(fn, "WaitForSingleObjectEx")) {
        uint32_t h = arg(c, 0);
        int n = fn[19] == 'E' ? 3 : 2;
        RET(wait_handles(c, 1, &h, 0, arg(c, 1)), n);
    }
    if (!strcmp(fn, "WaitForMultipleObjects")) {  // (n, handles*, all, ms)
        uint32_t n = arg(c, 0), hs[64];
        for (uint32_t i = 0; i < n && i < 64; ++i) hs[i] = M32(arg(c, 1) + 4 * i);
        RET(wait_handles(c, n, hs, arg(c, 2) != 0, arg(c, 3)), 4);
    }

    if (!strcmp(fn, "CreateThread")) {            // (sa, stack, start, param, flags, tid*)
        uint32_t tid;
        uint32_t h = thread_create(arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4), &tid);
        if (arg(c, 5)) M32(arg(c, 5)) = tid;
        RET(h, 6);
    }
    if (!strcmp(fn, "ResumeThread") || !strcmp(fn, "SuspendThread")) {
        KObj *t = kget(arg(c, 0));
        if (!t || t->type != K_THREAD) RET(0xFFFFFFFF, 1);
        int prev = t->suspend;
        if (fn[0] == 'R') { if (t->suspend > 0) --t->suspend; changed(); }
        else ++t->suspend;
        if (fn[0] == 'S' && t == self) thread_checkpoint(c);
        RET((uint32_t)prev, 1);
    }
    if (!strcmp(fn, "TerminateThread")) {         // (h, code)
        KObj *t = kget(arg(c, 0));
        if (!t || t->type != K_THREAD) RET(0, 2);
        t->exit_code = arg(c, 1);
        t->terminate = 1;
        changed();
        if (t == self) thread_exit_now(t, arg(c, 1));
        RET(1, 2);
    }
    if (!strcmp(fn, "ExitThread")) {
        if (!self) { fprintf(stderr, "boot: main thread called ExitThread(%u)\n", arg(c, 0)); exit((int)arg(c, 0)); }
        thread_exit_now(self, arg(c, 0));
    }
    if (!strcmp(fn, "GetExitCodeThread")) {       // (h, code*)
        KObj *t = kget(arg(c, 0));
        if (!t || t->type != K_THREAD) RET(0, 2);
        M32(arg(c, 1)) = t->signaled ? t->exit_code : 259;      // STILL_ACTIVE
        RET(1, 2);
    }
    if (!strcmp(fn, "QueueUserAPC")) RET(1, 3);                 // never delivered: the game doesn't wait alertably on them
    if (!strcmp(fn, "DuplicateHandle")) {         // (srcproc, src, dstproc, dst*, access, inherit, options)
        uint32_t src = arg(c, 1);
        if (src == 0xFFFFFFFE) {                                // GetCurrentThread() -> a real handle to this thread
            KObj *t = self;
            if (!t) {                                           // main thread: give it a thread object too
                static uint32_t main_h;
                if (!main_h) { KObj *m; main_h = knew(K_THREAD, &m); m->tid = 1; m->running = 1; }
                M32(arg(c, 3)) = main_h;
                RET(1, 7);
            }
            M32(arg(c, 3)) = KOBJ_BASE + 4 * (uint32_t)(t - kobj);
            RET(1, 7);
        }
        M32(arg(c, 3)) = src;
        RET(1, 7);
    }
    if (!strcmp(fn, "InterlockedIncrement")) { M32(arg(c, 0)) += 1; RET(M32(arg(c, 0)), 1); }
    if (!strcmp(fn, "InterlockedDecrement")) { M32(arg(c, 0)) -= 1; RET(M32(arg(c, 0)), 1); }
    if (!strcmp(fn, "InterlockedExchange")) { uint32_t o = M32(arg(c, 0)); M32(arg(c, 0)) = arg(c, 1); RET(o, 2); }
    if (!strcmp(fn, "InterlockedCompareExchange")) {
        uint32_t o = M32(arg(c, 0));
        if (o == arg(c, 2)) M32(arg(c, 0)) = arg(c, 1);
        RET(o, 3);
    }
    if (!strcmp(fn, "InterlockedExchangeAdd")) { uint32_t o = M32(arg(c, 0)); M32(arg(c, 0)) += arg(c, 1); RET(o, 2); }
    return 0;
}

// CloseHandle on a kernel object (threads keep running; their record stays until they exit).
int threads_close(uint32_t h) {
    KObj *o = kget(h);
    if (!o) return 0;
    if (o->type != K_THREAD || o->signaled) o->type = K_FREE;
    return 1;
}
