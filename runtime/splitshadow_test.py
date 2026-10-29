#!/usr/bin/env python3
"""







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
    s = (ROOT/'recomp/out'/file).read_text()
    return re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', s, re.S)[0]


def function(s, name):
    return re.search(r'void '+name+r'\([^\n]*\).*?\n\}', s, re.S)[0]


EXTRA = r'''
#include "gen/x360_engine.inc"
#define F32(a) MF32(a)
#define SHADOW_FOCUS_FAR 0x901AB0u
#define TIME_OF_DAY_PTR 0x9B392Cu
static void put_float(uint32_t a,float v) { MF32(a)=v; }
static int fit=2, shadow_size=2048, x360_vp_set, nculled;
static uint32_t shadow_source_view, culled[64][2];
static float x360_vp[16];
void x360_shadow_fit(const float *,float,float,const float *,float,float,float *);
int render_dump_active(void) { return 0; }
static void test_sun(void) {}
void guest_divide_error(Cpu *c,uint32_t pc) { (void)c;(void)pc; abort(); }
static int ambient[2], textures[2], cartextures[2], sections[2], refreshes, distance_calls[2], skip_fit;
static uint32_t expected_view, expected_plat;
static float expected_map[2][16], biased_map[2][16];
static int nearf(float a,float b) { return isfinite(a)&&fabsf(a-b)<0.002f*fmaxf(1,fabsf(b)); }
static Cpu cpu(void);
static void receiver(uint32_t view);
static void shadow_map(Cpu *c,uint32_t view,uint32_t cull);
static void guest_run(Cpu *c,void (*fn)(Cpu *),int n,const uint32_t *a) {
 uint32_t sp=c->esp;
 c->esp-=4*(n+1); M32(c->esp)=0xFEED;
 for(int i=0;i<n;i++) M32(c->esp+4+4*i)=a[i];
 fn(c); c->esp=sp;
}
static void mul(uint32_t dst,uint32_t a,uint32_t b) {
 float out[16];
 for(unsigned r=0;r<4;r++) for(unsigned col=0;col<4;col++) {
  out[4*r+col]=0;
  for(unsigned k=0;k<4;k++) out[4*r+col]+=MF32(a+4*(4*r+k))*MF32(b+4*(4*k+col));
 }
 memcpy(G+dst,out,64);
}
void f_006C86B0(Cpu *c) { c->eax=2048; c->esp+=4; }
void f_006C86C0(Cpu *c) { c->eax=2048; c->esp+=4; }
void f_006C6300(Cpu *c) { c->esp+=4; }
void f_006C6450(Cpu *c) { c->esp+=4; }
void f_006C6F30(Cpu *c) { (void)c; abort(); }
void f_006BE800(Cpu *c) { c->esp+=4; } // identity model bound
void f_0045ED20(Cpu *c) { // bMulMatrix: actually transform the bound centre by the selected camera
 uint32_t dst=arg(c,0),m=arg(c,1),v=arg(c,2);
 CHECK(m==expected_plat+0x80); ++distance_calls[expected_view==0x9196C0];
 for(unsigned col=0;col<3;col++) MF32(dst+4*col)=MF32(m+48+4*col)
  +MF32(v)*MF32(m+4*col)+MF32(v+4)*MF32(m+16+4*col)+MF32(v+8)*MF32(m+32+4*col);
 c->esp+=4;
}
void f_007E7D5E(Cpu *c) { mul(arg(c,0),arg(c,1),arg(c,2)); c->esp+=16; }
void f_00743EA0(Cpu *c) { CHECK(arg(c,0)==expected_view); ++ambient[expected_view==0x9196C0]; c->esp+=28; }
void f_0072CA00(Cpu *c) { // separated drivable zones, determined by the actual translated camera argument
 uint32_t pos=arg(c,0); int p2=MF32(pos)>100;
 c->eax=p2?0x580100:0x580000; c->esp+=8;
}
void f_00729640(Cpu *c) { // TreeCull emits distinct caster geometry
 CHECK(c->ecx==(expected_view==0x9196C0?0x590100u:0x590000u));
 ++sections[expected_view==0x9196C0]; c->esp+=8;
}
void f_004624A0(Cpu *c) { (void)c; abort(); }
void f_00723E40(Cpu *c) { (void)c; abort(); }
void f_0072DF20(Cpu *c) { (void)c; abort(); }
void f_00730460(Cpu *c) { (void)c; abort(); }
static void shadow_dispatch(Cpu *c,uint32_t t) {
 if(t>=FXMAGIC&&t<FXMAGIC+0x200) {
  uint32_t h=arg(c,1),v=arg(c,2); unsigned i=expected_view==0x9196C0;
  if(t==FXMAGIC+0xD0) {
   CHECK(h==0xC04 && v==0xAABBCCDD); ++textures[i];
   if(M32(c->esp)==0x006D6B9E) ++cartextures[i];
  }
  if(t==FXMAGIC+0x98) { // car ShadowTransform is the current view's biased map
   CHECK(h==0xC54);
   if(split_on) {
    float b[16]={0.5f,0,0,0, 0,-0.5f,0,0, 0,0,1,0, 0.5f+0.5f/2048,0.5f+0.5f/2048,0,1};
    for(unsigned r=0;r<4;r++) for(unsigned col=0;col<4;col++) {
     float expected=0; for(unsigned k=0;k<4;k++) expected+=biased_map[i][4*r+k]*b[4*k+col];
     CHECK(nearf(MF32(v+4*(4*r+col)),expected));
    }
   }
  }
  c->esp += 16; return;
 }
 fprintf(stderr,"unexpected import %x ret %x ecx %x\n",t,M32(c->esp),c->ecx); exit(2);
}
'''

POST = r'''
static Cpu cpu(void) { return (Cpu){.esp=0x800000,.ebx=7,.esi=8,.edi=9,.ebp=10,.cw=0x37f}; }
static void identity(uint32_t a) { memset(G+a,0,64); for(unsigned k=0;k<4;k++) MF32(a+20*k)=1; }
static void shadow_setup(void) {
 setup();
 for(unsigned i=0;i<2;i++) {
  uint32_t v=0x919650+i*0x70, plat=0x310000+i*0x1000,cam=M32(v+0x40),rain=M32(v+0x68);
  M32(v)=plat;
  identity(plat); identity(plat+0x40); identity(plat+0x80);
  MF32(plat+48)=i?-500:-10; MF32(plat+56)=-5;
  MF32(plat+0x40)=i?1.7f:1.1f; MF32(plat+0x54)=i?-2.1f:-1.4f;
  MF32(plat+0x80+56)=i?60:5;
  MF32(cam+0x40)=i?500:10; MF32(rain+0x290)=i?0.6f:0.1f;
  uint32_t zone=0x580000+i*0x100,sec=0x590000+i*0x100,header=0x591000+i*0x100;
  M16(zone+0x10)=1; M16(zone+0x12)=i?202:101;
  M32(0x9A3BB0+4*(i?202:101))=header; M32(header+4)=sec; M32(sec+0x28)=1;
 }
 M32(0x919C70)=0x985990; M32(0x919C74)=15; M32(0x919CB0)=0x570000;
 MF32(0x89096C)=1; M32(0x9B392C)=0x560000; MF32(0x560188)=1;
 MF32(0x560180)=0.4f; MF32(0x560184)=0.3f; MF32(0x560188)=0.8660254f;
 MF32(0x9018F0)=0; MF32(0x9018EC)=0.8660254f; MF32(0x9018E4)=0; MF32(0x9018E8)=1;
 MF32(SHADOW_FOCUS_FAR)=120; MF32(0x8FAE60)=1000; MF32(0x8FAE58)=10; MF32(0x8FAE5C)=50;
 MF32(0x8933D4)=0.5f; M32(0x982C3C)=3;
 M32(0x510044)=0x530000; M32(0x530C04)=0xC04; M32(0x530C2C)=0xC2C; M32(0x530C54)=0xC54;
 M32(0x510004)=0x12; M32(0x510038)=3;
 M32(0x982CF0)=0xAABBCCDD; M32(0x982CF8)=0xAABBCCDD; M32(0x982C80)=0x510000;
 M32(0x530004)=0; M32(0x530038)=3;
 M32(0x5A0000)=0x5A1000; M32(0x903328)=1;
 memset(ambient,0,sizeof ambient); memset(textures,0,sizeof textures); memset(cartextures,0,sizeof cartextures);
 memset(sections,0,sizeof sections); memset(distance_calls,0,sizeof distance_calls); refreshes=0;
}
void f_006E44C0_orig(Cpu *c) { // fake caster draw driver; use the actual hook and its camera/bias callbacks
 if(skip_fit) { c->esp+=4; return; }
 uint32_t view=arg(c,0),cull=arg(c,1),ret=M32(c->esp),sp=c->esp;
 unsigned i=view==0x9196C0; expected_view=view; expected_plat=M32(view);
 float light[3]; memcpy(light,G+0x560180,12);
 x360_shadow_fit((float *)(G+expected_plat),MF32(expected_plat+0x40),-MF32(expected_plat+0x54),light,
                 X360E_SHADOW_NEAR,MF32(SHADOW_FOCUS_FAR),expected_map[i]);
 M32(c->esp)=0x006E50C8; x360_shadow_camera(c,0x985990,0x9859D0);
 mul(0x985A10,0x985990,0x9859D0);
 for(unsigned k=0;k<16;k++) CHECK(nearf(MF32(0x985A10+4*k),expected_map[i][k]));
 CHECK(!rs174); CHECK(cull==shadow_cull);
 // The actual translated 006E50C8 refresh instruction, after matrix multiply.
 caster_refresh(c);
 CHECK(sections[i]==1);
 identity(0x5B0000); MF32(0x5B0000)=0.5; MF32(0x5B0014)=-0.5;
 M32(0x5B0030)=M32(0x5B0034)=0x3F002000;
 memset(G+0x5B1000,0xCC,64);
 M32(c->esp)=0x006E542D; x360_shadow_camera(c,0x5B1000,0x5B0000);
 mul(0x9859D0,0x5B1000,0x5B0000);
 for(unsigned k=0;k<16;k++) CHECK(nearf(MF32(0x5B1000+4*k),expected_map[i][k]));
 CHECK(nearf(MF32(0x5B0030),0.5f+0.5f/2048));
 memcpy(biased_map[i],G+0x9859D0,64);
 CHECK(x360_vp_set==0);
 M32(sp)=ret; c->esp=sp+4;
}
static void shadow_map(Cpu *c,uint32_t view,uint32_t cull) {
 CHECK(nworld==(maps?6:0)); CHECK(!rs174); map_owner=view; ++maps;
 uint32_t a[4]={view,cull,0,1}; guest_run(c,f_006E44C0,4,a);
 bound_rt=0xBAD; bound_ds=0xBAD; vp[0]=vp[1]=0; vp[2]=vp[3]=2048;
}
static void receiver(uint32_t view) {
 expected_view=view; expected_plat=M32(view); M32(0x982C80)=0x510000; unsigned i=view==0x9196C0;
 if(!nearf(MF32(0x8FAE64),i?0.4f:0.9f)) fprintf(stderr,"weather v%x = %f rain %f\n",view,MF32(0x8FAE64),MF32(M32(view+0x68)+0x290)); CHECK(nearf(MF32(0x8FAE64),i?0.4f:0.9f));
 Cpu c=cpu(); c.ecx=0x510000; int t=textures[i];
 guest_run(&c,f_006C5B80,0,NULL); CHECK(textures[i]==t+1);
 c=cpu(); c.ecx=0x510000; t=cartextures[i];
 guest_run(&c,f_006D6AA0,0,NULL); CHECK(cartextures[i]==t+1);
 // Actual translated sort-key technique gate and distance operand (11 stdcall arguments).
 uint32_t a[11]={0x5C0000,0,0,0,0x530000,0x5C1000,0,0,0,0x5A0000,0};
 c=cpu(); c.ecx=0x5D0000; guest_run(&c,f_006C7030,11,a);
 CHECK(distance_calls[i]>0); CHECK(M32(0x5D003C)==(i?1:3));
 c=cpu(); c.ebp=0x5E0000; c.esi=0x5F0000; M32(c.ebp+8)=view; M32(c.ebp+0x28)=0;
 int n=ambient[i]; ambient_block(&c); CHECK(ambient[i]==n+1);
 CHECK(M32(view+4)==i+1); CHECK(M32(M32(view+0x60)+4)==i+1);
}
int main(void) {
 G=calloc(1,MEMSIZE); shadow_setup(); split_on=1;
 Cpu c=cpu(); c.esp-=4; f_006DE300(&c);
 CHECK(maps==2 && refreshes==2); CHECK(ambient[0]==6 && ambient[1]==6);
 CHECK(sections[0]==1 && sections[1]==1); CHECK(nworld==12);
 for(unsigned k=0;k<12;k++) CHECK(world_ok[k]);
 CHECK(rs174==0 && sc[0]==1 && sc[1]==2 && sc[2]==3 && sc[3]==4);
 CHECK(shadow_source==0 && shadow_cull==0); CHECK(c.esp==0x800000);
 // Split off: compare every memory byte and CPU for the three complete translated gates,
 // weather, section lookup and the ambient block against their unmodified translations.
 for(unsigned i=0;i<2;i++) {
  void (*changed[])(Cpu *)={f_006C5B80,f_006C7030,f_006D6AA0,f_006C83D0,ambient_block,f_00731870};
  void (*retail[])(Cpu *)={retail_006C5B80,retail_006C7030,retail_006D6AA0,retail_006C83D0,retail_ambient,retail_section};
  for(unsigned k=0;k<6;k++) {
   shadow_setup(); split_on=0; expected_view=i?0x9196C0:0x919650; expected_plat=M32(expected_view);
   M32(0x982A20)=M32(expected_view+0x60);
   uint32_t a[11]={0x5C0000,0,0,0,0x530000,0x5C1000,0,0,0,0x5A0000,0};
   if(k==5) { a[0]=0x5C0000; a[1]=128; a[2]=0x5C1000; M32(a[2]+0x80)=0x919C70; }
   uint8_t *before=malloc(MEMSIZE),*after=malloc(MEMSIZE); memcpy(before,G,MEMSIZE);
   Cpu x=cpu(); x.ecx=0x510000; if(k==1)x.ecx=0x5D0000;
   x.ebp=0x5E0000; x.esi=0x5F0000; M32(x.ebp+8)=expected_view;
   memcpy(before,G,MEMSIZE); Cpu y=x;
   // Sink assertions use the original P1 distance matrix outside split.
   expected_plat=M32(0x919650);
   int nargs=k==1?11:k==5?3:0;
   guest_run(&x,changed[k],nargs,a); memcpy(after,G,MEMSIZE);
   memcpy(G,before,MEMSIZE); guest_run(&y,retail[k],nargs,a);
   CHECK(memcmp(after,G,MEMSIZE)==0); CHECK(memcmp(&x,&y,sizeof x)==0);
   free(before); free(after);
  }
 }
 // ID alias never admits reflections, unknown descriptors, or view 2 without both rectangles.
 shadow_setup(); split_on=1; M32(0x5A0004)=2; CHECK(split_shadow_id(0x5A0000)==2);
 CHECK(split_shadow_id(0x919650)==1); CHECK(split_shadow_id(0x9196C0)==1);
 split_on=0; CHECK(split_shadow_id(0x9196C0)==2);
 // P1's camera/receiver callbacks remain byte-identical to the former fixed fit.
 for(unsigned phase=0;phase<2;phase++) {
  shadow_setup(); split_on=0; shadow_source_view=0x919650;
  memcpy(G+0x9842D0,G+M32(0x919650),0xC0);
  identity(0x5B0000); M32(0x5B0030)=M32(0x5B0034)=0x3F002000;
  uint8_t *before=malloc(MEMSIZE),*after=malloc(MEMSIZE); memcpy(before,G,MEMSIZE);
  Cpu x=cpu(); M32(x.esp)=phase?0x006E542D:0x006E50C8; Cpu y=x;
  memcpy(before,G,MEMSIZE);
  x360_vp_set=0; x360_shadow_camera(&x,0x5B1000,0x5B0000); memcpy(after,G,MEMSIZE);
  memcpy(G,before,MEMSIZE); x360_vp_set=0; retail_camera(&y,0x5B1000,0x5B0000);
  CHECK(memcmp(after,G,MEMSIZE)==0); CHECK(memcmp(&x,&y,sizeof x)==0);
  free(before); free(after);
 }
 // An early return before fitting cannot reuse the preceding player's state.
 c=cpu(); skip_fit=1; x360_vp_set=1; uint32_t v=0x919650;
 guest_run(&c,f_006E44C0,1,&v); CHECK(x360_vp_set==0); skip_fit=0;
 printf("splitshadow: %d checks, %d failures\n",checks,failures); free(G); return failures?1:0;
}
'''


def main():
    src = (ROOT/'runtime/splitworld.c').read_text()
    body = '\n'.join(l for l in src.splitlines() if not l.startswith('#include'))
    x360 = (ROOT/'runtime/x360.m').read_text()
    camera = function(x360, 'x360_shadow_camera')
    retail_camera = camera.replace('x360_shadow_camera(', 'retail_camera(').replace('M32(shadow_source_view)', '0x009842D0u')
    wrapper = function(x360, 'f_006E44C0')
    gate_names = [('speed_40.c', n) for n in ['006C5B80','006C7030','006D6AA0','006C83D0']]
    gates = '\n'.join(translated(f,n) for f,n in gate_names)
    section = translated('speed_45.c','00731870').replace('f_00731870_orig(', 'f_00731870(')
    section += '\n'+translated('speed_45.c','00731B10')
    full_ambient = translated('speed_47.c','0074E160')
    ambient = full_ambient[full_ambient.index('    /* 0074E7FF:'):full_ambient.index('    /* 0074E843:')]
    ambient = 'static void ambient_block(Cpu *c) {\n uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0;\n'+ambient+'\n (void)cf;(void)pf;(void)af;(void)zf;(void)sf;(void)of;\n}\n'
    retail = gates+ambient+translated('speed_45.c','00731870')
    retail = retail.replace('f_00731870_orig(', 'retail_section(').replace(
        'split_shadow_section_camera(M32((uint32_t)(c->eax + 0x919620u)))',
        'M32((uint32_t)(c->eax + 0x919620u))')
    retail = re.sub(r'split_shadow_id\(c->(edx|ecx|edi)\)', r'M32(c->\1+4)', retail)
    retail = retail.replace('split_shadow_player_plat()', 'M32(0x919650)').replace('split_shadow_rain()', 'M32(0x9196B8)')
    for _,n in gate_names: retail = retail.replace('f_'+n+'(', 'retail_'+n+'(')
    retail = retail.replace('ambient_block(', 'retail_ambient(')
    caster = translated('speed_41.c','006E44C0')
    refresh = caster[caster.index('    /* 006E50C8:'):caster.index('    /* 006E50CD:')]
    refresh = 'static void caster_refresh(Cpu *c) {\n'+refresh+'\n}\n'
    pre = world.PRE.replace('static void world_call(uint32_t view) {', 'static void world_call(uint32_t view) {\n receiver(view);')
    pre = pre.replace('void dispatch(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"unexpected dispatch %x\\n",t); exit(2); }',
                      'void dispatch(Cpu *c,uint32_t t) { shadow_dispatch(c,t); }')
    pre = pre.replace(' switch (fn) {\n case 0x006E54E0u:', ''' switch (fn) {
 case 0x006C83D0u: guest_run(c,f_006C83D0,0,NULL); break;
 case 0x006BFDD0u: M32(a[0]+0x9C0)=1; M32(a[0]+0x80)=0x919C70; break;
 case 0x004FAE40u: CHECK(a[0]==0x919C70 && a[2]==0x4100); M32(a[1]+0x80)=a[0]; ++refreshes; break;
 case 0x00731B80u: { uint32_t e=c->ecx; guest_run(c,f_00731B10,1,&e); break; }
 case 0x006E54E0u:''')
    old = '''  CHECK(n==2 && a[0]==(maps?0x9196C0u:0x919650u)); CHECK(rs174==0);
  CHECK(nworld==(maps?6:0)); map_owner=a[0]; ++maps;
  bound_rt=0xBAD; bound_ds=0xBAD; vp[0]=vp[1]=0; vp[2]=vp[3]=2048; break;'''
    assert old in pre
    pre = pre.replace(old, '  shadow_map(c,a[0],a[1]); break;')
    # Declarations before the recording harness; definitions after its globals.
    insertion = pre.index('int split_view_rect(')
    pre = pre[:insertion]+EXTRA+pre[insertion:]
    setup = world.POST[world.POST.index('static void setup('):world.POST.index('int main(')]
    source = pre+body+camera+retail_camera+wrapper+gates+section+ambient+retail+refresh+setup+POST
    # Retail functions are used only after all per-view checks; unused helpers in harness are harmless.
    mutants = {
      'original ambient guard': ('split_shadow_id(c->edi)', 'M32(c->edi+4)'),
      'fixed P1 fit': ('uint32_t source_plat = M32(shadow_source_view);', 'uint32_t source_plat = M32(0x919650);'),
      'P1 section lookup': ('split_shadow_section_camera(M32((uint32_t)(c->eax + 0x919620u)))', 'M32((uint32_t)(c->eax + 0x919620u))'),
      'split map scissor': ('render_state(c, 174, 0);                                    // the whole map', 'render_state(c, 174, 1);                                    // the whole map'),
    }
    with tempfile.TemporaryDirectory(prefix='nfs-splitshadow-') as tmp:
        tmp = Path(tmp)
        cpp = tmp/'fit.o'
        subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined','-c',str(ROOT/'runtime/x360_shadow_fit.cpp'),'-o',str(cpp)],check=True,capture_output=True)
        def run(label, text):
            path=tmp/'test.c'; binary=tmp/'test'; path.write_text(text)
            r=subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-parentheses-equality','-g',
              '-fsanitize=address,undefined','-fno-sanitize-recover=all','-DRECOMP_ABICHECK',
              '-I'+str(ROOT/'recomp/out'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'runtime'),str(path),str(cpp),'-lc++','-o',str(binary)],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr[-7000:])
            r=subprocess.run([str(binary)],capture_output=True,text=True)
            lines=r.stdout.strip().splitlines()
            print(label, lines[-1] if lines else '(no output)')
            if label=='baseline' and r.returncode: print(r.stdout[:3000],r.stderr[-3000:])
            return r
        if run('baseline',source).returncode: raise SystemExit(1)
        for label,(old,new) in mutants.items():
            assert source.count(old)==1,(label,source.count(old))
            r=run(label,source.replace(old,new))
            if r.returncode!=1 or 'failures' not in r.stdout: raise SystemExit('mutant not rejected: '+label)
    print('SPLIT SHADOW PASS (4/4 mutants rejected)')


if __name__=='__main__': main()
