#include "blackbox.h"
void blackbox_game_frame(uint64_t frame, uint32_t flow, const BlackboxTiming *timing) {
    blackbox_record_frame(BLACKBOX_MW, frame, flow, timing, blackbox_now_ns());
}
