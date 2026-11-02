#!/usr/bin/env python3
"""Production chrome filtering against integrated pixel shading, no game runs.

CPU original -> fixed: worst strip normalized RMS .990615 -> .133734;
filtered integrated energy error <= .020357; zero-footprint difference = 0.
Metal original -> fixed normalized RMS: checker .066393 -> .003948, painted
checker .072762 -> .007328, strip .702164 -> .107273 (energy error .000980).
The CPU rotating-strip reference integrates the pinned original GGX equation;
the GPU reference supersamples the original production chrome fragment.
CPU: 32 subpixel phases, four widths/rotations and three view angles; normalized
RMS <= .18, integrated strip energy within 6%. Zero footprint keeps base alpha
.006, Cr F0, exact Fresnel and Smith masking (relative error <= .005).
GPU: checker cube phase sweep, RGB RMS <= .03 of reference peak; rotating strip
RMS <= .18, energy within 10%. Original level(0) and unfiltered direct controls
must independently fail the same pixel oracle. Explicit gradients must precede
varying branches, including the painted diffuse path. Off's seven source pins
remain in realworld_materials_test.py. Missing Metal locally is pending run_tests.
"""
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / 'runtime'
MODULE = (RT / 'realworld_materials.c').read_text()

ORIGINAL = '''static float rw_original(float3 N, float3 V, float3 L) {
    float nv=saturate(dot(N,V)), nl=saturate(dot(N,L));
    if (nv<=0.0f || nl<=0.0f) return 0.0f;
    float3 H=normalize(V+L); float nh=saturate(dot(N,H));
    float d=(1.0f-nh*nh)+0.000036f*nh*nh;
    float D=0.000036f/(3.141592653589793f*d*d);
    return D*rw_g1(nv)*rw_g1(nl)*rw_conductor(saturate(dot(V,H)))/(4.0f*nv);
}'''

CPU = r'''
#include <simd/simd.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
using float3=simd_float3;
using std::abs; using std::max;
struct float2 {float x,y;float2(float a,float b):x(a),y(b){}};
static float clamp(float x,float a,float b){return std::clamp(x,a,b);}
static float saturate(float x){return clamp(x,0,1);}
static float dot(float3 a,float3 b){return simd_dot(a,b);}
static float3 cross(float3 a,float3 b){return simd_cross(a,b);}
static float3 normalize(float3 x){return simd_normalize(x);}
'''

CPU_MAIN = r'''
int main(int argc,char**argv) {
 (void)argv;bool broken=argc>1;int errors=0,cases=0;double worst=0,old_worst=0,energy_error=0,zero_error=0;
 for(float width : {.02f,.04f,.08f,.12f})for(float rotation : {0.f,.4f,1.2f,2.4f})for(float view : {0.f,.78539816f,1.30899694f}) {
  float3 V={sinf(view),0,cosf(view)},L={-sinf(view),0,cosf(view)};
  float3 tangent={cosf(rotation),sinf(rotation),0},up={0,0,1};
  double se=0,old_se=0,total=0,old_total=0,ref_total=0,peak=0;int count=0;
  for(int phase=0;phase<32;++phase)for(int pixel=-3;pixel<=3;++pixel){
   float angle=(pixel+(phase+.5f)/32-.5f)*width;
   float3 N=up*cosf(angle)+tangent*sinf(angle),dx=width*(tangent*cosf(angle)-up*sinf(angle));
   float original=rw_original(N,V,L),zero=rw_direct(N,V,L);
   if(original>1e-5f)zero_error=max(zero_error,(double)abs(zero/original-1));
   double ref=0;
   for(int i=0;i<1024;++i){float a=angle+width*((i+.5f)/1024-.5f);ref+=rw_original(up*cosf(a)+tangent*sinf(a),V,L)/1024;}
   double value=broken?original:rw_direct(N,V,L,dx,up*0.f);
   se+=(value-ref)*(value-ref);old_se+=(original-ref)*(original-ref);++count;
   total+=value;old_total+=original;ref_total+=ref;peak=max(peak,ref);
  }
  double rms=sqrt(se/count)/peak,old_rms=sqrt(old_se/count)/peak,energy=abs(total/ref_total-1);
  worst=max(worst,rms);old_worst=max(old_worst,old_rms);energy_error=max(energy_error,energy);
  errors+=(rms>.18 || energy>.06);++cases;
 }
 errors+=zero_error>.005 || abs(rw_conductor(1)-.554535985f)>1e-5;
 printf("STRIP CPU %s cases=%d original_nrmse=%.6f fixed_nrmse=%.6f energy_error=%.6f zero_error=%.6f failures=%d\n",errors?"FAIL":"PASS",cases,old_worst,worst,energy_error,zero_error,errors);
 return errors?1:0;
}
'''

GPU = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
struct Params {simd_float4 k[3],scene;};
static id<MTLTexture> draw(id<MTLDevice>d,id<MTLCommandQueue>q,id<MTLRenderPipelineState>p,id<MTLTexture>cube,id<MTLTexture>white,Params params,unsigned w,unsigned h){
 MTLTextureDescriptor*t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:w height:h mipmapped:NO];
 t.storageMode=MTLStorageModeShared;t.usage=MTLTextureUsageRenderTarget;
 id<MTLTexture>out=[d newTextureWithDescriptor:t];id<MTLCommandBuffer>cb=[q commandBuffer];
 MTLRenderPassDescriptor*r=[MTLRenderPassDescriptor renderPassDescriptor];r.colorAttachments[0].texture=out;r.colorAttachments[0].loadAction=MTLLoadActionDontCare;r.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLRenderCommandEncoder>e=[cb renderCommandEncoderWithDescriptor:r];[e setRenderPipelineState:p];
 [e setVertexBytes:&params length:sizeof params atIndex:0];[e setFragmentBytes:&params length:sizeof params atIndex:0];
 [e setFragmentTexture:cube atIndex:0];[e setFragmentTexture:white atIndex:1];[e setFragmentTexture:white atIndex:2];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
 if(cb.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"GPU command: %s\n",cb.error.localizedDescription.UTF8String);exit(2);}return out;
}
int main(int argc,char**argv){@autoreleasepool{
 if(argc!=2)return 2;id<MTLDevice>d=MTLCreateSystemDefaultDevice();if(!d){puts("GPU unavailable");return 77;}
 id<MTLCommandQueue>q=[d newCommandQueue];NSError*error=nil;
 NSString*src=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
 id<MTLLibrary>lib=[d newLibraryWithSource:src options:nil error:&error];if(!lib){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 2;}
 id<MTLRenderPipelineState>pipelines[2];
 for(int i=0;i<2;++i){MTLRenderPipelineDescriptor*p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[lib newFunctionWithName:@"filter_vs"];p.fragmentFunction=[lib newFunctionWithName:i?@"reference_fs":@"filter_fs"];p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
  pipelines[i]=[d newRenderPipelineStateWithDescriptor:p error:&error];if(!pipelines[i]){fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 2;}}
 MTLTextureDescriptor*t=[MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm size:256 mipmapped:YES];t.storageMode=MTLStorageModeShared;t.usage=MTLTextureUsageShaderRead;
 id<MTLTexture>checker=[d newTextureWithDescriptor:t],black=[d newTextureWithDescriptor:t];
 uint32_t*pixels=(uint32_t*)malloc(256*256*4);
 for(int pattern=0;pattern<2;++pattern)for(unsigned f=0;f<6;++f){for(unsigned y=0;y<256;++y)for(unsigned x=0;x<256;++x)pixels[y*256+x]=0xff000000u|(!pattern && ((x+y)&1)?0xffffffu:0);
  [(pattern?black:checker) replaceRegion:MTLRegionMake2D(0,0,256,256) mipmapLevel:0 slice:f withBytes:pixels bytesPerRow:1024 bytesPerImage:262144];}
 free(pixels);id<MTLCommandBuffer>cb=[q commandBuffer];id<MTLBlitCommandEncoder>b=[cb blitCommandEncoder];[b generateMipmapsForTexture:checker];[b generateMipmapsForTexture:black];[b endEncoding];[cb commit];[cb waitUntilCompleted];
 t=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:1 height:1 mipmapped:NO];t.storageMode=MTLStorageModeShared;
 id<MTLTexture>white=[d newTextureWithDescriptor:t];uint32_t wp=0xffffffff;[white replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&wp bytesPerRow:4];
 int errors=0;const unsigned w=32,h=4;
 for(int mode=0;mode<3;++mode){double se=0,sum=0,rsum=0,peak=0;unsigned count=0;
  unsigned sx=mode==1?128:16,sy=mode==1?1:16;
  for(int phase=0;phase<16;++phase){@autoreleasepool{
   Params params={};params.k[1]=(simd_float4){mode==1?1.f:0.f,mode==1?1.f:0.f,mode==1?1.f:0.f,0};params.k[2].x=mode==2?1:0;
   params.scene=(simd_float4){(phase+.5f)/16-.5f,.08f,(float)mode,0};
   id<MTLTexture>cube=mode==1?black:checker;
   id<MTLTexture>lo=draw(d,q,pipelines[0],cube,white,params,w,h),hi=draw(d,q,pipelines[1],cube,white,params,w*sx,h*sy);
   float*low=(float*)malloc(w*h*16),*high=(float*)malloc(w*sx*h*sy*16);
   [lo getBytes:low bytesPerRow:w*16 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
   [hi getBytes:high bytesPerRow:w*sx*16 fromRegion:MTLRegionMake2D(0,0,w*sx,h*sy) mipmapLevel:0];
   for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){if(mode==1 && (x<12 || x>=20))continue;
    for(unsigned c=0;c<3;++c){double ref=0;for(unsigned j=0;j<sy;++j)for(unsigned i=0;i<sx;++i)ref+=high[((y*sy+j)*w*sx+x*sx+i)*4+c]/(sx*sy);
     double value=low[(y*w+x)*4+c];if(!std::isfinite(value) || !std::isfinite(ref)){puts("non-finite shading");return 2;}
     se+=(value-ref)*(value-ref);sum+=value;rsum+=ref;peak=std::max(peak,ref);++count;}}
   free(low);free(high);
  }}
  double rms=sqrt(se/count)/peak,energy=fabs(sum/rsum-1);bool ok=rms<(mode==1?.18:.03) && (mode!=1 || energy<.10);
  errors+=!ok;printf("%s GPU %s phase=16 samples=%u nrmse=%.6f energy_error=%.6f\n",mode==1?"STRIP":mode==2?"PAINT CHECKER":"CHROME CHECKER",ok?"PASS":"FAIL",count,rms,energy);
 }
 return errors?1:0;
}}
'''

MSL = r'''
#include <metal_stdlib>
using namespace metal;
#define RW_CHROME 1
struct Params {float4 k[3],scene;};
struct In {float4 pos [[position]];float4 texcoord1,texcoord3,texcoord4,texcoord5,texcoord6;};
struct Out {float4 c0;};
vertex In filter_vs(uint id [[vertex_id]],constant Params&m [[buffer(0)]]){
 float2 xy=id==0?float2(-1,-1):id==1?float2(3,-1):float2(-1,3);
 In o;o.pos=float4(xy,0,1);o.texcoord1=float4(0);o.texcoord3=float4(0,0,1,0);o.texcoord6=float4(0,0,0,1);
 float mode=m.scene.z,phase=m.scene.x;
 o.texcoord4=float4(mode==1?float3(xy.x*16*m.scene.y+phase*m.scene.y,0,1):mode==2?float3(xy*.7f,1):float3(0,0,1),.75f);
 o.texcoord5=float4(mode==1?float3(0,0,1):float3(xy.x*.7f+phase/128,xy.y*.3f,1),0);
 return o;
}
'''


def strings(source):
    return ''.join(ast.literal_eval(t) for t in re.findall(r'"(?:\\.|[^"\\])*"', source))


def main():
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == '4823c78f084e500264db849e3d0fd942d915953ddc07f5dba1eb83806b51942a'
    optics = strings(re.search(r'static const char optics\[\]\s*=\s*(.*?);\s*\n', MODULE, re.S)[1])
    tail = strings(re.search(r'const char \*tail =\n(.*?)\n    size_t size', MODULE, re.S)[1]).split('#else\n')[0]
    # Keep the actual fragment expressions and derivative placement in this check.
    assert tail.index('dLdx=dfdx(Li)') < tail.index('if (nl>0.0f')
    assert tail.index('dDdx=dfdx(Di)') < tail.index('if (painted) diffuse')
    old_lod = tail.replace('gradientcube(dLdx,dLdy)', 'level(0)').replace('gradientcube(dDdx,dDdy)', 'level(0)')
    old_direct = tail.replace('rw_direct(N,V,L,dNdx,dNdy)', 'rw_direct(N,V,L)')
    assert old_lod != tail and old_direct != tail
    reference = old_lod.replace('rw_direct(N,V,L,dNdx,dNdy)', 'rw_direct(N,V,L)')

    def fragment(name, body):
        return f'''fragment float4 {name}(In pi [[stage_in]],constant Params&m [[buffer(0)]],
texturecube<float>tex1 [[texture(0)]],texture2d<float>tex0 [[texture(1)]],texture2d<float>nmap [[texture(2)]]) {{
constexpr sampler smp1(coord::normalized,address::clamp_to_edge,filter::linear,mip_filter::linear);
constexpr sampler smp0(coord::normalized,address::clamp_to_edge,filter::linear);
float4 tex=tex0.sample(smp0,pi.texcoord1.xy);float lit=1;
''' + body + '\n#endif\nreturn o.c0;\n}\n'

    with tempfile.TemporaryDirectory(prefix='chrome-filter-') as td:
        tmp = Path(td)
        (tmp / 'strip.cpp').write_text(CPU + optics + ORIGINAL + CPU_MAIN)
        subprocess.run(['clang++', '-O2', '-std=c++17', tmp / 'strip.cpp', '-o', tmp / 'strip'], check=True)
        good = subprocess.run([tmp / 'strip'], capture_output=True, text=True)
        bad = subprocess.run([tmp / 'strip', '--original'], capture_output=True, text=True)
        proof = good.stdout + bad.stdout
        print(good.stdout.strip(), flush=True); print(bad.stdout.strip(), flush=True)
        assert good.returncode == 0, good.stdout + good.stderr
        assert bad.returncode == 1 and 'STRIP CPU FAIL' in bad.stdout, bad.stdout + bad.stderr
        if not (os.environ.get('CODEX_GPU_CHECK') == '1' or os.environ.get('NFS_GPU_SANDBOX') == '1'):
            print('GPU PENDING: run_tests chrome_filter_test'); return
        (tmp / 'gpu.mm').write_text('#include <simd/simd.h>\n' + GPU)
        subprocess.run(['clang++', '-O2', '-std=c++17', '-fobjc-arc', '-framework', 'Metal', '-framework', 'Foundation',
                        tmp / 'gpu.mm', '-o', tmp / 'gpu'], check=True)
        for name, body in (('fixed', tail), ('level-zero', old_lod), ('unfiltered-direct', old_direct)):
            source = tmp / (name + '.metal')
            source.write_text(MSL + optics + fragment('filter_fs', body) + fragment('reference_fs', reference))
            result = subprocess.run([tmp / 'gpu', source], capture_output=True, text=True)
            proof += name + '\n' + result.stdout + result.stderr
            print(name, result.stdout.strip(), flush=True)
            if name == 'fixed': assert result.returncode == 0, result.stdout + result.stderr
            else:
                assert result.returncode == 1, result.stdout + result.stderr
                assert ('CHROME CHECKER GPU FAIL' if name == 'level-zero' else 'STRIP GPU FAIL') in result.stdout
        if os.environ.get('NFS_METAL_CACHE'):
            (Path(os.environ['NFS_METAL_CACHE']) / 'chrome-filter-proof.txt').write_text(proof)


if __name__ == '__main__':
    main()
