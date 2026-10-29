"""WO3 scratch-only save/metadata transaction and restart proof (no game/Unicorn).
Good: 53/53 transaction + 8/8 restart checks. Mutant failures: omitted journal
2/17, omitted dependency comparison 1/53, omitted prepare 2/53, stale generation
1/2. The metadata record's generation/set/save hash/checksum are decoded below.
The actual boot file layer links separate strong metadata hooks, with exact
save-file bytes, write-ahead journal recovery, and fresh-process admission.
All opens use the WO2 owner-directory interposer before libc. Hard links are
refused with EPERM by that fixture; the real metadata reader object supplies
count/get and backup hooks. Exclusive-rename backups preserve 53/53 checks.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Missing-fixture proof: omitted guard exits 1 on import; this guard exits 3.
if any(not (Path(__file__).resolve().parents[1] / name).is_file() for name in ['runtime/auditfix_test.py', 'tools_snapshot/test_private_cars.py']):
    print("SKIP: optional development fixtures are absent")
    sys.exit(3)
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'recomp'))
import auditfix_test as audit
import carsaves_test as saves

MAIN = r'''
#undef fsync
#undef rename
#undef write
#include <CommonCrypto/CommonDigest.h>
#include "private_cars.h"
static unsigned checks,failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL line %u: %s\n",__LINE__,#x); } } while(0)
#define SIZE 0xF848u
static unsigned char mask[12]={1};
static void digest(unsigned char *b) { CC_MD5(b+16,SIZE-32,b+SIZE-16); }
static void keypath(const unsigned char *b,const char *suffix,char out[4096]) {
    unsigned char hash[32]; char key[65]; CC_SHA256(b,SIZE,hash);
    for(unsigned k=0;k<32;++k) snprintf(key+k*2,3,"%02x",hash[k]);
    snprintf(out,4096,"%s/.car-catalog/%s.%s",userroot,key,suffix);
}
static void readsave(const char *path,unsigned char *b) {
    int fd=open(path,O_RDONLY); assert(fd>=0); assert(pread(fd,b,SIZE,36)==SIZE); close(fd);
}
static int publish(const char *path,const unsigned char *b,int complete) {
    unsigned seq=save_fail_seq,commits=save_commit_seq;
    uint32_t h=file_open(path,path,0x40000000u,0,2,0); CHECK(h!=UINT32_MAX);
    unsigned char header[36]={0};
    CHECK(file_write(h,header,36)==36); CHECK(file_write(h,b,SIZE)==SIZE);
    int ok=file_close(h);
    if(complete) {
        M32(0x200+0x300)=1; M32(0x200+0x2f4)=0;
        save_task_fixup(0x200,0xb,seq);
        save_sidecars_complete(0x200,0xb,seq,commits);
    }
    return ok;
}
void extratrax_save_complete(int committed) { }
void padbind_save_complete(int committed) { }
int main(int argc,char **argv) {
    assert(argc==4); userroot=argv[3];
    private_cars_metadata_userdata(userroot);
    int off=!strcmp(argv[1],"off");
    int cat=private_cars_init(argv[1],!off,NULL,0);
    char path[4096]; snprintf(path,sizeof path,"%s/TEST",userroot);
    unsigned char b[SIZE],old[SIZE],out[SIZE];
    // Include the selected own-slot FORDGT as well as addition 84.
    mask[10]=2;
    if(strcmp(argv[2],"seed")) {
        readsave(path,b); memcpy(old,b,SIZE);
        int refuse=!strcmp(argv[2],"refuse");
        CHECK((private_cars_metadata_admit(b,SIZE,mask)!=NULL)==refuse);
        CHECK(!memcmp(old,b,SIZE));
        printf("restart-%s checks=%u failures=%u\n",argv[2],checks,failures);
        return failures?1:0;
    }
    assert(!cat); assert(private_cars_count()==3);
    // Retail MW bytes and metadata directory absence are exact.
    memset(b,0,SIZE); b[0x5BA1]=19; digest(b); memcpy(old,b,SIZE);
    unsigned char mw[12]={0}; char directory[4096]; snprintf(directory,sizeof directory,"%s/.car-catalog",userroot);
    CHECK(!private_cars_metadata_stage(b,SIZE,mw)); CHECK(!memcmp(old,b,SIZE));
    CHECK(access(directory,F_OK)==-1); CHECK(!private_cars_metadata_admit(b,SIZE,mw));
    CHECK(publish(path,b,1)); readsave(path,out); CHECK(!memcmp(old,out,SIZE));
    CHECK(access(directory,F_OK)==-1);
    // Unmarked additions have no recoverable generation provenance.
    CHECK(private_cars_metadata_admit(b,SIZE,mask)!=NULL);
    CHECK(!private_cars_metadata_stage(b,SIZE,mask)); CHECK(memcmp(old,b,16));
    CHECK(!private_cars_verify_save(b,SIZE)); CHECK(!private_cars_metadata_admit(b,SIZE,mask));
    char pending[4096],meta[4096]; keypath(b,"pending",pending); keypath(b,"meta",meta);
    CHECK(access(pending,F_OK)==0); CHECK(access(meta,F_OK)==-1);
    if(access(pending,F_OK)) { printf("transaction checks=%u failures=%u\n",checks,failures); return 1; }
    CHECK(publish(path,b,0)); CHECK(access(pending,F_OK)==0); CHECK(access(meta,F_OK)==-1);
    private_cars_save_complete(1); CHECK(access(meta,F_OK)==0); CHECK(access(pending,F_OK)==-1);
    memcpy(old,b,SIZE);
    // Journal for a NEW payload cannot supersede the OLD on-disk generation.
    b[100]^=1; digest(b); CHECK(!private_cars_metadata_stage(b,SIZE,mask));
    pid_t pid=fork(); assert(pid>=0);
    if(!pid) {
        uint32_t h=file_open(path,path,0x40000000u,0,2,0);
        unsigned char header[36]={0}; file_write(h,header,36); file_write(h,b,100); _exit(0);
    }
    int status=0; assert(waitpid(pid,&status,0)==pid); CHECK(WEXITSTATUS(status)==0);
    readsave(path,out); CHECK(!memcmp(old,out,SIZE)); CHECK(!private_cars_metadata_admit(out,SIZE,mask));
    // Kill after save rename, before completion: load exact durable .pending.
    pid=fork(); assert(pid>=0);
    if(!pid) _exit(publish(path,b,0)?0:2);
    assert(waitpid(pid,&status,0)==pid); CHECK(WEXITSTATUS(status)==0);
    readsave(path,out); CHECK(!memcmp(b,out,SIZE)); CHECK(!private_cars_metadata_admit(out,SIZE,mask));
    keypath(b,"pending",pending); keypath(b,"meta",meta);
    CHECK(access(pending,F_OK)==0); CHECK(access(meta,F_OK)==-1);
    CHECK(!private_cars_metadata_admit(old,SIZE,mask)); // retained older backup
    unsigned char wrong[12]={1}; CHECK(private_cars_metadata_admit(b,SIZE,wrong)!=NULL);
    out[101]^=1; digest(out); CHECK(private_cars_metadata_admit(out,SIZE,mask)!=NULL);
    // Missing metadata must block file-layer publication and leave OLD save.
    CHECK(publish(path,old,1)); // previous consistent save is the visible target
    char held[4096]; snprintf(held,sizeof held,"%s/held",userroot);
    assert(!rename(pending,held));
    CHECK(private_cars_metadata_admit(b,SIZE,mask)!=NULL);
    CHECK(!publish(path,b,1)); readsave(path,out); CHECK(!memcmp(out,old,SIZE));
    assert(!rename(held,pending));
    // Restore after read-only rejection; symlinked metadata is never followed.
    assert(!rename(pending,held)); assert(!symlink(held,pending));
    CHECK(private_cars_metadata_admit(b,SIZE,mask)!=NULL);
    assert(!unlink(pending)); assert(!rename(held,pending));
    CHECK(!private_cars_metadata_admit(b,SIZE,mask));
    CHECK(publish(path,b,0));
    // Leave an interrupted publication for fresh-process Off/stale/reinstall.
    printf("transaction checks=%u failures=%u\n",checks,failures);
    return failures?1:0;
}
'''


def tree_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    fixtures = saves.fixtures
    with tempfile.TemporaryDirectory(prefix='carsave-metadata-') as directory:
        root = Path(directory).resolve()
        src = {k: root / k / 'CARS' for k in ('C', 'P')}
        mw = root / 'MW/CARS'
        for base, names in ((src['C'], ('240SX', '300C')), (src['P'], ('FORDGT',)), (mw, ('RX7', 'CTS', 'FORDGT'))):
            for name in names:
                (base/name).mkdir(parents=True)
                for filename in fixtures.pc.PACKS:
                    (base/name/filename).write_bytes((name+filename).encode())
        catalog = root/'catalog'
        data = fixtures.pc.build(catalog,mw,src,adapter=fixtures.adapter)
        approvals = {r['source']: dict(evidence='synthetic metadata contract', **{f:r['outputs'][f]['sha256'] for f in fixtures.pc.PACKS})
                     for r in data['cars'] if r['status']=='converted'}
        approval = root/'approvals.json'; approval.write_bytes(fixtures.pc.encoded(approvals))
        fixtures.pc.build(catalog,mw,src,approvals=approval,adapter=fixtures.adapter)
        # Different valid generation, same identities/packs: presence is insufficient.
        stale = root/'stale'
        fixtures.pc.build(stale,mw,src,approvals=approval,adapter=fixtures.adapter)
        scan = audit.marked('runtime/boot.c','// BEGIN offline save scan:','// END offline save scan')
        layer = audit.marked('runtime/boot.c','// BEGIN offline file layer:','// END offline file layer')
        prefix = '#include <assert.h>\n'+audit.FILE_PREFIX
        source = saves.GUARD+prefix+scan+layer+MAIN
        private = (ROOT/'runtime/private_cars.c').read_text()
        for variant in ('good','generation','journal','dependencies','prepare'):
            actual, file_source = private, source
            if variant=='generation':
                actual=actual.replace('!generation[0] || memcmp(m->generation,generation,sizeof generation)', '!generation[0]')
            elif variant=='journal':
                actual=actual.replace('if(metadata_write(&m))', 'if(0) /* deliberately omit durable journal */')
            elif variant=='dependencies':
                actual=actual.replace('if(used && memcmp(m.used,used,sizeof m.used))','if(0) /* deliberately ignore used cars */')
            elif variant=='prepare':
                file_source=file_source.replace('!private_cars_save_prepare(s->temp)', '0 /* deliberately publish without metadata */')
            if variant!='good': assert actual!=private or file_source!=source
            (root/'metadata.c').write_text(saves.GUARD+actual)
            (root/'test.c').write_text(file_source)
            exe=root/variant
            build=subprocess.run(['clang','-O1','-g','-fsanitize=address','-I'+str(ROOT/'runtime'),
                                  str(root/'test.c'),str(root/'metadata.c'),'-o',str(exe)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr[-3500:]
            user=root/('user-'+variant); user.mkdir()
            seed=subprocess.run([str(exe),str(catalog),'seed',str(user)],capture_output=True,text=True)
            print(variant,seed.stdout.strip())
            if variant in ('journal','dependencies','prepare'):
                assert seed.returncode==1,seed.stderr[-2000:]
                continue
            assert seed.returncode==0,seed.stderr[-2000:]
            payload=(user/'TEST').read_bytes()[36:]
            key=hashlib.sha256(payload).hexdigest()
            record=(user/'.car-catalog'/f'{key}.pending').read_bytes()
            catalog_data=json.loads((catalog/json.loads((catalog/'current.json').read_bytes())['generation']/'catalog.json').read_bytes())
            assert len(record)==245 and record[:8]==b'NFSCAR3\x01'
            assert record[8:72].rstrip(b'\0').decode()==catalog_data['generation']
            assert record[72:137].rstrip(b'\0').decode()==catalog_data['compatibility']
            used=bytes([1]+[0]*9+[2,0]); assert record[137:149]==used
            signature=hashlib.sha256(catalog_data['compatibility'].encode()+b'\0'+used)
            for k,car in enumerate(catalog_data['cars']):
                if used[k//8] & (1 << (k%8)):
                    signature.update(bytes([k]))
                    for field in ('name','donor','source','price_donor'):
                        signature.update(car[field].encode()+b'\0')
            assert record[149:181]==signature.digest()
            assert record[181:213]==hashlib.sha256(payload).digest()
            assert record[213:245]==hashlib.sha256(record[:213]).digest()
            for label,path,expect in [('stale',stale,'refuse'),('Off','off','refuse'),('missing',root/'absent','refuse'),('reinstalled',catalog,'accept')]:
                before=tree_hash(user)
                result=subprocess.run([str(exe),str(path),expect,str(user)],capture_output=True,text=True)
                print(variant,label,result.stdout.strip())
                expected=1 if variant=='generation' and label=='stale' else 0
                assert result.returncode==expected,result.stderr[-2000:]
                assert tree_hash(user)==before, (variant,label,'profile or metadata changed during load')
    return 0

if __name__=='__main__': raise SystemExit(main())
