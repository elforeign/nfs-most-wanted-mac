// attrib_x360.c - X360 gameplay data laid over the PC's Attrib vaults. The two
// platforms' vaults are the same game, but the X360 has a few collections the PC lacks or cuts down. This overlay
// takes the X360 versions, byte-swapped to little-endian offline (tools_snapshot/attrib_x360.py -> attrib_x360.inc),
// and applies them while the vaults load; nothing is read from the X360 files at run time and no game file is touched.
//
// Layout arrays (R22, asphalt tyre smoke): Attrib::Collection::Collection (00457F90) builds a Collection from the
//   vault's CollectionLoadData ({key, class, ..., +0x1C: the collection's layout block, already relocated by
//   Vault::Initialize}). Before the original runs, a collection listed in k_layout_patch gets the X360 contents of one
//   array field written over its layout block (header {capacity, count, element size, flags} + capacity elements).
//   The class definitions are identical on both platforms (offset, element size, capacity 6), so the array grows in
//   place: the emittergroup's `Emitters` for fxtd_sl_asphalt (slide) 1 -> 5, fxtd_sk_asphalt (skid) 3 -> 5,
//   fxtd_sk_asphalt_no_leaves 2 -> 4 and fxtd_dr_asphalt_leaves (drift) 1 -> 2 emitters (+ fast, extra, dark smoke,
//   leaves, drift smoke; every referenced emitterdata is already in the PC vault).
//
// New collections (G6, drag-race traffic): the X360's five drag-race vaults (1_7_3, 3_7_3, 4_7_4, 7_7_1_r, 10_7_3) hold
//   32 gameplay collections the PC's lack (15 trafficspawntrigger, a character, a car marker, ...), which the PC's race
//   code finds by walking the vault's exports (FindInstances 005F3D90). Attrib::Vault::Initialize (00457670) is hooked:
//   when the vault just built is one of the five (export count + first and last export key), the little-endian
//   CollectionLoadData images from k_g6 are copied to guest memory (pointers relocated), a Collection is built from each
//   with the game's own constructor (parents first) and appended to the vault's export tables, so everything that
//   enumerates the vault or looks the collection up by key finds it.
// Switch: gameplay.x360_data (NFS_X360_DATA, default on, restart): off leaves the PC collections as they are.
// BOOT_DIAG: `attrib_x360:` lines (each patched collection; emitter groups added to the particle system and, once a
//   second while smoke is alive, the live emitter and particle counts of the EmitterSystem's groups).
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "funcs.h"
#include "host.h"
#include "settings.h"
#include "materials.h"

#include "gen/attrib_x360.inc"          // tools/gen_tables.py, from the player's files
#include "gen/attrib_light_data.inc"
#include "gen/x360_shader_lits.inc"     // X360F_GRASS_*: the 04_grass effect defaults (curl, shell/noise tables), ZZDATA0
#include "gen/x360_engine.inc"           // X360E_STEER_LOW_WORD: the X360 steering dead zone, from the player's ATTRIBUTES.BIN
#include "gen/grass_x360.inc"            // GRASS_X360_VAULT: the vault fields below

// R27, decoded X360 ATTRIBUTES.BIN lightmaterials D32A743F, child A79251B8
// (grasstype1), file 0x79470. Never register/inject this X360-only guest class.
// Layout: colour +00, scruff +4C, low space +50, noise bias +54, LOD +58,
// low intensity +60, shells (FLOAT) +64, diffuse bias +6C, height +70,
// diffuse space +78; NOISESPACE is explicit attribute 3A417EAE (generated from
// the player's file, tools_snapshot/pubtables_gen.py). Tables/curl are effect
// defaults (04_grass); parent 1BA4DDEA has different values.
const MatGrassParams *attrib_x360_grass(uint32_t material_key) {
#ifdef GRASS_X360_VAULT
    static const MatGrassParams p = {
        GRASS_X360_VAULT, .curl = X360F_GRASS_CURL,
        .ambient_shell = X360F_GRASS_AMBIENT_SHELL,
        .ambient_noise = X360F_GRASS_AMBIENT_NOISE,
        .diffuse_shell = X360F_GRASS_DIFFUSE_SHELL
    };
    return material_key == 0xA79251B8u ? &p : NULL;
#else
    (void)material_key;
    return NULL;                                                // no X360 copy: the PC grass
#endif
}

#define EMITTER_SYSTEM_LIST 0x378u          // EmitterSystem+0x378: bList of EmitterGroups (+0x390 their count)

static Setting s_x360_data = {.id = "gameplay.x360_data", .category = SET_GAMEPLAY, .order = 80, .label = "X360 Game Data",
    .help = "The X360 version's extra tyre smoke emitters on asphalt.",
    .type = SET_TOGGLE, .def = 1, .env = "NFS_X360_DATA", .restart = 1};

static const char *const kLightData[] = {"PC", "X360"}, *const kLightDataV[] = {"pc", "x360"};
static Setting s_light_data = {.id = "graphics.lighting_data", .category = SET_WORLD, .order = 20, .label = "Lighting data",
    .help = "PC keeps the original lighting and flares; X360 uses the console's time-of-day colours and flares without the PC's doubled flare alpha.",
    .type = SET_CHOICE, .choices = kLightData, .values = kLightDataV, .nchoices = 2,
    .def = 1, .env = "NFS_LIGHT_DATA", .restart = 1};

// The X360 label and help carry the dead zone read from the player's data (filled in at registration).
static char steer_x360_label[24] = "X360", steer_help[160] =
    "Type 1 analog steering: PC has no dead zone, X360 uses the console's dead zone, and keyboard steering stays binary.";
static const char *kSteerDeadzone[] = {"PC (none)", steer_x360_label};
static Setting s_steer_deadzone = {.id = "controls.steering_dead_zone", .category = SET_CONTROLS, .order = 110,
    .label = "Steering dead zone",
    .help = steer_help,
    .type = SET_CHOICE, .choices = kSteerDeadzone, .values = kLightDataV, .nchoices = 2,
    .def = 0, .env = "NFS_STEER_DEADZONE", .restart = 1};

__attribute__((constructor)) static void attrib_x360_register(void) {
    uint32_t w = X360E_STEER_LOW_WORD;
    if (w) {
        float low;
        memcpy(&low, &w, sizeof low);
        snprintf(steer_x360_label, sizeof steer_x360_label, "X360 (%.0f %%)", low * 100.0f);
        snprintf(steer_help, sizeof steer_help, "Type 1 analog steering: PC has no dead zone, X360 rescales the range "
                 "above %.0f%%, and keyboard steering stays binary.", low * 100.0f);
    }
    settings_add(&s_x360_data);
    settings_add(&s_light_data);
    settings_add(&s_steer_deadzone);
}

// Also used by the single-instruction override at guest 00505B4C. Use the
// startup value for both data and arithmetic even after an F10 pending change.
int attrib_x360_light_data(void) { return s_light_data.boot_value == 1; }

static unsigned light_seen, light_records[2], light_fields[2];

static void light_data_patch(uint32_t key, uint32_t cls, uint32_t lay) {
    if (cls == 0x399ED882u || cls == 0xC7C5806Du) ++light_seen;
    if (!lay || !attrib_x360_light_data()) return;
    int record = 0, group = cls == 0x399ED882u ? 0 : 1;
    for (size_t i = 0; i < sizeof k_light_patch / sizeof k_light_patch[0]; ++i) {
        if (k_light_patch[i].key != key || k_light_patch[i].cls != cls) continue;
        memcpy(G + lay + k_light_patch[i].off, k_light_patch[i].data, k_light_patch[i].len);
        ++light_fields[group];
        record = 1;
    }
    light_records[group] += record;
}

static int patched;

// I5: controller/drive_analog has explicit array attributes, NOT layout fields.
// CollectionLoadData +20h: types[numTypes], then 12-byte attribute entries.
// Each steering array has two 32-byte ControllerDataRecords: GAME_ACTION_* and
// SC_LEFT/RIGHT (keyboard). Change only the first record's low (+14h); retain
// sources, mode (+10h), high (+18h), keyboard record, pedals and TURN edge actions.
// InputMapping (006479D0) copies these into the 00642C90 linear ramp at startup.
#define STEER_RAMP_LOW 0x14u
static void steer_deadzone_patch(uint32_t ld) {
    if (M32(ld) != 0xAD37E45Au || M32(ld + 4) != 0x2DEE1998u) return;
    unsigned records = 0, written = 0;
    uint32_t value = 0;
    uint32_t entries = ld + 0x20 + 4 * M32(ld + 0x18);
    for (uint32_t i = 0; i < M32(ld + 0x14); ++i) {
        uint32_t entry = entries + 12 * i, key = M32(entry);
        uint32_t scalar = key == 0x37B1B6A8u ? 0x10C7B791u :
                          key == 0xB3A37C41u ? 0x053184B6u : 0;
        if (!scalar || (M8(entry + 10) & 0x22) != 2) continue;
        uint32_t array = M32(entry + 4);
        if (!array || M16(array + 2) != 2 || M16(array) < 2 || M16(array + 4) != 32) continue;
        uint32_t record = array + 8 + ((M16(array + 6) & 0x8000) ? 8 : 0);
        if (M32(record + 8) != scalar || M32(record + 0x10) != 0) continue;
        ++records;
        if (s_steer_deadzone.boot_value == 1 && X360E_STEER_LOW_WORD) {   // 0 = no X360 data: the PC dead zone stays
            M32(record + STEER_RAMP_LOW) = X360E_STEER_LOW_WORD;
            ++written;
        }
        value = M32(record + STEER_RAMP_LOW);
    }
    static int reported;
    if (diag_on && !reported) {
        diag_event("steering_deadzone: option=%s record=AD37E45A matched=%u patched=%u low=%.2f bits=%08X",
                   s_steer_deadzone.boot_value == 1 ? "x360" : "pc", records, written,
                   (double)((union { uint32_t u; float f; }){.u = value}).f, value);
        reported = 1;
    }
}

void f_00457F90_orig(Cpu *c);
void x360audio_attrib_patch(uint32_t key, uint32_t cls, uint32_t lay, uint32_t ld);
void f_00457F90(Cpu *c) {
    steer_deadzone_patch(M32(c->esp + 4));
    x360audio_attrib_patch(M32(M32(c->esp + 4)), M32(M32(c->esp + 4) + 4), M32(M32(c->esp + 4) + 0x1C), M32(c->esp + 4));   // sound attributes (x360audio.c)
    uint32_t ld_light = M32(c->esp + 4);
    light_data_patch(M32(ld_light), M32(ld_light + 4), M32(ld_light + 0x1C));
    if (s_x360_data.boot_value) {
        uint32_t ld = M32(c->esp + 4), key = M32(ld), cls = M32(ld + 4), lay = M32(ld + 0x1C);
        for (size_t i = 0; lay && i < sizeof k_layout_patch / sizeof k_layout_patch[0]; ++i)
            if (k_layout_patch[i].key == key && k_layout_patch[i].cls == cls) {
                memcpy(G + lay + k_layout_patch[i].off, k_layout_patch[i].data, k_layout_patch[i].len);
                ++patched;
                if (diag_on) diag_event("attrib_x360: collection %08X (class %08X) array at +%u replaced by the X360's (%u of %u)",
                                        key, cls, k_layout_patch[i].off, patched, (unsigned)(sizeof k_layout_patch / sizeof k_layout_patch[0]));
            }
    }
    f_00457F90_orig(c);
}

// Attrib::Vault::Vault members: +4 the ExportManager (type table {hash, handler} at *+0, count at +8), +0xC the
// dependency + export block (16-byte entries), +0x10 the key block (4 bytes), +0x14 dependency count, +0x2C export
// entries {data, 0, type index, 0}, +0x30 export keys, +0x34 export count. The export data of a collection is its Collection.
#define ALLOCATOR 0x008F7EA8u               // AttribAlloc::mAllocator: object whose vtable slot 0 is alloc(size, flags)
#define T_COLLECTION_LOAD_DATA 0x8E112EB7u  // StringHash32("Attrib::CollectionLoadData")

static uint32_t attrib_alloc(Cpu *c, uint32_t n) {
    uint32_t obj = M32(ALLOCATOR), args[2] = {n, 0}, esp = c->esp, ecx = c->ecx;
    c->ecx = obj;
    uint32_t r = call_guest(c, M32(M32(obj)), 2, args);
    c->esp = esp; c->ecx = ecx;
    return r;
}

static uint32_t g6_image[sizeof k_g6 / sizeof k_g6[0]];   // guest copy of each vault's images, made once and refreshed per load

// Diag: gameplay collections per template (trafficspawntrigger 81372B00, character FF4FE4B0) in a vault.
static void g6_count(uint32_t vault, const char *name, const char *when) {
    uint32_t trig = 0, chr = 0, tot = M32(vault + 0x34);
    for (uint32_t i = 0; i < tot; ++i) {
        uint32_t col = M32(M32(vault + 0x2C) + 16 * i), par = col ? M32(col + 0x10) : 0, pk = par ? M32(par + 0x20) : 0;
        trig += pk == 0x81372B00u; chr += pk == 0xFF4FE4B0u;
    }
    diag_event("attrib_x360: vault %s %s: %u trafficspawntrigger and %u character collections", name, when, trig, chr);
}

void f_00457670_orig(Cpu *c);
void f_00457670(Cpu *c) {
    uint32_t vault = c->ecx;
    unsigned seen_before = light_seen, records_before[2] = {light_records[0], light_records[1]},
             fields_before[2] = {light_fields[0], light_fields[1]};
    f_00457670_orig(c);
    if (diag_on && light_seen != seen_before) {
        uint32_t vt = M32(0x00901828u);  // g_VisualTreatment, queried by 006C0A20
        diag_event("lighting_data: option=%s vault=%08X records 399ED882=%u C7C5806D=%u fields=%u/%u g_VisualTreatment=%u active_quartet=%s",
                   attrib_x360_light_data() ? "x360" : "pc", vault,
                   light_records[0] - records_before[0], light_records[1] - records_before[1],
                   light_fields[0] - fields_before[0], light_fields[1] - fields_before[1], vt,
                   vt ? "3DCFA673/B902CFAD/59C373C3/D835D0C8" : "A01A3942/0E1C797C/94ABDFF0/919B6689");
    }
    if (!vault) return;
    uint32_t n = M32(vault + 0x34), keys = M32(vault + 0x30);
    if (!n || !keys) return;
    uint32_t k0 = M32(keys), k1 = M32(keys + 4 * (n - 1));
    size_t v = 0;
    while (v < sizeof k_g6 / sizeof k_g6[0] && !(k_g6[v].nexp == n && k_g6[v].k0 == k0 && k_g6[v].k1 == k1)) ++v;
    if (v == sizeof k_g6 / sizeof k_g6[0]) return;
    if (diag_on) g6_count(vault, k_g6[v].name, "as the PC has it");
    if (!s_x360_data.boot_value) return;
    Cpu saved = *c;
    uint32_t tt = M32(vault + 4), tcount = M32(tt + 8), tarr = M32(tt), tidx = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < tcount; ++i) if (M32(tarr + 8 * i) == T_COLLECTION_LOAD_DATA) { tidx = i; break; }
    uint32_t ndep = M32(vault + 0x14), add = k_g6[v].ncoll;
    if (tidx == 0xFFFFFFFFu) { if (diag_on) diag_event("attrib_x360: %s: no CollectionLoadData export type", k_g6[v].name); return; }
    if (!g6_image[v]) g6_image[v] = gmalloc(k_g6[v].size);
    uint32_t img = g6_image[v];
    memcpy(G + img, k_g6[v].blob, k_g6[v].size);
    for (uint32_t i = 0; i < k_g6[v].nreloc; ++i) M32(img + k_g6[v].reloc[i]) += img;
    // grow the two blocks (dependency + export entries, keys) so the vault's destructor frees them with the right sizes
    uint32_t blk = attrib_alloc(c, (ndep + n + add) * 16), kblk = attrib_alloc(c, (ndep + n + add) * 4);
    memcpy(G + blk, G + M32(vault + 0xC), (ndep + n) * 16);
    memcpy(G + kblk, G + M32(vault + 0x10), (ndep + n) * 4);
    M32(vault + 0xC) = blk; M32(vault + 0x2C) = blk + ndep * 16;
    M32(vault + 0x10) = kblk; M32(vault + 0x30) = kblk + ndep * 4;
    uint32_t built = 0;
    for (uint32_t i = 0; i < add; ++i) {
        uint32_t ld = img + k_g6[v].coll[i], key = M32(ld);
        uint32_t coll = attrib_alloc(c, 0x2c), args[2] = {ld, vault};
        c->ecx = coll;
        call_guest(c, 0x00457F90, 2, args);
        uint32_t e = M32(vault + 0x2C) + 16 * (n + built);
        M32(e) = coll; M32(e + 4) = 0; M32(e + 8) = tidx; M32(e + 12) = 0;
        M32(M32(vault + 0x30) + 4 * (n + built)) = key;
        M32(vault + 0x34) = n + ++built;                // visible to the next collection's parent lookups only through the class
        if (diag_on) diag_event("attrib_x360: %s: collection %08X added (parent %08X, %u entries)", k_g6[v].name, key, M32(ld + 8), M32(ld + 0x14));
    }
    if (diag_on) g6_count(vault, k_g6[v].name, "now");
    if (diag_on) diag_event("attrib_x360: vault %s gained %u X360 collections (%u -> %u exports)", k_g6[v].name, built, n, n + built);
    *c = saved;
}

// FindInstances (005F3D90 / 005F3F10, cdecl(a, b, int *instances, int *connections)): walks the vault's CollectionLoadData
// exports that are instances of the template (a) and adds their count to *instances. Diag: that count per call, so the
// drag races' extra triggers can be counted.
static void find_instances_diag(Cpu *c, void (*orig)(Cpu *), uint32_t fn) {
    uint32_t a = M32(c->esp + 4), pn = M32(c->esp + 12), vault = a ? M32(a) : 0, nexp = vault ? M32(vault + 0x34) : 0;
    uint32_t before = pn ? M32(pn) : 0;
    orig(c);
    if (diag_on && nexp > 45 && (pn || fn == 0x5F3D90))
        diag_event("attrib_x360: FindInstances %08X over a vault of %u exports: %u instances%s, block size %u", fn, nexp, pn ? M32(pn) - before : 0, pn ? "" : " (not counted)", c->eax);
}
void f_005F3D90_orig(Cpu *c);
void f_005F3D90(Cpu *c) { find_instances_diag(c, f_005F3D90_orig, 0x5F3D90); }
void f_005F3F10_orig(Cpu *c);
void f_005F3F10(Cpu *c) { find_instances_diag(c, f_005F3F10_orig, 0x5F3F10); }

// AITrafficManager::SpawnTraffic (thiscall, returns bool): diag only, the traffic cars it has put on the road.
void f_00439A10_orig(Cpu *c);
void f_00439A10(Cpu *c) {
    f_00439A10_orig(c);
    static uint32_t calls, spawned;
    ++calls;
    if (!diag_on) return;
    if (c->eax & 0xFF) { ++spawned; diag_event("attrib_x360: traffic car spawned (%u spawned, %u attempts)", spawned, calls); }
}

// Diagnostics only (BOOT_DIAG): the tyre smoke's effect on the particle system.
static uint32_t group_emitters(uint32_t g) {
    uint32_t n = 0;
    for (uint32_t e = M32(g + 8); e && e != g + 8 && n < 64; e = M32(e)) ++n;
    return n;
}
static uint32_t group_particles(uint32_t g) {
    uint32_t n = 0, k = 0;
    for (uint32_t e = M32(g + 8); e && e != g + 8 && k < 64; e = M32(e), ++k) n += M16(e + 0x1C);
    return n;
}
static uint32_t groups_added, emitters_added;

void f_00503C80_orig(Cpu *c);
void f_00503C80(Cpu *c) {
    uint32_t grp = M32(c->esp + 4);
    f_00503C80_orig(c);
    if (!diag_on) return;
    uint32_t n = group_emitters(grp);
    ++groups_added; emitters_added += n;
    diag_event("attrib_x360: emitter group %08X added with %u emitters (total %u groups, %u emitters)", grp, n, groups_added, emitters_added);
}

// EmitterSystem::CreateEmitterGroup (0050C7B0, thiscall(Collection *, uint)): each request by collection key, so the
// smoke groups show up by name (the first request of every key, then every tyre smoke one with the emitters it got).
void f_0050C7B0_orig(Cpu *c);
static uint32_t smoke_requests, smoke_emitters;
void f_0050C7B0(Cpu *c) {
    uint32_t coll = M32(c->esp + 4), key = diag_on && coll ? M32(coll + 0x20) : 0;
    f_0050C7B0_orig(c);
    if (!diag_on) return;
    static uint32_t seen[256];
    static int nseen;
    int smoke = key == 0x95CDE6CBu || key == 0xEAA2E866u || key == 0x7790C105u || key == 0xD2F8D5BAu;
    int i = 0;
    while (i < nseen && seen[i] != key) ++i;
    if (i == nseen && nseen < 256) seen[nseen++] = key;
    else if (!smoke) return;
    uint32_t n = c->eax ? group_emitters(c->eax) : 0;
    if (smoke) { ++smoke_requests; smoke_emitters += n; }
    diag_event("attrib_x360: emitter group requested: collection %08X%s -> %u emitters%s", key, smoke ? " (tyre smoke)" : "", n,
               smoke ? "" : " (first request)");
    if (smoke && !(smoke_requests & 63)) diag_event("attrib_x360: tyre smoke groups so far: %u, %u emitters", smoke_requests, smoke_emitters);
}

void f_00503D00_orig(Cpu *c);
void f_00503D00(Cpu *c) {
    uint32_t sys = c->ecx;
    static uint64_t last;
    if (diag_on && sys) {
        uint64_t now = host_ticks();
        if (now - last >= 1000000000ull) {
            last = now;
            uint32_t groups = 0, emitters = 0, particles = 0, head = sys + EMITTER_SYSTEM_LIST;
            for (uint32_t g = M32(head); g && g != head && groups < 4096; g = M32(g), ++groups) {
                emitters += group_emitters(g);
                particles += group_particles(g);
            }
            if (groups) diag_event("attrib_x360: live particle system: %u groups, %u emitters, %u particles", groups, emitters, particles);
        }
    }
    f_00503D00_orig(c);
}
