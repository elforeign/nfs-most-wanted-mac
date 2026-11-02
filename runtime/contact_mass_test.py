"""Translated contact fixtures: 93 checks, 0 failures; 8 pair + 2 single-body solver entries.
Disabled controls fail: Moment 26, tensor 3, inverse mass 17, penetration 6,
collision modifier 2, damage 3, production ghost gate 3, native delegation 1
(each exits 1).
Heavy/light, reversed and off-centre pairs; native world/OBB/ground callers;
GameBreaker/reset/invulnerability; both damage sides and unchanged shared Info.
Private executable supplies translated math/constants; surface/event bookkeeping
is stubbed. No game, GPU or emulation. Native eight-step convergence is checked
against product(1-i/8), rather than an assumed fully converged impulse.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'recomp'))
import recomp

HARNESS = r'''
#include "host.h"
#include "funcs.h"
#include "car_physics.h"
#include <assert.h>
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
void f_006B7720_orig(Cpu *c) { c->esp+=4; }
void f_006978F0_orig(Cpu *c) { c->esp+=8; }
int diag_on;
void diag_event(const char *s,...) { (void)s; }
static int enabled=1,invulnerable,ghost;
static unsigned checks,failures,damage_calls,solver_calls;
static float damage_value,world_mass;
static unsigned world_calls,single_solves,pair_solves;
static float single_mass,pair_mass;
GHOST_GATE
static CarPhysics heavy={1,1888,{1,2,1,0}},light={1,995,{1,1,1,0}};
int car_physics_enabled(void) { return enabled; }
uint32_t car_physics_key(const char *s) { return !strcmp(s,"300c")?1:3; }
const CarPhysics *car85_contact_physics(uint32_t key) { return key==1?&heavy:key==2?&light:NULL; }
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while (0)
#define NEAR(a,b,t) CHECK(isfinite(a) && fabs((a)-(b))<(t))
static Cpu cpu(void) { Cpu c={0}; c.esp=0x800000; c.cw=0x37f; return c; }
void bad_jump(Cpu *c,uint32_t a) { (void)c; fprintf(stderr,"bad jump %x\n",a); abort(); }
void stub(Cpu *c,uint32_t a) { (void)c; fprintf(stderr,"stub %x\n",a); abort(); }
TRANSLATED
void dispatch(Cpu *c,uint32_t fn) {
    uint32_t body=c->ecx-0x60,p=M32(M32(body+0x78));
    switch(fn) {
    // Fake ISimable only supplies attribute identity and IRigidBody lookup.
    case 0x100: c->eax=c->ecx+0x80; break;
    case 0x104: c->eax=M32(c->ecx+0x4c); break;
    case 0x108: c->eax=invulnerable; break;
    case 0x10c: c->eax=0; break;
    case 0x110: c->eax=0; break;
    case 0x114: c->eax=M32(c->ecx-0x14); break;
    case 0x118: c->eax=0; break;
    case 0x11c: damage_value=argf(c,2); ++damage_calls; c->esp+=24; break;
    case 0x130: c->esp+=4; break;
    case 0x12c: c->esp+=8; break;
    case 0x128: world_mass=MF32(arg(c,2)+0xd0); ++world_calls; c->esp+=12; break;
    case 0x120: c->eax=0; break;
    case 0x124: x87_push(c,MF32(M32(M32(c->ecx+0x30))+0x2c)); break;
    DISPATCH
    default: fprintf(stderr,"dispatch %x ecx %x\n",fn,c->ecx); abort();
    }
    c->esp+=4;
}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    uint32_t sp=c->esp;
    for(int i=n;i--;) { c->esp-=4; M32(c->esp)=args[i]; }
    c->esp-=4; M32(c->esp)=0;
    dispatch(c,fn); c->esp=sp; return c->eax;
}
static void body(uint32_t b,uint32_t key,float donor) {
    uint32_t p=b+0x400,owner=b+0x800,vt=b+0xa00,spec=b+0xb00;
    memset(G+b,0,0x1000);
    M32(b)=b+0xf00; M32(b+0xf34)=0x128;
    M32(b+0x60)=0x8ac354; M32(b+0x78)=b+0x300; M32(b+0x300)=p;
    M32(b+0x34)=owner; M32(owner)=vt; M32(vt+0x44)=0x100; M32(vt+0x54)=0x104;
    M32(owner+0x4c)=b+0x48; M32(owner+0x80+4)=owner+0xc0; M32(owner+0xe0)=key;
    M32(b+0x48)=vt+0x100; M32(vt+0x104)=0x114; M32(vt+0x110)=0x110; M32(vt+0x118)=0x124; M32(vt+0x1a4)=0x12c; M32(b+0x48+0x30)=b+0x300;
    M32(b+0x138)=vt+0x200; M32(vt+0x200)=vt+0x300; M32(vt+0x3bc)=0x108;
    M32(b+0x84)=spec; MF32(spec+0x30)=MF32(spec+0x34)=MF32(spec+0x38)=1;
    M16(spec+0xc2)=M16(spec+0xd2)=M16(spec+0xe2)=2;
    MF32(spec+0x40)=MF32(spec+0x44)=MF32(spec+0x48)=1;
    MF32(spec+0x70)=MF32(spec+0x74)=MF32(spec+0x78)=1;
    MF32(b+0xe0)=1; MF32(b+0xe4)=.5f; MF32(b+0xe8)=2;
    MF32(p+0x2c)=donor; MF32(p+0x3c)=1/donor;
    MF32(p+0x40)=MF32(p+0x44)=MF32(p+0x48)=1000;
    for (unsigned i=0;i<4;++i) MF32(p+0x70+i*20)=1;
}
static uint32_t moment(uint32_t b,uint32_t m) {
    Cpu c=cpu(); c.ecx=m; M32(c.esp+4)=b+0x60;
    f_00487160(&c); CHECK(c.esp==0x800008); return m;
}
static void solve(uint32_t a,uint32_t b,float offset) {
    uint32_t plane=0x40000; memset(G+plane,0,0x40);
    MF32(plane+4)=offset; MF32(plane+0xc)=1;
    Cpu c=cpu(); c.ecx=a; uint32_t args[3]={b,plane,8};
    ++solver_calls; call_guest(&c,0x488f70,3,args); printf("solve ret=%u normal=%g mass=%g/%g impulse=%g\n",c.eax,MF32(plane+0xc),MF32(a+0xd0),MF32(b+0xd0),MF32(a+0xf0));
}
static void modifier(uint32_t b,uint32_t other,uint32_t m) {
    Cpu c=cpu(); c.ecx=b; M32(c.esp+4)=other; M32(c.esp+8)=0x40000; M32(c.esp+12)=m;
    f_0069ABF0(&c); CHECK(c.esp==0x800010);
}
static void pair(float mass,int reverse,float offset) {
    heavy.mass=mass;
    body(0x10000,1,1595); body(0x12000,0,1595);
    uint32_t a=moment(0x10000,0x20000),b=moment(0x12000,0x21000);
    uint8_t driving[0x80]; memcpy(driving,G+0x10400,sizeof driving);
    MF32(a+0x98)=-10;
    if(reverse) { MF32(a+0x98)=10; solve(b,a,offset); }
    else solve(a,b,offset);
    float va=MF32(a+0x98),vb=MF32(b+0x98),sign=reverse?1:-1;
    double resolved=1.0-5040.0/2097152.0; // native 8-iteration relaxation, product(1-i/8)
    CHECK(!memcmp(driving,G+0x10400,sizeof driving));
    if (!offset) {
        NEAR(va,sign*(10-10*1595/(mass+1595)*resolved),.00002);
        NEAR(vb,sign*10*mass/(mass+1595)*resolved,.00002);
        NEAR(mass*(va-sign*10)+1595*vb,0,.5);
    } else {
        CHECK(fabs(MF32(a+0xa4))+fabs(MF32(a+0xa8))+fabs(MF32(a+0xac))>0.01);
        NEAR(mass*(va-sign*10)+1595*vb,0,.5);
        NEAR(fabs(MF32(a+0xac))*MF32(a+0x88),fabs(MF32(a+0xf0))*offset,.1);
    }
    printf("pair mass=%g reverse=%d offset=%g va=%g vb=%g\n",mass,reverse,offset,va,vb);
}
int main(int argc,char **argv) {
    assert(argc==2); setvbuf(stdout,NULL,_IONBF,0); G=calloc(1,0x1000000); FILE *f=fopen(argv[1],"rb"); assert(f);
    assert(fread(G,1,0x1000000,f)==0x1000000); fclose(f);
    body(0x10000,1,1595); body(0x12000,0,1595);
    uint32_t a=moment(0x10000,0x20000);
    NEAR(MF32(a+0xd0),1888,.001); NEAR(MF32(a+0xd4),1.0/1888,1e-8);
    NEAR(MF32(a+0x80),1888*17.0/12,.001); NEAR(MF32(a+0x84),1888*20.0/12*2,.002);
    pair(1888,0,0); pair(995,0,0); pair(1888,1,0); pair(995,1,0); pair(1888,0,1); pair(995,0,1);
    heavy.mass=1888;
    // Diagnostic setup is inert when disabled; the integrator wrapper delegates.
    Cpu pass=cpu(); pass.ecx=0x10000; f_006978F0(&pass); CHECK(pass.esp==0x800008);
    // Infinite-mass body exercises the same translated two-body OBB solver.
    body(0x10000,1,1595); body(0x12000,0,1595);
    a=moment(0x10000,0x20000); uint32_t b=moment(0x12000,0x21000);
    M8(b+0xc8)=1; MF32(a+0x98)=-10; solve(a,b,0); NEAR(MF32(a+0x98),-10*5040.0/2097152.0,.00002); NEAR(MF32(b+0x98),0,.001);
    // Native collision modifiers: positive absolute GameBreaker override,
    // reset, invulnerability and the separate native mass multiplier.
    a=moment(0x10000,0x20000); MF32(0x1016c)=3190; modifier(0x10000,0x12000,a);
    NEAR(MF32(a+0xd0),3776,.001); NEAR(MF32(a+0x80),1888*17.0/6,.002); CHECK(MF32(0x1016c)==3190);
    MF32(0x1016c)=0; a=moment(0x10000,0x20000); modifier(0x10000,0x12000,a); NEAR(MF32(a+0xd0),1888,.001);
    MF32(0x1016c)=3190; invulnerable=1; a=moment(0x10000,0x20000); modifier(0x10000,0x12000,a);
    CHECK(M8(a+0xc8)); NEAR(MF32(a+0xd0),1888,.001); invulnerable=0; MF32(0x1016c)=0;
    MF32(0x10b3c)=2; a=moment(0x10000,0x20000); modifier(0x10000,0x12000,a); NEAR(MF32(a+0xd0),3776,.001);
    // Penetration fallback weights the two translations by contact mass.
    body(0x10000,1,1595); body(0x12000,0,1595);
    uint32_t normal=0x40000,point=0x40100; MF32(normal)=1; MF32(normal+4)=MF32(normal+8)=0;
    Cpu c=cpu(); uint32_t args[8]={0x10000,0,0x12000,0,normal,point,0,1}; float depth=1; memcpy(args+6,&depth,4);
    call_guest(&c,0x698360,8,args);
    NEAR(MF32(0x10410),1595.0/(1888+1595),1e-6); NEAR(MF32(0x12410),-1888.0/(1888+1595),1e-6);
    // Reversing the penetration pair preserves the source/other translations.
    body(0x10000,1,1595); body(0x12000,0,1595);
    args[0]=0x12000; args[2]=0x10000; c=cpu(); call_guest(&c,0x698360,8,args);
    NEAR(MF32(0x12410),1888.0/(1888+1595),1e-6); NEAR(MF32(0x10410),-1595.0/(1888+1595),1e-6);
    body(0x10000,1,1595); body(0x12000,0,1595); MF32(0x1016c)=3190;
    args[0]=0x10000; args[2]=0x12000; c=cpu(); call_guest(&c,0x698360,8,args);
    NEAR(MF32(0x10410),1595.0/(3776+1595),1e-6); NEAR(MF32(0x12410),-3776.0/(3776+1595),1e-6);
    MF32(0x1016c)=0;
    // No collision can alter the driving mass/inertia, including after reset.
    CHECK(MF32(0x1042c)==1595 && MF32(0x1043c)==1.0f/1595 && MF32(0x10440)==1000);
    enabled=0; a=moment(0x10000,0x20000); CHECK(MF32(a+0xd0)==1595 && MF32(a+0x80)==1000); enabled=1;
    // Execute the production roam ghost veto in both pair orders.
    roam_ghosts=1; roam_car[0].used=roam_car[0].ghost=1; roam_car[0].sim=0x10800;
    c=cpu(); c.ecx=0x10000; M32(c.esp+4)=0x12000; f_0069B740(&c);
    CHECK(!(c.eax&255) && roam_ghost_skips==1 && native_collision_calls==0);
    c=cpu(); c.ecx=0x12000; M32(c.esp+4)=0x10000; f_0069B740(&c);
    CHECK(!(c.eax&255) && roam_ghost_skips==2 && native_collision_calls==0);
    roam_ghosts=0; c=cpu(); c.ecx=0x10000; M32(c.esp+4)=0x12000; f_0069B740(&c);
    CHECK((c.eax&255)==1 && native_collision_calls==1);
    // Run the translated world caller through Moment construction and its
    // native single-body solver. Surface/default bookkeeping is synthetic.
    body(0x10000,1,1595); MF32(0x10420)=-10;
    uint32_t surface=0x60000; M32(surface+8)=surface+0x100; MF32(surface+0x1e8)=1;
    MF32(normal)=1; MF32(normal+4)=MF32(normal+8)=0;
    memset(G+point,0,12); uint32_t zero=0x60200;
    c=cpu(); c.ecx=0x10000; uint32_t worldargs[6]={normal,point,0,0,surface,zero};
    call_guest(&c,0x699410,6,worldargs);
    printf("world calls=%u mass=%g vx=%g\n",world_calls,world_mass,MF32(0x10420)); CHECK(world_calls==1); NEAR(world_mass,1888,.001); double residual=1; for (unsigned i=1;i<16;++i) residual*=1.0-i/16.0;
    NEAR(MF32(0x10420),10-20*residual,.00002); NEAR(single_mass,1888,.001);
    // The translated ground/body contact path reaches the same Moment seam.
    body(0x10000,1,1595); MF32(0x10420)=-10;
    uint32_t ground=0x62000; memset(G+ground,0,48); MF32(ground+16)=1;
    unsigned previous=single_solves; c=cpu(); c.ecx=0x10000; uint32_t groundargs[2]={ground,1};
    call_guest(&c,0x697ce0,2,groundargs);
    CHECK(single_solves==previous+1); NEAR(single_mass,1888,.001); CHECK(MF32(0x10420)>0);
    CHECK(MF32(0x1042c)==1595);
    // The translated OBB caller constructs an independent world Moment.
    body(0x10000,1,1595); MF32(0x10420)=-10;
    uint32_t geometry=0x63000; memset(G+geometry,0,128);
    for(unsigned i=0;i<4;++i) MF32(geometry+16+i*20)=1;
    previous=pair_solves; c=cpu(); c.ecx=0x10000;
    uint32_t obbargs[7]={normal,point,0x64000,geometry,zero,surface,surface};
    call_guest(&c,0x698ea0,7,obbargs);
    CHECK(pair_solves==previous+1); NEAR(pair_mass,1888,.001); CHECK(MF32(0x10420)>-10);
    CHECK(MF32(0x1042c)==1595);
    // Narrow damage seam is checked using the real translated OnCollision.
    uint32_t damage=0x50000,info=0x51000,owner=0x10800,vt=0x52000;
    M32(damage-0x20)=owner; M32(damage-0x54)=vt; M32(vt+0x28)=0x11c;
    M32(owner+8)=77; M32(info+0x3c)=77; MF32(info+0x20)=2; MF32(info+0x5c)=3;
    M32(info+0x4c)=0; uint8_t before[0x80]; memcpy(before,G+info,0x80);
    c=cpu(); c.ecx=damage; M32(c.esp+4)=info; f_006A85B0(&c);
    CHECK(c.esp==0x800008 && damage_calls==1); NEAR(damage_value,1888*3,.01); CHECK(!memcmp(before,G+info,0x80));
    // The native vehicle/vehicle branch selects +6C for the second owner and
    // doubles the result. The shared Info and donor GetMass remain untouched.
    M32(damage-8)=vt+0x100; M32(vt+0x108)=0x130;
    M32(info+0x1c)=1; M32(info+0x3c)=0; M32(info+0x4c)=77; MF32(info+0x6c)=7;
    memcpy(before,G+info,0x80); c=cpu(); c.ecx=damage; M32(c.esp+4)=info; f_006A85B0(&c);
    CHECK(damage_calls==2); NEAR(damage_value,1888*7*2,.01); CHECK(!memcmp(before,G+info,0x80));
    body(0x10000,2,1595); M32(owner+8)=77; M32(info+0x1c)=0; M32(info+0x3c)=77; M32(info+0x4c)=0;
    c=cpu(); c.ecx=damage; M32(c.esp+4)=info; f_006A85B0(&c); NEAR(damage_value,995*3,.01);
    enabled=0; c=cpu(); c.ecx=damage; M32(c.esp+4)=info; f_006A85B0(&c); NEAR(damage_value,1595*3,.01);
    printf("contact checks=%u failures=%u pair_solves=%u single_solves=%u damage_calls=%u\n",checks,failures,pair_solves,single_solves,damage_calls);
    return failures?1:0;
}
'''


def main():
    if not Path(recomp.EXE).is_file():
        print('SKIP: private translated executable absent'); return 3
    rc = recomp.Recompiler(pe=recomp.PE()); rc.calls = True
    starts = sorted(int(line.split('\t')[0],16) for line in (ROOT/'recomp/functions_v13.tsv').read_text().splitlines()
                    if re.match(r'^[0-9A-Fa-f]{8}(?:\t|$)',line))
    rc.functions = dict(zip(starts, starts[1:]))
    hooks={0x487160,0x698360,0x69abf0,0x6a85b0,0x488f70,0x698650}
    pending=list(hooks|{0x488f70,0x6e8f50,0x676dd0,0x670ee0,0x676de0,0x676df0,0x6710e0,0x671130,0x676e10,0x698270,0x670f20,0x670ef0,0x670f00,0x6710f0,0x676e00,0x699410,0x698ea0,0x697ce0}); generated={}; stubs=set()
    while pending:
        addr=pending.pop()
        if addr in generated: continue
        code,error=rc.translate(addr,rc.functions[addr]); assert not error,(hex(addr),error)
        generated[addr]=code
        for target in set(int(x,16) for x in re.findall(r'f_([0-9A-F]{8})\(c\)',code)):
            if (0x401000<=target<0x402000 or 0x486000<=target<0x490000 or 0x670d00<=target<0x671200 or
                    target in {0x4665b0,0x452940,0x6982a0,0x6e9660,0x5c5e80,0x5cab00,0x410180,0x41ad40,0x45a430}): pending.append(target)
            else: stubs.add(target)
    stubs-=generated.keys()
    generated.pop(0x6e9660,None); generated.pop(0x45a430,None)
    stubs-={0x6e9660,0x45a430,0x6211d0,0x627400,0x68a2d0}
    with tempfile.TemporaryDirectory(prefix='contact-mass-') as directory:
        tmp=Path(directory); memory=bytearray(0x1000000)
        for _,vsize,rva,rawsize,off in rc.pe.sections:
            if rva>=0x490000: # constants/vtables, never executable code bytes
                data=rc.pe.read(rc.pe.base+rva,min(vsize,rawsize))
                if data: memory[rc.pe.base+rva:rc.pe.base+rva+len(data)]=data
        (tmp/'memory.bin').write_bytes(memory)
        translated=["void f_00627400(Cpu *c) { c->eax=0; c->esp+=4; }", "void f_006211D0(Cpu *c) { c->eax=0; c->esp+=8; }", "void f_0068A2D0(Cpu *c) { memset(G+c->ecx,0,128); c->esp+=4; }", "void f_006E9660(Cpu *c) { memset(G+c->ecx,0,20); M32(c->ecx+8)=0x60100; c->eax=c->ecx; c->esp+=8; }", "void f_0045A430(Cpu *c) { c->esp+=4; }"]
        for addr,code in generated.items():
            if addr in hooks: code=code.replace(f'void f_{addr:08X}(',f'void f_{addr:08X}_orig(',1)
            if addr==0x488830:
                code=code.replace('void f_00488830(', 'void fixture_00488830(',1)
                code+='\nvoid f_00488830(Cpu *c) { ++single_solves; single_mass=MF32(c->ecx+0xd0); fixture_00488830(c); }\n'
            if addr==0x488f70:
                code=code.replace('{\n', '{\n    ++pair_solves; pair_mass=MF32(c->ecx+0xd0);\n',1)
            translated.append(code)
        for addr in stubs:
            translated.append(f'void f_{addr:08X}(Cpu *c) {{ stub(c,0x{addr:X}); }}')
        dispatch='\n'.join(f'case 0x{a:X}: f_{a:08X}(c); return;' for a in generated)
        roam=(ROOT/'runtime/roam_game.c').read_text()
        start=roam.index('void f_0069B740(Cpu *c) {')
        gate=roam[start:roam.index('\n}',start)+2]
        gate_prefix="""#define ROAM_CARS 1
        typedef struct { int used,ghost; uint32_t sim; } RoamCar;
        static RoamCar roam_car[1]; static unsigned roam_ghost_skips,native_collision_calls;
        static int roam_ghosts;
        void f_0069B740_orig(Cpu *c) { ++native_collision_calls; c->eax=1; c->esp+=8; }
        """
        source=HARNESS.replace('TRANSLATED','\n'.join(translated)).replace('DISPATCH',dispatch).replace('GHOST_GATE',gate_prefix+gate)
        unit=tmp/'fixture.c'; unit.write_text(source)
        original=(ROOT/'runtime/contact_mass.c').read_text()
        variants={'actual':original,
                  'ghost':original,
                  'integration':original.replace('    f_006978F0_orig(c);','    /* omitted native delegation */'),
                  'tensor':original.replace('memcpy(G+moment+0x80,G+scratch+48,12);','/* omitted tensor */'),
                  'inverse-mass':original.replace('MF32(moment+0xd4)=1.0f/p->mass;','/* omitted inverse mass */'),
                  'moment':original.replace('if (entity>=0x60) source_moment','if (0 && entity>=0x60) source_moment'),
                  'penetration':original.replace('if (car_physics_enabled() && depth','if (0 && car_physics_enabled() && depth'),
                  'modifier':original.replace('if (p && MF32(body+0x16c)','if (0 && p && MF32(body+0x16c)'),
                  'damage':original.replace('float ratio=p->mass/body_mass(body);','float ratio=1.0f;')}
        for name,module in variants.items():
            assert name in ('actual','ghost') or module!=original,name
            unit.write_text(source.replace('if (roam_ghosts) {','if (0) {') if name=='ghost' else source)
            feature=tmp/'contact.c'; feature.write_text(module); exe=tmp/'fixture'
            build=subprocess.run(['clang','-O1','-w','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),
                '-I'+str(ROOT/'recomp/out'),str(unit),str(feature),'-o',str(exe)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr[-5000:]
            run=subprocess.run([str(exe),str(tmp/'memory.bin')],capture_output=True,text=True)
            summary=next((x for x in run.stdout.splitlines() if x.startswith('contact checks=')),run.stdout[-500:])
            print(name,summary)
            if run.returncode and name=='actual': print(run.stdout[-1500:],run.stderr[-1500:])
            assert (run.returncode==0)==(name=='actual'),(name,run.returncode)
    return 0

if __name__=='__main__': raise SystemExit(main())
