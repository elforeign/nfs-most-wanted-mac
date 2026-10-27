// aa_setting.c - F10 > Picture > Anti-aliasing: the game's own Video options > Antialiasing (g_FSAALevel).
// There is no copy of the value here: get() reads the game's variable, so the game's options screen and this row always
// agree, and the choice is saved in the game's registry.txt (pref "": never in app.txt). A change applies the way the
// options screen's Accept does (d3d9.m aa_game_apply): the level is stored and LoadedFlagMaybe makes the next frame run
// the game's device Reset, which rebuilds the MSAA back buffer. Offline test: runtime/aa_setting_test.c.
#include "settings.h"

int aa_game_levels(void);                 // d3d9.m: sample counts above 1 the device offers (0 until it exists)
int aa_game_get(void);                    // g_FSAALevel
void aa_game_apply(int level);            // g_FSAALevel = level, saved, device Reset next frame

static const char *const kAA[] = {"Off", "2x", "4x", "8x"};
static Setting s_aa;
static int aa_get(void) {
    int n = aa_game_levels() + 1;         // "Off" and what Metal offers (4 on this Mac); a later device doesn't shrink it below 1
    s_aa.nchoices = n < 1 ? 1 : n > 4 ? 4 : n;
    int v = aa_game_get();
    return v < 0 ? 0 : v > 3 ? 3 : v;
}
static void aa_apply(int v) { aa_game_apply(v); }
static Setting s_aa = {.id = "graphics.antialiasing", .category = SET_PICTURE, .order = 15, .label = "Anti-aliasing",
    .help = "Smooths jagged edges: the same setting as the game's Video options > Antialiasing, and a change redraws the screen once.",
    .type = SET_CHOICE, .choices = kAA, .nchoices = 4, .def = 2, .pref = "", .apply = aa_apply, .get = aa_get};
__attribute__((constructor)) static void aa_register(void) { settings_add(&s_aa); }
