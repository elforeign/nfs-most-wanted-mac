#!/usr/bin/env python3
"""Live adapter safety and byte identity, followed by private real-data checks.

Default-On proof: default-stock and default-added each FAIL (exit 1) on the
old unset-Off return; production PASS 2/2. With the variable unset, stock
compares 267,040 CPU/guest/PCM bytes (4,096 PCM) across four lifecycle pairs;
added AEMS/Ginsu constructors select their own engine/exhaust (2/2), nonzero PCM.
Explicit off/0 retains donor bytes. All 18 prior safety controls still PASS.
Native private replay also uses the unset default: stock/Off 120,320 PCM bytes
identical; Jaguar XK 2/2 resources and 4/4 source GINs, 163,701 nonzero samples.

Generated banks execute in the real interpreter with the production adapter.
Old -> new: 12 safety/lifetime controls fail 12/12 -> 0/12, including import
unwind, rejected vault packs, stock restoration, inactive source/Ginsu, live
mix gains, invalid RPM, full native pool, bridge/Ginsu recovery, duplicate
destroy and Ginsu detach. Two broken Off/stock guards also fail 2/2 -> 0/2;
fixed compares 534,080 CPU/guest/PCM bytes (8,192 PCM) across eight pairs.
Two engines retain independent NFSMix gains; replacement speech/siren tokens
survive recovery and teardown; all guest allocations are released.
The packet transport and live car observations are fixture boundaries.
Full-pool bridge/Ginsu steals, restricted admission range and a previous gain
of 1 fail 4/4 on the prior adapter -> 0/4 fixed. Steals wait muted for three
ticks, retain other voices, then resume when four/one slots become available.

The default and --private require private inputs (exit 3 when absent), using pinned native
data/decode/synthesis fragments. No sound device or game process is created.
Original must fail source matches.
Measured original 0/2 -> live 2/2 resources, 4/4 GIN inputs; 194/194 guest
allocations and 44/44 packet objects released across reload, detach and both
engine destructors. Native packet-reader PCM: 247,936 samples, 163,701 nonzero.
Off/stock: 120,320 equal PCM bytes across four comparisons, plus CPU/controller
bytes. Eleven targeted serving/export/PCM/GIN/descriptor/profile/lifetime/gain/
headroom/activity controls must fail. The float headroom fixture retains +1.25
and -1.5 through the native conversion boundary; the old int16 stage fails 2/2.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_pack as pack

GINSU = {
    '004E0FF0': 'a40b10102915355cff15dbba51f0a97405d6400a221e7207b99c73098b117ba9',
    '004E1020': 'e636844a6c340eaca2a74577d2954edf1baceb0db8758a71e7a7bbe1895c885f',
    '004E1070': '0730d91847003bc6099f31b988ebfb222c998bef807ac1b7314c265679eee345',
    '004E1280': '18954e483baff6b286f0825fa0b71af181970588b41a3eeda3518252644718cb',
    '004E1350': '4e3d75897c0cea30709fd828996b66bb9f398b57743e9b8c5bdcf38eabd157d0',
    '004E1410': 'b9c183210be34d119b53f3618087f7adb973973acfe8dff1e64247535a7b6c96',
    '004E14C0': '27e239fc921e1d757a75e54de1985f5316bd430dd11a77533d57856e08078973',
    '004E15E0': '0bb618c9676bace50ddd70ea8782bc5069042e12e50959d2543d6aafaeed22f0',
    '004E1740': '2a7ab92a01ca2a4463e594433e2806f4da99c663070b75b0da67d4d06f054750',
    '004E17F0': '9aa3b09b6d725a039045ec965836de75013016db5afe0e16569b316cec7debcd',
    '004E1C10': 'a9d79658b8903d67218805fd8d60f6b32124e8dba4618748cf36b513df6fdff5',
    '004E1CD0': '9fdfe3ae15c3539bd2581b80e7004da7a5da08ddebacd90e3d5bcb66d9636e34',
    '004E1D70': '976153169a8933a4649eec2c636884085605b05ad849bcbb129b913522b1c149',
    '004E8BD0': 'e8029ac4c1c15520006223b4927c241c3615aca6618b93e626d48f171570b64d',
    '004E8BE0': '8398713bb933dfa79707ab0d15a1bee21f700cf45ab06cc8902b3e2d75334769',
    '004E8C50': 'a4c868ebb030dc4be88e02e98b7d0be903227edcaa71b7519b422a438836cb23',
    '004E8CA0': 'a9608e3e0d390ef747eb6cf4da04b6de8bbbf9a039e9e9f104119e35ebdc877c',
    '0081D582': '96595194b87b8fe47771f1623e14a9a16e93e4beb6c0aa0b5513ecac3f786199',
    '0081E2CB': '258436b87ff7a87990ad9e207730d0eff774805495f30409f01a71b70f5e0cf9',
}
PCM_NATIVE = {
    '00825310': '7b7fc0f2cf93c8d00b4e78da3c91df2353795cfff8bbb1a140bb01402f904874',
    '00827182': '7677abb00ab13d72e931fa34bd5fbc2a38a9907b1521dff1165ca97abebafa92',
}

HARNESS = r'''
#include "host.h"
extern void car_audio_tick(Cpu *, uint32_t) __attribute__((weak));
#include "car_audio.c"
#include <assert.h>
#include <stdarg.h>
#include <inttypes.h>
uint8_t *G;
static uint32_t heap=0x1000000;
static unsigned allocated,freed,original_calls,source_updates,packets_created,packets_destroyed;
static uint32_t allocations[4096];
static uint32_t allocation_sizes[4096];
static double raw_rpm=3000;
static unsigned sample_frames,nonzero_frames,nonzero_ginsu,nonzero_bank;
static double total_energy;
static float source_peak;
void diag_event(const char *fmt,...) { (void)fmt; }
static uint8_t *stock_source[2];
static uint32_t stock_size[2];
static struct { uint32_t gin,data,synth,work; } stock_ginsu[2];
static int capture_stock;
static unsigned stock_frames;
static CC_SHA256_CTX stock_hash;
static char off_pcm_sha256[65];
static HostFn callback;
static struct {
    int live,playing;
    uint32_t callback,context,queue[16],frames[16],reader;
    unsigned head,count,acknowledged;
    float gain,dry,wet;
} transport[128];
uint32_t gmalloc_try(uint32_t n) {
    assert(allocated<4096); heap=(heap+63)&~63u; uint32_t p=heap; heap+=n+64;
    assert(heap<0x1f00000); memset(G+p,0,n); memset(G+p+n,0xd7,64);
    allocation_sizes[allocated]=n; allocations[allocated++]=p; return p;
}
uint32_t gmalloc(uint32_t n) { return gmalloc_try(n); }
void gfree(uint32_t p) {
    assert(p); unsigned i=0; for(;i<allocated;i++) if(allocations[i]==p) break; assert(i<allocated);
    for(unsigned j=0;j<64;j++) assert(G[p+allocation_sizes[i]+j]==0xd7);
    allocations[i]=0; ++freed;
}
uint32_t host_fn(HostFn fn,const char *name) { (void)name; callback=fn; return 0xf1000000; }
void interp_run(Cpu *,uint32_t);
NATIVE_DECLARATIONS
int code_region_of(uint32_t t) { return car_audio_code_region(t)?0:-1; }
void guest_divide_error(Cpu *c,uint32_t at) { (void)c;(void)at;abort(); }
static void submit(Cpu *c) {
    unsigned p=arg(c,0); uint32_t d=arg(c,1); assert(p<128 && transport[p].live);
    assert(transport[p].count<16); unsigned at=(transport[p].head+transport[p].count++)%16;
    transport[p].queue[at]=M32(d+12); transport[p].frames[at]=M32(d+4)&0x7fffffff;
    c->eax=0; c->esp+=4;
}
void dispatch(Cpu *c,uint32_t t) {
    if(car_audio_dispatch(c,t)) return;
    NATIVE_DISPATCH
    if(t==0xf1000000) { assert(callback); callback(c); return; }
    if(t==0xdead0001) { x87_push(c,raw_rpm); c->esp+=4; return; }
    if(t==0x8196c2) { c->eax=M32(arg(c,0)+16); M32(arg(c,0)+16)=0; c->esp+=4; return; }
    if(t==0x8196ce) { c->eax=M32(arg(c,0)+20); c->esp+=4; return; }
    if(t==0x831782) { ++source_updates; c->eax=0; c->esp+=4; return; }
    if(t==0x81dafd) { c->eax=512; c->esp+=4; return; }
    if(t==0x81db11) {
        unsigned p=0; while(p<128 && transport[p].live) ++p; assert(p<128);
        memset(transport+p,0,sizeof transport[p]);
        transport[p].live=1; transport[p].callback=arg(c,0); transport[p].context=arg(c,2);
        uint32_t reader=0x40000+p*0x40; transport[p].reader=reader; memset(G+reader,0,0x40);
        M32(reader+0x28)=p; M32(reader+0x20)=UINT32_MAX; M8(reader+0x30)=1;
        transport[p].head=transport[p].count=0; ++packets_created; c->eax=p; c->esp+=4; return;
    }
    if(t==0x81dbad) {
        unsigned p=arg(c,0); assert(p<128 && transport[p].live); transport[p].playing=1;
        M8(0x80000+p*0x90+0x6d)=1;
        transport[p].gain=(int8_t)M8(arg(c,3))*(1.0f/127.0f);
        transport[p].dry=(int8_t)M8(arg(c,3)+4)*(1.0f/127.0f);
        transport[p].wet=(int8_t)M8(arg(c,3)+5)*(1.0f/127.0f);
        c->eax=p+1000; c->esp+=4; return;
    }
    if(t==0x81deee) { submit(c); return; }
    if(t==0x81e274 || t==0x81e063) {
        unsigned p=arg(c,0); assert(p<128 && transport[p].live); transport[p].playing=0;
        M8(0x80000+p*0x90+0x6d)=0;
        if(t==0x81e063) { transport[p].live=0; ++packets_destroyed; }
        c->eax=0; c->esp+=4; return;
    }
    if(t==0x81e2cb || t==0x81d582) { memset(G+arg(c,0),0,t==0x81e2cb?16:24); c->eax=0; c->esp+=4; return; }
    if(t==0x81f7cb) {
        unsigned p=arg(c,0)-1000; c->eax=p<128 && transport[p].live && transport[p].playing?p:UINT32_MAX;
        c->esp+=4; return;
    }
    if(t==0x81d7ef || t==0x81e3cb || t==0x81e318) {
        unsigned p=arg(c,0)-1000; assert(p<128 && transport[p].live);
        if(t==0x81d7ef) transport[p].gain=(int32_t)arg(c,1)*(1.0f/127.0f);
        if(t==0x81e3cb) transport[p].dry=(uint8_t)arg(c,1)*(1.0f/127.0f);
        if(t==0x81e318) transport[p].wet=(int32_t)arg(c,2)*(1.0f/127.0f);
        c->eax=0; c->esp+=4; return;
    }
    if(t==0x81e092) {
        unsigned p=arg(c,0); assert(p<128 && transport[p].live);
        assert(!transport[p].acknowledged);
        M32(arg(c,2))=transport[p].count?transport[p].frames[transport[p].head]:0; M32(arg(c,3))=0;
        c->eax=transport[p].count?transport[p].queue[transport[p].head]:0; c->esp+=4; return;
    }
    if(t==0x81e1e8) {
        unsigned p=arg(c,0),n=arg(c,2); assert(p<128 && transport[p].live && transport[p].count);
        unsigned at=transport[p].head; transport[p].acknowledged+=n;
        assert(transport[p].acknowledged<=transport[p].frames[at]);
        if(transport[p].acknowledged==transport[p].frames[at]) {
            uint32_t pcm=transport[p].queue[at]; transport[p].head=(at+1)%16; --transport[p].count; transport[p].acknowledged=0;
            uint32_t args[]={pcm,transport[p].context}; native_call(c,transport[p].callback,0,2,args);
        }
        c->eax=0; c->esp+=4; return;
    }
    if(t==0x822050) { memset(G+arg(c,0),0,arg(c,1)); c->esp+=4; return; }
    if(t==0x828f10) abort(); /* 8-bit transport is outside this profile. */
    if(t==0x81bccb || t==0x81bcd9 || t==0x81e557 || t==0x81e4be || t==0x81e469) { c->eax=0; c->esp+=4; return; }
    fprintf(stderr,"unhandled %08x\n",t); abort();
}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    uint32_t sp=c->esp;
    for(int i=n-1;i>=0;i--) { c->esp-=4; M32(c->esp)=args[i]; }
    c->esp-=4; M32(c->esp)=0xfeedf00d; dispatch(c,fn); c->esp=sp; return c->eax;
}
NATIVE_STUBS
static void pump(Cpu *c) {
    Cpu caller=*c;
    for(unsigned p=0;p<128;p++) if(transport[p].live && transport[p].playing && transport[p].count) {
        uint32_t reader=transport[p].reader,available=M32(reader+0x20)-M32(reader+0x24),n=128;
        if((int32_t)available>0 && n>available) n=available;
        uint32_t out=0x50000+p*0x2000,args[]={reader,n,0,out};
        assert(native_call(c,0x827182,0,4,args)==n);
        for(unsigned i=0;i<n;i++) {
            float value=MF32(out+4*i)*transport[p].gain*(transport[p].dry+transport[p].wet); assert(isfinite(value));
            int s=value<=-32768?-32768:value>=32767?32767:(int)lrintf(value);
            ++sample_frames; nonzero_frames+=s!=0; total_energy+=(double)value*value;
            if(s) { if(transport[p].callback==0xf1000000) ++nonzero_bank; else ++nonzero_ginsu; }
            if(capture_stock) { uint16_t u=(uint16_t)s; uint8_t b[2]={(uint8_t)u,(uint8_t)(u>>8)}; CC_SHA256_Update(&stock_hash,b,2); ++stock_frames; }
        }
        if(bridge.produced) {
            unsigned slot=(unsigned)((bridge.produced-1)%PACKET_CACHE);
            for(unsigned ch=0;ch<4;ch++) for(unsigned j=0;j<PACKET_FRAMES;j++) {
                float v=fabsf(bridge.cache[slot][ch][j]); assert(isfinite(v));
                if(v>source_peak) source_peak=v;
            }
        }
    }
    *c=caller;
}
static void original(Cpu *c) { ++original_calls; c->eax=0xabcdef01; c->esp+=4; }
void f_004DC360_orig(Cpu *c) { original(c); }
void f_004EE1C0_orig(Cpu *c) {
    uint32_t object=c->ecx,packets=8,overhead=native_call(c,0x81dafd,0,1,&packets);
    for(unsigned i=0;i<2;i++) {
        stock_ginsu[i].gin=gmalloc(stock_size[i]); memcpy(G+stock_ginsu[i].gin,stock_source[i],stock_size[i]);
        stock_ginsu[i].data=gmalloc(0xb0); stock_ginsu[i].synth=gmalloc(0x44); stock_ginsu[i].work=gmalloc(overhead+0x8a4);
        uint32_t gin=stock_ginsu[i].gin; assert(native_call(c,0x4e1280,stock_ginsu[i].data,1,&gin)&255);
        uint32_t args[]={stock_ginsu[i].work,overhead+0x8a4}; native_call(c,0x4e8be0,stock_ginsu[i].synth,2,args);
        uint32_t data=stock_ginsu[i].data; assert(native_call(c,0x4e1c10,stock_ginsu[i].synth,1,&data)&255);
        uint32_t rpm=float_word((float)raw_rpm); int32_t voice=(int32_t)native_call(c,0x4e8ca0,stock_ginsu[i].synth,1,&rpm); assert(voice>=0);
        M32(object+0x54+0x18*i)=stock_ginsu[i].data; M32(object+0x58+0x18*i)=stock_ginsu[i].synth;
        M32(object+0x68+0x18*i)=(uint32_t)voice;
    }
    original(c);
}
void f_004F5CD0_orig(Cpu *c) { original(c); }
void f_004DC1D0_orig(Cpu *c) { M32(c->ecx+0x28)=0; original(c); }
void f_004DC670_orig(Cpu *c) { f_004DC1D0_orig(c); }
void f_004F5D60_orig(Cpu *c) {
    for(unsigned i=0;i<2;i++) {
        if(stock_ginsu[i].synth) native_call(c,0x4e8c50,stock_ginsu[i].synth,0,NULL);
        gfree(stock_ginsu[i].synth); gfree(stock_ginsu[i].data); gfree(stock_ginsu[i].work); gfree(stock_ginsu[i].gin);
        memset(stock_ginsu+i,0,sizeof stock_ginsu[i]);
    }
    memset(G+c->ecx+0x54,0,0x30); original(c);
}
void f_004EE3E0_orig(Cpu *c) {
    uint32_t m=M32(c->ecx+0x28);
    if(m) {
        M32(m+4+3*4)=512; M32(m+4+5*4)=0; M32(m+4+12*4)=1;
        M32(m+4+13*4)=0; M32(m+4+14*4)=8192; M32(m+4+15*4)=16384;
        M32(m+4+21*4)=12000; M32(m+4+25*4)=16384;
    }
    for(unsigned i=0;i<2;i++) if(stock_ginsu[i].synth && M32(stock_ginsu[i].synth+0x24)) {
        uint32_t frequency[]={float_word((float)raw_rpm),float_word(60)};
        native_call(c,0x4e1cd0,stock_ginsu[i].synth,2,frequency);
        uint32_t volume[]={M32(stock_ginsu[i].synth+0x20),127}; native_call(c,0x81d7ef,0,2,volume);
    }
    original(c);
}
void f_004EDD90_orig(Cpu *c) { f_004EE3E0_orig(c); }
void f_004EFBE0_orig(Cpu *c) { original(c); }
void f_004F03A0_orig(Cpu *c) { original(c); }
void f_004F0840_orig(Cpu *c) { original(c); }
static int category;
static uint32_t object;
int diag_on;
static int posaudio_engine_realistic(void) { return 0; }
static void source_register(int cat,uint32_t obj,uint32_t voice) { (void)cat;(void)obj;(void)voice; }
VOICE_WRAPPERS
static void setup(uint32_t key) {
    memset(G+0x20000,0,0x10000);
    M8(0x20000+8)=1;
    M32(0x20000+0x14)=0x21000; M32(0x21000+0x34)=0x22000;
    M32(0x22000+0x204)=0x23000; M32(0x23000+0x20)=key;
    M32(0x20000+0x28)=0x24000; M32(0x24000)=123;
    M32(0x20000+0x38)=0x25000; M32(0x25000)=0x26000; M32(0x26000+0x44)=0xdead0001;
    M32(0x20000+0x10)=0x27000; M32(0x27000)=32767u<<16|16384; M32(0x27000+4)=32767;
    M32(0x20000+0x40)=0x28000; M32(0x28000+0xb8)=float_word((float)raw_rpm);
    M32(0x28000+0x3c)=20000; M32(0x28000+0x40)=12767;
    MF32(0x20000+0x3c)=1.0f;
}
static void unchanged(Cpu *c,uint32_t key,int on) {
    if(on<0) { unsetenv("NFS_ADDCAR_AUDIO"); assert(car_audio_enabled()); }
    else setenv("NFS_ADDCAR_AUDIO",on?"on":"off",1);
    setup(key);
    Cpu a=*c,b=*c; a.ecx=b.ecx=0x20000;
    uint8_t before[0x10000],expected[0x10000]; memcpy(before,G+0x20000,sizeof before);
    CC_SHA256_Init(&stock_hash); capture_stock=1;
    f_004EE1C0_orig(&a); for(unsigned i=0;i<16;i++) { f_004EE3E0_orig(&a); for(unsigned k=0;k<4;k++) pump(&a); } f_004F5D60_orig(&a);
    uint8_t first[32],second[32]; CC_SHA256_Final(first,&stock_hash); memcpy(expected,G+0x20000,sizeof expected);
    memcpy(G+0x20000,before,sizeof before);
    CC_SHA256_Init(&stock_hash);
    f_004EE1C0(&b); for(unsigned i=0;i<16;i++) { f_004EE3E0(&b); for(unsigned k=0;k<4;k++) pump(&b); } f_004F5D60(&b);
    CC_SHA256_Final(second,&stock_hash); capture_stock=0;
    assert(!memcmp(&a,&b,sizeof a) && !memcmp(expected,G+0x20000,sizeof expected));
    assert(!memcmp(first,second,32)); hex(first,off_pcm_sha256);
    assert(!engine(0x20000));
}
static void rejected_packages(const char *filename) {
    FILE *f=fopen(filename,"rb"); assert(f); assert(!fseek(f,0,SEEK_END)); long n=ftell(f); rewind(f);
    uint8_t *source=malloc((size_t)n),*bad=malloc((size_t)n); assert(source && bad);
    assert(fread(source,1,(size_t)n,f)==(size_t)n); fclose(f);
    char path[4096]; assert(snprintf(path,sizeof path,"%s.bad",filename)<(int)sizeof path);
    unsigned before=allocated;
    for(unsigned mode=0;mode<5;mode++) {
        memcpy(bad,source,(size_t)n); size_t size=(size_t)n;
        if(mode==0) --size;
        if(mode==1) bad[0]^=1;
        if(mode==2) memset(bad+12,0,4);
        if(mode==3) bad[68+rd(bad+16)+0x28]^=1;
        if(mode==4) {
            /* Recomputing the transport checksum cannot admit a different
             * calibration or controller closure. The compiled profile wins. */
            bad[68+rd(bad+16)+0x28]^=1;
            digest(bad+68,(size_t)n-68,bad+36);
        }
        f=fopen(path,"wb"); assert(f); assert(fwrite(bad,1,size,f)==size); assert(!fclose(f));
        assert(car_audio_load(77,"jaguarxk",path)<0 && allocated==before);
    }
    assert(car_audio_load(77,"unsupported",filename)<0 && allocated==before);
    setenv("NFS_ADDCAR_AUDIO","off",1);
    assert(car_audio_load(77,"jaguarxk",path)==0 && allocated==before);
    setenv("NFS_ADDCAR_AUDIO","on",1);
    assert(!unlink(path)); free(source); free(bad);
}
int main(int argc,char **argv) {
    assert(argc==5); G=calloc(1,0x2000000); assert(G);
    M32(0x9c21f4)=0x80000; M16(0x9c218c)=128; M8(0x9c204a)=128;
    for(unsigned i=0;i<2;i++) {
        FILE *f=fopen(argv[3+i],"rb"); assert(f); assert(!fseek(f,0,SEEK_END)); long n=ftell(f); rewind(f);
        assert(n>0 && n<4000000); stock_size[i]=(uint32_t)n; stock_source[i]=malloc((size_t)n); assert(stock_source[i]);
        assert(fread(stock_source[i],1,(size_t)n,f)==(size_t)n); fclose(f);
    }
    FILE *mem=fopen(argv[2],"rb"); assert(mem); uint32_t spans; assert(fread(&spans,4,1,mem)==1);
    while(spans--) { uint32_t at,n; assert(fread(&at,4,1,mem)==1 && fread(&n,4,1,mem)==1); assert(at+n<0x1000000); assert(fread(G+at,1,n,mem)==n); } fclose(mem);
    MF32(0x9c1b54)=10; memset(G+0x9c1fe8,0,24);
    M32(0x9c2528)=0x825310;
    Cpu c={0}; c.esp=0x1f00000; c.cw=0x37f;
    unchanged(&c,77,0); unchanged(&c,999,-1);
    setenv("NFS_ADDCAR_AUDIO","on",1);
    rejected_packages(argv[1]);
    unsetenv("NFS_ADDCAR_AUDIO");
    assert(car_audio_open("SOUND/ENGINE/GIN_DB9.gin")==-1);
    assert(car_audio_load(77,"jaguarxk",argv[1])==1);
    unchanged(&c,77,0); unchanged(&c,999,-1);
    unsetenv("NFS_ADDCAR_AUDIO");
    sample_frames=nonzero_frames=nonzero_ginsu=nonzero_bank=0; total_energy=0;
    unsigned matched=0,matched_gins=0;
    for(unsigned cycle=0;cycle<2;cycle++) {
        setup(77); c.ecx=0x20000;
        f_004EE3E0_orig(&c); c.esp=0x1f00000;
        f_004EE1C0(&c);
        Engine *e=engine(0x20000);
        if(e) {
            const CarAudioRuntimeModule *m=e->instances[0].module;
            uint8_t *before=malloc(m->size); assert(before); memcpy(before,e->instances[0].state,m->size);
            M8(0x20000+8)=0; car_audio_tick(&c,0x20000);
            assert(!memcmp(before,e->instances[0].state,m->size)); free(before); M8(0x20000+8)=1;
            car_audio_tick(&c,0x20000);
            for(unsigned stream=0;stream<4;stream++) {
                unsigned p=(unsigned)bridge.stream[stream].packet;
                assert(transport[p].gain==1 && transport[p].dry==(stream<2?1:0) && transport[p].wet==(stream<2?0:1));
                for(unsigned buffer=0;buffer<2;buffer++) {
                    bridge.stream[stream].source[buffer][13]=1.25f;
                    bridge.stream[stream].source[buffer][14]=-1.5f;
                    uint32_t args[]={2,bridge.stream[stream].pcm[buffer]+26,0x150000};
                    native_call(&c,0x825310,0,3,args);
                    assert(MF32(0x150000)==40960 && MF32(0x150004)==-49152);
                    bridge.stream[stream].source[buffer][13]=bridge.stream[stream].source[buffer][14]=0;
                }
            }
        }
        if(e && cycle) { car_audio_unload(77); assert(e->package->retiring && e->package->key==77); }
        if(e) for(unsigned k=0;k<2;k++) {
            Resource *r=e->package->resource+e->package->layer[k][0];
            matched+=e->instances[k].bank==&r->bank;
            for(unsigned part=0;part<3;part++) {
                Resource *res=e->package->resource+e->package->layer[k][part];
                char pack_id[65],res_id[65],path[192]; hex(e->package->digest,pack_id); hex(res->digest,res_id);
                snprintf(path,sizeof path,"SOUND/ADDCAR_AUDIO/%s/%s",pack_id,res_id);
                int fd=car_audio_open(path); assert(fd>=0); uint8_t *data=malloc(res->size); assert(data);
                char absolute[192]; snprintf(absolute,sizeof absolute,"C:\\NFSMW\\%s",path);
                int second=car_audio_open(absolute); assert(second>=0);
                uint8_t first_bytes[16],second_bytes[16];
                assert(read(fd,first_bytes,16)==16 && read(second,second_bytes,16)==16);
                assert(!memcmp(first_bytes,second_bytes,16) && !memcmp(first_bytes,res->source,16));
                assert(lseek(fd,0,SEEK_SET)==0); close(second);
                size_t n=0; while(n<res->size) { ssize_t got=read(fd,data+n,res->size-n); assert(got>0); n+=(size_t)got; }
                uint8_t sha[32]; digest(data,n,sha); assert(!memcmp(sha,res->digest,32)); close(fd); free(data);
            }
            for(unsigned mode=0;mode<2;mode++) {
                unsigned i=k*2+mode;
                Resource *res=e->package->resource+e->package->layer[k][mode+1];
                assert(!memcmp(G+e->ginsu[i].gin,res->source,res->size));
                assert(M32(e->ginsu[i].data+0x1c)==e->ginsu[i].gin+0x20);
                assert(M32(e->ginsu[i].synth+0x28)==e->ginsu[i].data);
                ++matched_gins;
            }
        }
        for(unsigned tick=0;tick<32;tick++) {
            raw_rpm=1400+(tick%12)*420; MF32(0x28000+0xb8)=(float)raw_rpm;
            c.ecx=0x20000; f_004EE3E0(&c);
            if(e) for(unsigned k=0;k<2;k++) {
                const uint8_t *m=e->instances[k].engine.members; assert(m);
                assert(ri(m)==87 && ri(m+4)>0 && ri(m+8)==512 && ri(m+12)==512);
                assert(ri(m+4*15)==16384 && ri(m+4*28)==16384 && ri(m+4*21)==12000);
                assert(ri(m+4*84)==ri(e->package->calibration[1]+0x74));
            }
            for(unsigned k=0;k<4;k++) pump(&c);
        }
        c.ecx=0x20000; f_004F5D60(&c); assert(!engine(0x20000));
        for(unsigned i=0;i<128;i++) assert(!transport[i].live);
        for(unsigned i=0;i<512;i++) assert(!voices[i].owned);
        car_audio_unload(77); assert(allocated==freed);
        if(!cycle) assert(car_audio_load(77,"jaguarxk",argv[1])==1);
    }
    assert(car_audio_load(77,"jaguarxk",argv[1])==1);
    setup(77); c.ecx=0x20000; f_004EDD90_orig(&c); c.esp=0x1f00000; f_004DC360(&c);
    Engine *aems=engine(0x20000); unsigned aems_ok=aems && aems->instances[0].bank && aems->instances[1].bank;
    for(unsigned tick=0;tick<4;tick++) {
        diag_on=(int)(tick&1); category=39; object=0x55555;
        c.ecx=0x20000; f_004EDD90(&c); assert(category==39 && object==0x55555); for(unsigned k=0;k<4;k++) pump(&c);
    }
    diag_on=0; c.ecx=0x20000; f_004DC1D0(&c); assert(!engine(0x20000) && !M32(0x20000+0x28));
    setup(77); c.ecx=0x20000; f_004EDD90_orig(&c); c.esp=0x1f00000; f_004DC360(&c);
    c.ecx=0x20000; f_004EDD90(&c); c.ecx=0x20000; f_004F5CD0(&c); assert(!engine(0x20000)); car_audio_unload(77);
    for(unsigned i=0;i<128;i++) assert(!transport[i].live);
    for(unsigned i=0;i<512;i++) assert(!voices[i].owned);
    assert(allocated==freed);
    printf("{\"resource_matches\":%u,\"expected\":2,\"reload_cycles\":2,\"aems_lifecycle\":%u,\"source_gins\":%u,\"nonzero_pcm\":%u,\"bank_pcm\":%u,\"ginsu_pcm\":%u,\"pcm_frames\":%u,\"energy\":%.9g,\"source_peak\":%.9g,\"allocations\":%u,\"freed\":%u,\"packet_creates\":%u,\"packet_destroys\":%u,\"off_stock_bytes\":%u,\"off_pcm_sha256\":\"%s\"}\n",
        matched/2,aems_ok,matched_gins/2,nonzero_frames,nonzero_bank,nonzero_ginsu,sample_frames,total_energy,source_peak,allocated,freed,packets_created,packets_destroyed,stock_frames,off_pcm_sha256);
    free(stock_source[0]); free(stock_source[1]); free(G); return 0;
}
'''


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


# Only the guest packet transport and car observations are substitutes. The
# loader, imports, interpreter, engine hooks and all source lifetimes are real.
SAFETY = r'''
#include "host.h"
#include <assert.h>
#include <limits.h>
#include <stdarg.h>
#include <stdlib.h>
#define abort() _Exit(95)
#include "car_audio.c"
uint8_t *G;
int diag_on;
static unsigned checks,allocations,frees,donor_releases,donor_creates,stolen,rearms;
static uint32_t heap=0x1000000,generation=0x100;
static const uint32_t pool=0x80000,object=0x20000,member=0x24000;
static struct { int live,playing; uint32_t token; } packet[128];
static uint32_t blocks[4096];
static unsigned nblocks;
static void check(int ok,const char *what) { ++checks; if(!ok) { fprintf(stderr,"FAIL %s\n",what); exit(1); } }
#define CHECK(x) check(!!(x),#x)
void diag_event(const char *fmt,...) { (void)fmt; }
uint32_t gmalloc_try(uint32_t n) {
    uint32_t at=heap; heap+=(n+63)&~63u; CHECK(heap<0x1e00000 && nblocks<4096);
    memset(G+at,0,n); blocks[nblocks++]=at; ++allocations; return at;
}
uint32_t gmalloc(uint32_t n) { return gmalloc_try(n); }
void gfree(uint32_t at) {
    unsigned i=0; while(i<nblocks && blocks[i]!=at) ++i; CHECK(i<nblocks);
    blocks[i]=0; ++frees;
}
uint32_t host_fn(HostFn fn,const char *name) { (void)fn;(void)name; return 0xf1000000; }
static int valid_voice(uint32_t token) {
    unsigned i=token&255;
    return i<M16(0x9c218c) && M8(pool+i*0x90+0x6d) && M32(pool+i*0x90)==token;
}
static uint32_t admit(void) {
    unsigned n=M8(0x9c204a),i=0;
    while(i<n && M8(pool+i*0x90+0x6d)) ++i;
    if(i==n) {
        /* 0081F413's packet class 101 can steal priorities below 101. */
        for(i=0;i<n;i++) if(M8(pool+i*0x90+0x2a)<101) break;
        if(i==n) return UINT32_MAX;
        ++stolen;
    }
    uint32_t token=(generation+=0x100)|i;
    M32(pool+i*0x90)=token; M8(pool+i*0x90+0x6d)=1;
    M8(pool+i*0x90+0x2a)=101; M8(pool+i*0x90+0x26)=0;
    return token;
}
static void stop_packet(unsigned p) {
    CHECK(p<128 && packet[p].live);
    if(valid_voice(packet[p].token)) M8(pool+(packet[p].token&255)*0x90+0x6d)=0;
    packet[p].playing=0;
}
static uint32_t make_packet(void) {
    unsigned p=0; while(p<128 && packet[p].live) ++p; CHECK(p<128);
    packet[p].live=1; packet[p].token=UINT32_MAX; return p;
}
static void native(Cpu *c,uint32_t target) {
    uint32_t a=arg(c,0);
    if(target==0xdead0001) { x87_push(c,3000); c->esp+=4; return; }
    if(target==0x81dafd) c->eax=512;
    else if(target==0x81db11) c->eax=make_packet();
    else if(target==0x81dbad) {
        CHECK(a<128 && packet[a].live); uint32_t token=admit();
        packet[a].token=token; packet[a].playing=(int32_t)token>=0; c->eax=token;
    } else if(target==0x81e274 || target==0x81e063) {
        stop_packet(a); if(target==0x81e063) packet[a].live=0; c->eax=0;
    } else if(target==0x81deee) c->eax=0;
    else if(target==0x81e2cb || target==0x81d582) { memset(G+a,0,target==0x81e2cb?104:24); c->eax=0; }
    else if(target==0x81f7cb) c->eax=valid_voice(a)?a&255:UINT32_MAX;
    else if(target==0x81d7ef) {
        if(valid_voice(a)) M32(pool+(a&255)*0x90+0x38)=arg(c,1); c->eax=0;
    } else if(target==0x81e3cb || target==0x81e318 || target==0x81e557 || target==0x81e4be || target==0x81e469) c->eax=0;
    else if(target==0x831782) { ++donor_releases; c->eax=0; }
    else if(target==0x831a8a) { CHECK(a==0x914824); M32(arg(c,2))=123; ++donor_creates; c->eax=0; }
    else if(target==0x83198b) c->eax=0;
    else if(target==0x4e1280) { M32(c->ecx+0x1c)=a+0x20; c->eax=1; }
    else if(target==0x4e8be0) {
        M32(c->ecx+0x1c)=make_packet(); M32(c->ecx+0x20)=UINT32_MAX; c->eax=c->ecx;
    } else if(target==0x4e1c10) { M32(c->ecx+0x28)=a; c->eax=1; }
    else if(target==0x4e8ca0) {
        float frequency; memcpy(&frequency,&a,4); CHECK(isfinite(frequency) && frequency>0);
        unsigned p=M32(c->ecx+0x1c); if(packet[p].playing) stop_packet(p);
        c->eax=admit(); packet[p].token=c->eax; packet[p].playing=(int32_t)c->eax>=0;
        M32(c->ecx+0x20)=c->eax; M32(c->ecx+0x24)=packet[p].playing?44100:0; ++rearms;
    } else if(target==0x4e1d70 || target==0x4e8c50) {
        unsigned p=M32(c->ecx+0x1c); stop_packet(p); M32(c->ecx+0x24)=0;
        if(target==0x4e8c50) packet[p].live=0; c->eax=0;
    } else if(target==0x4e1cd0) c->eax=0;
    else { fprintf(stderr,"unhandled %x\n",target); exit(2); }
    c->esp+=4;
}
void dispatch(Cpu *c,uint32_t target) {
    if(target==0x825310) { f_00825310(c); return; }
    if(!car_audio_dispatch(c,target)) native(c,target);
}
int code_region_of(uint32_t target) { return car_audio_code_region(target)?0:-1; }
void guest_divide_error(Cpu *c,uint32_t at) { (void)c;(void)at; exit(2); }
uint32_t call_guest(Cpu *c,uint32_t target,int n,const uint32_t *args) {
    uint32_t sp=c->esp;
    for(int i=n-1;i>=0;i--) { c->esp-=4; M32(c->esp)=args[i]; }
    c->esp-=4; M32(c->esp)=0xfeedf00d; dispatch(c,target); c->esp=sp; return c->eax;
}
static void original(Cpu *c) { c->eax=0xabcdef01; c->esp+=4; }
void f_004DC360_orig(Cpu *c) { M32(member)=123; original(c); }
void f_004EE1C0_orig(Cpu *c) {
    M32(member)=123;
    for(unsigned i=0;i<2;i++) {
        uint32_t synth=0x31000+i*0x100; memset(G+synth,0,0x44);
        uint32_t data=0x30000+i*0x100; MF32(data+4)=1000; M32(object+0x54+i*0x18)=data;
        M32(synth+0x1c)=make_packet(); uint32_t rpm=float_word(3000);
        M32(object+0x58+i*0x18)=synth;
        M32(object+0x68+i*0x18)=native_call(c,0x4e8ca0,synth,1,&rpm);
    }
    original(c);
}
static void controller(Cpu *c) {
    if(M8(object+8)) M32(object+0x84)=M32(0x28000+0xb8);
    M32(member+4+3*4)=512; M32(member+4+5*4)=0; M32(member+4+12*4)=1;
    M32(member+4+14*4)=8192; M32(member+4+15*4)=16384; M32(member+4+21*4)=12000;
    for(unsigned i=0;i<2;i++) {
        uint32_t voice=M32(object+0x68+i*0x18),v[]={voice,127};
        if(M32(object+0x58+i*0x18)) native_call(c,0x81d7ef,0,2,v);
    }
    original(c);
}
void f_004F5CD0_orig(Cpu *c) { M32(member)=0; original(c); }
void f_004DC1D0_orig(Cpu *c) { M32(object+0x28)=0; M32(member)=0; original(c); }
void f_004DC670_orig(Cpu *c) {
    for(unsigned i=0;i<2;i++) { uint32_t v[]={M32(object+0x68+i*0x18),0}; native_call(c,0x81d7ef,0,2,v); }
    f_004DC1D0_orig(c);
}
void f_004F5D60_orig(Cpu *c) {
    for(unsigned i=0;i<2;i++) {
        uint32_t synth=M32(object+0x58+i*0x18); if(synth) native_call(c,0x4e8c50,synth,0,NULL);
    }
    memset(G+object+0x54,0,0x30); M32(member)=0; original(c);
}
void f_00825310_orig(Cpu *c) {
    for(unsigned i=0;i<arg(c,0);i++) MF32(arg(c,2)+4*i)=(int16_t)M16(arg(c,1)+2*i);
    c->esp+=4;
}
typedef struct { uint32_t key; char name[16],directory[PATH_MAX]; } TestAddition;
static unsigned fatal_calls;
static void fail(const char *why) { (void)why; ++fatal_calls; }
static void vault_audio(const TestAddition *a) { VAULT_AUDIO }
static uint32_t get(void) { uint32_t v; CHECK(fread(&v,4,1,stdin)==1); return v; }
static uint8_t *read_bytes(uint32_t n) { uint8_t *b=malloc(n); CHECK(b && fread(b,1,n,stdin)==n); return b; }
static void setup(uint32_t key) {
    memset(G+object,0,0x10000);
    M32(0x9c21f4)=pool; M16(0x9c218c)=64; M8(0x9c204a)=64;
    M8(object+8)=1; M32(object+0x14)=0x21000; M32(0x21000+0x34)=0x22000;
    M32(0x22000+0x204)=0x23000; M32(0x23000+0x20)=key;
    M32(object+0x28)=member; M32(member)=123;
    M32(object+0x38)=0x25000; M32(0x25000)=0x26000; M32(0x26000+0x44)=0xdead0001;
    M32(object+0x10)=0x27000; M32(0x27000)=32767u<<16|16384; M32(0x27000+4)=32767;
    M32(object+0x40)=0x28000; MF32(0x28000+0xb8)=3000;
    M32(0x28000+0x3c)=32767; M32(0x28000+0x40)=16384;
    MF32(object+0x3c)=1; MF32(object+0x84)=3000;
}
static void tick(Cpu *c) { c->ecx=object; controller(c); car_audio_tick(c,object); }
static void init(Cpu *c,int ginsu) {
    setup(77); controller(c); c->esp=0x1f00000; c->ecx=object;
    if(ginsu) f_004EE1C0(c); else f_004DC360(c);
    CHECK(engine(object)!=NULL); tick(c);
    for(unsigned i=0;i<4;i++) {
        uint32_t v=(uint32_t)bridge.stream[i].voice;
        CHECK(M8(pool+(v&255)*0x90+0x2a)==101 && !M8(pool+(v&255)*0x90+0x26));
        if(ginsu) {
            v=(uint32_t)engine(object)->ginsu[i].voice;
            CHECK(M8(pool+(v&255)*0x90+0x2a)==101 && !M8(pool+(v&255)*0x90+0x26));
        }
    }
}
static double energy(void) {
    bridge_render(bridge.produced); double n=0;
    unsigned slot=(unsigned)((bridge.produced-1)%PACKET_CACHE);
    for(unsigned ch=0;ch<4;ch++) for(unsigned i=0;i<PACKET_FRAMES;i++) n+=fabs(bridge.cache[slot][ch][i]);
    return n;
}
static void full_pool_recovery(Cpu *c,int ginsu) {
    init(c,1); Engine *e=engine(object);
    uint32_t old=(uint32_t)(ginsu?e->ginsu[0].voice:bridge.stream[0].voice);
    uint32_t other[64]={0};
    for(unsigned i=0;i<64;i++) if(!M8(pool+i*0x90+0x6d) || i==(old&255)) {
        other[i]=(generation+=0x100)|i;
        M32(pool+i*0x90)=other[i]; M8(pool+i*0x90+0x6d)=1;
        M8(pool+i*0x90+0x2a)=80; M32(pool+i*0x90+0x38)=73;
    }
    unsigned before=donor_creates,starts=rearms;
    for(unsigned t=0;t<3;t++) {
        tick(c); CHECK(engine(object)==e && !M32(member) && donor_creates==before);
        CHECK(!energy() && rearms==starts);
        if(!ginsu) CHECK(!bridge.live);
        for(unsigned k=0;k<4;k++) if(valid_voice((uint32_t)e->ginsu[k].voice))
            CHECK(!M32(pool+(e->ginsu[k].voice&255)*0x90+0x38));
        for(unsigned i=0;i<64;i++) if(other[i]) {
            CHECK(valid_voice(other[i])); CHECK(M32(pool+i*0x90+0x38)==73);
        }
    }
    /* Stopping the bridge freed three of its four slots. One unrelated
     * release must make exactly enough room for either recovery path. */
    CHECK(!free_native_voices(ginsu?1:4));
    unsigned released=0;
    for(unsigned i=0;i<64 && !released;i++) if(other[i] && i!=(old&255)) {
        M8(pool+i*0x90+0x6d)=0; other[i]=0; ++released;
    }
    CHECK(released==1 && free_native_voices(ginsu?1:4));
    CHECK(!free_native_voices(ginsu?2:5)); tick(c);
    CHECK(engine(object)==e && bridge.live && energy()>0 && donor_creates==before);
    CHECK(valid_voice((uint32_t)(ginsu?e->ginsu[0].voice:bridge.stream[0].voice)));
    CHECK((uint32_t)(ginsu?e->ginsu[0].voice:bridge.stream[0].voice)!=old);
    c->ecx=object; f_004F5D60(c);
    for(unsigned i=0;i<64;i++) if(other[i]) {
        CHECK(valid_voice(other[i]) && M32(pool+i*0x90+0x38)==73);
        M8(pool+i*0x90+0x6d)=0;
    }
}
static void poison(Cpu *c,int import) {
    Engine *e=engine(object); CHECK(e!=NULL);
    if(import==1) {
        CarAudioRuntimeInstance *p=e->instances;
        p->state[p->module->players[0].offset+15]=1;
    } else if(import==2) MF32(0x28000+0xb8)=NAN;
    else M32(object+0x38)=0;
    unsigned before=donor_creates; tick(c);
    CHECK(!engine(object) && !bridge.live && M32(member)==123 && donor_creates==before+1);
    for(unsigned i=0;i<2;i++) {
        uint32_t token=M32(object+0x68+i*0x18); CHECK(valid_voice(token));
        CHECK(M32(M32(object+0x58+i*0x18)+0x24)!=0);
    }
    tick(c);
    for(unsigned i=0;i<2;i++) CHECK(M32(pool+(M32(object+0x68+i*0x18)&255)*0x90+0x38)==127);
    c->ecx=object; f_004F5D60(c);
}
static void duplicate_destroy(void) {
    uint8_t state[32]={0}; state[12]=1; state[24]=0x7b;
    CarAudioRuntimeControl control={.offset=16};
    CarAudioRuntimeModule mod={.size=32,.destroy_offset=0,.control_count=1};
    CarAudioRuntimeBank b={0};
    CarAudioRuntimeInstance p={.bank=&b,.module=&mod,.state=state,.address=0x1000,
        .controls=&control,.destroying=1,.executing=1};
    uint32_t result=123; CHECK(!car_audio_runtime_import(&p,4,p.address,&result));
    CHECK(state[24]==0x7b && p.destroying && p.bank==&b && !result);
}
static void snapshot_pcm(Cpu *c,int16_t pcm[512]) {
    /* Deterministic stock observation, independently of the added backend. */
    unsigned gain=M32(member)?32767:0;
    for(unsigned i=0;i<512;i++) pcm[i]=(int16_t)((int)(i%31)-15)*(int)gain/32;
    for(unsigned i=0;i<512;i++) M16(0x90000+2*i)=(uint16_t)pcm[i];
    uint32_t args[]={512,0x90000,0x92000}; native_call(c,0x825310,0,3,args);
    for(unsigned i=0;i<512;i++) CHECK(MF32(0x92000+4*i)==pcm[i]);
}
static unsigned identity(Cpu *c,uint32_t key,int enabled,int mode) {
    int ginsu=mode&1,detach=mode&2;
    if(enabled<0) { unsetenv("NFS_ADDCAR_AUDIO"); CHECK(car_audio_enabled()); }
    else setenv("NFS_ADDCAR_AUDIO",enabled==2?"0":enabled?"on":"off",1);
    uint8_t expected[0x10000]; int16_t pcm[512],actual[512];
    setup(key); c->ecx=object; Cpu before=*c;
    if(ginsu) f_004EE1C0_orig(c); else f_004DC360_orig(c);
    controller(c); snapshot_pcm(c,pcm);
    if(detach) { if(ginsu) f_004DC670_orig(c); else f_004DC1D0_orig(c); }
    if(ginsu) f_004F5D60_orig(c); else f_004F5CD0_orig(c);
    Cpu cpu=*c; memcpy(expected,G+object,sizeof expected);
    setup(key); *c=before;
    if(ginsu) f_004EE1C0(c); else f_004DC360(c);
    controller(c); car_audio_tick(c,object); snapshot_pcm(c,actual);
    if(detach) { if(ginsu) f_004DC670(c); else f_004DC1D0(c); }
    if(ginsu) f_004F5D60(c); else f_004F5CD0(c);
    CHECK(!memcmp(pcm,actual,sizeof pcm)); CHECK(!memcmp(expected,G+object,sizeof expected));
    CHECK(!memcmp(&cpu,c,sizeof cpu)); CHECK(!engine(object));
    return sizeof pcm+sizeof expected+sizeof cpu;
}
int main(int argc,char **argv) {
    CHECK(argc==3); G=calloc(1,0x2000000); CHECK(G!=NULL);
    M32(0x9c21f4)=pool; M16(0x9c218c)=64;
    Package *p=packages; p->count=3; p->key=77;
    uint8_t cal[2][CALIBRATION]={{0}},gin[64]={0};
    memcpy(gin,"Gnsu20",6); float lo=1000,hi=8000; memcpy(gin+8,&lo,4);
    uint8_t *sources[2],*plans[2];
    CHECK(!car_audio_backend_init(&backend,voices,512,44100,resolve,NULL));
    for(unsigned k=0;k<2;k++) {
        Resource *r=p->resource+k;
        r->size=get(); r->plan_size=get(); sources[k]=read_bytes(r->size); plans[k]=read_bytes(r->plan_size);
        r->source=sources[k]; r->plan=plans[k]; r->kind=2; CHECK(!plan(r));
        digest(r->source,r->size,r->digest);
        r->sample_count=2; r->samples=calloc(2,sizeof(*r->samples)); CHECK(r->samples!=NULL);
        for(unsigned j=0;j<2;j++) {
            float *pcm=malloc(8*sizeof(float)); CHECK(pcm!=NULL);
            for(unsigned n=0;n<8;n++) pcm[n]=(n&1)?0.25f:-0.5f;
            r->samples[j]=(CarAudioPCM){pcm,8,44100,1,NULL,NULL};
        }
        memcpy(cal[k]+0x120,&lo,4); memcpy(cal[k]+0xe8,&hi,4);
        uint32_t unity=32767; memcpy(cal[k]+0x128,&unity,4);
        p->calibration[k]=cal[k]; p->layer[k][0]=k; p->layer[k][1]=p->layer[k][2]=2;
    }
    p->resource[2].source=gin; p->resource[2].size=sizeof gin; p->resource[2].kind=1;
    digest(gin,sizeof gin,p->resource[2].digest);
    Cpu c={0}; c.esp=0x1f00000; c.cw=0x37f;
    unsigned identity_bytes=0;
    for(unsigned k=0;k<4;k++) {
        identity_bytes+=identity(&c,77,0,k); identity_bytes+=identity(&c,999,1,k);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"default-stock")) {
        unsigned bytes=0;
        for(unsigned k=0;k<4;k++) bytes+=identity(&c,999,-1,k);
        printf("default-stock: %u identical CPU/guest/PCM bytes (4096 PCM)\n",bytes);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"default-added")) {
        unsetenv("NFS_ADDCAR_AUDIO");
        for(unsigned ginsu=0;ginsu<2;ginsu++) {
            init(&c,(int)ginsu); Engine *e=engine(object);
            CHECK(e->package==packages && e->instances[0].bank==&packages[0].resource[0].bank);
            CHECK(e->instances[1].bank==&packages[0].resource[1].bank);
            CHECK(energy()>0); c.ecx=object;
            if(ginsu) f_004F5D60(&c); else f_004F5CD0(&c);
        }
        printf("default-added: 2/2 constructors use source engine/exhaust\n");
    }
    for(unsigned i=0;i<2;i++) {
        setenv("NFS_ADDCAR_AUDIO",i?"0":"off",1); CHECK(!car_audio_enabled());
        for(unsigned k=0;k<4;k++) identity_bytes+=identity(&c,77,i?2:0,k);
    }
    setenv("NFS_ADDCAR_AUDIO","on",1);
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"pack")) {
        TestAddition a={.key=123}; strcpy(a.name,"jaguarxk");
        CHECK(snprintf(a.directory,sizeof a.directory,"%s",argv[2])<(int)sizeof a.directory);
        char path[PATH_MAX]; CHECK(snprintf(path,sizeof path,"%s/AUDIO.BIN",a.directory)<(int)sizeof path);
        FILE *bad=fopen(path,"wb"); CHECK(bad!=NULL);
        for(unsigned i=0;i<100;i++) fputc((int)i,bad); CHECK(!fclose(bad));
        CHECK(car_audio_load(a.key,a.name,path)<0); vault_audio(&a); CHECK(!fatal_calls && !for_object(0));
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"tick")) { init(&c,1); poison(&c,0); }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"import")) { init(&c,1); poison(&c,1); }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"raw")) { init(&c,1); poison(&c,2); }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"activity") || !strcmp(argv[1],"source-activity") || !strcmp(argv[1],"mix")) {
        init(&c,1); Engine *e=engine(object); double full=energy(); CHECK(full>0);
        M32(0x27000)=16384u<<16|16384; M32(0x27000+4)=16384;
        double half=energy(); CHECK(fabs(half/full-16384.0/32767)<1e-6);
        M8(object+8)=0; car_audio_tick(&c,object); CHECK(energy()==0);
        for(unsigned i=0;i<4;i++) CHECK(!M32(pool+(e->ginsu[i].voice&255)*0x90+0x38));
        for(unsigned i=0;i<4;i++) CHECK(!M32(pool+(bridge.stream[i].voice&255)*0x90+0x38));
        M8(object+8)=1; M32(0x27000)=32767u<<16|16384; M32(0x27000+4)=32767;
        tick(&c); CHECK(energy()>0);
        M32(0x27000)=16384; M32(0x27000+4)=0; tick(&c); CHECK(!energy());
        for(unsigned i=0;i<4;i++) CHECK(!M32(pool+(e->ginsu[i].voice&255)*0x90+0x38));
        c.ecx=object; f_004F5D60(&c);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"pool")) {
        for(unsigned i=0;i<64;i++) { M8(pool+i*0x90+0x6d)=1; M8(pool+i*0x90+0x2a)=80+i%20; }
        unsigned before=stolen; CHECK(bridge_start(&c)<0); CHECK(stolen==before && !bridge.live);
        memset(G+pool,0,64*0x90);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"pool-range")) {
        M8(0x9c204a)=16; M8(0x9c204b)=0xa5;
        for(unsigned i=0;i<16;i++) M8(pool+i*0x90+0x6d)=1;
        CHECK(!free_native_voices(1)); CHECK(bridge_start(&c)<0 && !bridge.live);
        M8(pool+15*0x90+0x6d)=0; CHECK(free_native_voices(1) && !free_native_voices(2));
        memset(G+pool,0,64*0x90); M8(0x9c204a)=64;
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"gain-rise")) {
        init(&c,0); Engine *e=engine(object); double full=energy(); CHECK(full>0);
        e->mix_gain=1; CHECK(fabs(energy()/full-1)<1e-6);
        e->mix_gain=0; CHECK(!energy());
        tick(&c); CHECK(energy()>0); c.ecx=object; f_004F5CD0(&c);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"bridge-full")) full_pool_recovery(&c,0);
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"ginsu-full")) full_pool_recovery(&c,1);
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"groups")) {
        init(&c,1); double one=energy(); uint32_t other=0x40000;
        memcpy(G+other,G+object,0x100); memcpy(G+0x44000,G+member,0x6c);
        M32(other+0x28)=0x44000; M32(0x44000)=123;
        M32(other+0x10)=0x47000; memcpy(G+0x47000,G+0x27000,16);
        init_engine(&c,other,0); CHECK(engine(other)!=NULL); car_audio_tick(&c,other);
        CHECK(fabs(energy()/one-2)<1e-6);
        M32(0x47000)=16384u<<16|16384;
        CHECK(fabs(energy()/one-(1+16384.0/32767))<1e-6);
        M8(object+8)=0; M8(other+8)=0; car_audio_tick(&c,object);
        CHECK(fabs(energy()/one-16384.0/32767)<1e-6); /* AEMS ignores +8. */
        for(unsigned i=0;i<4;i++) CHECK(M32(pool+(bridge.stream[i].voice&255)*0x90+0x38)==127);
        detach_engine(&c,engine(other),0);
        CHECK(energy()==0); c.ecx=object; f_004F5D60(&c);
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"recover")) {
        init(&c,1); Engine *e=engine(object);
        unsigned bp=(unsigned)bridge.stream[0].packet; uint32_t old=packet[bp].token;
        uint32_t speech=(generation+=0x100)|(old&255);
        M32(pool+(old&255)*0x90)=speech; M8(pool+(old&255)*0x90+0x2a)=90; tick(&c);
        CHECK(bridge.live && valid_voice((uint32_t)bridge.stream[0].voice));
        CHECK((uint32_t)bridge.stream[0].voice!=old && valid_voice(speech));
        old=(uint32_t)e->ginsu[0].voice; unsigned before=rearms;
        uint32_t siren=(generation+=0x100)|(old&255);
        M32(pool+(old&255)*0x90)=siren; M8(pool+(old&255)*0x90+0x2a)=80; tick(&c);
        CHECK(rearms==before+1 && valid_voice((uint32_t)e->ginsu[0].voice));
        CHECK(valid_voice(speech) && valid_voice(siren));
        c.ecx=object; f_004DC670(&c); CHECK(!engine(object) && !bridge.live && !M32(object+0x28));
        c.ecx=object; f_004F5D60(&c);
        CHECK(valid_voice(speech) && valid_voice(siren));
        M8(pool+(speech&255)*0x90+0x6d)=0; M8(pool+(siren&255)*0x90+0x6d)=0;
    }
    if(!strcmp(argv[1],"all") || !strcmp(argv[1],"destroy")) duplicate_destroy();
    CHECK(!stolen); car_audio_unload(77);
    CHECK(allocations==frees);
    for(unsigned k=0;k<2;k++) { free(sources[k]); free(plans[k]); }
    printf("{\"checks\":%u,\"identity_bytes\":%u,\"pcm_bytes\":16384,\"allocations\":%u,\"frees\":%u,\"stolen\":%u}\n",checks,identity_bytes,allocations,frees,stolen);
    free(G); return 0;
}
'''


def vault_audio_block(source):
    start = source.index('if (car_audio_load && a->directory[0]) {')
    at = source.index('{', start)
    depth = 1
    while depth:
        at += 1
        depth += (source[at] == '{') - (source[at] == '}')
    return source[start:at+1]


def safety(mutant=None, case=None):
    proof = module(ROOT / 'runtime/car_audio_runtime_test.py', 'live_fixture')
    payload = bytearray()
    for name in ('CAR_ENG', 'CAR'):
        data, manifest = proof.generated_fixture(name)
        plan = pack.execution_plan(manifest)
        payload += struct.pack('<II', len(data), len(plan)) + data + plan
    live = (ROOT / 'runtime/car_audio.c').read_text()
    runtime = (ROOT / 'runtime/car_audio_runtime.c').read_text()
    vault = vault_audio_block((ROOT / 'runtime/car85.c').read_text())
    changes = {
        'import': ('if(import_escape) longjmp(*import_escape,1);', 'abort();'),
        'tick': ('if(restore && replaced) restore_stock(c,object,ginsu);', '(void)restore; (void)replaced; (void)object; (void)ginsu;'),
        'activity': ('sync_ginsu_volume(c,e);\n    sync_bridge_volume(c);\n    if(!engine_active(e))',
                     'sync_bridge_volume(c);\n    if(!engine_active(e))'),
        'source-activity': ('float gain=engine_active(e) && e->mix_gain && !e->recovering?', 'float gain=e->mix_gain && !e->recovering?'),
        'raw': ('if(!isfinite(raw)) return -1;', 'if(!isfinite(raw)) return 0;'),
        'mix': ('voices[v].volume*=gain;', '(void)gain;'),
        'pool': ('if(!free_native_voices(4)) return -1;', '/* old packet admission */'),
        'recover': ('int recovery=bridge_recover(c),ginsu=recover_ginsu(c,e);', 'int recovery=0,ginsu=0;'),
        'recover-ginsu': ('int recovery=bridge_recover(c),ginsu=recover_ginsu(c,e);', 'int recovery=bridge_recover(c),ginsu=0;'),
        'bridge-full': ('if(!bridge.live && !free_native_voices(4)) return 1;', 'if(!bridge.live && !free_native_voices(4)) return -1;'),
        'ginsu-full': ('if(!free_native_voices(1)) return 1;', 'if(!free_native_voices(1)) return -1;'),
        'pool-range': ('unsigned count=M8(0x009c204a),free=0;', 'unsigned count=M16(0x009c218c),free=0;'),
        'gain-rise': ('fminf(1,(float)engine_mix_gain(e)/e->mix_gain)', '(float)engine_mix_gain(e)/e->mix_gain'),
        'off': ('if(!car_audio_enabled() || engine(object)) return;', 'if(engine(object)) return;'),
        'stock': ('if(key && packages[i].key==key) return packages+i;', 'if(key) return packages+i;'),
        'ginsu-detach': ('if(e) detach_engine(c,e,0); f_004DC670_orig(c);', 'f_004DC670_orig(c);'),
    }
    if mutant in changes:
        old, new = changes[mutant]
        assert live.count(old) == (2 if mutant == 'pool' else 1), mutant
        live = live.replace(old, new)
    elif mutant in ('default-stock', 'default-added'):
        old = 'return !value || !strcasecmp(value,"on") || !strcmp(value,"1");'
        assert live.count(old) == 1
        live = live.replace(old, 'return value && !strcasecmp(value,"on");')
    elif mutant == 'destroy':
        old = 'if(p->destroying) return 0;'
        assert runtime.count(old) == 1
        runtime = runtime.replace(old, '')
    elif mutant == 'pack':
        vault = '''if (car_audio_load && a->directory[0]) {
            char path[PATH_MAX];
            if (snprintf(path,sizeof path,"%s/AUDIO.BIN",a->directory)>=(int)sizeof path ||
                car_audio_load(a->key,a->name,path)<0) fail("invalid added-car audio sidecar");
        }'''
    elif mutant:
        raise AssertionError(mutant)
    with tempfile.TemporaryDirectory(prefix='car-audio-safe-') as directory:
        folder = Path(directory)
        (folder / 'car_audio.c').write_text(live)
        (folder / 'adapter.c').write_text(runtime)
        (folder / 'harness.c').write_text(SAFETY.replace('VAULT_AUDIO', vault))
        command = [shutil.which('clang') or 'clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g',
                   '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                   '-Wno-pointer-bool-conversion', '-Wno-deprecated-declarations', '-ffp-contract=off',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I'+str(folder), '-I'+str(ROOT / 'runtime'), '-I'+str(ROOT / 'recomp'),
                   str(folder / 'harness.c'), str(folder / 'adapter.c'), str(ROOT / 'runtime/interp.c'),
                   *[str(ROOT / f'runtime/car_audio_{n}.c') for n in ('csis', 'engine_class', 'player', 'backend', 'filter')],
                   '-o', str(folder / 'safety')]
        built = subprocess.run(command, capture_output=True, text=True, timeout=90)
        assert built.returncode == 0, built.stderr[-6000:]
        case = case or {'ginsu-detach': 'recover', 'recover-ginsu': 'recover', 'off': 'identity', 'stock': 'identity'}.get(mutant, mutant or 'all')
        result = subprocess.run([str(folder / 'safety'), case, str(folder)], input=payload,
                                capture_output=True, timeout=30)
        if mutant:
            assert result.returncode != 0, f'old {mutant} behavior unexpectedly passed'
            print(f'rejected old {mutant}: exit {result.returncode}')
            return None
        assert result.returncode == 0, result.stderr.decode()[-6000:]
        lines = result.stdout.decode().splitlines()
        for line in lines[:-1]:
            print(line)
        row = json.loads(lines[-1])
        assert row['identity_bytes'] > 0 and row['allocations'] == row['frees'] and not row['stolen']
        print(json.dumps(row, sort_keys=True))
        return row


def native_fragments(proof, source_exe):
    texts = '\n'.join((ROOT / f'recomp/out/speed_{n}.c').read_text() for n in (13, 14, 55))
    found = {}
    for name, sha in {**GINSU, **PCM_NATIVE}.items():
        match = re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', texts, re.S)
        body = match[0].replace('f_'+name+'_orig(', 'f_'+name+'(') if match else ''
        assert body and hashlib.sha256(body.encode()).hexdigest() == sha, name
        found[name] = body.replace('void f_'+name+'(', 'void f_'+name+'_orig(') if name == '00825310' else body
    services = sorted({name for body in found.values() for name in re.findall(r'f_([A-F0-9]{8})\(c\)', body)} - set(found))
    declarations = '\n'.join(f'void f_{n}(Cpu *);' for n in [*found, *services]) + '\nvoid f_00825310_orig(Cpu *);'
    code = proof.private_code(source_exe) + '\n' + declarations + '\n' + '\n'.join(found.values())
    dispatch = '\n'.join(f'if(t==0x{n}) {{ f_{n}(c); return; }}' for n in found)
    dispatch += '\n' + '\n'.join(f'if(t==0x{target:08X}) {{ f_{target:08X}(c); return; }}'
        for ordinal, (_, target, _, _) in proof.abi.ARITHMETIC.items())
    declarations += '\n' + '\n'.join(f'void f_{target:08X}(Cpu *);' for _, target, _, _ in proof.abi.ARITHMETIC.values())
    stubs = '\n'.join(f'void f_{n}(Cpu *c) {{ dispatch(c,0x{n}); }}' for n in services)
    return code, declarations, dispatch, stubs


def run(original=False, mutant=None):
    proof = module(ROOT / 'runtime/car_audio_runtime_test.py', 'runtime_proof')
    inputs = proof.private_inputs()
    if inputs is None:
        return None
    resources, _, source_exe, native_exe = inputs
    source = input_path('NFSC')
    data = pack.build((source / 'GLOBAL/attributes.bin').read_bytes(), 'jaguarxk', resources)
    with tempfile.TemporaryDirectory(prefix='car-audio-live-') as directory:
        folder = Path(directory)
        (folder / 'AUDIO.BIN').write_bytes(data)
        (folder / 'memory.bin').write_bytes(proof.memory_bytes(native_exe))
        code, declarations, dispatch, stubs = native_fragments(proof, source_exe)
        (folder / 'native.c').write_text(code)
        live = (ROOT / 'runtime/car_audio.c').read_text()
        if original:
            live = live.replace('init_engine(c,object,1);', '').replace('init_engine(c,object,0);', '')
        changes = {
            'serving': ('return fd;', 'close(fd); return -1;'),
            'secondary': ('for(unsigned k=0;k<2;k++) {\n        Resource *r=p->resource+p->layer[k][0]; int32_t words[100];',
                          'for(unsigned k=0;k<1;k++) {\n        Resource *r=p->resource+p->layer[k][0]; int32_t words[100];'),
            'pcm': ('car_audio_backend_render(&backend,dry,wet,PACKET_FRAMES);', '(void)dry; (void)wet;'),
            'gin': ('uint32_t gin=e->ginsu[k].gin;', 'uint32_t gin=e->ginsu[0].gin;'),
            'descriptor': ('gmalloc_try(160)', 'gmalloc_try(64)'),
            'profile': ('if(strcmp(package_id,', 'if(0 && strcmp(package_id,'),
            'defer': ('engines[i].package->retiring=1; return;', 'return;'),
            'gain': ('M16(attr+8)=4; M8(play)=127;', 'M16(attr+8)=4; M8(play)=0;'),
            'headroom': ('if(bridge.live && car_audio_enabled() && frames)', 'if(0 && bridge.live && car_audio_enabled() && frames)'),
            'detach': ('if(e) detach_engine(c,e,0); f_004DC1D0_orig(c);', 'f_004DC1D0_orig(c);'),
            'active': ('if(!engine_active(e)) { *c=saved; return; }', '(void)object;'),
        }
        if mutant:
            old, new = changes[mutant]
            assert live.count(old) == 1
            live = live.replace(old, new)
        (folder / 'car_audio.c').write_text(live)
        posaudio = (ROOT / 'runtime/posaudio.c').read_text()
        wrappers = posaudio[posaudio.index('#define VOICE_HOOK'):posaudio.index('\nvoid posaudio_diag_second')]
        harness = HARNESS.replace('NATIVE_DECLARATIONS', declarations).replace('NATIVE_DISPATCH', dispatch)
        harness = harness.replace('NATIVE_STUBS', stubs).replace('VOICE_WRAPPERS', wrappers)
        (folder / 'harness.c').write_text(harness)
        command = [shutil.which('clang') or 'clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1', '-g',
                   '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                   '-Wno-parentheses-equality', '-Wno-deprecated-declarations', '-ffp-contract=off',
                   '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I'+str(folder), '-I'+str(ROOT / 'runtime'), '-I'+str(ROOT / 'recomp'),
                   str(folder / 'harness.c'), str(folder / 'native.c'), str(ROOT / 'runtime/interp.c'),
                   *[str(ROOT / f'runtime/car_audio_{n}.c') for n in ('runtime', 'csis', 'engine_class', 'player', 'backend', 'filter')],
                   '-o', str(folder / 'live')]
        built = subprocess.run(command, capture_output=True, text=True, timeout=90)
        assert built.returncode == 0, built.stderr[-6000:]
        native_path = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent / 'Need For Speed Most Wanted Black Edition/speed.exe'))
        stock = [native_path.parent / 'SOUND/ENGINE' / name for name in ('GIN_DB9.gin', 'GIN_DB9_DCL.gin')]
        assert all(p.is_file() for p in stock), 'private stock GIN inputs absent'
        result = subprocess.run([str(folder / 'live'), str(folder / 'AUDIO.BIN'), str(folder / 'memory.bin'), *map(str, stock)],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr[-6000:]
        row = json.loads(result.stdout)
        print(json.dumps(row, sort_keys=True))
        assert row['resource_matches'] == row['expected'], 'engine/exhaust resource matches 0/2 -> 2/2 required'
        assert row['source_gins'] == 4
        assert row['aems_lifecycle'] == 1
        assert row['nonzero_pcm'] and row['bank_pcm'] and row['ginsu_pcm'] and row['energy'] > 0, 'nonzero source bank/Ginsu PCM required'
        assert row['allocations'] == row['freed'] and row['packet_creates'] == row['packet_destroys']
        return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private', action='store_true')
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--mutant', choices=('serving', 'secondary', 'pcm', 'gin', 'descriptor', 'profile', 'defer', 'gain', 'headroom', 'detach', 'active'))
    controls = ('import', 'pack', 'tick', 'activity', 'source-activity', 'mix', 'raw', 'pool', 'recover', 'recover-ginsu', 'destroy', 'off', 'stock', 'ginsu-detach', 'bridge-full', 'ginsu-full', 'pool-range', 'gain-rise', 'default-stock', 'default-added')
    parser.add_argument('--safety-control', choices=controls)
    args = parser.parse_args()
    if args.private or args.original or args.mutant:
        row = run(args.original, args.mutant)
        return 3 if row is None else 0
    safety(args.safety_control)
    if not args.safety_control:
        for control in controls:
            safety(control)
        print(f'live safety PASS: {len(controls)}/{len(controls)} old behaviors rejected; Off/stock byte identity')
        row = run()
        return 3 if row is None else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
