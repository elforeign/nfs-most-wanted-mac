/* pop_mod_api.h - the mod C API for the recompiled Populous: The Beginning.
 *
 * C only, macOS arm64, fixed-width types, C linkage. A mod includes exactly
 * this header and nothing else from the repository: nothing in the API is a
 * C++ symbol and no call reaches an internal function.
 *
 * VERSIONING. PopModApi carries version/size and is append-only: a plugin
 * built against an older header reads only the fields inside its own compiled
 * size, which it declares through POP_MOD_DECLARE_ABI(). Guest CPU state is
 * versioned separately as pop_cpu_v1, so a CPU-layout change ships as
 * pop_cpu_v2 without bumping PopModApi.version.
 *
 * ATTRIBUTION. Every function pointer takes the PopModApi it was handed as its
 * first argument. Each mod gets its own instance, whose address and mod_id are
 * stable for the life of the process.
 *
 * OWNERSHIP. guest_alloc returns mod-owned memory in a heap region separate
 * from the game's, which the mod must guest_free; the game view is loaned for
 * the callback's duration; API-returned strings are owned by the API and valid
 * until the next call on the same thread.
 */
#ifndef POP_MOD_API_H
#define POP_MOD_API_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define POP_MOD_API_VERSION 1

typedef int32_t PopModStatus;

#define POP_OK 0
#define POP_E_NOSYMBOL (-1)     /* not an eligible entry symbol */
#define POP_E_CONFLICT (-2)     /* a replace hook is already installed */
#define POP_E_REENTRY (-3)      /* the base would run twice in one call */
#define POP_E_LIMIT (-4)        /* handle space or a fixed buffer exhausted */
#define POP_E_STATE (-5)        /* not valid at this point in the lifecycle */
#define POP_E_WRONG_THREAD (-6) /* host-only API called off the main thread */
#define POP_E_RANGE (-7)        /* guest address, index or value out of range */
#define POP_E_NOMEM (-8)
#define POP_E_INVAL (-9)
#define POP_E_NOTFOUND (-10)
#define POP_E_ABI (-11) /* the plugin's declared ABI is unusable */

/* ------------------------------------------------------------------ CPU -- */

#define POP_PHASE_BEFORE 0
#define POP_PHASE_REPLACE 1
#define POP_PHASE_AFTER 2

/* Guest CPU state as a hook sees it.
 *
 * FLAGS ARE AS OBSERVED and may be stale for CF/ZF/SF/OF/PF/AF: the translator
 * keeps a flag only where per-function liveness found a later read. DF and the
 * FPU fields are always live. A hook needing exact, always-fresh flags is
 * unsupported in v1.
 *
 * eip is the callee ENTRY ADDRESS for before/replace, normalised by the host;
 * for after it is the caller's return address, because the guest RET has
 * already run - target identifies the hooked function once eip has moved on.
 *
 * before/replace callbacks may edit any field and every field inside `size` is
 * copied back. after callbacks may edit only eax/edx. Memory edits through
 * guest_read/write apply directly in every phase. `size` is set by the host
 * from the size the plugin declared; a callback must not enlarge it. */
typedef struct pop_cpu_v1 {
    uint32_t size;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi;
    uint32_t eip;
    uint32_t target;
    uint32_t phase;
    uint32_t cf, zf, sf, of, pf, af, df;
    double st[8];
    uint32_t fpu_top;
    uint16_t fpu_cw, fpu_sw, fpu_tag;
    uint16_t reserved0;
} pop_cpu_v1;

#define POP_CPU_V1_BASELINE_SIZE ((uint32_t)sizeof(pop_cpu_v1))
#define POP_CPU_V1_MIN_SIZE ((uint32_t)(offsetof(pop_cpu_v1, edi) + sizeof(uint32_t)))

static inline void pop_cpu_v1_init(pop_cpu_v1 *c) {
    unsigned char *p = (unsigned char *)c;
    uint32_t i;
    for (i = 0; i < (uint32_t)sizeof(pop_cpu_v1); ++i)
        p[i] = 0;
    c->size = (uint32_t)sizeof(pop_cpu_v1);
}

/* ------------------------------------------------------------- callbacks -- */

typedef struct PopModApi PopModApi;
typedef struct PopHookInvocation PopHookInvocation;

#define POP_HOOK_BEFORE 0
#define POP_HOOK_AFTER 1
#define POP_HOOK_REPLACE 2
#define POP_HOOK_WRAP 3

/* Explicit opt-out for callbacks that use only CPU/guest/host services. */
#define POP_HOOK_NO_GAME_VIEW 1u

#define POP_EVENT_BEFORE 0
#define POP_EVENT_AFTER 1

typedef void (*PopHookFn)(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv,
                          void *user);
typedef void (*PopEventFn)(const PopModApi *api, void *user);
/* Non-zero consumes the input: the guest sees it through none of DirectInput's
 * immediate state, DirectInput's buffered data, a posted Win32 message or
 * GetAsyncKeyState. A press the host consumed also consumes its repeats and
 * its release, so the guest never sees a key it believes is still down. */
typedef int32_t (*PopKeyFn)(const PopModApi *api, int32_t dik, int32_t vk, int32_t down,
                            void *user);
typedef int32_t (*PopMouseFn)(const PopModApi *api, int32_t x, int32_t y, int32_t dx, int32_t dy,
                              int32_t buttons, int32_t wheel, void *user);
typedef void (*PopMenuFn)(const PopModApi *api, void *user);
/* Invoked on the host thread at texture upload. `hash64` is a content hash of
 * the texture's pixels, palette and dimensions - stable across runs and across
 * DirectDraw handle reuse. Non-zero means an override was produced:
 * *out_rgba8 points at *out_bytes >= w*h*4 bytes in R,G,B,A byte order, owned
 * by the mod until this callback's next invocation. */
typedef int32_t (*PopTextureProviderFn)(const PopModApi *api, uint64_t hash64, int32_t w, int32_t h,
                                        int32_t format, uint8_t **out_rgba8, uint32_t *out_bytes,
                                        void *user);

/* Optional v1 tail: RGBA8 replacements may have different dimensions.
 * Normalized UVs are preserved; keep the original layout and aspect ratio.
 * Straight (not premultiplied) alpha. Storage remains owned by the provider
 * until its next invocation. A row may be padded; bytes includes all rows.
 * Maximum dimension 4096, maximum storage 64 MiB. size must be initialized. */
typedef struct PopTextureReplacement {
    uint32_t size;
    int32_t width, height, pitch;
    const uint8_t *pixels;
    uint64_t bytes;
} PopTextureReplacement;
typedef int32_t (*PopTextureProviderExFn)(const PopModApi *api, uint64_t hash64, int32_t w,
                                          int32_t h, int32_t format, PopTextureReplacement *out,
                                          void *user);

/* ------------------------------------------------------------ game view -- */

/* An immutable snapshot taken at the callback's entry and valid only until
 * that callback returns. `slot` is the PHYSICAL slot index, which is also the
 * index entity() takes; `id` is the entity's own id, a different number. */
typedef struct PopEntityView {
    uint32_t size;
    uint32_t slot;
    uint32_t guest_addr;
    uint32_t flags, render_flags, motion_flags, animation_tick;
    uint16_t id, angle;
    uint8_t kind, model, state, state_2, class_counter, owner, index, counter, counter_2;
    int32_t x, z, altitude;
    int32_t dx, dz, daltitude;
    uint8_t raw[179];
} PopEntityView;

/* One of the four records at tribe_base. `raw` is a snapshot copy the
 * callback owns for its duration; the named fields are the ones this
 * repository's own oracle probes already decode. */
typedef struct PopTribeView {
    uint32_t size;
    uint32_t index;
    uint32_t guest_addr;
    uint32_t bytes;   /* 0xc65 */
    uint8_t type;     /* +0xc1f: 1 = computer */
    uint8_t active;   /* +0xc20 */
    uint8_t tribe_id; /* +0xc22 */
    uint8_t reserved0;
    uint32_t control_flags; /* +0x596 */
    const uint8_t *raw;
} PopTribeView;

#define POP_SETTING_BOOL 0
#define POP_SETTING_INT 1

typedef struct PopSettingDesc {
    uint32_t size;
    const char *key;
    const char *label;
    int32_t kind;
    int64_t def, min, max;
} PopSettingDesc;

/* --------------------------------------------------------------- the API -- */

struct PopModApi {
    uint32_t version;
    uint32_t size;
    uint32_t mod_index;
    uint32_t reserved0;
    const char *mod_id;

    /* identity and logging */
    PopModStatus (*log)(const PopModApi *api, const char *text);
    const char *(*mod_dir)(const PopModApi *api);
    PopModStatus (*settings_get)(const PopModApi *api, const char *key, int64_t *out);
    PopModStatus (*settings_set)(const PopModApi *api, const char *key, int64_t v);

    /* hooks */
    PopModStatus (*hook_install)(const PopModApi *api, uint32_t addr, PopHookFn fn, int32_t mode,
                                 void *user, uint32_t *out_id);
    PopModStatus (*hook_remove)(const PopModApi *api, uint32_t id);
    PopModStatus (*call_original)(const PopModApi *api, uint32_t addr, pop_cpu_v1 *cpu);
    PopModStatus (*call_next)(const PopModApi *api, PopHookInvocation *inv, pop_cpu_v1 *cpu);
    PopModStatus (*hook_return)(const PopModApi *api, pop_cpu_v1 *cpu, uint32_t eax,
                                uint32_t arg_bytes);
    PopModStatus (*symbol)(const PopModApi *api, const char *name, uint32_t *out_addr);
    PopModStatus (*symbols_matching)(const PopModApi *api, const char *prefix, uint32_t *out_addrs,
                                     uint32_t cap, uint32_t *out_count);

    /* guest memory */
    PopModStatus (*guest_ptr)(const PopModApi *api, uint32_t addr, uint32_t len, void **out);
    PopModStatus (*guest_read_u8)(const PopModApi *api, uint32_t a, uint8_t *v);
    PopModStatus (*guest_read_u16)(const PopModApi *api, uint32_t a, uint16_t *v);
    PopModStatus (*guest_read_u32)(const PopModApi *api, uint32_t a, uint32_t *v);
    PopModStatus (*guest_write_u8)(const PopModApi *api, uint32_t a, uint8_t v);
    PopModStatus (*guest_write_u16)(const PopModApi *api, uint32_t a, uint16_t v);
    PopModStatus (*guest_write_u32)(const PopModApi *api, uint32_t a, uint32_t v);
    PopModStatus (*guest_alloc)(const PopModApi *api, uint32_t size, uint32_t *out_addr);
    PopModStatus (*guest_free)(const PopModApi *api, uint32_t addr);

    /* game view, read-only */
    uint32_t (*entity_count)(const PopModApi *api);
    /* nth allocated slot, in ascending slot order, for iteration. */
    PopModStatus (*entity_slot)(const PopModApi *api, uint32_t nth, uint32_t *out_slot);
    /* `slot` is the physical slot index; POP_E_NOTFOUND when it is not
     * allocated in this callback's snapshot. */
    PopModStatus (*entity)(const PopModApi *api, uint32_t slot, PopEntityView *out);
    PopModStatus (*tribe)(const PopModApi *api, uint32_t i, PopTribeView *out);

    /* events */
    PopModStatus (*on_frame)(const PopModApi *api, int32_t phase, PopEventFn fn, void *user,
                             uint32_t *out_id);
    PopModStatus (*on_turn)(const PopModApi *api, int32_t phase, PopEventFn fn, void *user,
                            uint32_t *out_id);
    PopModStatus (*on_level_load)(const PopModApi *api, PopEventFn fn, void *user,
                                  uint32_t *out_id);
    PopModStatus (*on_level_end)(const PopModApi *api, PopEventFn fn, void *user, uint32_t *out_id);
    PopModStatus (*on_key)(const PopModApi *api, PopKeyFn fn, void *user, uint32_t *out_id);
    PopModStatus (*on_mouse)(const PopModApi *api, PopMouseFn fn, void *user, uint32_t *out_id);

    /* host services: main thread only, POP_E_WRONG_THREAD off it */
    PopModStatus (*register_menu_item)(const PopModApi *api, const char *path, const char *label,
                                       PopMenuFn cb, void *user);
    PopModStatus (*register_setting)(const PopModApi *api, const PopSettingDesc *desc);
    PopModStatus (*texture_override_provider)(const PopModApi *api, PopTextureProviderFn cb,
                                              void *user);
    PopModStatus (*overlay_push)(const PopModApi *api, const char *dir, uint32_t *out_id);
    /* Opens the foundation's settings page over the presented frame. `mod_id`
     * null or "" shows every mod's settings. This is the public contract a
     * menu callback uses; no mod calls an internal function. */
    PopModStatus (*open_settings_page)(const PopModApi *api, const char *mod_id);

    /* Display services, appended in v1. Anchors: -1 left/top, 0 centre,
     * 1 right/bottom. Mutators are main-thread only; cursors ignore anchors. */
    PopModStatus (*set_anchor)(const PopModApi *api, uint64_t element_id, int8_t h, int8_t v);
    PopModStatus (*clear_anchor)(const PopModApi *api, uint64_t element_id);
    /* Total count; copies at most max ids. null ids is a count query. */
    uint32_t (*ui_elements)(const PopModApi *api, uint64_t *ids, uint32_t max);
    float (*host_aspect)(const PopModApi *api); /* drawable w/h, or 4/3 */
    PopModStatus (*display_transition)(const PopModApi *api, uint32_t *out_epoch);
    /* Logical scene canvas, including the HUD origin. Guest-thread only.
     * Publish with projection changes so clipping and inverse input agree. */
    PopModStatus (*set_scene_domain)(const PopModApi *api, uint32_t width, uint32_t height);
    /* Optional v1 tail. Like hook_install, but only for an exact nonzero
     * return PC read from the guest stack at function entry. The filter is
     * captured before any BEFORE callback, including for AFTER/WRAP hooks.
     * Nonmatching calls take no callback snapshot. Matching calls retain the
     * normal snapshot, ordering, removal, rollback and unwind contracts. */
    PopModStatus (*hook_install_at_callsite)(const PopModApi *api, uint32_t addr,
                                             uint32_t return_pc, PopHookFn fn, int32_t mode,
                                             void *user, uint32_t *out_id);
    /* Optional v1 tail. return_pc=0 disables filtering; flags=0 preserves
     * normal snapshots. NO_GAME_VIEW hides even an enclosing callback's view:
     * entity_count=0 and entity/slot/tribe return POP_E_STATE in this callback.
     * Nested ordinary hooks still capture their own immutable entry view.
     * Unknown flags are rejected. All ordering/unwind/removal rules apply. */
    PopModStatus (*hook_install_ex)(const PopModApi *api, uint32_t addr, uint32_t return_pc,
                                    PopHookFn fn, int32_t mode, uint32_t flags, void *user,
                                    uint32_t *out_id);
    /* Size-gated optional tail; original providers continue to work. */
    PopModStatus (*texture_override_provider_ex)(const PopModApi *api, PopTextureProviderExFn cb,
                                                 void *user);
    /* Optional v1 tail. Only inside a hook callback: runs the guest function
     * at `addr` (a listed function) with ECX = `ecx` and `nargs` dword
     * arguments on a scratch stack below the hooked frame, and returns its
     * EAX. Every register and the x87 state are restored afterwards, so
     * cdecl, stdcall and thiscall all work. Guest memory it writes stays
     * written. Allocated import trampolines (for example an IAT entry) are
     * also accepted; unallocated trampoline addresses are rejected. */
    PopModStatus (*guest_call)(const PopModApi *api, uint32_t addr, uint32_t ecx,
                               const uint32_t *args, uint32_t nargs, uint32_t *out_eax);
    /* Optional v1 tail. The screen the window is on, in pixels
     * (POP_E_NOTFOUND before the host knows it). */
    PopModStatus (*screen_size)(const PopModApi *api, uint32_t *w, uint32_t *h);
    /* Optional v1 tail. Thread-safe finite controller pulse, strengths
     * 0..65535 and duration 1..5000ms. Zero duration or both strengths zero
     * cancels this mod's pulse. Independent owners mix by maximum; teardown
     * revokes the owner's pulse. Does not replace persistent XInput output.
     * Acceptance means queued, not proof of a connected motor's response. */
    PopModStatus (*rumble_pulse)(const PopModApi *api, uint16_t low, uint16_t high, uint32_t ms);
    /* Optional v1 tail. How the host presents the game's mix and its movie
     * audio: POP_AUDIO_OUTPUT_* mode, plus flags. MIXER (0) keeps the mixer's
     * own layout. MONO/STEREO fold a 5.1 mix down (ITU coefficients, LFE
     * omitted); SURROUND51 keeps discrete channels where the device has them.
     * The game mix changes at the next output period; a movie uses the value
     * current when it opens. Thread-safe; any thread. */
    PopModStatus (*audio_output)(const PopModApi *api, uint32_t mode, uint32_t flags);
    /* Optional v1 tail (NFSMW Remaster A1). Gives the Direct3D 9 2D texture behind `tex_iface` (an
     * IDirect3DTexture9 interface pointer the guest holds) an opaque 64-bit key the host uses for texture
     * dumps and replacement. The pointer is resolved to the device's own object id at once; no guest address
     * is kept. Guest thread only. POP_E_NOTFOUND when it is not a live 2D texture of this device. */
    PopModStatus (*d3d9_texture_tag)(const PopModApi *api, uint32_t tex_iface, uint64_t key);
    /* Optional v1 tail (NFSU1 run 9). Where the game draws its menus inside its own frame, as fractions of the frame
     * (x0, y0, w, h; 0,0,1,1 = the whole frame, the default): the host maps the pointer into that box before it
     * places the game's cursor, so a 4:3 menu centred in a wide frame follows the mouse. Any thread.
     * POP_E_INVAL for a box outside the frame. */
    PopModStatus (*pointer_box)(const PopModApi *api, float x0, float y0, float w, float h);
};

#define POP_AUDIO_OUTPUT_MIXER 0u
#define POP_AUDIO_OUTPUT_MONO 1u
#define POP_AUDIO_OUTPUT_STEREO 2u
#define POP_AUDIO_OUTPUT_SURROUND51 3u
/* Discrete output only: exchange the centre and LFE channels, for an endpoint
 * chain verified (by a listening test) to swap them after the OS layout. */
#define POP_AUDIO_SWAP_CENTER_LFE 1u
/* Send the mix as a Dolby Digital 5.1 bitstream (AC-3 over HDMI/optical)
 * where the device accepts one, as the Xbox 360 does; roles then travel in the
 * bitstream, so SWAP_CENTER_LFE does not apply. Movies join that mix. The
 * output reopens when this flag changes; PCM is used whenever it cannot. */
#define POP_AUDIO_DOLBY_DIGITAL 2u

/* ---------------------------------------------------------- plugin ABI --- */

typedef struct PopModAbi {
    uint32_t abi_struct_size; /* sizeof(PopModAbi) */
    uint32_t api_version;     /* POP_MOD_API_VERSION at build time */
    uint32_t api_size;        /* sizeof(PopModApi) at build time */
    uint32_t cpu_size;        /* sizeof(pop_cpu_v1) at build time */
} PopModAbi;

/* A plugin's exported symbols: COFF needs dllexport for GetProcAddress to see
 * them; ELF and Mach-O need default visibility when built with -fvisibility=hidden. */
#if defined(_WIN32)
#define POP_MOD_EXPORT __declspec(dllexport)
#else
#define POP_MOD_EXPORT __attribute__((visibility("default")))
#endif

/* Exported by every plugin. The host validates it and refuses the plugin with
 * POP_E_ABI when it is missing or unusable; every copy-back is bounded by the
 * smaller of the host's size and the plugin's declared size. */
#define POP_MOD_DECLARE_ABI()                                                                      \
    POP_MOD_EXPORT const PopModAbi pop_mod_abi = {                                                 \
        (uint32_t)sizeof(PopModAbi), POP_MOD_API_VERSION, (uint32_t)sizeof(PopModApi),             \
        (uint32_t)sizeof(pop_cpu_v1)}

extern POP_MOD_EXPORT const PopModAbi pop_mod_abi;

POP_MOD_EXPORT PopModStatus pop_mod_init(const PopModApi *api);
POP_MOD_EXPORT PopModStatus pop_mod_exit(void);

#ifdef __cplusplus
}
#endif
#endif /* POP_MOD_API_H */
