"""Compiled production hooks + translated selection/counting/cache, no emulation.

The retail control must fail added-key reachability and forced pilot checks.
Retail: 369/6196 failures; fixed: 0/6198; public: 0/6196.
Shared-count removal: 360/6197; timer removal: 2/6198 failures.
Native/pilot render fixtures: 0/1561 each (3,122 assertions combined).
Counts and broken-control results are printed; visual acceptance is separate.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
source = ROOT / 'recomp/out/speed_03.c'
if not source.exists():
    print('SKIP: build recompiler first')
    raise SystemExit(3)
translated = source.read_text()


def function(text, name):
    match = re.search(r'^void ' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{', text, re.M)
    assert match, name
    return text[match.start():text.index('\n}', match.start()) + 2]


guest = '\n'.join(function(translated, n) for n in (
    'f_00426230', 'f_004253C0', 'f_00426380', 'f_00426497'))
guest += '\n' + function((ROOT / 'recomp/out/speed_04.c').read_text(), 'f_00439A10')
# Only rename the definitions. Calls still go through the production hooks.
for name in ('f_00426230', 'f_004253C0'):
    guest = re.sub(r'void ' + name + r'(?:_orig)?\(', 'void ' + name + '_orig(', guest)
module = (RT / 'attrib_x360.c').read_text()
start = module.index('static _Thread_local uint32_t traffic_count_native')
hooks = module[start:module.index('// Diagnostics only (BOOT_DIAG): the tyre smoke', start)]
policy = (RT / 'traffic_pilot.c').read_text()
policy = policy[policy.index('int traffic_pilot_quota_allows'):policy.index('int traffic_pilot_boot')]


def host_function(text, name):
    match = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', text, re.M)
    assert match, name
    return text[match.start():text.index('\n}', match.start()) + 2]


# Same production consumers and translated instruction ranges as the unchanged
# traffic_role_test; here feed the two identities reached by the spawn fixture.
render = (ROOT / 'recomp/out/speed_47.c').read_text()
decode = render[render.index('    /* 0074E23F:'):render.index('    /* 0074E261:')]
lod = render[render.index('    /* 0074E4D4:'):render.index('    /* 0074E570:')]
constructor = (ROOT / 'recomp/out/speed_48.c').read_text()
connect = constructor[constructor.index('    /* 0075F8F9:'):constructor.index('    /* 0075F937:')]
materials = (RT / 'materials.m').read_text()
physical = (RT / 'realworld_materials.c').read_text()
ROLE = ((RT / 'traffic_detail.c').read_text() + r'''
typedef struct GlassX360 { uint32_t hash; float min_a,max_a,env_min,env_max; } GlassX360;
static const GlassX360 glass_x360[]={{0x471A1DCAu,.1f,.2f,.3f,.4f}};
#define GLASS_X360_COUNT 1
''' + host_function(materials, 'glass_find') + host_function(physical, 'hash_name') +
        host_function(physical, 'realworld_materials_role') + r'''
static void decode_role(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0;
    c->edx=0;
''' + decode + r'''
}
static void select_lod(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0;
''' + lod + r'''
}
static void connect_type(Cpu *c) {
    uint32_t cf=0,of=0,zf=0,sf=0,pf=0,af=0,sp=c->esp;
''' + connect + r'''
    c->esp=sp;
}
static void render_subject(uint32_t key) {
    unsigned before=checks,errors=failures;
    CHECK(key==NATIVE || key==PILOT);
    float racer[]={120,25,20,10,0},traffic[]={80,40,20,10,0};
    memcpy(G+0x8b2ebc,racer,sizeof racer);memcpy(G+0x8b2ed0,traffic,sizeof traffic);
    MF32(0x8970c0)=120;MF32(0x8b2f7c)=80;
    unsigned type=key==PILOT?160:25;
    uint32_t table=0x140000;M32(0x9b09d8)=table;
    M32(table+25*208+0x94)=2;
    memcpy(G+table+160*208,G+table+25*208,208);
    for(int on=0;on<2;++on)for(int px=1;px<=130;++px) {
        traffic_detail_set(on);
        Cpu c={.esp=0x100000,.esi=0x110000,.ebp=0x120000,.cw=0x37f};
        M32(c.esi+0x84)=0x130000;M32(0x130000)=type;
        c.ecx=0x130000;connect_type(&c);
        CHECK(M32(c.esi+0x88)==table+type*208); // CarTypeInfo, not a physical driver
        decode_role(&c);CHECK(M32(c.esp+0x28)==1);
        c.edi=0;M32(c.ebp+0x20)=0;
        M32(c.esp+0x10)=px;M32(c.esi+0x1628)=0;M32(c.esi+0x162c)=4;
        select_lod(&c);uint32_t flags=M32(c.ebp+0x20);
        CHECK(flags==0x80000u);
        CHECK((glass_find(0x1FF8C329u,flags)!=NULL)==on);
        CHECK(realworld_materials_role(0x1FF8C329u,1,3,flags)==on);
        const float *table=on?racer:traffic;int i=0;while(px<table[i])++i;
        CHECK(M32(c.esp+0x14)==i && M32(c.esp+0x58)==i && !c.top);
    }
    printf("selected identity %08X type=%u render class2 glass/LOD fixture: %u checks, %u failures\n",key,type,checks-before,failures-errors);
}
''')

HEADER = r'''
#include "runtime.h"
#include "traffic_pilot.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
enum { MAN=0x20000, PAT=0x30000, LIST=0x40000, VT=0x50000,
       NATIVE=0x12345678, PILOT=0x87654321, OTHER=0xabcdef12 };
static unsigned checks,failures,records,reads,visited,max_index,diag_events;
static unsigned fixture_enabled=1,available=1,ready=1,force,mode,created;
static unsigned native_seen,pilot_seen,forced_checks,forced_failures,rng_calls;
static int diag_on;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; if(failures<5) fprintf(stderr,"line %d: %s\n",__LINE__,#x); } } while(0)
int rand(void) { ++rng_calls; return 1; }
void abi_violation(Cpu *c,uint32_t fn,uint32_t bx,uint32_t si,uint32_t di,uint32_t bp,uint32_t sp) {
    (void)c;(void)fn;(void)bx;(void)si;(void)di;(void)bp;(void)sp; CHECK(0);
}
void guest_divide_error(Cpu *c,uint32_t at) { (void)c;(void)at; abort(); }
int traffic_pilot_enabled(void) { return fixture_enabled; }
int traffic_pilot_available(void) { return available; }
int car85_traffic_ready(void) { return ready; }
uint32_t car85_traffic_donor_key(void) { return ready ? NATIVE : 0; }
uint32_t car85_traffic_key(void) { return ready ? PILOT : 0; }
TrafficPilotForce traffic_pilot_force(void) { return force; }
static void diag_event(const char *fmt,...) { (void)fmt; ++diag_events; }
void f_00426230(Cpu *c); void f_004253C0(Cpu *c);
void f_00426380(Cpu *c);
void f_00426497(Cpu *c);
void f_00439A10(Cpu *c);
void f_00426230_orig(Cpu *c); void f_004253C0_orig(Cpu *c);
void f_00403B70(Cpu *c) { c->eax=records; c->esp+=4; }
void f_00454810(Cpu *c) {
    unsigned i=M32(c->esp+8); CHECK(i<10); if(i>max_index)max_index=i;
    ++reads; c->eax=PAT+i*24; c->esp+=12;
}
void f_006269B0(Cpu *c) { c->eax=PAT+24*12; c->esp+=4; }
void f_00452430(Cpu *c) { c->eax=M32(c->ecx+4); c->esp+=4; }
void f_004040F0(Cpu *c) { c->esp+=4; }
void f_005CC240(Cpu *c) { c->eax=0; c->esp+=4; }
void f_0041EBD0(Cpu *c) { c->eax=1; c->esp+=4; }
void f_00437DC0(Cpu *c) { c->eax=1; c->esp+=8; }
void f_00403AF0(Cpu *c) { c->eax=0x70000; c->esp+=4; }
void f_00403B10(Cpu *c) { c->eax=0x70000; c->esp+=4; }
void f_00403B30(Cpu *c) { c->eax=0x70000; c->esp+=4; }
void f_00403B50(Cpu *c) { c->eax=PAT+M32(c->esp+4)*24; c->esp+=8; }
void f_0040E080(Cpu *c) { c->eax=0; c->esp+=8; }
void f_00401DA0(Cpu *c) { (void)c; abort(); }
void f_00404CF0(Cpu *c) { c->esp+=16; }
void f_004023C0(Cpu *c) { c->esp+=8; }
void f_00654AF0(Cpu *c) { c->esp+=8; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    unsigned sp=c->esp;
    for(int i=n-1;i>=0;--i){ c->esp-=4; M32(c->esp)=args[i]; }
    c->esp-=4; M32(c->esp)=0xfeedf00d;
    if(fn==0x403b70)f_00403B70(c);else if(fn==0x403b50)f_00403B50(c);else abort();
    c->esp=sp;return c->eax;
}
void f_005D59F0(Cpu *c) { (void)c; abort(); }
void dispatch(Cpu *c,uint32_t fn) {
    ++visited;
    switch(fn) {
    case 1: c->eax=M32(c->ecx+8); break; // IsActive
    case 2: c->eax=c->ecx; break;        // attribute instance
    case 3: c->eax=M32(c->ecx+4); break; // identity key
    case 4: c->eax=M32(c->ecx+12); break;// cached active availability
    case 5: c->eax=c->ecx; break;        // physics/simable fixture
    case 6: c->eax=1;c->esp+=4; break;   // spawn at road-nav fixture
    case 7: c->eax=0;break;             // activation fixture
    case 8: c->eax=0;break;
    case 9: ++created;c->eax=0;break;    // saturated physics factory fixture
    default: abort();
    }
    c->esp+=4;
}
static void reset(unsigned n) {
    memset(G,0,0xa00000); records=n; reads=visited=max_index=0;
    M32(MAN+0x94)=LIST; M32(LIST)=LIST;
    M32(VT+0x88)=1; M32(VT+0x48)=2; M32(VT+0x50)=3; M32(VT+0x5c)=4;
    M32(VT+0xac)=5; M32(VT+0x58)=6; M32(VT+0x80)=7; M32(VT+4)=5; M32(VT+0x70)=8;
    M32(MAN+0x3a4)=1;
    for(unsigned i=0;i<12;++i) {
        M32(PAT+i*24+4)=NATIVE; MF32(PAT+i*24+12)=1;
        MF32(MAN+0x64+i*4)=2;
    }
    fixture_enabled=available=ready=1; force=0; diag_on=0;
}
static void car(unsigned i,uint32_t key,unsigned active,unsigned reusable) {
    uint32_t node=LIST+32+i*32,v=0x60000+i*32;
    M32(node)=M32(LIST); M32(node+8)=v; M32(LIST)=node;
    M32(v)=VT; M32(v+4)=key; M32(v+8)=active; M32(v+12)=reusable;
}
static uint32_t select_car(int original) {
    Cpu c={.esp=0x90000,.ecx=MAN,.ebx=11,.esi=12,.edi=13,.ebp=14,.cw=0x37f};
    if(original) f_00426230_orig(&c); else f_00426230(&c);
    CHECK(c.esp==0x90004 && c.ebx==11 && c.esi==12 && c.edi==13 && c.ebp==14 && c.top==0);
    return c.eax;
}
static uint32_t cached(uint32_t key,int create) {
    Cpu c={.esp=0x90000,.ecx=MAN,.ebx=11,.esi=12,.edi=13,.ebp=14,.cw=0x37f};
    M32(c.esp+4)=key; M32(c.esp+8)=create;
    f_00426380(&c);
    CHECK(c.esp==0x9000c && c.ebx==11 && c.esi==12 && c.edi==13 && c.ebp==14);
    return c.eax;
}
'''

MAIN = r'''
int main(int argc,char **argv) {
    mode=argc>1 ? atoi(argv[1]) : 0; G=calloc(1,0xa00000); if(!G)return 2;
    // Premise: the original one-record selector can never return the added key.
    reset(1); unsigned old_native=0,old_pilot=0;
    for(unsigned i=0;i<16;++i) { uint32_t k=select_car(1); old_native+=k==NATIVE; old_pilot+=k==PILOT; }
    CHECK(old_native==16 && old_pilot==0);
    reset(1);
    for(unsigned i=0;i<16;++i) { uint32_t k=select_car(mode==1); native_seen+=k==NATIVE; pilot_seen+=k==PILOT; }
#if NFS_PUBLIC
    CHECK(native_seen==16 && pilot_seen==0);
#else
    CHECK(native_seen==8 && pilot_seen==8);
#endif
    // Both forced subjects use actual eligible sedan opportunities, no RNG.
    for(unsigned subject=1;subject<=2;++subject) {
        reset(1); force=subject; diag_on=1;
        for(unsigned i=0;i<8;++i) {
            uint32_t key=select_car(mode==1);
#if NFS_PUBLIC
            CHECK(key==NATIVE);
#else
            unsigned ok=key==(subject==1?NATIVE:PILOT);
            ++forced_checks; forced_failures+=!ok; CHECK(ok);
#endif
        }
    }
    // Empty, one, ten and >ten: no appended/displaced pattern and same cursor.
    for(unsigned n=0;n<=12;++n) for(unsigned start=0;start<13;++start) {
        reset(n); M32(MAN+0x60)=start;
        for(unsigned i=0;i<12;++i) MF32(MAN+0x64+i*4)=0;
        if(n) { M32(PAT+(n-1)*24+4)=OTHER; MF32(MAN+0x64+(n-1)*4)=2; }
        uint32_t key=select_car(mode==1);
        CHECK(key==((n && n<=10)?OTHER:0));
        CHECK(reads<=(n<10?n:10)); CHECK(max_index<10);
        CHECK(M32(MAN+0x60)==(n ? (n<=10 ? n : (start%10+9)%10+1) : start));
    }
    // Disabled, unavailable, retired/reloaded vaults preserve the retail CPU,
    // cursor and identity, even while a pilot from a previous lifetime is active.
    for(unsigned state=0;state<4;++state) {
        reset(1); car(0,PILOT,1,0); M32(PAT+16)=1;
        if(state==0)fixture_enabled=0; if(state==1)available=0; if(state==2)ready=0;
        uint32_t key=select_car(mode==1);
        CHECK(key==((state<3 || NFS_PUBLIC)?NATIVE:0));
    }
    for(unsigned state=0;state<3;++state) {
        reset(1);car(0,PILOT,1,0);M32(PAT+16)=1;
        if(state==0)fixture_enabled=0;if(state==1)available=0;if(state==2)ready=0;
        Cpu a={.esp=0x90000,.ecx=MAN,.ebx=11,.esi=12,.edi=13,.ebp=14,.cw=0x37f},b=a;
        f_00426230_orig(&a);unsigned cursor=M32(MAN+0x60);M32(MAN+0x60)=0;
        f_00426230(&b);
        CHECK(!memcmp(&a,&b,sizeof a));CHECK(M32(MAN+0x60)==cursor);
    }
    // Cross-product of active/inactive native+pilot cache populations, fixed
    // caps, percentage-derived caps and their intersection. The translated
    // TypeCounter ignores inactive entries and counts reusable active entries.
    for(unsigned a=0;a<4;++a) for(unsigned b=0;b<4;++b)
    for(unsigned fixed=0;fixed<5;++fixed) for(unsigned percent=0;percent<=50;percent+=10)
    for(unsigned pop=0;pop<23;pop+=11) {
        reset(1); M32(PAT+16)=fixed; M32(PAT+20)=percent;
        M32(MAN+0x98)=pop+7; M32(0x92cd24)=27;
        for(unsigned i=0;i<a;++i)car(i,NATIVE,1,i&1);
        for(unsigned i=0;i<b;++i)car(i+4,PILOT,1,i&1);
        car(8,NATIVE,0,1); car(9,PILOT,0,1); car(10,OTHER,1,1);
        unsigned count=a+(NFS_PUBLIC?0:b),cap=pop*percent/100;if(!cap)cap=1;
        unsigned allowed=(!fixed||count<fixed)&&(!percent||count<cap);
        uint32_t key=select_car(mode==1);
        CHECK(allowed ? (key==NATIVE || (!NFS_PUBLIC && key==PILOT)) : key==0);
    }
    // Keep retail's 32-bit product/truncation, including minimum-one and wrap,
    // instead of substituting the policy helper's wider advisory arithmetic.
    for(unsigned p=0;p<4;++p)for(unsigned q=0;q<4;++q) {
        reset(1);unsigned pop=p==3?UINT32_MAX:(p==2?20:p);
        unsigned percent=q==3?UINT32_MAX:(q==2?100:q);
        M32(MAN+0x98)=pop;M32(0x92cd24)=20;M32(PAT+20)=percent;
        car(0,NATIVE,1,0);car(1,PILOT,1,0);
        unsigned cap=(uint32_t)(pop*percent)/100;if(cap<2)cap=1;
        unsigned count=NFS_PUBLIC?1:2,allowed=!percent||count<cap;
        CHECK((select_car(mode==1)!=0)==allowed);
    }
    // The generic counter's cdecl arguments and initial count remain original
    // outside NextSpawn, even if the same collection key is counted elsewhere.
    reset(1);car(0,NATIVE,1,0);car(1,PILOT,1,0);car(2,NATIVE,0,0);
    for(unsigned active=0;active<2;++active) {
        Cpu c={.esp=0x90000,.ebx=11,.esi=12,.edi=13,.ebp=14};
        M32(c.esp)=0x1234;M32(c.esp+4)=0x71000;M32(c.esp+8)=M32(LIST);
        M32(c.esp+12)=LIST;M32(c.esp+16)=NATIVE;M32(c.esp+20)=active;M32(c.esp+24)=7;
        f_004253C0(&c);
        CHECK(M32(0x71000)==NATIVE && M32(0x71004)==active && M32(0x71008)==9-active);
        CHECK(c.esp==0x90004 && c.ebx==11 && c.esi==12 && c.edi==13 && c.ebp==14);
    }
    // Rejected native record must permit the next unchanged candidate.
    reset(10); car(0,PILOT,1,0); M32(PAT+16)=1;
    M32(PAT+24+4)=OTHER;
    CHECK(select_car(mode==1)==(NFS_PUBLIC?NATIVE:OTHER));
    // Zero and ineligible thresholds remain zero, including forced diagnostics.
    for(unsigned subject=0;subject<=2;++subject) for(int chance=-1;chance<=2;++chance) {
        reset(1); force=subject; MF32(PAT+12)=chance;
        CHECK((select_car(mode==1)!=0)==(chance==1));
    }
    reset(1); force=2; M32(PAT+4)=OTHER; CHECK(select_car(mode==1)==OTHER);
    // Identity-keyed cache reuse executes unchanged translated guest code.
    for(unsigned act=0;act<2;++act) for(unsigned reuse=0;reuse<2;++reuse) {
        reset(1);car(0,NATIVE,act,reuse);car(1,PILOT,act,reuse);
        CHECK(cached(NATIVE,0)==((!act||reuse)?0x60000:0));
        CHECK(cached(PILOT,0)==((!act||reuse)?0x60020:0));
        CHECK(cached(OTHER,0)==0); CHECK(cached(0,0)==0);
        // Full/absent factory: no alternate identity is substituted.
        CHECK(cached(OTHER,1)==0);
    }
    CHECK(created==0);
    // Twenty inactive native cache entries do not authorize key substitution.
    // Execute the translated factory tail too, with a saturated factory result.
    reset(1);force=2;M32(PAT+16)=2;M32(0x92cd24)=20;
    for(unsigned i=0;i<20;++i)car(i,NATIVE,0,0);
    uint32_t selected=select_car(mode==1);
    CHECK(selected==((NFS_PUBLIC || mode==1)?NATIVE:PILOT));
    M32(0x92c66c)=0x72000;M32(0x72000)=0;M32(0x72004)=9;
    CHECK(cached(PILOT,1)==0);CHECK(created==1);
    CHECK(cached(NATIVE,0)!=0);
    // Diagnostic readiness failure refuses a mismatched subject explicitly.
    reset(1);force=2;ready=0;diag_on=1;
    CHECK(select_car(mode==1)==((NFS_PUBLIC || mode==1)?NATIVE:0));
    if(!NFS_PUBLIC && mode!=1)CHECK(diag_events>0);
    // Execute the complete translated SpawnTraffic around the production
    // diagnostic wrapper. Cached pilot/native cars take exactly the retail
    // success path; only matching sedan timers reset, including duplicate rows.
    for(unsigned subject=1;subject<=2;++subject) for(unsigned success=0;success<2;++success) {
        reset(12); force=subject; MF32(MAN+0x8c)=1;
        for(unsigned i=0;i<12;++i) M32(PAT+i*24+4)=(i==0||i==4||i==10)?NATIVE:OTHER;
        if(success) { car(0,NATIVE,0,0); car(1,PILOT,0,0); }
        Cpu c={.esp=0x90000,.ecx=MAN,.ebx=11,.esi=12,.edi=13,.ebp=14,.cw=0x37f};
        if(mode==1)f_00439A10_orig(&c);else f_00439A10(&c);
        CHECK((c.eax&255)==success);
        CHECK(c.esp==0x90004 && c.ebx==11 && c.esi==12 && c.edi==13 && c.ebp==14 && !c.top);
        CHECK(MF32(MAN+0x64)==(success?0:2));
        CHECK(MF32(MAN+0x74)==(success?0:2));
        CHECK(MF32(MAN+0x68)==2 && MF32(MAN+0x8c)==1);
    }
    printf("retail premise: native=%u pilot=%u; selected: native=%u pilot=%u; forced=%u/%u failures\n",
        old_native,old_pilot,native_seen,pilot_seen,forced_failures,forced_checks);
    CHECK(rng_calls==0);
    printf("traffic spawn mode=%u public=%u: %u checks, %u failures\n",mode,NFS_PUBLIC,checks,failures);
    if(native_seen)render_subject(NATIVE);
    if(pilot_seen)render_subject(PILOT);
    free(G);return failures!=0;
}
'''

ORIGINAL_HOOKS = '''
void f_00439A10_orig(Cpu *c);
void f_004253C0(Cpu *c) { f_004253C0_orig(c); }
void f_00426230(Cpu *c) { f_00426230_orig(c); }
void f_00439A10(Cpu *c) { f_00439A10_orig(c); }
'''


def compile_unit(unit, exe, body, public):
    unit.write_text('\n'.join((HEADER, policy, body, guest, ROLE, MAIN)))
    p = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined',
                        '-DRECOMP_ABICHECK=1', f'-DNFS_PUBLIC={public}',
                        '-I', str(RT), '-I', str(ROOT / 'recomp'), str(unit), '-o', str(exe)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-4000:]


with tempfile.TemporaryDirectory(prefix='traffic-spawn-') as td:
    unit = Path(td) / 'check.c'
    exe = Path(td) / 'check'
    for public in (0, 1):
        for control in ((0, 1) if not public else (0,)):
            compile_unit(unit, exe, ORIGINAL_HOOKS if control else hooks, public)
            p = subprocess.run([str(exe), str(control)], capture_output=True, text=True)
            print(p.stdout.strip())
            if control:
                assert p.returncode == 1, 'retail control unexpectedly passed or crashed'
                assert 'pilot=0; forced=8/16 failures' in p.stdout
            else:
                assert p.returncode == 0, p.stderr[-4000:]
        if not public:
            for name, old, new in (
                ('shared count removed', 'M32(out + 8) += extra_count;', '(void)extra_count;'),
                ('success timer reset removed', 'traffic_spawn_reset(c, &spawn);', '(void)spawn;'),
            ):
                assert hooks.count(old) == 1
                broken = hooks.replace(old, new)
                compile_unit(unit, exe, broken, 0)
                p = subprocess.run([str(exe), '0'], capture_output=True, text=True)
                assert p.returncode == 1, name + ': control passed or crashed: ' + p.stderr[-1000:]
                result = next(line for line in p.stdout.splitlines() if line.startswith('traffic spawn mode='))
                print(name + ': ' + result)

# Hook registry: one strong adapter and one generated original per address.
registry = (ROOT / 'recomp/hooks.txt').read_text()
for address in ('00426230', '004253C0', '00439A10'):
    assert len(re.findall(r'^' + address + r'\s', registry, re.M)) == 1
    assert len(re.findall(r'^void f_' + address + r'\(Cpu \*c\) \{', module, re.M)) == 1
print('traffic spawn hook registry: 3 unique adapters; SpawnTraffic retained')
