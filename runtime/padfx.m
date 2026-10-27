// padfx.m - the DualSense's adaptive triggers and lightbar, from the race (stage 2 of the native DualSense).
//
// Both go out through GameController.framework (GCDualSenseGamepad.leftTrigger / rightTrigger, GCController.light),
// each behind its own switch, default off: NFS_PAD_TRIGGERS=on, NFS_PAD_LIGHT=on (F10 > Controls). The rumble stays
// rumble.m's (the X360 model on the compatible-vibration motors, NFS_RUMBLE). Nothing here touches a controller
// unless its switch is on and the pad is a real one: with BOOT_PAD=sim, or a switch off, the same decisions are made
// and BOOT_DIAG logs what would have been sent ("padfx:" lines in events.log, the "padfx" field of stats.log).
//
// Triggers follow the bindings (padbind_trigger_role): whichever of L2 / R2 drives
//   - GAS: light constant resistance; while the wheels spin under throttle it vibrates instead, as strongly as the
//     road surface's own wheelspin rumble curve says (feedback.c: the slip curve the X360 rumble model uses);
//   - BRAKE: resistance that builds with travel; at lock-up (wheels slipping while braking) it vibrates the same way;
//   - SHIFT UP / DOWN: weapon mode, a click that gives way at half travel, where the game takes the shift.
// Anything else, or outside a race (menus, pause, loading): off.
// Lightbar, in a race: the heat level's colour (green 1, yellow 3, orange 4, red 5, magenta 6+), red / blue police
// lights while cops see you in a pursuit, a slow blue pulse in cooldown; speedbreaker tints it pale cyan, nitrous
// blue-violet. Outside a race, and when the switch goes off or the game quits, the lightbar gets its own colour back.
// The player LEDs are left unset (playerIndex unset) while the light is ours.
//
// Game state, read on each LocalPlayer::DoFFB (006EE490, the player's sim tick, from feedback.c):
//   - speedbreaker: LocalPlayer +0x80 (SetGameBreaker 006E9AA0 writes it; ToggleGameBreaker 006EDD10 reads it as
//     IPlayer +0x34, the IPlayer sub-object being LocalPlayer +0x4C), charge +0x84 (DoGameBreaker 006EDD60);
//   - pursuit and heat: GManager::GetPlayerPursuitInterfaces (005EB9E0; args IPursuit **, IPerpetrator **, ret 8): the
//     first player's IVehicleAI::GetPursuit (vtable +0xA0, AIVehicle 00431D70) and its IPerpetrator. Heat: IPerpetrator
//     +0x1C (GetHeat 00409400 `fld [ecx+0x1c]`, slot +4 of the IPerpetrator vtables 00892250 / 008925D8, checked
//     before reading). Pursuit: AIPursuit's IPursuit vtable 00892770 (checked): IsPerpInSight (+0x28, 00433910) =
//     byte +0x114, GetCoolDownTimeRemaining (+0x20, 004338E0) = +0x100, GetCoolDownTimeRequired (+0x24, 004338F0) =
//     +0x104 - 7, GetPursuitStatus (+0x114, 00433B60) = +0x218.
#import <Foundation/Foundation.h>
#import <GameController/GameController.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>

#include "host.h"

#define FN_PLAYER_PURSUIT 0x005EB9E0u   // GManager::GetPlayerPursuitInterfaces(IPursuit **, IPerpetrator **)
#define FN_GET_HEAT 0x00409400u         // AIPerpVehicle::GetHeat, slot +4 of the IPerpetrator vtable
#define VT_IPURSUIT 0x00892770u         // AIPursuit's IPursuit vtable

// ---------------- switches ----------------
static int trig_sw = -1, light_sw = -1;   // -1: not read yet
static int env_on(const char *name) {
    const char *e = getenv(name);
    return e && (!strcmp(e, "on") || !strcmp(e, "1") || !strcasecmp(e, "true"));
}
static int triggers_on(void) { if (trig_sw < 0) trig_sw = env_on("NFS_PAD_TRIGGERS"); return trig_sw; }
static int light_on(void) { if (light_sw < 0) light_sw = env_on("NFS_PAD_LIGHT"); return light_sw; }
int padfx_wanted(void) { return triggers_on() || light_on() || diag_on; }

// ---------------- the race, from the player's sim tick ----------------
static struct {
    uint64_t tick_ns;                     // last DoFFB (0: none yet)
    int breaker; float charge;
    float heat;                           // -1: no perpetrator (or an unknown one)
    int pursuit, sight, status;           // sight -1: a pursuit of an unknown kind
    float cool_rem, cool_req;
} gs = {.heat = -1, .sight = -1};
void padfx_game_tick(Cpu *c, uint32_t lp) {
    static uint32_t out;
    if (!out) out = galloc(8);
    gs.tick_ns = host_ticks();
    gs.breaker = M8(lp + 0x80) != 0;
    gs.charge = MF32(lp + 0x84);
    M32(out) = M32(out + 4) = 0;
    uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx, a[2] = {out, out + 4};
    call_guest(c, FN_PLAYER_PURSUIT, 2, a);
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
    uint32_t pur = M32(out), perp = M32(out + 4);
    gs.heat = perp && M32(perp) && M32(M32(perp) + 4) == FN_GET_HEAT ? MF32(perp + 0x1C) : -1;
    gs.pursuit = pur != 0;
    if (pur && M32(pur) == VT_IPURSUIT) {
        gs.sight = M8(pur + 0x114) != 0;
        gs.cool_rem = MF32(pur + 0x100);
        gs.cool_req = MF32(pur + 0x104) - 7.0f;
        gs.status = (int)M32(pur + 0x218);
    } else {
        gs.sight = pur ? -1 : 0;
        gs.cool_rem = gs.cool_req = 0;
        gs.status = 0;
    }
}

// ---------------- what the pad should do ----------------
enum { T_OFF, T_FEEDBACK, T_SLOPE, T_WEAPON, T_VIBRATION };
typedef struct { int mode; float a, b, c, d; } Trig;   // feedback(start, strength) slope(start, end, s0, s1)
                                                        // weapon(start, end, strength) vibration(start, amp, freq)
typedef struct { int own; float r, g, b; } Light;      // own: the lightbar's own colour
static float q20(float v) { return roundf(fminf(fmaxf(v, 0), 1) * 20) / 20; }   // 0.05 steps: fewer reports
static Trig trig(int mode, float a, float b, float c, float d) { return (Trig){mode, q20(a), q20(b), q20(c), q20(d)}; }
static int trig_eq(Trig x, Trig y) { return x.mode == y.mode && x.a == y.a && x.b == y.b && x.c == y.c && x.d == y.d; }
static void trig_text(Trig t, char *buf, size_t n) {
    switch (t.mode) {
    case T_FEEDBACK: snprintf(buf, n, "feedback(%.2f,%.2f)", t.a, t.b); break;
    case T_SLOPE: snprintf(buf, n, "slope(%.2f-%.2f,%.2f-%.2f)", t.a, t.b, t.c, t.d); break;
    case T_WEAPON: snprintf(buf, n, "weapon(%.2f-%.2f,%.2f)", t.a, t.b, t.c); break;
    case T_VIBRATION: snprintf(buf, n, "vibration(%.2f,amp %.2f,freq %.2f)", t.a, t.b, t.c); break;
    default: snprintf(buf, n, "off");
    }
}
static const char *role_name(int r) {
    return r == PADBIND_ROLE_GAS ? "gas" : r == PADBIND_ROLE_BRAKE ? "brake" : r == PADBIND_ROLE_SHIFT ? "shift" :
           r == PADBIND_ROLE_OTHER ? "other" : "none";
}

// A slip level (0..1, from the surface's curve) as the trigger's vibration strength. The curves' own frequencies are
// pulse rates for the motors (about 1 Hz on asphalt), not a buzz: the trigger buzzes at a fixed rate instead,
// GameController's normalized 0..1 (0.2 for wheelspin, 0.3 for the brake's lock-up; to tune by hand).
static Trig vibration(float level, float freq, float floor) {
    return trig(T_VIBRATION, 0, floor + (1 - floor) * level, freq, 0);
}
static Trig trigger_for(int role, int racing, float self, float other_pedal, const float slip[2]) {
    if (!racing) return trig(T_OFF, 0, 0, 0, 0);
    float level = fmaxf(slip[0], slip[1]);
    switch (role) {
    case PADBIND_ROLE_GAS:                 // wheelspin: pressed, not braking
        if (self > 0.1f && other_pedal < 0.1f && level >= 0.05f) return vibration(level, 0.2f, 0.3f);
        return trig(T_FEEDBACK, 0.05f, 0.2f, 0, 0);
    case PADBIND_ROLE_BRAKE:               // lock-up: pressed, not on the gas
        if (self > 0.1f && other_pedal < 0.1f && level >= 0.05f) return vibration(level, 0.3f, 0.35f);
        if (@available(macOS 12.3, *)) return trig(T_SLOPE, 0.05f, 0.85f, 0.15f, 0.6f);
        return trig(T_FEEDBACK, 0.05f, 0.45f, 0, 0);
    case PADBIND_ROLE_SHIFT: return trig(T_WEAPON, 0.25f, 0.5f, 0.6f, 0);
    default: return trig(T_OFF, 0, 0, 0, 0);
    }
}

static void mix(float c[3], float r, float g, float b, float t) {
    c[0] += (r - c[0]) * t; c[1] += (g - c[1]) * t; c[2] += (b - c[2]) * t;
}
static Light light_for(int racing, int nos, uint64_t now) {
    if (!racing) return (Light){1, 0, 0, 0};
    static const float heat_rgb[7][3] = {{0.0f, 0.6f, 0.4f}, {0.0f, 1.0f, 0.0f}, {0.6f, 1.0f, 0.0f}, {1.0f, 0.8f, 0.0f},
                                         {1.0f, 0.4f, 0.0f}, {1.0f, 0.0f, 0.0f}, {1.0f, 0.0f, 0.4f}};
    float h = fminf(fmaxf(gs.heat, 0), 6), c[3];
    int i = (int)h;
    float t = h - (float)i;
    int j = i < 6 ? i + 1 : 6;
    for (int k = 0; k < 3; ++k) c[k] = heat_rgb[i][k] + (heat_rgb[j][k] - heat_rgb[i][k]) * t;
    double s = (double)now / 1e9;
    if (gs.pursuit && gs.sight != 0) {     // cops on you: red / blue, 4 changes a second
        if ((int)(s * 4) & 1) { c[0] = 0.0f; c[1] = 0.15f; c[2] = 1.0f; } else { c[0] = 1.0f; c[1] = 0.0f; c[2] = 0.0f; }
    } else if (gs.pursuit) {               // cooldown: blue breathing once every 2 s
        float v = 0.35f + 0.65f * (0.5f + 0.5f * (float)sin(s * M_PI));
        c[0] = 0.0f; c[1] = 0.1f * v; c[2] = v;
    }
    if (gs.breaker) mix(c, 0.7f, 0.9f, 1.0f, 0.6f);
    else if (nos) mix(c, 0.35f, 0.3f, 1.0f, 0.5f);
    return (Light){0, roundf(c[0] * 255) / 255, roundf(c[1] * 255) / 255, roundf(c[2] * 255) / 255};
}

// ---------------- output (main queue; the game thread only posts what it wants) ----------------
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static struct { GCController *ctl; int triggers, light; Trig l, r; Light c; } want;   // mu
static Trig sent_l = {-1}, sent_r = {-1};
static Light sent_c = {-1};
static GCController *owner;               // main: the controller whose light / triggers are ours
static GCColor *own_color;                // main: its lightbar colour before we took it
static int light_taken, trig_taken;
static _Atomic int queued, closed;
static void apply_trig(GCDualSenseAdaptiveTrigger *t, Trig x) {
    switch (x.mode) {
    case T_FEEDBACK: [t setModeFeedbackWithStartPosition:x.a resistiveStrength:x.b]; break;
    case T_SLOPE:
        if (@available(macOS 12.3, *)) [t setModeSlopeFeedbackWithStartPosition:x.a endPosition:x.b startStrength:x.c endStrength:x.d];
        break;
    case T_WEAPON: [t setModeWeaponWithStartPosition:x.a endPosition:x.b resistiveStrength:x.c]; break;
    case T_VIBRATION: [t setModeVibrationWithStartPosition:x.a amplitude:x.b frequency:x.c]; break;
    default: [t setModeOff];
    }
}
static void release_pad(GCController *ctl) {   // main: triggers off, the lightbar's own colour back
    if (!ctl) return;
    if (trig_taken && [ctl.extendedGamepad isKindOfClass:GCDualSenseGamepad.class]) {
        GCDualSenseGamepad *g = (GCDualSenseGamepad *)ctl.extendedGamepad;
        [g.leftTrigger setModeOff];
        [g.rightTrigger setModeOff];
    }
    if (light_taken && ctl.light && own_color) ctl.light.color = own_color;
    trig_taken = light_taken = 0;
    sent_l = sent_r = (Trig){-1};
    sent_c = (Light){-1};
}
static void flush(void) {                 // main
    queued = 0;
    pthread_mutex_lock(&mu);
    GCController *ctl = want.ctl;
    int tr = want.triggers && !closed, li = want.light && !closed;
    Trig l = want.l, r = want.r;
    Light c = want.c;
    pthread_mutex_unlock(&mu);
    if (owner && owner != ctl) { release_pad(owner); owner = nil; }
    if (!ctl) return;
    owner = ctl;
    GCDualSenseGamepad *g = [ctl.extendedGamepad isKindOfClass:GCDualSenseGamepad.class] ? (GCDualSenseGamepad *)ctl.extendedGamepad : nil;
    if (g && tr) {
        if (!trig_eq(l, sent_l)) { apply_trig(g.leftTrigger, l); sent_l = l; }
        if (!trig_eq(r, sent_r)) { apply_trig(g.rightTrigger, r); sent_r = r; }
        trig_taken = 1;
    } else if (g && trig_taken) {
        [g.leftTrigger setModeOff]; [g.rightTrigger setModeOff];
        trig_taken = 0; sent_l = sent_r = (Trig){-1};
    }
    if (ctl.light && li) {
        if (!light_taken) {
            own_color = ctl.light.color;
            ctl.playerIndex = GCControllerPlayerIndexUnset;
            light_taken = 1;
        }
        if (c.own != sent_c.own || c.r != sent_c.r || c.g != sent_c.g || c.b != sent_c.b) {
            if (!c.own) ctl.light.color = [[GCColor alloc] initWithRed:c.r green:c.g blue:c.b];
            else if (own_color) ctl.light.color = own_color;
            sent_c = c;
        }
    } else if (ctl.light && light_taken) {
        if (own_color) ctl.light.color = own_color;
        light_taken = 0; sent_c = (Light){-1};
    }
}
static void post(void) { if (!atomic_exchange(&queued, 1)) dispatch_async(dispatch_get_main_queue(), ^{ flush(); }); }

// Focus lost, the switch turned off, quitting: triggers off and the lightbar's own colour, at once.
static void stop_now(void) {
    pthread_mutex_lock(&mu);
    want.triggers = want.light = 0;
    pthread_mutex_unlock(&mu);
    if ([NSThread isMainThread]) { flush(); return; }
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    dispatch_async(dispatch_get_main_queue(), ^{ flush(); dispatch_semaphore_signal(done); });
    dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 200 * NSEC_PER_MSEC));
}
void padfx_stop(void) { if (owner || queued) stop_now(); }
void padfx_close(void) {                  // quitting (user32_request_close, exit, SIGTERM / SIGINT via rumble.m)
    if (atomic_exchange(&closed, 1)) return;
    if (owner || queued) {
        stop_now();
        if (diag_on) diag_event("padfx: closed, triggers off, lightbar back to its own colour");
    }
}
void padfx_set_triggers(int on) { trig_sw = on != 0; if (!on) padfx_stop(); if (diag_on) diag_event("padfx: triggers switch now %d", trig_sw); }
void padfx_set_light(int on) { light_sw = on != 0; if (!on) padfx_stop(); if (diag_on) diag_event("padfx: light switch now %d", light_sw); }

// ---------------- each pad read ----------------
static char diag_text[160];
void padfx_diag_text(char *buf, int n) { snprintf(buf, (size_t)n, "%s", diag_text[0] ? diag_text : "-"); }

void padfx_update(void *gc, int dualsense, float lt, float rt) {
    if (!padfx_wanted() || closed) return;
    uint64_t now = host_ticks();
    int racing = gs.tick_ns && now - gs.tick_ns < 300000000ull;
    int role[2] = {padbind_trigger_role(0), padbind_trigger_role(1)};
    float slip[2], hz[2], p[2] = {lt, rt};
    int nos;
    feedback_trigger_state(slip, hz, &nos);
    float pedal[2] = {0, 0};               // the other pedal on a trigger: gas [0] / brake [1] pressure
    for (int i = 0; i < 2; ++i) {
        if (role[i] == PADBIND_ROLE_GAS) pedal[0] = fmaxf(pedal[0], p[i]);
        if (role[i] == PADBIND_ROLE_BRAKE) pedal[1] = fmaxf(pedal[1], p[i]);
    }
    Trig t[2];
    for (int i = 0; i < 2; ++i)
        t[i] = dualsense ? trigger_for(role[i], racing, p[i], role[i] == PADBIND_ROLE_GAS ? pedal[1] : pedal[0], slip)
                         : trig(T_OFF, 0, 0, 0, 0);
    // a vibration's strength follows the slip at most 20 times a second (a mode change goes at once)
    static Trig held[2] = {{-1}, {-1}};
    static uint64_t held_at[2];
    for (int i = 0; i < 2; ++i) {
        if (t[i].mode == held[i].mode && !trig_eq(t[i], held[i]) && now - held_at[i] < 50000000ull) t[i] = held[i];
        else if (!trig_eq(t[i], held[i])) { held[i] = t[i]; held_at[i] = now; }
    }
    Light c = light_for(racing, nos, now);
    GCController *ctl = (__bridge GCController *)gc;
    int real = ctl != nil;
    int tr = real && dualsense && triggers_on(), li = real && light_on();
    if (real && (tr || li || owner)) {
        pthread_mutex_lock(&mu);
        int changed = want.ctl != ctl || want.triggers != tr || want.light != li || !trig_eq(want.l, t[0]) ||
                      !trig_eq(want.r, t[1]) || want.c.own != c.own || want.c.r != c.r || want.c.g != c.g || want.c.b != c.b;
        want.ctl = ctl; want.triggers = tr; want.light = li; want.l = t[0]; want.r = t[1]; want.c = c;
        pthread_mutex_unlock(&mu);
        if (changed) post();
        static int hooked;
        if (!hooked && (tr || li)) { hooked = 1; atexit(padfx_close); rumble_signals(); }
    }
    if (!diag_on) return;
    // BOOT_DIAG: each change of a trigger's mode or of the light (at most 400 lines), and a summary each second (the
    // modes and colour each second are in stats.log)
    char a[64], b[64];
    trig_text(t[0], a, sizeof a); trig_text(t[1], b, sizeof b);
    const char *out = !real ? "not sent: simulated pad" : !(tr || li) ? "not sent: switches off" : "sent";
    if (c.own) snprintf(diag_text, sizeof diag_text, "L2 %s R2 %s light own", a, b);
    else snprintf(diag_text, sizeof diag_text, "L2 %s R2 %s light %d,%d,%d", a, b, (int)lroundf(c.r * 255), (int)lroundf(c.g * 255),
                  (int)lroundf(c.b * 255));
    static Trig last[2] = {{-1}, {-1}};
    static Light lastc = {-1};
    static int lines;
    if ((!trig_eq(t[0], last[0]) || !trig_eq(t[1], last[1]) || c.own != lastc.own || c.r != lastc.r || c.g != lastc.g || c.b != lastc.b) &&
        lines < 400) {
        ++lines;
        diag_event("padfx: %s (%s)", diag_text, out);
        last[0] = t[0]; last[1] = t[1]; lastc = c;
    }
    static uint64_t next;
    static float peak_slip[2];
    peak_slip[0] = fmaxf(peak_slip[0], slip[0]); peak_slip[1] = fmaxf(peak_slip[1], slip[1]);
    if (now >= next) {
        next = now + 1000000000ull;
        // two lines: one would pass the event ring's line length
        diag_event("padfx: second racing %d dualsense %d roles L2 %s R2 %s pressure %.2f/%.2f slip peak rear %.2f front %.2f "
                   "(now %.2f %.0f Hz / %.2f %.0f Hz) nos %d", racing, dualsense, role_name(role[0]), role_name(role[1]), lt, rt,
                   peak_slip[0], peak_slip[1], slip[0], hz[0], slip[1], hz[1], nos);
        diag_event("padfx: heat %.2f pursuit %d sight %d cooldown %.1f/%.1f status %d breaker %d charge %.2f (%s)",
                   gs.heat, gs.pursuit, gs.sight, gs.cool_rem, gs.cool_req, gs.status, gs.breaker, gs.charge, out);
        peak_slip[0] = peak_slip[1] = 0;
    }
}
