#include "blackbox_render.h"
#include <stdatomic.h>
#include <string.h>

extern uint64_t d3d9_draw_count(void);
extern uint64_t render_deferred_count(void);
extern uint64_t render_failure_count(void);
extern uint64_t frame_serial;

/* Only the guest thread changes pass state. Callback producers use the core's
 * independently published presentation ring, never this accumulator. */
static BlackboxRenderFrame current;
static uint64_t begin_draws, frame_draws, deferred, failed;
static uint16_t active;
#define PASS_IDS 256u
/* Immutable numeric identity words, published last through release/acquire. No
 * arbitrary effect strings enter reports. Saturation retains unknown ID zero. */
static struct { _Atomic uint64_t identity; _Atomic uint32_t pass; } ids[PASS_IDS];
static unsigned nids;
static _Atomic uint64_t dictionary_losses;
static uint32_t hash(const char *s) {
    uint32_t h = 2166136261u;
    for (unsigned i = 0; s && i < 64 && s[i]; ++i) h = (h ^ (unsigned char)s[i]) * 16777619u;
    return h;
}
uint16_t blackbox_render_pass_id(const char *effect, const char *technique, const char *pass) {
    if (!blackbox_enabled()) return 0;
    uint64_t identity = (uint64_t)hash(effect) << 32 | hash(technique);
    uint32_t p = hash(pass);
    for (unsigned i = 0; i < nids; ++i)
        if (atomic_load_explicit(&ids[i].identity, memory_order_relaxed) == identity &&
            atomic_load_explicit(&ids[i].pass, memory_order_relaxed) == p) return (uint16_t)(i + 1);
    if (nids == PASS_IDS) {
        atomic_fetch_add_explicit(&dictionary_losses, 1, memory_order_relaxed);
        return 0;
    }
    unsigned i = nids++;
    atomic_store_explicit(&ids[i].pass, p, memory_order_relaxed);
    atomic_store_explicit(&ids[i].identity, identity, memory_order_release);
    return (uint16_t)(i + 1);
}
static void close_pass(uint64_t draws) {
    uint32_t delta = (uint32_t)(draws - begin_draws);
    if (!delta) return;
    if (!active) { current.unattributed += delta; return; }
    for (unsigned i = 0; i < current.count; ++i) if (current.pass[i].id == active) {
        current.pass[i].draws += delta; return;
    }
    if (current.count == 32) { current.overflow += delta; return; }
    current.pass[current.count++] = (BlackboxPass){.id = active, .draws = delta};
}
void blackbox_render_pass_begin(uint16_t id) {
    if (!blackbox_enabled()) return;
    uint64_t draws = d3d9_draw_count();
    close_pass(draws); begin_draws = draws; active = id;
}
void blackbox_render_pass_end(void) {
    if (!blackbox_enabled()) return;
    uint64_t draws = d3d9_draw_count();
    close_pass(draws); begin_draws = draws; active = 0;
}
int blackbox_render_take(BlackboxRenderFrame *out) {
    if (!blackbox_enabled()) return 0;
    uint64_t draws = d3d9_draw_count(), d = render_deferred_count(), f = render_failure_count();
    close_pass(draws); begin_draws = draws;
    current.serial = frame_serial;
    current.attempted = (uint32_t)(draws - frame_draws);
    current.deferred = (uint32_t)(d - deferred);
    current.failed = (uint32_t)(f - failed);
    *out = current;
    memset(&current, 0, sizeof current);
    frame_draws = draws; deferred = d; failed = f;
    return 1;
}
static const char *effect_label(uint32_t key) {
    static const char *const retail[] = {
        "IDI_WORLD_FX", "IDI_CAR_FX", "IDI_PARTICLES_FX", "IDI_TREE_FX",
        "IDI_WORLDBONE_FX", "IDI_GLOSSYWINDOW_FX"
    };
    for (unsigned i = 0; i < sizeof retail / sizeof *retail; ++i)
        if (hash(retail[i]) == key) return retail[i];
    return "<unknown>";
}
void blackbox_render_definitions(FILE *f) {
    fprintf(f, "AVAILABLE passes async_present\n");
    fprintf(f, "PASS_DICTIONARY capacity=256 lost=%llu\n", (unsigned long long)atomic_load_explicit(&dictionary_losses, memory_order_relaxed));
    fprintf(f, "PASS_IDS 0=unattributed; effect/technique/pass are bounded numeric hashes; limit=32 proposed\n");
    for (unsigned i = 0; i < PASS_IDS; ++i) {
        uint64_t key = atomic_load_explicit(&ids[i].identity, memory_order_acquire);
        if (!key) continue;
        fprintf(f, "PASS_ID %u effect=%s effect_hash=%08X technique_hash=%08X pass_hash=%08X\n", i + 1,
                effect_label((uint32_t)(key >> 32)), (unsigned)(key >> 32), (unsigned)key, atomic_load_explicit(&ids[i].pass, memory_order_relaxed));
    }
    fprintf(f, "HOST_IDS 1=post 2=copy 3=resolve 4=final 5=colourfilter 6=crop 7=ssao 8=luminance 9=treatment 10=lens 11=smoke 12=depth 13=quad 14=gamma 15=hdr 16=coverage 17=motion; post detail is HC stage; host encoders are separate from attempted D3D draws\n");
    fprintf(f, "PASS_GPU unavailable; optional BOOT_DIAG_GPUPASS supplies per-encoder timing separately\n");
}
void blackbox_render_host_stage(uint64_t serial, uint32_t id, uint32_t detail) {
    blackbox_record_present(serial, BB_HOST_ENCODER, 0, id, detail, 0);
}
void blackbox_render_host(uint64_t serial, uint32_t id) {
    blackbox_record_present(serial, BB_HOST_ENCODER, 0, id, 0, 0);
}
void blackbox_render_present(uint64_t serial, uint32_t stage, uint32_t status,
                             double start, double end, double shown) {
    if (!blackbox_enabled()) return;
    blackbox_record_present(serial, stage, status, start > 0 ? (uint64_t)(start * 1e9) : 0,
                           end > 0 ? (uint64_t)(end * 1e9) : 0, shown > 0 ? (uint64_t)(shown * 1e9) : 0);
}

void blackbox_render_complete(uint64_t serial, uint32_t stage, uint32_t status,
                              double start, double end, int64_t error) {
    if (!blackbox_enabled()) return;
    blackbox_record_present(serial, stage, status, start > 0 ? (uint64_t)(start * 1e9) : 0,
                           end > 0 ? (uint64_t)(end * 1e9) : 0, (uint64_t)error);
}

#ifdef __OBJC__
/* The ordinary final completion uses a global block: serial capture does not
 * allocate a fresh block per frame. The presenter alone reserves these tickets;
 * completion threads release them. Full tickets drop rather than wait. */
static struct { _Atomic uintptr_t key; _Atomic uint64_t serial; } final_tickets[32];
_Static_assert(sizeof final_tickets + sizeof ids + sizeof current < 16384, "renderer state budget");
void blackbox_render_final(id<MTLCommandBuffer> buffer, uint64_t serial) {
    if (!blackbox_enabled()) return;
    uintptr_t key = (uintptr_t)(__bridge void *)buffer;
    for (unsigned i = 0; i < 32; ++i) {
        uintptr_t expected = 0;
        if (!atomic_compare_exchange_strong_explicit(&final_tickets[i].key, &expected, 1,
                                                     memory_order_acquire, memory_order_relaxed)) continue;
        atomic_store_explicit(&final_tickets[i].serial, serial, memory_order_relaxed);
        atomic_store_explicit(&final_tickets[i].key, key, memory_order_release);
        [buffer addCompletedHandler:^(id<MTLCommandBuffer> completed) {
            uintptr_t completed_key = (uintptr_t)(__bridge void *)completed;
            for (unsigned j = 0; j < 32; ++j) {
                if (atomic_load_explicit(&final_tickets[j].key, memory_order_acquire) != completed_key) continue;
                uint64_t original = atomic_load_explicit(&final_tickets[j].serial, memory_order_relaxed);
                blackbox_render_complete(original, BB_PRESENT_GPU, (uint32_t)completed.status,
                                         completed.GPUStartTime, completed.GPUEndTime, completed.error.code);
                atomic_store_explicit(&final_tickets[j].key, 0, memory_order_release);
                return;
            }
        }];
        return;
    }
    blackbox_record_present(serial, BB_PRESENT_TICKET_LOST, 1, 0, 0, 0);
}
#endif
