// settings_menu.cpp - see settings_menu.h. Test72 (NFSMW graphics/mods plan, Revision 3, slice 1).
#include "settings_menu.h"
#include "../runtime/help_seam.h"
#include "controls_settings.h"
#include "display_settings.h"
#include "game_config.h"
#include "mods_internal.h"
#include "../runtime/mods_seam.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <mutex>

#ifndef RECOMP_SETTINGS_PAGE_TABS
#define RECOMP_SETTINGS_PAGE_TABS 0
#endif

namespace {

enum Source : uint8_t {
    SRC_HEADER,
    SRC_DISPLAY,
    SRC_CONTROLS,
    SRC_SETTING,
    SRC_MENU,
    SRC_APPLY,
    SRC_DISCARD,
    SRC_RESTORE_GFX,  // Test85: Restore graphics defaults (every picture section, not display/resolution)
    SRC_RESTORE_GROUP, // Test85: a section's Restore defaults (index: kSections entry)
    SRC_ADVANCED,      // Remaster run 13: shows or hides the Graphics page's Advanced section (index: its row count)
    SRC_BUG_REPORT,    // NFSMW 3.0.4: Help > Save bug report (host/bug_report.cpp)
    SRC_UPDATES,       // NFSMW 3.0.4: Help > Check for updates (host/update_check.cpp; opt-in, one request)
};
struct Item {
    Source src;
    uint32_t index = 0; // display/controls row, settings entry, menu entry
    std::string title;  // headers
};

bool g_open = false;
bool g_advanced = false; // Remaster run 13: the Advanced section is closed each time the menu opens
int g_tab = 0;
size_t g_cursor = 0; // index into the current tab's items
// The Graphics draft: "d:<display row>" or "s:<mod id>/<key>" -> the value Apply will commit.
std::map<std::string, int64_t> g_draft;
// Remaster run 16: drafts made by a Restore. Apply removes these settings' overrides (they follow their defaults
// again) instead of choosing the default value; an edit of the same line after the Restore makes it a choice again.
std::set<std::string> g_restore;
void clear_draft() {
    g_draft.clear();
    g_restore.clear();
}
std::string g_status;
// The keyboard and pad reach the menu on the input thread; the presenter snapshots it on the thread
// that seals a frame. One lock, recursive because a mod action may open or close the page.
std::recursive_mutex g_mu;
bool g_applying = false; // Apply was chosen off the main thread and waits for its next frame
std::mutex g_main_mu;    // the calls waiting for the main thread
std::vector<std::function<void()>> g_main_calls;
std::atomic<bool> g_main_waiting{false};
#ifdef POPM_TESTING
bool g_force_enabled = false;
#endif
// Short names, so seven fit the tab bar (settings_menu_draw.cpp also shrinks them to fit).
// NFSMW run 30 (owner D-174): pages named by what a player wants to change; the enum names stay.
const char *const kTabs[] = {"Graphics", "Display", "Audio", "Interface", "Gameplay", "Mods", "Controls"};
constexpr int kTabCount = MENU_TAB_COUNT;
static_assert(sizeof kTabs / sizeof kTabs[0] == MENU_TAB_COUNT, "a name for every page");

struct Setting {
    uint32_t entry = 0, owner = 0;
    const char *mod_id = "", *key = "", *label = "", *group = "", *help = "", *apply = "", *choices = "",
               *unit = "";
    int32_t kind = 0;
    int64_t value = 0, mn = 0, mx = 0, step = 1;
};
bool setting(uint32_t i, Setting *s) {
    s->entry = i;
    if (!mods_settings_entry(i, &s->owner, &s->mod_id, &s->key, &s->label, &s->kind, &s->value, &s->mn,
                             &s->mx))
        return false;
    mods_settings_presentation(i, &s->step, &s->unit);
    mods_settings_meta(i, &s->group, &s->help, &s->apply, &s->choices);
    for (const char **p : {&s->mod_id, &s->key, &s->label, &s->group, &s->help, &s->apply, &s->choices, &s->unit})
        if (!*p)
            *p = "";
    if (s->step < 1)
        s->step = 1;
    return true;
}
std::string draft_key(const Setting &s) {
    return std::string("s:") + s.mod_id + "/" + s.key;
}
std::string draft_key(int display_row) {
    return "d:" + std::to_string(display_row);
}
bool drafted(const Setting &s) {
    return !strcmp(s.apply, "renderer");
}
bool drafted(int display_row) {
    return display_row == DISPLAY_FILTERING;
}
// Remaster run 13 (owner 8 Oct: "not a list of tens of settings"): the Graphics page lists only the manifest
// group "graphics" (the main list); every other Graphics section sits in one Advanced section, closed each time
// the menu opens. Group "hidden" is kept and saved but never listed.
bool main_graphics_setting(const Setting &s) {
    return !strcmp(s.group, "graphics");
}
// What "Restore graphics defaults" covers: World filtering, and every picture setting (a section marked `picture`
// below, on the Graphics and Display pages; since NFSMW run 30 not Interface or Gameplay) that applies now or at Apply. Not the display rows
// (window, frame limit, overlay) and not settings that need a restart (resolution, widescreen): those are the
// player's setup, not graphics quality. Rumble, audio and the Mods page have their own section restores.
bool restorable(const Setting &s) {
    return !strcmp(s.apply, "renderer") || !strcmp(s.apply, "live");
}

// Remaster run 15 (owner 9 Oct: "the graphics options list is so huge...a tab for audio, another one for display,
// another one for xbox 360 specific settings, another for PC"): every listed section, page by page and in order.
// A setting's manifest group picks its section by exact match; any other group of a page's family ("graphics.*",
// "display.*", "xbox360.*", "pc.*") lands in that page's Other section, and any other group at all under the Mods
// page's Other. `restore` gives the section its own "Restore this section's defaults" row.
struct Section {
    const char *id, *title;
    int tab;
    bool picture, restore;
};
const Section kSections[] = {
    {"graphics", "Picture", MENU_TAB_GRAPHICS, true, false},
    // The Graphics page's Advanced section, in this order (NFSMW run 30, owner D-174: sections by effect, each
    // strength slider beside its switch; review/run30/settings-ia-design.md).
    {"graphics.lighting", "Lighting and shadows", MENU_TAB_GRAPHICS, true, false},
    {"graphics.surfaces", "Textures, surfaces and reflections", MENU_TAB_GRAPHICS, true, false},
    {"graphics.camera", "Camera and colour effects", MENU_TAB_GRAPHICS, true, false},
    {"graphics.weather", "Sky, weather and rain", MENU_TAB_GRAPHICS, true, false},
    {"graphics.particles", "Smoke and sparks", MENU_TAB_GRAPHICS, true, false},
    {"graphics.mirror", "Rear-view mirror", MENU_TAB_GRAPHICS, true, false},
    {"graphics.engine", "Detail limits and engine", MENU_TAB_GRAPHICS, true, false},
    {"", "Other", MENU_TAB_GRAPHICS, true, false},
    // Display: the window, frame limit and overlay rows open "Screen"; World filtering opens "Sharpness".
    {"display", "Screen", MENU_TAB_DISPLAY, true, false},
    {"display.quality", "Sharpness and upscaling", MENU_TAB_DISPLAY, true, false},
    {"display.hdr", "HDR", MENU_TAB_DISPLAY, true, false},
    {"", "Other", MENU_TAB_DISPLAY, true, false},
    {"audio", "Audio", MENU_TAB_AUDIO, false, true},
    {"ui", "HUD and minimap", MENU_TAB_XBOX360, false, true},
    {"ui.menus", "Menus, movies and subtitles", MENU_TAB_XBOX360, false, true},
    {"", "Other", MENU_TAB_XBOX360, false, true},
    {"game", "Career and rewards", MENU_TAB_PC, false, true},
    {"game.police", "Police and heat", MENU_TAB_PC, false, true},
    {"game.unlocks", "Unlocks and shortcuts", MENU_TAB_PC, false, true},
    {"game.fixes", "Fixes (leave on)", MENU_TAB_PC, false, true},
    {"", "Other", MENU_TAB_PC, false, true},
    {"mods", "Your mods", MENU_TAB_MODS, false, true},
    {"", "Other", MENU_TAB_MODS, false, true},
    {"diagnostics", "Diagnostics", MENU_TAB_MODS, false, true},
    {"controls", "Controller", MENU_TAB_CONTROLS, false, false},
    {"feedback", "Controller rumble", MENU_TAB_CONTROLS, false, true}, // owner 9 Oct: rumble on Controls
    {"controls.keys", "Keyboard keys", MENU_TAB_CONTROLS, false, false},
};
constexpr size_t kSectionCount = sizeof kSections / sizeof kSections[0];
// The page a group family belongs to when no section names the group itself.
int family_tab(const char *g) {
    struct Family {
        const char *prefix;
        int tab;
    };
    static const Family kFamilies[] = {{"graphics", MENU_TAB_GRAPHICS},
                                       {"display", MENU_TAB_DISPLAY},
                                       {"audio", MENU_TAB_AUDIO},
                                       {"ui", MENU_TAB_XBOX360},
                                       {"game", MENU_TAB_PC},
                                       {"xbox360", MENU_TAB_XBOX360},   // run 30: older groups still find a page
                                       {"pc", MENU_TAB_PC},
                                       {"controls", MENU_TAB_CONTROLS}};
    for (const Family &f : kFamilies) {
        const size_t n = strlen(f.prefix);
        if (!strncmp(g, f.prefix, n) && (g[n] == 0 || g[n] == '.'))
            return f.tab;
    }
    return MENU_TAB_MODS;
}
// The section a setting is listed in (an index into kSections), or -1 for group "hidden".
int section_of(const Setting &s) {
    if (!strcmp(s.group, "hidden"))
        return -1;
    for (size_t i = 0; i < kSectionCount; ++i)
        if (*kSections[i].id && !strcmp(kSections[i].id, s.group))
            return int(i);
    const int tab = family_tab(s.group);
    for (size_t i = 0; i < kSectionCount; ++i)
        if (!*kSections[i].id && kSections[i].tab == tab)
            return int(i);
    return -1;
}
int tab_of(const Setting &s) {
    const int i = section_of(s);
    return i < 0 ? -1 : kSections[i].tab;
}

std::vector<Item> build(int tab) {
    std::vector<Item> v;
    auto header = [&](const char *t) { v.push_back({SRC_HEADER, 0, t}); };
    const uint32_t count = mods_settings_entry_count();
    auto settings_in = [&](auto pred) {
        for (uint32_t i = 0; i < count; ++i) {
            Setting s;
            if (setting(i, &s) && s.owner != MODS_OWNER_RUNTIME && pred(s))
                v.push_back({SRC_SETTING, i, {}});
        }
    };
    // A section: its header, the host's rows that open it, its settings, its restore row; nothing when empty.
    auto section = [&](size_t si) {
        const Section &sec = kSections[si];
        const size_t mark = v.size();
        header(sec.title);
        if (!strcmp(sec.id, "display"))
            for (int r : {DISPLAY_WINDOW, DISPLAY_FPS, DISPLAY_OVERLAY})
                if (mods_display_row_applies(DisplayRow(r)))
                    v.push_back({SRC_DISPLAY, uint32_t(r), {}});
        if (!strcmp(sec.id, "display.quality") && mods_display_row_applies(DISPLAY_FILTERING))
            v.push_back({SRC_DISPLAY, uint32_t(DISPLAY_FILTERING), {}});
        settings_in([&](const Setting &s) { return section_of(s) == int(si); });
        if (v.size() == mark + 1)
            v.pop_back(); // an empty section shows no header
        else if (sec.restore)
            v.push_back({SRC_RESTORE_GROUP, uint32_t(si), {}});
    };
    if (tab == MENU_TAB_GRAPHICS) {
        size_t main = 0;
        while (strcmp(kSections[main].id, "graphics"))
            ++main;
        section(main);
        uint32_t advanced = 0;
        for (uint32_t i = 0; i < count; ++i) {
            Setting s;
            advanced += setting(i, &s) && s.owner != MODS_OWNER_RUNTIME && tab_of(s) == MENU_TAB_GRAPHICS &&
                        !main_graphics_setting(s);
        }
        if (advanced) {
            v.push_back({SRC_ADVANCED, advanced, {}});
            for (size_t si = 0; g_advanced && si < kSectionCount; ++si)
                if (si != main && kSections[si].tab == MENU_TAB_GRAPHICS)
                    section(si);
        }
        header("Defaults");
        v.push_back({SRC_RESTORE_GFX, 0, {}});
    } else if (tab == MENU_TAB_CONTROLS) {
        if (mods_settings_row_listed(DISPLAY_CONTROLS_BIT)) {
            header("On-screen controls");
            for (int r = 0; r < CONTROLS_ROW_COUNT; ++r)
                v.push_back({SRC_CONTROLS, uint32_t(r), {}});
        }
        for (size_t si = 0; si < kSectionCount; ++si)
            if (kSections[si].tab == MENU_TAB_CONTROLS)
                section(si);
        header("Changes");
    } else {
        if (tab == MENU_TAB_MODS && mods_menu_entry_count()) {
            header("Mod actions");
            for (uint32_t i = 0; i < mods_menu_entry_count(); ++i)
                v.push_back({SRC_MENU, i, {}});
        }
        for (size_t si = 0; si < kSectionCount; ++si)
            if (kSections[si].tab == tab)
                section(si);
        if (tab == MENU_TAB_MODS && (host_bug_report_available() || host_update_available())) {
            header("Help");
            if (host_bug_report_available())
                v.push_back({SRC_BUG_REPORT, 0, {}});
            if (host_update_available())
                v.push_back({SRC_UPDATES, 0, {}});
        }
        header("Changes");
    }
    // Every page that can hold a waiting change ends with Apply and Discard (also buttons beside the status line
    // while changes wait), so a change made on any page can be committed there.
    v.push_back({SRC_APPLY, 0, {}});
    v.push_back({SRC_DISCARD, 0, {}});
    return v;
}
bool selectable(const Item &it) {
    return it.src != SRC_HEADER;
}

const char *const kFiltering[] = {"Game's own", "Trilinear", "4x anisotropic", "8x anisotropic",
                                  "16x anisotropic"};
int display_draft_or_value(int row) {
    auto it = g_draft.find(draft_key(row));
    return it != g_draft.end() ? int(it->second) : mods_display_value(DisplayRow(row));
}
int64_t setting_draft_or_value(const Setting &s) {
    auto it = g_draft.find(draft_key(s));
    return it != g_draft.end() ? it->second : s.value;
}
std::string choice(const char *choices, int64_t index) {
    const char *p = choices;
    for (int64_t i = 0; *p && i < index; ++i) {
        p = strchr(p, '|');
        if (!p)
            return "";
        ++p;
    }
    const char *end = strchr(p, '|');
    return end ? std::string(p, end) : std::string(p);
}
std::string amount(int64_t value, const char *unit) {
    char digits[32];
    const uint64_t m = value < 0 ? uint64_t(0) - uint64_t(value) : uint64_t(value);
    snprintf(digits, sizeof digits, "%llu", (unsigned long long)m);
    std::string grouped;
    const size_t n = strlen(digits);
    for (size_t i = 0; i < n; ++i) {
        if (i && (n - i) % 3 == 0)
            grouped += ',';
        grouped += digits[i];
    }
    return std::string(value < 0 ? "-" : "") + unit + grouped;
}
std::string setting_value_text(const Setting &s, int64_t v) {
    if (s.kind == POP_SETTING_BOOL)
        return v ? "On" : "Off";
    if (*s.choices)
        return choice(s.choices, v - s.mn);
    if (*s.unit)
        return amount(v, s.unit);
    return std::to_string(v);
}
const char *timing_text(const char *apply) {
    if (!strcmp(apply, "live"))
        return "Applies now";
    if (!strcmp(apply, "renderer"))
        return "Applies when you choose Apply";
    if (!strcmp(apply, "next_screen"))
        return "Applies at the next screen or load";
    if (!strcmp(apply, "next_event"))
        return "Applies to the next eligible event";
    if (!strcmp(apply, "new_career"))
        return "New careers only";
    if (!strcmp(apply, "restart"))
        return "Applies after restarting the game";
    if (!strcmp(apply, "game_video"))
        return "Applies when a detail level is chosen in the game's Video menu";
    return "";
}
// Display rows read "Label: value"; controls rows pad the label to a column ("%-22s %s").
void split_line(const std::string &line, std::string *label, std::string *value) {
    size_t c = line.find(": "), skip = 2;
    if (c == std::string::npos) {
        c = line.find("  ");
        skip = 2;
    }
    if (c == std::string::npos) {
        *label = line;
        value->clear();
        return;
    }
    *label = line.substr(0, c);
    size_t v = c + skip;
    while (v < line.size() && line[v] == ' ')
        ++v;
    *value = line.substr(v);
}

MenuLine line_for(const Item &it) {
    MenuLine l;
    switch (it.src) {
    case SRC_HEADER:
        l.kind = MenuLine::HEADER;
        l.label = it.title;
        l.enabled = false;
        break;
    case SRC_DISPLAY: {
        const int row = int(it.index);
        l.adjustable = true;
        if (row == DISPLAY_FILTERING) {
            const int v = std::clamp(display_draft_or_value(row), 0, 4);
            l.label = "World filtering";
            l.value = kFiltering[v];
            l.pending = v != mods_display_value(DISPLAY_FILTERING);
            l.help = "Sharper road, wall and terrain textures at an angle. \"Game's own\" keeps the filtering "
                     "the game asks for; 4x-16x apply that anisotropy to every world texture, Trilinear "
                     "turns it off. Text, the HUD, movies, shadows and lighting are not affected.";
            l.timing = timing_text("renderer");
        } else {
            split_line(mods_display_line(DisplayRow(row)), &l.label, &l.value);
            l.timing = timing_text("live");
            if (row == DISPLAY_WINDOW)
                l.help = "Window, borderless full screen or full screen.";
            else if (row == DISPLAY_FPS)
                l.help = "The presented frame limit (60 is the default). The game itself keeps running at 60 Hz. "
                         "120 FPS is experimental: the original game was authored around 60 Hz, and above 60 FPS "
                         "some cinematics, vehicle poses or animations may render incorrectly.";
            else if (row == DISPLAY_OVERLAY)
                l.help = "Frame counters or a frame-time graph in a corner (F11 cycles it too).";
        }
        break;
    }
    case SRC_CONTROLS:
        split_line(mods_controls_line(ControlsRow(it.index)), &l.label, &l.value);
        l.adjustable = it.index != CONTROLS_EDIT_ROW;
        l.timing = timing_text("live");
        break;
    case SRC_SETTING: {
        Setting s;
        if (!setting(it.index, &s))
            break;
        const int64_t v = setting_draft_or_value(s);
        l.label = s.label;
        l.value = setting_value_text(s, v);
        l.pending = v != s.value;
        l.help = s.help;
        l.timing = timing_text(s.apply);
        l.adjustable = true;
        break;
    }
    case SRC_MENU: {
        uint32_t owner = 0;
        const char *path = nullptr, *label = nullptr;
        mods_menu_entry(it.index, &owner, &path, &label);
        l.label = label ? label : "";
        l.value = ">";
        break;
    }
    case SRC_APPLY:
        l.kind = MenuLine::ACTION;
        l.label = "Apply";
        l.enabled = !g_draft.empty();
        l.help = "Commit the changes marked with a dot.";
        break;
    case SRC_DISCARD:
        l.kind = MenuLine::ACTION;
        l.label = "Discard";
        l.enabled = !g_draft.empty();
        l.help = "Forget the changes not yet applied.";
        break;
    case SRC_ADVANCED:
        l.label = "Advanced settings";
        l.value = g_advanced ? "Hide" : "Show (" + std::to_string(it.index) + ")";
        l.adjustable = true;
        l.help = "Every single setting behind the Picture list: each Remaster feature, effect strengths, the day "
                 "and weather lengths, the mirror, rain and engine options. Look decides the Remaster features; the "
                 "rest you rarely need. Screen, HDR and sharpness are on the Display page. Closed again each time "
                 "this menu opens.";
        break;
    case SRC_RESTORE_GROUP:
        l.kind = MenuLine::ACTION;
        l.label = "Restore this section's defaults";
        l.list_only = true;
        l.help = std::string("Marks the default values of \"") + kSections[it.index].title +
                 "\" as changes; Apply commits them, Discard forgets them. Other sections stay as they are.";
        break;
    case SRC_BUG_REPORT:
        l.kind = MenuLine::ACTION;
        l.label = "Save bug report";
        l.help = "Saves one zip file to your Desktop (if macOS says no, to the game's profile folder) with the newest "
                 "logs, your settings, the mod reports and the Setup log, to attach to a bug report. Nothing is uploaded: you decide what to send. No saves or game files.";
        break;
    case SRC_UPDATES: {
        char status[96];
        host_update_status(status, sizeof status);
        l.label = "Check for updates";
        l.value = status;
        l.help = "Asks GitHub once whether a newer version is out (the only thing this game sends online, and only "
                 "when you press it). If one is, press again to open its page and download the new Setup.";
        break;
    }
    case SRC_RESTORE_GFX:
        l.kind = MenuLine::ACTION;
        l.label = "Restore graphics defaults";
        l.help = "Marks the default values of the picture settings that apply now or at Apply as changes (the "
                 "Definitive look included), here and on the Display, Xbox 360 and PC pages; Apply commits them. The "
                 "window mode, resolution, frame limit, rumble and settings that need a restart stay as they are.";
        break;
    }
    return l;
}

void fix_cursor(const std::vector<Item> &v, int dir) {
    if (v.empty()) {
        g_cursor = 0;
        return;
    }
    if (g_cursor >= v.size())
        g_cursor = v.size() - 1;
    for (size_t n = 0; n < v.size() && !selectable(v[g_cursor]); ++n)
        g_cursor = dir >= 0 ? (g_cursor + 1) % v.size() : (g_cursor + v.size() - 1) % v.size();
}

int64_t stepped(int64_t value, int64_t mn, int64_t mx, int64_t step, int delta, bool wrap) {
    if (delta > 0)
        return value >= mx ? (wrap ? mn : mx) : (uint64_t(mx) - uint64_t(value) <= uint64_t(step) ? mx : value + step);
    return value <= mn ? (wrap ? mx : mn) : (uint64_t(value) - uint64_t(mn) <= uint64_t(step) ? mn : value - step);
}

void set_draft(const std::string &key, int64_t value, int64_t committed) {
    g_restore.erase(key); // a Restore marks its drafts after this call
    if (value == committed)
        g_draft.erase(key);
    else
        g_draft[key] = value;
}

void adjust(const Item &it, int delta, bool from_enter) {
    switch (it.src) {
    case SRC_DISPLAY: {
        const int row = int(it.index);
        if (drafted(row)) {
            const int v = int(stepped(display_draft_or_value(row), 0, 4, 1, delta, from_enter));
            set_draft(draft_key(row), v, mods_display_value(DisplayRow(row)));
        } else {
            mods_menu_main_thread_call([row, delta] { mods_display_nudge(DisplayRow(row), delta); });
        }
        break;
    }
    case SRC_CONTROLS:
        if (it.index == CONTROLS_EDIT_ROW) {
            if (from_enter)
                mods_controls_nudge(ControlsRow(it.index), +1);
        } else {
            mods_controls_nudge(ControlsRow(it.index), delta);
        }
        break;
    case SRC_ADVANCED:
        g_advanced = from_enter ? !g_advanced : delta > 0;
        break;
    case SRC_SETTING: {
        Setting s;
        if (!setting(it.index, &s))
            break;
        const int64_t cur = drafted(s) ? setting_draft_or_value(s) : s.value;
        const bool wrap = from_enter && *s.choices;
        int64_t want = s.kind == POP_SETTING_BOOL ? !cur : stepped(cur, s.mn, s.mx, s.step, delta, wrap);
        // A retired choice keeps its value slot (saved profiles still hold it) with an empty label; stepping passes
        // over it (NFSMW: the removed Xbox 360 looks, 2 Oct).
        for (int64_t guard = s.mx - s.mn; s.kind != POP_SETTING_BOOL && *s.choices && guard > 0 && want != cur &&
                                          choice(s.choices, std::clamp(want, s.mn, s.mx) - s.mn).empty();
             --guard)
            want = stepped(want, s.mn, s.mx, s.step, delta, wrap);
        want = std::clamp(want, s.mn, s.mx);
        if (drafted(s))
            set_draft(draft_key(s), want, s.value);
        else {
            g_draft.erase(draft_key(s)); // a Restore's draft of this live setting gives way to the edit
            g_restore.erase(draft_key(s));
            mods_settings_set(s.owner, s.key, want);
        }
        break;
    }
    default:
        break;
    }
}

#ifdef POPM_TESTING
double g_test_seconds = -1;   // mods_menu_clock_for_test: a set clock for the watchdog tests
int g_test_pause_setting = -1;
#endif
double menu_seconds() {
#ifdef POPM_TESTING
    if (g_test_seconds >= 0)
        return g_test_seconds;
#endif
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
// NFSMW 3.0.4 (D-202 item 4, runtime/guest_pause.h): when the current pause began (0: not paused). The Apply
// watchdog below counts only time the game ran: it does not fire during a pause, and a pause moves the Apply's
// time forward by the paused part, so 20 s of running still prove it.
double g_paused_since = 0;
// Test85: last-known-good. When the Apply that recorded them happened (0: none pending).
double g_pending_since = 0;
void record_previous() {
    std::vector<std::pair<std::string, int64_t>> previous;
    const uint32_t count = mods_settings_entry_count();
    for (const auto &kv : g_draft) {
        const std::string &key = kv.first;
        if (key.rfind("d:", 0) == 0) {
            if (std::stoi(key.substr(2)) == DISPLAY_FILTERING)
                previous.push_back({std::string("host.display/") + RECOMP_SETTINGS_FILTERING_KEY,
                                    mods_display_value(DISPLAY_FILTERING)});
            continue;
        }
        for (uint32_t i = 0; i < count; ++i) {
            Setting s;
            if (setting(i, &s) && draft_key(s) == key) {
                previous.push_back({std::string(s.mod_id) + "/" + s.key, s.value});
                break;
            }
        }
    }
    mods_cpp_settings_mark_pending(previous);
    g_pending_since = previous.empty() ? 0 : menu_seconds();
}

void apply_draft() {
    if (g_draft.empty())
        return;
    record_previous();
    int applied = 0;
    const uint32_t count = mods_settings_entry_count();
    for (const auto &[key, value] : g_draft) {
        if (key.rfind("d:", 0) == 0) {
            const PopModStatus st = mods_display_set(DisplayRow(std::stoi(key.substr(2))), int(value));
            if (st == POP_OK)
                ++applied;
            else
                printf("mods: settings menu could not apply %s=%lld (status %d)\n", key.c_str(), (long long)value,
                       int(st));
            continue;
        }
        for (uint32_t i = 0; i < count; ++i) {
            Setting s;
            if (setting(i, &s) && draft_key(s) == key &&
                (g_restore.count(key) ? mods_settings_inherit(s.owner, s.key)
                                      : mods_settings_set(s.owner, s.key, value)) == POP_OK) {
                ++applied;
                break;
            }
        }
    }
    const size_t total = g_draft.size();
    clear_draft();
    char line[96];
    if (applied == int(total))
        snprintf(line, sizeof line, "Applied %d change%s.", applied, applied == 1 ? "" : "s");
    else
        snprintf(line, sizeof line, "Applied %d of %zu changes.", applied, total);
    g_status = line;
    printf("mods: settings menu applied %d of %zu graphics changes\n", applied, total);
    fflush(stdout);
}

void request_apply() {
    if (g_draft.empty())
        return;
    if (mods_host_on_main_thread()) {
        apply_draft();
        return;
    }
    g_applying = true;
    g_status = "Applying...";
    mods_menu_main_thread_call([] {
        std::lock_guard lock(g_mu);
        g_applying = false;
        apply_draft();
    });
}

// Remaster run 16: one setting back to its default. A changed value is drafted (Apply removes the override, Discard
// forgets it); a value already at the default only loses its override now (nothing to see or apply), so it follows
// later default changes again. The menu shows neither: the player sees the default value either way.
void restore_one(const Setting &s, int64_t def) {
    const std::string key = draft_key(s);
    set_draft(key, std::clamp(def, s.mn, s.mx), s.value);
    if (g_draft.count(key))
        g_restore.insert(key);
    else if (mods_settings_is_override((std::string(s.mod_id) + "/" + s.key).c_str()))
        mods_settings_inherit(s.owner, s.key);
}

// Test85: draft the defaults (Apply commits them, Discard forgets them).
void restore_defaults() {
    int n = 0;
    if (mods_display_row_applies(DISPLAY_FILTERING)) {
        set_draft(draft_key(DISPLAY_FILTERING), 0, mods_display_value(DISPLAY_FILTERING));
        ++n;
    }
    const uint32_t count = mods_settings_entry_count();
    for (uint32_t i = 0; i < count; ++i) {
        Setting s;
        int64_t def = 0;
        if (!setting(i, &s) || s.owner == MODS_OWNER_RUNTIME || !mods_settings_default(i, &def))
            continue;
        const int si = section_of(s);
        if (si < 0 || !kSections[si].picture || !restorable(s))
            continue;
        restore_one(s, def);
        ++n;
    }
    g_status = g_draft.empty() ? "Already at the defaults."
                               : "Graphics defaults marked; choose Apply to use them.";
    printf("mods: settings menu restore %s defaults: %d settings, %zu changes drafted\n",
           "graphics", n, g_draft.size());
    fflush(stdout);
}

// Test85: a section's defaults (the Mods page's, Audio, rumble), drafted the same way.
void restore_group(size_t gi) {
    const Section &g = kSections[gi];
    const uint32_t count = mods_settings_entry_count();
    int n = 0;
    for (uint32_t i = 0; i < count; ++i) {
        Setting s;
        int64_t def = 0;
        if (!setting(i, &s) || s.owner == MODS_OWNER_RUNTIME || section_of(s) != int(gi) ||
            !mods_settings_default(i, &def))
            continue;
        restore_one(s, def);
        ++n;
    }
    g_status = g_draft.empty() ? "Already at the defaults." : std::string(g.title) + " defaults marked; choose Apply to use them.";
    printf("mods: settings menu restore \"%s\" defaults: %d settings, %zu changes drafted\n", g.title, n, g_draft.size());
    fflush(stdout);
}

// Enter, or a click on the line.
void activate(const Item &it) {
    if (it.src == SRC_RESTORE_GFX) {
        restore_defaults();
        return;
    }
    if (it.src == SRC_ADVANCED) {
        g_advanced = !g_advanced;
        return;
    }
    if (it.src == SRC_RESTORE_GROUP) {
        restore_group(it.index);
        return;
    }
    if (it.src == SRC_BUG_REPORT) {
        char msg[256];
        host_save_bug_report(msg, sizeof msg);
        g_status = msg;
        return;
    }
    if (it.src == SRC_UPDATES) {
        host_update_activate();
        return;
    }
    if (it.src == SRC_APPLY)
        request_apply();
    else if (it.src == SRC_DISCARD) {
        g_status = g_draft.empty() ? "" : "Discarded the changes.";
        clear_draft();
    } else if (it.src == SRC_MENU)
        mods_menu_activate(it.index);
    else
        adjust(it, +1, true);
}

void show_tab(int tab) {
    g_tab = std::clamp(tab, 0, kTabCount - 1);
    g_cursor = 0;
    fix_cursor(build(g_tab), +1);
    g_status.clear();
}

} // namespace

bool mods_menu_enabled() {
#ifdef POPM_TESTING
    return g_force_enabled;
#else
    return RECOMP_SETTINGS_PAGE_TABS != 0;
#endif
}

void mods_menu_open(int tab) {
    std::lock_guard lock(g_mu);
    g_open = true;
    g_advanced = false;
    g_tab = std::clamp(tab, 0, kTabCount - 1);
    g_cursor = 0;
    g_status.clear();
    fix_cursor(build(g_tab), +1);
}

void mods_menu_close() {
    std::lock_guard lock(g_mu);
    if (g_applying) {
        g_open = false; // the chosen Apply still runs at the next frame
        return;
    }
    if (!g_draft.empty()) {
        printf("mods: settings menu closed; %zu unapplied graphics change%s discarded\n", g_draft.size(),
               g_draft.size() == 1 ? "" : "s");
        fflush(stdout);
    } else if (g_open) {
        printf("mods: settings menu closed\n");   // Test93: the record that it closed, and when
        fflush(stdout);
    }
    clear_draft();
    g_open = false;
}

bool mods_menu_is_open() {
    std::lock_guard lock(g_mu);
    return g_open;
}

extern "C" bool mods_menu_pause_wanted(void) {
    if (!mods_host_on_main_thread() || !mods_menu_is_open())
        return false;
#ifdef POPM_TESTING
    if (g_test_pause_setting >= 0)
        return g_test_pause_setting != 0;
#endif
    return mods_core_setting("pause_in_menu", 0) != 0;
}

extern "C" void mods_menu_note_pause(bool paused) {
    const double now = menu_seconds();
    if (paused) {
        g_paused_since = now > 0 ? now : 1e-9;
        return;
    }
    if (g_pending_since > 0 && g_paused_since > 0)
        g_pending_since += now - std::max(g_pending_since, g_paused_since);
    g_paused_since = 0;
}

int mods_menu_pending_count() {
    std::lock_guard lock(g_mu);
    return int(g_draft.size());
}

bool mods_menu_key(int dik) {
    std::lock_guard lock(g_mu);
    if (!g_open)
        return false;
    std::vector<Item> v = build(g_tab);
    fix_cursor(v, +1);
    const Item *cur = g_cursor < v.size() ? &v[g_cursor] : nullptr;
    switch (dik) {
    case 0xc8: // UP
        if (!v.empty()) {
            size_t c = g_cursor;
            do
                c = (c + v.size() - 1) % v.size();
            while (c != g_cursor && !selectable(v[c]));
            g_cursor = c;
        }
        break;
    case 0xd0: // DOWN
        if (!v.empty()) {
            size_t c = g_cursor;
            do
                c = (c + 1) % v.size();
            while (c != g_cursor && !selectable(v[c]));
            g_cursor = c;
        }
        break;
    case 0xcb: // LEFT
    case 0xcd: // RIGHT
        if (cur)
            adjust(*cur, dik == 0xcd ? +1 : -1, false);
        g_status.clear();
        break;
    case 0x1c: // RETURN
        if (cur)
            activate(*cur);
        break;
    case MENU_KEY_TAB_PREV:
    case MENU_KEY_TAB_NEXT:
    case MENU_KEY_TAB:
        show_tab((g_tab + (dik == MENU_KEY_TAB_PREV ? kTabCount - 1 : 1)) % kTabCount);
        break;
    case 0x01: // ESCAPE
        mods_menu_close();
        break;
    default:
        return true; // the menu owns the keyboard while it is open
    }
    return true;
}

void mods_menu_set_tab(int tab) {
    std::lock_guard lock(g_mu);
    if (g_open && tab != g_tab)
        show_tab(tab);
}

bool mods_menu_pointer_line(int tab, size_t line, int action) {
    std::lock_guard lock(g_mu);
    if (!g_open || tab != g_tab)
        return false;
    std::vector<Item> v = build(g_tab);
    if (line >= v.size() || !selectable(v[line]))
        return false;
    if (action == MENU_POINT_PRESS) {
        activate(v[line]);
        return true;
    }
    g_cursor = line;
    switch (action) {
    case MENU_POINT_ACTIVATE:
        activate(v[line]);
        break;
    case MENU_POINT_DECREASE:
    case MENU_POINT_INCREASE:
        if (v[line].src == SRC_APPLY || v[line].src == SRC_DISCARD || v[line].src == SRC_MENU ||
            v[line].src == SRC_RESTORE_GFX || v[line].src == SRC_RESTORE_GROUP)
            activate(v[line]);
        else
            adjust(v[line], action == MENU_POINT_INCREASE ? +1 : -1, false);
        g_status.clear();
        break;
    default:
        break;
    }
    return true;
}

bool mods_menu_snapshot(MenuSnapshot *out) {
    std::lock_guard lock(g_mu);
    if (!out || !g_open)
        return false;
    *out = MenuSnapshot{};
    out->tab = g_tab;
    out->tabs.assign(std::begin(kTabs), std::end(kTabs));
    std::vector<Item> v = build(g_tab);
    fix_cursor(v, +1);
    for (size_t i = 0; i < v.size(); ++i) {
        MenuLine l = line_for(v[i]);
        l.selected = i == g_cursor;
        if (l.selected)
            out->help = l.help;
        out->lines.push_back(std::move(l));
    }
    if (g_applying) {
        out->status = g_status;
    } else if (!g_draft.empty()) {
        char line[96];
        snprintf(line, sizeof line, "%zu change%s not applied yet: choose Apply or Discard (Esc discards).",
                 g_draft.size(), g_draft.size() == 1 ? "" : "s");
        out->status = line;
    } else {
        out->status = g_status;
    }
    out->prompts = "Keys: Up/Down choose    Left/Right change    Enter select    Tab next page    Esc or F10 close\n"
                   "Mouse: point at a row, click it or its < > arrows, click a page name, wheel scrolls    "
                   "Pad: L1/R1 pages";
    // A content hash: the renderer redraws only when something it would draw changed.
    uint64_t h = 1469598103934665603ull;
    auto mix = [&](const std::string &s) {
        for (unsigned char c : s)
            h = (h ^ c) * 1099511628211ull;
        h = (h ^ 0xff) * 1099511628211ull;
    };
    mix(std::to_string(out->tab));
    for (const MenuLine &l : out->lines) {
        mix(l.label);
        mix(l.value);
        mix(l.timing);
        mix(std::string(1, char('0' + l.selected + 2 * l.pending + 4 * l.enabled)));
    }
    mix(out->help);
    mix(out->status);
    out->generation = h;
    return true;
}

void mods_menu_main_thread_call(std::function<void()> fn) {
    if (mods_host_on_main_thread()) {
        fn();
        return;
    }
    std::lock_guard lock(g_main_mu);
    g_main_calls.push_back(std::move(fn));
    g_main_waiting.store(true, std::memory_order_release);
}

void mods_menu_main_thread_drain() {
    // Test85: 20 s of game frames after an Apply prove it; the recorded previous values are dropped.
    if (g_pending_since > 0 && g_paused_since == 0 && menu_seconds() - g_pending_since > 20.0) {
        g_pending_since = 0;
        mods_settings_clear_pending();
        fflush(stdout);
    }
    if (!g_main_waiting.load(std::memory_order_acquire) || !mods_host_on_main_thread())
        return;
    std::vector<std::function<void()>> calls;
    {
        std::lock_guard lock(g_main_mu);
        calls.swap(g_main_calls);
        g_main_waiting.store(false, std::memory_order_relaxed);
    }
    for (auto &fn : calls)
        fn();
    printf("mods: settings menu ran %zu deferred change%s on the main thread\n", calls.size(),
           calls.size() == 1 ? "" : "s");
    fflush(stdout);
}

#ifdef POPM_TESTING
void mods_menu_enable_for_test(bool on) {
    g_force_enabled = on;
}
void mods_menu_clock_for_test(double seconds) {
    g_test_seconds = seconds;
}
void mods_menu_pause_setting_for_test(int on) {
    g_test_pause_setting = on;
}
double mods_menu_pending_since_for_test() {
    return g_pending_since;
}
void mods_menu_reset_for_test() {
    g_paused_since = 0;
    g_test_seconds = -1;
    g_test_pause_setting = -1;
    std::lock_guard lock(g_mu);
    g_open = false;
    g_advanced = false;
    g_tab = 0;
    g_cursor = 0;
    clear_draft();
    g_status.clear();
    g_applying = false;
}
#endif
