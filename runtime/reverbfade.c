// Optional half-second environment transitions. The mixer and Reset hold the retail audio mutex.
#include "runtime.h"
#include "settings.h"
#include <math.h>
#include <string.h>

static Setting reverbfade_setting = {.id = "audio.reverb_crossfade", .category = SET_AUDIO,
    .order = 66, .label = "Reverb crossfade", .type = SET_TOGGLE, .def = 1,
    .help = "Blend old and new reverb environments over half a second. Requires a restart.",
    .env = "NFS_REVERB_XFADE", .restart = 1};
__attribute__((constructor)) static void reverbfade_register(void) { settings_add(&reverbfade_setting); }

void f_0081D93A_orig(Cpu *c);
void f_00820DA3_orig(Cpu *c);
void f_0082175F(Cpu *c);
void f_0081BCCB(Cpu *c);
void f_0081BCD9(Cpu *c);

enum { FADE_TAILS = 8 };
static struct {
    uint32_t processor, live, frames, length;
    unsigned count;
    struct { uint32_t node, frames, length; float gain[6]; } tail[FADE_TAILS];
} reverbfade;

static void reverbfade_call(Cpu *c, void (*fn)(Cpu *)) {
    Cpu t = *c; t.esp -= 16; M32(t.esp) = 0; fn(&t);
}
static void reverbfade_free(Cpu *c, uint32_t node) {
    Cpu t = *c; t.esp -= 16; M32(t.esp) = 0;
    M32(t.esp + 4) = 0; M32(t.esp + 8) = node; f_0082175F(&t);
}
static float reverbfade_in(void) {
    if (!reverbfade.length || reverbfade.frames >= reverbfade.length) return 1;
    return sinf(1.5707963267948966f * reverbfade.frames / reverbfade.length);
}
static float reverbfade_gain(uint32_t at, uint32_t length, int retiring) {
    if (at >= length) return retiring ? 0 : 1;
    float angle = 1.5707963267948966f * at / length;
    return retiring ? cosf(angle) : sinf(angle);
}
// Only the ProcessUpdate return address denotes an environment replacement. Releases,
// explicit resets and SetCustom's internal Reset retain their normal cleanup semantics.
void f_0081D93A(Cpu *c) {
    if (!reverbfade_setting.boot_value) { f_0081D93A_orig(c); return; }
    uint32_t p = c->ecx;
    reverbfade_call(c, f_0081BCCB);
    int swap = M32(c->esp) == 0x004DEC0D && p == M32(0x914290) &&
               M32(p) == 0x46584253 && M32(p + 4) == 0 && M32(p + 0x24) == 0;
    if (swap) {
        if (reverbfade.count == FADE_TAILS) {
            reverbfade_free(c, reverbfade.tail[0].node);
            memmove(reverbfade.tail, reverbfade.tail + 1, (--reverbfade.count) * sizeof reverbfade.tail[0]);
        }
        unsigned k = reverbfade.count++;
        uint32_t node = M32(p + 0x28);
        float gain = node == reverbfade.live ? reverbfade_in() : 1;
        reverbfade.tail[k].node = node; reverbfade.tail[k].frames = 0;
        unsigned rate = M16(0x9C2046);
        reverbfade.tail[k].length = rate >= 8000 && rate <= 192000 ? rate / 2 : 24000;
        for (unsigned ch = 0; ch < 6; ++ch) reverbfade.tail[k].gain[ch] = MF32(node + 0x68 + ch * 4) * gain;
        reverbfade.processor = p; reverbfade.live = 0; reverbfade.frames = 0;
        reverbfade.length = reverbfade.tail[k].length;
        M32(p + 0x28) = 0; M32(p + 0x24) = 1;
        reverbfade_call(c, f_0081BCD9);
        c->eax = 0; c->esp += 4; return;
    }
    if (p == reverbfade.processor) {
        for (unsigned i = 0; i < reverbfade.count; ++i) reverbfade_free(c, reverbfade.tail[i].node);
        memset(&reverbfade, 0, sizeof reverbfade);
    }
    reverbfade_call(c, f_0081BCD9);
    f_0081D93A_orig(c);
}

// The existing renderer wrapper is the sole mixer integration. Retiring graphs stay
// on the real bus list and consume the same input as the new graph. Scale their retail
// output gains for this block, then restore them so game level updates remain authoritative.
void reverbfade_mix(Cpu *c) {
    if (!reverbfade.count) { f_00820DA3_orig(c); return; }
    unsigned frames = M32(c->esp + 4);
    uint32_t p = reverbfade.processor;
    if (!reverbfade.live && M32(p + 0x24) == 0) reverbfade.live = M32(p + 0x28);
    uint32_t nodes[FADE_TAILS + 1]; float saved[FADE_TAILS + 1][6];
    unsigned n = reverbfade.count;
    for (unsigned i = 0; i < n; ++i) nodes[i] = reverbfade.tail[i].node;
    if (reverbfade.live) nodes[n++] = reverbfade.live;
    for (unsigned i = 0; i < n; ++i) {
        uint32_t node = nodes[i];
        int retiring = i < reverbfade.count;
        uint32_t at = retiring ? reverbfade.tail[i].frames : reverbfade.frames;
        uint32_t length = retiring ? reverbfade.tail[i].length : reverbfade.length;
        float previous = reverbfade_gain(at, length, retiring);
        float target = reverbfade_gain(at + frames, length, retiring);
        for (unsigned ch = 0; ch < 6; ++ch) {
            saved[i][ch] = MF32(node + 0x68 + ch * 4);
            float base = i < reverbfade.count ? reverbfade.tail[i].gain[ch] : saved[i][ch];
            MF32(node + 0x50 + ch * 4) = base * previous;
            MF32(node + 0x68 + ch * 4) = base * target;
        }
        M32(node + 0x80) = 1; // retail 0082279F ramps previous -> target over 16 samples
    }
    // SetCustom idles the bus even when a retained graph still has a tail. Keep
    // that graph running until retirement without extending the retail idle timer.
    uint32_t idle = M32(0x9C22F8);
    int force_tail = idle >= 2999;
    if (force_tail) M32(0x9C22F8) = 0;
    f_00820DA3_orig(c);
    if (force_tail) M32(0x9C22F8) = idle + 1;
    for (unsigned i = 0; i < n; ++i) for (unsigned ch = 0; ch < 6; ++ch) {
        MF32(nodes[i] + 0x50 + ch * 4) = saved[i][ch];
        MF32(nodes[i] + 0x68 + ch * 4) = saved[i][ch];
    }
    reverbfade.frames += frames;
    for (unsigned i = 0; i < reverbfade.count;) {
        reverbfade.tail[i].frames += frames;
        if (reverbfade.tail[i].frames < reverbfade.tail[i].length) { ++i; continue; }
        reverbfade_free(c, reverbfade.tail[i].node);
        memmove(reverbfade.tail + i, reverbfade.tail + i + 1, (--reverbfade.count - i) * sizeof reverbfade.tail[0]);
    }
}
