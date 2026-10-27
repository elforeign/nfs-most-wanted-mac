// gpupass.m - BOOT_DIAG_GPUPASS=1 (needs BOOT_DIAG): GPU time per render/blit encoder of the frame's command
// buffer, from Metal stage-boundary timestamps (MTLCounterSampleBuffer, the only sampling point Apple GPUs have),
// written once a second to <diag>/gpupass.log as a table per pass label, sorted by cost. Off: one int test per
// encoder, nothing else. Encoders get their label too (Instruments' Metal System Trace then names them).
//
// Cost attribution: Apple GPUs overlap one encoder's vertex stage with the previous encoder's fragment stage, so
// each encoder is charged the fragment-stage time it adds after the previous encoder's fragment end:
// cost = end_frag - max(start_frag, previous end). The costs of a frame add up to its fragment-busy span; the log
// also prints the command buffer's GPUStart..GPUEnd so the two can be compared (vertex-only overlap is in "vtx").
// BOOT_DIAG_GPUPASS=2 adds draw-level detail: every BOOT_DIAG_GPUPASS_EVERY-th frame (default 60) the game's passes
// are split every BOOT_DIAG_GPUPASS_N draws (default 32), so each piece has its own timestamps. Such a frame writes
// gpusplit_draws.log (game thread: "F <frame> E <encoder> <draw description>") and gpusplit_cost.log (completion:
// "F <frame> E <encoder> <ms> <label>"); join them on frame + encoder. Each split adds a store + load of the target
// (charged to the next piece), so compare pieces with each other, not with the unsplit per-second table; split
// frames are left out of gpupass.log's per-second table.
#include "render_internal.h"
#include <mach/mach_time.h>
#include <stdarg.h>

int gpt_on;
const char *gpt_phase = "game";             // label prefix of the game's own passes: "after-vt" once the X360 chain's last pass ran
enum { GPT_SLOTS = 4, GPT_MAXENC = 1000, GPT_LABELS = 128 };
static id<MTLCounterSampleBuffer> sbuf[GPT_SLOTS];
static uint16_t slot_lab[GPT_SLOTS][GPT_MAXENC];
static uint8_t slot_blit[GPT_SLOTS][GPT_MAXENC];
static int slot_n[GPT_SLOTS], slot_over[GPT_SLOTS];
static _Atomic int slot_busy[GPT_SLOTS];
static int cur = -1;                          // slot of the frame being encoded (-1: not timing this frame)
static char *labels[GPT_LABELS];
static int nlabels;
static double tick_ns = 1.0;                  // GPU timestamp tick in ns (calibrated against the CPU clock)
static FILE *gpt_f, *split_draws_f, *split_cost_f;
static int split_n = 32, split_every = 60, split_frame, split_count;   // split_frame: this frame splits; split_count: draws in the open encoder
static int slot_split[GPT_SLOTS];
// per-second accumulators (completion thread only)
static double acc_cost[GPT_LABELS], acc_vtx[GPT_LABELS], acc_enc[GPT_LABELS];
static double acc_gpu_ms, acc_span_ms, acc_sum_ms, acc_max_gpu;
static int acc_frames, acc_skipped;
static uint64_t acc_first, acc_last, acc_t0;

static int intern(const char *s) {
    for (int i = 0; i < nlabels; ++i) if (!strcmp(labels[i], s)) return i;
    if (nlabels == GPT_LABELS) return GPT_LABELS - 1;
    labels[nlabels] = strdup(s);
    return nlabels++;
}
void gpt_init(void) {
    const char *e = getenv("BOOT_DIAG_GPUPASS");
    if (!diag_on || !e || (*e != '1' && *e != '2') || !mtl) return;
    if (![mtl supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary]) { fprintf(stderr, "boot: gpupass: no stage-boundary sampling\n"); return; }
    id<MTLCounterSet> ts = nil;
    for (id<MTLCounterSet> s in mtl.counterSets) if ([s.name isEqualToString:MTLCommonCounterSetTimestamp]) ts = s;
    if (!ts) { fprintf(stderr, "boot: gpupass: no timestamp counter set\n"); return; }
    for (int i = 0; i < GPT_SLOTS; ++i) {
        MTLCounterSampleBufferDescriptor *d = [MTLCounterSampleBufferDescriptor new];
        d.counterSet = ts; d.storageMode = MTLStorageModeShared; d.sampleCount = GPT_MAXENC * 4;
        NSError *err = nil;
        sbuf[i] = [mtl newCounterSampleBufferWithDescriptor:d error:&err];
        if (!sbuf[i]) { fprintf(stderr, "boot: gpupass: sample buffer: %s\n", err.localizedDescription.UTF8String); return; }
    }
    // tick length: GPU timestamps 200 ms of the host clock apart (on the measured host 1 tick = 1 ns; measure per host)
    MTLTimestamp c0, g0, c1, g1;
    uint64_t h0 = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    [mtl sampleTimestamps:&c0 gpuTimestamp:&g0];
    usleep(200000);
    [mtl sampleTimestamps:&c1 gpuTimestamp:&g1];
    double cpu_ns = (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - h0);
    if (g1 > g0) tick_ns = cpu_ns / (double)(g1 - g0);
    char path[1024];
    snprintf(path, sizeof path, "%s/gpupass.log", diag_dir);
    gpt_f = fopen(path, "w");
    if (!gpt_f) return;
    fprintf(gpt_f, "gpupass: stage-boundary timestamps, GPU tick %.4f ns (cpu %.0f ns over gpu %llu ticks)\n", tick_ns, cpu_ns, (unsigned long long)(g1 - g0));
    fflush(gpt_f);
    gpt_on = 1;
    if (atoi(e) == 2) {
        const char *n = getenv("BOOT_DIAG_GPUPASS_N"), *v = getenv("BOOT_DIAG_GPUPASS_EVERY");
        if (n && atoi(n) > 0) split_n = atoi(n);
        if (v && atoi(v) > 0) split_every = atoi(v);
        snprintf(path, sizeof path, "%s/gpusplit_draws.log", diag_dir); split_draws_f = fopen(path, "w");
        snprintf(path, sizeof path, "%s/gpusplit_cost.log", diag_dir); split_cost_f = fopen(path, "w");
        if (split_draws_f && split_cost_f) { gpt_on = 2; fprintf(gpt_f, "gpupass: split every %d draws, every %d frames\n", split_n, split_every); }
    }
}
static void frame_begin(void) {
    if (cur >= 0) return;
    static int next;
    for (int k = 0; k < GPT_SLOTS; ++k) {
        int s = (next + k) % GPT_SLOTS;
        if (!slot_busy[s]) {
            cur = s; next = s + 1; slot_n[s] = 0; slot_over[s] = 0; slot_busy[s] = 1;
            split_frame = gpt_on == 2 && frame_serial % (uint64_t)split_every == 0; slot_split[s] = split_frame; split_count = 0;
            return;
        }
    }
    cur = -2;                                  // every slot waits for the GPU: this frame isn't timed
}
static int add(const char *fmt, va_list ap, int blit) {
    frame_begin();
    if (cur < 0) return -1;
    if (slot_n[cur] >= GPT_MAXENC) { slot_over[cur] = 1; return -1; }
    char s[64];
    vsnprintf(s, sizeof s, fmt, ap);
    int i = slot_n[cur]++;
    split_count = 0;
    slot_lab[cur][i] = (uint16_t)intern(s);
    slot_blit[cur][i] = (uint8_t)blit;
    return i;
}
void gpt_render(MTLRenderPassDescriptor *rp, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int i = add(fmt, ap, 0);
    va_end(ap);
    if (i < 0) return;
    MTLRenderPassSampleBufferAttachmentDescriptor *a = rp.sampleBufferAttachments[0];
    a.sampleBuffer = sbuf[cur];
    a.startOfVertexSampleIndex = 4 * i; a.endOfVertexSampleIndex = 4 * i + 1;
    a.startOfFragmentSampleIndex = 4 * i + 2; a.endOfFragmentSampleIndex = 4 * i + 3;
}
void gpt_name(id<MTLCommandEncoder> e) {
    if (cur < 0 || !slot_n[cur]) return;
    e.label = [NSString stringWithUTF8String:labels[slot_lab[cur][slot_n[cur] - 1]]];
}
id<MTLBlitCommandEncoder> gpt_blit(id<MTLCommandBuffer> cb, const char *fmt, ...) {
    if (!gpt_on || cb != cmd) return [cb blitCommandEncoder];
    va_list ap; va_start(ap, fmt);
    int i = add(fmt, ap, 1);
    va_end(ap);
    if (i < 0) return [cb blitCommandEncoder];
    MTLBlitPassDescriptor *bp = [MTLBlitPassDescriptor blitPassDescriptor];
    bp.sampleBufferAttachments[0].sampleBuffer = sbuf[cur];
    bp.sampleBufferAttachments[0].startOfEncoderSampleIndex = 4 * i + 2;
    bp.sampleBufferAttachments[0].endOfEncoderSampleIndex = 4 * i + 3;
    id<MTLBlitCommandEncoder> b = [cb blitCommandEncoderWithDescriptor:bp];
    b.label = [NSString stringWithUTF8String:labels[slot_lab[cur][i]]];
    return b;
}
static int cmp_desc(const void *a, const void *b) {
    double x = acc_cost[*(const int *)a], y = acc_cost[*(const int *)b];
    return x < y ? 1 : x > y ? -1 : 0;
}
static void flush_second(void) {
    if (!acc_frames) return;
    double nf = acc_frames;
    fprintf(gpt_f, "frames %llu-%llu n=%d skipped=%d  cmdbuf GPU %.2f ms/frame (max %.2f)  frag-busy span %.2f  sum of pass costs %.2f\n",
            (unsigned long long)acc_first, (unsigned long long)acc_last, acc_frames, acc_skipped, acc_gpu_ms / nf, acc_max_gpu, acc_span_ms / nf, acc_sum_ms / nf);
    int idx[GPT_LABELS], n = 0;
    for (int i = 0; i < nlabels; ++i) if (acc_enc[i] > 0) idx[n++] = i;
    qsort(idx, (size_t)n, sizeof *idx, cmp_desc);
    for (int k = 0; k < n; ++k) {
        int i = idx[k];
        if (acc_cost[i] / nf < 0.005 && k > 12) continue;
        fprintf(gpt_f, "  %7.3f ms  vtx %6.3f  enc/frame %5.1f  %s\n", acc_cost[i] / nf, acc_vtx[i] / nf, acc_enc[i] / nf, labels[i]);
    }
    fflush(gpt_f);
    memset(acc_cost, 0, sizeof acc_cost); memset(acc_vtx, 0, sizeof acc_vtx); memset(acc_enc, 0, sizeof acc_enc);
    acc_gpu_ms = acc_span_ms = acc_sum_ms = acc_max_gpu = 0; acc_frames = acc_skipped = 0;
}
static int valid(uint64_t t) { return t != 0 && t != MTLCounterErrorValue; }
// The frame's last command buffer, before commit: its completion reads the slot's timestamps.
void gpt_frame(id<MTLCommandBuffer> cb, uint64_t serial) {
    if (!gpt_on) return;
    int s = cur;
    cur = -1;
    if (s < 0) { if (s == -2) ++acc_skipped; return; }
    int n = slot_n[s], over = slot_over[s], split = slot_split[s];
    split_frame = 0;
    [cb addCompletedHandler:^(id<MTLCommandBuffer> b) {
        double gpu_ms = (b.GPUEndTime - b.GPUStartTime) * 1e3;
        NSData *d = n ? [sbuf[s] resolveCounterRange:NSMakeRange(0, (NSUInteger)n * 4)] : nil;
        const uint64_t *t = d.bytes;
        double cost[GPT_LABELS] = {0}, vtx[GPT_LABELS] = {0}, encs[GPT_LABELS] = {0};
        uint64_t prev = 0, first = 0, last = 0;
        double sum = 0;
        for (int i = 0; t && i < n; ++i) {
            int l = slot_lab[s][i];
            uint64_t sv = t[4 * i], ev = t[4 * i + 1], sf = t[4 * i + 2], ef = t[4 * i + 3];
            encs[l] += 1;
            if (!slot_blit[s][i] && valid(sv) && valid(ev) && ev > sv) vtx[l] += (ev - sv) * tick_ns * 1e-6;
            if (!valid(sf) || !valid(ef) || ef < sf) continue;
            if (!first || sf < first) first = sf;
            if (ef > last) last = ef;
            uint64_t from = sf > prev ? sf : prev;
            if (ef > from) { double c = (ef - from) * tick_ns * 1e-6; cost[l] += c; sum += c; }
            if (ef > prev) prev = ef;
        }
        slot_busy[s] = 0;
        if (split) {                                  // per encoder, in order; not in the per-second table
            prev = 0;
            for (int i = 0; t && i < n; ++i) {
                uint64_t sf = t[4 * i + 2], ef = t[4 * i + 3];
                double c = 0;
                if (valid(sf) && valid(ef) && ef >= sf) { uint64_t from = sf > prev ? sf : prev; if (ef > from) c = (ef - from) * tick_ns * 1e-6; if (ef > prev) prev = ef; }
                fprintf(split_cost_f, "F %llu E %d %.4f %s\n", (unsigned long long)serial, i, c, labels[slot_lab[s][i]]);
            }
            fprintf(split_cost_f, "F %llu total %.3f cmdbuf %.3f\n", (unsigned long long)serial, sum, gpu_ms);
            fflush(split_cost_f); fflush(split_draws_f);
            return;
        }
        // accumulate (completion handlers of one queue run one at a time, in order)
        uint64_t now = mach_absolute_time();
        if (!acc_t0) acc_t0 = now;
        mach_timebase_info_data_t tb; mach_timebase_info(&tb);
        if ((double)(now - acc_t0) * tb.numer / tb.denom >= 1e9) { flush_second(); acc_t0 = now; }
        if (!acc_frames) acc_first = serial;
        acc_last = serial;
        ++acc_frames;
        if (over) ++acc_skipped;
        acc_gpu_ms += gpu_ms; if (gpu_ms > acc_max_gpu) acc_max_gpu = gpu_ms;
        acc_span_ms += last > first ? (last - first) * tick_ns * 1e-6 : 0;
        acc_sum_ms += sum;
        for (int l = 0; l < nlabels; ++l) { acc_cost[l] += cost[l]; acc_vtx[l] += vtx[l]; acc_enc[l] += encs[l]; }
    }];
}
// A label for a render pass from its attachments: what it draws into, sample count, load of colour 0 / depth.
void gpt_render_auto(MTLRenderPassDescriptor *rp, const char *what) {
    id<MTLTexture> t = rp.colorAttachments[0].texture;
    id<MTLTexture> z = rp.depthAttachment.texture;
    id<MTLTexture> r = t ? t : z;
    if (!r) { gpt_render(rp, "%s (none)", what); return; }
    const char *kind = r == bb_rt.ms || r == bb_rt.tex || r == backbuffer_tex ? "bb" : r == ad_rt.ms ? "autodepth" : r.textureType == MTLTextureTypeCube ? "cube" : t ? "rt" : "zonly";
    char la = !t ? '-' : rp.colorAttachments[0].loadAction == MTLLoadActionLoad ? 'L' : rp.colorAttachments[0].loadAction == MTLLoadActionClear ? 'C' : 'D';
    char lz = !z ? '-' : rp.depthAttachment.loadAction == MTLLoadActionLoad ? 'L' : rp.depthAttachment.loadAction == MTLLoadActionClear ? 'C' : 'D';
    gpt_render(rp, "%s %s %lux%lu x%lu c%c z%c", what, kind, (unsigned long)r.width, (unsigned long)r.height, (unsigned long)r.sampleCount, la, lz);
}
// BOOT_DIAG_GPUPASS=2, before a draw's begin_pass: 1 when the open encoder of a split frame has its N draws.
int gpt_split_due(void) {
    return split_frame && cur >= 0 && enc && split_count >= split_n;
}
// After the draw's begin_pass: what the draw is, in the split frame's draw list.
void gpt_draw_note(const char *fmt, ...) {
    if (!split_frame || cur < 0 || !slot_n[cur]) return;
    ++split_count;
    char s[320];
    va_list ap; va_start(ap, fmt); vsnprintf(s, sizeof s, fmt, ap); va_end(ap);
    fprintf(split_draws_f, "F %llu E %d %s\n", (unsigned long long)frame_serial, slot_n[cur] - 1, s);
}
