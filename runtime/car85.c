// Private verified catalog additions; legacy TEST manifests remain scratch-only.
                                                                                
// with the original Cpu restored. Valid stock data delegates unchanged OFF;
// Legacy unavailable CAR85 and invalid type IDs are rejected before access.
// Private verified-load admission preserves linked records. The public profile
                                                            
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdlib.h>
#include <strings.h>
#include <limits.h>
#include <math.h>
#include <pwd.h>
#include <unistd.h>
#include <sys/stat.h>
#include <CoreGraphics/CoreGraphics.h>
#include <CoreText/CoreText.h>
#include <CommonCrypto/CommonDigest.h>
#include "funcs.h"
#include "host.h"
#include "private_cars.h"

#define TYPES 84u
#define STRIDE 0xD0u
#define TABLE 0x009B09D8u
#define PVEHICLE 0x4A97EC8Fu
#define FRONTEND 0x85885722u
#define ECAR 0xA5B543B7u
#define NEWKEY 0xCC78C3D3u  // StringToLowerCaseKey("car85"), lookup2/ABCDEF00
#define REFSPEC 0x2B936EB7u

static int configured, enabled;
static uint32_t owned_table;
#define MAX_ADDITIONS 76u
// The order in the manifest is the numeric identity. Never sort this array.
typedef struct {
    char name[16], donor_folder[16], directory[PATH_MAX];
    unsigned catalog_id;
    uint32_t key, model_key, record, type, hash, pv, fe, menu_fe, donor_model, string, logo, brand_string;
    int wheel_fit;
    uint32_t wheel_key, wheel_donor;
    float wheel_offsets[16], wheel_compressions[2], wheel_widths[4];
    uint8_t wheel_front[6], wheel_rear[6];
    uint32_t *solid_map;
    unsigned solid_count;
} Addition;
static Addition additions[MAX_ADDITIONS];
static unsigned nadditions;
static _Thread_local Addition *model_namespace;
static uint32_t parts_hash(const char *s);
void f_00751540_orig(Cpu *c);
void f_00752940_orig(Cpu *c);
void f_00752B60_orig(Cpu *c);
void f_00755800_orig(Cpu *c);
void f_0075F5D0_orig(Cpu *c);
static int manifest_mode, catalog_mode;
static unsigned addition_id(unsigned j) { return additions[j].catalog_id ? additions[j].catalog_id : TYPES + j; }
static Addition *addition_for_type(unsigned type) {
    for (unsigned j=0;j<nadditions;++j) if (addition_id(j)==type) return &additions[j];
    return NULL;
}
static int inserting;
typedef struct { uint32_t ld, vault, original; uint64_t generation; } Source;
static Source sources[2048];
static unsigned nsources;
typedef struct { uint32_t address; uint64_t generation; int ready; } VaultLife;
static VaultLife vaults[256];
static uint64_t next_generation;

static VaultLife *life(uint32_t address) {
    for (unsigned i = 0; i < sizeof vaults / sizeof vaults[0]; ++i)
        if (vaults[i].generation && vaults[i].address == address) return &vaults[i];
    return NULL;
}

void car85_vault_unloaded(uint32_t vault) {
    VaultLife *v = life(vault);
    if (!v) return;
    uint64_t generation = v->generation;
    unsigned before = nsources;
    memset(v, 0, sizeof *v);
    for (unsigned i = 0; i < nsources; ) {
        if (sources[i].generation != generation) { ++i; continue; }
        gfree(sources[i].ld);
        memmove(sources + i, sources + i + 1, (--nsources - i) * sizeof *sources);
    }
    if (diag_on && before != nsources)
        diag_event("car85: retired vault=%08X generation=%llu sources=%u", vault,
                   (unsigned long long)generation, before - nsources);
}

static void fail(const char *why) {
    fprintf(stderr, "car85: %s\n", why);
    if (diag_on) diag_event("car85: ERROR %s", why);
    exit(2);
}

static int scratch_profile(void) {
    const char *profile = getenv("BOOT_USERDATA"), *diag = getenv("BOOT_DIAG");
    char p[PATH_MAX], d[PATH_MAX], expected[PATH_MAX], owner[PATH_MAX], home[PATH_MAX];
    if (!profile || !*profile || !diag || !*diag || !realpath(profile, p) || !realpath(diag, d)) return 0;
    struct stat ps, ds;
    if (stat(p, &ps) || stat(d, &ds) || !S_ISDIR(ps.st_mode) || !S_ISDIR(ds.st_mode)) return 0;
    struct passwd *pw = getpwuid(getuid());
    if (!pw || !realpath(pw->pw_dir, home)) return 0;
    snprintf(owner, sizeof owner, "%s/Library/Application Support/NFSMW-port", home);
    char canonical_owner[PATH_MAX];
    if (realpath(owner, canonical_owner)) strcpy(owner, canonical_owner);
    size_t n = strlen(owner);
    if (!strncmp(p, owner, n) && (!p[n] || p[n] == '/')) return 0;
    if (snprintf(expected, sizeof expected, "%s.user", d) >= (int)sizeof expected) return 0;
    // run_guarded/game_run supplies this pair; canonical comparison rejects
    // a .user symlink aimed at another profile, including the owner's saves.
    return !strcmp(p, expected);
}

int car85_profile_scratch(void) { return scratch_profile(); }

// game_run deliberately gives every launch a fresh scratch. Import exactly one
// explicitly staged scratch TEST save for restart proofs; never overwrite a
// destination or copy settings/caches/other profiles. The source is read-only.
static void seed_scratch_profile(void) {
    const char *seed = getenv("NFS_TEST_ADDCARS_SEED");
    if (!seed || !*seed) return;
    if (!scratch_profile()) fail("save seeding requires guarded scratch userdata");
    struct stat st;
    if (stat(seed, &st) || !S_ISREG(st.st_mode) || st.st_size < 36 || st.st_size > 1024 * 1024)
        fail("invalid staged scratch save");
    char path[PATH_MAX];
    const char *suffixes[] = {"NFS Most Wanted", "NFS Most Wanted/TEST"};
    for (unsigned i = 0; i < 2; ++i) {
        if (snprintf(path, sizeof path, "%s/%s", getenv("BOOT_USERDATA"), suffixes[i]) >= (int)sizeof path)
            fail("scratch save path too long");
        if (mkdir(path, 0700) && errno != EEXIST) fail("cannot create scratch save directory");
        if (lstat(path, &st) || !S_ISDIR(st.st_mode)) fail("scratch save directory is not a directory");
    }
    if (snprintf(path, sizeof path, "%s/NFS Most Wanted/TEST/TEST", getenv("BOOT_USERDATA")) >= (int)sizeof path)
        fail("scratch save path too long");
    FILE *input = fopen(seed, "rb"), *output = fopen(path, "wx");
    if (!input || !output) fail("cannot seed scratch TEST save (destination must be absent)");
    uint8_t bytes[4096]; size_t n;
    while ((n = fread(bytes, 1, sizeof bytes, input)))
        if (fwrite(bytes, 1, n, output) != n) fail("scratch save seed write failed");
    if (ferror(input)) fail("scratch save seed read failed");
    fclose(input);
    if (fclose(output)) fail("scratch save seed close failed");
    const char *metadata = getenv("NFS_TEST_ADDCARS_SEED_META");
    if (metadata && *metadata) {
        // Fresh-process WO3 proof: import only the explicitly staged record for
        // this payload. Normal admission still verifies all provenance/hashes.
        // The source save/record are read-only; both destinations must be new.
        uint8_t record[245], digest[CC_SHA256_DIGEST_LENGTH];
        FILE *meta = fopen(metadata, "rb");
        if (!meta || fread(record, 1, sizeof record, meta) != sizeof record ||
            fgetc(meta) != EOF || ferror(meta)) fail("invalid staged scratch metadata");
        fclose(meta);
        input = fopen(seed, "rb");
        if (!input || fseek(input, 36, SEEK_SET)) fail("cannot hash staged scratch payload");
        CC_SHA256_CTX hash; CC_SHA256_Init(&hash);
        while ((n = fread(bytes, 1, sizeof bytes, input))) CC_SHA256_Update(&hash, bytes, (CC_LONG)n);
        if (ferror(input)) fail("staged scratch payload read failed");
        fclose(input); CC_SHA256_Final(digest, &hash);
        char hex[65];
        for (unsigned i = 0; i < sizeof digest; ++i) snprintf(hex + 2 * i, 3, "%02x", digest[i]);
        if (snprintf(path, sizeof path, "%s/.car-catalog", getenv("BOOT_USERDATA")) >= (int)sizeof path)
            fail("scratch metadata path too long");
        if (mkdir(path, 0700) && errno != EEXIST) fail("cannot create scratch metadata directory");
        if (lstat(path, &st) || !S_ISDIR(st.st_mode)) fail("scratch metadata directory is not a directory");
        size_t used = strlen(path);
        if (snprintf(path + used, sizeof path - used, "/%s.meta", hex) >= (int)(sizeof path - used))
            fail("scratch metadata record path too long");
        output = fopen(path, "wx");
        if (!output || fwrite(record, 1, sizeof record, output) != sizeof record || fclose(output))
            fail("cannot seed scratch metadata (destination must be absent)");
        if (diag_on) diag_event("addcars: seeded scratch metadata payload=%s", hex);
    }
    if (diag_on) diag_event("addcars: seeded scratch TEST save from %s", seed);
}

static void folder(char out[16], const char *s) {
    size_t n = strlen(s);
    if (!n || n >= 16) fail("folder must contain 1..15 ASCII letters, digits or underscores");
    for (size_t i = 0; i <= n; ++i) {
        unsigned char ch = (unsigned char)s[i];
        if (ch && !((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
                    (ch >= '0' && ch <= '9') || ch == '_')) fail("invalid folder");
        out[i] = (char)toupper(ch);
    }
}

// WFT1 is emitted by tools_snapshot/wheel_attrib.py from the source ecar.
// Optional for existing manifests; legacy CAR85 never reads a sidecar.
static void load_wheel_fit(Addition *a) {
    char path[PATH_MAX];
    if (snprintf(path, sizeof path, "%s/WHEELFIT.BIN", a->directory) >= (int)sizeof path)
        fail("wheel geometry path too long");
    FILE *f = fopen(path, "rb");
    if (!f) {
        if (errno != ENOENT) fail("cannot open wheel geometry");
        if (diag_on) diag_event("addcars: wheel geometry absent name=%s (donor values retained)", a->name);
        return;
    }
    uint32_t header[4];
    int good = fread(header, 1, 16, f) == 16 &&
        fread(a->wheel_offsets, 1, 64, f) == 64 &&
        fread(a->wheel_compressions, 1, 8, f) == 8 &&
        fread(a->wheel_widths, 1, 16, f) == 16 &&
        fread(a->wheel_front, 1, 6, f) == 6 && fread(a->wheel_rear, 1, 6, f) == 6;
    good = good && fgetc(f) == EOF && !ferror(f);
    fclose(f);
    if (!good || header[0] != 0x31544657u || header[1] != 1) fail("invalid WHEELFIT.BIN");
    // Validate identities with the game's StringToLowerCaseKey at vault load.
    a->wheel_fit = 1;
    a->wheel_key = header[2]; a->wheel_donor = header[3];
    for (unsigned i = 0; i < 16; ++i)
        if (!isfinite(a->wheel_offsets[i]) || fabsf(a->wheel_offsets[i]) > 20 ||
            ((i & 3) == 3 && (a->wheel_offsets[i] < .1f || a->wheel_offsets[i] > 1)))
            fail("invalid wheel centre/radius");
    for (unsigned i = 0; i < 2; ++i)
        if (!isfinite(a->wheel_compressions[i]) || fabsf(a->wheel_compressions[i]) > 20)
            fail("invalid frontend wheel compression");
    for (unsigned i = 0; i < 4; ++i)
        if (!isfinite(a->wheel_widths[i]) || a->wheel_widths[i] < .1f || a->wheel_widths[i] > 1)
            fail("invalid render wheel width");
}

static void load_solid_map(Addition *a, const char *path) {
    FILE *f=fopen(path,"rb");
    if (!f) fail("cannot open verified solid map");
    uint32_t h[4];
    // Version 2 adds private image rows to the same sorted donor -> private table.
    if (fread(h,1,16,f)!=16 || h[0]!=0x31504D53u || (h[1]!=1 && h[1]!=2) ||
        h[2]!=parts_hash(a->name) || !h[3] || h[3]>8192) fail("invalid solid map header");
    a->solid_count=h[3]; a->solid_map=malloc(8u*h[3]);
    if (!a->solid_map || fread(a->solid_map,8,h[3],f)!=h[3] || fgetc(f)!=EOF || ferror(f))
        fail("invalid solid map size");
    fclose(f);
    for (unsigned i=1;i<a->solid_count;++i)
        if (a->solid_map[2*i]<=a->solid_map[2*i-2]) fail("unordered solid map");
}

static int on(void) {
#if defined(NFS_PUBLIC) && NFS_PUBLIC
    return 0;
#endif
    if (configured) return enabled;
    configured = 1;
    seed_scratch_profile();
    const char *legacy = getenv("NFS_TEST_CAR85"), *manifest = getenv("NFS_TEST_ADDCARS");
    if ((!legacy || !*legacy) && (!manifest || !*manifest)) {
        catalog_mode = manifest_mode = 1;
        for (size_t i=0;i<private_cars_count();++i) {
            const PrivateCar *car=private_cars_get(i);
            if (car->swap) continue;
            Addition *a=&additions[nadditions++];
            folder(a->name,car->name); folder(a->donor_folder,car->donor);
            a->catalog_id=car->id;
            snprintf(a->directory,sizeof a->directory,"%s",car->geometry);
            char *slash=strrchr(a->directory,'/');
            if (!slash) fail("verified addition has no pack directory");
            *slash=0;
            if (car->wheel_fit[0]) load_wheel_fit(a);
            if (car->solid_map[0]) load_solid_map(a,car->solid_map);
        }
        return enabled=nadditions!=0;
    }
    if (!scratch_profile()) fail("additions require guarded scratch BOOT_USERDATA=<BOOT_DIAG>.user");
    if (legacy && *legacy && manifest && *manifest) fail("use either NFS_TEST_ADDCARS or NFS_TEST_CAR85");
    if (manifest && *manifest) {
        manifest_mode = 1;
        FILE *f = fopen(manifest, "r");
        if (!f) fail("cannot open additions manifest");
        char line[PATH_MAX + 128];
        while (fgets(line, sizeof line, f)) {
            if (!strchr(line, '\n') && !feof(f)) fail("manifest line too long");
            char *r = line; while (isspace((unsigned char)*r)) ++r;
            if (!*r || *r == '#') continue;
            // Exactly three tab-separated fields; paths can contain spaces.
            char *d = strchr(r, '\t');
            if (!d) fail("manifest requires NAME<TAB>DONOR<TAB>absolute converted directory");
            *d++ = 0;
            char *path = strchr(d, '\t');
            if (!path) fail("manifest requires three fields");
            *path++ = 0; path[strcspn(path, "\r\n")] = 0;
            if (strchr(path, '\t') || nadditions == MAX_ADDITIONS) fail("invalid manifest or more than 76 additions");
            Addition *a = &additions[nadditions];
            folder(a->name, r); folder(a->donor_folder, d);
            if (path[0] != '/' || !realpath(path, a->directory)) fail("converted directory must exist and be absolute");
            const char *files[] = {"GEOMETRY.BIN", "TEXTURES.BIN"};
            for (unsigned i = 0; i < 2; ++i) {
                char file[PATH_MAX]; struct stat st;
                if (snprintf(file, sizeof file, "%s/%s", a->directory, files[i]) >= (int)sizeof file ||
                    stat(file, &st) || !S_ISREG(st.st_mode) || !st.st_size) fail("converted geometry/textures missing or empty");
            }
            for (unsigned i = 0; i < nadditions; ++i)
                if (!strcmp(a->name, additions[i].name)) fail("duplicate manifest folder");
            load_wheel_fit(a);
            ++nadditions;
        }
        if (ferror(f)) fail("manifest read failed");
        fclose(f);
    } else {
        Addition *a = &additions[nadditions++];
        strcpy(a->name, "CAR85"); folder(a->donor_folder, legacy);
    }
    return enabled = nadditions != 0;
}

uint32_t car85_count(void) {
    on(); unsigned count=nadditions?addition_id(nadditions-1)+1:TYPES;
    return count;
}

void car85_vault_begin(uint32_t address) {
    if (!on()) return;
    car85_vault_unloaded(address); // address reuse is a new lifetime, never a cache hit
    for (unsigned i = 0; i < sizeof vaults / sizeof vaults[0]; ++i)
        if (!vaults[i].generation) {
            vaults[i] = (VaultLife){address, ++next_generation, 0};
            return;
        }
    fail("live vault capacity exhausted");
}

void f_0045C380(Cpu *c) {
    car85_vault_unloaded(c->ecx); // before Deinitialize can release layouts/exports
    f_0045C380_orig(c);
}

void f_0045C090(Cpu *c) {
    car85_vault_unloaded(c->ecx); // Deinitialize releases .bin layouts before the destructor
    f_0045C090_orig(c);
}

uint32_t car85_asset_name(uint32_t address) {
    uint32_t table = M32(TABLE), count = car85_count();
    if (!table || address - table >= count * STRIDE) {
        static uint32_t empty;
        if (!empty) empty = gstr("");
        return empty;
    }
    if (on() && owned_table) {
        for (unsigned i = 0; i < nadditions; ++i) {
            uint32_t off = address - (owned_table + addition_id(i) * STRIDE);
            if (off == 0 || off == 0x10) return additions[i].record + off;
        }
    }
    return address;
}

static uint32_t parts_hash(const char *s) {
    uint32_t h = 0xFFFFFFFFu;
    while (*s) h = h * 33u + (unsigned char)*s++;
    return h;
}

static Source *source(uint32_t cls, uint32_t key);

// PC SearchForString 0056BB80: unsigned sorted {hash, packed-string pointer}
// records at 0091CF80, count at 0091CF78. LoaderLanguage 0057E960 replaces
// both; UnloaderLanguage 0056BD10 clears both and frees only its resource.
// ASCII (<128) passes WideCharHistogram::UnpackString 0056BFC0 unchanged.
static const char *manufacturer(unsigned id) {
    static const char *names[] = {"", "BMW", "FORD", "SUBARU", "PORSCHE", "AUDI",
        "MAZDA", "GENERAL_MOTORS", "DODGE", "TOYOTA", "MITSUBISHI", "MCLAREN", "MERCEDES",
        "NISSAN", "LOTUS", "LAMBORGHINI", "RENAULT", "LEXUS", "PONTIAC", "CHEVROLET",
        "VAUXHALL", "ASTONMARTIN", "VOLKSWAGEN", "FIAT", "CADILLAC", "CORVETTE",
        "CHRYSLER", "ALFAROMEO", "KOENIGSEGG", "PLYMOUTH", "INFINITI", "JAGUAR",
        "SHELBY", "PAGANI", "HONDA", "ACURA", "SEAT", "BUGATTI", "BUICK"};
    return id < sizeof names / sizeof *names ? names[id] : "";
}

static unsigned menu_manufacturer(Addition *a, uint32_t donor_layout) {
    // Frozen WO1 manifest folders, independent of handling/price donors. Retail
    // IDs 1..25 retain their meaning; new private brands follow them.
#ifndef NFS_PUBLIC
#include "car85_brands_private.h"
#else
    static const struct { const char *folders; unsigned id; } brands[] = {{" ", 0}};   // the public build has no additions
#endif
    char token[20]; snprintf(token, sizeof token, " %s ", a->name);
    for (unsigned i = 0; i < sizeof brands / sizeof *brands; ++i)
        if (strstr(brands[i].folders, token)) return brands[i].id;
    return M8(donor_layout + 0x40); // arbitrary legacy TEST manifest folder
}

void f_00581790_orig(Cpu *c);
void f_00581790(Cpu *c) {
    if (on() && manifest_mode) {
        for (unsigned j = 0; j < nadditions; ++j) {
            Addition *a = &additions[j];
            if (M32(c->ecx + 4) != a->key) continue;
            Source *fe = source(FRONTEND, a->fe);
            if (!fe) break;
            if (!a->brand_string)
                a->brand_string = gstr(manufacturer(menu_manufacturer(a, M32(fe->ld + 0x1C))));
            c->eax = a->brand_string;
            c->esp += 4;
            return;
        }
    }
    f_00581790_orig(c);
}

static int language_compare(const void *x, const void *y) {
    uint32_t a, b; memcpy(&a, x, 4); memcpy(&b, y, 4);
    return (a > b) - (a < b);
}

static void register_names(void) {
    static uint32_t owned, capacity;
    if (!on() || !manifest_mode) return;
    uint32_t table = M32(0x0091CF80u), n = M32(0x0091CF78u);
    if (!table || !n || table == owned) return;
    // Wait until every donor frontend exists; partial registration must not
    // make the owned-table idempotence check suppress a later addition.
    for (unsigned j = 0; j < nadditions; ++j)
        if (!source(FRONTEND, additions[j].fe)) return;
    if (n > 100000) fail("invalid language record count");
    uint32_t bytes = (n + 2 * nadditions) * 8;
    if (capacity < bytes) {
        if (owned) gfree(owned);
        owned = gmalloc(bytes); capacity = bytes;
    }
    memcpy(G + owned, G + table, n * 8);
    for (unsigned j = 0; j < nadditions; ++j) {
        Addition *a = &additions[j];
        Source *fe = source(FRONTEND, a->fe);
        const char *brand = manufacturer(menu_manufacturer(a, M32(fe->ld + 0x1C)));
        if (!*brand) fail("donor has no stock manufacturer name");
        char labels[2][96];
        snprintf(labels[0], sizeof labels[0], "CARNAME_%s_%s", brand, a->name);
        snprintf(labels[1], sizeof labels[1], "CAR_NAME_%s", a->name);
        for (unsigned k = 0; k < 2; ++k) {
            uint32_t hash = parts_hash(labels[k]);
            for (uint32_t i = 0; i < n; ++i)
                if (M32(owned + i * 8) == hash) fail("added name collides with language record");
            M32(owned + n * 8) = hash;
            M32(owned + n * 8 + 4) = a->string;
            ++n;
        }
        if (diag_on) diag_event("addcars: names %s %s text=%s manufacturer=%s",
            labels[0], labels[1], a->name, brand);
    }
    qsort(G + owned, n, 8, language_compare);
    M32(0x0091CF80u) = owned; M32(0x0091CF78u) = n;
}

uint32_t extra_texture_add(Cpu *c, const uint8_t rec[124], const uint8_t pl[32],
                           uint32_t fmt, uint32_t data, uint32_t size, int add);

static void publish_logo(uint32_t texture, uint32_t hash) {
    // InternalLoadTexturePackHeaderChunks 005078E0 / GetLoadedTexture 00502E80:
    // a process-lifetime pack, with one sorted {hash, TextureInfo*} index row.
    // Keep the port's already-full OtherEcstacyTextures array untouched.
    uint32_t pack = gmalloc(0x20), header = gmalloc(0x80), index = gmalloc(8);
    uint32_t name = gstr("ADDCARS_LOGOS");
    M32(pack + 8) = name; M32(pack + 0xC) = name;
    M32(pack + 0x10) = parts_hash("ADDCARS_LOGOS");
    M32(pack + 0x14) = header; M32(pack + 0x18) = 1;
    strcpy((char *)G + header + 4, "ADDCARS_LOGOS");
    strcpy((char *)G + header + 0x20, "ADDCARS_LOGOS");
    M32(header + 0x60) = M32(pack + 0x10); M32(header + 0x6C) = 1;
    M32(header + 0x70) = pack; M32(header + 0x74) = index;
    M32(index) = hash; M32(index + 4) = texture;
    M32(texture + 0x68) = pack; M32(texture + 0x6C) = texture;
    uint32_t loaded = 0x0091A0DCu + (hash & 0x1FFF);
    if (M8(loaded) == 255) fail("logo loaded-texture bucket saturated");
    ++M8(loaded); ++M32(0x0091A0D8u);
    uint32_t head = 0x009173F8u, tail = M32(head + 4);
    M32(pack) = head; M32(pack + 4) = tail;
    M32(tail) = pack; M32(head + 4) = pack;
}

// UIQRCarSelect::RefreshHeader 007ADA50 displays GetLogoHash, not a text
// label. Register a simple white nameplate under its ordinary secondary hash.
// Same TextureInfo template/upload contract as padprompts.c; pixels stay in memory.
static void register_logos(Cpu *c) {
    static uint32_t brand_logos[39];
    if (!on() || !manifest_mode) return;
    Cpu saved = *c;
    for (unsigned k = 0; k < 2 * nadditions; ++k) {
        unsigned j = k / 2;
        Addition *a = &additions[j];
        Source *fe = source(FRONTEND, a->fe);
        if (!fe) continue;
        unsigned brand = menu_manufacturer(a, M32(fe->ld + 0x1C));
        if ((k & 1) && (brand <= 25 || brand >= 39)) continue; // retail artwork
        uint32_t *texture = (k & 1) ? &brand_logos[brand] : &a->logo;
        if (*texture) continue;
        const char *label = (k & 1) ? manufacturer(brand) : a->name;
        enum { W = 512, H = 128, SIZE = W * H * 4 };
        uint32_t data = gmalloc(SIZE);
        CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
        CGContextRef g = CGBitmapContextCreate(G + data, W, H, 8, W * 4, cs,
            kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
        CGColorSpaceRelease(cs);
        if (!g) { gfree(data); continue; }
        CTFontRef font = CTFontCreateUIFontForLanguage(kCTFontUIFontEmphasizedSystem, 96, NULL);
        CFStringRef s = CFStringCreateWithCString(NULL, label, kCFStringEncodingASCII);
        CGColorRef white = CGColorCreateGenericRGB(1, 1, 1, 1);
        const void *keys[] = {kCTFontAttributeName, kCTForegroundColorAttributeName}, *values[] = {font, white};
        CFDictionaryRef attr = CFDictionaryCreate(NULL, keys, values, 2,
            &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
        CFAttributedStringRef text = CFAttributedStringCreate(NULL, s, attr);
        CTLineRef line = CTLineCreateWithAttributedString(text);
        CGRect b = CTLineGetImageBounds(line, g);
        double scale = b.size.width > W - 16 ? (W - 16) / b.size.width : 1;
        CGContextTranslateCTM(g, W / 2.0, H / 2.0);
        CGContextScaleCTM(g, scale, scale);
        CGContextSetTextPosition(g, -b.origin.x - b.size.width / 2, -b.origin.y - b.size.height / 2);
        CTLineDraw(line, g); CGContextFlush(g);
        CFRelease(line); CFRelease(text); CFRelease(attr); CGColorRelease(white); CFRelease(s); CFRelease(font);
        CGContextRelease(g);
        // White straight-alpha pixels (the draw was premultiplied).
        for (unsigned i = 0; i < W * H; ++i)
            if (M8(data + i * 4 + 3)) memset(G + data + i * 4, 255, 3);
#include "gen/padprompts_l1.inc"
        uint8_t rec[124], pl[32] = {[8] = 1};
        memcpy(rec, l1, sizeof rec);
        char name[64]; snprintf(name, sizeof name, "%s_%s",
            (k & 1) ? "CARSELECT_MANUFACTURER" : "SECONDARY_LOGO", label);
        uint32_t hash = parts_hash(name); memcpy(rec + 0x24, &hash, 4);
        snprintf((char *)rec + 0x0C, 24, "%s", name);
        rec[0x44] = 0; rec[0x45] = 2; rec[0x46] = H; rec[0x47] = 0;
        rec[0x48] = 9; rec[0x49] = 7; rec[0x4A] = 0x20;
        *texture = extra_texture_add(c, rec, pl, 21, data, SIZE, 0);
        if (*texture) publish_logo(*texture, hash);
        if (diag_on) diag_event("addcars: logo %s texture=%08X private pack (extra slots unchanged=%u)",
            name, *texture, M32(0x00915FA8u));
    }
    *c = saved;
}

// Integration-test navigation only: once the scratch career reaches free roam,
// queue the retail Lua-bound JumpToCarLot transition (00605250 -> EQuitToFE,
// context 5, Car_Select.fng). The ordinary lot builds its own stock filter.
static void car_lot_probe(Cpu *c, uint32_t row) {
    static int queued;
    if (queued || !diag_on || !on() || !manifest_mode ||
        !getenv("NFS_TEST_ADDCARS_CARLOT")) return;
    uint32_t db = M32(0x0091CF90u);
    if (!db || M32(0x00925E90u) != 6 || !(M32(db + 0x12C) & 1)) return;
    if (!row) {
        uint32_t profile = M32(db + 0x10);
        if (!profile) return;
        for (unsigned i = 0; i < 200; ++i) {
            uint32_t candidate = profile + 0x414 + i * 20;
            if (M32(candidate) != 0xFFFFFFFFu && (M32(candidate + 0xC) & 2)) { row = candidate; break; }
        }
    }
    if (!row || !(M32(row + 0xC) & 2)) return;
    // Purchase proof fixture only: the ordinary lot still checks affordability
    // and spends its actual price. Never used without the guarded lot driver.
    if (getenv("NFS_TEST_ADDCARS_PURCHASE")) {
        uint32_t profile = M32(db + 0x10);
        if (!profile) return;
        M32(profile + 0xB4) = 200000; // GetCash 0050E300: CareerSettings+0xC
        diag_event("addcars: purchase fixture cash=200000 profile=%08X", profile);
    }
    queued = 1;
    Cpu saved = *c;
    call_guest(c, 0x00605250u, 0, NULL);
    *c = saved;
    diag_event("addcars: test queued stock Game_JumpToCarLot from running scratch career");
}

void f_00756A90(Cpu *c) {
    uint32_t chunk = M32(c->esp + 4);
    f_00756A90_orig(c);
    if (M32(chunk) != 0x34600u) return;
    uint32_t src = M32(TABLE);
    if (!on()) {
        if (diag_on) diag_event("car85: types=84 switch=off table=%08X", src);
        return;
    }
    // Preflight the whole manifest before publishing the new table.
    for (unsigned j = 0; j < nadditions; ++j) {
        Addition *a = &additions[j]; uint32_t found = TYPES;
        for (uint32_t i = 0; i < TYPES; ++i) {
            const char *name = (char *)G + src + i * STRIDE;
            if (!strncasecmp(name, a->name, 16)) fail("addition collides with original folder");
            if (!strncasecmp(name, a->donor_folder, 16)) found = i;
        }
        if (found == TYPES) fail("donor absent from original 84 types");
        a->type = M32(src + found * STRIDE + 0x90);
        a->hash = M32(src + found * STRIDE + 0x50);
        a->record = found; // index until the owned table exists
        for (unsigned i = 0; i < j; ++i)
            if (parts_hash(a->name) == parts_hash(additions[i].name)) fail("addition part hash collision");
        for (unsigned i = 0; i < TYPES; ++i)
            if (parts_hash(a->name) == M32(src + i * STRIDE + 0x50)) fail("addition part hash collides with original");
    }
    if (!owned_table) owned_table = gmalloc(car85_count() * STRIDE);
    memset(G + owned_table, 0, car85_count() * STRIDE);
    for (unsigned i=TYPES;i<car85_count();++i) M32(owned_table+i*STRIDE+0x90)=0xFFFFFFFFu;
    memcpy(G + owned_table, G + src, TYPES * STRIDE);
    for (unsigned j = 0; j < nadditions; ++j) {
        Addition *a = &additions[j];
        a->record = owned_table + a->record * STRIDE;
        uint32_t extra = owned_table + addition_id(j) * STRIDE;
        memcpy(G + extra, G + a->record, STRIDE);
        memset(G + extra, 0, 32);
        strcpy((char *)G + extra, a->name); strcpy((char *)G + extra + 0x10, a->name);
        if (manifest_mode) {
            memset(G + extra + 0x20, 0, 0x30);
            snprintf((char *)G + extra + 0x20, 0x30, "CARS\\%s\\GEOMETRY.BIN", a->name);
        }
        M32(extra + 0x50) = parts_hash(a->name);
        M32(extra + 0x90) = addition_id(j);
    }
    M32(TABLE) = owned_table;
    Cpu saved = *c;
    for (unsigned j = 0; j < nadditions; ++j) {
        uint32_t hash = parts_hash(additions[j].name);
        uint32_t type = call_guest(c, 0x007398D0, 1, &hash);
        if (type != addition_id(j)) fail("GetCarType did not return manifest type");
        if (diag_on) diag_event("addcars: name=%s type=%u donor=%s donor_type=%u types=%u originals_preserved=84",
            additions[j].name, type, additions[j].donor_folder, additions[j].type, car85_count());
    }
    *c = saved;

}

// The part namespace is separate from car type IDs. Keep every donor part and
// flattened index intact; each addition uses its donor's slot overrides.
void f_007399F0(Cpu *c) {
    uint32_t at = c->esp + 8, type = M32(at);
    if (!M32(TABLE) || type >= car85_count() || (type >= TYPES && !addition_for_type(type))) {
        static uint32_t empty_slots;
        if (!empty_slots) empty_slots = gmalloc(8);
        c->eax = empty_slots; c->esp += 4; return;
    }
    if (on() && type >= TYPES) M32(at) = addition_for_type(type)->type;
    f_007399F0_orig(c);
    M32(at) = type;
    car_lot_probe(c, 0);
}
void f_00739870(Cpu *c) {
    uint32_t at = c->esp + 4, hash = M32(at);
    if (on()) for (unsigned i = 0; i < nadditions; ++i)
        if (hash == parts_hash(additions[i].name)) { M32(at) = additions[i].hash; break; }
    f_00739870_orig(c);
    M32(at) = hash;
    car_lot_probe(c, 0);
}

// PVehicle factory 00689820 looks up collision bounds by MODEL's StringKey
// hash (+18 in pvehicle layout), independently of rendering and part hashes.
void f_00681340(Cpu *c) {
    uint32_t at = c->esp + 4, key = M32(at);
    if (on()) for (unsigned i = 0; i < nadditions; ++i)
        if (additions[i].model_key && key == additions[i].model_key) { M32(at) = additions[i].donor_model; break; }
    f_00681340_orig(c);
    M32(at) = key;
}

// A donor CarPart is shared, while its solid belongs to a specific RideInfo.
// Scope only model resolution; part IDs, serialization and physics stay retail.
// Boot registration sets nadditions before model loading. With no additions,
// each wrapper tail-delegates before configuration, guest reads or TLS access.
// Keep the optional bodies out of line so call merging cannot impose their
// register-save frame on the all-MW path.
static Addition *solid_context(uint32_t ride) {
    return on() && ride ? addition_for_type(M32(ride)) : NULL;
}
static uint32_t private_solid_hash(Addition *a, uint32_t hash) {
    if (!a || !a->solid_count || !hash) return hash;
    unsigned lo=0,hi=a->solid_count;
    while (lo<hi) {
        unsigned m=lo+(hi-lo)/2; uint32_t key=a->solid_map[2*m];
        if (key<hash) lo=m+1; else hi=m;
    }
    return lo<a->solid_count && a->solid_map[2*lo]==hash ? a->solid_map[2*lo+1] : hash;
}
static __attribute__((noinline)) void model_00751540(Cpu *c) {
    f_00751540_orig(c);
    c->eax=private_solid_hash(model_namespace,c->eax);
}
void f_00751540(Cpu *c) {
    if (!nadditions) { f_00751540_orig(c); return; }
    model_00751540(c);
}
// GetUsedCarTextureInfo builds lamp/skin image names from the donor record
// and donor CarParts. The texture cache is keyed by global hash alone, so a
// resident donor image would win; map each built hash to the pack's own image.
static __attribute__((noinline)) void model_00751860(Cpu *c) {
    uint32_t info=M32(c->esp+4); Addition *a=solid_context(M32(c->esp+8));
    f_00751860_orig(c);
    if (a) for (uint32_t at=info;at<info+0xE0*4;at+=4) M32(at)=private_solid_hash(a,M32(at));
}
void f_00751860(Cpu *c) {
    if (!nadditions) { f_00751860_orig(c); return; }
    model_00751860(c);
}
static __attribute__((noinline)) void model_00752940(Cpu *c) {
    Addition *saved=model_namespace;
    model_namespace=solid_context(M32(c->esp+4));
    f_00752940_orig(c); model_namespace=saved;
}
void f_00752940(Cpu *c) {
    if (!nadditions) { f_00752940_orig(c); return; }
    model_00752940(c);
}
static __attribute__((noinline)) void model_00752B60(Cpu *c) {
    Addition *saved=model_namespace;
    model_namespace=solid_context(M32(c->ecx+0xC));
    f_00752B60_orig(c); model_namespace=saved;
}
void f_00752B60(Cpu *c) {
    if (!nadditions) { f_00752B60_orig(c); return; }
    model_00752B60(c);
}
static __attribute__((noinline)) void model_00755800(Cpu *c) {
    Addition *saved=model_namespace;
    model_namespace=solid_context(M32(c->ecx+0x84));
    f_00755800_orig(c); model_namespace=saved;
}
void f_00755800(Cpu *c) {
    if (!nadditions) { f_00755800_orig(c); return; }
    model_00755800(c);
}
static __attribute__((noinline)) void model_0075F5D0(Cpu *c) {
    Addition *saved=model_namespace;
    model_namespace=solid_context(M32(c->esp+4));
    f_0075F5D0_orig(c); model_namespace=saved;
}
void f_0075F5D0(Cpu *c) {
    if (!nadditions) { f_0075F5D0_orig(c); return; }
    model_0075F5D0(c);
}

void car85_collection_seen(uint32_t ld, uint32_t vault) {
    if (!on() || inserting) return;
    uint32_t cls = M32(ld + 4);
    if (cls != PVEHICLE && cls != FRONTEND && cls != ECAR) return;
    VaultLife *v = life(vault);
    if (!v || v->ready) return; // dynamic/non-vault collections are not load descriptors
    if (nsources == sizeof sources / sizeof sources[0]) fail("source collection capacity exhausted");
    // Vault::Initialize releases dependency zero (.vlt) after constructing its
    // exports. The descriptor/types/entries are temporary; their relocated data
    // pointers refer to the vault's persistent .bin dependency. Own metadata now.
    uint32_t bytes = 0x20 + 4 * M32(ld + 0x18) + 12 * M32(ld + 0x14);
    uint32_t copy = gmalloc(bytes);
    memcpy(G + copy, G + ld, bytes);
    for (unsigned i = 0; i < nsources; ++i)
        if (sources[i].vault == vault && M32(sources[i].ld) == M32(ld) && M32(sources[i].ld + 4) == cls) {
            gfree(sources[i].ld);
            sources[i] = (Source){copy, vault, ld, v->generation};
            return;
        }
    sources[nsources++] = (Source){copy, vault, ld, v->generation};
}

static Source *source(uint32_t cls, uint32_t key) {
    for (unsigned i = nsources; i-- > 0; ) {
        VaultLife *v = life(sources[i].vault);
        if (v && v->generation == sources[i].generation && v->ready &&
            M32(sources[i].ld) == key && M32(sources[i].ld + 4) == cls) return &sources[i];
    }
    return NULL;
}

// Find an explicit attribute, following the donor's existing parent chain.
static uint32_t entry(uint32_t ld, uint32_t key) {
    for (unsigned depth = 0; ld && depth < 32; ++depth) {
        uint32_t e = ld + 0x20 + M32(ld + 0x18) * 4;
        for (uint32_t i = 0; i < M32(ld + 0x14); ++i, e += 12)
            if (M32(e) == key) return e;
        Source *p = source(M32(ld + 4), M32(ld + 8));
        ld = p ? p->ld : 0;
    }
    return 0;
}

static uint32_t attrib_alloc(Cpu *c, uint32_t bytes) {
    uint32_t obj = M32(0x008F7EA8u), args[2] = {bytes, 0};
    c->ecx = obj;
    uint32_t p = call_guest(c, M32(M32(obj)), 2, args);
    if (!p) fail("Attrib allocation failed");
    return p;
}

static int has_addition(uint32_t vault, uint32_t cls, uint32_t key) {
    VaultLife *v = life(vault);
    if (!v || !v->ready) return 0;
    uint32_t n = M32(vault + 0x34), exp = M32(vault + 0x2C), keys = M32(vault + 0x30);
    for (uint32_t i = 0; i < n; ++i) if (M32(keys + 4 * i) == key) {
        uint32_t col = M32(exp + 16 * i), cclass = M32(col + 0x14);
        if (M32(cclass) == cls) return 1;
    }
    return 0;
}

static void apply_wheel_fit(uint32_t src, uint32_t ld, uint32_t lay, Addition *a) {
    if (!a->wheel_fit) return;
    // MW ecar layout, metres: FL/FR/RR/RL {longitudinal,lateral,up,radius}.
    // No chassis/TIRE_OFFSETS, tires, suspension or pvehicle refs are changed.
    memcpy(G + lay + 0x10, a->wheel_offsets, 64);
    memcpy(G + lay + 0xDC, a->wheel_widths, 16);
    memcpy(G + lay + 0x116, a->wheel_front, 6);
    memcpy(G + lay + 0x108, a->wheel_rear, 6);
    // FECompressions is an inherited, non-layout float[2]. Own both its entry
    // and its array; writing the pointed-to donor array would alter stock cars.
    uint32_t old = entry(src, 0x8D5BEB72u);
    if (!old || !(M8(old + 10) & 2) || (M8(old + 10) & 0x20))
        fail("unexpected donor FECompressions encoding");
    uint32_t nt = M32(ld + 0x18), ne = M32(ld + 0x14);
    uint32_t own = ld + 0x20 + nt * 4, match = 0;
    for (uint32_t i = 0; i < ne; ++i)
        if (M32(own + i * 12) == 0x8D5BEB72u) match = own + i * 12;
    if (!match) {
        memmove(G + own + 4, G + own, ne * 12);
        M32(own) = 0x3C16EC5Eu; // EA::Reflection::Float
        M32(ld + 0x18) = nt + 1;
        match = own + 4 + ne * 12;
        memcpy(G + match, G + old, 12);
        M16(match + 8) = (uint16_t)nt;
        M32(ld + 0x14) = ne + 1; M32(ld + 0xC) += 4;
    }
    uint32_t array = gmalloc(16);
    M16(array) = 2; M16(array + 2) = 2; M16(array + 4) = 4; M16(array + 6) = 0;
    memcpy(G + array + 8, a->wheel_compressions, 8);
    M32(match + 4) = array;
    if (diag_on) diag_event("addcars: wheel geometry name=%s front=(%.4f,%.4f,%.4f,r=%.4f) rear=(%.4f,%.4f,%.4f,r=%.4f) FE=(%.4f,%.4f)",
        a->name, a->wheel_offsets[0], a->wheel_offsets[1], a->wheel_offsets[2], a->wheel_offsets[3],
        a->wheel_offsets[8], a->wheel_offsets[9], a->wheel_offsets[10], a->wheel_offsets[11],
        a->wheel_compressions[0], a->wheel_compressions[1]);
}

static void append_collection(Cpu *c, Source *s, uint32_t layout_size, int vehicle, Addition *a) {
    VaultLife *v = life(s->vault);
    if (!v || !v->ready || v->generation != s->generation) return;
    uint32_t src = s->ld, vault = s->vault, cls = M32(src + 4);
    if (has_addition(vault, cls, a->key)) return;
    // Own the descriptor and layout; shared pointed-to attributes stay in the
    // same long-lived vault as the donor (physics/audio graph is unchanged).
    uint32_t nt = M32(src + 0x18), ne = M32(src + 0x14);
    uint32_t bytes = 0x20 + 4 * nt + 12 * ne;
    uint32_t ld = gmalloc(bytes + ((vehicle || (cls == ECAR && a->wheel_fit)) ? 16 : 0)), lay = gmalloc(layout_size);
    memcpy(G + ld, G + src, bytes);
    memcpy(G + lay, G + M32(src + 0x1C), layout_size);
    M32(ld) = a->key; M32(ld + 0x1C) = lay;
    if (cls == ECAR) apply_wheel_fit(src, ld, lay, a);
    if (manifest_mode && cls == FRONTEND) {
        Source *menu = source(FRONTEND, a->menu_fe);
        if (!menu) fail("price/unlock donor frontend missing");
        uint32_t policy = M32(menu->ld + 0x1C);
        M32(lay + 0x38) = M32(policy + 0x38);
        M32(lay + 0x4C) = M32(policy + 0x4C);
        M8(lay + 0x58) = M8(policy + 0x58);
        M8(lay + 0x59) = M8(policy + 0x59);
        if (!a->string) a->string = gstr(a->name);
        M32(lay + 0x18) = a->string; // frontend NameId, GetNameHash 00591160
        M8(lay + 0x40) = menu_manufacturer(a, lay);
        // Menu policy follows the explicit price donor, independent of handling.
        if (diag_on) diag_event("addcars: menu name=%s category=%u available=%u price=%u bin=%u manufacturer=%s",
            a->name, M32(lay + 0x38), M8(lay + 0x58), M32(lay + 0x4C), M8(lay + 0x59),
            manufacturer(M8(lay + 0x40)));
    }
    if (vehicle) {
        // Preserve all StringKey hashes, generated by the game's constructor.
        if (!a->string) a->string = gstr(a->name);
        c->ecx = lay + 0x10;
        call_guest(c, 0x00453760, 1, &a->string);
        a->model_key = M32(lay + 0x18);
        uint32_t old = entry(src, FRONTEND);
        if (!old || (M8(old + 10) & 0x22)) fail("unexpected donor frontend RefSpec encoding");
        uint32_t ref = gmalloc(12);
        memcpy(G + ref, G + M32(old + 4), 12);
        M32(ref + 4) = a->key; M32(ref + 8) = 0; // RefSpec {class,key,cached collection}
        uint32_t own = ld + 0x20 + 4 * nt, match = 0;
        for (uint32_t i = 0; i < ne; ++i) if (M32(own + 12 * i) == FRONTEND) match = own + 12 * i;
        if (!match) {
            // Add a RefSpec type and an explicit override if it was inherited.
            memmove(G + own + 4, G + own, 12 * ne);
            M32(own) = REFSPEC;
            M32(ld + 0x18) = nt + 1;
            match = own + 4 + 12 * ne;
            memcpy(G + match, G + old, 12);
            M16(match + 8) = (uint16_t)nt;
            M32(ld + 0x14) = ne + 1;
            M32(ld + 0xC) += 4;
        }
        M32(match + 4) = ref;
    }
    uint32_t tt = M32(vault + 4), tidx = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < M32(tt + 8); ++i)
        if (M32(M32(tt) + 8 * i) == 0x8E112EB7u) tidx = i;
    if (tidx == 0xFFFFFFFFu) fail("vault has no CollectionLoadData type");
    uint32_t n = M32(vault + 0x34), nd = M32(vault + 0x14);
    uint32_t oldblk = M32(vault + 0xC), oldkeys = M32(vault + 0x10);
    uint32_t blk = attrib_alloc(c, (nd + n + 1) * 16), keys = attrib_alloc(c, (nd + n + 1) * 4);
    memcpy(G + blk, G + oldblk, (nd + n) * 16);
    memcpy(G + keys, G + oldkeys, (nd + n) * 4);
    M32(vault + 0xC) = blk; M32(vault + 0x2C) = blk + nd * 16;
    M32(vault + 0x10) = keys; M32(vault + 0x30) = keys + nd * 4;
    uint32_t coll = attrib_alloc(c, 0x2C), args[2] = {ld, vault};
    c->ecx = coll;
    inserting = 1;
    call_guest(c, 0x00457F90, 2, args);
    inserting = 0;
    uint32_t e = blk + (nd + n) * 16;
    M32(e) = coll; M32(e + 4) = 0; M32(e + 8) = tidx; M32(e + 12) = 0;
    M32(keys + (nd + n) * 4) = a->key;
    M32(vault + 0x34) = n + 1;
    if (diag_on) diag_event("car85: collection class=%08X donor=%08X new=%08X vault=%08X generation=%llu exports=%u->%u",
                            cls, M32(src), a->key, vault, (unsigned long long)v->generation, n, n + 1);
}

void car85_vault_loaded(Cpu *c, uint32_t vault) {
    if (!on() || !vault) return;
    VaultLife *v = life(vault);
    if (!v) return; // retired generation: do not read even the vault header
    int relevant = 0;
    for (unsigned i = 0; i < nsources; ++i) relevant |= sources[i].generation == v->generation;
    if (!relevant) { car85_vault_unloaded(vault); return; }
    v->ready = 1;
    Cpu saved = *c;
    // MODEL is a StringKey at layout+10, string pointer at +1C. Do not
    // assume the pvehicle key equals the lowercase folder's Attrib hash.
    for (unsigned j = 0; j < nadditions; ++j) {
        Addition *a = &additions[j];
        if (!a->string) a->string = gstr(a->name);
        uint32_t identity = call_guest(c, 0x004549B0, 1, &a->string);
        if (a->wheel_fit == 1) {
            uint32_t donor = gstr(a->donor_folder);
            if (a->wheel_key != identity || a->wheel_donor != call_guest(c, 0x004549B0, 1, &donor))
                fail("wheel geometry name/donor mismatch");
            a->wheel_fit = 2;
        }
        a->key = identity;
        if (!a->key) fail("zero addition Attrib key");
        for (unsigned i = 0; i < j; ++i) if (a->key == additions[i].key) fail("addition Attrib key collision");
        if (source(PVEHICLE, a->key) || source(FRONTEND, a->key) || source(ECAR, a->key))
            fail("addition Attrib key collides with original collection");
        Source *pv = NULL, *price = NULL;
        // WO1 freezes MUSTANGGT's ordinary lot policy for bonus CAMARO clones.
        const char *price_folder = !strcmp(a->donor_folder, "CAMARO") ? "MUSTANGGT" : a->donor_folder;
        uint32_t donor_name = gstr(a->donor_folder), price_name = gstr(price_folder);
        uint32_t donor_key = call_guest(c, 0x004549B0, 1, &donor_name);
        uint32_t price_key = call_guest(c, 0x004549B0, 1, &price_name);
        unsigned donor_matches = 0, price_matches = 0;
        for (unsigned i = 0; i < nsources; ++i) {
            VaultLife *owner = life(sources[i].vault);
            if (!owner || !owner->ready || owner->generation != sources[i].generation) continue;
            uint32_t ld = sources[i].ld, lay = M32(ld + 0x1C);
            if (M32(ld + 4) != PVEHICLE || !lay || !M32(lay + 0x1C)) continue;
            const char *model = (char *)G + M32(lay + 0x1C);
            if (!strcasecmp(model, price_folder)) {
                ++price_matches;
                if (!price || M32(ld) == price_key) price = &sources[i];
            }
            if (!strcasecmp(model, a->donor_folder)) {
                ++donor_matches;
                if (!pv || M32(ld) == donor_key) pv = &sources[i];
            }
        }
        // Retail also has scripted/upgraded pvehicles with the same MODEL
        // (MUSTANGGT: 9B93FD4D stock, F9DC5F64 scripted). Prefer the canonical
        // folder key; a noncanonical fallback must still be unambiguous.
        if (donor_matches > 1 && M32(pv->ld) != donor_key) fail("ambiguous donor pvehicle MODEL");
        if (price_matches > 1 && M32(price->ld) != price_key) fail("ambiguous price donor pvehicle MODEL");
        if (price) {
            uint32_t e = entry(price->ld, FRONTEND);
            if (!e) fail("price donor has no frontend RefSpec");
            a->menu_fe = M32(M32(e + 4) + 4);
        }
        if (pv) {
            a->pv = M32(pv->ld);
            a->donor_model = M32(M32(pv->ld + 0x1C) + 0x18);
            uint32_t e = entry(pv->ld, FRONTEND);
            if (!e) fail("donor has no frontend RefSpec");
            a->fe = M32(M32(e + 4) + 4);
            if (pv->vault == vault) append_collection(c, pv, 0x4E, 1, a);
        }
        Source *fe = source(FRONTEND, a->fe);
        if (fe && fe->vault == vault && source(FRONTEND, a->menu_fe)) append_collection(c, fe, 0x5B, 0, a);
        Source *ec = source(ECAR, donor_key);
        if (ec && ec->vault == vault) append_collection(c, ec, 0x11D, 0, a);
    }
    *c = saved;
}

static const char *reconcile_stock(uint32_t db, unsigned *inserted);

void f_00590D40(Cpu *c) {
    uint32_t db = c->ecx;
    if (on()) {
        // Also handles FE_ATTRIB arriving before the donor's pvehicle vault.
        for (unsigned i = 0; i < sizeof vaults / sizeof vaults[0]; ++i)
            if (vaults[i].generation && vaults[i].ready) car85_vault_loaded(c, vaults[i].address);
        for (unsigned j = 0; j < nadditions; ++j) {
            Addition *a = &additions[j];
            Source *pv = source(PVEHICLE, a->pv), *fe = source(FRONTEND, a->fe);
            if (!pv || !fe || !has_addition(pv->vault, PVEHICLE, a->key) || !has_addition(fe->vault, FRONTEND, a->key))
                fail("clone registration incomplete before FEPlayerCarDB::Default");
        }
    }
    f_00590D40_orig(c);
    // Bonus handling donors (e.g. CAMARO) are not PlayerUsable in Default's
    // enumeration. Insert their ordinary stock rows through the same atomic
    // reconciliation used on profile load; never clone donor presets/ownership.
    if (on() && manifest_mode) {
        unsigned inserted;
        const char *reason = reconcile_stock(db, &inserted);
        if (reason) fail(reason);
    }
    register_names();
    register_logos(c);
    if (!diag_on) return;
    unsigned stock = 0, presets = 0, owned = 0, free_rows = 0, added = 0;
    for (unsigned i = 0; i < 200; ++i) {
        uint32_t r = db + i * 0x14;
        if (M32(r) == 0xFFFFFFFFu) { ++free_rows; continue; }
        uint32_t f = M32(r + 0xC);
        if (f & 2) ++owned;
        else if (M8(r + 0x10) != 0xFF || (f & 8)) ++presets;
        else ++stock;
        for (unsigned j = 0; j < nadditions; ++j) added += M32(r + 8) == additions[j].key;
    }
    // Default only creates fresh stock/preset rows. Subtract our identified stock
    // additions to report the baseline in this same profile configuration.
    diag_event("addcars: census before stock=%u presets=%u owned=%u free=%u used=%u (baseline excludes additions)",
        stock - added, presets, owned, free_rows + added, 200 - free_rows - added);
    diag_event("addcars: census after stock=%u presets=%u owned=%u free=%u used=%u additions=%u types=%u",
        stock, presets, owned, free_rows, 200 - free_rows, added, car85_count());
}

void f_0056F090(Cpu *c) {
    // This copy is also used outside profile admission. Never delete unavailable
    // rows here: UserProfile::LoadFromBuffer bypasses this function entirely.
    f_0056F090_orig(c);
}

static int stock_row(uint32_t row) {
    uint32_t flags = M32(row + 12) & 0xFFFFu;
    return (flags == 1 || flags == 0x20) && M16(row + 16) == 0xFFFFu;
}

// All failure paths precede all writes. The profile layout/serializer and both
// linked-record pools stay unchanged. Return a reason instead of killing the
// offline harness; admission stages this block before the retail loader runs.
static const char *reconcile_stock(uint32_t db, unsigned *inserted) {
    unsigned slots[200], free_count = 0, missing[MAX_ADDITIONS], count = 0;
    uint32_t flags[MAX_ADDITIONS];
    *inserted = 0;
    if (!on()) return NULL;
    for (unsigned i = 0; i < 200; ++i)
        if (M32(db + i * 20) == 0xFFFFFFFFu) slots[free_count++] = i;
    for (unsigned j = 0; j < nadditions; ++j) {
        Addition *a = &additions[j];
        unsigned found = 0;
        for (unsigned i = 0; i < 200; ++i) {
            uint32_t row = db + i * 20;
            if (M32(row) == 0xFFFFFFFFu || !stock_row(row)) continue;
            if (M32(row + 8) == a->key || M32(row + 4) == a->key) {
                if (M32(row + 8) != a->key || M32(row + 4) != a->key)
                    return "added stock car identity mismatch; restore the original manifest";
                ++found;
            }
        }
        if (found > 1) return "duplicate added stock cars in profile";
        if (found) continue;
        Source *pv = source(PVEHICLE, a->pv), *fe = source(FRONTEND, a->fe);
        if (!pv || !fe || !has_addition(pv->vault, PVEHICLE, a->key) ||
            !has_addition(fe->vault, FRONTEND, a->key))
            return "missing car pack; reinstall the original additions manifest and packs";
        Source *menu = source(FRONTEND, a->menu_fe);
        if (!menu) return "missing price/unlock donor frontend";
        uint32_t layout = M32(menu->ld + 0x1C), category = M32(layout + 0x38);
        // FECarRecord::Default 005815B0: category bits plus availability.
        flags[count] = category < 4 ? (0x10000u << category) : 0;
        flags[count] |= category == 3 || !M8(layout + 0x58) ? 0x20 : 1;
        missing[count++] = j;
    }
    if (free_count < count) {
        if (!catalog_mode) return "not enough free FE car rows; no additions inserted";
        fprintf(stderr,"carsaves: capacity free=%u required=%u approved=%u; existing rows retained, inserted=0\n",free_count,count,nadditions);
        if (diag_on) diag_event("carsaves: capacity free=%u required=%u approved=%u; existing rows retained, inserted=0",free_count,count,nadditions);
        return NULL;
    }
    for (unsigned j = 0; j < count; ++j)
        for (unsigned i = 0; i < 200; ++i)
            if (M32(db + i * 20) == slots[j])
                return "FE car handle collision; no additions inserted";
    for (unsigned j = 0; j < count; ++j) {
        Addition *a = &additions[missing[j]];
        uint32_t row = db + slots[j] * 20;
        memset(G + row, 0, 20);
        M32(row) = slots[j]; M32(row + 4) = a->key; M32(row + 8) = a->key;
        M32(row + 12) = flags[j]; M16(row + 16) = 0xFFFFu;
    }
    *inserted = count;
    return NULL;
}

static int catalog_matches(const PrivateCar *car, const Addition *addition) {
    if (!car || !addition || strcmp(car->name, addition->name) || strcmp(car->donor, addition->donor_folder)) return 0;
    size_t n = strlen(addition->directory);
    return n && !strncmp(car->geometry, addition->directory, n) &&
        !strcmp(car->geometry + n, "/GEOMETRY.BIN") &&
        !strncmp(car->textures, addition->directory, n) &&
        !strcmp(car->textures + n, "/TEXTURES.BIN");
}

/* Derive dependencies from serialized identities, before reconciliation adds
 * stock rows. Include all saved rows (owned, customized, stock and presets).
 * Master Off must refuse these profiles, never delete/convert their rows. */
const char *car85_profile_dependencies(Cpu *c, uint32_t db, const PrivateCar *inventory, size_t count, unsigned char used[PRIVATE_CARS_MASK_SIZE]) {
    Cpu saved=*c;
    memset(used,0,PRIVATE_CARS_MASK_SIZE);
    const char *reason=NULL;
    for(unsigned i=0;i<200;++i) {
        uint32_t row=db+i*20;
        if(M32(row)==0xFFFFFFFFu) continue;
        uint32_t args[2]={PVEHICLE,M32(row+8)};
        if(!call_guest(c,0x00455FD0,2,args)) { reason="A required car pack is missing."; break; }
        // FE_ATTRIB is deliberately unloaded before free-roam autosaves. Save
        // dependencies need only pvehicle/GetType (and the frozen type table).
        // Admission's staged preflight still validates both class identities
        // while frontend data is loaded, before any retail profile writes.
        c->ecx=row; c->esp-=4; M32(c->esp)=0; f_005816B0_orig(c);
        uint32_t type=c->eax;
        if(type>=TYPES+MAX_ADDITIONS) { reason="Unrecognized saved car identity."; break; }
        if(type>=TYPES) { unsigned k=type-TYPES; used[k/8]|=1u<<(k%8); continue; }
        uint32_t table=M32(TABLE); if(!table) continue;
        const char *name=NULL;
        for(unsigned j=0;j<TYPES;++j) if(M32(table+j*STRIDE+0x90)==type) {
            name=(const char *)G+table+j*STRIDE; break;
        }
        if(!name) continue;
        for(size_t k=76;k<count;++k) {
            const PrivateCar *car=&inventory[k];
            if(car->swap && car->selected && !strncmp(name,car->name,16)) used[k/8]|=1u<<(k%8);
        }
    }
    *c=saved; return reason;
}

// Pure preflight on a staged FE block. No profile/global state is published.
// Catalog entries have already passed WO1 output hashes; legacy TEST manifests
// cannot satisfy shipping admission merely by claiming the same numeric type.
const char *car85_profile_preflight(Cpu *c, uint32_t db, const PrivateCar *inventory, size_t count) {
    Cpu saved = *c;
    const char *reason = NULL;
    for (unsigned i = 0; i < 200 && !reason; ++i) {
        uint32_t row = db + i * 20;
        if (M32(row) == 0xFFFFFFFFu) continue;
        uint32_t args[2] = {PVEHICLE, M32(row + 8)};
        if (!call_guest(c, 0x00455FD0, 2, args)) { reason = "A required car pack is missing."; break; }
        args[0] = FRONTEND; args[1] = M32(row + 4);
        if (!call_guest(c, 0x00455FD0, 2, args)) { reason = "A required car pack is missing."; break; }
        c->ecx = row;
        c->esp -= 4; M32(c->esp) = 0;
        f_005816B0_orig(c);
        uint32_t type = c->eax;
        if (type >= car85_count()) { reason = "Missing car or part data."; break; }
        if (type >= TYPES) {
            const PrivateCar *found = NULL;
            for (size_t k = 0; k < count; ++k)
                if (!inventory[k].swap && inventory[k].selected && inventory[k].id == type) found = &inventory[k];
            if (!catalog_matches(found, addition_for_type(type))) {
                reason = "Required added car is absent from the verified catalog. Reinstall its catalog and packs."; break;
            }
        }
        // 0056EFD0/0056EFF0 iterate 75 customization and 25 career records.
        unsigned custom = M8(row + 16), career = M8(row + 17);
        if (career != 0xFF && career >= 25) { reason = "Invalid career car link."; break; }
        if (custom == 0xFF) continue;
        if (custom >= 75) { reason = "Invalid customization link."; break; }
        uint32_t record = db + 4000 + custom * 0x198;
        for (unsigned slot = 0; slot < 139; ++slot) {
            uint32_t index = (uint32_t)(int32_t)(int16_t)M16(record + slot * 2);
            if (index == 0xFFFFFFFFu) continue;
            c->ecx = 0x009B26A8u;
            if (!call_guest(c, 0x00747C00, 1, &index)) { reason = "Missing installed part data."; break; }
        }
    }
    // Reconciliation must not introduce an unverified TEST-manifest identity.
    for (unsigned j = 0; !reason && j < nadditions; ++j) {
        const PrivateCar *found = NULL;
        for (size_t k = 0; k < count; ++k)
            if (!inventory[k].swap && inventory[k].selected && inventory[k].id == addition_id(j)) found = &inventory[k];
        if (!catalog_matches(found, &additions[j]))
            reason = "Active additions do not match the verified catalog. Restore its manifest and packs.";
    }
    unsigned inserted = 0;
    if (!reason) reason = reconcile_stock(db, &inserted);
    *c = saved;
    return reason;
}

void f_005816B0(Cpu *c) {
    uint32_t row = c->ecx;
    register_names(); // also refresh after language resource replacement
    register_logos(c);
    if (!on() && M32(c->ecx + 8) == NEWKEY) {
        c->eax = 0xFFFFFFFFu; c->esp += 4; return;
    }
    f_005816B0_orig(c);
    if (c->eax >= car85_count()) c->eax = 0xFFFFFFFFu;
    car_lot_probe(c, row);
}

void f_00739920(Cpu *c) {
    if (!M32(TABLE) || M32(c->esp + 4) >= car85_count() ||
        (M32(c->esp + 4) >= TYPES && !addition_for_type(M32(c->esp + 4)))) {
        c->eax = 0; c->esp += 4; return;
    }
    f_00739920_orig(c);
}

int car85_path(const char *guest, char *host, size_t n) {
    if (!on()) return 0;
    char rel[1024];
    if (strlen(guest) >= sizeof rel) return 0;
    strcpy(rel, guest);
    for (char *p = rel; *p; ++p) if (*p == '\\') *p = '/';
    char *r = rel;
    if (strlen(r) > 2 && r[1] == ':') r += 2;
    while (*r == '/') ++r;
    if (!strncasecmp(r, "NFSMW/", 6)) r += 6;
    if (strncasecmp(r, "CARS/", 5)) return 0;
    Addition *a = NULL; const char *tail = NULL;
    for (unsigned i = 0; i < nadditions; ++i) {
        size_t len = strlen(additions[i].name);
        if (!strncasecmp(r + 5, additions[i].name, len) && r[5 + len] == '/') {
            a = &additions[i]; tail = r + 6 + len; break;
        }
    }
    if (!a) return 0;
    if (!*tail) return 0;
    // Converted files are limited to the two validated, donor-prefixed packs.
    // Other resources keep the donor's original namespace and order.
    if (a->directory[0] && (!strcasecmp(tail, "GEOMETRY.BIN") || !strcasecmp(tail, "TEXTURES.BIN"))) {
        const char *file = !strcasecmp(tail, "GEOMETRY.BIN") ? "GEOMETRY.BIN" : "TEXTURES.BIN";
        int len = snprintf(host, n, "%s/%s", a->directory, file);
        if (len < 0 || (size_t)len >= n) return 0;
        if (diag_on) diag_event("addcars: redirect %s -> %s", guest, host);
        return 1;
    }
    // Resolve the donor path case-insensitively, matching boot.c's resolver.
    char path[1200];
    snprintf(path, sizeof path, "CARS/%s/%s", a->donor_folder, tail);
    char result[4096];
    snprintf(result, sizeof result, "%s", game_dir);
    char *save = NULL;
    for (char *tok = strtok_r(path, "/", &save); tok; tok = strtok_r(NULL, "/", &save)) {
        if (!strcmp(tok, ".") || !strcmp(tok, "..")) return 0;
        DIR *d = opendir(result);
        char component[1024];
        snprintf(component, sizeof component, "%s", tok);
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) if (!strcasecmp(e->d_name, tok)) {
                snprintf(component, sizeof component, "%s", e->d_name); break;
            }
            closedir(d);
        }
        size_t used = strlen(result);
        int wrote = snprintf(result + used, sizeof result - used, "/%s", component);
        if (wrote < 0 || (size_t)wrote >= sizeof result - used) return 0;
    }
    if (strlen(result) >= n) return 0;
    strcpy(host, result);
    if (diag_on) diag_event("car85: redirect %s -> %s", guest, result);
    return 1;
}
