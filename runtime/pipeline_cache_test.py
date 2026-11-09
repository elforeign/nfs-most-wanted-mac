#!/usr/bin/env python3
"""Execute the production descriptor cache on Metal; automatically in offline_tests.

Unset/1: listed frame builds 0; explicit 0 or skipped prewarm: 2 (exit 1).
Old unset-Off parser: 2 listed builds and a failed enabled check (exit 1).
Race/free-roam baseline diagnostic control: listed builds 80/86 (exit 1).
Two held GPU renders compare 1024/1024 bytes for replay versus direct creation.
Every scene recipe round-trips at 1x/2x/4x/8x AA: 812 cases, zero differences;
omitting stencil rejects 652/812 cases (exit 1).
Embedded identity: library/function/MRT/write-mask 4/4; omitted secondary
attachments fail 2 checks (exit 1). Source counter: frame=1, background/off=0.
Presentation scope: one counted build; ignoring it counts zero (exit 1).
Catalogue: 203/203; removing one new recipe: 202/203 (exit 1).
Frame join: normal 0, deliberately joined 1. In-flight demand never waits;
128 concurrent lookups / 256 shader publications share one cache mutex;
overwriting the first publication fails one overlap assertion (exit 1).
AUX resource preload: 3/3 effect groups; omitting worldreflect: 2/3 (exit 1).
--diag DIR fails on any listed descriptor built on the frame path.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


# Descriptor-only reference: the original catalogue plus the recorded AA-off
# race/pursuit and fresh-profile free-roam descriptors. Pin only this fragment.
def catalogue_check(source):
    table = source.split('static const PipelineRecipe recipes[] = {', 1)[1].split('\n};', 1)[0]
    rows = [re.sub(r'\s+', '', line.split('//')[0]) for line in table.splitlines() if '.vertex=' in line]
    digest = hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()
    failures = int(len(rows) != 203 or digest != '9ce5c6022db5a8718230bfceb8801cb4d1a591c9e2bea61d857f12b274490641')
    print(f'pipeline catalogue: recipes={len(rows)}/203 failures={failures}')
    return failures


def diagnostic(path):
    if catalogue_check((ROOT / 'pipeline_cache.m').read_text()):
        return 1
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
    sync = [int(n) for n in re.findall(r'pipeline_library: frame_sync=(\d+)', text)]
    assert sync, 'missing post source-compile diagnostics'
    source_compiles = max(len(re.findall(r'pipeline_library_sync:', text)), *sync)
    print(f'pipeline check: listed builds={total} load failures={failures} source compiles={source_compiles}')
    return int(total != 0 or failures != 0 or source_compiles != 0)


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
static dispatch_semaphore_t build_entered, build_release;
'''
MAIN = r'''
static int failures;
#define CHECK(x) do {if(!(x)){__atomic_add_fetch(&failures,1,__ATOMIC_RELAXED);fprintf(stderr,"failed: %s\n",#x);}}while(0)
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
    int skip=argc>1 && !strcmp(argv[1],"--skip-prewarm");
    int disabled=argc>1 && !strcmp(argv[1],"--prewarm-off");
    if(disabled)setenv("NFS_PSO_PREWARM","0",1);
    else if(argc>1 && !strcmp(argv[1],"--prewarm-on"))setenv("NFS_PSO_PREWARM","1",1);
    else unsetenv("NFS_PSO_PREWARM");
    CHECK(enabled()==!disabled);
    NSError *error=nil;
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;struct A{float2 p [[attribute(0)]];};vertex float4 v(A a [[stage_in]]){return float4(a.p,0,1);}fragment float4 f(){return float4(.125,.25,.5,1);}fragment float4 f2(){return float4(.5,.25,.125,1);}";
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
    if(!skip) pipeline_prewarm(queues,1);
    for(int i=0;i<4;++i)dispatch_sync(queues[i],^{});
    CHECK(states.count==((skip || disabled)?0:2));
    // Embedded source variants and secondary attachments must not alias.
    MTLRenderPipelineDescriptor *embedded=descriptor(&recipes[0]);
    id<MTLRenderPipelineState> first=pipeline_cached_library(embedded,lib,"embedded",&error);CHECK(first);
    CHECK(pipeline_cached_library(embedded,lib,"embedded",&error)==first);
    embedded.fragmentFunction=[lib newFunctionWithName:@"f2"];
    id<MTLRenderPipelineState> second=pipeline_cached_library(embedded,lib,"embedded",&error);CHECK(second && second!=first);
    CHECK(![held(first) isEqualToData:held(second)]);
    id<MTLLibrary> other=[mtl newLibraryWithSource:src options:nil error:&error];CHECK(other);
    embedded.vertexFunction=[other newFunctionWithName:@"v"];embedded.fragmentFunction=[other newFunctionWithName:@"f"];
    id<MTLRenderPipelineState> third=pipeline_cached_library(embedded,other,"embedded",&error);CHECK(third && third!=first);
    embedded.colorAttachments[1].pixelFormat=MTLPixelFormatRGBA8Unorm;
    id<MTLRenderPipelineState> mrt=pipeline_cached_library(embedded,other,"embedded",&error);CHECK(mrt && mrt!=third);
    embedded.colorAttachments[1].writeMask=MTLColorWriteMaskNone;
    id<MTLRenderPipelineState> masked=pipeline_cached_library(embedded,other,"embedded",&error);CHECK(masked && masked!=mrt);
    puts("pipeline embedded identity: library/function/MRT/write-mask 4/4");
    pipeline_loading_done();
    for(size_t i=0;i<2;++i) {
        MTLRenderPipelineDescriptor *d=descriptor(&recipes[i]);
        id<MTLRenderPipelineState> p=pipeline_cached(d,1,2,"test",&error);CHECK(p);
        CHECK([held(p) isEqualToData:reference[i]]);
        CHECK(pipeline_cached(d,1,2,"test",&error)==p);
    }
    // Hold a background build in flight. A frame lookup must build immediately,
    // without waiting for the worker thread or holding the cache mutex across Metal.
    build_entered=dispatch_semaphore_create(0);build_release=dispatch_semaphore_create(0);
    MTLRenderPipelineDescriptor *overlap=descriptor(&recipes[0]);
    overlap.colorAttachments[0].writeMask=3;
    __block id<MTLRenderPipelineState> background;
    dispatch_async(queues[0],^{ @autoreleasepool {
        background=pipeline_cached(overlap,1,2,"blocked",NULL);
    }});
    dispatch_semaphore_wait(build_entered,DISPATCH_TIME_FOREVER);
    uint64_t demand_start=host_ticks();
    id<MTLRenderPipelineState> demand=pipeline_cached(overlap,1,2,"demand",&error);
    uint64_t demand_ns=host_ticks()-demand_start;
    CHECK(demand);
    dispatch_semaphore_signal(build_release);
    dispatch_sync(queues[0],^{});
    CHECK(background==demand);
    printf("pipeline overlap: demand_ms=%.3f waited_for_worker=0 shared_publication=%d\n",demand_ns/1e6,background==demand);
    dispatch_apply(128,dispatch_get_global_queue(QOS_CLASS_DEFAULT,0),^(size_t i) { @autoreleasepool {
        pipeline_register_shader(1,[lib newFunctionWithName:@"v"]);
        pipeline_register_shader(2,[lib newFunctionWithName:@"f"]);
        MTLRenderPipelineDescriptor *d=descriptor(&recipes[i%2]);
        CHECK(pipeline_cached(d,1,2,"concurrent",NULL));
    }});
    puts("pipeline concurrent readers/writers: 128 lookups and 256 shader publications");
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
    CHECK(pipeline_library_source(src,"test",&error));
    CHECK(frame_library_compiles==1);
    dispatch_async(queues[0],^{CHECK(pipeline_library_source(src,"background",NULL));dispatch_semaphore_signal(done);});
    dispatch_semaphore_wait(done,DISPATCH_TIME_FOREVER);
    CHECK(frame_library_compiles==1);
    before=frame_builds;
    // Diagnostics must leave counters unchanged when disabled, even for a known pass.
    diag_on=0;
    CHECK(pipeline_build_device(mtl,descriptor(&recipes[0]),"edr",&error));
    CHECK(frame_builds==before);
    CHECK(pipeline_library_source(src,"off",&error));CHECK(frame_library_compiles==1);
    diag_on=1;
    printf("pipeline cache: listed frame builds=%llu held=1024/1024 bytes failures=%d\n",(unsigned long long)known_builds,failures);
    return failures || known_builds ? 1 : 0;
}}
'''


# Execute the actual resource selector without private shader data or Metal.
# The reflective-world effect must prepare AUX just like the two world effects.
def aux_preload_check(render, td):
    function = 'static void shader_preload_blob' + render.split('static void shader_preload_blob', 1)[1].split('static void shader_catalogue_warm(void) {', 1)[0]
    prelude = r"""
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
typedef struct { uint16_t shadow_samplers; } SmOptions;
typedef struct { int shadow; } ShaderShape;
typedef struct { uint32_t *tokens, ntokens; uint64_t tokens_hash; } Obj;
enum { MAT_VARIANT_AUX=512, MAT_VARIANT_WATER, MAT_VARIANT_ROAD_FILM,
       MAT_VARIANT_REAL_GLASS, MAT_VARIANT_REAL_CHROME, MAT_VARIANT_CARBON_LAMP,
       MAT_VARIANT_HEADLAMP_CLEAR, MAT_VARIANT_GLASS, MAT_VARIANT_TREES,
       MAT_VARIANT_BONE, MAT_VARIANT_SOFT_SMOKE, MAT_VARIANT_SOFT_SMOKE_RESOLVED,
       MAT_VARIANT_SOFT_SMOKE_LATE };
static int aux_count;
static size_t sm_token_count(const uint32_t *t, size_t n) { return n>=2?2:0; }
static uint64_t fnv(const void *p,size_t n,uint64_t seed) { return seed; }
static ShaderShape *shader_shape(const uint32_t *t,uint32_t n,uint64_t h) { static ShaderShape s;return &s; }
static void sm_default_options(SmOptions *o) { memset(o,0,sizeof *o); }
static void get_shader(Obj *s,const SmOptions *o) {}
static void shader_preload_variant(Obj *s,const SmOptions *o,uint32_t v) { aux_count+=v==MAT_VARIANT_AUX; }
static int roadfilm_shader_hash(uint64_t h) { return 0; }
static int roadfilm_enabled(void) { return 0; }
static int realworld_materials_mode(void) { return 0; }
"""
    main = r"""
int main(void) {
    uint32_t blob[]={0xFEFF0901u,0,0xFFFE0200u,0xFFFFu};
    const char *names[]={"IDI_WORLD_FX","IDI_WORLDNORMALMAP_FX","IDI_WORLDREFLECT_FX","IDI_CAR_FX"};
    int failures=0, total=0;
    for (int i=0;i<4;++i) {
        aux_count=0;shader_preload_blob(blob,4,names[i]);
        total+=aux_count;failures+=aux_count!=(i<3);
    }
    printf("AUX resource coverage: prepared=%d/3 failures=%d\n",total,failures);
    return failures?1:0;
}
"""
    for label, contents, expected, count in [
        ('normal', function, 0, 3),
        ('worldreflect omitted', function.replace('int aux = world || !strcmp(name, "IDI_WORLDREFLECT_FX");', 'int aux = world;'), 1, 2),
    ]:
        path = td / 'aux-selector.c'
        exe = td / 'aux-selector'
        path.write_text(prelude + contents + main)
        p = subprocess.run(['clang', '-O1', '-Werror', '-Wno-unused-parameter', str(path), '-o', str(exe)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-4000:]
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(label + ': ' + p.stdout.strip())
        assert p.returncode == expected and f'prepared={count}/3' in p.stdout, p.stdout + p.stderr


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--diag':
        return diagnostic(Path(sys.argv[2]))
    source = (ROOT / 'pipeline_cache.m').read_text()
    assert catalogue_check(source) == 0
    # Removing a newly added recipe must fail independently of GPU support.
    new_row = source.split("// Scene-AA, reflection and AO variants", 1)[1].split("\n};", 1)[0].splitlines()[-1]
    missing = source.replace(new_row + "\n", "", 1)
    assert missing != source and catalogue_check(missing) == 1
    render = (ROOT / 'render.m').read_text()
    def post_prewarm_joins(text):
        tail = text.split('    pipeline_prewarm(shader_workers,', 1)[1].split('    pipeline_loading_done();', 1)[0]
        return tail.count('dispatch_sync(')
    assert post_prewarm_joins(render) == 0
    joined = render.replace('    pipeline_loading_done();', '    dispatch_sync(shader_workers[0], ^{});\n    pipeline_loading_done();', 1)
    assert post_prewarm_joins(joined) == 1
    print('pipeline frame barrier: normal=0 joined control=1')
    source = source.replace('#include "pipeline_cache.h"', '').replace('#include "d3d9_internal.h"', '')
    source = source.replace('    int measure=on_frame_path();',
        '    if (!strcmp(pass,"blocked")) { dispatch_semaphore_signal(build_entered); dispatch_semaphore_wait(build_release,DISPATCH_TIME_FOREVER); }\n    int measure=on_frame_path();')
    catalogue = re.search(r'static const PipelineRecipe recipes\[\] = \{.*?\n\};', source, re.S)[0]
    catalogue = catalogue.replace('recipes[]', 'scene_recipes[]', 1)
    source = re.sub(r'(static const PipelineRecipe recipes\[\] = \{).*?(\n\};)',
                    lambda m: m[1] + FIXTURES + m[2], source, count=1, flags=re.S)
    with tempfile.TemporaryDirectory(prefix='pipeline-cache-test-') as td:
        td = Path(td)
        aux_preload_check(render, td)
        src = td / 'test.m'
        exe = td / 'test'
        def build(production):
            src.write_text(PRELUDE + production + catalogue + MAIN)
            p = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                                '-fobjc-arc', '-I', str(ROOT), str(src), '-framework', 'Foundation',
                                '-framework', 'Metal', '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-4000:]
        build(source)
        for args, expected in [([], 0), (['--prewarm-on'], 0), (['--prewarm-off'], 1), (['--skip-prewarm'], 1)]:
            p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60)
            print(p.stdout.strip())
            if p.returncode == 3:
                return 3
            assert p.returncode == expected, p.stderr
            assert f'listed frame builds={2 if expected else 0}' in p.stdout
        broken = source.replace('return !s || atoi(s);', 'return s && atoi(s);')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        print('old unset-Off control:', p.stdout.strip())
        assert p.returncode == 1 and 'enabled()==!disabled' in p.stderr and 'listed frame builds=2' in p.stdout, p.stdout + p.stderr
        broken = source.replace('if (published) p=published; else states[key]=p;', '(void)published; states[key]=p;')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        print('overwrite publication control:', p.stdout.strip())
        assert p.returncode == 1 and 'background==demand' in p.stderr, p.stdout + p.stderr
        broken = source.replace('d.stencilAttachmentPixelFormat=r->stencil;', 'd.stencilAttachmentPixelFormat=MTLPixelFormatInvalid;')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        print('stencil omitted control:', p.stdout.strip())
        bad_count = 4 * len(re.findall(r'\.stencil=(?!0[,}])\d+', catalogue))
        assert bad_count and p.returncode == 1 and f'bad={bad_count}' in p.stdout, p.stdout + p.stderr
        broken = source.replace('for (int i=1;i<8;++i)', 'for (int i=1;i<1;++i)')
        assert broken != source
        build(broken)
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        assert p.returncode == 1 and 'mrt && mrt!=third' in p.stderr and 'masked && masked!=mrt' in p.stderr, p.stdout + p.stderr
        print('secondary attachments omitted: 2 identity failures (exit 1)')
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
