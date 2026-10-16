// script.cpp - see script.h.
#include "script.h"
#include "game_config.h"
#include "fe_layout.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

double host_script_counter_metric(const char *name, uint32_t (*guest_u32)(uint32_t),
                                  uint32_t (*view_turn)(), uint32_t (*view_command_frame)()) {
    const bool is_turn = !strcmp(name, "turn");
    if (!is_turn && strcmp(name, "command_frame"))
        return -1.0;
    const uint32_t value =
        guest_u32(is_turn ? RECOMP_GLOBAL_SIMULATION_TURN_ADDR : RECOMP_GLOBAL_COMMAND_FRAME_ADDR);
    auto view = is_turn ? view_turn : view_command_frame;
    if (view) {
        const uint32_t observed = view();
        // Report each counter's first mismatch without flooding a long await.
        static bool warned[2] = {};
        if (observed != value && !warned[is_turn]) {
            warned[is_turn] = true;
            fprintf(stderr, "[smoke] %s game-view cross-check mismatch: guest %u, view %u\n", name,
                    value, observed);
        }
    }
    return value;
}

#if defined(RECOMP_GLOBAL_FE_ROOT_ADDR) && defined(RECOMP_GLOBAL_FE_ROOT_SKIP)
static bool fe_package_skipped(uint32_t (*guest_u32)(uint32_t), uint32_t name) {
    static const char *const skip[] = RECOMP_GLOBAL_FE_ROOT_SKIP;
    char text[64];
    size_t n = 0;
    char c;
    while (name && n + 1 < sizeof text && (c = (char)(guest_u32(name + (uint32_t)n) & 0xffu)) != 0)
        text[n++] = c;
    text[n] = 0;
    for (const char *const *s = skip; *s; ++s)
        if (!strcasecmp(text, *s))
            return true;
    return false;
}
#endif

int host_script_fe_screen(uint32_t (*guest_u32)(uint32_t), char *out, size_t cap) {
    if (!cap)
        return 0;
    out[0] = 0;
#ifdef RECOMP_GLOBAL_FE_ROOT_ADDR
    // As the game's own FindPackageWithControl: the root object's engine, a package list entry, walked by its link
    // to the entry with a control mask; that entry's name (e.g. "MainMenu.fng"). The offsets are the game's
    // (globals.toml fe_root fields); the defaults are NFSMW's (005b0be0): engine +0x00, the LAST entry at +0xe4,
    // walked back by +0x08 to the FIRST with control +0x20, name +0x0c. NFSU1 (004e6660) walks forward from the
    // head and keeps the LAST with control (`forward = 1`). Reads only; a torn read during a screen change answers
    // the next poll.
    const uint32_t root = guest_u32(RECOMP_GLOBAL_FE_ROOT_ADDR);
    const uint32_t engine = root ? guest_u32(root + RECOMP_GLOBAL_FE_ROOT_ENGINE) : 0;
    uint32_t package = engine ? guest_u32(engine + RECOMP_GLOBAL_FE_ROOT_LIST) : 0, found = 0;
    for (int guard = 0; package && guard < 256; ++guard, package = guest_u32(package + RECOMP_GLOBAL_FE_ROOT_LINK)) {
        if (!guest_u32(package + RECOMP_GLOBAL_FE_ROOT_CONTROL))
            continue;
#ifdef RECOMP_GLOBAL_FE_ROOT_SKIP
        // Overlays that keep a control mask on every screen (UG2's help bar: its own FUN_004a0000 skips them by
        // name the same way), so they would always answer.
        if (fe_package_skipped(guest_u32, guest_u32(package + RECOMP_GLOBAL_FE_ROOT_NAME)))
            continue;
#endif
        found = package;
        if (!RECOMP_GLOBAL_FE_ROOT_FORWARD)
            break;
    }
    if (found) {
        const uint32_t name = guest_u32(found + RECOMP_GLOBAL_FE_ROOT_NAME);
        size_t n = 0;
        char c;
        while (name && n + 1 < cap && (c = (char)(guest_u32(name + (uint32_t)n) & 0xffu)) != 0)
            out[n++] = c;
        out[n] = 0;
        return n > 0;
    }
#else
    (void)guest_u32;
#endif
    return 0;
}

int host_script_key_release_ready(uint32_t down_ms, uint32_t down_reads, uint32_t now_ms, uint32_t now_reads) {
    const uint32_t held = now_ms - down_ms;
    return held >= 3000u || (held >= 160u && now_reads - down_reads >= 2u);
}

double host_script_fe_metric(const char *name, uint32_t (*guest_u32)(uint32_t)) {
    if (strncmp(name, "fe:", 3))
        return -1.0;
    char screen[64];
    return host_script_fe_screen(guest_u32, screen, sizeof screen) && strcasestr(screen, name + 3) ? 1.0 : 0.0;
}

namespace {

struct Named {
    const char *name;
    uint8_t dik;
};

// The keys a script has any reason to press. DirectInput set-1 scan codes, the
// same numbers input.mm maps the real keyboard to.
const Named kKeys[] = {
    {"ESCAPE", 0x01},
    {"RETURN", 0x1c},
    {"SPACE", 0x39},
    {"TAB", 0x0f},
    {"BACK", 0x0e},
    {"UP", 0xc8},
    {"DOWN", 0xd0},
    {"LEFT", 0xcb},
    {"RIGHT", 0xcd},
    {"PAUSE", 0xc5},
    {"P", 0x19},
    {"Y", 0x15},
    {"N", 0x31},
    {"M", 0x32},
    {"F10", 0x44},
    {"PGUP", 0xc9}, // Test73: the settings menu's previous / next page
    {"PGDN", 0xd1},
    {"F1", 0x3b},
    {"F2", 0x3c},
    {"F3", 0x3d},
    {"F4", 0x3e},
    {"F5", 0x3f},
    {"F6", 0x40},
    {"F7", 0x41},   // Remaster EO key tests
    {"1", 0x02},
    {"2", 0x03},
    {"3", 0x04},
    {"LSHIFT", 0x2a},
    {"LCONTROL", 0x1d},
    {"LALT", 0x38},
    // Text entry (Test52 profile-name checks): the remaining letters, digits
    // and the punctuation a file name can trip over.
    {"A", 0x1e},
    {"B", 0x30},
    {"C", 0x2e},
    {"D", 0x20},
    {"E", 0x12},
    {"F", 0x21},
    {"G", 0x22},
    {"H", 0x23},
    {"I", 0x17},
    {"J", 0x24},
    {"K", 0x25},
    {"L", 0x26},
    {"O", 0x18},
    {"Q", 0x10},
    {"R", 0x13},
    {"S", 0x1f},
    {"T", 0x14},
    {"U", 0x16},
    {"V", 0x2f},
    {"W", 0x11},
    {"X", 0x2d},
    {"Z", 0x2c},
    {"4", 0x05},
    {"5", 0x06},
    {"6", 0x07},
    {"7", 0x08},
    {"8", 0x09},
    {"9", 0x0a},
    {"0", 0x0b},
    {"MINUS", 0x0c},
    {"PERIOD", 0x34},
    {"SLASH", 0x35},
    {"BACKSLASH", 0x2b},
    {"COMMA", 0x33},
    {"APOSTROPHE", 0x28},
    {"SEMICOLON", 0x27},
};
const int kKeyCount = (int)(sizeof kKeys / sizeof kKeys[0]);

// One whitespace-separated word. Returns null at the end of the line.
char *word(char **p) {
    char *s = *p;
    while (*s == ' ' || *s == '\t')
        ++s;
    if (!*s) {
        *p = s;
        return nullptr;
    }
    char *start = s;
    while (*s && *s != ' ' && *s != '\t')
        ++s;
    if (*s) {
        *s = 0;
        ++s;
    }
    *p = s;
    return start;
}

// A whole decimal number and nothing else.
//
// strtol stops at the first character it does not like and reports success all
// the same, so "12abc" parses as 12 and "abc" parses as 0. That is how a typo
// in a script becomes a silent zero and a run tests the wrong thing: `move abc
// def` moved the pointer to the top-left corner for as long as nobody looked.
// Every verb goes through one of these three now. Checked before the rule went
// in: no line of any of the game's smoke scripts is rejected by it, so it
// changes what is accepted and not what runs.
bool whole(const char *s, long *out) {
    if (!s || !*s)
        return false;
    char *end = nullptr;
    long v = strtol(s, &end, 10);
    if (!end || *end)
        return false;
    *out = v;
    return true;
}

// The same, in the base the verb reads: `peek` and `watch` take 0x-hex because
// that is how every note about this game writes an address.
bool whole_base0(const char *s, long *out) {
    if (!s || !*s)
        return false;
    char *end = nullptr;
    long v = strtol(s, &end, 0);
    if (!end || *end)
        return false;
    *out = v;
    return true;
}

// And for the one operand that is not a whole number: a threshold.
bool real(const char *s, double *out) {
    if (!s || !*s)
        return false;
    char *end = nullptr;
    double v = strtod(s, &end);
    if (!end || *end)
        return false;
    *out = v;
    return true;
}

// A dump name becomes part of a filename - smoke_<name>_present.ppm from
// `dump`, smoke_<name>_composite.ppm from `dumpc` - so it has to be one. The
// two share this so they cannot drift apart, which they did for exactly as
// long as `dumpc` had the rule and `dump` did not.
const char *name_fault(const char *s, size_t cap) {
    if (strlen(s) >= cap)
        return "name is too long";
    if (s[0] == '.')
        return "name cannot start with a dot";
    for (const char *c = s; *c; ++c) {
        bool ok = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') ||
                  (*c >= '0' && *c <= '9') || *c == '_' || *c == '-';
        if (!ok)
            return "name is letters, digits, _ and -";
    }
    return nullptr;
}

bool equal_nocase(const char *a, const char *b) {
    for (; *a && *b; ++a, ++b) {
        char ca = *a >= 'a' && *a <= 'z' ? (char)(*a - 32) : *a;
        char cb = *b >= 'a' && *b <= 'z' ? (char)(*b - 32) : *b;
        if (ca != cb)
            return false;
    }
    return !*a && !*b;
}

} // namespace

uint8_t host_script_dik(const char *name) {
    if (!name)
        return 0;
    for (int i = 0; i < kKeyCount; ++i)
        if (equal_nocase(kKeys[i].name, name))
            return kKeys[i].dik;
    return 0;
}

// Parse smoke-script commands into bounded executable steps with line-numbered errors.
// Validate command arguments and capture names before the runner performs any action.
int host_script_parse(const char *text, HostScriptStep *out, int max, char *error,
                      size_t error_len) {
    if (error && error_len)
        error[0] = 0;
    if (!text)
        return 0;

    auto fail = [&](int line, const char *what, const char *detail) -> int {
        if (error && error_len)
            snprintf(error, error_len, "line %d: %s%s%s", line, what, detail ? ": " : "",
                     detail ? detail : "");
        return -1;
    };

    int count = 0;
    uint32_t clock_ms = 0;
    int line_number = 0;
    const char *p = text;
    while (*p) {
        const char *end = strchr(p, '\n');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        ++line_number;
        char line[512];
        if (len >= sizeof line)
            return fail(line_number, "line is too long", nullptr);
        memcpy(line, p, len);
        line[len] = 0;
        p = end ? end + 1 : p + len;

        // Comments and blank lines.
        char *hash = strchr(line, '#');
        if (hash)
            *hash = 0;
        char *cursor = line;
        char *verb = word(&cursor);
        if (!verb)
            continue;

        if (count >= max)
            return fail(line_number, "too many steps", nullptr);
        HostScriptStep step;
        memset(&step, 0, sizeof step);
        step.at_ms = clock_ms;

        if (equal_nocase(verb, "wait")) {
            char *ms = word(&cursor);
            long v = 0;
            if (!ms)
                return fail(line_number, "wait needs a duration in ms", nullptr);
            if (!whole(ms, &v))
                return fail(line_number, "wait needs a number of ms", ms);
            if (v < 0)
                return fail(line_number, "wait cannot go backwards", ms);
            clock_ms += (uint32_t)v;
            continue; // wait moves the clock, it is not a step
        } else if (equal_nocase(verb, "pad")) {
            // PadButton order, then hat directions, then PadState's six axes.
            static const char *names[] = {
                "cross",  "circle",  "square",  "triangle",     "l1",           "r1",
                "l2",     "r2",      "l3",      "r3",           "select",       "start",
                "ps",     "up",      "right",   "down",         "left",         "left_x",
                "left_y", "right_x", "right_y", "left_trigger", "right_trigger"};
            char *control = word(&cursor), *value = word(&cursor);
            int index = -1;
            for (int i = 0; control && i < (int)(sizeof names / sizeof names[0]); ++i)
                if (equal_nocase(control, names[i]))
                    index = i;
            if (index < 0 || !value)
                return fail(line_number, "pad needs a known control and value", control);
            long v = 0;
            if (index < 17) {
                if (!equal_nocase(value, "down") && !equal_nocase(value, "up"))
                    return fail(line_number, "pad button needs down or up", value);
                v = equal_nocase(value, "down") ? 1 : 0;
            } else if (!whole(value, &v) || v < (index < 21 ? -32767 : 0) || v > 32767) {
                return fail(line_number, "pad axis is outside its range", value);
            }
            step.op = HOST_SCRIPT_PAD;
            step.button = index;
            step.x = (int32_t)v;
        } else if (equal_nocase(verb, "viewmove") || equal_nocase(verb, "viewclick") ||
                   equal_nocase(verb, "camera")) {
            char *x = word(&cursor);
            char *y = word(&cursor);
            long vx = 0, vy = 0;
            const bool camera = equal_nocase(verb, "camera");
            if (!x || !y || !whole(x, &vx) || !whole(y, &vy) || vx < (camera ? 0 : -32767) ||
                vx > (camera ? 65535 : 32767) || vy < 0 || vy > (camera ? 65535 : 32767))
                return fail(line_number, "invalid view/camera coordinates", nullptr);
            step.op = camera                           ? HOST_SCRIPT_CAMERA
                      : equal_nocase(verb, "viewmove") ? HOST_SCRIPT_VIEWMOVE
                                                       : HOST_SCRIPT_VIEWCLICK;
            step.x = (int32_t)vx;
            step.y = (int32_t)vy;
        } else if (equal_nocase(verb, "moveby")) {
            char *dx = word(&cursor);
            char *dy = word(&cursor);
            long vx = 0, vy = 0;
            if (!dx || !dy)
                return fail(line_number, "moveby needs dx and dy", nullptr);
            if (!whole(dx, &vx))
                return fail(line_number, "moveby dx is a number", dx);
            if (!whole(dy, &vy))
                return fail(line_number, "moveby dy is a number", dy);
            step.op = HOST_SCRIPT_MOVEBY;
            step.x = (int32_t)vx;
            step.y = (int32_t)vy;
        } else if (equal_nocase(verb, "tap") || equal_nocase(verb, "tap_drawable")) {
            char *x = word(&cursor), *y = word(&cursor);
            long vx = 0, vy = 0;
            if (!x || !y || !whole(x, &vx) || !whole(y, &vy) || vx < 0 || vy < 0 || vx > 32767 ||
                vy > 32767)
                return fail(line_number, "tap needs x and y in pixels (0..32767)", nullptr);
            step.op =
                equal_nocase(verb, "tap_drawable") ? HOST_SCRIPT_TAP_DRAWABLE : HOST_SCRIPT_TAP;
            step.x = (int32_t)vx;
            step.y = (int32_t)vy;
        } else if (equal_nocase(verb, "click") || equal_nocase(verb, "move")) {
            const bool moving = equal_nocase(verb, "move");
            char *which = word(&cursor);
            if (which && equal_nocase(which, "entity")) {
                char *id = word(&cursor);
                long value = 0;
                if (!id || !whole(id, &value) || value < 0 || value > 65535)
                    return fail(line_number, "entity gesture needs an entity id (0..65535)", id);
                step.op = moving ? HOST_SCRIPT_ENTITYMOVE : HOST_SCRIPT_ENTITYCLICK;
                step.entity_id = (int32_t)value;
            } else if (which && equal_nocase(which, "world")) {
                char *x = word(&cursor);
                char *z = word(&cursor);
                char *altitude = word(&cursor);
                long vx = 0, vz = 0, va = 0;
                if (!x || !z || !altitude || !whole(x, &vx) || !whole(z, &vz) ||
                    !whole(altitude, &va) || vx < 0 || vx > 65535 || vz < 0 || vz > 65535 ||
                    va < -32768 || va > 32767)
                    return fail(line_number,
                                "world gesture needs x z (0..65535) altitude (-32768..32767)",
                                nullptr);
                step.op = moving ? HOST_SCRIPT_WORLDMOVE : HOST_SCRIPT_WORLDCLICK;
                step.x = (int32_t)vx;
                step.y = (int32_t)vz;
                step.altitude = (int32_t)va;
            } else if (moving) {
                char *y = word(&cursor);
                long vx = 0, vy = 0;
                if (!which || !y)
                    return fail(line_number, "move needs x and y", nullptr);
                if (!whole(which, &vx))
                    return fail(line_number, "move x is a number", which);
                if (!whole(y, &vy))
                    return fail(line_number, "move y is a number", y);
                step.op = HOST_SCRIPT_MOVE;
                step.x = (int32_t)vx;
                step.y = (int32_t)vy;
            } else {
                char *x = word(&cursor);
                char *y = word(&cursor);
                if (!which || !x || !y)
                    return fail(line_number, "click needs left|right|middle, x and y", nullptr);
                if (equal_nocase(which, "left"))
                    step.button = 0;
                else if (equal_nocase(which, "right"))
                    step.button = 1;
                else if (equal_nocase(which, "middle"))
                    step.button = 2;
                else
                    return fail(line_number, "click needs left, right or middle", which);
                long vx = 0, vy = 0;
                if (!whole(x, &vx))
                    return fail(line_number, "click x is a number", x);
                if (!whole(y, &vy))
                    return fail(line_number, "click y is a number", y);
                step.op = HOST_SCRIPT_CLICK;
                step.x = (int32_t)vx;
                step.y = (int32_t)vy;
            }
        } else if (equal_nocase(verb, "button")) {
            char *which = word(&cursor);
            char *dir = word(&cursor);
            if (!which || !dir)
                return fail(line_number, "button needs left|right|middle and down|up", nullptr);
            if (equal_nocase(which, "left"))
                step.button = 0;
            else if (equal_nocase(which, "right"))
                step.button = 1;
            else if (equal_nocase(which, "middle"))
                step.button = 2;
            else
                return fail(line_number, "button needs left, right or middle", which);
            if (equal_nocase(dir, "down"))
                step.down = 1;
            else if (equal_nocase(dir, "up"))
                step.down = 0;
            else
                return fail(line_number, "button needs down or up", dir);
            step.op = HOST_SCRIPT_BUTTON;
        } else if (equal_nocase(verb, "key")) {
            char *name = word(&cursor);
            char *dir = word(&cursor);
            if (!name || !dir)
                return fail(line_number, "key needs a name and down|up", nullptr);
            step.dik = host_script_dik(name);
            if (!step.dik)
                return fail(line_number, "no such key", name);
            if (equal_nocase(dir, "down"))
                step.down = 1;
            else if (equal_nocase(dir, "up"))
                step.down = 0;
            else
                return fail(line_number, "key needs down or up", dir);
            step.op = HOST_SCRIPT_KEY;
            snprintf(step.name, sizeof step.name, "%s", name);
        } else if (equal_nocase(verb, "keyuntil")) {
            // `keyuntil RETURN fe:MC_Main>0 every 2500 within 60000`: press the key (and release it 160 ms later)
            // every `every` ms until the claim holds; a claim still false at `within` stops the run. For a screen
            // that ignores a key while it animates (NFSMW's title, run 33): the claim, not a clock, says it worked.
            char *name = word(&cursor);
            char *claim = word(&cursor);
            if (!name || !claim)
                return fail(line_number, "keyuntil needs a key and metric>value", nullptr);
            step.dik = host_script_dik(name);
            if (!step.dik)
                return fail(line_number, "no such key", name);
            char *gt = strchr(claim, '>');
            if (!gt || gt == claim || !gt[1])
                return fail(line_number, "keyuntil must be key metric>value or metric>=value", claim);
            char *number = gt + 1;
            step.at_least = (*number == '=');
            if (step.at_least)
                ++number;
            *gt = 0;
            double value = 0.0;
            if (!real(number, &value))
                return fail(line_number, "keyuntil needs a number after >", number);
            step.op = HOST_SCRIPT_KEYUNTIL;
            snprintf(step.name, sizeof step.name, "%s", claim);
            step.threshold = value;
            step.press_ms = 2500;
            step.timeout_ms = 60000;
            for (;;) {
                char *clause = word(&cursor);
                if (!clause)
                    break;
                const bool every = equal_nocase(clause, "every");
                if (!every && !equal_nocase(clause, "within"))
                    return fail(line_number, "keyuntil takes `every` or `within`", clause);
                char *ms = word(&cursor);
                long v = 0;
                if (!ms || !whole(ms, &v) || v <= 0)
                    return fail(line_number, "keyuntil needs a number of ms above zero after", clause);
                if (every && v < 400)
                    return fail(line_number, "keyuntil presses at most every 400 ms", ms);
                (every ? step.press_ms : step.timeout_ms) = (uint32_t)v;
            }
            if (step.press_ms >= step.timeout_ms)
                return fail(line_number, "keyuntil would time out before its second press", verb);
        } else if (equal_nocase(verb, "focus")) {
            char *state = word(&cursor);
            if (state && equal_nocase(state, "on"))
                step.down = 1;
            else if (state && equal_nocase(state, "off"))
                step.down = 0;
            else
                return fail(line_number, "focus needs on or off", state);
            step.op = HOST_SCRIPT_FOCUS;
        } else if (equal_nocase(verb, "simdump")) {
            char *name = word(&cursor);
            if (!name)
                return fail(line_number, "simdump needs a name", nullptr);
            if (const char *why = name_fault(name, sizeof step.name))
                return fail(line_number, why, name);
            step.op = HOST_SCRIPT_SIMDUMP;
            snprintf(step.name, sizeof step.name, "%s", name);
        } else if (equal_nocase(verb, "dump")) {
            char *name = word(&cursor);
            if (!name)
                return fail(line_number, "dump needs a name", nullptr);
            // The same rule `dumpc` has, for the same reason: the name becomes
            // part of a filename.
            if (const char *why = name_fault(name, sizeof step.name))
                return fail(line_number, why, name);
            step.op = HOST_SCRIPT_DUMP;
            snprintf(step.name, sizeof step.name, "%s", name);
        } else if (equal_nocase(verb, "expect")) {
            char *claim = word(&cursor);
            if (!claim)
                return fail(line_number, "expect needs metric>value", nullptr);
            char *gt = strchr(claim, '>');
            if (!gt || gt == claim || !gt[1])
                return fail(line_number, "expect must be metric>value", claim);
            *gt = 0;
            double value = 0.0;
            if (!real(gt + 1, &value))
                return fail(line_number, "expect needs a number after >", gt + 1);
            step.op = HOST_SCRIPT_EXPECT;
            snprintf(step.name, sizeof step.name, "%s", claim);
            step.threshold = value;
        } else if (equal_nocase(verb, "peek")) {
            char *addr = word(&cursor);
            char *len = word(&cursor);
            if (!addr || !len)
                return fail(line_number, "peek needs an address and a length", nullptr);
            // Base 0 so a script may write the addresses the way every note
            // about this game writes them, as 0x8e0428.
            long a = 0, n = 0;
            if (!whole_base0(addr, &a) || a < 0)
                return fail(line_number, "peek address is a number", addr);
            if (!whole_base0(len, &n) || n < 0)
                return fail(line_number, "peek length is a number", len);
            if (!n)
                return fail(line_number, "peek needs a length of at least one byte", len);
            // A peek prints what it read, so a length that would fill the log
            // is a mistake in the script rather than something to honour.
            if (n > 256)
                return fail(line_number, "peek reads at most 256 bytes", len);
            step.op = HOST_SCRIPT_PEEK;
            step.addr = (uint32_t)a;
            step.len = (uint32_t)n;
        } else if (equal_nocase(verb, "poke")) {
            // A synthetic fixture for smoke runs only (never the app): writes
            // one byte, or a little-endian dword with "poke <addr> <value> 4".
            char *addr = word(&cursor);
            char *value = word(&cursor);
            char *size = word(&cursor);
            long a = 0, v = 0, n = 1;
            if (!addr || !value || !whole_base0(addr, &a) || a < 0 || !whole_base0(value, &v))
                return fail(line_number, "poke needs an address and a value", addr);
            if (size && (!whole_base0(size, &n) || (n != 1 && n != 4)))
                return fail(line_number, "poke size is 1 or 4", size);
            if ((n == 1 && (v < 0 || v > 255)) || v < -2147483648L || v > 4294967295L)
                return fail(line_number, "poke value does not fit its size", value);
            step.op = HOST_SCRIPT_POKE;
            step.addr = (uint32_t)a;
            step.len = (uint32_t)n;
            step.threshold = double(v);
        } else if (equal_nocase(verb, "pokeptr") || equal_nocase(verb, "peekptr")) {
            // "peekptr <ptr> <off1> <off2> <len>" reads at [[ptr]+off1]+off2;
            // "pokeptr <ptr> <off1> <off2> <value> [1|4]" writes there (smoke
            // fixtures only). Offsets are small and non-negative.
            const bool poke = equal_nocase(verb, "pokeptr");
            char *addr = word(&cursor), *o1 = word(&cursor), *o2 = word(&cursor);
            char *value = word(&cursor), *size = word(&cursor);
            long a = 0, x = 0, y = 0, v = 0, n = 1;
            if (!addr || !o1 || !o2 || !value || !whole_base0(addr, &a) || a < 0 ||
                !whole_base0(o1, &x) || x < 0 || x > 0x100000 || !whole_base0(o2, &y) || y < 0 ||
                y > 0x100000 || !whole_base0(value, &v))
                return fail(line_number, "peekptr/pokeptr need ptr off1 off2 value", addr);
            if (poke) {
                if (size && (!whole_base0(size, &n) || (n != 1 && n != 4)))
                    return fail(line_number, "pokeptr size is 1 or 4", size);
                if ((n == 1 && (v < 0 || v > 255)) || v < -2147483648L || v > 4294967295L)
                    return fail(line_number, "pokeptr value does not fit its size", value);
            } else if (v < 1 || v > 256) {
                return fail(line_number, "peekptr reads 1..256 bytes", value);
            }
            step.op = HOST_SCRIPT_PEEKPTR;
            step.addr = (uint32_t)a;
            step.x = int32_t(x);
            step.y = int32_t(y);
            step.down = poke ? 1 : 0;
            step.len = poke ? uint32_t(n) : uint32_t(v);
            step.threshold = poke ? double(v) : 0.0;
        } else if (equal_nocase(verb, "window")) {   // R3 run 17 TEST: window <w> <h> in points (app runner)
            char *w = word(&cursor);
            char *h = word(&cursor);
            long vw = 0, vh = 0;
            if (!w || !h || !whole(w, &vw) || !whole(h, &vh) || vw < 64 || vw > 16384 || vh < 64 || vh > 16384)
                return fail(line_number, "window needs a width and a height in points (64..16384)", w);
            step.op = HOST_SCRIPT_WINDOW;
            step.x = (int32_t)vw;
            step.y = (int32_t)vh;
        } else if (equal_nocase(verb, "watch")) {
            char *owner = word(&cursor);
            char *kind = word(&cursor);
            if (!owner || !kind)
                return fail(line_number, "watch needs an owner and a kind", nullptr);
            long o = 0, k = 0;
            if (!whole_base0(owner, &o) || o < 0 || o > 255)
                return fail(line_number, "watch owner is a byte", owner);
            if (!whole_base0(kind, &k) || k < 0 || k > 255)
                return fail(line_number, "watch kind is a byte", kind);
            step.op = HOST_SCRIPT_WATCH;
            step.owner = (int32_t)o;
            step.kind = (int32_t)k;
        } else if (equal_nocase(verb, "readfile")) {
            char *path = word(&cursor);
            const char *want = word(&cursor);
            if (!path || !want)
                return fail(line_number, "readfile needs a guest path and a substring", nullptr);
            step.op = HOST_SCRIPT_READFILE;
            snprintf(step.name, sizeof step.name, "%s", path);
            snprintf(step.text, sizeof step.text, "%s", want);
        } else if (equal_nocase(verb, "await")) {
            char *claim = word(&cursor);
            if (!claim)
                return fail(line_number, "await needs metric>value", nullptr);
            const bool entity_body = equal_nocase(claim, "entity_body");
            char *gt = strchr(claim, '>');
            if (entity_body) {
                char *id = word(&cursor);
                long value = 0;
                if (!id || !whole(id, &value) || value < 0 || value > 65535)
                    return fail(line_number, "entity_body needs an entity id (0..65535)", id);
                step.entity_id = (int32_t)value;
            } else if (!gt || gt == claim || !gt[1])
                return fail(line_number, "await must be metric>value or metric>=value", claim);
            // `>=` as well as `>`. A turn is a counter and the natural way to
            // wait for one is "at least N"; writing turn>699 to mean turn>=700
            // is an off-by-one waiting to happen in a file nobody re-reads.
            // Amendment 13 writes `await turn>=100`, and without this the
            // parser split it on the '>' and reported "needs a number after >:
            // =700", which is what it did to the first reference script.
            double value = 0.0;
            if (!entity_body) {
                char *number = gt + 1;
                step.at_least = (*number == '=');
                if (step.at_least)
                    ++number;
                *gt = 0;
                if (!real(number, &value))
                    return fail(line_number, "await needs a number after >", number);
            }
            step.op = HOST_SCRIPT_AWAIT;
            snprintf(step.name, sizeof step.name, "%s", entity_body ? "entity_body" : claim);
            step.threshold = value;
            // `for <ms>` how long the claim must stay true, `within <ms>`
            // how long to wait for that before giving up. Either order, both
            // optional: a script that waits for something that never happens
            // should fail rather than hang, so the bound has a default.
            step.timeout_ms = 60000;
            step.hold_ms = 0;
            for (;;) {
                char *clause = word(&cursor);
                if (!clause)
                    break;
                bool hold = equal_nocase(clause, "for");
                if (!hold && !equal_nocase(clause, "within"))
                    return fail(line_number, "await takes `for` or `within`", clause);
                char *ms = word(&cursor);
                long value = 0;
                if (!ms)
                    return fail(line_number, "await needs a number after", clause);
                if (!whole(ms, &value) || value < 0)
                    return fail(line_number, "await needs a number of ms", ms);
                if (hold)
                    step.hold_ms = (uint32_t)value;
                else if (!value)
                    return fail(line_number, "await needs a timeout above zero", ms);
                else
                    step.timeout_ms = (uint32_t)value;
            }
            if (step.hold_ms >= step.timeout_ms)
                return fail(line_number, "await would time out before it could hold", verb);
        } else if (equal_nocase(verb, "mode")) {
            char *w = word(&cursor);
            char *h = word(&cursor);
            char *bpp = word(&cursor);
            long vw = 0, vh = 0, vb = 0;
            if (!w || !h || !bpp)
                return fail(line_number, "mode needs a width, a height and a depth", nullptr);
            if (!whole(w, &vw) || vw < 1 || vw > 16384)
                return fail(line_number, "mode width is a number of pixels", w);
            if (!whole(h, &vh) || vh < 1 || vh > 16384)
                return fail(line_number, "mode height is a number of pixels", h);
            // The depths DirectDraw has. A mode the game could never be asked
            // for is a mistake in the script, and rejecting it here is cheaper
            // than a run that waits twenty seconds to find out.
            // Eight and sixteen only. Those are the two pixel formats the
            // DirectDraw shim's enumeration can describe, so a script that
            // asks for 24 or 32 parses a mode the game can never be offered
            // and then waits twenty seconds to be told so.
            if (!whole(bpp, &vb) || (vb != 8 && vb != 16))
                return fail(line_number, "mode depth is 8 or 16", bpp);
            step.op = HOST_SCRIPT_MODE;
            step.w = (int32_t)vw;
            step.h = (int32_t)vh;
            step.bpp = (int32_t)vb;
            // No `within`. The game applies a mode when a level starts and it
            // recreates its surfaces, not when the options screen is left, so
            // there is no interval this verb could wait out: it arms the
            // claim and the run answers it at the end. The script's own awaits
            // are what bound the run.
        } else if (equal_nocase(verb, "guestclick")) {
            char *gx = word(&cursor);
            char *gy = word(&cursor);
            long vx = 0, vy = 0;
            if (!gx || !gy)
                return fail(line_number, "guestclick needs x and y in guest pixels", nullptr);
            if (!whole(gx, &vx) || vx < 0 || vx > 16383)
                return fail(line_number, "guestclick x is a guest pixel", gx);
            if (!whole(gy, &vy) || vy < 0 || vy > 16383)
                return fail(line_number, "guestclick y is a guest pixel", gy);
            step.op = HOST_SCRIPT_GUESTCLICK;
            step.x = (int32_t)vx;
            step.y = (int32_t)vy;
            step.button = 0;
            // Press to release, on the guest's clock. A press and a release in
            // one guest turn leave the button where it started and both are
            // lost, which is the same lesson `click` learned the hard way.
            step.press_ms = 60;
            char *next = word(&cursor);
            if (next && !equal_nocase(next, "hold")) {
                if (equal_nocase(next, "left"))
                    step.button = 0;
                else if (equal_nocase(next, "right"))
                    step.button = 1;
                else if (equal_nocase(next, "middle"))
                    step.button = 2;
                else
                    return fail(line_number, "guestclick needs left, right or middle", next);
                next = word(&cursor);
            }
            if (next) {
                if (!equal_nocase(next, "hold"))
                    return fail(line_number, "guestclick takes `hold`", next);
                char *ms = word(&cursor);
                long v = 0;
                if (!ms)
                    return fail(line_number, "guestclick needs a number after", next);
                if (!whole(ms, &v) || v <= 0)
                    return fail(line_number, "guestclick needs a hold above zero", ms);
                step.press_ms = (uint32_t)v;
            }
        } else if (equal_nocase(verb, "probe")) {
            char *px = word(&cursor);
            char *py = word(&cursor);
            char *pr = word(&cursor);
            char *pg = word(&cursor);
            char *pb = word(&cursor);
            long vx = 0, vy = 0, vr = 0, vg = 0, vb = 0;
            if (!px || !py || !pr || !pg || !pb)
                return fail(line_number, "probe needs x, y and a colour", nullptr);
            if (!whole(px, &vx) || vx < 0 || vx > 16383)
                return fail(line_number, "probe x is a drawable pixel", px);
            if (!whole(py, &vy) || vy < 0 || vy > 16383)
                return fail(line_number, "probe y is a drawable pixel", py);
            if (!whole(pr, &vr) || vr < 0 || vr > 255)
                return fail(line_number, "probe red is 0 to 255", pr);
            if (!whole(pg, &vg) || vg < 0 || vg > 255)
                return fail(line_number, "probe green is 0 to 255", pg);
            if (!whole(pb, &vb) || vb < 0 || vb > 255)
                return fail(line_number, "probe blue is 0 to 255", pb);
            step.op = HOST_SCRIPT_PROBE;
            step.x = (int32_t)vx;
            step.y = (int32_t)vy;
            step.r = (int32_t)vr;
            step.g = (int32_t)vg;
            step.b = (int32_t)vb;
            // Exact by default. A tolerance is something a script asks for on
            // purpose, not something it gets for nothing.
            step.tol = 0;
            char *tol = word(&cursor);
            if (tol) {
                long v = 0;
                if (!whole(tol, &v) || v < 0 || v > 255)
                    return fail(line_number, "probe tolerance is 0 to 255", tol);
                step.tol = (int32_t)v;
            }
        } else if (equal_nocase(verb, "dumpc")) {
            char *name = word(&cursor);
            if (!name)
                return fail(line_number, "dumpc needs a name", nullptr);
            // The name becomes part of a filename, so it has to be one. `dump`
            // has the same exposure and no such check; that is a gap in the
            // older verb rather than a reason to leave one in this one.
            if (strlen(name) >= sizeof step.name)
                return fail(line_number, "dumpc name is too long", name);
            if (name[0] == '.')
                return fail(line_number, "dumpc name cannot start with a dot", name);
            for (const char *c = name; *c; ++c) {
                bool ok = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') ||
                          (*c >= '0' && *c <= '9') || *c == '_' || *c == '-';
                if (!ok)
                    return fail(line_number, "dumpc name is letters, digits, _ and -", name);
            }
            step.op = HOST_SCRIPT_DUMPC;
            snprintf(step.name, sizeof step.name, "%s", name);
        } else if (equal_nocase(verb, "dumpat")) {
            char *claim = word(&cursor);
            char *name = word(&cursor);
            if (!claim || !name)
                return fail(line_number, "dumpat needs metric>=value and a name", nullptr);
            // Accept name-first syntax as well as existing metric-first scripts.
            if (!strchr(claim, '>') && strchr(name, '>')) {
                char *swap = claim;
                claim = name;
                name = swap;
            }
            char *gt = strchr(claim, '>');
            if (!gt || gt == claim || !gt[1])
                return fail(line_number, "dumpat must be metric>value or metric>=value", claim);
            char *number = gt + 1;
            step.at_least = (*number == '=');
            if (step.at_least)
                ++number;
            *gt = 0;
            double value = 0.0;
            if (!real(number, &value))
                return fail(line_number, "dumpat needs a number after >", number);
            if (const char *why = name_fault(name, sizeof step.text))
                return fail(line_number, why, name);
            step.op = HOST_SCRIPT_DUMPAT;
            snprintf(step.name, sizeof step.name, "%s", claim);
            step.threshold = value;
            // The NAME goes in `text`, because `name` is already carrying the
            // metric. Reusing one field for both would make the executor read
            // the metric out of a field the parser had overwritten.
            snprintf(step.text, sizeof step.text, "%s", name);
        } else if (equal_nocase(verb, "landmark")) {
            char *id = word(&cursor);
            char *expect = word(&cursor);
            const char *want = word(&cursor);
            long vid = 0;
            if (!id || !expect || !want)
                return fail(line_number, "landmark needs an id, `expect` and visible|hidden",
                            nullptr);
            if (!whole(id, &vid) || vid < 0 || vid > 65535)
                return fail(line_number, "landmark id is an entity index", id);
            if (!equal_nocase(expect, "expect"))
                return fail(line_number, "landmark takes `expect`", expect);
            if (*want == '$') {
                const char *name = want + 1;
                if (!*name ||
                    ((*name < 'A' || *name > 'Z') && (*name < 'a' || *name > 'z') && *name != '_'))
                    return fail(line_number, "invalid landmark environment name", want);
                for (const char *c = name; *c; ++c)
                    if ((*c < 'A' || *c > 'Z') && (*c < 'a' || *c > 'z') &&
                        (*c < '0' || *c > '9') && *c != '_')
                        return fail(line_number, "invalid landmark environment name", want);
                want = getenv(name);
                if (!want || !*want)
                    return fail(line_number, "landmark environment is unset or empty", name);
            }
            if (equal_nocase(want, "visible"))
                step.want_visible = 1;
            else if (equal_nocase(want, "hidden"))
                step.want_visible = 0;
            else
                return fail(line_number, "landmark expects visible or hidden", want);
            step.op = HOST_SCRIPT_LANDMARK;
            step.entity_id = (int32_t)vid;
            step.timeout_ms = 20000;
            char *clause = word(&cursor);
            if (clause) {
                if (!equal_nocase(clause, "within"))
                    return fail(line_number, "landmark takes `within`", clause);
                char *ms = word(&cursor);
                long v = 0;
                if (!ms)
                    return fail(line_number, "landmark needs a number after", clause);
                if (!whole(ms, &v) || v < 0)
                    return fail(line_number, "landmark needs a nonnegative timeout", ms);
                step.timeout_ms = (uint32_t)v;
            }
        } else if (equal_nocase(verb, "quit")) {
            step.op = HOST_SCRIPT_QUIT;
        } else {
            return fail(line_number, "unknown command", verb);
        }

        if (word(&cursor))
            return fail(line_number, "too many words", verb);
        out[count++] = step;
    }
    return count;
}

uint32_t host_script_hold_frames(uint32_t hold_ms, uint32_t step_ms) {
    if (!hold_ms)
        return 0;
    uint32_t step = step_ms ? step_ms : 50u; // the nominal frame, see script.h
    uint32_t frames = (hold_ms + step - 1) / step;
    return frames ? frames : 1u;
}

int host_script_run_unfinished(int abnormal_exit, int next_step, int step_count) {
    if (abnormal_exit)
        return 1;
    return next_step < step_count ? 1 : 0;
}

int host_script_drain_wanted(const HostScriptDrainState *s) {
    if (!s)
        return 0;
    if (s->quit_requested)
        return 0;
    // Someone else is already doing it. Saying yes here would have the drain
    // return having done nothing and be asked again at once.
    if (s->ticking)
        return 0;
    // An owed release is work, and only once it is actually owed.
    if (s->holding_button)
        return s->hold_reached ? 1 : 0;
    if (s->guestclick_held)
        return s->guestclick_reached ? 1 : 0;
    // A recorded path being replayed is the script, for as long as it lasts.
    if (s->sub_active)
        return s->sub_step_due ? 1 : 0;
    // An await is NOT work the drain can finish: draining runs the tick, the
    // tick looks at the claim, and if it has not passed nothing has changed.
    // The tick still looks on every clock read, which is where an await was
    // always answered.
    if (s->await_started)
        return 0;
    if (!s->steps_left)
        return 0;
    if (!s->script_started)
        return 1;
    return s->step_due ? 1 : 0;
}

uint32_t host_script_input_hold_frames(uint32_t hold_ms, uint32_t step_ms) {
    uint32_t frames = host_script_hold_frames(hold_ms, step_ms);
    return frames < 4u ? 4u : frames;
}

HostEntityWaitResult HostEntityWait::poll(uint32_t now_ms, uint32_t presents, uint64_t frame,
                                          uint32_t captured_present, bool body_present) {
    if (!active) {
        active = true;
        since_ms = now_ms;
        since_present = presents;
        last_frame = 0;
    }
    const uint32_t waited = presents - since_present;
    if (host_entity_body_ready(presents, frame, captured_present, true)) {
        last_frame = frame;
        if (host_entity_body_ready(presents, frame, captured_present, body_present) &&
            waited <= max_presents)
            return HostEntityWaitResult::ready;
    }
    return waited >= max_presents ? HostEntityWaitResult::timed_out : HostEntityWaitResult::pending;
}

void HostEntityWait::finish(uint32_t now_ms, uint32_t &script_start_ms) {
    script_start_ms += now_ms - since_ms; // preserve waits after this gesture
    *this = {};
}

void host_entity_body_diagnostic(char *out, size_t size, int32_t id, uint32_t completed_present,
                                 const HostEntityBodyRecord &body, bool passed) {
    snprintf(out, size,
             "entity_body %d: %s frame %llu, last record present %u, "
             "current completed present %u%s",
             id, passed ? "PASS attributed BODY" : "FAIL no fresh attributed BODY",
             (unsigned long long)body.frame, body.present, completed_present,
             body.frame ? "" : " (no record)");
}
