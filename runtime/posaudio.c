// Positional correctness and optional ITU geometry. No audio device access.
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "host.h"
#include "posaudio.h"

int posaudio_layout(void) {
    static int layout = -1;
    if (layout < 0) {
        const char *e = getenv("NFS_SPEAKER_LAYOUT");
        layout = e && !strcasecmp(e, "itu");
    }
    return layout;
}

uint32_t posaudio_mode_read(uint32_t address) {
    // The translator replaces ONLY the 47 traced reads. No guest memory is
    // changed, even transiently: profile serializers still read the loaded value.
    (void)address;
    return 1;
}

static void speaker_angles(int itu, double a[6]) {
    // Nearest representable guest angles, <0.0025 deg from ITU. This makes
    // fixed source defaults land exactly on speakers, with no snapping/gain jump.
    a[0] = 0; a[1] = itu ? 5461 : 8192; a[2] = itu ? 20025 : 24576;
    a[3] = 65536 - a[2]; a[4] = 65536 - a[1]; a[5] = 65536;
}

void posaudio_gains(double angle, int itu, float gains[5]) {
    double a[6]; speaker_angles(itu, a);
    angle = isfinite(angle) ? fmod(angle, 65536.0) : 0;
    if (angle < 0) angle += 65536;
    for (int i = 0; i < 5; ++i) gains[i] = 0;
    for (int i = 0; i < 5; ++i) if (angle >= a[i] && angle < a[i + 1]) {
        double t = (angle - a[i]) / (a[i + 1] - a[i]);
        gains[i] = (float)cos(t * (M_PI / 2));
        gains[(i + 1) % 5] = (float)sin(t * (M_PI / 2));
        break;
    }
}

uint16_t posaudio_map_default(uint16_t angle, int itu) {
    if (!itu) return angle;
    switch (angle) {
        case 0x2000: return 5461;   // FR +45 -> +30
        case 0x6000: return 20025;  // SR +135 -> +110
        case 0xA000: return 45511;  // SL -135 -> -110
        case 0xE000: return 60075;  // FL -45 -> -30
        default: return angle;     // centre, LFE and other modes
    }
}

uint32_t posaudio_default_angle(uint32_t angle) {
    return M32(0x009C1FDCu) == 3 ? posaudio_map_default((uint16_t)angle, posaudio_layout()) : angle;
}

// Engine sound (F10 Audio, audio.engine_sound; port extra, default Realistic). Retail: the player engine is
// one full-gain point source that SFXCTL_3DRearPos::UpdateParams 004E5A80 (the mix map's "Rear Car Pos") puts 2 m behind
// the car centre in the close cameras (EAXCar+0xC4 0 or 1) for every car, so 5.1 plays it from one speaker group (hood:
// SL+SR only; chase: C only). Realistic changes only the pan of the player's Ginsu engine voice: its azimuth comes from
// the real engine location of that car (table below) relative to the camera, and the source has a width (apparent size
// of the engine at that distance, at least kMinHalfWidth, fully around you when closer than kEngineRadius) plus a
// small diffuse share, scaled to the retail gains' power so its loudness is unchanged. The controller output the mix
// map reads (angles, distances, so its angle-dependent level curves) stays retail; AI, cops, traffic untouched.
enum { ENG_FRONT, ENG_FRONTMID, ENG_MID, ENG_REAR };
static const float kEngineOffset[] = {1.5f, 1.1f, -0.7f, -1.6f};   // metres ahead of the car centre (EAX_CarState+0x40)
static const struct { const char *name; uint8_t layout; } kEngineCars[] = {   // pvehicle collection names
    {"911gt2", ENG_REAR}, {"911turbo", ENG_REAR}, {"997s", ENG_REAR},
    {"carreragt", ENG_MID}, {"caymans", ENG_MID}, {"clio", ENG_MID}, {"elise", ENG_MID}, {"fordgt", ENG_MID},
    {"gallardo", ENG_MID}, {"murcielago", ENG_MID},
    {"bmwm3gtr", ENG_FRONTMID}, {"bmwm3gtre46", ENG_FRONTMID}, {"m3gtre46careerstart", ENG_FRONTMID},
    {"speedtest", ENG_FRONTMID}, {"corvette", ENG_FRONTMID}, {"corvettec6r", ENG_FRONTMID}, {"db9", ENG_FRONTMID},
    {"rx7", ENG_FRONTMID}, {"rx8", ENG_FRONTMID}, {"rx8speedt", ENG_FRONTMID}, {"slr", ENG_FRONTMID},
    {"viper", ENG_FRONTMID}, {"copsport", ENG_FRONTMID}, {"copsporthench", ENG_FRONTMID},
    {"copsportghost", ENG_FRONTMID}, {"copcross", ENG_FRONTMID}, {"cs_c6_copsporthench", ENG_FRONTMID},
    {"a3", ENG_FRONT}, {"a4", ENG_FRONT}, {"camaro", ENG_FRONT}, {"clk500", ENG_FRONT}, {"cobaltss", ENG_FRONT},
    {"cts", ENG_FRONT}, {"eclipsegt", ENG_FRONT}, {"gti", ENG_FRONT}, {"gto", ENG_FRONT}, {"imprezawrx", ENG_FRONT},
    {"is300", ENG_FRONT}, {"lancerevo8", ENG_FRONT}, {"monaro", ENG_FRONT}, {"mustanggt", ENG_FRONT},
    {"mustang_demo", ENG_FRONT}, {"wrx_demo", ENG_FRONT}, {"punto", ENG_FRONT}, {"sl500", ENG_FRONT},
    {"sl65", ENG_FRONT}, {"supra", ENG_FRONT}, {"tt", ENG_FRONT},
    {"copmidsize", ENG_FRONT}, {"copghost", ENG_FRONT}, {"copgto", ENG_FRONT}, {"copgtoghost", ENG_FRONT},
    {"copsuv", ENG_FRONT}, {"copsuvl", ENG_FRONT}, {"copsuvpatrol", ENG_FRONT}, {"cs_gto_copgto", ENG_FRONT},
    {"cs_mustang_copsuv", ENG_FRONT}, {"cs_viper_copmidsize", ENG_FRONT}, {"cs_clio_trafpizza", ENG_FRONT},
    {"cs_clio_traftaxi", ENG_FRONT}, {"cs_cts_traf_minivan", ENG_FRONT}, {"cs_cts_traffictruck", ENG_FRONT},
    {"cs_trafcement", ENG_FRONT}, {"cs_trafgarb", ENG_FRONT}, {"cs_semi", ENG_FRONT},
};
static const double kEngineRadius = 0.5, kMinHalfWidth = 30, kDiffuse = 0.1;
// AI racers, cops and traffic (same Realistic switch): the engine at the car's layout offset (kEngineOffset; +1.4 m, front,
// where the pvehicle is not in the table: traffic) and the siren on the roof (0 m), each spread over the arc the car
// subtends at its distance (a car alongside is wide, a far one a point) -- a car body (half-length 2 m) for engines, the
// light bar (0.6 m) for sirens -- with only a small diffuse share and a narrow floor, so far cars pan as in retail.
static const double kCarRadius = 2.0, kCarMinHalf = 4, kCarDiffuse = 0.05, kUnknownOffset = 1.4,
                    kSirenRadius = 0.6, kSirenMinHalf = 3, kSirenDiffuse = 0.03;

// The player's own tyre/road/body sounds (Realistic): the mix map gives them all one point, the "Rear Car Pos" controller's
// (2 m behind the car centre in the close cameras, the same point the retail engine uses), so 5.1 plays them from SL+SR
// in the hood view. Realistic puts that voice at the four wheels instead: contact patches at +-kWheelTrack/2 sideways and
// +-kWheelBase/2 along the car (typical road car; the per-car value is not in the sound data), each a quarter of the
// power, each spread by its apparent size (a wheel seen from 1 m is wide) with a diffuse share (road noise is not a point).
static const double kWheelTrack = 1.56, kWheelBase = 2.7, kWheelRadius = 0.4, kWheelMinHalf = 15, kWheelDiffuse = 0.10;

static int engine_real = -1;
void posaudio_set_engine_sound(int realistic) { engine_real = realistic != 0; }
int posaudio_engine_realistic(void) {
    if (engine_real < 0) {
        const char *e = getenv("NFS_ENGINE_SOUND");
        engine_real = !(e && (!strcasecmp(e, "retail") || !strcmp(e, "0")));                                         
    }
    return engine_real;
}

// Attrib::StringHash32 004519D0 (Jenkins lookup2, init 0xABCDEF00): an Attrib collection's key (Collection+0x20).
#define HMIX(a, b, c) do { \
    a -= b; a -= c; a ^= c >> 13; b -= c; b -= a; b ^= a << 8;  c -= a; c -= b; c ^= b >> 13; \
    a -= b; a -= c; a ^= c >> 12; b -= c; b -= a; b ^= a << 16; c -= a; c -= b; c ^= b >> 5;  \
    a -= b; a -= c; a ^= c >> 3;  b -= c; b -= a; b ^= a << 10; c -= a; c -= b; c ^= b >> 15; } while (0)
uint32_t posaudio_attrib_hash(const char *s) {
    const uint8_t *k = (const uint8_t *)s;
    uint32_t n = (uint32_t)strlen(s), len = n, a = 0x9E3779B9u, b = a, c = 0xABCDEF00u;
    if (!n) return 0;
    for (; len >= 12; k += 12, len -= 12) {
        a += k[0] | k[1] << 8 | k[2] << 16 | (uint32_t)k[3] << 24;
        b += k[4] | k[5] << 8 | k[6] << 16 | (uint32_t)k[7] << 24;
        c += k[8] | k[9] << 8 | k[10] << 16 | (uint32_t)k[11] << 24;
        HMIX(a, b, c);
    }
    c += n;
    for (uint32_t j = len; j-- > 0;) {
        if (j >= 8) c += (uint32_t)k[j] << (8 * (j - 7));
        else if (j >= 4) b += (uint32_t)k[j] << (8 * (j - 4));
        else a += (uint32_t)k[j] << (8 * j);
    }
    HMIX(a, b, c);
    return c;
}

int posaudio_engine_layout(uint32_t key) {   // -1: not in the table (the retail 2 m-behind position is kept)
    static uint32_t keys[sizeof kEngineCars / sizeof *kEngineCars];
    if (!keys[0]) for (size_t i = 0; i < sizeof keys / sizeof *keys; ++i) keys[i] = posaudio_attrib_hash(kEngineCars[i].name);
    for (size_t i = 0; i < sizeof keys / sizeof *keys; ++i) if (keys[i] == key) return kEngineCars[i].layout;
    return -1;
}

// Width model: a source of the given radius seen from dist metres spans asin(r/d) each side (at least min_half degrees);
// inside the radius it surrounds the listener (half-width 90 deg blended toward all five speakers equally). Shares are
// the panner's power gains averaged over that arc, plus a diffuse share spread evenly; the result has the given total power.
static void spread_gains(double angle, double dist, int itu, double power, double radius, double min_half, double diffuse,
                         float gains[5]) {
    double half = min_half, around = 0, share[5] = {0};
    if (!(dist >= radius)) { half = 90; around = isfinite(dist) && dist > 0 ? 1 - dist / radius : 1; }
    else if (asin(radius / dist) * (180 / M_PI) > half) half = asin(radius / dist) * (180 / M_PI);
    enum { N = 33 };
    for (int k = 0; k < N; ++k) {
        float g[5]; posaudio_gains(angle + half * (65536.0 / 360) * (2.0 * k / (N - 1) - 1), itu, g);
        for (int i = 0; i < 5; ++i) share[i] += (double)g[i] * g[i] / N;
    }
    double even = diffuse + (1 - diffuse) * around;
    for (int i = 0; i < 5; ++i) gains[i] = (float)sqrt(power * ((1 - even) * share[i] + even / 5));
}
void posaudio_engine_spread(double angle, double dist, int itu, double power, float gains[5]) {
    spread_gains(angle, dist, itu, power, kEngineRadius, kMinHalfWidth, kDiffuse, gains);
}

// Camera-relative azimuth as SFXCTL_3DObjPos::SetCameraAngle 004B5D70 computes it (0 = the camera's facing, 0x8000 behind).
static uint16_t engine_azimuth(double ex, double ey, double fx, double fy) {
    double len = hypot(ex, ey);
    if (!(len > 0)) return 0;
    ex /= len; ey /= len;
    double a = acos(fmax(-1, fmin(1, ex * fx + ey * fy))) * (32768 / M_PI);
    return (uint16_t)lround(ex * fy - ey * fx < 0 ? 65536 - a : a);
}

static int cur_cam = 2;           // the player's camera type (EAXCar+0xC4: 0/1 close cameras) at the last Rear Car Pos update
static uint32_t player_car;      // EAX_CarState of the player (from the last Rear Car Pos update)
// The player's engine as the last Rear Car Pos update saw it (guest lock held by both the update and the panner).
static struct { uint32_t state, key, updates, spread; int layout; uint16_t angle, retail; float dist, along, was[5], now[5]; int check; uint64_t at; } eng;
// The player's Rear Car Pos controller as of its last update: the angle word it gave the mix map and the car/camera geometry,
// so a bank voice carrying that word (matched like the AI sources below) can be moved to the wheels.
static struct { uint16_t word; uint64_t at; double px, py, dx, dy, cx, cy, fx, fy; uint32_t spread, seen; } rear;

void f_004E5A80_orig(Cpu *c);
void f_004E5A80(Cpu *c) {
    uint32_t ctl = c->ecx;
    f_004E5A80_orig(c);
    if (!posaudio_engine_realistic() && !diag_on) return;
    uint32_t state = M32(ctl + 0x14), car = state ? M32(state + 0x34) : 0, view = M32(ctl + 0x44), out = M32(ctl + 0xC);
    if (!car || view > 1 || !out) return;
    // EAX_CarState: forward row +0x10, position +0x40, Attrib::Gen::pvehicle instance +0x200 (its Collection at +4).
    uint32_t coll = M32(car + 0x204);
    eng.key = coll ? M32(coll + 0x20) : 0;
    eng.layout = posaudio_engine_layout(eng.key);
    double px = MF32(car + 0x40), py = MF32(car + 0x44), dx = MF32(car + 0x10), dy = MF32(car + 0x14);
    double cx = MF32(0x009144A0u + 8 * view), cy = MF32(0x009144A4u + 8 * view);   // camera position / facing (2D),
    double fx = MF32(0x009144C0u + 8 * view), fy = MF32(0x009144C4u + 8 * view);   // GenerateSinglePlayerMix case 0
    double off = eng.layout >= 0 ? kEngineOffset[eng.layout] : -2.0;
    double ex = px + dx * off - cx, ey = py + dy * off - cy;
    player_car = car; cur_cam = (int)(M32(ctl + 0x18) ? M32(M32(ctl + 0x18) + 0xC4) : 2);
    eng.state = state; eng.angle = engine_azimuth(ex, ey, fx, fy); eng.dist = (float)hypot(ex, ey);
    eng.along = (float)((cx - px) * dx + (cy - py) * dy); eng.at = host_ticks(); ++eng.updates;
    // BOOT_DIAG self-check of the geometry: the retail close-camera source (2 m behind) through the same formula
    // against the camera angle the original wrote at out+0xC.
    rear.word = (uint16_t)M32(out + 0xC); rear.at = eng.at;
    rear.px = px; rear.py = py; rear.dx = dx; rear.dy = dy; rear.cx = cx; rear.cy = cy; rear.fx = fx; rear.fy = fy;
    uint32_t cam = M32(ctl + 0x18) ? M32(M32(ctl + 0x18) + 0xC4) : 2, written = M32(out + 0xC);
    if (cam <= 1 && written <= 0xFFFF) {
        eng.retail = engine_azimuth(px - dx * 2 - cx, py - dy * 2 - cy, fx, fy);
        eng.check = (int16_t)(uint16_t)(eng.retail - written);
    }
}

// The pan of the player's own Ginsu engine voice in Realistic, from the update above if it is current.
static int engine_spread(uint32_t engine_object, uint32_t out) {
    if (!posaudio_engine_realistic() || !eng.state || M32(engine_object + 0x14) != eng.state) return 0;
    if (host_ticks() - eng.at > 500000000ull) return 0;
    double power = 0;
    for (int i = 0; i < 5; ++i) power += (double)MF32(out + 4 * i) * MF32(out + 4 * i);
    float g[5]; posaudio_engine_spread(eng.angle, eng.dist, posaudio_layout(), power, g);
    for (int i = 0; i < 5; ++i) { eng.was[i] = MF32(out + 4 * i); eng.now[i] = g[i]; MF32(out + 4 * i) = g[i]; }
    ++eng.spread;
    return 1;
}

// A source's car: object+0x14 is its CSTATE, whose +0x34 is the EAX_CarState (position +0x40, forward +0x10, pvehicle
// Collection at +0x204, key at Collection+0x20) -- the same chain the player's engine uses (CARSFX_TrafficEngine
// 004EFBE0 and CARSFX_Siren 004F03A0 read the same fields). Camera as in SetCameraAngle for view 0.
static int guest_ptr(uint32_t a) { return a >= 0x10000u && a < 0x7FFF0000u; }
typedef struct { uint32_t car, key; double px, py, dx, dy, cx, cy, fx, fy; } Source;
static int source_car(uint32_t obj, Source *s) {
    if (!guest_ptr(obj)) return 0;
    uint32_t state = M32(obj + 0x14), car = guest_ptr(state) ? M32(state + 0x34) : 0;
    if (!guest_ptr(car)) return 0;
    uint32_t eax = M32(0x00911FA8u), coll = M32(car + 0x204);
    if (guest_ptr(eax) && M32(eax + 100)) return 0;           // two-player split screen: the game's per-view choice stays
    s->car = car; s->key = guest_ptr(coll) ? M32(coll + 0x20) : 0;
    s->cx = MF32(0x009144A0u); s->cy = MF32(0x009144A4u); s->fx = MF32(0x009144C0u); s->fy = MF32(0x009144C4u);
    s->dx = MF32(car + 0x10); s->dy = MF32(car + 0x14); s->px = MF32(car + 0x40); s->py = MF32(car + 0x44);
    return isfinite(s->dx + s->dy + s->px + s->py + s->cx + s->cy + s->fx + s->fy);
}
// Camera-relative azimuth and distance of the point `off` metres ahead of the car centre.
static void source_pan(const Source *s, double off, double *angle, double *dist) {
    double ex = s->px + s->dx * off - s->cx, ey = s->py + s->dy * off - s->cy;
    *angle = engine_azimuth(ex, ey, s->fx, s->fy); *dist = hypot(ex, ey);
}
static int car_is_cop(uint32_t key) {
    static uint32_t keys[sizeof kEngineCars / sizeof *kEngineCars]; static int init;
    if (!init) {
        for (size_t i = 0; i < sizeof keys / sizeof *keys; ++i) {
            const char *n = kEngineCars[i].name;
            keys[i] = !strncmp(n, "cop", 3) || strstr(n, "_cop") ? posaudio_attrib_hash(n) : 0;
        }
        init = 1;
    }
    for (size_t i = 0; i < sizeof keys / sizeof *keys; ++i) if (keys[i] && keys[i] == key) return 1;
    return 0;
}

// Realistic for the other sources: AI racers and cop engines (CARSFX_GinsuEngine/AEMSEngine), traffic engines and
// cop sirens. The game's own gains (`out`) give the voice's power; only the distribution over the five speakers is
// replaced (the game's reverb/doppler/level paths and the mix map are untouched). Not touched when the car is unknown.
static struct { uint32_t spread, skipped, rejected, err_big; double err_sum, err_max; uint32_t err_n;
               uint64_t bn[2]; double was[2][5], now[2][5]; } others;   // BOOT_DIAG: shares before/after the spread, bucket 0 < 10 m, 1 farther
static int other_spread(int cat, uint32_t obj, uint32_t out, uint16_t game_angle) {
    Source s;
    if (!source_car(obj, &s)) { ++others.skipped; return 0; }
    int siren = cat == 2, layout = siren ? -1 : posaudio_engine_layout(s.key);
    double off = siren ? 0 : layout >= 0 ? kEngineOffset[layout] : kUnknownOffset, angle, dist, centre, d0;
    source_pan(&s, off, &angle, &dist);
    // Self-check against the game: the car-centre azimuth through the same formula vs the angle the game panned this voice
    // with. A voice whose angle is further off than a moving car can explain (3 deg plus a metre of travel) was matched to
    // the wrong source by its angle word and stays retail.
    source_pan(&s, 0, &centre, &d0);
    double e = fabs((double)(int16_t)(uint16_t)(((int)lround(centre) - (int)game_angle) & 0xFFFF)) * (360.0 / 65536);
    if (diag_on) { others.err_sum += e; ++others.err_n; if (e > others.err_max) others.err_max = e; if (e > 2) ++others.err_big; }
    if (e > 3 + 60 / fmax(d0, 2.0)) { ++others.skipped; ++others.rejected; return 0; }
    double power = 0;
    for (int i = 0; i < 5; ++i) power += (double)MF32(out + 4 * i) * MF32(out + 4 * i);
    float g[5];
    if (siren) spread_gains(angle, dist, posaudio_layout(), power, kSirenRadius, kSirenMinHalf, kSirenDiffuse, g);
    else spread_gains(angle, dist, posaudio_layout(), power, kCarRadius, kCarMinHalf, kCarDiffuse, g);
    if (diag_on) {
        int bk = d0 < 10 ? 0 : 1;
        double pn = 0;
        for (int i = 0; i < 5; ++i) pn += (double)g[i] * g[i];
        if (power > 1e-6 && pn > 1e-6) {
            ++others.bn[bk];
            for (int i = 0; i < 5; ++i) { others.was[bk][i] += (double)MF32(out + 4 * i) * MF32(out + 4 * i) / power; others.now[bk][i] += (double)g[i] * g[i] / pn; }
        }
    }
    for (int i = 0; i < 5; ++i) MF32(out + 4 * i) = g[i];
    ++others.spread;
    return 1;
}

// A bank voice carrying the player's Rear Car Pos angle word (Realistic: moves it to the four wheels, power unchanged).
static int rear_voice(uint16_t angle, uint32_t out) {
    if (!eng.state || !rear.at || rear.word != angle || host_ticks() - rear.at > 300000000ull) return 0;
    if (!isfinite(rear.px + rear.py + rear.dx + rear.dy + rear.cx + rear.cy + rear.fx + rear.fy)) return 0;
    ++rear.seen;
    if (!posaudio_engine_realistic()) return 1;
    double power = 0, sum[5] = {0};
    for (int i = 0; i < 5; ++i) power += (double)MF32(out + 4 * i) * MF32(out + 4 * i);
    for (int ax = -1; ax <= 1; ax += 2)
        for (int sd = -1; sd <= 1; sd += 2) {
            double al = ax * kWheelBase / 2, sdm = sd * kWheelTrack / 2;
            double ex = rear.px + rear.dx * al - rear.dy * sdm - rear.cx, ey = rear.py + rear.dy * al + rear.dx * sdm - rear.cy;
            float g[5];
            spread_gains(engine_azimuth(ex, ey, rear.fx, rear.fy), hypot(ex, ey), posaudio_layout(), power / 4, kWheelRadius, kWheelMinHalf, kWheelDiffuse, g);
            for (int i = 0; i < 5; ++i) sum[i] += (double)g[i] * g[i];
        }
    for (int i = 0; i < 5; ++i) MF32(out + 4 * i) = (float)sqrt(sum[i]);
    ++rear.spread;
    return 1;
}

// BOOT_DIAG records actual 0081FE64 calls, while one of the four 3D
// controllers updates. The thread-local context cannot tag another thread's
// music voice. Guest execution and the once/sec reader hold the guest lock.
// AI cop/racer engines (CARSFX_AEMSEngine), traffic engines and sirens do not pan synchronously: their update writes the
// angle word into a Csis member block and the AEMS script reaches the voice later through SNDAEMSI_bankazimuth 0081A0AC
// (FUN_0081F207 caller 0081A0D1), on the Snd thread. A voice is matched to its source by that 16-bit angle word: each
// source update records (word -> object, class, time), and a bank pan update with the same word within 0.3 s belongs to it
// (two sources on the same word inside the window keep the last: they sit at the same place anyway).
enum { SITE_BANKAZIMUTH = 0x0081A0D1u, SITE_STREAM = 0x0081CF56u };
static struct { uint32_t obj; uint16_t word; uint8_t cat; uint64_t at; } sources[256];
static uint64_t reg_updates, reg_hits;
static void source_register(int cat, uint32_t obj, uint32_t mix) {
    if (!guest_ptr(mix)) return;
    uint16_t w = (uint16_t)M32(mix);
    sources[w & 255].obj = obj; sources[w & 255].word = w; sources[w & 255].cat = (uint8_t)cat; sources[w & 255].at = host_ticks();
    ++reg_updates;
}

// BOOT_DIAG speaker-use tally per source class (cumulative since start): the five speakers' share of each voice update's
// final gain power, summed; how many updates put at least 10 % of their power on the centre / on the rear pair.
enum { K_PLAYER, K_AI, K_COP, K_TRAFFIC, K_SIREN, K_BANK, K_STREAM, K_OTHER, K_REAR, K_N };
static const char *const kclass[] = {"player", "ai", "cop", "traffic", "siren", "bank", "stream", "other", "rearpos"};
static struct { uint64_t n, centre, rear; double share[5]; } tally[K_N];
static _Thread_local uint32_t pan_site;      // BOOT_DIAG: the guest return address of the FUN_0081F207 call that is panning
// Callers of 0081FE64 outside the four context hooks: guest return address -> count (BOOT_DIAG only), with the share vectors.
static struct { uint32_t ret; uint64_t n; double share[5]; } callers[24];
static void tally_caller(uint32_t ret, uint32_t out) {
    ret = pan_site ? pan_site : ret;
    double p = 0, g2[5];
    for (int i = 0; i < 5; ++i) { g2[i] = (double)MF32(out + 4 * i) * MF32(out + 4 * i); p += g2[i]; }
    if (!(p > 1e-6)) return;
    int i = 0;
    for (; i < 24 && callers[i].ret && callers[i].ret != ret; ++i) {}
    if (i == 24) return;
    callers[i].ret = ret; ++callers[i].n;
    for (int j = 0; j < 5; ++j) callers[i].share[j] += g2[j] / p;
}
static void tally_voice(int k, uint32_t out) {
    double p = 0, g2[5];
    for (int i = 0; i < 5; ++i) { g2[i] = (double)MF32(out + 4 * i) * MF32(out + 4 * i); p += g2[i]; }
    if (!(p > 1e-6)) return;
    ++tally[k].n;
    for (int i = 0; i < 5; ++i) tally[k].share[i] += g2[i] / p;
    if (g2[0] / p >= 0.1) ++tally[k].centre;
    if ((g2[2] + g2[3]) / p >= 0.1) ++tally[k].rear;
}


// BOOT_DIAG: every 3D controller update (SFXCTL_3DObjPos::Generate3DParams 004B6030, the base the wheel/wind/collision/
// scrape/car/rear controllers share) -> per controller class (vtable): where its position vector sits relative to the
// player's car, and which speakers the bank voices carrying that controller's angle word end up on.
enum { CTL_MAX = 64 };
static struct { uint32_t vt; uint64_t n, near; double along, side, dist; uint64_t bn; double share[5]; uint64_t bank_rear;
                uint32_t sample_ctl; uint8_t view; int32_t posoff; } ctls[CTL_MAX];
static struct { uint32_t vt; uint16_t word; uint64_t at; uint8_t view; } ctl_reg[256];
void f_004B6030_orig(Cpu *c);
void f_004B6030(Cpu *c) {
    uint32_t ctl = c->ecx;
    f_004B6030_orig(c);
    if (!diag_on) return;
    uint32_t vt = M32(ctl), out = M32(ctl + 0xC), pos = M32(ctl + 0x28);
    if (!guest_ptr(out) || !guest_ptr(pos)) return;
    uint32_t st = M32(ctl + 0x14), pc = guest_ptr(st) ? M32(st + 0x34) : 0;
    uint8_t view = pc && pc == player_car ? (cur_cam <= 1 ? 2 : 1) : 0;   // 0 another car/object, 1 the player's car (chase), 2 (hood/bumper)
    int i = 0;
    for (; i < CTL_MAX && ctls[i].vt && (ctls[i].vt != vt || ctls[i].view != view); ++i) {}
    if (i == CTL_MAX) return;
    ctls[i].vt = vt; ctls[i].view = view; ++ctls[i].n; ctls[i].sample_ctl = ctl;
    if (!ctls[i].posoff && guest_ptr(player_car) && pos - player_car < 0x4000) ctls[i].posoff = (int32_t)(pos - player_car);
    if (guest_ptr(player_car)) {
        double rx = MF32(pos) - MF32(player_car + 0x40), ry = MF32(pos + 4) - MF32(player_car + 0x44);
        double dx = MF32(player_car + 0x10), dy = MF32(player_car + 0x14), d = hypot(rx, ry);
        if (isfinite(d) && d < 6) {
            ++ctls[i].near; ctls[i].along += rx * dx + ry * dy; ctls[i].side += rx * dy - ry * dx; ctls[i].dist += d;
        }
    }
    uint16_t w = (uint16_t)M32(out + 0xC), w2 = (uint16_t)M32(out + 8);
    ctl_reg[w & 255].vt = vt; ctl_reg[w & 255].word = w; ctl_reg[w & 255].at = host_ticks(); ctl_reg[w & 255].view = view;
    ctl_reg[w2 & 255].vt = vt; ctl_reg[w2 & 255].word = w2; ctl_reg[w2 & 255].at = host_ticks(); ctl_reg[w2 & 255].view = view;
}
static _Thread_local uint32_t cur_voice = 0xFFFFFFFFu;   // FUN_0081F207's voice slot argument while it pans
static struct { uint32_t val[8]; uint64_t cnt[8]; } vhist[CTL_MAX][36];   // BOOT_DIAG: the voice record's 36 words per controller class
static struct { uint64_t n, first, last; uint32_t w00, w08; double gain; } slotstat[CTL_MAX][64];   // per controller class and voice slot
static void ctl_tally(uint16_t angle, uint32_t out) {
    uint32_t slot = angle & 255u;
    if (!ctl_reg[slot].vt || ctl_reg[slot].word != angle || host_ticks() - ctl_reg[slot].at > 300000000ull) return;
    for (int i = 0; i < CTL_MAX; ++i) if (ctls[i].vt == ctl_reg[slot].vt && ctls[i].view == ctl_reg[slot].view) {
        double p = 0, g2[5];
        for (int j = 0; j < 5; ++j) { g2[j] = (double)MF32(out + 4 * j) * MF32(out + 4 * j); p += g2[j]; }
        if (!(p > 1e-6)) return;
        ++ctls[i].bn;
        if (cur_voice < 256 && guest_ptr(M32(0x009C21F4u))) {
            uint32_t rec = M32(0x009C21F4u) + cur_voice * 0x90;
            if (cur_voice < 64) {
                double pw = 0; for (int j = 0; j < 5; ++j) pw += (double)MF32(out + 4 * j) * MF32(out + 4 * j);
                if (!slotstat[i][cur_voice].n) slotstat[i][cur_voice].first = host_ticks();
                slotstat[i][cur_voice].last = host_ticks(); ++slotstat[i][cur_voice].n; slotstat[i][cur_voice].gain += pw;
                slotstat[i][cur_voice].w00 = M32(rec); slotstat[i][cur_voice].w08 = M32(rec + 0x20);
            }
            for (int w = 0; w < 36; ++w) {
                uint32_t v = M32(rec + 4 * w); int k = 0;
                for (; k < 8 && vhist[i][w].cnt[k] && vhist[i][w].val[k] != v; ++k) {}
                if (k < 8) { vhist[i][w].val[k] = v; ++vhist[i][w].cnt[k]; }
            }
        }
        for (int j = 0; j < 5; ++j) ctls[i].share[j] += g2[j] / p;
        if ((g2[2] + g2[3]) / p >= 0.1) ++ctls[i].bank_rear;
        return;
    }
}

typedef struct {
    uint32_t object, voice, input;
    uint16_t source, used;
    float gains[5];
    uint64_t seen;
} Voice;
static Voice voices[5];
static _Thread_local int category = -1;
static _Thread_local uint32_t object;
static const char *const names[] = {"engine", "traffic", "siren", "heli", "aems"};

void f_0081FE64_orig(Cpu *c);
void f_0081FE64(Cpu *c) {
    uint16_t angle = (uint16_t)arg(c, 0);
    uint32_t out = arg(c, 1);
    uint32_t ret_esp = c->esp;     // guest return address of this call, read after the table lookup (BOOT_DIAG callers)
    uint32_t ret_addr = M32(ret_esp);
    if (posaudio_layout() && M32(0x009C1FDCu) == 3 && M8(0x009C205Fu) == 5) {
        float gains[5]; posaudio_gains(angle, 1, gains);
        for (int i = 0; i < 5; ++i) MF32(out + 4 * i) = gains[i];
        MF32(out + 20) = 0;
        c->esp += 4;             // cdecl: caller owns the two arguments
    } else {
        f_0081FE64_orig(c);      // Game is the unmodified byte-table path
    }
    if (M32(0x009C1FDCu) == 3 && M8(0x009C205Fu) == 5) {
        int k = pan_site == SITE_STREAM ? K_STREAM : pan_site == SITE_BANKAZIMUTH ? K_BANK : K_OTHER;
        int real = posaudio_engine_realistic(), engine = category == 0 || category == 4;
        if (category >= 0) {
            if (engine && eng.state && M32(object + 0x14) == eng.state) {   // the player's own engine
                k = K_PLAYER;
                if (category == 0) engine_spread(object, out);   // checks Realistic itself
            } else if (category == 3) {
                k = K_OTHER;                                            // helicopter: retail
            } else {
                Source s;
                k = category == 1 ? K_TRAFFIC : category == 2 ? K_SIREN : K_AI;
                if (k == K_AI && diag_on && source_car(object, &s) && car_is_cop(s.key)) k = K_COP;
                if (real) other_spread(category, object, out, angle);
            }
        } else if (pan_site == SITE_BANKAZIMUTH && (real || diag_on)) {
            // Asynchronous bank voice (see `sources`): the source it belongs to, if one updated with this angle word.
            uint32_t slot = angle & 255u;
            if (sources[slot].obj && sources[slot].word == angle && host_ticks() - sources[slot].at < 300000000ull) {
                int cat = sources[slot].cat; Source s;
                ++reg_hits;
                k = cat == 1 ? K_TRAFFIC : cat == 2 ? K_SIREN : K_AI;
                if (k == K_AI && diag_on && source_car(sources[slot].obj, &s) && car_is_cop(s.key)) k = K_COP;
                if (real) other_spread(cat, sources[slot].obj, out, angle);
            } else if (rear_voice(angle, out)) {
                k = K_REAR;                                             // the player's tyre/road/body voices (see kWheelTrack)
            }
        }
        if (diag_on) { tally_voice(k, out); if (category < 0) { tally_caller(ret_addr, out); if (pan_site == SITE_BANKAZIMUTH) ctl_tally(angle, out); } }
    }
    if (diag_on && category >= 0 && M8(0x009C205Fu) == 5) {
        Voice *v = &voices[category];
        v->object = object; v->voice = out; v->used = angle;
        v->input = M32(object + 0x10);
        v->source = v->input ? M16(v->input) : 0;
        for (int i = 0; i < 5; ++i) v->gains[i] = MF32(out + 4 * i);
        v->seen = host_ticks();
    }
}

// Snd's per-voice pan update (reached from _SND3dpos, the Csis scripts and the music/ambience setup): BOOT_DIAG tags its
// caller so the speaker-use tally can tell which game code panned a voice.
void f_0081F207_orig(Cpu *c);
void f_0081F207(Cpu *c) {
    if (!diag_on && !posaudio_engine_realistic()) { f_0081F207_orig(c); return; }
    uint32_t saved = pan_site, sv = cur_voice; pan_site = M32(c->esp); cur_voice = M32(c->esp + 4);
    f_0081F207_orig(c);
    pan_site = saved; cur_voice = sv;
}

#define VOICE_HOOK(addr, cat) \
    void f_##addr##_orig(Cpu *c); \
    void f_##addr(Cpu *c) { \
        if (!diag_on && !(cat != 3 && posaudio_engine_realistic())) { f_##addr##_orig(c); return; } \
        int saved = category; uint32_t saved_object = object; \
        category = cat; object = c->ecx; \
        f_##addr##_orig(c); \
        if (cat == 1 || cat == 2 || cat == 4) source_register(cat, object, M32(object + 0x10)); \
        category = saved; object = saved_object; \
    }
VOICE_HOOK(004EE3E0, 0)
VOICE_HOOK(004EFBE0, 1)
VOICE_HOOK(004F03A0, 2)
VOICE_HOOK(004F0840, 3)
VOICE_HOOK(004EDD90, 4)

void posaudio_diag_second(void) {
    if (!diag_on) return;
    uint32_t eaxsound = M32(0x00911FA8u);
    uint32_t settings = eaxsound ? M32(eaxsound + 0x24) : 0;
    char line[1600];
    int n = snprintf(line, sizeof line, "posaudio: settings=%08X loaded=%d used=1 layout=%s gains=C,FR,SR,SL,FL",
                     settings, settings ? (int)M32(settings + 0x24) : -1, posaudio_layout() ? "itu" : "game");
    uint64_t now = host_ticks();
    int active = 0;
    for (int i = 0; i < 5; ++i) {
        Voice *v = &voices[i];
        if (!v->seen || now - v->seen > 1000000000ull) continue;
        ++active;
        n += snprintf(line + n, sizeof line - n,
                      " %s[obj=%08X voice=%08X mix=%08X src=%u/%.2fdeg pan=%u/%.2fdeg g=%.5f,%.5f,%.5f,%.5f,%.5f]",
                      names[i], v->object, v->voice, v->input, v->source, v->source * (360.0 / 65536),
                      v->used, v->used * (360.0 / 65536),
                      v->gains[0], v->gains[1], v->gains[2], v->gains[3], v->gains[4]);
    }
    if (eng.updates)
        n += snprintf(line + n, sizeof line - n, " player_engine[realistic=%d key=%08X layout=%d az=%.1fdeg dist=%.2f cam_along=%.2f"
                      " retail_check=%d updates=%u spread=%u]", posaudio_engine_realistic(), eng.key, eng.layout,
                      eng.angle * (360.0 / 65536), eng.dist, eng.along, eng.check, eng.updates, eng.spread);
    if (eng.spread) {   // power shares C,FR,SR,SL,FL of the last spread voice: the retail gains it replaced, then its own
        double pw = 0, pn = 0;
        for (int i = 0; i < 5; ++i) { pw += eng.was[i] * eng.was[i]; pn += eng.now[i] * eng.now[i]; }
        n += snprintf(line + n, sizeof line - n, " shares retail=%.2f,%.2f,%.2f,%.2f,%.2f realistic=%.2f,%.2f,%.2f,%.2f,%.2f power_db=%+.3f",
                      eng.was[0] * eng.was[0] / pw, eng.was[1] * eng.was[1] / pw, eng.was[2] * eng.was[2] / pw,
                      eng.was[3] * eng.was[3] / pw, eng.was[4] * eng.was[4] / pw, eng.now[0] * eng.now[0] / pn,
                      eng.now[1] * eng.now[1] / pn, eng.now[2] * eng.now[2] / pn, eng.now[3] * eng.now[3] / pn,
                      eng.now[4] * eng.now[4] / pn, 10 * log10(pn / pw));
    }
    diag_event("%s active=%d", line, active);
    // Cumulative speaker use per source class (share columns C,FR,SR,SL,FL = mean share of the voice updates' gain power;
    // centre/rear = fraction of updates with >= 10 % of their power on C / on SL+SR) and, in Realistic, how many
    // AI/cop/traffic/siren updates were spread, how many were left retail (no car found), and the largest/mean gap between
    // the car-centre azimuth recomputed here and the angle the game panned with (the geometry check).
    char st[1200]; int m = snprintf(st, sizeof st, "soundstage: realistic=%d registered=%llu matched=%llu rear[seen=%u wheels=%u] others[spread=%u skipped=%u rejected=%u az_gap_mean=%.2fdeg max=%.2fdeg over2deg=%u]",
                                    posaudio_engine_realistic(), (unsigned long long)reg_updates, (unsigned long long)reg_hits, rear.seen, rear.spread, others.spread, others.skipped, others.rejected,
                                    others.err_n ? others.err_sum / others.err_n : 0.0, others.err_max, others.err_big);
    for (int k = 0; k < K_N && m < (int)sizeof st - 150; ++k) {
        if (!tally[k].n) continue;
        double n = (double)tally[k].n;
        m += snprintf(st + m, sizeof st - m, " %s[n=%llu C=%.3f FR=%.3f SR=%.3f SL=%.3f FL=%.3f centre=%.3f rear=%.3f]", kclass[k],
                      (unsigned long long)tally[k].n, tally[k].share[0] / n, tally[k].share[1] / n, tally[k].share[2] / n,
                      tally[k].share[3] / n, tally[k].share[4] / n, tally[k].centre / n, tally[k].rear / n);
    }
    diag_event("%s", st);
    for (int bk = 0; bk < 2 && others.bn[bk]; ++bk) {   // the same voice updates, retail gains vs spread (C,FR,SR,SL,FL)
        double n = (double)others.bn[bk];
        diag_event("soundstage-spread: %s n=%llu retail C=%.3f FR=%.3f SR=%.3f SL=%.3f FL=%.3f spread C=%.3f FR=%.3f SR=%.3f SL=%.3f FL=%.3f",
                   bk ? "far(>=10m)" : "near(<10m)", (unsigned long long)others.bn[bk], others.was[bk][0] / n, others.was[bk][1] / n,
                   others.was[bk][2] / n, others.was[bk][3] / n, others.was[bk][4] / n, others.now[bk][0] / n, others.now[bk][1] / n,
                   others.now[bk][2] / n, others.now[bk][3] / n, others.now[bk][4] / n);
    }
    for (int i = 0; i < CTL_MAX && ctls[i].vt; ++i) {
        double nn = ctls[i].near ? (double)ctls[i].near : 1, bn = ctls[i].bn ? (double)ctls[i].bn : 1;
        diag_event("soundstage-ctl: vt=%08X view=%u posoff=%+d ctl=%08X n=%llu near(<6m of player car)=%llu along=%+.2f side=%+.2f dist=%.2f bankvoices=%llu rear=%.2f C=%.2f FR=%.2f SR=%.2f SL=%.2f FL=%.2f",
                   ctls[i].vt, ctls[i].view, ctls[i].posoff, ctls[i].sample_ctl, (unsigned long long)ctls[i].n, (unsigned long long)ctls[i].near, ctls[i].along / nn, ctls[i].side / nn,
                   ctls[i].dist / nn, (unsigned long long)ctls[i].bn, ctls[i].bank_rear / bn, ctls[i].share[0] / bn, ctls[i].share[1] / bn,
                   ctls[i].share[2] / bn, ctls[i].share[3] / bn, ctls[i].share[4] / bn);
    }
    {   // the voice-record words that take few distinct values for a controller class: its sound identity
        static int done_at;
        if (++done_at % 30 == 0)
            for (int i = 0; i < CTL_MAX && ctls[i].vt; ++i) if (ctls[i].bn > 500) {
                char b[3000]; int q = snprintf(b, sizeof b, "soundstage-vrec: vt=%08X view=%u", ctls[i].vt, ctls[i].view);
                for (int w = 0; w < 36; ++w) {
                    int d = 0; for (; d < 8 && vhist[i][w].cnt[d]; ++d) {}
                    if (d >= 1 && d < 8) {
                        q += snprintf(b + q, sizeof b - q, " w%02d{", w);
                        for (int k = 0; k < d && q < (int)sizeof b - 60; ++k) q += snprintf(b + q, sizeof b - q, "%X:%llu%s", vhist[i][w].val[k], (unsigned long long)vhist[i][w].cnt[k], k + 1 < d ? "," : "");
                        q += snprintf(b + q, sizeof b - q, "}");
                    }
                }
                diag_event("%s", b);
                q = snprintf(b, sizeof b, "soundstage-slots: vt=%08X view=%u", ctls[i].vt, ctls[i].view);
                for (int sl = 0; sl < 64; ++sl) if (slotstat[i][sl].n > 50)
                    q += snprintf(b + q, sizeof b - q, " s%d{n=%llu life=%.1fs gain=%.3f w00=%X w08=%X}", sl, (unsigned long long)slotstat[i][sl].n,
                                  (slotstat[i][sl].last - slotstat[i][sl].first) / 1e9, slotstat[i][sl].gain / slotstat[i][sl].n, slotstat[i][sl].w00, slotstat[i][sl].w08);
                diag_event("%s", b);
            }
    }
    char cl[900]; int q = snprintf(cl, sizeof cl, "soundstage-callers:");
    for (int i = 0; i < 24 && callers[i].ret && q < (int)sizeof cl - 120; ++i)
        q += snprintf(cl + q, sizeof cl - q, " %08X[n=%llu C=%.2f FR=%.2f SR=%.2f SL=%.2f FL=%.2f]", callers[i].ret, (unsigned long long)callers[i].n,
                      callers[i].share[0] / callers[i].n, callers[i].share[1] / callers[i].n, callers[i].share[2] / callers[i].n,
                      callers[i].share[3] / callers[i].n, callers[i].share[4] / callers[i].n);
    diag_event("%s", cl);
}
