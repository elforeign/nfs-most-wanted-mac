#include "seh.h"
#define RECOMP_GUEST_MEMORY_OWNER 1 /* defines and maps g_mem */
#include "memory.h"
#include "win32.h"
#include "loader.h"

#include "../platform/os.h"
#if defined(__APPLE__) || (defined(__linux__) && !defined(__ANDROID__))
#include <execinfo.h>
#endif
#include <algorithm>
#include <atomic>
#include <signal.h>
#if defined(__APPLE__)
#include <sys/ucontext.h>
#endif
#include <map>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <set>
#include <string>

uint8_t *g_mem = nullptr;

// ---------------------------------------------------------------------------
// The guest-memory watchpoint declared in x86.h. A stray write is only ever
// seen through the damage it does, somewhere else, later; this reports the
// write itself. The host backtrace names the generated function, which is the
// guest routine doing the writing - the one thing the corrupted value cannot
// say. Armed from RECOMP_WATCH=<hex address>[:<length>], default length 4.
// ---------------------------------------------------------------------------
uint32_t recomp_frame_watch = 0;

uint32_t g_watch_base = 0;
uint32_t g_watch_len = 0;
RecompDirty g_dirty[RECOMP_DIRTY_SLOTS];
uint32_t g_dirty_count = 0;
uint32_t g_store_hooks = 0;   // x86.h: (a watch is armed) or (a dirty range is open)

extern "C" void recomp_store_hooks_update(void) {
    g_store_hooks = (g_watch_len != 0 || g_dirty_count != 0) ? 1u : 0u;
}

namespace {
struct WatchArm {
    WatchArm() {
#if defined(RECOMP_GAME_STORE_HOOKS) && !RECOMP_GAME_STORE_HOOKS
        fprintf(stderr, "[recomp] translated stores: no watch / dirty-range check (game.toml store_hooks = false)\n");
#endif
        recomp_frame_watch = recomp_env("WATCH_FRAME") ? 1u : 0u;
        const char *s = recomp_env("WATCH");
        if (!s || !*s)
            return;
        char *end = nullptr;
        const unsigned long long base = strtoull(s, &end, 16);
        unsigned long long len = 4;
        if (end && *end == ':') {
            const unsigned long long given = strtoull(end + 1, nullptr, 16);
            if (given)
                len = given;
        }
        if (base >= GUEST_SIZE)
            return;
        if (len == 0 || len > GUEST_SIZE - base)
            len = GUEST_SIZE - base;
        g_watch_base = (uint32_t)base;
        g_watch_len = (uint32_t)len;
        recomp_store_hooks_update();
#if defined(RECOMP_GAME_STORE_HOOKS) && !RECOMP_GAME_STORE_HOOKS
        // D-177: this game's translated stores carry no check (game.toml store_hooks = false), so only the kit's
        // own stores could report; say so rather than watch silently.
        fprintf(stderr, "[recomp] RECOMP_WATCH: not available in this build (store_hooks = false): the game's own "
                        "stores are not checked\n");
#endif
    }
};
WatchArm g_watch_arm;

// The host call stack names the generated function - fn_00xxxxxx - and that
// name IS the guest address of the routine doing the writing, which is the
// whole point. One line per write: a watched address in a reused stack slot is
// written thousands of times, and a report long enough to read is a report too
// slow to reach the moment worth reading.
const char *watch_writer() {
#if defined(__APPLE__) || (defined(__linux__) && !defined(__ANDROID__))
    static char out[128];
    void *frames[32];
    const int n = backtrace(frames, 32);
    char **names = backtrace_symbols(frames, n);
    if (!names)
        return "?";
    out[0] = 0;
    for (int i = 1; i < n; ++i) {
        const char *fn = strstr(names[i], "fn_00");
        if (!fn)
            continue;
        size_t k = 0;
        while (k + 1 < sizeof out && fn[k] && fn[k] != ' ')
            ++k;
        snprintf(out, sizeof out, "%.*s", (int)k, fn);
        break;
    }
    free(names);
    return out[0] ? out : "?";
#else
    return "(no backtrace)";
#endif
}
} // namespace

extern "C" void recomp_frame_changed(X86 *c, uint32_t target, uint32_t before, uint32_t after) {
    // Rate-limited: a routine that does this does it on every call, and the
    // first few name it as well as thousands would. EIP is where the callee
    // left off: a routine that escapes through an indirect jump its own body
    // does not cover returns from there without running its epilogue, and that
    // address is the whole diagnosis.
    static uint32_t said = 0;
    if (said++ < 64)
        LOGW("frame: %08x returned with EBP %08x, was %08x (left at eip=%08x)", target, after,
             before, c ? c->eip : 0u);
}

extern "C" void recomp_watch_hit(uint32_t addr, uint32_t n, uint64_t value) {
    // Report every write, not just the first: what matters is which of them
    // was the last one before the damage was read back, and a legitimate
    // writer and a stray one look identical one at a time.
    static uint32_t hits = 0;
    LOGW("watch: %08x %u = %llx by %s (hit %u)", addr, n, (unsigned long long)value, watch_writer(),
         ++hits);
}

// ---------------------------------------------------------------------------
// Logging helpers (declared in guest.h; kept here so every translation unit in
// the runtime gets them without a separate object file).
// ---------------------------------------------------------------------------
int log_level() {
    static int lvl = -1;
    if (lvl < 0) {
        const char *e = recomp_env("LOG");
        lvl = e ? atoi(e) : 1;
    }
    return lvl;
}

void log_msg(int level, const char *fmt, ...) {
    if (log_level() < level)
        return;
    va_list ap;
    va_start(ap, fmt);
    fputs("[recomp] ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

// A guest that generates code writes a new routine at a new address every
// time, and a diagnostic keyed by that address is a distinct key on every
// call. The set is therefore bounded: past the cap nothing new is remembered
// or reported, so a run that would have filled memory with keys for addresses
// nobody will ever look at stays flat instead. The cap is far above what a
// run that is behaving reports.
static const size_t kLogOnceKeys = 4096;

bool log_once(const char *key, const char *fmt, ...) {
    static std::set<std::string> seen;
    static bool capped = false;
    if (seen.size() >= kLogOnceKeys) {
        if (!capped) {
            capped = true;
            if (log_level() >= 1)
                fprintf(stderr,
                        "[recomp] log_once: %zu distinct diagnostics reported; further "
                        "first-occurrence messages are suppressed\n",
                        seen.size());
        }
        return false;
    }
    if (!seen.insert(key).second)
        return false;
    if (log_level() < 1)
        return true;
    va_list ap;
    va_start(ap, fmt);
    fputs("[recomp] ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    return true;
}

std::string gm_str(uint32_t a, size_t max_len) {
    if (a == 0)
        return std::string();
    std::string out;
    for (size_t i = 0; i < max_len && a + i < GUEST_SIZE; ++i) {
        char ch = (char)g_mem[a + i];
        if (!ch)
            break;
        out.push_back(ch);
    }
    return out;
}

uint32_t gm_put_str(uint32_t a, const char *s, uint32_t cap) {
    if (!a || !cap)
        return 0;
    uint32_t n = 0;
    while (s[n] && n + 1 < cap) {
        g_mem[a + n] = (uint8_t)s[n];
        ++n;
    }
    g_mem[a + n] = 0;
    return n;
}

std::string gm_wstr(uint32_t a, size_t max_chars) {
    std::string out;
    if (!a || !gm_valid(a, 2))
        return out;
    // Clamp before address arithmetic, including the lookahead for a low surrogate.
    max_chars = std::min(max_chars, (size_t)(GUEST_SIZE - a) / 2);
    for (size_t i = 0; i < max_chars; ++i) {
        uint32_t u = rd16(a + 2 * (uint32_t)i);
        if (!u)
            break;
        if (u >= 0xd800 && u < 0xdc00 && i + 1 < max_chars) {
            uint32_t lo = rd16(a + 2 * (uint32_t)(i + 1));
            if (lo >= 0xdc00 && lo < 0xe000) {
                u = 0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00);
                ++i;
            }
        }
        if (u >= 0xd800 && u < 0xe000)
            u = 0xfffd;
        if (u < 0x80) {
            out += (char)u;
        } else if (u < 0x800) {
            out += (char)(0xc0 | (u >> 6));
            out += (char)(0x80 | (u & 0x3f));
        } else if (u < 0x10000) {
            out += (char)(0xe0 | (u >> 12));
            out += (char)(0x80 | ((u >> 6) & 0x3f));
            out += (char)(0x80 | (u & 0x3f));
        } else {
            out += (char)(0xf0 | (u >> 18));
            out += (char)(0x80 | ((u >> 12) & 0x3f));
            out += (char)(0x80 | ((u >> 6) & 0x3f));
            out += (char)(0x80 | (u & 0x3f));
        }
    }
    return out;
}

uint32_t gm_put_wstr(uint32_t a, const std::string &s, uint32_t cap) {
    if (!a || !cap || !gm_valid(a, 2))
        return 0;
    cap = std::min(cap, (GUEST_SIZE - a) / 2);
    uint32_t n = 0;
    size_t i = 0;
    while (i < s.size() && n + 1 < cap) {
        unsigned char b = (unsigned char)s[i];
        if (!b)
            break;
        uint32_t u = 0xfffd, minimum = 0;
        size_t len = 1;
        if (b < 0x80) {
            u = b;
        } else if (b >= 0xc2 && b <= 0xdf) {
            u = b & 0x1f;
            minimum = 0x80;
            len = 2;
        } else if (b >= 0xe0 && b <= 0xef) {
            u = b & 0x0f;
            minimum = 0x800;
            len = 3;
        } else if (b >= 0xf0 && b <= 0xf4) {
            u = b & 0x07;
            minimum = 0x10000;
            len = 4;
        }
        bool valid = len <= s.size() - i;
        for (size_t k = 1; valid && k < len; ++k) {
            unsigned char next = (unsigned char)s[i + k];
            valid = (next & 0xc0) == 0x80;
            u = (u << 6) | (next & 0x3f);
        }
        if (!valid || u < minimum || u > 0x10ffff || (u >= 0xd800 && u < 0xe000)) {
            // Consume one invalid byte at a time; never read beyond the source.
            u = 0xfffd;
            len = 1;
        }
        i += len;
        if (u >= 0x10000) {
            if (n + 2 >= cap)
                break;
            wr16(a + 2 * n++, (uint16_t)(0xd800 + ((u - 0x10000) >> 10)));
            wr16(a + 2 * n++, (uint16_t)(0xdc00 + ((u - 0x10000) & 0x3ff)));
        } else {
            wr16(a + 2 * n++, (uint16_t)u);
        }
    }
    wr16(a + 2 * n, 0);
    return n;
}

// ---------------------------------------------------------------------------
// Arena
// ---------------------------------------------------------------------------
namespace {

struct Blk {
    uint32_t size; // rounded, multiple of 16
    uint32_t req;  // bytes the guest asked for
    bool used;
};

// Every block in address order, plus an index of just the free ones so that
// first fit walks free blocks instead of the whole heap.
std::map<uint32_t, Blk> *g_blocks = nullptr;
std::set<uint32_t> *g_free = nullptr;
uint64_t g_total_allocs = 0, g_total_frees = 0;

inline uint32_t align_up(uint32_t v, uint32_t a) {
    return (v + a - 1) & ~(a - 1);
}

// Largest request the arena could ever satisfy. Anything at or above this
// would overflow the rounding in align_up (0xffffffff rounds to 0), so it is
// rejected before any arithmetic on it.
const uint32_t HEAP_ARENA_BYTES = std::max(HEAP_LIMIT - HEAP_BASE, GUEST_HEAP_EXTRA_SIZE);
static_assert(!GUEST_HEAP_EXTRA_SIZE ||
                  (GUEST_HEAP_EXTRA_BASE >= 0x10000000u && GUEST_HEAP_EXTRA_BASE < GUEST_SIZE &&
                   GUEST_HEAP_EXTRA_SIZE <= GUEST_SIZE - GUEST_HEAP_EXTRA_BASE),
              "extra heap must fit above the fixed runtime layout");
inline bool size_is_sane(uint32_t size) {
    return size <= HEAP_ARENA_BYTES;
}

// Records a block, keeping the free index in step.
void put_block(uint32_t addr, uint32_t size, uint32_t req, bool used) {
    (*g_blocks)[addr] = Blk{size, req, used};
    if (used)
        g_free->erase(addr);
    else
        g_free->insert(addr);
}

void drop_block(std::map<uint32_t, Blk>::iterator it) {
    g_free->erase(it->first);
    g_blocks->erase(it);
}

// The kit span (NFSMW issue 8, 3.0.4): the top 32 MB of the extra heap span hold the kit's own objects - DirectSound
// rings and views, guest thread stacks - and nothing the guest allocates. On Windows those live in DirectSound's and
// the system's own memory, never among the game's blocks; a player's game wrote a sound bank over the kit's sound
// buffer when the two were neighbours in one heap. heap_alloc never takes a block in the span, heap_alloc_kit takes
// only those, and no merge or in-place growth crosses its lower edge. RECOMP_KIT_SPAN=0 = one shared heap (3.0.3).
const uint32_t KIT_SPAN_BYTES = 0x02000000u;
uint32_t g_kit_lo = 0, g_kit_hi = 0;
inline bool kit_side(uint32_t addr) {
    return addr >= g_kit_lo && addr < g_kit_hi;
}
// Blocks at `a` and `b` may join only on the same side of the kit span's edge.
inline bool same_side(uint32_t a, uint32_t b) {
    return kit_side(a) == kit_side(b);
}
bool kit_span_enabled() {
    static const bool on = [] {
        const char *v = recomp_env("KIT_SPAN");
        return !(v && v[0] == '0');
    }();
    return on;
}
void guard_reset(); // the guarded-block registry, below
void large_reset(); // the large-block log, below
void readonly_reset(); // the read-only page table, below (cleared with the heap)
bool readonly_release(uint32_t addr); // below: true when `addr` was a read-only page (now writable, untracked)

void heap_reset() {
    guard_reset();
    readonly_reset();
    if (!g_blocks)
        g_blocks = new std::map<uint32_t, Blk>();
    if (!g_free)
        g_free = new std::set<uint32_t>();
    g_blocks->clear();
    g_free->clear();
    put_block(HEAP_BASE, HEAP_LIMIT - HEAP_BASE, 0, false);
    g_kit_lo = g_kit_hi = 0;
    if (GUEST_HEAP_EXTRA_SIZE >= 4 * KIT_SPAN_BYTES && kit_span_enabled()) {
        g_kit_hi = GUEST_HEAP_EXTRA_BASE + GUEST_HEAP_EXTRA_SIZE;
        g_kit_lo = g_kit_hi - KIT_SPAN_BYTES;
        put_block(GUEST_HEAP_EXTRA_BASE, GUEST_HEAP_EXTRA_SIZE - KIT_SPAN_BYTES, 0, false);
        put_block(g_kit_lo, KIT_SPAN_BYTES, 0, false);
    } else if (GUEST_HEAP_EXTRA_SIZE) {
        put_block(GUEST_HEAP_EXTRA_BASE, GUEST_HEAP_EXTRA_SIZE, 0, false);
    }
    g_total_allocs = g_total_frees = 0;
    large_reset();
}

} // namespace

void mem_init() {
    recomp_seh_reset(nullptr);
    if (g_mem) {
        os_vm_release(g_mem, GUEST_SIZE);
        g_mem = nullptr;
    }
    void *p = os_vm_reserve(GUEST_SIZE);
    if (!p) {
        fprintf(stderr, "[recomp] fatal: cannot map %u bytes of guest memory\n", GUEST_SIZE);
        abort();
    }
    g_mem = (uint8_t *)p;
    heap_reset();
}

void mem_shutdown() {
    recomp_seh_reset(nullptr);
    if (g_mem) {
        os_vm_release(g_mem, GUEST_SIZE);
        g_mem = nullptr;
    }
    if (g_blocks) {
        delete g_blocks;
        g_blocks = nullptr;
    }
    if (g_free) {
        delete g_free;
        g_free = nullptr;
    }
    guard_reset();
}

namespace {
uint32_t heap_alloc_side(uint32_t size, bool zero, uint32_t align, bool kit);
}
// 3.0.4 (issue 8): the guest's large blocks (256 KB and up), the first 48 taken and the first 48 of those freed, with
// the guest EIP, so a log shows which allocation a stray write came from and whether it was still live.
namespace {
constexpr uint32_t kLargeBlock = 0x40000u;
constexpr uint32_t kLargeLogged = 48;
uint32_t g_large_taken, g_large_freed;
uint32_t g_large_addr[kLargeLogged];
void large_reset() {
    g_large_taken = g_large_freed = 0;
}
void note_large_take(uint32_t a, uint32_t size) {
    if (!a || size < kLargeBlock || g_large_taken >= kLargeLogged)
        return;
    g_large_addr[g_large_taken++] = a;
    const X86 *c = guest_current_context();
    LOGW("heap: guest block %08x..%08x (%u KB) taken at EIP=%08x", a, a + size, size / 1024u, c ? c->eip : 0u);
}
void note_large_free(uint32_t a, uint32_t size) {
    if (g_large_freed >= kLargeLogged)
        return;
    for (uint32_t i = 0; i < g_large_taken; ++i)
        if (g_large_addr[i] == a) {
            g_large_addr[i] = 0;
            ++g_large_freed;
            const X86 *c = guest_current_context();
            LOGW("heap: guest block %08x (%u KB) freed at EIP=%08x", a, size / 1024u, c ? c->eip : 0u);
            return;
        }
}
} // namespace
uint32_t heap_alloc(uint32_t size, bool zero, uint32_t align) {
    const uint32_t a = heap_alloc_side(size, zero, align, false);
    note_large_take(a, size);
    return a;
}
uint32_t heap_alloc_kit(uint32_t size, bool zero, uint32_t align) {
    if (g_kit_hi) {
        if (uint32_t a = heap_alloc_side(size, zero, align, true))
            return a;
        LOGW("heap: the kit span is full; a %u-byte kit object goes in the shared heap", size);
    }
    return heap_alloc_side(size, zero, align, false);
}
namespace {
uint32_t heap_alloc_side(uint32_t size, bool zero, uint32_t align, bool kit) {
    if (!g_blocks)
        return 0;
    if (!size_is_sane(size)) {
        LOGW("heap_alloc: refusing a %u byte request, the arena is %u bytes", size,
             HEAP_ARENA_BYTES);
        // Oversized requests often originate in a corrupted guest length.
        // Use this thread's register file, including worker-thread callers.
        if (const X86 *c = guest_current_context()) {
            LOGW("heap_alloc: EIP=%08x EAX=%08x ECX=%08x EDX=%08x EBX=%08x "
                 "ESP=%08x EBP=%08x ESI=%08x EDI=%08x",
                 c->eip, c->r[R_EAX], c->r[R_ECX], c->r[R_EDX], c->r[R_EBX], c->r[R_ESP],
                 c->r[R_EBP], c->r[R_ESI], c->r[R_EDI]);
            auto chain = win32_return_chain(c->r[R_EBP], 12);
            for (size_t i = 0; i < chain.size(); ++i)
                LOGW("  frame %zu returns to %08x", i, chain[i]);
            // Frameless RTL helpers do not appear in the EBP chain. Include
            // stack words preceded by a CALL, as the exception diagnostic does.
            auto candidates = win32_stack_return_candidates(c->r[R_ESP], 0x400, loader_image_base(),
                                                            loader_image_limit(), 24);
            std::string line;
            for (uint32_t ret : candidates) {
                char word[16];
                snprintf(word, sizeof word, " %08x", ret);
                line += word;
            }
            if (!line.empty())
                LOGW("  return addresses on the stack, newest first:%s", line.c_str());
        }
        return 0;
    }
    if (align < 16)
        align = 16;
    uint32_t need = align_up(size ? size : 1, 16);

    for (uint32_t start : *g_free) {
        if (kit_side(start) != kit)
            continue; // the other side of the kit span's edge
        auto it = g_blocks->find(start);
        if (it == g_blocks->end() || it->second.used)
            continue; // index out of step
        uint32_t len = it->second.size;
        uint32_t user = align_up(start, align);
        if (user < start)
            continue; // overflow
        uint32_t pad = user - start;
        if (pad > len || need > len - pad)
            continue; // does not fit

        uint32_t tail = len - pad - need;
        drop_block(it);
        if (pad)
            put_block(start, pad, 0, false);
        put_block(user, need, size, true);
        if (tail)
            put_block(user + need, tail, 0, false);
        if (zero)
            memset(g_mem + user, 0, need);
        ++g_total_allocs;
        return user;
    }
    if (!kit)
        LOGW("heap_alloc: out of guest heap (%u bytes requested)", size);
    return 0;
}
} // namespace

bool heap_owns(uint32_t addr) {
    if (!g_blocks)
        return false;
    auto it = g_blocks->find(addr);
    return it != g_blocks->end() && it->second.used;
}

uint32_t heap_size(uint32_t addr) {
    if (!g_blocks)
        return 0xffffffffu;
    auto it = g_blocks->find(addr);
    if (it == g_blocks->end() || !it->second.used)
        return 0xffffffffu;
    return it->second.req;
}

bool heap_free(uint32_t addr) {
    if (!g_blocks || !addr)
        return false;
    auto it = g_blocks->find(addr);
    if (it == g_blocks->end() || !it->second.used) {
        LOGW("heap_free: %08x is not a live allocation", addr);
        return false;
    }
    if (it->second.req >= kLargeBlock)
        note_large_free(addr, it->second.req);
    it->second.used = false;
    it->second.req = 0;
    ++g_total_frees;

    // RECOMP_HEAP_QUARANTINE=1 retires a block instead of returning it: the
    // address is never handed out again, and no neighbour absorbs it either.
    // A guest that goes on using memory it has already freed then reads its
    // own dead object rather than whatever was allocated over the top of it,
    // which is the difference between a fault that names the culprit and one
    // that does not. Off by default, and a run with it on gives up every byte
    // it frees, so it is a diagnostic rather than a way to play.
    static const bool quarantine = recomp_env("HEAP_QUARANTINE") != nullptr;
    if (quarantine)
        return true;
    g_free->insert(addr);

    // Coalesce with the following block.
    auto next = std::next(it);
    if (next != g_blocks->end() && !next->second.used &&
        next->first == it->first + it->second.size && same_side(it->first, next->first)) {
        it->second.size += next->second.size;
        drop_block(next);
    }
    // Coalesce with the preceding block.
    if (it != g_blocks->begin()) {
        auto prev = std::prev(it);
        if (!prev->second.used && prev->first + prev->second.size == it->first && same_side(prev->first, it->first)) {
            prev->second.size += it->second.size;
            drop_block(it);
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// Guarded blocks (NFSMW GitHub issue 8). A player's game crashed two seconds
// in, every time, because the 16-byte COM view of its first DirectSound buffer
// had been written over: the view sat between the end of that buffer's sample
// ring and the bottom of the sound thread's stack, both regions the guest
// writes in bulk, and the first-fit heap packs them edge to edge. On Windows
// each lives in its own allocation with slack around it, so the same stray
// write lands on nothing. The gaps give it something harmless to land on here
// and say where it went.
// ---------------------------------------------------------------------------
namespace {
const uint8_t kGuardFill = 0xfd;
struct GuardRec {
    uint32_t size;  // usable bytes
    uint32_t guard; // bytes on each side
    const char *what;
    uint32_t tag;
    uint32_t hits;
};
std::map<uint32_t, GuardRec> *g_guards;
uint64_t g_guard_hits;

void guard_reset() {
    delete g_guards;
    g_guards = nullptr;
    g_guard_hits = 0;
}


bool guard_enabled() {
    static const bool on = [] {
        const char *v = recomp_env("HEAP_GUARD");
        return !(v && v[0] == '0');
    }();
    return on;
}

// First and last damaged byte of [p, p+n), or false when all hold the fill.
bool guard_damage(const uint8_t *p, uint32_t n, uint32_t *first, uint32_t *last,
                  uint32_t *count) {
    uint32_t f = n, l = 0, k = 0;
    for (uint32_t i = 0; i < n; i += 8) {
        uint64_t w;
        if (n - i >= 8) {
            memcpy(&w, p + i, 8);
            if (w == 0xfdfdfdfdfdfdfdfdull)
                continue;
        }
        for (uint32_t j = i; j < n && j < i + 8; ++j)
            if (p[j] != kGuardFill) {
                if (f == n)
                    f = j;
                l = j;
                ++k;
            }
    }
    *first = f;
    *last = l;
    *count = k;
    return k != 0;
}

bool guard_check_rec(uint32_t user, GuardRec &r) {
    const uint8_t *below = g_mem + user - r.guard;
    const uint8_t *above = g_mem + user + r.size;
    uint32_t bf, bl, bn, af, al, an;
    bool hit_below = guard_damage(below, r.guard, &bf, &bl, &bn);
    bool hit_above = guard_damage(above, r.guard, &af, &al, &an);
    if (!hit_below && !hit_above)
        return true;
    ++r.hits;
    ++g_guard_hits;
    if (r.hits == 1) {
        char bytes[3 * 16 + 1] = "";
        const uint8_t *s = hit_above ? above + af : below + bf;
        uint32_t avail = hit_above ? r.guard - af : r.guard - bf;
        for (uint32_t i = 0; i < 16 && i < avail; ++i)
            snprintf(bytes + 3 * i, 4, "%02x ", s[i]);
        char sides[160] = "";
        int n = 0;
        if (hit_above)
            n += snprintf(sides + n, sizeof sides - n, "%u bytes after its end (+%u..+%u)", an, af, al);
        if (hit_below)
            snprintf(sides + n, sizeof sides - n, "%s%u bytes before its start (-%u..-%u)",
                     hit_above ? ", " : "", bn, r.guard - bl, r.guard - bf);
        LOGW("heap guard: %s %u at %08x (%u bytes) was written outside its span: %s; first bytes %s"
             "; the gap took them, nothing next to it was touched",
             r.what, r.tag, user, r.size, sides, bytes);
        if (const X86 *c = guest_current_context())
            LOGW("heap guard: seen by guest code at EIP=%08x ESP=%08x", c->eip, c->r[R_ESP]);
    }
    memset(g_mem + user - r.guard, kGuardFill, r.guard);
    memset(g_mem + user + r.size, kGuardFill, r.guard);
    return false;
}
} // namespace

uint32_t heap_alloc_guarded(uint32_t size, bool zero, uint32_t guard, const char *what,
                            uint32_t tag) {
    if (!guard_enabled() || !guard)
        return heap_alloc(size, zero, 16);
    guard = align_up(guard, 16);
    uint64_t total = (uint64_t)size + 2ull * guard;
    if (!size_is_sane(size) || total > 0xffffffffull)
        return heap_alloc(size, zero, 16); // refused there, with its diagnostics
    uint32_t base = heap_alloc_kit((uint32_t)total, false, 16);
    if (!base)
        return 0;
    uint32_t user = base + guard;
    memset(g_mem + base, kGuardFill, guard);
    if (zero)
        memset(g_mem + user, 0, size);
    memset(g_mem + user + size, kGuardFill, guard);
    if (!g_guards)
        g_guards = new std::map<uint32_t, GuardRec>();
    (*g_guards)[user] = GuardRec{size, guard, what, tag, 0};
    return user;
}

bool heap_free_guarded(uint32_t addr) {
    if (!addr)
        return false;
    if (readonly_release(addr)) // a read-only page: opened and taken out of the table first
        return heap_free(addr);
    if (g_guards) {
        auto it = g_guards->find(addr);
        if (it != g_guards->end()) {
            guard_check_rec(addr, it->second);
            if (it->second.hits > 1)
                LOGW("heap guard: %s %u at %08x: its gaps were found written %u times in all",
                     it->second.what, it->second.tag, addr, it->second.hits);
            uint32_t base = addr - it->second.guard;
            g_guards->erase(it);
            return heap_free(base);
        }
    }
    return heap_free(addr);
}

bool heap_guard_check(uint32_t addr) {
    if (!g_guards || !addr)
        return true;
    auto it = g_guards->find(addr);
    return it == g_guards->end() || guard_check_rec(addr, it->second);
}

uint32_t heap_guard_check_all() {
    if (!g_guards)
        return 0;
    uint32_t bad = 0;
    for (auto &e : *g_guards)
        if (!guard_check_rec(e.first, e.second))
            ++bad;
    return bad;
}

uint64_t heap_guard_hits() {
    return g_guard_hits;
}

// Resize a guest heap allocation in place when possible, otherwise copy into a new block.
// Reject arena-sized overflow requests and leave the original allocation live if growth fails.
uint32_t heap_realloc(uint32_t addr, uint32_t new_size, bool zero) {
    if (!addr)
        return heap_alloc(new_size, zero);
    if (!g_blocks)
        return 0;
    if (!size_is_sane(new_size)) {
        LOGW("heap_realloc: refusing a %u byte request, the arena is %u bytes", new_size,
             HEAP_ARENA_BYTES);
        return 0;
    }
    auto it = g_blocks->find(addr);
    if (it == g_blocks->end() || !it->second.used) {
        LOGW("heap_realloc: %08x is not a live allocation", addr);
        return 0;
    }
    uint32_t old_req = it->second.req;
    uint32_t need = align_up(new_size ? new_size : 1, 16);
    uint32_t have = it->second.size;

    if (need <= have) {
        uint32_t tail = have - need;
        if (tail >= 16) {
            it->second.size = need;
            auto next = std::next(it);
            if (next != g_blocks->end() && !next->second.used && next->first == addr + have &&
                same_side(addr, next->first)) {
                uint32_t merged = tail + next->second.size;
                drop_block(next);
                put_block(addr + need, merged, 0, false);
            } else {
                put_block(addr + need, tail, 0, false);
            }
        }
        it->second.req = new_size;
        if (zero && new_size > old_req)
            memset(g_mem + addr + old_req, 0, new_size - old_req);
        return addr;
    }

    // Try to grow into a free neighbour.
    auto next = std::next(it);
    if (next != g_blocks->end() && !next->second.used && next->first == addr + have &&
        same_side(addr, next->first) && have + next->second.size >= need) {
        uint32_t total = have + next->second.size;
        drop_block(next);
        uint32_t tail = total - need;
        it->second.size = need;
        it->second.req = new_size;
        if (tail)
            put_block(addr + need, tail, 0, false);
        if (zero)
            memset(g_mem + addr + old_req, 0, new_size - old_req);
        return addr;
    }

    uint32_t fresh = kit_side(addr) ? heap_alloc_kit(new_size, false, 16) : heap_alloc(new_size, false);
    if (!fresh)
        return 0;
    uint32_t copy = old_req < new_size ? old_req : new_size;
    memmove(g_mem + fresh, g_mem + addr, copy);
    if (zero && new_size > copy)
        memset(g_mem + fresh + copy, 0, new_size - copy);
    heap_free(addr);
    return fresh;
}

HeapStats heap_stats() {
    HeapStats s{};
    s.total_allocs = g_total_allocs;
    s.total_frees = g_total_frees;
    if (!g_blocks)
        return s;
    for (auto &kv : *g_blocks) {
        if (kv.second.used) {
            ++s.used_blocks;
            s.used_bytes += kv.second.size;
            s.req_bytes += kv.second.req;
        } else {
            ++s.free_blocks;
            s.free_bytes += kv.second.size;
            if (kv.second.size > s.largest_free)
                s.largest_free = kv.second.size;
        }
    }
    return s;
}

// Check that the heap block map covers the arena without gaps, overlap or adjacent free blocks.
// Return the first invariant failure, or an empty string when the allocation structure is consistent.
std::string heap_check() {
    if (!g_blocks)
        return "heap not initialised";
    uint32_t cursor = HEAP_BASE;
    bool prev_free = false;
    bool extra_seen = false;
    char buf[160];
    for (auto &kv : *g_blocks) {
        if (GUEST_HEAP_EXTRA_SIZE && cursor == HEAP_LIMIT && !extra_seen) {
            cursor = GUEST_HEAP_EXTRA_BASE;
            extra_seen = true;
            prev_free = false;
        }
        if (kv.first != cursor) {
            snprintf(buf, sizeof buf, "gap or overlap at %08x (expected %08x)", kv.first, cursor);
            return buf;
        }
        if (kv.second.size == 0 || (kv.second.size & 15)) {
            snprintf(buf, sizeof buf, "block %08x has bad size %u", kv.first, kv.second.size);
            return buf;
        }
        if (kv.first == g_kit_lo && g_kit_hi)
            prev_free = false; // the kit span's lower edge keeps its free block apart (3.0.4)
        if (!kv.second.used && prev_free) {
            snprintf(buf, sizeof buf, "adjacent free blocks at %08x", kv.first);
            return buf;
        }
        prev_free = !kv.second.used;
        cursor += kv.second.size;
    }
    const uint32_t end =
        GUEST_HEAP_EXTRA_SIZE ? GUEST_HEAP_EXTRA_BASE + GUEST_HEAP_EXTRA_SIZE : HEAP_LIMIT;
    if (cursor != end) {
        snprintf(buf, sizeof buf, "heap ends at %08x, expected %08x", cursor, end);
        return buf;
    }
    for (uint32_t a : *g_free) {
        auto it = g_blocks->find(a);
        if (it == g_blocks->end() || it->second.used) {
            snprintf(buf, sizeof buf, "free index holds %08x, which is not a free block", a);
            return buf;
        }
    }
    size_t free_count = 0;
    for (auto &kv : *g_blocks)
        if (!kv.second.used)
            ++free_count;
    if (free_count != g_free->size()) {
        snprintf(buf, sizeof buf, "free index has %zu entries, the heap has %zu free blocks",
                 g_free->size(), free_count);
        return buf;
    }
    return std::string();
}

// ---------------------------------------------------------------------------
// Read-only guest pages (NFSMW GitHub issue 8, 3.0.3). The 3.0.2 log showed the
// 16-byte COM view of the game's DirectSound buffer written over between two
// untouched 16 KB gaps: not an overrun but a write aimed at the object itself,
// from code nobody has found by reading (the game's driver only calls through
// it). The view now sits alone on its own sealed page. The fault handler steps
// over each store to it, so the view stays what the kit wrote and the game goes
// on, and records where the store came from for the log.
//
// SIGNAL SAFETY: the hook only reads preallocated tables and the guest arena,
// and writes plain fields and atomics; no allocation, no lock, no stdio.
// ---------------------------------------------------------------------------
namespace {
constexpr int kReadonlyPages = 32;
struct ReadonlyPage {
    std::atomic<uint32_t> addr{0}; // guest address (page-aligned); 0 = free slot
    uint32_t bytes = 0;
    const char *what = "";
    uint32_t tag = 0;
    bool sealed = false;
};
ReadonlyPage g_readonly[kReadonlyPages];
constexpr uint32_t kReadonlyRecords = 8;
struct ReadonlyRecord {
    uint32_t page, offset, eip, esp, ebp, ret[6];
    uint64_t pc, lr;
    int slot;
};
ReadonlyRecord g_readonly_record[kReadonlyRecords];
std::atomic<uint32_t> g_readonly_hits{0}, g_readonly_logged{0};
std::atomic<uint64_t> g_readonly_last_pc{0};
std::atomic<uint32_t> g_readonly_same_pc{0};

int readonly_fault(uint64_t addr, void *uc_) {
#if defined(__APPLE__) && defined(__aarch64__)
    if (!g_mem)
        return 0;
    const uint64_t base = (uint64_t)(uintptr_t)g_mem;
    if (addr < base || addr >= base + (uint64_t)GUEST_SIZE)
        return 0;
    const uint32_t ga = (uint32_t)(addr - base);
    int slot = -1;
    for (int i = 0; i < kReadonlyPages; ++i) {
        const uint32_t a = g_readonly[i].addr.load(std::memory_order_acquire);
        if (a && g_readonly[i].sealed && ga >= a && ga - a < g_readonly[i].bytes) {
            slot = i;
            break;
        }
    }
    if (slot < 0)
        return 0;
    ucontext_t *uc = (ucontext_t *)uc_;
    if (!uc || !uc->uc_mcontext)
        return 0;
    // Only a write is ours to stop (EC 0x24/0x25 = data abort, ISS bit 6 = WnR); a read of a sealed page cannot fault.
    const uint64_t esr = uc->uc_mcontext->__es.__esr;
    const uint64_t ec = (esr >> 26) & 0x3f;
    if ((ec != 0x24 && ec != 0x25) || !(esr & (1u << 6)))
        return 0;
    const uint64_t pc = (uint64_t)__darwin_arm_thread_state64_get_pc(uc->uc_mcontext->__ss);
    // A store that faults again and again from one place (an exclusive-store loop) must not spin for ever: after 1000
    // in a row the page is opened and the store goes through, as it did before 3.0.3.
    if (g_readonly_last_pc.exchange(pc) == pc) {
        if (g_readonly_same_pc.fetch_add(1) > 1000) {
            g_readonly[slot].sealed = false;
            os_vm_set_readonly(g_mem + g_readonly[slot].addr.load(), g_readonly[slot].bytes, 0);
            return 1;
        }
    } else {
        g_readonly_same_pc.store(0);
    }
    const uint32_t n = g_readonly_hits.fetch_add(1);
    if (n < kReadonlyRecords) {
        ReadonlyRecord &r = g_readonly_record[n];
        memset(&r, 0, sizeof r);
        r.slot = slot;
        r.page = g_readonly[slot].addr.load();
        r.offset = ga - r.page;
        r.pc = pc;
        r.lr = (uint64_t)__darwin_arm_thread_state64_get_lr(uc->uc_mcontext->__ss);
        if (const X86 *c = guest_current_context()) {
            r.eip = c->eip;
            r.esp = c->r[R_ESP];
            r.ebp = c->r[R_EBP];
            uint32_t fp = r.ebp;
            for (int k = 0; k < 6 && fp >= 0x1000 && fp <= GUEST_SIZE - 8; ++k) {
                uint32_t next, ret;
                memcpy(&next, g_mem + fp, 4);
                memcpy(&ret, g_mem + fp + 4, 4);
                r.ret[k] = ret;
                if (next <= fp)
                    break;
                fp = next;
            }
        }
    }
    // Step over the store: every AArch64 instruction is 4 bytes, and this one only wrote (to a page that refused it).
    __darwin_arm_thread_state64_set_pc_fptr(uc->uc_mcontext->__ss, (void *)(uintptr_t)(pc + 4));
    return 1;
#else
    (void)addr;
    (void)uc_;
    return 0;
#endif
}

void readonly_reset() {
    for (auto &p : g_readonly) {
        p.addr.store(0);
        p.sealed = false;
    }
    g_readonly_hits.store(0);
    g_readonly_logged.store(0);
    g_readonly_last_pc.store(0);
    g_readonly_same_pc.store(0);
    os_set_write_fault_hook(readonly_fault);
}

int readonly_slot(uint32_t addr) {
    for (int i = 0; i < kReadonlyPages; ++i)
        if (addr && g_readonly[i].addr.load() == addr)
            return i;
    return -1;
}

bool readonly_release(uint32_t addr) {
    const int slot = readonly_slot(addr);
    if (slot < 0)
        return false;
    g_readonly[slot].sealed = false;
    os_vm_set_readonly(g_mem + addr, g_readonly[slot].bytes, 0);
    g_readonly[slot].addr.store(0);
    return true;
}
} // namespace

bool heap_readonly_enabled() {
#if defined(__APPLE__) && defined(__aarch64__)
    // 3.0.4: opt-in (RECOMP_READONLY_PAGES=1). Stopping a guest store keeps a kit object intact but leaves the
    // guest's own data half-written (issue 8: the game then hung); the kit span is the fix, the seal a probe. Read
    // on every call (only when a view is made), so a test can turn it on.
    const char *v = recomp_env("READONLY_PAGES");
    return v && v[0] == '1';
#else
    return false;
#endif
}

uint32_t heap_alloc_readonly(uint32_t size, const char *what, uint32_t tag) {
    if (!heap_readonly_enabled() || !size)
        return 0;
    const uint32_t page = (uint32_t)os_vm_page_size();
    const uint32_t bytes = align_up(size, page);
    int slot = -1;
    for (int i = 0; i < kReadonlyPages && slot < 0; ++i)
        if (!g_readonly[i].addr.load())
            slot = i;
    if (slot < 0)
        return 0; // table full: the caller allocates as before
    const uint32_t a = heap_alloc_kit(bytes, true, page);
    if (!a)
        return 0;
    g_readonly[slot].bytes = bytes;
    g_readonly[slot].what = what;
    g_readonly[slot].tag = tag;
    g_readonly[slot].sealed = false;
    g_readonly[slot].addr.store(a, std::memory_order_release);
    return a;
}

bool heap_seal_readonly(uint32_t addr) {
    const int slot = readonly_slot(addr);
    if (slot < 0)
        return false;
    if (os_vm_set_readonly(g_mem + addr, g_readonly[slot].bytes, 1) != 0)
        return false;
    g_readonly[slot].sealed = true;
    return true;
}

uint32_t heap_readonly_hits() {
    return g_readonly_hits.load();
}

void heap_readonly_report() {
    const uint32_t hits = g_readonly_hits.load(std::memory_order_acquire);
    uint32_t logged = g_readonly_logged.load();
    if (hits == logged)
        return;
    g_readonly_logged.store(hits);
    const uint64_t image = os_image_load();
    for (uint32_t n = logged; n < hits && n < kReadonlyRecords; ++n) {
        const ReadonlyRecord &r = g_readonly_record[n];
        const ReadonlyPage &p = g_readonly[r.slot];
        LOGW("heap: a write to the %s %u at %08x+%u was stopped (the object stays as the kit made it): guest EIP=%08x "
             "ESP=%08x EBP=%08x, returns %08x %08x %08x %08x %08x %08x; host pc image+%llx lr image+%llx",
             p.what, p.tag, r.page, r.offset, r.eip, r.esp, r.ebp, r.ret[0], r.ret[1], r.ret[2], r.ret[3], r.ret[4],
             r.ret[5], (unsigned long long)(image && r.pc > image ? r.pc - image : r.pc),
             (unsigned long long)(image && r.lr > image ? r.lr - image : r.lr));
    }
    if (hits > kReadonlyRecords)
        LOGW("heap: %u writes to read-only kit objects stopped so far (the first %u are logged)", hits,
             kReadonlyRecords);
}
