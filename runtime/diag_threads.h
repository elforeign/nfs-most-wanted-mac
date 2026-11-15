// Shared host backtraces for diagnostic reports and frame sampling.
static FILE *report_f;
static volatile int bt_done;
static void write_backtrace(FILE *f) {
    void *pcs[96];
    int n = backtrace(pcs, 96);
    char **s = backtrace_symbols(pcs, n);
    for (int i = 0; i < n; ++i) {
        const char *fn = s ? strstr(s[i], "f_00") : NULL;
        if (fn && strlen(fn) >= 10) {
            uint32_t a = (uint32_t)strtoul(fn + 2, NULL, 16), off = 0;
            const char *name = diag_symbol(a, &off);
            fprintf(f, "  %s   [%s]\n", s[i], name ? name : "?");
        } else {
            fprintf(f, "  %s\n", s ? s[i] : "?");
        }
    }
    free(s);
}
static volatile int sample_mode;          // SIGUSR2 from the slow-frame profiler: record the stack, write nothing
static void *sample_pcs[64];
static volatile int sample_n;
static void bt_signal(int sig) {
    (void)sig;
    if (sample_mode) { sample_n = backtrace(sample_pcs, 64); bt_done = 1; return; }
    if (report_f) { fprintf(report_f, "\n-- host thread %p --\n", (void *)pthread_self()); write_backtrace(report_f); fflush(report_f); }
    bt_done = 1;
}
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap);   // threads.c


// Capture the same host threads in both report formats; guest state is image-specific.
static int report_thread_backtraces(Cpu **cpus, uint32_t *tids) {
    pthread_t ths[64];
    int n = threads_list(ths, cpus, tids, 64);
    signal(SIGUSR2, bt_signal);
    for (int i = 0; i < n; ++i) {
        if (pthread_equal(ths[i], pthread_self())) continue;
        bt_done = 0;
        if (pthread_kill(ths[i], SIGUSR2) == 0)
            for (int k = 0; k < 200 && !bt_done; ++k) usleep(5000);
    }
    return n;
}
