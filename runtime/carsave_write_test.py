"""Retail/cosmetic completion: good 59/59; barrier mutant fails 17, callback mutant 4.
Compiles boot.c's production file layer, with a separately linked strong
admission latch overriding boot's public/default weak pass-through. All paths
are scratch; no game, owner profile, sockets, GPU or Unicorn.
"""
from pathlib import Path
import subprocess
import tempfile
import sys
# Missing-fixture proof: omitted guard exits 1 on import; this guard exits 3.
if any(not (Path(__file__).resolve().parents[1] / name).is_file() for name in ['runtime/auditfix_test.py']):
    print("SKIP: optional development fixtures are absent")
    sys.exit(3)
sys.path.insert(0,str(Path(__file__).resolve().parent))
import auditfix_test as audit

ROOT = Path(__file__).resolve().parents[1]
MAIN = r'''
#include "underglow.h"
#include "underglow_store.h"
#undef write
#undef rename
#undef fsync
static unsigned checks, failures;
extern int cosmetic_fail;
void extratrax_save_complete(int committed) { (void)committed; }
void padbind_save_complete(int committed) { (void)committed; }
extern int refused;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL line %u: %s\n",__LINE__,#x); } } while(0)
static void seed(const char *p) { FILE *f=fopen(p,"wb"); assert(f); fputs("ORIGINAL",f); fclose(f); }
static int unchanged(const char *p) { char b[16]={0}; FILE *f=fopen(p,"rb"); if(!f)return 0; size_t n=fread(b,1,16,f); fclose(f); return n==8&&!memcmp(b,"ORIGINAL",8); }
static unsigned char payload[UG_SAVE_BYTES], observed[UG_SAVE_BYTES];
static int publish(const char *path,int fail) {
    unsigned seq=save_fail_seq, commits=save_commit_seq;
    uint32_t h=file_open(path,path,0x40000000,0,2,0); CHECK(h!=UINT32_MAX);
    unsigned char header[36]={0};
    CHECK(file_write(h,header,sizeof header)==sizeof header);
    if(fail) short_write=1;
    ssize_t wrote=file_write(h,payload,sizeof payload);
    CHECK((wrote==(ssize_t)sizeof payload)==!fail);
    int ok=file_close(h);
    M32(0x400+0x300)=1; M32(0x400+0x2f4)=0;
    save_task_fixup(0x400,0xb,seq);
    save_sidecars_complete(0x400,0xb,seq,commits);
    CHECK((M32(0x400+0x2f4)==0)==!fail);
    return ok;
}
static int exact_payload(const char *path) {
    int fd=open(path,O_RDONLY); if(fd<0)return 0;
    struct stat st; int exact=!fstat(fd,&st) && st.st_size==36+UG_SAVE_BYTES;
    exact=exact && pread(fd,observed,sizeof observed,36)==sizeof observed;
    close(fd); return exact&&!memcmp(observed,payload,sizeof payload);
}
int main(int argc,char **argv) {
    assert(argc==2); userroot=argv[1]; assert(!mkdir(userroot,0700));
    char p[4096]; snprintf(p,sizeof p,"%s/TEST",userroot); seed(p);
    uint32_t h=file_open(p,p,0xC0000000,0,2,0); CHECK(h!=UINT32_MAX);
    CHECK(file_write(h,"PARTIAL",7)==7); CHECK(unchanged(p));
    refused=1; CHECK(profile_write_denied(p)); CHECK(profile_write_denied(userroot));
    CHECK(!file_end(h));
    CHECK(file_write(h,"BAD",3)==-1); CHECK(!file_close(h)); CHECK(unchanged(p));
    for(unsigned disp=1;disp<=5;++disp) {
        uint32_t w=file_open(p,p,0x40000000,0,disp,0);
        CHECK(w==UINT32_MAX); if(w!=UINT32_MAX) file_close(w);
        CHECK(unchanged(p));
    }
    h=file_open(p,p,0x80000000,1,3,0); CHECK(h!=UINT32_MAX); CHECK(file_close(h));
    // Even a stage fully written BEFORE refusal must never publish on close.
    refused=0; h=file_open(p,p,0xC0000000,0,2,0); CHECK(h!=UINT32_MAX);
    CHECK(file_write(h,"NEW",3)==3); refused=1;
    CHECK(!file_close(h)); CHECK(unchanged(p));
    refused=0;
    uint32_t prof=0x100000, buffer=0x200000, row=prof+0x414+20;
    memcpy(G+prof,"ALPHA",6); M32(row)=1; M32(row+4)=0x1234; M32(row+8)=0x5678;
    M32(row+12)=2; G[row+16]=1;
    for(unsigned i=0;i<sizeof payload;++i) payload[i]=(unsigned char)(i*31);
    memcpy(G+buffer,payload,sizeof payload);
    underglow_profile_default(prof); CHECK(underglow_set(prof,1,2));
    underglow_profile_save(prof,buffer,sizeof payload);
    CHECK(publish(p,0)); CHECK(exact_payload(p));
    underglow_profile_load(prof,buffer,sizeof payload); CHECK(underglow_get(prof,1)==2);
    // Optional sidecar write/rename failure cannot invalidate a valid retail file.
    for(int fault=1;fault<=2;++fault) {
        CHECK(underglow_set(prof,1,1)); underglow_profile_save(prof,buffer,sizeof payload);
        cosmetic_fail=fault; unsigned seq=save_fail_seq;
        CHECK(publish(p,0)); CHECK(save_fail_seq==seq); CHECK(exact_payload(p)); cosmetic_fail=0;
        underglow_profile_load(prof,buffer,sizeof payload); CHECK(underglow_get(prof,1)==2);
    }
    CHECK(underglow_set(prof,1,1)); underglow_profile_save(prof,buffer,sizeof payload);
    CHECK(!publish(p,1)); CHECK(exact_payload(p));
    underglow_save_complete(1); // no pending state may escape the failed save
    underglow_profile_load(prof,buffer,sizeof payload); CHECK(underglow_get(prof,1)==2);
    printf("write-barrier checks=%u failures=%u\n",checks,failures);
    return failures?1:0;
}
'''


def main():
    scan = audit.marked('runtime/boot.c','// BEGIN offline save scan:','// END offline save scan')
    layer = audit.marked('runtime/boot.c','// BEGIN offline file layer:','// END offline file layer')
    with tempfile.TemporaryDirectory(prefix='carsave-write-') as directory:
        root=Path(directory).resolve()
        (root/'latch.c').write_text('#include "host.h"\n#include <stdlib.h>\nint refused, cosmetic_fail; int private_cars_save_allowed(void) { return !refused; }\nvoid f_00590BD0_orig(Cpu *c) { abort(); }\nvoid f_00581370_orig(Cpu *c) { abort(); }\n')
        store = '#include <unistd.h>\n#include <stdio.h>\n#include <errno.h>\nextern int cosmetic_fail;\n'
        store += 'static ssize_t cosmetic_write(int fd,const void *p,size_t n) { return write(fd,p,cosmetic_fail==1?n/2:n); }\n'
        store += 'static int cosmetic_rename(const char *a,const char *b) { if(cosmetic_fail==2) { errno=EACCES; return -1; } return rename(a,b); }\n'
        store += '#define write cosmetic_write\n#define rename cosmetic_rename\n'+(ROOT/'runtime/underglow_store.c').read_text()
        (root/'store.c').write_text(store)
        prefix=audit.FILE_PREFIX.replace('static uint8_t guest[4096], *G = guest;', 'static uint8_t guest[0x1000000]; uint8_t *G = guest;')
        prefix=prefix.replace('static const char *userdata_dir(void)', 'const char *userdata_dir(void)')
        source='#include <assert.h>\n'+prefix+scan+layer+MAIN
        for mutant in ('good','barrier','callback'):
            code=source
            if mutant=='barrier':
                code=code.replace('return !private_cars_save_allowed() && (file_is_save(path) || !strcmp(path, userdata_dir()));','return 0; /* deliberately bypass latch */')
                assert code!=source
            if mutant=='callback':
                code=code.replace('underglow_save_complete(ok);','(void)ok;')
                assert code!=source
            (root/'test.c').write_text(code)
            exe=root/mutant
            build=subprocess.run(['clang','-O1','-Wno-deprecated-declarations','-I',str(ROOT/'runtime'),'-I',str(ROOT/'recomp'),str(root/'test.c'),str(root/'latch.c'),str(root/'store.c'),str(ROOT/'runtime/underglow.c'),'-o',str(exe)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr[-2000:]
            result=subprocess.run([str(exe),str(root/(mutant+'-data'))],capture_output=True,text=True)
            print(mutant,result.stdout.strip())
            assert result.returncode==int(mutant!='good'),result.stderr[-2000:]
    return 0

if __name__=='__main__': raise SystemExit(main())
