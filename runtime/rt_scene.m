// RT1: immutable rigid packets, object-space geometry cache and ordered native AS builds.
#include "game_select.h"
#include "d3d9_internal.h"
#include "rt_scene.h"
#include "rt_native.h"
#include <float.h>
#include <stdlib.h>

#if NFS_GAME == NFS_GAME_PRIVATE
#include "game_rt_scene.h"
#else
static int rt_scene_guest_active(void) { return M32(0x00925E90u) == 6; }
static int rt_scene_guest_view(RTSceneView *view) {
    if (!rt_scene_guest_active()) return 0;
    uint32_t target = M32(0x00982A20u), source = 0x00919650u;
    // Only the primary view; mirrors, split secondary views and captures fail closed.
    if (!target || M32(target) || M32(target + 4) != 1 || M32(source + 0x60) != target) return 0;
    uint32_t platform = M32(source);
    if (!platform) return 0;
    view->view = 1;
    memcpy(view->vp, G + platform + 0x80, sizeof view->vp);
    return 1;
}
#endif

int rt_scene_enabled(void) {
    static int enabled = -1;
    if (enabled < 0) {
        const char *s = getenv("NFS_RT1");
        enabled = s && (!strcmp(s, "1") || !strcmp(s, "on"));
    }
    return enabled;
}
int rt_scene_supported(id<MTLDevice> device) {
    if (@available(macOS 14.0, *))
        return device.supportsRaytracing && [device supportsFamily:MTLGPUFamilyApple9];
    return 0;
}

// No guest handles or host addresses constitute resource identity. Every field
// affecting the position/index decode is in the key, independent of material.
typedef struct {
    uint64_t vb, ib, vg, ig, declaration, fvf, shader;
    uint64_t offset, stride, position, type, prims, start, base, format, up_hash;
} RTGeometryKey;
static uint64_t live_geometry_bytes, live_as_bytes, live_frame_bytes;
static uint64_t geometry_serial;
@interface RTSceneGeometry : NSObject {
@public
    RTGeometryKey key;
    id<MTLBuffer> positions, indices;
    id<MTLAccelerationStructure> blas;
    MTLPrimitiveAccelerationStructureDescriptor *descriptor;
    NSUInteger scratch_size, refit_size, bytes;
    uint64_t used;
    uint64_t serial;
    BOOL scheduled;
    BOOL accounted;
}
@end
@implementation RTSceneGeometry
- (void)dealloc {
    if (accounted) {
        __atomic_fetch_sub(&live_geometry_bytes, positions.length+indices.length, __ATOMIC_RELAXED);
        __atomic_fetch_sub(&live_as_bytes, blas.size, __ATOMIC_RELAXED);
    }
}
@end

@interface RTScenePacket : NSObject {
@public
    RTSceneGeometry *geometry;
    RTSceneView view;
    float world[16];                    // row-vector affine local-to-world
    uint32_t cull, material;
}
@end
@implementation RTScenePacket
@end

@interface RTSceneBuild : NSObject {
@public
    RTSceneGeometry *geometry;
    id<MTLBuffer> scratch;
}
@end
@implementation RTSceneBuild
@end

@interface RTSceneFrame : NSObject {
@public
    NSArray<RTScenePacket *> *packets;
    NSArray<RTSceneBuild *> *builds;
    NSArray<id<MTLAccelerationStructure>> *blas;
    id<MTLBuffer> instances, scratch;
    id<MTLAccelerationStructure> tlas;
    MTLInstanceAccelerationStructureDescriptor *descriptor;
    id<MTLCounterSampleBuffer> counters;
    uint64_t frame, capture_ns, geometry_bytes, as_bytes, scratch_bytes;
    uint64_t transient_bytes;
    double tick_ns;
    RTSceneStats stats;
}
@end
@implementation RTSceneFrame
- (void)dealloc { __atomic_fetch_sub(&live_frame_bytes, transient_bytes, __ATOMIC_RELAXED); }
@end

static NSMutableDictionary<NSData *, RTSceneGeometry *> *cache;
static NSMutableArray<RTScenePacket *> *packets;
static NSMutableSet<NSData *> *placements;
static RTSceneStats stats;
static uint64_t epoch = 1, packet_frame = UINT64_MAX, last_capture_ns;
static id<MTLAccelerationStructure> last_tlas;
static NSArray<id<MTLAccelerationStructure>> *last_blas;
static FILE *timings;
static uint64_t clock_host, clock_gpu;
static double gpu_tick_ns;
enum { RT_MAX_VERTICES = 2 * 1024 * 1024, RT_MAX_INSTANCES = 65536 };
static const uint64_t cache_limit = 256ull * 1024 * 1024;

RTSceneStats rt_scene_stats(void) {
    RTSceneStats s=stats;
    s.live_geometry_bytes=__atomic_load_n(&live_geometry_bytes,__ATOMIC_RELAXED);
    s.live_as_bytes=__atomic_load_n(&live_as_bytes,__ATOMIC_RELAXED);
    s.live_frame_bytes=__atomic_load_n(&live_frame_bytes,__ATOMIC_RELAXED);
    return s;
}
id<MTLAccelerationStructure> rt_scene_last_tlas(void) { return last_tlas; }
NSArray<id<MTLAccelerationStructure>> *rt_scene_last_blas(void) { return last_blas; }

static void evict(NSData *key) {
    RTSceneGeometry *g = cache[key];
    stats.geometry_bytes -= g->positions.length + g->indices.length;
    stats.as_bytes -= g->blas.size;
    [cache removeObjectForKey:key];
}
void rt_scene_forget(uint64_t resource_id) {
    if (!rt_scene_enabled() || !resource_id || !cache) return;
    for (NSData *key in cache.allKeys) {
        RTSceneGeometry *g = cache[key];
        if (g->key.vb == resource_id || g->key.ib == resource_id || g->key.declaration == resource_id) evict(key);
    }
    // Already-captured packets own their immutable geometry until completion.
}
void rt_scene_reset(void) {
    if (!rt_scene_enabled()) return;
    cache = nil; packets = nil; placements = nil; last_tlas = nil; last_blas = nil;
    stats.geometry_bytes = stats.as_bytes = 0;
    packet_frame = UINT64_MAX;
    ++epoch;
}

static int program(uint64_t shader) {
#if NFS_GAME == NFS_GAME_PRIVATE
    return rt_scene_private_program(shader);
#else
    // Decoded world t0 VS1.1: POSITION v0; dp4 oPos.xyzw, v0, c0..c3.
    return shader == 0x090160e8e9a32d2cull;
#endif
}

static int inverse(const float *m, double out[4][4]) {
    double a[4][8];
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 8; ++j) a[i][j] = j < 4 ? m[i*4+j] : j-4 == i;
    for (int i = 0; i < 4; ++i) {
        int p = i;
        for (int j = i+1; j < 4; ++j) if (fabs(a[j][i]) > fabs(a[p][i])) p = j;
        if (!isfinite(a[p][i]) || fabs(a[p][i]) < 1e-12) return 0;
        for (int j = 0; j < 8; ++j) { double t = a[i][j]; a[i][j] = a[p][j]; a[p][j] = t; }
        double d = a[i][i];
        for (int j = 0; j < 8; ++j) a[i][j] /= d;
        for (int k = 0; k < 4; ++k) if (k != i) {
            double s = a[k][i];
            for (int j = 0; j < 8; ++j) a[k][j] -= s * a[i][j];
        }
    }
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 4; ++j) {
        out[i][j] = a[i][j+4];
        if (!isfinite(out[i][j])) return 0;
    }
    return 1;
}
static int world_matrix(const RTSceneView *view, float world[16]) {
    double inv[4][4];
    if (!inverse(view->vp, inv)) return 0;
    // Both pinned programs dot v0 with the four uploaded columns, before the
    // translator's half-pixel/Y clip fixup. Never invert that raster fixup.
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 4; ++j) {
        double x = 0;
        for (int k = 0; k < 4; ++k) x += dev.vsc[k][i] * inv[k][j];
        if (!isfinite(x) || fabs(x) > FLT_MAX) return 0;
        world[i*4+j] = (float)x;
    }
    for (int i = 0; i < 4; ++i) if (fabsf(world[i*4+3] - (i == 3)) > 0.002f) return 0;
    double check[4][4];
    return inverse(world, check);          // reject singular scale/invalid placements
}

static uint64_t hash_bytes(uint64_t h, const void *bytes, size_t size) {
    const uint8_t *p = bytes;
    for (size_t i = 0; i < size; ++i) h = (h ^ p[i]) * 1099511628211ull;
    return h;
}
static uint32_t index_at(const uint8_t *p, unsigned size, unsigned i) {
    uint32_t x = 0;
    memcpy(&x, p + (size_t)i * size, size);
    return x;
}

static RTSceneGeometry *geometry(const RTSceneDraw *d, const RTSceneElement *pos) {
    if (d->type < 4 || d->type > 6 || !d->prims || d->prims > RT_MAX_VERTICES/3) return nil;
    uint32_t n = d->type == 4 ? 3*d->prims : d->prims+2;
    if (d->start > UINT32_MAX-n) return nil;
    Obj *vb = d->up_vertices ? NULL : obj(dev.stream[pos->stream]);
    Obj *ib = d->indexed && !d->up_indices ? obj(dev.indices) : NULL;
    Obj *decl = obj(dev.decl);
    uint32_t stride = d->up_vertices ? d->up_stride : dev.stream_stride[pos->stream];
    uint32_t offset = d->up_vertices ? 0 : dev.stream_off[pos->stream];
    if (!stride || pos->offset + 12 > stride || (!d->up_vertices && (!vb || vb->kind!=K_VB || !vb->mem[0] || !vb->resource_id))) return nil;
    if (d->indexed && !d->up_indices && (!ib || ib->kind!=K_IB)) return nil;
    uint32_t format = d->up_indices ? d->up_index_fmt : ib ? ib->fmt : 0;
    if (d->indexed && (format != 101 && format != 102)) return nil;
    unsigned isize = format == 102 ? 4 : 2;
    uint64_t index_source = d->up_indices ? d->up_indices : ib ? ib->mem[0] : 0;
    if (d->indexed && (!index_source || (!d->up_indices && (!ib->resource_id || ((uint64_t)d->start+n)*isize > ib->size)) ||
        index_source + ((uint64_t)d->start+n)*isize > UINT32_MAX)) return nil;
    const uint8_t *input_indices = d->indexed ? G + index_source + (uint64_t)d->start*isize : NULL;
    RTGeometryKey key = {.vb=vb?vb->resource_id:0, .ib=ib?ib->resource_id:0,
        .vg=vb?vb->buffer_generation:0, .ig=ib?ib->buffer_generation:0,
        .declaration=decl?decl->resource_id:0, .fvf=dev.fvf, .shader=d->shader,
        .offset=offset, .stride=stride, .position=pos->offset, .type=d->type,
        .prims=d->prims, .start=d->start, .base=(uint32_t)d->base_vertex, .format=format};
    // UP bytes have no lifetime/generation contract. Snapshot and content-key
    // them below; static/dynamic resources can use the metadata cache directly.
    BOOL up = d->up_vertices || d->up_indices;
    NSData *cache_key = up ? nil : [NSData dataWithBytes:&key length:sizeof key];
    RTSceneGeometry *found = cache_key ? cache[cache_key] : nil;
    if (found) { found->used = dev.frames; ++stats.cache_hits; return found; }
    uint32_t lo = UINT32_MAX, hi = 0;
    for (unsigned i = 0; i < n; ++i) {
        uint32_t v = d->indexed ? index_at(input_indices, isize, i) : (d->up_vertices ? i : d->start+i);
        if (v < lo) lo = v;
        if (v > hi) hi = v;
    }
    uint64_t count = (uint64_t)hi-lo+1;
    int64_t first = (int64_t)lo + (d->indexed ? d->base_vertex : 0);
    int64_t last = (int64_t)hi + (d->indexed ? d->base_vertex : 0);
    uint64_t source = d->up_vertices ? d->up_vertices : vb->mem[0];
    if (first < 0 || last < first || count > RT_MAX_VERTICES) return nil;
    // Bound before multiplying: a positive signed base plus a UInt32 index
    // can exceed UInt32, and multiplying that by a stride could wrap UInt64.
    uint64_t available=UINT32_MAX-source;
    if (!d->up_vertices && vb->size<available) available=vb->size;
    uint64_t tail=(uint64_t)offset+pos->offset+12;
    if (tail>available || (uint64_t)last>(available-tail)/stride) return nil;
    NSMutableData *xyz = [NSMutableData dataWithLength:(NSUInteger)count*12];
    float *points = xyz.mutableBytes;
    for (uint64_t i = 0; i < count; ++i) {
        memcpy(points+3*i, G+source+offset+(first+i)*stride+pos->offset, 12);
        for (int k = 0; k < 3; ++k) if (!isfinite(points[3*i+k])) return nil;
    }
    NSMutableData *triangles = [NSMutableData dataWithLength:(NSUInteger)d->prims*12];
    uint32_t *out = triangles.mutableBytes, nt = 0;
    for (unsigned t = 0; t < d->prims; ++t) {
        unsigned corners[3];
        if (d->type == 4) { corners[0]=3*t; corners[1]=3*t+1; corners[2]=3*t+2; }
        else if (d->type == 5) { corners[0]=t+(t&1); corners[1]=t+1-(t&1); corners[2]=t+2; }
        else { corners[0]=0; corners[1]=t+1; corners[2]=t+2; }
        uint32_t v[3];
        for (int k = 0; k < 3; ++k) v[k] = (d->indexed ? index_at(input_indices,isize,corners[k]) : (d->up_vertices ? corners[k] : d->start+corners[k])) - lo;
        if (v[0]==v[1] || v[1]==v[2] || v[2]==v[0]) continue;
        double a[3], b[3];
        for (int k = 0; k < 3; ++k) { a[k]=points[3*v[1]+k]-points[3*v[0]+k]; b[k]=points[3*v[2]+k]-points[3*v[0]+k]; }
        if (a[1]*b[2]==a[2]*b[1] && a[2]*b[0]==a[0]*b[2] && a[0]*b[1]==a[1]*b[0]) continue;
        memcpy(out+3*nt++, v, sizeof v);
    }
    if (!nt) return nil;
    triangles.length = (NSUInteger)nt*12;
    if (up) {
        key.up_hash = hash_bytes(hash_bytes(1469598103934665603ull,xyz.bytes,xyz.length),triangles.bytes,triangles.length);
        cache_key = [NSData dataWithBytes:&key length:sizeof key];
        found = cache[cache_key];
        if (found && found->positions.length == xyz.length && found->indices.length == triangles.length &&
            !memcmp(found->positions.contents,xyz.bytes,xyz.length) && !memcmp(found->indices.contents,triangles.bytes,triangles.length)) {
            found->used=dev.frames; ++stats.cache_hits; return found;
        }
    }
    if (stats.geometry_bytes + stats.as_bytes + xyz.length + triangles.length > cache_limit) { ++stats.capacity; return nil; }
    RTSceneGeometry *g = [RTSceneGeometry new];
    if (geometry_serial==UINT64_MAX) abort();
    g->serial=++geometry_serial;
    g->key = key; g->used = dev.frames;
    g->positions = [mtl newBufferWithBytes:xyz.bytes length:xyz.length options:MTLResourceStorageModeShared];
    g->indices = [mtl newBufferWithBytes:triangles.bytes length:triangles.length options:MTLResourceStorageModeShared];
    if (!g->positions || !g->indices) return nil;
    MTLAccelerationStructureTriangleGeometryDescriptor *tri = [MTLAccelerationStructureTriangleGeometryDescriptor descriptor];
    tri.vertexBuffer=g->positions; tri.vertexStride=12; tri.vertexFormat=MTLAttributeFormatFloat3;
    tri.indexBuffer=g->indices; tri.indexType=MTLIndexTypeUInt32; tri.triangleCount=nt; tri.opaque=YES;
    g->descriptor = [MTLPrimitiveAccelerationStructureDescriptor descriptor];
    g->descriptor.geometryDescriptors = @[tri];
    MTLAccelerationStructureSizes sizes = [mtl accelerationStructureSizesWithDescriptor:g->descriptor];
    if (stats.geometry_bytes + stats.as_bytes + xyz.length + triangles.length + sizes.accelerationStructureSize > cache_limit) {
        ++stats.capacity; return nil;
    }
    g->blas = [mtl newAccelerationStructureWithSize:sizes.accelerationStructureSize];
    if (!g->blas) return nil;
    g->scratch_size=sizes.buildScratchBufferSize; g->refit_size=sizes.refitScratchBufferSize;
    g->bytes = xyz.length + triangles.length + g->blas.size;
    g->accounted=YES;
    __atomic_fetch_add(&live_geometry_bytes,xyz.length+triangles.length,__ATOMIC_RELAXED);
    __atomic_fetch_add(&live_as_bytes,g->blas.size,__ATOMIC_RELAXED);
    if (!cache) cache = [NSMutableDictionary new];
    if (cache[cache_key]) evict(cache_key);
    cache[cache_key]=g;
    stats.geometry_bytes += xyz.length+triangles.length; stats.as_bytes += g->blas.size;
    return g;
}

static void capture(const RTSceneDraw *d) {
    RTSceneView view = {.frame=dev.frames, .target_generation=epoch};
    if (!rt_scene_guest_view(&view) || dev.rs[7]!=1 || !(dev.rs[168]&7)) { ++stats.excluded; return; }
    Obj *target = obj(dev.rt[0]);
    if (!target || !target->resource_id) { ++stats.excluded; return; }
    view.target = target ? target->resource_id : 0;
    uint32_t flags=d->unsupported;
    if (dev.rs[15]) flags |= RT_SCENE_ALPHA;
    if (dev.rs[27]) flags |= RT_SCENE_BLEND;
    RTSceneElement position={0};
    const RTSceneElement *pos=NULL;
    for (unsigned i=0; i<d->element_count; ++i) {
        // The prepared renderer declaration has the same byte layout, but a
        // distinct C struct type. Copy bytes rather than aliasing that type.
        RTSceneElement e;
        memcpy(&e,(const uint8_t *)d->elements+i*sizeof e,sizeof e);
        if (e.usage==1 || e.usage==2) flags |= RT_SCENE_SKIN;
        if (!e.usage) { if (e.index || pos) flags |= RT_SCENE_MORPH; else { position=e; pos=&position; } }
        if (e.usage==9) { ++stats.bad_geometry; return; } // POSITIONT
    }
    stats.skinning += !!(flags&RT_SCENE_SKIN); stats.morphs += !!(flags&RT_SCENE_MORPH);
    stats.foliage += !!(flags&RT_SCENE_FOLIAGE); stats.glass += !!(flags&RT_SCENE_GLASS);
    stats.alpha_test += !!(flags&RT_SCENE_ALPHA); stats.blend += !!(flags&RT_SCENE_BLEND);
    stats.water += !!(flags&RT_SCENE_WATER);
    if (flags) return;
    if (!dev.rs[14]) { ++stats.excluded; return; }
    if (!program(d->shader)) { ++stats.unknown_program; return; }
    if (!pos || pos->type!=2 || pos->stream>=16 ||
        (!d->up_vertices && dev.stream_freq[pos->stream]!=1)) { ++stats.bad_geometry; return; }
    float world[16];
    if (!world_matrix(&view,world)) { ++stats.bad_transform; return; }
    if (packet_frame!=view.frame) {
        packets=[NSMutableArray new]; placements=[NSMutableSet new]; packet_frame=view.frame;
    }
    if (packets.count>=RT_MAX_INSTANCES) { ++stats.capacity; return; }
    RTSceneGeometry *g=geometry(d,pos);
    if (!g) { ++stats.bad_geometry; return; }
    // One instance per geometry placement, even when several opaque materials
    // submit the same range. Membership and source bytes stay camera-visible only.
    NSMutableData *placement=[NSMutableData dataWithBytes:&g->serial length:sizeof g->serial];
    [placement appendBytes:world length:sizeof world];
    [placement appendBytes:&view length:sizeof view];
    uint32_t cull=dev.rs[22]; [placement appendBytes:&cull length:sizeof cull];
    if ([placements containsObject:placement]) return;
    [placements addObject:placement];
    RTScenePacket *p=[RTScenePacket new]; p->geometry=g; p->view=view; p->cull=cull; p->material=flags;
    memcpy(p->world,world,sizeof world); [packets addObject:p];
    ++stats.instances;
}
void rt_scene_capture(const RTSceneDraw *draw) {
    if (!rt_scene_enabled()) return;
    uint64_t begin=host_ticks();
    ++stats.captures;
    if (rt_scene_supported(mtl)) capture(draw); else ++stats.excluded;
    stats.capture_ns += host_ticks()-begin;
}

static void encode(id<MTLCommandBuffer> command, const void *bytes, size_t size, NSArray *resources) {
    (void)bytes; (void)size;
    RTSceneFrame *f=resources[0];
    MTLAccelerationStructurePassDescriptor *pass=[MTLAccelerationStructurePassDescriptor accelerationStructurePassDescriptor];
    if (f->counters) {
        pass.sampleBufferAttachments[0].sampleBuffer=f->counters;
        pass.sampleBufferAttachments[0].startOfEncoderSampleIndex=0;
        pass.sampleBufferAttachments[0].endOfEncoderSampleIndex=1;
    }
    id<MTLAccelerationStructureCommandEncoder> e=[command accelerationStructureCommandEncoderWithDescriptor:pass];
    e.label=@"RT1 BLAS and TLAS";
    for (RTSceneBuild *b in f->builds)
        [e buildAccelerationStructure:b->geometry->blas descriptor:b->geometry->descriptor scratchBuffer:b->scratch scratchBufferOffset:0];
    // BLAS references in instance descriptors are indirect GPU dependencies.
    for (id<MTLAccelerationStructure> b in f->blas) [e useResource:b usage:MTLResourceUsageRead];
    [e buildAccelerationStructure:f->tlas descriptor:f->descriptor scratchBuffer:f->scratch scratchBufferOffset:0];
    [e endEncoding];
    if (timings) [command addCompletedHandler:^(id<MTLCommandBuffer> cb) {
        double gpu=-1;
        uint64_t ticks=0;
        NSData *data=f->counters ? [f->counters resolveCounterRange:NSMakeRange(0,2)] : nil;
        if (data.length==2*sizeof(MTLCounterResultTimestamp)) {
            const MTLCounterResultTimestamp *t=data.bytes;
            if (t[0].timestamp && t[0].timestamp!=MTLCounterErrorValue && t[1].timestamp!=MTLCounterErrorValue && t[1].timestamp>=t[0].timestamp) {
                ticks=t[1].timestamp-t[0].timestamp;
                if (f->tick_ns>0) gpu=ticks*f->tick_ns/1e6;
            }
        }
        flockfile(timings);
        fprintf(timings,"frame=%llu instances=%lu cold=%lu warm=%lu capture_ms=%.6f as_gpu_ms=%.6f as_gpu_ticks=%llu tick_ns=%.9g geometry_bytes=%llu as_bytes=%llu scratch_bytes=%llu live_geometry_bytes=%llu live_as_bytes=%llu live_frame_bytes=%llu skin=%llu morph=%llu foliage=%llu glass=%llu alpha=%llu blend=%llu unknown=%llu invalid=%llu excluded=%llu capacity=%llu status=%lu\n",
            f->frame,(unsigned long)f->packets.count,(unsigned long)f->builds.count,
            (unsigned long)(f->blas.count-f->builds.count),f->capture_ns/1e6,gpu,ticks,f->tick_ns,
            f->geometry_bytes,f->as_bytes,f->scratch_bytes,f->stats.live_geometry_bytes,f->stats.live_as_bytes,
            f->stats.live_frame_bytes,f->stats.skinning,f->stats.morphs,
            f->stats.foliage,f->stats.glass,f->stats.alpha_test,f->stats.blend,
            f->stats.unknown_program,f->stats.bad_geometry+f->stats.bad_transform,
            f->stats.excluded,f->stats.capacity,(unsigned long)cb.status);
        fflush(timings); funlockfile(timings);
    }];
}

void rt_scene_flush(id<MTLCommandBuffer> command) {
    if (!rt_scene_enabled()) return;
    uint64_t capture_ns=stats.capture_ns-last_capture_ns;
    last_capture_ns=stats.capture_ns;
    last_tlas=nil; last_blas=nil;
    if (!rt_scene_guest_active()) { rt_scene_reset(); return; }
    for (NSData *key in cache.allKeys) if (dev.frames-cache[key]->used>120) evict(key);
    if (diag_on && !timings && diag_dir) {
        char path[1024]; snprintf(path,sizeof path,"%s/rt1.log",diag_dir); timings=fopen(path,"w");
    }
    if (!packets.count && timings) {
        flockfile(timings);
        fprintf(timings,"frame=%llu instances=0 cold=0 warm=0 capture_ms=%.6f as_gpu_ms=0 skin=%llu morph=%llu foliage=%llu glass=%llu alpha=%llu blend=%llu unknown=%llu invalid=%llu excluded=%llu capacity=%llu\n",
            dev.frames,capture_ns/1e6,stats.skinning,stats.morphs,stats.foliage,stats.glass,
            stats.alpha_test,stats.blend,stats.unknown_program,stats.bad_geometry+stats.bad_transform,stats.excluded,stats.capacity);
        fflush(timings); funlockfile(timings);
    }
    if (!rt_scene_supported(mtl) || !packets.count) return;
    RTSceneFrame *f=[RTSceneFrame new]; f->packets=[packets copy]; f->frame=packet_frame;
    f->capture_ns=capture_ns;
    NSMutableArray *blas=[NSMutableArray new], *builds=[NSMutableArray new];
    NSMapTable<RTSceneGeometry *,NSNumber *> *indices=[NSMapTable strongToStrongObjectsMapTable];
    f->instances=[mtl newBufferWithLength:f->packets.count*sizeof(MTLAccelerationStructureInstanceDescriptor) options:MTLResourceStorageModeShared];
    if (!f->instances) { ++stats.capacity; [packets removeAllObjects]; return; }
    MTLAccelerationStructureInstanceDescriptor *desc=f->instances.contents;
    for (NSUInteger i=0; i<f->packets.count; ++i) {
        RTScenePacket *p=f->packets[i]; RTSceneGeometry *g=p->geometry;
        NSNumber *index=[indices objectForKey:g];
        if (!index) {
            index=@(blas.count); [indices setObject:index forKey:g]; [blas addObject:g->blas];
            if (!g->scheduled) {
                RTSceneBuild *b=[RTSceneBuild new]; b->geometry=g;
                b->scratch=[mtl newBufferWithLength:MAX(g->scratch_size,1) options:MTLResourceStorageModePrivate];
                if (!b->scratch) { ++stats.capacity; [packets removeAllObjects]; return; }
                [builds addObject:b]; f->scratch_bytes+=b->scratch.length;
            }
        }
        memset(&desc[i],0,sizeof desc[i]);
        // Metal stores a column-major 4x3 affine matrix. A guest row matrix's
        // rows are exactly those four columns after transposition.
        for (int c=0; c<4; ++c) memcpy(&desc[i].transformationMatrix.columns[c],p->world+c*4,12);
        desc[i].options=MTLAccelerationStructureInstanceOptionOpaque;
        if (p->cull==1) desc[i].options|=MTLAccelerationStructureInstanceOptionDisableTriangleCulling;
        if (p->cull==2) desc[i].options|=MTLAccelerationStructureInstanceOptionTriangleFrontFacingWindingCounterClockwise;
        desc[i].mask=0xff; desc[i].accelerationStructureIndex=index.unsignedIntValue;
    }
    f->blas=[blas copy]; f->builds=[builds copy];
    f->descriptor=[MTLInstanceAccelerationStructureDescriptor descriptor];
    f->descriptor.instancedAccelerationStructures=f->blas;
    f->descriptor.instanceDescriptorBuffer=f->instances; f->descriptor.instanceCount=f->packets.count;
    MTLAccelerationStructureSizes sizes=[mtl accelerationStructureSizesWithDescriptor:f->descriptor];
    f->tlas=[mtl newAccelerationStructureWithSize:sizes.accelerationStructureSize];
    f->scratch=[mtl newBufferWithLength:MAX(sizes.buildScratchBufferSize,1) options:MTLResourceStorageModePrivate];
    if (!f->tlas || !f->scratch) { ++stats.capacity; [packets removeAllObjects]; return; }
    f->scratch_bytes+=f->scratch.length;
    f->geometry_bytes=stats.geometry_bytes+f->instances.length;
    f->as_bytes=stats.as_bytes+f->tlas.size;
    if (timings && [mtl supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary]) {
        // Calibrate against monotonic host nanoseconds across frames. Keep raw
        // GPU ticks for cold samples preceding the first valid calibration.
        MTLTimestamp cpu,gpu; uint64_t before=host_ticks();
        [mtl sampleTimestamps:&cpu gpuTimestamp:&gpu];
        uint64_t host=before+(host_ticks()-before)/2;
        if (clock_host && host-clock_host>=1000000 && gpu>clock_gpu)
            gpu_tick_ns=(double)(host-clock_host)/(double)(gpu-clock_gpu);
        if (!clock_host || host-clock_host>=500000000) { clock_host=host; clock_gpu=gpu; }
        f->tick_ns=gpu_tick_ns;
        for (id<MTLCounterSet> set in mtl.counterSets) if ([set.name isEqualToString:MTLCommonCounterSetTimestamp]) {
            MTLCounterSampleBufferDescriptor *d=[MTLCounterSampleBufferDescriptor new];
            d.counterSet=set; d.storageMode=MTLStorageModeShared; d.sampleCount=2;
            f->counters=[mtl newCounterSampleBufferWithDescriptor:d error:nil]; break;
        }
    }
    f->transient_bytes=f->instances.length+f->scratch_bytes+f->tlas.size;
    __atomic_fetch_add(&live_frame_bytes,f->transient_bytes,__ATOMIC_RELAXED);
    f->stats=rt_scene_stats();
    // Frame/packets/AS/descriptors/scratch and source snapshots are retained by
    // rt_native_encode through GPU completion in both command paths.
    rt_native_encode(command,encode,NULL,0,@[f]);
    for (RTSceneBuild *b in builds) b->geometry->scheduled=YES;
    stats.blas_builds+=builds.count; ++stats.tlas_builds; stats.scratch_bytes=f->scratch_bytes;
    last_tlas=f->tlas; last_blas=f->blas;
    [packets removeAllObjects]; [placements removeAllObjects];
    // Bound resident static geometry; no unproven offscreen placement survives
    // into TLAS membership. Retired cache storage may still be held by the GPU.
}
