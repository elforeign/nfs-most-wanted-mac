// boot.c - first native boot of the recompiled speed.exe (port/recomp/out/speed.a).
//
// Loads the PE image into guest memory at its own addresses, builds a TEB (fs:) and a stack, points every IAT slot
// at a synthetic thunk address, and calls the entry point's translated C. Imports are served by a small Win32
// subset (the C runtime's start-up needs); every import call is logged, and the first unimplemented one stops the
// run with its name, which is the to-do list for the host layer.
//
//   ./boot "/path/to/Need For Speed Most Wanted Black Edition"
#include <ctype.h>
#include "durable.h"
#include <pthread.h>
#include <stdint.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <time.h>
#include <mach/mach_time.h>
#include <sys/time.h>

#include "funcs.h"
#include "host.h"
#include "private_cars.h"
#include "settings.h"
#include "server_loader.h"
#include "x360audio.h"
#include "x360copspeech.h"

uint8_t *G;
extern const uint32_t guest_func_addr[];
extern void (*const guest_func_ptr[])(Cpu *);
extern const unsigned guest_func_count;
extern const uint32_t speed_entry;
extern const struct ImportSlot { uint32_t slot; const char *name; } speed_imports[];
extern const unsigned speed_import_count;

#define TEB 0x7FFDE000u
#define PEB 0x7FFDF000u
#define STACK_TOP 0x7FF00000u
#define HEAP_BASE 0x30000000u
static uint32_t heap_next = HEAP_BASE;
static Cpu *g_cpu;
#include <signal.h>
#include <unistd.h>
static void watchdog(int sig) {
    // Guest call stack: return addresses into .text found on the guest stack (innermost first).
    (void)sig;
    fprintf(stderr, "boot: watchdog after BOOT_TIMEOUT s; guest esp %08X eip-ish return addresses:\n", g_cpu->esp);
    int shown = 0;
    for (uint32_t a = g_cpu->esp; a < STACK_TOP && shown < 16; a += 4) {
        uint32_t v = M32(a);
        if (v > 0x401000 && v < 0x88F2A5 && (M8(v - 5) == 0xE8 || M8(v - 6) == 0xFF || M8(v - 2) == 0xFF || M8(v - 3) == 0xFF)) {
            fprintf(stderr, "  [%08X] %08X\n", a, v);
            ++shown;
        }
    }
    _exit(3);
}
static unsigned import_calls;

void diag_report(const char *file, const char *why);
static void die(const char *fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    fprintf(stderr, "boot: %s\n", msg);
    diag_report("crash.txt", msg);
    app_fatal(msg);
    exit(1);
}

// ---------------- guest helpers ----------------
uint32_t galloc_used(void) { return heap_next - HEAP_BASE; }
void heap_note(int kind, void *ret, uint32_t n);   // heap.c: BOOT_DIAG allocation call sites
uint32_t galloc(uint32_t n) {
    heap_note(0, __builtin_return_address(0), n);
    uint32_t a = (heap_next + 15) & ~15u;
    if (a + n + 32 >= 0x50000000u) die("guest bump heap full (%u MB used): something allocates per call with galloc", (heap_next - HEAP_BASE) >> 20);
    heap_next = a + ((n + 15) & ~15u) + 16;
    M32(a - 4 + 16) = n;                                                        // size kept before the block
    memset(G + a + 16, 0, n);
    return a + 16;
}
// gmalloc/gfree (freeable guest memory for resources) and the Win32 heap functions are in heap.c.
uint32_t gstr(const char *s) {
    uint32_t a = galloc((uint32_t)strlen(s) + 1);
    strcpy((char *)G + a, s);
    return a;
}

void untranslated(Cpu *c, uint32_t addr) {
    die("reached untranslated function %08X (after %u import calls)", addr, import_calls);
}
// x86 raises #DE here; the port keeps running with the quotient/remainder an ARM divide gives (q 0, r n
                                                                                                           
// than fidelity in a case retail play shouldn't reach; the log line says if it ever does.
// True the first time a site is seen (at most 256 sites are remembered; later ones are never reported).
static int first_at_site(uint32_t *seen, uint32_t at) {
    for (unsigned k = 0; k < 256; k++) {
        uint32_t v = __atomic_load_n(&seen[k], __ATOMIC_RELAXED);
        if (v == at) return 0;
        if (v == 0) {
            uint32_t z = 0;
            if (!__atomic_compare_exchange_n(&seen[k], &z, at, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                if (z == at) return 0;
                continue;
            }
            return 1;
        }
    }
    return 0;
}
void guest_divide_error(Cpu *c, uint32_t at) {
    static uint32_t seen[256];
    if (!first_at_site(seen, at)) return;
    fprintf(stderr, "boot: guest divide error (#DE) at %08X (eax %08X, edx %08X): continuing with the ARM result\n",
            at, c->eax, c->edx);
    diag_event("divide_error at=%08X eax=%08X edx=%08X", at, c->eax, c->edx);
}
// int3 (a failed assert, __break 0045CD20, SpeedScript errors): continued like under a debugger (see recomp.py int3).
void guest_breakpoint(Cpu *c, uint32_t at) {
    static uint32_t seen[256];
    if (diag_on && first_at_site(seen, at)) diag_event("int3 at=%08X esp0=%08X", at, M32(c->esp));   // nothing when diag is off
}
// BEGIN audit3 Lua unwind helpers (compiled verbatim by difftest --audit-native).
// The guest _setjmp still writes its original buffer. Only Lua's known protected
// continuation is intercepted after the original CRT longjmp has completed.
#include <setjmp.h>
typedef struct LuaProtectedFrame {
    struct LuaProtectedFrame *prev;
    Cpu *cpu;
    uint32_t buffer;
    jmp_buf env;
} LuaProtectedFrame;
static _Thread_local LuaProtectedFrame *lua_protected_frame;
void host_lua_protected(Cpu *c, void (*body)(Cpu *), void (*resume)(Cpu *)) {
    LuaProtectedFrame frame = { .prev = lua_protected_frame, .cpu = c,
                                .buffer = c->esp - 0x48u };
    lua_protected_frame = &frame;
    if (_setjmp(frame.env) == 0) body(c);   // _setjmp: no signal-mask syscall per protected call
    else { ABI_SKIP(); resume(c); }
    lua_protected_frame = frame.prev;
}
void host_lua_resume(Cpu *c, uint32_t target) {
    if (target != 0x006069E8u) return;
    for (LuaProtectedFrame *p = lua_protected_frame; p; p = p->prev) {
        if (p->cpu != c || p->buffer != c->edx || c->esp != M32(p->buffer + 16) + 4) continue;
        // The original CRT longjmp has restored guest registers and run its SEH
        // cleanup / _NLG_Notify. Now discard abandoned native call frames.
        lua_protected_frame = p;
        _longjmp(p->env, 1);
    }
}
// END audit3 Lua unwind helpers.
void bad_jump(Cpu *c, uint32_t at) { die("jump table index out of range at %08X", at); }
void host_cpuid(Cpu *c) {
    // A generic Pentium 4 class CPU: SSE/SSE2, no vendor-specific paths.
    switch (c->eax) {
    case 0: c->eax = 1; c->ebx = 0x756E6547; c->edx = 0x49656E69; c->ecx = 0x6C65746E; break;   // "GenuineIntel"
    case 1: c->eax = 0x00000F29; c->ebx = 0; c->ecx = 0; c->edx = 0x0080FBFF; break;   // FPU, TSC, CX8, CMOV, MMX (VP6 needs it); no SSE: the translator does MMX only
    default: c->eax = c->ebx = c->ecx = c->edx = 0;
    }
}
// Nanoseconds of a monotonic clock. mach_absolute_time reads the commpage directly (clock_gettime went through
// gettimeofday and boot-time arithmetic, ~1 % of a race frame with the game's timer queries).
uint64_t host_ticks(void) {
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info(&tb);
    uint64_t t = mach_absolute_time();
    return tb.numer == tb.denom ? t : t / tb.denom * tb.numer + t % tb.denom * tb.numer / tb.denom;
}
// Wall-clock time as a Windows FILETIME (100 ns since 1601), advancing with host_ticks so that absolute waitable
// timer due times (the game's 100 Hz TIMER thread) convert exactly.
uint64_t host_filetime(void) {
    static uint64_t ft0, t0;
    if (!ft0) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        t0 = host_ticks();
        ft0 = (uint64_t)tv.tv_sec * 10000000ull + (uint64_t)tv.tv_usec * 10ull + 116444736000000000ull;
    }
    return ft0 + (host_ticks() - t0) / 100;
}

int code_region_of(uint32_t t);
void interp_run(Cpu *c, uint32_t entry);
void host_import(Cpu *c, uint32_t slot);
static HostFn host_fns[4096];
static const char *host_fn_names[4096];
static unsigned host_fn_count;
uint32_t host_fn(HostFn fn, const char *name) {
    if (host_fn_count == 4096) die("too many host functions");
    host_fns[host_fn_count] = fn;
    host_fn_names[host_fn_count] = name;
    return HOSTFN_BASE + 4 * host_fn_count++;
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    uint32_t sp = c->esp;
    for (int i = n - 1; i >= 0; --i) { c->esp -= 4; M32(c->esp) = args[i]; }
    c->esp -= 4;
    M32(c->esp) = 0xFEEDF00D;              // return address (never used: translated code returns to C)
    dispatch(c, fn);
    c->esp = sp;                           // stdcall callees already popped; cdecl ones didn't
    return c->eax;
}
// Calls per import and per host (COM) function, for <diag>/calls.txt: which stubs the game really uses.
static uint64_t import_calls_n[2048], host_fn_calls_n[4096];
// Calls per server.dll import (its IAT is outside speed's thunk table), for BOOT_DIAG_LANSERVER's summary.
const char *server_import_names[256];
uint32_t server_import_calls[256];
void boot_write_call_counts(FILE *f) {
    for (unsigned i = 0; i < speed_import_count && i < 2048; ++i)
        if (import_calls_n[i]) fprintf(f, "%12llu  %s\n", (unsigned long long)import_calls_n[i], speed_imports[i].name);
    for (unsigned i = 0; i < host_fn_count; ++i)
        if (host_fn_calls_n[i]) fprintf(f, "%12llu  %s\n", (unsigned long long)host_fn_calls_n[i], host_fn_names[i]);
}
void dispatch(Cpu *c, uint32_t t) {
    if (t == 0x006069E8u) host_lua_resume(c, t);   // Lua protected-error resume (audit 3 #2); dispatch is hot, so test inline
    if ((t >= SERVER_BASE && t < SERVER_RESERVED_END) || (t >= SERVER_THUNK_BASE && t < SERVER_THUNK_BASE + 4096)) {
        if (server_dispatch(c, t)) return;
        die("call to unavailable server target %08X", t);
    }
    if (t >= THUNK_BASE && t < THUNK_BASE + 4 * speed_import_count) {
        if ((t - THUNK_BASE) / 4 < 2048) __atomic_fetch_add(&import_calls_n[(t - THUNK_BASE) / 4], 1, __ATOMIC_RELAXED);
        host_import(c, speed_imports[(t - THUNK_BASE) / 4].slot);
        return;
    }
    if (t >= HOSTFN_BASE && t < HOSTFN_BASE + 4 * host_fn_count) {
        __atomic_fetch_add(&host_fn_calls_n[(t - HOSTFN_BASE) / 4], 1, __ATOMIC_RELAXED);
        HostFn fn = host_fns[(t - HOSTFN_BASE) / 4];
        const char *hn = host_fn_names[(t - HOSTFN_BASE) / 4];
        if (boot_trace) fprintf(stderr, "  [host] %s  [from %08X, esp %08X]\n", hn, M32(c->esp), c->esp);
        uint32_t ra = M32(c->esp), a[6];
        for (int k = 0; k < 6; ++k) a[k] = M32(c->esp + 4 + 4 * k);
        GilRec *gr = diag_on ? gil_rec_self() : NULL;   // BOOT_DIAG: the host call this thread is in (gilwait.log)
        if (gr) { gr->ra = ra; gr->imp_t0 = host_ticks(); gr->imp = hn; }
        if (!fn(c)) die("not implemented yet: %s (called from %08X, this %08X)", hn, ra, a[0]);
        if (gr) { gr->imp = NULL; gr->imp_t0 = host_ticks(); }
        if (diag_on) {
            // COM calls run at millions per second: ring only, unless BOOT_DIAG_EVENTS=all
            static int all = -1;
            if (all < 0) all = getenv("BOOT_DIAG_EVENTS") && !strcmp(getenv("BOOT_DIAG_EVENTS"), "all");
            static int8_t logged_of[4096];                 // per method: 1 ring only, 2 events.log (creations, resets)
            int8_t *lg = &logged_of[(t - HOSTFN_BASE) / 4];
            if (!*lg) *lg = strstr(hn, "Create") || strstr(hn, "Reset") ? 2 : 1;
            if (all || *lg == 2 || c->eax >= 0x80000000u)
                diag_event("%s(%X, %X, %X, %X, %X, %X) -> %X @%08X", hn, a[0], a[1], a[2], a[3], a[4], a[5], c->eax, ra);
            else
                diag_call_ring(hn, 6, a, c->eax, ra);
            void render_dump_call(const char *line);
            int render_dump_active(void);
            if (render_dump_active()) {
                char line[256];
                snprintf(line, sizeof line, "%s(%X, %X, %X, %X, %X, %X) -> %X @%08X", hn, a[0], a[1], a[2], a[3], a[4], a[5], c->eax, ra);
                render_dump_call(line);
            }
        }
        return;
    }
    // Indirect calls (virtual methods, callbacks) mostly hit a small set of targets: a direct-mapped cache in front
    // of the binary search over all functions. Guest code runs one thread at a time (GIL).
    static struct { uint32_t addr; void (*fn)(Cpu *); } cache[1 << 14];
    uint32_t h = (t >> 4 ^ t >> 17) & ((1 << 14) - 1);
    if (cache[h].addr == t && cache[h].fn) { cache[h].fn(c); return; }
    int lo = 0, hi = (int)guest_func_count - 1;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (guest_func_addr[m] == t) { cache[h].addr = t; cache[h].fn = guest_func_ptr[m]; guest_func_ptr[m](c); return; }
        if (guest_func_addr[m] < t) lo = m + 1; else hi = m - 1;
    }
    if (code_region_of(t) >= 0) { interp_run(c, t); return; }   // code the game loaded at run time
    die("indirect call to %08X, which is not a known function (return address %08X)", t, M32(c->esp));
}

// ---------------- Win32 subset ----------------
uint32_t last_error;
const char *game_dir;
int boot_trace;

// ---- files: Windows paths resolved case-insensitively under the game folder ----
#include <dirent.h>
#include <fcntl.h>
#include <fnmatch.h>
#include <sys/stat.h>
#include <unistd.h>
// Saves, settings (registry.txt) and the shader cache: ~/Library/Application Support/NFSMW-port, or BOOT_USERDATA
// (scripted test runs that create aliases or change settings use a scratch copy, not the player's profile).
const char *userdata_dir(void) {
    static char dir[1024];
    if (!dir[0]) {
        const char *e = getenv("BOOT_USERDATA");
        if (e && *e) snprintf(dir, sizeof dir, "%s", e);
        else snprintf(dir, sizeof dir, "%s/Library/Application Support/NFSMW-port", getenv("HOME") ? getenv("HOME") : "/tmp");
        // with its parents: a missing parent made every save and registry write fail silently (a "first run" each time)
        for (char *p = dir + 1; *p; ++p) if (*p == '/') { *p = 0; mkdir(dir, 0755); *p = '/'; }
        mkdir(dir, 0755);
    }
    return dir;
}
// BEGIN offline save scan: auditfix_test.py compiles these exact cleanup/enumeration helpers.
static int save_temp_name(const char *name) {
    static const char suffix[] = ".nfs-save-";
    size_t n = strlen(name), tail = sizeof suffix - 1 + 6;
    return n >= tail && !memcmp(name + n - tail, suffix, sizeof suffix - 1);
}
// Own fd; use directory-relative operations so neither a directory nor a file symlink is followed.
static void save_cleanup_dir(int fd, const char *path) {
    DIR *d = fdopendir(fd);
    if (!d) { close(fd); return; }
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        struct stat st;
        if (fstatat(fd, e->d_name, &st, AT_SYMLINK_NOFOLLOW)) continue;
        char child[4096];
        if (snprintf(child, sizeof child, "%s/%s", path, e->d_name) >= (int)sizeof child) continue;
        if (S_ISDIR(st.st_mode)) {
            int sub = openat(fd, e->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (sub >= 0) save_cleanup_dir(sub, child);
        } else if (S_ISREG(st.st_mode) && save_temp_name(e->d_name)) {
            if (!unlinkat(fd, e->d_name, 0)) fprintf(stderr, "boot: removed stale save temp %s\n", child);
            else fprintf(stderr, "boot: cannot remove stale save temp %s: %s\n", child, strerror(errno));
        }
    }
    closedir(d);
}
static void save_cleanup(void) {
    const char *root = userdata_dir();
    char path[1024];
    if (snprintf(path, sizeof path, "%s", root) >= (int)sizeof path) return;
    // Check every component, including a symlink supplied as BOOT_USERDATA (also with a trailing slash).
    int fd = open(root[0] == '/' ? "/" : ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return;
    char *next;
    for (char *part = strtok_r(path, "/", &next); part; part = strtok_r(NULL, "/", &next)) {
        int sub = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(fd);
        if (sub < 0) return;
        fd = sub;
    }
    save_cleanup_dir(fd, root);
}
static int file_is_save(const char *path);
static int resolve(const char *win, char *out, size_t n) {
    char rel[1024];
    snprintf(rel, sizeof rel, "%s", win);
    for (char *p = rel; *p; ++p) if (*p == '\\') *p = '/';
    char *r = rel;
    if (strlen(r) > 2 && r[1] == ':') r += 2;                 // drop a drive letter
    while (*r == '/') ++r;
    if (!strncasecmp(r, "NFSMW/", 6)) r += 6;                 // our fake module path C:\NFSMW\...
    if (!strncasecmp(r, "USERDATA", 8) && (r[8] == '/' || !r[8])) {   // SHGetFolderPath: saves, settings
        snprintf(out, n, "%s", userdata_dir());
        r += 8;
        while (*r == '/') ++r;
    } else {
        snprintf(out, n, "%s", game_dir);
    }
    for (char *tok = strtok(r, "/"); tok; tok = strtok(NULL, "/")) {
        if (!strcmp(tok, ".")) continue;
        DIR *d = opendir(out);
        int found = 0;
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) if (!strcasecmp(e->d_name, tok)) { strncat(out, "/", n - strlen(out) - 1); strncat(out, e->d_name, n - strlen(out) - 1); found = 1; break; }
            closedir(d);
        }
        if (!found) { strncat(out, "/", n - strlen(out) - 1); strncat(out, tok, n - strlen(out) - 1); }
    }
    return 0;
}
// Private converter probe: only existing car geometry/texture files, never saves or write opens.
static int test_car_path(const char *guest, char *host, size_t n) {
    const char *root = getenv("NFS_TEST_CAR_DIR");
    if (!root || !*root || !guest) return 0;
    char rel[1024], candidate[4096];
    if (snprintf(rel, sizeof rel, "%s", guest) >= (int)sizeof rel) return 0;
    for (char *p = rel; *p; ++p) if (*p == '\\') *p = '/';
    char *r = rel;
    if (strlen(r) > 2 && r[1] == ':') r += 2;
    while (*r == '/') ++r;
    if (!strncasecmp(r, "NFSMW/", 6)) r += 6;
    if (strncasecmp(r, "CARS/", 5)) return 0;
    char *car = r + 5, *file = strchr(car, '/');
    if (!file || file == car) return 0;
    *file++ = 0;
    for (const char *p = car; *p; ++p)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') ||
              (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) return 0;
    if (strcasecmp(file, "GEOMETRY.BIN") && strcasecmp(file, "TEXTURES.BIN")) return 0;
    if (snprintf(candidate, sizeof candidate, "%s", root) >= (int)sizeof candidate) return 0;
    // Match Windows component case on case-sensitive scratch volumes too.
    const char *parts[] = {car, file};
    for (int k = 0; k < 2; ++k) {
        DIR *dir = opendir(candidate);
        if (!dir) return 0;
        struct dirent *e;
        int found = 0;
        while ((e = readdir(dir))) if (!strcasecmp(e->d_name, parts[k])) {
            size_t len = strlen(candidate);
            if (snprintf(candidate + len, sizeof candidate - len, "/%s", e->d_name) <
                (int)(sizeof candidate - len)) found = 1;
            break;
        }
        closedir(dir);
        if (!found) return 0;
    }
    struct stat st;
    if (stat(candidate, &st) || !S_ISREG(st.st_mode) || strlen(candidate) >= n) return 0;
    snprintf(host, n, "%s", candidate);
    if (diag_on) {
        diag_event("test car redirect: %s -> %s", guest, host);
        fprintf(stderr, "boot: test car redirect: %s -> %s\n", guest, host);
    }
    return 1;
}
// FindFirstFileA / FindNextFileA: a directory listing with Windows wildcards (case-insensitive; "*.*" is everything),
// e.g. the save folder's aliases at start-up.
// Handle ranges (each import routes by range, so they must not overlap): file handles HANDLE_BASE 0x1000 + 4*256
// (to 0x1400), kernel objects KOBJ_BASE 0x2000 + 4*MAX_KOBJ(512) (threads.c, to 0x2800), find handles 0x2800 + 4*32
// (to 0x2880), registry keys REG_HKEY_BASE 0x3000. Windows (user32.m HWND_BASE 0x10010) and the D3D/DSound/DInput COM
// objects (host.h HOSTFN_BASE, d3dx_effect.c HANDLE_BASE 0xF2000000) are far above these.
#define FIND_BASE 0x2800u
static struct { DIR *d; char dir[1024]; char pat[260]; } finds[32];
static int find_next(uint32_t k, uint32_t data) {         // fills WIN32_FIND_DATAA (320 bytes) with the next match
    struct dirent *e;
    while ((e = readdir(finds[k].d))) {
        // Internal stages must not become aliases/save candidates, including while their writer is open.
        char p[2048];
        struct stat st;
        snprintf(p, sizeof p, "%s/%s", finds[k].dir, e->d_name);
        if (file_is_save(p) && save_temp_name(e->d_name)) continue;
        if (fnmatch(finds[k].pat, e->d_name, FNM_CASEFOLD) || stat(p, &st)) continue;
        memset(G + data, 0, 320);
        M32(data) = S_ISDIR(st.st_mode) ? 0x10 : 0x20;                          // DIRECTORY / ARCHIVE
        uint64_t ft = ((uint64_t)st.st_mtime + 11644473600ull) * 10000000ull;  // FILETIME
        M64(data + 4) = ft; M64(data + 12) = ft; M64(data + 20) = ft;
        M32(data + 28) = (uint32_t)((uint64_t)st.st_size >> 32); M32(data + 32) = (uint32_t)st.st_size;
        snprintf((char *)G + data + 44, 260, "%s", e->d_name);
        return 1;
    }
    return 0;
}
static uint32_t find_first(const char *win, uint32_t data) {
    char dir[1024], host[1024];
    snprintf(dir, sizeof dir, "%s", win);
    char *slash = strrchr(dir, '\\') > strrchr(dir, '/') ? strrchr(dir, '\\') : strrchr(dir, '/');
    const char *pat = slash ? slash + 1 : dir;
    uint32_t k = 0;
    while (k < 32 && finds[k].d) ++k;
    if (k == 32) { last_error = 4; return 0xFFFFFFFF; }               // ERROR_TOO_MANY_OPEN_FILES
    snprintf(finds[k].pat, sizeof finds[k].pat, "%s", strcmp(pat, "*.*") ? pat : "*");
    if (slash) *slash = 0; else dir[0] = 0;
    resolve(dir, host, sizeof host);
    if (!(finds[k].d = opendir(host))) { last_error = 3; return 0xFFFFFFFF; }   // ERROR_PATH_NOT_FOUND
    snprintf(finds[k].dir, sizeof finds[k].dir, "%s", host);
    if (find_next(k, data)) return FIND_BASE + 4 * k;
    closedir(finds[k].d); finds[k].d = NULL;
    last_error = 2;                                                    // ERROR_FILE_NOT_FOUND
    return 0xFFFFFFFF;
}
static int find_handle(uint32_t h) { uint32_t k = (h - FIND_BASE) / 4; return h >= FIND_BASE && k < 32 && finds[k].d ? (int)k : -1; }
// END offline save scan
// One resolver, always using the requested identity. Donor fallback from an
// addition returns immediately, so a donor's own-slot swap cannot leak into it.
static int private_car_path(const char *guest, char *host, size_t n) {
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    return 0;
#else
    if (car85_path(guest,host,n)) return 1;
    char rel[1024];
    if (!guest || snprintf(rel,sizeof rel,"%s",guest)>=(int)sizeof rel) return 0;
    for (char *p=rel;*p;++p) if (*p=='\\') *p='/';
    char *r=rel;
    if (strlen(r)>2 && r[1]==':') r+=2;
    while (*r=='/') ++r;
    if (!strncasecmp(r,"NFSMW/",6)) r+=6;
    if (strncasecmp(r,"CARS/",5)) return 0;
    char *name=r+5, *file=strchr(name,'/');
    if (!file) return 0;
    *file++=0;
    int geometry=!strcasecmp(file,"GEOMETRY.BIN");
    if (!geometry && strcasecmp(file,"TEXTURES.BIN")) return 0;
    for (size_t i=0;i<private_cars_count();++i) {
        const PrivateCar *car=private_cars_get(i);
        if (!car->swap || strcasecmp(name,car->name)) continue;
        const char *path=geometry?car->geometry:car->textures;
        if (strlen(path)>=n) return 0;
        snprintf(host,n,"%s",path);
        return 1;
    }
    return test_car_path(guest,host,n);
#endif
}


#define HANDLE_BASE 0x1000u
// BEGIN offline file layer: auditfix_test.py compiles these exact helpers in a scratch-only C harness.
// Interrupted replacement: old 8 bytes survive; in-place negative: old bytes lost. Completed close replaces.
static int fds[256];
static int handle_fd(uint32_t h) { uint32_t k = (h - HANDLE_BASE) / 4; return (h >= HANDLE_BASE && k < 256 && fds[k] > 0) ? fds[k] - 1 : -1; }   // -1: an in-memory X360 audio handle
typedef struct {
    char dest[4096], temp[4096];
    unsigned refs, writers;
    int failed;
} SaveFile;
// Bumped whenever a write/flush/close/rename of a staged save fails. The game's save task (007f4f11) ignores __close's
// result and clears its own error state right after the writes, so the hook below reads this to report the failure.
static unsigned save_fail_seq;
static unsigned save_commit_seq;
static struct { char *path; uint32_t access, share; SaveFile *save; } file_info[256];

static int file_is_save(const char *path) {
    const char *root = userdata_dir();
    size_t n = strlen(root);
    while (n && root[n - 1] == '/') --n;
    return !strncmp(path, root, n) && path[n] == '/' && path[n + 1];
}
static void file_error(void) {
    last_error = errno == EBUSY ? 32 : errno == EEXIST ? 80 : errno == ENOSPC ? 112 :
                 errno == EBADF ? 6 : errno == EMFILE ? 4 : errno == ENOENT ? 2 : 5;
}
static int file_flags(uint32_t access, uint32_t disp) {
    int fl = (access & 0x40000000) ? ((access & 0x80000000) ? O_RDWR : O_WRONLY) : O_RDONLY;
    if (disp == 1 || disp == 2 || disp == 4) fl |= O_CREAT;
    if (disp == 1) fl |= O_EXCL;
    if (disp == 2 || disp == 5) fl |= O_TRUNC;
    return fl;
}
static int file_copy(int src, int dst) {
    char buf[16384];
    ssize_t n;
    while ((n = read(src, buf, sizeof buf)) != 0) {
        if (n < 0) { if (errno == EINTR) continue; return -1; }
        for (ssize_t off = 0; off < n;) {
            ssize_t w = write(dst, buf + off, (size_t)(n - off));
            if (w < 0 && errno == EINTR) continue;
            if (w <= 0) return -1;
            off += w;
        }
    }
    return 0;
}
static SaveFile *file_stage(const char *path, int fl) {
    struct stat st;
    int exists = !stat(path, &st), src = -1, fd = -1;
    if (!exists && errno != ENOENT) return NULL;
    if (exists && (fl & O_EXCL)) { errno = EEXIST; return NULL; }
    if (!exists && !(fl & O_CREAT)) { errno = ENOENT; return NULL; }
    if (exists && !S_ISREG(st.st_mode)) { errno = EISDIR; return NULL; }
    // Check the destination's write permission without truncating it, even when no copy is needed.
    if (exists) { fd = open(path, O_WRONLY); if (fd < 0) return NULL; close(fd); fd = -1; }
    SaveFile *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    snprintf(s->dest, sizeof s->dest, "%s", path);
    if (snprintf(s->temp, sizeof s->temp, "%s.nfs-save-XXXXXX", path) >= sizeof s->temp) { errno = ENAMETOOLONG; goto bad; }
    fd = mkstemp(s->temp);                         // same directory/filesystem as the destination
    if (fd < 0) goto bad;
    if (fchmod(fd, exists ? st.st_mode & 0777 : 0644)) goto bad;
    if (exists && !(fl & O_TRUNC)) {
        src = open(path, O_RDONLY);
        if (src < 0 || file_copy(src, fd)) goto bad;
        close(src); src = -1;
    }
    if (close(fd)) { fd = -1; goto bad; }
    return s;
bad:;
    int err = errno;
    if (src >= 0) close(src);
    if (fd >= 0) close(fd);
    if (s->temp[0]) unlink(s->temp);
    free(s); errno = err;
    return NULL;
}
// Private extratrax supplies the strong admission latch; public defaults to allow.
__attribute__((weak)) int private_cars_save_allowed(void) { return 1; }
__attribute__((weak)) int private_cars_save_prepare(const char *path) { (void)path; return 1; }
__attribute__((weak)) void private_cars_save_complete(int committed) { (void)committed; }
__attribute__((weak)) int private_cars_backup_before_write(const char *destination, const char *staged) {
    (void)destination; (void)staged; return 1;
}
static int profile_write_denied(const char *path) {
    return !private_cars_save_allowed() && (file_is_save(path) || !strcmp(path, userdata_dir()));
}
static uint32_t file_open(const char *win, const char *host, uint32_t access, uint32_t share, uint32_t disp, uint32_t flags) {
    int k = 1, fd = -1, fresh = 0, fl = file_flags(access, disp);
    if ((fl != O_RDONLY) && profile_write_denied(host)) {
        errno = EACCES; file_error(); return 0xFFFFFFFFu;
    }
    for (; k < 256 && fds[k]; ++k) {}
    if (k == 256) { errno = EMFILE; goto bad; }
    SaveFile *s = NULL;
    char *path = NULL;
    if (file_is_save(host)) {
        for (int i = 1; i < 256; ++i) if (fds[i] && file_info[i].path && !strcmp(file_info[i].path, host)) {
            uint32_t a = file_info[i].access, sh = file_info[i].share;
            if (((access & 0x80000000) && !(sh & 1)) || ((access & 0x40000000) && !(sh & 2)) ||
                ((a & 0x80000000) && !(share & 1)) || ((a & 0x40000000) && !(share & 2))) { errno = EBUSY; goto bad; }
            if (file_info[i].save && file_info[i].save->temp[0]) s = file_info[i].save;
        }
        if (s && (fl & O_EXCL)) { errno = EEXIST; goto bad; }
        if (!s && (access & 0x40000000)) {
            s = file_stage(host, fl);
            if (!s) goto bad;
            fresh = 1;
        }
        path = strdup(host);
        if (!path) goto cleanup;
    }
    fd = !s && fl == O_RDONLY ? x360audio_open(win, host) : -1;
    if (fd < 0) fd = open(s ? s->temp : host, s ? fl & ~(O_CREAT | O_EXCL) : fl, 0644);
    if (fd < 0) goto cleanup;
    struct stat st;
    if (!(flags & 0x02000000) && !fstat(fd, &st) && S_ISDIR(st.st_mode)) { errno = EISDIR; goto cleanup; }
    if (fresh) {
        // Rare FILE_SHARE_WRITE readers opened before the writer must follow the staged inode too.
        // Prepare all descriptors first so an open failure leaves every existing handle unchanged.
        int redirects[256] = {0};
        for (int i = 1; i < 256; ++i) if (fds[i] && file_info[i].path && !strcmp(file_info[i].path, host)) {
            int r = open(s->temp, file_flags(file_info[i].access, 3));
            off_t pos = lseek(fds[i] - 1, 0, SEEK_CUR);
            if (r < 0 || pos < 0 || lseek(r, pos, SEEK_SET) < 0) {
                int err = errno;
                if (r >= 0) close(r);
                for (int j = 1; j < 256; ++j) if (redirects[j]) close(redirects[j] - 1);
                errno = err; goto cleanup;
            }
            redirects[i] = r + 1;
        }
        for (int i = 1; i < 256; ++i) if (redirects[i]) {
            close(fds[i] - 1); fds[i] = redirects[i];
            SaveFile *old = file_info[i].save;
            if (old && !--old->refs) free(old);
            file_info[i].save = s; ++s->refs;
        }
    }
    fds[k] = fd + 1;
    file_info[k].path = path; file_info[k].access = access; file_info[k].share = share; file_info[k].save = s;
    if (s) { ++s->refs; if (access & 0x40000000) ++s->writers; }
    return HANDLE_BASE + 4 * k;
cleanup:;
    int err = errno;
    if (fd >= 0) close(fd);
    free(path);
    if (fresh) { unlink(s->temp); free(s); }
    errno = err;
bad:
    file_error();
    return 0xFFFFFFFF;
}
static void file_failed(uint32_t h) {
    if (handle_fd(h) >= 0) {
        SaveFile *s = file_info[(h - HANDLE_BASE) / 4].save;
        if (s) { s->failed = 1; ++save_fail_seq; }
    }
    file_error();
}
static ssize_t file_write(uint32_t h, const void *buf, size_t n) {
    int fd = h >= 0x100 && h < 0x110 ? 2 : handle_fd(h);
    if (h >= HANDLE_BASE && (h - HANDLE_BASE) / 4 < 256 &&
        file_info[(h - HANDLE_BASE) / 4].path && profile_write_denied(file_info[(h - HANDLE_BASE) / 4].path)) {
        errno = EACCES; file_failed(h); return -1;
    }
    ssize_t r = write(fd, buf, n);
    if (r < 0 || (size_t)r != n) { if (r >= 0) errno = EIO; file_failed(h); }
    return r;
}
static int file_flush(uint32_t h) {
    int r = fsync(handle_fd(h));
    if (r) file_failed(h);
    return r == 0;
}
static int file_end(uint32_t h) {
    if (h >= HANDLE_BASE && (h - HANDLE_BASE) / 4 < 256 &&
        file_info[(h - HANDLE_BASE) / 4].path && profile_write_denied(file_info[(h - HANDLE_BASE) / 4].path)) {
        errno = EACCES; file_failed(h); return 0;
    }
    int fd = handle_fd(h);
    off_t pos = lseek(fd, 0, SEEK_CUR);
    int r = pos < 0 ? -1 : ftruncate(fd, pos);
    if (r) file_failed(h);
    return r == 0;
}
static int file_close(uint32_t h) {
    int fd = handle_fd(h);
    if (fd < 0) { errno = EBADF; file_error(); return 0; }
    unsigned k = (h - HANDLE_BASE) / 4;
    if (diag_on && file_info[k].path) {                     // BOOT_DIAG: sound bank closes (opens: CreateFileA, x360abk lines)
        size_t pl = strlen(file_info[k].path);
        if (pl > 4 && !strcasecmp(file_info[k].path + pl - 4, ".abk")) diag_event("abk close: %s", file_info[k].path);
    }
    SaveFile *s = file_info[k].save;
    int commit = s && (file_info[k].access & 0x40000000) && !--s->writers;
    if (s && profile_write_denied(file_info[k].path)) s->failed = 1;
    int ok = 1;
    if (commit && !s->failed && !private_cars_save_prepare(s->temp)) { errno = EIO; s->failed = 1; ok = 0; file_error(); }
    if (commit && !s->failed && !private_cars_backup_before_write(s->dest,s->temp)) { errno = EIO; s->failed = 1; ok = 0; file_error(); }
    if (commit && !s->failed && durable_sync(fd)) { s->failed = 1; ok = 0; file_error(); }
    if (close(fd)) { if (s) s->failed = 1; ok = 0; file_error(); }
    if (commit) {
        if (s->failed) ok = 0;
        else if (rename(s->temp, s->dest)) { ok = 0; file_error(); }
        else { durable_dir_sync(s->dest); ++save_commit_seq; } // published career generation
        if (!ok) unlink(s->temp);                  // failed save never replaces the previous destination
        s->temp[0] = 0;
    }
    if (s && !ok) ++save_fail_seq;
    if (s && !--s->refs) free(s);
    free(file_info[k].path); memset(&file_info[k], 0, sizeof file_info[k]); fds[k] = 0;
    return ok;
}
// MemcardInterfaceImpl save task (007f4f11, thiscall): state 0xb writes header + payload with __write/__close, then sets
// its result (+0x2f4 = 0 ok / 1 error, +0x2f8 = code) to 0 whatever happened. If a write, sync, close or rename failed
// during that step, report the failure the game itself uses for a failed open, so state 0xc shows its save-error dialog.
void extratrax_save_complete(int committed);
void padbind_save_complete(int committed);
static void save_sidecars_complete(uint32_t self, uint32_t state_before, unsigned seq_before, unsigned commits_before) {
    if (state_before != 0xb || !M32(self + 0x300)) return;
    int ok = save_fail_seq == seq_before && save_commit_seq != commits_before && !M32(self + 0x2f4);
    extratrax_save_complete(ok);
    padbind_save_complete(ok);
    private_cars_save_complete(ok);
}
static void save_task_fixup(uint32_t self, uint32_t state_before, unsigned seq_before) {
    if (state_before != 0xb || !M32(self + 0x300) || save_fail_seq == seq_before) return;
    M32(self + 0x2f4) = 1; M32(self + 0x2f8) = 7;
}
// END offline file layer
void f_007F4F11(Cpu *c) {                       // the career save task (see save_task_fixup)
    uint32_t self = c->ecx, state = M32(self + 0x70);
    unsigned seq = save_fail_seq, commits = save_commit_seq;
    f_007F4F11_orig(c);
    save_task_fixup(self, state, seq);
    save_sidecars_complete(self, state, seq, commits);
}
// -1 reserves a handle for X360 music or police-radio buffers (no temporary file on disk).
static X360MusicHandle music_handles[256];
static X360MusicHandle *handle_music(uint32_t h) {
    uint32_t k = (h - HANDLE_BASE) / 4;
    return h >= HANDLE_BASE && k < 256 && fds[k] == -1 ? &music_handles[k] : NULL;
}
static uint32_t new_music_handle(const X360MusicHandle *m) {
    for (int k = 1; k < 256; ++k) if (!fds[k]) {
        music_handles[k] = *m; fds[k] = -1; return HANDLE_BASE + k * 4;
    }
    last_error = 4; return 0xFFFFFFFF;
}

// ---- registry: the game's own key, kept in USERDATA/registry.txt between runs ----
// The file is written on every RegSetValueEx ("dword <name>=<n>" or "sz <name>=<text>" lines). There is no FirstTime
// value until the game writes one, so the first run auto-detects the video settings for the reported GPU and saves
// them; later runs (and the in-game options menu) use the saved ones. BOOT_REG=name=value[,name=value...] overrides
// DWORD values for one run without saving them (e.g. BOOT_REG=g_RacingResolution=3).
// The processor key is there as on any Windows machine: its ~MHz matches rdtsc (3 GHz). Without it EA's movie
// library calibrates rdtsc at the first movie by busy-waiting for a second (FUN_007ECBBE).
#define REG_HKEY_BASE 0x3000u
// BEGIN offline registry: audit4fix_test.py compiles reg_save in a scratch-only C harness.
typedef struct { const char *key; char name[64]; uint32_t type, dw; char sz[260]; } RegValue;
static const char *reg_keys[] = {NULL, "HKLM\\Software\\EA Games\\Need for Speed Most Wanted",
                                  "HKLM\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0"};
static RegValue reg_vals[64] = {
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "Install Dir", 1, 0, "C:\\NFSMW"},
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "CD Drive", 1, 0, "C:\\NFSMW\\"},
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "Language", 1, 0, "English US"},
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "CacheSize", 1, 0, "1468006400"},
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "SwapSize", 1, 0, "0"},
    {"HKLM\\Software\\EA Games\\Need for Speed Most Wanted", "StreamingInstall", 1, 0, "off"},
    {"HKLM\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", "~MHz", 4, 3000, ""},
};
static void reg_path(char *out, size_t n) {
    snprintf(out, n, "%s/registry.txt", userdata_dir());
}
static void reg_save(void) {

    char path[1024], tmp[1100];
    reg_path(path, sizeof path);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i) {
        if (strcmp(reg_vals[i].key, reg_keys[1])) continue;                 // only the game's key is saved
        if (reg_vals[i].type == 4) fprintf(f, "dword %s=%u\n", reg_vals[i].name, reg_vals[i].dw);
        else fprintf(f, "sz %s=%s\n", reg_vals[i].name, reg_vals[i].sz);
    }
    // A write error (disk full) keeps the previous registry.txt: durable_commit renames only a complete, synced file.
    if (!durable_commit(f, tmp, path)) fprintf(stderr, "boot: registry.txt not saved: %s\n", strerror(errno));
}
// END offline registry
static RegValue *reg_put(const char *name) {
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i) if (!strcmp(reg_vals[i].key, reg_keys[1]) && !strcasecmp(reg_vals[i].name, name)) return &reg_vals[i];
    for (unsigned i = 0; i < 64; ++i) if (!reg_vals[i].key) { reg_vals[i].key = reg_keys[1]; snprintf(reg_vals[i].name, sizeof reg_vals[i].name, "%s", name); return &reg_vals[i]; }
    return NULL;
}
static int reg_first_run = 1;                   // no FirstTime when the settings were loaded (the game writes it during that run)
static void reg_load(void) {
    char path[1024], line[512];
    reg_path(path, sizeof path);
    FILE *f = fopen(path, "r");
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        char *sp = strchr(line, ' '), *eq = sp ? strchr(sp, '=') : NULL;
        if (!eq) continue;
        *sp = *eq = 0;
        RegValue *v = reg_put(sp + 1);
        if (!v) continue;
        if (!strcmp(line, "dword")) { v->type = 4; v->dw = (uint32_t)strtoul(eq + 1, NULL, 10); }
        else { v->type = 1; snprintf(v->sz, sizeof v->sz, "%s", eq + 1); }
    }
    if (f) fclose(f);
    // The text language comes from the player's LANGUAGES folder, the Mac's languages and F10 (settings_port.c); with an
    // English install (or Auto on an English Mac) it is "English US", the value this table always held.
    const char *settings_game_language(void), *lang = settings_game_language();
    RegValue *lv = reg_put("Language");
    if (lv && lang) { lv->type = 1; snprintf(lv->sz, sizeof lv->sz, "%s", lang); }
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i) if (!strcasecmp(reg_vals[i].name, "FirstTime")) reg_first_run = 0;
    const char *o = getenv("BOOT_REG");
    while (o && *o) {
        char name[64];
        size_t n = strcspn(o, "=,");
        snprintf(name, sizeof name, "%.*s", (int)(n < 63 ? n : 63), o);
        o += n;
        if (*o == '=') {
            RegValue *v = reg_put(name);
            if (v) { v->type = 4; v->dw = (uint32_t)strtoul(o + 1, (char **)&o, 10); fprintf(stderr, "boot: registry %s=%u (BOOT_REG)\n", name, v->dw); }
        }
        while (*o && *o != ',') ++o;
        if (*o == ',') ++o;
    }
}
// d3d9.m (F10 Anti-aliasing): the game's own registry value, as its RegSetValueEx would store it; BOOT_REG runs keep the file.
void boot_reg_set_dword(const char *name, uint32_t v) {
    RegValue *r = reg_put(name);
    if (!r) return;
    r->type = 4; r->dw = v;
    if (!getenv("BOOT_REG")) reg_save();
}
// d3d9.m: is this the first run (the saved settings have no FirstTime yet), and did BOOT_REG set a value?
int boot_reg_first_run(void) { return reg_first_run; }
int boot_reg_override(const char *name) {
    const char *o = getenv("BOOT_REG");
    size_t n = strlen(name);
    for (; o && *o; o += strcspn(o, ",") + (o[strcspn(o, ",")] == ',')) if (!strncasecmp(o, name, n) && o[n] == '=') return 1;
    return 0;
}
// d3d9.m, after a first run's hardware detection: the game's own settings loader (FUN_006c1c00, which a first run
// skips) reads the registry again with only the BOOT_REG values answering, so they win over what detection picked.
static int reg_boot_only;
void boot_reg_reapply(Cpu *c) {
    if (!getenv("BOOT_REG")) return;
    Cpu s = *c;
    reg_boot_only = 1;
    call_guest(c, 0x006C1C00u, 0, NULL);
    reg_boot_only = 0;
    *c = s;
}
static int reg_open(uint32_t root, const char *sub, int create) {
    char path[512];
    snprintf(path, sizeof path, "%s\\%s", root == 0x80000002 ? "HKLM" : root == 0x80000001 ? "HKCU" : "HK?", sub ? sub : "");
    for (unsigned k = 1; k < sizeof reg_keys / sizeof *reg_keys; ++k) if (!strcasecmp(reg_keys[k], path)) return (int)k;
    (void)create;                                             // other keys stay absent (writes to them are dropped)
    return 0;
}
static const char *reg_key(uint32_t h) {
    uint32_t k = (h - REG_HKEY_BASE) / 4;
    return h >= REG_HKEY_BASE && k > 0 && k < sizeof reg_keys / sizeof *reg_keys ? reg_keys[k] : NULL;
}
static RegValue *reg_find(uint32_t h, const char *name) {
    const char *key = reg_key(h);
    if (!key) return NULL;
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i)
        if (!strcasecmp(reg_vals[i].key, key) && !strcasecmp(reg_vals[i].name, name ? name : "")) return &reg_vals[i];
    return NULL;
}
static void reg_set(uint32_t h, const char *name, uint32_t type, uint32_t data, uint32_t size) {
    const char *key = reg_key(h);
    if (!key) return;
    RegValue *v = reg_find(h, name);
    if (!v) for (unsigned i = 0; i < 64; ++i) if (!reg_vals[i].key) { v = &reg_vals[i]; v->key = key; snprintf(v->name, sizeof v->name, "%s", name ? name : ""); break; }
    if (!v) return;
    v->type = type;
    if (type == 4) v->dw = M32(data);
    else { uint32_t n = size < sizeof v->sz ? size : sizeof v->sz - 1; memcpy(v->sz, G + data, n); v->sz[n] = 0; }
    if (!getenv("BOOT_REG")) reg_save();          // BOOT_REG runs don't overwrite the saved settings
}

// ---- code the game loads at run time (EA AEMS sound modules marked executable with VirtualProtect) ----
// The same module is loaded again at the same address at every sound reload (9 times in an hour of career): a
// region at the same base is replaced, and the table grows. It was a fixed 256 that dropped every later region, so
// after ~an hour the first call into a newly placed module died ("indirect call ... not a known function").
typedef struct { uint32_t base, size; } CodeRegion;
static CodeRegion *code_regions;
static int ncode_regions, code_regions_cap;
static void code_region_add(Cpu *c, uint32_t a, uint32_t n) {
    int i = 0;
    while (i < ncode_regions && code_regions[i].base != a) ++i;
    if (i == ncode_regions) {
        if (ncode_regions == code_regions_cap) {
            code_regions_cap = code_regions_cap ? code_regions_cap * 2 : 256;
            code_regions = realloc(code_regions, (size_t)code_regions_cap * sizeof *code_regions);
        }
        ++ncode_regions;
    }
    code_regions[i].base = a; code_regions[i].size = n;
    uint32_t h = 2166136261u;                                 // FNV-1a of the bytes identifies the module
    for (uint32_t k = 0; k < n; ++k) h = (h ^ M8(a + k)) * 16777619u;
    fprintf(stderr, "boot: run-time code %08X..%08X (%u bytes, fnv %08X) from %08X\n", a, a + n, n, h, M32(c->esp));
    const char *dir = getenv("BOOT_DUMP_CODE");
    if (dir) {
        char path[1024];
        snprintf(path, sizeof path, "%s/code_%08X_%08X.bin", dir, h, a);
        FILE *f = fopen(path, "wb");
        if (f) { fwrite(G + a, 1, n, f); fclose(f); }
    }
}
int code_region_of(uint32_t t) {
    for (int i = 0; i < ncode_regions; ++i) if (t - code_regions[i].base < code_regions[i].size) return i;
    return -1;
}

static int win32(Cpu *c, const char *fn) {
    if (!strcmp(fn, "GetVersionExA")) {
        uint32_t p = arg(c, 0);
        M32(p + 4) = 5; M32(p + 8) = 1; M32(p + 12) = 2600; M32(p + 16) = 2;    // Windows XP SP2, NT platform
        strcpy((char *)G + p + 20, "Service Pack 2");
        RET(1, 1);
    }
    if (!strcmp(fn, "GetModuleHandleA")) {
        // the exe itself, or a system DLL (always loaded on Windows; the CRT relies on it)
        if (arg(c, 0) == 0) RET(0x400000, 1);
        const char *n = (const char *)G + arg(c, 0);
        if (server_library_name(n)) RET(server_handle(), 1);
        RET(strcasestr(n, "kernel32") || strcasestr(n, "user32") || strcasestr(n, "ntdll") ? 0x7C800000 : 0, 1);
    }
    if (!strcmp(fn, "GetProcAddress")) {
        if (arg(c, 0) == SERVER_BASE) RET(server_proc(arg(c, 0), arg(c, 1)), 2);
        if (boot_trace) fprintf(stderr, "      GetProcAddress(%08X) -> not provided\n", arg(c, 1));
        RET(0, 2);
    }
    if (!strcmp(fn, "LoadLibraryA")) {
        if (server_library_name(gptr(arg(c, 0)))) {
            char path[4096];
            resolve("server.dll", path, sizeof path);
            RET(server_load(c, path), 1);
        }
        RET(0, 1);
    }
    if (!strcmp(fn, "VirtualAlloc")) {
        uint32_t n = arg(c, 1), requested = arg(c, 0);
        if (requested && requested < SERVER_RESERVED_END && (uint64_t)requested + n > SERVER_BASE) {
            last_error = 487; RET(0, 4);
        }
        if (!arg(c, 0)) heap_note(3, (void *)(uintptr_t)M32(c->esp), n);
        heap_next = (heap_next + 0xFFFF) & ~0xFFFFu;
        uint32_t a = heap_next;
        heap_next += (n + 0xFFFF) & ~0xFFFFu;
        memset(G + a, 0, n);
        RET(arg(c, 0) ? arg(c, 0) : a, 4);
    }
    if (!strcmp(fn, "VirtualFree")) RET(1, 3);
    if (!strcmp(fn, "VirtualQuery")) RET(0, 3);
    if (!strcmp(fn, "VirtualProtect")) {          // (addr, size, protect, old*)
        uint32_t a = arg(c, 0), n = arg(c, 1), prot = arg(c, 2);
        if (arg(c, 3)) M32(arg(c, 3)) = 4;                    // PAGE_READWRITE
        if (prot & 0xF0) code_region_add(c, a, n);            // PAGE_EXECUTE*: code loaded at run time
        RET(1, 4);
    }
    if (!strcmp(fn, "GetStartupInfoA")) {
        uint32_t p = arg(c, 0);
        memset(G + p, 0, 68);
        M32(p) = 68;
        RET(0, 1);
    }
    if (!strcmp(fn, "GetStdHandle")) RET(0x100 + (arg(c, 0) & 0xFF), 1);
    if (!strcmp(fn, "GetFileType")) RET(2, 1);                 // FILE_TYPE_CHAR
    if (!strcmp(fn, "SetHandleCount")) RET(arg(c, 0), 1);
    if (!strcmp(fn, "GetCommandLineA")) {
        static uint32_t cl;
        if (!cl) cl = gstr("speed.exe");
        RET(cl, 0);
    }
    if (!strcmp(fn, "GetEnvironmentStringsW") || !strcmp(fn, "GetEnvironmentStrings")) RET(galloc(8), 0);
    if (!strcmp(fn, "FreeEnvironmentStringsA") || !strcmp(fn, "FreeEnvironmentStringsW")) RET(1, 1);
    if (!strcmp(fn, "GetModuleFileNameA")) {
        const char *s = arg(c, 0) && arg(c, 0) == server_handle() ? "C:\\NFSMW\\server.dll" : "C:\\NFSMW\\speed.exe";
        uint32_t n = arg(c, 2);
        strncpy((char *)G + arg(c, 1), s, n);
        RET(strlen(s), 3);
    }
    if (!strcmp(fn, "GetACP")) RET(1252, 0);
    if (!strcmp(fn, "GetOEMCP")) RET(437, 0);
    if (!strcmp(fn, "IsValidCodePage")) RET(1, 1);
    if (!strcmp(fn, "GetCPInfo")) {
        uint32_t p = arg(c, 1);
        memset(G + p, 0, 20);
        M32(p) = 1; M8(p + 4) = '?';
        RET(1, 2);
    }
    if (!strcmp(fn, "GetUserDefaultLCID")) RET(0x409, 0);
    if (!strcmp(fn, "TlsFree")) RET(1, 1);
    if (!strcmp(fn, "GetLastError")) RET(last_error, 0);
    if (!strcmp(fn, "SetLastError")) { last_error = arg(c, 0); RET(0, 1); }
    if (!strcmp(fn, "GetCurrentProcessId")) RET(1, 0);
    if (!strcmp(fn, "GetCurrentProcess")) RET(0xFFFFFFFF, 0);
    if (!strcmp(fn, "GetTickCount")) RET(host_ticks() / 1000000ull, 0);
    if (!strcmp(fn, "QueryPerformanceFrequency")) { M64(arg(c, 0)) = 1000000000ull; RET(1, 1); }
    if (!strcmp(fn, "QueryPerformanceCounter")) { M64(arg(c, 0)) = host_ticks(); RET(1, 1); }
    if (!strcmp(fn, "GetSystemTimeAsFileTime")) {
        M64(arg(c, 0)) = host_filetime();
        RET(0, 1);
    }
    if (!strcmp(fn, "SetUnhandledExceptionFilter")) RET(0, 1);
    if (!strcmp(fn, "IsDebuggerPresent")) RET(0, 0);
    if (!strcmp(fn, "SetErrorMode")) RET(0, 1);
    if (!strcmp(fn, "GetSystemInfo")) {
        uint32_t p = arg(c, 0);
        memset(G + p, 0, 36);
        M32(p + 4) = 0x1000; M32(p + 8) = 0x10000; M32(p + 12) = 0x7FFEFFFF; M32(p + 20) = 1; M32(p + 24) = 586; M32(p + 28) = 0x10000;
        RET(0, 1);
    }
    if (!strcmp(fn, "ExitProcess") || !strcmp(fn, "TerminateProcess")) {
        printf("boot: ExitProcess(%u) after %u import calls\n", arg(c, 0), import_calls);
        void app_restart_game_exited(int successful);
        app_restart_game_exited(!strcmp(fn, "ExitProcess") && arg(c, 0) == 0);
        exit(0);
    }
    // ---- code pages: Latin-1 <-> UTF-16 identity mapping (enough for the game's ASCII strings) ----
    if (!strcmp(fn, "MultiByteToWideChar")) {     // (cp, flags, src, srclen, dst, dstlen)
        uint32_t src = arg(c, 2), dst = arg(c, 4);
        int32_t n = (int32_t)arg(c, 3), cap = (int32_t)arg(c, 5);
        if (n < 0) n = (int32_t)strlen((char *)G + src) + 1;
        if (cap == 0) RET(n, 6);
        if (n > cap) n = cap;
        for (int32_t k = 0; k < n; ++k) M16(dst + 2 * k) = M8(src + k);
        RET(n, 6);
    }
    if (!strcmp(fn, "WideCharToMultiByte")) {     // (cp, flags, src, srclen, dst, dstlen, default, used)
        uint32_t src = arg(c, 2), dst = arg(c, 4);
        int32_t n = (int32_t)arg(c, 3), cap = (int32_t)arg(c, 5);
        if (n < 0) { n = 0; while (M16(src + 2 * n)) ++n; ++n; }
        if (cap == 0) RET(n, 8);
        if (n > cap) n = cap;
        for (int32_t k = 0; k < n; ++k) { uint16_t ch = M16(src + 2 * k); M8(dst + k) = ch < 256 ? (uint8_t)ch : '?'; }
        RET(n, 8);
    }
    if (!strcmp(fn, "GetStringTypeA") || !strcmp(fn, "GetStringTypeW")) {
        int wide = fn[13] == 'W';
        uint32_t src = arg(c, wide ? 1 : 2), out = arg(c, wide ? 3 : 4);
        int32_t n = (int32_t)arg(c, wide ? 2 : 3);
        if (n < 0) { n = 0; while (wide ? M16(src + 2 * n) : M8(src + n)) ++n; }
        for (int32_t k = 0; k < n; ++k) {
            unsigned ch = wide ? M16(src + 2 * k) : M8(src + k);
            uint16_t t = 0;
            if (ch >= 'A' && ch <= 'Z') t = 0x1 | 0x100;          // C1_UPPER | C1_ALPHA
            else if (ch >= 'a' && ch <= 'z') t = 0x2 | 0x100;     // C1_LOWER | C1_ALPHA
            else if (ch >= '0' && ch <= '9') t = 0x4;             // C1_DIGIT
            else if (ch == ' ' || (ch >= 9 && ch <= 13)) t = 0x8 | (ch == ' ' || ch == 9 ? 0x40 : 0);   // SPACE, BLANK
            else if (ch < 32 || ch == 127) t = 0x20;              // C1_CNTRL
            else if (ch < 128) t = 0x10;                          // C1_PUNCT
            if (ch >= '0' && ch <= '9') t |= 0x80;                // C1_XDIGIT
            if ((ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')) t |= 0x80;
            M16(out + 2 * k) = t;
        }
        RET(1, wide ? 4 : 5);
    }
    if (!strcmp(fn, "LCMapStringA") || !strcmp(fn, "LCMapStringW")) {   // (lcid, flags, src, srclen, dst, dstlen)
        int wide = fn[11] == 'W';
        uint32_t flags = arg(c, 1), src = arg(c, 2), dst = arg(c, 4);
        int32_t n = (int32_t)arg(c, 3), cap = (int32_t)arg(c, 5);
        if (n < 0) { n = 0; while (wide ? M16(src + 2 * n) : M8(src + n)) ++n; ++n; }
        if (cap == 0) RET(n, 6);
        if (n > cap) n = cap;
        for (int32_t k = 0; k < n; ++k) {
            unsigned ch = wide ? M16(src + 2 * k) : M8(src + k);
            if ((flags & 0x100) && ch >= 'A' && ch <= 'Z') ch += 32;      // LCMAP_LOWERCASE
            if ((flags & 0x200) && ch >= 'a' && ch <= 'z') ch -= 32;      // LCMAP_UPPERCASE
            if (wide) M16(dst + 2 * k) = (uint16_t)ch; else M8(dst + k) = (uint8_t)ch;
        }
        RET(n, 6);
    }
    if (!strcmp(fn, "CompareStringA")) {           // (lcid, flags, s1, n1, s2, n2) -> 1 less, 2 equal, 3 greater
        uint32_t a = arg(c, 2), b = arg(c, 4);
        int32_t n1 = (int32_t)arg(c, 3), n2 = (int32_t)arg(c, 5);
        if (n1 < 0) n1 = (int32_t)strlen((char *)G + a);
        if (n2 < 0) n2 = (int32_t)strlen((char *)G + b);
        int r = (arg(c, 1) & 1) ? strncasecmp((char *)G + a, (char *)G + b, n1 < n2 ? n1 : n2)
                                : memcmp(G + a, G + b, n1 < n2 ? n1 : n2);
        if (r == 0) r = n1 - n2;
        RET(r < 0 ? 1 : r == 0 ? 2 : 3, 6);
    }
    if (!strcmp(fn, "GetLocaleInfoA")) {           // (lcid, type, buf, len)
        const char *v = "1252";
        if (arg(c, 3) == 0) RET(strlen(v) + 1, 4);
        strncpy((char *)G + arg(c, 2), v, arg(c, 3));
        RET(strlen(v) + 1, 4);
    }
    if (!strcmp(fn, "IsValidLocale")) RET(1, 2);
    if (!strcmp(fn, "IsBadReadPtr") || !strcmp(fn, "IsBadWritePtr")) RET(arg(c, 0) == 0, 2);
    if (!strcmp(fn, "IsBadCodePtr")) RET(arg(c, 0) == 0, 1);
    if (!strcmp(fn, "OutputDebugStringA")) { fprintf(stderr, "  [debug] %s", (char *)G + arg(c, 0)); RET(0, 1); }
    if (!strcmp(fn, "GetTimeZoneInformation")) { memset(G + arg(c, 0), 0, 172); RET(0, 1); }
    if (!strcmp(fn, "GlobalMemoryStatusEx")) {
        uint32_t p = arg(c, 0);
        M32(p + 4) = 20; M64(p + 8) = 2ull << 30; M64(p + 16) = 1ull << 30; M64(p + 24) = 4ull << 30; M64(p + 32) = 3ull << 30;
        M64(p + 40) = 2ull << 30; M64(p + 48) = 1ull << 30;
        RET(1, 1);
    }
    if (!strcmp(fn, "GetPriorityClass")) RET(0x20, 1);
    if (!strcmp(fn, "SetPriorityClass") || !strcmp(fn, "SetThreadPriority")) RET(1, 2);
    if (!strcmp(fn, "GetThreadPriority")) RET(0, 1);
    if (!strcmp(fn, "GetProcessAffinityMask")) { M32(arg(c, 1)) = 1; M32(arg(c, 2)) = 1; RET(1, 3); }
    if (!strcmp(fn, "SetProcessAffinityMask")) RET(1, 2);
    if (!strcmp(fn, "SetThreadAffinityMask")) RET(1, 2);
    if (!strcmp(fn, "RegOpenKeyExA") || !strcmp(fn, "RegOpenKeyA") || !strcmp(fn, "RegCreateKeyA")) {
        // (root, subkey, [opts, sam,] hkey*)
        int n = strstr(fn, "KeyEx") ? 5 : 3;
        uint32_t out = arg(c, n - 1);
        int k = reg_open(arg(c, 0), gptr(arg(c, 1)), fn[3] == 'C');
        if (boot_trace) fprintf(stderr, "      %s(%08X, %s) -> %s\n", fn, arg(c, 0), gptr(arg(c, 1)) ? gptr(arg(c, 1)) : "", k ? "ok" : "not found");
        if (!k) RET(2, n);                                    // ERROR_FILE_NOT_FOUND
        M32(out) = REG_HKEY_BASE + 4 * (uint32_t)k;
        RET(0, n);
    }
    if (!strcmp(fn, "RegQueryValueExA")) {        // (hkey, name, reserved, type*, data*, size*)
        const RegValue *v = reg_find(arg(c, 0), gptr(arg(c, 1)));
        if (reg_boot_only && !(gptr(arg(c, 1)) && boot_reg_override(gptr(arg(c, 1))))) v = NULL;
        if (boot_trace) fprintf(stderr, "      RegQueryValueExA(%08X, %s) -> %s\n", arg(c, 0), gptr(arg(c, 1)) ? gptr(arg(c, 1)) : "(default)", v ? "found" : "not found");
        if (!v) RET(2, 6);
        uint32_t size = v->type == 4 ? 4 : (uint32_t)strlen(v->sz) + 1, sp = arg(c, 5);
        if (arg(c, 3)) M32(arg(c, 3)) = v->type;
        if (arg(c, 4)) {
            if (sp && M32(sp) < size) { M32(sp) = size; RET(234, 6); }   // ERROR_MORE_DATA
            if (v->type == 4) M32(arg(c, 4)) = v->dw; else memcpy(G + arg(c, 4), v->sz, size);
        }
        if (sp) M32(sp) = size;
        RET(0, 6);
    }
    if (!strcmp(fn, "RegSetValueExA")) {          // (hkey, name, reserved, type, data, size)
        reg_set(arg(c, 0), gptr(arg(c, 1)), arg(c, 3), arg(c, 4), arg(c, 5));
        RET(0, 6);
    }
    if (!strcmp(fn, "RegCloseKey")) RET(0, 1);
    if (!strcmp(fn, "GetCurrentDirectoryA")) { strncpy((char *)G + arg(c, 1), "C:\\NFSMW", arg(c, 0)); RET(8, 2); }
    if (!strcmp(fn, "SetCurrentDirectoryA")) RET(1, 1);
    if (!strcmp(fn, "CreateDirectoryA")) {
        char host[4096];
        resolve((char *)G + arg(c, 0), host, sizeof host);
        int r = mkdir(host, 0755);
        if (r && errno == EEXIST) { last_error = 183; RET(0, 2); }   // ERROR_ALREADY_EXISTS
        RET(r == 0, 2);
    }
    if (!strcmp(fn, "CreateFileA")) {             // (name, access, share, sa, disposition, flags, template)
        char host[4096];
        resolve((char *)G + arg(c, 0), host, sizeof host);
        uint32_t acc = arg(c, 1), disp = arg(c, 4);
        X360MusicHandle music;
        if (file_flags(acc, disp) == O_RDONLY && (x360music_open((char *)G + arg(c, 0), &music) ||
            x360copspeech_open((char *)G + arg(c, 0), &music) ||
            x360nisaudio_open((char *)G + arg(c, 0), &music))) {
            if (boot_trace) fprintf(stderr, "      CreateFileA(%s) -> X360 audio memory\n", (char *)G + arg(c, 0));
            if (diag_on) diag_event("CreateFileA(%s, %X, disp %u) -> ok", (char *)G + arg(c, 0), acc, disp);
            RET(new_music_handle(&music), 7);
        }
        int x360_arrest_path(const char *guest, char *host, size_t n);   // x360_textures.m: NFS_X360_ARREST=x360
        if (file_flags(acc, disp) == O_RDONLY) {
            x360_arrest_path((char *)G + arg(c, 0), host, sizeof host);
            private_car_path((char *)G + arg(c, 0), host, sizeof host);
        }
        uint32_t h = file_open((char *)G + arg(c, 0), host, acc, arg(c, 2), disp, arg(c, 5));
        if (boot_trace) fprintf(stderr, "      CreateFileA(%s) -> %s\n", (char *)G + arg(c, 0), h != 0xFFFFFFFF ? host : "not found");
        if (diag_on) diag_event("CreateFileA(%s, %X, disp %u) -> %s", (char *)G + arg(c, 0), acc, disp, h != 0xFFFFFFFF ? "ok" : strerror(errno));
        RET(h, 7);
    }
    if (!strcmp(fn, "ReadFile")) {                // (h, buf, n, read*, overlapped)
        int fd = handle_fd(arg(c, 0));
        X360MusicHandle *music = handle_music(arg(c, 0));
        ssize_t r = music ? (ssize_t)x360music_read(music, G + arg(c, 1), arg(c, 2)) : fd < 0 ? -1 : read(fd, G + arg(c, 1), arg(c, 2));
        if (arg(c, 3)) M32(arg(c, 3)) = r < 0 ? 0 : (uint32_t)r;
        void threads_note_io(void);
        threads_note_io();                        // wakes the game thread's loading poll (threads.c, SleepEx)
        RET(r >= 0, 5);
    }
    if (!strcmp(fn, "WriteFile")) {
        uint32_t h = arg(c, 0);
        ssize_t r = file_write(h, G + arg(c, 1), arg(c, 2));
        if (arg(c, 3)) M32(arg(c, 3)) = r < 0 ? 0 : (uint32_t)r;
        RET(r >= 0, 5);
    }
    if (!strcmp(fn, "SetFilePointer")) {          // (h, lo, hi*, method)
        X360MusicHandle *music = handle_music(arg(c, 0));
        if (music) {
            int64_t distance = (int32_t)arg(c, 1);
            if (arg(c, 2)) distance = (int64_t)((uint64_t)M32(arg(c, 2)) << 32 | arg(c, 1));
            int64_t pos = x360music_seek(music, distance, arg(c, 3));
            if (pos < 0) last_error = 87;  // ERROR_INVALID_PARAMETER
            else if (arg(c, 2)) M32(arg(c, 2)) = (uint32_t)((uint64_t)pos >> 32);
            RET(pos < 0 ? 0xFFFFFFFF : (uint32_t)pos, 4);
        }
        int fd = handle_fd(arg(c, 0));
        off_t o = lseek(fd, (int32_t)arg(c, 1), (int)arg(c, 3));
        RET(o < 0 ? 0xFFFFFFFF : (uint32_t)o, 4);
    }
    if (!strcmp(fn, "GetFileTime")) {
        struct stat st;
        int fd = handle_fd(arg(c, 0));
        if (fd < 0 || fstat(fd, &st)) { last_error = 6; RET(0, 4); }
        struct timespec times[] = {st.st_birthtimespec, st.st_atimespec, st.st_mtimespec};
        for (int i = 0; i < 3; ++i) if (arg(c, i + 1)) {
            uint64_t ft = ((uint64_t)times[i].tv_sec + 11644473600ull) * 10000000ull + (uint64_t)times[i].tv_nsec / 100;
            M32(arg(c, i + 1)) = (uint32_t)ft;
            M32(arg(c, i + 1) + 4) = (uint32_t)(ft >> 32);
        }
        RET(1, 4);
    }
    if (!strcmp(fn, "GetFileSize")) {
        X360MusicHandle *music = handle_music(arg(c, 0));
        if (music) {
            if (arg(c, 1)) M32(arg(c, 1)) = (uint32_t)((uint64_t)music->size >> 32);
            RET((uint32_t)music->size, 2);
        }
        struct stat st;
        int fd = handle_fd(arg(c, 0));
        if (fd < 0 || fstat(fd, &st)) RET(0xFFFFFFFF, 2);
        if (arg(c, 1)) M32(arg(c, 1)) = 0;
        RET((uint32_t)st.st_size, 2);
    }
    if (!strcmp(fn, "CloseHandle")) {
        if (threads_close(arg(c, 0))) RET(1, 1);
        if (handle_music(arg(c, 0))) {
            uint32_t k = (arg(c, 0) - HANDLE_BASE) / 4;
            memset(&music_handles[k], 0, sizeof music_handles[k]); fds[k] = 0;
            RET(1, 1);
        }
        RET(file_close(arg(c, 0)), 1);
    }
    if (!strcmp(fn, "FlushFileBuffers")) RET(file_flush(arg(c, 0)), 1);
    if (!strcmp(fn, "SetEndOfFile")) RET(file_end(arg(c, 0)), 1);
    if (!strcmp(fn, "GetFullPathNameA")) {
        const char *in = (char *)G + arg(c, 0);
        snprintf((char *)G + arg(c, 2), arg(c, 1), "%s", in);
        if (arg(c, 3)) M32(arg(c, 3)) = 0;
        RET(strlen(in), 4);
    }
    if (!strcmp(fn, "GetDiskFreeSpaceExA")) {
        if (arg(c, 1)) M64(arg(c, 1)) = 100ull << 30;
        if (arg(c, 2)) M64(arg(c, 2)) = 200ull << 30;
        if (arg(c, 3)) M64(arg(c, 3)) = 100ull << 30;
        RET(1, 4);
    }
    if (!strcmp(fn, "FindFirstFileA")) {          // (pattern, WIN32_FIND_DATAA *)
        uint32_t h = find_first((char *)G + arg(c, 0), arg(c, 1));
        if (diag_on) diag_event("FindFirstFileA(%s) -> %s", (char *)G + arg(c, 0), h != 0xFFFFFFFF ? (char *)G + arg(c, 1) + 44 : "none");
        RET(h, 2);
    }
    if (!strcmp(fn, "FindClose")) {
        int k = find_handle(arg(c, 0));
        if (k >= 0) { closedir(finds[k].d); finds[k].d = NULL; }
        RET(1, 1);
    }
    if (!strcmp(fn, "GetLongPathNameA")) {
        snprintf((char *)G + arg(c, 1), arg(c, 2), "%s", (char *)G + arg(c, 0));
        RET(strlen((char *)G + arg(c, 0)), 3);
    }
    // ---- misc: processes, comm ports, times, locale, globals ----
    if (!strcmp(fn, "CreateToolhelp32Snapshot")) { last_error = 5; RET(0xFFFFFFFF, 2); }
    if (!strcmp(fn, "Process32First") || !strcmp(fn, "Process32Next")) { last_error = 18; RET(0, 2); }
    if (!strcmp(fn, "CreateProcessA")) { last_error = 2; RET(0, 10); }
    if (!strcmp(fn, "GetCommState") || !strcmp(fn, "SetCommState") || !strcmp(fn, "SetCommTimeouts") ||
        !strcmp(fn, "SetCommMask") || !strcmp(fn, "PurgeComm")) { last_error = 6; RET(0, 2); }
    if (!strcmp(fn, "GetCommConfig") || !strcmp(fn, "SetCommConfig") || !strcmp(fn, "SetupComm") ||
        !strcmp(fn, "WaitCommEvent")) { last_error = 6; RET(0, 3); }
    if (!strcmp(fn, "GetOverlappedResult")) { last_error = 6; RET(0, 4); }
    if (!strcmp(fn, "GetSystemTime") || !strcmp(fn, "GetLocalTime")) {
        uint64_t ft = host_filetime();
        time_t t = (time_t)(ft / 10000000ull - 11644473600ull);
        struct tm tm;
        if (fn[3] == 'S') gmtime_r(&t, &tm); else localtime_r(&t, &tm);
        uint32_t p = arg(c, 0);
        M16(p) = (uint16_t)(tm.tm_year + 1900); M16(p + 2) = (uint16_t)(tm.tm_mon + 1); M16(p + 4) = (uint16_t)tm.tm_wday;
        M16(p + 6) = (uint16_t)tm.tm_mday; M16(p + 8) = (uint16_t)tm.tm_hour; M16(p + 10) = (uint16_t)tm.tm_min;
        M16(p + 12) = (uint16_t)tm.tm_sec; M16(p + 14) = (uint16_t)(ft / 10000ull % 1000);
        RET(0, 1);
    }
    if (!strcmp(fn, "FileTimeToLocalFileTime")) { M64(arg(c, 1)) = M64(arg(c, 0)); RET(1, 2); }
    if (!strcmp(fn, "FileTimeToSystemTime") || !strcmp(fn, "SystemTimeToFileTime")) {
        // FILETIME: 100 ns since 1601-01-01 UTC. SYSTEMTIME: y, mon, dow, day, h, min, s, ms (uint16 each).
        uint32_t in = arg(c, 0), out = arg(c, 1);
        if (fn[0] == 'F') {
            time_t t = (time_t)(M64(in) / 10000000ull - 11644473600ull);
            struct tm tm;
            gmtime_r(&t, &tm);
            M16(out) = (uint16_t)(tm.tm_year + 1900); M16(out + 2) = (uint16_t)(tm.tm_mon + 1); M16(out + 4) = (uint16_t)tm.tm_wday;
            M16(out + 6) = (uint16_t)tm.tm_mday; M16(out + 8) = (uint16_t)tm.tm_hour; M16(out + 10) = (uint16_t)tm.tm_min;
            M16(out + 12) = (uint16_t)tm.tm_sec; M16(out + 14) = (uint16_t)(M64(in) / 10000ull % 1000);
        } else {
            struct tm tm = {0};
            tm.tm_year = M16(in) - 1900; tm.tm_mon = M16(in + 2) - 1; tm.tm_mday = M16(in + 6);
            tm.tm_hour = M16(in + 8); tm.tm_min = M16(in + 10); tm.tm_sec = M16(in + 12);
            M64(out) = ((uint64_t)timegm(&tm) + 11644473600ull) * 10000000ull + (uint64_t)M16(in + 14) * 10000ull;
        }
        RET(1, 2);
    }
    if (!strcmp(fn, "GetDateFormatA") || !strcmp(fn, "GetTimeFormatA")) {   // (lcid, flags, systime*, fmt, buf, n)
        char s[32];
        uint32_t st = arg(c, 2);
        time_t now = time(NULL);
        struct tm tm;
        localtime_r(&now, &tm);
        if (fn[3] == 'D') snprintf(s, sizeof s, "%02u/%02u/%04u", st ? M16(st + 2) : tm.tm_mon + 1, st ? M16(st + 6) : tm.tm_mday, st ? M16(st) : tm.tm_year + 1900);
        else snprintf(s, sizeof s, "%02u:%02u:%02u", st ? M16(st + 8) : tm.tm_hour, st ? M16(st + 10) : tm.tm_min, st ? M16(st + 12) : tm.tm_sec);
        if (arg(c, 5) == 0) RET(strlen(s) + 1, 6);
        snprintf((char *)G + arg(c, 4), arg(c, 5), "%s", s);
        RET(strlen(s) + 1, 6);
    }
    if (!strcmp(fn, "CompareStringW")) {          // (lcid, flags, s1, n1, s2, n2)
        uint32_t a = arg(c, 2), b = arg(c, 4);
        int32_t n1 = (int32_t)arg(c, 3), n2 = (int32_t)arg(c, 5);
        if (n1 < 0) { n1 = 0; while (M16(a + 2 * n1)) ++n1; }
        if (n2 < 0) { n2 = 0; while (M16(b + 2 * n2)) ++n2; }
        int r = 0;
        for (int32_t k = 0; !r && k < n1 && k < n2; ++k) {
            unsigned x = M16(a + 2 * k), y = M16(b + 2 * k);
            if (arg(c, 1) & 1) { if (x < 128) x = (unsigned)tolower((int)x); if (y < 128) y = (unsigned)tolower((int)y); }
            r = (int)x - (int)y;
        }
        if (!r) r = n1 - n2;
        RET(r < 0 ? 1 : r == 0 ? 2 : 3, 6);
    }
    if (!strcmp(fn, "GetLocaleInfoW")) {          // (lcid, type, buf, len)
        const char *v = "1252";
        if (arg(c, 3) == 0) RET(strlen(v) + 1, 4);
        for (uint32_t k = 0; k <= strlen(v) && k < arg(c, 3); ++k) M16(arg(c, 2) + 2 * k) = (uint16_t)v[k];
        RET(strlen(v) + 1, 4);
    }
    if (!strcmp(fn, "EnumSystemLocalesA")) RET(1, 2);
    if (!strcmp(fn, "FreeLibrary")) {
        if (arg(c, 0) == SERVER_BASE) RET(server_free(c, arg(c, 0)), 1);
        RET(1, 1);
    }
    if (!strcmp(fn, "SetConsoleCtrlHandler")) RET(1, 2);
    if (!strcmp(fn, "SetEnvironmentVariableA")) RET(1, 2);
    if (!strcmp(fn, "SetStdHandle")) RET(1, 2);
    if (!strcmp(fn, "GetDriveTypeA")) RET(3, 1);                 // DRIVE_FIXED
    if (!strcmp(fn, "GetLogicalDrives")) RET(1u << 2, 0);        // C:
    if (!strcmp(fn, "GetDiskFreeSpaceA")) {       // (root, sectorsPerCluster*, bytesPerSector*, free*, total*)
        M32(arg(c, 1)) = 8; M32(arg(c, 2)) = 512; M32(arg(c, 3)) = 0x00FFFFFF; M32(arg(c, 4)) = 0x01FFFFFF;
        RET(1, 5);
    }
    if (!strcmp(fn, "DeleteFileA") || !strcmp(fn, "RemoveDirectoryA")) {
        char host[4096];
        resolve((char *)G + arg(c, 0), host, sizeof host);
        if (profile_write_denied(host)) { last_error = 5; RET(0, 1); }
        int r = fn[0] == 'D' ? unlink(host) : rmdir(host);
        if (r) last_error = 2;
        RET(r == 0, 1);
    }
    if (!strcmp(fn, "MoveFileA")) {
        char a[4096], b[4096];
        resolve((char *)G + arg(c, 0), a, sizeof a);
        resolve((char *)G + arg(c, 1), b, sizeof b);
        if (profile_write_denied(a) || profile_write_denied(b)) { last_error = 5; RET(0, 2); }
        int r = rename(a, b);
        if (r) last_error = 2;
        RET(r == 0, 2);
    }
    if (!strcmp(fn, "FindNextFileA")) {
        int k = find_handle(arg(c, 0));
        if (k >= 0 && find_next((uint32_t)k, arg(c, 1))) RET(1, 2);
        last_error = 18;                                               // ERROR_NO_MORE_FILES
        RET(0, 2);
    }
    // ---- WINMM: timers; waveOut/waveIn report no devices (sound goes through DirectSound) ----
    if (!strcmp(fn, "timeBeginPeriod") || !strcmp(fn, "timeEndPeriod")) RET(0, 1);
    if (!strcmp(fn, "timeGetTime")) RET(host_ticks() / 1000000ull, 0);
    if (!strcmp(fn, "waveOutGetNumDevs") || !strcmp(fn, "waveInGetNumDevs")) RET(0, 0);
    if (!strcmp(fn, "waveOutGetDevCapsA") || !strcmp(fn, "waveInGetDevCapsA")) RET(2, 3);          // MMSYSERR_BADDEVICEID
    if (!strcmp(fn, "waveOutOpen") || !strcmp(fn, "waveInOpen")) RET(2, 6);
    if (!strcmp(fn, "waveOutClose") || !strcmp(fn, "waveInClose") || !strcmp(fn, "waveOutReset") || !strcmp(fn, "waveInReset") ||
        !strcmp(fn, "waveInStart") || !strcmp(fn, "waveInStop")) RET(5, 1);                       // MMSYSERR_INVALHANDLE
    if (!strcmp(fn, "waveOutWrite") || !strcmp(fn, "waveOutPrepareHeader") || !strcmp(fn, "waveOutUnprepareHeader") ||
        !strcmp(fn, "waveInAddBuffer") || !strcmp(fn, "waveInPrepareHeader") || !strcmp(fn, "waveInUnprepareHeader") ||
        !strcmp(fn, "waveOutGetPosition") || !strcmp(fn, "waveInGetPosition")) RET(5, 3);
    if (!strcmp(fn, "waveOutSetVolume")) RET(5, 2);
    if (!strcmp(fn, "SHGetFolderPathA")) {        // (hwnd, csidl, token, flags, path): every folder is our user-data dir
        strcpy((char *)G + arg(c, 4), "C:\\USERDATA");
        if (boot_trace) fprintf(stderr, "      SHGetFolderPathA(csidl %X) -> C:\\USERDATA\n", arg(c, 1));
        RET(0, 5);
    }
    if (!strcmp(fn, "DebugBreak")) { fprintf(stderr, "boot: DebugBreak from %08X\n", M32(c->esp)); RET(0, 0); }
    if (!strcmp(fn, "FatalAppExitA")) die("FatalAppExit: %s", gptr(arg(c, 1)) ? gptr(arg(c, 1)) : "");
    return 0;
}

void host_import(Cpu *c, uint32_t slot) {
    int lo = 0, hi = (int)speed_import_count - 1;
    const char *name = NULL;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (speed_imports[m].slot == slot) { name = speed_imports[m].name; break; }
        if (speed_imports[m].slot < slot) lo = m + 1; else hi = m - 1;
    }
    int import_index = (lo + hi) / 2;
    if (!name) {
        unsigned module_index = 0;
        name = server_import(slot, &module_index);
        import_index = name ? (int)(speed_import_count + module_index) : -1;
        if (name && module_index < 256) { server_import_names[module_index] = name; ++server_import_calls[module_index]; }
    }
    if (!name) die("call through unknown IAT slot %08X", slot);
    ++import_calls;
    const char *fn = strchr(name, '!') + 1;
    if (boot_trace) fprintf(stderr, "  [%u] %s  esp %08X\n", import_calls, name, c->esp);
    // BOOT_DIAG_WATCH=hex,hex,... (or BOOT_WATCH): report when those guest dwords change, checked at every import
    // call; the change is logged with the import and the guest return address where it was noticed.
    static uint32_t watch_addr[16], watch_val[16];
    static int nwatch = -1;
    if (nwatch < 0) {
        nwatch = 0;
        const char *w = getenv("BOOT_DIAG_WATCH") ? getenv("BOOT_DIAG_WATCH") : getenv("BOOT_WATCH");
        while (w && *w && nwatch < 16) {
            watch_addr[nwatch] = (uint32_t)strtoul(w, (char **)&w, 16);
            watch_val[nwatch] = M32(watch_addr[nwatch]);
            ++nwatch;
            while (*w == ',') ++w;
        }
    }
    for (int k = 0; k < nwatch; ++k)
        if (M32(watch_addr[k]) != watch_val[k]) {
            uint32_t off = 0;
            const char *sym = diag_symbol(M32(c->esp), &off);
            fprintf(stderr, "boot: watch [%08X] %08X -> %08X before %s from %08X (%s+0x%X)\n", watch_addr[k], watch_val[k], M32(watch_addr[k]), name, M32(c->esp), sym ? sym : "?", off);
            if (diag_on) diag_event("WATCH [%08X] %08X -> %08X before %s @%08X %s", watch_addr[k], watch_val[k], M32(watch_addr[k]), name, M32(c->esp), sym ? sym : "?");
            watch_val[k] = M32(watch_addr[k]);
        }
    uint32_t ret_addr = M32(c->esp), a0 = arg(c, 0), a1 = arg(c, 1), a2 = arg(c, 2), a3 = arg(c, 3);
    gil_maybe_yield(c);
    GilRec *gr = diag_on ? gil_rec_self() : NULL;   // BOOT_DIAG: the import this thread is in, for gilwait.log
    if (gr) { gr->ra = ret_addr; gr->imp_t0 = host_ticks(); gr->imp = name; }
    int ok = !strncmp(name, "d3dx9", 5) ? d3dx(c, fn) || d3dx_effect(c, fn)
           : !strncmp(name, "d3d9", 4) ? win32_d3d9(c, fn)
           : !strncmp(name, "DSOUND", 6) ? win32_dsound(c, fn)
           : !strncmp(name, "DINPUT8", 7) ? win32_dinput(c, fn)
           : !strncmp(name, "WS2_32", 6) || !strncmp(name, "NETAPI32", 8) ? win32_ws2(c, fn)
           : !strncmp(name, "USER32", 6) || !strncmp(name, "GDI32", 5) ? win32_user32(c, fn)
           : win32_threads(c, fn) || win32_heap(c, fn) || win32(c, fn);
    if (!ok)
        die("not implemented yet: %s (call %u, return address %08X)", name, import_calls, ret_addr);
    if (gr) { gr->imp = NULL; gr->imp_t0 = host_ticks(); }   // imp_t0 now: when guest code resumed
    if (diag_on) {
        // high-frequency calls go to the in-memory ring only (they'd swamp events.log)
        static const char *const noisy[] = {"CriticalSection", "TlsGetValue", "TlsSetValue", "LastError", "QueryPerformance", "timeGetTime",
                                            "Interlocked", "HeapAlloc", "HeapFree", "HeapReAlloc", "HeapValidate", "IsBadReadPtr", "GetCurrentThreadId", "GetTickCount",
                                            "D3DXMatrix", "D3DXVec", "D3DXQuaternion", "D3DXPlane", "WaitForSingleObject", "ReleaseMutex",
                                            "SetWaitableTimer", "ReleaseSemaphore", "PeekMessage", "Sleep"};
        static int8_t quiet_of[4096];                      // per import: 0 unknown, 1 ring only, 2 events.log
        int8_t *qc = import_index >= 0 && import_index < 4096 ? &quiet_of[import_index] : NULL;
        if (!qc || !*qc) {
            int quiet = 0;
            for (unsigned k = 0; k < sizeof noisy / sizeof *noisy && !quiet; ++k) quiet = strstr(fn, noisy[k]) != NULL;
            if (qc) *qc = quiet ? 1 : 2;
            else if (!quiet) qc = NULL;
        }
        if (qc && *qc == 1) { uint32_t av[4] = {a0, a1, a2, a3}; diag_call_ring(name, 4, av, c->eax, ret_addr); }
        else diag_event("%s(%X, %X, %X, %X) -> %X @%08X", name, a0, a1, a2, a3, c->eax, ret_addr);
    }
    if (boot_trace && (!strncmp(fn, "Heap", 4) || !strncmp(fn, "Virtual", 7)))
        fprintf(stderr, "      (%08X, %08X, %08X, %08X) -> %08X  [from %08X]\n", a0, a1, a2, a3, c->eax, ret_addr);
}

// ---------------- deterministic race start ----------------
// BOOT_RACE=1 (or a race ID such as 15.1.1, from GLOBAL/gameplay.bin) skips the front end with the game's own
// Split-screen car memory pool (NFS_PADS=2). PC's CarLoaderPoolSizes (008F7EF0) holds 50,000 KiB for one player and
// 1 KiB for split (word 008F7EF4: PC never ran two player cars in one pool); the X360 uses 55,000 KiB for either
// player count (828FD46C/70). RaceStarter picks 008F7EF4 only for the split race, so the one-player size is untouched.
static void split_car_pool(void) {
    if (pad_count() != 2) return;
    const char *t = getenv("NFS_TEST_SPLITPOOL");                                // test only: =pc keeps PC's 1 KiB (before/after proof)
    if ((t && !strcmp(t, "pc")) || M32(0x008F7EF4) != 1) return;               // not the PC table: leave it
    M32(0x008F7EF4) = 55000;
}

// developer switches: main() calls RaceStarter::StartSkipFERace instead of LoadFrontend when SkipFE is set, and
// QuickGame::HandleSkipFEOptions builds the startup race from SkipFERaceID. Retail never reads online.cfg
// (OnlineCfg::ReadConfigFile has no caller), so the globals are written here, before the entry point runs.
// BOOT_RACE_CAR (default bmwm3gtre46; "race" = the car the race specifies), BOOT_RACE_LAPS (default 10), BOOT_RACE_AI (opponents, default 3).
static void race_setup(void) {
    const char *race = getenv("BOOT_RACE");
    if (!race || !*race || !strcmp(race, "0")) return;
    if (!strcmp(race, "1")) race = "15.1.1";
    // A race ID is one word (15.1.1): a value with a space or '=' is several NAME=value settings that reached the game
    // as one (zsh doesn't split an unquoted $VAR), e.g. BOOT_RACE="15.1.1 BOOT_AUTODRIVE=1 ...". The game then builds
    // its startup race from the unknown ID "15.1.1 BOOT_AUTODRIVE=1" and CarLoader loops for good on a car pack with a
    // garbage name at the first car loads (frame ~110: the 2026-10-20/21 "startup hang"). Refuse it instead.
    if (strpbrk(race, " \t=") || strlen(race) > 0x17) {
        fprintf(stderr, "boot: BOOT_RACE \"%s\" is not a race ID (several NAME=value in one argument?): not starting\n", race);
        exit(2);
    }
    const char *car = getenv("BOOT_RACE_CAR") ? getenv("BOOT_RACE_CAR") : "bmwm3gtre46";
    M32(0x00926064) = 1;                                                        // SkipFE
    M32(0x00926144) = 1;                                                        // SkipMovies
    memset(G + 0x00926068, 0, 0x18);                                            // SkipFERaceID (char[0x18])
    strncpy((char *)G + 0x00926068, race, 0x17);
    memset(G + 0x009B4128, 0, 0x40);                                            // onlinePlayerCar buffer
    strncpy((char *)G + 0x009B4128, car, 0x3F);
    M32(0x008F86A8) = strcmp(car, "race") ? 0x009B4128 : 0;                    // SkipFEPlayerCar ("race": the race's own
                                                                                // preset car, e.g. 16.2.1's M3GTRCAREERSTART)
    M32(0x008F86BC) = getenv("BOOT_RACE_LAPS") ? (uint32_t)atoi(getenv("BOOT_RACE_LAPS")) : 10;   // SkipFENumLaps
    if (getenv("BOOT_RACE_COPS")) M32(0x008F86C0) = 0;                         // SkipFEDisableCops (test: cops spawn in the race, for BOOT_PURSUIT)
    M32(0x00926080) = getenv("BOOT_RACE_AI") ? (uint32_t)atoi(getenv("BOOT_RACE_AI")) : 3;        // SkipFENumAICars
    printf("boot: SkipFE race %s, car %s, %u laps, %u opponents\n", race, car, M32(0x008F86BC), M32(0x00926080));
}

// BOOT_ONLINE=host|client: a 2-player race through the game's own client/server code (stage A1), entered by the
// developer SkipFE-online route: StartSkipFERace calls RaceStarter::InitSkipFEOnline (0056C3E0) when OnlineEnabled is
// set, which starts DirtySock, hosts or connects, and waits for NUMCLIENTCARS remote players. The OnlineCfg globals
// (OnlineCfg::ProcessSetting 0078A900 stores; retail never reads online.cfg) are written here like the SkipFE ones.
// BOOT_ONLINE_PEER=ip[:port]: the client's host (default 127.0.0.1:3658). The race is the BOOT_RACE one (default
// 15.1.1, 1 lap, no AI). Two copies on one Mac: the client's binds move one port up (ws2_port_shift) when the peer is
// a loopback address. Off by default: nothing here runs without BOOT_ONLINE.
extern uint32_t ws2_local_ip;
extern int ws2_port_shift;
extern uint8_t ws2_mac_last;
extern int ws2_online;
static void online_setup(void) {
    const char *role = getenv("BOOT_ONLINE");
    if (!role || !*role || !strcmp(role, "0")) return;
    int host = !strcmp(role, "host");
    if (!host && strcmp(role, "client")) { fprintf(stderr, "boot: BOOT_ONLINE must be host or client, not \"%s\"\n", role); exit(2); }
    const char *peer = getenv("BOOT_ONLINE_PEER") ? getenv("BOOT_ONLINE_PEER") : "127.0.0.1";
    unsigned a = 127, b = 0, cc = 0, d = 1, port = 3658;
    if (sscanf(peer, "%u.%u.%u.%u:%u", &a, &b, &cc, &d, &port) < 4 || a > 255 || b > 255 || cc > 255 || d > 255 || !port || port > 65535) {
        fprintf(stderr, "boot: BOOT_ONLINE_PEER \"%s\" is not ip[:port]\n", peer);
        exit(2);
    }
    uint32_t peer_ip = a << 24 | b << 16 | cc << 8 | d;   // host order, as SocketInTextGetAddr returns it
    // This copy's address: the one that routes to the peer (loopback peer -> 127.0.0.1).
    uint32_t local = 0x7F000001;
    if (a != 127) {
        int fd = socket(AF_INET, SOCK_DGRAM, 0);
        struct sockaddr_in to = {sizeof to, AF_INET, htons(9), {htonl(peer_ip)}}, me;
        socklen_t ml = sizeof me;
        if (fd >= 0 && !connect(fd, (struct sockaddr *)&to, sizeof to) && !getsockname(fd, (struct sockaddr *)&me, &ml)) local = ntohl(me.sin_addr.s_addr);
        if (fd >= 0) close(fd);
    }
    ws2_local_ip = local;
    ws2_online = 1;
    ws2_mac_last = host ? 1 : 2;
    if (!host && a == 127) ws2_port_shift = getenv("BOOT_ONLINE_PORTSHIFT") ? atoi(getenv("BOOT_ONLINE_PORTSHIFT")) : 1;
    setenv("BOOT_RACE", "15.1.1", 0);
    setenv("BOOT_RACE_LAPS", "1", 0);
    setenv("BOOT_RACE_AI", "0", 0);
    M32(0x00926110) = 1;                                       // OnlineEnabled
    M32(0x009B416C) = host;                                    // OnlineIsServer
    M32(0x00905D98) = 1;                                       // SkipFEOnlineNumExpectedRemotePlayers (NUMCLIENTCARS)
    M32(0x009B3E98) = host ? local : peer_ip;                  // SkipFEOnlineHostAddr (SERVERIP)
    *(uint16_t *)(G + 0x00905DF8) = (uint16_t)port;            // NetworkHostPort (SERVERPORT)
    M32(0x009B417C) = local;                                   // local address: read at 9 places, written by nothing in
                                                               // the exe; InitSkipFEOnline spins until it is nonzero
    M32(0x009B4174) = 0;                                       // NetworkUseLobbies: no EA lobby
    M32(0x00926094) = 1;                                       // SkipFEDisableTraffic: the online race manager
                                                               // (FUN_00761B30 state 5) ends the race at the first
                                                               // vehicle without an IOnlinePlayer
    if (host) M32(0x009B40F8) = peer_ip;                      // SkipFEOnlineClientAddr[0] (CLIENTIP0): the host
                                                               // links to each listed client (InitSkipFEOnline 0056C493)
    printf("boot: online %s, peer %u.%u.%u.%u:%u, local %u.%u.%u.%u, port shift %d\n", role, a, b, cc, d, port,
           local >> 24, local >> 16 & 255, local >> 8 & 255, local & 255, ws2_port_shift);
}

// BOOT_SKIPINTRO=1: a new career skips the prologue (the D-Day races) and starts in the safehouse with the career
// cars, through the game's own SkipCareerIntro switch (bytes 0 and 1, as EasterEggs::ActivateEasterEgg sets them).
// BOOT_UNLOCKALL=1 sets UnlockAllThings (every car, part and Challenge Series event). For scripted playability runs.
static void dev_switches(void) {
    if (getenv("BOOT_SKIPINTRO")) { G[0x00926125] = 1; G[0x00926126] = 1; printf("boot: SkipCareerIntro\n"); }
    if (getenv("BOOT_UNLOCKALL")) { G[0x00926124] = 1; printf("boot: UnlockAllThings\n"); }
}

// BOOT_DEADLINE=<seconds>: the run ends by itself, whatever the game threads do (the run scripts set it so a test game
// can't outlive its caller). After the deadline: a clean exit() (stdio and diag files flushed, code 124), and _exit 5 s
// later if that hangs. Also exits the same way once the parent is gone (reparented to launchd: the run script was
// killed). Only when BOOT_DEADLINE is set, so a game the user starts by hand is never affected.
static void *hard_exit_thread(void *p) { (void)p; sleep(5); _exit(124); return NULL; }
static void *deadline_thread(void *p) {
    long tenths = (long)(intptr_t)p * 10;
    pid_t parent = getppid();
    const char *why = "BOOT_DEADLINE reached";
    for (long t = 0; t < tenths; ++t) {
        usleep(100000);
        if (parent != 1 && getppid() != parent) { why = "BOOT_DEADLINE: parent process gone"; break; }
    }
    fprintf(stderr, "boot: %s, exiting\n", why);
    pthread_t h;
    pthread_create(&h, NULL, hard_exit_thread, NULL);
    exit(124);
}
static void deadline_start(void) {
    const char *e = getenv("BOOT_DEADLINE");
    if (!e || atoi(e) <= 0) return;
    pthread_t t;
    pthread_create(&t, NULL, deadline_thread, (void *)(intptr_t)atoi(e));
    pthread_detach(t);
}

#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
// Restart-only values are pinned before guest registration or file I/O.
static void private_cars_boot(void) {
    char root[4096];
    private_cars_metadata_userdata(userdata_dir());
    const char *override = getenv("NFS_TEST_PRIVATE_CARS_ROOT");
    if (override && *override) snprintf(root, sizeof root, "%s", override);
    else snprintf(root, sizeof root, "%s/private-cars", userdata_dir());
    const char *const names[]={PRIVATE_CARS_SWAP_NAMES};
    const char *originals[14]; size_t count=0;
    for (unsigned i=0;i<14;++i) {
        char id[64]; snprintf(id,sizeof id,"cars.original.%s",names[i]);
        Setting *setting=settings_find(id);
        if (setting && setting->boot_value) originals[count++]=names[i];
    }
    Setting *master=settings_find("cars.later_games");
    private_cars_init(root, master?master->boot_value:1, originals, count);
    if (private_cars_error()[0]) {
        fprintf(stderr,"cars: %s\n",private_cars_error());
    }
}
#endif

// ---------------- loader ----------------
int main(int argc, char **argv) {
    game_dir = app_start(argc, argv);
    void settings_init(void), settings_dump(const char *), report_init(void);
    settings_init();                       // settings.c: the F10 menu's saved values, before anything reads its switch
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    private_cars_boot();
#endif
    void settings_language_setup(const char *);
    settings_language_setup(game_dir);     // settings_port.c: the language packs present; the Language row when > 1
    if (getenv("BOOT_DIAG")) { char p[1100]; snprintf(p, sizeof p, "%s/settings_rows.txt", getenv("BOOT_DIAG")); settings_dump(p); }
    report_init();                         // report.m: full logging (next launch), the hang watchdog
    save_cleanup();                        // discard interrupted saves before any guest profile scan
    boot_trace = getenv("BOOT_TRACE") != NULL;
    deadline_start();
    char path[4096];
    snprintf(path, sizeof path, "%s/speed.exe", game_dir);
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open %s: %s", path, strerror(errno));
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *pe = malloc(size);
    fread(pe, 1, size, f);
    fclose(f);

    G = mmap(0, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_NORESERVE, -1, 0);
    if (G == MAP_FAILED) die("cannot reserve guest memory");
    uint32_t off = *(uint32_t *)(pe + 0x3C);
    uint16_t nsec = *(uint16_t *)(pe + off + 6), optsz = *(uint16_t *)(pe + off + 20);
    memcpy(G + 0x400000, pe, 0x1000);                                   // headers
    for (int i = 0; i < nsec; ++i) {
        uint8_t *s = pe + off + 24 + optsz + i * 40;
        uint32_t va = *(uint32_t *)(s + 12), rs = *(uint32_t *)(s + 16), raw = *(uint32_t *)(s + 20);
        memcpy(G + 0x400000 + va, pe + raw, rs);
    }
    for (unsigned i = 0; i < speed_import_count; ++i) M32(speed_imports[i].slot) = THUNK_BASE + 4 * i;
    online_setup();
    race_setup();
    split_car_pool();
    dev_switches();
    reg_load();
    widescreen_setup();
    feedback_setup();

    Cpu cpu = {0}, *c = &cpu;
    g_cpu = c;
    if (getenv("BOOT_TIMEOUT")) { signal(SIGALRM, watchdog); alarm((unsigned)atoi(getenv("BOOT_TIMEOUT"))); }
    c->fs_base = TEB;
    M32(TEB + 0x00) = 0xFFFFFFFF;          // SEH chain end
    M32(TEB + 0x04) = STACK_TOP;           // stack base
    M32(TEB + 0x08) = STACK_TOP - 0x100000;
    M32(TEB + 0x18) = TEB;                 // self
    M32(TEB + 0x30) = PEB;
    M32(PEB + 0x08) = 0x400000;            // image base
    c->esp = STACK_TOP - 0x100;
    c->cw = 0x027F;
    c->esp -= 4;
    M32(c->esp) = 0xDEADBEEF;              // entry's return address
    diag_init();
#if !defined(NFS_PUBLIC) || !NFS_PUBLIC
    if (diag_on && private_cars_error()[0]) diag_event("cars: %s",private_cars_error());
#endif
    threads_init();
    threads_set_main_cpu(c);
    x360copspeech_warm();                  // host-only CRC/conversion, before the loading screen needs speech
    printf("boot: image loaded, %u translated functions, entry %08X\n", guest_func_count, speed_entry);
    dispatch(c, speed_entry);
    printf("boot: entry returned eax=%08X after %u import calls\n", c->eax, import_calls);
    return 0;
}
