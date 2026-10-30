// Selectable PlayStation trigger pedals. Only the four driving joystick actions
// are overlaid; the profile's bindings and keyboard slots remain the player's.
#include "host.h"
#include "settings.h"
#include <math.h>

static const char *const pedal_choices[] = {"Shift", "Pedals"};
static const char *const pedal_values[] = {"shift", "pedals"};
static Setting s_pedals = {.id = "controls.triggers", .category = SET_CONTROLS, .order = 25,
    .label = "Triggers", .help = "PlayStation: Shift keeps your bindings. Pedals uses R2 gas, L2 brake/reverse, Cross shift up, Square shift down.",
    .type = SET_CHOICE, .choices = pedal_choices, .values = pedal_values, .nchoices = 2,
    .def = 0, .env = "NFS_TRIGGER_LAYOUT"};
__attribute__((constructor)) static void pedals_register(void) { settings_add(&s_pedals); }

int pedals_selected(void) { return s_pedals.value == 1; }
int pedals_binding(int action, uint32_t out[6]) {
    if (!pedals_selected()) return 0;
    uint32_t type, object;
    switch (action) {
    case 0: type = 7; object = 2; break; // gas: Rz+ (R2), no additional deadzone
    case 1: type = 5; object = 2; break; // brake: Z+ (L2)
    case 7: type = 2; object = 2; break; // shift down: Square
    case 8: type = 2; object = 0; break; // shift up: Cross
    default: return 0;
    }
    out[0] = type; out[1] = 0; out[2] = object;
    out[3] = out[4] = out[5] = 0;
    return 1;
}

#ifndef PEDALS_NO_TRACE
// BOOT_DIAG + scripted physical trigger pairs only. FetchInput identifies the
// human IInput; ComputeState then records the exact controls consumed by physics,
// after FetchInput's 5%/95% snap, DoAutoReverse and DoAutoBraking. No injected input.
static uint32_t trace_input;
static unsigned trace_step, trace_rows;
static float trace_lt, trace_rt;
void pedals_trace_sample(unsigned step, float lt, float rt) {
    trace_step = step; trace_lt = lt; trace_rt = rt; trace_rows = 0;
}
void f_006A8870_orig(Cpu *c);
void f_006A8870(Cpu *c) {
    if (!diag_on) { f_006A8870_orig(c); return; }
    uint32_t self = c->ecx;
    f_006A8870_orig(c);
    trace_input = self - 0x2Cu; // inline IInput subobject; its first word is the vtable
}
void f_0069D4D0_orig(Cpu *c);
// Logging lives out of line so the diag-off path stays a flag check and a tail call.
__attribute__((noinline)) static void pedals_trace(Cpu *c) {
    uint32_t self = c->ecx, state = M32(c->esp + 8), input = M32(self + 0x58);
    f_0069D4D0_orig(c);
    if (!trace_input || input != trace_input) return;
    static FILE *log;
    if (!log) {
        char path[1024]; snprintf(path, sizeof path, "%s/pedals.tsv", diag_dir);
        log = fopen(path, "w");
        if (!log) return;
        fprintf(log, "step\tlt\trt\tgas\tbrake\tspeed\tinput_gas\tinput_brake\n");
    }
    float x = MF32(state + 0x50), y = MF32(state + 0x54), z = MF32(state + 0x58);
    fprintf(log, "%u\t%.3f\t%.3f\t%.6f\t%.6f\t%.3f\t%.6f\t%.6f\n", trace_step,
            trace_lt, trace_rt, MF32(state + 0x4C), MF32(state + 0x5C), sqrtf(x*x + y*y + z*z),
            MF32(input + 0x1C), MF32(input + 0x20));
    fflush(log); ++trace_rows;
}
void f_0069D4D0(Cpu *c) {
    if (!diag_on || !trace_step || trace_rows >= 120) { f_0069D4D0_orig(c); return; }
    pedals_trace(c);
}
#endif
