                                                                                     
// POV 2/3 are close/far. GetPov supplies MW's car/view-specific bases each frame;
// no attribute data or shared camera tables are changed. Off calls retail verbatim.
#include <math.h>
#include <stdlib.h>
#include "host.h"
#include "settings.h"

static const char *const kValues[] = {"off", "on"};
static Setting h_camera = {.id = "controls.h_camera", .category = SET_CONTROLS, .order = 135,
    .label = "Camera", .help = "How the chase camera follows the car.", .type = SET_HEADING};
static Setting s_chase = {.id = "controls.chase_camera", .category = SET_CONTROLS, .order = 140,
    .label = "Speed-reactive chase camera",
    .help = "Widens the chase view and adjusts its distance, height and turning lag as speed rises.",
    .type = SET_TOGGLE, .values = kValues, .nchoices = 2, .def = 0, .env = "NFS_CHASE_CAM"};
__attribute__((constructor)) static void chase_register(void) { settings_add(&h_camera); settings_add(&s_chase); }

typedef struct { float fov_delta, distance_ratio, height_ratio, direction_ratio; } ChaseCurve;
static float ramp(float speed, float low, float high) {
    if (!(speed > low)) return 0; // includes NaN: keep the rest view
    if (speed >= high) return 1;
    return (speed - low) / (high - low);
}
ChaseCurve chase_curve(int far, float kmh) {
    ChaseCurve r;
    if (!far) {
        float t = ramp(kmh, 32.19f, 257.50f);
        r.fov_delta = 37.f * t;
        r.distance_ratio = (5.8f - .1f * t) / 5.8f;
        r.height_ratio = (1.6f - .1f * t) / 1.6f;
        r.direction_ratio = 1.f - .5f * ramp(kmh, 0, 80.47f);
    } else {
        r.fov_delta = 47.f * ramp(kmh, 8.05f, 193.12f);
        r.distance_ratio = (6.5f + .7f * ramp(kmh, 8.05f, 241.40f)) / 6.5f;
        r.height_ratio = (1.4f - .15f * ramp(kmh, 48.28f, 160.93f)) / 1.4f;
        r.direction_ratio = 1.f; // decoded far DirectionDuration is constant
    }
    return r;
}

typedef struct {
    uint32_t mover, anchor, camera, mode;
    int active, sampled;
    float kmh, fov, distance, height, direction;
} ChaseFrame;
static ChaseFrame *frame;

// Proof fixture only: ask the game's AI to cruise below a chosen speed. This
// changes its desired speed, never the camera's speed input or the car velocity.
static uint32_t cruise_human;
static float cruise_kmh(void) {
    if (!diag_on) return 0;
    const char *v = getenv("BOOT_DIAG_CHASE_CRUISE");
    float k = v ? strtof(v, NULL) : 0;
    return k == 50 || k == 200 ? k : 0;
}
void f_0042F140_orig(Cpu *c);
void f_0042F140(Cpu *c) { // AIVehicleHuman::Update; AIVehicle interface is +4C
    if (cruise_kmh()) cruise_human = c->ecx + 0x4C;
    f_0042F140_orig(c);
}
void f_00431C80_orig(Cpu *c);
void f_00431C80(Cpu *c) { // AIVehicle::SetDriveSpeed(float m/s)
    if (cruise_human && c->ecx == cruise_human) {
        // At low speed the AI brakes below its cap; a 5 km/h allowance centres
        // its cruise near 50. At 200 it reaches the cap. Shots use physical speed.
        float goal = cruise_kmh();
        float target = (goal + (goal == 50 ? 5.f : 0.f)) / 3.6f;
        if (goal > 0 && argf(c, 0) > target) MF32(c->esp + 4) = target;
    }
    f_00431C80_orig(c);
}

void f_004713C0_orig(Cpu *c);
void f_0046F2B0_orig(Cpu *c);
void f_0047CE80_orig(Cpu *c);
void f_0047D3C0_orig(Cpu *c);

void f_004713C0(Cpu *c) {
    uint32_t anchor = c->ecx, mode = arg(c, 0);
    int ours = frame && anchor == frame->anchor && mode == frame->mode;
    f_004713C0_orig(c);
    if (!ours) return;
    uint32_t pov = c->eax;
    if (frame->active) {
        ChaseCurve r = chase_curve(mode == 3, frame->kmh);
        // GetPov: {u16 mode, pitch; float lag, height, side; u16 fov, ...}.
        M16(pov + 0x10) = (uint16_t)(M16(pov + 0x10) + r.fov_delta * (65536.f / 360.f));
        MF32(pov + 4) *= r.distance_ratio;
        MF32(pov + 8) *= r.height_ratio;
    }
    if (diag_on) {
        frame->sampled = 1;
        frame->fov = M16(pov + 0x10) * (360.f / 65536.f);
        frame->distance = -MF32(pov + 4);
        frame->height = MF32(pov + 8);
    }
}

void f_0046F2B0(Cpu *c) {
    uint32_t table = c->ecx, out = arg(c, 0);
    // This table evaluator has other users. Only the call inside CubicCameraMover::Update.
    int ours = frame && M32(c->esp) == 0x0047D4B1u && table == 0x008EE040u + frame->mode * 20;
    f_0046F2B0_orig(c);
    if (!ours) return;
    if (frame->active) {
        ChaseCurve r = chase_curve(frame->mode == 3, frame->kmh);
        uint32_t rest = M32(table + 16);
        // +A0/A4/A8 feed mover+90's direction splines, not eye (+88),
        // target (+8C), up (+94), or FOV (+84). Use MW's REST values once,
        // replacing its speed taper instead of multiplying two speed effects.
        for (unsigned axis = 0; axis < 3; ++axis)
            MF32(out + 0xA0 + axis * 4) = MF32(rest + 0xA0 + axis * 4) * r.direction_ratio;
    }
    if (diag_on) frame->direction = MF32(out + 0xA0);
}

void f_0047CE80(Cpu *c) {
    if (frame && frame->active && c->ecx == frame->mover) {
        // The original scales lag by up to 1.20 (008EE0CC). Our lag already
        // contains the decoded curve, so skip that boost; vibration is elsewhere.
        c->esp += 8; // __thiscall, one stack argument
        return;
    }
    uint32_t out = arg(c, 0);
    int ours = frame && c->ecx == frame->mover;
    f_0047CE80_orig(c);
    if (ours && diag_on) frame->distance = -MF32(out);
}

static void chase_record(const ChaseFrame *f) {
    static int init;
    static FILE *log;
    if (!init) {
        init = 1;
        if (!getenv("BOOT_DIAG_CHASE")) return;
        char path[1024]; snprintf(path, sizeof path, "%s/chasecam.tsv", diag_dir);
        log = fopen(path, "w");
        if (log) fprintf(log, "sample\tsim\tmode\ton\tkmh\tcar_kmh\tfov_target\tdistance\theight\tdirection_s\tfov_actual\tmatrix_hex\n");
    }
    if (!log) return;
    static unsigned sample;
    float diag_car_speed(void);
    fprintf(log, "%u\t%.6f\t%u\t%d\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t%.6f\t",
            sample++, MF32(0x009885D8u), f->mode, f->active, f->kmh, diag_car_speed() * 3.6f, f->fov, f->distance,
            f->height, f->direction, M16(f->camera + 0xC4) * (360.f / 65536.f));
    for (unsigned i = 0; i < 16; ++i) fprintf(log, "%08x", M32(f->camera + 4 * i));
    fputc('\n', log); fflush(log);

    // One screenshot after a steady half second on a straight, with the FOV
    // spline settled. The logged camera inputs and actual physical car speed
    // accompany the frame request. No override of camera/physics values.
    static int shot;
    static float since, forward[3], previous_sim;
    float goal = cruise_kmh(), now = MF32(0x009885D8u), physical = diag_car_speed() * 3.6f;
    const float *fwd = (const float *)(G + f->anchor + 0x50);
    float dot = fwd[0] * forward[0] + fwd[1] * forward[1] + fwd[2] * forward[2];
    float dt = now - previous_sim;
    int steady = goal && now >= 16.f && fabsf(physical - goal) < 5 && dt > 0 && dt < .1f && dot > .9999f &&
        fabsf(M16(f->camera + 0xC4) * (360.f / 65536.f) - f->fov) < 1.f;
    // Bound the whole half-second's turn to 0.81 degrees, rather than merely
    // bounding each frame's turn (which would also admit long bends).
    if (!steady) { since = now; memcpy(forward, fwd, sizeof forward); }
    if (!shot && steady && now - since >= .5f) {
        uint64_t diag_current_frame(void);
        uint64_t capture = diag_current_frame() + 1;
        diag_shot_request(capture); shot = 1;
        diag_event("chasecam shot: frame=%llu sim=%.6f on=%d kmh=%.3f fov=%.6f distance=%.6f height=%.6f direction=%.6f",
            (unsigned long long)capture, now, f->active, physical, M16(f->camera + 0xC4) * (360.f / 65536.f),
            f->distance, f->height, f->direction);
    }
    previous_sim = now;
}

// Optional diagnostic replay, on the game thread with the GIL held. Camera
// Update writes its mover/anchor, five cubic objects, the Camera, scratch stack,
// and image globals (including shaker/noise state). Restore those inputs after
// an unscoped retail call, then run the Off wrappers on the same inputs. Only
// settled POVs are sampled, avoiding attribute collection changes/allocations.
// Each comparison records both 64-byte matrices, plus FOV, rather than comparing
// independently driven games. Never entered unless BOOT_DIAG_CHASE_COMPARE is set.
typedef struct { uint32_t address, size; uint8_t *bytes; } ChaseSaved;
static void chase_compare(Cpu *c, ChaseFrame *f, int broken) {
    ChaseSaved save[10] = {{0x008EA000u, 0xDD000u, NULL},
        {c->esp - 0x2000, 0x2010, NULL}, {f->mover, 0xE4, NULL},
        {f->anchor, 0x114, NULL}, {f->camera, 0x2C0, NULL}};
    for (unsigned i = 0; i < 5; ++i)
        save[5 + i] = (ChaseSaved){M32(f->mover + 0x84 + i * 4), i ? 0x84 : 0x2C, NULL};
    for (unsigned i = 0; i < 10; ++i) {
        save[i].bytes = malloc(save[i].size);
        if (!save[i].bytes) {
            for (unsigned j = 0; j < i; ++j) free(save[j].bytes);
            f_0047D3C0_orig(c); return;
        }
        memcpy(save[i].bytes, G + save[i].address, save[i].size);
    }
    Cpu before = *c;
    ChaseFrame *scope = frame;
    frame = NULL;
    f_0047D3C0_orig(c); // baseline: every inner hook calls its retail original
    uint32_t reference[16]; memcpy(reference, G + f->camera, sizeof reference);
    uint16_t reference_fov = M16(f->camera + 0xC4);
    for (unsigned i = 0; i < 10; ++i) {
        memcpy(G + save[i].address, save[i].bytes, save[i].size);
        free(save[i].bytes);
    }
    *c = before;
    frame = scope;
    f_0047D3C0_orig(c);
    if (broken) MF32(f->camera + 0x30) += .25f; // deliberately corrupt one output matrix word
    unsigned different = reference_fov != M16(f->camera + 0xC4);
    for (unsigned i = 0; i < 16; ++i) different += reference[i] != M32(f->camera + 4 * i);
    static FILE *log;
    if (!log) {
        char path[1024]; snprintf(path, sizeof path, "%s/chasecam_compare.tsv", diag_dir);
        log = fopen(path, "w");
        if (log) fprintf(log, "sim\tkmh\tbroken\tdifferent_words\tfov_reference\tfov_off\treference_hex\toff_hex\n");
    }
    if (log) {
        fprintf(log, "%.6f\t%.6f\t%d\t%u\t%u\t%u\t", MF32(0x009885D8u), f->kmh, broken,
                different, reference_fov, M16(f->camera + 0xC4));
        for (unsigned i = 0; i < 16; ++i) fprintf(log, "%08x", reference[i]);
        fputc('\t', log);
        for (unsigned i = 0; i < 16; ++i) fprintf(log, "%08x", M32(f->camera + 4 * i));
        fputc('\n', log); fflush(log);
    }
    diag_event("chasecam parity: kmh=%.3f broken=%d different_words=%u", f->kmh, broken, different);
}

void f_0047D3C0(Cpu *c) {
    if (!s_chase.value && !diag_on) { f_0047D3C0_orig(c); return; }
    uint32_t mover = c->ecx, view = M32(mover + 0x10), mode = M32(mover + 0x98);
    uint32_t camera = M32(mover + 0x1C);
    // Main local player views only: never mirrors, capture cameras, NIS or other POVs.
    if ((view != 1 && view != 2) || (mode != 2 && mode != 3) ||
        camera != M32(0x009195E0u + view * 0x70 + 0x40)) {
        f_0047D3C0_orig(c); return;
    }
    ChaseFrame local = {.mover = mover, .anchor = M32(mover + 0x80), .camera = camera, .mode = mode,
                         .active = s_chase.value != 0};
    local.kmh = fabsf(MF32(local.anchor + 0x10)) * 3.6f;
    ChaseFrame *previous = frame;
    frame = &local;
    if (diag_on) {
        static unsigned stable, last_mode;
        if (last_mode != mode) { stable = 0; last_mode = mode; }
        ++stable;
        const char *compare = getenv("BOOT_DIAG_CHASE_COMPARE");
        if (compare && !local.active && stable >= 120 && stable % 120 == 0)
            chase_compare(c, &local, !strcmp(compare, "broken"));
        else f_0047D3C0_orig(c);
    } else f_0047D3C0_orig(c);
    frame = previous;
    if (diag_on && local.sampled) chase_record(&local);
}
