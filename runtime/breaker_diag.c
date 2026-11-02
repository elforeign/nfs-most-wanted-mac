// Observe the actual retail decisions, without re-running guards or queries.
// build_all.py makes diagnostic copies from the same translation as _orig.
#include "host.h"
#include "funcs.h"
#include <math.h>
#include <strings.h>

typedef struct {
    const char *reason;
    char scene[128];
    double age, distance, limit, score;
} BreakerDecision;
static _Thread_local BreakerDecision *decision;

static void breaker_observe(Cpu *c, uint32_t pc) {
    BreakerDecision *d = decision;
    if (!d) return;
    switch (pc) {
    case 0x00640FBC: d->reason = "skip: existing INIS"; break;
    case 0x00641036: d->reason = "skip: event cameras off"; break;
    case 0x0064104A: d->reason = "skip: splitscreen"; break;
    case 0x00641057: d->reason = "skip: speedbreaker camera"; break;
    case 0x00641064: d->reason = "skip: non-pursuit"; break;
    case 0x00641080: d->reason = "skip: stale request"; break;
    case 0x0064108B: d->age = ST(0); break;
    case 0x0064109C: d->reason = "skip: no player"; break;
    case 0x006410B0: d->reason = "skip: busted"; break;
    case 0x006410DB: d->reason = "skip: no simable"; break;
    case 0x006410EC: d->reason = "skip: no cop in range"; break;
    case 0x0064124B: {
        double distance = MF32(c->esp + 0x10);
        if (!isfinite(d->distance) || distance < d->distance) {
            d->distance = distance;
            d->limit = ST(0);
        }
        break;
    }
    case 0x006412B2: d->reason = "skip: factory"; break;
    case 0x006412FD: d->reason = "skip: no INIS interface"; break;
    case 0x00641314: d->reason = "skip: load not reached"; break;
    case 0x006F533B: d->reason = "skip: animations disabled"; break;
    case 0x006F5343: d->reason = "skip: NIS moments disabled"; break;
    case 0x006F535E: d->reason = "skip: load incomplete"; break;
    case 0x006F53B8: d->score = MF32(c->esp + 0x24); break;
    case 0x006F54BE: d->reason = "skip: marker score >= 75"; break;
    case 0x006F54D3: d->reason = "skip: scene allocation"; break;
    case 0x006F5550: d->reason = c->eax ? "PLAYED" : "skip: scene allocation"; break;
    // No marker (or an existing ICE scene): retail takes the streamed path.
    case 0x006F55AD: d->reason = "PLAYED"; break;
    }
}
#include "breaker_diag.inc"

static BreakerDecision begin(uint32_t scene) {
    BreakerDecision d = {.reason = "skip: empty scene", .age = NAN,
        .distance = NAN, .limit = NAN, .score = NAN};
    snprintf(d.scene, sizeof d.scene, "%.127s", scene ? gptr(scene) : "(null)");
    return d;
}
static void emit(const BreakerDecision *d) {
    diag_event("breaker: scene %s %s age %.3f cop_distance %.3f limit %.3f marker_score %.3f",
               d->scene, d->reason, d->age, d->distance, d->limit, d->score);
}
void f_00640F00_orig(Cpu *c);
__attribute__((noinline)) static void breaker_request(Cpu *c) {
    uint32_t kind = arg(c, 2);
    if (!kind || strcasecmp(gptr(kind), "Moment")) { f_00640F00_orig(c); return; }
    BreakerDecision d = begin(arg(c, 1));
    BreakerDecision *previous = decision;
    decision = &d;
    breaker_00640F00(c);
    decision = previous;
    emit(&d);
}
__attribute__((noinline)) static void breaker_load(Cpu *c) {
    // A Load nested in a constructor contributes to that request's single line.
    BreakerDecision d = begin(arg(c, 1));
    BreakerDecision *previous = decision;
    if (!previous) decision = &d;
    decision->reason = "skip: animations disabled";
    breaker_006F51F0(c);
    decision = previous;
    if (!previous) emit(&d);
}

// Keep all diagnostic stack frames behind this branch, including at -O2.
void f_00640F00(Cpu *c) {
    if (!diag_on) { f_00640F00_orig(c); return; }
    breaker_request(c);
}
void breaker_diag_load(Cpu *c) {
    if (!diag_on) { f_006F51F0_orig(c); return; }
    if (arg(c, 0) != 4) { f_006F51F0_orig(c); return; }
    breaker_load(c);
}
