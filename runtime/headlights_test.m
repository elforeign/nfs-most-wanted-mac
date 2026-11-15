/* Production Metal fixture. Driven by headlights_test.py; no game process. */
#import <Foundation/Foundation.h>
#include <glob.h>
#include <stdarg.h>
#include "headlights.m"
#include "headlights_compat.inc"
#include "roadfilm.m"
#include "headlights_culling.inc"

uint8_t *G;
Device dev;
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
id<MTLCommandBuffer> cmd;
id<MTLRenderCommandEncoder> enc;
uint64_t frame_serial=71;
int pass_dirty=1,diag_on;
uint32_t q_active;
static float lamp_level=1;
float daynight_lamp_level(void){return lamp_level;}
MatWaterDraw mat_water_draw;
MatGrassDraw mat_grass_draw;
static unsigned checks,failures,guest_calls,guest_allocations,texture_allocations,draws;
static unsigned main_draws,main_guest,main_allocations,main_textures;
static const char *mutation;
static int blocker,queued,missing_caster,floor_receiver;
static unsigned selected,tree_visits;
static uint32_t scratch_address=0x200000;
static SmResult world_vs,world_ps;
static id<MTLFunction> native_vs,projected_vs,caster_vs,native_ps,projected_ps;
static id<MTLTexture> diffuse,output;
static id<MTLSamplerState> sampler;
static id<MTLDepthStencilState> depth_state_fixture;
static float centre[3],receiver_normal[3],translation[3];
static float camera_vp[4][4],halfwidth=.0001f;

#define CHECK(x) do{++checks;if(!(x)){++failures;if(failures<15)fprintf(stderr,"line %d: %s\n",__LINE__,#x);}}while(0)
static int near(double a,double b,double tolerance){return isfinite(a)&&fabs(a-b)<=tolerance;}
@interface HLDevice : NSObject { @public id<MTLDevice> real; }
@end
@implementation HLDevice
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d { ++texture_allocations;return [real newTextureWithDescriptor:d]; }
- (id)forwardingTargetForSelector:(SEL)s {return real;}
@end
static const char *held_variant;static int listed_on;
int postfx_look_variant(const char *s){return held_variant&&!strcmp(s,held_variant);}
int postfx_look_listed(const char *s){return listed_on&&!strcmp(s,"headlighton");}
/* D3D9 reference counts as d3d9.m keeps them: the device owns one reference
 * per binding. A count reaching zero frees the object (freed_bound counts a
 * free while the device or the snapshot still names it). */
static uint32_t ref_guest[8];static int ref_count[8],freed_bound,emulate_bind;
static int *refs(uint32_t g){for(int i=0;i<8;++i)if(ref_guest[i]==g)return &ref_count[i];for(int i=0;i<8;++i)if(!ref_guest[i]){ref_guest[i]=g;return &ref_count[i];}abort();}
void d3d9_addref_guest(uint32_t g){++*refs(g);}
void d3d9_release_guest(uint32_t g){if(--*refs(g)==0)++freed_bound;}
static void guest_bind(uint32_t *slot,uint32_t g){if(*slot==g)return;if(g)d3d9_addref_guest(g);uint32_t old=*slot;*slot=g;if(old)d3d9_release_guest(old);}
void diag_event(const char *s,...){(void)s;}
uint32_t gmalloc_try(uint32_t n){++guest_allocations;CHECK(n<1500000);return scratch_address;}
void end_pass(void){if(enc){[enc endEncoding];enc=nil;}pass_dirty=1;}
void ensure_cmd(void){if(!cmd)cmd=[queue commandBuffer];}
static void finish(void){end_pass();if(cmd){[cmd commit];[cmd waitUntilCompleted];CHECK(cmd.status==MTLCommandBufferStatusCompleted);cmd=nil;}}
static id<MTLLibrary> library(const char *s){NSError *e=nil;id<MTLLibrary> l=[mtl newLibraryWithSource:[NSString stringWithUTF8String:s] options:nil error:&e];if(!l){fprintf(stderr,"MSL: %s\n",e.localizedDescription.UTF8String);exit(2);}return l;}
static char *mutate(char *s){
    if(!mutation)return s;
    const char *old=NULL,*replacement=NULL;
    if(!strcmp(mutation,"projection")){old="return hl_linear(saturate(albedo))*e*";replacement="return hl_linear(saturate(albedo))*0.0f*e*";}
    if(!strcmp(mutation,"falloff")){old="fade/(r*r)*hl_visibility";replacement="fade/r*hl_visibility";}
    if(!strcmp(mutation,"shadows")){old="fade/(r*r)*hl_visibility(p,gn,l,map)";replacement="fade/(r*r)*1.0f";}
    if(!strcmp(mutation,"absolute")){old="float4 q=hl_transform(float4(p-l.position.xyz,0),l.vp);q.z-=.100083402836f;return q;";replacement="return hl_transform(float4(p,1),l.vp);";}
    if(old){CHECK(strstr(s,old));CHECK(hl_edit(&s,old,replacement));}return s;
}
static SmResult translate(const char *path){FILE *f=fopen(path,"rb");if(!f){fprintf(stderr,"missing %s\n",path);exit(3);}fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);uint32_t *words=malloc(n);CHECK(fread(words,1,n,f)==(size_t)n);fclose(f);SmOptions o;sm_default_options(&o);SmResult r={0};if(sm_translate(words,n/4,&o,&r)){fprintf(stderr,"translate: %s\n",r.error);exit(2);}free(words);return r;}
static id<MTLRenderPipelineState> pipeline(id<MTLFunction> vs,id<MTLFunction> ps,int shadow){
    MTLVertexDescriptor *v=[MTLVertexDescriptor vertexDescriptor];
    for(int i=0;i<4;++i){v.attributes[i].format=MTLVertexFormatFloat4;v.attributes[i].offset=i*16;v.attributes[i].bufferIndex=0;}
    v.layouts[0].stride=64;
    MTLRenderPipelineDescriptor *p=[MTLRenderPipelineDescriptor new];p.vertexFunction=vs;p.fragmentFunction=ps;p.vertexDescriptor=v;
    p.colorAttachments[0].pixelFormat=shadow?MTLPixelFormatR8Unorm:MTLPixelFormatRGBA32Float;
    if(shadow){p.colorAttachments[0].writeMask=MTLColorWriteMaskNone;p.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;}
    NSError *e=nil;id<MTLRenderPipelineState> result=[mtl newRenderPipelineStateWithDescriptor:p error:&e];
    if(!result){fprintf(stderr,"pipeline: %s\n",e.localizedDescription.UTF8String);exit(2);}return result;
}
static void set_named(const SmResult *r,const char *name,float constants[256][4],float x,float y,float z,float w){int i=hl_reg(r,name,1);if(i>=0){constants[i][0]=x;constants[i][1]=y;constants[i][2]=z;constants[i][3]=w;}}
static void constants(void){
    memset(dev.vsc,0,sizeof dev.vsc);int i=hl_reg(&world_vs,"WorldViewProj",4);CHECK(i>=0);
    for(int col=0;col<4;++col)for(int row=0;row<4;++row)dev.vsc[i+col][row]=camera_vp[row][col];
    set_named(&world_vs,"LocalLightVec",dev.vsc,0,0,1,0);
    set_named(&world_vs,"LocalEyePos",dev.vsc,centre[0]+1,centre[1]+1,centre[2]+1,1);
    set_named(&world_vs,"Fog_Const_3",dev.vsc,0,1,0,0);
    set_named(&world_vs,"Fog_One_Over_BrBm",dev.vsc,1,1,1,0);
    /* Native baseline black, with a nontrivial unchanged alpha. */
    set_named(&world_vs,"AmbientColour",dev.vsc,0,0,0,1);
    double m[4][4];for(int r=0;r<4;++r)for(int c=0;c<4;++c)m[r][c]=camera_vp[r][c];CHECK(hl_inverse(m,inverse_vp));
}
typedef struct {float position[4],normal[4],colour[4],uv[4];} Vertex;
static void bind_native(id<MTLRenderCommandEncoder> e,Vertex *v,unsigned n){
    [e setVertexBytes:v length:n*sizeof *v atIndex:0];
    [e setVertexBytes:dev.vsc length:sizeof dev.vsc atIndex:16];
    int ints[64]={0},bools[16]={0};[e setVertexBytes:ints length:sizeof ints atIndex:17];[e setVertexBytes:bools length:sizeof bools atIndex:18];
    SmVSAux va={0};va.point_size=1;[e setVertexBytes:&va length:sizeof va atIndex:19];
    float ps[224][4]={0};[e setFragmentBytes:ps length:sizeof ps atIndex:0];
    [e setFragmentBytes:ints length:sizeof ints atIndex:1];[e setFragmentBytes:bools length:sizeof bools atIndex:2];
    SmPSAux pa={0};[e setFragmentBytes:&pa length:sizeof pa atIndex:3];
    [e setFragmentTexture:diffuse atIndex:0];[e setFragmentSamplerState:sampler atIndex:0];[e setCullMode:MTLCullModeNone];
}
static Vertex vertex(float x,float y,float z){Vertex v={{x,y,z,1},{receiver_normal[0],receiver_normal[1],receiver_normal[2],0},{0,0,0,.6f},{.5f,.5f,0,0}};return v;}
static void shadow_geometry(void){
    HLConstants k;int admitted=headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,0,&k);CHECK(admitted==HL_CASTER);
    if(!admitted)return;
    if(missing_caster)return;
    Vertex v[6];
    float x=translation[0],y=translation[1],z=translation[2];
    /* Three different blocker orientations/topologies, outside the tiny main
     * camera's receiver footprint. A car front is a closed box's near face. */
    if(blocker==4){v[0]=vertex(x-20,centre[1],z+1);v[1]=vertex(x+20,centre[1],z+1);v[2]=vertex(x+20,centre[1],z+110);v[3]=v[0];v[4]=v[2];v[5]=vertex(x-20,centre[1],z+110);}
    else if(blocker==2){v[0]=vertex(x-3,y-.1f,z+1);v[1]=vertex(x+3,y-.1f,z+1);v[2]=vertex(x+3,y-.1f,z+20);v[3]=v[0];v[4]=v[2];v[5]=vertex(x-3,y-.1f,z+20);}
    else {float w=blocker==3?1:4,h=blocker==3?.5f:4,d=blocker==3?12:10;
        v[0]=vertex(x-w,y-h,z+d);v[1]=vertex(x+w,y-h,z+d);v[2]=vertex(x+w,y+h,z+d);v[3]=v[0];v[4]=v[2];v[5]=vertex(x-w,y+h,z+d);}
    ensure_cmd();if(!enc)enc=headlights_encoder(cmd);
    [enc setRenderPipelineState:pipeline(caster_vs,native_ps,1)];[enc setDepthStencilState:depth_state_fixture];
    [enc setViewport:(MTLViewport){0,0,HL_MAP_SIZE,HL_MAP_SIZE,0,1}];
    bind_native(enc,v,6);headlights_bind(enc,&k,0);
    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];++draws;headlights_drawn(0);
}
void f_006BE800(Cpu *c){(void)c;abort();} // no transformed BVH bounds
void f_004FCA70(Cpu *c){f_006CF2B0(c);}
void f_00729120(Cpu *c){
    CHECK(M32(c->esp+4)==7);++selected;c->esp+=16;
}
static unsigned visible(float x,float y,float z) {
    float lo[3]={x-.01f,y-.01f,z-.01f},hi[3]={x+.01f,y+.01f,z+.01f};
    memcpy(G+0x1b0000,lo,12);memcpy(G+0x1b0010,hi,12);
    Cpu c={.ecx=0x919650,.esp=0x1a0000,.cw=0x37f};M32(c.esp)=0xdead;
    M32(c.esp+4)=0x1b0000;M32(c.esp+8)=0x1b0010;M32(c.esp+12)=0;
    f_006CF2B0(&c);CHECK(c.esp==0x1a0010);return c.eax;
}
static void cull_planes(void){
    uint32_t platform=M32(0x919650);uint8_t saved[0x60];memcpy(saved,G+platform+0x140,sizeof saved);
    HLGpuLamp camera={0};for(int r=0;r<4;++r)for(int c=0;c<4;++c)camera.vp[c][r]=camera_vp[r][c];
    CHECK(hl_planes(platform,&camera));CHECK(!visible(centre[0],centre[1],10));
    HLLamp lamp={.right={1,0,0},.up={0,1,0},.forward={0,0,1},.enabled=1};HLGpuLamp gpu;
    headlights_pack_lamp(&lamp,&gpu);CHECK(hl_planes(platform,&gpu));
    CHECK(visible(0,0,25));CHECK(!visible(100,0,25));CHECK(!visible(0,0,-25));CHECK(!visible(0,0,121));CHECK(!visible(0,0,.05f));
    CHECK(!visible(0,10,25));CHECK(visible(5,-1,25));
    memcpy(G+platform+0x140,saved,sizeof saved);
}
uint32_t call_guest(Cpu *c,uint32_t address,int n,const uint32_t *args){
    ++guest_calls;
    switch(address){
    case 0x100001:return 0x110000;
    case 0x100002:return 1234;
    case 0x100003:{CHECK(n==1);float pose[16]={0,0,1,0,1,0,0,0,0,1,0,0,translation[0],translation[1],translation[2],1};memcpy(G+args[0],pose,64);return 0;}
    case 0x004FAE40:CHECK(n==3);M32(args[1]+0x80)=args[0];M32(args[1]+0x84)=args[2]|0x12;return 0;
    case 0x00729640:{
        CHECK(n==1);CHECK(c->ecx==0x140000);CHECK(M32(args[0]+0xc4)==UINT32_MAX);
        Cpu guest=*c;guest.esp=0x1a0000;guest.cw=0x37f;M32(guest.esp)=0xdead;M32(guest.esp+4)=args[0];
        unsigned before=selected;f_00729640(&guest);CHECK(guest.esp==0x1a0008);++tree_visits;
        queued=blocker&&selected>before;return 0;
    }
    case 0x00723FA0: {
        CHECK(n==2&&args[1]==0x20);HLCounters before=counters;
        headlights_prepare(c,args[0],args[1]);CHECK(!memcmp(&before,&counters,sizeof counters));
        M32(args[0]+0x54)+=5;return 0;
    }
    case 0x006E2F50:if(queued){shadow_geometry();queued=0;}return 0;
    case 0x100004:CHECK(n==2&&args[1]==0);return 0;
    case 0x100006:if(emulate_bind)guest_bind(&dev.indices,0xB000);return 0;
    case 0x100005:case 0x100007:case 0x100008:case 0x100009:return 0;
    default:fprintf(stderr,"unexpected guest %08x\n",address);exit(2);
    }
}
static void game_fixture(void){
    G=calloc(1,16*1024*1024);CHECK(G!=NULL);
    const uint32_t view=0x919650,platform=0x150000,camera=0x151000,conn=0x120000,info=0x121000;
    M32(view)=platform;M32(view+4)=1;MF32(view+0xc)=512;M32(view+0x40)=camera;M32(0x925E90)=6;
    M32(0x92D87C)=0x100000;M32(0x92D884)=1;M32(0x100000)=0x101000;M32(0x101000)=0x102000;M32(0x102004)=0x100001;
    M32(0x110000)=0x111000;M32(0x111070)=0x100002;
    M32(0x9B37CC)=0x112000;M32(0x9B37D4)=1;M32(0x112000)=conn;
    M32(conn)=0x113000;M32(0x113018)=0x100003;M32(0x11300c)=0x100004;M32(conn+0x30)=1234;M32(conn+0x28)=3;M32(conn+0x44)=info;
    M32(info+0x1620)=3;M32(info+0x104)=0x130000;
    M32(0x130000)=0x130100;M32(0x130100)=info+0x104;
    M32(0x130008)=0x9DB90133;M32(0x130108)=0xD09091C6;
    /* Chassis +X forward -> world +Z; chassis +Z up -> world +Y. */
    MF32(0x130020)=MF32(0x130120)=1;
    M32(0x9AA478)=0x140000;M32(0x140000)=0x9AA478;M32(0x140028)=0x141000;
    float bounds[6]={-4,-4,9,4,4,11};memcpy(G+0x141000,bounds,sizeof bounds);
    M16(0x141018)=1;M16(0x14101a)=7;MF32(0x8933d4)=.5f;
    M32(0x160000+0x9c0)=1;M32(0x160080)=view;
    M32(0x93DEBC)=0x170000;M32(0x170000)=0x170100;
    M32(0x170104)=0x100005;M32(0x170108)=0x100006;
    M32(0x982BDC)=0x180000;M32(0x180000)=0x180100;M32(0x18025c)=0x100007;
    M32(0x170048)=0x190000;M32(0x190000)=0x190100;M32(0x1901fc)=0x100008;M32(0x19020c)=0x100009;
    /* Render-target table entry 0 (back buffer, view id 1) is eViews[1]+0x60; entry 1 is the road reflection. */
    M32(view+0x60)=0x93DAB8;M32(0x93DAB8)=0;M32(0x93DABC)=1;M32(0x93DAD4)=1;M32(0x93DAD8)=4;
    M32(0x982A20)=0x93DAB8;
    dev.rs[7]=dev.rs[14]=1;dev.rs[168]=15;
    for(int i=0;i<4;++i)MF32(platform+0x80+i*20)=1;
}
static void install_camera(float distance,float h,float v){
    double hr=h*M_PI/180,vr=v*M_PI/180;
    double p[3]={distance*cos(vr)*sin(hr),distance*sin(vr),distance*cos(vr)*cos(hr)};
    for(int i=0;i<3;++i){centre[i]=translation[i]+p[i];receiver_normal[i]=-p[i]/distance;}
    memset(camera_vp,0,sizeof camera_vp);
    camera_vp[0][0]=camera_vp[1][1]=1/halfwidth;camera_vp[2][2]=1;camera_vp[3][3]=1;
    camera_vp[3][0]=-centre[0]/halfwidth;camera_vp[3][1]=-centre[1]/halfwidth;camera_vp[3][2]=.5f-centre[2];
    constants();
    float *plat=(float *)(G+M32(0x919650)+0x80);memcpy(plat,camera_vp,64);
}
static void prepare_fixture(void){
    Cpu c={.esp=0x1c0000,.ecx=0x160000,.eax=0xabcdef},saved=c;
    uint8_t entry[0xd0],view[0x70],camera[0x2c0],platform[0x1a0];
    memcpy(view,G+0x919650,sizeof view);memcpy(entry,G+0x160000,sizeof entry);memcpy(camera,G+0x151000,sizeof camera);memcpy(platform,G+0x150000,sizeof platform);
    uint32_t arena[2]={0x1d0000,0x1f0000};memcpy(G+0x915F84,arena,sizeof arena);
    uint32_t shader=0x123456;M32(0x982C80)=shader;
    Device before=dev;
    headlights_prepare(&c,0x919650,0x20);
    CHECK(!memcmp(&c,&saved,sizeof c));CHECK(!memcmp(view,G+0x919650,sizeof view));CHECK(!memcmp(entry,G+0x160000,sizeof entry));
    CHECK(!memcmp(camera,G+0x151000,sizeof camera));CHECK(!memcmp(platform,G+0x150000,sizeof platform));
    CHECK(!memcmp(arena,G+0x915F84,sizeof arena));CHECK(M32(0x982C80)==shader);
    // The module deliberately advances constant-upload generations on restore.
    before.ver_vsc=dev.ver_vsc;before.ver_psc=dev.ver_psc;before.ver_vsi=dev.ver_vsi;
    before.ver_psi=dev.ver_psi;before.ver_vsb=dev.ver_vsb;before.ver_psb=dev.ver_psb;
    CHECK(!memcmp(&before,&dev,sizeof dev));
    finish();
}
static float render_receiver(int enabled,float *alpha){
    static id<MTLRenderPipelineState> base,beam;
    if(!base){base=pipeline(native_vs,native_ps,0);beam=pipeline(projected_vs,projected_ps,0);}
    HLConstants k;int admitted=headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k);
    if(!enabled)admitted=0;
    MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];p.colorAttachments[0].texture=output;
    p.colorAttachments[0].loadAction=MTLLoadActionClear;p.colorAttachments[0].storeAction=MTLStoreActionStore;
    ensure_cmd();id<MTLRenderCommandEncoder> e=[cmd renderCommandEncoderWithDescriptor:p];
    [e setRenderPipelineState:admitted?beam:base];
    Vertex v[3]={vertex(centre[0]-halfwidth,centre[1]-halfwidth,centre[2]),vertex(centre[0]+3*halfwidth,centre[1]-halfwidth,centre[2]),vertex(centre[0]-halfwidth,centre[1]+3*halfwidth,centre[2])};
    if(floor_receiver){
        v[0]=vertex(centre[0]-halfwidth,centre[1],centre[2]-halfwidth);
        v[1]=vertex(centre[0]+3*halfwidth,centre[1],centre[2]-halfwidth);
        v[2]=vertex(centre[0]-halfwidth,centre[1],centre[2]+3*halfwidth);
    }
    bind_native(e,v,3);if(admitted)headlights_bind(e,&k,1);
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];++draws;if(admitted)headlights_drawn(1);
    [e endEncoding];finish();
    float pixel[4];[output getBytes:pixel bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
    *alpha=pixel[3];return pixel[0];
}
static void install_lamps(int second){
    /* The numeric oracle uses a canonical arbitrary orientation. Game-marker
     * preparation is independently checked below and does not feed a stale pose. */
    HLLamp l={.position={translation[0],translation[1],translation[2]},.right={1,0,0},.up={0,1,0},.forward={0,0,1},.enabled=1};
    headlights_pack_lamp(&l,&prepared.lamps[0]);l.enabled=second;headlights_pack_lamp(&l,&prepared.lamps[1]);
    prepared_frame=frame_serial;prepared_view=0x919650;valid=1;
}
static void clear_maps(void){
    for(int i=0;i<2;++i){cast_index=i;clear_map=1;ensure_cmd();id<MTLRenderCommandEncoder> e=headlights_encoder(cmd);[e endEncoding];}finish();
}
static void shadow_fixture(int shape){
    blocker=shape;casting=1;cast_index=0;clear_map=1;ensure_cmd();enc=headlights_encoder(cmd);
    shadow_geometry();end_pass();casting=0;finish();
}
static void sources(const char *root){
    const char *groups[]={"world","worldnormalmap","worldreflect","car"};unsigned compiled=0,covered=0,coated=0;
    for(int g=0;g<4;++g){char pattern[2048];snprintf(pattern,sizeof pattern,"%s/%s/*.bin",root,groups[g]);glob_t files={0};glob(pattern,0,NULL,&files);
        for(size_t i=0;i<files.gl_pathc;++i){SmResult r=translate(files.gl_pathv[i]);int mat=g+1;if(g==3&&strstr(files.gl_pathv[i],"t0_"))mat=5;
            if(r.shader_type==SM_PIXEL&&!headlights_material(r.program_key)){sm_result_free(&r);continue;}
            for(int cast=0;cast<2;++cast){if(cast&&r.shader_type==SM_PIXEL)continue;
                char *s=headlights_shader(r.msl,&r,cast,mat);if(!s){fprintf(stderr,"source rejected: %s cast %d\n",files.gl_pathv[i],cast);exit(2);}
                library(s);free(s);++compiled;
            }
            char *base=NULL;
            if(g==1&&r.shader_type==SM_PIXEL)base=parallax_ps_patch(r.msl);
            if(g==2)base=r.shader_type==SM_VERTEX?ripple_vs_patch(r.msl):ripple_ps_patch(r.msl);
            if(g==3&&r.shader_type==SM_VERTEX)base=strstr(files.gl_pathv[i],"t0_")?car_vs_patch(r.msl):car_vs_nm_patch(r.msl);
            if(g==3&&r.shader_type==SM_PIXEL){
                int technique=atoi(strrchr(files.gl_pathv[i],'/')+2);
                if(technique){asprintf(&base,"#define MAT_VARIANT %d\n#define MAT_SHADOW_HW 0\n#define MAT_CAR_HDR 1\n#define MAT_PAINT_POW_MUTANT 0\n#define MAT_PAINT_NAN_TINT 0\n#define MAT_PAINT_FINITE_AB 0\n%s",technique,car_paint_msl);}
                else base=car_t0_ps_patch(r.msl);
            }
            if((g>=2||(g==1&&r.shader_type==SM_PIXEL))&&!base){fprintf(stderr,"production material source rejected %s\n",files.gl_pathv[i]);exit(2);}
            if(base){
                if(g==2){
                    composition_base=base;
                    for(int film=0;film<2;++film){
                        int id=0;char *shelter=rain_cover_shader(NULL,0,&r,&id,film);
                        CHECK(shelter!=NULL);
                        char *beam=headlights_shader(shelter,&r,0,mat);CHECK(beam!=NULL);
                        library(beam);free(beam);free(shelter);++compiled;++covered;
                    }
                }
                if(g==3){
                    int t0=strstr(files.gl_pathv[i],"t0_")!=NULL;
                    char *coat=r.shader_type==SM_VERTEX?(t0?clearcoat_vertex(base):strdup(base)):
                        (t0?clearcoat_t0_patch(base):clearcoat_patch(base,1));
                    CHECK(coat!=NULL);
                    char *beam=headlights_shader(coat,&r,0,mat);CHECK(beam!=NULL);
                    library(beam);free(beam);free(coat);++compiled;++coated;
                }
                char *beam=headlights_shader(base,&r,0,mat);if(!beam){fprintf(stderr,"composite source rejected %s\n",files.gl_pathv[i]);exit(2);}library(beam);free(beam);++compiled;
                if(g==2&&r.shader_type==SM_PIXEL){char *film=roadfilm_patch(base);CHECK(film!=NULL);beam=headlights_shader(film,&r,0,mat);CHECK(beam!=NULL);library(beam);free(beam);free(film);++compiled;}
                if(g==3){
                    int technique=atoi(strrchr(files.gl_pathv[i],'/')+2);
                    char *chrome=r.shader_type==SM_VERTEX?realworld_materials_vertex(r.msl,technique==0,1):realworld_materials_pixel(car_paint_msl,technique,0,1);
                    CHECK(chrome!=NULL);beam=headlights_shader(chrome,&r,0,mat);if(!beam){fprintf(stderr,"chrome composition rejected %s\n",files.gl_pathv[i]);exit(2);}library(beam);free(beam);free(chrome);++compiled;
                }
                free(base);
            }
            sm_result_free(&r);
        }globfree(&files);
    }
    CHECK(covered>=12);CHECK(coated>=6);
    printf("production shader libraries=%u rain-cover/film=%u clearcoat=%u\n",compiled,covered,coated);
}
int main(int argc,char **argv){@autoreleasepool {
    if(argc<2)return 2;mutation=argc>2?argv[2]:NULL;
    id<MTLDevice> real=MTLCreateSystemDefaultDevice();if(!real){puts("SKIP: no Metal device");return 3;}
    HLDevice *counter=[HLDevice new];counter->real=real;mtl=(id<MTLDevice>)counter;queue=[mtl newCommandQueue];
    game_fixture();
    char path[2048];snprintf(path,sizeof path,"%s/world/t0_world_p0_vs_1_1.bin",argv[1]);world_vs=translate(path);
    snprintf(path,sizeof path,"%s/world/t0_world_p0_ps_1_1.bin",argv[1]);world_ps=translate(path);
    native_vs=[library(world_vs.msl) newFunctionWithName:@"sm_main"];native_ps=[library(world_ps.msl) newFunctionWithName:@"sm_main"];
    char *s=headlights_shader(world_vs.msl,&world_vs,0,1);if(mutation&&!strcmp(mutation,"absolute"))s=mutate(s);projected_vs=[library(s) newFunctionWithName:@"sm_main"];free(s);
    s=headlights_shader(world_vs.msl,&world_vs,1,1);if(mutation&&!strcmp(mutation,"absolute"))s=mutate(s);caster_vs=[library(s) newFunctionWithName:@"sm_main"];free(s);
    s=mutate(headlights_shader(world_ps.msl,&world_ps,0,1));projected_ps=[library(s) newFunctionWithName:@"sm_main"];free(s);
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    td.usage=MTLTextureUsageShaderRead;diffuse=[mtl newTextureWithDescriptor:td];float colour_pixel[4]={.46135613f,.46135613f,.46135613f,.37f};
    [diffuse replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:colour_pixel bytesPerRow:16];
    td.usage=MTLTextureUsageRenderTarget;output=[mtl newTextureWithDescriptor:td];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterLinear;sampler=[mtl newSamplerStateWithDescriptor:sd];
    MTLDepthStencilDescriptor *dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLessEqual;dd.depthWriteEnabled=YES;depth_state_fixture=[mtl newDepthStencilStateWithDescriptor:dd];
    install_camera(25,1.15,-.571);cull_planes();float alpha=0,alpha_base=0;
    /* Baseline is the production untranslated variant with no preparation.
     * Count external guest calls/device allocations as well as feature counters. */
    unsetenv("NFS_HEADLIGHT_PROJECTION");option=-1;
    main_draws=draws;main_guest=guest_calls;main_allocations=guest_allocations;main_textures=texture_allocations;
    float baseline=render_receiver(0,&alpha_base);unsigned baseline_draws=draws-main_draws;
    HLCounters off=counters;unsigned start=draws;
    prepare_fixture();float off_rgb=render_receiver(1,&alpha);
    CHECK(!headlights_enabled());CHECK(near(off_rgb,baseline,1e-8));CHECK(alpha==alpha_base);
    CHECK(draws-start==baseline_draws);CHECK(guest_calls==main_guest);CHECK(guest_allocations==main_allocations);
    CHECK(texture_allocations==main_textures);CHECK(!memcmp(&off,&counters,sizeof off));
    printf("Off/main: guest=%u/%u allocations=%u/%u textures=%u/%u draws=%u/%u\n",guest_calls-main_guest,0,guest_allocations-main_allocations,0,texture_allocations-main_textures,0,draws-start,baseline_draws);
    setenv("NFS_HEADLIGHT_PROJECTION","on",1);option=-1;
    blocker=0;prepare_fixture();CHECK(valid);
    CHECK(counters.allocations==4&&counters.passes==2);
    CHECK(maps[0].width==HL_MAP_SIZE&&maps[1].height==HL_MAP_SIZE&&maps[0].pixelFormat==MTLPixelFormatDepth32Float&&maps[1].pixelFormat==MTLPixelFormatDepth32Float);
    CHECK(prepared.lamps[0].position[3]==1&&prepared.lamps[1].position[3]==1);
    M32(0x121000+0x1624)=2;prepare_fixture();CHECK(valid);CHECK(prepared.lamps[1].position[3]==0);M32(0x121000+0x1624)=0;
    M32(0x121000+0x1620)=0;prepare_fixture();CHECK(!valid);M32(0x121000+0x1620)=3;
    /* Time-of-day lamp level: half strength packs w=.5; level 0 prepares nothing. */
    unsigned visits0=tree_visits,selected0=selected;
    lamp_level=.5f;prepare_fixture();CHECK(valid);CHECK(prepared.lamps[0].position[3]==.5f&&prepared.lamps[1].position[3]==.5f);
    unsigned passes=(unsigned)counters.passes;lamp_level=0;prepare_fixture();CHECK(!valid);CHECK(counters.passes==passes);lamp_level=1;
    tree_visits=visits0;selected=selected0; /* the BVH census below counts its own prepares */
    install_lamps(0);clear_maps();
    float e25=render_receiver(1,&alpha)-baseline;
    double expected=.18*hl_candela(1.15,-.571)/(25*25)/(M_PI*100);
    CHECK(near(e25,expected,expected*.004));CHECK(alpha==alpha_base);
    install_camera(50,1.15,-.571);float e50=render_receiver(1,&alpha)-baseline;
    CHECK(near(e25/e50,4,.03));printf("25/50m: %.9g / %.9g ratio %.7g expected 4\n",e25,e50,e25/e50);
    install_camera(25,1.15,-.571);install_lamps(1);float pair=render_receiver(1,&alpha)-baseline;CHECK(near(pair/e25,2,.01));
    prepared.lamps[1].position[3]=0;float one=render_receiver(1,&alpha)-baseline;CHECK(near(one,e25,e25*.003));
    receiver_normal[0]=receiver_normal[1]=0;receiver_normal[2]=1;CHECK(near(render_receiver(1,&alpha),baseline,1e-7));
    install_camera(25,1.15,-.571);for(int i=0;i<3;++i)receiver_normal[i]*=.5f;
    // Shader normalizes interpolated normals; use a unit normal with N.L=.5.
    double ray[3]={-receiver_normal[0]*2,-receiver_normal[1]*2,-receiver_normal[2]*2};
    double t[3]={ray[2],0,-ray[0]},len=hypot(t[0],t[2]);for(int i=0;i<3;++i)receiver_normal[i]=-.5*ray[i]+sqrt(.75)*t[i]/len;
    CHECK(near((render_receiver(1,&alpha)-baseline)/e25,.5,.005));
    const float angles[6][2]={{-3.43f,.57f},{-1,-.3f},{1.72f,-.86f},{0,-.86f},{-9,-1.72f},{9,-1.72f}};
    for(int i=0;i<6;++i){install_camera(25,angles[i][0],angles[i][1]);float value=render_receiver(1,&alpha)-baseline;
        double cd=hl_candela(angles[i][0],angles[i][1]);CHECK(near(value,.18*cd/625/(M_PI*100),fmax(1e-7,.18*cd/625/(M_PI*100)*.006)));}
    install_camera(25,3.43,-.3);float right=render_receiver(1,&alpha)-baseline;install_camera(25,-3.43,-.3);float left=render_receiver(1,&alpha)-baseline;CHECK(right>left+.005);
    install_camera(25,1.15,-.571);install_lamps(0);
    set_named(&world_vs,"Fog_Br_Plus_Bm",dev.vsc,.2f,.3f,.4f,0);
    set_named(&world_vs,"FogValue",dev.vsc,1,0,0,0);
    set_named(&world_vs,"LocalEyePos",dev.vsc,centre[0]+1,centre[1],centre[2],1);
    float fogged=render_receiver(1,&alpha)-baseline;
    CHECK(near(fogged/e25,exp(-.2),.004));
    printf("camera fog ratio %.7g expected %.7g\n",fogged/e25,exp(-.2));
    install_camera(25,1.15,-.571);install_lamps(0);++frame_serial;CHECK(render_receiver(1,&alpha)==baseline);install_lamps(0);
    // Large translation needs a wider raster footprint to avoid float vertex collapse.
    translation[0]=1024;translation[1]=-2048;translation[2]=512;halfwidth=.005f;
    install_camera(25,1.15,-.571);install_lamps(0);clear_maps();float shifted=render_receiver(1,&alpha)-baseline;CHECK(near(shifted,e25,e25*.015));
    printf("translated %.9g expected %.9g alpha %.7g\n",shifted,e25,alpha);
    for(int shape=1;shape<=3;++shape){clear_maps();shadow_fixture(shape);float blocked=render_receiver(1,&alpha)-baseline;CHECK(fabs(blocked)<1e-7);printf("occluder %d: %.9g unoccluded %.9g\n",shape,blocked,shifted);CHECK(alpha==alpha_base);}
    clear_maps();install_lamps(1);shadow_fixture(1);
    CHECK(near(render_receiver(1,&alpha)-baseline,shifted,shifted*.015));
    install_lamps(0);
    // A grazing road is also a caster. Correct per-texel receiver-plane depth
    // must preserve its illumination, rather than self-shadowing the beam.
    floor_receiver=1;install_camera(25,1.15,-.571);install_lamps(0);
    memset(camera_vp,0,sizeof camera_vp);camera_vp[0][0]=camera_vp[2][1]=1/halfwidth;
    camera_vp[1][2]=.001f;camera_vp[3][3]=1;camera_vp[3][0]=-centre[0]/halfwidth;
    camera_vp[3][1]=-centre[2]/halfwidth;camera_vp[3][2]=.5f-centre[1]*.001f;
    constants();memcpy(G+M32(0x919650)+0x80,camera_vp,64);
    receiver_normal[0]=receiver_normal[2]=0;receiver_normal[1]=1;
    clear_maps();float road_before=render_receiver(1,&alpha)-baseline;
    shadow_fixture(4);float road_after=render_receiver(1,&alpha)-baseline;
    CHECK(road_before>0);CHECK(near(road_after/road_before,1,.015));
    printf("road self-shadow ratio %.7g (%.9g / %.9g) expected 1\n",road_after/road_before,road_after,road_before);
    floor_receiver=0;install_camera(25,1.15,-.571);
    // Lamp frame survives neither failed caster admission nor an omitted draw.
    float bounds[6]={translation[0]-4,translation[1]-4,translation[2]+9,translation[0]+4,translation[1]+4,translation[2]+11};
    memcpy(G+0x141000,bounds,sizeof bounds);
    missing_caster=1;blocker=1;prepare_fixture();CHECK(!valid);CHECK(tree_visits==5&&selected==5);printf("resident BVH: visits=%u selected=%u (2+1+0+2 lamps)\n",tree_visits,selected);missing_caster=0;blocker=0;
    // Eligibility dimensions are independent: wrong view, alpha blend, target,
    // stale frame, unrecognized shader, grass and water all stay native.
    install_lamps(0);HLConstants k;
    CHECK(headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k)==HL_RECEIVER);
    CHECK(!headlights_draw(&world_vs,0,1,&k));CHECK(!headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,0,&k));
    dev.rs[27]=1;CHECK(!headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k));dev.rs[27]=0;
    M32(0x982A20)=0x93DAD4;CHECK(!headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k));
    M32(0x982A20)=0x919650;CHECK(!headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k));M32(0x982A20)=0x93DAB8;
    // Main view is the render-target entry the prepared eView names (+0x60).
    CHECK(headlights_draw(&world_vs,0x8abb0769bddd41b4ULL,1,&k)==HL_RECEIVER);
    // Casting rebinds the game's buffers; the restore keeps every count exact.
    { int *a=refs(0xA000),*b=refs(0xB000);*a=1;*b=1;freed_bound=0;emulate_bind=1;
      guest_bind(&dev.indices,0xA000);--*a; /* the game released its own reference while bound */
      prepare_fixture();emulate_bind=0;
      CHECK(dev.indices==0xA000);CHECK(*a==1);CHECK(*b==1);CHECK(freed_bound==0);
      printf("restore refs: bound %d (expect 1) cast buffer %d (expect 1) freed while bound %d (expect 0)\n",*a,*b,freed_bound);
      guest_bind(&dev.indices,0);freed_bound=0; }
    // A diagnostic list naming headlighton runs the feature without the switch; the held Off pass stays native.
    unsetenv("NFS_HEADLIGHT_PROJECTION");option=-1;diag_on=1;listed_on=1;
    CHECK(headlights_enabled());held_variant="headlightoff";CHECK(!headlights_enabled());
    held_variant="headlighton";listed_on=0;CHECK(headlights_enabled());held_variant=NULL;CHECK(!headlights_enabled());
    diag_on=0;CHECK(!headlights_enabled());setenv("NFS_HEADLIGHT_PROJECTION","on",1);option=-1;
    if(!mutation)sources(argv[1]);
    printf("headlights %s: checks=%u failures=%u caster_draws=%llu receiver_draws=%llu\n",mutation?mutation:"production",checks,failures,(unsigned long long)counters.caster_draws,(unsigned long long)counters.receiver_draws);
    return failures?1:0;
}}
