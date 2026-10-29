#!/usr/bin/env python3
"""Split world body (splitworld.c): the actual hook against a recording fake guest.
ASan/UBSan, 1280x721: 159 checks / 0 failures (including disabled-map cases).
Split off: only the translated body, no guest call, 16 MiB memory + CPU otherwise identical.
Mutants: P2 pass skipped 26 failures; P1 rectangle reused for P2 18; clear between passes 4.
Checks: preparation once (ePreDisplay, culling, 006D6CF0, 00769360), one whole-target clear
before any world call, sky/scenery/models/clans/vehicles for eViews 1 then 2 with that view's
descriptor id/target bound and its rectangle as viewport and scissor at every world call,
no Present/post/capture bodies, sequential shadow maps and per-source effects dispatch, scissor restored, ret + ABI.
Capture bodies have separate actual-hook coverage in splitcapture_test.py; this fixture stubs their scope.
No game/GPU. Autodiscovered by runtime/offline_tests.sh.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdarg.h>
#include <stdlib.h>
uint8_t *G;
int diag_on = 1;
uint32_t galloc(uint32_t n){static uint32_t a=0x700000;uint32_t r=a;a=(a+n+15)&~15u;return r;}
uint32_t split_capture_player(uint32_t original){return original;}
void split_post_mode(int active){(void)active;}
void split_post_view(Cpu*c,uint32_t v,const uint32_t r[4]){(void)c;(void)v;(void)r;}
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if(failures<20) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
#define MEMSIZE (16*1024*1024)
#define DEVMAGIC 0xD0000000u
#define SHMAGIC 0xE0000000u
#define FXMAGIC 0xE1000000u
static int split_on, orig_calls;
static uint32_t rects[2][4];
static uint64_t draws;
static uint32_t vp[6], sc[4], rs174, bound_rt, bound_ds, presents, clears, clear_flags, clear_vp_full;
static uint32_t calls[0x1000], ncalls;      // guest function addresses in order
static uint32_t world_view[16], world_desc[16], world_ok[16], nworld;
static uint32_t maps, map_owner, expect_maps;
#define DESC1 0x400000u
#define DESC2 0x40001Cu
#define SURF 0x600000u
#define DEPTH 0x601000u
int split_view_rect(uint32_t view, uint32_t r[4]) {
 if (!split_on || (view != 0x919650u && view != 0x9196C0u)) return 0;
 if (r) memcpy(r, rects[view == 0x9196C0u], 16); return 1;
}
uint64_t d3d9_draw_count(void) { return draws; }
void diag_event(const char *fmt,...) { (void)fmt; }
void f_006DE300_orig(Cpu *c) { ++orig_calls; M32(0x123456)=0x55; c->esp += 4; }
// Capture implementation has its own actual-hook/translated-operand harness (splitcapture_test.py).
void split_capture_begin(Cpu *c,uint32_t v,const uint32_t r[4],uint32_t cu) { (void)c;(void)v;(void)r;(void)cu; }
void split_capture_end(Cpu *c) { (void)c; }
void split_capture_mode(Cpu *c,int active) { (void)c;(void)active; }
static int count(uint32_t fn) { int n=0; for(uint32_t i=0;i<ncalls;i++) n+=calls[i]==fn; return n; }
static int first(uint32_t fn) { for(uint32_t i=0;i<ncalls;i++) if(calls[i]==fn) return (int)i; return -1; }
static void world_call(uint32_t view) {
 // At every world submission: the view's descriptor bound, its rectangle as viewport + scissor.
 uint32_t i = view == 0x9196C0u, d = M32(0x982A20);
 const uint32_t *r = rects[i];
 int ok = d == M32(view+0x60) && M32(d+4) == i+1 && bound_rt == M32(d+8) && bound_ds == M32(d+12)
   && vp[0]==r[0] && vp[1]==r[1] && vp[2]==r[2] && vp[3]==r[3] && rs174==1
   && sc[0]==r[0] && sc[1]==r[1] && sc[2]==r[0]+r[2] && sc[3]==r[1]+r[3];
 CHECK(expect_maps ? map_owner==view : map_owner==0); // P1 receivers complete before P2 overwrite
 if (nworld < 16) { world_view[nworld]=view; world_desc[nworld]=d; world_ok[nworld]=ok; ++nworld; }
 draws += 10 + i;
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
 if (ncalls < 0x1000) calls[ncalls++] = fn;
 c->eax = 0;
 if (fn >= DEVMAGIC && fn < DEVMAGIC + 0x200) {
  switch (fn - DEVMAGIC) {
  case 0x94: bound_rt = a[2]; vp[0]=vp[1]=0; vp[2]=1280; vp[3]=721; break;
  case 0x9C: bound_ds = a[1]; break;
  case 0xBC: memcpy(vp, G+a[1], 24); break;
  case 0xC0: memcpy(G+a[1], vp, 24); break;
  case 0x12C: memcpy(sc, G+a[1], 16); break;
  case 0x130: memcpy(G+a[1], sc, 16); break;
  case 0xE4: if (a[1]==174) rs174=a[2]; break;
  case 0xE8: M32(a[2]) = a[1]==174 ? rs174 : 0; break;
  case 0xAC: ++clears; clear_flags=a[3]; clear_vp_full = vp[0]==0&&vp[1]==0&&vp[2]==1280&&vp[3]==721&&!rs174&&!a[1]; break;
  case 0x44: ++presents; break;
  }
  (void)n; return 0;
 }
 switch (fn) {
 case 0x006E54E0u:
  CHECK(n==2 && a[0]==(maps?0x9196C0u:0x919650u)); CHECK(rs174==0);
  CHECK(nworld==(maps?6:0)); map_owner=a[0]; ++maps;
  bound_rt=0xBAD; bound_ds=0xBAD; vp[0]=vp[1]=0; vp[2]=vp[3]=2048; break;
 case 0x006DE210u: case 0x00723FA0u: case 0x007538D0u: case 0x007268C0u: case 0x00750B10u: world_call(a[0]); break;
 case 0x0073CFE0u: c->eax = 0; break;
 }
 return c->eax;
}
void dispatch(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"unexpected dispatch %x\n",t); exit(2); }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"bad jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t f,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",f); exit(2);
}
'''

POST = r'''
static Cpu cpu(void) { Cpu c; memset(&c,0,sizeof c); c.esp=0x800000; c.ebx=7; c.esi=8; c.edi=9; c.ebp=10; return c; }
static void setup(void) {
 memset(G,0,MEMSIZE);
 M32(0x982BDC)=0x500000; M32(0x500000)=0x501000;
 for (uint32_t o=0;o<0x200;o+=4) M32(0x501000+o)=DEVMAGIC+o;
 M32(0x510000)=0x511000; for (uint32_t o=0;o<0x20;o+=4) M32(0x511000+o)=SHMAGIC+o;
 M32(0x510048)=0x512000; M32(0x51004C)=0x5100F0; M32(0x512000)=0x513000;
 for (uint32_t o=0;o<0x200;o+=4) M32(0x513000+o)=FXMAGIC+o;
 M32(0x93DE78)=0x510000; M32(0x93DEBC)=0x510000;
 M32(0x93DEC4)=0x520000; M32(0x520048)=0x512000; M32(0x520044)=0x530000; M32(0x53018C)=0x77;
 uint32_t d1[7]={0,1,SURF,DEPTH,1,1280,360}, d2[7]={0,2,SURF,DEPTH,1,1280,361};
 memcpy(G+DESC1,d1,28); memcpy(G+DESC2,d2,28);
 for (unsigned i=0;i<2;i++) { uint32_t v=0x919650+i*0x70; M32(v+4)=i+1; M32(v+8)=1; M32(v+0x60)=i?DESC2:DESC1; M32(v+0x68)=0x540000+i*0x1000; M32(v+0x40)=0x550000+i*0x1000; }
 uint32_t r0[4]={0,0,1280,360}, r1[4]={0,360,1280,361}; memcpy(rects[0],r0,16); memcpy(rects[1],r1,16);
 ncalls=nworld=presents=clears=0; rs174=0; sc[0]=1; sc[1]=2; sc[2]=3; sc[3]=4; orig_calls=0;
 maps=map_owner=0; expect_maps=1; M32(0x901830)=2; M32(0x9017C4)=1; MF32(0x8FAE68)=1; MF32(0x8FAE50)=0.01f;
}
int main(void) {
 G = calloc(1, MEMSIZE);
 // Split off: the translated body only, nothing else touched.
 setup(); split_on=0;
 uint8_t *before = malloc(MEMSIZE); memcpy(before, G, MEMSIZE);
 Cpu c=cpu(); c.esp-=4; f_006DE300(&c);
 CHECK(orig_calls==1); CHECK(ncalls==0); CHECK(c.esp==0x800000);
 M32(0x123456)=0; CHECK(memcmp(before,G,MEMSIZE)==0);
 CHECK(c.ebx==7 && c.esi==8 && c.edi==9 && c.ebp==10); CHECK(c.eax==0 && c.ecx==0);
 free(before);
 // Split on.
 setup(); split_on=1;
 c=cpu(); c.esp-=4; f_006DE300(&c);
 CHECK(orig_calls==0); CHECK(c.esp==0x800000); CHECK(c.ebx==7 && c.esi==8 && c.edi==9 && c.ebp==10);
 CHECK(count(0x00504C50u)==1); CHECK(count(0x006BFDD0u)==1); CHECK(count(0x006D6CF0u)==1); CHECK(count(0x00769360u)==1);
 CHECK(count(0x006C83D0u)==2); CHECK(count(0x0073CFE0u)==1);
 CHECK(count(0x006E54E0u)==2); CHECK(count(0x00724230u)==2); CHECK(maps==2);
 CHECK(count(DEVMAGIC+0x44)==0);
 CHECK(clears==1); CHECK(clear_flags==7); CHECK(clear_vp_full);
 int cl=first(DEVMAGIC+0xAC); CHECK(cl>first(0x006BFDD0u)); CHECK(cl<first(0x006DE210u));
 CHECK(count(0x007538D0u)==2); CHECK(count(0x00723FA0u)==2); CHECK(count(0x00750B10u)==2); CHECK(count(0x007268C0u)==2);
 CHECK(count(0x006DE210u)==4); CHECK(count(0x006D04E0u)==2); CHECK(count(0x0041AD20u)==1);
 CHECK(nworld==12);
 for (unsigned k=0;k<12 && k<nworld;k++) {
  uint32_t want = k<6 ? 0x919650u : 0x9196C0u;
  CHECK(world_view[k]==want); CHECK(world_desc[k]==(k<6?DESC1:DESC2)); CHECK(world_ok[k]);
 }
 static const uint32_t never[] = {0x006D53D0u,0x006DC040u,0x006DBEC0u,0x006DBFE0u,0x006D4620u,0x006D3850u,0x006D4100u,
  0x006D4960u,0x006DB310u,0x006D1A40u,0x006DAE20u,0x00505E80u,0x006BD3B0u};
 for (unsigned k=0;k<sizeof never/4;k++) CHECK(count(never[k])==0);
 CHECK(rs174==0); CHECK(sc[0]==1 && sc[1]==2 && sc[2]==3 && sc[3]==4);
 CHECK(bound_rt==SURF && M32(0x9885B8)==1);
 CHECK(draws==10*6+11*6);
 // Preserve both retail map conditions, and an initially enabled scissor.
 for(unsigned i=0;i<2;i++) {
  setup(); split_on=1; expect_maps=0; rs174=1;
  if(i) MF32(0x8FAE68)=0; else M32(0x901830)=0;
  c=cpu(); c.esp-=4; f_006DE300(&c);
  CHECK(maps==0 && count(0x006E54E0u)==0); CHECK(nworld==12);
  for(unsigned k=0;k<12;k++) CHECK(world_ok[k]);
  CHECK(clears==1 && rs174==1); CHECK(sc[0]==1 && sc[1]==2 && sc[2]==3 && sc[3]==4);
 }
 printf("splitworld: %d checks, %d failures\n",checks,failures); free(G); return failures ? 1:0;
}
'''


def main():
    src = (ROOT / 'runtime/splitworld.c').read_text()
    body = '\n'.join(l for l in src.splitlines() if not l.startswith('#include'))
    source = PRE + body + POST
    mutants = {
        'P2 pass skipped': ('for (unsigned i = 0; i < 2; ++i) {', 'for (unsigned i = 0; i < 1; ++i) {'),
        'P1 rectangle reused': ('world_pass(c, view, r[i],', 'world_pass(c, view, r[0],'),
        'clear between passes': ('world_pass(c, view, r[i], cull, scratch, i + 1);',
                                 'world_pass(c, view, r[i], cull, scratch, i + 1); devcall(c, 0xAC, 7, cl);'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-splitworld-') as tmp:
        tmp = Path(tmp)
        def run(label, text):
            path = tmp / 'test.c'; binary = tmp / 'test'
            path.write_text(text)
            r = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-DRECOMP_ABICHECK',
                '-I'+str(ROOT/'recomp/out'), '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'runtime'), str(path), '-o', str(binary)],
                capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(r.stderr[-6000:])
            r = subprocess.run([str(binary)], capture_output=True, text=True)
            lines = r.stdout.strip().splitlines()
            print(label, lines[-1] if lines else '(no output)')
            if r.returncode and label == 'baseline': print(r.stdout[:2500], r.stderr[-3000:])
            return r
        if run('baseline', source).returncode: raise SystemExit(1)
        for label, (old, new) in mutants.items():
            assert source.count(old) == 1, (label, source.count(old))
            r = run('mutant ' + label, source.replace(old, new))
            if r.returncode != 1 or 'failures' not in r.stdout:
                raise SystemExit('mutant not rejected: ' + label)
    print('SPLIT WORLD PASS (3/3 mutants rejected)')


if __name__ == '__main__':
    main()
