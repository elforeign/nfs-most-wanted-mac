// Native Controls -> settings -> actual padfx / rumble sinks, ASan/UBSan.
// Measured normal and disconnected-row controls: trigopt_test.py header.
#include "roots.h"
#include "setting_bool.h"
#define TRIGOPT_TEST 1
#include "padfx_test_support.h"
#include "settings.c"
#include <errno.h>
static const char *pref_root;
const char *userdata_dir(void) { return pref_root; }
static int durable_commit_with(FILE *f, const char *tmp, const char *path, int flags) {
    (void)flags; return fclose(f) == 0 && rename(tmp, path) == 0;
}
#define app_pref disk_pref
#define app_pref_set disk_pref_set
#define app_pref_batch_begin disk_batch_begin
#define app_pref_batch_end disk_batch_end
#include "trigopt_prefs.inc"
#undef app_pref
#undef app_pref_set
#undef app_pref_batch_begin
#undef app_pref_batch_end
#ifndef TRIGOPT_SOURCE
#define TRIGOPT_SOURCE "trigopt.c"
#endif
#include TRIGOPT_SOURCE
#ifndef CONTROLPAGES_SOURCE
#define CONTROLPAGES_SOURCE "controlpages.c"
#endif
#include CONTROLPAGES_SOURCE
#include "trigopt_render_fixture.h"

static uint32_t heap = 0xa00000;
static HostFn callbacks[64];
static unsigned ncallbacks, added, native_calls, capture_calls;
static char drawn_text[8][64], drawn_value[16][32], saved[16];
static unsigned pref_writes;
enum { SCREEN = 0x20000, SLOT_BASE = 0x21000, LABEL_BASE = 0x23000, VALUE_BASE = 0x25000 };
uint32_t galloc(uint32_t n) { uint32_t p = heap; heap = (heap + n + 3) & ~3u; CHECK(heap < 0x1000000); return p; }
uint32_t gstr(const char *s) { uint32_t p = galloc((uint32_t)strlen(s) + 1); strcpy((char *)G + p, s); return p; }
uint32_t host_fn(HostFn fn, const char *name) {
    (void)name;
    CHECK(ncallbacks < 64); callbacks[ncallbacks] = fn; return HOSTFN_BASE + ncallbacks++ * 4;
}
const char *app_pref(const char *key) { return disk_pref(key); }
void app_pref_set(const char *key, const char *value) {
    CHECK(!strcmp(key, "controls.adaptive_triggers"));
    snprintf(saved, sizeof saved, "%s", value); ++pref_writes; disk_pref_set(key, value);
}
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
static void append(uint32_t end, uint32_t datum) {
    uint32_t node = datum + 4, last = M32(end + 4);
    M32(node) = end; M32(node + 4) = last; M32(last) = node; M32(end + 4) = node;
}
static unsigned datum_count(void) {
    unsigned n = 0;
    for (uint32_t p = M32(SCREEN + 0x3c); p != SCREEN + 0x3c; p = M32(p)) { CHECK(++n < 64); }
    return n;
}
static void draw_all(Cpu *c) {
    uint32_t node = M32(SCREEN + 0x3c);
    // Put the final eight rows in the physical viewport, including the toggle.
    unsigned count = datum_count();
    for (unsigned i = 0; i + 16 < count; ++i) node = M32(node);
    for (unsigned i = 0; i < 16 && node != SCREEN + 0x3c; ++i, node = M32(node)) {
        uint32_t slot = SLOT_BASE + 0x40 * i;
        uint32_t args[] = {node - 4, M32(SCREEN + 0x44) == node - 4};
        Cpu q = *c; q.ecx = slot; call_guest(&q, M32(M32(slot) + 4), 2, args);
    }
}
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    if (fn >= HOSTFN_BASE && fn < HOSTFN_BASE + ncallbacks * 4) {
        Cpu q = *c; uint32_t before = q.esp;
        for (int i = n - 1; i >= 0; --i) { q.esp -= 4; M32(q.esp) = a[i]; }
        q.esp -= 4; M32(q.esp) = 0;
        callbacks[(fn - HOSTFN_BASE) / 4](&q); CHECK(q.esp == before); return q.eax;
    }
    if (fn == FN_PLAYER_PURSUIT) { M32(a[0]) = 0; M32(a[1]) = 0; return 0; }
    if (fn == FN_GET_ATTRIBUTE) return a[1] == 0 ? 0x7000 : 0;
    if (fn == 0x00652AD0u) return galloc(a[0]);
    if (fn == 0x0051D330u) { M32(c->ecx) = 0x89c244; M8(c->ecx + 0x14) = 1; return c->ecx; }
    if (fn == 0x0059BD30u) { append(c->ecx + 0x10, a[0]); ++added; return 0; }
    if (fn == 0x0052AB90u) { draw_all(c); return 0; }
    if (fn == 0x005739D0u) return 0;
    if (fn == 0x0051D3A0u) {
        uint32_t label = M32(c->ecx + 0x14);
        M32(label + 0x1c) &= ~2u; return 0;
    }
    if (fn == 0x0050D880u) {
        CHECK(M32(c->ecx + 0x1c) & 2u);
        if (c->ecx >= LABEL_BASE && c->ecx < LABEL_BASE + 0x800)
            snprintf(drawn_text[(c->ecx - LABEL_BASE) / 0x100], 64, "%s", (char *)G + a[0]);
        else if (c->ecx >= VALUE_BASE && c->ecx < VALUE_BASE + 0x1000)
            snprintf(drawn_value[(c->ecx - VALUE_BASE) / 0x100], 32, "%s", (char *)G + a[0]);
        else CHECK(0);
        return 0;
    }
    if (fn == 0x00545F70u) { ++native_calls; if (a[0] == 0x936a6a7fu) ++capture_calls; return 0; }
    fprintf(stderr, "unexpected guest %08x\n", fn); abort();
}
void f_0052AC70_orig(Cpu *c) {
    uint32_t end = c->ecx + 0x3c; M32(end) = M32(end + 4) = end;
    unsigned count = M32(c->ecx + 0xe4) == 1 ? 36 : 38;
    for (unsigned i = 0; i < count; ++i) { uint32_t d = galloc(0x120); M32(d + 0x11c) = i / 2; append(end, d); }
    M32(c->ecx + 0x44) = M32(end) - 4;
    c->esp += 4; c->ecx = 0; // the hook must preserve self across retail's ABI
}
int padbind_edit_set(int s) { return s; }
void padbind_controls_dump(const char *s) { (void)s; }
void padbind_drag_defaults(Cpu *c) { (void)c; }
#define STUB(name) void name(Cpu *c) { c->esp += 4; }
STUB(f_0052AF20_orig) STUB(f_0052AB90_orig) STUB(f_0051D4B0_orig)
STUB(f_0051D590_orig) STUB(f_0051D6E0_orig) STUB(f_0052A780_orig) STUB(f_0051D410)
void dispatch(Cpu *c, uint32_t at) { (void)c; (void)at; abort(); }
void bad_jump(Cpu *c, uint32_t at) { (void)c; (void)at; abort(); }
static Cpu cpu(void) { return (Cpu){.ecx = SCREEN, .esp = 0x900000}; }
static void message(uint32_t msg, int secondary) {
    Cpu c = cpu(); if (secondary) c.ecx += 0x2c;
    uint32_t args[] = {msg, 0, 0, 0};
    call_guest(&c, M32(M32(c.ecx) + 4), 4, args);
}
void trigopt_rumble_init(void);
unsigned trigopt_rumble_sample(uint8_t *bytes);
unsigned trigopt_rumble_nonzero(void);
unsigned trigopt_rumble_opens(void);
static void output(GCController *a) {
    Cpu c = cpu(); gs.tick_ns = clock_ns; effects_valid = 1;
    padfx_update((__bridge void *)a, 1, .8f, 0); drain();
    (void)c;
}
int main(int argc, char **argv) { @autoreleasepool {
    G = calloc(1, 0x1000000); CHECK(G != NULL);
    unsetenv("NFS_PAD_TRIGGERS");
    const char *mode = argc > 1 ? argv[1] : "default";
    if (!strcmp(mode, "saved-off")) strcpy(saved, "off");
    if (!strcmp(mode, "env-on")) { strcpy(saved, "off"); setenv("NFS_PAD_TRIGGERS", "on", 1); }
    if (!strcmp(mode, "env-off")) { strcpy(saved, "on"); setenv("NFS_PAD_TRIGGERS", "off", 1); }
    if (!strncmp(mode, "override-", 9)) setenv("NFS_PAD_TRIGGERS", mode + 9, 1);
    CHECK(argc == 3); pref_root = argv[2];
    if (saved[0]) disk_pref_set("controls.adaptive_triggers", saved);
    settings_init();
    int initial = strcmp(mode, "saved-off") && strcmp(mode, "env-off") && strcmp(mode, "override-Off") && strcmp(mode, "override-0");
    CHECK(adaptive.value == initial && triggers_on() == initial);
    CHECK(adaptive.hidden && !adaptive.restart && adaptive.def == 1);
    Setting *visible[8]; CHECK(settings_rows(SET_CONTROLS, visible, 8) == 0);
    M32(SCREEN) = 0x89d234; M32(0x89d238) = 0x00545F70;
    M32(SCREEN + 0x2c) = 0x89d228; M32(0x89d230) = 0x0052AB90;
    uint32_t end = SCREEN + 0x34; M32(end) = M32(end + 4) = end;
    M32(SCREEN + 0xe4) = 3;
    for (unsigned i = 0; i < 16; ++i) {
        uint32_t slot = SLOT_BASE + i * 0x40, label = LABEL_BASE + i / 2 * 0x100, value = VALUE_BASE + i * 0x100;
        M32(slot) = 0x89c23c; M32(0x89c240) = 0x0051D3A0;
        M32(slot + 0x14) = label; M32(slot + 0x18) = value; append(end, slot);
        M32(label + 0x2c) = label + 0x80; M32(value + 0x2c) = value + 0x80;
        MF32(label + 0x80 + 0x1c) = 100; MF32(value + 0x80 + 0x1c) = i % 2 ? 450 : 300;
        MF32(label + 0x80 + 0x38) = 1; M32(label + 0x6c) = 0x10;
    }
    Cpu c = cpu(); f_0052AC70(&c); CHECK(c.esp == 0x900004);
    CHECK(added == 2 && datum_count() == 40);
    c = cpu(); trigopt_controls(&c); CHECK(added == 2 && datum_count() == 40);
    CHECK(!strcmp(drawn_text[7], "Adaptive triggers"));
    CHECK(!strcmp(drawn_value[14], initial ? "On" : "Off"));
    CHECK(M32(LABEL_BASE + 0x700 + 0x74) == 192);
    CHECK(!(M32(LABEL_BASE + 0x700 + 0x6c) & 0x10));
    // All language IDs traverse the actual retail renderer; font metrics span
    // short labels through ten times the available width. No text wraps/overlaps.
    for (unsigned special = 0; special < 2; ++special) for (unsigned language = 0; language < 16; ++language) {
        M32(0x8f41c0) = language;
        for (unsigned i = 0; i < 8; ++i) M32(LABEL_BASE + i * 0x100 + 0x24) = special ? 0x9583aa1a : 0;
        c = cpu(); trigopt_controls(&c); draw_all(&c);
        float scale = special && (language == 8 || language == 9) ? 2 : 1;
        for (unsigned w = 1; w <= 1921; w += 120) {
            float actual = render_width(LABEL_BASE + 0x700, (float)w, language);
            CHECK(fabsf(actual - fminf((float)w * scale, 192)) < .001f);
        }
    }
    // Real outputs: padfx.m -> fake GC methods; feedback.c -> rumble.m -> fake HID.
    GCController *a = controller(3); fake_writes = 0;
    light_sw = player_sw = 0;
    M32(0x91F150) = 0x9000; strcpy(joy_name(0x9000), "Controller (DualSense)");
    bind(A_SHIFTDOWN, T_AXIS_HI, 2); bind(A_SHIFTUP, T_ROT_HI, 2);
    M32(0x9885e0) = 0x1000; M32(0x102c) = 3;
    trigopt_rumble_init();
    S.active = 1; S.out_l = .8f; S.out_r = .4f; last_tick = clock_ns;
    output(a);
    unsigned on_writes = fake_writes;
    CHECK((a.extendedGamepad.leftTrigger.mode != T_OFF) == adaptive.value);
    uint8_t before[78] = {0}, after[78] = {0};
    CHECK(trigopt_rumble_sample(before) == 1 && before[3] == 102 && before[4] == 204);
    M32(SCREEN + 0x44) = M32(SCREEN + 0x40) - 4; // select final toggle column
    unsigned writes = pref_writes;
    message(0x936a6a7f, 0); drain();
    if (adaptive.locked) {
        CHECK(adaptive.value == initial && pref_writes == writes);
    } else {
        CHECK(adaptive.value == !initial && pref_writes == writes);

    }
    CHECK(S.out_l == .8f && S.out_r == .4f && effects_valid);
    unsigned clears = fake_writes - on_writes;
    fake_writes = 0; output(a);
    unsigned toggled_reports = fake_writes;
    CHECK(adaptive.value ? a.extendedGamepad.leftTrigger.mode == T_WEAPON : a.extendedGamepad.leftTrigger.mode == T_OFF);
    CHECK(trigopt_rumble_sample(after) == 2 && !memcmp(before, after, 48));
    CHECK(trigopt_rumble_nonzero() == 2 && trigopt_rumble_opens() == 0);
    CHECK(capture_calls == 0);
    if (!adaptive.locked) {
        CHECK(initial ? (on_writes == 2 && clears == 2 && toggled_reports == 0) : (on_writes == 0 && toggled_reports == 2));
        message(0x9120409e, 1); drain(); CHECK(adaptive.value == initial && pref_writes == writes);
        output(a);
        CHECK(adaptive.value ? a.extendedGamepad.leftTrigger.mode == T_WEAPON : a.extendedGamepad.leftTrigger.mode == T_OFF);
    }
    message(0x72619778, 0); CHECK(native_calls == 1); // native scrolling delegated
    M32(SCREEN + 0x44) = M32(SCREEN + 0x3c) - 4;
    message(0x936a6a7f, 0); CHECK(capture_calls == 1); // normal capture delegated
    // Keyboard / controller rebuilds replace data, retain exactly one added row.
    M32(SCREEN + 0xe4) = 1; c = cpu(); f_0052AC70(&c); CHECK(datum_count() == 38);
    M32(SCREEN + 0xe4) = 3; c = cpu(); f_0052AC70(&c); CHECK(datum_count() == 40);
    CHECK(added == 6);
    // Scroll the toggle out: localized label styling is restored on reused slots.
    uint32_t args[] = {M32(SCREEN + 0x3c) - 4, 0}; c = cpu(); c.ecx = SLOT_BASE + 14 * 0x40;
    call_guest(&c, M32(M32(c.ecx) + 4), 2, args);
    CHECK(M32(LABEL_BASE + 0x700 + 0x74) == 0 && M32(LABEL_BASE + 0x700 + 0x6c) == 0x10);
    CHECK(!(M32(LABEL_BASE + 0x700 + 0x1c) & 2));
    // A reopened screen gets fresh physical slots; callbacks remain bounded.
    for (unsigned i = 0; i < 16; ++i) M32(SLOT_BASE + i * 0x40) = 0x89c23c;
    c = cpu(); trigopt_controls(&c); CHECK(ncallbacks == 3);
    if (!adaptive.locked) {
        M32(SCREEN + 0x44) = M32(SCREEN + 0x40) - 4;
        message(0x936a6a7f, 0); CHECK(adaptive.value == !initial && pref_writes == writes);
        c = cpu(); f_0052A780(&c); CHECK(pref_writes == writes + 1);
        prefs_loaded = nprefs = 0;
        CHECK(!strcmp(app_pref(adaptive.id), initial ? "off" : "on"));
    }
    printf("trigopt %s: %d checks, %d failures; trigger reports initial=%u toggled=%u clear=%u; rumble=1/1 identical, opens=%u\n", mode, checks, failures, on_writes, toggled_reports, clears, trigopt_rumble_opens());
    // Leak detection owns no fake device lifecycle; no real controller was opened.
    return failures ? 1 : 0;
} }
