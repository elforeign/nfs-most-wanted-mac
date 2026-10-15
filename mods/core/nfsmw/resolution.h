/* resolution.h - resolution, display modes and everything that follows from them (moved out of nfsmw.c 7 Oct 2026, unchanged). Included once by nfsmw.c at the same place, so the single translation unit is as before. */
#ifndef RESOLUTION_H
#define RESOLUTION_H

/* ---- resolution and everything that follows from it ---------------------- */

/* Entry 0 is "Auto"; w and h are 0 there. */
typedef struct {
    uint16_t w, h;
    uint32_t hash;
    char label[24];
} ResEntry;
static ResEntry g_res[RES_MAX];
static int g_res_count = 0;

static const uint16_t kCommonSizes[][2] = {
    {640, 480},   {768, 480},   {800, 600},   {854, 480},   {960, 540},   {960, 640},   {1024, 576},
    {1024, 768},  {1152, 864},  {1280, 720},  {1280, 800},  {1280, 960},  {1280, 1024}, {1360, 768},
    {1366, 768},  {1400, 1050}, {1440, 900},  {1600, 900},  {1600, 1200}, {1680, 1050}, {1920, 1080},
    {1920, 1200}, {2048, 1152}, {2048, 1536}, {2560, 1080}, {2560, 1440}, {2560, 1600}, {2880, 1800},
    {3024, 1890}, {3456, 2160}, /* 14" and 16" MacBook Pro below the notch, 1:1 full screen (GitHub PR #10, Simonxdd) */
    {3200, 1800}, {3440, 1440}, {3840, 1600}, {3840, 2160}, {3840, 2400}, {4096, 2160}, {5120, 1440},
    {5120, 2160}, {5120, 2880}, {6016, 3384}, {7680, 4320},
};

/* The engine's string hash (bStringHash). */
static uint32_t string_hash(const char *s) {
    uint32_t h = 0xffffffffu;
    while (*s)
        h = h * 33u + (uint8_t)*s++;
    return h;
}

static void screen_shape(uint32_t *w, uint32_t *h) {
    uint32_t screen_w = 0, screen_h = 0;
    if (g_api->screen_size && g_api->screen_size(g_api, &screen_w, &screen_h) == POP_OK && screen_w &&
        screen_h) {
        float aspect = (float)screen_w / (float)screen_h;
        uint32_t rows = screen_h < 1080 ? screen_h : 1080;
        *h = rows;
        *w = ((uint32_t)lroundf(rows * aspect) + 1) & ~1u;
        return;
    }
    *w = 1280;
    *h = 720;
}

static void add_size(uint32_t w, uint32_t h) {
    if (g_res_count >= RES_MAX || !w || !h || w > 0x7fff || h > 0xffff)
        return;
    int at = 1;
    while (at < g_res_count && (g_res[at].w < w || (g_res[at].w == w && g_res[at].h < h)))
        ++at;
    if (at < g_res_count && g_res[at].w == w && g_res[at].h == h)
        return;
    memmove(&g_res[at + 1], &g_res[at], (size_t)(g_res_count - at) * sizeof g_res[0]);
    ResEntry *e = &g_res[at];
    char key[48];
    e->w = (uint16_t)w;
    e->h = (uint16_t)h;
    snprintf(e->label, sizeof e->label, "%ux%u", w, h);
    snprintf(key, sizeof key, "OPT_VO_PC_RES_%uX%u", w, h);
    e->hash = string_hash(key);
    ++g_res_count;
}

static void build_resolution_list(void) {
    uint32_t sw = 0, sh = 0, aw, ah;
    screen_shape(&aw, &ah);
    memset(g_res, 0, sizeof g_res);
    snprintf(g_res[0].label, sizeof g_res[0].label, "Auto (%ux%u)", aw, ah);
    g_res[0].hash = string_hash("OPT_VO_PC_RES_AUTO");
    g_res_count = 1;
    for (size_t i = 0; i < sizeof kCommonSizes / sizeof kCommonSizes[0]; ++i)
        add_size(kCommonSizes[i][0], kCommonSizes[i][1]);
    if (g_api->screen_size && g_api->screen_size(g_api, &sw, &sh) == POP_OK)
        add_size(sw, sh);
    for (int i = 0; i < RES_MAX; ++i) {
        g_api->guest_write_u32(g_api, TABLE_RES_SUPPORTED + 4u * (uint32_t)i, i < g_res_count);
        g_api->guest_write_u32(g_api, TABLE_RES_REFRESH + 4u * (uint32_t)i, 0); /* the default rate */
        g_api->guest_write_u32(g_api, TABLE_RES_LABEL + 4u * (uint32_t)i, i < g_res_count ? g_res[i].hash : 0);
    }
    g_api->guest_write_u32(g_api, SLOT_RES_LAST, (uint32_t)g_res_count - 1);
    g_api->guest_write_u32(g_api, SLOT_RES_COUNT, (uint32_t)g_res_count);
}

static int resolution_index(void) {
    uint32_t i = get_u32(RESOLUTION_INDEX);
    return i < (uint32_t)g_res_count ? (int)i : 0;
}

static void choose_resolution(uint32_t *w, uint32_t *h) {
    int64_t sw = setting("width", 0), sh = setting("height", 0);
    if (sw > 0 && sh > 0) {
        *w = (uint32_t)sw;
        *h = (uint32_t)sh;
        return;
    }
    const ResEntry *e = &g_res[resolution_index()];
    if (e->w && e->h) {
        *w = e->w;
        *h = e->h;
        return;
    }
    screen_shape(w, h);
}

/* HD Reflections AutoRes uses half the width and one sixth the height.
 * Use the selected game size, not the Retina display size. The verified
 * resource constructor reads these globals for both colour and depth;
 * 006bd0d0 also uses them for the mirror viewport. Re-evaluate on reset. */
static void mirror_resources(const PopModApi *api, pop_cpu_v1 *cpu,
                             PopHookInvocation *inv, void *user) {
    uint32_t w = 256, h = 256;
    (void)cpu; (void)inv; (void)user;
    if (setting("mirror_hd", 1)) {
        choose_resolution(&w, &h);
        w /= 2; h /= 6;
        if (w < 64) w = 64;
        if (h < 32) h = 32;
        if (w > 4096) w = 4096;
        if (h > 2048) h = 2048;
    }
    api->guest_write_u32(api, 0x008f9008u, w);
    api->guest_write_u32(api, 0x008f900cu, h);
    if (setting("mirror_msaa", 0)) {   /* run 13 mirror parity: the kit gives this one texture size 4x MSAA */
        char m[32];
        snprintf(m, sizeof m, "%ux%u", w, h);
        setenv("RECOMP_REMASTER_MIRROR_MSAA", m, 1);
    } else {
        unsetenv("RECOMP_REMASTER_MIRROR_MSAA");
    }
    char line[96];
    snprintf(line, sizeof line, "core.nfsmw: mirror colour/depth target %ux%u", w, h);
    api->log(api, line);
}

/* Test62 (owner decision D53-7): the rear-view mirror stays visible while looking back.
 * The mirror state update 006cfad0 (state 00982b3c: 1 off, 2 on) asks 00595d40(view 1) whether
 * the mirror is allowed (its only caller). 00595d40 returns 0 when any of these holds:
 *  (a) 0092d884 set and the camera director's current action (*[0092d87c] slot 0x20) answers
 *      slot 0x18 = 0 (005a0c20: [+0x18] | [+0x1c] == 0);
 *  (b) the view's first camera mover is type 1 and slot 0x30 is non-zero (004741d0: 0x8000,
 *      a 180 degree yaw, while byte [+0xa1] is set: Look Back);
 *  (c) 00525b20(1) (pause/overlay); (d) no options or option byte +0x39 clear, or the view's
 *      camera setting is 2..6 (the views without a mirror); (e) 00516b50(0089fbec) fails.
 * Test62 traces (smoke m5/m6): holding Look Back sets (b) at once and (a) from the next frame;
 * (c)(d)(e) stay as before. Policy while Look Back is held on a type-1 mover with that exact
 * slot 0x30: the same checks without the two Look Back vetoes (a)(b), in the original order;
 * otherwise the original, unchanged. Not HD Reflections' ForceEnableMirror (all camera views):
 * (d) still hides the mirror in chase views. An owner-chosen enhancement over the USA 360
 * (which hides it); `mirror_look_back` 0 restores the original. */
static uint32_t mirror_view_mover(uint32_t view) {
    const uint32_t list = 0x009195e0u + 0x70u * view + 0x44u, first = get_u32(list);
    return (first && first != list) ? first - 4 : 0;
}
/* Test65 (D53-5): the USA Xbox 360 "press start" screen. The USA WS_MW_LS_Splash package (68 objects)
 * answers message XENON_WS (3d4b7270) with the Xenon background group 84cca6e8 (z 200) and its logo
 * groups, and CURRENT_GEN_WIDESCREEN (cb835ee3) with the PC backgrounds 329d8bcb (z 950); the USA
 * constructor (822b84xx) sends XENON_WS where the PC one (005a3030) sends CURRENT_GEN_WIDESCREEN, shows
 * HDGROUP 534cc377 (EA logo 000004e5, "HD" text 6408ffb1) when the output is HD (always on the Mac) and
 * runs SHOW on the online notice bde7fa72. Active when the loaded package carries the Xenon background
 * group (the USA package, and also the owner's X360 UI Texts package, which keeps the Xenon background
 * but not the HD group); a package without it is untouched. The HD group's own APPEAR script fades it
 * out after about 4.6 s, exactly as on the console (package data, not a port timer). */
#define SPLASH_XENON_BACK 0x84cca6e8u
static int splash_usa_package(const PopModApi *api, uint32_t package_name) {
    uint32_t args[2] = {package_name, SPLASH_XENON_BACK}, obj = 0;
    return package_name && api->guest_call &&
           api->guest_call(api, 0x00524850u, 0, args, 2, &obj) == POP_OK && obj != 0;
}
/* BEFORE QueuePackageMessage 00516c90 called from the splash constructor (return 005a30a2). */
static void splash_ws_message(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    if (!setting("splash_xbox", 1) || get_u32(cpu->esp + 4) != 0xcb835ee3u ||
        !splash_usa_package(api, get_u32(cpu->esp + 8)))
        return;
    api->guest_write_u32(api, cpu->esp + 4, 0x3d4b7270u);   /* bStringHash("XENON_WS") */
    static int logged;
    if (!logged++) api->log(api, "core.nfsmw: splash: USA package, XENON_WS sent instead of CURRENT_GEN_WIDESCREEN");
}
/* REPLACE SplashScreen::SplashScreen 005a3030: the USA constructor's HD and online-notice steps. */
static void splash_ctor(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    const uint32_t self = cpu->ecx;
    api->call_original(api, cpu->target, cpu);
    const uint32_t package_name = self ? get_u32(self + 0x10) : 0;
    if (!setting("splash_xbox", 1) || !splash_usa_package(api, package_name)) return;
    uint32_t args[2] = {package_name, 0x534cc377u}, group = 0, notice = 0, unused = 0;
    if (api->guest_call(api, 0x00524850u, 0, args, 2, &group) == POP_OK && group)
        api->guest_call(api, 0x00514cc0u, 0, &group, 1, &unused);              /* FEngSetVisible(HDGROUP) */
    args[1] = 0xbde7fa72u;
    if (api->guest_call(api, 0x00524850u, 0, args, 2, &notice) == POP_OK && notice) {
        const uint32_t script[3] = {notice, 0x001ca7c0u, 1};                   /* FEngSetScript(obj, SHOW, force) */
        api->guest_call(api, 0x00514d10u, 0, script, 3, &unused);
    }
    static int logged;
    if (!logged++) api->log(api, group ? "core.nfsmw: splash: USA package, HD group shown" : "core.nfsmw: splash: USA package without HD group");
}
static void mirror_look_back(const PopModApi *api, pop_cpu_v1 *cpu,
                             PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    const uint32_t view = get_u32(cpu->esp + 4);
    const uint32_t mover = view < 8 ? mirror_view_mover(view) : 0;
    uint8_t looking_back = 0;
    static uint32_t last_mover;   /* the mover that was looking back on the previous call */
    /* Default OFF since Test144 (owner, 3 Oct): in Look Back the mirror only duplicated the screen. */
    const int eligible = setting("mirror_look_back", 0) && mover && get_u32(mover + 0xc) == 1 &&
                         get_u32(get_u32(mover) + 0x30) == 0x004741d0u && api->guest_call &&
                         api->guest_read_u8(api, mover + 0xa1, &looking_back) == POP_OK;
    /* Release: the director's action (a) still reports Look Back for one more frame (Test62 m7:
     * a one-frame drop to state 1 at release), so the first call after release is treated alike. */
    const int just_released = eligible && !looking_back && last_mover == mover;
    last_mover = eligible && looking_back ? mover : 0;
    if (!eligible || (!looking_back && !just_released)) {
        api->call_original(api, cpu->target, cpu);
        return;
    }
    uint32_t result = 0, one = 1, r = 0;
    do {
        if (api->guest_call(api, 0x00525b20u, 0, &one, 1, &r) != POP_OK || (r & 0xffu)) break;      /* (c) */
        const uint32_t opts = get_u32(0x0091cf90u);
        if (!opts) break;
        const uint32_t o = get_u32(opts + 0x10);
        uint8_t enabled = 0;
        if (!o || api->guest_read_u8(api, o + 0x39, &enabled) != POP_OK || !enabled) break;          /* (d) */
        const uint32_t camera = get_u32(o + (view * 5u + 0x1eu) * 4u);
        if (camera >= 2 && camera <= 6) break;
        uint32_t key = 0x0089fbecu;                                                                  /* (e) */
        if (api->guest_call(api, 0x00516b50u, get_u32(0x0091cadcu), &key, 1, &r) != POP_OK) break;
        result = (r & 0xffu) != 0;
    } while (0);
    if (setting("input_trace", 0)) {
        static uint32_t last = 0xffffffffu;
        if (result != last) {
            char line[96];
            snprintf(line, sizeof line, "core.nfsmw: mirror in Look Back: allowed=%u (view %u)", result, view);
            api->log(api, line);
            last = result;
        }
    }
    api->hook_return(api, cpu, (cpu->eax & 0xffffff00u) | result, 0);   /* cdecl: caller pops */
}

/* HD Reflections' full mirror scenery selection: 006bfdd0 supplies 0x1000
 * for view3, and 004fae40 additionally sets reduced-detail bit0x20.
 * Apply only to this view; keep the road/cubemap/player branches untouched.
 * The resulting 0x6002 input and cleared0x20 match the mod's two patches. */
static void mirror_scenery(const PopModApi *api, pop_cpu_v1 *cpu,
                           PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t view = get_u32(cpu->esp + 4), cull = get_u32(cpu->esp + 8);
    int enabled = view == MIRROR_VIEW && setting("mirror_full_scenery", 1);
    if (enabled) api->guest_write_u32(api, cpu->esp + 12, 0x6002);
    api->call_original(api, cpu->target, cpu);
    if (enabled && cull) {
        uint32_t flags = get_u32(cull + 0x84) & ~0x20u;
        api->guest_write_u32(api, cull + 0x84, flags);
        static int logged;
        if (!logged++) api->log(api, "core.nfsmw: full mirror scenery selection active");
    }
}

/* HD Reflections' mirror-shadow pass, verified against 006de300 and
 * 006e54e0. The original caller owns a frustum scratch matrix at ESP+e0.
 * Our entry still has its return address, hence +e4. guest_call preserves
 * registers/x87; all pointers remain guest addresses. The extra pass only
 * runs when the mirror is enabled, has a camera, and shadows are visible. */
/* Set when the mirror pass has drawn its target this frame; pfx_frame_body (post_fx.h) latches it for the mirror
 * grade/glass (clean_grade.h, run 13) and clears it, so a mirror not redrawn is never graded twice. */
static int g_mirror_fresh;
static void mirror_shadows(const PopModApi *api, pop_cpu_v1 *cpu,
                           PopHookInvocation *inv, void *user) {
    uint32_t scratch = cpu->esp + 0xe4u;
    uint8_t mirror_on = 0;
    void *validated = NULL;
    (void)inv; (void)user;
    api->call_original(api, FLUSH_RENDER_BATCHES, cpu);
    g_mirror_fresh = 1;
    api->guest_read_u8(api, MIRROR_VIEW + 8, &mirror_on);
    /* Default OFF since Test142 (3 Oct): ~1.35 ms per frame (Test141); a second shadow-map render for the mirror. */
    if (!setting("mirror_shadows", 0) || !mirror_on || !get_u32(MIRROR_VIEW + 0x40) ||
        !get_u32(0x00901830u) || !(get_float(0x008fae68u) >= get_float(0x008fae50u)) ||
        api->guest_ptr(api, scratch, 64, &validated) != POP_OK)
        return;
    const uint32_t args[] = {MIRROR_VIEW, scratch};
    uint32_t unused = 0;
    /* TEST ONLY (Test141, NFSMW_TEST_MIRROR_COST=1): time of this extra world-shadow pass, per 600 frames. */
    static int cost = -1;
    static uint64_t cost_ns; static uint32_t cost_frames;
    if (cost < 0) cost = getenv("NFSMW_TEST_MIRROR_COST") != NULL;
    struct timespec c0, c1;
    if (cost) clock_gettime(CLOCK_MONOTONIC, &c0);
    if (api->guest_call(api, DRAW_WORLD_SHADOWS, 0, args, 2, &unused) == POP_OK) {
        api->guest_call(api, FLUSH_RENDER_BATCHES, 0, NULL, 0, &unused);
        if (cost) {
            clock_gettime(CLOCK_MONOTONIC, &c1);
            cost_ns += (uint64_t)(c1.tv_sec - c0.tv_sec) * 1000000000ull + (uint64_t)(c1.tv_nsec - c0.tv_nsec);
            if (++cost_frames % 600 == 0) {
                char line[128];
                snprintf(line, sizeof line, "core.nfsmw: TEST mirror cost: world-shadow pass %.1f us/frame", cost_ns / 600.0 / 1000.0);
                api->log(api, line);
                cost_ns = 0;
            }
        }
        static int logged = 0;
        if (!logged++) api->log(api, "core.nfsmw: mirror world-shadow pass active");
    }
}

/* Test73 (owner Development 59 session: police light bars dark in the rear-view mirror).
 * The game draws the per-frame vehicle light flares (headlights, brake lights, police light
 * bars: the pool at 00916a78, stride 0x30, count 00916018) with 00507770(view). The player
 * view (006de620/006de817/006df359) calls it; the PC mirror pass in 006de300 (006de96d-006de9f8)
 * calls only the world-section flares 00505e80 and never 00507770, although 00507770 keeps a
 * dedicated branch for view 3 (the mirror, 00919730). HD Reflections' RestoreDetails adds the
 * call for the mirror at 006de9f0 and tests visibility bit 4 instead of the mirror's own bit 3
 * of the per-flare view mask (00915aa8[i]; its patch is "mov eax,2" for "mov eax,1" at
 * 0050777c). Same here, without code patches: after the mirror's own 00505e80 call returns
 * (006de9f5; only reached when the game's flares are on, 009017e4), copy bit 4 of each mask
 * into bit 3, call 00507770(mirror view), and put every mask back exactly as it was. */
#define FLARE_POOL_COUNT 0x00916018u
#define FLARE_VIEW_MASKS 0x00915aa8u
#define FLARE_POOL_MAX 348u /* the mask array ends where the count begins */
#define RENDER_VEHICLE_FLARES 0x00507770u
/* TEST ONLY (NFSMW_TEST_MIRROR_LIGHT_LOG): flares 00505380 is asked to draw for the mirror. */
static int g_mirror_lights_active;
static uint32_t g_mirror_flare_submits;
static void mirror_flare_count(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)api; (void)inv; (void)user;
    if (g_mirror_lights_active && get_u32(cpu->esp + 4) == MIRROR_VIEW) ++g_mirror_flare_submits;
}
static void mirror_lights(const PopModApi *api, pop_cpu_v1 *cpu,
                          PopHookInvocation *inv, void *user) {
    (void)cpu; (void)inv; (void)user;
    if (!setting("mirror_lights", 1)) return;
    uint32_t count = get_u32(FLARE_POOL_COUNT);
    if (count > FLARE_POOL_MAX) count = FLARE_POOL_MAX;
    uint32_t *masks = NULL, saved[FLARE_POOL_MAX];
    if (count && api->guest_ptr(api, FLARE_VIEW_MASKS, count * 4u, (void **)&masks) != POP_OK) return;
    static int own_bit = -1;
    if (own_bit < 0) own_bit = getenv("NFSMW_TEST_MIRROR_OWN_MASK") ? 1 : 0; /* TEST ONLY: bit 3 */
    for (uint32_t i = 0; i < count; ++i) {
        saved[i] = masks[i];
        if (!own_bit) masks[i] = (masks[i] & ~0x8u) | ((masks[i] >> 1) & 0x8u);
    }
    uint32_t own_clear = 0, used_clear = 0;
    for (uint32_t i = 0; i < count; ++i) {
        own_clear += !(saved[i] & 0x8u);
        used_clear += !(masks[i] & 0x8u);
    }
    const uint32_t view = MIRROR_VIEW;
    uint32_t unused = 0;
    const uint32_t submits_before = g_mirror_flare_submits;
    g_mirror_lights_active = 1;
    PopModStatus r = api->guest_call(api, RENDER_VEHICLE_FLARES, 0, &view, 1, &unused);
    g_mirror_lights_active = 0;
    /* 00507770 neither adds nor removes pool entries; restore what was there. */
    if (count && api->guest_ptr(api, FLARE_VIEW_MASKS, count * 4u, (void **)&masks) == POP_OK)
        for (uint32_t i = 0; i < count; ++i) masks[i] = saved[i];
    static uint32_t calls, drawn_frames;
    ++calls;
    if (count) ++drawn_frames;
    static int test_log = -1;
    if (test_log < 0) test_log = getenv("NFSMW_TEST_MIRROR_LIGHT_LOG") != NULL;
    if (test_log && calls % 120 == 0) {
        char line[200];
        snprintf(line, sizeof line, "core.nfsmw: TEST mirror lights call %u: pool %u, mirror bit clear %u, view-4 bit clear %u, flares submitted %u",
                 calls, count, own_clear, used_clear, g_mirror_flare_submits - submits_before);
        api->log(api, line);
    }
    if (r != POP_OK || calls == 1 || (setting("input_trace", 0) && calls % 600 == 0)) {
        char line[160];
        snprintf(line, sizeof line, "core.nfsmw: mirror vehicle lights %s (call %u, %u frames with flares, %u now)",
                 r == POP_OK ? "drawn" : "call failed", calls, drawn_frames, count);
        api->log(api, line);
    }
}

/* The PC detail slider re-enables the PC colour treatment/overbright/blur.
 * Those are independent of geometry quality and conflict with the accepted
 * native mod presentation. Apply at load/save/reset boundaries, not per draw.
 * This explicit native option is reversible for original-PC comparisons. */
static void video_mod_lighting(const PopModApi *api) {
    /* Run 29 (D-163): the PC look (the PC's own treatment and OverBright bloom) is retired; mod_lighting off still
     * brings the PC's treatment back. */
    /* One bloom: while the Xbox 360 exposure/bloom stage is on (x360_exposure, Test68), the PC
     * OverBright bloom stays off whatever mod_lighting says (owner, Development 56 session). */
    api->guest_write_u32(api, 0x009017fcu, 0); /* OverBright (the stage is always on: x360_exposure retired, D-117) */
    if (!setting("mod_lighting", 1)) return;
    api->guest_write_u32(api, 0x00901828u, 0); /* VisualTreatment: the PC sepia/golden colour grade */
    api->guest_write_u32(api, 0x009017fcu, 0); /* OverBright */
    api->guest_write_u32(api, 0x009017dcu, 0); /* MotionBlur */
}

/* Anti-aliasing from the settings menu (Test73; MENU3 slice 1; Off / 2x / 4x owner 8 Oct). The game's own FSAA value
 * 00901808 (0 off; 1 = NONMASKABLE quality 1, which the shim draws as 4x) is read when the device's
 * present parameters are rebuilt (006bfab0); its own video menu (0051034f/0051d1a4) asks for that
 * rebuild by setting the byte 00982c39, which 006e7220 handles at a safe point: resources released,
 * present parameters rebuilt, the device reset (006db0d0, which itself drops FSAA to 0 and retries
 * if the reset fails).
 * Owner 8 Oct ("turn off anti-aliasing at 4k ... off, 2x or 4x and let the user decide"): the setting `antialiasing`
 * is 0 Off (the default), 1 2x or 2 4x. The game's value only says on or off (its cap 00901890 is one level), so the
 * count travels beside it: 2x sets RECOMP_D3D9_MSAA_SAMPLES=2, which the kit's D3D9 layer reads when the device is
 * created or reset (kit dx/d3d9_msaa.h) and caps the multisampled count to 2; 4x and Off leave it unset, so 4x is
 * exactly the old 4x. The game's own setting is no longer a choice: the mod always writes the game's value.
 * It is applied at video load (before the device exists) and, when the menu changes it, once through the same
 * request the game's menu makes - an edge, never a continuous override, so a refused reset is not retried.
 * The old setting `msaa` (-1 the game's own, 0 off, 1 4x; now hidden) is carried over once as Off (owner 9 Oct, run 15:
 * "migrate everyone to Off" - the old 4x came mostly from presets and the old default, not a choice); then it is reset
 * to -1, so a later menu choice of 2x or 4x is kept. */
#define GAME_FSAA 0x00901808u
#define GAME_VIDEO_RESET_REQUEST 0x00982c39u
#define AA_OFF 0
#define AA_2X 1
#define AA_4X 2
static const char *const k_aa_name[3] = {"off", "2x", "4x"};
static int64_t g_msaa_applied = -2;
static int g_msaa_cap_env = -1; /* what RECOMP_D3D9_MSAA_SAMPLES says: -1 not yet set by us, 0 unset, 2 */
static int64_t aa_setting(void) {
    const int64_t v = setting("antialiasing", AA_OFF);
    return v < AA_OFF || v > AA_4X ? AA_OFF : v;
}
/* Owner 9 Oct (D-116; HDRP: HDR + 4x in Rosewood at 4K missed 1.13 % of the 60 Hz refreshes, in bursts of ~3 a second,
 * HDR + Off 0.14 %): with HDR on, 4x runs as 2x. The saved choice stays 4x and comes back with HDR off. */
static int64_t aa_effective(void) {
    const int64_t v = aa_setting();
    return v == AA_4X && setting("hdr_output", 0) ? AA_2X : v;
}
/* The old `msaa` value as the new setting: always Off (owner 9 Oct, run 15). */
static int64_t aa_from_old_msaa(int64_t old) { (void)old; return AA_OFF; }
static void msaa_migrate(const PopModApi *api) {
    static int done;
    if (done)
        return;
    done = 1;
    const int64_t old = setting("msaa", -1);
    if (old < 0)
        return;
    const int64_t aa = aa_from_old_msaa(old);
    api->settings_set(api, "antialiasing", aa);
    api->settings_set(api, "msaa", -1);
    char line[160];
    snprintf(line, sizeof line, "core.nfsmw: anti-aliasing: the old setting (%s) carried over as %s",
             old ? "4x" : "off", k_aa_name[aa]);
    api->log(api, line);
}
/* The sample-count cap for the kit; 1 when it changed. */
static int msaa_cap_env(int64_t aa) {
    const int cap = aa == AA_2X ? 2 : 0;
    if (cap == g_msaa_cap_env)
        return 0;
    g_msaa_cap_env = cap;
    if (cap)
        setenv("RECOMP_D3D9_MSAA_SAMPLES", "2", 1);
    else
        unsetenv("RECOMP_D3D9_MSAA_SAMPLES");
    return 1;
}
/* Test75: after a requested reset, the value it actually ran with. The game's reset path drops
 * FSAA to 0 and retries when the device refuses the multisampled one; the setting then says what
 * is really running (Off) instead of the refused choice, and the log says why. -1: nothing pending. */
static int32_t g_msaa_verify = -1;
static void msaa_verify(const PopModApi *api) {
    if (g_msaa_verify < 0)
        return;
    uint8_t pending = 0;
    api->guest_read_u8(api, GAME_VIDEO_RESET_REQUEST, &pending);
    if (pending)
        return; /* the game has not handled the request yet */
    const uint32_t running = get_u32(GAME_FSAA);
    const int32_t wanted = g_msaa_verify;
    g_msaa_verify = -1;
    char line[160];
    if ((running != 0) == (wanted != AA_OFF)) {
        snprintf(line, sizeof line, "core.nfsmw: anti-aliasing %s confirmed after the renderer reset", k_aa_name[wanted]);
        api->log(api, line);
        return;
    }
    const int64_t effective = running ? (g_msaa_cap_env == 2 ? AA_2X : AA_4X) : AA_OFF;
    snprintf(line, sizeof line,
             "core.nfsmw: anti-aliasing %s was refused by the renderer reset; running %s (setting changed to match)",
             k_aa_name[wanted], k_aa_name[effective]);
    api->log(api, line);
    g_msaa_applied = effective; /* not a new request: the game already runs it */
    api->settings_set(api, "antialiasing", effective);
}
static void msaa_apply(const PopModApi *api, int reset) {
    msaa_migrate(api);
    if (reset)
        msaa_verify(api);
    const int64_t want = aa_effective();
    if (want == g_msaa_applied)
        return;
    g_msaa_applied = want;
    const uint32_t fsaa = want != AA_OFF ? 1u : 0u;
    const int count_changed = msaa_cap_env(want) && fsaa; /* 2x <-> 4x: the same game value, another count */
    if (get_u32(GAME_FSAA) == fsaa && !count_changed)
        return;
    api->guest_write_u32(api, GAME_FSAA, fsaa);
    if (reset) {
        api->guest_write_u8(api, GAME_VIDEO_RESET_REQUEST, 1);
        g_msaa_verify = (int32_t)want;
    }
    char line[120];
    snprintf(line, sizeof line, "core.nfsmw: anti-aliasing %s (%s)%s", k_aa_name[want],
             reset ? "renderer reset requested" : "at video load",
             want != aa_setting() ? "; 4x runs as 2x while HDR is on" : "");
    api->log(api, line);
}

#endif
