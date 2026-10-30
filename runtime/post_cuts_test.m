// Held production curve + treatment inputs; max stored code delta <=1,
// exact final alpha, 1x/2x samples, and a deliberately wrong equal-weight kernel.
// Observed final max codes RGBA8/16F: 0/1; broken weights: 68/542 (exit 1).
// Curve max: 0/1; broken 176/2048. AO: 13,824 identical On/Off bytes.
// Production entry: 6 same-frame depth preparations / 0 failures / exit 0.
// Serial-cache control On and Off: 1 preparation / 5 failures / exit 1;
// the prior direct-preparation harness passed that mutation (exit 0).
// Split/partial: all channels identical; nonempty reuse/proof retain stores.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "post_cuts.h"
#include "shader.inc"
#include "load.inc"
#include "destination.inc"
static id<MTLDevice> device;
static id<MTLCommandQueue> queue;
static id<MTLSamplerState> sampler;
static int failures;
#define CHECK(x) do{if(!(x)){++failures;fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);}}while(0)
static id<MTLLibrary> library(NSString *s) {
    NSError *error=nil;id<MTLLibrary> l=[device newLibraryWithSource:s options:nil error:&error];
    if(!l){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);exit(2);}return l;
}
static id<MTLRenderPipelineState> pipeline(id<MTLLibrary> l,NSString *vs,NSString *fs,MTLPixelFormat f,unsigned samples) {
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[l newFunctionWithName:vs];d.fragmentFunction=[l newFunctionWithName:fs];d.colorAttachments[0].pixelFormat=f;d.rasterSampleCount=samples;
    NSError *error=nil;id<MTLRenderPipelineState> p=[device newRenderPipelineStateWithDescriptor:d error:&error];
    if(!p){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);exit(2);}return p;
}
static id<MTLTexture> texture(MTLPixelFormat f,unsigned w,unsigned h,unsigned samples) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
    d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    d.storageMode=samples>1?MTLStorageModePrivate:MTLStorageModeShared;
    if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
    return [device newTextureWithDescriptor:d];
}
static unsigned bytes(MTLPixelFormat f){return f==MTLPixelFormatRGBA16Float?8:4;}
static void fill(id<MTLTexture> t,int kind) {
    unsigned b=bytes(t.pixelFormat),w=(unsigned)t.width,h=(unsigned)t.height;
    void *data=malloc(w*h*b);
    for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)for(unsigned c=0;c<4;c++) {
        // Gradients, isolated bright pixels, checkerboard, deterministic noise,
        // borders and saturated HDR values in the same held input.
        uint32_t hash=(x*1973u+y*9277u+c*26699u)^0xabcdefu;hash^=hash>>16;hash*=0x27d4eb2du;hash^=hash>>15;
        float v=(hash&1023)/1023.f;
        switch((x/64)%5){case 0:v=(float)y/(h-1);break;case 1:v=((x+y)&1)?1:0;break;case 2:v=(x%31==0&&y%29==0)?1:0;break;default:break;}
        if(!x||!y||x==w-1||y==h-1)v=1;
        if(kind==1 && c<3)v*=t.pixelFormat==MTLPixelFormatRGBA16Float?3.5f:.8f;
        if(kind==2)v=c==0?.42f:c==1?0:c==2?1:.2f; // vignette
        if(kind==3)v=0; // no bloom
        unsigned i=(y*w+x)*4+c;
        if(b==8)((__fp16*)data)[i]=v;else ((uint8_t*)data)[i]=(uint8_t)lroundf(v*255);
    }
    [t replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:data bytesPerRow:w*b];free(data);
}
static void finish(id<MTLCommandBuffer> cb){[cb commit];[cb waitUntilCompleted];if(cb.status==MTLCommandBufferStatusError){fprintf(stderr,"%s\n",cb.error.localizedDescription.UTF8String);exit(2);}}
static id<MTLTexture> curve(id<MTLLibrary> l,id<MTLTexture> src) {
    id<MTLTexture> dst=texture(src.pixelFormat,320,180,1);
    id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=dst;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
    [e setRenderPipelineState:pipeline(l,@"vs_full",@"fs_tdown",src.pixelFormat,1)];
    float k[132]={0};k[128]=4;k[129]=4;k[130]=1.f/960;k[131]=1.f/540;
    [e setFragmentTexture:src atIndex:0];[e setFragmentSamplerState:sampler atIndex:0];[e setFragmentBytes:k length:sizeof k atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];finish(cb);return dst;
}
typedef struct {float coeffs[4][4],b[4],blur[4],dof[4],misc[4],zr[4],tp[4],rx[4];} VT;
static id<MTLTexture> treatment(id<MTLLibrary> l,id<MTLTexture> scene,id<MTLTexture> tone,id<MTLTexture> vig,id<MTLTexture> zero,id<MTLTexture> depth,unsigned samples,int on) {
    MTLPixelFormat f=scene.pixelFormat;id<MTLTexture> dst=texture(f,3840,2160,1),ms=samples>1?texture(f,3840,2160,samples):nil;
    fill(dst,3);
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    // Seed all MS samples too: the Off load has a valid, unrelated prior target.
    if(ms){MTLRenderPassDescriptor *seed=[MTLRenderPassDescriptor renderPassDescriptor];seed.colorAttachments[0].texture=ms;seed.colorAttachments[0].loadAction=MTLLoadActionClear;seed.colorAttachments[0].clearColor=MTLClearColorMake(.19,.37,.61,.83);seed.colorAttachments[0].storeAction=MTLStoreActionStore;[[cb renderCommandEncoderWithDescriptor:seed] endEncoding];}
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=ms?:dst;rp.colorAttachments[0].loadAction=on?final_load(0,0,0,3840,2160,ms?:dst):MTLLoadActionLoad;rp.colorAttachments[0].storeAction=ms?MTLStoreActionMultisampleResolve:MTLStoreActionStore;if(ms)rp.colorAttachments[0].resolveTexture=dst;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pipeline(l,@"vs_full",@"fs_vt",f,samples)];
    VT k={0};k.coeffs[0][0]=.72f;k.coeffs[1][0]=.25f;k.coeffs[2][0]=.10f;k.coeffs[3][0]=.03f;k.misc[0]=.87f;k.misc[1]=.1f;k.misc[2]=1.13f;k.misc[3]=1;k.dof[3]=21;k.zr[0]=.5;k.zr[1]=1000;k.tp[3]=4;
    [e setFragmentBytes:&k length:sizeof k atIndex:0];
    [e setFragmentTexture:scene atIndex:0];[e setFragmentTexture:tone atIndex:1];[e setFragmentTexture:vig atIndex:2];[e setFragmentTexture:zero atIndex:3];[e setFragmentTexture:zero atIndex:4];[e setFragmentTexture:depth atIndex:5];[e setFragmentTexture:zero atIndex:6];
    [e setFragmentSamplerState:sampler atIndex:0];[e setFragmentSamplerState:sampler atIndex:1];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];finish(cb);return dst;
}
static unsigned compare(id<MTLTexture> a,id<MTLTexture> b,const char *label,int alpha_exact) {
    unsigned w=(unsigned)a.width,h=(unsigned)a.height,bp=bytes(a.pixelFormat),max=0,alpha=0;void *av=malloc(w*h*bp),*bv=malloc(w*h*bp);
    [a getBytes:av bytesPerRow:w*bp fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];[b getBytes:bv bytesPerRow:w*bp fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
    for(unsigned i=0;i<w*h*4;i++){int x=bp==8?((uint16_t*)av)[i]:((uint8_t*)av)[i],y=bp==8?((uint16_t*)bv)[i]:((uint8_t*)bv)[i];unsigned d=(unsigned)abs(x-y);if(d>max)max=d;if(i%4==3&&d)++alpha;}
    printf("%s max_code=%u alpha_differences=%u pixels=%u\n",label,max,alpha,w*h);CHECK(max<=1);if(alpha_exact)CHECK(alpha==0);free(av);free(bv);return max;
}
// Held split/partial target: preserve pixels outside the submitted rectangle.
static void bounded_view(id<MTLLibrary> lib,MTLPixelFormat format,int split) {
    id<MTLTexture> src=texture(format,64,48,1);fill(src,0);
    id<MTLTexture> outputs[2];
    for(unsigned on=0;on<2;++on){
        id<MTLTexture> dst=outputs[on]=texture(format,96,64,1);fill(dst,1);
        id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=dst;
        rp.colorAttachments[0].loadAction=on?final_load(split,0,0,64,48,dst):MTLLoadActionLoad;
        CHECK(rp.colorAttachments[0].loadAction==MTLLoadActionLoad);
        rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:pipeline(lib,@"vs_full",@"fs_tdown",format,1)];
        uint32_t rect[4]={16,8,64,48};x_destination(e,rect);
        float k[132]={0};k[128]=4;k[129]=4;k[130]=1.f/64;k[131]=1.f/48;
        [e setFragmentTexture:src atIndex:0];[e setFragmentSamplerState:sampler atIndex:0];[e setFragmentBytes:k length:sizeof k atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];finish(cb);
    }
    compare(outputs[0],outputs[1],split?"split target":"partial target",1);
}
// Execute the production AO descriptor and depth-preparation functions.
static id<MTLDevice> mtl;
static id<MTLCommandBuffer> cmd;
static id<MTLRenderCommandEncoder> enc;
typedef struct {id<MTLTexture> tex,ms;} RenderTex;
static RenderTex ssao_mask,ssao_reference_mask,ssao_cpu_reference_mask;
static id<MTLTexture> ssao_half_depth,ssao_depth;
static int ssao_reference_record,ssao_cpu_reference_record,proof_mode;
static uint64_t ssao_half_serial,ssao_cleared,ssao_serial=1,frame_serial=1;
static unsigned pending_ops,enc_samples,observed_depth_cuts,observed_ms_cuts;
static id ssao_rec,ssao_reference_rec,ssao_cpu_reference_rec;
static int ssao_cpu_diag;
static uint64_t ssao_diag_replay_ns;
static struct {int unused;} ssao_eb,ssao_reference_eb,ssao_cpu_reference_eb;
static uint64_t host_ticks(void){CHECK(0);return 0;}
static int x360_post_ssao_enabled(void){return 1;}
static unsigned depth_prepares,replay_phase,replayed;
static id<MTLRenderPipelineState> coverage_pipeline;
static id<MTLDepthStencilState> coverage_depth_state;
static void observe_depth_prepare(void){++depth_prepares;}
// Fixture recorder: submit held geometry through the production replay hook.
static void rt_render_list_replay(id rec,id<MTLRenderCommandEncoder> e){
    (void)rec;
    if(pending_ops){
        [e setRenderPipelineState:coverage_pipeline];[e setDepthStencilState:coverage_depth_state];
        [e setFragmentBytes:&replay_phase length:sizeof replay_phase atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];++replayed;
    }
    pending_ops=0;
}
static void lamprec_replay(id rec,id<MTLRenderCommandEncoder> e){
    (void)rec;[e setRenderPipelineState:coverage_pipeline];[e setDepthStencilState:coverage_depth_state];
    [e setFragmentBytes:&replay_phase length:sizeof replay_phase atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
}
static int gpt_on,pass_dirty;
static struct {int unused;} eb;
static int ssao_proof(void){return proof_mode;}
static unsigned rt_render_list_ops(id x){(void)x;return pending_ops;}
static RenderTex *ssao_target(void){return ssao_cpu_reference_record?&ssao_cpu_reference_mask:ssao_reference_record?&ssao_reference_mask:&ssao_mask;}
static void end_pass(void){if(enc){[enc endEncoding];enc=nil;}}
static void ensure_cmd(void){}
#define gpt_render_auto(...) ((void)0)
#define gpt_name(...) ((void)0)
static id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d,const char *name,NSError **error){(void)name;return [mtl newRenderPipelineStateWithDescriptor:d error:error];}
static void observe_coverage(MTLRenderPassDescriptor *rp,int allow_cuts){
    unsigned cuts=allow_cuts?post_cuts_ao(proof_mode,ssao_target()==&ssao_mask,ssao_cleared==frame_serial,pending_ops==0):0;
    CHECK(rp.depthAttachment.storeAction==((cuts&POST_CUTS_AO_DEPTH)?MTLStoreActionDontCare:MTLStoreActionStore));
    CHECK(rp.colorAttachments[0].storeAction==((cuts&POST_CUTS_AO_MS)?MTLStoreActionMultisampleResolve:MTLStoreActionStoreAndMultisampleResolve));
    observed_depth_cuts+=rp.depthAttachment.storeAction==MTLStoreActionDontCare;
    observed_ms_cuts+=rp.colorAttachments[0].storeAction==MTLStoreActionMultisampleResolve;
}
#include "coverage.inc"
static int coverage_proof(const char *path){
    mtl=device;ssao_mask.tex=texture(MTLPixelFormatR8Unorm,64,36,1);ssao_mask.ms=texture(MTLPixelFormatR8Unorm,64,36,4);
    ssao_half_depth=texture(MTLPixelFormatDepth16Unorm,64,36,4);
    ssao_cpu_reference_mask.tex=texture(MTLPixelFormatR8Unorm,64,36,1);
    ssao_cpu_reference_mask.ms=texture(MTLPixelFormatR8Unorm,64,36,4);
    id<MTLTexture> z=texture(MTLPixelFormatDepth32Float,128,72,1);
    NSString *s=@"#include <metal_stdlib>\nusing namespace metal;\n"
    "vertex float4 v(uint v [[vertex_id]]) {float2 p=float2((v<<1)&2,v&2);return float4(p*float2(2,-2)+float2(-1,1),0,1);}\n"
    "struct O {float r [[color(0)]];float z [[depth(any)]];};\n"
    "fragment O f(float4 p [[position]],uint s [[sample_id]],constant uint &phase [[buffer(0)]]) {if((uint(p.x)+3*uint(p.y)+s+phase)%5<2)discard_fragment();return {0,0.3+0.12*s};}\n";
    id<MTLLibrary> l=library(s);MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[l newFunctionWithName:@"v"];pd.fragmentFunction=[l newFunctionWithName:@"f"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatR8Unorm;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth16Unorm;pd.rasterSampleCount=4;
    pd.colorAttachments[0].blendingEnabled=YES;pd.colorAttachments[0].sourceRGBBlendFactor=pd.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorZero;
    NSError *err=nil;id<MTLRenderPipelineState> p=[device newRenderPipelineStateWithDescriptor:pd error:&err];CHECK(p!=nil);
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLessEqual;dd.depthWriteEnabled=NO;id<MTLDepthStencilState> ds=[device newDepthStencilStateWithDescriptor:dd];
    coverage_pipeline=p;coverage_depth_state=ds;ssao_depth=z;
    FILE *out=fopen(path,"wb");CHECK(out!=NULL);
    // Empty -> incremental draws -> further incremental draws, each against
    // changed depth: a discarded nonempty mask cannot be rebuilt from new depth.
    for(unsigned phase=0;phase<6;++phase){
        proof_mode=phase==4;
        ssao_cpu_reference_rec=phase==3?@1:nil;
        if(phase==3)proof_mode=1;
        pending_ops=phase!=0;
        float depth[128*72];for(unsigned i=0;i<128*72;++i)depth[i]=.25f+((i+phase)%7)*.1f;
        [z replaceRegion:MTLRegionMake2D(0,0,128,72) mipmapLevel:0 withBytes:depth bytesPerRow:128*4];
        if(phase==5)ssao_cleared=0; // split/partial calls cannot enable cuts, even on first use
        cmd=[queue commandBuffer];replay_phase=phase;
        CHECK(render_ssao_coverage(z,phase<5)==ssao_mask.tex);
        CHECK(depth_prepares==phase+1); // Includes the second same-frame request.
        CHECK(pending_ops==0);CHECK(frame_serial==1);finish(cmd);
        uint8_t values[64*36];[ssao_target()->tex getBytes:values bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,64,36) mipmapLevel:0];
        if(phase==0)for(unsigned i=0;i<sizeof values;++i)CHECK(values[i]==255);
        fwrite(values,1,sizeof values,out);
    }
    fclose(out);CHECK(replayed==5);CHECK(observed_depth_cuts==(post_cuts_enabled()?2:0));CHECK(observed_ms_cuts==(post_cuts_enabled()?1:0));
    printf("AO coverage: %u bytes, depth_cuts=%u ms_cuts=%u depth_prepares=%u calls=6 failures=%d\n",6*64*36,observed_depth_cuts,observed_ms_cuts,depth_prepares,failures);return failures?1:0;
}

int main(int argc,char **argv){@autoreleasepool{
    if(argc>1&&!strcmp(argv[1],"--off")) {
        CHECK(!post_cuts_enabled());CHECK(!post_cuts_curve(3840,2160,0));CHECK(!post_cuts_full_target(0,0,0,3840,2160,3840,2160));return failures?1:0;
    }
    int broken=argc>1&&!strcmp(argv[1],"--broken");
    // Eligibility checks execute the production functions, including Off/invalid sizes.
    if(argc<=1 || strcmp(argv[1],"--coverage"))setenv("NFS_POST_CUTS","1",1);
    if(argc<=1 || strcmp(argv[1],"--coverage")){
    CHECK(post_cuts_full_target(0,0,0,3840,2160,3840,2160));
    CHECK(!post_cuts_full_target(1,0,0,3840,2160,3840,2160));CHECK(!post_cuts_full_target(0,0,0,1920,1080,3840,2160));CHECK(!post_cuts_full_target(0,1,0,3840,2160,3840,2160));CHECK(!post_cuts_full_target(0,0,1,3840,2160,3840,2160));
    CHECK(post_cuts_curve(3840,2160,0));CHECK(!post_cuts_curve(3840,2160,1));CHECK(!post_cuts_curve(2560,1440,0));CHECK(!post_cuts_curve(3840,2159,0));
    }
    device=MTLCreateSystemDefaultDevice();if(!device){puts("SKIP: native Metal device unavailable");return 3;}queue=[device newCommandQueue];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;sampler=[device newSamplerStateWithDescriptor:sd];
    if(argc>2&&!strcmp(argv[1],"--coverage"))return coverage_proof(argv[2]);
    NSString *cut=[NSString stringWithUTF8String:post_cuts_curve_source()];if(broken)cut=[cut stringByReplacingOccurrencesOfString:@"w[5]={2,2,2,2,1}" withString:@"w[5]={1,1,1,1,1}"];
    id<MTLLibrary> cuts=library(cut);
    for(unsigned hdr=0;hdr<2;hdr++){@autoreleasepool{
        MTLPixelFormat f=hdr?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm;
        id<MTLLibrary> legacy=library(hdr?x_edr_source([NSString stringWithUTF8String:x360_src]):[NSString stringWithUTF8String:x360_src]);
        bounded_view(legacy,f,0);bounded_view(legacy,f,1);
        id<MTLTexture> src=texture(f,960,540,1);fill(src,0);id<MTLTexture> a=curve(legacy,src),b=curve(cuts,src);compare(a,b,hdr?"RGBA16F curve":"RGBA8 curve",0);
        id<MTLTexture> scene=texture(f,3840,2160,1),vig=texture(f,8,8,1),zero=texture(f,8,8,1),depth=texture(MTLPixelFormatDepth32Float,8,8,1);fill(scene,1);fill(vig,2);fill(zero,3);float zs[64];for(int i=0;i<64;i++)zs[i]=.5;[depth replaceRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0 withBytes:zs bytesPerRow:32];
        for(unsigned samples=1;samples<=2;samples++){@autoreleasepool{
            id<MTLTexture> off=treatment(legacy,scene,a,vig,zero,depth,samples,0),on=treatment(legacy,scene,b,vig,zero,depth,samples,1);
            char label[80];snprintf(label,sizeof label,"%s final %ux",hdr?"RGBA16F":"RGBA8",samples);compare(off,on,label,1);
        }}
    }}
    printf("post cuts failures=%d\n",failures);return failures?1:0;
}}
