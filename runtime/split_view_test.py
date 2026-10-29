#!/usr/bin/env python3
"""Split-view contract: actual hooks + translated mode/table/matrix/frustum code.
ASan/UBSan: pads=2 1,271 checks / 0 failures; unset/1/invalid each 129 / 0.
P2-disabled, P1-matrix-reused, bottom-Y-zero mutants fail 5/191/10 checks.
Each single-pad mode: eight full 16 MiB memory snapshots + CPU identical
(134,217,728 memory bytes), widescreen off/on and retail modes 0/1/2/3.
No game/GPU/Unicorn. Autodiscovered by runtime/offline_tests.sh.
Math/import sinks: sin/cos, ftol, D3DX multiply; allocation failure is injected.
The matrix copy and pixel-size helper execute translated C. Single-pad cases
compare all 16 MiB plus CPU with the original mode/table/projection path.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def translated(file, name, original=False):
    s = (ROOT / 'recomp/out' / file).read_text()
    b = re.search(r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', s, re.S)[0]
    return re.sub(r'void f_' + name + r'(?:_orig)?\(',
                  'void f_' + name + ('_orig' if original else '') + '(', b, count=1)


def function(source, name):
    return re.search(r'(?:static )?void ' + name + r'\([^\n]*\).*?\n\}', source, re.S)[0]


PRE = r'''
#include "funcs.h"
#include "host.h"
#include <stdarg.h>
#include <stdlib.h>
uint8_t *G;
int diag_on;
uint32_t split_capture_source(void) { return 0; } // capture projection is covered by splitcapture_test.py
void split_capture_mode(Cpu *c,int active) { (void)c;(void)active; }
static int checks, failures, alloc_fail, allocations;
static uint32_t refl_w, refl_h;
static int refl_x360;
#define RT_REFLECTION 0x0093DAD4u
#define DB 0x300000u
#define PLAT1 0x310000u
#define PLAT2 0x311000u
#define CAM1 0x320000u
#define CAM2 0x321000u
#define MEMSIZE (16*1024*1024)
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if(failures<20) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
static int closef(float a,float b) { return isfinite(a) && fabsf(a-b)<2e-4f*fmaxf(1,fabsf(b)); }
int pad_count(void) { const char *p=getenv("NFS_PADS"); return p && !strcmp(p,"2") ? 2:1; }
uint32_t gmalloc_try(uint32_t n) { CHECK(n==56); ++allocations; return alloc_fail ? 0 : 0x400000; }
void diag_event(const char *fmt,...) { (void)fmt; }
void mirror_projection(uint32_t view,uint32_t proj) { (void)view; (void)proj; }
void dispatch(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"unexpected dispatch %x\n",t); exit(2); }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"bad jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t f,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",f); exit(2);
}
void f_00595D40(Cpu *c) { c->eax=0; c->esp+=8; }
void f_00738840(Cpu *c) { c->eax=0; c->esp+=4; }
void f_006C86B0(Cpu *c) { c->eax=512; c->esp+=4; }
void f_006C86C0(Cpu *c) { c->eax=512; c->esp+=4; }
void f_007C4B80(Cpu *c) { c->eax=(uint32_t)(int32_t)trunc(ST(0)); x87_pop(c); c->esp+=4; }
void f_0045DB30(Cpu *c) { x87_push(c,sin((uint16_t)arg(c,0)*6.283185307179586/65536)); c->esp+=4; }
void f_0045DBF0(Cpu *c) { x87_push(c,cos((uint16_t)arg(c,0)*6.283185307179586/65536)); c->esp+=4; }
static void mul(uint32_t dst,uint32_t a,uint32_t b) {
 float out[16];
 for(unsigned r=0;r<4;r++) for(unsigned col=0;col<4;col++) {
  out[r*4+col]=0;
  for(unsigned k=0;k<4;k++) out[r*4+col]+=MF32(a+4*(r*4+k))*MF32(b+4*(k*4+col));
 }
 memcpy(G+dst,out,64);
}
void f_007E7D5E(Cpu *c) {
 uint32_t dst=arg(c,0), a=arg(c,1), b=arg(c,2);
 widescreen_projection(c,b);
 mul(dst,a,b); c->eax=dst; c->esp+=16;
}
'''

POST = r'''
static Cpu cpu(void) {
 return (Cpu){.esp=0x800000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f};
}
static Cpu call(void (*fn)(Cpu *)) { Cpu c=cpu(); fn(&c); CHECK(c.esp==0x800004); return c; }
static void identity(uint32_t m) { memset(G+m,0,64); for(unsigned k=0;k<4;k++) MF32(m+20*k)=1; }
static void setup(unsigned w,unsigned h) {
 memset(G,0,MEMSIZE); split_views_reset();
 M32(FE_DATABASE)=DB; M8(DB)=2; M32(DB+0x12c)=0x404;
 M32(GAMEFLOW_STATE)=6; M32(WINDOW_WIDTH)=w; M32(WINDOW_HEIGHT)=h;
 M32(0x982B3C)=3; M32(0x982B40)=3;
 M32(0x982A28)=0x1234; M32(0x982A2C)=0x5678;
 M32(SPLIT_P1+0x60)=SPLIT_OUTPUT; M32(SPLIT_P1+8)=1;
 M32(SPLIT_P2+0x60)=0; M32(SPLIT_P2+8)=0;
 M32(0x919790)=RT_REFLECTION; M32(0x919800)=RT_REFLECTION;
 MF32(0x8AF994)=1.15f; MF32(0x8933D4)=.5f; MF32(0x89096C)=1;
 MF32(0x890968)=0; MF32(0x890BF8)=0; MF64(0x8942E0)=0;
 for(unsigned i=0;i<2;i++) {
  uint32_t v=SPLIT_P1+i*0x70, cam=CAM1+i*0x1000, plat=PLAT1+i*0x1000;
  M32(v+4)=i+1; M32(v+0x40)=cam; M32(v+0x44)=plat;
  identity(cam); MF32(cam+48)=i ? -11:3; MF32(cam+52)=i ? 4:-2;
  MF32(cam+0xBC)=1; MF32(cam+0xC0)=500;
  M16(cam+0xC4)=i ? 21846:16384;
 }
 call(f_006BD0D0); wide=1;
}
static Cpu matrix(unsigned i, int original) {
 uint32_t v=SPLIT_P1+i*0x70;
 Cpu c=cpu(); c.ecx=M32(v+0x44);
 M32(c.esp+4)=v; MF32(c.esp+8)=0; MF32(c.esp+12)=500;
 M32(c.esp+16)=1; M32(c.esp+20)=1;
 if(original) { cur_view=v; f_006CF400_orig(&c); cur_view=0; } else f_006CF400(&c);
 CHECK(c.esp==0x800018); CHECK(c.ebx==7 && c.esi==8 && c.edi==9 && c.ebp==10);
 CHECK(c.top==0); return c;
}
static void check_matrix(unsigned i,int hor) {
 uint32_t v=SPLIT_P1+i*0x70, p=M32(v+0x44), cam=M32(v+0x40), d=M32(v+0x60);
 unsigned w=M32(d+20), h=M32(d+24);
 float half=(float)((M16(cam+0xc4)/2)*6.283185307179586/65536);
 float raw=1/tanf(half), aspect=(float)w/h;
 float mx=hor ? raw*(4.0f/3.0f)/aspect : raw;
 unsigned va=(unsigned)(M16(cam+0xc4)*.5f*((float)h/w*1.15f));
 float my=hor ? -1/tanf(atanf(1/raw)*.75f*1.15f) : -1/tanf(va*6.283185307179586/65536);
 CHECK(closef(MF32(p+64),mx)); CHECK(closef(MF32(p+84),my));
 CHECK(closef(MF32(p+104),500.0f/499)); CHECK(closef(MF32(p+120),-500.0f/499));
 // Independent matrix product, using this player's camera and expected H/W.
 float proj[16]={0}, vp[16]={0}; proj[0]=mx; proj[5]=my; proj[10]=500.0f/499; proj[11]=1; proj[14]=-500.0f/499;
 for(unsigned r=0;r<4;r++) for(unsigned col=0;col<4;col++) {
  for(unsigned k=0;k<4;k++) vp[r*4+col]+=MF32(cam+4*(r*4+k))*proj[k*4+col];
  CHECK(closef(MF32(p+128+4*(r*4+col)),vp[r*4+col]));
 }
 for(unsigned plane=0;plane<6;plane++) {
  float f[4]; unsigned axis=plane/2; float sign=(plane&1) ? -1:1;
  for(unsigned r=0;r<4;r++) f[r]=(plane==4 ? 0:vp[r*4+3])+sign*vp[r*4+axis];
  float len=sqrtf(f[0]*f[0]+f[1]*f[1]+f[2]*f[2]);
  for(unsigned r=0;r<4;r++) CHECK(closef(MF32(p+0x140+16*plane+4*r),f[r]/len));
 }
 CHECK(closef(MF32(v+12),((float)(M32(WINDOW_WIDTH)/2)/tanf(half))*(hor ? (4.0f/3.0f)/aspect:1)));
 CHECK(closef(MF32(p+0xe8),MF32(p+0x68)+MF32(0x8F9210)));
}
static void layout(unsigned w,unsigned h) {
 uint32_t r[2][4], table[7]; memcpy(table,G+SPLIT_OUTPUT,28);
 call(f_006CFAD0);
 for(unsigned i=0;i<2;i++) {
  uint32_t v=SPLIT_P1+i*0x70, d=M32(v+0x60);
  CHECK(split_view_rect(v,r[i])); CHECK(M32(v+8)==1);
  CHECK(d!=SPLIT_OUTPUT && d!=M32(SPLIT_P1+(1-i)*0x70+0x60));
  CHECK(M32(d)==0 && M32(d+4)==i+1 && M32(d+16)==1);
  CHECK(M32(d+8)==0x1234 && M32(d+12)==0x5678);
  CHECK(M32(d+20)==w && M32(d+24)==(i ? h-h/2:h/2));
  CHECK(r[i][0]==0 && r[i][2]==w && r[i][3]==M32(d+24));
 }
 CHECK(r[0][1]==0 && r[1][1]==h/2);
 CHECK(r[0][1]+r[0][3]==r[1][1] && r[1][1]+r[1][3]==h);
 CHECK(!memcmp(table,G+SPLIT_OUTPUT,28)); CHECK(!split_view_rect(0x9197a0,NULL));
 for(int hor=0;hor<2;hor++) { wide=hor; for(unsigned i=0;i<2;i++) { matrix(i,0); check_matrix(i,hor); } }
 CHECK(memcmp(G+PLAT1,G+PLAT2,0x1a0)!=0);
 CHECK(!memcmp(table,G+SPLIT_OUTPUT,28));
}
static void single_memory(void) {
 uint8_t *before=malloc(MEMSIZE), *expect=malloc(MEMSIZE);
 for(unsigned hor=0;hor<2;hor++) for(unsigned mode=0;mode<4;mode++) {
  setup(1280,721); M32(0x982B3C)=mode; M32(0x982B40)=mode;
  if(pad_count()==2) M8(DB)=1;
  wide=hor; memcpy(before,G,MEMSIZE);
  Cpu a=call(f_006BD0D0_orig), b=call(f_006CFAD0_orig), c=matrix(0,1);
  memcpy(expect,G,MEMSIZE); memcpy(G,before,MEMSIZE);
  Cpu aa=call(f_006BD0D0), bb=call(f_006CFAD0), cc=matrix(0,0);
  CHECK(!memcmp(&a,&aa,sizeof a)); CHECK(!memcmp(&b,&bb,sizeof b)); CHECK(!memcmp(&c,&cc,sizeof c));
  CHECK(!memcmp(expect,G,MEMSIZE)); CHECK(!split_view_rect(SPLIT_P1,NULL));
 }
 free(before); free(expect);
}
int main(void) {
 G=calloc(1,MEMSIZE); CHECK(G!=NULL);
 single_memory();
 if(pad_count()==2) {
  setup(1280,721); alloc_fail=1; call(f_006CFAD0);
  CHECK(!split_active && !split_desc); CHECK(M32(SPLIT_P1+0x60)==SPLIT_OUTPUT && M32(SPLIT_P2+0x60)==0);
  CHECK(M32(SPLIT_P1+8)==1 && M32(SPLIT_P2+8)==0); alloc_fail=0;
  layout(1280,721); int n=allocations; layout(1280,721); CHECK(allocations==n);
  M8(DB+0x12c)=0; call(f_006CFAD0); CHECK(!split_active);
  CHECK(M32(SPLIT_P1+0x60)==SPLIT_OUTPUT && M32(SPLIT_P2+0x60)==0);
  CHECK(M32(SPLIT_P1+8)==1 && M32(SPLIT_P2+8)==0);
  M8(DB+0x12c)=4; layout(1280,721);
  // Actual table wrapper restores bindings and invalidates stale resources.
  M32(WINDOW_WIDTH)=1920; M32(WINDOW_HEIGHT)=1080;
  M32(0x982A28)=0xaaaa; M32(0x982A2C)=0xbbbb;
  call(f_006BD0D0); CHECK(!split_active && !split_view_rect(SPLIT_P2,NULL));
  CHECK(M32(SPLIT_P1+0x60)==SPLIT_OUTPUT && M32(SPLIT_P2+0x60)==0);
  for(unsigned i=0;i<14;i++) CHECK(M32(split_desc+4*i)==0);
  call(f_006CFAD0);
  for(unsigned i=0;i<2;i++) { uint32_t d=M32(SPLIT_P1+i*0x70+0x60); CHECK(M32(d+8)==0xaaaa && M32(d+12)==0xbbbb); CHECK(M32(d+20)==1920 && M32(d+24)==540); }
  M32(GAMEFLOW_STATE)=3; call(f_006CFAD0); CHECK(!split_active);
  setup(1920,1080); layout(1920,1080);
  setup(641,3); layout(641,3);
  setup(640,1); call(f_006CFAD0); CHECK(!split_active);
  setup(640,480); M32(SPLIT_OUTPUT+12)=0; call(f_006CFAD0); CHECK(!split_active);
  setup(640,480); M32(FE_DATABASE)=0; call(f_006CFAD0); CHECK(!split_active);
  // Compose with the existing X360 reflection resize on entry and exit.
  setup(1280,721); refl_w=640; refl_h=360; call(f_006BD0D0);
  CHECK(M32(RT_REFLECTION+20)==640 && M32(RT_REFLECTION+24)==360);
  call(f_006CFAD0); CHECK(split_active);
  CHECK(M32(RT_REFLECTION+20)==640 && M32(RT_REFLECTION+24)==360);
  M8(DB)=1; M32(0x982B3C)=1; call(f_006CFAD0); CHECK(!split_active);
  CHECK(M32(RT_REFLECTION+20)==640 && M32(RT_REFLECTION+24)==360);
  CHECK(M32(SPLIT_OUTPUT+20)==1280 && M32(SPLIT_OUTPUT+24)==721);
 }
 printf("split_view: %d checks, %d failures; pads=%d\n",checks,failures,pad_count()); free(G); return failures ? 1:0;
}
'''


def main():
    wide = (ROOT / 'runtime/widescreen.c').read_text()
    # Keep includes/macros, and the actual contract + projection hooks; omit display/UI code.
    macros = '\n'.join(line for line in wide.splitlines() if line.startswith('#'))
    contract = wide[wide.index('// ---- Hor+ ----'):wide.index('// ---- 2D ----')]
    x360 = (ROOT / 'runtime/x360.m').read_text()
    wrappers = '\n'.join(function(x360, n) for n in ('refl_entry', 'f_006BD0D0', 'f_006CFAD0'))
    bodies = '\n'.join(translated(f, n, orig) for f, n, orig in [
        ('speed_40.c', '006CF400', True), ('speed_40.c', '006CFAD0', True),
        ('speed_39.c', '006BD0D0', True), ('speed_04.c', '00444FE0', False),
        ('speed_39.c', '006BE9F0', False)])
    source = PRE + macros + '\nstatic int wide;\n' + contract + '\n' + wrappers + '\n' + bodies + POST
    mutants = {
        'P2 disabled': ('M32(v + 0x60) = d; M32(v + 8) = 1;', 'M32(v + 0x60) = d; M32(v + 8) = i ? 0 : 1;'),
        'P1 matrix reused': ('cur_view = arg(c, 0);', 'if (arg(c,0)==SPLIT_P2) { memcpy(G+c->ecx,G+PLAT1,0x1a0); c->esp+=24; return; }\n    cur_view = arg(c, 0);'),
        'bottom Y zero': ('uint32_t y = i ? h / 2 : 0,', 'uint32_t y = 0,'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-split-view-') as tmp:
        tmp = Path(tmp)
        def run(label, src, pads):
            path = tmp / 'test.c'; binary = tmp / 'test'
            path.write_text(src)
            result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-label', '-Wno-unused-but-set-variable', '-g', '-fsanitize=address,undefined',
                '-fno-sanitize-recover=all',
                '-DRECOMP_ABICHECK', '-I'+str(ROOT/'recomp/out'), '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'runtime'), str(path), '-o', str(binary)],
                capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-6000:])
            env = dict(os.environ); env.pop('NFS_PADS', None)
            if pads is not None: env['NFS_PADS'] = pads
            result = subprocess.run([str(binary)], env=env, capture_output=True, text=True)
            print(label, result.stdout.strip().splitlines()[-1])
            if result.returncode and label.startswith('baseline'): print(result.stdout[:2500])
            if result.stderr: print(result.stderr[-3000:])
            return result
        for pads in (None, '1', 'invalid', '2'):
            result = run('baseline '+str(pads), source, pads)
            if result.returncode: raise SystemExit(1)
        for label, (old, new) in mutants.items():
            assert source.count(old) == 1, (label, source.count(old))
            result = run('mutant '+label, source.replace(old, new), '2')
            if result.returncode != 1 or 'failures' not in result.stdout:
                raise SystemExit('mutant failed to produce assertion failures: '+label)
    print('SPLIT VIEW PASS (3/3 mutants rejected)')


if __name__ == '__main__':
    main()
