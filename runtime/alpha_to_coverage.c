// Smooth cut-out edges: live F10 choice, with separate pipelines for each state.
#include "alpha_to_coverage.h"
#include "settings.h"
#include <stdlib.h>
#include <string.h>

static int enabled = -1;
static void apply(int on) { enabled = on != 0; }

int alpha_to_coverage_draw(const uint32_t *rs, unsigned samples, int blended) {
    if (enabled < 0) {
        const char *s = getenv("NFS_ALPHA_TO_COVERAGE");
        enabled = s && (!strcmp(s, "on") || !strcmp(s, "1"));
    }
    // Equality/not-equal/never/always have no single edge to reconstruct.
    unsigned f = rs[25];
    return enabled && samples > 1 && !blended && rs[15] &&
           (f == 2 || f == 4 || f == 5 || f == 7);
}

static const char *const choices[] = {"Off", "On"};
static const char *const values[] = {"off", "on"};
static Setting setting = {
    .id = "graphics.alpha_to_coverage", .category = SET_WORLD, .order = 115,
    .label = "Smooth cut-out edges",
    .help = "Smooths fence, leaf and grass cut-outs when Anti-aliasing is 2x or higher. Off keeps the original sharp cut-off.",
    .type = SET_CHOICE, .choices = choices, .values = values, .nchoices = 2,
    .def = 0, .env = "NFS_ALPHA_TO_COVERAGE", .apply = apply
};
__attribute__((constructor)) static void register_setting(void) { settings_add(&setting); }
