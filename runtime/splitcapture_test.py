#!/usr/bin/env python3
"""
















"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(src, name):
    return re.search(r'(?:static )?(?:void|int|float|unsigned|uint32_t) '+name+r'\([^\n]*\).*?\n\}', src, re.S)[0]


def translated(file, name):
    s = (ROOT/'recomp/out'/file).read_text()
    return re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', s, re.S)[0]


PRE = r'''
#include "host.h"
#include "funcs.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on;
uint32_t galloc(uint32_t n){static uint32_t a=0x710000;uint32_t r=a;a=(a+n+15)&~15u;return r;}
// Post and effects/query lifecycle are covered by splitpost_test.py and splitfx_test.py.
void split_post_mode(int active){(void)active;}
static unsigned restart_posts;
void split_post_restart(void){++restart_posts;}
void split_post_view(Cpu*c,uint32_t v,const uint32_t r[4]){(void)c;(void)v;(void)r;}
#define MEMSIZE (16*1024*1024)
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; if(failures<12) printf("FAIL %d frame=%d source=%x %s\n",__LINE__,frame_no,split_capture_source(),#x); } } while(0)
static int checks,failures,frame_no,alloc_fail,fail_at,allocations,orig_calls,fallback_fixture;
static unsigned diag_lines,diag_ticks_calls,diag_draw_calls;
static uint64_t diag_clock=1;
static uint32_t rects[2][4]={{0,0,1280,360},{0,360,1280,361}},cur_view;
static int wide=1,split_on=1,refl_x360=1;
static uint32_t refl_w=640,refl_h=360;
#define WINDOW_WIDTH 0x982BE4u
#define WINDOW_HEIGHT 0x982BE8u
#define VIEW_REFLECTION 0x9197A0u
#define RA_PROJ_MULTIPLY 0x6CF62Fu
#define GAMEFLOW_STATE 0x925E90u
#define X360_SHADER_LITS 1
#define CUR_VIEW 0x982A20u
#define DEVFN 0xD0000000u
#define RESFN 0xD1000000u
#define MOVEFN 0xD2000000u
#define SHFN 0xD3000000u
#define FXFN 0xD4000000u
typedef struct { uint32_t w,h; } Obj;
typedef struct { Obj o; uint32_t guest,kind,parent,face,owner,refs,tag[6]; int alive; } Resource;
static Resource resources[4096];
static unsigned nresources;
static uint32_t rt,ds,vp[6],sc[4],rs[256],draws,worlds,cull_owner,cull_stamp,matrices_stamp;
static unsigned cube_faces[2],road_geometry[2],road_skies[2],blur[2],main_receivers[2],clears,presents,prep;
static float expected_face[2][6][16];
static float expected_orientation[2][16];
static uint32_t receiver_cube[2],receiver_road[2],expected_tag[2][6];
static uint32_t calls[4096],ncalls;
static uint32_t weather_strength;
static uint32_t new_resource(uint32_t kind,uint32_t w,uint32_t h,uint32_t parent,uint32_t face);
static Resource *res(uint32_t handle) {
 for(unsigned i=0;i<nresources;i++) if(resources[i].guest==handle) return &resources[i];
 fprintf(stderr,"unknown resource %x\n",handle); exit(2);
}
static Obj *obj(uint32_t handle) { return handle ? &res(handle)->o : NULL; }
static uint32_t new_resource(uint32_t kind,uint32_t w,uint32_t h,uint32_t parent,uint32_t face) {
 CHECK(nresources<4096); Resource *r=&resources[nresources]; memset(r,0,sizeof *r);
 r->guest=0x600000+0x40*nresources++; r->kind=kind; r->o.w=w; r->o.h=h; r->parent=parent;r->face=face;
 r->owner=split_capture_source()==0x9196C0 ? 2:split_capture_source()==0x919650 ? 1:0;
 r->refs=1;r->alive=1;M32(r->guest)=0x581000; return r->guest;
}
int split_view_rect(uint32_t view,uint32_t r[4]) {
 if(!split_on||(view!=0x919650&&view!=0x9196C0)) return 0;
 if(r)memcpy(r,rects[view==0x9196C0],16);return 1;
}
void diag_event(const char *s,...) { if(!strncmp(s,"splitcapture:",13))++diag_lines; }
uint64_t host_ticks(void) { ++diag_ticks_calls;return diag_clock; }
void graphics_reset_request(void) {}
uint64_t d3d9_draw_count(void) { ++diag_draw_calls;return draws; }
void mirror_projection(uint32_t view,uint32_t proj) { (void)view;(void)proj; }
static void refl_options(void) {}
static void test_sun(void) {}
static void test_shadow_switch(void) {}
void f_006BD620_orig(Cpu *c) { c->esp+=4; }
void f_006308E0_orig(Cpu *c) { c->eax=0x6308E0;c->ecx=0x123456;c->esp+=4; }
void f_006DE300_orig(Cpu *c) { ++orig_calls;c->esp+=4; }
void bad_jump(Cpu *c,uint32_t t) { (void)c;fprintf(stderr,"bad jump %x\n",t);exit(2); }
void abi_violation(Cpu *c,uint32_t f,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp;fprintf(stderr,"ABI %x\n",f);exit(2);
}
void guest_divide_error(Cpu *c,uint32_t pc) { (void)c;(void)pc;abort(); }
static void run_guest(Cpu *c,void (*fn)(Cpu *),int n,const uint32_t *a) {
 uint32_t sp=c->esp; c->esp-=4*(n+1);M32(c->esp)=0xFEED;
 for(int i=0;i<n;i++)M32(c->esp+4+4*i)=a[i];
 fn(c);c->esp=sp;
}
static void identity(uint32_t a) { memset(G+a,0,64);for(unsigned i=0;i<4;i++)MF32(a+i*20)=1; }
static void mul(uint32_t dst,uint32_t a,uint32_t b) {
 float out[16]={0};for(unsigned r=0;r<4;r++)for(unsigned col=0;col<4;col++)for(unsigned k=0;k<4;k++)
 { out[r*4+col]+=MF32(a+4*(r*4+k))*MF32(b+4*(k*4+col)); }
 memcpy(G+dst,out,64);
}
void widescreen_projection(Cpu *,uint32_t);
void f_007E7D5E(Cpu *c) { uint32_t d=arg(c,0),a=arg(c,1),b=arg(c,2);
 widescreen_projection(c,b);mul(d,a,b);c->eax=d;c->esp+=16;
}
void f_007E7D58(Cpu *c) {
 float m[16];uint32_t d=arg(c,0),a=arg(c,1);
 for(unsigned i=0;i<4;i++)for(unsigned j=0;j<4;j++)m[4*i+j]=MF32(a+4*(4*j+i));
 memcpy(G+d,m,64);c->eax=d;c->esp+=12;
}
void f_007C4B80(Cpu *c) { c->eax=(uint32_t)(int32_t)trunc(ST(0));x87_pop(c);c->esp+=4; }
void f_0045DB30(Cpu *c) { x87_push(c,sin((uint16_t)arg(c,0)*6.283185307179586/65536));c->esp+=4; }
void f_0045DBF0(Cpu *c) { x87_push(c,cos((uint16_t)arg(c,0)*6.283185307179586/65536));c->esp+=4; }
void f_004700D0(Cpu *c) { // matrix storage/history sink, same Camera layout consumed by retail matrix/frustum code
 uint32_t cam=c->ecx,m=arg(c,0);memcpy(G+cam+0xE0,G+cam,0xE0);memcpy(G+cam,G+m,64);
 for(unsigned i=0;i<3;i++)MF32(cam+0x40+4*i)=-(MF32(m+48)*MF32(m+16*i)+MF32(m+52)*MF32(m+16*i+4)+MF32(m+56)*MF32(m+16*i+8));
 c->esp+=12;
}
void f_004FAE40(Cpu *c) { // cull-entry construction records the actual view's current matrices
 uint32_t v=arg(c,0),entry=arg(c,1);M32(entry+0x80)=v;
 if(v==0x9197A0)CHECK(!memcmp(G+M32(v)+0x40,G+M32(split_capture_source())+0x40,64));
 c->esp+=4;
}
void f_00731B80(Cpu *c) {
 cull_owner=split_capture_source(); cull_stamp=matrices_stamp;
 uint32_t n=M32(c->ecx+0x9C0);int road=0;
 for(uint32_t i=0;i<n;i++) road|=M32(c->ecx+i*0xD0+0x80)==0x9197A0;
 if(cull_owner) {
   float wet=MF32(M32(cull_owner+0x68)+0x36A0);
   CHECK(road==(x360_water_reflection_level()!=0&&x360_water_reflection_wetness(0x6BFEE6,wet)>.01f));
 }
 c->esp+=4;
}
static void fake_dispatch(Cpu *c,uint32_t fn,int n,const uint32_t *a);
void dispatch(Cpu *c,uint32_t fn) {
 if(fn==MOVEFN) { c->eax=fallback_fixture?0:c->ecx+0x100;c->esp+=4;return; }
 if(fn==MOVEFN+4||fn==MOVEFN+8||fn==MOVEFN+12) { c->eax=c->ecx+0x100;c->esp+=4;return; }
 fprintf(stderr,"unexpected translated dispatch %x\n",fn);(void)c;(void)nresources;exit(2);
}
static void stamp(uint32_t view,int geometry) {
 unsigned p=split_capture_source()==0x9196C0;
 CHECK(cull_owner==split_capture_source()&&cull_stamp==matrices_stamp);
 CHECK(!rs[174]);Resource *surface=res(rt);CHECK(surface->alive&&surface->owner==p+1);
 CHECK(weather_strength==M32(M32(split_capture_source()+0x68)+0x290));
 if(view!=0x9197A0)CHECK(!memcmp(G+0x987FC0,expected_orientation[p],64));
 Resource *texture=surface->parent ? res(surface->parent):surface;
 CHECK(texture->owner==p+1);
 if(view==0x9197A0) {
   CHECK(M32(view+0x40)==M32(split_capture_source()+0x40));
   CHECK(!memcmp(G+M32(view)+0x40,G+M32(split_capture_source())+0x40,64));
   if(geometry)++road_geometry[p];else ++road_skies[p];
 } else {
   unsigned i=(view-0x919DC0)/0x70;CHECK(i<6);
   CHECK(!memcmp(G+M32(view+0x40),expected_face[p][i],64));
   CHECK(!memcmp(G+M32(view),G+M32(view+0x40),64)); // capture culling/frustum consumed this frame's face matrix
   texture->tag[surface->face]=(p+1)*100000+frame_no*100+i+1;
   expected_tag[p][surface->face]=texture->tag[surface->face];
   if(!geometry)++cube_faces[p];
 }
 ++draws;
}
static void receiver(uint32_t view) {
 unsigned p=view==0x9196C0;uint32_t d=M32(view+0x60);
 CHECK(M32(view+4)==p+1&&M32(d+4)==p+1&&M32(d)==0);
 CHECK(rt==M32(d+8)&&ds==M32(d+12));
 CHECK(!memcmp(vp,rects[p],16)&&rs[174]);
 Resource *road=res(M32(0x982A6C)),*cube=res(M32(0x982A30));
 CHECK(road->owner==p+1&&cube->owner==p+1);
 CHECK(x360_reflection_target(road->guest,road->o.w,road->o.h));
 CHECK(!x360_reflection_target(cube->guest,road->o.w,road->o.h));
 CHECK(!x360_reflection_target(road->guest,road->o.w,2*road->o.h));
 CHECK(!memcmp(G+0x987FC0,expected_orientation[p],64));
 CHECK(road_skies[p]>0&&cube_faces[p]>0);
 for(unsigned f=0;f<6;f++)CHECK(cube->tag[f]==expected_tag[p][f]&&cube->tag[f]/100000==p+1);
 CHECK(road->o.w==640&&road->o.h==rects[p][3]/2);
 receiver_cube[p]=cube->guest;receiver_road[p]=road->guest;++main_receivers[p];++worlds;
}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 if(ncalls<4096)calls[ncalls++]=fn;
 c->eax=0;
 if(fn>=DEVFN&&fn<DEVFN+0x200) { fake_dispatch(c,fn,n,a);return c->eax; }
 if(fn>=RESFN&&fn<RESFN+0x100) { fake_dispatch(c,fn,n,a);return c->eax; }
 if(fn>=SHFN&&fn<FXFN+0x200)return 0;
 switch(fn) {
 case 0x6CF400: run_guest(c,f_006CF400,n,a);++matrices_stamp;break;
 case 0x756060: run_guest(c,f_00756060,n,a);break;
 case 0x6BFDD0: run_guest(c,f_006BFDD0,n,a);break;
 case 0x6BD3B0: {
   uint32_t d=a[0];rt=M32(d+8);ds=M32(d+12);vp[0]=vp[1]=0;vp[2]=M32(d+20);vp[3]=M32(d+24);vp[4]=0;vp[5]=0x3F800000;
   M32(0x982A20)=d;CHECK(a[1]==1);break;
 }
 case 0x6DB310:
   CHECK(a[1]==(a[0]==0x9197A0?4u:3u));
   CHECK(a[2]==(a[0]==0x9197A0?M32(0x8F92E0):M8(0x520040)?M32(0x300064):0x3F000000u));
   stamp(a[0],0);break;
 case 0x723FA0:
   if(a[0]==0x919650||a[0]==0x9196C0)receiver(a[0]);else stamp(a[0],1);break;
 case 0x6D4960: {
   unsigned p=split_capture_source()==0x9196C0;++blur[p];
   Resource *t=res(M32(0x982A74));CHECK(t->owner==p+1&&t->o.w==640&&t->o.h==rects[p][3]/2);
   // Blur may bind an intermediate; the production scope must restore the actual incoming state.
   rt=M32(0x982A70);vp[0]=vp[1]=0;vp[2]=t->o.w;vp[3]=t->o.h;rs[174]=1;break;
 }
 case 0x504C50: ++prep;break;
 case 0x6E54E0: CHECK(main_receivers[a[0]==0x9196C0]==0);rt=0xBAD;ds=0xBAD;vp[0]=vp[1]=0;vp[2]=vp[3]=2048;break;
 case 0x6DE210:case 0x7538D0:case 0x7268C0:
   receiver(a[0]);break;
 case 0x750B10:
   if(a[0]==0x919650||a[0]==0x9196C0)receiver(a[0]);else stamp(a[0],1);break;
 case 0x769580:weather_strength=a[0];break;
 case 0x6E2F50:case 0x753D90:case 0x6C83D0:case 0x724230:case 0x6CFCE0:
 case 0x6C00D0:case 0x6C0130:case 0x6D04E0:case 0x41AD20:
 case 0x6D6CF0:case 0x769360:case 0x6C00B0:case 0x73CFE0:break;
 default:fprintf(stderr,"unexpected guest call %x\n",fn);exit(2);
 }
 return c->eax;
}
static void fake_dispatch(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 (void)n;uint32_t off=fn&0xFFF;
 if(fn>=RESFN) {
   Resource *r=res(a[0]);CHECK(r->alive);
   if(off==8) { CHECK(r->refs>0);if(!--r->refs)r->alive=0; }
   else if(off==0x48) {
     ++allocations;if(alloc_fail||allocations==fail_at){c->eax=0x80004005;return;}
     uint32_t out=r->kind==2?a[3]:a[2],f=r->kind==2?a[1]:0;
     M32(out)=new_resource(3,r->o.w,r->o.h,r->guest,f);
   }else { fprintf(stderr,"resource operation %x\n",off);exit(2); }
   return;
 }
 switch(off) {
 case 0x5C:case 0x64:case 0x74: {
   ++allocations;if(alloc_fail||allocations==fail_at){c->eax=0x80004005;return;}
   uint32_t out=off==0x64?a[6]:a[7];
   M32(out)=new_resource(off==0x64?2:off==0x5C?1:4,a[1],off==0x64?a[1]:a[2],0,0);break;
 }
 case 0x98:M32(a[2])=rt;++res(rt)->refs;break;
 case 0xA0:M32(a[1])=ds;++res(ds)->refs;break;
 case 0x94:rt=a[2];vp[0]=vp[1]=0;vp[2]=1280;vp[3]=721;break;
 case 0x9C:ds=a[1];break;
 case 0xBC:memcpy(vp,G+a[1],24);break;
 case 0xC0:memcpy(G+a[1],vp,24);break;
 case 0x12C:memcpy(sc,G+a[1],16);break;
 case 0x130:memcpy(G+a[1],sc,16);break;
 case 0xE4:rs[a[1]]=a[2];break;
 case 0xE8:M32(a[2])=rs[a[1]];break;
 case 0xAC:++clears;break;
 case 0x44:++presents;break;
 case 0x104:case 0x15C:break;
 default:fprintf(stderr,"device operation %x\n",off);exit(2);
 }
}
'''

POST = r'''
static Cpu cpu(void) { return (Cpu){.esp=0x800000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37F}; }
static void initialize(void) {
 G=calloc(1,MEMSIZE);CHECK(G!=NULL);
 M32(0x982BDC)=0x500000;M32(0x500000)=0x501000;
 for(unsigned o=0;o<0x200;o+=4)M32(0x501000+o)=DEVFN+o;
 for(unsigned o=0;o<0x100;o+=4)M32(0x581000+o)=RESFN+o;
 M32(0x510000)=0x511000;for(unsigned o=0;o<16;o+=4)M32(0x511000+o)=SHFN+o;
 M32(0x510048)=0x512000;M32(0x51004C)=0x5100F0;M32(0x512000)=0x513000;
 for(unsigned o=0;o<0x200;o+=4)M32(0x513000+o)=FXFN+o;
 M32(0x93DE78)=M32(0x93DEBC)=0x510000;M32(0x93DEC4)=0x520000;
 M32(0x520048)=0x512000;M32(0x520044)=0x530000;M32(0x53018C)=0x77;
 uint32_t target=new_resource(3,1280,721,0,0),depth=new_resource(4,1280,721,0,0);
 rt=target;ds=depth;vp[0]=17;vp[1]=19;vp[2]=891;vp[3]=413;vp[4]=0x3E800000;vp[5]=0x3F400000;
 sc[0]=11;sc[1]=13;sc[2]=817;sc[3]=419;rs[174]=1;rs[7]=2;rs[14]=0;
 M32(WINDOW_WIDTH)=1280;M32(WINDOW_HEIGHT)=721;M32(GAMEFLOW_STATE)=6;M32(0x8F9330)=1;
 MF32(0x8AF994)=1.15f;MF32(0x8933D4)=.5f;MF32(0x89096C)=1;MF32(0x890968)=0;
 MF32(0x8F9010)=1;MF32(0x8F9014)=500;M16(0x987DB8)=16384;
 M32(0x9017D4)=M32(0x90185C)=3;M32(0x9017B8)=1;M32(0x9017BC)=1;
 M32(0x8F8FF4)=64;M32(0x8F8FF8)=21;M32(0x8F8FFC)=75;M32(0x8F9000)=21;M32(0x8F9004)=75;
 // Retail level-1 schedule: alternate one face. Entry/reset must override it with all six.
 M32(0x8F9018+4)=2;
 for(unsigned s=0;s<2;s++)for(unsigned f=0;f<6;f++)M32(0x8F9028+(s*6+f)*4)=f==s;
 for(unsigned p=0;p<2;p++) {
  uint32_t v=0x919650+p*0x70,d=0x400000+p*0x1C,cam=0x550000+p*0x1000,plat=0x310000+p*0x1000,mov=0x570000+p*0x1000;
  M32(v)=plat;M32(v+4)=p+1;M8(v+8)=1;M32(v+0x40)=cam;M32(v+0x44)=mov+4;M32(mov)=0x590000;
  M32(0x590010)=MOVEFN;M32(v+0x60)=d;M32(v+0x68)=0x100000+p*0x10000;
  M32(d)=0;M32(d+4)=p+1;M32(d+8)=target;M32(d+12)=depth;M32(d+16)=1;M32(d+20)=1280;M32(d+24)=rects[p][3];
  identity(cam);MF32(cam+0xBC)=1;MF32(cam+0xC0)=500;M16(cam+0xC4)=p?21846:16384;
 }
 for(unsigned i=0;i<7;i++) {
  uint32_t v=i?0x919DC0+(i-1)*0x70:0x9197A0,plat=0x320000+i*0x1000,cam=i?0x350000+i*0x1000:0x550000,d=0x410000+i*0x1C;
  M32(v)=plat;M32(v+4)=i?17+i:4;M8(v+8)=1;M32(v+0x40)=cam;M32(v+0x60)=d;M32(v+0x68)=0x100000;
  static const unsigned face_for_view[6]={4,0,5,1,2,3},view_for_face[6]={1,3,4,5,0,2};
  M32(d)=i?5+face_for_view[i-1]:1;M32(d+4)=M32(v+4);M32(d+16)=1;M32(d+20)=M32(d+24)=64;
  if(i) { identity(cam);M32(0x982D60+(i-1)*4)=0x919DC0+view_for_face[i-1]*0x70; }
  else { M8(v+8)=0;M32(d+16)=0; } // retail split/FE transition leaves road view and descriptor inactive
 }
 // Standalone retail handles and descriptor state must survive each scope verbatim.
 for(unsigned i=0;i<5;i++)M32(0x982A68+4*i)=new_resource(i==1||i==3?1:4,640,360,0,0);
 for(unsigned i=0;i<13;i++)M32(0x982A30+4*i)=new_resource(i?4:2,64,64,0,0);
 M32(0x982A20)=0x400000;M32(0x982C80)=0;
 M32(0x9B392C)=0x300000;M32(0x901830)=1;MF32(0x8FAE68)=1;MF32(0x8FAE50)=.01f;
 MF32(0x8F92E0)=.3f;MF32(0x300064)=.75f;
 M32(0x92D87C)=0x5A0000;M32(0x92D884)=2;
 for(unsigned p=0;p<2;p++) {
  uint32_t pl=0x5B0000+p*0x1000;M32(0x5A0000+p*4)=pl;
  M32(pl+0x1C)=p;
  M32(pl)=0x592000;M32(0x592004)=MOVEFN+4;
  M32(pl+0x100)=0x593000;M32(0x593054)=MOVEFN+8;
  M32(pl+0x200)=0x594000;M32(0x594020)=MOVEFN+12;
  MF32(pl+0x300)=p?-177:21;MF32(pl+0x304)=p?6:2;MF32(pl+0x308)=p?131:17;
 }
}
static void update_players(void) {
 for(unsigned p=0;p<2;p++) {
  uint32_t v=0x919650+p*0x70,cam=M32(v+0x40),car=M32(v+0x44)-4+0x100;
  identity(cam);float angle=(p?1.1f:.2f)+frame_no*.07f;
  MF32(cam)=cosf(angle);MF32(cam+8)=sinf(angle);MF32(cam+32)=-sinf(angle);MF32(cam+40)=cosf(angle);
  MF32(cam+0x40)=p?100+frame_no*9:frame_no*3;MF32(cam+0x44)=p?7:2;MF32(cam+0x48)=p?-90:13;
  MF32(car+0x20)=MF32(cam+0x40)+4;MF32(car+0x24)=MF32(cam+0x44)+1;MF32(car+0x28)=MF32(cam+0x48)+6;
  MF32(M32(v+0x68)+0x36A0)=p?.8f:0;MF32(M32(v+0x68)+0x290)=p?.9f:.1f;
  uint32_t a[5]={v,0,0x466A6000,0,1};Cpu c=cpu();c.ecx=M32(v);run_guest(&c,f_006CF400,5,a);
 }
}
static void reference(void) {
 CaptureView snapshots[7];cap_views(snapshots,0);
 uint8_t orient[64];memcpy(orient,G+0x987FC0,64);
 uint32_t camera=M32(0x919690),mover=M32(0x919694),player=M32(0x5A0000);
 for(unsigned p=0;p<2;p++) {
  M32(0x919690)=M32(0x919650+p*0x70+0x40);M32(0x919694)=M32(0x919650+p*0x70+0x44);
  if(fallback_fixture)M32(0x5A0000)=0x5B0000+p*0x1000;
  Cpu c=cpu();run_guest(&c,retail_env,0,NULL);
  for(unsigned i=0;i<6;i++)memcpy(expected_face[p][i],G+M32(0x919DC0+i*0x70+0x40),64);
  memcpy(expected_orientation[p],G+0x987FC0,64);
 }
 M32(0x919690)=camera;M32(0x919694)=mover;M32(0x5A0000)=player;cap_views(snapshots,1);memcpy(G+0x987FC0,orient,64);
}
static void direct_scope(unsigned p) {
 Cpu c=cpu(),before=c;uint32_t source=0x919650+p*0x70;
 vp[0]=17;vp[1]=19;vp[2]=891;vp[3]=413;vp[4]=0x3E800000;vp[5]=0x3F400000;
 sc[0]=11;sc[1]=13;sc[2]=817;sc[3]=419;rs[174]=p;rs[7]=2;rs[14]=0;
 CaptureDevice device_before;cap_device_save(&c,&device_before,0x7FF000);
 cap_release(&c,device_before.rt);cap_release(&c,device_before.depth);
 cap_singleton(0);CaptureSingleton singleton_before=capture_saved;
 uint8_t cameras[2][0x2C0];for(unsigned i=0;i<2;i++)memcpy(cameras[i],G+M32(0x919690+i*0x70),0x2C0);
 split_capture_begin(&c,source,rects[p],0x7FD000);
 CHECK(!memcmp(&c,&before,sizeof c));
 uint32_t rw,rh;x360_reflection_dimensions(rects[p][2],rects[p][3],&rw,&rh);
 CHECK(res(M32(0x982A6C))->o.w==rw&&res(M32(0x982A6C))->o.h==rh);
 CaptureDevice after;cap_device_save(&c,&after,0x7FF000);cap_release(&c,after.rt);cap_release(&c,after.depth);
 CHECK(!memcmp(&device_before,&after,sizeof after));
 CHECK(!memcmp(G+0x987FC0,expected_orientation[p],64));
 uint32_t cube=M32(0x982A30);for(unsigned i=0;i<6;i++)CHECK(res(cube)->tag[i]==expected_tag[p][i]);
 if(diag_on) {
  uint32_t original_target=M32(CAP_RT);M32(CAP_RT)=M32(source+0x60);
  split_capture_receiver_texture(3,M32(CAP_REFL+4));split_capture_receiver_texture(1,M32(CAP_CUBE));
  M32(CAP_RT)=original_target;
 }
 split_capture_end(&c);cap_singleton(0);CHECK(!memcmp(&capture_saved,&singleton_before,sizeof capture_saved));
 for(unsigned i=0;i<2;i++)CHECK(!memcmp(cameras[i],G+M32(0x919690+i*0x70),0x2C0));
 CHECK(!memcmp(&c,&before,sizeof c)&&!split_capture_source());
}
static void parity(void (*current)(Cpu *),void (*retail)(Cpu *),uint32_t self,int n,const uint32_t *args) {
 uint8_t *initial=malloc(MEMSIZE),*actual=malloc(MEMSIZE);CHECK(initial&&actual);
 memcpy(initial,G,MEMSIZE);Cpu a=cpu(),b=cpu();a.ecx=b.ecx=self;
 run_guest(&a,current,n,args);memcpy(actual,G,MEMSIZE);memcpy(G,initial,MEMSIZE);
 run_guest(&b,retail,n,args);CHECK(!memcmp(&a,&b,sizeof a));CHECK(!memcmp(actual,G,MEMSIZE));
 memcpy(G,initial,MEMSIZE);free(initial);free(actual);
}
int main(void) {
 initialize();Cpu c=cpu();update_players();
 parity(f_00756060,retail_env,0,0,NULL);
 uint32_t camera_args[2]={M32(0x919690)+0x40,M32(0x919694)-4+0x120};
 parity(f_006DA350,retail_cameras,0x982D60,2,camera_args);
 uint32_t cull=0x7FD000;parity(f_006BFDD0,retail_cull,0,1,&cull);
 split_capture_mode(&c,1);
 for(frame_no=0;frame_no<7;frame_no++) {
  update_players();reference();
  memset(cube_faces,0,sizeof cube_faces);memset(road_geometry,0,sizeof road_geometry);memset(road_skies,0,sizeof road_skies);
  memset(main_receivers,0,sizeof main_receivers);clears=worlds=prep=0;
  uint32_t counter=M32(0x9885B8);c=cpu();c.esp-=4;f_006DE300(&c);
  CHECK(c.esp==0x800000&&c.ebx==7&&c.esi==8&&c.edi==9&&c.ebp==10);
  CHECK(clears==1&&presents==0&&prep==1&&worlds==12&&M32(0x9885B8)==counter+1);
  CHECK(receiver_cube[0]!=receiver_cube[1]&&receiver_road[0]!=receiver_road[1]);
  CHECK(road_geometry[0]==0&&road_geometry[1]==2&&blur[0]==0&&blur[1]==(unsigned)frame_no+1);
  CHECK(cube_faces[0]==(frame_no?1u:6u)&&cube_faces[1]==(frame_no?1u:6u));
  for(unsigned p=0;p<2;p++)for(unsigned f=0;f<6;f++)CHECK(res(receiver_cube[p])->tag[f]==expected_tag[p][f]);
 }
 // Non-default incoming viewport origins/depth range/scissor/RS restore exactly after the capture.
 update_players();reference();direct_scope(0);direct_scope(1);
 // Water history belongs to the player that emitted the main water material.
 water_mode=1;capture_source=0x9196C0;mat_water_emit(0);capture_source=0;mat_water_frame();
 capture_source=0x919650;CHECK(!mat_water_reflection_requested());capture_source=0x9196C0;CHECK(mat_water_reflection_requested());
 capture_source=0;mat_water_frame();capture_source=0x9196C0;CHECK(!mat_water_reflection_requested());capture_source=0;
 // The actual restart hook preserves the retail CPU and all guest bytes in single-player.
 // Identity-stable split restart must release BOTH banks and invalidate post histories.
 Cpu expected=c;f_006308E0_orig(&expected);uint32_t generation=capture_generation;
 uint8_t *single=malloc(MEMSIZE);memcpy(single,G,MEMSIZE);split_on=0;
 f_006308E0(&c);CHECK(!memcmp(&c,&expected,sizeof c));CHECK(!memcmp(single,G,MEMSIZE));free(single);
 CHECK(capture_generation==generation&&restart_posts==0);
 split_on=1;expected=c;f_006308E0_orig(&expected);
 uint32_t old_cube[2]={receiver_cube[0],receiver_cube[1]};f_006308E0(&c);
 CHECK(!memcmp(&c,&expected,sizeof c));CHECK(capture_generation==generation+1&&restart_posts==1);
 CHECK(!res(old_cube[0])->alive&&!res(old_cube[1])->alive);
 memset(cube_faces,0,sizeof cube_faces);update_players();reference();direct_scope(0);direct_scope(1);
 CHECK(cube_faces[0]==6&&cube_faces[1]==6);
 // Odd heights round target dimensions separately; exact source projections still match.
 rects[0][3]=359;rects[1][1]=359;rects[1][3]=360;M32(0x400018)=359;M32(0x400034)=360;
 update_players();reference();direct_scope(0);direct_scope(1);
 CHECK(capture_banks[0].h==179&&capture_banks[1].h==180);
 // Camera movers without a car take the retail local-player fallback, still owned by the source.
 fallback_fixture=1;update_players();reference();direct_scope(0);direct_scope(1);fallback_fixture=0;
 uint32_t player2=M32(0x5A0004),old_player_cube=capture_banks[1].cube[0];
 capture_source=0x9196C0;mat_water_emit(0);capture_source=0;mat_water_frame();
 M32(0x5B201C)=1;M32(0x5A0004)=0x5B2000;cube_faces[1]=0;update_players();reference();direct_scope(1);
 CHECK(cube_faces[1]==6&&old_player_cube&&!res(old_player_cube)->alive);M32(0x5A0004)=player2;
 capture_source=0x9196C0;CHECK(!mat_water_reflection_requested());capture_source=0;
 // Removal/reinsertion can reorder the local-player vector while slots and views retain their identities.
 M32(0x5A0000)=player2;M32(0x5A0004)=0x5B0000;fallback_fixture=1;
 update_players();reference();direct_scope(0);direct_scope(1);
 M32(0x5A0000)=0x5B0000;M32(0x5A0004)=player2;fallback_fixture=0;
 M8(0x520040)=1;update_players();reference();direct_scope(0);direct_scope(1);M8(0x520040)=0;
 // PC-sized captures retain the source projection, and X360 dimensions floor each half independently.
 refl_x360=0;M32(0x9017D4)=2;update_players();reference();direct_scope(0);direct_scope(1);
 CHECK(capture_banks[0].w==320&&capture_banks[0].h==240&&capture_banks[1].w==320&&capture_banks[1].h==240);
 refl_x360=1;uint32_t tiny_w,tiny_h;x360_reflection_dimensions(1,1,&tiny_w,&tiny_h);CHECK(tiny_w==1&&tiny_h==1);
 // Water-only targets initialize before the first possible receiver, then retain their own request history.
 M32(0x9017D4)=0;mat_water_reset();update_players();reference();unsigned road_before=road_geometry[0];
 direct_scope(0);CHECK(road_geometry[0]==road_before+2&&capture_banks[0].road_valid);
 direct_scope(0);CHECK(road_geometry[0]==road_before+2);
 split_capture_mode(&c,0);CHECK(!capture_banks[0].initialized&&!capture_banks[1].initialized);
 // Outside split no guest calls/resources/memory changes beyond the original body.
 unsigned calls_before=ncalls;int allocs_before=allocations;split_on=0;c=cpu();c.esp-=4;f_006DE300(&c);
 CHECK(orig_calls==1&&ncalls==calls_before+2&&allocations==allocs_before);
 // Partial allocation cleanup must release every already-created bank resource.
 split_on=1;split_capture_mode(&c,1);
 M32(0x9017D4)=3;
 for(unsigned failure=1;failure<=18;failure++) {
  split_capture_reset(&c);fail_at=allocations+failure;unsigned start=nresources;
  split_capture_begin(&c,0x919650,rects[0],0x7FD000);split_capture_end(&c);split_capture_reset(&c);
  for(unsigned i=start;i<nresources;i++)CHECK(!resources[i].alive);
 }
 CHECK(diag_ticks_calls==0&&diag_draw_calls==0&&diag_lines==0);
 fail_at=0;diag_on=1;update_players();reference();direct_scope(0);direct_scope(1);
 CHECK(diag_lines==2);
 for(unsigned p=0;p<2;p++)CHECK(capture_diag[p].road_ran&&capture_diag[p].road_draws&&capture_diag[p].cube_draws&&
   capture_diag[p].receivers[0][p]==1&&capture_diag[p].receivers[1][p]==1&&!capture_diag[p].receivers[0][p^1]&&
   capture_diag[p].stages[0]==8&&capture_diag[p].stages[1]==2);
 direct_scope(0);direct_scope(1);CHECK(diag_lines==2);
 diag_clock+=1000000000ull;direct_scope(0);direct_scope(1);CHECK(diag_lines==4);
 diag_on=0;unsigned ticks_before=diag_ticks_calls,draw_calls_before=diag_draw_calls;
 direct_scope(0);direct_scope(1);CHECK(diag_ticks_calls==ticks_before&&diag_draw_calls==draw_calls_before&&diag_lines==4);
 printf("splitcapture: %d checks, %d failures\n",checks,failures);free(G);return failures?1:0;
}
'''


def source():
    wide = (ROOT/'runtime/widescreen.c').read_text()
    x360 = (ROOT/'runtime/x360.m').read_text()
    mats = (ROOT/'runtime/materials.m').read_text()
    bodies = '\n'.join(translated(f, n) for f, n in [
        ('speed_40.c','006CF400'),('speed_40.c','006DA350'),('speed_47.c','00756060'),
        ('speed_39.c','006BFDD0'),('speed_39.c','006BDBB0'),('speed_39.c','006BDC60'),
        ('speed_40.c','006CF0A0'),('speed_06.c','0045ED30'),('speed_06.c','0045F490'),
        ('speed_06.c','0045F7C0'),('speed_04.c','00444FE0'),('speed_39.c','006BE9F0'),('speed_39.c','006BD610')])
    bodies += '\n'+translated('speed_00.c','004021E0')+'\n'+translated('speed_03.c','00425200')
    retail = translated('speed_40.c','006DA350')+'\n'+translated('speed_47.c','00756060')
    retail = re.sub(r'M32\(split_capture_address\(0x00919690u\)\)', 'M32(0x919690)', retail)
    retail = re.sub(r'M32\(split_capture_address\(0x00919694u\)\)', 'M32(0x919694)', retail)
    retail = retail.replace('split_capture_address(0x00919694u)', '(uint32_t)0x919694u')
    retail = retail.replace('c->eax = split_capture_player(c->eax);','')
    retail = retail.replace('f_006DA350(', 'retail_cameras(').replace('f_00756060(', 'retail_env(')
    retail += '\n'+translated('speed_39.c','006BFDD0').replace('f_006BFDD0(', 'retail_cull(').replace(
        'M32(split_capture_address(0x009196B8u))', 'M32(0x9196B8)')
    capture = (ROOT/'runtime/splitcapture.c').read_text()
    world = (ROOT/'runtime/splitworld.c').read_text()
    water = mats[mats.index('static int water_mode ='):mats.index('const uint8_t *mat_water_normal')]
    helpers = '\n'.join(function(x360,n) for n in ['x360_reflection_dimensions','x360_reflection_target','water_reflection_active',
        'x360_water_reflection_level','x360_water_reflection_wetness'])
    projection = '\n'.join(function(wide,n) for n in ['f_006CF400','widescreen_projection'])
    restart = function(x360,'f_006308E0')
    return PRE+water+helpers+projection+bodies+retail+capture+restart+world+POST


def main():
    src = source()
    mutants = {
        'restart-capture-stale': ('split_capture_reset(c);\n    split_post_restart();', '(void)c;\n    split_post_restart();'),
        'restart-post-stale': ('    split_post_restart();', '    (void)0;'),
        'restart-single-player-touched': ('if (!split_view_rect(0x00919650u, NULL) || !split_view_rect(0x009196C0u, NULL)) return;', 'if (0) return;'),
        'full-output-probe-height': ('h == r[3]/2;', 'h == r[3];'),
        'reused-P1-camera': ('M32(CAP_ROAD + 0x40) = camera;', 'M32(CAP_ROAD + 0x40) = M32(CAP_P1 + 0x40);'),
        'P1-wetness': ('M32(split_capture_address(0x009196B8u))', 'M32(0x9196B8)'),
        'shared-cube': ('&capture_banks[source == CAP_P2]', '&capture_banks[0]'),
        'P1-basis': ('c->esi = (uint32_t)(M32(split_capture_address(0x00919690u)));', 'c->esi = (uint32_t)(M32(0x919690));'),
        'stale-face': ('if (!(b->valid_faces & (1u << i))) M8(v + 8) = 1;', 'if (0) M8(v + 8) = 1;'),
        'stale-face-camera': ('call_guest(c, 0x00756060u, 0, NULL); // both eye/target', '(void)c; // both eye/target'),
        'stale-face-frustum': ('cap_matrices(c, v);', '(void)v;'),
        'stale-culling': ('call_guest(c, 0x006BFDD0u, 1, &cull);\n        CaptureDevice', '(void)cull;\n        CaptureDevice'),
        'P1-water-history': ('water_previous_frame[water_owner()]', 'water_previous_frame[1]'),
        'P1-player-fallback': ('if (player) return player;', 'return original;'),
        'local-player-order': ('M32(player + 0x1C) == slot', 'i == slot'),
        'water-uninitialized': ('return b->reflection[0] && b->road_level < 2 && !b->road_valid && mat_water_enabled();', 'return 0;'),
        'PC-dimensions': ('*w = refl_x360 ? (view_w > 1 ? view_w / 2 : 1) : 320;', '*w = view_w > 1 ? view_w / 2 : 1;'),
        'cube-sky-layer': ('cap_sky(c, v, 3,', 'cap_sky(c, v, 2,'),
        'cube-sky-strength': ('? colour : 0x3F000000u;', '? colour : M32(0x008F92E0u);'),
        'P1-weather-strength': ('rain = M32(capture_source + 0x68), strength', 'rain = M32(CAP_P1 + 0x68), strength'),
        'player-replacement': (' || b->player != player)', ')'),
        'stale-water-history': ('mat_water_reset_view(source);', '(void)source;'),
        'viewport-depth-range': ('memcpy(G + tmp, s->vp, 24);', 'memcpy(G + tmp, s->vp, 24); M32(tmp+16)=0; M32(tmp+20)=0x3F800000;'),
        'diag-unthrottled': ('diag->sample = now >= diag->next;', 'diag->sample = 1;'),
        'road-inactive': ('M8(CAP_ROAD + 8) = b->reflection[0] != 0;', 'M8(CAP_ROAD + 8) = 0;'),
        'road-target-inactive': ('M32(rd + 0x10) = b->reflection[0] != 0;', 'M32(rd + 0x10) = 0;'),
        'diag-shared-owner': ('CaptureDiag *d = &capture_diag[capture_source == CAP_P2];\n    if (!d->sample)',
                              'CaptureDiag *d = &capture_diag[0];\n    if (!d->sample)'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-splitcapture-') as d:
        d = Path(d)
        def run(label, text):
            path=d/'test.c';binary=d/'test';path.write_text(text)
            r=subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-g',
                '-Wno-unused-variable','-Wno-parentheses-equality',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-DRECOMP_ABICHECK',
                '-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),'-I'+str(ROOT/'runtime'),str(path),'-o',str(binary)],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr[-6500:])
            r=subprocess.run([str(binary)],capture_output=True,text=True)
            print(label, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-1200:])
            if label=='positive' and r.returncode: print(r.stdout[-2500:],r.stderr[-2500:])
            return r
        if run('positive',src).returncode:raise SystemExit(1)
        for label,(old,new) in mutants.items():
            assert src.count(old)==1,(label,src.count(old))
            r=run('mutant '+label,src.replace(old,new))
            if r.returncode!=1 or 'failures' not in r.stdout:raise SystemExit('mutant not rejected: '+label)
        renderer=(ROOT/'runtime/render.m').read_text()
        surface='id<MTLTexture> surface_target('+renderer.split('id<MTLTexture> surface_target(',1)[1].split('// ---------------- setup',1)[0]
        (d/'splitcapture_surface.inc').write_text(surface)
        gpu=d/'gpu'
        result=subprocess.run(['clang','-fobjc-arc','-Wall','-Wextra','-Werror','-I'+str(d),
            str(ROOT/'runtime/splitcapture_test.m'),'-framework','Metal','-framework','Foundation','-o',str(gpu)],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr[-4000:])
        print('PASS offline Objective-C compile: production surface_target GPU companion')
        if os.environ.get('CODEX_GPU_CHECK')=='1':
            for mode,expected in [('',0),('shared',1),('stale',1)]:
                result=subprocess.run([str(gpu)]+([mode] if mode else []),capture_output=True,text=True)
                print(result.stdout[-1000:],end='')
                if result.returncode!=expected:raise RuntimeError('GPU '+(mode or 'positive')+' exit '+str(result.returncode)+' '+result.stderr[-1500:])
        else:
            print('GPU PENDING: run CODEX_GPU_CHECK=1 on a GPU host; no GPU access attempted')
    print(f'SPLIT CAPTURE PASS ({len(mutants)}/{len(mutants)} CPU mutants rejected; GPU status above)')


if __name__=='__main__': main()
