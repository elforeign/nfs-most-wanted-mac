// Native translated archive proof, fake 4 GB guest memory; no speed entry/game.
// server_loader_test.py injects the production boot helpers before this harness.
// 2026-10-27: 829/829 checks pass; attach-failure unwind 21/21. 32 rehost
// cycles keep handles 1->1; omitted release grows 2->34 (+32), 2 failures.
// BSS/stop/relocation/IAT/entry/byte-order/fdset/FILETIME/notify/drain/release
// fail 1/1/1/1/2/4/1/3/1/2/2 checks, each exiting 1 (driver requires it).
#include "server/server_tables.h"
int diag_on;
const char *diag_dir;
uint64_t diag_idle_ns[IDLE_N];
volatile uint64_t perf_nframes;
const uint32_t guest_func_addr[] = {0}, speed_entry = 0;
void (*const guest_func_ptr[])(Cpu *) = {NULL};
const unsigned guest_func_count = 0, speed_import_count = 0;
const struct ImportSlot speed_imports[] = {{0, NULL}};
void diag_event(const char *fmt, ...) { (void)fmt; }
void diag_call_ring(const char *fn, int n, const uint32_t *a, uint32_t v, uint32_t ra) { (void)fn; (void)n; (void)a; (void)v; (void)ra; }
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; if (off) *off = 0; return ""; }
void diag_report(const char *f, const char *why) { (void)f; (void)why; }
void app_fatal(const char *why) { fprintf(stderr, "fatal: %s\n", why); exit(2); }
// Platform subsystems are outside this native DLL/CRT proof. Unexpected calls
// fail explicitly; asset overlays decline ordinary PC files as in production.
static void unexpected(const char *name) { fprintf(stderr, "unexpected subsystem: %s\n", name); exit(2); }
#define NO_SUBSYSTEM(name) int name(Cpu *c, const char *fn) { (void)c; (void)fn; unexpected(#name); return 0; }
NO_SUBSYSTEM(d3dx)
NO_SUBSYSTEM(d3dx_effect)
NO_SUBSYSTEM(win32_d3d9)
NO_SUBSYSTEM(win32_dinput)
NO_SUBSYSTEM(win32_dsound)
NO_SUBSYSTEM(win32_user32)
void interp_run(Cpu *c, uint32_t t) { (void)c; (void)t; unexpected("interp_run"); }
int render_dump_active(void) { return 0; }
void render_dump_call(const char *s) { (void)s; unexpected("render_dump_call"); }
void app_restart_game_exited(int ok) { (void)ok; unexpected("ExitProcess"); }
int x360_arrest_path(const char *g, char *h, size_t n) { (void)g; (void)h; (void)n; return 0; }
int car85_path(const char *g, char *h, size_t n) { (void)g; (void)h; (void)n; return 0; }
int x360audio_open(const char *w, const char *h) { (void)w; (void)h; return -1; }
int x360music_open(const char *w, X360MusicHandle *h) { (void)w; (void)h; return 0; }
int x360copspeech_open(const char *w, X360MusicHandle *h) { (void)w; (void)h; return 0; }
int x360nisaudio_open(const char *w, X360MusicHandle *h) { (void)w; (void)h; return 0; }
size_t x360music_read(X360MusicHandle *h, void *p, size_t n) { (void)h; (void)p; (void)n; unexpected("x360music_read"); return 0; }
int64_t x360music_seek(X360MusicHandle *h, int64_t d, unsigned m) { (void)h; (void)d; (void)m; unexpected("x360music_seek"); return 0; }
static unsigned checks, failures;
unsigned test_handle_count(void);
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static const char *dll_path;
static unsigned relocated, patched, attaches, detaches, thread_attaches, thread_detaches, stops;
static void before_dispatch(Cpu *c, uint32_t t) {
    if (t == SERVER_BASE + 0x1000) ++stops;
    if (t != server_entry_addr) return;
    if (M32(c->esp + 8) == 2) { ++thread_attaches; return; }
    if (M32(c->esp + 8) == 3) { ++thread_detaches; return; }
    if (M32(c->esp + 8) == 0) { ++detaches; return; }
    if (M32(c->esp + 8) != 1) return;
    ++attaches;
    FILE *file = fopen(dll_path, "rb"); CHECK(file != NULL);
    uint8_t *raw = malloc(server_file_size); CHECK(fread(raw, 1, server_file_size, file) == server_file_size); fclose(file);
    uint32_t pe = *(uint32_t *)(raw + 0x3c), sh = pe + 24 + *(uint16_t *)(raw + pe + 20);
    unsigned zero_bss = 1;
    for (unsigned j = 0; j < *(uint16_t *)(raw + pe + 6); ++j) {
        uint8_t *s = raw + sh + 40*j;
        uint32_t vs = *(uint32_t *)(s+8), va = *(uint32_t *)(s+12), rs = *(uint32_t *)(s+16);
        for (uint32_t k = rs; k < vs; ++k) zero_bss &= M8(SERVER_BASE + va + k) == 0;
    }
    CHECK(zero_bss);
    relocated = 0;
    for (unsigned i = 0; i < server_relocation_count; ++i) {
        uint32_t rva = server_relocation_rva[i], original = 0;
        for (unsigned j = 0; j < *(uint16_t *)(raw + pe + 6); ++j) {
            uint8_t *s = raw + sh + 40*j;
            uint32_t va = *(uint32_t *)(s+12), n = *(uint32_t *)(s+16);
            if (rva >= va && rva + 4 <= va + n) original = *(uint32_t *)(raw + *(uint32_t *)(s+20) + rva-va);
        }
        relocated += M32(SERVER_BASE + rva) == original + SERVER_BASE - server_preferred_base;
    }
    free(raw);
    CHECK(relocated == 5287);
    patched = 0;
    for (unsigned i = 0; i < server_import_count; ++i) patched += M32(server_imports[i].slot) == SERVER_THUNK_BASE + 4*i;
    CHECK(patched == 129);
    if (failures) { fprintf(stderr, "pre-entry: %u checks, %u failures, relocations %u/5287, IAT %u/129\n", checks, failures, relocated, patched); exit(1); }
}
static uint32_t api(Cpu *c, const char *fn, unsigned n, const uint32_t *a) {
    uint32_t sp = c->esp;
    for (unsigned i = n; i; --i) { c->esp -= 4; M32(c->esp) = a[i - 1]; }
    c->esp -= 4; M32(c->esp) = 0x12345678;
    CHECK(win32_threads(c, fn) || win32(c, fn));
    CHECK(c->esp == sp);
    return c->eax;
}
static uint32_t ordinal(Cpu *c, const char *name, unsigned n, const uint32_t *a) {
    for (unsigned i = 0; i < server_import_count; ++i) if (!strcmp(server_imports[i].name, name)) {
        uint32_t sp = c->esp;
        for (unsigned j = n; j; --j) { c->esp -= 4; M32(c->esp) = a[j - 1]; }
        c->esp -= 4; M32(c->esp) = 0x12345678;
        dispatch(c, M32(server_imports[i].slot));
        CHECK(c->esp == sp);
        return c->eax;
    }
    CHECK(0); return 0;
}
static void shims(Cpu *c, const char *path) {
    CHECK(ordinal(c, "WS2_32.dll!#8", 1, (uint32_t[]){0x12345678}) == 0x78563412);
    CHECK(ordinal(c, "WS2_32.dll!#14", 1, (uint32_t[]){0xdeadbeef}) == 0xefbeadde);
    CHECK(ordinal(c, "WS2_32.dll!#9", 1, (uint32_t[]){0xabcd1234}) == 0x3412);
    CHECK(ordinal(c, "WS2_32.dll!#15", 1, (uint32_t[]){0xffffbeef}) == 0xefbe);
    uint32_t set = galloc(4 + 64*4);
    M32(set) = 64; M32(set + 4 + 63*4) = 0x41fc;
    CHECK(ordinal(c, "WS2_32.dll!#151", 2, (uint32_t[]){0x41fc, set}) == 1);
    CHECK(ordinal(c, "WS2_32.dll!#151", 2, (uint32_t[]){0x5000, set}) == 0);
    CHECK(ordinal(c, "WS2_32.dll!#151", 2, (uint32_t[]){0x41fc, 0}) == 0);
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600); CHECK(fd >= 0);
    struct timespec times[] = {{1700000000, 123456700}, {1700000100, 987654300}};
    CHECK(futimens(fd, times) == 0);
    close(fd);
    uint32_t handle = api(c, "CreateFileA", 7, (uint32_t[]){gstr("SERVER.CFG"), 0x80000000, 3, 0, 3, 0, 0});
    CHECK(handle != 0xffffffff);
    fd = handle_fd(handle);
    uint32_t out = galloc(24);
    CHECK(api(c, "GetFileTime", 4, (uint32_t[]){handle, out, out+8, out+16}) == 1);
    struct stat st; CHECK(fstat(fd, &st) == 0);
    struct timespec expected[] = {st.st_birthtimespec, times[0], times[1]};
    for (unsigned i = 0; i < 3; ++i) {
        uint64_t ft = (uint64_t)(expected[i].tv_sec + 11644473600ll)*10000000 + expected[i].tv_nsec/100;
        CHECK(M32(out + 8*i) == (uint32_t)ft && M32(out + 8*i + 4) == ft >> 32);
    }
    CHECK(api(c, "GetFileTime", 4, (uint32_t[]){handle, 0, 0, 0}) == 1);
    M32(out) = 0xcafebabe;
    CHECK(api(c, "GetFileTime", 4, (uint32_t[]){0xffffffff, out, 0, 0}) == 0 && last_error == 6 && M32(out) == 0xcafebabe);
    CHECK(api(c, "CloseHandle", 1, (uint32_t[]){handle}) == 1);
    handle = api(c, "CreateFileA", 7, (uint32_t[]){gstr("C:\\NFSMW\\LOCALIZE.CSV"), 0x80000000, 3, 0, 3, 0, 0});
    CHECK(handle != 0xffffffff);
    CHECK(api(c, "ReadFile", 5, (uint32_t[]){handle, out, 16, out+20, 0}) == 1);
    CHECK(M32(out+20) == 9 && !memcmp(G+out, "LAN test\n", 9));
    CHECK(api(c, "CloseHandle", 1, (uint32_t[]){handle}) == 1);
    CHECK(api(c, "VirtualAlloc", 4, (uint32_t[]){SERVER_BASE - 0x1000, 0x2000, 0x3000, 4}) == 0);
    CHECK(api(c, "VirtualAlloc", 4, (uint32_t[]){SERVER_BASE, 0x1000, 0x3000, 4}) == 0);
}
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    game_dir = argv[1];
    dll_path = argv[2];
    G = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (G == MAP_FAILED) return 2;
    Cpu cpu = {.esp = STACK_TOP - 0x100, .fs_base = TEB, .cw = 0x27f}, *c = &cpu;
    M32(TEB) = 0xffffffff; M32(TEB + 0x18) = TEB; M32(TEB + 0x30) = PEB;
    threads_init();
    threads_set_main_cpu(c);
    boot_trace = getenv("SERVER_TEST_TRACE") != NULL;
    CHECK(server_library_name("SeRvEr.DlL") && server_library_name("SERVER"));
    CHECK(!server_library_name("other.dll") && !server_library_name("server.dll.bad"));
    CHECK(server_handle() == 0);
    CHECK(server_load(c, "/nonexistent/server.dll") == 0 && last_error == 126);
    CHECK(server_load(c, argv[4]) == 0 && last_error == 193);
    CHECK(server_handle() == 0 && M32(SERVER_BASE) == 0);
    CHECK(api(c, "LoadLibraryA", 1, (uint32_t[]){gstr("other.dll")}) == 0);
    memset(G + SERVER_BASE, 0xcc, SERVER_RESERVED_END - SERVER_BASE);
    uint32_t h = api(c, "LoadLibraryA", 1, (uint32_t[]){gstr("SeRvEr.DlL")});
#ifdef EXPECT_ATTACH_FAILURE
    CHECK(h == 0 && last_error == 1114);
    CHECK(server_handle() == 0 && M32(SERVER_BASE) == 0);
    CHECK(!server_dispatch(c, SERVER_BASE + 0x10b0));
    CHECK(!server_import(server_imports[0].slot, NULL));
    CHECK(attaches == 1 && detaches == 1);
    printf("attach failure unwind: %u checks, %u failures\n", checks, failures);
    gil_release();
    return failures != 0;
#endif
    CHECK(h == SERVER_BASE && server_handle() == h);
    if (!h) return 1;
    // CRT's process-attach counter: this is set by the real PE entry, never by
    // mapping alone. Check before attempting unload to make the negative safe.
    CHECK(attaches == 1);
    CHECK(M32(SERVER_BASE + 0x68e04) == 1);
    if (M32(SERVER_BASE + 0x68e04) != 1) return 1;
    CHECK(c->esp == STACK_TOP - 0x100);
    CHECK(api(c, "GetModuleHandleA", 1, (uint32_t[]){gstr("SERVER.DLL")}) == h);
    uint32_t module_path = galloc(64);
    CHECK(api(c, "GetModuleFileNameA", 3, (uint32_t[]){h, module_path, 64}) == strlen("C:\\NFSMW\\server.dll"));
    CHECK(!strcmp((char *)G + module_path, "C:\\NFSMW\\server.dll"));
    CHECK(api(c, "GetModuleFileNameA", 3, (uint32_t[]){0, module_path, 64}) == strlen("C:\\NFSMW\\speed.exe"));
    CHECK(!strcmp((char *)G + module_path, "C:\\NFSMW\\speed.exe"));
    for (unsigned i = 0; i < server_export_count; ++i) {
        const ServerExport *e = &server_exports[i];
        CHECK(api(c, "GetProcAddress", 2, (uint32_t[]){h, gstr(e->name)}) == e->address);
        CHECK(api(c, "GetProcAddress", 2, (uint32_t[]){h, e->ordinal}) == e->address);
    }
    CHECK(api(c, "GetProcAddress", 2, (uint32_t[]){h, gstr("missing")}) == 0 && last_error == 127);
    CHECK(api(c, "GetProcAddress", 2, (uint32_t[]){h, 999}) == 0);
    CHECK(call_guest(c, server_proc(h, 1), 0, NULL) == 0); // IsServerRunning before StartServer
    shims(c, argv[3]);
    uint32_t thread = api(c, "CreateThread", 6, (uint32_t[]){0, 0, server_proc(h, 1), 0, 0, 0});
    CHECK(thread != 0);
    CHECK(api(c, "WaitForSingleObject", 2, (uint32_t[]){thread, 5000}) == 0);
    CHECK(thread_attaches == 1 && thread_detaches == 1);
    CHECK(api(c, "CloseHandle", 1, (uint32_t[]){thread}) == 1);
    uint32_t suspended = api(c, "CreateThread", 6, (uint32_t[]){0, 0, server_proc(h, 1), 0, 4, 0});
    CHECK(suspended != 0);
    CHECK(api(c, "LoadLibraryA", 1, (uint32_t[]){gstr("server")}) == h);
    CHECK(api(c, "FreeLibrary", 1, (uint32_t[]){h}) == 1 && server_handle() == h);
    CHECK(api(c, "FreeLibrary", 1, (uint32_t[]){h}) == 1 && server_handle() == 0);
    CHECK(detaches == 1 && stops == 1);
    unsigned drain_released = api(c, "WaitForSingleObject", 2, (uint32_t[]){suspended, 0}) == 0xffffffff;
#ifndef TEST_REHOST_HANDLES
    CHECK(drain_released);
    CHECK(api(c, "CloseHandle", 1, (uint32_t[]){suspended}) == 0);
#endif
    CHECK(M32(SERVER_BASE) == 0 && !server_dispatch(c, SERVER_BASE + 0x10b0));
    CHECK(!server_import(server_imports[0].slot, NULL));
    CHECK(api(c, "FreeLibrary", 1, (uint32_t[]){h}) == 0);
    CHECK(api(c, "LoadLibraryA", 1, (uint32_t[]){gstr("SERVER")}) == h);
    CHECK(call_guest(c, server_proc(h, 1), 0, NULL) == 0);
    CHECK(api(c, "FreeLibrary", 1, (uint32_t[]){h}) == 1);
    CHECK(attaches == 2 && detaches == 2 && stops == 2);
#ifdef TEST_REHOST_HANDLES
    // Simulate the server's thread slot, using a harmless translated DLL entry
    // instead of StartServer (which opens sockets). Real CreateThread,
    // StopServer, FreeLibrary, DLL notifications and allocator run unchanged.
    uint32_t outside = api(c, "CreateEventA", 4, (uint32_t[]){0, 1, 1, 0});
    unsigned baseline = test_handle_count();
    unsigned nonflat = 0;
    for (unsigned i = 0; i < 32; ++i) {
        h = api(c, "LoadLibraryA", 1, (uint32_t[]){gstr("server")});
        CHECK(h == SERVER_BASE);
        thread = api(c, "CreateThread", 6, (uint32_t[]){0, 0, server_proc(h, 1), 0, 0, 0});
        CHECK(thread != 0);
        CHECK(api(c, "WaitForSingleObject", 2, (uint32_t[]){thread, 5000}) == 0);
        // StopServer clears this finished thread slot without CloseHandle.
        // The suspended thread above separately exercises deferred drain.
        M32(0x02058368u) = thread;
        CHECK(api(c, "FreeLibrary", 1, (uint32_t[]){h}) == 1 && server_handle() == 0);
        CHECK(M32(0x02058368u) == 0);
        nonflat += test_handle_count() != baseline;
        CHECK(api(c, "WaitForSingleObject", 2, (uint32_t[]){outside, 0}) == 0);
    }
    printf("rehost handles: 32 finished-thread cycles, before=%u after=%u growth=%u\n",
           baseline, test_handle_count(), test_handle_count()-baseline);
    CHECK(nonflat == 0);
    CHECK(drain_released);
    CHECK(api(c, "CloseHandle", 1, (uint32_t[]){outside}) == 1);
#endif
    printf("server loader: %u checks, %u failures, relocations %u/5287, IAT %u/129\n", checks, failures, relocated, patched);
    gil_release();
    return failures ? 1 : 0;
}
