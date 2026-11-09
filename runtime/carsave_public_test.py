"""Public admission/provenance, native translated loader/serializer, scratch only.

Measured: 43 good runs, 685 parent assertions plus 21 retry-child assertions; original control fails 13/22 with two
forbidden retail calls/profile writes. Eight baseline mutants fail respectively:
journal 1/8, dependency comparison 2/13, signature 13/22, exact generation 4/11,
backup 4/48, barrier 3/22, custom link 13/23, MW CRC override 1/14.
Total including controls: 55 invocations, 889 parent and 84 retry-child assertions,
ASan/UBSan clean. Journal reuse regression: fixed child 21/21, predecessor fails
8/21; file-only and directory-only omissions each fail 5/21. Retry covers both
pending and completed records after a failed post-rename directory sync.

Stored original control: the public PASS(005ACB70)/PASS(0058FD10) wrappers
forward directly to retail. The removed-pack case must fail refusal assertions
under this original control, with forbidden retail calls and profile writes.
Run prints measured good/control totals; private 53-transaction/8-restart checks
remain separate, unchanged coverage in carsave_metadata_test.py.
Guest class/type/part/audio helpers are contract doubles; four profile functions
are production translated C. Native catalog approval and all filesystem/journal
code are real. No game, real profile, Unicorn or music implementation is linked.
Export closure: the omitted private file fixture made the shipped proof skip
(exit 3). The public fixture passes 55 invocations, 889 parent/84 retry-child
assertions, retaining all original refusal and durability controls.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if any(not (ROOT / name).is_file() for name in ('runtime/save_file_fixture.py', 'runtime/car_catalog_test.py')):
    print('SKIP: optional native test fixtures are absent')
    raise SystemExit(3)
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_catalog as cc
sys.path.insert(0, str(ROOT / 'runtime'))
import save_file_fixture as audit

GUARD = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
extern int fault_kind;
extern unsigned mutations;
extern unsigned file_syncs,dir_syncs;
static int g_openat(int dir,const char *p,int flags,...) {
    mode_t mode=0;
    if(flags&O_CREAT) { va_list a; va_start(a,flags); mode=va_arg(a,int); va_end(a); }
    if(flags&(O_CREAT|O_TRUNC|O_WRONLY|O_RDWR)) {
        ++mutations;
        if(fault_kind==1) { errno=ENOSPC; return -1; }
    }
    return openat(dir,p,flags,mode);
}
static ssize_t g_write(int fd,const void *p,size_t n) {
    ++mutations;
    if(fault_kind==2) { errno=ENOSPC; return -1; }
    if(fault_kind==8) { fault_kind=0; errno=EINTR; return -1; }
    if(fault_kind==9) { fault_kind=0; return write(fd,p,n/2); }
    return write(fd,p,n);
}
static int g_renameat(int a,const char *b,int c,const char *d) {
    ++mutations;
    if(fault_kind==3) { errno=EIO; return -1; }
    return renameat(a,b,c,d);
}
static int g_renameatx(int a,const char *b,int c,const char *d,unsigned f) {
    ++mutations;
    if(fault_kind==3) { errno=EIO; return -1; }
    return renameatx_np(a,b,c,d,f);
}
static int sync_fault(int fd) {
    struct stat st; if(fstat(fd,&st)) abort();
    if(S_ISDIR(st.st_mode)) ++dir_syncs; else ++file_syncs;
    return fault_kind==4 || (fault_kind==10 && S_ISDIR(st.st_mode)) ||
           (fault_kind==11 && S_ISREG(st.st_mode));
}
static int g_sync(int fd) { if(sync_fault(fd)) { errno=EIO; return -1; } return fsync(fd); }
static int g_fcntl(int fd,int cmd,...) {
    va_list a; va_start(a,cmd);
    int value=cmd==F_FULLFSYNC?0:va_arg(a,int); va_end(a);
    if(cmd==F_FULLFSYNC && sync_fault(fd)) { errno=EIO; return -1; }
    return fcntl(fd,cmd,value);
}
static int g_mkdirat(int fd,const char *p,mode_t mode) { ++mutations; return mkdirat(fd,p,mode); }
#define openat g_openat
#define write g_write
#define renameat g_renameat
#define renameatx_np g_renameatx
#define fsync g_sync
#define fcntl g_fcntl
#define mkdirat g_mkdirat
#include "MODULE"
'''

NATIVE = r'''
#include "funcs.h"
#include "host.h"
#include "carsave_public.h"
#include <CommonCrypto/CommonDigest.h>
#include <assert.h>
#include <stdlib.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
uint8_t *G;
int fault_kind;
unsigned mutations,file_syncs,dir_syncs;
static unsigned checks,failures,dialogs,retail_calls,retail_writes,hashes,lookups;
static int missing_class,missing_part,missing_fe,broken_serializer;
static uint32_t arena=0x2000000;
static const uint32_t self=0x100000,buffer=0x200000,frontend=0x300000;
#define SIZE CARSAVE_PUBLIC_SIZE
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL %u: %s\n",__LINE__,#x); } } while(0)
uint32_t gmalloc(uint32_t n) { uint32_t p=arena; arena+=(n+63)&~63u; assert(arena<0x3f00000); return p; }
void gfree(uint32_t p) { (void)p; }
uint32_t gstr(const char *s) { uint32_t p=gmalloc((uint32_t)strlen(s)+1); strcpy((char *)G+p,s); return p; }
static uint32_t key(const char *s) { uint32_t h=2166136261u; for(;*s;++s) h=(h^(unsigned char)*s)*16777619u; return h; }
void f_005816B0_orig(Cpu *c) {
    uint32_t h=M32(c->ecx+8); c->eax=0;
    for(unsigned k=0;k<90;++k) {
        const CarCatalogSlot *s=car_catalog_inventory_get(k);
        if(s && s->ready && h==key(s->pvehicle)) c->eax=s->id;
    }
    /* Stable unresolved addition type even when its pack is removed. */
    if(h==key("MODEL_0_PV")) c->eax=84;
    if(h==key("MODEL_75_PV")) c->eax=159;
    c->esp+=4;
}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    (void)n;
    switch(fn) {
    case 0x00455FD0: ++lookups; return !missing_class && !(missing_fe && args[0]==0x85885722u);
    case 0x004549B0: return key((const char *)G+args[0]);
    case 0x00747C00: return !missing_part;
    case 0x00598590: ++dialogs; return 1;
    default: fprintf(stderr,"unexpected guest %08X\n",fn); abort();
    }
    (void)c;
}
void f_0057F7B0(Cpu *c) { c->eax=M32(c->esp+4)+0x59F9; c->esp+=12; }
void f_005AC950(Cpu *c) { ++retail_calls; ++retail_writes; M32(c->ecx)=0xC0DEC0DE; c->eax=M32(c->esp+4)+0x59F9; c->esp+=12; }
void f_006530A0(Cpu *c) { c->eax=0; c->esp+=8; }
void f_00576C50(Cpu *c) { abort(); (void)c; }
void f_0057FFB0(Cpu *c) { c->eax=M32(c->esp+4)+0x36; c->esp+=8; }
void f_00580080(Cpu *c) { c->eax=M32(c->esp+4)+0x36; c->esp+=8; }
void f_0056E180(Cpu *c) { c->eax=M32(c->esp+4)+4+19*48; c->esp+=4; }
void f_0056E200(Cpu *c) { c->eax=M32(c->esp+4)+4+19*48; c->esp+=4; }
void f_0057F920(Cpu *c) {
    uint32_t p=M32(c->esp+4),out=M32(c->esp+8),n=M32(c->esp+12);
    CC_MD5(G+p,n,G+out); if(broken_serializer) M8(out)^=1; c->esp+=20;
}
void f_0057F9F0(Cpu *c) {
    unsigned char d[16]; ++hashes; CC_MD5(G+M32(c->esp+4),M32(c->esp+12),d);
    c->eax=0xA5000000u|!memcmp(d,G+M32(c->esp+8),16); c->esp+=16;
}
void f_004C3790(Cpu *c) { c->eax=M32(c->esp+4); c->esp+=8; }
void f_004AECB0(Cpu *c) { c->esp+=12; }
void f_005CC240(Cpu *c) { c->eax=0; c->esp+=4; }
void f_00405DA0(Cpu *c) { c->eax=c->ecx; c->esp+=20; }
void f_0065FAF0(Cpu *c) { c->esp+=4; }
void f_00628CC0(Cpu *c) { c->eax=48; c->esp+=4; }
void f_0067AC60(Cpu *c) { c->eax=0; c->esp+=4; }
void f_00634370(Cpu *c) { c->esp+=4; }
void f_004655F0(Cpu *c) { c->esp+=4; }
void f_00401800(Cpu *c) { c->esp+=4; }
/* Frozen pre-admission public behavior; does not depend on a moving git ref. */
void f_005ACB70(Cpu *c) {
#ifdef ORIGINAL_PUBLIC
    f_005ACB70_orig(c);
#else
    carsave_public_load(c);
#endif
}
void f_0058FD10(Cpu *c) { carsave_public_serialize(c); }
GENERATED
extern void file_init(const char *);
extern int file_publish(const char *,const void *,int);
extern uint32_t file_begin(const char *);
extern int file_finish(uint32_t);
extern int file_latewrite(uint32_t);
extern int file_latetruncate(uint32_t);
extern void file_fail(int);
static void digest(unsigned char *b) { CC_MD5(b+16,SIZE-32,b+SIZE-16); }
static void invoke(Cpu *c,int save,unsigned slot) {
    uint32_t sp=c->esp;
    if(!save) { c->esp-=4; M32(c->esp)=slot; c->esp-=4; M32(c->esp)=0; }
    c->esp-=4; M32(c->esp)=SIZE; c->esp-=4; M32(c->esp)=buffer;
    c->esp-=4; M32(c->esp)=0; c->ecx=self;
    if(save) f_0058FD10(c); else f_005ACB70(c);
    CHECK(c->esp==sp);
}
static void empty_fe(uint32_t db) {
    memset(G+db,0,CARSAVE_PUBLIC_FE_SIZE);
    for(unsigned k=0;k<200;++k) M32(db+k*20)=UINT32_MAX;
}
static void row(uint32_t db,unsigned at,unsigned slot) {
    const CarCatalogSlot *s=car_catalog_inventory_get(slot); assert(s && s->ready);
    uint32_t r=db+at*20; M32(r)=at; M32(r+4)=key(s->frontend); M32(r+8)=key(s->pvehicle);
    M32(r+12)=1; M16(r+16)=0xFFFF;
}
static void seed(Cpu *c,int dependent) {
    memset(G+self,0,0x9CB0); empty_fe(self+0x414);
    if(dependent) { row(self+0x414,3,0); M16(self+0x414+3*20+16)=0; }
    invoke(c,1,0); CHECK(!carsave_public_verify(G+buffer,SIZE));
}
static void readsave(const char *p,unsigned char *out) {
    memset(out,0,SIZE); int fd=open(p,O_RDONLY); CHECK(fd>=0);
    if(fd>=0) { CHECK(pread(fd,out,SIZE,36)==SIZE); close(fd); }
}
static unsigned files(const char *root,const char *suffix) {
    DIR *d=opendir(root); if(!d) return 0;
    unsigned n=0; struct dirent *e;
    while((e=readdir(d))) { size_t a=strlen(e->d_name),b=strlen(suffix); if(a>=b && !strcmp(e->d_name+a-b,suffix)) ++n; }
    closedir(d); return n;
}
static void paths(const char *root,const unsigned char *b,char *pending,char *meta) {
    unsigned char h[32]; char k[65]; CC_SHA256(b,SIZE,h);
    for(unsigned i=0;i<32;++i) snprintf(k+2*i,3,"%02x",h[i]);
    snprintf(pending,4096,"%s/.car-catalog/%s.pending",root,k);
    snprintf(meta,4096,"%s/.car-catalog/%s.meta",root,k);
}
static void child_status(pid_t pid) { int status; assert(waitpid(pid,&status,0)==pid); CHECK(WIFEXITED(status)&&!WEXITSTATUS(status)); }
static const char *add_stock(Cpu *c,unsigned db) { row(db,10,75); (void)c; return NULL; }
static const char *bad_stock(Cpu *c,unsigned db) { row(db,10,75); M8(db+10*20+16)=75; (void)c; return NULL; }
static const char *overwrite_stock(Cpu *c,unsigned db) { row(db,3,75); (void)c; return NULL; }
static const char *overwrite_pool(Cpu *c,unsigned db) { M8(db+4000)^=1; (void)c; return NULL; }
int main(int argc,char **argv) {
    assert(argc==5); G=calloc(1,0x4000000); assert(G);
    Cpu c={0}; c.esp=0x1800000;
    M32(0x0091CF90u)=frontend; M32(frontend+8)=0xABC123;
    int off=!strcmp(argv[2],"off");
    int cat=car_catalog_init(argv[1],!off); (void)cat;
    carsave_public_init(argv[4],NULL); file_init(argv[4]);
    char path[4096],backups[4096],metadata[4096];
    snprintf(path,sizeof path,"%s/TEST",argv[4]); snprintf(backups,sizeof backups,"%s/backups",argv[4]);
    snprintf(metadata,sizeof metadata,"%s/.car-catalog",argv[4]);
    const char *mode=argv[3];
    if(!strcmp(mode,"seed")) {
        seed(&c,0); unsigned char ordinary[SIZE]; memcpy(ordinary,G+buffer,SIZE);
        CHECK(file_publish(path,G+buffer,1));
        CHECK(!files(metadata,".meta") && !files(metadata,".pending"));
        seed(&c,1); CHECK(memcmp(ordinary,G+buffer,16));
        CHECK(file_publish(path,G+buffer,1));
    } else if(!strcmp(mode,"seed-pending")) {
        seed(&c,1); CHECK(file_publish(path,G+buffer,0));
        CHECK(files(metadata,".pending")==1); CHECK(!files(metadata,".meta"));
    } else if(!strcmp(mode,"transaction")) {
        unsigned char ordinary[SIZE],saved[SIZE],out[SIZE];
        empty_fe(self+0x414); invoke(&c,1,0); memcpy(ordinary,G+buffer,SIZE);
        CHECK(!files(metadata,".pending")); CHECK(!mutations);
        CHECK(file_publish(path,ordinary,1)); readsave(path,out); CHECK(!memcmp(out,ordinary,SIZE));
        seed(&c,1); memcpy(saved,G+buffer,SIZE);
        CHECK(memcmp(saved,ordinary,16)); CHECK(!carsave_public_admit(saved,SIZE,NULL));
        unsigned char wrong[12]={2}; CHECK(carsave_public_admit(saved,SIZE,wrong)!=NULL);
        CHECK(!memcmp(saved+16,G+buffer+16,SIZE-16));
        char pending[4096],meta[4096]; paths(argv[4],saved,pending,meta);
        CHECK(!access(pending,F_OK)); CHECK(access(meta,F_OK));
        CHECK(file_publish(path,saved,0)); CHECK(files(backups,".bak")==1);
        DIR *bd=opendir(backups); CHECK(bd!=NULL); struct dirent *be;
        while(bd && (be=readdir(bd))) if(strstr(be->d_name,".bak")) {
            char bp[4096]; snprintf(bp,sizeof bp,"%s/%s",backups,be->d_name);
            readsave(bp,out); CHECK(!memcmp(out,ordinary,SIZE));
        }
        if(bd) closedir(bd);
        CHECK(!access(pending,F_OK)); carsave_public_save_complete(1);
        CHECK(access(pending,F_OK)); CHECK(!access(meta,F_OK));
        CHECK(file_publish(path,saved,1)); CHECK(files(backups,".bak")==1);
        /* New dependency on the same generation needs a second prior backup. */
        row(self+0x414,5,75); invoke(&c,1,0); memcpy(saved,G+buffer,SIZE);
        CHECK(file_publish(path,saved,1)); CHECK(files(backups,".bak")==2);
        /* Failed file rename keeps the exact prior generation. */
        G[self+0x324]^=1; invoke(&c,1,0); file_fail(1);
        CHECK(!file_publish(path,G+buffer,1)); file_fail(0);
        readsave(path,out); CHECK(!memcmp(out,saved,SIZE));
        /* Kill after stage, after publish, before completion. */
        pid_t pid=fork(); assert(pid>=0);
        if(!pid) { G[self+0x324]^=2; invoke(&c,1,0); _exit(file_publish(path,G+buffer,0)?0:2); }
        child_status(pid); readsave(path,out); CHECK(!carsave_public_admit(out,SIZE,NULL));
        CHECK(!carsave_public_admit(saved,SIZE,NULL));
        /* Completion rename failure leaves a recoverable pending journal. */
        memcpy(G+buffer,out,SIZE); CHECK(carsave_public_save_prepare(path));
        fault_kind=3; carsave_public_save_complete(1); fault_kind=0;
        CHECK(!carsave_public_admit(out,SIZE,NULL));
        /* Check exact payload against the original translated serializer. */
        uint32_t tmp=buffer+0x100000; memcpy(G+tmp,G+buffer,SIZE);
        uint32_t sp=c.esp; c.esp-=12; M32(c.esp)=0; M32(c.esp+4)=buffer; M32(c.esp+8)=SIZE; c.ecx=self;
        f_0058FD10_orig(&c); CHECK(c.esp==sp);
        unsigned char raw[SIZE]; memcpy(raw,G+buffer,SIZE);
        invoke(&c,1,0); CHECK(!memcmp(raw+16,G+buffer+16,SIZE-16));
        CHECK(!carsave_public_verify(G+buffer,SIZE));
        /* Kill before save rename: the old profile and its provenance survive. */
        memcpy(saved,G+buffer,SIZE); CHECK(file_publish(path,saved,1));
        pid=fork(); assert(pid>=0);
        if(!pid) { uint32_t h=file_begin(path); file_latewrite(h); _exit(0); }
        child_status(pid); readsave(path,out); CHECK(!memcmp(out,saved,SIZE));
        CHECK(!carsave_public_admit(out,SIZE,NULL));
    } else if(!strcmp(mode,"mw")) {
        seed(&c,0); unsigned char raw[SIZE]; memcpy(raw,G+buffer,SIZE);
        unsigned before=lookups; invoke(&c,1,0); CHECK(lookups==before);
        CHECK(!memcmp(raw,G+buffer,SIZE)); CHECK(!mutations);
        uint32_t sp=c.esp; c.esp-=12; M32(c.esp)=0; M32(c.esp+4)=buffer; M32(c.esp+8)=SIZE; c.ecx=self;
        f_0058FD10_orig(&c); CHECK(c.esp==sp); CHECK(!memcmp(raw,G+buffer,SIZE));
        M32(frontend+8)=M32(buffer+0x5A09); invoke(&c,0,0); CHECK(c.eax&255);
        M32(frontend+8)^=1; invoke(&c,0,0); CHECK(!(c.eax&255));
        CHECK(carsave_public_save_allowed()); CHECK(!mutations);
    } else if(!strcmp(mode,"journal")) {
        seed(&c,0); row(self+0x414,3,0);
        if(atoi(argv[2])==4) CHECK(!mkdir(metadata,0700));
        uint32_t writer=file_begin(path); CHECK(writer!=UINT32_MAX);
        fault_kind=atoi(argv[2]); invoke(&c,1,0); fault_kind=0;
        CHECK(!carsave_public_save_allowed()); CHECK(!memcmp(G+buffer,(unsigned char[16]){0},16));
        CHECK(!carsave_public_verify(G+buffer,SIZE)); CHECK(access(path,F_OK));
        CHECK(!file_latewrite(writer)); CHECK(!file_latetruncate(writer)); CHECK(!file_finish(writer));
    } else if(!strcmp(mode,"journal-retry")) {
        seed(&c,0); unsigned char raw[SIZE],used[12]={1};
        memcpy(raw,G+buffer,SIZE); CHECK(!mkdir(metadata,0700));
        fault_kind=10;
        CHECK(carsave_public_stage(G+buffer,SIZE,used)!=NULL);
        CHECK(!memcmp(raw,G+buffer,SIZE)); CHECK(files(metadata,".pending")==1);
        /* A fresh process sees the rename whose directory sync failed. */
        pid_t pid=fork(); assert(pid>=0);
        if(!pid) {
            for(int fault=10;fault<=11;++fault) {
                memcpy(G+buffer,raw,SIZE); fault_kind=fault;
                CHECK(carsave_public_stage(G+buffer,SIZE,used)!=NULL);
                CHECK(!memcmp(raw,G+buffer,SIZE));
            }
            fault_kind=0; file_syncs=dir_syncs=0;
            CHECK(!carsave_public_stage(G+buffer,SIZE,used));
            CHECK(file_syncs>0 && dir_syncs>0);
            CHECK(file_publish(path,G+buffer,1));
            CHECK(!carsave_public_admit(G+buffer,SIZE,used));
            CHECK(files(metadata,".meta")==1 && !files(metadata,".pending"));
            memcpy(raw,G+buffer,SIZE);
            for(int fault=10;fault<=11;++fault) {
                fault_kind=fault;
                CHECK(carsave_public_stage(G+buffer,SIZE,used)!=NULL);
                CHECK(!memcmp(raw,G+buffer,SIZE));
            }
            fault_kind=0; file_syncs=dir_syncs=0;
            CHECK(!carsave_public_stage(G+buffer,SIZE,used));
            CHECK(file_syncs>0 && dir_syncs>0);
            printf("journal-retry child checks=%u failures=%u file_syncs=%u dir_syncs=%u\n",checks,failures,file_syncs,dir_syncs);
            fflush(stdout); _exit(failures?1:0);
        }
        child_status(pid); fault_kind=0;
    } else if(!strcmp(mode,"serialization")) {
        seed(&c,0); uint32_t writer=file_begin(path); CHECK(writer!=UINT32_MAX);
        broken_serializer=1; invoke(&c,1,0);
        CHECK(!carsave_public_save_allowed()); CHECK(!files(metadata,".pending"));
        CHECK(!file_latewrite(writer)); CHECK(!file_latetruncate(writer)); CHECK(!file_finish(writer)); CHECK(access(path,F_OK));
    } else if(!strcmp(mode,"serialization-layout")) {
        seed(&c,0); unsigned char raw[SIZE]; memcpy(raw,G+buffer,SIZE);
        uint32_t sp=c.esp; c.esp-=12; M32(c.esp)=0; M32(c.esp+4)=buffer; M32(c.esp+8)=SIZE-1; c.ecx=self;
        f_0058FD10(&c); CHECK(c.esp==sp); CHECK(!carsave_public_save_allowed());
        CHECK(!memcmp(raw,G+buffer,SIZE)); CHECK(!mutations);
    } else if(!strcmp(mode,"metadata")) {
        seed(&c,1); unsigned char used[12]={1},wrong[12]={2},out[SIZE];
        memcpy(out,G+buffer,SIZE); CHECK(!carsave_public_admit(out,SIZE,used));
        CHECK(carsave_public_admit(out,SIZE,wrong)!=NULL);
        unsigned char original[SIZE]; memcpy(original,out,SIZE); unsigned before=mutations;
        out[0]^=1; CHECK(carsave_public_admit(out,SIZE,used)!=NULL);
        CHECK(mutations==before); CHECK(carsave_public_stage(out,SIZE,used)!=NULL);
        CHECK(mutations==before); memcpy(out,original,SIZE);
        used[11]=0x80; CHECK(carsave_public_admit(out,SIZE,used)!=NULL);
        char pending[4096],meta[4096]; paths(argv[4],out,pending,meta);
        int fd=open(pending,O_WRONLY); assert(fd>=0); unsigned char bad=0;
        CHECK(pwrite(fd,&bad,1,0)==1); close(fd); before=mutations;
        CHECK(carsave_public_admit(out,SIZE,NULL)!=NULL);
        memcpy(out,original,SIZE); memset(out,0,16);
        CHECK(carsave_public_stage(out,SIZE,(unsigned char[12]){1})!=NULL);
        CHECK(mutations==before+1); /* mkdirat(EEXIST), no metadata write/repair */
    } else if(!strcmp(mode,"shortwrite")) {
        seed(&c,0); row(self+0x414,3,0); fault_kind=atoi(argv[2]); invoke(&c,1,0);
        CHECK(carsave_public_save_allowed()); CHECK(!carsave_public_admit(G+buffer,SIZE,NULL));
    } else if(!strcmp(mode,"backup-failure")) {
        seed(&c,0); unsigned char raw[SIZE],out[SIZE]; memcpy(raw,G+buffer,SIZE); CHECK(file_publish(path,raw,1));
        row(self+0x414,3,0); invoke(&c,1,0);
        CHECK(!mkdir(backups,0700)); char block[4096]; snprintf(block,sizeof block,"%s/block",backups);
        /* Fault the backup's exclusive output open, after journal is durable. */
        fault_kind=atoi(argv[2]); CHECK(!file_publish(path,G+buffer,1)); fault_kind=0;
        CHECK(!carsave_public_save_allowed()); readsave(path,out); CHECK(!memcmp(raw,out,SIZE));
    } else {
        readsave(path,G+buffer); unsigned char before[0x9CB0],bytes[SIZE];
        memcpy(bytes,G+buffer,SIZE); memset(G+self,0xA6,0x9CB0); memcpy(before,G+self,sizeof before);
        if(!strcmp(mode,"custom") || !strcmp(mode,"career")) {
            if(!strcmp(mode,"custom")) M8(buffer+0x5BA1+3*20+16)=75;
            else M8(buffer+0x5BA1+3*20+17)=25;
            memset(G+buffer,0,16); digest(G+buffer);
            CHECK(!carsave_public_stage(G+buffer,SIZE,(unsigned char[12]){1}));
        }
        if(!strcmp(mode,"legacy")) memset(G+buffer,0,16);
        if(!strcmp(mode,"md5")) M8(buffer+0x5BA1)^=1;
        if(!strcmp(mode,"class")) missing_class=1;
        if(!strcmp(mode,"frontend")) missing_fe=1;
        if(!strcmp(mode,"parts")) missing_part=1;
        if(!strcmp(mode,"bad-reconcile")) carsave_public_init(argv[4],bad_stock);
        if(!strcmp(mode,"overwrite-row")) carsave_public_init(argv[4],overwrite_stock);
        if(!strcmp(mode,"overwrite-pool")) carsave_public_init(argv[4],overwrite_pool);
        if(!strcmp(mode,"reconcile")) carsave_public_init(argv[4],add_stock);
        unsigned writes=mutations,calls=retail_calls,crc=M32(frontend+8);
        int refusal=strcmp(mode,"load") && strcmp(mode,"reconcile") && strcmp(mode,"secondary") && strcmp(mode,"backup-load");
        uint32_t writer=file_begin(path); CHECK(writer!=UINT32_MAX);
        if(!strcmp(mode,"secondary") || !strcmp(mode,"secondary-refuse")) {
            M32(frontend+0x10)=self; c.ecx=frontend;
            uint32_t sp=c.esp; c.esp-=16; M32(c.esp)=0; M32(c.esp+4)=buffer; M32(c.esp+8)=SIZE; M32(c.esp+12)=0;
            f_005ACDD0(&c); CHECK(c.esp==sp);
        } else if(!strcmp(mode,"backup-load") || !strcmp(mode,"backup-refuse")) {
            M32(frontend+0x10)=self; M32(frontend+0x128)=buffer;
            c.ecx=frontend; uint32_t sp=c.esp; c.esp-=4; M32(c.esp)=0;
            f_005ACE40(&c); CHECK(c.esp==sp);
        } else invoke(&c,0,0);
        if(refusal) {
            CHECK(!carsave_public_save_allowed()); CHECK(dialogs==1);
            CHECK(!memcmp(before,G+self,sizeof before)); CHECK(retail_calls==calls);
            CHECK(mutations==writes); CHECK(!file_latewrite(writer)); CHECK(!file_latetruncate(writer)); CHECK(!file_finish(writer));
            CHECK(M32(frontend+8)==crc);
            unsigned char disk[SIZE]; readsave(path,disk); CHECK(!memcmp(disk,bytes,SIZE));
            CHECK(strstr(carsave_public_error(),"Restore or import"));
            /* A second otherwise valid load cannot clear the process barrier. */
            memcpy(G+buffer,bytes,SIZE); unsigned old_dialogs=dialogs;
            invoke(&c,0,0); CHECK(dialogs==old_dialogs+1); CHECK(retail_calls==calls);
            CHECK(!memcmp(before,G+self,sizeof before)); CHECK(mutations==writes);
            printf("refusal forbidden_retail_calls=%u forbidden_profile_writes=%u metadata_mutations=%u\n",retail_calls-calls,retail_writes,mutations-writes);
        } else {
            CHECK(carsave_public_save_allowed()); CHECK(retail_calls>calls); CHECK(mutations==writes);
            CHECK(M32(frontend+8)==crc);
            CHECK(!memcmp(bytes,G+buffer,SIZE));
            CHECK(!memcmp(G+self+0x414,G+buffer+0x5BA1,10*20));
            if(!strcmp(mode,"reconcile")) CHECK(M32(self+0x414+10*20)!=UINT32_MAX);
            CHECK(file_finish(writer));
        }
    }
    printf("public-save %s checks=%u failures=%u retail=%u profile_writes=%u\n",mode,checks,failures,retail_calls,retail_writes);
    free(G); return failures?1:0;
}
'''

FILE_EXTRA = r'''
extern int carsave_public_save_allowed(void);
extern int carsave_public_save_prepare(const char *);
extern int carsave_public_backup_before_write(const char *,const char *);
extern void carsave_public_save_complete(int);
void extratrax_save_complete(int ok) { (void)ok; }
void padbind_save_complete(int ok) { (void)ok; }
void file_init(const char *root) { userroot=root; }
void file_fail(int on) { fail_rename=on; }
uint32_t file_begin(const char *path) {
    unsigned char bytes[0xF848+36]; int fd=open(path,O_RDONLY);
    ssize_t n=fd<0?-1:read(fd,bytes,sizeof bytes); if(fd>=0) close(fd);
    uint32_t h=file_open(path,path,0x40000000u,0,2,0);
    if(h!=UINT32_MAX && n>0) { assert(file_write(h,bytes,(size_t)n)==n); lseek(handle_fd(h),0,SEEK_SET); }
    return h;
}
int file_finish(uint32_t h) { return file_close(h); }
int file_latewrite(uint32_t h) { return file_write(h,"BAD",3)>0; }
int file_latetruncate(uint32_t h) { return file_end(h); }
int file_publish(const char *path,const void *bytes,int complete) {
    uint32_t h=file_begin(path); if(h==UINT32_MAX) return 0;
    unsigned char header[36]={0};
    int ok=file_write(h,header,36)==36 && file_write(h,bytes,0xF848)==0xF848;
    if(!file_close(h)) ok=0;
    if(complete) carsave_public_save_complete(ok);
    return ok;
}
int private_cars_save_allowed(void) { return carsave_public_save_allowed(); }
int private_cars_save_prepare(const char *path) { return carsave_public_save_prepare(path); }
int private_cars_backup_before_write(const char *a,const char *b) { return carsave_public_backup_before_write(a,b); }
void private_cars_save_complete(int ok) { carsave_public_save_complete(ok); }
'''


def translated():
    result = []
    for filename, names in [('speed_22.c', ['0058FD10_orig']),
                            ('speed_23.c', ['005ACB70_orig', '005ACDD0', '005ACE40'])]:
        path = ROOT / 'recomp/out' / filename
        if not path.is_file():
            print('SKIP: translated profile functions absent; build recompiler output first')
            raise SystemExit(3)
        source = path.read_text()
        for name in names:
            start = source.index('void f_' + name + '(')
            end = source.index('\n}', start) + 2
            result.append(source[start:end])
    return '\n'.join(result)


def fixture(root):
    rows, outputs, inputs = cc.reservations(), {}, {}
    for slot in (0, 75):
        folder = f'MODEL_{slot}'
        base = root / f'local-{slot}'
        base.mkdir()
        groups = {}
        for group in cc.INPUT_GROUPS:
            p = base / group
            p.mkdir()
            (p / 'INPUT.BIN').write_bytes(f'synthetic {group} {slot}'.encode())
            groups[group] = p
        out = base / 'converted'
        out.mkdir()
        from car_catalog_test import sidecars
        for name, data in sidecars(folder, 'MODEL_DONOR').items():
            (out / name).write_bytes(data)
        row = dict(rows[slot], state='ready', origin=dict(game='carbon', platform='pc', folder=folder),
                   display=dict(name=f'Synthetic {slot}', manufacturer='Synthetic'),
                   bindings=dict(folder=folder, pvehicle=folder + '_PV', frontend=folder + '_FE', ecar=folder + '_EC'),
                   donors=dict(model='MODEL_DONOR', price='PRICE_DONOR'),
                   parts=[dict(type=0, keys=['BODY_STOCK', 'BODY_ALT']), dict(type=254, keys=['WHEEL_STOCK'])],
                   converter=dict(revision=1, policy=cc.digest('synthetic policy')),
                   inputs={g: {'INPUT.BIN': cc.fingerprint(p / 'INPUT.BIN')} for g, p in groups.items()},
                   outputs={n: cc.fingerprint(out / n) for n in os.listdir(out)})
        row['identity'] = cc.source_identity(**row['origin'])
        rows[slot], outputs[slot], inputs[slot] = row, out, groups
    compatible = copy.deepcopy(rows)
    compatible[0]['converter']['revision'] = 2
    compatible[75] = cc.reservations()[75]  # unrelated disappearance changes aggregate
    incompatible = copy.deepcopy(rows)
    incompatible[0]['parts'][0]['keys'].reverse()
    policy_doc = dict(schema=1, revision=1, slots=[dict(slot=s, identity=None, variants=[]) for s in range(90)])
    for variant in (rows, compatible, incompatible):
        for r in variant:
            if r['state'] != 'ready':
                continue
            entry = policy_doc['slots'][r['slot']]
            entry['identity'] = r['identity']
            pin = dict(contract=cc.contract_hash(r), compatibility=cc.slot_compatibility(r))
            if pin not in entry['variants']:
                entry['variants'].append(pin)
    data = cc.encoded(policy_doc)
    path = root / 'policy.json'
    path.write_bytes(data)
    policy = cc.load_policy(path, hashlib.sha256(data).hexdigest())
    for name, variant in [('catalog', rows), ('compatible', compatible), ('incompatible', incompatible)]:
        active = [r['slot'] for r in variant if r['state'] == 'ready']
        cc.publish(root / name, variant, {s: outputs[s] for s in active}, {s: inputs[s] for s in active}, policy=policy)
    (root / 'approval.h').write_text('#define CAR_CATALOG_POLICY_JSON ' + json.dumps(data.decode('ascii')) + '\n#define CAR_CATALOG_POLICY_SHA256 "' + hashlib.sha256(data).hexdigest() + '"\n')


def main():
    code = translated()
    with tempfile.TemporaryDirectory(prefix='public-save-') as directory:
        root = Path(directory).resolve()
        fixture(root)
        (root / 'native.c').write_text(NATIVE.replace('GENERATED', code))
        (root / 'module.c').write_text(GUARD.replace('MODULE', str(ROOT / 'runtime/carsave_public.c')))
        scan = audit.marked('runtime/boot.c', '// BEGIN offline save scan:', '// END offline save scan')
        layer = audit.marked('runtime/boot.c', '// BEGIN offline file layer:', '// END offline file layer')
        # These are the exact E-I adapter functions; avoid same-TU weak stubs.
        layer = re.sub(r'__attribute__\(\(weak\)\).*?\n(?=static int profile_write_denied)', '', layer, flags=re.S)
        file_source = '#include <assert.h>\n' + audit.FILE_PREFIX + scan + layer + FILE_EXTRA
        prototypes = '\n'.join(['int private_cars_save_allowed(void);', 'int private_cars_save_prepare(const char *);',
                                'int private_cars_backup_before_write(const char *,const char *);', 'void private_cars_save_complete(int);'])
        (root / 'file.c').write_text(file_source.replace(scan, prototypes + '\n' + scan, 1))
        common = ['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined', '-Wno-deprecated-declarations',
                  '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), '-I', str(ROOT / 'recomp/out'),
                  '-DCAR_CATALOG_APPROVAL_HEADER="' + str(root / 'approval.h') + '"']
        sources = [str(root / 'native.c'), str(root / 'module.c'), str(root / 'file.c'), str(ROOT / 'runtime/car_catalog.c')]
        def compile_native(name, options=()):
            build = subprocess.run(common + list(options) + sources + ['-o', str(root / name)], capture_output=True, text=True)
            assert not build.returncode, build.stderr[-5000:]
        compile_native('good')
        compile_native('original', ['-DORIGINAL_PUBLIC=1'])
        totals = cases = 0

        def run(mode, *, store='catalog', switch='on', user='user', original=False, expect=0, binary=None):
            nonlocal totals, cases
            userdata = root / user
            userdata.mkdir(exist_ok=True)
            p = subprocess.run([str(root / (binary or ('original' if original else 'good'))), str(root / store), switch, mode, str(userdata)], capture_output=True, text=True, timeout=45)
            assert p.returncode == expect, f'{mode}/{store}/{switch}: exit {p.returncode}\n{p.stdout}\n{p.stderr[-5000:]}'
            assert 'runtime error:' not in p.stderr and 'AddressSanitizer' not in p.stderr, p.stderr[-5000:]
            counts = re.findall(r'checks=(\d+) failures=(\d+)', p.stdout)
            assert counts, p.stdout
            totals += int(counts[-1][0])
            cases += 1
            print((binary + ' ' if binary else 'original ' if original else '') + mode + '/' + store + '/' + switch + ': ' + p.stdout.strip().replace('\n', '; '))
            return p

        run('mw', user='mw', switch='off')
        run('metadata', user='metadata')
        run('transaction', user='transaction')
        # A normal serializer supplies marked, durable fixtures for restart cases.
        run('seed')
        run('seed-pending', user='pending')
        run('load', user='pending')
        run('load', store='compatible', user='pending')
        run('load')
        run('load', store='compatible')
        for mode in ('missing', 'secondary-refuse', 'backup-refuse'):
            run(mode, switch='off')
        shutil.copytree(root / 'user', root / 'original-user')
        baseline = run('missing', switch='off', user='original-user', original=True, expect=1)
        assert re.search(r'forbidden_retail_calls=[1-9]', baseline.stdout), baseline.stdout
        run('missing', store='incompatible')
        for mode in ('secondary', 'backup-load', 'reconcile', 'custom', 'career', 'md5', 'class', 'frontend', 'parts', 'bad-reconcile', 'overwrite-row', 'overwrite-pool', 'legacy'):
            run(mode)
        for fault in (1, 2, 3, 4):
            run('journal', switch=str(fault), user='journal-' + str(fault))
        for fault in (8, 9):
            run('shortwrite', switch=str(fault), user='short-' + str(fault))
        run('journal-retry', user='journal-retry')
        run('serialization', user='serialization')
        run('serialization-layout', user='serialization-layout')
        for fault in (1, 2, 3, 4):
            run('backup-failure', user='backup-failure-' + str(fault), switch=str(fault))
        # Fresh-process refusal with damaged output and damaged/missing provenance.
        corrupt = root / 'corrupt'
        shutil.copytree(root / 'catalog', corrupt)
        geometry = next(corrupt.glob('g-*/cars/0/GEOMETRY.BIN'))
        geometry.chmod(0o600)
        geometry.write_bytes(b'corrupt synthetic output')
        run('missing', store='corrupt')
        geometry.chmod(0o400)
        for name, change in [('no-metadata', 'missing'), ('bad-metadata', 'corrupt'), ('symlink-metadata', 'symlink')]:
            user = root / name
            shutil.copytree(root / 'user', user)
            meta = next((user / '.car-catalog').glob('*.meta'))
            if change == 'missing':
                meta.unlink()
            elif change == 'corrupt':
                meta.write_bytes(b'corrupt')
            else:
                held = user / 'held'
                meta.rename(held)
                meta.symlink_to(held)
            run('missing', user=name)
        # Each gate has a broken control, in addition to the original public bug.
        actual = (ROOT / 'runtime/carsave_public.c').read_text()
        mutants = [
            ('reuse-no-sync', 'metadata_read(dir,key,&existing,1)', 'metadata_read(dir,key,&existing,0)', 'journal-retry', {}),
            ('reuse-no-dir-sync', '!memcmp(&existing,m,sizeof *m) && !durable_sync(dir)', '!memcmp(&existing,m,sizeof *m)', 'journal-retry', {}),
            ('reuse-predecessor', 'metadata_read(dir,key,&existing,1)', 'metadata_read(dir,key,&existing,0)', 'journal-retry', {}),
            ('no-journal', 'if (!metadata_write(&m))', 'if (0)', 'seed', {}),
            ('no-dependencies', 'if (used && memcmp(used,m.used,sizeof m.used))', 'if (0)', 'metadata', {}),
            ('no-signature', 'if (memcmp(hash,m->slots[k],32))', 'if (0)', 'missing', {'store': 'incompatible', 'user': 'user'}),
            ('exact-generation', 'if (!car_catalog_generation()[0])', 'if (strcmp(m->generation,car_catalog_generation()))', 'load', {'store': 'compatible', 'user': 'user'}),
            ('no-backup', 'if (destination && staged && backup(destination,staged))', 'if (1)', 'transaction', {}),
            ('no-barrier', 'return !refused;', 'return 1;', 'missing', {'switch': 'off', 'user': 'user'}),
            ('no-custom-link-check', 'if (custom>=75)', 'if (0)', 'custom', {'user': 'user'}),
            ('mw-crc-bypass', 'int added=car_catalog_count()!=0 || any(used) || memcmp(G+staged,G+buffer+FE_OFFSET,CARSAVE_PUBLIC_FE_SIZE);', 'int added=1;', 'mw', {'switch': 'off'}),
        ]
        for name, old, new, mode, options in mutants:
            assert old in actual
            (root / 'mutant.c').write_text(actual.replace(old, new, 1).replace(' && !durable_sync(dir)', '') if name=='reuse-predecessor' else actual.replace(old, new, 1))
            (root / 'module.c').write_text(GUARD.replace('MODULE', str(root / 'mutant.c')))
            compile_native(name)
            user = options.pop('user', 'user-' + name)
            if user == 'user':
                user = 'user-' + name
                shutil.copytree(root / 'user', root / user)
            run(mode, binary=name, expect=1, user=user, **options)
        print(f'public save proof: cases={cases} assertions={totals}; original refusal control rejected; native ASan/UBSan')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
