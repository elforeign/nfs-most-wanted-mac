"""Public MW lamp path; no game launches or embedded car/shader assets.

Contract: explicit converted lamp identities select an isolated shader variant;
native and Off keep the frozen production selector/patch sources. Public header
preprocessing must exclude the private implementation. Reviewed default-On
switch fails 3/5 opt-in cases; fixed switch fails 0/5. The reviewed switch
is pinned below and run against the same five expectations.
Original public helper is suitable MW treatment: its equation is pinned and
tested unchanged. Dropped-reflection and double-alpha controls must fail.
Measured: 108 rendered cases, 0 scalar failures with blending off/on;
dropped-reflection control fails 216 scalars, double-alpha fails 36.
Optional local bytecode fixtures: 32 public production variants compile,
10 original PC bodies preserved. GPU checks run through the offline harness.
"""
import ast
import hashlib
import itertools
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
SRC = (RT / 'materials.m').read_text()


def function(source, name):
    found = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', source, re.M)
    assert found, name
    start = source.index('{', found.start())
    depth = 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', source[start:], re.S):
        if token[0] == '{':
            depth += 1
        elif token[0] == '}':
            depth -= 1
            if depth == 0:
                return source[found.start():start + token.end()]
    raise AssertionError(name)


def initializer(source, name):
    found = re.search(r'^static const char ' + name + r'\[\]\s*=', source, re.M)
    assert found, name
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|;', source[found.end():], re.S):
        if token[0] == ';':
            return source[found.end():found.end()+token.end()] + '\n'
    raise AssertionError(name)


def macro(source, name):
    found = re.search(r'^#define ' + name + r'\b[^\n]*', source, re.M)
    assert found, name
    end = found.end()
    while source[found.start():end].rstrip().endswith('\\'):
        end = source.index('\n', end + 1)
    return source[found.start():end]


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


# Frozen reviewed default-On switch, before making the treatment opt-in.
OLD_SWITCH = '''static int carbon_lampglass_on(void) {
    static int on = -1;
    if (on < 0) { const char *e = getenv("NFS_CARBON_LAMPGLASS"); on = !(e && !strcmp(e, "off")); }
    return on;
}'''
OLD_HELPER = '''static float4 carbon_lamp(float4 tex, float4 light, float shade, float3 specular, float3 reflection, float2 masks) {
    return float4(light.rgb * shade * tex.rgb + specular + reflection, tex.a * light.a);
}'''
PINS = {
    'mat_replace': '090cc8b487bfc4667782ada08b4f0757ce78ae0f3f053d538510d3e8ea50a45c',
    'mat_replace_profile': '2934e2540fbb90d18704ceff64e1988564a5f46979869d9d42c0f60092b51670',
    'car_vs_patch': '4b6bd0861f07ce6f90635f80c349de728b2d3e6a8b4b2aa41a8aeaac35afe29a',
    'car_t0_ps_patch': '2d3ebd34e24953de29673b1c094b5802c68ce8218262cc29f1f3c9ce29fbf316',
}


def switches_and_namespace(tmp):
    for name, digest in PINS.items():
        assert hashlib.sha256(function(SRC, name).encode()).hexdigest() == digest, name
    assert hashlib.sha256(function(SRC, 'mat_replace').replace(', 0);', ', 1);').encode()).hexdigest() != PINS['mat_replace']
    for label, body, expected in (('actual', function(SRC, 'carbon_lampglass_on'), 0),
                                  ('original', OLD_SWITCH, 3)):
        path = tmp / f'{label}.c'
        path.write_text('#include <stdlib.h>\n#include <string.h>\n' + body +
                        '\nint main(void){return carbon_lampglass_on();}\n')
        exe = tmp / label
        run(['clang', '-Werror', '-DNFS_PUBLIC', path, '-o', exe])
        failures = 0
        for value, want in ((None, 0), ('', 0), ('on', 1), ('off', 0), ('invalid', 0)):
            env = dict(os.environ)
            env.pop('NFS_CARBON_LAMPGLASS', None)
            if value is not None:
                env['NFS_CARBON_LAMPGLASS'] = value
            failures += subprocess.run([str(exe)], env=env).returncode != want
        assert failures == expected, (label, failures)
        print(f'public {label} switch: {failures}/5 failures', flush=True)
    line = re.search(r'^static int carbon_lamp_hash.*$', SRC, re.M)[0]
    (tmp / 'identities.c').write_text('#include <stdint.h>\n' + line + '''
int main(void) {
 return !carbon_lamp_hash(0xDAEE90A1u) || !carbon_lamp_hash(0x3CF307CEu) ||
        carbon_lamp_hash(0xA6348EE3u) || carbon_lamp_hash(0xD79597D6u) || carbon_lamp_hash(0u);
}''')
    run(['clang', '-Werror', tmp / 'identities.c', '-o', tmp / 'identities'])
    run([tmp / 'identities'])
    # Public selection cannot acquire a private include even indirectly.
    header = tmp / 'headers.c'
    header.write_text('#include "materials.h"\nconst char *full=CARBON_LAMP_MSL;\nconst char *retail=CARBON_LAMP_RETAIL_MSL;\n')
    pp = run(['clang', '-E', '-P', '-H', '-DNFS_PUBLIC', '-I', RT, header], capture_output=True, text=True)
    assert 'carbon_lamp_private.h' not in pp.stderr
    assert 'car_lamp_public.h' in pp.stderr
    def value(name):
        text = re.search(r'const char \*' + name + r'=(.*?);\s*$', pp.stdout, re.M)[1]
        return ''.join(ast.literal_eval(t) for t in re.findall(r'"(?:\\.|[^"\\])*"', text))
    full, retail = value('full'), value('retail')
    assert function(retail, 'carbon_lamp') == OLD_HELPER
    assert function(full, 'carbon_lamp') == OLD_HELPER
    print('public include isolation, 5 identity cases, 4 native/Off source pins: PASS', flush=True)
    return full, retail


TRANSLATOR = r'''
#include "sm_translate.h"
#include <fstream>
#include <vector>
int main(int argc,char**argv){if(argc!=3)return 2;
 std::ifstream f(argv[1],std::ios::binary|std::ios::ate);if(!f)return 3;
 size_t n=f.tellg();f.seekg(0);std::vector<uint32_t>w(n/4);f.read((char*)w.data(),n);
 SmOptions o;sm_default_options(&o);SmResult r={};if(sm_translate(w.data(),w.size(),&o,&r))return 1;
 std::ofstream(argv[2])<<r.msl;sm_result_free(&r);return 0;}
'''


def compile_fragments(tmp):
    fixture_dir = ROOT / 'host/shaders/testdata/car'
    if not (fixture_dir / 't0_car_p0_ps_1_1.bin').is_file():
        print('SKIP optional full car fragments: local bytecode absent', flush=True)
        return
    shaders = ROOT / 'host/shaders'
    (tmp / 'translate.cpp').write_text(TRANSLATOR)
    run(['clang++', '-std=c++17', '-O1', '-I', shaders, tmp / 'translate.cpp',
         shaders / 'sm_parse.cpp', shaders / 'sm_msl.cpp', '-o', tmp / 'translate'])
    pre = '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "materials.h"\n'
    pre += '#include "../host/shaders/sm_coverage.h"\n'
    for name in ('car_shadow_off', 'pc_shader_lits', 'x360_shader_lits'):
        pre += f'#include "gen/{name}.inc"\n'
    pre += macro(SRC, 'CAR_PAINT_POW_MSL') + '\n'
    pre += 'static int car_light_hdr(void){return 1;}\n'
    pre += 'static const char carbon_lamp_math[]=CARBON_LAMP_MSL;\n'
    pre += 'static const char carbon_lamp_retail_math[]=CARBON_LAMP_RETAIL_MSL;\n'
    pre += 'static const char car_paint_msl[]=' + initializer(SRC, 'car_paint_msl')
    for name in ('dup_replace', 'edit', 'car_t0_ps_patch', 'carbon_lamp_patch', 'carbon_lamp_pc_patch',
                 'carbon_lamp_retail_patch', 'carbon_lamp_retail_pc_patch'):
        pre += function(SRC, name) + '\n'
    pre += r'''
int main(int argc,char**argv){if(argc!=7)return 2;
 FILE*f=fopen(argv[1],"rb");if(!f)return 3;fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
 char*s=calloc(n+1,1);fread(s,1,n,f);fclose(f);
 int mode=atoi(argv[3]),variant=atoi(argv[4]),hw=atoi(argv[5]),clear=atoi(argv[6]);char*out=NULL;
 if(mode==0){char*base=car_t0_ps_patch(s);if(base)out=clear?carbon_lamp_patch(base,1):carbon_lamp_retail_patch(base,1);}
 else if(mode==1)out=clear?carbon_lamp_pc_patch(s,variant,hw):carbon_lamp_retail_pc_patch(s,variant,hw);
 else{char*base=malloc(strlen(car_paint_msl)+256);sprintf(base,"#define MAT_VARIANT %d\n#define MAT_SHADOW_HW %d\n#define MAT_CAR_HDR 1\n%s",variant,hw,car_paint_msl);
  out=clear?carbon_lamp_patch(base,0):carbon_lamp_retail_patch(base,0);}
 if(!out)return 4;f=fopen(argv[2],"w");fputs(out,f);fclose(f);free(out);free(s);return 0;}
'''
    (tmp / 'emit.c').write_text(pre)
    run(['clang', '-Werror', '-Wno-unused-result', '-DNFS_PUBLIC', '-I', RT,
         tmp / 'emit.c', '-o', tmp / 'emit'])
    count = 0
    for variant in range(6):
        path = fixture_dir / (f't{variant}_car_p0_ps_2_{"0" if variant == 1 else "x"}.bin' if variant else 't0_car_p0_ps_1_1.bin')
        translated = tmp / f'input{variant}.metal'
        run([tmp / 'translate', path, translated])
        for clear in (0, 1):
            modes = [(0, 0)] if variant == 0 else [(1, 0), (2, 0), (2, 1)]
            for mode, hardware in modes:
                output = tmp / f'car{variant}_{mode}_{hardware}_{clear}.metal'
                run([tmp / 'emit', translated, output, mode, variant, hardware, clear])
                if mode == 1:
                    assert function(translated.read_text(), 'sm_body') in output.read_text()
                result = subprocess.run(['xcrun', 'metal', '-fmodules-cache-path=' + str(ROOT / '.metal-cache'),
                                         '-c', str(output), '-o', str(output.with_suffix('.air'))], capture_output=True, text=True)
                assert result.returncode == 0, result.stderr[-2000:]
                count += 1
    assert count == 32
    print(f'public production fragments: {count} compiled; 10 original PC bodies preserved', flush=True)


GPU = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdio.h>
int main(int argc,char**argv){@autoreleasepool{
 id<MTLDevice>d=MTLCreateSystemDefaultDevice();if(!d){puts("PENDING: Metal render device unavailable");return 77;}
 NSError*e=nil;NSString*s=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&e];
 id<MTLLibrary>l=[d newLibraryWithSource:s options:nil error:&e];if(!l){fprintf(stderr,"%s\n",e.description.UTF8String);return 2;}
 NSData*data=[NSData dataWithContentsOfFile:@(argv[2])];NSUInteger n=data.length/(6*16);
 id<MTLBuffer>in=[d newBufferWithBytes:data.bytes length:data.length options:MTLResourceStorageModeShared];
 MTLTextureDescriptor*t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:n height:1 mipmapped:NO];
 t.storageMode=MTLStorageModeShared;t.usage=MTLTextureUsageRenderTarget;
 id<MTLTexture>out=[d newTextureWithDescriptor:t];id<MTLCommandQueue>q=[d newCommandQueue];
 for(int blend=0;blend<2;blend++){
  MTLRenderPipelineDescriptor*p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[l newFunctionWithName:@"vertex_main"];p.fragmentFunction=[l newFunctionWithName:@"fragment_main"];
  p.colorAttachments[0].pixelFormat=t.pixelFormat;p.colorAttachments[0].blendingEnabled=blend;
  p.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorSourceAlpha;p.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorOneMinusSourceAlpha;
  p.colorAttachments[0].sourceAlphaBlendFactor=MTLBlendFactorOne;p.colorAttachments[0].destinationAlphaBlendFactor=MTLBlendFactorZero;
  id<MTLRenderPipelineState>ps=[d newRenderPipelineStateWithDescriptor:p error:&e];if(!ps){fprintf(stderr,"%s\n",e.description.UTF8String);return 2;}
  MTLRenderPassDescriptor*r=[MTLRenderPassDescriptor renderPassDescriptor];r.colorAttachments[0].texture=out;r.colorAttachments[0].loadAction=MTLLoadActionClear;r.colorAttachments[0].storeAction=MTLStoreActionStore;r.colorAttachments[0].clearColor=MTLClearColorMake(.2,.3,.4,1);
  id<MTLCommandBuffer>b=[q commandBuffer];id<MTLRenderCommandEncoder>enc=[b renderCommandEncoderWithDescriptor:r];
  [enc setRenderPipelineState:ps];[enc setVertexBuffer:in offset:0 atIndex:0];[enc setFragmentBuffer:in offset:0 atIndex:0];
  unsigned width=(unsigned)n;[enc setVertexBytes:&width length:4 atIndex:1];[enc drawPrimitives:MTLPrimitiveTypePoint vertexStart:0 vertexCount:n];[enc endEncoding];[b commit];[b waitUntilCompleted];if(b.status==MTLCommandBufferStatusError)return 3;
  float*got=calloc(n*4,4);[out getBytes:got bytesPerRow:n*16 fromRegion:MTLRegionMake2D(0,0,n,1) mipmapLevel:0];
  const float*want=data.bytes;unsigned failures=0;float bg[]={.2,.3,.4};
  for(NSUInteger i=0;i<n;i++)for(int k=0;k<4;k++){
   float v=want[i*24+20+k],a=want[i*24+23];if(blend&&k<3)v=v*a+bg[k]*(1-a);
   failures+=!isfinite(got[i*4+k])||fabs(got[i*4+k]-v)>2e-5*fmax(1,fabs(v));}
  printf("public render blend=%d: %lu cases, %u scalar failures\n",blend,(unsigned long)n,failures);free(got);if(failures)return 1;
 }return 0;}}
'''


def render_helper(tmp, helper):
    cases = []
    for alpha, material_alpha, shade, amplitude in itertools.product((0, .25, .75, 1), (0, .3, 1), (0, .5, 1), (0, .5, 8)):
        tex = (.25, .5, .75, alpha)
        light = (.4, 1, 2, material_alpha)
        spec = (amplitude, .2 * amplitude, .1 * amplitude, 0)
        reflection = (.3 * amplitude, .5 * amplitude, amplitude, 0)
        want = tuple(light[i] * shade * tex[i] + spec[i] + reflection[i] for i in range(3)) + (alpha * material_alpha,)
        cases.append((*tex, *light, *spec, *reflection, shade, 0, 0, 0, *want))
    (tmp / 'cases.bin').write_bytes(b''.join(struct.pack('<24f', *v) for v in cases))
    shader = '''#include <metal_stdlib>
using namespace metal;
''' + helper + '''
struct V {float4 position [[position]];float size [[point_size]];uint index [[flat]];};
vertex V vertex_main(uint id [[vertex_id]],device const float4 *inputs [[buffer(0)]],constant uint &width [[buffer(1)]]) {
 V v;v.position=float4((float(id)+.5f)*2.0f/float(width)-1.0f,0,0,1);v.size=1;v.index=id;return v;
}
fragment float4 fragment_main(V v [[stage_in]],device const float4 *inputs [[buffer(0)]]) {
 uint i=v.index*6;return carbon_lamp(inputs[i],inputs[i+1],inputs[i+4].x,inputs[i+2].rgb,inputs[i+3].rgb,float2(0));
}
'''
    (tmp / 'gpu.m').write_text(GPU)
    run(['clang', '-O1', '-fobjc-arc', tmp / 'gpu.m', '-framework', 'Foundation', '-framework', 'Metal', '-o', tmp / 'gpu'])
    variants = {'actual': shader,
                'lost_reflection': shader.replace('+ specular + reflection', '+ specular'),
                'double_alpha': shader.replace('tex.a * light.a)', 'tex.a * tex.a * light.a)')}
    for name, source in variants.items():
        path = tmp / f'{name}.metal'
        path.write_text(source)
        result = subprocess.run([str(tmp / 'gpu'), str(path), str(tmp / 'cases.bin')],
                                capture_output=True, text=True)
        print(result.stdout, end='', flush=True)
        assert not result.stderr, result.stderr[-2000:]
        if result.returncode == 77:
            assert not os.environ.get('CODEX_GPU_CHECK'), 'requested GPU check did not run'
            return
        assert result.returncode == (0 if name == 'actual' else 1), name
        counts = [int(n) for n in re.findall(r'(\d+) scalar failures', result.stdout)]
        assert counts == {'actual': [0, 0], 'lost_reflection': [216], 'double_alpha': [36]}[name], (name, counts)


def main():
    scratch = ROOT / '.metal-cache/car-lighting'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='public-') as td:
        tmp = Path(td)
        full, retail = switches_and_namespace(tmp)
        compile_fragments(tmp)
        render_helper(tmp, retail)


if __name__ == '__main__':
    main()
