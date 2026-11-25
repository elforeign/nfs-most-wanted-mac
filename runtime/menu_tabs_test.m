// Actual menu fixture: 4 sparse / 9 full; forced-true helper draws 9 sparse.
// The broken helper must fail both rendering and navigation checks.
#define main guide_fixture_main
#include "guide_test.m"
#undef main

static Setting fixtures[SET_NCATEGORIES];
static const int shown[] = {SET_PICTURE, SET_DISPLAY, SET_CONTROLS, SET_DIAGNOSTICS};

static void registry(int full) {
    for (int t = 0; t < SET_NCATEGORIES; ++t) {
        int visible = full;
        for (unsigned i = 0; i < sizeof shown / sizeof *shown; ++i) visible |= t == shown[i];
        fixtures[t].hidden = !visible;
    }
}
static int drawn_tabs(void) {
    [text_layouts removeAllObjects];
    CHECK(render());
    int count = 0;
    for (int t = 0; t < SET_NCATEGORIES; ++t) {
        NSString *name = [NSString stringWithUTF8String:settings_category_names[t]];
        int label = 0;
        for (NSArray *key in text_layouts) label |= [key[0] isEqualToString:name];
        CHECK(label == (tab_x[t + 1] > tab_x[t]));
        count += label;
    }
    return count;
}
static void key(unsigned code) {
    CHECK(portmenu_key((uint8_t)code, 1, 0));
    CHECK(portmenu_key((uint8_t)code, 0, 0));
}
static void pad_tab(uint32_t button) {
    uint32_t inject;
    portmenu_pad(button, 0, 0, 0, 0, 0, 0, 0, 0, 0, &inject);
    portmenu_pad(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, &inject);
}
static void mouse(int kind, double x) {
    double f = fit_scale(layer.bounds.size), ox, oy;
    panel_origin(layer.bounds.size, f, &ox, &oy);
    CHECK(portmenu_mouse(kind, ox + x * f, oy + (TAB_Y + 5) * f, 0));
}

int main(void) {
    @autoreleasepool {
        G = calloc(1, 0x1000000); ticks = 1000000000;
        layer = [CAMetalLayer layer]; layer.bounds = CGRectMake(0, 0, PW + 24, PH + 24);
        layers(1); is_open = 1;
        guide_row.hidden = licences_row.hidden = shortcut_row.hidden = 1;
        for (int t = 0; t < SET_NCATEGORIES; ++t) {
            fixtures[t] = (Setting){.id = settings_category_names[t], .category = t,
                .label = "Fixture row", .help = "Fixture description", .type = SET_TOGGLE};
            settings_add(&fixtures[t]);
        }
        registry(1);
        int full = drawn_tabs();
        printf("full shown=%d expected=9\n", full); CHECK(full == 9);
        double old_audio = (tab_x[SET_AUDIO] + tab_x[SET_AUDIO + 1]) / 2;
        for (int i = 0; i < 9; ++i) { key(0x09); CHECK(tab == (i + 1) % 9); }
        for (int i = 0; i < 9; ++i) { key('Q'); CHECK(tab == (17 - i) % 9); }
        registry(0);
        int sparse = drawn_tabs();
        printf("sparse shown=%d expected=4\n", sparse); CHECK(sparse == 4);
        for (int t = 0; t < SET_NCATEGORIES; ++t)
            if (fixtures[t].hidden) CHECK(tab_x[t] == tab_x[t + 1]);
        for (int i = 0; i < 4; ++i) { key(0x09); CHECK(tab == shown[(i + 1) % 4]); }
        CHECK(tab == SET_PICTURE);
        for (int i = 0; i < 4; ++i) { key('Q'); CHECK(tab == shown[(7 - i) % 4]); }
        key('E'); CHECK(tab == SET_DISPLAY);
        pad_tab(B_L1); CHECK(tab == SET_PICTURE);
        pad_tab(B_L1); CHECK(tab == SET_DIAGNOSTICS);
        pad_tab(B_R1); CHECK(tab == SET_PICTURE);
        CHECK(old_audio > tab_x[SET_NCATEGORIES] && old_audio < PW);
        mouse(0, old_audio); CHECK(hover_tab == -1);
        mouse(1, old_audio); CHECK(tab == SET_PICTURE);
        // Shown labels remain clickable after compaction.
        for (int i = 0; i < 4; ++i) {
            int t = shown[i];
            double x = (tab_x[t] + tab_x[t + 1]) / 2;
            mouse(0, x); CHECK(hover_tab == t);
            mouse(1, x); CHECK(tab == t);
        }
        // A registry change can hide a tab before the next raster updates bounds.
        for (int t = 0; t <= SET_NCATEGORIES; ++t) tab_x[t] = 0;
        tab_x[SET_WORLD] = 200; tab_x[SET_WORLD + 1] = 220;
        tab = SET_PICTURE;
        mouse(0, 210); CHECK(hover_tab == -1);
        mouse(1, 210); CHECK(tab == SET_PICTURE);
        set_open(0); tab = SET_AUDIO; set_open(1); CHECK(tab == SET_PICTURE);
        set_open(0); tab = SET_CONTROLS; set_open(1); CHECK(tab == SET_CONTROLS);
        // A heading with only hidden children does not make an empty page visible.
        static Setting child = {.id = "hidden-child", .category = SET_WORLD,
            .order = 1, .type = SET_TOGGLE, .hidden = 1};
        settings_add(&child);
        fixtures[SET_WORLD].hidden = 0; fixtures[SET_WORLD].type = SET_HEADING;
        CHECK(drawn_tabs() == 4);
        for (int t = 0; t < SET_NCATEGORIES; ++t) fixtures[t].hidden = t != SET_CONTROLS;
        CHECK(drawn_tabs() == 1);
        key(0x09); CHECK(tab == SET_CONTROLS);
        key('Q'); CHECK(tab == SET_CONTROLS);
        set_open(0); tab = SET_PICTURE; set_open(1); CHECK(tab == SET_CONTROLS);
        fixtures[SET_CONTROLS].hidden = 1;
        CHECK(drawn_tabs() == 0);
        set_open(0); set_open(1); CHECK(sel == -1);
        key(0x09); CHECK(tab == SET_CONTROLS && sel == -1);
        printf("menu tabs: %d checks, %d failures\n", checks, failures);
        free(G);
        return failures != 0;
    }
}
