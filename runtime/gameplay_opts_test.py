#!/usr/bin/env python3
"""F10 Gameplay tab: the production gameplay_opts.c hooks against a fake guest; no game, assets, profile or app.txt.

Covers the player-visible options that had no test: Markers per Rival, No Catch-up, Heat Level, Open Map,
Black Edition Content and Unlock All, Starting Bonus, Skip Intro Movies. Each option is driven three ways: its
default, a live change from the menu (settings_set, as F10 does) and its NFS_* / BOOT_* switch at start-up
(settings_init with the switch in the environment, as a launch with the switch does).

The harness includes the real gameplay_opts.c and links the real settings.c. The guest is a flat 64 MB buffer; the
translated originals (f_*_orig) and call_guest are recorders, so each check reads what the hook did to the guest's
registers, stack and memory and which game functions it called with which arguments.

Broken controls: each is the production source with one feature's logic removed (string replacement, asserted to
apply). Every control must fail its own feature's checks while the production source passes all of them.
Proof: production 53/53 checks pass; all 12 broken controls fail their feature checks.
Exit 0 pass, 1 fail, 3 skip (no C compiler).
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#define _DEFAULT_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "host.h"
#include "settings.h"
#include GAMEPLAY_SOURCE

uint8_t *G;
int diag_on;
const char *game_dir = "/nonexistent";
static uint64_t now_ns = 5000000000ull;
uint64_t host_ticks(void) { return now_ns; }
void diag_event(const char *fmt, ...) { (void)fmt; }
void report_note(const char *fmt, ...) { (void)fmt; }
const char *app_pref(const char *key) { (void)key; return NULL; }       // no app.txt: defaults or the environment
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
static uint32_t heap = 0x03000000u;
uint32_t galloc(uint32_t n) { uint32_t p = heap; heap += (n + 15) & ~15u; memset(G + p, 0, n); return p; }
uint32_t gstr(const char *s) { uint32_t p = galloc((uint32_t)strlen(s) + 1); strcpy((char *)G + p, s); return p; }

// ---- recorders
typedef struct { uint32_t fn, n, ecx, a[5]; } Call;
static Call calls[64];
static int ncalls;
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    if (ncalls < 64) {
        Call *k = &calls[ncalls++];
        k->fn = fn; k->n = (uint32_t)n; k->ecx = c->ecx;
        for (int i = 0; i < n && i < 5; ++i) k->a[i] = a[i];
    }
    return fn == 0x0056BC10u ? 0x1111u : fn == 0x00573140u ? 0x2222u : 0;
}
static int count_calls(uint32_t fn) { int k = 0; for (int i = 0; i < ncalls; ++i) k += calls[i].fn == fn; return k; }
static const Call *last_call(uint32_t fn) { for (int i = ncalls - 1; i >= 0; --i) if (calls[i].fn == fn) return &calls[i]; return NULL; }
static int orig_runs[16];
static uint32_t orig_eax;                   // what the stubbed original returns
static uint8_t mc_flag_at_orig;             // MemoryCard+0x1C when the NFSU2-save search ran
void f_007A77C0_orig(Cpu *c) { ++orig_runs[0]; c->eax = orig_eax; }
void f_007A77F0_orig(Cpu *c) { (void)c; ++orig_runs[1]; }
void f_00443BA0_orig(Cpu *c) { (void)c; ++orig_runs[2]; }
void f_005FF990_orig(Cpu *c) { ++orig_runs[3]; c->eax = 1; c->esp += 4 + 0x14; }   // the game: catch-up applies
void f_005DD090_orig(Cpu *c) { ++orig_runs[4]; c->esp += 4; }
void f_00518B40_orig(Cpu *c) { ++orig_runs[5]; uint32_t mc = M32(MEMORY_CARD); mc_flag_at_orig = mc ? M8(mc + 0x1C) : 0; }
void f_005992E0_orig(Cpu *c) { (void)c; ++orig_runs[6]; }
void f_004D5A10_orig(Cpu *c) { (void)c; }
void f_00576E30_orig(Cpu *c) { (void)c; }
void f_0058A680_orig(Cpu *c) { (void)c; }
void f_00576ED0_orig(Cpu *c) { (void)c; }
void f_00576F10_orig(Cpu *c) { (void)c; }
void f_0058AE50_orig(Cpu *c) { (void)c; }
void f_007A6160_orig(Cpu *c) { (void)c; }
void f_007AED80_orig(Cpu *c) { (void)c; }
void f_007B0620_orig(Cpu *c) { (void)c; }
void f_007B13C0_orig(Cpu *c) { (void)c; }

// ---- checks, per feature
enum { F_MARKERS, F_CATCHUP, F_HEAT, F_OPENMAP, F_EDITION, F_BONUS, F_MOVIES, NF };
static const char *const fname[NF] = {"markers", "catchup", "heat", "openmap", "edition", "bonus", "movies"};
static int checks[NF], fails[NF];
#define CHECK(f, x) do { ++checks[f]; if (!(x)) { ++fails[f]; fprintf(stderr, "FAIL %s line %d: %s\n", fname[f], __LINE__, #x); } } while (0)

static Cpu fresh(void) { Cpu c; memset(&c, 0, sizeof c); c.esp = 0x02F00000u; return c; }
static float getf(uint32_t a) { float v; memcpy(&v, G + a, 4); return v; }
static void setf(uint32_t a, float v) { memcpy(G + a, &v, 4); }
static void set_menu(const char *id, int v) { Setting *s = settings_find(id); if (!s || !settings_set(s, v)) { fprintf(stderr, "cannot set %s\n", id); exit(2); } }
static void front_end_package(const char *name) {      // cFEng::mInstance -> FEngine -> package list, one in control
    static uint32_t inst, fe, node;
    if (!inst) { inst = galloc(4); fe = galloc(0x100); node = galloc(0x40); }
    M32(CFENG_INSTANCE) = name ? inst : 0;
    M32(inst) = fe; M32(fe + 0xE4) = node; M32(node + 8) = 0; M32(node + 0x20) = 1; M32(node + 0xC) = name ? gstr(name) : 0;
}
static void frame(uint64_t advance_ns) { Cpu c = fresh(); now_ns += advance_ns; gameplay_frame(&c); }
static void free_roam(void) { M32(GAME_FLOW_STATE) = 6; M32(GRACE_STATUS) = 0; }
static void racing(void) { static uint32_t gs; if (!gs) gs = galloc(0x2000); M32(GAME_FLOW_STATE) = 6; M32(GRACE_STATUS) = gs; M32(gs + 0x1968) = 1; }

// Markers per Rival. GetNumSelected's two callers compare with 2: the hook must answer 2 only at the chosen limit.
static int num_selected(int turned) { Cpu c = fresh(); orig_eax = (uint32_t)turned; f_007A77C0(&c); return (int)c.eax; }
static uint32_t redraw_left(int turned) {                // Redraw: "Selections Remaining" printed with limit - turned
    static uint32_t self;
    if (!self) self = galloc(0x200);
    M32(self + 0x2C) = 6; M32(self + 0x10) = 0x4444;
    for (int i = 0; i < 6; ++i) { M32(self + 0x74 + 12 * i) = 0x100 + i; M8(self + 0x7C + 12 * i) = i < turned; }
    Cpu c = fresh(); c.ecx = self; ncalls = 0;
    f_007A77F0(&c);
    const Call *p = NULL;
    for (int i = 0; i < ncalls; ++i) if (calls[i].fn == 0x005252D0u && calls[i].a[1] == 0x38DEAC6Bu) p = &calls[i];
    return p ? p->a[3] : 0xFFFFFFFFu;
}
// No Catch-up. ComputeCatchUpSkill: 5 stack arguments, ret 0x14; answering false must also pop them.
static int catchup(uint32_t *esp_moved, int *orig) {
    Cpu c = fresh(); c.eax = 0xABCDEF01u; uint32_t esp = c.esp; int before = orig_runs[3];
    f_005FF990(&c);
    *esp_moved = c.esp - esp; *orig = orig_runs[3] - before;
    return (int)(c.eax & 0xFF) | (int)((c.eax >> 8) == 0xABCDEFu) << 8;
}
// Heat Level. AIPursuit::OnTask clamps to this+0xDC..0xE0.
static uint32_t pursuit(float lo, float hi) { uint32_t p = galloc(0x200); setf(p + 0xDC, lo); setf(p + 0xE0, hi); return p; }
static void on_task(uint32_t p) { Cpu c = fresh(); c.ecx = p; f_00443BA0(&c); }
static int heat_calls(float *value) {
    int n = 0;
    for (int i = 0; i < ncalls; ++i) if (calls[i].fn == FN_SET_WORLD_HEAT) { ++n; memcpy(value, &calls[i].a[0], 4); }
    return n;
}
// Open Map. GRaceBin::EnableBarriers (fastcall, ret).
static int enable_barriers_ran(uint32_t *esp_moved) {
    Cpu c = fresh(); uint32_t esp = c.esp; int before = orig_runs[4];
    f_005DD090(&c);
    *esp_moved = c.esp - esp;
    return orig_runs[4] - before;
}
// Black Edition: cFrontendDatabase's constructor, with or without the game's INGAMEC.BUN marker.
static void frontend_database(int ingamec) { Cpu c = fresh(); M8(IS_COLLECTORS_ED) = (uint8_t)ingamec; M8(UNLOCK_ALL_THINGS) = 0; f_005992E0(&c); }
// Starting Bonus: MemoryCard::ListOldSaveFilesPC.
static uint8_t bonus_flag_seen(void) {
    static uint32_t mc;
    if (!mc) mc = galloc(0x40);
    M32(MEMORY_CARD) = mc; M8(mc + 0x1C) = 0; mc_flag_at_orig = 0xEE;
    Cpu c = fresh(); f_00518B40(&c);
    return mc_flag_at_orig;
}

static void defaults(void) {
    // Markers per Rival default 6: picks keep coming until 6 are turned (retail stops at 2).
    CHECK(F_MARKERS, opts[O_MARKERS].value == 5);
    CHECK(F_MARKERS, num_selected(0) == 0);
    CHECK(F_MARKERS, num_selected(1) == 1);
    CHECK(F_MARKERS, num_selected(2) == 1);
    CHECK(F_MARKERS, num_selected(5) == 1);
    CHECK(F_MARKERS, num_selected(6) == 2);
    CHECK(F_MARKERS, redraw_left(2) == 4);
    CHECK(F_MARKERS, redraw_left(6) == 0);
    // No Catch-up default On: false, the 5 arguments popped, the game's catch-up never computed.
    uint32_t moved; int orig, r = catchup(&moved, &orig);
    CHECK(F_CATCHUP, (r & 0xFF) == 0 && (r >> 8) == 1);
    CHECK(F_CATCHUP, moved == 4 + 0x14);
    CHECK(F_CATCHUP, orig == 0);
    // Heat Level default Game: the pursuit's own range, no heat writes.
    uint32_t p = pursuit(1, 5);
    on_task(p);
    CHECK(F_HEAT, getf(p + 0xDC) == 1.0f && getf(p + 0xE0) == 5.0f && orig_runs[2] == 1);
    free_roam(); ncalls = 0; frame(2000000000ull);
    float h = 0; CHECK(F_HEAT, heat_calls(&h) == 0);
    // Open Map default Off: the bin's barriers go up.
    CHECK(F_OPENMAP, enable_barriers_ran(&moved) == 1 && moved == 4);
    // Black Edition default On: forced on without INGAMEC.BUN; Unlock All default Off.
    frontend_database(0);
    CHECK(F_EDITION, M8(IS_COLLECTORS_ED) == 1 && M8(UNLOCK_ALL_THINGS) == 0 && orig_runs[6] == 1);
    // Starting Bonus default On: the NFSU2-save flag is set before the game's search runs.
    CHECK(F_BONUS, bonus_flag_seen() == 1);
}

static void menu(void) {
    // Markers per Rival 2 (the original): the game's own count, untouched; no reprint.
    set_menu("gameplay.markers", 1);
    CHECK(F_MARKERS, num_selected(1) == 1 && num_selected(2) == 2 && num_selected(3) == 3);
    CHECK(F_MARKERS, redraw_left(1) == 0xFFFFFFFFu);
    set_menu("gameplay.markers", 3);                        // 4 per rival
    CHECK(F_MARKERS, num_selected(3) == 1 && num_selected(4) == 2);
    CHECK(F_MARKERS, redraw_left(1) == 3);
    // No Catch-up Off: the game's own ComputeCatchUpSkill runs.
    set_menu("gameplay.no_catchup", 0);
    uint32_t moved; int orig, r = catchup(&moved, &orig);
    CHECK(F_CATCHUP, orig == 1 && (r & 0xFF) == 1 && moved == 4 + 0x14);
    set_menu("gameplay.no_catchup", 1);
    r = catchup(&moved, &orig);
    CHECK(F_CATCHUP, orig == 0 && (r & 0xFF) == 0 && moved == 4 + 0x14);
    // Heat Level 4: set at once in free roam, again each second, never in the front end; pursuits clamp to 4..4,
    // the epic pursuit keeps 6..6.
    M32(GAME_FLOW_STATE) = 3; set_menu("gameplay.heat", 4);
    float h = 0;
    ncalls = 0; frame(0); CHECK(F_HEAT, heat_calls(&h) == 0);
    free_roam();
    ncalls = 0; frame(0); CHECK(F_HEAT, heat_calls(&h) == 1 && h == 4.0f);
    ncalls = 0; frame(500000000ull); CHECK(F_HEAT, heat_calls(&h) == 0);
    ncalls = 0; frame(600000000ull); CHECK(F_HEAT, heat_calls(&h) == 1 && h == 4.0f);
    uint32_t p = pursuit(1, 3), epic = pursuit(6, 6);
    on_task(p); on_task(epic);
    CHECK(F_HEAT, getf(p + 0xDC) == 4.0f && getf(p + 0xE0) == 4.0f);
    CHECK(F_HEAT, getf(epic + 0xDC) == 6.0f && getf(epic + 0xE0) == 6.0f);
    set_menu("gameplay.heat", 6);                           // Heat 6 on our own pursuit, then back down to 2
    on_task(p); CHECK(F_HEAT, getf(p + 0xDC) == 6.0f && getf(p + 0xE0) == 6.0f);
    set_menu("gameplay.heat", 2);
    on_task(p); CHECK(F_HEAT, getf(p + 0xDC) == 2.0f && getf(p + 0xE0) == 2.0f);
    set_menu("gameplay.heat", 0);
    uint32_t q = pursuit(2, 5);
    on_task(q); CHECK(F_HEAT, getf(q + 0xDC) == 2.0f && getf(q + 0xE0) == 5.0f);
    // Open Map On: nothing during a race; DisableBarriers once in free roam; EnableBarriers skipped (ret popped).
    set_menu("gameplay.open_map", 1);
    racing(); ncalls = 0; frame(0);
    CHECK(F_OPENMAP, count_calls(0x005DBF00u) == 0 && count_calls(0x005E3020u) == 0);
    free_roam(); ncalls = 0; frame(0); frame(0);
    CHECK(F_OPENMAP, count_calls(0x005DBF00u) == 1 && count_calls(0x005E3020u) == 0);
    CHECK(F_OPENMAP, enable_barriers_ran(&moved) == 0 && moved == 4);
    // Open Map Off again: the bin's barriers come back (EnableBinBarriers on GRaceStatus), once.
    set_menu("gameplay.open_map", 0);
    static uint32_t gs; if (!gs) gs = galloc(0x2000);
    M32(GAME_FLOW_STATE) = 6; M32(GRACE_STATUS) = gs; M32(gs + 0x1968) = 0;   // free roam with a GRaceStatus
    ncalls = 0; frame(0); frame(0);
    const Call *k = last_call(0x005E3020u);
    CHECK(F_OPENMAP, count_calls(0x005E3020u) == 1 && k && k->ecx == gs && count_calls(0x005DBF00u) == 0);
    CHECK(F_OPENMAP, enable_barriers_ran(&moved) == 1);
    // Black Edition Off: back to the game's own file check (missing here: off); On: forced again.
    frontend_database(0);
    set_menu("gameplay.black_edition", 0); CHECK(F_EDITION, M8(IS_COLLECTORS_ED) == 0);
    set_menu("gameplay.black_edition", 1); CHECK(F_EDITION, M8(IS_COLLECTORS_ED) == 1);
    set_menu("gameplay.unlock_all", 1); CHECK(F_EDITION, M8(UNLOCK_ALL_THINGS) == 1);
    set_menu("gameplay.unlock_all", 0); CHECK(F_EDITION, M8(UNLOCK_ALL_THINGS) == 0);
    // Starting Bonus Off: the search runs as the game's (no NFSU2 save, no bonus).
    set_menu("gameplay.starting_bonus", 0);
    CHECK(F_BONUS, bonus_flag_seen() == 0);
}

// Start-up switches (the environment wins over app.txt; the menu shows the row locked).
static void env_case(void) {
    CHECK(F_MARKERS, opts[O_MARKERS].locked && num_selected(2) == 1 && num_selected(3) == 2);   // NFS_MARKERS=3
    uint32_t moved; int orig, r = catchup(&moved, &orig);                                        // NFS_NO_CATCHUP=off
    CHECK(F_CATCHUP, opts[O_NO_CATCHUP].locked && orig == 1 && (r & 0xFF) == 1);
    uint32_t p = pursuit(1, 5);                                                                  // NFS_HEAT=6
    on_task(p); CHECK(F_HEAT, getf(p + 0xDC) == 6.0f && getf(p + 0xE0) == 6.0f);
    CHECK(F_OPENMAP, enable_barriers_ran(&moved) == 0);                                          // NFS_OPEN_MAP=on
    frontend_database(1);                                                                        // NFS_BLACK_EDITION=off, BOOT_UNLOCKALL=on
    CHECK(F_EDITION, M8(IS_COLLECTORS_ED) == 1 && M8(UNLOCK_ALL_THINGS) == 1);                  // the game's own file found
    frontend_database(0);
    CHECK(F_EDITION, M8(IS_COLLECTORS_ED) == 0 && M8(UNLOCK_ALL_THINGS) == 1);
    CHECK(F_BONUS, bonus_flag_seen() == 0);                                                      // NFS_STARTING_BONUS=off
    // NFS_SKIP_MOVIES=on: SkipMovies from the first frame (boot movies skipped) until the title screen has control,
    // then off (story movies play).
    M32(GAME_FLOW_STATE) = 3; front_end_package(NULL);
    frame(0); CHECK(F_MOVIES, M32(SKIP_MOVIES) == 1);
    front_end_package("LS_EAlogo.fng"); frame(0); CHECK(F_MOVIES, M32(SKIP_MOVIES) == 1);
    front_end_package("LS_Splash_PC.fng"); frame(0); CHECK(F_MOVIES, M32(SKIP_MOVIES) == 0);
    M32(SKIP_MOVIES) = 0; frame(0); CHECK(F_MOVIES, M32(SKIP_MOVIES) == 0);
}
static void movies_default(void) {                         // Skip Intro Movies Off: SkipMovies never touched
    M32(GAME_FLOW_STATE) = 3; front_end_package(NULL);
    frame(0); CHECK(F_MOVIES, M32(SKIP_MOVIES) == 0);
}

int main(int argc, char **argv) {
    G = mmap(NULL, 64u << 20, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (G == MAP_FAILED || argc < 2) return 2;
    settings_init();
    if (!strcmp(argv[1], "defaults")) { defaults(); movies_default(); }
    else if (!strcmp(argv[1], "menu")) menu();
    else if (!strcmp(argv[1], "env")) env_case();
    else return 2;
    int total = 0, bad = 0;
    for (int f = 0; f < NF; ++f) {
        if (!checks[f]) continue;
        printf("%s %s %d/%d\n", argv[1], fname[f], checks[f] - fails[f], checks[f]);
        total += checks[f]; bad += fails[f];
    }
    printf("%s: %s (%d checks, %d failures)\n", argv[1], bad ? "FAIL" : "PASS", total, bad);
    return bad ? 1 : 0;
}
'''

ENV = {'defaults': {},
       'menu': {},
       'env': {'NFS_MARKERS': '3', 'NFS_NO_CATCHUP': 'off', 'NFS_HEAT': '6', 'NFS_OPEN_MAP': 'on',
               'NFS_BLACK_EDITION': 'off', 'BOOT_UNLOCKALL': 'on', 'NFS_STARTING_BONUS': 'off',
               'NFS_SKIP_MOVIES': 'on'}}
SWITCHES = {'NFS_MARKERS', 'NFS_HEAT', 'NFS_BLACK_EDITION', 'NFS_STARTING_BONUS', 'BOOT_UNLOCKALL', 'NFS_OPEN_MAP',
            'NFS_SKIP_MOVIES', 'BOOT_SKIPINTRO', 'NFS_NO_CATCHUP', 'NFS_TEST_MARKERS', 'NFS_TEST_AUDIOSET'}

# Each control breaks one option in the production source; its feature must fail.
CONTROLS = [
    ('markers', 'retail limit: GetNumSelected passed through', 'if (want != 2) c->eax = (uint32_t)r;', ''),
    ('markers', 'Selections Remaining not reprinted', 'if (want == 2) return;\n    int turned', 'return;\n    int turned'),
    ('catchup', 'game catch-up always applied', 'if (!opts[O_NO_CATCHUP].value) { f_005FF990_orig(c); return; }',
     '{ f_005FF990_orig(c); return; }'),
    ('catchup', 'false returned without popping the 5 arguments', 'c->esp += 4 + 0x14;                                    // ret 0x14',
     'c->esp += 4;'),
    ('heat', 'epic pursuit 6..6 lowered', '(mine || !(lo == 6.0f && hi == 6.0f))', '(1)'),
    ('heat', 'heat not re-applied each second', 'next_heat = host_ticks() + 1000000000ull;', 'next_heat = ~0ull;'),
    ('openmap', 'barriers kept (EnableBarriers not skipped)', 'if (!opts[O_OPEN_MAP].value) { f_005DD090_orig(c); return; }',
     '{ f_005DD090_orig(c); return; }'),
    ('openmap', 'barriers changed mid-race', 'if (barriers_pending >= 0 && roaming)', 'if (barriers_pending >= 0 && ingame)'),
    ('edition', 'Black Edition not forced', 'if (opts[O_BLACK_EDITION].value) M8(IS_COLLECTORS_ED) = 1;', ''),
    ('edition', 'Unlock All ignored at start-up', 'if (opts[O_UNLOCK_ALL].value) M8(UNLOCK_ALL_THINGS) = 1;', ''),
    ('bonus', 'NFSU2-save flag not set', 'if (opts[O_STARTING_BONUS].value && mc) M8(mc + 0x1C) = 1;', ''),
    ('movies', 'SkipMovies left on for story movies', 'M32(SKIP_MOVIES) = 0;\n        movies_state = 2;', 'movies_state = 2;'),
]


def compiler():
    for cc in ('clang', 'cc'):
        if shutil.which(cc):
            return cc
    return None


def build(cc, tmp, source, name):
    gen = tmp / 'gen_include'
    gen.mkdir(exist_ok=True)
    (gen / 'funcs.h').write_text('// test stub: gameplay_opts.c declares the hooks it needs itself\n')
    src = tmp / (name + '_gameplay_opts.c')
    src.write_text(source)
    harness = tmp / (name + '_harness.c')
    harness.write_text(HARNESS)
    exe = tmp / name
    flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-unused-parameter',
             '-I' + str(gen), '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp')]
    for san in (['-fsanitize=address,undefined', '-fno-sanitize-recover=all'], []):
        objs = []
        for lib in ('settings', 'offline_stubs'):          # the real registry, built once per sanitizer choice
            o = tmp / ('%s%s.o' % (lib, '_san' if san else ''))
            if not o.exists():
                subprocess.run([cc] + san + flags + ['-c', str(ROOT / 'runtime' / (lib + '.c')), '-o', str(o)],
                               capture_output=True, text=True)
            objs.append(str(o))
        p = subprocess.run([cc] + san + flags + ['-DGAMEPLAY_SOURCE="%s"' % src, str(harness)] + objs +
                           ['-o', str(exe), '-lm'], capture_output=True, text=True)
        if p.returncode == 0:
            return exe, bool(san)
    raise SystemExit('build failed (%s):\n%s' % (name, p.stderr[-4000:]))


def run(exe, case):
    env = {k: v for k, v in os.environ.items() if k not in SWITCHES}
    env.update(ENV[case])
    env.update(HOME=str(exe.parent), ASAN_OPTIONS='detect_leaks=0')
    p = subprocess.run([str(exe), case], capture_output=True, text=True, env=env, timeout=60)
    per = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and '/' in parts[2]:
            ok, n = map(int, parts[2].split('/'))
            per[parts[1]] = (ok, n)
    return p, per


def main():
    cc = compiler()
    if not cc:
        print('SKIP: no C compiler')
        return 3
    production = (ROOT / 'runtime/gameplay_opts.c').read_text()
    with tempfile.TemporaryDirectory(prefix='gameplay-opts-') as t:
        tmp = Path(t)
        exe, sanitized = build(cc, tmp, production, 'production')
        print('C instrumentation: ' + ('ASan + UBSan' if sanitized else 'plain (sanitizer build unavailable)'))
        totals = {}
        for case in ENV:
            p, per = run(exe, case)
            sys.stdout.write(p.stdout)
            if p.returncode != 0:
                sys.stderr.write(p.stderr[-4000:])
                print('GAMEPLAY OPTIONS FAIL: production %s' % case)
                return 1
            for f, (ok, n) in per.items():
                a = totals.setdefault(f, [0, 0]); a[0] += ok; a[1] += n
        failed_controls = 0
        for i, (feature, why, old, new) in enumerate(CONTROLS):
            assert production.count(old) == 1, 'control no longer applies: ' + why
            exe_b, control_sanitized = build(cc, tmp, production.replace(old, new), 'broken%d' % i)
            assert control_sanitized == sanitized, 'control instrumentation differs: ' + why
            bad = 0
            for case in ENV:
                p, per = run(exe_b, case)
                ok, n = per.get(feature, (0, 0))
                if p.returncode != 0 and (n == 0 or ok < n):
                    bad += (n - ok) if n else 1
            print('control %-8s %-48s %s' % (feature, why, 'FAILS %d check(s), as it must' % bad if bad else 'PASSED: not caught'))
            failed_controls += bad > 0
        print('per feature (production): ' + ', '.join('%s %d/%d' % (f, a[0], a[1]) for f, a in totals.items()))
        print('GAMEPLAY OPTIONS %s: %d checks, %d/%d broken controls caught%s' % (
            'PASS' if failed_controls == len(CONTROLS) else 'FAIL', sum(a[1] for a in totals.values()), failed_controls,
            len(CONTROLS), '' if sanitized else ' (no sanitizer runtime on this host)'))
        return 0 if failed_controls == len(CONTROLS) else 1


if __name__ == '__main__':
    sys.exit(main())
