// Diagnostic rigid scene capture. No raster or lighting consumer is installed.
#pragma once
#import <Metal/Metal.h>
#include <stdint.h>

typedef struct { uint8_t stream, type, usage, index; uint16_t offset; } RTSceneElement;
enum {
    RT_SCENE_SKIN = 1 << 0, RT_SCENE_MORPH = 1 << 1,
    RT_SCENE_FOLIAGE = 1 << 2, RT_SCENE_GLASS = 1 << 3,
    RT_SCENE_ALPHA = 1 << 4, RT_SCENE_BLEND = 1 << 5,
    RT_SCENE_WATER = 1 << 6
};
typedef struct {
    uint32_t type, prims, start;
    int32_t base_vertex;
    int indexed;
    uint32_t up_vertices, up_stride, up_indices, up_index_fmt;
    const RTSceneElement *elements; unsigned element_count;
    uint64_t shader;
    uint32_t unsupported;
} RTSceneDraw;

typedef struct {
    uint64_t frame, view, target, target_generation;
    float vp[16];                      // guest row-vector view * projection
} RTSceneView;

typedef struct {
    uint64_t captures, instances, cache_hits, blas_builds, tlas_builds;
    uint64_t skinning, morphs, foliage, glass, alpha_test, blend, water;
    uint64_t unknown_program, bad_geometry, bad_transform, excluded, capacity;
    uint64_t capture_ns, geometry_bytes, as_bytes, scratch_bytes;
    uint64_t live_geometry_bytes, live_as_bytes, live_frame_bytes;
} RTSceneStats;

// Latched once, default Off. These guards precede all scene storage/device work.
int rt_scene_enabled(void);
int rt_scene_supported(id<MTLDevice> device);
void rt_scene_capture(const RTSceneDraw *draw); // game thread; snapshots before returning
void rt_scene_forget(uint64_t resource_id);
void rt_scene_reset(void);
void rt_scene_flush(id<MTLCommandBuffer> command); // active encoders must already be ended
RTSceneStats rt_scene_stats(void);

// Frame result for offline traversal checks; no production ray pass or image use.
id<MTLAccelerationStructure> rt_scene_last_tlas(void);
NSArray<id<MTLAccelerationStructure>> *rt_scene_last_blas(void);
