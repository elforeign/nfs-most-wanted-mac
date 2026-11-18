// Game-thread CPU profiles (BOOT_DIAG only), shared by both images' diagnostics. Included once, after
// diag_threads.h, by a source that defines frame_now, last_frame_ns, idle_now, diag_dir and diag_symbol.
//
// <dir>/profile_N.txt: a frame that takes longer than BOOT_DIAG_PROFILE ms (default 100; 0 = off; at most 20 frames)
// is sampled every ~2 ms (the game thread's host stack, so translated functions show as f_XXXXXXXX with the game's
// name, host code by its own): functions by inclusive and self samples, and the most common stacks. Shows where a
// load hitch goes without a profiler attached.
// <dir>/profile_session.txt: the whole session, one game-thread stack every BOOT_DIAG_PROFILE_SESSION ms (default 20;
// 0 = off) while it is drawing (not while it waits for window messages on purpose), aggregated by function into
// inclusive and self tables. Rewritten every 30 s and at exit; an F10 report copies it as profile.txt.
// Samples suspend the game thread (thread_suspend), read its registers and walk its frame-pointer chain, then
// resume it: no signal, so a sleep or wait the game thread is in finishes on time (a SIGUSR2 sample would end a
// sleep early with EINTR, e.g. a 400 ms BOOT_DIAG_HITCH frame took 17 ms). Nothing that can take a lock runs
// while it is suspended; names are resolved afterwards.
#include <mach/mach.h>
static pthread_t game_thread;
static int have_game_thread;
static int prof_sample(void **out, int max) {
    static uintptr_t lo, hi;
    if (!hi) {
        hi = (uintptr_t)pthread_get_stackaddr_np(game_thread);
        lo = hi - pthread_get_stacksize_np(game_thread);
    }
    thread_t th = pthread_mach_thread_np(game_thread);
    if (thread_suspend(th) != KERN_SUCCESS) return 0;
    int n = 0;
#if defined(__arm64__)
    arm_thread_state64_t st;
    mach_msg_type_number_t cnt = ARM_THREAD_STATE64_COUNT;
    if (thread_get_state(th, ARM_THREAD_STATE64, (thread_state_t)&st, &cnt) == KERN_SUCCESS) {
        uintptr_t fp = (uintptr_t)arm_thread_state64_get_fp(st);
        out[n++] = (void *)(uintptr_t)arm_thread_state64_get_pc(st);
        out[n++] = (void *)(uintptr_t)arm_thread_state64_get_lr(st);   // a leaf's caller (in its own frame otherwise)
        // Recheck every saved next pointer before dereferencing; subtraction cannot wrap.
        while (n < max && hi >= 16 && fp >= lo && fp <= hi - 16 && !(fp & 7)) {
            uintptr_t next = ((uintptr_t *)fp)[0], ret = ((uintptr_t *)fp)[1];
            if (!ret) break;
            out[n++] = (void *)ret;
            if (next <= fp) break;
            fp = next;
        }
    }
#endif
    thread_resume(th);
    return n;
}
#define PROF_SAMPLES 4096
#define PROF_DEPTH 48
static void *prof_pcs[PROF_SAMPLES][PROF_DEPTH];
static uint8_t prof_depth[PROF_SAMPLES];
static int prof_n, prof_files, prof_max = 20;   // BOOT_DIAG_PROFILE_MAX: how many profile_N.txt (default 20)
static uint64_t prof_frame_ns, prof_frame_no;
static void prof_name(void *pc, char *buf, size_t n) {
    Dl_info di;
    const char *s = dladdr(pc, &di) && di.dli_sname ? di.dli_sname : NULL;
    if (s && !strncmp(s, "f_00", 4) && strlen(s) >= 10) {
        uint32_t a = (uint32_t)strtoul(s + 2, NULL, 16), off = 0;
        const char *g = diag_symbol(a, &off);
        snprintf(buf, n, "%s %s", s, g ? g : "?");
    } else if (s) snprintf(buf, n, "%s", s);
    else snprintf(buf, n, "%p", pc);
}
typedef struct { void *pc; int incl, self; } ProfFn;
static int cmp_incl(const void *a, const void *b) { return ((const ProfFn *)b)->incl - ((const ProfFn *)a)->incl; }
static int cmp_self(const void *a, const void *b) { return ((const ProfFn *)b)->self - ((const ProfFn *)a)->self; }
static void prof_write(uint64_t end_ns) {
    if (prof_n == 0 || prof_files >= prof_max) return;
    char path[1024], nm[256];
    snprintf(path, sizeof path, "%s/profile_%d.txt", diag_dir, ++prof_files);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "frame %llu took %.1f ms; %d samples of the game thread (~2 ms apart, from %d ms into the frame)\n",
            (unsigned long long)prof_frame_no, (end_ns - prof_frame_ns) / 1e6, prof_n,
            getenv("BOOT_DIAG_PROFILE") ? atoi(getenv("BOOT_DIAG_PROFILE")) : 100);
    static ProfFn fns[8192];
    int nf = 0;
    for (int s = 0; s < prof_n; ++s) {
        void *start[PROF_DEPTH];                     // functions by their symbol's start: counted once per sample
        for (int d = 0; d < prof_depth[s]; ++d) {
            Dl_info di;
            start[d] = dladdr(prof_pcs[s][d], &di) && di.dli_saddr ? di.dli_saddr : prof_pcs[s][d];
            int k = 0, seen = 0;
            for (int e = 0; e < d; ++e) if (start[e] == start[d]) seen = 1;
            for (; k < nf && fns[k].pc != start[d]; ++k) {}
            if (k == nf) { if (nf == 8192) continue; fns[nf].pc = start[d]; fns[nf].incl = fns[nf].self = 0; ++nf; }
            if (!seen) fns[k].incl++;
            if (d == 0) fns[k].self++;
        }
    }
    qsort(fns, (size_t)nf, sizeof *fns, cmp_incl);
    fprintf(f, "\n-- inclusive (samples with the function on the stack) --\n");
    for (int k = 0; k < nf && k < 60; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%6d %5.1f%%  %s\n", fns[k].incl, 100.0 * fns[k].incl / prof_n, nm); }
    qsort(fns, (size_t)nf, sizeof *fns, cmp_self);
    fprintf(f, "\n-- self (innermost function) --\n");
    for (int k = 0; k < nf && k < 30 && fns[k].self; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%6d %5.1f%%  %s\n", fns[k].self, 100.0 * fns[k].self / prof_n, nm); }
    fprintf(f, "\n-- every 8th sample's stack --\n");
    for (int s = 0; s < prof_n; s += 8) {
        fprintf(f, "sample %d:\n", s);
        for (int d = 0; d < prof_depth[s]; ++d) { prof_name(prof_pcs[s][d], nm, sizeof nm); fprintf(f, "    %s\n", nm); }
    }
    fclose(f);
}

// ---- the whole session: counts by function start, so its memory is fixed however long the session runs ----
#define SESS_PCS 32768                 // return address -> its function's start (dladdr once per address)
#define SESS_FNS 8192                  // function start -> inclusive / self samples
static struct { void *pc, *start; } sess_pc[SESS_PCS];
static ProfFn sess_fn[SESS_FNS];
static uint64_t sess_samples, sess_idle, sess_missed, sess_t0, sess_written;
static pthread_mutex_t sess_mu = PTHREAD_MUTEX_INITIALIZER;
static uint32_t prof_hash(void *p) { uint64_t v = (uint64_t)(uintptr_t)p; v ^= v >> 33; v *= 0xff51afd7ed558ccdull; return (uint32_t)(v >> 32); }
static void *sess_start_of(void *pc) {
    for (uint32_t h = prof_hash(pc) % SESS_PCS, k = 0; k < 64; ++k, h = (h + 1) % SESS_PCS) {
        if (sess_pc[h].pc == pc) return sess_pc[h].start;
        if (!sess_pc[h].pc) {
            Dl_info di;
            sess_pc[h].pc = pc;
            sess_pc[h].start = dladdr(pc, &di) && di.dli_saddr ? di.dli_saddr : pc;
            return sess_pc[h].start;
        }
    }
    Dl_info di;                                       // table crowded: resolve without caching
    return dladdr(pc, &di) && di.dli_saddr ? di.dli_saddr : pc;
}
static ProfFn *sess_fn_of(void *start) {
    for (uint32_t h = prof_hash(start) % SESS_FNS, k = 0; k < SESS_FNS; ++k, h = (h + 1) % SESS_FNS) {
        if (sess_fn[h].pc == start) return &sess_fn[h];
        if (!sess_fn[h].pc) { sess_fn[h].pc = start; return &sess_fn[h]; }
    }
    return NULL;
}
static void sess_add(void *const *pcs, int depth) {
    void *start[PROF_DEPTH];
    pthread_mutex_lock(&sess_mu);
    ++sess_samples;
    for (int d = 0; d < depth; ++d) {
        start[d] = sess_start_of(pcs[d]);
        int seen = 0;                                 // recursion: once per sample
        for (int e = 0; e < d && !seen; ++e) seen = start[e] == start[d];
        ProfFn *fn = seen ? NULL : sess_fn_of(start[d]);
        if (!fn) continue;
        fn->incl++;
        if (d == 0) fn->self++;
    }
    pthread_mutex_unlock(&sess_mu);
}
static int sess_period_ms(void) {
    static int ms = -1;
    if (ms < 0) ms = getenv("BOOT_DIAG_PROFILE_SESSION") ? atoi(getenv("BOOT_DIAG_PROFILE_SESSION")) : 20;
    return ms;
}
// Writes <dir>/profile_session.txt (via a temporary file) from the counts so far.
void diag_profile_session_write(void) {
    if (!diag_on || !diag_dir || sess_period_ms() <= 0) return;
    static ProfFn fns[SESS_FNS];
    pthread_mutex_lock(&sess_mu);
    int nf = 0;
    for (int k = 0; k < SESS_FNS; ++k) if (sess_fn[k].pc) fns[nf++] = sess_fn[k];
    uint64_t n = sess_samples, idle = __atomic_load_n(&sess_idle, __ATOMIC_RELAXED),
             missed = __atomic_load_n(&sess_missed, __ATOMIC_RELAXED),
             t0 = __atomic_load_n(&sess_t0, __ATOMIC_RELAXED), now = host_ticks();
    __atomic_store_n(&sess_written, now, __ATOMIC_RELAXED);
    char path[1024], tmp[1100], nm[256];
    snprintf(path, sizeof path, "%s/profile_session.txt", diag_dir);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) { pthread_mutex_unlock(&sess_mu); return; }
    fprintf(f, "session CPU profile of the game thread: one stack every %d ms while it draws\n"
               "%llu samples over %.0f s up to frame %llu (%llu skipped while idle on purpose, %llu not answered)\n",
            sess_period_ms(), (unsigned long long)n, t0 && now > t0 ? (now - t0) / 1e9 : 0.0,
            (unsigned long long)__atomic_load_n(&frame_now, __ATOMIC_ACQUIRE), (unsigned long long)idle,
            (unsigned long long)missed);
    double div = n ? (double)n : 1.0;
    qsort(fns, (size_t)nf, sizeof *fns, cmp_incl);
    fprintf(f, "\n-- inclusive (samples with the function on the stack) --\n");
    for (int k = 0; k < nf && k < 120; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%8d %5.1f%%  %s\n", fns[k].incl, 100.0 * fns[k].incl / div, nm); }
    qsort(fns, (size_t)nf, sizeof *fns, cmp_self);
    fprintf(f, "\n-- self (innermost function) --\n");
    for (int k = 0; k < nf && k < 60 && fns[k].self; ++k) { prof_name(fns[k].pc, nm, sizeof nm); fprintf(f, "%8d %5.1f%%  %s\n", fns[k].self, 100.0 * fns[k].self / div, nm); }
    fclose(f);
    rename(tmp, path);
    pthread_mutex_unlock(&sess_mu);
}

// Sleeps ~100 ms in 2 ms steps: samples the game thread while its frame is late (profile_N.txt) and every
// BOOT_DIAG_PROFILE_SESSION ms while it draws (profile_session.txt).
static void profile_wait_100ms(void) {
    static int ms = -1, step_no;
    if (ms < 0) ms = getenv("BOOT_DIAG_PROFILE") ? atoi(getenv("BOOT_DIAG_PROFILE")) : 100;
    int sess_steps = sess_period_ms() > 0 ? (sess_period_ms() + 1) / 2 : 0;
    if (ms <= 0 && !sess_steps) { usleep(100000); return; }
    for (int step = 0; step < 50; ++step) {
        usleep(2000);
        uint64_t last = __atomic_load_n(&last_frame_ns, __ATOMIC_ACQUIRE), now = host_ticks();
        if (!__atomic_load_n(&sess_t0, __ATOMIC_RELAXED)) __atomic_store_n(&sess_t0, now, __ATOMIC_RELAXED);
        if (ms > 0 && prof_frame_ns && last != prof_frame_ns) { prof_write(last); prof_frame_ns = 0; prof_n = 0; }   // that frame ended
        int idle = __atomic_load_n(&idle_now, __ATOMIC_ACQUIRE);
        int sess = sess_steps && ++step_no % sess_steps == 0 && have_game_thread && last;
        if (sess && idle) { __atomic_fetch_add(&sess_idle, 1, __ATOMIC_RELAXED); sess = 0; }
        int slow = ms > 0 && have_game_thread && !idle && last && now - last >= (uint64_t)ms * 1000000ull && prof_files < prof_max;
        if (slow && prof_frame_ns != last) { prof_frame_ns = last; prof_frame_no = __atomic_load_n(&frame_now, __ATOMIC_ACQUIRE); prof_n = 0; }
        if (slow && prof_n >= PROF_SAMPLES) slow = 0;
        if (sess_steps && now - __atomic_load_n(&sess_written, __ATOMIC_RELAXED) >= 30000000000ull && sess_samples) diag_profile_session_write();
        if (!slow && !sess) continue;
        void *pcs[PROF_DEPTH];
        int d = prof_sample(pcs, PROF_DEPTH);
        if (d < 1) { __atomic_fetch_add(&sess_missed, sess, __ATOMIC_RELAXED); continue; }
        if (slow) {
            for (int k = 0; k < d; ++k) prof_pcs[prof_n][k] = pcs[k];
            prof_depth[prof_n++] = (uint8_t)d;
        }
        if (sess) sess_add(pcs, d);
    }
}
