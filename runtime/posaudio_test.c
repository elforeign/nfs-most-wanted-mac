// Run via posaudio_test.py; this process has no device/transport code linked.
#include <stdarg.h>
#include <stdlib.h>
#include "host.h"
#include "posaudio.h"

uint8_t *G;
int diag_on;
static unsigned checks, failures;
static uint64_t tick = 100;
static char diagnostic[1400], stage_line[1400];
#define CHECK(x) do { ++checks; if (!(x)) { if (failures < 4) fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)
uint64_t host_ticks(void) { return tick; }
void diag_event(const char *fmt, ...) {
    char line[1400];
    va_list ap; va_start(ap, fmt); vsnprintf(line, sizeof line, fmt, ap); va_end(ap);
    if (!strncmp(line, "posaudio:", 9)) memcpy(diagnostic, line, sizeof line);        // per-second source line
    else if (!strncmp(line, "soundstage:", 11)) memcpy(stage_line, line, sizeof line);  // speaker-use tally
}
void f_0081FB87(Cpu *c);
void f_0081FE64(Cpu *c);
void f_0081FE64_orig(Cpu *c);
static uint32_t ginsu_angle;   // the player-engine voice's pan lookup inside CARSFX_GinsuEngine::SetEngineParams
void f_004EE3E0_orig(Cpu *c) {
    c->esp -= 12; M32(c->esp + 4) = ginsu_angle; M32(c->esp + 8) = 0xB00000;
    f_0081FE64(c); c->esp += 12;
}
void f_004EE3E0(Cpu *c);
void f_004F0840_orig(Cpu *c) { c->esp += 4; }
void f_0081F207(Cpu *c);
void f_0081F207_orig(Cpu *c) {      // Snd's voice pan update: the bank azimuth handler's lookup with the voice's angle word
    c->esp -= 12; M32(c->esp + 4) = ginsu_angle; M32(c->esp + 8) = 0xB00000;
    f_0081FE64(c); c->esp += 12;
}
// Traffic, siren and AEMS engine updates: the same lookup with the voice angle the game's mix map gave (ginsu_angle).
static int async_pan;     // 1: the update only records the angle (Csis member block), the voice is panned later by FUN_0081F207
#define SOURCE_STUB(addr) void f_##addr##_orig(Cpu *c) { \
    if (async_pan) { c->esp += 4; return; } \
    c->esp -= 12; M32(c->esp + 4) = ginsu_angle; M32(c->esp + 8) = 0xB00000; f_0081FE64(c); c->esp += 12; } \
    void f_##addr(Cpu *c);
SOURCE_STUB(004EFBE0)
SOURCE_STUB(004F03A0)
SOURCE_STUB(004EDD90)
// Stub of the original Rear Car Pos update: ctl+0xC = output block; the original's 3DObjPos tail wrote the camera
// angle at out+0xC and the car angle at out+8 (retail: 180 deg in the hood camera, 0 in the chase camera).
static uint32_t stub_angle = 0x8000;
void f_004E5A80_orig(Cpu *c) {
    uint32_t out = M32(c->ecx + 0xC); M32(out + 0xC) = stub_angle; M32(out + 8) = stub_angle; c->esp += 8;
}
void f_004E5A80(Cpu *c);
void f_004B6030_orig(Cpu *c) { c->esp += 4; }   // generic 3D controller update: nothing to do offline
static Cpu cpu(void) { Cpu c = {0}; c.esp = 0xE00000; c.cw = 0x37F; return c; }
static void lookup(void (*fn)(Cpu *), unsigned angle, float out[6]) {
    Cpu c = cpu(); M32(c.esp + 4) = angle; M32(c.esp + 8) = 0xB00000;
    fn(&c); CHECK(c.esp == 0xE00004); memcpy(out, G + 0xB00000, 24);
}
int main(int argc, char **argv) {
    CHECK(argc == 2); G = calloc(1, 0x1000000);
    FILE *f = fopen(argv[1], "rb"); CHECK(f != NULL);
    CHECK(fread(G, 1, 0x1000000, f) == 0x1000000); fclose(f);
    M8(0x9C205F) = 5; M32(0x9C1FDC) = 3;
    const float degrees[] = {0, 45, 135, 225, 315};
    for (int i = 0; i < 5; ++i) MF32(0x9C1FC4 + 4 * i) = degrees[i];
    Cpu c = cpu(); f_0081FB87(&c); CHECK(c.esp == 0xE00004);
    // Quantized Game power has the original 8-bit error; byte identity is strict.
    double worst_game_power = 0;
    for (unsigned a = 0; a < 65536; ++a) {
        float original[6], selected[6]; lookup(f_0081FE64_orig, a, original); lookup(f_0081FE64, a, selected);
        if (!posaudio_layout()) CHECK(!memcmp(original, selected, sizeof original));
        CHECK(selected[5] == 0);
        double p = 0; for (int i = 0; i < 5; ++i) p += original[i] * original[i];
        if (fabs(p - 1) > worst_game_power) worst_game_power = fabs(p - 1);
    }
    CHECK(worst_game_power < 0.012);
    for (int layout = 0; layout < 2; ++layout) {
        float prev[5]; posaudio_gains(-0.25, layout, prev);
        for (unsigned a = 0; a <= 262144; ++a) {
            float g[5]; posaudio_gains(a * 0.25, layout, g);
            double power = 0;
            for (int i = 0; i < 5; ++i) { power += g[i] * g[i]; CHECK(fabs(g[i] - prev[i]) < 0.00008); }
            CHECK(fabs(power - 1) < 0.000001);
            memcpy(prev, g, sizeof prev);
        }
        const uint16_t anchors[] = {0, 0x2000, 0x6000, 0xA000, 0xE000};
        for (int i = 0; i < 5; ++i) {
            float g[5]; posaudio_gains(posaudio_map_default(anchors[i], layout), layout, g);
            for (int j = 0; j < 5; ++j) CHECK(fabs(g[j] - (i == j)) < 0.000001);
        }
    }
    // Career saved in Mono stays Mono in memory despite every forced read.
    M32(0x911FA8) = 0xA00000; M32(0xA00024) = 0xA00100;
    for (unsigned loaded = 0; loaded < 3; ++loaded) {
        M32(0xA00124) = loaded;
        CHECK(posaudio_mode_read(0xA00124) == 1); CHECK(M32(0xA00124) == loaded);
    }
    for (unsigned a = 0; a < 65536; ++a) CHECK(posaudio_map_default(a, 0) == a);
    // Engine sound. Attrib keys as the vault parser's hash32 gives them.
    CHECK(posaudio_attrib_hash("911turbo") == 0xD27D5B8Du && posaudio_attrib_hash("bmwm3gtre46") == 0x4E085FB3u);
    CHECK(posaudio_attrib_hash("m3gtre46careerstart") == 0x39DAE0F8u && posaudio_attrib_hash("carreragt") == 0x67063DD4u);
    CHECK(posaudio_engine_layout(0x12345678u) == -1);
    {
        // Rear Car Pos controller -> CSTATE -> EAX_CarState (forward +0x10, position +0x40, pvehicle Collection key);
        // camera (view 0) 2D position/facing at 009144A0/009144C0; the player's Ginsu engine object shares the CSTATE.
        const uint32_t ctl = 0xA03000, eaxcar = 0xA03100, out = 0xA03200, state = 0xA04000, car = 0xA04100,
                       coll = 0xA04400, ginsu = 0xA05000, ai = 0xA05100;
        M32(ctl + 0xC) = out; M32(ctl + 0x14) = state; M32(ctl + 0x18) = eaxcar; M32(ctl + 0x44) = 0;
        M32(state + 0x34) = car; M32(car + 0x204) = coll; M32(ginsu + 0x14) = state; M32(ai + 0x14) = 0xA04800; M32(0xA04800 + 0x34) = 0;   // an engine whose CSTATE has no car
        MF32(car + 0x10) = 0.6f; MF32(car + 0x14) = 0.8f; MF32(car + 0x40) = 100; MF32(car + 0x44) = 50;
        static const char *const cars[] = {"bmwm3gtre46", "a3", "gallardo", "911turbo"};   // front-mid, front, mid, rear
        static const char *const kinds[] = {"front-mid", "front", "mid", "rear"};
        static const float along[] = {0.45f, -6.0f};    // hood camera 0.45 m ahead of the car centre (measured 0.3-0.6), chase 6 m behind
        double worst_db = 0;
        for (int view = 0; view < 2; ++view)
            for (int k = 0; k < 4; ++k)
                for (int real = 0; real < 2; ++real) {
                    posaudio_set_engine_sound(real);
                    M32(coll + 0x20) = posaudio_attrib_hash(cars[k]);
                    MF32(0x9144A0) = 100 + 0.6f * along[view]; MF32(0x9144A4) = 50 + 0.8f * along[view];
                    MF32(0x9144C0) = 0.6f; MF32(0x9144C4) = 0.8f;
                    M32(eaxcar + 0xC4) = view ? 2 : 0; stub_angle = ginsu_angle = view ? 0 : 0x8000;
                    Cpu e = cpu(); e.ecx = ctl; f_004E5A80(&e); CHECK(e.esp == 0xE00008);
                    CHECK(M32(out + 0xC) == stub_angle);           // the controller output the mix map reads is retail
                    float retail[6], got[6], ai_got[6];
                    lookup(f_0081FE64_orig, ginsu_angle, retail);
                    if (posaudio_layout()) { posaudio_gains(ginsu_angle, 1, retail); retail[5] = 0; }
                    e = cpu(); e.ecx = ginsu; f_004EE3E0(&e); memcpy(got, G + 0xB00000, 24);
                    e = cpu(); e.ecx = ai; f_004EE3E0(&e); memcpy(ai_got, G + 0xB00000, 24);
                    CHECK(!memcmp(ai_got, retail, sizeof retail));  // other engines: retail always
                    if (!real) { CHECK(!memcmp(got, retail, sizeof retail)); continue; }   // Retail: byte-identical
                    double p = 0, pr = 0, sh[5];
                    for (int i = 0; i < 5; ++i) { p += got[i] * got[i]; pr += retail[i] * retail[i]; }
                    for (int i = 0; i < 5; ++i) sh[i] = got[i] * got[i] / p;   // C, FR, SR, SL, FL
                    double db = 10 * log10(p / pr), front = sh[0] + sh[1] + sh[4], rear = sh[2] + sh[3], top = 0;
                    for (int i = 0; i < 5; ++i) if (sh[i] > top) top = sh[i];
                    if (fabs(db) > worst_db) worst_db = fabs(db);
                    CHECK(fabs(db) < 0.5); CHECK(got[5] == retail[5]);
                    CHECK(top < 0.7);                               // spread: never one speaker (retail: 1.0)
                    if (view == 0 && k <= 1) CHECK(front > 0.6 && rear < 0.35);
                    if (view == 0 && k == 2) CHECK(rear > 0.6 && front > 0.02);
                    if (view == 0 && k == 3) CHECK(rear > 0.75 && front > 0.02 && front < 0.2);
                    if (view == 1) CHECK(front > 0.75);
                    printf("%s %-9s C %.2f FL %.2f FR %.2f SL %.2f SR %.2f (%+.3f dB)\n", view ? "chase" : "hood ",
                           kinds[k], sh[0], sh[4], sh[1], sh[3], sh[2], db);
                }
        printf("engine power worst %.4f dB\n", worst_db);
        posaudio_set_engine_sound(0);
    }
    {
        // AI racers, cops and traffic (Realistic): each class's update finds its car through object+0x14 -> CSTATE+0x34,
        // the pan keeps the game's power, and the arc follows the distance (80 m: a point, 10 m: wider, 3 m: wide).
        const uint32_t aistate = 0xA06000, aicar = 0xA06100, aicoll = 0xA06400, aiobj = 0xA06500;
        M32(0xA00000 + 100) = 0;                                   // g_pEAXSound+100: one player
        M32(aiobj + 0x14) = aistate; M32(aistate + 0x34) = aicar; M32(aicar + 0x204) = aicoll;
        M32(aicoll + 0x20) = posaudio_attrib_hash("a3");
        MF32(0x9144A0) = 100; MF32(0x9144A4) = 50; MF32(0x9144C0) = 0.6f; MF32(0x9144C4) = 0.8f;
        MF32(aicar + 0x10) = 0.6f; MF32(aicar + 0x14) = 0.8f;
        static const struct { const char *name; void (*fn)(Cpu *); } kinds[] = {
            {"ai engine", f_004EE3E0}, {"cop engine", f_004EDD90}, {"traffic", f_004EFBE0}, {"siren", f_004F03A0}};
        static const double metres[] = {80, 10, 3};
        double worst = 0;
        for (int kind = 0; kind < 4; ++kind)
            for (int m = 0; m < 3; ++m) {
                double top[2] = {0, 0}, db = 0;
                for (int real = 0; real < 2; ++real) {
                    posaudio_set_engine_sound(real);
                    MF32(aicar + 0x40) = 100 + 0.6f * metres[m]; MF32(aicar + 0x44) = 50 + 0.8f * metres[m];
                    ginsu_angle = 0;                                   // dead ahead, as the game's mix map says
                    float retail[6], got[6];
                    lookup(f_0081FE64_orig, ginsu_angle, retail);
                    if (posaudio_layout()) { posaudio_gains(ginsu_angle, 1, retail); retail[5] = 0; }
                    Cpu e = cpu(); e.ecx = aiobj; kinds[kind].fn(&e); memcpy(got, G + 0xB00000, 24);
                    double p = 0, pr = 0;
                    for (int i = 0; i < 5; ++i) { p += got[i] * got[i]; pr += retail[i] * retail[i]; }
                    for (int i = 0; i < 5; ++i) if (got[i] * got[i] / p > top[real]) top[real] = got[i] * got[i] / p;
                    if (!real) { CHECK(!memcmp(got, retail, sizeof retail)); continue; }   // Retail: byte-identical
                    db = 10 * log10(p / pr); CHECK(fabs(db) < 0.5); CHECK(got[5] == retail[5]);
                    if (fabs(db) > worst) worst = fabs(db);
                }
                if (m == 0) CHECK(top[1] > 0.9);                        // far: nearly the retail point
                if (m == 2) CHECK(top[1] < (kind == 3 ? 0.95 : 0.8));   // close: wider (dead ahead sits on the C speaker, the best case for retail)
                CHECK(top[1] <= top[0] + 1e-6);
                printf("%-10s %2.0f m: top speaker retail %.2f realistic %.2f (%+.3f dB)\n", kinds[kind].name, metres[m], top[0], top[1], db);
            }
        // Asynchronous voices (AEMS engines, traffic, sirens): the update records its angle word, the bank pan update that
        // follows with the same word (return address 0081A0D1 = SNDAEMSI_bankazimuth) is spread; another word or another
        // caller (a music stream, 0081CF56) is not.
        {
            const uint32_t mix = 0xA06700;
            M32(aiobj + 0x10) = mix; M32(aistate + 0x34) = aicar; async_pan = 1;
            MF32(aicar + 0x40) = 100 + 0.6f * 10; MF32(aicar + 0x44) = 50 + 0.8f * 10;
            for (int kind = 1; kind < 4; ++kind)
                for (int variant = 0; variant < 5; ++variant) {      // 0 match, 1 other word, 2 stream caller, 3 Retail, 4 wrong source
                    posaudio_set_engine_sound(variant != 3);
                    uint32_t word = variant == 4 ? 0x4000 : 0;                             // the car is dead ahead: word 0
                    ginsu_angle = word; M32(mix) = word;
                    Cpu u = cpu(); u.ecx = aiobj; kinds[kind].fn(&u);                          // records the word
                    ginsu_angle = variant == 1 ? 1 : word;
                    float retail[6], got[6]; lookup(f_0081FE64_orig, ginsu_angle, retail);
                    if (posaudio_layout()) { posaudio_gains(ginsu_angle, 1, retail); retail[5] = 0; }
                    u = cpu(); u.esp -= 4; M32(u.esp) = variant == 2 ? 0x0081CF56u : 0x0081A0D1u; M32(u.esp + 4) = 7;
                    f_0081F207(&u); memcpy(got, G + 0xB00000, 24);
                    if (variant == 0) CHECK(memcmp(got, retail, sizeof retail) != 0); else CHECK(!memcmp(got, retail, sizeof retail));
                    printf("async %-10s %s: %s\n", kinds[kind].name, variant == 0 ? "match" : variant == 1 ? "other word" : variant == 2 ? "stream" :
                           variant == 3 ? "Retail" : "wrong source", memcmp(got, retail, sizeof retail) ? "spread" : "retail");
                }
            async_pan = 0; M32(aiobj + 0x10) = 0;
        }
        // No car found (state without +0x34): retail even in Realistic.
        posaudio_set_engine_sound(1); M32(aistate + 0x34) = 0; ginsu_angle = 0x2000;
        float retail[6], got[6]; lookup(f_0081FE64_orig, ginsu_angle, retail);
        if (posaudio_layout()) { posaudio_gains(ginsu_angle, 1, retail); retail[5] = 0; }
        Cpu e = cpu(); e.ecx = aiobj; f_004EFBE0(&e); memcpy(got, G + 0xB00000, 24);
        CHECK(!memcmp(got, retail, sizeof retail));
        printf("AI/cop/traffic power worst %.4f dB\n", worst);
        posaudio_set_engine_sound(0); ginsu_angle = 0x4000;
    }
    {
        // The player's own tyre/road/body voices (Realistic): a bank pan update (return address 0081A0D1) carrying the angle word
        // the Rear Car Pos controller just gave the mix map is moved from that one point (2 m behind the car) to the four
        // wheels, power unchanged; another word, a music stream caller, Retail and a stale update stay retail.
        const uint32_t ctl = 0xA03000, eaxcar = 0xA03100, out = 0xA03200, state = 0xA04000, car = 0xA04100, coll = 0xA04400;
        M32(ctl + 0xC) = out; M32(ctl + 0x14) = state; M32(ctl + 0x18) = eaxcar; M32(ctl + 0x44) = 0;
        M32(state + 0x34) = car; M32(car + 0x204) = coll; M32(coll + 0x20) = posaudio_attrib_hash("a3");
        MF32(car + 0x10) = 0.6f; MF32(car + 0x14) = 0.8f; MF32(car + 0x40) = 100; MF32(car + 0x44) = 50;
        static const float along[] = {0.45f, -6.0f, 0.0f}, lateral[] = {0, 0, 1.5f};   // view 2: camera abeam, 1.5 m to the right of the car
        double worst = 0;
        for (int view = 0; view < 3; ++view)
            for (int variant = 0; variant < 5; ++variant) {   // 0 match, 1 Retail, 2 other word, 3 stream caller, 4 stale
                posaudio_set_engine_sound(variant != 1);
                diag_on = variant == 1;      // Retail with BOOT_DIAG keeps the rear controller's data current: it must still not move the voice
                MF32(0x9144A0) = 100 + 0.6f * along[view] + 0.8f * lateral[view]; MF32(0x9144A4) = 50 + 0.8f * along[view] - 0.6f * lateral[view];
                MF32(0x9144C0) = 0.6f; MF32(0x9144C4) = 0.8f;
                M32(eaxcar + 0xC4) = view ? 2 : 0; stub_angle = view ? 0 : 0x8000;
                Cpu e = cpu(); e.ecx = ctl; f_004E5A80(&e);
                if (variant == 4) tick += 400000000ull;                         // older than 0.3 s
                ginsu_angle = variant == 2 ? stub_angle + 1 : stub_angle;
                float retail[6], got[6]; lookup(f_0081FE64_orig, ginsu_angle, retail);
                if (posaudio_layout()) { posaudio_gains(ginsu_angle, 1, retail); retail[5] = 0; }
                e = cpu(); e.esp -= 4; M32(e.esp) = variant == 3 ? 0x0081CF56u : 0x0081A0D1u; M32(e.esp + 4) = 9;
                f_0081F207(&e); memcpy(got, G + 0xB00000, 24);
                if (variant != 0) { CHECK(!memcmp(got, retail, sizeof retail)); continue; }
                double p = 0, pr = 0, sh[5];
                for (int i = 0; i < 5; ++i) { p += got[i] * got[i]; pr += retail[i] * retail[i]; }
                for (int i = 0; i < 5; ++i) sh[i] = got[i] * got[i] / p;      // C, FR, SR, SL, FL
                double db = 10 * log10(p / pr), front = sh[0] + sh[1] + sh[4], rear = sh[2] + sh[3], top = 0;
                for (int i = 0; i < 5; ++i) if (sh[i] > top) top = sh[i];
                if (fabs(db) > worst) worst = fabs(db);
                CHECK(fabs(db) < 0.5); CHECK(got[5] == retail[5]);
                CHECK(memcmp(got, retail, sizeof retail) != 0);                 // moved off the retail point
                if (view < 2) CHECK(fabs(sh[1] - sh[4]) < 0.02 && fabs(sh[2] - sh[3]) < 0.02);   // symmetric: left and right wheels
                if (variant == 0 && view == 0) { CHECK(top < 0.5); CHECK(front > 0.3 && rear > 0.25 && rear < 0.75); }   // hood: retail rear 1.0
                if (variant == 0 && view == 1) CHECK(front > 0.75);                  // chase: all four wheels ahead
                if (variant == 0 && view == 2) CHECK(sh[0] + sh[1] + sh[4] > 0.4 && sh[3] > 0.3);   // abeam (car on the left): front wheels ahead-left, rear wheels behind-left; a point at the car centre would give the front speakers 0.15
                if (variant == 0) printf("rear pos voice %s: C %.2f FL %.2f FR %.2f SL %.2f SR %.2f (%+.3f dB)\n", view == 2 ? "abeam" : view ? "chase" : "hood ", sh[0], sh[4], sh[1], sh[3], sh[2], db);
                tick += 400000000ull;
            }
        diag_on = 0;
        printf("rear pos voice power worst %.4f dB\n", worst);
        posaudio_set_engine_sound(0); ginsu_angle = 0x4000;
    }
    // A real lookup made inside a source update is attributed to that source.
    diag_on = 1; c = cpu(); c.ecx = 0xA01000;
    M32(c.ecx + 0x10) = 0xA02000; M32(0xA02000) = 0x4000;
    f_004EFBE0(&c); posaudio_diag_second();
    CHECK(strstr(diagnostic, "traffic[") != NULL); CHECK(strstr(diagnostic, "active=1") != NULL);
    CHECK(strstr(diagnostic, "src=16384/90.00deg pan=16384/90.00deg") != NULL);
    tick += 1000000001ull; posaudio_diag_second(); CHECK(strstr(diagnostic, "active=0") != NULL);
    CHECK(strstr(stage_line, "other[n=") != NULL || strstr(stage_line, "traffic[n=") != NULL);   // the tally line is emitted
    printf("%u checks, %u failed; original Game worst power error %.7f\n", checks, failures, worst_game_power);
    free(G); return failures ? 1 : 0;
}
