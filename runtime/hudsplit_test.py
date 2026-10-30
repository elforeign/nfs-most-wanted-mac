#!/usr/bin/env python3
"""Actual HUD ownership/quad hooks and translated RenderObject/cache/FE routing.
ASan/UBSan: two pads 184,502 checks / 0 failures; unset/1/invalid each
163,245 / 0, including 8 full 16 MiB snapshots + CPU identical (134,217,728 B).
Blanket-half-viewport/no-scissor-restore/no-coordinate-restore/map-moved/
no-owner-restore mutants fail 8,536 / 1,752 / 728 / 288 / 1,177 checks.
No game/GPU/Unicorn. Geometry generation, mask-shader setup and final D3D draw
are recording sinks; cached routing is translated 005A45B0/00582C70/006BE7xx.
Expected pixels use an independent half-local mapping, never the hook's inverse.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    return re.search(r'(?:static )?(?:void|int|float) ' + name + r'\([^\n]*\).*?\n\}', source, re.S)[0]


def translated(file, name):
    return re.search(r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}',
                     (ROOT / 'recomp/out' / file).read_text(), re.S)[0]


PRE = r'''
#include "funcs.h"
#include "host.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
uint8_t *G;
int diag_on;
static int wide=1, checks, failures, prompts_mode, con, cached, uncached, masked;
static unsigned devcalls, draws, expected_owner, snapshots;
static uint32_t scissor[4], enabled, initial_scissor[4], initial_enable;
static float input[8], pixels[8];
static uint32_t expected_viewport[6], draw_object;
static int map_draw;
#define MEMSIZE (16*1024*1024)
#define OBJ 0x300000u
#define PKG 0x310000u
#define HUD 0x320000u
#define MAP 0x330000u
#define CACHE 0x340000u
#define NODE 0x350000u
#define POLY (NODE+0x10)
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if(failures<12) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
static int near(float a,float b) { return isfinite(a) && fabsf(a-b)<0.002f; }
int pad_count(void) { const char *p=getenv("NFS_PADS"); return p && !strcmp(p,"2") ? 2:1; }
uint32_t gmalloc_try(uint32_t n) { CHECK(n==56); return 0x400000; }
uint32_t galloc(uint32_t n) { CHECK(n==20); return 0x410000; }
void diag_event(const char *fmt,...) { (void)fmt; }
float movies_picture_aspect(void) { return 0; }
void f_005696C0_orig(Cpu *c) { c->esp+=4; }
static int mode(void) { return prompts_mode; }
static int console(void) { return con; }
static int keep_get(uint32_t o) { (void)o; return 0; }
static uint32_t cap_get(uint32_t o) { (void)o; return 0; }
static uint32_t glyph_sub(uint32_t tex) { return tex==111 ? 222:0; }
// Prompt diagnostics are a sink; this harness exercises render ownership and routing.
static void render_inventory(Cpu *c,uint32_t o,uint32_t f,uint32_t tex,uint32_t sub) {
 (void)c;(void)o;(void)f;(void)tex;(void)sub;
}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 (void)n; ++devcalls; CHECK(a[0]==0x420000);
 switch(fn) {
 case 0x130: memcpy(G+a[1],scissor,16); break;
 case 0xe8: CHECK(a[1]==174); M32(a[2])=enabled; break;
 case 0x12c: memcpy(scissor,G+a[1],16); break;
 case 0xe4: CHECK(a[1]==174); enabled=a[2]; break;
 default: CHECK(0); break;
 }
 c->eax=0xdead; c->ecx=0xbeef; c->edx=0xabcd; c->esp+=4; return 0;
}
void bad_jump(Cpu *c,uint32_t a) { (void)c; (void)a; abort(); }
void abi_violation(Cpu *c,uint32_t f,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)f;(void)b;(void)s;(void)i;(void)p;(void)sp; abort();
}
void dispatch(Cpu *c,uint32_t a) { (void)c;(void)a; abort(); }
void f_006D1260_orig(Cpu *c);
void f_006BE6D0(Cpu *c) { c->esp+=8; }
void f_006BE740(Cpu *c) { c->esp+=4; }
'''

SINK = r'''
// Record the original quad renderer's pixel output under the current FE matrix.
// The mask shader uses the same vertex positions and separate mask UVs.
void f_006D1260_orig(Cpu *c) {
 uint32_t p=arg(c,0); float kx,ky; fe_transform(&kx,&ky); ++draws;
 float w=M32(WINDOW_WIDTH), h=M32(WINDOW_HEIGHT);
 for(unsigned v=0;v<4;v++) {
  pixels[2*v]=w*.5f+(MF32(p+16*v)-320)*w*kx/640;
  pixels[2*v+1]=h*.5f+(MF32(p+16*v+4)-240)*h*ky/480;
  float ex,ey;
  if(expected_owner) {
   unsigned rh=expected_owner==2 ? (unsigned)h-(unsigned)h/2:(unsigned)h/2;
   float scale=fminf(w/(wide && w/h>=1.5f ? 640*4.0f/3:640),rh/240.0f);
   ex=w*.5f+scale*(input[2*v]-320);
   ey=(expected_owner==2 ? (unsigned)h/2:0)+rh*.5f+scale*(input[2*v+1]-(expected_owner==2 ? 360:120));
  } else {
   ex=w*.5f+(input[2*v]-320)*w*kx/640;
   ey=h*.5f+(input[2*v+1]-240)*h*ky/480;
  }
  CHECK(near(pixels[2*v],ex)); CHECK(near(pixels[2*v+1],ey));
 }
 if(expected_owner) {
  uint32_t lo=expected_owner==2 ? (unsigned)h/2:0;
  uint32_t hi=expected_owner==2 ? (unsigned)h:(unsigned)h/2;
  CHECK(enabled==1); CHECK(scissor[0]==(initial_enable ? initial_scissor[0]:0));
  CHECK(scissor[1]==(initial_enable && initial_scissor[1]>lo ? initial_scissor[1]:lo));
  CHECK(scissor[2]==(initial_enable ? initial_scissor[2]:(unsigned)w));
  CHECK(scissor[3]==(initial_enable && initial_scissor[3]<hi ? initial_scissor[3]:hi));
 } else { CHECK(enabled==initial_enable); CHECK(!memcmp(scissor,initial_scissor,16)); }
 CHECK(!memcmp(G+0x430000,expected_viewport,24));
 if(con) CHECK(M32((draw_object ? draw_object:OBJ)+0x24)==222);
 // Only x/y may change. UV, mask UV, colour, depth and flags stay byte-identical.
 for(unsigned i=0;i<0x94;i++) if(i!=0x90 && (i>=64 || i%16>=8)) CHECK(G[p+i]==(uint8_t)(i*3+1));
 c->eax=123; c->esp+=4;
}
// Mask setup is a sink, with the translated masked FE forwarding preserved.
void f_006D1930(Cpu *c) {
 ++masked; Cpu q=*c; q.esp-=32; M32(q.esp+4)=arg(c,0); M32(q.esp+8)=arg(c,1); M32(q.esp+12)=0;
 f_006D1260(&q); c->esp+=4;
}
// Geometry generation is a sink; use the real translated cache renderer next.
static void generate(Cpu *c) {
 ++uncached; uint32_t o=arg(c,0); M32(o+0x58)=CACHE; M32(o+0x1c)&=~0x2000000u;
 Cpu q=*c; q.ecx=CACHE; q.esp-=32; f_00582C70(&q); c->esp+=16;
}
void f_0059A500(Cpu *c) { generate(c); }
void f_0059A790(Cpu *c) { generate(c); }
void f_005A4390(Cpu *c) { generate(c); }
void f_00599DB0(Cpu *c) { generate(c); }
void f_0059A060(Cpu *c) { generate(c); }
'''

POST = r'''
static Cpu cpu(void) { return (Cpu){.esp=0x800000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f}; }
static void setup(unsigned w,unsigned h) {
 memset(G,0,MEMSIZE); split_views_reset();
 M32(FE_DATABASE)=0x440000; M8(0x440000)=2; M8(0x44012c)=4;
 M32(GAMEFLOW_STATE)=6; M32(WINDOW_WIDTH)=w; M32(WINDOW_HEIGHT)=h;
 M32(SPLIT_OUTPUT+8)=100; M32(SPLIT_OUTPUT+12)=101; M32(SPLIT_OUTPUT+20)=w; M32(SPLIT_OUTPUT+24)=h;
 split_views_update(); hud_owner=0; hud_map_count=0;
 if(split_active) {
  CHECK(split_rects[0][1]==0 && split_rects[1][1]==h/2);
  CHECK(split_rects[0][3]+split_rects[1][3]==h);
 }
 M32(0x91cfac)=PKG; M32(PKG+0x10)=0xe4a4f786;
 M32(OBJ+0x18)=1; M32(OBJ+0x10)=17;
 M32(CACHE+0xc)=2; M32(CACHE+0x18)=NODE; M32(NODE)=CACHE+0x18;
 M32(0x982bdc)=0x420000; M32(0x420000)=0x421000;
 for(unsigned i=0;i<0x180;i+=4) M32(0x421000+i)=i;
 expected_viewport[0]=expected_viewport[1]=0; expected_viewport[2]=w; expected_viewport[3]=h;
 expected_viewport[4]=0; expected_viewport[5]=0x3f800000;
 memcpy(G+0x430000,expected_viewport,24);
}
static void draw(uint32_t hash,unsigned owner,int cache,int mask,int clip) {
 uint32_t object=draw_object ? draw_object:OBJ;
 M32(PKG+0x10)=hash; expected_owner=split_active ? owner:0;
 con=prompts_mode!=0;
 M32(object+0x58)=cache ? CACHE:0; M32(object+0x1c)=0; M32(object+0x24)=con ? 111:0;
 M32(NODE+0xb4)=0x450000;
 for(unsigned i=0;i<0x94;i++) G[POLY+i]=(uint8_t)(i*3+1);
 for(unsigned v=0;v<4;v++) {
  MF32(POLY+16*v)=input[2*v]=200+(v%2)*20;
  MF32(POLY+16*v+4)=input[2*v+1]=(map_draw ? 230:(owner==2 ? 293:82))+(v/2)*20;
 }
 M8(POLY+0x90)=mask ? 4:0;
 uint8_t before[0x94]; memcpy(before,G+POLY,sizeof before);
 enabled=initial_enable=clip; scissor[0]=11; scissor[1]=9;
 scissor[2]=M32(WINDOW_WIDTH)-10; scissor[3]=M32(WINDOW_HEIGHT)-7;
 memcpy(initial_scissor,scissor,16);
 Cpu c=cpu(); M32(c.esp+4)=object; M32(c.esp+8)=0;
 unsigned olddraws=draws;
 uint8_t *pre=NULL;
 Cpu original=c;
 if(pad_count()!=2 && snapshots<8) { pre=malloc(MEMSIZE); memcpy(pre,G,MEMSIZE); }
 f_005A45B0(&c);
 CHECK(c.esp==0x80000c); CHECK(c.ebx==7 && c.esi==8 && c.edi==9 && c.ebp==10);
 CHECK(draws==olddraws+1); CHECK(hud_owner==0);
 CHECK(!memcmp(G+POLY,before,sizeof before));
 CHECK(enabled==initial_enable); CHECK(!memcmp(scissor,initial_scissor,16));
 if(con) { CHECK(M32(object+0x24)==111); CHECK(M32(object+0x1c)&0x2000000u); }
 if(cache) ++cached;
 if(pre) {
  uint8_t *post=malloc(MEMSIZE); memcpy(post,G,MEMSIZE); memcpy(G,pre,MEMSIZE);
  // prompts_mode=0 in these samples, so the direct translated body is the
  // original path. Compare every guest byte and the complete CPU result.
  CHECK(prompts_mode==0); f_005A45B0_orig(&original);
  CHECK(!memcmp(post,G,MEMSIZE)); CHECK(!memcmp(&c,&original,sizeof c));
  ++snapshots; free(pre); free(post);
 }
}
static void map_setup(void) {
 M32(HUD+0x20)=3; M32(HUD+0x320)=MAP;
 // Registered group with mask child, map tile and both independent markers.
 M32(MAP+8)=0x460000;
 for(unsigned i=0;i<4;i++) {
  uint32_t n=0x460000+16*i,o=OBJ+0x100*(i+1);
  M32(n)=i==3 ? MAP+8:n+16; M32(n+8)=o; M32(o+0x10)=31+i;
  M32(o+0x18)=i==0 ? 5:1;
 }
 M32(OBJ+0x100+0x64)=OBJ; M32(OBJ+0x18)=1;
 Cpu c=cpu(); c.ecx=HUD; f_005696C0(&c); CHECK(c.esp==0x800004); CHECK(hud_map_count==5);
}
int main(void) {
 G=calloc(1,MEMSIZE);
 const unsigned sizes[][2]={{640,480},{1280,720},{1440,900},{1280,721},{3440,1440},{600,900}};
 const uint32_t hashes[]={0xe4a4f786,0xe4b71007,0x6c130623,0x6c251ea4};
 for(unsigned z=0;z<6;z++) for(wide=0;wide<=1;wide++) {
  setup(sizes[z][0],sizes[z][1]);
  for(unsigned i=0;i<4;i++) for(prompts_mode=0;prompts_mode<3;prompts_mode++)
   for(int ca=0;ca<2;ca++) for(int ma=0;ma<2;ma++) for(int cl=0;cl<2;cl++) draw(hashes[i],i%2+1,ca,ma,cl);
  // Pause/results/menu/Chyron packages and absent package pass through unchanged.
  for(unsigned i=0;i<4;i++) draw(0x123400+i,0,1,1,0);
  M32(0x91cfac)=0; CHECK(split_hud_begin(OBJ)==0); CHECK(hud_owner==0); M32(0x91cfac)=PKG;
  if(pad_count()==2) {
   map_setup();
   map_draw=1;
   draw(hashes[0],0,1,1,0); // registered descendant/mask straddles divider
   for(unsigned i=1;i<=4;i++) {
    uint32_t o=OBJ+0x100*i; M32(PKG+0x10)=hashes[0];
    int saved=split_hud_begin(o); CHECK(hud_owner==0); split_hud_end(saved);
    if(i>=2) { draw_object=o; draw(hashes[0],0,1,1,0); draw_object=0; }
   }
   map_draw=0;
   // A sibling HUD draw still scales; stale address with a new hash is no map.
   M32(OBJ+0x10)=99; draw(hashes[0],1,1,0,0);
   M32(HUD+0x20)=5; Cpu c=cpu(); c.ecx=HUD; f_005696C0(&c); CHECK(hud_map_count==0);
   // Nested full-output scope restores its split caller, then full-output.
   M32(PKG+0x10)=hashes[1]; int saved=split_hud_begin(OBJ); CHECK(hud_owner==2);
   M32(PKG+0x10)=0; int nested=split_hud_begin(OBJ); CHECK(hud_owner==0);
   split_hud_end(nested); CHECK(hud_owner==2); split_hud_end(saved); CHECK(hud_owner==0);
  }
 }
 // Hidden objects must also unwind the owner scope without submitting a quad.
 setup(1280,720); prompts_mode=1; con=1; M32(OBJ+0x1c)=8;
 Cpu hidden=cpu(); M32(hidden.esp+4)=OBJ; M32(hidden.esp+8)=0;
 unsigned prior=draws; f_005A45B0(&hidden);
 CHECK(draws==prior); CHECK(hud_owner==0); CHECK(hidden.esp==0x80000c);
 // Split inactive, including paused/non-racing flow and lost predicate.
 setup(1280,720); split_views_restore(); draw(hashes[1],2,1,1,0);
 CHECK(masked>0 && cached>0 && uncached>0);
 if(pad_count()!=2) CHECK(devcalls==0);
 printf("hudsplit: %d checks, %d failures; pads=%d cached=%d uncached=%d masked=%d draws=%u snapshot_bytes=%u\n",checks,failures,pad_count(),cached,uncached,masked,draws,snapshots*MEMSIZE);
 free(G); return failures ? 1:0;
}
'''


def main():
    wide = (ROOT / 'runtime/widescreen.c').read_text()
    prompts = (ROOT / 'runtime/padprompts.c').read_text()
    macros = '\n'.join(line for line in wide.splitlines() if line.startswith('#'))
    contract = wide[wide.index('static uint32_t split_desc;'):wide.index('static uint32_t cur_view;')]
    fe = wide[wide.index('static float screen_aspect('):wide.index('float widescreen_fe_scale(')]
    hud = wide[wide.index('// Split HUD ownership'):wide.index('static uint32_t matrix_ra;')]
    hooks = '\n'.join(function(wide, n) for n in ('f_005696C0', 'f_006D1260'))
    prompt_hooks = '\n'.join(function(prompts, n) for n in ('render_prompt_object', 'f_005A45B0'))
    bodies = '\n'.join(translated(file, name) for file, name in (
        ('speed_22.c', '005A45B0'), ('speed_21.c', '00582C70'),
        ('speed_39.c', '006BE700'), ('speed_39.c', '006BE720')))
    source = PRE + macros + '\n' + contract + fe + hud + '\nstatic uint32_t matrix_ra;\n' + hooks + SINK + bodies + prompt_hooks + POST
    mutants = {
        'blanket half viewport': ('float centre = hud_owner == 2 ? 360.0f : 120.0f;', 'scale = fminf(w / (wide_hud() ? 640.0f * 4.0f / 3.0f : 640.0f), hud_rect[3] / 480.0f); float centre = 240.0f;'),
        'no scissor restore': ('if (split) split_hud_scissor(c, hud_rect, hud_scissor, 1);', ''),
        'no cached coordinates restore': ('if (split || widen) for', 'if (widen && !split) for'),
        'shared map moved with P1': ('if (hud_owner == 1)\n', 'if (hud_owner == -1)\n'),
        'no owner restore': ('void split_hud_end(int saved) { hud_owner = saved; }', 'void split_hud_end(int saved) { (void)saved; }'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-hudsplit-') as tmp:
        tmp = Path(tmp)
        def run(label, src, pads):
            path = tmp / 'test.c'; binary = tmp / 'test'; path.write_text(src)
            result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-label', '-Wno-unused-but-set-variable', '-g', '-fsanitize=address,undefined',
                '-fno-sanitize-recover=all', '-DRECOMP_ABICHECK',
                '-I'+str(ROOT/'recomp/out'), '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'runtime'),
                str(path), '-o', str(binary)], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-5000:])
            env = dict(os.environ); env.pop('NFS_PADS', None)
            if pads is not None: env['NFS_PADS'] = pads
            result = subprocess.run([str(binary)], env=env, capture_output=True, text=True)
            print(label, result.stdout.strip().splitlines()[-1:] or result.returncode)
            if result.returncode and label.startswith('baseline'): print(result.stdout[:2000])
            if result.stderr: print(result.stderr[-2000:])
            return result
        for pads in (None, '1', 'invalid', '2'):
            if run('baseline '+str(pads), source, pads).returncode: raise SystemExit(1)
        for label, (old, new) in mutants.items():
            assert source.count(old) == 1, (label, source.count(old))
            result = run('mutant '+label, source.replace(old, new), '2')
            if result.returncode != 1 or 'failures' not in result.stdout:
                raise SystemExit('mutant failed to produce assertion failures: '+label)
    print('HUDSPLIT PASS (5/5 mutants rejected)')


if __name__ == '__main__':
    main()
