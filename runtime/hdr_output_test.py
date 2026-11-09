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


# Frozen lazy source sites: default prewarm returned before doing any work.
POST_LAZY_EDR = ('d961d6fba8ab6afc753bad23d0476094de5e11b12575594cc4591c315a9faeea', r'''static id<MTLRenderPipelineState> x_edr_pipeline(NSString *fs, NSUInteger samples, int lamp) {
    static id<MTLLibrary> libraries[2];
    static NSMutableDictionary *states;
    if(!states)states=[NSMutableDictionary new];
    NSString *key=[NSString stringWithFormat:@"%@/%lu/%d",fs,(unsigned long)samples,lamp];
    id<MTLRenderPipelineState> p=states[key];if(p)return p;
    if(!libraries[lamp]) {
        NSString *src=[NSString stringWithUTF8String:x360_src];
        if(lamp) {
            src=[src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
            src=[src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin,uv).rgb;\n    if (patch) bl = 0;"];
        }
        NSError *error=nil;
        libraries[lamp]=[mtl newLibraryWithSource:hdrclip_shader(x_edr_source(src)) options:nil error:&error];
        if(!libraries[lamp]) { fprintf(stderr,"boot: EDR post: %s\n",error.localizedDescription.UTF8String);return nil; }
    }
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];
    d.vertexFunction=[libraries[lamp] newFunctionWithName:@"vs_full"];
    d.fragmentFunction=[libraries[lamp] newFunctionWithName:fs];
    d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
    if([fs isEqualToString:@"fs_downscale"])d.colorAttachments[1].pixelFormat=MTLPixelFormatRGBA16Float;
    d.rasterSampleCount=samples;
    p=pipeline_build(d, "edr", NULL);if(p)states[key]=p;
    return p;
}''')

POST_LAZY_WARM = ('f840c7276ff94301a427945e3e112215d272e93bbd33ca6b67851af332835139', r'''void render_post_prewarm(void) {
    const char *setting=getenv("NFS_PSO_PREWARM");
    if (!setting || !atoi(setting)) return;
    int failed=!x_pipeline_init(); // the EDR chain also uses the original post setup
    failed+=!x_contact_smoke_multiply(MTLPixelFormatBGRA8Unorm);
    failed+=!x_contact_smoke_multiply(MTLPixelFormatRGBA16Float);
    for (int lamp=0;lamp<2;++lamp) {
        for (NSString *fs in @[@"fs_downscale", @"fs_lum", @"fs_bright", @"fs_taps", @"fs_dofdown", @"fs_tdown"])
            failed+=!x_edr_pipeline(fs,1,lamp);
        for (NSUInteger samples=1;samples<=8;samples*=2)
            if ([mtl supportsTextureSampleCount:samples]) failed+=!x_edr_pipeline(@"fs_vt",samples,lamp);
    }
    if(failed && diag_on)diag_event("pipeline_prewarm: failed=post count=%d",failed);
}''')

POST_LAZY_LAMP = ('06680e217a138a84cdad0b5323fa622226486e73e6807c8f6cf8c38518eef07d', r'''static int lamp_post_init(uint32_t w, uint32_t h, NSUInteger samples, int view) {
    lp_lib=lp_libraries[view];
    if (!lp_lib) {
        NSString *src = [NSString stringWithUTF8String:x360_src];
        if (view) src=x_view_source(src);
        src = [src stringByReplacingOccurrencesOfString:@"fragment float4 fs_vt(" withString:@"fragment float4 fs_vt_lamp("];
        src = [src stringByReplacingOccurrencesOfString:@"texture2d<float> bself [[texture(6)]]" withString:@"texture2d<float> bself [[texture(6)]], texture2d<float> lamp [[texture(7)]]"];
        src = [src stringByReplacingOccurrencesOfString:@"    if (patch) bl = 0;" withString:@"    bl += lamp.sample(lin, uv).rgb;\n    if (patch) bl = 0;"];
        src = [src stringByAppendingString:[NSString stringWithUTF8String:lampglow_src]];
        NSError *err = nil;
        lp_lib = [mtl newLibraryWithSource:hdrclip_shader(src) options:nil error:&err];
        if (!lp_lib) { fprintf(stderr, "boot: lampglow shader: %s\n", err.localizedDescription.UTF8String); return 0; }
        lp_libraries[view]=lp_lib;
        lp_source = x_pso(lp_lib, @"fs_lamp_source", MTLPixelFormatRGBA16Float, 0);
        lp_taps = x_pso(lp_lib, @"fs_taps", MTLPixelFormatRGBA16Float, 0);
    }
    if (samples > 8 || !lp_source || !lp_taps) return 0;
    if (!lp_vt[view][samples]) {
        MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
        d.vertexFunction = [lp_lib newFunctionWithName:@"vs_full"];
        d.fragmentFunction = [lp_lib newFunctionWithName:@"fs_vt_lamp"];
        d.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm; d.rasterSampleCount = samples;
        lp_vt[view][samples] = pipeline_build(d, __func__, NULL);
    }
    if (!lt_source || lt_source.width != w / 4 || lt_source.height != h / 4) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:w / 4 height:h / 4 mipmapped:NO];
        d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget; d.storageMode = MTLStorageModePrivate;
        lt_source = [mtl newTextureWithDescriptor:d]; lt_blur = [mtl newTextureWithDescriptor:d];
        d.width = w / 8; d.height = h / 8;
        lt_a = [mtl newTextureWithDescriptor:d]; lt_b = [mtl newTextureWithDescriptor:d];
    }
    return lp_vt[view][samples] && lt_source && lt_blur && lt_a && lt_b;
}''')

POST_LAZY_BASELINE = {
    'x_edr_pipeline': POST_LAZY_EDR,
    'render_post_prewarm': POST_LAZY_WARM,
    'lamp_post_init': POST_LAZY_LAMP,
}

def lazy_post_control(source):
    for name, (digest, fragment) in POST_LAZY_BASELINE.items():
        assert hashlib.sha256(fragment.encode()).hexdigest() == digest
        prefix = fragment[:fragment.index(name)]
        a = source.index(prefix + name + "(")
        b = source.index("\n}", a) + 2
        source = source[:a] + fragment + source[b:]
    return source


def main():
    if not (ROOT / 'gen/x360_shader_lits.inc').is_file():
        print('SKIP: HDR composition proof needs generated shader tables (runtime/build.sh)')
        return 3
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
    # The routing fixture ends before the separately guarded dispatch/draw code.
    production = section(presenter, 'void render_set_gamma(', 'static __attribute__((always_inline)) inline void present_draw(')
    legacy = ('static id<MTLLibrary> legacy_blit_lib;\n'
              'static id<MTLRenderPipelineState> legacy_blit_pso;\n'
              'static id<MTLSamplerState> legacy_blit_linear,legacy_blit_point;\n'
              'static Map legacy_blit_pipelines;\n' +
              legacy_names(LEGACY_BLIT + LEGACY_GAMMA + LEGACY_COPY + LEGACY_QUAD))
    env = dict(os.environ)
    env.pop('NFS_HDR_OUTPUT', None)
    env.pop('NFS_TEST_GAMMA', None)
    env.pop('NFS_PADS', None)
    env.pop('NFS_FLOAT_SCENE', None)
    with tempfile.TemporaryDirectory(prefix='hdr-output-test-') as scratch:
        scratch = Path(scratch)
        (scratch / 'presenter.inc').write_text(production)
        probe = (ROOT / 'probe.m').read_text()
        probe_capture = section(probe, 'static id<MTLBuffer> capture(', '// BGRA8 pixels:').replace(' capture(', ' probe_capture(')
        (scratch / 'probe.inc').write_text(probe_capture)
        frame_end = section(presenter, '    cmd = nil;\n    render_float_scene_present_end();', '    // One frame in flight:')
        (scratch / 'frame_end.inc').write_text(frame_end)
        post_code = (section(post, 'static const char *x360_src =', '#include "lampglow_shader.h"') +
                     section(post, 'static NSString *x_edr_source(', '// END HDR output M post variant') +
                     section(post, 'static const char *ssao_src =', 'typedef struct { float proj[4],size[4],rect[4],bounds[4],tune[4]; } AOParams;') +
                     section(post, 'typedef struct { float off[16][4]', '// The visual treatment into a multisampled back buffer') +
                     section(post, 'static NSString *x_view_source(', '// GaussBlur5x5') +
                     section(post, 'static id<MTLRenderPipelineState> x_contact_smoke_multiply(MTLPixelFormat format) {', 'static struct { id<MTLRenderPipelineState> kernel,blur,apply'))
        cache = (ROOT / 'pipeline_cache.m').read_text().replace('#include "d3d9_internal.h"', '')
        cache = cache.replace('    int measure=on_frame_path();',
                              '    if (!strcmp(pass,"edr")) ++pipeline_edr_builds;\n    int measure=on_frame_path();')
        library_entry = '    if (!library || !d.vertexFunction || !d.fragmentFunction) return nil;'
        assert cache.count(library_entry) == 1
        cache = cache.replace(library_entry, '    ++post_cached_calls;\n' + library_entry)
        (scratch / 'cache.inc').write_text(cache)
        (scratch / 'post.inc').write_text(post_code)
        (scratch / 'quad.inc').write_text(maps + '\nstatic Map blit_pipelines;\n' + blit + quad)
        (scratch / 'routing.inc').write_text(
            section(render, 'static MTLTextureDescriptor *texture_descriptor(', 'RenderTex *gpu_tex(') +
            section(render, 'void render_float_scene_begin(void)', '// Only actual pre-post screen-copy destinations'))
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
        checked([exe, '--cpu-lifecycle'], env=env)
        omitted_boundary = frame_end.replace('hdr_output_frame_boundary();', '')
        assert omitted_boundary != frame_end
        (scratch / 'frame_end.inc').write_text(omitted_boundary)
        checked(build)
        missing = subprocess.run([str(exe), '--cpu-lifecycle'], capture_output=True, text=True, env=env)
        assert missing.returncode == 1 and 'FAIL: H=4 boundary sample' in missing.stderr, missing.stdout + missing.stderr
        print('hdr_output_test: CPU omitted frame boundary rejected 1 vs 4 (exit 1) PASS', flush=True)
        (scratch / 'frame_end.inc').write_text(frame_end)
        for name, bad_source, failure in (
            ('stale headroom', production.replace('hdr_frame_headroom=ceiling;', 'hdr_frame_headroom=1;'),
             'FAIL: H=4 boundary sample'),
            ('unlatched headroom', production.replace('return hdr_output_on ? hdr_frame_headroom : 1;',
             'return hdr_output_on ? hdr_output_ceiling((float)hdr_output_screen().maximumExtendedDynamicRangeColorComponentValue) : 1;'),
             'FAIL: headroom changed inside frame'),
        ):
            assert bad_source != production
            (scratch / 'presenter.inc').write_text(bad_source)
            checked(build)
            bad = subprocess.run([str(exe), '--cpu-lifecycle'], capture_output=True, text=True, env=env)
            assert bad.returncode == 1 and failure in bad.stderr, bad.stdout + bad.stderr
            print(f'hdr_output_test: CPU broken {name} control rejected (exit 1) PASS', flush=True)
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
        env['NFS_HDR_OUTPUT']='off'
        # Never treat sandbox device denial as GPU evidence.
        result = subprocess.run([str(exe), '--sdr'], capture_output=True, text=True,
                                env={**env, 'MTL_DEBUG_LAYER': '1'})
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode == 3:
            return 3
        assert result.returncode == 0, 'SDR baseline failed'
        auto_env={k:v for k,v in env.items() if k!='NFS_HDR_OUTPUT'}
        checked([exe, '--auto-hdr'], env=auto_env)
        old_request=production.replace('hdr_output_requested = nfs_hdr_output_requested();',
            'hdr_output_requested = nfs_hdr_output_mode()==2;')
        assert old_request!=production
        (scratch / 'presenter.inc').write_text(old_request)
        checked(build)
        old_auto=subprocess.run([str(exe), '--auto-hdr'], capture_output=True, text=True, env=auto_env)
        assert old_auto.returncode==1 and 'FAIL: Auto potential 7 startup' in old_auto.stderr, old_auto.stdout+old_auto.stderr
        print('hdr_output_test: old Auto startup 1 failure vs production 0 rejected PASS', flush=True)
        (scratch / 'presenter.inc').write_text(production)

        checked(build)
        edr_env = {**env, 'NFS_HDR_OUTPUT': 'edr', 'MTL_DEBUG_LAYER': '1'}
        for _ in range(8):
            for on in (True, False, True):
                checked([exe, '--startup'], env=edr_env if on else env)
        print('hdr_output_test: restart On/Off/On x8 formats=24/24 stale=0 PASS', flush=True)
        checked([exe, '--startup'], env={**edr_env, 'NFS_PADS': '2'})
        stale_layer = production.replace('layer.pixelFormat = MTLPixelFormatRGBA16Float;',
                                         'layer.pixelFormat = MTLPixelFormatBGRA8Unorm;')
        assert stale_layer != production
        (scratch / 'presenter.inc').write_text(stale_layer)
        checked(build)
        stale = subprocess.run([str(exe), '--startup'], capture_output=True, text=True, env=edr_env)
        assert stale.returncode == 1 and 'FAIL: startup layer format 80 expected 115' in stale.stderr, stale.stdout + stale.stderr
        print('hdr_output_test: stale startup format control rejected 80 vs 115 (exit 1) PASS', flush=True)
        (scratch / 'presenter.inc').write_text(production)
        checked(build)
        checked([exe, '--fallback'], env=edr_env)
        checked([exe, '--edr'], env=edr_env)
        bad_probe = probe_capture.replace('[e endEncoding];',
            '[e fillBuffer:b range:NSMakeRange(0,w*h*4) value:0]; [e endEncoding];')
        assert bad_probe != probe_capture
        (scratch / 'probe.inc').write_text(bad_probe)
        checked(build)
        probe_control = subprocess.run([str(exe), '--edr'], capture_output=True, text=True, env=edr_env)
        assert probe_control.returncode == 1 and 'FAIL: probe SDR bytes differ mismatches=17' in probe_control.stderr, probe_control.stdout + probe_control.stderr
        print('hdr_output_test: zeroed probe control rejected 17/20 bytes (exit 1) PASS', flush=True)
        (scratch / 'probe.inc').write_text(probe_capture)
        checked(build)
        broken = subprocess.run([str(exe), '--broken'], capture_output=True, text=True, env=edr_env)
        print(broken.stdout + broken.stderr, end='', flush=True)
        assert broken.returncode == 1 and 'FAIL: PATCH above_one=0 lost=2 bad_channels=6' in broken.stderr, 'BGRA8 mutant escaped or failed for another reason'
        default_proof = checked([exe, '--post-default'], env=edr_env).stdout
        default_proof += checked([exe, '--post-disabled'], env=edr_env).stdout
        old_default_cache = cache.replace('return !s || atoi(s);', 'return s && atoi(s);')
        assert old_default_cache != cache
        (scratch / 'cache.inc').write_text(old_default_cache)
        checked(build)
        old_default = subprocess.run([str(exe), '--post-default'], capture_output=True, text=True, env=edr_env)
        assert old_default.returncode == 1 and 'scene prewarm default/override' in old_default.stderr, old_default.stdout + old_default.stderr
        print('hdr_output_test: old scene default rejected: unset=0, expected 1 (exit 1)', flush=True)
        (scratch / 'cache.inc').write_text(cache)
        (scratch / 'post.inc').write_text(lazy_post_control(post_code))
        checked(build)
        lazy = subprocess.run([str(exe), '--post-default'], capture_output=True, text=True, env=edr_env)
        print('hdr_output_test: frozen lazy default control:', lazy.stdout.strip(), flush=True)
        assert lazy.returncode == 1 and 'frame_sync=4' in lazy.stdout, lazy.stdout + lazy.stderr
        lazy_off = subprocess.run([str(exe), '--post-disabled'], capture_output=True, text=True, env=edr_env)
        print('hdr_output_test: frozen lazy explicit-0 control:', lazy_off.stdout.strip(), flush=True)
        assert lazy_off.returncode == 1 and 'frame_sync=4' in lazy_off.stdout, lazy_off.stdout + lazy_off.stderr
        if os.environ.get('NFS_METAL_CACHE'):
            (Path(os.environ['NFS_METAL_CACHE']) / 'post-default-proof.log').write_text(
                default_proof + 'frozen lazy unset control: ' + lazy.stdout + 'frozen lazy 0 control: ' + lazy_off.stdout)
        # Without the memo every steady post pass re-enters the pipeline cache.
        unmemoized = post_code
        for hit in ('    if (p) return p;\n    id<MTLLibrary> library=x_post_library(1+lamp);\n',
                    '    if (lp_vt[view][samples]) return lp_vt[view][samples];   // memoized, as x_edr_states\n'):
            assert unmemoized.count(hit) == 1, hit
            unmemoized = unmemoized.replace(hit, hit[hit.index('    id<'):] if 'library=' in hit else '')
        (scratch / 'post.inc').write_text(unmemoized)
        checked(build)
        for mode, failure in (('--post-default', 'FAIL: post repeated lookups made '),
                              ('--prewarm', 'FAIL: steady post frames made ')):
            bad = subprocess.run([str(exe), mode], capture_output=True, text=True, env=edr_env)
            assert bad.returncode == 1 and failure in bad.stderr, bad.stdout + bad.stderr
            calls = int(bad.stderr.split(failure)[1].split()[0])
            assert calls > 0
            print(f'hdr_output_test: unmemoized post {mode} control: {calls} steady pipeline cache calls vs 0 (exit 1) PASS', flush=True)
        (scratch / 'post.inc').write_text(post_code)
        checked(build)
        checked([exe, '--prewarm'], env=edr_env)
        checked([exe, '--prewarm-msaa'], env=edr_env)
        checked([exe, '--prewarm-lamps'], env=edr_env)
        wrong_lamp = harness.replace('held?x_edr_pipeline(@"fs_vt",samples,lamp):direct',
                                     'held?x_edr_pipeline(@"fs_vt",samples,0):direct')
        assert wrong_lamp != harness
        (scratch / 'test.m').write_text(wrong_lamp)
        checked(build)
        missing = subprocess.run([str(exe), '--prewarm-lamps'], capture_output=True, text=True, env=edr_env)
        assert missing.returncode == 1 and 'held lamp pixels differ lamp=1 samples=1 differing=384/1024' in missing.stderr, missing.stdout + missing.stderr
        print('hdr_output_test: wrong lamp variant rejected:', missing.stderr.strip(), flush=True)
        (scratch / 'test.m').write_text(harness)
        skipped_downscale = post_code.replace('for (NSString *fs in @[@"fs_downscale",', 'for (NSString *fs in @[')
        assert skipped_downscale != post_code
        (scratch / 'post.inc').write_text(skipped_downscale)
        checked(build)
        missing = subprocess.run([str(exe), '--prewarm'], capture_output=True, text=True, env=edr_env)
        assert missing.returncode == 1 and 'frame_builds=2' in missing.stdout, missing.stdout + missing.stderr
        print('hdr_output_test: EDR prewarm normal 0 vs missing-downscale 2 frame builds PASS', flush=True)
        (scratch / 'post.inc').write_text(post_code)
        checked(build)
        checked([exe, '--composition'], env=edr_env)
        checked([exe, '--composition-msaa'], env=edr_env)
        bad_capture = production.replace('show && present_graded ? present_tex : backbuffer_tex;',
                                         'show && present_graded ? backbuffer_tex : backbuffer_tex;')
        assert bad_capture != production
        (scratch / 'presenter.inc').write_text(bad_capture)
        checked(build)
        capture = subprocess.run([str(exe), '--fallback'], capture_output=True, text=True, env=edr_env)
        print(capture.stdout + capture.stderr, end='', flush=True)
        assert capture.returncode == 1 and 'FAIL: SDR capture bytes differ grade=1 mismatches=2016' in capture.stderr, 'capture missing gamma control escaped'
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
