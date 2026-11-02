// One optional near caster replay. Retail submits first and is always retained.
#include "render_internal.h"
#include "shadow_near.h"
#include "gen/x360_engine.inc"
#include <math.h>
#include <limits.h>

#define NEAR_VIEW 0x00919C70u
#define SECTION_LIST 0x009AA478u
#define FAR_COLOUR 0x00982CECu
#define FAR_DEPTH 0x00982CF4u

static int near_on = -1, near_active;
static uint64_t near_generation;
static uint32_t near_size, near_cull, near_table, near_capacity;
static id<MTLTexture> near_depth, near_colour;
static MTLPixelFormat near_depth_format, near_colour_format;
static ShadowNearBinding near_binding;
static uint32_t near_camera_mode, near_diag_mode;
static float near_far_clip[16];
static uint32_t near_far_owner;
static uint64_t near_far_frame;
static uint64_t near_draws, near_alpha_draws;
static int near_incomplete;
static Device near_caster_device;
static int near_clamp_light, near_far_rendered;
static NSArray *near_diag_views;
static int near_ab_init, near_ab_count, near_ab_index, near_ab_active, near_ab_shot_done;
static int near_ab_finished, near_ab_burst, near_ab_sample, near_ab_samples, near_ab_next;
static double near_ab_hold_at=12, near_ab_starts[8];
static int near_ab_bursts;
static uint32_t near_ab_modes[4];
static const char *near_ab_names[4];
static NSMutableArray *near_ab_receivers;
static float near_ab_post_values[2];
static int near_ab_post_seen;
static void near_ab_options(void) {
    if(near_ab_init || !diag_on)return;near_ab_init=1;
    const char *e=getenv("BOOT_DIAG_SHADOW_NEAR_AB");
    if(!e || !shadow_near_enabled())return;
    static const char *names[]={"retail","projection","filter","full"};
    static const uint32_t modes[]={1,4,5,0};
    for(const char *at=e;*at && near_ab_count<4;) {
        size_t n=strcspn(at,",");int found=-1;
        for(int i=0;i<4;++i)if(strlen(names[i])==n && !strncmp(at,names[i],n))found=i;
        if(found<0){near_ab_count=0;return;}
        near_ab_names[near_ab_count]=names[found];near_ab_modes[near_ab_count++]=modes[found];
        at+=n+(at[n]==',');
    }
    const char *h=getenv("BOOT_DIAG_SHADOW_NEAR_HOLD_AT");
    if(h){char *end;double v=strtod(h,&end);if(*h && !*end && isfinite(v) && v>=0)near_ab_hold_at=v;}
    const char *seq=getenv("BOOT_DIAG_SEQ");
    if(seq && !strncmp(seq,"sim+",4)) {
        const char *at=seq+4;
        while(*at && near_ab_bursts<8) {
            char *end;double v=strtod(at,&end);if(end==at || !isfinite(v) || v<0)break;
            near_ab_starts[near_ab_bursts++]=v;
            if(*end==':'){near_ab_samples=atoi(end+1);break;}
            if(*end!=',')break;at=end+1;if(!strncmp(at,"sim+",4))at+=4;
        }
        if(near_ab_samples<1 || near_ab_samples>64){near_ab_bursts=near_ab_samples=0;}
    }
}
int shadow_near_ab_enabled(void) {near_ab_options();return near_ab_count>0;}
int shadow_near_ab_frozen(void) {return near_ab_active && near_ab_index>0;}
static void near_ab_camera(double t) {
    if(!shadow_near_ab_enabled() || near_ab_active)return;
    if(near_ab_bursts) {
        if(near_ab_next>=near_ab_bursts || t<near_ab_starts[near_ab_next])return;
        near_ab_burst=near_ab_next++;near_ab_sample=0;
    } else if(near_ab_finished || t<near_ab_hold_at)return;
    near_ab_index=0;near_ab_active=1;near_ab_shot_done=0;near_ab_post_seen=0;
    near_ab_receivers=[NSMutableArray array];
}
static uint64_t near_diag_capture_frame;

int shadow_near_enabled(void) {
    if (near_on < 0) {
        const char *s = getenv("NFS_SHADOW_NEAR");
        near_on = s && !strcmp(s, "on");
    }
    return near_on;
}
void shadow_near_set_enabled(int on) {
    if (near_on != !!on) { shadow_near_reset(); near_on = !!on; }
}
void shadow_near_reset(void) {
    // End/store before retiring attachments. Metal command buffers retain their
    // references until completion; no resource is recycled into a later epoch.
    if (!near_depth && !near_colour && !near_cull && !near_binding.owner) { near_far_owner = 0; return; }
    end_pass(); near_active = 0;
    near_depth = near_colour = nil;
    near_depth_format = near_colour_format = MTLPixelFormatInvalid;
    near_size = 0; ++near_generation;
    memset(&near_binding, 0, sizeof near_binding);
    near_far_owner = 0;
    if (near_table) gfree(near_table);
    if (near_cull) gfree(near_cull);
    near_table = near_cull = near_capacity = 0;
}
id<MTLTexture> shadow_near_surface(uint32_t surface) {
    if (!near_active) return nil;
    if (surface == M32(FAR_DEPTH)) return near_depth;
    if (surface == M32(FAR_COLOUR)) return near_colour;
    return nil;
}
void shadow_near_begin(void) {
    if (!shadow_near_enabled()) return;
    near_far_owner=0; near_far_rendered=0; memset(&near_binding,0,sizeof near_binding);
}
void shadow_near_far_camera(uint32_t source, uint32_t view, uint32_t proj, int clamp_light) {
    if (!shadow_near_enabled()) return;
    (void)view; (void)proj;
    // The pre-caster device state is retained here. The exact rendered far
    // matrix is read at the later bias callback, before retail overwrites it.
    near_far_owner = source; near_far_frame = frame_serial;
    near_caster_device = dev; near_clamp_light = clamp_light;
}
void shadow_near_far_rendered(uint32_t source, uint32_t platform) {
    if (!shadow_near_enabled() || source!=near_far_owner || frame_serial!=near_far_frame || !platform)return;
    memcpy(near_far_clip,G+platform+0x80,sizeof near_far_clip); near_far_rendered=1;
}
static int near_allocate(id<MTLTexture> far_z, id<MTLTexture> far_c) {
    if (!mtl || !far_z || !far_c || !is_depth_pf(far_z.pixelFormat)) return 0;
    uint32_t size = 8192;
    const char *s = getenv("NFS_SHADOW_NEAR_SIZE");
    if (s) {
        char *end = NULL; unsigned long n = strtoul(s, &end, 10);
        if (!*s || *end || n < 256 || n > 16384) return 0;
        size = (uint32_t)n;
    }
    // Apple GPU families used by this renderer support 16K 2D textures.
    // Older/unknown devices conservatively use 8K. Allocation remains fallible.
    uint32_t limit = [mtl supportsFamily:MTLGPUFamilyApple3] ? 16384 : 8192;
    if (size > limit) return 0;
    if (near_depth && near_size == size && near_depth_format == far_z.pixelFormat && near_colour_format == far_c.pixelFormat) return 1;
    uint64_t estimate = (uint64_t)size * size * 16; // depth/stencil + colour + alignment allowance
    uint64_t budget = mtl.recommendedMaxWorkingSetSize;
    if (!budget || estimate > budget / 4 || mtl.currentAllocatedSize > budget - estimate) return 0;
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:far_z.pixelFormat width:size height:size mipmapped:NO];
    d.storageMode = MTLStorageModePrivate;
    d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> z = [mtl newTextureWithDescriptor:d];
    d.pixelFormat = far_c.pixelFormat;
    id<MTLTexture> c = [mtl newTextureWithDescriptor:d];
    if (!z || !c || z == far_z || c == far_c || z == c) return 0;
    uint64_t actual = z.allocatedSize + c.allocatedSize;
    if (!actual || actual > budget / 4 || mtl.currentAllocatedSize > budget) return 0;
    end_pass(); near_depth = z; near_colour = c; near_size = size;
    near_depth_format = z.pixelFormat; near_colour_format = c.pixelFormat;
    ++near_generation;
    if (diag_on) diag_event("shadow near allocation: %ux%u depth format %lu allocatedSize %llu colour format %lu allocatedSize %llu total %llu generation %llu",
        size, size, (unsigned long)z.pixelFormat, (unsigned long long)z.allocatedSize,
        (unsigned long)c.pixelFormat, (unsigned long long)c.allocatedSize, (unsigned long long)actual, (unsigned long long)near_generation);
    return 1;
}
static ShadowNearVec3 nvec(double x, double y, double z) { return (ShadowNearVec3){x,y,z}; }
static double ndot(ShadowNearVec3 a, ShadowNearVec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static ShadowNearVec3 ncross(ShadowNearVec3 a, ShadowNearVec3 b) {
    return nvec(a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x);
}
static ShadowNearVec3 nunit(ShadowNearVec3 a) {
    double n = sqrt(ndot(a,a)); return n > 1e-12 ? nvec(a.x/n,a.y/n,a.z/n) : nvec(0,0,0);
}
static void near_union(ShadowNearBounds *b, ShadowNearVec3 p) {
    b->min = nvec(fmin(b->min.x,p.x),fmin(b->min.y,p.y),fmin(b->min.z,p.z));
    b->max = nvec(fmax(b->max.x,p.x),fmax(b->max.y,p.y),fmax(b->max.z,p.z));
}
static int near_transformed_bounds(ShadowNearBounds *bounds, uint32_t lo, uint32_t hi, const float m[16]) {
    for(int i=0;i<3;++i)if(!isfinite(MF32(lo+4*i)) || !isfinite(MF32(hi+4*i)) || MF32(lo+4*i)>MF32(hi+4*i))return 0;
    for(int i=0;i<16;++i)if(!isfinite(m[i]))return 0;
    for(int corner=0;corner<8;++corner) {
        double v[3],p[3];for(int j=0;j<3;++j)v[j]=MF32((corner&(1<<j)?hi:lo)+4*j);
        for(int j=0;j<3;++j)p[j]=v[0]*m[j]+v[1]*m[4+j]+v[2]*m[8+j]+m[12+j];
        near_union(bounds,nvec(p[0],p[1],p[2]));
    }
    return 1;
}
static int near_dynamic_bounds(Cpu *c, ShadowNearBounds *bounds) {
    // RenderWorldModels' full loaded list, not the player-visible model set.
    uint32_t node=M32(0x009B0F90u), visited=0;
    while(node && node!=0x009B0F90u) {
        if(++visited>100000)return 0;
        uint32_t model=M32(node+8), hierarchy=M32(node+0x10);
        if(!model && hierarchy)model=M32(hierarchy+16*(M32(node+0x14)+1));
        uint32_t solid=model?M32(model+0xC):0;
        if(solid) {
            uint32_t space=M32(node+0x30),matrix=node+0x40;
            if(space) {
                // Match WorldModel::Render: resolve dirty parents and use the
                // world matrix, rather than SpaceNode's local transform.
                Cpu saved=*c;c->ecx=space;matrix=call_guest(c,0x00446510u,0,NULL);*c=saved;
                if(!matrix)return 0;
            }
            if(!near_transformed_bounds(bounds,solid+0x20,solid+0x30,(const float *)(G+matrix)))return 0;
        }
        node=M32(node);
    }
    uint32_t cars=M32(0x009B37CCu),n=M32(0x009B37D4u);
    if(n>100000)return 0;
    for(uint32_t i=0;i<n;++i) {
        uint32_t car=M32(cars+4*i),info=car?M32(car+0x44):0;
        if(!info || M32(car+0x28)<2)continue;
        uint32_t render=M32(M32(car)+0xC),matrix=render==0x00750E20u?car+0x330:render==0x007511E0u?car+0x170:0;
        if(!matrix)return 0; // unknown caster route: retain the complete retail map
        float m[16];const float *a=(const float *)(G+0x009B34B0u),*b=(const float *)(G+matrix);
        for(int r=0;r<4;++r)for(int j=0;j<4;++j)m[r*4+j]=a[r*4]*b[j]+a[r*4+1]*b[4+j]+a[r*4+2]*b[8+j]+a[r*4+3]*b[12+j];
        for(int j=0;j<4;++j)m[12+j]=b[12+j]; // CarRenderInfo overrides translation after CarScaleMatrix
        if(!near_transformed_bounds(bounds,info+0x90,info+0xA0,m))return 0;
    }
    // Animated NIS effects have separate bounds. Until that route has a bounds
    // adapter, an active scene falls back as a whole rather than omitting it.
    uint32_t nis=M32(0x009885C8u);
    if(nis) {Cpu saved=*c;c->ecx=nis;uint32_t scene=call_guest(c,M32(M32(nis)+0x4C),0,NULL);*c=saved;if(scene)return 0;}
    return 1;
}
static int near_input(Cpu *c, uint32_t source, ShadowNearInput *in, uint32_t *instances) {
    uint32_t plat = M32(source), tod = M32(0x009B392Cu);
    if (!plat || !tod) return 0;
    memset(in, 0, sizeof *in); *instances = 0;
    const float *v = (const float *)(G + plat), *p = (const float *)(G + plat + 0x40);
    for (int i = 0; i < 16; ++i) if (!isfinite(v[i]) || !isfinite(p[i])) return 0;
    in->camera_position = nvec(-(v[12]*v[0]+v[13]*v[1]+v[14]*v[2]),
        -(v[12]*v[4]+v[13]*v[5]+v[14]*v[6]),-(v[12]*v[8]+v[13]*v[9]+v[14]*v[10]));
    in->camera_near = .1; in->camera_far = 3;
    // Fixed envelope for all supported bumper/chase FOVs, independent of yaw.
    in->max_tan_x = 1.5; in->max_tan_y = 1;
    if (fabs(p[0]) < 1.0/1.5 || fabs(p[5]) < 1) return 0;
    double radius = in->camera_far * sqrt(1+1.5*1.5+1);
    in->receiver_half_extent = nvec(radius,radius,radius);
    ShadowNearVec3 l = nunit(nvec(MF32(tod+0x180),MF32(tod+0x184),MF32(tod+0x188)));
    if (!ndot(l,l)) return 0;
    if (near_clamp_light) {
        if (l.z < X360E_SHADOW_LIGHT_Z_MIN) l = nunit(nvec(l.x,l.y,X360E_SHADOW_LIGHT_Z_MIN));
    }
    in->light_depth = nvec(-l.x,-l.y,-l.z);
    in->light_right = nunit(ncross(nvec(0,1,0),in->light_depth));
    if (!ndot(in->light_right,in->light_right)) in->light_right = nunit(ncross(nvec(1,0,0),in->light_depth));
    in->light_up = ncross(in->light_depth,in->light_right);
    // Include every loaded root AABB, rather than the camera-visible section
    // set. The resulting depth interval extrudes the receiver footprint all
    // the way upstream through the loaded scene. XY stays the dense near fit.
    in->contributing_casters.min = in->contributing_casters.max = in->camera_position;
    for (uint32_t sec=M32(SECTION_LIST), visits=0; sec && sec!=SECTION_LIST; sec=M32(sec)) {
        if(++visits>100000)return 0;
        uint32_t root=M32(sec+0x28);
        if (!root) { if(M32(sec+0x24))return 0; continue; }
        uint32_t count = M32(sec+0x24);
        if (count > 1000000 || *instances > 1000000-count) return 0;
        *instances += count;
        ShadowNearVec3 lo = nvec(MF32(root),MF32(root+4),MF32(root+8));
        ShadowNearVec3 hi = nvec(MF32(root+12),MF32(root+16),MF32(root+20));
        if (!isfinite(lo.x+lo.y+lo.z+hi.x+hi.y+hi.z) || lo.x>hi.x || lo.y>hi.y || lo.z>hi.z) return 0;
        near_union(&in->contributing_casters,lo); near_union(&in->contributing_casters,hi);
    }
    if(!near_dynamic_bounds(c,&in->contributing_casters))return 0;
    in->depth_padding = 1; in->depth_quantum = 16;
    in->resolution = near_size; in->max_texture_dimension = 16384;
    in->max_depth_bytes = (uint64_t)near_size*near_size*4;
    in->guard_texels = 1; in->blend_texels = 8;
    uint32_t far_plat = M32(NEAR_VIEW);
    for (int i = 0; i < 16; ++i) in->world_to_far_texture.m[i] = MF32(far_plat+0x40+4*i);
    return 1;
}
static void near_planes(uint32_t plat, const float m[16]) {
    // eView::GetVisibleState reads six FLOAT planes at platform+0x140.
    // D3D depth: w-x,w+x,w-y,w+y,w-z,z. Exact rendered float matrix.
    for (int k = 0; k < 6; ++k) {
        float q[4]; double n = 0;
        for (int r = 0; r < 4; ++r) {
            q[r] = k == 5 ? m[r*4+2] : m[r*4+3] + (k&1 ? 1 : -1)*m[r*4+k/2];
            if (r < 3) n += (double)q[r]*q[r];
        }
        n = sqrt(n);
        for (int r = 0; r < 4; ++r) MF32(plat+0x140+16*k+4*r) = q[r]/n;
    }
}
static void near_fx_mask(Cpu *c, uint32_t mask) {
    uint32_t w = M32(0x0093DE78u), fx = w ? M32(w+0x48) : 0, handles = w ? M32(w+0x44) : 0;
    if (!fx || !handles) return;
    uint32_t a[3] = {fx,M32(handles+0x18C),mask};
    call_guest(c,M32(M32(fx)+0x68),3,a);
}
static int near_cull_storage(uint32_t instances) {
    uint32_t capacity = (instances+2)*12;
    if (!near_cull) near_cull = gmalloc_try(0x9D0);
    if (!near_cull) return 0;
    if (capacity > near_capacity) {
        uint32_t table = gmalloc_try(capacity);
        if (!table) return 0;
        if (near_table) gfree(near_table);
        near_table = table; near_capacity = capacity;
    }
    return near_table != 0;
}
static uint32_t near_build_cull(Cpu *c, const ShadowNearInput *input) {
    memset(G+near_cull,0,0x9D0);
    uint32_t a[3] = {NEAR_VIEW,near_cull,0x4100};
    call_guest(c,0x004FAE40u,3,a);
    M32(near_cull+0x88) = M32(near_cull+0x8C) = near_table;
    M32(near_cull+0x90) = near_table+near_capacity-12;
    uint32_t camera = M32(NEAR_VIEW+0x40);
    memcpy(G+near_cull+0xA0,G+camera+0x40,12);
    memcpy(G+near_cull+0xB0,G+camera+0x50,12);
    // The retail LOD evaluator assumes a perspective eye. Force its finest
    // available caster LOD throughout the bounded light extrusion; using the
    // distant light eye's perspective size would discard nearby thin casters.
    double extent=0;
    for(int j=0;j<3;++j) {
        double eye=MF32(camera+0x40+4*j);
        double lo=j==0?input->contributing_casters.min.x:j==1?input->contributing_casters.min.y:input->contributing_casters.min.z;
        double hi=j==0?input->contributing_casters.max.x:j==1?input->contributing_casters.max.y:input->contributing_casters.max.z;
        double d=fmax(fabs(lo-eye),fabs(hi-eye));extent+=d*d;
    }
    double lod=input->resolution*(sqrt(extent)+1)/fmin(input->receiver_half_extent.x,input->receiver_half_extent.y);
    if(!isfinite(lod) || lod>INT_MAX/2)return UINT32_MAX;
    MF32(near_cull+0xC0)=(float)lod; MF32(NEAR_VIEW+0xC)=(float)lod;
    M32(near_cull+0xC4) = UINT32_MAX; // no camera preculler on the light extrusion
    M32(near_cull+0x9C0) = 1;
    for (uint32_t sec=M32(SECTION_LIST), visits=0; sec && sec!=SECTION_LIST; sec=M32(sec)) {
        if(++visits>100000)return UINT32_MAX;
        if (!sec || !M32(sec+0x28)) continue;
        c->ecx = sec; call_guest(c,0x00729640u,1,&near_cull);
        if(M32(near_cull+0x8C)>=M32(near_cull+0x90))return UINT32_MAX;
    }
    uint32_t end = M32(near_cull+0x8C);
    if (end < near_table || end >= near_table+near_capacity || (end-near_table)%12) return UINT32_MAX;
    return (end-near_table)/12;
}
int shadow_near_binding(uint32_t owner, uint64_t frame, ShadowNearBinding *out) {
    if (!out) return 0;
    memset(out,0,sizeof *out);
    if (!shadow_near_enabled() || near_active || !near_depth || near_binding.owner != owner ||
        near_binding.frame != frame || near_binding.generation != near_generation) return 0;
    *out = near_binding; return 1;
}
uint64_t shadow_near_draw_begin(void) { return near_draws; }
void shadow_near_draw_end(uint64_t before, uint32_t primitives) {
    if (near_active && primitives && near_draws == before) near_incomplete = 1;
}
void shadow_near_draw_note(int shader_kill) {
    if (!near_active) return;
    ++near_draws; near_alpha_draws += dev.rs[15] != 0 || shader_kill;
    if (!diag_on || !diag_dir || (!near_ab_active && (!near_diag_views.count || frame_serial!=near_diag_capture_frame))) return;
    static FILE *log;
    if(!log){char path[1024];snprintf(path,sizeof path,"%s/shadow_near_draws.tsv",diag_dir);log=fopen(path,"w");
        if(log)fprintf(log,"frame\towner\tdraw\tvs\tps\tstream\toffset\tindices\tdiffuse\talpha_func\talpha_ref\ttexkill\tcull\tbias\tslope\n");}
    if(!log)return;
    Obj *vs=obj(dev.vs),*ps=obj(dev.ps);
    fprintf(log,"%llu\t%08x\t%llu\t%016llx\t%016llx\t%08x\t%u\t%08x\t%08x\t%u\t%u\t%d\t%u\t%08x\t%08x\n",
        (unsigned long long)frame_serial,near_far_owner,(unsigned long long)near_draws,
        (unsigned long long)(vs?vs->tokens_hash:0),(unsigned long long)(ps?ps->tokens_hash:0),
        dev.stream[0],dev.stream_off[0],dev.indices,dev.tex[0],dev.rs[15]?dev.rs[25]:0,dev.rs[24]&255,shader_kill,dev.rs[22],dev.rs[195],dev.rs[175]);fflush(log);
}
static void near_diag(void);
int x360_shadow_map_size(void);
void shadow_near_replay(Cpu *c, uint32_t source, uint32_t cull) {
    (void)cull;
    if (!shadow_near_enabled() || near_active || q_active) return;
    memset(&near_binding,0,sizeof near_binding);
    if (source != 0x00919650u && source != 0x009196C0u) return;
    if (!M32(0x00901830u) || !near_far_rendered || near_far_owner != source || near_far_frame != frame_serial) return;
    uint32_t plat = M32(NEAR_VIEW), camera = M32(NEAR_VIEW+0x40);
    if (!plat || !camera || !M32(FAR_DEPTH) || !M32(FAR_COLOUR)) return;
    NSUInteger level, slice;
    id<MTLTexture> far_z = surface_target(M32(FAR_DEPTH),&level,&slice);
    id<MTLTexture> far_c = surface_target(M32(FAR_COLOUR),&level,&slice);
    ShadowNearInput input; ShadowNearResult result; uint32_t instances;
    if (!near_allocate(far_z,far_c) || !near_input(c,source,&input,&instances) ||
        !shadow_near_fit(&input,&result) || !near_cull_storage(instances)) return;
    // Promote the exact caster floats, then derive texture/remap from those.
    // This makes the binding agree with the renderer rather than the ideal fit.
    float clip[16];
    for (int i = 0; i < 16; ++i) clip[i] = (float)result.world_to_clip.m[i];
    float bias = .5f+.5f/near_size;
    for (int r = 0; r < 4; ++r) for (int j = 0; j < 4; ++j) {
        result.world_to_clip.m[r*4+j] = clip[r*4+j];
        result.world_to_texture.m[r*4+j] = j == 0 ? (float)(clip[r*4]*.5f+clip[r*4+3]*bias) :
            j == 1 ? (float)(-clip[r*4+1]*.5f+clip[r*4+3]*bias) : clip[r*4+j];
    }
    // Refit's inverse-far product is corrected below with an exact inverse.
    ShadowNearMatrix ideal = result.world_to_texture;
    // inverse(far) = far_to_near * inverse(ideal near). The orthographic near
    // inverse can be avoided: solve far * X = exact texture by pivoted elimination.
    double aug[4][8];
    for (int r=0;r<4;++r) for (int j=0;j<8;++j) aug[r][j] = j<4 ? input.world_to_far_texture.m[r*4+j] : ideal.m[r*4+j-4];
    for (int k=0;k<4;++k) {
        int pivot=k; for(int r=k+1;r<4;++r) if(fabs(aug[r][k])>fabs(aug[pivot][k])) pivot=r;
        if(fabs(aug[pivot][k])<1e-18)return;
        for(int j=0;j<8;++j){double t=aug[k][j];aug[k][j]=aug[pivot][j];aug[pivot][j]=t;}
        double div=aug[k][k];for(int j=0;j<8;++j)aug[k][j]/=div;
        for(int r=0;r<4;++r)if(r!=k){double t=aug[r][k];for(int j=0;j<8;++j)aug[r][j]-=t*aug[k][j];}
    }
    for(int r=0;r<4;++r)for(int j=0;j<4;++j)result.far_to_near.m[r*4+j]=(float)aug[r][j+4];
    Cpu saved_cpu = *c; Device saved_device = dev;
    uint8_t saved_plat[0x1A0], saved_camera[0x60];
    memcpy(saved_plat,G+plat,sizeof saved_plat); memcpy(saved_camera,G+camera+0x40,sizeof saved_camera);
    uint8_t saved_render_globals[0x60]; memcpy(saved_render_globals,G+0x982C80,sizeof saved_render_globals);
    uint32_t saved_deferred[3]; memcpy(saved_deferred,G+0x93DE28,sizeof saved_deferred);
    // eFrameMalloc/Matrix report exhaustion at 00405EFA/00405F04.
    uint32_t saved_frame_fail[2]; memcpy(saved_frame_fail,G+0x915F94,sizeof saved_frame_fail);
    uint32_t saved_globals[9];
    const uint32_t addresses[] = {0x982B38,0x8FAE6C,0x8FAE70,0x982CFC,0x982D00,0x903328,0x901940,0x982A20,0x919C7C};
    for(unsigned i=0;i<9;++i)saved_globals[i]=M32(addresses[i]);
    render_shadow_near_suspend();
    near_active = 1; near_incomplete = 0; near_draws = near_alpha_draws = 0; dev = near_caster_device;
    dev.rt[0] = M32(FAR_COLOUR); dev.rt[1]=dev.rt[2]=dev.rt[3]=0; dev.ds=M32(FAR_DEPTH);
    dev.viewport[0]=dev.viewport[1]=0; dev.viewport[2]=dev.viewport[3]=near_size;
    dev.viewport[4]=0; dev.viewport[5]=0x3F800000u; dev.rs[174]=0;
    // Preserve original constant/slope bias, cull and alpha state. Only the
    // viewport/scissor and independent targets differ from the caster stage.
    memset(G+plat,0,64); for(int i=0;i<4;++i)MF32(plat+20*i)=1;
    memcpy(G+plat+0x40,clip,64); memcpy(G+plat+0x80,clip,64); near_planes(plat,clip);
    ShadowNearVec3 eye = result.snapped_origin, d = input.light_depth;
    MF32(camera+0x40)=eye.x+d.x*result.depth_min; MF32(camera+0x44)=eye.y+d.y*result.depth_min; MF32(camera+0x48)=eye.z+d.z*result.depth_min;
    MF32(camera+0x50)=d.x; MF32(camera+0x54)=d.y; MF32(camera+0x58)=d.z;
    // The view ID is unchanged but its projection differs. Force the guest's
    // identity/effect/view cache to upload near matrices even for merged draws.
    M32(0x8FAE9C)=UINT32_MAX;
    uint32_t caster_count = near_build_cull(c,&input);
    if (caster_count != UINT32_MAX) {
        render_clear(0,0,7,0xFFFFFFFFu,1,0);
        // Retail 006E50C8..006E53xx stages, with their original material and
        // effect routes. Scenery mask 0x200 is the hardware level-2 caster set.
        M32(0x982B38)=1; M32(0x8FAE6C)=1; M32(0x982CFC)=0;
        near_fx_mask(c,M32(0x982C08)?0:15);
        M32(0x982CB4)=M32(0x982CB8)=M32(0x982CBC)=M32(0x982CC0)=M32(0x982CC4)=0;
        uint32_t world[2]={NEAR_VIEW,0};
        call_guest(c,0x007538D0u,2,world); call_guest(c,0x006E2F50u,0,NULL);
        uint32_t scenery[2]={NEAR_VIEW, M32(0x8FAECC)==0 ? (M32(0x982B9C)|0x100) : M32(0x901830)==1 ? 0x2000 : 0x200};
        c->ecx=near_cull; call_guest(c,0x00723FA0u,2,scenery); call_guest(c,0x006E2F50u,0,NULL);
        M32(0x903328)=1; // DrawCarShadow
        if(M32(0x982C08))near_fx_mask(c,1);
        if (M32(0x903320)) {
            uint32_t active = call_guest(c,0x006D79D0u,1,&source);
            if (active) { c->ecx=active; call_guest(c,M32(M32(active)+0xC),2,world); call_guest(c,0x006E2F50u,0,NULL); }
            uint32_t list=M32(0x9B37CC), count=M32(0x9B37D4);
            for(uint32_t i=0;i<count;++i) {
                uint32_t car=M32(list+4*i);
                if(!car || car==active || M32(car+0x28)<=1)continue;
                c->ecx=car; call_guest(c,M32(M32(car)+0xC),2,world);
            }
            call_guest(c,0x006E2F50u,0,NULL);
        }
        call_guest(c,0x006E0200u,0,NULL); M32(0x8FAE70)=UINT32_MAX;
        if(M32(0x8FAECC) && M32(0x982D10)) {
            c->ecx=M32(0x982D10); call_guest(c,0x006D7F70u,1,world);
        }
        near_fx_mask(c,7);
    }
    end_pass(); near_active=0;
    if (memcmp(saved_frame_fail,G+0x915F94,sizeof saved_frame_fail)) near_incomplete=1;
    memcpy(G+0x915F94,saved_frame_fail,sizeof saved_frame_fail);
    memcpy(G+plat,saved_plat,sizeof saved_plat); memcpy(G+camera+0x40,saved_camera,sizeof saved_camera);
    memcpy(G+0x982C80,saved_render_globals,sizeof saved_render_globals);
    memcpy(G+0x93DE28,saved_deferred,sizeof saved_deferred);
    for(unsigned i=0;i<9;++i)M32(addresses[i])=saved_globals[i];
    // Restoring the far view changes its matrices again; the next retail draw
    // must upload them instead of reusing constants left by the near replay.
    M32(0x8FAE9C)=UINT32_MAX;
    dev=saved_device; render_shadow_near_restore(); *c=saved_cpu;
    if (caster_count == UINT32_MAX || near_incomplete) return;
    ++near_generation;
    near_binding=(ShadowNearBinding){.frame=frame_serial,.generation=near_generation,
        .depth_allocated_bytes=near_depth.allocatedSize,.colour_allocated_bytes=near_colour.allocatedSize,
        .owner=source,.width=near_size,.height=near_size,.far_depth=M32(FAR_DEPTH),.far_colour=M32(FAR_COLOUR),.far_width=(uint32_t)far_z.width,.far_height=(uint32_t)far_z.height,
        .world_to_far_texture=input.world_to_far_texture,
        .fit=result,.caster_count=caster_count,.draws=(uint32_t)near_draws,.alpha_draws=(uint32_t)near_alpha_draws};
    for(int i=0;i<16;++i)near_binding.world_to_far_clip.m[i]=near_far_clip[i];
    near_diag();
}

// Eligibility and the complete filter slice live beside the translator.
char *shadow_near_shader(const char *source,const SmResult *pc,const uint32_t *tokens,uint32_t n) {
    return sm_shadow_near_patch(source,tokens,n,pc);
}
id<MTLTexture> shadow_near_receiver(uint32_t source, ShadowNearUniforms *u) {
    ShadowNearBinding binding;
    memset(u,0,sizeof *u);
    if (!shadow_near_binding(source,frame_serial,&binding)) return nil;
    for(int r=0;r<4;++r)for(int j=0;j<4;++j)u->rows[r][j]=(float)binding.fit.far_to_near.m[r*4+j];
    ShadowNearRect a=binding.fit.usable,b=binding.fit.blend;
    u->usable[0]=a.min_u;u->usable[1]=a.min_v;u->usable[2]=a.max_u;u->usable[3]=a.max_v;
    u->blend[0]=b.min_u;u->blend[1]=b.min_v;u->blend[2]=b.max_u;u->blend[3]=b.max_v;
    u->mode=near_ab_active?near_ab_modes[near_ab_index]:shadow_near_ab_enabled()?1:near_diag_mode;
    return near_depth;
}

// Capture/replay is explicitly diagnostic. Existing JSON means replay; a
// missing worktree JSON is captured after the held camera reaches sim 12.
// The rendering pose is frozen; no physics clocks or simulation state rewind.
#include <mach-o/dyld.h>
static NSString *pose_path;
static NSDictionary *pose_replay;
static NSMutableDictionary *pose_pending;
static NSMutableArray *pose_receivers;
static uint64_t pose_frame;
static int pose_init, pose_failed, pose_written;
static double pose_epoch = -1;
static int near_diag_last = -1;
static NSArray *pose_words(uint32_t address, unsigned count) {
    NSMutableArray *a=[NSMutableArray arrayWithCapacity:count];
    for(unsigned i=0;i<count;++i)[a addObject:@(M32(address+4*i))];
    return a;
}
static int pose_words_valid(id a, unsigned count) {
    if(![a isKindOfClass:[NSArray class]] || [a count]!=count)return 0;
    for(id n in a)if(![n isKindOfClass:[NSNumber class]] || !isfinite([n doubleValue]) || [n doubleValue]<0 ||
        [n doubleValue]>UINT32_MAX || [n doubleValue]!=(double)[n unsignedLongLongValue])return 0;
    return 1;
}
static int pose_copy(NSDictionary *d, NSString *key, uint32_t address, unsigned count) {
    NSArray *a=d[key]; if(!pose_words_valid(a,count))return 0;
    for(unsigned i=0;i<count;++i)M32(address+4*i)=[a[i] unsignedIntValue];
    return 1;
}
static int near_write_json(NSString *path,id object) {
    NSError *error=nil;
    NSData *data=[NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingPrettyPrinted|NSJSONWritingSortedKeys error:&error];
    if(!data){diag_event("shadow near JSON serialization: %s",error.localizedDescription.UTF8String);return 0;}
    FILE *f=fopen(path.fileSystemRepresentation,"wb");
    if(!f){diag_event("shadow near JSON open: errno %d path %s",errno,path.fileSystemRepresentation);return 0;}
    int ok=fwrite(data.bytes,1,data.length,f)==data.length;
    if(fclose(f))ok=0;
    return ok;
}
static int pose_valid(NSDictionary *d) {
    if(![d[@"schema"] isEqual:@1] || ![d[@"map_configuration"] isKindOfClass:[NSDictionary class]] ||
        !pose_words_valid(d[@"resolution"],2))return 0;
    for(NSString *key in @[@"car_type",@"camera_mode",@"fov_word"])if(!pose_words_valid(@[d[key]?:[NSNull null]],1))return 0;
    if([d[@"fov_word"] unsignedIntValue]>65535)return 0;
    for(NSString *key in @[@"camera_words",@"car_transform_words",@"view_words",@"projection_words"])if(!pose_words_valid(d[key],16))return 0;
    if(!pose_words_valid(d[@"car_wheel_words"],128) || !pose_words_valid(d[@"light_words"],3) ||
        !pose_words_valid(d[@"environment_words"],4) || !pose_words_valid(d[@"shadow_strength_words"],2) || !pose_words_valid(d[@"rain_words"],2) || !pose_words_valid(d[@"wetness_words"],1))return 0;
    return [d[@"visible_caster_ids"] isKindOfClass:[NSArray class]] && [d[@"visible_receiver_ids"] isKindOfClass:[NSArray class]];
}
static void pose_options(void) {
    if(pose_init || !diag_on)return;pose_init=1;
    const char *v=getenv("BOOT_DIAG_SHADOW_NEAR_VIEWS");
    if(v){
        NSArray *list=[[NSString stringWithUTF8String:v] componentsSeparatedByString:@","];
        int valid=list.count>0;
        for(NSString *x in list)valid &= [@[@"far",@"near",@"coverage"] containsObject:x];
        if(valid)near_diag_views=list;
    }
    const char *p=getenv("BOOT_DIAG_SHADOW_POSE"); if(!p || !*p)return;
    uint32_t capacity=PATH_MAX;char executable[PATH_MAX],resolved[PATH_MAX];
    if(_NSGetExecutablePath(executable,&capacity) || !realpath(executable,resolved)) { pose_failed=1;return; }
    NSString *root=[[[NSString stringWithUTF8String:resolved] stringByDeletingLastPathComponent] stringByDeletingLastPathComponent];
    const char *sandbox=getenv("CODEX_SANDBOX");
    if(sandbox && *sandbox && sandbox[0]=='/')root=[[NSString stringWithUTF8String:sandbox] stringByResolvingSymlinksInPath];
    int scratch_capture=!strcmp(p,"capture");
    NSString *path=scratch_capture ? [[NSString stringWithUTF8String:diag_dir] stringByAppendingPathComponent:@"shadow_pose.json"] : [[NSString stringWithUTF8String:p] stringByStandardizingPath];
    char canonical[PATH_MAX];
    if(!realpath(path.stringByDeletingLastPathComponent.fileSystemRepresentation,canonical)){pose_failed=1;return;}
    path=[[NSString stringWithUTF8String:canonical] stringByAppendingPathComponent:path.lastPathComponent];
    if(realpath(path.fileSystemRepresentation,canonical))path=[NSString stringWithUTF8String:canonical];
    if(![path isAbsolutePath] || (!scratch_capture && ![path hasPrefix:[root stringByAppendingString:@"/"]]) || ![[path pathExtension] isEqualToString:@"json"]) {
        pose_failed=1;diag_event("shadow pose: rejected path outside the worktree or without .json");return;
    }
    pose_path=path;
    if([[NSFileManager defaultManager] fileExistsAtPath:path]) {
        NSData *data=[NSData dataWithContentsOfFile:path];
        id d=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:nil]:nil;
        if(![d isKindOfClass:[NSDictionary class]] || !pose_valid(d)) { pose_failed=1;return; }
        pose_replay=d;
    }
    pose_receivers=[NSMutableArray array];
}
static void near_diag_camera_time(void) {
    if(pose_epoch<0)pose_epoch=MF32(0x009885D8u);
    double t=MF32(0x009885D8u)-pose_epoch;
    if(shadow_near_ab_enabled() || !near_diag_views.count || t<12)return;
    int index=(int)((t-12)*4);
    if(index>=(int)near_diag_views.count){near_diag_mode=0;return;}
    NSString *view=near_diag_views[index];
    near_diag_mode=[view isEqual:@"far"]?1:[view isEqual:@"coverage"]?3:0;
    if(index==near_diag_last)return;
    near_diag_last=index;
    uint64_t diag_current_frame(void);
    uint64_t capture=diag_current_frame()+1; near_diag_capture_frame=capture;
    diag_shot_request(capture);
    diag_event("shadow near screen view: %s frame %llu race_sim %.6f enabled %d pose %s",
        view.UTF8String,(unsigned long long)capture,t,shadow_near_enabled(),pose_failed?"INVALID":pose_replay?"replay":"capture");
}
void shadow_near_pose_camera(Cpu *c, uint32_t camera, uint32_t view, uint32_t mode) {
    if(!diag_on || view!=1 || M32(0x00925E90u)!=6)return;
    pose_options();
    if(!pose_path && !near_diag_views.count && !shadow_near_ab_enabled())return;
    near_camera_mode=mode;
    near_diag_camera_time();
    near_ab_camera(MF32(0x009885D8u)-pose_epoch);
    if(!pose_replay || pose_failed || MF32(0x009885D8u)-pose_epoch<10)return;
    NSArray *words=pose_replay[@"camera_words"];
    if(![words isKindOfClass:[NSArray class]] || words.count!=16 || [pose_replay[@"camera_mode"] unsignedIntValue]!=mode) {
        pose_failed=1;diag_event("shadow pose: camera mode or matrix mismatch; comparison invalid");return;
    }
    Cpu saved=*c;uint32_t scratch=c->esp-1024;
    if(!pose_copy(pose_replay,@"camera_words",scratch,16)){pose_failed=1;return;}
    c->esp=scratch-12;M32(c->esp)=0;M32(c->esp+4)=scratch;M32(c->esp+8)=0;c->ecx=camera;
    void f_004700D0(Cpu *c);f_004700D0(c);*c=saved;
    M16(camera+0xC4)=[pose_replay[@"fov_word"] unsignedIntValue];
}
static NSArray *pose_casters(uint32_t cull) {
    NSMutableArray *ids=[NSMutableArray array];
    uint32_t n=cull?M32(cull+0x9C0):0;if(n>12)return ids;
    for(uint32_t i=0;i<n;++i) {
        uint32_t entry=cull+i*0xD0;
        if(M32(entry+0x80)!=NEAR_VIEW)continue;
        uint32_t start=M32(entry+0x88),end=M32(entry+0x8C);
        if(end<start || (end-start)%12 || end-start>12000000)continue;
        for(uint32_t at=start;at<end;at+=12) {
            uint32_t scene=M32(at+8),model=M32(at)&~3u;
            int section=-1,index=-1;
            for(uint32_t sec=M32(SECTION_LIST),visits=0;sec && sec!=SECTION_LIST && visits++<100000;sec=M32(sec)) {
                uint32_t instances=M32(sec+0x20),count=M32(sec+0x24);
                if(scene>=instances && (uint64_t)scene<(uint64_t)instances+(uint64_t)count*0x40 && (scene-instances)%0x40==0) {
                    section=(int)M32(sec+0xC);index=(int)((scene-instances)/0x40);break;
                }
            }
            [ids addObject:@{@"model_flags":@(M32(at)),@"model_hash":@(model?M32(model+8):0),
                @"instance":@(scene),@"section":@(section),@"instance_index":@(index),
                @"bounds_words":scene?pose_words(scene,6):@[],@"flags":@(scene?M32(scene+0x18):0)}];
        }
    }
    return ids;
}
void shadow_near_pose_render(Cpu *c, uint32_t source, uint32_t cull) {
    if(!diag_on || source!=0x00919650u)return;
    pose_options();if(!pose_path || pose_failed)return;
    if(pose_pending && pose_frame!=frame_serial) {
        pose_pending[@"visible_receiver_ids"]=[pose_receivers copy];
        if(near_write_json(pose_path,pose_pending)) {pose_written=1;pose_replay=[pose_pending copy];}
        else {pose_failed=1;diag_event("shadow pose: capture write failed");}
        pose_pending=nil;
    }
    if(pose_epoch<0 || MF32(0x009885D8u)-pose_epoch<near_ab_hold_at)return;
    Cpu saved=*c;uint32_t car=call_guest(c,0x006D79D0u,1,&source);*c=saved;
    if(!car || M32(M32(car)+0xC)!=0x00750E20u) {pose_failed=1;diag_event("shadow pose: active car render route is not supported");return;}
    uint32_t camera=M32(source+0x40),plat=M32(source),tod=M32(0x009B392Cu),rain=M32(source+0x68);
    if(!camera || !plat || !tod)return;
    NSArray *resolution=@[@(M32(0x982BE4)),@(M32(0x982BE8))];
    NSDictionary *maps=@{@"far_size":@(x360_shadow_map_size()),@"near_size_request":@(getenv("NFS_SHADOW_NEAR_SIZE")?atoi(getenv("NFS_SHADOW_NEAR_SIZE")):8192),
        @"fit":@(getenv("NFS_SHADOW_FIT")?getenv("NFS_SHADOW_FIT"):"x360"),@"stable":@(getenv("NFS_SHADOW_STABLE")?getenv("NFS_SHADOW_STABLE"):"off")};
    if(pose_replay) {
        if(![pose_replay[@"resolution"] isEqual:resolution] || ![pose_replay[@"map_configuration"] isEqual:maps] ||
            [pose_replay[@"car_type"] unsignedIntValue]!=M32(car+0x2C)) {pose_failed=1;diag_event("shadow pose: car/resolution/map configuration differs; comparison invalid");return;}
        if(!pose_copy(pose_replay,@"car_transform_words",car+0x330,16) ||
            !pose_copy(pose_replay,@"car_wheel_words",car+0x130,128) ||
            !pose_copy(pose_replay,@"view_words",plat,16) || !pose_copy(pose_replay,@"projection_words",plat+0x40,16) ||
            !pose_copy(pose_replay,@"light_words",tod+0x180,3) || !pose_copy(pose_replay,@"environment_words",tod,4) ||
            !pose_copy(pose_replay,@"shadow_strength_words",0x008FAE64u,2)) {pose_failed=1;return;}
        if(rain && !pose_copy(pose_replay,@"rain_words",rain+0x28C,2))pose_failed=1;
        if(rain && !pose_copy(pose_replay,@"wetness_words",rain+0x36A0,1))pose_failed=1;
        // Retain the exact projected view used for rasterization/culling.
        for(int r=0;r<4;++r)for(int j=0;j<4;++j){float v=0;for(int k=0;k<4;++k)v+=MF32(plat+4*(r*4+k))*MF32(plat+0x40+4*(k*4+j));MF32(plat+0x80+4*(r*4+j))=v;}
        return;
    }
    if(pose_written || pose_pending)return;
    pose_frame=frame_serial;[pose_receivers removeAllObjects];
    pose_pending=[@{@"schema":@1,@"frame":@(frame_serial),@"sim_tick":@(M32(0x009885B8u)),@"sim_time":@(MF32(0x009885D8u)),
        @"race_sim_time":@(MF32(0x009885D8u)-pose_epoch),@"car_type":@(M32(car+0x2C)),@"car_transform_words":pose_words(car+0x330,16),
        @"car_wheel_words":pose_words(car+0x130,128),@"camera_mode":@(near_camera_mode),@"camera_words":pose_words(camera,16),
        @"fov_word":@(M16(camera+0xC4)),@"fov_degrees":@(M16(camera+0xC4)*360.0/65536),
        @"view_words":pose_words(plat,16),@"projection_words":pose_words(plat+0x40,16),@"light_words":pose_words(tod+0x180,3),
        @"environment_words":pose_words(tod,4),@"shadow_strength_words":pose_words(0x008FAE64u,2),@"rain_words":rain?pose_words(rain+0x28C,2):@[],@"wetness_words":rain?pose_words(rain+0x36A0,1):@[],
        @"resolution":resolution,@"map_configuration":maps,@"visible_caster_ids":pose_casters(cull),
        @"freeze":@"render pose only; simulation identity recorded, not rewound"} mutableCopy];
}
void shadow_near_receiver_note(void) {
    if(!diag_on)return;
    if(near_ab_active) {
        [near_ab_receivers addObject:@{@"stream":@(dev.stream[0]),@"offset":@(dev.stream_off[0]),@"indices":@(dev.indices),
            @"vs":@(dev.vs),@"ps":@(dev.ps),@"effect":@(M32(0x00982C80u)),@"model":@(M32(0x00982D10u))}];
    }
    if(!pose_pending || pose_frame!=frame_serial)return;
    uint32_t view=M32(0x00982A20u);if(!view || M32(view+4)!=1)return;
    Obj *vs=obj(dev.vs),*ps=obj(dev.ps);
    [pose_receivers addObject:@{@"stream":@(dev.stream[0]),@"offset":@(dev.stream_off[0]),@"indices":@(dev.indices),
        @"vs":@(vs?vs->tokens_hash:0),@"ps":@(ps?ps->tokens_hash:0)}];
}
static void near_diag(void) {
    if(!diag_on || (!near_diag_views.count && !shadow_near_ab_enabled()))return;
    static FILE *log;
    if(!log && diag_dir){char path[1024];snprintf(path,sizeof path,"%s/shadow_near.tsv",diag_dir);log=fopen(path,"w");
        if(log)fprintf(log,"frame\tsim\towner\tgeneration\twidth\theight\tdepth_bytes\tcolour_bytes\tcasters\tdraws\talpha_draws\tfar_clip_hex\tfar_texture_hex\tclip_hex\ttexture_hex\tremap_hex\n");}
    if(!log)return;
    fprintf(log,"%llu\t%.6f\t%08x\t%llu\t%u\t%u\t%llu\t%llu\t%u\t%u\t%u\t",
        (unsigned long long)near_binding.frame,MF32(0x9885D8),near_binding.owner,(unsigned long long)near_binding.generation,near_binding.width,near_binding.height,
        (unsigned long long)near_binding.depth_allocated_bytes,(unsigned long long)near_binding.colour_allocated_bytes,
        near_binding.caster_count,near_binding.draws,near_binding.alpha_draws);
    const ShadowNearMatrix *m[5]={&near_binding.world_to_far_clip,&near_binding.world_to_far_texture,&near_binding.fit.world_to_clip,&near_binding.fit.world_to_texture,&near_binding.fit.far_to_near};
    for(int k=0;k<5;++k){for(int i=0;i<16;++i){float f=m[k]->m[i];uint32_t bits;memcpy(&bits,&f,4);fprintf(log,"%08x",bits);}fputc(k==4?'\n':'\t',log);}fflush(log);
    NSArray *casters=nil;
    if((pose_pending && pose_frame==frame_serial) || frame_serial==near_diag_capture_frame)casters=pose_casters(near_cull);
    if(pose_pending && pose_frame==frame_serial)pose_pending[@"near_caster_ids"]=casters;
    if(frame_serial==near_diag_capture_frame && diag_dir) {
        char path[1024];snprintf(path,sizeof path,"%s/shadow_near_casters_%llu.json",diag_dir,(unsigned long long)frame_serial);
        NSDictionary *record=@{@"frame":@(frame_serial),@"owner":@(near_binding.owner),@"generation":@(near_binding.generation),@"casters":casters?:@[]};
        near_write_json([NSString stringWithUTF8String:path],record);
    }
}

// The pacing hook consumes this only after Present saved the evaluated pass.
// Every variant is rendered anew; clocks/cameras advance only between samples.
int shadow_near_ab_hold(void) {
    if(!near_ab_active || !near_ab_shot_done)return 0;
    near_ab_shot_done=0;
    if(++near_ab_index<near_ab_count) { [near_ab_receivers removeAllObjects];return 1; }
    near_ab_index=0;
    if(near_ab_bursts && ++near_ab_sample<near_ab_samples) {
        [near_ab_receivers removeAllObjects];near_ab_post_seen=0;return 0;
    }
    near_ab_active=0;near_ab_finished=1;return 0;
}
void shadow_near_ab_post(float *adaptation,float *exposure) {
    if(!near_ab_active)return;
    if(!near_ab_post_seen){near_ab_post_values[0]=*adaptation;near_ab_post_values[1]=*exposure;near_ab_post_seen=1;}
    else {*adaptation=near_ab_post_values[0];*exposure=near_ab_post_values[1];}
}
int shadow_near_ab_shot(char *path, unsigned long n) {
    if(!near_ab_active || near_ab_shot_done)return 0;
    const char *variant=near_ab_names[near_ab_index];
    snprintf(path,n,"%s/near_%02d_%03d_%s.bmp",diag_dir,near_ab_burst,near_ab_sample,variant);
    near_diag_capture_frame=frame_serial;
    diag_event("shadow near AB: variant %s burst %d sample %d frame %llu sim_word %08x render_counter %u real_dt %.9g near %llu draws %u alpha %u",variant,near_ab_burst,near_ab_sample,
        (unsigned long long)frame_serial,M32(0x009885D8u),M32(0x009885B8u),MF32(0x009259BCu),
        (unsigned long long)near_binding.generation,near_binding.draws,near_binding.alpha_draws);
    char ids[1024];snprintf(ids,sizeof ids,"%s/near_%02d_%03d_%s_ids.json",diag_dir,near_ab_burst,near_ab_sample,variant);
    NSDictionary *record=@{@"variant":[NSString stringWithUTF8String:variant],@"burst":@(near_ab_burst),@"sample":@(near_ab_sample),
        @"frame":@(frame_serial),@"sim_word":@(M32(0x009885D8u)),@"render_counter":@(M32(0x009885B8u)),@"time_frames":@(M32(0x008F82F4u)),
        @"view_words":pose_words(M32(0x00919650u),16),@"projection_words":pose_words(M32(0x00919650u)+0x40,16),
        @"camera_words":pose_words(M32(0x00919690u),16),@"camera_mode":@(near_camera_mode),
        @"light_words":M32(0x009B392Cu)?pose_words(M32(0x009B392Cu)+0x180,3):@[],
        @"post_values":@[@(near_ab_post_values[0]),@(near_ab_post_values[1])],
        @"receivers":near_ab_receivers?:@[],@"casters":near_cull?pose_casters(near_cull):@[]};
    near_write_json([NSString stringWithUTF8String:ids],record);
    near_ab_shot_done=1;return 1;
}

// Uncompressed top-down BGRA: bounded held bursts must not spend their launch
// budget serially compressing 4K PNGs. The existing capture queue owns the buffer.
int shadow_near_ab_write_bmp(const char *path,const void *bgra,uint32_t width,uint32_t height) {
    if(!path || !bgra || !width || !height || width>16384 || height>16384)return 0;
    uint64_t payload=(uint64_t)width*height*4;
    if(payload>UINT32_MAX-54)return 0;
    unsigned char header[54]={0};header[0]='B';header[1]='M';
    const uint32_t words[][2]={{2,(uint32_t)payload+54},{10,54},{14,40},{18,width},{22,0u-height},{34,(uint32_t)payload}};
    for(unsigned i=0;i<sizeof words/sizeof words[0];++i)for(int b=0;b<4;++b)header[words[i][0]+b]=(unsigned char)(words[i][1]>>(8*b));
    header[26]=1;header[28]=32;
    FILE *f=fopen(path,"wb");if(!f)return 0;
    int ok=fwrite(header,1,sizeof header,f)==sizeof header && fwrite(bgra,1,(size_t)payload,f)==payload;
    if(fclose(f))ok=0;
    return ok;
}
