#include "temporal_state.h"
#include <math.h>
#include <string.h>

int temporal_state_requested;
typedef struct {
    TemporalViewState state;
    TemporalMatrices pending;
    uint32_t player, camera, mover;
    uint64_t pending_frame;
    unsigned captured, accepted, seed_valid;
} View;
static View views[TEMPORAL_VIEW_COUNT];
static TemporalCarId cars[TEMPORAL_CAR_CAPACITY];
static uint64_t constructor_epoch, frame = 1, last_present;
static int split_mode, racing;

typedef struct {
    TemporalPartKey key;
    uint64_t generation, touched_frame;
    struct { float world[16]; uint64_t frame, serial, view_generation; } pose[2];
    uint64_t staged_frame, staged_serial, staged_generation;
    float staged[16];
} Part;
typedef struct {
    TemporalPartKey key;
    float world[16];
    uint64_t frame, generation, queue_generation, part_generation;
    unsigned part;
} QueuedPose;
static Part parts[TEMPORAL_POSE_CAPACITY];
static QueuedPose pose_queue[TEMPORAL_QUEUE_CAPACITY];
static uint64_t pose_generation, queue_generation, record_generation;
static uint64_t committed_serial[TEMPORAL_VIEW_COUNT];
static TemporalPoseToken draw_token;
static TemporalViewId draw_view = TEMPORAL_VIEW_COUNT;
static TemporalPartPose draw_pose;
_Static_assert(sizeof parts + sizeof pose_queue < 2 * 1024 * 1024, "bounded pose metadata");

static void reset_poses(void) {
    memset(parts, 0, sizeof parts);
    memset(pose_queue, 0, sizeof pose_queue);
    memset(committed_serial, 0, sizeof committed_serial);
    ++queue_generation; // tokens cannot revive across opt-in changes
    draw_view = TEMPORAL_VIEW_COUNT;
}

static int view_id(TemporalViewId id) { return (unsigned)id < TEMPORAL_VIEW_COUNT; }
static void invalidate_view(View *v, unsigned reason) {
#ifndef TEMPORAL_TEST_DISABLE_INVALIDATION
    v->seed_valid = 0;
    v->state.history_valid = 0;
    v->state.jitter_index = 0;
    v->state.reset_reasons |= reason;
    ++v->state.generation;
#else
    (void)v; (void)reason;
#endif
}
void temporal_invalidate(unsigned reasons) {
    if (!temporal_state_requested) return;
    for (unsigned i = 0; i < TEMPORAL_VIEW_COUNT; ++i) invalidate_view(&views[i], reasons);
}
void temporal_state_request(int enabled) {
    enabled = !!enabled;
    if (enabled == temporal_state_requested) return;
    memset(views, 0, sizeof views);
    memset(cars, 0, sizeof cars);
    reset_poses();
    frame = 1; last_present = 0; split_mode = 0; racing = 0;
    // Epochs survive disable/re-enable: a retained token can never become live again.
    temporal_state_requested = enabled;
}
void temporal_scene_mode(int split, unsigned flow, int paused) {
    if (!temporal_state_requested) return;
    split = !!split;
    int active = flow == 6 && !paused;
    if (split != split_mode) temporal_invalidate(TEMPORAL_RESET_MODE);
    if (active != racing) temporal_invalidate(TEMPORAL_RESET_PAUSE);
    split_mode = split; racing = active;
}
void temporal_frame_boundary(uint64_t presented, unsigned flow, int paused) {
    if (!temporal_state_requested) return;
    if (presented != frame || (last_present && presented != last_present + 1))
        temporal_invalidate(TEMPORAL_RESET_FRAME);
    last_present = presented;
    frame = presented + 1;
    temporal_scene_mode(split_mode, flow, paused);
}
void temporal_camera_cut(uint32_t camera) {
    if (!temporal_state_requested || !camera) return;
    for (unsigned i = 0; i < TEMPORAL_VIEW_COUNT; ++i) {
        View *v = &views[i];
        if (v->camera == camera || v->state.current.key.camera == camera)
            invalidate_view(v, TEMPORAL_RESET_CUT);
    }
}
void temporal_mover_changed(uint32_t view, uint32_t mover) {
    if (!temporal_state_requested) return;
    for (unsigned i = 0; i < TEMPORAL_VIEW_COUNT; ++i) {
        View *v = &views[i];
        uint32_t source = i == TEMPORAL_MIRROR ? 0x919730u : i == TEMPORAL_P1 ? 0x9196C0u : 0x919650u;
        if ((view && view == source) || (mover && (v->mover == mover || v->state.current.key.mover == mover)))
            invalidate_view(v, TEMPORAL_RESET_MOVER);
    }
}
void temporal_player_changed(uint32_t player) {
    if (!temporal_state_requested || !player) return;
    for (unsigned i = 0; i < TEMPORAL_VIEW_COUNT; ++i) {
        View *v = &views[i];
        if (v->player == player || v->state.current.key.player == player)
            invalidate_view(v, TEMPORAL_RESET_PLAYER);
    }
}
static double determinant(const float *m) {
    double a[4][4], det = 1;
    for (unsigned i = 0; i < 16; ++i) {
        if (!isfinite(m[i]) || fabsf(m[i]) > 1e10f) return 0;
        a[i / 4][i % 4] = m[i];
    }
    for (unsigned k = 0; k < 4; ++k) {
        unsigned p = k;
        for (unsigned i = k + 1; i < 4; ++i) if (fabs(a[i][k]) > fabs(a[p][k])) p = i;
        if (fabs(a[p][k]) < 1e-12) return 0;
        if (p != k) {
            for (unsigned j = k; j < 4; ++j) { double t = a[k][j]; a[k][j] = a[p][j]; a[p][j] = t; }
            det = -det;
        }
        det *= a[k][k];
        for (unsigned i = k + 1; i < 4; ++i) {
            double f = a[i][k] / a[k][k];
            for (unsigned j = k + 1; j < 4; ++j) a[i][j] -= f * a[k][j];
        }
    }
    return det;
}
static int valid_matrices(const TemporalMatrices *m) {
    return m && isfinite(m->near_z) && isfinite(m->far_z) && m->near_z > 0 && m->far_z > m->near_z &&
        fabs(determinant(m->view)) > 1e-8 && fabs(determinant(m->projection)) > 1e-8 &&
        fabs(determinant(m->vp)) > 1e-8;
}
static int discontinuous(const TemporalMatrices *a, const TemporalMatrices *b) {
    // Conservative fallback for cuts not signalled by the camera flag: >50 units
    // of view translation, >60 degrees of rotation, or a >10% projection jump.
    double distance = 0;
    for (unsigned i = 12; i < 15; ++i) { double d = a->view[i] - b->view[i]; distance += d * d; }
    if (distance > 2500 || a->near_z != b->near_z || a->far_z != b->far_z) return 1;
    for (unsigned r = 0; r < 3; ++r) {
        double dot = 0;
        for (unsigned j = 0; j < 3; ++j) dot += a->view[r * 4 + j] * b->view[r * 4 + j];
        if (dot < 0.5) return 1;
    }
    for (unsigned i = 0; i < 16; ++i)
        if (fabsf(a->projection[i] - b->projection[i]) > 0.1f * fmaxf(0.01f, fabsf(b->projection[i]))) return 1;
    return 0;
}
void temporal_view_capture(TemporalViewId id, const TemporalMatrices *m, uint32_t player, uint32_t camera, uint32_t mover) {
    if (!temporal_state_requested || !view_id(id)) return;
    View *v = &views[id];
    if (v->accepted && v->state.current.frame == frame) return; // frozen at first actual draw
    v->player = player; v->camera = camera; v->mover = mover;
    v->captured = 0;
    if (!camera || !valid_matrices(m)) { invalidate_view(v, TEMPORAL_RESET_MATRIX); return; }
    v->pending = *m;
    v->pending_frame = frame;
    v->captured = 1;
}
static int same_target(const TemporalViewKey *a, const TemporalViewKey *b) {
    return a->source == b->source && a->depth == b->depth && a->generation == b->generation &&
        !memcmp(a->rect, b->rect, sizeof a->rect) && a->width == b->width && a->height == b->height &&
        a->samples == b->samples && a->format == b->format && a->domain == b->domain;
}
const TemporalViewState *temporal_view_accept(TemporalViewId id, const TemporalViewKey *key) {
    if (!temporal_state_requested || !view_id(id) || !key) return NULL;
    View *v = &views[id];
    if (!racing || (id != TEMPORAL_MIRROR && (split_mode ? id == TEMPORAL_SP : id != TEMPORAL_SP))) return NULL;
    if (!v->captured || v->pending_frame != frame) return NULL;
    if (!key->source || !key->depth || !key->width || !key->height || !key->samples || !key->format ||
        !key->rect[2] || !key->rect[3] || key->rect[0] > key->width || key->rect[2] > key->width - key->rect[0] ||
        key->rect[1] > key->height || key->rect[3] > key->height - key->rect[1] ||
        key->player != v->player || key->camera != v->camera || key->mover != v->mover) {
        invalidate_view(v, TEMPORAL_RESET_TARGET); return NULL;
    }
    TemporalViewState *s = &v->state;
    if (v->accepted && s->current.frame == frame) {
        if (!same_target(key, &s->current.key)) { invalidate_view(v, TEMPORAL_RESET_TARGET); return NULL; }
        if (key->player_epoch != s->current.key.player_epoch) { invalidate_view(v, TEMPORAL_RESET_PLAYER); return NULL; }
        return s;
    }
    if (v->accepted) {
        if (s->current.frame + 1 != frame) invalidate_view(v, TEMPORAL_RESET_FRAME);
        if (!same_target(key, &s->current.key)) invalidate_view(v, TEMPORAL_RESET_TARGET);
        if (key->player != s->current.key.player || key->player_epoch != s->current.key.player_epoch)
            invalidate_view(v, TEMPORAL_RESET_PLAYER);
        if (key->camera != s->current.key.camera) invalidate_view(v, TEMPORAL_RESET_CUT);
        if (key->mover != s->current.key.mover) invalidate_view(v, TEMPORAL_RESET_MOVER);
        if (discontinuous(&v->pending, &s->current.matrices)) invalidate_view(v, TEMPORAL_RESET_MATRIX);
    }
    s->previous = s->current;
    s->history_valid = v->seed_valid && id != TEMPORAL_MIRROR;
    s->jitter_index = s->history_valid ? (s->jitter_index + 1) % 8 : 0;
    uint64_t serial = s->current.serial + 1;
    s->current = (TemporalSnapshot){.matrices = v->pending, .key = *key, .frame = frame, .serial = serial};
    v->accepted = 1;
    v->seed_valid = id != TEMPORAL_MIRROR;
    return s;
}
const TemporalViewState *temporal_view_state(TemporalViewId id) {
    return temporal_state_requested && view_id(id) ? &views[id].state : NULL;
}
const TemporalViewState *temporal_view_accept_target(TemporalViewId id, TemporalViewKey *key) {
    if (!temporal_state_requested || !view_id(id) || !key) return NULL;
    key->player=views[id].player; key->camera=views[id].camera; key->mover=views[id].mover;
    return temporal_view_accept(id,key);
}
void temporal_car_construct(uint32_t address) {
    if (!temporal_state_requested || !address) return;
    unsigned empty = TEMPORAL_CAR_CAPACITY;
    for (unsigned i = 0; i < TEMPORAL_CAR_CAPACITY; ++i) {
        if (cars[i].address == address) { empty = i; break; }
        if (!cars[i].address && empty == TEMPORAL_CAR_CAPACITY) empty = i;
    }
    if (empty == TEMPORAL_CAR_CAPACITY) {
        memset(cars, 0, sizeof cars); // unobserved live allocations remain untracked, never inherit a pose
        temporal_invalidate(TEMPORAL_RESET_OVERFLOW);
        empty = 0;
    }
    cars[empty] = (TemporalCarId){address, ++constructor_epoch};
}
void temporal_car_destroy(uint32_t address) {
    if (!temporal_state_requested || !address) return;
    for (unsigned i = 0; i < TEMPORAL_CAR_CAPACITY; ++i)
        if (cars[i].address == address) { cars[i] = (TemporalCarId){0}; return; }
}
TemporalCarId temporal_car_identity(uint32_t address) {
    if (temporal_state_requested && address)
        for (unsigned i = 0; i < TEMPORAL_CAR_CAPACITY; ++i) if (cars[i].address == address) return cars[i];
    return (TemporalCarId){0};
}
int temporal_car_alive(TemporalCarId id) {
    return id.address && id.epoch && temporal_car_identity(id.address).epoch == id.epoch;
}

size_t temporal_pose_storage_bytes(void) { return sizeof parts + sizeof pose_queue; }
void temporal_pose_clear(void) { draw_view = TEMPORAL_VIEW_COUNT; }
void temporal_pose_queue_reset(void) {
    if (!temporal_state_requested) return;
    ++queue_generation;
    temporal_pose_clear();
}
static int same_part(const TemporalPartKey *a, const TemporalPartKey *b) {
    return a->car.address == b->car.address && a->car.epoch == b->car.epoch &&
        a->view == b->view && a->part == b->part && a->instance == b->instance &&
        a->model == b->model && a->mesh == b->mesh;
}
static unsigned part_hash(const TemporalPartKey *k) {
    uint64_t h = k->car.epoch ^ ((uint64_t)k->car.address << 32);
    h = h * 1099511628211ull ^ k->view;
    h = h * 1099511628211ull ^ k->part;
    h = h * 1099511628211ull ^ k->instance;
    h = h * 1099511628211ull ^ k->model;
    h = h * 1099511628211ull ^ k->mesh;
    return (unsigned)(h ^ (h >> 32)) % TEMPORAL_POSE_CAPACITY;
}
static unsigned find_part(const TemporalPartKey *key) {
    unsigned base = part_hash(key), spare = TEMPORAL_POSE_CAPACITY;
    for (unsigned n = 0; n < TEMPORAL_POSE_CAPACITY; ++n) {
        unsigned i = (base + n) % TEMPORAL_POSE_CAPACITY;
        Part *p = &parts[i];
        if (!p->generation) { if (spare == TEMPORAL_POSE_CAPACITY) spare = i; break; }
        if (same_part(&p->key, key)) return i;
        // Keep this and the preceding rendered frame. Eviction always changes
        // generation, so queued tokens cannot follow an unrelated replacement.
        if (spare == TEMPORAL_POSE_CAPACITY && p->touched_frame + 1 < frame) spare = i;
    }
    if (spare != TEMPORAL_POSE_CAPACITY) {
        parts[spare] = (Part){.key = *key, .generation = ++pose_generation};
    }
    return spare; // full: leave the draw untracked, never alias a live part
}
static int valid_world(const float *m) {
    return m && fabs(determinant(m)) > 1e-8;
}
TemporalPoseToken temporal_pose_submit(unsigned slot, const TemporalPartKey *key, const float world[16]) {
    TemporalPoseToken token = {0};
    if (!temporal_state_requested || slot >= TEMPORAL_QUEUE_CAPACITY) return token;
    QueuedPose *q = &pose_queue[slot];
    *q = (QueuedPose){.generation = ++record_generation, .queue_generation = queue_generation};
    if (!key || !view_id(key->view) || key->view == TEMPORAL_MIRROR ||
        !key->part || !key->model || !key->mesh || !temporal_car_alive(key->car) || !valid_world(world)) return token;
    unsigned i = find_part(key);
    if (i == TEMPORAL_POSE_CAPACITY) return token;
    parts[i].touched_frame = frame;
    q->key = *key; q->frame = frame; q->part = i; q->part_generation = parts[i].generation;
    memcpy(q->world, world, sizeof q->world);
    return (TemporalPoseToken){q->queue_generation, q->generation, q->part_generation, slot};
}
int temporal_pose_resolve(TemporalPoseToken t, TemporalViewId id, TemporalPartPose *out) {
    if (out) memset(out, 0, sizeof *out);
    if (!temporal_state_requested || !out || !view_id(id) || id == TEMPORAL_MIRROR ||
        t.queue_slot >= TEMPORAL_QUEUE_CAPACITY) return 0;
    QueuedPose *q = &pose_queue[t.queue_slot];
    if (!q->frame || q->part >= TEMPORAL_POSE_CAPACITY) return 0;
    Part *p = &parts[q->part];
#ifndef TEMPORAL_TEST_STALE_GENERATION
    if (!t.record_generation || t.queue_generation != queue_generation || q->queue_generation != queue_generation ||
        t.record_generation != q->generation || t.part_generation != p->generation || q->part_generation != p->generation) return 0;
#endif
    View *v = &views[id];
    TemporalViewState *s = &v->state;
    if (q->frame != frame || q->key.view != id || !same_part(&p->key, &q->key) ||
        !temporal_car_alive(q->key.car) || !racing || !v->accepted || s->current.frame != frame ||
        !v->seed_valid) return 0;
    out->key = q->key;
    memcpy(out->current, q->world, sizeof out->current);
    memcpy(out->previous, q->world, sizeof out->previous); // invalid seeds have zero motion
    unsigned prev = (unsigned)((s->current.serial - 1) & 1);
    out->history_valid = s->history_valid && p->pose[prev].frame + 1 == frame &&
        p->pose[prev].serial + 1 == s->current.serial && p->pose[prev].view_generation == s->generation;
    if (out->history_valid) memcpy(out->previous, p->pose[prev].world, sizeof out->previous);
    // Multiple materials/passes for a rigid part must agree. Ambiguous transforms
    // fail closed instead of allowing playback order to choose its next pose.
    if (p->staged_frame == frame && p->staged_serial == s->current.serial &&
        p->staged_generation == s->generation && memcmp(p->staged, q->world, sizeof p->staged)) {
        p->staged_generation = UINT64_MAX;
        out->history_valid = 0;
    } else if (p->staged_frame != frame || p->staged_serial != s->current.serial) {
        p->staged_frame = frame; p->staged_serial = s->current.serial; p->staged_generation = s->generation;
        memcpy(p->staged, q->world, sizeof p->staged);
    }
    if (p->staged_generation != s->generation) out->history_valid = 0;
#ifdef TEMPORAL_TEST_COMMIT_EACH_DRAW
    // Deliberately broken: promote each draw into the previous-frame bank.
    memcpy(p->pose[prev].world, q->world, sizeof q->world);
    p->pose[prev].frame = frame - 1; p->pose[prev].serial = s->current.serial - 1;
    p->pose[prev].view_generation = s->generation;
#endif
    return 1;
}
int temporal_pose_commit(TemporalViewId id, uint64_t serial) {
    if (!temporal_state_requested || !view_id(id) || id == TEMPORAL_MIRROR) return 0;
    View *v = &views[id]; TemporalViewState *s = &v->state;
    if (!racing || !v->accepted || !v->seed_valid || s->current.frame != frame ||
        serial != s->current.serial || committed_serial[id] == serial) return 0;
    committed_serial[id] = serial;
    unsigned bank = (unsigned)(serial & 1);
    for (unsigned i = 0; i < TEMPORAL_POSE_CAPACITY; ++i) {
        Part *p = &parts[i];
        if (p->key.view != id || p->staged_frame != frame || p->staged_serial != serial ||
            p->staged_generation != s->generation || !temporal_car_alive(p->key.car)) continue;
        memcpy(p->pose[bank].world, p->staged, sizeof p->staged);
        p->pose[bank].frame = frame; p->pose[bank].serial = serial; p->pose[bank].view_generation = s->generation;
    }
    return 1;
}
void temporal_pose_record(unsigned slot, uint32_t mesh, const float world[16], TemporalViewId id) {
    temporal_pose_clear();
    if (!temporal_state_requested || slot >= TEMPORAL_QUEUE_CAPACITY || !world) return;
    QueuedPose *q = &pose_queue[slot];
    if (q->key.view != id || q->key.mesh != mesh || memcmp(world, q->world, sizeof q->world)) return;
    draw_token = (TemporalPoseToken){q->queue_generation, q->generation, q->part_generation, slot};
    draw_view = id;
}
const TemporalPartPose *temporal_pose_draw(TemporalViewId id) {
    return temporal_state_requested && draw_view == id && temporal_pose_resolve(draw_token, id, &draw_pose) ? &draw_pose : NULL;
}

#ifndef TEMPORAL_STATE_STANDALONE
#include "host.h"
int split_view_rect(uint32_t view, uint32_t rect[4]);
static uint32_t local_player(unsigned slot) {
    uint32_t list = M32(0x0092D87Cu), n = M32(0x0092D884u);
    for (uint32_t i = 0; list && i < n; ++i) {
        uint32_t p = M32(list + 4 * i);
        if (p && M32(p + 0x1C) == slot) return p;
    }
    return 0;
}
static int paused(void) {
    uint32_t sim = M32(0x009885E0u);
    return sim && M32(sim + 0x2C) == 4;
}
TemporalViewId temporal_guest_view_id(uint32_t view) {
    if (view == 0x919650u) return split_mode ? TEMPORAL_P0 : TEMPORAL_SP;
    if (view == 0x9196C0u && split_mode) return TEMPORAL_P1;
    return TEMPORAL_VIEW_COUNT; // reflection/mirror/shadow can never stage a car pose
}
void temporal_guest_pose_record(uint32_t q) {
    if (!temporal_state_requested) return;
    temporal_pose_clear();
    if (q < 0x0093E878u || (q - 0x0093E878u) % 0x44) return;
    unsigned slot = (q - 0x0093E878u) / 0x44;
    if (slot >= TEMPORAL_QUEUE_CAPACITY || slot >= M32(0x00982CDCu)) return;
    if (M32(q + 4) || M32(q + 32)) return; // non-rigid/skinned records have no deformation history
    uint32_t view = M32(0x00982A20u), world = M32(q + 28);
    // CUR_VIEW is the installed descriptor: entry +0, source view number +4.
    uint32_t source = view && M32(view + 4) < 32 ? 0x9195E0u + M32(view + 4) * 0x70u : 0;
    TemporalViewId id = source && !M32(view) && M32(source + 0x60) == view ?
        temporal_guest_view_id(source) : TEMPORAL_VIEW_COUNT;
    temporal_pose_record(slot, M32(q), world ? (const float *)(G + world) : NULL, id);
}
void temporal_guest_frame(uint64_t presented) {
    if (temporal_state_requested) temporal_frame_boundary(presented, M32(0x00925E90u), paused());
}
void temporal_guest_scene_mode(int split) {
    if (temporal_state_requested) temporal_scene_mode(split, M32(0x00925E90u), paused());
}
void temporal_guest_capture(uint32_t view, uint32_t platform) {
    if (!temporal_state_requested) return;
    temporal_guest_scene_mode(split_view_rect(0x919650u, NULL) && split_view_rect(0x9196C0u, NULL));
    TemporalViewId id;
    if (view == 0x919730u) id = TEMPORAL_MIRROR;
    else if (view == 0x919650u) id = split_mode ? TEMPORAL_P0 : TEMPORAL_SP;
    else if (view == 0x9196C0u && split_mode) id = TEMPORAL_P1;
    else return; // captures, reflections and shadows never produce temporal history
    uint32_t cam = M32(view + 0x40), link = M32(view + 0x44);
    uint32_t mover = link && link != view + 0x44 ? link - 4 : 0;
    TemporalMatrices m = {0};
    if (platform && cam) {
        memcpy(m.view, G + platform, 64);
        memcpy(m.projection, G + platform + 0x40, 64);
        memcpy(m.vp, G + platform + 0x80, 64);
        m.near_z = -m.projection[14] / m.projection[10];
        m.far_z = -m.projection[14] / (m.projection[10] - 1);
    }
    temporal_view_capture(id, &m, local_player(id == TEMPORAL_P1), cam, mover);
}

// Observe the cut flag before the translated function clears it, even if the
// Camera allocation/pose is unchanged. The original ABI/register result survives.
void f_004700D0_orig(Cpu *c);
void f_004700D0(Cpu *c) {
    if (temporal_state_requested && M8(c->ecx + 0x2A0)) temporal_camera_cut(c->ecx);
    f_004700D0_orig(c);
}
void f_00502980_orig(Cpu *c);
void f_00502980(Cpu *c) {
    if (temporal_state_requested) temporal_mover_changed(c->ecx, arg(c, 0));
    f_00502980_orig(c);
}
void f_005029B0_orig(Cpu *c);
void f_005029B0(Cpu *c) {
    if (temporal_state_requested) temporal_mover_changed(c->ecx, arg(c, 0));
    f_005029B0_orig(c);
}
void f_00470A10_orig(Cpu *c);
void f_00470A10(Cpu *c) {
    if (temporal_state_requested) temporal_mover_changed(0, c->ecx);
    f_00470A10_orig(c);
}
void f_007556C0_orig(Cpu *c);
void f_007556C0(Cpu *c) {
    if (temporal_state_requested) temporal_car_destroy(c->ecx);
    f_007556C0_orig(c);
}
// IPlayer lives at LocalPlayer+0x4C. Construction catches same-address reuse;
// attach/detach use IAttachable at +0x38 and also catch car replacement while
// the player allocation lives (IPlayer - IAttachable = 0x14).
void f_006FCA30_orig(Cpu *c);
void f_006FCA30(Cpu *c) {
    if (temporal_state_requested) temporal_player_changed(c->ecx + 0x4C);
    f_006FCA30_orig(c);
}
void f_006FBF20_orig(Cpu *c);
void f_006FBF20(Cpu *c) {
    if (temporal_state_requested) temporal_player_changed(c->ecx + 0x14);
    f_006FBF20_orig(c);
}
void f_006F8200_orig(Cpu *c);
void f_006F8200(Cpu *c) {
    if (temporal_state_requested) temporal_player_changed(c->ecx + 0x14);
    f_006F8200_orig(c);
}
#endif
