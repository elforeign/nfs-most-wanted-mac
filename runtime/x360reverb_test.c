// Offline production DSP/loader harness; run x360reverb_test.py, never opens an audio device.
// Translated retail code and PE constants are supplied by the Python driver. Only the guest
// allocator/free and dispatch infrastructure are supplied here; the DSP and mixer run unchanged.
// Proven 2026-10-25: 12 payload selections, 44.1/48 kHz sample equality and PC fallback PASS.
// Broken small gain / tunnel labels / omitted quad: exit 1, 2400000 / 1440000 / 2400000 mismatched samples.
// Environment swap, 48 kHz/6ch/1024 frames: Off PC 0.022690566 -> 0.003241766 (-16.901 dB),
// X360 -> 0.003852799 (-15.401 dB). On PC -> 0.022496880, X360 -> 0.022527421;
// fade relative to authored steady-level envelope PC -1.206..+0.149 dB, X360 -0.686..+0.277 dB.
// Fade-removed production mutation FAIL -16.933 dB; Off matches every retail sample;
// 100 alternating swaps per source return to two infrastructure allocations after Reset.
// Stopped-send regression: wet RMS 0 -> 0.021949668, identical to the active-tail oracle.
// DC fade endpoints, max sample step: 44.1k 0.000905618 -> 0.000053272 (steady 0.000000015);
// 48k 0.000830889 -> 0.000048876 (steady 0.000000060). Both broken controls fail.
// Crossfade default proof: original Off exits 1 with 2 failures (default and loaded value);
// On exits 0: 7 default/saved/environment cases pass, including saved Off and both overrides.
#define X360AUDIO_TEST
#include "x360audio.c"
#include "runtime.h"
#include "reverbfade.c"

uint8_t *G;
const char *game_dir;
int diag_on;
static unsigned failures;
static uint32_t heap;
static struct { uint32_t at, size; int live; } allocations[4096];
static unsigned allocation_count, live_allocations;
#define CHECK(x) do { if (!(x)) { if (failures < 8) fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)
const char *app_pref(const char *key) {
    if (!strcmp(key, "audio.reverb_crossfade")) return getenv("REVERB_TEST_FADE_PREF");
    return !strcmp(key, "audio.x360_reverb") ? getenv("REVERB_TEST_PREF") : NULL;
}
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); putchar('\n'); }
int extratrax_open(int mus, const X360MusicHandle *a, const X360MusicHandle *b, X360MusicHandle *out) {
    (void)mus; (void)a; (void)b; (void)out; return 0;
}
void f_0081E9E7(Cpu *c) {
    uint32_t size = M32(c->esp + 4);
    for (unsigned i = 0; i < allocation_count; ++i) if (!allocations[i].live && allocations[i].size >= size) {
        allocations[i].live = 1; ++live_allocations; c->eax = allocations[i].at; c->esp += 4; return;
    }
    CHECK(size < 0x1000000 && heap < 0x4000000 - size - 32);
    if (failures) exit(1);
    CHECK(allocation_count < 4096);
    allocations[allocation_count++] = (__typeof__(allocations[0])){heap, size, 1}; ++live_allocations;
    c->eax = heap; heap = (heap + size + 31) & ~31u; c->esp += 4;
}
void f_0081EAE9(Cpu *c) {
    uint32_t at = M32(c->esp + 4); int found = 0;
    for (unsigned i = 0; i < allocation_count; ++i) if (allocations[i].at == at && allocations[i].live) {
        allocations[i].live = 0; --live_allocations; memset(G + at, 0xDD, allocations[i].size); found = 1; break;
    }
    CHECK(found || !at); c->esp += 4;
}
static int mutex_depth;
void f_0081BCCB(Cpu *c) { ++mutex_depth; c->esp += 4; }
void f_0081BCD9(Cpu *c) { CHECK(mutex_depth > 0); --mutex_depth; c->esp += 4; }
void f_00820DA3(Cpu *c) { reverbfade_mix(c); }
void bad_jump(Cpu *c, uint32_t at) { (void)c; fprintf(stderr, "bad jump %08X\n", at); exit(1); }
void f_00821537(Cpu *c);
void f_008215FC(Cpu *c);
void f_0082175F(Cpu *c);
void f_0081DA87(Cpu *c);
void f_00820DA3(Cpu *c);
static Cpu cpu(void) { Cpu c = {0}; c.esp = 0xF00000; c.cw = 0x37F; return c; }
static void write_file(const char *path, const void *data, size_t n) {
    FILE *f = fopen(path, "wb"); CHECK(f);
    if (!f) exit(1);
    CHECK(fwrite(data, 1, n, f) == n); CHECK(!fclose(f));
}
static void setting_test(int expected, int fade_expected) {
    int fade_locked = !!getenv("NFS_REVERB_XFADE");
    settings_init();
    Setting *fade = settings_find("audio.reverb_crossfade"); CHECK(fade);
    CHECK(fade->category == SET_AUDIO && fade->order == 66 && fade->restart && fade->def == 1);
    CHECK(fade->boot_value == fade_expected && fade->value == fade_expected);
    CHECK(!strcmp(fade->env, "NFS_REVERB_XFADE") && !strcmp(fade->label, "Reverb crossfade"));
    CHECK(fade->locked == fade_locked);
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
static void swap_test(int argc, char **argv) {
    CHECK(argc == 8 || argc == 10); if (failures) return;
    const unsigned rate = argc == 10 ? atoi(argv[8]) : 48000, channels = argc == 10 ? atoi(argv[9]) : 6;
    CHECK((rate == 44100 || rate == 48000) && channels >= 1 && channels <= 6);
    int retail = !strcmp(argv[6], "retail") || !strcmp(argv[6], "idle_retail"), cycles = atoi(argv[7]);
    int rapid = !strcmp(argv[6], "rapid"), release = !strcmp(argv[6], "release");
    int stopped = !strcmp(argv[6], "idle") || !strcmp(argv[6], "idle_reference") || !strcmp(argv[6], "idle_retail");
    int dc = !strcmp(argv[6], "continuity");
    settings_init();
    G = calloc(1, 0x4000000); CHECK(G); heap = 0x1000000;
    FILE *f = fopen(argv[2], "rb"); CHECK(f);
    CHECK(fread(G, 1, 0x1000000, f) == 0x1000000); fclose(f);
    for (unsigned j = 0; j < 2; ++j) {
        long n = 0; uint8_t *fx = slurp(argv[3 + j], &n); CHECK(fx && reverb_valid(fx, n));
        memcpy(G + 0xB00000 + j * 0x1000, fx, n); free(fx);
    }
    M32(0x90BD18) = 1; M8(0x9C2319) = channels;
    M16(0x9C2046) = rate;
    M32(0x9C23F0) = 0xD00000; M32(0x9C23F4) = 0xD01000;
    M32(0x9C2524) = 0x8251F0;
    for (unsigned ch = 0; ch < channels; ++ch) M32(0x9C2410 + ch * 4) = 0xC00000 + ch * 0x1000;
    Cpu c = cpu(); f_00821537(&c); unsigned infrastructure_live = live_allocations;
    c = cpu(); M32(c.esp + 4) = rate; M32(c.esp + 8) = 0; M32(c.esp + 12) = 0xB00000;
    f_008215FC(&c); uint32_t node = c.eax, rng = 1;
    const uint32_t processor = 0xB10000;
    M32(0x914290) = processor; M32(processor) = 0x46584253;
    M32(processor + 0x28) = node; M32(processor + 0x24) = 0;
    unsigned baseline_live[2] = {0}, swaps = 0;
    uint32_t retired_node = 0;
    FILE *raw = fopen(argv[5], "wb"); CHECK(raw);
    unsigned interval = rapid ? 1 : 94, blocks = release ? 96 : 94 + interval * (unsigned)cycles;
    for (unsigned block = 0; block < blocks; ++block) {
        if (block >= 94 && (block - 94) % interval == 0) {
            retired_node = node;
            c = cpu(); c.ecx = processor; M32(c.esp) = 0x004DEC0D;
            if (retail) f_0081D93A_orig(&c); else f_0081D93A(&c);
            CHECK(c.esp == 0xF00004 && !mutex_depth);
            c = cpu(); c.ecx = processor; M32(c.esp + 4) = swaps % 2 ? 0xB00000 : 0xB01000;
            f_0081DA87(&c); node = M32(processor + 0x28); ++swaps;
            CHECK(M32(0x9C22F8) == 3000); // the real parser idles the bus
        }
        double sq = 0;
        for (unsigned sub = 0; sub < 8; ++sub) {
            uint32_t input = M32(M32(0x9C22F4));
            for (unsigned i = 0; i < 128; ++i) {
                rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
                MF32(input + i * 4) = stopped && block >= 94 ? 0 : dc ? 0.1f : ((int32_t)rng / 2147483648.0f) * 0.1f;
            }
            for (unsigned ch = 0; ch < channels; ++ch) memset(G + M32(0x9C2410 + ch * 4), 0, 1024);
            // Retail sends reset the idle counter only when a nonzero send is mixed.
            if (!stopped || block < 94 || !strcmp(argv[6], "idle_reference")) M32(0x9C22F8) = 0;
            c = cpu(); M32(c.esp + 4) = 128;
            uint32_t idle_before = M32(0x9C22F8);
            ++mutex_depth;
            if (retail) f_00820DA3_orig(&c); else f_00820DA3(&c);
            --mutex_depth; CHECK(c.esp == 0xF00004);
            if (stopped && block >= 94 && strcmp(argv[6], "idle_reference"))
                CHECK(M32(0x9C22F8) == idle_before + 1);
            if (reverbfade_setting.boot_value && block >= 94 && !rapid) {
                unsigned elapsed = (block % 94) * 1024 + (sub + 1) * 128;
                int old_live = 0;
                for (unsigned k = 0; k < allocation_count; ++k)
                    if (allocations[k].at == retired_node) old_live = allocations[k].live;
                CHECK(old_live == (elapsed < rate / 2));
                CHECK(reverbfade.count == (elapsed < rate / 2));
            }
            for (unsigned ch = 0; ch < channels; ++ch) for (unsigned i = 0; i < 128; ++i) {
                double v = MF32(M32(0x9C2410 + ch * 4) + i * 4); CHECK(isfinite(v)); sq += v * v;
            }
            for (unsigned i = 0; block < 188 && i < 128; ++i) for (unsigned ch = 0; ch < channels; ++ch) {
                float v = MF32(M32(0x9C2410 + ch * 4) + i * 4); CHECK(fwrite(&v, sizeof v, 1, raw) == 1);
            }
        }
        if (block >= 90 && block < 124) printf("swap block=%u wet_rms=%.9f%s\n", block, sqrt(sq / (1024 * channels)), block == 94 ? " SWAP" : "");
        if (block == 93) baseline_live[0] = live_allocations;
        CHECK(reverbfade.count <= FADE_TAILS);
        if (!rapid && block >= 94 && block % 94 == 93) {
            if (!baseline_live[swaps % 2]) baseline_live[swaps % 2] = live_allocations;
            CHECK(!reverbfade.count && live_allocations == baseline_live[swaps % 2]);
            CHECK(M32(M32(0x9C22FC)) == node && M32(node) == 0);
        }
    }
    CHECK(!fclose(raw));
    c = cpu(); c.ecx = processor; M32(c.esp) = 0; f_0081D93A(&c);
    CHECK(!reverbfade.count && !M32(M32(0x9C22FC)) && !M32(M32(0x9C22F4)));
    CHECK(live_allocations == infrastructure_live);
    printf("swaps=%u live_after_reset=%u infrastructure_live=%u steady_live=%u/%u\n", swaps, live_allocations, infrastructure_live, baseline_live[0], baseline_live[1]);
    free(G);
}
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "settings") && argc == 4) setting_test(atoi(argv[2]), atoi(argv[3]));
    else if (!strcmp(argv[1], "merge")) merge(argc, argv);
    else if (!strcmp(argv[1], "serve")) serve(argc, argv);
    else if (!strcmp(argv[1], "render")) render(argc, argv);
    else if (!strcmp(argv[1], "swap")) swap_test(argc, argv);
    else return 2;
    if (failures) fprintf(stderr, "%u failures\n", failures);
    return failures ? 1 : 0;
}
