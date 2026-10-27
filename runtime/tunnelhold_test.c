// Offline test of tunnelhold.h: the retail UpdateIsInTunnel flip logic on a flickering zone sequence (in 6 frames, out 18,
// ten times, then a long stay, then out for good), 60 fps sim clock. Usage: tunnelhold_test <hold seconds>.
// Exit 0 when the reverb target switches at most 4 times over the whole sequence (enter, leave, nothing in between).
// Exit 0 also needs the other-controller scenario (AI car calls and ticks during the player's hold) to leave it intact.
// Measured: hold 0.3 -> 2 applied changes (exit 0); hold 0 (retail) -> 22 (exit 1); see tunnelhold_test.py.
#include <stdio.h>
#include <stdlib.h>
#include "tunnelhold.h"
int main(int argc, char **argv) {
    double hold = argc > 1 ? atof(argv[1]) : 0.3, dt = 1.0 / 60, now = 0;
    enum { P = 1, AI = 2 };   // controllers: the player's and an AI car's
    TunHold h = {0}; int in = 0, applied = 0; uint32_t target = 9;   // city type 9 at the start
    for (int f = 0; f < 6 * 60 * 10 + 600; ++f, now += dt) {
        int zone_in = f < 10 * 24 ? (f % 24) < 6 : f < 10 * 24 + 300;   // flicker, then 5 s inside, then out
        if (zone_in != in) {                                              // UpdateIsInTunnel: flag flip -> SetCurrentReverbType
            uint32_t type = zone_in ? 5 : 9; in = zone_in;
            int apply = zone_in ? th_enter(&h, P, target, type) : th_leave(&h, P, now, hold, type, 0);
            if (apply && type != target) { target = type; ++applied; }
            else if (apply) ++applied;                                     // retail call with the same type still restarts the fade
        }
        if (th_tick(&h, AI, now)) { target = 0xBAD; ++applied; }   // another controller's tick never applies it
        if (th_tick(&h, P, now)) { target = h.type; ++applied; }
    }
    // Another controller's calls during the player's held exit: the AI car changes its own reverb (a retail call) at +3
    // frames and ticks every frame; the player's exit must still be applied by its own tick at the deadline.
    int pend = 0;
    if (hold > 0) {
        TunHold g = {0}; double t0 = 100, applied_at = -1;
        th_leave(&g, P, t0, hold, 9, 0);
        for (int f = 1; f < 120 && applied_at < 0; ++f) {
            double tn = t0 + f * dt;
            if (f == 3 && th_leave(&g, AI, tn, hold, 9, 0) != 1) pend |= 1;   // AI leave passes through, not held
            if (f == 3 && th_enter(&g, AI, 9, 5) != 1) pend |= 2;             // AI enter applies
            if (f == 4 && th_other(&g, AI) != 1) pend |= 4;                   // AI other call applies
            if (f >= 3 && th_tick(&g, AI, tn)) pend |= 8;                     // AI tick must not take it
            if (th_tick(&g, P, tn)) applied_at = tn - t0;
        }
        if (applied_at < 0 || applied_at < hold - 1e-6 || applied_at > hold + 2 * dt) pend |= 16;
        printf("other controller during the hold: %s (flags %d, player's exit applied at +%.3f s)\n", pend ? "BROKEN" : "ok", pend, applied_at);
    }
    printf("hold %.2f s: %d applied reverb type changes, final type %u\n", hold, applied, target);
    return !(applied <= 4 && target == 9 && !pend);
}
