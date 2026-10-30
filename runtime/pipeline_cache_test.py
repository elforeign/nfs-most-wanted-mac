#!/usr/bin/env python3
"""Execute the production descriptor cache on Metal; automatically in offline_tests.

Normal: listed frame builds 0; deliberately skipped prewarm: 2 (exit 1).
Race/free-roam baseline diagnostic control: listed builds 80/86 (exit 1).
Two held GPU renders compare 1024/1024 bytes for replay versus direct creation.
Every scene recipe round-trips at 1x/2x/4x/8x AA: 372 cases, zero differences;
omitting stencil rejects 292/372 cases (exit 1).
Presentation scope: one counted build; ignoring it counts zero (exit 1).
--diag DIR fails on any listed descriptor built on the frame path.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def diagnostic(path):
    text = (path / 'events.log').read_text()
    counts = [int(n) for n in re.findall(r'pipeline_frame:.*?known_total=(\d+)', text)]
    misses = len(re.findall(r'pipeline_known_miss:', text))
    failures = len(re.findall(r'pipeline_prewarm: (?:missing_shader|failed)=', text))
    catalogue = (ROOT / 'pipeline_cache.m').read_text().split('static const PipelineRecipe recipes[] = {', 1)[1].split('\n};', 1)[0]
    listed = [re.sub(r'\s+', '', line.split('//')[0]) for line in catalogue.splitlines() if '.vertex=' in line]
    recorded = re.findall(r'pipeline_recipe: \w+ (.*)', text)
    observed = 0
    for row in recorded:
        samples = re.search(r'\.samples=(\d+)', row)[1]
        row = re.sub(r'\s+', '', row)
        observed += any(row == recipe.replace('.samples=0,', f'.samples={samples},') for recipe in listed)
    total = max([misses, observed, *counts])
    assert counts, 'missing per-second frame-path pipeline diagnostics'
    print(f'pipeline check: listed builds={total} load failures={failures}')
    return int(total != 0 or failures != 0)


FIXTURES = r'''
+    {.vertex=1,.fragment=2,.samples=1,.color=MTLPixelFormatRGBA8Unorm,.mask=15,
+     .srcRGB=1,.srcAlpha=1,.attributes={[0]={MTLVertexFormatFloat2,0,0}},.layouts={[0]={8,1,1}}},
+    {.vertex=1,.fragment=2,.samples=1,.color=MTLPixelFormatRGBA8Unorm,.blend=1,.mask=7,
+     .srcRGB=1,.srcAlpha=1,.attributes={[0]={MTLVertexFormatFloat2,0,0}},.layouts={[0]={8,1,1}}},
'''.replace('+', '')
PRELUDE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include "pipeline_cache.h"
static id<MTLDevice> mtl;
static int diag_on=1;
static uint64_t host_ticks(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000000000ull+t.tv_nsec; }
static void diag_event(const char *s,...) { (void)s; }
'''
MAIN = r'''
static int failures;
#define CHECK(x) do {if(!(x)){++failures;fprintf(stderr,"failed: %s\n",#x);}}while(0)
static NSData *held(id<MTLRenderPipelineState> p) {
    id<MTLCommandQueue> q=[mtl newCommandQueue];
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:8 mipmapped:NO];
    td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
    id<MTLTexture> t=[mtl newTextureWithDescriptor:td];
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=t;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    rp.colorAttachments[0].clearColor=MTLClearColorMake(.1,.2,.3,.4);
    id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
    float v[]={-1,-1,3,-1,-1,3};[e setVertexBytes:v length:sizeof v atIndex:0];
    [e setRenderPipelineState:p];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
    [cb commit];[cb waitUntilCompleted];CHECK(cb.status==MTLCommandBufferStatusCompleted);
    unsigned char data[16*8*4];[t getBytes:data bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,8) mipmapLevel:0];
    return [NSData dataWithBytes:data length:sizeof data];
}
int main(int argc,const char **argv) { @autoreleasepool {
    mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("SKIP: needs Metal device");return 3;}
    setenv("NFS_PSO_PREWARM","1",1);
    NSError *error=nil;
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;struct A{float2 p [[attribute(0)]];};vertex float4 v(A a [[stage_in]]){return float4(a.p,0,1);}fragment float4 f(){return float4(.125,.25,.5,1);}";
    id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&error];CHECK(lib);
    pipeline_register_shader(1,[lib newFunctionWithName:@"v"]);pipeline_register_shader(2,[lib newFunctionWithName:@"f"]);
    unsigned roundtrips=0,bad_roundtrips=0;
    for(NSUInteger samples=1;samples<=8;samples*=2) {
        scene_sample_count=samples;
        for(size_t i=0;i<sizeof scene_recipes/sizeof *scene_recipes;++i) {
            PipelineRecipe expected=resolve_recipe(scene_recipes[i]);
            pipeline_register_shader(expected.vertex,[lib newFunctionWithName:@"v"]);
            pipeline_register_shader(expected.fragment,[lib newFunctionWithName:@"f"]);
            MTLRenderPipelineDescriptor *d=descriptor(&expected);
            PipelineRecipe actual=describe(d,expected.vertex,expected.fragment);
            ++roundtrips;bad_roundtrips+=memcmp(&actual,&expected,sizeof actual)!=0;
        }
    }
    printf("pipeline descriptors: roundtrips=%u bad=%u\n",roundtrips,bad_roundtrips);
    if(bad_roundtrips)return 1;
    dispatch_queue_t queues[4];for(int i=0;i<4;++i)queues[i]=dispatch_queue_create("pipeline-test",DISPATCH_QUEUE_SERIAL);
    NSMutableArray *reference=[NSMutableArray new];
    for(size_t i=0;i<2;++i) {
        MTLRenderPipelineDescriptor *d=descriptor(&recipes[i]);PipelineRecipe r=describe(d,1,2);
        CHECK(!memcmp(&r,&recipes[i],sizeof r));
        id<MTLRenderPipelineState> p=[mtl newRenderPipelineStateWithDescriptor:d error:&error];CHECK(p);
        [reference addObject:held(p)];
    }
    if(argc==1) pipeline_prewarm(queues,1);
    for(int i=0;i<4;++i)dispatch_sync(queues[i],^{});
    CHECK(argc>1 || states.count==2);
    pipeline_loading_done();
    for(size_t i=0;i<2;++i) {
        MTLRenderPipelineDescriptor *d=descriptor(&recipes[i]);
        id<MTLRenderPipelineState> p=pipeline_cached(d,1,2,"test",&error);CHECK(p);
        CHECK([held(p) isEqualToData:reference[i]]);
        CHECK(pipeline_cached(d,1,2,"test",&error)==p);
    }
    // A background PSO build is not charged to the rendering thread.
    uint64_t before=frame_builds;
    dispatch_semaphore_t done=dispatch_semaphore_create(0);
    dispatch_async(queues[0],^{NSError *e=nil;CHECK(pipeline_build(descriptor(&recipes[0]),"background",&e));dispatch_semaphore_signal(done);});
    dispatch_semaphore_wait(done,DISPATCH_TIME_FOREVER);
    CHECK(frame_builds==before);
    dispatch_async(queues[0],^{
        pipeline_frame_scope(1);
        NSError *e=nil;CHECK(pipeline_build_device(mtl,descriptor(&recipes[0]),"presentation",&e));
        pipeline_frame_scope(0);
        dispatch_semaphore_signal(done);
    });
    dispatch_semaphore_wait(done,DISPATCH_TIME_FOREVER);
    CHECK(frame_builds==before+1);
    dispatch_async(queues[0],^{NSError *e=nil;CHECK(pipeline_build(descriptor(&recipes[0]),"background",&e));dispatch_semaphore_signal(done);});
    dispatch_semaphore_wait(done,DISPATCH_TIME_FOREVER);
    CHECK(frame_builds==before+1);
    printf("pipeline counters: presentation=%llu background=0\n",(unsigned long long)(frame_builds-before));
    before=frame_builds;
    // Diagnostics must leave counters unchanged when disabled, even for a known pass.
    diag_on=0;
    CHECK(pipeline_build_device(mtl,descriptor(&recipes[0]),"edr",&error));
    CHECK(frame_builds==before);
    diag_on=1;
    printf("pipeline cache: listed frame builds=%llu held=1024/1024 bytes failures=%d\n",(unsigned long long)known_builds,failures);
    return failures || known_builds ? 1 : 0;
}}
'''


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--diag':
        return diagnostic(Path(sys.argv[2]))
    source = (ROOT / 'pipeline_cache.m').read_text()
    source = source.replace('#include "pipeline_cache.h"', '').replace('#include "d3d9_internal.h"', '')
    catalogue = re.search(r'static const PipelineRecipe recipes\[\] = \{.*?\n\};', source, re.S)[0]
    catalogue = catalogue.replace('recipes[]', 'scene_recipes[]', 1)
    source = re.sub(r'(static const PipelineRecipe recipes\[\] = \{).*?(\n\};)',
                    lambda m: m[1] + FIXTURES + m[2], source, count=1, flags=re.S)
    with tempfile.TemporaryDirectory(prefix='pipeline-cache-test-') as td:
        td = Path(td)
        src = td / 'test.m'
        exe = td / 'test'
        def build(production):
            src.write_text(PRELUDE + production + catalogue + MAIN)
            p = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                                '-fobjc-arc', '-I', str(ROOT), str(src), '-framework', 'Foundation',
                                '-framework', 'Metal', '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-4000:]
        build(source)
        for args, expected in [([], 0), (['--skip-prewarm'], 1)]:
            p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60)
            print(p.stdout.strip())
            if p.returncode == 3:
                return 3
            assert p.returncode == expected, p.stderr
            assert f'listed frame builds={2 if args else 0}' in p.stdout
        broken = source.replace('d.stencilAttachmentPixelFormat=r->stencil;', 'd.stencilAttachmentPixelFormat=MTLPixelFormatInvalid;')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        print('stencil omitted control:', p.stdout.strip())
        bad_count = 4 * len(re.findall(r'\.stencil=(?!0[,}])\d+', catalogue))
        assert bad_count and p.returncode == 1 and f'bad={bad_count}' in p.stdout, p.stdout + p.stderr
        broken = source.replace('frame_scope || pthread_equal(frame_thread,pthread_self())',
                                'pthread_equal(frame_thread,pthread_self())')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        print('presentation omitted control:', p.stdout.strip())
        assert p.returncode == 1 and 'pipeline counters: presentation=0 background=0' in p.stdout, p.stdout + p.stderr
    return 0


if __name__ == '__main__':
    sys.exit(main())
