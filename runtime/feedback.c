// feedback.c - console rumble: the X360 version's gamepad feedback model on the PC game.
//
// LocalPlayer::DoFFB (006EE490) and LocalPlayer::OnCollision (006F2370) drive the player's IFeedback every sim tick
// with the car's state: per wheel slip, skid and road surface, speed, shift quality, nitrous, engine damage, gear
// changes and collisions. On PC that interface's methods are empty stubs (the vtable at 008A7BD4), and DoFFB gives any
// DirectInput device to the steering-wheel force feedback instead (see f_006EE490 below). The X360's GameDevice
// implements them (vtable 820867C8; oscillator 823A3540, motor sums 823A36C8, XInput output 823A3A70, collision
// 823A4F30) as a two-motor rumble: oscillators set from the surface's rumble curves plus timed envelopes for events.
// This file puts that model in the PC vtable's slots, so the PC game rumbles like the console; rumble.m plays the two
// motor levels (left = low-frequency heavy motor, right = light) on the pad, scaled like the X360's level * 65535.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"
#include "gen/x360_engine.inc"   // X360E_FB_*: the console's envelope and collision numbers (the player's default.xex)

#define FEEDBACK_VTABLE 0x008A7BD4u      // IFeedback sub-object vtable of the PC input device's feedback object
#define FN_GET_ATTRIBUTE 0x00454810u     // Attrib::Instance::GetAttributePointer(this, key, index), thiscall
#define SIMABLES 0x0092FEB4u             // keyed table (handle -> ISimable): nodes {handle, ISimable *}, count at 0x0092FEBC
#define SIMABLE_COUNT 0x0092FEBCu
#define FN_KEYED_SEARCH 0x005D51F0u      // UTL::Collections::_KeyedNode::Search(begin, end, key), cdecl
#define FN_QUERY_VEHICLE 0x0040E040u     // UTL::COM::IUnknown::QueryInterface<IVehicle>(this, IVehicle **), thiscall
#define SIMABLE_LINEAR_VELOCITY 0x68     // ISimable::GetLinearVelocity(UMath::Vector3 &) const (PhysicsObject 00669590)

typedef struct { float phase, amp, freq, offset; } Osc;
typedef struct { float time, dur, start, end; } Env;
static struct {
    Osc osc[16];          // pairs (k, k+1) sum to the left motor, (k+2, k+3) to the right; 0-3 speed, 4-7 skid, 8-11 slip
    Env env[10];          // left/right: collision, shift kick, nitrous, engine damage, gear change
    float out_l, out_r;
    int active;
    int last_shift, nos, damage, gear;
} S;
static uint64_t last_tick;
// padfx.m's triggers: each axle's wheelspin / lock-up level from the surface's slip curve (the rumble model's own data,
// as its slip oscillators get it, whether or not the game's Rumble option is on), this DoFFB frame and the last one.
static float slip_now[2], hz_now[2], slip_pub[2], hz_pub[2];   // [0] rear, [1] front
static int nos_on;
static unsigned suppressed;
// These transitions run on the game/AppKit thread, like the IFeedback methods. The HID queue has its own atomic
// gate (rumble.m), so a report already queued cannot restore a nonzero value after this stop.
void feedback_suppress(unsigned reason, int on) {
    unsigned next = on ? suppressed | reason : suppressed & ~reason;
    if (next == suppressed) return;
    suppressed = next;
    memset(&S, 0, sizeof S);              // discard oscillators AND envelopes, including on the final resume
    last_tick = 0;
    memset(slip_now, 0, sizeof slip_now); memset(hz_now, 0, sizeof hz_now);
    memset(slip_pub, 0, sizeof slip_pub); memset(hz_pub, 0, sizeof hz_pub); nos_on = 0;
    rumble_suppress(suppressed != 0);
    if (diag_on) diag_event("feedback: %s %s (holds %02X), motors 0",
        on ? "suppress" : "resume", reason == FEEDBACK_MENU ? "menu" : reason == FEEDBACK_PAUSE ? "pause" :
        reason == FEEDBACK_FOCUS ? "focus" : "app", suppressed);
}
void feedback_poll_pause(void) {
    uint32_t sim = G ? M32(0x009885E0u) : 0;  // Sim::_mSystem; Sim::GetState (006E8D20), 4 = paused
    feedback_suppress(FEEDBACK_PAUSE, sim && M32(sim + 0x2C) == 4);
}
void feedback_trigger_state(float slip[2], float hz[2], int *nos) {
    memcpy(slip, slip_pub, sizeof slip_pub); memcpy(hz, hz_pub, sizeof hz_pub); *nos = nos_on;
}
static unsigned ncalls[16];                    // diag: calls per slot (slot / 4) since the last summary
static float peak_l, peak_r;
static float peak_x[3][2];                     // diag: largest x per channel (speed, skid, slip) and rear / front
static float peak_src[8][2];
static float peak_dv;                          // diag: largest collision strength                   // diag: largest level per source (speed, skid, slip, 5 envelopes) and motor
#define COUNT(slot) (ncalls[(slot) / 4]++)
// diag: the road surface under each tyre (collection -> name, filled by dump_surfaces), ticks per surface each second,
// and a motor trace (rumble.tsv: time, levels, the collision envelope, speed, each tyre's surface; collisions as # lines)
static struct { uint32_t coll; char name[32]; } snames[96];
static int nsnames, tyre_n;
static unsigned tyre_ticks[97];                 // [nsnames]: no surface (wheel in the air)
static int tyre_now[4];
static float speed_now;
static FILE *trace;
static int surf_index(uint32_t surf) {
    uint32_t coll = surf ? M32(surf + 4) : 0;
    for (int i = 0; i < nsnames; ++i) if (snames[i].coll == coll) return i;
    return 96;
}
static const char *surf_name(int i) { return i < nsnames ? snames[i].name : "-"; }
static void trace_open(void) {
    if (trace || !diag_on) return;
    char path[1100];
    snprintf(path, sizeof path, "%s/rumble.tsv", diag_dir);
    trace = fopen(path, "w");
    if (trace) fprintf(trace, "# ms\theavy\tlight\tcoll_env_s\tspeed\ttyres (FL FR RL RR)\n");
}

static float osc_eval(Osc *o, float dt_ms) {
    if (o->freq * o->amp > 0.0f) {
        o->phase += o->freq * dt_ms * 0.001f;
        if (o->phase > 1.0f) o->phase -= 1.0f;
        return sinf(o->phase * 6.2831855f) * o->amp + o->offset;
    }
    return o->offset;
}
static float env_eval(Env *e, float dt_ms) {
    if (e->time > 0 && e->dur > 0) {
        e->time -= dt_ms * 0.001f;
        float v = (e->start - e->end) / e->dur * e->time + e->end;
        return v < 0 ? 0 : v;
    }
    return 0;
}
// Called every pad read (rumble.m): advance by real time, as the X360 does each frame, and give the motor levels.
void feedback_last_levels(float *heavy, float *light) {    // the overlay's rumble bars: the levels last computed
    *heavy = !suppressed && S.active ? S.out_l : 0;
    *light = !suppressed && S.active ? S.out_r : 0;
}
void feedback_levels(float *heavy, float *light) {
    feedback_poll_pause();
    uint64_t now = host_ticks();
    float dt = last_tick ? (float)(now - last_tick) / 1e6f : 0;
    last_tick = now;
    if (dt > 0 && S.active && !suppressed) {
        float l = 0, r = 0;
        for (int k = 0; k < 16; k += 4) {
            float a = osc_eval(&S.osc[k], dt) + osc_eval(&S.osc[k + 1], dt), b = osc_eval(&S.osc[k + 2], dt) + osc_eval(&S.osc[k + 3], dt);
            if (k < 12) { peak_src[k / 4][0] = fmaxf(peak_src[k / 4][0], a); peak_src[k / 4][1] = fmaxf(peak_src[k / 4][1], b); }
            l = fmaxf(l, a); r = fmaxf(r, b);
        }
        for (int e = 0; e < 10; e += 2) {
            float a = env_eval(&S.env[e], dt), b = env_eval(&S.env[e + 1], dt);
            peak_src[3 + e / 2][0] = fmaxf(peak_src[3 + e / 2][0], a); peak_src[3 + e / 2][1] = fmaxf(peak_src[3 + e / 2][1], b);
            l = fmaxf(l, a); r = fmaxf(r, b);
        }
        S.out_l = fminf(fmaxf(l, 0), 1); S.out_r = fminf(fmaxf(r, 0), 1);
    }
    *heavy = !suppressed && S.active ? S.out_l : 0;
    *light = !suppressed && S.active ? S.out_r : 0;
    if (diag_on && S.active && trace) {
        static uint64_t last_line; static float lh = -1, ll = -1; static int lt[4];
        int changed = fabsf(*heavy - lh) > 0.005f || fabsf(*light - ll) > 0.005f || memcmp(lt, tyre_now, sizeof lt);
        if (changed || now - last_line >= 100000000ull) {
            fprintf(trace, "%.1f\t%.3f\t%.3f\t%.3f\t%.1f\t%s %s %s %s\n", now / 1e6, *heavy, *light, S.env[0].time, speed_now,
                    surf_name(tyre_now[0]), surf_name(tyre_now[1]), surf_name(tyre_now[2]), surf_name(tyre_now[3]));
            last_line = now; lh = *heavy; ll = *light; memcpy(lt, tyre_now, sizeof lt);
        }
    }
    if (diag_on) {
        static uint64_t next;
        peak_l = fmaxf(peak_l, *heavy); peak_r = fmaxf(peak_r, *light);
        if (now >= next) {
            next = now + 1000000000ull;
            static const char *nm[16] = {"-", "Stop", "Resume", "Reset", "BeginFrame", "End", "Speed", "Skid", "Slip",
                                         "EngineRPM", "ShiftKick", "Nos", "EngineDamage", "GearChange", "Collision", "-"};
            char b[400]; int n = 0;
            for (int i = 1; i < 15; ++i) if (ncalls[i]) n += snprintf(b + n, sizeof b - n, " %s %u", nm[i], ncalls[i]);
            diag_event("feedback: second active %d peak heavy %.2f light %.2f calls%s", S.active, peak_l, peak_r, n ? b : " none");
            if (S.active) {                 // two lines: one would pass the event ring's line length and lose the last sources
                diag_event("feedback: collision %.2f x speed %.2f/%.2f skid %.2f/%.2f slip %.2f/%.2f (rear/front)",
                           peak_dv, peak_x[0][0], peak_x[0][1], peak_x[1][0], peak_x[1][1], peak_x[2][0], peak_x[2][1]);
                diag_event("feedback: heavy/light: speed %.2f/%.2f skid %.2f/%.2f slip %.2f/%.2f collision %.2f/%.2f shift %.2f/%.2f "
                           "nos %.2f/%.2f damage %.2f/%.2f gear %.2f/%.2f",
                           peak_src[0][0], peak_src[0][1], peak_src[1][0], peak_src[1][1], peak_src[2][0], peak_src[2][1],
                           peak_src[3][0], peak_src[3][1], peak_src[4][0], peak_src[4][1], peak_src[5][0], peak_src[5][1],
                           peak_src[6][0], peak_src[6][1], peak_src[7][0], peak_src[7][1]);
            }
            if (S.active) {
                char t[400]; int m = 0;
                for (int i = 0; i <= 96; ++i)
                    if (tyre_ticks[i] && m < (int)sizeof t - 40) m += snprintf(t + m, sizeof t - m, " %s %u", surf_name(i), tyre_ticks[i]);
                diag_event("feedback: tyre ticks by surface%s", m ? t : " none");
            }
            memset(tyre_ticks, 0, sizeof tyre_ticks);
            if (trace) fflush(trace);
            memset(ncalls, 0, sizeof ncalls); peak_l = peak_r = 0;
            memset(peak_x, 0, sizeof peak_x); peak_dv = 0; memset(peak_src, 0, sizeof peak_src);
        }
    }
}

// ---- the IFeedback methods (thiscall; RET pops the arguments like the stubs' ret N)
static int fb_stop(Cpu *c) { COUNT(0x04); S.active = 0; rumble_stop(); RET(0, 0); }
static int fb_resume(Cpu *c) { COUNT(0x08); feedback_poll_pause(); S.active = !suppressed; RET(0, 0); }
static int fb_reset(Cpu *c) { COUNT(0x0C); S.active = 0; S.out_l = S.out_r = 0; memset(&S.env[0], 0, sizeof S.env[0]); RET(0, 0); }
static void dump_surfaces(Cpu *c);
static int fb_begin_frame(Cpu *c) {
    COUNT(0x10);
    if (diag_on) { dump_surfaces(c); trace_open(); tyre_n = 0; }
    for (int k = 0; k < 12; ++k) S.osc[k].amp = S.osc[k].freq = S.osc[k].offset = 0;   // phases kept
    memset(slip_now, 0, sizeof slip_now); memset(hz_now, 0, sizeof hz_now);
    RET(0, 0);
}
static int fb_end(Cpu *c) {
    COUNT(0x14);
    memcpy(slip_pub, slip_now, sizeof slip_now); memcpy(hz_pub, hz_now, sizeof hz_now);
    RET(0, 0);
}

// One rumble curve {f0, a0, o0, x0, f1, a1, o1, x1} at x into oscillator q (each value the largest asked this frame),
// as the X360's curve lerp (823802A8) and its Speed/Skid/Slip (823A4870/4968/4A78) do.
static void curve_into(const float r[8], float x, Osc *q) {
    if (!(r[3] <= x && r[3] < r[7])) return;
    float t = fminf((x - r[3]) / (r[7] - r[3]), 1.0f);
    q->freq = fmaxf(q->freq, r[0] + (r[4] - r[0]) * t);
    q->amp = fmaxf(q->amp, r[1] + (r[5] - r[1]) * t);
    q->offset = fmaxf(q->offset, r[2] + (r[6] - r[2]) * t);
}

// ---- beyond the X360, each with a switch back to the console's behaviour
// Rough paving: the game data gives cobble, stone and railroad only asphalt's wheelspin curve, so on the X360 they feel
// like asphalt. NFS_RUMBLE_SURFACES=on (default) adds a texture made up for the port, mostly on the light motor: an
// 8 -> 16 Hz flutter (light 0.12 at 3 m/s -> 0.28 peak at 40 m/s) and a faint heavy pulse (up to 0.06), both rising
// with speed. Concrete is left alone: it is ordinary road in much of the city. =x360: the console's curves only.
// Props: car-object contacts where my velocity changed by less than the soft-contact limit get the X360's ramp of my own speed (a
// speed range maps to 0 -> 1, FUN_820e0ed0 in 823A4F30), so a cardboard box at 50 m/s rumbled like a wall crash, full for 1 s.
// NFS_RUMBLE_PROPS=on (default): every contact with a non-vehicle object uses the X360's own world formula (a linear ramp of my speed change),
// with minimum strength/time so small signs still register. =x360: the console's formula and envelope unchanged.
// Vehicles: a full hit fades in 0.45 s; a running hit ignores equal/weaker peaks rather than prolonging a scrape.
// NFS_RUMBLE_VEHICLES=x360 restores the console's v-second envelope and contact extension.
static const float kVehicleHitFullFadeSeconds = 0.45f;
static const float kPropHitMinStrength = 0.25f;
static const float kPropHitMinSeconds = 0.10f;
static const float kRoughLight[8] = {8.0f, 0.04f, 0.08f, 3.0f, 16.0f, 0.08f, 0.20f, 40.0f};
static const float kRoughHeavy[8] = {10.0f, 0.00f, 0.00f, 3.0f, 12.0f, 0.03f, 0.03f, 40.0f};
static const uint32_t kRoughKeys[3] = {0x3830BABBu, 0x02FC0FC9u, 0x121453E7u};   // cobble, stone, railroad
static int rough_sw = -1, props_sw = -1, vehicles_sw = -1;
static int sw_on(const char *name) {
    const char *e = getenv(name);
    return !(e && (!strcmp(e, "x360") || !strcmp(e, "off") || !strcmp(e, "0")));
}
static int rough_on(void) { if (rough_sw < 0) rough_sw = sw_on("NFS_RUMBLE_SURFACES"); return rough_sw; }
static int props_on(void) { if (props_sw < 0) props_sw = sw_on("NFS_RUMBLE_PROPS"); return props_sw; }
static int vehicles_on(void) { if (vehicles_sw < 0) vehicles_sw = sw_on("NFS_RUMBLE_VEHICLES"); return vehicles_sw; }
void feedback_set_surfaces(int on) { rough_sw = on != 0; if (diag_on) diag_event("feedback: rough surfaces %s", on ? "on" : "x360"); }
void feedback_set_props(int on) { props_sw = on != 0; if (diag_on) diag_event("feedback: prop hits %s", on ? "by strength" : "x360"); }
#define SURFACE_CLASS 0x00988E0Cu        // _TheSurfaceClass (Attrib::Class *)
static uint32_t thiscall1(Cpu *c, uint32_t self, uint32_t fn, uint32_t a0);
static uint32_t collection_of(Cpu *c, uint32_t key) {   // Attrib::Class::GetCollection (00455960)
    uint32_t cls = M32(SURFACE_CLASS);
    return cls ? thiscall1(c, cls, 0x00455960u, key) : 0;
}
static int is_rough(Cpu *c, uint32_t surf) {
    static uint32_t coll[3];
    static int got;
    if (!got && M32(SURFACE_CLASS)) { for (int i = 0; i < 3; ++i) coll[i] = collection_of(c, kRoughKeys[i]); got = 1; }
    uint32_t mine = surf ? M32(surf + 4) : 0;
    return mine && (mine == coll[0] || mine == coll[1] || mine == coll[2]);
}
// Test switch NFS_TEST_SURFACE=cobble|stone|railroad|<hex key>: the model reads that surface under every tyre (only
// what the rumble sees; the physics keep the real road), to measure a surface on any route.
static uint32_t test_surface(Cpu *c, uint32_t surf) {
    static int state = -1;
    static uint32_t fake;
    if (state < 0) {
        const char *e = getenv("NFS_TEST_SURFACE");
        if (!e || !*e) { state = 0; return surf; }
        if (!M32(SURFACE_CLASS)) return surf;
        uint32_t key = !strcmp(e, "cobble") ? kRoughKeys[0] : !strcmp(e, "stone") ? kRoughKeys[1]
                     : !strcmp(e, "railroad") ? kRoughKeys[2] : (uint32_t)strtoul(e, NULL, 16);
        uint32_t coll = collection_of(c, key);
        state = 0;
        if (coll) { fake = galloc(64); thiscall1(c, fake, 0x006E9660u, coll); state = 1; }   // SimSurface::SimSurface(Collection *)
        if (diag_on) diag_event("feedback: test surface %s (%08X) %s", e, key, coll ? "under every tyre" : "not found");
    }
    return state ? fake : surf;
}

// The surface's rumble curve j for `key`: {f0, a0, o0, x0, f1, a1, o1, x1}, interpolated at x.
static void surface_curves(Cpu *c, uint32_t surf, uint32_t key, float x, int first) {
    if (!surf) return;
    for (uint32_t j = 0; j < 4; ++j) {
        uint32_t esp = c->esp, ecx = c->ecx;
        c->ecx = surf;
        uint32_t a[2] = {key, j};
        uint32_t p = call_guest(c, FN_GET_ATTRIBUTE, 2, a);
        c->esp = esp; c->ecx = ecx;
        if (!p) continue;
        float r[8];
        memcpy(r, G + p, sizeof r);
        curve_into(r, x, &S.osc[first + (int)j]);
    }
}
// The level a curve gives at x (its peak, amp + offset, 0..1) and that curve's frequency: the strongest of the 4.
static float surface_level(Cpu *c, uint32_t surf, uint32_t key, float x, float *hz) {
    float best = 0;
    *hz = 0;
    if (!surf) return 0;
    for (uint32_t j = 0; j < 4; ++j) {
        uint32_t esp = c->esp, ecx = c->ecx;
        c->ecx = surf;
        uint32_t a[2] = {key, j};
        uint32_t p = call_guest(c, FN_GET_ATTRIBUTE, 2, a);
        c->esp = esp; c->ecx = ecx;
        if (!p) continue;
        float r[8];
        memcpy(r, G + p, sizeof r);
        if (!(r[3] <= x && r[3] < r[7])) continue;
        float t = fminf((x - r[3]) / (r[7] - r[3]), 1.0f);
        float v = fminf(fmaxf(r[1] + (r[5] - r[1]) * t + r[2] + (r[6] - r[2]) * t, 0), 1);
        if (v > best) { best = v; *hz = r[0] + (r[4] - r[0]) * t; }
    }
    return best;
}
// Speed / Skid / Slip (bool isFront, const SimSurface &surface, float x): DoFFB 006EE699/6AC/6CE push x, &surface,
// then the front flag (the wheel index < 2) in bl/cl, upper bytes left over.
static void wheel_args(Cpu *c, int ch, float *x, int *front, uint32_t *surf) {   // ch: 0 speed, 1 skid, 2 slip
    *front = (int)(arg(c, 0) & 0xFF); *surf = test_surface(c, arg(c, 1)); *x = argf(c, 2);
    peak_x[ch][*front != 0] = fmaxf(peak_x[ch][*front != 0], *x);
}
static int fb_speed(Cpu *c) {
    COUNT(0x18);
    float x; int front; uint32_t surf;
    wheel_args(c, 0, &x, &front, &surf);
    if (diag_on) { int i = surf_index(surf); tyre_ticks[i]++; if (tyre_n < 4) tyre_now[tyre_n++] = i; speed_now = x; }
    if (S.active) surface_curves(c, surf, 0xBA1297DAu, x, 0);
    if (S.active && rough_on() && is_rough(c, surf)) { curve_into(kRoughHeavy, x, &S.osc[1]); curve_into(kRoughLight, x, &S.osc[2]); }
    RET(0, 3);
}
static int fb_skid(Cpu *c) {
    COUNT(0x1C);
    float x; int front; uint32_t surf;
    wheel_args(c, 1, &x, &front, &surf);
    if (front) x *= 0.25f;
    if (S.active) surface_curves(c, surf, 0x0C149044u, x, 4);
    RET(0, 3);
}
static int fb_slip(Cpu *c) {
    COUNT(0x20);
    float x; int front; uint32_t surf;
    wheel_args(c, 2, &x, &front, &surf);
    if (S.active) surface_curves(c, surf, 0x8FD11D27u, x, 8);
    if (padfx_wanted()) {
        float hz, v = surface_level(c, surf, 0x8FD11D27u, x, &hz);
        int k = front != 0;
        if (v > slip_now[k]) { slip_now[k] = v; hz_now[k] = hz; }
    }
    RET(0, 3);
}
static int fb_engine_rpm(Cpu *c) { COUNT(0x24); RET(0, 3); }          // no-op on X360
static int fb_shift_kick(Cpu *c) {                        // shift status: 3 good, 4 perfect, 5 missed window
    COUNT(0x28);
    int v = (int)arg(c, 0), last = S.last_shift;
    float k = v == 3 ? X360E_FB_SHIFT_GOOD : v == 4 ? X360E_FB_SHIFT_PERFECT : v == 5 ? 1.0f : 0.0f;
    if (last) memset(&S.env[2], 0, 2 * sizeof S.env[0]);
    S.last_shift = v;
    if (S.active && k > 0 && last != v && last < v) S.env[2] = S.env[3] = (Env){X360E_FB_SHIFT_TIME, X360E_FB_SHIFT_TIME, fminf(k, 1.0f), k};
    RET(0, 1);
}
static int fb_nos(Cpu *c) {                               // (engaged, capacity)
    COUNT(0x2C);
    int on = (int)(arg(c, 0) & 0xFF);
    nos_on = on;
    if (S.active && on != S.nos) { S.env[4] = on ? (Env){X360E_FB_NOS_TIME, X360E_FB_NOS_TIME, 1.0f, X360E_FB_NOS_END} : (Env){0}; S.nos = on; }
    RET(0, 2);
}
static int fb_engine_damage(Cpu *c) {
    COUNT(0x30);
    int on = (int)(arg(c, 0) & 0xFF);
    if (S.active && on != S.damage) {
        if (on) { S.env[6] = (Env){1, 1, 1.0f, X360E_FB_DAMAGE_END}; S.env[7] = (Env){1, 1, fminf(S.env[7].start, 1.0f), X360E_FB_DAMAGE_END}; }
        else S.env[6] = S.env[7] = (Env){0};
        S.damage = on;
    }
    RET(0, 1);
}
static int fb_gear_change(Cpu *c) {
    COUNT(0x34);
    int on = (int)(arg(c, 0) & 0xFF);
    if (S.active && on != S.gear) { if (on) S.env[8] = S.env[9] = (Env){X360E_FB_GEAR_TIME, X360E_FB_GEAR_TIME, X360E_FB_GEAR_LEVEL, X360E_FB_GEAR_LEVEL}; S.gear = on; }
    RET(0, 1);
}
static void vec(uint32_t p, float v[3]) { memcpy(v, G + p, 12); }
static float len3(const float v[3]) { return sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }
static float dist3(const float a[3], const float b[3]) { float d[3] = {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; return len3(d); }
static uint32_t scratch(void) { static uint32_t p; if (!p) p = galloc(16); return p; }   // guest out-parameters
static uint32_t thiscall1(Cpu *c, uint32_t self, uint32_t fn, uint32_t a0) {
    uint32_t esp = c->esp, ecx = c->ecx, a[1] = {a0};
    c->ecx = self;
    uint32_t r = call_guest(c, fn, 1, a);
    c->esp = esp; c->ecx = ecx;
    return r;
}
static uint32_t simable(Cpu *c, uint32_t handle) {       // as LocalPlayer::OnCollision finds a body
    if (!handle) return 0;
    uint32_t begin = M32(SIMABLES), a[3] = {begin, begin + 8 * M32(SIMABLE_COUNT), handle}, esp = c->esp;
    uint32_t node = call_guest(c, FN_KEYED_SEARCH, 3, a);
    c->esp = esp;
    return node ? M32(node + 4) : 0;
}
static void velocity_now(Cpu *c, uint32_t sim, float v[3]) {
    thiscall1(c, sim, M32(M32(sim) + SIMABLE_LINEAR_VELOCITY), scratch());
    vec(scratch(), v);
}
// Collision (const Sim::Collision::Info &, bool me is object A), as the X360's GameDevice (823A4F30) does it. Info:
// +0x1C type (x86 bitfield, low 3 bits: 1 car/object, 2 world, 3 ground), +0x3C / +0x4C the bodies' handles, +0x50 /
// +0x60 their velocities before the contact. The strength is how much the contact changed my velocity.
static int fb_collision(Cpu *c) {
    COUNT(0x38);
    uint32_t info = arg(c, 0);
    int me_a = (int)(arg(c, 1) & 0xFF);
    if (!S.active || !info) RET(0, 2);
    uint32_t me = simable(c, M32(info + (me_a ? 0x3C : 0x4C))), other = simable(c, M32(info + (me_a ? 0x4C : 0x3C)));
    if (!me) RET(0, 2);
    float now[3], before[3], other_now[3];
    velocity_now(c, me, now);
    vec(info + (me_a ? 0x50 : 0x60), before);
    float speed = len3(now), dv = dist3(now, before), dv_other = 0, v, duration = 0;
    int vehicle_hit = 0, prop_hit = 0;
    if (other) { velocity_now(c, other, other_now); dv_other = dist3(other_now, before); }
    switch (M32(info + 0x1C) & 7) {
    case 1:                                               // car or object
        vehicle_hit = other && (thiscall1(c, other, FN_QUERY_VEHICLE, scratch()) & 0xFF);
        if (props_on() && !vehicle_hit) {                  // a prop: by strength, with a small-contact nudge
            float strength = fminf(dv * X360E_FB_WORLD_SLOPE, 1.0f);
            v = fmaxf(strength, kPropHitMinStrength);
            duration = fmaxf(strength, kPropHitMinSeconds);
            prop_hit = 1;
            break;
        }
        if (dv < X360E_FB_HIT_SOFT) {
            // Hit from behind or alongside (both moving the same way): half of body A's speed before the contact (the
            // X360 takes the larger of |A| and |A|, so always A).
            int same = 0;
            if (vehicle_hit) {
                float mine[3], theirs[3];
                vec(info + (me_a ? 0x50 : 0x60), mine); vec(info + (me_a ? 0x60 : 0x50), theirs);
                float lm = len3(mine), lt = len3(theirs);
                for (int i = 0; i < 3; ++i) { if (lm != 0) mine[i] /= lm; if (lt != 0) theirs[i] /= lt; }
                if (mine[0] * theirs[0] + mine[1] * theirs[1] + mine[2] * theirs[2] > X360E_FB_SAME_DOT) {
                    float a[3];
                    vec(info + 0x50, a);
                    same = 1; dv = len3(a) * 0.5f;
                }
            }
            if (!same || dv <= X360E_FB_HIT_SOFT) {
                if (speed <= 2 || dv_other <= 2) RET(0, 2);
                v = fminf(fmaxf((speed - X360E_FB_RAMP_LO) / (X360E_FB_RAMP_HI - X360E_FB_RAMP_LO), 0), 1);
                break;
            }
        }
        v = fminf((dv - X360E_FB_HIT_SOFT) * X360E_FB_HIT_SLOPE, 1.0f);
        break;
    case 2: v = fminf(dv * X360E_FB_WORLD_SLOPE, 1.0f); break;   // world
    case 3: v = fminf((dv - 1.0f) * X360E_FB_GROUND_SLOPE, 1.0f); break;   // ground
    default: RET(0, 2);
    }
    int short_vehicle = vehicle_hit && vehicles_on();
    if (short_vehicle) duration = v * kVehicleHitFullFadeSeconds;
    else if (!prop_hit) duration = v;
    Env *e = &S.env[0];
    float previous_time = e->time;
    int restart = e->time <= 0 || v > e->start;           // compare to the running hit's peak, not its decayed level
    if (trace) fprintf(trace, "# %.1f collision type %u other %s dv %.2f speed %.1f dv_other %.2f -> %.2f (envelope was %.3f s)\n",
                       host_ticks() / 1e6, M32(info + 0x1C) & 7, !other ? "none" : (thiscall1(c, other, FN_QUERY_VEHICLE, scratch()) & 0xFF) ? "vehicle" : "object",
                       dv, speed, dv_other, v, S.env[0].time);
    peak_dv = fmaxf(peak_dv, v);
    if (v > 0) {                                          // left motor: strength and decay time are independent
        if (short_vehicle) {
            if (restart) { e->time = e->dur = duration; e->start = v; }
        } else if (e->time > 0) {
            e->dur = fmaxf(e->dur, duration); e->time = fmaxf(e->time, duration); e->start = fmaxf(e->start, v);
        } else { e->time = e->dur = duration; e->start = v; }
        e->end = 0;
    }
    if (diag_on) diag_event_ring("feedback: collision type %u dv %.1f speed %.1f other %.1f -> %.2f duration %.3f s env %.3f -> %.3f s %s",
        M32(info + 0x1C) & 7, dv, speed, dv_other, v, duration, previous_time, e->time,
        short_vehicle ? (restart ? "vehicle restart" : "vehicle held") : prop_hit ? "prop" : "x360");
    RET(0, 2);
}

// Diagnostics, once per run: every road surface's rumble curves (speed, skid, slip; up to 4 each) as the game resolves
// them (a surface inherits its parent's), in surfaces.txt. Collections are keyed by name hash: names come from the
// strings in GLOBAL/attributes.bin, hashed with the game's Attrib::StringHash32.
static uint32_t thiscall0(Cpu *c, uint32_t self, uint32_t fn) {
    uint32_t esp = c->esp, ecx = c->ecx;
    c->ecx = self;
    uint32_t r = call_guest(c, fn, 0, NULL);
    c->esp = esp; c->ecx = ecx;
    return r;
}
static void dump_surfaces(Cpu *c) {
    static int done;
    uint32_t cls = M32(SURFACE_CLASS);
    if (done || !cls) return;
    done = 1;
    char path[1100];
    snprintf(path, sizeof path, "%s/surfaces.txt", diag_dir);
    FILE *out = fopen(path, "w");
    if (!out) return;
    struct name { uint32_t key; char name[48]; } *names = calloc(4096, sizeof *names);
    int nn = 0;
    snprintf(path, sizeof path, "%s/GLOBAL/attributes.bin", game_dir);
    FILE *f = fopen(path, "rb");
    if (f) {
        uint32_t buf = galloc(64);
        char run[64]; int len = 0, ch;
        while ((ch = fgetc(f)) != EOF && nn < 4096) {
            if (ch >= 0x20 && ch < 0x7F && len < 47) { run[len++] = (char)ch; continue; }
            if (len >= 3 && ch == 0) {
                run[len] = 0;
                memcpy(G + buf, run, (size_t)len + 1);
                uint32_t esp = c->esp, a[1] = {buf};
                names[nn].key = call_guest(c, 0x004519D0u, 1, a);   // Attrib::StringHash32, cdecl
                c->esp = esp;
                memcpy(names[nn].name, run, (size_t)len + 1);
                ++nn;
            }
            len = 0;
        }
        fclose(f);
    }
    static const struct { uint32_t key; const char *name; } ch3[3] = {{0xBA1297DAu, "speed"}, {0x0C149044u, "skid"}, {0x8FD11D27u, "slip"}};
    uint32_t surf = galloc(64);
    int count = 0;
    fprintf(out, "# rumble curves per surface: {freq Hz, amp, offset} at x0 -> at x1 (x: m/s for speed, slip/skid for the others);"
                 " curves 0-1 drive the heavy (left) motor, 2-3 the light (right) one\n");
    for (uint32_t k = thiscall0(c, cls, 0x00456B00u); k; k = thiscall1(c, cls, 0x00456B20u, k)) {
        uint32_t coll = thiscall1(c, cls, 0x00455960u, k);
        if (!coll) continue;
        const char *nm = "?";
        for (int i = 0; i < nn; ++i) if (names[i].key == k) { nm = names[i].name; break; }
        thiscall1(c, surf, 0x006E9660u, coll);                  // SimSurface::SimSurface(Collection *)
        fprintf(out, "%08X %s (collection %08X)\n", k, nm, coll);
        if (nsnames < 96) { snames[nsnames].coll = coll; snprintf(snames[nsnames].name, sizeof snames[0].name, "%s", nm); ++nsnames; }
        for (int q = 0; q < 3; ++q)
            for (uint32_t j = 0; j < 4; ++j) {
                uint32_t esp = c->esp, ecx = c->ecx, a[2] = {ch3[q].key, j};
                c->ecx = surf;
                uint32_t p = call_guest(c, FN_GET_ATTRIBUTE, 2, a);
                c->esp = esp; c->ecx = ecx;
                if (!p) continue;
                float r[8];
                memcpy(r, G + p, sizeof r);
                fprintf(out, "    %-5s %u (%s): {%.2f %.2f %.2f} @%.1f -> {%.2f %.2f %.2f} @%.1f%s\n", ch3[q].name, j,
                        j < 2 ? "heavy" : "light", r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                        r[3] < r[7] ? "" : "  (never used: x0 >= x1)");
            }
        thiscall0(c, surf, 0x004690B0u);                         // ~SimSurface
        ++count;
    }
    fclose(out);
    free(names);
    diag_event("feedback: %d surfaces written to surfaces.txt (%d names hashed)", count, nn);
}

void feedback_setup(void) {                               // boot: fill the PC vtable's empty slots
    static const struct { uint32_t slot; HostFn fn; const char *name; } m[] = {
        {0x04, fb_stop, "Stop"}, {0x08, fb_resume, "Resume"}, {0x0C, fb_reset, "Reset"}, {0x10, fb_begin_frame, "BeginFrame"},
        {0x14, fb_end, "End"}, {0x18, fb_speed, "Speed"}, {0x1C, fb_skid, "Skid"}, {0x20, fb_slip, "Slip"},
        {0x24, fb_engine_rpm, "EngineRPM"}, {0x28, fb_shift_kick, "ShiftKick"}, {0x2C, fb_nos, "Nos"},
        {0x30, fb_engine_damage, "EngineDamage"}, {0x34, fb_gear_change, "GearChange"}, {0x38, fb_collision, "Collision"}};
    char name[48];
    for (unsigned i = 0; i < sizeof m / sizeof *m; ++i) {
        snprintf(name, sizeof name, "IFeedback::%s", m[i].name);
        M32(FEEDBACK_VTABLE + m[i].slot) = host_fn(m[i].fn, strdup(name));
    }
}

// LocalPlayer::DoFFB (006EE490), the console's way. On PC any DirectInput device on the player's port counts as a
// steering wheel (ISteeringWheel +0x0C only checks the DirectInput manager's device slot for the port), and DoFFB then
// hands the frame to the wheel's force feedback (+0x04) and returns before the IFeedback calls. The X360 has no wheel
// branch: DoFFB runs with the player's wheel (+0x5C) hidden, so Resume, BeginFrame, the per-wheel Slip / Skid / Speed,
// EngineRPM, ShiftKick, Nos, EngineDamage, GearChange and End reach the model above. Input still sees the device.
void f_006EE490_orig(Cpu *c);
void f_006EE490(Cpu *c) {
    uint32_t self = c->ecx, wheel = M32(self + 0x5C);
    M32(self + 0x5C) = 0;
    f_006EE490_orig(c);
    M32(self + 0x5C) = wheel;
    if (padfx_wanted()) padfx_game_tick(c, self);   // the lightbar's race state (padfx.m)
}

// LocalPlayer::CanDoFFB (006EE400): false while the AI drives the player's car (IHumanAI +0x0C), so BOOT_AUTODRIVE runs
// never reach the model. Test switch NFS_FFB_AUTODRIVE=1 (diag runs): the model runs then too, with the game's Rumble
// option still required, so an AI-driven route through town shows the surfaces and collisions in rumble.tsv.
void f_006EE400_orig(Cpu *c);
void f_006EE400(Cpu *c) {
    static int force = -1;
    if (force < 0) { const char *e = getenv("NFS_FFB_AUTODRIVE"); force = e && !strcmp(e, "1"); }
    f_006EE400_orig(c);
    if (force && !(c->eax & 0xFF) && M32(0x008F86E0u)   /* bRumbleEnabled */) c->eax = (c->eax & ~0xFFu) | 1;
}
