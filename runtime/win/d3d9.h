#pragma once
#include "d3d9_state.h"
/* One import plus bounded shared callbacks; no main/prefix wiring here. */
int win_d3d9_register(void);
unsigned win_d3d9_callbacks(void);
