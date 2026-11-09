// Pure shortcut state. Disabled bindings pass input through unchanged.
#pragma once
#include <stdint.h>
typedef struct {
    uint8_t keys[256];
    uint32_t pending, consumed, inject;
    uint32_t lastPad;
    double pendingAt, injectUntil;
    int passthrough, cancelled;
    int choice;
} ReplayInput;
typedef struct { uint32_t hide, inject; int save; } ReplayPad;
int replay_input_key(ReplayInput *, unsigned vk, int down, int repeat, int enabled, unsigned key, int blocked, int *save);
ReplayPad replay_input_pad(ReplayInput *, double now, uint32_t phys, int choice, int blocked);
void replay_input_cancel(ReplayInput *);
void replay_input_disconnect(ReplayInput *);
// Integration owned by portmenu; user32 reports focus and controller lifetime.
void replay_controls_cancel(void);
void replay_controls_disconnect(void);
int replay_key_in_use(unsigned scan);
