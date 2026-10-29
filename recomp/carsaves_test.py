"""WO2/WO3: translated retail profile save/load, scratch-only admission contract.
New MW profile/create/autosave: good 53/53, zero private lookups; restoring
unconditional private save validation fails 12/53 and denies MW autosaves.
WO3 production serializer/file/completion/load: 23/23; omitted metadata staging
and omitted own-slot dependency mutants each fail 4/23. Swap-only saves are
marked while retail payload bytes [16:] remain identical.
Retail MW CRC mismatch: good 21/21; unconditional-override mutant fails 4/21.
Measured matching: good 91/91, skip-admission mutant 20/91 failures; owner-open mutant 2/91.
Missing/stale/Off: good 22/22 each; skip mutant 3/22, owner mutant 2/22.
Native WO3 supplement: roundtrip changed 0/63560; deliberate corruption 1/63560,
exit 2, for both MW and metadata journal reload. Both fixture checks and native
proof use scratch BOOT_USERDATA. No Unicorn.
The catalog is generated with synthetic approved packs, then its real WO1 reader
hashes it. Career/online data and collection/type lookup are contract fixtures;
005ACB70/0058FD10/005ACDD0/005ACE40 are real translated retail code.
Every filesystem open is interposed: an owner profile path aborts the test.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Missing-fixture proof: omitted guard exits 1 on import; this guard exits 3.
if any(not (Path(__file__).resolve().parents[1] / name).is_file() for name in ['tools_snapshot/test_private_cars.py']):
    print("SKIP: optional development fixtures are absent")
    sys.exit(3)
sys.path.insert(0, str(ROOT / 'recomp'))
import addcars_test as suite
spec = importlib.util.spec_from_file_location('pcfixtures', ROOT / 'tools_snapshot/test_private_cars.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

GUARD = r'''
#include <fcntl.h>
#include <dirent.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
static void forbid_owner(const char *p) {
    if (p && (strstr(p,"/Library/Application Support/NFSMW-port") || !strcmp(p,"NFSMW-port"))) _exit(90);
}
static int guarded_open(const char *p,int flags,...) {
    forbid_owner(p); mode_t mode=0;
    if(flags&O_CREAT) { va_list v; va_start(v,flags); mode=va_arg(v,int); va_end(v); }
    return open(p,flags,mode);
}
static int guarded_openat(int fd,const char *p,int flags,...) {
    forbid_owner(p); mode_t mode=0;
    if(flags&O_CREAT) { va_list v; va_start(v,flags); mode=va_arg(v,int); va_end(v); }
    return openat(fd,p,flags,mode);
}
static FILE *guarded_fopen(const char *p,const char *mode) { forbid_owner(p); return fopen(p,mode); }
static DIR *guarded_opendir(const char *p) { forbid_owner(p); return opendir(p); }
#define open guarded_open
#define openat guarded_openat
#define fopen guarded_fopen
#define opendir guarded_opendir
'''

PROOF = r'''
#include <CommonCrypto/CommonDigest.h>
static unsigned dialogs, retail_hashes;
#define SAVE_SIZE 0xF848u
static void digest(uint32_t buffer) {
    CC_MD5(G+buffer+16,SAVE_SIZE-32,G+buffer+SAVE_SIZE-16);
}
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
static int appended;
static int songs_ready(void) { return 0; }
static uint32_t profile(void) { return 0; }
static void list_from_career(uint32_t self, int load) { abort(); }
static int pending_trax_valid;
#define MW_SONGS 26
static char default_name[40];
static int list_on(void) { return 0; }
static void list_to_career(uint32_t self) { abort(); }
static void profile_name(uint32_t self,char *name,size_t size) { snprintf(name,size,"TEST"); }
static void sidecar_stage(uint32_t self) { abort(); }
static void xlog(const char *format,...) { }
#define FEDB 0x0091CF90u
ADMISSION_WRAPPER
static void invoke(Cpu *c,uint32_t self,uint32_t buffer,unsigned slot,int save) {
    uint32_t sp=c->esp;
    if(save<=0) { c->esp-=4; M32(c->esp)=slot; c->esp-=4; M32(c->esp)=0; }
    c->esp-=4; M32(c->esp)=SAVE_SIZE;
    c->esp-=4; M32(c->esp)=buffer;
    c->esp-=4; M32(c->esp)=0; c->ecx=self;
    if(save>0) f_0058FD10(c); else if(save<0) f_005ACB70_orig(c); else f_005ACB70(c);
    CHECK(c->esp==sp);
}
static void invoke_frontend(Cpu *c,uint32_t frontend,uint32_t buffer,unsigned slot) {
    uint32_t sp=c->esp;
    c->esp-=4; M32(c->esp)=slot; c->esp-=4; M32(c->esp)=SAVE_SIZE;
    c->esp-=4; M32(c->esp)=buffer; c->esp-=4; M32(c->esp)=0; c->ecx=frontend;
    f_005ACDD0(c); CHECK(c->esp==sp);
}
static void invoke_retail_save(Cpu *c,uint32_t self,uint32_t buffer,unsigned size,int original) {
    uint32_t sp=c->esp;
    c->esp-=4; M32(c->esp)=size; c->esp-=4; M32(c->esp)=buffer;
    c->esp-=4; M32(c->esp)=0; c->ecx=self;
    if(original) f_0058FD10_orig(c); else f_0058FD10(c);
    CHECK(c->esp==sp);
}
static void originals(uint32_t db) {
    memset(G+db,0xFF,0x8CC8);
    for(unsigned i=0;i<200;++i) M32(db+i*20)=~0u;
    M32(db)=0; M32(db+4)=0x10000; M32(db+8)=0x20000;
    M32(db+12)=0x10001; M16(db+16)=0xFFFF;
}
static void register_addition(void) {
    Addition *a=&additions[0]; strcpy(a->name,"240SX"); strcpy(a->donor_folder,"RX7");
    a->key=0x88888888; a->pv=0x111; a->fe=0x222; a->menu_fe=a->fe;
    if(cars[0].geometry[0]) { strcpy(a->directory,cars[0].geometry); *strrchr(a->directory,'/')=0; }
    for(unsigned j=0;j<2;++j) {
        uint32_t v=setup_vault(),ld=gmalloc(0x20),lay=gmalloc(0x60),col=gmalloc(0x28),cl=gmalloc(4);
        life(v)->ready=1; M32(ld)=j?a->fe:a->pv; M32(ld+4)=j?FRONTEND:PVEHICLE;
        M32(ld+0x1C)=lay; M32(lay+0x38)=0; M8(lay+0x58)=1;
        M32(cl)=j?FRONTEND:PVEHICLE; M32(col+0x14)=cl;
        M32(M32(v+0x2C))=col; M32(M32(v+0x30))=a->key; M32(v+0x34)=1;
        sources[nsources++]=(Source){ld,v,ld,life(v)->generation};
    }
}
int carsave_roundtrip_file(const char *root,const void *input,void *output);
int main(int argc,char **argv) {
    assert(argc==2); G=calloc(1,0x4000000); assert(G);
    // Deliberately probe only fake forbidden paths: the interposer must abort
    // before reaching libc, even for descriptor-relative traversal.
    for(unsigned probe=0;probe<2;++probe) {
        pid_t pid=fork(); assert(pid>=0);
        if(!pid) {
            if(probe) openat(AT_FDCWD,"NFSMW-port",O_RDONLY);
            else opendir("/scratch/Library/Application Support/NFSMW-port");
            _exit(99);
        }
        int status=0; assert(waitpid(pid,&status,0)==pid);
        CHECK(WIFEXITED(status) && WEXITSTATUS(status)==90);
    }
    configured=1; enabled=0; nadditions=0;
    Cpu c={0}; c.esp=0x800000;
    uint32_t fe=gmalloc(0x300),self=gmalloc(0xA000),second=gmalloc(0xA000),buf=gmalloc(SAVE_SIZE+64),out=gmalloc(SAVE_SIZE+64);
    M32(FEDB)=fe; M32(fe+8)=0x10203040; M32(fe+16)=self; M32(fe+20)=second;
    uint32_t db=self+0x414; originals(db);
    if(getenv("SHIP_NEW_PROFILE_ONLY")) {
        unsigned start=checks,failed=failures,total_private_lookups=0;
        unsigned char expected[SAVE_SIZE+64],profile_before[0xA000];
        strcpy((char *)G+self,"TEST");
        for(unsigned i=0x4C;i<0xA8;++i) M8(self+i)=(unsigned char)(20+i%80);
        M32(self+0x70)=2; // nonzero audio/settings bytes must survive both saves
        // Same new career lifecycle as mixsmoke: first save succeeds, then the
        // first in-game autosave must still publish even without frontend/type
        // lookup data. Retail serialization never asks for those collections.
        for(unsigned phase=0;phase<3;++phase) {
            missing_frontend=phase==1; retail_type_sentinel=phase==2;
            M32(db+12)=phase?0x10002:0x10001;
            M32(db+4)=phase==1?0:0x10000;
            memcpy(profile_before,G+self,sizeof profile_before);
            for(unsigned extra=0;extra<=64;extra+=64) {
                // Independent sessions: one bad case must not hide the next
                // regression behind the deliberately sticky refusal latch.
                profile_refused=0; private_lookups=0;
                unsigned size=SAVE_SIZE+extra; Cpu input=c;
                invoke_retail_save(&c,self,buf,size,1);
                memcpy(expected,G+buf,size); Cpu retail=c;
                c=input; invoke_retail_save(&c,self,buf,size,0);
                CHECK(!memcmp(&retail,&c,sizeof c));
                CHECK(!memcmp(expected,G+buf,size));
                CHECK(!memcmp(profile_before,G+self,sizeof profile_before));
                CHECK(private_cars_save_allowed()); CHECK(private_lookups==0);
                if(!extra) {
                    CHECK(!carsave_roundtrip_file(getenv("BOOT_USERDATA"),G+buf,G+out));
                    CHECK(!memcmp(expected,G+out,size));
                }
                total_private_lookups+=private_lookups;
            }
        }
        char path[4096]; snprintf(path,sizeof path,"%s/.car-catalog",getenv("BOOT_USERDATA"));
        CHECK(access(path,F_OK)==-1); CHECK(dialogs==0);
        // The MW bypass must retain an already latched private-profile refusal.
        profile_refused=1; invoke_retail_save(&c,self,buf,SAVE_SIZE,0);
        CHECK(!private_cars_save_allowed());
        CHECK(carsave_roundtrip_file(getenv("BOOT_USERDATA"),G+buf,G+out)!=0);
        printf("new-profile checks=%u failures=%u private_lookups=%u\n",checks-start,failures-failed,total_private_lookups);
        return failures?1:0;
    }
    invoke(&c,self,buf,0,1); unsigned char bytes[SAVE_SIZE]; memcpy(bytes,G+buf,SAVE_SIZE);
    unsigned char before[0xA000]; memcpy(before,G+self,sizeof before);
    invoke(&c,self,buf,0,0); CHECK(c.eax&255); invoke(&c,self,out,0,1);
    CHECK(!memcmp(bytes,G+out,SAVE_SIZE)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    if(getenv("SHIP_RETAIL_CRC_ONLY")) {
        unsigned start=checks, failed=failures, hashes=retail_hashes;
        // Retail: career loads first; CRC mismatch returns AL=false before FE
        // copy/MD5. Compare the exact translated original, including that write.
        M32(fe+8)=0x50607080; M32(db+12)=0xDEADBEEF; M32(self+0xA8)=0x12345678;
        memcpy(before,G+self,sizeof before); Cpu input=c;
        invoke(&c,self,buf,0,-1); CHECK(!(c.eax&255)); CHECK(retail_hashes==hashes);
        CHECK(M32(self+0xA8)==0xC0DEC0DE); CHECK(M32(db+12)==0xDEADBEEF);
        unsigned char retail[0xA000]; memcpy(retail,G+self,sizeof retail); Cpu retail_cpu=c;
        memcpy(G+self,before,sizeof before); c=input;
        invoke(&c,self,buf,0,0); CHECK(!(c.eax&255)); CHECK(retail_hashes==hashes);
        CHECK(M32(fe+8)==0x50607080); CHECK(!memcmp(retail,G+self,sizeof retail));
        CHECK(!memcmp(&retail_cpu,&c,sizeof c)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
        CHECK(private_cars_save_allowed()); CHECK(dialogs==0);
        // A verified nonempty catalog still enables intentional accommodation.
        CHECK(!private_cars_init(argv[1],1,NULL,0));
        CHECK(private_cars_generation()[0]!=0); hashes=retail_hashes;
        invoke(&c,self,buf,0,0); CHECK(c.eax&255); CHECK(retail_hashes==hashes+1);
        CHECK(M32(fe+8)==0x50607080); CHECK(M32(db+12)==0x10001);
        printf("retail-crc checks=%u failures=%u\n",checks-start,failures-failed);
        return failures?1:0;
    }
    int unavailable=getenv("SHIP_EXPECT_REFUSAL")!=NULL;
    int cat=private_cars_init(argv[1],!getenv("SHIP_MASTER_OFF"),NULL,0);
    CHECK(unavailable ? private_cars_count()==0 : cat==0 && private_cars_count()==3);
    enabled=1; nadditions=1; register_addition();
    if(getenv("SHIP_FILE_ROUNDTRIP")) {
        unsigned start=checks,failed=failures;
        uint32_t table=gmalloc(84*STRIDE); M32(TABLE)=table;
        for(unsigned j=0;j<84;++j) M32(table+j*STRIDE+0x90)=j;
        strcpy((char *)G+table,"FORDGT"); // fixture's original type 0 is a selected own-slot swap
        unsigned char swapped[PRIVATE_CARS_MASK_SIZE]={0}; swapped[10]=2;
        invoke(&c,self,out,0,1); CHECK(!memcmp(G+out,"NFSCAR3",7));
        CHECK(!private_cars_metadata_admit(G+out,SAVE_SIZE,swapped));
        CHECK(!memcmp(bytes+16,G+out+16,SAVE_SIZE-16)); // only reserved bytes change
        // Full production serializer -> durable file layer -> completion seam
        // -> loader, including exact generation metadata and FE reconciliation.
        invoke(&c,self,buf,0,0); CHECK(c.eax&255);
        invoke(&c,self,buf,0,1); CHECK(private_cars_save_allowed());
        CHECK(!memcmp(G+buf,"NFSCAR3",7));
        swapped[0]=1; CHECK(!private_cars_metadata_admit(G+buf,SAVE_SIZE,swapped));
        CHECK(!carsave_roundtrip_file(getenv("BOOT_USERDATA"),G+buf,G+out));
        CHECK(!memcmp(G+buf,G+out,SAVE_SIZE));
        memcpy(bytes,G+out,SAVE_SIZE); memcpy(before,G+self,sizeof before);
        invoke(&c,self,out,0,0); CHECK(c.eax&255);
        CHECK(!memcmp(before+0x414,G+self+0x414,0x8CC8));
        CHECK(!memcmp(bytes,G+out,SAVE_SIZE));
        invoke(&c,self,buf,0,1); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
        // Own-slot identity remains MW; removing only its catalog selection
        // must nevertheless refuse without changing the profile or save.
        cars[81].selected=0; memcpy(before,G+self,sizeof before);
        invoke(&c,self,buf,0,0); CHECK(!(c.eax&255)); CHECK(!private_cars_save_allowed());
        CHECK(!memcmp(before,G+self,sizeof before)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
        printf("file-roundtrip checks=%u failures=%u bytes=%u\n",checks-start,failures-failed,SAVE_SIZE);
        return failures?1:0;
    }
    if(unavailable) {
        // Manufacture only a scratch serialized dependency, then use real
        // missing/corrupt/Off startup reader results for all loader paths.
        uint32_t r=db+20; M32(r)=1; M32(r+4)=M32(r+8)=additions[0].key;
        M32(r+12)=0x10002; M16(r+16)=0xFFFF;
        invoke(&c,self,buf,0,1); memcpy(bytes,G+buf,SAVE_SIZE);
        for(unsigned slot=0;slot<2;++slot) {
            uint32_t target=slot?second:self; memcpy(before,G+target,sizeof before);
            invoke_frontend(&c,fe,buf,slot); CHECK(!(c.eax&255));
            CHECK(!memcmp(before,G+target,sizeof before)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
        }
        memcpy(before,G+self,sizeof before); M32(fe+0x128)=buf; c.ecx=fe;
        uint32_t sp=c.esp; c.esp-=4; M32(c.esp)=0; f_005ACE40(&c);
        CHECK(c.esp==sp); CHECK(!memcmp(before,G+self,sizeof before));
        CHECK(!memcmp(bytes,G+buf,SAVE_SIZE)); CHECK(!private_cars_save_allowed());
        printf("unavailable checks=%u failures=%u dialogs=%u\n",checks,failures,dialogs);
        return failures?1:0;
    }
    // Old MW save inserts one missing stock row; owned and linked pools preserved.
    invoke(&c,self,buf,0,0); CHECK(c.eax&255);
    CHECK(M32(db+20+8)==additions[0].key); CHECK(M32(db+20+12)==0x10001);
    unsigned char staged[0x8CC8]; memcpy(staged,G+db,sizeof staged);
    invoke(&c,self,buf,0,0); CHECK(c.eax&255); CHECK(!memcmp(staged,G+db,sizeof staged));
    // Owned/customized copy, distinct from stock; -1 parts remain empty.
    uint32_t row=db+190*20; M32(row)=190; M32(row+4)=additions[0].key; M32(row+8)=additions[0].key;
    M32(row+12)=0x10002; M16(row+16)=0x0100;
    M16(db+4000)=42; // installed part in the owned car's customization record
    invoke(&c,self,buf,0,1); memcpy(bytes,G+buf,SAVE_SIZE); memcpy(staged,G+db,sizeof staged);
    for(unsigned slot=0;slot<2;++slot) {
        uint32_t target=slot?second:self;
        invoke_frontend(&c,fe,buf,slot); CHECK(c.eax&255);
        CHECK(!memcmp(staged,G+target+0x414,sizeof staged)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    }
    // RestoreFromBackupDB calls exactly the shared gate; invoke its real body.
    M32(fe+0x128)=buf; c.ecx=fe; uint32_t sp=c.esp; c.esp-=4; M32(c.esp)=0;
    f_005ACE40(&c); CHECK(c.esp==sp); CHECK(!M32(fe+0x128)); CHECK(!memcmp(staged,G+db,sizeof staged));
    // Missing/disabled/stale ID catalog: refuse before any retail writes, for
    // both primary and secondary, preserving save and complete target memory.
    unsigned hashes=retail_hashes;
    cars[0].selected=0;
    for(unsigned slot=0;slot<2;++slot) {
        uint32_t target=slot?second:self; memcpy(before,G+target,sizeof before);
        invoke_frontend(&c,fe,buf,slot); CHECK(!(c.eax&255)); CHECK(!private_cars_save_allowed());
        CHECK(!memcmp(before,G+target,sizeof before)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    }
    CHECK(retail_hashes==hashes); CHECK(dialogs==2);
    memcpy(before,G+self,sizeof before); M32(fe+0x128)=buf; c.ecx=fe; sp=c.esp; c.esp-=4; M32(c.esp)=0;
    f_005ACE40(&c); CHECK(c.esp==sp); CHECK(!M32(fe+0x128));
    CHECK(!memcmp(before,G+self,sizeof before)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    CHECK(retail_hashes==hashes); CHECK(dialogs==3);
    cars[0].selected=1; cars[0].id=85;
    invoke(&c,self,buf,0,0); CHECK(!(c.eax&255)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    cars[0].id=84; invoke(&c,self,buf,0,0); CHECK(c.eax&255);
    CHECK(!memcmp(staged,G+db,sizeof staged)); CHECK(!private_cars_save_allowed());
    for(unsigned failure=0;failure<2;++failure) {
        missing_class=failure==0; missing_parts=failure==1;
        memcpy(before,G+self,sizeof before); invoke(&c,self,buf,0,0);
        CHECK(!(c.eax&255)); CHECK(!memcmp(before,G+self,sizeof before));
        CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    }
    missing_class=missing_parts=0;
    char directory[PATH_MAX]; strcpy(directory,additions[0].directory); strcpy(additions[0].directory,"/unverified-test-pack");
    memcpy(before,G+self,sizeof before); invoke(&c,self,buf,0,0);
    CHECK(!(c.eax&255)); CHECK(!memcmp(before,G+self,sizeof before)); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    strcpy(additions[0].directory,directory);
    // Corrupt digest refuses before retail and cannot mutate an active profile.
    memcpy(before,G+self,sizeof before); M8(buf+0x5BA1+12)^=1;
    invoke(&c,self,buf,0,0); CHECK(!(c.eax&255)); CHECK(!memcmp(before,G+self,sizeof before));
    M8(buf+0x5BA1+12)^=1;
    for(unsigned field=16;field<=17;++field) {
        uint32_t link=buf+0x5BA1+190*20+field; unsigned char old=M8(link);
        M8(link)=field==16?75:25; digest(buf); memcpy(before,G+self,sizeof before);
        invoke(&c,self,buf,0,0); CHECK(!(c.eax&255)); CHECK(!memcmp(before,G+self,sizeof before));
        M8(link)=old; digest(buf); CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    }
    memcpy(before,G+self,sizeof before); invoke(&c,self,buf,2,0);
    CHECK(!(c.eax&255)); CHECK(!memcmp(before,G+self,sizeof before));
    // Capacity and handle collisions are atomic, including all linked records.
    uint32_t copy=gmalloc(0x8CC8); originals(copy);
    for(unsigned i=0;i<200;++i) { M32(copy+i*20)=i; M32(copy+i*20+4)=0x10000; M32(copy+i*20+8)=0x20000; }
    unsigned n=9; unsigned char full[0x8CC8]; memcpy(full,G+copy,sizeof full);
    CHECK(reconcile_stock(copy,&n)!=NULL); CHECK(n==0); CHECK(!memcmp(full,G+copy,sizeof full));
    M32(copy+199*20)=~0u; CHECK(!reconcile_stock(copy,&n)); CHECK(n==1);
    memcpy(full,G+copy,sizeof full); CHECK(!reconcile_stock(copy,&n)); CHECK(n==0); CHECK(!memcmp(full,G+copy,sizeof full));
    originals(copy); M32(copy)=1; memcpy(full,G+copy,sizeof full);
    CHECK(reconcile_stock(copy,&n)!=NULL); CHECK(n==0); CHECK(!memcmp(full,G+copy,sizeof full));
    CHECK(!memcmp(bytes,G+buf,SAVE_SIZE));
    printf("WO2 checks=%u failures=%u retail_hashes=%u dialogs=%u save_bytes=%u\n",checks,failures,retail_hashes,dialogs,SAVE_SIZE);
    return failures?1:0;
}
'''


def main():
    sys.path.insert(0,str(ROOT/'runtime'))
    import auditfix_test as audit
    private = (ROOT / 'runtime/extratrax.c').read_text()
    wrapper = private[private.index('// Profile admission precedes'):private.index('// UserProfile::SaveToBuffer')]
    wrapper += private[private.index('// UserProfile::SaveToBuffer'):private.index('// ---------------- shuffle')]
    prefix = suite.HARNESS.split('int main(int argc, char **argv)', 1)[0]
    prefix = prefix.replace('#include "car85.c"', '#include "car85.c"\n#define load catalog_load\n#include "private_cars.c"\n#undef load')
    prefix = prefix.replace('void f_005816B0_orig(Cpu *c) { c->eax=84; c->esp+=4; }',
                            'void f_005816B0_orig(Cpu *c) { c->eax=retail_type_sentinel||M32(c->ecx+8)==0x88888888?84:0; c->esp+=4; }')
    prefix = prefix.replace('case 0x007398D0:', '''case 0x00455FD0: ++private_lookups; c->eax=!missing_class && !(missing_frontend && args[0]==FRONTEND); break;
    case 0x00747C00: c->eax=!missing_parts; break;
    case 0x00598590: ++dialogs; c->eax=1; break;
    case 0x007398D0:''', 1)
    prefix = prefix.replace('uint8_t *G;', 'static unsigned dialogs, missing_parts, missing_class, missing_frontend, retail_type_sentinel, private_lookups;\nuint8_t *G;')
    proof = PROOF.replace('static unsigned dialogs, retail_hashes;', 'static unsigned retail_hashes;').replace('ADMISSION_WRAPPER', wrapper)
    suite.HARNESS = prefix + proof
    # Delegate translator/fixture generation, but execute our admission main.
    # The driver supplies root as argv[1], replacing its numeric variant input.
    with tempfile.TemporaryDirectory(prefix='shipcars-admission-') as directory:
        root = Path(directory).resolve()
        src = {k: root / k / 'CARS' for k in ('C', 'P')}
        mw = root / 'MW/CARS'
        for base, names in ((src['C'], ('240SX','300C')), (src['P'], ('FORDGT',)), (mw, ('RX7','CTS','FORDGT'))):
            for name in names:
                (base/name).mkdir(parents=True)
                for filename in fixtures.pc.PACKS: (base/name/filename).write_bytes((name+filename).encode())
        catalog_root = root / 'catalog'
        data = fixtures.pc.build(catalog_root,mw,src,adapter=fixtures.adapter)
        approvals = {r['source']: dict(evidence='synthetic admission contract', **{f:r['outputs'][f]['sha256'] for f in fixtures.pc.PACKS}) for r in data['cars'] if r['status']=='converted'}
        approval = root/'approvals.json'; approval.write_bytes(fixtures.pc.encoded(approvals))
        fixtures.pc.build(catalog_root,mw,src,approvals=approval,adapter=fixtures.adapter)
        if not Path(suite.base.recomp.EXE).is_file():
            print('SKIP: private speed.exe absent'); return 3
        rc = suite.base.recomp.Recompiler(pe=suite.base.recomp.PE()); rc.calls = True
        import re
        starts=sorted(int(line.split('\t')[0],16) for line in (ROOT/'recomp/functions_v13.tsv').read_text().splitlines() if re.match(r'^[0-9A-Fa-f]{8}(?:\t|$)',line))
        rc.functions=dict(zip(starts,starts[1:]))
        generated=[]
        addresses=(0x7398D0,0x739940,0x7398A0,0x7399F0,0x739870,0x460BF0,0x681340,0x56F090,0x739920,0x56BB80,0x503400,0x502E80,0x4FB210,0x5ACB70,0x58FD10,0x5ACDD0,0x5ACE40)
        for address in addresses:
            code,error=rc.translate(address,rc.functions.get(address,address+2048)); assert not error,error
            if address in (0x7399F0,0x739870,0x681340,0x56F090,0x739920,0x56BB80,0x5ACB70,0x58FD10):
                code=code.replace(f'void f_{address:08X}(',f'void f_{address:08X}_orig(',1)
            generated.append(code)
        source=(GUARD+prefix+proof).replace('GENERATED','\n'.join(generated))
        file_layer = GUARD + '#include <assert.h>\n' + audit.FILE_PREFIX
        file_layer += audit.marked('runtime/boot.c','// BEGIN offline save scan:','// END offline save scan')
        file_layer += audit.marked('runtime/boot.c','// BEGIN offline file layer:','// END offline file layer')
        file_layer += r'''
void extratrax_save_complete(int ok) { }
void padbind_save_complete(int ok) { }
int carsave_roundtrip_file(const char *root,const void *input,void *output) {
    userroot=root; char path[4096]; snprintf(path,sizeof path,"%s/roundtrip",root);
    unsigned seq=save_fail_seq,commits=save_commit_seq;
    uint32_t h=file_open(path,path,0x40000000u,0,2,0);
    if(h==UINT32_MAX) return 1;
    unsigned char header[36]={0};
    int ok=file_write(h,header,36)==36 && file_write(h,input,0xF848)==0xF848;
    if(!file_close(h)) ok=0;
    M32(0x500)=1; M32(0x4f4)=0;
    save_task_fixup(0x200,0xb,seq); save_sidecars_complete(0x200,0xb,seq,commits);
    if(!ok) return 1;
    int fd=open(path,O_RDONLY); if(fd<0) return 1;
    ssize_t got=pread(fd,output,0xF848,36); close(fd);
    return got!=0xF848;
}
'''
        (root/'files.c').write_text(file_layer)
        (root/'diag').mkdir(); (root/'diag.user').mkdir()
        env=dict(os.environ,BOOT_DIAG=str(root/'diag'),BOOT_USERDATA=str(root/'diag.user'))
        for key in ('NFS_TEST_ADDCARS','NFS_TEST_CAR85','NFS_TEST_ADDCARS_SEED'): env.pop(key,None)
        import shutil
        stale=root/'stale'; shutil.copytree(catalog_root,stale)
        import json
        pointer=json.loads((stale/'current.json').read_bytes())
        pack=stale/pointer['generation']/'additions/240SX/GEOMETRY.BIN'
        pack.write_bytes(pack.read_bytes()+b'stale')
        for variant in ("good","skip_admission","owner_guard","unconditional_crc","skip_metadata_stage","skip_swap_dependency","unguarded_mw_save"):
            mutant=variant!="good"
            actual=source
            if variant=="skip_admission":
                actual=actual.replace('    if (reason) { profile_refuse(c, reason); return; }','    /* deliberately skip refusal */')
                assert actual!=source
            if variant=="owner_guard":
                actual=actual.replace('_exit(90);','(void)p; /* deliberately bypass owner-open interposer */')
                assert actual!=source
            if variant=="unconditional_crc":
                actual=actual.replace("int private_crc = private_cars_generation()[0] != '\\0';", "int private_crc = 1; /* deliberately unconditional */")
                assert actual!=source
            if variant=="skip_metadata_stage":
                actual=actual.replace('if (!reason) reason = private_cars_metadata_stage(G + buffer, size, used);','/* deliberately omit metadata at the real serializer */')
                assert actual!=source
            if variant=='unguarded_mw_save':
                actual=actual.replace('if (private_cars_count() || car85_count() > 84) {','if (1) { /* restore the broken unconditional MW validation */')
                assert actual!=source
            car_source=(ROOT/'runtime/car85.c').read_text()
            if variant=='skip_swap_dependency':
                broken=car_source.replace('car->swap && car->selected && !strncmp(name,car->name,16)','0 /* deliberately omit own-slot dependency */')
                assert broken!=car_source
                car_source=broken
            (root/'car85.c').write_text(car_source)
            (root/'test.c').write_text(actual)
            exe=root/variant
            build=subprocess.run(['clang','-O1','-g','-fsanitize=address',
                '-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),
                str(root/'test.c'),str(root/'files.c'),'-framework','CoreGraphics','-framework','CoreText','-framework','CoreFoundation','-o',str(exe)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr[-3000:]
            if variant in ('good','unguarded_mw_save'):
                user=root/('new-profile-'+variant); user.mkdir()
                case=subprocess.run([str(exe),str(catalog_root)],env=dict(env,BOOT_USERDATA=str(user),SHIP_NEW_PROFILE_ONLY='1'),capture_output=True,text=True)
                print(variant,case.stdout.strip())
                if variant=='unguarded_mw_save':
                    print('old-path refusals:', '; '.join(line for line in case.stderr.splitlines() if 'carsaves: save refused:' in line))
                assert case.returncode==int(mutant),case.stderr[-2200:]
                if variant=='unguarded_mw_save': continue
            if variant in ('good','skip_metadata_stage','skip_swap_dependency'):
                case=subprocess.run([str(exe),str(catalog_root)],env=dict(env,SHIP_FILE_ROUNDTRIP='1'),capture_output=True,text=True)
                print(variant,case.stdout.strip())
                assert case.returncode==int(mutant),case.stderr[-2200:]
                if variant!='good': continue
            if variant in ("good","unconditional_crc"):
                case_env=dict(env,SHIP_RETAIL_CRC_ONLY='1')
                case=subprocess.run([str(exe),str(catalog_root)],env=case_env,capture_output=True,text=True)
                print(variant,case.stdout.strip())
                assert case.returncode==int(mutant),(variant,case.stderr[-1800:])
                if variant=="unconditional_crc": continue
            result=subprocess.run([str(exe),str(catalog_root)],env=env,capture_output=True,text=True)
            print(variant,result.stdout.strip())
            if result.returncode != int(mutant): print(result.stderr[-2500:])
            assert result.returncode==int(mutant),(mutant,result.returncode)
            for scenario,path in (('missing',root/'absent'),('stale',stale),('Off',catalog_root)):
                case_env=dict(env,SHIP_EXPECT_REFUSAL='1')
                if scenario=='Off': case_env['SHIP_MASTER_OFF']='1'
                case=subprocess.run([str(exe),str(path)],env=case_env,capture_output=True,text=True)
                print(('mutant ' if mutant else '')+scenario,case.stdout.strip())
                assert case.returncode==int(mutant),(scenario,mutant,case.stderr[-1800:])
    return 0

if __name__ == '__main__': raise SystemExit(main())
