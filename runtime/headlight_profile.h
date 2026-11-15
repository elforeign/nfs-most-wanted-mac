#pragma once
#include <stddef.h>

/* SI metres, candela and lux. Angles are degrees: +h right, +v up,
 * relative to the lamp's forward optical axis (not a pre-aimed axis).
 * The profile includes the nominal 0.57-degree downward aim. */
typedef struct { double x, y, z; } HLVec3;
typedef struct {
    size_t horizontal_count, vertical_count;
    const double *horizontal_deg, *vertical_deg;
    const double *candela;                 /* row-major [vertical][horizontal] */
} HLProfile;

extern const HLProfile hl_low_beam;
double hl_cutoff_deg(double horizontal_deg);
/* Bilinear lookup, gated by the cut-off; zero outside the angular domain or
 * for nonfinite inputs. Grid rows above the horizon are also zero. */
double hl_candela(double horizontal_deg, double vertical_deg);
/* Provisional artistic range limit: 1 through 100 m, linear to 0 at 120 m.
 * This fade is separate from the physical inverse-square law. */
double hl_distance_fade(double distance_m);
double hl_illuminance(double candela, double distance_m, double receiver_cosine);

/* Caller copies the current marker's world position and orthonormal basis;
 * right/up/forward are unit axes. Disabled lamps need no valid marker.
 * State is explicit and stateless: no stale marker retention, allocation,
 * game-memory access, renderer hook, visibility test or colour conversion. */
typedef struct {
    HLVec3 position, right, up, forward;
    int enabled;
} HLLamp;
/* Surface normal must be unit length. Invalid active geometry contributes 0;
 * the other lamp remains usable. Visibility/occlusion belongs to the caller. */
double hl_lamp_lux(const HLLamp *lamp, HLVec3 receiver, HLVec3 normal);
double hl_pair_lux(const HLLamp lamps[2], HLVec3 receiver, HLVec3 normal);
