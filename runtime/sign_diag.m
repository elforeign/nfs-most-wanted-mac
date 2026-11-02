// BOOT_DIAG_SIGN_AB: one manually armed scene, four matched sampling variants.
// This experiment has no timing interpretation. Invalid evidence is retained
// with a rejection reason; it never becomes a successful visual comparison.
#include "sign_diag.h"
#include "render_internal.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <zlib.h>

int sign_diag_on;
int pacing_lockstep_now(void);
id rt_render_list_new(void);
void rt_render_list_replay(id list, id<MTLRenderCommandEncoder> encoder);
enum { SD_STILL = 8, SD_YAW = 64, SD_RETREAT_MAX = 96 };
static int armed, finished, rejected, variant, phase, step, retreat_count;
static uint64_t serial_seen;
static struct timespec started;
static uint32_t camera, matrices, scratch;
static uint8_t c0[0x2C0];
static FILE *trace;
static float retreat_lod = -100;
static unsigned frame_draw, current_plane, nseen;
static struct { uint64_t key; unsigned count; } seen[128];
static id<MTLTexture> uv_target, uv_ms;
static id<MTLBuffer> uv_read, scene_read, final_read;
static NSUInteger scene_format, frame_w, frame_h;
static uint64_t uv_serial;
static NSMutableDictionary *pipeline_desc, *pipeline_source, *direct_functions, *uv_functions, *uv_pipelines;
static char frame_stem[1024];

typedef struct { uint32_t guest, hash, w, h, levels, old_levels; char name[25]; Obj *object; int pc_control; } SignTexture;
static SignTexture textures[128];
static unsigned ntextures;
typedef struct {
    uint64_t key, vs, ps;
    uint32_t hash, texture, w, h, levels, old_levels, index;
    float ps_constants[224][4], vs_constants[256][4];
    uint8_t freeze_vs[256], freeze_ps[224];
} SignPlane;
static SignPlane *planes;
static unsigned nplanes;
static struct { char name[48]; void *bytes; size_t size; } frozen[16];
static unsigned nfrozen;
static uint64_t hash_bytes(const void *p, size_t n) {
    const unsigned char *b = p; uint64_t h = 14695981039346656037ull;
    while (n--) h = (h ^ *b++) * 1099511628211ull;
    return h;
}
static void path_for(char *path, size_t n, const char *name) { snprintf(path,n,"%s/sign_%s",diag_dir,name); }
static void reject(const char *why) {
    if (!rejected && trace) { fprintf(trace,"REJECT %s\n",why); fflush(trace); }
    rejected = 1;
}
__attribute__((constructor)) static void setup(void) {
    const char *e = getenv("BOOT_DIAG_SIGN_AB"); sign_diag_on = e && !strcmp(e,"1");
    if(sign_diag_on)clock_gettime(CLOCK_REALTIME,&started);
}
int sign_diag_active(void) { return sign_diag_on && armed && !finished; }
int sign_diag_hold(void) { return sign_diag_active(); }
int sign_diag_selected(uint32_t hash, const char *name) {
    return hash == 0x6EBF6F28 || hash == 0xED2BD84A || (name && !strncmp(name,"REF_NEONS",9));
}
static SignTexture *texture(uint32_t guest) {
    for (unsigned i=0;i<ntextures;++i) if (textures[i].guest==guest) {
        Obj *o=obj(guest);
        if(o && o==textures[i].object && o->refs>0 && o->w==textures[i].w && o->h==textures[i].h && o->levels==textures[i].levels)return &textures[i];
    }
    return NULL;
}
void sign_diag_texture(uint32_t ti, uint32_t pi) {
    uint32_t hash=M32(ti+0x24); const char *name=(const char *)G+ti+12;
    if (!pi || !sign_diag_selected(hash,name)) return;
    Obj *o=obj(M32(pi+0x18)); if (!o) return;
    SignTexture *t=texture(o->guest);
    if (!t) { if (ntextures==128) { reject("texture registry full"); return; } t=&textures[ntextures++]; }
    *t=(SignTexture){.guest=o->guest,.hash=hash,.w=o->w,.h=o->h,.levels=o->levels,.object=o,.pc_control=!o->hd_swap};
    memcpy(t->name,name,24);
    t->old_levels=o->levels;
    if (o->hd_swap && is_dxt(o->fmt)) {
        t->old_levels=0;
        while (t->old_levels<o->levels && mipdim(o->w,t->old_levels)>=4 && mipdim(o->h,t->old_levels)>=4) ++t->old_levels;
    }
}
int sign_diag_canonical(unsigned stage) {
    SignTexture *t=stage<20?texture(dev.tex[stage]):NULL;
    return sign_diag_active() && variant==2 && t && (t->hash==0x6EBF6F28 || t->hash==0xED2BD84A);
}
id<MTLTexture> sign_diag_texture_view(uint32_t guest,id<MTLTexture> full) {
    SignTexture *t=texture(guest);
    if (sign_diag_active() && t && current_plane && trace) fprintf(trace,"gpu %s plane=%u guest=%08X full=%lu effective=%u clamp_max=%u sample_mode=%s\n",frame_stem,current_plane,guest,(unsigned long)full.mipmapLevelCount,variant==0?t->old_levels:t->levels,(variant==0?t->old_levels:t->levels)-1,variant==3?"direct-diffuse":"translated");
    if (!sign_diag_active() || variant!=0 || !t || (t->hash!=0x6EBF6F28 && t->hash!=0xED2BD84A) || t->old_levels==t->levels) return full;
    if (!t->old_levels || full.mipmapLevelCount!=t->levels) { reject("missing full texture or safe prefix"); return full; }
    // A shares precisely the original prefix bytes; the view clamps access to
    // those levels without changing dimensions, sampling, or any other draw.
    return [full newTextureViewWithPixelFormat:full.pixelFormat textureType:full.textureType
        levels:NSMakeRange(0,t->old_levels) slices:NSMakeRange(0,1)];
}
void sign_diag_freeze(const char *name,void *bytes,size_t size) {
    if (!sign_diag_active()) return;
    unsigned i=0; while (i<nfrozen && strcmp(frozen[i].name,name)) ++i;
    if (i==nfrozen) {
        if (nfrozen==16 || strlen(name)>=sizeof frozen[i].name) { reject("history registry full"); return; }
        strcpy(frozen[i].name,name); frozen[i].bytes=malloc(size); frozen[i].size=size;
        if (!frozen[i].bytes) { reject("history allocation"); return; }
        memcpy(frozen[i].bytes,bytes,size); ++nfrozen;
        char path[1200]; snprintf(path,sizeof path,"%s/sign_history_%s.bin",diag_dir,name);
        FILE *f=fopen(path,"wb"); if (f) { fwrite(bytes,1,size,f); fclose(f); } else reject("history write");
        if (trace) fprintf(trace,"history %s size=%zu hash=%016llX\n",name,size,(unsigned long long)hash_bytes(bytes,size));
    }
    if (frozen[i].size!=size) { reject("history size changed"); return; }
    memcpy(bytes,frozen[i].bytes,size);
}
void sign_diag_record_history(const char *name,const void *bytes,size_t size) {
    if(!sign_diag_active() || !bytes || !size)return;
    void *copy=malloc(size);if(!copy){reject("history record allocation");return;}
    memcpy(copy,bytes,size);sign_diag_freeze(name,copy,size);free(copy);
}
// Camera matrices are row-vector view transforms. Postmultiply by a local yaw;
// retreat translates the camera along its original negative viewing axis.
static void pose(float *out,const float *in,double yaw,double retreat) {
    memcpy(out,in,64);
    double cs=cos(yaw),sn=sin(yaw);
    for (unsigned row=0;row<4;++row) {
        out[row*4]=(float)(in[row*4]*cs-in[row*4+2]*sn);
        out[row*4+2]=(float)(in[row*4]*sn+in[row*4+2]*cs);
    }
    out[14]+=(float)retreat;
}
void sign_diag_world(Cpu *c) {
    if (!diag_on || !sign_diag_on || finished || M32(0x00925E90u)!=6) return;
    if (!armed) {
        char path[1200]; snprintf(path,sizeof path,"%s/arm",diag_dir);
        struct stat arm;
        if (stat(path,&arm) || arm.st_mtimespec.tv_sec<started.tv_sec ||
            (arm.st_mtimespec.tv_sec==started.tv_sec && arm.st_mtimespec.tv_nsec<started.tv_nsec)) return;
        camera=M32(0x00919690u); matrices=M32(0x00919650u);
        if (!camera || !matrices) return;
        path_for(path,sizeof path,"manifest.txt");trace=fopen(path,"w");
        if (!trace) { finished=1; return; }
        armed=1; memcpy(c0,G+camera,sizeof c0); scratch=gmalloc(64);
        if (!scratch) { reject("camera scratch allocation");finished=1;return; }
        fprintf(trace,"schema 2\nquality_drop %u\ncamera %08X matrices %08X fov %u\n",M32(0x00982AE0u),camera,matrices,M16(camera+0xC4));
        path_for(path,sizeof path,"C0.bin"); FILE *f=fopen(path,"wb"); if (f) { fwrite(c0,1,sizeof c0,f);fclose(f); } else reject("C0 write");
        path_for(path,sizeof path,"C0_view.bin"); f=fopen(path,"wb"); if(f){fwrite(G+matrices,1,0xC0,f);fclose(f);}else reject("C0 matrices write");
        if (!pacing_lockstep_now()) reject("requires race lockstep");
        if (!mtl || render_msaa_samples()!=2 || backbuffer_tex.width!=3840 || backbuffer_tex.height!=2160) reject("requires actual 3840x2160 and 2x MSAA");
        fprintf(trace,"output %lux%lu samples=%d\n",(unsigned long)backbuffer_tex.width,(unsigned long)backbuffer_tex.height,render_msaa_samples());
        MF32(0x009259BCu)=0;
        for(unsigned i=0;i<ntextures;++i){
            SignTexture *t=&textures[i];
            if(t->hash==0x6EBF6F28 || t->hash==0xED2BD84A){
                if(t->pc_control || t->levels<=t->old_levels || mipdim(t->w,t->levels-1)!=1 || mipdim(t->h,t->levels-1)!=1)reject("sign needs a complete HD chain and an original truncated prefix");
            }else if(!t->pc_control)reject("REF_NEONS control is not a PC texture");
        }
        phase=step=variant=0;
    }
    if (serial_seen==frame_serial) return;
    serial_seen=frame_serial;frame_draw=0;nseen=0;
    if (camera!=M32(0x00919690u) || matrices!=M32(0x00919650u)) { reject("camera allocation changed");gfree(scratch);scratch=0;finished=1;return; }
    memcpy(G+camera,c0,sizeof c0);
    double yaw=phase==1?(-.25+(double)step*.5/63.)*(M_PI/180.):0;
    double retreat=phase==2?2.*(pow(1.08,step+1)-1.):0;
    float base[16],out[16];memcpy(base,c0,sizeof base);pose(out,base,yaw,retreat);memcpy(G+scratch,out,sizeof out);
    Cpu saved=*c; uint32_t args[2]={scratch,0x3F800000u}; c->ecx=camera;
    call_guest(c,0x004700D0u,2,args);*c=saved;
    if(memcmp(G+camera,out,sizeof out) || memcmp(G+camera+0xC4,c0+0xC4,2))reject("camera replay or fixed FOV was not applied");
    // Rebuild projection, view-projection and frustum using the established
    // widescreen hook, holding the exact captured FOV and near/far planes.
    uint32_t setup_args[5]={0x00919650u,0,0,0,1};c->ecx=matrices;
    call_guest(c,0x006CF400u,5,setup_args);*c=saved;
    snprintf(frame_stem,sizeof frame_stem,"%c_%d_%03d",'A'+variant,phase,step);
    fprintf(trace,"frame %s camera=%016llX viewproj=%016llX yaw=%.12g retreat=%.12g sim=%08X real=%08X fov=%u\n",
        frame_stem,(unsigned long long)hash_bytes(G+camera,0xE0),(unsigned long long)hash_bytes(G+matrices,0xC0),yaw,retreat,
        M32(0x009885D8u),M32(0x008F82F4u),M16(camera+0xC4));fflush(trace);
    char path[1200];snprintf(path,sizeof path,"%s/sign_%s_pose.bin",diag_dir,frame_stem);
    FILE *f=fopen(path,"wb");if(f){fwrite(G+camera,1,0xE0,f);fwrite(G+matrices,1,0xC0,f);fclose(f);}else reject("pose write");
    retreat_lod=-100;
}

// Extract the actual diffuse sample coordinate, including dependent UV math,
// from the translated body. Multiple samples or unsupported output layouts
// reject the experiment instead of guessing a varying or inventing a shader.
static NSString *instrument(const char *source,int direct) {
    if (!source) return nil;
    NSString *s=[NSString stringWithUTF8String:source];
    NSString *needle=@"tex0.sample(smp0, "; NSRange r=[s rangeOfString:needle];
    if (r.location==NSNotFound || [s rangeOfString:needle options:0 range:NSMakeRange(NSMaxRange(r),s.length-NSMaxRange(r))].location!=NSNotFound) return nil;
    NSUInteger start=NSMaxRange(r),end=start,depth=0;
    for (;end<s.length;++end) {
        unichar c=[s characterAtIndex:end];
        if (c=='(') ++depth;
        else if (c==')') { if (!depth) break; --depth; }
        else if (c==',' && !depth) break;
    }
    if (end==s.length) return nil;
    NSString *uv=[s substringWithRange:NSMakeRange(start,end-start)];
    NSUInteger close=end;depth=0;
    for (;close<s.length;++close) {
        unichar c=[s characterAtIndex:close];
        if (c=='(') ++depth;
        else if (c==')') { if (!depth) break;--depth; }
    }
    if (close==s.length) return nil;
    NSString *sample=[s substringWithRange:NSMakeRange(r.location,close-r.location+1)];
    NSString *option=[[s substringWithRange:NSMakeRange(end,close-end)] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    NSString *lod=[NSString stringWithFormat:@"tex0.calculate_unclamped_lod(smp0,(%@))",uv];
    if ([option hasPrefix:@","]) option=[[option substringFromIndex:1] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (option.length) {
        if ([option hasPrefix:@"bias("] && [option hasSuffix:@")"])
            lod=[NSString stringWithFormat:@"(%@)+(%@)",lod,[option substringWithRange:NSMakeRange(5,option.length-6)]];
        else if ([option hasPrefix:@"level("] && [option hasSuffix:@")"])
            lod=[option substringWithRange:NSMakeRange(6,option.length-7)];
        else return nil; // supplied gradients require a different footprint probe
    }
    NSString *replacement=[NSString stringWithFormat:@"(so.sd_uv=(%@),so.sd_lod=(%@),so.sd_sample=(%@))",uv,lod,sample];
    s=[s stringByReplacingCharactersInRange:NSMakeRange(r.location,close-r.location+1) withString:replacement];
    NSString *decl=@"struct SmPSOut {";
    if (![s containsString:decl] || ![s containsString:@"    r.c0 = so.oc[0];"]) return nil;
    s=[s stringByReplacingOccurrencesOfString:decl withString:@"struct SmPSOut { float2 sd_uv; float sd_lod; float4 sd_sample;"];
    s=[s stringByReplacingOccurrencesOfString:@"SmPSOut so;" withString:@"SmPSOut so; so.sd_uv=float2(NAN); so.sd_lod=NAN; so.sd_sample=float4(0);"];
    if (direct) {
        // Retain the translated alpha test/kill and the original blend state.
        s=[s stringByReplacingOccurrencesOfString:@"    r.c0 = so.oc[0];" withString:@"    r.c0 = float4(so.sd_sample.rgb,so.oc[0].a);"];
    } else {
        // Unclamped LOD is measured before the A view's terminal clamp. The
        // minor-axis footprint is recorded separately for anisotropic evidence.
        NSString *body=@"    float2 sd_dx=dfdx(so.sd_uv)*float2(tex0.get_width(),tex0.get_height());\n"
            "    float2 sd_dy=dfdy(so.sd_uv)*float2(tex0.get_width(),tex0.get_height());\n"
            "    float sd_a=dot(sd_dx,sd_dx),sd_b=dot(sd_dx,sd_dy),sd_c=dot(sd_dy,sd_dy);\n"
            "    float sd_minor=.5*log2(max(1e-20f,.5f*(sd_a+sd_c-sqrt(max(0.f,(sd_a-sd_c)*(sd_a-sd_c)+4*sd_b*sd_b)))));\n"
            "    r.c0=float4(so.sd_uv,so.sd_lod,sd_id*64.f+clamp(sd_minor,-24.f,24.f));";
        s=[s stringByReplacingOccurrencesOfString:@"    r.c0 = so.oc[0];" withString:body];
        s=[s stringByReplacingOccurrencesOfString:@"constant SmPSAux &aux [[buffer(3)]]" withString:@"constant SmPSAux &aux [[buffer(3)]], constant float &sd_id [[buffer(24)]]"];
    }
    return s;
}
static id<MTLFunction> function_for(const char *source,uint64_t key,int direct) {
    NSMutableDictionary *__strong *cache=direct?&direct_functions:&uv_functions;
    if (!*cache) *cache=[NSMutableDictionary new];NSNumber *k=@(key);
    id fn=(*cache)[k];if (fn) return fn==[NSNull null]?nil:fn;
    NSString *s=instrument(source,direct);NSError *err=nil;
    id<MTLLibrary> lib=s?[mtl newLibraryWithSource:s options:nil error:&err]:nil;
    fn=[lib newFunctionWithName:@"sm_main"];
    if (!fn) { reject(direct?"direct diffuse shader unsupported":"UV footprint shader unsupported"); if (trace) fprintf(trace,"shader_error %s\n",err.description.UTF8String?:"unrecognized sample layout"); }
    if (s && trace) {
        char original_path[1200];snprintf(original_path,sizeof original_path,"%s/sign_%016llX_original.metal",diag_dir,(unsigned long long)key);
        FILE *original=fopen(original_path,"w");if(original){fputs(source,original);fclose(original);}
        char path[1200];snprintf(path,sizeof path,"%s/sign_%016llX_%s.metal",diag_dir,(unsigned long long)key,direct?"direct":"uv");
        FILE *f=fopen(path,"w");if(f){fputs(s.UTF8String,f);fclose(f);}
    }
    (*cache)[k]=fn?:[NSNull null];return fn;
}
id<MTLFunction> sign_diag_direct(uint64_t key,const char *source) {
    SignTexture *t=texture(dev.tex[0]);
    if (!sign_diag_active() || !current_plane || variant!=3 || !t || (t->hash!=0x6EBF6F28 && t->hash!=0xED2BD84A)) return nil;
    return function_for(source,key,1);
}
void sign_diag_pipeline(uint64_t key,MTLRenderPipelineDescriptor *descriptor,const char *source) {
    // A sign may reuse a pipeline first created for unrelated art. Retain all
    // descriptors while enabled so arming never depends on first-draw order.
    if (!source) return;
    if (!pipeline_desc) {pipeline_desc=[NSMutableDictionary new];pipeline_source=[NSMutableDictionary new];}
    pipeline_desc[@(key)]=[descriptor copy];pipeline_source[@(key)]=[NSString stringWithUTF8String:source];
}
void sign_diag_draw(uint64_t vs,uint64_t ps,unsigned type,unsigned prims,int indexed,int32_t base,unsigned start,const SmResult *vsinfo,const SmResult *psinfo) {
    current_plane=0;
    if (!sign_diag_active()) return;
    SignTexture *t=texture(dev.tex[0]);if (!t) return;
    // Exclude reflection/shadow/capture views: only the native-size main scene.
    NSUInteger level,slice;id<MTLTexture> target=surface_target(dev.rt[0],&level,&slice);
    if (!target || target.width!=3840 || target.height!=2160 || dev.viewport[2]!=3840 || dev.viewport[3]!=2160) return;
    uint64_t words[]={t->hash,vs,ps,dev.stream[0],dev.indices,type,prims,(uint64_t)indexed,(uint32_t)base,start};
    uint64_t signature=hash_bytes(words,sizeof words);unsigned occurrence=0;
    while(occurrence<nseen && seen[occurrence].key!=signature)++occurrence;
    if(occurrence==nseen){if(nseen==128){reject("draw signature registry full");return;}seen[nseen++]=(typeof(seen[0])){signature,0};}
    uint64_t identity[2]={signature,seen[occurrence].count++};
    uint64_t key=hash_bytes(identity,sizeof identity);unsigned i=0;while (i<nplanes && planes[i].key!=key) ++i;
    if (i==nplanes) {
        if (variant || phase || step || nplanes==128) { reject("selected draw identities changed after C0");return; }
        SignPlane *next=realloc(planes,(nplanes+1)*sizeof *planes);if (!next) {reject("plane allocation");return;}planes=next;
        SignPlane *p=&planes[nplanes++];memset(p,0,sizeof *p);p->key=key;p->index=i+1;p->hash=t->hash;p->texture=t->guest;
        p->w=t->w;p->h=t->h;p->levels=t->levels;p->old_levels=t->old_levels;p->vs=vs;p->ps=ps;
        memcpy(p->ps_constants,dev.psc,sizeof dev.psc);memcpy(p->vs_constants,dev.vsc,sizeof dev.vsc);
        for (unsigned stage=0;stage<2;++stage) {
            const SmResult *info=stage?psinfo:vsinfo;unsigned limit=stage?224:256;
            uint8_t *mask=stage?p->freeze_ps:p->freeze_vs;
            for (int k=0;k<info->n_constants;++k) {
                const SmConstant *v=&info->constants[k];
                NSString *name=v->name[0]?[[NSString stringWithUTF8String:v->name] lowercaseString]:nil;
                int animated=[name containsString:@"time"]||[name containsString:@"anim"]||[name containsString:@"scroll"]||[name containsString:@"uv"];
                if (v->set==2 && animated && ![name containsString:@"world"] && ![name containsString:@"view"] && ![name containsString:@"proj"])
                    for(unsigned j=v->reg;j<v->reg+v->count && j<limit;++j)mask[j]=1;
            }
        }
        if (trace) fprintf(trace,"plane %u hash=%08X guest=%08X size=%ux%u levels=%u old=%u name=%s key=%016llX\n",p->index,t->hash,t->guest,t->w,t->h,t->levels,t->old_levels,t->name,(unsigned long long)key);
    }
    SignPlane *p=&planes[i];current_plane=i+1;++frame_draw;
    // Freeze named UV/time inputs only; camera and lighting constants stay live.
    for(unsigned j=0;j<224;++j)if(p->freeze_ps[j])memcpy(dev.psc[j],p->ps_constants[j],sizeof dev.psc[j]);
    ++dev.ver_psc;
    for(unsigned j=0;j<256;++j)if(p->freeze_vs[j])memcpy(dev.vsc[j],p->vs_constants[j],sizeof dev.vsc[j]);
    ++dev.ver_vsc;
    if (trace) {
        float bias;memcpy(&bias,&dev.ss[0][8],4);
        fprintf(trace,"draw %s plane=%u vs=%016llX ps=%016llX ss=%u,%u,%u,%u,%u aniso=%u min=%u bias=%.9g canonical=%d psc=%016llX vsc=%016llX\n",frame_stem,current_plane,
            (unsigned long long)vs,(unsigned long long)ps,dev.ss[0][1],dev.ss[0][2],dev.ss[0][5],dev.ss[0][6],dev.ss[0][7],dev.ss[0][10],dev.ss[0][9],bias,sign_diag_canonical(0),
            (unsigned long long)hash_bytes(dev.psc,sizeof dev.psc),(unsigned long long)hash_bytes(dev.vsc,sizeof dev.vsc));
        int canonical=sign_diag_canonical(0);
        fprintf(trace,"effective %s plane=%u mag=%s min=%s mip=%s aniso=%u clamp_min=%u bias=%.9g\n",frame_stem,current_plane,
            canonical||dev.ss[0][5]>=2?"linear":"point",canonical||dev.ss[0][6]>=2?"linear":"point",canonical||dev.ss[0][7]>1?"linear":dev.ss[0][7]?"nearest":"none",canonical?16:(dev.ss[0][5]==3||dev.ss[0][6]==3?dev.ss[0][10]<1?1:dev.ss[0][10]>16?16:dev.ss[0][10]:1),canonical?0:dev.ss[0][9],canonical?0:bias);
        char path[1200];snprintf(path,sizeof path,"%s/sign_%s_%03u_constants.bin",diag_dir,frame_stem,current_plane);
        FILE *f=fopen(path,"wb");if(f){fwrite(dev.vsc,1,sizeof dev.vsc,f);fwrite(dev.psc,1,sizeof dev.psc,f);fwrite(dev.transform,1,sizeof dev.transform,f);fclose(f);}else reject("constants write");
    }
}

@interface SignDiagTee : NSProxy {
@public
    id target,list; id<MTLRenderPipelineState> pipeline;id<MTLDepthStencilState> depth_state;
    id<MTLTexture> depth;unsigned plane;int draws;
}
@end
@implementation SignDiagTee
- (NSMethodSignature *)methodSignatureForSelector:(SEL)s { return [target methodSignatureForSelector:s]; }
- (void)forwardInvocation:(NSInvocation *)inv {
    [inv invokeWithTarget:target];id<MTLRenderCommandEncoder> e=list;
    if (inv.selector==@selector(setRenderPipelineState:)) [e setRenderPipelineState:pipeline];
    else if (inv.selector==@selector(setDepthStencilState:)) [e setDepthStencilState:depth_state];
    else if (inv.selector==@selector(setVisibilityResultMode:offset:)) return;
    else {
        if ([NSStringFromSelector(inv.selector) hasPrefix:@"draw"]) { float index=plane;[e setFragmentBytes:&index length:4 atIndex:24];++draws; }
        [inv invokeWithTarget:e];
    }
}
@end
id<MTLRenderCommandEncoder> sign_diag_tee(id<MTLRenderCommandEncoder> encoder,uint64_t key,
    id<MTLTexture> color,id<MTLTexture> depth,id<MTLDepthStencilState> readonly_depth) {
    if (!sign_diag_active() || !current_plane) return encoder;
    if (!pipeline_desc[@(key)]) {reject("selected pipeline was not registered");return encoder;}
    if (dev.rt[1] || dev.rt[2] || dev.rt[3] || q_active) {reject("selected draw uses MRT or query");return encoder;}
    MTLRenderPipelineDescriptor *d=[pipeline_desc[@(key)] copy];
    id<MTLFunction> fn=function_for([pipeline_source[@(key)] UTF8String],key,0);if(!fn)return encoder;
    if (!uv_pipelines) uv_pipelines=[NSMutableDictionary new];
    id<MTLRenderPipelineState> pipeline=uv_pipelines[@(key)];
    if (!pipeline) {
        d.fragmentFunction=fn;d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
        d.colorAttachments[0].blendingEnabled=NO;d.colorAttachments[0].writeMask=MTLColorWriteMaskAll;d.alphaToCoverageEnabled=NO;
        NSError *err=nil;pipeline=[mtl newRenderPipelineStateWithDescriptor:d error:&err];
        if (!pipeline) {reject("UV pipeline unavailable");if(trace)fprintf(trace,"pipeline_error %s\n",err.description.UTF8String);return encoder;}
        uv_pipelines[@(key)]=pipeline;
    }
    if (!uv_target) {
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:color.width height:color.height mipmapped:NO];
        td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;td.storageMode=MTLStorageModePrivate;
        uv_target=[mtl newTextureWithDescriptor:td];td.textureType=MTLTextureType2DMultisample;td.sampleCount=color.sampleCount;
        uv_ms=color.sampleCount>1?[mtl newTextureWithDescriptor:td]:nil;
        if (!uv_target || (color.sampleCount>1 && !uv_ms)) {reject("UV target unavailable");return encoder;}
    }
    SignDiagTee *tee=[SignDiagTee alloc];tee->target=encoder;tee->list=rt_render_list_new();tee->pipeline=pipeline;
    tee->depth=depth;tee->depth_state=readonly_depth;tee->plane=current_plane;
    return (id<MTLRenderCommandEncoder>)tee;
}
static NSMutableArray *evidence_draws;
void sign_diag_replay(id<MTLRenderCommandEncoder> proxy) {
    if (!evidence_draws) evidence_draws=[NSMutableArray new];
    [evidence_draws addObject:proxy];
}
static void replay_evidence(id<MTLCommandBuffer> cb,SignDiagTee *tee) {
    if (!tee->draws) {reject("selected draw encoded no primitives");return;}
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor new];
    rp.colorAttachments[0].texture=uv_ms?:uv_target;
    rp.colorAttachments[0].resolveTexture=uv_ms?uv_target:nil;
    rp.colorAttachments[0].storeAction=uv_ms?MTLStoreActionStoreAndMultisampleResolve:MTLStoreActionStore;
    rp.colorAttachments[0].loadAction=uv_serial==frame_serial?MTLLoadActionLoad:MTLLoadActionClear;
    // NaN separates uncovered pixels from the valid (0,0) UV coordinate.
    rp.colorAttachments[0].clearColor=MTLClearColorMake(NAN,NAN,NAN,NAN);
    if (tee->depth) {
        rp.depthAttachment.texture=tee->depth;rp.depthAttachment.loadAction=MTLLoadActionLoad;rp.depthAttachment.storeAction=MTLStoreActionStore;
        if (tee->depth.pixelFormat==MTLPixelFormatDepth32Float_Stencil8) {
            rp.stencilAttachment.texture=tee->depth;rp.stencilAttachment.loadAction=MTLLoadActionLoad;rp.stencilAttachment.storeAction=MTLStoreActionStore;
        }
    }
    uv_serial=frame_serial;
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
    rt_render_list_replay(tee->list,e);[e endEncoding];
}
static id<MTLBuffer> copy_texture(id<MTLCommandBuffer> cb,id<MTLTexture> t,unsigned bpp) {
    if (!t || t.sampleCount!=1) {reject("unresolved capture source");return nil;}
    id<MTLBuffer> b=[mtl newBufferWithLength:t.width*t.height*bpp options:MTLResourceStorageModeShared];
    if (!b) {reject("readback allocation");return nil;}
    id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
    [e copyFromTexture:t sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(t.width,t.height,1)
        toBuffer:b destinationOffset:0 destinationBytesPerRow:t.width*bpp destinationBytesPerImage:t.width*t.height*bpp];[e endEncoding];return b;
}
void sign_diag_scene(id<MTLCommandBuffer> cb,id<MTLTexture> scene) {
    if (!sign_diag_active() || scene_read) return;
    // All opaque occluders have now written depth. Replay without depth writes
    // so only sign samples visible in the completed scene receive UV labels.
    for (SignDiagTee *tee in evidence_draws) replay_evidence(cb,tee);
    [evidence_draws removeAllObjects];
    scene_format=scene.pixelFormat;frame_w=scene.width;frame_h=scene.height;
    if (scene_format!=MTLPixelFormatBGRA8Unorm && scene_format!=MTLPixelFormatRGBA8Unorm && scene_format!=MTLPixelFormatRGBA16Float) {reject("unsupported scene format");return;}
    scene_read=copy_texture(cb,scene,scene_format==MTLPixelFormatRGBA16Float?8:4);
}
// Completion handlers receive the real Metal command buffer even when the
// default render thread records commands. Never query status on its recorder.
static dispatch_semaphore_t capture_done;
static MTLCommandBufferStatus capture_status;
static void capture_begin(id<MTLCommandBuffer> cb) {
    if(!capture_done)capture_done=dispatch_semaphore_create(0);
    capture_status=MTLCommandBufferStatusNotEnqueued;
    [cb addCompletedHandler:^(id<MTLCommandBuffer> real) {
        capture_status=real.status;dispatch_semaphore_signal(capture_done);
    }];
}
static int capture_wait(void) {
    dispatch_semaphore_wait(capture_done,DISPATCH_TIME_FOREVER);
    return capture_status==MTLCommandBufferStatusCompleted;
}
void sign_diag_present(id<MTLCommandBuffer> cb,id<MTLTexture> final) {
    if (!sign_diag_active()) return;
    capture_begin(cb);
    if (!scene_read || uv_serial!=frame_serial || !frame_draw || final.width!=frame_w || final.height!=frame_h) {reject("missing pre-post scene, selected draws or UV coverage");return;}
    uv_read=copy_texture(cb,uv_target,16);final_read=copy_texture(cb,final,4);
}
// Sparse native pixel samples, zlib-compressed. The analysis driver fits each
// sign plane's homography from exact shader UVs and samples the native PNGs.
typedef struct { uint32_t x,y,plane; float u,v,lod,minor,scene_lum; } Sample;
static unsigned char byte(float f) { return (unsigned char)lrintf(fminf(1,fmaxf(0,f))*255); }
static void advance_capture(void) {
    // A determines the retreat endpoint from an unclamped measured footprint;
    // every other variant replays exactly that path and endpoint.
    ++step;
    if (phase==0 && step==SD_STILL) {phase=1;step=0;}
    else if (phase==1 && step==SD_YAW) {phase=2;step=0;}
    else if (phase==2) {
        unsigned terminal=0;for(unsigned i=0;i<ntextures;++i)if(textures[i].hash==0x6EBF6F28)terminal=textures[i].old_levels?textures[i].old_levels-1:0;
        if (!variant && (retreat_lod>(float)terminal+.25f || step==SD_RETREAT_MAX)) {
            retreat_count=step;if(retreat_lod<=(float)terminal+.25f)reject("retreat never exceeded original ticker terminal mip");
        }
        if(retreat_count && step==retreat_count){phase=step=0;++variant;}
    }
    if(variant==4 || rejected){
        fprintf(trace,"%s variants=%d retreat_frames=%d planes=%u\n",rejected?"REJECTED":"CAPTURE_COMPLETE",variant,retreat_count,nplanes);fflush(trace);
        memcpy(G+camera,c0,sizeof c0);gfree(scratch);scratch=0;finished=1;
    }
}
void sign_diag_finish_present(id<MTLCommandBuffer> cb) {
    if (!sign_diag_active()) return;
    (void)cb;
    if (!capture_wait()) reject("GPU command buffer failed");
    if (uv_read && scene_read && final_read) {
        size_t pixels=frame_w*frame_h;uint8_t *scene=malloc(pixels*4);
        if (!scene) {reject("scene conversion allocation");finished=1;memcpy(G+camera,c0,sizeof c0);gfree(scratch);scratch=0;return;}
        if (scene_format==MTLPixelFormatRGBA16Float) {
            const __fp16 *p=scene_read.contents;
            for(size_t i=0;i<pixels;++i){scene[4*i]=byte(p[4*i+2]);scene[4*i+1]=byte(p[4*i+1]);scene[4*i+2]=byte(p[4*i]);scene[4*i+3]=255;}
        } else {
            memcpy(scene,scene_read.contents,pixels*4);
            if(scene_format==MTLPixelFormatRGBA8Unorm)for(size_t i=0;i<pixels;++i){uint8_t a=scene[4*i];scene[4*i]=scene[4*i+2];scene[4*i+2]=a;}
        }
        const float *uv=uv_read.contents;size_t capacity=4096;Sample *samples=malloc(capacity*sizeof *samples);unsigned n=0;
        if(!samples){free(scene);reject("sample allocation");advance_capture();return;}
        unsigned ticker=0,bank=0,control=0;
        for(uint32_t y=0;y<frame_h;++y)for(uint32_t x=0;x<frame_w;++x){
            size_t i=(size_t)y*frame_w+x;const float *p=uv+4*i;
            if(!isfinite(p[0])||!isfinite(p[1])||!isfinite(p[2])||!isfinite(p[3]))continue;
            unsigned plane=(unsigned)lrintf(p[3]/64.f);
            if(!plane||plane>nplanes)continue;
            // MSAA resolves can mix IDs/UVs on edges: only interior samples
            // whose neighbours belong to the same plane enter registration.
            if(!x||!y||x+1==frame_w||y+1==frame_h)continue;
            if(!isfinite(uv[4*(i-1)+3])||!isfinite(uv[4*(i+1)+3])||!isfinite(uv[4*(i-frame_w)+3])||!isfinite(uv[4*(i+frame_w)+3]))continue;
            if(lrintf(uv[4*(i-1)+3]/64.f)!=plane||lrintf(uv[4*(i+1)+3]/64.f)!=plane||lrintf(uv[4*(i-frame_w)+3]/64.f)!=plane||lrintf(uv[4*(i+frame_w)+3]/64.f)!=plane)continue;
            if(n==capacity){
                size_t next=capacity*2;Sample *grown=realloc(samples,next*sizeof *samples);
                if(!grown){reject("sample growth");continue;}samples=grown;capacity=next;
            }
            float luminance;
            if(scene_format==MTLPixelFormatRGBA16Float){const __fp16 *c=(const __fp16 *)scene_read.contents+4*i;luminance=.2126f*c[0]+.7152f*c[1]+.0722f*c[2];}
            else {const uint8_t *c=scene+4*i;luminance=(.2126f*c[2]+.7152f*c[1]+.0722f*c[0])/255.f;}
            samples[n++]=(Sample){x,y,plane,p[0],p[1],p[2],p[3]-plane*64.f,luminance};
            uint32_t hash=planes[plane-1].hash;if(hash==0x6EBF6F28){++ticker;if(p[2]>retreat_lod)retreat_lod=p[2];}else if(hash==0xED2BD84A)++bank;else ++control;
        }
        if(ticker<16||bank<16||control<16)reject("subjects absent or too small (ticker/bank/REF_NEONS each need 16 interior pixels)");
        char path[1200];snprintf(path,sizeof path,"%s/sign_%s_final.png",diag_dir,frame_stem);
        diag_png(path,final_read.contents,(uint32_t)frame_w,(uint32_t)frame_h,(uint32_t)frame_w*4,0);
        snprintf(path,sizeof path,"%s/sign_%s_scene.png",diag_dir,frame_stem);diag_png(path,scene,(uint32_t)frame_w,(uint32_t)frame_h,(uint32_t)frame_w*4,0);
        uLongf compressed=compressBound((uLong)n*sizeof *samples);void *packed=malloc(compressed);
        if(!packed||compress2(packed,&compressed,(const Bytef *)samples,(uLong)n*sizeof *samples,1)!=Z_OK)reject("sample compression");
        else {snprintf(path,sizeof path,"%s/sign_%s_samples.z",diag_dir,frame_stem);FILE *f=fopen(path,"wb");if(f){fwrite(packed,1,compressed,f);fclose(f);}else reject("sample write");}
        fprintf(trace,"coverage %s ticker=%u bank=%u control=%u ticker_lod_max=%.9g\n",frame_stem,ticker,bank,control,retreat_lod);
        free(scene);free(samples);free(packed);
    } else reject("incomplete capture");
    uv_read=scene_read=final_read=nil;
    advance_capture();
}
