#pragma once
#include "host.h"

// Bounded event adapters use generation-tagged handles and pin native events
// across GIL handovers; native Win32 waits provide atomic event consumption.
#define WIN_EVENT_LIMIT 128u
#define WIN_WAIT_LIMIT 64u
int win_sync_register(void);
