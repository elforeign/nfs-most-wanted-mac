"""Refused-profile write barrier: good 25/25; bypass mutant fails 17/25.
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
static unsigned checks, failures;
extern int refused;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL line %u: %s\n",__LINE__,#x); } } while(0)
static void seed(const char *p) { FILE *f=fopen(p,"wb"); assert(f); fputs("ORIGINAL",f); fclose(f); }
static int unchanged(const char *p) { char b[16]={0}; FILE *f=fopen(p,"rb"); if(!f)return 0; size_t n=fread(b,1,16,f); fclose(f); return n==8&&!memcmp(b,"ORIGINAL",8); }
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
    printf("write-barrier checks=%u failures=%u\n",checks,failures);
    return failures?1:0;
}
'''


def main():
    scan = audit.marked('runtime/boot.c','// BEGIN offline save scan:','// END offline save scan')
    layer = audit.marked('runtime/boot.c','// BEGIN offline file layer:','// END offline file layer')
    with tempfile.TemporaryDirectory(prefix='carsave-write-') as directory:
        root=Path(directory).resolve()
        (root/'latch.c').write_text('int refused; int private_cars_save_allowed(void) { return !refused; }\n')
        source='#include <assert.h>\n'+audit.FILE_PREFIX+scan+layer+MAIN
        for mutant in (False,True):
            code=source
            if mutant:
                code=code.replace('return !private_cars_save_allowed() && (file_is_save(path) || !strcmp(path, userdata_dir()));','return 0; /* deliberately bypass latch */')
                assert code!=source
            (root/'test.c').write_text(code)
            exe=root/('mutant' if mutant else 'good')
            build=subprocess.run(['clang','-O1',str(root/'test.c'),str(root/'latch.c'),'-o',str(exe)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr[-2000:]
            result=subprocess.run([str(exe),str(root/('bad' if mutant else 'user'))],capture_output=True,text=True)
            print('bypassed-latch mutant' if mutant else 'good',result.stdout.strip())
            assert result.returncode==int(mutant),result.stderr[-2000:]
    return 0

if __name__=='__main__': raise SystemExit(main())
