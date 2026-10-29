                                                                                                                       
//
// The PC world body ___eDisplayFrame (006DE300) draws only eViews[1]: its main block is guarded by P1's activation
// and descriptor (006DEB7A-006DEB89) and names 00919650 as immediates, with zero-origin viewports at 006DEC1A and
// 006DECDD. While widescreen.c's split contract is published (both descriptors installed, split_view_rect), the hook
// below replaces the body: the frame-wide preparation once (tunnel fade, ePreDisplay 00504C50, which advances
// animation state, and the retained culling preparation 006BFDD0 that covers both activated players), one full
// colour/depth/stencil clear, then for P1 (top) and P2 (bottom) the raw world in retail order: per-view setup
// (00769580, 00753D90, weather strength), source shadow map, target bind with the view's rectangle as viewport
// and scissor, 006CFCE0, sky 006DE210,
// scenery StuffScenery 00723FA0 (flags 0x20), RenderWorldModels 007538D0, clans 007268C0, vehicles 00750B10, and the
// late sky layer (ZFUNC 4, technique 8). Each source first captures its road reflection and environment cube.
// Nothing clears the output between passes. Flares, camera overlays, emitters,
// full-quality rain and the sun/query ring run per source in retail order.
// Mirror and NIS scene/face-pixelation effects remain excluded. GPS is the
// retail P1-only navigation singleton (0041A7D0), not a per-player route.
// Each source runs its local post chain and deferred lens rain after the world. Present stays eDisplayFrame's, once per frame.
// Outside split mode the translated body runs unchanged. BOOT_DIAG: `splitworld:` lines (bindings, descriptor ids,
// rectangles, camera positions, per-view draw counts, viewport checks after every stage).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"

#define VIEW_P1 0x00919650u
#define VIEW_P2 0x009196C0u
#define DEVICE 0x00982BDCu
#define CUR_SHADER 0x00982C80u
#define CUR_TARGET 0x00982A20u
#define SH_WORLD 0x0093DE78u          // RenderWorldModels' shader
#define SH_CAR 0x0093DEBCu            // clans and vehicles
#define SKY_FX 0x0093DEC4u            // +0x48 effect, +0x44 handles (+0x18C technique selector)

int split_view_rect(uint32_t view, uint32_t rect[4]);
uint64_t d3d9_draw_count(void);

// This context exists only around main-view effects, never captures. Operand
// hooks below leave all single-player/capture operands and real IDs unchanged.
static uint32_t fx_source, fx_rect[4];
static int rain_phase_used;
static uint32_t *flare_stepped;
static size_t flare_stepped_count, flare_stepped_capacity;
uint32_t split_fx_view(uint32_t original) { return fx_source ? fx_source : original; }
uint32_t split_fx_player(uint32_t original) { return fx_source ? split_capture_player(original) : original; }
uint32_t split_fx_flare_step(uint32_t view, uint32_t car) {
    uint32_t id = M32(view + 4);
    if (!fx_source || (id != 1 && id != 2)) return id;
    // 00744EFF only tests whether to advance the car's WORLD-space trail ring.
    // The first eligible source steps it; both sources render that same history.
    for (size_t i = 0; i < flare_stepped_count; ++i) if (flare_stepped[i] == car) return 0;
    if (flare_stepped_count == flare_stepped_capacity) {
        size_t n = flare_stepped_capacity ? 2 * flare_stepped_capacity : 128;
        uint32_t *p = realloc(flare_stepped, n * sizeof *p);
        if (!p) return id; // retain retail behaviour on optional host allocation failure
        flare_stepped = p; flare_stepped_capacity = n;
    }
    flare_stepped[flare_stepped_count++] = car;
    return id;
}
uint32_t split_fx_dimension(uint32_t original, unsigned axis) {
    return fx_source ? fx_rect[2 + axis] : original;
}
double split_fx_rain_dt(double dt) {
    if (!fx_source) return dt;
    if (rain_phase_used) return 0;
    rain_phase_used = 1;
    return dt;
}
float split_fx_rain_scale(float original) { return fx_source ? 1.f : original; }
uint32_t split_fx_drop_count(uint32_t original) { return fx_source ? 20 : original; }

typedef struct {
    uint32_t object, device, camera, rain, target, depth, width, height;
    int valid;
} SplitSun;
static SplitSun suns[2];
static void sun_release(Cpu *c, SplitSun *s) {
    if (s->valid) { c->ecx = s->object; call_guest(c, 0x006C0130u, 0, NULL); }
    s->valid = 0;
}
static void sun_mode(Cpu *c, int active) {
    if (!active) for (unsigned i = 0; i < 2; ++i) sun_release(c, &suns[i]);
}
static uint32_t sun_object(Cpu *c, uint32_t view, const uint32_t r[4]) {
    SplitSun *s = &suns[view == VIEW_P2];
    uint32_t d = M32(view + 0x60), dv = M32(DEVICE), cam = M32(view + 0x40), rain = M32(view + 0x68);
    if (!s->object) s->object = galloc(0x58);
    if (!s->valid || s->device != dv || s->camera != cam || s->rain != rain ||
        s->target != M32(d + 8) || s->depth != M32(d + 12) || s->width != r[2] || s->height != r[3]) {
        sun_release(c, s);
        memset(G + s->object, 0, 0x58);
        c->ecx = s->object; call_guest(c, 0x006C00D0u, 0, NULL); // four NEW query handles, never cloned
        s->device = dv; s->camera = cam; s->rain = rain;
        s->target = M32(d + 8); s->depth = M32(d + 12); s->width = r[2]; s->height = r[3]; s->valid = 1;
    }
    // Only configuration is shared. Position, smoothing, cursor and handles stay local.
    memcpy(G + s->object, G + 0x0093E018u, 16);
    return s->object;
}

// Only operand reads use these aliases. Actual eView/descriptor IDs stay 1/2.
static uint32_t shadow_source, shadow_cull;
static int shadow_split(void) {
    return split_view_rect(VIEW_P1, NULL) && split_view_rect(VIEW_P2, NULL);
}
uint32_t split_shadow_id(uint32_t object) {
    uint32_t id = M32(object + 4);
    if (id == 2 && shadow_split() && (object == VIEW_P2 || object == M32(VIEW_P2 + 0x60))) return 1;
    return id;
}
uint32_t split_shadow_player_plat(void) {
    uint32_t view = shadow_split() && M32(CUR_TARGET) == M32(VIEW_P2 + 0x60) ? VIEW_P2 : VIEW_P1;
    return M32(view);
}
uint32_t split_shadow_rain(void) {
    return M32((shadow_source && shadow_split() ? shadow_source : VIEW_P1) + 0x68);
}
uint32_t split_shadow_section_camera(uint32_t original) {
    return shadow_source && shadow_split() && original == M32(VIEW_P1 + 0x40)
        ? M32(shadow_source + 0x40) : original;
}

// Called at 006E50C8, after view 15's caster matrix is complete. DoCulling owns
// a shared draw table, so rebuild all entries, not just a shadow list whose new
// allocation could overwrite live receiver lists. No animation/frame prep here.
void split_shadow_casters(Cpu *c) {
    if (!shadow_cull || !shadow_source || !shadow_split()) return;
    Cpu saved = *c;
    uint32_t n = M32(shadow_cull + 0x9C0);
    for (uint32_t i = 0; i < n; ++i) {
        uint32_t entry = shadow_cull + i * 0xD0;
        if (M32(entry + 0x80) != 0x00919C70u) continue;
        uint32_t a[3] = {0x00919C70u, entry, 0x4100};
        call_guest(c, 0x004FAE40u, 3, a);
        c->ecx = shadow_cull; call_guest(c, 0x00731B80u, 0, NULL);
        break;
    }
    *c = saved;
}

static uint32_t devcall(Cpu *c, uint32_t off, int n, const uint32_t *a) {   // a[0] is the device
    return call_guest(c, M32(M32(a[0]) + off), n, a);
}
static void fx_begin(Cpu *c, uint32_t slot, uint32_t passes) {        // the inline shader Begin sequence
    uint32_t sh = M32(slot), dv = M32(DEVICE);
    M32(CUR_SHADER) = sh;
    c->ecx = sh; call_guest(c, M32(M32(sh) + 4), 0, NULL);
    uint32_t d[2] = {dv, M32(sh + 0x4C)}; devcall(c, 0x15C, 2, d);   // SetVertexDeclaration
    uint32_t e = M32(sh + 0x48), b[3] = {e, passes, 0};
    call_guest(c, M32(M32(e) + 0xFC), 3, b);
}
static void fx_end(Cpu *c, uint32_t slot) {
    uint32_t sh = M32(slot);
    c->ecx = sh; call_guest(c, M32(M32(sh) + 8), 0, NULL);
    uint32_t e = M32(sh + 0x48);
    call_guest(c, M32(M32(e) + 0x10C), 1, &e);
    M32(CUR_SHADER) = 0;
}
static void sky_technique(Cpu *c, uint32_t n) {
    uint32_t fx = M32(SKY_FX), e = M32(fx + 0x48), a[3] = {e, M32(M32(fx + 0x44) + 0x18C), n};
    call_guest(c, M32(M32(e) + 0x68), 3, a);
}
static void render_state(Cpu *c, uint32_t s, uint32_t v) { uint32_t a[3] = {M32(DEVICE), s, v}; devcall(c, 0xE4, 3, a); }

// Target, depth, viewport and scissor for one view. scratch: 24 bytes viewport + 16 bytes rect.
static void bind_view(Cpu *c, uint32_t desc, const uint32_t r[4], uint32_t scratch) {
    uint32_t dv = M32(DEVICE);
    uint32_t t[3] = {dv, 0, M32(desc + 8)}; devcall(c, 0x94, 3, t);  // resets the viewport to the whole target
    uint32_t z[2] = {dv, M32(desc + 12)}; devcall(c, 0x9C, 2, z);
    uint32_t vp[6] = {r[0], r[1], r[2], r[3], 0, 0x3F800000u};
    memcpy(G + scratch, vp, 24);
    uint32_t v[2] = {dv, scratch}; devcall(c, 0xBC, 2, v);
    uint32_t sc[4] = {r[0], r[1], r[0] + r[2], r[1] + r[3]};
    memcpy(G + scratch + 24, sc, 16);
    uint32_t s[2] = {dv, scratch + 24}; devcall(c, 0x12C, 2, s);
    render_state(c, 174, 1);                                        // D3DRS_SCISSORTESTENABLE
    M32(CUR_TARGET) = desc; M32(CUR_SHADER) = 0;
}

static uint32_t viewport_moved;                                   // diag: stages that left another viewport
static void check_stage(Cpu *c, const uint32_t r[4], uint32_t scratch, const char *stage, unsigned view) {
    if (!diag_on) return;
    uint32_t a[2] = {M32(DEVICE), scratch + 40};
    devcall(c, 0xC0, 2, a);                                         // GetViewport
    if (M32(scratch + 40) != r[0] || M32(scratch + 44) != r[1] || M32(scratch + 48) != r[2] || M32(scratch + 52) != r[3]) {
        if (viewport_moved++ < 16)
            diag_event("splitworld: view %u viewport changed by %s: %u,%u %ux%u (rect %u,%u %ux%u)", view, stage,
                       M32(scratch + 40), M32(scratch + 44), M32(scratch + 48), M32(scratch + 52), r[0], r[1], r[2], r[3]);
    }
}

static void camera_overlay(Cpu *c, uint32_t view, uint32_t scratch) {
    uint32_t db = M32(view + 0x64);
    if (M32(0x00925E90u) != 6 || !db || !M32(db + 0x30)) return;
    // 006DEFDC..006DF1C6: the selected ScreenEffectDB's four world-space corners.
    // ePoly: positions at +0..+0x3C, UVs +0x40..+0x5C, colours +0x80, flags +0x90.
    uint32_t poly = scratch + 96;
    memset(G + poly, 0, 0x94);
    static const unsigned corners[4] = {0, 16, 48, 32};
    for (unsigned k = 0; k < 4; ++k) {
        memcpy(G + poly + corners[k], G + db + 0x14C + 12 * k, 12);
        M32(poly + 0x80 + 4 * k) = 0xFFFFFFFFu;
    }
    MF32(poly + 0x48) = MF32(poly + 0x50) = MF32(poly + 0x54) = MF32(poly + 0x5C) = 1.f;
    fx_begin(c, 0x0093DE94u, scratch + 64);
    uint32_t a[4] = {poly, 0, 0x00987AB0u, 0};
    call_guest(c, 0x006D1A40u, 4, a);
    fx_end(c, 0x0093DE94u);
}

static void view_flares(Cpu *c, uint32_t view, uint32_t scratch) {
    if (!M32(0x009017E4u)) return;
    // Retail PC flare order, also used by the port's X360 post treatment.
    // g_X360EffectsEnable selects a different legacy downsample path; it is not
    // the port's NFS_POSTFX selection and is not changed here.
    fx_begin(c, SH_CAR, scratch + 64);
    render_state(c, 14, 0);
    uint32_t a[3] = {view, 0, 0};
    call_guest(c, 0x00505E80u, 2, a);
    call_guest(c, 0x00750B00u, 3, a);
    call_guest(c, 0x00744840u, 2, a);
    call_guest(c, 0x00507770u, 1, a);
    call_guest(c, 0x00738860u, 2, a);
    render_state(c, 14, 1);
    fx_end(c, SH_CAR);
}

static void view_particles(Cpu *c, uint32_t view, uint32_t scratch) {
    if (M32(0x009017ECu)) {
        fx_begin(c, 0x0093DEC0u, scratch + 64);
        c->ecx = 0x009166C0u; call_guest(c, 0x00503D00u, 1, &view);
        fx_end(c, 0x0093DEC0u);
        if (M32(0x00925E90u) == 6 && M32(view + 0x68)) {
            fx_begin(c, 0x0093DEC0u, scratch + 64);
            render_state(c, 14, 0);
            c->ecx = M32(view + 0x68); call_guest(c, 0x00758100u, 0, NULL);
            render_state(c, 14, 1);
            fx_end(c, 0x0093DEC0u);
        }
    }
}

static void world_pass(Cpu *c, uint32_t view, const uint32_t r[4], uint32_t cull, uint32_t scratch, unsigned i) {
    shadow_source = view; shadow_cull = cull;
    uint32_t desc = M32(view + 0x60), rain = M32(view + 0x68);
    uint32_t a1 = M32(rain + 0x290);
    c->ecx = M32(0x009B392Cu); call_guest(c, 0x00769580u, 1, &a1);
    uint32_t a2[2] = {view, 0};
    c->ecx = 0x009AE364u; call_guest(c, 0x00753D90u, 2, a2);
    call_guest(c, 0x006C83D0u, 0, NULL);                            // this source's weather strength
    bind_view(c, desc, r, scratch);
    split_capture_begin(c, view, r, cull);                          // source matrices/culling -> captures -> receivers
    if (M32(0x009017C4u)) {
        uint32_t a[2] = {0x00919960u, 0x004FAEE0u};
        c->ecx = cull; call_guest(c, 0x00724230u, 2, a);
    }
    call_guest(c, 0x006E2F50u, 0, NULL);
    bind_view(c, desc, r, scratch);                                  // 006DEBC2
    if ((int32_t)M32(0x00901830u) > 0 && MF32(0x008FAE68u) >= MF32(0x008FAE50u)) {
        render_state(c, 174, 0);                                    // the whole map, not the split rectangle
        uint32_t a[2] = {view, cull};
        call_guest(c, 0x006E54E0u, 2, a);
    }
    call_guest(c, 0x006E2F50u, 0, NULL);
    bind_view(c, desc, r, scratch);                                  // 006DEC85
    call_guest(c, 0x006CFCE0u, 0, NULL);
    sky_technique(c, 7);
    uint32_t sky[3] = {view, M32(0x008F92E4u), 0};
    call_guest(c, 0x006DE210u, 3, sky);
    check_stage(c, r, scratch, "sky", i);
    uint32_t st[2] = {view, 0x20};
    c->ecx = cull; call_guest(c, 0x00723FA0u, 2, st);                 // GrandSceneryCullInfo::StuffScenery
    call_guest(c, 0x006E2F50u, 0, NULL);
    check_stage(c, r, scratch, "scenery", i);
    uint32_t va[2] = {view, 0};
    fx_begin(c, SH_WORLD, scratch + 64); call_guest(c, 0x007538D0u, 2, va); fx_end(c, SH_WORLD);
    call_guest(c, 0x006E2F50u, 0, NULL);
    check_stage(c, r, scratch, "world models", i);
    fx_begin(c, SH_CAR, scratch + 64); call_guest(c, 0x007268C0u, 1, &view); fx_end(c, SH_CAR);
    fx_begin(c, SH_CAR, scratch + 64); call_guest(c, 0x00750B10u, 2, va); fx_end(c, SH_CAR);
    call_guest(c, 0x006E2F50u, 0, NULL);
    check_stage(c, r, scratch, "vehicles", i);
    fx_source = view; memcpy(fx_rect, r, sizeof fx_rect);
    camera_overlay(c, view, scratch);
    // Gps::Render itself accepts only ID1 and a single global navigation route;
    // it is not a second player's route. Retain its retail camera/route contract.
    call_guest(c, 0x006CFCE0u, 0, NULL);
    if (view == VIEW_P1) call_guest(c, 0x0041AD20u, 1, &view);
    view_flares(c, view, scratch);
    check_stage(c, r, scratch, "flares/overlay", i);
    sky_technique(c, 8);                                             // 006DF439: the late sky layer
    M8(0x00982BADu) = 1;
    render_state(c, 0x17, 4);
    uint32_t late[3] = {view, M32(0x008F92E4u), 0};
    call_guest(c, 0x006DE210u, 3, late);
    M8(0x00982BADu) = 0;
    sky_technique(c, 7);
    check_stage(c, r, scratch, "late sky", i);
    view_particles(c, view, scratch);
    c->ecx = sun_object(c, view, r); call_guest(c, 0x006D04E0u, 1, &view);
    check_stage(c, r, scratch, "emitters/rain/sun", i);
    if (diag_on && M32(0x009885B8u) % 120 == 0) {
        uint32_t s = suns[view == VIEW_P2].object;
        diag_event("splitfx: view %u rain %08X drops %u intensity %.4f wet %.4f tunnel %u sun %08X visibility %.4f cursor %u queries %08X/%08X/%08X/%08X",
            i, rain, rain ? M32(rain) : 0, rain ? MF32(rain+0x28C) : 0.f, rain ? MF32(rain+0x36A0) : 0.f,
            rain ? M32(rain+0x23C) : 0, s, MF32(s+0x18), M32(s+0x14),
            M32(s+0x24),M32(s+0x28),M32(s+0x2C),M32(s+0x30));
    }
    split_post_view(c,view,r);
    fx_source = 0;
    check_stage(c,r,scratch,"post/lens",i);
    split_capture_end(c);                                          // flush receivers before switching ownership
    shadow_source = shadow_cull = 0;
}

static void split_world(Cpu *c, const uint32_t r[2][4]) {
    uint32_t sp = c->esp, area = (sp - 0xAE0u) & ~15u;           // cull info 0x9C4, then scratch + overlay ePoly
    uint32_t cull = area, scratch = area + 0x9D0u;
    c->esp = area;
    rain_phase_used = 0;
    flare_stepped_count = 0;
    // 006DE30C-006DE3F5: the frame-wide preparation, once.
    if (!M32(0x009017D0u)) M32(0x00982BA0u) = 1;
    M32(0x00982B9Cu) = M32(0x00982B34u) ? 0x800u : 0;
    uint32_t tun[2] = {VIEW_P1, 1};
    uint32_t in = call_guest(c, 0x0073CFE0u, 2, tun);
    M32(0x00982BA4u) = in;
    double fade = in ? (double)MF32(0x008F9340u) * MF32(0x00925970u) + MF32(0x00982BA8u)
                     : MF32(0x00982BA8u) - (double)MF32(0x008F9344u) * MF32(0x00925970u);
    MF32(0x00982BA8u) = (float)fade;
    if (in && fade > MF32(0x0089096Cu)) M32(0x00982BA8u) = 0x3F800000u;
    if (!in && fade < MF32(0x00890968u)) M32(0x00982BA8u) = 0;
    M32(0x008EC2B8u) = M32(0x00901830u) == 0;
    call_guest(c, 0x006D6CF0u, 0, NULL);
    M32(CUR_SHADER) = 0;
    call_guest(c, 0x00504C50u, 0, NULL);                             // ePreDisplay
    c->ecx = M32(0x009B392Cu); call_guest(c, 0x00769360u, 0, NULL);
    M32(cull + 0x9C0u) = 0;
    call_guest(c, 0x006BFDD0u, 1, &cull);                            // culling for every activated view
    call_guest(c, 0x006E2F50u, 0, NULL);
    M32(0x008FAE6Cu) = 2;
    for (uint32_t a = 0x0093DE38u; a <= 0x0093DE54u; a += 4) M32(a) = 0;   // 006DEB06-006DEB74
    for (uint32_t a = 0x009828A0u; a <= 0x009828BCu; a += 4) M32(a) = 0;
    M32(0x00982B90u) = M32(0x00982B94u) = M32(0x00982B98u) = 0;

    uint32_t dv = M32(DEVICE), q[3] = {dv, 174, scratch + 72};
    devcall(c, 0xE8, 3, q);                                         // GetRenderState(SCISSORTESTENABLE)
    uint32_t scissor_on = M32(scratch + 72), sq[2] = {dv, scratch + 76};
    devcall(c, 0x130, 2, sq);                                       // GetScissorRect
    uint32_t saved_rect[4]; memcpy(saved_rect, G + scratch + 76, 16);

    uint32_t d1 = M32(VIEW_P1 + 0x60);
    uint32_t t[3] = {dv, 0, M32(d1 + 8)}; devcall(c, 0x94, 3, t);
    uint32_t z[2] = {dv, M32(d1 + 12)}; devcall(c, 0x9C, 2, z);
    render_state(c, 174, 0);
    uint32_t cl[7] = {dv, 0, 0, 7, 0, 0x3F800000u, 0};
    devcall(c, 0xAC, 7, cl);                                        // the one clear, whole target

    static uint32_t logged;
    for (unsigned i = 0; i < 2; ++i) {
        uint32_t view = i ? VIEW_P2 : VIEW_P1;
        uint64_t before = diag_on ? d3d9_draw_count() : 0;
        world_pass(c, view, r[i], cull, scratch, i + 1);
        if (diag_on && logged < 8) {
            uint32_t desc = M32(view + 0x60), cam = M32(view + 0x40);
            diag_event("splitworld: view %u eView %08X id %u desc %08X id %u target %08X depth %08X rect %u,%u %ux%u "
                       "scissor on, camera %08X at %.2f %.2f %.2f, draws %llu", i + 1, view, M32(view + 4), desc,
                       M32(desc + 4), M32(desc + 8), M32(desc + 12), r[i][0], r[i][1], r[i][2], r[i][3], cam,
                       cam ? MF32(cam + 48) : 0.f, cam ? MF32(cam + 52) : 0.f, cam ? MF32(cam + 56) : 0.f,
                       (unsigned long long)(d3d9_draw_count() - before));
        }
    }
    if (diag_on && logged < 8 && ++logged == 8) diag_event("splitworld: (further frames not logged)");

    // Leave P1's whole target bound, as the retail body does, and the scissor as found.
    t[2] = M32(d1 + 8); devcall(c, 0x94, 3, t);
    memcpy(G + scratch + 76, saved_rect, 16); devcall(c, 0x12C, 2, sq);
    render_state(c, 174, scissor_on);
    // 006DF59E-006DF8C9 without the screen effects: frame counters, effect flags off, textures unbound.
    ++M32(0x009885B8u);
    M8(0x00982B8Cu) = 0;
    M8(0x00982B26u) = M8(0x00982B27u) = M8(0x00982B29u) = M8(0x00982B2Au) = 0;
    for (uint32_t s = 0; s < 8; ++s) { uint32_t a[3] = {dv, s, 0}; devcall(c, 0x104, 3, a); }
    c->esp = sp + 4;                                                // ret
}

void f_006DE300_orig(Cpu *c);
void f_006DE300(Cpu *c) {                // ___eDisplayFrame: the world body
    uint32_t r[2][4];
    if (!split_view_rect(VIEW_P1, r[0]) || !split_view_rect(VIEW_P2, r[1])) {
        Cpu saved = *c; sun_mode(c, 0); *c = saved;
        split_post_mode(0);
        split_capture_mode(c, 0); f_006DE300_orig(c); return;
    }
    split_post_mode(1);
    split_capture_mode(c, 1);
    split_world(c, r);
}
