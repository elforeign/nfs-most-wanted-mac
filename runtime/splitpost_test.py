#!/usr/bin/env python3
"""Split post: actual parameter/scope hooks, fake guest and recording Metal encoder; no GPU/Unicorn.
ASan/UBSan hooks: 785 checks / 0 failures. Encoder: 1,313 checks / 0 failures;
32 split composites, 26 lens submissions (two non-MSAA PC local targets), 90 bounded crops,
zero split readback waits / eight SP waits. 36/36 mutants rejected (including forced rain).
Luminance opt-in: 1,425 recording checks / 0 failures, 12 startup waits across three
epochs; writable storage through 16 delayed-completion frames, safe saturation/resume.
Forced wait/oldest/pending read/overwrite mutants fail 49/1/22/44 checks;
missing startup wait/black readiness/stale epoch readiness fail 32/13/8.
Pinned Off/empty snapshot digest passes all four modes; omitted-wait control fails all four.
New controls: missing PC parent 2 failures; omitted SP wait 24; missing PC probe accounting 2.
Restart-history-stale/readback-stale 7/1; current-serial-wait 91 (50 attempted waits),
shared-adaptation 10, P1-depth 10, whole-source 212, missing-bounds 32. Other hooks:
shared-transition-records 222, P1-PC-motion-camera 3, shared-PC-motion 1, diagnostics-always-on 1,
P1-motion-basis 10, shared-state 81, P1-camera 20, shared-deferred 25, shared-VT 278, bottom-Y-zero 26;
short-dev-args: ASan stack-buffer-overflow. Other encoder controls: P1-depth-rect 12,
P1-lamp-source 22, shader-bounds 2, shared-ring 19, unsafe-slot-reuse 9, full-aspect 24, whole-lens-copy 72.
Five MSL variants compile; SP shader identical (11,905 bytes). Two six-frame SP command/consumed-
constant/readback snapshots match pinned pre-split 4175000^ (57,396 bytes; unset/1), plus three exact
SP guest/CPU/parameter snapshots match HEAD (50,331,648 guest bytes).
Covers 1280x721/719, separate focal distances/speeds/bases/brightness/VT records/drop lists,
PC targets/replay/failure fallback, viewport depth range/scissor, completed-only readback and ring saturation.
Recording objects prove ownership/submission contracts, not GPU pixel output; MCP probes cover screen reach.
"""
from pathlib import Path
import ast
import hashlib
import os
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SRC=(ROOT/'runtime/x360_post.m').read_text()
RENDER=(ROOT/'runtime/render_post.m').read_text()

def run(cmd):
    p=subprocess.run([str(x) for x in cmd],text=True,capture_output=True)
    if p.returncode:
        raise RuntimeError(f'{cmd[0]} exit {p.returncode}\n'+(p.stdout+p.stderr)[-7000:])
    return p.stdout

PRE=r'''
#include "host.h"
#include "settings.h"
#include <stdlib.h>
#include <math.h>
#include <stdarg.h>
uint8_t *G;
static struct {uint32_t ds,rs[256],rt[4],viewport[6],scissor[4];} dev;
int diag_on;
const char *diag_dir="/tmp";
int probe_sig;
static unsigned checks,failures,renders,rains,updates,frame,bank,pc_testing,pc_copies,pc_rains,pc_composites,pc_lens,pc_applied,pc_motion_calls,fail_render,diag_ticks;
#define CHECK(x) do { ++checks;if(!(x)) {++failures;if(failures<12)printf("FAIL %d %s\n",__LINE__,#x);} }while(0)
#define GAMEFLOW_STATE 0x925E90u
static inline float F32(uint32_t a){return MF32(a);}
static inline void put_float(uint32_t a,float f){MF32(a)=f;}
static X360PostParams params[2];
static X360PostView contexts[2];
static uint32_t update_vt[2],update_records[2][6];
static RainDrop lists[2][64];
static float readings[2]={.1f,.8f};
float x360_post_alpha=-1,x360_post_masked=-1;
void settings_add(Setting *s){s->value=s->def;}
void diag_event(const char *s,...){(void)s;}
uint64_t host_ticks(void){++diag_ticks;return frame*100000000ull;}
uint64_t diag_current_frame(void){return frame;}
void probe_applied(int p,int n){if(pc_testing && p==PROBE_RAINDROPS)pc_applied+=n;}
int x360_tone_curve(void){return 0;}
int lampglow_enabled(void){return 0;}
static unsigned history_resets;
void render_x360_post_reset(void){++history_resets;}
uint32_t split_capture_player(uint32_t v){(void)v;return bank+1;}
uint32_t galloc(uint32_t n){static uint32_t a=0x500000;uint32_t r=a;a=(a+n+15)&~15u;return r;}
float render_x360_post_view(uint32_t src,uint32_t dst,uint32_t vig,const X360PostParams *p,const X360PostView *v){
 if(fail_render)return -2;
 (void)src;(void)vig;unsigned b=v->source_view==0x9196C0;CHECK(b==bank);CHECK(dst==0x600000);
 CHECK(v->depth_surface==0x601000+b*0x1000);CHECK(v->destination[1]==(b?360:0));
 CHECK(v->destination[3]==(b?361:360));CHECK(!memcmp(v->source,v->destination,16));
 CHECK(p->znear==(b?.75f:.5f));CHECK(p->dof_params[0]==(b?30:10)*X360E_DOF_FOCAL);
 CHECK(p->blur[0]!=(b?params[0].blur[0]:123));CHECK(p->blur[0]*(b?-1:1)>0);
 static uint32_t sampled_generation[2],sample_count[2];
 if(sampled_generation[b]!=v->generation){sampled_generation[b]=v->generation;sample_count[b]=0;}
 if(sample_count[b]++==0)CHECK(fabsf(p->adapted_lum-X360E_ADAPT_START)<.0001f);
 else if(sample_count[b]>3)CHECK(fabsf(p->adapted_lum-readings[b])<.0001f);
 params[b]=*p;contexts[b]=*v;++renders;return readings[b];
}
float render_x360_post(uint32_t src,uint32_t dst,uint32_t vig,const X360PostParams *p){(void)src;(void)dst;(void)vig;params[0]=*p;return .25f;}
void render_x360_raindrops_warm(uint32_t a,uint32_t b,uint32_t c){(void)a;(void)b;(void)c;}
int render_x360_raindrops_view(uint32_t dst,const RainDrop *d,int n,uint32_t tx,uint32_t al,int sig,const X360PostView *v){
 (void)tx;(void)al;(void)sig;if(pc_testing){CHECK(v&&v->destination[1]==0&&v->destination[3]==(bank?361:360));CHECK(dst!=0x600000);CHECK(n==(bank?2:1));CHECK(d[0].x==(bank?.8f:.2f));++pc_lens;return n;}CHECK(v!=NULL);CHECK(dst==0x600000);CHECK(renders==rains+1);
 CHECK(n==(bank?2:1));CHECK(d[0].x==(bank?.8f:.2f));CHECK(d[0].variant==(bank?3:1));
 memcpy(lists[bank],d,n*sizeof *d);++rains;return n;
}
void f_006D53D0_orig(Cpu *c){if(pc_testing){++pc_composites;CHECK(M32(0x982A28)!=0x600000);CHECK(M32(0x982BE8)==(bank?361:360));}c->esp+=8;}
void f_006DC040_orig(Cpu*c){c->esp+=4;}
void f_006DBB20_orig(Cpu*c){CHECK(M32(0x919690)==M32((bank?0x9196C0:0x919650)+0x40));MF32(0x982C6C)+=bank+1;++pc_motion_calls;c->esp+=4;}
void f_006D4620_orig(Cpu *c){c->esp+=4;}
int render_x360_raindrops(uint32_t a,const RainDrop*b,int n,uint32_t d,uint32_t e,int f){(void)a;(void)b;(void)d;(void)e;(void)f;return n;}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args){
 (void)n;
 if(pc_testing && fn>=0xD0000000u){
  unsigned slot=(fn-0xD0000000u)/4;
  switch(slot){
   case 23:{uint32_t t=galloc(64);M32(t)=0x491000;M32(args[7])=t;CHECK(args[1]==1280&&args[2]==(bank?361:360));break;}
   case 18:{uint32_t t=galloc(64);M32(t)=0x491000;M32(args[2])=t;break;}
   case 34:++pc_copies;if(pc_copies%2==1){CHECK(M32(args[2]+4)==(bank?360:0));CHECK(M32(args[2]+12)==(bank?721:360));}else{CHECK(M32(args[4]+4)==(bank?360:0));CHECK(M32(args[4]+12)==(bank?721:360));}break;
   case 37:dev.rt[args[1]]=args[2];dev.viewport[0]=dev.viewport[1]=0;dev.viewport[2]=1280;dev.viewport[3]=721;break;
   case 39:dev.ds=args[1];break;
   case 47:memcpy(dev.viewport,G+args[1],24);break;
   case 75:memcpy(dev.scissor,G+args[1],16);break;
   case 57:dev.rs[args[1]]=args[2];break;
   case 2:break;
   default:CHECK(0);
  }return 0;
 }
 if(pc_testing && fn==0x6D4620){++pc_rains;CHECK(M32(0x9196B8)==M32((bank?0x9196C0:0x919650)+0x68));return 0;}
 if(pc_testing && (fn==0x6DBEC0||fn==0x6DBFE0)){CHECK(M32(0x982A28)!=0x600000);return 0;}
 if(fn==0x74C000){unsigned b=args[0]==0x9196C0;CHECK(b==bank);update_vt[b]=c->ecx;
  CHECK(c->ecx!=0x400000);CHECK(M32(0x982AF0)==c->ecx);
  for(unsigned j=0;j<6;++j){uint32_t r=M32(c->ecx+0x1C0+4*j);update_records[b][j]=r;
   CHECK(r!=0x420000+j*16);if(b)CHECK(r!=update_records[0][j]);MF32(r+8)+=b+1;}
  MF32(c->ecx+0x174)=b*.2f;MF32(c->ecx+0x178)=b*.3f;++updates;
 } else CHECK(fn==0x6E2F50);
 return 0;
}
'''
MAIN=r'''
int main(void){
 G=calloc(1,16*1024*1024);Cpu c={0};c.esp=0xF00000;c.eax=123;c.ecx=789;
 if(getenv("SPLITPOST_TEST_RAIN")) {
  M32(RAIN_PTR)=0x470000;M32(0x919728)=0x471000;
  Cpu saved=c;split_post_mode(1);CHECK(F32(RAIN_OVERRIDE)==1);CHECK(!memcmp(&c,&saved,sizeof c));
  put_float(RAIN_OVERRIDE,0);split_post_mode(1);CHECK(F32(RAIN_OVERRIDE)==1);
  put_float(RAIN_OVERRIDE,0);split_post_mode(0);CHECK(F32(RAIN_OVERRIDE)==0);
  c.esp-=8;M32(c.esp+4)=0;f_006D4620(&c);CHECK(F32(RAIN_OVERRIDE)==1);
  CHECK(M32(0x470000)==0&&M32(0x471000)==0);
  CHECK(F32(0x47028C)==0&&F32(0x47128C)==0);
  CHECK(F32(0x4736A0)==0&&F32(0x4746A0)==0);
  printf("test rain: %u checks / %u failures\n",checks,failures);free(G);return failures?1:0;
 }

 postfx=1;drops_have_tex=1;drops_tex[0]=1;drops_tex[1]=2;s_bloom.value=100;
 M32(VT_PLAT)=0x400000;M32(VT_TECHNIQUE)=0x430000;M32(GAMEFLOW_STATE)=6;
 M32(MOTION_BLUR_ON)=1;MF32(REAL_TIME_ELAPSED)=1.f/30;
 for(int j=0;j<14;j++)MF32(0x400000+LOOK_OFFS[j])=1;
 for(int j=0;j<6;j++){M32(0x400000+0x1C0+4*j)=0x420000+j*16;M32(0x420000+j*16)=0x440000;}
 for(int b=0;b<2;b++){
  uint32_t v=b?0x9196C0:0x919650,cam=0x450000+b*0x1000,plat=0x460000+b*0x1000,rain=0x470000+b*0x1000,desc=0x480000+b*0x100;
  M32(v)=plat;M32(v+4)=b+1;M32(v+0x40)=cam;M32(v+0x60)=desc;M32(v+0x68)=rain;
  M32(desc+8)=0x600000;M32(desc+12)=0x601000+b*0x1000;
  MF32(cam+0xBC)=b?.75f:.5f;MF32(cam+0xC0)=10000;MF32(cam+0xB4)=b?30:10;MF32(cam+0xB8)=2;
  MF32(cam+0x200)=b?100:30;MF32(plat)=b?-1:1;MF32(plat+20)=1;MF32(plat+40)=1;
  M32(rain)=b?2:1;
  for(int j=0;j<(b?2:1);j++){uint32_t r=rain+j*0x1C;MF32(r+4)=b?.8f:.2f;MF32(r+8)=.5f;MF32(r+0xC)=1;MF32(r+0x10)=2;MF32(r+0x14)=1;M32(r+0x1C)=b?3:1;}
 }
 uint8_t before[0x200];memcpy(before,G+0x400000,sizeof before);
 split_post_mode(1);
 for(frame=1;frame<=8;frame++){
  M32(0x9885B8)=frame;
  for(bank=0;bank<2;bank++){
   uint32_t r[4]={0,bank?360:0,1280,bank?361:360};Cpu old=c;
   split_post_view(&c,bank?0x9196C0:0x919650,r);
   CHECK(!memcmp(&c,&old,sizeof c));CHECK(M32(VT_PLAT)==0x400000);CHECK(!memcmp(before,G+0x400000,sizeof before));
   CHECK(post_state==&post_states[0]&&post_view==NULL);
  }
 }
 CHECK(renders==16&&rains==16&&updates==16);CHECK(lists[0][0].x!=lists[1][0].x);
 // Restart retains all identity operands. Both local histories must nevertheless reset;
 // the single-player state and the guest CPU remain exactly as before.
 PostState single=post_states[0];Cpu restart_cpu=c;
 uint32_t old_generations[2]={post_states[1].generation,post_states[2].generation};
 post_states[1].deferred=post_states[2].deferred=1;
 unsigned resets_before=history_resets;
 split_post_restart();CHECK(history_resets==resets_before+1);CHECK(!memcmp(&c,&restart_cpu,sizeof c));
 CHECK(!memcmp(&single,&post_states[0],sizeof single));
 CHECK(!post_states[1].mode_generation&&!post_states[2].mode_generation);
 CHECK(!post_states[1].deferred&&!post_states[2].deferred);
 for(bank=0;bank<2;++bank){uint32_t r[4]={0,bank?360:0,1280,bank?361:360};++frame;
  split_post_view(&c,bank?0x9196C0:0x919650,r);
  CHECK(post_states[bank+1].generation==old_generations[bank]+1);
  CHECK(post_states[bank+1].reset_value);
 }
 uint32_t gen=post_states[2].generation;M32(0x9196C0+0x40)=0x452000;
 memcpy(G+0x452000,G+0x451000,0x300);bank=1;uint32_t r[4]={0,360,1280,361};frame=1;
 split_post_view(&c,0x9196C0,r);CHECK(post_states[2].generation>gen);CHECK(post_states[2].reset_value);
 split_post_mode(0);split_post_mode(1);CHECK(post_states[1].mode_generation==0&&post_states[2].mode_generation==0);
 // Exercise the local PC/failure fallback, including non-default viewport depth range and scissor.
 pc_testing=1;postfx=0;drops_mode=0;M32(0x8F9218)=1;M32(D3D_DEVICE)=0x490000;M32(0x490000)=0x491000;
 for(unsigned i=0;i<128;++i)M32(0x491000+4*i)=0xD0000000u+4*i;
 dev.rt[0]=0x600000;dev.rt[1]=0x600010;dev.ds=0x601000;dev.rs[174]=1;
 uint32_t vp[6]={5,360,1234,355,0x3E800000,0x3F400000},sc[4]={7,365,1100,710};
 memcpy(dev.viewport,vp,24);memcpy(dev.scissor,sc,16);
 for(unsigned mode=0;mode<2;++mode){drops_mode=mode?2:0;
 for(bank=0;bank<2;++bank){uint32_t r[4]={0,bank?360:0,1280,bank?361:360};Cpu old=c;
  split_post_view(&c,bank?0x9196C0:0x919650,r);CHECK(!memcmp(&c,&old,sizeof c));
  CHECK(dev.rt[0]==0x600000&&dev.rt[1]==0x600010&&dev.ds==0x601000);
  CHECK(!memcmp(vp,dev.viewport,24)&&!memcmp(sc,dev.scissor,16)&&dev.rs[174]==1);
  CHECK(M32(VT_PLAT)==0x400000);
 }
 }
 CHECK(pc_composites==4&&pc_rains==2&&pc_copies==8&&pc_lens==2);CHECK(pc_applied==3);
 postfx=1;fail_render=1;bank=1;uint32_t fallback_rect[4]={0,360,1280,361};
 split_post_view(&c,0x9196C0,fallback_rect);CHECK(pc_composites==5&&pc_copies==10&&pc_lens==3);CHECK(pc_applied==5);
 CHECK(pc_motion_calls==5);CHECK(post_states[1].pc_motion[0]==0x40000000u&&post_states[2].pc_motion[0]==0x40C00000u);
 CHECK(!memcmp(vp,dev.viewport,24)&&!memcmp(sc,dev.scissor,16)&&dev.rs[174]==1);
 CHECK(diag_ticks==0);
 printf("hooks: %u checks / %u failures\n",checks,failures);free(G);return failures?1:0;
}
'''

with tempfile.TemporaryDirectory(prefix='splitpost-') as tmp:
    tmp=Path(tmp)
    # Compile the actual parameter builder and scope in a C fixture, replacing only the ObjC-only header.
    source=SRC.replace('#include "x360_internal.h"','')
    source=source.replace('#include "settings.h"','')
    constants=(ROOT/'runtime/gen/x360_engine.inc').read_text()
    fixture=PRE.replace('float x360_post_alpha',constants+'\nfloat x360_post_alpha')+source+MAIN
    def hook(label,text,forced_rain=False):
        f=tmp/(label+'.c');f.write_text(text);exe=tmp/label
        run(['clang','-x','c','-O1','-g','-fsanitize=address,undefined','-Wno-incompatible-pointer-types','-I',ROOT/'runtime','-I',ROOT/'recomp','-I',ROOT/'recomp/out',f,'-Wl,-dead_strip','-o',exe])
        env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'}
        if forced_rain:env.update(NFS_TEST_RAIN='1',SPLITPOST_TEST_RAIN='1')
        p=subprocess.run([str(exe)],text=True,capture_output=True,env=env)
        print(label+': '+(p.stdout+p.stderr)[-1800:].strip())
        return p.returncode
    assert hook('positive',fixture)==0
    assert hook('test-rain-positive',fixture,True)==0
    assert hook('test-rain-omitted',fixture.replace('if (active) test_rain_override();',''),True)!=0
    mutations={
        'PC-lens-unreported': ('if (drawn>0) probe_applied(PROBE_RAINDROPS,drawn);','(void)drawn;'),
        'restart-readback-stale': ('++split_post_generation;\n    render_x360_post_reset();', '++split_post_generation;\n    (void)0;'),
        'restart-history-stale': ('void split_post_restart(void) {\n    ++split_post_generation;', 'void split_post_restart(void) {\n    return;\n    ++split_post_generation;'),
        'shared-adaptation':('#define adapted (post_state->adapted_value)','#define adapted (post_states[1].adapted_value)'),
        'shared-transition-records':('M32(state->vt+0x1C0+4*i)=state->transitions[i];','M32(state->vt+0x1C0+4*i)=original;'),
        'P1-PC-motion-camera':('M32(CAMERA_PTR)=s->camera;','M32(CAMERA_PTR)=M32(0x00919690u);'),
        'diagnostics-always-on':('if (diag_on) {\n            uint64_t now=host_ticks();','if (1) {\n            uint64_t now=host_ticks();'),
        'shared-PC-motion':('M32(0x00982C6Cu+4*i)=s->pc_motion[i];','M32(0x00982C6Cu+4*i)=post_states[1].pc_motion[i];'),
        'P1-motion-basis':('uint32_t platform = view ? M32(view->source_view) : PLAYER_VIEW;','uint32_t platform = PLAYER_VIEW;'),
        'shared-state':('PostState *state=&post_states[source==0x009196C0u ? 2:1];','PostState *state=&post_states[1];'),
        'P1-camera':('M32(view ? view->source_view+0x40 : CAMERA_PTR)','M32(CAMERA_PTR)'),
        'P1-depth':('.depth_surface=M32(desc+12)', '.depth_surface=M32(M32(0x919650+0x60)+12)'),
        'shared-deferred':('post_state=state;post_view=&view;', 'state->drop_count=0;post_state=&post_states[1];post_view=&view;'),
        'shared-VT':('c->ecx=state->vt;call_guest(c,0x0074C000u,1,&source);','c->ecx=base;call_guest(c,0x0074C000u,1,&source);'),
        'short-dev-args':('a[10] = {d};','a[8] = {d};'),
        'bottom-Y-zero':('memcpy(view.destination,rect,16);','memcpy(view.destination,rect,16);view.destination[1]=0;'),
    }
    for label,(old,new) in mutations.items():
        assert old in fixture,label
        assert hook(label,fixture.replace(old,new,1))!=0,label+' survived'
    # Include the production encoder implementation with lightweight recording Metal objects.
    renderer=RENDER.replace('#include "render_internal.h"','')
    (tmp/'post_render.inc').write_text(renderer)
    msrc=(ROOT/'runtime/splitpost_test.m').read_text()
    rt=(ROOT/'runtime/render.m').read_text().split('RenderTex *surface_rt(',1)[1].split('// Render-target view',1)[0]
    msrc=msrc.replace('SURFACE_RT_PRODUCTION','RenderTex *surface_rt('+rt)
    (tmp/'splitpost_test.m').write_text(msrc)
    def encoder(label,text,nowait='0',negative=False):
        (tmp/'post_render.inc').write_text(text)
        exe=tmp/label
        run(['clang','-O1','-g','-w','-fobjc-arc','-I',tmp,'-I',ROOT/'runtime','-I',ROOT/'recomp','-I',ROOT/'recomp/out',tmp/'splitpost_test.m','-framework','Foundation','-framework','Metal','-framework','QuartzCore','-framework','ImageIO','-framework','CoreServices','-Wl,-dead_strip','-o',exe])
        env={**os.environ}
        if nowait is None:env.pop('NFS_LUM_NOWAIT',None)
        else:env['NFS_LUM_NOWAIT']=nowait
        p=subprocess.run([str(exe)],text=True,capture_output=True,env=env)
        output=(p.stdout+p.stderr).strip()
        if negative:
            output='negative control: '+next((line for line in output.splitlines() if line.startswith('encoder:')),output[-1800:])
        print(label+': '+output[-1800:])
        return p.returncode
    assert encoder('encoder-positive',renderer)==0
    assert encoder('encoder-nowait-positive',renderer,'1')==0
    assert encoder('encoder-nowait-unset',renderer,None)==0
    for label,old,new in [
        ('nowait-still-waits','!view && (!x_lum_nowait() || !xr->lum_ready)','!view'),
        ('nowait-no-startup-wait','!view && (!x_lum_nowait() || !xr->lum_ready)','!view && !x_lum_nowait()'),
        ('nowait-black-ready','if (measured >= 0.002f) xr->lum_ready=1;','xr->lum_ready=1;'),
        ('nowait-stale-ready','xr->lum_ready=0;','(void)0;'),
        ('nowait-oldest','slot->serial > newest->serial','slot->serial < newest->serial'),
        ('nowait-pending-read','slot->serial <= completed','1'),
        ('nowait-reuse-pending','if (!slot->pending && !writable) writable=slot;','if (!writable) writable=slot;'),
    ]:
        assert old in renderer,label
        assert encoder(label,renderer.replace(old,new,1),'1',negative=True)!=0,label+' survived'
    for label,old,new in [
        ('missing-SP-wait','if (!view && (!x_lum_nowait() || !xr->lum_ready)) {\n        for (unsigned i=0;i<X_LUM_SLOTS;++i)', 'if (0) {\n        for (unsigned i=0;i<X_LUM_SLOTS;++i)'),
        ('PC-lens-no-parent','if (!r && view) {', 'if (0) {'),
        ('P1-lamp-source','render_lamp_source_view(view ? view->source_view : 0)', 'render_lamp_source_view(0x00919650u)'),
        ('current-serial-wait','uint64_t completed = __atomic_load_n(&done_serial, __ATOMIC_ACQUIRE);','wait_serial(frame_serial); uint64_t completed = __atomic_load_n(&done_serial, __ATOMIC_ACQUIRE);'),
        ('whole-source','view ? x_crop(sg->tex,view->source,&xr->scene[frame_serial&1]) : sg->tex','sg->tex'),
        ('missing-bounds','vk.depth_bounds[j]=(view->depth[j]+0.5f)/size;','vk.depth_bounds[j]=0;'),
        ('P1-depth-rect','vk.depth_uv[j]=view->depth[j]/size;', 'vk.depth_uv[j]=0;'),
        ('shader-bounds','withString:@"float d = depth.sample(lin, clamp(k.depth_uv.xy + uv * k.depth_uv.zw, k.depth_bounds.xy, k.depth_bounds.zw));"','withString:@"float d = depth.sample(lin, k.depth_uv.xy + uv * k.depth_uv.zw);"'),
        ('shared-ring','xr = &x_resources[view ? (view->source_view == 0x009196C0u ? 2 : 1) : 0];','xr = &x_resources[1];'),
        ('unsafe-slot-reuse','if (!slot->pending && !writable) writable=slot;','if (!writable) writable=slot;'),
        ('full-aspect','view ? (float)view->destination[2]/view->destination[3] : (float)out.width/(float)out.height','(float)out.width/(float)out.height'),
        ('whole-lens-copy','view ? view->destination:full','full'),
    ]:
        assert old in renderer,label
        assert encoder(label,renderer.replace(old,new,1),negative=True)!=0,label+' survived'
    # Pin Off command/constants/readback bytes from the pre-split 4175000^
    # renderer (also matched 6ba9278a), with an incomplete prior frame per call.
    # A digest survives landing and standalone trees without any main ref.
    off_digest='e003e52470f0cbc91d2f1f2dcdd385d44cf5fdba2c66da07add9e2f31e22ca56'
    snapshot_main=r'''int main(int argc,char **argv){@autoreleasepool{
     single_testing=1;mtl=(id)[TDevice new];cmd=(id)[TCommand new];
     if(atoi(argv[1]))setenv("NFS_PADS","1",1);else unsetenv("NFS_PADS");
     for(unsigned i=0;i<16;++i){objects[i].gpu=&textures[i];textures[i].tex=texture(1280,720,MTLPixelFormatBGRA8Unorm);}
     autodepth_tex=textures[3].tex;ad_rt.tex=autodepth_tex;
     X360PostParams p={.middle_gray=.14f,.adapted_lum=.2f,.znear=.5f,.zfar=10000,.dof=1};
     p.coeffs[0][0]=1;p.desaturation=p.exposure=1;
     post_snapshot=fopen(argv[2],"wb");
     for(frame_serial=1;frame_serial<=6;++frame_serial){
      done_serial=0;((TTexture*)(id)textures[1].tex).tag=25+frame_serial;
      float values[]={render_x360_post(1,2,0,&p),x360_post_alpha,x360_post_masked};
      fwrite(values,1,sizeof values,post_snapshot);fwrite(&waits,1,sizeof waits,post_snapshot);
     }
     fclose(post_snapshot);return failures?1:0;
    }}'''
    snap_src=msrc.split('int main(void)',1)[0]+snapshot_main
    (tmp/'post_snapshot.m').write_text(snap_src)
    snapshot_bytes=0
    broken=renderer.replace('if (!view && (!x_lum_nowait() || !xr->lum_ready)) {','if (0) {',1)
    assert broken!=renderer
    for name,body in [('current',renderer),('missing-Off-wait',broken)]:
        (tmp/'post_render.inc').write_text(body)
        exe=tmp/('post-snapshot-'+name)
        run(['clang','-O1','-g','-w','-fobjc-arc','-I',tmp,'-I',ROOT/'runtime','-I',ROOT/'recomp','-I',ROOT/'recomp/out',tmp/'post_snapshot.m','-framework','Foundation','-framework','Metal','-framework','QuartzCore','-framework','ImageIO','-framework','CoreServices','-Wl,-dead_strip','-o',exe])
        for mode in range(2):
            for switch in ('','0'):
                path=tmp/(name+str(mode)+switch+'.post-snapshot')
                p=subprocess.run([str(exe),str(mode),str(path)],text=True,capture_output=True,env={**os.environ,'NFS_LUM_NOWAIT':switch})
                assert p.returncode==0,(p.stdout+p.stderr)[-1800:]
                if switch=='0':assert path.read_bytes()==(tmp/(name+str(mode)+'.post-snapshot')).read_bytes(),'explicit Off changed output/waits'
                matches=hashlib.sha256(path.read_bytes()).hexdigest()==off_digest
                assert matches==(name=='current'),f'{name}: pinned Off command/readback/wait snapshot {mode} differs'
    for mode in range(2):
        after=(tmp/('current'+str(mode)+'.post-snapshot')).read_bytes()
        snapshot_bytes+=len(after)
    print(f'single-player renderer: 2 pinned Off snapshots identical ({snapshot_bytes} command/constants/readback bytes; 6 frames each); omitted-wait control rejected in all 4 modes')
    # Compile original and view-specialized MSL offline; no Metal device is requested.
    macros=dict(re.findall(r'#define (\w+) "(.*)"',(ROOT/'runtime/gen/x360_shader_lits.inc').read_text()))
    macros.update(dict(re.findall(r'#define (\w+) "(.*)"',(ROOT/'runtime/gen/x360_engine.inc').read_text())))
    def shader(src,name):
        body=src.split('static const char *'+name+' =',1)[1].split(';\n',1)[0]
        return ''.join(ast.literal_eval(x[0]) if x[0].startswith('"') else macros[x[0]] for x in re.finditer(r'"(?:[^"\\]|\\.)*"|\bX360\w+\b',body))
    base=shader(RENDER,'x360_src')
    old=shader(run(['git','show','HEAD:runtime/render_post.m']),'x360_src')
    assert base==old,'single-player shader changed'
    variants={'base':base,'view':base.replace('float4 tp; float4 rx; };','float4 tp; float4 rx; float4 depth_uv; float4 depth_bounds; };').replace('float d = depth.sample(lin, uv);','float d = depth.sample(lin, clamp(k.depth_uv.xy + uv * k.depth_uv.zw, k.depth_bounds.xy, k.depth_bounds.zw));'),'drops':shader(RENDER,'drops_src')}
    extra=shader((ROOT/'runtime/lampglow_shader.h').read_text(),'lampglow_src')
    for name in ('base','view'):
        variants[name+'-lamp']=variants[name].replace('fragment float4 fs_vt(','fragment float4 fs_vt_lamp(').replace('texture2d<float> bself [[texture(6)]]','texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]').replace('    if (patch) bl = 0;','    bl += lamp.sample(lin, uv).rgb;\n    if (patch) bl = 0;')+extra
    metal=subprocess.check_output(['xcrun','--find','metal'],env={**os.environ,'TMPDIR':os.environ.get('OT_TOOL_TMPDIR',os.environ.get('TMPDIR','/tmp/'))},text=True).strip()
    for name,src in variants.items():
        f=tmp/(name+'.metal');f.write_text(src)
        run([metal,'-fmodules-cache-path='+str(tmp/'modules'),'-c',f,'-o',tmp/(name+'.air')])
    print(f'MSL: {len(variants)} compiled; single-player shader identical ({len(base)} bytes)')

    # Exact single-player memory, CPU and submitted parameter snapshots against HEAD.
    single_main=r'\nint main(int argc,char **argv){G=calloc(1,16*1024*1024);Cpu c={0};c.esp=0xF00000;c.eax=123;c.ecx=456;frame=1;bank=0;postfx=1;M32(VT_PLAT)=0x400000;M32(VT_TECHNIQUE)=0x430000;M32(SCREEN_TEXTURES)=11;M32(BACKBUFFER_SURF)=12;M32(CAMERA_PTR)=0x450000;M32(GAMEFLOW_STATE)=6;M32(MOTION_BLUR_ON)=1;MF32(REAL_TIME_ELAPSED)=1.f/30;for(int j=0;j<14;j++)MF32(0x400000+LOOK_OFFS[j])=1;MF32(0x450000+0xBC)=.5f;MF32(0x450000+0xC0)=10000;MF32(0x450000+0xB4)=20;MF32(0x450000+0xB8)=2;MF32(0x450000+0x200)=50;MF32(PLAYER_VIEW)=1;M32(c.esp+4)=1;int mode=atoi(argv[1]);if(mode==1)postfx=0;if(mode==2)M32(VT_TECHNIQUE)=0;f_006D53D0(&c);FILE*f=fopen(argv[2],"wb");fwrite(G,1,16*1024*1024,f);fwrite(&c,1,sizeof c,f);fwrite(params,1,sizeof params,f);fclose(f);free(G);return 0;}'
    single_main=single_main.replace(r'\n','\n',1)
    baseline=run(['git','show','HEAD:runtime/x360_post.m']).replace('#include "x360_internal.h"','').replace('#include "settings.h"','')
    prefix=PRE.replace('float x360_post_alpha',constants+'\nfloat x360_post_alpha')
    for name,source in [('baseline',baseline),('current',SRC.replace('#include "x360_internal.h"','').replace('#include "settings.h"',''))]:
        f=tmp/(name+'-single.c');f.write_text(prefix+source+single_main)
        run(['clang','-x','c','-O1','-w','-I',ROOT/'runtime','-I',ROOT/'recomp','-I',ROOT/'recomp/out',f,'-Wl,-dead_strip','-o',tmp/(name+'-single')])
        for mode in range(3):run([tmp/(name+'-single'),mode,tmp/(name+str(mode)+'.snapshot')])
    for mode in range(3):assert (tmp/('baseline'+str(mode)+'.snapshot')).read_bytes()==(tmp/('current'+str(mode)+'.snapshot')).read_bytes(),f'single {mode} changed'
    print('single-player: 3 exact 16 MiB memory + CPU + parameter snapshots identical (50,331,648 guest bytes)')
