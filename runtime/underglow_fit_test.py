"""Independent top-down triangle silhouette, 31 samples/strip, <=0.05 m outside.
Supplied original reference: Carrera GT 2.57 m, M3 GTR 4.25 m, XK 4.80 m
(0.88 m with mirrors omitted). Measured here with full skin and endpoints:
2.566129 / 4.246774 / 4.884516 m; mirrors are included in the XK bounds.
Wheel-only control: 137/138 pass, CEMTR fails 1.740645 m. Its decoded narrow
frame inset restores 138/138; removing it must reproduce that failure. Limit is
unchanged: its PlayerUsable=1 roster entry is never omitted. The check covers
all 124 player models + 14 replacements; failures always carry car names.
Pulse: 42/42 real road VS/PS GPU cases on UNORM and float targets, dim/bright .301960796; ignoring colour
fails 1/42 (ratio 1); old alpha fails on UNORM (ratio .603922). Mathematical factors 1/.30/1, Off 1.
Logical optical strips stay four; visible emitter submissions change 4 -> 0.
The old mounting function is SHA-256 pinned; no repository history is read.
"""
from pathlib import Path
import hashlib
import re
import struct
import subprocess
import sys
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import underglow_cars_test as cars
import underglow_light_test as light

OLD_MOUNTS = 'static int car_mounts(uint32_t self,UGStrip out[UG_STRIPS]) {\n    uint32_t layout=M32(self+0x17b4);\n    if(!layout || M16(layout+2)<4 || M16(layout+0xd6)<4) return 0;\n    double lo[3],hi[3];\n    for(unsigned k=0;k<3;++k) {\n        lo[k]=MF32(self+0x90+4*k);hi[k]=MF32(self+0xa0+4*k);\n        if(!isfinite(lo[k])||!isfinite(hi[k])||hi[k]<=lo[k]||fabs(lo[k])>20||fabs(hi[k])>20) return 0;\n    }\n    double rear=-INFINITY,front=INFINITY,cross=INFINITY,wheel_z=0;\n    double xmin=INFINITY,xmax=-INFINITY,ymin=INFINITY,ymax=-INFINITY,rmax=0;\n    for(unsigned i=0;i<4;++i) {\n        double x=MF32(layout+0x10+16*i),y=MF32(self+0x110+4*i);\n        double z=MF32(layout+0x18+16*i),radius=MF32(layout+0x1c+16*i),width=MF32(layout+0xdc+4*i);\n        if(!isfinite(x)||!isfinite(y)||!isfinite(z)||!isfinite(radius)||!isfinite(width)||\n           fabs(x)>20||fabs(y)>20||fabs(z)>20||radius<.1||radius>2||width<.05||width>2) return 0;\n        wheel_z+=z*.25;\n        xmin=fmin(xmin,x);xmax=fmax(xmax,x);ymin=fmin(ymin,y);ymax=fmax(ymax,y);rmax=fmax(rmax,radius);\n        if(i<2) front=fmin(front,x-radius-.05); else rear=fmax(rear,x+radius+.05);\n        cross=fmin(cross,fabs(y)-width*.5-.05);\n    }\n    /* Body overhang may extend half an axle spacing past the tyre envelope.\n     * This generous envelope admits wide kits, but not corrupt added-car\n     * bounds that would mount emitters metres away from normal wheels. */\n    double base=xmax-xmin,track=ymax-ymin;\n    if(base<.2 || track<.2 || lo[0]<xmin-rmax-base*.5 || hi[0]>xmax+rmax+base*.5 ||\n       lo[1]<ymin-track*.5 || hi[1]>ymax+track*.5 ||\n       hi[0]-lo[0]<base*.5 || hi[1]-lo[1]<track*.5 ||\n       lo[2]>wheel_z+rmax || hi[2]<wheel_z-rmax ||\n       lo[2]<wheel_z-track || hi[2]>wheel_z+base) {\n        if(diag_on) diag_event("underglow mounts rejected: inconsistent bounds/wheels self=%08X body=%.3fx%.3f base=%.3f track=%.3f",self,hi[0]-lo[0],hi[1]-lo[1],base,track);\n        return 0;\n    }\n    rear=fmax(rear,lo[0]+.1);front=fmin(front,hi[0]-.1);\n    /* A global minimum can be an exhaust/bumper vertex below the sill.\n     * Use the wheel-centre plane as the underside datum, bounded by this\n     * chassis. This remains chassis-local, independent of the road plane. */\n    double left=hi[1]-.05,right=lo[1]+.05;\n    double z=fmax(lo[2]-.005,fmin(hi[2]-.005,wheel_z-.01));\n    cross=fmin(cross,fmin(left,-right));\n    if(front-rear<.2 || left-right<.2 || cross<.1 || hi[0]-lo[0]<1) return 0;\n    UGVec3 a[4]={{rear,right,z},{rear,left,z},{hi[0]-.1,-cross,z},{lo[0]+.1,-cross,z}};\n    UGVec3 b[4]={{front,right,z},{front,left,z},{hi[0]-.1,cross,z},{lo[0]+.1,cross,z}};\n    for(unsigned i=0;i<UG_STRIPS;++i)\n        out[i]=(UGStrip){a[i],b[i],{0,0,-1},hypot(b[i].x-a[i].x,b[i].y-a[i].y),.01};\n    return 1;\n}'
OLD_SHA = '955d47c60f3116a8bbbf5fcd1ffa23bcb491b6e3e9829a59937171f98af41d3a'


def geometry(row, data, wheel, lo, hi):
    import carbon_car as cc
    import uc_car
    import v25_car
    tag, name = row['game'], row['folder']
    parts = ('BODY', 'DAMAGE0_BODY', 'FRONT_BUMPER', 'REAR_BUMPER',
             'SKIRT', 'DOOR_LEFT', 'DOOR_RIGHT')
    if tag in ('P','U'):
        parts=tuple(dict.fromkeys(parts+v25_car.BODY_PARTS+('HOOD','BASE','CHASSIS')))
    suffixes = ['_KIT00_' + part + '_A' for part in parts]
    # Mirrors/spoilers belong to the retail bounds union, not the body skin.
    bounds_suffixes = ['_KIT00_' + part + '_A' for part in
                       ('LEFT_SIDE_MIRROR', 'RIGHT_SIDE_MIRROR', 'SPOILER', 'SIDEMIRROR_LEFT', 'SIDEMIRROR_RIGHT', 'SPOILER_LIP')]
    wanted = {cc.name_hash(name + s) for s in suffixes}
    bounds_wanted = wanted | {cc.name_hash(name+s) for s in bounds_suffixes}
    triangles, lows, highs = [], [], []
    for entry in cc.index(data, 0x134004):
        if entry[0] not in bounds_wanted:
            continue
        solid = (uc_car.unpack_entry if tag=='U' else cc.unpack_entry)(data, entry)
        h = cc.payload(solid, 0x134011, 16)
        lows.append(struct.unpack_from('<3f', h, 32))
        highs.append(struct.unpack_from('<3f', h, 48))
        if entry[0] not in wanted:
            continue
        if tag in ('U', 'P'):
            model = (uc_car if tag=='U' else v25_car).source_model(solid)
            draws = model['draws']
        elif tag=='C':
            draws = [cc.model(solid)]
        else:
            # MW bodies share the native 36-byte position/normal/colour/UV
            # layout; unlike native_wheel_model, body effects need no waiver.
            mesh = cc.payload(solid, 0x134900, 16)
            draws = [dict(vertices=np.frombuffer(cc.payload(solid,0x134b01,128),np.uint8).reshape(-1,36),
                          indices=np.frombuffer(cc.payload(solid,0x134b03,16)[:cc.u32(mesh,44)*2],'<u2'))]
        for draw in draws:
            xyz=draw['vertices'].copy().view('<f4').reshape(-1,9)[:,:3]
            ix=draw['indices'].astype(int).reshape(-1,3)
            triangles.append(xyz[ix][:,:,:2])
    assert triangles, (tag,name,'no independent body triangles')
    label=tag+'/'+name.lower()
    SILHOUETTES.append((label,np.concatenate(triangles)))
    return np.min(lows,axis=0),np.max(highs,axis=0)


SILHOUETTES=[]


def outside(strips, triangles):
    # At each longitudinal sample, intersect the model's projected edges.
    # The body silhouette fills the skin's interior even on cars without a
    # modelled floor pan. No convex hull bridges longitudinal bumper corners.
    edges=np.concatenate([triangles[:,[i,(i+1)%3],:] for i in range(3)])
    start=edges[:,0];delta=edges[:,1]-start
    total=0.
    for strip in strips:
        p,q=np.array(strip[:3]),np.array(strip[3:])
        missing=0
        for t in np.linspace(0,1,31):
            x,y=p[:2]+t*(q[:2]-p[:2])
            active=(x>=np.minimum(edges[:,0,0],edges[:,1,0])-1e-8)&(x<=np.maximum(edges[:,0,0],edges[:,1,0])+1e-8)
            # Edges parallel to the sample plane contribute both endpoints.
            parallel=active&(np.abs(delta[:,0])<1e-10)
            crossing=active&~parallel
            ys=start[crossing,1]+(x-start[crossing,0])*delta[crossing,1]/delta[crossing,0]
            ys=np.concatenate([ys,edges[parallel,:,1].ravel()])
            missing+=not (len(ys) and ys.min()-1e-7<=y<=ys.max()+1e-7)
        total+=np.linalg.norm(q-p)*missing/31
    return total


PULSE_GPU = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "sm_translate.h"
#include "underglow_light.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
uint8_t *G;
static uint32_t scratch=0x1000;
static uint64_t now_ns;
static unsigned pulse_on;
uint64_t host_ticks(void){return now_ns;}
unsigned underglow_pulse(void){return pulse_on;}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
 if(fn==0x006C0A30u){memset(G+c->ecx,0,0x94);return 0;}
 if(fn!=0x006BE750u||n!=5)abort();
 return 0;
}
PRODUCTION_QUAD
static unsigned checks,failures;
#define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"pulse FAIL %d: %s\n",__LINE__,#x);}}while(0)
static id<MTLLibrary> shader(id<MTLDevice> d,const char *path) {
 NSData *b=[NSData dataWithContentsOfFile:[NSString stringWithUTF8String:path]];
 if(!b)abort();SmResult out={};SmOptions opt;sm_default_options(&opt);
 if(sm_translate((const uint32_t *)b.bytes,b.length/4,&opt,&out))abort();
 NSError *error=nil;
 id<MTLLibrary> lib=[d newLibraryWithSource:[NSString stringWithUTF8String:out.msl] options:nil error:&error];
 if(!lib){fprintf(stderr,"%s\n",error.description.UTF8String);abort();}
 sm_result_free(&out);return lib;
}
int main(int argc,char **argv) {@autoreleasepool {
 G=(uint8_t *)calloc(1,0x10000);Cpu c={};UGVec3 p[4]={};
 const double times[]={0,1,2};float levels[4];
 for(unsigned k=0;k<3;++k) {
  pulse_on=1;now_ns=(uint64_t)(times[k]*1e9);quad(&c,0,p,0,NULL);
  double want=k==1?.30:1;
  CHECK(fabs(pulse_factor(1,times[k])-want)<1e-12);
  levels[k]=(M32(scratch+0x580)&255)/255.f;
  CHECK((M32(scratch+0x580)>>24)==255);
  CHECK(fabs(levels[k]-want)<=1./255);
 }
 pulse_on=0;now_ns=1000000000;quad(&c,0,p,0,NULL);
 levels[3]=(M32(scratch+0x580)&255)/255.f;
 for(double t=0;t<3;t+=.125)CHECK(pulse_factor(0,t)==1);
 CHECK(levels[3]==1);
 id<MTLDevice> d=MTLCreateSystemDefaultDevice();if(!d){puts("SKIP: Metal device required for pulse effect proof");return 3;}
 id<MTLLibrary> vs=shader(d,argv[1]),ps=shader(d,argv[2]);
 MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
 pd.vertexFunction=[vs newFunctionWithName:@"sm_main"];pd.fragmentFunction=[ps newFunctionWithName:@"sm_main"];
 MTLPixelFormat format=!strcmp(argv[4],"unorm")?MTLPixelFormatRGBA8Unorm:MTLPixelFormatRGBA32Float;
 pd.colorAttachments[0].pixelFormat=format;
 pd.colorAttachments[0].blendingEnabled=YES;
 pd.colorAttachments[0].sourceRGBBlendFactor=MTLBlendFactorSourceAlpha;
 pd.colorAttachments[0].destinationRGBBlendFactor=MTLBlendFactorOne;
 MTLVertexDescriptor *vd=[MTLVertexDescriptor vertexDescriptor];
 for(unsigned k=0;k<3;++k){vd.attributes[k].format=MTLVertexFormatFloat4;vd.attributes[k].offset=k*16;vd.attributes[k].bufferIndex=0;}
 vd.layouts[0].stride=48;pd.vertexDescriptor=vd;
 NSError *error=nil;id<MTLRenderPipelineState> pipeline=[d newRenderPipelineStateWithDescriptor:pd error:&error];
 if(!pipeline){fprintf(stderr,"%s\n",error.description.UTF8String);return 1;}
 MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
 id<MTLTexture> tex=[d newTextureWithDescriptor:td];
 td.pixelFormat=format;id<MTLTexture> target=[d newTextureWithDescriptor:td];
 const float pixels[4]={.125,.5,.0625,1};[tex replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:pixels bytesPerRow:16];
 MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];id<MTLSamplerState> sampler=[d newSamplerStateWithDescriptor:sd];
 id<MTLCommandQueue> queue=[d newCommandQueue];float output[4][4];
 for(unsigned k=0;k<4;++k) {
  float vertices[3][12]={};
  for(unsigned j=0;j<3;++j) {
   vertices[j][0]=j==1?3:-1;vertices[j][1]=j==2?3:-1;vertices[j][3]=1;
   for(unsigned channel=4;channel<8;++channel)vertices[j][channel]=1;
   vertices[j][8]=vertices[j][9]=.5;vertices[j][11]=1;
   for(unsigned channel=4;channel<7;++channel)
    vertices[j][channel]=strcmp(argv[3],"good")?1:levels[k];
   vertices[j][7]=!strcmp(argv[3],"original-alpha")?levels[k]:1;
  }
  float constants[256][4]={};for(unsigned j=0;j<4;++j)constants[j][j]=1;
  int integers[16][4]={},booleans[16]={};SmVSAux va={};SmPSAux pa={};
  id<MTLCommandBuffer> cb=[queue commandBuffer];MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
  rp.colorAttachments[0].texture=target;rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
  rp.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
  id<MTLRenderCommandEncoder> enc=[cb renderCommandEncoderWithDescriptor:rp];
  [enc setRenderPipelineState:pipeline];[enc setVertexBytes:vertices length:sizeof vertices atIndex:0];
  [enc setVertexBytes:constants length:sizeof constants atIndex:16];[enc setVertexBytes:integers length:sizeof integers atIndex:17];
  [enc setVertexBytes:booleans length:sizeof booleans atIndex:18];[enc setVertexBytes:&va length:sizeof va atIndex:19];
  [enc setFragmentBytes:constants length:sizeof constants atIndex:0];[enc setFragmentBytes:integers length:sizeof integers atIndex:1];
  [enc setFragmentBytes:booleans length:sizeof booleans atIndex:2];[enc setFragmentBytes:&pa length:sizeof pa atIndex:3];
  [enc setFragmentTexture:tex atIndex:0];[enc setFragmentSamplerState:sampler atIndex:0];
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];[cb commit];[cb waitUntilCompleted];
  CHECK(cb.status==MTLCommandBufferStatusCompleted);
  if(format==MTLPixelFormatRGBA32Float) {
   [target getBytes:output[k] bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
  } else {
   uint8_t pixel[4];[target getBytes:pixel bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
   for(unsigned j=0;j<4;++j)output[k][j]=pixel[j]/255.f;
  }
 }
 CHECK(output[0][1]>.1);
 CHECK(fabs(output[1][1]/output[0][1]-.3)<=1./255);
 CHECK(fabs(output[2][1]/output[0][1]-1)<1e-6);
 CHECK(fabs(output[3][1]/output[0][1]-1)<1e-6);
 printf("pulse effect=%s/%s bright=%.9f dim=%.9f ratio=%.9f t=2/off=%.9f/%.9f checks=%u failures=%u\n",argv[3],argv[4],output[0][1],output[1][1],output[1][1]/output[0][1],output[2][1],output[3][1],checks,failures);
 free(G);return failures?1:0;
}}
'''


def pulse_proof(tmp, source):
    shaders=ROOT/'host/shaders/testdata/worldprelit'
    paths=[shaders/'t0_world_p0_vs_1_1.bin',shaders/'t0_world_p0_ps_1_1.bin']
    # Execute the retail effect 4 VS/PS, not an invented multiplying shader.
    if not all(p.is_file() for p in paths):
        print('SKIP: retail FE shader fixtures required');return 3
    functions=light.function_fragment(source,'static double pulse_factor(')+'\n'+light.function_fragment(source,'static void quad(')
    unit=tmp/'pulse.mm';exe=tmp/'pulse'
    unit.write_text(PULSE_GPU.replace('PRODUCTION_QUAD',functions))
    cp=subprocess.run(['clang++','-std=c++17','-O1','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'host/shaders'),str(unit),str(ROOT/'host/shaders/sm_parse.cpp'),str(ROOT/'host/shaders/sm_msl.cpp'),'-framework','Foundation','-framework','Metal','-o',str(exe)],capture_output=True,text=True)
    assert cp.returncode==0,cp.stderr[-3000:]
    for mode,format in [('good','unorm'),('good','float'),('ignore-colour','unorm'),('ignore-colour','float'),('original-alpha','unorm')]:
        cp=subprocess.run([str(exe),*map(str,paths),mode,format],capture_output=True,text=True)
        print(cp.stdout.strip(),flush=True)
        if cp.returncode==3:return 3
        assert cp.returncode==int(mode!='good'),cp.stderr[-2000:]
    return 0


def main():
    assert hashlib.sha256(OLD_MOUNTS.encode()).hexdigest()==OLD_SHA
    result=cars.player_fixtures(geometry)
    if result is None:
        print('SKIP: complete player geometry inputs required');return 3
    fixtures,stock,added,swaps=result
    source=(ROOT/'runtime/underglow_light.m').read_text()
    mounts=light.function_fragment(source,'static int car_mounts(')
    driver=r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "underglow_light.h"
uint8_t *G;int diag_on;
void diag_event(const char *fmt,...){}
FIXTURES
MOUNTS
int main(void) {
 G=calloc(1,0x40000);uint32_t self=0x1000,layout=0x20000;
 M32(self+0x17b4)=layout;M16(layout+2)=M16(layout+0xd6)=4;
 for(unsigned n=0;n<sizeof fixtures/sizeof fixtures[0];++n) {
  const struct CarFixture *f=&fixtures[n];
  M32(self+0x88)=0x30000;strcpy((char *)G+0x30000,strchr(f->name,'/')+1);
  for(int k=0;k<3;++k){MF32(self+0x90+4*k)=f->lo[k];MF32(self+0xa0+4*k)=f->hi[k];}
  for(int k=0;k<16;++k)MF32(layout+0x10+4*k)=f->wheels[k];
  for(int k=0;k<4;++k){MF32(self+0x110+4*k)=-f->wheels[4*k+1];MF32(layout+0xdc+4*k)=f->widths[k];}
  UGStrip s[4];int ok=car_mounts(self,s);printf("%s %d",f->name,ok);
  if(ok)for(int k=0;k<4;++k)printf(" %.9g %.9g %.9g %.9g %.9g %.9g",s[k].a.x,s[k].a.y,s[k].a.z,s[k].b.x,s[k].b.y,s[k].b.z);
  puts("");
 }
 free(G);
}
"""
    failures=[];broken={};current={}
    with tempfile.TemporaryDirectory(prefix='ug-fit-') as d:
        tmp=Path(d)
        if pulse_proof(tmp,source)==3:return 3
        inset='    if(type && !strcasecmp((char *)G+type,"cemtr")) side=fmin(side,.668085);'
        assert inset in mounts
        wheel_only=mounts.replace(inset,'')
        for label,body,results in [('original',OLD_MOUNTS,broken),('wheel-only',wheel_only,{}),('current',mounts,current)]:
            unit=tmp/(label+'.m');exe=tmp/label
            unit.write_text(driver.replace('FIXTURES',fixtures).replace('MOUNTS',body))
            cp=subprocess.run(['clang','-O1','-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),str(unit),'-o',str(exe)],capture_output=True,text=True)
            assert cp.returncode==0,cp.stderr[-2000:]
            cp=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
            rows=cp.stdout.splitlines();assert len(rows)==len(SILHOUETTES)==stock+added+swaps
            for line,(name,triangles) in zip(rows,SILHOUETTES):
                fields=line.split();assert fields[0]==name
                if fields[1]!='1':
                    failures.append(name+' rejected '+label);continue
                strips=np.array([float(v) for v in fields[2:]]).reshape(4,6)
                metres=outside(strips,triangles)
                if label=='wheel-only':
                    if name=='MW/cemtr':
                        assert abs(metres-1.740645)<1e-6,metres
                        print(f'CEMTR narrow-frame omitted: {metres:.6f} m outside')
                    continue
                # Replacement names can repeat; retain role/order in results.
                results[len(results)]=(name,metres)
                if label=='current' and metres>.05:
                    failures.append(f'{name}: {metres:.6f} m outside')
                print(f'{label} {name}: {metres:.6f} m outside',flush=True)
    assert any(v>.05 for n,v in broken.values()),'original mounting bug not reproduced'
    for name in ('MW/carreragt','MW/bmwm3gtr','C/jaguarxk'):
        before=[v for n,v in broken.values() if n==name]
        after=[v for n,v in current.values() if n==name]
        assert before and after and before[0]>.05,(name,before,after)
        print(f'named {name}: {before[0]:.6f} -> {after[0]:.6f} m')
    draw=light.function_fragment(source,'static void ug_draw(')
    # Submission hook + runtime light test execute the actual ePoly path;
    # this explicit count catches an accidentally restored emitter loop.
    emitters=len(re.findall(r'quad\(&c,s->view,[^;]*scratch\+0x200',draw))*4
    print(f'emitter quads per car: original=4 current={emitters}')
    if emitters:failures.append('visible emitter submission restored')
    print(f'fit: {stock+added}+{swaps} fixtures, limit=0.05 m, failures={len(failures)}')
    for failure in failures:print('FAIL '+failure)
    return bool(failures)

if __name__=='__main__':
    raise SystemExit(main())
