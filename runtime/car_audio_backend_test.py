#!/usr/bin/env python3
"""Concrete PCM backend proof; generated mono fixtures, no device or game.

Plan: execute SHA-256 pinned current MW option/filter fragments in --original;
compare rendered channel PCM to source goldens, then test pool and lifecycle.
No git-history baseline. Both modes compile and render actual PCM.
Measured: pinned original gain .0078740157187, impulse .0453514754772;
fixed gain .00781273841858, impulse .00473042856902. Original exits 1
with 2 numerical failures; fixed has 174/174 assertions. Eleven compiled
controls fail 6/2/2/3/7/2/12/2/3/2 assertions and (wrap-round) an ASan
heap-buffer-overflow abort, all exit 1. Wrapped-cursor case (1-frame loop,
16 kHz to 48 kHz, option 0 8192 then -4096): old code reads past the sample
(ASan abort, cursor -5.55e-17 + 1 rounds to 1.0); fixed passes. The normal check also executes the pinned original.
Required tolerances: gain 1e-9; 1-kHz impulse 2e-8 (source float stores differ
from the equation-rounded .004730417 by 1.16e-8). No filter changes here.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FROZEN = {
    '0081A259': '6bafb84394a87b70072a0573769217449a70ba29ec7b8696d5a14ed1df626841',
    '00824F3A': '9c792a3dec99302f4b957412a8b218d0aeb191a83d74a7ec0dba6f95acb4425c',
    '00826710': '9e4ee253735ca3a0f7e1da48e5e1c2129e1c3016a60a22c1b9f47be9c704a879',
}
ORIGINAL = r'''
#include "runtime.h"
uint8_t *G;
void f_0081A259(Cpu *);
void f_00824F3A(Cpu *);
void f_00826710(Cpu *);
static void original_call(void (*fn)(Cpu *)) {
    Cpu c={0}; c.esp=0x1000; c.cw=0x37f;
    fn(&c); assert(c.esp==0x1004 && c.top==0);
}
float original_gain(int32_t value) {
    if (!G) G=calloc(1,0xa00000);
    assert(G); M32(0x1004)=0x2000; M32(0x1008)=(uint32_t)value;
    original_call(f_0081A259);
    /* The native option is consumed in 127-scale units by its bank path. */
    return M8(0x2000)*(1.0f/127.0f);
}
void original_filter(CarAudioVoice *v, float *pcm) {
    if (!v->filter.active) return;
    if (!G) G=calloc(1,0xa00000);
    assert(G); MF32(0x8ae820)=0.00390625f;
    M32(0x3000)=(uint32_t)(int32_t)v->filter.cutoff_hz;
    M32(0x3004)=(uint32_t)(int32_t)v->filter.sample_rate;
    M32(0x3008)=256;
    M32(0x1004)=0x2000; M32(0x1008)=0x3000;
    original_call(f_00824F3A);
    MF32(0x201c)=v->filter.y1; MF32(0x4000)=*pcm;
    M32(0x1004)=0x201c; M32(0x1008)=1;
    M32(0x100c)=0x4000; M32(0x1010)=0x8000;
    original_call(f_00826710);
    *pcm=MF32(0x8000); v->filter.y1=MF32(0x201c);
}
'''
HARNESS = r'''
#include "car_audio_backend.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const CarAudioPlayerBackend *cb;
static CarAudioBackend pool;
static CarAudioVoice voices[4];
static float samples[16];
static int retired, resolved, fail_resolve, bad_sample;
static int failures, checks;
static void check(int ok, const char *label) {
    ++checks; if (!ok) { ++failures; fprintf(stderr,"FAIL %s\n",label); }
}
#define C(x) check(!!(x),#x)
static void near(double x,double y,double tol,const char *label) {
    ++checks; if (!isfinite(x) || fabs(x-y)>tol) {
        ++failures; fprintf(stderr,"FAIL %s: %.12g expected %.12g\n",label,x,y);
    }
}
static void retired_sample(void *p) { assert(p==&retired); ++retired; }
static CarAudioPCM pcm(int loop) {
    return (CarAudioPCM){samples,16,44100,loop,retired_sample,&retired};
}
static int resolve(void *ctx,uint64_t bank,int16_t sample,CarAudioPCM *out) {
    assert(ctx==&resolved && bank==77 && sample==3); ++resolved;
    if(fail_resolve) return -1;
    *out=pcm(1); if(bad_sample) out->rate=NAN;
    return 0;
}
static void init(size_t cap) {
    car_audio_backend_destroy(&pool);
    C(car_audio_backend_init(&pool,voices,cap,44100,resolve,&resolved)==0);
    cb=car_audio_backend_callbacks();
    for(unsigned i=0;i<16;i++) samples[i]=1;
    retired=resolved=fail_resolve=bad_sample=0;
}
static int32_t make(unsigned priority,unsigned angle) {
    CarAudioPlayerCreate r={.bank_identity=77,.sample=3,.mode=priority,
        .initialization={angle},.packed_offset=UINT32_MAX};
    int32_t h=-1; C(cb->create(&pool,&r,&h)==0); return h;
}
static float render_gain(int32_t h) {
    cb->option(&pool,h,3,16384); /* exact right, so no pan normalization */
    float dry[2]={0},wet[2]={0};
    car_audio_backend_render(&pool,dry,wet,1);
    near(dry[0],0,1e-12,"right-only left");
    return dry[1];
}
static void goldens(void) {
    init(1); int32_t h=make(80,0); cb->option(&pool,h,2,256);
    float gain=render_gain(h); printf("gain256=%.12g\n",gain);
    near(gain,.007812738,1e-9,"gain256 source");
    cb->release(&pool,h); h=make(80,0);
    samples[0]=1; for(unsigned i=1;i<16;i++) samples[i]=0;
    cb->option(&pool,h,6,1000);
    float impulse=render_gain(h); printf("impulse1000=%.12g\n",impulse);
    near(impulse,.004730417,2e-8,"impulse source");
    car_audio_backend_destroy(&pool); C(retired==2);
}
static void parameters(void) {
    init(1); int32_t h=make(60,8192);
    float d[4]={0},w[4]={0};
    car_audio_backend_render(&pool,d,w,1);
    near(d[0],.382683432,1e-7,"initial angle L");
    near(d[1],.923879533,1e-7,"initial angle R");
    near(w[1],0,0,"default wet");
    near(voices[0].pitch,1,0,"default pitch");
    near(voices[0].priority,.6,1e-7,"authored priority");
    cb->option(&pool,h,3,-8192-16384);
    memset(d,0,sizeof(d)); car_audio_backend_render(&pool,d,NULL,1);
    near(d[0],1,1e-7,"wrapped negative L"); near(d[1],0,1e-7,"wrapped negative R");
    cb->option(&pool,h,3,65536+17);
    memset(d,0,sizeof(d)); car_audio_backend_render(&pool,d,NULL,1);
    double theta=(8192.+17)*6.283185307179586/65536;
    near(d[0],sqrt((1-sin(theta))/2),1e-7,"continuous L");
    near(d[1],sqrt((1+sin(theta))/2),1e-7,"continuous R");
    cb->option(&pool,h,3,16384-8192);
    cb->option(&pool,h,2,65534); cb->option(&pool,h,8,-16384);
    cb->option(&pool,h,5,8192);
    memset(d,0,sizeof(d)); car_audio_backend_render(&pool,d,w,1);
    near(d[1],-2.*16384/32767,2e-7,"signed dry product");
    near(w[1],2.*8192/32767,1e-7,"wet product");
    cb->option(&pool,h,2,32767); cb->option(&pool,h,8,32767);
    cb->option(&pool,h,0,4097);
    for(unsigned i=0;i<16;i++) samples[i]=(float)i;
    voices[0].cursor=0; memset(d,0,sizeof(d));
    car_audio_backend_render(&pool,d,NULL,2);
    near(d[3],4097./4096,1e-7,"unquantized pitch PCM");
    cb->option(&pool,h,0,-2048); voices[0].cursor=2;
    memset(d,0,sizeof(d)); car_audio_backend_render(&pool,d,NULL,2);
    near(d[1],2,0,"reverse first"); near(d[3],1.5,0,"reverse second");
    cb->option(&pool,h,0,0); memset(d,0,sizeof(d));
    car_audio_backend_render(&pool,d,NULL,2);
    near(d[1],1,0,"zero pitch"); near(d[3],1,0,"zero pitch repeated");
    car_audio_backend_destroy(&pool); C(retired==1);
}
static void pause_history(void) {
    init(1); int32_t h=make(80,0); cb->option(&pool,h,3,16384);
    cb->option(&pool,h,6,1000); cb->option(&pool,h,0,2048);
    samples[0]=1; for(unsigned i=1;i<16;i++) samples[i]=0;
    float first[2]={0}; car_audio_backend_render(&pool,first,NULL,1);
    CarAudioFilter reference=voices[0].filter;
    double cursor=voices[0].cursor;
    cb->pause(&pool,h); cb->pause(&pool,h);
    float silent[8]={0}; car_audio_backend_render(&pool,silent,NULL,4);
    C(cb->status(&pool,h)==1); C(voices[0].cursor==cursor);
    C(memcmp(&reference,&voices[0].filter,sizeof(reference))==0);
    for(unsigned i=0;i<8;i++) near(silent[i],0,0,"paused silence");
    cb->option(&pool,h,2,16384); cb->option(&pool,h,8,8192);
    cb->option(&pool,h,5,4096); cb->option(&pool,h,0,8192);
    cb->option(&pool,h,6,2000);
    car_audio_filter_set_cutoff(&reference,2000);
    C(voices[0].filter.y1==reference.y1);
    cb->resume(&pool,h); C(voices[0].pitch==1); C(voices[0].cursor==cursor);
    float input=.5f,expected;
    car_audio_filter_process(&reference,&input,&expected,1);
    float d[2]={0},w[2]={0}; car_audio_backend_render(&pool,d,w,1);
    near(d[1],expected*(16384.f/32767)*(8192.f/32767),1e-8,"resumed dry/history");
    near(w[1],expected*(16384.f/32767)*(4096.f/32767),1e-8,"resumed wet/history");
    near(voices[0].cursor,1.5,0,"resume default pitch cursor");
    cb->option(&pool,h,6,1000000); C(voices[0].filter.y1==0);
    cb->option(&pool,h,6,1000); C(voices[0].filter.y1==0);
    cb->release(&pool,h); cb->release(&pool,h); C(retired==1);
    h=make(80,0); C(voices[0].cursor==0 && voices[0].filter.y1==0);
    cb->release(&pool,h); C(retired==2);
}
static void contention(void) {
    init(3); CarAudioPCM s=pcm(1); int32_t stock=-1;
    C(car_audio_backend_stock(&pool,&s,60,&stock)==0); C(stock==0);
    int32_t a=make(80,0), highest=make(100,0), b=make(60,0);
    C(!cb->status(&pool,stock)); C(cb->status(&pool,a));
    C(cb->status(&pool,highest)); C(cb->status(&pool,b)); C(retired==1);
    cb->pause(&pool,stock); cb->option(&pool,stock,2,0); cb->release(&pool,stock);
    C(retired==1); C(voices[0].paused==0 && voices[0].volume==1);
    float d[2]={0}; car_audio_backend_render(&pool,d,NULL,1);
    near(d[0],3*sqrt(.5),2e-7,"shared source and stock PCM");
    car_audio_backend_destroy(&pool); C(retired==4);
    init(3); highest=make(100,0); a=make(80,0);
    C(car_audio_backend_stock(&pool,&s,60,&stock)==0);
    b=make(80,0); C(!cb->status(&pool,stock));
    C(cb->status(&pool,highest) && cb->status(&pool,a) && cb->status(&pool,b));
    car_audio_backend_destroy(&pool); C(retired==4);
    init(2); a=make(80,0); b=make(80,0); int32_t newer=make(60,0);
    C(!cb->status(&pool,a) && cb->status(&pool,b) && cb->status(&pool,newer));
    car_audio_backend_destroy(&pool); C(retired==3);
    init(1); a=make(100,0);
    CarAudioPlayerCreate r={.bank_identity=77,.sample=3,.mode=60}; int32_t out=123;
    C(cb->create(&pool,&r,&out)==0 && out!=a); C(retired==1);
    C(!cb->status(&pool,a)); C(cb->status(&pool,out));
    cb->release(&pool,a); C(retired==1);
    pool.clock=UINT64_MAX; int32_t untouched=123;
    C(cb->create(&pool,&r,&untouched)<0 && untouched==123); C(retired==2);
    C(cb->status(&pool,out));
    C(car_audio_backend_stock(&pool,&s,80,&untouched)<0);
    C(retired==2); cb->release(&pool,out); C(retired==3);
}
static void age_batches(void) {
    init(3); int32_t a=make(80,0), b=make(80,0);
    C(voices[0].age==voices[1].age);
    C(car_audio_backend_tick(&pool)==0); int32_t recent=make(80,0);
    C(car_audio_backend_tick(&pool)==0); int32_t incoming=make(80,0);
    C(!cb->status(&pool,recent)); C(cb->status(&pool,a) && cb->status(&pool,b));
    C(cb->status(&pool,incoming));
    /* Equal effective ages follow insertion order, including reused slots. */
    car_audio_backend_destroy(&pool); C(retired==4);
    init(2); a=make(80,0); b=make(80,0); cb->release(&pool,a);
    recent=make(80,0); incoming=make(80,0);
    C(!cb->status(&pool,b)); C(cb->status(&pool,recent));
    C(cb->status(&pool,incoming));
    car_audio_backend_destroy(&pool); C(retired==4);
    C(car_audio_backend_tick(&pool)<0);
}
static void blocks_and_rates(void) {
    init(2); CarAudioPCM stock=pcm(1); int32_t a=-1;
    C(car_audio_backend_stock(&pool,&stock,60,&a)==0);
    int32_t b=make(80,0); C(cb->status(&pool,a) && cb->status(&pool,b));
    float sum[2]={0}; car_audio_backend_render(&pool,sum,NULL,1);
    near(sum[1],2*sqrt(.5),1e-7,"simultaneous stock/source PCM");
    cb->release(&pool,a); cb->release(&pool,b); C(retired==2);
    init(1); stock=pcm(1); stock.rate=22050;
    for(unsigned i=0;i<16;i++) samples[i]=(float)i;
    C(car_audio_backend_stock(&pool,&stock,60,&a)==0);
    cb->option(&pool,a,3,16384);
    float resampled[8]={0}; car_audio_backend_render(&pool,resampled,NULL,4);
    for(unsigned i=0;i<4;i++) near(resampled[i*2+1],i*.5,0,"sample-rate cursor");
    cb->release(&pool,a);
    samples[0]=1; for(unsigned i=1;i<16;i++) samples[i]=0;
    a=make(80,0); cb->option(&pool,a,3,16384); cb->option(&pool,a,6,1000);
    float whole[16]={0}; car_audio_backend_render(&pool,whole,NULL,8);
    cb->release(&pool,a);
    a=make(80,0); cb->option(&pool,a,3,16384); cb->option(&pool,a,6,1000);
    float split[16]={0}; car_audio_backend_render(&pool,split,NULL,3);
    car_audio_backend_render(&pool,split+6,NULL,5);
    C(memcmp(whole,split,sizeof(whole))==0);
    near(whole[3],.01820503920,2e-9,"impulse second");
    near(whole[15],.07577180862,2e-9,"impulse eighth");
    cb->release(&pool,a); C(retired==3);
    car_audio_backend_destroy(&pool);
}
static void lifecycle(void) {
    init(1); CarAudioPCM s=pcm(0); s.frames=2; int32_t h=-1;
    C(car_audio_backend_stock(&pool,&s,60,&h)==0);
    car_audio_backend_render(&pool,NULL,NULL,2); C(cb->status(&pool,h)==0);
    C(retired==0); cb->release(&pool,h); cb->release(&pool,h); C(retired==1);
    bad_sample=1;
    CarAudioPlayerCreate r={.bank_identity=77,.sample=3,.mode=80};
    C(cb->create(&pool,&r,&h)<0); C(retired==2); bad_sample=0;
    fail_resolve=1; C(cb->create(&pool,&r,&h)<0); C(retired==2); fail_resolve=0;
    r.mode=7; int old=resolved; C(cb->create(&pool,&r,&h)<0); C(resolved==old);
    C(car_audio_backend_init(NULL,voices,1,44100,resolve,NULL)<0);
    C(car_audio_backend_init(&pool,voices,0,44100,resolve,NULL)<0);
    C(car_audio_backend_init(&pool,voices,1,NAN,resolve,NULL)<0);
    pool.next_handle=0x80000000u; r.mode=80;
    C(cb->create(&pool,&r,&h)<0); C(retired==3);
    car_audio_backend_destroy(&pool); car_audio_backend_destroy(&pool);
}
static void wrap_round(void) {
    /* 1-frame loop at 16 kHz into 48 kHz: the reverse wrap lands on -5.55e-17,
     * and adding the length rounds to 1.0, one frame past the sample. */
    car_audio_backend_destroy(&pool);
    C(car_audio_backend_init(&pool,voices,1,48000,resolve,&resolved)==0);
    cb=car_audio_backend_callbacks(); retired=0;
    float *one=malloc(sizeof(float)); C(one!=NULL); if(!one) return;
    one[0]=1;
    CarAudioPCM s={one,1,16000,1,retired_sample,&retired}; int32_t h=-1;
    C(car_audio_backend_stock(&pool,&s,60,&h)==0);
    cb->option(&pool,h,3,16384); cb->option(&pool,h,0,8192);
    float out[4]={0}; /* both renders add into it */ car_audio_backend_render(&pool,out,NULL,2);
    cb->option(&pool,h,0,-4096); car_audio_backend_render(&pool,out,NULL,2);
    for(unsigned i=0;i<4;i++) near(out[i],i&1?2:0,1e-6,"wrapped loop PCM");
    cb->release(&pool,h); C(retired==1);
    car_audio_backend_destroy(&pool); free(one);
}
static void put(unsigned char *p,unsigned value) {
    for(unsigned i=0;i<4;i++) p[i]=(unsigned char)(value>>(8*i));
}
static void player_lifetime(void) {
    init(1); unsigned char state[40]={0},rows[16]={0};
    state[14]=1; state[16]=255; state[28]=2; put(state+36,256);
    put(rows,1); put(rows+4,3|(80u<<16)); put(rows+8,4); put(rows+12,~0u);
    CarAudioPlayer player={0};
    C(car_audio_player_attach(&player,state,sizeof(state),rows,sizeof(rows),77,cb,&pool)==0);
    put(state+24,1); C(car_audio_player_update(&player)==1);
    int32_t h=player.voice; float g=render_gain(h); near(g,.007812738,1e-9,"player PCM");
    put(state+24,2); C(car_audio_player_update(&player)==2);
    double cursor=voices[0].cursor; car_audio_backend_render(&pool,NULL,NULL,4);
    C(voices[0].cursor==cursor); put(state+36,32767);
    put(state+24,1); C(car_audio_player_update(&player)==1);
    near(render_gain(h),1,0,"player resumed updated PCM");
    car_audio_player_detach(&player); car_audio_player_detach(&player);
    C(retired==1); C(!cb->status(&pool,h));
    C(car_audio_player_attach(&player,state,sizeof(state),rows,sizeof(rows),77,cb,&pool)==0);
    C(car_audio_player_update(&player)==1); C(player.voice!=h);
    car_audio_player_destroy(&player,1); C(retired==2);
    car_audio_backend_destroy(&pool);
}
int main(int argc,char **argv) {
    goldens();
    if(argc>1 && !strcmp(argv[1],"all")) {
        parameters(); pause_history(); contention(); age_batches(); blocks_and_rates(); lifecycle(); player_lifetime(); wrap_round();
    }
    car_audio_backend_destroy(&pool);
#ifdef ORIGINAL
    free(G);
#endif
    printf("checks=%d failures=%d\n",checks,failures);
    return failures?1:0;
}
'''


def compile_proof(folder, original=False, mutant=None):
    compiler = shutil.which('clang')
    if not compiler:
        raise unittest.SkipTest('clang required')
    source = (ROOT / 'runtime/car_audio_backend.c').read_text()
    harness = HARNESS
    extra = []
    if original:
        generated_path = ROOT / 'recomp/out/speed_55.c'
        if not generated_path.exists():
            raise unittest.SkipTest('pinned generated fragments absent')
        generated = generated_path.read_text()
        bodies = []
        for name, digest in FROZEN.items():
            match = re.search(r'void f_' + name + r'\(Cpu \*restrict c\) \{.*?\n\}', generated, re.S)
            if not match or hashlib.sha256(match[0].encode()).hexdigest() != digest:
                raise AssertionError('pinned current fragment changed: ' + name)
            bodies.append(match[0])
        frozen = folder / 'frozen.c'
        frozen.write_text('#include "runtime.h"\n' + '\n'.join(bodies))
        source = source.replace('#include <string.h>', '#include <string.h>\n'
                                'float original_gain(int32_t);\n'
                                'void original_filter(CarAudioVoice *, float *);')
        source = source.replace('v->volume = (float)value * (1.0f / 32767.0f);',
                                'v->volume = original_gain(value);')
        source = source.replace('car_audio_filter_process(&v->filter, &pcm, &pcm, 1);',
                                'original_filter(v, &pcm);')
        harness = HARNESS.replace('static const CarAudioPlayerBackend *cb;',
                                  ORIGINAL + '\nstatic const CarAudioPlayerBackend *cb;')
        extra = ['-DORIGINAL', str(frozen)]
    mutations = {
        'quantized-gain': ('v->volume = (float)value * (1.0f / 32767.0f);',
                           'v->volume = (float)(value >> 8) / 127.0f;'),
        'quantized-pan': ('(uint32_t)v->base_angle + offset',
                          '((uint32_t)v->base_angle + offset) & ~255u'),
        'default-priority': ('.priority = priority * 0.01f', '.priority = 0.6f'),
        'newest-tie': ('v->handle < slot->handle', 'v->handle > slot->handle'),
        'epoch-age': ('v->age == b->clock - 1 ? 0 : v->age', '((void)b, v->age)'),
        'protected-100': ('v->priority >= 100.0f', 'v->priority >= 1.0f'),
        'pause-cursor': ('v->owned || v->ended || v->paused', 'v->owned || v->ended'),
        'resume-pitch': ('v->pitch = 1.0f;', 'v->pitch = 2.0f;'),
        'reset-history': ('case 6: car_audio_filter_set_cutoff',
                          'case 6: car_audio_filter_reset(&v->filter); car_audio_filter_set_cutoff'),
        'wrap-round': ('if (v->cursor >= length) v->cursor = 0.0;\n',
                       ''),
        'wet-product': ('wg = v->volume * v->wet', 'wg = v->wet'),
    }
    if mutant:
        before, after = mutations[mutant]
        assert source.count(before) == 1, (mutant, source.count(before))
        source = source.replace(before, after)
    backend = folder / 'backend.c'
    backend.write_text(source)
    main = folder / 'proof.c'
    main.write_text(harness)
    binary = folder / 'proof'
    command = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
               '-ffp-contract=off', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all', '-I', str(ROOT / 'runtime'),
               '-I', str(ROOT / 'recomp'), str(backend), str(main),
               str(ROOT / 'runtime/car_audio_filter.c'),
               str(ROOT / 'runtime/car_audio_player.c'), *extra, '-o', str(binary)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr[-5000:])
    return binary


def run(binary, original=False):
    # ASan aborts (SIGABRT) instead of exiting 1 when the caller's ASAN_OPTIONS ask for it.
    options = [o for o in os.environ.get('ASAN_OPTIONS', '').split(':')
               if o and o.split('=')[0] not in ('exitcode', 'abort_on_error')]
    env = dict(os.environ, ASAN_OPTIONS=':'.join(options + ['exitcode=1', 'abort_on_error=0']))
    return subprocess.run([str(binary), 'goldens' if original else 'all'],
                          capture_output=True, text=True, timeout=30, env=env)


class Proof(unittest.TestCase):
    def test_pcm(self):
        with tempfile.TemporaryDirectory(prefix='car-backend-') as folder:
            result = run(compile_proof(Path(folder), CONFIG.original), CONFIG.original)
            print(result.stdout, end='')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_acceptance_controls(self):
        if CONFIG.original:
            self.skipTest('original runs numerical source goldens only')
        with tempfile.TemporaryDirectory(prefix='car-original-') as folder:
            result = run(compile_proof(Path(folder), original=True), original=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('checks=8 failures=2', result.stdout)
            self.assertNotIn('Sanitizer', result.stderr)
        for mutant in ('quantized-gain', 'quantized-pan', 'default-priority', 'newest-tie',
                       'protected-100', 'epoch-age', 'pause-cursor', 'resume-pitch', 'reset-history', 'wet-product', 'wrap-round'):
            with self.subTest(mutant=mutant), tempfile.TemporaryDirectory(prefix='car-control-') as folder:
                result = run(compile_proof(Path(folder), mutant=mutant))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                if mutant == 'wrap-round':
                    self.assertIn('heap-buffer-overflow', result.stderr)
                    print(mutant + ': rejected by ASan heap-buffer-overflow')
                    continue
                self.assertIn('FAIL ', result.stderr)
                self.assertNotIn('Sanitizer', result.stderr)
                print(mutant + ': rejected ' + result.stdout.splitlines()[-1])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', action='store_true')
    CONFIG = parser.parse_args()
    unittest.main(argv=[__file__])
