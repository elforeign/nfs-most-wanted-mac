// mods_internal.h - every declaration shared between the mods module's
// translation units, in one place, with C linkage throughout.
//
// WHY C LINKAGE EVERYWHERE. These names are also referenced from the runtime's
// weak seam (runtime/mods_seam.h) and from C test fixtures. Mixing
// C++ linkage here and C linkage there is a link error at best and two
// different symbols at worst, so there is exactly one rule: extern "C".
//
// The few helpers that genuinely need C++ types (std::string, std::vector)
// live at the bottom, are used only inside the module, and are named
// mods_cpp_*.
#pragma once
#include "pop_mod_api.h"
#include "../runtime/guest.h"

#include <stdint.h>

// Owner identities. The runtime and the Lua core are not user mods and their
// registrations are never rolled back by a user mod's failure.
#define MODS_OWNER_RUNTIME 0u
#define MODS_OWNER_LUA 1u
#define MODS_OWNER_FIRST_MOD 2u

// The mod heap: the gap between the game heap's HEAP_LIMIT (0x0e000000) and
// the guest stack's lower limit (0x0ef00000), less a megabyte of guard.
#define MOD_HEAP_BASE 0x0e000000u
#define MOD_HEAP_END 0x0ee00000u

extern "C" {

uint32_t mods_hook_ancestor_stack(uint32_t addr);
bool mods_animation_init();
void mods_animation_reset();
bool mods_game_settings_init();
void mods_game_settings_reset();
#ifdef POPM_TESTING
uint64_t mods_view_test_push_count();
#endif

// ---- hooks (T5) -----------------------------------------------------------
PopModStatus mods_hook_install(uint32_t owner, uint32_t addr, PopHookFn fn, int32_t mode,
                               void *user, uint32_t *out_id);
PopModStatus mods_hook_install_ex(uint32_t owner, uint32_t addr, uint32_t return_pc, PopHookFn fn,
                                  int32_t mode, uint32_t flags, void *user, uint32_t *out_id);
PopModStatus mods_hook_install_at_callsite(uint32_t owner, uint32_t addr, uint32_t return_pc,
                                           PopHookFn fn, int32_t mode, void *user,
                                           uint32_t *out_id);
PopModStatus mods_hook_remove(uint32_t owner, uint32_t id);
void mods_hooks_remove_all(uint32_t owner);
void mods_hooks_set_load_order(uint32_t owner, uint32_t order);
void mods_hooks_set_cpu_size(uint32_t owner, uint32_t cpu_size);
uint32_t mods_hooks_installed_count(void);
void mods_hooks_reset(void);
// Test seam only: substitutes the storage a callback's pop_cpu_v1 lives in, so
// a test can poison the bytes past a declared size and prove nothing wrote
// them, and can hand over a buffer smaller than the declared size. Every
// transfer is bounded by the smallest of host layout, declared size and this
// capacity. No production code calls it.
// Compiled only into a test binary, with its state: a production host has no
// way to redirect where a callback's registers live, which is how it should be.
#ifdef POPM_TESTING
void mods_hooks_set_test_cpu_buffer(void *buf, uint32_t bytes);
#endif
PopModStatus mods_call_original(const PopModApi *api, uint32_t addr, pop_cpu_v1 *cpu);
PopModStatus mods_call_next(const PopModApi *api, PopHookInvocation *inv, pop_cpu_v1 *cpu);
PopModStatus mods_hook_return(const PopModApi *api, pop_cpu_v1 *cpu, uint32_t eax,
                              uint32_t arg_bytes);
void mods_fill_hooks_api(PopModApi *api);
// Applies mutations queued from a non-guest thread. Called by the runtime's
// scheduler checkpoint and by the host's tick.
void mods_registry_pump(void);
// Applies the queue once before the guest entry point runs, which is the only
// time it is safe to publish without the baton: no guest thread exists yet, so
// nothing is dispatching. The loader calls it at the end of mods_load_all so a
// hook installed during pop_mod_init is live for the entry point itself.
// Refuses, with a warning, if asked while a guest thread is running.
void mods_registry_pump_preentry(void);
// Signal-safe: a preformatted description of the callback running on this
// thread, or "". Never allocates and never formats.
const char *mods_active_callback_desc(void);
void mods_hooks_unwind_to_esp(uint32_t esp);
// How deep this thread is in hook invocations and in view snapshots; used by
// the unwind tests and by the view layer to restore an outer scope exactly.
uint32_t mods_hook_depth(void);
// Attribution shared by every callback kind - hooks, events, input, providers.
// mods_intern_desc returns a pointer that is stable for the life of the
// process, so a fault handler can print it without formatting anything.
const char *mods_intern_desc(const char *text);
const char *mods_push_active_callback(const char *desc); // returns the previous
void mods_pop_active_callback(const char *previous);

// ---- guest memory and symbols (T6) ---------------------------------------
PopModStatus mods_guest_alloc(uint32_t owner, uint32_t size, uint32_t *out_addr);
PopModStatus mods_guest_free(uint32_t owner, uint32_t addr);
void mods_guest_free_all(uint32_t owner);
uint32_t mods_guest_alloc_count(uint32_t owner);
bool mods_guest_owns(uint32_t addr);
void mods_fill_memory_api(PopModApi *api);

bool mods_symbols_load(const char *path);
const char *mods_symbols_error(void);
const char *mods_symbols_exe_sha256(void);
uint32_t mods_symbols_count(void);
PopModStatus mods_symbol(const char *name, uint32_t *out_addr);
PopModStatus mods_symbols_matching(const char *prefix, uint32_t *out, uint32_t cap,
                                   uint32_t *out_count);
bool mods_symbol_hookable(uint32_t addr);
const char *mods_symbol_kind(uint32_t addr); // "entry", "alternate", ...
uint32_t mods_symbol_global(const char *name);
uint32_t mods_symbol_global_stride(const char *name);
uint32_t mods_symbol_global_count(const char *name);
uint32_t mods_symbol_event(const char *name);
// Changes whenever the symbol tables are replaced (mods_symbols_load).
uint64_t mods_symbols_generation(void);

// ---- overlay (T7) ---------------------------------------------------------
void mods_overlay_reset(void);
void mods_overlay_set_profile_dir(const char *dir);
const char *mods_overlay_profile_dir(void);
PopModStatus mods_overlay_push(uint32_t owner, const char *dir, uint32_t *out_id);
void mods_overlay_remove_all(uint32_t owner);
void mods_overlay_begin_init(uint32_t owner);
void mods_overlay_end_init(void);
void mods_overlay_seal(void);
bool mods_overlay_sealed(void);
bool mods_overlay_layer_name_refused(const std::string &rel);   // run 29: program files never served from a layer
uint32_t mods_overlay_layer_count(void);
void mods_fill_overlay_api(PopModApi *api);

// ---- events, game view, input (T8) ---------------------------------------
bool mods_events_init(void);
void mods_events_remove_all(uint32_t owner);
// Drops every subscription AND the runtime's own installed event hooks, so a
// suite or a shutdown starts from a registry that holds nothing.
void mods_events_reset(void);
PopModStatus mods_on_frame(uint32_t owner, int32_t phase, PopEventFn fn, void *u, uint32_t *id);
PopModStatus mods_on_turn(uint32_t owner, int32_t phase, PopEventFn fn, void *u, uint32_t *id);
PopModStatus mods_on_level_load(uint32_t owner, PopEventFn fn, void *u, uint32_t *id);
PopModStatus mods_on_level_end(uint32_t owner, PopEventFn fn, void *u, uint32_t *id);
PopModStatus mods_on_key(uint32_t owner, PopKeyFn fn, void *u, uint32_t *id);
PopModStatus mods_on_mouse(uint32_t owner, PopMouseFn fn, void *u, uint32_t *id);
void mods_fill_events_api(PopModApi *api);

// One snapshot scope per callback, nested per guest thread.
void mods_view_push(void);
void mods_view_push_disabled(void);
void mods_view_pop(void);
uint32_t mods_view_depth(void);
// Drops the snapshots above `depth` on this thread and keeps the outer ones.
void mods_view_truncate(uint32_t depth);
// Drops every snapshot on this thread; called when an unwind abandons frames.
void mods_view_reset(void);
bool mods_view_active(void);
uint32_t mods_entity_count(void);
PopModStatus mods_entity_slot(uint32_t nth, uint32_t *out_slot);
PopModStatus mods_entity(uint32_t slot, PopEntityView *out);
PopModStatus mods_tribe(uint32_t i, PopTribeView *out);
bool mods_game_paused(void);
uint32_t mods_simulation_turn(void);
uint32_t mods_command_frame(void);

bool mods_input_key(uint8_t dik, uint8_t vk, bool down);
bool mods_input_button(int button, bool down, int32_t x, int32_t y);
bool mods_input_motion(int32_t x, int32_t y, int32_t dx, int32_t dy);
bool mods_input_wheel(int32_t dz);
void mods_input_release_all(void);
void mods_input_remove_all(uint32_t owner);
PopModStatus mods_input_add_key(uint32_t owner, PopKeyFn fn, void *user, uint32_t *out_id);
PopModStatus mods_input_add_mouse(uint32_t owner, PopMouseFn fn, void *user, uint32_t *out_id);

// ---- settings (T4) --------------------------------------------------------
const char *mods_settings_path(void);
bool mods_settings_load(const char *path);
bool mods_settings_save(void);
void mods_settings_reset(void);
PopModStatus mods_settings_get(uint32_t owner, const char *key, int64_t *out);
PopModStatus mods_settings_set(uint32_t owner, const char *key, int64_t v);
// Remaster run 16 (settings.cpp top): mods_settings_set is the player's choice (an explicit override, kept even
// when it equals the default). The mod API's settings_set is a derived write: the declared default makes the key
// follow the default again, any other value is held. Restore defaults removes the override (inherit).
PopModStatus mods_settings_set_derived(uint32_t owner, const char *key, int64_t v);
PopModStatus mods_settings_inherit(uint32_t owner, const char *key);
// "mod_id/key" holds an explicit override (it is in the profile file).
bool mods_settings_is_override(const char *mod_id_slash_key);
void mods_settings_remove_all(uint32_t owner);
// One transaction per mod init: restore puts back both the declarations and
// the persisted values as they were before that mod touched them.
void mods_settings_txn_begin(void);
void mods_settings_txn_commit(void);
void mods_settings_txn_rollback(void);
uint32_t mods_settings_entry_count(void);
// Entry i, by owner load order then declaration order.
bool mods_settings_entry(uint32_t i, uint32_t *owner, const char **mod_id, const char **key,
                         const char **label, int32_t *kind, int64_t *value, int64_t *min,
                         int64_t *max);
void mods_settings_declare(uint32_t owner, const char *mod_id, const char *key, const char *label,
                           int32_t kind, int64_t def, int64_t min, int64_t max);
// A declared setting's range, after the fact: the controls' layout row gains
// a choice when the player saves a layout under a new name. The stored value
// is clamped into the new range; an undeclared key is ignored.
void mods_settings_set_range(uint32_t owner, const char *key, int64_t min, int64_t max);
// Settings-page presentation of an int entry: step per nudge (>= 1) and unit
// ("" or "$"). Storage, ranges and the mod API are unaffected.
void mods_settings_set_presentation(uint32_t owner, const char *key, int64_t step,
                                    const char *unit);
bool mods_settings_presentation(uint32_t i, int64_t *step, const char **unit);
// Settings menu metadata from the manifest (Test72): group, help, apply timing, choice labels.
void mods_settings_set_meta(uint32_t owner, const char *key, const char *group, const char *help,
                            const char *apply, const char *choices);
bool mods_settings_meta(uint32_t i, const char **group, const char **help, const char **apply,
                        const char **choices);
// The persisted value under "mod_id/key", whether or not anything has
// declared that key this run. Used for one-time migrations that read an
// older owner's settings without declaring them (controls_settings.cpp
// reading host.keypad/*).
bool mods_settings_stored_value(const char *mod_id_slash_key, int64_t *out);
// Entry i's declared default (Test85: the menu's Restore defaults actions).
bool mods_settings_default(uint32_t i, int64_t *out);
// Test85: last-known-good graphics (settings.cpp; the recorder, mods_cpp_settings_mark_pending, is with the
// C++ helpers at the end). Cleared once the game has run 20 s after an Apply or at a clean shutdown; put back
// at the next launch otherwise.
void mods_settings_clear_pending(void);
// Test86 (also in runtime/mods_seam.h): a declared setting's value by "mod_id/key", or `fallback`.
int64_t mods_setting_value(const char *mod_id_slash_key, int64_t fallback);
int mods_settings_recover_pending(void);
// NFSMW 3.0.4 (D-202 item 4; also in runtime/mods_seam.h): the settings menu pausing the game (settings_menu.cpp).
bool mods_menu_pause_wanted(void);
void mods_menu_note_pause(bool paused);

// ---- host services and the page (T9) --------------------------------------
void mods_host_set_main_thread(void);
bool mods_host_on_main_thread(void);
PopModStatus mods_register_menu_item(uint32_t owner, const char *path, const char *label,
                                     PopMenuFn cb, void *user);
PopModStatus mods_register_setting(uint32_t owner, const PopSettingDesc *desc);
PopModStatus mods_texture_override_provider(uint32_t owner, PopTextureProviderFn cb, void *user);
// Called from the DirectX texture upload path on the host thread; the same
// declaration the runtime seam carries. 1 when a provider produced an override
// whose storage is at least w*h*4 bytes.
int mods_texture_override(uint64_t hash64, int32_t w, int32_t h, int32_t format,
                          uint8_t **out_rgba8, uint32_t *out_bytes);
PopModStatus mods_texture_override_provider_ex(uint32_t owner, PopTextureProviderExFn cb,
                                               void *user);
int mods_texture_override_ex(uint64_t hash64, int32_t w, int32_t h, int32_t format,
                             PopTextureReplacement *out);
uint32_t mods_menu_entry_count(void);
bool mods_menu_entry(uint32_t i, uint32_t *owner, const char **path, const char **label);
PopModStatus mods_menu_activate(uint32_t i);
void mods_host_services_remove_all(uint32_t owner);
void mods_fill_host_api(PopModApi *api);

void mods_page_init(void);
PopModStatus mods_page_open(const char *mod_id);
void mods_options_request(void);
void mods_page_close(void);
bool mods_page_visible(void);
uint32_t mods_page_cursor(void);
uint32_t mods_page_line_count(void);
const char *mods_page_line(uint32_t i);
// Draws the page over a presented frame. 8 bpp uses palette index
// `ink`/`paper`; 16 bpp uses 5-6-5 values. No-op when the page is hidden.
// Draws into HOST-OWNED frame storage, never into the guest's surface pixels.
void mods_page_draw(void *pixels, int w, int h, int bpp, int pitch, const uint32_t *palette);
// See runtime/mods_seam.h. Defined in settings_menu_draw.cpp.
int mods_menu_render(int w, int h, uint64_t *generation, const uint8_t **pixels);
int mods_menu_pointer(float u, float v, int button, int down, int wheel);
// The page is host state rather than a registration, so a rollback does not
// restore it. The loader saves it around each mod's init and restores it when
// that init fails, so a mod that opened its page and then failed does not
// leave the page open on its behalf.
void mods_page_state_push(void);
void mods_page_state_restore(void);
void mods_page_state_discard(void);
// Eight rows of a 6x8 glyph, bit 5 leftmost; '?' for anything outside 32..127.
const uint8_t *mods_font6x8_glyph(char c);

// ---- per-mod context (T1) -------------------------------------------------
// One definition, in context.cpp. The loader installs the real provider; a
// test installs its own; nothing else defines mods_api_for.
const PopModApi *mods_api_for(uint32_t owner);
void mods_set_context_provider(const PopModApi *(*fn)(uint32_t owner));

// ---- loader (T10) ---------------------------------------------------------
bool mods_load_all(void);
// Shutdown is two steps because it cannot always happen when it is asked for.
// mods_shutdown_request revokes every mod immediately, so nothing new can be
// registered and no plugin call does anything from that moment. The teardown -
// pop_mod_exit, reclamation, unloading - runs only when no guest thread is
// running and no mod callback is on the stack, so the host stops its workers
// and then calls mods_shutdown_complete until it returns true.
// mods_shutdown is the single-call form: correct when the caller has already
// stopped its workers, and a warning otherwise.
void mods_shutdown_request(void);
bool mods_shutdown_complete(void);
void mods_shutdown(void);
const PopModApi *mods_api_for(uint32_t owner);
uint32_t mods_record_count(void);
bool mods_record(uint32_t i, const char **id, const char **version, const char **dir,
                 const char **plugin, const char **script, const char **assets, int *loaded,
                 const char **reason, uint32_t *owner, int *affects_simulation);

// ---- Lua core (T11) -------------------------------------------------------
// Null on success, an error message otherwise. Defined by the Lua core when it
// is linked; the loader rejects a [script] mod with a reason when it is not.
const char *mods_lua_run_script(uint32_t owner, const char *path);
bool mods_lua_available(void);
// Creates the shared interpreter state; called by mods_lua_core_init.
bool mods_lua_open_runtime(void);
// Test seams: read an integer global, and evaluate a chunk returning a string.
// Compiled only into a test binary. A production host has no business running
// arbitrary Lua inside a mod's own interpreter, and an entry point that does is
// the first thing an exploit would look for.
#ifdef POPM_TESTING
bool mods_lua_global_int(uint32_t owner, const char *name, int64_t *out);
const char *mods_lua_eval(uint32_t owner, const char *chunk);
#endif
// The pinned globals a script can read. PopModApi has no accessor for them, so
// this is the one seam that stands in for one - declared here, defined in
// mods/lua/bindings.cpp, and implemented in terms of the API's own
// symbol lookup and bounds-checked reads, so that every guest byte a Lua
// binding can reach goes through the same check and there is no second path.
#define MODS_PINNED_PAUSED 0
#define MODS_PINNED_SIMULATION_TURN 1
#define MODS_PINNED_COMMAND_FRAME 2
PopModStatus mods_lua_read_pinned(const PopModApi *api, int32_t which, int64_t *out);
// Lifecycle step 2: the Lua runtime's own init, ordered before every user mod.
bool mods_lua_core_init(void);
void mods_lua_drop_mod(uint32_t owner);
void mods_lua_shutdown(void);
uint32_t mods_lua_errors(void);

// ---- run record (T13) -----------------------------------------------------
// Writes the record for this run. Valid to call whether or not any mod loaded
// and whether or not mods were enabled at all: a run with none records an
// explicit empty set, with the build and input metadata that identify it, so
// "no mods" and "no record" are different things in the output. Returns false
// without leaving a partial file if anything it needs is missing.
bool mods_write_run_record(const char *path);
// Captures a mod directory's identity when the loader COMMITS the mod - after
// its plugin has loaded and its init has succeeded. That is the most precise
// statement of what this run ran, and it excludes a mod that failed, whose
// payload was never part of the run. Call it once per committed mod, with the
// mod's directory.
//
// Without it the record falls back to hashing the directory at shutdown and
// says so (`payload_at: "shutdown"`), which describes the files as they are
// then rather than as they ran.
//
// There is no precondition. It is safe from any thread, at any point in the
// process including before main, whether or not a loader exists, and it never
// blocks on a FIFO or a device node it finds in the tree. An earlier version
// was none of those things, and moving the call earlier in the loader was
// measured to break unrelated parts of the process; see the comment on the
// definition in run_record.cpp for the four defects that have been removed
// since, and for what that evidence does and does not establish.
void mods_run_record_capture_payload(const char *dir);

} // extern "C"

#ifdef __cplusplus
#include <string>
#include <vector>
// Module-internal helpers with C++ types. Not part of any cross-language
// boundary, never referenced from the runtime seam.
bool mods_cpp_overlay_resolve(const std::string &relative, int op, std::string *out_host);
void mods_cpp_overlay_list(const std::string &relative_dir,
                           std::vector<std::pair<std::string, std::string>> *out);
// Test85: the values a settings-menu Apply replaces ("mod_id/key", value), for last-known-good.
void mods_cpp_settings_mark_pending(const std::vector<std::pair<std::string, int64_t>> &previous);
#endif
