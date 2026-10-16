// settings_page_tests.cpp - navigation, editing and the drawing itself.
#include "mods_tests.h"
#include "../display_settings.h"
#include "../mods_internal.h"
#include "../settings_menu.h"
#include <algorithm>
#include <cstdio>
#include <thread>
#include "../../runtime/memory.h"
#include <limits>
#include <string>
#include <vector>

static const PopModApi *page_test_provider(uint32_t owner);

namespace {
PopModApi g_a, g_b;

// The page filters its rows by the mod id it gets from the context provider,
// so a suite that opens the page for "a.mod" has to be a run in which owner
// FIRST_MOD really is a.mod. Without this the filter matches whatever provider
// the previous suite installed and the page comes back empty.
void install_context() {
    memset(&g_a, 0, sizeof g_a);
    g_a.version = POP_MOD_API_VERSION;
    g_a.size = (uint32_t)sizeof(PopModApi);
    g_a.mod_index = MODS_OWNER_FIRST_MOD;
    g_a.mod_id = "a.mod";
    g_b = g_a;
    g_b.mod_index = MODS_OWNER_FIRST_MOD + 1;
    g_b.mod_id = "b.mod";
    mods_set_context_provider(page_test_provider);
}

// The page's keyboard is the runtime's and outlives a suite, so every suite
// here hands it back. F10 is reserved by the page, and a later suite pressing
// it would otherwise open a page nobody in that suite knows about.
void teardown() {
    mods_page_close();
    mods_input_remove_all(MODS_OWNER_RUNTIME);
}

void setup() {
    // The registries this suite counts rows from, emptied first. Another
    // suite's leftover menu entry or key handler would be an extra row and an
    // extra consumer, and every count below would be one out.
    mods_host_services_remove_all(MODS_OWNER_FIRST_MOD);
    mods_host_services_remove_all(MODS_OWNER_FIRST_MOD + 1);
    mods_input_remove_all(MODS_OWNER_FIRST_MOD);
    mods_settings_reset();
    mods_host_set_main_thread();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "ui_scale", "UI scale", POP_SETTING_INT, 2,
                          1, 4);
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "shadows", "Shadows", POP_SETTING_BOOL, 1,
                          0, 1);
    mods_settings_declare(MODS_OWNER_FIRST_MOD + 1, "b.mod", "volume", "Volume", POP_SETTING_INT, 5,
                          0, 10);
    install_context();
    mods_page_init();
    mods_page_close();
}
} // namespace

static const PopModApi *page_test_provider(uint32_t owner) {
    if (owner == MODS_OWNER_FIRST_MOD)
        return &g_a;
    if (owner == MODS_OWNER_FIRST_MOD + 1)
        return &g_b;
    return nullptr;
}

MOD_TEST_SUITE(page_navigates_and_edits) {
    setup();
    MOD_CHECK_EQ(mods_page_open("a.mod"), POP_OK);
    MOD_CHECK(mods_page_visible());
    MOD_CHECK_EQ(mods_page_line_count(), 2u); // only that mod's
    MOD_CHECK_EQ(mods_page_cursor(), 0u);

    int64_t v = 0;
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false); // RIGHT
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "ui_scale", &v), POP_OK);
    MOD_CHECK_EQ(v, 4);
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false); // clamps
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "ui_scale", &v), POP_OK);
    MOD_CHECK_EQ(v, 4);
    mods_input_key(0xcb, 0, true);
    mods_input_key(0xcb, 0, false); // LEFT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "ui_scale", &v), POP_OK);
    MOD_CHECK_EQ(v, 3);

    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false); // DOWN
    MOD_CHECK_EQ(mods_page_cursor(), 1u);
    mods_input_key(0x1c, 0, true);
    mods_input_key(0x1c, 0, false); // RETURN
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "shadows", &v), POP_OK);
    MOD_CHECK_EQ(v, 0);

    // Every key the page uses is consumed while it is open, so the game does
    // not act on the same press.
    MOD_CHECK(mods_input_key(0xd0, 0, true));
    mods_input_key(0xd0, 0, false);
    mods_input_key(0x01, 0, true);
    mods_input_key(0x01, 0, false); // ESCAPE
    MOD_CHECK(!mods_page_visible());
    // Closed, the same key is the game's again.
    MOD_CHECK(!mods_input_key(0xd0, 0, true));
    mods_input_key(0xd0, 0, false);
    teardown();
}

MOD_TEST_SUITE(page_activates_a_menu_entry) {
    setup();
    static int g_activated = 0;
    g_activated = 0;
    PopModApi &api = g_a; // the same context the page filters by
    mods_fill_host_api(&api);
    MOD_CHECK_EQ(api.register_menu_item(
                     &api, "options", "Do the thing",
                     [](const PopModApi *, void *) { ++g_activated; }, nullptr),
                 POP_OK);

    MOD_CHECK_EQ(mods_page_open("a.mod"), POP_OK);
    // The menu entry is a row on the page, listed before the settings.
    MOD_CHECK_EQ(mods_page_line_count(), 3u);
    MOD_CHECK(std::string(mods_page_line(0)).find("Do the thing") != std::string::npos);
    // RETURN on it runs the mod's callback - the route from a host keypress to
    // a registered menu item, which is what makes register_menu_item useful.
    MOD_CHECK_EQ(mods_page_cursor(), 0u);
    mods_input_key(0x1c, 0, true);
    mods_input_key(0x1c, 0, false);
    MOD_CHECK_EQ(g_activated, 1);
    // And RETURN on a setting row still edits rather than activating anything.
    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false);
    mods_input_key(0x1c, 0, true);
    mods_input_key(0x1c, 0, false);
    MOD_CHECK_EQ(g_activated, 1);
    teardown();
}

MOD_TEST_SUITE(page_edits_clamp_at_the_int64_bounds) {
    setup();
    // T4's settings are 64-bit and nothing stops a mod declaring the whole
    // range, so the step that would leave it is the one that must not be
    // taken: signed overflow is undefined, not merely a wrong number.
    const int64_t lo = std::numeric_limits<int64_t>::min();
    const int64_t hi = std::numeric_limits<int64_t>::max();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "huge", "Huge", POP_SETTING_INT, hi, lo,
                          hi);
    // Two degenerate ranges, where the only value allowed is the bound itself
    // and the bound is the end of the type. A guard that computes `max - step`
    // to decide whether to step overflows on these before it decides anything.
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "floor", "Floor", POP_SETTING_INT, lo, lo,
                          lo);
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "ceil", "Ceil", POP_SETTING_INT, hi, hi,
                          hi);
    MOD_CHECK_EQ(mods_page_open("a.mod"), POP_OK);
    MOD_CHECK_EQ(mods_page_line_count(), 5u);
    // Down to it, then push against the top.
    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false);
    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false);
    MOD_CHECK_EQ(mods_page_cursor(), 2u);
    int64_t v = 0;
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false); // RIGHT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "huge", &v), POP_OK);
    MOD_CHECK(v == hi);
    // And against the bottom.
    MOD_CHECK_EQ(mods_settings_set(MODS_OWNER_FIRST_MOD, "huge", lo), POP_OK);
    mods_input_key(0xcb, 0, true);
    mods_input_key(0xcb, 0, false); // LEFT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "huge", &v), POP_OK);
    MOD_CHECK(v == lo);
    // One step in from each end still moves, so the guard is a bound and not
    // a freeze.
    MOD_CHECK_EQ(mods_settings_set(MODS_OWNER_FIRST_MOD, "huge", lo + 1), POP_OK);
    mods_input_key(0xcb, 0, true);
    mods_input_key(0xcb, 0, false);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "huge", &v), POP_OK);
    MOD_CHECK(v == lo);
    MOD_CHECK_EQ(mods_settings_set(MODS_OWNER_FIRST_MOD, "huge", hi - 1), POP_OK);
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "huge", &v), POP_OK);
    MOD_CHECK(v == hi);

    // min == max == INT64_MIN, pushed up: the only legal value is the one it
    // already has, and reaching for the next one must not compute it.
    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false);
    MOD_CHECK_EQ(mods_page_cursor(), 3u);
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false); // RIGHT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "floor", &v), POP_OK);
    MOD_CHECK(v == lo);
    mods_input_key(0xcb, 0, true);
    mods_input_key(0xcb, 0, false); // LEFT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "floor", &v), POP_OK);
    MOD_CHECK(v == lo);

    // min == max == INT64_MAX, pushed down.
    mods_input_key(0xd0, 0, true);
    mods_input_key(0xd0, 0, false);
    MOD_CHECK_EQ(mods_page_cursor(), 4u);
    mods_input_key(0xcb, 0, true);
    mods_input_key(0xcb, 0, false); // LEFT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "ceil", &v), POP_OK);
    MOD_CHECK(v == hi);
    mods_input_key(0xcd, 0, true);
    mods_input_key(0xcd, 0, false); // RIGHT
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "ceil", &v), POP_OK);
    MOD_CHECK(v == hi);
    teardown();
}

MOD_TEST_SUITE(page_toggles_on_its_reserved_key) {
    setup();
    MOD_CHECK(!mods_page_visible());
    MOD_CHECK(mods_input_key(0x44, 0x79, true)); // F10, consumed
    mods_input_key(0x44, 0x79, false);
    MOD_CHECK(mods_page_visible());
    // Three mod settings, the controls' seven rows and either all nine
    // display rows or only the three host controls when no symbols are available.
    MOD_CHECK_EQ(mods_page_line_count(), mods_symbols_count() ? 19u : 13u);
    MOD_CHECK(mods_input_key(0x44, 0x79, true));
    mods_input_key(0x44, 0x79, false);
    MOD_CHECK(!mods_page_visible());
    teardown();
}

MOD_TEST_SUITE(page_shows_only_the_rows_the_game_lists) {
    // game.toml [settings] rows = ["window", "performance_overlay", "controls"]
    mods_settings_rows_for_test(1u << DISPLAY_WINDOW | 1u << DISPLAY_OVERLAY |
                                1u << DISPLAY_CONTROLS_BIT);
    setup();
    mods_page_open(nullptr);
    // Three mod settings, Display, Performance overlay and the controls' seven.
    MOD_CHECK_EQ(mods_page_line_count(), 12u);
    MOD_CHECK(std::string(mods_page_line(0)).find("Display:") == 0);
    MOD_CHECK(std::string(mods_page_line(1)).find("Performance overlay:") == 0);
    MOD_CHECK(std::string(mods_page_line(2)).find("Controls") == 0);
    // An unlisted row cannot be set and keeps its neutral value.
    MOD_CHECK_EQ(mods_display_set(DISPLAY_UI_SCALE, 3), POP_E_STATE);
    MOD_CHECK_EQ(mods_display_set(DISPLAY_RENDERING, 1), POP_E_STATE);
    MOD_CHECK_EQ(mods_display_scale(), 0);
    MOD_CHECK_EQ(mods_display_classic(), 0);
    mods_page_close();
    teardown();
    // Without the controls rows.
    mods_settings_rows_for_test(1u << DISPLAY_WINDOW | 1u << DISPLAY_OVERLAY);
    setup();
    mods_page_open(nullptr);
    MOD_CHECK_EQ(mods_page_line_count(), 5u);
    mods_page_close();
    teardown();
    mods_settings_rows_for_test((2u << DISPLAY_CONTROLS_BIT) - 1);
}

MOD_TEST_SUITE(textures_row_says_what_the_pack_holds) {
    mods_display_reset();
    MOD_CHECK(mods_display_line(DISPLAY_TEXTURES) == "Textures: HD pack");
    mods_display_texture_pack(0, 1);
    MOD_CHECK(mods_display_line(DISPLAY_TEXTURES) == "Terrain detail: on");
    mods_display_texture_pack(12, 1);
    MOD_CHECK(mods_display_line(DISPLAY_TEXTURES) == "Textures: HD pack");
    mods_display_texture_pack(0, 0);
    MOD_CHECK(mods_display_line(DISPLAY_TEXTURES) == "Textures: original (no texture pack)");
    mods_display_reset();
}

MOD_TEST_SUITE(page_draws_into_host_storage_only) {
    // The guest arena, mapped before anything asks for an address inside it.
    // Every suite that touches guest memory does this for itself; without it
    // gm_ptr is an offset from a pointer nothing has set.
    mem_init();
    setup();
    // A real guest surface: memory inside the guest's address space, reached
    // through gm_ptr exactly as the DirectDraw shim reaches a surface's
    // pixels. The presenter copies it and draws on the copy, which is the
    // whole contract - the pointer a presenter is handed points at the picture
    // the game is still reading, and this proves the page never goes there.
    const uint32_t kBytes = 320 * 200;
    uint32_t guest_addr = 0;
    MOD_CHECK_EQ(mods_guest_alloc(MODS_OWNER_FIRST_MOD, kBytes, &guest_addr), POP_OK);
    MOD_CHECK(guest_addr != 0);
    uint8_t *guest_pixels = gm_ptr(guest_addr);
    for (uint32_t i = 0; i < kBytes; ++i)
        guest_pixels[i] = (uint8_t)(3 + (i % 5));
    const std::vector<uint8_t> before(guest_pixels, guest_pixels + kBytes);

    std::vector<uint8_t> frame(320 * 200, 7);
    std::vector<uint32_t> palette(256);
    for (uint32_t i = 0; i < 256; ++i)
        palette[i] = (i << 16) | (i << 8) | i;
    mods_page_draw(frame.data(), 320, 200, 8, 320, palette.data());
    // Hidden: nothing is drawn.
    for (uint8_t px : frame)
        MOD_CHECK_EQ(px, 7);

    mods_page_open(nullptr);
    // The presenter's own copy, taken from the guest's surface the way a
    // presenter takes it, and drawn on.
    frame.assign(guest_pixels, guest_pixels + kBytes);
    mods_page_draw(frame.data(), 320, 200, 8, 320, palette.data());
    size_t changed = 0;
    for (uint32_t i = 0; i < kBytes; ++i)
        if (frame[i] != before[i])
            ++changed;
    MOD_CHECK(changed > 100); // a panel and legible text
    // And the guest's own surface is byte for byte what it was.
    MOD_CHECK(memcmp(guest_pixels, before.data(), kBytes) == 0);
    MOD_CHECK_EQ(mods_guest_free(MODS_OWNER_FIRST_MOD, guest_addr), POP_OK);
    frame.assign(320 * 200, 7);
    // It stays inside the frame: the last row is only touched if the page is
    // that tall, and nothing beyond `pitch` is written.
    mods_page_draw(frame.data(), 320, 200, 8, 320, palette.data());
    std::vector<uint8_t> guard(320 * 200, 0xee);
    mods_page_draw(guard.data(), 320, 200, 8, 320, palette.data());
    MOD_CHECK_EQ(guard[320 * 200 - 1], 0xee);

    // 16 bpp is the other mode a host presents.
    std::vector<uint16_t> frame16(320 * 200, 0x1111);
    mods_page_draw(frame16.data(), 320, 200, 16, 640, nullptr);
    size_t changed16 = 0;
    for (uint16_t px : frame16)
        if (px != 0x1111)
            ++changed16;
    MOD_CHECK(changed16 > 100);
    teardown();
}

// Test74: the tabbed menu's Tab key and the pointer's line actions (hover, click, the < > arrows and
// the Apply/Discard buttons beside the status line, which must not move the cursor).
namespace {
size_t menu_line(const MenuSnapshot &m, const char *label) {
    for (size_t i = 0; i < m.lines.size(); ++i)
        if (m.lines[i].label == label)
            return i;
    return size_t(-1);
}
} // namespace

MOD_TEST_SUITE(menu_tab_key_and_pointer_lines) {
    setup();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "aa", "Anti-aliasing", POP_SETTING_INT, 0, 0, 2);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "aa", "graphics", "help", "renderer", "Game|Off|4x");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "lights", "Lights", POP_SETTING_BOOL, 1, 0, 1);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "lights", "visual", "help", "live", "");
    mods_menu_enable_for_test(true);
    mods_menu_reset_for_test();
    mods_menu_open(0);
    MenuSnapshot m;
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, 0);
    // Remaster run 15: seven pages, in this order.
    MOD_CHECK_EQ(int(m.tabs.size()), int(MENU_TAB_COUNT));
    MOD_CHECK(m.tabs[MENU_TAB_GRAPHICS] == "Graphics" && m.tabs[MENU_TAB_DISPLAY] == "Display" &&
              m.tabs[MENU_TAB_AUDIO] == "Audio" && m.tabs[MENU_TAB_XBOX360] == "Interface" &&
              m.tabs[MENU_TAB_PC] == "Gameplay" && m.tabs[MENU_TAB_MODS] == "Mods" && m.tabs[MENU_TAB_CONTROLS] == "Controls");
    // Tab walks the pages and wraps (Page Up back); a page can be chosen directly (a click on its name).
    MOD_CHECK(mods_menu_key(MENU_KEY_TAB));
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, 1);
    for (int i = 1; i < MENU_TAB_COUNT; ++i)
        mods_menu_key(MENU_KEY_TAB);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, 0);
    mods_menu_key(MENU_KEY_TAB_PREV);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, int(MENU_TAB_CONTROLS));
    mods_menu_key(MENU_KEY_TAB_NEXT);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, 0);
    mods_menu_set_tab(MENU_TAB_CONTROLS);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK_EQ(m.tab, int(MENU_TAB_CONTROLS));
    mods_menu_set_tab(0);
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t aa = menu_line(m, "Anti-aliasing"), apply = menu_line(m, "Apply"), discard = menu_line(m, "Discard");
    MOD_CHECK(aa != size_t(-1) && apply != size_t(-1) && discard != size_t(-1));
    // A line from a page no longer showing, and a header, are refused.
    MOD_CHECK(!mods_menu_pointer_line(MENU_TAB_MODS, aa, MENU_POINT_HOVER));
    MOD_CHECK_EQ(int(m.lines[0].kind), int(MenuLine::HEADER));
    MOD_CHECK(!mods_menu_pointer_line(0, 0, MENU_POINT_HOVER));
    // Pointing selects; the arrows step the draft; the row click steps it too.
    MOD_CHECK(mods_menu_pointer_line(0, aa, MENU_POINT_HOVER));
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.lines[aa].selected);
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK(mods_menu_pointer_line(0, aa, MENU_POINT_INCREASE));
    MOD_CHECK_EQ(mods_menu_pending_count(), 1);
    MOD_CHECK(mods_menu_pointer_line(0, aa, MENU_POINT_DECREASE));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK(mods_menu_pointer_line(0, aa, MENU_POINT_ACTIVATE));
    MOD_CHECK(mods_menu_pointer_line(0, aa, MENU_POINT_ACTIVATE));
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.lines[aa].value == "4x");
    MOD_CHECK(m.lines[aa].pending);
    // Discard as a button beside the status line: the draft goes, the cursor stays.
    MOD_CHECK(mods_menu_pointer_line(0, discard, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.lines[aa].selected);
    int64_t v = -1;
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "aa", &v), POP_OK);
    MOD_CHECK_EQ(v, 0);
    // Apply as a button commits, and the cursor still stays on the row being changed.
    mods_menu_pointer_line(0, aa, MENU_POINT_INCREASE);
    MOD_CHECK(mods_menu_pointer_line(0, apply, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "aa", &v), POP_OK);
    MOD_CHECK_EQ(v, 1);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.lines[aa].selected);
    // A live setting on the Mods page changes at once from its arrow.
    mods_menu_set_tab(MENU_TAB_MODS);
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t lights = menu_line(m, "Lights");
    MOD_CHECK(lights != size_t(-1));
    MOD_CHECK(mods_menu_pointer_line(MENU_TAB_MODS, lights, MENU_POINT_DECREASE));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "lights", &v), POP_OK);
    MOD_CHECK_EQ(v, 0);
    // Off the main thread (input delivered by a guest worker holding the baton), Apply waits for the
    // main thread's next frame instead of failing.
    mods_menu_set_tab(0);
    mods_menu_pointer_line(0, aa, MENU_POINT_INCREASE);
    MOD_CHECK_EQ(mods_menu_pending_count(), 1);
    std::thread([&] { mods_menu_pointer_line(0, apply, MENU_POINT_PRESS); }).join();
    MOD_CHECK_EQ(mods_menu_pending_count(), 1);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.status == "Applying...");
    mods_menu_main_thread_drain();
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "aa", &v), POP_OK);
    MOD_CHECK_EQ(v, 2);
    int ran = 0;
    std::thread([&] { mods_menu_main_thread_call([&] { ++ran; }); }).join();
    MOD_CHECK_EQ(ran, 0);
    mods_menu_main_thread_drain();
    mods_menu_main_thread_drain();
    MOD_CHECK_EQ(ran, 1);
    mods_menu_set_tab(MENU_TAB_MODS);
    // Closed, the menu refuses the pointer.
    mods_menu_close();
    MOD_CHECK(!mods_menu_pointer_line(MENU_TAB_MODS, lights, MENU_POINT_HOVER));
    mods_menu_reset_for_test();
    mods_menu_enable_for_test(false);
    teardown();
}

// A retired choice (empty label) keeps its value slot but the menu steps over it (NFSMW removed looks, 2 Oct).
MOD_TEST_SUITE(menu_skips_retired_choice) {
    setup();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "look", "Picture look", POP_SETTING_INT, 0, 0, 3);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "look", "graphics", "help", "live", "Hybrid||PC|Custom");
    mods_menu_enable_for_test(true);
    mods_menu_reset_for_test();
    mods_menu_open(0);
    MenuSnapshot m;
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t row = menu_line(m, "Picture look");
    MOD_CHECK(row != size_t(-1));
    int64_t v = -1;
    MOD_CHECK(mods_menu_pointer_line(0, row, MENU_POINT_INCREASE));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "look", &v), POP_OK);
    MOD_CHECK_EQ(v, 2); // 0 -> (1 retired) -> 2
    MOD_CHECK(mods_menu_pointer_line(0, row, MENU_POINT_INCREASE));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "look", &v), POP_OK);
    MOD_CHECK_EQ(v, 3);
    MOD_CHECK(mods_menu_pointer_line(0, row, MENU_POINT_DECREASE));
    MOD_CHECK(mods_menu_pointer_line(0, row, MENU_POINT_DECREASE));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "look", &v), POP_OK);
    MOD_CHECK_EQ(v, 0); // 2 -> (1 retired) -> 0
    mods_menu_reset_for_test();
    mods_menu_enable_for_test(false);
    teardown();
}

// Remaster run 13: the Graphics page lists group "graphics" (the Picture list) and folds every other "graphics.*"
// group into one Advanced section, closed each time the menu opens; group "hidden" is never listed.
// Run 15: Display, Audio, Xbox 360 and PC pages take their groups; each page can Apply.
// Test85: Restore graphics defaults only drafts (Apply commits, Discard forgets), covers the Picture list and the
// Advanced settings that apply now or at Apply, and never a restart-only setting (resolution).
MOD_TEST_SUITE(menu_restore_defaults) {
    setup();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "aa", "Anti-aliasing", POP_SETTING_INT, 0, 0, 2);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "aa", "graphics", "help", "renderer", "Game|Off|4x");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "look", "Look", POP_SETTING_INT, 0, 0, 2);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "look", "graphics", "help", "renderer", "Definitive|Xbox|PC");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "glow", "Glow %", POP_SETTING_INT, 40, 0, 100);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "glow", "graphics.lighting", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "width", "Width", POP_SETTING_INT, 0, 0, 9999);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "width", "display", "help", "restart", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "day", "Day length", POP_SETTING_INT, 24, 1, 120);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "day", "graphics.weather", "help", "restart", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "hdrpeak", "HDR peak %", POP_SETTING_INT, 50, 0, 100);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "hdrpeak", "display.hdr", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "pools", "Pools %", POP_SETTING_INT, 100, 0, 200);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "pools", "ui", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "lod", "Car detail", POP_SETTING_INT, -1, -1, 3);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "lod", "game", "help", "restart", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "keys", "Light keys", POP_SETTING_BOOL, 0, 0, 1);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "keys", "game.unheard", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "surround", "Surround", POP_SETTING_BOOL, 0, 0, 1);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "surround", "audio", "help", "next_screen", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "shake", "Shake %", POP_SETTING_INT, 100, 0, 200);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "shake", "feedback", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "odd", "Odd one", POP_SETTING_BOOL, 0, 0, 1);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "odd", "graphics.unheard", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "secret", "Secret", POP_SETTING_INT, 0, 0, 3);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "secret", "hidden", "help", "renderer", "");
    mods_settings_set(MODS_OWNER_FIRST_MOD, "aa", 2);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "look", 1);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "glow", 90);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "width", 1920);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "secret", 2);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "hdrpeak", 80);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "pools", 150);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "keys", 1);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "surround", 1);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "shake", 30);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "odd", 1);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "odd", 0); // run 16: the player's choice of the default value
    MOD_CHECK(mods_settings_is_override("a.mod/odd"));
    mods_menu_enable_for_test(true);
    mods_menu_reset_for_test();
    mods_menu_open(0);
    MenuSnapshot m;
    MOD_CHECK(mods_menu_snapshot(&m));
    // Closed: the Picture list and the Advanced row; Advanced rows and the hidden setting are not listed.
    const size_t advanced = menu_line(m, "Advanced settings");
    MOD_CHECK(menu_line(m, "Picture") != size_t(-1) && menu_line(m, "Look") != size_t(-1));
    MOD_CHECK(advanced != size_t(-1) && m.lines[advanced].value.rfind("Show (", 0) == 0);
    MOD_CHECK(menu_line(m, "Glow %") == size_t(-1) && menu_line(m, "Width") == size_t(-1));
    MOD_CHECK(menu_line(m, "Secret") == size_t(-1));
    // Run 15: each new page lists its own groups, a family's unknown group under that page's Other.
    struct PageRows {
        int tab;
        const char *head, *row;
    };
    for (const PageRows &p : {PageRows{MENU_TAB_DISPLAY, "Screen", "Width"}, PageRows{MENU_TAB_DISPLAY, "HDR", "HDR peak %"},
                              PageRows{MENU_TAB_AUDIO, "Audio", "Surround"},
                              PageRows{MENU_TAB_XBOX360, "HUD and minimap", "Pools %"},
                              PageRows{MENU_TAB_CONTROLS, "Controller rumble", "Shake %"},
                              PageRows{MENU_TAB_PC, "Career and rewards", "Car detail"}, PageRows{MENU_TAB_PC, "Other", "Light keys"}}) {
        mods_menu_set_tab(p.tab);
        MOD_CHECK(mods_menu_snapshot(&m));
        const size_t head = menu_line(m, p.head), row = menu_line(m, p.row);
        MOD_CHECK(head != size_t(-1) && row != size_t(-1) && head < row);
        MOD_CHECK(menu_line(m, "Apply") != size_t(-1) && menu_line(m, "Discard") != size_t(-1));
        MOD_CHECK(menu_line(m, "Look") == size_t(-1) && menu_line(m, "Lights") == size_t(-1));
    }
    mods_menu_set_tab(MENU_TAB_MODS);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(menu_line(m, "Surround") == size_t(-1) && menu_line(m, "Shake %") == size_t(-1));
    mods_menu_set_tab(0);
    MOD_CHECK(mods_menu_snapshot(&m));
    // Opened: its parts in order, an unknown graphics group under Other; still no hidden setting.
    MOD_CHECK(mods_menu_pointer_line(0, advanced, MENU_POINT_PRESS));
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.lines[advanced].value == "Hide");
    const size_t effects = menu_line(m, "Lighting and shadows"), setup_head = menu_line(m, "Sky, weather and rain"),
                 other = menu_line(m, "Other");
    MOD_CHECK(effects != size_t(-1) && setup_head != size_t(-1) && other != size_t(-1));
    MOD_CHECK(effects < menu_line(m, "Glow %") && setup_head < menu_line(m, "Day length") &&
              other < menu_line(m, "Odd one"));
    MOD_CHECK(effects < setup_head && setup_head < other);
    MOD_CHECK(menu_line(m, "Width") == size_t(-1) && menu_line(m, "Pools %") == size_t(-1));
    MOD_CHECK(menu_line(m, "Rear-view mirror") == size_t(-1)); // an empty part shows no header
    MOD_CHECK(menu_line(m, "Secret") == size_t(-1));
    mods_menu_set_tab(1);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(menu_line(m, "Secret") == size_t(-1));
    mods_menu_set_tab(0);
    // Left closes it, Right opens it; a fresh opening of the menu starts closed.
    MOD_CHECK(mods_menu_pointer_line(0, advanced, MENU_POINT_DECREASE));
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(menu_line(m, "Glow %") == size_t(-1));
    MOD_CHECK(mods_menu_pointer_line(0, advanced, MENU_POINT_INCREASE));
    mods_menu_close();
    mods_menu_open(0);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(menu_line(m, "Glow %") == size_t(-1));
    const size_t all_restore = menu_line(m, "Restore graphics defaults"), discard = menu_line(m, "Discard"),
                 apply = menu_line(m, "Apply");
    MOD_CHECK(all_restore != size_t(-1) && menu_line(m, "Restore appearance defaults") == size_t(-1));
    MOD_CHECK(mods_menu_pointer_line(0, all_restore, MENU_POINT_PRESS));
    MOD_CHECK(mods_menu_pending_count() >= 4); // aa, look, glow, hdrpeak (+ World filtering); run 30: not pools, keys
    int64_t v = -1;
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "glow", &v), POP_OK);
    MOD_CHECK_EQ(v, 90); // drafted, not yet applied
    MOD_CHECK(mods_menu_pointer_line(0, discard, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK(mods_settings_is_override("a.mod/glow")); // Discard kept the choice
    MOD_CHECK(!mods_settings_is_override("a.mod/odd")); // already at the default: the override went at once, unseen
    MOD_CHECK(mods_menu_pointer_line(0, all_restore, MENU_POINT_PRESS));
    MOD_CHECK(mods_menu_pointer_line(0, apply, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "aa", &v), POP_OK);
    MOD_CHECK_EQ(v, 0);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "look", &v), POP_OK);
    MOD_CHECK_EQ(v, 0);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "glow", &v), POP_OK);
    MOD_CHECK_EQ(v, 40);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "width", &v), POP_OK);
    MOD_CHECK_EQ(v, 1920); // a restart-only setting is the player's setup, not restored
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "secret", &v), POP_OK);
    MOD_CHECK_EQ(v, 2); // a hidden setting is never touched by the page
    // Picture settings on the Display page are graphics defaults too; rumble and audio are not, and since run 30
    // (NFSMW D-174) neither are the Interface and Gameplay pages (HUD and game choices: their own section restores).
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "hdrpeak", &v), POP_OK);
    MOD_CHECK_EQ(v, 50);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "pools", &v), POP_OK);
    MOD_CHECK_EQ(v, 150);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "keys", &v), POP_OK);
    MOD_CHECK_EQ(v, 1);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "shake", &v), POP_OK);
    MOD_CHECK_EQ(v, 30);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "surround", &v), POP_OK);
    MOD_CHECK_EQ(v, 1);
    // Run 16: Apply of a Restore removes the overrides (the keys follow later default changes); out of scope stay.
    for (const char *k : {"a.mod/aa", "a.mod/look", "a.mod/glow", "a.mod/hdrpeak"})
        MOD_CHECK(!mods_settings_is_override(k));
    for (const char *k : {"a.mod/width", "a.mod/secret", "a.mod/shake", "a.mod/surround", "a.mod/pools", "a.mod/keys"})
        MOD_CHECK(mods_settings_is_override(k));
    // A live setting edited after a Restore draft takes the edit, and its draft goes.
    mods_settings_set(MODS_OWNER_FIRST_MOD, "glow", 70);
    MOD_CHECK(mods_menu_pointer_line(0, all_restore, MENU_POINT_PRESS));
    MOD_CHECK(mods_menu_pointer_line(0, menu_line(m, "Advanced settings"), MENU_POINT_PRESS));
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t glow = menu_line(m, "Glow %");
    MOD_CHECK(glow != size_t(-1) && m.lines[glow].pending && m.lines[glow].value == "40");
    MOD_CHECK(mods_menu_pointer_line(0, glow, MENU_POINT_DECREASE));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "glow", &v), POP_OK);
    MOD_CHECK_EQ(v, 69);
    MOD_CHECK(mods_settings_is_override("a.mod/glow")); // the edit is a choice again
    MOD_CHECK(mods_menu_pointer_line(0, menu_line(m, "Discard"), MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 0);
    // A Mods page section restores only its own settings, through the same draft.
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "lights", "Lights", POP_SETTING_BOOL, 1, 0, 1);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "lights", "visual", "help", "live", "");
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "rumble", "Rumble %", POP_SETTING_INT, 100, 0, 200);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "rumble", "feedback", "help", "live", "");
    mods_settings_set(MODS_OWNER_FIRST_MOD, "lights", 0);
    mods_settings_set(MODS_OWNER_FIRST_MOD, "rumble", 50);
    mods_menu_set_tab(MENU_TAB_MODS);
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t visual_restore = menu_line(m, "Restore this section's defaults"); // the first section: visual
    MOD_CHECK(visual_restore != size_t(-1));
    MOD_CHECK(mods_menu_pointer_line(MENU_TAB_MODS, visual_restore, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 1);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(m.status.find("not applied") != std::string::npos || m.status.find("marked") != std::string::npos);
    mods_menu_set_tab(0);
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(mods_menu_pointer_line(0, menu_line(m, "Apply"), MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "lights", &v), POP_OK);
    MOD_CHECK_EQ(v, 1);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "rumble", &v), POP_OK);
    MOD_CHECK_EQ(v, 50);
    // The rumble section (Controls page, owner 9 Oct) restores only rumble, and Apply on that page commits it.
    mods_menu_set_tab(MENU_TAB_CONTROLS);
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t rumble_restore = menu_line(m, "Restore this section's defaults");
    MOD_CHECK(rumble_restore != size_t(-1) && rumble_restore > menu_line(m, "Shake %"));
    MOD_CHECK(mods_menu_pointer_line(MENU_TAB_CONTROLS, rumble_restore, MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_menu_pending_count(), 2); // shake and rumble (both group feedback)
    MOD_CHECK(mods_menu_snapshot(&m));
    MOD_CHECK(mods_menu_pointer_line(MENU_TAB_CONTROLS, menu_line(m, "Apply"), MENU_POINT_PRESS));
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "shake", &v), POP_OK);
    MOD_CHECK_EQ(v, 100);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "rumble", &v), POP_OK);
    MOD_CHECK_EQ(v, 100);
    MOD_CHECK_EQ(mods_settings_get(MODS_OWNER_FIRST_MOD, "surround", &v), POP_OK);
    MOD_CHECK_EQ(v, 1);
    MOD_CHECK(!mods_settings_is_override("a.mod/shake") && !mods_settings_is_override("a.mod/rumble"));
    MOD_CHECK(mods_settings_is_override("a.mod/surround") && !mods_settings_is_override("a.mod/lights"));
    mods_menu_close();
    mods_menu_reset_for_test();
    mods_menu_enable_for_test(false);
    teardown();
}

// Remaster run 15: seven page names fit the tab bar at every window shape, left of Close and in order, and a click
// on each name opens that page (the names shrink to fit; the 1080p reference scales with the window). The page is
// drawn once, so every name is found where Graphics' bar put it: the bar never had to slide.
MOD_TEST_SUITE(menu_tab_bar_fits_and_clicks) {
    setup();
    mods_menu_enable_for_test(true);
    mods_menu_reset_for_test();
    struct Size {
        int w, h;
    };
    for (const Size sz : {Size{1920, 1080}, Size{3840, 2160}, Size{1280, 720}, Size{1024, 768}, Size{2560, 1080},
                          Size{800, 600}, Size{640, 480}}) {
        mods_menu_open(MENU_TAB_GRAPHICS);
        uint64_t gen = 0;
        const uint8_t *pixels = nullptr;
        MOD_CHECK(mods_menu_render(sz.w, sz.h, &gen, &pixels) == 2); // drawn
        const double s = std::min(sz.w / 1920.0, sz.h / 1080.0);
        const double ph = std::min(sz.h - 40 * s, 940 * s), py = (sz.h - ph) / 2;
        const float pv = float((py + 54 * s) / sz.h);
        std::vector<int> first_x(MENU_TAB_COUNT, -1);
        int close_x = -1;
        for (int x = 0; x < sz.w; x += 3) {
            MenuSnapshot m;
            MOD_CHECK(mods_menu_snapshot(&m));
            const int before = m.tab;
            mods_menu_pointer(float(x + 0.5) / sz.w, pv, 0, 1, 0);
            if (!mods_menu_is_open()) { // Close
                if (close_x < 0)
                    close_x = x;
                mods_menu_open(before);
                gen = 0;
                mods_menu_render(sz.w, sz.h, &gen, &pixels);
                continue;
            }
            MOD_CHECK(mods_menu_snapshot(&m));
            if (first_x[size_t(m.tab)] < 0 && (m.tab != before || m.tab == MENU_TAB_GRAPHICS))
                first_x[size_t(m.tab)] = x;
        }
        for (int t = 1; t < MENU_TAB_COUNT; ++t)
            MOD_CHECK(first_x[size_t(t)] > first_x[size_t(t - 1)]); // every page reached, in order
        MOD_CHECK(close_x > first_x[MENU_TAB_CONTROLS]);
        if (first_x[MENU_TAB_CONTROLS] < 0 || close_x < 0)
            printf("tab bar %dx%d: Controls at %d, Close at %d\n", sz.w, sz.h, first_x[MENU_TAB_CONTROLS], close_x);
        mods_menu_close();
    }
    mods_menu_reset_for_test();
    mods_menu_enable_for_test(false);
    teardown();
}

// NFSMW 3.0.4 (D-202 item 4, runtime/guest_pause.h): the menu asks for the pause only on the main thread, while open,
// with pause_in_menu on; the Apply watchdog (Test85: 20 s of the game running prove an Apply) does not count paused
// time, whether the Apply came before the pause or during it. Control: the same clock without a pause clears it.
MOD_TEST_SUITE(menu_pause_in_menu) {
    setup();
    mods_settings_declare(MODS_OWNER_FIRST_MOD, "a.mod", "aa", "Anti-aliasing", POP_SETTING_INT, 0, 0, 2);
    mods_settings_set_meta(MODS_OWNER_FIRST_MOD, "aa", "graphics", "help", "renderer", "Game|Off|4x");
    mods_menu_enable_for_test(true);
    mods_menu_reset_for_test();
    MOD_CHECK(!mods_menu_pause_wanted());   // closed
    mods_menu_open(0);
    MOD_CHECK(!mods_menu_pause_wanted());   // open, pause_in_menu off (no core mod here: its default)
    mods_menu_pause_setting_for_test(1);
    MOD_CHECK(mods_menu_pause_wanted());
    bool off_main = true;
    std::thread([&] { off_main = mods_menu_pause_wanted(); }).join();
    MOD_CHECK(!off_main);                   // a worker never parks
    MenuSnapshot m;
    MOD_CHECK(mods_menu_snapshot(&m));
    const size_t aa = menu_line(m, "Anti-aliasing"), apply = menu_line(m, "Apply");
    MOD_CHECK(aa != size_t(-1) && apply != size_t(-1));
    auto apply_at = [&](double t) {
        mods_menu_clock_for_test(t);
        int64_t v = 0;
        mods_settings_get(MODS_OWNER_FIRST_MOD, "aa", &v);
        mods_menu_pointer_line(0, aa, v ? MENU_POINT_DECREASE : MENU_POINT_INCREASE);
        MOD_CHECK(mods_menu_pointer_line(0, apply, MENU_POINT_PRESS));
        MOD_CHECK_EQ(mods_menu_pending_since_for_test(), t);
    };
    auto drain_at = [](double t) {
        mods_menu_clock_for_test(t);
        mods_menu_main_thread_drain();
        return mods_menu_pending_since_for_test();
    };
    // Applied at 100, paused 105-135: the drain at 130 (25 s after, but paused) keeps it; 20 s of running = 150.
    apply_at(100);
    mods_menu_clock_for_test(105);
    mods_menu_note_pause(true);
    MOD_CHECK_EQ(drain_at(130), 100.0);
    mods_menu_clock_for_test(135);
    mods_menu_note_pause(false);
    MOD_CHECK_EQ(mods_menu_pending_since_for_test(), 130.0);
    MOD_CHECK_EQ(drain_at(149), 130.0);
    MOD_CHECK_EQ(drain_at(151), 0.0);
    // Control: applied at 200, no pause: cleared at 221.
    apply_at(200);
    MOD_CHECK_EQ(drain_at(221), 0.0);
    // Applied during a pause (300-340, Apply at 310): its 20 s start at the resume.
    mods_menu_clock_for_test(300);
    mods_menu_note_pause(true);
    apply_at(310);
    mods_menu_clock_for_test(340);
    mods_menu_note_pause(false);
    MOD_CHECK_EQ(mods_menu_pending_since_for_test(), 340.0);
    MOD_CHECK_EQ(drain_at(359), 340.0);
    MOD_CHECK_EQ(drain_at(361), 0.0);
    mods_menu_close();
    MOD_CHECK(!mods_menu_pause_wanted());
    mods_menu_reset_for_test();
    mods_menu_enable_for_test(false);
    teardown();
}
