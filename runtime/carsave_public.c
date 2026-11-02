/* Public profile admission and durable provenance, independent of music.
 * Validate -> stage FE -> validate dependencies/parts -> retail -> publish FE.
 * Serialize retail -> journal exact bytes -> file prepare -> backup -> rename
 * -> complete. Pending records remain valid after interruption at any boundary.
 * Compatibility is per required slot, never exact generation/aggregate equality.
 */
#include "carsave_public.h"
#include "host.h"
#include "durable.h"
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#define FE_OFFSET 0x5BA1u
#define FEDB 0x0091CF90u
#define PVEHICLE 0x4A97EC8Fu
#define FRONTEND 0x85885722u
static const unsigned char magic[8] = {'N','F','S','C','A','R','4',1};
/* Byte-only structure: no host padding/endian dependence. Unused signatures
 * must be zero. SHA256 binds identity AND ordered part compatibility per slot. */
typedef struct {
    unsigned char magic[8];
    char generation[67];
    unsigned char used[CARSAVE_PUBLIC_MASK_SIZE];
    unsigned char slots[CAR_CATALOG_SLOTS][32];
    unsigned char save_hash[32], checksum[32];
} Metadata;
static char userdata[4096], problem[512];
static int refused;
static CarSavePublicReconcile reconcile;
static char prepared[32][65];
static unsigned nprepared;

void carsave_public_init(const char *root, CarSavePublicReconcile callback) {
    if (!root || strlen(root)>=sizeof userdata) userdata[0]=0;
    else strcpy(userdata,root);
    reconcile=callback;
}
int carsave_public_save_allowed(void) { return !refused; }
const char *carsave_public_error(void) { return problem; }
static void latch(const char *why) {
    refused=1;
    snprintf(problem,sizeof problem,"%s Save unchanged. Restore or import the required car packs, then restart.",why);
    fprintf(stderr,"Profile refused: %s\n",problem);
}
const char *carsave_public_verify(const void *buffer,size_t size) {
    if (!buffer || size!=CARSAVE_PUBLIC_SIZE) return "Unsupported or truncated profile layout.";
    unsigned char digest[16];
    CC_MD5((const unsigned char *)buffer+16,(CC_LONG)size-32,digest);
    return memcmp(digest,(const unsigned char *)buffer+size-16,16) ? "Profile integrity check failed." : NULL;
}
static int any(const unsigned char *used) {
    for (unsigned k=0;k<CARSAVE_PUBLIC_MASK_SIZE;++k) if (used[k]) return 1;
    return 0;
}
static int relative(const char *path) {
    if (!path || !*path || strlen(path)>=4096 || *path=='/' || strchr(path,'\\')) return 0;
    for (const char *p=path;*p;) {
        const char *end=strchr(p,'/'); size_t n=end?(size_t)(end-p):strlen(p);
        if (!n || n>255 || (n==1 && *p=='.') || (n==2 && !memcmp(p,"..",2))) return 0;
        for (size_t k=0;k<n;++k) if ((unsigned char)p[k]<32) return 0;
        if (!end) return 1;
        p=end+1; if (!*p) return 0;
    }
    return 0;
}
static int open_relative(int base,const char *path,int directory) {
    if (!relative(path)) { errno=EINVAL; return -1; }
    char copy[4096]; strcpy(copy,path);
    int fd=dup(base); if (fd<0) return -1;
    for (char *p=copy;;) {
        char *slash=strchr(p,'/'); if (slash) *slash=0;
        int next=openat(fd,p,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC|((slash || directory)?O_DIRECTORY:0));
        int error=errno; close(fd); errno=error;
        if (next<0) return -1;
        fd=next;
        if (!slash) return fd;
        p=slash+1;
    }
}
static int root_open(void) {
    if (*userdata!='/' || !relative(userdata+1)) { errno=EINVAL; return -1; }
    int base=open("/",O_RDONLY|O_DIRECTORY|O_CLOEXEC); if (base<0) return -1;
    int fd=open_relative(base,userdata+1,1); close(base); return fd;
}
static int metadata_root(int create) {
    int base=root_open(); if (base<0) return -1;
    if (create) {
        int made=mkdirat(base,".car-catalog",0700);
        if ((made && errno!=EEXIST) || (!made && durable_sync(base))) { close(base); return -1; }
    }
    int fd=open_relative(base,".car-catalog",1); close(base); return fd;
}
static void hex(const unsigned char hash[32],char out[65]) {
    for (unsigned k=0;k<32;++k) snprintf(out+k*2,3,"%02x",hash[k]);
}
static void checksum(Metadata *m) { CC_SHA256(m,(CC_LONG)offsetof(Metadata,checksum),m->checksum); }
static void marker(const Metadata *m,unsigned char out[16]) {
    unsigned char hash[32]; CC_SHA256(m,(CC_LONG)offsetof(Metadata,save_hash),hash);
    memcpy(out,magic,8); memcpy(out+8,hash,8);
}
static void signature(const CarCatalogSlot *slot,unsigned char out[32]) {
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx);
    CC_SHA256_Update(&ctx,slot->identity,65);
    CC_SHA256_Update(&ctx,slot->compatibility,65);
    CC_SHA256_Final(out,&ctx);
}
static const char *catalog(const Metadata *m) {
    if (!any(m->used) || (m->used[11]&0xFC)) return "Invalid car dependency metadata.";
    if (!car_catalog_generation()[0]) return "Required car catalog is unavailable or disabled.";
    unsigned char zero[32]={0};
    for (unsigned k=0;k<CAR_CATALOG_SLOTS;++k) {
        if (!(m->used[k/8]&(1u<<(k%8)))) {
            if (memcmp(m->slots[k],zero,32)) return "Invalid unused car signature.";
            continue;
        }
        const CarCatalogSlot *slot=car_catalog_inventory_get(k);
        if (!slot || slot->slot!=k || !slot->ready || !slot->selected)
            return "A required car pack is missing, disabled or corrupt.";
        unsigned char hash[32]; signature(slot,hash);
        if (memcmp(hash,m->slots[k],32)) return "Required car identity or part ordering is incompatible.";
    }
    return NULL;
}
static int metadata_read(int dir,const char *key,Metadata *m,int sync) {
    const char *suffixes[]={"meta","pending"};
    for (unsigned k=0;k<2;++k) {
        char name[80]; snprintf(name,sizeof name,"%s.%s",key,suffixes[k]);
        int fd=open_relative(dir,name,0);
        if (fd<0) { if (errno==ENOENT) continue; return -1; }
        struct stat st; size_t n=0;
        int ok=!fstat(fd,&st) && S_ISREG(st.st_mode) && st.st_nlink==1 && st.st_size==sizeof *m;
        while (ok && n<sizeof *m) {
            ssize_t got=read(fd,(unsigned char *)m+n,sizeof *m-n);
            if (got<0 && errno==EINTR) continue;
            if (got<=0) { ok=0; break; } n+=(size_t)got;
        }
        if (ok && sync && durable_sync(fd)) ok=0;
        close(fd); if (!ok) { errno=EINVAL; return -1; }
        Metadata copy=*m; checksum(&copy);
        if (memcmp(m->magic,magic,8) || memcmp(m->checksum,copy.checksum,32)) { errno=EINVAL; return -1; }
        return 0;
    }
    errno=ENOENT; return -1;
}
static int write_all(int fd,const void *bytes,size_t size) {
    size_t n=0;
    while (n<size) {
        ssize_t got=write(fd,(const unsigned char *)bytes+n,size-n);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) return 0;
        n+=(size_t)got;
    }
    return 1;
}
static int metadata_write(const Metadata *m) {
    int dir=metadata_root(1); if (dir<0) return 0;
    char key[65],name[80],temp[80]; hex(m->save_hash,key);
    Metadata existing;
    if (!metadata_read(dir,key,&existing,1)) {
        int ok=!memcmp(&existing,m,sizeof *m) && !durable_sync(dir); close(dir); return ok;
    }
    /* Never repair damaged provenance implicitly. */
    if (errno!=ENOENT) { close(dir); return 0; }
    static unsigned serial;
    snprintf(temp,sizeof temp,".stage-%ld-%u",(long)getpid(),++serial);
    int fd=openat(dir,temp,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    int ok=fd>=0 && write_all(fd,m,sizeof *m) && !durable_sync(fd);
    if (fd>=0 && close(fd)) ok=0;
    snprintf(name,sizeof name,"%s.pending",key);
    if (ok && renameatx_np(dir,temp,dir,name,RENAME_EXCL)) ok=0;
    if (ok && durable_sync(dir)) ok=0;
    if (!ok) unlinkat(dir,temp,0);
    close(dir); return ok;
}
const char *carsave_public_stage(void *buffer,size_t size,const unsigned char *used) {
    const char *why=carsave_public_verify(buffer,size); if (why) return why;
    if (!used) return "Missing dependency set.";
    unsigned char zero[16]={0};
    if (memcmp(buffer,zero,16) && (why=carsave_public_admit(buffer,size,used))) return why;
    if (!any(used)) return NULL;
    Metadata m={0}; memcpy(m.magic,magic,8); memcpy(m.used,used,sizeof m.used);
    const char *generation=car_catalog_generation();
    if (strlen(generation)>=sizeof m.generation) return "Unsupported catalog generation.";
    strcpy(m.generation,generation);
    for (unsigned k=0;k<CAR_CATALOG_SLOTS;++k) if (used[k/8]&(1u<<(k%8))) {
        const CarCatalogSlot *slot=car_catalog_inventory_get(k);
        if (!slot) return "A required car pack is missing.";
        signature(slot,m.slots[k]);
    }
    if ((why=catalog(&m))) return why;
    unsigned char tag[16],old[16]; marker(&m,tag);
    memcpy(old,buffer,16); memcpy(buffer,tag,16);
    CC_SHA256(buffer,(CC_LONG)size,m.save_hash); checksum(&m);
    if (!metadata_write(&m)) { memcpy(buffer,old,16); return "Car compatibility journal could not be saved."; }
    return NULL;
}
static const char *read_metadata(const void *buffer,size_t size,Metadata *m) {
    unsigned char hash[32],tag[16]; char key[65];
    CC_SHA256(buffer,(CC_LONG)size,hash); hex(hash,key);
    int dir=metadata_root(0); int rc=dir<0?-1:metadata_read(dir,key,m,0); if (dir>=0) close(dir);
    if (rc || memcmp(m->save_hash,hash,32)) return "Car compatibility metadata is missing or damaged.";
    marker(m,tag);
    if (memcmp(buffer,tag,16)) return "Car compatibility marker does not match the save.";
    return catalog(m);
}
const char *carsave_public_admit(const void *buffer,size_t size,const unsigned char *used) {
    const char *why=carsave_public_verify(buffer,size); if (why) return why;
    unsigned char zero[16]={0};
    if (memcmp(buffer,magic,8)) {
        if (memcmp(buffer,zero,16)) return "Unrecognized car compatibility marker.";
        if (used && any(used)) return "Added-car save has no verifiable provenance.";
        return NULL;
    }
    Metadata m; why=read_metadata(buffer,size,&m); if (why) return why;
    if (used && memcmp(used,m.used,sizeof m.used)) return "Saved car dependencies do not match their metadata.";
    return NULL;
}

/* Class/type queries read the staged FE rows. Save mode avoids frontend lookup:
 * that class is unloaded during free roam. Restore CPU after all guest queries. */
void f_005816B0_orig(Cpu *c);
static uint32_t binding_key(Cpu *c,const CarCatalogSlot *slot,unsigned frontend) {
    /* Inventory/bindings are startup-immutable. Hash strings once rather than
     * leaking guest strings on every row of every free-roam autosave. */
    static uint32_t keys[CAR_CATALOG_SLOTS][2];
    uint32_t *out=&keys[slot->slot][frontend];
    if (!*out) {
        uint32_t name=gstr(frontend?slot->frontend:slot->pvehicle);
        *out=call_guest(c,0x004549B0,1,&name);
    }
    return *out;
}
static const char *references(Cpu *c,uint32_t db,unsigned char *used,int loading) {
    Cpu saved=*c; const char *why=NULL;
    memset(used,0,CARSAVE_PUBLIC_MASK_SIZE);
    for (unsigned i=0;i<200 && !why;++i) {
        uint32_t row=db+i*20;
        if (M32(row)==UINT32_MAX) continue;
        uint32_t args[]={PVEHICLE,M32(row+8)};
        if (!call_guest(c,0x00455FD0,2,args)) { why="A required car collection is missing."; break; }
        c->ecx=row; c->esp-=4; M32(c->esp)=0; f_005816B0_orig(c);
        unsigned type=c->eax;
        if (type>=84+CAR_CATALOG_ADDITIONS) { why="Unrecognized saved car identity."; break; }
        if (type>=84) {
            unsigned k=type-84;
            const CarCatalogSlot *slot=car_catalog_inventory_get(k);
            if (!slot || slot->slot!=k || slot->id!=type || !slot->ready || !slot->selected) {
                why="A required car pack is missing, disabled or corrupt."; break;
            }
            uint32_t key=binding_key(c,slot,0);
            if (!key || M32(row+8)!=key) { why="Saved car identity does not match its trusted slot."; break; }
            key=binding_key(c,slot,1);
            if (!key || M32(row+4)!=key) { why="Saved frontend identity does not match its trusted slot."; break; }
            used[k/8]|=1u<<(k%8);
        }
        if (!loading) continue;
        args[0]=FRONTEND; args[1]=M32(row+4);
        if (!call_guest(c,0x00455FD0,2,args)) { why="A required frontend collection is missing."; break; }
        unsigned custom=M8(row+16),career=M8(row+17);
        if (career!=255 && career>=25) { why="Invalid career car link."; break; }
        if (custom==255) continue;
        if (custom>=75) { why="Invalid customization link."; break; }
        uint32_t record=db+4000+custom*0x198;
        for (unsigned j=0;j<139;++j) {
            uint32_t index=(uint32_t)(int32_t)(int16_t)M16(record+2*j);
            if (index==UINT32_MAX) continue;
            c->ecx=0x009B26A8u;
            if (!call_guest(c,0x00747C00,1,&index)) { why="Missing installed customization part data."; break; }
        }
    }
    *c=saved; return why;
}
static void refuse_load(Cpu *c,const char *why) {
    latch(why);
    Cpu saved=*c;
    static uint32_t text,format,empty;
    if (!text) { text=gmalloc(sizeof problem); format=gstr("%s"); empty=gstr(""); }
    memcpy(G+text,problem,sizeof problem);
    uint32_t args[]={empty,empty,1,format,text}; call_guest(c,0x00598590,5,args);
    *c=saved; c->eax&=0xFFFFFF00u; c->esp+=20;
}
static const char *reconciled_rows(Cpu *c,uint32_t original,uint32_t staged) {
    /* Reconciliation may only occupy previously free records. Linked pools and
     * every existing row remain byte-exact, including unused record fields. */
    if (memcmp(G+original+4000,G+staged+4000,CARSAVE_PUBLIC_FE_SIZE-4000))
        return "Reconciliation changed linked profile records.";
    Cpu saved=*c; const char *why=NULL;
    for (unsigned k=0;k<200 && !why;++k) {
        uint32_t before=original+k*20,after=staged+k*20;
        if (!memcmp(G+before,G+after,20)) continue;
        unsigned flags=M32(after+12)&0xFFFFu;
        if (M32(before)!=UINT32_MAX || M32(after)!=k || M16(after+16)!=0xFFFFu ||
            (flags!=1 && flags!=0x20)) { why="Reconciliation changed an existing car record."; break; }
        c->ecx=after; c->esp-=4; M32(c->esp)=0; f_005816B0_orig(c);
        if (c->eax<84 || c->eax>=84+CAR_CATALOG_ADDITIONS) why="Reconciliation inserted an unverified car.";
        for (unsigned j=0;j<200 && !why;++j) if (j!=k && M32(staged+j*20)==k)
            why="Reconciliation introduced a car handle collision.";
    }
    *c=saved; return why;
}
void f_005ACB70_orig(Cpu *c);
void f_0058FD10_orig(Cpu *c);
void carsave_public_load(Cpu *c) {
    uint32_t self=c->ecx,buffer=M32(c->esp+4),size=M32(c->esp+8);
    const char *why=refused?"A previous profile refusal requires restart.":NULL;
    if (!why && ((uint64_t)buffer+size>(1ull<<32) || M32(c->esp+16)>1 ||
                 !M32(FEDB) || (uint64_t)self+0x9CB0u>(1ull<<32))) why="Invalid profile buffer or slot.";
    if (!why) why=carsave_public_admit(G+buffer,size,NULL);
    if (why) { refuse_load(c,why); return; }
    uint32_t staged=gmalloc(CARSAVE_PUBLIC_FE_SIZE);
    memcpy(G+staged,G+buffer+FE_OFFSET,CARSAVE_PUBLIC_FE_SIZE);
    unsigned char used[CARSAVE_PUBLIC_MASK_SIZE];
    why=references(c,staged,used,1);
    if (!why) why=carsave_public_admit(G+buffer,size,used);
    if (!why && reconcile && car_catalog_count()) {
        Cpu saved=*c; why=reconcile(c,staged); *c=saved;
        if (!why) why=reconciled_rows(c,buffer+FE_OFFSET,staged);
        if (!why) { unsigned char reconciled[CARSAVE_PUBLIC_MASK_SIZE]; why=references(c,staged,reconciled,1); }
    }
    if (why) { gfree(staged); refuse_load(c,why); return; }
    uint32_t frontend=M32(FEDB),crc=M32(frontend+8);
    /* An active catalog changes the fresh FE CRC even when capacity leaves
     * the saved rows untouched. Dependencies and staged rows are validated above. */
    int added=car_catalog_count()!=0 || any(used) || memcmp(G+staged,G+buffer+FE_OFFSET,CARSAVE_PUBLIC_FE_SIZE);
    if (added) M32(frontend+8)=M32(buffer+0x5A09);
    f_005ACB70_orig(c);
    if (added) M32(frontend+8)=crc;
    if (c->eax&255) memcpy(G+self+0x414,G+staged,CARSAVE_PUBLIC_FE_SIZE);
    else if (added) latch("Retail profile admission failed.");
    gfree(staged);
}
void carsave_public_serialize(Cpu *c) {
    uint32_t buffer=M32(c->esp+4),size=M32(c->esp+8);
    if (refused || size!=CARSAVE_PUBLIC_SIZE || !M32(FEDB) ||
        (uint64_t)buffer+size>(1ull<<32) || (uint64_t)c->ecx+0x9CB0u>(1ull<<32)) {
        if (!refused) latch("Invalid serialization buffer or profile layout.");
        c->eax=0; c->esp+=12; return;
    }
    f_0058FD10_orig(c);
    const char *why=carsave_public_verify(G+buffer,size);
    if (!why && car_catalog_count()) {
        unsigned char used[CARSAVE_PUBLIC_MASK_SIZE];
        why=references(c,buffer+FE_OFFSET,used,0);
        if (!why) why=carsave_public_stage(G+buffer,size,used);
    }
    if (why) latch(why);
}

/* The file layer calls prepare before any rename and calls allowed at every
 * write/truncate/close, even when the descriptor predates admission refusal. */
static int read_payload(const char *path,unsigned char *bytes,struct stat *st) {
    if (!path || *path!='/' || !relative(path+1)) return -1;
    int root=open("/",O_RDONLY|O_DIRECTORY|O_CLOEXEC); if (root<0) return -1;
    int fd=open_relative(root,path+1,0); close(root); if (fd<0) return -1;
    int ok=!fstat(fd,st) && S_ISREG(st->st_mode) && st->st_nlink==1;
    if (ok && st->st_size==CARSAVE_PUBLIC_SIZE+36)
        ok=pread(fd,bytes,CARSAVE_PUBLIC_SIZE,36)==CARSAVE_PUBLIC_SIZE;
    close(fd); return ok?0:-1;
}
int carsave_public_save_prepare(const char *path) {
    if (refused) return 0;
    unsigned char bytes[CARSAVE_PUBLIC_SIZE]; struct stat st;
    if (read_payload(path,bytes,&st)) { latch("Cannot read staged save."); return 0; }
    if (st.st_size!=CARSAVE_PUBLIC_SIZE+36) return 1; /* other userdata files */
    const char *why=carsave_public_admit(bytes,sizeof bytes,NULL);
    if (why) { latch(why); return 0; }
    if (memcmp(bytes,magic,8)) return 1;
    unsigned char hash[32]; char key[65]; CC_SHA256(bytes,sizeof bytes,hash); hex(hash,key);
    for (unsigned k=0;k<nprepared;++k) if (!strcmp(key,prepared[k])) return 1;
    if (nprepared==32) { latch("Too many staged profile generations."); return 0; }
    strcpy(prepared[nprepared++],key); return 1;
}

static int backup(const char *destination,const char *staged) {
    unsigned char bytes[CARSAVE_PUBLIC_SIZE]; struct stat st;
    if (read_payload(staged,bytes,&st)) return 0;
    if (st.st_size!=CARSAVE_PUBLIC_SIZE+36 || memcmp(bytes,magic,8)) return 1;
    Metadata next; if (read_metadata(bytes,sizeof bytes,&next)) return 0;
    size_t len=strlen(userdata);
    if (!len || strncmp(destination,userdata,len) || destination[len]!='/') return 0;
    const char *path=destination+len+1;
    int base=root_open(); if (base<0) return 0;
    int input=open_relative(base,path,0);
    if (input<0) { int absent=errno==ENOENT; close(base); return absent; }
    if (fstat(input,&st) || !S_ISREG(st.st_mode) || st.st_nlink!=1) { close(input); close(base); return 0; }
    if (st.st_size==sizeof bytes+36 && pread(input,bytes,sizeof bytes,36)==sizeof bytes &&
        !carsave_public_admit(bytes,sizeof bytes,NULL) && !memcmp(bytes,magic,8)) {
        Metadata old;
        if (!read_metadata(bytes,sizeof bytes,&old)) {
            int new_dependency=0;
            for (unsigned k=0;k<CAR_CATALOG_SLOTS;++k) if (next.used[k/8]&(1u<<(k%8)))
                new_dependency|=!(old.used[k/8]&(1u<<(k%8))) || memcmp(old.slots[k],next.slots[k],32);
            if (!new_dependency) { close(input); close(base); return 1; }
        }
    }
    int made=mkdirat(base,"backups",0700);
    if ((made && errno!=EEXIST) || (!made && durable_sync(base))) { close(input); close(base); return 0; }
    int dir=open_relative(base,"backups",1); close(base); if (dir<0) { close(input); return 0; }
    unsigned char hash[32]; char key[65],name[160],temp[180];
    CC_SHA256(path,(CC_LONG)strlen(path),hash); hex(hash,key);
    /* Name binds destination and exact prior bytes, so successive first-use
     * transitions retain distinct backups even within one catalog generation. */
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); unsigned char chunk[8192]; ssize_t got;
    lseek(input,0,SEEK_SET);
    while ((got=read(input,chunk,sizeof chunk))>0) CC_SHA256_Update(&ctx,chunk,(CC_LONG)got);
    int ok=got==0; CC_SHA256_Final(hash,&ctx); char content[65]; hex(hash,content);
    snprintf(name,sizeof name,"%.32s-%s.bak",key,content);
    int prior=open_relative(dir,name,0);
    if (prior>=0) {
        struct stat before; unsigned char actual[32]; CC_SHA256_Init(&ctx);
        ok=ok && !fstat(prior,&before) && S_ISREG(before.st_mode) && before.st_nlink==1 && before.st_size==st.st_size;
        while (ok && (got=read(prior,chunk,sizeof chunk))>0) CC_SHA256_Update(&ctx,chunk,(CC_LONG)got);
        CC_SHA256_Final(actual,&ctx);
        ok=ok && got==0 && !memcmp(actual,hash,32) && !durable_sync(prior) && !durable_sync(dir);
        close(prior); close(input); close(dir); return ok;
    }
    if (errno!=ENOENT) ok=0;
    static unsigned serial;
    snprintf(temp,sizeof temp,".%s-%ld-%u.tmp",name,(long)getpid(),++serial);
    int output=ok?openat(dir,temp,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600):-1;
    if (output<0) ok=0;
    if (lseek(input,0,SEEK_SET)<0) ok=0;
    CC_SHA256_Init(&ctx);
    while (ok && (got=read(input,chunk,sizeof chunk))>0) {
        CC_SHA256_Update(&ctx,chunk,(CC_LONG)got); ok=write_all(output,chunk,(size_t)got);
    }
    unsigned char copied[32]; CC_SHA256_Final(copied,&ctx);
    if (got<0 || memcmp(copied,hash,32) || (ok && durable_sync(output))) ok=0;
    if (output>=0 && close(output)) ok=0;
    close(input);
    if (ok && renameatx_np(dir,temp,dir,name,RENAME_EXCL)) ok=0;
    if (ok && durable_sync(dir)) ok=0;
    unlinkat(dir,temp,0); close(dir); return ok;
}
int carsave_public_backup_before_write(const char *destination,const char *staged) {
    if (refused) return 0;
    if (destination && staged && backup(destination,staged)) return 1;
    latch("Profile backup could not be preserved."); return 0;
}
void carsave_public_save_complete(int committed) {
    int dir=committed && !refused && nprepared?metadata_root(0):-1;
    if (dir>=0) {
        for (unsigned k=0;k<nprepared;++k) {
            char from[80],to[80];
            snprintf(from,sizeof from,"%s.pending",prepared[k]); snprintf(to,sizeof to,"%s.meta",prepared[k]);
            if (renameat(dir,from,dir,to) && errno!=ENOENT)
                fprintf(stderr,"Profile recovery journal retained: %s\n",strerror(errno));
        }
        if (durable_sync(dir)) fprintf(stderr,"Profile recovery journal retained after flush failure.\n");
        close(dir);
    }
    nprepared=0;
}
