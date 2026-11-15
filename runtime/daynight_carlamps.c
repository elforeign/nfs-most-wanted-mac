// Car lamps follow the time-of-day clock. Retail lights every car's headlights in all play; with the clock on,
// lamp level L (daynight.c) scales the car flares. L is 1 while the clock is off, so retail is untouched.
// The headlight lamp textures stay _ON: no retail car pack carries the _OFF/_GLASS_OFF textures the game
// would swap in (UpdateLightStateTextures 00737E30 never does).
#include "host.h"
#include "daynight.h"

enum {
    FLARE_RET = 0x0074323Au,            // RenderFlaresOnCar 00742950's eRenderLightFlare call
    LEFT_HEAD = 0x9DB90133u, RIGHT_HEAD = 0xD09091C6u,
    LEFT_BRAKE = 0x31A66786u, RIGHT_BRAKE = 0xBF700A79u,
};

// BOOT_DIAG: headlight flare strengths on each captured frame, before (in) and after (out) the scale.
uint64_t diag_current_frame(void);
static uint64_t counted_frame = UINT64_MAX;
static int counted_shot;
static double head_in, head_out;
static unsigned head_skipped;
static void count_frame(void) {
    uint64_t frame = diag_current_frame() + 1;   // present counts the frame before capturing it
    if (frame == counted_frame) return;
    if (counted_shot)
        diag_event("daynight_carlamps: frame=%llu L=%.4f head_in=%.4f head_out=%.4f head_skipped=%u",
                   (unsigned long long)counted_frame, (double)daynight_lamp_level(), head_in, head_out,
                   head_skipped);
    counted_frame = frame;
    counted_shot = diag_shot_due(frame) != 0;
    head_in = head_out = 0;
    head_skipped = 0;
}

// eRenderLightFlare 00505380 entry (cdecl; strength is argument 4). Returns 1 when the flare is not drawn:
// the caller then returns eax 0, as retail does for a strength <= 0.
int daynight_carlamps_flare(Cpu *c) {
    if (M32(c->esp) != FLARE_RET || !arg(c, 1)) return 0;
    uint32_t hash = M32(arg(c, 1) + 8);
    float level = daynight_lamp_level(), in = MF32(c->esp + 16);
    int head = hash == LEFT_HEAD || hash == RIGHT_HEAD;
    // Brake markers only while unbraked (exactly 0.5); a braked or broken lamp stays retail.
    if (!head && !((hash == LEFT_BRAKE || hash == RIGHT_BRAKE) && in == 0.5f)) return 0;
    if (diag_on && head) { count_frame(); head_in += in; head_out += in * level; head_skipped += level <= 0; }
    if (level >= 1) return 0;
    if (level <= 0) return 1;
    MF32(c->esp + 16) = in * level;
    return 0;
}
