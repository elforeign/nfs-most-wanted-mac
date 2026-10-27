// aa_setting_test.c - offline test that F10 > Picture > Anti-aliasing and the game's g_FSAALevel stay in sync (never starts the game):
//   cc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I ../recomp runtime/aa_setting_test.c runtime/aa_setting.c runtime/settings.c -o /tmp/aat && /tmp/aat
// The game's variable is a plain int here (the real one is guest memory at 0x00901808). Checks: (1) the row shows the game's value
// whichever way it changed (Video options -> row, row -> game variable), (2) a row change asks for the device Reset and stores the level
// in the registry once, an unchanged choice does neither, (3) the row is in Picture, (4) the row never writes app.txt.
// Result: PASS, 0 failures. Broken run (-DAA_TEST_BROKEN: the mock game ignores the row's apply, i.e. no sync from the row): FAIL, 4 failures.
#include <stdio.h>
#include <string.h>
#include "settings.h"

int diag_on = 0;
const char *game_dir;
static int napp;
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; ++napp; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }

static int game_level = 2, resets, reg_writes;
int aa_game_levels(void) { return 3; }
int aa_game_get(void) { return game_level; }
void aa_game_apply(int level) {
#ifndef AA_TEST_BROKEN
    if (level == game_level) return;
    game_level = level; ++reg_writes; ++resets;
#else
    (void)level;
#endif
}
static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)

int main(void) {
    settings_init();
    Setting *aa = settings_find("graphics.antialiasing");
    CHECK(aa && aa->category == SET_PICTURE);
    if (!aa) return 1;
    CHECK(settings_get("graphics.antialiasing") == 2 && aa->nchoices == 4);        // game says 4x
    game_level = 3;                                                                 // Video options changed it
    CHECK(settings_get("graphics.antialiasing") == 3);
    CHECK(settings_set(aa, 1));                                                     // F10 changed it
    CHECK(game_level == 1 && resets == 1 && reg_writes == 1);
    CHECK(settings_get("graphics.antialiasing") == 1);
    CHECK(!settings_set(aa, 1) && resets == 1);                                     // same choice: no second Reset
    CHECK(settings_set(aa, 0) && game_level == 0);
    CHECK(napp == 0);                                                               // nothing in app.txt
    printf("aa_setting_test: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
