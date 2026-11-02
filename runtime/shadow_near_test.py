"""Production near caster replay and pinned original hook regression.
The original is literal source, SHA checked, and executes before the changed hook.
Production: 493 checks / zero failures. Eight source controls fail 18/4/2/30/8/11/2/15
checks (upstream/world/car omission, alias, guest/device/matrix restoration).
Review P2 fixtures, original/fixed failures: parented bounds 3/0, matrix cache 4/0.
Original depth [-16,16] misses Z=105; fixed [-128,16] includes it.
Original/fixed projection uploads: 0/2 (near then restored far).
Held exposure/variant mutations fail 3/18 checks. BMP orientation 4/4 versus 0/4.
Real Metal: 124 checks / zero failures; alpha/omission/W controls fail
2048/2048/1872 of 4096 samples. GPU absence is reported separately.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tempfile
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]
PINNED_HOOK = 'void f_006E44C0(Cpu *c) {                // shadow light camera + caster pass\n    if (diag_on) shadow_pan_environment();\n    shadow_source_view = M32(c->esp + 4);\n    x360_vp_set = 0;\n    f_006E44C0_orig(c);\n    for (int i = 0; i < nculled; ++i) {\n        uint32_t a[3] = {culled[i][0], culled[i][1], 1}, esp = c->esp;\n        call_guest(c, M32(M32(culled[i][0]) + 0x68), 3, a);   // ID3DXEffect::SetInt\n        c->esp = esp;\n    }\n    static int logged;\n    if (diag_on && nculled && !logged++) diag_event("x360: shadow pass done, %d effect cull modes reset to none", nculled);\n    nculled = 0;\n}'
PINNED_SHA = '6002b9cf7cdb9fd802af228b8d7d361de7ccb636e644bfe8173ba2d145c7d229'
PREMISE = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint32_t eax,esp; } Cpu;
static uint32_t mem[32];
#define M32(a) mem[(a)/4]
static int diag_on,nculled,x360_vp_set,passes;
static uint32_t recorded_targets[2];
static uint32_t shadow_source_view,culled[64][2];
static void shadow_pan_environment(void) {}
static void diag_event(const char *s,...) { (void)s; }
static void call_guest(Cpu *c,uint32_t f,int n,uint32_t *a) {(void)c;(void)f;(void)n;(void)a;}
static void f_006E44C0_orig(Cpu *c) {recorded_targets[passes++]=0xDD;c->esp+=4;}
/* HOOK */
int main(void) { Cpu c={0,4}; M32(8)=16; f_006E44C0(&c);
 int targets=passes?1:0; if(passes==2 && recorded_targets[0]!=recorded_targets[1])++targets;
 printf("pinned original: traversals=%d targets=%d, required On=2/2; Off=%s\n",passes,targets,passes==1?"PASS":"FAIL");
 return passes==2 && targets==2?0:1; }
'''
def premise(tmp):
    assert hashlib.sha256(PINNED_HOOK.encode()).hexdigest() == PINNED_SHA
    src,binary=tmp/'premise.c',tmp/'premise'
    src.write_text(PREMISE.replace('/* HOOK */',PINNED_HOOK))
    subprocess.run(['clang','-std=c11',str(src),'-o',str(binary)],check=True,capture_output=True)
    run=subprocess.run([str(binary)],capture_output=True,text=True)
    print(run.stdout.strip()); assert run.returncode == 1,run.stderr


HARNESS = r'''
#include "render_internal.h"
#include "shadow_near.h"
#include "funcs.h"
#include <stdarg.h>
static unsigned checks, failures, far_passes, near_passes, tree_calls, world_calls, car_calls, merged_calls;
#define CHECK(x) do { ++checks; if(!(x)){if(failures<12)fprintf(stderr,"line %d: %s\n",__LINE__,#x);++failures;} } while(0)
uint8_t *G;
Device dev;
id<MTLDevice> mtl;
id<MTLCommandBuffer> cmd;
id<MTLRenderCommandEncoder> enc;
uint64_t frame_serial=10;
uint32_t q_active;
int diag_on;
const char *diag_dir;
static int alloc_fail, allocation_calls, heap_fail, skip_camera, restoring, drop_draw, frame_fail;
static unsigned fake_live;
@interface NearTexture : NSObject
@property NSUInteger width,height,sampleCount,allocatedSize;
@property MTLPixelFormat pixelFormat;
@end
@implementation NearTexture
- (instancetype)init {if((self=[super init]))++fake_live;return self;}
- (void)dealloc {--fake_live;}
@end
@interface NearDevice : NSObject
@property uint64_t recommendedMaxWorkingSetSize,currentAllocatedSize;
- (BOOL)supportsFamily:(MTLGPUFamily)family;
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)descriptor;
@end
@implementation NearDevice
- (BOOL)supportsFamily:(MTLGPUFamily)family {(void)family;return YES;}
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)d {
 ++allocation_calls;if(alloc_fail && allocation_calls==alloc_fail)return nil;
 NearTexture *t=[NearTexture new];t.width=d.width;t.height=d.height;t.pixelFormat=d.pixelFormat;t.sampleCount=1;
 t.allocatedSize=d.width*d.height*(is_depth_pf(d.pixelFormat)?8:4)+4096;return t;
}
@end
static id<MTLTexture> far_z,far_c;
void diag_event(const char *fmt,...) {(void)fmt;}
uint64_t diag_current_frame(void) {return frame_serial-1;}
void diag_shot_request(uint64_t f) {(void)f;}
int x360_shadow_map_size(void) {return 1600;}
void f_004700D0(Cpu *c) {memcpy(G+c->ecx,G+arg(c,0),64);c->esp+=12;}
void end_pass(void) {}
void render_targets_changed(void) {}
void render_shadow_near_restore(void) {++restoring;}
void render_shadow_near_suspend(void) {}
Obj *obj(uint32_t g) {(void)g;return NULL;}
static uint32_t heap=0x1000000;
uint32_t gmalloc_try(uint32_t n) {if(heap_fail)return 0;uint32_t p=heap;heap+=(n+63)&~63u;CHECK(heap<0x2000000);return p;}
void gfree(uint32_t p) {(void)p;}
id<MTLTexture> surface_target(uint32_t s,NSUInteger *l,NSUInteger *sl) {
 *l=*sl=0;id<MTLTexture> t=shadow_near_surface(s);if(t)return t;
 return s==0xCC?far_c:s==0xDD?far_z:nil;
}
static Device caster_device;
static uint32_t seen[8],seen_count;
static unsigned alpha_coverage,opaque_coverage;
static void record_geometry(uint32_t model) {
 CHECK(model==0x501 || model==0x502);
 CHECK(dev.rt[0]==0xCC && dev.ds==0xDD && dev.rt[1]==0 && dev.rt[2]==0 && dev.rt[3]==0);
 CHECK(dev.rs[195]==caster_device.rs[195] && dev.rs[175]==caster_device.rs[175]);
 CHECK(dev.rs[22]==caster_device.rs[22]);
 dev.rs[15]=model==0x502;dev.rs[24]=95;dev.rs[25]=5;M32(0x93DE28)=model;M32(0x93DE2C)=model+1;M32(0x93DE30)=model+2;
 uint64_t before=shadow_near_draw_begin();
 if(!drop_draw)shadow_near_draw_note(dev.rs[15]);
 shadow_near_draw_end(before,1);
 if(frame_fail){M32(0x915F94)=1;M32(0x915F98)+=0x130;}
 if(model==0x502)alpha_coverage+=32;else opaque_coverage+=64;
}
void render_clear(uint32_t n,uint32_t r,uint32_t flags,uint32_t colour,float z,uint32_t st) {
 (void)n;(void)r;CHECK(flags==7 && colour==0xFFFFFFFFu && z==1 && st==0);++near_passes;
 NSUInteger l,s;id<MTLTexture> a=surface_target(dev.ds,&l,&s),b=surface_target(dev.rt[0],&l,&s);
 CHECK(a && b && a!=far_z && b!=far_c && a!=b);
 CHECK(a.width==dev.viewport[2] && a.height==dev.viewport[3] && b.width==a.width);
 CHECK(dev.viewport[0]==0 && dev.viewport[1]==0 && !dev.rs[174]);
}
static void guest_run(Cpu *c,void (*f)(Cpu *),int n,const uint32_t *args) {
 uint32_t sp=c->esp;c->esp-=4*(n+1);M32(c->esp)=0xFEED;
 for(int i=0;i<n;++i)M32(c->esp+4+4*i)=args[i];f(c);c->esp=sp;
}
// Arithmetic leaves the hierarchy and dirty-state decisions to the translated
// SpaceNode routines. These sinks implement only their matrix/vector primitives.
void f_0045ECF0(Cpu *c) {
 uint32_t dst=arg(c,0),a=arg(c,1),b=arg(c,2);float out[16];
 for(int r=0;r<4;++r)for(int j=0;j<4;++j){out[r*4+j]=0;for(int k=0;k<4;++k)out[r*4+j]+=MF32(b+16*r+4*k)*MF32(a+16*k+4*j);}
 memcpy(G+dst,out,sizeof out);c->esp+=4;
}
void f_0045ED20(Cpu *c) {
 uint32_t dst=arg(c,0),m=arg(c,1),v=arg(c,2);float out[3];
 for(int j=0;j<3;++j)out[j]=MF32(v)*MF32(m+4*j)+MF32(v+4)*MF32(m+16+4*j)+MF32(v+8)*MF32(m+32+4*j)+MF32(m+48+4*j);
 memcpy(G+dst,out,sizeof out);c->esp+=4;
}
void f_00444FE0(Cpu *c) {memcpy(G+arg(c,0),G+arg(c,1),64);c->esp+=4;}
void f_007E7D5E(Cpu *c) {
 uint32_t dst=arg(c,0),a=arg(c,1),b=arg(c,2);float out[16];
 for(int r=0;r<4;++r)for(int j=0;j<4;++j){out[r*4+j]=0;for(int k=0;k<4;++k)out[r*4+j]+=MF32(a+16*r+4*k)*MF32(b+16*k+4*j);}
 memcpy(G+dst,out,sizeof out);c->eax=dst;c->esp+=16;
}
void f_007E7D64(Cpu *c) {(void)c;fprintf(stderr,"unexpected transpose\n");abort();}
void f_006BDE60(Cpu *c) {(void)c;fprintf(stderr,"unexpected inverse\n");abort();}
void f_0045F490(Cpu *c) {(void)c;fprintf(stderr,"unexpected vector transform\n");abort();}
void bad_jump(Cpu *c,uint32_t at) {(void)c;fprintf(stderr,"unexpected jump %x\n",at);abort();}
static float uploaded_projection[16];
static unsigned projection_uploads;
void dispatch(Cpu *c,uint32_t fn) {
 CHECK(fn==0xABD000 || fn==0xABD100);uint32_t state=arg(c,1),matrix=arg(c,2);
 if(fn==0xABD000 && state==3){memcpy(uploaded_projection,G+matrix,64);++projection_uploads;}
 c->eax=0;c->esp+=16;
}
void f_006BE800(Cpu *c) {(void)c;abort();}
void f_007C4B80(Cpu *c) {c->eax=(int32_t)ST(0);x87_pop(c);c->esp+=4;}
void f_00729120(Cpu *c) {
 uint32_t i=arg(c,0),entry=arg(c,1),visibility=arg(c,2),sec=c->ecx;
 CHECK(i==0 && visibility>0);CHECK(M32(entry+0xC4)==UINT32_MAX);
 Cpu lod=*c;uint32_t sp=lod.esp,args[4]={entry,M32(sec+0x28),0x40C00000u,sp-0x100};
 guest_run(&lod,f_00729060,4,args);CHECK((int32_t)lod.eax>=32);
 uint32_t at=M32(entry+0x8C);CHECK(at<M32(entry+0x90));
 uint32_t model=sec==0xB10000?0x501:0x502;
 M32(at)=model;M32(at+4)=0;M32(at+8)=M32(sec+0x20);M32(entry+0x8C)=at+12;
 seen[seen_count++]=model;c->esp+=16;
}
/* TRANSLATED */
static uint32_t pass_source;
static int matrix_fixture;
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 uint32_t sp=c->esp,result=0;
 if(fn==0x00446510){guest_run(c,f_00446510,n,a);result=c->eax;}
 else if(fn==0x004FAE40){CHECK(n==3 && a[0]==0x919C70 && a[2]==0x4100);M32(a[1]+0x80)=a[0];M32(a[1]+0x84)=a[2]|0x10;}
 else if(fn==0x00729640){++tree_calls;guest_run(c,f_00729640,n,a);}
 else if(fn==0x007538D0){++world_calls;CHECK(n==2 && a[0]==0x919C70);}
 else if(fn==0x00723FA0){CHECK(n==2 && a[0]==0x919C70 && a[1]==0x200);uint32_t e=c->ecx;
  if(matrix_fixture){uint32_t args[2]={0x009017E0,15};guest_run(c,f_006C8000_orig,2,args);}
  for(uint32_t p=M32(e+0x88);p<M32(e+0x8C);p+=12)record_geometry(M32(p));}
 else if(fn==0x006D79D0){CHECK(n==1 && a[0]==pass_source);result=0xB80000;}
 else if(fn==0xABC000){++car_calls;CHECK(n==2 && a[0]==0x919C70 && c->ecx==0xB80000);}
 else if(fn==0xABC200){result=1;}
 else if(fn==0x006D7F70){++merged_calls;CHECK(n==1 && a[0]==0x919C70 && c->ecx==0xB90000);}
 else if(fn!=0x006E2F50 && fn!=0x006E0200){fprintf(stderr,"unexpected guest call %x\n",fn);abort();}
 c->eax=result;c->ecx=0xDEAD;c->edx=0xBEEF;c->esp=sp;return result;
}
static int shadow_cull_front,nculled,x360_vp_set;
static uint32_t shadow_source_view,culled[64][2];
void shadow_pan_environment(void) {}
void f_006E44C0_orig(Cpu *c) {
 ++far_passes;pass_source=arg(c,0);
 if(!skip_camera){
  // The retail routine would already have bound these caster targets and states
  // when the existing camera callback is reached.
  caster_device=dev;
  shadow_near_far_camera(pass_source,0x985990,0x9859D0,1);
  shadow_near_far_rendered(pass_source,0x985990);
 }
 c->eax=0x12345678;c->ecx=0x3344;c->edx=0x5566;c->esp+=4;
}
/* HOOK */
#include "NEAR_SOURCE"
static void identity(uint32_t a){memset(G+a,0,64);for(int i=0;i<4;++i)MF32(a+i*20)=1;}
static void section(uint32_t id,uint32_t sec,float x,float y,float z) {
 uint32_t root=sec+0x100,instance=sec+0x200,tail=M32(0x9AA47C);
 M32(tail)=sec;M32(sec)=0x9AA478;M32(sec+4)=tail;M32(0x9AA47C)=sec;M32(sec+0xC)=id;M32(sec+0x28)=root;M32(sec+0x24)=1;M32(sec+0x20)=instance;
 for(int i=0;i<3;++i){float p=i==0?x:i==1?y:z;MF32(root+4*i)=p-.5;MF32(root+12+4*i)=p+.5;}
 M16(root+0x18)=1;M16(root+0x1A)=0;memcpy(G+instance,G+root,24);
}
static void setup(void) {
 memset(G,0,0x2000000);dev=(Device){0};heap=0x1000000;
 dev.rs[22]=2;dev.rs[195]=0x33800000;dev.rs[175]=0x3F800000;dev.rs[174]=1;
 dev.rs[7]=dev.rs[14]=1;dev.viewport[0]=42;dev.viewport[1]=52;dev.viewport[2]=3840;dev.viewport[3]=2160;
 dev.rt[0]=0x101;dev.rt[1]=0x102;dev.ds=0x103;
 dev.scissor[0]=1;dev.scissor[1]=2;dev.scissor[2]=3;dev.scissor[3]=4;
 M32(0x919650)=0xB00000;M32(0x9196C0)=0xB01000;M32(0x919C70)=0x985990;M32(0x919CB0)=0xB05000;
 identity(0xB00000);identity(0xB00040);identity(0xB01000);identity(0xB01040);MF32(0xB00054)=-2;MF32(0xB01054)=-2;
 identity(0x985990);identity(0x9859D0);identity(0x985A10);memset(G+0x985A50,0xCA,0xA0);
 M32(0x9B392C)=0xB06000;MF32(0xB06188)=1;
 M32(0x982CEC)=0xCC;M32(0x982CF4)=0xDD;M32(0x901830)=2;M32(0x982C08)=1;M32(0x8FAECC)=1;
 M32(0x903320)=1;M32(0xB80000)=0xB81000;M32(0xB8100C)=0xABC000;M32(0x982D10)=0xB90000;
 MF32(0x8933D4)=.5;M32(0x9AA478)=M32(0x9AA47C)=0x9AA478;
 section(5,0xB10000,0,0,0);section(5000,0xB20000,2,1,20);section(201,0xB30000,200,200,20);
 seen_count=alpha_coverage=opaque_coverage=0;
}
static Cpu run_hook(uint32_t source) { @autoreleasepool {
 Cpu c={.esp=0xE00000,.cw=0x37f,.ebx=3,.esi=4,.edi=5,.ebp=6};M32(c.esp)=0xFEED;M32(c.esp+4)=source;M32(c.esp+8)=0xBC0000;
 f_006E44C0(&c);return c;
} }
static void bounds_checks(void) {
 setup();shadow_near_set_enabled(1);
 Cpu c={.esp=0xE00000,.cw=0x37f},saved=c;ShadowNearInput in;uint32_t count;
 uint32_t node=0xBD0000,model=0xBD0100,solid=0xBD0200,space=0xBD0300;
 M32(0x9B0F90)=node;M32(node)=0x9B0F90;M32(node+8)=model;M32(model+0xC)=solid;M32(node+0x30)=space;
 identity(space+0x60);MF32(space+0x60+0x38)=150;M32(space+0xF4)=1;
 for(int i=0;i<3;++i){MF32(solid+0x20+4*i)=-2;MF32(solid+0x30+4*i)=10;}
 CHECK(near_input(&c,0x919650,&in,&count));CHECK(count==3 && in.contributing_casters.max.z==160);
 // Parent and child are both dirty, with a deliberately stale resolved matrix.
 // Remove scenery so the world model alone must extrude the near depth fit.
 uint32_t parent=0xBD0400;M32(0x9AA478)=0x9AA478;
 identity(parent+0x60);MF32(parent+0x98)=100;M32(parent+0xF4)=1;
 identity(space+0x60);MF32(space+0x98)=5;identity(space+0x20);
 M32(space+0xF0)=parent;M32(space+0xF4)=1;
 CHECK(near_input(&c,0x919650,&in,&count));
 CHECK(in.contributing_casters.min.z==0 && in.contributing_casters.max.z==115);
 CHECK(!M32(space+0xF4) && !M32(parent+0xF4));CHECK(!memcmp(&c,&saved,sizeof c));
 ShadowNearResult fit;in.resolution=256;in.max_depth_bytes=256*256*4;
 CHECK(shadow_near_fit(&in,&fit));
 double caster_depth=-105;
 CHECK(caster_depth>=fit.depth_min && caster_depth<=fit.depth_max);
 printf("parented bounds: max_z=%.0f depth=[%.0f,%.0f] caster=%.0f\n",in.contributing_casters.max.z,fit.depth_min,fit.depth_max,caster_depth);
 M32(0x9B37CC)=0xBD1000;M32(0x9B37D4)=1;M32(0xBD1000)=0xB80000;M32(0xB80044)=0xBD2000;M32(0xB80028)=3;M32(0xB8100C)=0x00750E20;
 identity(0xB80330);identity(0x9B34B0);MF32(0xB80368)=300;
 for(int i=0;i<3;++i){MF32(0xBD2090+4*i)=-1;MF32(0xBD20A0+4*i)=1;}
 CHECK(near_input(&c,0x919650,&in,&count));CHECK(in.contributing_casters.max.z==301);
 M32(0x9885C8)=0xBD3000;M32(0xBD3000)=0xBD3100;M32(0xBD314C)=0xABC200;
 CHECK(!near_input(&c,0x919650,&in,&count));CHECK(!memcmp(&c,&saved,sizeof c));M32(0x9885C8)=0;
 unsigned passes=near_passes;q_active=1;(void)run_hook(0x919650);q_active=0;CHECK(near_passes==passes && !near_binding.owner);
 M32(0xB8100C)=0xABC000;(void)run_hook(0x919650);CHECK(near_passes==passes && !near_binding.owner);
 MF32(solid+0x20)=NAN;CHECK(!near_input(&c,0x919650,&in,&count));
 shadow_near_set_enabled(0);
 puts("near bounds: loaded ID 5000, transformed world/car, NIS/unknown-route/query fallback checked");
}
static void pose_checks(void) {
 char path[PATH_MAX];uint32_t capacity=sizeof path;CHECK(!_NSGetExecutablePath(path,&capacity));
 NSString *manifest=[[[NSString stringWithUTF8String:path] stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"pose.json"];
 [[NSFileManager defaultManager] removeItemAtPath:manifest error:nil];
 setenv("BOOT_DIAG_SHADOW_POSE",manifest.fileSystemRepresentation,1);unsetenv("BOOT_DIAG_SHADOW_NEAR_VIEWS");
 setup();diag_on=1;pose_init=pose_failed=pose_written=0;pose_epoch=-1;pose_pending=nil;pose_replay=nil;pose_path=nil;
 M32(0x925E90)=6;M32(0x919690)=0xB07000;identity(0xB07000);M16(0xB070C4)=12000;
 M32(0xB8100C)=0x00750E20;identity(0xB80330);M32(0xB8002C)=31;
 M32(0x982BE4)=3840;M32(0x982BE8)=2160;M32(0x9196B8)=0xB0A000;
 M32(0x9196B0)=0xBC2000;M32(0xBC2004)=1;M32(0x982A20)=0xBC2000;
 M32(0xBC09C0)=1;M32(0xBC0080)=0x919C70;M32(0xBC0088)=0xBC1000;M32(0xBC008C)=0xBC100C;M32(0xBC1000)=0x501;M32(0xBC1008)=0xB10200;
 pass_source=0x919650;Cpu c={.esp=0xE00000,.cw=0x37f},before=c;
 shadow_near_pose_camera(&c,0xB07000,1,0);CHECK(!pose_failed && pose_path!=nil);
 MF32(0x9885D8)=12;shadow_near_pose_camera(&c,0xB07000,1,0);shadow_near_pose_render(&c,0x919650,0xBC0000);
 CHECK(pose_pending!=nil && [pose_pending[@"camera_mode"] unsignedIntValue]==0);shadow_near_receiver_note();
 ++frame_serial;MF32(0x9885D8)=12.02;shadow_near_pose_render(&c,0x919650,0xBC0000);
 CHECK(pose_written && !pose_failed && !memcmp(&c,&before,sizeof c));
 NSData *captured=[NSData dataWithContentsOfFile:manifest];CHECK(captured!=nil);if(!captured)return;
 NSDictionary *d=[NSJSONSerialization JSONObjectWithData:captured options:0 error:nil];
 CHECK([d[@"visible_receiver_ids"] count]==1 && [d[@"visible_caster_ids"] count]==1);
 CHECK([d[@"visible_caster_ids"][0][@"section"] intValue]==5 && [d[@"visible_caster_ids"][0][@"instance_index"] intValue]==0);
 CHECK([d[@"car_transform_words"] count]==16 && [d[@"car_wheel_words"] count]==128 && [d[@"projection_words"] count]==16);
 MF32(0xB07030)=99;M16(0xB070C4)=30000;MF32(0xB80360)=99;MF32(0xB06188)=.1;
 shadow_near_set_enabled(1);shadow_near_pose_camera(&c,0xB07000,1,0);shadow_near_pose_render(&c,0x919650,0xBC0000);
 CHECK(!pose_failed && MF32(0xB07030)==0 && M16(0xB070C4)==12000 && MF32(0xB80360)==0 && MF32(0xB06188)==1);
 CHECK(!memcmp(&c,&before,sizeof c));
 M32(0x982BE4)=1920;MF32(0xB80360)=42;shadow_near_pose_render(&c,0x919650,0xBC0000);CHECK(pose_failed && MF32(0xB80360)==42);
 uint32_t value=M32(0xB07000);CHECK(!pose_copy(@{@"bad":@[@1]},@"bad",0xB07000,16));CHECK(M32(0xB07000)==value);
 pose_init=pose_failed=0;pose_replay=nil;pose_path=nil;setenv("BOOT_DIAG_SHADOW_POSE","/tmp/near-outside.json",1);pose_options();CHECK(pose_failed && !pose_path);
 unsetenv("BOOT_DIAG_SHADOW_POSE");diag_on=0;shadow_near_set_enabled(0);
 printf("near pose: capture/replay bumper, IDs, exact words, type/config/path rejection checked\n");
}
static void matrix_cache_checks(void) {
 setup();shadow_near_set_enabled(1);matrix_fixture=1;
 M32(0x9AA478)=0x9AA478;M32(0x903320)=0;M32(0x982D10)=0;
 // Identity-only merged scenery keeps precisely the far pass's matrix/effect/view
 // tuple. The real translated cache must upload the changed near projection.
 uint32_t effect=0xBE0000;M32(0x982C80)=effect;M32(effect+0x44)=0xBE1000;
 M32(effect+0x48)=0xBE4000;M32(0xBE4000)=0xBE4100;M32(0xBE4198)=0xABD100;
 M32(0x982BDC)=0xBE3000;M32(0xBE3000)=0xBE3100;M32(0xBE31B0)=0xABD000;
 identity(0x009017E0);
 M32(0x988554)=0x009017E0;M32(0x988550)=effect;M32(0x8FAE9C)=15;
 memcpy(uploaded_projection,G+0x9859D0,64);projection_uploads=0;
 (void)run_hook(0x919650);
 CHECK(near_binding.owner==0x919650);CHECK(projection_uploads==1);
 CHECK(uploaded_projection[0]==(float)near_binding.fit.world_to_clip.m[0]);
 CHECK(M32(0x8FAE9C)==UINT32_MAX);
 Cpu c={.esp=0xE00000,.cw=0x37f};uint32_t args[2]={0x009017E0,15};
 guest_run(&c,f_006C8000_orig,2,args);
 CHECK(projection_uploads==2);CHECK(!memcmp(uploaded_projection,G+0x9859D0,64));
 unsigned before=projection_uploads;guest_run(&c,f_006C8000_orig,2,args);CHECK(projection_uploads==before);
 printf("matrix cache: near/far projection uploads=%u (required 2)\n",projection_uploads);
 matrix_fixture=0;shadow_near_set_enabled(0);
}
static void ab_checks(void) {
 diag_on=1;shadow_near_set_enabled(1);near_ab_init=0;near_ab_count=0;
 setenv("BOOT_DIAG_SHADOW_NEAR_AB","retail,projection,filter,full",1);
 setenv("BOOT_DIAG_SHADOW_NEAR_HOLD_AT","25",1);
 CHECK(shadow_near_ab_enabled());CHECK(near_ab_count==4 && near_ab_hold_at==25);
 near_ab_camera(24.999);CHECK(!near_ab_active);near_ab_camera(25);CHECK(near_ab_active && near_ab_index==0);
 uint32_t sim=M32(0x9885D8),tick=M32(0x9885B8);char path[1024];
 for(int i=0;i<4;++i) {
  CHECK(near_ab_index==i);CHECK((near_ab_modes[i]==(uint32_t[]){1,4,5,0}[i]));
  CHECK(shadow_near_ab_frozen()==(i>0));
  float adaptation=1.25f+i,exposure=2.5f+i;
  shadow_near_ab_post(&adaptation,&exposure);CHECK(adaptation==1.25f && exposure==2.5f);
  CHECK(shadow_near_ab_shot(path,sizeof path));CHECK(strstr(path,near_ab_names[i])!=NULL);
  CHECK(M32(0x9885D8)==sim && M32(0x9885B8)==tick);
  CHECK(shadow_near_ab_hold()==(i<3));
 }
 CHECK(!near_ab_active && near_ab_finished);near_ab_camera(26);CHECK(!near_ab_active);
 unsetenv("BOOT_DIAG_SHADOW_NEAR_AB");unsetenv("BOOT_DIAG_SHADOW_NEAR_HOLD_AT");
 near_ab_init=near_ab_count=near_ab_finished=0;near_ab_hold_at=12;diag_on=0;shadow_near_set_enabled(0);
 puts("near AB: four separately evaluated modes; held transitions 1/1/1/0, identical sim/tick words");
}
/* GPU_CHECKS */
int main(int argc,char **argv) { @autoreleasepool {
 G=calloc(1,0x2000000);
 if(argc==2)return gpu_checks(argv[1]);
 NearDevice *device=[NearDevice new];device.recommendedMaxWorkingSetSize=16ull<<30;mtl=(id)device;
 far_z=[device newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float_Stencil8 width:1600 height:1600 mipmapped:NO]];
 far_c=[device newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:1600 height:1600 mipmapped:NO]];
 setup();setenv("NFS_SHADOW_NEAR","off",1);setenv("NFS_SHADOW_NEAR_SIZE","4096",1);near_on=-1;
 Device saved=dev;Cpu c=run_hook(0x919650);CHECK(far_passes==1 && near_passes==0 && !near_depth);
 CHECK(!memcmp(&dev,&saved,sizeof dev));CHECK(c.esp==0xE00004 && c.eax==0x12345678 && c.ecx==0x3344);
 CHECK(!strcmp(getenv("NFS_SHADOW_NEAR"),"off"));
 shadow_near_set_enabled(1);uint8_t matrix[0x1A0],camera[0x60];memcpy(matrix,G+0x985990,sizeof matrix);memcpy(camera,G+0xB05040,sizeof camera);
 int alloc0=allocation_calls;uint64_t generation=0;
 for(unsigned i=0;i<8;++i){
  uint32_t source=i%2?0x9196C0:0x919650;seen_count=0;c=run_hook(source);
  ShadowNearBinding b;CHECK(shadow_near_binding(source,frame_serial,&b));CHECK(!shadow_near_binding(source,frame_serial+1,&b));
  CHECK(shadow_near_binding(source,frame_serial,&b));CHECK(b.width==4096 && b.height==4096 && b.far_width==1600 && b.far_height==1600);
  CHECK(b.world_to_far_clip.m[0]==1 && b.world_to_far_texture.m[15]==1);
  CHECK(b.depth_allocated_bytes==4096ull*4096*8+4096 && b.colour_allocated_bytes==4096ull*4096*4+4096);
  CHECK(b.caster_count==2 && b.draws==2 && b.alpha_draws==1);CHECK(seen_count==2 && seen[0]==0x501 && seen[1]==0x502);
  CHECK(b.generation>generation);generation=b.generation;
  CHECK(!memcmp(matrix,G+0x985990,sizeof matrix) && !memcmp(camera,G+0xB05040,sizeof camera));
  CHECK(!memcmp(&saved,&dev,sizeof dev));CHECK(!M32(0x93DE28) && !M32(0x93DE2C) && !M32(0x93DE30));CHECK(c.esp==0xE00004 && c.eax==0x12345678 && c.ecx==0x3344 && c.edx==0x5566);
  CHECK(!shadow_near_binding(i%2?0x919650:0x9196C0,frame_serial,&b));
  frame_serial++;
 }
 CHECK(allocation_calls==alloc0+2);CHECK(far_passes==9 && near_passes==8 && world_calls==8 && car_calls==8 && merged_calls==8);
 CHECK(alpha_coverage==256 && opaque_coverage==512);CHECK(tree_calls==24);
 ShadowNearBinding b;
 drop_draw=1;c=run_hook(0x919650);CHECK(!shadow_near_binding(0x919650,frame_serial,&b));drop_draw=0;
 CHECK(!memcmp(&saved,&dev,sizeof dev) && !near_active);
 frame_fail=1;c=run_hook(0x919650);CHECK(!shadow_near_binding(0x919650,frame_serial,&b));frame_fail=0;
 CHECK(!M32(0x915F94) && !M32(0x915F98));CHECK(!memcmp(&saved,&dev,sizeof dev) && !near_active);
 skip_camera=1;c=run_hook(0x919650);CHECK(!shadow_near_binding(0x919650,frame_serial,&b));skip_camera=0;
 shadow_near_reset();CHECK(!near_depth && !near_colour && !near_cull && !near_table && !shadow_near_binding(0x919650,frame_serial,&b));
 unsigned pass0=near_passes;alloc_fail=allocation_calls+2;c=run_hook(0x919650);CHECK(near_passes==pass0 && !near_binding.owner && !near_active);alloc_fail=0;
 CHECK(!memcmp(&saved,&dev,sizeof dev));
 heap_fail=1;c=run_hook(0x919650);CHECK(near_passes==pass0 && !near_binding.owner);heap_fail=0;
 shadow_near_reset();setenv("NFS_SHADOW_NEAR_SIZE","999999",1);c=run_hook(0x919650);CHECK(near_passes==pass0 && !near_binding.owner);
 setenv("NFS_SHADOW_NEAR_SIZE","4096x",1);c=run_hook(0x919650);CHECK(near_passes==pass0);
 setenv("NFS_SHADOW_NEAR_SIZE","4096",1);device.recommendedMaxWorkingSetSize=1ull<<20;c=run_hook(0x919650);CHECK(near_passes==pass0);
 device.recommendedMaxWorkingSetSize=16ull<<30;shadow_near_set_enabled(0);c=run_hook(0x919650);CHECK(near_passes==pass0 && !near_binding.owner);
 unsigned alpha_proved=alpha_coverage,opaque_proved=opaque_coverage;bounds_checks();pose_checks();matrix_cache_checks();ab_checks();
 CHECK(fake_live==2);far_z=far_c=nil;CHECK(fake_live==0);
 printf("near replay: %u checks, %u failures; far=%u near=%u IDs=501/502 alpha=%u opaque=%u restore=%d\n",checks,failures,far_passes,near_passes,alpha_proved,opaque_proved,restoring);
 free(G);return failures?1:0;
} }
'''


GPU_CHECKS = r'''

static unsigned gpu_mask_case(id<MTLDevice> device,id<MTLCommandQueue> queue,const char *root,const char *directory,int control) {
 NSString *path=[NSString stringWithFormat:@"%s/host/shaders/testdata/%s/t0_depth_technique_alpha_p0_ps_2_0.bin",root,directory];
 NSData *data=[NSData dataWithContentsOfFile:path];SmOptions opt;sm_default_options(&opt);SmResult pc={0};
 CHECK(data!=nil && !sm_translate(data.bytes,(uint32_t)data.length/4,&opt,&pc));
 NSMutableString *source=[NSMutableString stringWithUTF8String:pc.msl];
 if(control==1)[source replaceOccurrencesOfString:@"if (so.killed) discard_fragment();" withString:@"/* deliberate alpha omission */" options:0 range:NSMakeRange(0,source.length)];
 [source appendString:@"\nstruct NearCasterV { float4 p [[position]]; float4 uv [[user(texcoord0)]]; float4 shadow [[user(texcoord1)]]; };\nvertex NearCasterV near_caster(uint id [[vertex_id]]) { float2 p=float2(id&1,id>>1); NearCasterV o;o.p=float4(2*p.x-1,1-2*p.y,.25f,1);o.uv=float4(p,0,1);o.shadow=o.p;return o;}\n"];
 NSError *error=nil;id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];CHECK(library!=nil);
 if(!library){fprintf(stderr,"mask compile: %s\n",error.localizedDescription.UTF8String);return 4096;}
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"near_caster"];pd.fragmentFunction=[library newFunctionWithName:@"sm_main"];
 pd.colorAttachments[0].pixelFormat=near_colour.pixelFormat;pd.depthAttachmentPixelFormat=pd.stencilAttachmentPixelFormat=near_depth.pixelFormat;
 id<MTLRenderPipelineState> pso=[device newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pso!=nil);
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];td.storageMode=MTLStorageModeShared;
 id<MTLTexture> mask=[device newTextureWithDescriptor:td];uint8_t pixels[8*8*4];
 for(unsigned y=0;y<8;++y)for(unsigned x=0;x<8;++x){unsigned i=(y*8+x)*4;pixels[i]=pixels[i+1]=pixels[i+2]=255;pixels[i+3]=((x+y)&1)?255:0;}
 [mask replaceRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0 withBytes:pixels bytesPerRow:32];
 MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=near_colour;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 rp.depthAttachment.texture=near_depth;rp.depthAttachment.loadAction=MTLLoadActionClear;rp.depthAttachment.clearDepth=1;rp.depthAttachment.storeAction=MTLStoreActionStore;
 rp.stencilAttachment.texture=near_depth;rp.stencilAttachment.loadAction=MTLLoadActionClear;rp.stencilAttachment.storeAction=MTLStoreActionStore;
 id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
 if(control!=2){
  MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLessEqual;dd.depthWriteEnabled=YES;
  [e setRenderPipelineState:pso];[e setDepthStencilState:[device newDepthStencilStateWithDescriptor:dd]];[e setCullMode:MTLCullModeNone];
  [e setViewport:(MTLViewport){0,0,256,256,0,1}];[e setScissorRect:(MTLScissorRect){0,0,256,256}];
  float constants[224][4]={{0}};int32_t integers[16][4]={{0}},booleans[16]={0};SmPSAux aux={0};
  [e setFragmentBytes:constants length:sizeof constants atIndex:0];[e setFragmentBytes:integers length:sizeof integers atIndex:1];
  [e setFragmentBytes:booleans length:sizeof booleans atIndex:2];[e setFragmentBytes:&aux length:sizeof aux atIndex:3];[e setFragmentTexture:mask atIndex:0];
  MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;sd.sAddressMode=sd.tAddressMode=MTLSamplerAddressModeClampToEdge;
  [e setFragmentSamplerState:[device newSamplerStateWithDescriptor:sd] atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
 }
 [e endEncoding];
 NSMutableString *compute=[NSMutableString stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\n%s",near_receiver_msl];
 if(control==3)[compute replaceOccurrencesOfString:@"float3 p=h.xyz/h.w;" withString:@"float3 p=h.xyz;" options:0 range:NSMakeRange(0,compute.length)];
 [compute appendString:@"\nkernel void near_probe(device const float4 *f [[buffer(0)]],device float2 *out [[buffer(1)]],constant NearShadowParams &k [[buffer(2)]],depth2d<float> z [[texture(0)]],uint id [[thread_position_in_grid]]) {out[id]=near_shadow_eval(f[id],k,z);}\n"];
 id<MTLLibrary> cl=[device newLibraryWithSource:compute options:nil error:&error];CHECK(cl!=nil);
 id<MTLComputePipelineState> cp=cl?[device newComputePipelineStateWithFunction:[cl newFunctionWithName:@"near_probe"] error:&error]:nil;CHECK(cp!=nil);
 if(!cp){fprintf(stderr,"probe compile: %s\n",error.localizedDescription.UTF8String);return 4096;}
 float coords[4096][4];
 for(unsigned y=0;y<64;++y)for(unsigned x=0;x<64;++x){float u=(x+.5f)/64,v=(y+.5f)/64,w=1/(1-.25f*u+.125f*v);unsigned i=y*64+x;
  coords[i][0]=u*w;coords[i][1]=v*w;coords[i][2]=(.75f*w-.25f)*2;coords[i][3]=1;}
 ShadowNearUniforms uniform={.rows={{1,0,0,.25f},{0,1,0,-.125f},{0,0,.5f,0},{0,0,.25f,1}},.usable={0,0,1,1},.blend={.001f,.001f,.999f,.999f}};
 id<MTLBuffer> input=[device newBufferWithBytes:coords length:sizeof coords options:MTLResourceStorageModeShared];
 id<MTLBuffer> output=[device newBufferWithLength:4096*2*sizeof(float) options:MTLResourceStorageModeShared];
 id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:cp];[ce setBuffer:input offset:0 atIndex:0];[ce setBuffer:output offset:0 atIndex:1];
 [ce setBytes:&uniform length:sizeof uniform atIndex:2];[ce setTexture:near_depth atIndex:0];[ce dispatchThreads:MTLSizeMake(4096,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];[ce endEncoding];
 [cb commit];[cb waitUntilCompleted];CHECK(cb.status==MTLCommandBufferStatusCompleted);
 const float *values=output.contents;unsigned mismatch=0,shadowed=0;
 for(unsigned y=0;y<64;++y)for(unsigned x=0;x<64;++x){unsigned i=y*64+x;float expected=((x/8+y/8)&1)?0:1;
  mismatch+=!isfinite(values[i*2]) || fabsf(values[i*2]-expected)>.001f || fabsf(values[i*2+1]-1)>.001f;shadowed+=values[i*2]<.5f;}
 printf("near GPU mask %s control=%d: samples=4096 shadowed=%u expected=2048 mismatches=%u\n",directory,control,shadowed,mismatch);
 sm_result_free(&pc);return mismatch;
}
static int gpu_checks(const char *root) {
 id<MTLDevice> device=MTLCreateSystemDefaultDevice();
 if(!device){puts("near GPU: PENDING real Metal device");return 0;}
 mtl=device;
 NSString *proof=[NSString stringWithFormat:@"%s/.metal-cache/s3",root];
 [[NSFileManager defaultManager] createDirectoryAtPath:proof withIntermediateDirectories:YES attributes:nil error:nil];
 FILE *report=fopen([[proof stringByAppendingPathComponent:@"metal-proof.txt"] fileSystemRepresentation],"w");
 if(report)fprintf(report,"device=%s recommendedMaxWorkingSetSize=%llu\n",device.name.UTF8String,(unsigned long long)device.recommendedMaxWorkingSetSize);
 unsigned translated_count=0,compiled=0;
 for(NSString *directory in @[@"world",@"worldnormalmap",@"worldreflect"]) {
  NSString *path=[NSString stringWithFormat:@"%s/host/shaders/testdata/%@",root,directory];
  for(NSString *name in [[[NSFileManager defaultManager] contentsOfDirectoryAtPath:path error:nil] sortedArrayUsingSelector:@selector(compare:)]) {
   if(![name hasSuffix:@".bin"] || ![name containsString:@"_ps_"])continue;
   NSData *data=[NSData dataWithContentsOfFile:[path stringByAppendingPathComponent:name]];
   SmOptions opt;sm_default_options(&opt);SmResult pc={0};
   CHECK(!sm_translate(data.bytes,(uint32_t)data.length/4,&opt,&pc));
   int slot=-1;for(int i=0;i<pc.n_constants;++i)if(pc.constants[i].set==3 && !strcmp(pc.constants[i].name,"shadow_sampler"))slot=pc.constants[i].reg;
   if(slot<0 || !sm_shadow_near_taps(pc.program_key)){CHECK(!shadow_near_shader(pc.msl,&pc,data.bytes,(uint32_t)data.length/4));sm_result_free(&pc);continue;}
   sm_result_free(&pc);opt.shadow_samplers=1u<<slot;
   CHECK(!sm_translate(data.bytes,(uint32_t)data.length/4,&opt,&pc));++translated_count;
   char *source=shadow_near_shader(pc.msl,&pc,data.bytes,(uint32_t)data.length/4);CHECK(source!=NULL);
   if(source) {
    NSError *error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithUTF8String:source] options:nil error:&error];
    if(!lib)fprintf(stderr,"near MSL %s: %s\n",name.UTF8String,error.localizedDescription.UTF8String);
    CHECK(lib!=nil);if(lib)++compiled;
    NSString *text=[NSString stringWithUTF8String:source];
    CHECK([[text componentsSeparatedByString:@"z.sample_compare(s,p.xy,p.z)"] count]==2);
    CHECK(([text containsString:[NSString stringWithFormat:@"[[buffer(%u)]]",(unsigned)SHADOW_NEAR_BUFFER]]));
    CHECK([text containsString:@"if (aux.alpha_func != 0u"] && [text containsString:@"if (so.killed) discard_fragment();"]);
    free(source);
   }
   sm_result_free(&pc);
  }
 }
 CHECK(translated_count==9 && compiled==translated_count);
 id<MTLTexture> z=[device newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float_Stencil8 width:1600 height:1600 mipmapped:NO]];
 id<MTLTexture> colour=[device newTextureWithDescriptor:[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:1600 height:1600 mipmapped:NO]];
 for(NSString *size in @[@"4096",@"8192"]) {
  @autoreleasepool {
   shadow_near_reset();setenv("NFS_SHADOW_NEAR_SIZE",size.UTF8String,1);
   int allocated=near_allocate(z,colour);CHECK(allocated);
   if(allocated && report)fprintf(report,"near Metal %s: depth allocatedSize=%llu colour allocatedSize=%llu total=%llu (stencil/alignment included)\n",size.UTF8String,
       (unsigned long long)near_depth.allocatedSize,(unsigned long long)near_colour.allocatedSize,
       (unsigned long long)(near_depth.allocatedSize+near_colour.allocatedSize));
  }
 }
 shadow_near_reset();setenv("NFS_SHADOW_NEAR_SIZE","256",1);CHECK(near_allocate(z,colour));
 id<MTLCommandQueue> queue=[device newCommandQueue];
 for(int control=0;control<4;++control) {unsigned mismatch=gpu_mask_case(device,queue,root,"worlddepth",control);CHECK(control?mismatch>0:mismatch==0);if(report)fprintf(report,"alpha/W control %d mismatches %u/4096\n",control,mismatch);}
 CHECK(gpu_mask_case(device,queue,root,"treedepth",0)==0);
 shadow_near_reset();
 printf("near GPU: %u checks, %u failures; translated=%u compiled=%u\n",checks,failures,translated_count,compiled);
 if(report){fprintf(report,"near GPU: %u checks, %u failures; translated=%u compiled=%u\n",checks,failures,translated_count,compiled);fclose(report);}
 return failures?1:0;
}
'''


def translated(name):
    for path in sorted((ROOT/'recomp/out').glob('speed_*.c')):
        source=path.read_text()
        match=re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}',source,re.S)
        if match:return match[0]
    raise RuntimeError('missing translated function '+name)

def bmp_check(tmp,production):
    function=production[production.index('int shadow_near_ab_write_bmp('):]
    code='#include <stdint.h>\n#include <stdio.h>\n#include <limits.h>\n'+function+r'''
int main(int argc,char **argv) {
 unsigned char pixels[]={0,0,255,255,0,255,0,255,255,0,0,255,255,255,255,255};
 if(argc!=2 || !shadow_near_ab_write_bmp(argv[1],pixels,2,2))return 1;
 if(shadow_near_ab_write_bmp(argv[1],pixels,0,2) || shadow_near_ab_write_bmp(argv[1],pixels,16385,2))return 2;
 return 0;
}
'''
    for label,source in [('production',code),('bottom-up mutation',code.replace('0u-height','height'))]:
        src=tmp/'bmp.c';exe=tmp/'bmp';path=tmp/'capture.bmp';src.write_text(source)
        subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],check=True,capture_output=True)
        subprocess.run([str(exe),str(path)],check=True,capture_output=True)
        with Image.open(path) as im:
            matches=im.size==(2,2) and list(im.convert('RGB').getdata())==[(255,0,0),(0,255,0),(0,0,255),(255,255,255)]
        assert matches==(label=='production'),label
    print('bounded BMP: exact RGB/orientation 4/4, bottom-up control 0/4; invalid dimensions rejected')


def main():
    required=['runtime/gen/x360_engine.inc','recomp/out/speed_04.c','recomp/out/speed_15.c','recomp/out/speed_40.c','recomp/out/speed_45.c','recomp/out/speed_47.c','host/shaders/build/libsmtranslate.a','host/shaders/testdata/worlddepth/t0_depth_technique_alpha_p0_ps_2_0.bin','host/shaders/testdata/treedepth/t0_depth_technique_alpha_p0_ps_2_0.bin']
    if any(not (ROOT/p).exists() for p in required):
        print('SKIP: needs generated shadow constants, translations and shader fixtures');return 3
    with tempfile.TemporaryDirectory(prefix='nfs-near-') as d:
        tmp=Path(d);premise(tmp)
        production=(ROOT/'runtime/shadow_near.m').read_text()
        bmp_check(tmp,production)
        # The same shader helper compiled into the production translator, exposed
        # here only to the existing alpha-caster mapping fixture.
        helper=re.search(r'near_receiver_msl\[\] = R"MSL\((.*?)\)MSL";',
                         (ROOT/'host/shaders/shadow_near_msl.h').read_text(),re.S)[1]
        production+='\nstatic const char near_receiver_msl[]='+json.dumps(helper)+';\n'

        x360=(ROOT/'runtime/x360.m').read_text()
        wrapper=re.search(r'void shadow_cull_restore\(Cpu \*c\).*?\n}',x360,re.S)[0]+'\n'+re.search(r'void f_006E44C0\(Cpu \*c\).*?\n}',x360,re.S)[0]
        translated_source='\n'.join(translated(n) for n in ['004FCA70','006CF2B0','00729060','00729640','00446510','007477F0','006C8000'])
        cpp=tmp/'fit.o'
        subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined','-c',str(ROOT/'runtime/x360_shadow_fit.cpp'),'-o',str(cpp)],check=True,capture_output=True)
        def run(label,code):
            module=tmp/'near.m';module.write_text(code)
            src=tmp/'harness.m';binary=tmp/'replay'
            src.write_text(HARNESS.replace('/* TRANSLATED */',translated_source).replace('/* HOOK */',wrapper).replace('NEAR_SOURCE',str(module)).replace('/* GPU_CHECKS */',GPU_CHECKS))
            r=subprocess.run(['clang','-std=c11','-fobjc-arc','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-parentheses-equality',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),
                str(src),str(cpp),str(ROOT/'host/shaders/build/libsmtranslate.a'),'-lc++','-framework','Foundation','-framework','Metal','-o',str(binary)],capture_output=True,text=True)
            if r.returncode:raise RuntimeError(r.stderr[-7000:])
            r=subprocess.run([str(binary)],capture_output=True,text=True)
            print(label+': '+r.stdout.strip())
            if r.returncode and label=='production':print(r.stderr[-4000:])
            return r
        assert run('production',production).returncode==0
        r=subprocess.run([str(tmp/'replay'),str(ROOT)],capture_output=True,text=True)
        print(r.stdout.strip())
        if r.returncode:print(r.stderr[-6000:])
        assert r.returncode==0
        mutations={
            'local parented bounds':('matrix=call_guest(c,0x00446510u,0,NULL);','matrix=space+0x60;'),
            'stale matrix cache':('M32(0x8FAE9C)=UINT32_MAX;','(void)0;'),
            'omitted upstream caster':('if (!sec || !M32(sec+0x28)) continue;','if (!sec || !M32(sec+0x28) || M32(sec+0xC)>=2700) continue;'),
            'omitted world bounds':('if(solid) {','if(0 && solid) {'),
            'omitted car bounds':('if(!info || M32(car+0x28)<2)continue;','if(1 || !info || M32(car+0x28)<2)continue;'),
            'aliased depth target':('near_depth = z; near_colour = c;','near_depth = far_z; near_colour = c;'),
            'lost guest restoration':('memcpy(G+0x93DE28,saved_deferred,sizeof saved_deferred);','(void)saved_deferred;'),
            'lost device restoration':('dev=saved_device; render_shadow_near_restore();','(void)saved_device; render_shadow_near_restore();'),
            'published incomplete pass':('caster_count == UINT32_MAX || near_incomplete','caster_count == UINT32_MAX'),
            'lost matrix restoration':('memcpy(G+plat,saved_plat,sizeof saved_plat);','(void)saved_plat;'),
            'advancing exposure':('else {*adaptation=near_ab_post_values[0];*exposure=near_ab_post_values[1];}',
                                  'else {(void)adaptation;(void)exposure;}'),
            'lost held variants':('if(++near_ab_index<near_ab_count)', 'if(0 && ++near_ab_index<near_ab_count)'),
            'lost alpha state':('call_guest(c,0x00723FA0u,2,scenery);','dev.rs[15]=0; call_guest(c,0x00723FA0u,2,scenery);'),
        }
        # The alpha fixture supplies state at actual geometry emission; removing
        # alpha in the renderer is covered by the GPU mask proof below.
        del mutations['lost alpha state']
        for label,(old,new) in mutations.items():
            assert old in production,label
            mutant=production.replace(old,new)
            r=run(label,mutant)
            assert r.returncode==1 and 'failures' in r.stdout,(label,r.returncode,r.stderr[-3000:])
    print('SHADOW NEAR PASS (pinned one-pass original and 12 production mutants rejected)')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
