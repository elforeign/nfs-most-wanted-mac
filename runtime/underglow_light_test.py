"""Production light, actual diag wrapper/counters, independent stock asset inputs.
Seven cars (BMW, Carrera GT, Punto, Elise, Mustang GT, Clio, 997S), handles
82/11..16: 410107/410107 diagnostic/forced On, 410109/410109 native On;
each positive pass strips=4 pool=1. Stock inventory: 66 admitted, 7 rejected
(degenerate traffic/trailer axles). Original model/hash/LOD gates reject six
positive cars and 65 inventory cars (72 failures incl. LOD 2). Pinned original
switch under native On submits 0/0 and fails 80. Native Off, master Off and
colour Off: 7/7, 0 allocations/draws/road queries; omitted native gates draw
4/1 and fail 2. A queued-light gate omission fails 1. Low-bound height fails
6 on the low-pose 997S; fixed wheel-datum mounts pass all seven cars.
Independent quadrature checks pool RGB against submitted emitter quads.
64 unowned registrations retain 4/1; old ring fails 2. Old diag wiring fails
2 (1 allocation/6 queries vs 0/0). Missing begin/end, strips, tenfold units,
stale queue and delayed None admission each fail. No game or emulation.
"""
from pathlib import Path
import subprocess
import hashlib
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "underglow_light.h"
#include "d3d9_internal.h"
#include "diag_car.h"
#include <stdlib.h>
#include <stdarg.h>
#include <math.h>
#include <string.h>
uint8_t *G;int diag_on;const char *diag_dir;Device dev;
static Obj objects[3];static unsigned next_object,allocs,draws,strips,pools,failures,checks;
static unsigned saved_preset=2,saved_light=1;static int look_off;
static unsigned saved_strength;
unsigned underglow_pool_strength(void){return saved_strength;}
FIXTURES
static unsigned fixture_index,fixture_handle=82;
static float emitted[4][16],pool_quad[16];

static unsigned diag_allocs,diag_queries,admission_queries,original_calls;
static uint64_t frame_now;
static const uint32_t profile=0x81000,ride=0x51000,self=0x50000,view=0x20000;
#define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"line %d: %s\n",__LINE__,#x);}}while(0)
uint32_t galloc(uint32_t n){static uint32_t at=0x100000;uint32_t p=at;at+=(n+15)&~15u;++allocs;return p;}
Obj *obj(uint32_t p){for(unsigned i=0;i<3;++i)if(objects[i].guest==p)return &objects[i];return NULL;}
unsigned underglow_light_get(uint32_t p,uint32_t h){return p==profile&&h==fixture_handle?saved_light:0;}
unsigned underglow_get(uint32_t p,uint32_t h){return p==profile&&h==fixture_handle?saved_preset:0;}
int underglow_owned(uint32_t p,uint32_t h){return p==profile&&h==fixture_handle;}
void diag_event(const char *fmt,...){}
int postfx_look_variant(const char *v){return look_off&&!strcmp(v,"ugoff");}
void f_005910E0_orig(Cpu *c){}
void f_007609B0_orig(Cpu *c){uint32_t r=M32(c->esp+4);if(r)memcpy(G+c->ecx+8,G+r,0x310);}
void f_007A9DB0_orig(Cpu *c){memcpy(G+0x9BA6B0,G+M32(c->esp+4),0x310);}
void f_00739A70_orig(Cpu *c){}
void f_0075D220_orig(Cpu *c){M32(c->ecx+0x40)=0x55000;M32(0x55000)=41;c->eax=1;}
void f_0075D220(Cpu *c);
void f_00750B00_orig(Cpu *c){}
void f_00750B00(Cpu *c);
void f_005910E0(Cpu *c);void f_007A9DB0(Cpu *c);void f_007609B0(Cpu *c);void f_00739A70(Cpu *c);
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a){
 if(fn==0x00739B40){++diag_queries;M32(a[1])=0;M32(a[2])=4;return 0;}
 if(fn==0x1002){++admission_queries;CHECK(c->ecx==0x58000&&n==0);return M32(0x58060);}
 if(fn==0x1001){++admission_queries;CHECK(c->ecx==0x59000&&n==0);return 0x58000;}
 if(fn==0x00503400){CHECK(n==3);return 0x6000;}
 if(fn==0x1000){CHECK(n==9);Obj *o=&objects[next_object++];o->guest=0x7000+next_object*0x100;
  o->w=a[1];o->h=a[2];o->mem[0]=galloc(o->w*o->h*16);o->kind=K_TEXTURE;M32(a[7])=o->guest;return 0;}
 if(fn==0x006C0A30){memset(G+c->ecx,0,0x94);return 0;}
 CHECK(fn==0x006BE750&&n==5);++draws;
 uint32_t poly=a[0],ti=a[1];Obj *o=obj(M32(M32(ti)+0x18));CHECK(o!=NULL);
 CHECK(G[ti+0x56]==2);
 if(o->w==1){++strips;float *p=(float *)(G+poly);double width=hypot(hypot(p[0]-p[12],p[1]-p[13]),p[2]-p[14]);CHECK(fabs(width-.01)<1e-6);
  memcpy(emitted[(strips-1)%4],p,64);
  /* In every positive pose the emitter is above the receiver, follows the
   * chassis and stays within its vertical envelope plus mounting clearance. */
  CHECK(p[2]>0 && p[2] <= fixtures[fixture_index].hi[2]+MF32(0x52108));
  CHECK(p[2]>=fixtures[fixture_index].lo[2]-.005+MF32(0x52108)-1e-6);
  float *v=(float *)(G+o->mem[0]);CHECK(v[1]>10&&v[1]>v[0]&&v[1]>v[2]);}
 else{++pools;float *p=(float *)(G+poly);memcpy(pool_quad,p,64);
  CHECK(p[0]<fixtures[fixture_index].lo[0]-.5 && p[4]>fixtures[fixture_index].hi[0]+.5);
  CHECK(p[1]<fixtures[fixture_index].lo[1]-.5 && p[13]>fixtures[fixture_index].hi[1]+.5);
  float *v=(float *)(G+o->mem[0]);double sum=0;for(unsigned i=0;i<o->w*o->h;++i){CHECK(isfinite(v[4*i+1])&&v[4*i+1]>=0);sum+=v[4*i+1];}CHECK(sum>100);
 }
 return 0;
}
/* Independent midpoint quadrature of the submitted emitter quads verifies
 * the pool payload against the geometry actually sent to retail ePoly. */
static void verify_pool(void) {
 Obj *o=&objects[1];float *v=(float *)(G+o->mem[0]);
 unsigned sx=o->w/2,sy=105;
 double px=pool_quad[0]+(pool_quad[4]-pool_quad[0])*(sx+.5)/o->w;
 double py=pool_quad[1]+(pool_quad[13]-pool_quad[1])*(sy+.5)/o->h,e=0;
 for(unsigned j=0;j<4;++j) {
  float *p=emitted[j];double a[3],b[3];
  for(unsigned k=0;k<3;++k){a[k]=(p[k]+p[12+k])*.5;b[k]=(p[4+k]+p[8+k])*.5;}
  double length=hypot(hypot(b[0]-a[0],b[1]-a[1]),b[2]-a[2]);
  /* Side emitters remain between the wheel exclusion intervals. */
  if(j<2){CHECK(a[0]>fixtures[fixture_index].wheels[8]+fixtures[fixture_index].wheels[11]);
           CHECK(b[0]<fixtures[fixture_index].wheels[0]-fixtures[fixture_index].wheels[3]);}
  for(unsigned k=0;k<4096;++k){double t=(k+.5)/4096,rx=px-a[0]-t*(b[0]-a[0]),ry=py-a[1]-t*(b[1]-a[1]);
   double h=a[2]+t*(b[2]-a[2]),r2=rx*rx+ry*ry+h*h;
   if(h>0)e+=106.075/M_PI*h*h/(r2*r2)*length/4096;
  }
  if(saved_strength){double dx=px-(a[0]+b[0])*.5,dy=py-(a[1]+b[1])*.5;
   double cosine=1/sqrt(1+dx*dx+dy*dy);e+=50*pow(cosine,4);}
 }
 float *sample=v+4*(sy*o->w+sx);double y=.2126*sample[0]+.7152*sample[1]+.0722*sample[2];
 CHECK(e>0 && fabs(y/(e*.18/(M_PI*100))-1)<.003);
}
static void load_fixture(unsigned index) {
 fixture_index=index;fixture_handle=index?10+index:82;
 uint32_t row=profile+0x414+fixture_handle*20;
 M32(row)=fixture_handle;M32(row+12)=2;M32(row+4)=123+index;M32(row+8)=456+index;G[row+16]=index?index:7;
 uint32_t bound_ride=M32(self+0x84);
 if(bound_ride){Cpu bind={0};bind.esp=0x92000;bind.ecx=profile+0x414;
  M32(bind.esp+4)=fixture_handle;M32(bind.esp+12)=bound_ride;f_005910E0(&bind);}
 const struct CarFixture *f=&fixtures[index];
 strcpy((char *)G+0x70000+41*0xd0,f->name);
 for(unsigned i=0;i<3;++i){MF32(self+0x90+4*i)=f->lo[i];MF32(self+0xa0+4*i)=f->hi[i];}
 M32(self+0x17b4)=0x71000;M16(0x71002)=4;M16(0x710d6)=4;
 for(unsigned i=0;i<16;++i)MF32(0x71010+4*i)=f->wheels[i];
 for(unsigned i=0;i<4;++i){MF32(self+0x110+4*i)=-f->wheels[4*i+1];MF32(0x710dc+4*i)=f->widths[i];}
}
void *ug_diag_calloc(size_t n,size_t size){++diag_allocs;return calloc(n,size);}
uint32_t car85_diag_model_hash(Cpu *c,uint32_t r,uint32_t part,int lod){++diag_queries;return 0;}
FILE *dump_file(void){return NULL;}
void underglow_render_car_begin(const Cpu *c,UGCarCapture *s){memset(s,0,sizeof *s);}
void underglow_render_car_end(const Cpu *c,const UGCarCapture *s){}
void f_0074E160_orig(Cpu *c){++original_calls;}
#define calloc ug_diag_calloc
#include "diag_car_core.c"
#undef calloc
PRODUCTION_WRAPPER
int main(int argc,char **argv){
 if(argc>1&&!strcmp(argv[1],"saved-off"))saved_preset=0;
 if(argc>1&&(!strcmp(argv[1],"native-off")||!strcmp(argv[1],"forced-on")))saved_light=0;
 G=calloc(1,16*1024*1024);CHECK(G!=NULL);
 uint32_t row=profile+0x414+82*20;M32(row)=82;M32(row+12)=2;M32(row+4)=123;M32(row+8)=456;G[row+16]=7;
 M32(0x9B09D8)=0x70000;strcpy((char *)G+0x70000+41*0xd0,"bmwm3gtre46");M32(ride)=41;M32(ride+0x48)=0x6200;M32(self+0x84)=ride;
 M32(self+0xb84+0x17*0x14)=0x60100;M32(0x60108)=0x8E3052B5;M32(view+4)=1;
 load_fixture(0);
 M32(0x982C80)=0x3000;M32(0x3004)=4;M32(0x6000)=0x6100;
 static Obj device;device.guest=0x4000;dev.self=&device;M32(0x4000)=0x4100;M32(0x415c)=0x1000;
 Cpu c={0};c.esp=0x90000;c.ecx=profile+0x414;M32(c.esp+4)=82;M32(c.esp+12)=ride;f_005910E0(&c);
 M32(c.esp+4)=ride;f_007A9DB0(&c);c.ecx=0x53000;M32(c.esp+4)=0x9BA6B0;f_007609B0(&c);M32(self+0x84)=0x53008;
 c.ecx=self;M32(c.esp+4)=view;M32(c.esp+8)=0x52100;M32(c.esp+12)=0x52000;
 for(int i=0;i<4;++i)MF32(0x52000+20*i)=1;MF32(0x52108)=.25;
 c.eax=0x12340001;Cpu saved=c;UGCarCapture capture;
 f_0074E160(&c);CHECK(original_calls==1);
 int enabled=argc>1&&strcmp(argv[1],"off")&&strcmp(argv[1],"saved-off")&&strcmp(argv[1],"native-off");
 if(enabled){CHECK(strips==4);CHECK(pools==1);CHECK(allocs>0);if(strips==4&&pools==1)verify_pool();}
 else{CHECK(allocs==0&&draws==0);}
 CHECK(!memcmp(&c,&saved,sizeof c));
 printf("diag-off: allocations=%u queries=%u\n",diag_allocs,diag_queries);
 CHECK(diag_allocs==0);CHECK(diag_queries==0);
 if(!enabled){
  M32(0x925E90)=6;M32(0x92D87c)=0x59200;M32(0x92D884)=1;
  M32(0x59200)=0x59000;M32(0x59000)=0x59100;M32(0x59104)=0x1001;
  unsigned before=admission_queries;f_0074E160(&c);
  printf("off/none road: guest queries=%u\n",admission_queries-before);
  CHECK(admission_queries==before);
 }
 if(enabled){
  Cpu traffic=c;
  for(unsigned i=0;i<64;++i){traffic.ecx=0xa1000+i*0x400;f_00739A70(&traffic);}
  unsigned before_strips=strips,before_pools=pools;frame_now+=120;f_0074E160(&c);
  printf("traffic-64: strips=%u pool=%u\n",strips-before_strips,pools-before_pools);
  CHECK(strips-before_strips==4);CHECK(pools-before_pools==1);
 }
 printf("submissions: strips=%u pool=%u allocations=%u checks=%u failures=%u\n",strips,pools,allocs,checks,failures);
 if(enabled){
  /* Change cars within one process, keeping the shared texture objects alive:
   * this catches a pool cache keyed only by colour/pose instead of geometry. */
  for(unsigned i=1;i<7;++i){
   load_fixture(i);MF32(0x52108)=i==6?.08:.25;unsigned bs=strips,bp=pools;f_0074E160(&c);
   CHECK(strips-bs==4 && pools-bp==1);
   printf("car=%s handle=%u strips=%u pool=%u\n",fixtures[i].name,fixture_handle,strips-bs,pools-bp);
   if(strips-bs==4 && pools-bp==1)verify_pool();
  }
  MF32(0x52108)=.25;
  unsigned supported=0,rejected=0;
  for(unsigned i=0;i<sizeof fixtures/sizeof fixtures[0];++i){
   load_fixture(i);UGCarCapture inventory;underglow_light_begin(&c,&inventory);
   CHECK(!!inventory.valid==fixtures[i].supported);
   if(inventory.valid)++supported;else ++rejected;
  }
  printf("stock admission inventory: %u supported, %u rejected\n",supported,rejected);
  load_fixture(0);f_0074E160(&c);if(strips&&pools)verify_pool();
  unsigned a=allocs,d=draws;saved_preset=0;underglow_light_begin(&c,&capture);underglow_light_end(&c,&capture);CHECK(!capture.valid&&allocs==a&&draws==d);
  saved_preset=2;M32(c.esp+36)=1;underglow_light_begin(&c,&capture);CHECK(!capture.valid);M32(c.esp+36)=0;
  c.ecx=M32(self+0x84);f_00739A70(&c);c.ecx=self;underglow_light_begin(&c,&capture);CHECK(!capture.valid);
  G[self+0x78]=1;M32(0x925E90)=6;M32(0x91CF90)=0x80000;M32(0x80010)=profile;M32(profile+0xa8)=82;
  uint32_t record=profile+0x414+4000+7*0x198;memset(G+record,0x2a,278);memcpy(G+0x56000,G+record,278);
  M32(0x92D87c)=0x59200;M32(0x92D884)=1;M32(0x59200)=0x59000;M32(0x59000)=0x59100;M32(0x59104)=0x1001;
  M32(0x9B37cc)=0x59300;M32(0x9B37d4)=1;M32(0x59300)=0x57000;M32(0x57044)=self;M32(0x57030)=0x1234;
  M32(0x58000)=0x58100;M32(0x58170)=0x1002;M32(0x58060)=0x1234;
  c.ecx=0x57000;M32(c.esp+16)=0x56000;f_0075D220(&c);c.ecx=self;M32(self+0x84)=0x55000;
  M32(self+0x1628)=1;M32(self+0xb84+0x17*0x14+4)=0x60100;M32(0x60108)=0x8E3052B6;
  underglow_light_begin(&c,&capture);CHECK(capture.valid&&capture.handle==82);
  unsigned before=draws;underglow_light_end(&c,&capture);CHECK(draws==before);f_00750B00(&c);CHECK(draws==before+5&&allocs==a);
  before=draws;f_00750B00(&c);CHECK(draws==before);
  underglow_light_end(&c,&capture);++dev.frames;f_00750B00(&c);CHECK(draws==before);
  underglow_light_end(&c,&capture);diag_on=1;look_off=1;f_00750B00(&c);CHECK(draws==before);look_off=0;diag_on=0;
  underglow_light_end(&c,&capture);saved_preset=0;before=draws;f_00750B00(&c);
  CHECK(draws==before && allocs==a);saved_preset=2;
  if(argc>1 && !strcmp(argv[1],"native-on")) {
   underglow_light_end(&c,&capture);saved_light=0;UGCarCapture disabled;
   unsigned queries=admission_queries;underglow_light_begin(&c,&disabled);
   CHECK(!disabled.valid && admission_queries==queries);
   before=draws;f_00750B00(&c);CHECK(draws==before && allocs==a);saved_light=1;
  }
  M32(0x58060)=0x4321;underglow_light_begin(&c,&capture);CHECK(!capture.valid);M32(0x58060)=0x1234;
  M32(0x5901c)=1;underglow_light_begin(&c,&capture);CHECK(!capture.valid);M32(0x5901c)=0;
  /* A retail LOD/body-kit choice is independent of the optical mounts. */
  M32(self+0x1628)=2;underglow_light_begin(&c,&capture);CHECK(capture.valid);M32(self+0x1628)=1;
  M16(0x71002)=3;underglow_light_begin(&c,&capture);CHECK(!capture.valid);M16(0x71002)=4;
  MF32(self+0x90)=NAN;underglow_light_begin(&c,&capture);CHECK(!capture.valid);load_fixture(0);
  G[0x56000+270]^=1;c.ecx=0x57000;f_0075D220(&c);c.ecx=self;underglow_light_begin(&c,&capture);CHECK(!capture.valid);

 }
 printf("total checks=%u failures=%u\n",checks,failures);free(G);return failures?1:0;
}
'''


PRODUCTION_PATH = """    if (!diag_on) {
        f_0074E160_orig(c);
        underglow_light_end(c, &light_capture);
        return;
    }
"""
UNOWNED_GUARD = """    /* Unowned ride invalidations must not displace stored provenance. */
    if(!o.profile) return;
"""


def function_fragment(source, signature):
    start = source.index(signature)
    body = source.index('{', start)
    depth = 0
    for at in range(body, len(source)):
        depth += (source[at] == '{') - (source[at] == '}')
        if not depth:
            return source[start:at+1]
    raise AssertionError('unterminated function')


OLD_ADMISSION = '    uint32_t types=M32(0x009B09D8u),type=M32(ride);\n    if(!types || type>=256 || strcasecmp((char *)G+types+type*0xd0,"bmwm3gtre46")) return;\n    int lod=(int)M32(self+0x1628);\n    if(lod<0||lod>1) return;\n    uint32_t body=M32(self+0xb84+0x17*0x14+lod*4)&~3u;\n    if(!body||M32(body+8)!=0x8E3052B5u+(unsigned)lod) return;\n'
OLD_ENABLED = 'int underglow_light_enabled(void) {\n    static int enabled=-1;\n    if (enabled<0) { const char *v=getenv("NFS_UNDERGLOW"); enabled=v&&!strcmp(v,"1"); }\n    return enabled;\n}'

def fixtures():
    import sys, struct
    sys.path.insert(0,str(ROOT/"tools_snapshot"))
    game=ROOT.parent/"Need For Speed Most Wanted Black Edition"
    if not (game/"GLOBAL/attributes.bin").is_file():
        return None
    try:
        import carbon_car as cc
        from wheel_attrib import Wheels
    except ModuleNotFoundError:
        return None
    wheels=Wheels(game/"GLOBAL/attributes.bin","mw")
    rows=[]
    required=("BMWM3GTRE46","CARRERAGT","PUNTO","ELISE","MUSTANGGT","CLIO","997S")
    names=list(required)+[p.name for p in sorted((game/"CARS").iterdir()) if p.name not in required]
    unsupported={"TRAFCAMPER","TRAILERA","TRAILERB","TRAILERCMT","TRAILERCON","TRAILERCRATE","TRAILERLOG"}
    for name in names:
        path=game/"CARS"/name/"GEOMETRY.BIN"
        if not path.is_file():
            if name in required: return None
            continue
        solids=cc.solids(path.read_bytes())
        header=next((cc.payload(s,0x134011,16) for _,s in solids if cc.payload(s,0x134011,16)[160:].split(b"\0")[0].decode()==name+"_KIT00_BODY_A"),None)
        if header is None:
            if name in required: return None
            continue
        bounds=struct.unpack_from("<3f",header,32)+struct.unpack_from("<3f",header,48)
        try: data=wheels.extract(name)
        except (ValueError,KeyError):
            if name in required: raise
            continue
        fields=[bounds[:3],bounds[3:],data["TireOffsets"],data["TireSkidWidth"]]
        rows.append("{\""+name.lower()+"\","+",".join("{"+",".join(format(v,".9g") for v in values)+"}" for values in fields)+","+str(int(name not in unsupported))+"}")
    return "struct CarFixture {const char *name;float lo[3],hi[3],wheels[16],widths[4];int supported;};\nstatic const struct CarFixture fixtures[]={"+",\n".join(rows)+"};\n"

def main():
    fixture_data=fixtures()
    if fixture_data is None:
        print('SKIP: stock car geometry and attributes required'); return 3
    assert hashlib.sha256(OLD_ADMISSION.encode()).hexdigest()=='26a54ad44c8e3c2f0c299c2dcdff4a006a836de07674cf9fc31938c0df6681de'
    assert hashlib.sha256(OLD_ENABLED.encode()).hexdigest()=='4fc0767a58a88315eb69c3f7f6e52d5eef2ecea443bef06a451262ad6da4905b'
    source = (ROOT/'runtime'/'underglow_light.m').read_text()
    diag = (ROOT/'runtime'/'diag.c').read_text()
    wrapper = diag[diag.index('\nunsigned diag_car1[4];')+1:diag.index('void f_007387E0_orig')]
    assert PRODUCTION_PATH in wrapper and UNOWNED_GUARD in source
    old_wiring = wrapper.replace(PRODUCTION_PATH, '')
    old_ring = source.replace(UNOWNED_GUARD, '')
    # Pin original function fragments, not repository history or whole files.
    assert hashlib.sha256(function_fragment(old_wiring, 'void f_0074E160(Cpu *c)').encode()).hexdigest() == '02a74ee4de62d6b197a62552e9fe6dffaa92e93eac34cc567e04d820fd4e91a0'
    assert hashlib.sha256(function_fragment(old_ring, 'static void remember(UGOwner o)').encode()).hexdigest() == 'af4eb13714cba3224a2f5891a8d24ca4dbb13b5793d470a8cfd70ab1945c83ad'
    none_first = """    unsigned preset=underglow_get(o.profile,o.handle);
    if(!preset) return;
    if(!light_on(o.profile,o.handle)) return;
    if(M32(0x00925E90u)==6 && !primary_car(c,self,ride)) return;"""
    assert none_first in source
    none_late = """    if(M32(0x00925E90u)==6 && !primary_car(c,self,ride)) return;
    unsigned preset=underglow_get(o.profile,o.handle);
    if(!preset) return;
    if(!light_on(o.profile,o.handle)) return;"""
    variants = {'good': (source, wrapper),
                'original-admission': (source.replace('    if(!cached_mounts(self,ride,o,s->mounts)) return;',OLD_ADMISSION+'    if(!cached_mounts(self,ride,o,s->mounts)) return;'),wrapper),
                'original-switch': (source.replace(function_fragment(source,'int underglow_light_enabled(void)'),OLD_ENABLED),wrapper),
                'queued-light-gate-omitted': (source.replace(' || !light_on(s->profile,s->handle)',''),wrapper),
                'native-gates-omitted': (source.replace('    if(!light_on(o.profile,o.handle)) return;\n','').replace(' || !light_on(s->profile,s->handle)',''),wrapper),
                'none-after-primary': (source.replace(none_first, none_late), wrapper),
                'old-wiring': (source, old_wiring),
                'old-ring': (old_ring, wrapper),
                'missing-begin-hook': (source, wrapper.replace('underglow_light_begin(c, &light_capture);', 'memset(&light_capture, 0, sizeof light_capture);')),
                'missing-end-hook': (source, wrapper.replace('underglow_light_end(c, &light_capture);', '')),
                'disabled-light': (source.replace('return light_override()!=0;', 'return 0;'), wrapper),
                'missing-strips': (source.replace('i<UG_STRIPS;++i) {\n        UGVec3 delta', 'i<0;++i) {\n        UGVec3 delta'), wrapper),
                'low-bound-mount-height': (source.replace('fmax(lo[2]-.005,fmin(hi[2]-.005,wheel_z-.01))','lo[2]-.005'),wrapper),
                'pool-units': (source.replace('rgb[k]*.18/(M_PI*100)', 'rgb[k]*.18/(M_PI*10)'), wrapper),
                'stale-queue': (source.replace('road_frame!=dev.frames || ', ''), wrapper)}
    with tempfile.TemporaryDirectory(prefix='ug-light-') as td:
        tmp = Path(td)
        diagnostic = (ROOT/'runtime'/'diag_car.c').read_text().split('// BOOT_DIAG cartex:')[0]
        (tmp/'diag_car_core.c').write_text(diagnostic)
        core = tmp/'core.o'
        cp = subprocess.run(['clang','-O1','-Iruntime','-Irecomp','-DUG_RENDER_CORE_ONLY','-c','runtime/underglow_render.m','-o',str(core)],cwd=ROOT,capture_output=True,text=True)
        assert cp.returncode == 0, cp.stderr[-3000:]
        for name, (code, hook) in variants.items():
            assert name == 'good' or (code, hook) != (source, wrapper), name
            harness = tmp/'harness.m'
            harness.write_text(HARNESS.replace('PRODUCTION_WRAPPER', hook).replace('FIXTURES',fixture_data))
            unit = tmp/(name+'.m'); unit.write_text(code)
            exe = tmp/name
            cp = subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-Iruntime','-Irecomp',
                                 str(harness), str(unit), str(core), 'runtime/underglow_photometry.c',
                                 '-framework','Metal','-framework','Foundation','-o',str(exe)],
                                cwd=ROOT,capture_output=True,text=True)
            assert cp.returncode == 0, cp.stderr[-4000:]
            import os
            settings = ['1','0','saved-off','native-on','native-off','forced-on'] if name == 'good' else ['native-on'] if name in ('original-switch','queued-light-gate-omitted') else ['native-off'] if name=='native-gates-omitted' else ['saved-off'] if name == 'none-after-primary' else ['1','saved-off'] if name == 'old-wiring' else ['1']
            for setting in settings:
                environment=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
                environment.pop('NFS_UNDERGLOW',None)
                if not setting.startswith('native'): environment['NFS_UNDERGLOW']='1' if setting in ('saved-off','forced-on') else setting
                cp = subprocess.run([str(exe),'on' if setting == '1' else setting if setting in ('saved-off','native-on','native-off','forced-on') else 'off'],
                                    env=environment,
                                    capture_output=True,text=True)
                print(name,setting,cp.stdout.strip(),flush=True)
                assert cp.returncode == (0 if name == 'good' else 1), cp.stderr[-4000:]
    print('underglow_light_test PASS')

if __name__ == '__main__':
    raise SystemExit(main())
