#include "traffic_detail.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static int enabled = -1;
void traffic_detail_set(int on) { enabled = on != 0; }
int traffic_detail_on(void) {
    if (enabled < 0) {
        const char *e = getenv("NFS_TRAFFIC_DETAIL");
        enabled = e && !strcmp(e, "on");
    }
    return enabled;
}
uint32_t traffic_detail_glass(uint32_t hash, uint32_t flags) {
    // Render 0074E23F decodes PVehicle +0x94 == 2 into flag 0x80000.
    // Reuse the windshield's existing optics and generated material values.
    return (flags & 0x80000u) && hash == 0x1FF8C329u && traffic_detail_on() ? 0x471A1DCAu : hash;
}

// Diagnostic held passes use the same pacing contract as the other simshot pairs.
static int ab_enabled = -1, ab_state, ab_saved;
static char ab_path[512];
int traffic_detail_ab_shot(char *path, size_t size) {
    if (ab_enabled < 0) {
        const char *e = getenv("BOOT_DIAG_TRAFFIC_AB");
        ab_enabled = e && !strcmp(e, "1");
    }
    if (!ab_enabled) return 0;
    if (ab_state == 2) {
        snprintf(path, size, "%s", ab_path);
        ab_state = 3;
        return 1;
    }
    if (!ab_state && strstr(path, "/simshot_") && strstr(path, ".png")) {
        const char *suffix = strrchr(path, '.');
        ab_saved = traffic_detail_on();
        snprintf(ab_path, sizeof ab_path, "%.*s_traffic_%s.png", (int)(suffix - path), path, ab_saved ? "off" : "on");
        ab_state = 1;
    }
    return 0;
}
int traffic_detail_ab_hold(void) {
    if (ab_state == 1) { traffic_detail_set(!ab_saved); ab_state = 2; return 1; }
    if (ab_state == 3) { traffic_detail_set(ab_saved); ab_state = 0; return 1; }
    return 0;
}
