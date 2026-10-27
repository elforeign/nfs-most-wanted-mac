// Offline production-code tests; run with x360music_test.py (never starts the game).
#define X360AUDIO_TEST
#include "x360audio.c"

const char *game_dir;
int diag_on = 1;
// Extra EA Trax (extratrax.c) is off here: the pair is served as x360audio.c builds it.
int extratrax_open(int is_mus, const X360MusicHandle *a, const X360MusicHandle *b, X360MusicHandle *out) { (void)is_mus; (void)a; (void)b; (void)out; return 0; }
static int failures;
static char saved_pref[64];
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)
const char *app_pref(const char *key) {
    return !strcmp(key, "audio.x360_music") ? getenv("NFS_MUSIC_TEST_PREF") : NULL;
}
void app_pref_set(const char *key, const char *value) {
    CHECK(!strcmp(key, "audio.x360_music"));
    snprintf(saved_pref, sizeof saved_pref, "%s", value);
}
void report_note(const char *fmt, ...) { (void)fmt; }
static void settings_test(void) {
    unsetenv("NFS_X360_MUSIC"); unsetenv("NFS_X360_AUDIO"); unsetenv("NFS_MUSIC_TEST_PREF");
    settings_init();
    CHECK(settings_count() == 3);   // Sound mix, Music, Reverb (A8)
    Setting *mix = settings_at(0), *music = settings_at(1);
    CHECK(!strcmp(mix->id, "audio.x360_mix") && !strcmp(music->id, "audio.x360_music"));
    CHECK(!strcmp(mix->label, "Sound mix") && !strcmp(music->label, "Music"));
    CHECK(!strcmp(mix->env, "NFS_X360_AUDIO") && !strcmp(music->env, "NFS_X360_MUSIC"));
    CHECK(mix->type == SET_TOGGLE && music->type == SET_TOGGLE && mix->restart);
    CHECK(mix->nchoices == 2 && music->nchoices == 2);
    CHECK(mix->category == SET_AUDIO && music->category == SET_AUDIO && music->restart);
    CHECK(mix->def == 1 && music->def == 1 && music->boot_value == 1);
    char label[32];
    CHECK(!strcmp(settings_value_name(music, 0, label, sizeof label), "PC"));
    CHECK(!strcmp(settings_value_name(music, 1, label, sizeof label), "X360"));
    CHECK(settings_set(music, 0) == 1 && music->value == 0 && music->boot_value == 1);
    CHECK(!strcmp(saved_pref, "off"));
    CHECK(!getenv("NFS_X360_MUSIC"));  // changing the row takes effect next boot
    CHECK(mix->value == 1 && mix->boot_value == 1);
    settings_reset(music);
    CHECK(music->value == 1 && music->boot_value == 1 && !saved_pref[0]);
    puts("settings: adjacent Audio rows, mix and music default X360, PC/X360 labels, saved toggle, restart semantics passed");
}
void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); putchar('\n');
}
static void put_be32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (24 - i * 8));
}
static void converter_test(const char *input, const char *output) {
    long size = 0;
    uint8_t *src = slurp(input, &size), *dst = malloc(MUSIC_MAP_SIZE), *bad = malloc(MUSIC_MAP_SIZE);
    char why[256];
    CHECK(src && size == MUSIC_MAP_SIZE && dst && bad);
    if (!src || size != MUSIC_MAP_SIZE || !dst || !bad) exit(1);
    CHECK(x360music_convert(src, (size_t)size, dst, why, sizeof why));
    CHECK(!memcmp(dst, "xDFP\x05\x01", 6));
    FILE *f = fopen(output, "wb"); CHECK(f);
    if (f) { CHECK(fwrite(dst, 1, (size_t)size, f) == (size_t)size); CHECK(!fclose(f)); }
    // Each deliberately broken input must fail, including the packed opcode path.
    CHECK(!x360music_convert(src, 0, dst, why, sizeof why));
    CHECK(!x360music_convert(src, (size_t)size - 1, dst, why, sizeof why));
    CHECK(!x360music_convert(src, (size_t)size + 1, dst, why, sizeof why));
    const size_t bad_bytes[] = {0, 4, 5, 0xf, 0x12, 0x14, 0x48, 0x178c0};
    for (unsigned i = 0; i < sizeof bad_bytes / sizeof bad_bytes[0]; ++i) {
        memcpy(bad, src, (size_t)size); bad[bad_bytes[i]] ^= 0xff;
        CHECK(!x360music_convert(bad, (size_t)size, dst, why, sizeof why));
    }
    memcpy(bad, src, (size_t)size);
    size_t action = be16(src + 0x178c0) * 4u + 24;
    put_be32(bad + action, (be32(src + action) & ~(127u << 17)) | (127u << 17));
    CHECK(!x360music_convert(bad, (size_t)size, dst, why, sizeof why));
    CHECK(strstr(why, "opcode"));
    long again_size;
    uint8_t *again = slurp(input, &again_size);
    CHECK(again && again_size == size && !memcmp(src, again, (size_t)size));
    free(src); free(dst); free(bad); free(again);
    puts("converter: 12 deliberately malformed inputs rejected; source unchanged");
}
static void serving_test(const char *extract, const char *pc, const char *expected, int want) {
    setenv("NFS_X360_FILES", extract, 1); game_dir = pc;
    X360MusicHandle map = {0}, mus = {0}, again = {0};
    CHECK(settings_count() == 3);   // Sound mix, Music, Reverb (A8)
    CHECK(!x360music_open("SOUND/PFDATA/OTHER.mpf", &map));
    CHECK(!x360music_open("NOTSOUND/PFDATA/MW_Music.mpf", &map));
    CHECK(!x360music_open("SOUND/PFDATA/MW_Music.mpf.bak", &map));
    CHECK(x360music_open("sound\\PFData\\MW_Music.mpf", &map) == want);
    CHECK(x360music_open("SOUND/PFDATA/MW_MUSIC.MUS", &mus) == want);
    CHECK(x360music_open("C:\\NFSMW\\Sound\\PFdata\\MW_Music.MPF", &again) == want);
    if (s_x360_music.boot_value) {
        if (want) CHECK(strstr(music_result, "bounded chunks through SCEl"));
        else CHECK(strncmp(music_result, "ok:", 3));
    }
    if (!want) {
        CHECK(!map.data && !mus.data && !again.data);
        puts("serving: both opens kept PC"); return;
    }
    CHECK(map.data && !map.mus && map.size == MUSIC_MAP_SIZE);
    CHECK(mus.data && mus.mus && mus.size == 653261696);
    CHECK(again.data == map.data && again.pos == 0);
    long size; uint8_t *gold = slurp(expected, &size);
    CHECK(gold && size == (long)map.size && !memcmp(gold, map.data, map.size)); free(gold);
    uint8_t buf[128], other[128];
    CHECK(x360music_read(&map, buf, 8) == 8 && !memcmp(buf, "xDFP\x05\x01", 6));
    CHECK(map.pos == 8 && again.pos == 0);
    CHECK(x360music_read(&again, other, 8) == 8 && !memcmp(buf, other, 8));
    CHECK(x360music_seek(&map, -4, 1) == 4);
    CHECK(x360music_seek(&map, -4, 2) == (int64_t)map.size - 4);
    CHECK(x360music_read(&map, buf, sizeof buf) == 4);
    CHECK(x360music_read(&map, buf, 1) == 0);
    CHECK(x360music_seek(&map, 10, 2) == (int64_t)map.size + 10);
    CHECK(x360music_read(&map, buf, 10) == 0);
    int64_t old = map.pos;
    CHECK(x360music_seek(&map, -1, 0) == -1 && map.pos == old);
    CHECK(x360music_seek(&map, 0, 3) == -1 && map.pos == old);
    CHECK(x360music_seek(&map, INT64_MAX, 1) == -1 && map.pos == old);
    CHECK(x360music_seek(&map, INT64_MIN, 1) == -1 && map.pos == old);
    CHECK(x360music_seek(&map, 0, 0) == 0);
    CHECK(x360music_read(&map, buf, 0) == 0 && map.pos == 0);
    CHECK(x360music_seek(&mus, 256, 0) == 256);
    CHECK(x360music_read(&mus, buf, 40) == 40 && !memcmp(buf, "SCHl", 4));
    CHECK(music_header(buf) == 2);
    CHECK(x360music_seek(&mus, 641034752, 0) == 641034752);
    CHECK(x360music_read(&mus, buf, 40) == 40 && music_header(buf) == 4);
    CHECK(x360music_seek(&mus, 240, 0) == 240);
    CHECK(x360music_read(&mus, buf, 80) == 80);  // header inside sector-style read
    CHECK(x360audio_open("sound\\PFData\\MW_Music.mpf", "/absent") == -1);
    puts("serving: case/slash matching, independent cursors, reads, seeks, EOF, source mapping passed");
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "settings")) settings_test();
    else if (argc == 4 && !strcmp(argv[1], "convert")) converter_test(argv[2], argv[3]);
    else if (argc == 6 && !strcmp(argv[1], "serve")) {
        settings_init(); serving_test(argv[2], argv[3], argv[4], atoi(argv[5]));
    }
    else { fprintf(stderr, "use x360music_test.py\n"); return 2; }
    printf("%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
