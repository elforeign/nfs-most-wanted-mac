// Opt-in single-scattering approximation. Constants/references: docs/particles.md.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "particles_physical.h"

static int physical = -1;
extern uint64_t frame_serial;
static uint64_t lamp_frame;
static ParticleLamps lamps;
static uint32_t lamp_keys[8];
int postfx_look_variant(const char *variant);
int particles_physical_enabled(void) {
    if (diag_on && postfx_look_variant("softoff")) return 0;
    if (diag_on && (postfx_look_variant("softon") || postfx_look_variant("softbroken") ||
                   postfx_look_variant("softlitoff") || postfx_look_variant("softhdoff"))) return 1;
    if (physical < 0) {
        const char *e = getenv("NFS_PARTICLES");
        physical = e && !strcmp(e, "physical");
    }
    return physical;
}
void particles_physical_set(int enabled) { physical = enabled != 0; }

// Capture before flare facing/occlusion changes its brightness. Main-view car
// flares precede the emitter pass. Only the two decoded front marker hashes;
// exclude reflection/environment replays and never retain guest pointers.
void particles_physical_lamp(Cpu *cpu) {
    if(!particles_physical_enabled())return;
    uint32_t view=M32(cpu->esp+4), flare=M32(cpu->esp+8), matrix=M32(cpu->esp+12);
    if(!view || (M32(view+4)!=1 && M32(view+4)!=2) || !flare || !matrix || M32(cpu->esp+20) || M32(cpu->esp+24))return;
    uint32_t key=M32(flare+8);
    if(key!=0x9DB90133u && key!=0xD09091C6u)return;
    float intensity=MF32(cpu->esp+16);
    if(!isfinite(intensity) || intensity<=0)return;
    if(lamp_frame!=frame_serial){memset(&lamps,0,sizeof lamps);lamp_frame=frame_serial;}
    unsigned count=(unsigned)lamps.k[0][0], index=0;
    for(;index<count;++index)if(lamp_keys[index]==flare)break;
    if(index>=8)return;
    float *p=lamps.k[1+2*index], *d=lamps.k[2+2*index];
    float length=0;
    for(int c=0;c<3;++c){
        p[c]=MF32(matrix+48+4*c);d[c]=0;
        for(int r=0;r<3;++r){
            float m=MF32(matrix+16*r+4*c);
            p[c]+=MF32(flare+0x10+4*r)*m;
            d[c]+=MF32(flare+0x20+4*r)*m;
        }
        if(!isfinite(p[c]) || !isfinite(d[c]))return;
        length+=d[c]*d[c];
    }
    if(length<1e-8f)return;
    for(int c=0;c<3;++c)d[c]/=sqrtf(length);
    p[3]=fminf(1,intensity);d[3]=60.0f;
    lamp_keys[index]=flare;
    if(index==count)lamps.k[0][0]=count+1;
}
void particles_physical_lamps(ParticleLamps *out) {
    if(lamp_frame==frame_serial && particles_physical_enabled())*out=lamps;
    else memset(out,0,sizeof *out);
    if(diag_on && postfx_look_variant("softhdoff"))memset(out,0,sizeof *out);
}

static const char particles_phase_msl[] =
"struct ParticleLamps { float4 k[17]; };\n"
"inline float particle_hg(float mu, float g) {\n"
"    float d = max(1.0f + g*g - 2.0f*g*clamp(mu,-1.0f,1.0f), 1.0e-6f);\n"
"    return (1.0f-g*g) / (12.566370614359172f * d * sqrt(d));\n"
"}\n"
"static float3 particle_world(float4 p, constant MatConsts &m) {\n"
"    float4 q=p.x*m.k[4]+p.y*m.k[5]+p.z*m.k[6]+p.w*m.k[7];\n"
"    return q.xyz / q.w;\n"
"}\n"
"static float3 particle_light(float4 p, constant MatConsts &m, constant ParticleLamps &lamps, float visibility) {\n"
"    float2 xy=float2(p.x*m.k[3].x+m.k[11].x, p.y*m.k[3].y+m.k[11].y);\n"
"    float3 a=particle_world(float4(xy,0.0f,1.0f),m);\n"
"    float3 b=particle_world(float4(xy,0.5f,1.0f),m);\n"
"    float3 ray=normalize(b-a);\n"
"    float phase=12.566370614359172f*particle_hg(dot(ray,m.k[10].xyz),m.k[8].w);\n"
"    float3 light=m.k[8].xyz + m.k[9].xyz*visibility*phase;\n"
"    float3 world=particle_world(float4(xy,p.z,1.0f),m);\n"
"    for(uint i=0;i<min(uint(lamps.k[0].x),8u);++i) {\n"
"        float4 h=lamps.k[1+2*i], f=lamps.k[2+2*i];\n"
"        float3 delta=world-h.xyz; float r=length(delta);\n"
"        if(r<0.05f || r>=f.w)continue;\n"
"        float3 q=delta/r;\n"
"        float cone=smoothstep(0.819152f,0.965926f,dot(q,f.xyz));\n"
"        float attenuation=pow(1.0f-r/f.w,2.0f)/(1.0f+r*r/64.0f);\n"
"        light+=float3(h.w*cone*attenuation*12.566370614359172f*particle_hg(dot(q,-ray),m.k[8].w));\n"
"    }\n"
"    return m.k[9].w*light;\n"
"}\n";

// Replace one known anchor; fail closed if the source contract has changed.
static int particle_edit(char **source, const char *from, const char *to) {
    char *at=strstr(*source,from);
    if (!at || strstr(at+strlen(from),from)) return 0;
    char *next=NULL;
    if (asprintf(&next,"%.*s%s%s",(int)(at-*source),*source,to,at+strlen(from))<0) return 0;
    free(*source);*source=next;return 1;
}
char *particles_physical_shader(char *source) {
    char *head=NULL;
    if (asprintf(&head,"%sfragment Out sm_main(",particles_phase_msl)<0) { free(source);return NULL; }
    int ok=particle_edit(&source,"struct MatConsts { float4 k[4]; };","struct MatConsts { float4 k[12]; };") &&
        particle_edit(&source,"fragment Out sm_main(",head) &&
        particle_edit(&source,"constant MatConsts &m [[buffer(4)]],","constant MatConsts &m [[buffer(4)]], constant ParticleLamps &lamps [[buffer(6)]],") &&
        particle_edit(&source,"    Out o;\n","    Out o; float physical_visibility=1.0f;\n") &&
        particle_edit(&source,
            "        float shadow = saturate(n * (1.0f / 9.0f) + saturate(1.0f - 4.0f * uv.y));\n",
            "        float shadow = saturate(n * (1.0f / 9.0f) + saturate(1.0f - 4.0f * uv.y));\n        physical_visibility=shadow;\n") &&
        particle_edit(&source,"        float s = lit1(tex1, smp1, uv, z);\n",
            "        float s = lit1(tex1, smp1, uv, z);\n        physical_visibility=s;\n") &&
        particle_edit(&source,"    return o;\n",
            "    if(m.k[3].w > 0.5f && m.k[0].z < 0.5f && m.k[2].y > 0.5f) {\n"
            "        o.c0.rgb=2.0f*tex.rgb*v0.rgb*particle_light(pi.pos,m,lamps,physical_visibility);\n"
            "    }\n    return o;\n");
    free(head);
    if (!ok) { free(source);return NULL; }
    return source;
}

static int particle_inverse(const float *input, float output[4][4]) {
    double a[4][8]={0};
    for(int r=0;r<4;++r)for(int c=0;c<4;++c){a[r][c]=input[4*r+c];a[r][c+4]=r==c;}
    for(int c=0;c<4;++c){
        int pivot=c;for(int r=c+1;r<4;++r)if(fabs(a[r][c])>fabs(a[pivot][c]))pivot=r;
        if(!isfinite(a[pivot][c]) || fabs(a[pivot][c])<1e-12)return 0;
        for(int j=0;j<8;++j){double t=a[c][j];a[c][j]=a[pivot][j];a[pivot][j]=t;}
        double scale=a[c][c];for(int j=0;j<8;++j)a[c][j]/=scale;
        for(int r=0;r<4;++r)if(r!=c){double f=a[r][c];for(int j=0;j<8;++j)a[r][j]-=f*a[c][j];}
    }
    for(int r=0;r<4;++r)for(int c=0;c<4;++c){if(!isfinite(a[r][c+4]))return 0;output[r][c]=(float)a[r][c+4];}
    return 1;
}
void particles_physical_constants(MatConsts *out, unsigned view_id, const uint32_t viewport[4]) {
    if(!particles_physical_enabled() || (view_id!=1 && view_id!=2) || !viewport[2] || !viewport[3])return;
    uint32_t view=M32(0x009195E0u+view_id*0x70u);
    if(!view || !particle_inverse((const float *)(G+view+0x80),&out->k[4]))return;
    out->k[3][0]=2.0f/viewport[2];out->k[3][1]=-2.0f/viewport[3];out->k[3][3]=1;
    out->k[11][0]=-1.0f-viewport[0]*out->k[3][0];
    out->k[11][1]=1.0f-viewport[1]*out->k[3][1];
    if(diag_on && postfx_look_variant("softlitoff"))out->k[3][3]=0;
    lighting_particle_scene(out->k[8],out->k[9],out->k[10]);
    // Mineral aerosol proxy, not a calibration of tyre smoke or this scene's dust.
    out->k[8][3]=0.682f;out->k[9][3]=0.93f;
    if(diag_on && getenv("BOOT_DIAG_SOFT_SMOKE_COST")){
        static uint64_t noted;
        if(noted!=frame_serial){noted=frame_serial;
            diag_event("particle scene: frame %llu ambient %.4f %.4f %.4f sun %.4f %.4f %.4f direction %.4f %.4f %.4f lamps %u",
                (unsigned long long)frame_serial,out->k[8][0],out->k[8][1],out->k[8][2],out->k[9][0],out->k[9][1],out->k[9][2],
                out->k[10][0],out->k[10][1],out->k[10][2],lamp_frame==frame_serial?(unsigned)lamps.k[0][0]:0);
        }
    }
}
