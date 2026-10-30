                                                                             
// override leaves the guest unchanged. Downsample before any receiver; scope
// trilinear state to car draws. PC must leave guest size, allocation and state alone.
// Included by d3d9.m to keep the module and its test independent of build lists.
#import <Metal/Metal.h>
#include "pipeline_cache.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static int envcube_option(const char *value) { return !value || strcmp(value, "pc"); }
static int envcube_blit_option(const char *value) { return !value || !strcmp(value,"1"); }
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

// Software reference and fallback. Runs on the same command buffer as capture
// writes and receiver draws. Each 2D view
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
    id<MTLRenderPipelineState> p = pipeline_build_device(device, d, __func__, &error);
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

// Return completed mip-face coverage, matching the software generator's contract.
static unsigned envcube_generate_blit(id<MTLCommandBuffer> cb, id<MTLTexture> cube, int broken) {
    if (broken || !cb || !cube || cube.mipmapLevelCount < 2) return 0;
    id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
    if (!b) return 0;
    [b generateMipmapsForTexture:cube]; [b endEncoding];
    return (unsigned)(cube.mipmapLevelCount-1)*6;
}

// Diagnostic only: a missing native opcode must not inherit the reference's
// already-correct lower levels and report a false zero difference.
static void envcube_poison_mips(id<MTLCommandBuffer> cb, id<MTLTexture> cube) {
    for (NSUInteger l=1;l<cube.mipmapLevelCount;++l) for (NSUInteger f=0;f<6;++f) {
        MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
        p.colorAttachments[0].texture=cube; p.colorAttachments[0].level=l; p.colorAttachments[0].slice=f;
        p.colorAttachments[0].loadAction=MTLLoadActionClear; p.colorAttachments[0].clearColor=MTLClearColorMake(1,0,1,1);
        p.colorAttachments[0].storeAction=MTLStoreActionStore;
        [[cb renderCommandEncoderWithDescriptor:p] endEncoding];
    }
}

// Aligned readback layout shared by the diagnostic and the exhaustive GPU test.
static NSUInteger envcube_row(NSUInteger edge) { return (edge*4+255)&~(NSUInteger)255; }
static NSUInteger envcube_readback_size(id<MTLTexture> cube) {
    NSUInteger n=0;
    for (NSUInteger l=0;l<cube.mipmapLevelCount;++l) {
        NSUInteger edge=MAX((NSUInteger)1,cube.width>>l); n+=envcube_row(edge)*edge*6;
    }
    return n;
}
static id<MTLBuffer> envcube_readback(id<MTLDevice> device, id<MTLCommandBuffer> cb, id<MTLTexture> cube) {
    id<MTLBuffer> data=[device newBufferWithLength:envcube_readback_size(cube) options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];
    NSUInteger off=0;
    for (NSUInteger l=0;l<cube.mipmapLevelCount;++l) {
        NSUInteger edge=MAX((NSUInteger)1,cube.width>>l), row=envcube_row(edge);
        for (NSUInteger f=0;f<6;++f) {
            [b copyFromTexture:cube sourceSlice:f sourceLevel:l sourceOrigin:MTLOriginMake(0,0,0)
                sourceSize:MTLSizeMake(edge,edge,1) toBuffer:data destinationOffset:off
                destinationBytesPerRow:row destinationBytesPerImage:row*edge options:MTLBlitOptionNone];
            off+=row*edge;
        }
    }
    [b endEncoding]; return data;
}

static unsigned envcube_max_difference(const uint8_t *a, const uint8_t *b, NSUInteger edge, NSUInteger row) {
    unsigned maxdiff=0;
    for (NSUInteger y=0;y<edge;++y) for (NSUInteger x=0;x<edge*4;++x) {
        unsigned diff=(unsigned)abs((int)a[y*row+x]-(int)b[y*row+x]);
        if (diff>maxdiff) maxdiff=diff;
    }
    return maxdiff;
}

#ifndef ENVCUBE_NO_RUNTIME
#include "render_internal.h"
static int envcube_sharp, envcube_broken, envcube_blitmips;
static float envcube_test_phase=-1;
static int envcube_ab_state;
static char envcube_ab_path[512];
typedef struct {
    uint32_t guest, faces, draws[6], samples, pending, generations, mip_passes, stale_samples, mip_blits;
    uint64_t compared_frame;
    NSArray *views;
    id<MTLTexture> held;
} EnvCubeState;
static EnvCubeState envcubes[32];
static id<MTLRenderPipelineState> envcube_pso;
static uint32_t envcube_saved[16][14], envcube_saved_mask;
static EnvCubeState *envcube_find(uint32_t guest) {
    if (!guest) return NULL;
    for (unsigned i = 0; i < 32; ++i) if (envcubes[i].guest == guest) return &envcubes[i];
    return NULL;
}
static void envcube_copy_base(id<MTLCommandBuffer> cb, id<MTLTexture> source, id<MTLTexture> target) {
    id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];
    for (NSUInteger f=0;f<6;++f)
        [b copyFromTexture:source sourceSlice:f sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
            sourceSize:MTLSizeMake(source.width,source.height,1) toTexture:target destinationSlice:f
            destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
    [b endEncoding];
}
// Same-scene screenshot pair: hold simulation using the existing pacing hook,
// and hold all six captured base faces even though capture cadence advances.
int envcube_ab_shot(char *path, size_t n) {
    if (!diag_on || !envcube_sharp || !getenv("BOOT_DIAG_CUBEMIPS_AB")) return 0;
    if (envcube_ab_state==2) {
        snprintf(path,n,"%s",envcube_ab_path); envcube_ab_state=3; return 1;
    }
    if (!envcube_ab_state && path[0] && strstr(path,"/simshot_")) {
        snprintf(envcube_ab_path,sizeof envcube_ab_path,"%.*s_%s.png",(int)(strlen(path)-4),path,
                 envcube_blitmips ? "softwaremips":"blitmips");
        for (unsigned i=0;i<32;++i) {
            EnvCubeState *d=&envcubes[i]; Obj *t=obj(d->guest);
            if (!t || t->levels<2) continue;
            RenderTex *g=gpu_tex(t); if (!g || !g->tex) continue;
            if (!d->held) {
                MTLTextureDescriptor *desc=[MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:g->tex.pixelFormat size:g->tex.width mipmapped:NO];
                desc.storageMode=MTLStorageModePrivate;
                d->held=[mtl newTextureWithDescriptor:desc];
            }
            if (d->held) envcube_copy_base(cmd,g->tex,d->held);
        }
        envcube_ab_state=1;
    }
    return 0;
}
int envcube_ab_hold(void) {
    if (envcube_ab_state==1) { envcube_ab_state=2; return 1; }
    if (envcube_ab_state==3) { envcube_ab_state=0; return 1; }
    return 0;
}
static void envcube_forget(Obj *o) {
    if (o->kind != K_CUBE) return;
    EnvCubeState *d = envcube_find(o->guest);
    if (d) *d = (EnvCubeState){0};
}
static void envcube_configure(void) {
    envcube_sharp = envcube_option(getenv("NFS_CAR_REFLECTION"));
    envcube_broken = diag_on && getenv("NFS_TEST_ENVCUBE_NO_MIPS") != NULL;
    const char *blitmips=getenv("NFS_CUBE_BLITMIPS");
    envcube_blitmips=envcube_blit_option(blitmips);
    const char *phase=diag_on ? getenv("NFS_TEST_CUBEMIPS_PHASE"):NULL;
    envcube_test_phase=phase ? strtof(phase,NULL):-1;
    if (envcube_sharp) M32(0x008F8FF4u) = 1024;
    if (diag_on) diag_event("envcube: option=%s edge_constant=%u blitmips=%d", envcube_sharp ? "sharp" : "pc", M32(0x008F8FF4u), envcube_blitmips);
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
    int native=envcube_blitmips ^ (envcube_ab_state==2);
    if (envcube_ab_state==2 && d->held) envcube_copy_base(cmd,d->held,g->tex);
    int compare=diag_on && d->compared_frame!=dev.frames && diag_frame_wanted(dev.frames,"BOOT_DIAG_CUBEMIPS");
    if (!native || compare) {
        if (!envcube_pso) envcube_pso = envcube_pipeline(mtl, g->tex.pixelFormat);
        if (!d->views) d->views = envcube_views(g->tex);
    }
    unsigned n;
    if (compare) {
        // Hold the captured base level: both generators consume identical texels,
        // in order on this command buffer, without advancing simulation or capture.
        d->compared_frame=dev.frames;
        unsigned old_coverage=envcube_generate(cmd,g->tex,d->views,envcube_pso,0);
        id<MTLBuffer> old=envcube_readback(mtl,cmd,g->tex);
        envcube_poison_mips(cmd,g->tex);
        unsigned native_coverage=envcube_generate_blit(cmd,g->tex,0);
        id<MTLBuffer> native=envcube_readback(mtl,cmd,g->tex);
        NSUInteger width=g->tex.width, levels=g->tex.mipmapLevelCount, format=g->tex.pixelFormat;
        uint64_t frame=dev.frames;
        uint32_t tod=M32(0x009B392Cu), rain=M32(0x009196B8u);
        float phase=-1,wetness=-1;
        if (tod) memcpy(&phase,G+tod+8,4);
        if (rain) memcpy(&wetness,G+rain+0x36A0,4);
        NSString *path=[NSString stringWithFormat:@"%s/cubemips_%llu_%08X.csv",diag_dir,(unsigned long long)frame,d->guest];
        diag_event("envcube: comparison frame=%llu extra_render_encoders=%u extra_blit_encoders=3 (reference, poison, readbacks, native)",
            (unsigned long long)frame,(unsigned)(levels-1)*12);
        [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) {
            FILE *f=fopen(path.UTF8String,"w"); if (!f) return;
            fprintf(f,"# status=%lu format=%lu edge=%lu software=%u native=%u phase=%.6f wetness=%.6f\nface,mip,max_difference\n",
                (unsigned long)done.status,(unsigned long)format,(unsigned long)width,old_coverage,native_coverage,phase,wetness);
            NSUInteger off=0;
            for (NSUInteger l=0;l<levels;++l) {
                NSUInteger edge=MAX((NSUInteger)1,width>>l), row=envcube_row(edge);
                for (NSUInteger face=0;face<6;++face) {
                    fprintf(f,"%lu,%lu,%u\n",(unsigned long)face,(unsigned long)l,
                        envcube_max_difference((const uint8_t *)old.contents+off,(const uint8_t *)native.contents+off,edge,row));
                    off+=row*edge;
                }
            }
            fclose(f);
        }];
    }
    if (native) n=envcube_generate_blit(cmd,g->tex,envcube_broken);
    else n=envcube_generate(cmd,g->tex,d->views,envcube_pso,envcube_broken);
    if (diag_on) { if (native) d->mip_blits += n!=0; else d->mip_passes += n; }
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
    if (envcube_test_phase>=0.05f && envcube_test_phase<=0.95f) {
        uint32_t tod=M32(0x009B392Cu);
        // SetTimeOfDay (007696B0) is exactly this write. The game's next update
        // derives the lighting from the requested phase; no lighting is forged.
        if (tod) memcpy(G+tod+8,&envcube_test_phase,4);
    }
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
        if (d->faces || d->samples) diag_event("envcube: frame=%llu cube=%08X mode=%u faces=%02X draws=%u,%u,%u,%u,%u,%u car_samples=%u mip_generations=%u mip_passes=%u stale_samples=%u mip_blits=%u", (unsigned long long)dev.frames, d->guest, M32(0x009017B8u), d->faces, d->draws[0], d->draws[1], d->draws[2], d->draws[3], d->draws[4], d->draws[5], d->samples, d->generations, d->mip_passes, d->stale_samples,d->mip_blits);
        d->faces = d->samples = d->generations = d->mip_passes = d->stale_samples = d->mip_blits = 0;
        memset(d->draws, 0, sizeof d->draws);
    }
}
#endif
