"""Actual translated retail save/load/create/delete through both profile wrappers.
Public-only export: fixed 1/1, predecessor 540af7d2 0/1 (FileNotFoundError).
No game or emulation. Good 92/92 + restart 4/4 per build, including independent Light and colour.
Changed default assertions on ORIGINAL On core: 90/92 (2 failures); fixed 92/92.
Colour alone stays Off; explicit Light On persists across save/load and restart.
Original omission fails 24; explicit requested roundtrip 7/7 vs omitted callbacks
6/7; load 12; save/completion 9; default 16; create 1; delete 10.
Helpers for audio, catalog and customization allocation are contract fixtures.
CPU wrapper comparisons are equivalence evidence, not predecessor failures.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]

def function(source, name):
    start=re.search(r'(?:static )?(?:void|int) '+name+r'\([^\n]*\)\s*\{',source)
    assert start, name
    pos=start.end(); depth=1
    while depth:
        if source[pos]=='{': depth+=1
        elif source[pos]=='}': depth-=1
        pos+=1
    return source[start.start():pos]+'\n'

PREFIX=r'''
#include "funcs.h"
#include "host.h"
#include "private_cars.h"
#include "save_timing.h"
#include "underglow.h"
#include "underglow_store.h"
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <CommonCrypto/CommonDigest.h>
static unsigned checks,failures,retail_hashes;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL line %u: %s\n",__LINE__,#x); } } while(0)
#define SAVE_SIZE UG_SAVE_BYTES
void f_0057F7B0(Cpu *c) { c->eax=M32(c->esp+4)+0x59F9; c->esp+=12; }
void f_005AC950(Cpu *c) { M32(c->ecx)=0xC0DEC0DE; c->eax=M32(c->esp+4)+0x59F9; c->esp+=12; }
void f_006530A0(Cpu *c) { c->eax=0; c->esp+=8; }
void f_00576C50(Cpu *c) { abort(); }
void f_0057FFB0(Cpu *c) { c->eax=M32(c->esp+4)+0x36; c->esp+=8; }
void f_00580080(Cpu *c) { c->eax=M32(c->esp+4)+0x36; c->esp+=8; }
void f_0056E180(Cpu *c) { c->eax=M32(c->esp+4)+4+19*48; c->esp+=4; }
void f_0056E200(Cpu *c) { c->eax=M32(c->esp+4)+4+19*48; c->esp+=4; }
void f_0057F920(Cpu *c) {
    uint32_t p=M32(c->esp+4), out=M32(c->esp+8), n=M32(c->esp+12);
    CC_MD5(G+p,n,G+out); c->esp+=20;
}
void f_0057F9F0(Cpu *c) {
    unsigned char d[16]; ++retail_hashes;
    CC_MD5(G+M32(c->esp+4),M32(c->esp+12),d);
    c->eax=0xA5000000u | !memcmp(d,G+M32(c->esp+8),16); c->esp+=16;
}
void f_004C3790(Cpu *c) { c->eax=M32(c->esp+4); c->esp+=8; }
void f_004AECB0(Cpu *c) { c->esp+=12; }
void f_005CC240(Cpu *c) { c->eax=0; c->esp+=4; }
void f_00405DA0(Cpu *c) { c->eax=c->ecx; c->esp+=20; }
void f_0065FAF0(Cpu *c) { c->esp+=4; }
void f_00628CC0(Cpu *c) { c->eax=48; c->esp+=4; }
void f_0067AC60(Cpu *c) { c->eax=0; c->esp+=4; }
void f_00634370(Cpu *c) { c->eax=0; c->esp+=4; }
void f_004655F0(Cpu *c) { c->esp+=4; }
void f_00401800(Cpu *c) { c->esp+=4; }

#define FEDB 0x0091CF90u
#define MW_SONGS 26
uint8_t *G;
int diag_on;
static const char *root;
const char *userdata_dir(void) { return root; }
void diag_event(const char *fmt,...) { }
uint32_t gmalloc(uint32_t n) { static uint32_t top=0xB00000; uint32_t p=top; top+=(n+63)&~63u; assert(top<0xE00000); return p; }
uint32_t gstr(const char *s) { uint32_t p=gmalloc((uint32_t)strlen(s)+1); strcpy((char *)G+p,s); return p; }
uint32_t call_guest(Cpu *c,uint32_t addr,int n,const uint32_t *args) { abort(); }
size_t private_cars_count(void) { return 0; }
uint32_t car85_count(void) { return 84; }
unsigned car85_player_count(void) { return 0; }
void f_005ACB70_orig(Cpu *c);
void f_0058FD10_orig(Cpu *c);
void carsave_public_load(Cpu *c) { f_005ACB70_orig(c); }
void carsave_public_serialize(Cpu *c) { f_0058FD10_orig(c); }
size_t private_cars_inventory_count(void) { return 0; }
const PrivateCar *private_cars_inventory_get(size_t i) { return NULL; }
const char *private_cars_generation(void) { return ""; }
const char *private_cars_verify_save(const void *b,size_t size) {
    unsigned char d[16]; if(size!=SAVE_SIZE) return "size";
    CC_MD5((const unsigned char *)b+16,(CC_LONG)size-32,d);
    return memcmp(d,(const unsigned char *)b+size-16,16)?"hash":NULL;
}
const char *private_cars_metadata_admit(const void *b,size_t n,const unsigned char *used) { return NULL; }
const char *private_cars_metadata_stage(void *b,size_t n,const unsigned char *used) { return NULL; }
const char *car85_profile_dependencies(Cpu *c,uint32_t db,const PrivateCar *cars,size_t n,unsigned char *used) { memset(used,0,12); return NULL; }
const char *car85_profile_preflight(Cpu *c,uint32_t db,const PrivateCar *cars,size_t n) { return NULL; }
int car85_profile_scratch(void) { return 1; }
static int appended, pending_trax_valid;
static char default_name[40];
static int songs_ready(void) { return 0; }
static uint32_t profile(void) { return 0; }
static void songs_append(void) { abort(); }
static void list_from_career(uint32_t p,int load) { abort(); }
static int list_on(void) { return 0; }
static void list_to_career(uint32_t p) { abort(); }
static void profile_name(uint32_t p,char *name,size_t size) { snprintf(name,size,"TEST"); }
static void sidecar_stage(uint32_t p) { abort(); }
static void xlog(const char *fmt,...) { }
static unsigned save_fail_seq,save_commit_seq;
void extratrax_save_complete(int ok) { }
void padbind_save_complete(int ok) { }
void private_cars_save_complete(int ok) { }
static int fail_creation;
void f_005AB450_orig(Cpu *c) { c->eax=0xABCDEF12; c->esp+=12; }
void f_005815B0(Cpu *c) { M32(c->ecx+12)=0; M16(c->ecx+16)=0xFFFF; c->esp+=4; }
void f_00580CD0(Cpu *c) {
    c->eax=0;
    if(!fail_creation) for(unsigned i=0;i<76;++i) {
        uint32_t p=c->ecx+4000+i*408;
        if(G[p+404]==255) { G[p+404]=(uint8_t)i; c->eax=p; break; }
    }
    c->esp+=4;
}
void f_005816B0(Cpu *c) { c->eax=0; c->esp+=4; }
void f_00739A70(Cpu *c) { c->esp+=20; }
void f_00759800(Cpu *c) { c->esp+=4; }
void f_007594A0(Cpu *c) { c->esp+=4; }
void f_0056F2F0(Cpu *c) { c->esp+=8; }
void f_0056F100(Cpu *c) { c->eax=c->ecx+4000+M32(c->esp+4)*408; c->esp+=8; }
void f_0056F120(Cpu *c) { c->eax=c->ecx+0x8728+M32(c->esp+4)*56; c->esp+=8; }
void f_00580FD0(Cpu *c) { c->esp+=8; }
'''
MAIN=r'''
static void cpu_equivalence(uint32_t p,uint32_t buffer) {
    void (*originals[])(Cpu *)={f_0058FD10_orig,f_005ACB70_orig,f_00590BD0_orig,f_00581370_orig,f_005AB450_orig};
    void (*hooks[])(Cpu *)={f_0058FD10,f_005ACB70,f_00590BD0,f_00581370,f_005AB450};
    unsigned char *memory=malloc(0x1000000); assert(memory);
    for(unsigned op=0;op<5;++op) {
        Cpu before={0}; before.esp=0xF00000; before.ecx=p;
        before.eax=0x13572468; before.ebx=19; before.edx=71; before.esi=31;
        before.edi=47; before.ebp=83; before.st[3]=.125; before.mm[5]=0xF123ABCD;
        before.mxcsr=0x1F80; before.cw=0x37F;
        uint32_t args[4]={buffer,SAVE_SIZE,0,0}; unsigned n=op==1?4:2;
        if(op==2) { before.ecx=p+0x414; args[0]=0; args[1]=2; }
        if(op==3) { before.ecx=p+0x414; args[0]=1; args[1]=2; args[2]=0; n=3; }
        for(unsigned j=n;j>0;--j) { before.esp-=4; M32(before.esp)=args[j-1]; }
        before.esp-=4; M32(before.esp)=0;
        memcpy(memory,G,0x1000000);
        Cpu raw=before; originals[op](&raw);
        memcpy(G,memory,0x1000000);
        Cpu wrapped=before; hooks[op](&wrapped);
        CHECK(!memcmp(&raw,&wrapped,sizeof raw));
        memcpy(G,memory,0x1000000);
    }
    free(memory);
}
static Cpu cpu(void) { Cpu c={0}; c.esp=0xF00000; c.ebx=19; c.esi=31; c.edi=47; c.ebp=83; return c; }
static void invoke(uint32_t p,uint32_t b,int save) {
    Cpu c=cpu(); uint32_t sp=c.esp;
    if(!save) { c.esp-=4; M32(c.esp)=0; c.esp-=4; M32(c.esp)=0; }
    c.esp-=4; M32(c.esp)=SAVE_SIZE; c.esp-=4; M32(c.esp)=b; c.esp-=4; M32(c.esp)=0; c.ecx=p;
    if(save) f_0058FD10(&c); else { f_005ACB70(&c); CHECK((c.eax&255)==1); }
    CHECK(c.esp==sp && c.ebx==19 && c.esi==31 && c.edi==47 && c.ebp==83);
}
static void defaults(uint32_t p) {
    Cpu c=cpu(); c.ecx=p; c.esp-=12; f_005AB450(&c);
    CHECK(c.esp==0xF00000 && c.eax==0xABCDEF12);
}
static void seed(uint32_t p,const char *name) {
    memset(G+p,0,0x9CB0); strcpy((char *)G+p,name);
    memset(G+p+0x414,0xFF,0x8CC8);
    uint32_t db=p+0x414;
    for(unsigned i=0;i<3;++i) {
        uint32_t r=db+i*20; M32(r)=i; M32(r+4)=0x1234; M32(r+8)=0x5678;
        M32(r+12)=i?0x10002:0x10001; G[r+16]=(uint8_t)i; G[r+17]=255;
        G[db+4000+i*408+404]=(uint8_t)i;
    }
}
static void complete(int ok) {
    unsigned seq=save_fail_seq, commits=save_commit_seq;
    M32(0x400+0x300)=1; M32(0x400+0x2f4)=0;
    if(ok) ++save_commit_seq; else ++save_fail_seq;
    save_sidecars_complete(0x400,0xb,seq,commits);
}
static uint32_t creation(uint32_t p,int hooked) {
    Cpu c=cpu(); c.ecx=p+0x414; c.esp-=4; M32(c.esp)=2; c.esp-=4; M32(c.esp)=0;
    c.esp-=4; M32(c.esp)=0;
    if(hooked) f_00590BD0(&c); else f_00590BD0_orig(&c);
    CHECK(c.esp==0xF00000 && c.ebx==19 && c.esi==31 && c.edi==47 && c.ebp==83);
    return c.eax;
}
static unsigned deletion(uint32_t p,uint32_t handle,unsigned kind,int hooked) {
    Cpu c=cpu(); c.ecx=p+0x414; c.esp-=4; M32(c.esp)=0; c.esp-=4; M32(c.esp)=kind;
    c.esp-=4; M32(c.esp)=handle; c.esp-=4; M32(c.esp)=0;
    if(hooked) f_00581370(&c); else f_00581370_orig(&c);
    CHECK(c.esp==0xF00000 && c.ebx==19 && c.esi==31 && c.edi==47 && c.ebp==83);
    return c.eax&255;
}
int main(int argc,char **argv) {
    assert(argc==3); root=argv[1]; G=calloc(1,0x1000000); assert(G);
    uint32_t p=0x100000, other=0x120000, b=0x200000, baseline=0x220000, second=0x240000;
    M32(FEDB)=0x800000; M32(0x800008)=0xC0DEC0DE; M32(0x800010)=p;
    seed(p,"ALPHA"); seed(other,"BETA");
    if(!strcmp(argv[2],"restart")) {
        char path[4096]; snprintf(path,sizeof path,"%s/retail",root);
        FILE *f=fopen(path,"rb"); assert(f); assert(fread(G+b,1,SAVE_SIZE,f)==SAVE_SIZE); fclose(f);
        invoke(p,b,0); CHECK(underglow_get(p,1)==2 && underglow_get(p,2)==4);CHECK(underglow_light_get(p,1)==1 && !underglow_light_get(p,2));
        printf("restart good=%u broken=%u\n",checks-failures,failures); free(G); return failures?1:0;
    }
    if(!strcmp(argv[2],"roundtrip")) {
        // Supply the requested core state even when the old wrappers have no callbacks.
        underglow_profile_default(p); invoke(p,baseline,1);
        CHECK(underglow_set(p,1,2)); invoke(p,b,1); CHECK(!memcmp(G+b,G+baseline,SAVE_SIZE)); complete(1);
        underglow_profile_default(p); invoke(p,b,0);
        unsigned observed=underglow_get(p,1); CHECK(observed==2);
        printf("requested-roundtrip requested=2 observed=%u retail-bytes=%u unchanged=%d good=%u broken=%u\n",
               observed,SAVE_SIZE,!memcmp(G+b,G+baseline,SAVE_SIZE),checks-failures,failures);
        free(G); return failures?1:0;
    }
    defaults(p); CHECK(underglow_get(p,1)==0);CHECK(!underglow_light_get(p,1));
    invoke(p,baseline,1); // Off retail baseline
    CHECK(underglow_set(p,1,2)); CHECK(underglow_set(p,2,4));
    CHECK(underglow_get(p,1)==2 && underglow_get(p,2)==4);
    CHECK(!underglow_light_get(p,1));
    CHECK(underglow_light_set(p,1,1));CHECK(underglow_light_get(p,1)==1);
    CHECK(underglow_light_set(p,2,0));CHECK(!underglow_light_get(p,2));
    CHECK(!underglow_set(p,0,2) && !underglow_set(p,1,8));
    invoke(p,b,1); CHECK(!memcmp(G+b,G+baseline,SAVE_SIZE)); complete(1);
    defaults(p); CHECK(underglow_get(p,1)==0); invoke(p,b,0);
    CHECK(underglow_get(p,1)==2 && underglow_get(p,2)==4);
    CHECK(underglow_light_get(p,1)==1 && !underglow_light_get(p,2));
    // Failed completion cannot overwrite this exact digest's old cosmetic state.
    CHECK(underglow_set(p,1,1)); invoke(p,b,1); complete(0); complete(1);
    invoke(p,b,0); CHECK(underglow_get(p,1)==2);
    // Profile switch (including name reuse at the same pointer) restores by digest.
    defaults(other); CHECK(underglow_get(p,1)==0 && underglow_get(other,1)==0);
    CHECK(underglow_set(other,1,7)); invoke(other,second,1); complete(1);
    invoke(p,b,0); CHECK(underglow_get(p,1)==2); invoke(other,second,0); CHECK(underglow_get(other,1)==7);
    invoke(p,b,0); strcpy((char *)G+p,"REUSED"); CHECK(underglow_get(p,1)==0); strcpy((char *)G+p,"ALPHA");
    // Valid retail bytes with a new name have a different digest: Off.
    memcpy(G+second,G+b,SAVE_SIZE); G[second+0x5A0D]^=1;
    CC_MD5(G+second+16,SAVE_SIZE-32,G+second+SAVE_SIZE-16);
    invoke(p,second,0); CHECK(underglow_get(p,1)==0); invoke(p,b,0);
    // Failure must leave existing choices; successful sell/rebuy clears reused identity.
    CHECK(!deletion(p,1,4,1)); CHECK(underglow_get(p,1)==2);
    fail_creation=1; CHECK(!creation(p,1)); fail_creation=0; CHECK(underglow_get(p,1)==2);
    CHECK(deletion(p,1,2,1)); CHECK(underglow_get(p,1)==0 && underglow_get(p,2)==4);CHECK(!underglow_light_get(p,1));
    CHECK(creation(p,1)==p+0x414+20); CHECK(underglow_get(p,1)==0);
    // Isolate the creation reset: bypass delete hook then recreate identical row.
    CHECK(underglow_set(p,1,2)); CHECK(deletion(p,1,2,0));
    CHECK(creation(p,1)==p+0x414+20); CHECK(underglow_get(p,1)==0);
    // Isolate deletion reset: recreate via original, without the creation hook.
    CHECK(underglow_set(p,1,2)); CHECK(deletion(p,1,2,1));
    CHECK(creation(p,0)==p+0x414+20); CHECK(underglow_get(p,1)==0);
    // Changed customization or type invalidates an old identity.
    CHECK(underglow_set(p,1,2)); G[p+0x414+20+16]=3; CHECK(underglow_get(p,1)==0);
    G[p+0x414+20+16]=1; M32(p+0x414+20+8)^=1; CHECK(underglow_get(p,1)==0);
    // A pending snapshot cannot escape across a load/default or later failed save.
    invoke(p,b,0); CHECK(underglow_set(p,1,1)); invoke(p,b,1); defaults(other); complete(1);
    invoke(p,b,0); CHECK(underglow_get(p,1)==2);
    cpu_equivalence(p,b);
    invoke(p,b,0);
    // Leave a real serialized payload plus snapshot for an independent process.
    CHECK(underglow_set(p,1,2) && underglow_set(p,2,4));CHECK(underglow_light_set(p,1,1));CHECK(underglow_light_set(p,2,0)); invoke(p,b,1); complete(1);
    char path[4096]; snprintf(path,sizeof path,"%s/retail",root);
    FILE *f=fopen(path,"wb"); assert(f); assert(fwrite(G+b,1,SAVE_SIZE,f)==SAVE_SIZE); fclose(f);
    CHECK(!private_cars_verify_save(G+b,SAVE_SIZE));
    printf("lifecycle good=%u broken=%u retail-bytes=%u unchanged=%d hashes=%u\n",checks-failures,failures,SAVE_SIZE,!memcmp(G+b,G+baseline,SAVE_SIZE),retail_hashes);
    free(G); return failures?1:0;
}
'''

def wrapper_variants(runtime):
    public=(runtime/'extratrax_public.c').read_text()
    variants=[('public',''.join(function(public,'f_'+a) for a in ('005AB450','005ACB70','0058FD10')))]
    private_path=runtime/'extratrax.c'
    if private_path.is_file():
        private=private_path.read_text()
        wrappers=function(private,'f_005AB450')
        wrappers+=private[private.index('// Profile admission precedes'):private.index('// UserProfile::SaveToBuffer')]
        wrappers+=function(private,'f_0058FD10')
        variants.append(('private-trax-off',wrappers))
    return variants

def main():
    files={'0058FD10':'speed_22.c','005ACB70':'speed_23.c','00590BD0':'speed_22.c','00581370':'speed_21.c'}
    if any(not (ROOT/'recomp/out'/f).is_file() for f in files.values()):
        print('SKIP: translated retail fixtures absent'); return 3
    generated=[]
    for addr,f in files.items():
        text=(ROOT/'recomp/out'/f).read_text()
        name='f_'+addr+('_orig' if 'void f_'+addr+'_orig(' in text else '')
        body=function(text,name).replace('void '+name+'(', 'void f_'+addr+'_orig(',1)
        generated.append(body)
    completion=function((ROOT/'runtime/boot.c').read_text(),'save_sidecars_complete')
    with tempfile.TemporaryDirectory() as tmp:
        d=Path(tmp)
        exported=d/'runtime'; exported.mkdir()
        (exported/'extratrax_public.c').write_text((ROOT/'runtime/extratrax_public.c').read_text())
        public_only=wrapper_variants(exported)
        assert len(public_only)==1 and public_only[0][0]=='public'
        print('public-only export: 1/1')
        for variant,wrappers in wrapper_variants(ROOT/'runtime'):
            for mutation in ('good','original','load','save','default','completion','create','delete'):
                w=wrappers; hook=(ROOT/'runtime/underglow.c').read_text(); done=completion
                if mutation=='original': w=re.sub(r'underglow_\w+\([^;]*;', '(void)0;',w)
                if mutation in ('load','save','default'):
                    w=re.sub(r'underglow_profile_'+mutation+r'\([^;]*;', '(void)0;',w)
                if mutation=='completion': done=done.replace('underglow_save_complete(ok);','(void)ok;')
                if mutation=='create': hook=hook.replace('forget(M32(row));','(void)row;')
                if mutation=='delete': hook=hook.replace('forget(handle);','(void)handle;')
                hookfile=d/(variant+mutation+'-hook.c'); hookfile.write_text(hook)
                source=PREFIX+'\n'.join(generated)+w+done+MAIN
                path=d/(variant+mutation+'.c'); path.write_text(source); exe=path.with_suffix('')
                r=subprocess.run(['clang',*(['-DNFS_PUBLIC=1'] if variant=='public' else []),'-O1','-g','-fsanitize=address','-Wno-deprecated-declarations','-I',str(ROOT/'runtime'),'-I',str(ROOT/'recomp'),'-I',str(ROOT/'recomp/out'),str(path),str(hookfile),str(ROOT/'runtime/underglow_store.c'),'-o',str(exe)],capture_output=True,text=True)
                assert r.returncode==0,r.stderr[-4000:]
                data=d/(variant+mutation+'-data'); data.mkdir()
                r=subprocess.run([str(exe),str(data),'seed'],capture_output=True,text=True)
                print(variant,mutation,r.stdout.strip())
                assert r.returncode==int(mutation!='good'),r.stderr[-4000:]
                if mutation in ('good','original'):
                    proof_data=d/(variant+mutation+'-roundtrip'); proof_data.mkdir()
                    proof=subprocess.run([str(exe),str(proof_data),'roundtrip'],capture_output=True,text=True)
                    print(variant,mutation,proof.stdout.strip())
                    assert proof.returncode==int(mutation=='original'),proof.stderr[-2000:]
                if mutation=='good':
                    r=subprocess.run([str(exe),str(data),'restart'],capture_output=True,text=True)
                    print(variant,r.stdout.strip()); assert r.returncode==0,r.stderr[-2000:]
    return 0
if __name__=='__main__': raise SystemExit(main())
