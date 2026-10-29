"""








"""
from pathlib import Path
import os
import hashlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
# Frozen production baseline, so the comparison remains meaningful after merging.
BASELINE_REV = 'c9e5b989bc190f9fbf6a886de14a509d4f6a5928'
LEGACY_GAMMA = r'''static const char *const gamma_src =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct VOut { float4 pos [[position]]; float2 uv; };\n"
"vertex VOut gamma_vs(uint vid [[vertex_id]]) { float2 p = float2((vid << 1) & 2, vid & 2); VOut o; o.pos = float4(p * float2(2, -2) + float2(-1, 1), 0, 1); o.uv = p; return o; }\n"
"fragment float4 gamma_fs(VOut i [[stage_in]], texture2d<float> s [[texture(0)]], constant float *lut [[buffer(0)]]) {\n"
"    constexpr sampler pt(filter::nearest, address::clamp_to_edge);\n"
"    float4 c = s.sample(pt, i.uv); uint3 n = uint3(clamp(c.rgb, 0.0, 1.0) * 255.0 + 0.5);\n"
"    return float4(lut[n.r], lut[256 + n.g], lut[512 + n.b], c.a);\n"
"}\n";
static void gamma_blit(void) {
    static id<MTLRenderPipelineState> pso;
    if (!pso) {
        NSError *err = nil;
        id<MTLLibrary> lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:gamma_src] options:nil error:&err];
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [lib newFunctionWithName:@"gamma_vs"];
        pd.fragmentFunction = [lib newFunctionWithName:@"gamma_fs"];
        pd.colorAttachments[0].pixelFormat = present_tex.pixelFormat;
        pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
        if (!pso) { fprintf(stderr, "boot: gamma ramp pipeline: %s\n", err.localizedDescription.UTF8String); gamma_active = 0; }
    }
    if (!pso) return;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = present_tex; rp.colorAttachments[0].loadAction = MTLLoadActionDontCare; rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on) gpt_render_auto(rp, "gamma");
    id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp];
    if (gpt_on) gpt_name(e);
    [e setRenderPipelineState:pso];
    [e setFragmentTexture:backbuffer_tex atIndex:0];
    [e setFragmentBytes:gamma_lut length:sizeof gamma_lut atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [e endEncoding];
}
'''
LEGACY_COPY = r'''static int present_copy(void) {
    static int brightness_started;
    if (!brightness_started) { brightness_started = 1; brightness_init(); }
    if (!layer || presenting || user32_window_occluded()) return 0;   // occluded: nothing would be shown
    if (!present_q) present_q = dispatch_queue_create("nfsmw.present", DISPATCH_QUEUE_SERIAL);
    present_graded = gamma_active && backbuffer_tex.width == present_tex.width && backbuffer_tex.height == present_tex.height;
    if (present_graded) gamma_blit();
    else {
        id<MTLBlitCommandEncoder> b = gpt_blit(cmd, "blit present copy");
        [b copyFromTexture:backbuffer_tex toTexture:present_tex];
        [b endEncoding];
    }
    presenting = 1;
    return 1;
}
'''
LEGACY_BLIT = r'''static const char *blit_src =
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "struct V { float4 pos [[position]]; float2 uv; };\n"
    "vertex V blit_vs(uint vid [[vertex_id]], constant float4 *r [[buffer(0)]]) {\n"
    "  float2 p = float2(vid & 1, vid >> 1); V o; o.pos = float4(mix(r[0].xy, r[0].zw, p), 0, 1); o.uv = mix(r[1].xy, r[1].zw, p); return o; }\n"
    "fragment float4 blit_fs(V i [[stage_in]], texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) { return t.sample(s, i.uv); }\n";

'''
LEGACY_QUAD = r'''void quad_into(id<MTLCommandBuffer> cb, id<MTLTexture> src, MTLRegion sr, id<MTLTexture> dst, NSUInteger level, NSUInteger slice, MTLRegion dr, int linear) {
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = dst;
    rp.colorAttachments[0].level = level;
    rp.colorAttachments[0].slice = slice;
    rp.colorAttachments[0].loadAction = MTLLoadActionLoad;
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    if (gpt_on && cb == cmd) gpt_render_auto(rp, "quad");
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
    if (gpt_on && cb == cmd) gpt_name(e);
    if(!float_scene_on && dst.pixelFormat!=MTLPixelFormatBGRA8Unorm){[e endEncoding];return;}
    id<MTLRenderPipelineState> pso = blit_pso;
    if (dst.pixelFormat!=MTLPixelFormatBGRA8Unorm || dst.sampleCount!=1) {
        uint64_t key=((uint64_t)dst.pixelFormat<<32)|dst.sampleCount;
        pso=(__bridge id<MTLRenderPipelineState>)map_get(&blit_pipelines,key);
        if (!pso) {
            MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
            pd.vertexFunction = [blit_lib newFunctionWithName:@"blit_vs"];
            pd.fragmentFunction = [blit_lib newFunctionWithName:@"blit_fs"];
            pd.colorAttachments[0].pixelFormat = dst.pixelFormat;
            pd.rasterSampleCount = dst.sampleCount;
            pso = [mtl newRenderPipelineStateWithDescriptor:pd error:nil];
            if(pso)map_put(&blit_pipelines,key,pso);
        }
    }
    if(!pso){[e endEncoding];abort();}
    [e setRenderPipelineState:pso];
    float dw = (float)mipdim((uint32_t)dst.width, (uint32_t)level), dh = (float)mipdim((uint32_t)dst.height, (uint32_t)level);
    float r[8] = {
        dr.origin.x / dw * 2 - 1, 1 - dr.origin.y / dh * 2, (dr.origin.x + dr.size.width) / dw * 2 - 1, 1 - (dr.origin.y + dr.size.height) / dh * 2,
        sr.origin.x / (float)src.width, sr.origin.y / (float)src.height, (sr.origin.x + sr.size.width) / (float)src.width, (sr.origin.y + sr.size.height) / (float)src.height};
    [e setVertexBytes:r length:sizeof r atIndex:0];
    [e setFragmentTexture:src atIndex:0];
    [e setFragmentSamplerState:linear ? blit_linear : blit_point atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
    [e endEncoding];
}
'''
LEGACY_INIT = r'''    NSError *err = nil;
    id<MTLLibrary> lib = blit_lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:blit_src] options:nil error:&err];
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"blit_vs"];
    pd.fragmentFunction = [lib newFunctionWithName:@"blit_fs"];
    pd.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    blit_pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!blit_pso) fprintf(stderr, "boot: render: blit pipeline: %s\n", err.localizedDescription.UTF8String);
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterLinear;
    blit_linear = [mtl newSamplerStateWithDescriptor:sd];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;
    blit_point = [mtl newSamplerStateWithDescriptor:sd];
'''


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def legacy_names(source):
    for name in ('gamma_src', 'gamma_blit', 'present_copy', 'blit_src', 'blit_pso',
                 'blit_lib', 'blit_pipelines', 'blit_linear', 'blit_point', 'quad_into'):
        source = source.replace(name, 'legacy_' + name)
    return source


def checked(args, **kwargs):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{args[0]} exit {result.returncode}\n{result.stdout[-1500:]}{result.stderr[-4000:]}')
    if result.stdout:
        print(result.stdout, end='', flush=True)
    if result.stderr:
        print(result.stderr, end='', flush=True)
    return result


def main():
    render = (ROOT / 'render.m').read_text()
    presenter = (ROOT / 'render_present.m').read_text()
    post = (ROOT / 'render_post.m').read_text()
    guard = (ROOT / 'hdrclip.m').read_text()
    legacy_post = section(post, 'static const char *x360_src =', '#include "lampglow_shader.h"')
    assert hashlib.sha256(legacy_post.encode()).hexdigest() == 'a7b0ddc42942bd19ebada259861bd6503c9253474d2a881130c1b3f0fe594146', 'legacy post shader changed'
    print('hdr_output_test: legacy post source 13018/13018 bytes frozen SHA-256 PASS', flush=True)
    quad = section(render, 'void quad_into(id<MTLCommandBuffer> cb,', 'void render_stretch(')
    blit = section(render, 'static const char *blit_src =', '// Startup-only hidden EDR')
    maps = section(render, 'typedef struct { uint64_t key; void *obj; } MapSlot;', '// ---------------- GPU-side objects')
    init = section(render[render.index('void render_init(void)'):], '    NSError *err = nil;', '    render_resize();')
    production = section(presenter, 'void render_set_gamma(', 'static void present_show(')
    legacy = ('static id<MTLLibrary> legacy_blit_lib;\n'
              'static id<MTLRenderPipelineState> legacy_blit_pso;\n'
              'static id<MTLSamplerState> legacy_blit_linear,legacy_blit_point;\n'
              'static Map legacy_blit_pipelines;\n' +
              legacy_names(LEGACY_BLIT + LEGACY_GAMMA + LEGACY_COPY + LEGACY_QUAD))
    env = dict(os.environ)
    env.pop('NFS_HDR_OUTPUT', None)
    env.pop('NFS_TEST_GAMMA', None)
    with tempfile.TemporaryDirectory(prefix='hdr-output-test-') as scratch:
        scratch = Path(scratch)
        (scratch / 'presenter.inc').write_text(production)
        post_code = (section(post, 'static const char *x360_src =', '#include "lampglow_shader.h"') +
                     section(post, 'static NSString *x_edr_source(', '// END HDR output M post variant') +
                     section(post, 'typedef struct { float off[16][4]', '// The visual treatment into a multisampled back buffer'))
        (scratch / 'post.inc').write_text(post_code)
        (scratch / 'quad.inc').write_text(maps + '\nstatic Map blit_pipelines;\n' + blit + quad)
        (scratch / 'routing.inc').write_text(
            section(render, 'static MTLTextureDescriptor *texture_descriptor(', 'RenderTex *gpu_tex(') +
            section(render, 'void render_float_scene_begin(void) {', '// Only actual pre-post screen-copy destinations'))
        (scratch / 'guard.inc').write_text(
            section(guard, 'enum { HC_FAMILIES=', 'static const char *hc_names') +
            section(guard, 'static NSString *hc_msl(', 'int hdrclip_test(') +
            section(guard, 'static NSString *hc_code(', 'static unsigned hc_select('))
        (scratch / 'legacy.inc').write_text(legacy)
        harness = (ROOT / 'hdr_output_test.m').read_text().replace('INIT_BLIT',
            '{' + init + '}\n{' + legacy_names(LEGACY_INIT) + '}')
        (scratch / 'test.m').write_text(harness)
        exe = scratch / 'test'
        build = ['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                 '-Wno-unused-variable', '-fobjc-arc', '-I', scratch, '-I', ROOT, scratch / 'test.m',
                 '-framework', 'Cocoa', '-framework', 'QuartzCore', '-framework', 'Metal', '-o', exe]
        checked(build)
        checked([exe, '--cpu'], env=env)
        broken_init = production.replace('if (!hdr_output_requested || !layer) return;',
            'if (!hdr_output_requested || !layer) { hdr_output_on = 1; return; }')
        assert broken_init != production
        (scratch / 'presenter.inc').write_text(broken_init)
        checked(build)
        bad_cpu = subprocess.run([str(exe), '--cpu'], capture_output=True, text=True, env=env)
        assert bad_cpu.returncode == 1 and 'FAIL: unset switch changed mode' in bad_cpu.stderr, bad_cpu.stdout + bad_cpu.stderr
        print('hdr_output_test: CPU broken unset-mode control rejected (exit 1) PASS', flush=True)
        broken_power = production.replace('float y=gamma_lut[channel*256+128];', 'float y=(128.0f/255)+(float)channel*0;')
        assert broken_power != production
        (scratch / 'presenter.inc').write_text(broken_power)
        checked(build)
        bad_power = subprocess.run([str(exe), '--cpu'], capture_output=True, text=True, env=env)
        assert bad_power.returncode == 1 and 'FAIL: continuous brightness exponent' in bad_power.stderr, bad_power.stdout + bad_power.stderr
        print('hdr_output_test: CPU broken constant-power control rejected (exit 1) PASS', flush=True)
        (scratch / 'presenter.inc').write_text(production)
        checked(build)
        checked([exe, '--sources', scratch], env=env)
        tool_env = {**env, 'TMPDIR': env.get('OT_TOOL_TMPDIR', env.get('TMPDIR', '/tmp/'))}
        metal = checked(['xcrun', '--find', 'metal'], env=tool_env).stdout.strip()
        for shader in ('hdr', 'gamma', 'blit', 'post', 'post_edr', 'hud'):
            checked([metal, '-fmodules-cache-path=' + str(scratch / 'modules'), '-c',
                     scratch / (shader + '.metal'), '-o', scratch / (shader + '.air')],
                    env={**env, 'TMPDIR': str(scratch) + '/'})
        print('hdr_output_test: offline Metal compile: HDR/gamma/quad/post/post_edr/HUD 6/6 PASS', flush=True)
        # Never treat sandbox device denial as GPU evidence.
        result = subprocess.run([str(exe), '--sdr'], capture_output=True, text=True,
                                env={**env, 'MTL_DEBUG_LAYER': '1'})
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode == 3:
            return 3
        assert result.returncode == 0, 'SDR baseline failed'
        edr_env = {**env, 'NFS_HDR_OUTPUT': 'edr', 'MTL_DEBUG_LAYER': '1'}
        checked([exe, '--fallback'], env=edr_env)
        checked([exe, '--edr'], env=edr_env)
        broken = subprocess.run([str(exe), '--broken'], capture_output=True, text=True, env=edr_env)
        print(broken.stdout + broken.stderr, end='', flush=True)
        assert broken.returncode == 1 and 'FAIL: PATCH above_one=0 lost=2 bad_channels=6' in broken.stderr, 'BGRA8 mutant escaped or failed for another reason'
        checked([exe, '--composition'], env=edr_env)
        checked([exe, '--composition-msaa'], env=edr_env)
        bad_fallback = production.replace('} else if (present_graded) gamma_blit();',
                                         '} else if (present_graded && !hdr_output_on) gamma_blit();')
        assert bad_fallback != production
        (scratch / 'presenter.inc').write_text(bad_fallback)
        checked(build)
        fallback = subprocess.run([str(exe), '--fallback'], capture_output=True, text=True, env=edr_env)
        print(fallback.stdout + fallback.stderr, end='', flush=True)
        assert fallback.returncode == 1 and 'FAIL: SDR baseline bytes differ grade=1 mismatches=2016' in fallback.stderr, 'H=1 missing gamma control escaped'
        (scratch / 'presenter.inc').write_text(production)
        good_guard = (scratch / 'guard.inc').read_text()
        bad_guard = good_guard.replace('policy==2u?1.0f:65504.0f', '65504.0f')
        assert bad_guard != good_guard
        (scratch / 'guard.inc').write_text(bad_guard)
        checked(build)
        guard_result = subprocess.run([str(exe), '--composition'], capture_output=True, text=True, env=edr_env)
        print(guard_result.stdout + guard_result.stderr, end='', flush=True)
        assert guard_result.returncode == 1 and 'FAIL: HUD paper white 4.00000000' in guard_result.stderr, 'unbounded HUD source control escaped'
        (scratch / 'guard.inc').write_text(good_guard)
        bad_hud = production.replace('c.rgb=hdr_decode(min(pow(c.rgb,tone.xyz),float3(65504)));',
                                     'c.rgb=hdr_decode(min(pow(c.rgb,tone.xyz),float3(65504)))*tone.w;')
        assert bad_hud != production
        (scratch / 'presenter.inc').write_text(bad_hud)
        checked(build)
        hud = subprocess.run([str(exe), '--composition'], capture_output=True, text=True, env=edr_env)
        print(hud.stdout + hud.stderr, end='', flush=True)
        assert hud.returncode == 1 and 'FAIL: HUD paper white 4.00000000' in hud.stderr, 'HUD scaling control escaped'
        print(f'hdr_output_test: PASS normal=0 broken=1; SDR baseline={BASELINE_REV[:12]}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
