/* MW 1.3 FE observations on the guest frame thread. */
#include "runtime.h"
#include "blackbox.h"
#include "blackbox_mw_fe.h"
#include <strings.h>

enum { PACKAGE_LIMIT = 24, OBJECT_LIMIT = 64, NAME_LIMIT = 48 };
#define FADE_PACKAGE_HASH 0x89246F98u /* FEHashUpper("FadeScreen.fng") */
#define FADE_OBJECT_HASH 0x027FF2DCu

static int readable(uint32_t address) {
    /* Guest memory is a flat 4 GiB mapping. Leave room for node fields. */
    return address >= 0x1000u && address < 0xFFFFF000u;
}

static void read_name(char name[NAME_LIMIT], uint32_t address) {
    if (readable(address)) {
        for (unsigned i = 0; i < NAME_LIMIT; ++i) {
            unsigned ch = M8(address + i);
            name[i] = (char)ch;
            if (ch == 0) return;
            if (ch < 0x20 || ch >= 0x7F) break;
        }
    }
    name[0] = 0;
}

static uint32_t label(uint32_t address, int stage) {
    char name[NAME_LIMIT];
    read_name(name, address);
    return stage ? blackbox_mw_stage_label(name) : blackbox_fe_label(name);
}

static void read_manager(BlackboxMWFE *state) {
    state->pause_request = M32(0x0091CAE4u);
    uint32_t manager = M32(0x0091CAE0u);
    if (!readable(manager)) return;

    state->manager = manager;
    state->manager_0 = M8(manager);
    state->manager_1 = M8(manager + 1);
    state->controller_errors_lo = M32(manager + 2);
    state->controller_errors_hi = M32(manager + 6);
    state->requested_name = M32(manager + 0x0C);
    state->garage_type = M32(manager + 0x1C);
    state->boot = M8(manager + 0x24);
}

static uint32_t read_control(BlackboxMWFE *state, uint32_t package) {
    uint32_t name = 0;
    /* cFEng::FindPackageWithControl walks backward from the active tail. */
    for (; readable(package) && state->package_count < PACKAGE_LIMIT; package = M32(package + 8)) {
        uint32_t control = M32(package + 0x20);
        uint32_t hash = M32(package + 0x10);
        state->list_signature = (state->list_signature ^ package) * 16777619u;
        state->list_signature = (state->list_signature ^ hash) * 16777619u;
        state->list_signature = (state->list_signature ^ control) * 16777619u;
        ++state->package_count;
        if (control && !state->control_package) {
            state->control_package = package;
            state->control_hash = hash;
            name = M32(package + 0x0C);
        }
    }
    if (package) state->limits |= BB_MW_PACKAGE_LIMIT;
    return name;
}

static uint32_t find_fade_package(BlackboxMWFE *state, uint32_t package) {
    /* FEList::FindNode walks forward from the head, using name hash then name. */
    static uint32_t cached_name;
    static int cached_match;
    for (unsigned i = 0; readable(package) && i < PACKAGE_LIMIT; ++i, package = M32(package + 4)) {
        if (M32(package + 0x10) != FADE_PACKAGE_HASH) continue;
        uint32_t address = M32(package + 0x0C);
        if (address != cached_name) {
            char name[NAME_LIMIT];
            read_name(name, address);
            cached_match = !strcasecmp(name, "FadeScreen.fng");
            cached_name = address;
        }
        if (cached_match) return package;
    }
    if (package) state->limits |= BB_MW_FADE_PACKAGE_LIMIT;
    return 0;
}

static void read_fade(BlackboxMWFE *state, uint32_t engine) {
    static struct { uint32_t link, object; } cached_path[OBJECT_LIMIT];
    static unsigned cached_count;
    static uint32_t cached_package, cached_object;
    uint32_t package = find_fade_package(state, M32(engine + 0xE0));
    if (!package && !(state->limits & BB_MW_FADE_PACKAGE_LIMIT))
        package = find_fade_package(state, M32(engine + 0xF0)); /* idle fallback */
    state->fade_package = package;
    if (!package) {
        cached_object = 0;
        return;
    }

    unsigned checked = 0;
    if (cached_object && package == cached_package) {
        /* Verify the links to the cached object so a detached subtree can't
         * keep reporting an old script after the package changes in place. */
        while (checked < cached_count &&
               M32(cached_path[checked].link) == cached_path[checked].object)
            ++checked;
    }
    if (!checked || checked != cached_count || M32(cached_object + 0x10) != FADE_OBJECT_HASH) {
        /* FEPackage::ForAllObjects visits children before the next sibling.
         * Save the links to pending siblings, with one budget for the entire tree. */
        uint32_t siblings[OBJECT_LIMIT];
        unsigned depth = 0;
        uint32_t link = package + 0x4C;
        uint32_t object = M32(link);
        cached_count = 0;
        cached_object = 0;
        for (unsigned i = 0; readable(object) && i < OBJECT_LIMIT; ++i) {
            cached_path[cached_count].link = link;
            cached_path[cached_count++].object = object;
            if (M32(object + 0x10) == FADE_OBJECT_HASH) {
                cached_package = package;
                cached_object = object;
                break;
            }
            link = object + 4;
            if (M32(object + 0x18) == 5 && M32(object + 0x64)) {
                siblings[depth++] = link;
                link = object + 0x64;
            }
            object = M32(link);
            while (!object && depth) {
                link = siblings[--depth];
                object = M32(link);
            }
        }
        if (!cached_object && object) state->limits |= BB_MW_FADE_OBJECT_LIMIT;
    }
    state->fade_object = cached_object;
    if (!cached_object) return;

    uint32_t script = M32(cached_object + 0x54);
    if (readable(script)) state->fade_script = M32(script + 0x30);
    else if (script) state->limits |= BB_MW_FADE_SCRIPT_INVALID;
}

void blackbox_mw_fe(uint64_t frame, uint32_t flow, uint64_t now) {
    if (!blackbox_enabled() || !G) return;

    static BlackboxMWFE previous;
    static uint32_t previous_control_name;
    static uint32_t requested_label, loading_label, stage_label, control_label;
    static int have_previous;
    BlackboxMWFE state = {
        .flow = flow,
        .continuation = M32(0x00925E70u),
        .stage_name = M32(0x00925E78u),
        .loading_name = M32(0x008F4318u),
        .screen_seen = M8(0x00988E1Du),
        .tips_done = M8(0x008F3F38u),
    };
    read_manager(&state);
    uint32_t wrapper = M32(0x0091CADCu);
    uint32_t engine = readable(wrapper) ? M32(wrapper) : 0;
    uint32_t control_name = 0;
    if (readable(engine)) {
        state.engine_ready = 1;
        control_name = read_control(&state, M32(engine + 0xE4));
        read_fade(&state, engine);
    }
    if (have_previous && control_name == previous_control_name && !memcmp(&state, &previous, sizeof state))
        return;

    if (state.requested_name != previous.requested_name) requested_label = label(state.requested_name, 0);
    if (state.loading_name != previous.loading_name) loading_label = label(state.loading_name, 0);
    if (state.stage_name != previous.stage_name) stage_label = label(state.stage_name, 1);
    if (control_name != previous_control_name) control_label = label(control_name, 0);
    previous = state;
    previous_control_name = control_name;
    have_previous = 1;
    state.requested_label = requested_label;
    state.loading_label = loading_label;
    state.stage_label = stage_label;
    state.control_label = control_label;
    blackbox_record_mw_fe(&state, frame, now);
}
