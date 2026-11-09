/* Chassis mounts measured against the stock KIT00 body triangles. */
#include "underglow_render.h"
#include <math.h>
#include <string.h>

static UGVec3 ug_add(UGVec3 a, UGVec3 b);
static UGVec3 ug_scale(UGVec3 a, double s);
static UGVec3 ug_cross(UGVec3 a, UGVec3 b);

static const UGStrip mounts[UG_STRIPS] = {
    {{-.7, -.8, .026910}, {1.15, -.8, -.012890}, {-.021508536677700304, 0, -.9997686646669739}, 1.850428069393674, .01},
    {{-.7,  .8, .026910}, {1.15,  .8, -.012890}, {-.021508536677700304, 0, -.9997686646669739}, 1.850428069393674, .01},
    {{2.05, -.65, -.006730}, {2.05, .65, -.006730}, {0, 0, -1}, 1.3, .01},
    {{-1.85, -.65, .108940}, {-1.85, .65, .108940}, {0, 0, -1}, 1.3, .01}
};

const UGStrip *ug_mount_fixture(const char *model) {
    return model && !strcmp(model, "bmwm3gtre46") ? mounts : NULL;
}

int ug_mount_transform(const char *model, const float basis[16], const float position[3], UGStrip out[UG_STRIPS]) {
    return ug_strips_transform(ug_mount_fixture(model),basis,position,out);
}

int ug_strips_transform(const UGStrip source[UG_STRIPS], const float basis[16], const float position[3], UGStrip out[UG_STRIPS]) {
    if (!out) return 0;
    memset(out, 0, sizeof(UGStrip) * UG_STRIPS);
    if (!source || !basis || !position) return 0;
    for (int i = 0; i < 16; ++i) if (!isfinite(basis[i])) return 0;
    for (int i = 0; i < 3; ++i) if (!isfinite(position[i])) return 0;
    /* Reject scaling/shear/reflection: mount dimensions and optical normals
     * belong to the validated stock chassis, not an arbitrary body transform. */
    for (int i = 0; i < 3; ++i) for (int j = 0; j < 3; ++j) {
        double d = 0;
        for (int k = 0; k < 3; ++k) d += basis[4*i+k] * (double)basis[4*j+k];
        if (fabs(d - (i == j)) > 1e-4) return 0;
    }
    double determinant = basis[0]*(basis[5]*basis[10]-basis[6]*basis[9]) -
        basis[1]*(basis[4]*basis[10]-basis[6]*basis[8]) + basis[2]*(basis[4]*basis[9]-basis[5]*basis[8]);
    if (fabs(determinant - 1) > 1e-4 || fabs(basis[3]) + fabs(basis[7]) + fabs(basis[11]) > 1e-5 || fabs(basis[15]-1) > 1e-5) return 0;
    UGVec3 rows[3] = {{basis[0],basis[1],basis[2]}, {basis[4],basis[5],basis[6]}, {basis[8],basis[9],basis[10]}};
    UGVec3 cofactors[3] = {ug_cross(rows[1], rows[2]), ug_cross(rows[2], rows[0]), ug_cross(rows[0], rows[1])};
    for (int i = 0; i < UG_STRIPS; ++i) {
        out[i] = source[i];
        for (int end = 0; end < 3; ++end) {
            UGVec3 p = end == 0 ? source[i].a : end == 1 ? source[i].b : source[i].normal;
            UGVec3 q = {
                p.x*basis[0] + p.y*basis[4] + p.z*basis[8] + (end < 2 ? position[0] : 0),
                p.x*basis[1] + p.y*basis[5] + p.z*basis[9] + (end < 2 ? position[1] : 0),
                p.x*basis[2] + p.y*basis[6] + p.z*basis[10] + (end < 2 ? position[2] : 0)};
            if (end == 2) q = ug_scale(ug_add(ug_add(ug_scale(cofactors[0], p.x), ug_scale(cofactors[1], p.y)), ug_scale(cofactors[2], p.z)), 1/determinant);
            if (end == 0) out[i].a = q;
            else if (end == 1) out[i].b = q;
            else out[i].normal = q;
        }
        double normal_length = sqrt(out[i].normal.x*out[i].normal.x + out[i].normal.y*out[i].normal.y + out[i].normal.z*out[i].normal.z);
        out[i].normal.x /= normal_length; out[i].normal.y /= normal_length; out[i].normal.z /= normal_length;
        double dx = out[i].b.x-out[i].a.x, dy = out[i].b.y-out[i].a.y, dz = out[i].b.z-out[i].a.z;
        out[i].length = sqrt(dx*dx+dy*dy+dz*dz);
    }
    return 1;
}

static UGVec3 ug_sub(UGVec3 a, UGVec3 b) { return (UGVec3){a.x-b.x, a.y-b.y, a.z-b.z}; }
static UGVec3 ug_add(UGVec3 a, UGVec3 b) { return (UGVec3){a.x+b.x, a.y+b.y, a.z+b.z}; }
static UGVec3 ug_scale(UGVec3 a, double s) { return (UGVec3){a.x*s, a.y*s, a.z*s}; }
static double ug_dot(UGVec3 a, UGVec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static UGVec3 ug_cross(UGVec3 a, UGVec3 b) { return (UGVec3){a.y*b.z-a.z*b.y, a.z*b.x-a.x*b.z, a.x*b.y-a.y*b.x}; }
static int ug_finite(UGVec3 a) { return isfinite(a.x) && isfinite(a.y) && isfinite(a.z); }
static int ug_clip_le(double a, double b, double *lo, double *hi) {
    if (b == 0) return a <= 0;
    double t = -a/b;
    if (b > 0) *hi = fmin(*hi, t); else *lo = fmax(*lo, t);
    return *hi >= *lo;
}

int ug_blocked_interval(UGVec3 receiver, UGVec3 a, UGVec3 b, const UGTriangle *triangle, double interval[2]) {
    if (!interval) return -1;
    interval[0] = interval[1] = 0;
    if (!triangle || !ug_finite(receiver) || !ug_finite(a) || !ug_finite(b)) return -1;
    for (int i = 0; i < 3; ++i) if (!ug_finite(triangle->v[i])) return -1;
    UGVec3 n = ug_cross(ug_sub(triangle->v[1], triangle->v[0]), ug_sub(triangle->v[2], triangle->v[0]));
    double magnitude = sqrt(ug_dot(n, n));
    if (!isfinite(magnitude) || magnitude < 1e-12) return -1;
    n = ug_scale(n, 1/magnitude);
    double distance = ug_dot(n, ug_sub(triangle->v[0], receiver));
    /* The receiver's own triangle is not a source blocker. This is a metric
     * tolerance, independent of triangle area or world-coordinate magnitude. */
    if (fabs(distance) < 1e-7) return 0;
    double sign = distance < 0 ? -1 : 1, lo = 0, hi = 1;
    UGVec3 start = ug_sub(a, receiver), delta = ug_sub(b, a);
    if (!ug_clip_le(fabs(distance)-sign*ug_dot(n, start), -sign*ug_dot(n, delta), &lo, &hi)) return 0;
    for (int i = 0; i < 3; ++i) {
        UGVec3 edge = ug_cross(ug_sub(triangle->v[(i+1)%3], triangle->v[i]), n);
        double r = ug_dot(edge, ug_sub(receiver, triangle->v[i]));
        UGVec3 plane = ug_add(ug_scale(n, r), ug_scale(edge, distance));
        if (!ug_clip_le(sign*ug_dot(plane, start), sign*ug_dot(plane, delta), &lo, &hi)) return 0;
    }
    if (hi <= lo) return 0;
    interval[0] = lo; interval[1] = hi;
    return 1;
}

int ug_visible_lux(double flux, const UGStrip *strip, UGVec3 receiver, UGVec3 normal,
                   const UGTriangle *triangles, unsigned count, double *lux) {
    if (!lux) return 0;
    *lux = 0;
    if (!strip || count > UG_MAX_BLOCKERS || (count && !triangles)) return 0;
    double full;
    if (!ug_segment_lux(flux, strip->a, strip->b, strip->normal, receiver, normal, &full)) return 0;
    if (flux == 0) return 1;
    double intervals[UG_MAX_BLOCKERS][2]; unsigned used = 0;
    for (unsigned i = 0; i < count; ++i) {
        double interval[2];
        int status = ug_blocked_interval(receiver, strip->a, strip->b, &triangles[i], interval);
        if (status < 0) return 0;
        if (!status) continue;
        unsigned at = used++;
        while (at && intervals[at-1][0] > interval[0]) {
            memcpy(intervals[at], intervals[at-1], sizeof interval); --at;
        }
        memcpy(intervals[at], interval, sizeof interval);
    }
    UGVec3 delta = ug_sub(strip->b, strip->a);
    double cursor = 0, sum = 0;
    for (unsigned i = 0; i <= used; ++i) {
        double end = i < used ? intervals[i][0] : 1;
        if (end > cursor) {
            double part;
            UGVec3 a = ug_add(strip->a, ug_scale(delta, cursor));
            UGVec3 b = ug_add(strip->a, ug_scale(delta, end));
            if (!ug_segment_lux(flux, a, b, strip->normal, receiver, normal, &part)) return 0;
            sum += part;
        }
        if (i < used) cursor = fmax(cursor, intervals[i][1]);
    }
    *lux = fmin(full, sum);
    return 1;
}

int ug_receiver_admit(const UGVisibility *snapshot, uint64_t frame, uint64_t generation,
                       UGVec3 receiver, UGVec3 normal) {
    if (!snapshot || snapshot->complete != 1 || snapshot->grounded != 1 || !generation ||
        snapshot->frame != frame || snapshot->generation != generation ||
        !snapshot->count || snapshot->count > UG_MAX_BLOCKERS ||
        !ug_finite(receiver) || !ug_finite(normal) || !ug_finite(snapshot->centre) ||
        !isfinite(snapshot->radius) || snapshot->radius <= 0 || snapshot->radius > 6 ||
        fabs(ug_dot(normal, normal)-1) > 1e-6 || normal.z < .5) return 0;
    UGVec3 offset = ug_sub(receiver, snapshot->centre);
    if (offset.x*offset.x+offset.y*offset.y > snapshot->radius*snapshot->radius) return 0;
    int admitted = 0;
    for (unsigned i = 0; i < snapshot->count; ++i) {
        const UGTriangle *t = &snapshot->triangles[i];
        for (int j = 0; j < 3; ++j) if (!ug_finite(t->v[j])) return 0;
        UGVec3 e = ug_sub(t->v[1], t->v[0]), f = ug_sub(t->v[2], t->v[0]);
        UGVec3 n = ug_cross(e, f), p = ug_sub(receiver, t->v[0]);
        double area = sqrt(ug_dot(n, n));
        if (!isfinite(area) || area < 1e-12) return 0;
        n = ug_scale(n, 1/area);
        if (n.z < 0) n = ug_scale(n, -1);
        if (n.z < .5 || ug_dot(n, normal) < .9 || fabs(ug_dot(n, p)) > .002) continue;
        double ee = ug_dot(e, e), ff = ug_dot(f, f), ef = ug_dot(e, f);
        double pe = ug_dot(p, e), pf = ug_dot(p, f), denominator = ee*ff-ef*ef;
        if (denominator <= 1e-18) continue;
        double u = (pe*ff-pf*ef)/denominator, v = (pf*ee-pe*ef)/denominator;
        /* Inset the admitted triangle to make cracks conservative. */
        if (u >= 1e-6 && v >= 1e-6 && u+v <= 1-1e-6) admitted = 1;
    }
    return admitted;
}

static const char receiver_math[] =
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"static bool ug_clip(float a,float b,thread float &lo,thread float &hi) {\n"
" if(b==0) return a<=0; float t=-a/b; if(b>0)hi=min(hi,t);else lo=max(lo,t);return hi>=lo;\n"
"}\n"
"static float ug_primitive(float t,float q2,float ac,float adbc,float bd) {\n"
" float q=sqrt(q2),angle=atan(t/q),den=q2+t*t;\n"
" float i0=t/(2*q2*den)+angle/(2*q2*q),i1=-1/(2*den),i2=angle/(2*q)-t/(2*den);\n"
" return ac*i0+adbc*i1+bd*i2;\n"
"}\n"
"static float ug_segment(float flux,float3 a,float3 b,float3 en,float3 r,float3 rn) {\n"
" float3 u=b-a,v=a-r;float len=length(u);if(!(len>0)||!isfinite(len)||!(flux>=0)||!isfinite(flux))return 0;u/=len;\n"
" float lo=dot(v,u),hi=lo+len;float3 perp=v-u*lo;float q2=dot(perp,perp);if(!(q2>1e-12f))return 0;\n"
" float aa=-dot(en,perp),bb=-dot(en,u),cc=dot(rn,perp),dd=dot(rn,u);\n"
" if(!ug_clip(-aa,-bb,lo,hi)||!ug_clip(-cc,-dd,lo,hi)||hi<=lo)return 0;\n"
" float value=flux/M_PI_F*(ug_primitive(hi,q2,aa*cc,aa*dd+bb*cc,bb*dd)-ug_primitive(lo,q2,aa*cc,aa*dd+bb*cc,bb*dd));\n"
" return isfinite(value)?max(0.0f,value):0.0f;\n"
"}\n"
"static int ug_interval(float3 r,float3 a,float3 b,float3 v0,float3 v1,float3 v2,thread float2 &interval) {\n"
" float3 n=cross(v1-v0,v2-v0);float mag=length(n);if(!isfinite(mag)||mag<1e-8f)return -1;n/=mag;\n"
" float d=dot(n,v0-r);if(abs(d)<1e-6f)return 0;float sign=d<0?-1.0f:1.0f,lo=0,hi=1;float3 start=a-r,delta=b-a;\n"
" if(!ug_clip(abs(d)-sign*dot(n,start),-sign*dot(n,delta),lo,hi))return 0;\n"
" float3 vv[3]={v0,v1,v2};\n"
" for(uint i=0;i<3;++i){float3 edge=cross(vv[(i+1)%3]-vv[i],n);float rr=dot(edge,r-vv[i]);float3 plane=rr*n+d*edge;\n"
" if(!ug_clip(sign*dot(plane,start),sign*dot(plane,delta),lo,hi))return 0;}\n"
" if(hi<=lo)return 0;interval=float2(max(0.0f,lo-1e-6f),min(1.0f,hi+1e-6f));return 1;\n"
"}\n"
"static float ug_visible(float flux,float3 a,float3 b,float3 en,float3 r,float3 rn,constant float4 *tri,uint count) {\n"
" if(count>64||!all(isfinite(a))||!all(isfinite(b))||!all(isfinite(r))||!all(isfinite(en))||!all(isfinite(rn))||\n"
" abs(dot(en,en)-1)>1e-4f||abs(dot(rn,rn)-1)>1e-4f)return 0;\n"
" float full=ug_segment(flux,a,b,en,r,rn);if(!(full>0))return 0;float2 intervals[64];uint used=0;\n"
" for(uint i=0;i<count;++i){float2 iv;int status=ug_interval(r,a,b,tri[3*i].xyz,tri[3*i+1].xyz,tri[3*i+2].xyz,iv);\n"
" if(status<0)return 0;if(!status)continue;uint at=used++;while(at&&intervals[at-1].x>iv.x){intervals[at]=intervals[at-1];--at;}intervals[at]=iv;}\n"
" float cursor=0,sum=0;float3 delta=b-a;\n"
" for(uint i=0;i<=used;++i){float end=i<used?intervals[i].x:1.0f;if(end>cursor+1e-7f)sum+=ug_segment(flux,a+delta*cursor,a+delta*end,en,r,rn);\n"
" if(i<used)cursor=max(cursor,intervals[i].y);}\n"
" return min(full,sum);\n"
"}\n"
"// Linear Rec.709 flux preserves measured Y. L=rho*E/pi cd/m2; one\n"
"// renderer-linear unit is defined as 100 cd/m2 before exposure/bloom.\n"
"static float3 ug_diffuse(float unit_lux,float3 flux_rgb,float3 albedo) {\n"
" return max(0.0f,unit_lux)*max(float3(0),flux_rgb)*clamp(albedo,0.0f,1.0f)/(M_PI_F*100.0f);\n"
"}\n"
"static bool ug_receiver_current(float3 normal,uint admitted,ulong frame,ulong expected_frame,ulong generation,ulong expected_generation) {\n"
" return admitted==1 && frame==expected_frame && generation!=0 && generation==expected_generation &&\n"
" all(isfinite(normal)) && abs(dot(normal,normal)-1)<1e-4f && normal.z>=.5f;\n"
"}\n";

const char *ug_render_msl(void) { return receiver_math; }

#ifndef UG_RENDER_CORE_ONLY
#include "host.h"
#include "underglow.h"
#include "roadfilm.h"
#include <strings.h>

static int scene_requested, scene_observed;
static uint64_t scene_started;
static _Thread_local UGCarCapture *active_capture;
static uint32_t ug_arg(const Cpu *c, unsigned index) { return M32(c->esp+4+4*index); }
typedef struct UGReceiverObservation {
    uint32_t mesh, effect, view, texture[5];
    float world[16];
    int valid;
} UGReceiverObservation;
static UGReceiverObservation receiver_observation;
static unsigned scene_meshes;
static uint64_t scene_mesh_keys[64][10];
static unsigned scene_variants;
static int scene_has_car;
static float scene_centre[3];

int underglow_render_scene_step(const char *name, uint64_t elapsed) {
    if (!diag_on) return -1;
    if (strcmp(name, "garage") && strcmp(name, "dry") && strcmp(name, "wet") && strcmp(name, "bridge")) return -1;
    if (strcmp(name, "garage")) {
        diag_event("underglow scene: FAIL %s: no pinned geometry/camera/receiver manifest", name);
        return -1;
    }
    if (!elapsed) {
        scene_requested = 1; scene_observed = 0; scene_meshes = 0; scene_variants = 0; scene_has_car = 0; scene_started = host_ticks();
        diag_event("underglow scene: garage observation requested; appearance preflight pending");
    }
    if (scene_observed && elapsed >= 60) {
        scene_requested = 0;
        diag_event("underglow scene: FAIL garage: car observed, %u mesh samples; receiver material, source visibility and held render state not validated; input stopped", scene_meshes);
        return -1;
    }
    if (elapsed >= 1800 || host_ticks()-scene_started >= 30000000000ull) {
        scene_requested = 0;
        diag_event("underglow scene: FAIL garage: supported car observation timeout");
        return -1;
    }
    return 0;
}

void underglow_render_car_begin(const Cpu *c, UGCarCapture *s) {
    memset(s, 0, sizeof *s);
    s->previous = active_capture; active_capture = NULL;
    if (!diag_on || !scene_requested) return;
    /* The garage submits the reflected car first, using the main view ID.
     * It is not the physical chassis pose or a ground-height observation. */
    if (ug_arg(c, 8)) return;
    uint32_t self = c->ecx, ride = M32(self+0x84), types = M32(0x009B09D8u);
    if (!ride || !types || M32(ride) >= 256) return;
    uint32_t type = M32(ride);
    if (strncasecmp((const char *)G+types+type*0xd0, "bmwm3gtre46", 12)) return;
    s->self = self; s->view = ug_arg(c, 0); s->view_id = M32(s->view+4);
    if (s->view_id != 1) return;
    uint32_t matrix = ug_arg(c, 2), position = ug_arg(c, 1);
    if (!matrix || !position) return;
    memcpy(s->basis, G+matrix, sizeof s->basis);
    memcpy(s->position, G+position, sizeof s->position);
    for (int i = 0; i < 2; ++i) {
        uint32_t wheel = ug_arg(c, 3+i);
        if (wheel) memcpy(s->wheels[i], G+wheel, sizeof s->wheels[i]);
    }
    int lod = (int)M32(self+0x1628);
    if (lod < 0 || lod > 3) return;
    /* Part 00 is BASE (interior), not BODY. BODY is retail slot 17. */
    s->body = M32(self+0xb84+0x17*0x14+lod*4)&~3u;
    if (!s->body || M32(s->body+8) != 0x8E3052B5u) return;
    UGStrip transformed[UG_STRIPS];
    if (!ug_mount_transform("bmwm3gtre46", s->basis, s->position, transformed)) return;
    uint32_t db = M32(0x0091CF90u);
    s->profile = db ? M32(db+0x10) : 0;
    s->handle = s->profile ? M32(s->profile+0xA8) : UINT32_MAX;
    s->preset = underglow_get(s->profile, s->handle);
    s->valid = 1;
    active_capture = s;
}

void underglow_render_part(const Cpu *c) {
    UGCarCapture *s = active_capture;
    if (!diag_on || !scene_requested || !s || !s->valid || c->ecx != s->self || ug_arg(c, 0) != s->view) return;
    uint32_t offset = ug_arg(c, 1)-s->self, matrix = ug_arg(c, 2);
    if (!matrix) return;
    if (offset >= 0x10ac && offset <= 0x10bc && s->front_tyres < 2)
        memcpy(s->tyre_world[s->front_tyres++], G+matrix, 64);
    else if (offset >= 0x10c0 && offset <= 0x10d0 && s->rear_tyres < 2)
        memcpy(s->tyre_world[2+s->rear_tyres++], G+matrix, 64);
}

static void ug_json_floats(FILE *file, const float *v, unsigned count) {
    fputc('[', file);
    for (unsigned i = 0; i < count; ++i) fprintf(file, "%s%.9g", i ? "," : "", v[i]);
    fputc(']', file);
}

void underglow_render_car_end(const Cpu *c, const UGCarCapture *s) {
    active_capture = s->previous;
    if (!s->valid || !(c->eax&255) || !scene_requested || !diag_dir) return;
    if (!scene_has_car) { memcpy(scene_centre, s->position, sizeof scene_centre); scene_has_car = 1; }
    const char *stage = "observation";
    unsigned variant = 0;
    if (postfx_look_variant("ugoff")) { stage = "ugoff"; variant = 1; }
    else if (postfx_look_variant("ugrepeat")) { stage = "ugrepeat"; variant = 2; }
    else if (postfx_look_variant("uggreen")) { stage = "uggreen"; variant = 4; }
    if ((!variant && scene_observed) || (variant && (scene_variants&variant))) return;
    char path[4096];
    int length = snprintf(path, sizeof path, "%s/underglow_garage_%s.json", diag_dir, stage);
    if (length <= 0 || length >= (int)sizeof path) return;
    FILE *file = fopen(path, "w");
    if (!file) return;
    uint32_t platform = M32(s->view), camera = M32(s->view+0x40);
    fprintf(file, "{\"status\":\"observation-only\",\"model\":\"bmwm3gtre46\",\"self\":%u,\"view\":%u,\"view_id\":%u,\"body_hash\":%u,\"handle\":%u,\"preset\":%u,\"basis\":",
            s->self, s->view, s->view_id, s->body ? M32(s->body+8) : 0, s->handle, s->preset);
    ug_json_floats(file, s->basis, 16);
    fputs(",\"position\":", file); ug_json_floats(file, s->position, 3);
    fputs(",\"car_scale\":", file); ug_json_floats(file, (const float *)(G+0x009B34B0u), 16);
    fputs(",\"wheel_input_0\":", file); ug_json_floats(file, s->wheels[0], 64);
    fputs(",\"wheel_input_1\":", file); ug_json_floats(file, s->wheels[1], 64);
    fprintf(file, ",\"front_tyres\":%u,\"rear_tyres\":%u,\"tyre_world\":[", s->front_tyres, s->rear_tyres);
    for (int i = 0; i < 4; ++i) { if (i) fputc(',', file); ug_json_floats(file, s->tyre_world[i], 16); }
    fputc(']', file);
    if (platform) {
        fputs(",\"view_matrix\":", file); ug_json_floats(file, (const float *)(G+platform), 16);
        fputs(",\"projection\":", file); ug_json_floats(file, (const float *)(G+platform+0x40), 16);
    }
    if (camera) { fputs(",\"camera\":", file); ug_json_floats(file, (const float *)(G+camera+0x40), 24); }
    fputs("}\n", file);
    if (fclose(file)) return;
    if (variant) scene_variants |= variant; else scene_observed = 1;
    diag_event("underglow mount observation: bmwm3gtre46 handle=%u preset=%u body=%08X view=%u position=%.6f,%.6f,%.6f (no render contribution)",
               s->handle, s->preset, s->body ? M32(s->body+8) : 0, s->view_id, s->position[0], s->position[1], s->position[2]);
}

int underglow_render_observing(void) { return diag_on && scene_requested; }
void underglow_render_receiver_clear(void) { receiver_observation.valid = 0; }
void underglow_render_receiver_record(uint32_t queue) {
    receiver_observation.valid = 0;
    if (!diag_on || !scene_requested || scene_meshes >= 64 || !queue || M32(queue+4)) return;
    uint32_t view = M32(0x00982A20u), effect = M32(queue+16), world = M32(queue+28), mesh = M32(queue);
    if (!view || M32(view+4) != 1 || !effect || !world || !mesh) return;
    uint32_t id = M32(effect+4);
    if (id != 1 && id != 3) return;
    receiver_observation.mesh = mesh; receiver_observation.effect = id; receiver_observation.view = view;
    memcpy(receiver_observation.world, G+world, 64);
    for (int i = 0; i < 16; ++i) if (!isfinite(receiver_observation.world[i])) return;
    for (int i = 0; i < 5; ++i) receiver_observation.texture[i] = M32(queue+36+4*i);
    receiver_observation.valid = 1;
}

void underglow_render_mesh_observe(const UGMeshView *m) {
    if (!diag_on || !scene_requested || !diag_dir || !scene_has_car || scene_meshes >= 64 || !m) return;
    uint32_t current_effect = M32(0x00982C80u);
    if (current_effect && M32(current_effect+4) == 4) return;
    /* Some garage geometry bypasses the scenery queue. The retail matrix
     * setter records its exact last world/effect inputs at these addresses.
     * This fallback inventories geometry only; it grants no light eligibility. */
    if (!receiver_observation.valid) {
        uint32_t view = M32(0x00982A20u), effect = M32(0x00982C80u), world = M32(0x00988554u);
        if (!view || M32(view+4) != 1 || !effect || !world || M32(effect+4) > 32 || M32(effect+4) == 4) return;
        memset(&receiver_observation, 0, sizeof receiver_observation);
        receiver_observation.view = view; receiver_observation.effect = M32(effect+4);
        memcpy(receiver_observation.world, G+world, 64);
        for (int i = 0; i < 16; ++i) if (!isfinite(receiver_observation.world[i])) return;
        for (int i = 0; i < 5; ++i) receiver_observation.texture[i] = M32(0x00982CB4u+4*i);
    }
    if (!m->vertices || !m->indices || m->stride < 12 || m->position_offset > m->stride-12 ||
        (m->index_width != 2 && m->index_width != 4) || !m->primitives || m->primitives > 4096 ||
        (uint64_t)m->start*m->index_width > m->index_bytes ||
        (uint64_t)m->primitives*3*m->index_width > m->index_bytes-(uint64_t)m->start*m->index_width) return;
    uint64_t world_hash = 14695981039346656037ull;
    const uint8_t *world_bytes = (const uint8_t *)receiver_observation.world;
    for (unsigned i = 0; i < 64; ++i) world_hash = (world_hash^world_bytes[i])*1099511628211ull;
    uint64_t key[10] = {receiver_observation.mesh, m->vertex_generation, m->index_generation, m->shader,
        (uint32_t)m->base_vertex, m->start, m->primitives, world_hash, m->vertex_buffer, m->index_buffer};
    for (unsigned i = 0; i < scene_meshes; ++i) if (!memcmp(key, scene_mesh_keys[i], sizeof key)) return;
    unsigned count = 0, nearby = 0;
    float triangles[128][3][3], bounds[6] = {INFINITY, INFINITY, INFINITY, -INFINITY, -INFINITY, -INFINITY};
    for (unsigned i = 0; i < m->primitives; ++i) {
      float triangle[3][3], box[6] = {INFINITY, INFINITY, INFINITY, -INFINITY, -INFINITY, -INFINITY};
      for (int v = 0; v < 3; ++v) {
        uint32_t index = 0;
        memcpy(&index, m->indices+((uint64_t)m->start+3*i+v)*m->index_width, m->index_width);
        int64_t vertex = (int64_t)m->base_vertex+index;
        if (vertex < 0 || (uint64_t)vertex > (SIZE_MAX-m->position_offset)/m->stride) return;
        uint64_t offset = (uint64_t)vertex*m->stride+m->position_offset;
        if (offset > m->vertex_bytes || m->vertex_bytes-offset < 12) return;
        float p[3]; memcpy(p, m->vertices+offset, 12);
        for (int c = 0; c < 3; ++c) {
            const float *w = receiver_observation.world;
            float x = p[0]*w[c]+p[1]*w[4+c]+p[2]*w[8+c]+w[12+c];
            if (!isfinite(x)) return;
            triangle[v][c] = x;
            box[c] = fminf(box[c], x); box[3+c] = fmaxf(box[3+c], x);
        }
      }
      double dx = fmax(fmax(box[0]-scene_centre[0], scene_centre[0]-box[3]), 0);
      double dy = fmax(fmax(box[1]-scene_centre[1], scene_centre[1]-box[4]), 0);
      if (dx*dx+dy*dy > 12.25 || box[2] > scene_centre[2]+.5 || box[5] < scene_centre[2]-6) continue;
      ++nearby;
      if (count == 128) continue;
      memcpy(triangles[count++], triangle, sizeof triangle);
      for (int c = 0; c < 3; ++c) { bounds[c] = fminf(bounds[c], box[c]); bounds[3+c] = fmaxf(bounds[3+c], box[3+c]); }
    }
    if (!count) return;
    char path[4096];
    int n = snprintf(path, sizeof path, "%s/underglow_mesh_%03u.bin", diag_dir, scene_meshes);
    if (n <= 0 || n >= (int)sizeof path) return;
    FILE *file = fopen(path, "wb"); if (!file) return;
    size_t written = fwrite(triangles, 36, count, file); int closed = fclose(file);
    if (written != count || closed) return;
    n = snprintf(path, sizeof path, "%s/underglow_meshes.jsonl", diag_dir);
    if (n <= 0 || n >= (int)sizeof path) return;
    file = fopen(path, scene_meshes ? "a" : "w"); if (!file) return;
    fprintf(file, "{\"status\":\"observation-only\",\"sample\":%u,\"mesh\":%u,\"effect\":%u,\"view\":%u,\"shader\":\"%016llx\",\"eligible_shader\":%s,\"vertex_generation\":%llu,\"index_generation\":%llu,\"vertex_buffer\":%u,\"index_buffer\":%u,\"start\":%u,\"base_vertex\":%d,\"triangles\":%u,\"nearby_triangles\":%u,\"input_triangles\":%u,\"complete\":%s,\"bounds\":",
            scene_meshes, receiver_observation.mesh, receiver_observation.effect, receiver_observation.view,
            (unsigned long long)m->shader, roadfilm_shader_hash(m->shader) ? "true" : "false",
            (unsigned long long)m->vertex_generation, (unsigned long long)m->index_generation,
            m->vertex_buffer, m->index_buffer, m->start, m->base_vertex,
            count, nearby, m->primitives, count == nearby ? "true" : "false");
    ug_json_floats(file, bounds, 6);
    fputs(",\"world\":", file); ug_json_floats(file, receiver_observation.world, 16);
    fprintf(file, ",\"wvp_valid\":%s,\"world_view_projection_columns\":", m->wvp_valid ? "true" : "false");
    ug_json_floats(file, m->wvp_columns, 16);
    fprintf(file, ",\"bound_textures\":[%u,%u]", m->texture[0], m->texture[1]);
    fputs(",\"textures\":[", file);
    for (int i = 0; i < 5; ++i) fprintf(file, "%s%u", i ? "," : "", receiver_observation.texture[i]);
    fputs("]}\n", file);
    if (!fclose(file)) { memcpy(scene_mesh_keys[scene_meshes], key, sizeof key); ++scene_meshes; }
}
#endif
