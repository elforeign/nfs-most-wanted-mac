// Rendered-frame metadata only. No jitter, GPU resources or image history.
#pragma once
#include <stdint.h>
#include <stddef.h>

typedef enum {
    TEMPORAL_SP, TEMPORAL_P0, TEMPORAL_P1, TEMPORAL_MIRROR, TEMPORAL_VIEW_COUNT
} TemporalViewId;
enum {
    TEMPORAL_RESET_CUT = 1u, TEMPORAL_RESET_MOVER = 2u,
    TEMPORAL_RESET_PLAYER = 4u, TEMPORAL_RESET_MODE = 8u,
    TEMPORAL_RESET_FRAME = 16u, TEMPORAL_RESET_PAUSE = 32u,
    TEMPORAL_RESET_TARGET = 64u, TEMPORAL_RESET_MATRIX = 128u,
    TEMPORAL_RESET_OVERFLOW = 256u
};
typedef struct {
    float view[16], projection[16], vp[16];
    float near_z, far_z;
} TemporalMatrices;
// The scene consumer supplies ACTUAL attachment properties at its first draw,
// not requested menu settings. generation covers resource/device recreation.
typedef struct {
    uint64_t source, depth, generation, player_epoch;
    uint32_t rect[4], width, height, samples, format, domain;
    uint32_t player, camera, mover;
} TemporalViewKey;
typedef struct {
    TemporalMatrices matrices;
    TemporalViewKey key;
    uint64_t frame, serial;
} TemporalSnapshot;
typedef struct {
    TemporalSnapshot current, previous;
    uint64_t generation;
    unsigned reset_reasons, history_valid, jitter_index;
} TemporalViewState;
typedef struct { uint32_t address; uint64_t epoch; } TemporalCarId;
#define TEMPORAL_CAR_CAPACITY 1024u

// One renderer owns this opt-in on the game thread. There is no consumer yet.
// Disabled hooks do only their guard: no guest reads, copies, lookup or allocation.
extern int temporal_state_requested;
void temporal_state_request(int enabled);
void temporal_frame_boundary(uint64_t presented_frame, unsigned flow, int paused);
void temporal_scene_mode(int split, unsigned flow, int paused);
void temporal_view_capture(TemporalViewId id, const TemporalMatrices *matrices,
                           uint32_t player, uint32_t camera, uint32_t mover);
// Call only for the first real scene draw, with this view's actual attachments.
// Repeated calls in a frame return the frozen snapshot without advancing it.
// NULL means no valid setup/scene. A reset returns a seed with history_valid=0.
const TemporalViewState *temporal_view_accept(TemporalViewId id, const TemporalViewKey *key);
// Renderer integration: pair actual attachments with the frozen capture's CPU
// ownership, without duplicating guest player/camera/mover layout knowledge.
const TemporalViewState *temporal_view_accept_target(TemporalViewId id, TemporalViewKey *key);
const TemporalViewState *temporal_view_state(TemporalViewId id);
void temporal_invalidate(unsigned reasons);
void temporal_camera_cut(uint32_t camera);
void temporal_mover_changed(uint32_t view, uint32_t mover);
void temporal_player_changed(uint32_t player);
void temporal_car_construct(uint32_t address);
void temporal_car_destroy(uint32_t address);
TemporalCarId temporal_car_identity(uint32_t address);
int temporal_car_alive(TemporalCarId identity);

// Queue records own their matrices; guest world pointers are never identities.
// part is a CRI-relative CarPartModel slot (including LOD); instance identifies
// the stable submission site, separating wheels sharing one slot/model.
#define TEMPORAL_POSE_CAPACITY 4096u
#define TEMPORAL_QUEUE_CAPACITY 4096u
typedef struct {
    TemporalCarId car;
    TemporalViewId view;
    uint32_t part, instance, model, mesh;
} TemporalPartKey;
typedef struct {
    uint64_t queue_generation, record_generation, part_generation;
    uint32_t queue_slot;
} TemporalPoseToken;
typedef struct {
    TemporalPartKey key;
    float current[16], previous[16];
    unsigned history_valid;
} TemporalPartPose;
// Start/retire a guest queue batch, including empty flushes and non-mesh appends.
void temporal_pose_queue_reset(void);
TemporalPoseToken temporal_pose_submit(unsigned slot, const TemporalPartKey *key,
                                       const float world[16]);
// Only the actual accepted scene view can resolve/stage a queued observation.
// Tokens fail closed after slot/batch reuse, part eviction, lifetime or view reset.
int temporal_pose_resolve(TemporalPoseToken token, TemporalViewId view, TemporalPartPose *out);
// Explicitly called AFTER the consumer encoded this view's real history.
// One successful commit per accepted serial. A failed/unencoded view never seeds.
int temporal_pose_commit(TemporalViewId view, uint64_t accepted_serial);
void temporal_pose_clear(void);
const TemporalPartPose *temporal_pose_draw(TemporalViewId view);
// Playback validates the guest record against its submitted owned descriptor.
void temporal_pose_record(unsigned slot, uint32_t mesh, const float world[16], TemporalViewId view);
size_t temporal_pose_storage_bytes(void);

// Guest adapters. Kept here so scene consumers need no guest layout knowledge.
struct Cpu;
void temporal_guest_capture(uint32_t view, uint32_t platform);
void temporal_guest_scene_mode(int split);
void temporal_guest_frame(uint64_t presented_frame);
void temporal_guest_pose_record(uint32_t record);
TemporalViewId temporal_guest_view_id(uint32_t view);
