"""Production grip bridge, real generated originals and admitted source solver.

Generated originals: 18/18 CPU/FPU + 16 MiB snapshots PASS; changed originals
FAIL 3/3. CPU/bytes/helpers mutants each FAIL 18/18; wrong admission FAIL 1/1.
Zero scale originally admitted (0/1 refusals); fixed singular admission 12/12.
Native fallback at 10 m/s: zero-scale/overflow refusals stock and finite 6/6;
original inactive-only restoration FAIL 4/4; nested donor 0/2 -> 2/2.
Original Off helper/TLS check FAIL 1/1;
fixed unset/Off paths have zero extra calls/accesses, omission controls FAIL 6/6.
Off assembly frame/helper writes: intermediate 1/3 -> fixed 3/3 tail calls.
Integration/lifetime: 12/12 PASS; 15/15 omitted-term controls FAIL.
Hook/link/default registry: 8/8 PASS; omission/default controls FAIL 8/8.
F10 toggle: 8/8 cases, 185/185 checks PASS. Deliberate mid-scope apply:
FAIL 4/4 cases (15 failed assertions); setting/lifetime/startup controls FAIL 7/7.
Integration orchestration uses explicit doubles; all bypass snapshots and native
refusal forces use full SHA-256-pinned generated originals. Native scalar
constants come from the local PC executable; no game runs. Bare trees exit 3
for that part after the self-contained admission/solver checks finish.
"""
from pathlib import Path
import os
import hashlib
import struct
import sys
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#include "host.h"
#include "car_grip.h"
#include "settings.h"
#include <assert.h>
#include <stdlib.h>
#include <math.h>
#include <float.h>
uint8_t *G;
int diag_on;
const char *game_dir;
static char saved_pref[8];
const char *app_pref(const char *key) { return !strcmp(key,"cars.added_grip") ? saved_pref : NULL; }
void app_pref_set(const char *key,const char *value) {
    if (!strcmp(key,"cars.added_grip")) snprintf(saved_pref,sizeof saved_pref,"%s",value);
}
void report_note(const char *fmt,...) { (void)fmt; }
void diag_event(const char *fmt,...) { (void)fmt; }
static unsigned helpers, allocations, releases, calls, checks, fails, original_wheels, drift_calls;
static uint32_t arena=0xb00000;
static uint32_t vault_address=0x4000;
static uint64_t generation=7;
typedef struct { uint32_t address; uint64_t generation; int ready; } VaultLife;
static VaultLife vault_life={0x4000,7,1};
static VaultLife *life(uint32_t v) { return v==vault_life.address ? &vault_life : NULL; }
typedef struct { uint32_t catalog_id,key; CarGrip grip; CarGripBinding grip_binding; } Addition;
static Addition additions[1];
static unsigned nadditions=1;
static int catalog_mode=1, enabled=1;
ADMISSION
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); } } while(0)
uint32_t gmalloc(uint32_t n) { ++allocations; uint32_t a=arena; arena+=(n+63)&~63u; assert(arena<0xf00000); return a; }
void gfree(uint32_t a) { (void)a; ++releases; }
static unsigned env_calls, scope_accesses;
static char *bridge_getenv(const char *name) { ++env_calls; return getenv(name); }
static unsigned nested, nesting, expire, reuse, force_lock;
static unsigned toggle_in_scope;
static uint32_t other_suspension=0x7000;
static float applied_torque;
static unsigned test_level,test_junk,last_event;
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    (void)n; ++helpers;
    if(fn==0x00672D30) return test_level;
    if(fn==0x00672E80) return 8;
    if(fn==0x00454810) {
        if(a[0]==0xCDC136E8) return test_junk?0x12000:0;
        if(a[0]==0x171737E9) return 0x12010;
        if(a[0]==0xC5860F58 && a[1]==0) return 0x12020;
        return 0;
    }
    if(fn==0x00456CB0 || fn==0x0045A430) return 0;
    if(fn==0x005CB5D0) { float x,y; memcpy(&x,a,4); memcpy(&y,a+1,4); x87_push(c,atan2f(x,y)/6.2831855f); return 0; }
    if(fn==0x0068E220) { M8(c->ecx+0x134)=force_lock; if(force_lock) MF32(c->ecx+0xd0)=0; return 0; }
    if(fn==0x0068E090) {
        CHECK(MF32(M32(M32(c->ecx+0x100)+8)+0x18+4*M32(c->ecx+0x10c))==1);
        x87_push(c,100); return 0;
    }
    if(fn==0x0068E2E0) { ++calls; return 0; }
    if(fn==0x123456) { applied_torque=MF32(a[0]+4); CHECK(last_event==1); last_event=2; return 0; }
    fprintf(stderr,"bad helper %x\n",fn); abort();
}
#define enabled bridge_enabled
#define getenv bridge_getenv
#include "car_grip_bridge.c"
#undef getenv
#undef enabled
static void orig(Cpu *c,unsigned id,unsigned args) {
    M32(0x200+id*4)^=c->ecx+id;
    c->eax^=0x12345678+id; c->edx+=id; c->sw^=0x100; c->st[c->top]+=id;
    c->esp+=4+args*4;
}
void f_006A9760_orig(Cpu *c) { ++drift_calls; last_event=1; orig(c,2,1); }
void f_0069DA90_orig(Cpu *c) {
    if(scope && expire) CHECK(M32(scope->suspension+0xa0)==scope->native_layout);
    ++original_wheels; orig(c,1,5);
}
void f_006AA020_orig(Cpu *c) {
    /* A bypassed recursive call hides TLS while its parent's scope is live. */
    if (toggle_in_scope && (!nested || nesting)) {
        GripScope *active=scope;
        CarGripSelected cached={0};
        if(active) cached=active->selected;
        CHECK(settings_set(settings_find("cars.added_grip"),0));
        CHECK(grip_on==1);
        if(toggle_in_scope==2) {
            CHECK(settings_set(settings_find("cars.added_grip"),1));
            CHECK(settings_set(settings_find("cars.added_grip"),0));
            CHECK(grip_on==1);
        }
        for(unsigned i=0;i<5;++i) for(unsigned j=0;j<additions[0].grip.tiers[0].fields[i].count;++j)
            additions[0].grip.tiers[0].fields[i].values[j]=9;
        CHECK(scope==active);
        if(active) CHECK(!memcmp(&cached,&active->selected,sizeof cached));
        toggle_in_scope=0;
    }
    if(scope) {
        uint32_t sus=c->ecx,state=arg(c,0),native=scope->native_layout;
        CHECK(M32(sus+0xa0)==scope->shadow && M32(sus+0xa0)!=native);
        CHECK(!memcmp(G+scope->shadow+0x28,G+native+0x28,16));
        CHECK(MF32(scope->shadow+0x48)==MF32(native+0x48));
        if(nested && !nesting) {
            GripScope *outer=scope; Cpu n=*c; nesting=1;
            n.ecx=nested==2?c->ecx:other_suspension; f_006AA020(&n); nesting=0;
            CHECK(scope==outer && M32(sus+0xa0)==outer->shadow);
            CHECK(grip_on==1);
        }
        Cpu q=*c; f_006A9760(&q);
        if(expire) { ++vault_life.generation; additions[0].grip_binding.generation=vault_life.generation; }
        if(reuse) M32(sus+0xa0)=0x8100;
        for(unsigned i=0;i<4;++i) {
            q=*c; q.ecx=M32(sus+0x1b4+4*i); q.esp-=32;
            MF32(q.esp+4)=3; MF32(q.esp+8)=10; MF32(q.esp+12)=30;
            MF32(q.esp+16)=1000; MF32(q.esp+20)=.01f;
            uint32_t sp=q.esp; f_0069DA90(&q); CHECK(q.esp==sp+24);
        }
        CHECK(state==scope->state);
    }
    orig(c,0,1);
}
static void fixture(void) {
    memset(G,0,0x1000000); arena=0xb00000;
    helpers=allocations=releases=calls=original_wheels=drift_calls=0; applied_torque=0;
    nested=nesting=expire=reuse=force_lock=toggle_in_scope=0; test_level=test_junk=last_event=0; scope=NULL; setenv("NFS_ADDCAR_GRIP","on",1); car_grip_init();
    vault_life=(VaultLife){vault_address,generation,1};
    additions[0].catalog_id=1; additions[0].key=0x1122;
    CarGrip *g=&additions[0].grip; memset(g,0,sizeof *g); g->present=1; g->source=1; g->donor=2; g->tier_count=1;
    uint32_t keys[5]={0xAAFC01B3,0xEC096166,0xF177BE1B,0x64C43C4B,0xC51EB1A1};
    for(unsigned i=0;i<5;++i) {
        CarGripField *f=&g->tiers[0].fields[i]; f->key=keys[i]; f->semantics=i==4?0:1;
        f->count=i<3?2:i==3?4:9;
        for(unsigned j=0;j<f->count;++j) f->values[j]=i==4?2:1;
    }
    car_grip_bind(&additions[0].grip_binding,g,0x1122,vault_address,generation);
    M32(0x1034)=0x2000; M32(0x2000)=0x3000; M32(0x3044)=0x00686A40;
    M32(0x2058)=0x5000; M32(0x4ff8)=0x2000;
    M32(0x0092D87C)=0x5100; M32(0x0092D884)=1; M32(0x5100)=0x5000;
    M32(0x203c)=0x6000; M32(0x6020)=0x1122; M32(0x6024)=vault_address;
    M32(0x10a0)=0x8000; memset(G+0x8000,0x55,0x50);
    M32(0x008F87A8)=0xBD38D1CA; M32(0x008F87B4)=0xC5860F58;
    M32(0x12000)=1; M32(0x12024)=0xF177BE1B; MF32(0x12028)=.5f;
    MF32(0x008F8AB0)=10; MF32(0x008F8AAC)=2;
    MF32(0x9010)=0; MF32(0x9014)=1; MF32(0x9018)=0;
    MF32(0x904c)=1; MF32(0x9094)=.5f; MF32(0x909c)=.1f;
    MF32(0x90a4)=100; MF32(0x90bc)=40; M16(0x90cc)=2;
    MF32(0x1108)=.5f; M32(0x10e8)=0xa000; M32(0xa000)=0xa100; M32(0xa194)=0x123456;
    for(unsigned i=0;i<4;++i) {
        uint32_t t=0x10000+0x200*i; M32(0x11b4+4*i)=t;
        M32(t+0x100)=0x1098; M32(t+0x104)=0x11000; M32(0x11008)=0x11100;
        M32(t+0xa4)=0x11200; M32(t+0x10c)=i/2;
        MF32(t+0xc4)=.5f; MF32(t+0xd0)=20; MF32(t+0xfc)=10;
        MF32(t+0x110)=1; MF32(t+0xe8)=1; MF32(t+0xec)=1;
        MF32(t+0xe0)=100; MF32(t+0x124)=100; MF32(t+0x128)=1; MF32(t+0x12c)=1;
    }
    MF32(0x112e4)=.5f; MF32(0x112ec)=MF32(0x112f0)=1;
    M32(0x800000)=0; M32(0x800004)=0x9000;
}
static Cpu cpu(unsigned hook) {
    Cpu c; memset(&c,0xa6,sizeof c); c.esp=0x800000; c.ecx=hook==1?0x10000:0x1000;
    c.top=3; c.sw=0x400; c.cw=0x27f;
    for(unsigned i=0;i<8;++i) c.st[i]=i*.25;
    return c;
}
int main(int argc,char **argv) {
    assert(argc==2); G=calloc(1,0x1000000); assert(G);
    int target=atoi(argv[1]); int explicit_off=target>=30 && target<33;
    if(explicit_off) target-=30;
    if(target<15) {
        fixture(); unsigned kind=target/3,hook=target%3;
        if(kind==0) { if(explicit_off) setenv("NFS_ADDCAR_GRIP","off",1); else unsetenv("NFS_ADDCAR_GRIP"); }
        if(kind==1) M32(0x6020)=0x3344;
        if(kind==2) M32(0x2058)=0;
        if(kind==3) M32(0x4ff8)=0x2200;
        /* Production negative control: another car has an admitted binding. */
        if(kind==4) { additions[0].key=0x7788; additions[0].grip_binding.key=0x7788; }
        car_grip_init();
        if(kind==0) setenv("NFS_ADDCAR_GRIP","on",1); /* Cached Off stays Off. */
        uint8_t *before=malloc(0x1000000),*expected=malloc(0x1000000);
        memcpy(before,G,0x1000000); Cpu a=cpu(hook),b=a;
        void (*direct[])(Cpu *)={f_006AA020_orig,f_0069DA90_orig,f_006A9760_orig};
        void (*bridge[])(Cpu *)={f_006AA020,f_0069DA90,f_006A9760};
        env_calls=scope_accesses=0;
        direct[hook](&a); unsigned expected_scope=scope_accesses;
        memcpy(expected,G,0x1000000); memcpy(G,before,0x1000000);
        env_calls=scope_accesses=0;
        bridge[hook](&b);
        if(kind==0) CHECK(!env_calls && scope_accesses==expected_scope);
        CHECK(!memcmp(&a,&b,sizeof a)); CHECK(!memcmp(expected,G,0x1000000));
        CHECK(!helpers && !allocations && !calls); CHECK(scope==NULL);
        free(before); free(expected);
    } else if(target>=50) {
        fixture(); unsetenv("NFS_ADDCAR_GRIP");
        if(target==60 || target==62) strcpy(saved_pref,"on");
        if(target==61 || target==63) strcpy(saved_pref,"off");
        if(target==62) setenv("NFS_ADDCAR_GRIP","off",1);
        if(target==63) setenv("NFS_ADDCAR_GRIP","on",1);
        settings_init(); car_grip_init();
        Setting *row=settings_find("cars.added_grip");
        CHECK(row && settings_count()==1);
        CHECK(row->category==SET_CARS && row->type==SET_TOGGLE && row->def==0);
        CHECK(!strcmp(row->label,"Added cars' own grip"));
        CHECK(!strcmp(row->help,"Added cars use the grip and steering help they were built with. Off keeps the borrowed grip of the car they replace. Takes effect from the next race or car change."));
        CHECK(!strcmp(row->env,"NFS_ADDCAR_GRIP") && !strcmp(row->values[0],"off") && !strcmp(row->values[1],"on"));
        CHECK(row->apply && !row->restart && !row->get);
        if(target>=60) {
            int expected=target==60 || target==63;
            CHECK(grip_on==expected && row->value==expected);
            CHECK(row->locked==(target==62 || target==63));
            if(row->locked) CHECK(!settings_set(row,!expected) && grip_on==expected);
        } else {
            CHECK(!grip_on && row->value==0 && !row->locked);
            CHECK(settings_set(row,1) && grip_on==1 && !strcmp(saved_pref,"on"));
            toggle_in_scope=target==53?2:1;
            nested=target==51?1:target==52?2:0;
            Cpu c=cpu(0); f_006AA020(&c);
            CHECK(!toggle_in_scope && !scope && !grip_depth && !grip_on);
            CHECK(row->value==0 && !strcmp(saved_pref,"off"));
            CHECK(allocations==releases && M32(0x10a0)==0x8000);
            CHECK(calls==(target==52?8:4) && original_wheels==0 && applied_torque==50);
            CHECK(fabsf(MF32(0x100d8)+100)<1e-3);
            unsigned before_helpers=helpers,before_allocations=allocations;
            c=cpu(0); f_006AA020(&c);
            CHECK(helpers==before_helpers && allocations==before_allocations);
            CHECK(settings_set(row,1) && grip_on==1);
            CHECK(settings_set(row,0) && !grip_on);
        }
    } else if(target==25) {
        fixture();
        float invalid[]={0,-0.0f,0x1p-149f,-1,NAN,INFINITY};
        for(unsigned axle=0;axle<2;++axle) for(unsigned k=0;k<6;++k) {
            GripScope candidate={0};
            for(unsigned i=0;i<CAR_GRIP_SELECTED_FIELDS;++i)
                candidate.selected.fields[i]=additions[0].grip.tiers[0].fields[i];
            candidate.selected.fields[2].values[axle]=invalid[k];
            CHECK(!make_overlay(&candidate));
        }
    } else {
        fixture(); nested=target==24?2:target==16; expire=target==17;
        if(target==26) setenv("NFS_ADDCAR_GRIP","off",1); /* Cached On stays On. */
        if(target==19) {
            test_level=4; CarGrip *g=&additions[0].grip; g->tier_count=3;
            g->tiers[1]=g->tiers[0]; g->tiers[2]=g->tiers[0];
            for(unsigned i=0;i<5;++i) for(unsigned j=0;j<g->tiers[0].fields[i].count;++j) {
                g->tiers[1].fields[i].values[j]*=2; g->tiers[2].fields[i].values[j]*=4;
            }
        }
        test_junk=target==20; force_lock=target==22; reuse=target==23;
        if(target==21) for(unsigned i=0;i<4;++i) {
            uint32_t t=M32(0x11b4+4*i); MF32(t+0x110)=.5f; MF32(t+0xd0)=40; MF32(t+0xd4)=1000;
        }
        Cpu c=cpu(0); uint8_t layout[0x50]; memcpy(layout,G+0x8000,0x50);
        f_006AA020(&c);
        CHECK(scope==NULL); CHECK(allocations==releases); CHECK(M32(0x10a0)==(reuse?0x8100:0x8000)); CHECK(!memcmp(layout,G+0x8000,0x50));
        CHECK(helpers>0 && allocations>0); CHECK(applied_torque==(target==19?150:50));
        if(expire || reuse) CHECK(calls==0 && original_wheels==4);
        else { CHECK(calls==(target==24?8:4)); CHECK(original_wheels==0); CHECK(fabsf(MF32(0x100d8)+(target==19?300:target==20?50:target==21?287.347885f:target==22?1000:100))<1e-3); }
        if(target==18) {
            /* Same addresses, new lifetime; old binding must refuse before helpers. */
            ++vault_life.generation; helpers=allocations=0;
            c=cpu(0); f_006AA020(&c); CHECK(!helpers && !allocations);
            car_grip_bind(&additions[0].grip_binding,&additions[0].grip,0x1122,vault_address,vault_life.generation);
            drift_calls=0; c=cpu(0); f_006AA020(&c); CHECK(helpers>0);
        }
    }
    printf("bridge case %d: %u checks, %u failures\n",target,checks,fails);
    free(G); return fails!=0;
}
'''


def compile_run(tmp, text, case, tag, wrong_admission=False, harness=HARNESS):
    text=text.replace('static _Thread_local GripScope *scope;', 'static _Thread_local GripScope *scope;\nstatic GripScope **test_scope_access(void) { ++scope_accesses; return &scope; }\n#define scope (*test_scope_access())')
    bridge=tmp/'car_grip_bridge.c'; bridge.write_text(text)
    car=(ROOT/'runtime/car85.c').read_text()
    admission=car[car.index('static const CarGripBinding *live_grip_binding'):car.index('void car85_vault_unloaded')]
    if wrong_admission:
        assert 'a->key!=key' in admission
        admission=admission.replace('a->key!=key','0',1)
    (tmp/'harness.c').write_text(harness.replace('ADMISSION',admission))
    exe=tmp/tag
    p=subprocess.run(['clang','-std=c11','-O0' if 'baseline_006AA020' in harness else '-O1','-g','-fsanitize=address,undefined','-Werror','-Wall','-Wextra','-Wno-parentheses-equality',
        '-I'+str(tmp),'-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),str(tmp/'harness.c'),
        *[str(ROOT/'runtime'/('car_grip'+s+'.c')) for s in ('','_force','_wheel','_assist')],
        str(ROOT/'runtime/settings.c'),str(ROOT/'runtime/offline_stubs.c'),
        '-o',str(exe)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr[-3500:]
    results=[]
    for i in case:
        p=subprocess.run([str(exe),str(i)],capture_output=True,text=True,timeout=10)
        results.append(p)
    return results


# Full generated originals, checked independently of the wrapper under test.
NATIVE_DIGESTS = {
    '006AA020': '5810b8067dc2b5ab81e6094bfa9133ffd4aa45b4b2f24afa5299b87380194c63',
    '0069DA90': 'e9f481b42f52f3d7e183d599d3556cf18ae5fda52779e50efa8390be18159b25',
    '006A9760': '46a9c2717866b10fb1374fb722a57e146ea3453a6e7141179dbf35b4183a568d',
}
NATIVE_SUPPORT = r"""
#include "funcs.h"
void bad_jump(Cpu *c,uint32_t at) { (void)c; fprintf(stderr,"bad native jump %x\n",at); abort(); }
void dispatch(Cpu *c,uint32_t target) {
    if(target==0x111111) { c->eax=0; c->esp+=4; return; }
    if(target==0x222222) { c->esp+=8; return; }
    fprintf(stderr,"bad native dispatch %x at %x\n",target,M32(c->esp)); abort();
}
/* External steering/tuning and body interfaces are isolated here. The three
 * functions being compared, including the native divide, are never doubles. */
void f_0069E9E0(Cpu *c) { c->esp+=16; }
void f_006A9B40(Cpu *c) { c->esp+=8; }
void f_00671530(Cpu *c) { c->esp+=40; }
void f_006E8DE0(Cpu *c) { x87_push(c,1); c->esp+=4; }
void f_007C45F0(Cpu *c) { abort(); (void)c; }
void f_0068E370(Cpu *c) { MF32(c->ecx+0xd4)=0; c->esp+=8; }
void f_005CB5D0(Cpu *c) { x87_push(c,atan2f(argf(c,0),argf(c,1))/6.2831855f); c->esp+=4; }
void f_0068E090(Cpu *c) {
    uint32_t layout=M32(M32(c->ecx+0x100)+8);
    x87_push(c,100*MF32(layout+0x18+4*M32(c->ecx+0x10c))); c->esp+=12;
}
void f_0046F7C0(Cpu *c) { c->eax=0; c->esp+=12; }
void f_006533B0(Cpu *c) { c->eax=0; c->esp+=8; }
"""


def generated_function(address, original=False):
    name='f_'+address+('_orig' if original else '')
    for path in sorted((ROOT/'recomp/out').glob('speed_*.c')):
        source=path.read_text()
        found=re.search(r'void '+name+r'\(Cpu \*restrict c\) \{.*?\n}\n',source,re.S)
        if found:
            return found.group()
    raise AssertionError('missing generated original: '+name)


def native_checks(tmp, text):
    executable=Path(os.environ.get('NFS_GAME',ROOT.parent/'Need For Speed Most Wanted Black Edition'))/'speed.exe'
    if not executable.is_file() or not (ROOT/'recomp/out/funcs.h').is_file():
        print('SKIP: generated-original fixtures need local translation and PC executable')
        return False
    originals={a:generated_function(a,True) for a in NATIVE_DIGESTS}
    for a,body in originals.items():
        assert hashlib.sha256(body.encode()).hexdigest()==NATIVE_DIGESTS[a],a
    helpers=''.join(generated_function(a) for a in (
        '00401B80','00401D10','0040FE20','005C5E80','0068E180','0068E220','0068E2E0'))
    # Read only the scalar constants the translated functions actually name.
    # Their bytes stay in the disposable harness, never in the source tree.
    image=executable.read_bytes()
    pe=struct.unpack_from('<I',image,0x3c)[0]
    count=struct.unpack_from('<H',image,pe+6)[0]
    optional=struct.unpack_from('<H',image,pe+20)[0]
    base=struct.unpack_from('<I',image,pe+52)[0]
    sections=[]
    for i in range(count):
        at=pe+24+optional+40*i
        size,rva,raw_size,raw=struct.unpack_from('<IIII',image,at+8)
        sections.append((base+rva,raw_size,raw))
    constants=[]
    combined=''.join(originals.values())+helpers
    for width,address in sorted(set(re.findall(r'MF(32|64)\(\(uint32_t\)\(0x([0-9A-Fa-f]+)u\)\)',combined))):
        address=int(address,16); size=int(width)//8
        matches=[raw+address-start for start,n,raw in sections if start<=address and address+size<=start+n]
        assert len(matches)<=1,hex(address)
        value=int.from_bytes(image[matches[0]:matches[0]+size],'little') if matches else 0
        constants.append(f'M{width}(0x{address:x})=0x{value:x}'+('ULL' if width=='64' else 'u')+';')
    support=NATIVE_SUPPORT+helpers
    baseline=''.join(body.replace('f_'+a+'_orig(', 'baseline_'+a+'(',1) for a,body in originals.items())
    harness=HARNESS.replace('static uint32_t other_suspension=0x7000;', '')
    begin=harness.index('static void orig('); end=harness.index('static void fixture(void)')
    harness=harness[:begin]+support+'ORIGINALS\n'+baseline+harness[end:]
    # Set valid donor tyres, external interfaces, wheel inputs and native constants.
    needle='    MF32(0x112e4)=.5f;'
    setup='\n'.join(constants)+r"""
    M32(0x1048)=0xa000; M32(0xa0cc)=0x111111;
    M32(0xa1cc)=0x111111; M32(0xa184)=M32(0xa188)=M32(0xa194)=0x222222;
    M32(0x10c8)=0x15000; M32(0x10cc)=0x15000;
    MF32(0x90c0)=.01f; MF32(0x90bc)=10; MF32(0x909c)=.01f;
    M32(0x1104)=0; MF32(0x1108)=0;
    for(unsigned i=0;i<2;++i) {
        MF32(0x8018+4*i)=1; MF32(0x8020+4*i)=1; MF32(0x8038+4*i)=1;
    }
    for(unsigned i=0;i<4;++i) {
        uint32_t t=M32(0x11b4+4*i); MF32(t+0x13c)=1;
        MF32(t+0x11c)=MF32(t+0x120)=1;
    }
"""
    harness=harness.replace(needle,setup+needle)
    harness=harness.replace('Cpu a=cpu(hook),b=a;',r"""Cpu a=cpu(hook),b=a;
        if(hook==1) {
            MF32(a.esp+4)=3; MF32(a.esp+8)=10; MF32(a.esp+12)=30;
            MF32(a.esp+16)=1000; MF32(a.esp+20)=.01f;
            memcpy(before,G,0x1000000);
        }
""")
    harness=harness.replace('{f_006AA020_orig,f_0069DA90_orig,f_006A9760_orig}',
                            '{baseline_006AA020,baseline_0069DA90,baseline_006A9760}')
    # Nothing in the native run may use the integration doubles' positive cases.
    harness=harness[:harness.index('    } else if(target>=50)')]+r"""
    } else if(target>=40 && target<46) {
        fixture();
        GripScope selected={.suspension=0x1000,.state=0x9000,.shadow=0x8100,.native_layout=0x8000};
        GripScope parent={.suspension=0x1000,.shadow=0x8200,.native_layout=0x8000};
        if(target>=44) {
            memcpy(G+parent.shadow,G+parent.native_layout,0x50);
            for(unsigned i=0;i<2;++i) {
                MF32(parent.shadow+0x18+4*i)=.5f;
                MF32(parent.shadow+0x20+4*i)=2;
                MF32(parent.shadow+0x38+4*i)=2;
            }
            selected.previous=&parent; selected.native_layout=parent.shadow;
        }
        uint32_t pv; CHECK(identify(selected.suspension,&selected.owner,&pv));
        CHECK(select_live(&(Cpu){0},pv,&selected)); CHECK(make_overlay(&selected));
        unsigned axle=(target-40)%2;
        uint32_t tire=M32(0x11b4+8*axle);
        Cpu a=cpu(1); a.ecx=tire; Cpu b=a;
        MF32(a.esp+4)=3; MF32(a.esp+8)=10; MF32(a.esp+12)=30;
        MF32(a.esp+16)=1000; MF32(a.esp+20)=.01f;
        MF32(tire+0x110)=.5f; MF32(tire+0xd0)=40; MF32(tire+0xd4)=1000;
        uint8_t expected[0x140],*before=malloc(0x1000000); assert(before);
        memcpy(before,G,0x1000000);
        baseline_0069DA90(&a); memcpy(expected,G+tire,sizeof expected);
        memcpy(G,before,0x1000000); free(before);
        memcpy(G+selected.shadow,G+selected.native_layout,0x50);
        if(target<42) {
            selected.overlay.grip_scale[axle]=0;
            MF32(selected.shadow+0x18+4*axle)=0;
        } else {
            selected.overlay.dynamic_grip[axle]=FLT_MAX;
            MF32(selected.shadow+0x20+4*axle)=FLT_MAX;
        }
        M32(0x10a0)=selected.shadow; scope=&selected;
        f_0069DA90(&b);
        CHECK(!memcmp(&a,&b,sizeof a));
        for(unsigned i=0;i<sizeof expected;i+=4) if(memcmp(expected+i,G+tire+i,4)) {
            float ex; memcpy(&ex,expected+i,4); fprintf(stderr,"tire +%x expected %g actual %g\n",i,ex,MF32(tire+i));
        }
        CHECK(!memcmp(expected,G+tire,sizeof expected));
        CHECK(isfinite(MF32(tire+0xd8)) && isfinite(MF32(tire+0xdc)));
        CHECK(M32(0x10a0)==selected.shadow); CHECK(allocations==releases);
        scope=NULL;
    }
    printf("native bridge case %d: %u checks, %u failures\n",target,checks,fails);
    free(G); return fails!=0;
}
"""
    # The baseline functions are independent copies: changing an original called
    # by a wrapper must fail the full CPU/FPU and guest-memory comparison.
    cases=[*range(15),30,31,32,40,41,42,43,44,45]
    good_harness=harness.replace('ORIGINALS',''.join(originals.values()))
    results=compile_run(tmp,text,cases,'native',harness=good_harness)
    for p in results: assert p.returncode==0,p.stdout+p.stderr[-3000:]
    broken=''.join(body.replace('{','{ M32(0x350)^=1;',1) for body in originals.values())
    controls=compile_run(tmp,text,[0,1,2],'native_changed_originals',
                         harness=harness.replace('ORIGINALS',broken))
    assert all(p.returncode==1 for p in controls),[(p.returncode,p.stdout,p.stderr[-1000:]) for p in controls]
    print('real generated originals: 18/18 CPU/FPU + 16 MiB snapshots PASS; changed originals FAIL 3/3')
    bypass=[*range(15),30,31,32]
    for name,injected in [('bytes','M32(0x300)^=1;'),
                          ('cpu','c->st[c->top]+=1;'),
                          ('helper','gmalloc(1);'),
                          ('off_env','(void)getenv("NFS_ADDCAR_GRIP");'),
                          ('off_scope','scope=NULL;')]:
        bad=text
        for fn in ('006AA020','0069DA90','006A9760'):
            needle='void f_'+fn+'(Cpu *c) {'
            assert needle in bad
            bad=bad.replace(needle,needle+' '+injected,1)
        designated=[0,1,2,30,31,32] if name.startswith('off_') else bypass
        controls=compile_run(tmp,bad,designated,'native_bypass_'+name,harness=good_harness)
        assert all(p.returncode==1 for p in controls),(name,[(p.returncode,p.stdout,p.stderr[-1000:]) for p in controls])
    result=compile_run(tmp,text,[12],'native_wrong_admission',True,harness=good_harness)[0]
    assert result.returncode==1,result.stdout+result.stderr
    print('real bypass mutants: CPU/bytes/helpers FAIL 18/18 each; Off getenv/TLS controls FAIL 6/6 each; wrong admission FAIL 1/1')
    refusal='int restore=s && M32(s->suspension+0xa0)==s->shadow;'
    assert refusal in text
    refused=compile_run(tmp,text.replace(refusal,'int restore=s && !active && M32(s->suspension+0xa0)==s->shadow;',1),
                        [40,41,42,43],'native_bad_refusal',harness=good_harness)
    assert all(p.returncode==1 for p in refused),[(p.returncode,p.stdout,p.stderr[-1000:]) for p in refused]
    unwrapping='p->suspension==s->suspension && layout==p->shadow'
    assert unwrapping in text
    nested=compile_run(tmp,text.replace(unwrapping,'0 && '+unwrapping,1),[44,45],
                       'native_bad_nested_donor',harness=good_harness)
    assert all(p.returncode==1 for p in nested),[(p.returncode,p.stdout,p.stderr[-1000:]) for p in nested]
    print('native solver refusals above 1 m/s: 6/6 stock/finite PASS; original guard FAIL 4/4; parent-shadow donor FAIL 2/2')
    return True


def off_fast_path(tmp):
    if sys.platform != 'darwin' or os.uname().machine != 'arm64':
        return
    assembly=tmp/'bridge.s'
    compiled=subprocess.run(['clang','-O2','-S','-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'runtime'),
                             str(ROOT/'runtime/car_grip_bridge.c'),'-o',str(assembly)],
                            capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr[-2000:]
    source=assembly.read_text()
    accepted=0
    for address in NATIVE_DIGESTS:
        body=source.split('_f_'+address+':',1)[1].split('.cfi_endproc',1)[0]
        if not re.search(r'(?m)^\t(?:st[a-z]*|bl(?:r)?|sub|add)\s|\bsp\b|_scope|_getenv',body):
            accepted+=1
    assert accepted==3,f'Off wrapper assembly: {accepted}/3 without helper calls or frame/scope writes'
    print('Off assembly: 3/3 wrappers tail-call without TLS, helpers or frame/scope writes')


def main():
    text=(ROOT/'runtime/car_grip_bridge.c').read_text()
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        off_fast_path(tmp)
        settings_cases=[50,51,52,53,60,61,62,63]
        settings_good=compile_run(tmp,text,settings_cases,'settings')
        for p in settings_good: assert p.returncode==0,p.stdout+p.stderr[-3000:]
        immediate='if (!grip_depth) grip_on=grip_requested;'
        assert immediate in text
        broken=compile_run(tmp,text.replace(immediate,'grip_on=grip_requested;',1),
                           [50,51,52,53],'settings_mid_scope')
        assert all(p.returncode==1 for p in broken),[(p.returncode,p.stdout,p.stderr[-1000:]) for p in broken]
        for p in settings_good+broken: print(p.stdout.strip())
        setting_mutants=[
            ('category','.category=SET_CARS','.category=SET_WORLD',50),
            ('default','.def=0','.def=1',50),
            ('values','{"off","on"}','{"on","off"}',50),
            ('release','if (!--grip_depth) grip_on=grip_requested;','--grip_depth;',50),
            ('nested_release','if (!--grip_depth) grip_on=grip_requested;','--grip_depth; grip_on=grip_requested;',52),
            ('startup','grip_apply(v && !strcmp(v,"on"));','grip_apply(v && !strcmp(v,"off"));',60),
        ]
        for name,old,new,case in setting_mutants:
            assert old in text,name
            p=compile_run(tmp,text.replace(old,new,1),[case],'settings_'+name)[0]
            assert p.returncode==1,(name,p.stdout,p.stderr[-1000:])
        boot=(ROOT/'runtime/boot.c').read_text()
        def initialized_after_settings(source):
            return bool(re.search(r'settings_init\(\);[^}]*car_grip_init\(\);',source))
        assert initialized_after_settings(boot)
        assert not initialized_after_settings(boot.replace('    car_grip_init();','    /* omitted */',1))
        print('F10 grip: 8/8 cases PASS; mid-scope apply FAIL 4/4; setting/lifetime/startup controls FAIL 7/7')
        native_proven=native_checks(tmp,text)
        good=compile_run(tmp,text,list(range(15,27)),'normal')
        for p in good: assert p.returncode==0,p.stdout+p.stderr[-3000:]
        mutants=[
            ('restore','M32(s.suspension+0xa0)=s.native_layout;','M32(s.suspension+0xa0)=s.shadow;',15),
            ('tls','scope=s.previous;\n    gfree(s.shadow);','scope=&s;\n    gfree(s.shadow);',15),
            ('assist','q,1,&torque)','0,1,&torque)',15),
            ('force','&& loaded(c,s,c->ecx)','&& (loaded(c,s,c->ecx), 0)',15),
            ('nested','scope=NULL;\n    if (!identify','scope=s.previous;\n    if (!identify',16),
            ('generation','now.generation==s->owner.generation','1',17),
            ('live_level','live.level=(int)invoke(&q,0,0x00672D30,2,a);','live.level=0;',19),
            ('junkman','live.scale_count=i;','live.scale_count=0;',20),
            ('angle_units','0x005CB5D0,2,angles','0x006678F0,2,angles',15),
            ('retired_native','if (restore) M32(s->suspension+0xa0)=donor;','if (0) M32(s->suspension+0xa0)=s->native_layout;',17),
            ('release','gfree(s.shadow);','(void)s.shadow;',15),
            ('reuse','if (M32(s.suspension+0xa0)==s.shadow)','if (1)',23),
            ('slip','wheel_slip=slip','wheel_slip=0',21),
            ('brake_lock','0x0068E220,1,&a','0x0068E2E0,1,&a',22),
            ('scale_twice','MF32(s->shadow+0x18+4*axle)=1;','MF32(s->shadow+0x18+4*axle)=scale;',20),
        ]
        for name,old,new,case in mutants:
            assert old in text,name
            p=compile_run(tmp,text.replace(old,new,1),[case],name)[0]
            assert p.returncode!=0,(name,p.stdout,p.stderr)
        print('bridge integration: 12/12 cases PASS; 15/15 integration mutants FAIL; singular scales refused 12/12')
    hooks=(ROOT/'recomp/hooks.txt').read_text()
    build=(ROOT/'runtime/build.sh').read_text()
    table=(ROOT/'runtime/switches.tsv').read_text()
    for address in ('006AA020','0069DA90','006A9760'):
        needle=address+'    car_grip_bridge.c:'
        check=lambda value: bool(re.search(r'^'+address+r'\s+car_grip_bridge.c:',value,re.M))
        assert check(hooks) and not check(hooks.replace(needle,'removed:',1))
    for source in ('car_grip_bridge.c','car_grip_wheel.c','car_grip_force.c','car_grip_assist.c'):
        assert source in build and source not in build.replace(source,'',1)
    check=lambda value: bool(re.search(r'^NFS_ADDCAR_GRIP\topt\toff\t',value,re.M))
    assert check(table) and not check(table.replace('NFS_ADDCAR_GRIP\topt\toff','NFS_ADDCAR_GRIP\topt\ton'))
    assert not any(re.search(r'^'+a+r'\s',hooks,re.M) for a in ('0069EBF0','0068EF10'))
    print('hook/link/default registry: 8/8 PASS, omitted-line/default controls FAIL 8/8; native yaw routines unhooked')
    return 0 if native_proven else 3



if __name__=='__main__': raise SystemExit(main())
