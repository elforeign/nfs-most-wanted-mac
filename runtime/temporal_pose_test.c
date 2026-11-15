// Production code and hook proof. Counts and mutation results: temporal_pose_test.py.
#include "host.h"
#include "materials.h"
#include "temporal_state.h"
#include <math.h>
#include <stdlib.h>

static unsigned checks, failures, tracked_entries;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures < 25) fprintf(stderr, "FAIL %d: %s\n", __LINE__, #x); } } while (0)
uint8_t *G;
static uint64_t presented;
static int split, disabled_probe, append_fail, empty_flush, deformed;
static unsigned emitted, original_calls;
static TemporalMatrices matrices;
static TemporalViewKey view_key;
static TemporalPartKey temporal_part_source;
static TemporalPartPose output;
static uint32_t matrix_ra;
static uint32_t aux_queue_generation;
static MatAuxDraw aux_source, aux_queue[4096];
typedef struct { uint32_t header, instances, count, id, generation; } AuxSection;
static AuxSection aux_sections[1024];
static struct { unsigned matched, attached; } aux_diag;
int diag_on;

void __attribute__((no_instrument_function)) __cyg_profile_func_enter(void *fn, void *caller) {
    (void)caller;
    if (fn == (void *)temporal_pose_submit || fn == (void *)temporal_pose_queue_reset ||
        fn == (void *)temporal_car_identity || fn == (void *)temporal_guest_pose_record ||
        fn == (void *)temporal_guest_view_id || fn == (void *)temporal_pose_clear) ++tracked_entries;
}
void __attribute__((no_instrument_function)) __cyg_profile_func_exit(void *fn, void *caller) { (void)fn; (void)caller; }
void diag_event(const char *fmt, ...) { (void)fmt; }
int split_view_rect(uint32_t v, uint32_t r[4]) { (void)v; (void)r; return split; }
#define ORIGINAL(name) void name(Cpu *c) { ++original_calls; c->eax = 0x123; c->esp += 4; }
ORIGINAL(f_004700D0_orig)
ORIGINAL(f_00470A10_orig)
ORIGINAL(f_00502980_orig)
ORIGINAL(f_005029B0_orig)
ORIGINAL(f_007556C0_orig)
ORIGINAL(f_006FCA30_orig)
ORIGINAL(f_006FBF20_orig)
ORIGINAL(f_006F8200_orig)
ORIGINAL(f_00753650_orig)
ORIGINAL(f_0075A130_orig)
ORIGINAL(f_0075A200_orig)
ORIGINAL(f_0075A330_orig)
ORIGINAL(f_0075A400_orig)
ORIGINAL(f_0075A520_orig)
void f_0073B9C0_orig(Cpu *c);
void f_00749BE0_orig(Cpu *c);
void mirror_scenery_render(Cpu *c);
void materials_car_diagnostics(Cpu *c);
void materials_car_part_diagnostics(Cpu *c);
void f_006E0AB0_orig(Cpu *c);
void f_006D7660_orig(Cpu *c);
void f_006E0200_orig(Cpu *c);
void f_006C8000_orig(Cpu *c);
int auxlights_enabled(void) { return 0; }
const AuxLightContext *auxlights_find(uint32_t section, const uint8_t *bounds) { (void)section; (void)bounds; return NULL; }
void mat_aux_fallback(int reason) { (void)reason; }
void materials_aux_record(uint32_t q) { (void)q; }
void materials_grass_record(uint32_t q) { (void)q; }
void materials_glass_record(uint32_t q) { (void)q; }
void materials_trees_record(uint32_t q) { (void)q; }
void materials_aux_clear(void) {}
void materials_grass_clear(void) {}
void materials_glass_clear(void) {}
void materials_trees_clear(void) {}
void underglow_render_receiver_clear(void) {}
static void fe_transform(float *x, float *y) { *x = *y = 1; }
uint32_t galloc(uint32_t bytes) { (void)bytes; abort(); }
#include "temporal_pose_hooks.inc"

static void identity(float *m) { memset(m, 0, 64); for (unsigned i = 0; i < 4; ++i) m[i * 5] = 1; }
static void world(float *m, float x) {
    identity(m); m[0] = 0; m[1] = 2; m[4] = -3; m[5] = 0; m[10] = .5f;
    m[12] = x; m[13] = 2*x;
}
static TemporalPartKey key(TemporalViewId view, uint32_t part, uint32_t instance) {
    return (TemporalPartKey){.car = temporal_car_identity(0x200000), .view = view,
        .part = part, .instance = instance, .model = 0x400000, .mesh = 0x410000};
}
static void fresh(int two) {
    temporal_state_request(0); temporal_state_request(1);
    split = two; temporal_scene_mode(split, 6, 0); presented = 0;
    temporal_car_construct(0x200000);
    memset(&matrices, 0, sizeof matrices);
    identity(matrices.view); identity(matrices.projection); identity(matrices.vp);
    matrices.near_z = 1; matrices.far_z = 1000;
    view_key = (TemporalViewKey){.source = 1, .depth = 2, .generation = 3, .player_epoch = 1,
        .rect = {0, 0, 1280, 721}, .width = 1280, .height = 721, .samples = 4,
        .format = 80, .domain = 1, .player = 0x30004C, .camera = 0x310000, .mover = 0x320000};
    M32(0x982CDC) = 0; temporal_part_source = (TemporalPartKey){0};
}
static const TemporalViewState *accept(TemporalViewId view) {
    TemporalViewKey k = view_key;
    if (view == TEMPORAL_P1) { k.player += 0x1000; k.camera += 0x1000; k.mover += 0x1000; }
    temporal_view_capture(view, &matrices, k.player, k.camera, k.mover);
    const TemporalViewState *s = temporal_view_accept(view, &k);
    CHECK(s != NULL); return s;
}
static void next(void) { temporal_frame_boundary(++presented, 6, 0); temporal_pose_queue_reset(); }
static void commit(TemporalViewId view) {
    uint64_t serial = temporal_view_state(view)->current.serial;
    CHECK(temporal_pose_commit(view, serial)); CHECK(!temporal_pose_commit(view, serial));
}
static TemporalPoseToken submit(unsigned slot, TemporalPartKey k, float x) {
    float m[16]; world(m, x); return temporal_pose_submit(slot, &k, m);
}
static void resolve(TemporalPoseToken t, TemporalViewId v, int valid, float x, float prev) {
    CHECK(temporal_pose_resolve(t, v, &output));
    CHECK(output.history_valid == (unsigned)valid);
    CHECK(output.current[12] == x && output.current[13] == 2*x);
    CHECK(output.previous[12] == prev && output.previous[13] == 2*prev);
    float expected[16]; world(expected, x); CHECK(!memcmp(expected, output.current, 64));
    world(expected, prev); CHECK(!memcmp(expected, output.previous, 64));
}
static void queued_parts(void) {
    fresh(0); accept(TEMPORAL_SP);
    // Same model+mesh, independent slots and two instances of the same wheel slot.
    TemporalPartKey keys[6] = {key(TEMPORAL_SP, 0xB84, 1), key(TEMPORAL_SP, 0xB98, 1),
        key(TEMPORAL_SP, 0x10C0, 2), key(TEMPORAL_SP, 0x10C0, 3),
        key(TEMPORAL_SP, UINT32_MAX, 4), key(TEMPORAL_SP, UINT32_MAX, 5)};
    TemporalPoseToken t[6];
    for (unsigned i = 0; i < 6; ++i) t[i] = submit(i, keys[i], i+10);
    // Submission is not an observation. The actual playback freezes each pose.
    for (unsigned i = 6; i--;) resolve(t[i], TEMPORAL_SP, 0, i+10, i+10);
    commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP);
    for (unsigned i = 0; i < 6; ++i) t[5-i] = submit(5-i, keys[i], i+20);
    for (unsigned i = 0; i < 6; ++i) {
        resolve(t[5-i], TEMPORAL_SP, 1, i+20, i+10);
        // Repeated materials/passes must see the preceding FRAME, never a draw.
        resolve(t[5-i], TEMPORAL_SP, 1, i+20, i+10);
    }
    commit(TEMPORAL_SP);
    for (unsigned i = 0; i < 6; ++i) resolve(t[5-i], TEMPORAL_SP, 1, i+20, i+10);
    next(); accept(TEMPORAL_SP);
    for (unsigned i = 0; i < 6; ++i) resolve(submit(i, keys[i], i+30), TEMPORAL_SP, 1, i+30, i+20);
    commit(TEMPORAL_SP);
    // LOD/model/mesh changes cannot borrow correspondence, including A->B->A.
    next(); accept(TEMPORAL_SP); TemporalPartKey lod = keys[0]; lod.part += 4;
    resolve(submit(0, lod, 40), TEMPORAL_SP, 0, 40, 40); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); resolve(submit(0, keys[0], 50), TEMPORAL_SP, 0, 50, 50); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); lod = keys[0]; lod.model += 4;
    resolve(submit(0, lod, 60), TEMPORAL_SP, 0, 60, 60); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); lod.mesh += 4;
    resolve(submit(0, lod, 70), TEMPORAL_SP, 0, 70, 70);
}
static void generations(void) {
    fresh(0); accept(TEMPORAL_SP); TemporalPartKey a = key(TEMPORAL_SP, 1, 1), b = key(TEMPORAL_SP, 2, 1);
    TemporalPoseToken old = submit(0, a, 10), current = submit(0, b, 20);
    CHECK(!temporal_pose_resolve(old, TEMPORAL_SP, &output));
    resolve(current, TEMPORAL_SP, 0, 20, 20);
    TemporalPoseToken forged = current; ++forged.part_generation;
    CHECK(!temporal_pose_resolve(forged, TEMPORAL_SP, &output));
    forged = current; ++forged.queue_generation;
    CHECK(!temporal_pose_resolve(forged, TEMPORAL_SP, &output));
    forged = current; ++forged.record_generation;
    CHECK(!temporal_pose_resolve(forged, TEMPORAL_SP, &output));
    float m[16]; world(m, 20);
    temporal_pose_record(0, b.mesh, m, TEMPORAL_SP); CHECK(temporal_pose_draw(TEMPORAL_SP));
    current = submit(0, a, 30);
    CHECK(!temporal_pose_draw(TEMPORAL_SP)); // old playback token, newly occupied queue slot
    world(m, 30); temporal_pose_record(0, a.mesh+1, m, TEMPORAL_SP); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    m[12] = 999; temporal_pose_record(0, a.mesh, m, TEMPORAL_SP); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    world(m, 30); temporal_pose_record(0, a.mesh, m, TEMPORAL_P0); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    temporal_pose_queue_reset(); current = submit(0, a, 30);
    CHECK(!temporal_pose_resolve(old, TEMPORAL_SP, &output));
    resolve(current, TEMPORAL_SP, 0, 30, 30);
    temporal_pose_submit(0, NULL, NULL);
    CHECK(!temporal_pose_resolve(current, TEMPORAL_SP, &output));
    TemporalPoseToken bad = submit(TEMPORAL_QUEUE_CAPACITY, a, 1);
    CHECK(!temporal_pose_resolve(bad, TEMPORAL_SP, &output));
    current = submit(0, a, 30); commit(TEMPORAL_SP);
    temporal_state_request(0); temporal_state_request(1); temporal_scene_mode(0, 6, 0); accept(TEMPORAL_SP);
    temporal_car_construct(a.car.address); a.car = temporal_car_identity(a.car.address); submit(0, a, 30);
    CHECK(!temporal_pose_resolve(current, TEMPORAL_SP, &output));
}
static void two_cars_and_acceptance(void) {
    fresh(0); TemporalPartKey a = key(TEMPORAL_SP, 1, 1);
    TemporalPoseToken t = submit(0, a, 10);
    CHECK(!temporal_pose_resolve(t, TEMPORAL_SP, &output)); CHECK(!temporal_pose_commit(TEMPORAL_SP, 1));
    accept(TEMPORAL_SP); resolve(t, TEMPORAL_SP, 0, 10, 10);
    temporal_car_construct(0x201000);
    TemporalPartKey b = a; b.car = temporal_car_identity(0x201000);
    resolve(submit(1, b, 100), TEMPORAL_SP, 0, 100, 100);
    CHECK(!temporal_pose_commit(TEMPORAL_SP, temporal_view_state(TEMPORAL_SP)->current.serial+1));
    commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP);
    resolve(submit(0, b, 110), TEMPORAL_SP, 1, 110, 100);
    resolve(submit(1, a, 20), TEMPORAL_SP, 1, 20, 10); commit(TEMPORAL_SP);
    next(); temporal_camera_cut(view_key.camera); accept(TEMPORAL_SP);
    resolve(submit(0, a, 30), TEMPORAL_SP, 0, 30, 30); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); resolve(submit(0, a, 40), TEMPORAL_SP, 1, 40, 30);
}
static void lifetime_and_views(void) {
    fresh(0); accept(TEMPORAL_SP); TemporalPartKey a = key(TEMPORAL_SP, 1, 1);
    TemporalPoseToken t = submit(0, a, 10); resolve(t, TEMPORAL_SP, 0, 10, 10); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP);
    temporal_car_destroy(a.car.address); temporal_car_construct(a.car.address); // traffic -> cop allocation reuse
    CHECK(!temporal_pose_resolve(submit(0, a, 20), TEMPORAL_SP, &output));
    a.car = temporal_car_identity(a.car.address);
    resolve(submit(0, a, 20), TEMPORAL_SP, 0, 20, 20); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); resolve(submit(0, a, 30), TEMPORAL_SP, 1, 30, 20);
    temporal_car_destroy(a.car.address); CHECK(!temporal_pose_commit(TEMPORAL_MIRROR, 1));
    CHECK(!temporal_pose_resolve(submit(0, a, 31), TEMPORAL_SP, &output));
    fresh(1); accept(TEMPORAL_P0); accept(TEMPORAL_P1); accept(TEMPORAL_MIRROR);
    TemporalPartKey p0 = key(TEMPORAL_P0, 1, 1), p1 = key(TEMPORAL_P1, 1, 1), refl = key(TEMPORAL_MIRROR, 1, 1);
    CHECK(!temporal_pose_resolve(submit(0, refl, 900), TEMPORAL_MIRROR, &output));
    resolve(submit(1, p1, 100), TEMPORAL_P1, 0, 100, 100);
    resolve(submit(2, p0, 10), TEMPORAL_P0, 0, 10, 10);
    CHECK(!temporal_pose_resolve(submit(3, p1, 100), TEMPORAL_P0, &output));
    commit(TEMPORAL_P1); commit(TEMPORAL_P0); next(); accept(TEMPORAL_P0); accept(TEMPORAL_P1);
    resolve(submit(0, p0, 20), TEMPORAL_P0, 1, 20, 10);
    resolve(submit(1, p1, 110), TEMPORAL_P1, 1, 110, 100);
    temporal_camera_cut(view_key.camera);
    CHECK(!temporal_pose_resolve(submit(2, p0, 20), TEMPORAL_P0, &output));
    resolve(submit(3, p1, 110), TEMPORAL_P1, 1, 110, 100);
    CHECK(!temporal_pose_commit(TEMPORAL_P0, temporal_view_state(TEMPORAL_P0)->current.serial));
    commit(TEMPORAL_P1);
}
static void missing_and_conflict(void) {
    fresh(0); accept(TEMPORAL_SP); TemporalPartKey a = key(TEMPORAL_SP, 1, 1);
    resolve(submit(0, a, 10), TEMPORAL_SP, 0, 10, 10); // unencoded: no commit
    next(); accept(TEMPORAL_SP); resolve(submit(0, a, 20), TEMPORAL_SP, 0, 20, 20); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP); submit(0, a, 30); commit(TEMPORAL_SP); // queued but never drawn
    next(); accept(TEMPORAL_SP); resolve(submit(0, a, 40), TEMPORAL_SP, 0, 40, 40); commit(TEMPORAL_SP);
    next(); accept(TEMPORAL_SP);
    resolve(submit(0, a, 50), TEMPORAL_SP, 1, 50, 40);
    CHECK(temporal_pose_resolve(submit(1, a, 51), TEMPORAL_SP, &output)); CHECK(!output.history_valid);
    CHECK(temporal_pose_resolve(submit(2, a, 50), TEMPORAL_SP, &output)); CHECK(!output.history_valid);
    commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP);
    resolve(submit(0, a, 60), TEMPORAL_SP, 0, 60, 60);
    float m[16]; world(m, 70); m[0] = NAN;
    CHECK(!temporal_pose_resolve(temporal_pose_submit(1, &a, m), TEMPORAL_SP, &output));
}

// Hook fixture simulates retail submission + final matrix update, not pose state.
// Every wrapper above is copied verbatim from materials.m / widescreen.c.
static uint32_t source_view = 0x919650, part_slot = 0xB84, call_site = 0x74ECA0;
static int direct_model;
static float fixture_x;
static void set_args(Cpu *c, uint32_t ra, uint32_t a0, uint32_t a1, uint32_t a2) {
    M32(c->esp) = ra; M32(c->esp+4) = a0; M32(c->esp+8) = a1; M32(c->esp+12) = a2;
}
static void model_submit(Cpu *caller, uint32_t ra) {
    Cpu c = *caller; c.esp -= 0x100; c.ecx = source_view;
    set_args(&c, ra, 0x400000, 0x500000, 0);
    f_006DA9B0(&c);
}
void f_0073B9C0_orig(Cpu *c) {
    if (!disabled_probe) model_submit(c, 0x73BBA0);
    c->eax = 23; c->ecx = 0xBAD; c->esp += 24;
}
void f_00749BE0_orig(Cpu *c) {
    if (!disabled_probe) {
        Cpu m = *c; m.esp -= 0x100;
        set_args(&m, 0x749C10, 0x400000, source_view, 0);
        f_0073B9C0(&m);
    }
    c->eax = 29; c->ecx = 0xBAD; c->esp += 28;
}
void materials_car_diagnostics(Cpu *c) {
    ++original_calls;
    if (!disabled_probe) {
        if (direct_model) model_submit(c, call_site);
        else {
            Cpu p = *c; p.esp -= 0x100; p.ecx = c->ecx;
            set_args(&p, call_site, source_view, c->ecx + part_slot, 0x500000);
            f_007387E0(&p);
        }
    }
    c->eax = 7; c->ecx = 0xBAD; c->esp += 52;
}
void materials_car_part_diagnostics(Cpu *c) {
    ++original_calls;
    if (!disabled_probe) model_submit(c, 0x738820);
    c->eax = 9; c->ecx = 0xBAD; c->esp += 24;
}
void mirror_scenery_render(Cpu *c) {
    Cpu a = *c; a.esp -= 0x100;
    // This is the submitted matrix, after scale/position/wheel articulation.
    world((float *)(G+0x500000), fixture_x);
    f_006E0AB0(&a);
    c->eax = 11; c->ecx = 0xBAD; c->esp += 24;
}
void f_006E0AB0_orig(Cpu *c) {
    ++original_calls;
    unsigned n = M32(0x982CDC);
    if (!append_fail && n < 4096) {
        uint32_t q = 0x93E878 + n*0x44;
        M32(q) = 0x410000; M32(q+28) = 0x500000;
        M32(q+4) = deformed == 1; M32(q+32) = deformed == 2 ? 0x510000 : 0;
        ++M32(0x982CDC);
    }
    c->eax = 13;
}
void f_006D7660_orig(Cpu *c) {
    const TemporalPartPose *d = temporal_pose_draw(split ? TEMPORAL_P0 : TEMPORAL_SP);
    if (d) { output = *d; ++emitted; }
    c->eax = 15;
}
void f_006E0200_orig(Cpu *c) {
    if (!empty_flush && M32(0x982CDC)) {
        Cpu p = *c; p.ecx = 0x93E878; f_006D7660(&p);
    }
    M32(0x982CDC) = 0; c->eax = 17;
}
void f_006C8000_orig(Cpu *c) {
    const TemporalPartPose *d = temporal_pose_draw(split ? TEMPORAL_P0 : TEMPORAL_SP);
    if (d) { output = *d; ++emitted; }
    c->eax = 19;
}
static Cpu cpu(void) { return (Cpu){.ecx = 0x200000, .esp = 0x700000, .esi = 0x93E878, .edi = 0x123}; }
static void installed_view(uint32_t entry, uint32_t number) {
    M32(0x982A20) = 0x600000; M32(0x600000) = entry; M32(0x600004) = number;
    M32(0x9195E0 + number*0x70 + 0x60) = 0x600000;
}
static void shipping_hooks(void) {
    fresh(0); accept(TEMPORAL_SP); installed_view(0, 1);
    M32(0x200000+0xB84) = 0x400003;
    Cpu c = cpu(); set_args(&c, 1, source_view, 0, 0); fixture_x = 10;
    f_0074E160(&c); CHECK(c.eax == 7 && c.ecx == 0xBAD && c.esp == 0x700034);
    // A mutable frame matrix can no longer change the queue-owned observation.
    world((float *)(G+0x500000), 999); temporal_guest_pose_record(0x93E878);
    CHECK(!temporal_pose_draw(TEMPORAL_SP));
    world((float *)(G+0x500000), 10); c = cpu(); c.ecx = 0x93E878;
    emitted = 0; f_006D7660(&c); CHECK(emitted == 1 && !output.history_valid && output.current[12] == 10);
    CHECK(!temporal_pose_draw(TEMPORAL_SP)); commit(TEMPORAL_SP);
    c = cpu(); empty_flush = 1; f_006E0200(&c); empty_flush = 0;
    CHECK(!temporal_pose_draw(TEMPORAL_SP)); next(); accept(TEMPORAL_SP);
    // Reflection-first queue records cannot seed or overwrite the main view.
    source_view = 0x9197A0; installed_view(1, 4); c = cpu(); set_args(&c, 1, source_view, 0, 0); fixture_x = 900;
    f_0074E160(&c); c = cpu(); c.ecx = 0x93E878; emitted = 0; f_006D7660(&c); CHECK(!emitted);
    c = cpu(); f_006E0200(&c);
    source_view = 0x919650; installed_view(0, 1); c = cpu(); set_args(&c, 1, source_view, 0, 0); fixture_x = 20;
    f_0074E160(&c); c = cpu(); set_args(&c, 0x006E03F9, 0x500000, 1, 0); emitted = 0;
    f_006C8000(&c); CHECK(emitted == 1 && output.history_valid && output.previous[12] == 10);
    // An unrelated matrix setup retires an inlined record even without a draw.
    set_args(&c, 0x1234, 0x500000, 1, 0); f_006C8000(&c); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    commit(TEMPORAL_SP); c = cpu(); f_006E0200(&c);
    // Direct tire submissions share one model, but their sites distinguish wheels.
    next(); accept(TEMPORAL_SP); direct_model = 1;
    for (unsigned i = 0; i < 4; ++i) {
        call_site = 0x750000 + 16*i; fixture_x = 30+i; c = cpu(); set_args(&c, 1, source_view, 0, 0); f_0074E160(&c);
        c = cpu(); c.ecx = 0x93E878 + i*0x44; f_006D7660(&c); CHECK(!output.history_valid && output.current[12] == 30+i);
    }
    commit(TEMPORAL_SP); c = cpu(); f_006E0200(&c); next(); accept(TEMPORAL_SP);
    for (unsigned i = 0; i < 4; ++i) {
        call_site = 0x750000 + 16*i; fixture_x = 40+i; c = cpu(); set_args(&c, 1, source_view, 0, 0); f_0074E160(&c);
    }
    for (unsigned i = 4; i--;) {
        // Retail uses separate frame matrices per instance. Restore each queued
        // pointer's bytes here because the fixture deliberately reuses storage.
        world((float *)(G+0x500000), 40+i); c = cpu(); c.ecx = 0x93E878 + i*0x44;
        f_006D7660(&c); CHECK(output.history_valid && output.previous[12] == 30+i);
    }
    direct_model = 0;
    // Unscoped model/scenery appends always overwrite the queue descriptor.
    c = cpu(); M32(0x982CDC) = 0; fixture_x = 44; mirror_scenery_render(&c);
    world((float *)(G+0x500000), 44); temporal_guest_pose_record(0x93E878); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    // Empty/failed appends and bad/misaligned playback are safe.
    append_fail = 1; c = cpu(); M32(0x982CDC) = 0; f_006E0AB0(&c); CHECK(M32(0x982CDC) == 0); append_fail = 0;
    temporal_guest_pose_record(0); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    temporal_guest_pose_record(0x93E879); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    for (deformed = 1; deformed <= 2; ++deformed) {
        M32(0x982CDC) = 0; c = cpu(); set_args(&c, 1, source_view, 0, 0); f_0074E160(&c);
        temporal_guest_pose_record(0x93E878); CHECK(!temporal_pose_draw(TEMPORAL_SP));
    }
    deformed = 0;
}
static void shipping_split(void) {
    fresh(1); accept(TEMPORAL_P0); accept(TEMPORAL_P1);
    M32(0x200000+0xB84) = 0x400003; part_slot = 0xB84; call_site = 0x74ECA0;
    for (unsigned f = 0; f < 2; ++f) {
        for (unsigned n = 0; n < 2; ++n) {
            unsigned player = f ? 1-n : n;
            source_view = player ? 0x9196C0 : 0x919650; installed_view(0, 1+player);
            fixture_x = (player ? 100 : 10) + 10*f;
            Cpu c = cpu(); set_args(&c, 1, source_view, 0, 0); f_0074E160(&c);
        }
        for (unsigned n = 2; n--;) {
            unsigned player = f ? 1-n : n;
            source_view = player ? 0x9196C0 : 0x919650; installed_view(0, 1+player);
            TemporalViewId id = player ? TEMPORAL_P1 : TEMPORAL_P0;
            float x = (player ? 100 : 10) + 10*f;
            world((float *)(G+0x500000), x); temporal_guest_pose_record(0x93E878+n*0x44);
            const TemporalPartPose *d = temporal_pose_draw(id);
            CHECK(d && d->history_valid == f && d->current[12] == x);
            CHECK(d && d->previous[12] == (f ? x-10 : x));
            CHECK(!temporal_pose_draw(player ? TEMPORAL_P0 : TEMPORAL_P1));
        }
        commit(TEMPORAL_P0); commit(TEMPORAL_P1);
        Cpu c = cpu(); empty_flush = 1; f_006E0200(&c); empty_flush = 0;
        next(); accept(TEMPORAL_P0); accept(TEMPORAL_P1);
    }
    source_view = 0x919650;
}
static void destructibles(void) {
    fresh(0); accept(TEMPORAL_SP); installed_view(0, 1); source_view = 0x919650;
    // Existing allocations are lazily seeded when a consumer first asks.
    for (unsigned f = 0; f < 2; ++f) {
        fixture_x = 12+f*3; Cpu c = cpu(); c.ecx = 0x220000;
        set_args(&c, 0x753870, 0x400000, source_view, 0); f_0073B9C0(&c);
        CHECK(c.eax == 23 && c.ecx == 0xBAD && c.esp == 0x700018);
        c = cpu(); c.ecx = 0x93E878; emitted = 0; f_006D7660(&c);
        CHECK(emitted == 1 && output.history_valid == f && output.current[12] == fixture_x);
        CHECK(emitted == 1 && output.previous[12] == 12);
        commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP); M32(0x982CDC) = 0;
    }
    // Constructor and destructor hooks invalidate same-address history.
    void (*constructors[])(Cpu *) = {f_0075A130,f_0075A200,f_0075A330,f_0075A400,f_0075A520};
    for (unsigned i = 0; i < sizeof constructors/sizeof *constructors; ++i) {
        TemporalCarId old = temporal_car_identity(0x220000);
        Cpu c = cpu(); c.ecx = 0x220000; constructors[i](&c);
        CHECK(!temporal_car_alive(old));
        fixture_x = 20+i; c = cpu(); c.ecx = 0x220000;
        set_args(&c, 0x753870, 0x400000, source_view, 0); f_0073B9C0(&c);
        c = cpu(); c.ecx = 0x93E878; emitted = 0; f_006D7660(&c);
        CHECK(emitted == 1 && !output.history_valid && output.current[12] == fixture_x);
        commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP); M32(0x982CDC) = 0;
    }
    TemporalCarId old = temporal_car_identity(0x220000);
    Cpu c = cpu(); c.ecx = 0x220000; f_00753650(&c); CHECK(!temporal_car_alive(old));
    // Two hierarchy nodes sharing a model retain independent transforms.
    for (unsigned f = 0; f < 2; ++f) {
        for (unsigned node = 0; node < 2; ++node) {
            fixture_x = 40+10*node+f; c = cpu(); c.ecx = 0x220000;
            set_args(&c, 0x753870, 0x430000, node, source_view); f_00749BE0(&c);
            c = cpu(); c.ecx = 0x93E878+node*0x44; emitted = 0; f_006D7660(&c);
            CHECK(emitted == 1 && output.key.part == node+1 && output.key.instance == 0x430000);
            CHECK(emitted == 1 && output.history_valid == f && output.previous[12] == 40+10*node);
        }
        commit(TEMPORAL_SP); next(); accept(TEMPORAL_SP); M32(0x982CDC) = 0;
    }
}
static void overflow(void) {
    fresh(0); accept(TEMPORAL_SP);
    TemporalPartKey a = key(TEMPORAL_SP, 1, 1); TemporalPoseToken first = submit(0, a, 1);
    for (unsigned i = 1; i < TEMPORAL_POSE_CAPACITY; ++i) { a.part = i+1; submit(i, a, 1); }
    a.part = TEMPORAL_POSE_CAPACITY+1;
    CHECK(!temporal_pose_resolve(submit(1, a, 2), TEMPORAL_SP, &output));
    resolve(first, TEMPORAL_SP, 0, 1, 1);
    // Old queue tokens must fail when their part-table slot is eventually evicted.
    next(); accept(TEMPORAL_SP); next(); accept(TEMPORAL_SP);
    for (unsigned i = 0; i < TEMPORAL_POSE_CAPACITY; ++i) { a.part = i+10000; submit(i, a, 2); }
    CHECK(!temporal_pose_resolve(first, TEMPORAL_SP, &output));
    CHECK(temporal_pose_storage_bytes() < 2*1024*1024);
}
static void disabled(void) {
    temporal_state_request(0); disabled_probe = 1;
    uint8_t *saved = G; G = NULL; Cpu c = cpu(); unsigned before = tracked_entries;
    Cpu expected = c; materials_car_diagnostics(&expected);
    f_0074E160(&c); CHECK(!memcmp(&c, &expected, sizeof c));
    c = cpu(); expected = c; materials_car_part_diagnostics(&expected);
    f_007387E0(&c); CHECK(!memcmp(&c, &expected, sizeof c));
    CHECK(tracked_entries == before); G = saved;
    M32(0x982CDC) = 0; c = cpu(); before = tracked_entries; f_006E0AB0(&c);
    c = cpu(); set_args(&c, 0x006E03F9, 0x500000, 1, 0); f_006C8000(&c);
    c = cpu(); c.ecx = 0x93E878; f_006D7660(&c);
    c = cpu(); f_006E0200(&c);
    CHECK(tracked_entries == before); CHECK(!temporal_state_requested);
    disabled_probe = 0;
}
int main(void) {
    G = calloc(1, 16*1024*1024); if (!G) return 2;
    queued_parts(); generations(); two_cars_and_acceptance(); lifetime_and_views(); missing_and_conflict(); shipping_hooks(); shipping_split(); destructibles(); overflow(); disabled();
    free(G); printf("%u checks / %u failures; pose storage %zu bytes\n", checks, failures, temporal_pose_storage_bytes());
    return failures ? 1 : 0;
}
