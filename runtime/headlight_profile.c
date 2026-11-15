/* Generic right-hand-traffic low beam: regulation-bounded, not a measured lamp.
 * Source: UN Regulation 112, 01 series, section 6.2.4, Class B, Annex 3:
 * https://eur-lex.europa.eu/eli/reg/2014/112/oj/eng
 * Bounds used: 75R (+1.15,-0.57) >= 10100 cd; B50L (-3.43,+0.57)
 * <= 350 cd; 25L/25R (+/-9,-1.72) >= 1700 cd. Additional anchors:
 * 50R (+1.72,-0.86) >= 10100; 50V (0,-0.86) >= 5100 cd.
 * Original grid/implementation: MIT, see ../LICENSE. Only public regulatory
 * numeric constraints are used; intermediate values are authored assumptions.
 * No measured dataset is embedded: redistribution permission for a complete
 * measured grid was not established (UMTRI-2001-19 was considered).
 * This is not a certification of all R112 requirements or a vehicle-specific
 * lamp. The 15-degree right kick-up is capped at the horizon, conservatively
 * suppressing all upward light. Left cut-off: -0.57 degrees (nominal 1% aim).
 * Zero outside the sampled domain (+/-45 horizontal, -5..+7 vertical);
 * bilinear interpolation below the explicit sharp cut-off gate.
 * E = I * max(N.L,0) / r^2 lux; unit normal, L points receiver -> lamp.
 * Fade: unity through 100 m, linear 100..120 m, zero at/after 120 m.
 * CPU proof: headlight_profile_test.py, 410/410 pass. Compiled controls:
 * broken falloff 17/410 failures (25/50 m ratio 2, expected 4);
 * broken cut-off 67/410 failures (upward light, expected zero).
 */
#include "headlight_profile.h"
#include <math.h>

static const double horizontal[] = {-45, -20, -9, -5.15, -3.43, 0, 1.15, 1.72, 3.43, 5.15, 9, 20, 45};
static const double vertical[] = {-5, -4, -1.72, -.86, -.57, -.3, 0, .57, 7};
static const double candela[][13] = {
    {0, 150, 650, 900, 1100, 1300, 1500, 1600, 1500, 1100, 650, 150, 0},
    {0, 250, 1200, 1800, 2200, 2600, 3000, 3200, 3000, 2200, 1400, 250, 0},
    {0, 500, 2000, 3500, 4000, 6500, 10000, 11000, 9000, 6000, 2200, 500, 0},
    {0, 150, 700, 3000, 4500, 6500, 13000, 16000, 15000, 7500, 2000, 150, 0},
    {0, 0, 200, 800, 1800, 4500, 11000, 14000, 15000, 7000, 1700, 100, 0},
    {0, 0, 0, 0, 0, 0, 7000, 9000, 11000, 4500, 1200, 80, 0},
    {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}
};
const HLProfile hl_low_beam = {
    sizeof horizontal / sizeof *horizontal, sizeof vertical / sizeof *vertical,
    horizontal, vertical, &candela[0][0]
};

double hl_cutoff_deg(double h) {
    if (!isfinite(h)) return -0.57;
    return fmin(0.0, -0.57 + 0.2679491924311227 * fmax(h, 0.0));
}
/* Last interval includes its upper endpoint. Caller has checked the domain. */
static size_t interval(const double *axis, size_t count, double value) {
    size_t i = 0;
    while (i + 2 < count && value > axis[i + 1]) ++i;
    return i;
}
double hl_candela(double h, double v) {
    const HLProfile *p = &hl_low_beam;
    if (!isfinite(h) || !isfinite(v) || h < horizontal[0] ||
        h > horizontal[p->horizontal_count - 1] || v < vertical[0] ||
        v > vertical[p->vertical_count - 1]) return 0.0;
    if (v > hl_cutoff_deg(h)) return 0.0;
    size_t x = interval(horizontal, p->horizontal_count, h);
    size_t y = interval(vertical, p->vertical_count, v);
    double u = (h - horizontal[x]) / (horizontal[x + 1] - horizontal[x]);
    double t = (v - vertical[y]) / (vertical[y + 1] - vertical[y]);
    double a = candela[y][x] * (1 - u) + candela[y][x + 1] * u;
    double b = candela[y + 1][x] * (1 - u) + candela[y + 1][x + 1] * u;
    return a * (1 - t) + b * t;
}
double hl_distance_fade(double r) {
    if (!isfinite(r) || r < 0 || r >= 120) return 0.0;
    return r <= 100 ? 1.0 : (120.0 - r) / 20.0;
}
double hl_illuminance(double intensity, double r, double cosine) {
    if (!isfinite(intensity) || intensity < 0 || !isfinite(r) || r <= 0 ||
        !isfinite(cosine) || cosine > 1 || cosine <= 0) return 0.0;
    return intensity * cosine * hl_distance_fade(r) / (r * r);
}

static double dot(HLVec3 a, HLVec3 b) { return a.x*b.x + a.y*b.y + a.z*b.z; }
static HLVec3 sub(HLVec3 a, HLVec3 b) { return (HLVec3){a.x-b.x, a.y-b.y, a.z-b.z}; }
static int finite_vec(HLVec3 a) { return isfinite(a.x) && isfinite(a.y) && isfinite(a.z); }
static int unit(HLVec3 a) { return finite_vec(a) && fabs(dot(a,a) - 1) <= 1e-5; }
double hl_lamp_lux(const HLLamp *lamp, HLVec3 receiver, HLVec3 normal) {
    if (!lamp || !lamp->enabled || !finite_vec(lamp->position) ||
        !finite_vec(receiver) || !unit(normal) || !unit(lamp->right) ||
        !unit(lamp->up) || !unit(lamp->forward) ||
        fabs(dot(lamp->right, lamp->up)) > 1e-5 ||
        fabs(dot(lamp->right, lamp->forward)) > 1e-5 ||
        fabs(dot(lamp->up, lamp->forward)) > 1e-5) return 0.0;
    HLVec3 delta = sub(receiver, lamp->position);
    double r = hypot(hypot(delta.x, delta.y), delta.z);
    if (!isfinite(r) || r <= 0 || r >= 120) return 0.0;
    HLVec3 ray = {delta.x/r, delta.y/r, delta.z/r};
    double x = dot(ray, lamp->right), y = dot(ray, lamp->up);
    double z = dot(ray, lamp->forward);
    if (z <= 0) return 0.0;
    double degrees = 180.0 / acos(-1.0);
    /* Spherical horizontal azimuth / vertical elevation, matching R112. */
    double h = atan2(x, z) * degrees;
    double v = atan2(y, hypot(x, z)) * degrees;
    return hl_illuminance(hl_candela(h, v), r, fmin(1.0, -dot(normal, ray)));
}
double hl_pair_lux(const HLLamp lamps[2], HLVec3 receiver, HLVec3 normal) {
    if (!lamps) return 0.0;
    return hl_lamp_lux(&lamps[0], receiver, normal) + hl_lamp_lux(&lamps[1], receiver, normal);
}
