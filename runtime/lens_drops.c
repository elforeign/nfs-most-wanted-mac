// Procedural spherical-cap lens drops. No external artwork or random state.
#include "host.h"
#include "lens_drops.h"
#include <math.h>

static const double pi = LENS_PI;
static double transmission(double rho) {
    double si = rho * sin(LENS_CONTACT_DEGREES * pi / 180.0), st = LENS_WATER_INDEX * si;
    if (st >= 1.0) return 0.0; // total internal reflection, water to air
    double ci = sqrt(1.0 - si * si), ct = sqrt(1.0 - st * st);
    double rs = (LENS_WATER_INDEX * ci - ct) / (LENS_WATER_INDEX * ci + ct);
    double rp = (ci - LENS_WATER_INDEX * ct) / (ci + LENS_WATER_INDEX * ct);
    return 1.0 - (rs * rs + rp * rp) * 0.5;
}
static uint16_t half_bits(double d) {
    _Float16 h = (_Float16)d;
    uint16_t bits; memcpy(&bits, &h, sizeof bits); return bits;
}
// Variant k's footprint edge at angle theta (y up), as a fraction of the cap radius.
static double boundary(int k, double theta) {
    uint32_t rng = 0x4C454E53u;
    double phase = 0;
    for (int i = 0; i <= k; ++i) {
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        phase = 2.0 * pi * (double)rng / 4294967296.0;
    }
    return k ? 1 - (1 + cos((k + 1) * theta + phase)) / 32 : 1;
}
static void generate_sharp(LensDropPixels *p) {
    const double a = LENS_CAP_RADIUS, n = LENS_WATER_INDEX;
    memset(p, 0, sizeof *p);
    for (int y = 0; y < 32; ++y) for (int x = 0; x < 32; ++x) {
        double u = (x + .5) / 32.0, v = (y + .5) / 32.0;
        double px = u - .5, py = .5 - v, r = hypot(px, py), rho = r / a;
        double dx = .5, dy = .5, si = rho * sin(LENS_CONTACT_DEGREES * pi / 180.0);
        if (rho <= 1.0 && n * si < 1.0) {
            double offset = r > 0 ? LENS_RAY_SCALE * tan(asin(si) - asin(n * si)) / r : 0;
            dx = u + offset * px; dy = 1.0 - v + offset * py;
        }
        for (int k = 0; k < 4; ++k) {
            uint16_t *d = p->displacement + (y * 128 + k * 32 + x) * 4;
            d[0] = 0; d[1] = half_bits(dx); d[2] = half_bits(dy); d[3] = half_bits(1);
            int covered = 0; double t = 0;
            for (int sy = 0; sy < 4; ++sy) for (int sx = 0; sx < 4; ++sx) {
                double qx = (x + (sx + .5) / 4) / 32 - .5;
                double qy = .5 - (y + (sy + .5) / 4) / 32;
                double qr = hypot(qx, qy);
                if (qr <= a * boundary(k, atan2(qy, qx))) { ++covered; t += transmission(qr / a); }
            }
            uint8_t *alpha = p->alpha + (y * 128 + k * 32 + x) * 4;
            alpha[0] = (uint8_t)lround(255.0 * covered / 16);
            alpha[1] = covered ? (uint8_t)lround(255.0 * t / covered) : 0;
            alpha[2] = alpha[0];
            alpha[3] = 255;
        }
    }
}
// Convolve the footprint and Fresnel loss with a circular entrance pupil; the
// finite front-surface/pupil separation avoids the z=0 singularity. The ray
// field stays sharp, at each variant's own cap size: the authored fragment
// pass traces every pupil ray through it and averages radiance.
static double sharp_coverage(const LensDropPixels *sharp, int k, double qx, double qy, double diameter) {
    double sx = (qx / diameter + .5) * 32 - .5, sy = (qy / diameter + .5) * 32 - .5, alpha = 0;
    int ix = (int)floor(sx), iy = (int)floor(sy);
    for (int j = 0; j < 2; ++j) for (int i = 0; i < 2; ++i) {
        if (ix+i < 0 || ix+i >= 32 || iy+j < 0 || iy+j >= 32) continue;
        double w = (i ? sx-ix : 1-sx+ix) * (j ? sy-iy : 1-sy+iy);
        alpha += w * sharp->alpha[((iy+j)*128+k*32+ix+i)*4] / 255.0;
    }
    return alpha;
}
void lens_drops_generate(LensDropPixels *p) {
    generate_sharp(p);
#if LENS_DEFOCUS
    LensDropPixels sharp = *p;
    const double expand = LENS_DEFOCUS_EXPAND;
    const double pupil = LENS_PUPIL_QUAD;
    // Dry window: air/glass at normal incidence; wet: water/glass behind the cap.
    const double ag = (LENS_GLASS_INDEX-1)/(LENS_GLASS_INDEX+1), wg = (LENS_GLASS_INDEX-LENS_WATER_INDEX)/(LENS_GLASS_INDEX+LENS_WATER_INDEX);
    const double behind = (1-wg*wg)/(1-ag*ag);
    for (int y = 0; y < 32; ++y) for (int x = 0; x < 32; ++x) {
        double u = (x + .5) / 32.0, v = (y + .5) / 32.0;
        double px = (u - .5) * expand, py = (v - .5) * expand;
        double coverage[4] = {0}, kept[4] = {0};
        for (int ay = 0; ay < 32; ++ay) for (int ax = 0; ax < 32; ++ax) {
            double bx = (ax + .5) / 16.0 - 1, by = (ay + .5) / 16.0 - 1;
            if (bx * bx + by * by > 1) continue;
            double qx = px + bx * pupil, qy = py + by * pupil;
            double radius = hypot(qx, qy);
            for (int k = 0; k < 4; ++k) {
                double diameter = pow(LENS_DIAMETER_RATIO, 3 - k);
                double alpha = sharp_coverage(&sharp, k, qx, qy, diameter);
                // Front air/water Fresnel: reflected light is not a black TIR
                // annulus. Use the incident front surface, relative to the dry
                // window (capped at 1: the frame cannot gain light), then
                // pupil-average.
                double si = fmin(radius / (LENS_CAP_RADIUS * diameter), 1) * sin(LENS_CONTACT_DEGREES*pi/180);
                double ci = sqrt(1-si*si), ct = sqrt(1-si*si/(LENS_WATER_INDEX*LENS_WATER_INDEX));
                double rs = (ci-LENS_WATER_INDEX*ct)/(ci+LENS_WATER_INDEX*ct);
                double rp = (LENS_WATER_INDEX*ci-ct)/(LENS_WATER_INDEX*ci+ct);
                coverage[k] += alpha;
                kept[k] += alpha * fmin(1, (1-(rs*rs+rp*rp)*.5) * behind);
            }
        }
        double radius = hypot(px, py);
        for (int k = 0; k < 4; ++k) {
            // Sharp signed ray coordinates, identity outside the cap or under TIR.
            double diameter = pow(LENS_DIAMETER_RATIO, 3 - k), dx = u, dy = 1 - v;
            double si = radius / (LENS_CAP_RADIUS * diameter) * sin(LENS_CONTACT_DEGREES*pi/180);
            if (si <= sin(LENS_CONTACT_DEGREES*pi/180) && LENS_WATER_INDEX*si < 1 && radius > 0) {
                double offset = LENS_FOOTPRINT_RAY_SCALE * tan(asin(si)-asin(LENS_WATER_INDEX*si)) / radius / expand;
                dx += offset*px; dy -= offset*py;
            }
            uint16_t *d = p->displacement + (y*128+k*32+x)*4;
            d[1] = half_bits(dx); d[2] = half_bits(dy);
            uint8_t *a = p->alpha + (y*128+k*32+x)*4;
            // Signed distance to the sharp footprint edge, in texels of this
            // quad (0.5 + d/16): the fragment pass draws the edge at pixel
            // scale, so the drop keeps a crisp rim at any magnification.
            double edge = LENS_CAP_RADIUS * diameter * boundary(k, atan2(-py, px));
            a[0] = (uint8_t)lround(255*fmin(fmax(.5 + (edge - radius) / expand * 32 / 16, 0), 1));
            a[1] = coverage[k] ? (uint8_t)lround(255*kept[k]/coverage[k]) : 255;
            a[2] = (uint8_t)lround(255*fmin(sharp_coverage(&sharp, k, px, py, diameter), 1));
        }
    }
#endif
}
static uint32_t method(Cpu *c, uint32_t object, unsigned slot, int n, uint32_t *args) {
    args[0] = object;
    uint32_t sp = c->esp, result = call_guest(c, M32(M32(object) + slot * 4), n, args);
    c->esp = sp;
    return result;
}
int lens_drops_upload(Cpu *c, uint32_t device, uint32_t textures[2]) {
    if (!device) return 0;
    LensDropPixels pixels; lens_drops_generate(&pixels);
    uint32_t tmp = galloc(12), pair[2] = {0, 0};
    for (int i = 0; i < 2; ++i) {
        unsigned width = 128, row = width * (i ? 4 : 8);
        uint32_t create[9] = {device, width, 32, 1, 0, i ? 32 : 113, 1, tmp, 0};
        M32(tmp) = 0;
        if (method(c, device, 23, 9, create) || !(pair[i] = M32(tmp))) goto fail;
        uint32_t lock[5] = {pair[i], 0, tmp + 4, 0, 0};
        if (method(c, pair[i], 19, 5, lock)) goto fail;
        uint32_t pitch = M32(tmp + 4), address = M32(tmp + 8);
        if (!address || pitch < row) goto fail;
        const uint8_t *src = i ? pixels.alpha : (const uint8_t *)pixels.displacement;
        for (unsigned y = 0; y < 32; ++y) memcpy(G + address + y * pitch, src + y * row, row);
        uint32_t unlock[2] = {pair[i], 0};
        if (method(c, pair[i], 20, 2, unlock)) goto fail;
    }
    memcpy(textures, pair, sizeof pair); return 1;
fail:
    for (int i = 0; i < 2; ++i) if (pair[i]) { uint32_t release[1]; method(c, pair[i], 2, 1, release); }
    return 0;
}
int lens_drops_mode(const char *env) {
    return env && !strcmp(env, "pc_before") ? 0 : env && !strcmp(env, "pc") ? 1 : 2;
}
int lens_drops_provider(Cpu *c, uint32_t device, uint32_t textures[2], int enabled) {
    if (textures[0] && textures[1]) return 1;
    textures[0] = textures[1] = 0;
    return enabled && lens_drops_upload(c, device, textures) ? 2 : 0;
}
