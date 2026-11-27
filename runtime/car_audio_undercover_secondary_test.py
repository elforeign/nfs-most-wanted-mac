#!/usr/bin/env python3
"""Undercover secondary players and ordered tiers, without a game or device.

Original profile control: 0/19 banks; source inventory 336 live / 144 null.
Fixed: 14 cars, 106 tier attachments, 212 layers, 312 selector rows (144 empty),
5,077,075 PCM frames within one sample unit, 848 balanced tokens. Nine wrong
selector/null/loop/tier/system/hook/release/CarID/bank controls exit -6.
The native proof checks all ordered authored tiers, both constructors and
CSIS token ownership through unload; a half-volume PCM control also fails.
car_audio_secondary.h supplies the isolated native-controller token hooks.
Missing private inputs exit 3 after the portable translator fixture checks.
"""
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio, car_audio_undercover as uc, car_audio_pack, uc_attrib
from setup_kit.car_convert import FitAttributes, hash32
from setup_kit.input_paths import input_path


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def expectations(attributes):
    """Typed original fields, independent of the package writer's graph."""
    n = FitAttributes(attributes, vaults=uc_attrib.vaults(attributes))
    a = object.__new__(car_audio.AudioAttributes); a.defs, a.collections = n.defs, n.collections
    rows = json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_text())
    result = {}
    for row in rows:
        if row['origin']['game'] != 'undercover': continue
        car = row['origin']['folder']; own = hash32(uc_attrib.STOCK_ALIASES.get(car, car).lower())
        tiers = a.field(hash32('pvehicle'), own, hash32('Audio'))['values']
        plans = []
        for ref in tiers:
            cls, key = int(ref['class'], 16), int(ref['key'], 16)
            assert cls == car_audio.CAR
            paths, banks = [], []
            for field in (0x9aed4ffe, 0xb3186761):
                ng = a.field(cls, key, field)['values'][0]; nc, nk = int(ng['class'], 16), int(ng['key'], 16)
                assert nc == 0x6d483f8f
                paths.append('SOUND/ENGINE/' + a.field(nc, nk, 0x0eb1c9d2)['values'][0]['text'].upper())
                names = a.field(nc, nk, 0xa7468316)['values']
                banks.append(['SOUND/' + ('IG_GLOBAL/' if v['text'].upper() == 'CAR_WHINE.ABK' else 'ENGINE/')
                              + v['text'].upper() for v in names])
            assert banks[0][1] == banks[1][1] == 'SOUND/IG_GLOBAL/CAR_WHINE.ABK'
            gear = 'SOUND/SHIFTING/' + a.field(cls, key, 0x364efa6a)['values'][0]['text'].upper()
            plans.append(paths + [banks[0][0], banks[1][0], banks[0][1], gear])
        assert len(plans) == (1 if car == 'FOR_SHL_TER_08' else 4)
        result[car] = plans
    assert len(result) == 14 and result['LAM_GAL_SUP_08'] == result['LAM_GAL_560_09']
    return result


def source_trace(folder, iso, attributes):
    """Check the independently decoded console selector/controller fragments.

    8228298C copies car +264 to CSTATE +98; 822847AC constructs that
    attribute from caraudio +18, the exhaust NG field. 822A4F50 loads its
    first SweetBank; 822A5C1C selects event 0/1. 82284A50 clamps the requested
    Audio index to count-1 before querying 9C256C4C, without Carbon's -1 tier
    mapping. The interpreter vector also binds pulse/timer/latch to the pinned
    handlers: read-and-clear word 0; elapsed@0/pulse@4/reset@8/delay@12;
    and u16 input offset/u8 count/held@4/values@8. Source bytes are read from
    the disc, never embedded in the test.
    """
    attrib=FitAttributes(attributes,vaults=uc_attrib.vaults(attributes))
    assert attrib.defs[car_audio.CAR][0xb3186761][1]==0x18
    assert attrib.defs[car_audio.CAR][0x9aed4ffe][1]==0x30
    from setup_kit import xdvdfs
    with uc.Disc(iso) as disc:
        entries=[e for name,e in disc.files.items() if name.upper().endswith('/DEFAULT.XEX') or name.upper()=='DEFAULT.XEX']
        assert len(entries)==1 and entries[0].size<32<<20
        raw=xdvdfs.read_exact(disc.stream,entries[0].offset,entries[0].size)
    path=folder/'source.xex';path.write_bytes(raw);reader=folder/'xex_read'
    built=subprocess.run(['clang','-O2',str(ROOT/'tools_snapshot/xex_read.c'),'-o',str(reader)],capture_output=True,text=True,timeout=30)
    assert built.returncode==0,built.stderr[-2000:]
    fragments=(
        (0x8228298c,24,'0d6f0bbb13a5b603d7ee4836021b3dea0b397640a86a415e6b3cc0889bc81f39'),
        (0x822847ac,40,'dfbca6f380a93a260607e88254daf12a7d41447c2e32a32e3452a2cb9783755f'),
        (0x822a4f50,84,'a4591284eebdcad03873ee54fe15335ce8182604c3d050928066dc194d5ce3d7'),
        (0x822a5c1c,36,'d9598f745635a9d6afa8090572a66928bc4046749d1635a2dff5dad17ec67eb4'),
        (0x82284a50,84,'6fcf7a4c0559b4c58e63ba85b111ecdaf5950c3d9f599af57cdae1300859ea69'),
        (0x822845bc,32,'8d6208c8bd0e159407984d2f55a71e949eed9b96c22969867288d4f9f4c2d79a'),
        (0x829b9930,20,'c5620f01e8b6cf26036f11727ffe800da633529904d084fb8af42a8c544ec07e'),
        (0x829ba160,128,'9584e7ebf87ab94898463d6ea9bcf45323d9935e7c2df8a7c5e6f43baae7a0a0'),
        (0x829ba1e0,84,'e63cfa9b7105084b09a488145ce00cb3f4fe97a6a34f5c9c8945aa742666037c'),
        (0x830dc8fc,4,'cda8e51eae518a8686be90296c0e00b11f20c6fc06cdf0752ffd7a48a95de4d0'),
        (0x830dc91c,8,'81e89ca661b4d58550b726994f1f3fc151a1274fd7502c5b10491cc901050f6c'))
    for address,size,sha in fragments:
        read=subprocess.run([str(reader),str(path),hex(address),str(size),'0x45410876',
            'befa136b20e47fc62b8129ccb32b97e6163ddb0bff52760d532788fc3e925c71'],capture_output=True,timeout=30)
        assert read.returncode==0 and len(read.stdout)==size,read.stderr[-1000:]
        assert hashlib.sha256(read.stdout).hexdigest()==sha
        if address==0x822847ac:assert struct.unpack_from('>I',read.stdout,12)[0]&65535==0x18
    print('source controller/selector fragments:',len(fragments),'checked')


MAIN = r'''
static uint32_t source_u32(const uint8_t *p) { return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3]; }
static uint32_t hook(Cpu *c,void (*fn)(Cpu *),uint32_t object,unsigned n,const uint32_t *args) {
    Cpu saved=*c; c->ecx=object;
    for(unsigned i=n;i;i--) { c->esp-=4; M32(c->esp)=args[i-1]; }
    c->esp-=4; M32(c->esp)=0xfeedf00d; fn(c); uint32_t result=c->eax; *c=saved; return result;
}
static unsigned live_slots,null_slots,selector_rows,empty_rows,program_ticks,token_checks,decoded_frames,max_pcm_error;
static char oracle_root[1024];
static char seen[32][65]; static unsigned seen_count;
static void bank_proof(Resource *r) {
    if(!r->bank.source) {
        int code=plan(r);if(code)fprintf(stderr,"secondary plan failed size=%u translated=%u plan=%u\n",r->size,r->translated_size,r->plan_size);
        assert(!code);code=prepare_samples(r);if(code) {
            X360Abk parsed;char why[256];int ok=x360abk_parse(r->source,r->size,&parsed,why,sizeof why);
            fprintf(stderr,"secondary PCM failed size=%u parse=%d %s\n",r->size,ok,why);
            if(ok)for(unsigned j=0;j<parsed.count;j++)if(parsed.s[j].hdr) {
                int16_t *pcm=NULL;unsigned frames;
                if(!x360abk_decode(r->source,parsed.s+j,&pcm,&frames,why,sizeof why))fprintf(stderr,"decode %u: %s\n",j,why);
                free(pcm);
            }
        }assert(!code);
    }
    char id[65];hex(r->digest,id);
    for(unsigned i=0;i<seen_count;i++) if(!strcmp(seen[i],id)) return;
    assert(seen_count<32);strcpy(seen[seen_count++],id);
    const uint8_t *raw=r->source; unsigned bank=source_u32(raw+32),count=source_u32(raw+bank+8);
    assert(count==r->sample_count);
    char path[1200];snprintf(path,sizeof path,"%s/%s.pcm",oracle_root,id);
    FILE *oracle=fopen(path,"rb");assert(oracle);unsigned expected_count;
    assert(fread(&expected_count,4,1,oracle)==1 && expected_count==count);
    for(unsigned i=0;i<count;i++) {
        unsigned offset=source_u32(raw+bank+12+4*i);const CarAudioPCM *s=r->samples+i;
        unsigned expected_frames;assert(fread(&expected_frames,4,1,oracle)==1);
        if(offset==UINT32_MAX) { assert(!s->pcm && !s->frames && !expected_frames); ++null_slots;continue; }
        unsigned rate=source_u32(raw+bank+offset)&0x3ffff,word=source_u32(raw+bank+offset+4);
        assert(s->pcm && s->rate==rate && s->frames==(word&0x1fffffff));
        assert(s->loop==!!(word&0x20000000));
        if(s->loop)assert(s->loop_start==source_u32(raw+bank+offset+8));
        assert(expected_frames==s->frames);int16_t *expected=malloc((size_t)s->frames*2);assert(expected);
        assert(fread(expected,2,s->frames,oracle)==s->frames);
        for(unsigned k=0;k<s->frames;k++) {
            unsigned error=(unsigned)abs((int)(s->pcm[k]*32768)-expected[k]);
            assert(error<=1);if(error>max_pcm_error)max_pcm_error=error;
        }
        free(expected);decoded_frames+=s->frames;
        int nonzero=0;for(unsigned k=0;k<s->frames;k++) nonzero|=s->pcm[k]!=0;
        assert(nonzero);++live_slots;
    }
    assert(fgetc(oracle)==EOF && !ferror(oracle));fclose(oracle);
    /* Find the original selected export by its source CSIS relocation, then
     * read its original BE selector pointer. No translated indices supply
     * these expectations, including the holes between live slot identities. */
    const CarAudioRuntimeModule *m=r->modules;unsigned f3=source_u32(raw+56),descriptor=0;
    for(unsigned i=0;i<source_u32(raw+f3);i++) {
        unsigned row=f3+4+12*i,at=source_u32(raw+row+4);
        if(!strcmp((const char *)raw+at+4,(const char *)m->interface.id+4)) descriptor=source_u32(raw+row)-4;
    }
    assert(descriptor && raw[descriptor+36]==1);
    unsigned state=source_u32(raw+descriptor+44),player=source_u32(raw+descriptor+60);
    unsigned table=source_u32(raw+state+player+4),rows=source_u32(raw+table);
    const CarAudioRuntimePlayerPlan *p=m->players;
    assert(m->player_count==1 && rd(r->bank.source+p->selector)==rows);
    for(unsigned i=0;i<rows;i++) {
        uint32_t first=source_u32(raw+table+4+12*i),index=first>>16;
        const uint8_t *row=r->bank.source+p->selector+4+12*i;
        assert(rd(row)==(first==0xffff0000?65535:((first&65535)<<8)|index));
        uint8_t *copy=malloc(p->size);assert(copy);memcpy(copy,r->bank.source+m->state+p->offset,p->size);
        memcpy(copy+20,&i,4);uint32_t command=1;memcpy(copy+24,&command,4);
        CarAudioPlayer player_state={0};
        assert(!car_audio_player_attach(&player_state,copy,p->size,r->bank.source+p->selector,
            4+12*rows,r->bank.identity,car_audio_backend_callbacks(),&backend));
        int status=car_audio_player_update(&player_state);
        if(first==0xffff0000) { assert(!status && !player_state.owned && !player_state.last_error);++empty_rows; }
        else {
            assert(status==1 && player_state.owned);CarAudioVoice *voice=NULL;
            for(unsigned k=0;k<512;k++)if(voices[k].owned && voices[k].handle==player_state.voice)voice=voices+k;
            assert(voice && voice->sample.pcm==r->samples[index].pcm);
            assert(voice->sample.loop_start==r->samples[index].loop_start);
            assert(fabsf(voice->priority-((first>>8)&255)*.01f)<1e-6f);
        }
        car_audio_player_detach(&player_state);free(copy);++selector_rows;
    }
}
static uint32_t current_handle,current_token,current_members;
static unsigned keep_token;
void fixture_shift_update(Cpu *c) {
    uint32_t args[]={current_handle,current_members,0x2f000};
    hook(c,f_00831A8A,0,3,args);current_token=M32(0x2f000);
    c->eax=0;c->esp+=8;
}
static void event(Cpu *c,Engine *e,unsigned kind,unsigned selector) {
    current_handle=kind==0?0x9147ec:kind==1?0x91488c:0x914874;
    current_members=0x2e000;memset(G+current_members,0,40);
    int32_t values[9]={0};
    if(kind==0) { values[0]=(int32_t)selector;values[1]=11;values[2]=7000;values[3]=20000;values[4]=16384; }
    if(kind==1) { values[1]=7000;values[2]=20000;values[3]=16384;values[6]=25000;values[8]=32767; }
    if(kind==2) { values[0]=(int32_t)selector;values[1]=20000;values[2]=4096;values[3]=16384;values[5]=1; }
    memcpy(G+current_members,values,sizeof values);
    uint32_t sfx=0x2c000;M32(sfx+0x14)=M32(e->object+0x14);
    uint32_t time=0;hook(c,f_004F33B0,sfx,1,&time);
    Secondary *s=secondary_token(current_token);assert(s && s->owner==e && s->instance.bank);
    unsigned starts=0;
    for(unsigned tick=0;tick<40 && s->instance.bank;tick++) {
        uint32_t args[]={current_members};hook(c,f_0083179C,s->token,1,args);
        assert(!secondary_tick(c,e));++program_ticks;
        if(s->instance.bank) {
            const CarAudioRuntimePlayerPlan *p=s->instance.module->players;
            unsigned index=M32(s->state+p->offset+20);
            assert(index==(kind==1?0:selector));
            unsigned table=p->selector,rows=rd(s->instance.bank->source+table);
            if(rows && s->instance.players[0].owned) {
                if(index>=rows)index=rows-1;
                unsigned sample=rd(s->instance.bank->source+table+4+12*index)&65535;
                Resource *r=e->package->resource+e->tier->secondary[kind==0?1:kind==1?2:3];
                CarAudioVoice *voice=NULL;
                for(unsigned k=0;k<512;k++)if(voices[k].owned && voices[k].handle==s->instance.players[0].voice)voice=voices+k;
                assert(voice && voice->sample.pcm==r->samples[sample].pcm);
                if(voice->volume>0)++starts;
            }
        }
        for(unsigned k=0;k<4;k++)pump(c);
    }
    if(!starts)fprintf(stderr,"silent event kind=%u selector=%u\n",kind,selector);
    assert(starts);
    uint32_t out=0x2f004;hook(c,f_008311AA,s->token,1,&out);
    assert(M32(out)==(s->instance.bank?2u:1u));++token_checks;
    if(!keep_token) { hook(c,f_00831782,s->token,0,NULL);assert(!secondary_token(current_token)); }
}
int main(int argc,char **argv) {
    assert(argc==5);G=calloc(1,0x2000000);assert(G);
    assert(strlen(argv[1])<sizeof oracle_root);strcpy(oracle_root,argv[1]);char *slash=strrchr(oracle_root,'/');assert(slash);*slash=0;
    M32(0x9c21f4)=0x80000;M16(0x9c218c)=128;M8(0x9c204a)=128;
    for(unsigned i=0;i<2;i++) {
        FILE *f=fopen(argv[3+i],"rb");assert(f);fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
        stock_size[i]=(uint32_t)n;stock_source[i]=malloc((size_t)n);assert(stock_source[i]);
        assert(fread(stock_source[i],1,(size_t)n,f)==(size_t)n);fclose(f);
    }
    FILE *mem=fopen(argv[2],"rb");assert(mem);uint32_t spans;assert(fread(&spans,4,1,mem)==1);
    while(spans--) {uint32_t at,n;assert(fread(&at,4,1,mem)==1 && fread(&n,4,1,mem)==1);assert(at+n<0x1000000);assert(fread(G+at,1,n,mem)==n);}fclose(mem);
    MF32(0x9c1b54)=10;M32(0x9c2528)=0x825310;
    FILE *manifest=fopen(argv[1],"r");assert(manifest);char car[16],path[1024],expected[4][6][65];
    unsigned cars=0,layers=0,tiers=0,rejected=0,authored;
    while(fscanf(manifest,"%15s %1023s %u",car,path,&authored)==3) {
        assert(authored==1 || authored==4);
        for(unsigned t=0;t<authored;t++)for(unsigned k=0;k<6;k++)assert(fscanf(manifest,"%64s",expected[t][k])==1);
        for(unsigned cycle=0;cycle<2;cycle++)for(unsigned tier=0;tier<authored;tier++) {
            assert(allocated==freed && packets_created==packets_destroyed);allocated=freed=0;heap=0x1000000;
            if(car_audio_load(77,1,car,path)!=1) {++rejected;continue;}
            char forced[2]={(char)('0'+tier),0};setenv("NFS_TEST_UPGRADE_INTERVAL",forced,1);
            setup(77);MF32(0x20000+0x3c)=0;
            Cpu c={0};c.esp=0x1f00000;c.cw=0x37f;c.ecx=0x20000;f_004EE3E0_orig(&c);c.esp=0x1f00000;
            if(!cycle)f_004EE1C0(&c);else f_004DC360(&c);MF32(0x20000+0x3c)=1;
            Engine *e=engine(0x20000);assert(e && e->replaced && e->package->version==7);Package *p=e->package;
            assert(e->tier==p->tiers+tier);++tiers;
            for(int level=-1;level<=5;level++)assert(tier_for_level(p,level)==(unsigned)(level<0?0:(unsigned)level>=authored?authored-1:(unsigned)level));
            for(unsigned k=0;k<6;k++) {
                unsigned index=k<2?e->tier->layer[k][0]:e->tier->secondary[k-2];Resource *r=p->resource+index;
                char id[65];hex(r->digest,id);assert(!strcmp(id,expected[tier][k]));if(k<2)++layers;
            }
            for(unsigned i=0;i<p->count;i++)if(p->resource[i].kind==2)bank_proof(p->resource+i);
            event(&c,e,0,0);event(&c,e,0,1);event(&c,e,1,0);event(&c,e,2,0);event(&c,e,2,1);event(&c,e,2,2);
            keep_token=1;event(&c,e,1,0);keep_token=0;
            secondary_destroy(e);uint32_t ended=0x2f004;hook(&c,f_008311AA,current_token,1,&ended);assert(M32(ended)==1);
            hook(&c,f_00831782,current_token,0,NULL);
            keep_token=1;event(&c,e,1,0);keep_token=0;uint32_t retained=current_token;
            car_audio_unload(1);assert(p->retiring && p->bytes);c.ecx=0x20000;
            if(!cycle)f_004DC670(&c);else f_004F5CD0(&c);
            if(!cycle){c.ecx=0x20000;f_004F5D60(&c);}
            assert(!engine(0x20000) && !bridge.live && !p->key);
            uint32_t out=0x2f004;hook(&c,f_008311AA,retained,1,&out);assert(M32(out)==1);
            hook(&c,f_00831782,retained,0,NULL);
            assert(allocated==freed && packets_created==packets_destroyed);
            for(unsigned i=0;i<512;i++)assert(!secondary[i].token && !voices[i].owned);
            assert(!secondary_tokens);
        }
        ++cars;
    }
    fclose(manifest);free(stock_source[0]);free(stock_source[1]);free(G);
    printf("Undercover secondary: cars=%u tiers=%u layers=%u live=%u null=%u rows=%u empty=%u ticks=%u tokens=%u frames=%u PCM_error=%u rejected=%u\n",
        cars,tiers,layers,live_slots,null_slots,selector_rows,empty_rows,program_ticks,token_checks,decoded_frames,max_pcm_error,rejected);
    return cars==14 && tiers==106 && layers==212 && live_slots==336 && null_slots==144 && selector_rows==312 && empty_rows==144 && decoded_frames==5077075 && !rejected?0:1;
}
'''


def private(original=False, mutant=None, pc=False):
    attr = input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    images = sorted((input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    ffmpeg=ROOT/'third_party/ffmpeg/bin/ffmpeg'
    if not attr.is_file() or len(images) != 1 or not (ROOT / 'recomp/out/speed_55.c').is_file() or not ffmpeg.is_file():
        print('SKIP private secondary runtime: attributes, disc or translated fragments absent');return False
    attributes = attr.read_bytes(); expected = expectations(attributes)
    if mutant:
        subject='BMW_M6_STK_08' if mutant=='bank' else 'DOD_CHR_BEE_07'
        expected={car: tiers for car, tiers in expected.items() if car==subject}
    paths = {p for plans in expected.values() for tier in plans for p in tier}
    if pc:
        corpus = module(ROOT / 'tests/setup/test_undercover_pc_audio.py', 'uc_pc_corpus')
        sources = corpus.resources()
        if sources is None:
            print('SKIP PC secondary runtime: installed sounds absent');return False
        resources = {p: sources[1][p] for p in paths}
    else:
        with uc.Disc(images[0]) as disc: resources = {p: disc.read(p) for p in paths}
    assert sum(p.endswith('.ABK') for p in paths) == (4 if mutant else 19)
    ps = module(ROOT / 'runtime/car_audio_prostreet_test.py', 'uc_secondary_runtime')
    with tempfile.TemporaryDirectory(prefix='uc-secondary-') as work:
        folder = Path(work); manifest = []; implementation = (ROOT / 'runtime/car_audio.c').read_text()
        source_trace(folder,images[0],attributes)
        corpus=module(ROOT/'tests/setup/test_car_audio_undercover.py','uc_secondary_pcm_oracle')
        for name,raw in resources.items():
            if not name.endswith('.ABK'):continue
            sounds=uc.bank(raw)
            with (folder/(hashlib.sha256(raw).hexdigest()+'.pcm')).open('wb') as oracle:
                oracle.write(struct.pack('<I',len(sounds)))
                for sound in sounds:
                    oracle.write(struct.pack('<I',sound.frames if sound else 0))
                    if sound:
                        from setup_kit.car_audio_codec import decode_sound
                        oracle.write(decode_sound(sound).pcm if pc else corpus.xma_decode(sound,ffmpeg))
        for car, tiers in expected.items():
            own = {p for tier in tiers for p in tier}; assert set(uc.describe(attributes, car)['resources']) == own
            package = car_audio_pack.build(attributes, car, {p: resources[p] for p in own}, game='undercover')
            assert struct.unpack_from('<I', package, 4)[0] == 7
            file = folder / (car + '.bin');file.write_bytes(package)
            pin = hashlib.sha256(package).hexdigest()
            pattern = r'(\{"' + car.lower() + r'",")[0-9a-f]{64}("\})'
            # Only the v7 table changes; the v6 compatibility table is retained.
            at = implementation.index('static const struct { const char *car,*sha; } undercover_packages[]=')
            if not pc:
                implementation = implementation[:at] + re.sub(pattern, lambda m:m[1]+pin+m[2], implementation[at:], count=1)
            manifest.append(car.lower() + ' ' + str(file) + ' ' + str(len(tiers)) + ' ' + ' '.join(
                hashlib.sha256(resources[p]).hexdigest() for tier in tiers for p in tier) + '\n')
        file = folder / 'manifest.txt';file.write_text(''.join(manifest))
        overrides = {}
        if mutant in ('selector', 'null'):
            source=(ROOT / 'runtime/car_audio_player.c').read_text()
            marker='    if (sample == UINT16_MAX) { reset(p); return; }'
            assert source.count(marker)==1
            change='    sample = 0;\n' if mutant=='selector' else '    if (sample == UINT16_MAX) sample = 0;\n'
            overrides['car_audio_player.c']=source.replace(marker,change+marker)
        if mutant=='loop':
            marker='(CarAudioPCM){samples,frames,(float)rate,loop!=0,NULL,NULL,loop?loop-1:0}'
            assert implementation.count(marker)==1
            implementation=implementation.replace(marker,marker.replace('loop!=0','0'))
        if mutant=='pcm':
            marker='samples[k]=(float)sample*(1.0f/32768.0f);'
            assert implementation.count(marker)==1
            implementation=implementation.replace(marker,marker.replace('1.0f/','0.5f/'))
        if mutant=='tier':
            marker='if(p->version==7) return level<0?0:(unsigned)level<p->tier_count?(unsigned)level:p->tier_count-1;'
            assert implementation.count(marker)==1
            implementation=implementation.replace(marker,'if(p->version==7) return 0;')
        if mutant=='binding':
            source=(ROOT / 'runtime/car_audio_csis.c').read_text()
            overrides['car_audio_csis.c']=source.replace('system == 0x6a16','system == 0xffff')
        if mutant in ('hooks', 'release', 'carid', 'bank'):
            source=(ROOT / 'runtime/car_audio_secondary.h').read_text()
            if mutant=='hooks':
                marker='if(secondary_owner) {';assert source.count(marker)==1
                source=source.replace(marker,'if(0 && secondary_owner) {')
            elif mutant=='release':
                marker='if(secondary[i].owner==e) secondary_end(secondary+i);';assert source.count(marker)==1
                source=source.replace(marker,'if(secondary[i].owner==e) (void)e;')
            elif mutant=='carid':
                source=source.replace('values[1]=(int32_t)r->event_identity;', '/* donor identity retained */')
                source=source.replace('if(s->kind==0) values[1]=(int32_t)s->event_identity;', '/* donor update retained */')
            else:
                source=source.replace('unsigned index=kind==0?1:', 'unsigned index=kind==0?0:')
            (folder / 'car_audio_secondary.h').write_text(source)
        if original:
            guard='if(version==7) for(size_t i=0;i<sizeof undercover_packages/sizeof *undercover_packages;i++)'
            assert implementation.count(guard)==1
            implementation=implementation.replace(guard,guard.replace('version==7','0'))
        def transform(harness):
            old='void f_004F33B0_orig(Cpu *c) { c->eax=0; c->esp+=8; }'
            assert harness.count(old)==1
            return harness.replace(old,'void fixture_shift_update(Cpu *);\nvoid f_004F33B0_orig(Cpu *c) { fixture_shift_update(c); }')
        def check(result):
            if mutant:
                failures={'selector':'voice && voice->sample.pcm==r->samples[index].pcm',
                    'null':'!status && !player_state.owned', 'loop':'s->loop==!!',
                    'tier':'tier_for_level(p,level)', 'binding':'!code',
                    'hooks':'s && s->owner==e', 'release':'M32(ended)==1', 'carid':'starts',
                    'bank':'voice && voice->sample.pcm==r->samples[sample].pcm', 'pcm':'error<=1'}
                assert result.returncode!=0 and failures[mutant] in result.stderr, result.stderr[-4000:]
                print('rejected secondary control:', mutant, 'exit', result.returncode)
            elif original:
                assert result.returncode==1 and 'rejected=106' in result.stdout, result.stderr[-3000:]
            else:
                assert result.returncode==0, result.stderr[-5000:]+result.stdout[-1000:]
        main = MAIN
        if pc:
            main = main.replace('decoded_frames==5077075', 'decoded_frames==5002331')
            # ABKC/module controls are LE; embedded S10A/SNR stays BE.
            for expression in ('raw+32', 'raw+56', 'raw+f3', 'raw+row+4', 'raw+row',
                               'raw+descriptor+44', 'raw+descriptor+60', 'raw+state+player+4', 'raw+table'):
                main = main.replace('source_u32('+expression+')', 'rd('+expression+')')
            main = main.replace('first=source_u32(raw+table+4+12*i),index=first>>16',
                                'first=rd(raw+table+4+12*i),index=first&65535')
            main = main.replace('(first==0xffff0000?65535:((first&65535)<<8)|index)', 'first')
            main = main.replace('first==0xffff0000', 'first==65535')
            main = main.replace('((first>>8)&255)*.01f', '(first>>16)*.01f')
        ps.live(folder,file,harness_main=main,implementation=implementation,check=check,
                harness_transform=transform,console=True,overrides=overrides)
    return True


if __name__ == '__main__':
    proof = module(ROOT / 'tests/setup/test_car_audio_undercover_secondary.py', 'uc_secondary_portable')
    import unittest
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(proof.Secondary))
    if not result.wasSuccessful(): raise SystemExit(1)
    mutant = sys.argv[sys.argv.index('--mutant')+1] if '--mutant' in sys.argv else None
    if not private('--original' in sys.argv, mutant): raise SystemExit(3)
