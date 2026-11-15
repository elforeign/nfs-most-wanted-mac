// Process-lived clock policy, separate from the engine's lighting block.
#pragma once
#include "daynight.h"

typedef enum { DAYNIGHT_GAME, DAYNIGHT_CYCLE, DAYNIGHT_FIXED } DaynightMode;
#define DAYNIGHT_SUN_HEIGHT 0.20f
#define DAYNIGHT_SECONDS_PER_HOUR 150.0

typedef struct {
    double hour, race_hour, authored_hour;
    float phase;
    int ready, in_race, phase_valid;
    unsigned race_id;
} DaynightClock;

void daynight_clock_step(DaynightClock *clock, const DaynightClockInput *input,
                        DaynightMode mode, double fixed_hour, double rate,
                        double start_hour, DaynightClockOutput *output);
