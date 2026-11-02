"""Production snapshot proof: bounded, restartable, checksummed atomic storage.
Good 197/197 + restart 4/4; digest omission fails 3, rename 3, validation 8.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parent
MAIN=r'''
#include <assert.h>
#include <errno.h>
#include <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
static int fail_write, fail_rename, fail_sync;
static ssize_t injected_write(int fd,const void *p,size_t n) {
    if(fail_write) { errno=ENOSPC; return write(fd,p,n/2); }
    return write(fd,p,n);
}
static int injected_rename(const char *a,const char *b) {
    if(fail_rename) { errno=EACCES; return -1; } return rename(a,b);
}
static int injected_sync(int fd) { if(fail_sync) { errno=EIO; return -1; } return fsync(fd); }
#define write injected_write
#define rename injected_rename
#define fsync injected_sync
#include "STORE_SOURCE"
#undef write
#undef rename
#undef fsync
static unsigned checks,failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL %u: %s\n",__LINE__,#x); } } while(0)
static unsigned char payload[UG_SAVE_BYTES], original[UG_SAVE_BYTES];
static UGSnapshot snap, out;
static void reseal(unsigned char *bytes,size_t size) {
    unsigned char digest[32]; memset(bytes+16,0,32); CC_SHA256(bytes,(CC_LONG)size,digest); memcpy(bytes+16,digest,32);
}
static void putfile(const char *path,const unsigned char *bytes,size_t n) {
    int fd=open(path,O_WRONLY|O_TRUNC); assert(fd>=0); assert(write(fd,bytes,n)==(ssize_t)n); close(fd);
}
int main(int argc,char **argv) {
    assert(argc==3); const char *dir=argv[1];
    for(unsigned i=0;i<sizeof payload;++i) payload[i]=(unsigned char)(i*37);
    memcpy(original,payload,sizeof payload);
    CHECK(ug_save_digest(payload,sizeof payload,snap.digest));
    snap.count=2;
    snap.entries[0]=(UGEntry){{5,0xABC,0xDEF,3,2},2};
    snap.entries[1]=(UGEntry){{6,0xABC,0xDEF,4,2},4};
    if(!strcmp(argv[2],"restart")) {
        CHECK(ug_store_load(dir,snap.digest,&out)); CHECK(out.count==2);
        CHECK(out.entries[0].preset==2 && out.entries[1].preset==4);
        printf("restart good=%u broken=%u\n",checks-failures,failures); return failures?1:0;
    }
    CHECK(!ug_store_load(dir,snap.digest,&out)); CHECK(out.count==0);
    CHECK(!ug_save_digest(payload,sizeof payload-1,out.digest));
    CHECK(ug_store_publish(dir,&snap)); CHECK(!memcmp(original,payload,sizeof payload));
    CHECK(ug_store_load(dir,snap.digest,&out)); CHECK(out.count==2 && out.entries[1].preset==4);
    unsigned char wrong[32]; memcpy(wrong,snap.digest,32); wrong[0]^=1;
    CHECK(!ug_store_load(dir,wrong,&out)); CHECK(out.count==0);
    UGSnapshot replacement=snap; replacement.entries[0].preset=1;
    for(unsigned f=0;f<3;++f) {
        fail_write=f==0; fail_sync=f==1; fail_rename=f==2;
        CHECK(!ug_store_publish(dir,&replacement));
        fail_write=fail_sync=fail_rename=0;
        CHECK(ug_store_load(dir,snap.digest,&out)); CHECK(out.entries[0].preset==2);
    }
    replacement.count=UG_MAX_CARS+1; CHECK(!ug_store_publish(dir,&replacement));
    replacement=snap; replacement.entries[0].preset=8; CHECK(!ug_store_publish(dir,&replacement));
    replacement=snap; replacement.entries[0].car.customization=76; CHECK(!ug_store_publish(dir,&replacement));
    replacement=snap; replacement.entries[1].car.handle=5; CHECK(!ug_store_publish(dir,&replacement));
    char path[4096]; snprintf(path,sizeof path,"%s/underglow.bin",dir);
    unsigned char bytes[CAPACITY+1], saved[CAPACITY+1];
    int fd=open(path,O_RDONLY); assert(fd>=0); ssize_t size=read(fd,saved,sizeof saved); close(fd); assert(size>0);
    // Every truncation boundary must reject, not partially restore a prefix.
    for(size_t cut=0;cut<(size_t)size;++cut) {
        putfile(path,saved,cut); memset(&out,0xFF,sizeof out);
        CHECK(!ug_store_load(dir,snap.digest,&out) && out.count==0);
    }
    // Resealed malformed records exercise validation independently of checksum.
    for(unsigned fault=0;fault<8;++fault) {
        memcpy(bytes,saved,(size_t)size);
        if(fault==0) bytes[0]^=1;
        if(fault==1) put32(bytes+8,2);
        if(fault==2) put32(bytes+12,UG_MAX_SNAPSHOTS+1);
        if(fault==3) put32(bytes+HEADER+32,UG_MAX_CARS+1);
        if(fault==4) put32(bytes+HEADER+36+20,8);
        if(fault==5) put32(bytes+HEADER+36+12,76);
        if(fault==6) put32(bytes+HEADER+36+16,1);
        if(fault==7) put32(bytes+HEADER+36+24,5);
        reseal(bytes,(size_t)size); putfile(path,bytes,(size_t)size);
        CHECK(!ug_store_load(dir,snap.digest,&out)); CHECK(out.count==0);
    }
    memcpy(bytes,saved,(size_t)size); bytes[size-1]^=1; putfile(path,bytes,(size_t)size);
    CHECK(!ug_store_load(dir,snap.digest,&out));
    memcpy(bytes,saved,(size_t)size); bytes[size]=0; putfile(path,bytes,(size_t)size+1);
    CHECK(!ug_store_load(dir,snap.digest,&out));
    putfile(path,saved,(size_t)size);
    fd=open(path,O_WRONLY); assert(fd>=0); assert(!ftruncate(fd,CAPACITY+1)); close(fd);
    CHECK(!ug_store_load(dir,snap.digest,&out));
    assert(!unlink(path)); assert(!mkfifo(path,0600));
    CHECK(!ug_store_load(dir,snap.digest,&out)); assert(!unlink(path));
    CHECK(!symlink("absent-sidecar-target",path)); CHECK(!ug_store_load(dir,snap.digest,&out)); assert(!unlink(path));
    fd=open(path,O_WRONLY|O_CREAT,0600); assert(fd>=0); close(fd);
    putfile(path,saved,(size_t)size);
    // Saturation evicts the oldest digest and never grows beyond the format cap.
    for(unsigned i=1;i<=UG_MAX_SNAPSHOTS;++i) {
        replacement=snap; replacement.digest[0]^=(unsigned char)i;
        CHECK(ug_store_publish(dir,&replacement));
    }
    CHECK(!ug_store_load(dir,snap.digest,&out));
    CHECK(ug_store_load(dir,replacement.digest,&out));
    struct stat st; CHECK(!stat(path,&st) && st.st_size<=CAPACITY);
    CHECK(ug_store_publish(dir,&snap));
    printf("store good=%u broken=%u\n",checks-failures,failures);
    return failures?1:0;
}
'''
def main():
    with tempfile.TemporaryDirectory() as tmp:
        d=Path(tmp)
        original=(ROOT/'underglow_store.c').read_text()
        for mutation in ('good','digest','rename','validation'):
            source=original
            if mutation=='digest': source=source.replace('if (!memcmp(all[i].digest,digest,32))','if (1)')
            if mutation=='rename': source=source.replace('ok=SAVE_TIME("rename", rename(temp,path))==0;', '(void)SAVE_TIME("rename", rename(temp,path)); ok=1;')
            if mutation=='validation': source=source.replace('if (!valid(s)) return 0;', 'if (0) return 0;')
            assert source!=original or mutation=='good'
            production=d/(mutation+'-store.c'); production.write_text(source)
            driver=d/(mutation+'.c'); driver.write_text(MAIN.replace('STORE_SOURCE',str(production)))
            exe=d/mutation
            r=subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-Wno-deprecated-declarations','-I',str(ROOT),str(driver),'-o',str(exe)],capture_output=True,text=True)
            assert r.returncode==0,r.stderr[-3000:]
            profile=d/(mutation+'-data');profile.mkdir()
            r=subprocess.run([str(exe),str(profile),'seed'],capture_output=True,text=True)
            print(mutation,r.stdout.strip())
            assert r.returncode==int(mutation!='good'),r.stderr[-3000:]
            if mutation=='good':
                r=subprocess.run([str(exe),str(profile),'restart'],capture_output=True,text=True)
                print(r.stdout.strip()); assert r.returncode==0,r.stderr
if __name__=='__main__': main()
