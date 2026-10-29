// Pre-store exports and floating texture reads; float-scene policy/export guard.
#pragma once
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
enum { HC_WORLD, HC_OTHER, HC_MSAA, HC_RESOLVE, HC_COPY, HC_DOWN, HC_MASK,
    HC_LUM, HC_BRIGHT, HC_BLUR, HC_BLOOM_DOWN, HC_BLOOM_H, HC_BLOOM_V,
    HC_BLOOM_H2, HC_BLOOM_V2, HC_DOF, HC_CURVE, HC_FINAL,
    HC_FINAL_RESOLVE, HC_LAMP_SOURCE, HC_LAMP_BLUR, HC_LAMP_DOWN, HC_LAMP_H, HC_LAMP_V, HC_MIRROR, HC_STAGES };
// Isolated post tests link render_post without render.m. Weak Off definitions
// keep that contract; the renderer owner's strong definitions supply diagnostics.
extern int hdrclip_on;
extern int float_scene_on;
#define HDRCLIP_ACTIVE (hdrclip_on)
void hdrclip_init(void);
NSString *hdrclip_source(NSString *source);
NSString *hdrclip_world_source(NSString *source, uint64_t key, int material, unsigned variant);
static inline NSString *hdrclip_shader(NSString *source) { return hdrclip_source(source); }
void hdrclip_bind(id<MTLRenderCommandEncoder> e, unsigned stage);
void hdrclip_scan(id<MTLTexture> t, unsigned stage);
void hdrclip_finish(void);
int float_scene_test(int mutant);
#ifndef HDRCLIP_IMPLEMENTATION
__attribute__((weak)) int hdrclip_on;
__attribute__((weak)) int float_scene_on;
__attribute__((weak,noinline)) NSString *hdrclip_source(NSString *source) { return source; }
__attribute__((weak,noinline)) void hdrclip_bind(id<MTLRenderCommandEncoder> e,unsigned stage) { (void)e;(void)stage; }
__attribute__((weak,noinline)) void hdrclip_scan(id<MTLTexture> t,unsigned stage) { (void)t;(void)stage; }
__attribute__((weak,noinline)) void hdrclip_finish(void) {}
#endif
