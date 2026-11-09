"""Road-pool production texture proof, with pinned measured pool from main.
Byte equality covers seven cars, seven colours and two chassis poses; Stronger
switches invalidate once and returning to Measured restores the exact payload.
Production 24,108,402 checks, zero failures; pinned main / omitted strength
cache each fail 294. Changed measured units differ in 98/98 payloads; good
Measured equals main 98/98, Stronger differs 98/98. Metal executes production
render.m depth states over uploaded pool texels: 0/1006 body/wheel pixels lit;
disabling depth lights 1006 each (2012 failures); visible lit 2938 / 3038.
The GPU result is retained in the Metal cache.
No game or emulation.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import underglow_light_test as light

OLD_POOL = '    x0-=1;y0-=1;x1+=1;y1+=1;\n    static UGStrip prior_mounts[UG_STRIPS];\n    static float prior_basis[16],prior_height=-100;static unsigned prior_preset;\n    int basis_changed=0;\n    for(int i=0;i<12;++i) basis_changed |= fabsf(prior_basis[i]-s->basis[i])>1e-4f;\n    if(prior_preset!=s->preset||fabsf(prior_height-local[2])>.002||basis_changed||memcmp(prior_mounts,s->mounts,sizeof prior_mounts)) {\n        float *pixels=(float *)(G+pool->mem[0]);\n        for(int y=0;y<POOL_H;++y)for(int x=0;x<POOL_W;++x) {\n            UGVec3 p={x0+(x1-x0)*(x+.5)/POOL_W,y0+(y1-y0)*(y+.5)/POOL_H,0};\n            rgb_lux(duties,strips,p,rgb);\n            double edge=fmin(fmin(fmin(p.x-x0,x1-p.x),fmin(p.y-y0,y1-p.y))/.25,1);\n            float *v=pixels+4*(y*POOL_W+x);\n            for(int k=0;k<3;++k)v[k]=rgb[k]*.18/(M_PI*100)*fmax(0,edge);v[3]=1;\n        }\n        pool->dirty=1;prior_preset=s->preset;prior_height=local[2];memcpy(prior_basis,s->basis,64);memcpy(prior_mounts,s->mounts,sizeof prior_mounts);\n    }\n'
OLD_POOL_SHA = '6652ba90cfd85bdbd1f80cf993d36a5c6a63138a7d6a505c4d7e754c82676343'


DRIVER = r'''
#include <CommonCrypto/CommonDigest.h>
#include "underglow_pool.h"
static unsigned broad_calls;
static double counted_broad(const UGStrip *s,UGVec3 p){++broad_calls;return ug_pool_broad(s,p);}
#include "light.m"
static void digest(const void *bytes,size_t n,char out[65]) {
 unsigned char d[32];CC_SHA256(bytes,(CC_LONG)n,d);
 for(unsigned i=0;i<32;++i)sprintf(out+2*i,"%02x",d[i]);
}
static double luminance(double x,double y) {
 Obj *o=&objects[1];float *v=(float *)(G+o->mem[0]);
 double u=(x-pool_quad[0])/(pool_quad[4]-pool_quad[0])*o->w-.5;
 double w=(y-pool_quad[1])/(pool_quad[13]-pool_quad[1])*o->h-.5;
 int ix=(int)floor(u),iy=(int)floor(w);
 if(ix<0||iy<0||ix+1>=(int)o->w||iy+1>=(int)o->h)return 0;
 double L=0,weights[3]={.2126729,.7151522,.0721750};
 for(int j=0;j<2;++j)for(int i=0;i<2;++i)for(int k=0;k<3;++k)
  L+=weights[k]*v[4*((iy+j)*o->w+ix+i)+k]*(i?u-ix:1-u+ix)*(j?w-iy:1-w+iy)*100;
 return L;
}
int main(void) {
 SETUP
 /* The same horizontal road plane and depth lift as the queued road path.
  * The existing production light test separately drives that queue. */
 G[self+0x78]=1;
 for(unsigned car=0;car<7;++car)for(unsigned preset=1;preset<8;++preset)for(unsigned pose=0;pose<2;++pose) {
  M32(0x925E90)=0;load_fixture(car);saved_preset=preset;
  float theta=pose?.47f:0;
  MF32(0x52000)=MF32(0x52014)=cosf(theta);MF32(0x52004)=sinf(theta);MF32(0x52010)=-sinf(theta);
  MF32(0x52108)=car==6?.08f:.25f;
  underglow_light_begin(&c,&capture);CHECK(capture.valid);
  M32(0x925E90)=6;
  size_t bytes=POOL_W*POOL_H*16;
  float *measured=malloc(bytes);CHECK(measured!=NULL);
  char emitter_before[65],pool_before[65],quad_before[65];
  unsigned ba=allocs;
  for(unsigned strength=0;strength<3;++strength) {
   saved_strength=strength==1;objects[1].dirty=0;
   ug_draw(&c,&capture);CHECK(strips%4==0 && pools>0);
   if(ba)CHECK(allocs==ba);else ba=allocs;
   CHECK(objects[0].ground_decal && objects[1].ground_decal);
   for(unsigned j=0;j<4;++j)CHECK(fabs(pool_quad[4*j+2]-.002)<1e-8);
   char em[65],po[65],qu[65],pq[65];
   digest(G+objects[0].mem[0],16,em);digest(G+objects[1].mem[0],bytes,po);
   digest(emitted,sizeof emitted,qu);
   digest(pool_quad,sizeof pool_quad,pq);
   if(!strength) {
    strcpy(emitter_before,em);strcpy(pool_before,po);strcpy(quad_before,qu);
    memcpy(measured,G+objects[1].mem[0],bytes);
   } else {
    CHECK(!strcmp(emitter_before,em));CHECK(!strcmp(quad_before,qu));
    CHECK(objects[1].dirty);
    if(strength==1)CHECK(strcmp(pool_before,po));
    else CHECK(!memcmp(measured,G+objects[1].mem[0],bytes));
   }
   printf("digest|%u|%u|%u|%u|%s|%s|%s|%s\n",car,preset,pose,strength,em,po,qu,pq);
   if(car==1 && preset==5 && !pose && strength<2) {
    const char *directory=getenv("UG_ROAD_PIXELS");
    if(directory) {
     char path[4096];snprintf(path,sizeof path,"%s/pool%u.bin",directory,strength);
     FILE *f=fopen(path,"wb");CHECK(f!=NULL);
     if(f){CHECK(fwrite(G+objects[1].mem[0],1,bytes,f)==bytes);CHECK(!fclose(f));}
    }
    const double offsets[]={.1,.3,.6,1.0};
    for(unsigned d=0;d<4;++d) {
     double L=luminance(fixtures[car].lo[0]-offsets[d],0);
     printf("patch|%u|%.1f|%.9f|sun=%.9f|shade=%.9f\n",strength,offsets[d],L,
      1+L/(.18*100000/M_PI),1+L/(.18*10000/M_PI));
     /* Retail close/far stationary chase targets decoded in the camera spec:
      * eye relative to the car origin; the patch is behind the rear bound.
      * Diffuse radiance has no extra inverse-square loss to the camera. */
     if(d==1)printf("chase patch ranges close=%.6f far=%.6f m\n",
      hypot(4.7+fixtures[car].lo[0]-offsets[d],1.62+MF32(0x52108)),
      hypot(5.436+fixtures[car].lo[0]-offsets[d],1.7+MF32(0x52108)));
    }
   }
   objects[1].dirty=0;unsigned bc=broad_calls;
   ug_draw(&c,&capture);
   CHECK(!objects[1].dirty && broad_calls==bc && allocs==ba);
  }
  free(measured);
 }
 /* Off returns before any new work, for either strength. */
 for(unsigned strength=0;strength<2;++strength) {
  saved_strength=strength;saved_light=0;
  unsigned bd=draws,ba=allocs,bc=broad_calls;ug_draw(&c,&capture);
  CHECK(draws==bd && allocs==ba && broad_calls==bc);
 }
 printf("checks=%u failures=%u broad_calls=%u allocations=%u\n",checks,failures,broad_calls,allocs);
 return !!failures;
}
'''


def checked(command, **kwargs):
    result=subprocess.run(command,capture_output=True,text=True,**kwargs)
    assert result.returncode==0,result.stderr[-3000:]
    return result


METAL = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <math.h>
#include <stdio.h>
typedef int Map;
static Map dsstates;
static id<MTLDevice> mtl;
static struct {int ds;} dev;
static int diag_on,ad1_nds,ad1_ds_has[512];
static uint64_t ad1_ds_key[512];
static struct {unsigned ds_calls,ds_mismatch;} ad1;
static void *map_get(Map *m,uint64_t k){return NULL;}
static void map_put(Map *m,uint64_t k,id v){}
DEPTH_FUNCTIONS
static int blocked(unsigned x,unsigned y) {
 double u=(x+.5)/64,v=(y+.5)/64;
 return (u>.25&&u<.75&&v>.25&&v<.65)||hypot(u-.3,v-.7)<.09||hypot(u-.7,v-.7)<.09;
}
int main(int argc,char **argv){@autoreleasepool {
 mtl=MTLCreateSystemDefaultDevice();if(!mtl)return 3;
 NSError *error=nil;
 NSString *text=@"#include <metal_stdlib>\nusing namespace metal;\n"
 "struct V{float4 p[[position]];float2 uv;};\n"
 "vertex V vs(uint i[[vertex_id]]){float2 p=float2(i==1?3:-1,i==2?3:-1);return {float4(p,.7,1),float2((p.x+1)*.5,(1-p.y)*.5)};}\n"
 "struct O{float4 c[[color(0)]];float z[[depth(any)]];};\n"
 "fragment O opaque(V v[[stage_in]]){float2 q=v.p.xy/64;bool b=(q.x>.25&&q.x<.75&&q.y>.25&&q.y<.65)||distance(q,float2(.3,.7))<.09||distance(q,float2(.7,.7))<.09;return {float4(.25,.25,.25,1),b?.3f:.8f};}\n"
 "fragment float4 light(V v[[stage_in]],texture2d<float> t[[texture(0)]]){constexpr sampler s(filter::linear,address::clamp_to_edge);return float4(t.sample(s,v.uv).rgb,0);}";
 id<MTLLibrary> lib=[mtl newLibraryWithSource:text options:nil error:&error];
 if(!lib){fprintf(stderr,"%s\n",error.description.UTF8String);return 2;}
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[lib newFunctionWithName:@"vs"];pd.fragmentFunction=[lib newFunctionWithName:@"opaque"];
 pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;
 id<MTLRenderPipelineState> opaque=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
 pd.fragmentFunction=[lib newFunctionWithName:@"light"];
 pd.colorAttachments[0].blendingEnabled=YES;
 pd.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorOne;pd.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorOne;
 pd.colorAttachments[0].sourceAlphaBlendFactor=MTLBlendFactorZero;pd.colorAttachments[0].destinationAlphaBlendFactor=MTLBlendFactorOne;
 id<MTLRenderPipelineState> light=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!opaque||!light){fprintf(stderr,"%s\n",error.description.UTF8String);return 2;}
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:64 height:64 mipmapped:NO];
 td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
 id<MTLTexture> target=[mtl newTextureWithDescriptor:td];
 td.pixelFormat=MTLPixelFormatDepth32Float;td.storageMode=MTLStorageModePrivate;
 id<MTLTexture> depth=[mtl newTextureWithDescriptor:td];
 td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:256 height:160 mipmapped:NO];
 td.usage=MTLTextureUsageShaderRead;td.storageMode=MTLStorageModeShared;
 id<MTLTexture> pool=[mtl newTextureWithDescriptor:td];
 float *pixels=calloc(256*160*4,sizeof(float));
 _Float16 result[64*64*4];unsigned failures=0,hidden=0;
 for(unsigned mode=0;mode<3;++mode){
  if(mode){char path[4096];snprintf(path,sizeof path,"%s/pool%u.bin",argv[1],mode-1);
   FILE*f=fopen(path,"rb");if(!f)return 2;size_t n=fread(pixels,16,256*160,f);fclose(f);if(n!=256*160)return 2;}
  [pool replaceRegion:MTLRegionMake2D(0,0,256,160) mipmapLevel:0 withBytes:pixels bytesPerRow:256*16];
  MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
  pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  pass.depthAttachment.texture=depth;pass.depthAttachment.loadAction=MTLLoadActionClear;pass.depthAttachment.clearDepth=1;pass.depthAttachment.storeAction=MTLStoreActionDontCare;
  id<MTLCommandBuffer> cb=[[mtl newCommandQueue] commandBuffer];id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:pass];
  MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionAlways;dd.depthWriteEnabled=YES;
  [enc setDepthStencilState:[mtl newDepthStencilStateWithDescriptor:dd]];
  [enc setRenderPipelineState:opaque];[enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
  uint32_t rs[256]={0};rs[7]=1;rs[23]=4;dev.ds=1;
  [enc setDepthStencilState:depth_state_for(rs)];[enc setRenderPipelineState:light];[enc setFragmentTexture:pool atIndex:0];
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];[cb commit];[cb waitUntilCompleted];
  if(cb.status!=MTLCommandBufferStatusCompleted)return 2;
  [target getBytes:result bytesPerRow:64*8 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
  unsigned leaks=0,lit=0;
  for(unsigned y=0;y<64;++y)for(unsigned x=0;x<64;++x){
   unsigned i=4*(y*64+x);double v=(double)result[i]+result[i+1]+result[i+2];
   if(blocked(x,y)){if(!mode)++hidden;if(v!=.75)++leaks;}else if(v>.75)++lit;
  }
  printf("depth mode=%u forbidden=%u leaks=%u visible-lit=%u\n",mode,hidden,leaks,lit);
  failures+=leaks;if(mode&&!lit)++failures;if(!mode&&lit)++failures;
 }
 free(pixels);printf("depth failures=%u\n",failures);return !!failures;
}}
'''


def depth_check(tmp):
    source=(ROOT/'runtime/render.m').read_text()
    fragment=light.function_fragment(source,'static MTLCompareFunction cmp_fn(')+'\n'+light.function_fragment(source,'static MTLStencilOperation stencil_op(')+'\n'+light.function_fragment(source,'static id<MTLDepthStencilState> depth_state_for(')
    evidence=[]
    for name,functions in [('depth',fragment),('depth-disabled',fragment.replace('zen ? cmp_fn(rs[23]) : MTLCompareFunctionAlways','MTLCompareFunctionAlways'))]:
        assert name=='depth' or functions!=fragment
        path=tmp/(name+'.m');path.write_text(METAL.replace('DEPTH_FUNCTIONS',functions))
        exe=tmp/name
        checked(['clang','-O1','-fobjc-arc',str(path),'-framework','Foundation','-framework','Metal','-o',str(exe)])
        cp=subprocess.run([str(exe),str(tmp)],capture_output=True,text=True)
        print(name,cp.stdout.strip(),flush=True)
        evidence.append(name+'\n'+cp.stdout)
        if cp.returncode==3:
            print('GPU PENDING: run through run_tests on Metal host');return False
        assert cp.returncode==int(name!='depth'),cp.stderr[-3000:]
    cache=os.environ.get('NFS_METAL_CACHE')
    if cache:
        (Path(cache)/'underglow-road-depth.txt').write_text('\n'.join(evidence))
    return True


def main():
    fixtures=light.fixtures()
    if fixtures is None:
        print('SKIP: stock geometry required');return 3
    assert hashlib.sha256(OLD_POOL.encode()).hexdigest()==OLD_POOL_SHA
    source=(ROOT/'runtime/underglow_light.m').read_text()
    begin=source.index('    unsigned strength=underglow_pool_strength();')
    end=source.index('    /* Submit before the queued opaque chassis:',begin)
    baseline=source[:begin]+OLD_POOL+source[end:]
    diag=(ROOT/'runtime/diag.c').read_text()
    wrapper=diag[diag.index('\nunsigned diag_car1[4];')+1:diag.index('void f_007387E0_orig')]
    prefix=light.HARNESS.split('int main(int argc,char **argv)')[0]
    # This driver covers every preset; the original harness is Green-only.
    prefix=prefix.replace('CHECK(v[1]>10&&v[1]>v[0]&&v[1]>v[2]);','CHECK(v[0]+v[1]+v[2]>0);')
    prefix=prefix.replace('CHECK(sum>100);','CHECK(sum>=0);')
    setup=' G=calloc(1,16*1024*1024);'+light.HARNESS.split(' G=calloc(1,16*1024*1024);',1)[1].split(' f_0074E160(&c);CHECK(original_calls==1);',1)[0]
    driver=prefix.replace('FIXTURES',fixtures).replace('PRODUCTION_WRAPPER',wrapper)+DRIVER.replace('SETUP',setup)
    variants={'good':source,'main-pool':baseline,
              'measured-changed':source.replace('rgb[k]*.18/(M_PI*100)','rgb[k]*.19/(M_PI*100)'),
              'strength-cache-omitted':source.replace('prior_strength!=strength||','')}
    with tempfile.TemporaryDirectory(prefix='ug-road-') as td:
        tmp=Path(td)
        (tmp/'driver.m').write_text(driver)
        (tmp/'diag_car_core.c').write_text((ROOT/'runtime/diag_car.c').read_text().split('// BOOT_DIAG cartex:')[0])
        render=tmp/'render.o'
        checked(['clang','-O1','-Iruntime','-Irecomp','-DUG_RENDER_CORE_ONLY','-c','runtime/underglow_render.m','-o',str(render)],cwd=ROOT)
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0');env.pop('NFS_UNDERGLOW',None)
        results={}
        for name,code in variants.items():
            assert name=='good' or code!=source
            code=code.replace('double broad=ug_pool_broad(strips,p);','double broad=counted_broad(strips,p);')
            (tmp/'light.m').write_text(code)
            exe=tmp/name
            checked(['clang','-O1','-g','-fsanitize=address,undefined','-Wno-deprecated-declarations',
                     '-Iruntime','-Irecomp',str(tmp/'driver.m'),str(render),'runtime/underglow_photometry.c',
                     '-framework','Metal','-framework','Foundation','-o',str(exe)],cwd=ROOT)
            env.pop('UG_ROAD_PIXELS',None)
            if name=='good':env['UG_ROAD_PIXELS']=str(tmp)
            cp=subprocess.run([str(exe)],env=env,capture_output=True,text=True)
            print(name,cp.stdout.splitlines()[-1],flush=True)
            assert cp.returncode==int(name in ('main-pool','strength-cache-omitted')),cp.stderr[-3000:]
            results[name]={tuple(row.split('|')[1:5]):row.split('|')[5:]
                           for row in cp.stdout.splitlines() if row.startswith('digest|')}
            if name=='good':
                for row in cp.stdout.splitlines():
                    if row.startswith(('patch|','chase patch')):print(row)
        baseline=results['main-pool'];good=results['good']
        measured=[key for key in good if key[-1]=='0']
        identical=sum(good[key]==baseline[key] for key in measured)
        changed=sum(results['measured-changed'][key]!=baseline[key] for key in measured)
        stronger=sum(good[key[:-1]+('1',)]!=good[key] for key in measured)
        print(f'Measured identical to pinned main pool: {identical}/{len(measured)}; changed-units control differs {changed}; Stronger differs {stronger}')
        assert identical==changed==stronger==len(measured)==98
        if not depth_check(tmp):return 3
    return 0


if __name__=='__main__':
    raise SystemExit(main())
