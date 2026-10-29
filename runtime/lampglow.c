// Selective cop-flare context. Called around the original emitter by mirror.c;
// the renderer consumes only immediate draws made inside that call.
#include <math.h>
#include <string.h>
#include "host.h"
#include "settings.h"

static Setting police_glow = {.id = "graphics.lamp_glow", .category = SET_CARS, .order = 37,
    .label = "Police light glow", .help = "Adds a soft red and blue glow around flashing police lamps in the X360 post-processing chain.",
    .type = SET_TOGGLE, .def = 0, .env = "NFS_LAMP_GLOW"};
__attribute__((constructor)) static void lamp_register(void) { settings_add(&police_glow); }

int lampglow_enabled(void) {
    if (postfx_look_variant("lampglowoff")) return 0;
    return police_glow.value || postfx_look_variant("lampglow");
}
static int lamp_type;
static unsigned lamp_owner;
static unsigned submitted[2], draws[2], replayed, refused;
static unsigned owner_submitted[2][2], owner_draws[2][2];
static double intensity[2], emission[2];

void lampglow_begin(Cpu *c) {
    uint32_t view = arg(c, 0), flare = arg(c, 1);
    int t = flare ? (int8_t)M8(flare + 0x2c) : 0;
    // Main cameras' car lamps: never mirror/env-map/reflected sprites.
    unsigned id = view ? M32(view + 4) : 0;
    lamp_type = (id == 1 || id == 2) && arg(c, 4) == 0 && (t == 5 || t == 6) ? t : 0;
    lamp_owner = lamp_type ? id : 0;
    if (diag_on && lamp_type) {
        float f = MF32(c->esp + 16);
        ++submitted[t - 5];
        ++owner_submitted[lamp_owner - 1][t - 5];
        if (isfinite(f)) intensity[t - 5] += fmaxf(0, f);
    }
}
void lampglow_end(void) { lamp_type = 0; lamp_owner = 0; }
int lampglow_type(void) { return lamp_type; }
unsigned lampglow_owner(void) { return lamp_owner; }
void lampglow_draw(float rgb_sum) {
    if (!diag_on || !lamp_type) return;
    ++draws[lamp_type - 5];
    ++owner_draws[lamp_owner - 1][lamp_type - 5];
    emission[lamp_type - 5] += rgb_sum;
}
void lampglow_replayed(int ok) { if (diag_on) { if (ok) ++replayed; else ++refused; } }   // enabled draws: replayed into the capture / refused by the draw-state check
static unsigned batches, batched, flush_why[4], rec_refused;
void lampglow_batch(int why, unsigned replays, unsigned refused_msgs) {
    if (!diag_on) return;
    ++batches; batched += replays; rec_refused += refused_msgs;
    if (why >= 0 && why < 4) ++flush_why[why];
}
void lampglow_diag_second(void) {
    diag_event("lampviews: P1 red/blue submitted=%u/%u draws=%u/%u; P2 red/blue submitted=%u/%u draws=%u/%u",
        owner_submitted[0][0], owner_submitted[0][1], owner_draws[0][0], owner_draws[0][1],
        owner_submitted[1][0], owner_submitted[1][1], owner_draws[1][0], owner_draws[1][1]);
    memset(owner_submitted, 0, sizeof owner_submitted); memset(owner_draws, 0, sizeof owner_draws);
    diag_event("lampglow: on=%d type5 submitted=%u draws=%u flash=%.4f emissive=%.4f type6 submitted=%u draws=%u flash=%.4f emissive=%.4f (emissive=sum vertex RGB*alpha / vertices, pre-texture/depth) replayed=%u refused=%u batches=%u batched=%u flush pass/depthwrite/ztarget/bloom=%u/%u/%u/%u rec_refused=%u",
        lampglow_enabled(), submitted[0], draws[0], intensity[0], emission[0], submitted[1], draws[1], intensity[1], emission[1], replayed, refused,
        batches, batched, flush_why[0], flush_why[1], flush_why[2], flush_why[3], rec_refused);
    extern int lamp_batch_alt;     // render.m, NFS_LAMP_BATCH=alt: bit 1 = this second replayed immediately; flip
    if (lamp_batch_alt) { diag_event("lampglow: alt second was %s", lamp_batch_alt & 2 ? "split" : "batched"); lamp_batch_alt ^= 2; }
    replayed = refused = 0;
    batches = batched = rec_refused = 0; memset(flush_why, 0, sizeof flush_why);
    for (int i = 0; i < 2; ++i) { submitted[i] = draws[i] = 0; intensity[i] = emission[i] = 0; }
}
