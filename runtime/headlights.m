/* Optional generic low beams; photometry and limitations: headlight_profile.h.
 * Two independent 1024-square D32 maps, current-frame ownership only. Occluders
 * are lamp-frustum reculls of resident scenery and current vehicle meshes,
 * independent of the sun shadow setting and the camera's visible list.
 * Delta L = linear diffuse reflectance * lux / (pi * 100 cd/m2 per unit).
 * The clock's authored-night unit conversion is packed in lamp strength W.
 * Neutral source RGB; no assertion about the installed bulb's spectrum.
 * Camera fog attenuates this term once; exposure/bloom/output encoding follow
 * the existing path. SDR targets can clip. No volumetric light is added.
 */
#include "headlights.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HLCounters counters;
void headlights_counters(HLCounters *out) { *out = counters; }
int headlights_admit(int enabled, uint64_t frame, uint64_t prepared, int valid,
                     unsigned view, int main_target, int opaque, int material) {
    return enabled && valid && frame == prepared && view == 1 && main_target && opaque && material;
}
int headlights_material(uint64_t h) {
    switch (h) {
    case 0x8abb0769bddd41b4ULL: case 0x629cd2e4c94c6bbcULL: case 0xcc1387a106b0817aULL:
    case 0x92b6eaaea4001456ULL: case 0x6612e0af7dcecd0aULL: case 0x9f1ba5e229fce41fULL: return 1;
    case 0x8cbd8764e8812945ULL: case 0x58c4434a7033b3baULL: case 0xe5af96aee84674f6ULL:
    case 0xd71de6c5aa405eaaULL: case 0xb2a0298b595a8c2cULL: case 0xb0057ade7f4a5b3fULL: return 2;
    case 0xe5f37cd552b4bd5fULL: case 0xd2d9b9d538a06aeeULL: case 0xcb861f5d715664a3ULL:
    case 0x268cb5f65c872238ULL: case 0xccaacc20f85f0ba7ULL: case 0x242b92c6a96de029ULL: return 3;
    case 0x393123da8b3ec17fULL: case 0x2cd941122737ddf5ULL: case 0xe3c84e78b9847542ULL:
    case 0x6f1a7154236357c9ULL: case 0xa8f9086d6be6db85ULL: return 4;
    case 0x89e9a52ccee09bcfULL: return 5;
    default: return 0;
    }
}

void headlights_pack_lamp(const HLLamp *l, HLGpuLamp *o) {
    memset(o, 0, sizeof *o);
    if (!l || !l->enabled) return;
    const HLVec3 axes[4] = {l->position,l->right,l->up,l->forward};
    float *dst[4] = {o->position,o->right,o->up,o->forward};
    for (int i=0;i<4;++i) { dst[i][0]=axes[i].x;dst[i][1]=axes[i].y;dst[i][2]=axes[i].z; }
    o->position[3]=1;
    /* 90-degree horizontal, +/-7 degrees vertical; covers the full cookie.
     * The cookie gates the actual -5..7 degree domain. Positive forward,
     * D3D z in [0,1], near .10 m / far 120 m. Same matrix for both passes. */
    const double scale=1/tan(7*acos(-1.0)/180), a=120.0/119.9, b=-12.0/119.9;
    const double p[3]={l->position.x,l->position.y,l->position.z};
    for(int r=0;r<3;++r) {
        o->vp[0][r]=dst[1][r];o->vp[1][r]=dst[2][r]*scale;
        o->vp[2][r]=dst[3][r]*a;o->vp[3][r]=dst[3][r];
        o->vp[0][3]-=p[r]*o->vp[0][r];o->vp[1][3]-=p[r]*o->vp[1][r];
        o->vp[2][3]-=p[r]*o->vp[2][r];o->vp[3][3]-=p[r]*o->vp[3][r];
    }
    o->vp[2][3]+=b;
}

/* Generated from the public profile at shader construction, never duplicated
 * as a second optical table. The nonuniform angular axes require manual
 * bilinear interpolation; hardware texture filtering would change the units. */
static const char hl_math[] =
"struct HLLampGPU { float4 position,right,up,forward; float4 vp[4]; };\n"
"struct HLConstants { HLLampGPU lamps[2]; float4 world[4],normal[3]; float4 fog,local_eye; uint4 frame; };\n"
"static float4 hl_transform(float4 p,constant float4 *m) { return float4(dot(p,m[0]),dot(p,m[1]),dot(p,m[2]),dot(p,m[3])); }\n"
/* Keep large world translations out of the projection's dot products. Both
 * caster and receiver use this exact matrix/helper in lamp-relative metres. */
"static float4 hl_project(float3 p,constant HLLampGPU &l) {float4 q=hl_transform(float4(p-l.position.xyz,0),l.vp);q.z-=.100083402836f;return q;}\n"
"static float3 hl_linear(float3 a) { return select(pow((a+.055f)/1.055f,float3(2.4f)),a/12.92f,a<=.04045f); }\n"
"static float hl_cookie(float h,float v) {\n"
" if(h<hl_h[0]||h>hl_h[HL_NX-1]||v<hl_v[0]||v>hl_v[HL_NY-1]||v>min(0.0f,-.57f+.2679491924f*max(h,0.0f)))return 0;\n"
" uint x=0,y=0;while(x+2<HL_NX&&h>hl_h[x+1])++x;while(y+2<HL_NY&&v>hl_v[y+1])++y;\n"
" float u=(h-hl_h[x])/(hl_h[x+1]-hl_h[x]),t=(v-hl_v[y])/(hl_v[y+1]-hl_v[y]);\n"
/* Feather inward: no upward light, and the useful central road lobe is
 * unchanged. The lower domain edge otherwise clips nonzero grid values. */
" float edge=smoothstep(0.0f,.2f,min(0.0f,-.57f+.2679491924f*max(h,0.0f))-v)*smoothstep(hl_v[0],hl_v[0]+.5f,v);\n"
" return mix(mix(hl_cd[y*HL_NX+x],hl_cd[y*HL_NX+x+1],u),mix(hl_cd[(y+1)*HL_NX+x],hl_cd[(y+1)*HL_NX+x+1],u),t)*edge;\n"
"}\n"
/* Four point-depth taps with receiver-plane correction. A fixed normalized
 * bias leaks metres at long range and still self-shadows grazing roads. Each
 * tap compares against the receiver plane at THAT texel's centre. A 2e-7
 * depth tolerance covers round-off (at most 3 cm at the 120 m far plane). */
"static float hl_visibility(float3 p,float3 gn,constant HLLampGPU &l,depth2d<float> map) {\n"
" float4 q=hl_project(p,l);if(q.w<=0)return 0;float3 s=q.xyz/q.w;float2 uv=s.xy*float2(.5f,-.5f)+.5f;\n"
" if(any(uv<=0)||any(uv>=1)||s.z<0||s.z>1)return 0;\n"
" float2 size=float2(map.get_width(),map.get_height()),texel=uv*size-.5f,base=floor(texel),f=fract(texel);float sum=0;\n"
" float numerator=dot(gn,p-l.position.xyz);\n"
" for(uint y=0;y<2;++y)for(uint x=0;x<2;++x) {\n"
"  uint2 at=uint2(clamp(base+float2(x,y),float2(0),size-1));float2 t=(float2(at)+.5f)/size;\n"
"  float3 ray=l.forward.xyz+l.right.xyz*(2*t.x-1)+l.up.xyz*((1-2*t.y)*.122784560903f);\n"
"  float denominator=dot(gn,ray);float z=s.z;\n"
"  if(abs(denominator)>1e-12f) {float distance=numerator/denominator;if(distance<=0)continue;z=1.000834028357f-.100083402836f/distance;}\n"
"  float weight=(x?f.x:1-f.x)*(y?f.y:1-f.y);sum+=weight*float(z<=map.read(at)+.0000002f);\n"
" } return sum;\n"
"}\n"
"static float hl_lux(float3 p,float3 n,float3 gn,constant HLLampGPU &l,depth2d<float> map) {\n"
" if(l.position.w==0)return 0;float3 d=p-l.position.xyz;float r=length(d);if(r<.1f||r>=120)return 0;d/=r;\n"
" float x=dot(d,l.right.xyz),y=dot(d,l.up.xyz),z=dot(d,l.forward.xyz);if(z<=0)return 0;\n"
" float h=atan2(x,z)*57.2957795131f,v=atan2(y,length(float2(x,z)))*57.2957795131f;\n"
" float fade=r<=100?1:(120-r)/20;return hl_cookie(h,v)*max(-dot(n,d),0.0f)*fade/(r*r)*hl_visibility(p,gn,l,map)*l.position.w;\n"
"}\n"
"static float3 hl_light(float3 p,float3 n,float3 albedo,float3 fog,constant HLConstants &k,depth2d<float> a,depth2d<float> b) {\n"
" if(k.frame.z==0||k.frame.x!=k.frame.y)return float3(0);\n"
" if(any((as_type<uint3>(p)&0x7f800000u)==0x7f800000u)||any((as_type<uint3>(n)&0x7f800000u)==0x7f800000u)||abs(dot(n,n)-1.0f)>.01f)return float3(0);\n"
" float3 gn=cross(dfdx(p),dfdy(p));\n"
" float e=hl_lux(p,n,gn,k.lamps[0],a)+hl_lux(p,n,gn,k.lamps[1],b);return hl_linear(saturate(albedo))*e*(1.0f/314.159265359f)*fog;\n"
"}\n"
"static float3 hl_normalmap(float3 p,float3 n,float2 uv,float3 tex) {\n"
" float3 a=dfdx(p),b=dfdy(p);float2 u=dfdx(uv),v=dfdy(uv);\n"
" float3 t=a*v.y-b*u.y,bt=b*u.x-a*v.x;t*=u.x*v.y-v.x*u.y<0?-1:1;t-=n*dot(n,t);\n"
" if(length(t)<1e-8f)return n;t=normalize(t);float3 bn=cross(n,t);bn*=dot(bn,bt)<0?-1:1;\n"
" return normalize(t*tex.x+bn*tex.y+n*tex.z);\n"
"}\n";
const char *headlights_msl(void) {
    /* Only shader construction calls this, serialized by render.m. */
    static char *source;
    if(source)return source;
    char grid[8192];size_t at=0;
    const HLProfile *p=&hl_low_beam;
    at+=snprintf(grid+at,sizeof grid-at,"#define HL_NX %zuu\n#define HL_NY %zuu\nconstant float hl_h[]={",p->horizontal_count,p->vertical_count);
    for(size_t i=0;i<p->horizontal_count;++i)at+=snprintf(grid+at,sizeof grid-at,"%s%.9ff",i?",":"",p->horizontal_deg[i]);
    at+=snprintf(grid+at,sizeof grid-at,"};\nconstant float hl_v[]={");
    for(size_t i=0;i<p->vertical_count;++i)at+=snprintf(grid+at,sizeof grid-at,"%s%.9ff",i?",":"",p->vertical_deg[i]);
    at+=snprintf(grid+at,sizeof grid-at,"};\nconstant float hl_cd[]={");
    for(size_t i=0;i<p->horizontal_count*p->vertical_count;++i)at+=snprintf(grid+at,sizeof grid-at,"%s%.3ff",i?",":"",p->candela[i]);
    snprintf(grid+at,sizeof grid-at,"};\n");
    if(asprintf(&source,"#include <metal_stdlib>\nusing namespace metal;\n%s%s",grid,hl_math)<0)return NULL;
    return source;
}
static int hl_edit(char **s,const char *from,const char *to) {
    char *p=strstr(*s,from);if(!p||strstr(p+strlen(from),from))return 0;
    char *t=NULL;if(asprintf(&t,"%.*s%s%s",(int)(p-*s),*s,to,p+strlen(from))<0)return 0;
    free(*s);*s=t;return 1;
}
static int hl_param(char **s,const char *entry,const char *param) {
    char *p=strstr(*s,entry);if(!p)return 0;
    char *end=strstr(p,") {\n");if(!end)return 0;
    char *t=NULL;if(asprintf(&t,"%.*s,%s%s",(int)(end-*s),*s,param,end)<0)return 0;
    free(*s);*s=t;return 1;
}
static const char hl_varyings[] =
"    float3 hl_world [[user(hl_world)]];\n"
"    float3 hl_normal [[user(hl_normal)]];\n"
"    float3 hl_fog [[user(hl_fog)]];\n"
"    float2 hl_uv [[user(hl_uv)]];\n";

char *headlights_shader(const char *source,const SmResult *r,int caster,int material) {
    if(!source||!r)return NULL;
    char *s=NULL;
    if(asprintf(&s,"%s\n%s",headlights_msl(),source)<0)return NULL;
    int ok=1;
    if(r->shader_type==SM_VERTEX) {
        if(!strstr(s,"struct SmVSStageOut")) {free(s);return NULL;}
        ok=hl_param(&s,"vertex SmVSStageOut sm_main(","constant HLConstants &hl [[buffer(23)]]");
        char *fields=NULL;asprintf(&fields,"struct SmVSStageOut {\n%s",hl_varyings);
        ok=ok&&hl_edit(&s,"struct SmVSStageOut {\n",fields);free(fields);
        int position=-1,normal=-1,uv=-1;
        for(int i=0;i<r->n_inputs;++i) { const SmInput *a=&r->inputs[i];
            if(a->usage==SM_USAGE_POSITION&&!a->usage_index)position=a->reg;
            if(a->usage==SM_USAGE_NORMAL&&!a->usage_index)normal=a->reg;
            if(a->usage==SM_USAGE_TEXCOORD&&!a->usage_index)uv=a->reg;
        }
        if(position<0||normal<0||uv<0)ok=0;
        char *body=NULL;
        asprintf(&body,
            "    float4 hl_p=hl_transform(float4(si.v[%d].xyz,1),hl.world);\n"
            "    r.hl_world=hl_p.xyz/hl_p.w;\n"
            "    r.hl_normal=normalize(float3(dot(si.v[%d].xyz,hl.normal[0].xyz),dot(si.v[%d].xyz,hl.normal[1].xyz),dot(si.v[%d].xyz,hl.normal[2].xyz)));\n"
            "    r.hl_fog=exp(-hl.fog.xyz*clamp(length(si.v[%d].xyz-hl.local_eye.xyz)*hl.fog.w,0.0f,2500.0f));\n"
            "    r.hl_uv=so.tex[0].xy;\n%s    return r;\n",
            position,normal,normal,normal,position,
            caster?"    r.pos=hl_project(r.hl_world,hl.lamps[hl.frame.w]);\n":"");
        /* Cars use the diffuse UV in oT1; all world families use oT0. */
        if(material>=4) {char *t=strstr(body,"r.hl_uv=so.tex[0]");if(t)t[strlen("r.hl_uv=so.tex[")]='1';}
        ok=ok&&hl_edit(&s,"    return r;\n",body);free(body);
    } else if(r->shader_type==SM_PIXEL&&!caster) {
        int custom=strstr(s,"fragment Out sm_main(")!=NULL;
        const char *entry=custom?"fragment Out sm_main(":"fragment SmPSStageOut sm_main(";
        const char *structure=custom?"struct In {":"struct SmPSStageIn {";
        char *fields=NULL;asprintf(&fields,"%s\n%s",structure,hl_varyings);
        ok=hl_edit(&s,structure,fields);free(fields);
        char resources[256];
        snprintf(resources,sizeof resources,
            "constant HLConstants &hl [[buffer(%d)]], depth2d<float> hl_a [[texture(%d)]], depth2d<float> hl_b [[texture(%d)]]",
            HL_PS_BUFFER,HL_SHADOW_SLOT,HL_SHADOW_SLOT+1);
        ok=ok&&hl_param(&s,entry,resources);
        char diffuse[128],normal[256];
        snprintf(diffuse,sizeof diffuse,"tex%d.sample(smp%d,pi.hl_uv).rgb",material==5&&!custom?1:0,material==5&&!custom?1:0);
        strcpy(normal,"normalize(pi.hl_normal)");
        if(material==2||material==3)snprintf(normal,sizeof normal,"hl_normalmap(pi.hl_world,normalize(pi.hl_normal),pi.hl_uv,tex1.sample(smp1,pi.hl_uv).xyz*2.0f-1.0f)");
        if(custom&&material==4&&!strstr(source,"RW_CHROME"))snprintf(normal,sizeof normal,"m.k[3].w>.5f?hl_normalmap(pi.hl_world,normalize(pi.hl_normal),pi.hl_uv,(nmap.sample(smp0,pi.hl_uv).xyz*2.0f-1.0f)*float3(1.0f,m.k[7].y,1.0f)):normalize(pi.hl_normal)");
        char *term=NULL;asprintf(&term,"hl_light(pi.hl_world,%s,%s,pi.hl_fog,hl,hl_a,hl_b)",normal,diffuse);
        char *code=NULL;
        if(custom) {
            /* Car paint has a local shoulder; add before it. */
            const char *anchor=strstr(source,"RW_CHROME")?
                "    if (m.k[1].w>0.5f) {":
                "    if (m.k[1].w > 0.5f) {                       // NFS_HIGHLIGHTS=rolloff";
            asprintf(&code,"    o.c0.rgb += %s;\n%s",term,anchor);
            ok=ok&&hl_edit(&s,anchor,code);
        } else if(material==5&&strstr(s,"NFS_CAR_LIGHT=hdr")) {
            /* The t0 shoulder lives in sm_body. Pass its extra light explicitly. */
            ok=ok&&hl_param(&s,"static void sm_body(","float3 hl_add");
            char *call=strstr(s,"    sm_body(si, so, c, ci, cb, aux");
            char *end=call?strstr(call,");\n"):NULL;
            if(!end)ok=0;
            else {char *t=NULL;asprintf(&t,"%.*s,%s%s",(int)(end-s),s,term,end);free(s);s=t;}
            ok=ok&&hl_edit(&s,"    so.oc[0] = r0;\n","    r0.rgb += hl_add;\n    so.oc[0] = r0;\n");
        } else {
            asprintf(&code,"    so.oc[0].rgb += %s;\n    SmPSStageOut r;\n",term);
            ok=ok&&hl_edit(&s,"    SmPSStageOut r;\n",code);
        }
        free(code);free(term);
    }
    if(!ok){free(s);return NULL;}return s;
}

static int option=-1;
int headlights_enabled(void) {
#ifndef HEADLIGHTS_CORE_ONLY
    extern int diag_on;int postfx_look_variant(const char *),postfx_look_listed(const char *);
    /* Each held variant is one pass, too short for first-use shader compiles
     * and lamp maps: a list naming `headlighton` runs the feature all along. */
    if(diag_on&&(postfx_look_variant("headlightoff")||postfx_look_variant("nightold")))return 0;
    if(diag_on&&(postfx_look_variant("headlighton")||postfx_look_listed("headlighton")))return 1;
#endif
    int value=__atomic_load_n(&option,__ATOMIC_ACQUIRE);
    if(value<0){const char *s=getenv("NFS_HEADLIGHT_PROJECTION");value=!(s&&(!strcmp(s,"0")||!strcasecmp(s,"off")));__atomic_store_n(&option,value,__ATOMIC_RELEASE);}
    return value;
}
#ifndef HEADLIGHTS_CORE_ONLY
#include "render_internal.h"
#include "daynight.h"

static HLConstants prepared;
static uint64_t prepared_frame;
static uint32_t prepared_view;
static int valid,casting,cast_index,cast_failed,clear_map;
static uint64_t cast_requested,cast_emitted;
static id<MTLTexture> maps[2],colour;
static double inverse_vp[4][4];
static uint32_t storage;
int headlights_casting(void) { return casting; }
void headlights_missed(void) { if(casting)cast_failed=1; }
id<MTLTexture> headlights_colour(void) {return colour;}
id<MTLTexture> headlights_depth(void) {return maps[cast_index];}
id<MTLRenderCommandEncoder> headlights_encoder(id<MTLCommandBuffer> command) {
    MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
    p.colorAttachments[0].texture=colour;
    p.colorAttachments[0].loadAction=MTLLoadActionDontCare;p.colorAttachments[0].storeAction=MTLStoreActionDontCare;
    p.depthAttachment.texture=maps[cast_index];
    p.depthAttachment.loadAction=clear_map?MTLLoadActionClear:MTLLoadActionLoad;
    p.depthAttachment.clearDepth=1;p.depthAttachment.storeAction=MTLStoreActionStore;
    clear_map=0;++counters.passes;
    return [command renderCommandEncoderWithDescriptor:p];
}
void headlights_bind(id<MTLRenderCommandEncoder> e,const HLConstants *k,int receiver) {
    [e setVertexBytes:k length:sizeof *k atIndex:HL_VS_BUFFER];
    if(receiver) {
        [e setFragmentBytes:k length:sizeof *k atIndex:HL_PS_BUFFER];
        [e setFragmentTexture:maps[0] atIndex:HL_SHADOW_SLOT];
        [e setFragmentTexture:maps[1] atIndex:HL_SHADOW_SLOT+1];
    }
}
void headlights_drawn(int receiver) { if(receiver)++counters.receiver_draws;else {++counters.caster_draws;++cast_emitted;} }
static int hl_inverse(const double m[4][4],double out[4][4]) {
    double a[4][8];for(int i=0;i<4;++i)for(int j=0;j<8;++j)a[i][j]=j<4?m[i][j]:j-4==i;
    for(int i=0;i<4;++i) {
        int p=i;for(int j=i+1;j<4;++j)if(fabs(a[j][i])>fabs(a[p][i]))p=j;
        if(!isfinite(a[p][i])||fabs(a[p][i])<1e-12)return 0;
        for(int j=0;j<8;++j){double t=a[i][j];a[i][j]=a[p][j];a[p][j]=t;}
        double scale=a[i][i];for(int j=0;j<8;++j)a[i][j]/=scale;
        for(int k=0;k<4;++k)if(k!=i){double t=a[k][i];for(int j=0;j<8;++j)a[k][j]-=t*a[i][j];}
    }
    for(int i=0;i<4;++i)for(int j=0;j<4;++j){out[i][j]=a[i][j+4];if(!isfinite(out[i][j]))return 0;}
    return 1;
}
static int hl_reg(const SmResult *s,const char *name,int count) {
    for(int i=0;i<s->n_constants;++i) {
        const SmConstant *c=&s->constants[i];
        if(c->set==2&&c->count>=count&&!strcmp(c->name,name)&&c->reg+count<=256)return c->reg;
    }
    return -1;
}
int headlights_draw(const SmResult *vs,uint64_t pixel_hash,int main_target,HLConstants *out) {
    if(!headlights_enabled())return 0;
    int material=headlights_material(pixel_hash);
    /* 0x00982A20 is the render-target table entry being drawn (7 words at
     * 0093DAB8, word 1 its view id); each eView points at its entry at +0x60
     * (006B91A0 sets eViews[1]+0x60 to entry 0, the back buffer). */
    uint32_t target=M32(0x00982A20u);
    int main_view=target&&prepared_view&&target==M32(prepared_view+0x60);
    int opaque=dev.rs[7]==1&&dev.rs[14]&&!dev.rs[27]&&dev.rs[168]&&
        !mat_water_draw.mesh&&!mat_grass_draw.mesh;
    if(casting) {
        if(!material||!opaque)return 0;
    } else if(!headlights_admit(1,frame_serial,prepared_frame,valid,
                    main_view,main_target,opaque,material))return 0;
    int wvp=hl_reg(vs,"WorldViewProj",4);
    if(wvp<0){headlights_missed();return 0;}
    *out=prepared;
    double world[4][4],inverse[4][4];
    for(int i=0;i<4;++i)for(int j=0;j<4;++j) {
        world[i][j]=0;for(int k=0;k<4;++k)world[i][j]+=dev.vsc[wvp+k][i]*inverse_vp[k][j];
        out->world[j][i]=world[i][j];
    }
    /* Subtract an enabled lamp origin in double precision BEFORE packing the
     * vertex transform. Interpolating kilometre-sized world coordinates loses
     * millimetres per fragment and corrupts the receiver-plane derivatives.
     * Both caster and receiver now rasterize the same lamp-relative metres;
     * the projection helper uses only the VP's linear columns (w=0). */
    int anchor=prepared.lamps[0].position[3]>0?0:1;
    for(int i=0;i<3;++i) {
        double origin=prepared.lamps[anchor].position[i];
        out->world[i][3]=(float)(world[3][i]-origin);
        for(int lamp=0;lamp<2;++lamp)out->lamps[lamp].position[i]-=origin;
    }
    if(!hl_inverse(world,inverse)){headlights_missed();return 0;}
    for(int i=0;i<3;++i)for(int j=0;j<3;++j)out->normal[i][j]=inverse[i][j];
    int fog=hl_reg(vs,"Fog_Br_Plus_Bm",1),amount=hl_reg(vs,"FogValue",1),eye=hl_reg(vs,"LocalEyePos",1);
    if(fog>=0&&amount>=0){
        memcpy(out->fog,dev.vsc[fog],12);out->fog[3]=dev.vsc[amount][0];
        memcpy(prepared.fog,out->fog,sizeof out->fog);
    }
    // Car VSs do not carry fog constants. Reuse this frame's atmosphere from
    // the preceding world casters/receivers, with this car's own local eye.
    if(eye>=0)memcpy(out->local_eye,dev.vsc[eye],16);
    out->frame[0]=(uint32_t)frame_serial;out->frame[1]=(uint32_t)prepared_frame;
    out->frame[2]=valid;out->frame[3]=cast_index;
    if(casting)++cast_requested;
    return casting?HL_CASTER:HL_RECEIVER;
}
static HLVec3 hl_cross(HLVec3 a,HLVec3 b){return (HLVec3){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
static int hl_unit(HLVec3 *v) {
    double r=hypot(hypot(v->x,v->y),v->z);if(!isfinite(r)||r<1e-8)return 0;
    v->x/=r;v->y/=r;v->z/=r;return 1;
}
static int hl_player(Cpu *c,HLLamp lamps[2],uint32_t scratch) {
    uint32_t list=M32(0x0092D87Cu),n=M32(0x0092D884u),player=0;
    if(!list||!n||n>8)return 0;
    for(unsigned i=0;i<n;++i){uint32_t p=M32(list+i*4);if(p&&!M32(p+0x1c)){player=p;break;}}
    if(!player)return 0;
    c->ecx=player;uint32_t sim=call_guest(c,M32(M32(player)+4),0,NULL);if(!sim)return 0;
    c->ecx=sim;uint32_t id=call_guest(c,M32(M32(sim)+0x70),0,NULL);
    list=M32(0x009B37CCu);n=M32(0x009B37D4u);if(!id||!list||n>256)return 0;
    uint32_t conn=0;
    for(unsigned i=0;i<n;++i){uint32_t p=M32(list+4*i);if(p&&M32(p+0x30)==id&&M32(p+0x28)==3&&!M8(p+0x60)){conn=p;break;}}
    uint32_t info=conn?M32(conn+0x44):0;if(!info)return 0;
    c->ecx=conn;call_guest(c,M32(M32(conn)+0x18),1,&scratch);
    float pose[16];memcpy(pose,G+scratch,64);
    for(unsigned i=0;i<16;++i)if(!isfinite(pose[i]))return 0;
    uint32_t on=M32(info+0x1620),broken=M32(info+0x1624);
    /* ForceHeadlightsOn is the same retail override used by 00742950.
     * With no active state bits and no override, daytime flare sprites do not
     * count as illumination. Broken bits always win. */
    if(M32(0x009B092Cu))on|=3;
    uint32_t end=info+0x104,p=M32(end);unsigned seen=0;
    for(unsigned count=0;p&&p!=end&&count<256;++count,p=M32(p)) {
        uint32_t h=M32(p+8);int i=h==0x9DB90133u?0:h==0xD09091C6u?1:-1;
        if(i<0||(seen&(1u<<i)))continue;seen|=1u<<i;
        if(!(on&(1u<<i))||(broken&(1u<<i)))continue;
        float xyz[3],dir[3];memcpy(xyz,G+p+0x10,12);memcpy(dir,G+p+0x20,12);
        HLLamp *l=&lamps[i];double pos[3],f[3];
        for(int col=0;col<3;++col){pos[col]=pose[12+col];f[col]=0;for(int r=0;r<3;++r){pos[col]+=xyz[r]*pose[4*r+col];f[col]+=dir[r]*pose[4*r+col];}}
        l->position=(HLVec3){pos[0],pos[1],pos[2]};l->forward=(HLVec3){f[0],f[1],f[2]};
        l->up=(HLVec3){pose[8],pose[9],pose[10]};
        if(!hl_unit(&l->forward))continue;
        l->right=hl_cross(l->forward,l->up);if(!hl_unit(&l->right))continue;
        l->up=hl_cross(l->right,l->forward);if(!hl_unit(&l->up))continue;
        l->enabled=isfinite(pos[0])&&isfinite(pos[1])&&isfinite(pos[2]);
    }
    return lamps[0].enabled||lamps[1].enabled;
}
static int hl_resources(void) {
    if(!colour){
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:HL_MAP_SIZE height:HL_MAP_SIZE mipmapped:NO];
        d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageRenderTarget;
        colour=[mtl newTextureWithDescriptor:d];if(colour)++counters.allocations;
        d.pixelFormat=MTLPixelFormatDepth32Float;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        for(int i=0;i<2;++i){maps[i]=[mtl newTextureWithDescriptor:d];if(maps[i])++counters.allocations;}
    }
    return colour&&maps[0]&&maps[1];
}
static int hl_planes(uint32_t platform,const HLGpuLamp *l) {
    float planes[6][4];
    for(int i=0;i<6;++i) {
        double len=0;
        for(int j=0;j<4;++j){planes[i][j]=i==4?l->vp[2][j]:l->vp[3][j]+(i<4&&!(i&1)?1:-1)*l->vp[i/2][j];if(j<3)len+=planes[i][j]*planes[i][j];}
        if(!isfinite(len)||len<1e-12)return 0;
        for(int j=0;j<4;++j)planes[i][j]/=sqrt(len);
    }
    memcpy(G+platform+0x140,planes,sizeof planes);return 1;
}
/* Reuse the stock section BVHs, but walk EVERY resident section rather than
 * WhatSectionsShouldWeDraw's drivable/PVS list (00731870). No camera/sun list
 * reuse: TreeCull (00729640) tests the installed lamp planes. */
static int hl_cull(Cpu *c,uint32_t entry) {
    uint32_t end=0x009AA478u,p=M32(end);unsigned n=0;
    for(;p&&p!=end&&n<4096;++n,p=M32(p)) {
        if(M32(p+0x28)){c->ecx=p;call_guest(c,0x00729640u,1,&entry);}
    }
    ++counters.traversals;
    return p==end;
}
/* Device bindings own references (d3d9.m dev_bind). The casting pass's guest
 * Set* calls move those references, so the snapshot holds its own reference
 * to each binding and is rebound through the same counting, never copied. */
void d3d9_addref_guest(uint32_t guest);
void d3d9_release_guest(uint32_t guest);
static void hl_bindings(const Device *d,void (*f)(uint32_t)) {
    for(int i=0;i<20;++i)if(d->tex[i])f(d->tex[i]);
    for(int i=0;i<16;++i)if(d->stream[i])f(d->stream[i]);
    for(int i=0;i<4;++i)if(d->rt[i])f(d->rt[i]);
    if(d->indices)f(d->indices);
    if(d->ds)f(d->ds);
}
static void hl_rebind(uint32_t *slot,uint32_t guest) {
    if(*slot==guest)return;
    if(guest)d3d9_addref_guest(guest);
    uint32_t old=*slot;*slot=guest;
    if(old)d3d9_release_guest(old);
}
static void hl_restore(const Device *saved) {
    for(int i=0;i<20;++i)hl_rebind(&dev.tex[i],saved->tex[i]);
    for(int i=0;i<16;++i)hl_rebind(&dev.stream[i],saved->stream[i]);
    for(int i=0;i<4;++i)hl_rebind(&dev.rt[i],saved->rt[i]);
    hl_rebind(&dev.indices,saved->indices);hl_rebind(&dev.ds,saved->ds);
    uint64_t draws=dev.draws,frame_draws=dev.frame_draws;
    dev=*saved;dev.draws=draws;dev.frame_draws=frame_draws; /* every binding already equal */
    hl_bindings(saved,d3d9_release_guest);
}
void headlights_prepare(Cpu *c,uint32_t view,uint32_t flags) {
    /* Cached dispatch precedes guest reads, allocations, culls and pass splits. */
    if(!headlights_enabled()||casting||view!=0x00919650u||flags!=0x20)return;
    valid=0;memset(&prepared,0,sizeof prepared);
    /* Lamp level from the time-of-day clock (1 when it is off): no maps by day. */
    float level=daynight_lamp_level();
    if(!(level>0))return;
    uint32_t cull=c->ecx,platform=M32(view),camera=M32(view+0x40);
    if(!cull||!platform||!camera||M32(0x00925E90u)!=6||q_active)return;
    enum { table_bytes=5000*12,matrix_bytes=5001*64,scratch_bytes=1024*1024 };
    if(!storage){storage=gmalloc_try(table_bytes+matrix_bytes+scratch_bytes);if(storage)++counters.allocations;}
    if(!storage)return;
    Cpu saved=*c;HLLamp lamps[2]={0};
    if(!hl_player(c,lamps,storage+table_bytes+matrix_bytes)){*c=saved;return;}
    *c=saved;
    double vp[4][4];for(int i=0;i<4;++i)for(int j=0;j<4;++j)vp[i][j]=MF32(platform+0x80+16*i+4*j);
    if(diag_on && diag_shot_due(frame_serial)) {
        char line[1024];
        int at=snprintf(line,sizeof line,"headlights_view: frame=%llu geometry=",
            (unsigned long long)frame_serial);
        const HLVec3 axes[]={lamps[0].position,lamps[0].right,lamps[0].up,lamps[0].forward};
        for(int i=0;i<4;++i)at+=snprintf(line+at,sizeof line-at,"%.12g,%.12g,%.12g,",axes[i].x,axes[i].y,axes[i].z);
        for(int i=0;i<4;++i)for(int j=0;j<4;++j)at+=snprintf(line+at,sizeof line-at,"%.12g%s",vp[i][j],i==3&&j==3?"":",");
        diag_event("%s",line);
        // Reference road plane .65 m below the lamp, at known forward ranges.
        // Normalized screen coordinates allow distance-labelled capture ROIs;
        // actual sloping/curved scenery still needs inspection in the capture.
        for(int distance=5;distance<=60;distance+=5) {
            HLVec3 p=lamps[0].position;
            const HLVec3 f=lamps[0].forward,u=lamps[0].up;
            p.x+=distance*f.x-.65*u.x;p.y+=distance*f.y-.65*u.y;p.z+=distance*f.z-.65*u.z;
            double pos[4]={p.x,p.y,p.z,1},q[4]={0};
            for(int i=0;i<4;++i)for(int j=0;j<4;++j)q[i]+=pos[j]*vp[j][i];
            if(q[3]>0)diag_event("headlights_range: frame=%llu metres=%d screen=%.6f,%.6f lux=%.6f scale=%.3f",
                (unsigned long long)frame_serial,distance,.5+.5*q[0]/q[3],.5-.5*q[1]/q[3],
                hl_pair_lux(lamps,p,u),daynight_headlight_scale());
        }
    }
    if(!hl_inverse(vp,inverse_vp))return;
    if(!hl_resources())return;
    call_guest(c,0x006E2F50u,0,NULL); /* finish preceding sky/queue before snapshot */
    end_pass();
    Device device=dev;
    uint8_t entry_save[0xD0],view_save[0x70],camera_save[0x2c0],platform_save[0x1a0];
    uint32_t target=M32(0x00982A20u),effect=M32(0x00982C80u),arena[2],failed[2];
    memcpy(view_save,G+view,sizeof view_save);
    memcpy(camera_save,G+camera,sizeof camera_save);memcpy(platform_save,G+platform,sizeof platform_save);
    memcpy(arena,G+0x00915F84u,sizeof arena);memcpy(failed,G+0x00915F94u,sizeof failed);
    uint32_t entry=0;unsigned count=M32(cull+0x9c0);
    for(unsigned i=0;i<count&&i<12;++i)if(M32(cull+i*0xd0+0x80)==view){entry=cull+i*0xd0;break;}
    if(!entry){*c=saved;return;}
    hl_bindings(&device,d3d9_addref_guest);
    memcpy(entry_save,G+entry,sizeof entry_save);
    prepared_frame=frame_serial;prepared_view=view;
    float radiance_scale=daynight_headlight_scale();
    for(int i=0;i<2;++i){headlights_pack_lamp(&lamps[i],&prepared.lamps[i]);prepared.lamps[i].position[3]*=level*radiance_scale;} /* w: renderer lamp strength */
    casting=1;cast_failed=0;cast_requested=cast_emitted=0;
    for(int i=0;i<2;++i) {
        cast_index=i;clear_map=1;ensure_cmd();
        M32(0x00915F94u)=M32(0x00915F98u)=0;
        /* The first caster combines clear + draw. Disabled/empty lamps still
         * get a clear, so no receiver can read the preceding frame's map. */
        if(!lamps[i].enabled){id<MTLRenderCommandEncoder> empty=headlights_encoder(cmd);[empty endEncoding];continue;}
        if(!hl_planes(platform,&prepared.lamps[i])){cast_failed=1;continue;}
        memcpy(G+camera+0x40,prepared.lamps[i].position,12);memcpy(G+camera+0x50,prepared.lamps[i].forward,12);
        uint32_t args[3]={view,entry,0x6000};call_guest(c,0x004FAE40u,3,args);
        memcpy(G+entry+0xa0,prepared.lamps[i].position,12);memcpy(G+entry+0xb0,prepared.lamps[i].forward,12);
        M32(entry+0xc0)=M32(view+0xc);M32(entry+0xc4)=UINT32_MAX;
        M32(entry+0x88)=M32(entry+0x8c)=storage;M32(entry+0x90)=storage+table_bytes;
        M32(0x00915F84u)=storage+table_bytes;M32(0x00915F88u)=storage+table_bytes+matrix_bytes;
        if(!hl_cull(c,entry)||M32(entry+0x8c)>=M32(entry+0x90)||M32(0x00915F94u))cast_failed=1;
        /* Culling matrices live until the queue drains. Submission uses a
         * separate bounded arena and cannot consume receiver frame memory. */
        M32(0x00915F84u)=storage+table_bytes+matrix_bytes;
        M32(0x00915F88u)=storage+table_bytes+matrix_bytes+scratch_bytes;
        uint32_t a[2]={view,0x20};c->ecx=cull;call_guest(c,0x00723FA0u,2,a);
        call_guest(c,0x006E2F50u,0,NULL);
        /* OnRender submits body AND wheel transforms; its culling uses the
         * same temporary lamp planes. Other cars include off-camera blockers. */
        uint32_t list=M32(0x009B37CCu),n=M32(0x009B37D4u);
        if(n>256)cast_failed=1;
        uint32_t car=M32(0x0093DEBCu),dv=M32(0x00982BDCu);
        if(car&&dv) {
            M32(0x00982C80u)=car;c->ecx=car;call_guest(c,M32(M32(car)+4),0,NULL);
            uint32_t decl[2]={dv,M32(car+0x4c)};call_guest(c,M32(M32(dv)+0x15c),2,decl);
            uint32_t fx=M32(car+0x48),begin[3]={fx,storage+table_bytes+matrix_bytes+128,0};
            call_guest(c,M32(M32(fx)+0xfc),3,begin);
            a[1]=0;
            for(unsigned j=0;list&&j<n&&j<256;++j){uint32_t conn=M32(list+4*j);if(conn){c->ecx=conn;call_guest(c,M32(M32(conn)+0xc),2,a);}}
            c->ecx=car;call_guest(c,M32(M32(car)+8),0,NULL);
            call_guest(c,M32(M32(fx)+0x10c),1,&fx);M32(0x00982C80u)=0;
        } else cast_failed=1;
        call_guest(c,0x006E2F50u,0,NULL);end_pass();
        if(clear_map){id<MTLRenderCommandEncoder> empty=headlights_encoder(cmd);[empty endEncoding];}
        if(M32(0x00915F94u))cast_failed=1;
    }
    if(cast_requested!=cast_emitted)cast_failed=1;
    casting=0;
    memcpy(G+entry,entry_save,sizeof entry_save);memcpy(G+view,view_save,sizeof view_save);memcpy(G+camera,camera_save,sizeof camera_save);memcpy(G+platform,platform_save,sizeof platform_save);
    memcpy(G+0x00915F84u,arena,sizeof arena);memcpy(G+0x00915F94u,failed,sizeof failed);
    M32(0x00982A20u)=target;M32(0x00982C80u)=effect;
    /* Restoring an older constant-version number can alias a ring upload. */
    device.ver_vsc=dev.ver_vsc+1;device.ver_psc=dev.ver_psc+1;device.ver_vsi=dev.ver_vsi+1;
    device.ver_psi=dev.ver_psi+1;device.ver_vsb=dev.ver_vsb+1;device.ver_psb=dev.ver_psb+1;
    hl_restore(&device);pass_dirty=1;*c=saved;valid=!cast_failed;
    if(diag_on){static uint64_t last;if(frame_serial-last>=120){last=frame_serial;diag_event("headlights: valid=%d lamps=%d/%d allocations=%llu passes=%llu casters=%llu receivers=%llu reculls=%llu",valid,lamps[0].enabled,lamps[1].enabled,(unsigned long long)counters.allocations,(unsigned long long)counters.passes,(unsigned long long)counters.caster_draws,(unsigned long long)counters.receiver_draws,(unsigned long long)counters.traversals);}}
}
#endif
