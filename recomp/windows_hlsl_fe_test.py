"""Front-end HLSL translation and bounded C bridge, discovered offline.

Four pinned retail programs: fixed 4/4; legacy interface control 2/4;
t1->t0 binding mutant 3/4; ordinary DP4->MUL control lives in windows_image_test.
Bridge negatives include malformed/truncated bytecode and decoded unsupported ops.
A missing-NUL-size bridge mutant fails its buffer check (exit 1).
Windows: --sdk-headers compiles with real SDK/CRT and requires D3DCompile 4/4,
negative rejection and exit 0. Missing private fixtures returns 3.
Desktop wrapper integration: check hlsl_fe -> this script --sdk-headers.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SHADERS = ROOT / 'host/shaders'
# Bytecode and expected HLSL digests, in VS/PS order for each pair.
PINS = (
    ('fe', 'vs', 'd052fd907941ee4a7f5194ef6fac3e402ea4c6cbbc34dece044fd7eae6e08019', 'ad7daf80290ecbcbfad450f4c197c8bc74d35061163b3e6d7e963018efc7d11e'),
    ('fe', 'ps', 'adf1eb6cc3716d24759139d3af433f2d177f423bdb0649a814cae70aee07b326', 'a2bcaa73420848289e4d97bcb6cde092ef42569dedc091f43a68511efbbe9a2d'),
    ('fe_mask', 'vs', 'b5ef251e76682d1aa9980558ac99cecc3c71ecb883ede02620e34ec354c2a439', '7682afca619b8c50f44f36a81315f17abb11c833b55726940af52b8f3e6f2040'),
    ('fe_mask', 'ps', '9a611e53e931af0f1ebdd4d7c1c12fa0923f58bd87dc7474a4c5d1ea0ae4dd6f', '05784f0f5e4a4676996d8b3bb26b05b55ea9d41c543d74e5bd516763873420b6'),
)

HARNESS = r'''
#include "sm_hlsl_c.h"
#include "hlsl.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#ifdef REAL_SDK
#include <windows.h>
#include <d3dcompiler.h>
#endif
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"check line %d: %s\n",__LINE__,#x); return 1; } } while(0)
extern "C" int header_is_c(void);
static std::vector<unsigned char> read(const char *path) {
    FILE *f=fopen(path,"rb"); if (!f) std::abort();
    fseek(f,0,SEEK_END); long n=ftell(f); rewind(f);
    std::vector<unsigned char> b(n); if(fread(b.data(),1,b.size(),f)!=b.size()) std::abort();
    fclose(f); return b;
}
static int reject(const void *b,size_t n) {
    char out[32]="stale",error[8]; memset(error,'?',sizeof error);
    size_t size=99; sm_hlsl_interface info; memset(&info,255,sizeof info);
    auto rc=sm_hlsl_translate(b,n,out,sizeof out,&size,&info,error,sizeof error);
    return rc!=SM_HLSL_OK && rc!=SM_HLSL_BUFFER_TOO_SMALL && !out[0] && !size &&
        !info.stage && !info.texture_mask && !info.input_mask && error[0] && !error[7];
}
int main(int argc,char **argv) {
    CHECK(argc==6 && header_is_c());
    unsigned compilations=0, negative=0;
    for (unsigned fixture=0;fixture<4;++fixture) {
        auto b=read(argv[fixture+1]); size_t need=0; char error[128]; sm_hlsl_interface info={};
        CHECK(sm_hlsl_translate(b.data(),b.size(),nullptr,0,&need,&info,error,sizeof error)==SM_HLSL_BUFFER_TOO_SMALL);
        CHECK(need>1 && need<=SM_HLSL_MAX_SOURCE && error[0]);
        CHECK(info.stage==fixture%2 && info.texture_mask==(fixture<2?1u:3u));
        CHECK(info.input_mask==(fixture%2?0u:fixture<2?7u:15u));
        if (!info.stage) {
            CHECK(info.input_usage[0]==0 && info.input_usage[1]==10 && info.input_usage[2]==5);
            CHECK(info.input_usage_index[2]==0);
            if(fixture==2) CHECK(info.input_usage[3]==5 && info.input_usage_index[3]==1);
        }
        std::vector<char> text(need+2,'?');
        for(size_t cap: {size_t(0),size_t(1),need-1}) {
            memset(text.data(),'?',text.size()); size_t actual=0;
            CHECK(sm_hlsl_translate(b.data(),b.size(),text.data()+1,cap,&actual,&info,error,1)==SM_HLSL_BUFFER_TOO_SMALL);
            CHECK(actual==need && !error[0] && text[0]=='?' && text[cap+1]=='?');
            CHECK(cap?!text[1]:text[1]=='?');
        }
        for(unsigned repeat=0;repeat<128;++repeat) {
            size_t actual=0; memset(text.data(),'?',text.size());
            CHECK(sm_hlsl_translate(b.data(),b.size(),text.data()+1,need,&actual,&info,error,sizeof error)==SM_HLSL_OK);
            CHECK(actual==need && text[0]=='?' && !text[need] && text[need+1]=='?' && !error[0]);
        }
        const char *source=text.data()+1;
        char path[1024]; snprintf(path,sizeof path,"%s/%u.hlsl",argv[5],fixture);
        FILE *out=fopen(path,"wb"); CHECK(out);
        CHECK(fwrite(source,1,need-1,out)==need-1); CHECK(!fclose(out));
#ifdef REAL_SDK
        ID3DBlob *code=nullptr,*errors=nullptr;
        HRESULT hr=D3DCompile(source,need-1,"translated",nullptr,nullptr,"sm_main",
            info.stage?"ps_5_0":"vs_5_0",D3DCOMPILE_ENABLE_STRICTNESS,0,&code,&errors);
        if(FAILED(hr) && errors) fprintf(stderr,"%.*s\n",int(errors->GetBufferSize()),(char*)errors->GetBufferPointer());
        if(errors) errors->Release();
        CHECK(SUCCEEDED(hr) && code && code->GetBufferSize()); code->Release(); ++compilations;
#endif
        std::vector<unsigned char> unaligned(b.size()+1); memcpy(unaligned.data()+1,b.data(),b.size());
        size_t actual=0;
        CHECK(sm_hlsl_translate(unaligned.data()+1,b.size(),text.data(),text.size(),&actual,nullptr,nullptr,0)==SM_HLSL_OK);
        // Every prefix lacks END, including unaligned byte counts and partial comments.
        for(size_t n=0;n<b.size();++n) { CHECK(reject(b.data(),n)); ++negative; }
        auto bad=b; bad[0]=0; CHECK(reject(bad.data(),bad.size())); ++negative;
        bad=b; bad.insert(bad.end(),{0,0,0,0}); CHECK(reject(bad.data(),bad.size())); ++negative;
        CHECK(reject(nullptr,b.size()) && reject(b.data(),SM_HLSL_MAX_BYTECODE+4)); negative+=2;
        CHECK(sm_hlsl_translate(b.data(),b.size(),nullptr,1,&actual,nullptr,error,sizeof error)==SM_HLSL_INVALID_ARGUMENT);
        CHECK(sm_hlsl_translate(b.data(),b.size(),text.data(),text.size(),nullptr,nullptr,error,sizeof error)==SM_HLSL_INVALID_ARGUMENT);
        CHECK(sm_hlsl_translate(b.data(),b.size(),text.data(),text.size(),&actual,nullptr,nullptr,1)==SM_HLSL_INVALID_ARGUMENT);
        // Parser accepts DP3, but this bounded backend must reject it explicitly.
        if (!info.stage) {
            std::vector<uint32_t> words(b.size()/4); memcpy(words.data(),b.data(),b.size());
            sm::Shader s; CHECK(sm::parse(words.data(),words.size(),s));
            bool changed=false;
            for (auto &i:s.code) if(i.op==sm::OP_DP4) {
                words[i.token_offset]=(words[i.token_offset]&0xffff0000u)|sm::OP_DP3; changed=true; break;
            }
            CHECK(changed && reject(words.data(),b.size())); ++negative;
            std::string result,why;
            for(int mutation=0;mutation<8;++mutation) {
                auto invalid=s;
                if(mutation==0) invalid.code[0].usage_index=7;
                if(mutation==1) invalid.code[1].dst.num=invalid.code[0].dst.num;
                if(mutation==2) invalid.code[1].usage=invalid.code[0].usage;
                for(auto &i:invalid.code) if(i.op==sm::OP_DP4) {
                    if(mutation==3) i.src[0].mod=sm::SM_BIAS;
                    if(mutation==4) i.dst.shift=1;
                    if(mutation==5) i.dst.sat=true, i.dst.pp=true;
                    if(mutation==6) i.src[0].num=4;
                    if(mutation==7) i.src[0].swz[0]=4;
                    break;
                }
                result="stale"; CHECK(!sm::emit_hlsl(invalid,result,why) && result.empty() && !why.empty()); ++negative;
            }
            auto modified=s;
            for(auto &i:modified.code) if(i.op==sm::OP_DP4) {
                i.src[0].mod=sm::SM_NEG;
                for(unsigned lane=0;lane<4;++lane) i.src[0].swz[lane]=3-lane;
                i.dst.sat=true; break;
            }
            CHECK(sm::emit_hlsl(modified,result,why));
            CHECK(result.find("saturate(sm_dp4(-v0.wzyx")!=std::string::npos);
            // Register numbering is determined by declarations, not ordinal assumptions.
            auto remapped=s;
            for(auto &i:remapped.code) {
                if(i.op==sm::OP_DCL && i.dst.num<2) i.dst.num=1-i.dst.num;
                for(auto &r:i.src) if(r.type==sm::R_INPUT && r.num<2) r.num=1-r.num;
            }
            CHECK(sm::emit_hlsl(remapped,result,why));
            CHECK(result.find("v0 = input.color")!=std::string::npos && result.find("v1 = input.position")!=std::string::npos);
        } else {
            std::vector<uint32_t> words(b.size()/4); memcpy(words.data(),b.data(),b.size());
            sm::Shader s; CHECK(sm::parse(words.data(),words.size(),s));
            std::string result,why;
            for(int mutation=0;mutation<4;++mutation) {
                auto invalid=s;
                for(auto &i:invalid.code) if(i.coissue) {
                    if(mutation==0) i.src.back().swz[3]=0;
                    if(mutation==1) i.dst.mask=15;
                    if(mutation==2) i.src.back().swz[3]=255;
                    if(mutation==3) i.dst.shift=-1;
                }
                CHECK(!sm::emit_hlsl(invalid,result,why) && result.empty()); ++negative;
            }
        }
    }
#ifdef REAL_SDK
    CHECK(compilations==4);
    printf("D3DCompile PASS: %u/4 native shader compilations\n",compilations);
#else
    (void)compilations;
    puts("D3DCompile PENDING: run --sdk-headers on Windows");
#endif
    printf("bridge PASS: 4 fixtures, 512 repeated calls, %u negative cases; C ABI and buffer boundaries\n",negative);
    return 0;
}
'''


def run(argv, expected=0):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True)
    assert result.returncode == expected, (result.returncode, result.stdout[-2000:], result.stderr[-3000:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-headers', action='store_true')
    parser.add_argument('--compiler', default='clang')
    parser.add_argument('--shader-dir', type=Path, default=SHADERS / 'testdata')
    args = parser.parse_args()
    fixtures = [args.shader_dir / pair / f't0_world_p0_{stage}_1_1.bin' for pair, stage, _, _ in PINS]
    goldens = [SHADERS / 'testdata' / pair / f'{pair}_{stage}.hlsl.golden' for pair, stage, _, _ in PINS]
    if not all(p.is_file() for p in fixtures):
        print('SKIP hlsl_fe: private bytecode absent')
        return 3
    compiler = shutil.which(args.compiler)
    if not compiler:
        print('SKIP hlsl_fe: compiler absent')
        return 3
    if args.sdk_headers and os.name != 'nt':
        raise RuntimeError('--sdk-headers requires native Windows SDK/CRT')
    for binary, golden, (_, _, digest, expected) in zip(fixtures, goldens, PINS):
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest, binary
        assert hashlib.sha256(golden.read_bytes()).hexdigest() == expected, golden
    with tempfile.TemporaryDirectory(prefix='hlsl-fe-') as tmp:
        folder = Path(tmp)
        harness = folder / 'bridge_check.cpp'; harness.write_text(HARNESS, encoding='utf-8')
        c = folder / 'header.c'
        c.write_text('#include "sm_hlsl_c.h"\nint header_is_c(void) { return SM_HLSL_OK==0 && sizeof(sm_hlsl_interface)==44; }\n')
        obj = folder / ('header.obj' if os.name == 'nt' else 'header.o')
        flags = ['-Wall', '-Wextra', '-Werror', '-D_CRT_SECURE_NO_WARNINGS']
        run([compiler, '-std=c11', *flags, '-I', SHADERS, '-c', c, '-o', obj])
        common = [compiler, '--driver-mode=g++', '-std=c++17', '-O1', *flags,
                  '-I', SHADERS, '-I', ROOT / 'runtime/win/image']
        sdk = ['-DREAL_SDK', '-ld3dcompiler'] if args.sdk_headers else []
        sanitizer = [] if os.name == 'nt' else ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        tool = folder / ('bridge.exe' if os.name == 'nt' else 'bridge')
        run([*common, *sanitizer, harness, obj, SHADERS / 'sm_parse.cpp', SHADERS / 'sm_hlsl.cpp',
             SHADERS / 'sm_hlsl_c.cpp', *sdk, '-o', tool])
        print(run([tool, *fixtures, folder]).strip())
        bridge = (SHADERS / 'sm_hlsl_c.cpp').read_text()
        original_size = '*required = text.size() + 1;'
        assert bridge.count(original_size) == 1
        bad_bridge = folder / 'bad_bridge.cpp'
        bad_bridge.write_text(bridge.replace(original_size, '*required = text.size();'))
        bad_tool = folder / ('bad_bridge.exe' if os.name == 'nt' else 'bad_bridge')
        run([*common, *sanitizer, harness, obj, SHADERS / 'sm_parse.cpp', SHADERS / 'sm_hlsl.cpp',
             bad_bridge, *sdk, '-o', bad_tool])
        run([bad_tool, *fixtures, folder], 1)
        print('missing-NUL-size bridge mutant: buffer check rejected, exit 1')
        good = sum((folder / f'{i}.hlsl').read_bytes() == golden.read_bytes() for i, golden in enumerate(goldens))
        print(f'HLSL fe + fe_mask golden {good}/4')
        assert good == 4
        emitter = (SHADERS / 'sm_hlsl.cpp').read_text()
        old = 'i.dst.num >= 4 ||'
        assert emitter.count(old) == 1
        # Restore the two original interface limits as an independent failure control.
        legacy = emitter.replace(old, 'i.dst.num >= 3 ||').replace('type == R_ADDR && num < 2)', 'type == R_ADDR && num == 0)')
        mutant = folder / 'legacy.cpp'; mutant.write_text(legacy)
        translate = ROOT / 'runtime/win/image/translate.cpp'
        legacy_tool = folder / ('legacy.exe' if os.name == 'nt' else 'legacy')
        run([*common, SHADERS / 'sm_parse.cpp', mutant, translate, '-o', legacy_tool])
        for i, fixture in enumerate(fixtures):
            log = run([legacy_tool, fixture, folder / 'legacy.hlsl'], 0 if i<2 else 1)
            if i==2: assert 'unsupported HLSL input declaration' in log
            if i==3: assert 'unsupported HLSL destination' in log
        print('legacy interface control: 2/4; masked VS input declaration and PS destination rejected')
        original = 'i.dst.num, i.dst.num, i.dst.num); sampled'
        assert emitter.count(original) == 1
        mutant.write_text(emitter.replace(original, '0u, i.dst.num, i.dst.num); sampled'))
        broken = folder / ('binding.exe' if os.name == 'nt' else 'binding')
        run([*common, SHADERS / 'sm_parse.cpp', mutant, translate, '-o', broken])
        matched = 0
        for fixture, golden in zip(fixtures, goldens):
            target = folder / 'mutant.hlsl'
            run([broken, fixture, target])
            matched += target.read_bytes() == golden.read_bytes()
        print(f't1->t0 binding mutant golden {matched}/4; check exits 1')
        assert matched == 3
        # Exercise the same failing comparison in a subprocess, including its exit code.
        run([sys.executable, '-I', '-c',
             'import pathlib,sys; sys.exit(pathlib.Path(sys.argv[1]).read_bytes()!=pathlib.Path(sys.argv[2]).read_bytes())',
             target, goldens[-1]], 1)
    print('windows_hlsl_fe_test PASS: golden 4/4; legacy 2/4; binding mutant 3/4 and bridge mutant rejected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
