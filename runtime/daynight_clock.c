// Shared clock policy: free roam advances, races hold, career keeps authored time.
#include "daynight_clock.h"
#include "game_select.h"
#include <math.h>

#define PI 3.14159265358979323846

static double phase_hour(float phase) {
    return 6 + 12 * (fmaxf(.05f, fminf(.95f, phase)) - .05) / .9;
}

void daynight_clock_step(DaynightClock *clock, const DaynightClockInput *input,
                        DaynightMode mode, double fixed_hour, double rate,
                        double start_hour, DaynightClockOutput *output) {
    if (!input->active) {
        clock->in_race = clock->phase_valid = 0;
        return;
    }
    if (!clock->ready) {
        clock->hour = isfinite(start_hour) ? daynight_wrap(start_hour) : phase_hour(input->phase);
        clock->ready = 1;
    }
    if (input->race && (!clock->in_race || input->race_id != clock->race_id)) {
        clock->race_hour = clock->hour;
        clock->authored_hour = phase_hour(input->phase);
    }
    clock->in_race = input->race;
    clock->race_id = input->race_id;
    if (input->paused && clock->phase_valid && input->phase != clock->phase) {
        clock->hour = phase_hour(input->phase);
        clock->race_hour = clock->authored_hour = clock->hour;
    }
    if (!input->race && !input->paused && !input->override && mode != DAYNIGHT_FIXED &&
        isfinite(input->dt) && input->dt > 0 && isfinite(rate) && rate > 0)
        clock->hour = daynight_wrap(clock->hour + input->dt / rate);
    double hour = input->race ? (mode == DAYNIGHT_GAME && input->career == 2 ?
                                clock->authored_hour : clock->race_hour) : clock->hour;
    if (mode == DAYNIGHT_FIXED) hour = daynight_wrap(fixed_hour);
    if (input->override) hour = daynight_wrap(input->override_hour);
    output->hour = hour;
    // Fade the directional contribution at the horizon, preserving the engine's
    // phase, tilt and unit light direction for sky consumers. Height is chosen.
    double height = fabs(cos((hour - 12) * PI / 12) * cos(input->tilt));
    output->weight = (float)fmin(1, height / DAYNIGHT_SUN_HEIGHT);
    DaynightSample sample;
    daynight_sample(hour, input->azimuth, input->tilt, &sample);
    clock->phase = sample.phase;
    clock->phase_valid = 1;
}

#if NFS_GAME == NFS_GAME_MW
#include "host.h"
#include "settings.h"
#include <stdlib.h>
#include <strings.h>

static const char *const modes[] = {"Game", "Cycle", "Fixed"};
static Setting mode_setting = {
    .id = "gameplay.daynight_mode", .category = SET_GAMEPLAY, .label = "Day and night",
    .type = SET_CHOICE, .choices = modes, .nchoices = 3, .def = DAYNIGHT_GAME,
    .env = "NFS_DAYNIGHT_MODE", .hidden = 1, .live_env = 1
};
static Setting hour_setting = {
    .id = "gameplay.daynight_hour", .category = SET_GAMEPLAY, .label = "Fixed hour",
    .type = SET_SLIDER, .min = 0, .max = 2400, .step = 1, .unit = .01f, .def = 1200,
    .env = "NFS_DAYNIGHT_HOUR", .hidden = 1, .live_env = 1
};

static double number(const char *key, double fallback, double min, double max) {
    const char *text = getenv(key);
    if (!text || !*text) return fallback;
    char *end;
    double value = strtod(text, &end);
    return end != text && !*end && isfinite(value) && value >= min && value <= max ? value : fallback;
}

static void clock_diagnostic(const DaynightClockInput *input, const DaynightClockOutput *output,
                             DaynightMode mode) {
    static uint64_t last, frames, wraps;
    static double sum_dt, max_dt, max_t, max_phase, max_n, max_light;
    static double previous_hour;
    static DaynightSample previous;
    static float previous_weight;
    static int have_previous, previous_advancing;
    if (!input->active) { have_previous = 0; return; }
    ++frames;
    if (isfinite(input->dt) && input->dt > 0) {
        sum_dt += input->dt;
        max_dt = fmax(max_dt, input->dt);
    }
    DaynightSample sample;
    daynight_sample(output->hour, input->azimuth, input->tilt, &sample);
    int advancing = !input->race && !input->paused && !input->override && mode != DAYNIGHT_FIXED &&
                    isfinite(input->dt) && input->dt > 0;
    if (have_previous) {
        int wrap = advancing && previous_advancing && output->hour < previous_hour;
        double delta = fabs(output->hour - previous_hour);
        max_t = fmax(max_t, fmin(delta, 24 - delta));
        if (!wrap) max_phase = fmax(max_phase, fabs(sample.phase - previous.phase));
        max_n = fmax(max_n, fabs(sample.night - previous.night));
        double norm = 0;
        for (int i = 0; i < 3; ++i) {
            double change = output->weight * sample.light[i] - previous_weight * previous.light[i];
            norm += change * change;
        }
        double light_step = sqrt(norm);
        max_light = fmax(max_light, light_step);
        if (wrap) {
            ++wraps;
            diag_event("daynight_clock: wrap frame=%llu T=%.9f>%.9f phase=%.9f>%.9f "
                       "n=%.9f>%.9f w=%.9f>%.9f dlight=%.9g",
                       (unsigned long long)frames, previous_hour, output->hour,
                       previous.phase, sample.phase, previous.night, sample.night,
                       previous_weight, output->weight, light_step);
        }
    }
    previous_hour = output->hour; previous = sample;
    previous_weight = output->weight; have_previous = 1; previous_advancing = advancing;
    uint64_t now = host_ticks();
    if (last && now - last < 1000000000ull) return;
    last = now;
    diag_event("daynight_clock: T=%.9f mode=%s race=%d career=%d held=%d sum_dt=%.9f frames=%llu wall=%.6f "
               "wraps=%llu dt_max=%.9g dT=%.9g dphase=%.9g dn=%.9g dlight=%.9g phase=%.9f n=%.9f w=%.9f "
               "wlight=%.9f,%.9f,%.9f",
               output->hour, modes[mode], input->race, input->career,
               input->race || input->paused || input->override || mode == DAYNIGHT_FIXED,
               sum_dt, (unsigned long long)frames, now * 1e-9, (unsigned long long)wraps,
               max_dt, max_t, max_phase, max_n, max_light,
               sample.phase, sample.night, output->weight,
               output->weight * sample.light[0], output->weight * sample.light[1],
               output->weight * sample.light[2]);
}

static void clock_driver(const DaynightClockInput *input, DaynightClockOutput *output) {
    static DaynightClock clock;
    // settings_init exports saved preferences after constructors have run.
    const char *text = getenv("NFS_DAYNIGHT_MODE");
    DaynightMode mode = text && !strcasecmp(text, "cycle") ? DAYNIGHT_CYCLE :
                       text && !strcasecmp(text, "fixed") ? DAYNIGHT_FIXED : DAYNIGHT_GAME;
    double hour = number("NFS_DAYNIGHT_HOUR", 12, 0, 24);
    double rate = number("NFS_TEST_DAYNIGHT_RATE", DAYNIGHT_SECONDS_PER_HOUR, .000001, 1e9);
    double start = number("NFS_TEST_DAYNIGHT_START", NAN, 0, 24);
    daynight_clock_step(&clock, input, mode, hour, rate, start, output);
    if (diag_on) clock_diagnostic(input, output, mode);
}

__attribute__((constructor)) static void clock_register(void) {
    settings_add(&mode_setting);
    settings_add(&hour_setting);
    daynight_register_clock(clock_driver);
}
#endif
