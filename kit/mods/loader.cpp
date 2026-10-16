// loader.cpp - discovery, load order and the transactional lifecycle.
//
//  1. Discover core then user <root>/<name>/mod.toml, validate, compute load
//     order with core strictly first, refuse
//     conflicts, cycles, duplicate ids, unmet requires and game/api
//     mismatches; print loaded and rejected mods with reasons.
//  2. Load the Lua runtime as a core plugin, ordered before every user mod.
//  3. For each mod in load order, inside one transaction: push its overlay,
//     dlopen its plugin, validate its ABI record, call pop_mod_init, run its
//     [script]. Any failure rolls back every hook, event, input subscription,
//     overlay layer, setting, menu entry, provider registration and guest
//     allocation that mod made, and the mod is reported failed. Guest memory
//     it wrote through guest_read/write is NOT rolled back - a documented
//     limitation, because the API has no record of writes outside its own
//     tracked resources. Mods that require a failed mod are rejected without
//     running their own init.
//  4. Seal the overlay layer list; the entry point runs.
//  5. Shutdown, after guest threads stop: pop_mod_exit in reverse load order,
//     every tracked resource reclaimed, then the Lua runtime last.
#include "mods_internal.h"
#include "builtin_mods.h"
#include "../runtime/layout.h"
#include "controls_settings.h"
#include "options_menu.h"
#include "sprite_view.h"
#include "manifest_types.h"
#include "roots.h"
// sched_guest_threads_stopped: a plugin may not be unloaded while a guest
// thread could still be inside one of its hooks.
#include "../runtime/win32.h"
#include "../platform/os.h"

#include <algorithm>
#include <atomic>
#include <type_traits>
#include <deque>
#include <set>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

namespace {

struct ModContext {
    // Stable for the life of the process: plugins keep both the API pointer
    // and mod_id, and a later push_back must not move them, which is why the
    // container is a deque and never a vector.
    // `api` is what the plugin is given: every entry point in it is a guard
    // that checks this context's lifecycle and then forwards to `live`, which
    // holds the modules' own function pointers. A plugin that saved
    // api->hook_install into a variable before it was revoked therefore saved
    // the guard, not the module, and calling it still returns POP_E_STATE.
    // Rewriting the table on revocation cannot do that: a saved pointer does
    // not change when the table does.
    PopModApi api{};
    PopModApi live{};
    std::string id, name, version, dir, plugin, script, assets, reason;
    bool affects_simulation = false;
    bool loaded = false;
    // A context is LIVE while its mod is loading or loaded, and REVOKED once
    // it has been rolled back or shut down. A plugin keeps the API pointer it
    // was given for the life of the process, so the pointer stays valid and
    // every call through it starts returning POP_E_STATE instead.
    // Written by the loader or by shutdown, read by every guard - and a guard
    // runs on whichever guest thread is calling. A plain bool read across
    // threads is a data race, and the one it would lose is the one that
    // matters: a callback seeing a stale false after revocation.
    std::atomic<bool> revoked{false};
    // The typed reason a mod was rejected, POP_OK while it is fine. A sentence
    // is for a person; this is what a caller or a test can act on.
    PopModStatus status = POP_OK;
    uint32_t owner = 0;
    void *handle = nullptr;
    PopModStatus (*exit_fn)() = nullptr;
};

// Contexts are never destroyed before the process is.
//
// A plugin keeps the API pointer it was given for the life of the process and
// may call through it at any time - a captured callback, a timer, a pointer in
// its own state - and every one of those calls reads this context's `revoked`
// flag and its strings. Freeing the context would turn that read into a
// dereference of released memory, and an atomic flag does not help: the guard
// can observe "not revoked" and then be descheduled while the container it
// points into is cleared. So the storage outlives every possible call, and
// only what the loader REPORTS moves on. A deque, because a plugin holds
// &c.api and a later push_back must not move it.
std::deque<ModContext> &contexts() {
    static std::deque<ModContext> d;
    return d;
}

// Where the current generation starts. Everything before it has been retired:
// revoked, still addressable, and no longer part of what mods_record reports.
size_t &generation_base() {
    static size_t n = 0;
    return n;
}

// Owners are unique for the life of the process, not per generation. Two
// generations reusing owner 2 would make the first context found by owner the
// retired one, and a live mod would be answered POP_E_STATE for ever.
//
// Bounded, too. An owner id is the key every registry uses, and 0 and 1 are
// the runtime's and Lua's: a counter allowed to run off the end would hand a
// mod one of those, and that mod's rollback would take the runtime's own
// event hooks with it. Running out is refused instead.
uint32_t &next_owner() {
    static uint32_t n = MODS_OWNER_FIRST_MOD;
    return n;
}

bool owner_available() {
    return next_owner() >= MODS_OWNER_FIRST_MOD && next_owner() < 0xffffffffu;
}

// Guards in flight, so a caller that wants to take the registry apart can wait
// until none is. A guard reads its context's revoked flag and then forwards
// into the context's own state; retiring between those two is a read of
// storage nobody is holding. In production nothing retires while the process
// runs - mods_load_all happens once - so this exists for the test reset,
// which is the only thing that does.
std::atomic<int> &guards_in_flight() {
    static std::atomic<int> n{0};
    return n;
}

struct GuardScope {
    GuardScope() {
        guards_in_flight().fetch_add(1, std::memory_order_acq_rel);
    }
    ~GuardScope() {
        guards_in_flight().fetch_sub(1, std::memory_order_acq_rel);
    }
};

// Loading happens once. See mods_load_all.
bool g_loaded_once = false;

// Set once the teardown has actually run, so a second ask reports true without
// writing a second run record. Cleared by a fresh load, because that is a new
// run and its record is due again.
bool g_shutdown_done = false;

// The context a guard belongs to. Found by the api instance's own index,
// which is fixed when the context is built and never changes.
//
// R3 run 31 O2: the answer is remembered per thread. Contexts are only ever
// appended, never freed or moved (above), so the first context with an owner
// stays the first for ever and the scan's answer cannot change once found; a
// miss is never remembered, because the context may not have been built yet.
ModContext *context_of(const PopModApi *api) {
    if (!api)
        return nullptr;
    thread_local uint32_t last_owner = 0;
    thread_local ModContext *last = nullptr;
    if (last && last_owner == api->mod_index)
        return last;
    for (ModContext &c : contexts())
        if (c.owner == api->mod_index) {
            last_owner = c.owner;
            last = &c;
            return &c;
        }
    return nullptr;
}

std::string read_text(const std::string &path) {
    FILE *f = fopen(path.c_str(), "rb");
    if (!f)
        return "";
    std::string out;
    char buf[8192];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0)
        out.append(buf, n);
    fclose(f);
    return out;
}

PopModStatus api_log(const PopModApi *api, const char *text) {
    printf("[mod %s] %s\n", api->mod_id, text ? text : "");
    fflush(stdout);
    return POP_OK;
}

const char *api_mod_dir(const PopModApi *api) {
    for (ModContext &c : contexts())
        if (c.owner == api->mod_index)
            return c.dir.c_str();
    return "";
}

ModContext *context_of(const PopModApi *api);

// One guard for every entry point that returns a status. The member to
// forward to is a template parameter, so this is one definition rather than
// thirty-odd, and the compiler still checks each signature at the point of
// assignment.
// The member's own type, so the pointer can be taken out of the scope below
// and called after it has closed.
template <auto Member>
using ApiFn = std::remove_reference_t<decltype(std::declval<PopModApi &>().*Member)>;

// NOTHING WITH A DESTRUCTOR IS LIVE ACROSS THE FORWARD.
//
// call_original and call_next run guest code, and guest code longjmps
// (runtime/cpu.cpp restores the registers and longjmps out of the whole host
// frame). A longjmp does not run destructors: an RAII counter held across the
// forward would be leaked by the first unwind and, worse, jumping over a
// non-trivial destructor is undefined behaviour in its own right.
//
// So the counter covers the loader's own work - finding the context, reading
// the revocation flag, taking the function pointer - and closes before the
// call. The forwarding span itself is covered by the hook runtime's
// invocation depth, which the reset waits on as well, and which is maintained
// by a mechanism that expects to be unwound past.
template <auto Member, class... A> PopModStatus guarded(const PopModApi *api, A... args) {
    ApiFn<Member> fn = nullptr;
    {
        GuardScope in_flight;
        ModContext *c = context_of(api);
        if (!c || c->revoked.load(std::memory_order_acquire))
            return POP_E_STATE;
        fn = c->live.*Member;
    }
    if (!fn)
        return POP_E_STATE;
    return fn(api, args...);
}

const char *guarded_dir(const PopModApi *api) {
    const char *(*fn)(const PopModApi *) = nullptr;
    {
        GuardScope in_flight;
        ModContext *c = context_of(api);
        if (!c || c->revoked.load(std::memory_order_acquire))
            return "";
        fn = c->live.mod_dir;
    }
    return fn ? fn(api) : "";
}

uint32_t guarded_count(const PopModApi *api) {
    uint32_t (*fn)(const PopModApi *) = nullptr;
    {
        GuardScope in_flight;
        ModContext *c = context_of(api);
        if (!c || c->revoked.load(std::memory_order_acquire))
            return 0;
        fn = c->live.entity_count;
    }
    return fn ? fn(api) : 0;
}

uint32_t guarded_elements(const PopModApi *api, uint64_t *ids, uint32_t max) {
    ApiFn<&PopModApi::ui_elements> fn = nullptr;
    {
        GuardScope in_flight;
        ModContext *c = context_of(api);
        if (!c || c->revoked.load(std::memory_order_acquire))
            return 0;
        fn = c->live.ui_elements;
    }
    return fn ? fn(api, ids, max) : 0;
}
float guarded_aspect(const PopModApi *api) {
    ApiFn<&PopModApi::host_aspect> fn = nullptr;
    {
        GuardScope in_flight;
        ModContext *c = context_of(api);
        if (!c || c->revoked.load(std::memory_order_acquire))
            return 4.0f / 3.0f;
        fn = c->live.host_aspect;
    }
    return fn ? fn(api) : 4.0f / 3.0f;
}

// Called when a mod is rolled back or shut down. The plugin may still hold
// this exact PopModApi and may still call through it - a callback captured
// elsewhere, a timer, a retained pointer in its own state - so the instance
// stays valid and every function in it starts refusing. Zeroing the pointers
// instead would turn a retained call into a crash with no explanation.
void revoke_api(ModContext &c) {
    // The flag is the whole of it. Every entry point the plugin can reach is a
    // guard that reads this, including any pointer it saved earlier, so
    // nothing has to be rewritten and nothing can be missed.
    // Release, so a guard that sees this also sees everything the loader did
    // before revoking - the registrations already removed, above all.
    c.revoked.store(true, std::memory_order_release);
}

// Retires everything the loader is currently reporting, without freeing it.
// See the note on contexts(): the storage a plugin can still call into must
// outlive every possible call, so a generation ends by being revoked and left
// where it is. Only mods_record moves on.
void retire_contexts() {
    for (ModContext &c : contexts())
        revoke_api(c);
    generation_base() = contexts().size();
}

// Build each mod API as guarded wrappers around its live service table.
// Retained plugin function pointers must still reject calls after that mod is revoked.
void build_api(ModContext &c) {
    // The modules fill `live`; the plugin is handed `api`, whose every entry
    // point is a guard over the matching member of `live`.
    memset(&c.live, 0, sizeof c.live);
    c.live.version = POP_MOD_API_VERSION;
    c.live.size = (uint32_t)sizeof(PopModApi);
    c.live.mod_index = c.owner;
    c.live.mod_id = c.id.c_str();
    c.live.log = api_log;
    c.live.mod_dir = api_mod_dir;
    c.live.settings_get = [](const PopModApi *a, const char *k, int64_t *v) {
        return mods_settings_get(a->mod_index, k, v);
    };
    c.live.settings_set = [](const PopModApi *a, const char *k, int64_t v) {
        return mods_settings_set_derived(a->mod_index, k, v); // run 16: mod code's writes never make intent
    };
    mods_fill_hooks_api(&c.live);
    mods_fill_memory_api(&c.live);
    mods_fill_overlay_api(&c.live);
    mods_fill_events_api(&c.live);
    mods_fill_host_api(&c.live);

    memset(&c.api, 0, sizeof c.api);
    c.api.version = POP_MOD_API_VERSION;
    c.api.size = (uint32_t)sizeof(PopModApi);
    c.api.mod_index = c.owner;
    c.api.mod_id = c.id.c_str(); // stable: c is never moved
    c.api.log = guarded<&PopModApi::log>;
    c.api.settings_get = guarded<&PopModApi::settings_get>;
    c.api.settings_set = guarded<&PopModApi::settings_set>;
    c.api.hook_install = guarded<&PopModApi::hook_install>;
    c.api.hook_remove = guarded<&PopModApi::hook_remove>;
    c.api.call_original = guarded<&PopModApi::call_original>;
    c.api.call_next = guarded<&PopModApi::call_next>;
    c.api.hook_return = guarded<&PopModApi::hook_return>;
    c.api.symbol = guarded<&PopModApi::symbol>;
    c.api.symbols_matching = guarded<&PopModApi::symbols_matching>;
    c.api.guest_ptr = guarded<&PopModApi::guest_ptr>;
    c.api.guest_read_u8 = guarded<&PopModApi::guest_read_u8>;
    c.api.guest_read_u16 = guarded<&PopModApi::guest_read_u16>;
    c.api.guest_read_u32 = guarded<&PopModApi::guest_read_u32>;
    c.api.guest_write_u8 = guarded<&PopModApi::guest_write_u8>;
    c.api.guest_write_u16 = guarded<&PopModApi::guest_write_u16>;
    c.api.guest_write_u32 = guarded<&PopModApi::guest_write_u32>;
    c.api.guest_alloc = guarded<&PopModApi::guest_alloc>;
    c.api.guest_free = guarded<&PopModApi::guest_free>;
    c.api.entity_slot = guarded<&PopModApi::entity_slot>;
    c.api.entity = guarded<&PopModApi::entity>;
    c.api.tribe = guarded<&PopModApi::tribe>;
    c.api.on_frame = guarded<&PopModApi::on_frame>;
    c.api.on_turn = guarded<&PopModApi::on_turn>;
    c.api.on_level_load = guarded<&PopModApi::on_level_load>;
    c.api.on_level_end = guarded<&PopModApi::on_level_end>;
    c.api.on_key = guarded<&PopModApi::on_key>;
    c.api.on_mouse = guarded<&PopModApi::on_mouse>;
    c.api.register_menu_item = guarded<&PopModApi::register_menu_item>;
    c.api.register_setting = guarded<&PopModApi::register_setting>;
    c.api.texture_override_provider = guarded<&PopModApi::texture_override_provider>;
    c.api.texture_override_provider_ex = guarded<&PopModApi::texture_override_provider_ex>;
    c.api.overlay_push = guarded<&PopModApi::overlay_push>;
    c.api.open_settings_page = guarded<&PopModApi::open_settings_page>;
    c.api.mod_dir = guarded_dir;
    c.api.entity_count = guarded_count;
    c.api.set_anchor = guarded<&PopModApi::set_anchor>;
    c.api.clear_anchor = guarded<&PopModApi::clear_anchor>;
    c.api.ui_elements = guarded_elements;
    c.api.host_aspect = guarded_aspect;
    c.api.display_transition = guarded<&PopModApi::display_transition>;
    c.api.set_scene_domain = guarded<&PopModApi::set_scene_domain>;
    c.api.hook_install_at_callsite = guarded<&PopModApi::hook_install_at_callsite>;
    c.api.guest_call = guarded<&PopModApi::guest_call>;
    c.api.screen_size = guarded<&PopModApi::screen_size>;
    c.api.rumble_pulse = guarded<&PopModApi::rumble_pulse>;
    c.api.audio_output = guarded<&PopModApi::audio_output>;
    c.api.d3d9_texture_tag = guarded<&PopModApi::d3d9_texture_tag>;
    c.api.pointer_box = guarded<&PopModApi::pointer_box>;
    c.api.hook_install_ex = guarded<&PopModApi::hook_install_ex>;
}

// Owners whose registrations are gone but whose resources could not yet be
// reclaimed, because a captured callback of theirs might still be running.
// Reclaimed at shutdown, once quiescence holds.
std::vector<uint32_t> &deferred_reclaim() {
    static std::vector<uint32_t> v;
    return v;
}

// True when nothing of any mod can be executing: every guest thread has
// finished and no mod callback is on this thread's stack.
bool quiescent() {
    return sched_guest_threads_stopped() && mods_hook_depth() == 0;
}

// Everything one mod registered, undone in one call.
//
// Revoking and removing registrations is always safe: the registries are
// coordinated and a removed subscription simply stops being consulted.
// RECLAIMING is not: freeing guest memory or dropping Lua state while a
// captured callback of this mod could still be running is a use-after-free,
// so that half waits for quiescence and is deferred to shutdown otherwise.
void rollback(uint32_t owner) {
    mods_hooks_remove_all(owner);
    mods_events_remove_all(owner);
    // Input subscriptions are their own registry, not part of the event one:
    // on_key goes to mods_input_add_key, so mods_events_remove_all does not
    // touch it. Left behind, a failed mod's key handler goes on eating keys
    // for the rest of the process, which is the kind of trace a rolled-back
    // mod is supposed to leave none of.
    mods_input_remove_all(owner);
    mods_overlay_remove_all(owner);
    mods_host_services_remove_all(owner);
    mods_settings_txn_rollback();
    mods_settings_remove_all(owner);
    if (quiescent()) {
        mods_guest_free_all(owner);
        mods_lua_drop_mod(owner);
    } else {
        LOGW("mods: owner %u rolled back while something of it could still be "
             "running; its memory and script state are reclaimed at shutdown",
             owner);
        deferred_reclaim().push_back(owner);
    }
}

// ---- plugin handles: a loaded library, or an entry of recomp_builtin_mods ----

bool is_builtin(void *handle) {
    for (const RecompBuiltinMod *m = recomp_builtin_mods; m->stem; ++m)
        if (handle == (void *)m)
            return true;
    return false;
}

// The built-in plugin a manifest's plugin path names, by the file's stem.
const RecompBuiltinMod *builtin_for(const std::string &plugin_path) {
    size_t slash = plugin_path.find_last_of("/\\");
    std::string stem = plugin_path.substr(slash == std::string::npos ? 0 : slash + 1);
    size_t dot = stem.rfind('.');
    if (dot != std::string::npos)
        stem.resize(dot);
    for (const RecompBuiltinMod *m = recomp_builtin_mods; m->stem; ++m)
        if (stem == m->stem)
            return m;
    return nullptr;
}

void *plugin_sym(void *handle, const char *name) {
    if (!is_builtin(handle))
        return os_dlsym(handle, name);
    const RecompBuiltinMod *m = (const RecompBuiltinMod *)handle;
    if (!strcmp(name, "pop_mod_abi"))
        return (void *)m->abi;
    if (!strcmp(name, "pop_mod_init"))
        return (void *)m->init;
    if (!strcmp(name, "pop_mod_exit"))
        return (void *)m->exit;
    return nullptr;
}

// Returns POP_OK, or POP_E_ABI with `why` set. The status is typed and kept,
// because "abi" appearing somewhere in a sentence is not something a caller or
// a test can act on.
PopModStatus validate_abi(void *handle, std::string *why) {
    const PopModAbi *abi = (const PopModAbi *)plugin_sym(handle, "pop_mod_abi");
    if (!abi) {
        *why = "no pop_mod_abi record";
        return POP_E_ABI;
    }
    if (abi->abi_struct_size != sizeof(PopModAbi)) {
        *why = "pop_mod_abi is " + std::to_string(abi->abi_struct_size) +
               " bytes, not this host's " + std::to_string(sizeof(PopModAbi));
        return POP_E_ABI;
    }
    if (abi->api_version != POP_MOD_API_VERSION) {
        *why = "plugin abi api_version " + std::to_string(abi->api_version) +
               " is not this host's " + std::to_string(POP_MOD_API_VERSION);
        return POP_E_ABI;
    }
    // api_size must name a real PREFIX of the struct, not merely a number in
    // range: a plugin declaring 1 byte has no usable PopModApi at all, and the
    // smallest thing that is one ends after mod_id.
    const uint32_t API_MIN = (uint32_t)(offsetof(PopModApi, mod_id) + sizeof(const char *));
    // A prefix ENDS on a member boundary. Every member from mod_id onwards is
    // a pointer, so a size that is not pointer-aligned stops in the middle of
    // one, and the host would then serve half a field: API_MIN + 1 was passing
    // for exactly that reason.
    if (abi->api_size % sizeof(void *) != 0) {
        *why = "plugin abi api_size " + std::to_string(abi->api_size) +
               " does not end on a member boundary";
        return POP_E_ABI;
    }
    if (abi->api_size < API_MIN || abi->api_size > sizeof(PopModApi)) {
        *why = "plugin abi api_size " + std::to_string(abi->api_size) +
               " is not a usable prefix of this host's " + std::to_string(sizeof(PopModApi));
        return POP_E_ABI;
    }
    if (abi->cpu_size < POP_CPU_V1_MIN_SIZE || abi->cpu_size > POP_CPU_V1_BASELINE_SIZE) {
        *why = "plugin abi cpu_size " + std::to_string(abi->cpu_size) + " is outside " +
               std::to_string(POP_CPU_V1_MIN_SIZE) + ".." +
               std::to_string(POP_CPU_V1_BASELINE_SIZE);
        return POP_E_ABI;
    }
    return POP_OK;
}

} // namespace

uint32_t mods_record_count() {
    const size_t n = contexts().size();
    const size_t base = generation_base();
    // Both are zero before anything has loaded, and the answer is zero: no mod
    // has been recorded, which is the truth rather than an error. The guard is
    // for the other case - a base at or past the end, which is what a retire
    // leaves - because this is unsigned arithmetic and the wrap would answer
    // four billion, and the caller's very first mods_record would then index
    // far outside the deque. A host that asks before the loader has run is
    // asking a reasonable question and must get a usable answer.
    return base >= n ? 0u : (uint32_t)(n - base);
}

// The typed reason a mod was rejected, or POP_OK. Declared where it is used
// rather than in mods_internal.h, which belongs to another task: a status a
// caller can branch on is worth having, and searching a sentence for the word
// "abi" is not a test of anything.
extern "C" PopModStatus mods_record_status(const char *id) {
    for (size_t i = generation_base(); i < contexts().size(); ++i)
        if (contexts()[i].id == id)
            return contexts()[i].status;
    return POP_OK;
}

bool mods_record(uint32_t i, const char **id, const char **version, const char **dir,
                 const char **plugin, const char **script, const char **assets, int *loaded,
                 const char **reason, uint32_t *owner, int *affects_simulation) {
    if (i >= mods_record_count())
        return false;
    ModContext &c = contexts()[generation_base() + i];
    if (id)
        *id = c.id.c_str();
    if (version)
        *version = c.version.c_str();
    if (dir)
        *dir = c.dir.c_str();
    if (plugin)
        *plugin = c.plugin.c_str();
    if (script)
        *script = c.script.c_str();
    if (assets)
        *assets = c.assets.c_str();
    if (loaded)
        *loaded = c.loaded ? 1 : 0;
    if (reason)
        *reason = c.reason.c_str();
    if (owner)
        *owner = c.owner;
    if (affects_simulation)
        *affects_simulation = c.affects_simulation ? 1 : 0;
    return true;
}

// Discover, validate and order mods, then initialize each against its guarded API.
// Production loading occurs once per process; shutdown owns unloading and resource reclamation.
// Directory entries that can hold a mod: anything not hidden.
static int collect_mod_dirs(const char *name, void *user) {
    if (name[0] != '.')
        ((std::vector<std::string> *)user)->push_back(name);
    return 0;
}

bool mods_load_all() {
    g_shutdown_done = false; // a fresh run, so its record is due again
    // Once per process, and the spec is why: v1 unloads a plugin only at
    // shutdown, so there is no reload to support. A second call would have to
    // retire a generation while retained guards could still be walking it, and
    // append to the same container they walk - neither of which is safe and
    // neither of which anything needs. So it is refused, and refused without
    // touching anything, which is what POP_E_STATE means for a call that
    // returns a bool.
    //
    // The test binary loads many times, and does it through the reset below.
    if (g_loaded_once) {
        LOGW("mods: mods_load_all has already run; refusing a second load "
             "(POP_E_STATE). Plugins are unloaded at shutdown and not before.");
        return false;
    }
    g_loaded_once = true;
    retire_contexts();
    const ModsRoots roots = mods_roots();
    printf("mods: roots core=%s user=%s\n", roots.core.c_str(), roots.user.c_str());
    // The overlay and saved settings first, before anything that can fail and before
    // RECOMP_NO_MODS is honoured: the profile being the writable tier is what
    // keeps a game's saves and settings out of its own installation, and that
    // holds for a port with no symbol table and no mods at all.
    mods_overlay_reset();
    mods_settings_load(mods_settings_path());
    mods_settings_recover_pending(); // Test85: an unproven graphics Apply from the last session is undone
    if (!mods_symbols_load(nullptr)) {
        LOGW("mods: %s", mods_symbols_error());
        return false;
    }
    // Runtime observation is always on, including RECOMP_NO_MODS.
    if (!mods_sprite_hooks_init())
        return false;
    if (recomp_env("NO_MODS"))
        return true;
    // Every module asks mods_api_for; from here on it is this loader that
    // answers, through the one seam.
    mods_set_context_provider([](uint32_t owner) -> const PopModApi * {
        // A revoked context is not handed out at all: a module about to invoke
        // a callback asks here first, and a mod that has been rolled back must
        // not be reached even by something that still holds its id.
        for (ModContext &c : contexts())
            if (c.owner == owner)
                return c.revoked.load(std::memory_order_acquire) ? nullptr : &c.api;
        return nullptr;
    });
    if (!mods_events_init())
        return false;
    if (!mods_animation_init())
        return false;
    if (!mods_game_settings_init())
        return false;
    if (!mods_options_init())
        return false;
    // The settings page is NOT armed here. Arming a keyboard handler for a
    // page the host may never draw is the host's decision, not the loader's,
    // and Task 12 wires mods_page_init into the presenters that can actually
    // draw it.
    mods_host_set_main_thread();
    // Lifecycle step 2: the Lua runtime is a core plugin with its own init,
    // ordered before every user mod so a [script] mod has an interpreter.
    if (!mods_lua_core_init())
        LOGW("mods: no Lua runtime in this build; [script] mods will be rejected");

    // 1. Discover and validate.
    std::vector<ModManifest> manifests;
    std::vector<std::pair<std::string, std::string>> rejected;
    auto discover = [&](const std::string &root, bool core) {
        std::vector<std::string> dirs;
        if (os_listdir(root.c_str(), collect_mod_dirs, &dirs) == 0) {
            std::sort(dirs.begin(), dirs.end()); // deterministic discovery
            for (const std::string &name : dirs) {
                std::string dir = root + "/" + name;
                std::string text = read_text(dir + "/mod.toml");
                if (text.empty())
                    continue;
                std::string error;
                ModManifest m = mods_parse_manifest(text, &error);
                m.dir = dir;
                m.core_root = core;
                if (!error.empty()) {
                    rejected.push_back({m.id.empty() ? name : m.id, error});
                    continue;
                }
                if (m.api != POP_MOD_API_VERSION) {
                    rejected.push_back(
                        {m.id, "api " + std::to_string(m.api) + " is not this host's api 1"});
                    continue;
                }
                std::string want = mods_symbols_exe_sha256();
                if (m.game.size() > want.size() || want.compare(0, m.game.size(), m.game) != 0) {
                    rejected.push_back({m.id, "game hash does not match the loaded EXE"});
                    continue;
                }
                manifests.push_back(m);
            }
        }
    };
    discover(roots.core, true);
    discover(roots.user, false);
    mods_resolve_order(manifests, &rejected);

    // 2 and 3. Each mod in load order, inside its own transaction.
    std::set<std::string> failed;
    for (const ModManifest &m : manifests) {
        if (!owner_available()) {
            // Nothing is half-loaded by this: the mod is recorded as rejected
            // exactly as a bad manifest would be, and every mod after it gets
            // the same answer.
            contexts().emplace_back();
            ModContext &r = contexts().back();
            r.id = m.id;
            r.status = POP_E_LIMIT;
            r.reason = "no owner id is left for it";
            failed.insert(m.id);
            printf("mods: rejected %s: %s\n", r.id.c_str(), r.reason.c_str());
            continue;
        }
        contexts().emplace_back();
        ModContext &c = contexts().back();
        c.id = m.id;
        c.name = m.name;
        c.version = m.version;
        c.dir = m.dir;
        c.plugin = m.plugin_path;
        c.script = m.script_path;
        c.assets = m.assets_path;
        c.affects_simulation = m.affects_simulation;
        c.owner = next_owner()++;
        build_api(c);
        mods_hooks_set_load_order(c.owner, c.owner);

        // A dependent of a mod that already failed never runs its own init.
        std::string why;
        for (const ModRequire &r : m.requires_)
            if (failed.count(r.id))
                why = "requires " + r.id + ", which failed to load";

        if (why.empty()) {
            // The page is saved around this mod's init for the same reason
            // the settings are: it is host state this mod can change, and a
            // failure must leave none of it behind. The page is not a
            // registration, so the rollback does not put it back.
            mods_page_state_push();
            mods_settings_txn_begin();
            for (const ModSetting &s : m.settings) {
                mods_settings_declare(c.owner, m.id.c_str(), s.key.c_str(), s.label.c_str(), s.kind,
                                      s.def, s.min, s.max);
                mods_settings_set_presentation(c.owner, s.key.c_str(), s.step, s.unit.c_str());
                mods_settings_set_meta(c.owner, s.key.c_str(), s.group.c_str(), s.help.c_str(),
                                       s.apply.c_str(), s.choices.c_str());
            }
            // The loader's own [assets] insertion happens BEFORE the public
            // window opens: it is the loader pushing a layer on the mod's
            // behalf, not the mod pushing one, and it must not depend on the
            // window a plugin is allowed to push in.
            if (!m.assets_path.empty()) {
                uint32_t layer = 0;
                mods_overlay_begin_init(c.owner);
                PopModStatus st =
                    mods_overlay_push(c.owner, (m.dir + "/" + m.assets_path).c_str(), &layer);
                mods_overlay_end_init();
                if (st != POP_OK) {
                    c.status = st;
                    why = "its [assets] directory could not be added (" + std::to_string((int)st) +
                          ")";
                }
            }
        }

        const RecompBuiltinMod *builtin = why.empty() ? builtin_for(m.plugin_path) : nullptr;
        if (builtin) {
            c.handle = (void *)builtin;
        } else if (why.empty() && !m.plugin_path.empty()) {
            std::string path = m.dir + "/" + m.plugin_path;
            OsStat st;
            if (os_stat(path.c_str(), &st) != 0) {
                // A manifest written on one platform names that platform's
                // extension; the plugin shipped for this one has the same stem.
                size_t dot = m.plugin_path.rfind('.');
                if (dot != std::string::npos)
                    path = m.dir + "/" + m.plugin_path.substr(0, dot) + os_plugin_extension();
            }
            c.handle = os_dlopen(path.c_str());
            if (!c.handle)
                why = std::string("dlopen failed: ") + os_dlerror();
        }
        if (why.empty() && c.handle) {
            if ((c.status = validate_abi(c.handle, &why)) != POP_OK) {
                // validate_abi said what is wrong and gave the typed status
            } else {
                auto init =
                    (PopModStatus (*)(const PopModApi *))plugin_sym(c.handle, "pop_mod_init");
                if (!init) {
                    why = "no pop_mod_init export";
                } else {
                    const PopModAbi *abi = (const PopModAbi *)plugin_sym(c.handle, "pop_mod_abi");
                    mods_hooks_set_cpu_size(c.owner, abi->cpu_size);
                    c.exit_fn = (PopModStatus (*)())plugin_sym(c.handle, "pop_mod_exit");
                    // Open across pop_mod_init and nothing else. A [script]
                    // running afterwards can call back into a plugin API the
                    // mod retained, and that must not be a second chance to
                    // push a layer.
                    mods_overlay_begin_init(c.owner);
                    PopModStatus st = init(&c.api);
                    mods_overlay_end_init();
                    if (st != POP_OK) {
                        c.status = st;
                        why = "pop_mod_init returned " + std::to_string((int)st);
                    }
                }
            }
        }
        if (why.empty() && !m.script_path.empty()) {
            if (!mods_lua_available())
                why = "this build has no Lua runtime, so its [script] cannot load";
            else if (const char *e =
                         mods_lua_run_script(c.owner, (m.dir + "/" + m.script_path).c_str()))
                why = std::string("script error: ") + e;
        }

        if (why.empty()) {
            // The identity of what this run actually loaded, taken now rather
            // than read back from disk at shutdown: a payload replaced or
            // rebuilt during the run would otherwise be recorded as the one
            // that ran. One call covers the plugin, the script and the assets,
            // because all three live in the mod's directory.
            //
            // EVERY mod that commits, not only those with something to dlopen.
            // An assets-only mod has no plugin and no script, and its assets
            // are its entire contribution: it is the one whose identity most
            // needs taking at the moment it loaded, and gating this on a
            // plugin path was exactly the case that missed it.
            mods_run_record_capture_payload(m.dir.c_str());
            mods_page_state_discard();
            mods_settings_txn_commit();
            c.loaded = true;
            printf("mods: loaded %s %s\n", c.id.c_str(), c.version.c_str());
        } else {
            // Revoke BEFORE reclaiming: from here nothing the plugin still
            // holds can register anything new, so the rollback below cannot
            // race a registration it has already passed.
            revoke_api(c);
            mods_page_state_restore();
            rollback(c.owner);
            // The handle is NOT closed here. Unloading happens only at
            // shutdown, because a captured invocation can still hold this
            // plugin's callback and its user data, and unmapping the code
            // under it is a crash with no stack. It is closed in
            // mods_shutdown, once the scheduler says nothing is running.
            c.reason = why;
            failed.insert(c.id);
            printf("mods: rejected %s: %s\n", c.id.c_str(), why.c_str());
        }
    }

    for (const auto &r : rejected) {
        contexts().emplace_back();
        ModContext &c = contexts().back();
        c.id = r.first;
        c.reason = r.second;
        printf("mods: rejected %s: %s\n", c.id.c_str(), c.reason.c_str());
    }

    // Everything a mod installed during pop_mod_init is queued, because this
    // thread is not a guest thread and holds no baton. Applying it here, while
    // no guest thread exists, is what makes a hook installed at init live for
    // the entry point itself rather than from the first checkpoint after it.
    mods_registry_pump_preentry();

    // 4. The layer list is fixed from here.
    mods_overlay_seal();
    mods_settings_save();
    return true;
}

namespace {
bool g_shutdown_requested = false;

// The teardown itself. Only ever called when quiescence holds.
void shutdown_now() {
    // The record is written FIRST, while the settings, the mod list and the
    // payload paths are all still here: writing it after the teardown loop
    // would record an empty run.
    mods_write_run_record(host_state_file("mods/run.json").c_str());

    for (size_t i = contexts().size(); i-- > generation_base();) {
        ModContext &c = contexts()[i];
        if (!c.loaded)
            continue;
        // Live through its own exit, revoked the moment it returns, and only
        // then reclaimed. The three are in this order for three different
        // reasons: the exit needs the API, nothing after the exit may use it,
        // and reclaiming before either would pull the ground from under both.
        if (c.exit_fn)
            c.exit_fn();
        revoke_api(c);
        rollback(c.owner);
    }
    // Anything a rollback could not reclaim earlier, reclaimed now.
    for (uint32_t owner : deferred_reclaim()) {
        mods_guest_free_all(owner);
        mods_lua_drop_mod(owner);
    }
    deferred_reclaim().clear();

    // The plugins that failed to initialise kept their handles too, for the
    // same reason the loaded ones did. This is the point at which unloading is
    // safe, so it is the point at which they go.
    // The handles of THIS generation. A retired context's plugin may still be
    // holding a pointer into its own code, so its image stays mapped: the
    // guard it would reach is only reachable while the code that calls it is.
    for (size_t i = generation_base(); i < contexts().size(); ++i)
        if (contexts()[i].handle) {
            if (!is_builtin(contexts()[i].handle))
                os_dlclose(contexts()[i].handle);
            contexts()[i].handle = nullptr;
        }
    // The on-screen controls hold their last hidden-group change until a pump
    // flushes it; there is no pump after this one.
    mods_controls_flush();
    mods_settings_save();
    // The runtime's own registrations go last, so nothing is left believing it
    // has hooks in a registry that is about to be gone.
    mods_events_reset();
    mods_animation_reset();
    mods_hooks_remove_all(MODS_OWNER_RUNTIME);
    mods_host_services_remove_all(MODS_OWNER_RUNTIME);
    mods_lua_shutdown();
    mods_set_context_provider(nullptr);
    retire_contexts();
    g_shutdown_requested = false;
    g_shutdown_done = true;
}
} // namespace

#ifdef POPM_TESTING
// Starts the loader over, for a test binary that loads many times in one
// process. Not compiled into a real build, because a real build loads once.
//
// It waits for quiescence before retiring anything - no guest thread running,
// no mod callback on a stack, no guard in flight - so the generation it
// retires is one nothing can be part-way through reading. That is the same
// property production gets for free by never retiring at all.
extern "C" bool mods_test_reset_loader(void) {
    sched_registry_lock();
    bool idle = sched_guest_threads_stopped_locked() && mods_hook_depth() == 0 &&
                guards_in_flight().load(std::memory_order_acquire) == 0;
    sched_registry_unlock();
    if (!idle)
        return false;
    // The full teardown, so a retired generation does not keep its exit
    // functions unrun, its guest memory unreclaimed or its images mapped.
    if (g_loaded_once) {
        mods_shutdown_request();
        // Bounded. Quiescence was true a moment ago and the teardown does not
        // need anything else, so this ends on the first or second turn; a loop
        // with no bound would hang a test binary rather than fail it.
        int tries = 0;
        while (!mods_shutdown_complete())
            if (++tries > 1000)
                return false;
    }
    g_loaded_once = false;
    return true;
}

// Puts the owner counter where a test needs it, so the end of the range can be
// reached in a test rather than in a thought experiment.
extern "C" void mods_test_set_next_owner(uint32_t owner) {
    next_owner() = owner;
}
extern "C" uint32_t mods_test_next_owner(void) {
    return next_owner();
}

// So a test can prove the counter is not leaked by a forward that never
// returns. It must read zero after every unwind.
extern "C" int mods_test_guards_in_flight(void) {
    return guards_in_flight().load(std::memory_order_acquire);
}
#endif

// Asked for once. Every mod is revoked immediately, so from here nothing new
// can be registered and no plugin call does anything, whether or not the
// teardown can run yet.
void mods_shutdown_request() {
    g_shutdown_requested = true;
    // Nothing is revoked here. A mod's pop_mod_exit is the last chance it has
    // to write anything down, and persisting state at exit is the ordinary
    // reason to have one: revoking up front made every settings_set from an
    // exit handler return POP_E_STATE, which is not a refusal anyone asked
    // for. Each mod is revoked immediately after its own exit returns, in
    // shutdown_now, and still before anything of it is reclaimed.
}

// Polled by the host until it returns true. It runs the teardown the first
// time quiescence holds and reports true from then on; until then it reports
// false and changes nothing, so a host that stops its workers and keeps
// asking always finishes, and one that never stops them never tears down
// under a running thread.
bool mods_shutdown_complete() {
    if (!g_shutdown_requested)
        mods_shutdown_request();
    // Only the main thread may complete a shutdown. The teardown touches
    // host-owned state - the settings file, the overlay, the page - that
    // belongs to that thread, and a worker passing the quiescence test would
    // otherwise tear it down from the wrong one. A worker asking is told the
    // shutdown is not finished, which is true.
    if (!mods_host_on_main_thread())
        return false;
    // The condition is whether the LOADER RAN, not whether anything loaded.
    // A run that reached the loader and found an empty or absent mods
    // directory is still a run, and its record - an explicit empty set with
    // the build and input metadata - is what says so. Gating on the record
    // count meant those runs wrote nothing, so "no mods ran" and "nothing
    // wrote a record" looked identical in the output, which is the one
    // distinction the record exists to make.
    //
    // g_shutdown_done keeps a second ask from writing a second record;
    // shutdown_now is already correct with nothing to tear down.
    if (!g_loaded_once)
        return true;
    if (g_shutdown_done)
        return true;
    if (!quiescent())
        return false;
    shutdown_now();
    return true;
}

// The single-call form the seam declares. It asks and then completes, which is
// correct exactly when the caller has already stopped its guest workers.
void mods_shutdown() {
    mods_settings_clear_pending(); // Test85: a clean shutdown proves the last graphics Apply
    mods_shutdown_request();
    if (!mods_shutdown_complete())
        LOGW("mods: shutdown asked for while guest threads are still running "
             "or a mod callback is on this stack; every mod is revoked and the "
             "teardown waits for mods_shutdown_complete()");
}
