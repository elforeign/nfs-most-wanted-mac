// materials.h - material-shader replacement layer (materials.m), shared with render.m.
#pragma once
#include <stdint.h>

#include "../host/shaders/sm_translate.h"
#include "auxlights.h"

enum { MAT_VARIANT_AUX = 0x200, MAT_AUX_VS_BUF = 20, MAT_AUX_PS_BUF = 5 };
enum { AUX_UNMATCHED, AUX_SECTION, AUX_GENERATION, AUX_VIEW, AUX_EFFECT, AUX_SPACE,
       AUX_TEXTURE, AUX_INPUT, AUX_CONSTANTS, AUX_STATE, AUX_SHADER, AUX_PIPELINE, AUX_REASONS };
typedef struct MatAuxDraw {
    AuxLightContext context;
    uint32_t section, generation, queue_generation, instance, mesh, effect, textures[5];
    float bbox[6], world[4][4], source_world[4][4];
    int valid, emitted, space;
} MatAuxDraw;
extern MatAuxDraw mat_aux_draw;
void materials_aux_record(uint32_t q);
void materials_aux_clear(void);
void mat_aux_fallback(int reason);
void mat_aux_emit(void);
int mat_aux_ab_held(void);           // BOOT_DIAG_AUX_AB: the held A/B pass draws without the scenery light pass
void mat_aux_frame(void);
int mat_aux_constants(const SmResult *vs, const float (*vsc)[4], float out[52][4]);
char *mat_aux_shader(const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint);

// Extra constants of a replacement pixel shader: fragment buffer(MAT_PS_BUF), filled per draw by the entry.
enum { MAT_PS_BUF = 4 };
typedef struct MatConsts { float k[12][4]; } MatConsts;   // car paint reads all 12; the others declare 4 and ignore the rest

#ifndef NFS_PUBLIC
#include "carbon_lamp_private.h"
#else
// The public build has no converted cars; this pass-through keeps the call sites compiling and is never selected.
#define CARBON_LAMP_MSL \
"static float4 carbon_lamp(float4 tex, float4 light, float shade, float3 specular, float3 reflection, float2 masks) {\n" \
"    return float4(light.rgb * shade * tex.rgb + specular + reflection, tex.a * light.a);\n" \
"}\n"
#endif

// Called by render.m's compile_entry (any thread) after sm_translate succeeded: the replacement MSL (malloc'd) when
// the shader's bytecode is in the table and its switch is on, else NULL. *id = table index + 1 (0 = none).
// The replacement keeps the translation's binding contract, so `refl` stays valid for binding.
char *mat_replace(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
// Lamp-only cache variant: native/Off retains mat_replace's original source.
enum { MAT_VARIANT_CARBON_LAMP = 0x107 };
int mat_carbon_lamp_draw(void);
char *mat_carbon_lamp_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
enum { MAT_VARIANT_SOFT_SMOKE = 0x104, MAT_VARIANT_SOFT_SMOKE_RESOLVED = 0x105, MAT_VARIANT_SOFT_SMOKE_LATE = 0x106 };
int mat_is_particle(int id);
int mat_soft_particle_view(void);
char *mat_soft_smoke_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
char *mat_soft_smoke_resolved_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
char *mat_soft_smoke_late_shader(const uint32_t *tokens, uint32_t n, const SmResult *refl, int *id);
// Per draw (game thread): fills a replacement's extra constants. vsc = the device's vertex shader float constants.
void mat_constants(int id, const float (*vsc)[4], MatConsts *out);
// An extra texture the replacement samples (MAT_TEX_*, 0 = none): render.m binds it at fragment texture(MAT_TEX_SLOT).
enum { MAT_TEX_SLOT = 15, MAT_TEX_RIPPLES = 1, MAT_TEX_CARNM = 2 };
int mat_flake_volume_ok(void);    // R30: the X360 32^3 noise volume (R27's) loads, render.m binds it at fragment texture(14) of the car paint PS
uint32_t mat_carnm_texture(void);   // MAT_TEX_CARNM: this car draw's tangent-space normal map (a guest D3D texture), 0 = none
int mat_extra_texture(int id);
const uint8_t *mat_ripple_frame(int i, uint32_t *w, uint32_t *h);   // RAINSPLASH frame i (0-15) as RGBA8, NULL: none

// Car paint values the PC setter FUN_006c76e0 drops (captured by the hook in materials.m before each car draw).
typedef struct MatCarPaint { float spec_power, metallic, hotspot; } MatCarPaint;
extern MatCarPaint mat_car;

// R27: host-only lightmaterials contract, independent of gameplay.x360_data.
typedef struct MatGrassParams {
    float colour[4];
    float height, shells, lod_start, scruff;
    float diffuse_space, noise_space, low_noise_space, low_noise_intensity;
    float diffuse_bias, noise_bias, curl;
    float ambient_shell[6], ambient_noise[6], diffuse_shell[6];
} MatGrassParams;
const MatGrassParams *attrib_x360_grass(uint32_t material_key);
typedef struct MatGrassDraw {
    uint32_t mesh, view, effect, textures[5];
    float bounds[6], world[4][4], world_view[3][4];
    int skip, emitted, transition;
} MatGrassDraw;
extern MatGrassDraw mat_grass_draw;       // next D3D draw of a sorted 006E0200 record; game thread only
void materials_grass_record(uint32_t q); // 006C8000 entry returning to 006E03F9 only, q = ESI
void materials_grass_clear(void);        // finish diagnostics and clear after draw / skipped record / queue exit
void materials_set_grass(int x360);
int mat_grass_enabled(void);
// Explicit replacement interface; variant 0 always remains the original PC shader.
enum { MAT_GRASS_VS_BUF = 20, MAT_GRASS_PS_BUF = 5 };
char *mat_grass_shader(const SmResult *pc, int stage, int shell, uint16_t input_int, uint16_t input_uint); // 0..4 Grass, 5..9 Transition
int mat_grass_constants(const SmResult *pcvs, const SmResult *pcps, const float (*vsc)[4], const float (*psc)[4], float out[64][4]);
uint32_t mat_grass_texture(int slot);     // queued TextureInfo -> D3D texture; opacity is slot 4, specular slot 3
int mat_grass_shadow_slot(const SmResult *pc);
const uint8_t *mat_grass_noise(uint32_t *w, uint32_t *h);
const uint8_t *mat_grass_volume(uint32_t *size); // all six tight RGBA8 mips, 32^3 -> 1^3
enum {
    GRASS_NO_GEOMETRY, GRASS_PC, GRASS_VIEW, GRASS_EFFECT, GRASS_TRANSFORM,
    GRASS_STATE, GRASS_DIFFUSE, GRASS_ASSETS, GRASS_CONSTANTS, GRASS_SHADOW,
    GRASS_SHADER, GRASS_PIPELINE, GRASS_DEPTH, GRASS_TRANSITION, GRASS_INPUT, GRASS_SKIP_COUNT
};
void mat_grass_diag_emit(int n, const float k[64][4]);
void mat_grass_diag_frame(void);           // Present, BOOT_DIAG only, including zero-material intervals

// R18: separate namespace; 0 and grass variants 1..10 retain their cache keys.
enum { MAT_VARIANT_WATER = 0x100, MAT_WATER_VS_BUF = 20, MAT_WATER_PS_BUF = 5 };
typedef struct MatWaterDraw { uint32_t mesh, view; float world[4][4]; } MatWaterDraw;
extern MatWaterDraw mat_water_draw;
void materials_set_water(int x360);
int mat_water_enabled(void);
char *mat_water_shader(const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint);
int mat_water_constants(const SmResult *pcvs, const float (*vsc)[4], float out[32][4]);
const uint8_t *mat_water_normal(uint32_t *w, uint32_t *h);
void mat_water_emit(int variant);
void mat_water_failure(int pipeline);
void mat_water_frame(void);
int mat_water_reflection_requested(void);
void mat_water_reflection_forced(void);

// R19 building glass: X360 glassreflect (09BC692C/09BC78B4) on PC glossywindow (effect 5) draws. Default PC.
enum { MAT_VARIANT_GLASS = 0x101, MAT_GLASS_VS_BUF = 20, MAT_GLASS_PS_BUF = 5 };
enum { GLASS_NO_DRAW, GLASS_PC, GLASS_VIEW, GLASS_STATE, GLASS_CONSTANTS, GLASS_SHADER, GLASS_PIPELINE, GLASS_SKIP_COUNT };
typedef struct MatGlassDraw { uint32_t mesh, view; int skip, emitted; float world_view[3][4]; } MatGlassDraw;
extern MatGlassDraw mat_glass_draw;        // next D3D draw of a 006D7660 / 006E0200 record; game thread only
void materials_glass_record(uint32_t q);   // 006C8000 entry from 006D7685 / 006E03F9, q = the record (ESI)
void materials_set_building_glass(int x360);
int mat_glass_enabled(void);
// NULL unless tokens are one of the nine PC glossywindow shaders (never glossywindowdepth/debug).
char *mat_glass_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint);
int mat_glass_constants(const SmResult *pcvs, const SmResult *pcps, const float (*vsc)[4], const float (*psc)[4], float out[32][4]);
void mat_glass_emit(int blended);
// X360 UV1.y per vertex for the glass vertex buffer with these bytes (glass_uv1.inc): row index and *out (one float per
// 36-byte vertex, GLASS_UV1_NONE where nothing corresponds), or -1. Bound at MAT_GLASS_UV1_SLOT (attribute and buffer).
#define GLASS_UV1_NONE (-1e30f)
enum { MAT_GLASS_UV1_SLOT = 29 };
int mat_glass_uv1(const uint8_t *bytes, uint32_t size, const float **out, uint32_t *matched);
void mat_glass_uv_note(int found, uint32_t vertices, uint32_t matched);
void mat_glass_failure(int pipeline);
void materials_set_particles(int x360);   // R21 NFS_PARTICLES, F10 live
void mat_glass_frame(void);                // Present: BOOT_DIAG per-second `glass:` line, including zero intervals

// R28 tree shading: X360 billboardshader standard/p0 (09BF5164/09BF6634) on PC tree (effect 6) draws whose material
// has X360 Params (trees.h). Default PC.
enum { MAT_VARIANT_TREES = 0x102, MAT_TREES_VS_BUF = 20, MAT_TREES_PS_BUF = 5 };
enum { TREES_NO_DRAW, TREES_UNMATCHED, TREES_PC, TREES_VIEW, TREES_STATE, TREES_CONSTANTS, TREES_SHADER, TREES_PIPELINE, TREES_SKIP_COUNT };
typedef struct MatTreesDraw { uint32_t mesh, view; int skip, emitted; float world_view[3][4], params[4], centre[3]; int alpha_ref; } MatTreesDraw;
extern MatTreesDraw mat_trees_draw;        // next D3D draw of a 006D7660 / 006E0200 tree record; game thread only
void materials_trees_record(uint32_t q);   // 006C8000 entry from 006D7685 / 006E03F9, q = the record (ESI)
void materials_trees_clear(void);
void materials_set_trees(int x360);
int mat_trees_enabled(void);
void mat_trees_emit(int blended);          // render.m: the replaced draw went out (blended: with its second pass)
// NULL unless tokens are the PC tree t0 VS / PS.
char *mat_trees_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint);
int mat_trees_constants(const SmResult *pcvs, const float (*vsc)[4], float out[32][4]);
void mat_trees_failure(int pipeline);
void mat_trees_frame(void);                // Present: BOOT_DIAG per-second `trees:` line, including zero intervals

// X360 worldbone (15_worldbone 09c05e24/09c07860) on the X360 arrest characters' normal-mapped draws, NFS_X360_ARREST=x360
// only. Normal, specular and env cube at fragment textures MAT_BONE_TEX..+2.
enum { MAT_VARIANT_BONE = 0x103, MAT_BONE_VS_BUF = 20, MAT_BONE_PS_BUF = 5, MAT_BONE_TEX = 12, MAT_BONE_K = 96 };
enum { BONE_NO_DRAW, BONE_PC, BONE_TEXTURE, BONE_STATE, BONE_SHADER, BONE_PIPELINE, BONE_SKIP_COUNT };
void mat_bone_car_cube(uint32_t tex);      // render.m, every draw with a cube on sampler 1: kept when it is a car draw
int mat_bone_draw(uint32_t tex[3], int *skip);   // 1: replace this draw; tex = normal, specular, env cube (guest D3D)
void mat_bone_skip(int reason);
char *mat_bone_shader(const uint32_t *tokens, uint32_t n, const SmResult *pc, int stage, uint16_t input_int, uint16_t input_uint);
int mat_bone_constants(const float (*vsc)[4], float out[MAT_BONE_K][4]);
void mat_bone_emit(void);
void mat_bone_failure(int pipeline);
void mat_bone_frame(void);                 // Present: BOOT_DIAG per-second `arrestnm:` line while worldbone draws happen
