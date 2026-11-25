// Two-pad world collision: fixed P1 0.000, P2 0.984; singleton control P1 0.984, P2 0.000.
// Offline regression: actual feedback.c, fake motor transport; no Cocoa, game, controller or Unicorn.
// From repo root:
//   cc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I recomp runtime/feedback_test.c -o /tmp/feedback_test && /tmp/feedback_test
// Lifecycle proof: PASS 38/38; original source and omitted model reset each FAIL 3/38.
// Invalidation clears sustained oscillators and timed envelopes; a fresh BeginFrame can resume immediately.
// Native Rumble Off over 8 ticks: original 8 pad effect clears, fixed 0; 8 motor stops in both.
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
static int slot_stops[2];
uint64_t host_ticks(void) { return clock_ns; }
void rumble_stop_slot(unsigned slot) { if (slot >= 2) abort(); ++slot_stops[slot]; rumble_stop(); }
void rumble_stop(void) { if (!output_off) { motor_l = motor_r = 0; ++stop_calls; } }
void rumble_suppress(int on) { output_hold = on; rumble_stop(); }
void diag_event(const char *fmt, ...) {
    if (!strncmp(fmt, "feedback: %s %s", 15)) ++transition_logs;
}
void diag_event_ring(const char *fmt, ...) { (void)fmt; }
uint32_t galloc(uint32_t n) { if (n != 16) abort(); return 0x3000; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    (void)c;
    if (fn == FN_KEYED_SEARCH && n == 3) return a[2] == 1 ? 0x3100 : 0;
    if (fn == 0x3200 && n == 1) { memset(G + a[0], 0, 12); return a[0]; }
    abort();
}
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
    feedback_states[0].model.osc[0] = (Osc){.offset = 0.6f};    // sustained road texture, not an envelope that eventually decays
    feedback_states[0].model.osc[2] = (Osc){.offset = 0.3f};
    feedback_states[0].model.env[0] = (Env){10, 10, 1, 0};      // a pending collision must also be discarded
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
static Cpu owned_cpu(unsigned slot) {
    Cpu c = {0}; c.ecx = 0x8000 + 0x100 * slot; c.esp = 0x9000;
    return c;
}
static void collision(unsigned slot) {
    Cpu c = owned_cpu(slot); fb_resume(&c);
    c = owned_cpu(slot); M32(c.esp + 4) = 0x4000; M32(c.esp + 8) = 1;
    fb_collision(&c);
}
static void two_pad_isolation(void) {
    diag_on = 0;
    M32(0x0092D87Cu) = 0x6000; M32(0x0092D884u) = 2;
    for (unsigned slot = 0; slot < 2; ++slot) {
        uint32_t player = 0x7000 + 0x100 * slot;
        M32(0x6000 + 4 * slot) = player;
        M32(player + 0x0C) = 0x8000 + 0x100 * slot;
        M32(player + 0x18) = slot;
    }
    memset(feedback_states, 0, sizeof feedback_states);
    M32(0x3104) = 0x3300; M32(0x3300) = 0x3400; M32(0x3468) = 0x3200;
    M32(0x401C) = 2; M32(0x403C) = 1; MF32(0x4050) = 1.0f / X360E_FB_WORLD_SLOPE; // full-strength world contact
    collision(1);
    float h[2], l[2];
    for (unsigned slot = 0; slot < 2; ++slot) feedback_levels_slot(slot, &h[slot], &l[slot]);
    clock_ns += 16000000;
    for (unsigned slot = 0; slot < 2; ++slot) feedback_levels_slot(slot, &h[slot], &l[slot]);
    printf("P2-only collision: P1 %.3f, P2 %.3f\n", h[0], h[1]);
    CHECK(h[0] == 0 && l[0] == 0 && h[1] > 0 && l[1] == 0);
    collision(0); // simultaneous envelopes survive an independent P2 invalidation
    feedback_levels_slot(0, &h[0], &l[0]); clock_ns += 16000000;
    feedback_levels_slot(0, &h[0], &l[0]);
    feedback_invalidate_slot(1);
    feedback_levels_slot(1, &h[1], &l[1]);
    CHECK(h[1] == 0 && feedback_states[1].model.env[0].time == 0);
    CHECK(h[0] > 0 && feedback_states[0].model.env[0].time > 0);
    collision(1);
    Cpu c = owned_cpu(0); fb_stop(&c); // P1 native Rumble Off leaves P2 live
    feedback_levels_slot(1, &h[1], &l[1]); clock_ns += 16000000;
    feedback_levels_slot(1, &h[1], &l[1]);
    CHECK(!feedback_states[0].model.active && h[1] > 0);
    int stops0 = slot_stops[0], stops1 = slot_stops[1];
    M32(0x0092D884u) = 0; // leaving the race: no LocalPlayers, no pending feedback
    feedback_poll_pause(); // no pad read is needed for neutral output
    CHECK(slot_stops[0] == stops0 + 1 && slot_stops[1] == stops1 + 1);
    for (unsigned slot = 0; slot < 2; ++slot) {
        feedback_levels_slot(slot, &h[slot], &l[slot]);
        CHECK(h[slot] == 0 && l[slot] == 0 && !feedback_states[slot].effects_valid);
    }
    M32(0x0092D884u) = 2;
    // Reorder the vector and swap owned ports: feedback follows the port, not list order or settings index.
    M32(0x6000) = 0x7100; M32(0x6004) = 0x7000;
    M32(0x7118) = 0; M32(0x7018) = 1;
    collision(1);
    feedback_levels_slot(0, &h[0], &l[0]); clock_ns += 16000000;
    feedback_levels_slot(0, &h[0], &l[0]); feedback_levels_slot(1, &h[1], &l[1]);
    CHECK(h[0] > 0 && h[1] == 0);
    feedback_suppress(FEEDBACK_MENU, 1);
    for (unsigned slot = 0; slot < 2; ++slot) {
        feedback_levels_slot(slot, &h[slot], &l[slot]);
        CHECK(h[slot] == 0 && l[slot] == 0);
    }
    feedback_suppress(FEEDBACK_MENU, 0);
    M32(0x0092D884u) = 0;
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
    CHECK(feedback_states[0].model.osc[0].offset == 0 && feedback_states[0].model.env[0].time == 0);
    moving();
    feedback_invalidate_effects();
    diag_on = 0; // a new BeginFrame must reset the model before building fresh output
    Cpu next = {0}; fb_begin_frame(&next);
    moving();
    diag_on = 1;

    int fx_before = fx_stops;
    int stops_before = stop_calls;
    M32(0x008F86E0u) = 0;                 // native Rumble Off calls Stop every feedback tick
    Cpu c = {0};
    for (int tick = 0; tick < 8; ++tick) fb_stop(&c);
    printf("native Rumble Off: %d pad effect clears, %d motor stops over 8 ticks\n",
           fx_stops - fx_before, stop_calls - stops_before);
    CHECK(fx_stops == fx_before && stop_calls == stops_before + 8);
    CHECK(motor_l == 0 && motor_r == 0);  // native IFeedback::Stop, before next pad read
    quiet();
    output_off = 1; motor_l = 0.17f; motor_r = 0.23f; // NFS_RUMBLE=off transport leaves the pad alone
    feedback_suppress(FEEDBACK_MENU, 1); pad_read_motors();
    feedback_suppress(FEEDBACK_MENU, 0); pad_read_motors();
    CHECK(motor_l == 0.17f && motor_r == 0.23f);
    CHECK(transition_logs == 20);
    two_pad_isolation();
    free(G);
    printf("feedback_test: %s, %d checks, %d failures, %d transition logs\n", failures ? "FAIL" : "PASS", checks, failures, transition_logs);
    return failures != 0;
}
