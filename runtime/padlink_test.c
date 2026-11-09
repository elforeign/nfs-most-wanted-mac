// Link-trail regression and shared capture for production-hook tests (no hardware).
// Old-behavior controls are embedded mutations, never a checkout from git history.
// Production-hook proofs: padtwo (promotion/unfocused, slot battery, unknown battery),
// padfx (second-pad LED), rumble (transport and stale HID completion). Results below
// are checked by the padlink suite row as well as each normal hook test.
// Fixed: module 17/17; production padtwo 45/45 (one pad), 92/92 (two pads),
// padfx 293/293 and rumble 47/47. Restoring 534f48c8 behavior with mutations:
// presence-only hooks FAIL 6/45 and 6/92; enumeration battery FAIL 1/92;
// unknown-as-zero FAIL 1/45 and 1/92; unkeyed outputs FAIL 1/293 in padfx, 1/47 in rumble;
// transport retention FAIL 1/47 in rumble. Idle-flush removal FAIL 2/17;
// diagnostics-off work restored FAIL 3/17 (fixed module PASS 17/17).
// All controls drive actual production hooks; none reads repository history.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "host.h"
static char link_notes[128][512];
static int link_nnotes;
void report_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

void report_note(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    if (link_nnotes < 128) vsnprintf(link_notes[link_nnotes++], sizeof link_notes[0], fmt, ap);
    va_end(ap);
}
#ifndef PADLINK_SOURCE
#define PADLINK_SOURCE "padlink.c"
#endif
#define mu link_mu
#include PADLINK_SOURCE
#undef mu
#ifndef PADLINK_HOOK_TEST
int diag_on;
static uint64_t clock_ns = 1000000000ull;
static int link_diags, clock_calls;
uint64_t host_ticks(void) { ++clock_calls; return clock_ns; }
void diag_event(const char *fmt, ...) { (void)fmt; ++link_diags; }
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
int main(void) {
    void *a = (void *)1, *b = (void *)2;
    padlink_select(a, "A");
    padlink_battery(a, 0, -1);
    CHECK(link_nnotes == 1 && !links[0].low_noted && !links[0].battery_noted);
    padlink_battery(a, .08f, 0);
    CHECK(strstr(link_notes[1], "battery low: 8 %"));
    padlink_battery(a, .04f, 0); CHECK(link_nnotes == 2);
    int calls = clock_calls;
    CHECK(padlink_token(a) == 0);
    padlink_output(1, PADLINK_FX, 1); padlink_idle(0);
    CHECK(clock_calls == calls && links[0].total[1] == 0);
    diag_on = 1;
    uint64_t old = padlink_token(a);
    padlink_output(old, PADLINK_FX, 1);
    CHECK(links[0].total[1] == 1 && link_diags == 0);
    clock_ns += 1100000000; padlink_idle(0);
    CHECK(link_diags == 1 && links[0].peak[1] == 1);
    padlink_output(old, PADLINK_FX, 1); padlink_idle(1);
    CHECK(link_diags == 2);
    padlink_select(b, "B"); CHECK(links[0].total[1] == 0);
    padlink_output(old, PADLINK_FX, 1); CHECK(links[0].total[1] == 0);
    CHECK(links[0].transport == -1 && !links[0].battery_at);
    padlink_hid(a, 1); CHECK(links[1].transport == 1 && links[0].transport == -1);
    padlink_detach(a); CHECK(!links[1].id && links[1].transport == -1);
    padlink_hid(a, 0); CHECK(links[1].transport == 0);
    padlink_output(padlink_token(a), PADLINK_RUMBLE, 0);
    CHECK(links[1].total[0] == 1 && links[1].failed == 1);
    padlink_detach(b); CHECK(!links[0].id);
    int longest = 0;
    for (int i = 0; i < link_nnotes; ++i) if ((int)strlen(link_notes[i]) > longest) longest = (int)strlen(link_notes[i]);
    CHECK(longest <= 153);
    printf("padlink: %s %d/%d\n", failures ? "FAIL" : "PASS", checks-failures, checks);
    return failures != 0;
}
#endif
