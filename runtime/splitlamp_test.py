#!/usr/bin/env python3
"""Recording test of actual render.m lamp_prepare/record/open/flush/source + lampbatch.m.
No GPU/device creation. ASan/UBSan: 96 checks / 0 failures; 4 frame clears,
7 passes / 7 replays (batched and immediate). 3/3 controls rejected:
second clear 6 failures, mixed owner 1, shared validity 1.
Post crop/filter isolation is covered by splitpost_test.py's real renderer/recording encoder.
Harness repair plan: extract only lamp-owned state/functions, then rerun the
unchanged positive/control cases and the entire offline suite. Adjacent optional
render features must not add dependencies to this lamp-only recording fixture.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]

PRE=r'''
#import "render_internal.h"
#include "lampbatch.h"
#include <stdio.h>
#include <string.h>
static unsigned checks,failures,clears,passes,replays,selected_owner;
#define CHECK(x) do{++checks;if(!(x)){++failures;if(failures<12)printf("FAIL %d %s\n",__LINE__,#x);}}while(0)
uint8_t *G;
int diag_on,gpt_on,pass_dirty;
uint64_t frame_serial=1,done_serial;
Device dev;
id<MTLDevice> mtl;
id<MTLCommandBuffer> cmd;
id<MTLRenderCommandEncoder> enc;
RenderTex *enc_ms[5];
uint8_t enc_resolve[5];
uint32_t enc_samples=4,q_active;
static struct{int a;}eb;
static unsigned lamp_n;
static id<MTLTexture> scene,depth;
unsigned lampglow_owner(void){return selected_owner;}
void lampglow_batch(int why,unsigned n,unsigned refused){(void)why;(void)n;CHECK(!refused);}
static void lamp_hash_diag(void){}
void diag_event(const char*s,...){(void)s;}
void lampglow_replayed(int ok){(void)ok;}
void ensure_cmd(void){}
void gpt_render_auto(MTLRenderPassDescriptor*r,const char*s){(void)r;(void)s;}
void gpt_name(id<MTLCommandEncoder>e){(void)e;}
void msaa_resolve(RenderTex*r){(void)r;}
void ms_attach(MTLRenderPassAttachmentDescriptor*a,RenderTex*r,int level){(void)level;a.resolveTexture=r->tex;}
void drop_ms_texture(RenderTex*r){r->ms=nil;r->tex=nil;}
id<MTLTexture> surface_target(uint32_t h,NSUInteger*level,NSUInteger*slice){*level=*slice=0;return h==1?scene:h==2?depth:nil;}
static void draw_impl(uint32_t,uint32_t,int,int32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);
static void lamp_flush(int);

@interface LTexture:NSObject
@property NSUInteger width,height,sampleCount;
@property MTLPixelFormat pixelFormat;
@property unsigned top,bottom;
@end
@implementation LTexture
@end
@interface LDevice:NSObject
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)d;
@end
@implementation LDevice
- (id)newTextureWithDescriptor:(MTLTextureDescriptor*)d{LTexture*t=[LTexture new];t.width=d.width;t.height=d.height;t.sampleCount=d.sampleCount;t.pixelFormat=d.pixelFormat;return t;}
@end
id<MTLTexture> new_ms_texture(MTLPixelFormat f,NSUInteger w,NSUInteger h,uint32_t n){MTLTextureDescriptor*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];d.sampleCount=n;return[mtl newTextureWithDescriptor:d];}
@interface LEncoder:NSObject
@property LTexture *target;
@property(nonatomic) MTLViewport viewport;
@property MTLScissorRect scissor;
@property unsigned owner;
- (void)setViewport:(MTLViewport)v;
- (void)setScissorRect:(MTLScissorRect)r;
- (void)setFragmentBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i;
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n;
@end
@implementation LEncoder
- (void)setViewport:(MTLViewport)v{_viewport=v;}
- (void)setScissorRect:(MTLScissorRect)r{_scissor=r;}
- (void)setFragmentBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{(void)i;CHECK(n==4);memcpy(&_owner,p,4);}
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n{(void)t;(void)s;(void)n;
 CHECK(self.owner>=1&&self.owner<=2);CHECK(self.viewport.originY==self.scissor.y);
 CHECK(self.viewport.height==self.scissor.height);CHECK(self.target.width==1280&&self.target.height==721);
 if(self.scissor.y)self.target.bottom+=200;else self.target.top+=100;++replays;
}
@end
@interface LCommand:NSObject
- (id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)r;
@end
@implementation LCommand
- (id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)r{++passes;LTexture*t=(id)r.colorAttachments[0].texture;
 CHECK(r.depthAttachment.texture==depth);CHECK(r.depthAttachment.loadAction==MTLLoadActionLoad);
 CHECK(r.stencilAttachment.texture==depth);CHECK(t.width==depth.width&&t.height==depth.height&&t.sampleCount==depth.sampleCount);
 if(r.colorAttachments[0].loadAction==MTLLoadActionClear){++clears;t.top=t.bottom=0;}
 else CHECK(r.colorAttachments[0].loadAction==MTLLoadActionLoad);
 LEncoder*e=[LEncoder new];e.target=t;return e;
}
@end
'''
POST=r'''
void end_pass(void){enc=nil;pass_dirty=1;if(lamp_n&&!lamp_flushing&&!lamp_replay)lamp_flush(0);}
static void draw_impl(uint32_t a,uint32_t b,int c,int32_t d,uint32_t e,uint32_t f,uint32_t g,uint32_t h,uint32_t i,uint32_t j,uint32_t k){
 (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g;(void)h;(void)i;(void)j;(void)k;
 [enc setViewport:(MTLViewport){dev.viewport[0],dev.viewport[1],dev.viewport[2],dev.viewport[3],0,1}];
 [enc setScissorRect:(MTLScissorRect){dev.scissor[0],dev.scissor[1],dev.scissor[2]-dev.scissor[0],dev.scissor[3]-dev.scissor[1]}];
 unsigned o=selected_owner;[enc setFragmentBytes:&o length:4 atIndex:0];
 [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
}
static void select_owner(unsigned id){selected_owner=id;dev.viewport[0]=0;dev.viewport[1]=id==2?360:0;dev.viewport[2]=1280;dev.viewport[3]=id==2?361:360;
 dev.viewport[4]=0;dev.viewport[5]=0x3F800000;dev.scissor[0]=0;dev.scissor[1]=dev.viewport[1];dev.scissor[2]=1280;dev.scissor[3]=dev.viewport[1]+dev.viewport[3];dev.rs[174]=1;}
static void record(void){CHECK(lamp_prepare());lamp_record(6,2,0,0,0,0,0,0,0,0,0);}
int main(void){@autoreleasepool{
 G=calloc(1,16*1024*1024);M32(0x919654)=1;M32(0x9196C4)=2;M32(0x919734)=3;
 mtl=(id)[LDevice new];cmd=(id)[LCommand new];scene=new_ms_texture(MTLPixelFormatBGRA8Unorm,1280,721,4);depth=new_ms_texture(MTLPixelFormatDepth32Float_Stencil8,1280,721,4);
 dev.rt[0]=1;dev.ds=2;dev.rs[7]=dev.rs[27]=1;dev.rs[20]=dev.rs[19]=2;
 CHECK(!render_lamp_source_view(0x919650)&&!render_lamp_source_view(0x9196C0));
 select_owner(1);record();CHECK(passes==0&&lamp_n==1);
 select_owner(2);record();CHECK(passes==1&&lamp_n==1); // same depth, different owner still MUST flush
 CHECK(((LTexture*)lamp_rt.ms).top==100&&((LTexture*)lamp_rt.ms).bottom==0);
 CHECK(render_lamp_source_view(0x9196C0)==lamp_rt.tex);CHECK(passes==2&&clears==1&&replays==2);
 CHECK(((LTexture*)lamp_rt.ms).top==100&&((LTexture*)lamp_rt.ms).bottom==200);
 CHECK(render_lamp_source_view(0x919650)==lamp_rt.tex);CHECK(!render_lamp_source_view(0x919730));
 ++frame_serial;select_owner(1);record();CHECK(!render_lamp_source_view(0x9196C0));CHECK(render_lamp_source_view(0x919650));
 CHECK(clears==2);CHECK(((LTexture*)lamp_rt.ms).top==100&&((LTexture*)lamp_rt.ms).bottom==0);
 // Even identical viewport/scissor and depth cannot mix ownership in a batch.
 ++frame_serial;select_owner(1);record();unsigned before=passes;selected_owner=2;record();CHECK(passes==before+1&&lamp_owner==2);
 lamp_flush(3);CHECK(clears==3);
 // Immediate replay shares the same once-per-frame clear and validity contract.
 ++frame_serial;select_owner(1);CHECK(lamp_prepare());lamp_open(depth,1);draw_impl(0,0,0,0,0,0,0,0,0,0,0);end_pass();
 select_owner(2);lamp_open(depth,2);draw_impl(0,0,0,0,0,0,0,0,0,0,0);end_pass();CHECK(clears==4);
 CHECK(((LTexture*)lamp_rt.ms).top==100&&((LTexture*)lamp_rt.ms).bottom==200);
 q_active=1;CHECK(!lamp_prepare());q_active=0;((LTexture*)depth).height=720;CHECK(!lamp_prepare());((LTexture*)depth).height=721;
 printf("splitlamp: %u checks, %u failures, %u clears / %u passes / %u replays\n",checks,failures,clears,passes,replays);
 free(G);return failures?1:0;
}}
'''


def function(src,name):
    return re.search(r'^(?:static )?(?:void|int|id<MTLTexture>) '+name+r'\([^;{}]*\) \{.*?^}',src,re.S|re.M)[0]


def source():
    src=(ROOT/'runtime/render.m').read_text()
    storage='\n'.join(re.search(r'^static '+declaration+r'.*$',src,re.M)[0] for declaration in (
        r'RenderTex lamp_rt;',r'int lamp_replay;',r'uint64_t lamp_serial;',r'uint64_t lamp_valid\[2\];'))+'\n'
    storage+=function(src,'lamp_prepare')+'\n'
    storage+=src[src.index('static id lamp_rec;'):src.index('void end_pass(void) {')]
    return PRE+storage+'\n'+ '\n'.join(function(src,n) for n in ['lamp_open','lamp_flush','lamp_record','render_lamp_source_view'])+POST


def main():
    src=source()
    mutants={
      'second clear':('lamp_serial == frame_serial ? MTLLoadActionLoad','lamp_serial == frame_serial && owner == 1 ? MTLLoadActionLoad'),
      'mixed owner':(' || owner != lamp_owner',''),
      'shared validity':('lamp_valid[owner - 1] != frame_serial','lamp_serial != frame_serial'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-splitlamp-') as tmp:
        tmp=Path(tmp)
        def run(label,text):
            (tmp/'test.m').write_text(text)
            r=subprocess.run(['clang','-fobjc-arc','-Wno-objc-method-access','-Wno-incompatible-pointer-types','-Wno-deprecated-declarations',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-g','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),
                '-framework','Foundation','-framework','Metal',str(tmp/'test.m'),str(ROOT/'runtime/lampbatch.m'),'-o',str(tmp/'test')],capture_output=True,text=True)
            if r.returncode:raise RuntimeError(r.stderr[-6000:])
            r=subprocess.run([str(tmp/'test')],capture_output=True,text=True)
            print(label,r.stdout.strip().splitlines()[-1] if r.stdout.strip() else '(no output)')
            if r.returncode and label=='positive':print(r.stdout[:2500],r.stderr[-2500:])
            return r
        if run('positive',src).returncode:raise SystemExit(1)
        for label,(old,new) in mutants.items():
            assert src.count(old)==1,(label,src.count(old))
            r=run('mutant '+label,src.replace(old,new))
            if r.returncode!=1 or 'failures' not in r.stdout:raise SystemExit('mutant not rejected: '+label+' '+r.stderr[-1500:])
    print('SPLIT LAMP PASS (3/3 mutants rejected)')


if __name__=='__main__':main()
