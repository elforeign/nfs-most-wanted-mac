// Shipping motion raster/resolve proof; numbers and controls in the Python header.
#import "temporal_render.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
#include "temporal_camera_control.inc"
extern char *sm_temporal_variant_control(const char *,int);
int diag_on;
static uint64_t diag_clock=1000000000ull;
uint64_t temporal_render_test_clock(void){return diag_clock;}
static unsigned diag_messages;
static char diag_message[256];
void diag_event(const char *format,...){va_list ap;va_start(ap,format);vsnprintf(diag_message,sizeof diag_message,format,ap);va_end(ap);++diag_messages;}
#include "temporal_motion_sources.inc"
#include "temporal_motion_classify.inc"
#include "lampbatch.h"

static unsigned checks,failures,scene_calls;
static TemporalMotionTarget *reference_target;
static double max_uv_error,max_depth_error,max_reference_error;
static unsigned reference_components,reference_exact;
static int clear_vectors_control;
#define CHECK(x) do{++checks;if(!(x)){++failures;if(failures<18)fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);}}while(0)
static id<MTLDevice> device;
static id<MTLCommandQueue> queue;
static unsigned W=32,H=24;
static unsigned draw_samples=1;
static uint64_t serial=1;
static NSString *vertex_source[3],*pixel_source[3];
static SmResult vertex_reflection[3],pixel_reflection[3];
static id<MTLTexture> dummy2d,dummycube,dummy3d;
static id<MTLSamplerState> sampler;
static unsigned samples_checked[3],variant_checked[3],mixed_checked,a2c_checked,occlusion_checked;

// The exact request guard is extracted from draw_impl by the wrapper. Its
// disabled side cannot touch these deliberately absent scene bindings.
static struct { uint32_t viewport[4],rt[4],rs[256]; } dev;
static TemporalMotionTarget *temporal_draw_target;
static TemporalViewId temporal_draw_view=TEMPORAL_VIEW_COUNT;
static unsigned ended_passes,descriptor_entry;
static int pass_dirty;
typedef struct {uint32_t ecx,esp;} Cpu;
static TemporalPartKey temporal_part_source;
static float destructible_world[16];
static TemporalPoseToken destructible_token;
void f_0073B9C0_orig(Cpu *c) {
    TemporalPartKey key=temporal_part_source;key.mesh=0x410000;
    destructible_token=temporal_pose_submit(0,&key,destructible_world);c->esp+=24;
}
static int ssao_replay,lamp_replay;
static uint32_t q_active,rs_storage[256],*rs=rs_storage;
static id<MTLTexture> rt0,dst;
static struct { id<MTLTexture> ms,tex; } bb_rt,ad_rt;
static uint64_t frame_serial;
static int shipping_scene(id<MTLTexture>,id<MTLTexture>,uint64_t,const uint32_t[4],int);
int temporal_render_scene(id<MTLTexture> c,id<MTLTexture> z,uint64_t f,const uint32_t vp[4],int ok) {
    ++scene_calls;return shipping_scene(c,z,f,vp,ok);
}
static void shipping_guard(void) {
#include "temporal_motion_guard.inc"
}

static void identity(float *m){memset(m,0,64);for(unsigned i=0;i<4;++i)m[5*i]=1;}
static void mul(const float *a,const float *b,float *r){float t[16]={0};for(int i=0;i<4;++i)for(int j=0;j<4;++j)for(int k=0;k<4;++k)t[i*4+j]+=a[i*4+k]*b[k*4+j];memcpy(r,t,64);}
static void world(float *m,double x,double y,double angle){identity(m);m[0]=cos(angle);m[1]=sin(angle);m[4]=-sin(angle);m[5]=cos(angle);m[12]=x;m[13]=y;}
static void roll(float *m,double angle){float r[16];identity(r);r[5]=cos(angle);r[6]=sin(angle);r[9]=-sin(angle);r[10]=cos(angle);mul(r,m,m);}
static TemporalMatrices camera(double x){
    TemporalMatrices m={.near_z=.5f,.far_z=100};identity(m.view);m.view[12]=-x;
    memset(m.projection,0,64);m.projection[0]=m.projection[5]=1;m.projection[10]=100.f/99.5f;m.projection[11]=1;m.projection[14]=-50.f/99.5f;
    mul(m.view,m.projection,m.vp);return m;
}
static TemporalViewState view_state(double now,double before){TemporalViewState v={.history_valid=1};v.current.matrices=camera(now);v.previous.matrices=camera(before);return v;}
static TemporalPartPose part(double x,double y,double a,double px,double py,double pa){TemporalPartPose p={.history_valid=1};world(p.current,x,y,a);world(p.previous,px,py,pa);return p;}

static id<MTLTexture> tex(MTLPixelFormat format,unsigned samples,unsigned w,unsigned h){
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    if(samples==1 && format==MTLPixelFormatRG16Float)d.usage|=MTLTextureUsageShaderWrite;
    if(samples>1){d.textureType=MTLTextureType2DMultisample;d.sampleCount=samples;}
    return [device newTextureWithDescriptor:d];
}
static void complete(id<MTLCommandBuffer> cb){[cb commit];[cb waitUntilCompleted];if(cb.error){fprintf(stderr,"Metal: %s\n",cb.error.localizedDescription.UTF8String);exit(2);}}
static id<MTLLibrary> library(NSString *source){NSError *e=nil;id<MTLLibrary> l=[device newLibraryWithSource:source options:nil error:&e];if(!l){fprintf(stderr,"MSL: %s\n",e.localizedDescription.UTF8String);exit(2);}return l;}
id<MTLLibrary> temporal_render_test_library(id<MTLDevice> d,NSString *s,NSError **error){return [d newLibraryWithSource:s options:nil error:error];}
static void end_pass(void){++ended_passes;}
static uint32_t scene_memory(uint32_t address){
    if(address==0x2004)return 0x400000;
    if(address==0x2008)return 0x919650;
    if(address==0x982A20u)return 0x1000;
    if(address==0x1000)return descriptor_entry;
    if(address==0x1004)return 1;
    if(address==0x9196B0)return 0x1000;
    return 0;
}
TemporalViewId temporal_guest_view_id(uint32_t source){return source==0x919650?TEMPORAL_SP:TEMPORAL_VIEW_COUNT;}
#define mtl device
#define M32(address) scene_memory(address)
#include "temporal_destructible_hook.inc"
#include "temporal_motion_scene.inc"
#undef M32
#undef mtl

static id<MTLRenderPipelineState> pipeline(unsigned variant,unsigned samples,int motion,int a2c,int reactive){
    (void)reactive;draw_samples=samples;
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[library(vertex_source[variant]) newFunctionWithName:@"sm_main"];
    d.fragmentFunction=[library(pixel_source[variant]) newFunctionWithName:@"sm_main"];
    MTLVertexDescriptor *vd=[MTLVertexDescriptor vertexDescriptor];
    for(int k=0;k<vertex_reflection[variant].n_inputs;++k){unsigned reg=vertex_reflection[variant].inputs[k].reg;
        vd.attributes[reg].format=MTLVertexFormatFloat4;vd.attributes[reg].bufferIndex=0;vd.attributes[reg].offset=reg*16;
    }
    vd.layouts[0].stride=16*16;d.vertexDescriptor=vd;
    d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;d.rasterSampleCount=samples;
    d.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;d.alphaToCoverageEnabled=a2c;
    if(motion)CHECK(temporal_render_pipeline(device,d,vertex_source[variant],pixel_source[variant],samples));
    if(motion==2){
        char *vs=sm_temporal_variant_control(vertex_source[variant].UTF8String,SM_VERTEX);
        char *ps=sm_temporal_variant_control(pixel_source[variant].UTF8String,SM_PIXEL);
        CHECK(vs && ps);d.vertexFunction=[library(@(vs)) newFunctionWithName:@"sm_main"];
        d.fragmentFunction=[library(@(ps)) newFunctionWithName:@"sm_main"];free(vs);free(ps);
    }
    NSError *error=nil;id<MTLRenderPipelineState> result=[device newRenderPipelineStateWithDescriptor:d error:&error];
    if(!result){fprintf(stderr,"PSO: %s\n",error.localizedDescription.UTF8String);exit(2);}return result;
}
typedef struct {float attr[16][4];} Vertex;
static void draw(id<MTLRenderCommandEncoder> e,id<MTLRenderPipelineState> p,unsigned variant,
                 const TemporalViewState *v,const TemporalPartPose *pose,double depth,double alpha,
                 double edge,int reactive,int invalid,int full){
    Vertex vertices[6]={0};
    // A vertical edge at a non-pixel/sample boundary, with optional full coverage.
    double x1=full?3:edge;
    const double xy[6][2]={{-1,-1},{x1,-1},{-1,3},{x1,-1},{x1,3},{-1,3}};
    float wvp[16],model[16];if(pose)memcpy(model,pose->current,64);else identity(model);
    mul(model,v->current.matrices.vp,wvp);
    for(unsigned i=0;i<6;++i){
        // Build object positions from desired screen coordinates using the known
        // analytic inverse rigid transform, independent of production inversion.
        double camera_x=-v->current.matrices.view[12];
        double t=(depth+model[8]*model[12]+model[9]*model[13]+model[10]*model[14]-camera_x*model[8])/
            (model[8]*xy[i][0]+model[9]*xy[i][1]+model[10]);
        double dx=xy[i][0]*t+camera_x-model[12],dy=xy[i][1]*t-model[13],dz=t-model[14];
        double ox=model[0]*dx+model[1]*dy+model[2]*dz,oy=model[4]*dx+model[5]*dy+model[6]*dz;
        double oz=model[8]*dx+model[9]*dy+model[10]*dz;
        for(int k=0;k<vertex_reflection[variant].n_inputs;++k){SmInput in=vertex_reflection[variant].inputs[k];float *a=vertices[i].attr[in.reg];a[3]=1;
            if(in.usage==SM_USAGE_POSITION){a[0]=ox;a[1]=oy;a[2]=oz;}
            else if(in.usage==SM_USAGE_NORMAL){a[2]=1;}
            else if(in.usage==SM_USAGE_COLOR){a[0]=a[1]=a[2]=.7f;a[3]=alpha;}
            else if(in.usage==SM_USAGE_TEXCOORD){a[0]=a[1]=.25f;}
        }
    }
    float constants[256][4];for(int i=0;i<256;++i)for(int j=0;j<4;++j)constants[i][j]=.25f;
    for(int i=0;i<4;++i)for(int j=0;j<4;++j)constants[i][j]=wvp[j*4+i];
    constants[4][0]=constants[4][1]=constants[4][2]=constants[4][3]=1;
    // Valid finite light/camera data for the shipping car vertex and pixel stages.
    constants[13][0]=constants[13][1]=0;constants[13][2]=10;constants[13][3]=1;
    int integers[16][4]={0},booleans[16]={0};float material[12][4]={{0}};
    material[0][0]=1;material[0][2]=.4;material[0][3]=1;
    SmVSAux va={{1.f/W,-1.f/H,0,0},1,{0,0,0}};
    SmPSAux pa={0};if(alpha<1){pa.alpha_func=7;pa.alpha_ref=154.f/255;pa.alpha_to_coverage=draw_samples>1;}
    [e setRenderPipelineState:p];[e setCullMode:MTLCullModeNone];
    [e setVertexBytes:vertices length:sizeof vertices atIndex:0];
    [e setVertexBytes:constants length:sizeof constants atIndex:16];[e setVertexBytes:integers length:sizeof integers atIndex:17];
    [e setVertexBytes:booleans length:sizeof booleans atIndex:18];[e setVertexBytes:&va length:sizeof va atIndex:19];
    [e setFragmentBytes:constants length:sizeof constants atIndex:0];[e setFragmentBytes:integers length:sizeof integers atIndex:1];
    [e setFragmentBytes:booleans length:sizeof booleans atIndex:2];[e setFragmentBytes:&pa length:sizeof pa atIndex:3];
    [e setFragmentBytes:material length:sizeof material atIndex:4];
    for(unsigned i=0;i<16;++i){[e setFragmentTexture:i==1?dummycube:i==14?dummy3d:dummy2d atIndex:i];[e setFragmentSamplerState:sampler atIndex:i];}
    (void)reactive;(void)invalid;
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:full?3:6];
}


// Read the actual scene samples to derive coverage independently of replay.
static id<MTLBuffer> read_depth(id<MTLCommandBuffer> cb,id<MTLTexture> depth){
    static id<MTLComputePipelineState> pso[2];unsigned ms=depth.sampleCount>1,n=(unsigned)depth.sampleCount;
    if(!pso[ms]){
        NSString *source=[NSString stringWithFormat:@"%s#include <metal_stdlib>\nusing namespace metal;\n"
            "kernel void read_depth(uint2 p [[thread_position_in_grid]],\n"
            "#ifdef MS\ndepth2d_ms<float,access::read> z [[texture(0)]],\n#else\ndepth2d<float,access::read> z [[texture(0)]],\n#endif\n"
            "device float *out [[buffer(0)]]){if(p.x>=z.get_width()||p.y>=z.get_height())return;\n"
            "#ifdef MS\nuint n=z.get_num_samples();for(uint s=0;s<n;++s)out[(p.y*z.get_width()+p.x)*n+s]=z.read(p,s);\n"
            "#else\nout[p.y*z.get_width()+p.x]=z.read(p);\n#endif\n}",ms?"#define MS\n":""];
        pso[ms]=[device newComputePipelineStateWithFunction:[library(source) newFunctionWithName:@"read_depth"] error:NULL];
    }
    id<MTLBuffer> out=[device newBufferWithLength:W*H*n*4 options:MTLResourceStorageModeShared];
    id<MTLComputeCommandEncoder> e=[cb computeCommandEncoder];[e setComputePipelineState:pso[ms]];
    [e setTexture:depth atIndex:0];[e setBuffer:out offset:0 atIndex:0];
    [e dispatchThreads:MTLSizeMake(W,H,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[e endEncoding];return out;
}
static id<MTLBuffer> read_texture(id<MTLCommandBuffer> cb,id<MTLTexture> t){
    unsigned stride=(W*4+255)&~255u;id<MTLBuffer> b=[device newBufferWithLength:stride*H options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:stride destinationBytesPerImage:stride*H];[e endEncoding];return b;
}
static id<MTLBuffer> read_coverage(id<MTLCommandBuffer> cb,id<MTLTexture> t){
    unsigned stride=(W+255)&~255u;id<MTLBuffer> b=[device newBufferWithLength:stride*H options:MTLResourceStorageModeShared];
    id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(W,H,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:stride destinationBytesPerImage:stride*H];[e endEncoding];return b;
}
static double half_value(uint16_t x){_Float16 h;memcpy(&h,&x,2);return (double)h;}
static double tolerance(double x){return fmax(0.0000002,fabs(x)*.0011);}
static void close_value(double actual,double expected,int depth){
    double delta=fabs(actual-expected);if(depth)max_depth_error=fmax(max_depth_error,delta);else max_uv_error=fmax(max_uv_error,delta);
    if(delta>tolerance(expected) && failures<6)fprintf(stderr,"motion mismatch: actual=%.10g expected=%.10g delta=%.10g allowed=%.10g\n",actual,expected,delta,tolerance(expected));
    CHECK(isfinite(actual) && delta<=tolerance(expected));
}
// Independent closed-form oracle for a planar rigid part and camera pan.
static void analytic(const TemporalViewState *v,const TemporalPartPose *pose,double depth,double nx,double ny,double out[3]){
    double camera_x=-v->current.matrices.view[12],t=depth;
    if(pose){const float *m=pose->current;t=(depth+m[8]*m[12]+m[9]*m[13]+m[10]*m[14]-camera_x*m[8])/(m[8]*nx+m[9]*ny+m[10]);}
    double x=nx*t+camera_x,y=ny*t,z=t;
    if(pose){double dx=x-pose->current[12],dy=y-pose->current[13],dz=z-pose->current[14];
        double ox=pose->current[0]*dx+pose->current[1]*dy+pose->current[2]*dz;
        double oy=pose->current[4]*dx+pose->current[5]*dy+pose->current[6]*dz;
        double oz=pose->current[8]*dx+pose->current[9]*dy+pose->current[10]*dz;
        x=ox*pose->previous[0]+oy*pose->previous[4]+oz*pose->previous[8]+pose->previous[12];
        y=ox*pose->previous[1]+oy*pose->previous[5]+oz*pose->previous[9]+pose->previous[13];
        z=ox*pose->previous[2]+oy*pose->previous[6]+oz*pose->previous[10]+pose->previous[14];}
    double px=(x+v->previous.matrices.view[12])/z,py=y/z;
    out[0]=(px-nx)*.5;out[1]=(ny-py)*.5;out[2]=z;
}


static MTLRenderPassDescriptor *scene_pass(id<MTLTexture> colour,id<MTLTexture> z,id<MTLTexture> resolved){
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=colour;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    rp.depthAttachment.texture=z;rp.depthAttachment.loadAction=MTLLoadActionClear;rp.depthAttachment.clearDepth=1;rp.depthAttachment.storeAction=MTLStoreActionStore;
    if(z.sampleCount>1){rp.depthAttachment.resolveTexture=resolved;rp.depthAttachment.storeAction=MTLStoreActionStoreAndMultisampleResolve;rp.depthAttachment.depthResolveFilter=MTLMultisampleDepthResolveFilterMin;}
    return rp;
}
// Count the actual production frame encodes independently of shader text.
@interface MotionCommandProof : NSObject
@property id<MTLCommandBuffer> buffer;
@property unsigned render_count,completion_count,counter_count;
@end
@implementation MotionCommandProof
- (id<MTLDevice>)device{return _buffer.device;}
- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)rp{
    ++_render_count;_counter_count+=rp.sampleBufferAttachments[0].sampleBuffer!=nil;CHECK(rp.colorAttachments[0].texture.pixelFormat==MTLPixelFormatRG16Float);
    CHECK(rp.colorAttachments[1].texture.pixelFormat==MTLPixelFormatR8Unorm);
    if(clear_vectors_control)rp.colorAttachments[0].loadAction=MTLLoadActionClear;
    return [_buffer renderCommandEncoderWithDescriptor:rp];
}
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler{++_completion_count;[_buffer addCompletedHandler:handler];}
- (id)forwardingTargetForSelector:(SEL)s{(void)s;return _buffer;}
@end
static id<MTLTexture> object_resolve(id<MTLCommandBuffer> cb,TemporalMotionTarget *t,id<MTLTexture> depth){
    MotionCommandProof *proof=[MotionCommandProof new];proof.buffer=cb;
    id<MTLTexture> result=temporal_render_resolve(device,(id<MTLCommandBuffer>)proof,t,depth,TEMPORAL_SP);
    CHECK(proof.render_count==1);
    CHECK(proof.completion_count==(diag_on?1u:0u));
    CHECK(diag_on || proof.counter_count==0);return result;
}
// The mock consumer includes the shipping function, exactly as TAA will. No
// camera pass is added to the shipping frame; this compute exists only here.
static id<MTLTexture> consume(id<MTLCommandBuffer> cb,TemporalMotionTarget *t,id<MTLTexture> depth){
    static id<MTLComputePipelineState> pso;
    if(!pso){
        NSString *source=@(temporal_render_camera_msl());
#ifdef TEMPORAL_TEST_STALE_VP
        source=[source stringByReplacingOccurrencesOfString:@"p=a.previous*c" withString:@"p=c"];
#endif
#ifdef TEMPORAL_TEST_UNRESOLVED_DEPTH
        source=[source stringByReplacingOccurrencesOfString:@"depth2d<float,access::read> z" withString:@"depth2d_ms<float,access::read> z"];
        source=[source stringByReplacingOccurrencesOfString:@"z.read(uint2(position))" withString:@"z.read(uint2(position),0)"];
#endif
        source=[source stringByAppendingString:@"kernel void consumer(uint2 pixel [[thread_position_in_grid]],"
#ifdef TEMPORAL_TEST_UNRESOLVED_DEPTH
            "depth2d_ms<float,access::read> z [[texture(0)]],"
#else
            "depth2d<float,access::read> z [[texture(0)]],"
#endif
            "texture2d<half,access::read> objects [[texture(1)]],texture2d<half,access::read> coverage [[texture(2)]],"
            "texture2d<half,access::write> out [[texture(3)]],constant TemporalCameraAux &a [[buffer(0)]]){"
            "if(pixel.x>=out.get_width()||pixel.y>=out.get_height())return;"
            "out.write(half4(temporal_consumer_motion(float2(pixel)+.5f,z,objects,coverage,a),0,0),pixel);}"];
        pso=[device newComputePipelineStateWithFunction:[library(source) newFunctionWithName:@"consumer"] error:NULL];CHECK(pso!=nil);
    }
    id<MTLTexture> out=tex(MTLPixelFormatRG16Float,1,W,H);
    id<MTLComputeCommandEncoder> e=[cb computeCommandEncoder];[e setComputePipelineState:pso];
#ifdef TEMPORAL_TEST_UNRESOLVED_DEPTH
    [e setTexture:t->depth atIndex:0];(void)depth;
#else
    [e setTexture:depth atIndex:0];
#endif
    [e setTexture:t->raster atIndex:1];[e setTexture:t->coverage atIndex:2];[e setTexture:out atIndex:3];
    [e setBytes:&t->views[TEMPORAL_SP] length:sizeof(SmTemporalAux) atIndex:0];
    [e dispatchThreads:MTLSizeMake(W,H,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[e endEncoding];return out;
}
static id<MTLTexture> stored_combined(id<MTLCommandBuffer> cb,TemporalMotionTarget *t,id<MTLTexture> depth){
    static id<MTLRenderPipelineState> pso;
    if(!pso){MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];id<MTLLibrary> lib=library(@(control_camera_msl));
        d.vertexFunction=[lib newFunctionWithName:@"motion_vs"];d.fragmentFunction=[lib newFunctionWithName:@"reproject"];
        d.colorAttachments[0].pixelFormat=MTLPixelFormatRG16Float;d.colorAttachments[1].pixelFormat=MTLPixelFormatR8Unorm;
        pso=[device newRenderPipelineStateWithDescriptor:d error:NULL];CHECK(pso!=nil);
    }
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=t->raster;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    rp.colorAttachments[1].texture=t->coverage;rp.colorAttachments[1].loadAction=MTLLoadActionClear;rp.colorAttachments[1].storeAction=MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];[e setRenderPipelineState:pso];
    [e setFragmentTexture:depth atIndex:0];[e setFragmentBytes:&t->views[TEMPORAL_SP] length:sizeof(SmTemporalAux) atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e setFragmentTexture:depth atIndex:31];lamprec_replay(t->objects[TEMPORAL_SP],e);[e endEncoding];return t->raster;
}
static id<MTLRenderCommandEncoder> object_encoder(id<MTLRenderCommandEncoder> real,TemporalMotionTarget *target,unsigned variant,unsigned samples,
    const TemporalViewState *v,const TemporalPartPose *pose,int a2c){
    uint32_t vp[4]={0,0,W,H};if(!temporal_render_moved(v,pose,vp))return real;
    SmTemporalAux aux;float raster[2]={1.f/W,-1.f/H};temporal_render_constants(v,pose,1,raster,&aux);
    aux.viewport[0]=W;aux.viewport[1]=H;[device getDefaultSamplePositions:(MTLSamplePosition *)aux.sample_positions count:samples];
    if(reference_target)real=temporal_render_record(real,reference_target,TEMPORAL_SP,pipeline(variant,samples,2,a2c,0),&aux);
    return temporal_render_record(real,target,TEMPORAL_SP,pipeline(variant,samples,1,a2c,0),&aux);
}
static int destructible_pose(TemporalPartPose expected,TemporalPartPose *out) {
    temporal_render_request(0);temporal_render_request(1);temporal_scene_mode(0,6,0);
    TemporalViewKey key={.source=1,.depth=2,.width=W,.height=H,.samples=4,.format=115,
        .player=7,.camera=8,.mover=9,.rect={0,0,W,H}};
    int tracked=0;
    for(unsigned f=0;f<2;++f){
        TemporalMatrices m=camera(f?.2:-.1);temporal_view_capture(TEMPORAL_SP,&m,7,8,9);
        const TemporalViewState *v=temporal_view_accept(TEMPORAL_SP,&key);CHECK(v!=NULL);
        if(!v)return 0;
        memcpy(destructible_world,f?expected.current:expected.previous,64);
        Cpu c={.ecx=0x220000,.esp=0x2000};f_0073B9C0(&c);
        tracked=temporal_pose_resolve(destructible_token,TEMPORAL_SP,out);
        CHECK(temporal_pose_commit(TEMPORAL_SP,v->current.serial));
        if(!f){temporal_frame_boundary(1,6,0);temporal_pose_queue_reset();}
    }
    return tracked;
}
static void raster_case(unsigned variant,unsigned samples,unsigned scenario,int a2c){
    unsigned before=failures;
    TemporalPartPose destructible=part(.8,.1,.21,.3,-.2,-.17),tracked={0};
    int has_destructible=scenario==5?destructible_pose(destructible,&tracked):0;
    id<MTLTexture> colour=tex(MTLPixelFormatRGBA16Float,samples,W,H),z=tex(MTLPixelFormatDepth32Float,samples,W,H);
    id<MTLTexture> rz=samples>1?tex(MTLPixelFormatDepth32Float,1,W,H):z;
    TemporalMotionTarget *target=temporal_render_prepare(device,colour,z,serial++);
    CHECK(target && target->raster.sampleCount==1 && target->raster.pixelFormat==MTLPixelFormatRG16Float && target->resolved==target->raster);
    if(!target || target->raster.sampleCount!=1)return;
    TemporalViewState v=view_state(.2,-.1);uint32_t vp[4]={0,0,W,H};temporal_render_view(target,TEMPORAL_SP,&v,vp);
    TemporalMotionTarget reference={.raster=tex(MTLPixelFormatRG16Float,1,W,H),.coverage=tex(MTLPixelFormatR8Unorm,1,W,H)};
    reference.views[TEMPORAL_SP]=target->views[TEMPORAL_SP];reference_target=&reference;
    TemporalPartPose foreground=part(.8,.1,.21,.3,-.2,-.17),background=part(0,0,0,-.3,.1,.07);
    if(scenario==1)foreground=part(.8,0,0,.1,0,0);
    if(scenario==6)foreground=part(.8,0,0,.5,0,0); // object cancels camera: valid zero motion
    if(scenario==2)foreground=part(0,0,.31,0,0,-.24);
    if(scenario==3){foreground=part(-.7,.3,1.7,-.6,.4,.4);roll(foreground.current,.08);roll(foreground.previous,-.11);}
    const TemporalPartPose *pose=scenario==0?NULL:scenario==5?&destructible:&foreground;int edge=scenario==4 || a2c;
    id<MTLRenderPipelineState> p=pipeline(variant,samples,0,a2c,0);
    MTLRenderPassDescriptor *rp=scene_pass(colour,z,rz);
    CHECK(!rp.colorAttachments[4].texture); // scene has no motion attachment
    id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> real=[cb renderCommandEncoderWithDescriptor:rp];
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLess;dd.depthWriteEnabled=YES;
    [real setDepthStencilState:[device newDepthStencilStateWithDescriptor:dd]];
    // Static scenery is drawn once into the scene, with no motion recording.
    const TemporalPartPose *record_pose=scenario==5?(has_destructible?&tracked:NULL):edge?&background:pose;
    id<MTLRenderCommandEncoder> e=pose?object_encoder(real,target,variant,samples,&v,record_pose,a2c):real;
    draw(e,p,variant,&v,edge?&background:pose,edge?9:5,1,0,0,0,1);
    if(edge){
        e=object_encoder(real,target,variant,samples,&v,&foreground,a2c);
        draw(e,p,variant,&v,&foreground,5,a2c?(153.5/255+1e-6):1,.013,0,0,a2c);occlusion_checked++;
        // A farther object submitted last must not overwrite the visible object.
        e=object_encoder(real,target,variant,samples,&v,&background,a2c);
        draw(e,p,variant,&v,&background,12,1,0,0,0,1);
    }
    [real endEncoding];CHECK(lamprec_refused(target->objects[TEMPORAL_SP])==0);
    if(!pose)CHECK(lamprec_ops(target->objects[TEMPORAL_SP])==0);
    id<MTLBuffer> depth=read_depth(cb,z);
    id<MTLTexture> motion=object_resolve(cb,target,rz);CHECK(motion==target->raster);
    if(!motion)return;
    id<MTLBuffer> coverage=read_coverage(cb,target->coverage);
    id<MTLBuffer> result=read_texture(cb,consume(cb,target,rz));
    id<MTLBuffer> baseline=read_texture(cb,stored_combined(cb,&reference,rz));reference_target=NULL;complete(cb);
    float *depths=depth.contents;uint16_t *out=result.contents;unsigned stride=((W*4+255)&~255u)/2;
    MTLSamplePosition positions[4];[device getDefaultSamplePositions:positions count:samples];
    unsigned mixed=0,fg=0,bg=0;
    for(unsigned y=2;y<H-2;++y)for(unsigned x=2;x<W-2;++x){
        unsigned base=(y*W+x)*samples,best=0;
        for(unsigned i=0;i<samples;++i){if(depths[base+i]<depths[base+best])best=i;fg+=depths[base+i]<.94;bg+=depths[base+i]>=.94;}
        int is_fg=depths[base+best]<.94;
        for(unsigned i=1;i<samples;++i)if(fabs(depths[base+i]-depths[base])>2e-6){mixed++;break;}
        const TemporalPartPose *expected_pose=edge?(is_fg?&foreground:&background):pose;
        double nx=2*(x+(pose?positions[best].x:.5))/W-1-1.0/W;
        double ny=1-2*(y+(pose?positions[best].y:.5))/H+1.0/H;
        double expected[3];analytic(&v,expected_pose,edge && !is_fg?9:5,nx,ny,expected);
        CHECK(((uint8_t *)coverage.contents)[y*((W+255)&~255u)+x]==(pose?255:0));
        for(unsigned k=0;k<2;++k){
            double actual=half_value(out[y*stride+x*2+k]);close_value(actual,expected[k],0);
            double stored=half_value(((uint16_t *)baseline.contents)[y*stride+x*2+k]);
            ++reference_components;reference_exact+=actual==stored;
            max_reference_error=fmax(max_reference_error,fabs(actual-stored));close_value(actual,stored,0);
        }
    }
    if(edge && samples>1){CHECK(mixed>0 && fg>0 && bg>0);mixed_checked+=mixed;}
    if(a2c){CHECK(fg>0 && (samples==1 || bg>0));a2c_checked++;}
    variant_checked[variant]++;samples_checked[samples==1?0:samples==2?1:2]++;
    if(failures!=before)fprintf(stderr,"case variant=%u samples=%u scenario=%u a2c=%d failures=%u\n",variant,samples,scenario,a2c,failures-before);
}
static void contracts(void){
    temporal_render_request(0);for(unsigned i=0;i<1000;++i)shipping_guard();CHECK(scene_calls==0);
    CHECK(temporal_render_prepare(nil,nil,nil,1)==NULL);
    temporal_render_request(1);shipping_guard();CHECK(scene_calls==1);
    TemporalViewState v=view_state(0,0);SmTemporalAux a;TemporalPartPose pose=part(0,0,0,0,0,0);
    CHECK(!temporal_render_constants(NULL,NULL,1,NULL,&a) && !a.valid);
    v.history_valid=0;CHECK(!temporal_render_constants(&v,NULL,1,NULL,&a));v.history_valid=1;
    pose.history_valid=0;CHECK(!temporal_render_constants(&v,&pose,1,NULL,&a));pose.history_valid=1;
    CHECK(!temporal_render_constants(&v,&pose,0,NULL,&a));
    id<MTLTexture> c=tex(MTLPixelFormatRGBA16Float,4,W,H),z=tex(MTLPixelFormatDepth32Float,1,W,H);
    CHECK(temporal_render_prepare(device,c,c,serial++)==NULL);
    CHECK(temporal_render_prepare(device,c,z,serial++)==NULL);
    z=tex(MTLPixelFormatDepth32Float,4,W,H);TemporalMotionTarget *t=temporal_render_prepare(device,c,z,serial++);
    CHECK(t && t->raster.sampleCount==1 && t->raster.pixelFormat==MTLPixelFormatRG16Float);
    CHECK(temporal_render_resolve(device,[queue commandBuffer],t,z,TEMPORAL_SP)==nil);
    uint32_t vp[4]={0,0,3840,2160};v=view_state(.2,-.1);pose=part(0,0,0,0,0,0);
    CHECK(!temporal_render_moved(&v,&pose,vp)); // parked despite camera motion
    pose.current[12]=.000001f;CHECK(!temporal_render_moved(&v,&pose,vp));
    pose.current[12]=.01f;CHECK(temporal_render_moved(&v,&pose,vp));
    pose=part(0,0,0,0,0,.001);CHECK(temporal_render_moved(&v,&pose,vp));
    pose=part(0,0,0,0,0,0);roll(pose.current,.001);CHECK(temporal_render_moved(&v,&pose,vp));
    for(unsigned i=0;i<20;++i){
        pose=part(i*1e-8,-(double)i*1e-8,i*1e-9,0,0,0);
        CHECK(!temporal_render_moved(&v,&pose,vp));
        for(unsigned j=0;j<18;++j){
            double nx=(j%3-1.)*1.001,ny=((j/3)%3-1.)*1.001,depth=j<9?.5:100;
            double a[3],b[3];analytic(&v,&pose,depth,nx,ny,a);analytic(&v,NULL,depth,nx,ny,b);
            CHECK(fabs(a[0]-b[0])*vp[2]<=1./16 && fabs(a[1]-b[1])*vp[3]<=1./16);
        }
    }
}
static void scene_case(void){
    temporal_render_request(0);temporal_render_request(1);temporal_draw_target=NULL;temporal_draw_view=TEMPORAL_VIEW_COUNT;
    temporal_scene_mode(0,6,0);TemporalMatrices matrix=camera(0);temporal_view_capture(TEMPORAL_SP,&matrix,7,8,9);
    id<MTLTexture> c=tex(MTLPixelFormatRGBA16Float,2,W,H),z=tex(MTLPixelFormatDepth32Float,2,W,H);
    uint32_t vp[4]={0,0,W,H};unsigned ended=ended_passes;
    CHECK(shipping_scene(c,z,1,vp,1));CHECK(temporal_draw_target && temporal_draw_target->raster.sampleCount==1 && ended_passes==ended+1);
    CHECK(shipping_scene(c,z,1,vp,1) && ended_passes==ended+1);
    CHECK(!shipping_scene(c,z,1,vp,0) && temporal_draw_target==NULL && ended_passes==ended+2);
    CHECK(shipping_scene(c,z,1,vp,1) && ended_passes==ended+3);
    temporal_frame_boundary(1,6,0);temporal_view_capture(TEMPORAL_SP,&matrix,7,8,9);pass_dirty=0;
    CHECK(shipping_scene(c,z,2,vp,1));CHECK(ended_passes==ended+4 && pass_dirty && temporal_draw_target->frame==2);
    CHECK(temporal_view_state(TEMPORAL_SP)->history_valid);
    descriptor_entry=3;CHECK(!shipping_scene(c,z,2,vp,1));descriptor_entry=0;
    temporal_render_request(0);temporal_render_request(1);temporal_draw_target=NULL;temporal_draw_view=TEMPORAL_VIEW_COUNT;
}
static void __attribute__((unused)) diagnostics_case(void){
    id<MTLTexture> colour=tex(MTLPixelFormatRGBA16Float,1,W,H),z=tex(MTLPixelFormatDepth32Float,1,W,H);
    id<MTLCommandBuffer> init=[queue commandBuffer];
    MTLRenderPassDescriptor *rp=scene_pass(colour,z,z);rp.depthAttachment.clearDepth=.9;
    id<MTLRenderCommandEncoder> fill=[init renderCommandEncoderWithDescriptor:rp];[fill endEncoding];complete(init);
    TemporalViewState v=view_state(.2,-.1);TemporalPartPose pose=part(.8,0,0,.1,0,0);uint32_t vp[4]={0,0,W,H};
    diag_on=1;
    TemporalMotionTarget *target=temporal_render_prepare(device,colour,z,serial++);temporal_render_view(target,TEMPORAL_SP,&v,vp);
    id<MTLRenderCommandEncoder> rec=object_encoder(nil,target,2,1,&v,&pose,0);
    draw(rec,pipeline(2,1,0,0,0),2,&v,&pose,5,1,0,0,0,1);
    [rec drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3 instanceCount:3];
    CHECK(target->drawn_instances[TEMPORAL_SP]==4);
    id<MTLCommandBuffer> cb=[queue commandBuffer];CHECK(object_resolve(cb,target,z)!=nil);complete(cb);
    CHECK(diag_messages==1 && strstr(diag_message,"instances=4 "));
    const char *gpu=strstr(diag_message,"gpu_ms=");CHECK(gpu && strtod(gpu+7,NULL)>0);
    printf("motion diag: %s\n",diag_message);
    target=temporal_render_prepare(device,colour,z,serial++);temporal_render_view(target,TEMPORAL_SP,&v,vp);
    CHECK(target->drawn_instances[TEMPORAL_SP]==0);
    cb=[queue commandBuffer];CHECK(object_resolve(cb,target,z)!=nil);
    id<MTLBuffer> mask=read_coverage(cb,target->coverage);complete(cb);
    CHECK(diag_messages==1); // deterministic clock: no elapsed diagnostic second
    for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x)CHECK(!((uint8_t *)mask.contents)[y*((W+255)&~255u)+x]);
    diag_clock+=1000000000ull;
    target=temporal_render_prepare(device,colour,z,serial++);temporal_render_view(target,TEMPORAL_SP,&v,vp);
    cb=[queue commandBuffer];CHECK(object_resolve(cb,target,z)!=nil);complete(cb);
    CHECK(diag_messages==2 && strstr(diag_message,"instances=0 "));
    printf("motion diag: %s; off_callbacks=0 throttle=PASS\n",diag_message);
    diag_on=0;
}
static int compare_double(const void *a,const void *b){double x=*(const double *)a,y=*(const double *)b;return (x>y)-(x<y);}
// Converted nearest-sample case: motion consumes minimum-resolved depth once;
// distinct per-sample velocities are intentionally dropped by the 1x design.
static void resolved_depth_case(void){
    id<MTLTexture> z=tex(MTLPixelFormatDepth32Float,4,W,H),rz=tex(MTLPixelFormatDepth32Float,1,W,H);
    id<MTLTexture> colour=tex(MTLPixelFormatRGBA16Float,4,W,H);
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 fill(uint i [[vertex_id]]){return float4(float2((i<<1)&2,i&2)*2-1,0,1); }\nstruct D{float z [[depth(any)]];};fragment D depth_value(uint i [[sample_id]]) {D d;d.z=i==0?.9f:i<3?.5f:1.f;return d;}\n";
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];id<MTLLibrary> lib=library(source);
    pd.vertexFunction=[lib newFunctionWithName:@"fill"];pd.fragmentFunction=[lib newFunctionWithName:@"depth_value"];
    pd.rasterSampleCount=4;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
    id<MTLRenderPipelineState> depth_pso=[device newRenderPipelineStateWithDescriptor:pd error:NULL];CHECK(depth_pso!=nil);
    id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:scene_pass(colour,z,rz)];
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthWriteEnabled=YES;dd.depthCompareFunction=MTLCompareFunctionAlways;
    [e setDepthStencilState:[device newDepthStencilStateWithDescriptor:dd]];[e setRenderPipelineState:depth_pso];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    TemporalMotionTarget *target=temporal_render_prepare(device,colour,z,serial++);
    TemporalViewState v=view_state(.2,-.1);uint32_t vp[4]={0,0,W,H};temporal_render_view(target,TEMPORAL_SP,&v,vp);
    id<MTLTexture> motion=temporal_render_resolve(device,cb,target,rz,TEMPORAL_SP);CHECK(motion!=nil);
    if(!motion){complete(cb);return;}
    id<MTLBuffer> result=read_texture(cb,consume(cb,target,rz));complete(cb);uint16_t *h=result.contents;
    unsigned stride=((W*4+255)&~255u)/2;
    double depth=v.current.matrices.projection[14]/(.5-v.current.matrices.projection[10]);
    for(unsigned y=2;y<H-2;++y)for(unsigned x=2;x<W-2;++x){
        double expected[3];analytic(&v,NULL,depth,2*(x+.5)/W-1-1./W,1-2*(y+.5)/H+1./H,expected);
        for(unsigned k=0;k<2;++k)close_value(half_value(h[y*stride+x*2+k]),expected[k],0);
    }
}
static MTLScissorRect vehicle_area(unsigned car,unsigned part_id){
    unsigned cell_w=W/6,cell_h=H/5,x=(car%6)*cell_w,y=(car/6)*cell_h;
    if(!part_id)return (MTLScissorRect){x+cell_w*8/100,y+cell_h*17/100,cell_w*85/100,cell_h*65/100};
    unsigned wheel=part_id-1;
    return (MTLScissorRect){x+cell_w*(wheel%2?72:16)/100,y+cell_h*(wheel/2?80:0)/100,cell_w*12/100,cell_h*20/100};
}
static void benchmark_scene(unsigned moving){
    W=3840;H=2160;enum {N=31};
    id<MTLTexture> colour=tex(MTLPixelFormatRGBA16Float,4,W,H),z=tex(MTLPixelFormatDepth32Float,4,W,H),rz=tex(MTLPixelFormatDepth32Float,1,W,H);
    TemporalViewState v=view_state(.2,-.1);uint32_t vp[4]={0,0,W,H};
    id<MTLRenderPipelineState> scene=pipeline(2,4,0,0,0),motion=pipeline(2,4,1,0,0);double times[2][N];
    // Thirty vehicle proxies, each one body and four articulated wheels. Their
    // screen areas are declared, rather than inferred from any game capture.
    // Body: 85% x 65% of a cell. Four wheels: 12% x 20%, beside the body.
    // Nine moving = player + six traffic + two cops; the other 21 are parked.
    id<MTLCommandBuffer> init=[queue commandBuffer];id<MTLRenderCommandEncoder> real=[init renderCommandEncoderWithDescriptor:scene_pass(colour,z,rz)];
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionAlways;dd.depthWriteEnabled=YES;
    [real setDepthStencilState:[device newDepthStencilStateWithDescriptor:dd]];draw(real,scene,2,&v,NULL,9,1,0,0,0,1);
    for(unsigned car=0;car<30;++car)for(unsigned part_id=0;part_id<5;++part_id){
        TemporalPartPose pose=part(.8,.1,0,.8,.1,0);if(part_id)roll(pose.current,.08);
        [real setScissorRect:vehicle_area(car,part_id)];draw(real,scene,2,&v,&pose,5,1,0,0,0,1);
    }
    [real endEncoding];complete(init);
    unsigned retained=0,skipped=0;double coverage=0;
    for(unsigned j=0;j<N+6;++j)for(unsigned k=0;k<2;++k){unsigned mode=(j+k)%2;
        TemporalMotionTarget *target=temporal_render_prepare(device,colour,z,serial++);temporal_render_view(target,TEMPORAL_SP,&v,vp);
        retained=skipped=0;coverage=0;
        for(unsigned car=0;car<30;++car)for(unsigned part_id=0;part_id<5;++part_id){
            TemporalPartPose pose=part(.8,.1,0,car<moving?.1:.8,car<moving?0:.1,0);
            if(part_id){roll(pose.current,.08);roll(pose.previous,car<moving?-.11:.08);}
            // The production draw hook stages the pose before deciding to replay.
            if(!temporal_render_moved(&v,&pose,vp)){++skipped;continue;}
            SmTemporalAux aux;float raster[2]={1.f/W,-1.f/H};temporal_render_constants(&v,&pose,1,raster,&aux);
            aux.viewport[0]=W;aux.viewport[1]=H;[device getDefaultSamplePositions:(MTLSamplePosition *)aux.sample_positions count:4];
            id<MTLRenderCommandEncoder> rec=temporal_render_record(nil,target,TEMPORAL_SP,motion,&aux);
            MTLScissorRect area=vehicle_area(car,part_id);
            [rec setViewport:(MTLViewport){0,0,W,H,0,1}];[rec setScissorRect:area];
            draw(rec,scene,2,&v,&pose,5,1,0,0,0,1);++retained;
            coverage+=(double)area.width*area.height/(W*H);
        }
        CHECK(retained==moving*5 && skipped==(30-moving)*5);
        id<MTLCommandBuffer> cb=[queue commandBuffer];
        clear_vectors_control=mode;CHECK(object_resolve(cb,target,rz)==target->raster);clear_vectors_control=0;
        complete(cb);if(j>=6)times[mode][j-6]=(cb.GPUEndTime-cb.GPUStartTime)*1000;
    }
    for(unsigned i=0;i<2;++i)qsort(times[i],N,sizeof(double),compare_double);
    printf("motion cost: scene=%s device=%s 3840x2160 scene_samples=4 motion_samples=1 N=%u moving_cars=%u bodies=%u wheels=%u skipped=%u reprojection_passes=0 object_raster_median_ms=%.6f clear_vectors_control_ms=%.6f atlas_MB=%.4f moving_coverage=%.6f\n",
        moving==9?"realistic":"worst",device.name.UTF8String,N,moving,moving,moving*4,skipped,times[0][N/2],times[1][N/2],3840.*2160*5/1e6,coverage);
    CHECK(times[0][N/2]>0 && times[1][N/2]>0);
}
static void benchmark(void){benchmark_scene(9);benchmark_scene(30);}
static SmResult translate_file(const char *path){
    FILE *f=fopen(path,"rb");if(!f){fprintf(stderr,"missing shader %s\n",path);exit(3);}
    fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);uint32_t *tokens=malloc(bytes);
    if(fread(tokens,1,bytes,f)!=(size_t)bytes)exit(2);fclose(f);
    SmResult r;SmOptions o;sm_default_options(&o);
    if(sm_translate(tokens,bytes/4,&o,&r)){fprintf(stderr,"translate %s\n",r.error);exit(2);}
    uint32_t *fogged=fog_patch(tokens,(uint32_t)bytes/4);
    if(fogged){
        uint64_t original=r.program_key;int rigid=mat_temporal_static(original);sm_result_free(&r);
        CHECK(sm_translate(fogged,bytes/4,&o,&r)==0);CHECK(r.program_key!=original);
        if(strstr(path,"/world/")){CHECK(rigid);CHECK(!mat_temporal_static(r.program_key));}
        free(fogged);
    }
    free(tokens);return r;
}
static void sources(void){
    vertex_reflection[0]=translate_file("host/shaders/testdata/world/t0_world_p0_vs_1_1.bin");
    pixel_reflection[0]=translate_file("host/shaders/testdata/world/t0_world_p0_ps_1_1.bin");
    vertex_source[0]=@(vertex_reflection[0].msl);pixel_source[0]=@(pixel_reflection[0].msl);
    vertex_reflection[1]=translate_file("host/shaders/testdata/car/t0_car_p0_vs_1_1.bin");vertex_source[1]=@(vertex_reflection[1].msl);
    pixel_source[1]=@(car_paint_msl);
    FFShader ff={0};ff_build(&ff,1,0,0,1,1,1,1,1,1);SmOptions o;sm_default_options(&o);
    CHECK(!sm_translate(ff.vs.tokens,ff.vs.ntokens,&o,&vertex_reflection[2]));CHECK(!sm_translate(ff.ps.tokens,ff.ps.ntokens,&o,&pixel_reflection[2]));
    free(ff.vs.tokens);free(ff.ps.tokens);vertex_source[2]=@(vertex_reflection[2].msl);pixel_source[2]=@(pixel_reflection[2].msl);
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:1 height:1 mipmapped:NO];
    d.usage=MTLTextureUsageShaderRead;d.storageMode=MTLStorageModeShared;uint32_t white=0xFFFFFFFFu;
    dummy2d=[device newTextureWithDescriptor:d];[dummy2d replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&white bytesPerRow:4];
    d.textureType=MTLTextureTypeCube;dummycube=[device newTextureWithDescriptor:d];for(unsigned i=0;i<6;++i)[dummycube replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:i withBytes:&white bytesPerRow:4 bytesPerImage:4];
    d.textureType=MTLTextureType3D;d.depth=1;dummy3d=[device newTextureWithDescriptor:d];[dummy3d replaceRegion:MTLRegionMake3D(0,0,0,1,1,1) mipmapLevel:0 slice:0 withBytes:&white bytesPerRow:4 bytesPerImage:4];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;sampler=[device newSamplerStateWithDescriptor:sd];
}

int main(int argc,char **argv){@autoreleasepool{
    device=MTLCreateSystemDefaultDevice();if(!device){puts("SKIP: Metal device absent");return 3;}queue=[device newCommandQueue];
    sources();
    contracts();scene_case();resolved_depth_case();
#ifdef TEMPORAL_TEST_UNRESOLVED_DEPTH
    printf("motion raster: checks=%u failures=%u unresolved-depth control\n",checks,failures);
    return failures?1:0;
#endif
    // Convert 2x/4x analytic raster cases to 1x: the consumer has one motion
    // value at the pixel centre, so sample-position and mixed/A2C winners no
    // longer define motion. Scene MSAA resolve is checked independently above.
    for(unsigned variant=0;variant<3;++variant)for(unsigned scene=0;scene<7;++scene)raster_case(variant,1,scene,0);
    raster_case(2,1,4,1);
#ifndef TEMPORAL_TEST_MSAA_TARGET
    CHECK(variant_checked[0]==7 && variant_checked[1]==7 && variant_checked[2]==8);
    CHECK(samples_checked[0]==22 && samples_checked[1]==0 && samples_checked[2]==0);
    CHECK(a2c_checked==1 && occlusion_checked==4);
#endif
#ifndef TEMPORAL_TEST_MSAA_TARGET
    diagnostics_case();
#endif
    if(argc>1 && !strcmp(argv[1],"--cost"))benchmark();
    printf("motion reference: components=%u exact=%u max_error=%.9g\n",reference_components,reference_exact,max_reference_error);
    printf("motion raster: checks=%u failures=%u variants=%u/%u/%u samples=%u/%u/%u A2C=%u mixed=%u max_uv_error=%.9g max_depth_error=%.9g\n",checks,failures,variant_checked[0],variant_checked[1],variant_checked[2],samples_checked[0],samples_checked[1],samples_checked[2],a2c_checked,mixed_checked,max_uv_error,max_depth_error);
    return failures?1:0;
}}
