#!/usr/bin/env python3
"""Actual tree emission/gate/recorder regression tests; no GPU or game.
Expected: fixed renderer 0 failures, old renderer 7 failures; fixed recorder 0
retained objects, old recorder 100 retained objects (unreplayed invocation cycles).
Built-in SSAO selector: 0/6 failures; default-Off mutant: 3/6 (builtin_features_test).
Half R8/4x + D16 coverage: 0 failures; BGRA allocator mutant: 1 failure.
RGB-only multiply: 0 failures; disabled attenuation mutant: 1 failure.
Typed coverage lists: 0 failures; broken inline byte copy: 1 failure.
Proof tree replay state: 0 failures; missing reset: 3 failures.
Disabled diagnostic clocks: 0 calls/failures; unconditional clock mutant: 1 call/failure.
Harness repair: extract the multiply descriptor's exact function body; do not
include the adjacent optional smoke shader/library builder. Assertions and the
disabled-attenuation control remain unchanged.
"""
from pathlib import Path
import re, subprocess, tempfile, sys, ast
ROOT=Path(__file__).resolve().parents[1]
def run(cmd):
    p=subprocess.run([str(x) for x in cmd],capture_output=True,text=True)
    if p.returncode: raise RuntimeError((p.stdout+p.stderr)[-2500:])
    return p.stdout

def function(src,name):
    hit=re.search(r'(?:static )?(?:int|void|id<MTLRenderPipelineState>) '+name+r'\([^;]*?\)\s*\{',src,re.S)
    a=hit.start();i=hit.end();level=1
    while level:
        if src[i]=='{':level+=1
        if src[i]=='}':level-=1
        i+=1
    return src[a:i]

with tempfile.TemporaryDirectory(prefix='ssao-review-') as td:
    td=Path(td)
    pre=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static int failures,emits,ssao_replay=2,ssao_tree_fringe=1,ssao_reference_record,q_active,lamp_replay,enabled=1,chain=1;
#define CHECK(x) do{if(!(x)){++failures;printf("FAIL %s\n",#x);}}while(0)
static uint8_t *G;
#define M32(a) (*(uint32_t *)(G+(a)))
#define M8(a) (*(uint8_t *)(G+(a)))
int x360_post_ssao_enabled(void){return enabled;}
int x360_post_chain_on(void){return chain;}
static struct{uint32_t rs[256];} dev;
typedef struct { int alpha_func;float alpha_ref;} SmPSAux;
static struct { SmPSAux pa;} eb;
static struct { int alpha_ref;} mat_trees_draw;
static struct { id pso,pso1;float k[32][4]; } trees_now;
static int prepares;static void prepare_fixture(void){++prepares;trees_now.pso=@1;trees_now.pso1=@2;}
#define trees_prepare(...) prepare_fixture()
#define MAT_TREES_VS_BUF 1
#define MAT_TREES_PS_BUF 2
#define MAT_WATER_VS_BUF 4
#define MAT_WATER_PS_BUF 5
@interface Encoder:NSObject
@end
@implementation Encoder
-(void)setRenderPipelineState:(id)x{(void)x;}
-(void)setVertexBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{(void)p;(void)n;(void)i;}
-(void)setFragmentBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{if(i==3&&n==sizeof(SmPSAux))memcpy(&eb.pa,p,n);}
-(void)setDepthStencilState:(id)x{(void)x;}
-(void)setFragmentTexture:(id)x atIndex:(NSUInteger)i{(void)x;(void)i;}
@end
static id<MTLRenderCommandEncoder> enc;
static id depth_state_for(const uint32_t*x){(void)x;return nil;}
static void mat_trees_emit(int b){(void)b;++emits;}
typedef struct {int unused;} GrassDraw;
typedef struct {int unused;} AuxDraw;
typedef struct {id pso,normal,reflection;float k[32][4];int matched;} WaterDraw;
static int glass_emit(void(^g)(void)){(void)g;return 0;}
static int bone_emit(void(^g)(void)){(void)g;return 0;}
static void aux_emit(AuxDraw*a,void(^g)(void)){(void)a;(void)g;}
static void grass_emit(GrassDraw*a,void(^g)(void)){(void)a;g();}
static void mat_water_emit(int x){(void)x;}
'''
    fixed=(ROOT/'runtime/render.m').read_text()
    old=fixed.replace(function(fixed,'ssao_can_record'),'static int ssao_can_record(void){return x360_post_ssao_enabled() && x360_post_chain_on() && M32(0x925E90)==6;}')
    old=old.replace('(dev.rs[27] || ssao_tree_fringe)','dev.rs[27]')
    old=old.replace('    trees_prepare(vs, ps,','    if (!ssao_replay) trees_prepare(vs, ps,',1)
    old=old.replace('if (ssao_replay) { if (!trees_emit(geometry)) geometry();return; }','if (ssao_replay) { geometry();return; }')
    (td/'old-render.m').write_text(old)
    for mode,path in [('fixed',ROOT/'runtime/render.m'),('old',td/'old-render.m')]:
        if not path.exists(): continue
        src=path.read_text()
        if 'static int ssao_can_record(void)' in src:gate=function(src,'ssao_can_record')
        else:
            old=re.search(r'if \(!lamp_replay && x360_post_ssao_enabled\(\).*?\) \{',src)[0][4:-3]
            gate='static int ssao_can_record(void){return '+old+';}'
        m=re.search(r'if \((\(?dev\.rs\[27\].*?)\) \{\n\s*ssao_record',src)
        predicate='static int capture(void){return '+m[1]+';}'
        body=pre+gate+'\n'+predicate+'\n'+function(src,'trees_emit')+'\n'+function(src,'material_emit')+r'''
int main(void){@autoreleasepool{
 G=calloc(1,0x1000000);M32(0x925E90)=6;M32(0x901828)=1;M32(0x982AF0)=1;M8(0x8F9B5C)=1;
 CHECK(ssao_can_record());M32(0x901828)=0;CHECK(!ssao_can_record());M32(0x901828)=1;
 M8(0x8F9B5C)=0;CHECK(!ssao_can_record());M8(0x8F9B5C)=1;M32(0x982AF0)=0;CHECK(!ssao_can_record());
 M32(0x982AF0)=1;M32(0x901828)=0;int queued=0;for(int frame=0;frame<1000;++frame)if(ssao_can_record())++queued;CHECK(queued==0);printf("disabled frames queued: %d/1000\n",queued);
 dev.rs[168]=7;CHECK(capture());enc=(id)[Encoder new];PREPARE_TREE_CALL
 CHECK(prepares==1);mat_trees_draw.alpha_ref=95;
 eb.pa=(SmPSAux){7,95.f/255};__block int mask=255;
 GrassDraw grass={0};WaterDraw water={0};AuxDraw aux={0};
 material_emit(&grass,&water,&aux,^{float alpha=.2f;if(eb.pa.alpha_func==0||(eb.pa.alpha_func==2&&alpha<eb.pa.alpha_ref)||(eb.pa.alpha_func==7&&alpha>=eb.pa.alpha_ref))mask=0;});
 CHECK(mask==0);CHECK(emits==0);free(G);printf("renderer failures: %d\n",failures);return 0;
}}
'''
        prepare=re.search(r'^.*trees_prepare\(vs, ps,.*$',src,re.M)[0]
        body=body.replace('PREPARE_TREE_CALL',prepare)
        f=td/(mode+'.m');f.write_text(body);exe=td/mode
        run(['clang','-O1','-w','-fobjc-arc',f,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip())
        n=int(re.search(r'failures: (\d+)',output)[1]);assert n==0 if mode=='fixed' else n>=7
    lifetime=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "lampbatch.h"
#include <stdio.h>
@interface Sink:NSObject
@property int value;
@end
@implementation Sink
-(void)setVertexBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{(void)i;if(n==sizeof(int))self.value=*(const int*)p;}
@end
int main(void){@autoreleasepool{
 id rec=lamprec_new();int bytes=123;[(id<MTLRenderCommandEncoder>)rec setVertexBytes:&bytes length:sizeof bytes atIndex:0];bytes=0;
 Sink *sink=[Sink new];lamprec_replay(rec,(id)sink);if(sink.value!=123||lamprec_ops(rec)!=0)return 3;

 int retained=0;
 for(int i=0;i<100;++i){__weak id weak;@autoreleasepool{
  id r=lamprec_new();weak=r;int k=123;[(id<MTLRenderCommandEncoder>)r setVertexBytes:&k length:sizeof k atIndex:0];r=nil;
 }if(weak)++retained;}
 printf("retained recorders: %d\n",retained);return 0;
}}
'''
    (td/'lifetime.m').write_text(lifetime)
    old_rec=(ROOT/'runtime/lampbatch.m').read_text().replace('    inv.target=nil; // queued calls own their arguments, never their recorder\n','')
    (td/'old-rec.m').write_text(old_rec)
    for mode,path in [('fixed',ROOT/'runtime/lampbatch.m'),('old',td/'old-rec.m')]:
        if not path.exists():continue
        exe=td/('lifetime-'+mode)
        run(['clang','-O1','-w','-fobjc-arc','-I',ROOT/'runtime',td/'lifetime.m',path,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip());n=int(re.search(r': (\d+)',output)[1]);assert n==(0 if mode=='fixed' else 100)
    # Execute the production typed recorder and its nested render-thread replay
    # without requesting a device. Stack bytes and resource lifetimes matter here.
    typed=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
id<MTLDevice> mtl;id<MTLCommandQueue> queue;int diag_on;
uint64_t host_ticks(void){return 0;}
void diag_event(const char *fmt,...){(void)fmt;}
extern id rt_render_list_new(void);
extern unsigned rt_render_list_ops(id);
extern void rt_render_list_replay(id,id<MTLRenderCommandEncoder>);
static int failures;
#define CHECK(x) do{if(!(x)){++failures;printf("FAIL %s\n",#x);}}while(0)
@interface Sink:NSObject
@property int calls;
@end
@implementation Sink
-(void)setVertexBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{++_calls;CHECK(n==16000&&i==17);const unsigned char*b=p;for(int j=0;j<16000;++j)if(b[j]!=(unsigned char)j){CHECK(0);break;}}
-(void)setFragmentBytes:(const void*)p length:(NSUInteger)n atIndex:(NSUInteger)i{++_calls;CHECK(n==sizeof(int)&&i==3&&*(const int*)p==123);}
-(void)setFragmentTexture:(id)t atIndex:(NSUInteger)i{++_calls;CHECK(t!=nil&&i==4);}
-(void)setViewport:(MTLViewport)v{++_calls;CHECK(v.originX==12.5&&v.width==1720&&v.zfar==1);}
-(void)setScissorRect:(MTLScissorRect)v{++_calls;CHECK(v.x==3&&v.y==5&&v.width==100&&v.height==200);}
-(void)drawIndexedPrimitives:(MTLPrimitiveType)t indexCount:(NSUInteger)n indexType:(MTLIndexType)it indexBuffer:(id)b indexBufferOffset:(NSUInteger)o instanceCount:(NSUInteger)k baseVertex:(NSInteger)bv baseInstance:(NSUInteger)bi{++_calls;CHECK(t==MTLPrimitiveTypeTriangle&&n==6&&it==MTLIndexTypeUInt16&&b!=nil&&o==8&&k==2&&bv==-7&&bi==9);}
@end
int main(void){@autoreleasepool{
 Sink*s=[Sink new];__weak id weak;
 @autoreleasepool{
  id leaf=rt_render_list_new();id<MTLRenderCommandEncoder>e=leaf;
  unsigned char bytes[16000];for(int j=0;j<16000;++j)bytes[j]=(unsigned char)j;
  [e setVertexBytes:bytes length:sizeof bytes atIndex:17];memset(bytes,0,sizeof bytes);
  int value=123;[e setFragmentBytes:&value length:sizeof value atIndex:3];value=0;
  id resource=[NSObject new];weak=resource;[e setFragmentTexture:resource atIndex:4];
  [e setViewport:(MTLViewport){12.5,0,1720,1080,0,1}];
  [e setScissorRect:(MTLScissorRect){3,5,100,200}];
  [e drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:6 indexType:MTLIndexTypeUInt16 indexBuffer:resource indexBufferOffset:8 instanceCount:2 baseVertex:-7 baseInstance:9];
  resource=nil;CHECK(weak!=nil);CHECK(rt_render_list_ops(leaf)>0);
  id parent=rt_render_list_new();rt_render_list_replay(leaf,parent);
  CHECK(rt_render_list_ops(leaf)==0);CHECK(s.calls==0);
  // Later reuse/discard must never overwrite the queued immutable snapshot.
  value=999;[e setFragmentBytes:&value length:sizeof value atIndex:3];rt_render_list_replay(leaf,nil);
  CHECK(weak!=nil);rt_render_list_replay(parent,(id)s);CHECK(s.calls==6);
  CHECK(rt_render_list_ops(parent)==0);leaf=nil;parent=nil;e=nil;
 }
 CHECK(weak==nil);printf("typed recorder failures: %d\n",failures);return 0;
}}
'''
    (td/'typed.m').write_text(typed)
    production=(ROOT/'runtime/render_thread.m').read_text()
    (td/'broken-typed.m').write_text(production.replace('memcpy(a + 2, p, n);','memset(a + 2, 0, n);',1))
    for mode,path in [('fixed',ROOT/'runtime/render_thread.m'),('broken',td/'broken-typed.m')]:
        exe=td/('typed-'+mode)
        run(['clang','-O1','-w','-fobjc-arc','-I',ROOT/'runtime','-I',ROOT/'recomp','-I',ROOT/'recomp/out',td/'typed.m',path,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip());n=int(re.search(r'failures: (\d+)',output)[1]);assert n==(0 if mode=='fixed' else 1)
    # Every proof replay must start from the original material state; also prove
    # the production per-draw clocks are silent when diagnostics are disabled.
    replay_fixture=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int failures,diag_on,ssao_cpu_diag,clocks,ssao_tree_fringe=1,ssao_reference_record,ssao_cpu_reference_record,ssao_replay,draws;
#define CHECK(x) do{if(!(x)){++failures;printf("FAIL %s\n",#x);}}while(0)
static uint64_t host_ticks(void){++clocks;return 0;}
static uint64_t ssao_diag_record_ns,ssao_diag_draws;
typedef struct{int skip;}MatTreesDraw;
#define TREES_NO_DRAW 0
static MatTreesDraw mat_trees_draw={7};
typedef struct{id<MTLTexture>ms;}RenderTex;
static RenderTex target;
static RenderTex *ssao_target(void){return &target;}
static int ssao_proof(void){return 1;}
static struct{int value;}eb,ssao_eb,ssao_reference_eb,ssao_cpu_reference_eb,ssao_saved_eb;
static id<MTLRenderCommandEncoder>enc,ssao_saved_enc;
static id ssao_rec,ssao_reference_rec,ssao_cpu_reference_rec;
static id<MTLTexture>ssao_coverage_z,ssao_depth,ssao_half_depth;
static RenderTex *enc_ms[5],*ssao_saved_ms[5];
static uint8_t enc_resolve[5],ssao_saved_resolve[5];
static int pass_dirty,ssao_saved_dirty;
static uint32_t enc_samples,ssao_saved_samples;
static struct{uint32_t rs[256];}dev;
#define draw_impl(...) do{CHECK(mat_trees_draw.skip==TREES_NO_DRAW);mat_trees_draw.skip=99;++draws;}while(0)
'''
    replay_body=function(fixed,'ssao_record')
    reset='mat_trees_draw=saved_tree;if(ssao_tree_fringe)mat_trees_draw.skip=TREES_NO_DRAW;'
    assert reset in replay_body
    for mode,body in [('fixed',replay_body),('broken',replay_body.replace(reset,'')),
                      ('clock-mutant',replay_body.replace('uint64_t t0=ssao_cpu_diag?host_ticks():0;','uint64_t t0=host_ticks();',1))]:
        f=td/('proof-state-'+mode+'.m');exe=td/('proof-state-'+mode)
        f.write_text(replay_fixture+body+r'''
int main(void){@autoreleasepool{
 dev.rs[15]=27;enc=(id)@4;ssao_rec=@1;ssao_reference_rec=@2;ssao_cpu_reference_rec=@3;
 eb.value=123;pass_dirty=1;enc_samples=4;
 ssao_record(1,1,0,0,0,0,0,0,0,0,0);
 CHECK(draws==3);CHECK(clocks==0);CHECK(dev.rs[15]==27&&mat_trees_draw.skip==7);
 CHECK(eb.value==123&&pass_dirty==1&&enc_samples==4&&enc==(id)@4);
 CHECK(!ssao_replay&&!ssao_reference_record&&!ssao_cpu_reference_record);
 printf("proof replay failures: %d\n",failures);return 0;
}}
''')
        run(['clang','-O1','-w','-fobjc-arc',f,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip());n=int(re.search(r'failures: (\d+)',output)[1]);assert n=={'fixed':0,'broken':3,'clock-mutant':1}[mode]
    # Recording-only resource contract: half R8/4x resolves beside matching D16 depth.
    layout=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <string.h>
static int failures;
#define CHECK(x) do{if(!(x)){++failures;printf("FAIL %s\n",#x);}}while(0)
@interface Texture:NSObject
@property NSUInteger width,height,sampleCount;
@property MTLPixelFormat pixelFormat;
@end
@implementation Texture
@end
@interface Device:NSObject
@end
@implementation Device
-(id)newTextureWithDescriptor:(MTLTextureDescriptor*)d{Texture*t=[Texture new];t.width=d.width;t.height=d.height;t.sampleCount=d.sampleCount;t.pixelFormat=d.pixelFormat;return t;}
@end
@interface Command:NSObject
@property(strong) MTLRenderPassDescriptor *last;
@end
@implementation Command
-(id)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor*)d{self.last=d;return [NSObject new];}
@end
typedef struct { id<MTLTexture> tex,ms;uint32_t w,h;int stale;} RenderTex;
static RenderTex ssao_mask,ssao_reference_mask,ssao_cpu_reference_mask;
static id<MTLTexture> ssao_half_depth;
static int ssao_reference_record,ssao_cpu_reference_record;
static uint64_t ssao_half_serial;
static int ssao_proof(void){return 0;}
static const MTLSamplePosition ssao_samples[4]={{.25f,.25f},{.75f,.25f},{.25f,.75f},{.75f,.75f}};
static RenderTex *ssao_target(void){return ssao_reference_record?&ssao_reference_mask:&ssao_mask;}
static id<MTLDevice> mtl;
static id<MTLTexture> backbuffer_tex;
static id<MTLCommandBuffer> cmd;
static id<MTLRenderCommandEncoder> enc;
static int gpt_on,pass_dirty;
static uint32_t enc_samples;
static uint64_t ssao_serial,ssao_cleared,frame_serial=1;
static struct{int dummy;}eb;
static void end_pass(void){enc=nil;}
static void ensure_cmd(void){}
static void drop_ms_texture(RenderTex*r){r->ms=nil;}
static void ms_attach(id x,RenderTex*r,int n){(void)x;(void)r;(void)n;CHECK(0);}
static void gpt_render_auto(id x,const char*s){(void)x;(void)s;}
static void gpt_name(id x){(void)x;}
'''
    src=(ROOT/'runtime/render.m').read_text()
    for mode in ('r8-fixed','bgra-mutant'):
        alloc=function(src,'ssao_prepare')
        if mode=='bgra-mutant':alloc=alloc.replace('MTLPixelFormatR8Unorm','MTLPixelFormatBGRA8Unorm')
        body=layout+alloc+chr(10)+function(src,'ssao_open')+r'''
int main(void){@autoreleasepool{
 mtl=(id)[Device new];Command*c=[Command new];cmd=(id)c;
 Texture*z=[Texture new];z.width=3440;z.height=2160;z.sampleCount=4;z.pixelFormat=MTLPixelFormatDepth32Float_Stencil8;
 backbuffer_tex=(id)z;CHECK(ssao_prepare((id)z));
 CHECK(ssao_mask.tex.width==1720&&ssao_mask.tex.height==1080);
 CHECK(ssao_mask.tex.pixelFormat==MTLPixelFormatR8Unorm);CHECK(ssao_mask.tex.sampleCount==1&&ssao_mask.ms.sampleCount==4);
 CHECK(ssao_half_depth.width==1720&&ssao_half_depth.height==1080&&ssao_half_depth.sampleCount==4&&ssao_half_depth.pixelFormat==MTLPixelFormatDepth16Unorm);
 Texture*resolved=[Texture new];resolved.width=3440;resolved.height=2160;resolved.sampleCount=1;resolved.pixelFormat=z.pixelFormat;
 ssao_open(ssao_half_depth);CHECK(c.last.depthAttachment.texture==ssao_half_depth);
 CHECK(c.last.colorAttachments[0].texture==ssao_mask.ms);CHECK(c.last.colorAttachments[0].resolveTexture==ssao_mask.tex);CHECK(c.last.depthAttachment.loadAction==MTLLoadActionLoad);
 CHECK(enc_samples==4);printf("coverage layout failures: %d",failures);puts("");return 0;
}}
'''
        f=td/(mode+'.m');f.write_text(body);exe=td/mode
        run(['clang','-O1','-w','-fobjc-arc',f,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip())
        assert int(re.search(r': (\d+)',output)[1])==(0 if mode=='r8-fixed' else 1)

    # Actual multiply descriptor: shade RGB in place while retaining scene alpha.
    xpost=(ROOT/'runtime/render_post.m').read_text()
    builder=function(xpost,'x_contact_multiply')
    stub=layout.replace('@implementation Device', '@implementation Device\n-(id)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor*)d error:(NSError**)e{(void)e;return d;}')
    for mode in ('multiply-fixed','multiply-unshaded'):
        actual=builder if mode=='multiply-fixed' else builder.replace('MTLBlendFactorSourceAlpha','MTLBlendFactorOne')
        body=stub+actual+r'''
int main(void){@autoreleasepool{
 mtl=(id)[Device new];MTLRenderPipelineDescriptor*d=(id)x_contact_multiply(nil,MTLPixelFormatBGRA8Unorm);
 CHECK(d.colorAttachments[0].blendingEnabled);CHECK(d.colorAttachments[0].sourceRGBBlendFactor==MTLBlendFactorZero);
 CHECK(d.colorAttachments[0].destinationRGBBlendFactor==MTLBlendFactorSourceAlpha);
 CHECK(d.colorAttachments[0].writeMask==(MTLColorWriteMaskRed|MTLColorWriteMaskGreen|MTLColorWriteMaskBlue));
 printf("multiply failures: %d\n",failures);return 0;
}}'''
        f=td/(mode+'.m');f.write_text(body);exe=td/mode
        run(['clang','-O1','-w','-fobjc-arc',f,'-framework','Metal','-framework','Foundation','-o',exe])
        output=run([exe]);print(mode,output.strip())
        assert int(re.search(r': (\d+)',output)[1])==(0 if mode=='multiply-fixed' else 1)

    # The approved feature has no settings row; test its production selector
    # and obsolete profile keys using the shared built-in feature harness.
    print(run([sys.executable, '-I', ROOT/'runtime/builtin_features_test.py']).strip())
    print('ssao_review_test: PASS; tree fringe, disabled VT gate, recorder lifetime')
