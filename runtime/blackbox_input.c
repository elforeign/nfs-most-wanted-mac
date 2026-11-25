/* Sample cached host observations on the guest frame thread. */
#include "blackbox.h"

int prompts_physical_input(void);
int prompts_pad_connected(void);
int user32_pad_observed_layout(void);

void blackbox_input(uint64_t frame, uint64_t now) {
    static BlackboxInput previous;
    static int have_state;
    if (!blackbox_enabled()) return;
    BlackboxInput state = {
        .source = prompts_physical_input() == 1,
        .connected = prompts_pad_connected() != 0,
        .layout = user32_pad_observed_layout(),
        .source_since = previous.source_since
    };
    state.changes = (state.source != previous.source) |
                    ((state.connected != previous.connected) << 1) |
                    ((state.layout != previous.layout) << 2);
    if (!have_state || (state.changes & 1)) state.source_since = now;
    if (!have_state || state.changes) {
        blackbox_record_input(&state, frame, now);
        previous = state;
        have_state = 1;
    }
}
