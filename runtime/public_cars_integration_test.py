"""Actual public boot/catalog/car/menu/file/save wiring, synthetic local packs.

No game, installed catalog, private approvals, music implementation or profile.
The frozen zero-activation guard and PASS load wrapper must fail the positive
activation/menu/save cases. Every mutant is required to change production code.
Measured: 14 good processes pass 353/353 assertions; 15 controls are rejected.
All 29 runs total 1,698 assertions, ASan/UBSan clean. The frozen zero guard
fails 1/2; original PASS admission fails 13/22 (two forbidden retail writes).
Capacity load regression: fixed 18/18; predecessor fails 4/18 (zero/one free
rows). The earlier byte-preservation-only check is equivalence evidence.
Combined shared accounting: 145/145; compacted player IDs fail 12/145,
lost pilot extent exits 2 at type lookup. Two public imports retain 90 inventory
slots, 12 mask bytes and 36,040 FE bytes with traffic type 160 registered.
Extended proof: 32 runs, 1,988 assertions, 17 rejected controls.
Diagnostic cash guard: fixed 4/4; predecessor fails 2/4; guarded scratch still runs.
Missing-setting default: fixed 11/11; predecessor fails 9/11.
Retail menu and profile functions are translated production code; class lookup,
allocation and renderer upload are explicit contract doubles. The public cases
exercise selection, unlock, purchase, failed allocation and exact cash spending.
"""
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if any(not (ROOT / p).is_file() for p in ('runtime/carsave_public_test.py', 'runtime/auditfix_test.py')):
    print('SKIP: native integration fixtures absent')
    raise SystemExit(3)
sys.path.insert(0, str(ROOT / 'runtime'))
import carsave_public_test as saves
import auditfix_test as audit


NATIVE = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdio.h>
#include <CommonCrypto/CommonDigest.h>
#include "car85.c"
#include "carsave_public.h"
#include "settings.h"
uint8_t *G;
int diag_on;
const char *game_dir;
static unsigned checks,failures,constructors,retail_calls,uploads;
static unsigned retail_writes,hashes,lot_calls;
static int broken_serializer;
static uint32_t arena=0x2000000;
static const char *userroot;
static uint32_t selected,created,create_fail,menu_vault,retail_type_override=UINT32_MAX;
static Setting master={.boot_value=1};
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL %u: %s\n",__LINE__,#x); } } while(0)
extern uint32_t test_key(const char *);
uint32_t gmalloc(uint32_t n) { uint32_t p=arena; arena+=(n+63)&~63u; assert(arena<0x7000000); return p; }
void gfree(uint32_t p) { (void)p; }
uint32_t gstr(const char *s) { uint32_t p=gmalloc((uint32_t)strlen(s)+1); strcpy((char *)G+p,s); return p; }
void diag_event(const char *fmt,...) { (void)fmt; }
const char *userdata_dir(void) { return userroot; }
Setting *settings_find(const char *s) { return !getenv("TEST_MISSING_SETTING") && !strcmp(s,"cars.later_games")?&master:NULL; }
BOOT_INIT
uint32_t extra_texture_add(Cpu *c,const uint8_t r[124],const uint8_t p[32],uint32_t fmt,uint32_t data,uint32_t size,int add) {
    (void)c;(void)p;(void)fmt;(void)data;(void)size;(void)add;
    ++uploads; uint32_t t=gmalloc(160); memcpy(G+t,r,124); return t;
}
void f_00756A90_orig(Cpu *c) { M32(TABLE)=M32(c->esp+4)+16; ++retail_calls; c->esp+=4; }
void f_005816B0_orig(Cpu *c) {
    if (retail_type_override!=UINT32_MAX) { c->eax=retail_type_override; c->esp+=4; return; }
    c->eax=0;
    for (unsigned j=0;j<nadditions;++j) if (M32(c->ecx+8)==additions[j].key) c->eax=addition_id(j);
    c->esp+=4;
}
void f_00581790_orig(Cpu *c) { ++retail_calls; c->eax=0; c->esp+=4; }
void f_00591160_orig(Cpu *c) { ++retail_calls; c->eax=17; c->esp+=4; }
void f_00591330_orig(Cpu *c) { ++retail_calls; c->eax=18; c->esp+=4; }
static uint32_t cloned(uint32_t v,uint32_t cls,uint32_t key);
void f_0051E1A0(Cpu *c) {
    uint32_t layout=cloned(menu_vault,FRONTEND,M32(c->esp+4)); assert(layout);
    M32(c->ecx+8)=layout; c->eax=c->ecx; c->esp+=16;
}
void f_004E4EA0(Cpu *c) { c->eax=c->ecx; c->esp+=16; }
void f_0045A430(Cpu *c) { c->esp+=4; }
void f_007A3DB0(Cpu *c) { c->eax=selected; c->esp+=4; }
void f_00599D20(Cpu *c) {
    CHECK(M32(c->esp+4)==M32(selected));
    if(create_fail) c->eax=0;
    else {
        created=c->ecx+199*20; memcpy(G+created,G+selected,20);
        M32(created)=199; M32(created+12)=0xF0002; M8(created+16)=33; M8(created+17)=2; c->eax=created;
    }
    c->esp+=8;
}
void f_005910E0(Cpu *c) { c->esp+=4; }
void f_0056C5B0(Cpu *c) { c->esp+=4; }
void bad_jump(Cpu *c,uint32_t pc) { (void)c; fprintf(stderr,"bad jump %08X\n",pc); abort(); }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    (void)n;
    switch (fn) {
    case 0x00455FD0: case 0x00747C00: return 1;
    case 0x00598590: return 1;
    case 0x00605250: ++lot_calls; return 0;
    case 0x004549B0: return test_key((char *)G+a[0]);
    case 0x00453760: M32(c->ecx+8)=parts_hash((char *)G+a[0]); M32(c->ecx+12)=a[0]; return c->ecx;
    case 0x01001000: return gmalloc(a[0]);
    case 0x007398D0:
        for(unsigned i=0;i<car85_count();++i) if(M32(M32(TABLE)+i*STRIDE+0x50)==a[0]) return i;
        return UINT32_MAX;
    case 0x00457F90: {
        uint32_t ld=a[0],col=c->ecx,cls=gmalloc(4);
        M32(cls)=M32(ld+4); M32(col+0x14)=cls; M32(col+0x18)=M32(ld+0x1C);
        M32(col+0x20)=M32(ld); M32(col+0x24)=a[1]; ++constructors; return col;
    }
    default: fprintf(stderr,"unexpected guest call %08X\n",fn); abort();
    }
}
PROFILE_HELPERS
STUBS
TRANSLATED_PROFILES
TRANSLATED_MENUS
static uint32_t invoke(Cpu *c,uint32_t row,void (*fn)(Cpu *)) {
    uint32_t sp=c->esp; c->ecx=row; c->esp-=4; M32(c->esp)=0; fn(c); CHECK(c->esp==sp); return c->eax;
}
static uint32_t unlocked(Cpu *c,int career,unsigned handle) {
    uint32_t sp=c->esp; c->esp-=16; M32(c->esp)=M32(c->esp+4)=0; M32(c->esp+8)=handle; M32(c->esp+12)=0;
    if(career) f_0058A5F0(c); else f_0058A0D0(c);
    c->esp=sp; return c->eax&255;
}
static uint32_t vault(void) {
    uint32_t v=gmalloc(64),tt=gmalloc(12),types=gmalloc(8);
    M32(v+4)=tt; M32(tt)=types; M32(tt+8)=1; M32(types)=0x8E112EB7;
    M32(v+0xC)=M32(v+0x2C)=gmalloc(16); M32(v+0x10)=M32(v+0x30)=gmalloc(4);
    car85_vault_begin(v); return v;
}
static uint32_t collection(uint32_t v,uint32_t cls,uint32_t key,uint32_t size) {
    uint32_t ld=gmalloc(256); M32(ld)=key; M32(ld+4)=cls;
    M32(ld+0x1C)=gmalloc(size); M32(ld+0x18)=1; M32(ld+0x20)=REFSPEC;
    car85_collection_seen(ld,v); return ld;
}
static void ref(uint32_t ld,uint32_t key,uint32_t cls,uint32_t target) {
    uint32_t p=ld+36+12*M32(ld+0x14),r=gmalloc(12);
    M32(p)=key; M32(p+4)=r; M32(r)=cls; M32(r+4)=target; ++M32(ld+0x14);
}
static uint32_t cloned(uint32_t v,uint32_t cls,uint32_t key) {
    for (unsigned i=0;i<M32(v+0x34);++i) if (M32(M32(v+0x30)+4*i)==key) {
        uint32_t col=M32(M32(v+0x2C)+16*i);
        if (M32(M32(col+0x14))==cls) return M32(col+0x18);
    }
    return 0;
}
static void empty(uint32_t db,unsigned used) {
    memset(G+db,0xA7,0x8CC8);
    for(unsigned i=0;i<200;++i) { M32(db+20*i)=i<used?i:UINT32_MAX; M32(db+20*i+4)=M32(db+20*i+8)=1; }
}
int main(int argc,char **argv) {
    assert(argc==4); G=calloc(1,0x8000000); assert(G); userroot=argv[1]; game_dir=argv[1];
    M32(0x9173F8)=M32(0x9173FC)=0x9173F8;
    master.boot_value=strcmp(argv[2],"off")!=0;
    /* None of these diagnostic paths may be consulted by a public build. */
    setenv("NFS_TEST_ADDCARS","/must-not-open",1); setenv("NFS_TEST_ADDCARS_SEED","/must-not-open",1);
    setenv("NFS_TEST_PRIVATE_CARS_ROOT","/must-not-open",1); setenv("NFS_TEST_CAR_DIR","/must-not-open",1);
    if (!strcmp(argv[2],"missing-setting")) setenv("TEST_MISSING_SETTING","1",1);
    private_cars_boot(); int active=!strcmp(argv[2],"on");
    CHECK(private_cars_count()==(active?2:0)); CHECK(car85_count()==(active?160:84));
    if (active && nadditions!=2) { printf("public integration on checks=%u failures=%u\n",checks,failures); return 1; }
    private_cars_boot(); CHECK(private_cars_count()==(active?2:0));
    Cpu c={.esp=0x1800000}; uint32_t chunk=gmalloc(16+84*STRIDE);
    M32(chunk)=0x34600;
    for(unsigned i=0;i<84;++i) {
        uint32_t p=chunk+16+i*STRIDE; snprintf((char *)G+p,16,"BASE_%u",i);
        M32(p+0x50)=parts_hash((char *)G+p); M32(p+0x90)=i;
    }
    strcpy((char *)G+chunk+16,"MODEL_DONOR"); M32(chunk+16+0x50)=parts_hash("MODEL_DONOR");
    uint8_t original[84*STRIDE]; memcpy(original,G+chunk+16,sizeof original);
    M32(c.esp+4)=chunk; f_00756A90(&c);
    CHECK(!memcmp(original,G+M32(TABLE),sizeof original)); CHECK(retail_calls==1);
    if (!active) {
        char path[256]="UNCHANGED";
        CHECK(!car85_path("CARS/MODEL_0/GEOMETRY.BIN",path,sizeof path)); CHECK(!strcmp(path,"UNCHANGED"));
        CHECK(M32(TABLE)==chunk+16); CHECK(!nadditions);
        c.ecx=gmalloc(20); f_00591160(&c); CHECK(c.eax==17); f_00591330(&c); CHECK(c.eax==18);
    } else {
        CHECK(addition_id(0)==84 && addition_id(1)==159); CHECK(!addition_for_type(85) && !addition_for_type(158));
        CHECK(M32(M32(TABLE)+85*STRIDE+0x90)==UINT32_MAX);
        char path[4096];
        CHECK(car85_path("CARS/MODEL_0/GEOMETRY.BIN",path,sizeof path));
        CHECK(!strcmp(path,private_cars_get(0)->geometry));
        FILE *f=fopen(path,"rb"); char bytes[64]={0}; assert(f); rewind(f); fread(bytes,1,63,f); fclose(f);
        CHECK(!strcmp(bytes,"synthetic geometry MODEL_0"));
        CHECK(car85_path("CARS/MODEL_0/TEXTURES.BIN",path,sizeof path)); CHECK(!strcmp(path,private_cars_get(0)->textures));
        CHECK(!car85_path("CARS/MODEL_11/GEOMETRY.BIN",path,sizeof path));
        CHECK(car85_path("CARS/MODEL_0/OTHER.BIN",path,sizeof path)); CHECK(strstr(path,"/CARS/MODEL_DONOR/OTHER.BIN"));
        for(unsigned j=0;j<2;++j) {
            CHECK(additions[j].solid_count==2 && additions[j].wheel_fit==1 && additions[j].render_fit);
            CHECK(additions[j].collision_size==80 && additions[j].collision_bytes && additions[j].paint_key==123);
        }
        Addition standalone={0}; load_collision(&standalone,private_cars_get(0)->collision);
        CHECK(standalone.collision_size==80 && standalone.collision_key==test_key("MODEL_0") && !standalone.wheel_fit);
        free(standalone.collision_bytes);
        Addition unicode={0}; snprintf(unicode.display_name,sizeof unicode.display_name,"Caf\xc3\xa9");
        uint32_t histogram=gmalloc(516); M32(histogram)=256; M16(histogram+4+2*128)=0xE9;
        M32(0x91CF7C)=histogram; uint32_t packed=packed_display(&unicode);
        CHECK(!memcmp(G+packed,"Caf\x80",5)); M32(0x91CF7C)=0;
        uint32_t allocator=gmalloc(4),vt=gmalloc(4); M32(0x8F7EA8)=allocator; M32(allocator)=vt; M32(vt)=0x1001000;
        uint32_t v=vault(),donor=test_key("MODEL_DONOR"),price=test_key("PRICE_DONOR"); menu_vault=v;
        uint32_t pv=collection(v,PVEHICLE,donor,0x74),pr=collection(v,PVEHICLE,price,0x74);
        uint32_t fe=collection(v,FRONTEND,11,0x5B),pf=collection(v,FRONTEND,12,0x5B),ec=collection(v,ECAR,donor,0x11D);
        M32(M32(pv+0x1C)+0x1C)=gstr("MODEL_DONOR"); M32(M32(pr+0x1C)+0x1C)=gstr("PRICE_DONOR");
        ref(pv,FRONTEND,FRONTEND,11); ref(pr,FRONTEND,FRONTEND,12);
        uint32_t policy=M32(pf+0x1C); M32(policy+0x38)=1; M32(policy+0x4C)=34567; M8(policy+0x58)=1; M8(policy+0x59)=4;
        uint32_t handling=M32(fe+0x1C); M32(handling+0x4C)=99999;
        const uint32_t links[]={0xAFA210F0u,0xBD38D1CAu,0x0C2DA793u,0xCCF03CB3u,0xD74C1435u,0x585517F3u,0x4B675DC8u};
        for(unsigned i=0;i<7;++i) {
            uint32_t cls=i<2?links[i]:0x93C171E4u;
            uint32_t ld=collection(v,cls,100+i,0x74); ref(i<2?pv:ec,links[i],cls,100+i);
            if(i>=2) for(unsigned k=0;k<4;++k) { uint32_t ar=M32(ld+0x1C)+0x20+16*k; M16(ar)=M16(ar+2)=2; M16(ar+4)=4; }
        }
        ref(ec,0x8D5BEB72u,0,0); uint32_t compression=entry(ec,0x8D5BEB72u); M8(compression+10)=2;
        uint32_t oldarray=gmalloc(16); M32(compression+4)=oldarray; M16(oldarray)=M16(oldarray+2)=2; M16(oldarray+4)=4;
        /* Sources snapshot descriptors at registration, just as the real loader
         * does once descriptors are final. Re-observe final synthetic records. */
        nsources=0;
        car85_collection_seen(pv,v); car85_collection_seen(pr,v); car85_collection_seen(fe,v); car85_collection_seen(pf,v); car85_collection_seen(ec,v);
        for(unsigned i=0;i<7;++i) {
            /* Linked collections are independent of their synthetic descriptors. */
            uint32_t ld=collection(v,i<2?links[i]:0x93C171E4u,100+i,0x74);
            if(i>=2) for(unsigned k=0;k<4;++k) { uint32_t ar=M32(ld+0x1C)+0x20+16*k; M16(ar)=M16(ar+2)=2; M16(ar+4)=4; }
        }
        uint8_t donor_before[0x11D]; memcpy(donor_before,G+M32(ec+0x1C),sizeof donor_before);
        car85_vault_loaded(&c,v); CHECK(constructors==22);
        CHECK(!memcmp(donor_before,G+M32(ec+0x1C),sizeof donor_before)); CHECK(M32(handling+0x4C)==99999);
        for(unsigned j=0;j<2;++j) {
            Addition *a=&additions[j];
            CHECK(a->key==test_key(j?"MODEL_75_PV":"MODEL_0_PV")); CHECK(a->frontend_key!=a->key && a->ecar_key!=a->key);
            CHECK(cloned(v,ECAR,a->ecar_key) && cloned(v,ECAR,a->folder_key));
            uint32_t lay=cloned(v,FRONTEND,a->frontend_key); CHECK(lay!=0); if(!lay) return 1;
            CHECK(M32(lay+0x4C)==34567 && M8(lay+0x59)==4 && M8(lay+0x58)==1 && M32(lay+0x38)==1);
            uint32_t row=gmalloc(20); M32(row+4)=a->frontend_key; c.ecx=row; f_00581790(&c);
            CHECK(!strcmp((char *)G+c.eax,"Synthetic"));
            char label[64]; snprintf(label,sizeof label,"CAR_NAME_%s",a->name); f_00591160(&c); CHECK(c.eax==parts_hash(label));
            snprintf(label,sizeof label,"CARSELECT_IMPORTED_%u",a->catalog_id); f_00591330(&c); CHECK(c.eax==parts_hash(label));
        }
        uint32_t lang=gmalloc(8); M32(lang)=1; M32(lang+4)=gstr("Base"); M32(0x91CF80)=lang; M32(0x91CF78)=1;
        register_names(); CHECK(M32(0x91CF78)==3); CHECK(!strcmp((char *)G+additions[0].display_string,"Synthetic 0"));
        uint32_t db=gmalloc(0x8CC8); empty(db,197); unsigned inserted=0;
        CHECK(!reconcile_stock(db,&inserted) && inserted==2); CHECK(M32(db+197*20+4)==additions[0].frontend_key && M32(db+197*20+8)==additions[0].key);
        CHECK(M32(db+198*20+4)==additions[1].frontend_key && M32(db+198*20+8)==additions[1].key);
        CHECK(!reconcile_stock(db,&inserted) && !inserted);
        empty(db,199); uint8_t full[0x8CC8]; memcpy(full,G+db,sizeof full);
        CHECK(!car85_public_reconcile(&c,db)); CHECK(!memcmp(full,G+db,sizeof full));
        uint32_t fedb=gmalloc(0x200),profile=gmalloc(0xA000); db=profile+0x414;
        M32(0x91CF90)=fedb; M32(fedb+0x10)=profile; M32(fedb+0x12C)=0x8001;
        /* Complete retail-profile load with a different fresh catalog CRC.
         * With zero/one free rows reconciliation is an exact capacity no-op. */
        uint32_t payload=gmalloc(CARSAVE_PUBLIC_SIZE);
        carsave_public_init(userroot,car85_public_reconcile);
        for(unsigned free_rows=0;free_rows<=2;++free_rows) {
            memset(G+profile,0,0x9CB0);
            for(unsigned i=0;i<200;++i) {
                uint32_t r=db+i*20; M32(r)=i<200-free_rows?i:UINT32_MAX;
                M32(r+4)=M32(r+8)=1; M32(r+12)=1; M16(r+16)=0xFFFF;
            }
            M32(fedb+8)=0x1234;
            uint32_t sp=c.esp; c.ecx=profile; c.esp-=12;
            M32(c.esp)=0; M32(c.esp+4)=payload; M32(c.esp+8)=CARSAVE_PUBLIC_SIZE;
            f_0058FD10_orig(&c); CHECK(c.esp==sp);
            CHECK(!carsave_public_verify(G+payload,CARSAVE_PUBLIC_SIZE));
            uint8_t saved_fe[CARSAVE_PUBLIC_FE_SIZE]; memcpy(saved_fe,G+payload+0x5BA1,sizeof saved_fe);
            M32(fedb+8)=0x5678; CHECK(M32(payload+0x5A09)!=M32(fedb+8));
            memset(G+profile,0xA6,0x9CB0); c.ecx=profile; c.esp-=20;
            M32(c.esp)=0; M32(c.esp+4)=payload; M32(c.esp+8)=CARSAVE_PUBLIC_SIZE;
            M32(c.esp+12)=M32(c.esp+16)=0;
            unsigned before=checks; carsave_public_load(&c);
            CHECK(c.esp==sp); CHECK(c.eax&255); CHECK(carsave_public_save_allowed());
            CHECK(M32(fedb+8)==0x5678);
            CHECK(!memcmp(saved_fe,G+payload+0x5BA1,sizeof saved_fe));
            if(free_rows<2) CHECK(!memcmp(saved_fe,G+db,sizeof saved_fe));
            else CHECK(M32(db+198*20+8)==additions[0].key && M32(db+199*20+8)==additions[1].key);
            printf("capacity free=%u checks=%u failures=%u\n",free_rows,checks-before,failures);
        }
        /* Diagnostic environment variables on ordinary userdata cannot seed cash. */
        uint32_t owned=db; M32(owned+12)=2; M32(profile+0xB4)=98765; M32(0x925E90)=6;
        unsetenv("BOOT_USERDATA"); unsetenv("BOOT_DIAG");
        setenv("NFS_TEST_ADDCARS_CARLOT","1",1); setenv("NFS_TEST_ADDCARS_PURCHASE","1",1); diag_on=1;
        unsigned diag_before=checks; car_lot_probe(&c,owned);
        CHECK(M32(profile+0xB4)==98765); CHECK(!lot_calls);
        char diag[4096],scratch[4096]; snprintf(diag,sizeof diag,"%s/diag",userroot); snprintf(scratch,sizeof scratch,"%s/diag.user",userroot);
        setenv("BOOT_DIAG",diag,1); setenv("BOOT_USERDATA",scratch,1); car_lot_probe(&c,owned);
        CHECK(M32(profile+0xB4)==200000); CHECK(lot_calls==1);
        printf("diagnostic guard checks=%u failures=%u\n",checks-diag_before,failures);
        diag_on=0; unsetenv("NFS_TEST_ADDCARS_CARLOT"); unsetenv("NFS_TEST_ADDCARS_PURCHASE");
        empty(db,197); CHECK(!reconcile_stock(db,&inserted) && inserted==2);
        for(unsigned j=0;j<2;++j) {
            uint32_t r=db+(197+j)*20;
            CHECK(invoke(&c,r,f_005816B0)==addition_id(j));
            CHECK(invoke(&c,r,f_00581730)==34567);
            for(unsigned all=0;all<2;++all) for(int delta=-1;delta<=1;++delta) {
                M8(profile+0xB0)=4+delta; M8(UNLOCK_ADDRESS)=all;
                CHECK(unlocked(&c,0,197+j)==(all || delta<=0));
                CHECK(unlocked(&c,1,197+j)==(all || delta<=0));
            }
        }
        retail_type_override=85; CHECK(invoke(&c,db+197*20,f_005816B0)==UINT32_MAX); retail_type_override=UINT32_MAX;
        uint32_t screen=gmalloc(0x200);
        for(unsigned failure=0;failure<2;++failure) {
            selected=db+197*20; create_fail=failure; created=0; M32(profile+0xB4)=2000000;
            uint8_t stock[20]; memcpy(stock,G+selected,20); uint32_t sp=c.esp;
            c.ecx=screen; c.esp-=20; M32(c.esp)=0; M32(c.esp+4)=0xD05FC3A3;
            M32(c.esp+8)=M32(c.esp+12)=M32(c.esp+16)=0; f_007C1770(&c);
            CHECK(c.esp==sp); CHECK(M32(profile+0xB4)==2000000-(failure?0:34567));
            CHECK(!memcmp(stock,G+selected,20));
            if(!failure) { CHECK(created!=selected); CHECK(M32(created+4)==additions[0].frontend_key && M32(created+8)==additions[0].key);
                CHECK(M32(created+12)==0xF0002 && M32(profile+0xA8)==199); }
        }
        CHECK(!private_cars_get(2) && !private_cars_inventory_get(90));
        CHECK(private_cars_inventory_get(75)->id==159 && !private_cars_inventory_get(1)->selected);
        register_logos(&c); CHECK(uploads==4);
    }
    printf("public integration %s checks=%u failures=%u constructors=%u uploads=%u\n",argv[2],checks,failures,constructors,uploads);
    free(G); return failures?1:0;
}
'''


def function(text, name):
    start = text.index('static void ' + name + '(')
    return text[start:text.index('\n}', start) + 2]


def build(args):
    result = subprocess.run(args, text=True, capture_output=True)
    assert not result.returncode, result.stderr[-5000:]


def translated_menus():
    functions = []
    for chunk, names in [('15', ('004FB210', '00502E80', '00503400_orig')),
                         ('20', ('0056D7A0',)), ('21', ('00581730',)),
                         ('22', ('0058A0D0', '0058A5F0')), ('52', ('007C1770',))]:
        path = ROOT / f'recomp/out/speed_{chunk}.c'
        if not path.is_file():
            print('SKIP: translated menu functions absent')
            raise SystemExit(3)
        text = path.read_text()
        for name in names:
            start = text.index('void f_' + name + '(')
            code = text[start:text.index('\n}', start) + 2]
            if name.endswith('_orig'):
                code = code.replace('void f_' + name + '(', 'void f_' + name[:-5] + '(', 1)
            functions.append(code)
    return '\n'.join(functions)


def combined_source(source):
    """Inject a verified-traffic contract at the shared registration boundary.

    Public startup stays disabled; separate pilot tests exercise its approval
    reader. This checks coexistence with the real trusted public import reader.
    """
    source=source.replace('static int broken_serializer;', 'static int broken_serializer; static uint32_t pilot_ld;')
    source=source.replace('++constructors; return col;', 'if(M32(ld)==test_key("TRAFFICSEDAN") && M32(ld+4)==PVEHICLE) pilot_ld=ld; ++constructors; return col;')
    source=source.replace('private_cars_boot(); int active=', r'''private_cars_boot();
    CHECK(!traffic_pilot_enabled() && !private_cars_traffic_get());
    CHECK(on());
    char traffic_dir[4096]; snprintf(traffic_dir,sizeof traffic_dir,"%s/traffic-packs",userroot);
    configure_traffic(NULL,traffic_dir);
    int active=''',1)
    source=source.replace('(active?160:84)', '(active?161:84)')
    source=source.replace('uint8_t original[84*STRIDE];', r'''strcpy((char *)G+chunk+16+25*STRIDE,"TRAF4DSEDA");
    M32(chunk+16+25*STRIDE+0x50)=parts_hash("TRAF4DSEDA");
    uint8_t original[84*STRIDE];''',1)
    source=source.replace('CHECK(constructors==22);', r'''CHECK(constructors==24);
        CHECK(car85_count()==161 && nadditions==2 && asset_count()==3);
        CHECK(addition_for_type(159)==&additions[1] && addition_for_type(160)==&traffic_addition);
        CHECK(car85_traffic_ready()); CHECK(!cloned(v,FRONTEND,traffic_addition.key));
        uint32_t tref=entry(pilot_ld,FRONTEND);
        CHECK(tref && M32(M32(tref+4)+4)==11);
        CHECK(M32(M32(TABLE)+160*STRIDE+0x90)==160);''')
    source=source.replace('nsources=0;', r'''uint32_t td=test_key("TRAF4DSEDA");
        uint32_t tp=collection(v,PVEHICLE,td,0x4E),te=collection(v,ECAR,td,0x11D);
        M32(M32(tp+0x1C)+0x1C)=gstr("TRAF4DSEDA"); ref(tp,FRONTEND,FRONTEND,11);
        ref(te,0x8D5BEB72u,0,0); uint32_t tc=entry(te,0x8D5BEB72u),ta=gmalloc(16);
        M8(tc+10)=2; M32(tc+4)=ta; M16(ta)=M16(ta+2)=2; M16(ta+4)=4;
        nsources=0; car85_collection_seen(tp,v); car85_collection_seen(te,v);''',1)
    source=source.replace('/* Diagnostic environment variables on ordinary userdata cannot seed cash. */', r'''
        unsigned char expected[12]={1},observed[CARSAVE_PUBLIC_FE_SIZE]; expected[75/8]|=1u<<(75%8);
        memcpy(observed,G+db,sizeof observed); M32(fedb+8)=0xABCD;
        uint32_t sp=c.esp; c.ecx=profile; c.esp-=12;
        M32(c.esp)=0; M32(c.esp+4)=payload; M32(c.esp+8)=CARSAVE_PUBLIC_SIZE;
        carsave_public_serialize(&c); CHECK(c.esp==sp); CHECK(carsave_public_save_allowed());
        CHECK(!carsave_public_admit(G+payload,CARSAVE_PUBLIC_SIZE,expected));
        CHECK(!memcmp(observed,G+payload+0x5BA1,sizeof observed));
        M32(fedb+8)=0xDCBA; memset(G+db,0xA6,sizeof observed); c.ecx=profile; c.esp-=20;
        M32(c.esp)=0; M32(c.esp+4)=payload; M32(c.esp+8)=CARSAVE_PUBLIC_SIZE;
        M32(c.esp+12)=M32(c.esp+16)=0; carsave_public_load(&c);
        CHECK(c.esp==sp && (c.eax&255)); CHECK(M32(fedb+8)==0xDCBA);
        CHECK(!memcmp(observed,G+db,sizeof observed));
        CHECK(private_cars_count()==2 && private_cars_inventory_count()==90);
        printf("combined types=%u player-rows=2 traffic-type=160 fe-bytes=%zu mask-bytes=%zu\n",car85_count(),sizeof observed,sizeof expected);
        /* Diagnostic environment variables on ordinary userdata cannot seed cash. */''')
    return source


def main():
    with tempfile.TemporaryDirectory(prefix='public-cars-integration-') as directory:
        root = Path(directory).resolve()
        saves.fixture(root)
        shutil.copytree(root / 'catalog', root / 'car-catalog')
        (root / 'diag').mkdir(); (root / 'diag.user').mkdir()
        boot = (ROOT / 'runtime/boot.c').read_text()
        car = (ROOT / 'runtime/car85.c').read_text()
        menus = translated_menus()
        unlock = re.search(r'mov (?:al|bl|cl|dl), byte ptr \[0x([0-9a-f]+)\]', menus[menus.index('void f_0058A5F0('):]).group(1)
        source = NATIVE.replace('BOOT_INIT', function(boot, 'private_cars_boot')).replace('TRANSLATED_MENUS', menus).replace('UNLOCK_ADDRESS', '0x' + unlock + 'u')
        helpers=saves.NATIVE[saves.NATIVE.index('void f_0057F7B0('):saves.NATIVE.index('/* Frozen pre-admission public behavior;')]
        profiles=saves.translated()
        profiles=profiles[:profiles.index('void f_005ACDD0(')]
        source=source.replace('PROFILE_HELPERS',helpers).replace('TRANSLATED_PROFILES',profiles)
        calls = set(re.findall(r'\b(f_[0-9A-F]{8}(?:_orig)?)\(', car + source))
        provided = set(re.findall(r'void (f_[0-9A-F]{8}(?:_orig)?)\(Cpu \*(?:restrict )?c\)\s*\{', car + source))
        calls |= {'f_005ACB70_orig', 'f_0058FD10_orig'}
        stubs = '\n'.join(f'void {n}(Cpu *c) {{ (void)c; abort(); }}' for n in sorted(calls - provided))
        (root / 'native.c').write_text(source.replace('STUBS', stubs))
        (root / 'reader.c').write_text('#include "' + str(ROOT / 'runtime/car_catalog.c') + '"\nuint32_t test_key(const char *s) {return attribute_key_case(s,1);}\n')
        (root / 'car85.c').write_text(car)
        common = ['clang', '-std=c11', '-O1', '-g', '-DNFS_PUBLIC=1', '-fsanitize=address,undefined',
                  '-Wno-deprecated-declarations', '-I', str(root), '-I', str(ROOT / 'runtime'),
                  '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'),
                  '-DCAR_CATALOG_APPROVAL_HEADER="' + str(root / 'approval.h') + '"']
        inputs = [str(ROOT/'runtime/traffic_pilot.c'), str(root / 'native.c'), str(root / 'reader.c'), str(ROOT / 'runtime/private_cars_public.c'),
                  str(root / 'carsave.c'), '-framework', 'CoreGraphics', '-framework', 'CoreText', '-framework', 'CoreFoundation']
        cases = assertions = controls = 0

        def run(exe, args, expect=0):
            nonlocal cases, assertions
            p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=60)
            assert p.returncode == expect, f'{exe.name} {args}: exit={p.returncode}\n{p.stdout}\n{p.stderr[-5000:]}'
            assert 'runtime error:' not in p.stderr and 'AddressSanitizer' not in p.stderr, p.stderr[-5000:]
            print(exe.name, p.stdout.strip())
            cases += 1
            counts = re.findall(r'checks=(\d+)', p.stdout)
            assertions += int(counts[-1]) if counts else 0
            return p

        save_module=(ROOT/'runtime/carsave_public.c').read_text()
        (root/'carsave.c').write_text(save_module)
        exe = root / 'activation'
        build(common + inputs + ['-o', str(exe)])
        run(exe, [root, 'on', 'normal'])
        run(exe, [root, 'off', 'normal'])
        run(exe, [root, 'missing-setting', 'normal'])
        absent = root / 'absent'
        absent.mkdir()
        run(exe, [absent, 'absent', 'normal'])
        from setup_kit.car_convert import hash32, name_hash
        packs=root/'traffic-packs'; packs.mkdir()
        offsets=(1.,.7,0.,.3,1.,-.7,0.,.3,-1.,-.7,0.,.3,-1.,.7,0.,.3)
        (packs/'WHEELFIT.BIN').write_bytes(struct.pack('<4I22f12B',0x31544657,1,
            hash32('trafficsedan'),hash32('traf4dseda'),*offsets,.17,.17,.2,.2,.2,.2,*([0]*12)))
        (packs/'SOLIDMAP.BIN').write_bytes(struct.pack('<8I',0x31504D53,2,name_hash('TRAFFICSEDAN'),2,101,1001,202,2002))
        (root/'native.c').write_text(combined_source(source).replace('STUBS',stubs))
        combined_variants={
            'combined':car,
            'combined-player-count':car.replace('a->catalog_id=car->id;', 'a->catalog_id=TYPES+i;'),
            'combined-pilot-count':car.replace('if (traffic_active) return TRAFFIC_PILOT_TYPE + 1;', 'if (traffic_active) return count;'),
        }
        for label,changed in combined_variants.items():
            assert label=='combined' or changed!=car
            (root/'car85.c').write_text(changed)
            build(common+inputs+['-o',str(root/label)])
            result=run(root/label,[root,'on','combined'],expect=0 if label=='combined' else 1 if label=='combined-player-count' else 2)
            if label=='combined-pilot-count':
                assert 'GetCarType did not return manifest type' in result.stderr,result.stderr[-1000:]
                print(label,'GetCarType refused the missing type 160 (exit 2)')
            controls+=label!='combined'
        (root/'native.c').write_text(source.replace('STUBS',stubs))
        (root/'car85.c').write_text(car)
        # Frozen original guard at car85.c:on(): public builds returned zero
        # before configuration/catalog activation. The same positive case fails.
        original = car.replace('static int on(void) {', 'static int on(void) {\n#if defined(NFS_PUBLIC) && NFS_PUBLIC\n    return 0;\n#endif', 1)
        (root / 'car85.c').write_text(original)
        build(common + inputs + ['-o', str(root / 'original-zero')])
        run(root / 'original-zero', [root, 'on', 'normal'], expect=1)
        controls += 1
        mutations = {
            'wrong-price': ('M32(lay + 0x4C) = M32(policy + 0x4C);', 'M32(lay + 0x4C) = 1;'),
            'wrong-unlock': ('M8(lay + 0x59) = M8(policy + 0x59);', 'M8(lay + 0x59) = 0;'),
            'wrong-name': ('snprintf(label,sizeof label,"CAR_NAME_%s",a->name);', 'snprintf(label,sizeof label,"CAR_NAME_%s",a->donor_folder);'),
            'wrong-brand': ('gstr(addition_brand(a, M32(fe->ld + 0x1C)))', 'gstr("MODEL_DONOR")'),
            'missing-collision': ('if (car->collision[0]) load_collision(a,car->collision);', '/* omitted verified collision */'),
            'wrong-binding': ('sizeof a->frontend,"%s",car->frontend', 'sizeof a->frontend,"%s",car->pvehicle'),
            'allow-hole': (' || (c->eax>=TYPES && !addition_for_type(c->eax))', ''),
            'unguarded-cash': (' || !scratch_profile() || !on()', ' || !on()'),
            'partial-capacity': ('if (free_count < count) {', 'if (free_count < count) { M32(db)=123;'),
        }
        for name, (before, after) in mutations.items():
            changed = car.replace(before, after)
            assert changed != car, name
            (root / 'car85.c').write_text(changed)
            build(common + inputs + ['-o', str(root / name)])
            run(root / name, [root, 'on', 'normal'], expect=1)
            controls += 1
        (root / 'car85.c').write_text(car)
        old_capacity=save_module.replace('car_catalog_count()!=0 || any(used)', 'any(used)',1)
        assert old_capacity!=save_module
        (root/'carsave.c').write_text(old_capacity)
        build(common+inputs+['-o',str(root/'capacity-predecessor')])
        run(root/'capacity-predecessor',[root,'on','normal'],expect=1)
        controls+=1
        (root/'carsave.c').write_text(save_module)
        old_boot=source.replace('master?master->boot_value:0,NULL,0','master?master->boot_value:1,NULL,0',1)
        assert old_boot!=source
        (root/'native.c').write_text(old_boot.replace('STUBS',stubs))
        build(common+inputs+['-o',str(root/'fallback-predecessor')])
        run(root/'fallback-predecessor',[root,'missing-setting','normal'],expect=1)
        controls+=1
        (root/'native.c').write_text(source.replace('STUBS',stubs))

        # Compile the translated E-S harness with the real public wrappers and
        # strong adapters, replacing its standalone forwarding contract doubles.
        native = saves.NATIVE
        start = native.index('/* Frozen pre-admission public behavior;')
        native = native[:start] + native[native.index('GENERATED', start):]
        native = native.replace('GENERATED', saves.translated())
        native = native.replace('int cat=car_catalog_init(argv[1],!off);', 'int cat=private_cars_init(argv[1],!off,NULL,0);')
        native = native.replace('seed(&c,1); CHECK(memcmp(ordinary,G+buffer,16));\n        CHECK(file_publish(path,G+buffer,1));',
                                'seed(&c,1); CHECK(memcmp(ordinary,G+buffer,16));\n        CHECK(file_publish(path,G+buffer,1)); CHECK(files(metadata,".meta")==1 && !files(metadata,".pending"));')
        native = '#include "private_cars.h"\n' + native
        native += '\nvoid underglow_profile_default(uint32_t p) {}\nvoid underglow_profile_load(uint32_t p,uint32_t b,uint32_t n) {}\nvoid underglow_save_begin(void) {}\nvoid underglow_profile_save(uint32_t p,uint32_t b,uint32_t n) {}\n'
        public = (ROOT / 'runtime/extratrax_public.c').read_text()
        wrappers = re.findall(r'^PASS\(([0-9A-F]{8})\)', public, re.M) + ['005AB450']
        native += '\n' + '\n'.join(f'void f_{n}_orig(Cpu *c) {{ (void)c; abort(); }}' for n in wrappers)
        (root / 'save.c').write_text(native)
        scan = audit.marked('runtime/boot.c', '// BEGIN offline save scan:', '// END offline save scan')
        layer = audit.marked('runtime/boot.c', '// BEGIN offline file layer:', '// END offline file layer')
        # Keep weak production defaults: the production adapter must override them.
        extra = saves.FILE_EXTRA[:saves.FILE_EXTRA.index('int private_cars_save_allowed(void) {')]
        extra = extra.replace('void extratrax_save_complete(int ok) { (void)ok; }', '')
        extra = extra.replace('uint32_t h=file_begin(path); if(h==UINT32_MAX) return 0;',
                              'unsigned seq=save_fail_seq,commits=save_commit_seq;\n    uint32_t h=file_begin(path); if(h==UINT32_MAX) return 0;')
        extra = extra.replace('if(complete) carsave_public_save_complete(ok);',
                              'if(complete) { M32(0x500)=1; M32(0x4f4)=0; save_task_fixup(0x200,0xb,seq); save_sidecars_complete(0x200,0xb,seq,commits); }')
        extra += '\n' + r'''
int snapshot_handles(const char *path) {
    uint32_t a=file_open(path,path,0x80000000u,1,3,0),b=file_open(path,path,0x80000000u,1,3,0);
    if(a==UINT32_MAX || b==UINT32_MAX) return 0;
    char x[4]={0},y[4]={0};
    int ok=file_read(a,x,3)==3 && file_read(b,y,3)==3 && !memcmp(x,y,3);
    ok=ok && file_seek(a,1,SEEK_CUR)==4 && file_seek(b,0,SEEK_CUR)==3;
    ok=ok && file_seek(a,-1,SEEK_SET)==-1;
    ok=ok && file_seek(a,0,SEEK_SET)==0 && file_read(a,y,3)==3 && !memcmp(x,y,3);
    return file_close(a) && file_close(b) && ok;
}
'''
        (root / 'file.c').write_text('#include <assert.h>\n' + audit.FILE_PREFIX + scan + layer + extra)
        file_source = (root / 'file.c').read_text()
        # Check repeated descriptor reads in the same executable as real saves.
        native = native.replace('carsave_public_init(argv[4],NULL); file_init(argv[4]);', '''carsave_public_init(argv[4],NULL); file_init(argv[4]);
    extern int snapshot_handles(const char *);
    if (private_cars_count()) CHECK(snapshot_handles(private_cars_get(0)->geometry));''')
        (root / 'save.c').write_text(native)
        save_inputs = [str(root / 'save.c'), str(root / 'file.c'), str(ROOT / 'runtime/carsave_public.c'),
                       str(ROOT / 'runtime/car_catalog.c'), str(ROOT / 'runtime/private_cars_public.c'), str(root / 'public.c')]
        (root / 'public.c').write_text(public)
        build(common + save_inputs + ['-o', str(root / 'save')])
        for mode in ('seed', 'load', 'secondary', 'backup-load', 'reconcile', 'transaction'):
            user = root / ('save-user' if mode != 'transaction' else 'transaction-user')
            user.mkdir(exist_ok=True)
            run(root / 'save', [root / 'catalog', 'on', mode, user])
        for mode in ('missing', 'secondary-refuse', 'backup-refuse'):
            run(root / 'save', [root / 'catalog', 'off', mode, root / 'save-user'])
        user = root / 'ordinary-user'
        user.mkdir()
        run(root / 'save', [absent, 'off', 'mw', user])
        # Original public load wrapper: retain the old direct PASS implementation.
        (root / 'public.c').write_text(public.replace('    carsave_public_load(c);', '    f_005ACB70_orig(c);'))
        build(common + save_inputs + ['-o', str(root / 'original-pass')])
        original_user = root / 'original-save'
        shutil.copytree(root / 'save-user', original_user)
        run(root / 'original-pass', [root / 'catalog', 'off', 'missing', original_user], expect=1)
        controls += 1
        (root / 'public.c').write_text(public)
        old_read = 'if (!file_info[k].snapshot) return read(fd,buf,n);'
        assert old_read in file_source
        (root / 'file.c').write_text(file_source.replace(old_read, 'return read(fd,buf,n);'))
        build(common + save_inputs + ['-o', str(root / 'shared-pack-offset')])
        user = root / 'shared-offset-user'
        user.mkdir()
        run(root / 'shared-pack-offset', [root / 'catalog', 'on', 'seed', user], expect=1)
        controls += 1
        missing_complete=file_source.replace('private_cars_save_complete(ok);','(void)ok;')
        assert missing_complete!=file_source
        (root / 'file.c').write_text(missing_complete)
        build(common + save_inputs + ['-o', str(root / 'missing-completion')])
        user=root/'missing-completion-user'; user.mkdir()
        run(root/'missing-completion', [root/'catalog','on','seed',user], expect=1)
        controls+=1
        print(f'public integration proof: runs={cases} assertions={assertions} rejected-controls={controls}; native ASan/UBSan')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
