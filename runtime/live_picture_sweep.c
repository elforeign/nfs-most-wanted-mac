// Diagnostic-only driver for live_picture_check.sh. No work without a sweep: script step.
#include "settings.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern int diag_on;
void diag_event(const char *fmt, ...);

#ifndef NFS_PUBLIC
static int eligible(const Setting *s) { return s->apply && !s->restart; }
static int valued(const Setting *s) {
    return !s->derived && (s->type == SET_TOGGLE || s->type == SET_CHOICE || s->type == SET_SLIDER);
}
static int current(const Setting *s) { return s->get ? s->get() : s->value; }
static void change(Setting *s, const char *phase) {
    int want = s->def;
    if (!strcmp(phase, "on")) {
        if (s->type == SET_SLIDER) {
            int step = s->step > 0 ? s->step : 1;
            // Use the far endpoint so a missed undo has a visible signal where possible.
            want = s->def == s->min ? s->max : s->min;
            want = s->min + (want - s->min) / step * step;
        } else {
            int n = s->type == SET_TOGGLE ? 2 : s->nchoices;
            want = n > 1 ? (s->def + 1) % n : s->def;
        }
    }
    const char *broken = getenv("NFS_TEST_LIVE_SWEEP_SKIP_UNDO");
    int result = 0;
    if (strcmp(phase, "undo") || !broken || strcmp(broken, s->id)) {
        // The runner pins null audio and absent simulated pads. Exercise the row callback
        // through settings_set while retaining those backend switches.
        int locked = s->locked;
        s->locked = 0;
        result = settings_set(s, want);
        s->locked = locked;
    }
    diag_event("live sweep: %s %s result=%d read=%d want=%d default=%d", phase, s->id,
               result, current(s), want, s->def);
}
#endif

void live_picture_sweep_step(const char *phase) {
#ifndef NFS_PUBLIC
    if (!diag_on) return;
    if (!strcmp(phase, "init")) {
        for (int i = 0; i < settings_count(); ++i) {
            Setting *s = settings_at(i);
            if (!eligible(s)) continue;
            diag_event("live sweep: %s %s", valued(s) ? "row" : "command", s->id);
            if (valued(s)) change(s, "init");
        }
        return;
    }
    if (strcmp(phase, "on") && strcmp(phase, "undo")) return;
    const char *list = getenv("NFS_TEST_LIVE_SWEEP_ROWS");
    if (!list || !*list) return; // the untouched baseline
    char *copy = strdup(list), *save = NULL;
    if (!copy) { diag_event("live sweep: ERROR allocation"); return; }
    for (char *id = strtok_r(copy, ",", &save); id; id = strtok_r(NULL, ",", &save)) {
        Setting *s = settings_find(id);
        if (!s || !eligible(s) || !valued(s)) diag_event("live sweep: ERROR %s unavailable", id);
        else change(s, phase);
    }
    free(copy);
#else
    (void)phase;
#endif
}
