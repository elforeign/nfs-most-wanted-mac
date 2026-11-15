#import "temporal_render.h"
#include "lampbatch.h"
#include <objc/runtime.h>
#include <math.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
extern int diag_on;
void diag_event(const char *, ...) __attribute__((format(printf,1,2)));
#ifndef TEMPORAL_RENDER_TEST
#include "render_internal.h"
#endif

static TemporalMotionTarget atlas;
_Static_assert(sizeof(SmTemporalAux)==208, "motion constant ABI");
void temporal_render_request(int enabled) {
    if (!!enabled==temporal_state_requested) return;
#ifndef TEMPORAL_RENDER_TEST
    end_pass();temporal_draw_target=NULL;
#endif
    temporal_state_request(enabled);atlas=(TemporalMotionTarget){0};
}
static id<MTLTexture> texture(id<MTLDevice> device,MTLPixelFormat format,NSUInteger w,NSUInteger h,NSUInteger samples) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
    return [device newTextureWithDescriptor:d];
}
TemporalMotionTarget *temporal_render_prepare(id<MTLDevice> device,id<MTLTexture> colour,id<MTLTexture> depth,uint64_t frame) {
    if(!temporal_state_requested)return NULL;
    if(!colour || !depth || !frame || !colour.width || !colour.height ||
       colour.width!=depth.width || colour.height!=depth.height)return NULL;
    if(depth.pixelFormat!=MTLPixelFormatDepth32Float && depth.pixelFormat!=MTLPixelFormatDepth32Float_Stencil8)return NULL;
#ifndef TEMPORAL_TEST_MISMATCHED_SAMPLES
    if(colour.sampleCount!=depth.sampleCount)return NULL;
#endif
    if(colour.sampleCount!=1 && colour.sampleCount!=2 && colour.sampleCount!=4)return NULL;
    if(!atlas.raster || atlas.raster.width!=colour.width || atlas.raster.height!=colour.height || atlas.depth!=depth || atlas.source!=colour){
        TemporalMotionTarget next={.generation=atlas.generation+1};
        unsigned samples=1;
#ifdef TEMPORAL_TEST_MSAA_TARGET
        samples=(unsigned)colour.sampleCount;
#endif
        next.raster=texture(device,MTLPixelFormatRG16Float,colour.width,colour.height,samples);next.resolved=next.raster;
        next.coverage=texture(device,MTLPixelFormatR8Unorm,colour.width,colour.height,samples);
        next.depth=depth;next.source=colour;if(!next.raster || !next.coverage)return NULL;atlas=next;
    }
    if(atlas.frame!=frame){
        for(unsigned i=0;i<TEMPORAL_MIRROR;++i)atlas.objects[i]=nil;
        atlas.view_mask=0;
        if(diag_on)memset(atlas.drawn_instances,0,sizeof atlas.drawn_instances);
    }
    atlas.frame=frame;return &atlas;
}
static void multiply(const float a[16],const float b[16],float r[16]) {
    float out[16];
    for(unsigned i=0;i<4;++i)for(unsigned j=0;j<4;++j){
        double v=0;for(unsigned k=0;k<4;++k)v+=(double)a[4*i+k]*b[4*k+j];out[4*i+j]=(float)v;
    }
    memcpy(r,out,sizeof out);
}
static int inverse(const float in[16],float out[16]) {
    double a[4][8];
    for(unsigned i=0;i<4;++i)for(unsigned j=0;j<8;++j){
        a[i][j]=j<4?in[4*i+j]:j-4==i;
        if(!isfinite(a[i][j]))return 0;
    }
    for(unsigned k=0;k<4;++k){
        unsigned p=k;for(unsigned i=k+1;i<4;++i)if(fabs(a[i][k])>fabs(a[p][k]))p=i;
        if(fabs(a[p][k])<1e-12)return 0;
        for(unsigned j=0;j<8;++j){double t=a[k][j];a[k][j]=a[p][j];a[p][j]=t;}
        double divisor=a[k][k];for(unsigned j=0;j<8;++j)a[k][j]/=divisor;
        for(unsigned i=0;i<4;++i)if(i!=k){double v=a[i][k];for(unsigned j=0;j<8;++j)a[i][j]-=v*a[k][j];}
    }
    for(unsigned i=0;i<4;++i)for(unsigned j=0;j<4;++j){out[4*i+j]=(float)a[i][4+j];if(!isfinite(out[4*i+j]))return 0;}
    return 1;
}
int temporal_render_constants(const TemporalViewState *view,const TemporalPartPose *pose,int supported,const float raster[2],SmTemporalAux *out) {
    memset(out,0,sizeof *out);out->reactive=1;
    if(raster)memcpy(out->raster,raster,2*sizeof(float));
    if(!view || !view->history_valid || !supported || (pose && !pose->history_valid))return 0;
    float mapping[16];
    if(!inverse(view->current.matrices.vp,mapping))return 0;
#ifndef TEMPORAL_TEST_MISSING_OBJECT
    if(pose){
        float object[16];if(!inverse(pose->current,object))return 0;
        multiply(mapping,object,mapping);multiply(mapping,pose->previous,mapping);
    }
#else
    (void)pose;
#endif
    multiply(mapping,view->previous.matrices.vp,out->clip_to_previous);
    multiply(mapping,view->previous.matrices.view,out->clip_to_previous_view);
    out->valid=1;out->reactive=0;
    return 1;
}

int temporal_render_moved(const TemporalViewState *view,const TemporalPartPose *pose,const uint32_t viewport[4]) {
#ifdef TEMPORAL_TEST_REPLAY_STATIONARY
    if(pose)return 1;
#endif
    if(!view || !pose || !viewport || !view->history_valid || !pose->history_valid)return 0;
    if(!memcmp(pose->current,pose->previous,sizeof pose->current))return 0;
    SmTemporalAux object,base;
    if(!temporal_render_constants(view,pose,1,NULL,&object) ||
       !temporal_render_constants(view,NULL,1,NULL,&base))return 1;
    // Affine extrema occur at clip-box corners. Bound the difference of the
    // two perspective divisions conservatively, including depth and rotation;
    // a crossing of either previous near plane always retains the draw.
    double min_base=INFINITY,min_object=INFINITY,max_base[2]={0},delta[4]={0};
    for(unsigned i=0;i<8;++i){
        double c[4]={(i&1?1:-1)*(1.+2./viewport[2]),(i&2?1:-1)*(1.+2./viewport[3]),i&4?1:0,1},b[4]={0},o[4]={0};
        for(unsigned j=0;j<4;++j)for(unsigned k=0;k<4;++k){
            b[j]+=c[k]*base.clip_to_previous[k*4+j];o[j]+=c[k]*object.clip_to_previous[k*4+j];
        }
        min_base=fmin(min_base,b[3]);min_object=fmin(min_object,o[3]);
        for(unsigned j=0;j<4;++j){
            if(!isfinite(b[j]) || !isfinite(o[j]))return 1;
            delta[j]=fmax(delta[j],fabs(o[j]-b[j]));
            if(j<2)max_base[j]=fmax(max_base[j],fabs(b[j]));
        }
    }
    if(min_base<=0 || min_object<=0)return 1;
    for(unsigned j=0;j<2;++j){
        double pixels=.5*viewport[2+j]*(delta[j]/min_object+max_base[j]*delta[3]/(min_object*min_base));
        if(pixels>1./16)return 1;
    }
    return 0;
}

void temporal_render_view(TemporalMotionTarget *target,TemporalViewId id,const TemporalViewState *state,const uint32_t viewport[4]) {
    if(!target || id>=TEMPORAL_MIRROR || !state || !viewport)return;
    SmTemporalAux *a=&target->views[id];float raster[2]={1.f/viewport[2],-1.f/viewport[3]};
    temporal_render_constants(state,NULL,1,raster,a);
    a->viewport[0]=viewport[2];a->viewport[1]=viewport[3];a->viewport[2]=viewport[0];a->viewport[3]=viewport[1];
    target->view_mask|=1u<<id;
}
int temporal_render_pipeline(id<MTLDevice> device,MTLRenderPipelineDescriptor *p,NSString *vertex,NSString *fragment,int samples) {
    static NSMutableDictionary<NSString *,id<MTLFunction>> *functions;
    if(!functions)functions=[NSMutableDictionary new];
    NSString *sources[2]={vertex,fragment};id<MTLFunction> stages[2];
    for(int i=0;i<2;++i){
        NSString *key=[NSString stringWithFormat:@"%d:%d:%@",i,samples>1,sources[i]];stages[i]=functions[key];
        if(!stages[i]){
            char *adapted=sm_temporal_variant(sources[i].UTF8String,i);if(!adapted)return 0;
            NSString *src=@(adapted);free(adapted);
#ifdef TEMPORAL_TEST_WRONG_SIGN
            src=[src stringByReplacingOccurrencesOfString:@"p.xy/p.w-c.xy/c.w" withString:@"c.xy/c.w-p.xy/p.w"];
#endif
            NSError *error=nil;
#ifdef TEMPORAL_RENDER_TEST
            id<MTLLibrary> lib=temporal_render_test_library(device,src,&error);
#else
            id<MTLLibrary> lib=pipeline_library_source(src,"object_motion",&error);
#endif
            stages[i]=[lib newFunctionWithName:@"sm_main"];
            if(!stages[i]){fprintf(stderr,"object motion: %s\n",error.localizedDescription.UTF8String);return 0;}functions[key]=stages[i];
        }
    }
    p.vertexFunction=stages[0];p.fragmentFunction=stages[1];p.rasterSampleCount=1;p.alphaToCoverageEnabled=NO;
    p.depthAttachmentPixelFormat=MTLPixelFormatInvalid;p.stencilAttachmentPixelFormat=MTLPixelFormatInvalid;
    for(unsigned i=0;i<8;++i){p.colorAttachments[i]=[MTLRenderPipelineColorAttachmentDescriptor new];}
    p.colorAttachments[0].pixelFormat=MTLPixelFormatRG16Float;
    p.colorAttachments[1].pixelFormat=MTLPixelFormatR8Unorm;
    return 1;
}
void temporal_render_bind(id<MTLRenderCommandEncoder> e,const SmTemporalAux *a) {
    [e setVertexBytes:a length:sizeof *a atIndex:SM_VS_BUF_TEMPORAL];
    [e setFragmentBytes:a length:sizeof *a atIndex:SM_PS_BUF_TEMPORAL];
}

@interface MotionRecord : NSObject {
@public
    id<MTLRenderCommandEncoder> real;
    id recorder;
    id<MTLRenderPipelineState> pipeline;
    SmTemporalAux aux;
    TemporalMotionTarget *target;
    TemporalViewId view;
}
@end
@implementation MotionRecord
- (NSMethodSignature *)methodSignatureForSelector:(SEL)s {
    Protocol *protocols[2]={@protocol(MTLRenderCommandEncoder),@protocol(MTLCommandEncoder)};
    for(unsigned i=0;i<2;++i)for(int required=1;required>=0;--required){
        struct objc_method_description d=protocol_getMethodDescription(protocols[i],s,(BOOL)required,YES);
        if(d.name)return [NSMethodSignature signatureWithObjCTypes:d.types];
    }
    return [super methodSignatureForSelector:s];
}
- (void)forwardInvocation:(NSInvocation *)inv {
    if(real)[inv invokeWithTarget:real];
    SEL s=inv.selector;
    if(s==@selector(setRenderPipelineState:)){[(id<MTLRenderCommandEncoder>)recorder setRenderPipelineState:pipeline];return;}
    if(s==@selector(setDepthStencilState:) || s==@selector(setStencilReferenceValue:) ||
       s==@selector(setDepthBias:slopeScale:clamp:) || s==@selector(setVisibilityResultMode:offset:))return;
    if([NSStringFromSelector(s) hasPrefix:@"draw"]){
#ifndef TEMPORAL_TEST_NO_OVERRIDE
        temporal_render_bind(recorder,&aux);[inv invokeWithTarget:recorder];
        if(diag_on){
            NSUInteger instances=1,index=2;
            for(NSString *part in [NSStringFromSelector(s) componentsSeparatedByString:@":"]){
                if([part isEqualToString:@"instanceCount"]){[inv getArgument:&instances atIndex:index];break;}
                ++index;
            }
            target->drawn_instances[view]+=instances;
        }
#endif
    }else [inv invokeWithTarget:recorder];
}
@end
id<MTLRenderCommandEncoder> temporal_render_record(id<MTLRenderCommandEncoder> real,TemporalMotionTarget *target,TemporalViewId view,id<MTLRenderPipelineState> pso,const SmTemporalAux *aux) {
    if(!target || view>=TEMPORAL_MIRROR || !pso || !aux)return real;
    if(!target->objects[view])target->objects[view]=lamprec_new();
    MotionRecord *r=[MotionRecord new];r->real=real;r->recorder=target->objects[view];r->pipeline=pso;r->aux=*aux;r->target=target;r->view=view;
    return (id<MTLRenderCommandEncoder>)r;
}
// Consumers concatenate this source into their own shader. The constants are
// target->views[view] (SmTemporalAux); all positions and depth are resolved pixels.
const char *temporal_render_camera_msl(void) {
    return
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct TemporalCameraAux{float4x4 previous,previous_view;float4 raster,viewport;float2 positions[4];float valid,reactive,pad0,pad1;};\n"
"inline half2 temporal_camera_motion(float2 position,depth2d<float,access::read> z,constant TemporalCameraAux &a){\n"
" float d=z.read(uint2(position));if(a.valid<.5f || !isfinite(d) || d>=1)return half2(0);\n"
" float2 ndc=(position-a.viewport.zw)/a.viewport.xy*float2(2,-2)+float2(-1,1)-a.raster.xy;\n"
" float4 c=float4(ndc,d,1),p=a.previous*c;\n"
" if(p.w<=0 || !all(isfinite(p)))return half2(0);\n"
" return half2((p.xy/p.w-c.xy/c.w)*float2(.5f,-.5f)); }\n"
"inline half2 temporal_consumer_motion(float2 position,depth2d<float,access::read> z,texture2d<half,access::read> objects,texture2d<half,access::read> coverage,constant TemporalCameraAux &a){\n"
" uint2 pixel=uint2(position);return coverage.read(pixel).r>half(0)?objects.read(pixel).rg:temporal_camera_motion(position,z,a); }\n";
}
static int ready(TemporalMotionTarget *t,id<MTLTexture> z,TemporalViewId view){
    return temporal_state_requested && t && view<TEMPORAL_MIRROR && (t->view_mask&(1u<<view)) && z &&
        z.sampleCount==1 && t->raster.sampleCount==1 && z.width==t->raster.width && z.height==t->raster.height &&
        (z.pixelFormat==MTLPixelFormatDepth32Float || z.pixelFormat==MTLPixelFormatDepth32Float_Stencil8);
}
// Stage timestamps isolate this encoder even in a larger frame command buffer.
// Allocation, clocks, instance counting and completion handlers are diag-only.
static id<MTLCounterSampleBuffer> motion_diag_begin(id<MTLDevice> device,MTLRenderPassDescriptor *rp,double *tick_ns) {
    if(![device supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary])return nil;
    static uint64_t cpu0,gpu0;static double scale=1;
    MTLTimestamp cpu,gpu;[device sampleTimestamps:&cpu gpuTimestamp:&gpu];
    uint64_t now=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    if(cpu0 && now>cpu0 && gpu>gpu0)scale=(double)(now-cpu0)/(gpu-gpu0);
    cpu0=now;gpu0=gpu;*tick_ns=scale;
    for(id<MTLCounterSet> set in device.counterSets)if([set.name isEqualToString:MTLCommonCounterSetTimestamp]){
        MTLCounterSampleBufferDescriptor *d=[MTLCounterSampleBufferDescriptor new];
        d.counterSet=set;d.storageMode=MTLStorageModeShared;d.sampleCount=4;
        id<MTLCounterSampleBuffer> samples=[device newCounterSampleBufferWithDescriptor:d error:NULL];
        if(!samples)return nil;
        MTLRenderPassSampleBufferAttachmentDescriptor *a=rp.sampleBufferAttachments[0];a.sampleBuffer=samples;
        a.startOfVertexSampleIndex=0;a.endOfVertexSampleIndex=1;a.startOfFragmentSampleIndex=2;a.endOfFragmentSampleIndex=3;
        return samples;
    }
    return nil;
}
static uint64_t motion_diag_clock(void) {
#ifdef TEMPORAL_RENDER_TEST
    extern uint64_t temporal_render_test_clock(void);
    return temporal_render_test_clock();
#else
    return clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
#endif
}
static void motion_diag_end(id<MTLCommandBuffer> cb,id<MTLCounterSampleBuffer> samples,double tick_ns,uint64_t frame,uint64_t instances,TemporalViewId view) {
    [cb addCompletedHandler:^(id<MTLCommandBuffer> done){
        if(done.status!=MTLCommandBufferStatusCompleted)return;
        NSData *data=[samples resolveCounterRange:NSMakeRange(0,4)];const uint64_t *t=data.bytes;
        double ms=-1;
        if(data.length>=4*sizeof(uint64_t) && t[0]!=MTLCounterErrorValue && t[1]!=MTLCounterErrorValue &&
           t[2]!=MTLCounterErrorValue && t[3]!=MTLCounterErrorValue && t[3]>=t[0])
            ms=(fmax(t[1],t[3])-fmin(t[0],t[2]))*tick_ns*1e-6;
        // Completions on the renderer's queue are ordered. Log a sampled frame,
        // including zero draws; never substitute whole-frame command-buffer time.
        static uint64_t last;uint64_t now=motion_diag_clock();
        if(!last || now-last>=1000000000ull){
            last=now;diag_event("temporal objects: frame=%llu view=%u instances=%llu gpu_ms=%.6f",
                (unsigned long long)frame,(unsigned)view,(unsigned long long)instances,ms);
        }
    }];
}
int temporal_render_objects(id<MTLCommandBuffer> cb,TemporalMotionTarget *t,id<MTLTexture> z,TemporalViewId view){
    if(!cb || !ready(t,z,view) || lamprec_refused(t->objects[view]))return 0;
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    id<MTLTexture> attachments[2]={t->raster,t->coverage};
    for(unsigned i=0;i<2;++i){
        rp.colorAttachments[i].texture=attachments[i];
        // Uncovered vectors are undefined; only coverage needs a full clear.
        rp.colorAttachments[i].loadAction=t->cleared_frame==t->frame?MTLLoadActionLoad:
            i?MTLLoadActionClear:MTLLoadActionDontCare;
        rp.colorAttachments[i].clearColor=MTLClearColorMake(0,0,0,0);
        rp.colorAttachments[i].storeAction=MTLStoreActionStore;
    }
    double tick_ns=1;id<MTLCounterSampleBuffer> samples=nil;
    if(diag_on)samples=motion_diag_begin(cb.device,rp,&tick_ns);
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];e.label=@"temporal objects";
    [e setFragmentTexture:z atIndex:31];lamprec_replay(t->objects[view],e);[e endEncoding];
    if(diag_on)motion_diag_end(cb,samples,tick_ns,t->frame,t->drawn_instances[view],view);
    t->cleared_frame=t->frame;return 1;
}
id<MTLTexture> temporal_render_resolve(id<MTLDevice> device,id<MTLCommandBuffer> cb,TemporalMotionTarget *t,id<MTLTexture> z,TemporalViewId view){
    (void)device;
    return temporal_render_objects(cb,t,z,view)?t->resolved:nil;
}
#ifndef TEMPORAL_RENDER_TEST
TemporalMotionTarget *temporal_draw_target;
TemporalViewId temporal_draw_view=TEMPORAL_VIEW_COUNT;
int temporal_render_scene(id<MTLTexture> colour,id<MTLTexture> depth,uint64_t serial,const uint32_t viewport[4],int eligible) {
    id<MTLTexture> old_raster=temporal_draw_target?temporal_draw_target->raster:nil;
    uint64_t old_frame=temporal_draw_target?temporal_draw_target->frame:0;
    TemporalMotionTarget *next=NULL;
    TemporalViewId id=TEMPORAL_VIEW_COUNT;
    if(eligible && colour && depth){
        uint32_t descriptor=M32(0x00982A20u);
        uint32_t source=descriptor && !M32(descriptor) && M32(descriptor+4)<32?0x9195E0u+M32(descriptor+4)*0x70u:0;
        if(source && M32(source+0x60)==descriptor)id=temporal_guest_view_id(source);
        if(id<TEMPORAL_MIRROR){
            TemporalViewKey key={.source=(uint64_t)(__bridge void *)colour,.depth=(uint64_t)(__bridge void *)depth,
                .width=(uint32_t)colour.width,.height=(uint32_t)colour.height,.samples=(uint32_t)colour.sampleCount,
                .format=(uint32_t)colour.pixelFormat,.domain=colour.pixelFormat==MTLPixelFormatRGBA16Float};
            memcpy(key.rect,viewport,sizeof key.rect);
            const TemporalViewState *state=temporal_view_accept_target(id,&key);
            if(state){
                next=temporal_render_prepare(mtl,colour,depth,serial);
                temporal_render_view(next,id,state,viewport);
            }
        }
    }
    if(temporal_draw_target!=next || temporal_draw_view!=id || (next && (old_raster!=next->raster || old_frame!=serial))){end_pass();pass_dirty=1;}
    temporal_draw_target=next;temporal_draw_view=id;
    return next!=NULL;
}
#endif
