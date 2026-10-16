#include <atomic>
#include "seh.h"
#include "profile.h"
#include "game_config.h"
// kernel32.cpp - KERNEL32 shims: heap, files, modules, TLS, sync objects,
// time and the string/locale helpers the CRT startup calls.
//
// The guest file system is rooted at RECOMP_GUEST_ROOT, which maps onto the
// directory holding the loaded EXE (original/gog by default). Lookups are
// case-insensitive and '\' is translated to '/'.
#include "imports.h"
#include "kernel32_internal.h"
#include "windows_version.h"
#include "mods_seam.h"
#include "display_seam.h"
#include "frame_deadline.h"
#include "memory.h"
#include "win32.h"
#include "native_seam.h"
#include "loader.h"

#include <errno.h>
#include "../platform/os.h"
#include "../platform/ab_phase.h"

#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <map>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <algorithm>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// Win32 constants
// ---------------------------------------------------------------------------
enum {
    ERROR_SUCCESS_ = 0,
    ERROR_FILE_NOT_FOUND_ = 2,
    ERROR_PATH_NOT_FOUND_ = 3,
    ERROR_ACCESS_DENIED_ = 5,
    ERROR_INVALID_HANDLE_ = 6,
    ERROR_NOT_ENOUGH_MEMORY_ = 8,
    ERROR_NO_MORE_FILES_ = 18,
    ERROR_HANDLE_EOF_ = 38,
    ERROR_ALREADY_EXISTS_ = 183,
    ERROR_CALL_NOT_IMPLEMENTED_ = 120,
};
static const uint32_t INVALID_HANDLE_VALUE_ = 0xffffffffu;
static const uint32_t FILE_ATTRIBUTE_READONLY_ = 0x001;
static const uint32_t FILE_ATTRIBUTE_DIRECTORY_ = 0x010;
static const uint32_t FILE_ATTRIBUTE_ARCHIVE_ = 0x020;
static const uint32_t FILE_ATTRIBUTE_NORMAL_ = 0x080;

namespace {

// ---------------------------------------------------------------------------
// Process-wide state
// ---------------------------------------------------------------------------
// The developer checkout's game directory: RECOMP_DEVELOPER_EXE without its file name.
std::string g_game_dir =
    std::string(RECOMP_DEVELOPER_EXE).substr(0, std::string(RECOMP_DEVELOPER_EXE).rfind('/'));
std::string g_cur_dir = RECOMP_GUEST_ROOT;
uint32_t g_last_error = 0;
jmp_buf g_exit_jmp;
bool g_exit_jmp_valid = false;
bool g_exited = false;
uint32_t g_exit_code = 0;
uint32_t g_unhandled_filter = 0;
uint32_t g_cmdline_addr = 0;
uint32_t g_envblock_addr = 0;
uint32_t g_envblockw_addr = 0;
bool g_tls_used[TLS_SLOTS] = {false};
uint32_t g_process_heap = 0;

// ---------------------------------------------------------------------------
// Handles
// ---------------------------------------------------------------------------
enum HKind {
    H_NONE,
    H_FILE,
    H_FIND,
    H_SEM,
    H_EVENT,
    H_MUTEX,
    H_THREAD,
    H_HEAP,
    H_MAPPING,
    H_MODULE,
    H_STD
};

struct HObj {
    HKind kind = H_NONE;
    std::string object_name; // Named events, mutexes and mappings share a kernel object.
    uint32_t references = 1;
    int fd = -1;      // H_FILE, H_MAPPING
    std::string path; // H_FILE, H_MAPPING, H_MODULE
    // H_FILE opened for writing on an existing file: the open went through
    // the read tier, and the write tier is resolved by the first write. See
    // file_promote_for_write.
    bool write_pending = false;
    int write_flags = 0;    // the open flags the caller asked for
    std::string guest_name; // what to resolve again, as the caller spelled it
    // H_FIND
    std::vector<std::string> matches;
    // The host path each match actually came from. A listing can merge tiers,
    // so a match's metadata has to be read from its own file rather than from
    // one directory shared by all of them.
    std::vector<std::string> match_paths;
    size_t find_pos = 0;
    std::string find_dir;
    // H_SEM / H_EVENT / H_MUTEX
    int32_t count = 0;
    int32_t max_count = 0;
    bool signalled = false;
    bool manual_reset = false;
    // A waitable timer is an event that time sets: at timer_due (sched_now
    // seconds) while armed, and again every timer_period seconds after.
    bool timer = false;
    bool timer_armed = false;
    double timer_due = 0.0, timer_period = 0.0;
    // A mutex is owned, recursively, by one thread at a time. Ownership is why
    // a mutex cannot be answered with a plain "signalled" flag once guest
    // threads interleave: the owner may re-enter it, nobody else may.
    uint32_t owner_tid = 0;
    uint32_t owner_recursion = 0;
    // A mutex whose owner ended without releasing it is abandoned: the next
    // waiter still gets it, but is told with WAIT_ABANDONED that whatever the
    // owner was protecting may be half written.
    bool abandoned = false;
    // H_THREAD
    uint32_t exit_code = 0;
    uint32_t thread_start = 0;
    uint32_t thread_param = 0;
    // A service thread the runtime runs for itself (see k32_spawn_service_thread): its body is native code
    // run with the thread's own guest context and the scheduler baton, instead of guest code at thread_start.
    void (*native_start)(X86 *, uint32_t) = nullptr;
    bool thread_suspended = false;
    bool thread_ran = false;
    // H_MAPPING
    uint32_t map_size = 0;
    uint32_t map_view = 0;
};

std::map<uint32_t, HObj> &handles() {
    static std::map<uint32_t, HObj> m;
    return m;
}
uint32_t g_next_handle = 0x00010004;

// The scheduler's lock (defined with the scheduler below). The handle table is
// read under it by threads that do not hold the baton: a woken waiter re-checks
// its wait (sched_expire_locked) before it is handed the baton. So every insert
// and erase takes it too; a tree rebalanced or a node freed under that reader
// was the SND DAC thread's start-up crash (review/run22/snd-crash.md). Callers
// never hold it: these run from guest imports and from start-up.
extern std::mutex g_sched_m;

uint32_t handle_new(HKind kind) {
    std::lock_guard<std::mutex> l(g_sched_m);
    uint32_t h = g_next_handle;
    g_next_handle += 4;
    handles()[h].kind = kind;
    return h;
}
void handle_erase(uint32_t h) {
    std::lock_guard<std::mutex> l(g_sched_m);
    handles().erase(h);
}
HObj *handle_get(uint32_t h, HKind kind) {
    auto it = handles().find(h);
    if (it == handles().end() || it->second.kind != kind)
        return nullptr;
    return &it->second;
}
HObj *handle_any(uint32_t h) {
    auto it = handles().find(h);
    return it == handles().end() ? nullptr : &it->second;
}

// A/W names share one object namespace. Repeated opens retain the object until
// every returned handle is closed; initial state only applies on first creation.
bool reuse_named_object(X86 *c, const std::string &name, HKind kind) {
    if (name.empty())
        return false;
    for (auto &entry : handles()) {
        HObj &o = entry.second;
        if (o.object_name != name)
            continue;
        if (o.kind != kind) {
            set_last_error(ERROR_INVALID_HANDLE_);
            set_eax(c, 0);
            return true;
        }
        ++o.references;
        set_last_error(ERROR_ALREADY_EXISTS_);
        set_eax(c, entry.first);
        return true;
    }
    return false;
}

// Cooperative guest threads, defined further down. Declared here because the
// waits, Sleep and TLS all have to ask which thread is running and be able to
// hand the baton on.
const uint32_t MAXIMUM_WAIT_OBJECTS_ = 64;

struct GuestThread;
GuestThread *cur_thread();
bool guest_yield();
uint32_t cur_tls_base();
uint32_t cur_thread_id();
// Blocking primitives, implemented on the scheduler further down. Every one of
// them deschedules the caller rather than spinning or inventing an answer.
void sched_sleep_ms(uint32_t ms);
uint32_t sched_wait_objects(const uint32_t *handles, uint32_t count, bool wait_all,
                            uint32_t timeout_ms);
void sched_enter_critsec(uint32_t cs);
bool sched_try_critsec(uint32_t cs);
void sched_leave_critsec(uint32_t cs);
void sched_wake_all();

struct VmRegion {
    uint32_t size = 0;
    bool committed = false;
};
std::map<uint32_t, VmRegion> &vm_regions() {
    static std::map<uint32_t, VmRegion> m;
    return m;
}

// Pseudo module handles. IMAGE_BASE is the EXE itself.
std::map<std::string, uint32_t> &modules() {
    static std::map<std::string, uint32_t> m;
    return m;
}
uint32_t g_next_module = 0x60000000;

std::string lower(std::string s) {
    for (char &ch : s)
        ch = (char)tolower((unsigned char)ch);
    return s;
}

// ---------------------------------------------------------------------------
// Path mapping
// ---------------------------------------------------------------------------
std::map<std::string, std::map<std::string, std::string>> &dir_cache() {
    static std::map<std::string, std::map<std::string, std::string>> m;
    return m;
}

int collect_listing(const char *name, void *user) {
    auto *entries = (std::map<std::string, std::string> *)user;
    entries->emplace(lower(name), name);
    return 0;
}

const std::map<std::string, std::string> &listing(const std::string &dir) {
    auto it = dir_cache().find(dir);
    if (it != dir_cache().end())
        return it->second;
    std::map<std::string, std::string> entries;
    os_listdir(dir.c_str(), collect_listing, &entries);
    return dir_cache().emplace(dir, std::move(entries)).first->second;
}

std::vector<std::string> split_path(const std::string &p) {
    std::vector<std::string> out;
    std::string cur;
    for (char ch : p) {
        if (ch == '/' || ch == '\\') {
            if (!cur.empty())
                out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(ch);
        }
    }
    if (!cur.empty())
        out.push_back(cur);
    return out;
}

} // namespace

void win32_invalidate_dir_cache() {
    dir_cache().clear();
}

const std::string &win32_game_dir() {
    return g_game_dir;
}

// Called with the guest baton held, like the file shims. Reopening this path
// gives a host service an independent descriptor without exposing the guest's.
bool win32_file_handle_position(uint32_t handle, std::string *host_path, int64_t *offset) {
    HObj *o = handle_get(handle, H_FILE);
    if (!o || o->fd < 0)
        return false;
    int64_t pos = os_fd_seek(o->fd, 0, OS_SEEK_CUR);
    if (pos < 0)
        return false;
    if (host_path)
        *host_path = o->path;
    if (offset)
        *offset = pos;
    return true;
}

// The overlay seam. Null in an unmodded build, which is every build until the
// mod foundation installs one.
static int (*g_file_resolve)(const char *, int, char *, size_t) = nullptr;
static void (*g_file_list)(const char *, void (*)(void *, const char *, const char *),
                           void *) = nullptr;

void win32_set_file_ops(int (*resolve)(const char *, int, char *, size_t),
                        void (*list)(const char *, void (*)(void *, const char *, const char *),
                                     void *)) {
    g_file_resolve = resolve;
    g_file_list = list;
    // A different resolver answers differently for the same guest path, so
    // everything remembered about the old one has to go.
    win32_invalidate_dir_cache();
}

// The path a guest gave, reduced to components: drive stripped, the current
// directory applied when it was relative, "." and ".." resolved, and a leading
// root component dropped so <root>\data\x and \data\x are the same file.
static std::vector<std::string> normalise_components(const std::string &guest_path) {
    std::string p = guest_path;
    bool absolute = false;
    if (p.size() >= 2 && p[1] == ':') {
        p = p.substr(2);
        absolute = true;
    }
    if (!p.empty() && (p[0] == '\\' || p[0] == '/'))
        absolute = true;

    std::vector<std::string> comps;
    if (!absolute) {
        std::string base = g_cur_dir;
        if (base.size() >= 2 && base[1] == ':')
            base = base.substr(2);
        comps = split_path(base);
    }
    for (const std::string &c : split_path(p))
        comps.push_back(c);

    std::vector<std::string> norm;
    for (const std::string &c : comps) {
        if (c == ".")
            continue;
        if (c == "..") {
            if (!norm.empty())
                norm.pop_back();
            continue;
        }
        norm.push_back(c);
    }
    // An absolute path spelled through the guest root - which GetModuleFileNameA
    // hands out, and which is more than one component for a game installed
    // under C:\GOG Games\<name> - is the same file as the root-relative one.
    static const std::vector<std::string> root = [] {
        std::string r = RECOMP_GUEST_ROOT;
        if (r.size() >= 2 && r[1] == ':')
            r = r.substr(2);
        return split_path(r);
    }();
    bool under_root = norm.size() >= root.size() && !root.empty();
    for (size_t i = 0; under_root && i < root.size(); ++i)
        under_root = lower(norm[i]) == lower(root[i]);
    if (under_root)
        norm.erase(norm.begin(), norm.begin() + (long)root.size());
    else if (!norm.empty() && lower(norm[0]) == lower(win32_guest_root_name()))
        norm.erase(norm.begin());
    return norm;
}

// The one serializer for a normalised relative path. Both the resolver and
// the lister are given exactly this, so a listing and an open agree about
// what path they are talking about, including at the guest root - which is
// the empty string for both, never ".". The contract says the resolver never
// sees a "." component, and the root is the one place it would appear.
static std::string serialise_relative(const std::vector<std::string> &norm) {
    std::string rel;
    for (size_t i = 0; i < norm.size(); ++i) {
        if (i)
            rel += "/";
        rel += norm[i];
    }
    return rel;
}

std::string normalised_relative(const std::string &guest_path) {
    return serialise_relative(normalise_components(guest_path));
}

// The case-insensitive walk through the game directory, unchanged.
static std::string resolve_in_game_dir(const std::vector<std::string> &norm, bool for_create) {
    std::string host = g_game_dir;
    for (size_t i = 0; i < norm.size(); ++i) {
        std::string next = host + "/" + norm[i];
        OsStat st;
        if (os_stat(next.c_str(), &st) == 0) {
            host = next;
            continue;
        }
        const auto &entries = listing(host);
        auto it = entries.find(lower(norm[i]));
        if (it != entries.end()) {
            host = host + "/" + it->second;
            continue;
        }
        if (for_create && i + 1 == norm.size())
            return next;
        return std::string();
    }
    return host;
}

std::string win32_host_path_op(const std::string &guest_path, int op) {
    if (guest_path.empty())
        return std::string();
    std::vector<std::string> norm = normalise_components(guest_path);
    if (g_file_resolve) {
        std::string rel = serialise_relative(norm);
        char out[1024];
        if (g_file_resolve(rel.c_str(), op, out, sizeof out))
            return std::string(out);
        // No answer. A read falls through to the game directory; a mutation
        // does NOT - refusing is the whole point, and silently writing into
        // the GOG folder is the failure this seam exists to prevent.
        if (op != WIN32_FILE_READ && op != WIN32_FILE_LIST)
            return std::string();
    }
    // A destination that does not exist yet is the normal case for both a
    // rename and a create, so both permit a missing final component. Without
    // this an unmodded MoveFileA or CopyFileA to a new filename fails, which
    // is a behaviour change and not one this seam is allowed to make.
    bool may_create = (op == WIN32_FILE_WRITE || op == WIN32_FILE_RENAME_DST);
    return resolve_in_game_dir(norm, may_create);
}

extern "C" int recomp_writable_path(const char *guest_path, char *out, size_t out_len) {
    // Only the resolver's answer: without an overlay a write would fall
    // through to the game directory, which a native override must not touch.
    if (!guest_path || !out || !out_len || !g_file_resolve)
        return 0;
    const std::string rel = serialise_relative(normalise_components(guest_path));
    return g_file_resolve(rel.c_str(), WIN32_FILE_WRITE, out, out_len) ? 1 : 0;
}
extern "C" int recomp_readable_path(const char *guest_path, char *out, size_t out_len) {
    if (!guest_path || !out || !out_len)
        return 0;
    const std::string path = win32_host_path_op(guest_path, WIN32_FILE_READ);
    if (path.empty() || path.size() >= out_len)
        return 0;
    memcpy(out, path.c_str(), path.size() + 1);
    return 1;
}

std::string win32_host_path(const std::string &guest_path, bool for_create) {
    return win32_host_path_op(guest_path, for_create ? WIN32_FILE_WRITE : WIN32_FILE_READ);
}

std::string win32_guest_path(const std::string &host_path) {
    std::string rel = host_path;
    if (rel.compare(0, g_game_dir.size(), g_game_dir) == 0)
        rel = rel.substr(g_game_dir.size());
    std::string out = RECOMP_GUEST_ROOT;
    for (const std::string &c : split_path(rel)) {
        out += "\\";
        out += c;
    }
    return out;
}

void set_last_error(uint32_t code) {
    g_last_error = code;
}
uint32_t get_last_error() {
    return g_last_error;
}

jmp_buf *process_exit_jmp() {
    g_exit_jmp_valid = true;
    return &g_exit_jmp;
}
bool process_exited() {
    return g_exited;
}
uint32_t process_exit_code() {
    return g_exit_code;
}

uint32_t win32_create_event(bool manual_reset, bool signalled) {
    uint32_t h = handle_new(H_EVENT);
    handles()[h].manual_reset = manual_reset;
    handles()[h].signalled = signalled;
    return h;
}

bool win32_reset_event(uint32_t handle) {
    HObj *o = handle_get(handle, H_EVENT);
    if (!o)
        return false;
    o->signalled = false;
    return true;
}

bool win32_signal_event(uint32_t handle, bool pulse) {
    HObj *o = handle_get(handle, H_EVENT);
    if (!o)
        return false;
    o->signalled = !pulse; // a pulse releases a waiter and leaves it unsignalled
    return true;
}

uint32_t tls_reserve_slot() {
    for (uint32_t i = 0; i < TLS_SLOTS; ++i)
        if (!g_tls_used[i]) {
            g_tls_used[i] = true;
            return i;
        }
    return 0xffffffffu;
}

uint32_t loader_tls_block_for_thread(uint32_t tls_array) {
    const LoaderTls &t = loader_tls();
    if (t.index == 0xffffffffu)
        return 0;
    uint32_t raw = t.raw_end - t.raw_start;
    uint64_t bytes = (uint64_t)raw + t.zero_fill + 16;
    if (bytes > 0xffffffffu)
        return 0;
    uint32_t block = heap_alloc((uint32_t)bytes, true, 16);
    if (!block)
        return 0;
    memcpy(g_mem + block, g_mem + t.raw_start, raw);
    wr32(tls_array + 4 * t.index, block);
    return block;
}

void win32_init(const std::string &game_dir) {
    kernel32_wide_reset();
    g_game_dir = game_dir.empty() ? std::string(".") : game_dir;
    g_cur_dir = RECOMP_GUEST_ROOT;
    g_last_error = 0;
    g_exited = false;
    g_exit_code = 0;
    g_cmdline_addr = 0;
    g_envblock_addr = 0;
    g_envblockw_addr = 0;
    for (uint32_t i = 0; i < TLS_SLOTS; ++i)
        g_tls_used[i] = false;
    {
        std::lock_guard<std::mutex> l(g_sched_m);
        handles().clear();
    }
    modules().clear();
    vm_regions().clear();
    dir_cache().clear();
    g_next_handle = 0x00010004;
    modules()[lower(std::string(RECOMP_EXECUTABLE))] = IMAGE_BASE;
    g_process_heap = handle_new(H_HEAP);
    // Standard handles exist from the start.
    for (int i = 0; i < 3; ++i) {
        uint32_t h = handle_new(H_STD);
        handles()[h].fd = i;
    }
    registry_load();
}

namespace {

// FILETIME for a unix timestamp.
uint64_t filetime(time_t t) {
    return ((uint64_t)t + 11644473600ull) * 10000000ull;
}

void put_filetime(uint32_t addr, int64_t t) {
    uint64_t ft = filetime(t);
    wr32(addr, (uint32_t)ft);
    wr32(addr + 4, (uint32_t)(ft >> 32));
}

uint32_t attrs_for(const OsStat &st) {
    uint32_t a = st.is_dir ? FILE_ATTRIBUTE_DIRECTORY_ : FILE_ATTRIBUTE_ARCHIVE_;
    if (st.is_readonly)
        a |= FILE_ATTRIBUTE_READONLY_;
    return a;
}

bool wildcard_match(const char *pat, const char *str) {
    if (*pat == '\0')
        return *str == '\0';
    if (*pat == '*') {
        for (const char *s = str;; ++s) {
            if (wildcard_match(pat + 1, s))
                return true;
            if (!*s)
                return false;
        }
    }
    if (*str == '\0')
        return false;
    if (*pat == '?' || tolower((unsigned char)*pat) == tolower((unsigned char)*str))
        return wildcard_match(pat + 1, str + 1);
    return false;
}

void fill_find_data(uint32_t addr, const std::string &host_path, const std::string &name,
                    bool wide) {
    memset(g_mem + addr, 0, wide ? 592 : 320);
    OsStat st{};
    if (os_stat(host_path.c_str(), &st) == 0) {
        wr32(addr + 0, attrs_for(st));
        put_filetime(addr + 4, st.ctime);
        put_filetime(addr + 12, st.atime);
        put_filetime(addr + 20, st.mtime);
        wr32(addr + 28, (uint32_t)(st.size >> 32));
        wr32(addr + 32, (uint32_t)st.size);
    } else {
        wr32(addr + 0, FILE_ATTRIBUTE_NORMAL_);
    }
    if (wide) {
        gm_put_wstr(addr + 44, name, 260);
        gm_put_wstr(addr + 564, name, 14);
    } else {
        gm_put_str(addr + 44, name.c_str(), 260);
        gm_put_str(addr + 304, name.c_str(), 14);
    }
}

uint32_t guest_strdup(const char *s) {
    uint32_t n = (uint32_t)strlen(s) + 1;
    uint32_t a = heap_alloc(n, true);
    if (a)
        memcpy(g_mem + a, s, n);
    return a;
}

// -------------------------------------------------------------------------
// Heap / memory
// -------------------------------------------------------------------------
void k_HeapCreate(X86 *c) {
    set_eax(c, handle_new(H_HEAP));
}
void k_HeapDestroy(X86 *c) {
    handle_erase(arg(c, 0));
    set_eax(c, 1);
}

void k_HeapAlloc(X86 *c) {
    uint32_t flags = arg(c, 1), size = arg(c, 2);
    set_eax(c, heap_alloc(size, (flags & 8) != 0));
}
void k_HeapReAlloc(X86 *c) {
    uint32_t flags = arg(c, 1), ptr = arg(c, 2), size = arg(c, 3);
    set_eax(c, heap_realloc(ptr, size, (flags & 8) != 0));
}
void k_HeapFree(X86 *c) {
    set_eax(c, heap_free(arg(c, 2)) ? 1 : 0);
}
void k_HeapSize(X86 *c) {
    uint32_t sz = heap_size(arg(c, 2));
    set_eax(c, sz);
}

void k_GlobalAlloc(X86 *c) {
    uint32_t flags = arg(c, 0), size = arg(c, 1);
    set_eax(c, heap_alloc(size, (flags & 0x40) != 0)); // GMEM_ZEROINIT
}
void k_GlobalReAlloc(X86 *c) {
    set_eax(c, heap_realloc(arg(c, 0), arg(c, 1), (arg(c, 2) & 0x40) != 0));
}
void k_GlobalFree(X86 *c) {
    set_eax(c, heap_free(arg(c, 0)) ? 0 : arg(c, 0));
}
void k_GlobalLock(X86 *c) {
    set_eax(c, arg(c, 0));
}
void k_GlobalUnlock(X86 *c) {
    set_eax(c, 0);
}
void k_LocalAlloc(X86 *c) {
    uint32_t flags = arg(c, 0), size = arg(c, 1);
    set_eax(c, heap_alloc(size, (flags & 0x40) != 0));
}
void k_LocalFree(X86 *c) {
    set_eax(c, heap_free(arg(c, 0)) ? 0 : arg(c, 0));
}

// Virtual regions are whole 4 KB pages, base and size, so two allocations can
// never share a page. Commit state is tracked per region: freshly committed
// pages read as zero, a commit over already committed pages leaves them alone,
// and a decommit discards the contents.
// The region containing `addr`, or nullptr.
std::map<uint32_t, VmRegion>::iterator vm_find(uint32_t addr) {
    auto &m = vm_regions();
    auto it = m.upper_bound(addr);
    if (it == m.begin())
        return m.end();
    --it;
    if (addr >= it->first && addr < it->first + it->second.size)
        return it;
    return m.end();
}

void k_VirtualAlloc(X86 *c) {
    uint32_t addr = arg(c, 0), size = arg(c, 1), type = arg(c, 2);
    if (size > 0xfffff000u) {
        set_last_error(ERROR_NOT_ENOUGH_MEMORY_);
        set_eax(c, 0);
        return;
    }
    uint32_t pages = (size + 4095u) & ~4095u;
    if (!pages)
        pages = 4096;

    if (addr) {
        auto it = vm_find(addr);
        if (it != vm_regions().end()) {
            if (type & 0x1000) { // MEM_COMMIT
                if (!it->second.committed) {
                    // First commit of these pages: they must read as zero.
                    memset(g_mem + it->first, 0, it->second.size);
                    it->second.committed = true;
                }
                // Already committed: the contents stay exactly as they are.
            }
            set_eax(c, addr);
            return;
        }
        LOGV("VirtualAlloc: ignoring requested base %08x, no region there", addr);
    }

    uint32_t base = heap_alloc(pages, true, 4096);
    if (!base) {
        set_last_error(ERROR_NOT_ENOUGH_MEMORY_);
        set_eax(c, 0);
        return;
    }
    VmRegion r;
    r.size = pages;
    r.committed = (type & 0x1000) != 0;
    vm_regions()[base] = r;
    set_eax(c, base);
}

void k_VirtualFree(X86 *c) {
    uint32_t addr = arg(c, 0), size = arg(c, 1), type = arg(c, 2);
    auto it = vm_find(addr);
    if (type & 0x8000) { // MEM_RELEASE
        // Win32 releases a whole reservation and only from its base address,
        // with dwSize zero. Nothing is untracked unless the release succeeds,
        // or a later decommit of the still-live region would fail.
        if (it == vm_regions().end() || addr != it->first || size != 0) {
            set_last_error(87); // ERROR_INVALID_PARAMETER
            set_eax(c, 0);
            return;
        }
        if (!heap_free(addr)) {
            set_last_error(87);
            set_eax(c, 0);
            return;
        }
        vm_regions().erase(it);
        set_eax(c, 1);
        return;
    }
    // MEM_DECOMMIT keeps the reservation but the pages lose their contents.
    if (it == vm_regions().end()) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    uint32_t avail = it->first + it->second.size - addr;
    uint32_t n = size ? ((size + 4095u) & ~4095u) : avail;
    if (n > avail)
        n = avail;
    memset(g_mem + addr, 0, n);
    if (!size || n == it->second.size)
        it->second.committed = false;
    set_eax(c, 1);
}

// -------------------------------------------------------------------------
// Files
// -------------------------------------------------------------------------
// A handle opened for writing on an existing file went through the read tier
// (see k_CreateFileA); the first byte written brings it to the write tier:
// the path is resolved again as a WRITE - which is where an overlay copies the
// file up - and the handle continues there at the offset it had reached.
// False when the write tier refuses, which the caller reports as denied.
static bool file_promote_for_write(HObj *o) {
    if (!o->write_pending)
        return true;
    std::string host = win32_host_path_op(o->guest_name, WIN32_FILE_WRITE);
    if (host.empty())
        return false;
    int64_t pos = os_fd_seek(o->fd, 0, OS_SEEK_CUR);
    int fd = os_fd_open(host.c_str(), o->write_flags | OS_O_CREAT);
    if (fd < 0)
        return false;
    if (pos > 0)
        os_fd_seek(fd, pos, OS_SEEK_SET);
    os_fd_close(o->fd);
    o->fd = fd;
    o->path = host;
    o->write_pending = false;
    win32_invalidate_dir_cache();
    LOGV("CreateFileA(%s): first write, now %s", o->guest_name.c_str(), host.c_str());
    return true;
}

void k_CreateFileA(X86 *c) {
    create_file_named(c, gm_str(arg(c, 0)));
}

void k_ReadFile(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_FILE);
    uint32_t buf = arg(c, 1), want = arg(c, 2), pread = arg(c, 3);
    if (!o) {
        if (handle_get(arg(c, 0), H_STD)) {
            if (pread)
                wr32(pread, 0);
            set_eax(c, 1);
            return;
        }
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    if (!gm_valid(buf, want)) {
        set_last_error(ERROR_ACCESS_DENIED_);
        set_eax(c, 0);
        return;
    }
    int64_t n = os_fd_read(o->fd, g_mem + buf, want);
    if (recomp_env("TRACE_FILES"))
        LOGW("file: read handle=%08x want=%u got=%lld", arg(c, 0), want, (long long)n);
    if (n < 0) {
        set_last_error(ERROR_ACCESS_DENIED_);
        if (pread)
            wr32(pread, 0);
        set_eax(c, 0);
        return;
    }
    if (pread)
        wr32(pread, (uint32_t)n);
    set_eax(c, 1);
}

void k_WriteFile(X86 *c) {
    uint32_t h = arg(c, 0), buf = arg(c, 1), want = arg(c, 2), pwrote = arg(c, 3);
    HObj *o = handle_any(h);
    if (o && o->kind == H_STD) {
        FILE *to = o->fd == 2 ? stderr : stdout;
        fwrite(g_mem + buf, 1, want, to);
        // A guest that writes to its console and then ends the process - an
        // abort in the runtime, ExitProcess - has said something worth
        // keeping, so it is not left in a buffer.
        fflush(to);
        if (pwrote)
            wr32(pwrote, want);
        set_eax(c, 1);
        return;
    }
    if (!o || o->kind != H_FILE) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    if (!file_promote_for_write(o)) {
        set_last_error(ERROR_ACCESS_DENIED_);
        set_eax(c, 0);
        return;
    }
    int64_t n = os_fd_write(o->fd, g_mem + buf, want);
    if (n < 0) {
        set_last_error(ERROR_ACCESS_DENIED_);
        set_eax(c, 0);
        return;
    }
    if (pwrote)
        wr32(pwrote, (uint32_t)n);
    set_eax(c, 1);
}

void k_SetFilePointer(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_FILE);
    int32_t dist = (int32_t)arg(c, 1);
    uint32_t phigh = arg(c, 2), method = arg(c, 3);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    int64_t off = dist;
    if (phigh)
        off |= ((int64_t)(int32_t)rd32(phigh)) << 32;
    int whence = method == 1 ? OS_SEEK_CUR : method == 2 ? OS_SEEK_END : OS_SEEK_SET;
    int64_t pos = os_fd_seek(o->fd, off, whence);
    if (pos < 0) {
        set_last_error(ERROR_ACCESS_DENIED_);
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    if (phigh)
        wr32(phigh, (uint32_t)((uint64_t)pos >> 32));
    set_eax(c, (uint32_t)pos);
}

void k_GetFileSize(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_FILE);
    uint32_t phigh = arg(c, 1);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    OsStat st{};
    if (os_fd_stat(o->fd, &st) != 0) {
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    if (phigh)
        wr32(phigh, (uint32_t)(st.size >> 32));
    set_eax(c, (uint32_t)st.size);
}

void k_CloseHandle(X86 *c) {
    uint32_t h = arg(c, 0);
    HObj *o = handle_any(h);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    if (--o->references) {
        set_eax(c, 1);
        return;
    }
    if ((o->kind == H_FILE || o->kind == H_MAPPING) && o->fd >= 0)
        os_fd_close(o->fd);
    handle_erase(h);
    set_eax(c, 1);
}

void k_FlushFileBuffers(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_FILE);
    if (o)
        os_fd_fsync(o->fd);
    set_eax(c, 1);
}

void k_SetEndOfFile(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_FILE);
    if (!o || !file_promote_for_write(o)) {
        set_eax(c, 0);
        return;
    }
    int64_t pos = os_fd_seek(o->fd, 0, OS_SEEK_CUR);
    set_eax(c, os_fd_truncate(o->fd, pos) == 0 ? 1 : 0);
}

void k_GetFileType(X86 *c) {
    HObj *o = handle_any(arg(c, 0));
    if (!o) {
        set_eax(c, 0);
        return;
    } // FILE_TYPE_UNKNOWN
    set_eax(c, o->kind == H_STD ? 2 : 1); // CHAR : DISK
}

void k_GetFileAttributesA(X86 *c) {
    get_file_attributes_named(c, gm_str(arg(c, 0)));
}

void k_SetFileAttributesA(X86 *c) {
    set_file_attributes_named(c, gm_str(arg(c, 0)));
}

void k_CreateDirectoryA(X86 *c) {
    create_directory_named(c, gm_str(arg(c, 0)));
}

void k_RemoveDirectoryA(X86 *c) {
    remove_directory_named(c, gm_str(arg(c, 0)));
}

void k_DeleteFileA(X86 *c) {
    delete_file_named(c, gm_str(arg(c, 0)));
}

void k_MoveFileA(X86 *c) {
    std::string from = win32_host_path_op(gm_str(arg(c, 0)), WIN32_FILE_RENAME_SRC);
    std::string to = win32_host_path_op(gm_str(arg(c, 1)), WIN32_FILE_RENAME_DST);
    if (from.empty() || to.empty()) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    int rc = os_rename(from.c_str(), to.c_str());
    win32_invalidate_dir_cache();
    set_eax(c, rc == 0 ? 1 : 0);
}

void k_CopyFileA(X86 *c) {
    copy_file_named(c, gm_str(arg(c, 0)), gm_str(arg(c, 1)));
}

// Open a guest file enumeration using the same normalized paths as file access.
// Return the first match in the Win32 find-data layout and retain the remaining matches in a handle.
void k_FindFirstFileA(X86 *c) {
    find_first_named(c, gm_str(arg(c, 0)), false);
}

void k_FindNextFileA(X86 *c) {
    find_next(c, false);
}

void k_FindClose(X86 *c) {
    handle_erase(arg(c, 0));
    set_eax(c, 1);
}

void k_GetFullPathNameA(X86 *c) {
    std::string name = gm_str(arg(c, 0));
    uint32_t len = arg(c, 1), buf = arg(c, 2), pfile = arg(c, 3);
    std::string full = full_path_named(name);
    if (buf && len) {
        uint32_t n = gm_put_str(buf, full.c_str(), len);
        if (pfile) {
            size_t s = full.find_last_of('\\');
            wr32(pfile, s == std::string::npos ? buf : buf + (uint32_t)s + 1);
        }
        set_eax(c, n);
    } else {
        set_eax(c, (uint32_t)full.size() + 1);
    }
}

void k_GetCurrentDirectoryA(X86 *c) {
    uint32_t len = arg(c, 0), buf = arg(c, 1);
    if (!buf || len == 0) {
        set_eax(c, (uint32_t)g_cur_dir.size() + 1);
        return;
    }
    set_eax(c, gm_put_str(buf, g_cur_dir.c_str(), len));
}

void k_SetCurrentDirectoryA(X86 *c) {
    std::string want = gm_str(arg(c, 0));
    std::string host = win32_host_path(want);
    if (host.empty()) {
        set_last_error(ERROR_PATH_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    if (want.size() >= 2 && want[1] == ':')
        g_cur_dir = want;
    else if (!want.empty() && (want[0] == '\\' || want[0] == '/'))
        g_cur_dir = "C:" + want;
    else
        g_cur_dir = g_cur_dir + "\\" + want;
    set_eax(c, 1);
}

// Both encodings expose the same guest path, never a host filesystem path.
std::string module_file_name(uint32_t hmod) {
    std::string path = RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE;
    if (hmod && hmod != IMAGE_BASE) {
        for (const auto &kv : modules())
            if (kv.second == hmod) {
                path = RECOMP_GUEST_ROOT "\\" + kv.first;
                break;
            }
    }
    return path;
}

void k_GetModuleFileNameA(X86 *c) {
    std::string path = module_file_name(arg(c, 0));
    set_eax(c, gm_put_str(arg(c, 1), path.c_str(), arg(c, 2)));
}

void k_GetModuleFileNameW(X86 *c) {
    std::string path = module_file_name(arg(c, 0));
    set_eax(c, gm_put_wstr(arg(c, 1), path, arg(c, 2)));
}

void load_library_named(X86 *c, const std::string &module_name);

std::string library_name(const std::string &module_name) {
    std::string name = lower(module_name);
    if (name.find('\\') != std::string::npos || name.find('/') != std::string::npos)
        name = name.substr(name.find_last_of("\\/") + 1);
    if (name.find('.') == std::string::npos)
        name += ".dll";
    return name;
}

void get_module_handle_named(X86 *c, const std::string &module_name) {
    std::string name = library_name(module_name);
    // A handle lookup must neither attach a mapped DLL nor acquire a reference.
    if (LoaderModule *m = loader_module_named(name.c_str())) {
        if (!m->attached)
            set_last_error(126); // ERROR_MOD_NOT_FOUND
        set_eax(c, m->attached ? m->base : 0);
        return;
    }
    auto it = modules().find(name);
    if (it != modules().end()) {
        set_eax(c, it->second);
        return;
    }
    // Registered DLLs are already available to static imports. Materialize
    // their pseudo handles through the same path as an explicit LoadLibrary.
    load_library_named(c, module_name);
}

void k_GetModuleHandleA(X86 *c) {
    uint32_t p = arg(c, 0);
    if (!p) {
        set_eax(c, IMAGE_BASE);
        return;
    }
    get_module_handle_named(c, gm_str(p));
}

void k_GetModuleHandleW(X86 *c) {
    uint32_t p = arg(c, 0);
    if (!p) {
        set_eax(c, IMAGE_BASE);
        return;
    }
    get_module_handle_named(c, gm_wstr(p));
}

// A module handle is only handed out for a DLL the runtime has shims for.
// Anything else fails the way a missing DLL does, so the guest takes its own
// "feature unavailable" path instead of calling into nothing.
bool runtime_serves_module(const std::string &lower_name) {
    return imports_has_dll(lower_name.c_str());
}

// Mapped auxiliary code stays in the arena, but its Win32 lifetime follows
// LoadLibrary/FreeLibrary. The main executable never enters this DLL path.
uint32_t call_dll_entry(X86 *c, const LoaderModule &m, uint32_t reason) {
    if (!m.entry || recomp_module_lookup(m.entry) < 0) {
        LOGV("%s: DLL entry %08x is not translated, not run", m.name.c_str(), m.entry);
        return 1;
    }
    uint32_t result = guest_call(c, m.entry, m.base, reason, 0);
    LOGV("%s: DLL entry %08x reason %u returned %08x", m.name.c_str(), m.entry, reason, result);
    return result;
}

void load_library_named(X86 *c, const std::string &module_name) {
    std::string name = library_name(module_name);
    auto it = modules().find(name);
    if (it != modules().end()) {
        set_eax(c, it->second);
        return;
    }
    // Restore post-IAT bytes before each fresh attach, just as remapping the
    // original DLL would. Never restore the main image or run its EXE entry.
    if (LoaderModule *m = loader_module_named(name.c_str())) {
        if (m->base == loader_image_base()) {
            set_eax(c, m->base);
            return;
        }
        if (!m->attached) {
            memcpy(g_mem + m->base, m->initial_image.data(), m->initial_image.size());
            m->attached = true;
            m->load_refs = 1;
            if (!call_dll_entry(c, *m, 1)) { // DLL_PROCESS_ATTACH
                call_dll_entry(c, *m, 0);    // failed attach still receives detach
                m->attached = false;
                m->load_refs = 0;
                set_last_error(1114); // ERROR_DLL_INIT_FAILED
                set_eax(c, 0);
                return;
            }
        } else {
            ++m->load_refs;
        }
        set_eax(c, m->base);
        return;
    }
    if (!runtime_serves_module(name)) {
        log_once(("loadlib:" + name).c_str(),
                 "LoadLibrary(\"%s\"): no shims for that module, reporting it as missing",
                 name.c_str());
        set_last_error(126); // ERROR_MOD_NOT_FOUND
        set_eax(c, 0);
        return;
    }
    uint32_t h = g_next_module;
    g_next_module += 0x10000;
    modules()[name] = h;
    LOGV("LoadLibrary(\"%s\") -> pseudo module %08x", name.c_str(), h);
    set_eax(c, h);
}

void k_LoadLibraryA(X86 *c) {
    load_library_named(c, gm_str(arg(c, 0)));
}

void k_LoadLibraryW(X86 *c) {
    load_library_named(c, gm_wstr(arg(c, 0)));
}

void k_LoadLibraryExW(X86 *c) {
    // File and flags are ignored: registered shim tables do not map a real DLL.
    k_LoadLibraryW(c);
}

void k_FreeLibrary(X86 *c) {
    uint32_t handle = arg(c, 0);
    if (const LoaderModule *mapped = loader_module_containing(handle)) {
        LoaderModule *m = loader_module_named(mapped->name.c_str());
        if (handle != m->base || handle == loader_image_base() || !m->load_refs) {
            set_last_error(ERROR_INVALID_HANDLE_);
            set_eax(c, 0);
            return;
        }
        if (--m->load_refs == 0) {
            call_dll_entry(c, *m, 0); // DLL_PROCESS_DETACH, before discarding globals
            m->attached = false;
        }
    }
    // Shim-only libraries have process lifetime and no guest entry point.
    set_eax(c, 1);
}

void k_GetProcAddress(X86 *c) {
    uint32_t hmod = arg(c, 0);
    uint32_t name = arg(c, 1);
    bool ordinal = name <= 0xffff;
    std::string proc = ordinal ? "#" + std::to_string(name) : gm_str(name);
    if (const LoaderModule *m = loader_module_containing(hmod)) {
        uint32_t a = hmod == m->base ? (ordinal ? loader_module_export_ordinal(*m, name)
                                                : loader_module_export(*m, proc.c_str()))
                                     : 0;
        if (!a)
            log_once(("gpa:" + m->name + "!" + proc).c_str(),
                     "GetProcAddress(%s, \"%s\") -> 0 (no such export)", m->name.c_str(),
                     proc.c_str());
        set_eax(c, a);
        return;
    }
    std::string dll;
    for (const auto &kv : modules())
        if (kv.second == hmod) {
            dll = kv.first;
            break;
        }
    uint32_t a = dll.empty() ? 0 : imports_resolve(dll.c_str(), proc.c_str());
    if (!a) {
        log_once(("gpa:" + dll + "!" + proc).c_str(),
                 "GetProcAddress(%s, \"%s\") -> 0 (no shim registered)",
                 dll.empty() ? "?" : dll.c_str(), proc.c_str());
    }
    set_eax(c, a);
}

// -------------------------------------------------------------------------
// File mappings
// -------------------------------------------------------------------------
void k_CreateFileMappingA(X86 *c) {
    create_mapping_named(c, gm_str(arg(c, 5)));
}

void k_MapViewOfFile(X86 *c) {
    HObj *m = handle_get(arg(c, 0), H_MAPPING);
    uint32_t off_low = arg(c, 3), bytes = arg(c, 4);
    if (!m) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    uint32_t n = bytes ? bytes : (m->map_size - off_low);
    uint32_t addr = heap_alloc(n, true, 4096);
    if (!addr) {
        set_last_error(ERROR_NOT_ENOUGH_MEMORY_);
        set_eax(c, 0);
        return;
    }
    os_fd_seek(m->fd, off_low, OS_SEEK_SET);
    int64_t got = os_fd_read(m->fd, g_mem + addr, n);
    if (got < 0)
        got = 0;
    m->map_view = addr;
    LOGV("MapViewOfFile(%s, %u bytes) -> %08x", m->path.c_str(), n, addr);
    set_eax(c, addr);
}

void k_UnmapViewOfFile(X86 *c) {
    // Views are copies: nothing is written back. Log if the guest expected a
    // shared, writable mapping.
    heap_free(arg(c, 0));
    set_eax(c, 1);
}

// -------------------------------------------------------------------------
// Modules, environment, misc process state
// -------------------------------------------------------------------------
// The command line is the executable's path, plus the switches RECOMP_GUEST_ARGS
// names: a game's own -debugout or -nointro, the way its players and its
// developers steered it.  Read once, when the CRT first asks.
void k_GetCommandLineA(X86 *c) {
    get_command_line(c);
}
} // namespace
void win32_reset_command_line_for_test() {
    kernel32_wide_reset_command_line();
    g_cmdline_addr = 0;
}
namespace {

// The environment block is a run of NUL-terminated strings ended by an extra
// NUL. GetEnvironmentStringsW must hand back UTF-16, not the ANSI bytes.
static const char *const k_environment[] = {"PATH=" RECOMP_GUEST_ROOT, "windir=C:\\WINDOWS",
                                            nullptr};

void k_GetEnvironmentStrings(X86 *c) {
    if (!g_envblock_addr || !heap_owns(g_envblock_addr)) {
        uint32_t bytes = 1;
        for (int i = 0; k_environment[i]; ++i)
            bytes += (uint32_t)strlen(k_environment[i]) + 1;
        uint32_t a = heap_alloc(bytes, true);
        uint32_t at = a;
        for (int i = 0; a && k_environment[i]; ++i)
            at += gm_put_str(at, k_environment[i], 0x1000) + 1;
        g_envblock_addr = a;
    }
    set_eax(c, g_envblock_addr);
}

void k_GetEnvironmentStringsW(X86 *c) {
    if (!g_envblockw_addr || !heap_owns(g_envblockw_addr)) {
        uint32_t chars = 1;
        for (int i = 0; k_environment[i]; ++i)
            chars += (uint32_t)strlen(k_environment[i]) + 1;
        uint32_t a = heap_alloc(chars * 2, true);
        uint32_t at = a;
        for (int i = 0; a && k_environment[i]; ++i) {
            const char *s = k_environment[i];
            for (uint32_t j = 0; s[j]; ++j) {
                wr16(at, (uint16_t)(uint8_t)s[j]);
                at += 2;
            }
            wr16(at, 0);
            at += 2;
        }
        g_envblockw_addr = a;
    }
    set_eax(c, g_envblockw_addr);
}

void k_FreeEnvironmentStrings(X86 *c) {
    set_eax(c, 1);
}
void k_SetEnvironmentVariableA(X86 *c) {
    set_eax(c, 1);
}

void k_GetStartupInfoA(X86 *c) {
    startup_info(c);
}

void k_GetSystemInfo(X86 *c) {
    uint32_t p = arg(c, 0);
    if (!p) {
        set_eax(c, 0);
        return;
    }
    memset(g_mem + p, 0, 36);
    wr32(p + 0, 0);           // wProcessorArchitecture = INTEL, wReserved
    wr32(p + 4, 4096);        // dwPageSize
    wr32(p + 8, IMAGE_BASE);  // lpMinimumApplicationAddress
    wr32(p + 12, HEAP_LIMIT); // lpMaximumApplicationAddress
    wr32(p + 16, 1);          // dwActiveProcessorMask
    wr32(p + 20, 1);          // dwNumberOfProcessors
    wr32(p + 24, 586);        // dwProcessorType = PROCESSOR_INTEL_PENTIUM
    wr32(p + 28, 4096);       // dwAllocationGranularity is 65536 on Win32
    wr32(p + 28, 65536);
    wr32(p + 32, (5 << 8) | 6); // wProcessorLevel / wProcessorRevision
    set_eax(c, 0);
}

void k_GetProcessHeap(X86 *c) {
    set_eax(c, g_process_heap);
}

// OSVERSIONINFO(A|W), including the eight-byte EX suffix when requested.
void get_version_info(X86 *c, bool wide) {
    uint32_t p = arg(c, 0);
    uint32_t size = p && gm_valid(p, 4) ? rd32(p) : 0;
    uint32_t base = wide ? 276 : 148;
    if ((size != base && size != base + 8) || !gm_valid(p, size)) {
        set_last_error(87 /* ERROR_INVALID_PARAMETER */);
        set_eax(c, 0);
        return;
    }
    memset(g_mem + p + 4, 0, size - 4);
    wr32(p + 4, windows_version::major);
    wr32(p + 8, windows_version::minor);
    wr32(p + 12, windows_version::build);
    wr32(p + 16, windows_version::platform);
    if (wide)
        gm_put_wstr(p + 20, windows_version::csd, 128);
    else
        gm_put_str(p + 20, windows_version::csd, 128);
    if (size > base) {
        wr16(p + base, windows_version::service_pack);
        wr8(p + base + 6, 1); // VER_NT_WORKSTATION
    }
    set_eax(c, 1);
}

void k_GetVersionExA(X86 *c) {
    get_version_info(c, false);
}

void k_GetVersionExW(X86 *c) {
    get_version_info(c, true);
}

void k_GetVersion(X86 *c) {
    set_eax(c, windows_version::packed);
}

void k_GetStdHandle(X86 *c) {
    int32_t which = (int32_t)arg(c, 0);
    int fd = which == -10 ? 0 : which == -11 ? 1 : 2;
    for (auto &kv : handles())
        if (kv.second.kind == H_STD && kv.second.fd == fd) {
            set_eax(c, kv.first);
            return;
        }
    set_eax(c, INVALID_HANDLE_VALUE_);
}
void k_SetStdHandle(X86 *c) {
    set_eax(c, 1);
}
void k_SetHandleCount(X86 *c) {
    set_eax(c, arg(c, 0));
}

void k_OutputDebugStringA(X86 *c) {
    LOGW("OutputDebugString: %s", gm_str(arg(c, 0)).c_str());
    set_eax(c, 0);
}

void k_GetLastError(X86 *c) {
    set_eax(c, g_last_error);
}
void k_SetLastError(X86 *c) {
    g_last_error = arg(c, 0);
    set_eax(c, 0);
}

void k_IsBadReadPtr(X86 *c) {
    uint32_t p = arg(c, 0), n = arg(c, 1);
    set_eax(c, (p && gm_valid(p, n ? n : 1)) ? 0 : 1);
}
void k_IsBadWritePtr(X86 *c) {
    uint32_t p = arg(c, 0), n = arg(c, 1);
    set_eax(c, (p && gm_valid(p, n ? n : 1)) ? 0 : 1);
}
void k_IsBadCodePtr(X86 *c) {
    uint32_t p = arg(c, 0);
    set_eax(c, loader_in_image(p) || imports_is_trampoline(p) ? 0 : 1);
}
// IsProcessorFeaturePresent(feature): FALSE for every feature. Runtime CPU dispatchers (an MSVC 7-era
// math library picks x87, 3DNow! (PF 7) or SSE (PF 6) tables with it) must take the x87 path, which is the
// only one the translator models; this matches CPUID, which advertises FPU|TSC|CMOV and nothing else.
// lstrcmpiA(a, b): case-insensitive ordinal comparison of two NUL-terminated strings, -1/0/1. Windows
// compares by the user locale's word sort; ASCII case folding is what a 2003 game's file and key lookups need.
void k_lstrcmpiA(X86 *c) {
    uint32_t a = arg(c, 0), b = arg(c, 1);
    int r = 0;
    for (uint32_t i = 0;; ++i) {
        int ca = (a && gm_valid(a + i, 1)) ? rd8(a + i) : 0;
        int cb = (b && gm_valid(b + i, 1)) ? rd8(b + i) : 0;
        if (ca >= 'A' && ca <= 'Z')
            ca += 32;
        if (cb >= 'A' && cb <= 'Z')
            cb += 32;
        if (ca != cb) {
            r = ca < cb ? -1 : 1;
            break;
        }
        if (!ca)
            break;
    }
    set_eax(c, (uint32_t)r);
}
void k_IsProcessorFeaturePresent(X86 *c) {
    (void)arg(c, 0);
    set_eax(c, 0);
}

void k_GetVolumeInformationA(X86 *c) {
    volume_information_named(c, gm_str(arg(c, 0)), false);
}

void k_GetDiskFreeSpaceA(X86 *c) {
    disk_free_space(c);
}

void k_GetSystemDirectoryA(X86 *c) {
    static const char dir[] = "C:\\Windows\\System32";
    uint32_t buf = arg(c, 0), size = arg(c, 1);
    uint32_t len = sizeof dir - 1;
    if (size <= len) {
        set_eax(c, len + 1);
        return;
    }
    memcpy(g_mem + buf, dir, len + 1);
    set_eax(c, len);
}

void k_GetLogicalDriveStringsA(X86 *c) {
    logical_drive_strings(c, false);
}

void k_GetDriveTypeA(X86 *c) {
    drive_type_named(c, gm_str(arg(c, 0)));
}

// -------------------------------------------------------------------------
// Time
// -------------------------------------------------------------------------
// Native pacing replaces both draw-loop waits in the pinned executable.
// 0049cfc0 selects the second one when game-state 2 has frame-rate flags set;
// 0049cfe0 supplies its default 60 (or 24/20/14) Hz limit. Neither deadline
// drives simulation: 004a5590 uses 005cd92c/005cd930 and the real clock.
// Return the draw deadline to retire after reading the real host time.
static uint32_t native_frame_clock(X86 *c) {
    using Clock = std::chrono::steady_clock;
    static FrameDeadline deadline;
    static int active = 0;
    const uint32_t caller = rd32(c->r[R_ESP]);
    if (caller != RECOMP_HOOK_FRAME_CLOCK_BEGIN && caller != RECOMP_HOOK_FRAME_CLOCK_WAIT &&
        caller != RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP)
        return 0;
    if (recomp_env("PIN_CLOCK"))
        return 0;
    const int requested = mods_display_fps();
    if (caller == RECOMP_HOOK_FRAME_CLOCK_BEGIN) { // before 1000 / DrawFrameRateLimit
        // Keep DrawFrameRateLimit as the legacy animation cadence. Retiring
        // the old wait below already replaces its deadline; changing this
        // byte would make visual animation inherit the presentation cap.
        deadline.begin(requested, std::chrono::duration_cast<std::chrono::nanoseconds>(
                                      Clock::now().time_since_epoch())
                                      .count());
        active = requested;
    } else if (active && (caller == RECOMP_HOOK_FRAME_CLOCK_WAIT ||
                          caller == RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP)) {
        const auto ms = deadline.wait_ms(
            std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch())
                .count());
        // A bounded cooperative sleep also services input/audio.
        if (ms)
            sched_sleep_ms(ms);
        if (caller == RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP) {
            // The instructions after this wait clamp the measured rendering
            // rate (005ca850) to EDI. Keeping the original 60 here would also
            // double frame-rate-scaled camera/animation motion at 120 Hz.
            c->r[R_EDI] = uint32_t(active);
            return RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE;
        }
        return RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE;
    }
    return 0;
}
void k_GetTickCount(X86 *c) {
    const uint32_t draw_deadline = native_frame_clock(c);
    // The cadence trace's main signal: this game's frame limiter spins here,
    // so the interval between these calls is the shape of the main loop.
    host_note_cadence("GetTickCount");
    const uint32_t now = host_millis();
    // The fractional native deadline has already paced this frame. Retiring
    // only this draw wait prevents the guest's integer-millisecond deadline
    // from adding another wait. Every caller still receives real host time.
    if (draw_deadline)
        wr32(draw_deadline, now);
    set_eax(c, now);
}
// Sleep suspends the caller for at least the interval it asks for, and lets
// everything else run meanwhile. Sleep(0) is the documented "give up the rest
// of my time slice" and only yields.
//
// RECOMP_SLEEP_TRACE=<csv path> (diagnostic; off by default, then one cached test): every guest Sleep on the
// main thread is written with its caller (the return address), the time asked for, the time it took, the
// monotonic clock at its start (frames.csv's render_monotonic_ns base) and up to eight guest dwords listed in
// RECOMP_SLEEP_TRACE_WATCH (comma-separated hex addresses, read after the sleep: e.g. a game's pending-load
// counters). For attributing main-thread waits to their callers (NFSMW streaming hitches, Test115).
bool sleep_trace_on();
void sleep_trace_main(X86 *c, uint32_t ms);
void k_Sleep(X86 *c) {
    if (sleep_trace_on()) {
        sleep_trace_main(c, arg(c, 0));
    } else {
        sched_sleep_ms(arg(c, 0));
    }
    set_eax(c, 0);
}

void put_systemtime(uint32_t p, const struct tm &t, int millis) {
    if (!p)
        return;
    wr16(p + 0, (uint16_t)(t.tm_year + 1900));
    wr16(p + 2, (uint16_t)(t.tm_mon + 1));
    wr16(p + 4, (uint16_t)t.tm_wday);
    wr16(p + 6, (uint16_t)t.tm_mday);
    wr16(p + 8, (uint16_t)t.tm_hour);
    wr16(p + 10, (uint16_t)t.tm_min);
    wr16(p + 12, (uint16_t)t.tm_sec);
    wr16(p + 14, (uint16_t)millis);
}

void k_GetLocalTime(X86 *c) {
    struct tm t{};
    os_localtime((int64_t)time(nullptr), &t);
    put_systemtime(arg(c, 0), t, (int)(host_millis() % 1000));
    set_eax(c, 0);
}

void k_GetSystemTime(X86 *c) {
    struct tm t{};
    os_gmtime((int64_t)time(nullptr), &t);
    put_systemtime(arg(c, 0), t, (int)(host_millis() % 1000));
    set_eax(c, 0);
}

// (lpSystemTime, lpFileTime). The inverse of FileTimeToSystemTime, and the
// one the CRT reaches for when a game asks what time it is in a form it can
// do arithmetic on. It was declared with its argument count and no body, so
// the stack stayed straight and the output buffer kept whatever it held.
//
// The date arithmetic is exact rather than a trip through mktime: mktime
// reads the host's timezone, and a FILETIME is UTC by definition. Windows
// ignores wDayOfWeek on the way in, so this does too.
void k_SystemTimeToFileTime(X86 *c) {
    const uint32_t in = arg(c, 0), out = arg(c, 1);
    if (!in || !out || !gm_valid(in, 16) || !gm_valid(out, 8)) {
        set_last_error(87 /* ERROR_INVALID_PARAMETER */);
        set_eax(c, 0);
        return;
    }
    const int year = rd16(in), month = rd16(in + 2), day = rd16(in + 6);
    const int hour = rd16(in + 8), minute = rd16(in + 10), second = rd16(in + 12);
    const int millis = rd16(in + 14);
    // 1601 is where a FILETIME starts; below it there is nothing to express.
    if (year < 1601 || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 ||
        second > 59 || millis > 999) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    // Days from the civil date, counting from 1970-01-01. The era trick puts
    // the leap day at the end of a 400-year cycle so no special cases remain.
    const int shifted = year - (month <= 2);
    const int era = (shifted >= 0 ? shifted : shifted - 399) / 400;
    const unsigned yoe = (unsigned)(shifted - era * 400);
    const unsigned doy = (unsigned)((153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1);
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    const int64_t days = (int64_t)era * 146097 + (int64_t)doe - 719468;
    const int64_t seconds = days * 86400 + hour * 3600 + minute * 60 + second;
    const uint64_t ft = filetime((time_t)seconds) + (uint64_t)millis * 10000ull;
    wr32(out, (uint32_t)ft);
    wr32(out + 4, (uint32_t)(ft >> 32));
    set_eax(c, 1);
}

void k_GetTimeZoneInformation(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p)
        memset(g_mem + p, 0, 172);
    set_eax(c, 0); // TIME_ZONE_ID_UNKNOWN
}

// -------------------------------------------------------------------------
// TLS, interlocked, critical sections
// -------------------------------------------------------------------------
// A TLS index is process wide but its value is per thread, so allocating and
// freeing one clears the slot in every thread's array while a get or a set
// only ever touches the running thread's.
void tls_clear_slot_everywhere(uint32_t i);

void k_TlsAlloc(X86 *c) {
    uint32_t i = tls_reserve_slot();
    if (i != 0xffffffffu) {
        tls_clear_slot_everywhere(i); // a fresh slot starts at zero
        set_eax(c, i);
        return;
    }
    set_last_error(ERROR_NOT_ENOUGH_MEMORY_);
    set_eax(c, 0xffffffffu); // TLS_OUT_OF_INDEXES
}
void k_TlsGetValue(X86 *c) {
    uint32_t i = arg(c, 0);
    set_eax(c, i < TLS_SLOTS ? rd32(cur_tls_base() + 4 * i) : 0);
}
void k_TlsSetValue(X86 *c) {
    uint32_t i = arg(c, 0);
    if (i < TLS_SLOTS)
        wr32(cur_tls_base() + 4 * i, arg(c, 1));
    set_eax(c, i < TLS_SLOTS ? 1 : 0);
}

void k_TlsFree(X86 *c) {
    uint32_t i = arg(c, 0);
    if (i >= TLS_SLOTS || !g_tls_used[i]) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    g_tls_used[i] = false; // the index is available to TlsAlloc again
    tls_clear_slot_everywhere(i);
    set_eax(c, 1);
}

// One microsecond tick, driven by the same clock as GetTickCount so a guest
// that mixes the two never sees them disagree.
void k_QueryPerformanceFrequency(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p) {
        wr32(p, 1000000);
        wr32(p + 4, 0);
    }
    set_eax(c, 1);
}

// The counter runs at the 1 MHz QueryPerformanceFrequency reports, in real microseconds. A counter that moved in
// 1000-tick (whole millisecond) steps rounded a spin-wait up to the next millisecond: NFS Underground's frame limiter
// (00585b40) spins on this counter until its frame time has passed, and every frame took 17 ms, 58.8 fps instead of
// 60 (run 5, drive d1). The microseconds are the monotonic clock aligned to host_millis() at the first read, which
// is still read every time for its side effects (a boot host's clock poll and ticks), and a reading never goes
// back. Only for a game with game.toml [game] qpc_us = true (NFSMW keeps its released millisecond steps, D-203). A
// pinned clock (deterministic runs) keeps the millisecond steps, as does RECOMP_QPC_MS=1 (kill switch) and the
// earlier phase of an in-run A/B (RECOMP_AB_SWITCHES=qpcfine).
static uint64_t qpc_ticks() {
    const uint64_t ms_ticks = (uint64_t)host_millis() * 1000ull;
    static const bool ms_only = [] {
        const char *e = recomp_env("QPC_MS");
        return e && !strcmp(e, "1");
    }();
    if (!RECOMP_QPC_US || ms_only || host_time_source_is_pinned())
        return ms_ticks;
    static std::atomic<int64_t> offset_us{INT64_MIN};
    static std::atomic<uint64_t> last{0};
    const int64_t now_us = (int64_t)host_guest_clock_us(os_monotonic_ns() / 1000ull);   // held while paused (3.0.4)
    int64_t off = offset_us.load(std::memory_order_relaxed);
    if (off == INT64_MIN) {
        const int64_t want = now_us - (int64_t)ms_ticks;
        if (offset_us.compare_exchange_strong(off, want, std::memory_order_relaxed))
            off = want; // else off holds the value another thread set first
    }
    uint64_t ticks = recomp_ab::old(recomp_ab::QPCFINE) ? ms_ticks
                                                        : (uint64_t)std::max<int64_t>(0, now_us - off);
    uint64_t prev = last.load(std::memory_order_relaxed);
    while (ticks > prev && !last.compare_exchange_weak(prev, ticks, std::memory_order_relaxed))
        ;
    return std::max(ticks, prev);
}

void k_QueryPerformanceCounter(X86 *c) {
    host_note_cadence("QueryPerformanceCounter");
    uint32_t p = arg(c, 0);
    if (p) {
        uint64_t ticks = qpc_ticks();
        wr32(p, (uint32_t)ticks);
        wr32(p + 4, (uint32_t)(ticks >> 32));
    }
    set_eax(c, 1);
}

void k_InterlockedIncrement(X86 *c) {
    uint32_t p = arg(c, 0);
    uint32_t v = rd32(p) + 1;
    wr32(p, v);
    set_eax(c, v);
}
void k_InterlockedDecrement(X86 *c) {
    uint32_t p = arg(c, 0);
    uint32_t v = rd32(p) - 1;
    wr32(p, v);
    set_eax(c, v);
}
void k_InterlockedExchange(X86 *c) {
    uint32_t p = arg(c, 0), v = arg(c, 1);
    uint32_t old = rd32(p);
    wr32(p, v);
    set_eax(c, old);
}
// InterlockedCompareExchange(Destination, Exchange, Comperand): UG2 imports it (run 2). Guest threads run one at a
// time under the scheduler's baton, so a plain read-compare-write is atomic for the guest, as the others above.
void k_InterlockedCompareExchange(X86 *c) {
    uint32_t p = arg(c, 0), v = arg(c, 1), cmp = arg(c, 2);
    uint32_t old = rd32(p);
    if (old == cmp)
        wr32(p, v);
    set_eax(c, old);
}

// CRITICAL_SECTION is 24 bytes. The fields the guest can read are kept where
// Windows keeps them: LockCount at +4, RecursionCount at +8, OwningThread at
// +12. Ownership is not decoration now that guest threads interleave - a yield
// inside a protected section lets another thread reach the same section, and
// only an owner check keeps it out.
void k_InitializeCriticalSection(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p && gm_valid(p, 24)) {
        memset(g_mem + p, 0, 24);
        wr32(p + 4, 0xffffffffu); // LockCount = -1 (unowned)
    }
    set_eax(c, 0);
}
void k_EnterCriticalSection(X86 *c) {
    imports_call_leaves_surfaces();
    sched_enter_critsec(arg(c, 0));
    set_eax(c, 0);
}
void k_TryEnterCriticalSection(X86 *c) {
    imports_call_leaves_surfaces();
    set_eax(c, sched_try_critsec(arg(c, 0)) ? 1 : 0);
}
void k_LeaveCriticalSection(X86 *c) {
    imports_call_leaves_surfaces();
    sched_leave_critsec(arg(c, 0));
    set_eax(c, 0);
}
void k_DeleteCriticalSection(X86 *c) {
    set_eax(c, 0);
}

// -------------------------------------------------------------------------
// Synchronisation objects (single threaded, counted)
// -------------------------------------------------------------------------
void k_CreateSemaphoreA(X86 *c) {
    uint32_t h = handle_new(H_SEM);
    handles()[h].count = (int32_t)arg(c, 1);
    handles()[h].max_count = (int32_t)arg(c, 2);
    LOGV("CreateSemaphoreA(initial=%d max=%d) -> %08x", handles()[h].count, handles()[h].max_count,
         h);
    set_eax(c, h);
}

void k_ReleaseSemaphore(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_SEM);
    int32_t n = (int32_t)arg(c, 1);
    uint32_t pprev = arg(c, 2);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    if (pprev)
        wr32(pprev, (uint32_t)o->count);
    if (o->max_count && o->count + n > o->max_count) {
        set_eax(c, 0);
        return;
    }
    o->count += n;
    set_eax(c, 1);
}

void k_CreateEventA(X86 *c) {
    create_event_named(c, gm_str(arg(c, 3)));
}
void k_ResetEvent(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_EVENT);
    if (o)
        o->signalled = false;
    set_eax(c, o ? 1 : 0);
}
void k_CreateMutexA(X86 *c) {
    create_mutex_named(c, gm_str(arg(c, 2)));
}

// Gives up one level of ownership; at zero the mutex is free and a waiter can
// take it. Releasing a mutex this thread does not own fails, as Windows does.
void k_ReleaseMutex(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_MUTEX);
    if (!o || o->owner_tid != cur_thread_id()) {
        set_last_error(288); // ERROR_NOT_OWNER
        set_eax(c, 0);
        return;
    }
    if (o->owner_recursion)
        --o->owner_recursion;
    if (!o->owner_recursion) {
        o->owner_tid = 0;
        sched_wake_all();
    }
    set_eax(c, 1);
}

void k_SetEvent(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_EVENT);
    if (o) {
        o->signalled = true;
        sched_wake_all();
    }
    set_eax(c, o ? 1 : 0);
}

void k_WaitForSingleObject(X86 *c) {
    uint32_t h = arg(c, 0);
    set_eax(c, sched_wait_objects(&h, 1, false, arg(c, 1)));
}

void k_WaitForMultipleObjects(X86 *c) {
    wait_multiple_objects(c);
}

// -------------------------------------------------------------------------
// Threads.
//
// Plan correction 12 makes this conditional: a long-lived thread gets a
// cooperative context, anything else runs synchronously.  The game has two
// long-lived ones.  Both DirectInput service threads (0052c880 keyboard,
// 0052cd40 mouse) tell their creator they are ready by setting a field it
// polls, and then loop on WaitForSingleObject for the rest of the process.
// Running such a body to completion inside CreateThread never returns, so
// every guest thread here gets a real context instead.
//
// A guest thread owns its register file, a guest stack, a TEB and a TLS
// array, and runs on its own host thread so guest code can nest host C frames
// as deeply as it likes.  Exactly one of them executes at a time: a baton,
// held under g_sched_m, names the thread allowed to run.  That is what makes
// this safe with no locking anywhere in the guest arena, the shims or the
// generated code - there is never more than one guest instruction in flight,
// exactly as before.
//
// The baton moves only where Windows would have descheduled the running
// thread anyway: a wait that cannot be satisfied yet, Sleep, and the status
// poll a spin loop makes (GetExitCodeThread).  A guest that makes no such
// call never yields.  That is why the frame limiter's GetTickCount spin is
// left alone - nothing it waits for is another thread's to produce, and
// yielding on every clock read would cost a context switch per read.
//
//   RECOMP_CREATETHREAD=skip   record a thread without running it
//   RECOMP_CREATETHREAD=sync   run the body to completion in CreateThread
//                            (the pre-scheduler behaviour, for bisection)
// -------------------------------------------------------------------------
const uint32_t STILL_ACTIVE_ = 0x103;
const uint32_t THREAD_STACK_BYTES = 0x00040000u; // 256 KB, committed
const uint32_t THREAD_STACK_GUARD = 0x00004000u; // 16 KB gap each side (heap_alloc_guarded)
const uint32_t WAIT_TIMEOUT_ = 0x102;

// What a blocked thread is waiting for.  A blocked thread is not runnable and
// the scheduler will not give it the baton until sched_satisfy_locked says its
// wait can complete, at which point the wait is *taken* (a semaphore
// decremented, an auto-reset event cleared, a mutex owned) by the scheduler
// itself, so exactly one waiter can ever be released by one signal.
enum WaitKind : uint8_t { W_NONE, W_OBJECTS, W_CRITSEC };

struct GuestThread {
    size_t index = 0;
    uint32_t handle = 0; // 0 for the main thread: it has none
    // What to run, captured when the thread is created: the guest may close its handle (CreateThread then
    // CloseHandle at once is the common idiom) before the thread is first scheduled, and that must not lose
    // the start routine (NFS Underground's network thread did: a call to 0 at its first slice).
    uint32_t start = 0, param = 0;
    void (*native_start)(X86 *, uint32_t) = nullptr;
    uint32_t id = 1;     // GetCurrentThreadId
    X86 ctx{};
    uint32_t stack_lo = 0, stack_hi = 0;
    uint32_t teb = TEB_BASE, tls = TLS_BASE;
    OsThreadId tid = 0;
    bool is_main = false;
    bool spawned = false; // a host thread exists for it
    bool finished = false;
    // Guards thread_run_exit_cleanup, which several exit paths reach and which
    // must run exactly once: a second unwind would abandon frames that are no
    // longer there and a second observer call would double-count the exit.
    bool exit_cleanup_done = false;
    // Windows keeps a suspend count, not a flag: two SuspendThreads need two
    // ResumeThreads.  CREATE_SUSPENDED starts it at 1.
    int32_t suspend_count = 0;
    // Blocking state.  `blocked` alone is enough to keep the thread off the
    // runnable list; the rest says when it may come back.
    bool blocked = false;
    WaitKind wait_kind = W_NONE;
    uint32_t wait_h[MAXIMUM_WAIT_OBJECTS_];
    uint32_t wait_n = 0;
    bool wait_all = false;
    uint32_t wait_cs = 0; // guest CRITICAL_SECTION address
    bool has_deadline = false;
    double deadline = 0.0;    // monotonic seconds
    uint32_t wait_result = 0; // what the wait returns once released
    double last_yield = 0.0;  // for the bounded scheduling checkpoint
    double runnable_at = 0.0; // when its last wait was satisfied or timed out (T01 evidence)
    jmp_buf exit_jmp;
    uint32_t exit_code = 0;
};

// Scheduling deadlines run on a real monotonic clock, never on host_millis():
// the guest clock can be pinned (the parity fixture pins it to a constant) and
// a pinned clock would make every timed wait either instant or eternal.
double sched_now() {
    return (double)os_monotonic_ns() * 1e-9;
}

std::vector<GuestThread *> &threads() {
    static std::vector<GuestThread *> v;
    return v;
}

std::mutex g_sched_m;
std::condition_variable_any g_sched_cv;
size_t g_baton = 0; // index of the thread allowed to run
// Read by the host from the very thread that sets it, so a plain bool is the
// whole of it: no other thread looks.
bool g_in_idle_slice = false;
bool (*g_input_pending)() = nullptr;
void (*g_input_drain)() = nullptr;
bool g_exit_requested = false; // a guest thread called ExitProcess
uint32_t g_exit_requested_code = 0;
uint32_t g_next_thread_id = 2; // 1 is the main thread
__thread size_t t_self = 0;    // this host thread's index in threads()
// Guest-thread identity is REGISTERED, never inferred. t_self is a
// __thread size_t defaulting to 0, so an unregistered host thread asking
// "am I a guest thread?" would answer by claiming to be threads()[0]. This
// flag is set only where a thread begins running guest code and cleared when
// it stops, so a host thread can never mistake itself for one.
__thread bool t_is_guest = false;
// The running thread's register file and id, as plain thread-locals. A signal
// handler needs these and must not touch cur_thread(), which allocates on
// first use and indexes a vector; neither is async-signal-safe. Null means the
// main thread, whose registers are the loader's process-wide context.
// --------------------------------------------------------------------------
// Registry coordination for the mod foundation.
//
// The baton makes guest threads mutually exclusive, so a mutation made from
// guest code is already serialised against every other guest thread. What is
// not serialised is a mutation from a HOST thread - a menu callback on the
// main thread while a guest thread runs - so those are queued and applied at
// a checkpoint by whichever guest thread next passes one.
//
// sched_registry_lock is the scheduler's own mutex, taken for the mutation
// only. Nothing may call a mod callback or run guest code while holding it.
// --------------------------------------------------------------------------
static void (*g_registry_checkpoint)() = nullptr;
static void (*g_thread_exit_observer)() = nullptr;

// Events the host asked to signal from one of its own threads. The host may
// not touch a guest handle directly: the handle table belongs to whichever
// guest thread holds the baton, and the host has no baton. So a request is
// queued here under the scheduler lock and applied by the next thread to enter
// the scheduler, which by construction is the baton holder.
std::vector<uint32_t> g_host_signals;
// Run 23 (TSan): how many are queued, for sched_checkpoint's lock-free peek (it
// read g_host_signals.empty() without the lock while a host thread pushed).
std::atomic<uint32_t> g_host_signals_n{0};
// ---------------------------------------------------------------------------
// Idling on the run thread.
//
// The run thread is the one that called run_entry, and on a windowed host it
// is also the thread that services the window system. Whenever it parks in the
// scheduler - waiting for the baton, or sleeping until the earliest deadline -
// nothing pumps events and nothing draws, so the window stops responding and
// the cursor spins even though the guest is perfectly healthy.
//
// While waiting for another guest thread, service host events without blocking
// and then use the condition variable: handing the baton back wakes it at once.
// A bounded wait still pumps events if that worker runs for a long time.
// With every guest blocked, hand the deadline to the host so incoming input can
// wake the idle game. Other guest threads only need the condition variable.
//
// An installer rather than a weak symbol, deliberately. A weak default cannot
// be tested for at run time, and the two cases genuinely differ: with no host
// the condition variable is exactly right, because a signal wakes it at once
// and it costs nothing meanwhile. A null pointer says which case this is.
int (*g_idle_waiter)(double) = nullptr;

// Two slice lengths, because the two waits are not the same problem.
//
// Waiting for the baton uses an interruptible condition-variable wait. This
// cap controls how often host events are pumped while a guest worker runs;
// it does not delay a completed handoff.
//
// Waiting until the earliest deadline with nothing else runnable, nobody is
// going to hand anything back, and the host should be allowed to block
// properly rather than be woken five hundred times a second to be told there
// is still nothing to do. The cap is what a signal the host does not return
// early for would cost in latency.
const double HOST_IDLE_BATON_SLICE = 0.002;
const double HOST_IDLE_MAX_SLICE = 0.050;

__thread X86 *t_ctx = nullptr;
__thread uint32_t t_id = 1;

// The main thread's entry appears the moment anything asks, so t_self == 0 is
// always a valid index.  It allocates no handle: a process's own thread has no
// handle until it asks for one, and allocating one here would shift every
// later handle value and with it the bytes the parity comparison sees.
GuestThread *cur_thread() {
    if (threads().empty()) {
        GuestThread *m = new GuestThread();
        m->is_main = true;
        m->index = 0;
        m->id = 1;
        m->teb = TEB_BASE;
        m->tls = TLS_BASE;
        // Room for every thread a game starts, so the vector a parked worker
        // indexes is never moved (thread_spawn also holds g_sched_m to grow it).
        threads().reserve(64);
        threads().push_back(m);
        g_baton = 0;
    }
    return threads()[t_self];
}

uint32_t cur_tls_base() {
    return cur_thread()->tls;
}
uint32_t cur_thread_id() {
    return cur_thread()->id;
}

} // namespace

// The register file of the thread that is running guest code right now. The
// main thread runs on the loader's process-wide context; every other guest
// thread has its own. A fault handler needs this: reporting the main context
// for a worker's fault names the wrong instruction.
// Async-signal-safe by construction: two thread-local reads and no allocation,
// no container and no lock. A fault handler calls this.
X86 *guest_current_context() {
    return t_ctx ? t_ctx : loader_context();
}
uint32_t guest_current_thread_id() {
    return t_id;
}

// --------------------------------------------------------------------------
// The scheduler coordination the mod foundation asks for. Defined here rather
// than in the anonymous namespace above because win32.h declares them.
// --------------------------------------------------------------------------
// Separate profiler registry: normal guest registry writes are baton-serialised,
// whereas this host sampler must use the scheduler mutex for every access.
// Slots have process lifetime, including after a guest thread is retired.
namespace {
std::map<size_t, ProfileSlot *> &profile_slots() {
    static auto *slots = new std::map<size_t, ProfileSlot *>;
    return *slots;
}
} // namespace
void sched_register_profile_slot(ProfileSlot *slot) {
    g_sched_m.lock();
    cur_thread(); // initialise the main registry/baton before starting the sampler
    profile_slots()[t_self] = slot;
    g_sched_m.unlock();
}
ProfileSlot *sched_current_holder_slot() {
    g_sched_m.lock();
    auto it = profile_slots().find(g_baton);
    ProfileSlot *slot = it == profile_slots().end() ? nullptr : it->second;
    g_sched_m.unlock();
    return slot;
}
bool sched_is_guest_thread() {
    return t_is_guest;
}
// Used by run_entry to register the booting thread, which the scheduler never
// spawned and so never passed through thread_host_main.
// Guest entry has begun once any thread has declared itself a guest thread,
// because that declaration is what running translated code requires. Monotone:
// entry is not something that un-happens, and a caller asking "is it still
// safe to touch the tables without the baton" must never be told yes twice.
bool g_guest_entry_begun = false;

void sched_set_guest_thread(bool yes) {
    t_is_guest = yes;
    if (!yes) {
        recomp_profile_truncate(0);
        return;
    }
    g_sched_m.lock();
    g_guest_entry_begun = true;
    // Remember WHICH host thread this is. The run thread is never created
    // by the scheduler, so nothing else records it, and sched_run_thread_finished
    // has to be able to tell it from a host thread that merely defaulted to
    // t_self 0 and would otherwise declare the main thread finished.
    cur_thread()->tid = os_thread_self();
    g_sched_m.unlock();
}

// The whole answer, for a caller that already holds g_sched_m. Everything it
// reads is scheduler state, which is why it may only be asked under that lock.
bool sched_holds_baton_locked() {
    return t_is_guest && g_baton == t_self;
}

bool sched_holds_baton() {
    if (!t_is_guest)
        return false;
    g_sched_m.lock();
    bool mine = sched_holds_baton_locked();
    g_sched_m.unlock();
    return mine;
}

// Spawned threads only. The main guest is not spawned, so this says nothing
// about whether the guest's own entry point is running - which is why a caller
// deciding whether the tables are unattended has to ask both this and
// sched_guest_entry_begun_locked().
bool sched_guest_threads_stopped_locked() {
    for (const GuestThread *t : threads())
        if (t->spawned && !t->finished)
            return false;
    return true;
}

bool sched_guest_threads_stopped() {
    g_sched_m.lock();
    bool all_done = sched_guest_threads_stopped_locked();
    g_sched_m.unlock();
    return all_done;
}

bool sched_guest_entry_begun_locked() {
    return g_guest_entry_begun;
}

#ifdef POPM_TESTING
// For a process that tears one guest down and starts another, which only the
// test binaries do: a real run enters the guest once. Without it every suite
// after the first would be answered as though the guest were already running.
//
// Behind the test macro because it resets a safety latch. A latch production
// code can reset is not a latch, and the pre-entry pump's whole guarantee is
// that once the guest is running nothing can be granted again.
void sched_forget_guest_entry() {
    g_sched_m.lock();
    g_guest_entry_begun = false;
    g_sched_m.unlock();
}
#endif

void sched_registry_lock() {
    g_sched_m.lock();
}
void sched_registry_unlock() {
    g_sched_m.unlock();
}

bool sched_in_idle_slice() {
    return g_in_idle_slice;
}

// The host has queued input. A thread that already passed the emptiness check
// above is parked on the scheduler's condition with a deadline of up to a
// second, and would sleep out that deadline with the input in hand; this wakes
// it so it re-evaluates. Signalling costs nothing when nobody is parked.
void sched_input_arrived() {
    g_sched_m.lock();
    g_sched_cv.notify_all();
    g_sched_m.unlock();
}

void sched_set_input_queue(bool (*pending)(), void (*drain)()) {
    g_input_pending = pending;
    g_input_drain = drain;
}

void sched_set_checkpoint(void (*fn)()) {
    g_registry_checkpoint = fn;
}
void sched_set_thread_exit_observer(void (*fn)()) {
    g_thread_exit_observer = fn;
}

// Called at every point the baton can move, with no lock held: a callback that
// took g_sched_m here would deadlock against the wait it is about to enter.
void sched_run_checkpoint() {
    if (g_registry_checkpoint)
        g_registry_checkpoint();
}

namespace {

// A TLS index is process wide, so allocating or freeing one has to clear that
// slot in every thread's array, not just the running thread's.
void tls_clear_slot_everywhere(uint32_t i) {
    cur_thread();
    for (GuestThread *t : threads())
        wr32(t->tls + 4 * i, 0);
}

void thread_finish_exit_process();

// T01 correction (Test58): the deadline ticker. Every guest Sleep/timed wait ends
// in a host timed wait, and on macOS those wake late by the timer's coalescing
// leeway: measured on the owner's Mac 2.8 ms median, 4.5 ms max, whatever the
// mechanism (condition variable, nanosleep, mach_wait_until) or thread class;
// a time-constraint thread wakes within 0.05 ms. The game's frame limiter sleeps
// every frame, so that leeway landed directly on the frame (16.7 -> 19-21 ms).
// One tiny time-constraint thread therefore sleeps until the earliest armed
// deadline and wakes the scheduler (and the host's idle waiter). It only signals;
// the timed waits stay as the backstop, so a missed tick is never worse than
// before. RECOMP_SCHED_TICKER=0 disables it (A/B evidence only).
void (*g_idle_wake)() = nullptr;
struct DeadlineTicker {
    std::mutex m;
    std::condition_variable cv;
    uint64_t armed_ns = 0; // earliest pending deadline, 0 when none
    bool started = false;
};
DeadlineTicker &deadline_ticker() {
    static DeadlineTicker *t = new DeadlineTicker; // process lifetime: never joined
    return *t;
}
bool sched_ticker_enabled() {
    static const bool on = [] {
        const char *v = recomp_env("SCHED_TICKER");
        return !(v && strcmp(v, "0") == 0);
    }();
    return on;
}
void deadline_ticker_main() {
    os_thread_set_time_critical_wake();
    DeadlineTicker &t = deadline_ticker();
    std::unique_lock<std::mutex> l(t.m);
    for (;;) {
        if (!t.armed_ns) {
            t.cv.wait(l);
            continue;
        }
        const uint64_t target = t.armed_ns;
        const uint64_t now = os_monotonic_ns();
        if (now < target) {
            t.cv.wait_for(l, std::chrono::nanoseconds(target - now));
            continue; // re-read: an earlier deadline may have been armed meanwhile
        }
        t.armed_ns = 0;
        l.unlock();
        g_sched_m.lock();
        g_sched_cv.notify_all();
        g_sched_m.unlock();
        if (g_idle_wake && g_in_idle_slice)
            g_idle_wake();
        l.lock();
    }
}
// Caller may hold g_sched_m (lock order: g_sched_m, then the ticker's own mutex;
// the ticker never takes its mutex while holding g_sched_m).
void sched_ticker_arm(double until) {
    if (!sched_ticker_enabled() || until <= 0.0)
        return;
    const uint64_t ns = uint64_t(until * 1e9);
    DeadlineTicker &t = deadline_ticker();
    std::lock_guard<std::mutex> l(t.m);
    if (!t.started) {
        t.started = true;
        std::thread(deadline_ticker_main).detach();
    }
    if (!t.armed_ns || ns < t.armed_ns) {
        t.armed_ns = ns;
        t.cv.notify_one();
    }
}

// Caller holds g_sched_m. Waits until `until` (an absolute monotonic time, or
// 0 for "no deadline, wake when something changes"). The lock is released
// across a host slice, so the host may call back into the runtime - signalling
// a guest event, most obviously - without deadlocking against the scheduler.
void sched_wait_locked(size_t me, double until, double max_slice) {
    double now = sched_now();
    if (until && until <= now)
        return;
    if (until)
        sched_ticker_arm(until);

    if (g_idle_waiter && threads()[me]->is_main) {
        const bool awaiting_baton = g_baton != me;
        double slice = max_slice;
        if (until && until - now < slice)
            slice = until - now;
        int (*waiter)(double) = g_idle_waiter;
        g_sched_m.unlock();
        // The lock is down and another guest thread may take the baton and run
        // guest code for the whole slice. Say so, so the host can hold back
        // anything the guest would see - input delivery, and mod callbacks
        // above all - until it holds the baton itself.
        g_in_idle_slice = true;
        // AppKit's event wait cannot hear g_sched_cv. Sleeping there for each
        // handoff makes short mouse/keyboard workers cost a full 2 ms slice,
        // many times per rendered frame while the pointer moves. Poll events
        // first, then wait on the notification the returning worker signals.
        waiter(awaiting_baton ? 0.0 : slice);
        g_in_idle_slice = false;
        g_sched_m.lock();
        if (!awaiting_baton || g_baton == me)
            return;
    }

    // The host has been serviced, or none is installed. Sleep interruptibly,
    // capped so host events and deadlines are still re-examined.
    double wait = until ? (until - sched_now()) : 1.0;
    if (g_idle_waiter && threads()[me]->is_main)
        wait = std::min(wait, max_slice);
    if (wait <= 0.0)
        return;
    if (wait > 1.0)
        wait = 1.0;
    g_sched_cv.wait_for(g_sched_m, std::chrono::duration<double>(wait));
}

// ---------------------------------------------------------------------------
// The scheduler.
//
// One baton, held under g_sched_m, names the thread allowed to run guest code.
// A thread that cannot make progress marks itself blocked and calls
// sched_run_others, which does not return until the thread is runnable again
// and holds the baton.  Nothing is ever released by a fabricated timeout: a
// wait ends when its object is taken or its deadline passes, and an INFINITE
// wait with nothing left to signal it is reported as a deadlock rather than
// answered with WAIT_TIMEOUT.
// ---------------------------------------------------------------------------
const size_t NO_THREAD = (size_t)-1;

// The last few baton hand-offs, so a scheduler that wedges can say how it got
// there instead of hanging silently.  Written under g_sched_m.
struct Handoff {
    size_t from, to;
    const char *why;
};
Handoff g_handoffs[16];
uint32_t g_handoff_count = 0;

// T01 evidence: baton hold time per guest thread id, from the recorded hand-offs,
// logged every 30 s by the main thread (guest_block).
uint64_t g_hold_ns[32];
double g_hold_since = 0.0;
// CPU each guest thread burns running guest code (from its return from a block to its
// next block), per guest thread id; logged with the hold times.
std::atomic<uint64_t> g_run_cpu_ns[32];
__thread uint64_t t_run_cpu_from = 0;
void record_handoff_locked(size_t from, size_t to, const char *why) {
    g_handoffs[g_handoff_count % 16] = Handoff{from, to, why};
    ++g_handoff_count;
    const double now = (double)os_monotonic_ns() * 1e-9;
    if (g_hold_since > 0.0 && from < threads().size() && now > g_hold_since)
        g_hold_ns[threads()[from]->id % 32] += uint64_t((now - g_hold_since) * 1e9);
    g_hold_since = now;
}

const char *wait_kind_name(WaitKind k) {
    return k == W_OBJECTS ? "objects" : (k == W_CRITSEC ? "critical section" : "-");
}

// Caller holds g_sched_m.
void dump_scheduler_locked(const char *what, size_t me) {
    fprintf(stderr, "[sched] %s (thread %zu); baton %zu; %zu threads:\n", what, me, g_baton,
            threads().size());
    for (GuestThread *t : threads())
        fprintf(stderr, "[sched]   %zu: handle %08x id %u%s%s%s%s suspend=%d wait=%s n=%u%s\n",
                t->index, t->handle, t->id, t->is_main ? " main" : "",
                t->spawned ? " spawned" : " not-spawned", t->finished ? " finished" : "",
                t->blocked ? " blocked" : "", t->suspend_count, wait_kind_name(t->wait_kind),
                t->wait_n, t->has_deadline ? " timed" : " untimed");
    uint32_t n = g_handoff_count < 16 ? g_handoff_count : 16;
    fprintf(stderr, "[sched] last %u hand-offs, oldest first:\n", n);
    for (uint32_t k = 0; k < n; ++k) {
        const Handoff &h = g_handoffs[(g_handoff_count - n + k) % 16];
        fprintf(stderr, "[sched]   %zu -> %zu (%s)\n", h.from, h.to, h.why);
    }
    fflush(stderr);
}

void watchdog_dump_locked_bounded() {
    std::unique_lock<std::mutex> lk(g_sched_m, std::defer_lock);
    double give_up = sched_now() + 1.0;
    while (!lk.try_lock()) {
        if (sched_now() > give_up) {
            fprintf(stderr, "[sched] watchdog: the scheduler lock stayed held for 1 s; no thread dump\n");
            fflush(stderr);
            return;
        }
        os_sleep_us(1000);
    }
    dump_scheduler_locked("watchdog", g_baton);
    double now = sched_now();
    for (GuestThread *t : threads()) {
        // The main thread runs on the loader's context, every other one on its own.
        const X86 *x = t->is_main ? loader_context() : &t->ctx;
        uint32_t esp = x ? x->r[R_ESP] : 0;
        fprintf(stderr, "[sched]   %zu: start %08x eip %08x esp %08x stack %08x %08x %08x %08x", t->index, t->start,
                x ? x->eip : 0, esp, esp ? rd32(esp) : 0, esp ? rd32(esp + 4) : 0, esp ? rd32(esp + 8) : 0,
                esp ? rd32(esp + 12) : 0);
        if (t->has_deadline)
            fprintf(stderr, " deadline in %.3f s", t->deadline - now);
        fprintf(stderr, " runnable %.1f s ago\n", t->runnable_at > 0.0 ? now - t->runnable_at : -1.0);
        for (uint32_t k = 0; t->blocked && t->wait_kind == W_OBJECTS && k < t->wait_n; ++k) {
            HObj *o = handle_any(t->wait_h[k]);
            if (!o) {
                fprintf(stderr, "[sched]     waits on %08x: no such handle\n", t->wait_h[k]);
                continue;
            }
            fprintf(stderr, "[sched]     waits on %08x%s: kind %d signalled %d manual %d count %d owner %u x%u%s '%s'\n",
                    t->wait_h[k], t->wait_all ? " (all)" : "", (int)o->kind, o->signalled, o->manual_reset,
                    o->count, o->owner_tid, o->owner_recursion, o->abandoned ? " abandoned" : "",
                    o->object_name.c_str());
        }
        if (t->blocked && t->wait_kind == W_CRITSEC && t->wait_cs)
            fprintf(stderr, "[sched]     waits on critical section %08x: lock %d recursion %d owner %u\n", t->wait_cs,
                    (int32_t)rd32(t->wait_cs + 4), (int32_t)rd32(t->wait_cs + 8), rd32(t->wait_cs + 12));
    }
    fflush(stderr);
}

bool thread_runnable(const GuestThread *t) {
    if (t->finished)
        return false;
    if (!t->is_main && !t->spawned)
        return false;
    // Once a process exit is pending a worker may take the baton for one
    // purpose: to end. Every point at which one resumes - guest_block,
    // guest_yield, a thread body about to start - checks the pending exit
    // before the next guest instruction and terminates the thread there, so
    // this offers no guest code a chance to run. Blocked and suspended ones
    // are offered it too, because their wait is never going to be satisfied
    // now and Windows would not have let them continue either. It is what
    // unwinds their frames and lets a host's shutdown drive finish instead of
    // waiting out its bound. The main thread keeps the ordinary rules: it owns
    // the landing pad, and sched_run_others_locked wakes it from any wait.
    if (g_exit_requested && !t->is_main)
        return true;
    if (t->blocked)
        return false;
    if (t->suspend_count > 0)
        return false;
    return true;
}

// ---------------------------------------------------------------------------
// Object availability.  Ownership matters now that threads interleave, so a
// mutex answers per thread and taking it is a separate step from testing it.
// ---------------------------------------------------------------------------
// Signals a timer whose time has come and arms its next period. A periodic
// timer that fell behind fires once and continues from now, as Windows does.
void timer_fire(HObj *o) {
    if (!o->timer || !o->timer_armed)
        return;
    double now = sched_now();
    if (now < o->timer_due)
        return;
    o->signalled = true;
    if (o->timer_period > 0.0) {
        o->timer_due += o->timer_period;
        if (o->timer_due <= now)
            o->timer_due = now + o->timer_period;
    } else {
        o->timer_armed = false;
    }
}

bool object_available(uint32_t h, uint32_t tid) {
    HObj *o = handle_any(h);
    if (!o)
        return false;
    timer_fire(o);
    switch (o->kind) {
    case H_SEM:
        return o->count > 0;
    case H_EVENT:
        return o->signalled;
    case H_MUTEX:
        return o->owner_tid == 0 || o->owner_tid == tid;
    case H_THREAD:
        return o->exit_code != 0x103; // signalled once it ends
    default:
        return true;
    }
}

// Takes the object for `tid`. True when it was abandoned, which the caller
// turns into WAIT_ABANDONED; taking it clears the flag, so only the first
// waiter after the owner died is told.
bool object_take(uint32_t h, uint32_t tid) {
    HObj *o = handle_any(h);
    if (!o)
        return false;
    bool was_abandoned = false;
    switch (o->kind) {
    case H_SEM:
        if (o->count > 0)
            --o->count;
        break;
    case H_EVENT:
        if (o->signalled && !o->manual_reset)
            o->signalled = false;
        break;
    case H_MUTEX:
        was_abandoned = o->abandoned;
        o->abandoned = false;
        o->owner_tid = tid;
        ++o->owner_recursion;
        break;
    default:
        break;
    }
    return was_abandoned;
}

// A thread is ending. Every mutex it still owns becomes abandoned and free.
void release_mutexes_of_locked(uint32_t tid) {
    for (auto &kv : handles()) {
        HObj &o = kv.second;
        if (o.kind != H_MUTEX || o.owner_tid != tid)
            continue;
        o.owner_tid = 0;
        o.owner_recursion = 0;
        o.abandoned = true;
        LOGW("thread %u ended still owning mutex %08x: it is abandoned", tid, kv.first);
    }
}

// A guest CRITICAL_SECTION, laid out where Windows puts these fields so a
// guest that reads them directly sees what it expects.
const uint32_t CS_OFF_LOCK_COUNT = 4; // -1 unowned, else recursion - 1
const uint32_t CS_OFF_RECURSION = 8;
const uint32_t CS_OFF_OWNER = 12; // owning thread id, 0 unowned

bool critsec_available(uint32_t cs, uint32_t tid) {
    if (!cs || !gm_valid(cs, 24))
        return true;
    uint32_t owner = rd32(cs + CS_OFF_OWNER);
    return owner == 0 || owner == tid;
}

void critsec_take(uint32_t cs, uint32_t tid) {
    if (!cs || !gm_valid(cs, 24))
        return;
    uint32_t rec = rd32(cs + CS_OFF_OWNER) == tid ? rd32(cs + CS_OFF_RECURSION) : 0;
    wr32(cs + CS_OFF_OWNER, tid);
    wr32(cs + CS_OFF_RECURSION, rec + 1);
    wr32(cs + CS_OFF_LOCK_COUNT, rec); // LockCount is recursion - 1
}

// Caller holds g_sched_m.  Can this blocked thread's wait complete now?  If it
// can, the wait is taken here and the thread becomes runnable, so one signal
// releases exactly one waiter.
bool sched_satisfy_locked(GuestThread *t) {
    if (!t->blocked)
        return true;
    if (t->wait_kind == W_OBJECTS) {
        if (t->wait_all) {
            for (uint32_t i = 0; i < t->wait_n; ++i)
                if (!object_available(t->wait_h[i], t->id))
                    return false;
            bool ab = false;
            for (uint32_t i = 0; i < t->wait_n; ++i)
                ab |= object_take(t->wait_h[i], t->id);
            t->wait_result = ab ? 0x80u : 0u; // WAIT_ABANDONED_0
        } else {
            uint32_t hit = t->wait_n;
            for (uint32_t i = 0; i < t->wait_n; ++i)
                if (object_available(t->wait_h[i], t->id)) {
                    hit = i;
                    break;
                }
            if (hit == t->wait_n)
                return false;
            bool ab = object_take(t->wait_h[hit], t->id);
            t->wait_result = (ab ? 0x80u : 0u) + hit;
        }
    } else if (t->wait_kind == W_CRITSEC) {
        if (!critsec_available(t->wait_cs, t->id))
            return false;
        critsec_take(t->wait_cs, t->id);
        t->wait_result = 0;
    } else if (t->wait_kind == W_NONE && t->has_deadline) {
        return false; // a plain sleep: only time releases it
    }
    t->blocked = false;
    t->wait_kind = W_NONE;
    t->wait_n = 0;
    t->has_deadline = false;
    t->runnable_at = sched_now();
    return true;
}

// Caller holds g_sched_m.  Releases every blocked thread whose wait can now
// complete or whose deadline has passed.
// Caller holds g_sched_m AND the baton, so touching the handle table is safe:
// no other guest thread is running guest code.
void apply_host_signals_locked() {
    if (g_host_signals.empty())
        return;
    for (uint32_t h : g_host_signals) {
        HObj *o = handle_get(h, H_EVENT);
        if (o)
            o->signalled = true;
    }
    g_host_signals.clear();
    g_host_signals_n.store(0, std::memory_order_relaxed);
}

void sched_expire_locked() {
    apply_host_signals_locked();
    double now = sched_now();
    for (GuestThread *t : threads()) {
        if (!t->blocked)
            continue;
        if (sched_satisfy_locked(t))
            continue;
        if (t->has_deadline && now >= t->deadline) {
            t->blocked = false;
            t->runnable_at = now;
            t->wait_result = t->wait_kind == W_NONE ? 0 : 0x102; // WAIT_TIMEOUT
            t->wait_kind = W_NONE;
            t->wait_n = 0;
            t->has_deadline = false;
        }
    }
}

// Caller holds g_sched_m.  The next runnable thread after `from`, or NO_THREAD.
size_t sched_next_runnable_locked(size_t from) {
    size_t n = threads().size();
    for (size_t k = 1; k <= n; ++k) {
        size_t i = (from + k) % n;
        if (thread_runnable(threads()[i]))
            return i;
    }
    return NO_THREAD;
}

// Caller holds g_sched_m.  The earliest deadline among blocked threads, or 0
// when none of them is timed, in which case only a signal can release them.
double sched_earliest_deadline_locked() {
    double best = 0.0;
    for (GuestThread *t : threads()) {
        if (!t->blocked || !t->has_deadline)
            continue;
        if (best == 0.0 || t->deadline < best)
            best = t->deadline;
    }
    return best;
}

// T01 evidence: where the MAIN guest thread's wall time goes while it does not
// hold the baton. yield = runnable but another guest thread held the baton
// (the bounded checkpoint yield); blocked = its own sleep or wait, until the
// deadline passed or the wait was satisfied; late = runnable again after such a
// wait but still without the baton. max = the longest single yield/late stretch
// since the last read. Read (and max reset) by the presenter at each seal.
std::atomic<uint64_t> g_main_yield_ns{0}, g_main_blocked_ns{0}, g_main_late_ns{0},
    g_main_max_starve_ns{0};
// Split of the main thread's blocking: plain Sleep time the guest ASKED for, how far
// its sleeps overran their deadline before it ran again, and time in object /
// critical-section waits (another thread's signal or lock release).
std::atomic<uint64_t> g_main_sleep_req_ns{0}, g_main_sleep_over_ns{0}, g_main_wait_ns{0};
// Of the sleep overrun: time past the main thread's deadline during which another
// guest thread held the baton (cooperative: it cannot be interrupted, so the main
// thread runs again only when that thread next enters the scheduler). Per-thread
// totals (by guest thread id, under g_sched_m) are logged every 30 s.
std::atomic<uint64_t> g_main_others_over_ns{0};
double g_main_sleep_deadline = 0.0; // main thread's current plain-sleep deadline, 0 when none

uint64_t g_overrun_blame_ns[32];
double g_overrun_blame_logged = 0.0;
void main_starve_max(uint64_t ns) {
    uint64_t m = g_main_max_starve_ns.load(std::memory_order_relaxed);
    while (ns > m && !g_main_max_starve_ns.compare_exchange_weak(m, ns, std::memory_order_relaxed)) {
    }
}
uint64_t sched_seconds_ns(double seconds) {
    return seconds > 0 ? (uint64_t)(seconds * 1e9) : 0;
}

// Caller holds g_sched_m and the baton.  Runs everything else until `me` is
// runnable again, then returns with the baton.  When nothing at all is
// runnable this sleeps until the earliest deadline rather than spinning, and
// when there is no deadline either it says so once and keeps waiting: an
// INFINITE wait must not be answered with a timeout that never happened.
void sched_run_others_locked(size_t me, const char *why) {
    for (;;) {
        // A worker can ask the process to exit while this thread is parked in
        // a wait nothing is ever going to satisfy. The exit outranks the wait:
        // the main thread owns the landing pad, so it has to come back and
        // take it however long it was told to wait for. Only until the exit
        // has been performed, though: after that this thread is draining, and
        // taking the baton straight back here would leave the workers it is
        // draining no chance to end.
        if (g_exit_requested && !g_exited && threads()[me]->is_main) {
            GuestThread *t = threads()[me];
            t->blocked = false;
            t->wait_kind = W_NONE;
            t->wait_n = 0;
            t->has_deadline = false;
            t->wait_result = 0x102; // the wait never completed
            g_baton = me;
            return;
        }
        sched_expire_locked();
        if (thread_runnable(threads()[me])) {
            // Runnable is not the same as entitled. Every resume from a wait
            // comes back here, and a woken thread that is no longer the holder
            // must not take the baton: doing that put it beside whoever the
            // holder had become and both ran guest code at once. It waits for
            // the ordinary handoff instead.
            //
            // The exception is a baton stranded on a thread that has finished,
            // which can hand it to nobody. Taking it there authorises no
            // second runner, because the one it is taken from is not running.
            if (g_baton == me)
                return;
            if (g_baton >= threads().size() || threads()[g_baton]->finished) {
                g_baton = me;
                return;
            }
            sched_wait_locked(me, 0.0, HOST_IDLE_BATON_SLICE);
            continue;
        }

        size_t next = sched_next_runnable_locked(me);
        if (next != NO_THREAD) {
            g_baton = next;
            record_handoff_locked(me, next, why);
            g_sched_cv.notify_all();
            // Wait for the baton, then loop: holding it is not the same as
            // being runnable, and a thread that ends hands the baton on
            // without knowing who is eligible. The run thread services the
            // host between slices rather than parking here.
            const double handed_at = sched_now();
            while (g_baton != me)
                sched_wait_locked(me, 0.0, HOST_IDLE_BATON_SLICE);
            if (g_main_sleep_deadline > 0.0 && threads()[me]->is_main) {
                const double back = sched_now();
                const double from = std::max(handed_at, g_main_sleep_deadline);
                if (back > from) {
                    const uint64_t ns = sched_seconds_ns(back - from);
                    g_main_others_over_ns.fetch_add(ns, std::memory_order_relaxed);
                    // The thread that handed the baton back held it last.
                    for (uint32_t k = 0; k < 16 && k < g_handoff_count; ++k) {
                        const Handoff &h = g_handoffs[(g_handoff_count - 1 - k) % 16];
                        if (h.to == me) {
                            if (h.from < threads().size())
                                g_overrun_blame_ns[threads()[h.from]->id % 32] += ns;
                            break;
                        }
                    }
                }
            }
            continue;
        }

        // Input the host decoded but could not apply is work this thread can
        // do, and doing it may be what makes something runnable: the messages
        // it posts are what signal the event a blocked thread is waiting for.
        // Here is the one safe place for it - nothing else is runnable, so
        // nothing else is in guest code - and it goes before any decision to
        // sleep, because sleeping on input already in hand is the deadlock
        // this exists to prevent.
        // ... and only when no mod callback is on this stack. A hook that
        // delegates into the original can end up inside a blocking guest wait
        // and reach this loop with its own frame still live; dispatching input
        // callbacks there would run one mod's callback inside another's, with
        // the outer one holding a game view it took before any of it. Queued
        // input waits for the outer callback to return, which is the same
        // depth condition the mod layer's own quiescence uses.
        //
        // THIS THREAD's depth, deliberately, and not a process-wide count of
        // callbacks in flight. A callback suspended on another cooperative
        // thread is not running - only the baton holder runs - so it cannot
        // observe anything this drain does. A process-wide gate would instead
        // starve input for as long as any worker sat parked inside a hook,
        // which is a hang rather than a safety property.
        if (g_input_pending && g_input_drain && mods_hook_depth() == 0 && g_input_pending()) {
            g_baton = me;
            g_sched_m.unlock();
            g_input_drain();
            g_sched_m.lock();
            continue;
        }

        double deadline = sched_earliest_deadline_locked();
        if (deadline == 0.0) {
            static bool told = false;
            if (!told) {
                told = true;
                dump_scheduler_locked("every guest thread is blocked with no deadline "
                                      "and nothing left to signal them",
                                      me);
            }
            // Wait rather than invent a wake-up. The host's run deadline ends
            // the process; a fabricated WAIT_TIMEOUT would be a lie.
            sched_wait_locked(me, 0.0, HOST_IDLE_MAX_SLICE);
            continue;
        }
        // Sleep exactly as far as the earliest deadline, no further and no
        // rounding: a guest that asks for Sleep(1) has to wake in about a
        // millisecond, not on the next tick of some coarser poll.
        sched_wait_locked(me, deadline, HOST_IDLE_MAX_SLICE);
    }
}

// Caller holds g_sched_m.  A thread that is ending gives the baton to whoever
// can run.  When nobody can, it goes to the main thread, which re-enters the
// scheduler and sleeps on the deadlines from there.
void sched_handoff_on_exit_locked(size_t me, const char *why) {
    sched_expire_locked();
    size_t next = sched_next_runnable_locked(me);
    if (next == NO_THREAD)
        next = 0;
    g_baton = next;
    record_handoff_locked(me, next, why);
    g_sched_cv.notify_all();
}

// Blocks the calling thread until its wait completes or its deadline passes.
// Returns what the wait should report. Caller fills in the wait descriptor.
uint32_t guest_block(const char *why) {
    GuestThread *me = cur_thread();
    {
        const uint64_t cpu = os_thread_cpu_ns();
        if (t_run_cpu_from && cpu > t_run_cpu_from)
            g_run_cpu_ns[me->id % 32].fetch_add(cpu - t_run_cpu_from, std::memory_order_relaxed);
        t_run_cpu_from = 0;
    }
    g_sched_m.lock();
    if (g_baton != me->index) {
        static bool told = false;
        if (!told) {
            told = true;
            dump_scheduler_locked("block without the baton", me->index);
        }
        me->blocked = false;
        g_sched_m.unlock();
        return 0x102;
    }
    me->last_yield = sched_now();
    const double block_start = me->last_yield;
    const bool plain_sleep = me->wait_kind == W_NONE && me->has_deadline;
    const double sleep_until = me->deadline;
    me->runnable_at = 0.0;
    if (me->is_main)
        g_main_sleep_deadline = plain_sleep ? sleep_until : 0.0;
    sched_run_others_locked(me->index, why);
    uint32_t r = me->wait_result;
    if (me->is_main) {
        g_main_sleep_deadline = 0.0;
        const double back = sched_now();
        if (back - g_overrun_blame_logged >= 30.0) {
            uint64_t total = 0;
            for (uint64_t v : g_overrun_blame_ns)
                total += v;
            if (total && g_overrun_blame_logged > 0.0) {
                char line[512];
                int n = snprintf(line, sizeof line, "sched: main-thread sleep overrun while another "
                                                    "guest thread held the baton, last 30 s: %.1f ms",
                                 total / 1e6);
                for (uint32_t id = 0; id < 32 && n > 0 && n < (int)sizeof line - 32; ++id)
                    if (g_overrun_blame_ns[id])
                        n += snprintf(line + n, sizeof line - n, "; thread %u %.1f ms", id,
                                      g_overrun_blame_ns[id] / 1e6);
                LOGW("%s", line);
            }
            {
                char line[512];
                int n = snprintf(line, sizeof line, "sched: baton held, last 30 s (from hand-offs):");
                for (uint32_t id = 0; id < 32 && n > 0 && n < (int)sizeof line - 32; ++id)
                    if (g_hold_ns[id] >= 1000000)
                        n += snprintf(line + n, sizeof line - n, " thread %u %.0f ms;", id,
                                      g_hold_ns[id] / 1e6);
                if (g_overrun_blame_logged > 0.0)
                    LOGW("%s", line);
                for (uint64_t &v : g_hold_ns)
                    v = 0;
                n = snprintf(line, sizeof line, "sched: guest CPU between blocks, last 30 s:");
                for (uint32_t id = 0; id < 32 && n > 0 && n < (int)sizeof line - 32; ++id) {
                    const uint64_t v = g_run_cpu_ns[id].exchange(0, std::memory_order_relaxed);
                    if (v >= 1000000)
                        n += snprintf(line + n, sizeof line - n, " thread %u %.0f ms;", id, v / 1e6);
                }
                if (g_overrun_blame_logged > 0.0)
                    LOGW("%s", line);
            }
            for (uint64_t &v : g_overrun_blame_ns)
                v = 0;
            g_overrun_blame_logged = back;
        }
        if (plain_sleep) {
            g_main_sleep_req_ns.fetch_add(sched_seconds_ns(sleep_until - block_start),
                                          std::memory_order_relaxed);
            g_main_sleep_over_ns.fetch_add(sched_seconds_ns(back - sleep_until),
                                           std::memory_order_relaxed);
        } else {
            g_main_wait_ns.fetch_add(sched_seconds_ns(back - block_start), std::memory_order_relaxed);
        }
        const double ready = me->runnable_at >= block_start && me->runnable_at <= back
                                 ? me->runnable_at : back;
        const uint64_t late = sched_seconds_ns(back - ready);
        g_main_blocked_ns.fetch_add(sched_seconds_ns(ready - block_start), std::memory_order_relaxed);
        g_main_late_ns.fetch_add(late, std::memory_order_relaxed);
        main_starve_max(late);
    }
    g_sched_m.unlock();
    t_run_cpu_from = os_thread_cpu_ns();
    if (g_exit_requested)
        thread_finish_exit_process();
    return r;
}

// A voluntary yield: let anything else that is runnable run, then come back.
// False means nothing else could have run, so the caller has made no progress.
bool guest_yield() {
    sched_run_checkpoint();
    if (threads().size() < 2)
        return false;
    GuestThread *me = cur_thread();
    g_sched_m.lock();
    if (g_baton != me->index) {
        static bool told = false;
        if (!told) {
            told = true;
            dump_scheduler_locked("yield without the baton", me->index);
        }
        g_sched_m.unlock();
        return false;
    }
    me->last_yield = sched_now();
    sched_expire_locked();
    size_t next = sched_next_runnable_locked(me->index);
    bool moved = next != NO_THREAD && next != me->index;
    if (moved) {
        const double away = me->last_yield;
        g_baton = next;
        record_handoff_locked(me->index, next, "yield");
        g_sched_cv.notify_all();
        while (g_baton != me->index)
            sched_wait_locked(me->index, 0.0, HOST_IDLE_BATON_SLICE);
        if (me->is_main) {
            const uint64_t ns = sched_seconds_ns(sched_now() - away);
            g_main_yield_ns.fetch_add(ns, std::memory_order_relaxed);
            main_starve_max(ns);
        }
    }
    g_sched_m.unlock();
    if (moved && g_exit_requested)
        thread_finish_exit_process();
    return moved;
}

// A bounded scheduling checkpoint, called from imports_dispatch before every
// shim.  Scheduling must not depend on which polling API the game happens to
// call: the frame limiter spins on GetTickCount and would otherwise starve the
// DirectInput service threads indefinitely, expired 200 ms waits and all.  Any
// call into the runtime is a checkpoint, and a thread yields at most once per
// millisecond so the check costs a clock read on the hot path.
const double SCHED_SLICE_SECONDS = 0.001; // default; RECOMP_SCHED_SLICE_MS overrides (sched_checkpoint)

} // namespace

void sched_watchdog_dump() {
    watchdog_dump_locked_bounded();
}

// Cumulative main-thread baton accounting (see g_main_yield_ns); out[3] is the
// longest single starvation stretch since the previous call, which resets it;
// out[4..6] sleep requested / sleep overrun / object+lock waits; out[7] the part of
// the overrun during which another guest thread held the baton.
void recomp_sched_main_totals(uint64_t out[8]) {
    out[0] = g_main_yield_ns.load(std::memory_order_relaxed);
    out[1] = g_main_blocked_ns.load(std::memory_order_relaxed);
    out[2] = g_main_late_ns.load(std::memory_order_relaxed);
    out[3] = g_main_max_starve_ns.exchange(0, std::memory_order_relaxed);
    out[4] = g_main_sleep_req_ns.load(std::memory_order_relaxed);
    out[5] = g_main_sleep_over_ns.load(std::memory_order_relaxed);
    out[6] = g_main_wait_ns.load(std::memory_order_relaxed);
    out[7] = g_main_others_over_ns.load(std::memory_order_relaxed);
}

// Blocks the calling guest thread for `ms`, letting everything else run. The
// message loop uses it so a wait for a message is a real wait rather than a
// spin.
void guest_sleep_ms(uint32_t ms) {
    sched_sleep_ms(ms);
}
uint32_t guest_wait_objects(const uint32_t *handles, uint32_t count, bool wait_all,
                            uint32_t timeout_ms) {
    return sched_wait_objects(handles, count, wait_all, timeout_ms);
}

// Installs what the run thread does instead of parking in the scheduler. See
// the note by g_idle_waiter: on a windowed host the run thread also services
// the window system, so a scheduler wait there is a frozen window. The waiter
// is given a slice in seconds, may service whatever it likes, and should
// return as soon as it has something to report or the slice is up. Passing
// null restores the plain condition-variable wait, which is what a host with
// nothing to service wants.
void host_set_idle_waiter(int (*fn)(double seconds)) {
    g_idle_waiter = fn;
}
// Installs a thread-safe way to end the host's idle wait early (see the deadline
// ticker). Null (the default) leaves the idle waiter to its own timeout.
void host_set_idle_wake(void (*fn)()) {
    g_idle_wake = fn;
}

// Signals a guest event object from a host thread. Safe to call from anywhere,
// including while a guest thread holds the baton: the request is queued under
// the scheduler lock and applied by the next thread to enter the scheduler,
// which is always the baton holder, so the handle table is only ever touched
// by one thread. The broadcast wakes a scheduler parked on a deadline, so a
// thread waiting on this event is released as soon as the signal lands rather
// than when its timeout happens to expire.
void guest_event_signal_from_host(uint32_t handle) {
    if (!handle || handle == 0xffffffffu)
        return;
    g_sched_m.lock();
    g_host_signals.push_back(handle);
    g_host_signals_n.store((uint32_t)g_host_signals.size(), std::memory_order_relaxed);
    g_sched_cv.notify_all();
    g_sched_m.unlock();
}

// Hands the baton to whatever else can run and takes it back. False means
// nothing else could have run, so the caller has made no progress and must not
// treat the call as a wait. A host's blocking message waiter needs this: a
// cooperative thread that sits in the waiter holds the baton, so the thread
// that would post the message cannot run and the wait becomes a deadlock.
bool host_guest_yield() {
    return guest_yield();
}

// Test seam (run 23, runtime_tests --sched-race): what a woken worker does to the
// scheduler's containers before it holds the baton (sched_expire_locked), from
// a host thread: walk the thread list and the handle table under g_sched_m.
uint32_t sched_census_locked_walk() {
    std::lock_guard<std::mutex> l(g_sched_m);
    uint32_t n = 0;
    for (GuestThread *t : threads())
        n += t && !t->finished;
    for (const auto &kv : handles())
        n += kv.second.kind == H_EVENT;
    return n;
}

// The guest stack pointers of this thread's open sched_atomic_enter stretches,
// innermost last. Nesting deeper than this is not tracked, and not needed.
const uint32_t SCHED_ATOMIC_MAX = 8;
__thread uint32_t t_atomic = 0;
__thread uint32_t t_atomic_esp[SCHED_ATOMIC_MAX];
void sched_atomic_enter(uint32_t esp) {
    if (t_atomic < SCHED_ATOMIC_MAX)
        t_atomic_esp[t_atomic] = esp;
    ++t_atomic;
}
void sched_atomic_leave() {
    if (t_atomic)
        --t_atomic;
}
void sched_atomic_unwind_to_esp(uint32_t esp) {
    // A stretch entered below `esp` belongs to a frame being unwound.
    while (t_atomic && t_atomic <= SCHED_ATOMIC_MAX && t_atomic_esp[t_atomic - 1] < esp)
        --t_atomic;
}

void sched_checkpoint() {
    // Drain first and unconditionally: a guest with no worker threads never
    // reaches the scheduler proper, and a signal it never applied is a wait
    // that never ends.
    if (g_host_signals_n.load(std::memory_order_relaxed)) {
        g_sched_m.lock();
        apply_host_signals_locked();
        g_sched_m.unlock();
    }
    // Test71: every import call reaches here (thousands per frame), and reading the clock on each
    // cost ~6% of the main thread in busy driving. Read it on every 16th call only: the 1 ms slice
    // is unchanged, a yield is at most 15 calls late (microseconds), and a polling loop such as
    // the frame limiter's GetTickCount spin still yields.
    // RECOMP_SCHED_CHECKPOINT_STRIDE=1 (TEST ONLY) restores a clock read on every call for A/B runs;
    // so does the earlier phase of an in-run A/B (RECOMP_AB_PERIOD, platform/ab_phase.h).
    // Test75: the stride is counted before anything else, so 15 of 16 calls return without touching
    // a thread-local (each one is a _tlv_get_addr call on macOS). The counter is shared: only the
    // baton holder runs guest code, and a lost increment would only move a yield by one call.
    static const uint32_t stride_mask = [] {
        const char *e = recomp_env("SCHED_CHECKPOINT_STRIDE");
        return (e && !strcmp(e, "1")) ? 0u : 15u;
    }();
    static std::atomic<uint32_t> calls{0};
    const uint32_t n = calls.load(std::memory_order_relaxed) + 1;
    calls.store(n, std::memory_order_relaxed);
    if ((n & stride_mask) != 0)
        return;
    if (threads().size() < 2 || t_atomic)
        return;
    GuestThread *me = threads()[t_self];
    double now = sched_now();
    // Remaster S5 (TEST, off by default): RECOMP_SCHED_SLICE_MS=<0.25..50> sets the slice (default 1 ms), so a
    // longer slice can be measured against audio underruns and frame times. No guest code runs in parallel.
    static const double slice = [] {
        const char *e = recomp_env("SCHED_SLICE_MS");
        const double ms = e ? atof(e) : 1.0;
        return (ms >= 0.25 && ms <= 50.0 ? ms : 1.0) / 1000.0;
    }();
    if (now - me->last_yield < slice)
        return;
    me->last_yield = now;
    guest_yield();
}

namespace {

// A shim that changed an object's state calls this so a thread blocked on it
// is reconsidered at the next scheduling pass rather than sleeping out its
// whole timeout.
void sched_wake_all() {
    g_sched_m.lock();
    g_sched_cv.notify_all();
    g_sched_m.unlock();
}

namespace {
struct SleepTrace {
    FILE *f = nullptr;
    uint32_t watch[8] = {};
    int nwatch = 0;
    SleepTrace() {
        const char *path = getenv("RECOMP_SLEEP_TRACE");
        if (!path || !*path)
            return;
        f = fopen(path, "w");
        if (!f)
            return;
        setvbuf(f, nullptr, _IOLBF, 0); // a killed run keeps every line written so far
        if (const char *w = getenv("RECOMP_SLEEP_TRACE_WATCH")) {
            const char *p = w;
            while (*p && nwatch < 8) {
                char *end = nullptr;
                const unsigned long v = strtoul(p, &end, 16);
                if (end == p)
                    break;
                watch[nwatch++] = (uint32_t)v;
                p = *end == ',' ? end + 1 : end;
            }
        }
        fprintf(f, "start_monotonic_ns,caller,requested_ms,actual_ms");
        for (int i = 0; i < nwatch; ++i)
            fprintf(f, ",w%08x", watch[i]);
        fputc('\n', f);
    }
};
SleepTrace &sleep_trace() {
    static SleepTrace t; // constructed once, on the first guest Sleep
    return t;
}
} // namespace

bool sleep_trace_on() {
    return sleep_trace().f != nullptr;
}

void sleep_trace_main(X86 *c, uint32_t ms) {
    SleepTrace &st = sleep_trace();
    if (!cur_thread()->is_main) {
        sched_sleep_ms(ms);
        return;
    }
    const uint32_t caller = rd32(c->r[R_ESP]);
    const uint64_t t0 = os_monotonic_ns();
    sched_sleep_ms(ms);
    const uint64_t t1 = os_monotonic_ns();
    fprintf(st.f, "%llu,%08x,%u,%.3f", (unsigned long long)t0, caller, ms, double(t1 - t0) / 1e6);
    for (int i = 0; i < st.nwatch; ++i)
        fprintf(st.f, ",%u", rd32(st.watch[i]));
    fputc('\n', st.f);
}

void sched_sleep_ms(uint32_t ms) {
    sched_run_checkpoint();
    GuestThread *me = cur_thread();
    if (ms == 0) {
        guest_yield();
        return;
    } // give up the rest of the slice
    g_sched_m.lock();
    me->blocked = true;
    me->wait_kind = W_NONE;
    me->wait_n = 0;
    me->has_deadline = true;
    me->deadline = sched_now() + (double)ms / 1000.0;
    g_sched_m.unlock();
    guest_block("sleep");
}

// WAIT_OBJECT_0 + index, WAIT_TIMEOUT or WAIT_FAILED. Nothing here reports a
// timeout that did not happen: a zero timeout answers from the current state,
// and any other wait is a real block that ends when the object is taken or the
// deadline passes.
uint32_t sched_wait_objects(const uint32_t *handles_, uint32_t count, bool wait_all,
                            uint32_t timeout_ms) {
    sched_run_checkpoint();
    if (!count || count > MAXIMUM_WAIT_OBJECTS_) {
        set_last_error(87);
        return 0xffffffffu;
    }
    for (uint32_t i = 0; i < count; ++i) {
        if (!handle_any(handles_[i])) {
            set_last_error(ERROR_INVALID_HANDLE_);
            return 0xffffffffu;
        }
    }
    GuestThread *me = cur_thread();

    // Satisfiable right now?  Taken here so a zero timeout still consumes.
    g_sched_m.lock();
    bool ready = true, abandoned = false;
    uint32_t index = 0;
    if (wait_all) {
        for (uint32_t i = 0; i < count && ready; ++i)
            if (!object_available(handles_[i], me->id))
                ready = false;
        if (ready)
            for (uint32_t i = 0; i < count; ++i)
                abandoned |= object_take(handles_[i], me->id);
    } else {
        ready = false;
        for (uint32_t i = 0; i < count; ++i)
            if (object_available(handles_[i], me->id)) {
                index = i;
                ready = true;
                break;
            }
        if (ready)
            abandoned = object_take(handles_[index], me->id);
    }
    if (ready) {
        g_sched_m.unlock();
        return (abandoned ? 0x80u : 0u) + index; // WAIT_ABANDONED_0 + i
    }
    if (timeout_ms == 0) {
        g_sched_m.unlock();
        return 0x102;
    }

    me->blocked = true;
    me->wait_kind = W_OBJECTS;
    me->wait_all = wait_all;
    me->wait_n = count;
    for (uint32_t i = 0; i < count; ++i)
        me->wait_h[i] = handles_[i];
    me->has_deadline = timeout_ms != 0xffffffffu; // INFINITE has none
    me->deadline = me->has_deadline ? sched_now() + (double)timeout_ms / 1000.0 : 0.0;
    g_sched_m.unlock();
    return guest_block("wait");
}

void sched_enter_critsec(uint32_t cs) {
    GuestThread *me = cur_thread();
    g_sched_m.lock();
    if (critsec_available(cs, me->id)) {
        critsec_take(cs, me->id);
        g_sched_m.unlock();
        return;
    }
    me->blocked = true;
    me->wait_kind = W_CRITSEC;
    me->wait_cs = cs;
    me->wait_n = 0;
    me->has_deadline = false; // EnterCriticalSection does not time out
    g_sched_m.unlock();
    guest_block("critical section");
}

bool sched_try_critsec(uint32_t cs) {
    GuestThread *me = cur_thread();
    g_sched_m.lock();
    bool got = critsec_available(cs, me->id);
    if (got)
        critsec_take(cs, me->id);
    g_sched_m.unlock();
    return got;
}

void sched_leave_critsec(uint32_t cs) {
    if (!cs || !gm_valid(cs, 24))
        return;
    GuestThread *me = cur_thread();
    g_sched_m.lock();
    if (rd32(cs + CS_OFF_OWNER) != me->id) {
        // Leaving a section this thread does not own is a guest bug; Windows
        // corrupts the section rather than diagnosing it, so say so once.
        log_once("critsec-not-owner",
                 "LeaveCriticalSection(%08x) from thread %u, which does not own it", cs, me->id);
        g_sched_m.unlock();
        return;
    }
    uint32_t rec = rd32(cs + CS_OFF_RECURSION);
    if (rec)
        --rec;
    wr32(cs + CS_OFF_RECURSION, rec);
    wr32(cs + CS_OFF_LOCK_COUNT, rec ? rec - 1 : 0xffffffffu);
    if (!rec) {
        wr32(cs + CS_OFF_OWNER, 0);
        g_sched_cv.notify_all();
    }
    g_sched_m.unlock();
}

// Everything a guest thread must do before it may be seen as finished, run
// exactly once however the thread ends: a normal return, ExitThread, or a
// worker serving ExitProcess.
//
// The ordering is the whole point. It runs while this thread still holds the
// baton and with g_sched_m NOT held, so:
//
//   * mod code unwinding here is still mutually exclusive with every other
//     guest thread, which is what the baton guarantees and what makes the
//     unwind safe;
//   * sched_guest_threads_stopped() cannot yet report this thread finished,
//     so a plugin unload cannot start underneath code that is still running;
//   * the observer cannot be swapped or read as already-fired by a test or a
//     host that is about to look at it.
//
// Publishing `finished` and handing off the baton happens only after this
// returns, and never before.
void thread_run_exit_cleanup(GuestThread *t) {
    if (t->exit_cleanup_done)
        return;
    t->exit_cleanup_done = true;
    recomp_seh_reset(nullptr);
    // The thread is gone, and anything it left on the mod layer's invocation
    // stack goes with it: unwinding to the top of the address space abandons
    // every frame. The weak seam makes this free in a build without mods.
    mods_hooks_unwind_to_esp(0xffffffffu);
    recomp_profile_truncate(0);
    if (g_thread_exit_observer)
        g_thread_exit_observer();
    // Last, so that mod code running in the unwind or the observer still sees
    // a guest thread holding the baton, which is what it is.
    t_is_guest = false;
}

void *thread_host_main(void *arg) {
    GuestThread *t = (GuestThread *)arg;
    t_self = t->index;
    t_is_guest = true;
    t_ctx = &t->ctx;
    t_id = t->id;
    // Evidence only (Test58): the class this worker's host thread runs at. Raising it
    // to the main thread's was measured (A/B) and made no difference; not applied.
    LOGW("sched: guest thread %u runs on a host thread of class %s", t->id, os_thread_class_name());

    g_sched_m.lock();
    while (g_baton != t->index && !g_exit_requested)
        g_sched_cv.wait(g_sched_m);
    bool exiting = g_exit_requested;
    g_sched_m.unlock();
    // Created before the exit, first scheduled after it: Windows would never
    // run this body, so neither does this thread. It still ends through the
    // ordinary path so its handle reports an exit code.
    if (exiting) {
        thread_run_exit_cleanup(t);
        g_sched_m.lock();
        t->finished = true;
        release_mutexes_of_locked(t->id);
        if (HObj *o = handle_any(t->handle)) {
            o->exit_code = 0;
            o->thread_ran = false;
        }
        if (g_baton == t->index)
            sched_handoff_on_exit_locked(t->index, "process exit before start");
        g_sched_m.unlock();
        return nullptr;
    }

    uint32_t start = t->start, param = t->param;
    void (*native_start)(X86 *, uint32_t) = t->native_start;
    uint32_t result = 0;
    if (setjmp(t->exit_jmp) == 0) {
        if (native_start)
            native_start(&t->ctx, param);
        else
            result = guest_call(&t->ctx, start, param);
    } else
        result = t->exit_code;

    // Before anything is published: this thread still holds the baton here.
    thread_run_exit_cleanup(t);

    g_sched_m.lock();
    t->finished = true;
    release_mutexes_of_locked(t->id);
    if (HObj *o = handle_any(t->handle)) {
        o->exit_code = result;
        o->thread_ran = true;
    }
    sched_handoff_on_exit_locked(t->index, "thread ended");
    g_sched_m.unlock();
    return nullptr;
}

// A guest thread asked the process to exit.  Only the main thread can serve
// that, because the landing pad is on its stack, so the flag is set, the baton
// goes to the main thread, and this host thread ends here.  The main thread
// picks the flag up the moment sched_yield returns to it.
void thread_finish_exit_process() {
    GuestThread *me = cur_thread();
    if (me->is_main) {
        g_exited = true;
        g_exit_code = g_exit_requested_code;
        // The request is consumed here: the main thread is performing it, and
        // leaving it set would send this thread back into this function the
        // next time it reached a scheduling point.
        g_exit_requested = false;
        if (g_exit_jmp_valid)
            longjmp(g_exit_jmp, 1);
        exit((int)g_exit_code);
    }
    // This host thread ends in os_thread_exit and never returns to
    // thread_host_main, so the cleanup has to happen here too - and here as
    // well it runs while the baton is still held and before `finished` is
    // published.
    thread_run_exit_cleanup(me);

    g_sched_m.lock();
    me->finished = true;
    release_mutexes_of_locked(me->id);
    sched_handoff_on_exit_locked(me->index, "process exit");
    g_sched_m.unlock();
    os_thread_exit();
}

// ExitProcess called from a guest thread cannot longjmp: the landing pad is on
// the main thread's stack. Record the request, hand the baton to the main
// thread and end this one; the main thread performs the exit the instant it is
// running again. Returns true when it has taken responsibility, in which case
// it never returns at all.
// ExitProcess ends every other thread: Windows runs no further user code on
// them, and the guest has just torn down the objects they were working with.
// Publishing the pending exit is what stops them, because thread_runnable
// offers the baton to no one but the main thread while one is pending. A
// worker parked in a wait stays parked, which is the same thing Windows does
// to it. Called on the thread performing the exit, without g_sched_m.
// Not called for an exit a worker requested: the main thread consumes that
// request when it serves it, because it is the thread that has to come back
// through the landing pad.
void publish_process_exit(uint32_t code) {
    g_sched_m.lock();
    g_exit_requested = true;
    g_exit_requested_code = code;
    g_sched_cv.notify_all();
    g_sched_m.unlock();
}

bool request_process_exit(uint32_t code) {
    if (cur_thread()->is_main)
        return false;
    g_sched_m.lock();
    g_exit_requested = true;
    g_exit_requested_code = code;
    // Wake anything parked: the main thread may be inside an indefinite wait,
    // and it is the only thread that can perform the exit.
    g_sched_cv.notify_all();
    g_sched_m.unlock();
    thread_finish_exit_process();
    return true;
}

// Sets up a guest thread's own address-space furniture: a committed stack, a
// TEB with the SEH head empty and the stack bounds Windows records there, and
// a TLS array with the image's initial block and all other slots zeroed.
// Returns false when the guest heap cannot supply them.
bool thread_prepare_context(GuestThread *t) {
    // Gaps below and above the stack (NFSMW issue 8): the first-fit heap puts
    // the next small kit object right against its low end.
    t->stack_lo = heap_alloc_guarded(THREAD_STACK_BYTES, true, THREAD_STACK_GUARD, "thread stack", t->id);
    if (!t->stack_lo)
        return false;
    t->stack_hi = t->stack_lo + THREAD_STACK_BYTES;
    uint32_t block = heap_alloc(TEB_SIZE + TLS_SLOTS * 4, true, 16);
    if (!block) {
        heap_free_guarded(t->stack_lo);
        t->stack_lo = 0;
        return false;
    }
    t->teb = block;
    t->tls = block + TEB_SIZE;

    memset(&t->ctx, 0, sizeof t->ctx);
    t->ctx.fpu_cw = 0x037f;
    t->ctx.fpu_sw = 0;
    t->ctx.fpu_tag = 0xffff;
    t->ctx.fpu_top = 0;
    t->ctx.eflags_misc = 0x00000202u;
    t->ctx.fs_base = t->teb;
    wr32(t->teb + 0x00, 0xffffffffu); // SEH chain head: empty
    wr32(t->teb + 0x04, t->stack_hi);
    wr32(t->teb + 0x08, t->stack_lo);
    wr32(t->teb + 0x18, t->teb);
    wr32(t->teb + 0x2c, t->tls);
    if (!loader_tls_block_for_thread(t->tls) && loader_tls().index != 0xffffffffu) {
        heap_free(t->teb);
        heap_free_guarded(t->stack_lo);
        t->teb = t->tls = t->stack_lo = t->stack_hi = 0;
        return false;
    }
    t->ctx.r[R_ESP] = (t->stack_hi - 0x20) & ~0xfu;
    t->ctx.r[R_EBP] = 0;
    return true;
}

// The pre-scheduler path, kept for RECOMP_CREATETHREAD=sync: runs a thread body
// to completion on the caller's context.
jmp_buf g_sync_thread_jmp;
bool g_in_sync_thread = false;
uint32_t g_sync_thread_handle = 0;

void run_thread_body(X86 *c, uint32_t h) {
    HObj *o = handle_get(h, H_THREAD);
    if (!o || o->thread_ran)
        return;
    uint32_t start = o->thread_start, param = o->thread_param;
    o->thread_ran = true;
    o->thread_suspended = false;

    jmp_buf saved;
    bool saved_in = g_in_sync_thread;
    uint32_t saved_handle = g_sync_thread_handle;
    memcpy(&saved, &g_sync_thread_jmp, sizeof saved);
    g_in_sync_thread = true;
    g_sync_thread_handle = h;
    // The body runs on a borrowed context: a thread has its own register file,
    // so whichever way it ends the caller gets its own registers back. The
    // snapshot is written before the setjmp and never touched again, and this
    // frame stays live across an ExitThread longjmp, so it survives intact.
    X86 caller_ctx = *c;
    const uint32_t profile_depth = recomp_profile_depth();
    if (setjmp(g_sync_thread_jmp) == 0) {
        uint32_t result = guest_call(c, start, param);
        if (HObj *t = handle_get(h, H_THREAD))
            t->exit_code = result;
    }
    recomp_profile_truncate(profile_depth);
    *c = caller_ctx;
    recomp_seh_frame_leave(c); // retire checkpoints from a synchronous guest worker
    g_in_sync_thread = saved_in;
    g_sync_thread_handle = saved_handle;
    memcpy(&g_sync_thread_jmp, &saved, sizeof saved);
}

// Starts a cooperative thread.  It does not run yet: CreateThread returns to
// its caller first, exactly as on Windows, and the new thread gets the baton
// the first time somebody yields.
bool thread_spawn(uint32_t h) {
    cur_thread(); // main is threads()[0]
    GuestThread *t = new GuestThread();
    t->index = threads().size();
    t->handle = h;
    if (HObj *o = handle_any(h)) {
        t->start = o->thread_start;
        t->param = o->thread_param;
        t->native_start = o->native_start;
    }
    t->id = g_next_thread_id++;
    if (!thread_prepare_context(t)) {
        delete t;
        return false;
    }
    // Under g_sched_m: woken workers read threads() under it without the baton
    // (sched_wait_locked, sched_expire_locked), and a push that reallocates
    // frees the block they index. Released before the host thread starts: its
    // first act is to take the lock and wait for the baton.
    {
        std::lock_guard<std::mutex> l(g_sched_m);
        threads().push_back(t);
    }
    OsThread *host = os_thread_create(thread_host_main, t, 0);
    if (!host) {
        {
            std::lock_guard<std::mutex> l(g_sched_m);
            threads().pop_back();
        }
        if (loader_tls().index != 0xffffffffu)
            heap_free(rd32(t->tls + 4 * loader_tls().index));
        heap_free_guarded(t->stack_lo);
        heap_free(t->teb);
        delete t;
        return false;
    }
    t->tid = os_thread_id_of(host);
    os_thread_detach(host);
    t->spawned = true;
    return true;
}

// A thread the runtime itself needs, as Windows has (the multimedia timer thread): a cooperative guest
// thread with its own context that holds the baton while it runs, whose body is `fn` instead of guest code.
// It calls into the guest with guest_call and blocks with sched_sleep_ms like any other thread. Returns the
// thread handle, or 0 if no context was available.
uint32_t k32_spawn_service_thread(void (*fn)(X86 *, uint32_t), uint32_t param, const char *what) {
    uint32_t h = handle_new(H_THREAD);
    handles()[h].thread_param = param;
    handles()[h].native_start = fn;
    handles()[h].exit_code = STILL_ACTIVE_;
    if (!thread_spawn(h)) {
        LOGW("sched: %s service thread: no context available", what);
        handles()[h].thread_ran = true;
        handles()[h].exit_code = 0;
        return 0;
    }
    handles()[h].thread_ran = true;
    LOGW("sched: %s service -> guest thread %u", what, threads().back()->id);
    return h;
}

// Create the guest thread context and its cooperative host worker.
// Each worker has its own stack/registers, but only the scheduler baton holder executes guest code.
void k_CreateThread(X86 *c) {
    uint32_t start = arg(c, 2), param = arg(c, 3), flags = arg(c, 4), ptid = arg(c, 5);
    uint32_t h = handle_new(H_THREAD);
    handles()[h].thread_start = start;
    handles()[h].thread_param = param;
    handles()[h].exit_code = STILL_ACTIVE_;

    const char *mode = recomp_env("CREATETHREAD");
    if (mode && strcmp(mode, "skip") == 0) {
        LOGW("CreateThread(%08x): skipped (RECOMP_CREATETHREAD=skip)", start);
        handles()[h].thread_ran = true;
        handles()[h].exit_code = 0;
        if (ptid)
            wr32(ptid, 0);
        set_eax(c, h);
        return;
    }
    if (mode && strcmp(mode, "sync") == 0) {
        LOGW("CreateThread(%08x, param=%08x): running synchronously to completion "
             "(RECOMP_CREATETHREAD=sync)",
             start, param);
        handles()[h].exit_code = 0;
        if (ptid)
            wr32(ptid, 1);
        run_thread_body(c, h);
        set_eax(c, h);
        return;
    }

    if (!thread_spawn(h)) {
        LOGW("CreateThread(%08x): no context available, reporting failure", start);
        handles()[h].exit_code = 0;
        handles()[h].thread_ran = true;
        set_last_error(ERROR_NOT_ENOUGH_MEMORY_);
        set_eax(c, 0);
        return;
    }
    GuestThread *t = threads().back();
    LOGW("sched: CreateThread start=%08x param=%08x -> guest thread %u", start, param, t->id);
    if (flags & 0x00000004u) { // CREATE_SUSPENDED
        t->suspend_count = 1;
        handles()[h].thread_suspended = true;
    }
    handles()[h].thread_ran = true; // it exists; it is not a stub
    if (ptid)
        wr32(ptid, t->id);
    LOGV("CreateThread(%08x, param=%08x, flags=%08x) -> handle %08x id %u", start, param, flags, h,
         t->id);
    set_eax(c, h);
}

void k_ExitThread(X86 *c) {
    uint32_t code = arg(c, 0);
    GuestThread *me = cur_thread();
    if (!me->is_main) {
        me->exit_code = code;
        longjmp(me->exit_jmp, 1);
    }
    if (g_in_sync_thread) {
        HObj *o = handle_any(g_sync_thread_handle);
        if (o)
            o->exit_code = code;
        longjmp(g_sync_thread_jmp, 1);
    }
    LOGW("ExitThread(%u) outside a thread body: treating it as ExitProcess", code);
    publish_process_exit(code);
    g_exited = true;
    g_exit_code = code;
    if (g_exit_jmp_valid)
        longjmp(g_exit_jmp, 1);
    set_eax(c, 0);
}

// A thread that has not finished reports STILL_ACTIVE, which is what a caller
// polling this in a spin loop is testing for.  The yield is what lets the
// thread being polled make progress: on Windows the poller would simply be
// descheduled here.
void k_GetExitCodeThread(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_THREAD);
    uint32_t p = arg(c, 1);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    guest_yield();
    o = handle_get(arg(c, 0), H_THREAD);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    if (p)
        wr32(p, o->exit_code);
    set_eax(c, 1);
}

// Both return the PREVIOUS suspend count, and a thread runs again only when
// the count reaches zero: two SuspendThreads need two ResumeThreads.
void k_ResumeThread(X86 *c) {
    uint32_t h = arg(c, 0);
    HObj *o = handle_get(h, H_THREAD);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0xffffffffu);
        return;
    }
    for (GuestThread *t : threads()) {
        if (t->handle != h || !t->spawned)
            continue;
        g_sched_m.lock();
        int32_t was = t->suspend_count;
        if (t->suspend_count > 0)
            --t->suspend_count;
        o->thread_suspended = t->suspend_count > 0;
        if (!t->suspend_count)
            g_sched_cv.notify_all();
        g_sched_m.unlock();
        set_eax(c, (uint32_t)was);
        return;
    }
    if (o->thread_suspended && !o->thread_ran) { // RECOMP_CREATETHREAD=sync
        LOGW("ResumeThread(%08x): running the suspended thread body synchronously", h);
        o->thread_suspended = false;
        run_thread_body(c, h);
        set_eax(c, 1);
        return;
    }
    set_eax(c, 0);
}

void k_SuspendThread(X86 *c) {
    uint32_t h = arg(c, 0);
    HObj *o = handle_get(h, H_THREAD);
    if (!o) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0xffffffffu);
        return;
    }
    GuestThread *me = cur_thread();
    for (GuestThread *t : threads()) {
        if (t->handle != h || !t->spawned)
            continue;
        g_sched_m.lock();
        int32_t was = t->suspend_count++;
        o->thread_suspended = true;
        g_sched_m.unlock();
        set_eax(c, (uint32_t)was);
        // Suspending yourself has to take effect before the call returns, so
        // it deschedules here and comes back only once somebody resumes it.
        if (t == me)
            guest_block("self-suspend");
        return;
    }
    set_eax(c, 0);
}

void k_GetCurrentThread(X86 *c) {
    set_eax(c, 0xfffffffeu);
}
void k_GetCurrentProcess(X86 *c) {
    set_eax(c, 0xffffffffu);
}
void k_GetCurrentThreadId(X86 *c) {
    set_eax(c, cur_thread_id());
}
void k_GetThreadPriority(X86 *c) {
    set_eax(c, 0);
}
void k_SetThreadPriority(X86 *c) {
    set_eax(c, 1);
}

void k_ExitProcess(X86 *c) {
    LOGW("ExitProcess(%u)", arg(c, 0));
    if (request_process_exit(arg(c, 0)))
        return; // never returns
    publish_process_exit(arg(c, 0));
    g_exit_code = arg(c, 0);
    g_exited = true;
    if (g_exit_jmp_valid)
        longjmp(g_exit_jmp, 1);
    // No landing pad was installed (the guest was not started through
    // run_entry): ExitProcess must not return, so end the host process.
    exit((int)g_exit_code);
}

void k_TerminateProcess(X86 *c) {
    LOGW("TerminateProcess(%u)", arg(c, 1));
    if (request_process_exit(arg(c, 1)))
        return; // never returns
    publish_process_exit(arg(c, 1));
    g_exit_code = arg(c, 1);
    g_exited = true;
    if (g_exit_jmp_valid)
        longjmp(g_exit_jmp, 1);
    exit((int)g_exit_code);
}

// -------------------------------------------------------------------------
// Exceptions
// -------------------------------------------------------------------------
void k_SetUnhandledExceptionFilter(X86 *c) {
    uint32_t prev = g_unhandled_filter;
    g_unhandled_filter = arg(c, 0);
    set_eax(c, prev);
}
void k_UnhandledExceptionFilter(X86 *c) {
    set_eax(c, 1);
} // EXCEPTION_EXECUTE_HANDLER
// There is no SEH in the runtime, so an exception the guest raises cannot be
// delivered anywhere. Continuing would run the guest past a point it never
// reaches on Windows, so both abort with the state that caused them.
} // namespace

// Generated code at a title's verified busy-wait edge (translate.py POLL_CHECKPOINTS). The checkpoint yields
// the baton only after the current slice and never inside an atomic stretch, exactly as at an import call.
extern "C" void recomp_poll_checkpoint(X86 *c) {
    (void)c;
    sched_checkpoint();
}

// External-linkage entry points for the multimedia timer service in misc.cpp (both helpers are internal here).
uint32_t recomp_spawn_service_thread(void (*fn)(X86 *, uint32_t), uint32_t param, const char *what) {
    return k32_spawn_service_thread(fn, param, what);
}
void recomp_service_sleep_ms(uint32_t ms) {
    sched_sleep_ms(ms);
}

static bool printable_guest_string(uint32_t a, std::string *out) {
    if (!a || !gm_valid(a, 1))
        return false;
    std::string s = gm_str(a, 200);
    if (s.size() < 2)
        return false;
    for (char ch : s)
        if (!(ch == '\t' || ch == '\n' || (ch >= 0x20 && ch < 0x7f)))
            return false;
    *out = s;
    return true;
}

std::string win32_describe_cxx_throw(uint32_t code, uint32_t nargs, uint32_t args) {
    if (code != 0xe06d7363u || nargs < 3 || !args || !gm_valid(args, 12))
        return std::string();
    uint32_t object = rd32(args + 4), throw_info = rd32(args + 8);
    std::string text;
    // ThrowInfo -> CatchableTypeArray -> CatchableType[0] -> TypeDescriptor -> name at +8.
    if (throw_info && gm_valid(throw_info, 16)) {
        uint32_t array = rd32(throw_info + 12);
        if (array && gm_valid(array, 8) && rd32(array) >= 1) {
            uint32_t catchable = rd32(array + 4);
            if (catchable && gm_valid(catchable, 8)) {
                uint32_t type = rd32(catchable + 4);
                std::string name;
                if (type && printable_guest_string(type + 8, &name))
                    text += "type " + name;
            }
        }
    }
    std::string what;
    if (object && gm_valid(object, 8) && printable_guest_string(rd32(object + 4), &what))
        text += (text.empty() ? "" : ", ") + std::string("message \"") + what + "\"";
    // A class of the game's own keeps its text wherever it likes: quote every
    // dword of the object that points at text, with its offset.
    for (uint32_t off = 0; object && off < 32 && gm_valid(object + off, 4); off += 4) {
        std::string s;
        if (off != 4 && printable_guest_string(rd32(object + off), &s))
            text += (text.empty() ? "" : ", ") + std::string("+") + std::to_string(off) + " \"" +
                    s + "\"";
    }
    // And the raw dwords, for fields that are numbers: a line, a code, a count.
    std::string raw;
    for (uint32_t off = 0; object && off < 32 && gm_valid(object + off, 4); off += 4) {
        char buf[16];
        snprintf(buf, sizeof buf, "%s%08x", off ? " " : "", rd32(object + off));
        raw += buf;
    }
    if (!raw.empty())
        text += (text.empty() ? "" : ", ") + std::string("object [") + raw + "]";
    return text;
}

std::string win32_describe_pointer(uint32_t value) {
    // object -> vtable; vtable[-1] -> RTTICompleteObjectLocator; +12 -> TypeDescriptor;
    // +8 -> the mangled name.  Every hop is checked before it is taken, and the
    // name has to look like one MSVC writes.
    if (!value || !gm_valid(value, 4))
        return std::string();
    uint32_t vtbl = rd32(value);
    if (vtbl < 4 || !gm_valid(vtbl - 4, 4))
        return std::string();
    uint32_t col = rd32(vtbl - 4);
    if (!col || !gm_valid(col, 16))
        return std::string();
    uint32_t type = rd32(col + 12);
    if (!type || !gm_valid(type, 12))
        return std::string();
    std::string name;
    if (!printable_guest_string(type + 8, &name) || name.compare(0, 2, ".?") != 0)
        return std::string();
    return name;
}

std::vector<uint32_t> win32_stack_return_candidates(uint32_t esp, uint32_t bytes, uint32_t lo,
                                                    uint32_t hi, size_t max) {
    std::vector<uint32_t> out;
    for (uint32_t at = esp; at + 4 <= esp + bytes && out.size() < max; at += 4) {
        if (!gm_valid(at, 4))
            break;
        uint32_t v = rd32(at);
        if (v < lo + 2 || v >= hi)
            continue;
        // The byte patterns of a CALL whose next instruction is v: E8 rel32 (5
        // bytes), FF /2 with a register or [reg] (2 bytes), FF /2 [reg+disp8]
        // (3), FF /2 [reg+disp32] (6), FF 15 [disp32] (6).
        bool call = false;
        if (v >= lo + 5 && rd8(v - 5) == 0xe8)
            call = true;
        else if (v >= lo + 2 && rd8(v - 2) == 0xff && (rd8(v - 1) & 0x38) == 0x10 &&
                 (rd8(v - 1) >> 6) != 1 && (rd8(v - 1) & 7) != 4 && (rd8(v - 1) >> 6) != 2)
            call = true;
        else if (v >= lo + 3 && rd8(v - 3) == 0xff && (rd8(v - 2) & 0xf8) == 0x50)
            call = true;
        else if (v >= lo + 6 && rd8(v - 6) == 0xff &&
                 ((rd8(v - 5) & 0xf8) == 0x90 || rd8(v - 5) == 0x15))
            call = true;
        if (call)
            out.push_back(v);
    }
    return out;
}

std::vector<uint32_t> win32_return_chain(uint32_t ebp, size_t max) {
    std::vector<uint32_t> out;
    while (ebp && gm_valid(ebp, 8) && out.size() < max) {
        uint32_t ret = rd32(ebp + 4), next = rd32(ebp);
        if (!loader_in_image(ret))
            break;
        out.push_back(ret);
        if (next <= ebp)
            break; // frames grow upwards; anything else is not a chain
        ebp = next;
    }
    return out;
}

namespace {

void k_RaiseException(X86 *c) {
    // A raise the guest goes on to handle is invisible in a run log otherwise,
    // and the frames it climbed out of are exactly what a language exception
    // hides: the log shows the dialog that reported it and nothing that led
    // there. Verbose only - a Delphi program raises to signal, not to fail.
    if (log_level() >= 2) {
        std::vector<uint32_t> chain = win32_return_chain(c->r[R_EBP], 12);
        std::string frames;
        for (uint32_t r : chain) {
            char b[16];
            snprintf(b, sizeof b, " %08x", r);
            frames += b;
        }
        LOGV("RaiseException(code=%08x nargs=%u arg0=%08x) from ret=%08x, frames:%s", arg(c, 0),
             arg(c, 2), arg(c, 2) && gm_valid(arg(c, 3), 4) ? rd32(arg(c, 3)) : 0,
             rd32(c->r[R_ESP]), frames.c_str());
    }
    if (recomp_seh_raise(c, arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3)))
        return; // only the testing runtime can return from an unhandled raise
    LOGW("RaiseException(code=%08x flags=%08x nargs=%u args=%08x) at ESP=%08x: "
         "unhandled, aborting",
         arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), c->r[R_ESP]);
    std::string what = win32_describe_cxx_throw(arg(c, 0), arg(c, 2), arg(c, 3));
    if (!what.empty())
        LOGW("  a C++ throw: %s", what.c_str());
    std::vector<uint32_t> chain = win32_return_chain(c->r[R_EBP], 12);
    for (size_t i = 0; i < chain.size(); ++i)
        LOGW("  frame %zu returns to %08x", i, chain[i]);
    static const char *const regs[8] = {"EAX", "ECX", "EDX", "EBX", "ESP", "EBP", "ESI", "EDI"};
    std::string objects;
    for (int i = 0; i < 8; ++i) {
        std::string what = win32_describe_pointer(c->r[i]);
        if (!what.empty())
            objects += std::string(" ") + regs[i] + "=" + what;
    }
    if (!objects.empty())
        LOGW("  registers holding objects:%s", objects.c_str());
    std::vector<uint32_t> scan = win32_stack_return_candidates(
        c->r[R_ESP], 0x400, loader_image_base(), loader_image_limit(), 24);
    std::string line;
    for (uint32_t v : scan) {
        char buf[16];
        snprintf(buf, sizeof buf, " %08x", v);
        line += buf;
    }
    if (!line.empty())
        LOGW("  return addresses on the stack, newest first:%s", line.c_str());
    abort();
}
void k_RtlUnwind(X86 *c) {
    recomp_seh_unwind(c, arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3));
}

// -------------------------------------------------------------------------
// Strings, code pages, locale
// -------------------------------------------------------------------------
void k_lstrlenA(X86 *c) {
    set_eax(c, (uint32_t)gm_str(arg(c, 0)).size());
}

void k_lstrcpyA(X86 *c) {
    uint32_t d = arg(c, 0);
    std::string s = gm_str(arg(c, 1));
    if (d) {
        memcpy(g_mem + d, s.c_str(), s.size() + 1);
    }
    set_eax(c, d);
}

void k_lstrcatA(X86 *c) {
    uint32_t d = arg(c, 0);
    std::string a = gm_str(d), b = gm_str(arg(c, 1));
    if (d) {
        std::string r = a + b;
        memcpy(g_mem + d, r.c_str(), r.size() + 1);
    }
    set_eax(c, d);
}

void k_IsDBCSLeadByte(X86 *c) {
    set_eax(c, 0);
}
void k_GetACP(X86 *c) {
    set_eax(c, 1252);
}
void k_GetOEMCP(X86 *c) {
    set_eax(c, 437);
}

void k_GetCPInfo(X86 *c) {
    uint32_t p = arg(c, 1);
    if (p) {
        memset(g_mem + p, 0, 20);
        wr32(p, 1);      // MaxCharSize
        wr8(p + 4, '?'); // DefaultChar
    }
    set_eax(c, 1);
}

void k_MultiByteToWideChar(X86 *c) {
    uint32_t src = arg(c, 2);
    int32_t srclen = (int32_t)arg(c, 3);
    uint32_t dst = arg(c, 4);
    int32_t dstlen = (int32_t)arg(c, 5);
    std::string s =
        srclen < 0 ? gm_str(src) : std::string((const char *)(g_mem + src), (size_t)srclen);
    uint32_t need = (uint32_t)s.size() + (srclen < 0 ? 1 : 0);
    if (dstlen == 0 || !dst) {
        set_eax(c, need);
        return;
    }
    uint32_t n = need < (uint32_t)dstlen ? need : (uint32_t)dstlen;
    for (uint32_t i = 0; i < n; ++i)
        wr16(dst + 2 * i, (uint16_t)(uint8_t)(i < s.size() ? s[i] : 0));
    if (recomp_env("TRACE_FILES") && srclen > 256)
        LOGW("file: MultiByteToWideChar cp=%u srclen=%d dstlen=%d -> %u", arg(c, 0), srclen, dstlen,
             n);
    set_eax(c, n);
}

void k_WideCharToMultiByte(X86 *c) {
    uint32_t src = arg(c, 2);
    int32_t srclen = (int32_t)arg(c, 3);
    uint32_t dst = arg(c, 4);
    int32_t dstlen = (int32_t)arg(c, 5);
    std::string s;
    for (int32_t i = 0; srclen < 0 || i < srclen; ++i) {
        uint16_t w = rd16(src + 2 * i);
        if (srclen < 0 && !w)
            break;
        s.push_back((char)(w < 256 ? w : '?'));
    }
    uint32_t need = (uint32_t)s.size() + (srclen < 0 ? 1 : 0);
    if (dstlen == 0 || !dst) {
        set_eax(c, need);
        return;
    }
    uint32_t n = need < (uint32_t)dstlen ? need : (uint32_t)dstlen;
    for (uint32_t i = 0; i < n; ++i)
        wr8(dst + i, (uint8_t)(i < s.size() ? s[i] : 0));
    set_eax(c, n);
}

// CT_CTYPE1 flags for the ASCII range, enough for the CRT's isctype tables.
uint16_t ctype1(unsigned char ch) {
    uint16_t f = 0;
    if (isupper(ch))
        f |= 0x0001;
    if (islower(ch))
        f |= 0x0002;
    if (isdigit(ch))
        f |= 0x0004;
    if (isspace(ch))
        f |= 0x0008;
    if (ispunct(ch))
        f |= 0x0010;
    if (iscntrl(ch))
        f |= 0x0020;
    if (ch == ' ')
        f |= 0x0040;
    if (isxdigit(ch))
        f |= 0x0080;
    if (isalpha(ch))
        f |= 0x0100;
    return f;
}

// There is no instruction cache to flush: the guest's code is the translation,
// and bytes it writes are never executed. Returning success is what lets a
// runtime that patches its own thunks carry on.
void k_FlushInstructionCache(X86 *c) {
    set_eax(c, 1);
}

void k_GetStringTypeA(X86 *c) {
    uint32_t type = arg(c, 1), src = arg(c, 2);
    int32_t len = (int32_t)arg(c, 3);
    uint32_t out = arg(c, 4);
    if (type != 1) {
        log_once("GetStringTypeA-type", "GetStringTypeA: only CT_CTYPE1 is implemented");
    }
    std::string s = len < 0 ? gm_str(src) : std::string((const char *)(g_mem + src), (size_t)len);
    for (size_t i = 0; i < s.size(); ++i)
        wr16(out + 2 * (uint32_t)i, ctype1((unsigned char)s[i]));
    set_eax(c, 1);
}

void k_GetStringTypeW(X86 *c);
// The Ex form carries a locale first; the rest is GetStringTypeW.
void k_GetStringTypeExW(X86 *c) {
    uint32_t saved = c->r[R_ESP];
    c->r[R_ESP] += 4;
    k_GetStringTypeW(c);
    c->r[R_ESP] = saved;
}
void k_GetStringTypeW(X86 *c) {
    uint32_t type = arg(c, 0), src = arg(c, 1);
    int32_t len = (int32_t)arg(c, 2);
    uint32_t out = arg(c, 3);
    if (type != 1) {
        log_once("GetStringTypeW-type", "GetStringTypeW: only CT_CTYPE1 is implemented");
    }
    for (int32_t i = 0; len < 0 || i < len; ++i) {
        uint16_t w = rd16(src + 2 * i);
        if (len < 0 && !w)
            break;
        wr16(out + 2 * (uint32_t)i, ctype1((unsigned char)(w < 256 ? w : 0)));
    }
    set_eax(c, 1);
}

void k_LCMapStringA(X86 *c) {
    uint32_t flags = arg(c, 1), src = arg(c, 2);
    int32_t srclen = (int32_t)arg(c, 3);
    uint32_t dst = arg(c, 4);
    int32_t dstlen = (int32_t)arg(c, 5);
    std::string s =
        srclen < 0 ? gm_str(src) : std::string((const char *)(g_mem + src), (size_t)srclen);
    if (flags & 0x00000100)
        for (char &ch : s)
            ch = (char)tolower((unsigned char)ch); // LCMAP_LOWERCASE
    if (flags & 0x00000200)
        for (char &ch : s)
            ch = (char)toupper((unsigned char)ch); // LCMAP_UPPERCASE
    uint32_t need = (uint32_t)s.size() + (srclen < 0 ? 1 : 0);
    if (!dstlen || !dst) {
        set_eax(c, need);
        return;
    }
    uint32_t n = need < (uint32_t)dstlen ? need : (uint32_t)dstlen;
    memcpy(g_mem + dst, s.c_str(), n);
    set_eax(c, n);
}

void k_LCMapStringW(X86 *c) {
    uint32_t flags = arg(c, 1), src = arg(c, 2);
    int32_t srclen = (int32_t)arg(c, 3);
    uint32_t dst = arg(c, 4);
    int32_t dstlen = (int32_t)arg(c, 5);
    std::vector<uint16_t> s;
    for (int32_t i = 0; srclen < 0 || i < srclen; ++i) {
        uint16_t w = rd16(src + 2 * i);
        if (srclen < 0 && !w) {
            s.push_back(0);
            break;
        }
        if (w < 256) {
            if (flags & 0x00000100)
                w = (uint16_t)tolower((int)w);
            if (flags & 0x00000200)
                w = (uint16_t)toupper((int)w);
        }
        s.push_back(w);
    }
    uint32_t need = (uint32_t)s.size();
    if (!dstlen || !dst) {
        set_eax(c, need);
        return;
    }
    uint32_t n = need < (uint32_t)dstlen ? need : (uint32_t)dstlen;
    for (uint32_t i = 0; i < n; ++i)
        wr16(dst + 2 * i, s[i]);
    set_eax(c, n);
}

void k_CompareStringA(X86 *c) {
    uint32_t flags = arg(c, 1);
    int32_t l1 = (int32_t)arg(c, 3), l2 = (int32_t)arg(c, 5);
    std::string a =
        l1 < 0 ? gm_str(arg(c, 2)) : std::string((const char *)(g_mem + arg(c, 2)), (size_t)l1);
    std::string b =
        l2 < 0 ? gm_str(arg(c, 4)) : std::string((const char *)(g_mem + arg(c, 4)), (size_t)l2);
    int r = (flags & 1) ? os_strcasecmp(a.c_str(), b.c_str()) : strcmp(a.c_str(), b.c_str());
    set_eax(c, r < 0 ? 1 : r == 0 ? 2 : 3);
}

void k_CompareStringW(X86 *c) {
    uint32_t flags = arg(c, 1);
    uint32_t p1 = arg(c, 2), p2 = arg(c, 4);
    int32_t l1 = (int32_t)arg(c, 3), l2 = (int32_t)arg(c, 5);
    std::string a, b;
    for (int32_t i = 0; l1 < 0 || i < l1; ++i) {
        uint16_t w = rd16(p1 + 2 * i);
        if (l1 < 0 && !w)
            break;
        a.push_back((char)w);
    }
    for (int32_t i = 0; l2 < 0 || i < l2; ++i) {
        uint16_t w = rd16(p2 + 2 * i);
        if (l2 < 0 && !w)
            break;
        b.push_back((char)w);
    }
    int r = (flags & 1) ? os_strcasecmp(a.c_str(), b.c_str()) : strcmp(a.c_str(), b.c_str());
    set_eax(c, r < 0 ? 1 : r == 0 ? 2 : 3);
}

// A minimal en-US locale: the CRT asks for decimal separators and date order.
const char *locale_info(uint32_t lctype) {
    switch (lctype & 0xffff) {
    case 0x0002:
        return "en-US"; // LOCALE_SLANGUAGE (abbreviated)
    case 0x000e:
        return "."; // LOCALE_SDECIMAL
    case 0x000f:
        return ","; // LOCALE_STHOUSAND
    case 0x0014:
        return "$"; // LOCALE_SCURRENCY
    case 0x001d:
        return "/"; // LOCALE_SDATE
    case 0x001e:
        return ":"; // LOCALE_STIME
    case 0x0021:
        return "0"; // LOCALE_IDATE (MDY)
    case 0x0025:
        return "M/d/yy"; // LOCALE_SSHORTDATE
    case 0x0028:
        return "h:mm:ss tt"; // LOCALE_STIMEFORMAT
    case 0x1004:
        return "1252"; // LOCALE_IDEFAULTANSICODEPAGE
    default:
        return "";
    }
}

// The Visual C++ 6 CRT's start-up asks about the environment and the locale
// before main: nothing is set, and the answer is the one en-US machine every
// game of the era saw.
void k_GetEnvironmentVariableA(X86 *c) {
    set_last_error(203); // ERROR_ENVVAR_NOT_FOUND
    set_eax(c, 0);
}
void k_GetUserDefaultLCID(X86 *c) {
    set_eax(c, 0x0409);
}
void k_IsValidCodePage(X86 *c) {
    uint32_t cp = arg(c, 0);
    set_eax(
        c, (cp == 0 || cp == 1 || cp == 437 || cp == 850 || cp == 1200 || cp == 1252 || cp == 65001)
               ? 1
               : 0);
}
void k_IsValidLocale(X86 *c) {
    uint32_t lcid = arg(c, 0);
    set_eax(c, (lcid == 0x0409 || lcid == 0x0009 || lcid == 0x0400 || lcid == 0x0800) ? 1 : 0);
}
void k_EnumSystemLocalesA(X86 *c) {
    // The CRT enumerates to find a locale matching a name it was given; with
    // none offered it falls back to the default, so no callback is made.
    set_eax(c, 1);
}

// GlobalMemoryStatus(MEMORYSTATUS*): the machine a game of this era sized its
// caches for.  A quarter of half a gigabyte is in use; the virtual space is a
// 32-bit process's 2 GB less the reserved top pages.
// SetErrorMode(mode) returns the previous mode; nothing here shows a critical-
// error box either way.  GetLogicalDrives: one fixed disk, C:.
static uint32_t g_error_mode = 0;
void k_SetErrorMode(X86 *c) {
    uint32_t prev = g_error_mode;
    g_error_mode = arg(c, 0);
    set_eax(c, prev);
}
void k_GetLogicalDrives(X86 *c) {
    set_eax(c, 0x4);
}

void k_GlobalMemoryStatus(X86 *c) {
    uint32_t p = arg(c, 0);
    if (!p)
        return;
    const uint32_t mb = 1024u * 1024u;
    wr32(p + 0, 32);           // dwLength
    wr32(p + 4, 25);           // dwMemoryLoad, percent
    wr32(p + 8, 512u * mb);    // dwTotalPhys
    wr32(p + 12, 384u * mb);   // dwAvailPhys
    wr32(p + 16, 1024u * mb);  // dwTotalPageFile
    wr32(p + 20, 768u * mb);   // dwAvailPageFile
    wr32(p + 24, 0x7ffe0000u); // dwTotalVirtual
    wr32(p + 28, 0x60000000u); // dwAvailVirtual
}

void k_GetLocaleInfoA(X86 *c) {
    uint32_t lctype = arg(c, 1), buf = arg(c, 2), len = arg(c, 3);
    const char *s = locale_info(lctype);
    uint32_t need = (uint32_t)strlen(s) + 1;
    if (!len || !buf) {
        set_eax(c, need);
        return;
    }
    set_eax(c, gm_put_str(buf, s, len) + 1);
}

void k_GetLocaleInfoW(X86 *c) {
    uint32_t lctype = arg(c, 1), buf = arg(c, 2), len = arg(c, 3);
    const char *s = locale_info(lctype);
    uint32_t need = (uint32_t)strlen(s) + 1;
    if (!len || !buf) {
        set_eax(c, need);
        return;
    }
    uint32_t n = need < len ? need : len;
    for (uint32_t i = 0; i < n; ++i)
        wr16(buf + 2 * i, (uint16_t)(uint8_t)s[i]);
    set_eax(c, n);
}

} // namespace

// ---------------------------------------------------------------------------
// The run thread's own ending.
//
// A worker announces its exit through thread_host_main's tail: cleanup, then
// finished, then hand the baton on. The run thread never went through that,
// because the scheduler did not spawn it - run_entry only cleared the
// thread-local flag, which tells the scheduler nothing about the baton. So a
// worker still parked waiting for the run thread's baton waited for a thread
// that had already stopped running guest code, and a host polling for
// quiescence polled until its bound expired. This is the same tail, for the
// one thread that never had it.
// ---------------------------------------------------------------------------
// Unwinds every mod invocation this thread abandoned, unconditionally and
// without touching the scheduler. The frames are thread-local, so this is safe
// on any thread and says nothing about the baton; the fixture path needs it
// because its teardown thread never registered with the scheduler and so can
// never be recognised by sched_run_thread_finished.
void sched_run_thread_unwind_frames() {
    recomp_seh_reset(nullptr);
    mods_hooks_unwind_to_esp(0xffffffffu);
    recomp_profile_truncate(0);
}

// Retire the calling guest thread and unwind its abandoned mod invocations.
// Transfer execution only when this thread owns the baton, including during forced shutdown.
void sched_run_thread_finished() {
    GuestThread *t = nullptr;
    bool have_baton = false;
    g_sched_m.lock();
    if (!threads().empty() && t_self < threads().size()) {
        GuestThread *candidate = threads()[t_self];
        // Only the thread that registered itself may declare itself finished.
        // A host thread that never ran guest code has t_self 0 by default, and
        // without this check it would retire the main thread on its behalf.
        if (os_thread_self() == candidate->tid && !candidate->finished) {
            t = candidate;
            have_baton = (g_baton == t_self);
        }
    }
    g_sched_m.unlock();
    if (!t)
        return;

    // Outside the lock, and before `finished`: the cleanup unwinds this
    // thread's abandoned mod invocations and calls the exit observer, and
    // neither may run under the scheduler's mutex. Both touch only this
    // thread's own state, so they are correct whoever holds the baton.
    thread_run_exit_cleanup(t);

    g_sched_m.lock();
    t->finished = true;
    release_mutexes_of_locked(t->id);
    // The HANDOFF is the part that needs the baton. This can be reached from a
    // forced unwind delivered during an idle slice, at which point another
    // worker may own the baton and be running; assigning it from here would
    // authorise a second runner and there would then be two threads executing
    // guest code. A thread that does not hold the baton simply marks itself
    // finished: the holder hands off when it next yields or exits, and skips
    // this one because it is finished.
    if (have_baton && g_baton == t_self)
        sched_handoff_on_exit_locked(t->index, "the run thread finished");
    else
        LOGV("sched: run thread %zu finished without the baton; the holder "
             "will hand off",
             t_self);
    g_sched_m.unlock();
}

// ---------------------------------------------------------------------------
// Drives the scheduler until every spawned guest thread has stopped, or
// `timeout_seconds` elapses. Returns true when they have stopped.
//
// THE DRIVER IS NOT AN OUTSIDE ACTOR, and the previous one being outside is
// the whole of what was wrong with it.
//
// It used to look at the table, decide nobody was running, and assign the
// baton itself. Both halves were unsound. A worker sets `blocked` and then
// RELEASES the scheduler mutex before parking in guest_block, so a driver
// reading `blocked` as "parked" hands the baton away while that worker is
// still on its way to the park; the worker then returns without the baton and
// runs beside whoever got it. And expiring deadlines from outside satisfies
// waits and releases mutexes and critical sections that a running thread may
// be inside, which the scheduler's mutex does not protect.
//
// So this thread joins the scheduler instead. It is the run thread - the same
// host thread, retired a moment ago by sched_run_thread_finished - coming back
// as itself: it registers, takes the baton through the ordinary handoff, and
// gives it up through the ordinary holder yield, which is the same code a
// guest Sleep(1) runs. Every baton movement then happens on the one path that
// sequences release and park correctly, and nothing here ever touches a thread
// that might be running.
//
// It is the run thread returning rather than a new registration, so no
// refcount is taken and none is owed.
// ---------------------------------------------------------------------------
bool g_drive_open = false;
bool g_drive_was_finished = false;

// Drive ordinary scheduler handoffs until guest workers stop or the shutdown deadline expires.
// The returning caller keeps a valid scheduler registration for the remaining cleanup sequence.
bool sched_drive_until_stopped(double timeout_seconds) {
    const double until = sched_now() + (timeout_seconds > 0 ? timeout_seconds : 0);
    const double kYield = 0.001; // a guest Sleep(1), which is what this is

    sched_set_guest_thread(true);
    g_sched_m.lock();
    if (threads().empty() || t_self >= threads().size()) {
        g_sched_m.unlock();
        sched_set_guest_thread(false);
        return true; // no scheduler to drive
    }
    const size_t me = t_self;
    GuestThread *t = threads()[me];
    const bool was_finished = t->finished;
    // Back on the roster, and runnable: a finished thread is never handed the
    // baton, so it could not take part.
    t->finished = false;
    t->blocked = false;
    t->has_deadline = false;
    t->wait_kind = W_NONE;
    t->wait_n = 0;

    // Take the baton the ordinary way: wait for whoever holds it to yield.
    while (g_baton != me && !sched_guest_threads_stopped_locked()) {
        if (sched_now() >= until)
            break;
        if (g_baton >= threads().size() || threads()[g_baton]->finished) {
            g_baton = me; // stranded on a thread that cannot hand it on
            break;
        }
        sched_wait_locked(me, 0.0, HOST_IDLE_BATON_SLICE);
    }

    bool stopped = false;
    while (g_baton == me) {
        if (sched_guest_threads_stopped_locked()) {
            stopped = true;
            break;
        }
        if (sched_now() >= until)
            break;
        // Yield as a guest Sleep does, and for the same reason: a runnable
        // worker takes the baton by the ordinary handoff, runs, and hands it
        // back when it parks or finishes. Deadlines are expired inside that
        // path, by the holder, which is the only thread entitled to.
        t->blocked = true;
        t->wait_kind = W_NONE;
        t->wait_n = 0;
        t->has_deadline = true;
        t->deadline = sched_now() + kYield;
        t->last_yield = sched_now();
        sched_run_others_locked(me, "shutdown drive");
        t->blocked = false;
        t->has_deadline = false;
    }
    if (!stopped && sched_guest_threads_stopped_locked())
        stopped = true;

    if (!stopped)
        LOGW("sched: drove for %.1fs and a spawned guest thread is still "
             "running; the caller's precondition - that every worker ends on "
             "its own - did not hold, so this returns false rather than "
             "waiting for one that may never end",
             timeout_seconds);

    // Registered, and holding the baton if it can be held.
    //
    // The caller runs the mods' exit handlers next, and an exit handler is
    // ordinary mod code: it may remove its own hook, drop an overlay layer, do
    // anything the API allows. Every one of those needs the baton, and a
    // caller that had deregistered first would have them queued or refused on
    // the very thread that is tearing the process down - with nothing left to
    // apply the queue. So the drive ends still registered and still holding,
    // and sched_drive_release() puts the roster back afterwards.
    t->blocked = false;
    t->has_deadline = false;
    if (g_baton != me && (g_baton >= threads().size() || threads()[g_baton]->finished))
        g_baton = me;
    g_drive_was_finished = was_finished;
    g_drive_open = true;
    g_sched_m.unlock();
    return stopped;
}

// Ends what sched_drive_until_stopped began: the exits have run, so the roster
// goes back to what it was and the registration goes away.
void sched_drive_release(void) {
    if (!g_drive_open)
        return;
    g_sched_m.lock();
    g_drive_open = false;
    if (!threads().empty() && t_self < threads().size()) {
        GuestThread *t = threads()[t_self];
        t->blocked = false;
        t->has_deadline = false;
        if (g_drive_was_finished)
            t->finished = true;
        if (g_baton == t_self) {
            // To a worker that can use it, or to nobody at all.
            //
            // The exit handoff falls back to thread 0, which is right for a
            // guest thread ending mid-run - thread 0 re-enters the scheduler
            // and sleeps on the deadlines. It is wrong here: thread 0 IS this
            // thread, retired, and a baton parked on a thread that will never
            // yield strands every blocked worker behind it. A free baton is
            // not a lost one: the next drive takes it.
            size_t next = sched_next_runnable_locked(t_self);
            if (next != NO_THREAD && !threads()[next]->finished) {
                g_baton = next;
                record_handoff_locked(t_self, next, "the shutdown driver released");
            } else {
                g_baton = NO_THREAD;
            }
            g_sched_cv.notify_all();
        }
    }
    g_sched_m.unlock();
    sched_set_guest_thread(false);
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------
// Encoding-independent bodies shared by the ANSI and wide import tables.
void create_file_named(X86 *c, const std::string &name) {
    if (recomp_env("TRACE_FILES"))
        LOGW("file: open \"%s\" -> \"%s\"", name.c_str(), win32_host_path(name).c_str());
    uint32_t access = arg(c, 1), disp = arg(c, 4);
    bool want_write = (access & 0x40000000u) != 0; // GENERIC_WRITE
    bool create = (disp == 1 || disp == 2 || disp == 4 || disp == 5);
    // A create or a truncation is a write from the start. Writing to an
    // existing file is not, yet: a game opens its archives read/write and
    // never writes them, and treating that open as a write copied every
    // archive into the profile. Such a handle opens through the read tier and
    // moves to the write tier at its first WriteFile (file_promote_for_write).
    bool deferred = want_write && !create;
    int op = (want_write && !deferred) ? WIN32_FILE_WRITE : WIN32_FILE_READ;
    std::string host = win32_host_path_op(name, op);
    if (host.empty()) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        LOGV("CreateFileA(%s): not found", name.c_str());
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    int flags = want_write ? (access & 0x80000000u ? OS_O_RDWR : OS_O_WRONLY) : OS_O_RDONLY;
    int wanted = flags;
    if (deferred)
        flags = OS_O_RDONLY;
    switch (disp) {
    case 1:
        flags |= OS_O_CREAT | OS_O_EXCL;
        break; // CREATE_NEW
    case 2:
        flags |= OS_O_CREAT | OS_O_TRUNC;
        break; // CREATE_ALWAYS
    case 4:
        flags |= OS_O_CREAT;
        break; // OPEN_ALWAYS
    case 5:
        flags |= OS_O_TRUNC;
        break; // TRUNCATE_EXISTING
    default:
        break; // OPEN_EXISTING
    }
    int fd = os_fd_open(host.c_str(), flags);
    if (fd < 0) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    if (create)
        win32_invalidate_dir_cache();
    uint32_t h = handle_new(H_FILE);
    handles()[h].fd = fd;
    handles()[h].path = host;
    if (deferred) {
        handles()[h].write_pending = true;
        handles()[h].write_flags = wanted;
        handles()[h].guest_name = name;
    }
    set_last_error(ERROR_SUCCESS_);
    LOGV("CreateFileA(%s) -> %s handle %08x%s", name.c_str(), host.c_str(), h,
         deferred ? " (write tier at the first write)" : "");
    set_eax(c, h);
}

void get_file_attributes_named(X86 *c, const std::string &name) {
    std::string host = win32_host_path(name);
    OsStat st{};
    if (host.empty() || os_stat(host.c_str(), &st) != 0) {
        if (recomp_env("TRACE_FILES"))
            LOGW("file: attrs \"%s\" -> NOT FOUND (host \"%s\")", name.c_str(), host.c_str());
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0xffffffffu);
        return;
    }
    if (recomp_env("TRACE_FILES"))
        LOGW("file: attrs \"%s\" -> ok size=%lld", name.c_str(), (long long)st.size);
    set_eax(c, attrs_for(st));
}

void set_file_attributes_named(X86 *c, const std::string &name) {
    (void)name; // Attribute changes are advisory, matching the ANSI shim.
    set_eax(c, 1);
}

void create_directory_named(X86 *c, const std::string &name) {
    if (!win32_host_path(name).empty()) {
        set_last_error(ERROR_ALREADY_EXISTS_);
        set_eax(c, 0);
        return;
    }
    std::string host = win32_host_path(name, true);
    if (host.empty()) {
        set_last_error(ERROR_PATH_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    int rc = os_mkdir(host.c_str());
    win32_invalidate_dir_cache();
    set_eax(c, rc == 0 ? 1 : 0);
}

void remove_directory_named(X86 *c, const std::string &name) {
    std::string host = win32_host_path_op(name, WIN32_FILE_DELETE);
    if (host.empty()) {
        set_last_error(ERROR_PATH_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    int rc = os_rmdir(host.c_str());
    win32_invalidate_dir_cache();
    set_eax(c, rc == 0 ? 1 : 0);
}

void delete_file_named(X86 *c, const std::string &name) {
    std::string host = win32_host_path_op(name, WIN32_FILE_DELETE);
    if (host.empty()) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    int rc = os_unlink(host.c_str());
    win32_invalidate_dir_cache();
    set_eax(c, rc == 0 ? 1 : 0);
}

void copy_file_named(X86 *c, const std::string &source, const std::string &dest) {
    std::string from = win32_host_path_op(source, WIN32_FILE_READ);
    std::string to = win32_host_path_op(dest, WIN32_FILE_RENAME_DST);
    bool fail_if_exists = arg(c, 2) != 0;
    if (from.empty() || to.empty()) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    OsStat st{};
    if (fail_if_exists && os_stat(to.c_str(), &st) == 0) {
        set_last_error(ERROR_ALREADY_EXISTS_);
        set_eax(c, 0);
        return;
    }
    FILE *in = fopen(from.c_str(), "rb");
    if (!in) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    FILE *out = fopen(to.c_str(), "wb");
    if (!out) {
        fclose(in);
        set_last_error(ERROR_ACCESS_DENIED_);
        set_eax(c, 0);
        return;
    }
    char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, in)) > 0)
        fwrite(buf, 1, n, out);
    fclose(in);
    fclose(out);
    win32_invalidate_dir_cache();
    set_eax(c, 1);
}

void find_first_named(X86 *c, const std::string &pattern, bool wide) {
    uint32_t data = arg(c, 1);

    // Split the pattern into a directory and a leaf mask.
    std::string dirpart, leaf = pattern;
    size_t slash = pattern.find_last_of("\\/");
    if (slash != std::string::npos) {
        dirpart = pattern.substr(0, slash);
        leaf = pattern.substr(slash + 1);
    }
    uint32_t h = handle_new(H_FIND);
    HObj &o = handles()[h];

    if (g_file_list) {
        // The lister is given the SAME normalised relative directory a file
        // open would produce, so a listing and an open agree about what path
        // they are talking about.
        // The same serializer the resolver uses, so the root is "" on both
        // sides rather than "." on one of them.
        std::string rel = normalised_relative(dirpart);
        struct Ctx {
            HObj *o;
            const char *leaf;
        } ctx{&o, leaf.c_str()};
        g_file_list(
            rel.c_str(),
            [](void *p, const char *nm, const char *host) {
                Ctx *cx = (Ctx *)p;
                if (!wildcard_match(cx->leaf, nm))
                    return;
                cx->o->matches.push_back(nm);
                cx->o->match_paths.push_back(host);
            },
            &ctx);
    } else {
        std::string host_dir = dirpart.empty() ? win32_host_path_op(".", WIN32_FILE_LIST)
                                               : win32_host_path_op(dirpart, WIN32_FILE_LIST);
        if (host_dir.empty()) {
            handle_erase(h);
            set_last_error(ERROR_PATH_NOT_FOUND_);
            set_eax(c, INVALID_HANDLE_VALUE_);
            return;
        }
        o.find_dir = host_dir;
        for (const auto &kv : listing(host_dir))
            if (wildcard_match(leaf.c_str(), kv.second.c_str())) {
                o.matches.push_back(kv.second);
                o.match_paths.push_back(host_dir + "/" + kv.second);
            }
    }
    if (o.matches.empty()) {
        handle_erase(h);
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, INVALID_HANDLE_VALUE_);
        return;
    }
    fill_find_data(data, o.match_paths[0], o.matches[0], wide);
    o.find_pos = 1;
    set_eax(c, h);
}

void find_next(X86 *c, bool wide) {
    HObj *o = handle_get(arg(c, 0), H_FIND);
    if (!o || o->find_pos >= o->matches.size()) {
        set_last_error(ERROR_NO_MORE_FILES_);
        set_eax(c, 0);
        return;
    }
    fill_find_data(arg(c, 1), o->match_paths[o->find_pos], o->matches[o->find_pos], wide);
    ++o->find_pos;
    set_eax(c, 1);
}

std::string full_path_named(const std::string &name) {
    std::string full;
    if (name.size() >= 2 && name[1] == ':')
        full = name;
    else if (!name.empty() && (name[0] == '\\' || name[0] == '/'))
        full = "C:" + name;
    else
        full = g_cur_dir + "\\" + name;
    return full;
}

void volume_information_named(X86 *c, const std::string &root, bool wide) {
    (void)root;
    auto put = [wide](uint32_t p, const char *s, uint32_t n) {
        return wide ? gm_put_wstr(p, s, n) : gm_put_str(p, s, n);
    };
    // No CD check is modelled: report a fixed volume with no label. If the
    // game turns out to key off the volume name this must come from the trace.
    uint32_t namebuf = arg(c, 1), namelen = arg(c, 2), pserial = arg(c, 3);
    uint32_t pmaxcomp = arg(c, 4), pflags = arg(c, 5);
    uint32_t fsbuf = arg(c, 6), fslen = arg(c, 7);
    if (namebuf && namelen)
        put(namebuf, "", namelen);
    if (pserial)
        wr32(pserial, 0x1a2b3c4d);
    if (pmaxcomp)
        wr32(pmaxcomp, 255);
    if (pflags)
        wr32(pflags, 0);
    if (fsbuf && fslen)
        put(fsbuf, "FAT32", fslen);
    log_once("GetVolumeInformationA",
             "GetVolumeInformationA: reporting an unlabelled FAT32 volume");
    set_eax(c, 1);
}

// The one drive the runtime presents: 4 GB free of 8 GB, in 512-byte sectors,
// 8 per cluster. A game checks this before writing a save.
void disk_free_space(X86 *c) {
    uint32_t spc = arg(c, 1), bps = arg(c, 2), fr = arg(c, 3), tot = arg(c, 4);
    if (spc)
        wr32(spc, 8);
    if (bps)
        wr32(bps, 512);
    if (fr)
        wr32(fr, 0x00100000);
    if (tot)
        wr32(tot, 0x00200000);
    set_eax(c, 1);
}

void drive_type_named(X86 *c, const std::string &root) {
    (void)root;
    set_eax(c, 3); // DRIVE_FIXED
}

void logical_drive_strings(X86 *c, bool wide) {
    static const char drives[] = "C:\\\0";
    uint32_t len = arg(c, 0), buf = arg(c, 1);
    uint32_t need = sizeof drives; // includes both NULs
    if (!buf || len < need) {
        set_eax(c, need);
        return;
    }
    if (wide) {
        gm_put_wstr(buf, "C:\\", len);
        wr16(buf + 8, 0);
    } else {
        memcpy(g_mem + buf, drives, need);
    }
    set_eax(c, need - 1);
}

void get_file_attributes_ex_named(X86 *c, const std::string &name) {
    uint32_t out = arg(c, 2);
    if (arg(c, 1) != 0 || !out || !gm_valid(out, 36)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    std::string host = win32_host_path(name);
    OsStat st{};
    if (host.empty() || os_stat(host.c_str(), &st) != 0) {
        set_last_error(ERROR_FILE_NOT_FOUND_);
        set_eax(c, 0);
        return;
    }
    wr32(out, attrs_for(st));
    put_filetime(out + 4, st.ctime);
    put_filetime(out + 12, st.atime);
    put_filetime(out + 20, st.mtime);
    wr32(out + 28, (uint32_t)(st.size >> 32));
    wr32(out + 32, (uint32_t)st.size);
    set_eax(c, 1);
}

void create_event_named(X86 *c, const std::string &name) {
    if (reuse_named_object(c, name, H_EVENT))
        return;
    uint32_t h = handle_new(H_EVENT);
    handles()[h].manual_reset = arg(c, 1) != 0;
    handles()[h].signalled = arg(c, 2) != 0;
    handles()[h].object_name = name;
    set_last_error(ERROR_SUCCESS_);
    set_eax(c, h);
}

void create_mutex_named(X86 *c, const std::string &name) {
    if (reuse_named_object(c, name, H_MUTEX))
        return;
    uint32_t h = handle_new(H_MUTEX);
    if (arg(c, 1)) { // bInitialOwner
        handles()[h].owner_tid = cur_thread_id();
        handles()[h].owner_recursion = 1;
    }
    handles()[h].object_name = name;
    set_last_error(ERROR_SUCCESS_);
    set_eax(c, h);
}

void create_mapping_named(X86 *c, const std::string &name) {
    if (reuse_named_object(c, name, H_MAPPING))
        return;
    HObj *f = handle_get(arg(c, 0), H_FILE);
    uint32_t size = arg(c, 4);
    if (!f) {
        set_last_error(ERROR_INVALID_HANDLE_);
        set_eax(c, 0);
        return;
    }
    OsStat st{};
    os_fd_stat(f->fd, &st);
    if (!size)
        size = (uint32_t)st.size;
    uint32_t h = handle_new(H_MAPPING);
    handles()[h].fd = os_fd_dup(f->fd);
    handles()[h].map_size = size;
    handles()[h].path = f->path;
    handles()[h].object_name = name;
    set_last_error(ERROR_SUCCESS_);
    set_eax(c, h);
}

void open_mutex_named(X86 *c, const std::string &name) {
    if (reuse_named_object(c, name, H_MUTEX)) {
        if (c->r[R_EAX])
            set_last_error(ERROR_SUCCESS_);
        return;
    }
    set_last_error(ERROR_FILE_NOT_FOUND_);
    set_eax(c, 0);
}

void get_command_line(X86 *c) {
    if (!g_cmdline_addr) {
        std::string line = RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE;
        if (const char *extra = recomp_env("GUEST_ARGS"); extra && *extra)
            line += std::string(" ") + extra;
        g_cmdline_addr = guest_strdup(line.c_str());
    }
    set_eax(c, g_cmdline_addr);
}

void startup_info(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p) {
        memset(g_mem + p, 0, 68);
        wr32(p, 68);
    }
    set_eax(c, 0);
}

void wait_multiple_objects(X86 *c) {
    uint32_t n = arg(c, 0), parr = arg(c, 1), wait_all = arg(c, 2), timeout = arg(c, 3);
    if (!n || n > MAXIMUM_WAIT_OBJECTS_ || !parr || !gm_valid(parr, 4 * n)) {
        set_last_error(87);
        set_eax(c, 0xffffffffu); // WAIT_FAILED
        return;
    }
    uint32_t handles_[MAXIMUM_WAIT_OBJECTS_];
    for (uint32_t i = 0; i < n; ++i)
        handles_[i] = rd32(parr + 4 * i);
    set_eax(c, sched_wait_objects(handles_, n, wait_all != 0, timeout));
}

// ---------------------------------------------------------------------------
// Process, priority and waitable-timer imports.
// ---------------------------------------------------------------------------
uint64_t system_filetime() {
    const auto since_epoch = std::chrono::system_clock::now().time_since_epoch();
    const int64_t us = std::chrono::duration_cast<std::chrono::microseconds>(since_epoch).count();
    return (uint64_t)(us * 10) + 11644473600ull * 10000000ull;
}

void k_CancelWaitableTimer(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_EVENT);
    if (o && o->timer)
        o->timer_armed = false;
    set_eax(c, o ? 1 : 0);
}

// A snapshot the guest cannot walk: Process32First reports none. A game that
// looks for another copy of itself concludes it is the only one.
void k_CreateToolhelp32Snapshot(X86 *c) {
    set_last_error(ERROR_ACCESS_DENIED_);
    set_eax(c, 0xffffffffu); // INVALID_HANDLE_VALUE
}

void k_CreateWaitableTimerA(X86 *c) {
    uint32_t h = handle_new(H_EVENT);
    HObj &o = handles()[h];
    o.manual_reset = arg(c, 1) != 0; // a notification timer stays signalled
    o.signalled = false;
    o.timer = true;
    set_eax(c, h);
}

void k_DuplicateHandle(X86 *c) {
    // One process, so a duplicate is the same handle. Closing either is
    // harmless: handle_close on an already closed handle reports failure.
    uint32_t source = arg(c, 1), target = arg(c, 3);
    if (target)
        wr32(target, source);
    set_eax(c, 1);
}

void k_GetPriorityClass(X86 *c) {
    set_eax(c, 0x00000020); // NORMAL_PRIORITY_CLASS
}

void k_GetProcessAffinityMask(X86 *c) {
    uint32_t proc = arg(c, 1), sys = arg(c, 2);
    if (proc)
        wr32(proc, 1);
    if (sys)
        wr32(sys, 1);
    set_eax(c, 1); // one logical processor: the guest is single threaded here
}

void k_GetSystemTimeAsFileTime(X86 *c) {
    uint64_t ft = system_filetime();
    uint32_t out = arg(c, 0);
    if (out && gm_valid(out, 8)) {
        wr32(out, (uint32_t)ft);
        wr32(out + 4, (uint32_t)(ft >> 32));
    }
    set_eax(c, 0);
}

// The 64-bit form of the same machine. Every field is a ULONGLONG, so the
// structure is 64 bytes and each value is written as a pair of dwords.
void k_GlobalMemoryStatusEx(X86 *c) {
    uint32_t p = arg(c, 0);
    if (!p) {
        set_last_error(87 /* ERROR_INVALID_PARAMETER */);
        set_eax(c, 0);
        return;
    }
    const uint64_t mb = 1024ull * 1024ull;
    auto put64 = [&](uint32_t off, uint64_t v) {
        wr32(p + off, (uint32_t)v);
        wr32(p + off + 4, (uint32_t)(v >> 32));
    };
    wr32(p + 0, 64);         // dwLength
    wr32(p + 4, 25);         // dwMemoryLoad, percent
    put64(8, 512ull * mb);   // ullTotalPhys
    put64(16, 384ull * mb);  // ullAvailPhys
    put64(24, 1024ull * mb); // ullTotalPageFile
    put64(32, 768ull * mb);  // ullAvailPageFile
    put64(40, 2047ull * mb); // ullTotalVirtual: a 32-bit process's 2 GB
    put64(48, 1900ull * mb); // ullAvailVirtual
    put64(56, 0);            // ullAvailExtendedVirtual is always zero
    set_eax(c, 1);
}

void k_Process32First(X86 *c) {
    set_last_error(ERROR_NO_MORE_FILES_);
    set_eax(c, 0);
}

void k_Process32Next(X86 *c) {
    set_last_error(ERROR_NO_MORE_FILES_);
    set_eax(c, 0);
}

void k_SetPriorityClass(X86 *c) {
    set_eax(c, 1); // accepted and ignored: the scheduler is cooperative
}

void k_SetProcessAffinityMask(X86 *c) {
    set_eax(c, 1);
}

void k_SetThreadAffinityMask(X86 *c) {
    set_eax(c, 1); // the previous mask
}

// (hTimer, pDueTime, lPeriod, pfnCompletionRoutine, lpArgToCompletionRoutine,
// fResume). A negative due time is relative, in 100 ns; a positive one is an
// absolute UTC FILETIME. Completion routines need an alertable wait, which
// nothing here performs, so they are not called.
void k_SetWaitableTimer(X86 *c) {
    HObj *o = handle_get(arg(c, 0), H_EVENT);
    uint32_t due = arg(c, 1);
    int32_t period = (int32_t)arg(c, 2);
    if (!o || !o->timer || !due || !gm_valid(due, 8) || period < 0) {
        set_last_error(87 /* ERROR_INVALID_PARAMETER */);
        set_eax(c, 0);
        return;
    }
    int64_t when = (int64_t)((uint64_t)rd32(due) | ((uint64_t)rd32(due + 4) << 32));
    double from_now =
        when < 0 ? (double)(-when) * 1e-7 : ((double)when - (double)system_filetime()) * 1e-7;
    if (from_now < 0.0)
        from_now = 0.0;
    o->signalled = false;
    o->timer_armed = true;
    o->timer_due = sched_now() + from_now;
    o->timer_period = (double)period / 1000.0;
    sched_wake_all(); // a sleeping scheduler recomputes its deadline
    set_eax(c, 1);
}

void k_SleepEx(X86 *c) {
    // No APCs are ever queued, so an alertable wait is a plain one and
    // nothing was delivered.
    k_Sleep(c);
    set_eax(c, 0);
}

const ImportShim g_kernel32_shims[] = {
    // Serial-port ABI only; these unavailable-device calls still log and return FALSE.
    // Counts follow the Win32 stdcall declarations, including OVERLAPPED pointers.
    {"KERNEL32.dll", "GetCommConfig", 3, nullptr},
    {"KERNEL32.dll", "GetCommState", 2, nullptr},
    {"KERNEL32.dll", "PurgeComm", 2, nullptr},
    {"KERNEL32.dll", "SetCommConfig", 3, nullptr},
    {"KERNEL32.dll", "SetCommMask", 2, nullptr},
    {"KERNEL32.dll", "SetCommState", 2, nullptr},
    {"KERNEL32.dll", "SetCommTimeouts", 2, nullptr},
    {"KERNEL32.dll", "SetupComm", 3, nullptr},
    {"KERNEL32.dll", "WaitCommEvent", 3, nullptr},
    // memory
    {"KERNEL32.dll", "HeapCreate", 3, k_HeapCreate},
    {"KERNEL32.dll", "HeapDestroy", 1, k_HeapDestroy},
    {"KERNEL32.dll", "HeapAlloc", 3, k_HeapAlloc},
    {"KERNEL32.dll", "HeapReAlloc", 4, k_HeapReAlloc},
    {"KERNEL32.dll", "HeapFree", 3, k_HeapFree},
    {"KERNEL32.dll", "HeapSize", 3, k_HeapSize},
    {"KERNEL32.dll", "GlobalAlloc", 2, k_GlobalAlloc},
    {"KERNEL32.dll", "GlobalReAlloc", 3, k_GlobalReAlloc},
    {"KERNEL32.dll", "GlobalFree", 1, k_GlobalFree},
    {"KERNEL32.dll", "GlobalLock", 1, k_GlobalLock},
    {"KERNEL32.dll", "GlobalUnlock", 1, k_GlobalUnlock},
    {"KERNEL32.dll", "LocalAlloc", 2, k_LocalAlloc},
    {"KERNEL32.dll", "LocalFree", 1, k_LocalFree},
    {"KERNEL32.dll", "VirtualAlloc", 4, k_VirtualAlloc},
    {"KERNEL32.dll", "VirtualFree", 3, k_VirtualFree},
    {"KERNEL32.dll", "CreateFileMappingA", 6, k_CreateFileMappingA},
    {"KERNEL32.dll", "MapViewOfFile", 5, k_MapViewOfFile},
    {"KERNEL32.dll", "UnmapViewOfFile", 1, k_UnmapViewOfFile},
    // files
    {"KERNEL32.dll", "CreateFileA", 7, k_CreateFileA},
    {"KERNEL32.dll", "ReadFile", 5, k_ReadFile},
    {"KERNEL32.dll", "WriteFile", 5, k_WriteFile},
    {"KERNEL32.dll", "SetFilePointer", 4, k_SetFilePointer},
    {"KERNEL32.dll", "GetFileSize", 2, k_GetFileSize},
    {"KERNEL32.dll", "CloseHandle", 1, k_CloseHandle},
    {"KERNEL32.dll", "FlushFileBuffers", 1, k_FlushFileBuffers},
    {"KERNEL32.dll", "SetEndOfFile", 1, k_SetEndOfFile},
    {"KERNEL32.dll", "GetFileType", 1, k_GetFileType},
    {"KERNEL32.dll", "GetFileAttributesA", 1, k_GetFileAttributesA},
    {"KERNEL32.dll", "SetFileAttributesA", 2, k_SetFileAttributesA},
    {"KERNEL32.dll", "CreateDirectoryA", 2, k_CreateDirectoryA},
    {"KERNEL32.dll", "RemoveDirectoryA", 1, k_RemoveDirectoryA},
    {"KERNEL32.dll", "DeleteFileA", 1, k_DeleteFileA},
    {"KERNEL32.dll", "MoveFileA", 2, k_MoveFileA},
    {"KERNEL32.dll", "CopyFileA", 3, k_CopyFileA},
    {"KERNEL32.dll", "FindFirstFileA", 2, k_FindFirstFileA},
    {"KERNEL32.dll", "FindNextFileA", 2, k_FindNextFileA},
    {"KERNEL32.dll", "FindClose", 1, k_FindClose},
    {"KERNEL32.dll", "GetFullPathNameA", 4, k_GetFullPathNameA},
    {"KERNEL32.dll", "GetCurrentDirectoryA", 2, k_GetCurrentDirectoryA},
    {"KERNEL32.dll", "SetCurrentDirectoryA", 1, k_SetCurrentDirectoryA},
    {"KERNEL32.dll", "GetVolumeInformationA", 8, k_GetVolumeInformationA},
    {"KERNEL32.dll", "GetDiskFreeSpaceA", 5, k_GetDiskFreeSpaceA},
    {"KERNEL32.dll", "GetSystemDirectoryA", 2, k_GetSystemDirectoryA},
    {"KERNEL32.dll", "GetLogicalDriveStringsA", 2, k_GetLogicalDriveStringsA},
    {"KERNEL32.dll", "GetDriveTypeA", 1, k_GetDriveTypeA},
    // modules and process state
    {"KERNEL32.dll", "GetModuleFileNameA", 3, k_GetModuleFileNameA},
    {"KERNEL32.dll", "GetModuleFileNameW", 3, k_GetModuleFileNameW},
    {"KERNEL32.dll", "GetModuleHandleA", 1, k_GetModuleHandleA},
    {"KERNEL32.dll", "GetModuleHandleW", 1, k_GetModuleHandleW},
    {"KERNEL32.dll", "LoadLibraryA", 1, k_LoadLibraryA},
    {"KERNEL32.dll", "LoadLibraryW", 1, k_LoadLibraryW},
    {"KERNEL32.dll", "LoadLibraryExW", 3, k_LoadLibraryExW},
    {"KERNEL32.dll", "FreeLibrary", 1, k_FreeLibrary},
    {"KERNEL32.dll", "GetProcAddress", 2, k_GetProcAddress},
    {"KERNEL32.dll", "GetCommandLineA", 0, k_GetCommandLineA},
    {"KERNEL32.dll", "GetEnvironmentStrings", 0, k_GetEnvironmentStrings},
    {"KERNEL32.dll", "GetEnvironmentStringsA", 0, k_GetEnvironmentStrings},
    {"KERNEL32.dll", "GetEnvironmentStringsW", 0, k_GetEnvironmentStringsW},
    {"KERNEL32.dll", "FreeEnvironmentStringsA", 1, k_FreeEnvironmentStrings},
    {"KERNEL32.dll", "FreeEnvironmentStringsW", 1, k_FreeEnvironmentStrings},
    {"KERNEL32.dll", "SetEnvironmentVariableA", 2, k_SetEnvironmentVariableA},
    {"KERNEL32.dll", "GetStartupInfoA", 1, k_GetStartupInfoA},
    {"KERNEL32.dll", "GetSystemInfo", 1, k_GetSystemInfo},
    // The guest OS is 32-bit x86, so native and process system info agree.
    {"KERNEL32.dll", "GetNativeSystemInfo", 1, k_GetSystemInfo},
    {"KERNEL32.dll", "GetVersion", 0, k_GetVersion},
    // Not imported by D3DPopTB.exe; registered because the CRT prerequisites in
    // the plan name them and GetProcAddress must be able to find them.
    {"KERNEL32.dll", "GetVersionExA", 1, k_GetVersionExA},
    {"KERNEL32.dll", "GetVersionExW", 1, k_GetVersionExW},
    {"KERNEL32.dll", "GetProcessHeap", 0, k_GetProcessHeap},
    {"KERNEL32.dll", "GetStdHandle", 1, k_GetStdHandle},
    {"KERNEL32.dll", "SetStdHandle", 2, k_SetStdHandle},
    {"KERNEL32.dll", "SetHandleCount", 1, k_SetHandleCount},
    {"KERNEL32.dll", "OutputDebugStringA", 1, k_OutputDebugStringA},
    {"KERNEL32.dll", "GetLastError", 0, k_GetLastError},
    {"KERNEL32.dll", "SetLastError", 1, k_SetLastError},
    {"KERNEL32.dll", "IsBadReadPtr", 2, k_IsBadReadPtr},
    {"KERNEL32.dll", "IsBadWritePtr", 2, k_IsBadWritePtr},
    {"KERNEL32.dll", "IsBadCodePtr", 1, k_IsBadCodePtr},
    {"KERNEL32.dll", "IsProcessorFeaturePresent", 1, k_IsProcessorFeaturePresent},
    {"KERNEL32.dll", "lstrcmpiA", 2, k_lstrcmpiA},
    // time
    {"KERNEL32.dll", "GetTickCount", 0, k_GetTickCount},
    {"KERNEL32.dll", "QueryPerformanceCounter", 1, k_QueryPerformanceCounter},
    {"KERNEL32.dll", "QueryPerformanceFrequency", 1, k_QueryPerformanceFrequency},
    {"KERNEL32.dll", "Sleep", 1, k_Sleep},
    {"KERNEL32.dll", "GetLocalTime", 1, k_GetLocalTime},
    {"KERNEL32.dll", "GetSystemTime", 1, k_GetSystemTime},
    // Restored with the merge: these were on the branch this game was ported
    // on, and main had never needed them. An import with no entry here has an
    // unknown argument count, so imports_dispatch pops only the return address
    // and every call leaks its arguments - a polling thread walks the guest
    // stack pointer clean out of its stack.
    {"KERNEL32.dll", "GetSystemTimeAsFileTime", 1, k_GetSystemTimeAsFileTime},
    {"KERNEL32.dll", "SleepEx", 2, k_SleepEx},
    {"KERNEL32.dll", "CreateWaitableTimerA", 3, k_CreateWaitableTimerA},
    {"KERNEL32.dll", "SetWaitableTimer", 6, k_SetWaitableTimer},
    {"KERNEL32.dll", "CancelWaitableTimer", 1, k_CancelWaitableTimer},
    {"KERNEL32.dll", "DuplicateHandle", 7, k_DuplicateHandle},
    {"KERNEL32.dll", "GlobalMemoryStatusEx", 1, k_GlobalMemoryStatusEx},
    {"KERNEL32.dll", "CreateToolhelp32Snapshot", 2, k_CreateToolhelp32Snapshot},
    {"KERNEL32.dll", "Process32First", 2, k_Process32First},
    {"KERNEL32.dll", "Process32Next", 2, k_Process32Next},
    {"KERNEL32.dll", "GetPriorityClass", 1, k_GetPriorityClass},
    {"KERNEL32.dll", "SetPriorityClass", 2, k_SetPriorityClass},
    {"KERNEL32.dll", "GetProcessAffinityMask", 3, k_GetProcessAffinityMask},
    {"KERNEL32.dll", "SetProcessAffinityMask", 2, k_SetProcessAffinityMask},
    {"KERNEL32.dll", "SetThreadAffinityMask", 2, k_SetThreadAffinityMask},
    {"KERNEL32.dll", "CreateProcessA", 10, nullptr},
    {"KERNEL32.dll", "DebugBreak", 0, nullptr},
    {"KERNEL32.dll", "FatalAppExitA", 2, nullptr},
    {"KERNEL32.dll", "GetDateFormatA", 6, nullptr},
    {"KERNEL32.dll", "GetTimeFormatA", 6, nullptr},
    {"KERNEL32.dll", "GetDiskFreeSpaceExA", 4, nullptr},
    {"KERNEL32.dll", "GetLongPathNameA", 3, nullptr},
    {"KERNEL32.dll", "GetOverlappedResult", 4, nullptr},
    {"KERNEL32.dll", "HeapValidate", 3, nullptr},
    {"KERNEL32.dll", "QueueUserAPC", 3, nullptr},
    {"KERNEL32.dll", "SetConsoleCtrlHandler", 2, nullptr},
    {"KERNEL32.dll", "TerminateThread", 2, nullptr},
    // UG1 (Speed.exe) imports the console input API and FormatMessageA: no console here.
    {"KERNEL32.dll", "ReadConsoleInputA", 4, nullptr},
    {"KERNEL32.dll", "PeekConsoleInputA", 4, nullptr},
    {"KERNEL32.dll", "GetNumberOfConsoleInputEvents", 2, nullptr},
    {"KERNEL32.dll", "GetConsoleMode", 2, nullptr},
    {"KERNEL32.dll", "SetConsoleMode", 2, nullptr},
    {"KERNEL32.dll", "FormatMessageA", 7, nullptr},
    {"KERNEL32.dll", "GetTimeZoneInformation", 1, k_GetTimeZoneInformation},
    // TLS / interlocked / critical sections
    {"KERNEL32.dll", "TlsAlloc", 0, k_TlsAlloc},
    {"KERNEL32.dll", "TlsGetValue", 1, k_TlsGetValue},
    {"KERNEL32.dll", "TlsSetValue", 2, k_TlsSetValue},
    {"KERNEL32.dll", "TlsFree", 1, k_TlsFree},
    {"KERNEL32.dll", "InterlockedIncrement", 1, k_InterlockedIncrement},
    {"KERNEL32.dll", "InterlockedDecrement", 1, k_InterlockedDecrement},
    {"KERNEL32.dll", "InterlockedExchange", 2, k_InterlockedExchange},
    {"KERNEL32.dll", "InterlockedCompareExchange", 3, k_InterlockedCompareExchange},
    {"KERNEL32.dll", "InitializeCriticalSection", 1, k_InitializeCriticalSection},
    {"KERNEL32.dll", "EnterCriticalSection", 1, k_EnterCriticalSection},
    {"KERNEL32.dll", "LeaveCriticalSection", 1, k_LeaveCriticalSection},
    {"KERNEL32.dll", "DeleteCriticalSection", 1, k_DeleteCriticalSection},
    // synchronisation objects
    {"KERNEL32.dll", "CreateSemaphoreA", 4, k_CreateSemaphoreA},
    {"KERNEL32.dll", "ReleaseSemaphore", 3, k_ReleaseSemaphore},
    {"KERNEL32.dll", "CreateEventA", 4, k_CreateEventA},
    {"KERNEL32.dll", "ResetEvent", 1, k_ResetEvent},
    {"KERNEL32.dll", "CreateMutexA", 3, k_CreateMutexA},
    {"KERNEL32.dll", "WaitForSingleObject", 2, k_WaitForSingleObject},
    {"KERNEL32.dll", "WaitForMultipleObjects", 4, k_WaitForMultipleObjects},
    // threads and process exit
    {"KERNEL32.dll", "CreateThread", 6, k_CreateThread},
    {"KERNEL32.dll", "ExitThread", 1, k_ExitThread},
    {"KERNEL32.dll", "GetExitCodeThread", 2, k_GetExitCodeThread},
    {"KERNEL32.dll", "ResumeThread", 1, k_ResumeThread},
    {"KERNEL32.dll", "SuspendThread", 1, k_SuspendThread},
    {"KERNEL32.dll", "ReleaseMutex", 1, k_ReleaseMutex},
    {"KERNEL32.dll", "SetEvent", 1, k_SetEvent},
    {"KERNEL32.dll", "TryEnterCriticalSection", 1, k_TryEnterCriticalSection},
    {"KERNEL32.dll", "GetCurrentThread", 0, k_GetCurrentThread},
    {"KERNEL32.dll", "GetCurrentThreadId", 0, k_GetCurrentThreadId},
    {"KERNEL32.dll", "GetCurrentProcess", 0, k_GetCurrentProcess},
    {"KERNEL32.dll", "GetThreadPriority", 1, k_GetThreadPriority},
    {"KERNEL32.dll", "SetThreadPriority", 2, k_SetThreadPriority},
    {"KERNEL32.dll", "ExitProcess", 1, k_ExitProcess},
    {"KERNEL32.dll", "TerminateProcess", 2, k_TerminateProcess},
    // exceptions
    {"KERNEL32.dll", "SetUnhandledExceptionFilter", 1, k_SetUnhandledExceptionFilter},
    {"KERNEL32.dll", "UnhandledExceptionFilter", 1, k_UnhandledExceptionFilter},
    {"KERNEL32.dll", "RaiseException", 4, k_RaiseException},
    {"KERNEL32.dll", "RtlUnwind", 4, k_RtlUnwind},
    // strings, code pages, locale
    {"KERNEL32.dll", "lstrlenA", 1, k_lstrlenA},
    {"KERNEL32.dll", "lstrcpyA", 2, k_lstrcpyA},
    {"KERNEL32.dll", "lstrcatA", 2, k_lstrcatA},
    {"KERNEL32.dll", "IsDBCSLeadByte", 1, k_IsDBCSLeadByte},
    {"KERNEL32.dll", "GetACP", 0, k_GetACP},
    {"KERNEL32.dll", "GetOEMCP", 0, k_GetOEMCP},
    {"KERNEL32.dll", "GetCPInfo", 2, k_GetCPInfo},
    {"KERNEL32.dll", "MultiByteToWideChar", 6, k_MultiByteToWideChar},
    {"KERNEL32.dll", "WideCharToMultiByte", 8, k_WideCharToMultiByte},
    {"KERNEL32.dll", "FlushInstructionCache", 3, k_FlushInstructionCache},
    // Named for their argument counts. An import the kit does not know is
    // called with its arguments left on the stack, which is corruption at a
    // distance; a logging-only entry with the right count is what a guest
    // survives, and the log says which of these it wanted.
    {"KERNEL32.dll", "SetFileTime", 4, nullptr},
    {"KERNEL32.dll", "SystemTimeToFileTime", 2, k_SystemTimeToFileTime},
    {"KERNEL32.dll", "SystemTimeToTzSpecificLocalTime", 3, nullptr},
    {"KERNEL32.dll", "TzSpecificLocalTimeToSystemTime", 3, nullptr},
    {"KERNEL32.dll", "MoveFileW", 2, nullptr},
    {"KERNEL32.dll", "OpenProcess", 3, nullptr},
    {"KERNEL32.dll", "ExpandEnvironmentStringsW", 3, nullptr},
    {"KERNEL32.dll", "GetCurrentDirectoryW", 2, nullptr},
    {"KERNEL32.dll", "GetEnvironmentVariableW", 4, nullptr},
    {"KERNEL32.dll", "VerLanguageNameW", 3, nullptr},
    {"KERNEL32.dll", "SearchPathW", 6, nullptr},
    {"KERNEL32.dll", "GetStringTypeA", 5, k_GetStringTypeA},
    // The Ex form takes the same five arguments in the same order.
    {"KERNEL32.dll", "GetStringTypeExA", 5, k_GetStringTypeA},
    {"KERNEL32.dll", "GetStringTypeW", 4, k_GetStringTypeW},
    {"KERNEL32.dll", "GetStringTypeExW", 5, k_GetStringTypeExW},
    {"KERNEL32.dll", "LCMapStringA", 6, k_LCMapStringA},
    {"KERNEL32.dll", "LCMapStringW", 6, k_LCMapStringW},
    {"KERNEL32.dll", "CompareStringA", 6, k_CompareStringA},
    {"KERNEL32.dll", "CompareStringW", 6, k_CompareStringW},
    {"KERNEL32.dll", "GetLocaleInfoA", 4, k_GetLocaleInfoA},
    {"KERNEL32.dll", "GetEnvironmentVariableA", 3, k_GetEnvironmentVariableA},
    {"KERNEL32.dll", "GetUserDefaultLCID", 0, k_GetUserDefaultLCID},
    {"KERNEL32.dll", "GetSystemDefaultLCID", 0, k_GetUserDefaultLCID},
    {"KERNEL32.dll", "IsValidCodePage", 1, k_IsValidCodePage},
    {"KERNEL32.dll", "IsValidLocale", 2, k_IsValidLocale},
    {"KERNEL32.dll", "EnumSystemLocalesA", 2, k_EnumSystemLocalesA},
    {"KERNEL32.dll", "GlobalMemoryStatus", 1, k_GlobalMemoryStatus},
    {"KERNEL32.dll", "SetErrorMode", 1, k_SetErrorMode},
    {"KERNEL32.dll", "GetLogicalDrives", 0, k_GetLogicalDrives},
    {"KERNEL32.dll", "GetLocaleInfoW", 4, k_GetLocaleInfoW},
};
const size_t g_kernel32_shim_count = sizeof(g_kernel32_shims) / sizeof(g_kernel32_shims[0]);
