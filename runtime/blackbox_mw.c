#include "blackbox.h"
#include "blackbox_mw_cinematics.h"
#include "blackbox_mw_fe.h"

void blackbox_game_frame(uint64_t frame, uint32_t flow, const BlackboxTiming *timing) {
    uint64_t now = blackbox_now_ns();
    blackbox_mw_fe(frame, flow, now);
    blackbox_mw_cinematics(frame, now);
    blackbox_record_frame(BLACKBOX_MW, frame, flow, timing, now);
}
