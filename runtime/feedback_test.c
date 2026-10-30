// Offline regression: actual feedback.c, fake motor transport; no Cocoa, game, controller or Unicorn.
// From repo root:
//   cc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I recomp runtime/feedback_test.c -o /tmp/feedback_test && /tmp/feedback_test
// Lifecycle proof: PASS 38/38; original source and omitted model reset each FAIL 3/38.
// Invalidation clears sustained oscillators and timed envelopes; a fresh BeginFrame can resume immediately.
// Round 2: PASS 33/33, 0 failures; --broken-padfx FAIL 4/33 (suppression/stop forwarding removed).
// Original rumble regression: PASS 29/29, 20 transition logs (ASan/UBSan).
// Original pre-fix baseline: FAIL, 19/29 checks, including immediate menu stop / rumble while open / stale resume.
// Save the pre-fix source with: git show HEAD:runtime/feedback.c > /tmp/feedback-before.c
// Then add -I runtime -DFEEDBACK_LEGACY
//   -DFEEDBACK_SOURCE='"/tmp/feedback-before.c"' to the same compile command.
// The legacy adapter below represents the missing menu/pause/focus/app hooks (no-op), not a different model.
#define _POSIX_C_SOURCE 200809L
#include <stdarg.h>
#ifndef FEEDBACK_SOURCE
#define FEEDBACK_SOURCE "feedback.c"
#endif
#include FEEDBACK_SOURCE

uint8_t *G;
int diag_on;
const char *diag_dir = "/tmp";
const char *game_dir = "/nonexistent";
static uint64_t clock_ns = 1000000000;
static float motor_l, motor_r;
static int output_off, output_hold, stop_calls, transition_logs, failures, checks;
uint64_t host_ticks(void) { return clock_ns; }
void rumble_stop(void) { if (!output_off) { motor_l = motor_r = 0; ++stop_calls; } }
void rumble_suppress(int on) { output_hold = on; rumble_stop(); }
void diag_event(const char *fmt, ...) {
    if (!strncmp(fmt, "feedback: %s %s", 15)) ++transition_logs;
}
void diag_event_ring(const char *fmt, ...) { (void)fmt; }
uint32_t galloc(uint32_t n) { (void)n; abort(); }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) { (void)c; (void)fn; (void)n; (void)a; abort(); }
static unsigned fx_holds;
static int broken_padfx;
static int fx_stops;
void padfx_suppress(unsigned reason, int on) { if (broken_padfx) return; fx_holds = on ? fx_holds | reason : fx_holds & ~reason; ++fx_stops; }
void padfx_stop(void) { if (!broken_padfx) ++fx_stops; }
void padfx_poll(void) {}
int padfx_road_wanted(void) { return 0; }
int padfx_wanted(void) { return 0; }
void padfx_game_tick(Cpu *c, uint32_t player) { (void)c; (void)player; abort(); }
void f_006EE490_orig(Cpu *c) { (void)c; abort(); }
void f_006EE400_orig(Cpu *c) { (void)c; abort(); }
#ifdef FEEDBACK_LEGACY
void feedback_suppress(unsigned reason, int on) { (void)reason; (void)on; }
void feedback_poll_pause(void) {}
#endif
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static void pad_read_motors(void) {
    float l, r;
    clock_ns += 16000000;
    feedback_levels(&l, &r);
    if (!output_off) { motor_l = output_hold ? 0 : l; motor_r = output_hold ? 0 : r; }
}
static void game_frame(void) {
    Cpu c = {0};
    fb_resume(&c);                        // DoFFB resumes the model each tick
    S.osc[0] = (Osc){.offset = 0.6f};    // sustained road texture, not an envelope that eventually decays
    S.osc[2] = (Osc){.offset = 0.3f};
    S.env[0] = (Env){10, 10, 1, 0};      // a pending collision must also be discarded
}
static void moving(void) {
    game_frame(); pad_read_motors(); pad_read_motors();
    CHECK(motor_l > 0.5f && motor_r > 0.2f);
}
static void quiet(void) {
    pad_read_motors();
    float l, r; feedback_last_levels(&l, &r);
    CHECK(motor_l == 0 && motor_r == 0 && l == 0 && r == 0);
}
int main(int argc, char **argv) {
    setenv("NFS_RUMBLE", "off", 1);
    broken_padfx = argc > 1 && !strcmp(argv[1], "--broken-padfx");
    G = calloc(1, 0x00A00000); if (!G) return 2;
    M32(0x009885E0u) = 0x1000;
    M32(0x102Cu) = 3;
    moving();
    diag_on = 1;
    feedback_suppress(FEEDBACK_MENU, 1);   // portmenu.m set_open, before sim time is held
    CHECK(fx_holds == FEEDBACK_MENU && fx_stops == 1);
    CHECK(motor_l == 0 && motor_r == 0);  // immediate, no pad read required
    int stops = stop_calls;
    feedback_suppress(FEEDBACK_MENU, 1);
    CHECK(stop_calls == stops);           // no per-frame transition spam
    for (int i = 0; i < 3; ++i) { game_frame(); quiet(); }
    feedback_suppress(FEEDBACK_MENU, 0);
    CHECK(fx_holds == 0 && fx_stops == 2);
    quiet(); quiet();                     // no replay even before the first resumed game tick
    moving();                            // next game update takes over

    M32(0x102Cu) = 4;
    feedback_poll_pause();                // portmenu_pass, even when no pad is read
    CHECK(motor_l == 0 && motor_r == 0);
    game_frame(); quiet();
    feedback_suppress(FEEDBACK_MENU, 1);
    feedback_suppress(FEEDBACK_MENU, 0);   // closing F10 must leave the game's pause in force
    game_frame(); quiet();
    M32(0x102Cu) = 3; feedback_poll_pause();
    quiet(); moving();
    M32(0x102Cu) = 4; quiet();             // pause discovered on a pad read also stops immediately
    M32(0x102Cu) = 3; quiet(); moving();
    M32(0x102Cu) = 4; feedback_poll_pause();
    M32(0x102Cu) = 3; moving();            // the very next DoFFB can resume before the next loop/pad poll

    feedback_suppress(FEEDBACK_FOCUS, 1);
    CHECK(motor_l == 0 && motor_r == 0);
    feedback_suppress(FEEDBACK_APP, 1);
    feedback_suppress(FEEDBACK_FOCUS, 0);
    CHECK(fx_holds == FEEDBACK_APP);
    game_frame(); quiet();                // app still inactive
    feedback_suppress(FEEDBACK_APP, 0);
    quiet(); moving();
    feedback_suppress(FEEDBACK_MENU, 1);
    feedback_suppress(FEEDBACK_FOCUS, 1);
    feedback_suppress(FEEDBACK_FOCUS, 0);
    game_frame(); quiet();                // focus return cannot override F10
    feedback_suppress(FEEDBACK_MENU, 0);
    quiet(); moving();

    feedback_invalidate_effects(); // unplug invalidates motors as well as trigger publications
    quiet(); quiet();
    CHECK(S.osc[0].offset == 0 && S.env[0].time == 0);
    moving();
    feedback_invalidate_effects();
    diag_on = 0; // a new BeginFrame must reset the model before building fresh output
    Cpu next = {0}; fb_begin_frame(&next);
    moving();
    diag_on = 1;

    int fx_before = fx_stops;
    Cpu c = {0}; fb_stop(&c);
    CHECK(fx_stops == fx_before + 1);
    CHECK(motor_l == 0 && motor_r == 0);  // native IFeedback::Stop, before next pad read
    quiet();
    output_off = 1; motor_l = 0.17f; motor_r = 0.23f; // NFS_RUMBLE=off transport leaves the pad alone
    feedback_suppress(FEEDBACK_MENU, 1); pad_read_motors();
    feedback_suppress(FEEDBACK_MENU, 0); pad_read_motors();
    CHECK(motor_l == 0.17f && motor_r == 0.23f);
    CHECK(transition_logs == 20);
    free(G);
    printf("feedback_test: %s, %d checks, %d failures, %d transition logs\n", failures ? "FAIL" : "PASS", checks, failures, transition_logs);
    return failures != 0;
}
