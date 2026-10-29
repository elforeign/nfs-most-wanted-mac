// render_target.m - render targets: MSAA textures and resolves, pass attachments (split from render.m; shared state in render_internal.h).
#include "render_internal.h"

// ---------------- MSAA ----------------
// The game's Antialiasing option (g_FSAALevel, D3DMULTISAMPLE_NONMASKABLE with the level as quality) makes the back
// buffer and the auto depth buffer multisampled (presentation parameters), plus one window-sized multisampled render
// target (CreateRenderTarget in FUN_006c2e60, used by FUN_006dae20). In a race the scene is drawn into the back
// buffer and StretchRect'ed into the scene texture that the visual treatment reads, then the treatment and the HUD
// are drawn into the back buffer again. A multisampled surface gets a 2DMultisample texture (RenderTex.ms) that its
// passes render into and store, and keeps its single-sample texture (.tex) as the resolve target, which is what
// everything reading the surface uses: StretchRect sources, Present, the X360 post chain (colour and depth), diag
// captures. The resolve is a load + resolve pass when such a read finds ms newer than tex (3 per race frame: the
// scene copy, the motion-blur copy and Present). Shadow maps, reflections and the game's other targets are created
// without MultiSample and stay single-sampled; StretchRect into a multisampled surface draws into ms.
static uint32_t msaa_counts[8], msaa_n = ~0u;
uint32_t render_msaa_levels(uint32_t *counts) {
    if (msaa_n == ~0u) {
        id<MTLDevice> d = mtl ? mtl : MTLCreateSystemDefaultDevice();
        msaa_n = 0;
        for (uint32_t c = 2; c <= 8; c *= 2) if ([d supportsTextureSampleCount:c]) msaa_counts[msaa_n++] = c;
    }
    if (counts) memcpy(counts, msaa_counts, msaa_n * sizeof *counts);
    return msaa_n;
}
uint32_t render_msaa_count(uint32_t type, uint32_t quality) {
    uint32_t n = render_msaa_levels(NULL);
    if (type == 1) return n ? msaa_counts[(quality < 1 ? 1 : quality > n ? n : quality) - 1] : 1;   // NONMASKABLE: level
    for (uint32_t i = 0; i < n; ++i) if (msaa_counts[i] == type) return type;                     // 2_SAMPLES..16_SAMPLES
    return 1;
}
int render_msaa_samples(void) { return bb_rt.ms ? (int)bb_rt.ms.sampleCount : 1; }
static uint64_t stat_resolves;
uint64_t render_msaa_resolves(void) { uint64_t n = stat_resolves; stat_resolves = 0; return n; }   // since the last call (stats.log)
int64_t msaa_bytes;
int64_t render_msaa_bytes(void) { return msaa_bytes; }
id<MTLTexture> new_ms_texture(MTLPixelFormat pf, NSUInteger w, NSUInteger h, uint32_t samples) {
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pf width:w height:h mipmapped:NO];
    d.textureType = MTLTextureType2DMultisample;
    d.sampleCount = samples;
    d.usage = MTLTextureUsageRenderTarget;
    // Soft smoke resolves the nearest covered depth sample in a separate pass.
    // The Off path never reads it and keeps the existing resolve policy.
    if (is_depth_pf(pf)) d.usage |= MTLTextureUsageShaderRead;
    const char *hc=diag_on?getenv("BOOT_DIAG_HDR_CLIP"):NULL;
    if (diag_on && (getenv("BOOT_DIAG_SOFT_SMOKE") || (hc && !strcmp(hc,"1")))) d.usage |= MTLTextureUsageShaderRead;
    d.storageMode = MTLStorageModePrivate;
    id<MTLTexture> t = [mtl newTextureWithDescriptor:d];
    msaa_bytes += (int64_t)t.allocatedSize;
    return t;
}
void drop_ms_texture(RenderTex *r) {
    if (r->ms) msaa_bytes -= (int64_t)r->ms.allocatedSize;
    r->ms = nil; r->stale = 0;
}


// Before a multisampled target is released or replaced: the open encoder must not refer to its RenderTex.
void msaa_forget(RenderTex *r) {
    for (int i = 0; i < 5; ++i) if (r && enc_ms[i] == r) { end_pass(); return; }
}
// Brings a multisampled target's single-sample texture up to date (see MSAA): a pass that loads the samples and
// resolves them (Metal resolves an attachment that has a resolve texture at the end of every pass it is in, so
// the game's own passes, often several per target and frame, only store), unless the target's passes resolve on
// store (ms_attach), in which case the open pass into it is ended or tex is current already.
int resolve_magenta;
uint8_t enc_resolve[5];
static int msaa_onstore = -1;              // NFS_TEST_MSAA_RESOLVE=pass: only the separate load + resolve passes (A/B)
static void reads_roll(RenderTex *r) {     // a new frame: this frame's read count starts at 0, the last one's is kept
    if (r->read_serial == frame_serial) return;
    r->reads_prev = r->read_serial + 1 == frame_serial ? r->reads : 0;
    r->reads = 0; r->read_serial = frame_serial;
}
void msaa_resolve(RenderTex *r) {
    if (!r || !r->ms) return;
    reads_roll(r); ++r->reads;
    if (!r->stale) return;
    if (enc && !(r == &bb_rt && resolve_magenta))
        for (int i = 0; i < 5; ++i) if (enc_ms[i] == r && enc_resolve[i]) {
            end_pass();                                    // resolves on store, clears r->stale
            ++stat_resolves;
            FILE *df = dump_file();
            if (df) fprintf(df, "msaa resolve on store: %s %lux%lu x%lu\n", r == &bb_rt ? "back buffer" : r == &ad_rt ? "auto depth buffer" : "render target",
                            (unsigned long)r->ms.width, (unsigned long)r->ms.height, (unsigned long)r->ms.sampleCount);
            return;
        }
    end_pass();
    ensure_cmd();
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    MTLRenderPassAttachmentDescriptor *a = is_depth_pf(r->ms.pixelFormat) ? (MTLRenderPassAttachmentDescriptor *)rp.depthAttachment
                                                                         : (MTLRenderPassAttachmentDescriptor *)rp.colorAttachments[0];
    a.texture = r->ms; a.loadAction = MTLLoadActionLoad; a.storeAction = MTLStoreActionStoreAndMultisampleResolve;
    a.resolveTexture = r->tex;
    if (r == &bb_rt && resolve_magenta) {                  // BOOT_DIAG_PROBE msaa: Present's resolve, of magenta samples
        a.loadAction = MTLLoadActionClear;
        rp.colorAttachments[0].clearColor = MTLClearColorMake(1, 0, 1, 1);
        probe_applied(PROBE_MSAA, 1);
    }
    if (gpt_on) gpt_render(rp, "resolve %s %lux%lu x%lu", r == &bb_rt ? "bb" : r == &ad_rt ? "autodepth" : "rt", (unsigned long)r->ms.width, (unsigned long)r->ms.height, (unsigned long)r->ms.sampleCount);
    { id<MTLRenderCommandEncoder> e = [cmd renderCommandEncoderWithDescriptor:rp]; if (gpt_on) gpt_name(e); [e endEncoding]; }
    r->stale = 0;
    ++stat_resolves;
    FILE *df = dump_file();
    if (df) fprintf(df, "msaa resolve: %s %lux%lu x%lu\n", r == &bb_rt ? "back buffer" : r == &ad_rt ? "auto depth buffer" : "render target",
                    (unsigned long)r->ms.width, (unsigned long)r->ms.height, (unsigned long)r->ms.sampleCount);
}
void ms_attach(MTLRenderPassAttachmentDescriptor *a, RenderTex *r, int slot) {
    a.texture = r->ms;
    a.level = 0; a.slice = 0;
    enc_ms[slot] = r;
    if (slot==4 && r==&ad_rt) {
        int soft=render_soft_depth_store();
        if(soft){
            a.storeAction=MTLStoreActionStore;
            if(soft==1){
                a.resolveTexture=r->tex;a.storeAction=MTLStoreActionStoreAndMultisampleResolve;
                ((MTLRenderPassDepthAttachmentDescriptor *)a).depthResolveFilter=MTLMultisampleDepthResolveFilterMin;
                enc_resolve[slot]=1;r->stale=1;
            }else{a.resolveTexture=nil;enc_resolve[slot]=0;r->stale=0;}
            return;
        }
    }
    if (r->resolve_pass && slot < 4) {     // a texture draws sample: resolve at the end of every pass into it
        a.storeAction = MTLStoreActionStoreAndMultisampleResolve;
        a.resolveTexture = r->tex;
        return;
    }
    // Resolve at the end of the pass while tex will still be read this frame (fewer reads so far than last frame:
    // the back buffer twice or three times, the auto depth once, before the X360 chain), so the read needs no
    // separate pass that loads all samples again. A pass that isn't the last before the read resolves once too often.
    // Safe because no draw samples bb/ad .tex inside a game pass: back buffer and auto depth are surfaces, not
    // textures, and the post passes that read them are their own encoders. (Not MTLStoreActionUnknown decided at the
    // end: with a resolve texture Metal then only allows resolve actions.)
    if (msaa_onstore < 0) { const char *e = getenv("NFS_TEST_MSAA_RESOLVE"); msaa_onstore = !(e && !strcmp(e, "pass")); }
    reads_roll(r);
    if (msaa_onstore && r->tex && r->reads < r->reads_prev) {
        a.storeAction = MTLStoreActionStoreAndMultisampleResolve;
        a.resolveTexture = r->tex;
        enc_resolve[slot] = 1;
    }
    r->stale = 1;
}
// Makes a render-target texture (one level) and its depth surface multisampled from their first use on, like surfaces
// created with MultiSample: mirror.c for the rear-view mirror. The texture's level 0 surface then renders into the
// multisampled texture, and each pass resolves it into the texture that draws sample (no stale tracking: the mirror
// is read by the HUD's draws, inside a pass). Both or neither (a pass with mismatched sample counts would drop its
// depth): 0 when either is unknown, already has its GPU texture (the sample count can't change any more) or isn't a
// render target / stand-alone depth surface.
int render_target_msaa(uint32_t texture, uint32_t depth, int samples) {
    Obj *t = obj(texture), *d = obj(depth);
    if (!t || !d || t->gpu || d->gpu || samples < 2) return 0;
    if (t->kind != K_TEXTURE || !(t->usage & 1) || t->levels > 1 || d->kind != K_SURFACE || d->parent) return 0;
    t->samples = d->samples = (uint32_t)samples;
    return 1;
}
// Metal needs every attachment of a pass to have the same sample count; D3D9 doesn't allow a mismatch either
// (the depth buffer must match the render target's multisampling), so a depth buffer that doesn't match is left out.
id<MTLTexture> pass_depth(id<MTLTexture> rt0, id<MTLTexture> ds) {
    if (!ds || !rt0 || ds.sampleCount == rt0.sampleCount) return ds;
    static int logged;
    if (diag_on && logged++ < 8) diag_event("render: depth buffer %ux%u x%lu with render target %ux%u x%lu: sample counts differ, drawn without depth",
                                            (unsigned)ds.width, (unsigned)ds.height, (unsigned long)ds.sampleCount, (unsigned)rt0.width, (unsigned)rt0.height, (unsigned long)rt0.sampleCount);
    return nil;
}
