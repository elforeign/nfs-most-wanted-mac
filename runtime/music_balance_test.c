// PCM proof uses the translated retail map writer, 16-frame mixer and int16 converter.
// Original -> fixed measurements and deliberately broken controls: music_balance_test.py header.
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "settings.h"
#ifndef MUSIC_BALANCE_SOURCE
#define MUSIC_BALANCE_SOURCE "music_balance.c"
#endif
#include MUSIC_BALANCE_SOURCE

uint8_t *G;
static int checks, failures;
static double max_db_error, max_share_error;
static size_t protected_bytes;
static unsigned angle_checks;
#define CHECK(x) do { ++checks; if (!(x)) { if (failures < 8) printf("FAIL %d: %s\n", __LINE__, #x); ++failures; } } while (0)
int diag_on;
const char *game_dir;
static char saved_values[2][16];
static int pref_writes;
#ifdef APP_PREF_SOURCE
#include APP_PREF_SOURCE
#else
const char *app_pref(const char *key) {
    for (int i = 0; i < 2; ++i) if (!strcmp(key, balance_settings[i]->id)) return saved_values[i];
    return NULL;
}
void app_pref_set(const char *key, const char *value) {
    for (int i = 0; i < 2; ++i) if (!strcmp(key, balance_settings[i]->id)) {
        snprintf(saved_values[i], sizeof saved_values[i], "%s", value); ++pref_writes;
    }
}
#endif
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
#ifndef APP_PREF_SOURCE
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
#endif
static uint32_t heap = 0x1800000;
uint32_t galloc(uint32_t n) { uint32_t at = heap; heap += n; return at; }
uint32_t gstr(const char *s) { uint32_t at = galloc((uint32_t)strlen(s) + 1); strcpy((char *)G + at, s); return at; }
uint32_t host_fn(HostFn fn, const char *name) { (void)fn; (void)name; return 0xf1000000u; }
static int native_added, native_original;
static char drawn[2][64];
void f_00529310_orig(Cpu *c) { ++native_original; c->esp += 4; }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    (void)n;
    if (fn == 0x00652AD0u) return galloc(args[0]);
    if (fn == 0x0051A7B0u) {
        uint32_t vt = galloc(64); M32(c->ecx) = vt;
        M32(vt + 8) = 0x0050F600u; M32(vt + 12) = 0x0051B2D0u;
    } else if (fn == 0x00588570u) {
        uint32_t w = args[0];
        M32(w + 0x2c) = galloc(128); M32(w + 0x30) = galloc(128);
        ++native_added;
        Cpu q = *c; q.ecx = w; balance_draw(&q);
    } else if (fn == 0x0050D880u) {
        CHECK(M32(c->ecx + 0x1c) & 2u);
        int row = c->ecx == M32(audio_options.widget[0] + 0x30) ? 0 : 1;
        snprintf(drawn[row], sizeof drawn[row], "%s", (char *)G + args[0]);
    }
    return 0;
}
void f_004E4120(Cpu *c);
void f_0082279F(Cpu *c);
void f_00825957_orig(Cpu *c);
void dispatch(Cpu *c, uint32_t at) { (void)c; (void)at; abort(); }
void bad_jump(Cpu *c, uint32_t at) { (void)c; (void)at; abort(); }
void guest_divide_error(Cpu *c, uint32_t at) { (void)c; (void)at; abort(); }

enum { MAP = 0x1000000, BASE = 0x1010000, PROC = 0x1020000, SHARED = 0x1030000,
       UNIQUE = 0x1040000, OUT = 0x1050000, SRC = 0x1060000, PLANE = 0x1070000,
       PCM = 0x1080000, CONVERTER = 0x1090000, FRAMES = 256, CHANNELS = 6 };
static Cpu invoke(void (*fn)(Cpu *), uint32_t ecx, const uint32_t *args, int n) {
    Cpu c = {.esp = 0x1f00000, .ecx = ecx, .cw = 0x37f, .mxcsr = 0x1f80};
    for (int i = n - 1; i >= 0; --i) { c.esp -= 4; M32(c.esp) = args[i]; }
    uint32_t sp = c.esp;
    c.esp -= 4; M32(c.esp) = 0;
    fn(&c);
    CHECK(c.esp == sp);
    return c;
}
static void setup(uint32_t id, uint32_t format, unsigned n, const uint32_t *out, int db, unsigned three_d) {
    memset(G + MAP, 0, 0xa0000);
    M32(MAP + 0x124) = BASE; M32(MAP + 0x1d4) = 16;
    M32(MAP + 0x16c) = PROC; M32(MAP + 0x188) = 2;
    // Duplicate writers are deliberate: the final halfword must receive one trim.
    for (int i = 0; i < 2; ++i) { M32(PROC + i * 8) = SHARED; M32(PROC + i * 8 + 4) = UNIQUE; }
    M32(SHARED + 12) = OUT;
    M32(SHARED + 8) = three_d << 16;
    M32(UNIQUE) = id; M32(UNIQUE + 8) = (uint32_t)db; M32(UNIQUE + 16) = BASE;
    M32(UNIQUE + 12) = OUT + 0x1000;
    for (unsigned j = 0; j < three_d; ++j) {
        uint32_t proc = OUT + 0x2000 + j * 8, spatial = OUT + 0x3000 + j * 0x20;
        M32(OUT + 0x1000 + j * 4) = proc; M32(proc + 4) = spatial;
        // Test low and high full-circle angle words through the retail bypass.
        M32(spatial + 8) = db > 0 ? 9000 : db == -700 ? 28000 : 50000;
        M32(spatial + 12) = (uint32_t)(-350 * (int)(j + 1));
        M32(spatial + 20) = 120 * (j + 1);
    }
    for (int i = 0; i < 30; ++i) M16(BASE + i * 2) = (uint16_t)(1000 + i * 71);
    M32(BASE + 60) = 0xa5a50001;
    M32(OUT) = format;
    for (unsigned i = 0; i < n; ++i) M32(OUT + 4 + i * 4) = out[i];
}
static unsigned trim_step(int cat, int e, int f) { return cat == 1 ? e + 1 : cat == 2 ? f + 1 : 0; }
static double multiplier(unsigned step) { return step ? pow(10.0, -(double)(step + 1) / 20.0) : 1; }

static void records(FILE *file) {
    uint32_t header[5], out[15];
    unsigned records = 0;
    while (fread(header, sizeof header, 1, file) == 1) {
        uint32_t id = header[0], cat = header[1], format = header[2], n = header[3];
        unsigned three_d = header[4];
        if (n > 15 || fread(out, 4, n, file) != n) abort();
        ++records;
        CHECK(category(id) == (int)cat);
        for (int e = 0; e < 3; ++e) for (int f = 0; f < 3; ++f) {
            // Instances must not alter classification; levels include positive
            // authored gain (clamped by retail), ordinary gain and deep ducking.
            for (int state = 0; state < 3; ++state) {
                int db = state == 0 ? 300 : state == 1 ? -700 : -1800;
                uint32_t instance_id = id | (uint32_t)(state * 7) << 11;
                uint8_t original[64];
                setup(instance_id, format, n, out, db, three_d);
                Cpu a = invoke(f_004E9B30_orig, MAP, NULL, 0);
                memcpy(original, G + BASE, sizeof original);
                setup(instance_id, format, n, out, db, three_d);
                engine.value = e; effects.value = f;
                Cpu b = invoke(f_004E9B30, MAP, NULL, 0);
                CHECK(!memcmp(&a, &b, sizeof a));
                uint32_t mask = 0, angle_mask = 0;
                for (unsigned j = 0; j < n; ++j)
                    if ((out[j] & 0x80000000u) && ((out[j] >> 21) & 31) < three_d)
                        angle_mask |= 1u << ((out[j] >> 26) & 31);
                unsigned step = trim_step(cat, e, f);
                if (((format >> 24) & 15) == 0 && step)
                    for (unsigned j = 0; j < n; ++j) {
                        unsigned field = out[j] >> 26 & 31;
                        int copied_angle = (out[j] & 0x80000000u) && ((out[j] >> 21) & 31) < three_d;
                        if (field < 30 && !copied_angle) mask |= 1u << field;
                    }
                for (unsigned k = 0; k < 32; ++k) {
                    if (angle_mask & (1u << k)) ++angle_checks;
                    uint16_t before; memcpy(&before, original + k * 2, 2);
                    uint16_t want = before;
                    if ((mask & (1u << k)) && before <= 32767)
                        want = (uint16_t)lround(before * multiplier(step));
                    if (M16(BASE + k * 2) != want && failures < 8)
                        printf("record %u id %08x fmt %08x pair %d/%d db %d field %u: before %u want %u got %u\n",
                            records, id, format, e, f, db, k, before, want, M16(BASE + k * 2));
                    CHECK(M16(BASE + k * 2) == want);
                }
            }
        }
    }
    CHECK(feof(file));
    printf("decoded map records: %u; independent step pairs: 9; levels: 3; duplicate writes: 2; exact 3D angle checks: %u\n",
        records, angle_checks);
}
static uint32_t bits(float v) { uint32_t b; memcpy(&b, &v, 4); return b; }
static void render(const uint16_t volumes[CHANNELS], int16_t pcm[FRAMES * CHANNELS], int extra) {
    for (unsigned ch = 0; ch < CHANNELS; ++ch) {
        memset(G + PLANE, 0, FRAMES * 4);
        for (unsigned i = 0; i < FRAMES; ++i)
            MF32(SRC + i * 4) = (float)(7000 * sin(2 * 3.141592653589793 * (i * 7.0 / FRAMES)));
        float g = volumes[ch] / 32767.0f;
        for (unsigned i = 0; i < FRAMES; i += 16) {
            uint32_t args[] = {bits(g), bits(g), SRC + i * 4, PLANE + i * 4};
            invoke(f_0082279F, 0, args, 4);
            if (extra) { // A second, protected music voice in the same master plane.
                uint32_t music[] = {bits(0.1f), bits(0.1f), SRC + i * 4, PLANE + i * 4};
                invoke(f_0082279F, 0, music, 4);
            }
        }
        uint32_t convert[] = {CONVERTER, FRAMES, PLANE, PCM};
        invoke(f_00825957_orig, 0, convert, 4);
        for (unsigned i = 0; i < FRAMES; ++i) pcm[i * CHANNELS + ch] = (int16_t)M16(PCM + i * 2);
    }
}
static double power(const int16_t *pcm, int ch) {
    double sum = 0;
    for (unsigned i = 0; i < FRAMES; ++i) { double x = pcm[i * CHANNELS + ch]; sum += x * x; }
    return sum / FRAMES;
}
static void solos(void) {
    // Independent expected membership, including all protected streams and unknown IDs.
    const uint32_t ids[] = {0x40020010, 0x40020070, 0x40050000, 0x40040110,
                            0x40000010, 0x40010010, 0x60020010, 0x400d0010};
    const int cats[] = {1, 2, 2, 2, 0, 0, 0, 0};
    const char *names[] = {"engine", "skids", "traffic", "siren", "speech", "music/ambience", "controller", "unknown"};
    uint32_t outputs[CHANNELS];
    for (unsigned ch = 0; ch < CHANNELS; ++ch) outputs[ch] = (ch << 26) | ((uint16_t)(-120 * (int)ch));
    int16_t original[FRAMES * CHANNELS], actual[FRAMES * CHANNELS];
    for (unsigned source = 0; source < sizeof ids / sizeof *ids; ++source) {
        uint16_t reference[CHANNELS], current[CHANNELS];
        setup(ids[source], CHANNELS, CHANNELS, outputs, -400, 0);
        invoke(f_004E9B30_orig, MAP, NULL, 0);
        memcpy(reference, G + BASE, sizeof reference);
        render(reference, original, 0);
        for (int e = 0; e < 3; ++e) for (int f = 0; f < 3; ++f) {
            setup(ids[source], CHANNELS, CHANNELS, outputs, -400, 0);
            engine.value = e; effects.value = f;
            invoke(f_004E9B30, MAP, NULL, 0);
            memcpy(current, G + BASE, sizeof current);
            render(current, actual, 0);
            unsigned step = trim_step(cats[source], e, f);
            double total_a = 0, total_b = 0;
            for (unsigned ch = 0; ch < CHANNELS; ++ch) {
                double pa = power(original, ch), pb = power(actual, ch);
                double db = 10 * log10(pb / pa);
                double error = fabs(db - (step ? -(int)(step + 1) : 0));
                if (error > max_db_error) max_db_error = error;
                CHECK(error < 0.006);
                total_a += pa; total_b += pb;
            }
            for (unsigned ch = 0; ch < CHANNELS; ++ch) {
                double error = fabs(power(original, ch) / total_a - power(actual, ch) / total_b);
                if (error > max_share_error) max_share_error = error;
                CHECK(error < 0.00008);
            }
            if (!step) {
                CHECK(!memcmp(original, actual, sizeof actual));
                if (!cats[source]) protected_bytes += sizeof actual;
            }
            if (e == 2 && f == 2) printf("solo %s: original 0.000000 -> fixed %.6f dB; PCM bytes %zu\n",
                names[source], 10 * log10(total_b / total_a), sizeof actual);
            if (source == 0 && f == 0)
                printf("engine step %d dB: %.6f dB in PCM\n", e + 2, 10 * log10(total_b / total_a));

        }
    }
}
static void defaults_pcm(void) {
    const uint32_t ids[] = {0x40020010, 0x40020070};
    uint32_t outputs[CHANNELS];
    for (unsigned ch = 0; ch < CHANNELS; ++ch) outputs[ch] = ch << 26;
    for (int source = 0; source < 2; ++source) {
        uint16_t levels[CHANNELS];
        int16_t off[FRAMES * CHANNELS], actual[FRAMES * CHANNELS];
        setup(ids[source], CHANNELS, CHANNELS, outputs, -400, 0);
        invoke(f_004E9B30_orig, MAP, NULL, 0);
        memcpy(levels, G + BASE, sizeof levels); render(levels, off, 0);
        setup(ids[source], CHANNELS, CHANNELS, outputs, -400, 0);
        invoke(f_004E9B30, MAP, NULL, 0);
        memcpy(levels, G + BASE, sizeof levels); render(levels, actual, 0);
        double a = 0, b = 0;
        for (unsigned ch = 0; ch < CHANNELS; ++ch) { a += power(off, ch); b += power(actual, ch); }
        double db = 10 * log10(b / a);
        CHECK(levels[0] == 16404);
        CHECK(memcmp(off, actual, sizeof off) != 0);
        CHECK(fabs(db + 2) < 0.006);
        printf("default %s: source=%08x Q15=%u PCM=%.6f dB versus Off\n",
               source ? "effects" : "engine", ids[source], levels[0], db);
    }
}
static Cpu native_frame(uint32_t self) { Cpu c = {.ecx = self, .esp = 0x1f00000}; return c; }
static void native_message(uint32_t screen, uint32_t message) {
    Cpu c = native_frame(screen); M32(c.esp + 4) = message; music_balance_options(&c, 1);
}
static void native_step(int row, uint32_t message) {
    Cpu c = native_frame(audio_options.widget[row]); M32(c.esp + 8) = message;
    uint32_t sp = c.esp; balance_act(&c); CHECK(c.esp == sp + 12);
}
static void native_options(void) {
    uint32_t screen = 0x1700000;
    Cpu c = native_frame(screen); f_00529310(&c);
    CHECK(native_original == 1 && native_added == 2);
    CHECK(!strcmp(drawn[0], "-2 dB") && !strcmp(drawn[1], "-2 dB"));
    for (int row = 0; row < 2; ++row) for (int value = 1; value < 4; ++value) {
        native_step(row, 0xb5971bf1u);
        CHECK(balance_settings[row]->value == value % 3);
        CHECK(!strcmp(drawn[row], trims[value % 3]));
    }
    native_step(0, 0x9120409eu); CHECK(engine.value == 2 && effects.value == 0);
    CHECK(pref_writes == 0);
    native_message(screen, 0x911ab364u); CHECK(engine.value == 0 && effects.value == 0);
    native_step(1, 0xb5971bf1u);
    native_message(screen, 0x406415e3u);
    CHECK(pref_writes == 1 && !strcmp(saved_values[1], "3"));
    c = native_frame(screen); music_balance_options(&c, 2); CHECK(effects.value == 1);
    c = native_frame(screen); f_00529310(&c);
    native_step(1, 0xb5971bf1u);
    music_balance_options(&c, 0); CHECK(effects.value == 1);
    native_step(0, 0xb5971bf1u);
    music_balance_options(&c, 2); CHECK(engine.value == 0 && effects.value == 1);
    // Programmatic edits rebase cancellation; an environment lock prevents native edits.
    c = native_frame(screen); f_00529310(&c);
    settings_set(&engine, 2); native_step(0, 0xb5971bf1u);
    native_message(screen, 0x911ab364u); CHECK(engine.value == 2);
    engine.locked = 1; native_step(0, 0xb5971bf1u); CHECK(engine.value == 2); engine.locked = 0;
    music_balance_options(&c, 2);
}
int main(int argc, char **argv) {
#ifdef APP_PREF_SOURCE
    if (argc == 3 && (!strcmp(argv[1], "migration") || !strcmp(argv[1], "migration-env"))) {
        int overrides = !strcmp(argv[1], "migration-env");
        if (!overrides) { unsetenv("NFS_MUSIC_ENGINE_TRIM"); unsetenv("NFS_MUSIC_EFFECTS_TRIM"); }
        settings_init();
        const char *retired[] = {"gameplay.junkman", "gameplay.maxperf_career", "graphics.decal_depth"};
        for (unsigned i = 0; i < sizeof retired / sizeof *retired; ++i) {
            CHECK(app_pref(retired[i]) != NULL);
            CHECK(settings_find(retired[i]) == NULL);
        }
        int expected_engine = atoi(argv[2]);
        CHECK(engine.value == expected_engine && effects.value == 0);
        CHECK(!effects.saved);
        CHECK(engine.locked == overrides && effects.locked == overrides);
        Setting *rows[8]; CHECK(settings_rows(SET_AUDIO, rows, 8) == 0);
        printf("tolerant profile load: %d checks, %d failures\n", checks, failures);
        return failures ? 1 : 0;
    }
#endif
    if (argc == 4) {
        int e = atoi(argv[2]), f = atoi(argv[3]);
        snprintf(saved_values[0], sizeof saved_values[0], "%s", values[e]);
        snprintf(saved_values[1], sizeof saved_values[1], "%s", values[f]);
        unsetenv("NFS_MUSIC_ENGINE_TRIM"); unsetenv("NFS_MUSIC_EFFECTS_TRIM");
        settings_init();
        CHECK(engine.value == e && effects.value == f && engine.saved && effects.saved);
        return failures ? 1 : 0;
    }
    if (argc != 3) return 2;
    unsetenv("NFS_MUSIC_ENGINE_TRIM"); unsetenv("NFS_MUSIC_EFFECTS_TRIM");
    settings_init();
    CHECK(settings_count() == 2 && engine.value == 0 && effects.value == 0);
    CHECK(engine.def == 0 && effects.def == 0);
    G = calloc(1, 0x2000000);
    FILE *image = fopen(argv[1], "rb"), *fixture = fopen(argv[2], "rb");
    if (!G || !image || !fixture) return 2;
    CHECK(fread(G, 1, 0x1000000, image) == 0x1000000); fclose(image);
    defaults_pcm();
    native_options();
    records(fixture); fclose(fixture);
    solos();
    printf("protected PCM exact bytes %zu; max dB error %.9f; max channel-power share error %.9f\n",
        protected_bytes, max_db_error, max_share_error);
    printf("music_balance: %d checks, %d failures\n", checks, failures);
    free(G);
    return failures ? 1 : 0;
}
