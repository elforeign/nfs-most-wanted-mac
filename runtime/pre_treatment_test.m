// Graph validation, empty path and an offline 4K GPU bandwidth fixture.
// Four serial scene+depth stages vs the same four operations fused in registers.
// RGBA16F quantization is retained after EVERY stage in both paths; all final
// pixels must match byte for byte. Timings exclude initialization/readback/compile.
// Each sample is one command buffer, GPU start/end timestamps, alternating A/B;
                                                                         
// separate 1.808083 ms, fused .535375 ms (1.272708 saved, fuse eligible).
// Graph: 39 checks / 0 failures; wrong order: 27 / 1; GPU: 245 / 0,
// 33,177,600 stored half codes compared, 0 unequal. No runtime fusion installed.
// Empty graph allocations/encodes/bindings: 0/0/0; broken path: 2/2/2,
// with 39 checks / 3 failures vs 39 / 0.
#import <Foundation/Foundation.h>
#include "pre_treatment.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
static unsigned checks,failures,calls;
static id<MTLTexture> fake[5];
#define CHECK(x) do { ++checks;if(!(x)){++failures;if(failures<10)fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);} }while(0)
@interface PostTrap : NSObject
@property unsigned allocations,encodes,bindings;
@end
@implementation PostTrap
- (id)newTextureWithDescriptor:(id)d { (void)d;++_allocations;return nil; }
- (id)renderCommandEncoderWithDescriptor:(id)d { (void)d;++_encodes;return nil; }
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { (void)t;(void)i;++_bindings; }
@end
static id<MTLTexture> record(id<MTLTexture> scene,const PreTreatmentFrame *frame) {
    CHECK(frame->user==(void *)0x1234);CHECK(scene==fake[calls?calls-1:0]);
    ++calls;return fake[calls-1];
}
static void graph_checks(int wrong_order) {
    for (int i=0;i<5;++i) fake[i]=(id<MTLTexture>)[NSObject new];
    PreTreatmentGraph graph={.external_inputs=POST_SPATIAL_INPUTS|POST_BIT(POST_HISTORY_COLOUR)|POST_BIT(POST_HISTORY_DEPTH)};
    CHECK(pre_treatment_validate(&graph));
    PostTrap *trap=[PostTrap new];
    // No frame, geometry, device or command buffer exists. A second empty graph
    // has poisoned metadata and a callback that would record any node execution.
    // Empty dispatch reads only count, returns identical texture, calls=0. There
    // can be no allocation/encoder/binding: no Metal-capable object is supplied.
    CHECK(pre_treatment_run(&graph,(id)trap,NULL)==(id)trap);CHECK(calls==0);
    graph.external_inputs=~0u;graph.nodes[0]=(PreTreatmentNode){.encode=record};
    CHECK(pre_treatment_run(&graph,(id)trap,NULL)==(id)trap);CHECK(calls==0);
    CHECK(trap.allocations==0);CHECK(trap.encodes==0);CHECK(trap.bindings==0);
    printf("empty graph: allocations=%u encodes=%u bindings=%u callbacks=%u\n",trap.allocations,trap.encodes,trap.bindings,calls);
    graph.external_inputs=POST_SPATIAL_INPUTS|POST_BIT(POST_HISTORY_COLOUR)|POST_BIT(POST_HISTORY_DEPTH);
    const char *names[5]={"ao/smoke","sky haze","light shafts","heat haze","taa"};
    for (unsigned n=0;n<5;++n) {
        unsigned in=n>1?POST_COLOUR_0+n-2:POST_SCENE,out=n?POST_COLOUR_0+n-1:POST_SCENE;
        PreTreatmentNode node={.name=names[n],.id=n,.after=n?1u<<(n-1):0,
            .inputs=POST_BIT(in)|POST_BIT(POST_DEPTH)|POST_BIT(POST_NORMALS),
            .outputs=POST_BIT(out),.colour_in=in,.colour_out=out,.in_place=!n,.encode=record};
        if (n==4) node.inputs|=POST_BIT(POST_HISTORY_COLOUR)|POST_BIT(POST_HISTORY_DEPTH);
        CHECK(pre_treatment_register(&graph,&node));
    }
    CHECK(graph.count==5);
    if (wrong_order) { PreTreatmentNode node=graph.nodes[1];graph.nodes[1]=graph.nodes[2];graph.nodes[2]=node; }
    int valid=pre_treatment_validate(&graph);CHECK(valid);
    if (valid) {
        PreTreatmentFrame frame={.user=(void *)0x1234};
        CHECK(pre_treatment_run(&graph,fake[0],&frame)==fake[4]);CHECK(calls==5);
    }
    PreTreatmentGraph bad=graph;bad.nodes[1]=graph.nodes[2];CHECK(!pre_treatment_validate(&bad));
    bad=graph;bad.nodes[0].after=1u<<4;CHECK(!pre_treatment_validate(&bad)); // cycle
    bad=graph;bad.nodes[1].id=0;CHECK(!pre_treatment_validate(&bad)); // duplicate
    bad=graph;bad.nodes[1].colour_out=POST_SCENE;bad.nodes[1].outputs=POST_BIT(POST_SCENE);CHECK(!pre_treatment_validate(&bad)); // neighbour alias
    bad=graph;bad.external_inputs&=~POST_BIT(POST_HISTORY_DEPTH);CHECK(!pre_treatment_validate(&bad));
    bad=graph;bad.nodes[1].inputs|=POST_BIT(POST_COLOUR_3);CHECK(!pre_treatment_validate(&bad)); // future read
    bad=graph;bad.nodes[1].intermediates=POST_BIT(POST_DEPTH);CHECK(!pre_treatment_validate(&bad)); // clobber
    bad=graph;bad.nodes[1].encode=NULL;CHECK(!pre_treatment_validate(&bad));
    bad=graph;bad.count=POST_MAX_NODES+1;CHECK(!pre_treatment_validate(&bad));
    bad=graph;bad.nodes[1].after=1u<<7;CHECK(!pre_treatment_validate(&bad));
    PreTreatmentNode duplicate=graph.nodes[0];int count=graph.count;
    CHECK(!pre_treatment_register(&graph,&duplicate));CHECK(graph.count==count);
}
static const char fixture_msl[]=
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct V { float4 pos [[position]];float2 uv; };\n"
"vertex V vs(uint n [[vertex_id]]){float2 p=float2((n<<1)&2,n&2);return {float4(p*float2(2,-2)+float2(-1,1),0,1),p};}\n"
"inline half4 stage(half4 c,depth2d<float> z,uint2 p,uint n){\n"
" uint2 q=(p+uint2(n*13,n*7))%uint2(z.get_width(),z.get_height());float d=z.read(q);\n"
" return half4(half3(float3(c.rgb)+float3(d*(n+1)*.001)),c.a); }\n"
"fragment half4 separate(V i [[stage_in]],texture2d<half,access::read> s [[texture(0)]],depth2d<float> z [[texture(1)]],constant uint &n [[buffer(0)]]){return stage(s.read(uint2(i.pos.xy)),z,uint2(i.pos.xy),n);}\n"
"fragment half4 fused(V i [[stage_in]],texture2d<half,access::read> s [[texture(0)]],depth2d<float> z [[texture(1)]]){\n"
" half4 c=s.read(uint2(i.pos.xy));for(uint n=0;n<4;++n)c=stage(c,z,uint2(i.pos.xy),n);return c; }\n";
static id<MTLTexture> texture(id<MTLDevice> device,MTLPixelFormat format,unsigned w,unsigned h,int upload) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;d.storageMode=upload?MTLStorageModeShared:MTLStorageModePrivate;
    id<MTLTexture> t=[device newTextureWithDescriptor:d];CHECK(t!=nil);return t;
}
static double encode(id<MTLCommandQueue> queue,id<MTLRenderPipelineState> pso,id<MTLTexture> source,
    id<MTLTexture> depth,id<MTLTexture> a,id<MTLTexture> b,int fused) {
    id<MTLCommandBuffer> cb=[queue commandBuffer];
    id<MTLTexture> scene=source;
    for (unsigned n=0;n<(fused?1u:4u);++n) {
        id<MTLTexture> out=(n&1)?b:a;
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionDontCare;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:pso];[e setFragmentTexture:scene atIndex:0];[e setFragmentTexture:depth atIndex:1];
        if (!fused) [e setFragmentBytes:&n length:sizeof n atIndex:0];
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];scene=out;
    }
    [cb commit];[cb waitUntilCompleted];CHECK(cb.status==MTLCommandBufferStatusCompleted);
    if (cb.error) fprintf(stderr,"Metal: %s\n",cb.error.localizedDescription.UTF8String);
    double ms=(cb.GPUEndTime-cb.GPUStartTime)*1000;CHECK(isfinite(ms) && ms>0);return ms;
}
static int compare_double(const void *a,const void *b) { double x=*(const double *)a,y=*(const double *)b;return (x>y)-(x<y); }
static int measurement(void) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    if (!device) { puts("pre_treatment: no Metal device");return 3; }
    id<MTLCommandQueue> queue=[device newCommandQueue];NSError *error=nil;
    MTLCompileOptions *opts=[MTLCompileOptions new];
    if (@available(macOS 15.0,*)) opts.mathMode=MTLMathModeSafe;
    else {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        opts.fastMathEnabled=NO;
#pragma clang diagnostic pop
    }
    id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithUTF8String:fixture_msl] options:opts error:&error];
    CHECK(lib!=nil);if (!lib) { fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1; }
    id<MTLRenderPipelineState> psos[2];
    for (int n=0;n<2;++n) {
        MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
        d.vertexFunction=[lib newFunctionWithName:@"vs"];d.fragmentFunction=[lib newFunctionWithName:n?@"fused":@"separate"];
        d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
        psos[n]=[device newRenderPipelineStateWithDescriptor:d error:&error];CHECK(psos[n]!=nil);
        if (!psos[n]) {fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 1;}
    }
    enum {w=3840,h=2160,N=41};
    id<MTLTexture> scene=texture(device,MTLPixelFormatRGBA16Float,w,h,1),depth=texture(device,MTLPixelFormatDepth32Float,w,h,1);
    id<MTLTexture> a=texture(device,MTLPixelFormatRGBA16Float,w,h,0),b=texture(device,MTLPixelFormatRGBA16Float,w,h,0),f=texture(device,MTLPixelFormatRGBA16Float,w,h,0);
    __fp16 *colour=malloc((size_t)w*h*8);float *z=malloc((size_t)w*h*4);
    for (unsigned p=0;p<w*h;++p) {
        z[p]=(p%251)/251.f;
        for (unsigned c=0;c<4;++c) colour[4*p+c]=(__fp16)(c==3?.375f:((p*(c+1))%129)/64.f);
    }
    [scene replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:colour bytesPerRow:w*8];
    [depth replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:z bytesPerRow:w*4];free(z);
    double times[2][N];
    for (int sample=-8;sample<N;++sample) @autoreleasepool {
        for (int order=0;order<2;++order) {
            int fused=(sample+8+order)&1;
            double ms=encode(queue,psos[fused],scene,depth,fused?f:a,b,fused);
            if (sample>=0) times[fused][sample]=ms;
        }
    }
    free(colour);id<MTLBuffer> readbacks[2];id<MTLCommandBuffer> readback=[queue commandBuffer];
    id<MTLBlitCommandEncoder> blit=[readback blitCommandEncoder];
    for (int n=0;n<2;++n) {
        readbacks[n]=[device newBufferWithLength:(size_t)w*h*8 options:MTLResourceStorageModeShared];
        [blit copyFromTexture:n?f:b sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1)
            toBuffer:readbacks[n] destinationOffset:0 destinationBytesPerRow:w*8 destinationBytesPerImage:(size_t)w*h*8];
    }
    [blit endEncoding];[readback commit];[readback waitUntilCompleted];CHECK(readback.status==MTLCommandBufferStatusCompleted);
    unsigned mismatches=0;
    for (unsigned p=0;p<w*h*4;++p) mismatches+=memcmp((char *)readbacks[0].contents+p*2,(char *)readbacks[1].contents+p*2,2)!=0;
    CHECK(mismatches==0);
    for (int n=0;n<2;++n) qsort(times[n],N,sizeof(double),compare_double);
    double separate=times[0][N/2],fused=times[1][N/2],saving=separate-fused;
    printf("4K GPU fixture: device=%s size=%ux%u RGBA16F+Depth32F private_targets N=%u median separate_ms=%.6f fused_ms=%.6f saving_ms=%.6f decision=%s threshold_ms=0.100 byte_mismatches=%u\n",
        device.name.UTF8String,w,h,N,separate,fused,saving,saving>=.1?"fuse":"keep separate",mismatches);
    return 0;
}
int main(int argc,char **argv) {
    @autoreleasepool {
        int wrong=argc>1 && !strcmp(argv[1],"--wrong-order");graph_checks(wrong);
        if (argc>1 && !strcmp(argv[1],"--gpu")) {int rc=measurement();if(rc)return rc;}
        printf("pre_treatment: checks=%u failures=%u calls=%u\n",checks,failures,calls);return failures?1:0;
    }
}
