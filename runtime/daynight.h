// Optional time-of-day orbit and live lighting blend.
#pragma once
#include <stdint.h>

typedef struct {
    float diffuse[3], ambient[4], specular[3];
    float environment, sky_brightness, sky[3], haze[3];
    float sky_scale, haze_scale, scatter, sun_falloff, distance_scale;
} DaynightRecord;

typedef struct {
    float phase, night, sun[3], light[3];
    int moon;
} DaynightSample;

double daynight_wrap(double hour);
void daynight_sample(double hour, float azimuth, float tilt, DaynightSample *out);
void daynight_mix(DaynightRecord *day, const DaynightRecord *night, float n);
void daynight_night_record(DaynightRecord *out);
float daynight_lamp_level(void); // car lamp level 0..1; 1 when the clock is off
float daynight_headlight_scale(void); // photometric-to-authored night radiance; 1 with clock off

// Optional MW clock provider. Without a provider the standalone lighting unit
// retains its provisional clock; registration introduces no link dependency.
typedef struct {
    double dt;
    float phase, tilt, azimuth;
    int active, race, career, paused, override;
    unsigned race_id;
    double override_hour;
} DaynightClockInput;
typedef struct {
    double hour;
    float weight;
} DaynightClockOutput;
typedef void (*DaynightClockDriver)(const DaynightClockInput *, DaynightClockOutput *);
void daynight_register_clock(DaynightClockDriver driver);

// Optional light added to each in-game car after its rig is set up (guest addresses: light context, world
// position, local-to-world matrix; level = daynight_lamp_level()). Called only while the clock drives.
typedef void (*DaynightCarLight)(uint32_t context, uint32_t position, uint32_t local_world, float level);
void daynight_register_car_light(DaynightCarLight light);
