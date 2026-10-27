// streaming.c - world section streaming ahead of the camera (pop-in when entering a new zone).
//
                                                                                        
//  UpdateCameraMovers 0047C880 feeds the player camera's position and velocity to TrackStreamer::PredictStreamingPosition
//  (TheTrackStreamer 009AA9D0, entry 0 at +0xC0: position +0, velocity +0xC, valid +0x1C, zone +0x1E). Each frame
//  ServiceGameState 007310B0 -> DetermineCurrentZones -> GetPredictedZone 0072DB80 takes the drivable section at the
//  camera (a 1.5 s / 100 m prediction only inside the few TrackPathZones of type 6); on a change SwitchZones 00730460
//  -> DetermineStreamingSections FUN_0072DD70 marks the zone's set needed (its LoadingSection's GetSectionsToLoad
//  0072C750, else the drivable section's visible list) and unloads every other loaded section at once. So the next
//  zone's sections start loading only when the camera crosses into it, and appear 0.1-0.2 s later, 200-800 m away.
//
// NFS_WORLD_STREAMING=ahead (F10 Graphics "World streaming", default ahead): the zones the camera is about to reach
// (drivable sections sampled along its velocity up to 3 s / 300 m ahead, and on a 60 m ring around it for turns) are
// streamed too: ServiceGameState forces a zone switch when a new one shows up (+0x2B0, as SetStreamingPosition does),
// and the FUN_0072DD70 hook adds their sets after the PC's (AddCurrentStreamingSections 00728060). A zone stays in the
// set 3 s after it was last sampled. At the race start the set is loaded behind the loading screen (below). The pool gets 128 MB more at boot (InitMemoryPool 0072D9C0; jettison picks are
// not ranked by distance, so the pool must hold the whole set). `pc`: the PC's behaviour and pool.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host.h"

#define TS 0x009AA9D0u                  // TheTrackStreamer
#define VSM 0x009A3B50u                 // TheVisibleSectionManager
#define FN_FIND_DRIVABLE 0x0072CA00u    // VisibleSectionManager::FindDrivableSection (this, bVector2*) -> section
#define FN_SECTIONS_TO_LOAD 0x0072C750u // VisibleSectionManager::GetSectionsToLoad (this, LoadingSection*, short*, max)
#define FN_ADD_CURRENT 0x00728060u      // TrackStreamer::AddCurrentStreamingSections (this, short*, n, position)
#define SIMTIME 0x009885D8u
#define POOL_EXTRA 0x8000000u           // +128 MB

static int mode = -1;                   // 0 pc, 1 ahead
static int boot_mode = -1;              // the mode the pool was sized for
static int stream_mode(void) {
    if (mode < 0) { const char *e = getenv("NFS_WORLD_STREAMING"); mode = e && !strcmp(e, "pc") ? 0 : 1; }
    return mode;
}
void streaming_set_mode(int v) {
    mode = v ? 1 : 0;
    if (diag_on) diag_event("streaming: requested %s, pool %s; %s", mode ? "ahead" : "pc", boot_mode == 1 ? "ahead" : "pc",
                            mode && boot_mode != 1 ? "restart required for larger pool" : "live at next ServiceGameState");
}

#define MAXZ 12
static struct { int16_t zone; uint64_t seen; } ahead[MAXZ];
static int nahead;
static uint32_t gpt, gbuf;              // guest scratch: a bVector2, a section list
static uint64_t frames;
static float sim(void) { return MF32(SIMTIME); }

static int find_zone(Cpu *c, float x, float y) {
    MF32(gpt) = x; MF32(gpt + 4) = y;
    c->ecx = VSM;
    uint32_t d = call_guest(c, FN_FIND_DRIVABLE, 1, &gpt);
    return d ? (int16_t)M16(d + 0xC) : -1;
}

static int ahead_add(int16_t z, uint64_t now) {   // 1: new
    for (int i = 0; i < nahead; ++i) if (ahead[i].zone == z) { ahead[i].seen = now; return 0; }
    if (nahead == MAXZ) {               // replace the stalest
        int o = 0;
        for (int i = 1; i < nahead; ++i) if (ahead[i].seen < ahead[o].seen) o = i;
        ahead[o].zone = z; ahead[o].seen = now;
        return 1;
    }
    ahead[nahead].zone = z; ahead[nahead].seen = now; ++nahead;
    return 1;
}
static void pool_log(const char *what);
static int ahead_live(int i, uint64_t now) { return now - ahead[i].seen < 3000000000ull; }

static void sample(Cpu *c) {
    uint32_t e = TS + 0xC0;
    if (!M8(e + 0x1C) || !M32(TS + 0x838) || M8(TS + 0x2B1)) return;   // no position, no pool, switching disabled
    if (!gpt) { gpt = galloc(8); gbuf = galloc(0x400); }
    float x = MF32(e), y = MF32(e + 4), vx = MF32(e + 0xC), vy = MF32(e + 0x10), s = sqrtf(vx * vx + vy * vy);
    int cur = (int16_t)M16(e + 0x1E), fresh = 0;
    uint64_t now = host_ticks();
    float pts[40][2];
    int np = 0;
    if (s > 1.0f) {
        float reach = s * 3.0f;
        if (reach < 60) reach = 60;
        if (reach > 300) reach = 300;
        for (float d = 20; d <= reach && np < 24; d += 20) { pts[np][0] = x + vx / s * d; pts[np][1] = y + vy / s * d; ++np; }
    }
    for (int k = 0; k < 8; ++k) {
        float a = (float)k * 0.785398f;
        pts[np][0] = x + 60 * cosf(a); pts[np][1] = y + 60 * sinf(a); ++np;
    }
    for (int i = 0; i < np; ++i) {
        int z = find_zone(c, pts[i][0], pts[i][1]);
        if (z > 0 && z != cur) fresh |= ahead_add((int16_t)z, now);
    }
    if (fresh) {
        M8(TS + 0x2B0) = 1;             // DetermineCurrentZones returns true: SwitchZones -> FUN_0072DD70 below
        if (diag_on) {
            char l[160] = "";
            for (int i = 0; i < nahead && strlen(l) < 140; ++i)
                if (ahead_live(i, now)) snprintf(l + strlen(l), 12, " %d", ahead[i].zone);
            diag_event("streaming: t %.2f camera zone %d, speed %.0f m/s: ahead zones%s", sim(), cur, s, l);
        }
    }
}

void f_007310B0_orig(Cpu *c);
void f_007310B0(Cpu *c) {               // TrackStreamer::ServiceGameState (this)
    if (stream_mode() == 1 && boot_mode == 1 && c->ecx == TS) {
        uint32_t ecx = c->ecx;
        if ((++frames & 1) == 0) sample(c);
        c->ecx = ecx;
    }
    f_007310B0_orig(c);
}

static int zone_set(Cpu *c, int16_t z) {  // the zone's streaming set into gbuf, as FUN_0072DD70 builds it
    for (uint32_t ls = M32(VSM + 0x20); ls && ls != VSM + 0x20; ls = M32(ls)) {
        int n = (int16_t)M16(ls + 0x18);
        for (int i = 0; i < n; ++i)
            if ((int16_t)M16(ls + 0x1A + 2u * (uint32_t)i) == z) {
                uint32_t a[3] = {ls, gbuf, 0x180};
                c->ecx = VSM;
                return (int)call_guest(c, FN_SECTIONS_TO_LOAD, 3, a);
            }
    }
    for (uint32_t d = M32(VSM + 0x10); d && d != VSM + 0x10; d = M32(d))
        if ((int16_t)M16(d + 0xC) == z) {
            int n = (int16_t)M16(d + 0x10);
            if (n > 0x180) n = 0x180;
            for (int i = 0; i < n; ++i) M16(gbuf + 2u * (uint32_t)i) = M16(d + 0x12 + 2u * (uint32_t)i);
            return n;
        }
    return 0;
}

void f_0072DD70_orig(Cpu *c);
void f_0072DD70(Cpu *c) {               // TrackStreamer::DetermineStreamingSections (fastcall this)
    uint32_t self = c->ecx;
    f_0072DD70_orig(c);
    if (self != TS || stream_mode() != 1 || boot_mode != 1 || !gbuf) return;
    uint64_t now = host_ticks();
    int cur = (int16_t)M16(TS + 0xDE), w = 0;
    static int last_cur = -1;           // the zone just left stays streamed (3 s) instead of unloading and reloading
    if (cur != last_cur) { if (last_cur > 0) ahead_add((int16_t)last_cur, now); last_cur = cur; }
    for (int i = 0; i < nahead; ++i) {
        if (!ahead_live(i, now) || ahead[i].zone == cur) continue;
        ahead[w++] = ahead[i];
        int n = zone_set(c, ahead[w - 1].zone);
        if (n > 0) { uint32_t a[3] = {gbuf, (uint32_t)n, 0}; c->ecx = TS; call_guest(c, FN_ADD_CURRENT, 3, a); }
    }
    nahead = w;
}

// The game's loading waits (race start: Sim::SetStream 006F1170 -> here, behind the loading screen): the look-ahead
// set is sampled afresh at the new position first, so the orig's forced SwitchZones adds it and
// WaitForCurrentLoadingToComplete waits for it like the PC's own set. Every race then starts with the same resident
// sections (emitters included), and later look-ahead loads only come once the car moves.
void f_007317A0_orig(Cpu *c);
void f_007317A0(Cpu *c) {               // TrackStreamer::BlockUntilLoadingComplete (this)
    uint32_t ecx = c->ecx;
    if (stream_mode() == 1 && boot_mode == 1 && ecx == TS) {
        nahead = 0;                     // zones sampled at an earlier position don't carry over
        sample(c);
        c->ecx = ecx;
        if (diag_on) diag_event("streaming: t %.2f frame %llu block until loading complete: %d ahead zones", sim(),
                                (unsigned long long)perf_nframes, nahead);
    }
    f_007317A0_orig(c);
    if (diag_on && ecx == TS) pool_log("block done");
}

void f_0072D9C0_orig(Cpu *c);
void f_0072D9C0(Cpu *c) {               // TrackStreamer::InitMemoryPool (this, size)
    boot_mode = stream_mode();
    uint32_t pc = arg(c, 0);
    if (boot_mode == 1) M32(c->esp + 4) = pc + POOL_EXTRA;
    if (diag_on) diag_event("streaming: pool %u KB (PC %u KB), mode %s", arg(c, 0) >> 10, pc >> 10, boot_mode ? "ahead" : "pc");
    f_0072D9C0_orig(c);
}

// ---- diagnostics (BOOT_DIAG): each zone switch's needed set and what the pool holds ----
static void pool_log(const char *what) {
    uint32_t base = M32(TS), n = M32(TS + 4);
    unsigned need = 0, need_kb = 0, res = 0, res_kb = 0, miss = 0, miss_kb = 0;
    for (uint32_t i = 0; i < n && base; ++i) {
        uint32_t s = base + 0x5Cu * i, st = M32(s + 0xC), kb = M32(s + 0x18) >> 10;
        if (st == 3 || st == 4) { ++res; res_kb += kb; }
        if (M8(s + 0xB)) { ++need; need_kb += kb; if (st != 3 && st != 4) { ++miss; miss_kb += kb; } }
    }
    diag_event("streaming: t %.2f %s zone %d: needed %u (%u KB), to load %u (%u KB), resident %u (%u KB), pool %u KB",
               sim(), what, (int16_t)M16(TS + 0xDE), need, need_kb, miss, miss_kb, res, res_kb, M32(TS + 0x83C) >> 10);
}
void f_00730460_orig(Cpu *c);
void f_00730460(Cpu *c) {               // TrackStreamer::SwitchZones (this, zones)
    uint32_t self = c->ecx;
    f_00730460_orig(c);
    if (diag_on && self == TS) pool_log("switch");
}
