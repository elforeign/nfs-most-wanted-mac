#!/usr/bin/env python3
"""Stock BMW: native Setup/list/unlock/purchase and profile round-trip proof.

The frozen original hook is a direct call to retail Setup. It must fail the
same positive career-lot and Quick Race list checks as the changed code passes.
Before: original career lot 0/32 and Quick Race 0/32 (positive checks fail).
After: both lists 32/32; Off restores 0/32 in each. The same bought-car save
loads On and Off; the original and four mutations are rejected (12 runs).
Blank-profile unlock regression: predecessor 4/4 locked, fixed 0/4 locked.
Production hooks and translated PC functions run under ASan/UBSan. Attrib,
UI rendering, list allocation/sorting and career allocation are explicit
contract doubles. The profile serializer, loader, integrity and public save
admission are real. All files are scratch; no game or real profile is used.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import carsave_public_test as saves

# SHA-256 of only the translated functions used below, with a hooked entry's
# definition normalized to its retail name. These do not depend on git history.
PINS = {
    '0056F180': ('20', 'a6dbccd1ad7df2193458dcd14a8293fcbcaa22eb7eabd2c7a0d4311cee07808b'),
    '005815B0': ('21', '9d0016b3013c2a5773b11e70da2d58d6b47427d4bcd4fc2d182a89527660a799'),
    '00581730': ('21', '94231749433d04566fbcfb0278f2d97a3f6b3ce89d91ae6f5d8fba7d52976e3e'),
    '0058A0D0': ('22', '6d5b834519b8a3300d917e4bbf9776a41f2737fbd9807638f2c4e18f18686e0b'),
    '0058A5F0': ('22', 'cd841b5d38fe65cdaed1da421f8b2537b9adf6f536c29f9e7818a466e214a23c'),
    '007BF7B0': ('52', '4cd54a634206e47753343fd141b8f10be5703762f5f1318c3fd29b74fa122f10'),
    '007C2780': ('52', 'be465fbcbcfb078abb24702acd9064e8019f9947d0f2f0294edf3713dd187531'),
    '007C1770': ('52', '63e3ca46328b27b886fce01c9147e91ab67439c662a95dd679f3e239bff47199'),
    '0058FD10_orig': ('22', '7459d493a1a0c5e74af5565a995bc080750a4f166ca50ca0af79241fe75a4bec'),
    '005ACB70_orig': ('23', '8d639e0776551e5dddc0c8778bb5f46d25f7aec13bdeef0f5e6089c90cc12485'),
    '0056D7A0': ('20', '59a000502166d96115248cbf762c81153decee35b58000ee95ea6bf9af57a284'),
    '0057F7B0': ('21', 'cdd8d00fbbebc043052d3933a1bcfd75d8b0563e9daa0a63e3c39815d434a9aa'),
    '005AC950': ('23', 'f9cd2ec21ce260b998859e064d041489bcedd729fd10ad67f3c9cb265ee4bf6e'),
    '0056D810': ('20', '7d1c5298562777ed47274580cba361378c4ccf539c0302cba43ce6179f15df9c'),
    '0056D8C0': ('20', '7eecddc1e6f659bd9d32c6c533420c4f4c9522fc0b78e8ffdc91aed99b499e10'),
    '0056D940': ('20', 'd7c61ac8a03bc5e7a241dda3483990c387405c18cc09ab62419ae4aa0e9e43c5'),
    '0058FC00': ('22', '410d4e06a3b015845208efd4ab071d89f12abd2c06be6652c82959e8b27cb33a'),
    '007C94B0': ('52', 'e3f090820214b2f0dc6beca4d29ce3183124c1281455562d42315df509a5efd3'),
    '0056ECC0': ('20', '3b3156fcb9cad8496afa0d8c3f5345d6f296d4abf9d6780c0e06af13ef76317b'),
    '0056DBE0': ('20', '88db4097f7d3bb680c95b6b1ac481743c140a04849f17e9179ad88876614a7a6'),
}

NATIVE = r'''
#include "funcs.h"
#include "host.h"
#include "carsave_public.h"
#include <CommonCrypto/CommonDigest.h>
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>
uint8_t *G;
static unsigned checks,failures,retail_calls,retail_writes,hashes,dialogs,accepted;
static int broken_serializer,missing_donor,create_fail;
static uint32_t arena=0x2000000,selected,created;
static const uint32_t profile=0x100000,db=0x100414,fedb=0x300000,screen=0x400000;
static const uint32_t bmw=0x23CC4A4E,bonus=0x81C2FEA6,porsche=0x3C4DA7DA;
static uint32_t bmw_layout=0x500000,bonus_layout=0x500100,price_layout=0x500200;
static const uint32_t settings=0x30001C;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL %u: %s\n",__LINE__,#x); } } while(0)
uint32_t gmalloc(uint32_t n) { uint32_t p=arena; arena+=(n+63)&~63u; assert(arena<0x3f00000); return p; }
void gfree(uint32_t p) { (void)p; }
uint32_t gstr(const char *s) { uint32_t p=gmalloc((uint32_t)strlen(s)+1); strcpy((char *)G+p,s); return p; }
void bad_jump(Cpu *c,uint32_t pc) { (void)c; fprintf(stderr,"unexpected jump %08X\n",pc); abort(); }
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp;fprintf(stderr,"ABI %08X\n",fn);abort();
}
static uint32_t layout(uint32_t key) {
 if(key==bmw) return bmw_layout;
 if(key==bonus) return bonus_layout;
 if(key==porsche) return price_layout;
 fprintf(stderr,"unknown frontend %08X\n",key);abort();
}
void f_0051E1A0(Cpu *c) { M32(c->ecx+8)=layout(M32(c->esp+4));c->eax=c->ecx;c->esp+=16; }
void f_004E4EA0(Cpu *c) { c->eax=c->ecx;c->esp+=16; }
void f_0045A430(Cpu *c) { c->esp+=4; }
void f_005816B0_orig(Cpu *c) { c->eax=M32(c->ecx+8)==0xCD88083F?6:41;c->esp+=4; }
void f_005816B0(Cpu *c) { f_005816B0_orig(c); }
size_t car_catalog_count(void) { return 0; }
const char *car_catalog_generation(void) { return ""; }
const CarCatalogSlot *car_catalog_inventory_get(size_t n) { (void)n;return NULL; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 uint32_t sp=c->esp;
 for(int i=n;i;--i) { c->esp-=4;M32(c->esp)=a[i-1]; }
 c->esp-=4;M32(c->esp)=0xFEEDF00D;
 switch(fn) {
 case 0x00455FD0: c->eax=!(missing_donor && a[1]==porsche);break;
 case 0x0051E1A0: f_0051E1A0(c);break;
 case 0x0045A430: f_0045A430(c);break;
 case 0x0056DBE0: f_0056DBE0(c);break;
 case 0x00747C00: c->eax=1;break;
 case 0x00598590: ++dialogs;c->eax=1;break;
 default: bad_jump(c,fn);
 }
 c->esp=sp;return c->eax;
}
/* UI/allocator boundaries. Native Setup and RefreshCarList are complete. */
void f_00516C90(Cpu *c) { c->esp+=16; }
void f_00524850(Cpu *c) { c->eax=0;c->esp+=4; }
void f_00514C70(Cpu *c) { c->esp+=4; }
void f_005268A0(Cpu *c) { c->eax=settings;c->esp+=4; }
void f_005AF1C0(Cpu *c) { c->eax=0x38A3C42C;c->esp+=4; }
void f_004061B0(Cpu *c) { c->eax=M32(c->ecx+0x10+4*M32(c->esp+4))+0x414;c->esp+=8; }
void f_0056ECF0(Cpu *c) { c->eax=c->ecx+20*M32(c->esp+4);c->esp+=8; }
void f_0056E2B0(Cpu *c) { c->eax=0;c->esp+=4; }
void f_00652AD0(Cpu *c) { c->eax=gmalloc(M32(c->esp+4));c->esp+=4; }
void f_00652B00(Cpu *c) { c->esp+=4; }
void f_00462830(Cpu *c) { c->esp+=8; }
void f_007B4E20(Cpu *c) { c->esp+=8; }
void f_007ADA50(Cpu *c) { c->esp+=4; }
void f_0058A9F0(Cpu *c) { if(M32(c->esp+4)==2) f_0058A5F0(c);else f_0058A0D0(c); }
/* Purchase allocation contract; notification, GetCost and SpendCash are native. */
void f_007A3DB0(Cpu *c) { c->eax=selected;c->esp+=4; }
void f_00599D20(Cpu *c) {
 CHECK(M32(c->esp+4)==M32(selected));
 if(create_fail) c->eax=0;
 else {
  created=c->ecx+199*20;memcpy(G+created,G+selected,20);
  M32(created)=199;M32(created+12)=0xF0002;M8(created+16)=33;M8(created+17)=2;c->eax=created;
  uint32_t custom=c->ecx+4000+33*0x198;memset(G+custom,0xff,0x198);M8(custom)=33;
  M8(c->ecx+0x8728+2*0x38)=2;
 }
 c->esp+=8;
}
void f_005910E0(Cpu *c) { c->esp+=4; }
void f_0056C5B0(Cpu *c) { c->esp+=4; }
void f_007A3D50(Cpu *c) { ++accepted;c->esp+=4; }
/* Equal-key obfuscation evaluates to zero; no gameplay manager/races run. */
void f_005AC7D0(Cpu *c) { c->eax=0;c->esp+=4; }
void f_005AC6F0(Cpu *c) { c->esp+=4; }
void f_005FB770(Cpu *c) { CHECK(M32(c->esp+8)==0);c->esp+=12; }
PROFILE_HELPERS
FEATURE
STUBS
GENERATED
static void invoke(Cpu *c,uint32_t self,void (*fn)(Cpu *)) {
 uint32_t sp=c->esp;c->ecx=self;c->esp-=4;M32(c->esp)=0;fn(c);CHECK(c->esp==sp);
}
static void setup(Cpu *c,unsigned context,unsigned player) {
 M32(fedb+0x12c)=context;M32(screen+0x1a8)=player;
 invoke(c,screen,f_007C2780);
}
static unsigned listed(unsigned handle,unsigned *locked) {
 uint32_t head=screen+0x2c;unsigned n=0;*locked=0;
 for(uint32_t p=M32(head);p!=head;p=M32(p)) {
  assert(++n<=200);if(M32(p+8)==handle) { *locked=M8(p+12);return 1; }
 }
 return 0;
}
static void notification(Cpu *c,uint32_t msg) {
 uint32_t sp=c->esp;c->ecx=screen;c->esp-=20;M32(c->esp)=0;M32(c->esp+4)=msg;
 M32(c->esp+8)=M32(c->esp+12)=M32(c->esp+16)=0;f_007C1770(c);CHECK(c->esp==sp);
}
static void serialize(Cpu *c,uint32_t buffer) {
 uint32_t sp=c->esp;c->ecx=profile;c->esp-=12;M32(c->esp)=0;M32(c->esp+4)=buffer;M32(c->esp+8)=CARSAVE_PUBLIC_SIZE;
 carsave_public_serialize(c);CHECK(c->esp==sp);CHECK(carsave_public_save_allowed());CHECK(!carsave_public_verify(G+buffer,CARSAVE_PUBLIC_SIZE));
}
static void load(Cpu *c,uint32_t buffer) {
 uint32_t sp=c->esp;c->ecx=profile;c->esp-=20;M32(c->esp)=0;M32(c->esp+4)=buffer;M32(c->esp+8)=CARSAVE_PUBLIC_SIZE;
 M32(c->esp+12)=M32(c->esp+16)=0;carsave_public_load(c);CHECK(c->esp==sp);CHECK(c->eax&255);CHECK(carsave_public_save_allowed());
}
static void stock(Cpu *c,uint32_t base,unsigned i,uint32_t fe,uint32_t pv) {
 uint32_t r=base+20*i;M32(r)=i;M32(r+4)=fe;M32(r+8)=pv;invoke(c,r,f_005815B0);
}
int main(int argc,char **argv) {
 assert(argc==4);int active=strcmp(argv[1],"off") && strcmp(argv[1],"missing");
 G=calloc(1,0x4000000);assert(G);Cpu c={.esp=0x1800000,.cw=0x37f,.ebx=7,.esi=8,.edi=9,.ebp=10};
 M32(0x91CF90)=fedb;M32(fedb+0x10)=profile;M32(fedb+0x14)=0x120000;M32(fedb+8)=0xA4B5C6D7;
 M32(0x9173F8)=M32(0x9173FC)=0x9173F8;
 M32(0x91E004)=0x520000; /* native race-save reservation, zero race rows */
 /* PC 0058A1A1's type-41 dispatch byte: 1 selects the ordinary rank gate,
  * not the special always-unlocked starter-type branch (zero). */
 M8(0x58A420+41-5)=1;
 M32(bmw_layout+0x38)=M32(bonus_layout+0x38)=M32(price_layout+0x38)=1;
 M32(bmw_layout+0x4c)=500000;M8(bmw_layout+0x59)=M8(bonus_layout+0x59)=15;
 M32(price_layout+0x4c)=75000;M8(price_layout+0x58)=1;M8(price_layout+0x59)=6;
 M8(profile+0xb0)=15;M32(profile+0xb4)=2000000;M32(profile+0xac)=0x31;
 for(unsigned player=0;player<2;++player) {
  uint32_t base=M32(fedb+0x10+4*player)+0x414;
  for(unsigned i=0;i<200;++i) M32(base+20*i)=UINT32_MAX;
  stock(&c,base,82,bmw,0x4E085FB3);stock(&c,base,83,bonus,0xCD88083F);
  stock(&c,base,81,porsche,0x3C4DA7DA);
  stock(&c,base,1,bmw,0x39DAE0F8);M32(base+20)=0x12345678;M32(base+20+12)=0xF0020;
 }
 uint8_t before[CARSAVE_PUBLIC_FE_SIZE],second[CARSAVE_PUBLIC_FE_SIZE],starter[20],bonus_before[20],story[0x27c];
 memcpy(before,G+db,sizeof before);memcpy(second,G+0x120414,sizeof second);
 memcpy(starter,G+db+20,20);memcpy(bonus_before,G+db+83*20,20);
 memcpy(story,G+profile+0xa8,sizeof story);
 CHECK(M32(db+82*20+12)==0x20020);CHECK(M32(db+83*20+12)==0x20020);
 M32(screen+0x2c)=M32(screen+0x30)=screen+0x2c;M32(settings+0x10)=81;M32(settings+0x14)=81;
 if(!strcmp(argv[1],"missing")) missing_donor=1;
 carsave_public_init(argv[3],NULL);
 uint32_t buffer=gmalloc(CARSAVE_PUBLIC_SIZE);
 if(!strcmp(argv[2],"load")) {
  FILE *f=fopen(argv[3],"rb");assert(f);CHECK(fread(G+buffer,1,CARSAVE_PUBLIC_SIZE,f)==CARSAVE_PUBLIC_SIZE);fclose(f);
  load(&c,buffer);
  CHECK(!memcmp(G+db,G+buffer+0x5BA1,CARSAVE_PUBLIC_FE_SIZE));
  CHECK(M32(db+199*20+8)==0x4E085FB3 && M32(db+199*20+12)==0xF0002);
  CHECK(M8(db+199*20+16)==33 && M8(db+199*20+17)==2);CHECK(M32(profile+0xb4)==1925000);
  CHECK(M32(profile+0xac)==0x31 && M8(profile+0xb0)==15);
  uint8_t owned[20],custom[0x198],career[0x38];memcpy(owned,G+db+199*20,20);
  memcpy(custom,G+db+4000+33*0x198,sizeof custom);memcpy(career,G+db+0x8728+2*0x38,sizeof career);
  M32(settings+0x10)=82;
  setup(&c,0x8001,0);unsigned locked;CHECK(listed(82,&locked)==(unsigned)active);
  CHECK(!memcmp(owned,G+db+199*20,20));CHECK(!memcmp(custom,G+db+4000+33*0x198,sizeof custom));
  CHECK(!memcmp(career,G+db+0x8728+2*0x38,sizeof career));
  setup(&c,1,0);CHECK(listed(199,&locked) && !locked);
  M32(settings+0x10)=199;setup(&c,0,0);CHECK(listed(199,&locked) && !locked);
  CHECK(M32(screen+0x1ac)==0xF0002 && M32(settings+0x10)==199);
  serialize(&c,buffer);load(&c,buffer);CHECK(!memcmp(owned,G+db+199*20,20));
 } else {
  unsigned career_found=0,qr_found=0,blank_checks=0,blank_locked=0;
  for(unsigned player=0;player<2;++player) {
   uint32_t p=M32(fedb+0x10+4*player),base=p+0x414;
   for(unsigned rank=1;rank<=16;++rank) {
    M8(profile+0xb0)=rank;
    for(unsigned kind=0;kind<2;++kind) {
     setup(&c,kind?0:0x8001,player);unsigned locked;
     unsigned found=listed(82,&locked);CHECK(found==(unsigned)active);if(found) CHECK(!locked);
     if(rank==16 && found) { ++blank_checks;blank_locked+=locked!=0; }
     if(kind) qr_found+=found;else career_found+=found;
     CHECK(!listed(83,&locked));CHECK(!listed(0x12345678,&locked));
     CHECK(M32(screen+0x1ac)==0xF0001);
    }
   }
   CHECK(M32(base+82*20+12)==(active?0x20001:0x20020));
  }
  M8(profile+0xb0)=15;
  printf("lists career=%u/32 quick-race=%u/32 expected=%u/32; blank locked=%u/%u\n",career_found,qr_found,active?32:0,blank_locked,blank_checks);
  CHECK(!memcmp(starter,G+db+20,20));CHECK(!memcmp(bonus_before,G+db+83*20,20));
  CHECK(!memcmp(story,G+profile+0xa8,sizeof story));
  CHECK(M32(bmw_layout+0x4c)==(active?75000:500000));CHECK(M8(bmw_layout+0x58)==0 && M8(bmw_layout+0x59)==(active?16:15));
  CHECK(M32(price_layout+0x4c)==75000 && M8(price_layout+0x59)==6);
  /* Except the two stock flags, every FE byte stays identical. */
  M32(db+82*20+12)=0x20020;M32(0x120414+82*20+12)=0x20020;
  CHECK(!memcmp(before,G+db,sizeof before));CHECK(!memcmp(second,G+0x120414,sizeof second));
  if(active) {
   M8(profile+0xb0)=15;setup(&c,0x8001,0);selected=db+82*20;
   for(unsigned fail=0;fail<2;++fail) {
    create_fail=fail;created=0;M32(profile+0xb4)=2000000;notification(&c,0xD05FC3A3);
    CHECK(M32(profile+0xb4)==2000000-(fail?0:75000));
    if(!fail) CHECK(created==db+199*20 && M32(profile+0xa8)==199);
   }
   M32(profile+0xb4)=1925000;
   serialize(&c,buffer);uint8_t saved[CARSAVE_PUBLIC_FE_SIZE];memcpy(saved,G+db,sizeof saved);
   memset(G+db,0xA6,sizeof saved);load(&c,buffer);CHECK(!memcmp(saved,G+db,sizeof saved));
   CHECK(M32(fedb+8)==0xA4B5C6D7);CHECK(!dialogs);
   FILE *f=fopen(argv[3],"wb");assert(f);CHECK(fwrite(G+buffer,1,CARSAVE_PUBLIC_SIZE,f)==CARSAVE_PUBLIC_SIZE);fclose(f);
   /* Quick Race acceptance uses the real locked/delay guard. */
   M32(settings+0x10)=82;setup(&c,0,0);unsigned locked;unsigned found=listed(82,&locked);
   CHECK(found && !locked);CHECK(M32(screen+0x1ac)==0xF0001);
    if(found && M32(screen+0x1ac)==0xF0001) {
    M32(screen+0x34)=M32(screen+0x2c);while(M32(M32(screen+0x34)+8)!=82) M32(screen+0x34)=M32(M32(screen+0x34));
    M8(profile+0x86)=1;M32(screen+0x38)=0;M32(0x925b14)=4000;MF32(0x890984)=0.00025f;MF32(0x8910f0)=0.25f;
    notification(&c,0x406415E3);CHECK(accepted==1);
   }
   CHECK(M32(db+199*20+12)==0xF0002 && M8(db+199*20+16)==33 && M8(db+199*20+17)==2);
   /* Replacing the frontend vault must use new live layouts, never cached ones. */
   uint32_t retired=bmw_layout;bmw_layout=0x530000;price_layout=0x530100;
   M32(retired+0x4c)=57000;M32(bmw_layout+0x38)=1;M32(bmw_layout+0x4c)=500000;M8(bmw_layout+0x59)=15;
   M32(price_layout+0x38)=1;M32(price_layout+0x4c)=75000;M8(price_layout+0x59)=6;
   setup(&c,0,0);CHECK(M32(bmw_layout+0x4c)==75000);CHECK(M32(retired+0x4c)==57000);
  } else {
   /* A prior On save can leave the canonical stock flag enabled. Off hides it. */
   M32(db+82*20+12)=0x20001;M32(settings+0x10)=82;setup(&c,0,0);unsigned locked;
   CHECK(!listed(82,&locked));CHECK(M32(screen+0x1ac)==0xF0001);
  }
 }
 CHECK(M32(fedb+8)==0xA4B5C6D7);CHECK(c.ebx==7 && c.esi==8 && c.edi==9 && c.ebp==10 && c.cw==0x37f);
 printf("bmw %s %s checks=%u failures=%u\n",argv[1],argv[2],checks,failures);free(G);return !!failures;
}
'''


def translated():
    result = []
    for name, (chunk, pin) in PINS.items():
        path = ROOT / f'recomp/out/speed_{chunk}.c'
        if not path.is_file():
            print('SKIP: translated native functions absent')
            raise SystemExit(3)
        source = path.read_text()
        pattern = r'void f_' + name.removesuffix('_orig') + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}'
        code = re.search(pattern, source, re.S).group()
        code = re.sub(r'void f_[0-9A-F]{8}(?:_orig)?\(', 'void f_' + name + '(', code, count=1)
        assert hashlib.sha256(code.encode()).hexdigest() == pin, name + ': native fragment changed'
        if name == '007C2780':
            code = code.replace('void f_007C2780(', 'void f_007C2780_orig(', 1)
        result.append(code)
    return '\n'.join(result)


def main():
    generated = translated()
    helper = saves.NATIVE[saves.NATIVE.index('void f_0057F7B0('):saves.NATIVE.index('/* Frozen pre-admission public behavior;')]
    for native in ('0057F7B0', '005AC950'):
        helper = re.sub(r'void f_' + native + r'\(Cpu \*c\) \{[^\n]+\}\n', '', helper)
    feature = (ROOT / 'runtime/bmw_availability.c').read_text()
    baseline = 'void f_007C2780_orig(Cpu *c); void f_007C2780(Cpu *c) { f_007C2780_orig(c); }'
    source = NATIVE.replace('PROFILE_HELPERS', helper).replace('GENERATED', generated)
    with tempfile.TemporaryDirectory(prefix='bmw-availability-') as directory:
        root = Path(directory)
        cases = 0
        common = ['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                  '-Wno-deprecated-declarations', '-I', str(ROOT / 'runtime'),
                  '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out')]

        def build(args):
            built = subprocess.run(common + args, capture_output=True, text=True)
            assert not built.returncode, built.stderr[-5000:]

        # Native functions/admission are identical in every control. Compile
        # them once, then link each tiny production/mutant hook separately.
        unit = source.replace('FEATURE', '')
        calls = set(re.findall(r'\b(f_[0-9A-F]{8}(?:_orig)?)\(', unit))
        provided = set(re.findall(r'void (f_[0-9A-F]{8}(?:_orig)?)\(Cpu \*(?:restrict )?c\)\s*\{', unit))
        provided.add('f_007C2780')
        stubs = '\n'.join(f'void {n}(Cpu *c) {{ bad_jump(c,0x{n[2:10]}u); }}' for n in sorted(calls - provided))
        fixture = root / 'fixture.c'
        fixture.write_text(unit.replace('STUBS', stubs))
        build(['-c', str(fixture), '-o', str(root / 'fixture.o')])
        build(['-c', str(ROOT / 'runtime/carsave_public.c'), '-o', str(root / 'save.o')])
        wrong_price = feature.replace('= price;', '= 1;')
        wrong_asset = feature.replace('#define BMW_STOCK 0x4E085FB3u', '#define BMW_STOCK 0xCD88083Fu')
        retail_unlock = feature.replace('M8(M32(instance + 8) + 0x59) = 16;', '/* authored unlock threshold */')
        owned = feature.replace('M16(row + 16) != 0xFFFF ||', '').replace(
            '(flags != 0x20020 && flags != 0x20001)', '(flags != 0x20020 && flags != 0x20001 && flags != 0xF0002)')
        for control in (wrong_price, wrong_asset, owned, retail_unlock):
            assert control != feature
        variants = [('production', feature), ('original', baseline), ('wrong-price', wrong_price),
                    ('wrong-asset', wrong_asset), ('owned-row', owned), ('retail-unlock', retail_unlock)]
        for label, implementation in variants:
            path = root / 'hook.c'
            path.write_text('#include "host.h"\n' + implementation)
            exe = root / label
            build([str(path), str(root / 'fixture.o'), str(root / 'save.o'), '-o', str(exe)])
            modes = [('on', 'seed', None), ('off', 'seed', 'off'), ('on', 'load', None),
                     ('off', 'load', 'off'), ('missing', 'seed', None), ('off', 'seed', '0'),
                     ('on', 'seed', 'on')] if label == 'production' else [('on', 'seed', None)]
            for mode, operation, setting in modes:
                env = dict(os.environ)
                env.pop('NFS_BMW_AVAILABLE', None)
                if setting is not None:
                    env['NFS_BMW_AVAILABLE'] = setting
                payload = root / (label + '.save')
                ran = subprocess.run([str(exe), mode, operation, str(payload)], env=env, capture_output=True, text=True, timeout=60)
                print(label, ran.stdout.strip())
                expected = 0 if label == 'production' else 1
                assert ran.returncode == expected, ran.stderr[-5000:] + ran.stdout[-2000:]
                assert 'runtime error:' not in ran.stderr and 'AddressSanitizer' not in ran.stderr, ran.stderr[-2000:]
                if label == 'original':
                    assert 'career=0/32 quick-race=0/32 expected=32/32' in ran.stdout
                if label == 'retail-unlock':
                    assert 'blank locked=4/4' in ran.stdout
                cases += 1
        print(f'BMW availability PASS: {cases} processes, original and 4 mutations rejected')


if __name__ == '__main__':
    main()
