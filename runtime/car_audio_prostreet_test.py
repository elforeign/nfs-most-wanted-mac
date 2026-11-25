#!/usr/bin/env python3
"""Stock player regression: original native identities 0/62 (exit 1);
legacy-CarID resolver control 10/62 (exit 1); fixed 62/62 through both constructors.

Retained v4 Veyron playback regression, null transport and real Ginsu synthesis.

Idle RPM regression: original attribute mapping gives exhaust gain 0 at 1056 RPM;
fixed idle exhaust gain 0.638338685 at 1087 RPM;
source 00509680 intersects serialized TMX bounds (886..6761 / 1086..8840),
mapping idle to 1087 and the top to 6759 RPM for both layers. The original
mapping mutant fails the nonzero exhaust idle check on both engine lifecycles.

Original has no admitted ProStreet sidecar: 0/2 source layers, 0/5 GINs.
Fixed serves engine bank 68 / exhaust bank 62: 2/2 TMX layers and 5/5 original GIN recordings, produces nonzero
idle/Ginsu PCM, and release every allocation on both native engine lifecycles.
Removing the ProStreet package pin reproduces the old rejection (exit 1).
Race level: with the engine's mix group at 11192/32767 and the native full-throttle controller word (-24627), the
main Ginsu voice plays at native level 24 of 127. Restoring the former second group gain plays it at 8 and fails.
The original ProStreet TMX evaluator is a separately SHA-checked numeric oracle;
its voice start/stop callback is stubbed, after its track gains are calculated.
"""
import ctypes as ct
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'recomp')]
from setup_kit import car_audio, car_audio_pack, car_audio_prostreet as ps
from setup_kit.input_paths import input_path


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


class Boundaries(unittest.TestCase):
    def test_prostreet_admission_is_narrow(self):
        with self.assertRaisesRegex(ValueError, 'unsupported ProStreet'):
            car_audio.graph(b'unknown', 'veyron', {}, game='prostreet')
        with self.assertRaisesRegex(ValueError, 'unsupported ProStreet'):
            car_audio_pack.build(b'unknown', 'veyron', {}, game='prostreet')
        with self.assertRaisesRegex(ValueError, 'unsupported ProStreet'):
            car_audio_pack.resource_paths(b'unknown', 'veyron', (), game='prostreet')
        with self.assertRaises(ValueError):
            ps.tmx(bytes(96))

    def test_namespace_emits_required_audio(self):
        from setup_kit import ps_namespace
        source = {'GLOBAL/GLOBALB.LZC': b'', 'GLOBAL/INGAMEA.BUN': b'', 'GLOBAL/ATTRIBUTES.BIN': b'attrs',
                  'CARS/VEYRON/GEOMETRY.BIN': b'', 'CARS/VEYRON/TEXTURES.BIN': b'', 'SOUND/ENGINE/SOURCE.TMX': b'sound'}
        mw = {p: b'' for p in ('CARS/MURCIELAGO/GEOMETRY.BIN', 'CARS/MURCIELAGO/TEXTURES.BIN',
                               'CARS/FORDGT/GEOMETRY.BIN', 'CARS/FORDGT/TEXTURES.BIN',
                               'GLOBAL/GLOBALB.LZC', 'GLOBAL/ATTRIBUTES.BIN')}
        with mock.patch.object(ps_namespace.cc, 'unpack_file', side_effect=lambda b: b), \
             mock.patch.object(ps_namespace, 'stock_packs', return_value={}), \
             mock.patch.object(ps_namespace.ps_attrib, 'sidecars', return_value={}), \
             mock.patch.object(ps_namespace.cc, 'collision_sidecar', return_value=None), \
             mock.patch.object(ps, 'admitted', return_value=True), \
             mock.patch.object(car_audio_pack, 'resource_paths', return_value=['SOUND/ENGINE/SOURCE.TMX']), \
             mock.patch.object(car_audio_pack, 'build', return_value=b'source package') as build:
            output = ps_namespace.convert('VEYRON', 'MURCIELAGO', source, mw)
        self.assertEqual(output['AUDIO.BIN'], b'source package')
        build.assert_called_once_with(b'attrs', 'VEYRON', {'SOUND/ENGINE/SOURCE.TMX': b'sound'}, game='prostreet')


MAIN = r'''
int main(int argc,char **argv) {
    assert(argc==5); G=calloc(1,0x2000000); assert(G); diag_on=1;
    M32(0x9c21f4)=0x80000; M16(0x9c218c)=128; M8(0x9c204a)=128;
    for(unsigned i=0;i<2;i++) {
        FILE *f=fopen(argv[3+i],"rb"); assert(f); assert(!fseek(f,0,SEEK_END)); long n=ftell(f); rewind(f);
        assert(n>0 && n<4000000); stock_size[i]=(uint32_t)n; stock_source[i]=malloc((size_t)n); assert(stock_source[i]);
        assert(fread(stock_source[i],1,(size_t)n,f)==(size_t)n); fclose(f);
        uint8_t sha[32]; char id[65]; digest(stock_source[i],stock_size[i],sha); hex(sha,id);
        printf("old Murcielago %s=%s\n",i?"deceleration":"acceleration",id);
    }
    FILE *mem=fopen(argv[2],"rb"); assert(mem); uint32_t spans; assert(fread(&spans,4,1,mem)==1);
    while(spans--) { uint32_t at,n; assert(fread(&at,4,1,mem)==1 && fread(&n,4,1,mem)==1); assert(at+n<0x1000000); assert(fread(G+at,1,n,mem)==n); } fclose(mem);
    MF32(0x9c1b54)=10; memset(G+0x9c1fe8,0,24); M32(0x9c2528)=0x825310;
    Cpu c={0}; c.esp=0x1f00000; c.cw=0x37f;
    unsetenv("NFS_ADDCAR_AUDIO");
    if(car_audio_load(77,1,"veyron",argv[1])!=1) {
        puts("{\"layers\":0,\"gins\":0}"); return 1;
    }
    assert(car_audio_load(78,1,"murcielago",argv[1])<0);
    FILE *wrong=fopen(argv[1],"rb"); assert(wrong);
    assert(car_audio_load_stream(78,1,"veyron",2,wrong)<0); fclose(wrong);
    unsigned layers=0,gins=0;
    for(unsigned cycle=0;cycle<2;cycle++) {
        if(cycle) assert(car_audio_load(77,1,"veyron",argv[1])==1);
        /* Live constructors run before the first update writes the pitch
         * multiplier: a guarded race read 0 at both attach points. */
        setup(77); MF32(0x20000+0x3c)=0; c.ecx=0x20000; f_004EE3E0_orig(&c); c.esp=0x1f00000;
        if(!cycle) f_004EE1C0(&c); else f_004DC360(&c);
        MF32(0x20000+0x3c)=1;
        Engine *e=engine(0x20000); assert(e && e->replaced && e->package->version==4);
        Package *p=e->package;
        unsigned voice=0;
        for(unsigned k=0;k<2;k++) {
            Resource *r=p->resource+e->tier->layer[k][0];
            char name[192],pid[65],rid[65]; hex(p->digest,pid); hex(r->digest,rid);
            snprintf(name,sizeof name,"SOUND/ADDCAR_AUDIO/%s/%s",pid,rid);
            int fd=car_audio_open(name); assert(fd>=0); uint8_t *bytes=malloc(r->size); assert(bytes);
            size_t at=0; while(at<r->size) { ssize_t n=read(fd,bytes+at,r->size-at); assert(n>0); at+=(size_t)n; }
            close(fd); assert(!memcmp(bytes,r->source,r->size)); free(bytes); ++layers;
            assert(r->samples && r->samples->loop && r->samples->rate==48000);
            for(unsigned j=0;j<p->tmx[k].track_count;j++) {
                const CarAudioTmxTrack *track=p->tmx[k].tracks+j;
                if(!track->gin) continue;
                assert(!memcmp(G+e->ginsu[voice].gin,r->source+track->offset,track->size));
                assert(M32(e->ginsu[voice].data+0x1c)==e->ginsu[voice].gin+0x20);
                ++gins; ++voice;
            }
        }
        for(unsigned tick=0;tick<28;tick++) {
            raw_rpm=1000+(tick%14)*600; M32(0x28000+0xb8)=float_word((float)raw_rpm);
            c.ecx=0x20000; f_004EE3E0_orig(&c); M32(0x24000+0x10)=tick%2?1024:0;
            c.esp=0x1f00000; car_audio_tick(&c,0x20000); assert(engine(0x20000));
            if(!tick) {
                printf("idle exhaust gain %.9g\n",e->tmx_gains[1][2]); fflush(stdout);
                assert(fabsf(e->tmx_gains[1][2]-0.638338685f)<0.000004f);
            }
            for(unsigned k=0;k<4;k++) pump(&c);
        }
        assert(nonzero_ginsu && nonzero_bank);
        /* A live race holds the engine's NFSMix group near 11192/32767 and
         * the full-throttle controller volume (state+0x38) near 23837. Native
         * 004EE3E0 writes member+0x18 from their product, so that word already
         * carries the group: every source level follows it exactly once. The
         * fixture's stand-in writes 0 there, hence the explicit native value. */
        M32(0x27000)=11192u<<16|16384; M32(0x27000+4)=11192;
        raw_rpm=6000; M32(0x28000+0xb8)=float_word(6000);
        c.ecx=0x20000; f_004EE3E0_orig(&c); M32(0x24000+0x10)=1024;
        M32(0x24000+0x18)=(uint32_t)(((23837*11192>>15)*0x7fff>>15)-0x7fff);
        c.esp=0x1f00000; car_audio_tick(&c,0x20000); car_audio_tick(&c,0x20000);
        float race_control=(32767+(int32_t)M32(0x24000+0x18))*(1.0f/32767);
        assert((int32_t)M32(0x24000+0x18)==-24627);
        unsigned race_levels=0,race_main=0;
        for(unsigned k=0;k<5;k++) {
            int race_level=(int)(e->tmx_gains[e->ginsu[k].layer][e->ginsu[k].track]*127.0f*race_control);
            int played=(int)lroundf(transport[(unsigned)e->ginsu[k].voice-1000].gain*127);
            if(!k) { race_main=(unsigned)played; printf("race mix 11192: main Ginsu level %d of 127, single group gain %d\n",played,race_level); }
            assert(played==race_level); race_levels+=race_level>0;
        }
        assert(race_main && race_levels);
        for(unsigned k=0;k<2;k++) for(unsigned j=0;j<p->tmx[k].track_count;j++) if(!p->tmx[k].tracks[j].gin) {
            float race_volume=(float)(int32_t)(e->tmx_gains[k][j]*race_control*32767)*(1.0f/32767.0f),played=-1;
            for(unsigned v=0;v<512;v++) if(voices[v].owned && voices[v].handle==e->resident[k][j]) played=voices[v].volume;
            assert(played==race_volume);
        }
        M32(0x27000)=32767u<<16|16384; M32(0x27000+4)=32767; M32(0x24000+0x18)=0;
        car_audio_tick(&c,0x20000);
        M32(0x27000)=16384; car_audio_tick(&c,0x20000);
        for(unsigned k=0;k<5;k++) assert(transport[(unsigned)e->ginsu[k].voice-1000].gain==0);
        double muted=total_energy;
        for(unsigned k=0;k<4;k++) pump(&c);
        assert(total_energy==muted);
        M32(0x27000)=32767u<<16|16384; car_audio_tick(&c,0x20000);
        M32(0x24000+0x18)=(uint32_t)-32767; car_audio_tick(&c,0x20000);
        for(unsigned k=0;k<5;k++) assert(transport[(unsigned)e->ginsu[k].voice-1000].gain==0);
        muted=total_energy;
        for(unsigned k=0;k<4;k++) pump(&c);
        assert(total_energy==muted);
        M32(0x24000+0x18)=0; car_audio_tick(&c,0x20000);
        M8(0x20000+8)=0; car_audio_tick(&c,0x20000);
        if(!cycle) for(unsigned k=0;k<5;k++) assert(transport[(unsigned)e->ginsu[k].voice-1000].gain==0);
        M8(0x20000+8)=1; car_audio_tick(&c,0x20000);
        car_audio_unload(1); assert(p->retiring && p->bytes);
        c.ecx=0x20000; if(!cycle) f_004DC670(&c); else f_004F5CD0(&c);
        assert(!engine(0x20000) && !bridge.live && !p->key);
        if(!cycle) { c.ecx=0x20000; f_004F5D60(&c); }
        assert(allocated==freed && packets_created==packets_destroyed);
    }
    setenv("NFS_ADDCAR_AUDIO","off",1); assert(car_audio_load(77,2,"veyron",argv[1])==0);
    printf("{\"layers\":%u,\"gins\":%u,\"nonzero_idle_pcm\":%u,\"nonzero_ginsu_pcm\":%u,\"allocations\":%u,\"freed\":%u,\"packets\":%u}\n",
        layers/2,gins/2,nonzero_bank,nonzero_ginsu,allocated,freed,packets_created);
    free(stock_source[0]); free(stock_source[1]); free(G); return 0;
}
'''


ALL_MAIN = r'''
int main(int argc,char **argv) {
    assert(argc==5); G=calloc(1,0x2000000); assert(G);
    M32(0x9c21f4)=0x80000; M16(0x9c218c)=128; M8(0x9c204a)=128;
    for(unsigned i=0;i<2;i++) {
        FILE *f=fopen(argv[3+i],"rb"); assert(f); fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
        stock_size[i]=(uint32_t)n; stock_source[i]=malloc((size_t)n); assert(stock_source[i]);
        assert(fread(stock_source[i],1,(size_t)n,f)==(size_t)n); fclose(f);
    }
    FILE *mem=fopen(argv[2],"rb"); assert(mem); uint32_t spans; assert(fread(&spans,4,1,mem)==1);
    while(spans--) { uint32_t at,n; assert(fread(&at,4,1,mem)==1 && fread(&n,4,1,mem)==1); assert(at+n<0x1000000); assert(fread(G+at,1,n,mem)==n); } fclose(mem);
    MF32(0x9c1b54)=10; M32(0x9c2528)=0x825310;
    FILE *manifest=fopen(argv[1],"r"); assert(manifest);
    char car[16],path[1024],expected[2][65]; unsigned matches[2]={0},cars=0;
    while(fscanf(manifest,"%15s %1023s %64s %64s",car,path,expected[0],expected[1])==4) {
        for(unsigned cycle=0;cycle<2;cycle++) {
            /* Each constructor starts with a fresh freed guest heap. */
            assert(allocated==freed && packets_created==packets_destroyed);
            allocated=freed=0; heap=0x1000000;
            assert(car_audio_load(77,1,car,path)==1);
            setup(77); MF32(0x20000+0x3c)=0;
            Cpu c={0}; c.esp=0x1f00000;c.cw=0x37f;c.ecx=0x20000;
            f_004EE3E0_orig(&c); c.esp=0x1f00000;
            if(!cycle) f_004EE1C0(&c); else f_004DC360(&c);
            MF32(0x20000+0x3c)=1;
            Engine *e=engine(0x20000); assert(e && e->replaced && e->package->version==5);
            Package *p=e->package; unsigned voice=0;
            float low=0,high=100000;
            for(unsigned role=0;role<2;role++) {
                Resource *r=p->resource+e->tier->layer[role][0];unsigned bounds=rd(r->source+0x4c);
                assert(p->minimum_rpm[role]==rf(r->source+bounds));
                assert(p->maximum_rpm[role]==rf(r->source+bounds+4));
                low=fmaxf(low,p->minimum_rpm[role]);high=fminf(high,p->maximum_rpm[role]);
            }
            assert(p->tmx_minimum_rpm==low+1 && p->tmx_rpm_span==high-low-3);
            for(unsigned role=0;role<2;role++) {
                Resource *r=p->resource+e->tier->layer[role][0];
                char name[192],pid[65],rid[65]; hex(p->digest,pid); hex(r->digest,rid);
                snprintf(name,sizeof name,"SOUND/ADDCAR_AUDIO/%s/%s",pid,rid);
                int fd=car_audio_open(name); assert(fd>=0); uint8_t *bytes=malloc(r->size); assert(bytes);
                size_t at=0; while(at<r->size) { ssize_t n=read(fd,bytes+at,r->size-at); assert(n>0);at+=(size_t)n; }
                close(fd); uint8_t hash[32];char served[65];digest(bytes,r->size,hash);hex(hash,served);
                assert(!memcmp(bytes,r->source,r->size));free(bytes);
                matches[cycle]+=!strcmp(served,expected[role]);
                const uint8_t *pcm=r->sample_source;
                unsigned sample=0;
                for(unsigned j=0;j<p->tmx[role].track_count;j++) {
                    const CarAudioTmxTrack *t=p->tmx[role].tracks+j;
                    if(t->gin) {
                        assert(!memcmp(G+e->ginsu[voice].gin,r->source+t->offset,t->size));
                        assert(M32(e->ginsu[voice].data+0x1c)==e->ginsu[voice].gin+0x20);++voice;
                    } else {
                        assert(r->samples[sample].rate==t->rate && rd(pcm+4)==t->rate);
                        ++sample;pcm+=16+rd(pcm+12);
                    }
                }
            }
            assert(voice==ginsu_count(e) && voice<=6);
            unsigned prior_gin=nonzero_ginsu,prior_bank=nonzero_bank;
            for(unsigned tick=0;tick<28;tick++) {
                raw_rpm=1000+(tick%14)*600;M32(0x28000+0xb8)=float_word((float)raw_rpm);
                c.ecx=0x20000;f_004EE3E0_orig(&c);M32(0x24000+0x10)=tick%2?1024:0;
                c.esp=0x1f00000;car_audio_tick(&c,0x20000);assert(engine(0x20000));
                for(unsigned k=0;k<4;k++)pump(&c);
            }
            assert(nonzero_ginsu>prior_gin && nonzero_bank>prior_bank);
            M32(0x27000)=16384;car_audio_tick(&c,0x20000);
            for(unsigned k=0;k<voice;k++)assert(transport[(unsigned)e->ginsu[k].voice-1000].gain==0);
            double muted=total_energy;for(unsigned k=0;k<4;k++)pump(&c);assert(total_energy==muted);
            car_audio_unload(1);assert(p->retiring && p->bytes);c.ecx=0x20000;
            if(!cycle)f_004DC670(&c);else f_004F5CD0(&c);
            assert(!engine(0x20000) && !bridge.live && !p->key);
            if(!cycle){c.ecx=0x20000;f_004F5D60(&c);}
            assert(allocated==freed && packets_created==packets_destroyed);
        }
        ++cars;
    }
    fclose(manifest);printf("stock native player identities: %u/62, %u/62; cars=%u\n",matches[0],matches[1],cars);
    free(stock_source[0]);free(stock_source[1]);free(G);
    return matches[0]==62 && matches[1]==62 && cars==31?0:1;
}
'''

def live(folder, package, *, original=False, broken_mix=False, double_mix=False, broken_rpm=False, stock=False, control=False):
    proof = module(ROOT/'runtime/car_audio_runtime_test.py', 'ps_native_proof')
    driver = module(ROOT/'runtime/car_audio_test.py', 'ps_live_proof')
    source = input_path('NFSC/NFSC.exe').read_bytes()
    native = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent/'Need For Speed Most Wanted Black Edition/speed.exe'))
    (folder/'memory.bin').write_bytes(proof.memory_bytes(native.read_bytes()))
    code, declarations, dispatch, stubs = driver.native_fragments(proof, source)
    (folder/'native.c').write_text(code)
    implementation = (ROOT/'runtime/car_audio.c').read_text()
    if control:
        for car, pin in json.loads((folder/'control_pins.json').read_text()).items():
            implementation=re.sub(r'(\{"'+car+r'",")[0-9a-f]{64}("\})', lambda m: m[1]+pin+m[2], implementation)
    if original:
        implementation = implementation.replace('if(version==4 && !strcasecmp(name,"veyron"))',
                                                'if(0 && version==4 && !strcasecmp(name,"veyron"))')
    if broken_rpm:
        old = ('        float rpm=(float)(p->minimum_rpm[layer]+(p->maximum_rpm[layer]-p->minimum_rpm[layer])*(normalized-1000)/9000);\n'
               '        rpm=fmaxf(p->minimum_rpm[layer],fminf(p->maximum_rpm[layer],rpm));')
        assert hashlib.sha256(old.encode()).hexdigest() == 'b3576eb9d998c8d40a076bde6160d77459500313eaa800f2fd9655e5bfb088d0'
        current='    float rpm=(float)(p->tmx_minimum_rpm+p->tmx_rpm_span*fmax(0,fmin(1,(normalized-1000)/9000)));'
        assert implementation.count(current)==1
        implementation=implementation.replace(current,'')
        marker='    for(unsigned layer=0;layer<2;layer++) {'
        # Restrict the mutation to the production updater.
        at=implementation.index('static int update_tmx(')
        implementation=implementation[:at]+implementation[at:].replace(marker,marker+'\n'+old,1)
    if broken_mix:
        guard='float changed=e->mix_gain?(float)engine_mix_gain(e)/e->mix_gain:0;'
        assert implementation.count(guard) == 1
        implementation=implementation.replace(guard,'float changed=1;')
    if double_mix:
        for fixed, former in (
                ('(int32_t)(base?e->tmx_gains[e->ginsu[k].layer][e->ginsu[k].track]*127.0f*source_gain:0)',
                 '(int32_t)(e->tmx_gains[e->ginsu[k].layer][e->ginsu[k].track]*base*(127.0f/32767)*source_gain)'),
                ('float gain=spatial?tmx_controller_gain(e):0;',
                 'float gain=spatial?(M32(spatial+4)&32767)*(1.0f/32767)*tmx_controller_gain(e):0;')):
            assert implementation.count(fixed) == 1
            implementation=implementation.replace(fixed,former)
    (folder/'car_audio.c').write_text(implementation)
    posaudio = (ROOT/'runtime/posaudio.c').read_text()
    wrappers = posaudio[posaudio.index('#define VOICE_HOOK'):posaudio.index('\nvoid posaudio_diag_second')]
    harness = driver.HARNESS[:driver.HARNESS.index('int main(int argc,char **argv)')] + (ALL_MAIN if stock else MAIN)
    harness = harness.replace('NATIVE_DECLARATIONS', declarations).replace('NATIVE_DISPATCH', dispatch)
    harness = harness.replace('NATIVE_STUBS', stubs).replace('VOICE_WRAPPERS', wrappers)
    harness = harness.replace('void diag_event(const char *fmt,...) { (void)fmt; }',
        'void diag_event(const char *fmt,...) { va_list args; va_start(args,fmt); vprintf(fmt,args); putchar(10); va_end(args); }')
    (folder/'harness.c').write_text(harness)
    cmd = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
           '-Wno-unused-function', '-Wno-unused-variable', '-Wno-parentheses-equality', '-Wno-deprecated-declarations',
           '-ffp-contract=off', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
           '-I'+str(folder), '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'),
           str(folder/'harness.c'), str(folder/'native.c'), str(ROOT/'runtime/interp.c'),
           *[str(ROOT/f'runtime/car_audio_{n}.c') for n in ('runtime','csis','engine_class','player','backend','filter','tmx')],
           *driver.xma_link(folder), '-o', str(folder/'live')]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-5000:]
    stock_dir = native.parent/'SOUND/ENGINE'
    result = subprocess.run([str(folder/'live'),str(package),str(folder/'memory.bin'),
                             str(stock_dir/'GIN_Murcielago.gin'),str(stock_dir/'GIN_Murcielago_DCL.gin')],
                            capture_output=True, text=True, timeout=40)
    print(result.stdout[-10000:], end='')
    if stock:
        assert result.returncode == (1 if control else 0), result.stderr[-5000:]+result.stdout[-1000:]
        expected=10 if control else 62
        assert f'{expected}/62, {expected}/62; cars=31' in result.stdout
    elif broken_rpm:
        assert result.returncode != 0 and 'e->tmx_gains[1][2]-0.638338685f' in result.stderr, result.stderr[-3000:]
        print('original attribute RPM mapping rejected: exhaust gain 0 at 1056 RPM')
    elif double_mix:
        assert result.returncode != 0 and 'played==race_level' in result.stderr, result.stderr[-3000:]
        print('second NFSMix group gain rejected (a race-level engine must not be attenuated twice)')
    elif broken_mix:
        assert result.returncode != 0 and 'Assertion failed' in result.stderr, result.stderr[-3000:]
        print('broken ProStreet mix control rejected (native voices must mute with their group)')
    elif original:
        assert result.returncode == 1 and '"layers":0' in result.stdout, result.stderr[-3000:]
        print('original ProStreet rejection: 0/2 source layers, 0/5 GINs (exit 1)')
    else:
        assert result.returncode == 0, result.stderr[-5000:]
        report = json.loads(result.stdout.splitlines()[-1])
        assert report['layers'] == 2 and report['gins'] == 5
        assert report['nonzero_idle_pcm'] and report['nonzero_ginsu_pcm']
        assert report['allocations'] == report['freed']
    return result.stdout


def gain_oracle(folder, executable, resources):
    """Compile pinned range and gain functions; supplement the translator's SSE subset.

    These scalar SSE moves/comparisons have no audio semantics. The table and
    branch decisions still come from the separately supplied source executable.
    """
    import capstone
    import recomp
    proof = module(ROOT/'runtime/car_audio_runtime_test.py', 'ps_gain_pe')
    pe = proof.SourcePE(executable)
    functions = {
        0x509680: (0xc4, '8fae6a4d1f01048d51c22531664ca42a220c86410610ceba5ab33ff2cb0a0477'),
        0x510620: (0x5d4, '718a4469bb76115b64c4c7048ed513b9cd1c668fd295ce69f087377bb866a70d'),
        0x508210: (0x2cc, 'fae13de92a8e8a9f941d86411226b24b55da1f80dcfcd42e4f303b8072295e40')}
    bounds = {a: a+n for a, (n, _) in functions.items()} | {0x507da0: 0x507da1, 0x828710: 0x828711}
    rc = recomp.Recompiler(pe, bounds); rc.calls = True
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    bodies = []
    for address, (size, digest) in functions.items():
        raw = pe.read(address, size)
        assert hashlib.sha256(raw).hexdigest() == digest
        code, error = rc.translate(address, address+size)
        assert code and not error
        instructions = {i.address: i for i in md.disasm(raw, address)}
        def supplement(match):
            instruction = instructions[int(match[1],16)]
            def operand(op):
                if op.type == capstone.x86.X86_OP_REG:
                    name = instruction.reg_name(op.reg)
                    assert name.startswith('xmm')
                    return f'c->xmm[{int(name[3:])}][0]'
                assert op.type == capstone.x86.X86_OP_MEM
                m = op.mem; parts = [str(m.disp)]
                if m.base: parts.append('c->'+instruction.reg_name(m.base))
                if m.index: parts.append(f'c->{instruction.reg_name(m.index)}*{m.scale}')
                return 'M32((uint32_t)('+'+'.join(parts)+'))'
            ops = instruction.operands
            if instruction.mnemonic in ('movss','movaps'):
                return operand(ops[0])+'='+operand(ops[1])+';'
            if instruction.mnemonic == 'xorps':
                assert ops[0].reg == ops[1].reg
                return operand(ops[0])+'=0;'
            if instruction.mnemonic in ('comiss','ucomiss'):
                left,right=(operand(o) for o in ops)
                return ('{ float a=xmm_float('+left+'), b=xmm_float('+right+'); '
                        'pf=isnan(a)||isnan(b); zf=pf||a==b; cf=pf||a<b; of=sf=af=0; }')
            raise AssertionError('unhandled oracle instruction: '+instruction.mnemonic)
        code = re.sub(r'untranslated\(c, 0x([A-F0-9]+)u\); return; /\* [^\n]+? \*/', supplement, code)
        assert 'untranslated(' not in code
        bodies.append(code)
    header = r'''
#include "runtime.h"
#include "car_audio_tmx.h"
#include <assert.h>
#include <stdlib.h>
uint8_t *G;
static float xmm_float(uint32_t n) { float f; memcpy(&f,&n,4); return f; }
void f_00510620(Cpu *); void f_00508210(Cpu *); void f_00509680(Cpu *);
void f_00507DA0(Cpu *c) { c->esp+=8; }
void f_00828710(Cpu *c) { int64_t n=(int64_t)c->st[c->top]; x87_pop(c); c->eax=(uint32_t)n; c->edx=(uint32_t)((uint64_t)n>>32); c->esp+=4; }
void bad_jump(Cpu *c,uint32_t at) { (void)c;(void)at;abort(); }
void untranslated(Cpu *c,uint32_t at) { (void)c;(void)at;abort(); }
'''
    wrapper = r'''
static CarAudioTmx parsed;
void oracle_range(const float *bounds,float *range,const uint8_t *constants) {
    free(G); G=calloc(1,0xb00000); assert(G);
    memcpy(G+0x9fa7c0,constants,8); memcpy(G+0x9fa7e8,constants+8,8);
    M32(0x1000+0x15c)=0x4000; M32(0x1000+0x2b8)=0x4008;
    memcpy(G+0x4000,bounds,16);
    Cpu c={0}; c.esp=0x180000; c.ecx=0x1000; c.cw=0x37f;
    f_00509680(&c); assert(c.esp==0x180004);
    range[0]=MF32(0x1000+0x334); range[1]=MF32(0x1000+0x338);
}
void oracle_init(const uint8_t *data,size_t n,const uint8_t *constants) {
    free(G); G=calloc(1,0xb00000); assert(G && n<0x200000);
    memcpy(G+0x96a500,constants,16); memcpy(G+0x96dcf8,constants+16,8);
    memcpy(G+0xa4eb40,constants+24,2048);
    assert(!car_audio_tmx_parse(&parsed,data,n)); memcpy(G+0x20000,data,n);
    uint32_t d=0x20000,i=0x1000;
    M32(i+0xc)=d; M32(i+0x18)=d+M32(d+60); M32(i+0x1c)=d+M32(d+64);
    M32(i+0x24)=d+M32(d+68); M32(i+0x2c)=d+M32(d+88); M32(i+0x3c)=0x3000;
    uint32_t at=M32(i+0x18);
    for(unsigned j=0;j<4;j++) { M32(at+20)=0x2000+4*M32(at+12); at+=44; if(j&1) at+=16; }
    at=M32(i+0x1c);
    for(unsigned j=0;j<4;j++) {
        unsigned pairs=M32(at+4); M32(at+24)=0x2000+4*M32(at+16); at+=M32(at+8);
        for(unsigned k=0;k<2*pairs;k++,at+=32) M32(at+28)=0x3000+4*M32(at+4);
    }
    at=M32(i+0x2c);
    for(unsigned j=0;j<M32(d+40);j++,at+=36) M32(at+32)=0x3000+4*M32(at+4);
}
void oracle_gain(const float *input,float *original,float *adapted) {
    memcpy(G+0x2000,input,20); Cpu c={0}; c.esp=0x180000; c.ecx=0x1000; c.cw=0x37f;
    M32(c.esp)=0xfeedf00d; MF32(c.esp+4)=1.0f/60;
    f_00510620(&c); assert(c.esp==0x180008); memcpy(original,G+0x3000,24);
    assert(!car_audio_tmx_gains(&parsed,input,adapted));
}
'''
    (folder/'gain_oracle.c').write_text(header+'\n'.join(bodies)+wrapper)
    result=subprocess.run(['clang','-shared','-fPIC','-std=c11','-O1','-ffp-contract=off',
        '-Wno-parentheses-equality','-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'runtime'),
        str(folder/'gain_oracle.c'),str(ROOT/'runtime/car_audio_tmx.c'),'-o',str(folder/'gain_oracle.dylib')],
        capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr[-3000:]
    lib=ct.CDLL(str(folder/'gain_oracle.dylib'))
    lib.oracle_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_void_p]
    lib.oracle_gain.argtypes=[ct.POINTER(ct.c_float)]*3
    constants=b''
    for address,size,digest in (
        (0x96a500,16,'fbeb0331fbb8b07aa8e0b39f5cb84dea76b0d6d2ddc6035c01ab8332be309d61'),
        (0x96dcf8,8,'5b82b9fbf7471f0fc00080bc7322947be992914f7df0eb48a771ce6dc9e39432'),
        (0xa4eb40,2048,'72f1e763a5500c460dd69270d645a5f86fb89962b3c22f84b210e3febadaf670')):
        raw=pe.read(address,size); assert hashlib.sha256(raw).hexdigest()==digest
        constants+=raw
    lib.car_audio_tmx_parse.argtypes=[ct.c_void_p,ct.c_void_p,ct.c_size_t]
    malformed=0
    for path,data in resources.items():
        if not path.lower().endswith('.tmx'): continue
        for at in (12,16,20,24,40,48,56,60,64,88,92):
            changed=bytearray(data);changed[at]^=0x80;changed=bytes(changed)
            with unittest.TestCase().assertRaises((ValueError,struct.error)):ps.tmx(changed)
            assert lib.car_audio_tmx_parse(ct.create_string_buffer(4096),changed,len(changed))<0,(path,at)
            malformed+=1
    print(f'TMX malformed topology refusals: {malformed} Python + native')
    # Source 00502560 binds the TMX input's bounds pointer to player+13c;
    # 00509680 intersects engine/exhaust. Serialized bounds begin at header+4c.
    bounds=[]
    for role in ('engine','exhaust'):
        data=next(data for path,data in resources.items() if path.lower().endswith('-'+role+'.tmx'))
        bounds.extend(struct.unpack_from('<2f',data,struct.unpack_from('<I',data,0x4c)[0]))
    lib.oracle_range.argtypes=[ct.POINTER(ct.c_float),ct.POINTER(ct.c_float),ct.c_void_p]
    range_constants=pe.read(0x9fa7c0,8)+pe.read(0x9fa7e8,8)
    assert range_constants==struct.pack('<2d',1,2)
    mapped=(ct.c_float*2)();lib.oracle_range((ct.c_float*4)(*bounds),mapped,range_constants)
    assert list(mapped)==[max(bounds[::2])+1, min(bounds[1::2])-max(bounds[::2])-3],list(mapped)
    # The source updater writes one mapped RPM to both TMX players.
    for address,size,digest in (
        (0x521a21,0x19,'ae8557f0e539072ded1d85528e6f5e669c6693ae0fea3ec27ac10a240319bc07'),
        (0x521b31,0xd,'8f7b435300ab99fe363842b49a77ea0631ad52b49a1a4672bad9bd6081df1c3e'),
        (0x521b8c,0xe,'1a2e868272bdc6db2afbc0e308789429fe5c37e41b3577030b810284f603452c')):
        assert hashlib.sha256(pe.read(address,size)).hexdigest()==digest
    print(f'original source RPM bounds {bounds}: idle {mapped[0]}, maximum {sum(mapped)}')
    maximum=0; comparisons=0; silent_control=0
    for path,data in resources.items():
        if not path.lower().endswith('.tmx'): continue
        lib.oracle_init(data,len(data),constants)
        track_count=len(ps.tmx(data)['tracks'])
        if path.lower().endswith('car_062-exhaust.tmx'):
            for idle in (1056,mapped[0]):
                inputs=(ct.c_float*5)(idle,1,0,0,0)
                original,adapted=(ct.c_float*6)(),(ct.c_float*6)()
                lib.oracle_gain(inputs,original,adapted)
                print(f'original exhaust at {idle} RPM: {list(original)[:track_count]}')
                assert (original[2]>0) if idle==mapped[0] else not any(original)
                difference=max(abs(a-b) for a,b in zip(original,adapted))
                maximum=max(maximum,difference); comparisons+=track_count
                silent_control+=sum(abs(value)>0.000004 for value in original)
                assert difference<0.000004
        for rpm in (886,900,1086,1100,1205,1538,1582,1740,1841,3000,3067,5000,5383,5703,6683,6761,7000,7687,8732,8840):
            for load in (0,.1,.2,.237172,.5,.90669,.936337,1):
                for throttle in (0,.2,.5,1):
                    inputs=(ct.c_float*5)(rpm,1,load,0,throttle)
                    original,adapted=(ct.c_float*6)(),(ct.c_float*6)()
                    lib.oracle_gain(inputs,original,adapted)
                    difference=max(abs(a-b) for a,b in zip(original,adapted))
                    maximum=max(maximum,difference);comparisons+=track_count
                    silent_control+=sum(abs(value)>0.000004 for value in original)
                    assert difference<0.000004,(path,rpm,load,throttle,list(original),list(adapted))
    print(f'ProStreet original TMX gain oracle: {comparisons} tracks; maximum absolute error {maximum:.9g}')
    assert silent_control>0
    print(f'zeroed source blend control: {silent_control}/{comparisons} source track values fail')


def catalog_proof(folder, source):
    """Exercise real conversion/admission with a disposable test trust root.

    This proves the Python/native boundary without installing approvals in the
    checkout; shipped approval generation remains a separate release check.
    """
    from setup_kit import car_catalog as catalog, car_import, car_selection
    before=next(r for r in car_selection.load() if r['origin']['folder']=='VEYRON')
    mw=Path(os.environ.get('NFS_GAME',ROOT.parent/'Need For Speed Most Wanted Black Edition')).resolve()
    engine=car_import.CarImporter(folder/'catalog',folder/'work')
    plan=engine.plan(source,mw_pc=mw,cars=[car_selection.spec(before)],measure=True)
    after=plan['cars'][0]['contract']
    assert after['outputs']['AUDIO.BIN']['sha256']=='5fef0aea88488249d4d0064343686fb2f0c586049c4bdc6dfbd2475f1d9b0ada'
    assert {k:v for k,v in after['outputs'].items() if k!='AUDIO.BIN'} == {k:v for k,v in before['outputs'].items() if k!='AUDIO.BIN'}
    names={n.upper() for n in ps.resource_paths((source/'GLOBAL/ATTRIBUTES.BIN').read_bytes(),'VEYRON',
                     (p.relative_to(source).as_posix() for p in (source/'SOUND/ENGINE').iterdir()))}
    assert {n for n in after['inputs']['source'] if n.startswith('SOUND/ENGINE/')}==names
    rows=car_selection.load()
    rows=[after if r['slot']==before['slot'] and r['origin']==before['origin'] else r for r in rows]
    doc=json.loads(catalog.packaged_policy().data)
    variants=doc['slots'][after['slot']]['variants']
    old=catalog.contract_hash(before)
    for v in variants:
        if v['contract']==old: v['legacy']=12
    if not any(v['contract']==catalog.contract_hash(after) for v in variants):
        variants.append(dict(contract=catalog.contract_hash(after),compatibility=catalog.slot_compatibility(after)))
    doc['revision']=15
    wire=catalog.encoded(doc);policy=catalog.Policy(wire,hashlib.sha256(wire).hexdigest())
    with mock.patch.object(catalog,'packaged_policy',return_value=policy), \
         mock.patch.object(car_selection,'load',return_value=rows):
        engine.import_cars(source,mw_pc=mw,cars=[car_selection.spec(after)])
        verified=catalog.verify_current(engine.root,policy=policy)
        assert not verified.error and len(verified.selected)==1,verified.error
        assert verified.selected[0]['outputs']['AUDIO.BIN']==after['outputs']['AUDIO.BIN']
    (folder/'proof_policy.h').write_text('#define CAR_CATALOG_POLICY_JSON '+json.dumps(wire.decode())+
        '\n#define CAR_CATALOG_POLICY_SHA256 "'+policy.sha256+'"\n')
    main=folder/'catalog_main.c'
    main.write_text(r'''
#include "car_catalog.h"
#include <assert.h>
#include <string.h>
int main(int argc,char **argv) {
    assert(argc==2); int n=car_catalog_init(argv[1],1);
    if(n<0) { fprintf(stderr,"%s\n",car_catalog_error()); return 1; }
    assert(n==1 && car_catalog_count()==1);
    const CarCatalogSlot *s=car_catalog_get(0); assert(s && !strcmp(s->source_folder,"VEYRON"));
    assert(s->outputs[CAR_CATALOG_AUDIO].present && s->outputs[CAR_CATALOG_AUDIO].version==5);
    FILE *f=car_catalog_open_sidecar(s->slot,CAR_CATALOG_AUDIO); assert(f);
    unsigned char h[8]; assert(fread(h,1,8,f)==8 && !memcmp(h,"A7CA\5\0\0\0",8)); fclose(f);
    puts("native Veyron AUDIO.BIN admission 1/1, version 5"); return 0;
}
''')
    original=(ROOT/'runtime/car_catalog.c').read_text()
    guard='NEED(kind!=CAR_CATALOG_AUDIO || carbon ||\n             (pc && equal(j,game,"prostreet")),'
    assert original.count(guard)==1
    for broken in (True,False):
        implementation=folder/'catalog_impl.c'
        implementation.write_text(original.replace(guard,'NEED(kind!=CAR_CATALOG_AUDIO || carbon,') if broken else original)
        command=['clang','-std=c11','-Werror','-Wno-deprecated-declarations','-DNFS_PUBLIC=1',
                 '-DCAR_CATALOG_APPROVAL_HEADER="proof_policy.h"','-I'+str(folder),'-I'+str(ROOT/'runtime'),
                 str(main),str(implementation),str(ROOT/'runtime/car_grip.c'),str(ROOT/'runtime/car_performance.c'),
                 '-o',str(folder/'catalog_reader')]
        built=subprocess.run(command,capture_output=True,text=True,timeout=60)
        assert built.returncode==0,built.stderr[-3000:]
        result=subprocess.run([str(folder/'catalog_reader'),str(engine.root)],capture_output=True,text=True,timeout=15)
        assert result.returncode==(1 if broken else 0),result.stderr[-3000:]
        print('original Carbon-only catalog admission 0/1 (exit 1)' if broken else result.stdout.strip())
    print('Veyron converter: original non-audio hashes unchanged; exact two-resource closure')


def player_expectations(attributes, source):
    """Independent roster and typed NG refs, without adapter admission/description."""
    from setup_kit.car_convert import hash32
    from setup_kit.ps_attrib import pvehicle_key
    rows=json.loads((ROOT/'tools/setup_kit/car_selections.json').read_text())
    cars=sorted(r['origin']['folder'] for r in rows if r['origin']['game']=='prostreet')
    assert len(cars)==31 and len(set(cars))==31
    attrib=car_audio.AudioAttributes(attributes)
    files={p.name.casefold():p for p in (source/'SOUND/ENGINE').iterdir() if p.is_file()}
    expected={}
    for car in cars:
        vehicle=attrib.field(hash32('pvehicle'),pvehicle_key(attrib,car),hash32('vehicle'))['values'][0]
        audio=attrib.field(int(vehicle['class'],16),int(vehicle['key'],16),hash32('CARAUDIO'))['values'][0]
        layers=[]
        for field in (0x9aed4ffe,0xb3186761):
            ref=attrib.field(int(audio['class'],16),int(audio['key'],16),field)['values'][0]
            assert ref['class']=='6d483f8f'
            name=attrib.field(0x6d483f8f,int(ref['key'],16),0x0eb1c9d2)['values'][0]['text']
            layers.append(files[name.casefold()])
        expected[car]=layers
    assert [p.name.lower() for p in expected['MCLARENF1']]==['car_047-engine.tmx','car_063-exhaust.tmx']
    return attrib,files,expected


def legacy_description(attrib, car):
    """Deliberately broken CarID selector, preserving its original field offsets."""
    from setup_kit.car_convert import hash32
    from setup_kit.ps_attrib import pvehicle_key
    value=lambda cls,key,field:attrib.field(cls,key,field)['values'][0]
    v=value(hash32('pvehicle'),pvehicle_key(attrib,car),hash32('vehicle'))
    audio=value(int(v['class'],16),int(v['key'],16),hash32('CARAUDIO'))
    layers=[]
    for field,role in ((car_audio.ENGINE,'engine'),(hash32('exhaustaudio'),'exhaust')):
        ref=value(car_audio.CAR,int(audio['key'],16),field)
        key=int(ref['key'],16);bank=value(car_audio.ENGINE,key,0x0c35b607)
        layers.append(dict(role=role,paths=[f'SOUND/ENGINE/CAR_{bank:03d}-{role.upper()}.TMX']))
    return dict(layers=layers)


def legacy_v4(attributes, resources):
    """Retain the exact released v4 closure and RPM attributes as a fixture."""
    from setup_kit import car_audio_codec as codec
    rows=[]
    for path,data in sorted(resources.items()):
        sounds=[t['sound'] for t in ps.tmx(data)['tracks'] if t['sound']] if path.endswith('.TMX') else []
        rows.append((path,data,sounds))
    indices={p:i for i,(p,_,_) in enumerate(rows)}
    payload=bytearray(struct.pack('<2I4f',indices['SOUND/ENGINE/CAR_068-ENGINE.TMX'],
        indices['SOUND/ENGINE/CAR_062-EXHAUST.TMX'],900,6683,1056,8732))+bytes.fromhex(ps.ATTRIBUTES)
    for path,data,sounds in rows:
        payload+=struct.pack('<4I',3 if path.endswith('.TMX') else 2,len(data),0,len(sounds))
        payload+=hashlib.sha256(data).digest()+data
        for sound in sounds:
            pcm=codec.decode_sound(sound).pcm
            payload+=struct.pack('<4I',sound.frames,sound.rate,sound.loop_start+1,len(pcm))+pcm
    result=car_audio_pack.HEADER.pack(b'A7CA',4,68+len(payload),4,1,b'veyron'.ljust(16,b'\0'),hashlib.sha256(payload).digest())+payload
    assert hashlib.sha256(result).hexdigest()=='471356c3aa5a0ba5c561c887ed61db3ca031388d22ff86d51faf6bd98c1925a7'
    return result


def private():
    source = input_path('Need for Speed ProStreet')
    native = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent/'Need For Speed Most Wanted Black Edition/speed.exe'))
    required = (source/'GLOBAL/ATTRIBUTES.BIN', source/'nfs.exe', input_path('NFSC/NFSC.exe'), native,
                *(ROOT/f'recomp/out/speed_{n}.c' for n in (13,14,55)))
    if not all(p.is_file() for p in required):
        print('SKIP private ProStreet/native synthesis inputs absent'); return False
    attributes=(source/'GLOBAL/ATTRIBUTES.BIN').read_bytes()
    attrib,files,expected=player_expectations(attributes,source)
    resources={f'SOUND/ENGINE/{p.name.upper()}':p.read_bytes() for paths in expected.values() for p in paths}
    assert len(resources)==42
    counts=set();gin_rates=set();snr_rates=set();curves=set();empty=False
    for data in resources.values():
        plan=ps.tmx(data);counts.add(len(plan['tracks']))
        curves.update(c['curve'] for c in plan['controls'])
        for b in plan['bins']:
            empty |= not b['ends']
            curves.update(e['curve'] for e in b['ends'])
        for t in plan['tracks']:
            if t['kind']=='gin':gin_rates.add(struct.unpack_from('<I',data,t['offset']+28)[0])
            else:snr_rates.add(t['sound'].rate)
    assert counts=={3,4,5,6} and curves=={0,1,4,8,9} and empty
    assert gin_rates=={24000,32000,36000,44100,48000} and snr_rates=={32000,44000,48000}
    print('stock TMX coverage: tracks',sorted(counts),'curves',sorted(curves),
          'GIN rates',sorted(gin_rates),'SNR rates',sorted(snr_rates),'empty bins',empty)
    with tempfile.TemporaryDirectory(prefix='prostreet-audio-') as work:
        folder=Path(work).resolve()
        gain_oracle(folder,(source/'nfs.exe').read_bytes(),resources)
        pins={};manifest=[];control=[]
        for car,paths in expected.items():
            selected=ps.resource_paths(attributes,car,resources)
            actual={p:resources[p] for p in selected}
            assert set(selected)=={f'SOUND/ENGINE/{p.name.upper()}' for p in paths}
            for missing in selected:
                with unittest.TestCase().assertRaises(ValueError):
                    ps.build(attributes,car,{p:b for p,b in actual.items() if p!=missing})
            package=ps.build(attributes,car,actual)
            path=folder/(car+'.bin');path.write_bytes(package)
            identities=' '.join(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)
            manifest.append(f'{car.lower()} {path} {identities}\n')
            broken=legacy_description(attrib,car)
            old={p:files[p.split('/')[-1].casefold()].read_bytes() for e in broken['layers'] for p in e['paths']}
            with mock.patch.object(ps,'describe',return_value=broken):
                package=ps.build(attributes,car,old)
            path=folder/(car+'-legacy.bin');path.write_bytes(package)
            pins[car.lower()]=hashlib.sha256(package).hexdigest()
            control.append(f'{car.lower()} {path} {identities}\n')
        (folder/'stock.txt').write_text(''.join(manifest));(folder/'control.txt').write_text(''.join(control))
        (folder/'control_pins.json').write_text(json.dumps(pins))
        live(folder,folder/'stock.txt',stock=True)
        live(folder,folder/'control.txt',stock=True,control=True)
        # Every released v4 byte and its original lifecycle/mix regressions stay checked.
        old={f'SOUND/ENGINE/{n}':files[n.lower()].read_bytes() for n in
             ('CAR_068-ENGINE.TMX','CAR_062-EXHAUST.TMX','SWTN_CAR_068.ABK','SWTN_CAR_062.ABK')}
        path=folder/'v4.bin';path.write_bytes(legacy_v4(attributes,old))
        gain_oracle(folder,(source/'nfs.exe').read_bytes(),old)
        for options in (dict(original=True),dict(broken_mix=True),dict(double_mix=True),dict(broken_rpm=True),{}):
            live(folder,path,**options)
        catalog_proof(folder,source.resolve())
    return True


if __name__ == '__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries))
    if not result.wasSuccessful(): sys.exit(1)
    sys.exit(0 if private() else 3)
