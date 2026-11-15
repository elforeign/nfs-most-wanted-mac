// Rendered-frame state and real guest adapters, run by temporal_state_test.py.
// Proof counts for production and disabled-invalidation control are in the driver.
#include "host.h"
#include "temporal_state.h"
#include <stdlib.h>
#include <math.h>

static unsigned checks, failures, original_calls, constructor_calls;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures < 30) fprintf(stderr, "FAIL %d: %s\n", __LINE__, #x); } } while (0)
uint8_t *G;
static int split;
static uint32_t cur_view;
static unsigned nadditions, traffic_active;
static TemporalMatrices matrices;
static TemporalViewKey key;
static uint64_t presented;
int diag_on;
void diag_event(const char *fmt, ...) { (void)fmt; }
void split_capture_reset(Cpu *c) { (void)c; }
void split_post_restart(void) {}
int split_view_rect(uint32_t v, uint32_t r[4]) {
    if (!split || (v != 0x919650u && v != 0x9196C0u)) return 0;
    if (r) { r[0] = 0; r[1] = v == 0x9196C0u ? 360 : 0; r[2] = 1280; r[3] = v == 0x9196C0u ? 361 : 360; }
    return 1;
}
void f_004700D0_orig(Cpu *c) { if (G) M8(c->ecx + 0x2A0) = 0; ++original_calls; c->eax = 0x123; c->esp += 12; }
#define ORIGINAL(fn) void fn(Cpu *c) { ++original_calls; c->eax = 0x123; c->esp += 4; }
ORIGINAL(f_00502980_orig)
ORIGINAL(f_005029B0_orig)
ORIGINAL(f_00470A10_orig)
ORIGINAL(f_007556C0_orig)
ORIGINAL(f_006FCA30_orig)
ORIGINAL(f_006FBF20_orig)
ORIGINAL(f_006F8200_orig)
ORIGINAL(f_006308E0_orig)
void f_0075F5D0_orig(Cpu *c) { ++constructor_calls; c->eax = c->ecx; c->ecx = 0xBAD; c->esp += 8; }
static void model_0075F5D0(Cpu *c) { f_0075F5D0_orig(c); }
void f_006CF400_orig(Cpu *c) {
    uint32_t platform = c->ecx;
    memcpy(G + platform, matrices.view, 64);
    memcpy(G + platform + 0x40, matrices.projection, 64);
    memcpy(G + platform + 0x80, matrices.vp, 64);
    // This models the completed Hor+ projection; capture must run after it.
    MF32(platform + 0x40) *= 0.75f;
    c->ecx = 0xBAD; c->eax = 0x123; c->esp += 24;
}
#include "temporal_hooks.inc"
void f_004700D0(Cpu *c);
void f_00502980(Cpu *c);
void f_005029B0(Cpu *c);
void f_00470A10(Cpu *c);
void f_007556C0(Cpu *c);
void f_006FCA30(Cpu *c);
void f_006FBF20(Cpu *c);
void f_006F8200(Cpu *c);

static void identity(float m[16]) { memset(m, 0, 64); for (unsigned i = 0; i < 4; ++i) m[i * 5] = 1; }
static void fresh(void) {
    temporal_state_request(0); temporal_state_request(1);
    temporal_scene_mode(0, 6, 0); presented = 0; split = 0;
    memset(&matrices, 0, sizeof matrices);
    identity(matrices.view); identity(matrices.projection);
    matrices.projection[10] = 1000.f / 999.f;
    matrices.projection[11] = 1;
    matrices.projection[14] = -1000.f / 999.f;
    matrices.projection[15] = 0;
    memcpy(matrices.vp, matrices.projection, 64);
    matrices.near_z = 1; matrices.far_z = 1000;
    key = (TemporalViewKey){.source = 1, .depth = 2, .generation = 3, .player_epoch = 1,
        .rect = {0, 0, 1280, 721}, .width = 1280, .height = 721, .samples = 4,
        .format = 80, .domain = 1, .player = 0x30004C, .camera = 0x310000, .mover = 0x320000};
}
static const TemporalViewState *accept(TemporalViewId id) {
    temporal_view_capture(id, &matrices, key.player, key.camera, key.mover);
    const TemporalViewState *s = temporal_view_accept(id, &key);
    CHECK(s != NULL); return s;
}
static void next(void) { temporal_frame_boundary(++presented, 6, 0); }
static void warm(void) { fresh(); accept(TEMPORAL_SP); next(); CHECK(accept(TEMPORAL_SP)->history_valid); }
static void reset_proof(unsigned reason) {
    CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    next(); const TemporalViewState *s = accept(TEMPORAL_SP);
    CHECK(!s->history_valid); CHECK(s->jitter_index == 0);
    CHECK(s->reset_reasons & reason);
    next(); CHECK(accept(TEMPORAL_SP)->history_valid);
}
static Cpu cpu(uint32_t object) { return (Cpu){.ecx = object, .esp = 0x700000, .ebx = 7, .esi = 8, .edi = 9}; }
static void call_hook(void (*hook)(Cpu *), uint32_t object) {
    Cpu c = cpu(object); unsigned before = original_calls;
    hook(&c); CHECK(original_calls == before + 1); CHECK(c.eax == 0x123);
    CHECK(c.ebx == 7 && c.esi == 8 && c.edi == 9);
}
static void lifecycle(void) {
    warm(); M8(key.camera + 0x2A0) = 1;
    call_hook(f_004700D0, key.camera); CHECK(M8(key.camera + 0x2A0) == 0);
    reset_proof(TEMPORAL_RESET_CUT); // exact same Camera and matrices
    warm(); call_hook(f_004700D0, key.camera); CHECK(temporal_view_state(TEMPORAL_SP)->history_valid);
    warm(); M32(0x700004) = key.mover; call_hook(f_00502980, 0x919650); reset_proof(TEMPORAL_RESET_MOVER);
    warm(); call_hook(f_005029B0, 0x919650); reset_proof(TEMPORAL_RESET_MOVER);
    warm(); call_hook(f_00470A10, key.mover); reset_proof(TEMPORAL_RESET_MOVER);
    warm(); call_hook(f_006FCA30, key.player - 0x4C); reset_proof(TEMPORAL_RESET_PLAYER);
    warm(); call_hook(f_006FBF20, key.player - 0x14); reset_proof(TEMPORAL_RESET_PLAYER);
    warm(); call_hook(f_006F8200, key.player - 0x14); reset_proof(TEMPORAL_RESET_PLAYER);
    warm(); call_hook(f_006308E0, 0); reset_proof(TEMPORAL_RESET_PLAYER); // SP restart with all identities unchanged
    warm(); temporal_invalidate(TEMPORAL_RESET_TARGET); reset_proof(TEMPORAL_RESET_TARGET); // device reset
    warm(); temporal_scene_mode(0, 6, 1); CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    next(); CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); // missing setup stays rejected
    CHECK(!accept(TEMPORAL_SP)->history_valid); next(); CHECK(accept(TEMPORAL_SP)->history_valid);
    temporal_scene_mode(0, 3, 0); CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    next(); CHECK(!accept(TEMPORAL_SP)->history_valid);
    warm(); next(); key.player += 0x1000; CHECK(!accept(TEMPORAL_SP)->history_valid);
    warm(); next(); ++key.player_epoch; CHECK(!accept(TEMPORAL_SP)->history_valid);
    warm(); next(); ++key.camera; CHECK(!accept(TEMPORAL_SP)->history_valid);
    warm(); next(); ++key.mover; CHECK(!accept(TEMPORAL_SP)->history_valid);
}
static void frames(void) {
    fresh(); const TemporalViewState *s = accept(TEMPORAL_SP);
    CHECK(!s->history_valid && s->current.serial == 1 && s->current.frame == 1);
    TemporalMatrices first = matrices;
    CHECK(!memcmp(&s->current.matrices, &first, sizeof first));
    matrices.view[12] = 2;
    CHECK(accept(TEMPORAL_SP)->current.matrices.view[12] == 0); // setup/replay cannot overwrite frozen draw
    CHECK(s->current.serial == 1 && s->jitter_index == 0);
    matrices.projection[0] = 0.95f; matrices.vp[12] = 3;
    next(); CHECK(accept(TEMPORAL_SP)->history_valid);
    CHECK(!memcmp(&s->current.matrices, &matrices, sizeof matrices));
    CHECK(!memcmp(&s->previous.matrices, &first, sizeof first));
    CHECK(s->current.matrices.view[12] == 2 && s->previous.matrices.view[12] == 0);
    CHECK(s->previous.serial == 1 && s->current.serial == 2 && s->jitter_index == 1);
    for (unsigned i = 0; i < 10; ++i) { next(); accept(TEMPORAL_SP); CHECK(s->jitter_index == (i + 2) % 8); }
    warm(); temporal_frame_boundary(presented, 6, 0); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    next(); CHECK(!accept(TEMPORAL_SP)->history_valid); // duplicate boundary never reuses old history
    warm(); presented += 2; temporal_frame_boundary(presented, 6, 0); CHECK(!accept(TEMPORAL_SP)->history_valid);
    warm(); next(); next(); CHECK(!accept(TEMPORAL_SP)->history_valid); // one view missed a whole frame
    warm(); next(); CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); // never use a stale setup
    temporal_view_capture(TEMPORAL_SP, NULL, key.player, key.camera, key.mover);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
}
static void targets(void) {
    // Each structural field changes separately after a valid preceding render.
#define CHANGE(expr) do { warm(); next(); expr; CHECK(!accept(TEMPORAL_SP)->history_valid); } while (0)
    CHANGE(++key.source); CHANGE(++key.depth); CHANGE(++key.generation);
    CHANGE(--key.rect[2]); CHANGE(--key.rect[3]);
    CHANGE(key.rect[0] = 1; --key.rect[2]); CHANGE(key.rect[1] = 1; --key.rect[3]);
    CHANGE(++key.width); CHANGE(++key.height); CHANGE(key.samples = 2);
    CHANGE(++key.format); CHANGE(++key.domain);
    CHANGE(matrices.near_z = 2); CHANGE(matrices.far_z = 900);
    CHANGE(matrices.view[12] = 100); CHANGE(matrices.view[0] = -1; matrices.view[10] = -1);
    CHANGE(matrices.projection[0] = 2);
#undef CHANGE
    warm(); next(); matrices.view[3] = NAN;
    temporal_view_capture(TEMPORAL_SP, &matrices, key.player, key.camera, key.mover);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    warm(); next(); memset(matrices.vp, 0, 64);
    temporal_view_capture(TEMPORAL_SP, &matrices, key.player, key.camera, key.mover);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    warm(); next(); key.rect[2] = UINT32_MAX;
    temporal_view_capture(TEMPORAL_SP, &matrices, key.player, key.camera, key.mover);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    warm(); ++key.source; CHECK(!temporal_view_accept(TEMPORAL_SP, &key)); // target changes during replay
    warm(); ++key.player_epoch; CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
}
static void ownership(void) {
    warm(); temporal_scene_mode(1, 6, 0);
    CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid); CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    key.rect[3] = 360;
    CHECK(!accept(TEMPORAL_P0)->history_valid);
    TemporalViewKey p0 = key;
    key.player += 0x100; key.camera += 0x100; key.mover += 0x100;
    key.rect[1] = 360; key.rect[3] = 361; matrices.view[12] = 10;
    CHECK(!accept(TEMPORAL_P1)->history_valid);
    CHECK(temporal_view_state(TEMPORAL_P0)->current.matrices.view[12] == 0);
    CHECK(temporal_view_state(TEMPORAL_P1)->current.matrices.view[12] == 10);
    TemporalViewKey p1 = key;
    next(); key = p0; matrices.view[12] = 1; CHECK(accept(TEMPORAL_P0)->history_valid);
    CHECK(temporal_view_state(TEMPORAL_P0)->previous.matrices.view[12] == 0);
    key = p1; matrices.view[12] = 11; CHECK(accept(TEMPORAL_P1)->history_valid);
    CHECK(temporal_view_state(TEMPORAL_P1)->previous.matrices.view[12] == 10);
    split = 1; call_hook(f_006308E0, 0);
    CHECK(!temporal_view_state(TEMPORAL_P0)->history_valid);
    CHECK(!temporal_view_state(TEMPORAL_P1)->history_valid);
    next(); key = p0; matrices.view[12] = 1; CHECK(!accept(TEMPORAL_P0)->history_valid);
    key = p1; matrices.view[12] = 11; CHECK(!accept(TEMPORAL_P1)->history_valid);
    next(); key = p0; matrices.view[12] = 1; CHECK(accept(TEMPORAL_P0)->history_valid);
    key = p1; matrices.view[12] = 11; CHECK(accept(TEMPORAL_P1)->history_valid);
    temporal_camera_cut(key.camera);
    CHECK(!temporal_view_state(TEMPORAL_P1)->history_valid);
    CHECK(temporal_view_state(TEMPORAL_P0)->history_valid);
    // Mirror accepts CPU matrices but never owns a temporal history/index.
    for (unsigned i = 0; i < 3; ++i) {
        const TemporalViewState *s = accept(TEMPORAL_MIRROR);
        CHECK(!s->history_valid && s->jitter_index == 0); next();
    }
    CHECK(temporal_view_state(TEMPORAL_P0)->current.serial == 4);
    temporal_scene_mode(0, 6, 0); CHECK(!accept(TEMPORAL_SP)->history_valid);
    CHECK(!temporal_view_accept(TEMPORAL_P1, &key));
}
static void identities(void) {
    fresh(); CHECK(!temporal_car_identity(0x600000).epoch);
    for (unsigned path = 0; path < 3; ++path) {
        nadditions = path == 1; traffic_active = path == 2;
        Cpu c = cpu(0x600000); unsigned before = constructor_calls;
        f_0075F5D0(&c); CHECK(constructor_calls == before + 1);
        TemporalCarId a = temporal_car_identity(0x600000);
        CHECK(a.address == 0x600000 && a.epoch && temporal_car_alive(a));
        call_hook(f_007556C0, 0x600000); CHECK(!temporal_car_alive(a));
        c = cpu(0x600000); f_0075F5D0(&c);
        TemporalCarId b = temporal_car_identity(0x600000);
        CHECK(b.address == a.address && b.epoch > a.epoch && !temporal_car_alive(a));
    }
    TemporalCarId a = temporal_car_identity(0x600000);
    temporal_car_construct(a.address); CHECK(!temporal_car_alive(a)); // even an unobserved destructor cannot alias
    a = temporal_car_identity(0x600000);
    temporal_state_request(0); temporal_state_request(1);
    temporal_car_construct(a.address); CHECK(!temporal_car_alive(a));
    warm(); temporal_car_construct(0x600000); a = temporal_car_identity(0x600000);
    for (unsigned i = 1; i <= TEMPORAL_CAR_CAPACITY; ++i) temporal_car_construct(0x600000 + i * 16);
    CHECK(!temporal_car_alive(a)); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    CHECK(temporal_car_identity(0x600000 + TEMPORAL_CAR_CAPACITY * 16).epoch > a.epoch);
}
static void guest_capture(void) {
    fresh(); memset(G, 0, 16 * 1024 * 1024);
    M32(0x925E90) = 6;
    M32(0x92D87C) = 0x400000; M32(0x92D884) = 1;
    M32(0x400000) = key.player; M32(key.player + 0x1C) = 0;
    M32(0x919690) = key.camera; M32(0x919694) = key.mover + 4;
    Cpu c = cpu(0x410000); M32(c.esp + 4) = 0x919650;
    cur_view = 123;
    f_006CF400(&c); CHECK(cur_view == 123 && c.ecx == 0xBAD && c.eax == 0x123 && c.esp == 0x700018);
    const TemporalViewState *s = temporal_view_accept(TEMPORAL_SP, &key);
    CHECK(s != NULL);
    if (s) {
        CHECK(s->current.matrices.projection[0] == 0.75f); CHECK(s->current.matrices.view[0] == 1);
        CHECK(!memcmp(s->current.matrices.view, G + 0x410000, 64));
        CHECK(!memcmp(s->current.matrices.projection, G + 0x410040, 64));
        CHECK(!memcmp(s->current.matrices.vp, G + 0x410080, 64));
    }
    temporal_guest_frame(1);
    // Other scene types must not overwrite SP, even though their matrices exist.
    temporal_guest_capture(0x9197A0, 0x410000);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    M32(0x9885E0) = 0x420000; M32(0x42002C) = 4;
    temporal_guest_scene_mode(0); CHECK(!temporal_view_state(TEMPORAL_SP)->history_valid);
    // Actual eView routing under split, including mirror-first setup and odd seam.
    M32(0x42002C) = 3; split = 1;
    M32(0x919700) = key.camera + 0x1000; M32(0x919704) = key.mover + 0x1000 + 4;
    M32(0x919770) = key.camera + 0x2000; M32(0x919774) = key.mover + 0x2000 + 4;
    M32(0x92D884) = 2; M32(0x400004) = key.player + 0x1000;
    M32(key.player + 0x1000 + 0x1C) = 1;
    temporal_guest_capture(0x919730, 0x410000);
    TemporalViewKey mirror = key; mirror.camera += 0x2000; mirror.mover += 0x2000;
    s = temporal_view_accept(TEMPORAL_MIRROR, &mirror);
    CHECK(s && !s->history_valid && !s->jitter_index);
    temporal_guest_capture(0x919650, 0x410000);
    key.rect[3] = 360;
    s = temporal_view_accept(TEMPORAL_P0, &key); CHECK(s && s->current.key.player == 0x30004C);
    CHECK(!temporal_view_accept(TEMPORAL_SP, &key));
    TemporalViewKey p0 = key;
    key.player += 0x1000; key.camera += 0x1000; key.mover += 0x1000;
    key.rect[1] = 360; key.rect[3] = 361;
    temporal_guest_capture(0x9196C0, 0x410000);
    s = temporal_view_accept(TEMPORAL_P1, &key); CHECK(s && s->current.key.player == key.player);
    CHECK(temporal_view_state(TEMPORAL_P0)->current.key.player == p0.player);
    CHECK(temporal_view_state(TEMPORAL_P0)->current.key.rect[3] == 360);
    CHECK(temporal_view_state(TEMPORAL_P1)->current.key.rect[1] == 360);
    CHECK(temporal_view_state(TEMPORAL_P1)->current.key.rect[3] == 361);
}
static void disabled(void) {
    temporal_state_request(0);
    // Full guest and CPU equivalence, including the existing constructor fast
    // return and the widescreen wrapper after all projection writes.
    uint8_t *before = malloc(16 * 1024 * 1024), *after = malloc(16 * 1024 * 1024);
    CHECK(before && after);
    if (!before || !after) exit(2);
    memcpy(before, G, 16 * 1024 * 1024);
    Cpu original = cpu(0x410000), hooked = original;
    M32(original.esp + 4) = 0x919650;
    memcpy(before, G, 16 * 1024 * 1024);
    f_006CF400_orig(&original); memcpy(after, G, 16 * 1024 * 1024);
    memcpy(G, before, 16 * 1024 * 1024); f_006CF400(&hooked);
    CHECK(!memcmp(&original, &hooked, sizeof original)); CHECK(!memcmp(G, after, 16 * 1024 * 1024));
    nadditions = traffic_active = 0;
    original = cpu(0x600000); hooked = original;
    f_0075F5D0_orig(&original); f_0075F5D0(&hooked);
    CHECK(!memcmp(&original, &hooked, sizeof original)); CHECK(!memcmp(G, after, 16 * 1024 * 1024));
    free(before); free(after);
    uint8_t *saved = G; G = NULL; // off hooks must not touch ANY guest memory
    call_hook(f_004700D0, key.camera); call_hook(f_00502980, 0x919650);
    call_hook(f_005029B0, 0x919650); call_hook(f_00470A10, key.mover);
    call_hook(f_006FCA30, key.player); call_hook(f_006FBF20, key.player); call_hook(f_006F8200, key.player);
    call_hook(f_007556C0, 0x600000);
    call_hook(f_006308E0, 0);
    Cpu c = cpu(0x600000); f_0075F5D0(&c);
    temporal_guest_frame(9); temporal_guest_scene_mode(1); temporal_guest_capture(0x919650, 0x410000);
    temporal_view_capture(TEMPORAL_SP, (const TemporalMatrices *)(uintptr_t)1, 1, 2, 3);
    temporal_invalidate(1); temporal_camera_cut(1); temporal_mover_changed(1, 2); temporal_player_changed(1);
    temporal_car_construct(1); temporal_car_destroy(1);
    CHECK(!temporal_car_identity(1).epoch && !temporal_view_state(TEMPORAL_SP));
    CHECK(!temporal_view_accept(TEMPORAL_SP, (const TemporalViewKey *)(uintptr_t)1));
    G = saved;
}
int main(void) {
    G = calloc(1, 16 * 1024 * 1024); if (!G) return 2;
    lifecycle(); frames(); targets(); ownership(); identities(); guest_capture(); disabled();
    free(G); printf("temporal state: %u checks / %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
