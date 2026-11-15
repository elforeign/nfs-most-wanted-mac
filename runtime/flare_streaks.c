// Lamp streaks with motion (NFS_FLARE_STREAKS, on unless 0/off): the later game's streak_flares vertex program puts
// a flare's head at the current and its tail at the previous frame's projection, widened across the motion, the
// tail colour halved. Here the world section flares eRenderLightFlare 00505380 draws for eRenderWorldLightFlares
// (call 00505F38, type 0, main views) are stretched on the CPU before Render 006BE750 submits their ePoly.
// The streak is the lamp's screen velocity over a fixed exposure, so its length does not depend on the frame
// rate; it scales by the night factor (car lamp level with the day-night clock on, 0 without it: daytime), so
// Off and day draw retail. No per-lamp history: an occluded lamp just stops drawing, no trail is left behind.
#include "host.h"
#include "daynight.h"
#include <math.h>
#include <stdlib.h>
#include <strings.h>
#include <time.h>

extern uint64_t frame_serial;               // render.m: the frame being encoded
uint64_t diag_current_frame(void);

enum {
    RENDER_RET = 0x00505E4Eu,               // 00505380's Render (ePoly) call
    WORLD_RET = 0x00505F3Du,                // eRenderWorldLightFlares 00505E80's eRenderLightFlare call
    VIEW_MAIN1 = 0x00919650u, VIEW_MAIN2 = 0x009196C0u,
};
#define EXPOSURE (1.0f / 30.0f)             // seconds of motion one streak shows
#define MAX_DT 0.1                          // a longer frame (hitch, resume) draws retail
#define CUT_SPEED 120.0f                    // m/s eye speed above which the camera cut
#define CUT_TURN 6.0f                       // rad/s view turn above which the camera cut
#define MAX_STRETCH 8.0f                    // streak at most 8 billboard lengths

typedef struct { float m[16], eye[3]; double t; uint64_t serial; } StreakCamera;

static int enabled = -1, daynight = -1;
static StreakCamera views[2][2];            // [view][0 previous, 1 current]

// One flare quad (four float4 corners at 16-byte steps, texture coordinates, colours ARGB) turned into the streak
// of the lamp's screen motion between prev and cur. Returns the streak length in world units at the lamp
// (0: unchanged).
float flare_streak_apply(float *corner, float *uv, uint32_t *colour, const StreakCamera *prev, const StreakCamera *cur,
                         float factor) {
    double dt = cur->t - prev->t;
    if (factor <= 0 || prev->serial + 1 != cur->serial || !(dt > 0) || dt > MAX_DT) return 0;
    const float *a = prev->m, *b = cur->m;
    float move = 0, turn = b[2] * a[2] + b[6] * a[6] + b[10] * a[10];
    for (int j = 0; j < 3; ++j) move += (cur->eye[j] - prev->eye[j]) * (cur->eye[j] - prev->eye[j]);
    if (sqrtf(move) > CUT_SPEED * dt || acosf(fminf(1, fmaxf(-1, turn))) > CUT_TURN * dt) return 0;
    float c[3] = {0};
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 3; ++j) c[j] += corner[i * 4 + j] * .25f;
    float vn[3], vp[3];
    for (int k = 0; k < 3; ++k) {
        vn[k] = c[0] * b[k] + c[1] * b[4 + k] + c[2] * b[8 + k] + b[12 + k];
        vp[k] = c[0] * a[k] + c[1] * a[4 + k] + c[2] * a[8 + k] + a[12 + k];
    }
    if (vn[2] < .1f || vp[2] < .1f) return 0;
    // Screen-plane motion from now to the previous frame, as a world offset at the lamp's current depth.
    float scale = vn[2] * EXPOSURE / (float)dt * factor;
    float dx = (vp[0] / vp[2] - vn[0] / vn[2]) * scale, dy = (vp[1] / vp[2] - vn[1] / vn[2]) * scale;
    float d[3] = {b[0] * dx + b[1] * dy, b[4] * dx + b[5] * dy, b[8] * dx + b[9] * dy};
    float len = sqrtf(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    if (!(len > 1e-6f)) return 0;
    // Plane axes: m along the motion (towards the tail), n across it, both in the camera-facing quad's plane.
    float m[3] = {d[0] / len, d[1] / len, d[2] / len}, f[3] = {b[2], b[6], b[10]};
    float n[3] = {f[1] * m[2] - f[2] * m[1], f[2] * m[0] - f[0] * m[2], f[0] * m[1] - f[1] * m[0]};
    float nl = sqrtf(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (!(nl > 1e-6f)) return 0;
    for (int j = 0; j < 3; ++j) n[j] /= nl;
    float along[4], across[4], h = 0, w = 0;
    for (int i = 0; i < 4; ++i) {
        along[i] = across[i] = 0;
        for (int j = 0; j < 3; ++j) {
            along[i] += (corner[i * 4 + j] - c[j]) * m[j];
            across[i] += (corner[i * 4 + j] - c[j]) * n[j];
        }
        h = fmaxf(h, fabsf(along[i])); w = fmaxf(w, fabsf(across[i]));
    }
    if (!(h > 0) || !(w > 0)) return 0;
    len = fminf(len, MAX_STRETCH * 2 * h);
    // The billboard's texture coordinates as an affine function of (along, across), from corners 0..2.
    float det = (along[1] - along[0]) * (across[2] - across[0]) - (along[2] - along[0]) * (across[1] - across[0]);
    if (fabsf(det) < 1e-9f * h * w) return 0;
    float tex[2][3];                                    // u, v = k0 + k1 * along + k2 * across
    for (int k = 0; k < 2; ++k) {
        float d1 = uv[2 + k] - uv[k], d2 = uv[4 + k] - uv[k];
        tex[k][1] = (d1 * (across[2] - across[0]) - d2 * (across[1] - across[0])) / det;
        tex[k][2] = (d2 * (along[1] - along[0]) - d1 * (along[2] - along[0])) / det;
        tex[k][0] = uv[k] - tex[k][1] * along[0] - tex[k][2] * across[0];
    }
    // The streak, drawn over the unchanged billboard (the later game draws both techniques): the glow's tail half
    // in the texture's own axes, its centre line from the lamp (texture core, full colour) to the tail (texture edge,
    // colour x(1 - dim)), its long edges on the texture border, so it fades on every side whatever the billboard's
    // spin (the billboard's rotated mapping put the edges outside the texture). Width = the glow's radius.
    float g = sqrtf(tex[0][2] * tex[0][2] + tex[1][2] * tex[1][2]);
    if (!(g > 0)) return 0;
    w = .5f / g;
    float sa[4] = {0, 0, len, len}, sb[4] = {-w, w, w, -w};
    float wind = (along[1] - along[0]) * (across[2] - across[0]) - (along[2] - along[0]) * (across[1] - across[0]);
    if (wind < 0) { sb[0] = sb[3] = w; sb[1] = sb[2] = -w; }
    float dim = .5f * fminf(1, len / (2 * h));
    for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 3; ++j) corner[i * 4 + j] = c[j] + m[j] * sa[i] + n[j] * sb[i];
        uv[i * 2] = tex[0][0] + (sa[i] > 0 ? .5f : 0); uv[i * 2 + 1] = tex[1][0] + (sb[i] > 0 ? .5f : -.5f);
        float keep = sa[i] > 0 ? 1 - dim : 1;
        uint32_t v = colour[0], out = 0;
        for (int s = 0; s < 32; s += 8) out |= (uint32_t)lrintf((float)((v >> s) & 255) * keep) << s;
        colour[i] = out;
    }
    return len;
}

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static int on_env(const char *name) {
    const char *e = getenv(name);
    return e && (!strcmp(e, "1") || !strcasecmp(e, "on"));
}
// Streaks default to on; only an explicit 0/off turns them off.
static int streaks_env(void) {
    const char *e = getenv("NFS_FLARE_STREAKS");
    return !e || !(!strcmp(e, "0") || !strcasecmp(e, "off"));
}

// BOOT_DIAG: the longest streak in each 600 frames, in pixels on the view's projection (head to tail), and the
// same lamp's billboard length along that direction (what Off draws).
static uint64_t shot_frame;
static unsigned shot_streaks;
static float shot_on, shot_off;
static void screen(uint32_t view, const float *p, float out[2]) {
    uint32_t vp = M32(view) + 0x80;
    float r[4];
    for (int k = 0; k < 4; ++k)
        r[k] = p[0] * MF32(vp + k * 4) + p[1] * MF32(vp + 16 + k * 4) + p[2] * MF32(vp + 32 + k * 4) + MF32(vp + 48 + k * 4);
    float w = r[3] != 0 ? 1 / r[3] : 0;
    out[0] = (r[0] * w + 1) * (float)(int32_t)M32(0x00982BE4u) * .5f;
    out[1] = (r[1] * w - 1) * (float)(int32_t)M32(0x00982BE8u) * -.5f;
}
// Screen distance from the lamp's centre to the streak's texture core (the point whose texture coordinates are the
// billboard's centre): 0 when the streak starts on the lamp.
static float core_gap(const float s[4][2], const float *uv, const float *lamp, const float *uv0) {
    float centre[2] = {0}, a[2][2], st[2];
    for (int i = 0; i < 4; ++i) { centre[0] += uv0[i * 2] * .25f; centre[1] += uv0[i * 2 + 1] * .25f; }
    for (int k = 0; k < 2; ++k) { a[k][0] = uv[2 + k] - uv[k]; a[k][1] = uv[4 + k] - uv[k]; }
    float det = a[0][0] * a[1][1] - a[0][1] * a[1][0];
    if (fabsf(det) < 1e-12f) return -1;
    float du = centre[0] - uv[0], dv = centre[1] - uv[1];
    float p = (du * a[1][1] - dv * a[0][1]) / det, q = (dv * a[0][0] - du * a[1][0]) / det;
    for (int k = 0; k < 2; ++k) st[k] = s[0][k] + p * (s[1][k] - s[0][k]) + q * (s[2][k] - s[0][k]);
    return hypotf(st[0] - lamp[0], st[1] - lamp[1]);
}
static float shot_gap;
static void diag_streak(uint32_t view, const float *before, const float *after, const float *uv0, const float *uv) {
    if (frame_serial / 600 != shot_frame / 600) {
        if (shot_frame)
            diag_event("flare_streaks: frames to %llu factor=%.3f streaks=%u longest_px=%.1f billboard_px=%.1f "
                       "core_gap_px=%.1f",
                       (unsigned long long)shot_frame, (double)(daynight ? daynight_lamp_level() : 0), shot_streaks,
                       (double)shot_on, (double)shot_off, (double)shot_gap);
        shot_streaks = 0; shot_on = shot_off = 0;
    }
    shot_frame = frame_serial;
    // Screen extent along the streak (the stretched quad's longest corner pair), before and after.
    float sa[4][2], sb[4][2], u[2] = {0}, best = 0;
    for (int i = 0; i < 4; ++i) { screen(view, after + i * 4, sa[i]); screen(view, before + i * 4, sb[i]); }
    for (int i = 0; i < 4; ++i) for (int j = i + 1; j < 4; ++j) {
        float x = sa[j][0] - sa[i][0], y = sa[j][1] - sa[i][1], l = hypotf(x, y);
        if (l > best) { best = l; u[0] = x / l; u[1] = y / l; }
    }
    float lo = 1e30f, hi = -1e30f;
    for (int i = 0; i < 4; ++i) { float a = sb[i][0] * u[0] + sb[i][1] * u[1]; lo = fminf(lo, a); hi = fmaxf(hi, a); }
    float on = best, off = hi - lo, mid[3] = {0}, lamp[2];
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 3; ++j) mid[j] += before[i * 4 + j] * .25f;
    screen(view, mid, lamp);
    float gap = core_gap(sa, uv, lamp, uv0);
    // BOOT_DIAG_STREAK_SHOTS=<px>: a full-size shot of up to 8 frames (a second apart) whose streak is at least
    // that long and wholly on screen, clear of the HUD's top elements, with its sim time for a
    // matched Off launch (BOOT_DIAG_SIMSHOTS: a simshot is a held pass, so it shows the billboard, never a streak).
    static int shot_px = -1, shots;
    static uint64_t shot_last;
    if (shot_px < 0) { const char *e = getenv("BOOT_DIAG_STREAK_SHOTS"); shot_px = e ? atoi(e) : 0; }
    if (shot_px > 0 && on >= shot_px && shots < 8 && frame_serial >= shot_last + 60) {
        float w = (float)(int32_t)M32(0x00982BE4u), hgt = (float)(int32_t)M32(0x00982BE8u);
        int inside = 1;
        for (int i = 0; i < 4; ++i) inside &= sa[i][0] >= 0 && sa[i][0] <= w && sa[i][1] >= 0 && sa[i][1] <= hgt;
        // Clear of the HUD's top elements: the lap timer (top right) and the bar (top centre), corners and centre.
        for (int i = 0; i <= 4; ++i) {
            float x = i < 4 ? sa[i][0] : (sa[0][0] + sa[2][0]) * .5f, y = i < 4 ? sa[i][1] : (sa[0][1] + sa[2][1]) * .5f;
            inside &= !(x > .76f * w && y < .3f * hgt) && !(x > .36f * w && x < .64f * w && y < .2f * hgt);
        }
        if (inside) {
            uint64_t f = diag_current_frame() + 1;
            diag_shot_request(f);
            diag_event("flare_streaks: shot frame=%llu sim=%.4f streak_px=%.1f billboard_px=%.1f core_gap_px=%.1f "
                       "lamp=%.0f,%.0f head=%.1f,%.1f tail=%.1f,%.1f half_width_px=%.1f", (unsigned long long)f,
                       (double)MF32(0x009885D8u), (double)on, (double)(hi - lo), (double)gap, (double)lamp[0],
                       (double)lamp[1], (double)(sa[0][0] + sa[1][0]) * .5, (double)(sa[0][1] + sa[1][1]) * .5,
                       (double)(sa[2][0] + sa[3][0]) * .5, (double)(sa[2][1] + sa[3][1]) * .5,
                       (double)hypotf(sa[1][0] - sa[0][0], sa[1][1] - sa[0][1]) * .5);
            shot_last = frame_serial; ++shots;
        }
    }
    ++shot_streaks;
    if (on > shot_on) { shot_on = on; shot_off = off; shot_gap = gap; }
}

// BOOT_DIAG (switch on): flare polygons of the main views every 600 frames, world section lamps (what streaks)
// against car and pool flares (which keep their billboards).
static uint64_t count_frame;
static unsigned count_world, count_other, count_dark, count_still;   // dark: night factor 0; still: no streak
static void diag_count(Cpu *c) {
    if (c->ecx != VIEW_MAIN1 && c->ecx != VIEW_MAIN2) return;
    if (frame_serial / 600 != count_frame / 600) {
        if (count_frame)
            diag_event("flare_streaks: frames %llu..%llu world_lamp_polys=%u other_flare_polys=%u dark=%u unstreaked=%u",
                       (unsigned long long)(count_frame / 600 * 600), (unsigned long long)(frame_serial - 1),
                       count_world, count_other, count_dark, count_still);
        count_world = count_other = count_dark = count_still = 0;
    }
    count_frame = frame_serial;
    if (M32(c->ebp + 4) == WORLD_RET && M32(c->ebp + 0x1c) == 0) ++count_world; else ++count_other;
}

// From the Render 006BE750 hook (daynight_sky.c), before the polygon is submitted.
void flare_streaks_poly(Cpu *c) {
    if (enabled < 0) { enabled = streaks_env(); daynight = on_env("NFS_DAYNIGHT"); }
    if (!enabled || M32(c->esp) != RENDER_RET) return;
    if (diag_on) diag_count(c);
    if (M32(c->ebp + 4) != WORLD_RET || M32(c->ebp + 0x1c) != 0) return;
    uint32_t view = c->ecx, poly = M32(c->esp + 4), camera = view ? M32(view + 0x40) : 0;
    int slot = view == VIEW_MAIN1 ? 0 : view == VIEW_MAIN2 ? 1 : -1;
    if (slot < 0 || !poly || !camera) return;
    StreakCamera *s = views[slot];
    if (s[1].serial != frame_serial) {
        s[0] = s[1];
        for (int k = 0; k < 16; ++k) s[1].m[k] = MF32(camera + k * 4);
        for (int j = 0; j < 3; ++j) s[1].eye[j] = MF32(camera + 0x40 + j * 4);
        s[1].t = now_seconds();
        s[1].serial = frame_serial;
    }
    float factor = daynight ? daynight_lamp_level() : 0;
    if (factor <= 0) { count_dark += diag_on; return; }
    float corner[16], before[16], uv[8], uv0[8];
    uint32_t colour[4];
    for (int k = 0; k < 16; ++k) before[k] = corner[k] = MF32(poly + k * 4);
    for (int k = 0; k < 8; ++k) uv0[k] = uv[k] = MF32(poly + 0x40 + k * 4);
    for (int i = 0; i < 4; ++i) colour[i] = M32(poly + 0x80 + i * 4);
    if (flare_streak_apply(corner, uv, colour, &s[0], &s[1], factor) <= 0) { count_still += diag_on; return; }
    Cpu saved = *c;                                  // the billboard as retail draws it, then the streak over it
    uint32_t args[5] = {poly, M32(c->esp + 8), M32(c->esp + 12), M32(c->esp + 16), M32(c->esp + 20)};
    call_guest(c, 0x006BE750u, 5, args);
    *c = saved;
    for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 3; ++j) MF32(poly + i * 16 + j * 4) = corner[i * 4 + j];
        MF32(poly + 0x40 + i * 8) = uv[i * 2]; MF32(poly + 0x44 + i * 8) = uv[i * 2 + 1];
        M32(poly + 0x80 + i * 4) = colour[i];
    }
    if (diag_on) diag_streak(view, before, corner, uv0, uv);
}
