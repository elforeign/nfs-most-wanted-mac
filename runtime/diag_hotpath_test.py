#!/usr/bin/env python3
"""Native diagnostic routing/selector fragments; no game or private assets.

Before/after: _CIpow events=1/ring=0 -> events=0/ring=1 (old-noisy fails).
Also proves floor/GetTechnique ring routing, failing HRESULT and all-mode logging,
and selector parity with one getenv per name, including absent/empty lists.
Live/all: 1394 checks each, 0 failures; old noisy/COM/selector: 3/11/455 failures.
Pinned old COM and selector controls must fail independently. Auto-discovered by
runtime/offline_tests.sh; baseline fragments are SHA-256 checked without history.
"""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD_NOISY = '        static const char *const noisy[] = {"CriticalSection", "TlsGetValue", "TlsSetValue", "LastError", "QueryPerformance", "timeGetTime",\n                                            "Interlocked", "HeapAlloc", "HeapFree", "HeapReAlloc", "HeapValidate", "IsBadReadPtr", "GetCurrentThreadId", "GetTickCount",\n                                            "D3DXMatrix", "D3DXVec", "D3DXQuaternion", "D3DXPlane", "WaitForSingleObject", "ReleaseMutex",\n                                            "SetWaitableTimer", "ReleaseSemaphore", "PeekMessage", "Sleep"};\n'
OLD_NOISY_SHA256 = '52cfbf5ed45b091ca80f377bb5f142d678b0480d98838dddb2732c801514ca55'
OLD_COM = '            static int all = -1;\n            if (all < 0) all = getenv("BOOT_DIAG_EVENTS") && !strcmp(getenv("BOOT_DIAG_EVENTS"), "all");\n            static int8_t logged_of[4096];                 // per method: 1 ring only, 2 events.log (creations, resets)\n            int8_t *lg = &logged_of[(t - HOSTFN_BASE) / 4];\n            if (!*lg) *lg = strstr(hn, "Create") || strstr(hn, "Reset") ? 2 : 1;\n            if (all || *lg == 2 || c->eax >= 0x80000000u)\n                diag_event("%s(%X, %X, %X, %X, %X, %X) -> %X @%08X", hn, a[0], a[1], a[2], a[3], a[4], a[5], c->eax, ra);\n            else\n                diag_call_ring(hn, 6, a, c->eax, ra);\n'
OLD_COM_SHA256 = '2ef12ddf2a901837a605afeade3e39bbee6141465b2abd4a32de2f88ab280ec2'
OLD_PARSER = "/* Absolute-frame selectors only: no assumptions about a private game's state. */\nstatic int private_frame_wanted(uint64_t frame, const char *list_env) {\n    const char *p=getenv(list_env);\n    while(p && *p && *p!=':') {\n        char *end;unsigned long long n=strtoull(p,&end,10);\n        if(end==p)return 0;\n        if(n==frame && (!*end || *end==',' || *end==':'))return 1;\n        if(*end!=',')return 0;p=end+1;\n    }\n    return 0;\n}\n"
OLD_PARSER_SHA256 = '1a4e52f63c2bfdcae26cf7c7d29b8dc403acf8ccb79e61235962033db150ad93'
OLD_FRAME = 'int diag_frame_wanted(uint64_t frame, const char *list_env) { return private_frame_wanted(frame,list_env); }'
OLD_FRAME_SHA256 = '32a2b8c69fcc63b36beed6359888e5b7e933254d472601cbc8c598a8b75896b7'


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    for value, digest in ((OLD_NOISY, OLD_NOISY_SHA256), (OLD_COM, OLD_COM_SHA256),
                          (OLD_PARSER, OLD_PARSER_SHA256), (OLD_FRAME, OLD_FRAME_SHA256)):
        assert hashlib.sha256(value.encode()).hexdigest() == digest
    boot = (ROOT / 'runtime/boot.c').read_text()
    imports = boot.split('        // high-frequency calls go to the in-memory ring only', 1)[1]
    imports = imports.split('\n', 1)[1].split('\n    }\n    if (boot_trace', 1)[0]
    com = boot.split('            static int all = -1;', 1)[1]
    com = '            static int all = -1;' + com.split('            void render_dump_call', 1)[0]
    diag = (ROOT / 'recomp/private_runtime/game_diagnostics.inc').read_text()
    selector = function(diag, 'int diag_frame_wanted(')
    parser = (ROOT / 'recomp/private_runtime/frame_shots.inc').read_text().split('static int private_shot_due')[0]
    # Pin the original grammar, not merely two wrappers around today's parser.
    assert parser == OLD_PARSER
    pre = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static unsigned events, rings, failures, checks, env_calls;
static uint64_t race_frame, key_frame;
static char *counted_getenv(const char *name) { ++env_calls; return getenv(name); }
static void diag_event(const char *fmt, ...) { (void)fmt; ++events; }
static void diag_call_ring(const char *name,int n,const uint32_t *a,uint32_t ret,uint32_t ra) {
    (void)name; (void)n; (void)a; (void)ret; (void)ra; ++rings;
}
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); } } while(0)
typedef struct { uint32_t eax; } Cpu;
#define HOSTFN_BASE 0xF0000000u
'''
    harness = pre + r'''
static void import_call(const char *name, const char *fn, int import_index) {
    Cpu cpu={0}, *c=&cpu;
    uint32_t a0=0,a1=0,a2=0,a3=0,ret_addr=0;
''' + imports + r'''
}
static void com_call(const char *hn, uint32_t result, unsigned index) {
    Cpu cpu={result}, *c=&cpu;
    uint32_t t=HOSTFN_BASE+4*index, a[6]={0}, ra=0;
''' + com + r'''
}
#define getenv counted_getenv
''' + parser + '\n' + selector + r'''
#undef getenv
int main(void) {
    int all=getenv("BOOT_DIAG_EVENTS") && !strcmp(getenv("BOOT_DIAG_EVENTS"),"all");
    import_call("msvcrt::_CIpow","_CIpow",0);
    printf("_CIpow events=%u ring=%u\n",events,rings);
    CHECK(events==(unsigned)all && rings==(unsigned)!all);
    events=rings=0; import_call("msvcrt::floor","floor",1);
    CHECK(events==(unsigned)all && rings==(unsigned)!all);
    events=rings=0; import_call("msvcrt::_CIpow","_CIpow",0);
    CHECK(events==(unsigned)all && rings==(unsigned)!all); // cached import classification
    const char *getters[]={"GetParameter","GetParameterByName","GetParameterBySemantic",
        "GetParameterElement","GetTechnique","GetTechniqueByName","GetPass","GetPassByName",
        "GetAnnotation","GetAnnotationByName","GetCurrentTechnique"};
    for(unsigned i=0;i<sizeof getters/sizeof *getters;++i) {
        char hn[80]; snprintf(hn,sizeof hn,"ID3DXEffect::%s",getters[i]);
        for(unsigned j=0;j<2;++j) { // first and cached classification; high-bit handle and null
            events=rings=0; com_call(hn,j?0:0xF2000000u,i);
            CHECK(events==(unsigned)all && rings==(unsigned)!all);
        }
    }
    const char *hresults[]={"ID3DXEffect::SetValue","ID3DXEffect::GetTechniqueDesc",
        "ID3DXEffect::GetParameterDesc","ID3DXEffect::GetPassDesc","ID3DXEffect::GetValue"};
    for(unsigned i=0;i<sizeof hresults/sizeof *hresults;++i) {
        events=rings=0; com_call(hresults[i],0x80004005u,20+i);
        CHECK(events==1 && rings==0);
        events=rings=0; com_call(hresults[i],0,20+i);
        CHECK(events==(unsigned)all && rings==(unsigned)!all);
    }
    events=rings=0;com_call("IDirect3DDevice9::CreateTexture",0,40);
    com_call("IDirect3DDevice9::Reset",0,41);CHECK(events==2 && rings==0);
    unsigned selector_env=0;
    const char *values[]={"1,3,10:4","","0,2, 4","race+1,key+2","3x,5","18446744073709551615",NULL};
    for(unsigned i=0;i<sizeof values/sizeof *values;++i) {
        char name[48];snprintf(name,sizeof name,"DIAG_HOTPATH_LIST_%u",i);
        if(values[i])setenv(name,values[i],1);else unsetenv(name);
        unsigned before=env_calls;
        for(unsigned step=0;step<4;++step) {
            race_frame=step/2;key_frame=step%2; // changes within a frame must invalidate the answer
            for(uint64_t f=0;f<16;++f) {
                int expected=private_frame_wanted(f,name);
                unsigned count=env_calls;
                CHECK(diag_frame_wanted(f,name)==expected);
                CHECK(diag_frame_wanted(f,name)==expected);
                CHECK(env_calls==count+(step==0 && f==0));
                selector_env+=env_calls-count;
            }
        }
        // Name ownership: caller can reuse its buffer, equal text must hit the same cache.
        char copy[48];strcpy(copy,name);
        int expected=private_frame_wanted(UINT64_MAX,name);
        unsigned count=env_calls;
        CHECK(diag_frame_wanted(UINT64_MAX,copy)==expected);
        selector_env+=env_calls-count;
        CHECK(env_calls-before==66); // 65 reference calls plus one lookup by the production selector
    }
    printf("DIAG HOTPATH checks=%u failures=%u selector_lists=7 selector_getenv=%u all=%d\n",checks,failures,selector_env,all);
    return failures?1:0;
}
'''
    with tempfile.TemporaryDirectory(prefix='diag-hotpath-') as td:
        td = Path(td)
        live_noisy = imports[imports.index('        static const char *const noisy[]'):].split('        static int8_t quiet_of', 1)[0]
        for mode in ('good', 'all', 'old-noisy', 'old-com', 'old-frame'):
            source = harness
            if mode == 'old-noisy':
                source = source.replace(live_noisy, OLD_NOISY)
            elif mode == 'old-com':
                source = source.replace(com, OLD_COM)
            elif mode == 'old-frame':
                source = source.replace(selector, OLD_FRAME)
            path = td / 'fixture.c'
            path.write_text(source)
            build = subprocess.run(['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-O1',
                            '-fsanitize=address,undefined', str(path), '-o', str(td / 'fixture')],
                           capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[:4000]
            env = dict(os.environ, BOOT_DIAG_EVENTS='all' if mode == 'all' else '')
            run = subprocess.run([str(td / 'fixture')], env=env, capture_output=True, text=True)
            print(mode + ': ' + run.stdout.strip().replace('\n', '; '))
            if run.returncode != (0 if mode in ('good', 'all') else 1):
                raise AssertionError(mode + ': ' + run.stderr[:3000])
    print('DIAG HOTPATH PASS: live/all=0 failures; all three pinned controls rejected')


if __name__ == '__main__':
    main()
