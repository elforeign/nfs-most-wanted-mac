// settings_menu.h - the tabbed settings menu (Test72; NFSMW graphics/mods plan, Revision 3):
// Graphics, Display, Audio, Xbox 360, PC, Mods and Controls pages (Remaster run 15) over the EXISTING
// settings owners (display rows, controls rows, mod manifest settings). It keeps no state of its own besides the open page, the cursor
// and the Graphics draft: a value lives where it always lived and persists the way it always did.
// Graphics changes whose manifest says `apply = "renderer"` (and the World filtering row) are
// collected in a draft and committed together by Apply; everything else applies the way its owner
// already applies it, with that timing shown. A game opts in with game.toml [settings] page = "tabs".
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

struct MenuLine {
    enum Kind : uint8_t { HEADER, ROW, ACTION } kind = ROW;
    std::string label, value, help, timing;
    bool selected = false, pending = false, enabled = true, adjustable = false;
    // Test128: an action that stays in the list only (a Mods section's restore): the footer's buttons are the
    // page-wide actions, so a page with many sections does not run its buttons off the panel.
    bool list_only = false;
};
struct MenuSnapshot {
    uint64_t generation = 0; // changes whenever anything drawn changes
    int tab = 0;
    std::vector<std::string> tabs;
    std::vector<MenuLine> lines;
    std::string help, status, prompts;
};

// The pages, in tab order (Remaster run 15, owner 9 Oct: "a tab for audio, another one for display, another one
// for xbox 360 specific settings, another for PC"). A setting's manifest group decides its page (settings_menu.cpp).
enum MenuTab {
    MENU_TAB_GRAPHICS,
    MENU_TAB_DISPLAY,
    MENU_TAB_AUDIO,
    MENU_TAB_XBOX360,
    MENU_TAB_PC,
    MENU_TAB_MODS,
    MENU_TAB_CONTROLS,
    MENU_TAB_COUNT
};
bool mods_menu_enabled(void);
void mods_menu_open(int tab);
// Closing discards an unapplied Graphics draft (the status said so while it was pending).
void mods_menu_close(void);
bool mods_menu_is_open(void);
// A DirectInput key code, or the menu's own codes below. True when consumed.
bool mods_menu_key(int dik);
constexpr int MENU_KEY_TAB_PREV = 0xc9; // Page Up / left shoulder
constexpr int MENU_KEY_TAB_NEXT = 0xd1; // Page Down / right shoulder
constexpr int MENU_KEY_TAB = 0x0f;      // Tab: the next page, wrapping
void mods_menu_set_tab(int tab);
// The pointer, on line `line` of page `tab` as the last snapshot listed it (settings_menu_draw.cpp
// keeps where each line was drawn). False when that page is no longer showing or the line cannot
// be chosen.
// PRESS acts like ACTIVATE but leaves the cursor where it is (the Apply/Discard buttons beside the
// status line, which must not scroll the list away from the row being changed).
enum MenuPointerAction {
    MENU_POINT_HOVER,
    MENU_POINT_ACTIVATE,
    MENU_POINT_DECREASE,
    MENU_POINT_INCREASE,
    MENU_POINT_PRESS
};
bool mods_menu_pointer_line(int tab, size_t line, int action);
bool mods_menu_snapshot(MenuSnapshot *out);
// The draft as "key=value" lines and the number of pending changes (tests, logs).
int mods_menu_pending_count(void);
// Display changes belong to the thread that booted (mods_display_set refuses any other), but input
// reaches the menu on whichever thread holds the guest baton when it is delivered, sometimes a guest
// worker. Such a change runs now on the main thread, else at the next game frame there (the game's
// frame hook drains it). Test74.
void mods_menu_main_thread_call(std::function<void()> fn);
void mods_menu_main_thread_drain(void);
#ifdef POPM_TESTING
void mods_menu_reset_for_test(void);
void mods_menu_clock_for_test(double seconds);   // the Apply watchdog's clock; -1 = the real one
void mods_menu_pause_setting_for_test(int on);    // pause_in_menu without a core mod; -1 = the real setting
double mods_menu_pending_since_for_test(void);
void mods_menu_enable_for_test(bool on); // off by default, so the older page's tests keep it
#endif
