#include <assert.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <time.h>
#include "render_internal.h"
#include "daynight_stream.h"

#define D3D_OK 0
#define D3DERR_INVALIDCALL 0x8876086Cu

uint8_t *G;
Device dev;
id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
id<MTLCommandBuffer> cmd;
id<MTLRenderCommandEncoder> enc;
uint32_t q_active;
int diag_on;
const char *diag_dir;
uint64_t frame_serial;
static uint64_t stat_q_issued;
static int visibility_active, lamp_lookups, lamp_calls;
static int64_t live_buf, live_buf_bytes, live_tex, live_tex_bytes, live_rt, live_rt_bytes;
static uint32_t top = 0x10000;
static unsigned objects_live;
static struct { uint64_t glass_stale_mid; } ad1;
static Obj *obj_lookup_for_lamps(uint32_t guest) { ++lamp_lookups; return obj(guest); }
static id<MTLTexture> lock_target;
static id<MTLBuffer> ring;

uint32_t gmalloc(uint32_t n) {
    uint32_t p = top + 64;
    top = p + n;
    assert(top < 0x400000);
    M32(p - 4) = n;
    objects_live += n == 16;
    return p;
}

void gfree(uint32_t address) {
    if (address) {
        assert(M32(address - 4));
        objects_live -= M32(address - 4) == 16;
        M32(address - 4) = 0;
    }
}

uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }
int diag_frame_wanted(uint64_t frame, const char *name) { return frame == 7 && !strcmp(name, "BOOT_DIAG_SHOTS"); }

static void envcube_forget(Obj *o) { (void)o; }
void mat_bone_reset(void) {}
void render_panes_changed(Obj *o) { (void)o; }
void msaa_forget(RenderTex *r) { (void)r; }
void drop_ms_texture(RenderTex *r) { (void)r; }
void msaa_resolve(RenderTex *r) { (void)r; abort(); }
RenderTex *surface_rt(uint32_t guest) { (void)guest; return NULL; }
uint32_t render_msaa_count(uint32_t type, uint32_t quality) { assert(!type && !quality); return 1; }
void end_pass(void) { assert(!enc); }
void ensure_cmd(void) { if (!cmd) cmd = rt_command_buffer(); }

id<MTLTexture> surface_target(uint32_t guest, NSUInteger *level, NSUInteger *slice) {
    assert(obj(guest) && obj(guest)->kind == K_SURFACE);
    *level = *slice = 0;
    return lock_target;
}

@interface VisibilitySpy : NSObject
@end
@implementation VisibilitySpy
- (void)setVisibilityResultMode:(MTLVisibilityResultMode)mode offset:(NSUInteger)offset {
    (void)offset;
    visibility_active = mode != MTLVisibilityResultModeDisabled;
}
@end
void query_segment(void) { [enc setVisibilityResultMode:MTLVisibilityResultModeCounting offset:0]; }
static void query_test_ab(void) {}

int mat_glass_uv1(const uint8_t *bytes, uint32_t size, const float **y, uint32_t *matched) {
    (void)bytes; (void)size;
    *y = NULL;
    *matched = 0;
    return -1;
}

static void *ring_alloc(uint32_t bytes, uint32_t *off, id<MTLBuffer> __unsafe_unretained *buffer) {
    assert(bytes == 120);
    *off = 0;
    *buffer = ring;
    return ring.contents;
}

static id<MTLBuffer> gpu_buf(Obj *vb) { assert(vb); return nil; }

void daynight_stream_bind(id<MTLRenderCommandEncoder> encoder, id<MTLDevice> device,
                          Obj *vb, id<MTLBuffer> identity, uint32_t stride, uint32_t offset,
                          uint64_t shader_hash, uint64_t frame) {
    (void)encoder; (void)device; (void)vb; (void)identity; (void)stride;
    (void)offset; (void)shader_hash; (void)frame;
    ++lamp_calls;
}

#include "render_audit_fragments.inc"

static uint32_t invoke(int (*method)(Cpu *), const uint32_t *args, size_t n) {
    Cpu cpu = {.esp = 0x8000};
    memcpy(G + cpu.esp + 4, args, n * sizeof(*args));
    assert(method(&cpu));
    return cpu.eax;
}

static double screenshot_difference(void) {
    enum { width = 19, height = 7, pitch = width * 4 };
    MTLTextureDescriptor *desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                   width:width height:height mipmapped:NO];
    desc.storageMode = MTLStorageModeShared;
    id<MTLTexture> source = [mtl newTextureWithDescriptor:desc];
    id<MTLTexture> reference = [mtl newTextureWithDescriptor:desc];
    desc.storageMode = MTLStorageModePrivate;
    lock_target = [mtl newTextureWithDescriptor:desc];
    assert(source && reference && lock_target);
    uint8_t pattern[height][pitch];
    for (unsigned y = 0; y < height; ++y)
        for (unsigned x = 0; x < width; ++x) {
            pattern[y][x * 4] = 55 + x;
            pattern[y][x * 4 + 1] = 85 + y;
            pattern[y][x * 4 + 2] = 115 - x - y;
            pattern[y][x * 4 + 3] = 255;
        }
    MTLRegion region = MTLRegionMake2D(0, 0, width, height);
    [source replaceRegion:region mipmapLevel:0 withBytes:pattern bytesPerRow:pitch];
    assert(invoke(v_CreateRenderTarget, (uint32_t[]){0, width, height, 21, 0, 0, 1, 0x200, 0}, 9) == D3D_OK);
    uint32_t guest = M32(0x200), guest_pitch;
    uint32_t mem = surface_mem(obj(guest), &guest_pitch);
    assert(guest_pitch == pitch);
    memset(G + mem, 165, height * pitch);

    // Mirror the retail same-frame StretchRect into the private surface. Capture
    // a second copy of the same frame independently, in the same command buffer.
    ensure_cmd();
    id<MTLBlitCommandEncoder> blit = [cmd blitCommandEncoder];
    for (id<MTLTexture> target in @[lock_target, reference])
        [blit copyFromTexture:source sourceSlice:0 sourceLevel:0 sourceOrigin:region.origin sourceSize:region.size
                    toTexture:target destinationSlice:0 destinationLevel:0 destinationOrigin:region.origin];
    [blit endEncoding];
    assert(invoke(s_LockRect, (uint32_t[]){guest, 0x204, 0, 0x10}, 4) == D3D_OK);
    if (cmd) { [cmd commit]; [cmd waitUntilCompleted]; cmd = nil; }
    uint8_t expected[height][pitch];
    [reference getBytes:expected bytesPerRow:pitch fromRegion:region mipmapLevel:0];
    assert(!memcmp(pattern, expected, sizeof expected));
    assert(M32(0x204) == pitch && M32(0x208) == mem);
    unsigned difference = 0;
    for (unsigned y = 0; y < height; ++y)
        for (unsigned x = 0; x < width; ++x)
            for (unsigned c = 0; c < 3; ++c)
                difference += abs(G[mem + y * pitch + x * 4 + c] - expected[y][x * 4 + c]);
    assert(invoke(m_Release, &guest, 1) == 0);
    return (double)difference / (width * height * 3);
}

static unsigned padding_from_guest(void) {
    ring = [mtl newBufferWithLength:120 options:MTLResourceStorageModeShared];
    memset(ring.contents, 1, ring.length);
    uint32_t vertices = gmalloc(120);
    memset(G + vertices, 42, 96);
    memset(G + vertices + 96, 255, 24);
    upload_vertices(vertices, 24, 4);
    uint8_t *bytes = ring.contents;
    unsigned nonzero = 0;
    for (unsigned i = 0; i < 96; ++i) assert(bytes[i] == 42);
    for (unsigned i = 96; i < 120; ++i) nonzero += bytes[i] != 0;
    gfree(vertices);
    return nonzero;
}

static unsigned queries_after_releases(int *active, int *encoder_active) {
    unsigned before = objects_live;
    for (unsigned i = 0; i < 2048; ++i) {
        Obj *query = new_obj(K_QUERY, 0);
        query->qtype = 9;
        uint32_t handle = query->guest;
        if (i == 0) {
            enc = (id)[VisibilitySpy new];
            render_query_issue(query, 2);
            assert(q_active == handle && visibility_active);
            query->refs = 2;
            assert(invoke(m_Release, &handle, 1) == 1);
            assert(q_active == handle && visibility_active);
        }
        assert(invoke(m_Release, &handle, 1) == 0);
        if (i == 0) {
            *active = q_active != 0;
            *encoder_active = visibility_active;
            q_active = 0;
            enc = nil;
        }
    }
    return objects_live - before;
}

static unsigned glass_buffers_after_release(void) {
    __weak id<MTLBuffer> buffers[32] = {};
    for (unsigned i = 0; i < 32; ++i) {
        @autoreleasepool {
            Obj *vb = new_obj(K_VB, 0);
            vb->size = 36 * 4;
            vb->mem[0] = gmalloc(vb->size);
            uint32_t handle = vb->guest;
            GlassUV1 *uv = glass_uv1_for(vb);
            assert(uv && uv == glass_uv1_for(vb) && uv->buf.length == 4 * sizeof(float));
            buffers[i] = uv->buf;
            assert(invoke(m_Release, &handle, 1) == 0);
        }
    }
    unsigned live = 0;
    for (unsigned i = 0; i < 32; ++i) live += buffers[i] != nil;
    return live;
}

static unsigned off_lamp_lookups(void) {
    unsetenv("NFS_DAYNIGHT");
    unsetenv("NFS_DAYNIGHT_BAKE");
    Obj *vb = new_obj(K_VB, 0);
    dev.stream[0] = vb->guest;
    bind_lamp_stream(vb);
    assert(!lamp_calls || lamp_calls == 1);
    unsigned lookups = lamp_lookups;
    diag_on = 1;
    diag_dir = ".";
    dev.frames = 7;
    bind_lamp_stream(vb);
    assert(lamp_calls == (lookups ? 2 : 1));
    return lookups;
}

int main(void) {
    @autoreleasepool {
        mtl = MTLCreateSystemDefaultDevice();
        if (!mtl) return 3;
        queue = [mtl newCommandQueue];
        G = calloc(1, 0x400000);
        assert(queue && G);
        int query_active, encoder_active;
        unsigned queries = queries_after_releases(&query_active, &encoder_active);
        double mad = screenshot_difference();
        unsigned padding = padding_from_guest();
        unsigned glass = glass_buffers_after_release();
        unsigned lamp = off_lamp_lookups();
        printf("mad=%.2f padding=%u query_objects=%u query_active=%d encoder_active=%d glass_live=%u lamp_off=%u\n",
               mad, padding, queries, query_active, encoder_active, glass, lamp);
        free(G);
        return mad > 1 || padding || queries > 1024 || query_active || encoder_active || glass || lamp;
    }
}
