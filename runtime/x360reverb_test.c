// Offline production DSP/loader harness; run x360reverb_test.py, never opens an audio device.
// Translated retail code and PE constants are supplied by the Python driver. Only the guest
// allocator/free and dispatch infrastructure are supplied here; the DSP and mixer run unchanged.
// Proven 2026-10-25: 12 payload selections, 44.1/48 kHz sample equality and PC fallback PASS.
// Broken small gain / tunnel labels / omitted quad: exit 1, 2400000 / 1440000 / 2400000 mismatched samples.
#define X360AUDIO_TEST
#include "x360audio.c"
#include "runtime.h"

uint8_t *G;
const char *game_dir;
int diag_on;
static unsigned failures;
static uint32_t heap;
#define CHECK(x) do { if (!(x)) { if (failures < 8) fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)
const char *app_pref(const char *key) { return !strcmp(key, "audio.x360_reverb") ? getenv("REVERB_TEST_PREF") : NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); putchar('\n'); }
int extratrax_open(int mus, const X360MusicHandle *a, const X360MusicHandle *b, X360MusicHandle *out) {
    (void)mus; (void)a; (void)b; (void)out; return 0;
}
void f_0081E9E7(Cpu *c) {
    uint32_t size = M32(c->esp + 4);
    CHECK(size < 0x1000000 && heap < 0x4000000 - size - 32);
    if (failures) exit(1);
    c->eax = heap; heap = (heap + size + 31) & ~31u; c->esp += 4;
}
void f_0081EAE9(Cpu *c) { c->esp += 4; } // arena is discarded after each process
void bad_jump(Cpu *c, uint32_t at) { (void)c; fprintf(stderr, "bad jump %08X\n", at); exit(1); }
void f_00821537(Cpu *c);
void f_008215FC(Cpu *c);
void f_00820DA3(Cpu *c);
static Cpu cpu(void) { Cpu c = {0}; c.esp = 0xF00000; c.cw = 0x37F; return c; }
static void write_file(const char *path, const void *data, size_t n) {
    FILE *f = fopen(path, "wb"); CHECK(f);
    if (!f) exit(1);
    CHECK(fwrite(data, 1, n, f) == n); CHECK(!fclose(f));
}
static void setting_test(int expected) {
    settings_init();
    Setting *s = settings_find("audio.x360_reverb"); CHECK(s);
    CHECK(s->category == SET_AUDIO && s->order == 65 && s->restart && s->def == 1);
    CHECK(s->boot_value == expected && s->value == expected);
    CHECK(!strcmp(s->label, "Reverb") && !strcmp(s->env, "NFS_X360_REVERB"));
    char name[32];
    CHECK(!strcmp(settings_value_name(s, 0, name, sizeof name), "PC"));
    CHECK(!strcmp(settings_value_name(s, 1, name, sizeof name), "X360"));
    settings_look_apply(PRESET_PC); CHECK(s->value == expected);
    settings_look_apply(PRESET_X360); CHECK(s->value == expected);
    if (!s->locked) { settings_set(s, !expected); CHECK(s->boot_value == expected && s->value == !expected); }
}
static void merge(int argc, char **argv) {
    CHECK(argc == 7); if (failures) return;
    long pn = 0, xn = 0; size_t n = 0;
    uint8_t *pc = slurp(argv[2], &pn), *x = slurp(argv[3], &xn); CHECK(pc && x);
    if (failures) exit(1);
    diag_on = 1;
    uint8_t *out = x360audio_merge_memory(pc, pn, x, xn, atoi(argv[5]), atoi(argv[6]), &n);
    CHECK(out && n && memory_valid(out, n));
    if (out) write_file(argv[4], out, n);
    free(pc); free(x); free(out);
}
static void serve(int argc, char **argv) {
    CHECK(argc == 7); if (failures) return;
    game_dir = argv[2]; settings_init(); diag_on = 1;
    char pcpath[4096]; snprintf(pcpath, sizeof pcpath, "%s/GLOBAL/InGameMemoryFile.bin", game_dir);
    int fd = x360audio_open("GLOBAL\\InGameMemoryFile.bin", pcpath);
    CHECK((fd >= 0) == atoi(argv[4]));
    if (fd >= 0) {
        off_t n = lseek(fd, 0, SEEK_END); lseek(fd, 0, SEEK_SET);
        uint8_t *b = malloc(n); CHECK(read(fd, b, n) == n); close(fd);
        long want_n = 0; uint8_t *want = slurp(argv[3], &want_n);
        CHECK(want && n == want_n && !memcmp(want, b, n));
        free(want); free(b);
    }
    CHECK(s_x360_audio.boot_value == atoi(argv[5])); CHECK(s_x360_reverb.boot_value == atoi(argv[6]));
}
static void render(int argc, char **argv) {
    CHECK(argc == 7); if (failures) return;
    unsigned rate = (unsigned)atoi(argv[5]), channels = (unsigned)atoi(argv[6]);
    CHECK((rate == 44100 || rate == 48000) && channels >= 1 && channels <= 6);
    G = calloc(1, 0x4000000); CHECK(G); heap = 0x1000000;
    FILE *f = fopen(argv[2], "rb"); CHECK(f);
    CHECK(fread(G, 1, 0x1000000, f) == 0x1000000); fclose(f);
    long size = 0; uint8_t *fx = slurp(argv[3], &size); CHECK(fx && reverb_valid(fx, size));
    if (failures) exit(1);
    memcpy(G + 0xB00000, fx, size); free(fx);
    M32(0x90BD18) = 1; M8(0x9C2319) = channels;
    M32(0x9C23F0) = 0xD00000; M32(0x9C23F4) = 0xD01000;
    M32(0x9C2524) = 0x8251F0; // retail scalar floating-point accumulate
    for (unsigned ch = 0; ch < channels; ++ch) M32(0x9C2410 + ch * 4) = 0xC00000 + ch * 0x1000;
    Cpu c = cpu(); f_00821537(&c); CHECK(c.esp == 0xF00004);
    c = cpu(); M32(c.esp + 4) = rate; M32(c.esp + 8) = 0; M32(c.esp + 12) = 0xB00000;
    f_008215FC(&c); CHECK(c.esp == 0xF00004 && c.eax);
    uint32_t bus = c.eax, input = M32(M32(0x9C22F4));
    printf("render rate=%u channels=%u selected=%u nodes=%u\n", rate, channels, M32(bus + 0x48), be32(G + bus + 12 + (M32(bus + 0x48) - 1) * 4));
    f = fopen(argv[4], "wb"); CHECK(f); if (!f) exit(1);
    float interleaved[128 * 6];
    for (unsigned at = 0; at < rate * 10; at += 128) {
        unsigned frames = rate * 10 - at; if (frames > 128) frames = 128;
        memset(G + input, 0, 1024); if (!at) MF32(input) = 0.25f;
        for (unsigned ch = 0; ch < channels; ++ch) memset(G + M32(0x9C2410 + ch * 4), 0, 1024);
        M32(0x9C22F8) = 0; // active input bus (retail idle cutoff must not truncate a ten-second response)
        c = cpu(); M32(c.esp + 4) = frames; f_00820DA3(&c); CHECK(c.esp == 0xF00004);
        for (unsigned i = 0; i < frames; ++i) for (unsigned ch = 0; ch < channels; ++ch) {
            float v = MF32(M32(0x9C2410 + ch * 4) + i * 4);
            CHECK(isfinite(v) && fabsf(v) <= 4);
            interleaved[i * channels + ch] = v;
        }
        CHECK(fwrite(interleaved, sizeof(float) * channels, frames, f) == frames);
    }
    CHECK(!fclose(f)); free(G);
}
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "settings") && argc == 3) setting_test(atoi(argv[2]));
    else if (!strcmp(argv[1], "merge")) merge(argc, argv);
    else if (!strcmp(argv[1], "serve")) serve(argc, argv);
    else if (!strcmp(argv[1], "render")) render(argc, argv);
    else return 2;
    if (failures) fprintf(stderr, "%u failures\n", failures);
    return failures ? 1 : 0;
}
