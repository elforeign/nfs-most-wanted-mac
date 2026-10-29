// Split-only road/cube ownership. Retail single-player resources and calls are untouched.
// Each scope installs one bank until its last receiver has flushed, then restores the singleton.
#include "host.h"
#include "funcs.h"
#include "materials.h"
uint64_t d3d9_draw_count(void);

#define CAP_P1 0x00919650u
#define CAP_P2 0x009196C0u
#define CAP_ROAD 0x009197A0u
#define CAP_FACE 0x00919DC0u
#define CAP_DEVICE 0x00982BDCu
#define CAP_RT 0x00982A20u
#define CAP_SHADER 0x00982C80u
#define CAP_REFL 0x00982A68u
#define CAP_CUBE 0x00982A30u

enum { CAP_CAMERA = 0x2C0, CAP_PLATFORM = 0x1A0, CAP_VIEWS = 7 };
typedef struct {
    uint8_t view[0x70], camera[CAP_CAMERA], platform[CAP_PLATFORM], desc[0x1C];
} CaptureView;
typedef struct {
    uint32_t reflection[5], cube[13]; // surface/texture/blur surface/blur texture/depth; cube/6 surfaces/6 depths
    uint32_t w, h, edge, road_level, road_max, env_level, camera, mover, player, generation;
    unsigned valid_faces, road_valid, initialized;
    CaptureView views[CAP_VIEWS];
    uint8_t orientation[64], target[12];
} CaptureBank;
typedef struct {
    uint32_t reflection[5], cube[13], target, shader;
    CaptureView views[CAP_VIEWS];
    uint8_t orientation[64], env_target[12];
} CaptureSingleton;
typedef struct {
    uint32_t rt, depth, vp[6], scissor[4], scissor_on, z_on, z_write, target, shader;
} CaptureDevice;

static CaptureBank capture_banks[2];
static CaptureSingleton capture_saved;
static uint32_t capture_source, capture_generation = 1;
static int capture_active;
typedef struct {
    uint64_t next, road_draws, cube_draws;
    unsigned sample, road_ran, faces, receivers[2][2], stages[2];
} CaptureDiag;
static CaptureDiag capture_diag[2];

void split_capture_receiver_texture(unsigned stage, uint32_t texture) {
    if (!diag_on || !capture_source || !texture || M32(CAP_RT) != M32(capture_source + 0x60)) return;
    CaptureDiag *d = &capture_diag[capture_source == CAP_P2];
    if (!d->sample) return;
    for (unsigned bank = 0; bank < 2; ++bank) {
        CaptureBank *b = &capture_banks[bank];
        if (texture == b->reflection[1] || texture == b->reflection[3]) {
            ++d->receivers[0][bank]; d->stages[0] |= 1u << stage;
        }
        if (texture == b->cube[0]) { ++d->receivers[1][bank]; d->stages[1] |= 1u << stage; }
    }
}

uint32_t split_capture_source(void) { return capture_source; }
int split_capture_reflection_pending(void) {
    if (!capture_source) return 0;
    CaptureBank *b = &capture_banks[capture_source == CAP_P2];
    return b->reflection[0] && b->road_level < 2 && !b->road_valid && mat_water_enabled();
}
uint32_t split_capture_address(uint32_t original) {
    if (capture_source && (original == CAP_P1 + 0x40 || original == CAP_P1 + 0x44 || original == CAP_P1 + 0x68))
        return capture_source + original - CAP_P1;
    return original;
}
static uint32_t cap_local_player(uint32_t source) {
    // First(1), 00425200: local-player vector +4 data, +0xC count.
    // LocalPlayer::GetSettings (006E99A0) reads the IPlayer's logical slot at
    // +0x1C. Use that slot, preserving ownership after removal/reinsertion.
    uint32_t list = M32(0x0092D87Cu), count = M32(0x0092D884u), slot = source == CAP_P2;
    for (uint32_t i = 0; list && i < count; ++i) {
        uint32_t player = M32(list + i * 4);
        if (player && M32(player + 0x1C) == slot) return player;
    }
    return 0;
}
uint32_t split_capture_player(uint32_t original) {
    if (capture_source) {
        uint32_t player = cap_local_player(capture_source);
        if (player) return player;
    }
    return original;
}
static uint32_t capture_view(unsigned i) { return i ? CAP_FACE + (i - 1) * 0x70u : CAP_ROAD; }
static uint32_t cap_com(Cpu *c, uint32_t object, uint32_t off, int n, const uint32_t *a) {
    return call_guest(c, M32(M32(object) + off), n, a);
}
static uint32_t cap_dev(Cpu *c, uint32_t off, int n, const uint32_t *a) {
    return cap_com(c, M32(CAP_DEVICE), off, n, a);
}
static void cap_release(Cpu *c, uint32_t object) {
    if (object) cap_com(c, object, 8, 1, &object);
}
static void cap_flush(Cpu *c) { call_guest(c, 0x006E2F50u, 0, NULL); }
static void cap_state(Cpu *c, uint32_t state, uint32_t value) {
    uint32_t a[3] = {M32(CAP_DEVICE), state, value}; cap_dev(c, 0xE4, 3, a);
}
static void cap_views(CaptureView views[CAP_VIEWS], int load) {
    for (unsigned i = 0; i < CAP_VIEWS; ++i) {
        uint32_t v = capture_view(i);
        // The live view owns these pointers. Restore view bytes before reading them on load.
        if (load) memcpy(G + v, views[i].view, 0x70);
        uint32_t cam = M32(v + 0x40), plat = M32(v), desc = M32(v + 0x60);
        if (load) {
            // View 4 aliases a live player Camera. Matrix construction only reads it;
            // replaying its snapshot would rewind P1's simulation every frame.
            if (cam && i) memcpy(G + cam, views[i].camera, CAP_CAMERA);
            if (plat) memcpy(G + plat, views[i].platform, CAP_PLATFORM);
            if (desc) memcpy(G + desc, views[i].desc, 0x1C);
        } else {
            memcpy(views[i].view, G + v, 0x70);
            if (cam) memcpy(views[i].camera, G + cam, CAP_CAMERA);
            if (plat) memcpy(views[i].platform, G + plat, CAP_PLATFORM);
            if (desc) memcpy(views[i].desc, G + desc, 0x1C);
        }
    }
}
static void cap_singleton(int load) {
    if (load) {
        memcpy(G + CAP_REFL, capture_saved.reflection, sizeof capture_saved.reflection);
        memcpy(G + CAP_CUBE, capture_saved.cube, sizeof capture_saved.cube);
        cap_views(capture_saved.views, 1);
        memcpy(G + 0x00987FC0u, capture_saved.orientation, 64);
        memcpy(G + 0x009B3790u, capture_saved.env_target, 12);
        M32(CAP_RT) = capture_saved.target; M32(CAP_SHADER) = capture_saved.shader;
    } else {
        memcpy(capture_saved.reflection, G + CAP_REFL, sizeof capture_saved.reflection);
        memcpy(capture_saved.cube, G + CAP_CUBE, sizeof capture_saved.cube);
        cap_views(capture_saved.views, 0);
        memcpy(capture_saved.orientation, G + 0x00987FC0u, 64);
        memcpy(capture_saved.env_target, G + 0x009B3790u, 12);
        capture_saved.target = M32(CAP_RT); capture_saved.shader = M32(CAP_SHADER);
    }
}
static void cap_device_save(Cpu *c, CaptureDevice *s, uint32_t tmp) {
    uint32_t dv = M32(CAP_DEVICE), a[3] = {dv, 0, tmp};
    M32(tmp) = 0; cap_dev(c, 0x98, 3, a); s->rt = M32(tmp);
    uint32_t b[2] = {dv, tmp};
    M32(tmp) = 0; cap_dev(c, 0xA0, 2, b); s->depth = M32(tmp);
    cap_dev(c, 0xC0, 2, b); memcpy(s->vp, G + tmp, 24);
    cap_dev(c, 0x130, 2, b); memcpy(s->scissor, G + tmp, 16);
    a[1] = 174; cap_dev(c, 0xE8, 3, a); s->scissor_on = M32(tmp);
    a[1] = 7; cap_dev(c, 0xE8, 3, a); s->z_on = M32(tmp);
    a[1] = 14; cap_dev(c, 0xE8, 3, a); s->z_write = M32(tmp);
    s->target = M32(CAP_RT); s->shader = M32(CAP_SHADER);
}
static void cap_device_restore(Cpu *c, const CaptureDevice *s, uint32_t tmp) {
    cap_flush(c);
    uint32_t dv = M32(CAP_DEVICE), a[3] = {dv, 0, s->rt}; cap_dev(c, 0x94, 3, a);
    uint32_t b[2] = {dv, s->depth}; cap_dev(c, 0x9C, 2, b);
    b[1] = tmp; memcpy(G + tmp, s->vp, 24); cap_dev(c, 0xBC, 2, b);
    memcpy(G + tmp, s->scissor, 16); cap_dev(c, 0x12C, 2, b);
    cap_state(c, 174, s->scissor_on); cap_state(c, 7, s->z_on); cap_state(c, 14, s->z_write);
    M32(CAP_RT) = s->target; M32(CAP_SHADER) = s->shader;
    cap_release(c, s->rt); cap_release(c, s->depth); // Get* owns references, including texture surfaces
}
static void cap_drop(Cpu *c, CaptureBank *b) {
    // Release surface references before their parent textures.
    cap_release(c, b->reflection[0]); cap_release(c, b->reflection[2]); cap_release(c, b->reflection[4]);
    cap_release(c, b->reflection[1]); cap_release(c, b->reflection[3]);
    for (unsigned i = 1; i < 13; ++i) cap_release(c, b->cube[i]);
    cap_release(c, b->cube[0]);
    memset(b, 0, sizeof *b);
}
void split_capture_reset(Cpu *c) {
    Cpu saved = *c;
    // Reset/destruction is outside a receiver scope; the retail resources are installed here.
    for (unsigned i = 0; i < 2; ++i) cap_drop(c, &capture_banks[i]);
    ++capture_generation; capture_source = 0;
    mat_water_reset();
    *c = saved;
}
void split_capture_mode(Cpu *c, int active) {
    if (capture_active && !active) split_capture_reset(c);
    capture_active = active;
}
void f_006BD620_orig(Cpu *c);
void f_006BD620(Cpu *c) { // device reset/destruction, before the retail resource release
    split_capture_reset(c);
    f_006BD620_orig(c);
}
static int cap_texture(Cpu *c, uint32_t w, uint32_t h, uint32_t fmt, uint32_t tmp, uint32_t *tex, uint32_t *surf) {
    uint32_t a[9] = {M32(CAP_DEVICE), w, h, 1, 1, fmt, 0, tmp, 0};
    M32(tmp) = 0;
    if (cap_dev(c, 0x5C, 9, a) || !(*tex = M32(tmp))) return 0;
    uint32_t s[3] = {*tex, 0, tmp}; M32(tmp) = 0;
    return !cap_com(c, *tex, 0x48, 3, s) && (*surf = M32(tmp));
}
static int cap_depth(Cpu *c, uint32_t w, uint32_t h, uint32_t fmt, uint32_t tmp, uint32_t *depth) {
    uint32_t a[9] = {M32(CAP_DEVICE), w, h, fmt, 0, 0, 0, tmp, 0}; M32(tmp) = 0;
    return !cap_dev(c, 0x74, 9, a) && (*depth = M32(tmp));
}
static int cap_allocate(Cpu *c, CaptureBank *b, uint32_t tmp) {
    if (b->road_level || mat_water_enabled()) {
        if (!cap_texture(c, b->w, b->h, M32(0x008F9000u), tmp, &b->reflection[1], &b->reflection[0]) ||
            !cap_depth(c, b->w, b->h, M32(0x008F9004u), tmp, &b->reflection[4])) return 0;
        if (b->road_max == 3 && b->road_level &&
            !cap_texture(c, b->w, b->h, M32(0x008F9000u), tmp, &b->reflection[3], &b->reflection[2])) return 0;
    }
    if (b->env_level) {
        uint32_t a[8] = {M32(CAP_DEVICE), b->edge, 1, 1, M32(0x008F8FF8u), 0, tmp, 0}; M32(tmp) = 0;
        if (cap_dev(c, 0x64, 8, a) || !(b->cube[0] = M32(tmp))) return 0;
        for (unsigned i = 0; i < 6; ++i) {
            uint32_t s[4] = {b->cube[0], i, 0, tmp}; M32(tmp) = 0;
            if (cap_com(c, b->cube[0], 0x48, 4, s) || !(b->cube[1 + i] = M32(tmp)) ||
                !cap_depth(c, b->edge, b->edge, M32(0x008F8FFCu), tmp, &b->cube[7 + i])) return 0;
        }
    }
    return 1;
}
static void cap_matrices(Cpu *c, uint32_t view) {
    uint32_t a[5] = {view, 0, 0x466A6000u, 0, 1};
    c->ecx = M32(view); call_guest(c, 0x006CF400u, 5, a);
}
static void cap_shader(Cpu *c, int begin, uint32_t tmp) {
    uint32_t sh = M32(0x0093DEBCu), fx = M32(sh + 0x48);
    c->ecx = sh; call_guest(c, M32(M32(sh) + (begin ? 4 : 8)), 0, NULL);
    if (begin) {
        M32(CAP_SHADER) = sh;
        uint32_t d[2] = {M32(CAP_DEVICE), M32(sh + 0x4C)}; cap_dev(c, 0x15C, 2, d);
        uint32_t a[3] = {fx, tmp, 0}; cap_com(c, fx, 0xFC, 3, a);
    } else {
        cap_com(c, fx, 0x10C, 1, &fx); M32(CAP_SHADER) = 0;
    }
}
static void cap_sky(Cpu *c, uint32_t view, uint32_t layer, uint32_t colour) {
    uint32_t old[5] = {M32(0x008FADE0u), M32(0x008FADF0u), M32(0x008FADF4u), M32(0x008FADF8u), M32(0x008FADFCu)};
    M32(0x008FADE0u) = colour;
    uint32_t strength = layer == 4 ? M32(0x008F92E0u) :
        M8(M32(0x0093DEC4u) + 0x40) ? colour : 0x3F000000u;
    uint32_t a[3] = {view, layer, strength};
    call_guest(c, 0x006DB310u, 3, a); cap_flush(c);
    M32(0x008FADE0u) = old[0]; M32(0x008FADF0u) = old[1]; M32(0x008FADF4u) = old[2];
    M32(0x008FADF8u) = old[3]; M32(0x008FADFCu) = old[4];
}
static void cap_bind(Cpu *c, uint32_t view) {
    cap_flush(c); cap_state(c, 174, 0); // retail clears the whole offscreen target
    uint32_t a[2] = {M32(view + 0x60), 1}; call_guest(c, 0x006BD3B0u, 2, a);
    uint32_t rain = M32(capture_source + 0x68), strength = M32(rain + 0x290);
    c->ecx = M32(0x009B392Cu); call_guest(c, 0x00769580u, 1, &strength);
}
void split_capture_begin(Cpu *c, uint32_t source, const uint32_t rect[4], uint32_t cull) {
    Cpu saved = *c;
    uint32_t tmp = (c->esp - 0x100u) & ~15u; c->esp = tmp;
    cap_flush(c); cap_singleton(0); capture_source = source;
    CaptureBank *b = &capture_banks[source == CAP_P2];
    CaptureDiag *diag = NULL;
    if (diag_on) {
        diag = &capture_diag[source == CAP_P2];
        uint64_t now = host_ticks(); diag->sample = now >= diag->next;
        if (diag->sample) {
            memset(diag->receivers, 0, sizeof diag->receivers); memset(diag->stages, 0, sizeof diag->stages);
            diag->road_draws = diag->cube_draws = 0; diag->road_ran = diag->faces = 0;
            diag->next = now + 1000000000ull;
            // Identify the custom split course so the single-player edge check
            // can use the same event, rather than a different SkipFE default.
            static uint32_t reported_race;
            uint32_t status = M32(0x0091E000u), race = status ? M32(status + 0x1968) : 0;
            if (race && race != reported_race) {
                c->ecx = race; uint32_t id = call_guest(c, 0x005FBA70u, 0, NULL);
                diag_event("splitcapture: event %s parameters %08X", id ? gptr(id) : "?", race);
                reported_race = race;
            }
        }
    }
    uint32_t w, h; x360_reflection_dimensions(rect[2], rect[3], &w, &h);
    uint32_t edge = M32(0x008F8FF4u), level = M32(0x009017D4u), max = M32(0x0090185Cu), env = M32(0x009017B8u);
    uint32_t camera = M32(source + 0x40), mover = M32(source + 0x44);
    uint32_t player = cap_local_player(source);
    if (b->generation != capture_generation || b->w != w || b->h != h || b->edge != edge || b->road_level != level ||
        b->road_max != max || b->env_level != env || b->camera != camera || b->mover != mover || b->player != player) {
        mat_water_reset_view(source);
        cap_drop(c, b);
        b->w = w; b->h = h; b->edge = edge; b->road_level = level; b->road_max = max; b->env_level = env;
        b->camera = camera; b->mover = mover; b->player = player; b->generation = capture_generation;
        memcpy(b->views, capture_saved.views, sizeof b->views);
        memcpy(b->orientation, capture_saved.orientation, 64); memcpy(b->target, capture_saved.env_target, 12);
    }
    if (!b->initialized) {
        b->initialized = cap_allocate(c, b, tmp);
        if (!b->initialized) { cap_drop(c, b); *c = saved; return; }
    }
    // Reflection view 4 normally aliases P1's Camera; point it at the real source without modifying that camera.
    cap_views(b->views, 1);
    M32(CAP_ROAD + 0x40) = camera; M32(CAP_ROAD + 0x68) = M32(source + 0x68);
    memcpy(G + CAP_REFL, b->reflection, sizeof b->reflection);
    memcpy(G + CAP_CUBE, b->cube, sizeof b->cube);
    uint32_t rd = M32(CAP_ROAD + 0x60);
    M32(rd + 8) = b->reflection[0]; M32(rd + 12) = b->reflection[4];
    // MaybeChangeViewMode only activates reflection for retail modes 1/2.
    // Split inherits inactive view/target flags from the FE; activate this
    // scoped capture explicitly, then cap_singleton restores both flags.
    M8(CAP_ROAD + 8) = b->reflection[0] != 0;
    M32(rd + 0x10) = b->reflection[0] != 0;
    M32(rd + 0x14) = w; M32(rd + 0x18) = h;
    if (b->reflection[0]) cap_matrices(c, CAP_ROAD);
    if (b->cube[0]) {
        memcpy(G + 0x00987FC0u, b->orientation, 64); memcpy(G + 0x009B3790u, b->target, 12);
        call_guest(c, 0x00756060u, 0, NULL); // both eye/target selection and the internal basis use this source
        for (unsigned i = 0; i < 6; ++i) {
            uint32_t v = CAP_FACE + i * 0x70u, d = M32(v + 0x60);
            M32(v + 0x68) = M32(source + 0x68);
            M32(d + 8) = b->cube[1 + M32(d) - 5]; M32(d + 12) = b->cube[7 + M32(d) - 5];
            M32(d + 0x14) = M32(d + 0x18) = edge;
            if (!(b->valid_faces & (1u << i))) M8(v + 8) = 1;
            cap_matrices(c, v);
        }
    }
    if (b->reflection[0] || b->cube[0]) {
        // The shared culling table must follow the newly installed matrices, including both source receivers.
        call_guest(c, 0x006BFDD0u, 1, &cull);
        CaptureDevice device; cap_device_save(c, &device, tmp);
        M32(0x008FAE6Cu) = 3;
        if (b->reflection[0] && M8(CAP_ROAD + 8) && M32(rd + 0x10) && x360_water_reflection_level()) {
            uint64_t before = diag && diag->sample ? d3d9_draw_count() : 0;
            cap_bind(c, CAP_ROAD);
            cap_sky(c, CAP_ROAD, 4, M32(0x008F92E0u));
            float wet = MF32(M32(source + 0x68) + 0x36A0);
            if (x360_water_reflection_level() > 1 && x360_water_reflection_wetness(0x006BFEE6u, wet) > .01f) {
                uint32_t a[2] = {CAP_ROAD, 8}; c->ecx = cull; call_guest(c, 0x00723FA0u, 2, a);
                cap_shader(c, 1, tmp); a[1] = 1; call_guest(c, 0x00750B10u, 2, a); cap_shader(c, 0, tmp);
                cap_flush(c);
                if (level == 3 && b->reflection[3]) call_guest(c, 0x006D4960u, 0, NULL);
            }
            cap_flush(c); b->road_valid = 1;
            if (diag && diag->sample) { diag->road_ran = 1; diag->road_draws = d3d9_draw_count() - before; }
        }
        for (unsigned i = 0; b->cube[0] && i < 6; ++i) {
            uint32_t v = CAP_FACE + i * 0x70u, d = M32(v + 0x60);
            if (!M8(v + 8) || !M32(d + 0x10)) continue;
            uint64_t before = diag && diag->sample ? d3d9_draw_count() : 0;
            cap_bind(c, v); cap_state(c, 7, 0); cap_state(c, 14, 0);
            call_guest(c, 0x006C00B0u, 0, NULL); // unbind the receiver cube before sky/scenery
            cap_sky(c, v, 3, M32(M32(0x009B392Cu) + 100));
            cap_state(c, 7, 1); cap_state(c, 14, 1);
            if (M32(0x009017BCu) || (int32_t)M32(0x00901830u) < 2) {
                uint32_t a[2] = {v, M32(0x009017BCu) ? 0 : 0x1000};
                c->ecx = cull; call_guest(c, 0x00723FA0u, 2, a);
            }
            cap_flush(c); b->valid_faces |= 1u << i;
            if (diag && diag->sample) { diag->faces |= 1u << i; diag->cube_draws += d3d9_draw_count() - before; }
        }
        M32(0x008FAE6Cu) = 2;
        cap_device_restore(c, &device, tmp);
    }
    *c = saved;
}
void split_capture_end(Cpu *c) {
    if (!capture_source) return;
    Cpu saved = *c; cap_flush(c);
    CaptureBank *b = &capture_banks[capture_source == CAP_P2];
    if (diag_on) {
        CaptureDiag *d = &capture_diag[capture_source == CAP_P2];
        if (d->sample) diag_event("splitcapture: source %u view %08X camera %08X wet %.4f level %u generation %u "
            "road ran %u valid %u texture %08X blur %08X depth %08X %ux%u draws %llu "
            "cube %08X %ux%u faces %02X valid %02X draws %llu receivers road banks %u/%u stages %X cube banks %u/%u stages %X globals %08X/%08X",
            M32(capture_source + 4), capture_source, M32(capture_source + 0x40), MF32(M32(capture_source + 0x68) + 0x36A0),
            b->road_level, b->generation, d->road_ran, b->road_valid, b->reflection[1], b->reflection[3], b->reflection[4],
            b->w, b->h, (unsigned long long)d->road_draws, b->cube[0], b->edge, b->edge, d->faces, b->valid_faces,
            (unsigned long long)d->cube_draws, d->receivers[0][0], d->receivers[0][1], d->stages[0],
            d->receivers[1][0], d->receivers[1][1], d->stages[1], M32(CAP_REFL + 4), M32(CAP_CUBE));
    }
    if (b->initialized) {
        cap_views(b->views, 0);
        memcpy(b->views[0].view + 0x40, capture_saved.views[0].view + 0x40, 4);
        memcpy(b->orientation, G + 0x00987FC0u, 64); memcpy(b->target, G + 0x009B3790u, 12);
    }
    cap_singleton(1); capture_source = 0;
    *c = saved;
}
