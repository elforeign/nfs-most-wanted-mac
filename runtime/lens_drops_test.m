// Production: 0 non-finite; suppressed: 0 changed, exit 1.
// Visibility (limit .05, size-3 drops): .098313; round 2's averaged ray field .020099
// (the drops a player could not see); suppressed .000071. The earlier .1 limit
// rested on a doubled ray scale (a 0.5 mm reference cap's deflection in 1 mm quad units).
// Brightness (limit .93): inside/ring over the dry frame's, on a vignetted frame:
// .806340 at 063ddc17 (doubled ray scale, clamped far rays on the dark border,
// absolute Fresnel loss) -> .957901.
// Rim step (limit .06): steepest neighbour-pixel step across the rim over the
// frame value: .012207 for b168e2cc's defocused edge, .022786 at 063ddc17 -> .107422.
#include "render_internal.h"
uint64_t frame_serial=1;
void blackbox_render_host(uint64_t serial,uint32_t id) { (void)serial;(void)id; }
#include "lens_drops.h"
#include <math.h>
#include <stdarg.h>

uint8_t *G;
int diag_on, gpt_on, pass_dirty, render_static_made;
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
id<MTLCommandBuffer> cmd;
static Obj objects[8];
static RenderTex textures[8];
static unsigned next_texture = 2, creates, failures;
static uint32_t cursor = 4096;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); ++failures; } } while(0)
uint32_t galloc(uint32_t n) { uint32_t a=cursor; cursor=(cursor+n+63)&~63u; return a; }
uint64_t host_ticks(void) { return 0; }
void diag_event(const char *s,...) { (void)s; }
void gpt_render_auto(MTLRenderPassDescriptor *p,const char *s) { (void)p;(void)s; }
void gpt_name(id<MTLCommandEncoder> e) { (void)e; }
Obj *obj(uint32_t h) { return h>=512 && h<=832 ? &objects[2+(h-512)/64] : h==1 ? &objects[1] : NULL; }
static id<MTLTexture> texture(MTLPixelFormat format,unsigned w,unsigned h) {
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.storageMode=MTLStorageModeShared; d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    return [mtl newTextureWithDescriptor:d];
}
// COM backing fixture: production upload creates/locks/unlocks these guest resources.
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a) {
    (void)c;(void)n;
    unsigned slot=fn-1000;
    if(slot==23) {
        unsigned h=next_texture++; CHECK(h<8); ++creates;
        Obj *o=&objects[h]; o->guest=512+(h-2)*64;o->w=a[1];o->h=a[2];o->fmt=a[5];o->levels=a[3];
        CHECK(a[3]==1 && a[4]==0 && a[6]==1); CHECK(o->h==32);
        CHECK(o->fmt==113 || o->fmt==32);
        o->pitch[0]=o->w*(o->fmt==113?8:4)+16; // prove pitched rows, including padding
        o->mem[0]=galloc(o->pitch[0]*o->h);
        M32(o->guest)=128;M32(a[7])=o->guest;
    } else if(slot==19) { Obj *o=obj(a[0]);M32(a[2])=o->pitch[0];M32(a[2]+4)=o->mem[0]; }
    else if(slot==20) { obj(a[0])->dirty=1; }
    else CHECK(slot==2);
    return 0;
}
RenderTex *gpu_tex(Obj *o) {
    if(!o) return NULL;
    unsigned h=(unsigned)(o-objects);RenderTex *r=&textures[h];
    if(!r->tex) {
        r->tex=texture(o->fmt==113?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm,o->w,o->h);
        [r->tex replaceRegion:MTLRegionMake2D(0,0,o->w,o->h) mipmapLevel:0 withBytes:G+o->mem[0] bytesPerRow:o->pitch[0]];
    }
    return r;
}
RenderTex *surface_rt(uint32_t h) { return h==1 ? &textures[1] : NULL; }
id<MTLTexture> surface_target(uint32_t h,NSUInteger *l,NSUInteger *s) { *l=*s=0;return h==1?textures[1].tex:nil; }
void end_pass(void) {}
void ensure_cmd(void) { if(!cmd)cmd=[queue commandBuffer]; }
void msaa_resolve(RenderTex *r) { (void)r; }
void render_ripple_warm(void) {}
id<MTLLibrary> pipeline_library_source(NSString *s,const char *name,NSError **e) { (void)name;return [mtl newLibraryWithSource:s options:nil error:e]; }
id<MTLRenderPipelineState> pipeline_build(MTLRenderPipelineDescriptor *d,const char *name,NSError **e) { (void)name;return [mtl newRenderPipelineStateWithDescriptor:d error:e]; }
static struct { id<MTLTexture> drop; } x_resources[3], *xr=&x_resources[0];
static void x_destination(id<MTLRenderCommandEncoder> e,const uint32_t r[4]) { [e setViewport:(MTLViewport){r[0],r[1],r[2],r[3],0,1}]; }
static id<MTLTexture> x_crop(id<MTLTexture> src,const uint32_t r[4],id<MTLTexture> __strong *cache) {
    *cache=texture(src.pixelFormat,r[2],r[3]);
    id<MTLBlitCommandEncoder> b=[cmd blitCommandEncoder];
    [b copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(r[0],r[1],0) sourceSize:MTLSizeMake(r[2],r[3],1) toTexture:*cache destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];[b endEncoding];return *cache;
}
// Sentinel console constants exercise the legacy branch without console data.
#define X360E_DROP_RADIUS 0.073f
#define X360E_DROP_LIFE 2.5f
#include "drops.inc"
#include "instances.inc"

int main(void) { @autoreleasepool {
    mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("SKIP: no Metal device");return 3;}queue=[mtl newCommandQueue];
    G=calloc(1,1<<20);M32(128)=128;for(int i=0;i<24;++i)M32(128+i*4)=1000+i;
    M32(256)=128;
    Cpu c={0};c.esp=900000;
    uint32_t sentinel[2]={6,7};CHECK(lens_drops_provider(&c,256,sentinel,lens_drops_mode(NULL)==2)==1);CHECK(sentinel[0]==6&&sentinel[1]==7&&creates==0);
    uint32_t disabled[2]={6,0};CHECK(lens_drops_provider(&c,256,disabled,lens_drops_mode("pc")==2)==0);CHECK(!disabled[0]&&!disabled[1]&&creates==0);
    RainDrop records[64];Instance expected[64],actual[64];
    for(unsigned i=0;i<64;++i)records[i]=(RainDrop){i/64.f,1-i/64.f,i/63.f,(i+1)/64.f,i};
    for(unsigned aspect=0;aspect<3;++aspect)for(int sig=0;sig<2;++sig){
        float ar=aspect==0?4.f/3:aspect==1?16.f/9:21.f/9;
        original_instances(expected,records,64,ar,sig);production_instances(actual,records,64,ar,sig,0);
        CHECK(!memcmp(expected,actual,sizeof expected));
        production_instances(actual,records,64,ar,sig,1);
        for(int i=0;i<64;++i){CHECK(actual[i].lc[0]==records[i].x&&actual[i].lc[1]==records[i].y);CHECK(actual[i].lc[2]==LENS_DROP_RADIUS*records[i].size*(LENS_DEFOCUS?LENS_DEFOCUS_EXPAND:1)/ar);CHECK(actual[i].ba[0]==records[i].life*LENS_DROP_LIFE);}
    }
    LensDropPixels a,b;lens_drops_generate(&a);lens_drops_generate(&b);CHECK(!memcmp(&a,&b,sizeof a));
    double mass[4]={0};
    for(int k=0;k<4;++k)for(int y=0;y<32;++y)for(int x=0;x<32;++x)mass[k]+=a.alpha[(y*128+k*32+x)*4+2]/255.0;
    printf("coverage_mass=%.6f/%.6f/%.6f/%.6f\n",mass[0],mass[1],mass[2],mass[3]);
    // Log-spaced diameter 2^(1/6) gives area ratio 2^(1/3) (.794); permit the cap's
    // small authored boundary perturbations and byte quantization.
    for(int k=0;k<3;++k){CHECK(mass[k]/mass[k+1]>.70);CHECK(mass[k]/mass[k+1]<.88);}
    unsigned nonfinite=0;float low=0,high=0;
    for(unsigned i=0;i<sizeof a.displacement/2;++i){_Float16 h;memcpy(&h,&a.displacement[i],2);nonfinite+=!isfinite((float)h);low=fminf(low,h);high=fmaxf(high,h);}
    CHECK(nonfinite==0&&low<0&&high>1);
    for(int k=0;k<4;++k)for(int y=0;y<32;++y)for(int x=0;x<32;++x)if(x<2||x>=30||y<2||y>=30)CHECK(a.alpha[(y*128+k*32+x)*4+2]==0);
    for(int k=1;k<4;++k) {int different=0;for(int y=0;y<32;++y)different|=memcmp(a.alpha+y*128*4,a.alpha+(y*128+k*32)*4,32*4)!=0;CHECK(different);}
    uint32_t pair[2]={0,7};CHECK(lens_drops_mode(NULL)==2&&lens_drops_mode("x360")==2&&lens_drops_mode("pc")==1&&lens_drops_mode("pc_before")==0);
    CHECK(lens_drops_provider(&c,256,pair,lens_drops_mode(NULL)==2)==2);CHECK(creates==2&&pair[0]==512&&pair[1]==576&&c.esp==900000);
    CHECK(objects[2].w==128&&objects[2].fmt==113&&objects[3].w==128&&objects[3].fmt==32);
    unsigned w=512,h=288;float *base=calloc(w*h*4,sizeof(float)),*result=calloc(w*h*4,sizeof(float));
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){unsigned i=(y*w+x)*4;base[i]=(x+.5f)/w;base[i+1]=(y+.5f)/h;base[i+2]=.6f;base[i+3]=1;}
    textures[1].tex=texture(MTLPixelFormatRGBA16Float,w,h);
    // Convert the synthetic frame once to half, so the drops-off baseline is exact.
    uint16_t *stored=calloc(w*h*4,2),*out=calloc(w*h*4,2);
    for(unsigned i=0;i<w*h*4;++i){_Float16 v=base[i];memcpy(stored+i,&v,2);}
    [textures[1].tex replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:stored bytesPerRow:w*8];
    // The smallest and the largest variant; the optics checks below read the first.
    RainDrop drops[2]={{.x=.2f,.y=.65f,.life=1,.size=3,.variant=0},{.x=.6f,.y=.65f,.life=1,.size=3,.variant=3}},d=drops[0];
    CHECK(render_lens_drops_view(1,drops,2,pair[0],pair[1],0,1,NULL)==2);
    [cmd commit];[cmd waitUntilCompleted];CHECK(cmd.status==MTLCommandBufferStatusCompleted);
    [textures[1].tex getBytes:out bytesPerRow:w*8 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
    unsigned changed=0;nonfinite=0;
    for(unsigned i=0;i<w*h;++i){changed+=memcmp(stored+i*4,out+i*4,8)!=0;for(unsigned k=0;k<4;++k){_Float16 v;memcpy(&v,out+i*4+k,2);result[i*4+k]=v;nonfinite+=!isfinite((float)v);}}
    float rr=LENS_DROP_RADIUS*d.size*(LENS_DEFOCUS?LENS_DEFOCUS_EXPAND:1);
    unsigned cx=(unsigned)lroundf((d.x+rr/(w/(float)h)*.5f)*w-.5f);
    unsigned cy=(unsigned)lroundf((d.y-rr*.5f)*h-.5f);
    // A blurred transparent disc keeps scene radiance near its original value;
    // the blue channel proves the old black TIR annulus is gone.
    float centre=result[(cy*w+cx)*4];
    float left=result[(cy*w+cx-3)*4],right=result[(cy*w+cx+3)*4];
    unsigned rim=cx+(unsigned)(rr*h*.42f);
    float rim_blue=result[(cy*w+rim)*4+2];
    // Visibility: mean |drops - no drops| (RGB) inside both sharp cap discs. On
    // the gradient frame it is the refracted image's displacement times its
    // opacity, so a drop that refracts nothing or is too faint reads near 0.
    // Size 3 keeps the refracted field inside the frame at the physical scale.
    double seen=0;unsigned seen_px=0;
    for(int j=0;j<2;++j){
        double qx=(drops[j].x+rr/(w/(float)h)*.5)*w-.5,qy=(drops[j].y-rr*.5)*h-.5;
        double cap=LENS_CAP_RADIUS*pow(LENS_DIAMETER_RATIO,3-drops[j].variant)*rr*h/(LENS_DEFOCUS?LENS_DEFOCUS_EXPAND:1);
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x)if(hypot(x-qx,y-qy)<cap){
            for(int k=0;k<3;++k)seen+=fabsf(result[(y*w+x)*4+k]-base[(y*w+x)*4+k])/3;++seen_px;}
    }
    seen/=seen_px?seen_px:1;
    printf("visibility=%.6f footprint_px=%u\n",seen,seen_px);
    CHECK(seen>.05);
    printf("optics centre=%.6f left=%.6f right=%.6f rim_blue=%.6f\n",centre,left,right,rim_blue);
    if(changed && LENS_DEFOCUS) { CHECK(rim_blue>.5f); CHECK(fabsf(centre-base[(cy*w+cx)*4])<.15f); }
    // Brightness: a road-like frame, uniform .6 in the middle and vignetted to
    // .15 at the borders as the graded composite is. A drop low on the right
    // (as on the still) must not read darker than the frame around it: mean
    // inside the cap over the cap..2x ring, relative to the same discs without
    // drops (1 = no change). Clamped far rays read the dark border.
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){unsigned i=(y*w+x)*4;double ex=fabs((x+.5)/w-.5)*2,ey=fabs((y+.5)/h-.5)*2;
        float l=(float)(.15+.45*(1-fmin(1,fmax(0,fmax(ex,ey)-.6)/.4)));base[i]=base[i+1]=base[i+2]=l;}
    for(unsigned i=0;i<w*h*4;++i){_Float16 v=base[i];memcpy(stored+i,&v,2);}
    [textures[1].tex replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:stored bytesPerRow:w*8];
    RainDrop lit={.x=.6f,.y=.7f,.life=1,.size=1,.variant=3};cmd=nil;ensure_cmd();
    CHECK(render_lens_drops_view(1,&lit,1,pair[0],pair[1],0,1,NULL)==1);
    [cmd commit];[cmd waitUntilCompleted];
    [textures[1].tex getBytes:out bytesPerRow:w*8 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
    float lr=LENS_DROP_RADIUS*lit.size*(LENS_DEFOCUS?LENS_DEFOCUS_EXPAND:1);
    double lx=(lit.x+lr/(w/(float)h)*.5)*w-.5,ly=(lit.y-lr*.5)*h-.5,lcap=LENS_CAP_RADIUS*lr*h/(LENS_DEFOCUS?LENS_DEFOCUS_EXPAND:1),sum[4]={0};unsigned cnt[2]={0};
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){double r=hypot(x-lx,y-ly);int ring=r>=lcap;if(r>=2*lcap)continue;
        _Float16 v;memcpy(&v,out+(y*w+x)*4,2);sum[ring]+=v;sum[2+ring]+=base[(y*w+x)*4];++cnt[ring];}
    double bright=(sum[0]/sum[1])/(sum[2]/sum[3]);
    // Rim sharpness: steepest step between neighbouring pixels on the drop's
    // central scanline from .7 to 1.4 cap radii (right side, the shaded half),
    // over the frame value. A defocused edge spreads the step over many pixels.
    double rim_step=0;
    for(unsigned x=(unsigned)(lx+.7*lcap);x<(unsigned)(lx+1.4*lcap);++x){_Float16 l,r;unsigned y=(unsigned)lround(ly);
        memcpy(&l,out+(y*w+x)*4,2);memcpy(&r,out+(y*w+x+1)*4,2);rim_step=fmax(rim_step,fabs((double)r-(double)l)/base[(y*w+x)*4]);}
    printf("rim_step=%.6f cap_px=%.2f\n",rim_step,lcap);
    CHECK(rim_step>=.06);
    printf("brightness=%.6f inside=%.4f ring=%.4f dry_inside=%.4f dry_ring=%.4f\n",bright,sum[0]/cnt[0],sum[1]/cnt[1],sum[2]/cnt[0],sum[3]/cnt[1]);
    CHECK(bright>=.93);
    printf("changed=%u nonfinite=%u deterministic=1 console_provider=1 formats=113/32\n",changed,nonfinite);
    CHECK(changed>0);CHECK(nonfinite==0);
    free(base);free(result);free(stored);free(out);free(G);return failures?1:0;
}}
