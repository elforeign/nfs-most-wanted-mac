#!/usr/bin/env python3
"""














"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def translated(file, name, original=False):
    source = (ROOT / 'recomp/out' / file).read_text()
    pat = r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}'
    body = re.search(pat, source, re.S).group()
    return re.sub(r'void f_' + name + r'(?:_orig)?\(',
                  'void f_' + name + ('_orig' if original else '') + '(', body, count=1)


PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
void f_005B7420_orig(Cpu *c) { (void)c; abort(); } // Menu allocation has its own translated-code test.
int diag_on;
static int checks, failures, shared_init, init_calls[2], factory_calls[4];
static int fail_pad1, backend_stops, device_frees, other_frees;
static uint32_t dev[4]={0x310000,0x320000,0x330000,0x340000};
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
void diag_event(const char *fmt,...) { (void)fmt; }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
static Cpu cpu(uint32_t self) {
 return (Cpu){.ecx=self,.esp=0x800000,.fs_base=0x700000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
}
static void abi(Cpu *c,unsigned n) {
 CHECK(c->esp==0x800004+4*n); CHECK(c->ebx==7); CHECK(c->esi==8); CHECK(c->edi==9); CHECK(c->ebp==10);
 CHECK(c->top==0 && c->cw==0x37f);
}
void f_005CC240(Cpu *c) {
 uint32_t p=arg(c,0), h=~0u;
 while(M8(p)) h=h*33+M8(p++);
 c->eax=h; c->esp+=4;
}
void f_00628D30_orig(Cpu *c) { ++shared_init; c->eax=0x1234; c->esp+=4; }
void f_0063F850_orig(Cpu *c) { ++init_calls[0]; c->esp+=4; }
void f_0063CD10(Cpu *c) {
 unsigned p=arg(c,0); CHECK(p<2); ++factory_calls[p];
 c->eax=p==1 && fail_pad1 ? 0 : dev[p];
 if(c->eax) {
  uint32_t d=c->eax;
  memset(G+d-16,0xa5,0x1858+32); M32(d)=0x8a7b9c; M32(d+0x24)=p;
  M32(d+0x18)=d+0x34; M32(d+0x1c)=d+0x1034; M32(d+0x20)=d+0x1434;
  M32(d+0x1838)=0;
  M32(d+0x184c)=0; ++M32(0x9203c0);
 }
 c->esp+=4;
}
void dispatch(Cpu *c,uint32_t t) {
 if(t==0x63cd10) { f_0063CD10(c); return; }
 if(t==0x63f850) { f_0063F850(c); return; }
 if(t==0x6287d0) { f_006287D0(c); return; }
 if(t==0x628bf0) { c->eax=M32(c->ecx+0x1838); c->esp+=4; return; }
 if(t==0x63f9a0) { f_0063F9A0(c); return; }
 if(t==0x202) { ++backend_stops; CHECK(M32(0x9203c0)==0); c->esp+=4; return; }
 if(t==0x203) { ++other_frees; CHECK(arg(c,0)==1); c->esp+=8; return; }
 if(t==0x101 || t==0x102) {
  unsigned p=arg(c,0),kind=t-0x101; CHECK(p==(unsigned)(pad_count()==2?2:1)+kind);
  ++factory_calls[2+kind]; c->eax=dev[2+kind]; c->esp+=4; return;
 }
 fprintf(stderr,"dispatch %x\n",t); exit(2);
}
static void setup(void) {
 memset(G,0,0x1000000); memset(factory_calls,0,sizeof factory_calls);
 shared_init=init_calls[0]=init_calls[1]=backend_stops=device_frees=other_frees=0;
 const char *names[]={"GameDevice","MouseDevice","KeyboardDevice"};
 for(unsigned i=0;i<3;i++) {
  uint32_t str=0x200000+i*32,node=0x210000+i*16;
  strcpy((char *)G+str,names[i]); Cpu c=cpu(0); M32(c.esp+4)=str; f_005CC240(&c);
  M32(node)=c.eax; M32(node+4)=i?0x100+i:0x63cd10; M32(node+8)=i<2?node+16:0;
  M32(0x8a7d3c-12*i)=0; // original hash arguments below use real strings
 }
 strcpy((char *)G+0x8a7d3c,"GameDevice"); strcpy((char *)G+0x8a7d30,"MouseDevice");
 strcpy((char *)G+0x8a7d20,"KeyboardDevice");
 M32(0x920464)=0x210000; M32(0x8a7b9c+12)=0x63f850;
 for(unsigned i=0;i<16;i++) M32(0x920de8+4*i)=0xcccccccc;
 for(unsigned i=0;i<76;i++) {
  uint32_t s=0x220000+16*i; snprintf((char *)G+s,16,"ACTION%u",i); M32(0x91f418+52*i)=s;
 }
}
'''

FIXTURE = r'''
static struct { uint32_t p,n; int freed; } allocs[32];
static unsigned na, next_heap, queue_allocs;
static int fail_fe, fail_queue=-1, live_queues, freed_queues, connected1=1;
static unsigned flushes[2], enables[2], connect_checks[2], transitions, messages, errors[2], cleared[2];
static uint32_t user_mode;
static unsigned package_count=2;
static int prompts_lost;
int prompts_pad_lost(void) { return prompts_lost; }
int padguest_connection(Cpu *c);
static void heap_reset(void) {
 na=queue_allocs=live_queues=freed_queues=0; next_heap=0x400000;
 memset(flushes,0,sizeof flushes); memset(enables,0,sizeof enables); memset(connect_checks,0,sizeof connect_checks);
 transitions=messages=0; memset(errors,0,sizeof errors); memset(cleared,0,sizeof cleared);
 M32(0x920e4c)=0x2a0000; M32(0x920e54)=0;
}
static uint32_t alloc(unsigned n) {
 CHECK(na<32); unsigned p=next_heap+16; next_heap+=((n+15)&~15)+32;
 allocs[na].p=p; allocs[na].n=n; allocs[na++].freed=0;
 memset(G+p-16,0xa5,n+32); return p;
}
static void release(uint32_t p,unsigned n) {
 for(unsigned i=0;i<na;i++) if(allocs[i].p==p) {
  CHECK(!allocs[i].freed); CHECK(n==allocs[i].n); allocs[i].freed=1;
  if(n==0x28c) ++freed_queues;
  return;
 }
 CHECK(0);
}
static void bounds(void) {
 for(unsigned i=0;i<na;i++) for(unsigned b=0;b<16;b++) {
  CHECK(M8(allocs[i].p-16+b)==0xa5); CHECK(M8(allocs[i].p+allocs[i].n+b)==0xa5);
 }
}
void f_00652AD0(Cpu *c) { c->eax=fail_fe?0:alloc(arg(c,0)); c->esp+=4; }
void f_00652B00(Cpu *c) {
 uint32_t p=arg(c,0); for(unsigned i=0;i<na;i++) if(allocs[i].p==p) { release(p,allocs[i].n); break; }
 c->esp+=4;
}
void f_005D29D0(Cpu *c) {
 CHECK(c->ecx==0x925b30); CHECK(arg(c,0)==0x28c && arg(c,1)==0);
 unsigned i=queue_allocs++; c->eax=(int)i==fail_queue?0:alloc(arg(c,0)); c->esp+=12;
}
void f_005D0370(Cpu *c) {
 CHECK(c->ecx==0x925b30);
 if(arg(c,1)==0x1858) {
  ++device_frees; CHECK(arg(c,0)==dev[0] || arg(c,0)==dev[1]);
  CHECK(backend_stops==(M32(0x9203c0)==0));
 } else release(arg(c,0),arg(c,1));
 c->esp+=16;
}
void f_005D5A50(Cpu *c) { c->esp+=8; }
void f_005D59E0(Cpu *c) { c->esp+=4; }
void f_00635650(Cpu *c) {
 M32(0x2a0000+4*live_queues)=c->ecx; ++live_queues; M32(0x920e54)=live_queues; c->esp+=4;
}
void f_00633CD0(Cpu *c) { c->eax=live_queues; c->esp+=4; }
void f_00648040(Cpu *c) {
 CHECK(arg(c,0)<2); CHECK(arg(c,1)==0x82d21520);
 M32(c->ecx+0x268)=arg(c,0); M32(c->ecx+0x274)=arg(c,1); c->esp+=12;
}
void f_00635210(Cpu *c) { c->esp+=4; }
void f_0063FFD0(Cpu *c) { --live_queues; CHECK(live_queues>=0); c->esp+=4; }
void f_0054EBC0_orig(Cpu *c) {
 uint32_t fe=M32(0x91cad0);
 if(fe) {
  Cpu q=*c; q.ecx=fe; q.esp-=64; M32(q.esp)=0; f_00524770(&q);
  for(unsigned i=0;i<na;i++) if(allocs[i].p==fe) { release(fe,allocs[i].n); break; }
 }
 M32(0x91cad0)=0; c->esp+=4;
}
int pad_read_n(int idx,PadState *st) { CHECK(idx==1); st->connected=connected1; return connected1; }
void f_006E8DF0(Cpu *c) { c->eax=user_mode; c->esp+=4; }
void f_0054ECE0(Cpu *c) { unsigned p=arg(c,0); CHECK(p<2); ++errors[p]; c->esp+=8; }
void f_0050EA10(Cpu *c) { unsigned p=arg(c,0); CHECK(p<2); ++cleared[p]; c->esp+=8; }
void f_00516B50(Cpu *c) { c->eax=0; c->esp+=8; }
void f_00517070(Cpu *c) { c->esp+=4; }
void f_007C4B80(Cpu *c) { c->eax=(int32_t)c->st[c->top]; x87_pop(c); c->esp+=4; }
void f_00572070(Cpu *c) { c->eax=0x250000; c->esp+=4; }
void f_00585920(Cpu *c) { c->esp+=12; }
void f_0057D0F0(Cpu *c) { c->esp+=4; }
void f_005BBB60(Cpu *c) { ++transitions; c->esp+=12; }
void f_005BBB40(Cpu *c) { ++transitions; c->esp+=12; }
void f_005BBB80(Cpu *c) { ++transitions; --package_count; c->esp+=4; }
void f_005B0C30(Cpu *c) { c->eax=package_count; c->esp+=8; }
void f_004AF1E0(Cpu *c) { c->esp+=4; }
void f_004B9080(Cpu *c) { c->esp+=4; }
void f_00516510(Cpu *c) { c->eax=0x260000; c->esp+=4; }
void f_005252D0(Cpu *c) { ++messages; c->esp+=4; }
void f_00516BE0(Cpu *c) { ++messages; c->esp+=20; }
void f_0056AD30(Cpu *c) { c->esp+=4; }
void f_00585770(Cpu *c) { c->esp+=8; }
void f_00516F30_stub(Cpu *c) { c->esp+=4; }
void f_00660200(Cpu *c) { c->esp+=4; }
'''

MAIN = r'''
static Cpu invoke(PgFn fn,uint32_t self,unsigned n,const uint32_t *args) {
 Cpu c=cpu(self);
 for(unsigned i=0;i<n;i++) M32(c.esp+4+4*i)=args[i];
 fn(&c); abi(&c,n); return c;
}
static void snapshot(FILE *out) { fwrite(G,1,0x1000000,out); }
static void ownership(unsigned player,unsigned port) {
 uint32_t a[]={player,port}; invoke(f_0056DBA0,0x270000,2,a);
}
static void event(uint32_t q,unsigned id,float v) {
 M32(q)=1; M32(q+8)=0; M32(q+16)=id; M32(q+20)=M32(q+0x268); MF32(q+24)=v;
}
static void fe_tests(FILE *out) {
 unsigned two=pad_count()==2;
 setup(); fail_pad1=0; invoke(f_0063CBF0,0x920de0,0,NULL); heap_reset();
 M32(0x91cf90)=0x270000; memset(G+0x270000,0xaa,32); M8(0x27000c)=M8(0x27000d)=0xff;
 M32(0x91cae0)=0x260000; M32(0x91cadc)=0x280000; M32(0x280000)=0x280100;
 invoke(f_00516D70,0,0,NULL);
 uint32_t fe=M32(0x91cad0),q0=M32(fe),q1=two?M32(fe+4):0;
 CHECK(na==2+two && live_queues==1+two); CHECK(allocs[0].n==4+4*two);
 CHECK(q0 && (!two || q1)); CHECK(M32(q0+0x268)==0 && M32(q0+0x274)==0x82d21520);
 CHECK(M32(q0+0x278)==0x89b370 && M32(q0+0x26c)==1);
 if(two) CHECK(M32(q1+0x268)==1 && M32(q1+0x274)==0x82d21520 && M32(q1+0x26c)==1);
 M8(q0+0x280)=1; if(two) M8(q1+0x280)=1;
 uint32_t all[]={4,1},zero[]={0,1},one[]={1,1},invalid[]={2,1},none[]={~0u,1};
 invoke(f_00514A10,fe,2,all); CHECK(M32(q0+0x26c)==0); if(two) CHECK(M32(q1+0x26c)==0);
 invoke(f_00514A70,fe,2,zero); CHECK(M32(q0+0x26c)==1); if(two) CHECK(M32(q1+0x26c)==0);
 Cpu c=invoke(f_00514AE0,fe,1,all); CHECK((c.eax&255)==!two);
 if(two) { invoke(f_00514A70,fe,2,one); CHECK(M32(q1+0x26c)==1); }
 c=invoke(f_00514AE0,fe,1,all); CHECK((c.eax&255)==1);
 invoke(f_00514B20,fe,2,all); CHECK(M8(q0+0x281)==1); if(two) CHECK(M8(q1+0x281)==1);
 c=invoke(f_00514B50,fe,1,zero); CHECK((c.eax&255)==1);
 if(two) {
  c=invoke(f_00514B50,fe,1,one); CHECK((c.eax&255)==1);
  for(unsigned p=2;p<6;p++) {
   uint32_t a[]={p,1}; if(p==4) continue;
   invoke(f_00514A70,fe,2,a); invoke(f_00514A10,fe,2,a); invoke(f_00514B20,fe,2,a);
   c=invoke(f_00514B50,fe,1,a); CHECK(!c.eax);
   c=invoke(f_00514A60,fe,1,a); CHECK(!c.eax);
  }
  invoke(f_00514A70,fe,2,none); invoke(f_00514B20,fe,2,none);
 }
 // FlushActions and ResumeAllSystems must clear both FE-map columns, not a neighbour.
 M32(q0)=2; if(two) M32(q1)=3;
 for(unsigned i=0;i<16;i++) { M32(0x8f3434+28*i)=11; M32(0x8f3438+28*i)=22; M32(0x8f343c+28*i)=33; }
 invoke(f_005247A0,fe,0,NULL); CHECK(M32(q0)==0); if(two) CHECK(M32(q1)==0);
 for(unsigned i=0;i<16;i++) { CHECK(!M32(0x8f3434+28*i)); CHECK(M32(0x8f3438+28*i)==(two?0:22)); CHECK(M32(0x8f343c+28*i)==33); }
 M32(q0)=2; if(two) M32(q1)=3; invoke(f_005149E0,fe,0,NULL);
 CHECK(M32(q0)==0); if(two) CHECK(M32(q1)==0);
 uint32_t yes[]={1},no[]={0};
 M32(q0)=2; if(two) M32(q1)=3; M32(0x8f3438)=22;
 invoke(f_00525790,0x280000,1,no); CHECK(M32(q0)==2); if(two) CHECK(M32(q1)==3);
 invoke(f_00525790,0x280000,1,yes); CHECK(M32(q0)==0); CHECK(M32(0x8f3438)==(two?0:22)); if(two) CHECK(M32(q1)==0);
 // Actual package push/switch/pop bodies exercise the decoded all-queue call sites.
 uint32_t package[]={0x200000,0,0,1};
 for(unsigned mask=0;mask<(two?4u:2u);mask++) {
  M32(q0+0x26c)=mask&1; if(two) M32(q1+0x26c)=(mask>>1)&1;
  M32(q0)=2; if(two) M32(q1)=3;
  invoke(f_005257F0,0x280000,4,package); CHECK(M32(q0+0x26c)==1); if(two) CHECK(M32(q1+0x26c)==1);
  CHECK(M32(q0)==((mask&1)?2:0)); if(two) CHECK(M32(q1)==((mask&2)?3:0));
  M32(q0+0x26c)=mask&1; if(two) M32(q1+0x26c)=(mask>>1)&1;
  M32(q0)=2; if(two) M32(q1)=3;
  invoke(f_00525940,0x280000,4,package); CHECK(M32(q0+0x26c)==1); if(two) CHECK(M32(q1+0x26c)==1);
  CHECK(M32(q0)==((mask&1)?2:0)); if(two) CHECK(M32(q1)==((mask&2)?3:0));
  M32(q0+0x26c)=mask&1; if(two) M32(q1+0x26c)=(mask>>1)&1;
  package_count=2; invoke(f_005258B0,0x280000,1,no);
  unsigned all_on=mask==(two?3u:1u);
  CHECK(M32(q0+0x26c)==(all_on?0:(mask&1)));
  if(two) CHECK(M32(q1+0x26c)==(all_on?0:((mask>>1)&1)));
 }
 invoke(f_00514A70,fe,2,all); invoke(f_00516F30,0x260000,0,NULL);
 CHECK(M32(q0+0x26c)==0); if(two) CHECK(M32(q1+0x26c)==0);
 invoke(f_00514A70,fe,2,all);
 // Execute the actual PressStart body through assignment and its advance message.
 uint32_t screen=0x290000; M32(screen+0x10)=0x200000;
 M32(screen+0x2c)=0; uint32_t join[]={0xebfcda65,0,1,0};
 unsigned before=messages; invoke(f_007A3F20,screen,4,join); CHECK(messages==before+1);
 CHECK(M8(0x27000c)==(two?0:0xff));
 if(two) {
  M32(screen+0x2c)=1; before=messages;
  invoke(f_007A3F20,screen,4,join); CHECK(messages==before && M8(0x27000d)==0xff);
  ownership(1,0); CHECK(M8(0x27000d)==0xff);
  join[2]=2; invoke(f_007A3F20,screen,4,join); CHECK(messages==before+1 && M8(0x27000d)==1);
  CHECK(M32(screen+0x30)==2);
  ownership(0,1); CHECK(M8(0x27000c)==0);
  before=messages;
  for(unsigned p=0;p<5;p++) {
   join[2]=p==0?0:1u<<(p+1); invoke(f_007A3F20,screen,4,join);
   CHECK(messages==before && M8(0x27000d)==1);
  }
  // Assignment does not set required; one/all unassignment clears just its old queue(s).
  M8(q0+0x281)=M8(q1+0x281)=1;
  ownership(1,~0u); CHECK(M8(0x27000d)==0xff && M8(q1+0x281)==0 && M8(q0+0x281)==1);
  M8(q1+0x281)=1; ownership(0,4); ownership(0,~0u);
  CHECK(M8(0x27000c)==0xff && !M8(q0+0x281) && !M8(q1+0x281));
  ownership(0,0); ownership(1,1); CHECK(!M8(q0+0x281) && !M8(q1+0x281));
  ownership(2,0); ownership(~0u,0); ownership(1,2); CHECK(M8(0x27000e)==0xaa && M8(0x27000d)==1);
  // PressStart's notification retains its screen switch and enables both queues.
  M32(q0+0x26c)=1; M32(q1+0x26c)=0;
  uint32_t notification[]={0x911ab364,0,0,0}; before=transitions;
  invoke(f_007A3F20,screen,4,notification);
  CHECK(transitions==before+1 && M32(q0+0x26c)==1 && M32(q1+0x26c)==1);
  // Original port-0 handler and new port-1 handler update distinct FE columns.
  MF32(0x8933d4)=.5f; MF32(0x89096c)=1;
  for(unsigned i=0;i<16;i++) M32(0x8f3428+28*i)=0x30+i;
  M8(q0+0x280)=M8(q1+0x280)=1;
  event(q0,0x30,1); event(q1,0x31,1); invoke(f_00559400,fe,0,NULL);
  CHECK(!M32(q0) && !M32(q1)); CHECK(M32(0x8f3434)==1 && M32(0x8f3454)==1);
  CHECK(M32(0x8f3438)==0 && M32(0x8f3450)==0);
  event(q1,0x31,0); invoke(f_00559400,fe,0,NULL); CHECK(!M32(0x8f3454));
  M8(q1+0x280)=0; event(q1,0x32,1); invoke(f_00559400,fe,0,NULL);
  for(unsigned i=0;i<16;i++) CHECK(!M32(0x8f3438+28*i));
  // Connection virtual, required bytes and unplug routing, including old All=4.
  connected1=0; c=invoke(f_006287D0,dev[1],0,NULL); CHECK(!(c.eax&255));
  connected1=1; c=invoke(f_006287D0,dev[1],0,NULL); CHECK(c.eax&255);
  prompts_lost=1;
  c=invoke(f_006287D0,dev[0],0,NULL); CHECK(!(c.eax&255));
  c=invoke(f_006287D0,dev[1],0,NULL); CHECK(c.eax&255);
  prompts_lost=0;
  // Real IOModule unplug loop and ActionQueue::ReceiveAction publish the status.
  M32(0x8a7b9c+4)=0x6287d0;
  M32(0x8a7b9c+0x14)=0x628bf0;
  for(unsigned i=2;i<4;i++) { M32(dev[i])=0x2b0000; M32(0x2b0004)=0x6287d0; M32(0x2b0014)=0x628bf0; }
  M8(q0+0x281)=M8(q1+0x281)=1;
  connected1=0; invoke(f_00644E40,0x920de0,0,NULL);
  CHECK(M8(q0+0x280)==1 && M8(q1+0x280)==0);
  connected1=1; invoke(f_00644E40,0x920de0,0,NULL);
  CHECK(M8(q0+0x280)==1 && M8(q1+0x280)==1);
  invoke(f_00559400,fe,0,NULL); CHECK(!M32(q0) && !M32(q1));
  M8(q1+0x280)=0;
  M8(0x260001)=1; M32(0x925e90)=6; M8(0x270000)=2; M8(0x27012c)=4;
  c=invoke(f_00559280,fe,0,NULL); CHECK(c.eax==1 && errors[1] && !errors[0]);
  M8(q1+0x280)=1; c=invoke(f_00559280,fe,0,NULL); CHECK(c.eax==0);
  ownership(1,~0u); ownership(0,4); c=invoke(f_00559280,fe,0,NULL); CHECK(c.eax==0);
  CHECK(M8(q0+0x281) && M8(q1+0x281));
  M32(0x925e90)=0; M8(0x260001)=0; invoke(f_00559280,fe,0,NULL);
  CHECK(!M8(q0+0x281) && !M8(q1+0x281));
 }
 bounds(); snapshot(out);
 invoke(f_00524770,fe,0,NULL); CHECK(live_queues==0 && freed_queues==1+two);
 CHECK(!M32(fe)); if(two) CHECK(!M32(fe+4)); bounds();
 if(two) for(int failure=0;failure<4;failure++) {
  heap_reset(); fail_fe=failure==0; fail_queue=failure==1?0:failure==2?1:-1;
  if(failure==3) M32(0x920dec)=0;
  invoke(f_00516D70,0,0,NULL); fe=M32(0x91cad0);
  CHECK(!fe==fail_fe);
  if(fe) {
   CHECK((M32(fe)!=0)==(failure!=1)); CHECK((M32(fe+4)!=0)==(failure!=2 && failure!=3));
   invoke(f_00514A10,fe,2,all); invoke(f_00514A70,fe,2,all); invoke(f_00514B20,fe,2,all);
   invoke(f_005247A0,fe,0,NULL); invoke(f_005149E0,fe,0,NULL);
   invoke(f_0054EBC0,0,0,NULL); CHECK(live_queues==0 && !M32(0x91cad0));
  }
  bounds();
 }
}
static void device_teardown_tests(void) {
 setup(); fail_pad1=0; invoke(f_0063CBF0,0x920de0,0,NULL);
 CHECK(M32(0x9203c0)==(unsigned)(pad_count()==2?2:1));
 M32(0x8a7b9c)=0x63f9a0;
 M32(0x9203b4)=0x2c0000; M32(0x2c0000)=0x2c0100; M32(0x2c0104)=0x202;
 for(unsigned i=2;i<4;i++) { M32(dev[i])=0x2b0000; M32(0x2b0000)=0x203; }
 invoke(f_006279E0,0x920de0,0,NULL);
 CHECK(M32(0x920de4)==0 && M32(0x9203c0)==0);
 CHECK(backend_stops==1 && device_frees==(pad_count()==2?2:1) && other_frees==2);
 for(unsigned i=0;i<(unsigned)(pad_count()==2?4:3);i++) CHECK(!M32(0x920de8+4*i));
 for(unsigned p=0;p<(unsigned)(pad_count()==2?2:1);p++) {
  CHECK(M32(dev[p]-4)==0xa5a5a5a5 && M32(dev[p]+0x1858)==0xa5a5a5a5);
 }
 invoke(f_006279E0,0x920de0,0,NULL); CHECK(backend_stops==1);
}
int main(int argc,char **argv) {
 if(argc!=2) return 2;
 FILE *out=fopen(argv[1],"wb"); if(!out) return 2;
 G=calloc(1,0x1000000); if(!G) return 2;
 for(fail_pad1=0;fail_pad1<=1;fail_pad1++) {
  setup(); Cpu c=cpu(0x920de0); f_0063CBF0(&c); abi(&c,0);
  unsigned two=pad_count()==2;
  CHECK(M32(0x920de4)==3+two); CHECK(shared_init==1);
  CHECK(factory_calls[0]==1 && factory_calls[1]==two);
  CHECK(factory_calls[2]==1 && factory_calls[3]==1);
  CHECK(M32(0x920de8)==dev[0]);
  CHECK(M32(0x920de8+4*(1+two))==dev[2]); CHECK(M32(0x920de8+4*(2+two))==dev[3]);
  CHECK(M32(0x920e28)==(two?(fail_pad1?13:15):7));
  for(unsigned i=3+two;i<16;i++) CHECK(M32(0x920de8+4*i)==0xcccccccc);
  if(two) {
   CHECK(M32(0x920dec)==(fail_pad1?0:dev[1]));
   if(!fail_pad1) {
    CHECK(M32(dev[1]+0x1838)==76);
    for(unsigned i=0;i<76;i++) {
     uint32_t r=dev[1]+0x34+16*i; CHECK(M32(r)==1);
     c=cpu(0); M32(c.esp+4)=M32(0x91f418+52*i); f_005CC240(&c);
     CHECK(M32(r+4)==c.eax); CHECK(M32(r+8)==dev[1]+0x1034+4*i);
     CHECK(M32(r+12)==dev[1]+0x1434+4*i);
    }
    CHECK(M32(dev[1]-4)==0xa5a5a5a5 && M32(dev[1]+0x1858)==0xa5a5a5a5);
   }
  }
  // A different caller must not create another logical pad.
  unsigned before=factory_calls[1]; c=cpu(0); c.esi=0x920de0; M32(c.esp)=0x123456;
  f_00628D30(&c); CHECK(factory_calls[1]==before); CHECK(c.esi==0x920de0 && c.esp==0x800004);
 }
 fe_tests(out); device_teardown_tests(); fclose(out);
 printf("padguest_join NFS_PADS=%s: %d checks, %d failures\n",getenv("NFS_PADS")?:"unset",checks,failures);
 free(G); return failures!=0;
}
'''


def run(cmd, **kwargs):
    r = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    if r.returncode:
        raise RuntimeError(r.stdout[-6000:])
    return r.stdout


def main():
    hook = (ROOT/'runtime/padguest.c').read_text()
    pad = re.search(r'int pad_count\(void\) \{.*?\n\}', (ROOT/'runtime/user32.m').read_text(), re.S).group()
    groups = (
        ('speed_31.c', ('0063CBF0','00633D80','00633D90','00633DC0','00633D10','00634D00','0063F9A0')),
        ('speed_17.c', ('00516D70','00514960','005149E0','00514A10','00514A60','00514A70',
                         '00514AE0','00514B20','00514B50','00514B7C','00514B81',
                         '00516F30','00516E10','00516C90')),
        ('speed_18.c', ('00524770','005247A0','00525790','005257F0','005258B0','00525940')),
        ('speed_19.c', ('00559280','00559400')),
        ('speed_20.c', ('0056DBA0',)),
        ('speed_21.c', ('00572FF0','00573020')),
        ('speed_30.c', ('00626D70','00626DD0','006287D0','00628F10','006279E0')),
        ('speed_32.c', ('00648DB0','00644060','00642D70','006466B0','00644E40','006440B0')),
        ('speed_51.c', ('007A3F20',)),
    )
    hooked = set(re.findall(r'^void f_([0-9A-F]{8})\(Cpu \*c\) \{',hook,re.M))
    bodies = '\n'.join(translated(file,name,name in hooked or name == '006287D0')
                       for file,names in groups for name in names)
    connection = re.search(r'void f_006287D0\(Cpu \*c\) \{.*?\n\}',
                           (ROOT/'runtime/fe_console.c').read_text(),re.S).group()
    bodies += '\n' + connection
    def passthrough(source, name):
        pat = r'void f_' + name + r'\(Cpu \*c\) \{.*?\n\}'
        changed, n = re.subn(pat, 'void f_'+name+'(Cpu *c) { f_'+name+'_orig(c); }',source, count=1, flags=re.S)
        assert n == 1
        return changed
    baseline = hook
    for name in hooked:
        baseline = passthrough(baseline, name)
    mutants = {
        'setter-ret-8': passthrough(hook,'0056DBA0'),
        'all-enabled-loop-one': passthrough(hook,'00626DD0'),
        'joy-enable-all-one': hook.replace('end = port == 4 ? 2 : port + 1;',
                                           'end = port == 4 ? 1 : port + 1;',1),
        'scalar-init-skipped': passthrough(hook,'0063F850'),
        'fe-allocation-four': hook.replace('uint32_t a[] = {8};','uint32_t a[] = {4};',1),
        'port-one-events-skipped': passthrough(hook,'00559400'),
        'join-guard-skipped': passthrough(hook,'007A3F20'),
        'connection-always-true': re.sub(r'int padguest_connection\(Cpu \*c\) \{.*?\n\}',
                                         'int padguest_connection(Cpu *c) { return 0; }',hook, count=1,flags=re.S),
    }
    repair = hook.replace('case 0x00525878: case 0x00525910: case 0x005259C8:',
                          'case 0x00525878: case 0x00525910: case 0x005259C8: case 0x00525894: case 0x005259E4:')
    repair = re.sub(r'    case 0x00525894: case 0x005259E4:.*?pg_enable\(c, fe, 1, 1, 1\);', '',repair, count=1,flags=re.S)
    repair = repair.replace('case 0x00525930: case 0x00516F4A:',
                             'case 0x005258A8: case 0x005259F8: case 0x00525930: case 0x00516F4A:')
    mutants['repair-flushes-enabled-queue'] = repair
    appended = passthrough(hook,'00628D30') + r'''
void f_0063CBF0_orig(Cpu *c);
void f_0063CBF0(Cpu *c) {
 uint32_t io=c->ecx; f_0063CBF0_orig(c);
 if(pad_count()!=2) return;
 uint32_t a[]={1}, dev=pg_call(c,f_0063CD10,0,1,a), n=M32(io+4);
 if(dev) {
  Cpu q=*c; q.ecx=dev; q.esp-=64; M32(q.esp)=0; dispatch(&q,M32(M32(dev)+12));
  M32(io+0x48)|=1u<<n;
 }
 M32(io+8+4*n)=dev; M32(io+4)=n+1;
}
'''
    mutants['pad-appended-after-mouse'] = appended
    assert all(source != hook for source in mutants.values())
    with tempfile.TemporaryDirectory(prefix='padguest-join-') as tmp:
        tmp = Path(tmp)
        def compile_one(name, source):
            path=tmp/(name+'.c'); exe=tmp/name
            compiled_bodies = bodies
            if name == 'pad-appended-after-mouse':
                compiled_bodies = compiled_bodies.replace('void f_0063CBF0(', 'void f_0063CBF0_orig(',1)
            path.write_text(PRE+FIXTURE+pad+compiled_bodies+source+MAIN)
            run(['clang','-std=c11','-g','-w','-DRECOMP_ABICHECK','-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all','-I',str(ROOT/'recomp'),'-I',str(ROOT/'recomp/out'),
                 '-I',str(ROOT/'runtime'),str(path),'-o',str(exe)])
            return exe
        exe=compile_one('good',hook)
        old=compile_one('original',baseline)
        for mode in (None,'1','invalid','2'):
            env=dict(os.environ); env.pop('NFS_PADS',None); env.pop('ASAN_OPTIONS',None)
            if mode is not None: env['NFS_PADS']=mode
            print(run([str(exe),str(tmp/'sample.bin')],env=env).strip())
            if mode != '2':
                run([str(old),str(tmp/'original.bin')],env=env)
                assert (tmp/'sample.bin').read_bytes() == (tmp/'original.bin').read_bytes()
                print(f'  original {mode or "unset"}: {(tmp/"sample.bin").stat().st_size:,} guest bytes identical')
        for name,source in mutants.items():
            broken=compile_one(name,source)
            result=subprocess.run([str(broken),str(tmp/'mutant.bin')],env=dict(os.environ,NFS_PADS='2'),
                                  text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            assert result.returncode == 1 and 'checks,' in result.stdout, (name,result.stdout[-2000:])
            print(f'  rejected {name}: {result.stdout.splitlines()[-1]}')
    print('padguest_join_test: PASS')


if __name__ == '__main__':
    main()
