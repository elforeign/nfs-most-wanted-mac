// tunnelhold.h - tunnel reverb hold (port extra), pure state so runtime/tunnelhold_test.c can drive it.
// Retail SFXCTL_Tunnel::UpdateIsInTunnel (004C5C00) calls SetCurrentReverbType on every in/out flip of the track-path
// zone, no hysteresis, so short tunnels and zone-4 stretches flip every few frames and the 120/300 ms reverb fade never
// builds. The hold keeps the tunnel reverb type for hold_s of sim time after the game says we left; entering is immediate
// (a re-entry with the held type changes nothing), any other SetCurrentReverbType call by the same controller applies at once and ends the hold.
#ifndef TUNNELHOLD_H
#define TUNNELHOLD_H
#include <stdint.h>

// The hold belongs to the controller (SFXCTL_Tunnel, one per car with sound) whose exit started it (`owner`): every other
// controller's calls pass through untouched and neither end the hold nor apply it; only the owner's calls and ticks do.
typedef struct { int active; uint32_t owner, type; int32_t fade; double deadline; } TunHold;

// Controller `who` left the tunnel and wants `type`. Returns 1 = apply now, 0 = held (applied later by th_tick).
// While another controller's hold is active this one is not held (retail, applied at once).
static inline int th_leave(TunHold *h, uint32_t who, double now, double hold_s, uint32_t type, int32_t fade) {
    if (hold_s <= 0 || (h->active && h->owner != who)) return 1;
    h->active = 1; h->owner = who; h->type = type; h->fade = fade; h->deadline = now + hold_s;
    return 0;
}
// Controller `who` entered a tunnel and wants `type`; cur is its reverb target now (+A0). Ends its own hold.
// Returns 1 = apply, 0 = nothing to do (the held tunnel type is still the target: the flicker is absorbed).
static inline int th_enter(TunHold *h, uint32_t who, uint32_t cur, uint32_t type) {
    int was = h->active && h->owner == who;
    if (was) h->active = 0;
    return !(was && type == cur);
}
// Any other SetCurrentReverbType call by `who`: applies at once, and ends that controller's own hold.
static inline int th_other(TunHold *h, uint32_t who) { if (h->active && h->owner == who) h->active = 0; return 1; }
// Once per frame per controller: 1 = `who`'s hold ran out, apply h->type / h->fade to it now.
static inline int th_tick(TunHold *h, uint32_t who, double now) {
    if (!h->active || h->owner != who || now < h->deadline) return 0;
    h->active = 0; return 1;
}
#endif
