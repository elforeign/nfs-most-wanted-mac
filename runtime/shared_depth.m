#import "shared_depth.h"
#ifndef SHARED_DEPTH_TEST
#include "pipeline_cache.h"
extern int gpt_on;
void gpt_render(MTLRenderPassDescriptor *, const char *, ...);
void gpt_name(id<MTLCommandEncoder>);
#endif
#include <string.h>
#include <stddef.h>
#include <stdio.h>
#include <math.h>

// The normal formula and snapped sample location intentionally match the
// original contact-shadow kernel. Min/max separately covers every source pixel,
// including odd dimensions, instead of bounding only the snapped samples.
static const char shared_depth_msl[] =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct K { float4 proj,size,rect,bounds; };\n"
"struct V { float4 pos [[position]]; float2 uv; };\n"
"vertex V sd_vs(uint id [[vertex_id]]) { float2 p=float2((id<<1)&2,id&2);return {float4(p*float2(2,-2)+float2(-1,1),0,1),p}; }\n"
"inline float sd_z(float d,constant K &k){return k.proj.z*k.proj.w/(k.proj.w-d*(k.proj.w-k.proj.z));}\n"
"inline float sd_raw(depth2d<float> z,float2 uv,constant K &k){constexpr sampler pt(coord::normalized,filter::nearest,address::clamp_to_edge);return z.sample(pt,clamp(k.rect.xy+uv*k.rect.zw,k.bounds.xy,k.bounds.zw));}\n"
"inline float3 sd_pos(depth2d<float> z,float2 uv,constant K &k){float d=sd_z(sd_raw(z,uv,k),k);return float3((uv*float2(2,-2)+float2(-1,1))*k.proj.xy*d,d);}\n"
"inline float3 sd_normal(depth2d<float> z,float2 uv,constant K &k){\n"
" float3 p=sd_pos(z,uv,k);float2 t=k.size.xy;\n"
" float3 l=p-sd_pos(z,uv-float2(t.x,0),k),r=sd_pos(z,uv+float2(t.x,0),k)-p;\n"
" float3 u=p-sd_pos(z,uv-float2(0,t.y),k),d=sd_pos(z,uv+float2(0,t.y),k)-p;\n"
" float3 n=cross(abs(l.z)<abs(r.z)?l:r,abs(u.z)<abs(d.z)?u:d);\n"
" n*=rsqrt(max(dot(n,n),1e-20));return dot(n,p)>0?-n:n; }\n"
"struct O {float4 dn [[color(0)]];float2 mm [[color(1)]];};\n"
"fragment O sd_half(V i [[stage_in]],depth2d<float> z [[texture(0)]],constant K &k [[buffer(0)]]){\n"
" float2 uv=(floor(i.uv/k.size.xy)+0.5)*k.size.xy;\n"
" float3 n=sd_normal(z,uv,k);float d=sd_z(sd_raw(z,uv,k),k);\n"
#ifdef SHARED_DEPTH_TEST_FLIP_NORMALS
" n=-n;\n"
#endif
" uint2 base=uint2(i.pos.xy)*2;float lo=INFINITY,hi=-INFINITY;\n"
" for(uint y=0;y<2;++y)for(uint x=0;x<2;++x){float2 q=min(float2(base+uint2(x,y))+0.5,1.0/k.size.xy-0.5)*k.size.xy;float v=sd_z(sd_raw(z,q,k),k);lo=min(lo,v);hi=max(hi,v);}\n"
" return {float4(n,d),float2(lo,hi)}; }\n"
"fragment float2 sd_reduce(V i [[stage_in]],texture2d<float,access::read> z [[texture(0)]]){\n"
" uint2 base=uint2(i.pos.xy)*2,m=uint2(z.get_width()-1,z.get_height()-1);float lo=INFINITY,hi=-INFINITY;\n"
" for(uint y=0;y<2;++y)for(uint x=0;x<2;++x){float2 v=z.read(min(base+uint2(x,y),m)).xy;lo=min(lo,v.x);hi=max(hi,v.y);}\n"
" return float2(lo,hi); }\n";

typedef struct {
    SharedDepthSet set;
    SharedDepthInput input;
    uint64_t requested;
    int requested_valid, valid;
} DepthSlot;
static DepthSlot slots[SHARED_DEPTH_VIEWS];
static id<MTLRenderPipelineState> half_pso, reduce_pso;

static int build_pipelines(id<MTLDevice> device,id<MTLLibrary> lib) {
    for (unsigned pass=0;pass<2;++pass) {
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"sd_vs"];
        d.fragmentFunction=[lib newFunctionWithName:pass?@"sd_reduce":@"sd_half"];
        d.colorAttachments[0].pixelFormat=pass?MTLPixelFormatRG32Float:MTLPixelFormatRGBA32Float;
        if (!pass) d.colorAttachments[1].pixelFormat=MTLPixelFormatRG32Float;
#ifdef SHARED_DEPTH_TEST
        NSError *error=nil;
        id<MTLRenderPipelineState> p=[device newRenderPipelineStateWithDescriptor:d error:&error];
        if (!p) fprintf(stderr,"shared depth pipeline: %s\n",error.localizedDescription.UTF8String);
#else
        id<MTLRenderPipelineState> p=pipeline_build_device(device,d,"shared_depth",NULL);
#endif
        if (pass) reduce_pso=p;else half_pso=p;
    }
    return half_pso && reduce_pso;
}
static int pipelines(id<MTLDevice> device) {
#ifdef SHARED_DEPTH_TEST
    if (half_pso && reduce_pso) return 1;
    NSError *error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithUTF8String:shared_depth_msl] options:nil error:&error];
    if (!lib) { fprintf(stderr,"shared depth: %s\n",error.localizedDescription.UTF8String);return 0; }
    return build_pipelines(device,lib);
#else
    // Like contact shadows, compile off the frame. Pending/failed preparation
    // exposes no set; a frame never waits on the Metal compiler queue.
    static int state;
    if (__atomic_load_n(&state,__ATOMIC_ACQUIRE)==2) return 1;
    int expected=0;
    if (__atomic_compare_exchange_n(&state,&expected,1,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)) {
        [device newLibraryWithSource:[NSString stringWithUTF8String:shared_depth_msl] options:nil
            completionHandler:^(id<MTLLibrary> lib,NSError *error) {
                int ready=lib && build_pipelines(device,lib);
                if (!ready) fprintf(stderr,"shared depth: compile failed: %s\n",error?error.localizedDescription.UTF8String:"pipeline unavailable");
                __atomic_store_n(&state,ready?2:3,__ATOMIC_RELEASE);
            }];
    }
    return 0;
#endif
}
static id<MTLTexture> texture(id<MTLDevice> device,MTLPixelFormat format,unsigned w,unsigned h) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    d.storageMode=MTLStorageModePrivate;
    return [device newTextureWithDescriptor:d];
}
void shared_depth_request(unsigned view,uint64_t frame) {
    if (view>=SHARED_DEPTH_VIEWS) return;
    slots[view].requested=frame;slots[view].requested_valid=1;
}
const SharedDepthSet *shared_depth_get(unsigned view,uint64_t frame) {
    if (view>=SHARED_DEPTH_VIEWS) return NULL;
    DepthSlot *s=&slots[view];
    return s->valid && s->set.frame==frame?&s->set:NULL;
}
static void discard(DepthSlot *s) {
    uint64_t epoch=s->set.epoch;
    s->set=(SharedDepthSet){.epoch=epoch};s->valid=0;
}
const SharedDepthSet *shared_depth_prepare(id<MTLDevice> device,id<MTLCommandBuffer> cb,
    unsigned view,id<MTLTexture> resolved,const SharedDepthInput *in,int ao_on) {
    if (view>=SHARED_DEPTH_VIEWS || !in) return NULL; // mirrors never alias a player
    // Switching between SP and split releases inactive storage. Frame validation
    // rejects old data even when a view disappears without another hook call.
    if (!view) { discard(&slots[1]);discard(&slots[2]); }
    else discard(&slots[0]);
    DepthSlot *s=&slots[view];
    int wanted=ao_on || (s->requested_valid && s->requested==in->frame);
    if (!wanted || !resolved || !cb || !device || !in->width || !in->height || resolved.sampleCount!=1) {
        discard(s);return NULL;
    }
    // All spatial contents expire every frame, including same-object camera cuts.
    // Structural/identity changes additionally retire textures and advance epoch.
    int reset=!s->set.depth_normal || memcmp(&s->input.width,&in->width,
        offsetof(SharedDepthInput,params)-offsetof(SharedDepthInput,width));
#ifdef SHARED_DEPTH_TEST_NO_RESIZE_RESET
    if (s->set.depth_normal && (s->input.width!=in->width || s->input.height!=in->height)) reset=0;
#endif
    if (!reset && s->valid && s->set.frame==in->frame && s->set.resolved==resolved &&
        !memcmp(&s->input.params,&in->params,sizeof in->params)) return &s->set;
    s->valid=0;
    if (!pipelines(device)) return NULL;
    if (reset) {
        discard(s);++s->set.epoch;
        unsigned w=(in->width+1)/2,h=(in->height+1)/2;
        s->set.depth_normal=texture(device,MTLPixelFormatRGBA32Float,w,h);
        for (unsigned level=0;level<SHARED_DEPTH_LEVELS;++level) {
            s->set.minmax[level]=texture(device,MTLPixelFormatRG32Float,w,h);
            s->set.levels=level+1;
            if (w==1 && h==1) break;
            w=(w+1)/2;h=(h+1)/2;
        }
    }
    if (!s->set.depth_normal) { discard(s);return NULL; }
    for (unsigned l=0;l<s->set.levels;++l) if (!s->set.minmax[l]) { discard(s);return NULL; }
    s->input=*in;s->set.resolved=resolved;s->set.params=in->params;s->set.frame=in->frame;
    for (unsigned level=0;level<s->set.levels;++level) {
#ifdef SHARED_DEPTH_TEST_SKIP_LEVEL
        if (level==1) continue;
#endif
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=level?s->set.minmax[level]:s->set.depth_normal;
        rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        if (!level) {
            rp.colorAttachments[1].texture=s->set.minmax[0];
            rp.colorAttachments[1].loadAction=MTLLoadActionDontCare;rp.colorAttachments[1].storeAction=MTLStoreActionStore;
        }
#ifndef SHARED_DEPTH_TEST
        if (gpt_on) gpt_render(rp,level?"shared depth minmax L%u":"shared depth normals + minmax L%u",level);
#endif
        id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
#ifndef SHARED_DEPTH_TEST
        if (gpt_on) gpt_name(e);
#endif
        [e setRenderPipelineState:level?reduce_pso:half_pso];
        [e setFragmentTexture:level?s->set.minmax[level-1]:resolved atIndex:0];
        if (!level) [e setFragmentBytes:&in->params length:sizeof in->params atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    }
    s->valid=1;return &s->set;
}

extern id<MTLDevice> mtl;
extern id<MTLCommandBuffer> cmd;
extern uint64_t frame_serial;
extern int x360_post_ssao_enabled(void);
static uint32_t view_player(unsigned slot) {
    // Local-player vector: +4 data, +0xC count; IPlayer +0x1C is its logical
    // slot. Track the object, not the slot number, across removal/reinsertion.
    uint32_t list=M32(0x0092D87Cu),count=M32(0x0092D884u),logical=slot==2;
    for (uint32_t i=0;list && i<count;++i) {
        uint32_t player=M32(list+i*4);
        if (player && M32(player+0x1C)==logical) return player;
    }
    return 0;
}
void render_shared_depth_pre_treatment(id<MTLTexture> scene,id<MTLTexture> resolved,
    unsigned samples,unsigned hdr,const X360PostParams *p,const X360PostView *view,unsigned mode) {
    unsigned slot=view?(view->source_view==0x009196C0u?2:1):0;
    // Only the two player eViews are accepted; mirrors never inherit P0's slot.
    if (view && view->source_view!=0x00919650u && view->source_view!=0x009196C0u) return;
    SharedDepthInput in={.frame=frame_serial};
    int ao=x360_post_ssao_enabled();
    if (!ao && !(slots[slot].requested_valid && slots[slot].requested==frame_serial)) {
        shared_depth_prepare(mtl,cmd,slot,nil,&in,0);return;
    }
    uint32_t platform=view?M32(view->source_view):0x009842D0u;
    float px=MF32(platform+0x40),py=MF32(platform+0x54);
    if (!resolved || !scene || !isfinite(px) || !isfinite(py) || fabsf(px)<0.01f || fabsf(py)<0.01f) {
        discard(&slots[slot]);return;
    }
    in.width=(uint32_t)scene.width;in.height=(uint32_t)scene.height;in.samples=samples;
    in.hdr=hdr;
    in.generation=view?view->generation:0;in.mode=mode;
    in.camera=M32(view?view->source_view+0x40:0x00919690u);
    in.player=view_player(slot);
    in.params=(SharedDepthParams){.proj={1/px,1/py,p->znear,p->zfar},
        .size={1.0f/scene.width,1.0f/scene.height,0,0},.rect={0,0,1,1},
        .bounds={0.5f/resolved.width,0.5f/resolved.height,1-0.5f/resolved.width,1-0.5f/resolved.height}};
    if (view) for (unsigned j=0;j<2;++j) {
        float sz=j?resolved.height:resolved.width;
        in.params.rect[j]=view->depth[j]/sz;in.params.rect[j+2]=view->depth[j+2]/sz;
        in.params.bounds[j]=(view->depth[j]+0.5f)/sz;
        in.params.bounds[j+2]=(view->depth[j]+view->depth[j+2]-0.5f)/sz;
    }
    shared_depth_prepare(mtl,cmd,slot,resolved,&in,ao);
}
