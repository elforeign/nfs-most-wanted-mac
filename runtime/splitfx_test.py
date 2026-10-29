#!/usr/bin/env python3
"""Actual splitworld effects + translated rain, pool, projection, sun queries and emitters.
CPU/recording sinks only: no GPU, game or Unicorn. ASan/UBSan: 5,345 checks / 0 failures.
Seven 16 MiB single-player snapshots + CPU match original rain (including paused),
drops, pooled flares, projection, sun operands and vehicle-trail update block: 117,440,512 bytes. Native rain,
emitter traversal and query-ring bodies execute; D3D, physics/particle submission,
sun layer geometry and frame simulation are recording sinks.
14/14 controls rejected (failure counts): P1 rain 8, double phase 1, kept /8 3,
kept 10 drops 3, shared queries 11, pool P1 rain 7, P1 tunnel 8, skipped P2
emitters 2, P1 emitter basis 20, duplicate tick 3, ID1-only lamps 4, P1 headlight
direction 2, global projection 2, double vehicle trail tick 2. Real IDs/exclusion masks and one frame clear checked.
"""
from pathlib import Path
import importlib.util
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('world', ROOT/'runtime/splitworld_test.py')
world = importlib.util.module_from_spec(spec)
spec.loader.exec_module(world)


def translated(file, name):
    src = (ROOT/'recomp/out'/file).read_text()
    return re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', src, re.S)[0]


DECL = r'''
static int fx_route(Cpu *,uint32_t,int,const uint32_t *);
static void fx_dispatch(Cpu *,uint32_t);
'''
EXTRA = r'''
#include "settings.h"
static int paused, tick_count, rain_calls[2], emitter_calls[2], particle_calls[2], pool_tunnels[2], pool_flares[2];
static int sun_calls[2], query_created, query_released, query_issued[2], query_read[2], overlay_calls[2];
static uint32_t sun_seen[2], current_render_view, next_query=0x720000;
static unsigned snapshot_bytes;
static Setting *lamp_setting;
int lamp_batch_alt;
void settings_add(Setting *s){lamp_setting=s;s->value=s->def;}
int postfx_look_variant(const char *v){(void)v;return 0;}
static void run_guest(Cpu*c,void(*f)(Cpu*),int n,const uint32_t *a){
 uint32_t sp=c->esp;c->esp-=4*(n+1);M32(c->esp)=0xFEED;
 for(int i=0;i<n;++i)M32(c->esp+4+4*i)=a[i];
 f(c);c->esp=sp;
}
void guest_divide_error(Cpu*c,uint32_t pc){(void)c;(void)pc;abort();}
static void identity(uint32_t a){memset(G+a,0,64);for(unsigned i=0;i<4;++i)MF32(a+20*i)=1;}
void f_00410D40(Cpu*c){c->eax=0xBAD;c->esp+=4;}
void f_0064B680(Cpu*c){c->eax=paused;c->esp+=4;}
void f_006BF530(Cpu*c){c->eax=3;c->esp+=4;} // true split mode throughout: never falsified
void f_007252F0(Cpu*c){c->esp+=20;}
void f_0074ACC0(Cpu*c){c->esp+=4;}
void f_0074B000(Cpu*c){uint32_t v=arg(c,0);c->eax=(v==0x919650);c->esp+=4;}
void f_007539D0(Cpu*c){x87_push(c,1.0);c->esp+=4;}
void f_00753AC0(Cpu*c){x87_push(c,0.8);c->esp+=4;}
void f_0073CC80(Cpu*c){c->esp+=8;}
void f_00757770(Cpu*c){c->esp+=4;}
void f_0074A070(Cpu*c){c->esp+=4;}
void f_0074A160(Cpu*c){c->esp+=4;}
void f_0074A320(Cpu*c){c->esp+=8;}
void f_0074A5D0(Cpu*c){c->esp+=4;}
void f_0045DA70(Cpu*c){c->esp+=4;}
void f_0045F490(Cpu*c){c->esp+=4;}
void f_007C4B80(Cpu*c){int64_t v=(int64_t)ST(0);c->eax=(uint32_t)v;c->edx=(uint64_t)v>>32;x87_pop(c);c->esp+=4;}
void f_0073CFE0(Cpu*c){uint32_t v=arg(c,0);++pool_tunnels[v==0x9196C0];
 if(fx_source){CHECK(v==fx_source);CHECK(fabsf(MF32(c->esp+0x1C)-(1-MF32(M32(fx_source+0x68)+0x290)))<1e-5);}c->eax=(v==0x919650);c->esp+=4;}
void f_00505380(Cpu*c){uint32_t v=arg(c,0);CHECK(v==fx_source || !fx_source);
 ++pool_flares[v==0x9196C0];c->eax=1;c->esp+=4;}
void f_007E7D64(Cpu*c){uint32_t dst=arg(c,0),src=arg(c,1),m=arg(c,2);float out[4]={0};
 for(unsigned col=0;col<4;++col)for(unsigned row=0;row<4;++row)out[col]+=MF32(src+4*row)*MF32(m+4*(row*4+col));
 memcpy(G+dst,out,16);c->eax=dst;c->esp+=16;}
void f_00460BF0(Cpu*c){c->eax=1;c->esp+=4;}
void f_004FD370(Cpu*c){c->esp+=4;}
void f_00503400(Cpu*c){c->eax=0x680000;c->esp+=4;}
void f_006C0160(Cpu*c){uint32_t p=arg(c,0);memset(G+p,0,0x90);CHECK(!fx_source||c->ecx==sun_seen[fx_source==0x9196C0]);c->esp+=28;}
void f_006C8000(Cpu*c){c->esp+=4;}
void f_006D1260(Cpu*c){if(fx_source)CHECK(vp[1]==(fx_source==0x9196C0?360u:0u));c->esp+=4;}
void f_006C0A30(Cpu*c){memset(G+c->ecx,0,0x94);c->esp+=4;}
void f_00444FE0(Cpu*c){memcpy(G+arg(c,0),G+arg(c,1),64);c->esp+=4;}
void f_006BCC00(Cpu*c){uint32_t v=arg(c,0),plat=M32(v);current_render_view=v;CHECK(v==fx_source);
 for(unsigned i=0;i<3;++i)memcpy(G+arg(c,i+1),G+plat+16*i,12);c->esp+=4;}
void f_006CEEE0(Cpu*c){CHECK(arg(c,0)==fx_source);c->eax=1;c->esp+=4;}
void f_004FDFE0(Cpu*c){(void)c;CHECK(0);abort();}
void f_006BD920(Cpu*c){CHECK(current_render_view==fx_source);CHECK(arg(c,0)==0x672000);
 uint32_t plat=M32(current_render_view);CHECK(MF32(arg(c,1))==MF32(plat));
 CHECK(MF32(0x672008)==4+.25f*tick_count);c->esp+=4;}
void f_004FEF50(Cpu*c){++particle_calls[current_render_view==0x9196C0];c->esp+=4;}
void f_006C1160(Cpu*c){c->esp+=28;}
void f_006DA2F0(Cpu*c){c->esp+=4;}
void f_006C6770(Cpu*c){c->esp+=4;}
static void fx_dispatch(Cpu*c,uint32_t fn){
 if(fn>=0xF0000000u){ // query vtable, distinct object per player, never current/in-flight reads in host
   uint32_t q=arg(c,0),o=M32(q+4),off=fn-0xF0000000u;CHECK(o<2);
   if(off==8){++query_released;c->esp+=8;return;}
   if(off==0x18){++query_issued[o];c->esp+=12;return;}
   if(off==0x1C){++query_read[o];M32(arg(c,1))=o?1024:0;c->eax=0;c->esp+=20;return;}
   CHECK(0);abort();
 }
 if(fn>=DEVMAGIC&&fn<DEVMAGIC+0x200){unsigned off=fn-DEVMAGIC;
   if(off==0x1D8){uint32_t q=next_query;next_query+=16;M32(q)=0x71F000;M32(q+4)=fx_source==0x9196C0;
     M32(arg(c,2))=q;++query_created;c->eax=0;c->esp+=16;return;}
   if(off==0xE4){c->esp+=16;return;}
   if(off==0x15C){c->esp+=12;return;}
 }
 if(fn>=SHMAGIC&&fn<SHMAGIC+0x20){c->esp+=4;return;}
 if(fn>=FXMAGIC&&fn<FXMAGIC+0x200){uint32_t off=fn-FXMAGIC;
   c->esp+=off==0xFC?16:off==0x68?16:8;return;}
 fprintf(stderr,"fx dispatch %x\n",fn);abort();
}
static int fx_route(Cpu*c,uint32_t fn,int n,const uint32_t*a){
 switch(fn){
 case 0x504C50: ++tick_count;MF32(0x672008)+=.25f;return 1;
 case 0x6C00D0: run_guest(c,f_006C00D0,n,a);return 1;
 case 0x6C0130: run_guest(c,f_006C0130,n,a);return 1;
 case 0x758100:{unsigned i=fx_source==0x9196C0;CHECK(c->ecx==M32(fx_source+0x68));++rain_calls[i];run_guest(c,f_00758100,n,a);return 1;}
 case 0x503D00:{unsigned i=a[0]==0x9196C0;++emitter_calls[i];CHECK(a[0]==fx_source);run_guest(c,f_00503D00_orig,n,a);return 1;}
 case 0x507770: run_guest(c,f_00507770,n,a);return 1;
 case 0x6D04E0:{unsigned i=a[0]==0x9196C0;++sun_calls[i];sun_seen[i]=c->ecx;
   CHECK(c->ecx!=0x93E018);CHECK(!i||sun_seen[0]!=sun_seen[1]);
   run_guest(c,f_006D04E0,n,a);return 1;}
 case 0x6D1A40:{unsigned i=fx_source==0x9196C0;++overlay_calls[i];uint32_t db=M32(fx_source+0x64),poly=a[0];
   CHECK(M32(poly)==M32(db+0x14C));CHECK(M32(poly+0x30)==M32(db+0x164));CHECK(M32(poly+0x80)==0xFFFFFFFF);return 1;}
 default:return 0;
 }
}
'''
TEST = r'''
static void effects_setup(void){
 setup();paused=0;tick_count=0;memset(rain_calls,0,sizeof rain_calls);memset(emitter_calls,0,sizeof emitter_calls);
 memset(particle_calls,0,sizeof particle_calls);memset(pool_tunnels,0,sizeof pool_tunnels);memset(pool_flares,0,sizeof pool_flares);
 memset(sun_calls,0,sizeof sun_calls);memset(query_issued,0,sizeof query_issued);memset(query_read,0,sizeof query_read);
 memset(overlay_calls,0,sizeof overlay_calls);memset(suns,0,sizeof suns);query_created=query_released=0;next_query=0x720000;
 for(uint32_t a=0;a<0x20;a+=4)M32(0x71F000+a)=0xF0000000u+a;
 M32(0x93DE94)=M32(0x93DE9C)=M32(0x93DEC0)=0x510000;
 M32(0x925E90)=6;M32(0x982BE4)=1280;M32(0x982BE8)=721;
 M32(0x9017E4)=M32(0x9017EC)=1;M32(0x9B2A9C)=M32(0x9B0C48)=1;M32(0x8F86E4)=1;
 MF32(0x925970)=.1f;MF32(0x89096C)=1;MF32(0x890960)=360;MF32(0x890F14)=.125f;MF32(0x8B2F28)=8;
 MF32(0x904AEC)=10;MF32(0x904AC4)=MF32(0x904AC8)=100;MF32(0x904B30)=1;MF32(0x9B0A50)=4;
 MF32(0x8933D4)=.5f;MF32(0x891068)=-.5f;MF32(0x8AE8F8)=2;MF32(0x8AF2C0)=-2;MF32(0x890DC4)=.01f;
 M32(0x916054)=0x690000;M32(0x9B392C)=0x691000;MF32(0x69117C)=1;
 identity(0x987AB0);M32(0x93E01C)=1;MF32(0x93E020)=.5f;MF32(0x93E024)=.999f;
 for(unsigned i=0;i<2;++i){uint32_t v=0x919650+0x70*i,r=0x580000+0x8000*i,p=0x570000+0x1000*i,db=0x560000+0x1000*i;
   M32(v+0x44)=0x675000+0x100*i;M32(v)=p;identity(p);identity(p+0x80);MF32(p)=1+i;MF32(p+0x80+48)=i?.5f:-.5f;
   M32(v+0x68)=r;M32(r+0x288)=v;M32(r+0x280)=4;
   MF32(r+0x28C)=.5f;MF32(r+0x290)=i?.8f:.2f;MF32(r+0x369C)=1;MF32(r+0x36A0)=.7f;
   M32(v+0x64)=db;M32(db+0x30)=1;for(unsigned k=0;k<12;++k)MF32(db+0x14C+4*k)=i*100+k;
   for(unsigned k=0;k<20;++k){MF32(r+0x1C*k+4)=.3f+i*.2f;MF32(r+0x1C*k+8)=.4f;MF32(r+0x1C*k+12)=2;MF32(r+0x1C*k+16)=2;}
 }
 // A real linked emitter group/emitter/particle; simulation age changes only in the once-only frame prep sink.
 M8(0x8F86EC)=1;M32(0x9166C0)=1;M32(0x916A38)=0x670000;M32(0x670000)=0x916A38;
 M32(0x670008)=0x671000;M32(0x671000)=0x670008;M32(0x67107C)=0x673000;M32(0x673008)=0x674000;
 M32(0x671080)=0x672000;M32(0x672000)=0x671080;MF32(0x672008)=4;
 M32(0x915F8C)=1; // ActivePoolIndex (set from translated pool's actual operand below by Python)
 M8(0x915F90+0x2C)=0x15;MF32(0x915F90+0x10)=.5f;
}
static void trail_history(void){
 uint32_t car=0x676000;flare_stepped_count=0;M32(car+0x1280)=0;
 for(unsigned i=0;i<2;++i){fx_source=i?0x9196C0:0x919650;Cpu c=cpu();c.eax=fx_source;c.edi=car;identity(c.esp+0x40);
  flare_history(&c);CHECK(M32(car+0x1280)==1);CHECK(M32(fx_source+4)==i+1);}
 flare_stepped_count=0;fx_source=0x9196C0;Cpu c=cpu();c.eax=fx_source;c.edi=car;identity(c.esp+0x40);flare_history(&c);CHECK(M32(car+0x1280)==2);
 // A car missed by P1 still steps when P2 first encounters it.
 c=cpu();c.eax=fx_source;c.edi=car+0x2000;identity(c.esp+0x40);flare_history(&c);CHECK(M32(car+0x2000+0x1280)==1);
 fx_source=0;uint8_t*before=malloc(MEMSIZE),*after=malloc(MEMSIZE);memcpy(before,G,MEMSIZE);
 c=cpu();c.eax=0x919650;c.edi=car;identity(c.esp+0x40);flare_history(&c);memcpy(after,G,MEMSIZE);
 memcpy(G,before,MEMSIZE);Cpu d=cpu();d.eax=0x919650;d.edi=car;identity(d.esp+0x40);retail_flare_history(&d);
 CHECK(!memcmp(&c,&d,sizeof c));CHECK(!memcmp(after,G,MEMSIZE));snapshot_bytes+=MEMSIZE;free(before);free(after);
}
static void lamp_selectors(void){Cpu c=cpu();c.esp=0x7F0000;M32(c.esp+4)=0x680100;M32(c.esp+8)=0x680200;
 for(unsigned id=0;id<24;++id)for(unsigned reflection=0;reflection<3;++reflection)for(unsigned t=0;t<23;++t){
  M32(0x680104)=id;M8(0x68022C)=t;M32(c.esp+20)=reflection;lampglow_begin(&c);
  int want=(id==1||id==2)&&!reflection&&(t==5||t==6)?t:0;
  CHECK(lampglow_type()==want);CHECK(lampglow_owner()==(want?id:0));lampglow_end();CHECK(!lampglow_type()&&!lampglow_owner());
 }
}
static void snapshots(void){
 const struct{void(*current)(Cpu*);void(*retail)(Cpu*);uint32_t ecx;int n;uint32_t args[2];} cases[]={
  {f_00758100,retail_00758100,0x580000,0,{0,0}},
  {f_0073CDB0,retail_0073CDB0,0x580000,1,{0x919650,0}},
  {f_00507770,retail_00507770,0,1,{0x919650,0}},
  {f_006BE940,retail_006BE940,0x919650,2,{0x680400,0x680440}},
  {f_006D04E0,retail_006D04E0,0x680500,1,{0x919650,0}},
  {f_00758100,retail_00758100,0x580000,0,{0,0}}
 };
 fx_source=0;uint8_t *before=malloc(MEMSIZE),*after=malloc(MEMSIZE);
 for(unsigned i=0;i<sizeof cases/sizeof cases[0];++i){
  effects_setup();paused=(i==5);M32(0x93E01C)=0;M32(0x680504)=0;memcpy(before,G,MEMSIZE);
  Cpu a=cpu();a.ecx=cases[i].ecx;run_guest(&a,cases[i].current,cases[i].n,cases[i].args);memcpy(after,G,MEMSIZE);
  memcpy(G,before,MEMSIZE);Cpu b=cpu();b.ecx=cases[i].ecx;run_guest(&b,cases[i].retail,cases[i].n,cases[i].args);
  CHECK(!memcmp(&a,&b,sizeof a));CHECK(!memcmp(after,G,MEMSIZE));snapshot_bytes+=MEMSIZE;
 }
 free(before);free(after);
}
int main(void){G=calloc(1,MEMSIZE);snapshots();trail_history();lamp_selectors();effects_setup();split_on=1;
 Cpu c=cpu();c.esp-=4;f_006DE300(&c);
 CHECK(first(0x504C50)<first(0x503D00));CHECK(tick_count==1);CHECK(MF32(0x672008)==4.25f);CHECK(count(0x504C50)==1);CHECK(clears==1);
 CHECK(rain_calls[0]==1&&rain_calls[1]==1);CHECK(emitter_calls[0]==1&&emitter_calls[1]==1);
 CHECK(particle_calls[0]==1&&particle_calls[1]==1);CHECK(overlay_calls[0]==1&&overlay_calls[1]==1);
 CHECK(sun_calls[0]==1&&sun_calls[1]==1);CHECK(query_created==8);CHECK(sun_seen[0]!=sun_seen[1]);
 CHECK(fabsf(MF32(0x9B0A50)-5)<1e-6);CHECK(M32(0x580000)==20&&M32(0x588000)==20);
 CHECK(fabsf(MF32(0x580000+0x3694)-1)<1e-6&&fabsf(MF32(0x588000+0x3694)-1)<1e-6);
 CHECK(pool_tunnels[0]==1&&pool_tunnels[1]==1);CHECK(pool_flares[0]==1&&pool_flares[1]==1);
 for(unsigned frame=0;frame<5;++frame){maps=nworld=0;c=cpu();c.esp-=4;f_006DE300(&c);}
 CHECK(query_created==8);CHECK(query_issued[0]==12&&query_issued[1]==12);CHECK(query_read[0]==2&&query_read[1]==2);
 CHECK(MF32(sun_seen[0]+0x18)!=MF32(sun_seen[1]+0x18));
 for(unsigned i=0;i<2;++i){fx_source=i?0x9196C0:0x919650;memcpy(fx_rect,rects[i],16);
  uint32_t a[2]={0x680400,0x680440};MF32(a[1])=MF32(a[1]+4)=MF32(a[1]+8)=0;
  c=cpu();c.ecx=fx_source;run_guest(&c,f_006BE940,2,a);
  CHECK(fabsf(MF32(a[0])-(i?960:320))<1e-5);CHECK(fabsf(MF32(a[0]+4)-rects[i][3]*.5f)<1e-5);
  c=cpu();flare_player_operand(&c);CHECK(c.eax==0x675000+0x100*i);
  CHECK(split_fx_drop_count(10)==20);CHECK(split_fx_rain_scale(.125f)==1&&split_fx_rain_scale(8)==1);
 }
 uint32_t fl0=pool_flares[0],fl1=pool_flares[1];M32(0x915AA8)=1u<<1;
 for(unsigned i=0;i<2;++i){fx_source=i?0x9196C0:0x919650;uint32_t a=fx_source;c=cpu();run_guest(&c,f_00507770,1,&a);}
 CHECK(pool_flares[0]==(int)fl0&&pool_flares[1]==(int)fl1+1);CHECK(M32(0x919654)==1&&M32(0x9196C4)==2);
 float phase=MF32(0x9B0A50);paused=1;c=cpu();c.ecx=M32(fx_source+0x68);run_guest(&c,f_00758100,0,NULL);CHECK(MF32(0x9B0A50)==phase);paused=0;
 fx_source=0;split_on=0;c=cpu();c.esp-=4;f_006DE300(&c);CHECK(query_released==8);
 CHECK(split_fx_drop_count(10)==10&&split_fx_rain_scale(.125f)==.125f&&split_fx_rain_dt(.1)==.1);
 printf("splitfx: %d checks, %d failures, %u SP snapshot bytes\n",checks,failures,snapshot_bytes);
 free(G);return failures?1:0;
}
'''


def source():
    body = (ROOT/'runtime/splitworld.c').read_text()
    pre = world.PRE.replace('static int checks, failures;', DECL+'static int checks, failures;')
    pre=pre.replace('uint32_t split_capture_player(uint32_t original){return original;}',
                    'uint32_t split_capture_player(uint32_t original){uint32_t v=split_fx_view(0);return v?M32(v+0x44):original;}')
    pre = pre.replace('fprintf(stderr,"ABI %x\\n",f);', 'fprintf(stderr,"ABI %x regs %x/%x %x/%x %x/%x %x/%x sp %x/%x\\n",f,c->ebx,b,c->esi,s,c->edi,i,c->ebp,p,c->esp,sp);')
    pre = pre.replace(' switch (fn) {', ' if(fx_route(c,fn,n,a))return c->eax;\n switch (fn) {')
    pre = pre.replace('void dispatch(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"unexpected dispatch %x\\n",t); exit(2); }',
                      'void dispatch(Cpu *c,uint32_t t){fx_dispatch(c,t);}')
    units = [('speed_47.c','00758100'),('speed_46.c','0073CDB0'),('speed_16.c','00507770'),
             ('speed_39.c','006BE940'),('speed_40.c','006D04E0'),('speed_40.c','006D0350'),
             ('speed_39.c','006C00D0'),('speed_39.c','006C0130'),('speed_15.c','00503D00')]
    bodies = '\n'.join(translated(f,n) for f,n in units)
    flare=translated('speed_16.c','00505380')
    operand=flare[flare.index('    /* 005056BB:'):flare.index('    /* 005056C0:')]
    bodies += '\nstatic void flare_player_operand(Cpu*c){\n'+operand+'\n}\n'
    trail=translated('speed_47.c','00744E20')
    block=trail[trail.index('    /* 00744EFF:'):trail.index('    /* 00744F79:')]
    history='static void flare_history(Cpu*c){uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0;\n'+block+'\n(void)cf;(void)pf;(void)af;(void)zf;(void)sf;(void)of;\n}\n'
    bodies += history
    retail_history=history.replace('flare_history(', 'retail_flare_history(').replace('split_fx_flare_step(c->eax, c->edi)', 'M32((uint32_t)(c->eax + 0x4u))')
    retail = '\n'.join(translated(f,n).replace('f_'+n+'(', 'retail_'+n+'(') for f,n in units[:5])
    retail = retail.replace('split_fx_view(0x00919650u)','0x00919650u').replace('split_fx_drop_count(10)','(uint32_t)0xAu')
    retail = re.sub(r'split_fx_rain_(?:dt|scale)\((MF32\(\(uint32_t\)\([^\n]*?\)\))\)',r'\1',retail)
    retail = re.sub(r'split_fx_dimension\((M32\(\(uint32_t\)\(0x982BE[48]u\)\)), [01]\)',r'\1',retail)
    assert 'split_fx_' not in retail
    setup = world.POST[world.POST.index('static Cpu cpu('):world.POST.index('int main(')]
    lamp = (ROOT/'runtime/lampglow.c').read_text()
    lamp = re.sub(r'\bdraws\b','lamp_draws',lamp)
    # Pool list addresses are decoded, never guessed fixture constants.
    pool = translated('speed_16.c','00507770')
    active = re.search(r'mov eax, dword ptr \[(0x[0-9a-f]+)\]',pool)[1]
    list_type = re.search(r'mov esi, (0x[0-9a-f]+)',pool)[1]
    test = TEST.replace('0x915F8C',active).replace('0x915F90+0x2C',list_type).replace('0x915F90+0x10',f'({list_type}-0x2C)+0x10')
    return pre+body+EXTRA+bodies+retail+retail_history+lamp+setup+test


def main():
    text = source()
    mutants = {
        'P1-rain reuse': ('c->ecx = M32(view + 0x68); call_guest(c, 0x00758100u', 'c->ecx = M32(VIEW_P1 + 0x68); call_guest(c, 0x00758100u'),
        'double phase': ('if (rain_phase_used) return 0;', 'if (0) return 0;'),
        'kept /8 reduction': ('return fx_source ? 1.f : original;', 'return original;'),
        'kept 10 drops': ('return fx_source ? 20 : original;', 'return original;'),
        'shared sun queries': ('SplitSun *s = &suns[view == VIEW_P2];', 'SplitSun *s = &suns[0];'),
        'pool P1 rain': ('M32(split_fx_view(0x00919650u) + 0x68)', 'M32(0x009196B8u)'),
        'P1 tunnel': ('uint32_t v = split_fx_view(0x00919650u);', 'uint32_t v = 0x00919650u;'),
        'skipped P2 emitters': ('c->ecx = 0x009166C0u; call_guest(c, 0x00503D00u, 1, &view);',
                                'c->ecx = 0x009166C0u; if(view==VIEW_P1)call_guest(c, 0x00503D00u, 1, &view);'),
        'P1 emitter basis': ('c->ecx = 0x009166C0u; call_guest(c, 0x00503D00u, 1, &view);',
                             'uint32_t bad=VIEW_P1; c->ecx = 0x009166C0u; call_guest(c, 0x00503D00u, 1, &bad);'),
        'duplicate tick': ('static void view_particles(Cpu *c, uint32_t view, uint32_t scratch) {',
                           'static void view_particles(Cpu *c, uint32_t view, uint32_t scratch) {call_guest(c,0x00504C50,0,NULL);'),
        'ID1-only lamps': ('(id == 1 || id == 2)', '(id == 1)'),
        'double vehicle trail tick': ('if (flare_stepped[i] == car) return 0;', 'if (0 && flare_stepped[i] == car) return 0;'),
        'P1 headlight direction': ('c->eax = split_fx_player(c->eax);', ''),
        'global projection': ('return fx_source ? fx_rect[2 + axis] : original;', 'return original + 0*axis;'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-splitfx-') as tmp:
        tmp = Path(tmp)
        def run(label, src):
            (tmp/'test.c').write_text(src)
            r = subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-parentheses-equality','-g',
                '-fsanitize=address,undefined','-fno-sanitize-recover=all','-DRECOMP_ABICHECK',
                '-I'+str(ROOT/'recomp/out'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'runtime'),str(tmp/'test.c'),'-o',str(tmp/'test')],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr[-6000:])
            r = subprocess.run([str(tmp/'test')],capture_output=True,text=True)
            print(label,r.stdout.strip().splitlines()[-1] if r.stdout.strip() else '(no output)')
            if r.returncode and label=='positive':print(r.stdout[:2500],r.stderr[-2500:])
            return r
        if run('positive',text).returncode:raise SystemExit(1)
        for label,(old,new) in mutants.items():
            assert text.count(old)==1,(label,text.count(old))
            r=run('mutant '+label,text.replace(old,new))
            if r.returncode!=1 or 'failures' not in r.stdout:raise SystemExit('mutant not rejected: '+label+' '+r.stderr[-2000:])
    print('SPLIT FX PASS (14/14 mutants rejected)')


if __name__=='__main__':main()
