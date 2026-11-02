// Physical trigger diagnostics; driving bindings belong to the Controls screen.
#include "host.h"
#include <math.h>

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
