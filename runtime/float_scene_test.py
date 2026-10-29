#!/usr/bin/env python3
"""Execute production world-export transform and quad_into, including its PSO cache.
Ramp: normal exact=2050 clipped=0 nonfinite=0; BGRA8-copy mutant clipped=1792
(896 pixels x 1x/4x modes), copy_clip=5376 channels versus normal 0;
Off draw/copy compare 8208 bytes. Observed GPU-to-GPU final exposure differences:
32 below-white / 1788 above-white pixels (both sample modes combined). These
are reported separately: transport PASS does not satisfy strict picture isolation.
Native observations are in the README row; bare sandbox runs exit 3 after CPU checks.
The full offline suite autodiscovers this file. No private assets required.
"""
from pathlib import Path
import subprocess
import tempfile
import sys

root=Path(__file__).resolve().parent
source=(root/'render.m').read_text()
map_code=source[source.index('typedef struct { uint64_t key; void *obj; } MapSlot;'):source.index('// ---------------- GPU-side objects')]
blit=source[source.index('static const char *blit_src ='):source.index('void render_init(void)')]
quad=source[source.index('void quad_into(id<MTLCommandBuffer> cb,'):source.index('void render_stretch(')]
init=source[source.index('    NSError *err = nil;',source.index('void render_init(void)')):source.index('    render_resize();',source.index('void render_init(void)'))]
pre=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static int diag_on=1,gpt_on;
static char *diag_dir="/tmp";
static uint64_t frame_serial=1;
static id<MTLDevice> mtl;
static id<MTLCommandBuffer> cmd;
static id<MTLLibrary> blit_lib;
static id<MTLRenderPipelineState> blit_pso;
static id<MTLSamplerState> blit_linear,blit_point;
#define M32(a) 6
#define MF32(a) 15.0f
#define diag_event(...) ((void)0)
#define gpt_render_auto(...) ((void)0)
#define gpt_name(...) ((void)0)
static void end_pass(void){}
static void ensure_cmd(void){}
static uint32_t mipdim(uint32_t x,uint32_t l){return (x>>l)?x>>l:1;}
#define HDRCLIP_COPY_TEST 1
#include "hdrclip.m"
'''
main=r'''
int main(int argc,const char **argv){@autoreleasepool{
    NSString *s=@"fragment float4 world(){return float4(1);}";
    hdrclip_on=float_scene_on=0;
    if(hdrclip_world_source(s,1,0,0)!=s)return 1;
    float_scene_on=1;NSString *p=hdrclip_world_source(s,1,0,0);
    if(![p containsString:@"if(fs_store)hc_v="] || ![p containsString:@"as_type<uint4>(v)"] || [p containsString:@"hc_count("])return 1;
    float_scene_on=0;
    fprintf(stderr,"float_scene_test: CPU Off source identity / conditional finite export PASS\n");
    mtl=MTLCreateSystemDefaultDevice();
    if(!mtl){fprintf(stderr,"GPU PENDING: run on a Metal-capable host: bare float_scene_test.py\n");return 3;}
    INIT
    return float_scene_test(argc>1 && !strcmp(argv[1],"--mutant"));
}}
'''.replace('INIT',init)
with tempfile.TemporaryDirectory(prefix='float-scene-test-') as td:
    td=Path(td);m=td/'test.m';exe=td/'test'
    m.write_text(pre+map_code+'\nstatic Map blit_pipelines;\n'+blit+quad+main)
    build=subprocess.run(['clang','-O1','-fobjc-arc','-Wno-unused-function','-I',str(root),str(m),'-framework','Foundation','-framework','Metal','-o',str(exe)],capture_output=True,text=True)
    if build.returncode:
        print('\n'.join(build.stderr.splitlines()[-20:]));sys.exit(1)
    good=subprocess.run([str(exe)],capture_output=True,text=True)
    print(good.stderr,end='')
    if good.returncode==3:sys.exit(3)
    if good.returncode:sys.exit(1)
    bad=subprocess.run([str(exe),'--mutant'],capture_output=True,text=True)
    print(bad.stderr,end='')
    if bad.returncode!=1 or 'clipped=1792' not in bad.stderr:sys.exit('FAIL: forced BGRA8 copy survived')
    print('float_scene_test: PASS normal=0 mutant=1')
