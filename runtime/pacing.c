// pacing.c - frame rate and game timing: the sim ticks at the display's refresh rate, one frame per tick.
//
// The game simulates in fixed ticks (Scheduler::Run, 00642E60; 1/60 s in the original): each main-loop pass adds the
// measured real time to an accumulator and runs as many whole ticks as it holds. A pass that comes in under one tick
// sleeps (step - elapsed) ms, but the time stamp is taken before the sleep, so the sleep is charged to the *next*
// pass: on a fast machine passes alternate ~16 ms / ~4 ms (about 95 fps) and the short ones run no tick at all, so
// they show the same picture again (pixel-identical: BOOT_DIAG_SEQ).
//
// Here (NFS_FRAMEPACE=sim, the default):
// - The tick rate is the display's highest refresh rate (120 Hz on ProMotion, 60 on most external displays;
//   NFS_SIMHZ=<hz> overrides). The sim takes its time step from the Scheduler, so physics, AI and the race clock
//   keep real time at any rate (sim time = wall time at 30/60/120/240 Hz; equal race clocks, speeds and lap
//   progress at equal sim times, BOOT_DIAG_SIMSHOTS). The few places that count ticks or loop passes instead of
//   time are corrected below: sim task rates and delays, the catch-up limit, and the real-time frame counter.
// - The limiter's sleep is skipped and Present waits until one tick period after the previous frame, so every pass
//   runs exactly one tick and every frame is a new picture, evenly spaced. The period is nudged to keep the
//   accumulator's leftover fraction near 0.5, so measurement jitter never produces a pass with zero or two ticks.
// - A Mac that can't keep up renders fewer frames and runs several ticks in each; game time stays correct down to
//   10 fps, as in the original.
// - The pre-race NIS waits 31 OnTask calls (ticks) before playing: counted in 1/60 s here (NISActivity::OnTask).
// - The HUD comes on once its set-up frames have also taken their 60 Hz time (FEngHud::DetermineHudFeatures), so the
//   countdown, GO and the racers' release keep their 60 Hz timing (GO was 0.05 s sooner at 120 Hz). Left: GO is
//   0.05 s sooner at 120 Hz than at 60 because each tick-quantized wait costs 1/120 s instead of 1/60 (README).
// NFS_FRAMEPACE=game keeps the original: 60 Hz ticks, the game's limiter and real-time counter.
#include <mach/mach_time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "funcs.h"
#include "host.h"

#define SCHEDULER 0x009203B0u              // Scheduler::fgScheduler
#define RUN_SLEEP_RET 0x00642ED7u          // return address of Scheduler::Run's bThreadYield(ms) call

void gil_acquire(void);
void gil_release(void);
void report_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));   // report.m

static int pace_on(void) {
    static int on = -1;
    if (on < 0) on = !(getenv("NFS_FRAMEPACE") && !strcmp(getenv("NFS_FRAMEPACE"), "game"));
    return on;
}

// NFS_LOCKSTEP=1 (a test switch; refs.sh sets it for the reference frames): a deterministic clock in races. Every
// frame is exactly one sim tick of game time and of the game's real time, whatever the wall clock did: at the end of each
// main-loop pass AdvanceRealTime decides: a pass that presented a frame and wasn't held (the probe's held passes) gives
// the Scheduler's target timer whatever brings its leftover tick fraction back to 0.5 after one tick, and the next
// pass 1/hz s in PrepareRealTimestep (RealTimeElapsed, which the camera movers, emitters and sound integrate with);
// any other pass (the race start and the NIS load run 2-3 passes per frame) gives none. Decided per pass from its
// Present and hold only, never from the wall time (a tick per pass gave 3-tick frames; deciding from RealTimeElapsed
// let the wall-timed pass that enters the race add a tick, and left the held passes' camera movers a tick). Without it a slow frame is caught up with several ticks in the next pass, which
// changes the autodrive car's path: a 60 ms frame at the hood-camera switch (sim 20.7 s, gate.KlTdlx 2026-10-21) gave
// one pass of 3 ticks and a hood frame at sim 25 s 6.4 % of whose blocks differed (README "Reference frames"). A Mac
// that can't keep up then runs the race slower instead of skipping ticks. Front end and loading screens are unchanged
// (game-flow state 6 only). The scripted keys (BOOT_KEYS seq:) count frames instead of wall time with it.
int pacing_lockstep(void) {
    static int on = -1;
    if (on < 0) on = getenv("NFS_LOCKSTEP") && atoi(getenv("NFS_LOCKSTEP")) > 0;
    return on && pace_on();
}
int pacing_lockstep_now(void) { return pacing_lockstep() && M32(0x00925E90u) == 6; }   // racing (GAMEFLOW_STATE)
#define lockstep_now pacing_lockstep_now
static int sim_hz;
static uint32_t presents, presents_adv;    // Presents so far (pacing_present), and at the last AdvanceRealTime
static int lock_tick;                      // NFS_LOCKSTEP: the last pass presented a frame and wasn't held, so the next
                                           // pass gets one tick of real time (decided at its AdvanceRealTime)
static int catching_up;                    // Scheduler requested multiple ticks since the last Present

// PrepareRealTimestep(float seconds) (00661280, cdecl; the main loop passes the wall time since its last pass): with
// NFS_LOCKSTEP one tick's worth after a pass that presented a frame and wasn't held (the probe's and the A/B
// switches' held passes draw the same scene: no camera mover or emitter may move either), none otherwise. It quantizes to 1/3600 s (RealTimeElapsed) and carries the rest, so 1/hz s comes out
// exactly for 60 Hz and its divisors of 3600.
void f_00661280(Cpu *c) {
    if (sim_hz && lockstep_now()) MF32(c->esp + 4) = lock_tick ? 1.0f / (float)sim_hz : 0.0f;
    f_00661280_orig(c);
}

// bThreadYield(int ms) (0064CF00, a jump to THREAD_yield): skipped when Scheduler::Run's limiter calls it.
void f_0064CF00(Cpu *c) {
    if (pace_on() && M32(c->esp) == RUN_SLEEP_RET) { c->esp += 4; return; }
    f_0064CF00_orig(c);
}

// The sim's tick rate: Scheduler::Init's argument (1/60 s in the game). SimSystem takes its time step from the
// Scheduler (SimSystem+0x18 = step, +0x1C = step x sim speed) and physics, AI and the race clock integrate with it,
// so the game keeps real time at any rate (checked at 60/120/240 Hz: sim time = wall time, same race clock, speeds
// and lap progress at equal sim times; BOOT_DIAG_SIMSHOTS). Default: the display's highest refresh rate, so every
// refresh shows a new sim state; NFS_SIMHZ=<hz> picks one (30-480), NFS_FRAMEPACE=game keeps 60. A Mac that can't
// keep up still keeps time: the Scheduler runs several ticks in a slow frame.
int user32_display_hz(void);
int pacing_sim_hz(void) { return sim_hz; }
void f_006484E0(Cpu *c) {
    if (!sim_hz) {
        const char *e = getenv("NFS_SIMHZ");
        int hz = e ? atoi(e) : 0;
        if (!pace_on()) hz = 60;
        else if (hz < 30 || hz > 480) hz = user32_display_hz();
        if (hz < 30) hz = 60;
        if (hz > 480) hz = 480;
        sim_hz = hz;
        fprintf(stderr, "boot: sim at %d Hz%s\n", hz, e ? " (NFS_SIMHZ)" : pace_on() ? " (display refresh)" : "");
    }
    MF32(c->esp + 4) = 1.0f / (float)sim_hz;
    f_006484E0_orig(c);
}

// SimTask::SimTask(index, rate, taskable, delay, mode) (006ECCD0, thiscall): sim tasks count ticks. A task with rate
// r < 1 runs every 1/r ticks with dt = step / r, and `delay` is a number of ticks before its first run. At a faster
// tick rate those would run more often and start sooner (the race countdown's 35-tick wait came 0.29 s early at
// 120 Hz), so rates below 1 are scaled by 60/hz and delays by hz/60: every task keeps its original real-time rate,
// dt and delay, while every-tick tasks (physics, rate 1) run at the full tick rate.
void diag_timeline(const char *fmt, ...);
void f_006ECCD0(Cpu *c) {
    if (diag_on) {
        uint32_t off = 0;
        const char *from = diag_symbol(M32(c->esp), &off);
        diag_timeline("SimTask %08X rate %g delay %g mode %u taskable %08X vt %08X (from %s+0x%X)", c->ecx, MF32(c->esp + 8), MF32(c->esp + 16), M32(c->esp + 20), M32(c->esp + 12), M32(c->esp + 12) ? M32(M32(c->esp + 12)) : 0,
                      from ? from : "?", off);
    }
    if (sim_hz && sim_hz != 60) {
        float k = 60.0f / (float)sim_hz, rate = MF32(c->esp + 8), delay = MF32(c->esp + 16);
        if (rate > 0.0f && rate < 1.0f) MF32(c->esp + 8) = rate * k;
        if (delay > 0.0f) MF32(c->esp + 16) = delay / k;
    }
    f_006ECCD0_orig(c);
}

// NISActivity::OnTask (006F58D0, thiscall on the NISActivity's ITaskable, an every-tick sim task): a loaded NIS
// (state +0x2EC == 3) starts playing once this has run more than 30 times (+0x3D4 counts the calls), i.e. 31 ticks
// (0.52 s) after loading. At 120 Hz that came 0.26 s sooner, and with it the whole pre-race intro, the countdown and
// GO (timeline.log, BOOT_DIAG). Here the count advances by 60/hz per call, so the wait is 31/60 s at any tick rate.
void f_006F58D0(Cpu *c) {
    if (!sim_hz || sim_hz == 60) { f_006F58D0_orig(c); return; }
    static uint32_t owner, written, acc;      // acc: fraction of a 60 Hz count, in 1/sim_hz units
    uint32_t self = c->ecx, state = M32(self + 0x2EC), n = M32(self + 0x3D4);
    f_006F58D0_orig(c);
    if (state != 3 || M32(self + 0x3D4) != n + 1) return;          // not counting (playing, or started now)
    if (self != owner || n != written) { owner = self; acc = 0; }    // another NIS, or the game reset the count
    acc += 60;
    uint32_t whole = acc / (uint32_t)sim_hz;
    acc -= whole * (uint32_t)sim_hz;
    M32(self + 0x3D4) = written = n + whole;
}

// The HUD's set-up after FEngHud::FEngHud (005A6600, thiscall; LocalPlayer::SetHud makes it at the end of the pre-race
// intro) is paced by frames: its resources (the HUD texture pack, then the minimap and tach textures, each loaded
// through a completion callback serviced once a frame; HudResourceManager::LoadingCompleteCallback 005A0710) are
// ready on the HUD's 6th update, and FEngHud::DetermineHudFeatures (0057CA60, called once per FEngHud::Update)
// returns 0 until then, so no HUD element updates. The countdown (Countdown::Update 005679B0) shows "3" on its first
// update and steps by WorldTimer (sim time) from there, and GO posts MCountdownDone, which releases the racers
// (Game_NotifyCountdownDone): 6 frames are 0.1 s at 60 Hz but 0.05 s at 120, so the race started 0.05 s sooner.
// Here the HUD comes on no sooner after its creation than the same number of frames took at 60 Hz: if the game
// enables it on update k (k frames after FEngHud::FEngHud), it stays off until k/60 s of ticks have passed.
// At 60 Hz (or NFS_FRAMEPACE=game) nothing changes.
static uint32_t hud_obj, hud_tick0, hud_frames, hud_on_frame;    // the newest FEngHud; 0 frames: no longer held
static uint32_t sched_ticks(void) { uint32_t s = M32(SCHEDULER); return s ? M32(s + 0x28) : 0; }
void f_005A6600(Cpu *c) {
    if (diag_on) {
        uint32_t off = 0;
        const char *from = diag_symbol(M32(c->esp), &off);
        diag_timeline("call FEngHud::FEngHud from %s", from ? from : "?");
    }
    hud_obj = c->ecx;
    hud_tick0 = sched_ticks();
    hud_frames = 1;
    hud_on_frame = 0;
    f_005A6600_orig(c);
}
void diag_hud_features(Cpu *c, uint32_t self, uint32_t player, int held);
void f_0057CA60(Cpu *c) {
    uint32_t self = c->ecx, player = M32(c->esp + 4);
    f_0057CA60_orig(c);
    int held = 0;
    if (sim_hz > 60 && self == hud_obj && hud_frames) {
        if (!hud_on_frame && (c->eax || c->edx)) hud_on_frame = hud_frames;
        int32_t ticks = (int32_t)(sched_ticks() - hud_tick0);
        if (hud_on_frame && (ticks < 0 || (uint32_t)ticks * 60u >= hud_on_frame * (uint32_t)sim_hz)) hud_frames = 0;
        else {
            ++hud_frames;
            if (hud_on_frame) { c->eax = c->edx = 0; held = 1; }
        }
    }
    if (diag_on) diag_hud_features(c, self, player, held);
}

// Scheduler::RunNormalSpeed(n) (00642DB0, thiscall): runs n ticks, at most 6 per frame (2 with a debug flag), so a
// frame slower than 6 ticks loses time. That limit was 100 ms at 60 Hz; at a higher tick rate it is scaled to the
// same real time (the extra ticks run here, the rest and the per-frame part in the original), so a Mac that can't
// keep up with the tick rate still keeps game time down to 10 fps, as the original did.
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args);
void f_00642DB0(Cpu *c) {
    uint32_t self = c->ecx, n = M32(c->esp + 4);
    if (n > 1) catching_up = 1;
    uint32_t cap = (M8(0x009203ACu) == 0) * 4 + 2;
    if (sim_hz > 60 && M32(0x009B0FA0u) != 5 && !(M8(self + 0x24) && n) && n > cap) {
        uint32_t want = n < cap * (uint32_t)sim_hz / 60 ? n : cap * (uint32_t)sim_hz / 60;
        for (uint32_t k = cap; k < want; ++k) {                 // the ticks beyond the original limit
            for (uint32_t phase = 0; phase < 8; ++phase) {
                uint32_t head = M32(M32(self + 0x10) + 4);
                for (uint32_t node = M32(head); node != M32(M32(self + 0x10) + 4); node = M32(node)) {
                    uint32_t task = M32(node + 8), args[2] = {M32(self + 0x28), phase}, esp = c->esp;
                    c->ecx = task;
                    call_guest(c, M32(M32(task) + 4), 2, args);
                    c->esp = esp;
                }
            }
            uint32_t esp = c->esp;
            call_guest(c, 0x00633EF0u, 0, NULL);                // queued events
            c->esp = esp;
            M32(self + 0x28) += 1;
        }
        c->ecx = self;
        M32(c->esp + 4) = cap;
    }
    f_00642DB0_orig(c);
}

// AdvanceRealTime (0065C6D0), end of every main-loop pass: RealTimeFrames counts real time in 1/60 s units, carrying
// the remainder. The original forces at least one unit per pass, so above 60 fps everything timed by it ran fast:
// the camera movers (RealTime), streaming, texture animations, speech, the pre-race intro (GO came 0.34 s early at
// 120 fps, 0.5 s at 240). Here a pass adds only the whole units of real time that elapsed (0 at high frame rates),
// unless NFS_FRAMEPACE=game keeps the game's behaviour. Same float arithmetic as the original otherwise.
#define RealTimeElapsed 0x009259BCu        // float, seconds of this pass
#define RealTimeErr     0x00925960u        // float, carried remainder (RealTimeElapsedFrame)
#define RealTimeFrames  0x008F82F4u
#define RealTimeFramesElapsed 0x008F82FCu
#define RealTime        0x008F82F8u
#define RealTimer       0x00925B14u        // 1/4000 s
#define RealLoopCounter 0x00925968u
// The F10 menu open in a race (portmenu.m) holds real time the same way for as long as it's open: RealTimer is the
// Scheduler's target timer (Scheduler::GetTargetTimer 006277D0), so no sim tick runs and the race stands still while
// frames keep rendering; the Scheduler's accumulator is untouched, so closing resumes from the same point.
static void hold_real_time(Cpu *c) {
    MF32(RealTimeElapsed) = 0.0f;
    uint32_t esp = c->esp;
    M32(0x00925680u) = call_guest(c, 0x0045CE40u, 0, NULL);          // the main loop's ticker = bGetTicker()
    c->esp = esp;
}
void f_0065C6D0(Cpu *c) {
    void portmenu_pass(Cpu *c);
    int portmenu_hold(void);
    portmenu_pass(c);                      // the game's sound paused / resumed with the menu
    if (diag_on) { void diag_warp_pass(Cpu *c); diag_warp_pass(c); void lanserver_selftest_pass(Cpu *c); lanserver_selftest_pass(c); }
    if (!pace_on()) { if (portmenu_hold()) hold_real_time(c); f_0065C6D0_orig(c); return; }
    int postfx_ab_hold(void);              // x360_post.m, BOOT_DIAG_POSTFX_AB: drop this pass's time (the next draws the same scene)
    int carpaint_ab_hold(void);            // materials.m, BOOT_DIAG_CARPAINT_AB: same-frame X360 / PC car paint
    int detail_ab_hold(void);              // detail.c, BOOT_DIAG_DETAIL_AB: same-frame detail distances
    int held = postfx_ab_hold() | probe_hold() | carpaint_ab_hold() | portmenu_hold() | detail_ab_hold();   // probe.m, BOOT_DIAG_PROBE: the same (base, signature, after)
    if (held) hold_real_time(c);
    uint32_t s = M32(SCHEDULER);
    int frame_pass = presents != presents_adv, lock = sim_hz && s && lockstep_now();
    presents_adv = presents;
    if (lock) {                            // NFS_LOCKSTEP: this pass's real time is one tick if it was a frame, else none
        lock_tick = frame_pass && !held;   // (the pass that enters the race was timed by the wall clock before: dropped)
        MF32(RealTimeElapsed) = lock_tick ? 1.0f / (float)sim_hz : 0.0f;
    } else
        lock_tick = 0;
    float elapsed = MF32(RealTimeElapsed) + MF32(RealTimeErr);
    int32_t frames = (int32_t)(60.0f * elapsed);
    MF32(RealTimeErr) = elapsed - (float)frames * (1.0f / 60.0f);
    M32(RealTimeFrames) += (uint32_t)frames;
    M32(RealTimeFramesElapsed) = (uint32_t)frames;
    M32(RealTime) = M32(RealTimeFrames);
    if (lock && lock_tick) {               // one tick in the next pass, leaving the Scheduler's fraction at 0.5
        float step = MF32(s + 0x20), frac = MF32(s + 0x1C);
        if (!(frac >= 0.0f && frac <= 1.0f)) frac = 0.5f;
        M32(RealTimer) += (uint32_t)(int32_t)((1.5f - frac) * step * 4000.0f + 0.5f);
    } else
        M32(RealTimer) += (uint32_t)(int32_t)(MF32(RealTimeElapsed) * 4000.0f + 0.5f);
    M32(RealLoopCounter) += 1;
    c->esp += 4;                           // cdecl, no arguments: pop the return address
}

static uint64_t ns_to_abs(uint64_t ns) {
    static mach_timebase_info_data_t tb;
    if (!tb.denom) mach_timebase_info(&tb);
    return ns / tb.numer * tb.denom + ns % tb.numer * tb.denom / tb.numer;
}

// After Present: wait for the next frame's slot, with the GIL released so other guest threads run meanwhile.
void pacing_present(void) {
    ++presents;
    int behind = catching_up;
    catching_up = 0;
    static uint64_t deadline;
    static int slow = -1;                  // BOOT_DIAG_SLOWFRAME=<ms>: pad every frame (a slower Mac, for timing tests)
    if (slow < 0) slow = getenv("BOOT_DIAG_SLOWFRAME") ? atoi(getenv("BOOT_DIAG_SLOWFRAME")) : 0;
    if (slow > 0) { gil_release(); usleep((useconds_t)slow * 1000); gil_acquire(); }
    // BOOT_DIAG_HITCH=<sim s>:<ms>: one frame padded by ms once the race's sim time passes s (a single slow frame, as a
    // shader compile or a busy machine makes one; refs.sh's NFS_LOCKSTEP must keep the frames after it the same)
    static float hitch_at = -2, hitch_race0 = -1;
    static int hitch_ms;
    if (hitch_at == -2) {
        hitch_at = -1;
        const char *h = getenv("BOOT_DIAG_HITCH");
        if (h && sscanf(h, "%f:%d", &hitch_at, &hitch_ms) != 2) hitch_at = -1;
    }
    if (hitch_at >= 0 && M32(0x00925E90u) == 6) {
        float now;
        memcpy(&now, G + 0x009885D8u, 4);                         // Sim::GetTime
        if (hitch_race0 < 0) hitch_race0 = now;
        if (now - hitch_race0 >= hitch_at) {
            if (diag_on) diag_event("hitch: %d ms frame at sim %.3f", hitch_ms, now - hitch_race0);
            hitch_at = -1;
            gil_release(); usleep((useconds_t)hitch_ms * 1000); gil_acquire();
        }
    }
    if (!pace_on()) return;
    uint32_t s = M32(SCHEDULER);
    if (!s) return;
    float step = MF32(s + 0x20), frac = MF32(s + 0x1C);
    if (!(step > 0.001f && step < 0.1f) || !(frac >= 0.0f && frac <= 1.0f)) return;
    uint64_t period = (uint64_t)(step * 1e9), now = host_ticks();
    // The sim needed more than one tick: let it recover without a pacing sleep, even if the old slot is ahead.
    // Lockstep's deterministic game/real-time clocks and its frame slots keep their existing policy.
    if (behind && !lockstep_now()) { deadline = now; return; }
    // keep the leftover fraction near the middle: a pass that measures slightly short or long still runs one tick
    int64_t adjust = (int64_t)((0.5f - frac) * 0.1f * (float)period);
    deadline += (uint64_t)((int64_t)period + adjust);
    if (deadline + period < now || deadline > now + 2 * period) deadline = now;   // behind by a frame, or first call
    if (deadline <= now) return;
    gil_release();
    // Keep the original absolute deadline: releasing the GIL or being descheduled can already take us past it.
    // Adding (deadline - old_now) to a fresh Mach timestamp would sleep again even though we're now behind.
    mach_wait_until(ns_to_abs(deadline));
    uint64_t woke = host_ticks();
    gil_acquire();
    uint64_t acquired = host_ticks();
    diag_idle_ns[IDLE_SLEEP] += woke - now;
    diag_idle_ns[IDLE_GIL] += acquired - woke;
    if (acquired - now > 2 * period)
        report_note("pacing wait overrun: requested %.2f ms, sleep %.2f ms, GIL %.2f ms",
                    (deadline - now) / 1e6, (woke - now) / 1e6, (acquired - woke) / 1e6);
}
