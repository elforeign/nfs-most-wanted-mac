#include "save_timing.h"
#include "underglow_store.h"
#include <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

#define HEADER 48u
#define CAPACITY (HEADER + UG_MAX_SNAPSHOTS * (36u + UG_MAX_CARS * 24u))
static const unsigned char magic[8] = {'U','G','S','N','A','P',0,0};
static uint32_t get32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static void put32(unsigned char *p, uint32_t v) {
    for (unsigned i=0;i<4;++i) p[i]=(unsigned char)(v>>(8*i));
}
int ug_identity_equal(const UGIdentity *a, const UGIdentity *b) {
    return a && b && a->handle==b->handle && a->frontend==b->frontend &&
        a->vehicle==b->vehicle && a->customization==b->customization && a->kind==b->kind;
}
int ug_save_digest(const void *bytes, size_t size, unsigned char digest[32]) {
    if (!digest) return 0;
    memset(digest,0,32);
    if (!bytes || size!=UG_SAVE_BYTES) return 0;
    return CC_SHA256(bytes,(CC_LONG)size,digest)!=NULL;
}
static int valid(const UGSnapshot *s) {
    if (!s || s->count>UG_MAX_CARS) return 0;
    for (unsigned i=0;i<s->count;++i) {
        const UGEntry *e=&s->entries[i];
        if (e->car.handle>=UG_MAX_CARS || e->car.customization>=76 ||
            (e->car.kind!=2 && e->car.kind!=4 && e->car.kind!=6) || e->preset==0 || e->preset>7) return 0;
        for (unsigned j=0;j<i;++j) if (s->entries[j].car.handle==e->car.handle) return 0;
    }
    return 1;
}
static int path_for(const char *dir, char *path, size_t n) {
    if (!dir || !*dir) return 0;
    int r=snprintf(path,n,"%s/underglow.bin",dir);
    return r>0 && (size_t)r<n;
}
static void checksum(unsigned char *bytes, size_t size, unsigned char hash[32]) {
    memset(bytes+16,0,32);
    CC_SHA256(bytes,(CC_LONG)size,hash);
}
/* Fully validate before exposing any snapshot, including records not requested. */
static unsigned read_all(const char *path, UGSnapshot *snapshots) {
    unsigned char bytes[CAPACITY+1], expected[32], actual[32];
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
    if (fd<0) return 0;
    struct stat st;
    if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size<HEADER || st.st_size>CAPACITY) { close(fd); return 0; }
    size_t size=(size_t)st.st_size, got=0;
    while (got<size) {
        ssize_t r=read(fd,bytes+got,size-got);
        if (r<=0) { close(fd); return 0; }
        got+=(size_t)r;
    }
    unsigned char extra;
    int clean=read(fd,&extra,1)==0;
    close(fd);
    if (!clean || memcmp(bytes,magic,8) || get32(bytes+8)!=1) return 0;
    unsigned count=get32(bytes+12);
    if (!count || count>UG_MAX_SNAPSHOTS) return 0;
    memcpy(expected,bytes+16,32); checksum(bytes,size,actual);
    if (memcmp(expected,actual,32)) return 0;
    size_t at=HEADER;
    for (unsigned i=0;i<count;++i) {
        UGSnapshot *s=&snapshots[i];
        memset(s,0,sizeof *s);
        if (at+36>size) return 0;
        memcpy(s->digest,bytes+at,32); s->count=get32(bytes+at+32); at+=36;
        if (s->count>UG_MAX_CARS || at+s->count*24u>size) return 0;
        for (unsigned j=0;j<s->count;++j) {
            UGEntry *e=&s->entries[j];
            e->car.handle=get32(bytes+at); e->car.frontend=get32(bytes+at+4);
            e->car.vehicle=get32(bytes+at+8); e->car.customization=get32(bytes+at+12);
            e->car.kind=get32(bytes+at+16); e->preset=get32(bytes+at+20); at+=24;
        }
        if (!valid(s)) return 0;
        for (unsigned j=0;j<i;++j) if (!memcmp(snapshots[j].digest,s->digest,32)) return 0;
    }
    return at==size ? count : 0;
}
int ug_store_load(const char *dir, const unsigned char digest[32], UGSnapshot *out) {
    if (!out) return 0;
    memset(out,0,sizeof *out);
    char path[4096];
    if (!digest || !path_for(dir,path,sizeof path)) return 0;
    UGSnapshot *all=calloc(UG_MAX_SNAPSHOTS,sizeof *all);
    if (!all) return 0;
    unsigned count=read_all(path,all); int found=0;
    for (unsigned i=0;i<count;++i) if (!memcmp(all[i].digest,digest,32)) { *out=all[i]; found=1; break; }
    free(all);
    return found;
}
int ug_store_publish(const char *dir, const UGSnapshot *s) {
    char path[4096], temp[4096];
    if (!valid(s) || !path_for(dir,path,sizeof path)) return 0;
    UGSnapshot *all=calloc(UG_MAX_SNAPSHOTS,sizeof *all);
    unsigned char *bytes=calloc(1,CAPACITY);
    if (!all || !bytes) { free(all); free(bytes); return 0; }
    unsigned count=read_all(path,all), keep=0;
    /* Oldest first; replacing a digest moves it to the newest position. */
    for (unsigned i=0;i<count;++i) if (memcmp(all[i].digest,s->digest,32)) all[keep++]=all[i];
    if (keep==UG_MAX_SNAPSHOTS) { memmove(all,all+1,(--keep)*sizeof *all); }
    all[keep++]=*s;
    memcpy(bytes,magic,8); put32(bytes+8,1); put32(bytes+12,keep);
    size_t at=HEADER;
    for (unsigned i=0;i<keep;++i) {
        const UGSnapshot *snap=&all[i];
        memcpy(bytes+at,snap->digest,32); put32(bytes+at+32,snap->count); at+=36;
        for (unsigned j=0;j<snap->count;++j) {
            const UGEntry *e=&snap->entries[j];
            put32(bytes+at,e->car.handle); put32(bytes+at+4,e->car.frontend);
            put32(bytes+at+8,e->car.vehicle); put32(bytes+at+12,e->car.customization);
            put32(bytes+at+16,e->car.kind); put32(bytes+at+20,e->preset); at+=24;
        }
    }
    unsigned char hash[32]; checksum(bytes,at,hash); memcpy(bytes+16,hash,32);
    int n=snprintf(temp,sizeof temp,"%s.tmp.XXXXXX",path), fd=-1, ok=0;
    if (n>0 && (size_t)n<sizeof temp) fd=mkstemp(temp);
    if (fd>=0) {
        /* A short write is an optional failure, never a partial publication. */
        ok=SAVE_TIME("write", write(fd,bytes,at))==(ssize_t)at && SAVE_TIME("fsync", fsync(fd))==0;
        if (SAVE_TIME("close", close(fd))) ok=0;
        if (ok) ok=SAVE_TIME("rename", rename(temp,path))==0;
        if (!ok) unlink(temp);
        else {
            int parent=open(dir,O_RDONLY);
            if (parent>=0) { (void)SAVE_TIME("directory.fsync", fsync(parent)); SAVE_TIME("directory.close", close(parent)); }
        }
    }
    free(bytes); free(all);
    return ok;
}
