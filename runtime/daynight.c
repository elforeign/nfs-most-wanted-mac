// An optional full orbit; the original advance and blend run unchanged when Off.
#include "daynight.h"
#include "game_select.h"
#include <math.h>

// Optional private palette; the exported tree builds with its own chosen sky.
#if __has_include("daynight_private.inc")
#include "daynight_private.inc"
#else
#define DN_SKY_R 0.025f
#define DN_SKY_G 0.045f
#define DN_SKY_B 0.075f
#define DN_SKY_BRIGHTNESS 0.0f
#define DN_ENVIRONMENT 0.15f
#endif

#define PI 3.14159265358979323846

double daynight_wrap(double hour) {
    if (!isfinite(hour)) return 12;
    hour = fmod(hour, 24);
    return hour < 0 ? hour + 24 : hour;
}

static float smooth(float x) {
    x = fmaxf(0, fminf(1, x));
    return x * x * (3 - 2 * x);
}

void daynight_sample(double hour, float azimuth, float tilt, DaynightSample *out) {
    hour = daynight_wrap(hour);
    out->phase = 0.05f + 0.9f * fmaxf(0, fminf(1, (float)((hour - 6) / 12)));
    out->night = hour < 12 ? 1 - smooth((float)((hour - 5) / 2))
                           : smooth((float)((hour - 17) / 2));
    double a = (hour - 12) * PI / 12;
    double s = sin(a), z = cos(a), ca = cos(azimuth), sa = sin(azimuth);
    // Rotate a unit orbit about Z, then tilt: sunrise and sunset oppose.
    out->sun[0] = (float)(ca * s - sa * sin(tilt) * z);
    out->sun[1] = (float)(sa * s + ca * sin(tilt) * z);
    out->sun[2] = (float)(cos(tilt) * z);
    out->moon = hour < 6 || hour > 18;
    for (int i = 0; i < 3; ++i) out->light[i] = out->sun[i] * (out->moon ? -1 : 1);
}

void daynight_night_record(DaynightRecord *out) {
    *out = (DaynightRecord){
        .diffuse = {0.025f, 0.035f, 0.055f},
        .ambient = {0.012f, 0.018f, 0.035f, 4},
        .specular = {0.025f, 0.035f, 0.055f},
        .environment = DN_ENVIRONMENT, .sky_brightness = DN_SKY_BRIGHTNESS,
        .sky = {DN_SKY_R, DN_SKY_G, DN_SKY_B},
        .haze = {0.012f, 0.020f, 0.045f},
        .sky_scale = 1, .haze_scale = 0.15f, .scatter = 0.05f,
        .sun_falloff = 8, .distance_scale = 1
    };
}

static float mix(float day, float night, float n) {
    // Explicit endpoints avoid cancellation and preserve the unmodified block.
    return n <= 0 ? day : n >= 1 ? night : day + n * (night - day);
}

void daynight_mix(DaynightRecord *day, const DaynightRecord *night, float n) {
    for (int i = 0; i < 3; ++i) {
        day->diffuse[i] = mix(day->diffuse[i], night->diffuse[i], n);
        day->specular[i] = mix(day->specular[i], night->specular[i], n);
        day->sky[i] = mix(day->sky[i], night->sky[i], n);
        day->haze[i] = mix(day->haze[i], night->haze[i], n);
    }
    for (int i = 0; i < 4; ++i)
        day->ambient[i] = mix(day->ambient[i], night->ambient[i], n);
#define FIELD(f) day->f = mix(day->f, night->f, n)
    FIELD(environment); FIELD(sky_brightness); FIELD(sky_scale); FIELD(haze_scale);
    FIELD(scatter); FIELD(sun_falloff); FIELD(distance_scale);
#undef FIELD
}

#if NFS_GAME == NFS_GAME_MW
#include "host.h"
#include <stdlib.h>
#include <strings.h>

#define TOD_POINTER 0x009B392Cu
#define FLOW 0x00925E90u
#define REAL_DT 0x009259BCu
#define SECONDS_PER_GAME_HOUR 150.0 // 60 real minutes per full day

#define CAR_RIG 0x008F2E70u // ShaperLightsCarsInGame: the fixed rig of every in-game car

static int enabled, fixed, cars_enabled;
static double fixed_hour;
// Car rig scale from the last blend: sun light 0 by the sun's, fills 1-2 by the sky's luminance change.
static struct { float sun, sky; int ready; } car_light = {1, 1, 0};
static DaynightClockDriver clock_driver;
static float clock_weight = 1;
void daynight_register_clock(DaynightClockDriver driver) { clock_driver = driver; }
static DaynightCarLight car_local_light;
void daynight_register_car_light(DaynightCarLight light) { car_local_light = light; }
static struct {
    uint32_t object;
    double hour;
    float phase;
    int ready;
} clock_state;

__attribute__((constructor)) static void daynight_configure(void) {
    fixed = 0;
    const char *e = getenv("NFS_DAYNIGHT");
    enabled = e && (!strcmp(e, "1") || !strcasecmp(e, "on"));
    e = getenv("NFS_DAYNIGHT_CARS");
    cars_enabled = enabled && e && (!strcmp(e, "1") || !strcasecmp(e, "on"));
    e = getenv("NFS_TEST_DAYNIGHT_HOUR");
    if (enabled && e && *e) {
        char *end;
        double h = strtod(e, &end);
        if (!*end && isfinite(h) && h >= 0 && h <= 24) {
            fixed = 1;
            fixed_hour = daynight_wrap(h);
        }
    }
}

// Car lamp level L = min(1, 2n): full from 18:00 to 06:00, 0 from 07:00 to 17:00. 1 while the clock is
// off or not driving (front end, Off), so retail lamps are unchanged.
float daynight_lamp_level(void) {
    if (!enabled || !clock_state.ready) return 1;
    DaynightSample s;
    daynight_sample(clock_state.hour, 0, 0, &s);
    return fminf(1, 2 * s.night);
}

static float read_float(uint32_t a) { float f; memcpy(&f, G + a, 4); return f; }
static void write_float(uint32_t a, float f) { memcpy(G + a, &f, 4); }

// Only fields actually filled by 00769580; padding and other object state survive.
static void record_io(uint32_t tod, DaynightRecord *r, int write) {
#define FIELD(offset, field) do { if (write) memcpy(G + tod + offset, &r->field, sizeof(r->field)); \
                                else memcpy(&r->field, G + tod + offset, sizeof(r->field)); } while (0)
    FIELD(0x20, diffuse); FIELD(0x30, ambient); FIELD(0x40, specular);
    FIELD(0x60, environment); FIELD(0x64, sky_brightness);
    FIELD(0x70, sky); FIELD(0x80, haze); FIELD(0x90, sky_scale);
    FIELD(0x94, haze_scale); FIELD(0x98, scatter); FIELD(0x9c, sun_falloff);
    FIELD(0xa0, distance_scale);
#undef FIELD
}

static void sample_object(uint32_t tod, DaynightSample *s) {
    daynight_sample(clock_state.hour, read_float(tod + 0xc), read_float(tod + 0x10), s);
}

static void drive(uint32_t tod) {
    float phase = read_float(tod + 8);
    if (clock_driver) {
        uint32_t race = M32(0x0091E000u), sim = M32(0x009885E0u);
        DaynightClockInput input = {.phase = phase, .dt = read_float(REAL_DT),
            .tilt = read_float(tod + 0x10), .azimuth = read_float(tod + 0xc), .active = 1,
            .race = race && M32(race + 0x1968) != 0,
            .race_id = race ? M32(race + 0x1968) : 0,
            .career = race ? (int)M32(race + 0x1964) : 0,
            .paused = sim && M32(sim + 0x2c) == 4,
            .override = fixed, .override_hour = fixed_hour};
        DaynightClockOutput output = {0};
        clock_driver(&input, &output);
        clock_state.hour = output.hour;
        clock_weight = output.weight;
        clock_state.object = tod;
        clock_state.ready = 1;
    } else {
        if (!clock_state.ready || clock_state.object != tod || phase != clock_state.phase) {
            // Standalone fallback when no clock provider is linked.
            clock_state.hour = 6 + 12 * (fmaxf(0.05f, fminf(0.95f, phase)) - 0.05f) / 0.9;
            clock_state.object = tod;
            clock_state.ready = 1;
        }
        float dt = read_float(REAL_DT);
        if (fixed) clock_state.hour = fixed_hour;
        else if (isfinite(dt) && dt > 0)
            clock_state.hour = daynight_wrap(clock_state.hour + dt / SECONDS_PER_GAME_HOUR);
    }
    DaynightSample s;
    sample_object(tod, &s);
    clock_state.phase = s.phase;
    write_float(tod + 8, s.phase);
    M32(tod + 4) = 1; // Integer direction, never the retail endpoint reversal.
    memcpy(G + tod + 0x180, s.light, sizeof(s.light));
    write_float(tod + 0x18c, 0);
    for (int i = 0; i < 3; ++i) write_float(tod + 0x170 + 4 * i, s.light[i] * 100000);
    write_float(tod + 0x17c, 1);
}

void f_00769360_orig(Cpu *c);
void f_00769360(Cpu *c) {
    if (!enabled) { f_00769360_orig(c); return; }
    uint32_t tod = c->ecx;
    if (!tod || M32(FLOW) != 6) {
        if (clock_driver) {
            DaynightClockInput input = {0};
            DaynightClockOutput output = {0};
            clock_driver(&input, &output);
        }
        clock_state.ready = 0;
        f_00769360_orig(c);
        return;
    }
    drive(tod);
    c->esp += 4; // thiscall, no stack arguments; preserve nonvolatile registers.
}

// Rec. 709 luminance ratio; noon (unchanged block) gives exactly 1, a dark source keeps the rig.
static float luma_ratio(const float *after, const float *before, float after_scale, float before_scale) {
    float a = (0.2126f * after[0] + 0.7152f * after[1] + 0.0722f * after[2]) * after_scale;
    float b = (0.2126f * before[0] + 0.7152f * before[1] + 0.0722f * before[2]) * before_scale;
    float r = a == b ? 1 : a / b;
    return b > 0 && isfinite(r) && r >= 0 ? r : 1;
}

void f_00769580_orig(Cpu *c);
void f_00769580(Cpu *c) {
    if (!enabled) { f_00769580_orig(c); return; }
    uint32_t tod = c->ecx;
    f_00769580_orig(c); // Fresh weather/low-sun mix on EVERY view, never accumulated.
    if (!tod || M32(FLOW) != 6 || !clock_state.ready || clock_state.object != tod) {
        car_light.ready = 0;
        return;
    }
    DaynightRecord before, night, after;
    record_io(tod, &before, 0);
    daynight_night_record(&night);
    after = before;
    DaynightSample s;
    sample_object(tod, &s);
    daynight_mix(&after, &night, s.night);
    for (int i = 0; i < 3; ++i) {
        after.diffuse[i] *= clock_weight;
        after.specular[i] *= clock_weight;
    }
    record_io(tod, &after, 1);
    if (cars_enabled) {
        car_light.sun = luma_ratio(after.diffuse, before.diffuse, 1, 1);
        car_light.sky = luma_ratio(after.ambient, before.ambient, after.ambient[3], before.ambient[3]);
        car_light.ready = 1;
    }
    if (diag_on) {
        static uint64_t last;
        uint64_t now = host_ticks();
        if (!last || now - last >= 5000000000ull) {
            last = now;
            diag_event("daynight: T=%.6f phase=%.6f n=%.6f moon=%d sunZ=%.6f lightZ=%.6f "
                       "sun=%.6f,%.6f,%.6f ambient=%.6f,%.6f,%.6f*%.6f "
                       "sky=%.6f,%.6f,%.6f env=%.6f skyBrightness=%.6f "
                       "beforeSun=%.6f,%.6f,%.6f beforeAmbient=%.6f,%.6f,%.6f*%.6f",
                       clock_state.hour, s.phase, s.night, s.moon, s.sun[2], s.light[2],
                       after.diffuse[0], after.diffuse[1], after.diffuse[2],
                       after.ambient[0], after.ambient[1], after.ambient[2], after.ambient[3],
                       after.sky[0], after.sky[1], after.sky[2], after.environment, after.sky_brightness,
                       before.diffuse[0], before.diffuse[1], before.diffuse[2],
                       before.ambient[0], before.ambient[1], before.ambient[2], before.ambient[3]);
            diag_event("daynight: rest spec=%.6f,%.6f,%.6f haze=%.6f,%.6f,%.6f "
                       "scales=%.6f,%.6f,%.6f,%.6f,%.6f "
                       "beforeSpec=%.6f,%.6f,%.6f beforeSky=%.6f,%.6f,%.6f "
                       "beforeHaze=%.6f,%.6f,%.6f beforeScales=%.6f,%.6f,%.6f,%.6f,%.6f "
                       "beforeEnv=%.6f beforeSkyBrightness=%.6f",
                       after.specular[0], after.specular[1], after.specular[2],
                       after.haze[0], after.haze[1], after.haze[2],
                       after.sky_scale, after.haze_scale, after.scatter, after.sun_falloff, after.distance_scale,
                       before.specular[0], before.specular[1], before.specular[2],
                       before.sky[0], before.sky[1], before.sky[2],
                       before.haze[0], before.haze[1], before.haze[2],
                       before.sky_scale, before.haze_scale, before.scatter, before.sun_falloff, before.distance_scale,
                       before.environment, before.sky_brightness);
            if (cars_enabled)
                diag_event("daynight: cars sun=%.6f sky=%.6f", car_light.sun, car_light.sky);
        }
    }
}

// elSetupLights (cdecl: light context, rig, ...): the in-game car rig takes the hour's light strength;
// its shipped colours, ratios and directions stay. Context +0x10/+0x20/+0x30: light 0-2 RGB.
void f_00502130_orig(Cpu *c);
static void rig_by_hour(Cpu *c) {
    if (!cars_enabled) { f_00502130_orig(c); return; }
    uint32_t context = arg(c, 0), rig = arg(c, 1);
    f_00502130_orig(c);
    if (!context || rig != CAR_RIG || (int32_t)c->eax != 1 || !car_light.ready || !clock_state.ready ||
        M32(FLOW) != 6)
        return;
    int postfx_look_variant(const char *v);   // x360_post.m: BOOT_DIAG_LOOK_AB `carsoff`, the same frame Off
    if (diag_on && postfx_look_variant("carsoff")) return;
    for (int light = 0; light < 3; ++light) {
        float f = light ? car_light.sky : car_light.sun;
        if (f == 1) continue;
        for (int i = 0; i < 3; ++i) {
            uint32_t a = context + 0x10 + 0x10 * light + 4 * i;
            write_float(a, read_float(a) * f);
        }
    }
}
__attribute__((noinline)) static void setup_local_light(Cpu *c) {
    uint32_t context = arg(c, 0), rig = arg(c, 1), position = arg(c, 2), local_world = arg(c, 3);
    rig_by_hour(c);
    // Nearby scenery lights: only while the clock drives, at its night level.
    if (enabled && clock_state.ready && rig == CAR_RIG && (int32_t)c->eax == 1 && M32(FLOW) == 6)
        car_local_light(context, position, local_world, daynight_lamp_level());
}
__attribute__((noinline)) static void setup_local_diag(Cpu *c) {
    // Count only in diagnostic runs; the rig's existing work is excluded.
    {
        uint64_t diag_current_frame(void);
        static uint64_t frame = UINT64_MAX;
        static unsigned calls, work;
        uint64_t now = diag_current_frame() + 1;
        if (now != frame) {
            if (frame != UINT64_MAX && frame % 60 == 0)
                diag_event("car_locallight_setup: frame=%llu calls=%u work=%u",
                           (unsigned long long)frame, calls, work);
            frame = now;
            calls = work = 0;
        }
        ++calls;
        work += car_local_light != NULL;
    }
    if (car_local_light) setup_local_light(c);
    else rig_by_hour(c);
}
void f_00502130(Cpu *c) {
    if (diag_on) { setup_local_diag(c); return; }
    if (!car_local_light) { rig_by_hour(c); return; }
    setup_local_light(c);
}
#endif
