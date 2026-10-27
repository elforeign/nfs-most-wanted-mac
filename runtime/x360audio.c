// x360audio.c - optional X360 sound mix/engines (A4/A5) and independent music (A7); A9 collision volume; G3 traffic horn/engine banks;
// A10 all other sound banks at 48 kHz.
// Switch: audio.x360_mix (NFS_X360_AUDIO=on|off, default on; restart): on makes the files
// the game opens come from the X360 extract (the roots.txt X360 root; NFS_X360_FILES=<dir> overrides).
// Mix maps (A4): the game gets them from Global\InGameMemoryFile.bin, not SOUND\MixMaps (the same data on both platforms,
// big-endian on X360: word-swapped, 96.8% of words equal, the rest the 79 master-channel levels, +1..+6 dB). The CreateFileA
// import calls x360audio_open(), which returns that memory file with the X360's mix maps put in, or -1 for the PC's.
                                                                                                                                                          
// BOOT_DIAG: `x360audio:` mix/engine/attrib lines; `x360abk:` G3 bank conversion (sounds, tables, bytes, ms) and serving,
// A10 banks served / kept PC by reason (cache, ms, bytes);
// `x360music:` pair selection, conversion and actual segment reads.
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef X360AUDIO_TEST
#include "funcs.h"
#include "host.h"
#else
extern int diag_on;
void diag_event(const char *fmt, ...);
#endif
#include "roots.h"
#include "x360audio.h"
#include "settings.h"
#include "tunnelhold.h"
#ifdef NFS_PUBLIC
#define extratrax_open(...) 0   // Extra EA Trax is owner-only: absent from public builds (extratrax_public.c)
#endif

extern const char *game_dir;

static const char *const kPcX360[] = {"PC", "X360"}, *const kPcX360V[] = {"off", "on"};   // shown PC / X360, saved off / on
static Setting s_x360_audio = {.id = "audio.x360_mix", .category = SET_AUDIO, .order = 60, .label = "Sound mix",
    .help = "X360 uses the console's mix levels (louder engine, skids and sirens) and needs the X360 files.",
    .type = SET_TOGGLE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_X360_AUDIO", .restart = 1};

static Setting s_x360_music = {.id = "audio.x360_music", .category = SET_AUDIO, .order = 70, .label = "Music",
    .help = "X360 plays the console's music at 44.1 kHz and needs the X360 files.",
    .type = SET_TOGGLE, .choices = kPcX360, .values = kPcX360V, .nchoices = 2, .def = 1, .env = "NFS_X360_MUSIC", .restart = 1};

static Setting s_x360_reverb = {.id = "audio.x360_reverb", .category = SET_AUDIO, .order = 65, .label = "Reverb",
    .help = "X360 uses the console's reverb presets, with PC mono/stereo fallback. Needs the X360 files and a restart.",
    .type = SET_CHOICE, .choices = kPcX360, .nchoices = 2, .def = 1, .env = "NFS_X360_REVERB", .restart = 1};

__attribute__((constructor)) static void x360audio_register(void) {
    // F10 Audio: Xbox 360 sound heading 50; mix 60, music 70, police 80, cutscenes 90, reverb 95.
    settings_add(&s_x360_audio);
    settings_add(&s_x360_music);
    settings_add(&s_x360_reverb);
}

int x360audio_on(void) { return s_x360_audio.boot_value; }

static const char *x360_dir(void) { return roots_x360_files(); }

// A file of the X360 extract by its case-insensitive path below it ("SOUND/MIXMAPS/MAPOUTPUT.MXB"); host path or 0.
int x360audio_case_find(const char *root, const char *rel, char *out, size_t n) {
    char cur[4096], part[512];
    snprintf(cur, sizeof cur, "%s", root);
    snprintf(part, sizeof part, "%s", rel);
    char *state;
    for (char *tok = strtok_r(part, "/", &state); tok; tok = strtok_r(NULL, "/", &state)) {
        char *next = NULL;
        DIR *d = opendir(cur);
        struct dirent *e;
        while (d && (e = readdir(d))) if (!strcasecmp(e->d_name, tok)) { asprintf(&next, "%s/%s", cur, e->d_name); break; }
        if (d) closedir(d);
        if (!next) return 0;
        snprintf(cur, sizeof cur, "%s", next);
        free(next);
    }
    snprintf(out, n, "%s", cur);
    return 1;
}

static int x360_find(const char *rel, char *out, size_t n) {
    return x360audio_case_find(x360_dir(), rel, out, n);
}

static void put_swapped(uint8_t *dst, const uint8_t *src, uint32_t n) {
    for (uint32_t i = 0; i + 3 < n; i += 4) { dst[i] = src[i + 3]; dst[i + 1] = src[i + 2]; dst[i + 2] = src[i + 1]; dst[i + 3] = src[i]; }
}

static uint8_t *slurp(const char *path, long *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    rewind(f);
    uint8_t *b = malloc(*size > 0 ? *size : 1);
    if (b && fread(b, 1, *size, f) != (size_t)*size) { free(b); b = NULL; }
    fclose(f);
    return b;
}

// Music (A7): convert_mpf.py's exact retail layout. Scalar words and packed fields
// have different endian rules; the MUS is already EA ADPCM R3 and stays unchanged.
enum { MUSIC_MAP_SIZE = 0x213d4, MUSIC_TABLE = 0x1ae0c, MUSIC_SEGMENTS = 3257 };
static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0];
}
static unsigned be16(const uint8_t *p) { return (unsigned)p[0] << 8 | p[1]; }
static void store_le32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
static uint32_t repack(uint32_t v, const uint8_t *widths) {
    uint32_t out = 0;
    unsigned bit = 0;
    while (bit < 32) {
        unsigned w = *widths++;
        uint32_t mask = UINT32_MAX >> (32 - w);
        out |= ((v >> (32 - bit - w)) & mask) << bit;
        bit += w;
    }
    return out;
}
static void transform(const uint8_t *src, uint8_t *dst, size_t off, const uint8_t *widths) {
    store_le32(dst + off, repack(be32(src + off), widths));
}
static void swap16(const uint8_t *src, uint8_t *dst, size_t off) {
    dst[off] = src[off + 1]; dst[off + 1] = src[off];
}
static int music_error(char *why, size_t n, const char *reason) {
    snprintf(why, n, "%s", reason);
    return 0;
}
int x360music_convert(const uint8_t *src, size_t size, uint8_t *dst, char *why, size_t why_size) {
    static const uint32_t header[] = {0x48,0x1d0c,0x178c0,0x1794c,0x1a8ac,0x1a910,0x1adf4,0x1adf8,MUSIC_TABLE,MUSIC_MAP_SIZE};
    static const uint8_t scalar[] = {32}, edge[] = {8,8,16}, event[] = {24,8};
    static const uint8_t nodes[4][5] = {{16,5,6,5},{12,5,3,4,8},{16,9,2,5},{24,8}};
    static const uint8_t action_widths[] = {8,7,1,2,2,3,3,6};
    static const uint8_t args[15][4] = {
        [1]={16,16}, [2]={16,16}, [4]={16,8,1,7}, [5]={8,7,1,16},
        [8]={16,8,8}, [9]={24,8}, [13]={16,16}, [14]={8,8,16}};
    if (size != MUSIC_MAP_SIZE) return music_error(why, why_size, "MPF size is not retail MW 136148");
    if (memcmp(src, "PFDx\x05\x01\xb0\x01", 8) || be16(src + 0x12) != 3681 || src[0xf] != 70)
        return music_error(why, why_size, "MPF magic/version/node/event count mismatch");
    for (unsigned i = 0; i < 10; ++i) if (be32(src + 0x14 + i * 4) != header[i])
        return music_error(why, why_size, "MPF section layout mismatch");
    memcpy(dst, src, size);
    transform(src, dst, 0, scalar); swap16(src, dst, 0x12);
    for (size_t off = 0x14; off < 0x3c; off += 4) transform(src, dst, off, scalar);
    for (unsigned i = 0; i < 3681; ++i) {
        size_t off = be16(src + 0x48 + i * 2) * 4u;
        size_t end = i + 1 < 3681 ? be16(src + 0x48 + (i + 1) * 2) * 4u : 0x178c0;
        if ((!i && off != 0x1d0c) || off < 0x1d0c || end > 0x178c0 || end < off + 16)
            return music_error(why, why_size, "MPF node bounds mismatch");
        swap16(src, dst, 0x48 + i * 2);
        for (unsigned j = 0; j < 4; ++j) transform(src, dst, off + j * 4, nodes[j]);
        for (size_t pos = off + 16; pos < end; pos += 4) transform(src, dst, pos, edge);
    }
    unsigned commands = 0;
    for (unsigned i = 0; i < 70; ++i) {
        size_t off = be16(src + 0x178c0 + i * 2) * 4u;
        size_t end = i + 1 < 70 ? be16(src + 0x178c0 + (i + 1) * 2) * 4u : 0x1a8ac;
        if ((!i && off != 0x1794c) || off < 0x1794c || end > 0x1a8ac || end < off + 20 || (end - off - 20) % 12)
            return music_error(why, why_size, "MPF event/action bounds mismatch");
        swap16(src, dst, 0x178c0 + i * 2);
        for (unsigned j = 0; j < 5; ++j) transform(src, dst, off + j * 4, j == 3 ? event : scalar);
        for (size_t pos = off + 20; pos < end; pos += 12) {
            uint32_t action = repack(be32(src + pos + 4), action_widths);
            unsigned opcode = (action >> 8) & 127;
            if (opcode >= 15 || !args[opcode][0]) return music_error(why, why_size, "MPF unsupported action opcode");
            transform(src, dst, pos, scalar);
            store_le32(dst + pos + 4, action);
            transform(src, dst, pos + 8, args[opcode]);
            ++commands;
        }
    }
    if (commands != 894) return music_error(why, why_size, "MPF action count mismatch");
    for (size_t off = 0x1a910; off < 0x1adf8; off += 4) transform(src, dst, off, scalar);
    for (size_t off = MUSIC_TABLE; off < size; off += 4) transform(src, dst, off, scalar);
    snprintf(why, why_size, "ok: LE xDFP 5.1, 3681 nodes, 70 events, 894 actions");
    return 1;
}

static pthread_once_t music_once = PTHREAD_ONCE_INIT;
static X360MusicHandle music_mpf, music_mus;
static char music_result[256];
static unsigned music_channels[MUSIC_SEGMENTS];
static uint8_t music_seen[MUSIC_SEGMENTS];
static pthread_mutex_t music_diag_lock = PTHREAD_MUTEX_INITIALIZER;

static void music_log(const char *fmt, ...) {
    char line[1024];
    va_list ap; va_start(ap, fmt); vsnprintf(line, sizeof line, fmt, ap); va_end(ap);
    if (diag_on) diag_event("x360music: %s", line);
    else fprintf(stderr, "x360music: %s\n", line);
}
// Retail SCHl is 36 or 40 bytes (sample-count width varies). Attribute values are BE on both platforms.
static unsigned music_header(const uint8_t *p) {
    unsigned size = le32(p + 4);
    if (memcmp(p, "SCHl", 4) || (size != 36 && size != 40) || memcmp(p + 8, "GSTR", 4)) return 0;
    unsigned ended = 0;
    unsigned codec = 0, rate = 0, channels = 0, format = 0;
    for (unsigned pos = 16; pos < size;) {
        unsigned id = p[pos++];
        if (id == 0xff) { ended = 1; break; }
        if (id == 0xfd || id == 0xfe || id == 0xfc) continue;
        if (pos == size) return 0;
        unsigned len = p[pos++], v = 0;
        if (len > 4 || pos + len > size) return 0;
        for (unsigned j = 0; j < len; ++j) v = (v << 8) | p[pos++];
        if (id == 6) format = v;
        if (id == 0x80) codec = v;
        if (id == 0x82) channels = v;
        if (id == 0x84) rate = v;
    }
    return ended && format == 101 && codec == 3 && rate == 44100 && (channels == 2 || channels == 4) ? channels : 0;
}
static uint8_t *music_read_map(const char *path, char *why, size_t why_size) {
    FILE *f = fopen(path, "rb");
    if (!f) { snprintf(why, why_size, "cannot read MPF: %s", strerror(errno)); return NULL; }
    struct stat st;
    uint8_t *b = NULL;
    if (fstat(fileno(f), &st) || !S_ISREG(st.st_mode) || st.st_size != MUSIC_MAP_SIZE)
        snprintf(why, why_size, "MPF size/type mismatch, expected %d bytes", MUSIC_MAP_SIZE);
    else if (!(b = malloc(MUSIC_MAP_SIZE))) snprintf(why, why_size, "MPF allocation failed");
    else if (fread(b, 1, MUSIC_MAP_SIZE, f) != MUSIC_MAP_SIZE) {
        free(b); b = NULL; snprintf(why, why_size, "MPF short read");
    }
    fclose(f);
    return b;
}
static void music_init(void) {
    char xpath[4096], ppath[4096], why[256] = "X360 MPF missing";
    uint8_t *x = NULL, *pc = NULL, *converted = NULL;
    const uint8_t *mus = MAP_FAILED;
    int fd = -1, ok = 0;
    struct stat st = {0};
    if (!x360_find("SOUND/PFDATA/MW_MUSIC.MPF", xpath, sizeof xpath) || !(x = music_read_map(xpath, why, sizeof why))) goto done;
    snprintf(why, sizeof why, "MPF allocation failed");
    if (!(converted = malloc(MUSIC_MAP_SIZE))) goto done;
    if (!x360music_convert(x, MUSIC_MAP_SIZE, converted, why, sizeof why)) goto done;
    snprintf(why, sizeof why, "PC MPF missing (graph validation required)");
    if (!game_dir || !x360audio_case_find(game_dir, "SOUND/PFDATA/MW_Music.mpf", ppath, sizeof ppath) || !(pc = music_read_map(ppath, why, sizeof why))) goto done;
    snprintf(why, sizeof why, "converted MPF graph differs from PC");
    if (memcmp(pc, converted, MUSIC_TABLE)) goto done;
    for (size_t off = MUSIC_TABLE + 4; off < MUSIC_MAP_SIZE; off += 8) if (memcmp(pc + off, converted + off, 4)) {
        snprintf(why, sizeof why, "converted MPF segment durations differ from PC"); goto done;
    }
    snprintf(why, sizeof why, "X360 MUS missing/unreadable");
    if (!x360_find("SOUND/PFDATA/MW_MUSIC.MUS", xpath, sizeof xpath) || (fd = open(xpath, O_RDONLY)) < 0) goto done;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 40 || (uint64_t)st.st_size > UINT32_MAX) goto done;
    snprintf(why, sizeof why, "X360 MUS read-only mapping failed");
    mus = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (mus == MAP_FAILED) goto done;
    size_t prev = 0;
    unsigned stereo = 0, quad = 0, chunks = 0;
    for (unsigned i = 0; i < MUSIC_SEGMENTS; ++i) {
        uint64_t off = (uint64_t)le32(converted + MUSIC_TABLE + i * 8) << 7;
        if (off < 256 || off <= prev || off + 40 > (uint64_t)st.st_size || !le32(converted + MUSIC_TABLE + i * 8 + 4)) {
            snprintf(why, sizeof why, "segment %u offset/duration outside MUS", i); goto done;
        }
        unsigned ch = music_header(mus + off);
        if (!ch) { snprintf(why, sizeof why, "segment %u is not 44.1 kHz EA ADPCM R3 SCHl", i); goto done; }
        // Read only chunk headers, skipping payloads. A valid SCHl alone does not
        // prove the stream is complete: require every extent through SCEl to fit
        // before the next segment (or EOF for the last), with forward progress.
        uint64_t limit = (uint64_t)st.st_size;
        if (i + 1 < MUSIC_SEGMENTS) {
            limit = (uint64_t)le32(converted + MUSIC_TABLE + (i + 1) * 8) << 7;
            if (limit <= off || limit > (uint64_t)st.st_size) {
                snprintf(why, sizeof why, "segment %u next offset outside MUS", i); goto done;
            }
        }
        for (uint64_t pos = off;;) {
            if (limit - pos < 8) {
                snprintf(why, sizeof why, "segment %u missing SCEl/truncated chunk header at %llu", i, (unsigned long long)pos); goto done;
            }
            uint32_t size = le32(mus + pos + 4);
            if (size < 8 || size > limit - pos) {
                snprintf(why, sizeof why, "segment %u chunk extent outside MUS/segment at %llu (size %u)", i, (unsigned long long)pos, size); goto done;
            }
            ++chunks;
            if (!memcmp(mus + pos, "SCEl", 4)) break;
            pos += size;
        }
        music_channels[i] = ch;
        stereo += ch == 2; quad += ch == 4; prev = (size_t)off;
    }
    if (stereo != 3243 || quad != 14) { snprintf(why, sizeof why, "MUS channel counts mismatch"); goto done; }
    // Publish only the complete validated pair. Both buffers live until process exit;
    // later opens need no path lookup, and cannot switch half the pair back to PC.
    music_mpf = (X360MusicHandle){.data = converted, .size = MUSIC_MAP_SIZE};
    music_mus = (X360MusicHandle){.data = mus, .size = (size_t)st.st_size, .mus = 1};
    snprintf(why, sizeof why, "ok: LE xDFP 5.1, graph/durations match PC, 3243 stereo + 14 quad, 44100 Hz R3, %u bounded chunks through SCEl", chunks);
    ok = 1;
done:
    if (fd >= 0) close(fd);
    free(x); free(pc);
    if (!ok) {
        free(converted);
        if (mus != MAP_FAILED) munmap((void *)mus, (size_t)st.st_size);
    }
    snprintf(music_result, sizeof music_result, "%s", why);
    if (ok) music_log("X360 pair ready: MW_Music.mpf=%zu bytes (memory), MW_Music.mus=%zu bytes (unchanged file mapping), segments=%d, conversion=%s",
                      music_mpf.size, music_mus.size, MUSIC_SEGMENTS, music_result);
    else music_log("PC pair kept; conversion/validation failed: %s (extract %s)", music_result, x360_dir());
}

int x360music_open(const char *win, X360MusicHandle *out) {
    // Match both separator styles and case, including the game's sound/PFData path.
    const char *rel = win;
    size_t len = strlen(win);
    const size_t tail = sizeof("SOUND/PFDATA/MW_Music.mpf") - 1;
    if (len < tail) return 0;
    rel += len - tail;
    if (rel > win && rel[-1] != '/' && rel[-1] != '\\') return 0;
    char normalized[64];
    for (size_t i = 0; i <= tail; ++i) normalized[i] = rel[i] == '\\' ? '/' : rel[i];
    int is_mus = !strcasecmp(normalized, "SOUND/PFDATA/MW_Music.mus");
    if (!is_mus && strcasecmp(normalized, "SOUND/PFDATA/MW_Music.mpf")) return 0;
    // Extra EA Trax (extratrax.c, off by default): the chosen pair with the extra songs appended; off: unchanged.
    if (!s_x360_music.boot_value) {
        static int logged;
        if (!logged) { logged = 1; music_log("PC pair kept (NFS_X360_MUSIC=off)"); }
        return extratrax_open(is_mus, NULL, NULL, out);
    }
    pthread_once(&music_once, music_init);
    if (!music_mpf.data) return extratrax_open(is_mus, NULL, NULL, out);
    *out = is_mus ? music_mus : music_mpf;
    if (diag_on) music_log("serve %s: %zu bytes, segments=%d, conversion=ok", is_mus ? "MW_Music.mus (X360 unchanged)" : "MW_Music.mpf (X360 converted memory)", out->size, MUSIC_SEGMENTS);
    extratrax_open(is_mus, &music_mpf, &music_mus, out);
    return 1;
}
size_t x360music_read(X360MusicHandle *h, void *dst, size_t n) {
    size_t pos = (size_t)h->pos;
    size_t left = pos < h->size ? h->size - pos : 0;
    if (n > left) n = left;
    if (n) {
        if (h->read_at) n = h->read_at(h->source, dst, n, pos);
        else memcpy(dst, h->data + pos, n);
    }
    if (diag_on && h->mus && n) {
        // File reads can start before a segment (sector-aligned streaming). Find
        // SCHl headers inside the returned range; log each segment only once.
        unsigned lo = 0, hi = MUSIC_SEGMENTS;
        while (lo < hi) {
            unsigned mid = (lo + hi) / 2;
            size_t off = (size_t)le32(music_mpf.data + MUSIC_TABLE + mid * 8) << 7;
            if (off < pos) lo = mid + 1; else hi = mid;
        }
        pthread_mutex_lock(&music_diag_lock);
        for (unsigned i = lo; i < MUSIC_SEGMENTS; ++i) {
            size_t off = (size_t)le32(music_mpf.data + MUSIC_TABLE + i * 8) << 7;
            if (off + 8 > pos + n || off + le32(h->data + off + 4) > pos + n) break;
            if (!music_seen[i]) {
                music_seen[i] = 1;
                music_log("Snd stream read: segment=%u offset=%zu SCHl rate=44100 channels=%u codec=EA_ADPCM_R3 bytes=%zu", i, off, music_channels[i], n);
            }
        }
        pthread_mutex_unlock(&music_diag_lock);
    }
    h->pos += (int64_t)n;
    return n;
}
int64_t x360music_seek(X360MusicHandle *h, int64_t distance, unsigned method) {
    if (method > 2) { errno = EINVAL; return -1; }
    int64_t base = method == 0 ? 0 : method == 1 ? h->pos : (int64_t)h->size;
    if (distance < -base || distance > INT64_MAX - base) { errno = EINVAL; return -1; }
    return h->pos = base + distance;
}

// Engine sounds (A5): SOUND\\ENGINE\\GIN_*.GIN are Gnsu20 files in the same little-endian format on both platforms (87 of
// the 160 re-authored: longer sample sets, 18 at 48 kHz), so the X360 file is simply opened in place of the PC's. Their
// `engineaudio` attribute values (RPM points) come with them, see x360audio_attrib_patch.
static int x360audio_gin(const char *win, size_t len) {
    const char *base = win + len;
    while (base > win && base[-1] != '\\' && base[-1] != '/') --base;
    char rel[512], xpath[4096];
    snprintf(rel, sizeof rel, "SOUND/ENGINE/%s", base);
    int fd = x360_find(rel, xpath, sizeof xpath) ? open(xpath, O_RDONLY) : -1;
    if (diag_on) diag_event("x360audio: %s -> %s", base, fd >= 0 ? "X360 file" : "not in the X360 extract, PC file kept");
    return fd;
}

// Sound attributes (A5): the engineaudio RPM values of the X360, and (G3) the pvehicle HornType / TrafficEngType of the
// 21 traffic cars, which index sounds of the converted X360 TRAFFIC_MB/ENV_COMMON_MB banks below and so are applied only
// when both banks converted, as scalar fields of the collection's layout block (tools_snapshot/x360audio_attrib.py).
// Called from the Attrib Collection constructor hook (attrib_x360.c) with the vault's CollectionLoadData fields before
// the original runs.
#ifndef X360AUDIO_TEST
#include "gen/x360audio_attrib.inc"   // tools/gen_tables.py, from the player's files
#include "x360abk.h"

// G3: the X360's traffic engine (11 against the PC's 2) and truck sounds (SOUND\IG_GLOBAL\TRAFFIC_MB) and horns
// (ENV_COMMON_MB, ENV_STATIC: 11 against 5, HornType 12/13 exist only there). The PC reads only BNKl banks, so each is
// rebuilt at first use from the PC file and the X360 file (x360abk.c: XMA1 decoded to PCM16, the X360 sample tables
// converted) and served from memory. Both pairs must match the pinned retail files and decode to the pinned PCM, else
// both PC banks and the PC indices stay together.
static const struct { const char *rel; X360AbkPin pin; } k_g3[2] = {
    {"SOUND/IG_GLOBAL/TRAFFIC_MB.abk", {"TRAFFIC_MB", 0x87C6513Bu, 0xF41BF541u, 0x26AA3B1Cu}},
    {"SOUND/IG_GLOBAL/ENV_COMMON_MB.abk", {"ENV_COMMON_MB", 0x8E26F599u, 0xBCE7608Bu, 0x5A76F2EEu}},
};
static uint8_t *g3_bank[2];
static size_t g3_size[2];
static int g3_ok;
static pthread_once_t g3_once = PTHREAD_ONCE_INIT;

// Test switch (inert unless set): NFS_TEST_X360ABK=g3off keeps the PC traffic/env banks and indices (G3 off),
// a10off keeps every other bank PC (A10 off), so a mix-on run can be bisected by mechanism.
static int abk_test_off(const char *what) {
    const char *e = getenv("NFS_TEST_X360ABK");
    return e && strstr(e, what) != NULL;
}

static void g3_init(void) {
    char why[256] = "", pp[4096], xp[4096];
    int ok = !abk_test_off("g3off");
    if (!ok) snprintf(why, sizeof why, "NFS_TEST_X360ABK=g3off");
    for (int i = 0; i < 2 && ok; ++i) {
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        long ps = 0, xs = 0;
        uint8_t *pc = game_dir && x360audio_case_find(game_dir, k_g3[i].rel, pp, sizeof pp) ? slurp(pp, &ps) : NULL;
        uint8_t *x = x360_find(k_g3[i].rel, xp, sizeof xp) ? slurp(xp, &xs) : NULL;
        X360AbkBuildInfo info;
        if (!pc || !x) { snprintf(why, sizeof why, "%s file missing", pc ? "X360" : "PC"); ok = 0; }
        else ok = x360abk_convert_pinned(pc, ps, x, xs, &k_g3[i].pin, &g3_bank[i], &g3_size[i], &info, why, sizeof why);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (diag_on) diag_event("x360abk: %s %s: %s (%u sounds, %u sample tables, %zu bytes, %.1f ms)", k_g3[i].pin.name,
                                ok ? "converted" : "NOT converted", why, ok ? info.sounds : 0, ok ? info.tables : 0, g3_size[i],
                                (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6);
        free(pc); free(x);
    }
    if (!ok) for (int i = 0; i < 2; ++i) { free(g3_bank[i]); g3_bank[i] = NULL; }
    g3_ok = ok;
    if (diag_on) diag_event("x360abk: G3 %s", ok ? "on: X360 traffic/horn banks and HornType/TrafficEngType" : "off: PC banks and PC indices kept");
}
static int g3_ready(void) { pthread_once(&g3_once, g3_init); return g3_ok; }

// SOUND\IG_GLOBAL\TRAFFIC_MB.abk / ENV_COMMON_MB.abk: the converted bank as an unlinked temp file at offset 0, or -1.
static int g3_open(const char *win, size_t len) {
    const char *base = win + len;
    while (base > win && base[-1] != '\\' && base[-1] != '/') --base;
    int i = !strcasecmp(base, "TRAFFIC_MB.abk") ? 0 : !strcasecmp(base, "ENV_COMMON_MB.abk") ? 1 : -1;
    if (i < 0 || !g3_ready()) return -1;
    char tmp[1200]; roots_tmp_template("nfsmw_x360abk_", tmp, sizeof tmp);
    int fd = mkstemp(tmp);
    if (fd < 0) return -1;
    unlink(tmp);
    if (write(fd, g3_bank[i], g3_size[i]) != (ssize_t)g3_size[i] || lseek(fd, 0, SEEK_SET)) { close(fd); return -1; }
    if (diag_on) diag_event("x360abk: %s served converted (%zu bytes)", base, g3_size[i]);
    return fd;
}
// A10: every other bank (SOUND\...\*.abk) whose X360 counterpart has the PC's structure gets the X360's 48 kHz sounds
// (x360abk_swap_pc: the PC file with only its samples swapped, decided per bank by structure), converted at its first
// open and cached in ~/Library/Caches/NFSMW-port/x360abk, or <BOOT_USERDATA>/x360abk_cache in scratch runs (NFS_X360ABK_CACHE=<dir> overrides; x360abk_serve), so
// nothing stays in memory. Each bank is decided once per run; a bank kept PC is not tried again.
// BOOT_DIAG: one `x360abk:` line per bank (served or kept PC and why, cache, ms, bytes) with the running totals.
static pthread_mutex_t abk_lock = PTHREAD_MUTEX_INITIALIZER;
static struct { char rel[96]; } abk_pc[512];
static unsigned abk_npc, abk_served, abk_kept, abk_nreason, abk_rcount[8];
static char abk_reason[8][32];
static double abk_ms;
static size_t abk_bytes;

static int abk_open(const char *win, size_t len, const char *host) {
    const char *base = win + len;
    while (base > win && base[-1] != '\\' && base[-1] != '/') --base;
    if (!strcasecmp(base, "TRAFFIC_MB.abk") || !strcasecmp(base, "ENV_COMMON_MB.abk")) return g3_open(win, len);
    if (abk_test_off("a10off")) return -1;
    const char *r = NULL;
    for (const char *p = win; *p && !r; ++p)
        if ((p == win || p[-1] == '\\' || p[-1] == '/') && !strncasecmp(p, "SOUND", 5) && (p[5] == '\\' || p[5] == '/')) r = p;
    char rel[96], xp[4096], cache[2048], why[256];
    if (!r || strlen(r) >= sizeof rel) return -1;
    for (size_t i = 0; i <= strlen(r); ++i) rel[i] = r[i] == '\\' ? '/' : r[i];
    pthread_mutex_lock(&abk_lock);
    for (unsigned i = 0; i < abk_npc; ++i) if (!strcasecmp(abk_pc[i].rel, rel)) {
        pthread_mutex_unlock(&abk_lock);
        if (diag_on) diag_event("x360abk: %s kept PC (decided at its first open)", rel);
        return -1;
    }
    pthread_mutex_unlock(&abk_lock);
    // Guarded/gate runs set a scratch BOOT_USERDATA: their cache goes there, never into the user's Library.
    const char *env = getenv("NFS_X360ABK_CACHE"), *home = getenv("HOME"), *ud = getenv("BOOT_USERDATA");
    if (env && *env) snprintf(cache, sizeof cache, "%s", env);
    else if (roots_caches()) snprintf(cache, sizeof cache, "%s/x360abk", roots_caches());
    else if (ud && *ud) snprintf(cache, sizeof cache, "%s/x360abk_cache", ud);
    else snprintf(cache, sizeof cache, "%s/Library/Caches/NFSMW-port/x360abk", home ? home : "/tmp");
    X360AbkServe st = {0};
    int fd = -1;
    if (!x360_find(rel, xp, sizeof xp)) snprintf(why, sizeof why, "X360 file: not in the X360 extract");
    else fd = x360abk_serve(host, xp, cache, &st, why, sizeof why);
    pthread_mutex_lock(&abk_lock);
    int first = 1;
    if (fd < 0) {
        for (unsigned i = 0; i < abk_npc; ++i) if (!strcasecmp(abk_pc[i].rel, rel)) first = 0;
        if (first && abk_npc < 512) snprintf(abk_pc[abk_npc++].rel, sizeof abk_pc[0].rel, "%s", rel);
        if (first) {
            ++abk_kept;
            unsigned n = (unsigned)strcspn(why, ":"), i = 0;
            while (i < abk_nreason && (strlen(abk_reason[i]) != n || strncmp(abk_reason[i], why, n))) ++i;
            if (i == abk_nreason && abk_nreason < 8) snprintf(abk_reason[abk_nreason++], sizeof abk_reason[0], "%.*s", (int)n, why);
            if (i < 8) abk_rcount[i]++;
        }
    } else ++abk_served;
    abk_ms += st.ms;
    if (fd >= 0) abk_bytes += st.bytes;
    if (diag_on && first) {
        char tot[256];
        int l = snprintf(tot, sizeof tot, "opens served %u, banks kept PC %u (", abk_served, abk_kept);
        for (unsigned i = 0; i < abk_nreason && l < (int)sizeof tot; ++i)
            l += snprintf(tot + l, sizeof tot - l, "%s%s %u", i ? ", " : "", abk_reason[i], abk_rcount[i]);
        snprintf(tot + (l < (int)sizeof tot ? l : (int)sizeof tot - 1), sizeof tot - (l < (int)sizeof tot ? l : (int)sizeof tot - 1),
                 "), %.1f ms, %zu bytes served", abk_ms, abk_bytes);
        if (fd >= 0 && st.cached) diag_event("x360abk: %s served X360 (cache, %zu bytes, %.1f ms); %s", rel, st.bytes, st.ms, tot);
        else if (fd >= 0) diag_event("x360abk: %s served X360 (converted, %u sounds, %u placeholders kept PC, %zu bytes, %.1f ms); %s", rel,
                                     st.info.sounds, st.info.placeholders, st.bytes, st.ms, tot);
        else diag_event("x360abk: %s kept PC: %s (%.1f ms); %s", rel, why, st.ms, tot);
    }
    pthread_mutex_unlock(&abk_lock);
    return fd;
}
// A9: audioimpact `debris` (FFA56FEE) StitchCollisionVol (entry FCC8E754, 4 x int16, not a layout field: the collection's
// entry table, {key, data pointer, type, flags} x12 bytes after the type list, points at it) is 15000 on the PC, 13000 on
// the X360; the other three words are equal.
static uint16_t g16(uint32_t a) { uint16_t v; memcpy(&v, G + a, 2); return v; }
static uint32_t g32(uint32_t a) { uint32_t v; memcpy(&v, G + a, 4); return v; }
static void x360audio_collision_vol(uint32_t ld) {
    uint32_t nent = g32(ld + 0x14), ntyp = g32(ld + 0x18), e = ld + 0x20 + 4 * ntyp;
    for (uint32_t i = 0; i < nent; ++i, e += 12) {
        if (g32(e) != 0xFCC8E754u) continue;
        uint32_t p = g32(e + 4);
        if (!p || (g32(e + 8) >> 16 & 0x20) || g16(p) != 15000) {
            if (diag_on) diag_event("x360audio: attrib FFA56FEE StitchCollisionVol not patched (ptr %08X value %u)", p, p ? g16(p) : 0);
            return;
        }
        const uint16_t v13 = 13000;
        memcpy(G + p, &v13, 2);
        if (diag_on) diag_event("x360audio: attrib FFA56FEE class FBFFB107 StitchCollisionVol 15000 -> 13000 (entry at %08X)", p);
        return;
    }
}

void x360audio_attrib_patch(uint32_t key, uint32_t cls, uint32_t lay, uint32_t ld) {
    if (!s_x360_audio.boot_value) return;
    if (key == 0xFFA56FEEu && cls == 0xFBFFB107u) x360audio_collision_vol(ld);
    if (!lay) return;
    for (size_t i = 0; i < sizeof k_audio_patch / sizeof k_audio_patch[0]; ++i)
        if (k_audio_patch[i].key == key && k_audio_patch[i].cls == cls) {
            memcpy(G + lay + k_audio_patch[i].off, k_audio_patch[i].data, k_audio_patch[i].len);
            if (diag_on) diag_event("x360audio: attrib %08X class %08X +%u set to the X360's (%u bytes)", key, cls, k_audio_patch[i].off, k_audio_patch[i].len);
        }
    for (size_t i = 0; i < sizeof k_traffic_patch / sizeof k_traffic_patch[0]; ++i)
        if (k_traffic_patch[i].key == key && k_traffic_patch[i].cls == cls && g3_ready()) {
            uint8_t was = G[lay + k_traffic_patch[i].off];
            memcpy(G + lay + k_traffic_patch[i].off, k_traffic_patch[i].data, k_traffic_patch[i].len);
            if (diag_on) diag_event("x360audio: attrib %08X pvehicle %s %u -> %u (G3)", key,
                                    k_traffic_patch[i].off == 76 ? "HornType" : "TrafficEngType", was, k_traffic_patch[i].data[0]);
        }
}

#endif

// Global\InGameMemoryFile.bin is a "memory file" the game loads whole (AddMemoryFile 0065AA00, FindMemoryFileEntry
// 0065AA50): header {0, 0, magic, count} then `count` entries {name hash, offset, size, size, metadata} (0x14 bytes each, from
// +0x10), the data at the offsets. It holds the four mix maps (9996 bytes each, big-endian on X360) and the reverb
// presets (A8). FX records are already big-endian on BOTH platforms: never word-swap them.
static const uint32_t reverb_keys[] = {0xAAB5B207, 0x3A0F2F2C, 0x8F11660F, 0xBFFFF2FA};
static void store_be32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (24 - 8 * i));
}

static int memory_valid(const uint8_t *b, size_t size) {
    if (size < 16 || le32(b + 8) != 0x53219999 || le32(b + 12) > (size - 16) / 20) return 0;
    size_t end = 16 + 20 * le32(b + 12);
    for (unsigned i = 0; i < le32(b + 12); ++i) {
        const uint8_t *e = b + 16 + 20 * i;
        size_t off = le32(e + 4), len = le32(e + 8);
        if ((off & 15) || off < end || off > size || len > size - off || len != le32(e + 12)) return 0;
        end = off + len;
    }
    return 1;
}
static const uint8_t *memory_entry(const uint8_t *b, uint32_t key) {
    const uint8_t *found = NULL;
    for (unsigned i = 0; i < le32(b + 12); ++i) {
        const uint8_t *e = b + 16 + 20 * i;
        if (le32(e) == key) { if (found) return NULL; found = e; }
    }
    return found;
}

// Bounds-check the retail DSP primitives/edges before exposing a graph to the guest parser.
static int reverb_valid(const uint8_t *b, size_t size) {
    static const uint8_t widths[] = {4, 8, 0, 4, 0, 0, 4, 8, 0, 4};
    if (size < 56 || memcmp(b, "FX\x0b\0", 4) || be32(b + 4) != size) return 0;
    size_t pos = 56;
    for (unsigned ch = 0; ch < 6; ++ch) {
        unsigned nodes = be32(b + 8 + 4 * ch), edges = be32(b + 32 + 4 * ch);
        unsigned inputs = 0;
        if (!nodes && edges) return 0;
        for (unsigned i = 0; i < nodes; ++i) {
            if (pos >= size || b[pos] >= sizeof widths || !widths[b[pos]] || widths[b[pos]] > size - pos) return 0;
            inputs += b[pos] == 0;
            unsigned label = b[pos + 1];
            if (b[pos] == 9) { if ((label & 15) > ch + 1 || (label >> 4) > ch + 1) return 0; }
            else if (label > ch + 1) return 0;
            pos += widths[b[pos]];
        }
        if (nodes && inputs != 1) return 0; // retail parser scans until it finds the input node
        if (edges > (size - pos) / 12) return 0;
        for (unsigned i = 0; i < edges; ++i, pos += 12)
            if (be32(b + pos) >= nodes || be32(b + pos + 4) >= nodes ||
                b[pos + 8] < 1 || b[pos + 8] > 2 || b[pos + 9] < 1 || b[pos + 9] > 2) return 0;
    }
    return pos == size;
}

// Rebuild offsets and BOTH length fields. Unchanged entries retain their exact payloads and metadata.
// The first three presets keep PC mono/stereo and append X360 quad. Small tunnel uses X360's
// mono/stereo (identical except the two low-pass Q8 gains, 256 -> 217).
uint8_t *x360audio_merge_memory(const uint8_t *pc, size_t psz, const uint8_t *x, size_t xsz,
                              int mix, int reverb, size_t *out_size) {
    *out_size = 0;
    if (!memory_valid(pc, psz) || !memory_valid(x, xsz) || psz > UINT32_MAX - 4096) return NULL;
    const uint8_t *xp[4] = {0};
    if (reverb) for (unsigned k = 0; k < 4; ++k) {
        static const unsigned pc_sizes[] = {256, 192, 252, 256}, x_sizes[] = {268, 308, 224, 256};
        const uint8_t *pe = memory_entry(pc, reverb_keys[k]), *xe = memory_entry(x, reverb_keys[k]);
        if (!pe || !xe || le32(pe + 8) != pc_sizes[k] || le32(xe + 8) != x_sizes[k]) return NULL;
        const uint8_t *pb = pc + le32(pe + 4), *xb = x + le32(xe + 4);
        if (!reverb_valid(pb, pc_sizes[k]) || !reverb_valid(xb, x_sizes[k])) return NULL;
        // Import only the expected populated variants (selector chooses highest <= output channels).
        for (unsigned ch = 0; ch < 6; ++ch) {
            if ((be32(pb + 8 + ch * 4) != 0) != (ch < 2) ||
                (be32(xb + 8 + ch * 4) != 0) != (k == 3 ? ch < 2 : ch == 3)) return NULL;
        }
        xp[k] = xb;
    }
    // Validated original offsets are 16-byte aligned; compaction cannot grow them.
    // Three appended graphs plus their alignment padding need less than 1 KiB.
    uint8_t *out = calloc(1, psz + 4096);
    if (!out) return NULL;
    unsigned n = le32(pc + 12), merged = 0, maps = 0;
    size_t pos = (16 + 20 * n + 15) & ~(size_t)15;
    memcpy(out, pc, 16 + 20 * n);
    for (unsigned i = 0; i < n; ++i) {
        const uint8_t *pe = pc + 16 + 20 * i, *src = pc + le32(pe + 4);
        uint8_t *oe = out + 16 + 20 * i;
        unsigned len = le32(pe + 8), key = le32(pe);
        store_le32(oe + 4, (uint32_t)pos);
        memcpy(out + pos, src, len);
        if (mix && len == 9996) {
            const uint8_t *xe = memory_entry(x, key);
            if (xe && le32(xe + 8) == len) {
                put_swapped(out + pos, x + le32(xe + 4), len); ++maps;
                if (diag_on) diag_event("x360audio: mix map %08X served from the X360 file (%u bytes, word-swapped)", key, len);
            }
        }
        for (unsigned k = 0; reverb && k < 4; ++k) if (key == reverb_keys[k]) {
            if (k == 3) memcpy(out + pos, xp[k], len);
            else {
                unsigned extra = be32(xp[k] + 4) - 56;
                memcpy(out + pos + len, xp[k] + 56, extra);
                len += extra;
                store_be32(out + pos + 4, len);
                memcpy(out + pos + 8 + 3 * 4, xp[k] + 8 + 3 * 4, 4);
                memcpy(out + pos + 32 + 3 * 4, xp[k] + 32 + 3 * 4, 4);
            }
            ++merged;
            if (diag_on) diag_event("x360reverb: preset %08X merged PC=%u X360=%u bytes=%u", key,
                                   le32(pe + 8), be32(xp[k] + 4), len);
        }
        store_le32(oe + 8, len); store_le32(oe + 12, len);
        pos += len;
        if (i + 1 < n) pos = (pos + 15) & ~(size_t)15;
    }
    if (diag_on) diag_event("x360audio: Global\\InGameMemoryFile.bin with %u X360 mix maps, %u reverb presets merged (%zu bytes)", maps, merged, pos);
    *out_size = pos;
    return out;
}

// `win` is the game's path, `host` the PC file it resolved to; returns an unlinked temp file at offset 0, or -1.
int x360audio_open(const char *win, const char *host) {
    if (!s_x360_audio.boot_value && !s_x360_reverb.boot_value) return -1;
    size_t len = strlen(win);
    if (s_x360_audio.boot_value && len > 14 && !strcasecmp(win + len - 4, ".gin")) return x360audio_gin(win, len);
#ifndef X360AUDIO_TEST
    if (s_x360_audio.boot_value && len > 14 && !strcasecmp(win + len - 4, ".abk")) return abk_open(win, len, host);
#endif
    if (len < 22 || (win[len - 21] != '\\' && win[len - 21] != '/') || strcasecmp(win + len - 20, "InGameMemoryFile.bin")) return -1;
    char xpath[4096];
    long psz = 0, xsz = 0;
    uint8_t *pc = slurp(host, &psz), *x = NULL;
    if (pc && x360_find("GLOBAL/INGAMEMEMORYFILE.BIN", xpath, sizeof xpath)) x = slurp(xpath, &xsz);
    if (!pc || !x || psz < 0x210 || xsz < 0x210) {
        if (diag_on) diag_event("x360audio: InGameMemoryFile.bin: X360 file not found in %s, PC file kept", x360_dir());
        free(pc); free(x);
        return -1;
    }
    size_t size = 0;
    uint8_t *merged = x360audio_merge_memory(pc, (size_t)psz, x, (size_t)xsz,
                                            s_x360_audio.boot_value, s_x360_reverb.boot_value, &size);
    free(pc); free(x);
    if (!merged) {
        if (diag_on) diag_event("x360reverb: memory-file validation/merge failed; PC file kept");
        return -1;
    }
    char tmp[1200]; roots_tmp_template("nfsmw_x360mix_", tmp, sizeof tmp);
    int fd = mkstemp(tmp);
    if (fd < 0) { free(merged); return -1; }
    unlink(tmp);
    int ok = write(fd, merged, size) == (ssize_t)size;
    free(merged);
    lseek(fd, 0, SEEK_SET);
    if (!ok) { close(fd); return -1; }
    return fd;
}

#ifndef X360AUDIO_TEST
// NFSMixMaster::CreateMainMainMap (004F1EC0, thiscall (this, eRACETYPE)): diagnostics only; the map file is read next.
void f_004F1EC0_orig(Cpu *c);
void f_004F1EC0(Cpu *c) {
    if (diag_on) diag_event("x360audio: CreateMainMainMap(race type %u), X360 mix %s", g32(c->esp + 4), s_x360_audio.boot_value ? "on" : "off");
    uint32_t self = c->ecx;
    f_004F1EC0_orig(c);
    if (diag_on) { uint32_t b = g32(self + 0x20); diag_event("x360audio: mix map buffer %08X: %08X %08X %08X, +5F8 %08X", b, g32(b), g32(b + 4), g32(b + 8), g32(b + 0x5F8)); }
}

// SFXCTL_Tunnel::SetCurrentReverbType (004B57E0, thiscall (this, eREVERBFX type, int fade)): the tunnel reverb hold
                                                                                                                   
// BOOT_DIAG, a log line (events.log). The reverb state is this+0xA0 (type), +0x9C (fade).
// UpdateIsInTunnel (004C5C00, thiscall (this, float dt)) is wrapped to tell a leave (flag +0x28 went 1 -> 0) from other
// callers (UpdateCityVerb, EndTunnelVerb), and to end the hold once its sim time runs out.
static struct { uint32_t self; uint8_t prev; } tun_ctx;
static TunHold tun_hold;
static double tun_hold_s(void) {
    static double v = -1;
    if (v < 0) { const char *e = getenv("NFS_TUNNEL_REVERB_HOLD"); v = e && *e ? atof(e) : 0.3; if (v < 0) v = 0; }
    return v;
}
#define SIMTIME_G 0x009885D8u
void f_004B57E0_orig(Cpu *c);
void f_004B57E0(Cpu *c) {
    uint32_t t = c->ecx, type = g32(c->esp + 4); int32_t fade = (int32_t)g32(c->esp + 8);
    double now = MF32(SIMTIME_G), hold = tun_hold_s();
    const char *how = ""; int apply = 1;
    if (hold > 0) {
        if (tun_ctx.self == t && tun_ctx.prev && !M8(t + 0x28)) { if (!th_leave(&tun_hold, t, now, hold, type, fade)) { how = " HELD"; apply = 0; } }
        else if (tun_ctx.self == t && M8(t + 0x28)) { if (!th_enter(&tun_hold, t, g32(t + 0xA0), type)) { how = " HOLD ABSORBED re-entry"; apply = 0; } }
        else if (tun_hold.active && tun_hold.owner == t) { th_other(&tun_hold, t); how = " hold ended"; }
        else if (tun_hold.active) how = " (another controller's hold kept)";
    }
    if (diag_on) {
        uint32_t cs = g32(t + 0x14), car = cs ? g32(cs + 0x34) : 0;
        diag_event("x360audio: tunnel reverb type %u, fade %d (was type %u) tunnel %08X car %d zone %u in %u pos %.1f %.1f %.2f t %.3f%s",
                   type, fade, g32(t + 0xA0), t, car ? (int)g32(car + 0x228) : -1,
                   g32(t + 0x38), M8(t + 0x28), car ? MF32(car + 0x40) : 0.f, car ? MF32(car + 0x44) : 0.f, car ? MF32(car + 0x48) : 0.f, now, how);
    }
    if (apply) f_004B57E0_orig(c);
    else c->esp += 12;   // skipped: return address and the two thiscall arguments popped, as the function would
}
void f_004C5C00_orig(Cpu *c);
void f_004C5C00(Cpu *c) {
    uint32_t t = c->ecx;
    tun_ctx.self = t; tun_ctx.prev = M8(t + 0x28);
    f_004C5C00_orig(c);
    tun_ctx.self = 0;
    if (tun_hold.active && th_tick(&tun_hold, t, MF32(SIMTIME_G))) {   // hold ran out: the type the game asked for at the leave
        if (diag_on) diag_event("x360audio: tunnel reverb hold over: type %u fade %d (was type %u) tunnel %08X t %.3f",
                                tun_hold.type, tun_hold.fade, g32(t + 0xA0), t, MF32(SIMTIME_G));
        uint32_t sp = c->esp, ecx = c->ecx;
        c->esp -= 12; M32(c->esp + 8) = (uint32_t)tun_hold.fade; M32(c->esp + 4) = tun_hold.type; M32(c->esp) = 0xFEEDF00D;
        c->ecx = t;
        f_004B57E0_orig(c);   // not the hook: it would see this as another caller
        c->esp = sp; c->ecx = ecx;
    }
}

// Tunnel reverb chain (BOOT_DIAG only, 2026-10-25 "no reverb in a tunnel"). SFXCTL_Tunnel::UpdateIsInTunnel (004C5C00)
// picks eREVERBFX 5/6/3 on entering a zone 3 / 4-5 / 7-10 tunnel; SetCurrentReverbType stores it in +A0 and starts a
// fade (+78 cInterpLine, table 008F2140 row +10 ms); UpdateReflectionParams (004BEB40) moves it to +98 when that fade
// ends and sets +96, then SFXObj_Reverb::ProcessUpdate (004DEBC0) calls GlobalFxProcessor::SetCustom with the patch
// m_pFXEditPatch[+98] (00914260, names csfxedit 008EFCC0). Engine voices send tunnel+C4 x mixer input >> 23 to FX
// bus 0 via _SNDfxlevel (0081E318); the FX renderer (00820DA3) reads bus 0 and adds the wet signal to the outputs.
static const char *fx_name(uint32_t patch) {
    static char name[8][32]; static unsigned k;
    for (unsigned i = 0; i < 12; ++i) if (patch && g32(0x00914260 + 4 * i) == patch) {
        char *o = name[k++ & 7]; uint32_t s = g32(0x008EFCC0 + 4 * i); unsigned j = 0;
        for (; s && j + 1 < 32; ++j) { o[j] = (char)M8(s + j); if (!o[j]) break; }
        o[j < 32 ? j : 31] = 0;
        return o;
    }
    return "?";
}
void f_0081DA87_orig(Cpu *c);
void f_0081DA87(Cpu *c) {   // Snd::GlobalFxProcessor::SetCustom(this, patch)
    if (diag_on) diag_event("x360audio: FX patch set %s (%08X) processor %08X", fx_name(g32(c->esp + 4)), g32(c->esp + 4), c->ecx);
    f_0081DA87_orig(c);
}
static struct { unsigned calls, nonzero, max; } fxsend;
void f_0081E318_orig(Cpu *c);
void f_0081E318(Cpu *c) {   // _SNDfxlevel(voice, bus, level 0-127)
    if (diag_on && g32(c->esp + 8) == 0) {
        unsigned l = g32(c->esp + 12);
        fxsend.calls++; if (l) fxsend.nonzero++; if (l > fxsend.max && l < 1000) fxsend.max = l;
    }
    f_0081E318_orig(c);
}
static double mono_s(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }
void f_004F0330_orig(Cpu *c);
void f_004F0330(Cpu *c) {   // SFXObj_Reverb::UpdateParams: once a second, the player tunnel's reverb state
    static double next;
    uint32_t self = c->ecx, t = g32(self + 0x28), in = g32(self + 0x10);
    int log = diag_on && t && mono_s() >= next;
    uint32_t c4 = log ? g32(t + 0xC4) : 0, cc = log ? g32(t + 0xCC) : 0;
    f_004F0330_orig(c);
    if (!log) return;
    next = mono_s() + 1.0;
    uint32_t snd = g32(0x00911FA8);
    diag_event("x360audio: reverb state type %u (target %u) %s, zone %u in %u, flags 94/95/96/97/90 %u%u%u%u%u, ok %u, "
               "game %d, levels c4 %u->%u c8 %u cc %u->%u d0 %u, reverb mix input %u, fxlevel bus0 max %u nonzero %u/%u, patch %s",
               g32(t + 0x98), g32(t + 0xA0), fx_name(g32(0x00914260 + 4 * (g32(t + 0x98) & 15))), g32(t + 0x38), M8(t + 0x28),
               M8(t + 0x94), M8(t + 0x95), M8(t + 0x96), M8(t + 0x97), M8(t + 0x90), M8(self + 8),
               snd ? (int)g32(snd + 0x70) : -1, c4, g32(t + 0xC4), g32(t + 0xC8), cc, g32(t + 0xCC), g32(t + 0xD0),
               in ? g32(in) & 0x7FFF : 99999, fxsend.max, fxsend.nonzero, fxsend.calls, g32(0x00914290) ? "processor" : "none");
    fxsend.calls = fxsend.nonzero = fxsend.max = 0;
}
// FX renderer (mixer thread, cdecl (frames)): bus 0 input RMS and the wet signal it adds to the outputs (after - before).
void f_00820DA3_orig(Cpu *c);
void f_00820DA3(Cpu *c) {
    if (!diag_on) { f_00820DA3_orig(c); return; }
    static double in_sq, wet_sq, wet_peak, wet_ch[6]; static unsigned frames_acc, calls, idle;
    static float before[6][1024];
    unsigned n = g32(c->esp + 4), ch = M8(0x009C2319);
    if (n > 1024) n = 1024;
    if (ch > 6) ch = 6;
    uint32_t bus = g32(0x009C22F4), inb = bus ? g32(bus) : 0;
    if (g32(0x009C22F8) >= 2999) idle++;
    for (unsigned i = 0; inb && i < n; ++i) { float v = MF32(inb + 4 * i); in_sq += (double)v * v; }
    for (unsigned k = 0; k < ch; ++k) { uint32_t o = g32(0x009C2410 + 4 * k); for (unsigned i = 0; o && i < n; ++i) before[k][i] = MF32(o + 4 * i); }
    f_00820DA3_orig(c);
    for (unsigned k = 0; k < ch; ++k) {
        uint32_t o = g32(0x009C2410 + 4 * k);
        for (unsigned i = 0; o && i < n; ++i) { double d = MF32(o + 4 * i) - before[k][i]; wet_sq += d * d; wet_ch[k] += d * d; if (fabs(d) > wet_peak) wet_peak = fabs(d); }
    }
    frames_acc += n; calls++;
    if (frames_acc >= 48000) {
        diag_event("x360audio: FX bus0 input rms %.5f, wet out rms %.5f peak %.4f (%u ch, %u frames, %u calls, %u idle) per ch %.0f %.0f %.0f %.0f %.0f %.0f",
                   sqrt(in_sq / frames_acc), ch ? sqrt(wet_sq / ((double)frames_acc * ch)) : 0.0, wet_peak, ch, frames_acc, calls, idle,
                   sqrt(wet_ch[0] / frames_acc), sqrt(wet_ch[1] / frames_acc), sqrt(wet_ch[2] / frames_acc), sqrt(wet_ch[3] / frames_acc),
                   sqrt(wet_ch[4] / frames_acc), sqrt(wet_ch[5] / frames_acc));
        in_sq = wet_sq = wet_peak = 0; frames_acc = calls = idle = 0; memset(wet_ch, 0, sizeof wet_ch);
    }
}


// Audio Memory Pool (InitializeSoundDriver 004CC3A0: 0x563000 + 0x4000 = 5.4 MB on PC, recomp.py override at 004CC3A9).
// EAXAemsManager::InitiateLoad (004CFFA0) loads a bank into this pool and refuses it (-4) when bLargestMalloc(pool)
// is smaller than the file; there is no main-pool fallback on that path. The PC banks are EA-XA; the X360 mix serves
// 48 kHz PCM16 (A10, G3), 4-13x larger (free roam: 2.9 MB PC -> 18 MB), so with both A10 and G3 the in-game banks
// overflow the pool and the game mix goes silent. With the mix on, the pool gets AUDIO_POOL_EXTRA more.
// NFS_TEST_AUDIOPOOL=pc (test only) keeps the PC size.
enum { AUDIO_POOL_PC = 0x563000u, AUDIO_POOL_EXTRA = 0x2800000u };   // +40 MB
uint32_t x360audio_pool_size(void) {
    const char *e = getenv("NFS_TEST_AUDIOPOOL");
    uint32_t size = AUDIO_POOL_PC + (s_x360_audio.boot_value && !(e && !strcmp(e, "pc")) ? AUDIO_POOL_EXTRA : 0);
    if (diag_on) diag_event("x360audio: Audio Memory Pool %u KB (PC %u KB), X360 mix %s", (size + 0x4000u) >> 10,
                            (AUDIO_POOL_PC + 0x4000u) >> 10, s_x360_audio.boot_value ? "on" : "off");
    return size;
}

// EAXAemsManager::InitiateLoad (004CFFA0, thiscall): diagnostics only. Each bank load's file, size, result
// (-4: did not fit the Audio Memory Pool) and the pool's largest free block afterwards (pool number at 008EE558).
void f_004CFFA0_orig(Cpu *c);
void f_004CFFA0(Cpu *c) {
    uint32_t self = c->ecx;
    f_004CFFA0_orig(c);
    if (!diag_on) return;
    int32_t rc = (int32_t)c->eax;
    uint32_t req = g32(self + 0xFC), size = req ? g32(req + 0x30) : 0, eax = c->eax;
    uint32_t args[1] = {g32(0x008EE558)};
    int32_t largest = (int32_t)call_guest(c, 0x00464080, 1, args);
    c->eax = eax;
    char name[96] = "";
    for (unsigned i = 0; i + 1 < sizeof name; ++i) { name[i] = (char)M8(self + 0x10 + i); if (!name[i]) break; }
    diag_event("x360audio: bank load %s: %u bytes, type %u, result %d%s; audio pool largest free %d; buf %08X slot %08X res %08X",
               name, size, req ? g32(req) : 0, rc, rc == -4 ? " (does not fit the Audio Memory Pool)" : "", largest,
               req ? g32(req + 0x28) : 0, req ? g32(req + 0x24) : 0, req ? g32(req + 0x2C) : 0);
}

// BOOT_DIAG: where each bank's memory lives and when it is released.
static void asset_name(uint32_t a, char *out, size_t n) {
    uint32_t p = a ? g32(a + 0x14) : 0;
    size_t i = 0;
    if (p) for (; i + 1 < n; ++i) { out[i] = (char)M8(p + i); if (!out[i]) break; }
    out[i < n ? i : n - 1] = 0;
}
// Bank slots: EAXAemsManager::InitializeSlots registers the AI-car bank slots from the
// table at 00896364: type 0 160,000 bytes, type 1 140,000 bytes (PC EA-XA engine banks). A bank loaded with a slot is
// read into a pool buffer, then addmodulebank copies it into the slot through FUN_004AF3B0, a bump allocator with NO
// capacity check, and relocates its code modules there. The X360 mix's AI engine banks are 48 kHz PCM16 (CAR_70 701,784
// bytes; the largest, CAR_27/CAR_99, ~0.92 MB), so the copy ran 541 KB past the 160 KB slot into SIREN_MB (allocated
// just above it at the pool top) and overwrote SIREN's module code at 1305BF6C; the mixer thread then executed CAR_70
// sample bytes there (interp: unsupported instruction). With the mix on every slot is registered with SLOT_X360 bytes.
enum { SLOT_X360 = 0x100000u };   // 1 MB: above the largest X360 engine bank (9.6 s at 48 kHz PCM16 + header)
// EAXAemsManager::RegisterSlots (004CEA30, thiscall (type, count, p3, size, flag)).
void f_004CEA30_orig(Cpu *c);
void f_004CEA30(Cpu *c) {
    uint32_t type = arg(c, 0), count = arg(c, 1), size = arg(c, 3);
    if (s_x360_audio.boot_value && size && size < SLOT_X360) M32(c->esp + 0x10) = SLOT_X360;
    if (diag_on) diag_event("x360audio: bank slots type %u: %u x %u bytes (PC %u)", type, count, arg(c, 3), size);
    f_004CEA30_orig(c);
}

// FUN_004AF3B0 (cdecl (ctx, size)): Snd's resident-memory callback during addmodulebank; slot bump or the load buffer.
void f_004AF3B0_orig(Cpu *c);
void f_004AF3B0(Cpu *c) {
    uint32_t a = g32(0x0091236C), size = arg(c, 1), slot = a ? g32(a + 0x24) : 0, cur = slot ? g32(slot + 0xC) : 0;
    f_004AF3B0_orig(c);
    if (!diag_on) return;
    char n[64]; asset_name(a, n, sizeof n);
    uint32_t base = slot ? g32(slot + 8) : 0, cap = slot ? g32(slot + 0x10) : 0;
    diag_event("x360audio: bank resident %s: %u bytes -> %08X (slot %08X base %08X cur %08X cap %u)%s", n, size, c->eax, slot,
               base, cur, cap, slot && cur + size > base + cap ? " SLOT OVERFLOW: writes past the slot" : "");
}
// EAXAemsManager::ResolveCurrentDataMemory (004AF3E0, thiscall): frees the load buffer once a resident copy exists.
void f_004AF3E0_orig(Cpu *c);
void f_004AF3E0(Cpu *c) {
    uint32_t a = g32(0x0091236C), buf = a ? g32(a + 0x28) : 0;
    f_004AF3E0_orig(c);
    if (!diag_on) return;
    char n[64]; asset_name(a, n, sizeof n);
    diag_event("x360audio: bank resolve %s: load type %u buf %08X -> %08X (slot %08X res %08X)", n, a ? g32(a + 0x20) : 0, buf,
               a ? g32(a + 0x28) : 0, a ? g32(a + 0x24) : 0, a ? g32(a + 0x2C) : 0);
}
// EAXAemsManager::UnloadSndData (004D0A60, thiscall (index)): every bank unload (g_SndAssetList 00912858, stride 0x68).
void f_004D0A60_orig(Cpu *c);
void f_004D0A60(Cpu *c) {
    uint32_t i = arg(c, 0), a = 0x00912858u + 0x68u * i;
    if (diag_on && i < 0x30 && M8(a + 0x39)) {
        char n[64]; asset_name(a, n, sizeof n);
        diag_event("x360audio: bank unload [%u] %s: buf %08X slot %08X res %08X", i, n, g32(a + 0x28), g32(a + 0x24), g32(a + 0x2C));
    }
    f_004D0A60_orig(c);
}

#endif
