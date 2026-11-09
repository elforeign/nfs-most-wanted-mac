// settings_live_test.c - offline test of the live_env plumbing (settings.c): a change to a live_env row exports its switch
// (unset at the default) before apply runs, so code that reads the switch while running sees the new value at once.
//   cc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I recomp runtime/settings_live_test.c runtime/settings.c runtime/offline_stubs.c
// Rows: a toggle (default On, like Contact shadows), a choice (default Sharp, like Car reflections), a locked row (its switch
// set at start: the environment decides) and a row without live_env (switch untouched, as before).
// Result: PASS, 0 failures. Broken run (-DLIVE_TEST_BROKEN: the rows lack live_env, i.e. the old behaviour): FAIL, 4 failures.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"

int diag_on = 0;
const char *game_dir;
const char *app_pref(const char *key) { (void)key; return NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }

static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)

#ifdef LIVE_TEST_BROKEN
#define LIVE 0
#else
#define LIVE 1
#endif
static char seen[64];     // what the switch held when apply ran
static int applied;
static const char *const kPcSharp[] = {"Standard", "Sharp"}, *const kPcSharpV[] = {"pc", "sharp"};
static void apply_toggle(int v) { (void)v; const char *e = getenv("LT_TOGGLE"); snprintf(seen, sizeof seen, "%s", e ? e : "(unset)"); ++applied; }
static Setting s_toggle = {.id = "t.toggle", .type = SET_TOGGLE, .def = 1, .env = "LT_TOGGLE", .live_env = LIVE, .apply = apply_toggle};
static Setting s_choice = {.id = "t.choice", .type = SET_CHOICE, .choices = kPcSharp, .values = kPcSharpV, .nchoices = 2, .def = 1,
                           .env = "LT_CHOICE", .live_env = LIVE};
static Setting s_locked = {.id = "t.locked", .type = SET_TOGGLE, .def = 1, .env = "LT_LOCKED", .live_env = LIVE};
static Setting s_plain = {.id = "t.plain", .type = SET_TOGGLE, .def = 1, .env = "LT_PLAIN", .restart = 1};

int main(void) {
    setenv("LT_LOCKED", "off", 1);
    settings_add(&s_toggle); settings_add(&s_choice); settings_add(&s_locked); settings_add(&s_plain);
    settings_init();
    CHECK(!getenv("LT_TOGGLE") && !getenv("LT_CHOICE"));
    CHECK(settings_set(&s_toggle, 0));
    CHECK(getenv("LT_TOGGLE") && !strcmp(getenv("LT_TOGGLE"), "off"));
    CHECK(applied == 1 && !strcmp(seen, "off"));                       // exported before apply
    CHECK(settings_set(&s_toggle, 1));
    CHECK(!getenv("LT_TOGGLE") && !strcmp(seen, "(unset)"));            // back at the default: the switch is unset
    CHECK(settings_set(&s_choice, 0));
    CHECK(getenv("LT_CHOICE") && !strcmp(getenv("LT_CHOICE"), "pc"));
    CHECK(settings_set(&s_choice, 1));
    CHECK(!getenv("LT_CHOICE"));
    CHECK(!settings_set(&s_locked, 1) && !strcmp(getenv("LT_LOCKED"), "off"));   // an env-locked row never changes
    CHECK(settings_set(&s_plain, 0));
    CHECK(!getenv("LT_PLAIN"));                                         // rows without live_env keep the old behaviour
    CHECK(s_toggle.live_env && !s_toggle.restart);
    printf("settings_live: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
