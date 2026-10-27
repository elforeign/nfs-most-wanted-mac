// offline_stubs.c - host symbols that production code references but the offline test builds don't link (boot.c, app.m).
// Add it to a test's clang line when the link says "Undefined symbols": settings.c declares app_pref_batch_* weak, but Mach-O
// ld still wants a definition; recompiled functions (recomp/out) call guest_divide_error since the audit3 recompiler fix.
// A test that needs to observe these calls defines its own (see settings_look_test.c) and does not link this file.
#include "runtime.h"
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void guest_divide_error(Cpu *c, uint32_t at) { (void)c; (void)at; }
