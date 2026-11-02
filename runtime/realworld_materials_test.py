"""Production glass/chrome source and analytical fixtures; no game launches.
Automotive head-on regression: original rear/windscreen alpha=.10123384/.10123384;
required rear >=.65, windscreen .20-.35; fixed .80/.25 (coverage=1).
The original production pane/alpha expression fails both checks (2 failures).
Tolerances: F0 1e-5, pane R/T 1e-4, energy 1+1e-5, GGX FWHM 10%.
The old boosted glass is R=.144/T=.60, chrome R=.50/direct mask=0.
Wrong F0, wrong absorption, double-alpha and wrong-role controls must fail.
Both vertex layouts and all six pixel techniques are generated and GPU compiled.
Observed CPU F0 glass=.043249186/Cr=.554535985; 0 numeric failures.
Wrong-k control F0=.272139370, 2 failures; absorption control 3 failures.
GGX FWHM=.015479401 versus .015446579, relative error .213%.
24 production GPU libraries pass; wrong-F0 and masked-specular controls fail.
Seven native source identities pass; changing a byte rejects the native pin.
Decoded sampler layouts: t0 cube/2D; t1-t5 2D/cube/2D/2D.
22 pixel variants accepted (12 fixture/role pairs plus 10 depth variants).
Old-guard mutant: t0 glass/chrome rejected (2), t1-t5 accepted (20).
Unknown-layout controls: 116 rejections; original guard exits 4 on t0 glass.
Rim regression at 70254116: all 11 chrome pixel variants fail (18 RGB
assertions each); white and gold both output (.554536,.554536,.554536).
Fixed neutral-IBL coat: white (1,1,1), gold (1,.664,.136), tolerance 2e-5;
inputs (1,1,1)/(1,.65,.1), dielectric F0=.04. Lost-tint control must fail.
Part-mask regression on the texture-wide implementation: all 11 chrome variants
fail, 24 RGB assertions each; fixed target: 0. Masked white/gold coat,
unmasked Cr F0=.554535985, half-mask average, tolerance 2e-5.
P8 blue high-nibble fixtures use bytes 15/240 to reject wrong-channel/scaling.
16 mode/car/colour cases plus slot reuse/failure/bare/unknown/missing-mask and P8 controls pass.
Pane regression: 82 connected material sections / 3,047 base LOD A triangles.
Counts below are front/rear/side/roof panes (triangles):
Corvette 2/2/2/0 (114/160/62/0); Brera 2/2/6/2 (102/100/98/78).
COPSPORT 2/2/0/0 (62/82/0/0); GTI 2/2/6/0 (128/184/154/0).
911GT2 2/2/6/0 (156/176/174/0); 240SX 2/2/6/0 (122/100/208/0).
Murcielago640 2/1/6/9 (174/4/146/87); Zonda 2/2/6/2 (118/62/116/80).
Frozen preceding classifier: 24 mixed panes / 505 wrong triangles -> 0 / 0.
Brera roof: 26 front / 33 rear / 0 side / 19 mixed triangles -> 78 roof.
Quarter-window errors: 911GT2 38/142, 240SX 78/156, Murcielago640 86/112,
Zonda 58/64 -> zero each. Expected roles use parts/positions, not normals.
Both index widths and 45 barycentric samples per triangle retain one pane VLT.
Thirteen up-facing sections use .10 roof VLT; added assets missing -> exit 3.
GPU front/rear/side/roof fixtures use both materials and interior-view flips.
Area-weighted seam: 0 wrong vertices; equal-weight mutant: 6 wrong vertices.
LOD A-D: 310 sections / 6,304 triangles; frozen round-2 159 mismatches -> 0.
Buffer-generation cache: 1,000 draws scan/classify once; frozen scan count 1,000.
Writable lock/unlock, read-only lock, topology, reuse and release controls pass.
Both GPU vertex layouts: 24 offset cases, 0 errors; lost-base mutant: 8 errors.
Unshadowed resource regression: original glass/chrome declarations disagree
with retail t0 in 8 texture/sampler bindings; corrected declarations: 0.
Compiled GPU texture-type mismatches: 2 per original t0 variant, corrected 0.
BMWM3GTR / BMWM3GTRE46 LOD A-D: 80 sections / 1,556 triangles, 0 wrong VLTs.
LOD A front/rear/side: 114/108/132 and 114/108/136 triangles; VLT .75/.20/.70.
Native t0 cube s0 / diffuse s1 GPU draws replace the old shadowed-layout fixture.
The entire suite discovers this file through runtime/*_test.py.
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
RT = ROOT / 'runtime'
SRC = (RT / 'materials.m').read_text()
MODULE_PATH = Path(os.environ.get('REALWORLD_MATERIALS_SOURCE', RT / 'realworld_materials.c'))
MODULE = MODULE_PATH.read_text()


def function(s, name):
    m = re.search(r'^(?:static )?[^\n]*\b' + name + r'\([^{};]*?\) \{', s, re.M)
    assert m, name
    start = s.index('{', m.start()); depth = 0
    for t in re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|[{}]', s[start:], re.S):
        if t[0] == '{': depth += 1
        elif t[0] == '}':
            depth -= 1
            if not depth: return s[m.start():start+t.end()]
    raise AssertionError(name)


def initializer(s, name):
    m = re.search(r'static const char ' + name + r'\[\]\s*=\s*(.*?);\s*\n', s, re.S)
    assert m, name
    return m[1] + ';\n'


def literal(s, name):
    return ''.join(ast.literal_eval(t) for t in re.findall(r'"(?:\\.|[^"\\])*"', initializer(s, name)))


def t0_binding_errors(source):
    # The renderer uses the translated retail reflection, not replacement MSL.
    # Include sampler registers: swapping textures alone samples the wrong state.
    errors = 0
    for name, slot in (('tex0', 1), ('tex1', 0), ('smp0', 1), ('smp1', 0)):
        kind = 'texture' if name.startswith('tex') else 'sampler'
        match = re.search(r'\b' + name + r' \[\[' + kind + r'\((\d+)\)\]\]', source)
        errors += match is None or int(match[1]) != slot
    return errors


def glass_regression(tmp, optics, module):
    # Execute the production pane/alpha expression, including its normal and mask inputs.
    tail = function(module, 'realworld_materials_pixel')
    shader = ''.join(ast.literal_eval(t) for t in re.findall(r'"(?:\\.|[^"\\])*"', tail))
    glass = shader.split('#else\n')[-1].split('    float3 reflected=')[0]
    main = r"""
static float alpha(float nx, float side, float coverage) {
 struct {struct {float w;} texcoord4,texcoord3;} pi={{side>.5f?.70f:nx<0?.20f:.75f},{0}};
 struct {simd_float4 k[3];} m={}; m.k[2][2]=side;
 float nv=1;
""" + glass + r"""
 return alpha;
}
int main(){
 float rear=alpha(-.25f,0,1), front=alpha(.40f,0,1), side=alpha(0,1,1);
 int errors=(rear<.65f)+(front<.20f || front>.35f);
 if(!errors){
  errors+=fabs(rear-.80f)>1e-5 || fabs(front-.25f)>1e-5 || fabs(side-.30f)>1e-5;
  errors+=fabs(alpha(-.25f,0,.5f)-.40f)>1e-5;
  for(float ci : {1.f,.5f,.1736481777f,0.f}) {
   float2 clear=rw_pane(ci);
   for(float nx : {-.25f,.40f}) {
    float2 glass=rw_glass(ci,nx<0?.20f:.75f);
    errors+=fabs(glass.x-clear.x)>1e-6 || glass.y<0 || glass.x+glass.y>1.00001f;
   }
  }
 }
 printf("automotive rear alpha=%.9f (>=.65) windscreen=%.9f (.20-.35) failures=%d\n",rear,front,errors);
 return errors?1:0;
}
"""
    if 'static float2 rw_glass(' not in optics:
        main = main[:main.index(' if(!errors){')] + main[main.index(' printf("automotive'): ]
    (tmp/'glass.cpp').write_text(CPU+optics+main)
    run(['clang++','-std=c++17',tmp/'glass.cpp','-o',tmp/'glass'])
    return subprocess.run([tmp/'glass']).returncode


def run(args, **kw):
    return subprocess.run(list(map(str, args)), check=True, **kw)


# Frozen at the reviewed base, never read a moving branch during the check.
PINNED = {'car_paint_msl': '1b14bb9b0d6b5495b4cb3c4701337a52797109c9c7a7cfe8a667183c4a3c88a7', 'mat_replace': '090cc8b487bfc4667782ada08b4f0757ce78ae0f3f053d538510d3e8ea50a45c', 'mat_replace_profile': '2934e2540fbb90d18704ceff64e1988564a5f46979869d9d42c0f60092b51670', 'car_vs_patch': '4b6bd0861f07ce6f90635f80c349de728b2d3e6a8b4b2aa41a8aeaac35afe29a', 'car_vs_nm_patch': '2cc8e58cb6fe6cf7e31681ff835d43e7c47949afba555aed630776fd7a955bb8', 'car_vs_cover_patch': '18783835712d837f8fa6b008e3163ce023a4fc7eca75b01fa22d0db140ae466f', 'car_t0_ps_patch': '2d3ebd34e24953de29673b1c094b5802c68ce8218262cc29f1f3c9ce29fbf316'}

CPU = r'''
#include <simd/simd.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
using float3=simd_float3;
using std::abs;
struct float2 {float x,y;float2(float a,float b):x(a),y(b){}};
static float clamp(float x,float a,float b){return std::clamp(x,a,b);}
static float saturate(float x){return clamp(x,0,1);}
static float dot(float3 a,float3 b){return simd_dot(a,b);}
static float3 cross(float3 a,float3 b){return simd_cross(a,b);}
static float3 normalize(float3 x){return simd_normalize(x);}
using std::max;
'''
CPU_MAIN = r'''
int main(){int errors=0;float angles[]={1,.5f,.1736481777f};
 double R[]={.081346,.1669926,.552676},T[]={.898766,.808937,.421764};
 for(int i=0;i<3;++i){float2 rt=rw_pane(angles[i]);
  errors+=fabs(rt.x-R[i])>1e-4 || fabs(rt.y-T[i])>1e-4;
  errors+=rt.x+rt.y>1.00001;
  printf("pane cos=%.9f R=%.9f T=%.9f legacy R=.144 T=.60\n",angles[i],rt.x,rt.y);
 }
 errors+=fabs(rw_surface(1)-.0432492)>1e-5;
 errors+=fabs(rw_conductor(1)-.554536)>1e-5;
 float3 n={0,0,1}; float direct=rw_direct(n,n,n);
 errors+=fabs(direct-(.554536/(4*3.141592653589793*.000036)))>.1;
 // Held-out width: solve half maximum in the actual GGX lobe at normal view.
 double lo=0,hi=.1,peak=direct;
 for(int j=0;j<40;j++){double a=(lo+hi)/2;float3 l={(float)sin(a),0,(float)cos(a)};
  if(rw_direct(n,n,l)>peak*.5)lo=a;else hi=a;}
 double width=lo+hi;
 double oracle=4*asin(.006*sqrt((sqrt(2.)-1)/(1-.000036)));
 errors+=fabs(width/oracle-1)>.1;
 printf("F0 glass=%.9f chrome=%.9f; direct=%.6f; GGX FWHM=%.9f oracle=%.9f errors=%d\n",rw_surface(1),rw_conductor(1),direct,width,oracle,errors);
 return errors?1:0;}
'''

EMIT_MAIN = r'''
static uint32_t namehash(const char*s){uint32_t h=~0u;while(*s)h=h*33+(uint8_t)*s++;return h;}
int x360_post_rolloff(void){return 0;}
int main(int argc,char**argv){
 if(argc==1){int failures=0;
  int expected=atoi(getenv("EXPECT")?getenv("EXPECT"):"0");failures+=realworld_materials_mode()!=expected;
  for(int mode=0;mode<4;++mode){realworld_materials_set(mode);
   const char*names[]={"WINDSHIELD","WINDOWMASK","CHROME","MAGCHROME","USER_RIMS_CHROME","MOLDINGS","HEADLIGHTGLASS","CLEARPLASTIC","WINDSHIELD_TINT_L1_BLACK","BRAKEDISC","USER_RIMS"};
   for(int i=0;i<11;i++)for(int car=0;car<2;car++){
    int want=car?(i<2 && (mode&1)?1:i>=2 && i<5 && (mode&2)?2:0):0;
    failures+=realworld_materials_role(namehash(names[i]),car,mode,0)!=want;
   }
  }
  realworld_materials_set(3);realworld_materials_capture(namehash("CHROME"),1,0);
  failures+=realworld_materials_draw(0x89E9A52CCEE09BCFULL,0x1BEA38904139377AULL)!=MAT_VARIANT_REAL_CHROME;
  failures+=realworld_materials_draw(0x89E9A52CCEE09BCFULL,0xB1CE127C28045F2CULL)!=0;
  realworld_materials_capture(0,0,0);failures+=realworld_materials_draw(0x89E9A52CCEE09BCFULL,0x1BEA38904139377AULL)!=0;
  float vsc[256][4]={{0}};MatConsts out={0};
  for(int i=0;i<256;++i)vsc[i][0]=(float)i;
  realworld_materials_constants(MAT_REAL_T0,vsc,&out);
  failures+=out.k[1][0]!=7 || out.k[1][1]!=8 || out.k[1][2]!=9;
  realworld_materials_constants(MAT_REAL_SHADOWED,vsc,&out);
  failures+=out.k[1][0]!=11 || out.k[1][1]!=12 || out.k[1][2]!=13;
  realworld_materials_capture(namehash("WINDOWMASK"),1,0);
  realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][2]!=1;
  realworld_materials_capture(namehash("WINDSHIELD"),1,0);
  realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][2]!=0;
  realworld_materials_capture(namehash("CHROME"),1,0);
  realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][2]!=0;
  return failures?1:0;
 }
 int vertex=argc==5;const char*tokenpath=vertex?argv[4]:argv[6];
 FILE*f=fopen(tokenpath,"rb");fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);uint32_t*tokens=malloc(bytes);fread(tokens,1,bytes,f);fclose(f);
 SmOptions options;sm_default_options(&options);
 if(!vertex && atoi(argv[4]))options.shadow_samplers=1u<<2;
 SmResult refl={0};if(sm_translate(tokens,(uint32_t)bytes/4,&options,&refl))return 3;
 const char*reject=getenv("REJECT_LAYOUT");
 if(!vertex && reject){
  if(!strcmp(reject,"type"))refl.samplers[atoi(getenv("BAD_SAMPLER"))].type=SM_TEX_3D;
  else if(!strcmp(reject,"missing"))--refl.n_samplers;
  else if(!strcmp(reject,"extra")){refl.samplers[refl.n_samplers]=(SmSampler){.reg=4,.type=SM_TEX_2D};++refl.n_samplers;}
  else if(!strcmp(reject,"duplicate"))refl.samplers[1].reg=refl.samplers[0].reg;
  else if(!strcmp(reject,"register"))refl.samplers[0].reg=15;
  else if(!strcmp(reject,"shadow"))refl.samplers[0].shadow=1;
  else if(!strcmp(reject,"swapped")){int t=refl.samplers[0].type;refl.samplers[0].type=refl.samplers[1].type;refl.samplers[1].type=t;}
  else return 6;
 }
 int id=0;char*out=mat_realworld_shader(tokens,(uint32_t)bytes/4,&refl,&id,vertex?0:atoi(argv[5]));
 sm_result_free(&refl);
 if(!vertex && reject){int failed=out!=NULL || id!=0;free(out);free(tokens);return failed?5:0;}
 if(!vertex && id!=(atoi(argv[3])?MAT_REAL_SHADOWED:MAT_REAL_T0))return 4;
 free(tokens);
 if(!out)return 2;f=fopen(argv[2],"w");fputs(out,f);fclose(f);free(out);return 0;
}
'''

GPU = r'''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <initializer_list>
static const char *scene = R"MSL(
vertex In rw_scene(uint id [[vertex_id]],constant float4 &f [[buffer(0)]]) {
 float2 xy=id==0?float2(-1,-1):id==1?float2(3,-1):float2(-1,3);
 In o; o.pos=float4(xy,0,1);o.color0=float4(.7f,.7f,.7f,.4f);o.color1=float4(0);
 o.texcoord0=float4(0,0,1,0);o.texcoord1=float4(f.w, f.w==.5f?.5f:.25f,0,0);o.texcoord2=float4(0,0,0,1);
 o.texcoord3=float4(0,0,f.z>0.5f?-1:1,f.z>2.5f?.815f:f.z>1.5f?.9f:0);
 o.texcoord4=float4(0,0,1,f.z>2.5f?.10f:f.z>1.5f?.70f:f.z>0.5f?.20f:.75f);
 o.texcoord5=float4(sqrt(1-f.x*f.x),0,f.y<0?-f.x:f.x,0);o.texcoord6=float4(0,0,0,1);return o;
}
)MSL";
static int production(id<MTLDevice>d,NSString*source,const char*path){
 bool unshadowed=[source containsString:@"#define MAT_VARIANT 0\n"];
 NSError*err=nil;NSString*s=[source stringByAppendingString:@(scene)];
 id<MTLLibrary>lib=[d newLibraryWithSource:s options:nil error:&err];
 if(!lib){fprintf(stderr,"scene %s: %s\n",path,err.description.UTF8String);return 2;}
 MTLRenderPipelineDescriptor*pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"rw_scene"];
 pd.fragmentFunction=[lib newFunctionWithName:@"sm_main"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 MTLRenderPipelineReflection*reflection=nil;
 id<MTLRenderPipelineState>pipe=[d newRenderPipelineStateWithDescriptor:pd options:MTLPipelineOptionArgumentInfo reflection:&reflection error:&err];
 if(!pipe){fprintf(stderr,"pipeline: %s\n",err.description.UTF8String);return 2;}
 // Reject mismatches before issuing an invalid draw with the retail bindings.
 int resource_errors=0,resources=0;
 for(MTLArgument*argument in reflection.fragmentArguments){
  if(argument.type!=MTLArgumentTypeTexture || argument.index>1)continue;
  bool cube=argument.index==(unshadowed?0:1);
  resource_errors+=argument.textureType!=(cube?MTLTextureTypeCube:MTLTextureType2D);++resources;
 }
 if(resource_errors || resources!=2){fprintf(stderr,"native texture layout %s: mismatches=%d resources=%d\n",path,resource_errors,resources);return 1;}
 MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;
 id<MTLTexture>tex=[d newTextureWithDescriptor:td],shadow=[d newTextureWithDescriptor:td];
 td.width=td.height=2;id<MTLTexture>mask=[d newTextureWithDescriptor:td];td.width=td.height=1;
 // BGRA mask byte +1 (green) for 32-bit; palette byte +0 high nibble (blue) for P8.
 float maskPixels[16]={1,0,240.f/255,1, 0,1,15.f/255,1, 0,1,15.f/255,1, 1,0,240.f/255,1};
 [mask replaceRegion:MTLRegionMake2D(0,0,2,2) mipmapLevel:0 withBytes:maskPixels bytesPerRow:32];
 float pixel[4]={1,1,1,1},zero[4]={0};[shadow replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:zero bytesPerRow:16];
 td.textureType=MTLTextureTypeCube;id<MTLTexture>cube=[d newTextureWithDescriptor:td];
 td.textureType=MTLTextureType2D;td.usage=MTLTextureUsageRenderTarget;id<MTLTexture>out=[d newTextureWithDescriptor:td];
 td.pixelFormat=MTLPixelFormatDepth32Float;td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture>depth=[d newTextureWithDescriptor:td];
 MTLSamplerDescriptor*sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;
 sd.compareFunction=MTLCompareFunctionLessEqual;id<MTLSamplerState>sam=[d newSamplerStateWithDescriptor:sd];
 id<MTLCommandQueue>q=[d newCommandQueue];int failures=0;bool chrome=[source containsString:@"#define RW_CHROME 1"],hw=[source containsString:@"#define MAT_SHADOW_HW 1"];
 for(int tint=0;tint<(chrome?10:9);tint++)
 for(int light=0;light<2;light++)for(int angle=0;angle<3;angle++)for(int half=0;half<2;half++){
  if(chrome && tint && (light || angle))continue;
  pixel[0]=1;pixel[1]=(tint==2||tint>=4)?.65f:1;pixel[2]=(tint==2||tint>=4)?.1f:1;
  if(light && angle)continue;
  float ci=angle==0?1:angle==1?.5f:.1736481777f,cov=half?.5f:1;
  pixel[3]=cov;[tex replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:pixel bytesPerRow:16];
  for(int face=0;face<6;face++){float env[4]={light?0.f:1.f,light?0.f:1.f,light?0.f:1.f,1};
   [cube replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:face withBytes:env bytesPerRow:16 bytesPerImage:16];}
  float constants[256][4]={{0}},mk[12][4]={{0}},aux[68]={0};constants[0][0]=1;mk[1][0]=mk[1][1]=mk[1][2]=light?.001f:0;
  mk[2][0]=chrome&&tint?1:0;mk[2][1]=tint>=7?1:0;mk[2][2]=!chrome&&(tint==2||tint==3||tint==5||tint==6)?1:0;
  float uv=tint>=4?((tint-4)%3==0?.25f:(tint-4)%3==1?.75f:.5f):.25f;
  // Separate solid-mask fixtures preserve the original white/gold oracle.
  float fullMask[16];for(int j=0;j<16;j++)fullMask[j]=1;
  [mask replaceRegion:MTLRegionMake2D(0,0,2,2) mipmapLevel:0 withBytes:tint>=4?maskPixels:fullMask bytesPerRow:32];
  float fixture[4]={ci,!chrome&&light?-cov:cov,chrome?(tint==3?1.f:0.f):float(tint==8?3:tint==0||tint==4||tint==5?0:tint==1||tint==6?1:2),uv};MTLRenderPassDescriptor*rp=[MTLRenderPassDescriptor renderPassDescriptor];
  rp.colorAttachments[0].texture=out;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
  id<MTLCommandBuffer>cb=[q commandBuffer];id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];
  [e setRenderPipelineState:pipe];[e setVertexBytes:fixture length:16 atIndex:0];
  [e setFragmentBytes:constants length:sizeof constants atIndex:0];[e setFragmentBytes:aux length:sizeof aux atIndex:3];[e setFragmentBytes:mk length:sizeof mk atIndex:4];
  // Bind the retail technique's registers, independently of replacement source.
  [e setFragmentTexture:tex atIndex:unshadowed?1:0];[e setFragmentTexture:cube atIndex:unshadowed?0:1];[e setFragmentTexture:hw?depth:shadow atIndex:2];[e setFragmentTexture:tex atIndex:3];[e setFragmentTexture:mask atIndex:15];
  for(int j=0;j<4;j++)[e setFragmentSamplerState:sam atIndex:j];
  [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
  if(cb.status==MTLCommandBufferStatusError)return 2;
  float v[4];[out getBytes:v bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
  double rs[]={.081346,.1669926,.552676},ts[]={.898766,.808937,.421764};
  if(!chrome){
   double ct=sqrt(1-(1-ci*ci)/(1.525139*1.525139));
   ts[angle]*=pow((tint==8?.10:tint==0||tint==4||tint==5?.75:tint==1||tint==6?.20:.70)/.89876616,1/ct);
  }
  if(chrome&&tint){
   float weight=tint<4?1:((tint-4)%3==2?.5f:float(((tint-4)%3)^(tint>=7)));
   // Opposite green/blue masks catch both the channel and the palette decode.
   for(int channel=0;channel<3;channel++){
    float expected=(1-weight)*.554535985f+weight*(.04f+.96f*pixel[channel]);
    failures+=!isfinite(v[channel])||fabs(v[channel]-expected)>2e-5;
   }
   failures+=fabs(v[3]-cov)>1e-6;
   if(!half && tint<4)printf("rim %s RGB %.6f %.6f %.6f expected %.6f %.6f %.6f\n",tint==1?"white":tint==2?"gold":"opposite-light",v[0],v[1],v[2],.04+.96*pixel[0],.04+.96*pixel[1],.04+.96*pixel[2]);
  }else if(chrome){
   if(light)failures+=fabs(v[0]-1.2257936)>1e-4;
   else if(angle==0)failures+=fabs(v[0]-.554536)>1e-5;
   failures+=fabs(v[3]-cov)>1e-6 || !isfinite(v[0]);
  }else{
   // The back view flips the shading normal, never the car-space pane class.
   failures+=fabs(v[0]*v[3]-(light?0:cov*rs[angle]))>1e-4;
   failures+=fabs(v[3]-cov*(1-ts[angle]))>1e-4;
  }
 }
 if(failures)fprintf(stderr,"production %s failures=%d\n",path,failures);
 return failures?1:0;
}

static int pane_vertex(id<MTLDevice>d,NSString*source){
 NSString*s=[source stringByReplacingOccurrencesOfString:@"    r.pos = so.pos;" withString:
 @"    uint local=rwID-rwBase; r.pos=float4(local==0?float2(-1,-1):local==1?float2(3,-1):float2(-1,3),0,1);"];
 s=[s stringByAppendingString:@"\nstruct PaneProbeIn {float4 pos [[position]];float4 tint [[user(texcoord4)]];}; fragment float4 pane_probe(PaneProbeIn i [[stage_in]]) { return float4(i.tint.w); }\n"];
 NSError*err=nil;id<MTLLibrary>lib=[d newLibraryWithSource:s options:nil error:&err];
 if(!lib){fprintf(stderr,"pane vertex: %s\n",err.description.UTF8String);return 2;}
 MTLRenderPipelineDescriptor*pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[lib newFunctionWithName:@"sm_main"];pd.fragmentFunction=[lib newFunctionWithName:@"pane_probe"];
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 MTLVertexDescriptor*vd=[MTLVertexDescriptor vertexDescriptor];
 for(int i=0;i<16;++i){vd.attributes[i].format=MTLVertexFormatFloat4;vd.attributes[i].offset=16*i;vd.attributes[i].bufferIndex=0;}
 vd.layouts[0].stride=256;pd.vertexDescriptor=vd;
 id<MTLRenderPipelineState>pipe=[d newRenderPipelineStateWithDescriptor:pd error:&err];
 if(!pipe){fprintf(stderr,"pane pipeline: %s\n",err.description.UTF8String);return 2;}
 MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;id<MTLTexture>out=[d newTextureWithDescriptor:td];
 id<MTLBuffer>vertices=[d newBufferWithLength:8*256 options:MTLResourceStorageModeShared];memset(vertices.contents,0,8*256);
 id<MTLCommandQueue>q=[d newCommandQueue];int errors=0;
 for(int path=0;path<3;++path)for(float vlt : {.75f,.20f,.70f,.10f}){
  uint16_t indices[3];for(int i=0;i<3;++i)indices[i]=i+(path==0?3:path==1?0:6);
  int baseVertex=path==0?1:path==1?0:-2;uint32_t base=path==1?0:4;
  float values[16];for(float&v:values)v=-1;for(int i=0;i<3;++i)values[i]=vlt;
  id<MTLBuffer>ib=[d newBufferWithBytes:indices length:6 options:MTLResourceStorageModeShared];
  MTLRenderPassDescriptor*rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=out;
  rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
  id<MTLCommandBuffer>cb=[q commandBuffer];id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:rp];
  [e setRenderPipelineState:pipe];[e setVertexBuffer:vertices offset:0 atIndex:0];
  float zero[1024]={0};for(int slot=16;slot<=19;++slot)[e setVertexBytes:zero length:sizeof zero atIndex:slot];
  [e setVertexBytes:values length:sizeof values atIndex:20];[e setVertexBytes:&base length:4 atIndex:21];
  [e drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:3 indexType:MTLIndexTypeUInt16 indexBuffer:ib indexBufferOffset:0 instanceCount:1 baseVertex:baseVertex baseInstance:0];
  [e endEncoding];[cb commit];[cb waitUntilCompleted];if(cb.status==MTLCommandBufferStatusError)return 2;
  float got[4];[out getBytes:got bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
  errors+=!isfinite(got[0])||fabs(got[0]-vlt)>1e-6;
 }
 printf("pane vertex static/rebased/negative-base: 12 cases, errors=%d\n",errors);return errors ? (errors==8 ? 1 : 2) : 0;
}

int main(int argc,char**argv){@autoreleasepool{
 id<MTLDevice>d=MTLCreateSystemDefaultDevice();if(!d)return 77;
 NSError*err=nil;int failures=0;
 for(int i=1;i<argc;++i){NSString*s=[NSString stringWithContentsOfFile:@(argv[i]) encoding:NSUTF8StringEncoding error:&err];
  id<MTLLibrary>lib=[d newLibraryWithSource:s options:nil error:&err];
  if(!lib){fprintf(stderr,"%s: %s\n",argv[i],err.description.UTF8String);return 2;}
  id<MTLFunction>fn=[lib newFunctionWithName:@"rw_check"];
  if(!fn){if([s containsString:@"#define RW_CHROME"]) failures+=production(d,s,argv[i]);
   else if([s containsString:@"vertex SmVSStageOut sm_main("]){int result=pane_vertex(d,s);if(result==2)return 2;failures+=result;}continue;}
  id<MTLComputePipelineState>p=[d newComputePipelineStateWithFunction:fn error:&err];if(!p)return 2;
  id<MTLBuffer>b=[d newBufferWithLength:256 options:MTLResourceStorageModeShared];
  id<MTLCommandQueue>q=[d newCommandQueue];id<MTLCommandBuffer>cb=[q commandBuffer];id<MTLComputeCommandEncoder>e=[cb computeCommandEncoder];
  [e setComputePipelineState:p];[e setBuffer:b offset:0 atIndex:0];[e dispatchThreads:MTLSizeMake(3,1,1) threadsPerThreadgroup:MTLSizeMake(3,1,1)];[e endEncoding];[cb commit];[cb waitUntilCompleted];
  if(cb.status==MTLCommandBufferStatusError)return 2;
  float *v=(float *)b.contents;double r[]={.081346,.1669926,.552676},t[]={.898766,.808937,.421764};
  for(int j=0;j<3;j++){
   failures+=fabs(v[4*j]-r[j])>1e-4 || fabs(v[4*j+1]-t[j])>1e-4;
   failures+=v[4*j]+v[4*j+1]>1.00001;
   failures+=fabs(v[4*j+2]-.554536)>1e-5 || fabs(v[4*j+3]-.0432492)>1e-5;
  }
  printf("GPU optics R/T normal %.9f/%.9f grazing %.9f/%.9f F0 %.9f/%.9f failures=%d\n",v[0],v[1],v[8],v[9],v[2],v[3],failures);
 }
 printf("GPU libraries=%d failures=%d\n",argc-1,failures);return failures?1:0;
}}
'''


# Frozen per-vertex classifier from the preceding implementation.
PRE_PANE_VLT = 'static float rw_vlt(float nx, float ny, float side) {\n    // Car frame: +X front, -X rear, Y lateral. Ignore roofward slope.\n    // Measured LOD A lateral ratio <=.160, axial >=.205; leave a gap.\n    // Material fallback only for the gap or a near-vertical normal.\n    float vlt = side>0.5f ? 0.70f : nx<0.0f ? 0.20f : 0.75f;\n    float ax=abs(nx), horizontal=ax+abs(ny);\n    if (horizontal>1e-4f) {\n        if (ax<=0.17f*horizontal) vlt=0.70f;\n        else if (ax>=0.20f*horizontal) vlt=nx<0.0f ? 0.20f : 0.75f;\n    }\n    return vlt;\n}'


# Frozen round-2 routines: LOD B-D 159 wrong triangles; 1,000 draws scan 1,000 times.
PRE_LOD_CLASSIFIER = '#include "realworld_materials.h"\n#include "traffic_detail.h"\n#include "../host/shaders/sm_coverage.h"\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <math.h>\n\ntypedef struct { uint32_t parent, index; float p[3]; double sum[3]; } PaneVertex;\nstatic uint32_t pane_root(PaneVertex *v, uint32_t i) {\n    while (v[i].parent != i) { v[i].parent = v[v[i].parent].parent; i = v[i].parent; }\n    return i;\n}\nstatic int pane_position(const void *a, const void *b) {\n    const PaneVertex *x=a, *y=b;\n    for (int k=0;k<3;++k) { if (x->p[k]<y->p[k]) return -1; if (x->p[k]>y->p[k]) return 1; }\n    return 0;\n}\nstatic float pane_vlt(const double n[3], int mask) {\n    double horizontal=fabs(n[0])+fabs(n[1]), length2=n[0]*n[0]+n[1]*n[1]+n[2]*n[2];\n    // Factory roof privacy proxy; see docs/realworld-materials.md.\n    if (n[2]>0 && n[2]*n[2]>=.98*.98*length2) return .10f;\n    if (horizontal<1e-12) return mask ? .70f : .75f;\n    if (fabs(n[0])<=.25*horizontal) return .70f;\n    return n[0]<0 ? .20f : .75f;\n}\n// Each connected component of one material section gets one constant VLT.\n// Weld coincident positions across UV/normal seams, without joining nearby panes.\nfloat *realworld_materials_panes(const void *vertices, uint32_t nv, uint32_t stride, uint32_t position,\n                               uint32_t normal, const void *indices, uint32_t ni, uint32_t isz, uint32_t first, int mask) {\n    if (!vertices || !indices || !nv || !ni || ni%3 || (isz!=2 && isz!=4) ||\n        stride<12 || position>stride-12 || normal>stride-12) return NULL;\n    PaneVertex *v=calloc(nv,sizeof *v), *sorted=malloc((size_t)nv*sizeof *sorted);\n    float *out=malloc((size_t)nv*sizeof *out);\n    uint32_t *ix=malloc((size_t)ni*sizeof *ix);\n    if (!v || !sorted || !out || !ix) { free(v);free(sorted);free(out);free(ix);return NULL; }\n    for (uint32_t i=0;i<nv;++i) { v[i].parent=i; v[i].index=i; out[i]=mask?.70f:.75f; }\n    for (uint32_t k=0;k<ni;++k) {\n        uint32_t j=0;\n        memcpy(&j,(const uint8_t *)indices+(size_t)k*isz,isz);\n        if (j<first || j-first>=nv) goto invalid;\n        ix[k]=j-first;\n        const uint8_t *src=(const uint8_t *)vertices+(size_t)ix[k]*stride;\n        memcpy(v[ix[k]].p,src+position,12);\n        float n[3];memcpy(n,src+normal,12);\n        for (int c=0;c<3;++c) if (!isfinite(v[ix[k]].p[c]) || !isfinite(n[c])) goto invalid;\n    }\n    // Only referenced vertices participate in welding.\n    uint32_t used=0;\n    for (uint32_t k=0;k<ni;++k) {\n        uint32_t j=ix[k];\n        if (!v[j].sum[0]) { sorted[used++]=v[j]; v[j].sum[0]=1; }\n    }\n    for (uint32_t j=0;j<nv;++j) v[j].sum[0]=0;\n    qsort(sorted,used,sizeof *sorted,pane_position);\n    for (uint32_t k=1;k<used;++k) if (!pane_position(&sorted[k-1],&sorted[k]))\n        v[pane_root(v,sorted[k].index)].parent=pane_root(v,sorted[k-1].index);\n    for (uint32_t k=0;k<ni;k+=3) {\n        uint32_t root=pane_root(v,ix[k]);\n        v[pane_root(v,ix[k+1])].parent=root;v[pane_root(v,ix[k+2])].parent=root;\n    }\n    for (uint32_t k=0;k<ni;k+=3) {\n        double a[3],b[3],cross[3];\n        for (int c=0;c<3;++c) { a[c]=(double)v[ix[k+1]].p[c]-v[ix[k]].p[c];b[c]=(double)v[ix[k+2]].p[c]-v[ix[k]].p[c]; }\n        cross[0]=a[1]*b[2]-a[2]*b[1];cross[1]=a[2]*b[0]-a[0]*b[2];cross[2]=a[0]*b[1]-a[1]*b[0];\n        double weight=sqrt(cross[0]*cross[0]+cross[1]*cross[1]+cross[2]*cross[2])/3;\n        uint32_t root=pane_root(v,ix[k]);\n        for (int j=0;j<3;++j) {\n            float n[3];memcpy(n,(const uint8_t *)vertices+(size_t)ix[k+j]*stride+normal,12);\n            for (int c=0;c<3;++c) v[root].sum[c]+=weight*n[c];\n        }\n    }\n    for (uint32_t k=0;k<used;++k) { uint32_t j=sorted[k].index;out[j]=pane_vlt(v[pane_root(v,j)].sum,mask); }\n    free(v);free(sorted);free(ix);return out;\ninvalid:\n    free(v);free(sorted);free(ix);free(out);return NULL;\n}\n'
PRE_STREAM_HELPER = 'static RWPaneDraw realworld_pane_draw(const Elem *el, int nel, uint32_t type, uint32_t prims,\n                                     int indexed, int32_t base_vertex, uint32_t start, uint32_t up_vertices) {\n    if (type!=4 || !indexed || up_vertices || prims>UINT32_MAX/3) return (RWPaneDraw){0};\n    const Elem *position=NULL,*normal=NULL;\n    for (int i=0;i<nel;++i) {\n        if (el[i].usage==SM_USAGE_POSITION && !el[i].index) position=&el[i];\n        if (el[i].usage==SM_USAGE_NORMAL && !el[i].index) normal=&el[i];\n    }\n    if (!position || !normal || position->stream!=normal->stream || position->type!=2 || normal->type!=2) return (RWPaneDraw){0};\n    unsigned stream=position->stream;\n    Obj *vb=obj(dev.stream[stream]),*ib=obj(dev.indices);\n    uint32_t stride=dev.stream_stride[stream],isz=ib && ib->fmt==102?4:2,ni=3*prims;\n    if (!vb || !ib || !vb->mem[0] || !ib->mem[0] || !stride ||\n        (uint64_t)(start+ (uint64_t)ni)*isz>ib->size) return (RWPaneDraw){0};\n    const uint8_t *indices=G+ib->mem[0]+(size_t)start*isz;\n    uint32_t lo=UINT32_MAX,hi=0;\n    for (uint32_t k=0;k<ni;++k) {\n        uint32_t j=0;memcpy(&j,indices+(size_t)k*isz,isz);\n        if (j<lo) lo=j;if (j>hi) hi=j;\n    }\n    int64_t first=(int64_t)base_vertex+lo;\n    uint32_t nv=hi-lo+1;\n    if (!nv || lo>hi || first<0 || dev.stream_off[stream]+((uint64_t)first+nv)*stride>vb->size) return (RWPaneDraw){0};\n    const uint8_t *vertices=G+vb->mem[0]+dev.stream_off[stream]+(size_t)first*stride;\n    uint32_t key[]={nv,stride,position->offset,normal->offset,isz,lo,(uint32_t)realworld_materials_window_mask(),ni};\n    uint64_t h=fnv(key,sizeof key,1469598103934665603ull);\n    h=fnv(indices,(size_t)ni*isz,h);h=fnv(vertices,(size_t)nv*stride,h);\n    static struct { uint64_t hash; id<MTLBuffer> tint; } cache[64];\n    static unsigned next;\n    for (unsigned i=0;i<64;++i) if (cache[i].tint && cache[i].hash==h) return (RWPaneDraw){cache[i].tint,(uint32_t)first};\n    float *values=realworld_materials_panes(vertices,nv,stride,position->offset,normal->offset,indices,ni,isz,lo,key[6]);\n    if (!values) return (RWPaneDraw){0};\n    id<MTLBuffer> tint=[mtl newBufferWithBytes:values length:(size_t)nv*sizeof(float) options:MTLResourceStorageModeShared];\n    free(values);\n    unsigned slot=next++%64;cache[slot].hash=h;cache[slot].tint=tint;\n    return (RWPaneDraw){tint,(uint32_t)first};\n}\n'

def mesh_regression(tmp, optics):
    """Connected material sections; expected roles come from parts/position."""
    import ctypes as ct
    stock = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'CARS'
    private = Path(os.environ.get('NFS_PRIVATE_CARS', Path.home() / 'Library/Application Support/NFSMW-port/private-cars'))
    pointer = private / 'current.json'
    generation = json.loads(pointer.read_text())['generation'] if pointer.is_file() else 'absent'
    assert Path(generation).name == generation
    added = private / generation / 'additions'
    cars = ('CORVETTE', 'BRERA', 'COPSPORT', 'GTI', '911GT2', '240SX', 'MURCIELAGO640', 'ZONDA')
    converted = ('BRERA', '240SX', 'MURCIELAGO640', 'ZONDA')
    paths = [(added if car in converted else stock) / car / 'GEOMETRY.BIN' for car in cars]
    if any(not p.is_file() for p in paths):
        print('SKIP: pane proof needs stock Corvette/COPSPORT/GTI/911GT2 and added Brera/240SX/Murcielago640/Zonda')
        raise SystemExit(3)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    import carbon_car as cc
    import numpy as np
    assert hashlib.sha256(PRE_PANE_VLT.encode()).hexdigest() == 'f2c270e4b66cd52fda54e1e29abd07bbfa7a5eed8cf27631f6bedd8cd17b95f9'
    harness = '#include "'+str(RT/'traffic_detail.c')+'"\n#include "'+str(MODULE_PATH)+'"\n'
    harness += 'int x360_post_rolloff(void){return 0;}\n' + PRE_PANE_VLT.replace('rw_vlt(', 'baseline_vlt(').replace('static float', 'float', 1).replace('abs(', 'fabsf(')
    (tmp/'panes.c').write_text(harness)
    run(['clang', '-dynamiclib', '-I', RT, tmp/'panes.c', '-o', tmp/'panes.dylib'])
    assert hashlib.sha256(PRE_LOD_CLASSIFIER.encode()).hexdigest() == 'b7f89d4f06313381d83cf0c32432e0a5060a5ad3679b7fa18b068a9dcafc6d42'
    (tmp/'old-panes.c').write_text(PRE_LOD_CLASSIFIER)
    run(['clang','-dynamiclib','-I',RT,tmp/'old-panes.c','-o',tmp/'old-panes.dylib'])
    previous = ct.CDLL(str(tmp/'old-panes.dylib'))
    lib = ct.CDLL(str(tmp/'panes.dylib'))
    lib.realworld_materials_panes.argtypes = [ct.c_void_p, *([ct.c_uint32]*4), ct.c_void_p, *([ct.c_uint32]*3), ct.c_int]
    lib.realworld_materials_panes.restype = ct.POINTER(ct.c_float)
    lib.realworld_materials_role.argtypes = [ct.c_uint32, ct.c_int, ct.c_int, ct.c_uint32]
    lib.baseline_vlt.argtypes = [ct.c_float]*3
    lib.baseline_vlt.restype = ct.c_float
    free = ct.CDLL(None).free
    free.argtypes = [ct.c_void_p]
    previous.realworld_materials_panes.argtypes = lib.realworld_materials_panes.argtypes
    previous.realworld_materials_panes.restype = lib.realworld_materials_panes.restype
    lod_counts = {lod:[0,0,0,0] for lod in 'ABCD'} # sections, triangles, fixed wrong, preceding wrong
    # Front/rear/side/roof, counted separately per connected material section.
    expected_triangles = ((114,160,62,0), (102,100,98,78), (62,82,0,0), (128,184,154,0),
                          (156,176,174,0), (122,100,208,0), (174,4,146,87), (118,62,116,80))
    expected_panes = ((2,2,2,0), (2,2,6,2), (2,2,0,0), (2,2,6,0),
                      (2,2,6,0), (2,2,6,0), (2,1,6,9), (2,2,6,2))
    vlts = (.75, .20, .70, .10)
    review_quarters = {'911GT2': (38,142), '240SX': (78,156), 'MURCIELAGO640': (86,112), 'ZONDA': (58,64)}
    for car, path, want_triangles, want_panes in zip(cars, paths, expected_triangles, expected_panes):
        triangles = [0]*4; panes = [0]*4
        mixed = wrong = old_mixed = old_wrong = quarter_bad = quarter_total = 0
        roof_old = [0]*4
        for _, solid in cc.solids(path.read_bytes()):
            name = cc.payload(solid, 0x134011, 16)[160:].split(b'\0')[0].decode()
            if name[-2:] not in ('_A','_B','_C','_D') or '_T' in name or '_DAMAGE1_' in name or not ('WINDOW' in name or 'ROOF_SUN' in name):
                continue
            lod = name[-1]
            model = cc.native_wheel_model(solid)
            vertices = model['vertices'].view('<f4').reshape(-1, 9)
            for record in model['records']:
                material = model['lights'][record[29]]
                if material not in (cc.name_hash('WINDSHIELD'), cc.name_hash('WINDOWMASK')):
                    continue
                mask = int(material == cc.name_hash('WINDOWMASK'))
                first, count = cc.u32(record, 68), cc.u32(record, 92)
                indices = np.ascontiguousarray(model['indices'][first:first+count], dtype='<u2')
                tri = indices.reshape(-1, 3)
                # Test index offsets and both element widths, as used by the renderer.
                lo, hi = int(indices.min()), int(indices.max())
                section = np.ascontiguousarray(vertices[lo:hi+1])
                fixed = None
                for dtype in ('<u2', '<u4'):
                    ix = indices.astype(dtype)
                    ptr = lib.realworld_materials_panes(section.ctypes.data, len(section), 36, 0, 12,
                                                       ix.ctypes.data, len(ix), ix.itemsize, lo, mask)
                    assert ptr, (car, name, 'classifier rejected decoded draw')
                    values = np.ctypeslib.as_array(ptr, shape=(len(section),)).copy()
                    free(ptr)
                    if fixed is not None: assert np.array_equal(fixed, values), '16/32-bit mismatch'
                    fixed = values
                # Normal and winding reversals cannot change the geometric class.
                reversed_vertices = section.copy();reversed_vertices[:,3:6] *= -1
                reversed_indices = np.ascontiguousarray(indices.reshape(-1,3)[:,[0,2,1]])
                ptr = lib.realworld_materials_panes(reversed_vertices.ctypes.data,len(section),36,0,12,
                                                   reversed_indices.ctypes.data,len(indices),2,lo,mask)
                assert ptr
                reversed_values = np.ctypeslib.as_array(ptr,shape=(len(section),)).copy();free(ptr)
                assert np.array_equal(fixed,reversed_values), (car,name,'normal/winding reversal')
                ptr = previous.realworld_materials_panes(section.ctypes.data, len(section), 36, 0, 12,
                                                         indices.ctypes.data, len(indices), 2, lo, mask)
                assert ptr
                preceding = np.ctypeslib.as_array(ptr, shape=(len(section),)).copy();free(ptr)
                parent = {int(i):int(i) for i in indices}
                def root(i):
                    while parent[i] != i:
                        parent[i] = parent[parent[i]]; i = parent[i]
                    return i
                def union(a,b): parent[root(a)] = root(b)
                positions = {}
                for i in parent:
                    union(i, positions.setdefault(tuple(vertices[i,:3]), i))
                for a,b,c in tri: union(int(a),int(b)); union(int(a),int(c))
                components = {}
                for t in tri: components.setdefault(root(int(t[0])), []).append(t)
                for component in components.values():
                    vv = vertices[np.array(component)]
                    x = vv[:,:,0].mean()
                    if 'ROOF_SUN' in name: want = 3
                    elif car == 'COPSPORT': want = 1 if x < -.5 else 0
                    elif car == 'MURCIELAGO640' and '_REAR_WINDOW_' in name: want = 1 if x > -.6 else 3
                    elif car == 'ZONDA' and '_REAR_WINDOW_' in name: want = 3 if x > -.2 else 1
                    else: want = 2 if '_LEFT_' in name or '_RIGHT_' in name else 1 if '_REAR_' in name else 0
                    got = fixed[np.array(component)-lo]
                    wrong_fixed = np.count_nonzero(np.any(abs(got-vlts[want]) > 1e-6, axis=1))
                    wrong_previous = np.count_nonzero(np.any(abs(preceding[np.array(component)-lo]-vlts[want]) > 1e-6, axis=1))
                    for j,n in enumerate((1,len(component),wrong_fixed,wrong_previous)): lod_counts[lod][j] += int(n)
                    assert wrong_fixed == 0, (car,name,'LOD mismatch',wrong_fixed)
                    if lod != 'A': continue
                    triangles[want] += len(component); panes[want] += 1
                    mixed += np.ptp(got) > 1e-6
                    wrong += np.count_nonzero(np.any(abs(got-vlts[want]) > 1e-6, axis=1))
                    old = np.array([[lib.baseline_vlt(float(v[3]),float(v[4]),mask) for v in t] for t in vv])
                    bad = np.any(abs(old-vlts[want]) > 1e-6, axis=1)
                    old_wrong += int(bad.sum()); old_mixed += np.ptp(old) > 1e-6
                    if car in review_quarters and ('_REAR_LEFT_' in name or '_REAR_RIGHT_' in name):
                        quarter_bad += int(bad.sum()); quarter_total += len(component)
                    if car == 'BRERA' and want == 3:
                        for t in old:
                            cls = 3 if np.ptp(t)>1e-6 else 0 if t[0]>.72 else 1 if t[0]<.3 else 2
                            roof_old[cls] += 1
                    # Every triangle's 45 barycentric samples retain exactly the pane VLT.
                    for a in range(9):
                        for b in range(9-a):
                            samples = (a*got[:,0]+b*got[:,1]+(8-a-b)*got[:,2])/8
                            assert np.all(abs(samples-vlts[want])<1e-6), (car,name,want)
        print(f'glass panes {car}: front/rear/side/roof={tuple(panes)}, triangles={tuple(triangles)}, mixed/wrong={mixed}/{wrong}; preceding mixed/wrong={old_mixed}/{old_wrong}')
        assert tuple(triangles) == want_triangles and tuple(panes) == want_panes, (car, triangles, panes)
        assert mixed == wrong == 0, (car, mixed, wrong)
        expected_old = {'CORVETTE':(0,0), 'BRERA':(2,78), 'COPSPORT':(0,0), 'GTI':(0,0),
                        '911GT2':(4,38), '240SX':(4,78), 'MURCIELAGO640':(10,173), 'ZONDA':(4,138)}
        assert (old_mixed,old_wrong) == expected_old[car], (car,old_mixed,old_wrong)
        if car in review_quarters:
            assert (quarter_bad,quarter_total) == review_quarters[car], (car,quarter_bad,quarter_total)
        if car == 'BRERA': assert roof_old == [26,33,0,19], roof_old


    assert lod_counts == {'A':[82,3047,0,0], 'B':[82,1592,0,0], 'C':[74,1030,0,99], 'D':[72,635,0,60]}, lod_counts
    print(f'LOD A-D sections/triangles/fixed mismatches/preceding mismatches: {lod_counts}')

    # Decode both stock M3 GTR variants with the same production pane builder.
    # Part names supply the oracle; material bindings come from each draw.
    for car, side_triangles in (('BMWM3GTR', 132), ('BMWM3GTRE46', 136)):
        path = stock / car / 'GEOMETRY.BIN'
        if not path.is_file():
            print('SKIP: M3 GTR pane proof needs both stock geometry files')
            raise SystemExit(3)
        counts = {lod: [0, 0] for lod in 'ABCD'}
        triangles = [0, 0, 0]
        for _, solid in cc.solids(path.read_bytes()):
            name = cc.payload(solid, 0x134011, 16)[160:].split(b'\0')[0].decode()
            if name[-2:] not in ('_A', '_B', '_C', '_D') or '_T' in name or '_DAMAGE1_' in name or 'WINDOW' not in name:
                continue
            model = cc.native_wheel_model(solid)
            vertices = model['vertices'].view('<f4').reshape(-1, 9)
            for record in model['records']:
                material = model['lights'][record[29]]
                if material not in (cc.name_hash('WINDSHIELD'), cc.name_hash('WINDOWMASK')):
                    continue  # Separate decal sections retain native shading.
                mask = int(material == cc.name_hash('WINDOWMASK'))
                role = 2 if '_LEFT_' in name or '_RIGHT_' in name else 1 if '_REAR_' in name else 0
                first, count = cc.u32(record, 68), cc.u32(record, 92)
                indices = np.ascontiguousarray(model['indices'][first:first+count], dtype='<u2')
                lo, hi = int(indices.min()), int(indices.max())
                section = np.ascontiguousarray(vertices[lo:hi+1])
                for dtype in ('<u2', '<u4'):
                    ix = indices.astype(dtype)
                    ptr = lib.realworld_materials_panes(section.ctypes.data, len(section), 36, 0, 12,
                                                       ix.ctypes.data, len(ix), ix.itemsize, lo, mask)
                    assert ptr, (car, name, 'pane stream not built')
                    values = np.ctypeslib.as_array(ptr, shape=(len(section),)).copy(); free(ptr)
                    assert np.all(abs(values[indices-lo]-vlts[role]) < 1e-6), (car, name, 'wrong pane VLT')
                assert lib.realworld_materials_role(material, 1, 1, 0) == 1, (car, name, 'glass bypassed')
                counts[name[-1]][0] += 1; counts[name[-1]][1] += count//3
                if name[-1] == 'A': triangles[role] += count//3
        assert counts == {'A':[10,222+side_triangles], 'B':[10,202], 'C':[10,128], 'D':[10,92]}, (car, counts)
        assert triangles == [114,108,side_triangles], (car, triangles)
        print(f'glass panes {car}: LOD A-D sections/triangles={counts}; front/rear/side VLT=.75/.20/.70, wrong=0')

    # Undamaged dynamic WINDOW_* bindings resolve to WINDOW_FRONT; the separate
    # rear defroster and border mask also have coverage. No all-zero texture.
    import x360_tex as xt
    coverage = {}
    for name, hs, w, h, fmt, pixels in xt.textures_in((stock.parent/'GLOBAL/GLOBALB.BUN').read_bytes(), 0x80):
        if name not in ('WINDOW_FRONT', 'REAR_DEFROSTER', 'WINDOW_MASK'): continue
        alpha = xt.decode_preview(xt.PC_FMT[fmt], w, h, pixels)[:, :, 3]
        assert alpha.min() > 0
        coverage[name] = (int(alpha.min()), int(alpha.max()), round(float(alpha.mean()), 4))
    assert coverage == {'WINDOW_FRONT': (17,255,253.1157), 'REAR_DEFROSTER': (255,255,255.0), 'WINDOW_MASK': (255,255,255.0)}, coverage
    print(f'retail glass coverage alpha min/max/mean: {coverage}')

    # A large front-facing triangle and a tiny rear-facing triangle meet only
    # through duplicated seam vertices. Equal weighting would select roof (.10).
    synthetic = np.zeros((6,9), dtype='<f4')
    synthetic[:,:3] = ((0,0,1),(0,10,1),(-10,0,6),(0,0,1),(-.1,0,.95),(0,-.1,1))
    synthetic[:3,3:6] = (.5,0,.866)
    synthetic[3:,3:6] = (-.5,0,.866)
    seam_indices = np.arange(6,dtype='<u2')
    def synthetic_result(library):
        f = library.realworld_materials_panes
        f.argtypes = lib.realworld_materials_panes.argtypes
        f.restype = lib.realworld_materials_panes.restype
        p = f(synthetic.ctypes.data,6,36,0,12,seam_indices.ctypes.data,6,2,0,0)
        assert p
        result = np.ctypeslib.as_array(p,shape=(6,)).copy();free(p)
        return result
    assert np.all(abs(synthetic_result(lib)-.75)<1e-6), 'area weighting / welded seam'
    changed = MODULE.replace('v[root].sum[c]+=sign*cross[c]', 'v[root].sum[c]+=sign*cross[c]/sqrt(cross[0]*cross[0]+cross[1]*cross[1]+cross[2]*cross[2])')
    assert changed != MODULE
    (tmp/'unweighted.c').write_text(changed)
    (tmp/'panes-bad.c').write_text(harness.replace(str(MODULE_PATH),str(tmp/'unweighted.c')))
    run(['clang','-dynamiclib','-I',RT,tmp/'panes-bad.c','-o',tmp/'panes-bad.dylib'])
    unweighted = synthetic_result(ct.CDLL(str(tmp/'panes-bad.dylib')))
    assert np.count_nonzero(abs(unweighted-.75)>1e-6) == 6
    print('area-weighted seam: 0 wrong vertices; unweighted control: 6 wrong vertices')


def pane_stream_regression(tmp, renderer):
    """Execute production draw lookup and D3D lock hooks, with real Metal buffers."""
    d3d = (RT/'d3d9.m').read_text()
    source = r'''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <string.h>
#include "traffic_detail.c"
#include "realworld_materials.c"
int x360_post_rolloff(void){return 0;}
static uint8_t data[8192],*G=data;
static id<MTLDevice> mtl;
typedef struct {uint8_t stream,type,usage,index;uint16_t offset;} Elem;
typedef struct {uint32_t size,fmt,mem[1],guest;uint64_t buffer_generation;uint8_t buffer_lock_write,pane_cached;int dirty;} Obj;
static Obj vb,ib;
static struct {uint32_t stream[16],stream_off[16],stream_stride[16],indices;} dev;
static Obj *obj(uint32_t h){return h==1?&vb:h==2?&ib:NULL;}
static unsigned range_builds, classifications;
static float *counted_panes(const void *v,uint32_t nv,uint32_t stride,uint32_t p,uint32_t n,
                           const void *ix,uint32_t ni,uint32_t isz,uint32_t first,int mask){
 ++classifications;return realworld_materials_panes(v,nv,stride,p,n,ix,ni,isz,first,mask);
}
#define realworld_materials_panes counted_panes
'''
    start = renderer.index('typedef struct { id<MTLBuffer> tint; uint32_t first; } RWPaneDraw;')
    end = renderer.index('\nstatic void draw_impl',start)
    fixed = renderer[start:end]
    assert 'fnv(' not in fixed, 'repeated draw must not hash geometry'
    assert fixed.index('if (entry) return entry->draw;') < fixed.index('const uint8_t *indices=')
    assert 'render_panes_changed(o);' in function(renderer,'render_release')
    # Count geometry scans, not just classifier calls: the old classifier was
    # cached, but its full index/vertex scan still ran on every draw.
    scan = '    uint32_t lo=UINT32_MAX,hi=0;'
    assert scan in fixed and scan in PRE_STREAM_HELPER
    fixed = fixed.replace(scan,'    ++range_builds;\n'+scan)
    lock = r'''
typedef struct {uint32_t a[5];} Cpu;
static uint32_t arg(Cpu*c,int i){return c->a[i];}
#define THIS obj(arg(c,0))
#define M32(a) (*(uint32_t *)(G+(a)))
#define D3D_OK 0
#define RET(v,n) return (v)
'''+function(d3d,'b_Lock')+'\n'+function(d3d,'b_Unlock')+'\n'
    main = r'''
int main(void){@autoreleasepool{
 mtl=MTLCreateSystemDefaultDevice();if(!mtl)return 77;
 vb=(Obj){.size=2048,.mem={64},.guest=1};ib=(Obj){.size=6,.fmt=101,.mem={3000},.guest=2};
 dev.stream[0]=1;dev.stream_off[0]=36;dev.stream_stride[0]=36;dev.indices=2;
 Elem el[]={{0,2,SM_USAGE_POSITION,0,0},{0,2,SM_USAGE_NORMAL,0,12}};
 uint16_t ix[]={3,4,5};memcpy(G+3000,ix,sizeof ix);
 float *v=(float *)(G+64);
 // Front-facing geometric triangle; authored normals are intentionally unrelated.
 v[5*9]=1;v[6*9]=1;v[6*9+1]=1;v[7*9]=1;v[7*9+2]=1;
 RWPaneDraw a=realworld_pane_draw(el,2,4,1,1,1,0,0);
 int errors=!a.tint||a.first!=4;
 if(!a.tint)return 2;
 errors+=fabsf(((float*)a.tint.contents)[0]-.75f)>1e-6;
 for(int i=1;i<1000;++i){RWPaneDraw b=realworld_pane_draw(el,2,4,1,1,1,0,0);errors+=a.tint!=b.tint;}
 printf("pane cache repeated draws=1000 geometry scans=%u classifications=%u expected=1/1\n",range_builds,classifications);
 errors+=range_builds!=1;
 errors+=classifications!=1;
 // Read-only lock preserves the cache and generation.
 Cpu c={{1,0,0,1024,0x10}};b_Lock(&c);b_Unlock(&c);
 errors+=vb.buffer_generation!=0;
 errors+=realworld_pane_draw(el,2,4,1,1,1,0,0).tint!=a.tint;
 // A middle-vertex position edit turns the geometric direction toward the side.
 c.a[4]=0;b_Lock(&c);v[6*9]=-4;b_Unlock(&c);
 errors+=vb.buffer_generation!=2;
 RWPaneDraw b=realworld_pane_draw(el,2,4,1,1,1,0,0);
 errors+=a.tint==b.tint||!b.tint;
 if(b.tint)for(int i=0;i<3;++i)errors+=fabsf(((float*)b.tint.contents)[i]-.70f)>1e-6;
 // Repeated generations replace old streams rather than growing the dictionary.
 for(int i=0;i<100;++i){b_Lock(&c);b_Unlock(&c);realworld_pane_draw(el,2,4,1,1,1,0,0);}
 errors+=rw_panes.count!=1;
 // Topology writes invalidate via the index buffer's generation too.
 c.a[0]=2;b_Lock(&c);ix[1]=ix[2]=3;memcpy(G+3000,ix,sizeof ix);b_Unlock(&c);
 RWPaneDraw d=realworld_pane_draw(el,2,4,1,1,1,0,0);
 errors+=!d.tint||b.tint==d.tint||ib.buffer_generation!=2;
 if(d.tint)errors+=fabsf(((float*)d.tint.contents)[0]-.75f)>1e-6;
 errors+=rw_panes.count!=1;
 // Material and ranges are part of the key. More than 64 live entries survive.
 for(int i=0;i<96;++i){dev.stream_off[0]=i*4;realworld_pane_draw(el,2,4,1,1,0,0,0);}
 errors+=rw_panes.count!=97;
 dev.stream_off[0]=36;
 unsigned before=range_builds;
 errors+=realworld_pane_draw(el,2,4,1,1,1,0,0).tint!=d.tint;
 errors+=range_builds!=before;
 realworld_materials_set(1);realworld_materials_capture(0,0,0);
 errors+=realworld_pane_draw(el,2,5,1,1,1,0,0).tint!=nil;
 errors+=realworld_pane_draw(el,2,4,1,1,-9,0,0).tint!=nil;
 errors+=realworld_pane_draw(el,2,4,1,1,1,1,0).tint!=nil;
 // Release removes dependencies before identities can be reused.
 render_panes_changed(&ib);errors+=rw_panes.count!=0;
 ib=(Obj){.size=6,.fmt=101,.mem={3000},.guest=2};
 errors+=!realworld_pane_draw(el,2,4,1,1,1,0,0).tint;
 render_panes_changed(&vb);errors+=rw_panes.count!=0;
 printf("pane stream generations/range/reuse/release: errors=%d\n",errors);return errors?1:0;
}}
'''
    args = ['clang','-fobjc-arc','-I',RT,'-framework','Foundation','-framework','Metal']
    (tmp/'stream.m').write_text(source+fixed+lock+main)
    run([*args,tmp/'stream.m','-o',tmp/'stream'])
    run([tmp/'stream'])
    # The exact preceding helper scans all the geometry on every repeated draw.
    assert hashlib.sha256(PRE_STREAM_HELPER.encode()).hexdigest() == 'eab155d54bde13d50d0ca2076a12ee2519d3930e02f8e277403e465ce7b0690c'
    old = 'typedef struct { id<MTLBuffer> tint; uint32_t first; } RWPaneDraw;\n'
    old += function(renderer,'fnv')+'\n'+PRE_STREAM_HELPER.replace(scan,'    ++range_builds;\n'+scan)
    old_main = main[:main.index(' // Read-only lock')]+r'''
 printf("preceding geometry scans=%u\n",range_builds);return range_builds==1000 && classifications==1 && errors==1?1:2;
}}
'''
    (tmp/'stream-old.m').write_text(source+old+old_main)
    run([*args,tmp/'stream-old.m','-o',tmp/'stream-old'])
    assert subprocess.run([tmp/'stream-old']).returncode == 1
    # Missing invalidation/generation changes must fail the same production test.
    broken = lock.replace('++b->buffer_generation; render_panes_changed(b);','')
    assert broken != lock
    (tmp/'stream-bad.m').write_text(source+fixed+broken+main)
    run([*args,tmp/'stream-bad.m','-o',tmp/'stream-bad'])
    assert subprocess.run([tmp/'stream-bad']).returncode == 1


def main():
    with tempfile.TemporaryDirectory(prefix='glass-transmission-') as td:
        optics = literal(MODULE, 'optics')
        mesh_regression(Path(td), optics)
        assert glass_regression(Path(td), optics, MODULE)==0, 'automotive glass transmission'
        # Frozen original pane/alpha expression: the same production check fails twice.
        old = 'float2 rt=rw_pane(nv); float alpha=coverage*(1.0f-rt.y);'
        assert hashlib.sha256(old.encode()).hexdigest()=='ae8d846da805cf6af632b4214382df16d6ead9edcd6498fb2db96e6ca7050526'
        fixed = 'float2 rt=rw_glass(nv,pi.texcoord4.w); float alpha=coverage*(1.0f-rt.y);'
        assert fixed in MODULE
        assert glass_regression(Path(td), optics, MODULE.replace(fixed, old))==1
    required=[ROOT/'host/shaders/build/libsmtranslate.a',
              *(RT/'gen'/name for name in ('car_shadow_off.inc','pc_shader_lits.inc','x360_shader_lits.inc')),
              *(ROOT/f'host/shaders/testdata/car/t{v}_car_p0_vs_1_1.bin' for v in range(2))]
    if any(not p.exists() for p in required) or len(list((ROOT/'host/shaders/testdata/car').glob('*_ps_*.bin')))!=6:
        print('SKIP: needs built shader translator and generated car shader fixtures')
        raise SystemExit(3)
    for name, digest in PINNED.items():
        value = initializer(SRC, name) if name == 'car_paint_msl' else function(SRC, name)
        assert hashlib.sha256(value.encode()).hexdigest() == digest, 'Off source changed: ' + name
    renderer = (RT / 'render.m').read_text()
    assert 'realworld_materials_draw(ps->tokens_hash, vs->tokens_hash)' in renderer
    assert 'realworld_materials_capture(m ? M32(m + 0xC) : 0, m && fx && M32(fx + 4) == 4, arg(c, 1))' in SRC
    assert 'realworld_materials_constants(id, vsc, out)' in SRC
    assert 'realworld_materials_texture(diffuse ? M32(diffuse + 0x24) : 0)' in SRC
    for hook in ('007535E0','0073B470','0073B640'):
        assert re.search('^'+hook+r'\s', (ROOT/'recomp/hooks.txt').read_text(), re.M)
    assert 'obj(realworld_materials_rim_mask())' in renderer
    optics = literal(MODULE, 'optics')
    with tempfile.TemporaryDirectory(prefix='realworld-materials-') as td:
        tmp = Path(td)
        pane_stream_regression(tmp, renderer)
        (tmp/'cpu.cpp').write_text(CPU+optics+CPU_MAIN)
        run(['clang++','-std=c++17',tmp/'cpu.cpp','-o',tmp/'cpu'])
        run([tmp/'cpu'])
        for name,bad in [('f0',optics.replace('3.3290909091f','0.0f')),('absorption',optics.replace('2.2e-7f','2.2e-5f'))]:
            assert bad != optics
            (tmp/'cpu-bad.cpp').write_text(CPU+bad+CPU_MAIN)
            run(['clang++','-std=c++17',tmp/'cpu-bad.cpp','-o',tmp/'cpu-bad'])
            assert subprocess.run([tmp/'cpu-bad']).returncode == 1, name+' control escaped'
        pre = '#include "'+str(RT/'traffic_detail.c')+'"\n'
        pre += '#include "'+str(MODULE_PATH)+'"\n#include "../host/shaders/sm_coverage.h"\n#include "gen/car_shadow_off.inc"\n#include "gen/pc_shader_lits.inc"\n#include "gen/x360_shader_lits.inc"\n'
        pre += re.search(r'^#define CAR_PAINT_POW_MSL.*?\n\n', SRC, re.M|re.S)[0]
        pre += 'static const char car_paint_msl[] = '+initializer(SRC,'car_paint_msl')
        rows=re.findall(r'CAR\("[^"\n]+", (0x[0-9A-F]+ULL), ([1-5])\)',SRC)
        assert len(rows)==5
        pre += '\nstatic char *car_t0_ps_patch(const char *s){return strdup(s); }\n'
        pre += 'typedef struct MatShader {uint64_t hash;const char *msl;char *(*patch)(const char *);int variant;} MatShader;\n'
        pre += 'static const MatShader table[] = {\n'
        pre += ',\n'.join('{'+h+',car_paint_msl,NULL,'+v+'}' for h,v in rows)
        t0=re.search(r'"car t0 ps_1_1[^"\n]*", (0x[0-9A-F]+ULL)',SRC)[1]
        pre += ',{'+t0+',NULL,car_t0_ps_patch,0}};\n'
        shader_guard=function(SRC,'mat_realworld_shader')
        pre += shader_guard+'\n'

        (tmp/'emit.c').write_text(pre+EMIT_MAIN)
        def build_emit(source, output):
            run(['clang','-I',RT,'-c',source,'-o',tmp/'emit.o'])
            run(['clang++',tmp/'emit.o',ROOT/'host/shaders/build/libsmtranslate.a','-o',output])
        build_emit(tmp/'emit.c',tmp/'emit')
        if MODULE_PATH == RT/'realworld_materials.c':
            rim_main = r'''
uint8_t *G;
static int composite_ok=1, composite_kind=0, composite_has_mask=1;
void f_0073B470_orig(Cpu*c){c->eax=composite_ok;c->esp+=4;}
void f_0073B640_orig(Cpu*c){c->eax=composite_ok;c->esp+=4;}
void f_007535E0_orig(Cpu*c){
 if(composite_has_mask){
  Cpu inner={0};inner.esp=0x9000;M32(0x900c)=0x3000;
  M32(0x3000)=0x4000;M32(0x4018)=77;
  if(composite_kind)f_0073B470(&inner);else f_0073B640(&inner);
  if(inner.esp!=0x9004||inner.eax!=composite_ok)abort();
 }
 c->eax=composite_ok;c->esp+=4;
}
int x360_post_rolloff(void){return 0;}
static void composite(uint32_t texture,int painted){
 Cpu c={0};c.esp=0x8000;M32(0x8004)=0x1000;
 M32(0x1040)=texture;M32(0x1180)=painted?0x2000:0;
 f_007535E0(&c);if(c.esp!=0x8004||c.eax!=composite_ok)abort();
}
int main(void){float vsc[256][4]={{0}};MatConsts out={0};int failures=0;
 G=calloc(1,65536);if(!G)return 2;
 composite(17,1);composite(18,0);
 for(int m=0;m<4;m++)for(int car=0;car<2;car++)for(int painted=0;painted<2;painted++){
  realworld_materials_set(m);
  realworld_materials_capture(hash_name("USER_RIMS_CHROME"),car,0);
  realworld_materials_texture(painted?17:18);
  realworld_materials_constants(MAT_REAL_T0,vsc,&out);
  failures+=(out.k[2][0]>.5f)!=(car&&(m&2)&&painted);
  failures+=realworld_materials_rim_mask()!=(car&&(m&2)&&painted?77:0);
 }
 realworld_materials_set(3);
 composite(17,0);
 realworld_materials_capture(hash_name("USER_RIMS_CHROME"),1,0);realworld_materials_texture(17);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][0]!=0;
 composite(17,1);
 composite_ok=0;composite(17,0);composite_ok=1;
 realworld_materials_capture(hash_name("USER_RIMS_CHROME"),1,0);realworld_materials_texture(17);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][0]!=1;
 failures+=realworld_materials_draw(0x89E9A52CCEE09BCFULL,0x1BEA38904139377AULL)!=MAT_VARIANT_REAL_CHROME;
 realworld_materials_capture(hash_name("MAGCHROME"),1,0);realworld_materials_texture(17);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][0]!=0;
 realworld_materials_capture(hash_name("USER_RIMS_CHROME"),1,0);realworld_materials_texture(19);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);failures+=out.k[2][0]!=0;
 composite_kind=1;composite(17,1);
 realworld_materials_capture(hash_name("USER_RIMS_CHROME"),1,0);realworld_materials_texture(17);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);
 failures+=out.k[2][0]!=1||out.k[2][1]!=1||realworld_materials_rim_mask()!=77;
 composite_has_mask=0;composite(17,1);
 realworld_materials_capture(hash_name("USER_RIMS_CHROME"),1,0);realworld_materials_texture(17);
 realworld_materials_constants(MAT_REAL_T0,vsc,&out);
 failures+=out.k[2][0]!=0||out.k[2][1]!=0||realworld_materials_rim_mask()!=0;
 printf("rim capture: 16 mode/car/colour cases + reuse/failed-composite/bare/unknown controls, failures=%d\n",failures);
 free(G);return failures?1:0;}
'''
            wrapper = 'static uint32_t rim_composite_mask;static int rim_composite_paletted;\n'
            wrapper += function(SRC, 'ti_d3d')+'\n'
            for name in ('f_0073B470','f_0073B640','f_007535E0'):
                wrapper += 'void '+name+'_orig(Cpu*c);\n'+function(SRC,name)+'\n'
            (tmp/'rim.c').write_text('#include "host.h"\n#include "'+str(RT/'traffic_detail.c')+'"\n#include "'+str(MODULE_PATH)+'"\nvoid f_007535E0_orig(Cpu*c);\n'+wrapper+rim_main)
            run(['clang','-I',RT,'-I',ROOT/'recomp',tmp/'rim.c','-o',tmp/'rim'])
            run([tmp/'rim'])

        # Restore the original guard, retaining the same decoded input path.
        old_guard=shader_guard[:shader_guard.index('        // Decoded car fixtures:')]+'''        int shadow=0;
        for(int j=0;j<refl->n_samplers;++j) {
            const SmSampler *sm=&refl->samplers[j];
            if(sm->reg<2 && sm->type!=(sm->reg ? SM_TEX_CUBE : SM_TEX_2D)) return NULL;
            if(sm->shadow && sm->reg!=2) return NULL;
            shadow |= sm->shadow;
        }
'''+shader_guard[shader_guard.index('        char *s=realworld_materials_pixel'):]
        (tmp/'old-guard.c').write_text(pre.replace(shader_guard,old_guard)+EMIT_MAIN)
        build_emit(tmp/'old-guard.c',tmp/'old-guard')
        for choice,expected in [(None,0),('off',0),('glass',1),('chrome',2),('all',3),('rims',0),('garbage',0)]:
            env=dict(os.environ,EXPECT=str(expected));env.pop('NFS_REALWORLD_MATERIALS',None)
            if choice is not None:env['NFS_REALWORLD_MATERIALS']=choice
            run([tmp/'emit'],env=env)
        bad=MODULE.replace('if (!car) return 0;','if (!car) return 1;')
        (tmp/'role-bad.c').write_text(bad)
        (tmp/'emit-bad.c').write_text((pre+EMIT_MAIN).replace('#include "'+str(MODULE_PATH)+'"','#include "role-bad.c"'))
        build_emit(tmp/'emit-bad.c',tmp/'emit-bad')
        assert subprocess.run([tmp/'emit-bad'],env=dict(os.environ,EXPECT='0',NFS_REALWORLD_MATERIALS='off')).returncode==1
        # Execute the production pair-selection block under pending/failure states.
        draw=renderer.split('    int real_material =',1)[1].split('    if (mat_carbon_lamp_draw())',1)[0]
        draw='    int real_material ='+draw
        guard=r'''
#include "realworld_materials.h"
typedef struct { int pending,ok; } ShaderEntry;
typedef struct {uint64_t tokens_hash;} Obj;
static ShaderEntry p,v,base_p,base_v;
static Obj pixel,vertex;
static int enabled,pane_ready;
typedef struct {void *tint;uint32_t first;} RWPaneDraw;
#define realworld_pane_draw(...) ((RWPaneDraw){pane_ready?(void*)1:0,0})
int realworld_materials_draw(uint64_t a,uint64_t b){(void)a;(void)b;return enabled?MAT_VARIANT_REAL_GLASS:0;}
static ShaderEntry *get_shader_variant(Obj *o,int *options,int variant){(void)options;(void)variant;return o==&pixel?&p:&v;}
static int selected(void){Obj *ps=&pixel,*vs=&vertex;int po=0,vo=0;ShaderEntry*pse=&base_p,*vse=&base_v;
''' + draw + r'''
 return rw_pane_active==(pse==&p) ? (pse==&p)*2+(vse==&v) : -1;
}
int main(void){int failures=0;
 for(pane_ready=0;pane_ready<2;pane_ready++)for(enabled=0;enabled<2;enabled++)for(p.pending=0;p.pending<2;p.pending++)for(v.pending=0;v.pending<2;v.pending++)
 for(p.ok=0;p.ok<2;p.ok++)for(v.ok=0;v.ok<2;v.ok++){
  int want=pane_ready&&enabled&&!p.pending&&!v.pending&&p.ok&&v.ok?3:0;failures+=selected()!=want;
 }
 return failures?1:0;}
'''
        (tmp/'draw.c').write_text(guard)
        run(['clang','-Wall','-Wextra','-Werror','-I',RT,tmp/'draw.c','-o',tmp/'draw'])
        run([tmp/'draw'])
        bad=guard.replace('!__atomic_load_n(&real_vs->pending, __ATOMIC_ACQUIRE) && ','')
        assert bad!=guard
        (tmp/'draw-bad.c').write_text(bad)
        run(['clang','-I',RT,tmp/'draw-bad.c','-o',tmp/'draw-bad'])
        assert subprocess.run([tmp/'draw-bad']).returncode==1,'pending vertex control escaped'
        translator_src = r'''#include "sm_translate.h"
#include <fstream>
#include <vector>
#include <iostream>
int main(int argc,char**argv){std::ifstream f(argv[1],std::ios::binary|std::ios::ate);size_t n=f.tellg();f.seekg(0);std::vector<uint32_t>w(n/4);f.read((char*)w.data(),n);SmOptions o;sm_default_options(&o);SmResult r={};if(sm_translate(w.data(),w.size(),&o,&r))return 1;std::cout<<r.msl;sm_result_free(&r);}'''
        (tmp/'translate.cpp').write_text(translator_src)
        run(['clang++','-I',ROOT/'host/shaders',tmp/'translate.cpp',ROOT/'host/shaders/build/libsmtranslate.a','-o',tmp/'translate'])
        sources=[]
        for v in range(2):
            translated=tmp/f'vs{v}.original'
            with translated.open('w') as f:run([tmp/'translate',ROOT/f'host/shaders/testdata/car/t{v}_car_p0_vs_1_1.bin'],stdout=f)
            out=tmp/f'vs{v}.metal';run([tmp/'emit',translated,out,1-v,ROOT/f'host/shaders/testdata/car/t{v}_car_p0_vs_1_1.bin']);sources.append(out)
            assert ('c[13].xyz-si.v[0].xyz' if v==0 else 'c[17].xyz-si.v[0].xyz') in out.read_text()
            assert 'dot(rwN,c[' in out.read_text() and 'rwTint[rwID-rwBase]' in out.read_text() and '[[vertex_id]]' in out.read_text()
        accepted=old_rejected=old_accepted=unknown_rejected=0
        t0_errors = preceding_t0_errors = 0
        for v in range(6):
            for depth in range(2 if v else 1):
                for chrome in range(2):
                    out=tmp/f'ps{v}-{depth}-{chrome}.metal'
                    pixel=next((ROOT/'host/shaders/testdata/car').glob(f't{v}_car_p0_ps_*.bin'))
                    args=[tmp/'vs0.original',out,v,depth,chrome,pixel]
                    run([tmp/'emit',*args]);sources.append(out);accepted+=1
                    if v == 0:
                        generated = out.read_text()
                        t0_errors += t0_binding_errors(generated)
                        # Frozen original declarations, before the t0 remap.
                        preceding = 'tex0 [[texture(0)]], sampler smp0 [[sampler(0)]]\ntex1 [[texture(1)]], sampler smp1 [[sampler(1)]]'
                        assert hashlib.sha256(preceding.encode()).hexdigest() == '1a71d6927001d7ac65f8d241b20c810bca9a604fe2e81806062df97163c5d742'
                        broken = generated
                        for i, declaration in enumerate(preceding.splitlines()):
                            fixed = f'tex{i} [[texture({1-i})]], sampler smp{i} [[sampler({1-i})]]'
                            broken = broken.replace(fixed, declaration)
                        preceding_t0_errors += t0_binding_errors(broken)
                    assert 'rw_direct(N,V,L,dNdx,dNdy)*m.k[1].rgb*lit' in out.read_text()
                    assert 'reflected/alpha' in out.read_text()
                    result=subprocess.run(list(map(str,[tmp/'old-guard',*args])))
                    assert result.returncode==(4 if v==0 else 0), 'old sampler guard control'
                    old_rejected+=result.returncode==4;old_accepted+=result.returncode==0
                    if depth==0:
                        for reject in ['type','missing','extra','duplicate','register','shadow','swapped']:
                            for sampler in range((4 if v else 2) if reject=='type' else 1):
                                run([tmp/'emit',*args],env=dict(os.environ,REJECT_LAYOUT=reject,BAD_SAMPLER=str(sampler)))
                                unknown_rejected+=1
        assert (accepted,old_rejected,old_accepted,unknown_rejected)==(22,2,20,116)
        print(f't0 native texture/sampler binding errors: preceding={preceding_t0_errors}, fixed={t0_errors}')
        assert preceding_t0_errors == 8 and t0_errors == 0, 'unshadowed retail resource layout'
        print(f'sampler guard: accepted={accepted}; old guard rejected t0={old_rejected}, accepted t1-t5={old_accepted}; unknown rejected={unknown_rejected}')
        # Double-alpha fails the exact same pane oracle after fixed-function blending.
        rt=.081346;alpha=1-.898766
        assert abs((rt/alpha)*alpha-rt)<=1e-4
        assert abs(rt*alpha-rt)>1e-4
        # A changed native byte must fail the pinned identity check.
        frozen=initializer(SRC,'car_paint_msl')
        assert hashlib.sha256((frozen+' ').encode()).hexdigest()!=PINNED['car_paint_msl']
        (tmp/'gpu.m').write_text(GPU)
        run(['clang++','-x','objective-c++','-fobjc-arc','-framework','Foundation','-framework','Metal',tmp/'gpu.m','-o',tmp/'gpu'])
        kernel='''\nkernel void rw_check(device float4 *o [[buffer(0)]],uint i [[thread_position_in_grid]]) {\nfloat ci=i==0?1.0f:i==1?0.5f:0.1736481777f;o[i]=float4(rw_pane(ci),rw_conductor(1.0f),rw_surface(1.0f));}\n'''
        (tmp/'optics.metal').write_text('#include <metal_stdlib>\nusing namespace metal;\n'+optics+kernel)
        gpu=subprocess.run([str(tmp/'gpu'),*map(str,sources),str(tmp/'optics.metal')])
        if gpu.returncode==77 and not (os.environ.get('CODEX_GPU_CHECK') or os.environ.get('NFS_GPU_SANDBOX')):
            print('PENDING: GPU execution needs run_tests')
        else:
            assert gpu.returncode==0,'GPU source/optics check'
            for chrome in range(2):
                original=(tmp/f'ps0-0-{chrome}.metal').read_text()
                broken=original.replace('tex0 [[texture(1)]], sampler smp0 [[sampler(1)]]',
                                        'tex0 [[texture(0)]], sampler smp0 [[sampler(0)]]')
                broken=broken.replace('tex1 [[texture(0)]], sampler smp1 [[sampler(0)]]',
                                      'tex1 [[texture(1)]], sampler smp1 [[sampler(1)]]')
                assert broken!=original
                (tmp/'bad-t0.metal').write_text(broken)
                assert subprocess.run([tmp/'gpu',tmp/'bad-t0.metal']).returncode==1, 'native t0 layout control escaped'
            (tmp/'bad-vertex.metal').write_text((tmp/'vs0.metal').read_text().replace('rwTint[rwID-rwBase]','rwTint[rwID]'))
            assert subprocess.run([tmp/'gpu',tmp/'bad-vertex.metal']).returncode==1, 'lost stream base escaped'
            (tmp/'bad.metal').write_text((tmp/'optics.metal').read_text().replace('3.3290909091f','0.0f'))
            assert subprocess.run([tmp/'gpu',tmp/'bad.metal']).returncode==1,'GPU wrong-F0 escaped'
            for name,old,new,filename in [
                ('lost-rim-tint','paint=saturate(paint); bool painted=paint>0.0f;','paint=0.0f; bool painted=false;','ps1-0-1.metal'),
                ('texture-wide-rim','paint=saturate(paint); bool painted=paint>0.0f;','paint=m.k[2].x; bool painted=paint>0.0f;','ps1-0-1.metal'),
                ('clear-automotive','rw_glass(nv,pi.texcoord4.w)','rw_pane(nv)','ps1-0-0.metal'),
                ('lost-pane-vlt','rw_glass(nv,pi.texcoord4.w)','rw_glass(nv,0.75f)','ps1-0-0.metal'),
                ('roof-only-vlt','rw_glass(nv,pi.texcoord4.w)','rw_glass(nv,0.10f)','ps1-0-0.metal'),
                ('double-alpha','reflected/alpha','reflected','ps1-0-0.metal'),
                ('masked-specular','rw_direct(N,V,L,dNdx,dNdy)*m.k[1].rgb*lit','rw_direct(N,V,L,dNdx,dNdy)*m.k[1].rgb*lit*v1.rgb','ps1-0-1.metal'),
                ('wrong-f0','3.3290909091f','0.0f','ps1-0-1.metal')]:
                original=(tmp/filename).read_text();mutant=original.replace(old,new);assert mutant!=original
                (tmp/'bad-production.metal').write_text(mutant)
                assert subprocess.run([tmp/'gpu',tmp/'bad-production.metal']).returncode==1,name+' production control escaped'

        print(f'PASS native pins={len(PINNED)}; 7 switch cases; 88 role cases; wrong-F0/absorption/role/double-alpha rejected; {len(sources)} shader variants')


if __name__ == '__main__':
    main()
