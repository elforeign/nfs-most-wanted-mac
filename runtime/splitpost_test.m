// Review: 1,313 recording checks / 0 failures; two PC-local lens submissions.
// Missing-parent / missing-SP-wait controls fail 2 / 24 checks; eight SP waits, zero split waits.
// Opt-in: 1,425 checks / 0 failures, 12 startup waits; forced wait/oldest/pending read/reuse fail 49/1/22/44.
// Missing startup wait/black readiness/stale epoch readiness fail 32/13/8 checks.
// Two six-frame SP snapshots match pre-split renderer (57,396 shader-consumed command/readback bytes).
// Recording encoder companion; no GPU/device creation. Production renderer is included by the Python runner.
#import "render_internal.h"
#include <stdarg.h>
#include <math.h>
static unsigned checks,failures,copies,composites,lens,waits,owner,single_testing,pc_lens_local;
uint64_t diag_idle_ns[IDLE_N];
static FILE *post_snapshot;
static uint32_t height=721;
#define CHECK(x) do {++checks;if(!(x)){++failures;if(failures<12)printf("FAIL %d %s\n",__LINE__,#x);}}while(0)
uint8_t *G;
int diag_on,gpt_on,pass_dirty,render_static_made;
const char *diag_dir="/tmp";
int x360_post_ssao_enabled(void){return 0;}
int postfx_look_variant(const char *s){(void)s;return 0;}
void render_soft_smoke_proof_finish(void){} // proof is inactive in this recording-only fixture
void render_soft_smoke_late(id<MTLTexture> scene){(void)scene;}
id<MTLTexture> render_soft_smoke_layer(void){return nil;}
void render_soft_smoke_layer_applied(void){}
void render_soft_smoke_late_finish(id<MTLTexture> scene){(void)scene;}
id<MTLTexture> render_ssao_coverage(id<MTLTexture> depth,int allow_cuts){(void)allow_cuts;(void)depth;CHECK(0);return nil;}
void diag_png(const char *path,const void *p,uint32_t w,uint32_t h,uint32_t pitch,int alpha){
 (void)path;(void)p;(void)w;(void)h;(void)pitch;(void)alpha;CHECK(0);
}
uint64_t frame_serial=1,done_serial;
Device dev;
id<MTLDevice> mtl;
id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d, const char *pass, NSError **e) {
    (void)pass; return [mtl newRenderPipelineStateWithDescriptor:d error:e];
}
id<MTLCommandBuffer> cmd;
id<MTLTexture> backbuffer_tex,present_tex,autodepth_tex;
RenderTex bb_rt,ad_rt;
const char *gpt_phase;
static Obj objects[16];
static RenderTex textures[16];
Obj *obj(uint32_t h){return h<16?&objects[h]:NULL;}
RenderTex *gpu_tex(Obj *o){return o?(RenderTex *)o->gpu:NULL;}
SURFACE_RT_PRODUCTION
id<MTLTexture> surface_target(uint32_t h,NSUInteger *level,NSUInteger *slice){*level=*slice=0;RenderTex*r=h<16?&textures[h]:NULL;return r?(r->ms?:r->tex):nil;}
int in_flight(uint64_t serial){return serial>done_serial;}
void wait_serial(uint64_t serial){++waits;CHECK(single_testing);CHECK(serial<frame_serial);done_serial=serial;}
void end_pass(void){}
void ensure_cmd(void){}
void msaa_resolve(RenderTex*r){(void)r;}
id<MTLTexture> render_lamp_source(void){return textures[5].tex;}
id<MTLTexture> render_lamp_source_view(uint32_t view){CHECK(view==(single_testing?0:(owner?0x9196C0u:0x919650u)));return render_lamp_source();}
void diag_event(const char*s,...){(void)s;}
uint64_t host_ticks(void){return 1;}
void gpt_render(MTLRenderPassDescriptor *r,const char*s,...){(void)r;(void)s;}
void gpt_render_auto(MTLRenderPassDescriptor*r,const char*s){(void)r;(void)s;}
void gpt_name(id<MTLCommandEncoder>e){(void)e;}
int texdump_frame(void){return 0;}
void texdump(uint32_t h,id<MTLTexture>t){(void)h;(void)t;}

@interface TTexture:NSObject
@property NSUInteger width,height,sampleCount,arrayLength,usage;
@property MTLPixelFormat pixelFormat;
@property unsigned tag;
- (void)replaceRegion:(MTLRegion)r mipmapLevel:(NSUInteger)l withBytes:(const void*)b bytesPerRow:(NSUInteger)n;
@end
@implementation TTexture
- (void)replaceRegion:(MTLRegion)r mipmapLevel:(NSUInteger)l withBytes:(const void*)b bytesPerRow:(NSUInteger)n{(void)r;(void)l;(void)b;(void)n;}
@end
@interface TBuffer:NSObject
@property NSMutableData *data;
@property uint64_t serial;
- (void*)contents;
@end
@implementation TBuffer
- (void*)contents{return self.data.mutableBytes;}
@end
@interface TFunction:NSObject
@property NSString *name;
@property MTLFunctionType functionType;
@end
@implementation TFunction
@end
@interface TLibrary:NSObject
- (id)newFunctionWithName:(NSString*)name;
@end
@implementation TLibrary
- (id)newFunctionWithName:(NSString*)name{TFunction*f=[TFunction new];f.name=name;f.functionType=[name hasPrefix:@"vs_"]?MTLFunctionTypeVertex:MTLFunctionTypeFragment;return f;}
@end
@interface TPipeline:NSObject
@property NSString *name;
@end
@implementation TPipeline
@end
@interface TDevice:NSObject
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)d;
- (id)newBufferWithLength:(NSUInteger)n options:(NSUInteger)o;
- (id)newLibraryWithSource:(NSString*)src options:(id)o error:(NSError**)err;
- (id)newSamplerStateWithDescriptor:(id)d;
- (id)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor*)d error:(NSError**)err;
@end
@implementation TDevice
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)d{TTexture*t=[TTexture new];t.width=d.width;t.height=d.height;t.pixelFormat=d.pixelFormat;t.sampleCount=d.sampleCount;t.usage=d.usage;t.arrayLength=d.arrayLength;return t;}
- (id)newBufferWithLength:(NSUInteger)n options:(NSUInteger)o{(void)o;TBuffer*b=[TBuffer new];b.data=[NSMutableData dataWithLength:n];return b;}
- (id)newLibraryWithSource:(NSString*)src options:(id)o error:(NSError**)err{(void)o;(void)err;CHECK([src containsString:@"fs_vt"]||[src containsString:@"fs_drop"]);if([src containsString:@"float4 depth_uv;"])CHECK([src containsString:@"clamp(k.depth_uv.xy + uv * k.depth_uv.zw, k.depth_bounds.xy, k.depth_bounds.zw)"]);return [TLibrary new];}
- (id)newSamplerStateWithDescriptor:(id)d{(void)d;return [NSObject new];}
- (id)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor*)d error:(NSError**)err{(void)err;TPipeline*p=[TPipeline new];p.name=[(id)d.fragmentFunction name];return p;}
@end
@interface TEncoder:NSObject {
 id input[8];
 NSData *constants,*vertices;
 TPipeline *pipeline;
 MTLViewport viewport;
 MTLScissorRect scissor;
}
@property MTLRenderPassDescriptor *rp;
- (void)setRenderPipelineState:(id)p;
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i;
- (void)setFragmentSamplerState:(id)s atIndex:(NSUInteger)i;
- (void)setFragmentBytes:(const void*)b length:(NSUInteger)n atIndex:(NSUInteger)i;
- (void)setVertexBytes:(const void*)b length:(NSUInteger)n atIndex:(NSUInteger)i;
- (void)setViewport:(MTLViewport)v;
- (void)setScissorRect:(MTLScissorRect)r;
- (void)drawPrimitives:(NSUInteger)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n;
- (void)drawPrimitives:(NSUInteger)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)c;
- (void)endEncoding;
@end
@implementation TEncoder
- (void)setRp:(MTLRenderPassDescriptor*)r{_rp=r;TTexture*t=(id)r.colorAttachments[0].texture;viewport=(MTLViewport){0,0,t.width,t.height,0,1};scissor=(MTLScissorRect){0,0,t.width,t.height};}
- (void)setRenderPipelineState:(id)p{pipeline=p;}
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i{input[i]=t;}
- (void)setFragmentSamplerState:(id)s atIndex:(NSUInteger)i{(void)s;(void)i;}
- (void)setFragmentBytes:(const void*)b length:(NSUInteger)n atIndex:(NSUInteger)i{(void)i;constants=[NSData dataWithBytes:b length:n];}
- (void)setVertexBytes:(const void*)b length:(NSUInteger)n atIndex:(NSUInteger)i{(void)i;vertices=[NSData dataWithBytes:b length:n];}
- (void)setViewport:(MTLViewport)v{viewport=v;}
- (void)setScissorRect:(MTLScissorRect)r{scissor=r;}
- (void)drawPrimitives:(NSUInteger)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n{
 (void)t;(void)s;(void)n;TTexture*out=(id)self.rp.colorAttachments[0].texture;
 if(single_testing){
  if(post_snapshot){
   const char *name=pipeline.name.UTF8String;fwrite(name,1,strlen(name)+1,post_snapshot);
   // The unchanged SP shader consumes 44 floats; split-only trailing constants
   // are outside its constant struct and cannot affect this snapshot.
   size_t used=[pipeline.name hasPrefix:@"fs_vt"]?44*sizeof(float):constants.length;
   uint64_t sizes[]={out.width,out.height,self.rp.colorAttachments[0].loadAction,used};
   fwrite(sizes,1,sizeof sizes,post_snapshot);fwrite(constants.bytes,1,used,post_snapshot);
  }
  TTexture*source=input[0];out.tag=source.tag;return;
 }
 if([pipeline.name hasPrefix:@"fs_vt"]){
  ++composites;TTexture*scene=input[0];CHECK(scene.width==1280&&scene.height==(owner?height-360:360));
  CHECK(scene.tag==(owner?200:25));CHECK(input[5]==textures[owner?4:3].tex);
  CHECK(viewport.originY==(owner?360:0));CHECK(viewport.height==(owner?height-360:360));
  CHECK(scissor.y==(owner?360:0)&&scissor.height==(owner?height-360:360));
  CHECK(self.rp.colorAttachments[0].loadAction==MTLLoadActionLoad);
  const float*k=constants.bytes;CHECK(constants.length==sizeof(float)*52);
  CHECK(fabsf(k[45]-(owner?360.f/height:0))<1e-6);
  CHECK(fabsf(k[49]-(owner?360.5f/height:.5f/height))<1e-6);
  CHECK(fabsf(k[51]-(owner?(height-.5f)/height:359.5f/height))<1e-6);
  if([pipeline.name hasSuffix:@"lamp"]){TTexture*lamp=input[7];CHECK(lamp.tag==(owner?200:25));}
 }else if([pipeline.name isEqual:@"fs_downscale"]||[pipeline.name isEqual:@"fs_dofdown"]||[pipeline.name isEqual:@"fs_lamp_source"]){
  TTexture*scene=input[0];CHECK(scene.width==1280&&scene.height==(owner?height-360:360));CHECK(scene.tag==(owner?200:25));
  if([pipeline.name isEqual:@"fs_lamp_source"]){TTexture*lamp=input[1];CHECK(lamp.height==scene.height&&lamp.tag==scene.tag);}
 }
 TTexture*source=input[0];out.tag=source.tag;
 if(self.rp.colorAttachments[1].texture)((TTexture*)(id)self.rp.colorAttachments[1].texture).tag=source.tag;
}
- (void)drawPrimitives:(NSUInteger)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)c{
 (void)t;(void)s;(void)n;++lens;CHECK(c==(owner?2:1));TTexture*scene=input[1];
 CHECK(scene.height==(owner?height-360:360));CHECK(scene.tag==(owner?200:25));
 CHECK(viewport.originY==(pc_lens_local?0:(owner?360:0)));CHECK(scissor.y==(pc_lens_local?0:(owner?360:0)));
 const float*q=vertices.bytes;float aspect=1280.f/(owner?height-360:360);
 CHECK(fabsf((q[2]-q[0])*aspect-(q[3]-q[1]))<1e-5);
}
- (void)endEncoding{}
@end
@interface TBlit:NSObject
- (void)copyFromTexture:(TTexture*)src sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toTexture:(TTexture*)dst destinationSlice:(NSUInteger)ds destinationLevel:(NSUInteger)dl destinationOrigin:(MTLOrigin)d;
- (void)copyFromTexture:(TTexture*)src sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toBuffer:(TBuffer*)dst destinationOffset:(NSUInteger)d destinationBytesPerRow:(NSUInteger)row destinationBytesPerImage:(NSUInteger)image;
- (void)endEncoding;
@end
@implementation TBlit
- (void)copyFromTexture:(TTexture*)src sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toTexture:(TTexture*)dst destinationSlice:(NSUInteger)ds destinationLevel:(NSUInteger)dl destinationOrigin:(MTLOrigin)d{
 (void)s;(void)l;(void)ds;(void)dl;(void)d;++copies;CHECK(o.x+z.width<=src.width&&o.y+z.height<=src.height);
 CHECK(z.width==dst.width&&z.height==dst.height);CHECK(o.y==(pc_lens_local?0:(owner?360:0)));CHECK(z.height==(owner?height-360:360));
 dst.tag=pc_lens_local?src.tag:(o.y?200:25);
}
- (void)copyFromTexture:(TTexture*)src sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l sourceOrigin:(MTLOrigin)o sourceSize:(MTLSize)z toBuffer:(TBuffer*)dst destinationOffset:(NSUInteger)d destinationBytesPerRow:(NSUInteger)row destinationBytesPerImage:(NSUInteger)image{
 (void)s;(void)l;(void)o;(void)z;(void)d;(void)row;(void)image;CHECK(!dst.serial || dst.serial<=done_serial);dst.serial=frame_serial;uint32_t*p=dst.contents;for(unsigned i=0;i<4096;++i)p[i]=src.tag;
}
- (void)endEncoding{}
@end
@interface TCommand:NSObject
- (id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)r;
@end
@implementation TCommand
- (id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)r{TEncoder*e=[TEncoder new];e.rp=r;return e;}
@end
id<MTLBlitCommandEncoder> gpt_blit(id<MTLCommandBuffer>c,const char*s,...){(void)c;(void)s;return(id)[TBlit new];}

#include "post_render.inc"

static id<MTLTexture> texture(unsigned w,unsigned h,MTLPixelFormat format){MTLTextureDescriptor*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;return[mtl newTextureWithDescriptor:d];}
int main(void){@autoreleasepool{ @try {
 mtl=(id)[TDevice new];cmd=(id)[TCommand new];
 for(unsigned i=0;i<16;++i){objects[i].gpu=&textures[i];textures[i].tex=texture(1280,height,i==3||i==4?MTLPixelFormatDepth32Float:MTLPixelFormatBGRA8Unorm);}
 textures[6].tex=texture(32,32,MTLPixelFormatRGBA8Unorm);textures[7].tex=texture(128,32,MTLPixelFormatRGBA8Unorm);
 autodepth_tex=textures[3].tex;
 X360PostParams p={.middle_gray=.14f,.adapted_lum=.2f,.znear=.5f,.zfar=10000,.dof=1,.lamp_glow=.5f};
 p.coeffs[0][0]=1;p.desaturation=p.exposure=1;
 for(unsigned pass=0;pass<2;++pass){height=pass?719:721;
  for(unsigned i=1;i<=5;++i)((TTexture*)(id)textures[i].tex).height=height;
  for(unsigned step=1;step<=6;++step){frame_serial=pass*10+step;done_serial=frame_serial-1;
   for(owner=0;owner<2;++owner){
    X360PostView v={.source_view=owner?0x9196C0:0x919650,.source_surface=1,.depth_surface=owner?4:3,.generation=pass+1};
    uint32_t r[4]={0,owner?360:0,1280,owner?height-360:360};memcpy(v.source,r,16);memcpy(v.destination,r,16);memcpy(v.depth,r,16);memcpy(v.lamp,r,16);
    float l=render_x360_post_view(0,2,0,&p,&v);CHECK(step==1?l==-1:fabsf(l-(owner?200:25)*4096*9.536743e-07)<1e-5);
    RainDrop d[2]={{.x=.2f,.y=.4f,.life=.5f,.size=1},{.x=.7f,.y=.2f,.life=.8f,.size=1}};
    CHECK(render_x360_raindrops_view(2,d,owner?2:1,6,7,0,&v)==(owner?2:1));
   }
  }
 }
 // Saturated ring: repeated calls in the same unsubmitted frame cannot wait or reuse an in-flight slot.
 owner=0;height=719;done_serial=0;frame_serial=20;
 X360PostView v={.source_view=0x919650,.source_surface=1,.depth_surface=3,.generation=20,.source={0,0,1280,360},.destination={0,0,1280,360},.depth={0,0,1280,360},.lamp={0,0,1280,360}};
 for(unsigned i=0;i<8;++i)CHECK(render_x360_post_view(0,2,0,&p,&v)==-1);
 unsigned pending=0;for(unsigned i=0;i<X_LUM_SLOTS;++i)pending+=x_resources[1].readback[i].pending;CHECK(pending==3);CHECK(waits==0);
 // The PC chain destination is a local, non-MSAA texture's level-0 surface.
 for(owner=0;owner<2;++owner){
  pc_lens_local=1;objects[8].parent=9;objects[9].gpu=&textures[8];
  textures[8].tex=texture(1280,owner?height-360:360,MTLPixelFormatBGRA8Unorm);
  ((TTexture*)(id)textures[8].tex).tag=owner?200:25;
  CHECK(surface_rt(8)==NULL);
  v.source_view=owner?0x9196C0:0x919650;v.destination[1]=0;v.destination[3]=owner?height-360:360;
  RainDrop drops[2]={{.life=1,.size=1},{.life=1,.size=1}};
  CHECK(render_x360_raindrops_view(8,drops,owner?2:1,6,7,0,&v)==(owner?2:1));
 }
 pc_lens_local=0;single_testing=1;owner=0;((TTexture*)(id)textures[1].tex).tag=25;
 if(x_lum_nowait()) {
  // Black loading readbacks must not release the startup wait. Repeat after a
  // reset with an old-epoch scene readback still pending: it cannot arm this epoch.
  memset(&x_resources[0],0,sizeof x_resources[0]);
  for(unsigned epoch=0;epoch<2;++epoch){
   if(epoch)render_x360_post_reset();
   unsigned before=waits;
   for(unsigned i=0;i<6;++i){
    frame_serial=40+epoch*10+i;done_serial=frame_serial-2;
    ((TTexture*)(id)textures[1].tex).tag=i<4?0:25;
    float m=render_x360_post(1,2,0,&p);
    CHECK(i==0?m==-1:fabsf(m-(i==5?25:0)*4096*9.536743e-07)<1e-5);
    CHECK(waits==before+i+(epoch?1:0));
   }
  }
  // Present guarantees frame N-2 complete, while N-1 may still be pending.
  // Every frame must submit into free storage, consume the newest completed
  // sample, and leave the pending previous frame untouched.
  memset(&x_resources[0],0,sizeof x_resources[0]);
  unsigned before=waits;
  for(unsigned i=0;i<16;++i){
   frame_serial=100+i;done_serial=frame_serial-2;
   ((TTexture*)(id)textures[1].tex).tag=25+i;
   float m=render_x360_post(1,2,0,&p);
   CHECK(i==0||i==2?m==-1:fabsf(m-(i==1?25:25+i-2)*4096*9.536743e-07)<1e-5);
   unsigned current=0;
   for(unsigned j=0;j<X_LUM_SLOTS;++j)current+=x_resources[0].readback[j].pending&&x_resources[0].readback[j].serial==frame_serial;
   CHECK(current==1);CHECK(waits==before+(i?1:0));
  }
  before=waits;
  // Delayed completion beyond the Present bound: full ring must never wait,
  // overwrite pending storage, or invent a measurement. Resume at newest.
  frame_serial=120;done_serial=0;
  for(unsigned i=0;i<8;++i)CHECK(render_x360_post(1,2,0,&p)==-1);
  CHECK(waits==before);
  done_serial=120;frame_serial=121;
  CHECK(fabsf(render_x360_post(1,2,0,&p)-40*4096*9.536743e-07)<1e-5);
  CHECK(waits==before);
 } else {
 // With single-player (unset / 1), the previous frame is initially incomplete.
 // Each call must wait, consume that frame, and reuse the now-completed storage.
 for(unsigned mode=0;mode<2;++mode){
  if(mode)setenv("NFS_PADS","1",1);else unsetenv("NFS_PADS");
  frame_serial+=10;done_serial=frame_serial-1;
  unsigned before=waits;
  CHECK(render_x360_post(1,2,0,&p)==-1);
  for(unsigned i=0;i<4;++i){
   ++frame_serial;float measured=render_x360_post(1,2,0,&p);
   CHECK(fabsf(measured-25*4096*9.536743e-07)<1e-5);
   CHECK(waits==before+i+1);CHECK(done_serial==frame_serial-1);
  }
  memset(&x_resources[0],0,sizeof x_resources[0]);
 }
 }
 printf("encoder: %u checks / %u failures; %u composites, %u lens, %u crops, %u waits\n",checks,failures,composites,lens,copies,waits);
 return failures?1:0;
} @catch(NSException *e) {printf("recorder exception: %s\n",e.description.UTF8String);return 2;} }}
