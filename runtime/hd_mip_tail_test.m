// Native replay: no game process, emulator, or reimplementation of Init/swap.
#define HD_MIP_TAIL_TEST 1
#include "uiblur_test.m"
#include "hd_tail_mode.h"

typedef struct {
    id<MTLTexture> tex, ms; uint32_t w, h, fmt; int rt;
    int64_t bytes; uint64_t used; int resolve_pass;
} RenderTex;
static id<MTLDevice> mtl;
static uint64_t frame_serial = 1;
static int64_t live_tex, live_tex_bytes, live_rt, live_rt_bytes;
static unsigned stat_renamed, stat_uploads, probe_sig;
static uint32_t bad_tex_binds;
enum { K_CUBE = 4, K_VOLUME = 5, K_SURFACE = 6, K_VB = 7, K_IB = 8, PROBE_HDTEX = 1 };
static int is_depth_fmt(uint32_t fmt) { (void)fmt; return 0; }
static int is_dxt(uint32_t fmt) { return fmt != 21; }
static uint32_t level_rows(uint32_t fmt, uint32_t h) { return fmt == 21 ? h : (h + 3) / 4; }
static MTLPixelFormat mtl_format(uint32_t fmt, int *conv, MTLTextureSwizzleChannels *sw) {
    *conv = 0; *sw = MTLTextureSwizzleChannelsMake(MTLTextureSwizzleRed, MTLTextureSwizzleGreen, MTLTextureSwizzleBlue, MTLTextureSwizzleAlpha);
    return fmt == 0x31545844 ? MTLPixelFormatBC1_RGBA : fmt == 0x33545844 ? MTLPixelFormatBC2_RGBA : MTLPixelFormatBC3_RGBA;
}
static int in_flight(uint64_t serial) { (void)serial; return 0; }
static MTLTextureDescriptor *texture_descriptor(id<MTLTexture> t) { (void)t; abort(); }
static id<MTLTexture> new_ms_texture(MTLPixelFormat f, uint32_t w, uint32_t h, uint32_t samples) {
    (void)f; (void)w; (void)h; (void)samples; abort();
}
static void probe_tint(uint32_t fmt, void *p, size_t n, int rgba) { (void)fmt; (void)p; (void)n; (void)rgba; abort(); }
static void probe_applied(unsigned p, int n) { (void)p; (void)n; abort(); }
static void convert_row(uint32_t fmt, const uint8_t *s, uint8_t *d, uint32_t w) { (void)fmt; (void)s; (void)d; (void)w; abort(); }
static struct { Obj *self; uint32_t backbuffer, autodepth; } dev;
static Obj *ref_owner(Obj *o) { return o; }
static void mat_bone_reset(void) { abort(); } // this replay releases textures, never a device
static void envcube_forget(Obj *o) { (void)o; }
static void obj_retire(Obj *o) { assert(!o->refs); }
static void render_release(Obj *o) {
    RenderTex *g = o->gpu;
    if (g) { g->tex = nil; free(g); o->gpu = NULL; }
}
#include "hd_gpu_generated.h"

int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 6);
    int drop = atoi(argv[2]), malformed = atoi(argv[5]), gpu = !strcmp(argv[4], "gpu"), skip = 0;
    if (gpu) { mtl = MTLCreateSystemDefaultDevice(); if (!mtl) skip = 1; }
    G = calloc(1, 64u << 20); assert(G);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    fseek(f, 0, SEEK_END); size_t size = (size_t)ftell(f); rewind(f);
    uint8_t *dds = malloc(size); assert(fread(dds, 1, size, f) == size); fclose(f);
    uint32_t d[32]; memcpy(d, dds, sizeof d);
    unsigned bytes = 0, finalw = 0, finalh = 0, count = 0;
    int complete = 1;
    for (unsigned repeat = 0; repeat < 32; ++repeat) {
        assert(live_allocs == 0 && !live_head); heap = 0x1000000; upload = (Uploaded){0};
        uint32_t ti = 0x20000, pi = ti + 0x100;
        memset(G + ti, 0, 0x200);
        M32(ti) = pi; memcpy(G + ti + 12, "SIGN_REPLAY", 12);
        M16(ti + 0x44) = d[4] / 4; M16(ti + 0x46) = d[3] / 4;
        M32(pi + 0x14) = d[21]; M8(ti + 0x4E) = 1;
        uint32_t pcbase = ((d[4] / 4 + 3) / 4) * ((d[3] / 4 + 3) / 4) * 8;
        M32(ti + 0x38) = M32(ti + 0x40) = pcbase;
        uint32_t pc = M32(ti + 0x70) = gmalloc(pcbase);
        memset(G + pc, 0xAB, pcbase);
        M32(0x982AE0) = drop; M32(0x982BDC) = 0x30000; M32(0x30000) = 0x31000;
        M32(0x3105C) = 1; M32(0x3304C) = 2; M32(0x33050) = 3; M32(0x987EC4) = 0x987EC0;
        memcpy(G + 0x8AFFEC, "SFX_TRACKBARRIER", 17);
        HdTex h = {.path = argv[1], .ww = d[4], .hh = d[3], .mips = d[7], .fmt = d[21]};
        Cpu c = {.ecx = ti, .esp = 0xF00000}; replay_swap(&c, &h, 0);
        assert(c.esp == 0xF00004 && M32(ti + 0x70) == pc && M8(ti + 0x4E) == 1);
        if (malformed) {
            assert(upload.w == d[4] / 4 && upload.h == d[3] / 4 && upload.levels == 1);
            assert(!memcmp(G + upload.mem[0], G + pc, pcbase));
        } else {
            assert(upload.w == d[4] >> drop && upload.h == d[3] >> drop);
            complete &= upload.levels == d[7] - drop;
            size_t off = 128; bytes = 0;
            RenderTex *g = gpu && !skip ? gpu_tex(&upload) : NULL;
            assert(!g || (g->tex && g->tex.mipmapLevelCount == upload.levels));
            for (uint32_t l = 0; l < d[7]; ++l) {
                uint32_t w = mipdim(d[4], l), ht = mipdim(d[3], l);
                uint32_t pitch = ((w + 3) / 4) * 8, n = pitch * ((ht + 3) / 4);
                if (l >= (uint32_t)drop && l - drop < upload.levels) {
                    uint32_t i = l - drop;
                    assert(upload.pitch[i] == pitch && !memcmp(G + upload.mem[i], dds + off, n));
                    if (g) {
                        void *out = malloc(n);
                        [g->tex getBytes:out bytesPerRow:pitch fromRegion:MTLRegionMake2D(0, 0, w, ht) mipmapLevel:i];
                        assert(!memcmp(out, dds + off, n)); free(out);
                    }
                    bytes += n; finalw = w; finalh = ht;
                }
                off += n;
            }
            if (complete && drop == 0) assert(bytes == (unsigned)atoi(argv[3]));
            if (complete) assert(finalw == 1 && finalh == 1);
#if HAVE_HD_TAIL_HELPERS
            if (!repeat) {
                if (g) { render_release(&upload); g = NULL; }
                uint32_t safe=0;
                while (safe<upload.levels && mipdim(upload.w,safe)>=4 && mipdim(upload.h,safe)>=4) ++safe;
                for (uint32_t l=safe;l<upload.levels;++l) { gfree(upload.mem[l]); upload.mem[l]=0; }
                upload.levels=safe;unsigned before=live_allocs;fail_after=1;
                hd_append_tail(pi,dds,d[4],d[3],d[7],d[21]);
                assert(upload.levels==safe && live_allocs==before);fail_after=-1;
                hd_append_tail(pi,dds,d[4],d[3],d[7],d[21]);assert(upload.levels==d[7]-(uint32_t)drop);
                uint32_t payload;assert(hd_compressed_layout(dds,size,d[4],d[3],d[7],d[21],&payload));
                uint8_t head[128];memcpy(head,dds,128);
                ((uint32_t *)head)[7]=17;assert(!hd_compressed_layout(head,sizeof head,d[4],d[3],17,d[21],&payload));
                ((uint32_t *)head)[7]=d[7];((uint32_t *)head)[4]=0;
                assert(!hd_compressed_layout(head,size,d[4],d[3],d[7],d[21],&payload));
            }
#endif
        }
        count = upload.levels;
        // Execute the production resource release, including the tail slots.
        assert(obj_release(&upload) == 0);
        for (unsigned l = 0; l < 96; ++l) assert(!upload.mem[l]);
        gfree(pc); assert(live_allocs == 0);
    }
    printf("upload %ux%u levels=%u/%u payload=%u final=%ux%u cycles=32 %s%s\n", upload.w, upload.h,
           count, d[7] - drop, bytes, finalw, finalh, malformed ? "fallback PASS" : complete ? "PASS" : "FAIL incomplete",
           gpu ? skip ? " GPU unavailable" : " GPU byte parity" : " CPU");
    free(dds); free(G);
    return !complete ? 1 : skip ? 3 : 0;
} }
