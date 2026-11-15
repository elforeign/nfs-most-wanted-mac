"""Authored water (NFS_WATER=port) production proof, bare (no console or PC assets).

The production patch and wave kernel (runtime/water_port.m) on translator-shaped world shaders, a checker reflection,
a 16 m plane, sun off: the generated maps displace 15,874/16,384 pixels from the flat-normal control (>0); suppressed
generation (all band amplitudes 0) displaces 0 and fails, and so does disabling the shader's map read (0). Sun glint
changes 4,410 pixels (0 with the glint removed); a plane 5 m above sea level takes the harbour sea state, 15,846
pixels (0 with the height rule removed). Also: generator numbers (significant heights 0.0465 / 0.3582 m, F0 0.020373),
exact 128 m tiling, animation in simulation time, shaders without the anchors keep PC (NULL), the menu row's
availability without console files and the provider decided without game state.

Default provider (production water_provider_get compiled with a stubbed roots_x360_has): no console files + switch unset
-> port; console files + unset -> console; =pc -> console path (PC water); =port -> port. The old default (unset ->
console) returns console for the first case and fails it; the production rule passes all of them.
"""
import array
import json
import math
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
GEN = ROOT / 'tools_snapshot/port_water_gen.py'

# Translator-shaped fixtures (host/shaders sm_msl.cpp layout): a full-screen plane, local xy = 8 m x corner, z = 0.
VERTEX = '''#include <metal_stdlib>
using namespace metal;
struct SmVSIn  { float4 v[16]; };
struct SmVSOut { float4 pos; float4 col[2]; float4 tex[8]; float4 fog; float4 psize; };
static void sm_body(thread const SmVSIn &si, thread SmVSOut &so) {
    float4 r0 = float4(1.0f), r1 = float4(1.0f);
    so.pos = float4(si.v[0].xy / 8.0f, 0.5f, 1.0f);
    // mul oD0.xyz, r1, r0
    { float4 d = r1 * r0; so.col[0].xyz = d.xyz; }
}
struct SmVSStageOut {
    float4 pos [[position]];
    float4 texcoord2 [[user(texcoord2)]];
};
vertex SmVSStageOut sm_main(uint id [[vertex_id]]) {
    float2 corners[3] = {float2(-1, -1), float2(3, -1), float2(-1, 3)};
    SmVSIn si; si.v[0] = float4(corners[id] * 8.0f, 0.0f, 1.0f);
    SmVSOut so; sm_body(si, so);
    SmVSStageOut r;
    r.pos = so.pos;
    r.texcoord2 = float4(0.0f);
    return r;
}
'''
PIXEL = '''#include <metal_stdlib>
using namespace metal;
struct SmPSIn { float4 tex[8]; };
struct SmPSOut { float4 oc[4]; };
static void sm_body(thread const SmPSIn &si, thread SmPSOut &so, constant float4 *c) {
    // add r0.xyz, r0, t2
    so.oc[0] = float4(0.2f, 0.3f, 0.4f, 1.0f) + float4(si.tex[2].xyz, 0.0f);
}

struct SmPSStageIn {
    float4 pos [[position]];
    float4 texcoord2 [[user(texcoord2)]];
};
struct SmPSStageOut { float4 c0 [[color(0)]]; };
fragment SmPSStageOut sm_main(SmPSStageIn pi [[stage_in]],
    constant float4 *c [[buffer(0)]]) {
    SmPSIn si;
    si.tex[2] = pi.texcoord2;
    SmPSOut so;
    sm_body(si, so, c);
    SmPSStageOut r; r.c0 = so.oc[0];
    return r;
}
'''

HARNESS = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "water_port.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static id<MTLDevice> mtl;
static id<MTLRenderPipelineState> pipeline(const char *vs, const char *ps) {
    NSError *e = nil;
    id<MTLLibrary> v = [mtl newLibraryWithSource:@(vs) options:nil error:&e];
    if (!v) { fprintf(stderr, "VS %s\n", e.description.UTF8String); exit(11); }
    id<MTLLibrary> p = [mtl newLibraryWithSource:@(ps) options:nil error:&e];
    if (!p) { fprintf(stderr, "PS %s\n", e.description.UTF8String); exit(12); }
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = [v newFunctionWithName:@"sm_main"]; d.fragmentFunction = [p newFunctionWithName:@"sm_main"];
    d.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> r = [mtl newRenderPipelineStateWithDescriptor:d error:&e];
    if (!r) { fprintf(stderr, "pipeline %s\n", e.description.UTF8String); exit(13); }
    return r;
}
// The production kernel renders the four wave-map slices for time t, then mips (as render.m water_port_waves).
static id<MTLTexture> waves_at(float t) {
    NSError *e = nil;
    id<MTLLibrary> lib = [mtl newLibraryWithSource:@(water_port_kernel()) options:nil error:&e];
    if (!lib) { fprintf(stderr, "kernel %s\n", e.description.UTF8String); exit(16); }
    id<MTLComputePipelineState> cp = [mtl newComputePipelineStateWithFunction:[lib newFunctionWithName:@"port_water_waves"] error:&e];
    if (!cp) exit(17);
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:256 height:256 mipmapped:YES];
    d.textureType = MTLTextureType2DArray; d.arrayLength = 4; d.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    d.storageMode = MTLStorageModePrivate;
    id<MTLTexture> tex = [mtl newTextureWithDescriptor:d];
    id<MTLCommandBuffer> cb = [[mtl newCommandQueue] commandBuffer];
    id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:cp]; [ce setTexture:tex atIndex:0]; [ce setBytes:&t length:sizeof t atIndex:0];
    [ce dispatchThreads:MTLSizeMake(256, 256, 4) threadsPerThreadgroup:MTLSizeMake(16, 16, 1)]; [ce endEncoding];
    id<MTLBlitCommandEncoder> be = [cb blitCommandEncoder]; [be generateMipmapsForTexture:tex]; [be endEncoding];
    [cb commit]; [cb waitUntilCompleted];
    return tex;
}
static void draw(id<MTLRenderPipelineState> p, id<MTLTexture> target, id<MTLTexture> refl, float t, float height, float sunlight, float *pixels) {
    float k[32][4] = {{0}}, world[4][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}};
    k[15][0] = k[16][1] = k[17][2] = 1; k[17][3] = height; k[19][2] = 6; k[19][3] = 1;   // eye 6 m above the plane centre
    k[18][1] = -0.8f; k[18][2] = 0.6f;                       // sun low ahead: its mirror image lies on the plane
    float sun[4] = {sunlight, sunlight, sunlight, 1};
    water_port_constants(k, sun, t);
    id<MTLCommandQueue> q = [mtl newCommandQueue];
    id<MTLCommandBuffer> cb = [q commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target; pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:pass];
    [e setRenderPipelineState:p];
    float c[4] = {0};
    [e setFragmentBytes:c length:sizeof c atIndex:0]; [e setFragmentBytes:k length:sizeof k atIndex:5];
    [e setFragmentTexture:refl atIndex:14]; [e setFragmentTexture:waves_at(t) atIndex:15];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [e endEncoding];
    [cb commit]; [cb waitUntilCompleted]; if (cb.status == MTLCommandBufferStatusError) exit(14);
    [target getBytes:pixels bytesPerRow:128 * 16 fromRegion:MTLRegionMake2D(0, 0, 128, 128) mipmapLevel:0];
}
static unsigned differ(const float *a, const float *b) {
    unsigned n = 0;
    for (int i = 0; i < 128 * 128; ++i)
        if (fabsf(a[4*i] - b[4*i]) > 1e-4f || fabsf(a[4*i+1] - b[4*i+1]) > 1e-4f || fabsf(a[4*i+2] - b[4*i+2]) > 1e-4f) ++n;
    return n;
}
int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2) return 2;
    const char *vs = VERTEX, *ps = PIXEL;
    char *pv = water_port_shader(vs, 1), *pp = water_port_shader(ps, 0);
    if (!pv || !pp) return 19;
    // A shader without the anchors (old translation, or another effect) keeps the PC draw.
    char *bare = strdup(ps); *strstr(bare, "r0, t2\n") = 'q';
    if (water_port_shader(bare, 0) || water_port_shader(pv, 1)) return 20;
    float k[32][4] = {{0}}, sun[4] = {0.5f, 0.6f, 0.7f, 1};
    water_port_constants(k, sun, 2.5f);
    if (k[21][0] != 2.5f || k[22][2] != 0.7f) return 21;
    if (strcmp(argv[1], "gpu")) { puts("patch anchors ok"); return 0; }
    mtl = MTLCreateSystemDefaultDevice(); if (!mtl) { puts("SKIP: no Metal device"); return 3; }
    id<MTLRenderPipelineState> waves = pipeline(pv, pp);
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:128 height:128 mipmapped:NO];
    d.storageMode = MTLStorageModeShared; d.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> target = [mtl newTextureWithDescriptor:d];
    d.width = d.height = 64; d.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> refl = [mtl newTextureWithDescriptor:d];
    static float checker[64 * 64 * 4];
    for (int y = 0; y < 64; ++y) for (int x = 0; x < 64; ++x) {
        float v = ((x >> 2) + (y >> 2)) & 1 ? 1.0f : 0.0f; float *t = checker + 4 * (y * 64 + x);
        t[0] = t[1] = t[2] = v; t[3] = 1; }
    [refl replaceRegion:MTLRegionMake2D(0, 0, 64, 64) mipmapLevel:0 withBytes:checker bytesPerRow:64 * 16];
    static float a[128 * 128 * 4], c[128 * 128 * 4], dark[128 * 128 * 4], raised[128 * 128 * 4];
    draw(waves, target, refl, 0.0f, 0.0f, 1.0f, a); draw(waves, target, refl, 1.0f, 0.0f, 1.0f, c);
    draw(waves, target, refl, 0.0f, 0.0f, 0.0f, dark); draw(waves, target, refl, 0.0f, 5.0f, 1.0f, raised);
    printf("glint_pixels=%u harbour_pixels=%u ", differ(a, dark), differ(a, raised));
    FILE *f = fopen(argv[2], "wb"); if (!f || fwrite(dark, sizeof dark, 1, f) != 1) return 15;   // sun off: the normals alone fclose(f);
    printf("animated_pixels=%u\n", differ(a, c));
    return 0;
}}
'''


def run(args, expected=0):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True)
    if p.returncode == 3:
        print(p.stdout.strip())
        raise SystemExit(3)
    assert p.returncode == expected, f'{args[0]}: {p.returncode}\n{p.stdout[-1500:]}\n{p.stderr[-4000:]}'
    return p.stdout.strip()


def displaced(a, b):
    """Pixels whose colour differs by more than 1e-4 in any channel (RGBA32F, 128 x 128)."""
    x, y = array.array('f', Path(a).read_bytes()), array.array('f', Path(b).read_bytes())
    return sum(any(abs(x[i + c] - y[i + c]) > 1e-4 for c in range(3)) for i in range(0, len(x), 4))


def provider_default(body):
    """Compile the production provider rule; 1 = console path, 2 = authored."""
    old = body.replace('roots_x360_has("shaders") ? 1 : 2', '1')
    assert old != body
    cases = (('', 0, 2), ('', 1, 1), ('pc', 0, 1), ('pc', 1, 1), ('port', 1, 2), ('x360', 0, 1))   # switch, console files, want
    with tempfile.TemporaryDirectory(prefix='water-default-') as d:
        results = {}
        for name, text in (('production', body), ('old default', old)):
            src = Path(d) / 'p.c'
            src.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nstatic int console;\n'
                           'static int roots_x360_has(const char *f) { (void)f; return console; }\nstatic int water_provider;\n'
                           'static int water_provider_get(void) {' + text + '\n}\n'
                           'int main(int c, char **v) { (void)c; console = atoi(v[1]); printf("%d", water_provider_get()); return 0; }\n')
            exe = Path(d) / 'p'
            run(['clang', '-w', src, '-o', exe])
            got = []
            for sw, files, _ in cases:
                env = {'PATH': '/usr/bin'} | ({'NFS_WATER': sw} if sw else {})
                got.append(int(subprocess.run([exe, str(files)], capture_output=True, text=True, env=env).stdout))
            results[name] = got
        want = [w for *_, w in cases]
        assert results['production'] == want, results
        assert results['old default'][0] != want[0], results   # the old default fails the first case
        print('default provider (unset/no files, unset/files, pc, pc/files, port, x360): production %s, old default %s'
              % (results['production'], results['old default']))


def main():
    gen = runpy.run_path(str(GEN))
    head = gen['header']()
    assert head == gen['header']() and '"0.020373"' in head
    for name, want in (('HARBOUR', 0.0465), ('COAST', 0.3582)):
        layers = gen['waves'](*gen['SCENARIOS'][name])
        hs = 4 * math.sqrt(sum(a * a / 2 for layer in layers for _, _, a, _ in layer))
        assert abs(hs - want) < 0.0005, (name, hs)
        error = max(abs(u - v) for x, y, t in ((1.3, 7.1, 0.4), (90.0, -3.0, 12.0))
                    for u, v in zip(gen['slope'](x, y, t, layers), gen['slope'](x + 128, y - 128, t, layers)))
        assert error < 1e-9, error
        assert gen['slope'](1.3, 7.1, 0.0, layers) != gen['slope'](1.3, 7.1, 0.5, layers)
    print('generator: Hs 0.0465/0.3582 m, F0 0.020373, 128 m tiling error < 1e-9, animated')
    settings = (RT / 'settings.c').read_text().split('static int x360_unavailable', 1)[0]
    assert 'graphics.water' not in settings.split('kNeedsX360[]', 1)[1].split('};', 1)[0]
    assert '"pc", "x360", "port"' in (RT / 'settings_port.c').read_text()
    material = (RT / 'materials.m').read_text()
    provider = material.split('static int water_provider_get(void) {', 1)[1].split('\n}', 1)[0]
    assert 'getenv("NFS_WATER")' in provider and 'water_this_frame' not in provider   # no frame state: fixed before warming
    provider_default(provider)
    with tempfile.TemporaryDirectory(prefix='water-port-') as directory:
        tmp = Path(directory)
        (tmp / 'gen').mkdir()

        def build(header, name):
            (tmp / 'gen/port_water.inc').write_text(header)
            (tmp / 'h.m').write_text(HARNESS.replace('VERTEX', json.dumps(VERTEX)).replace('PIXEL', json.dumps(PIXEL)))
            # A copy beside the test's gen/: an include resolves next to the including file first.
            (tmp / 'water_port.m').write_text((RT / 'water_port.m').read_text())
            run(['clang', '-O1', '-fobjc-arc', '-Wall', '-Wextra', '-I', tmp, '-I', RT, tmp / 'h.m', tmp / 'water_port.m',
                 '-framework', 'Foundation', '-framework', 'Metal', '-o', tmp / name])
            return tmp / name
        good = build(head, 'good')
        print(run([good, 'cpu']))
        animated = run([good, 'gpu', tmp / 'waves.raw'])
        # The flat-normal control and the broken run: suppressed generation, every band amplitude 0.
        zero = re.sub(r'float4\(([^,]+), ([^,]+), [^,]+, ', r'float4(\1, \2, 0.0f, ', head)
        assert zero != head
        flat = build(zero, 'suppressed')
        run([flat, 'gpu', tmp / 'flat.raw'])
        run([flat, 'gpu', tmp / 'flat2.raw'])
        fixed, broken = displaced(tmp / 'waves.raw', tmp / 'flat.raw'), displaced(tmp / 'flat2.raw', tmp / 'flat.raw')
        print(f'authored normals: displaced_pixels={fixed} of 16384, {animated}')
        print(f'suppressed generation: displaced_pixels={broken}')
        assert fixed > 0 and 'animated_pixels=0' not in animated, (fixed, animated)
        assert 'glint_pixels=0 ' not in animated and 'harbour_pixels=0 ' not in animated, animated
        assert broken == 0, broken

if __name__ == '__main__':
    main()
