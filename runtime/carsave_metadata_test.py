"""WO3 scratch-only save/metadata transaction and restart proof (no game/Unicorn).
Good: 53/53 transaction + 8/8 restart checks. Mutant failures: omitted journal
2/17, omitted dependency comparison 1/53, omitted prepare 2/53. Generation
admission: a marker from another generation with the same compatibility signature
is admitted (unmodified exact-generation code: stale control fails 1/2; restored by
the 'generation' mutant), a forged marker with another signature is refused (the
'compat' mutant, signature check removed, admits it: 1/2). The metadata record's generation/set/save hash/checksum are decoded below.
The actual boot file layer links separate strong metadata hooks, with exact
save-file bytes, write-ahead journal recovery, and fresh-process admission.
Approved traffic leaves all 90 player rows identical; its non-NULL getter is
required before admitting the existing save without writing any bytes (3/3).
Pinned 1ddd0da2 old loader fails 1/3; exact-generation mutant fails 1/3 for that
catalog, alongside its original stale-generation control.
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
import shutil
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
        if(!strcmp(argv[2],"traffic")) CHECK(private_cars_traffic_get()!=NULL);
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



# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_LOAD = r'''static int load(const char *root,const char *const *originals,size_t noriginals) {
    int base=-1,gen=-1,fd=-1; Json current={0},catalog={0}; int rc=-1;
    char path[4096],hash[65],gen_name[64],status[32],approval[32],role[32]; uint64_t size,schema;
#define CHECK(test,why) do { if (!(test)) { bad(why); goto done; } } while (0)
    base=root_open(root); CHECK(base>=0,"private root unavailable or symlink");
    fd=open_relative(base,"current.json",0); CHECK(fd>=0,"current.json unavailable or symlink");
    CHECK(json_read(fd,&current)==0,"invalid current.json"); close(fd); fd=-1;
    CHECK(number(&current,field(&current,0,"schema"),&schema)==0 && schema==1,"current schema");
    CHECK(string(&current,field(&current,0,"generation"),gen_name,sizeof gen_name)==0 &&
          !strncmp(gen_name,"g-",2) && hex(gen_name+2,32),"generation path escape/name");
    CHECK(spec(&current,field(&current,0,"catalog"),path,sizeof path,hash,&size)==0 && !strcmp(path,"catalog.json"),"catalog path/spec");
    gen=open_relative(base,gen_name,1); CHECK(gen>=0,"generation unavailable or symlink");
    fd=checked_open(gen,path,hash,size); CHECK(fd>=0,"catalog integrity/symlink");
    CHECK(json_read(fd,&catalog)==0,"invalid catalog JSON"); close(fd); fd=-1;
    CHECK(json_hash_matches(&catalog,hash,size),"catalog changed after hash verification");
    CHECK(number(&catalog,field(&catalog,0,"schema"),&schema)==0 && schema==1,"catalog schema");
    CHECK(equal(&catalog,field(&catalog,0,"generation"),gen_name),"generation mismatch");
    CHECK(string(&catalog,field(&catalog,0,"compatibility"),compatibility,sizeof compatibility)==0 && !strcmp(compatibility,expected_compatibility),"compatibility signature");
    int array=field(&catalog,0,"cars"); CHECK(array>=0 && catalog.t[array].type=='[',"cars array");
    for (int row=array+1;row<catalog.t[array].next;row=catalog.t[row].next) {
        CHECK(ncar<NCARS,"catalog too many cars");
        PrivateCar *c=&cars[ncar]; uint64_t id;
        CHECK(number(&catalog,field(&catalog,row,"id"),&id)==0 && id<=159 &&
              string(&catalog,field(&catalog,row,"role"),role,sizeof role)==0,"car identity");
        c->id=(unsigned)id; c->swap=!strcmp(role,"swap");
        CHECK((ncar<76 && !strcmp(role,"addition") && id==84+ncar) ||
              (ncar>=76 && c->swap && id==0),"stable addition order/role");
        CHECK(string(&catalog,field(&catalog,row,"name"),c->name,sizeof c->name)==0 && word(c->name) &&
              string(&catalog,field(&catalog,row,"donor"),c->donor,sizeof c->donor)==0 && word(c->donor) &&
              string(&catalog,field(&catalog,row,"price_donor"),c->price_donor,sizeof c->price_donor)==0 && word(c->price_donor) &&
              string(&catalog,field(&catalog,row,"source"),c->source,sizeof c->source)==0 && relative(c->source),"car name/donor/source");
        CHECK(!strcmp(c->name,identities[ncar][0]) && !strcmp(c->donor,identities[ncar][1]) &&
              !strcmp(c->source,identities[ncar][2]) && !strcmp(c->price_donor,identities[ncar][3]),"frozen identity/donor changed");
        CHECK(!c->swap || !strcmp(c->name,c->donor),"swap donor identity");
        for (size_t k=0;k<ncar;++k) CHECK(strcmp(cars[k].name,c->name),"duplicate identity");
        CHECK(string(&catalog,field(&catalog,row,"status"),status,sizeof status)==0 &&
              (!strcmp(status,"pending")||!strcmp(status,"converted")||!strcmp(status,"failed")),"conversion status");
        CHECK(string(&catalog,field(&catalog,row,"approval"),approval,sizeof approval)==0 &&
              (!strcmp(approval,"pending")||!strcmp(approval,"approved")),"approval state");
        int approved=!strcmp(approval,"approved");
        CHECK(!approved || !strcmp(status,"converted"),"approval without conversion");
        int evidence=field(&catalog,row,"evidence");
        CHECK(!approved || (evidence>=0 && catalog.t[evidence].type=='"' && catalog.t[evidence].end-catalog.t[evidence].begin>2),"approval without evidence");
        c->selected=approved;
        if (c->swap) for (size_t k=0;k<noriginals;++k) if (originals[k] && !strcmp(originals[k],c->name)) c->selected=0;
        int outputs=field(&catalog,row,"outputs"); CHECK(outputs>=0 && catalog.t[outputs].type=='{',"outputs object");
        int pair=0;
        for (int key=outputs+1;key<catalog.t[outputs].next;key=catalog.t[key+1].next) {
            char filename[128],expected[256];
            CHECK(string(&catalog,key,filename,sizeof filename)==0 && relative(filename) && !strchr(filename,'/'),"output filename");
            CHECK(spec(&catalog,key+1,path,sizeof path,hash,&size)==0,"output path escape/spec");
            snprintf(expected,sizeof expected,"%ss/%s/%s",c->swap?"swap":"addition",c->name,filename);
            CHECK(!strcmp(expected,path),"output identity/path mismatch");
            int which=!strcmp(filename,"GEOMETRY.BIN")?1:!strcmp(filename,"TEXTURES.BIN")?2:0;
            pair|=which;
            if (c->selected) {
                fd=checked_open(gen,path,hash,size);
                if (fd<0 && c->swap) {
                    c->selected=0; c->geometry[0]=c->textures[0]=c->wheel_fit[0]=0;
                    size_t used=strlen(problem);
                    snprintf(problem+used,sizeof problem-used,"%sswap %s pair unavailable; using original MW model",used?"; ":"",c->name);
                } else {
                    CHECK(fd>=0,"selected output integrity/symlink");
                    if (!strcmp(filename,"PHYSICS.BIN") && car_physics_enabled()) {
                        CarPhysics physics;
                        CHECK(car_physics_read(fd,c->name,c->donor,&physics)==0,"invalid physics metadata");
                    }
                    close(fd); fd=-1;
                }
                if (c->selected && !strcmp(filename,"WHEELFIT.BIN"))
                    CHECK(snprintf(c->wheel_fit,sizeof c->wheel_fit,"%s/%s/%s",root,gen_name,path)<(int)sizeof c->wheel_fit,"wheel output path too long");
                if (c->selected && !strcmp(filename,"SOLIDMAP.BIN"))
                    CHECK(snprintf(c->solid_map,sizeof c->solid_map,"%s/%s/%s",root,gen_name,path)<(int)sizeof c->solid_map,"solid map path too long");
                if (c->selected && !strcmp(filename,"PHYSICS.BIN"))
                    CHECK(snprintf(c->physics,sizeof c->physics,"%s/%s/%s",root,gen_name,path)<(int)sizeof c->physics,"physics path too long");
                if (c->selected && !strcmp(filename,"PAINT.BIN"))
                    CHECK(snprintf(c->paint,sizeof c->paint,"%s/%s/%s",root,gen_name,path)<(int)sizeof c->paint,"paint path too long");
                if (which && c->selected) {
                    char *dest=which==1?c->geometry:c->textures;
                    CHECK(snprintf(dest,4096,"%s/%s/%s",root,gen_name,path)<4096,"output path too long");
                }
            }
        }
        CHECK((!strcmp(status,"converted") && pair==3) ||
              (strcmp(status,"converted") && catalog.t[outputs].next==outputs+1),"incomplete pack pair");
        nselected+=(size_t)c->selected; ++ncar;
    }
    CHECK(ncar==NCARS,"catalog count must preserve 76 additions and 14 swaps");
    strcpy(generation,gen_name); rc=0;
done:
    if (fd>=0) close(fd);
    if (gen>=0) close(gen);
    if (base>=0) close(base);
    json_free(&current); json_free(&catalog);
    if (rc) { ncar=nselected=0; generation[0]=compatibility[0]=0; memset(cars,0,sizeof cars); }
    return rc;
#undef CHECK
}'''
assert hashlib.sha256(OLD_LOAD.encode()).hexdigest() == 'df59d00dff2c9f94ded79c426d80fa246d6dd341a2291285fd130c4e8a150972', 'pinned baseline changed'

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
        # Different valid generation, same identities/packs/signature: admitted.
        stale = root/'stale'
        fixtures.pc.build(stale,mw,src,approvals=approval,adapter=fixtures.adapter)
        # A verified, approved traffic pilot never enters the 90-car save mask,
        # part signature or selected player inventory. Preserve an old save
        # byte-for-byte while admitting it against this optional catalog member.
        traffic = root/'traffic'
        shutil.copytree(stale,traffic)
        pointer=json.loads((traffic/'current.json').read_bytes())
        gen=traffic/pointer['generation']
        traffic_data=json.loads((gen/'catalog.json').read_bytes())
        row=dict(fixtures.pc.TRAFFIC,status='converted',approval='approved',
                 evidence='synthetic save-independence evidence',inputs_sha256='1'*64,outputs={})
        for filename in fixtures.pc.TRAFFIC_FILES:
            path=gen/'traffic/TRAFFICSEDAN'/filename
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(('synthetic save-independent traffic '+filename).encode())
            row['outputs'][filename]=dict(path=path.relative_to(gen).as_posix(),**fixtures.pc.fingerprint(path))
        row['approval_record']=dict(evidence=row['evidence'],inputs_sha256=row['inputs_sha256'],
                                    conversion_revision=2,**{f:s['sha256'] for f,s in row['outputs'].items()})
        traffic_data['traffic']=row
        (gen/'catalog.json').write_bytes(fixtures.pc.encoded(traffic_data))
        pointer['catalog'].update(fixtures.pc.fingerprint(gen/'catalog.json'))
        (traffic/'current.json').write_bytes(fixtures.pc.encoded(pointer))
        fixtures.pc.verify(traffic)
        assert traffic_data['cars']==fixtures.pc.verify(stale)['cars']
        scan = audit.marked('runtime/boot.c','// BEGIN offline save scan:','// END offline save scan')
        layer = audit.marked('runtime/boot.c','// BEGIN offline file layer:','// END offline file layer')
        prefix = '#include <assert.h>\n'+audit.FILE_PREFIX
        source = saves.GUARD+prefix+scan+layer+MAIN
        private = (ROOT/'runtime/private_cars.c').read_text()
        for variant in ('good','old-loader','generation','compat','journal','dependencies','prepare'):
            actual, file_source = private, source
            if variant=='old-loader':
                start=actual.index('static int load(')
                end=actual.index('\n}',start)+2
                actual=actual[:start]+OLD_LOAD+actual[end:]
            elif variant=='generation':
                actual=actual.replace('if (!generation[0])\n        return "Required','if (!generation[0] || memcmp(m->generation,generation,sizeof generation))\n        return "Required')
            elif variant=='compat':
                actual=actual.replace('memcmp(m->compatibility,compatibility,sizeof compatibility) || ','0 /* deliberately skip signature */ || ')
            elif variant=='journal':
                actual=actual.replace('if(metadata_write(&m))', 'if(0) /* deliberately omit durable journal */')
            elif variant=='dependencies':
                actual=actual.replace('if(used && memcmp(m.used,used,sizeof m.used))','if(0) /* deliberately ignore used cars */')
            elif variant=='prepare':
                file_source=file_source.replace('!SAVE_TIME("provenance.prepare", private_cars_save_prepare(s->temp))', '0 /* deliberately publish without metadata */')
            if variant!='good': assert actual!=private or file_source!=source
            (root/'metadata.c').write_text(saves.GUARD+actual)
            (root/'test.c').write_text(file_source)
            exe=root/variant
            build=subprocess.run(['clang',str(ROOT/'runtime/car_physics.c'),'-O1','-g','-fsanitize=address','-I'+str(ROOT/'runtime'),
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
            for label,path,expect in [('stale',stale,'accept'),('traffic',traffic,'traffic'),('Off','off','refuse'),('missing',root/'absent','refuse'),('reinstalled',catalog,'accept')]:
                before=tree_hash(user)
                result=subprocess.run([str(exe),str(path),expect,str(user)],capture_output=True,text=True)
                print(variant,label,result.stdout.strip())
                expected=1 if (variant=='generation' and label in ('stale','traffic')) or (variant=='old-loader' and label=='traffic') else 0
                assert result.returncode==expected,result.stderr[-2000:]
                assert tree_hash(user)==before, (variant,label,'profile or metadata changed during load')
            # Forged marker: same save, different compatibility signature -> refused.
            forged=root/('forged-'+variant); (forged/'.car-catalog').mkdir(parents=True)
            rec=bytearray(record); rec[72]=ord('0') if rec[72]!=ord('0') else ord('1')
            marker=b'NFSCAR3\x01'+hashlib.sha256(bytes(rec[:181])).digest()[:8]
            body=bytearray(payload); body[:16]=marker
            rec[181:213]=hashlib.sha256(bytes(body)).digest(); rec[213:245]=hashlib.sha256(bytes(rec[:213])).digest()
            (forged/'TEST').write_bytes((user/'TEST').read_bytes()[:36]+bytes(body))
            (forged/'.car-catalog'/(hashlib.sha256(bytes(body)).hexdigest()+'.pending')).write_bytes(bytes(rec))
            result=subprocess.run([str(exe),str(catalog),'refuse',str(forged)],capture_output=True,text=True)
            print(variant,'forged signature',result.stdout.strip())
            assert result.returncode==(1 if variant=='compat' else 0),result.stderr[-2000:]
    return 0

if __name__=='__main__': raise SystemExit(main())
