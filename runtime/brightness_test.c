// Offline only: real FE hooks/settings, fake guest calls and renderer; no game entry point.
// brightness_test.py also compiles a broken vtable guard: the independence checks must fail.
#include <assert.h>
#include <stdarg.h>
#include <unistd.h>
#ifndef FE_CONSOLE_SOURCE
#define FE_CONSOLE_SOURCE "fe_console.c"
#endif
#include FE_CONSOLE_SOURCE

uint8_t *G;
int diag_on;
static unsigned checks, writes, phase_writes, init_original, act_original, gamma_writes;
static uint32_t heap = 0x100000, added[8];
static int nadded;
static uint16_t last_ramp[768];
static const char *pref_path;
static char saved[64];
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
const char *app_pref(const char *key) {
    assert(!strcmp(key, "graphics.brightness"));
    FILE *f = fopen(pref_path, "r");
    saved[0] = 0;
    if (f) { assert(fscanf(f, "graphics.brightness=%63s", saved) == 1); fclose(f); }
    return saved;
}
void app_pref_set(const char *key, const char *value) {
    assert(!strcmp(key, "graphics.brightness"));
    FILE *f = fopen(pref_path, "w");
    assert(f);
    fprintf(f, "%s=%s\n", key, value);
    fclose(f);
    ++writes;
}
void render_set_gamma(const uint16_t ramp[768]) { memcpy(last_ramp, ramp, sizeof last_ramp); ++gamma_writes; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
int x360_post_chain_on(void) { return 0; }
int prompts_pad_lost(void) { return 0; }
uint32_t gstr(const char *s) { (void)s; abort(); }
#define UNUSED_ORIG(name) void name(Cpu *c) { (void)c; }
UNUSED_ORIG(f_00573960_orig)
int padguest_connection(Cpu *c) { (void)c; return 0; }   // NFS_PADS=2 only; one pad here
UNUSED_ORIG(f_00588570_orig)
UNUSED_ORIG(f_006287D0_orig)
UNUSED_ORIG(f_0056BB80_orig)
UNUSED_ORIG(f_00545750_orig)
UNUSED_ORIG(f_00529280_orig)
UNUSED_ORIG(f_00510450_orig)
void f_0050F9E0_orig(Cpu *c) { ++init_original; c->esp += 4; }
void f_0051B870_orig(Cpu *c) { ++act_original; c->esp += 12; }
void f_00652AD0(Cpu *c) { c->eax = heap; heap += (M32(c->esp + 4) + 15) & ~15; }
void f_00589540(Cpu *c) { memset(G + c->ecx, 0, 0x9C); }
void f_0056CB20(Cpu *c) {
    CHECK(M32(c->esp + 4) == 0);
    CHECK(M32(c->esp + 8) == 0x3F800000u);
    CHECK(M32(c->esp + 12) == 0x3C23D70Au);
    CHECK(M32(c->esp + 20) == 0x42C80000u);
    M32(c->ecx + 0x28) = M32(c->esp + 16);
}
void f_0056CD20(Cpu *c) { M32(c->ecx + 0x28) = M32(c->esp + 4); }
void f_00574BC0(Cpu *c) { MF32(c->ecx + 0x84) += 0.01f; }
void f_007696B0(Cpu *c) { M32(tod.object + 8) = M32(c->esp + 4); ++phase_writes; }
void f_00588860(Cpu *c) {
    uint32_t widget = M32(c->esp + 4);
    added[nadded++] = widget;
    Cpu t = *c; t.ecx = widget;
    f_0050F9E0(&t);
    c->eax = M32(c->ecx + 0x104)++;
}
void f_005295F0_orig(Cpu *c) { M32(c->ecx + 0x144) = 1; M32(c->ecx + 0x104) = 3; nadded = 0; }
static Cpu frame(uint32_t self) { return (Cpu){.ecx = self, .esp = 0x80000}; }
static void setup(uint32_t screen) { Cpu c = frame(screen); f_005295F0(&c); }
static void move(uint32_t widget) {
    Cpu c = frame(widget);
    M32(c.esp + 8) = 1;
    f_0051B870(&c);
    CHECK(c.esp == 0x8000C);
}
static void notify(uint32_t screen, uint32_t message) {
    Cpu c = frame(screen); M32(c.esp + 4) = message; f_00545750(&c);
}
static void destroy(uint32_t screen) { Cpu c = frame(screen); f_00529280(&c); }
static void open_screen(uint32_t screen) {
    M32(screen + 0x144) = 0; M8(screen + 0x124) = 1; M8(screen + 0x154) = 0;
    setup(screen);
    CHECK(nadded == 2);
    CHECK(M32(added[0]) == tod_vtable);
    CHECK(M32(added[1]) == brightness_vtable);
    CHECK(tod_vtable != brightness_vtable);
    CHECK(M32(tod_vtable + 12) == 0x0051B700);
    CHECK(M32(brightness_vtable + 12) == 0x0051B750);
}
int main(int argc, char **argv) {
    assert(argc == 2); pref_path = argv[1]; unlink(pref_path);
    G = calloc(1, 0x1000000); assert(G);
    CHECK(fabs(brightness_exponent(0) - X360E_BRIGHT_HIGH) < 1e-12);   // endpoints: the generated constants
    CHECK(fabs(brightness_exponent(1) - X360E_BRIGHT_LOW) < 1e-12);
    CHECK(X360E_BRIGHT_LOW < 1.0 && 1.0 < X360E_BRIGHT_HIGH);              // the default (identity) lies inside the slider
    CHECK(fabs(brightness_exponent(BRIGHTNESS_IDENTITY) - 1) < 1e-8);
    uint16_t ramp[768];
    brightness_ramp(BRIGHTNESS_IDENTITY, ramp);
    for (int i = 0; i < 768; ++i) CHECK(abs(ramp[i] - (i & 255) * 257) <= 1);
    for (int s = 0; s <= 100; ++s) {
        brightness_ramp(s / 100.0f, ramp);
        CHECK(ramp[0] == 0 && ramp[255] == 65535);
        for (int i = 1; i < 256; ++i) CHECK(ramp[i] >= ramp[i - 1] && ramp[i] == ramp[256+i] && ramp[i] == ramp[512+i]);
    }
    settings_init();
    CHECK(settings_find("graphics.brightness") == &s_brightness);
    brightness_init();
    CHECK(brightness_get() == BRIGHTNESS_IDENTITY && writes == 0);
    for (int i = 0; i < 768; ++i) CHECK(last_ramp[i] == (i & 255) * 257);
    // F10 percentage: the identity percentage is the default, the special case and what every selection reads back
    CHECK(s_brightness.def == BRIGHTNESS_IDENTITY_PCT);
    CHECK(strstr(s_brightness.help, "default") != NULL);
    for (int percent = 0; percent <= 100; ++percent) {
        brightness_percent_apply(percent);
        CHECK(brightness_percent() == percent);
        CHECK((percent == BRIGHTNESS_IDENTITY_PCT) == (brightness_get() == BRIGHTNESS_IDENTITY));
    }
    brightness_preview(BRIGHTNESS_IDENTITY); brightness_save(); brightness_loaded = 0;
    float values[] = {0, 1, BRIGHTNESS_IDENTITY, 0.532448947f};
    for (unsigned i = 0; i < sizeof values / sizeof *values; ++i) {
        brightness_preview(values[i]); brightness_save(); brightness_loaded = 0;
        CHECK(brightness_get() == values[i]);
        brightness_init(); brightness_ramp(values[i], ramp);
        CHECK(!memcmp(last_ramp, ramp, sizeof ramp));
    }
    app_pref_set("graphics.brightness", "nan"); brightness_loaded = 0;
    CHECK(brightness_get() == BRIGHTNESS_IDENTITY);
    CHECK(settings_set(&s_brightness, 70));
    brightness_loaded = 0; CHECK(brightness_get() == 0.7f && settings_get("graphics.brightness") == 70);
    settings_reset(&s_brightness); brightness_loaded = 0;
    CHECK(brightness_get() == BRIGHTNESS_IDENTITY);

    uint32_t screen = 0x20000, object = 0x30000;
    M32(0x009B392C) = object; MF32(object + 8) = 0.43f;
    unsigned before_writes = writes, before_gamma = gamma_writes;
    open_screen(screen);
    CHECK(bright.value == BRIGHTNESS_IDENTITY && MF32(tod.widget + 0x84) == 0.43f);
    CHECK(gamma_writes == before_gamma && writes == before_writes && phase_writes == 0);
    move(bright.widget);
    CHECK(bright.changed && !tod.changed && phase_writes == 0);
    CHECK(brightness_get() == bright.value && writes == before_writes);
    float new_brightness = brightness_get();
    move(tod.widget);
    CHECK(phase_writes == 1 && tod.changed && brightness_get() == new_brightness);
    // An unrelated native widget must use the original functions, regardless of row backups.
    uint32_t native = 0x40000; M32(native) = 0x0089BB40;
    Cpu c = frame(native); f_0050F9E0(&c); move(native);
    CHECK(init_original == 1 && act_original == 1 && phase_writes == 1);
    CHECK(brightness_get() == new_brightness);
    M8(screen + 0x154) = 1; setup(screen);
    CHECK(nadded == 0 && !bright.widget && !tod.widget);
    M8(screen + 0x154) = 0; setup(screen);
    CHECK(nadded == 2 && MF32(bright.widget + 0x84) == new_brightness);
    notify(screen, 0x911AB364); destroy(screen);
    CHECK(brightness_get() == BRIGHTNESS_IDENTITY && MF32(object + 8) == 0.43f);
    CHECK(writes == before_writes);
    open_screen(screen); move(bright.widget); new_brightness = brightness_get();
    notify(screen, 0x406415E3); destroy(screen);
    CHECK(writes == before_writes + 1);
    brightness_loaded = 0; CHECK(brightness_get() == new_brightness);
    open_screen(screen); move(bright.widget);
    c = frame(screen); f_00510450(&c); destroy(screen);
    CHECK(brightness_get() == new_brightness && writes == before_writes + 1);
    open_screen(screen); move(bright.widget); destroy(screen);
    CHECK(brightness_get() == new_brightness && writes == before_writes + 1);
    open_screen(screen); destroy(screen);
    CHECK(brightness_get() == new_brightness && writes == before_writes + 1);
    printf("PASS %u assertions: ramps, pref file round-trip/boot, F10/reset, independent vtables, Back/Accept/rejection, Advanced, untouched\n", checks);
    unlink(pref_path); free(G);
    return 0;
}
