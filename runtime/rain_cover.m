/* Orthographic highest-surface map of resident opaque scenery. CPU rasterization
 * avoids a GPU readback or synchronization in the guest precipitation loop.
 * No camera visibility, sun-shadow settings, authored zone edits or asset tables.
 */
#include "rain_cover.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int cover_needs(const RainCoverMap *m, float x, float y, uint64_t frame) {
    return !m->valid || frame < m->frame ||
        fabsf(x-m->x) >= RAIN_COVER_SPAN*.5f-RAIN_COVER_MARGIN ||
        fabsf(y-m->y) >= RAIN_COVER_SPAN*.5f-RAIN_COVER_MARGIN;
}
/* Include the 18.42 m rotated particle reach and a little streak room. */
static int cover_contains(const RainCoverMap *m, float x, float y) {
    return m->valid && fabsf(x-m->x) < 64-RAIN_COVER_REACH &&
        fabsf(y-m->y) < 64-RAIN_COVER_REACH;
}
static double cover_edge_frames(const RainCoverMap *m, float x, float y, float dx, float dy) {
    if (!cover_contains(m,x,y)) return 0;
    double tx = dx ? (64-RAIN_COVER_REACH-(dx>0 ? x-m->x : m->x-x))/fabsf(dx) : INFINITY;
    double ty = dy ? (64-RAIN_COVER_REACH-(dy>0 ? y-m->y : m->y-y))/fabsf(dy) : INFINITY;
    return fmin(tx,ty);
}
static int cover_start(RainCoverMap *m, float x, float y, uint64_t frame) {
    if (!isfinite(x) || !isfinite(y)) { m->valid = 0; return 0; }
    x = floorf(x * 4) * .25f; y = floorf(y * 4) * .25f;
    if (!m->height) {
        m->height = malloc(RAIN_COVER_SIZE * RAIN_COVER_SIZE * sizeof(float));
        if (!m->height) { m->valid = 0; return 0; }
        ++m->allocations;
    }
    m->x = x; m->y = y; m->frame = frame; m->triangles = 0;
    m->valid = 1; ++m->generation;
    return 1;
}
int rain_cover_begin(RainCoverMap *m, float x, float y, uint64_t frame) {
    if (isfinite(x) && isfinite(y) && !cover_needs(m,x,y,frame)) return 0;
    if (!cover_start(m,x,y,frame)) return 0;
    for (unsigned i = 0; i < RAIN_COVER_SIZE * RAIN_COVER_SIZE; ++i) m->height[i] = -INFINITY;
    return 1;
}

typedef struct {
    double x[3], y[3], d;
    float p[3][3], uv[3][3];
    int lx, hx, hy, row, col, active;
} CoverRaster;
static void cover_raster_begin(CoverRaster *r, RainCoverMap *m, const float p[3][3], const float uv[3][3]) {
    r->active = 0;
    if (!m->valid) return;
    double *x = r->x, *y = r->y;
    for (int i = 0; i < 3; ++i) {
        for (int j = 0; j < 3; ++j) if (!isfinite(p[i][j])) return;
        x[i] = (p[i][0] - (double)m->x + 64) * 4;
        y[i] = (p[i][1] - (double)m->y + 64) * 4;
    }
    double d = (x[1]-x[0])*(y[2]-y[0]) - (y[1]-y[0])*(x[2]-x[0]);
    if (fabs(d) < 1e-8) return; // vertical walls have no top-down area
    double lx = fmax(0, ceil(fmin(x[0],fmin(x[1],x[2]))-.5));
    double hx = fmin(511, floor(fmax(x[0],fmax(x[1],x[2]))-.5));
    double ly = fmax(0, ceil(fmin(y[0],fmin(y[1],y[2]))-.5));
    double hy = fmin(511, floor(fmax(y[0],fmax(y[1],y[2]))-.5));
    if (lx > hx || ly > hy) return;
    ++m->triangles;
    memcpy(r->p,p,sizeof r->p);
    if (uv) memcpy(r->uv,uv,sizeof r->uv);
    r->d = d; r->lx = lx; r->hx = hx; r->hy = hy;
    r->row = ly; r->col = lx; r->active = 1;
}
static void cover_raster_step(CoverRaster *r, RainCoverMap *m, unsigned budget,
                              float (*opacity)(void *,float,float), void *texture) {
    if (!r->active) return;
    const double *x = r->x, *y = r->y, d = r->d;
    const float (*p)[3] = r->p, (*uv)[3] = r->uv;
    while (r->active && budget--) {
        int row = r->row, col = r->col;
        if (++r->col > r->hx) { r->col = r->lx; if (++r->row > r->hy) r->active = 0; }
        double px = col+.5-x[0], py = row+.5-y[0];
        double b = (px*(y[2]-y[0])-py*(x[2]-x[0]))/d;
        double c = ((x[1]-x[0])*py-(y[1]-y[0])*px)/d;
        if (b < -1e-9 || c < -1e-9 || b+c > 1+1e-9) continue;
        if (opacity) {
            float t[3];
            for (int k = 0; k < 3; ++k) t[k] = (float)(uv[0][k]+b*(uv[1][k]-uv[0][k])+c*(uv[2][k]-uv[0][k]));
            if (opacity(texture,t[0],t[1])*t[2] <= 95.0f/255) continue;
        }
        float h = (float)(p[0][2]+b*(p[1][2]-p[0][2])+c*(p[2][2]-p[0][2]));
        unsigned at = row*RAIN_COVER_SIZE+col;
        if (h > m->height[at]) m->height[at] = h;
    }
}
static void cover_triangle(RainCoverMap *m, const float p[3][3], const float uv[3][3],
                           float (*opacity)(void *,float,float), void *texture) {
    CoverRaster r;
    cover_raster_begin(&r,m,p,uv);
    cover_raster_step(&r,m,UINT32_MAX,opacity,texture);
}
void rain_cover_triangle(RainCoverMap *m, const float p[3][3]) {
    cover_triangle(m,p,NULL,NULL,NULL);
}

int rain_cover_sample(const RainCoverMap *m, float x, float y, float z) {
    if (!m->valid || !isfinite(x) || !isfinite(y) || !isfinite(z)) return 0;
    float u = (x-m->x+64)*4, v = (y-m->y+64)*4;
    if (!(u >= 0 && u < 512 && v >= 0 && v < 512)) return 0;
    return m->height[(unsigned)v*512+(unsigned)u] > z + RAIN_COVER_CLEARANCE;
}

#ifndef RAIN_COVER_CORE_ONLY
#include "d3d9_internal.h"
#include "settings.h"
#include "materials.h"
#include "roadfilm.h"

int rain_cover_enabled = -1;
static RainCoverMap map, back;
static void cover_invalidate(void);
static void cover_reset(void);
static uint32_t rain_scope;
static int curtain_scope;
static uint64_t tested, hidden, curtain_tested, curtain_hidden;
static id<MTLTexture> gpu_map;
static uint64_t gpu_generation;
int rain_cover_on(void) {
    if (rain_cover_enabled < 0) { const char *e = getenv("NFS_RAIN_COVER"); rain_cover_enabled = !(e && *e) || !strcmp(e,"on"); }   // unset: On
    return rain_cover_enabled;
}
void rain_cover_set(int on) {
    rain_cover_enabled = on != 0; cover_reset();
    if (!rain_cover_enabled) { free(map.height); map.height = NULL; free(back.height); back.height = NULL; gpu_map = nil; gpu_generation = 0; }
}

typedef struct { Obj *image; unsigned wrap; } CoverOpacity;
static float alpha_texel(Obj *t, unsigned x, unsigned y) {
    unsigned p = t->mem[0], pitch = t->pitch[0];
    if (t->fmt == 21) return M8(p+y*pitch+x*4+3)/255.f;
    if (t->fmt == 22) return 1;
    if (t->fmt == 25) return (M16(p+y*pitch+x*2)>>15)&1;
    if (t->fmt == 26) return (M16(p+y*pitch+x*2)>>12)/15.f;
    if (t->fmt == 28) return M8(p+y*pitch+x)/255.f;
    unsigned pixel = (y&3)*4+(x&3), block = p+(y/4)*pitch+(x/4)*(t->fmt == 0x31545844u ? 8 : 16);
    if (t->fmt == 0x31545844u) {
        return !(M16(block) <= M16(block+2) && ((M32(block+4)>>(2*pixel))&3) == 3);
    }
    if (t->fmt == 0x33545844u || t->fmt == 0x32545844u) return ((M8(block+pixel/2)>>(4*(pixel&1)))&15)/15.f;
    unsigned a = M8(block), b = M8(block+1);
    uint64_t codes = 0; memcpy(&codes,G+block+2,6);
    unsigned code = (unsigned)(codes>>(3*pixel))&7;
    if (code == 0) return a/255.f;
    if (code == 1) return b/255.f;
    if (a > b) return ((8-code)*a+(code-1)*b)/(7.f*255);
    if (code >= 6) return code == 7;
    return ((6-code)*a+(code-1)*b)/(5.f*255);
}
static unsigned alpha_address(int p, unsigned n, int wrap) {
    if (!wrap) return p < 0 ? 0 : (unsigned)p >= n ? n-1 : (unsigned)p;
    int v = p%(int)n; return v < 0 ? v+(int)n : v;
}
static float cover_opacity(void *context, float u, float v) {
    CoverOpacity *a = context; Obj *t = a->image;
    if (!isfinite(u) || !isfinite(v)) return 0;
    u = a->wrap&1 ? u-floorf(u) : fminf(1,fmaxf(0,u));
    v = a->wrap&2 ? v-floorf(v) : fminf(1,fmaxf(0,v));
    float x = u*t->w-.5f, y = v*t->h-.5f;
    int ix = (int)floorf(x), iy = (int)floorf(y);
    float fx = x-ix, fy = y-iy, alpha = 0;
    for (int row = 0; row < 2; ++row) for (int col = 0; col < 2; ++col) {
        unsigned px = alpha_address(ix+col,t->w,a->wrap&1), py = alpha_address(iy+row,t->h,a->wrap&2);
        alpha += alpha_texel(t,px,py)*(col?fx:1-fx)*(row?fy:1-fy);
    }
    return alpha;
}
static int alpha_supported(const Obj *t) {
    if (!t || t->kind != K_TEXTURE || !t->mem[0] || !t->w || !t->h || t->w > 8192 || t->h > 8192 || !t->pitch[0]) return 0;
    switch (t->fmt) {
    case 21: case 22: case 25: case 26: case 28:
    case 0x31545844u: case 0x32545844u: case 0x33545844u: case 0x34545844u: case 0x35545844u: return 1;
    default: return 0;
    }
}
static int cover_attribute(uint32_t fx, unsigned usage, unsigned type, unsigned bytes, unsigned stride) {
    Obj *decl = obj(M32(fx+0x4c));
    for (unsigned i = 0; decl && decl->kind == K_DECL && i < decl->decl_n && i < 64; ++i) {
        uint32_t e = decl->decl_mem+8*i;
        if (M16(e) == 0 && M8(e+4) == type && M8(e+6) == usage && !M8(e+7) && M16(e+2)+bytes <= stride) return M16(e+2);
    }
    return -1;
}

/* Section loader 0072D0E0: section +18/+1c models, +20/+24 instances;
 * DrawAScenery 00729160: instance +3e model index, model +28 LOD A.
 * Render 006DA9B0 / draw 006D7660: solid platform +14 mesh array (68-byte
 * entries), mesh +34 effect/stride, +44 index start, +40 triangle count,
 * +4c CPU vertices, +54 D3D vertices. Positions are float3 at stream offset 0.
 * Opaque standard/world/worldnormal/worldreflect meshes cast. Alpha-tested
 * roof cutouts use their CPU texture alpha, UVs and interpolated vertex alpha;
 * blended materials, foliage, water, glass and cars do not fill holes.
 */
/* Only scalar guest addresses/indices survive a slice. Objects and streams are
 * resolved on the game thread after checking the resident list each frame. */
typedef struct {
    int active, phase;
    unsigned sections, instance, mesh, triangle;
    uint32_t section, instances, models, nm, ni, solid, meshes, mesh_count, ib;
    uint32_t vertices, bytes, stride, count, first, image, wrap;
    int uv_offset, colour_offset;
    float world[4][4];
    CoverRaster raster;
} CoverBuild;
enum { COVER_CLEAR, COVER_SECTION, COVER_INSTANCE, COVER_MESH, COVER_TRIANGLE, COVER_RASTER };
static CoverBuild build;
static uint64_t scene_signature, prepared_frame, slice_frame;
static int prepared, sliced;
static float previous_x, previous_y;
static uint64_t previous_time, build_work_ns, last_work_ns = 5500000;
static uint64_t last_build_frames = 37;
static double frame_seconds = 1.0/90;
static int scene_dirty;
static double build_travel;
static float build_dx, build_dy;
static uint64_t diag_uncovered, diag_reset_uncovered, diag_edge_uncovered;
static uint64_t diag_draw_frame = UINT64_MAX;
static int diag_reset_pending;
static uint64_t diag_slices, diag_rebuilds, diag_frames;
static uint64_t diag_cover_frame = UINT64_MAX;
static double diag_build_ms, diag_max_ms;

static uint64_t cover_clock(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000u+t.tv_nsec;
}
static uint64_t cover_signature(void) {
    const uint32_t end = 0x009AA478u;
    uint32_t s = M32(end);
    uint64_t h = 14695981039346656037ull;
    for (unsigned n = 0; s && s != end && n < 4096; ++n) {
        const uint32_t values[] = {s,M32(s),M32(s+0x18),M32(s+0x1c),M32(s+0x20),M32(s+0x24)};
        for (unsigned i = 0; i < sizeof values/sizeof *values; ++i) h = (h^values[i])*1099511628211ull;
        s = values[1];
    }
    return s == end ? h : 0;
}
static void cover_invalidate(void) {
    map.valid = back.valid = 0; build.active = 0;
}
static void cover_reset(void) {
    cover_invalidate(); prepared = sliced = scene_dirty = 0;
    previous_time = build_work_ns = 0; last_work_ns = 5500000;
    last_build_frames = 37; frame_seconds = 1.0/90; build_travel = 30; build_dx = build_dy = 0;
}
static void cover_step(void) {
    switch (build.phase) {
    case COVER_CLEAR: {
        for (unsigned i = 0; i < RAIN_COVER_SIZE*RAIN_COVER_SIZE; ++i) back.height[i] = -INFINITY;
        build.phase = COVER_SECTION;
        return;
    }
    case COVER_SECTION: {
        uint32_t s = build.section;
        if (s == 0x009AA478u) {
            RainCoverMap old = map;
            back.generation = map.generation+1;
            map = back; back = old; back.valid = 0;
            build.active = 0;
            last_build_frames = dev.frames-map.frame+1;
            if (diag_on) { ++diag_rebuilds; diag_reset_pending = 0; }
            return;
        }
        if (!s || ++build.sections > 4096) { cover_invalidate(); return; }
        build.ni = M32(s+0x24); build.nm = M32(s+0x1c);
        build.instances = M32(s+0x20); build.models = M32(s+0x18);
        build.instance = 0; build.phase = COVER_INSTANCE;
        if (!build.instances || !build.models || build.ni > 65536 || build.nm > 65536) build.ni = 0;
        return;
    }
    case COVER_INSTANCE: {
        /* Batch rejected instances; bound the batch so even an implausibly
         * large resident list returns to the deadline check promptly. */
        for (unsigned skipped = 0; skipped < 128; ++skipped) {
            if (build.instance == build.ni) { build.section = M32(build.section); build.phase = COVER_SECTION; return; }
            uint32_t inst = build.instances+64*build.instance++;
            if (MF32(inst+12) < back.x-64 || MF32(inst) > back.x+64 ||
                MF32(inst+16) < back.y-64 || MF32(inst+4) > back.y+64) continue;
            unsigned index = M16(inst+0x3e);
            if (index >= build.nm) continue;
            uint32_t flags = M32(inst+0x18);
            /* FUN_00723F70: retain exactly the world-detail caster filter. */
            if (((flags & 0x80000) && back.world_detail != 0) ||
                ((flags & 0x40000) && back.world_detail == 0 && !(flags & 0x100))) continue;
            float w[4][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}};
            if (!(flags & 0x200)) {
                int16_t basis[9]; memcpy(basis,G+inst+0x2c,sizeof basis);
                for (int r = 0; r < 3; ++r) for (int c = 0; c < 3; ++c) w[r][c] = basis[3*r+c]/8192.f;
                memcpy(w[3],G+inst+0x20,12);
            }
            memcpy(build.world,w,sizeof w);
            uint32_t model = M32(build.models+index*0x48+0x28);
            build.solid = model ? M32(model+0xc) : 0;
            uint32_t plat = build.solid ? M32(build.solid) : 0;
            if (!plat) continue;
            build.mesh_count = M32(plat+0x10); build.meshes = M32(plat+0x14); build.ib = M32(plat+0x28);
            Obj *ib = obj(build.ib);
            if (!build.meshes || build.mesh_count > 4096 || !ib || ib->kind != K_IB || ib->fmt != 101 || !ib->mem[0]) continue;
            build.mesh = 0; build.phase = COVER_MESH;
            return;
        }
        return;
    }
    case COVER_MESH: {
        if (build.mesh == build.mesh_count) { build.phase = COVER_INSTANCE; return; }
        uint32_t mesh = build.meshes+build.mesh++*0x68, fx = M32(mesh+0x34);
        if (!fx) return;
        unsigned type = M32(fx+4), stride = M32(fx+8);
        if ((type != 0 && type != 1 && type != 3 && type != 8) || stride < 12 || stride > 128) return;
        uint32_t textures = M32(build.solid+0x2c);
        uint32_t ti = textures ? M32(textures+4+M8(mesh+0x18)*8) : 0;
        if (!ti || M8(ti+0x56)) return;
        build.uv_offset = build.colour_offset = -1;
        build.image = 0;
        if (M8(ti+0x55) == 1) {
            uint32_t texture_platform = M32(ti);
            build.image = texture_platform ? M32(texture_platform+0x18) : 0;
            build.wrap = M8(ti+0x4f);
            build.uv_offset = cover_attribute(fx,5,1,8,stride);
            build.colour_offset = cover_attribute(fx,10,4,4,stride);
            if (build.uv_offset < 0 || !alpha_supported(obj(build.image))) return;
        }
        Obj *vb = obj(M32(mesh+0x54)), *ib = obj(build.ib);
        build.vertices = vb && vb->kind == K_VB ? vb->mem[0] : M32(mesh+0x4c);
        build.bytes = vb && vb->kind == K_VB ? vb->size : M32(mesh+0x50);
        build.count = M32(mesh+0x40); build.first = M32(mesh+0x44); build.stride = stride;
        if (!ib || !build.vertices || build.count > 100000 || build.first > ib->size/2 || build.count > (ib->size/2-build.first)/3) return;
        build.triangle = 0; build.phase = COVER_TRIANGLE;
        return;
    }
    case COVER_TRIANGLE: {
        if (build.triangle == build.count) { build.phase = COVER_MESH; return; }
        Obj *ib = obj(build.ib);
        if (!ib || ib->kind != K_IB || !ib->mem[0]) { cover_invalidate(); return; }
        unsigned k = build.triangle++, stride = build.stride;
        float p[3][3], uv[3][3];
        for (unsigned corner = 0; corner < 3; ++corner) {
            unsigned index = M16(ib->mem[0]+(build.first+3*k+corner)*2);
            if ((uint64_t)index*stride+12 > build.bytes) return;
            float local[3]; memcpy(local,G+build.vertices+index*stride,12);
            for (int c = 0; c < 3; ++c) p[corner][c] = build.world[3][c]+local[0]*build.world[0][c]+local[1]*build.world[1][c]+local[2]*build.world[2][c];
            if (build.uv_offset >= 0) {
                memcpy(uv[corner],G+build.vertices+index*stride+build.uv_offset,8);
                uv[corner][2] = build.colour_offset >= 0 ? M8(build.vertices+index*stride+build.colour_offset+3)/255.f : 1;
            }
        }
        cover_raster_begin(&build.raster,&back,p,build.uv_offset >= 0 ? uv : NULL);
        if (build.raster.active) build.phase = COVER_RASTER;
        return;
    }
    case COVER_RASTER: {
        CoverOpacity opacity = {.image=obj(build.image),.wrap=build.wrap};
        if (build.uv_offset >= 0 && !alpha_supported(opacity.image)) { cover_invalidate(); return; }
        cover_raster_step(&build.raster,&back,64,build.uv_offset >= 0 ? cover_opacity : NULL,&opacity);
        if (!build.raster.active) build.phase = COVER_TRIANGLE;
        return;
    }
    }
}
static uint32_t prepare(uint32_t rain) {
    if (diag_on && diag_cover_frame != dev.frames) { diag_cover_frame = dev.frames; ++diag_frames; }
    uint32_t view = rain ? M32(rain+0x288) : 0, camera = view ? M32(view+0x40) : 0;
    if (!camera || M32(0x00925E90u) != 6 || (M32(view+4) != 1 && M32(view+4) != 2)) {
        cover_invalidate(); prepared = 0; return 0;
    }
    uint32_t world_detail = M32(0x00982B9Cu);
    int context_changed = map.view != view || map.camera != camera || map.world_detail != world_detail;
    if (context_changed) {
        if (diag_on) { diag_reset_pending = 1; diag_event("rain cover reset: view %08x camera %08x detail %u",view,camera,world_detail); }
        cover_invalidate(); prepared = 0;
    }
    map.view = view; map.camera = camera; map.world_detail = world_detail;
    if (prepared && prepared_frame == dev.frames) return camera;
    int reset = prepared && dev.frames < prepared_frame;
    uint64_t signature = cover_signature();
    if (reset || !signature) cover_invalidate();
    if (signature != scene_signature) {
        /* Published heights contain no guest pointers. Keep shelter while
         * restarting the cursor against the newly resident scenery. */
        back.valid = 0; build.active = 0; scene_dirty = 1;
    }
    float x = MF32(camera+0x40), y = MF32(camera+0x44);
    if (!isfinite(x) || !isfinite(y)) { cover_invalidate(); prepared = 0; return camera; }
    uint64_t now = cover_clock();
    uint64_t frames = prepared && !reset ? dev.frames-prepared_frame : 0;
    float dx = frames ? (x-previous_x)/frames : 0, dy = frames ? (y-previous_y)/frames : 0;
    if (frames && now > previous_time) {
        double dt = (now-previous_time)*1e-9/frames;
        frame_seconds = fmin(1.0/15,fmax(1.0/240,dt));
    }
    previous_time = now; previous_x = x; previous_y = y;
    scene_signature = signature; prepared_frame = dev.frames; prepared = 1;
    if (!signature || (sliced && slice_frame == dev.frames)) return camera;
    float distance = hypotf(dx,dy);
    /* Treat a jump as a teleport, rather than predicting from it. */
    if (distance >= 8) dx = dy = distance = 0;
    build_travel += distance*frames;
    int direction_changed = dx*build_dx+dy*build_dy < .5f*distance*hypotf(build_dx,build_dy);
    double edge = cover_edge_frames(&map,x,y,dx,dy);
    double duration = fmax((double)last_build_frames,(double)last_work_ns/150000);
    if (!build.active && !scene_dirty && map.valid && edge > 1.5*duration) return camera;
    /* Avoid a burst of short refreshes after initialization or a speed
     * change. The adaptive slice can still finish within the remaining
     * 14 m when a build starts 30 m from an unpredicted first origin.
     * A turn over 60 degrees can consume the previous forward lead before
     * then, so it bypasses the spacing guard. */
    if (!build.active && !scene_dirty && cover_contains(&map,x,y) && !direction_changed && build_travel < 30) return camera;
    sliced = 1; slice_frame = dev.frames;
    uint64_t start = cover_clock();
    if (!build.active) {
        float lead = distance > 0 ? fminf(40,distance/frame_seconds*.6f)/distance : 0;
        back.valid = 0;
        if (!cover_start(&back,x+dx*lead,y+dy*lead,dev.frames)) return camera;
        back.view = view; back.camera = camera; back.world_detail = world_detail;
        build = (CoverBuild){.active=1,.phase=COVER_CLEAR,.section=M32(0x009AA478u)};
        build_work_ns = 0; scene_dirty = 0; build_travel = 0; build_dx = dx; build_dy = dy;
    }
    /* Normal slices are 0.15 ms. Borrow up to 0.5 ms only when the
     * estimated remaining work would miss the front's particle-safe edge.
     * A missing front needs the first complete map as soon as possible. */
    /* Scenery work can grow as new roofs enter the footprint. Keep a
     * reserve beyond the last completed build, and never let an overrun
     * collapse the estimate to one tiny slice. */
    double remaining = fmax(1.5*last_work_ns-build_work_ns,(double)build_work_ns);
    uint64_t budget = 150000;
    if (edge < 1.5*remaining/150000+2) {
        budget = (uint64_t)fmin(475000,fmax(150000,remaining/fmax(1,edge/1.5-2)));
    }
    /* No front map at all (world entry, camera cut, detail change): rain
     * would show under a roof until the first map exists, so finish it in
     * this frame or the next. One bounded catch-up at a cut, never in motion. */
    if (!map.valid) budget = 8000000;
    /* Reserve 25 us below the 0.5 ms ceiling for a bounded batch.
     * Four bounded steps amortize the clock read; the generous cap is only
     * a stalled-clock guard, never the normal per-frame work limit. */
    for (unsigned steps = 0; build.active && steps < 1048576; ++steps) {
        if (!(steps&3) && cover_clock()-start >= budget) break;
        cover_step();
    }
    uint64_t elapsed = cover_clock()-start;
    build_work_ns += elapsed;
    if (!build.active && map.valid) last_work_ns = build_work_ns;
    if (diag_on) {
        double ms = elapsed*1e-6;
        ++diag_slices; diag_build_ms += ms; diag_max_ms = fmax(diag_max_ms,ms);
    }
    return camera;
}

/* The one guest hook owns the precipitation scope. Integration with the
 * existing Rain::Render wrapper rejects particles only for that submission;
 * neither particle simulation nor authored-zone flags/counts are rewritten. */
void f_00758100_orig(Cpu *c);
void f_00758100(Cpu *c) {
    if (!rain_cover_on()) { f_00758100_orig(c); return; }
    uint32_t saved = rain_scope; int old = curtain_scope;
    rain_scope = c->ecx; curtain_scope = 0;
    prepare(rain_scope);
    f_00758100_orig(c);
    rain_scope = saved; curtain_scope = old;
    if (diag_on) {
        static uint64_t last;
        if (dev.frames-last >= 60) {
            last = dev.frames;
            diag_event("rain cover: map %llu triangles %llu particles hidden/tested %llu/%llu curtain %llu/%llu origin %.1f %.1f rebuilds %llu slices %llu build ms mean/max %.4f/%.4f uncovered %llu build frames %llu reset uncovered %llu edge uncovered %llu camera %.1f %.1f",
                (unsigned long long)map.generation,(unsigned long long)map.triangles,
                (unsigned long long)hidden,(unsigned long long)tested,
                (unsigned long long)curtain_hidden,(unsigned long long)curtain_tested,map.x,map.y,
                (unsigned long long)diag_rebuilds,(unsigned long long)diag_slices,
                diag_frames ? diag_build_ms/diag_frames : 0,diag_max_ms,
                (unsigned long long)diag_uncovered,(unsigned long long)last_build_frames,
                (unsigned long long)diag_reset_uncovered,(unsigned long long)diag_edge_uncovered,previous_x,previous_y);
            diag_slices = diag_rebuilds = diag_frames = diag_uncovered = 0;
            diag_reset_uncovered = diag_edge_uncovered = 0; diag_build_ms = diag_max_ms = 0;
            tested = hidden = curtain_tested = curtain_hidden = 0;
        }
    }
}
void rain_cover_render(Cpu *c, void (*render)(Cpu *)) {
    if (!rain_cover_on()) { render(c); return; }
    uint32_t rain = c->ecx;
    uint32_t camera = prepare(rain);
    if (diag_on && M32(rain+0x284) && diag_draw_frame != dev.frames) {
        diag_draw_frame = dev.frames;
        if (!camera || !cover_contains(&map,MF32(camera+0x40),MF32(camera+0x44))) {
            ++diag_uncovered;
            if (diag_reset_pending) ++diag_reset_uncovered;
            if (map.valid) ++diag_edge_uncovered;
        }
    }
    unsigned n = M32(rain+0x33d8), count = M32(rain+0x284);
    if (n > 350 || !map.valid) { render(c); curtain_scope = rain_scope != 0; return; }
    uint8_t states[350];
    uint32_t current = M32(rain+0x298);
    if (current > 1) { render(c); curtain_scope = rain_scope != 0; return; }
    float w[4][4]; memcpy(w,G+rain+0x3610,64);
    for (unsigned i = 0; i < n; ++i) {
        uint32_t state = rain+0x2e60+4*i;
        states[i] = M8(state);
        if (states[i]) continue; // Rain::Update remains authoritative
        float local[3], p[3]; memcpy(local,G+rain+(current+0x2a+2*i)*16,12);
        for (int k = 0; k < 3; ++k) p[k] = w[3][k]+local[0]*w[0][k]+local[1]*w[1][k]+local[2]*w[2][k];
        if (diag_on) ++tested;
        if (rain_cover_sample(&map,p[0],p[1],p[2])) {
            M8(state) = 2; if (M32(rain+0x284)) --M32(rain+0x284);
            if (diag_on) ++hidden;
        }
    }
    render(c);
    for (unsigned i = 0; i < n; ++i) M8(rain+0x2e60+4*i) = states[i];
    M32(rain+0x284) = count;
    curtain_scope = rain_scope != 0;
}
int rain_cover_curtain(void) {
    if (!rain_cover_on()) return 0;
    if (!rain_scope || !curtain_scope || !map.valid) return 0;
    /* 0074A5D0 submits one ePoly per curtain streak, already in world space.
     * Sample its centre at the lowest corner so a streak crossing the roof
     * cannot extend into the sheltered volume. */
    float x = 0, y = 0, z = INFINITY;
    for (unsigned i = 0; i < 4; ++i) {
        uint32_t p = rain_scope+0x34d0+16*i;
        x += MF32(p)*.25f; y += MF32(p+4)*.25f; z = fminf(z,MF32(p+8));
    }
    int covered = rain_cover_sample(&map,x,y,z);
    if (diag_on) { ++curtain_tested; curtain_hidden += covered; }
    return covered;
}
int rain_cover_lens(uint32_t rain) {
    if (!rain_cover_on()) return 0;
    if (rain && M32(rain+0x234)) return 1;
    uint32_t camera = prepare(rain);
    return camera && rain_cover_sample(&map,MF32(camera+0x40),MF32(camera+0x44),MF32(camera+0x48));
}
void rain_cover_constants(float out[4]) {
    memset(out,0,16);
    if (!rain_cover_on()) return;
    uint32_t target = M32(0x00982A20u), vid = target ? M32(target+4) : 0;
    uint32_t rain = vid == 1 || vid == 2 ? M32(0x00919648u+vid*0x70) : 0;
    /* The mirror's draws have no rain scope: prepare(0) would drop the main view's map every frame. */
    if (rain) prepare(rain);
    if (!map.valid) return;
    out[0] = map.x-64; out[1] = map.y-64; out[2] = 1.0f/128;
    out[3] = rain && M32(rain+0x234) ? 2 : 1;
}
id<MTLTexture> rain_cover_texture(void) {
    if (!rain_cover_on() || !map.valid || !mtl) return nil;
    /* A new immutable texture per refresh avoids overwriting a map that a
     * preceding frame's command buffer still samples. Metal retains it. */
    if (gpu_generation != map.generation) {
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR32Float width:512 height:512 mipmapped:NO];
        d.storageMode = MTLStorageModeShared; d.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> t = [mtl newTextureWithDescriptor:d];
        if (!t) return nil;
        [t replaceRegion:MTLRegionMake2D(0,0,512,512) mipmapLevel:0 withBytes:map.height bytesPerRow:512*sizeof(float)];
        gpu_map = t; gpu_generation = map.generation;
    }
    return gpu_map;
}

int rain_cover_ripple(uint64_t hash) {
    if (!rain_cover_on() || !roadfilm_shader_hash(hash)) return 0;
    float rc[4]; rain_cover_constants(rc);
    return rc[3] && rain_cover_texture() != nil;
}
static int cover_edit(char **source, const char *from, const char *to) {
    char *at = strstr(*source,from), *out;
    if (!at || asprintf(&out,"%.*s%s%s",(int)(at-*source),*source,to,at+strlen(from)) < 0) return 0;
    free(*source); *source = out; return 1;
}
char *rain_cover_shader(const uint32_t *tokens, uint32_t n, const SmResult *r, int *id, int film) {
    char *base = film && r->shader_type == SM_PIXEL ? mat_roadfilm_shader(tokens,n,r,id) : mat_replace(tokens,n,r,id);
    if (!base) return NULL; // requires the existing ripple material
    int ok;
    const char *field = "    float3 rain_world [[user(rain_world)]];\n";
    char replacement[512];
    if (r->shader_type == SM_VERTEX) {
        snprintf(replacement,sizeof replacement,"struct SmVSStageOut {\n%s",field);
        ok = cover_edit(&base,"struct SmVSStageOut {\n",replacement);
        ok = ok && cover_edit(&base,"vertex SmVSStageOut sm_main(","vertex SmVSStageOut sm_main(constant float4 *rc_world [[buffer(22)]], ");
        ok = ok && cover_edit(&base,"    return r;\n",
            "    r.rain_world=float3(dot(float4(si.v[0].xyz,1),rc_world[0]),dot(float4(si.v[0].xyz,1),rc_world[1]),dot(float4(si.v[0].xyz,1),rc_world[2]));\n    return r;\n");
    } else {
        snprintf(replacement,sizeof replacement,"struct SmPSStageIn {\n%s",field);
        ok = cover_edit(&base,"struct SmPSStageIn {\n",replacement);
        ok = ok && cover_edit(&base,"fragment SmPSStageOut sm_main(",
            "fragment SmPSStageOut sm_main(constant float4 &rc [[buffer(8)]], texture2d<float> rc_map [[texture(10)]], ");
        ok = ok && cover_edit(&base,"    si.tex[2] = mat_ripple(pi.texcoord2, mk, texR);",
            "    MatConsts sheltered=mk;\n"
            "    float2 rc_uv=(pi.rain_world.xy-rc.xy)*rc.z;\n"
            "    if(rc.w>1 || (rc.w>0 && all(rc_uv>=0) && all(rc_uv<1) && rc_map.read(uint2(rc_uv*512)).r > pi.rain_world.z+0.35f)) sheltered.k[0].x=0;\n"
            "    si.tex[2] = mat_ripple(pi.texcoord2, sheltered, texR);");
        /* mat_ripple accepts a constant address-space reference; the local
         * copy must use thread space, which also accepts the baseline copy. */
        ok = ok && cover_edit(&base,"constant MatConsts &m, texture2d_array<float> tr", "thread const MatConsts &m, texture2d_array<float> tr");
    }
    if (!ok) { free(base); return NULL; }
    return base;
}
void rain_cover_bind(id<MTLRenderCommandEncoder> encoder, const SmResult *vs) {
    float rc[4]; rain_cover_constants(rc);
    id<MTLTexture> texture = rain_cover_texture();
    if (!texture) rc[3] = 0;
    float world[4][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0},{0,0,0,1}};
    int reg = -1;
    for (int i = 0; i < vs->n_constants; ++i) {
        const SmConstant *k = &vs->constants[i];
        if (k->set == 2 && k->count >= 4 && !strcmp(k->name,"WorldViewProj") && k->reg <= 252) reg = k->reg;
    }
    uint32_t platform = map.view ? M32(map.view) : 0;
    if (platform && reg >= 0) {
        double a[4][8];
        for (int row = 0; row < 4; ++row) for (int col = 0; col < 8; ++col)
            a[row][col] = col < 4 ? MF32(platform+0x80+row*16+col*4) : (col-4 == row);
        int valid = 1;
        for (int col = 0; col < 4; ++col) {
            int pivot = col;
            for (int row = col+1; row < 4; ++row) if (fabs(a[row][col]) > fabs(a[pivot][col])) pivot = row;
            if (!isfinite(a[pivot][col]) || fabs(a[pivot][col]) < 1e-12) { valid = 0; break; }
            for (int k = 0; k < 8; ++k) { double t = a[pivot][k]; a[pivot][k] = a[col][k]; a[col][k] = t; }
            double scale = a[col][col]; for (int k = 0; k < 8; ++k) a[col][k] /= scale;
            for (int row = 0; row < 4; ++row) if (row != col) {
                double f = a[row][col]; for (int k = 0; k < 8; ++k) a[row][k] -= f*a[col][k];
            }
        }
        if (valid) {
            for (int row = 0; row < 4; ++row) for (int col = 0; col < 4; ++col) {
                double value = 0;
                for (int k = 0; k < 4; ++k) value += dev.vsc[reg+k][row]*a[k][col+4];
                world[col][row] = (float)value;
            }
        } else rc[3] = 0;
    } else rc[3] = 0;
    [encoder setVertexBytes:world length:sizeof world atIndex:22];
    [encoder setFragmentBytes:rc length:sizeof rc atIndex:8];
    [encoder setFragmentTexture:texture atIndex:10];
}
#endif
