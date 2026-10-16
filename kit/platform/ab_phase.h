// Test71 (TEST ONLY): an A/B of CPU-side changes inside one run, so both sides see the same
// traffic and scene mix (separate runs of the busy city vary by more than the changes measured).
// RECOMP_AB_PERIOD=<seconds>: during odd periods of the uptime clock (the clock the frame timings'
// sealed_s column uses) the switches that ask recomp_ab::old(which) take their earlier behaviour; during
// even periods, the current one. The phase is set once per presented frame on the main thread.
// Unset (every normal run): always the current behaviour; a switch costs one relaxed load.
#pragma once

#include "os.h"

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace recomp_ab {
inline std::atomic<int> old_phase{0};
inline uint64_t period_ns() {
    static const uint64_t p = [] {
        const char *e = recomp_env("AB_PERIOD");
        long s = e ? strtol(e, nullptr, 10) : 0;
        return s > 0 ? (uint64_t)s * 1000000000ull : 0ull;
    }();
    return p;
}
inline void tick() {
    // Test75: RECOMP_AB_FORCE_OLD=1 keeps the earlier behaviour for the whole run (image comparisons).
    static const bool force_old = [] {
        const char *e = recomp_env("AB_FORCE_OLD");
        return e && !strcmp(e, "1");
    }();
    if (force_old)
        old_phase.store(1, std::memory_order_relaxed);
    else if (const uint64_t p = period_ns())
        old_phase.store((int)((os_monotonic_ns() / p) & 1), std::memory_order_relaxed);
}
// Which switches take part: RECOMP_AB_SWITCHES=<names, comma separated> (default: all but the null controls).
// Run 29 (D-167, review/run29/opt-design.md): 64 bits (bit 29 was the last free one); bits 32+ are the optimisation
// pass's switches. Unset env = every switch takes part, as before.
enum : uint64_t {
    // Bits 3-8 (sampler, imports, view, preshader, audio, consts, mirrors, aabb: the Test71-75 CPU A/Bs; bit 0 was stride, now QPCFINE) FREE
    // since run 29 (D-167): their earlier code paths were removed; the measured side is the only one.
    // 512u (bit 9, was COMMIT), 8192u (bit 13, SDELTA) and 16384u (bit 14, BATCH) FREE since run 31 (D-176): the A4-lite
    // experiments (commit delta, state-table deltas, hand-over size) were deleted after R31U measured no clear saving.
    UG1FOG = 2u,    // UG1 run 6: Xbox-style fog (OLD: none; needs RECOMP_UG1_FOG=1, d3d9_metal.mm); name ug1fog
    HLFADE = 4u,    // UG1 run 6: headlight distance fade (OLD: the PC ramp; needs RECOMP_UG1_HEADLIGHT_FADE); name hlfade
    QPCFINE = 1u,   // UG1 run 5: QueryPerformanceCounter in microseconds (OLD: 1000-tick millisecond steps, kernel32.cpp); name qpcfine
    A2C = 1024u,    // Remaster A3 step A: alpha-to-coverage variant (d3d9_metal.mm; needs RECOMP_REMASTER_A2C=1)
    NULLDRAW = 2048u, // Remaster A4 gate: draws skipped in OLD periods (d3d9.cpp null_draw); opt-in only
    NULLFX = 4096u,   // Remaster A4 gate: effect passes not applied in OLD periods (d3dx9.cpp apply_pass); opt-in only
    SOFTFX = 32768u,  // Remaster S3 trial: particle pass break + depth copy (OLD: none; needs RECOMP_REMASTER_SOFTFX=1)
    WETROAD = 65536u, // Remaster R2 wet roads: native wet-road shading on road shaders (OLD: the game's; needs RECOMP_REMASTER_WETROAD=1)
    SHADOWPCF = 131072u, // Remaster R2 softer sun shadows: 12-tap shadow filter (OLD: the single 2x2 compare; needs RECOMP_REMASTER_SHADOWPCF=1)
    POOLNULL = 262144u,  // Remaster P1 (TEST ONLY): pool-pass draws skipped in OLD periods; opt-in, needs RECOMP_REMASTER_POOLDRAW_NULL=1
    PBRWORLD = 524288u,  // Remaster run 12 PBR step 2 pilot: normal-mapped sun term on pilot facades (OLD: the game's; needs RECOMP_REMASTER_PBRWORLD=1)
    ROADSHEEN = 1048576u, // Remaster run 13 road sheen: measured GGX sun highlight on roads (OLD: the game's; needs RECOMP_REMASTER_ROADSHEEN=1)
    CARENV = 2097152u,    // Remaster run 13 car reflections: Fresnel-shaped environment strength (OLD: the game's; needs RECOMP_REMASTER_CARENV=1)
    WATER = 4194304u,     // Remaster run 13 natural water: ripple normal, sky Fresnel and sun glint on WORLD water (OLD: the game's; needs RECOMP_REMASTER_WATER=1)
    GLASS = 8388608u,     // Remaster run 13 natural glass: Fresnel-weighted building-glass reflection (OLD: the game's; needs RECOMP_REMASTER_GLASS=1)
    FEFLOOR = 16777216u,  // Remaster run 13 front-end floor shadows: the car's shadow on the garage floor (OLD: the floor as today; needs RECOMP_REMASTER_FEFLOOR=1)
    SKY = 1073741824u,    // Remaster run 13 Xbox 360 sky (ours): the 360-order sky combine and horizon in-scatter (OLD: the PC sky program; needs RECOMP_REMASTER_SKY=1)
    PARALLAX = 33554432u, // Remaster run 13 wall parallax: WORLDNORMALMAP uv offset from the normal-map alpha (OLD: the game's; needs RECOMP_REMASTER_PARALLAX=1)
    ATMOS = 67108864u,    // Remaster run 14 B1 height fog: the atmosphere pass before the particles (OLD: none; needs RECOMP_REMASTER_ATMOS=1); name heightfog
    // 536870912u (bit 29) FREE since run 18 (D-131): was GRASS (run 13 grass shells, retired by D-117; the grass code
    // was removed in run 29, D-167). Reserved for RT (technical-track.md 2.5).
    LITSMOKE = 134217728u, // Remaster run 14 lit smoke: particle relight on the core mod's kind marker (OLD: the game's; needs setting lit_smoke); name litsmk ("smoke" is the mod's denser-smoke switch)
    SSR = 268435456u,     // Remaster run 15 Wave B screen-space reflections (OLD: none; needs RECOMP_REMASTER_SSR=1); name ssr
    UPTEMP = 2147483648u, // Remaster run 15 Wave B MetalFX temporal (OLD: the spatial scaler on the same jittered backing, history reset on each switch; needs RECOMP_REMASTER_UPSCALE_TEMPORAL=1 with upscaling); name uptemp
    // Run 29 optimisation pass (OLD = the code before the item; each also has a kill env RECOMP_OPT_<NAME>=0):
    FXMAT = 1ull << 32,      // O4 effect binding delta: matrix constants copied whole, commits only when inputs moved; name fxmat
    DRAWRING = 1ull << 33,   // O5 draw hand-off: declared-stage walk, batch reserve; name drawring
    HOOKFAST = 1ull << 34,   // O2 hook plumbing: prebuilt dispatch chains, the render-list block read; name hookfast
    NATIVEFTOL = 1ull << 35, // O3 native _ftol (007c4b80); name nativeftol
    LABELCACHE = 1ull << 36, // O6 effect-label classes cached by label pointer; name labelcache
};
inline uint64_t switches() {
    static const uint64_t m = [] {
        const char *e = recomp_env("AB_SWITCHES");
        if (!e || !*e)
            return ~0ull & ~(uint64_t)(NULLDRAW | NULLFX | POOLNULL); // nulldraw, nullfx and poolnull must be named explicitly
        uint64_t bits = 0;
        if (strstr(e, "qpcfine"))
            bits |= QPCFINE;
        if (strstr(e, "ug1fog"))
            bits |= UG1FOG;
        if (strstr(e, "hlfade"))
            bits |= HLFADE;
        if (strstr(e, "a2c"))
            bits |= A2C;
        if (strstr(e, "nulldraw"))
            bits |= NULLDRAW;
        if (strstr(e, "nullfx"))
            bits |= NULLFX;
        if (strstr(e, "softfx"))
            bits |= SOFTFX;
        if (strstr(e, "wetroad"))
            bits |= WETROAD;
        if (strstr(e, "shadowpcf"))
            bits |= SHADOWPCF;
        if (strstr(e, "poolnull"))
            bits |= POOLNULL;
        if (strstr(e, "pbrworld"))
            bits |= PBRWORLD;
        if (strstr(e, "roadsheen"))
            bits |= ROADSHEEN;
        if (strstr(e, "carenv"))
            bits |= CARENV;
        if (strstr(e, "water"))
            bits |= WATER;
        if (strstr(e, "glass"))
            bits |= GLASS;
        if (strstr(e, "fefloor"))
            bits |= FEFLOOR;
        if (strstr(e, "parallax"))
            bits |= PARALLAX;
        if (strstr(e, "sky"))
            bits |= SKY;
        if (strstr(e, "litsmk"))
            bits |= LITSMOKE;
        if (strstr(e, "heightfog"))
            bits |= ATMOS;
        if (strstr(e, "ssr"))
            bits |= SSR;
        if (strstr(e, "uptemp"))
            bits |= UPTEMP;
        if (strstr(e, "fxmat"))
            bits |= FXMAT;
        if (strstr(e, "drawring"))
            bits |= DRAWRING;
        if (strstr(e, "hookfast"))
            bits |= HOOKFAST;
        if (strstr(e, "nativeftol"))
            bits |= NATIVEFTOL;
        if (strstr(e, "labelcache"))
            bits |= LABELCACHE;
        return bits;
    }();
    return m;
}
inline bool old(uint64_t which) {
    return old_phase.load(std::memory_order_relaxed) != 0 && (switches() & which) != 0;
}
} // namespace recomp_ab
