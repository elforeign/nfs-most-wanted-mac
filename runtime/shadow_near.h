#ifndef NFS_SHADOW_NEAR_H
#define NFS_SHADOW_NEAR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Row vectors, row-major matrices. All distances are world metres. This API
 * fits one extra near map; it never selects/replaces the existing far fit.
 * Double precision preserves texture phase during fitting and inversion. */
typedef struct ShadowNearVec3 { double x, y, z; } ShadowNearVec3;
typedef struct ShadowNearMatrix { double m[16]; } ShadowNearMatrix;
typedef struct ShadowNearBounds { ShadowNearVec3 min, max; } ShadowNearBounds;
typedef struct ShadowNearRect { double min_u, min_v, max_u, max_v; } ShadowNearRect;

typedef struct ShadowNearInput {
    ShadowNearVec3 camera_position;
    /* Supported camera envelope: arbitrary yaw/pitch/roll; slopes include
     * every supported FOV/aspect. A conservative sphere contains its corners. */
    double camera_near, camera_far, max_tan_x, max_tan_y;
    /* Explicit orthonormal right-handed basis, fixed for the fit epoch.
     * depth points away from the light (towards shadowed receivers).
     * The fitter rejects invalid bases rather than silently rotating them. */
    ShadowNearVec3 light_right, light_up, light_depth;
    ShadowNearVec3 world_origin;
    /* Receiver box in this light basis, centred at camera_position. Must
     * contain the complete supported yaw envelope. Half extents stay fixed
     * through yaw and subtexel translation; no frame-dependent tight fit. */
    ShadowNearVec3 receiver_half_extent;
    /* Conservative world AABB of ALL contributing casters, including those
     * upstream/outside the camera frustum. No scene search occurs here. */
    ShadowNearBounds contributing_casters;
    double depth_padding, depth_quantum;  /* outward quantized depth bounds */
    uint32_t resolution, max_texture_dimension; /* independent device limit */
    uint64_t max_depth_bytes;             /* D32 payload budget, not allocation */
    double guard_texels, blend_texels;
    /* Existing final far receiver matrix, including its own texture bias.
     * Can be PC or X360 LiSPSM, any invertible depth mapping. */
    ShadowNearMatrix world_to_far_texture;
} ShadowNearInput;

typedef struct ShadowNearResult {
    uint32_t resolution;
    uint64_t depth_bytes;
    ShadowNearVec3 light_right, light_up, light_depth;
    ShadowNearVec3 receiver_half_extent;
    ShadowNearVec3 snapped_origin;   /* world position at XY centre, depth 0 */
    double centre_right, centre_up;  /* relative to input world_origin */
    double half_width, half_height, pitch_right, pitch_up;
    double depth_min, depth_max;     /* relative to world_origin along depth */
    ShadowNearRect usable, blend;   /* outer usable and inner full-near UV */
    ShadowNearMatrix world_to_clip; /* XY [-1,1], depth [0,1], w=1 */
    ShadowNearMatrix world_to_texture; /* (+.5,-.5) scale, +.5+.5/N once */
    /* inverse(world_to_far_texture) * world_to_texture. Multiply the FULL
     * far homogeneous vec4, then divide near xyz by near w. Never reuse the
     * far depth or w. No additional texture-centre bias may be applied. */
    ShadowNearMatrix far_to_near;
} ShadowNearResult;

/* Pure, reentrant, no shared/player state. 1 on success; 0 on invalid input,
 * singular/ill-conditioned far matrix, insufficient coverage or allocation budget. Failure
 * clears result. Input and result must be distinct, non-overlapping objects. */
int shadow_near_fit(const ShadowNearInput *input, ShadowNearResult *result);

#ifdef __cplusplus
}
#endif

/* Render binding. Matrices are the FLOAT values actually submitted, promoted
 * to double for inspection; consumers must match frame, owner and generation.
 * Texture ownership stays with the render module until reset. A failed or
 * unfinished replay publishes nothing. Colour-mask/car consumers stay retail. */
#ifdef __cplusplus
extern "C" {
#endif
struct Cpu;
typedef struct ShadowNearBinding {
    uint64_t frame, generation, depth_allocated_bytes, colour_allocated_bytes;
    uint32_t owner, width, height, far_depth, far_colour, far_width, far_height;
    ShadowNearMatrix world_to_far_clip, world_to_far_texture;
    ShadowNearResult fit;
    uint32_t caster_count, alpha_draws, draws; /* scenery records; alpha/discard draws; all encoded draws */
} ShadowNearBinding;
void shadow_near_set_enabled(int on);
int shadow_near_enabled(void);
void shadow_near_reset(void);
void shadow_near_begin(void);
void shadow_near_replay(struct Cpu *cpu, uint32_t source, uint32_t cull);
int shadow_near_binding(uint32_t owner, uint64_t frame, ShadowNearBinding *binding);
void shadow_near_far_camera(uint32_t source, uint32_t view, uint32_t proj, int clamp_light);
void shadow_near_far_rendered(uint32_t source, uint32_t platform);
void shadow_near_pose_camera(struct Cpu *cpu, uint32_t camera, uint32_t view, uint32_t mode);
void shadow_near_pose_render(struct Cpu *cpu, uint32_t source, uint32_t cull);
void shadow_near_receiver_note(void);
int shadow_near_ab_hold(void);
int shadow_near_ab_shot(char *path, unsigned long n);
int shadow_near_ab_enabled(void);
int shadow_near_ab_frozen(void);
void shadow_near_ab_post(float *adaptation, float *exposure);
int shadow_near_ab_write_bmp(const char *path, const void *bgra, uint32_t width, uint32_t height);
#ifdef __cplusplus
}
#endif

#endif
