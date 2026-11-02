#!/usr/bin/env python3
"""Shader frame-path regression, without Metal or a game launch.

Production lookup/scheduling: normal frame-thread compile_entry calls 0; broken 1.
Cache round-trip: normal 10 records; broken AUX whitelist 1 record;
broken Clear-lamp whitelist 6 records; broken film whitelist 7 records;
broken material whitelist Glass/Chrome 8/9 records.
Near-cache review P3, original/fixed: warmed records 1 -> 6 of 6; duplicate bytes on
successive launches 2710/0 and 5420/0; failures 4/0. Unknown bits/bases rejected.
Unknown-flag control: overly broad mask/production failures 1/0.
Readiness after publication: normal pending 0; broken 1.
Concurrent identical-source requests: normal 1 compile; broken cache 4.
Cold profile (2026-10-28): source compiles before/after 13/0, hangs 0/0,
stalls 0/0, deferred draws 635515/0 (game_run runs 1 and 5).
MSL identity: normal 1744 comparisons / 0 differences; broken shadow mask
rejected at its first difference. These are source comparisons, not frame proof.
Contact shadows (free-roam entry stalled 500+ ms on a frame-path compile): synchronous
newLibraryWithSource calls in x_contact_scene_impl, normal 0; the previous source 2.
Optional --diag DIR checks real frame_sync counters and hang/stall reports.
The translator check compares exact MSL before/after option canonicalization.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "runtime/render.m").read_text()


def between(start, end):
    return SRC.split(start, 1)[1].split(end, 1)[0]


def run(args, expected=0):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       cwd=ROOT, timeout=90)
    assert p.returncode == expected, f"exit {p.returncode}, expected {expected}: {p.stdout[-3000:]}"
    return p.stdout


def diagnostic(path):
    text = (path / "events.log").read_text()
    counts = [int(n) for n in re.findall(r"shader_compile:.*?frame_sync=(\d+)", text)]
    assert counts, "missing shader_compile frame_sync counter"
    hangs = len(list(path.glob("hang_*.txt")))
    stalls = len(list(path.glob("stall_*.txt")))
    print(f"game: frame_sync={max(counts)} hangs={hangs} stalls={stalls}")
    return int(max(counts) != 0 or hangs != 0 or stalls != 0)


PRELUDE = r'''
#import <Foundation/Foundation.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "host/shaders/sm_translate.h"
enum { MAT_VARIANT_WATER=0x100, MAT_VARIANT_GLASS, MAT_VARIANT_TREES,
       MAT_VARIANT_BONE, MAT_VARIANT_SOFT_SMOKE, MAT_VARIANT_SOFT_SMOKE_RESOLVED,
       MAT_VARIANT_SOFT_SMOKE_LATE, MAT_VARIANT_CARBON_LAMP, MAT_VARIANT_HEADLAMP_CLEAR, MAT_VARIANT_ROAD_FILM, MAT_VARIANT_REAL_GLASS, MAT_VARIANT_REAL_CHROME, MAT_VARIANT_AUX=0x200 };
enum { SHADOW_NEAR_VARIANT=0x40000000u };
typedef struct {
 uint64_t key; SmResult refl; volatile int pending; int ok; dispatch_semaphore_t ready;
 uint32_t *tokens, ntokens; SmOptions opt; uint32_t variant;
} ShaderEntry;
typedef struct {
 uint64_t tokens_hash, last_key, memo_key[2]; int last_idx, memo_next;
 uint8_t memo_opt[2][48]; uint32_t memo_variant[2], vc_serial;
 uint32_t *tokens, ntokens;
} Obj;
static ShaderEntry **shaders;
static int nshaders, capshaders;
static dispatch_queue_t shader_q, shader_workers[4];
static unsigned shader_worker_next;
static dispatch_semaphore_t allow_compile, started;
static int frame_sync, compiled;
static const char *scratch;
static const char *userdata_dir(void) { return scratch; }
static int diag_on;
static uint64_t frame_serial=1;
static unsigned long long stat_sync_shader;
static void diag_event(const char *fmt, ...) { (void)fmt; }
@protocol MTLLibrary @end
@class MTLCompileOptions;
static int library_requests;
@interface CompileHitchDevice : NSObject
- (id<MTLLibrary>)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error;
@end
@implementation CompileHitchDevice
- (id<MTLLibrary>)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error {
 (void)options;
 __atomic_add_fetch(&library_requests,1,__ATOMIC_RELAXED);
 usleep(10000);
 if ([source isEqualToString:@"invalid"]) { *error=[NSError errorWithDomain:@"fixture" code:7 userInfo:nil];return nil; }
 return (id<MTLLibrary>)[NSObject new];
}
@end
static CompileHitchDevice *mtl;
static uint64_t host_ticks(void) {
 struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
 return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static void shader_catalogue_warm(void) {}
static void compile_entry(ShaderEntry *e) {
 if (pthread_main_np()) { ++frame_sync; }
 else {
   dispatch_semaphore_signal(started);
   dispatch_semaphore_wait(allow_compile, DISPATCH_TIME_FOREVER);
 }
 e->ok=1;
 __atomic_add_fetch(&compiled,1,__ATOMIC_RELAXED);
 __atomic_store_n(&e->pending,0,__ATOMIC_RELEASE);
 dispatch_semaphore_signal(e->ready);
}
static int check(int good, const char *why) {
 if (!good) fprintf(stderr,"FAIL: %s frame_sync=%d compiled=%d\n",why,frame_sync,compiled);
 return !good;
}
'''

MAIN = r'''
int main(int argc, char **argv) { @autoreleasepool {
 scratch=argv[1];
 allow_compile=dispatch_semaphore_create(0); started=dispatch_semaphore_create(0);
 shader_cache_warm();
 // Original synthetic fixture shared with the translator tests (no game bytecode).
 FILE *fixture=fopen(argv[2],"rb"); if(!fixture)return 2;
 fseek(fixture,0,SEEK_END);long token_bytes=ftell(fixture);rewind(fixture);
 if(token_bytes<=0 || token_bytes%4)return 2;
 uint32_t *tokens=malloc(token_bytes);
 if(fread(tokens,1,token_bytes,fixture)!=(size_t)token_bytes)return 2;fclose(fixture);
 Obj s={0}; s.tokens=tokens; s.ntokens=token_bytes/4;
 SmOptions o; sm_default_options(&o);
 ShaderEntry *e=get_shader_variant(&s,&o,0);
 if (check(frame_sync==0,"no compile on frame thread")) return 1;
 if (check(__atomic_load_n(&e->pending,__ATOMIC_ACQUIRE),"cold lookup returns pending")) return 1;
 if (check(!dispatch_semaphore_wait(started,dispatch_time(DISPATCH_TIME_NOW,1000000000)),"background started")) return 1;
 if (check(get_shader_variant(&s,&o,0)==e,"pending memo lookup returns")) return 1;
 if (check(shader_pending(e),"bounded base wait returns while compiler is blocked")) return 1;
 Obj other={0}; other.tokens=tokens; other.ntokens=s.ntokens;
 if (check(get_shader_variant(&other,&o,0)==e,"pending shared lookup returns")) return 1;
 SmOptions irrelevant=o; irrelevant.sampler_type[7]=SM_TEX_CUBE;
 if (check(get_shader_variant(&s,&irrelevant,0)==e && get_shader_variant(&s,&irrelevant,0)==e,
           "unread texture options share the pending shader")) return 1;
 uint32_t variants[]={MAT_VARIANT_AUX,MAT_VARIANT_SOFT_SMOKE,
    MAT_VARIANT_SOFT_SMOKE_RESOLVED,MAT_VARIANT_SOFT_SMOKE_LATE,MAT_VARIANT_CARBON_LAMP,MAT_VARIANT_HEADLAMP_CLEAR,MAT_VARIANT_ROAD_FILM,MAT_VARIANT_REAL_GLASS,MAT_VARIANT_REAL_CHROME};
 for (int i=0;i<9;i++) get_shader_variant(&s,&o,variants[i]);
 for (int i=0;i<10;i++) dispatch_semaphore_signal(allow_compile);
 for (int i=0;i<4;i++) dispatch_sync(shader_workers[i], ^{});
 mtl=[CompileHitchDevice new];diag_on=1;
 id results[4];
 dispatch_group_t group=dispatch_group_create();
 for (int i=0;i<4;i++) {
   id __strong *slot=&results[i];
   dispatch_group_async(group,shader_workers[i],^{ @autoreleasepool {
     NSError *error=nil; *slot=shader_library(@"same exact source",nil,&error);
   }});
 }
 dispatch_group_wait(group,DISPATCH_TIME_FOREVER);
 printf("identical-source requests=%d frame_sync=%llu\n",library_requests,stat_sync_shader);
 if(check(library_requests==1 && !stat_sync_shader,"concurrent exact-source compile sharing"))return 1;
 for(int i=1;i<4;i++)if(check(results[i]==results[0],"shared library identity"))return 1;
 dispatch_sync(shader_workers[0],^{ @autoreleasepool {
   NSError *a=nil,*b=nil;
   id one=shader_library(@"invalid",nil,&a),two=shader_library(@"invalid",nil,&b);
   if(one||two||a.code!=7||b.code!=7)abort();
 }});
 if(check(library_requests==2,"failed compile publishes error and wakes followers"))return 1;
 if (check(!e->pending && e->ok && compiled==10,"release/acquire publication")) return 1;
 if (check(!shader_pending(e),"ready shader no longer pending")) return 1;
 if (check(get_shader_variant(&s,&o,0)==e,"ready lookup reused")) return 1;
 // Keep shape metadata, but rebuild the shader/cache index exactly as a new launch does.
 nshaders=capshaders=0; shaders=NULL; memset(vcache,0,sizeof vcache);
 shader_cache_warm();
 printf("cache records=%d frame_sync=%d\n",nshaders,frame_sync);
 if (check(nshaders==10,"cache accepts AUX, soft smoke, lamp, film and material profiles")) return 1;
 for (int i=0;i<10;i++) dispatch_semaphore_signal(allow_compile);
 for (int i=0;i<4;i++) dispatch_sync(shader_workers[i], ^{});
 int comparisons=0;
 for (int a=2;a<argc;a++) {
   FILE *f=fopen(argv[a],"rb"); if(!f)return 2;
   fseek(f,0,SEEK_END); long bytes=ftell(f); rewind(f);
   uint32_t *t=malloc(bytes); if(fread(t,1,bytes,f)!=(size_t)bytes)return 2; fclose(f);
   uint64_t hash=fnv(t,bytes,1469598103934665603ull);
   ShaderShape *shape=shader_shape(t,bytes/4,hash);
   for (unsigned k=0;k<16;k++) {
     SmOptions raw; sm_default_options(&raw);
     for (int st=0;st<16;st++) raw.sampler_type[st] = k&1 ? (shape && shape->types[st] ? shape->types[st] : SM_TEX_CUBE) : 0;
     raw.shadow_samplers=(uint16_t)(k*0x1111);
     raw.vs_input_int=k&2 ? 0xffff:0; raw.vs_input_uint=k&4 ? 0xffff:0;
     for(int st=0;st<8;st++)raw.tex_projected[st]=k&8 ? 4:0;
     SmOptions normalized=shader_options(shape,&raw); SmResult before={0},after={0};
     int b=sm_translate(t,bytes/4,&raw,&before), c=sm_translate(t,bytes/4,&normalized,&after);
     if (check(b==c && (b || !strcmp(before.msl,after.msl)),argv[a]))return 1;
     sm_result_free(&before);sm_result_free(&after);++comparisons;
   }
   free(t);
 }
 printf("PASS: frame_sync=%d; exact MSL comparisons=%d\n",frame_sync,comparisons);
 // A separate profile models repeated launches with near records interleaved
 // before a normal record. Warm lookups must neither lose later entries nor
 // append another copy of the same near shader on every launch.
 char cache[1024];cache_path(cache,sizeof cache);if(unlink(cache))return 2;
 uint32_t near_variants[]={0,SHADOW_NEAR_VARIANT,SHADOW_NEAR_VARIANT|10,
    SHADOW_NEAR_VARIANT|MAT_VARIANT_ROAD_FILM,SHADOW_NEAR_VARIANT|MAT_VARIANT_REAL_GLASS,MAT_VARIANT_AUX};
 nshaders=capshaders=0;shaders=NULL;memset(vcache,0,sizeof vcache);
 Obj near_s={0};near_s.tokens=tokens;near_s.ntokens=token_bytes/4;
 for(unsigned i=0;i<6;++i)get_shader_variant(&near_s,&o,near_variants[i]);
 for(unsigned i=0;i<6;++i)dispatch_semaphore_signal(allow_compile);
 for(int i=0;i<4;++i)dispatch_sync(shader_workers[i], ^{});
 FILE *near_file=fopen(cache,"rb");if(!near_file)return 2;
 fseek(near_file,0,SEEK_END);long original_bytes=ftell(near_file);fclose(near_file);
 int near_failures=0;
 for(int launch=0;launch<2;++launch) {
   nshaders=capshaders=0;shaders=NULL;memset(vcache,0,sizeof vcache);
   shader_cache_warm();int warmed=nshaders;
   near_failures+=check(warmed==6,"warm accepts near variants and later base record");
   near_s=(Obj){0};near_s.tokens=tokens;near_s.ntokens=token_bytes/4;
   for(unsigned i=0;i<6;++i)get_shader_variant(&near_s,&o,near_variants[i]);
   near_file=fopen(cache,"rb");if(!near_file)return 2;
   fseek(near_file,0,SEEK_END);long bytes=ftell(near_file);fclose(near_file);
   near_failures+=check(bytes==original_bytes,"warm near lookup does not append duplicates");
   printf("near cache launch %d: warm=%d/6 duplicate_bytes=%ld\n",launch+1,warmed,bytes-original_bytes);
   for(int i=0;i<nshaders;++i)dispatch_semaphore_signal(allow_compile);
   for(int i=0;i<4;++i)dispatch_sync(shader_workers[i], ^{});
 }
 if(near_failures)return 1;
 if(check(!shader_cache_variant_valid(SHADOW_NEAR_VARIANT|11) &&
          !shader_cache_variant_valid(SHADOW_NEAR_VARIANT|0x80000000u) &&
          !shader_cache_variant_valid(0x80000000u),"unknown cache flags and base variants rejected"))return 1;
 return 0;
}}
'''


def contact_sync_compiles(post):
    body = post.split("static id<MTLTexture> x_contact_scene_impl(id<MTLTexture> scene", 1)[1]
    body = body.split("\n}\n", 1)[0]
    return len(re.findall(r"newLibraryWithSource:[^\]]*error:", body))



# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_CONTACT = r'''static id<MTLTexture> x_contact_scene_impl(id<MTLTexture> scene, id<MTLTexture> depth,
                                    const X360PostParams *p, const X360PostView *view) {
    xr->ao_ready=0;
    if (!x360_post_ssao_enabled() || !depth) return scene;
    id<MTLTexture> mask=render_ssao_coverage(depth);
    if (!mask) {
        static int warned;
        if (diag_on && !warned) { warned=1;diag_event("ssao: On requested, skipped without current transparency coverage"); }
        return scene;
    }
    uint32_t platform=view ? M32(view->source_view) : 0x009842D0u;
    float px=MF32(platform+0x40), py=MF32(platform+0x54);
    if (!isfinite(px)||!isfinite(py)||fabsf(px)<0.01f||fabsf(py)<0.01f) return scene;
    static id<MTLRenderPipelineState> kernel,blur,apply,multiply_rgba,multiply_bgra,multiply_float,legacy_kernel,legacy_blur,legacy_apply;
    if (!kernel) {
        NSError *err=nil;
        NSString *src=[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_src]];
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&err];
        if (!lib) { fprintf(stderr,"boot: SSAO shader: %s\n",err.localizedDescription.UTF8String); return scene; }
        kernel=x_pso(lib,@"fs_ao",MTLPixelFormatRG16Float,0);
        blur=x_pso(lib,@"fs_ao_blur",MTLPixelFormatRG16Float,0);
        apply=x_pso(lib,@"fs_ao_apply",MTLPixelFormatRGBA8Unorm,0);
        multiply_rgba=x_contact_multiply(lib,MTLPixelFormatRGBA8Unorm);multiply_bgra=x_contact_multiply(lib,MTLPixelFormatBGRA8Unorm);
    }
    if(scene.pixelFormat==MTLPixelFormatRGBA16Float && !multiply_float) {
        NSString *src=[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_src]];
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:nil];
        multiply_float=x_contact_multiply(lib,MTLPixelFormatRGBA16Float);
    }
    id<MTLRenderPipelineState> multiply=scene.pixelFormat==MTLPixelFormatRGBA16Float?multiply_float:scene.pixelFormat==MTLPixelFormatBGRA8Unorm?multiply_bgra:multiply_rgba;
    if (!kernel||!blur||!apply||!multiply) return scene;
    NSUInteger hw=(scene.width+1)/2, hh=(scene.height+1)/2;
    int pair=x_contact_pair();
    if (!xr->ao[0]||xr->ao[0].width!=hw||xr->ao[0].height!=hh) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRG16Float width:hw height:hh mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead; d.storageMode=MTLStorageModePrivate;
        xr->ao[0]=[mtl newTextureWithDescriptor:d];xr->ao[1]=[mtl newTextureWithDescriptor:d];
    }
    if (pair && (!xr->ao_scene||xr->ao_scene.width!=scene.width||xr->ao_scene.height!=scene.height))
    {
        xr->ao_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
        if (pair) xr->ao_reference_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
    }
    if (pair&&(!xr->ao_reference_scene||xr->ao_reference_scene.width!=scene.width||xr->ao_reference_scene.height!=scene.height))
        xr->ao_reference_scene=x_rt_format((uint32_t)scene.width,(uint32_t)scene.height,0,MTLPixelFormatRGBA8Unorm);
    id<MTLTexture> applied=scene;
    if (!xr->ao[0]||!xr->ao[1]||!applied||(pair&&(!xr->ao_scene||!xr->ao_reference_scene))) return scene;
    AOParams k={.proj={1/px,1/py,p->znear,p->zfar},.size={1.0f/scene.width,1.0f/scene.height,0,0},
        .rect={0,0,1,1},.bounds={0.5f/depth.width,0.5f/depth.height,1-0.5f/depth.width,1-0.5f/depth.height},.tune={0.75f,0.08f,0,0}};
    // Keep the original two-pixel bilateral sigma despite the coarser AO grid.
    k.size[2]=0.25f*(scene.width/(float)hw)*(scene.width/(float)hw);
    k.size[3]=0.25f*(scene.height/(float)hh)*(scene.height/(float)hh);
    if (view) for (int j=0;j<2;++j) {
        float sz=j?depth.height:depth.width;
        k.rect[j]=view->depth[j]/sz;k.rect[j+2]=view->depth[j+2]/sz;
        k.bounds[j]=(view->depth[j]+0.5f)/sz;k.bounds[j+2]=(view->depth[j]+view->depth[j+2]-0.5f)/sz;
    }
    const char *labels[]={"ssao kernel","ssao bilateral H","ssao bilateral V","ssao apply"};
    // Negative control and normal AO consume exactly the same held scene/depth.
    // Finish with normal AO so diagnostics cannot change the displayed look.
    id<MTLTexture> __strong *reference=xr->ao_reference;
    if (pair) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:(scene.width+1)/2 height:(scene.height+1)/2 mipmapped:NO];
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;
        reference[0]=[mtl newTextureWithDescriptor:d];reference[1]=[mtl newTextureWithDescriptor:d];
        if (!reference[0]||!reference[1]) return scene;
        if(!xr->ao_original||xr->ao_original.width!=scene.width||xr->ao_original.height!=scene.height||xr->ao_original.pixelFormat!=scene.pixelFormat){
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;xr->ao_original=[mtl newTextureWithDescriptor:d];
        }
        id<MTLBlitCommandEncoder> original=gpt_blit(cmd,"ssao diagnostic original");
        [original copyFromTexture:scene sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(scene.width,scene.height,1) toTexture:xr->ao_original destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];[original endEncoding];
        x_contact_shot(scene,"skipped","on"); // deliberately broken: the apply is absent
    }
    if (pair&&!legacy_kernel) {
        NSError *error=nil;id<MTLLibrary> lib=[mtl newLibraryWithSource:[[NSString stringWithUTF8String:x360_src] stringByAppendingString:[NSString stringWithUTF8String:ssao_legacy_src]] options:nil error:&error];
        legacy_kernel=x_pso(lib,@"fs_ao",reference[0].pixelFormat,0);legacy_blur=x_pso(lib,@"fs_ao_blur",reference[0].pixelFormat,0);legacy_apply=x_pso(lib,@"fs_ao_apply",MTLPixelFormatRGBA8Unorm,0);
        if (!legacy_kernel||!legacy_blur||!legacy_apply) return scene;
    }
    id<MTLTexture> reference_mask=pair?render_ssao_reference_coverage():nil;
    id<MTLTexture> cpu_mask=pair?render_ssao_cpu_reference_coverage():nil;
    if(pair&&(!reference_mask||!cpu_mask))return scene;
    static id<MTLTexture> uncovered;
    if(pair){
        if(!uncovered){MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:1 height:1 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;uncovered=[mtl newTextureWithDescriptor:d];uint8_t white=255;[uncovered replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&white bytesPerRow:1];}
        if(!xr->ao_cpu_scene||xr->ao_cpu_scene.width!=scene.width||xr->ao_cpu_scene.height!=scene.height||xr->ao_cpu_scene.pixelFormat!=scene.pixelFormat){MTLTextureDescriptor*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:scene.pixelFormat width:scene.width height:scene.height mipmapped:NO];d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;d.storageMode=MTLStorageModePrivate;xr->ao_cpu_scene=[mtl newTextureWithDescriptor:d];}
    }
    if (pair&&!view) {x_contact_shot(mask,"normal","coverage");x_contact_shot(cpu_mask,"cpu_reference","coverage");x_contact_shot(reference_mask,"legacy","coverage");}
    for (int variant=pair?4:0;variant>=0;--variant) {
        k.tune[0]=0.75f*(variant==1?8.0f:1.0f);
        const char *name=variant==4?"uncovered":variant==3?"cpu_reference":variant==2?"legacy":variant?"broken":"normal";
        int multiplied=variant==0||variant>=3;
        if(variant>=3){id<MTLBlitCommandEncoder>b=gpt_blit(cmd,"ssao proof original copy");[b copyFromTexture:scene toTexture:xr->ao_cpu_scene];[b endEncoding];}
        if (pair) x_contact_shot(scene,name,"off");
        for (int pass=0;pass<4;++pass) {
            id<MTLTexture> __strong *chain=variant==2?reference:xr->ao;
            id<MTLTexture> target=pass==3?(variant>=3?xr->ao_cpu_scene:variant==2?xr->ao_reference_scene:variant==1?xr->ao_scene:applied):chain[pass==1];
            id<MTLTexture> input=pass==1?chain[0]:pass==2?chain[1]:scene;
            k.tune[2]=pass==1?1.0f/chain[0].width:0;k.tune[3]=pass==2?1.0f/chain[0].height:0;
            MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
            rp.colorAttachments[0].texture=target;rp.colorAttachments[0].loadAction=pass==3&&multiplied?MTLLoadActionLoad:MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLTexture> smoke=pass==3 && variant==0 && !view?render_soft_smoke_layer():nil;
            id<MTLRenderPipelineState> smoke_pso=smoke?x_contact_smoke_multiply(target.pixelFormat):nil;
            if (gpt_on) gpt_render(rp,"%s",smoke_pso?"ssao multiply + soft smoke":pass==3&&variant==0?"ssao multiply":labels[pass]);
            id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:rp];
            if (gpt_on) gpt_name(e);
            [e setRenderPipelineState:smoke_pso?:variant==2?(pass==0?legacy_kernel:pass==3?legacy_apply:legacy_blur):(pass==0?kernel:pass==3?(multiplied?multiply:apply):blur)];
            if(smoke_pso){[e setFragmentTexture:smoke atIndex:4];render_soft_smoke_layer_applied();}
            if(pass!=3||!multiplied)[e setFragmentTexture:input atIndex:0];[e setFragmentTexture:depth atIndex:1];
            if (pass==0||pass==3) [e setFragmentTexture:variant==4?uncovered:variant==3?cpu_mask:variant==2?reference_mask:mask atIndex:3];
            if (pass==3) [e setFragmentTexture:chain[0] atIndex:2];
            [e setFragmentBytes:&k length:sizeof k atIndex:0];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        }
        if (pair) x_contact_shot(variant>=3?xr->ao_cpu_scene:variant==2?xr->ao_reference_scene:variant==1?xr->ao_scene:applied,name,"on");
    }
    xr->ao_ready=1;
    return applied;
}
'''
assert hashlib.sha256(OLD_CONTACT.encode()).hexdigest() == '416372ce18268fe2ee157b0a60f01f28f126e453f3186926a627d5413a25c886', 'pinned baseline changed'

def contact_off_frame():
    post = (ROOT / "runtime/render_post.m").read_text()
    normal = contact_sync_compiles(post)
    assert normal == 0, f"contact shadows compile on the frame: {normal}"
    # Literal frame-path call sites retained from the original initializer.
    # The checker needs only these expressions, independent of repository history.
    original = '''static id<MTLTexture> x_contact_scene_impl(id<MTLTexture> scene) {
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&err];
        id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:nil];
}\n'''
    broken = contact_sync_compiles(original)
    assert broken == 2, "literal original should show two frame-path compiles"
    print(f"contact shadows: frame-path compiles normal {normal}, pinned original {broken}")


def offline():
    contact_off_frame()
    shape = between("// BEGIN offline shader options test\n", "// END offline shader options test")
    submit = between("// BEGIN offline shader scheduling test\n", "// END offline shader scheduling test")
    ready = between("// BEGIN offline shader readiness test\n", "// END offline shader readiness test")
    library = "typedef struct ShaderLibrary" + between("typedef struct ShaderLibrary", "static pthread_mutex_t shader_source_mu")
    new = "static ShaderEntry *new_entry" + between("static ShaderEntry *new_entry", "// BEGIN offline shader scheduling test")
    cache = "static void cache_path" + between("static void cache_path", "// Lookup cache in front")
    lookup = "typedef struct { uint32_t serial, variant;" + between("typedef struct { uint32_t serial, variant;", "// The game's fx_2_0 resources")
    fnv = "static uint64_t fnv" + between("static uint64_t fnv", "// ").split("\n}\n", 1)[0] + "\n}\n"
    code = PRELUDE + fnv + shape + new + submit + ready + library + cache + lookup + MAIN
    synthetic = ROOT / "host/shaders/testdata_synth/synth/vs20_math.bin"
    fixtures = sorted((ROOT / "host/shaders/testdata").glob("**/*.bin"))
    with tempfile.TemporaryDirectory(prefix="compilehitch-") as d:
        tmp = Path(d)
        objects = []
        for source in ("sm_parse.cpp", "sm_msl.cpp"):
            obj = tmp / (source + ".o")
            run(["clang++", "-std=c++17", "-O1", "-c", str(ROOT / "host/shaders" / source), "-o", str(obj)])
            objects.append(str(obj))
        def build(name, contents):
            path = tmp / (name + ".m"); path.write_text(contents)
            exe = tmp / name
            run(["clang", "-O1", "-fobjc-arc", "-I", str(ROOT), str(path), *objects,
                 "-framework", "Foundation", "-lc++", "-o", str(exe)])
            profile = tmp / (name + "-profile"); profile.mkdir()
            return [str(exe), str(profile), str(synthetic)]
        print(run(build("normal", code) + [str(p) for p in fixtures]).strip())
        broken_near = code.replace("    variant &= ~SHADOW_NEAR_VARIANT;", "    (void)variant;")
        assert broken_near != code
        print("broken near cache: " + run(build("broken-near-cache", broken_near), 1).strip())
        broken_flags = code.replace("    variant &= ~SHADOW_NEAR_VARIANT;", "    variant &= 0x3FFFFFFFu;")
        assert broken_flags != code
        print("broken cache flags: " + run(build("broken-cache-flags", broken_flags), 1).strip())
        broken = code.replace("    shader_submit(e);\n    cache_append(e);", "    compile_entry(e);\n    cache_append(e);")
        assert broken != code
        print("broken scheduling: " + run(build("broken", broken), 1).strip())
        broken_cache = code.replace(" && variant != MAT_VARIANT_AUX", "")
        assert broken_cache != code
        print("broken cache: " + run(build("broken-cache", broken_cache), 1).strip())
        broken_clear = code.replace(" && variant != MAT_VARIANT_HEADLAMP_CLEAR", "")
        assert broken_clear != code
        print("broken Clear cache: " + run(build("broken-clear-cache", broken_clear), 1).strip())
        broken_film = code.replace(" && variant != MAT_VARIANT_ROAD_FILM", "")
        assert broken_film != code
        print("broken film cache: " + run(build("broken-film-cache", broken_film), 1).strip())
        for material in ('GLASS', 'CHROME'):
            broken_material = code.replace(' && variant != MAT_VARIANT_REAL_' + material, '')
            assert broken_material != code
            print('broken material cache ' + material + ': ' + run(build('broken-material-' + material, broken_material), 1).strip())
        broken_ready = code.replace("if (!__atomic_load_n(&e->pending, __ATOMIC_ACQUIRE)) return 0;",
                                    "if (!__atomic_load_n(&e->pending, __ATOMIC_ACQUIRE)) return 1;")
        assert broken_ready != code
        print("broken readiness: " + run(build("broken-ready", broken_ready), 1).strip())
        broken_library = code.replace("ShaderLibrary *s = shader_libraries;", "ShaderLibrary *s = NULL;")
        assert broken_library != code
        print("broken library sharing: " + run(build("broken-library", broken_library), 1).strip())
        broken_options = code.replace("    opt.vs_input_int &= s->inputs;", "    opt.shadow_samplers = 0;\n    opt.vs_input_int &= s->inputs;")
        assert broken_options != code
        if fixtures:
            print("broken options: " + run(build("broken-options", broken_options) + [str(p) for p in fixtures], 1).strip())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--diag":
        sys.exit(diagnostic(Path(sys.argv[2])))
    offline()
