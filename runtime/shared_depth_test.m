// Metal fixture: analytic plane/sphere normals; every min/max level (odd sizes);
// Shipping MSAA resolve + four-tap AO encoders versus the pinned original.
// Production graph hook vs pinned pre-graph main: 108 cases (SP/P0/P1;
// RGBA/BGRA/HDR; x1/x2/x4/x8; AO/full/half smoke), 0 unequal stored codes.
// Omitted dispatch: 4,893,908 unequal codes / 176 failures; fixed: 0 / 0.
// Registered normals/minmax with AO Off: old request path 42 checks / 13
// failures, 0/6 ready; fixed 60 / 0, 6/6 ready. Empty/default-Off/mirror paths:
// 0 device allocations, encodes or bindings. Split AO stays On; smoke is SP-only.
// Limits: normal vector error <=0.04 (sphere finite differences), plane <=0.001;
// pyramid relative error <=2e-5; AO/SDR <=1e-4. Stored HDR <=one half-float code
// step, with AO checked before quantization (a rounding boundary may amplify it).
// Measured: production 14,960,067 checks / 0 failures; 10,410 normals, plane max
// 0.0000308623, sphere max 0.0351433; 1,138 min/max pairs, relative max 1.1058e-7.
// 10,084,488 AO/depth/colour samples: AO max 7.6294e-6; HDR differs in 24
// samples by one code step (max 0.001953125 at scene red 2.5). Alpha exact.
// SP/P0/P1 darkened pixels: SDR 3,814/3,860/5,921; HDR 9,166/9,255/9,386.
// Original entry point: shipping 0/36 and bridge 0/8 => 2 coverage failures;
// updated entry point 36/36 and 8/8 => 0. Original SP binding: 2/18 failures
// vs 0/18 fixed, replacing only the live IPlayer with a stable camera/view.
// Flipped normals: 967,424 failures, plane error 2.00000024, AO max 0.242798,
// colour max 0.201172. Skipped L1: 315 failures, pyramid relative
// error 8.47289944 (production 1.1058e-7). No resize reset: 4/58 lifetime failures
// (production 0), held 33x25 instead of required 31x23, epoch delta 0 instead of 1.
// Resets cover resize/MSAA/HDR/camera/player/generation/mode, current-frame cuts,
// disjoint split rectangles, request expiry, mirror exclusion and same-frame reuse.
#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "shared_depth.h"
#include "pre_treatment.h"
#include "shared_depth_reference.h"
#include "shared_depth_current.h" // extracted shipping AO by shared_depth_test.py

static unsigned checks,failures,normal_samples,pyramid_samples,ao_samples;
static float plane_error,sphere_error,pyramid_error,ao_error,colour_error,ao_peak;
static id<MTLDevice> device;
static id<MTLCommandQueue> queue;
id<MTLDevice> mtl;
id<MTLCommandBuffer> cmd;
uint64_t frame_serial;
uint8_t *G;
int diag_on,gpt_on;
static int ao_enabled=1;
int x360_post_ssao_enabled(void) { return ao_enabled; }
// The original bridge called this with zero outside a split-capture scope.
uint32_t split_capture_player(uint32_t original) { return original; }
static unsigned production_cases,bridge_cases,player_cases;
static unsigned hdr_rounding_samples;
static unsigned graph_cases,graph_mismatches,smoke_applied,smoke_finished;
static unsigned graph_sp_cases,graph_split_cases;
static id<MTLTexture> active_smoke,active_smoke_depth;
int ssao_cpu_diag;
uint64_t ssao_diag_post_ns;
static id<MTLRenderPipelineState> reference_contact_multiply(id<MTLLibrary>,MTLPixelFormat);
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures<10) fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)
uint64_t host_ticks(void) { CHECK(0);return 0; }

static void complete(id<MTLCommandBuffer> cb) {
    [cb commit];[cb waitUntilCompleted];
    CHECK(cb.status==MTLCommandBufferStatusCompleted);
    if (cb.error) fprintf(stderr,"Metal: %s\n",cb.error.localizedDescription.UTF8String);
}
static id<MTLTexture> tex(MTLPixelFormat format,unsigned w,unsigned h,int upload) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    d.storageMode=upload?MTLStorageModeShared:MTLStorageModePrivate;
    id<MTLTexture> t=[device newTextureWithDescriptor:d];CHECK(t!=nil);return t;
}
static float *read_texture(id<MTLTexture> t,unsigned channels) {
    unsigned w=(unsigned)t.width,h=(unsigned)t.height;
    int half=t.pixelFormat==MTLPixelFormatRG16Float || t.pixelFormat==MTLPixelFormatRGBA16Float;
    int bgra=t.pixelFormat==MTLPixelFormatBGRA8Unorm;
    int unorm=t.pixelFormat==MTLPixelFormatRGBA8Unorm || bgra;
    unsigned bytes=unorm?1:half?2:4,pitch=(w*channels*bytes+255)&~255u;
    id<MTLBuffer> b=[device newBufferWithLength:pitch*h options:MTLResourceStorageModeShared];CHECK(b!=nil);
    id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1)
        toBuffer:b destinationOffset:0 destinationBytesPerRow:pitch destinationBytesPerImage:pitch*h];
    [e endEncoding];complete(cb);
    float *out=calloc((size_t)w*h*channels,sizeof(float));
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w*channels;++x) {
        const char *p=(const char *)b.contents+y*pitch+x*bytes;
        if (unorm) out[y*w*channels+x]=*(const uint8_t *)p/255.f;
        else if (half) out[y*w*channels+x]=*(const __fp16 *)p;
        else memcpy(out+y*w*channels+x,p,4);
    }
    if (bgra) for (unsigned i=0;i<w*h;++i) {
        float r=out[i*4];out[i*4]=out[i*4+2];out[i*4+2]=r;
    }
    return out;
}
static SharedDepthInput input(unsigned w,unsigned h,uint64_t frame) {
    return (SharedDepthInput){.frame=frame,.width=w,.height=h,.samples=4,.camera=1,.player=1,
        .params={.proj={1,0.75f,0.5f,100},.size={1.f/w,1.f/h,0,0},.rect={0,0,1,1},
            .bounds={.5f/w,.5f/h,1-.5f/w,1-.5f/h}}};
}
static float linear_z(float raw,const SharedDepthParams *k) {
    return k->proj[2]*k->proj[3]/(k->proj[3]-raw*(k->proj[3]-k->proj[2]));
}
static float raw_z(float z,const SharedDepthParams *k) {
    return (k->proj[3]-k->proj[2]*k->proj[3]/z)/(k->proj[3]-k->proj[2]);
}
static float depth_at(unsigned x,unsigned y,unsigned w,unsigned h,int shape,const SharedDepthParams *k) {
    float rx=((x+.5f)/w*2-1)*k->proj[0],ry=(1-(y+.5f)/h*2)*k->proj[1],z;
    if (shape==0) z=3/(1-.15f*rx+.2f*ry); // z=3+.15x-.2y
    else if (shape==1) { // sphere centered at (0,0,4), radius 1.6; back plane at 10
        float a=rx*rx+ry*ry+1,disc=64-4*a*(16-1.6f*1.6f);
        z=disc>0?(8-sqrtf(disc))/(2*a):10;
    } else if (shape==2) {
        if (y<3) return 1; // sky and transparency stay untouched in final AO
        z=fminf(5,1/fmaxf(-ry,1e-6f));
    } else return .05f+.88f*((x*7+y*11)%19)/18.f;
    return raw_z(z,k);
}
static id<MTLTexture> depth_texture(unsigned w,unsigned h,int shape,SharedDepthInput *in,float **raw) {
    float *data=malloc((size_t)w*h*4);
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) data[y*w+x]=depth_at(x,y,w,h,shape,&in->params);
    if (shape==3) data[w*h-1]=.99f; // the odd last row/column must reach the root
    id<MTLTexture> z=tex(MTLPixelFormatDepth32Float,w,h,1);
    [z replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:data bytesPerRow:w*4];
    if (raw) *raw=data;else free(data);return z;
}
static const SharedDepthSet *prepare(unsigned slot,id<MTLTexture> z,SharedDepthInput *in,int ao) {
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    const SharedDepthSet *set=shared_depth_prepare(device,cb,slot,z,in,ao);
    complete(cb);CHECK(set!=NULL);return set;
}
static void check_normals(const SharedDepthSet *set,const SharedDepthInput *in,const float *raw,int shape) {
    float *v=read_texture(set->depth_normal,4);
    unsigned w=(in->width+1)/2,h=(in->height+1)/2;
    CHECK(set->depth_normal.width==w && set->depth_normal.height==h);
    if (set->depth_normal.width!=w || set->depth_normal.height!=h) { free(v);return; }
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) {
        unsigned fx=(unsigned)floorf((x+.5f)/w*in->width),fy=(unsigned)floorf((y+.5f)/h*in->height);
        float *n=v+(y*w+x)*4,z=linear_z(raw[fy*in->width+fx],&in->params);
        CHECK(isfinite(n[3]) && fabsf(n[3]-z)<=fmaxf(1,z)*2e-5f);
        if (fx<2 || fy<2 || fx+2>=in->width || fy+2>=in->height) continue;
        float expected[3]={.15f,-.2f,-1};
        if (shape==1) {
            expected[0]=((fx+.5f)/in->width*2-1)*in->params.proj[0]*z;
            expected[1]=(1-(fy+.5f)/in->height*2)*in->params.proj[1]*z;
            expected[2]=z-4;
            if (z>5 || expected[2]>-.8f) continue; // exclude silhouette one-sided stencil
        }
        float length=sqrtf(expected[0]*expected[0]+expected[1]*expected[1]+expected[2]*expected[2]),error=0;
        for (unsigned j=0;j<3;++j) error+=(n[j]-expected[j]/length)*(n[j]-expected[j]/length);
        error=sqrtf(error);++normal_samples;
        if (shape==0) { plane_error=fmaxf(plane_error,error);CHECK(error<=.001f); }
        else { sphere_error=fmaxf(sphere_error,error);CHECK(error<=.04f); }
    }
    free(v);
}
static void check_pyramid(const SharedDepthSet *set,const SharedDepthInput *in,const float *raw) {
    unsigned w=(in->width+1)/2,h=(in->height+1)/2,scale=2,level=0;
    for (;;) {
        CHECK(level<set->levels);if (level>=set->levels) break;
        CHECK(set->minmax[level].width==w && set->minmax[level].height==h);
        if (set->minmax[level].width!=w || set->minmax[level].height!=h) break;
        float *v=read_texture(set->minmax[level],2);
        for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) {
            float lo=INFINITY,hi=-INFINITY;
            // Independent full-resolution oracle, never reduce the tested chain.
            for (unsigned fy=y*scale;fy<fminf((y+1)*scale,in->height);++fy)
                for (unsigned fx=x*scale;fx<fminf((x+1)*scale,in->width);++fx) {
                    float z=linear_z(raw[fy*in->width+fx],&in->params);lo=fminf(lo,z);hi=fmaxf(hi,z);
                }
            float a=v[(y*w+x)*2],b=v[(y*w+x)*2+1];
            float err=fmaxf(fabsf(a-lo)/fmaxf(1,lo),fabsf(b-hi)/fmaxf(1,hi));
            pyramid_error=fmaxf(pyramid_error,err);++pyramid_samples;
            CHECK(isfinite(a) && isfinite(b) && err<=2e-5f);
        }
        free(v);++level;if (w==1 && h==1) break;
        w=(w+1)/2;h=(h+1)/2;scale*=2;
    }
    CHECK(level==set->levels);
}

static const char vertex_msl[]=
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct VOut {float4 pos [[position]];float2 uv;};\n"
"vertex VOut vs_full(uint vid [[vertex_id]]){float2 p=float2((vid<<1)&2,vid&2);return {float4(p*float2(2,-2)+float2(-1,1),0,1),p};}\n";
typedef struct { id<MTLRenderPipelineState> kernel,blur,apply[3],multiply[3]; } AOPipelines;
typedef struct { id<MTLTexture> kernel,blurred,colour; } AOResult;
static AOPipelines ao_pipelines(const char *source) {
    NSString *src=[NSString stringWithFormat:@"%s%s",vertex_msl,source];NSError *error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:src options:nil error:&error];
    if (!lib) { fprintf(stderr,"AO shader: %s\n",error.localizedDescription.UTF8String);exit(1); }
    AOPipelines p={0};
    for (unsigned i=0;i<5;++i) {
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"vs_full"];
        d.fragmentFunction=[lib newFunctionWithName:i==0?@"fs_ao":i==1?@"fs_ao_blur":@"fs_ao_apply"];
        d.colorAttachments[0].pixelFormat=i<2?MTLPixelFormatRG16Float:i==2?MTLPixelFormatRGBA8Unorm:i==3?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm;
        id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:d error:&error];
        if (!pipeline) { fprintf(stderr,"AO pipeline: %s\n",error.localizedDescription.UTF8String);exit(1); }
        if (i==0) p.kernel=pipeline;else if (i==1) p.blur=pipeline;else p.apply[i-2]=pipeline;
        if (i>=2) p.multiply[i-2]=reference_contact_multiply(lib,d.colorAttachments[0].pixelFormat);
    }
    return p;
}
static AOResult ao_render(AOPipelines p,const SharedDepthSet *set,id<MTLTexture> z,
    id<MTLTexture> mask,unsigned w,unsigned h,int hdr,id<MTLTexture> source) {
    unsigned hw=(w+1)/2,hh=(h+1)/2;
    id<MTLTexture> chain[3]={tex(MTLPixelFormatRG16Float,hw,hh,0),tex(MTLPixelFormatRG16Float,hw,hh,0),tex(MTLPixelFormatRG16Float,hw,hh,0)};
    id<MTLTexture> scene=source?:tex(MTLPixelFormatRGBA16Float,w,h,1);
    __fp16 *pixels=malloc((size_t)w*h*8);
    for (unsigned i=0;i<w*h;++i) { pixels[i*4]=hdr?2.5f:.7f;pixels[i*4+1]=.4f;pixels[i*4+2]=.2f;pixels[i*4+3]=.37f; }
    if (!source) [scene replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:pixels bytesPerRow:w*8];free(pixels);
    id<MTLTexture> out=tex(source?source.pixelFormat:hdr?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm,w,h,0);
    AOParams k={.tune={.75f,.08f,0,0}};memcpy(&k,&set->params,sizeof set->params);
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    if (source) {
        id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];[b copyFromTexture:source toTexture:out];[b endEncoding];
    }
    for (unsigned pass=0;pass<4;++pass) {
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=pass<3?chain[pass]:out;
        rp.colorAttachments[0].loadAction=pass==3&&source?MTLLoadActionLoad:MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
        unsigned format=out.pixelFormat==MTLPixelFormatBGRA8Unorm?2:hdr;
        [e setRenderPipelineState:pass==0?p.kernel:pass==3?(source?p.multiply[format]:p.apply[format]):p.blur];
        [e setFragmentTexture:pass==1?chain[0]:pass==2?chain[1]:scene atIndex:0];
        [e setFragmentTexture:z atIndex:1];[e setFragmentTexture:chain[2] atIndex:2];
        [e setFragmentTexture:mask atIndex:3];[e setFragmentTexture:set->depth_normal atIndex:4];
        k.tune[2]=pass==1?1.f/hw:0;k.tune[3]=pass==2?1.f/hh:0;
        [e setFragmentBytes:&k length:sizeof k atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    }
    complete(cb);return (AOResult){chain[0],chain[2],out};
}
static void compare_texture(id<MTLTexture> a,id<MTLTexture> b,unsigned channels) {
        unsigned n=(unsigned)(a.width*a.height*channels);
        CHECK(a.width==b.width && a.height==b.height);
        float *av=read_texture(a,channels),*bv=read_texture(b,channels);
        for (unsigned i=0;i<n;++i) {
            float err=fabsf(av[i]-bv[i]);++ao_samples;
            CHECK(isfinite(av[i]) && isfinite(bv[i]) && err<=1e-4f);
            if (channels==2 && !(i&1)) { ao_error=fmaxf(ao_error,err);ao_peak=fmaxf(ao_peak,bv[i]); }
            if (channels==4) colour_error=fmaxf(colour_error,err);
        }
        free(av);free(bv);
}
static void compare_ao(AOResult a,AOResult b) {
    compare_texture(a.kernel,b.kernel,2);compare_texture(a.blurred,b.blurred,2);compare_texture(a.colour,b.colour,4);
}
static void compare_stored_colour(id<MTLTexture> a,id<MTLTexture> b) {
    if (a.pixelFormat!=MTLPixelFormatRGBA16Float) { compare_texture(a,b,4);return; }
    float *av=read_texture(a,4),*bv=read_texture(b,4);
    for (unsigned i=0;i<a.width*a.height*4;++i) {
        // The float32 AO bound is checked independently before the multiply.
        // Crossing a half-float rounding boundary can turn sub-micro AO error
        // into one stored code step above 1.0; never allow a second code step.
        __fp16 ah=av[i],bh=bv[i];uint16_t ac,bc;
        memcpy(&ac,&ah,2);memcpy(&bc,&bh,2);
        CHECK(isfinite(av[i]) && isfinite(bv[i]) && av[i]>=0 && bv[i]>=0 && abs((int)ac-(int)bc)<=1);
        hdr_rounding_samples+=ac!=bc;++ao_samples;
        colour_error=fmaxf(colour_error,fabsf(av[i]-bv[i]));
    }
    free(av);free(bv);
}
static void check_ao(AOPipelines current,AOPipelines reference,const SharedDepthSet *set,
    SharedDepthInput *in,id<MTLTexture> z) {
    unsigned w=in->width,h=in->height,hw=(w+1)/2,hh=(h+1)/2;
    id<MTLTexture> mask=tex(MTLPixelFormatR8Unorm,hw,hh,1);uint8_t *m=malloc(hw*hh);
    for (unsigned y=0;y<hh;++y) for (unsigned x=0;x<hw;++x) m[y*hw+x]=x<3 || (x==hw/2 && y>hh/2)?0:255;
    [mask replaceRegion:MTLRegionMake2D(0,0,hw,hh) mipmapLevel:0 withBytes:m bytesPerRow:hw];free(m);
    for (int hdr=0;hdr<2;++hdr) compare_ao(ao_render(current,set,z,mask,w,h,hdr,nil),ao_render(reference,set,z,mask,w,h,hdr,nil));
}
// Off must never touch a device or command encoder, including after On.
@interface NoWork : NSObject
@property unsigned calls;
@end
@implementation NoWork
- (id)newTextureWithDescriptor:(id)d { (void)d;++_calls;return nil; }
- (id)renderCommandEncoderWithDescriptor:(id)d { (void)d;++_calls;return nil; }
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { (void)t;(void)i;++_calls; }
@end
static void check_off(id<MTLTexture> z,SharedDepthInput *in) {
    NoWork *trap=[NoWork new];
    CHECK(shared_depth_prepare((id)trap,(id)trap,0,z,in,0)==NULL);
    CHECK(trap.calls==0);CHECK(shared_depth_get(0,in->frame)==NULL);
}
static void check_lifetime(id<MTLTexture> z,SharedDepthInput in) {
    const SharedDepthSet *s=prepare(0,z,&in,1);uint64_t epoch=s->epoch;
    id<MTLTexture> original=s->depth_normal;
    CHECK(shared_depth_get(0,in.frame)==s);CHECK(shared_depth_get(0,in.frame+1)==NULL);
    NoWork *trap=[NoWork new];
    CHECK(shared_depth_prepare(device,(id)trap,0,z,&in,1)==s);CHECK(trap.calls==0);
    ++in.frame;prepare(0,z,&in,1);CHECK(shared_depth_get(0,in.frame-1)==NULL);
    CHECK(shared_depth_get(0,in.frame)->depth_normal==original); // no allocations between frames
    ++in.frame;in.params.proj[0]*=1.1f; // normal camera FOV changes reuse spatial storage
    id<MTLCommandBuffer> zoom=[queue commandBuffer];
    const SharedDepthSet *zoomed=shared_depth_prepare((id)trap,zoom,0,z,&in,1);
    CHECK(zoomed && zoomed->depth_normal==original);CHECK(trap.calls==0);complete(zoom);
    for (unsigned change=0;change<6;++change) {
        ++in.frame;
        if (change==0) in.samples=2;
        if (change==1) in.hdr=1;
        if (change==2) ++in.camera;
        if (change==3) ++in.player;
        if (change==4) ++in.generation;
        if (change==5) ++in.mode;
        s=prepare(0,z,&in,1);CHECK(s->epoch>epoch);CHECK(s->depth_normal!=original);
        epoch=s->epoch;original=s->depth_normal;
    }
    ++in.frame;check_off(z,&in); // release active storage; Off expires old set
    shared_depth_request(0,in.frame);s=prepare(0,z,&in,0);CHECK(s->frame==in.frame);
    ++in.frame;check_off(z,&in); // request lasts one frame
    CHECK(shared_depth_get(3,in.frame)==NULL);
    shared_depth_request(3,in.frame); // mirror rejected, even if explicitly requested
    CHECK(shared_depth_prepare(device,(id)trap,3,z,&in,1)==NULL);CHECK(trap.calls==0);
    in.width-=2;in.height-=2;in.params.size[0]=1.f/in.width;in.params.size[1]=1.f/in.height;
    ++in.frame;s=prepare(0,z,&in,1);epoch=s->epoch;original=s->depth_normal;
    in.width-=2;in.height-=2;in.params.size[0]=1.f/in.width;in.params.size[1]=1.f/in.height;
    ++in.frame;s=prepare(0,z,&in,1);
    CHECK(s->depth_normal.width==(in.width+1)/2);CHECK(s->depth_normal.height==(in.height+1)/2);
    CHECK(s->epoch>epoch);CHECK(s->depth_normal!=original);
}
static void check_cut(void) {
    // Reuse the camera object, depth texture and allocation across a cut. Only
    // the frame and its spatial contents change; no temporal data may survive.
    SharedDepthInput in=input(65,49,900);float *raw=NULL;
    id<MTLTexture> z=depth_texture(in.width,in.height,0,&in,&raw);
    const SharedDepthSet *s=prepare(0,z,&in,1);id<MTLTexture> held=s->depth_normal;
    float *before=read_texture(held,4);
    for (unsigned y=0;y<in.height;++y) for (unsigned x=0;x<in.width;++x)
        raw[y*in.width+x]=raw_z(7,&in.params);
    [z replaceRegion:MTLRegionMake2D(0,0,in.width,in.height) mipmapLevel:0 withBytes:raw bytesPerRow:in.width*4];
    ++in.frame;CHECK(shared_depth_get(0,in.frame)==NULL);
    s=prepare(0,z,&in,1);CHECK(s->depth_normal==held);CHECK(shared_depth_get(0,in.frame-1)==NULL);
    float *after=read_texture(s->depth_normal,4);unsigned count=(unsigned)(held.width*held.height);
    for (unsigned i=0;i<count;++i) {
        CHECK(fabsf(after[i*4+3]-7)<.001f);CHECK(after[i*4+3]>before[i*4+3]+2);
    }
    free(before);free(after);free(raw);
}
static void check_split(void) {
    // Two odd-height local views of one atlas, each with a different constant plane.
    unsigned w=35,h=55;float raw[w*h];
    SharedDepthInput a=input(w,27,1000),b=input(w,28,1000);
    for (unsigned y=0;y<h;++y) for (unsigned x=0;x<w;++x) raw[y*w+x]=raw_z(y<27?2:9,&a.params);
    id<MTLTexture> z=tex(MTLPixelFormatDepth32Float,w,h,1);
    [z replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:raw bytesPerRow:w*4];
    a.params.rect[3]=27.f/h;a.params.bounds[1]=.5f/h;a.params.bounds[3]=26.5f/h;
    b.params.rect[1]=27.f/h;b.params.rect[3]=28.f/h;b.params.bounds[1]=27.5f/h;b.params.bounds[3]=54.5f/h;
    const SharedDepthSet *sa=prepare(1,z,&a,1),*sb=prepare(2,z,&b,1);
    CHECK(sa->depth_normal!=sb->depth_normal);CHECK(shared_depth_get(0,1000)==NULL);
    float *av=read_texture(sa->minmax[sa->levels-1],2),*bv=read_texture(sb->minmax[sb->levels-1],2);
    CHECK(fabsf(av[0]-2)<.001f && fabsf(av[1]-2)<.001f);
    CHECK(fabsf(bv[0]-9)<.001f && fabsf(bv[1]-9)<.001f);free(av);free(bv);
}

// The production bridge reads the real guest-memory layout. Only unrelated
// diagnostics, optional smoke and the pipeline-cache service are test doubles.
static id<MTLTexture> coverage;
static struct {
    int ao_ready;
    id<MTLTexture> ao[2],ao_reference[2],ao_scene,ao_reference_scene,ao_original,ao_cpu_scene;
} resources[3],*xr=resources;
static RenderTex bb_rt,ad_rt,*enc_ms[5];
static id<MTLRenderCommandEncoder> enc;
static int resolve_magenta;
static uint8_t enc_resolve[5];
static uint64_t stat_resolves;
static void end_pass(void) { CHECK(enc==nil); }
static void ensure_cmd(void) { if (!cmd) cmd=[queue commandBuffer]; }
static FILE *dump_file(void) { return NULL; }
void probe_applied(int p,int applied) { (void)p;(void)applied;CHECK(0); }
static void gpt_render(MTLRenderPassDescriptor *rp,const char *fmt,...) { (void)rp;(void)fmt;CHECK(0); }
static void gpt_name(id<MTLCommandEncoder> e) { (void)e;CHECK(0); }
void diag_event(const char *fmt,...) { (void)fmt;CHECK(0); }
static int x_contact_pair(void) { return 0; }
void render_contact_warm(void) { CHECK(0); }
static id<MTLTexture> render_ssao_coverage(id<MTLTexture> z,int cuts) { (void)z;(void)cuts;return coverage; }
static id<MTLTexture> render_ssao_reference_coverage(void) { CHECK(0);return nil; }
static id<MTLTexture> render_ssao_cpu_reference_coverage(void) { CHECK(0);return nil; }
static id<MTLTexture> render_soft_smoke_layer(void) { return active_smoke; }
static id<MTLTexture> render_soft_smoke_half_depth(void) { return active_smoke_depth; }
static void render_soft_smoke_layer_applied(void) { ++smoke_applied; }
static void render_soft_smoke_late_finish(id<MTLTexture> scene) { CHECK(scene!=nil);++smoke_finished; }
static void x_contact_shot(id<MTLTexture> t,const char *a,const char *b) { (void)t;(void)a;(void)b;CHECK(0); }
static id<MTLBlitCommandEncoder> gpt_blit(id<MTLCommandBuffer> c,const char *name) { (void)name;return [c blitCommandEncoder]; }
static id<MTLTexture> x_rt_format(uint32_t w,uint32_t h,int unused,MTLPixelFormat f) { (void)unused;return tex(f,w,h,0); }
static id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d,const char *name,NSError **error) {
    (void)name;return [device newRenderPipelineStateWithDescriptor:d error:error];
}
static id<MTLLibrary> pipeline_library_source(NSString *src,const char *name,NSError **error) {
    (void)name;return [device newLibraryWithSource:src options:nil error:error];
}
static id<MTLRenderPipelineState> x_pso(id<MTLLibrary> lib,NSString *name,MTLPixelFormat f,int unused) {
    (void)unused;MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[lib newFunctionWithName:@"vs_full"];d.fragmentFunction=[lib newFunctionWithName:name];
    d.colorAttachments[0].pixelFormat=f;return pipeline_build(d,"fixture",NULL);
}
#include "shared_depth_production.h" // exact shipping resolve, descriptor and AO encoders

static void live_inputs(void) {
    if (!G) G=calloc(1,0x1000000);
    memset(G,0,0x1000000);
    MF32(0x009842D0u+0x40)=1;MF32(0x009842D0u+0x54)=1/.75f;
    M32(0x00919690u)=0x10000; // main eView camera
    M32(0x00919650u)=0x20000;M32(0x009196C0u)=0x21000; // split platforms
    MF32(0x20040)=1;MF32(0x20054)=1/.75f;MF32(0x21040)=2;MF32(0x21054)=2;
    M32(0x009196C0u+0x40)=0x11000;
    M32(0x0092D87Cu)=0x30000;M32(0x0092D884u)=2;
    // Deliberately reversed vector order: logical slot, not vector index, owns a view.
    M32(0x30000)=0x32000;M32(0x30004)=0x31000;
    M32(0x32000+0x1C)=1;M32(0x31000+0x1C)=0;M32(0x33000+0x1C)=0;
    ao_enabled=1;
}
static const SharedDepthSet *bridge_rt(id<MTLTexture> scene,RenderTex *depth,unsigned hdr,
    const X360PostView *view,unsigned mode) {
    const X360PostParams p={.znear=.5f,.zfar=100};
    cmd=[queue commandBuffer];
    CHECK(production_pre_treatment(scene,depth,hdr,&p,view,mode)==scene);
    complete(cmd);cmd=nil;
    const SharedDepthSet *s=shared_depth_get(view?(view->source_view==0x009196C0u?2:1):0,frame_serial);
    CHECK(s!=NULL);return s;
}
static id<MTLTexture> ms_texture(MTLPixelFormat,unsigned,unsigned,unsigned);
static const SharedDepthSet *bridge(id<MTLTexture> scene,id<MTLTexture> z,unsigned samples,unsigned hdr,
    const X360PostView *view,unsigned mode) {
    // Hold the fixture's temporary MS attachment through the shipping hook.
    // With a conditional aggregate initializer, optimized ARC releases its
    // temporary before bridge_rt's sampleCount message (sanitizers mask it).
    __attribute__((objc_precise_lifetime)) id<MTLTexture> ms=nil;
    if (samples>1) ms=ms_texture(MTLPixelFormatDepth32Float,(unsigned)z.width,(unsigned)z.height,samples);
    RenderTex depth={.tex=z,.ms=ms};
    return bridge_rt(scene,&depth,hdr,view,mode);
}
static void check_bridge(int player_only) {
    live_inputs();frame_serial=2000;
    SharedDepthInput in=input(65,49,frame_serial);
    id<MTLTexture> z=depth_texture(65,49,0,&in,NULL),scene=tex(MTLPixelFormatRGBA8Unorm,65,49,0);
    const SharedDepthSet *s=bridge(scene,z,1,0,NULL,0);if (!s) return;
    uint64_t epoch=s->epoch;id<MTLTexture> held=s->depth_normal;
    // SP has no split-capture scope. Only the IPlayer pointer changes: camera,
    // mode, size, projection and all other bridge arguments remain identical.
    M32(0x30004)=0x33000;++frame_serial;s=bridge(scene,z,1,0,NULL,0);
    CHECK(s->epoch==epoch+1);CHECK(s->depth_normal!=held);++player_cases;
    epoch=s->epoch;held=s->depth_normal;
    ++frame_serial;s=bridge(scene,z,1,0,NULL,0);
    CHECK(s->epoch==epoch);CHECK(s->depth_normal==held);
    if (player_only) return;
    for (unsigned change=0;change<5;++change) {
        unsigned samples=change>=1?4:1,hdr=change>=2;
        if (change==0) M32(0x00919690u)=0x12000;
        if (change==3) { MF32(0x009842D0u+0x40)=2;MF32(0x009842D0u+0x54)=2; }
        ++frame_serial;s=bridge(scene,z,samples,hdr,NULL,change>=4?7:0);
        CHECK(s->epoch==epoch+(change!=3));
        CHECK((s->depth_normal==held)==(change==3));
        CHECK(fabsf(s->params.proj[0]-(change>=3?.5f:1))<1e-6f);
        epoch=s->epoch;held=s->depth_normal;++bridge_cases;
    }
    // Scene resize, generation, view-specific camera and atlas gathering are
    // driven through the same bridge used by post treatment, not reset structs.
    X360PostView v={.source_view=0x009196C0u,.generation=8,.depth={3,5,31,23}};
    scene=tex(MTLPixelFormatRGBA8Unorm,31,23,0);
    ++frame_serial;s=bridge(scene,z,2,0,&v,9);
    CHECK(s->depth_normal.width==16 && s->depth_normal.height==12);
    CHECK(fabsf(s->params.proj[0]-.5f)<1e-6f);
    CHECK(fabsf(s->params.rect[0]-3.f/65)<1e-6f && fabsf(s->params.rect[1]-5.f/49)<1e-6f);
    CHECK(fabsf(s->params.rect[2]-31.f/65)<1e-6f && fabsf(s->params.rect[3]-23.f/49)<1e-6f);
    CHECK(fabsf(s->params.bounds[0]-3.5f/65)<1e-6f && fabsf(s->params.bounds[3]-27.5f/49)<1e-6f);
    CHECK(shared_depth_get(0,frame_serial)==NULL);epoch=s->epoch;held=s->depth_normal;
    for (unsigned change=0;change<3;++change) {
        if (change==0) ++v.generation;
        if (change==1) ++M32(0x009196C0u+0x40);
        if (change==2) { M32(0x30000)=0x34000;M32(0x3401C)=1; }
        ++frame_serial;s=bridge(scene,z,2,0,&v,9);
        CHECK(s->epoch==epoch+1);CHECK(s->depth_normal!=held);
        epoch=s->epoch;held=s->depth_normal;++bridge_cases;
    }
    ++frame_serial;ao_enabled=0;NoWork *trap=[NoWork new];cmd=(id)trap;
    render_shared_depth_pre_treatment(scene,z,2,0,NULL,&v,9);
    CHECK(trap.calls==0 && shared_depth_get(2,frame_serial)==NULL);
    cmd=nil;ao_enabled=1;
    shared_depth_request(2,frame_serial);ao_enabled=0;s=bridge(scene,z,2,0,&v,9);CHECK(s!=NULL);
    // Projection invalidation and mirror policy also pass through the bridge.
    ao_enabled=1;MF32(0x21040)=NAN;++frame_serial;cmd=(id)trap;
    render_shared_depth_pre_treatment(scene,z,2,0,NULL,&v,9);
    CHECK(shared_depth_get(2,frame_serial)==NULL && trap.calls==0);
    v.source_view=0x009197A0u;
    render_shared_depth_pre_treatment(scene,z,2,0,NULL,&v,9);
    CHECK(trap.calls==0);cmd=nil;
}

static unsigned input_nodes,input_ready;
static id<MTLTexture> needs_geometry(id<MTLTexture> scene,const PreTreatmentFrame *frame) {
    ++input_nodes;
    const SharedDepthSet *s=frame->geometry;
    CHECK(s && s->depth_normal && s->levels && s->minmax[0] && s->frame==frame_serial);
    if (s && s->depth_normal && s->levels && s->minmax[0] && s->frame==frame_serial) ++input_ready;
    return scene;
}
static void check_graph_inputs(void) {
    unsigned start=checks,failed=failures;
    live_inputs();ao_enabled=0;
    PreTreatmentGraph saved=x_pre_treatment;
    NoWork *trap=[NoWork new];mtl=(id)trap;cmd=(id)trap;
    x_pre_treatment.count=0;
    CHECK(x_pre_treatment_dispatch((id)trap,(id)trap,8,1,NULL,NULL,0)==(id)trap);
    CHECK(trap.calls==0); // empty: no allocation, encoder, texture binding or frame read
    x_pre_treatment=saved;
    ++frame_serial;
    CHECK(x_pre_treatment_dispatch((id)trap,nil,1,0,NULL,NULL,0)==(id)trap);
    CHECK(trap.calls==0 && shared_depth_get(0,frame_serial)==NULL);
    X360PostView mirror={.source_view=0x009197A0u};
    CHECK(x_pre_treatment_dispatch((id)trap,(id)trap,1,0,NULL,&mirror,0)==(id)trap);
    CHECK(trap.calls==0);
    mtl=device;cmd=nil;
    const X360PostParams p={.znear=.5f,.zfar=100};
    SharedDepthInput in=input(65,49,frame_serial);
    RenderTex depth={.tex=depth_texture(65,49,0,&in,NULL)};
    id<MTLTexture> scene=tex(MTLPixelFormatRGBA8Unorm,31,23,0);
    for (unsigned kind=0;kind<2;++kind) for (unsigned slot=0;slot<3;++slot) {
        x_pre_treatment=saved;
        PreTreatmentNode node={.name="spatial consumer",.id=1,.after=1,
            .inputs=POST_BIT(POST_SCENE)|POST_BIT(kind?POST_MINMAX:POST_NORMALS),
            .outputs=POST_BIT(POST_SCENE),.colour_in=POST_SCENE,.colour_out=POST_SCENE,
            .in_place=1,.encode=needs_geometry};
        CHECK(render_pre_treatment_register(&node));
        X360PostView v={.source_view=slot==2?0x009196C0u:0x00919650u,.depth={3,5,31,23}};
        ++frame_serial;cmd=[queue commandBuffer];
        CHECK(production_pre_treatment(scene,&depth,0,&p,slot?&v:NULL,0)==scene);
        complete(cmd);cmd=nil;
        const SharedDepthSet *s=shared_depth_get(slot,frame_serial);
        CHECK(s!=NULL);
        if (s) {
            float *normal=read_texture(s->depth_normal,4);
            CHECK(isfinite(normal[3]) && normal[3]>p.znear && normal[3]<p.zfar);free(normal);
        }
    }
    CHECK(input_nodes==6 && input_ready==6);
    printf("registered spatial inputs AO Off: nodes=%u ready=%u no_work_calls=%u checks=%u failures=%u\n",
        input_nodes,input_ready,trap.calls,checks-start,failures-failed);
    x_pre_treatment=saved;ao_enabled=1;
}

static void contact_pipelines(void) {
    NSString *src=[NSString stringWithFormat:@"%s%s",x360_src,current_ao_msl];NSError *error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:src options:nil error:&error];
    CHECK(lib!=nil);if (!lib) { fprintf(stderr,"shipping AO: %s\n",error.localizedDescription.UTF8String);exit(1); }
    contact_pso.kernel=x_pso(lib,@"fs_ao",MTLPixelFormatRG16Float,0);
    contact_pso.blur=x_pso(lib,@"fs_ao_blur",MTLPixelFormatRG16Float,0);
    contact_pso.apply=x_pso(lib,@"fs_ao_apply",MTLPixelFormatRGBA8Unorm,0);
    contact_pso.multiply_rgba=x_contact_multiply(lib,MTLPixelFormatRGBA8Unorm);
    contact_pso.multiply_bgra=x_contact_multiply(lib,MTLPixelFormatBGRA8Unorm);
    contact_pso.multiply_float=x_contact_multiply(lib,MTLPixelFormatRGBA16Float);
    CHECK(contact_pso.kernel && contact_pso.blur && contact_pso.apply && contact_pso.multiply_rgba && contact_pso.multiply_bgra && contact_pso.multiply_float);
}
static id<MTLTexture> ms_texture(MTLPixelFormat f,unsigned w,unsigned h,unsigned samples) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
    d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;
    d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget;
    id<MTLTexture> t=[device newTextureWithDescriptor:d];CHECK(t!=nil);return t;
}
static void seed_msaa(RenderTex *colour,RenderTex *depth,id<MTLTexture> raw,int hdr,unsigned samples) {
    // All samples are shaded individually. Sample 0 is the analytic fixture;
    // others differ, proving the default production sample-0 depth resolve.
    const char *seed="struct Seed {float z [[depth(any)]];};fragment Seed seed(VOut i [[stage_in]],uint s [[sample_id]],depth2d<float> z [[texture(0)]]) {return {max(0.0,z.read(uint2(i.pos.xy))-float(s)*0.004)};}";
    NSError *error=nil;id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithFormat:@"%s%s",x360_src,seed] options:nil error:&error];
    CHECK(lib!=nil);if (!lib) { fprintf(stderr,"MSAA seed: %s\n",error.localizedDescription.UTF8String);exit(1); }
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[lib newFunctionWithName:@"vs_full"];d.fragmentFunction=[lib newFunctionWithName:@"seed"];
    d.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;d.rasterSampleCount=samples;
    d.colorAttachments[0].pixelFormat=colour->tex.pixelFormat;
    id<MTLRenderPipelineState> p=[device newRenderPipelineStateWithDescriptor:d error:&error];CHECK(p!=nil);
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionAlways;dd.depthWriteEnabled=YES;
    // Poison the single-sample destination so a skipped resolve fails numerically.
    cmd=[queue commandBuffer];MTLRenderPassDescriptor *poison=[MTLRenderPassDescriptor renderPassDescriptor];
    poison.depthAttachment.texture=depth->tex;poison.depthAttachment.loadAction=MTLLoadActionClear;
    poison.depthAttachment.clearDepth=.25;poison.depthAttachment.storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:poison];[e endEncoding];
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.depthAttachment.texture=depth->ms?:depth->tex;rp.depthAttachment.loadAction=MTLLoadActionClear;rp.depthAttachment.storeAction=MTLStoreActionStore;
    rp.colorAttachments[0].texture=colour->ms?:colour->tex;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    rp.colorAttachments[0].clearColor=MTLClearColorMake(hdr?2.5:.7,.4,.2,.37);
    e=[cmd renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:p];
    [e setDepthStencilState:[device newDepthStencilStateWithDescriptor:dd]];
    [e setFragmentTexture:raw atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    colour->stale=depth->stale=1;
    uint64_t before=stat_resolves;
    msaa_resolve(colour);msaa_resolve(depth);
    CHECK(stat_resolves==before+(samples>1?2:0));
    if (samples>1) CHECK(!depth->stale && !colour->stale);
    complete(cmd);cmd=nil;
}
static id<MTLTexture> crop_scene(id<MTLTexture> input,unsigned y,unsigned height) {
    id<MTLTexture> out=tex(input.pixelFormat,(unsigned)input.width,height,0);
    id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];
    [b copyFromTexture:input sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,y,0)
        sourceSize:MTLSizeMake(input.width,height,1) toTexture:out destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
    [b endEncoding];complete(cb);return out;
}
static void check_graph(id<MTLTexture> original,RenderTex *depth,const X360PostParams *p,
    const X360PostView *view,unsigned hdr) {
    CHECK(pre_treatment_validate(&x_pre_treatment));CHECK(x_pre_treatment.count==1);
    unsigned w=(unsigned)original.width,h=(unsigned)original.height;
    for (unsigned smoke=0;smoke<3;++smoke) {
        id<MTLTexture> direct=crop_scene(original,0,h),through=crop_scene(original,0,h);
        if (smoke) {
            unsigned sw=smoke==2?(w+1)/2:w,sh=smoke==2?(h+1)/2:h;
            active_smoke=tex(MTLPixelFormatRGBA16Float,sw,sh,1);
            __fp16 *rgba=malloc((size_t)sw*sh*8);
            for (unsigned i=0;i<sw*sh;++i) {
                rgba[i*4]=.1f;rgba[i*4+1]=.05f;rgba[i*4+2]=.2f;rgba[i*4+3]=(i%7)/8.f;
            }
            [active_smoke replaceRegion:MTLRegionMake2D(0,0,sw,sh) mipmapLevel:0 withBytes:rgba bytesPerRow:sw*8];free(rgba);
            if (smoke==2) {
                active_smoke_depth=tex(MTLPixelFormatDepth32Float,sw,sh,1);float *z=malloc((size_t)sw*sh*4);
                for (unsigned i=0;i<sw*sh;++i) z[i]=(i%3)?.95f:.3f;
                [active_smoke_depth replaceRegion:MTLRegionMake2D(0,0,sw,sh) mipmapLevel:0 withBytes:z bytesPerRow:sw*4];free(z);
            }
        }
        smoke_applied=smoke_finished=0;
        // Baseline is the pinned pre-graph main encoder and call order. Each
        // path gets a fresh frame so dispatch must prepare its own geometry.
        ++frame_serial;cmd=[queue commandBuffer];
        render_shared_depth_pre_treatment(direct,depth->tex,depth->ms?(unsigned)depth->ms.sampleCount:1,hdr,p,view,0);
        CHECK(x_contact_scene_impl(direct,depth->tex,p,view)==direct);CHECK(xr->ao_ready);
        if (!view) render_soft_smoke_late_finish(direct);
        complete(cmd);cmd=nil;
        ++frame_serial;cmd=[queue commandBuffer];
        CHECK(production_pre_treatment(through,depth,hdr,p,view,0)==through);CHECK(xr->ao_ready);
        complete(cmd);cmd=nil;
        CHECK(smoke_applied==(!view&&smoke?2u:0));CHECK(smoke_finished==(view?0u:2u));
        float *a=read_texture(direct,4),*b=read_texture(through,4);unsigned differences=0;
        // Readback expands finite stored half/UNORM codes losslessly. memcmp also
        // checks exact alpha and signed zero, with no HDR code-step tolerance.
        for (unsigned i=0;i<w*h*4;++i) differences+=memcmp(a+i,b+i,sizeof(float))!=0;
        CHECK(differences==0);graph_mismatches+=differences;++graph_cases;free(a);free(b);
        if (view) ++graph_split_cases;else ++graph_sp_cases;
        active_smoke=active_smoke_depth=nil;
    }
}
static void check_production(AOPipelines reference) {
    live_inputs();contact_pipelines();
    enum { w=257,h=193,hw=(w+1)/2,hh=(h+1)/2 };
    for (unsigned samples=1;samples<=8;samples*=2) {
        CHECK([device supportsTextureSampleCount:samples]);
        for (unsigned fmt=0;fmt<3;++fmt) for (unsigned slot=0;slot<3;++slot) {
            xr=&resources[slot];
            int hdr=fmt==2;MTLPixelFormat f=hdr?MTLPixelFormatRGBA16Float:fmt?MTLPixelFormatBGRA8Unorm:MTLPixelFormatRGBA8Unorm;
            SharedDepthInput in=input(w,h,++frame_serial);float *raw=NULL;
            if (slot==2) in.params.proj[0]=in.params.proj[1]=.5f;
            unsigned atlas_h=slot?2*h+3:h,offset=slot==2?h+3:0;
            id<MTLTexture> upload=depth_texture(w,atlas_h,3,&in,&raw);
            for (unsigned y=0;y<atlas_h;++y) for (unsigned x=0;x<w;++x)
                raw[y*w+x]=y>=offset && y<offset+h?depth_at(x,y-offset,w,h,2,&in.params):.25f;
            [upload replaceRegion:MTLRegionMake2D(0,0,w,atlas_h) mipmapLevel:0 withBytes:raw bytesPerRow:w*4];
            RenderTex colour={.tex=tex(f,w,atlas_h,0)};
            RenderTex depth={.tex=tex(MTLPixelFormatDepth32Float,w,atlas_h,0)};
            if (samples>1) { colour.ms=ms_texture(f,w,atlas_h,samples);depth.ms=ms_texture(MTLPixelFormatDepth32Float,w,atlas_h,samples); }
            seed_msaa(&colour,&depth,upload,hdr,samples);
            float *resolved=read_texture(depth.tex,1);
            for (unsigned i=0;i<w*atlas_h;++i) CHECK(isfinite(resolved[i]) && fabsf(resolved[i]-raw[i])<1e-6f);
            free(resolved);free(raw);
            id<MTLTexture> scene=slot?crop_scene(colour.tex,offset,h):colour.tex;
            X360PostView v={.source_view=slot==2?0x009196C0u:0x00919650u,.generation=1,
                .depth={0,offset,w,h}};
            const X360PostView *view=slot?&v:NULL;
            const X360PostParams p={.znear=.5f,.zfar=100};
            cmd=[queue commandBuffer];render_shared_depth_pre_treatment(scene,depth.tex,samples,hdr,&p,view,0);
            complete(cmd);cmd=nil;
            const SharedDepthSet *s=shared_depth_get(slot,frame_serial);CHECK(s!=NULL);if (!s) continue;
            coverage=tex(MTLPixelFormatR8Unorm,hw,hh,1);uint8_t mask[hw*hh];
            for (unsigned y=0;y<hh;++y) for (unsigned x=0;x<hw;++x) mask[y*hw+x]=x<3 || (x==hw/2 && y>hh/2)?0:255;
            [coverage replaceRegion:MTLRegionMake2D(0,0,hw,hh) mipmapLevel:0 withBytes:mask bytesPerRow:hw];
            AOResult expected=ao_render(reference,s,depth.tex,coverage,w,h,hdr,scene);
            check_graph(scene,&depth,&p,view,hdr);
            float *before=read_texture(scene,4);
            cmd=[queue commandBuffer];
            CHECK(x_contact_scene_impl(scene,depth.tex,&p,view)==scene);
            complete(cmd);cmd=nil;CHECK(xr->ao_ready);
            compare_texture(xr->ao[0],expected.blurred,2);compare_stored_colour(scene,expected.colour);
            float *after=read_texture(scene,4);unsigned darkened=0;
            for (unsigned i=0;i<w*h;++i) {
                CHECK(after[i*4+3]==before[i*4+3]); // multiply must preserve alpha
                darkened+=after[i*4]<before[i*4]-1e-4f;
                if (i<w*3) CHECK(after[i*4]==before[i*4]); // sky
            }
            CHECK(darkened>20);if (hdr) CHECK(after[0]>1);
            printf("shipping x%u %s slot=%u: darkened=%u\n",samples,hdr?"HDR":fmt?"BGRA":"RGBA",slot,darkened);
            free(before);free(after);++production_cases;
        }
    }
}
static void check_production_coverage(void) {
    CHECK(production_cases==36); // SP/P0/P1, x1/x2/x4/x8, RGBA/BGRA/HDR
    if (production_cases==36) CHECK(graph_cases==108 && graph_sp_cases==36 && graph_split_cases==72 && graph_mismatches==0);
    printf("pre-treatment AO/smoke parity: cases=%u SP=%u split=%u expanded_code_mismatches=%u\n",graph_cases,graph_sp_cases,graph_split_cases,graph_mismatches);
    CHECK(bridge_cases==8); // live camera/MSAA/HDR/FOV/mode/generation/split inputs
}
int main(int argc,char **argv) {
    @autoreleasepool {
        device=MTLCreateSystemDefaultDevice();if (!device) { puts("shared_depth: no Metal device");return 3; }
        queue=[device newCommandQueue];mtl=device;
        if (argc==2 && !strcmp(argv[1],"--graph-inputs")) {
            check_graph_inputs();
            printf("shared_depth graph inputs: checks=%u failures=%u\n",checks,failures);
            return failures?1:0;
        }
        if (argc==2 && (!strcmp(argv[1],"--bridge") || !strcmp(argv[1],"--player"))) {
            check_bridge(!strcmp(argv[1],"--player"));
            printf("shared_depth bridge: checks=%u failures=%u bridge=%u player=%u\n",checks,failures,bridge_cases,player_cases);
            return failures?1:0;
        }
        if (argc==2 && !strcmp(argv[1],"--lifetime")) {
            SharedDepthInput in=input(65,49,10);
            id<MTLTexture> z=depth_texture(in.width,in.height,3,&in,NULL);
            check_lifetime(z,in);
            printf("shared_depth lifetime: checks=%u failures=%u\n",checks,failures);
            return failures?1:0;
        }
        AOPipelines current=ao_pipelines(current_ao_msl),reference=ao_pipelines(reference_ao_msl);
        if (argc==2 && !strcmp(argv[1],"--production")) {
            check_production(reference);
            printf("shared_depth shipping: checks=%u failures=%u cases=%u AO=%u max=%.9g colour=%.9g hdr_rounding=%u\n",checks,failures,production_cases,ao_samples,ao_error,colour_error,hdr_rounding_samples);
            return failures?1:0;
        }
        SharedDepthInput in=input(65,49,1);id<MTLTexture> z=nil;
        check_off(nil,&in);
        for (int shape=0;shape<4;++shape) {
            in=input(shape==1?513:65,shape==1?385:49,in.frame+1);
            float *raw=NULL;z=depth_texture(in.width,in.height,shape,&in,&raw);
            const SharedDepthSet *set=prepare(0,z,&in,1);
            if (shape<2) check_normals(set,&in,raw,shape);
            if (shape==3) {
                // Initialize all reductions before the second frame so a skipped
                // level deterministically retains a known, wrong sentinel.
                id<MTLCommandBuffer> cb=[queue commandBuffer];
                for (unsigned l=1;l<set->levels;++l) {
                    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
                    rp.colorAttachments[0].texture=set->minmax[l];rp.colorAttachments[0].loadAction=MTLLoadActionClear;
                    rp.colorAttachments[0].storeAction=MTLStoreActionStore;rp.colorAttachments[0].clearColor=MTLClearColorMake(-7,-9,0,0);
                    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e endEncoding];
                }
                complete(cb);++in.frame;set=prepare(0,z,&in,1);check_pyramid(set,&in,raw);
            }
            if (shape<3) check_ao(current,reference,set,&in,z);
            free(raw);
        }
        CHECK(normal_samples>500);CHECK(pyramid_samples>1000);CHECK(ao_samples>50000);CHECK(ao_peak>.01f);
        ++in.frame;check_lifetime(z,in);check_cut();check_split();check_bridge(0);check_graph_inputs();check_production(reference);
        check_production_coverage();CHECK(player_cases==1);
        printf("shared_depth: checks=%u failures=%u normals=%u plane=%.9g sphere=%.9g pyramid=%u rel=%.9g AO=%u max=%.9g colour=%.9g peak=%.9g hdr_rounding=%u\n",
            checks,failures,normal_samples,plane_error,sphere_error,pyramid_samples,pyramid_error,ao_samples,ao_error,colour_error,ao_peak,hdr_rounding_samples);
        return failures?1:0;
    }
}
