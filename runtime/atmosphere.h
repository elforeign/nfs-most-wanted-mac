// Local, homogeneous air transport. No particle/emitter optical properties.
#pragma once
#include <stdint.h>

enum { ATMOSPHERE_DISTANCES=64 };
typedef struct {
    float sun[3];             // world-space direction TOWARDS sun; game Z is up
    float fog_colour[3];      // authored RGB, in the scene's linear-light convention
    float fog_distance;       // metres: positive authored fog start, LUT extent only
} AtmosphereInput;
typedef struct {
    float rayleigh[3], mie_scattering[3], mie_extinction[3], hg_g;
} AtmosphereConstants;
typedef struct {
    AtmosphereInput input;
    AtmosphereConstants constants;
    float transmittance[ATMOSPHERE_DISTANCES][3];
    float scatter_integral[ATMOSPHERE_DISTANCES][3]; // (1-T)/extinction
    uint64_t builds;
    int valid;
} AtmosphereLUT;

AtmosphereConstants atmosphere_constants(void);
// Invalid inputs invalidate the cache; an unchanged normalized key does no work.
int atmosphere_update(AtmosphereLUT *lut, const AtmosphereInput *input);
// Distance is clamped to the LUT extent; phase is analytic at every view angle.
// View points from eye to scene.
int atmosphere_sample(const AtmosphereLUT *lut, float distance, const float view[3],
    float transmittance[3], float inscatter[3]);
// Snapshot the game's already evaluated fog record (six floats, FUN_00753d90).
// Pass 0 for the SP record. Split consumers must supply their own evaluated record.
// This reads only: it neither calls guest code nor installs a per-frame hook.
int atmosphere_read_game(AtmosphereInput *input, uint32_t fog_record);
