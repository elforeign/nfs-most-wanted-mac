#pragma once
#include <stdint.h>
#include <stddef.h>
#include "underglow_photometry.h"

enum { UG_STRIPS = 4 };
enum { UG_MAX_BLOCKERS = 64 };
typedef struct UGStrip {
    UGVec3 a, b, normal;
    double length, width;
} UGStrip;

/* Stock KIT00 BMWM3GTRE46 only. Coordinates are chassis-local metres:
 * +X front, +Y left, +Z up. No ground-relative emitter placement. */
const UGStrip *ug_mount_fixture(const char *model);
int ug_strips_transform(const UGStrip source[UG_STRIPS], const float basis[16], const float position[3], UGStrip out[UG_STRIPS]);
int ug_mount_transform(const char *model, const float basis[16], const float position[3], UGStrip out[UG_STRIPS]);

typedef struct UGTriangle { UGVec3 v[3]; } UGTriangle;
/* A blocker intersects an interval of the finite emitter, not just its centre.
 * -1 = malformed input, 0 = unobstructed, 1 = closed occluded interval. */
int ug_blocked_interval(UGVec3 receiver, UGVec3 a, UGVec3 b, const UGTriangle *triangle, double interval[2]);
/* Complete bounded blocker set required. Failure clears the contribution. */
int ug_visible_lux(double flux, const UGStrip *strip, UGVec3 receiver, UGVec3 normal,
                   const UGTriangle *triangles, unsigned count, double *lux);

typedef struct UGVisibility {
    uint64_t frame, generation;
    UGVec3 centre;
    double radius;
    unsigned count, complete, grounded;
    UGTriangle triangles[UG_MAX_BLOCKERS];
} UGVisibility;
/* Real receiver containment plus exact frame/generation ownership. */
int ug_receiver_admit(const UGVisibility *snapshot, uint64_t frame, uint64_t generation,
                       UGVec3 receiver, UGVec3 normal);
/* Finite-source shader math used by receiver programs and float-target checks. */
const char *ug_render_msl(void);

#ifndef UG_RENDER_CORE_ONLY
#include "runtime.h"
typedef struct UGCarCapture {
    struct UGCarCapture *previous;
    uint32_t self, view, view_id, body, profile, handle, preset;
    UGStrip mounts[UG_STRIPS];
    float basis[16], position[3], wheels[2][64];
    float tyre_world[4][16];
    unsigned front_tyres, rear_tyres;
    int valid;
} UGCarCapture;
/* Observation is requested only by the bounded delegated scene fixture. */
void underglow_render_car_begin(const Cpu *cpu, UGCarCapture *capture);
void underglow_render_car_end(const Cpu *cpu, const UGCarCapture *capture);
void underglow_render_part(const Cpu *cpu);
int underglow_render_scene_step(const char *name, uint64_t elapsed);
int underglow_render_observing(void);
void underglow_render_receiver_record(uint32_t queue);
void underglow_render_receiver_clear(void);
typedef struct UGMeshView {
    const uint8_t *vertices, *indices;
    size_t vertex_bytes, index_bytes;
    uint32_t stride, position_offset, index_width, start, primitives;
    int32_t base_vertex;
    uint64_t vertex_generation, index_generation, shader;
    uint32_t vertex_buffer, index_buffer;
    float wvp_columns[16];
    uint32_t wvp_valid, texture[2];
} UGMeshView;
void underglow_render_mesh_observe(const UGMeshView *mesh);
#endif

