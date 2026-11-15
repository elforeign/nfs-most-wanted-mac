#include "paint_flake.h"
#include <math.h>
#include <stddef.h>

typedef struct { uint64_t state, inc; } Pcg;
static uint32_t pcg(Pcg *p) {
    uint64_t old = p->state;
    p->state = old * UINT64_C(6364136223846793005) + p->inc;
    uint32_t x = (uint32_t)(((old >> 18) ^ old) >> 27), r = (uint32_t)(old >> 59);
    return (x >> r) | (x << ((-r) & 31));
}
static Pcg seeded(uint64_t stream) {
    Pcg p = {0, (stream << 1) | 1};
    pcg(&p); p.state += 0x464c414b; pcg(&p); return p;
}
static double uniform(Pcg *p) { return (pcg(p) + 0.5) / 4294967296.0; }
static double gaussian(Pcg *p) {
    double radius = sqrt(-2.0 * log(uniform(p))), angle = 6.2831853071795864769 * uniform(p);
    return radius * cos(angle);
}
const uint8_t *paint_flake_volume(uint32_t *bytes) {
    static uint8_t volume[PAINT_FLAKE_BYTES];
    static int ready;
    if (!ready) {
        Pcg p = seeded(1);
        // z,y,x traversal; alpha is projected coverage, not light intensity.
        for (size_t i = 0; i < 32 * 32 * 32; ++i) {
            int occupied = uniform(&p) < 0.95;
            for (int c = 0; c < 3; ++c) {
                double g = fmax(-1.0, fmin(1.0, gaussian(&p))) / 2.9;   // clipped at one sigma: std 0.25, none beyond 0.345, so no rare strong specks
                volume[4*i+c] = (uint8_t)floor(127.5 * (1.0 + g) + 0.5);
            }
            volume[4*i+3] = occupied ? 255 : 0;
        }
        size_t src = 0, dst = 32 * 32 * 32 * 4;
        for (unsigned n = 32; n > 1; n >>= 1) {
            unsigned m = n / 2;
            for (unsigned z = 0; z < m; ++z) for (unsigned y = 0; y < m; ++y) for (unsigned x = 0; x < m; ++x)
                for (unsigned c = 0; c < 4; ++c) {
                    unsigned sum = 0;
                    for (unsigned dz = 0; dz < 2; ++dz) for (unsigned dy = 0; dy < 2; ++dy) for (unsigned dx = 0; dx < 2; ++dx)
                        sum += volume[src + 4 * (((2*z+dz)*n + 2*y+dy)*n + 2*x+dx) + c];
                    volume[dst + 4*((z*m+y)*m+x)+c] = (uint8_t)((sum + 4) / 8);
                }
            src = dst; dst += m*m*m*4;
        }
        ready = 1;
    }
    *bytes = sizeof volume; return volume;
}
const uint8_t *paint_flake_noise(uint32_t *width, uint32_t *height) {
    static uint8_t noise[512 * 512 * 4];
    static int ready;
    if (!ready) {
        Pcg p = seeded(2);
        for (size_t i = 0; i < 512 * 512; ++i) {
            for (int c = 0; c < 3; ++c) noise[4*i+c] = (uint8_t)(pcg(&p) >> 24);
            noise[4*i+3] = 255;
        }
        ready = 1;
    }
    *width = *height = 512; return noise;
}
float paint_flake_fade_b(void) { return 0.25f; }   // per metre, the console's B (its A is 1.2 in the shader)
