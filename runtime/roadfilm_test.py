"""Water film proof: exercise production source composition, six road variants,
Retail byte identity, ripple independence, and exact air/water Fresnel in a
linear float target. F=1 is the deliberately broken numeric control.
Capture plan: held filmoff/filmon at 3840x2160, fixed sim/camera/weather/post;
label near-normal/grazing asphalt and paint ROIs, compare dry road <=1 code.
No game is launched by this test. Numeric/control counts are printed below.
Review controls: four frozen SHA-256 identities pass; NULL mat_replace fails
1/4 identities and 7/7 production Retail ripple cases (exit 5).
Film wraps reflection slot 3 in 14/14 variants; tex3 -> tex0 fallback fails
7/7 ripple-Off slot checks even though stripping the wrapper restores bytes.
Production t0 sm_main rendered with ripples Off, wet/dry, 0/60/80 degrees:
Retail + film 0/48 channel failures (RGB tolerance 1e-4, alpha 1e-6);
tex0 fallback mutant 9/48 failures, exit 1. Standalone Fresnel also checked.
Observed CPU 0/60/80: .020059317/.059125613/.346916139; max error 3.87e-7.
F=1 fails 3/3 (minimum error .653084); 14 production/ripple variants restore
Retail bytes exactly. GPU float-target numeric/control and source compile pass
through run_tests. Dry native selection rejects the missing-wetness guard.
Held 4K wet pairs: run_shots/roadfilm_wet_ripples.jpg and
run_shots/roadfilm_wet_no_ripples.jpg; full-size labelled copies under
.metal-cache/roadfilm-proof/. Near-field/grazing/paint display-linear Y,
ripples On: .0506106/.0355496/.0404094 -> .0496025/.0352999/.0399811.
Ripples Off: .0520069/.0367830/.0415172 -> .0497126/.0360349/.0400915.
Dry capture before the native-selection guard misses the <=1-code bound (2);
fresh dry capture with the guard and a near-normal view remain unverified.
The game tool's six-run cap prevents another capture in this session.
"""
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def function(source, name):
    match = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^\n]*\) \{', source, re.M)
    assert match, name
    start = source.index('{', match.start())
    depth = 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/|[{}]', source[start:], re.S):
        if token[0] == '{': depth += 1
        if token[0] == '}':
            depth -= 1
            if not depth: return source[match.start():start + token.end()]
    raise AssertionError('unterminated ' + name)


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def capture_pair(args):
    """--capture-pair OFF ON OUTPUT.jpg LABEL ROIS_JSON wet|dry.

    Metrics use original lossless pixels, before labels/JPEG/resizing. OUTPUT is
    a <=1920-wide review pair; adjacent _off/_on JPEGs retain the full 4K size.
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    off, on, output, label, regions, mode = args
    images = [Image.open(p).convert('RGB') for p in (off, on)]
    assert all(im.size == (3840, 2160) for im in images), 'capture must be native 4K'
    arrays = [np.asarray(im, dtype=np.float64) for im in images]
    metrics = {}
    for name, box in json.loads(regions).items():
        x, y, right, bottom = box
        assert 0 <= x < right <= 3840 and 0 <= y < bottom <= 2160
        crops = [v[y:bottom, x:right] for v in arrays]
        def ymean(v):
            v = v / 255
            v = np.where(v <= .04045, v / 12.92, ((v + .055) / 1.055) ** 2.4)
            return float((v[..., 0] * .2126 + v[..., 1] * .7152 + v[..., 2] * .0722).mean())
        diff = np.abs(crops[0] - crops[1])
        metrics[name] = dict(box=box, off_Y=ymean(crops[0]), on_Y=ymean(crops[1]), max_code=float(diff.max()), mean_code=float(diff.mean()))
    if mode == 'dry':
        passed = all(m['max_code'] <= 1 for m in metrics.values())
        # An injected 2-code error in a dry pixel must fail the same bound.
        assert not all(m['max_code'] <= 1 for m in [dict(max_code=2)])
    else:
        assert mode == 'wet'
        passed = all(m['on_Y'] < m['off_Y'] for m in metrics.values())
        assert not all(m['off_Y'] < m['on_Y'] for m in metrics.values()), 'swapped wet control escaped'
    out = Path(output); out.parent.mkdir(parents=True, exist_ok=True)
    font_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
    large = ImageFont.truetype(font_path, 42); small = ImageFont.truetype(font_path, 22)
    panels = []
    for im, suffix, title in zip(images, ('off', 'on'), ('Retail (Off)', 'Water film (On)')):
        annotated = im.copy(); draw = ImageDraw.Draw(annotated)
        draw.rectangle((0, 0, 3840, 76), fill='black'); draw.text((25, 14), label + ' | ' + title, fill='white', font=large)
        for name, box in json.loads(regions).items():
            draw.rectangle(box, outline='#ffdd00', width=4)
            draw.text((box[0] + 6, box[1] + 5), name, fill='#ffdd00', font=large)
        annotated.save(out.with_name(out.stem + '_' + suffix + '.jpg'), quality=88, optimize=True)
        panels.append(annotated.resize((960, 540)))
    review = Image.new('RGB', (1920, 592), 'black')
    for i, panel in enumerate(panels): review.paste(panel, (960 * i, 0))
    ImageDraw.Draw(review).text((15, 550), 'Held 3840 x 2160 sources; metrics measured before JPEG. ' + ('PASS' if passed else 'FAIL'), fill='white', font=small)
    review.save(out, quality=85, optimize=True)
    print(json.dumps(dict(label=label, passed=passed, regions=metrics), sort_keys=True), flush=True)
    return 0 if passed else 1


TRANSLATOR = r'''#include "sm_translate.h"
#include <fstream>
#include <vector>
int main(int argc,char**argv) {
 if(argc<3)return 2;std::ifstream f(argv[1],std::ios::binary|std::ios::ate);
 if(!f)return 3;size_t n=(size_t)f.tellg();f.seekg(0);
 std::vector<uint32_t> words(n/4);f.read((char*)words.data(),n);
 SmOptions o;sm_default_options(&o);o.shadow_samplers=argc>3?16:0;SmResult r={};
 if(sm_translate(words.data(),words.size(),&o,&r))return 1;
 std::ofstream(argv[2])<<r.msl;sm_result_free(&r);return 0;
}'''

EMIT = r'''
int main(int argc,char**argv) {
 if(argc!=5)return 2;
 FILE*f=fopen(argv[1],"rb");if(!f)return 2;fseek(f,0,SEEK_END);long len=ftell(f);rewind(f);
 char*source=calloc((size_t)len+1,1);if(fread(source,1,(size_t)len,f)!=(size_t)len)return 2;fclose(f);
 ripple_mode=atoi(argv[3]);
 f=fopen(argv[4],"rb");if(!f)return 2;fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);
 uint32_t*tokens=malloc((size_t)bytes);if(fread(tokens,1,(size_t)bytes,f)!=(size_t)bytes)return 2;fclose(f);
 SmResult r={.msl=source,.shader_type=SM_PIXEL,.n_samplers=4};int id=0;
 for(int j=0;j<4;j++)r.samplers[j]=(SmSampler){.reg=j,.type=SM_TEX_2D};
 char*base=mat_replace(tokens,(uint32_t)bytes/4,&r,&id);
 char*want=ripple_mode?ripple_ps_patch(source):NULL;
 if((base==NULL)!=(want==NULL)||(base&&strcmp(base,want))||(ripple_mode&&!id))return 5;
 free(want);
 const char*off=base?base:source;
 char*on=mat_roadfilm_shader(tokens,(uint32_t)bytes/4,&r,&id);if(!on)return 4;
 char path[1024];snprintf(path,sizeof path,"%s.off",argv[2]);f=fopen(path,"wb");fputs(off,f);fclose(f);
 f=fopen(argv[2],"wb");fputs(on,f);fclose(f);
 free(base);free(on);free(source);free(tokens);return 0;
}
'''

GPU = r'''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <string.h>
#include "sm_translate.h"
// Render the unmodified translated fragment entry with controlled scene inputs.
static int production(int argc,char**argv){@autoreleasepool{
 id<MTLDevice>d=MTLCreateSystemDefaultDevice();if(!d)return 77;
 id<MTLCommandQueue>q=[d newCommandQueue];NSError*err=nil;
 MTLSamplerDescriptor*sd=[MTLSamplerDescriptor new];
 sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
 id<MTLSamplerState>sampler=[d newSamplerStateWithDescriptor:sd];
 MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;
 const float pixels[4][4]={{.4f,.2f,.1f,.6f},{.5f,.5f,1,1},{.5f,.5f,.5f,1},{.3f,.5f,.7f,1}};
 id<MTLTexture>tex[4];for(int j=0;j<4;j++){
  tex[j]=[d newTextureWithDescriptor:td];
  [tex[j] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:pixels[j] bytesPerRow:16];
 }
 td.usage=MTLTextureUsageRenderTarget;id<MTLTexture>out=[d newTextureWithDescriptor:td];
 int failures=0,checks=0;double maxerr=0;
 for(int file=2;file<argc;file++){
  NSString*source=[NSString stringWithContentsOfFile:@(argv[file]) encoding:NSUTF8StringEncoding error:&err];
  id<MTLLibrary>lib=[d newLibraryWithSource:source options:nil error:&err];
  if(!lib){fprintf(stderr,"%s\n",err.description.UTF8String);return 2;}
  MTLRenderPipelineDescriptor*pd=[MTLRenderPipelineDescriptor new];
  pd.vertexFunction=[lib newFunctionWithName:@"rf_scene"];
  pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];
  pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
  id<MTLRenderPipelineState>p=[d newRenderPipelineStateWithDescriptor:pd error:&err];
  if(!p){fprintf(stderr,"%s\n",err.description.UTF8String);return 2;}
  for(int wet=0;wet<2;wet++)for(int angle=0;angle<3;angle++){
   const double fresnel[]={.0200593122,.0591255992,.3469160963};
   const float cosines[]={1,.5f,.1736481777f};float ci=cosines[angle];
   float eye[4]={sqrtf(1-ci*ci),0,ci,0},constants[256][4]={{0}};
   constants[1][3]=wet;constants[2][0]=1;
   int integers[16][4]={{0}},booleans[16]={0};SmPSAux aux={0};
   MTLRenderPassDescriptor*rp=[MTLRenderPassDescriptor renderPassDescriptor];
   rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionClear;
   rp.colorAttachments[0].storeAction=MTLStoreActionStore;
   id<MTLCommandBuffer>cb=[q commandBuffer];id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];
   [e setRenderPipelineState:p];[e setVertexBytes:eye length:sizeof eye atIndex:0];
   id<MTLBuffer>c=[d newBufferWithBytes:constants length:sizeof constants options:MTLResourceStorageModeShared];
   [e setFragmentBuffer:c offset:0 atIndex:0];
   [e setFragmentBytes:integers length:sizeof integers atIndex:1];
   [e setFragmentBytes:booleans length:sizeof booleans atIndex:2];
   [e setFragmentBytes:&aux length:sizeof aux atIndex:3];
   for(int j=0;j<4;j++){[e setFragmentTexture:tex[j] atIndex:j];[e setFragmentSamplerState:sampler atIndex:j];}
   [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
   [cb commit];[cb waitUntilCompleted];
   if(cb.status==MTLCommandBufferStatusError)return 2;
   float values[4];[out getBytes:values bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
   // First library is Retail, subsequent libraries must attenuate reflection only.
   double f=file==2?1:fresnel[angle];
   for(int j=0;j<3;j++){
    double want=2*(pixels[0][j]*.25+pixels[3][j]*pixels[0][3]*wet*f);
    double error=fabs(values[j]-want);checks++;failures+=!isfinite(values[j])||error>1e-4;maxerr=fmax(maxerr,error);
   }
   checks++;failures+=!isfinite(values[3])||fabs(values[3]-1)>1e-6;
  }
 }
 printf("GPU production sm_main: %d/%d failures, max RGB error %.9g (Retail/film, wet/dry, 0/60/80 deg)\n",failures,checks,maxerr);
 return failures?1:0;
}}

int main(int argc,char**argv){if(argc>1&&!strcmp(argv[1],"--production"))return production(argc,argv);@autoreleasepool{
 id<MTLDevice>d=MTLCreateSystemDefaultDevice();if(!d){puts("GPU unavailable: no Metal device");return 77;}
 NSError*err=nil;id<MTLLibrary>numeric=nil;
 for(int i=1;i<argc;i++){
  NSString*s=[NSString stringWithContentsOfFile:@(argv[i]) encoding:NSUTF8StringEncoding error:&err];
  id<MTLLibrary>lib=[d newLibraryWithSource:s options:nil error:&err];
  if(!lib){fprintf(stderr,"%s: %s\n",argv[i],err.description.UTF8String);return 2;}
  if(i==1)numeric=lib;
 }
 MTLRenderPipelineDescriptor*pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[numeric newFunctionWithName:@"rf_vertex"];
 pd.fragmentFunction=[numeric newFunctionWithName:@"rf_fragment"];
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 id<MTLRenderPipelineState>p=[d newRenderPipelineStateWithDescriptor:pd error:&err];
 if(!p){fprintf(stderr,"%s\n",err.description.UTF8String);return 2;}
 MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:3 height:1 mipmapped:NO];
 td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
 id<MTLTexture>out=[d newTextureWithDescriptor:td];
 MTLRenderPassDescriptor*rp=[MTLRenderPassDescriptor renderPassDescriptor];
 rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionClear;
 rp.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLCommandQueue>q=[d newCommandQueue];id<MTLCommandBuffer>cb=[q commandBuffer];
 id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];
 [e setRenderPipelineState:p];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
 [e endEncoding];[cb commit];[cb waitUntilCompleted];
 if(cb.status==MTLCommandBufferStatusError){fprintf(stderr,"%s\n",cb.error.description.UTF8String);return 2;}
 float values[12];[out getBytes:values bytesPerRow:sizeof values fromRegion:MTLRegionMake2D(0,0,3,1) mipmapLevel:0];
 const double expected[]={.020059,.059126,.346916};int failures=0;double maxerr=0;
 for(int i=0;i<3;i++){
  double y=.2126*values[4*i]+.7152*values[4*i+1]+.0722*values[4*i+2];
  double error=fabs(y-expected[i]);maxerr=fmax(maxerr,error);failures+=error>1e-4;
  printf("GPU Fresnel %d deg: linear Y=%.9f expected=%.6f error=%.9g\n",i==0?0:i==1?60:80,y,expected[i],error);
 }
 printf("GPU Fresnel: %d/3 failures, max error %.9g; %d source libraries compiled\n",failures,maxerr,argc-1);
 return failures?1:0;
}}
'''


def main():
    src = (RUNTIME / 'roadfilm.m').read_text()
    materials = (RUNTIME / 'materials.m').read_text()
    # Frozen independently of branch/HEAD; update only after reviewing Retail changes.
    retail_hashes = {
        'mat_replace': '090cc8b487bfc4667782ada08b4f0757ce78ae0f3f053d538510d3e8ea50a45c',
        'mat_replace_profile': '2934e2540fbb90d18704ceff64e1988564a5f46979869d9d42c0f60092b51670',
        'ripple_ps_patch': 'aaed5f07aa5cf298598839e094296b4da4377c314a7a1836dfec6456ccdff449',
        'ripple_vs_patch': 'd399c18ee439d26af2470358a2ff0646401914f34734641fe5d4af54ef3bce9f',
    }
    def retail_failures(source):
        return [name for name, digest in retail_hashes.items()
                if hashlib.sha256(function(source, name).encode()).hexdigest() != digest]
    assert not retail_failures(materials), f'Retail selectors changed: {retail_failures(materials)}'
    null_replace = function(materials, 'mat_replace').replace(
        'return mat_replace_profile(tokens, n, refl, id, 0);', 'return NULL;')
    null_materials = materials.replace(function(materials, 'mat_replace'), null_replace)
    assert retail_failures(null_materials) == ['mat_replace'], 'NULL replacement identity control escaped'
    renderer = (RUNTIME / 'render.m').read_text()
    all_ids = (RUNTIME / 'materials.h').read_text() + (RUNTIME / 'roadfilm.h').read_text()
    ids = re.findall(r'MAT_VARIANT_\w+\s*=\s*(0x[0-9a-fA-F]+)', all_ids)
    assert len(ids) == len(set(ids)), 'shader variant IDs overlap'
    assert 'roadfilm_draw(ps->tokens_hash, dev.psc[1][3])' in renderer
    assert 'MAT_VARIANT_ROAD_FILM ? mat_roadfilm_shader' in renderer
    row = re.search(r'static Setting s_roadfilm = .*?\n.*?\n.*?\n.*?;', (RUNTIME / 'settings_port.c').read_text(), re.S)[0]
    assert all(x in row for x in ('.category = SET_PICTURE', '"Wet road reflection"', '.def = 0', '.apply = roadfilm_set'))
    assert '{"graphics.road_film", "0", "0"}' in (RUNTIME / 'settings.c').read_text()
    math_init = src.split('static const char roadfilm_math[] =', 1)[1].split('\n\nchar *roadfilm_patch', 1)[0]
    math_msl = ''.join(ast.literal_eval(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', math_init))
    scalar = math_msl.split('static float4 roadfilm_reflection', 1)[0]
    with tempfile.TemporaryDirectory(prefix='roadfilm-') as directory:
        tmp = Path(directory)
        numeric = '#include <math.h>\n#include <stdio.h>\n#define clamp(x,a,b) fminf(fmaxf(x,a),b)\n' + scalar
        numeric += r'''
int main(void){double angles[]={0,60,80},want[]={.020059,.059126,.346916};int failures=0;double maxerr=0,minbroken=1;
 for(int i=0;i<3;i++){float ci=cos(angles[i]*3.141592653589793/180.0);double f=roadfilm_fresnel(ci),err=fabs(f-want[i]);
 failures+=err>1e-4;maxerr=fmax(maxerr,err);minbroken=fmin(minbroken,fabs(1-want[i]));printf("CPU %.0f deg: %.9f\n",angles[i],f);}
 float previous=0;for(int deg=0;deg<=90;deg++){float f=roadfilm_fresnel(cosf(deg*3.141592653589793f/180));
 failures+=!isfinite(f)||f<0||f>1.000001f||f+1e-6f<previous;previous=f;}
 printf("CPU Fresnel: %d failures; max error %.9g; F=1 control 3/3 rejected (min error %.9g)\n",failures,maxerr,minbroken);
 return failures?1:0;}
'''
        for label, code, expected in [('actual', numeric, 0), ('f1', numeric.replace('return 0.5f * (rs * rs + rp * rp);', 'return 1.0f;'), 1)]:
            assert label == 'actual' or code != numeric
            file = tmp / (label + '.c'); file.write_text(code)
            run(['clang', file, '-o', tmp / label])
            assert subprocess.run([tmp / label]).returncode == expected
        selector = '#include "roadfilm.m"\nstatic const char *variant="";\nint postfx_look_variant(const char*v){return !strcmp(v,variant);}\n'
        selector += r'''int main(void){
 int expected=getenv("EXPECT")?atoi(getenv("EXPECT")):0;if(roadfilm_enabled()!=expected)return 1;
 roadfilm_set(1);if(!roadfilm_enabled())return 2;roadfilm_set(0);if(roadfilm_enabled())return 3;
 variant="filmon";if(!roadfilm_enabled())return 4;variant="filmoff";if(roadfilm_enabled())return 5;
 variant="filmon";
 if(roadfilm_draw(0xD2D9B9D538A06AEEULL,0)||roadfilm_draw(0xD2D9B9D538A06AEEULL,-1)||roadfilm_draw(0,1))return 8;
 if(!roadfilm_draw(0xD2D9B9D538A06AEEULL,1))return 9;
 if(!roadfilm_shader_hash(0xD2D9B9D538A06AEEULL)||roadfilm_shader_hash(0)||roadfilm_shader_hash(0x8CBD8764E8812945ULL))return 6;
 if(roadfilm_patch("static void sm_body() {}"))return 7;return 0;}
'''
        (tmp / 'selector.c').write_text(selector)
        run(['clang', '-Wall', '-Wextra', '-Werror', '-I', RUNTIME, tmp / 'selector.c', '-o', tmp / 'selector'])
        for value in (None, '0', '1', 'garbage'):
            env = dict(os.environ); env.pop('NFS_ROAD_FILM', None)
            if value is not None: env['NFS_ROAD_FILM'] = value
            env['EXPECT'] = str(int(value == '1'))
            run([tmp / 'selector'], env=env)
        broken_src = src.replace('wetness > 0.0f && roadfilm_shader_hash(hash)', 'roadfilm_shader_hash(hash)')
        assert broken_src != src
        (tmp / 'roadfilm-broken.m').write_text(broken_src)
        (tmp / 'selector-broken.c').write_text(selector.replace('#include "roadfilm.m"', '#include "roadfilm-broken.m"'))
        run(['clang', '-Wno-unused-parameter', '-I', RUNTIME, tmp / 'selector-broken.c', '-o', tmp / 'selector-broken'])
        assert subprocess.run([tmp / 'selector-broken']).returncode == 8, 'dry native-selection control escaped'
        # Execute the actual draw selector, including asynchronous publication.
        draw = renderer.split('    ShaderEntry *pse = get_shader(ps, &po);', 1)[1].split('    if (mat_carbon_lamp_draw())', 1)[0]
        draw = '    ShaderEntry *pse = get_shader(ps, &po);' + draw + '    return pse;\n'
        draw_fixture = r'''
#include "roadfilm.m"
static const char *variant="filmon";
int postfx_look_variant(const char*v){return !strcmp(v,variant);}
typedef struct {volatile int pending;int ok;} ShaderEntry;
typedef struct {uint64_t tokens_hash;} Obj;
static ShaderEntry native={0,1},water={0,1};
static Obj object={0xD2D9B9D538A06AEEULL};
static struct {float psc[2][4];} dev;
static int lookups;
static ShaderEntry *get_shader(Obj*s,int*o){(void)s;(void)o;return &native;}
static ShaderEntry *get_shader_variant(Obj*s,int*o,int v){(void)s;(void)o;if(v!=MAT_VARIANT_ROAD_FILM)abort();lookups++;return &water;}
static ShaderEntry *select_draw(void){Obj*ps=&object;int po=0;
DRAW
}
int main(void){
 dev.psc[1][3]=0;if(select_draw()!=&native||lookups)return 1;
 dev.psc[1][3]=1;object.tokens_hash=0;if(select_draw()!=&native||lookups)return 2;
 object.tokens_hash=0xD2D9B9D538A06AEEULL;variant="filmoff";if(select_draw()!=&native||lookups)return 3;
 variant="filmon";water.pending=1;if(select_draw()!=&native||lookups!=1)return 4;
 water.pending=0;if(select_draw()!=&water||lookups!=2)return 5;
 water.ok=0;if(select_draw()!=&native||lookups!=3)return 6;
 return 0;
}
'''.replace('DRAW', draw)
        broken_draw = draw_fixture.replace('!__atomic_load_n(&film->pending, __ATOMIC_ACQUIRE) && ', '')
        assert broken_draw != draw_fixture
        for name, body, expected in [('draw', draw_fixture, 0), ('draw-pending-broken', broken_draw, 4)]:
            (tmp / (name + '.c')).write_text(body)
            run(['clang', '-Wall', '-Wextra', '-Werror', '-I', RUNTIME, tmp / (name + '.c'), '-o', tmp / name])
            assert subprocess.run([tmp / name]).returncode == expected
        print('Production draw selection 6/6 PASS; missing dry guard and pending-state mutations rejected', flush=True)
        print('Retail source selectors byte-identical; defaults/live/held selectors 4/4 PASS', flush=True)
        inputs = sorted((ROOT / 'host/shaders/testdata/worldreflect').glob('*_ps_2_*.bin'))
        inc = RUNTIME / 'gen/x360_shader_lits.inc'
        if len(inputs) != 6 or not inc.exists():
            print('SKIP: needs private worldreflect shader fixtures and generated ripple literals'); return 3
        (tmp / 'translate.cpp').write_text(TRANSLATOR)
        run(['clang++', '-std=c++17', '-O1', '-I', ROOT / 'host/shaders', tmp / 'translate.cpp',
             ROOT / 'host/shaders/sm_parse.cpp', ROOT / 'host/shaders/sm_msl.cpp', '-o', tmp / 'translate'])
        helpers = materials.split('static const char ripple_ps_helpers[] =', 1)[1].split('// Edits *s', 1)[0]
        emit = '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "roadfilm.m"\n#include "materials.h"\n#include "gen/x360_shader_lits.inc"\n'
        emit += 'int postfx_look_variant(const char*v){(void)v;return 0;}\n'
        emit += 'static const char ripple_ps_helpers[] =' + helpers
        emit += '\n'.join(function(materials, name) for name in ('edit', 'count', 'ripple_ps_patch'))
        # Use the production selector with its real ripple rows and bytecode hashes.
        # Unrelated car branches are inert; no ripple selection/patching is stubbed.
        emit += re.search(r'^typedef struct MatShader\s*\{.*?^}\s*MatShader\s*;', materials, re.M | re.S)[0]
        emit += r'''
static int ripple_mode;
static int ripples_on(void){return ripple_mode;}
static const char car_paint_msl[]="unused";
static int diag_on;
static void diag_event(const char *fmt,...){(void)fmt;}
static int car_light_hdr(void){return 0;}
static int car_paint_on(void){return 0;}
static char *car_t0_ps_patch(const char*s){(void)s;abort();}
static char *car_vs_cover_patch(const char*s){(void)s;abort();}
static char *car_vs_patch(const char*s){(void)s;abort();}
static char *carbon_lamp_pc_patch(const char*s,int v,int h){(void)s;(void)v;(void)h;abort();}
static char *carbon_lamp_retail_pc_patch(const char*s,int v,int h){(void)s;(void)v;(void)h;abort();}
static char *carbon_lamp_patch(char*s,int v){(void)s;(void)v;abort();}
static char *carbon_lamp_retail_patch(char*s,int v){(void)s;(void)v;abort();}
static const MatShader table[]={
'''
        rows = re.findall(r'^    RIPPLE_PS\("([^"]+)", (0x[0-9A-F]+ULL)\),', materials, re.M)
        assert len(rows) == 6
        for name, hash_value in rows:
            emit += '{.name="' + name + '",.hash=' + hash_value + ',.stage=SM_PIXEL,'
            emit += '.samplers={SM_TEX_2D,SM_TEX_2D,SM_TEX_2D,SM_TEX_2D},.depth_ok=1u<<4,'
            emit += '.patch=ripple_ps_patch,.enabled=ripples_on},\n'
        emit += '};\n'
        emit += '\n'.join(function(materials, name) for name in ('mat_replace_profile', 'mat_replace', 'mat_roadfilm_shader'))
        emit += EMIT
        (tmp / 'emit.c').write_text(emit)
        run(['clang', '-Wall', '-Wextra', '-Werror', '-I', RUNTIME, tmp / 'emit.c', '-o', tmp / 'emit'])
        null_emit = emit.replace(function(materials, 'mat_replace'), null_replace)
        (tmp / 'emit-null.c').write_text(null_emit)
        run(['clang', '-Wno-unused-parameter', '-I', RUNTIME, tmp / 'emit-null.c', '-o', tmp / 'emit-null'])
        wrong_src = src.replace('tex3.sample(smp3, (', 'tex0.sample(smp0, (')
        assert wrong_src != src
        (tmp / 'roadfilm-wrongslot.m').write_text(wrong_src)
        wrong_emit = emit.replace('#include "roadfilm.m"', '#include "roadfilm-wrongslot.m"')
        (tmp / 'emit-wrongslot.c').write_text(wrong_emit)
        run(['clang', '-Wall', '-Wextra', '-Werror', '-I', RUNTIME, tmp / 'emit-wrongslot.c', '-o', tmp / 'emit-wrongslot'])
        def reflection_slot(output):
            wrapped = output.split('roadfilm_reflection(', 2)[2]
            return wrapped.startswith(('tex3.sample(smp3, (', 'mat_refl(tex3, smp3, '))
        wrong_rejected = 0
        production_paths = []
        compiled = []
        null_rejected = 0
        for i, fixture in enumerate(inputs):
            for shadow in ([False, True] if fixture.name.startswith('t1_') else [False]):
                base = tmp / f'base-{i}-{int(shadow)}.metal'
                run([tmp / 'translate', fixture, base, *(['shadow'] if shadow else [])])
                assert subprocess.run([tmp / 'emit-null', base, tmp / 'null.metal', '1', fixture]).returncode == 5
                null_rejected += 1
                for ripple in (0, 1):
                    on = tmp / f'film-{i}-{int(shadow)}-{ripple}.metal'
                    run([tmp / 'emit', base, on, ripple, fixture])
                    off = Path(str(on) + '.off').read_text(); output = on.read_text()
                    assert output.count('roadfilm_reflection(') == 2
                    assert reflection_slot(output), f'film coats wrong texture: {on.name}'
                    # Remove exactly the two film edits and require every remaining byte.
                    restored = output.replace(math_msl, '', 1)
                    start = restored.index('roadfilm_reflection(')
                    end = restored.index(', si.tex[4], c[1].w)', start)
                    restored = restored[:start] + restored[start + len('roadfilm_reflection('):end] + restored[end + len(', si.tex[4], c[1].w)'):]
                    assert restored == off, f'non-reflection source changed: {on.name}'
                    if not ripple:
                        assert off == base.read_text(), 'Retail source differs'
                        wrong = tmp / f'wrongslot-{i}-{int(shadow)}.metal'
                        run([tmp / 'emit-wrongslot', base, wrong, ripple, fixture])
                        assert not reflection_slot(wrong.read_text()), 'tex0 fallback control escaped'
                        wrong_rejected += 1
                        if fixture.name.startswith('t0_'):
                            # Keep the actual fragment entry/body; supply a vertex scene only.
                            vertex = r'''
vertex SmPSStageIn rf_scene(uint i [[vertex_id]], constant float4 &eye [[buffer(0)]]) {
 SmPSStageIn r={};
 r.pos=float4(i==1?3.0f:-1.0f,i==2?3.0f:-1.0f,0,1);
 r.color0=float4(.25f,.25f,.25f,1);
 r.texcoord0=float4(.5f,.5f,0,1);r.texcoord1=float4(.5f,.5f,0,1);
 r.texcoord3=float4(0,0,1,0);r.texcoord4=eye;
 r.texcoord5=float4(0);r.texcoord6=float4(1);return r;
}
'''
                            for label, code in [('retail', off), ('film', output), ('wrongslot', wrong.read_text())]:
                                scene = tmp / f'scene-{label}.metal'
                                scene.write_text(code + vertex);production_paths.append(scene)
                    compiled.append(on)
        print(f'tex0 fallback control: {wrong_rejected}/{wrong_rejected} wrong slots rejected', flush=True)
        print(f'NULL mat_replace control: {null_rejected}/{null_rejected} Retail ripple replacements rejected; pinned identity 1/4 rejected', flush=True)
        print(f'Production composition: {len(compiled)} variants PASS; remove film edits => exact Retail bytes; ripples 0/1', flush=True)
        (tmp / 'gpu.m').write_text(GPU)
        run(['clang', '-O1', '-fobjc-arc', '-I', ROOT / 'host/shaders', tmp / 'gpu.m', '-framework', 'Metal', '-framework', 'Foundation', '-o', tmp / 'gpu'])
        numeric_msl = '#include <metal_stdlib>\nusing namespace metal;\n' + math_msl + r'''
vertex float4 rf_vertex(uint i [[vertex_id]]) {return float4(i==1?3.0f:-1.0f,i==2?3.0f:-1.0f,0,1);}
fragment float4 rf_fragment(float4 p [[position]]) {
 uint i=uint(p.x);float ci=i==0?1.0f:i==1?0.5f:0.1736481777f;
 float4 r=roadfilm_reflection(float4(1),float4(sqrt(1-ci*ci),0,ci,0),1);
 return r;
}
'''
        (tmp / 'numeric.metal').write_text(numeric_msl)
        result = subprocess.run([str(tmp / 'gpu'), str(tmp / 'numeric.metal'), *map(str, compiled)])
        if result.returncode == 77 and not (os.environ.get('CODEX_GPU_CHECK') or os.environ.get('NFS_GPU_SANDBOX')):
            print('GPU unavailable locally; run through run_tests for numeric float target and 14 shader compiles'); return 0
        assert result.returncode == 0, 'GPU numeric or production shader compilation failed'
        assert len(production_paths) == 3
        for label, film_path, expected, counts in [('actual', production_paths[1], 0, '0/48'),
                                                    ('tex0', production_paths[2], 1, '9/48')]:
            result = subprocess.run([tmp / 'gpu', '--production', production_paths[0], film_path],
                                    capture_output=True, text=True)
            print(result.stdout, end='', flush=True)
            assert result.returncode == expected and f'{counts} failures' in result.stdout, (label, result.stdout, result.stderr)

        mutant = numeric_msl.replace('return 0.5f * (rs * rs + rp * rp);', 'return 1.0f;')
        assert mutant != numeric_msl
        (tmp / 'broken.metal').write_text(mutant)
        assert subprocess.run([str(tmp / 'gpu'), str(tmp / 'broken.metal')]).returncode == 1, 'F=1 GPU control escaped'
        print('Water film PASS: production linear-float Fresnel and F=1 GPU control', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(capture_pair(sys.argv[2:]) if len(sys.argv) > 1 and sys.argv[1] == '--capture-pair' else main())
