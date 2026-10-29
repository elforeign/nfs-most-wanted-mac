// overlay.m - the performance overlay: what the port is doing, drawn over the game while it runs.
//
// View > Performance Overlay (Cmd-Option-P cycles) picks off / compact / full, saved as `overlay` in USERDATA/app.txt;
// NFS_OVERLAY=off|compact|full overrides the menu. Compact is one line (fps, frame time p50/p99, sim rate); full adds
// a 4 s frame-time graph (frame interval, game-thread CPU, GPU, the display's frame budget) and the game thread's
// time split, sim ticks, GPU time and draws, shader compiles, texture uploads, memory, render settings, audio output
// and controller with the rumble motors.
//
// Where it draws: the game's drawable is the back buffer's size (the layer scales it to the window), so text drawn
// into it would be as blurry as a 720p picture on a 4K screen. The overlay has its own CAMetalLayer, a sublayer of
// the game's at the window's backing scale and only as large as the panel, so the text is pixel-sharp at any game
// resolution and the game's pixels (and diag captures) are untouched. The presenter fills it from the nfsmw.present
// queue in the same command buffer as the game's frame (render_present.m present_show -> overlay_present), so it costs
// nothing on the game thread and nothing at all when off.
//
// Numbers: perf_frames / perf_gpu_ms (diag.c, always on) are the same per-frame counters stats.log is made from, so
// they agree with it (fps, p50/p99/max, busy and its split over the last second). The game thread takes a snapshot of
// the counters 4 times a second (a few microseconds); a background queue formats the text and rasterizes it with
// CoreText into a texture; the presenter draws that texture plus the graph and rumble bars, which follow every frame.
// With BOOT_DIAG and the overlay on, overlay.log gets the panel text once a second (to check it against stats.log),
// and each BOOT_DIAG_SHOTS frame also writes overlay_NNNNN_compact.png and _full.png (the panel as drawn, with alpha).
#import <Cocoa/Cocoa.h>
#import <CoreAudio/CoreAudio.h>
#import <CoreText/CoreText.h>
#import <GameController/GameController.h>
#include <mach/mach.h>
#include <os/lock.h>
#include <stdlib.h>

#include "d3d9_internal.h"

// ---------------- level: menu, app.txt, NFS_OVERLAY ----------------
static int level = -1;
static const char *const level_names[] = {"off", "compact", "full"};
static int parse_level(const char *s) {
    for (int i = 0; i < 3; ++i) if (s && !strcasecmp(s, level_names[i])) return i;
    return s && *s >= '0' && *s <= '2' && !s[1] ? *s - '0' : -1;
}
int overlay_level(void) {
    if (level < 0) {
        int env = parse_level(getenv("NFS_OVERLAY"));
        level = env >= 0 ? env : parse_level(app_pref("overlay"));
        if (level < 0) level = OVERLAY_OFF;
        if (level) fprintf(stderr, "boot: performance overlay %s%s\n", level_names[level], env >= 0 ? " (NFS_OVERLAY)" : "");
    }
    return level;
}
void overlay_set_level(int l) {
    if (l < 0 || l > 2) return;
    overlay_level();
    level = l;
    app_pref_set("overlay", level_names[l]);
    if (diag_on) diag_event("overlay: level now %d", l);
}

// ---------------- the counters snapshot (game thread) ----------------
typedef struct {
    double t;                    // host_ticks seconds
    int level;
    float scale;                 // backing scale of the window
    // last second of frames
    int n;                       // frames
    float fps, p50, p99, max, busy, idle[IDLE_N], gpu, draws, ticks;
    int catchup, repeats;        // frames that ran >1 / 0 sim ticks
    float uploads;               // texture uploads per frame since the previous snapshot
    int sim_hz, display_hz, compiling, nshaders, npipes;
    uint64_t deferred;
    // memory
    uint64_t rss;
    uint32_t bump, gm, heap, vinyls, vinyls_mb, skins, skins_mb;
    int64_t tex, tex_mb, buf, buf_mb;
    // render
    uint32_t bw, bh, ww, wh, shadow, shadow_size, fsaa;
    int msaa, postfx_x360, widescreen;
    // audio, input
    char audio[96], device[96], pad[96];
    int audio_live;
    uint64_t underruns;
    int rumble_off, pad_xbox;
    float graph_max;             // the graph's top, ms
} Snap;

// A finished panel: its texture (premultiplied BGRA), size in pixels, and where the presenter draws the live parts.
typedef struct {
    id<MTLTexture> tex;
    float w, h, scale;
    float gx, gy, gw, gh;        // graph area (pixels, top-left origin); gw 0 = no graph
    float bx[2], by, bw, bh;     // rumble bars (heavy, light); bw 0 = none
    float graph_max, budget;     // ms at the graph's top, frame budget line
} Panel;

static dispatch_queue_t overlay_q;
static os_unfair_lock panel_lock = OS_UNFAIR_LOCK_INIT;
static Panel panel;              // under panel_lock: the latest finished panel
static CAMetalLayer *olayer;     // created on the main thread, drawn on the present queue
static int capture_due;          // diag: write overlay PNGs with the next snapshot (game thread)
static uint64_t capture_frame;

static int cmp_f(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }
static uint64_t rss_now(void) {
    mach_task_basic_info_data_t info;
    mach_msg_type_number_t n = MACH_TASK_BASIC_INFO_COUNT;
    return task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &n) == KERN_SUCCESS ? info.resident_size : 0;
}
static void default_device_name(char *out, size_t n) {
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioDeviceID dev = 0;
    UInt32 sz = sizeof dev;
    snprintf(out, n, "?");
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &sz, &dev) || !dev) return;
    CFStringRef name = NULL;
    a.mSelector = kAudioObjectPropertyName;
    sz = sizeof name;
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &sz, &name) || !name) return;
    CFStringGetCString(name, out, (CFIndex)n, kCFStringEncodingUTF8);
    CFRelease(name);
}

uint32_t galloc_used(void);
uint32_t gmalloc_used(void);
uint32_t heap_used(void);
void vinyls_live(uint32_t *nv, uint32_t *mbv, uint32_t *ns, uint32_t *mbs);
void render_live_stats(int64_t *tex, int64_t *tex_mb, int64_t *buf, int64_t *buf_mb);
void render_overlay_stats(int *compiling, int *nshader, int *npipe, uint64_t *uploads, uint64_t *deferred, uint64_t *renamed);
int pacing_sim_hz(void);
int user32_display_hz(void);
int dsound_output_info(const char **mode, int *live, uint64_t *underruns);
// MSAA sample count of the game's targets; the antialiasing work (render.m) provides the real one.
__attribute__((weak)) int render_msaa_samples(void) { return 1; }
static CGFloat backing_scale(void);

static void snapshot(Snap *s, int lv) {
    memset(s, 0, sizeof *s);
    s->t = host_ticks() / 1e9;
    s->level = lv;
    s->scale = layer ? (float)backing_scale() : 1;
    // the last second of frames, newest first (the same window stats.log uses)
    static float ft[PERF_RING];
    uint64_t head = __atomic_load_n(&perf_nframes, __ATOMIC_ACQUIRE);
    float sum = 0, busy = 0, idle[IDLE_N] = {0}, draws = 0, ticks = 0;
    int n = 0;
    for (; n < PERF_RING - 1 && (uint64_t)n < head && sum < 1000; ++n) {
        const PerfFrame *p = &perf_frames[(head - 1 - n) % PERF_RING];
        ft[n] = p->frame_ms;
        sum += p->frame_ms; busy += p->busy_ms; draws += p->draws; ticks += p->ticks;
        for (int k = 0; k < IDLE_N; ++k) idle[k] += p->idle_ms[k];
        s->catchup += p->ticks > 1;
        s->repeats += p->ticks == 0;
    }
    s->n = n;
    if (n) {
        s->fps = sum > 0 ? n * 1000.0f / sum : 0;
        s->busy = busy / n; s->draws = draws / n; s->ticks = ticks / n;
        for (int k = 0; k < IDLE_N; ++k) s->idle[k] = idle[k] / n;
        qsort(ft, (size_t)n, sizeof *ft, cmp_f);
        s->p50 = ft[n / 2]; s->p99 = ft[(n * 99) / 100]; s->max = ft[n - 1];
        uint64_t g = __atomic_load_n(&perf_gpu_serial, __ATOMIC_ACQUIRE);
        float gs = 0;
        int gn = 0;
        for (; gn < n && (uint64_t)gn < g; ++gn) gs += perf_gpu_ms[(g - gn) % PERF_RING];
        s->gpu = gn ? gs / gn : 0;
    }
    // the graph's scale: whole frame budgets covering the last 4 s but a few spikes (they run off the top), 2-8 budgets
    s->display_hz = user32_display_hz();
    float budget = 1000.0f / (s->display_hz > 0 ? s->display_hz : 60), acc = 0;
    int m = 0;
    for (; m < PERF_RING - 1 && (uint64_t)m < head && acc < 4000; ++m) {
        const PerfFrame *p = &perf_frames[(head - 1 - m) % PERF_RING];
        acc += p->frame_ms;
        ft[m] = fmaxf(p->frame_ms, p->busy_ms);
    }
    float top = 0;
    if (m) { qsort(ft, (size_t)m, sizeof *ft, cmp_f); top = ft[m - 1 - m / 100]; }
    s->graph_max = budget * fminf(8, fmaxf(2, ceilf(top * 1.1f / budget)));
    if (lv < OVERLAY_FULL && !capture_due) return;
    static uint64_t last_uploads, last_frames;
    uint64_t uploads, renamed;
    render_overlay_stats(&s->compiling, &s->nshaders, &s->npipes, &uploads, &s->deferred, &renamed);
    s->uploads = head > last_frames ? (float)(uploads - last_uploads) / (float)(head - last_frames) : 0;
    last_uploads = uploads; last_frames = head;
    s->sim_hz = pacing_sim_hz();
    s->rss = rss_now();
    s->bump = galloc_used(); s->gm = gmalloc_used(); s->heap = heap_used();
    vinyls_live(&s->vinyls, &s->vinyls_mb, &s->skins, &s->skins_mb);
    render_live_stats(&s->tex, &s->tex_mb, &s->buf, &s->buf_mb);
    s->bw = dev.pp[0]; s->bh = dev.pp[1];
    if (layer) { CGSize b = layer.bounds.size; s->ww = (uint32_t)(b.width * s->scale); s->wh = (uint32_t)(b.height * s->scale); }
    s->shadow = M32(0x00901830u);                                // g_ShadowDetail
    s->shadow_size = (uint32_t)x360_shadow_map_size();
    s->fsaa = M32(0x00901808u);                                  // g_FSAALevel
    s->msaa = render_msaa_samples();
    const char *ws = getenv("NFS_WIDESCREEN");
    int settings_get(const char *id);                            // settings.c: the live values (F10 menu, switches)
    s->postfx_x360 = settings_get("graphics.postfx") != 1;
    s->widescreen = !(ws && !strcmp(ws, "off"));
    s->rumble_off = settings_get("controls.rumble") == 0;
    s->pad_xbox = user32_pad_native() ? 2 : user32_pad_layout_xbox();
    // the slower queries once a second
    static double slow_at;
    static char device[96] = "?", pad[96] = "none";
    if (s->t >= slow_at) {
        slow_at = s->t + 1;
        default_device_name(device, sizeof device);
        @autoreleasepool {
            snprintf(pad, sizeof pad, "none");
            for (GCController *k in GCController.controllers)
                if (k.extendedGamepad) { snprintf(pad, sizeof pad, "%s", k.vendorName.UTF8String ?: "controller"); break; }
        }
    }
    memcpy(s->device, device, sizeof device);
    memcpy(s->pad, pad, sizeof pad);
    const char *mode = NULL;
    if (dsound_output_info(&mode, &s->audio_live, &s->underruns)) snprintf(s->audio, sizeof s->audio, "%s", mode);
    else s->audio_live = -2;                                     // the game hasn't created its sound device (yet)
}

// ---------------- the panel: text rasterized with CoreText (overlay queue) ----------------
#define PT 11.0                  // font size in points
#define PAD 8.0                  // panel padding in points
#define COLS 64                  // full panel width in characters
#define GRAPH_PT 64.0            // graph height in points
#define LABEL 13                 // value column

typedef struct { char label[16], text[160]; int dim; } Line;
static int panel_lines(const Snap *s, int lv, Line *L) {
    int n = 0;
#define LN(lab, ...) do { snprintf(L[n].label, sizeof L[n].label, "%s", lab); snprintf(L[n].text, sizeof L[n].text, __VA_ARGS__); L[n].dim = 0; ++n; } while (0)
    if (lv == OVERLAY_COMPACT) {
        LN("", "%5.1f fps  %5.2f ms p50  %5.2f p99  sim %d Hz", s->fps, s->p50, s->p99, pacing_sim_hz());
        return n;
    }
    LN("frames", "%5.1f fps  %5.2f ms p50  %5.2f p99  %6.2f max", s->fps, s->p50, s->p99, s->max);
    LN("", "%s", "");            // the graph goes here (GRAPH_PT high, the line is its legend's place)
    LN("game thread", "busy %5.2f ms  sleep %5.2f  gil %4.2f  gpu wait %4.2f  drawable %4.2f", s->busy, s->idle[IDLE_SLEEP],
       s->idle[IDLE_GIL], s->idle[IDLE_GPU], s->idle[IDLE_DRAWABLE]);
    LN("sim", "%d Hz (display %d)  %.2f ticks/frame  catch-up %d  repeats %d", s->sim_hz, s->display_hz, s->ticks, s->catchup, s->repeats);
    LN("gpu", "%5.2f ms  %4.0f draws  compiling %d  uploads %.1f/frame", s->gpu, s->draws, s->compiling, s->uploads);
    LN("", "%d shader variants  %d pipelines  %llu draws deferred", s->nshaders, s->npipes, (unsigned long long)s->deferred);
    L[n - 1].dim = 1;
    LN("memory", "RSS %llu MB  bump %u MB  gmalloc %u MB  heap %u MB", (unsigned long long)(s->rss >> 20), s->bump >> 20, s->gm >> 20, s->heap >> 20);
    LN("", "GPU textures %lld (%lld MB)  buffers %lld (%lld MB)", (long long)s->tex, (long long)s->tex_mb, (long long)s->buf, (long long)s->buf_mb);
    LN("", "HD vinyls %u (%u MB)  skins %u (%u MB)", s->vinyls, s->vinyls_mb, s->skins, s->skins_mb);
    L[n - 1].dim = 1;
    uint32_t g = 1;
    for (uint32_t a = s->bw, b = s->bh; b;) { uint32_t t = a % b; a = b; b = t; g = a; }
    char aspect[32];
    if (s->bw && s->bh && s->bw / g <= 64) snprintf(aspect, sizeof aspect, "%u:%u", s->bw / g, s->bh / g);
    else snprintf(aspect, sizeof aspect, "%.2f:1", s->bh ? (double)s->bw / s->bh : 0.0);
    int wide = s->bh && s->bw * 3 > s->bh * 4;
    LN("render", "%ux%u in %ux%u  %s %s", s->bw, s->bh, s->ww, s->wh, aspect, !wide ? "" : s->widescreen ? "Hor+" : "(widescreen off)");
    char aa[32];
    if (s->msaa > 1) snprintf(aa, sizeof aa, "MSAA %dx", s->msaa);
    else snprintf(aa, sizeof aa, "AA off");
    char sh[32];
    if (s->shadow) snprintf(sh, sizeof sh, "shadows %u (%u)", s->shadow, s->shadow_size);
    else snprintf(sh, sizeof sh, "shadows off");
    LN("", "post %s  %s  %s", s->postfx_x360 ? "X360" : "PC", sh, aa);
    if (s->audio_live > 0) LN("audio", "%s  %s  %llu underruns", s->audio, s->device, (unsigned long long)s->underruns);
    else LN("audio", "%s", s->audio_live == -2 ? "no sound device" : s->audio_live < 0 ? "waiting for an output device (mixing on the wall clock)"
                                                                                        : "silent (NFS_AUDIO=null)");
    LN("input", "%s  %s%s", s->pad, s->pad_xbox == 2 ? "native bindings (Options > Controls)" : s->pad_xbox ? "Xbox layout" : "PlayStation layout", s->rumble_off ? "  rumble off" : "");
    LN("rumble", "heavy %*s light", 14, "");
#undef LN
    return n;
}

static CTFontRef font(float scale) {
    static CTFontRef f;
    static float fs;
    if (!f || fs != scale) {
        if (f) CFRelease(f);
        f = CTFontCreateUIFontForLanguage(kCTFontUIFontUserFixedPitch, PT * scale, NULL);
        fs = scale;
    }
    return f;
}
static void draw_text(CGContextRef ctx, CTFontRef f, const char *s, double x, double y, CGColorRef color) {
    CFStringRef str = CFStringCreateWithCString(NULL, s, kCFStringEncodingUTF8);
    if (!str) return;
    const void *k[2] = {kCTFontAttributeName, kCTForegroundColorAttributeName}, *v[2] = {f, color};
    CFDictionaryRef attr = CFDictionaryCreate(NULL, k, v, 2, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFAttributedStringRef as = CFAttributedStringCreate(NULL, str, attr);
    CTLineRef line = CTLineCreateWithAttributedString(as);
    CGContextSetTextPosition(ctx, x, y);
    CTLineDraw(line, ctx);
    CFRelease(line); CFRelease(as); CFRelease(attr); CFRelease(str);
}

// Builds the panel's texture and layout for a snapshot at a level (also writes overlay.log in diag mode).
static Panel build_panel(const Snap *s, int lv) {
    Panel p = {0};
    Line L[24];
    int n = panel_lines(s, lv, L);
    float k = s->scale;
    CTFontRef f = font(k);
    UniChar zero = '0';
    CGGlyph gl;
    CGSize adv;
    CTFontGetGlyphsForCharacters(f, &zero, &gl, 1);
    CTFontGetAdvancesForGlyphs(f, kCTFontOrientationHorizontal, &gl, &adv, 1);
    double cw = adv.width, asc = CTFontGetAscent(f), lh = ceil(asc + CTFontGetDescent(f) + CTFontGetLeading(f) + 2 * k);
    double pad = PAD * k, gh = lv == OVERLAY_FULL ? GRAPH_PT * k : 0;
    int cols = 0;
    for (int i = 0; i < n; ++i) { int c = (int)strlen(L[i].text) + (lv == OVERLAY_FULL ? LABEL : 0); if (c > cols) cols = c; }
    if (lv == OVERLAY_FULL && cols < COLS) cols = COLS;
    p.w = (float)ceil(2 * pad + cols * cw);
    p.h = (float)ceil(2 * pad + n * lh + (gh > lh ? gh - lh : 0));
    p.scale = k;
    p.graph_max = s->graph_max;
    p.budget = 1000.0f / (s->display_hz > 0 ? s->display_hz : 60);
    size_t W = (size_t)p.w, H = (size_t)p.h;
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, W, H, 8, W * 4, cs, kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
    CGColorSpaceRelease(cs);
    if (!ctx) return p;
    CGContextSetRGBFillColor(ctx, 0.04, 0.05, 0.06, 0.72);
    CGPathRef bg = CGPathCreateWithRoundedRect(CGRectMake(0, 0, W, H), 6 * k, 6 * k, NULL);
    CGContextAddPath(ctx, bg); CGContextFillPath(ctx); CGPathRelease(bg);
    CGColorRef white = CGColorCreateGenericRGB(0.95, 0.96, 0.97, 1), dim = CGColorCreateGenericRGB(0.60, 0.64, 0.68, 1);
    CGColorRef c_frame = CGColorCreateGenericRGB(0.95, 0.96, 0.97, 1), c_cpu = CGColorCreateGenericRGB(0.36, 0.85, 0.47, 1);
    CGColorRef c_gpu = CGColorCreateGenericRGB(1.0, 0.62, 0.25, 1), c_bud = CGColorCreateGenericRGB(0.95, 0.30, 0.30, 1);
    double y = pad;                                              // top of the current line, from the top
    FILE *log = NULL;
    static double log_at;
    if (diag_on && lv == s->level && s->t >= log_at) {           // overlay.log: once a second, the panel as shown
        char path[1024];
        snprintf(path, sizeof path, "%s/overlay.log", diag_dir);
        static FILE *f_log;
        if (!f_log) f_log = fopen(path, "w");
        log = f_log;
        log_at = s->t + 1;
        if (log) fprintf(log, "t %.1f %s\n", s->t, level_names[lv]);
    }
    for (int i = 0; i < n; ++i) {
        double base = H - (y + asc);                             // CG's origin is bottom-left
        double x = pad;
        if (lv == OVERLAY_FULL) {
            draw_text(ctx, f, L[i].label, x, base, dim);
            x += LABEL * cw;
        }
        if (lv == OVERLAY_FULL && i == 1) {                      // the graph: legend on the right, area below
            p.gx = (float)x; p.gy = (float)y; p.gw = (float)(W - pad - x); p.gh = (float)gh;
            char top[32];
            snprintf(top, sizeof top, "%.0f ms", s->graph_max);
            draw_text(ctx, f, top, pad, base, dim);
            draw_text(ctx, f, "4 s", pad, H - (y + gh - lh + asc), dim);
            double lx = pad, ly = H - (y + lh + asc);
            draw_text(ctx, f, "frame", lx, ly, c_frame);
            draw_text(ctx, f, "cpu", lx + 6 * cw, ly, c_cpu);
            ly -= lh;
            draw_text(ctx, f, "gpu", lx, ly, c_gpu);
            draw_text(ctx, f, "budget", lx + 4 * cw, ly, c_bud);
            y += gh;
            if (log) fprintf(log, "  [graph 0-%.0f ms, budget %.2f ms]\n", s->graph_max, p.budget);
            continue;
        }
        draw_text(ctx, f, L[i].text, x, base, L[i].dim ? dim : white);
        if (lv == OVERLAY_FULL && !strcmp(L[i].label, "rumble")) {   // bars after "heavy " and "light "
            p.bx[0] = (float)(x + 6 * cw); p.bx[1] = (float)(x + 27 * cw);
            p.bw = (float)(14 * cw - 2 * k); p.by = (float)(y + 0.2 * lh); p.bh = (float)(0.6 * lh);
        }
        if (log) fprintf(log, "  %-*s%s\n", lv == OVERLAY_FULL ? LABEL : 0, L[i].label, L[i].text);
        y += lh;
    }
    if (log) fflush(log);
    CGColorRelease(white); CGColorRelease(dim); CGColorRelease(c_frame); CGColorRelease(c_cpu); CGColorRelease(c_gpu); CGColorRelease(c_bud);
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:W height:H mipmapped:NO];
    d.usage = MTLTextureUsageShaderRead;
    d.storageMode = MTLStorageModeShared;
    p.tex = [mtl newTextureWithDescriptor:d];
    [p.tex replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:CGBitmapContextGetData(ctx) bytesPerRow:W * 4];
    CGContextRelease(ctx);
    return p;
}

// ---------------- drawing (present queue; capture: overlay queue) ----------------
static const char *overlay_src =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct VI { float2 pos [[attribute(0)]]; float2 uv [[attribute(1)]]; float4 color [[attribute(2)]]; };\n"
    "struct VO { float4 pos [[position]]; float2 uv; float4 color; };\n"
    "vertex VO ov_vs(VI i [[stage_in]], constant float2 &size [[buffer(1)]]) {\n"
    "  VO o; o.pos = float4(i.pos / size * float2(2, -2) + float2(-1, 1), 0, 1); o.uv = i.uv; o.color = i.color; return o; }\n"
    "fragment float4 ov_fs(VO i [[stage_in]], texture2d<float> t [[texture(0)]], sampler s [[sampler(0)]]) {\n"
    "  if (i.uv.x < 0) return float4(i.color.rgb * i.color.a, i.color.a);   // solid: lines and bars\n"
    "  return t.sample(s, i.uv) * i.color.a; }\n";   // the panel texture is premultiplied
typedef struct { float x, y, u, v; uint8_t c[4]; } Vtx;
static id<MTLRenderPipelineState> pso;
static id<MTLSamplerState> smp;
static int pso_init(void) {
    static int tried;
    if (pso || tried) return pso != nil;
    tried = 1;
    NSError *err = nil;
    id<MTLLibrary> lib = [mtl newLibraryWithSource:[NSString stringWithUTF8String:overlay_src] options:nil error:&err];
    if (!lib) { fprintf(stderr, "boot: overlay: %s\n", err.localizedDescription.UTF8String); return 0; }
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    vd.attributes[0].format = MTLVertexFormatFloat2; vd.attributes[0].offset = 0;
    vd.attributes[1].format = MTLVertexFormatFloat2; vd.attributes[1].offset = 8;
    vd.attributes[2].format = MTLVertexFormatUChar4Normalized; vd.attributes[2].offset = 16;
    vd.layouts[0].stride = sizeof(Vtx);
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"ov_vs"];
    pd.fragmentFunction = [lib newFunctionWithName:@"ov_fs"];
    pd.vertexDescriptor = vd;
    MTLRenderPipelineColorAttachmentDescriptor *ca = pd.colorAttachments[0];
    ca.pixelFormat = MTLPixelFormatBGRA8Unorm;
    ca.blendingEnabled = YES;
    ca.sourceRGBBlendFactor = ca.sourceAlphaBlendFactor = MTLBlendFactorOne;
    ca.destinationRGBBlendFactor = ca.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    pso = [mtl newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!pso) { fprintf(stderr, "boot: overlay: %s\n", err.localizedDescription.UTF8String); return 0; }
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = MTLSamplerMinMagFilterNearest;   // the panel is drawn 1:1
    smp = [mtl newSamplerStateWithDescriptor:sd];
    return 1;
}

typedef struct { Vtx *v; int n, cap; } Verts;
static void vpush(Verts *b, float x, float y, float u, float v, const uint8_t c[4]) {
    if (b->n == b->cap) { b->cap = b->cap ? 2 * b->cap : 4096; b->v = realloc(b->v, (size_t)b->cap * sizeof *b->v); }
    b->v[b->n++] = (Vtx){x, y, u, v, {c[0], c[1], c[2], c[3]}};
}
static void quad(Verts *b, float x0, float y0, float x1, float y1, float u0, float v0, float u1, float v1, const uint8_t c[4]) {
    vpush(b, x0, y0, u0, v0, c); vpush(b, x1, y0, u1, v0, c); vpush(b, x0, y1, u0, v1, c);
    vpush(b, x1, y0, u1, v0, c); vpush(b, x1, y1, u1, v1, c); vpush(b, x0, y1, u0, v1, c);
}
static void segment(Verts *b, float x0, float y0, float x1, float y1, float t, const uint8_t c[4]) {
    float dx = x1 - x0, dy = y1 - y0, l = sqrtf(dx * dx + dy * dy);
    if (l < 1e-3f) return;
    float nx = -dy / l * t * 0.5f, ny = dx / l * t * 0.5f;
    vpush(b, x0 + nx, y0 + ny, -1, 0, c); vpush(b, x1 + nx, y1 + ny, -1, 0, c); vpush(b, x0 - nx, y0 - ny, -1, 0, c);
    vpush(b, x1 + nx, y1 + ny, -1, 0, c); vpush(b, x1 - nx, y1 - ny, -1, 0, c); vpush(b, x0 - nx, y0 - ny, -1, 0, c);
}

// The panel, then the parts that follow every frame: the last 4 s of frame / CPU / GPU times and the rumble motors.
static void encode(id<MTLRenderCommandEncoder> e, const Panel *p) {
    Verts b = {0};               // per call: the present queue and a diag capture may both be drawing
    static const uint8_t white[4] = {255, 255, 255, 255}, c_frame[4] = {242, 245, 247, 230}, c_cpu[4] = {92, 217, 120, 255},
                         c_gpu[4] = {255, 158, 64, 255}, c_bud[4] = {242, 77, 77, 200}, c_grid[4] = {255, 255, 255, 40},
                         c_bar[4] = {255, 255, 255, 30}, c_heavy[4] = {92, 160, 255, 255}, c_light[4] = {150, 220, 255, 255};
    quad(&b, 0, 0, p->w, p->h, 0, 0, 1, 1, white);
    float k = p->scale;
    if (p->gw > 0 && p->graph_max > 0) {
        float x0 = p->gx, y0 = p->gy, w = p->gw, h = p->gh - 2 * k, ys = h / p->graph_max;
        quad(&b, x0, y0 + h, x0 + w, y0 + h + k, -1, 0, -1, 0, c_grid);               // the 0 ms axis
        quad(&b, x0, y0, x0 + w, y0 + k, -1, 0, -1, 0, c_grid);                       // the top
        for (float m = 2 * p->budget; m < p->graph_max * 0.999f; m += p->budget)       // budget multiples
            quad(&b, x0, y0 + h - m * ys, x0 + w, y0 + h - m * ys + k, -1, 0, -1, 0, c_grid);
        uint64_t head = __atomic_load_n(&perf_nframes, __ATOMIC_ACQUIRE), g = __atomic_load_n(&perf_gpu_serial, __ATOMIC_ACQUIRE);
        float px[3] = {0}, py[3] = {0}, t = 0, xs = w / 4000.0f;
        for (uint64_t i = 0; i < PERF_RING - 2 && i < head && t <= 4000; ++i) {
            const PerfFrame *f = &perf_frames[(head - 1 - i) % PERF_RING];
            float x = x0 + w - t * xs;
            float v[3] = {f->frame_ms, f->busy_ms, i < g ? perf_gpu_ms[(g - i) % PERF_RING] : 0};
            for (int s = 0; s < 3; ++s) {
                float y = y0 + h - fminf(v[s], p->graph_max) * ys;
                if (i) segment(&b, px[s], py[s], x, y, 1.5f * k, s == 0 ? c_frame : s == 1 ? c_cpu : c_gpu);
                px[s] = x; py[s] = y;
            }
            t += f->frame_ms > 0 ? f->frame_ms : 1000.0f / 60;
        }
        for (float x = x0; x < x0 + w; x += 8 * k)                                     // the frame budget, dashed, on top
            quad(&b, x, y0 + h - p->budget * ys - 0.5f * k, fminf(x + 5 * k, x0 + w), y0 + h - p->budget * ys + 1.0f * k, -1, 0, -1, 0, c_bud);
    }
    if (p->bw > 0) {
        float lv[2];
        feedback_last_levels(&lv[0], &lv[1]);
        for (int m = 0; m < 2; ++m) {
            quad(&b, p->bx[m], p->by, p->bx[m] + p->bw, p->by + p->bh, -1, 0, -1, 0, c_bar);
            quad(&b, p->bx[m], p->by, p->bx[m] + p->bw * fminf(fmaxf(lv[m], 0), 1), p->by + p->bh, -1, 0, -1, 0, m ? c_light : c_heavy);
        }
    }
    float size[2] = {p->w, p->h};
    [e setRenderPipelineState:pso];
    [e setVertexBytes:size length:sizeof size atIndex:1];
    if ((size_t)b.n * sizeof(Vtx) <= 4096) [e setVertexBytes:b.v length:(NSUInteger)b.n * sizeof(Vtx) atIndex:0];
    else [e setVertexBuffer:[mtl newBufferWithBytes:b.v length:(NSUInteger)b.n * sizeof(Vtx) options:MTLResourceStorageModeShared] offset:0 atIndex:0];
    [e setFragmentTexture:p->tex atIndex:0];
    [e setFragmentSamplerState:smp atIndex:0];
    [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:(NSUInteger)b.n];
    free(b.v);
}

// render_present.m present_show, on the nfsmw.present queue, in the command buffer that shows the game's frame.
void overlay_present(id<MTLCommandBuffer> cb) {
    if (level <= OVERLAY_OFF || !olayer || olayer.hidden) return;
    os_unfair_lock_lock(&panel_lock);
    Panel p = panel;
    os_unfair_lock_unlock(&panel_lock);
    if (!p.tex || !pso_init()) return;
    CGSize ds = olayer.drawableSize;
    if ((uint32_t)ds.width != (uint32_t)p.w || (uint32_t)ds.height != (uint32_t)p.h) return;   // resized on the main thread next
    id<CAMetalDrawable> d = [olayer nextDrawable];
    if (!d) return;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = d.texture;
    rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
    encode(e, &p);
    [e endEncoding];
    [cb presentDrawable:d];
}

// diag: the panel at both levels for this snapshot, as drawn (with alpha), for composites with the frame's PNG.
static void capture(const Snap *s, uint64_t frame) {
    if (!pso_init()) return;
    for (int lv = OVERLAY_COMPACT; lv <= OVERLAY_FULL; ++lv) {
        Panel p = build_panel(s, lv);
        if (!p.tex) continue;
        MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:(NSUInteger)p.w height:(NSUInteger)p.h mipmapped:NO];
        d.usage = MTLTextureUsageRenderTarget;
        d.storageMode = MTLStorageModeShared;
        id<MTLTexture> t = [mtl newTextureWithDescriptor:d];
        MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture = t;
        rp.colorAttachments[0].loadAction = MTLLoadActionClear;
        rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
        rp.colorAttachments[0].storeAction = MTLStoreActionStore;
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        id<MTLRenderCommandEncoder> e = [cb renderCommandEncoderWithDescriptor:rp];
        encode(e, &p);
        [e endEncoding];
        [cb commit];
        [cb waitUntilCompleted];
        size_t W = t.width, H = t.height;
        void *px = malloc(W * H * 4);
        [t getBytes:px bytesPerRow:W * 4 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
        char path[1024];
        snprintf(path, sizeof path, "%s/overlay_%05llu_%s.png", diag_dir, (unsigned long long)frame, level_names[lv]);
        diag_png(path, px, (uint32_t)W, (uint32_t)H, (uint32_t)W * 4, 1);
        free(px);
        diag_event("overlay: %s panel %zux%zu at scale %.1f in a %ux%u window (%s)", level_names[lv], W, H, s->scale, s->ww, s->wh, path);
    }
}

// ---------------- the layer (main thread = the game thread) ----------------
// The window's backing scale (the game's CAMetalLayer keeps contentsScale 1: its drawable is the back buffer's size).
static CGFloat backing_scale(void) {
    id v = layer.delegate;                                       // a view's backing layer: the view
    CGFloat k = [v isKindOfClass:NSView.class] ? ((NSView *)v).window.backingScaleFactor : 0;
    return k > 0 ? k : NSScreen.mainScreen.backingScaleFactor > 0 ? NSScreen.mainScreen.backingScaleFactor : 1;
}
static void layer_update(void) {
    if (!layer) return;
    CAMetalLayer *parent = layer;
    int show = level > OVERLAY_OFF;
    if (!olayer) {
        if (!show) return;
        olayer = [CAMetalLayer layer];
        olayer.device = mtl;
        olayer.pixelFormat = MTLPixelFormatBGRA8Unorm;
        // Independent SDR tag: the parent may be extended-linear sRGB in the
        // hidden EDR presenter experiment. Overlay values remain sRGB/paper white.
        CGColorSpaceRef srgb_colorspace(void);
        olayer.colorspace = srgb_colorspace();
        olayer.opaque = NO;
        olayer.framebufferOnly = YES;
        olayer.anchorPoint = CGPointZero;
        olayer.actions = @{@"bounds": NSNull.null, @"position": NSNull.null, @"contents": NSNull.null, @"hidden": NSNull.null,
                           @"contentsScale": NSNull.null};
        olayer.zPosition = 1;
        [parent addSublayer:olayer];
    }
    os_unfair_lock_lock(&panel_lock);
    float w = panel.w, h = panel.h;
    int have = panel.tex != nil;
    os_unfair_lock_unlock(&panel_lock);
    show = show && have;
    if (olayer.hidden == show) olayer.hidden = !show;
    if (!show) return;
    CGFloat k = backing_scale(), m = 12;
    CGSize b = parent.bounds.size;
    CGRect fr = CGRectMake(m, parent.geometryFlipped ? m : b.height - m - h / k, w / k, h / k);
    if (olayer.contentsScale != k) olayer.contentsScale = k;
    if (!CGRectEqualToRect(olayer.frame, fr)) olayer.frame = fr;
    if (olayer.drawableSize.width != w || olayer.drawableSize.height != h) olayer.drawableSize = CGSizeMake(w, h);
}

// Every Present (diag.c diag_frame, game thread): nothing at all when off; otherwise keeps the layer in place and
// takes a counters snapshot 4 times a second for the overlay queue to turn into the next panel.
void overlay_frame(void) {
    int lv = overlay_level();
    if (diag_on && lv && getenv("BOOT_DIAG_SHOTS") && diag_frame_wanted(dev.frames, "BOOT_DIAG_SHOTS")) { capture_due = 1; capture_frame = dev.frames; }
    if (!lv) { if (olayer && !olayer.hidden) olayer.hidden = YES; return; }
    layer_update();
    static double next;
    double now = host_ticks() / 1e9;
    static int last_lv;
    if (now < next && lv == last_lv && !capture_due) return;
    next = now + 0.25;
    last_lv = lv;
    if (!overlay_q) overlay_q = dispatch_queue_create("nfsmw.overlay", DISPATCH_QUEUE_SERIAL);
    Snap *s = malloc(sizeof *s);
    snapshot(s, lv);
    int cap = capture_due;
    uint64_t cf = capture_frame;
    capture_due = 0;
    dispatch_async(overlay_q, ^{
        @autoreleasepool {
            Panel p = build_panel(s, lv);
            if (p.tex) {
                os_unfair_lock_lock(&panel_lock);
                panel = p;
                os_unfair_lock_unlock(&panel_lock);
            }
            if (cap) capture(s, cf);
            free(s);
        }
    });
}
