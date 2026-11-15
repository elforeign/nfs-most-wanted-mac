// car_normals_port.m - upload of the port-authored car normal maps (car_normals_port.h). The console maps and their path
// are untouched: materials.m picks a family here only when its console map is not loaded.
#include "car_normals_port.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#if defined(__has_include)
#if __has_include("gen/port_car_normals.inc")
#include "gen/port_car_normals.inc"
#endif
#endif
#ifndef CAR_NORMALS_PORT
static const CarNormalsPortMap car_normals_port_maps[4];
#endif

int car_normals_port_has(int family) {
    return family >= 0 && family < 4 && car_normals_port_maps[family].xy && car_normals_port_maps[family].w;
}
static uint16_t half(float f) { __fp16 h = (__fp16)f; uint16_t u; memcpy(&u, &h, 2); return u; }
static int flat_on = -1;
void car_normals_port_set_flat(int on) { flat_on = on; }   // test only: the flat-normal control, live (BOOT_KEYS carnm:portflat)
id<MTLTexture> car_normals_port_texture(id<MTLDevice> device, int family) {
    static id<MTLTexture> tex[4], flat;
    if (flat_on < 0) { const char *e = getenv("NFS_TEST_CARNM_FLAT"); flat_on = e && atoi(e) == 1; }
    if (!car_normals_port_has(family)) return nil;
    if (flat_on) {
        if (!flat) {
            MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:1 height:1 mipmapped:NO];
            d.usage = MTLTextureUsageShaderRead;
            flat = [device newTextureWithDescriptor:d];
            const uint16_t px[4] = {half(.5f), half(.5f), half(1.f), half(1.f)};
            [flat replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:px bytesPerRow:8];
        }
        return flat;
    }
    if (tex[family]) return tex[family];
    const CarNormalsPortMap *m = &car_normals_port_maps[family];
    unsigned w = m->w, h = m->h, levels = 1;
    while ((w >> levels) || (h >> levels)) ++levels;
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:w height:h mipmapped:YES];
    d.mipmapLevelCount = levels; d.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> t = [device newTextureWithDescriptor:d];
    float *n = malloc(sizeof(float) * 3 * w * h), *next = malloc(sizeof(float) * 3 * w * h);
    uint16_t *px = malloc(8 * w * h);
    if (!t || !n || !next || !px) { free(n); free(next); free(px); return nil; }
    for (unsigned i = 0; i < w * h; ++i) {
        float x = m->xy[2 * i] / 32767.f, y = m->xy[2 * i + 1] / 32767.f;
        n[3 * i] = x; n[3 * i + 1] = y; n[3 * i + 2] = sqrtf(fmaxf(0.f, 1.f - x * x - y * y));
    }
    for (unsigned level = 0, lw = w, lh = h;; ++level) {
        for (unsigned i = 0; i < lw * lh; ++i) {
            for (int c = 0; c < 3; ++c) px[4 * i + c] = half(.5f * n[3 * i + c] + .5f);
            px[4 * i + 3] = half(1.f);
        }
        [t replaceRegion:MTLRegionMake2D(0, 0, lw, lh) mipmapLevel:level withBytes:px bytesPerRow:8 * lw];
        if (level + 1 == levels) break;
        unsigned nw = lw > 1 ? lw / 2 : 1, nh = lh > 1 ? lh / 2 : 1;
        for (unsigned y = 0; y < nh; ++y) for (unsigned x = 0; x < nw; ++x) {
            float s[3] = {0};
            for (unsigned dy = 0; dy < 2; ++dy) for (unsigned dx = 0; dx < 2; ++dx) {
                unsigned sx = lw > 1 ? 2 * x + dx : 0, sy = lh > 1 ? 2 * y + dy : 0;
                for (int c = 0; c < 3; ++c) s[c] += n[3 * (sy * lw + sx) + c];
            }
            float l = sqrtf(s[0] * s[0] + s[1] * s[1] + s[2] * s[2]);
            for (int c = 0; c < 3; ++c) next[3 * (y * nw + x) + c] = l > 0 ? s[c] / l : (c == 2);
        }
        float *swap = n; n = next; next = swap;
        lw = nw; lh = nh;
    }
    free(n); free(next); free(px);
    return tex[family] = t;
}
