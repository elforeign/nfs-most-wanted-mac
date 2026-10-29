                                                                             
// override leaves the guest unchanged. Downsample before any receiver; scope
// trilinear state to car draws. PC must leave guest size, allocation and state alone.
// Included by d3d9.m to keep the module and its test independent of build lists.
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int envcube_option(const char *value) { return !value || strcmp(value, "pc"); }
static uint32_t envcube_level_count(uint32_t edge) {
    uint32_t n = 1; while (edge > 1) { edge >>= 1; ++n; } return n;
}
static uint64_t envcube_bytes(uint32_t edge, uint32_t levels) {
    uint64_t n = 0;
    for (uint32_t l = 0; l < levels; ++l) { n += (uint64_t)edge * edge * 6 * 4; if (edge > 1) edge >>= 1; }
    return n;
}
static void envcube_filter(uint32_t ss[14], int sharp) {
    if (!sharp) return;
    ss[5] = ss[6] = ss[7] = 2; ss[8] = ss[9] = 0;
}

// Render passes instead of generateMipmapsForTexture: the render-thread recorder
// has no mip-blit opcode. These use its existing render operations, on the same
// command buffer as all six face writes and the receiver draws. Each 2D view
// exposes exactly one source mip/face; a centred linear sample averages 2x2 texels.
static NSString *const envcube_shader = @
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct V { float4 p [[position]]; float2 uv; };\n"
"vertex V env_vs(uint i [[vertex_id]]) { float2 u=float2((i<<1)&2,i&2); return {float4(u*float2(2,-2)+float2(-1,1),0,1),u}; }\n"
"fragment float4 env_fs(V v [[stage_in]], texture2d<float> t [[texture(0)]]) { constexpr sampler s(coord::normalized,address::clamp_to_edge,filter::linear); return t.sample(s,v.uv); }\n";
static id<MTLRenderPipelineState> envcube_pipeline(id<MTLDevice> device, MTLPixelFormat format) {
    NSError *error = nil;
    id<MTLLibrary> lib = [device newLibraryWithSource:envcube_shader options:nil error:&error];
    if (!lib) { fprintf(stderr, "envcube: mip shader: %s\n", error.localizedDescription.UTF8String); return nil; }
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [lib newFunctionWithName:@"env_vs"]; d.fragmentFunction = [lib newFunctionWithName:@"env_fs"];
    d.colorAttachments[0].pixelFormat = format;
    id<MTLRenderPipelineState> p = [device newRenderPipelineStateWithDescriptor:d error:&error];
    if (!p) fprintf(stderr, "envcube: mip pipeline: %s\n", error.localizedDescription.UTF8String);
    return p;
}
static NSArray *envcube_views(id<MTLTexture> cube) {
    NSMutableArray *views = [NSMutableArray new];
    for (NSUInteger l = 1; l < cube.mipmapLevelCount; ++l) for (NSUInteger f = 0; f < 6; ++f) {
        id<MTLTexture> v = [cube newTextureViewWithPixelFormat:cube.pixelFormat textureType:MTLTextureType2D levels:NSMakeRange(l-1,1) slices:NSMakeRange(f,1)];
        if (!v) return nil; [views addObject:v];
    }
    return views;
}
#ifndef ENVCUBE_PASS
#define ENVCUBE_PASS() [MTLRenderPassDescriptor renderPassDescriptor]
#endif
static unsigned envcube_generate(id<MTLCommandBuffer> cb, id<MTLTexture> cube, NSArray *views, id<MTLRenderPipelineState> pipeline, int broken) {
    if (broken || !cb || !pipeline || views.count != (cube.mipmapLevelCount-1)*6) return 0;
    unsigned generated = 0;
    for (NSUInteger l = 1; l < cube.mipmapLevelCount; ++l) for (NSUInteger f = 0; f < 6; ++f) {
        MTLRenderPassDescriptor *p = ENVCUBE_PASS();
        p.colorAttachments[0].texture = cube; p.colorAttachments[0].level = l; p.colorAttachments[0].slice = f;
        p.colorAttachments[0].loadAction = MTLLoadActionDontCare; p.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:p];
        if (!e) return generated;
        [e setRenderPipelineState:pipeline];
        [e setFragmentTexture:views[(l-1)*6+f] atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding]; ++generated;
    }
    return generated;
}

#ifndef ENVCUBE_NO_RUNTIME
#include "render_internal.h"
static int envcube_sharp, envcube_broken;
typedef struct {
    uint32_t guest, faces, draws[6], samples, pending, generations, mip_passes, stale_samples;
    NSArray *views;
} EnvCubeState;
static EnvCubeState envcubes[32];
static id<MTLRenderPipelineState> envcube_pso;
static uint32_t envcube_saved[16][14], envcube_saved_mask;
static EnvCubeState *envcube_find(uint32_t guest) {
    if (!guest) return NULL;
    for (unsigned i = 0; i < 32; ++i) if (envcubes[i].guest == guest) return &envcubes[i];
    return NULL;
}
static void envcube_forget(Obj *o) {
    if (o->kind != K_CUBE) return;
    EnvCubeState *d = envcube_find(o->guest);
    if (d) *d = (EnvCubeState){0};
}
static void envcube_configure(void) {
    envcube_sharp = envcube_option(getenv("NFS_CAR_REFLECTION"));
    envcube_broken = diag_on && getenv("NFS_TEST_ENVCUBE_NO_MIPS") != NULL;
    if (envcube_sharp) M32(0x008F8FF4u) = 1024;
    if (diag_on) diag_event("envcube: option=%s edge_constant=%u", envcube_sharp ? "sharp" : "pc", M32(0x008F8FF4u));
}
static uint32_t envcube_creation_levels(uint32_t edge, uint32_t levels, uint32_t usage, uint32_t fmt) {
    return envcube_sharp && edge == 1024 && usage == 1 && fmt == 21 ? envcube_level_count(edge) : levels;
}
static void envcube_created(Obj *t, uint32_t caller, uint32_t output) {
    if (diag_on) diag_event("envcube: create cube=%08X caller=%08X output=%08X edge=%u levels=%u usage=%X fmt=%u", t->guest, caller, output, t->w, t->levels, t->usage, t->fmt);
    if ((!envcube_sharp && !diag_on) || t->usage != 1 || t->fmt != 21 || t->w != M32(0x008F8FF4u)) return;
    for (unsigned i = 0; i < 32; ++i) if (!envcubes[i].guest) { envcubes[i].guest = t->guest; return; }
    fprintf(stderr, "envcube: more than 32 live capture cubes\n"); abort();
}
static void envcube_flush(EnvCubeState *d) {
    if (!envcube_sharp || !d || !d->pending) return;
    Obj *t = obj(d->guest); if (!t || t->levels < 2) return;
    end_pass(); ensure_cmd();
    RenderTex *g = gpu_tex(t); if (!g || !g->tex) return;
    if (!envcube_pso) envcube_pso = envcube_pipeline(mtl, g->tex.pixelFormat);
    if (!d->views) d->views = envcube_views(g->tex);
    unsigned n = envcube_generate(cmd, g->tex, d->views, envcube_pso, envcube_broken);
    if (diag_on) d->mip_passes += n;
    if (n == 6*(t->levels-1)) { if (diag_on) ++d->generations; d->pending = 0; }
}
static void envcube_target_change(uint32_t old, uint32_t next) {
    if (!envcube_sharp || old == next) return;
    Obj *a = obj(old), *b = obj(next);
    if (a && a->parent && (!b || b->parent != a->parent)) envcube_flush(envcube_find(a->parent));
}
static void envcube_draw(void) {
    if (!envcube_sharp && !diag_on) return;
    Obj *rt = obj(dev.rt[0]);
    if (rt && rt->parent && rt->face < 6) {
        EnvCubeState *d = envcube_find(rt->parent);
        if (d) {
            d->pending = 1;
            if (diag_on) {
                if (!d->draws[rt->face] && dev.frames % 120 == 0)
                    diag_event("envcube: capture frame=%llu cube=%08X face=%u target=%ux%u viewport=%ux%u", (unsigned long long)dev.frames, d->guest, rt->face, rt->w, rt->h, dev.viewport[2], dev.viewport[3]);
                d->faces |= 1u << rt->face; ++d->draws[rt->face];
            }
        }
    }
    uint32_t fx = M32(0x00982C80u);
    if (!fx || M32(fx + 4) != 4) return; // car effect, same ID as existing cardraw diagnostics
    for (int s = 0; s < 16; ++s) {
        EnvCubeState *d = envcube_find(dev.tex[s]); if (!d) continue;
        Obj *t = obj(d->guest); if (!t) continue;
        // No sampler-state edits persist outside this one draw. This also covers
        // splitcapture's independent P1/P2 cubes; creation and sizing read the shared edge.
        if (envcube_sharp) {
            memcpy(envcube_saved[s], dev.ss[s], sizeof dev.ss[s]); envcube_saved_mask |= 1u << s;
            envcube_filter(dev.ss[s], 1);
        }
        if (diag_on) {
            if (envcube_sharp && d->pending && (!rt || rt->parent != d->guest)) ++d->stale_samples;
            if (!d->samples && dev.frames % 120 == 0) diag_event("envcube: car sample frame=%llu cube=%08X edge=%u levels=%u stage=%d min=%u mag=%u mip=%u bias=%08X viewport=%ux%u", (unsigned long long)dev.frames, d->guest, t->w, t->levels, s, dev.ss[s][6], dev.ss[s][5], dev.ss[s][7], dev.ss[s][8], dev.viewport[2], dev.viewport[3]);
            ++d->samples;
        }
    }
}
static void envcube_draw_end(void) {
    for (unsigned s = 0; envcube_saved_mask; ++s) if (envcube_saved_mask & (1u << s)) {
        memcpy(dev.ss[s], envcube_saved[s], sizeof dev.ss[s]); envcube_saved_mask &= ~(1u << s);
    }
}
static void envcube_frame(void) {
    if (!diag_on) return;
    static int memory_reported;
    if (!memory_reported) {
        Obj *cube = obj(M32(0x00982A30u)), *backup = obj(M32(0x00982A64u));
        uint64_t depth = 0; unsigned ndepth = 0;
        for (unsigned f = 0; f < 6; ++f) {
            Obj *s = obj(M32(0x00982A4Cu + f*4));
            if (s && s->gpu) { depth += ((RenderTex *)s->gpu)->bytes; ++ndepth; }
        }
        if (cube && cube->gpu && ndepth == 6) {
            uint64_t guest = envcube_bytes(cube->w, cube->levels) + (backup ? envcube_bytes(backup->w,backup->levels) : 0);
            uint64_t gpu = ((RenderTex *)cube->gpu)->bytes + (backup && backup->gpu ? ((RenderTex *)backup->gpu)->bytes : 0);
            diag_event("envcube: memory edge=%u guest_colour_bytes=%llu gpu_colour_bytes=%llu gpu_depth_bytes=%llu total_bytes=%llu", cube->w, (unsigned long long)guest, (unsigned long long)gpu, (unsigned long long)depth, (unsigned long long)(guest+gpu+depth));
            memory_reported = 1;
        }
    }
    for (unsigned i = 0; i < 32; ++i) {
        EnvCubeState *d = &envcubes[i];
        if (d->faces || d->samples) diag_event("envcube: frame=%llu cube=%08X mode=%u faces=%02X draws=%u,%u,%u,%u,%u,%u car_samples=%u mip_generations=%u mip_passes=%u stale_samples=%u", (unsigned long long)dev.frames, d->guest, M32(0x009017B8u), d->faces, d->draws[0], d->draws[1], d->draws[2], d->draws[3], d->draws[4], d->draws[5], d->samples, d->generations, d->mip_passes, d->stale_samples);
        d->faces = d->samples = d->generations = d->mip_passes = d->stale_samples = 0;
        memset(d->draws, 0, sizeof d->draws);
    }
}
#endif
