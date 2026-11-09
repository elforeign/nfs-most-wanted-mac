#include "save_timing.h"
#include "underglow_store.h"
#include <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/xattr.h>

#define HEADER 48u
#define CAPACITY (HEADER + UG_MAX_SNAPSHOTS * (36u + UG_MAX_CARS * 24u))
/* v2 readers validate every byte of the data fork, including nonzero choices.
 * Only the new explicit zero needs an extension. An attribute on the same
 * inode travels through the atomic rename; older readers ignore it entirely.
 * Bind the bitmap to the data checksum and checksum the extension itself. */
#define ZERO_ATTR "org.nfsmw.underglow.zero"
#define ZERO_BYTES ((UG_MAX_SNAPSHOTS * UG_MAX_CARS + 7u) / 8u)
#define ZERO_SIZE (32u + ZERO_BYTES + 32u)
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
            (e->car.kind!=2 && e->car.kind!=4 && e->car.kind!=6) || (e->preset&~UG_CHOICE_MASK)) return 0;
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
static unsigned legacy_colour(unsigned colour) {
    return colour==3 ? 4 : colour==4 ? 6 : colour==6 ? 3 : colour;
}
static unsigned stored_choice(unsigned choice) {
    unsigned colour=choice&7u;
    colour=colour==3 ? 6 : colour==4 ? 3 : colour==6 ? 4 : colour;
    return (choice&UG_LIGHT_ON)|colour;
}
/* Fully validate before exposing any snapshot, including records not requested. */
static unsigned read_all(const char *path, UGSnapshot *snapshots) {
    unsigned char bytes[CAPACITY+1], expected[32], actual[32], zeros[ZERO_SIZE]={0};
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
    ssize_t zero_size=-1;
    if(clean && get32(bytes+8)==2) {
        zero_size=fgetxattr(fd,ZERO_ATTR,zeros,sizeof zeros,0,0);
        if(zero_size<0 && errno!=ENOATTR && errno!=ENOTSUP) clean=0;
    }
    close(fd);
    if (!clean || memcmp(bytes,magic,8) || get32(bytes+8)<1 || get32(bytes+8)>3) return 0;
    unsigned count=get32(bytes+12);
    if (!count || count>UG_MAX_SNAPSHOTS) return 0;
    memcpy(expected,bytes+16,32); checksum(bytes,size,actual);
    if (memcmp(expected,actual,32)) return 0;
    int zero_extension=0;
    if(zero_size>=0) {
        if(zero_size!=ZERO_SIZE) return 0;
        /* A writer unaware of attributes may replace the data in place. */
        if(!memcmp(zeros,expected,32)) {
            CC_SHA256(zeros,32+ZERO_BYTES,actual);
            if(memcmp(actual,zeros+32+ZERO_BYTES,32)) return 0;
            zero_extension=1;
        }
    }
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
            if (e->preset&~UG_CHOICE_MASK) return 0;
            if (get32(bytes+8)<3 && !e->preset) return 0;
            if (get32(bytes+8)==1 && e->preset>7) return 0;
            /* Colour-only snapshots predate the independent Light choice. */
            if (get32(bytes+8)==1) e->preset|=UG_LIGHT_ON;
            if (get32(bytes+8)<3) e->preset=(e->preset&UG_LIGHT_ON)|legacy_colour(e->preset&7u);
            unsigned bit=i*UG_MAX_CARS+j;
            if(zero_extension && (zeros[32+bit/8]&(1u<<(bit%8)))) {
                if(e->preset!=UG_LIGHT_ON) return 0;
                e->preset=0;zeros[32+bit/8]&=(unsigned char)~(1u<<(bit%8));
            }
        }
        if (!valid(s)) return 0;
        for (unsigned j=0;j<i;++j) if (!memcmp(snapshots[j].digest,s->digest,32)) return 0;
    }
    if(zero_extension) for(unsigned i=0;i<ZERO_BYTES;++i) if(zeros[32+i]) return 0;
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
    memcpy(bytes,magic,8); put32(bytes+8,2); put32(bytes+12,keep);
    unsigned char zeros[ZERO_SIZE]={0};int has_zero=0;
    size_t at=HEADER;
    for (unsigned i=0;i<keep;++i) {
        const UGSnapshot *snap=&all[i];
        memcpy(bytes+at,snap->digest,32); put32(bytes+at+32,snap->count); at+=36;
        for (unsigned j=0;j<snap->count;++j) {
            const UGEntry *e=&snap->entries[j];
            put32(bytes+at,e->car.handle); put32(bytes+at+4,e->car.frontend);
            put32(bytes+at+8,e->car.vehicle); put32(bytes+at+12,e->car.customization);
            put32(bytes+at+16,e->car.kind);
            put32(bytes+at+20,e->preset ? stored_choice(e->preset) : UG_LIGHT_ON);at+=24;
            if(!e->preset) { unsigned bit=i*UG_MAX_CARS+j;zeros[32+bit/8]|=1u<<(bit%8);has_zero=1; }
        }
    }
    unsigned char hash[32]; checksum(bytes,at,hash); memcpy(bytes+16,hash,32);
    if(has_zero) { memcpy(zeros,hash,32);CC_SHA256(zeros,32+ZERO_BYTES,zeros+32+ZERO_BYTES); }
    int n=snprintf(temp,sizeof temp,"%s.tmp.XXXXXX",path), fd=-1, ok=0;
    if (n>0 && (size_t)n<sizeof temp) fd=mkstemp(temp);
    if (fd>=0) {
        /* A short write is an optional failure, never a partial publication. */
        ok=(!has_zero || fsetxattr(fd,ZERO_ATTR,zeros,sizeof zeros,0,0)==0) &&
            SAVE_TIME("write", write(fd,bytes,at))==(ssize_t)at && SAVE_TIME("fsync", fsync(fd))==0;
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
