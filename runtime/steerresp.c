// steerresp.c - F10 Controls > Steering response (Retail / Medium / Linear).
// SuspensionRacer::DoHumanSteering (0069E820) remaps the human's steering input (-1..1, GameAction left minus right) through
// Table::GetValue of SteerInputRemapTables table 0 (008F8EE0, called at 0069E8F2): {count 21, min -1, max 1, scale 10, float
// data* 008F8B80}, interpolated linearly. Retail is 0, .034, .061, .080, .116, .148, .216, .303, .453, .712, 1 over 0..1 (odd
// around 0), so half stick turns 15 % of the speed's max lock; the X360's table is byte-identical. Medium moves every point
// halfway to the input (x), Linear makes output = input. Only the 21 data floats change: max lock by speed (008F8F30) and the rate limit
// (CalculateSteeringSpeed 0068EC70) stay the game's. DoHumanSteering is the human path only (AI cars do not call it), and
// keyboard steering is 0/1, which every curve maps to itself, so only analog pad steering feels different.
// Live: the table is rewritten from the hook of DoHumanSteering whenever the setting differs from what was last written.
// Switch: controls.steering_response (NFS_STEER_RESPONSE=retail|medium|linear). BOOT_DIAG: `steerresp:` lines (mode when
// written; input -> output of the game's own table lookup when the stick moves, at most 40).
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "host.h"
#include "settings.h"

#define STEER_TABLE 0x008F8EE0u
#define STEER_DATA 0x008F8B80u
#define STEER_N 21

static float kRetail[STEER_N];          // the exe's own 21 points, copied from guest memory before the first rewrite
static int have_retail;
static void steerresp_capture(void) {
    if (!have_retail) memcpy(kRetail, G + STEER_DATA, sizeof kRetail);
    have_retail = 1;
}

// Point i of mode 0 Retail, 1 Medium, 2 Linear; the input at point i is (i - 10) / 10.
float steerresp_point(int mode, int i) {
    steerresp_capture();
    float x = (float)(i - 10) / 10.0f;
    return mode == 2 ? x : mode == 1 ? .5f * (kRetail[i] + x) : kRetail[i];
}

// Table::GetValue (006533B0): the game's lookup, for the diag and the offline test.
float steerresp_lookup(const float *data, float v) {
    float t = (v - -1.0f) * 10.0f;
    int k = (int)t;
    if (k < 0 || t < 0) return data[0];
    if (k >= STEER_N - 1) return data[STEER_N - 1];
    float f = t - (float)k;
    return f * data[k + 1] + (1.0f - f) * data[k];
}

static const char *const kResp[] = {"Retail", "Medium", "Linear"}, *const kRespV[] = {"retail", "medium", "linear"};
static Setting s_resp = {.id = "controls.steering_response", .category = SET_CONTROLS, .order = 120, .label = "Steering response",
    .help = "How far the stick turns the wheels. Retail is the game's curve (half stick = 15% of the lock); Linear turns in direct proportion; Medium is halfway.",
    .type = SET_CHOICE, .choices = kResp, .values = kRespV, .nchoices = 3, .def = 0, .env = "NFS_STEER_RESPONSE"};
__attribute__((constructor)) static void steerresp_register(void) { settings_add(&s_resp); }

static int written = 0;                 // the mode whose points are in guest memory (the exe's own data is Retail)
void steerresp_sync(int mode) {
    if (mode == written) return;
    steerresp_capture();
    float *d = (float *)(G + STEER_DATA);
    for (int i = 0; i < STEER_N; ++i) d[i] = steerresp_point(mode, i);
    written = mode;
    if (diag_on) diag_event("steerresp: %s written to table %08X (point .5 = %.3f, point 1 = %.3f)", kResp[mode], STEER_TABLE,
                            (double)d[15], (double)d[20]);
}

void f_0069E820_orig(Cpu *c);
void f_0069E820(Cpu *c) {               // __thiscall DoHumanSteering(State &): ecx this, [esp+4] the chassis state
    steerresp_sync(s_resp.value);
    if (diag_on) {
        static int lines;
        static float last = 9;
        uint32_t st = M32(c->esp + 4);
        float in;
        memcpy(&in, G + st + 0x8c, 4);
        if (lines < 40 && (in > last + .01f || in < last - .01f)) {
            diag_event("steerresp: mode=%s input %.3f -> table %.3f", kResp[written], (double)in,
                       (double)steerresp_lookup((const float *)(G + STEER_DATA), in));
            last = in; ++lines;
        }
    }
    f_0069E820_orig(c);
}
