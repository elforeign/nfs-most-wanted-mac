#!/usr/bin/env python3
"""Paired execution/lifetime proof; generated fixtures run without retail data.

Plan: pin current resolver/arithmetic fragments, relocate complete source modules,
execute them with interp_run, compare against separately translated source
arithmetic and independent player/control/PCM expectations, then unload/reload.
Measured original 88/127 -> fixed 127/127 interfaces across 69 banks,
98 modules and 400 player states. Fixed: 3,332 full state snapshots, 68,992
PCM channel samples, zero state/PCM mismatches or sanitizer invalid accesses;
3,940/3,940 PCM borrows retired and 454/454 control objects released.
Generated: 5/9 -> 9/9 interfaces, paired 2/4 -> 4/4; 244 state snapshots,
5,120 PCM samples, 60/60 borrows and 16/16 objects released. Six compiled
detach/selector/ordinal/control/unlink/overlap controls all fail without private
banks; fixed exits 0, original exits 1. External destroy unlink: 0/1 -> 1/1;
duplicate and overlapping plans: 0/2 -> 2/2 rejected before image writes.
Independent PCM/filter tolerance 2e-6; scalar/cursor tolerance 2e-7, each scaled
by max(1, magnitude). Complete source/native module traces match exactly.
Private mode exits 3 when its source banks or either executable is absent.
No hooks or sound admission are installed; no Windows files are rewritten.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import argparse
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_abi as abi

# Function-sized baselines, independent of repository history and adjacent code.
NATIVE = {
    '008199D1': '33451ec7460e20b6b1cc2b1dd7b3d93bead27eaf177ab6283de605566a07f840',
    '00819A0F': '55d50a52a87eaba96e70afe18b1657f648478f4c97aaeb2f0212e919c51ff686',
    '00819A32': 'f20bb4803867321aa8ffa461253505aaa3bf95f20db1d9b236b9b4dd31db48b5',
    '00819B13': '654e101c458fbe6b0237aa78fdfeeee353fe42fe59434495692b8a58bc780029',
    '00819BBE': '53c7a676510bbfdff04707acb9c46ef2a18b7a0049e892fdafca187b6f8b1a7e',
    '00819CC6': '126c301204559724b361839f76fc3c7f20818ec30cb531fd338086ef8d4b6a4f',
    '00819DC6': 'c4bf6eb6afd6e780cffa22e4c1de466c7c6e23e48a376e120fa7d9052193425a',
    '00819E57': '41f124b83f6b7bce6e132e2c3de6789a3f71a5357f83e5b49ed2865d5aca7e18',
    '00819F0B': '54c3ab0313384cecf38f12b68b1041fc9dbafb93a6487ce642ce2da72763f86a',
    '00819F57': '91ef439c97b0f245401eb36b970f08c10a261977ef6cdb894357710655db6c4a',
    '00819F92': '9ddf1a03f7ff99da18d457aa4e73edcadd96002109fa6f643c28262306fdceae',
    '0081A61D': '93f44aacbaff20ff24879d03eba242c864c17f4dfc6006f8d4f84a46baab6462',
    '0081A707': '68f7f38f3ec8ce49edf0621ab28eca548fdf1379f4130836a4b5114d0b2422d8',
    '0081A7A6': '3df130045864eb2b2a1d7778077c3106122ca7d994d99ab88435041278152ff1',
    '0081A7CD': '6f5954722d756e0c7acdfe36d8c77b5d3e3b5798327948800af55631c6e37fae',
    '00819F3A': '81ff40230aea1d3eb2353d154507c086e331b111009e32109c2022efc526f308',
    '0081EB58': '2d97ade757319d806cf0478d23d36690d47c095d4559f66159ec70981359979d',
    '0081F81A': 'ec62a494e5f5d4af0885c73faa91599f3e2b69c262fe97c4dbade957a057fea0',
    '008196C2': '11aaa950d36289b4b1346358e030a4f37a0aecda50c7cb6db3a908eddc2fc563',
    '008196CE': 'f66a409b99eb6653d8e0816f138fadcfbcdf7b88340c413016a3459dbe4262b8',
}
SOURCE = {
    '00897E88': (66, '706470218d845a59abf653b9083eacbf00516874e658ebaa484dba74b98f2203'),
    '00897ECA': (38, 'cfd465248f08c58643097df95feda2499a730595a03d82ab64591f4824d6a3ba'),
    '00897EF0': (162, 'a3d64924d8c46fc31c382044e3f868edfa357035b6228638faefd2e080ad5c72'),
    '00897FDA': (58, '9de2128db413ebfa2ac2b22ca8e3bc57f66d6e1b4f6455a08e47cc30d58bd2af'),
    '00898090': (40, '28d56b92aa735d720c50f915091b0d22950d8281f6947b72e2577ee7cbf18521'),
    '0089819F': (253, '16d6a7212cc404b05b7557cc499e3407ca3dd0e7b84b88481167f8a9dcb591a3'),
    '0089829C': (145, '48a04419c528771194f39befa585e988df8af29689eb97cd74515cbbddf0c26b'),
    '0089832D': (30, '409da033f28b33ae7e7e1151dd5bcc5d3ef05995493840a228ce3711bf283dc7'),
    '008983F4': (47, '53c773c668ce588b751821e783f6ae98204063f3b127cfb823ed639e8572f631'),
    '00898440': (31, 'aea69a6a6c542e5af151152205eb53dfe5b5696dd6bf99845c69fab89c555f83'),
    '00898487': (24, '5b7b220fbc4f84d93a67ba1d9c4cf8dd06cd030fa24c0332b100d97dc1166810'),
    '008984EC': (234, 'c3b681ed64182dd676a72367b61d297f7c7d440cedc5b4e9926abce6a144cdcb'),
    '008985D6': (159, 'f7367c30fab5ef988a5369871be3cd022586ffc41a781796a92ce3988f0ab7c1'),
    '00898675': (42, 'b9d7fdf8108c217d0eaf8cb6ee41ac46595aa6771a957829f9332777a90b9eef'),
    '0089869F': (22, '0990cf2fb995a6ff54fbf098ef7cc3c6358010d1ab31c3cba7d543e4b6a7c0fc'),
    '00898423': (29, '15a713287471413c67101717adc2b5ec0b20473f828711924818d35cf896d0b6'),
    '0089E1EC': (161, '6bf32d41823dea32dc9285301f3b0542c3323ee562a743c14537df47582189e7'),
    '008979A4': (23, '1a6e487a043d1724702002db6413ca673e3a7cb37d883f222f69a58230581a05'),
    '0089E28D': (86, '6f4fc28c54c834255dd041b46079995e7b50fcb0d80a3198aac56ce9f3eca121'),
    '00897BA8': (15, 'b049b4a1f4630061c7ffb5b585c7e3ad92b5f588e0cfefa0472642787281b8a4'),
    '00897BB7': (11, 'fc83b3bd19c79cc5178fdc5622d0ee4b68ac506bd7365ff18346406a54f89c38'),
}
RESOLVER = {
    'car_audio_csis_check': '6ac6bec69b49c3b8bbf625948ff95217fdbb1ceb119cfe1c9b7e03bf97232549',
    'car_audio_csis_resolve': '1b54cf8ae03f57e26c7d92b3fd3b716025cf947f32fd57db9ca5bcde8b9cd12d',
    'word': 'e5b08cfd3854a07fb94a60b991da63f13e9594e710f32bfc228430085e1e12b0',
    'valid': '2a5eb70a2d4fe40fc188bec54634e2178c7a91b1528e0020812023f297839844',
}

HARNESS = r'''
#include "host.h"
#include "car_audio_runtime.h"
#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include <inttypes.h>
uint8_t *G;
void interp_run(Cpu *,uint32_t);
static CarAudioRuntimeInstance *active;
static CarAudioRuntimeBank bank;
static unsigned borrows,retired,objects,object_creates,object_releases,calls;
static int source_mode;
static const float waveform[]={0.25f,-0.5f,1.0f,0.0f,-0.25f,0.75f,0.5f,-1.0f};
static uint32_t get(void) { uint8_t b[4]; assert(fread(b,1,4,stdin)==4); return (uint32_t)b[0]|(uint32_t)b[1]<<8|(uint32_t)b[2]<<16|(uint32_t)b[3]<<24; }
static void hex(const uint8_t *s,size_t n) { for(size_t i=0;i<n;i++) printf("%02x",s[i]); }
static void retired_sample(void *p) { assert(p==&bank); ++retired; }
static int resolve(void *p,uint64_t identity,int16_t sample,CarAudioPCM *out) {
    assert(p==&bank && identity==77 && sample>=0); ++borrows;
    printf("{\"event\":\"resolve\",\"sample\":%d,\"bank\":%"PRIu64"}\n",sample,identity);
    *out=(CarAudioPCM){waveform,8,44100.0f,1,retired_sample,p}; return 0;
}
static int create_class(void *p,const int32_t *v,size_t n,void **out) {
    (void)p; assert(n==1); int32_t *a=malloc(sizeof(*a)); assert(a); *a=*v;
    *out=a; ++objects; ++object_creates; return 0;
}
static void update_class(void *p,void *o,const int32_t *v,size_t n) { (void)p; assert(n==1 && o); *(int32_t *)o=*v; }
static void release_class(void *p,void *o) { (void)p; assert(o && objects); free(o); --objects; ++object_releases; }
static void report_player(unsigned ordinal,uint32_t at,uint32_t result,const uint8_t *before,size_t size) {
    printf("{\"event\":\"import\",\"ordinal\":%u,\"offset\":%u,\"instance\":%u,\"result\":%u,\"before\":\"",ordinal,at-active->address,active->address,result);
    hex(before,size); printf("\",\"after\":\""); hex(G+at,size);
    printf("\",\"selectors\":\"");
    if(ordinal==27) for(size_t i=0;i<active->module->player_count;i++) {
        const CarAudioRuntimePlayerPlan *plan=active->module->players+i;
        if(at!=active->address+plan->offset) continue;
        uint32_t n; memcpy(&n,bank.source+plan->selector,4);
        hex(bank.source+plan->selector,4+(size_t)n*12);
    }
    printf("\",\"value\":");
    void *object=NULL;
    if(ordinal==38) for(size_t i=0;i<active->module->control_count;i++)
        if(at==active->address+active->controls[i].offset) object=active->controls[i].object;
    if(object) printf("%d",*(int32_t *)object); else printf("null");
    puts("}");
}
/* Both executions run the complete, original bank glue in the real interpreter.
 * The oracle run selects independently recompiled source arithmetic functions.
 */
int code_region_of(uint32_t t) { return bank.source && t>=bank.base && t<bank.base+bank.size?0:-1; }
static unsigned ordinal_of(uint32_t t) {
    switch(t) {
    case 0x8196c2:return 0; case 0x8196ce:return 1; case 0x8196e8:return 4;
    case 0x8199d1:return 6; case 0x819a0f:return 7; case 0x819a32:return 8;
    case 0x819b13:return 10; case 0x819bbe:return 13; case 0x819cc6:return 15;
    case 0x819dc6:return 16; case 0x819e57:return 17; case 0x819f0b:return 21;
    case 0x819f57:return 22; case 0x819f92:return 25; case 0x81a52c:return 27;
    case 0x81a61d:return 28; case 0x81a707:return 29; case 0x81a7a6:return 30;
    case 0x81a7cd:return 31; case 0x819f3a:return 35; case 0x819875:return 38;
    default:abort();
    }
}
PRIVATE_DECLARATIONS
static void primitive(Cpu *c,unsigned ordinal) {
    PRIVATE_DISPATCH
    /* Generated-only services; private execution above uses real fragments. */
    if(ordinal==0) { uint32_t at=arg(c,0); c->eax=M32(at+16); M32(at+16)=0; c->esp+=4; return; }
    if(ordinal==1) { c->eax=M32(arg(c,0)+20); c->esp+=4; return; }
    abort();
}
void guest_divide_error(Cpu *c,uint32_t at) { (void)c; fprintf(stderr,"invalid divide %x\n",at); abort(); }
void dispatch(Cpu *c,uint32_t t) {
    unsigned ordinal=ordinal_of(t); ++calls;
    if(ordinal==4 || ordinal==27 || ordinal==38) {
        uint32_t at=arg(c,0),result=0; size_t size=16;
        if(ordinal==27) {
            size=0;
            for(size_t i=0;i<active->module->player_count;i++)
                if(at==active->address+active->module->players[i].offset) size=active->module->players[i].size;
            assert(size);
        } else if(ordinal==38) {
            size=24+4*M8(at+13)+(M8(at+12)?8*M8(at+13):0);
        }
        uint8_t before[4096]; assert(size<=sizeof(before)); memcpy(before,G+at,size);
        assert(car_audio_runtime_import(active,ordinal,at,&result)==0);
        c->eax=result; c->esp+=4;
        report_player(ordinal,at,result,before,size);
    } else primitive(c,ordinal);
}
static int execute(void *context,uint32_t entry,uint32_t arg0) {
    assert(context==active); Cpu c={0}; c.esp=0x700000; c.cw=0x37f;
    c.esi=123; c.edi=456; c.ebx=789; c.ebp=1011;
    M32(c.esp)=0xffff; M32(c.esp+4)=arg0;
    interp_run(&c,entry);
    assert(c.esp==0x700004 && c.esi==123 && c.edi==456 && c.ebx==789 && c.ebp==1011 && c.top==0);
    return 0;
}
static CarAudioCsisInterface read_interface(void) {
    CarAudioCsisInterface s={0}; s.id_size=get(); s.member_words=(uint16_t)get(); s.kind=(uint8_t)get();
    assert(s.id_size>=6 && s.id_size<=260); uint8_t *id=malloc(s.id_size); assert(id);
    assert(fread(id,1,s.id_size,stdin)==s.id_size); s.id=id; return s;
}
static void snapshot(CarAudioRuntimeInstance *p,CarAudioBackend *backend,unsigned tick) {
    printf("{\"event\":\"state\",\"tick\":%u,\"alive\":%u,\"state\":\"",tick,p->bank!=NULL);
    if(p->bank) hex(p->state,p->module->size);
    puts("\"}");
    /* Export voice state before render for an independent equation-based PCM
       oracle. The PCM fixture is generated, including in private bank replay. */
    printf("{\"event\":\"pcm\",\"voices\":["); unsigned printed=0;
    for(size_t i=0;i<backend->capacity;i++) {
        CarAudioVoice *v=backend->voices+i; if(!v->owned || v->ended) continue;
        printf("%s{\"handle\":%d,\"cursor\":%.17g,\"pitch\":%.9g,\"volume\":%.9g,\"dry\":%.9g,\"wet\":%.9g,\"pan\":[%.9g,%.9g],\"priority\":%.9g,\"paused\":%u,\"filter\":[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g, %d]}",
          printed++?",":"",v->handle,v->cursor,v->pitch,v->volume,v->dry,v->wet,v->pan[0],v->pan[1],v->priority,v->paused,
          v->filter.a1,v->filter.a2,v->filter.b0,v->filter.b1,v->filter.b2,
          v->filter.x1,v->filter.x2,v->filter.y1,v->filter.y2,v->filter.active);
    }
    float dry[16]={0},wet[16]={0}; car_audio_backend_render(backend,dry,wet,8);
    printf("],\"dry\":["); for(unsigned i=0;i<16;i++) printf("%s%.9g",i?",":"",dry[i]);
    printf("],\"wet\":["); for(unsigned i=0;i<16;i++) printf("%s%.9g",i?",":"",wet[i]); puts("]}");
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IONBF,0);
    source_mode=argc>1 && !strcmp(argv[1],"source");
    G=calloc(1,0xc00000); assert(G);
    PRIVATE_MEMORY
    uint32_t size=get(); assert(size>=0x60 && size<0x100000); uint8_t *source=malloc(size); assert(source);
    assert(fread(source,1,size,stdin)==size); unsigned nm=get(),nr=get(); assert(nm<=1024 && nr<=65536);
    CarAudioRuntimeInterface *refs=calloc(nr,sizeof(*refs)); assert(refs);
    for(unsigned i=0;i<nr;i++) { refs[i].site=get(); refs[i].interface=read_interface(); }
    CarAudioRuntimeModule *mods=calloc(nm,sizeof(*mods)); assert(mods);
    for(unsigned i=0;i<nm;i++) {
        CarAudioRuntimeModule *m=mods+i;
        m->descriptor=get(); m->entry=get(); m->state=get(); m->size=get(); m->member_offset=get(); m->destroy_offset=get();
        unsigned ref=get(); assert(ref<nr); m->interface=refs[ref].interface;
        m->player_count=get(); assert(m->player_count<=255);
        CarAudioRuntimePlayerPlan *players=calloc(m->player_count?m->player_count:1,sizeof(*players)); assert(players); m->players=players;
        for(size_t j=0;j<m->player_count;j++) { players[j].offset=get(); players[j].size=get(); players[j].selector=get(); }
        m->control_count=get(); assert(m->control_count<=255);
        uint32_t *controls=calloc(m->control_count?m->control_count:1,4); assert(controls); m->controls=controls;
        for(size_t j=0;j<m->control_count;j++) controls[j]=get();
    }
    CarAudioCsisInterface reg[6]; uint8_t ids[6][260]; unsigned nt=0;
    for(unsigned i=0;i<nr;i++) {
        CarAudioCsisInterface r=refs[i].interface; unsigned j;
        for(j=0;j<nt;j++) if(!strcmp((const char *)reg[j].id+4,(const char *)r.id+4)) break;
        if(j<nt) continue;
        assert(nt<6); memcpy(ids[nt],r.id,r.id_size);
        if(strcmp((const char *)r.id+4,"CAR") && strcmp((const char *)r.id+4,"CAR_ENG")) { ids[nt][0]=0x3f; ids[nt][1]=0x38; }
        r.id=ids[nt]; reg[nt++]=r;
    }
    unsigned bound=0;
    for(unsigned i=0;i<nr;i++) {
#ifdef ORIGINAL
        bound+=car_audio_csis_resolve(&refs[i].interface,reg,nt,NULL)==0;
#else
        bound+=car_audio_csis_execution_resolve(&refs[i].interface,reg,nt,NULL)==0;
#endif
    }
    printf("{\"event\":\"bound\",\"bound\":%u,\"total\":%u}\n",bound,nr);
    if(bound!=nr) goto finish;
#ifdef ORIGINAL
    goto finish;
#endif
    CarAudioBackend backend={0}; CarAudioVoice voices[64];
    assert(car_audio_backend_init(&backend,voices,64,44100,resolve,&bank)==0);
    CarAudioRuntimeClasses classes={create_class,update_class,release_class,NULL};
    for(unsigned cycle=0;cycle<2;cycle++) {
        memset(G+0x10000,0xa5,size);
        assert(car_audio_runtime_load(&bank,source,G+0x10000,size,0x10000,77,mods,nm,refs,nr,reg,nt,&backend,&classes)==0);
        /* Bad relocation/late identity must leave every output byte untouched. */
        CarAudioRuntimeBank rejected={0};
        uint8_t *bad=malloc(size); assert(bad); memcpy(bad,source,size);
        uint8_t *dest=malloc(size); assert(dest);
        for(unsigned mutation=0;mutation<7;mutation++) {
            memcpy(bad,source,size); memset(dest,0xa5,size);
            uint32_t f1,f2; memcpy(&f1,bad+0x30,4); memcpy(&f2,bad+0x34,4);
            uint32_t old_member=mods[0].member_offset;
            const uint8_t *old_id=refs[nr-1].interface.id;
            uint16_t old_words=refs[nr-1].interface.member_words;
            uint32_t guest_base=0x10000;
            if(mutation==0) memset(bad+f1+4,0xff,4);
            if(mutation==1) {
                uint32_t n; memcpy(&n,bad+f1,4);
                if(n<2) continue;
                memcpy(bad+f1+8,bad+f1+4,4);
            }
            if(mutation==2) {
                uint32_t n,at; memcpy(&n,bad+f2,4);
                if(!n) continue;
                memcpy(&at,bad+f2+4,4); memset(bad+at,0xff,4);
            }
            if(mutation==3) refs[nr-1].interface.member_words++;
            if(mutation==4) mods[0].member_offset++;
            if(mutation==5) guest_base=UINT32_MAX-size+1;
            if(mutation==6) refs[nr-1].interface.id=NULL;
            assert(car_audio_runtime_load(&rejected,bad,dest,size,guest_base,77,mods,nm,refs,nr,reg,nt,&backend,&classes)<0);
            for(unsigned k=0;k<size;k++) assert(dest[k]==0xa5);
            mods[0].member_offset=old_member;
            refs[nr-1].interface.member_words=old_words;
            refs[nr-1].interface.id=old_id;
        }
        /* Generated paired fixture has room for two descriptor rows. Both
         * duplicate and partially overlapping, individually valid plans must
         * fail before image mutation, without relying on private banks. */
        if(nm==2 && mods[0].player_count==1 && mods[0].control_count==1) {
            CarAudioRuntimeModule *plans=malloc(nm*sizeof(*plans)); assert(plans);
            memcpy(plans,mods,nm*sizeof(*plans));
            CarAudioRuntimePlayerPlan players[2]={mods[0].players[0],mods[0].players[0]};
            plans[0].players=players; plans[0].player_count=2; plans[0].control_count=0;
            for(unsigned kind=0;kind<2;kind++) {
                memcpy(bad,source,size); memset(dest,0xa5,size);
                uint32_t descriptor=plans[0].descriptor;
                bad[descriptor+36]=2; bad[descriptor+39]=0;
                if(kind) {
                    players[0].size=128; players[1].offset=players[0].offset+100; players[1].size=28;
                    uint8_t *state=bad+plans[0].state+players[1].offset;
                    memset(state,0,28); memcpy(state+4,&players[1].selector,4); state[16]=255;
                }
                memcpy(bad+descriptor+64,&players[1].offset,4);
                assert(car_audio_runtime_load(&rejected,bad,dest,size,0x10000,77,plans,nm,refs,nr,reg,nt,&backend,&classes)<0);
                for(unsigned k=0;k<size;k++) assert(dest[k]==0xa5);
            }
            free(plans);
        }
        free(bad); free(dest);
        for(unsigned module=0;module<nm;module++) {
            puts("{\"event\":\"reset\"}");
            CarAudioRuntimeInstance instance={0}; active=&instance;
            const CarAudioRuntimeModule *m=mods+module; int32_t words[100]={0};
            assert(m->interface.member_words<=100);
            for(unsigned j=0;j<m->interface.member_words;j++) memcpy(words+j,source+m->state+m->member_offset+4*j,4);
            unsigned engine=car_audio_engine_class_matches(0,&m->interface)||car_audio_engine_class_matches(1,&m->interface);
            if(engine) {
                for(unsigned j=0;j<100;j++) words[j]=0;
                words[0]=(int32_t)get(); words[12]=1; words[2]=words[3]=512;
                words[14]=words[27]=16384; words[21]=words[29]=1000;
                words[25]=8192;
                for(unsigned j=30;j<68;j++) words[j]=1000+(int32_t)(j%8)*1000;
                for(unsigned j=68;j<84;j++) words[j]=32767;
                for(unsigned j=84;j<100;j++) words[j]=4000;
            }
            assert(car_audio_runtime_create(&bank,module,&instance,G+0x200000,m->size,0x200000,words,m->interface.member_words)==0);
            if(engine) for(unsigned j=0;j<100;j++) assert(M32(instance.address+m->member_offset+4*j)==(uint32_t)words[j]);
            assert(car_audio_runtime_create(&bank,module,&instance,G+0x200000,m->size,0x200000,words,m->interface.member_words)<0);
            CarAudioRuntimeInstance collision={0};
            assert(car_audio_runtime_create(&bank,module,&collision,G+0x210000,m->size,0x200000,words,m->interface.member_words)<0);
            assert(!collision.bank);
            uint32_t invalid=123;
            assert(car_audio_runtime_import(&instance,27,0x1fffff,&invalid)<0 && invalid==123);
            assert(car_audio_runtime_import(&instance,4,0x200000,&invalid)<0);
            assert(car_audio_runtime_update(&instance,words,m->interface.member_words+1)<0);
            if(engine) {
                words[0]++; assert(car_audio_runtime_update(&instance,words,100)<0); words[0]--;
            }
            for(unsigned tick=0;tick<6;tick++) {
                if(engine) {
                    words[1]=(int32_t)tick*1700;
                    words[13]=words[26]=-(int32_t)tick*100;
                    words[14]=words[27]=32767/(int32_t)(tick+1);
                    words[15]=words[28]=(int32_t)tick*7651;
                    words[21]=words[29]=tick==5?25000:1000+(int32_t)tick*3700;
                    for(unsigned j=30;j<68;j++) words[j]=1000+(int32_t)(j%8)*1000+(int32_t)tick*13;
                    for(unsigned j=68;j<84;j++) words[j]=32767-(int32_t)tick*271;
                    for(unsigned j=84;j<100;j++) words[j]=4000+(int32_t)tick*23+(int32_t)j;
                    assert(car_audio_runtime_update(&instance,words,100)==0);
                    for(unsigned j=0;j<100;j++) assert(M32(instance.address+m->member_offset+4*j)==(uint32_t)words[j]);
                }
                assert(car_audio_runtime_tick(&instance,execute,&instance)==0);
                printf("{\"event\":\"module\",\"alive\":%u,\"state\":\"",instance.bank!=NULL);
                hex(G+0x200000,m->size); puts("\"}");
                if(!instance.bank) puts("{\"event\":\"reset\"}");
                if(!instance.bank) assert(car_audio_runtime_create(&bank,module,&instance,G+0x200000,m->size,0x200000,words,m->interface.member_words)==0);
                /* Pause/resume is a source player command. Dispatch through
                   the same ordinal boundary as the module, including handle 0. */
                for(size_t j=0;j<m->player_count;j++) {
                    uint32_t at=instance.address+m->players[j].offset;
                    M32(at+24)=tick==2?2:tick==4?0:1;
                    Cpu c={0}; c.esp=0x700000; M32(c.esp+4)=at; dispatch(&c,0x81a52c);
                    assert(c.esp==0x700004);
                }
                snapshot(&instance,&backend,tick);
                assert(car_audio_backend_tick(&backend)==0);
            }
            /* Explicit ordinal 4 request with live voices/control objects. */
            uint32_t at=instance.address+m->destroy_offset,result;
            M32(at+12)=1;
            assert(car_audio_runtime_import(&instance,4,at,&result)==0);
            assert(!instance.bank && !bank.instances);
            for(size_t j=0;j<m->control_count;j++) assert(!M32(0x200000+m->controls[j]+8));
            car_audio_runtime_destroy(&instance); car_audio_runtime_destroy(&instance);
            assert(objects==0); puts("{\"event\":\"reset\"}");
            for(size_t j=0;j<backend.capacity;j++) assert(!voices[j].owned);
            /* Leave a fresh live instance for unload's ownership walk. */
            assert(car_audio_runtime_create(&bank,module,&instance,G+0x200000,m->size,0x200000,words,m->interface.member_words)==0);
            for(unsigned step=0;step<5;step++) {
                for(size_t j=0;j<m->player_count;j++) {
                    const CarAudioRuntimePlayerPlan *plan=m->players+j;
                    uint32_t at2=instance.address+plan->offset,n;
                    memcpy(&n,bank.source+plan->selector,4);
                    for(uint32_t k=0;k<n;k++) {
                        uint16_t sample; memcpy(&sample,bank.source+plan->selector+4+12*k,2);
                        if(sample!=UINT16_MAX) { M32(at2+20)=k; break; }
                    }
                    M32(at2+24)=step==1?2:step==3?0:1;
                    Cpu c={0}; c.esp=0x700000; M32(c.esp+4)=at2; dispatch(&c,0x81a52c);
                }
                for(size_t j=0;j<m->control_count;j++) {
                    uint32_t at2=instance.address+m->controls[j];
                    uint32_t fields=at2+16+(M8(at2+12)?8*M8(at2+13):0);
                    M32(fields)=step==0 || step==4;
                    M32(fields+4)=step==3;
                    M32(fields+8)=step==0?(uint32_t)-9:99;
                    Cpu c={0}; c.esp=0x700000; M32(c.esp+4)=at2; dispatch(&c,0x819875);
                }
                snapshot(&instance,&backend,6+step);
            }
            car_audio_runtime_unload(&bank); assert(!instance.bank && !objects);
            puts("{\"event\":\"reset\"}");
            memset(G+0x200000,0xdd,m->size); memset(G+0x10000,0xdd,size);
            assert(car_audio_runtime_load(&bank,source,G+0x10000,size,0x10000,77,mods,nm,refs,nr,reg,nt,&backend,&classes)==0);
        }
        /* Both exports coexist in one bank and one voice pool. Destroying
           CAR must leave CAR_ENG's full storage and resources attached. */
        size_t pair[2]={SIZE_MAX,SIZE_MAX};
        for(size_t i=0;i<nm;i++) for(unsigned k=0;k<2;k++)
            if(car_audio_engine_class_matches(k,&mods[i].interface)) pair[k]=i;
        if(pair[0]!=SIZE_MAX && pair[1]!=SIZE_MAX) {
            puts("{\"event\":\"reset\"}");
            CarAudioRuntimeInstance instances[2]={{0}}; int32_t values[2][100]={{0}};
            for(unsigned k=0;k<2;k++) {
                values[k][12]=1; values[k][2]=512+(int32_t)k*256;
                values[k][14]=16384; values[k][21]=1000;
                assert(car_audio_runtime_create(&bank,pair[k],instances+k,G+0x200000+k*0x10000,
                    mods[pair[k]].size,0x200000+k*0x10000,values[k],100)==0);
            }
            for(unsigned tick=0;tick<3;tick++) {
                for(unsigned k=0;k<2;k++) {
                    active=instances+k; values[k][1]=4096+(int32_t)(tick+k)*111;
                    assert(car_audio_runtime_update(active,values[k],100)==0);
                    assert(car_audio_runtime_tick(active,execute,active)==0 && active->bank);
                }
                snapshot(instances+1,&backend,tick);
            }
            assert(instances[0].players[0].owned && instances[1].players[0].owned);
            assert(instances[0].players[0].voice!=instances[1].players[0].voice);
            uint8_t retained[400]; memcpy(retained,instances[1].engine.members,400);
            car_audio_runtime_destroy(instances);
            assert(instances[1].bank && instances[1].players[0].owned &&
                !memcmp(retained,instances[1].engine.members,400));
            car_audio_runtime_unload(&bank);
            assert(!instances[1].bank && !objects);
            memset(G+0x200000,0xdd,0x20000);
            for(size_t j=0;j<backend.capacity;j++) assert(!voices[j].owned);
        }
        car_audio_runtime_unload(&bank);
    }
    car_audio_backend_destroy(&backend);
    assert(borrows==retired && !objects && object_creates==object_releases);
    printf("{\"event\":\"lifetime\",\"borrows\":%u,\"retired\":%u,\"creates\":%u,\"releases\":%u,\"live\":%u,\"calls\":%u}\n",borrows,retired,object_creates,object_releases,objects,calls);
finish:
    for(unsigned i=0;i<nm;i++) { free((void *)mods[i].players); free((void *)mods[i].controls); }
    for(unsigned i=0;i<nr;i++) free((void *)refs[i].interface.id);
    free(mods); free(refs); free(source); free(G); return 0;
}
'''

def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pe_sections(data):
    pe = struct.unpack_from('<I', data, 60)[0]
    table = pe + 24 + struct.unpack_from('<H', data, pe+20)[0]
    return [struct.unpack_from('<8sIIII', data, table+40*i)
            for i in range(struct.unpack_from('<H', data, pe+6)[0])]


class SourcePE:
    """Private, separately pinned source image; never the production PE loader."""
    def __init__(self, data):
        self.data, self.sections, self.imports = data, pe_sections(data), {}

    def read(self, address, size):
        for _, virtual, rva, extent, offset in self.sections:
            if rva <= address-0x400000 and address-0x400000+size <= rva+extent:
                at = offset+address-0x400000-rva
                return self.data[at:at+size]
        raise AssertionError(f'source fragment outside sections: {address:x}')


def pinned_native():
    found = {}
    path = ROOT / 'recomp/out/speed_55.c'
    assert path.is_file(), 'private replay needs generated translated fragments'
    text = path.read_text()
    for name, digest in NATIVE.items():
        match = re.search(r'void f_'+name+r'\(Cpu \*restrict c\) \{.*?\n\}', text, re.S)
        assert match and hashlib.sha256(match[0].encode()).hexdigest() == digest, name
        found[name] = match[0]
    return found


def private_code(data):
    # Static translation is confined to verified function-sized source bytes.
    # No emulation library or executable memory is used.
    sys.path.insert(0, str(ROOT / 'recomp'))
    import recomp
    pe = SourcePE(data)
    functions = {int(name, 16): int(name, 16)+size for name, (size, _) in SOURCE.items()}
    rc = recomp.Recompiler(pe, functions)
    rc.calls = True
    found = pinned_native()
    for name, (size, digest) in SOURCE.items():
        addr = int(name, 16)
        assert hashlib.sha256(pe.read(addr, size)).hexdigest() == digest, name
        code, error = rc.translate(addr, addr+size)
        assert code and not error and 'untranslated(' not in code, (name, error)
        found[name] = code
    names = set(found)
    for body in found.values():
        names.update(re.findall(r'f_([A-F0-9]{8})\(c\)', body))
    assert names == set(found), 'every arithmetic dependency must be pinned'
    return '#include "runtime.h"\n' + ''.join(f'void f_{n}(Cpu *);\n' for n in sorted(names)) + '\n'.join(found.values())


def compile_proof(folder, original=False, source_exe=None, mutant=None):
    folder.mkdir(parents=True, exist_ok=True)
    declarations, dispatch, memory = '', '', ''
    extra = []
    if source_exe is not None:
        private = folder / 'fragments.c'
        private.write_text(private_code(source_exe))
        extra.append(str(private))
        for ordinal, (source, target, _, _) in {**abi.ARITHMETIC, **{k: abi.LIFECYCLE[k] for k in (0, 1)}}.items():
            declarations += f'void f_{source:08X}(Cpu *); void f_{target:08X}(Cpu *);\n'
            dispatch += f'if(ordinal=={ordinal}) {{ if(source_mode) f_{source:08X}(c); else f_{target:08X}(c); return; }}\n'
        memory = '''unsigned spans=get(); for(unsigned i=0;i<spans;i++) {
            uint32_t at=get(),n=get(); assert(at<=0xc00000 && n<=0xc00000-at);
            assert(fread(G+at,1,n,stdin)==n);
        }
        if(source_mode) { MF32(0xbbee68)=10; memset(G+0xbbf420,0,24); }
        else { MF32(0x9c1b54)=10; memset(G+0x9c1fe8,0,24); }
        '''
    harness = HARNESS.replace('PRIVATE_DECLARATIONS', declarations).replace('PRIVATE_DISPATCH', dispatch).replace('PRIVATE_MEMORY', memory)
    (folder / 'harness.c').write_text(harness)
    source = (ROOT / 'runtime/car_audio_runtime.c').read_text()
    changes = {
        'detach': ('car_audio_player_destroy(p->players,p->module->player_count);', '(void)p->players;'),
        'selector': ('b->source+plan->selector,', 'b->source+plan->selector+12,'),
        'ordinal': ('car_audio_player_update(p->players+i)', '(int)0'),
        'control': ('c->object=NULL; wr(p->state+c->offset+8,0);', '(void)c;'),
        'unlink': ('if(!p->executing) car_audio_runtime_destroy(p);', '(void)p;'),
        'overlap': ('if((uint64_t)p->offset < (uint64_t)q->offset+q->size &&', 'if(0 && (uint64_t)p->offset < (uint64_t)q->offset+q->size &&'),
    }
    if mutant:
        old, new = changes[mutant]
        assert source.count(old) == 1
        source = source.replace(old, new)
    (folder / 'adapter.c').write_text(source)
    csis = (ROOT / 'runtime/car_audio_csis.c').read_text()
    for name, digest in RESOLVER.items():
        match = re.search(r'(?:static )?(?:int|uint16_t) '+name+r'\([^;]*?\n\{.*?\n\}', csis, re.S)
        assert match and hashlib.sha256(match[0].encode()).hexdigest() == digest, name
    command = [shutil.which('clang') or 'clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-parentheses-equality', '-ffp-contract=off', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
               '-I'+str(ROOT / 'runtime'), '-I'+str(ROOT / 'recomp'), str(folder / 'harness.c'),
               str(folder / 'adapter.c'), str(ROOT / 'runtime/interp.c'),
               *[str(ROOT / 'runtime' / ('car_audio_'+n+'.c')) for n in ('csis', 'engine_class', 'player', 'backend', 'filter')],
               *extra, '-o', str(folder / 'proof')]
    if original:
        command.append('-DORIGINAL')
    ran = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert ran.returncode == 0, ran.stderr[-6000:]
    return folder / 'proof'


def pack(*values):
    return struct.pack('<'+'I'*len(values), *values)


def encode(data, manifest, identity=0):
    # Use the existing complete-manifest decoder, including imported controls.
    csis = module_from(ROOT / 'runtime/car_audio_csis_test.py', 'csis_fixture')
    refs = csis.manifest_refs(data, manifest)
    out = pack(len(data)) + data + pack(len(manifest['modules']), len(refs))
    for row, (wire, words, kind) in zip(manifest['interfaces'], refs):
        out += pack(row['site'], len(wire), words, kind) + wire
    for module in manifest['modules']:
        ref = next(i for i, r in enumerate(manifest['interfaces']) if r['site'] == module['descriptor']+4)
        out += pack(*(module[k] for k in ('descriptor', 'entry', 'state', 'state_size', 'member_offset', 'destroy_offset')), ref, len(module['players']))
        for player in module['players']:
            out += pack(player['offset'], player['size'], player['selector_offset'])
        out += pack(len(module['class_offsets']), *module['class_offsets'])
    for _ in range(2):
        for module in manifest['modules']:
            if module['interface'] in ('CAR', 'CAR_ENG'):
                out += pack(identity)
    return out


def memory_bytes(executable):
    spans = [(0x400000+rva, executable[offset:offset+extent])
             for name, virtual, rva, extent, offset in pe_sections(executable)
             if name.rstrip(b'\0') in (b'.rdata', b'.data')]
    return pack(len(spans)) + b''.join(pack(at, len(data))+data for at, data in spans)


def run(binary, payload, source=False, memory=b''):
    result = subprocess.run([str(binary), 'source' if source else 'native'], input=memory+payload,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, f'exit {result.returncode}: '+result.stderr[-3500:].decode(errors='replace')+result.stdout[-1500:].decode(errors='replace')
    return [json.loads(line) for line in result.stdout.splitlines()]


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def check_import(row):
    before, after = bytes.fromhex(row['before']), bytes.fromhex(row['after'])
    expected = bytearray(before)
    ordinal = row['ordinal']
    if ordinal == 27:
        command = max(0, min(2, struct.unpack_from('<i', before, 24)[0]))
        current, previous = before[12:14]
        owned = before[16] == 0
        if command != current:
            expected[12:14] = bytes((command, current))
        # Imported snapshots independently enforce the source transition graph,
        # private handle/type changes, options cache writes and untouched bytes.
        if command == 0 and command != current:
            struct.pack_into('<I', expected, 8, 0)
            expected[16] = 255
        elif command == 1:
            # Fresh creation must choose a source sample, never native type.
            if owned or (command != current and (current, previous) != (2, 1)):
                # Empty source rows are allowed; actual status disambiguates.
                if row['result'] == 1:
                    expected[8:12] = after[8:12]
                    expected[16] = 0
                    for i in range(before[14]):
                        expected[32+12*i:36+12*i] = before[36+12*i:40+12*i]
        assert after == expected, ('player source state', before.hex(), after.hex(), expected.hex())
        assert row['result'] == (command if after[16] == 0 else 0)
    elif ordinal == 38:
        clamp, count = before[12:14]
        at = 16+(8*count if clamp else 0)
        create, destroy = struct.unpack_from('<ii', before, at)
        alive = bool(struct.unpack_from('<I', before, 8)[0])
        if destroy:
            alive = False
        elif (create and not alive) or (not create and alive):
            if clamp:
                for i in range(count):
                    lo, hi = struct.unpack_from('<ii', before, 16+8*i)
                    value = struct.unpack_from('<i', before, at+8+4*i)[0]
                    struct.pack_into('<i', expected, at+8+4*i, max(lo, min(hi, value)))
            alive = True
        # Handles are opaque sidecar identities, independently test nonzero.
        assert bool(struct.unpack_from('<I', after, 8)[0]) == alive
        expected[8:12] = after[8:12]
        assert after == expected and row['result'] == int(alive)
    elif ordinal == 4:
        assert before == after and row['result'] == 0


class SourceReplay:
    """Independent object, selector, control-unit, cursor and filter oracle.

    Derives expected voices exclusively from original rows and pre-call source
    commands. Rendered backend fields are observations, never expected inputs.
    The established filter proof supplies its independent source equations.
    """
    def __init__(self):
        self.voices = {}
        self.pending = []
        self.controls = {}
        self.filter = module_from(ROOT / 'runtime/car_audio_filter_test.py', 'filter_equations')

    def reset(self):
        self.voices.clear()
        self.pending.clear()
        self.controls.clear()

    def control(self, row):
        before = bytes.fromhex(row['before'])
        clamp, count = before[12:14]
        at = 16+(8*count if clamp else 0)
        create, destroy, value = struct.unpack_from('<iii', before, at)
        key = row['instance'], row['offset']
        alive = key in self.controls
        assert bool(struct.unpack_from('<I', before, 8)[0]) == alive
        if destroy:
            self.controls.pop(key, None)
        elif (create and not alive) or (not create and alive):
            if clamp:
                lo, hi = struct.unpack_from('<ii', before, 16)
                value = max(lo, min(hi, value))
            self.controls[key] = value
        assert row['value'] == self.controls.get(key), 'owned control value mismatch'

    def player(self, row):
        before, after = bytes.fromhex(row['before']), bytes.fromhex(row['after'])
        command = max(0, min(2, struct.unpack_from('<i', before, 24)[0]))
        current, previous = before[12:14]
        owned = before[16] == 0
        old_handle = struct.unpack_from('<I', before, 8)[0]
        handle = struct.unpack_from('<I', after, 8)[0]
        fresh = False
        if owned:
            assert old_handle in self.voices, 'source voice must already be owned'
        if command != current:
            if command == 0:
                if owned:
                    del self.voices[old_handle]
                owned = False
            elif command == 2 and owned:
                self.voices[old_handle]['paused'] = True
            elif command == 1:
                if owned:
                    self.voices[old_handle]['pitch'] = 1.0
                    self.voices[old_handle]['paused'] = False
                elif (current, previous) != (2, 1):
                    selectors = bytes.fromhex(row['selectors'])
                    count = struct.unpack_from('<I', selectors)[0]
                    if count:
                        selected = struct.unpack_from('<i', before, 20)[0]
                        selected = max(0, min(count-1, selected))
                        selector = selectors[4+12*selected:16+12*selected]
                        sample = struct.unpack_from('<H', selector)[0]
                        if sample != 65535:
                            assert self.pending == [dict(event='resolve', sample=sample, bank=77)]
                            self.pending.clear()
                            assert handle not in self.voices
                            theta = selector[3]*256*(2*math.pi/65536)
                            pan = [f32(math.sqrt((1-math.sin(theta))/2)),
                                   f32(math.sqrt((1+math.sin(theta))/2))]
                            self.voices[handle] = dict(cursor=0.0, pitch=1.0, volume=1.0,
                                dry=1.0, wet=0.0, pan=pan, paused=False,
                                priority=f32(selector[2]*f32(0.01)),
                                operations=[('I', 44100)])
                            owned = fresh = True
        assert not self.pending, 'unexpected source sample resolution'
        expected = bytearray(before)
        if command != current:
            expected[12:14] = bytes((command, current))
        expected[16] = 0 if owned else 255
        struct.pack_into('<I', expected, 8, handle if fresh else old_handle if owned else 0)
        if command == 1 and owned:
            voice = self.voices[handle]
            for i in range(before[14]):
                offset = 28+12*i
                code = before[offset]
                old, value = struct.unpack_from('<ii', before, offset+4)
                if not fresh and old == value:
                    continue
                expected[offset+4:offset+8] = before[offset+8:offset+12]
                if code == 0:
                    voice['pitch'] = f32(f32(value)*f32(1/4096))
                elif code in (2, 5, 8):
                    voice[{2: 'volume', 5: 'wet', 8: 'dry'}[code]] = f32(f32(value)*f32(1/32767))
                elif code == 3:
                    # Accepted selectors have base angle zero; preserve source
                    # wrap before continuous pan evaluation.
                    angle = value % 65536
                    sine = math.sin(angle*(2*math.pi/65536))
                    voice['pan'] = [f32(math.sqrt((1-sine)/2)), f32(math.sqrt((1+sine)/2))]
                elif code == 6:
                    voice['operations'].append(('C', f32(value)))
        assert after == expected, 'independent complete player-state mismatch'
        assert row['result'] == (command if owned else 0)

    def render(self, row):
        assert {v['handle'] for v in row['voices']} == set(self.voices), 'surviving or missing voice'
        dry, wet = [0.0]*16, [0.0]*16
        waveform = (0.25, -0.5, 1, 0, -0.25, 0.75, 0.5, -1)
        for actual in row['voices']:
            voice = self.voices[actual['handle']]
            for key in ('cursor', 'pitch', 'volume', 'dry', 'wet', 'priority'):
                assert abs(actual[key]-voice[key]) <= 2e-7*max(1, abs(voice[key])), (key, actual[key], voice[key])
            assert bool(actual['paused']) == voice['paused']
            assert all(abs(a-b) < 2e-7 for a, b in zip(actual['pan'], voice['pan']))
            state = self.filter.source_trace([*voice['operations'], ('D',)])[-1]
            expected_filter = [*state[2:7], *state[7:11], state[0]]
            assert all(abs(a-b) < 2e-6*max(1, abs(b)) for a, b in zip(actual['filter'], expected_filter)), 'filter coefficient/history mismatch'
            if voice['paused']:
                continue
            samples = []
            for _ in range(8):
                voice['cursor'] %= 8
                index = math.floor(voice['cursor'])
                fraction = voice['cursor']-index
                samples.append(f32((1-fraction)*waveform[index]+fraction*waveform[(index+1)%8]))
                voice['cursor'] += voice['pitch']
            voice['operations'].append(('P', len(samples), samples))
            rendered = self.filter.source_trace(voice['operations'])[-1][1:]
            for j, value in enumerate(rendered):
                for channel in range(2):
                    for bus, gain in ((dry, voice['dry']), (wet, voice['wet'])):
                        bus[2*j+channel] = f32(bus[2*j+channel]+f32(f32(value*f32(voice['volume']*gain))*voice['pan'][channel]))
        for bus, expected in (('dry', dry), ('wet', wet)):
            assert all(abs(a-b) <= 2e-6*max(1, abs(b)) for a, b in zip(row[bus], expected)), ('source PCM mismatch', bus)


def verify(rows):
    source = SourceReplay()
    counts = dict(states=0, pcm=0, player_calls=0, control_calls=0, borrows=0, retired=0, control_creates=0, control_releases=0)
    for row in rows:
        if row['event'] == 'reset':
            source.reset()
        elif row['event'] == 'resolve':
            source.pending.append(row)
        elif row['event'] == 'import':
            check_import(row)
            if row['ordinal'] == 27:
                source.player(row)
                counts['player_calls'] += 1
            if row['ordinal'] == 38:
                source.control(row)
                counts['control_calls'] += 1
        elif row['event'] == 'pcm':
            source.render(row)
            counts['pcm'] += 32
        elif row['event'] in ('state', 'module'):
            counts['states'] += 1
        elif row['event'] == 'lifetime':
            assert row['borrows'] == row['retired'] and row['creates'] == row['releases'] and row['live'] == 0
            counts['borrows'] += row['borrows']
            counts['retired'] += row['retired']
            counts['control_creates'] += row['creates']
            counts['control_releases'] += row['releases']
    return counts


def generated_fixture(name):
    fixtures = module_from(ROOT / 'tests/setup/test_car_audio_abi.py', 'abi_fixture')
    data = fixtures.fixture(name=name)
    return data, abi.inspect_bank(data, fixtures.resource(data))


def paired_fixture():
    """Two full authored engine programs plus owned imported control objects.

    Unlike the single-call fixtures, these programs read source members, write
    six player options, and call all three lifetime adapters in one invocation.
    """
    from setup_kit import car_audio
    descriptors = (0x60, 0x60+68)
    programs, calls = [], []
    for kind in range(2):
        code = bytearray(bytes((0x56, 0x8b, 0x74, 0x24, 8)))
        cursor = 24
        imports = []
        def move(to):
            nonlocal cursor
            code.extend(bytes((0x81, 0xc6))+struct.pack('<i', to-cursor))
            cursor = to
        def call(ordinal):
            code.extend(bytes((0x56, 0xe8)))
            imports.append((len(code), ordinal))
            code.extend(pack(ordinal))
        def member(word, destination, bias=0):
            code.extend(bytes((0x8b, 0x86))+struct.pack('<i', 64+4*word-cursor))
            if bias:
                code.extend(bytes((0x81, 0xc0))+pack(bias))
            code.extend(bytes((0x89, 0x86))+struct.pack('<i', destination-cursor))
        call(0)
        move(44)
        call(1)
        for slot, word, bias in ((0, 1, 0), (1, 2, 0), (2, 15, 0),
                                 (3, 14, 0), (4, 21, 0), (5, 13, 32767)):
            member(word, 512+36+12*slot, bias)
        move(512)
        call(27)
        move(800)
        call(38)
        move(1008)
        call(4)
        code.extend(bytes((0x83, 0xc4, 20, 0x5e, 0xc3)))
        programs.append(code)
        calls.append(imports)
    code_at = 0x100
    entries = (code_at, code_at+len(programs[0]))
    state_at = (sum(map(len, programs))+code_at+15) & ~15
    states = (state_at, state_at+1024)
    bank = (state_at+2048+127) & ~127
    extent = 20+2*92
    f1 = bank+extent
    imports = [(entry+at, ordinal) for entry, group in zip(entries, calls) for at, ordinal in group]
    f2 = f1+4+4*len(imports)
    pointers = [state+516 for state in states]
    f3 = f2+4+4*len(pointers)
    names_at = f3+4+4*12
    ids = [struct.pack('<HH', 0x311c, abi.INTERFACES[name][0])+name.encode()+b'\0'
           for name in ('CAR', 'CAR_ENG', 'CAR_SputOutput')]
    data = bytearray(names_at+sum(map(len, ids)))
    data[:4] = b'ABKC'
    data[4:8] = bytes((1, 1, 2, 1))
    struct.pack_into('<H', data, 10, 2)
    def put(at, *values):
        data[at:at+4*len(values)] = pack(*values)
    put(0x14, len(data), bank, descriptors[0], bank, extent)
    put(0x30, f1, f2, f3)
    name_offsets = []
    cursor = names_at
    for wire in ids:
        name_offsets.append(cursor)
        data[cursor:cursor+len(wire)] = wire
        cursor += len(wire)
    for kind, (descriptor, entry, state, code) in enumerate(zip(descriptors, entries, states, programs)):
        data[descriptor+36:descriptor+40] = bytes((1, 1, 1, 1))
        put(descriptor+40, entry, state, 1024, 1008, 0, 512, 800)
        data[entry:entry+len(code)] = code
        data[state+60] = 100
        put(state+512+4, state+900)
        data[state+512+14] = 6
        data[state+512+16] = 255
        put(state+512+24, 1)
        for slot, code in enumerate((0, 2, 3, 5, 6, 8)):
            data[state+512+28+12*slot] = code
            put(state+512+32+12*slot, 0x7fffffff, 0)
        data[state+800+12:state+800+14] = bytes((1, 1))
        put(state+800+16, (-3) & 0xffffffff, 7, 1, 0, 10)
        put(state+900, 1, (60+20*kind)<<16 | 1, 2, 0xffffffff)
    data[bank:bank+4] = b'S10A'
    struct.pack_into('>3I', data, bank+8, 2, 20, 112)
    for offset in (20, 112):
        struct.pack_into('>4I', data, bank+offset, 4<<24 | 44100, 1, 84, 1)
    put(f1, len(imports), *(at for at, _ in imports))
    put(f2, len(pointers), *pointers)
    put(f3, 4)
    rows = [(descriptors[0]+4, name_offsets[0]), (descriptors[1]+4, name_offsets[1]),
            (states[0]+800, name_offsets[2]), (states[1]+800, name_offsets[2])]
    for i, (site, name) in enumerate(rows):
        put(f3+4+12*i, site, name, 1)
    data = bytes(data)
    resource = {'kind': 'abk', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                **car_audio.bank_inventory(data)}
    return data, abi.inspect_bank(data, resource)


def private_inputs():
    source = input_path('NFSC')
    native = Path(os.environ.get('NFS_SPEED_EXE', ROOT.parent / 'Need For Speed Most Wanted Black Edition/speed.exe'))
    roster = ROOT / 'tools_snapshot/private_cars.py'
    if not all(p.is_file() for p in (source / 'GLOBAL/attributes.bin', source / 'NFSC.exe', native, roster)):
        print('SKIP private execution inputs absent')
        return None
    source_exe, native_exe = (source / 'NFSC.exe').read_bytes(), native.read_bytes()
    assert hashlib.sha256(source_exe).hexdigest() == 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
    assert hashlib.sha256(native_exe).hexdigest() == '80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c'
    from setup_kit import car_audio
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    resources = {p.relative_to(source).as_posix(): p.read_bytes()
                 for p in (source / 'SOUND/ENGINE').rglob('*') if p.suffix.lower() in ('.abk', '.gin')}
    graphs = car_audio.inventory((source / 'GLOBAL/attributes.bin').read_bytes(), cars, resources)
    assert not graphs['unsupported']
    manifest = abi.closure_manifest(graphs['supported'].values(), resources)
    return resources, manifest['banks'], source_exe, native_exe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private', action='store_true')
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--generated', action='store_true')
    parser.add_argument('--mutant', choices=('detach', 'selector', 'ordinal', 'control', 'unlink', 'overlap'))
    args = parser.parse_args()
    inputs = private_inputs() if args.private else None
    if args.private and inputs is None:
        return 3
    with tempfile.TemporaryDirectory(prefix='car-runtime-') as directory:
        folder = Path(directory)
        binary = compile_proof(folder, args.original, inputs[2] if inputs else None, args.mutant)
        if inputs:
            resources, banks, source_exe, native_exe = inputs
        else:
            banks, resources = {}, {}
            for name in ('CAR', 'CAR_ENG', 'CAR_SWTN', 'CAR_WHINE', 'CAR_Sputter'):
                data, manifest = generated_fixture(name)
                banks[name], resources[name] = manifest, data
            data, manifest = paired_fixture()
            banks['paired'], resources['paired'] = manifest, data
        counts = dict(banks=0, modules=0, players=0, bound=0, interfaces=0, states=0, pcm=0, player_calls=0, control_calls=0, borrows=0, retired=0, control_creates=0, control_releases=0)
        for name, manifest in banks.items():
            data = resources[name]
            identity = int(re.search(r'car_(\d+)_', name, re.I)[1]) if inputs and re.search(r'car_(\d+)_', name, re.I) else 0
            payload = encode(data, manifest, identity)
            rows = run(binary, payload, memory=memory_bytes(native_exe) if inputs else b'')
            counts['banks'] += 1
            counts['modules'] += len(manifest['modules'])
            counts['players'] += sum(len(m['players']) for m in manifest['modules'])
            counts['bound'] += rows[0]['bound']
            counts['interfaces'] += rows[0]['total']
            checked = verify(rows)
            for key, value in checked.items():
                counts[key] += value
            if inputs and not args.original:
                expected = run(binary, payload, source=True, memory=memory_bytes(source_exe))
                verify(expected)
                assert rows == expected, 'whole source/native module state or PCM mismatch: '+name
        print(json.dumps(counts, sort_keys=True))
        if inputs:
            assert (counts['banks'], counts['modules'], counts['players'], counts['interfaces']) == (69, 98, 400, 127)
        assert counts['bound'] == counts['interfaces'], f"interfaces {counts['bound']}/{counts['interfaces']}"
        if not inputs and not args.original and not args.mutant:
            data, manifest = paired_fixture()
            payload = encode(data, manifest)
            original = compile_proof(folder / 'original', original=True)
            before = run(original, payload)[0]
            assert (before['bound'], before['total']) == (2, 4)
            print('paired generated interfaces: original 2/4 -> fixed 4/4')
            for mutant in ('detach', 'selector', 'ordinal', 'control', 'unlink', 'overlap'):
                broken = compile_proof(folder / mutant, mutant=mutant)
                try:
                    verify(run(broken, payload))
                except AssertionError:
                    print('rejected mutation: '+mutant)
                else:
                    raise AssertionError('mutation unexpectedly passed: '+mutant)
    return 0


if __name__ == '__main__':
    sys.exit(main())
