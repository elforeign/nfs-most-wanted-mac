// settings.h - the port's settings registry: what the F10 menu (portmenu.m) shows, saved in USERDATA/app.txt.
//
// A setting is a static `Setting` registered once at start-up with settings_add() (before the menu first opens; the
// menu lists a category's settings by their `order` key, ties by id: never by registration/constructor-link order). Values are ints: a toggle is 0/1, a choice is the
// index into `choices`, a slider is the number itself (min..max by step). Each setting:
//   - is saved in app.txt under `pref` (default: its id) as its `values[i]` string (choice/toggle; the label
//     lowercased when `values` is NULL) or as the number (slider), and only when the user changed it;
//   - may name the NFS_* switch that sets it (`env`). A switch set in the environment wins: the menu shows the item
//     locked with the switch's value. Otherwise, at start-up (settings_init) a saved value that differs from the
//     default is exported to that environment variable, so code that reads its switch once at start keeps working
//     with no changes: a `restart` setting needs nothing more;
//   - applies live through `apply(value)` (called on the game thread when the user changes it), or needs a restart
//     (`restart`: the menu shows the value that will be used and marks it "after restart");
//   - may provide `get()` for a value that can also change elsewhere (a menu-bar item, a shortcut); else the
//     registry's own copy is the value.
// Adding one (e.g. in looksliders.m):
//     static const char *const kBloom[] = {"Off", "Low", "Normal", "High"};
//     static void bloom_apply(int v) { bloom_level = v; }
//     static Setting s_bloom = {.id = "graphics.bloom", .category = SET_PICTURE, .order = 55, .label = "Bloom strength",
//                               .help = "How strongly bright areas glow.", .type = SET_CHOICE, .choices = kBloom,
//                               .nchoices = 4, .def = 2, .env = "NFS_BLOOM", .apply = bloom_apply};
//     __attribute__((constructor)) static void bloom_register(void) { settings_add(&s_bloom); }
// then read settings_get("graphics.bloom") (or your own copy kept by apply) where the feature runs.
#pragma once
#include <stdint.h>

// The F10 tabs, in order. Picture / World / Cars & cutscenes / Mirror replace the old flat Graphics list.
enum { SET_PICTURE, SET_WORLD, SET_CARS, SET_MIRROR, SET_DISPLAY, SET_AUDIO, SET_CONTROLS, SET_GAMEPLAY, SET_ONLINE, SET_DIAGNOSTICS,
       SET_NCATEGORIES };
typedef enum {
    SET_TOGGLE,      // Off / On (choices may rename them, e.g. {"PC", "X360"})
    SET_CHOICE,      // one of choices[0..nchoices-1]
    SET_SLIDER,      // min..max by step, shown with `format` (printf, one int)
    SET_TEXT,        // a short line typed on the keyboard (not saved); settings_text() reads it
    SET_ACTION,      // a command: apply(0) runs it; `value_text` may label it
    SET_INFO,        // a read-only row: `value_text(buf)` fills the value (refreshed while the menu is open)
    SET_HEADING,     // a section title inside a category (not selectable)
} SettingType;

typedef struct Setting Setting;
struct Setting {
    const char *id;                   // "graphics.car_paint": unique, also the app.txt key unless `pref` is set
    int category;                     // SET_PICTURE...
    int order;                        // position in category (ascending, ties by id); Audio Xbox 360 group: 60..90 (Reverb 65)
    const char *label;
    const char *help;                 // one line shown under the list for the selected item
    SettingType type;
    const char *const *choices;       // toggle/choice labels (toggle default {"Off", "On"})
    const char *const *values;        // the saved / switch value of each choice (NULL: the label, lowercased)
    int nchoices;
    int min, max, step;               // slider
    const char *format;               // slider value text, e.g. "%d%%" (default "%d")
    float unit;                       // slider: the saved / switch value is value x unit (0: 1), e.g. 0.01 for 140 -> 1.4
    int def;                          // default value
    const char *env;                  // NFS_* switch that overrides it (NULL: none)
    const char *pref;                 // app.txt key (NULL: id; "" never saved)
    int restart;                      // 1: takes effect at the next start
    int live_from_default;            // live only in a run started with the default; else needs a restart
    int own_pref;                     // the feature reads its app.txt key itself: never exported to `env`
    void (*apply)(int value);         // live change (game thread); SET_TEXT: submit on Enter; NULL for restart-only settings
    int (*get)(void);                 // the live value when it can change elsewhere (NULL: the registry's copy)
    void (*value_text)(char *buf, int n);   // SET_INFO / SET_ACTION: the value column (NULL: none)
    // ---- filled in by the registry ----
    int value;                        // current value (what get() returns when there's no get)
    int boot_value;                   // the value in effect since start-up (restart settings)
    int locked;                       // its env switch is set: the environment decides
    char env_text[48];                // the switch's value as set
    int saved;                        // came from app.txt
    int hidden;                       // absent from F10: preset-owned preference, or missing X360 files (PC fallback)
    char text[96];                    // SET_TEXT
};

void settings_add(Setting *s);              // register (start-up; static storage); reads app.txt and the environment
void settings_init(void);                   // boot.c, before the game starts: exports saved values to their switches
Setting *settings_find(const char *id);
void settings_dump(const char *path);       // BOOT_DIAG: the listed rows and the hidden ones
int settings_count(void);
Setting *settings_at(int i);                // registration order (not the menu's order: use settings_rows)
int settings_rows(int cat, Setting **out, int cap);   // a category's settings in menu order (order key, then id)
// The Picture tab's look choices include the platform rows and retail visual extras.
enum { PRESET_PORT, PRESET_X360, PRESET_PC, PRESET_MINE };
int settings_look_state(void);              // which of them the rows are in now (what the preset row shows)
int settings_look_choice(void);             // last requested choice, for cycling past a conflicting environment override
int settings_look_apply(int which);         // choose one; returns the rows it changed that need a restart (-1: no saved picks)
int settings_look_partial(void);            // an Xbox 360 look cannot use all its required files
int settings_get(const char *id);           // the current value (-1: no such setting)
int settings_set(Setting *s, int value);    // user change: clamps, applies live, saves; 0 if locked or unchanged
void settings_reset(Setting *s);            // back to the default (saved key removed)
const char *settings_text(const char *id);  // SET_TEXT value ("" if none)
const char *settings_value_name(const Setting *s, int value, char *buf, int n);   // choice label / slider text
const char *settings_source(const Setting *s);   // "env NFS_X", "app.txt" or "default" (bug reports)
extern const char *const settings_category_names[SET_NCATEGORIES];
