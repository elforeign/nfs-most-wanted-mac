// gameplay_opts.c - the port menu's Gameplay options (category "Gameplay" in the settings registry, settings.h), in the
// spirit of the community "Extra Options" mod but built from the decomp. Each option is a
// backend (a hook or a write to the game's own switch) plus its registration. Settings are per user (app.txt).
//
                                                                                                                      
//   the game deals up to 6 reward cards (FEMarkerManager's later-selection list, 6 slots, 00576B80) on
//   SafeHouseMarkers.fng (FEMarkerSelection, 6 buttons BUTTON_1..6) and lets you turn 2:
//   FEMarkerSelection::NotificationMessage (007B3CD0) stops accepting picks and shows Continue when GetNumSelected
//   (007A77C0) >= 2 (`cmp eax, 2` at 007B3D7B and 007B3E17, its only two callers). The hook reports that count to
//   those checks as 2 once N cards are turned. Redraw (007A77F0) prints "Selections Remaining" as 2 - turned: printed
//   again with N - turned.
// Heat Level (gameplay.heat: Game / 1..6, NFS_HEAT, default Game): picking N sets the player's heat to N
//   through the game's script function Game_SetWorldHeat (00612660), as BOOT_PURSUIT does, and keeps it there in free
//   roam. The career caps heat per Blacklist rank: each pursuit gets a minimum and maximum heat when it is created
//   (AIPursuit::AIPursuit, 00442D00: GRaceBin::GetBase/MaxOpenWorldHeat of the current bin; 1..5 outside the career;
//   6..6 for the epic pursuit) and AIPursuit::OnTask (00443BA0) clamps the heat to them every tick (bClamp(heat,
//   this+0xDC, this+0xE0)), so the hook sets that range to N..N, which also lifts the cap; the heat is re-applied each
//   second outside pursuits. Heat 6 is the game's top level (the epic pursuit's; the free-roam pursuit tables have a
                                                                                                                 
// Black Edition content (gameplay.black_edition, NFS_BLACK_EDITION, default on): the game's IsCollectorsEdition byte
//   (008F8718) is set by cFrontendDatabase::cFrontendDatabase (005992E0) when .\GLOBAL\INGAMEC.BUN exists (a marker
//   file: nothing loads it). It gates the CE_ bonus presets (UnlockSystem::IsBonusCarCEOnly, 00576AD0), the CE-only
//   events (UnlockSystem::IsEventAvailable, 00576A20: 21.1.1, 21.2.1, 21.2.2, 19.9.70) and a few unlockables. On
//   forces it after that check; Off leaves the game's own file check.
                                                                                                                 
//   $10,000 for an NFS Underground 2 save. On the career's first car pick (UIQRCarSelect::Setup 007C2780, while the
//   career's one-time-bonus flag, CareerSettings+4 bit 1, is clear) MemoryCard::ListOldSaveFilesPC (00518B40) looks
//   for "<Local AppData>\NFS Underground 2\*" and sets MemoryCard+0x1C when it has files; EndListingOldSaveFiles
//   (00518570) then shows the game's own message (DialogInterface::ShowOneButton, text 0x34DC1BEC) and
//   CareerSettings::AwardOneTimeCashBonus (0056D7C0) sets the flag and adds 10000 when +0x1C is set. The hook sets
//   +0x1C before the search, so the game's own path pays it, once per career (the flag is in the save). With Skip
//   Career Prologue there is no first car pick: the same end of the check runs once the safe house menu has control.
// Unlock All (gameplay.unlock_all, BOOT_UNLOCKALL, default off): the game's UnlockAllThings byte (00926124, also set
//   by its own cheat code, EasterEggs) makes every car, part and Challenge Series event report unlocked. The flag
//   itself is not saved; what you buy or race while it is on is.
// Open Map (gameplay.open_map, NFS_OPEN_MAP, default off): the career's region barriers come from the current
//   Blacklist bin (GRaceBin::EnableBarriers 005DD090, called by GRaceStatus::EnterBin and GManager::BeginGameplay);
//   the hook skips it. Turned on in free roam the game's DisableBarriers script function (005DBF00) removes them at
//   once; turned off, EnableBinBarriers (005E3020) puts the bin's back. A race's own route barriers
//   (GRaceStatus::EnableBarriers 005FE330) are untouched.
// Skip Intro Movies (gameplay.skip_movies, NFS_SKIP_MOVIES, default off): the game's SkipMovies switch (00926144),
//   which MoviePlayer::Play obeys for every movie, is on only until the title screen, so the boot movies (EA, PSA,
//   attract) are skipped and the story movies still play.
// Skip Career Prologue (gameplay.skip_prologue, BOOT_SKIPINTRO, default off): the game's SkipCareerIntro switch
//   (00926125/6, as EasterEggs::ActivateEasterEgg sets it): a new career starts in the safe house with the career cars.
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "funcs.h"
#include "host.h"
#include "settings.h"

#define UNLOCK_ALL_THINGS   0x00926124u    // bool UnlockAllThings
#define IS_COLLECTORS_ED    0x008F8718u    // bool IsCollectorsEdition
#define GAME_FLOW_STATE     0x00925E90u    // 6 = free roam or a race
#define FE_MARKER_MANAGER   0x0091D928u    // TheFEMarkerManager
#define RIVAL_FLOW          0x0091CA48u    // uiRepSheetRivalFlow::mInstance
#define CFENG_INSTANCE      0x0091CADCu    // cFEng::mInstance
#define FN_SET_WORLD_HEAT   0x00612660u    // Game_SetWorldHeat(float), cdecl
#define FN_ADD_MARKER_LATER 0x00576B80u    // FEMarkerManager::AddMarkerForLaterSelection(type, param), thiscall
#define FN_CLEAR_MARKERS    0x00576BD0u    // FEMarkerManager::ClearMarkersForLaterSelection(), thiscall
#define FN_RIVAL_FLOW_START 0x005485F0u    // uiRepSheetRivalFlow::StartFlow(int), thiscall

#define GRACE_STATUS        0x0091E000u    // GRaceStatus::fObj (+0x1968: the race's parameters, 0 in free roam)
#define MEMORY_CARD         0x0091CB20u    // MemoryCard::s_pThis (+0x1C: an NFSU2 save was found)
#define FE_DATABASE         0x0091CF90u    // FEDatabase (+0x10 user profile; career cash at +0xB4)
#define SKIP_MOVIES         0x00926144u    // SkipMovies
#define SKIP_CAREER_INTRO   0x00926125u    // SkipCareerIntro (2 bytes)

static const char *const k_markers[] = {"1", "2", "3", "4", "5", "6"};
static const char *const k_heat[] = {"Game", "1", "2", "3", "4", "5", "6"};

static volatile int heat_apply_pending, barriers_pending = -1;
static int fedb_ready;                                  // cFrontendDatabase constructed
static int ce_found = -1;                               // the game's own INGAMEC.BUN check (-1: not run yet)

// apply() runs on the game thread when the user changes a setting (settings.h); start-up values are applied by the
// cFrontendDatabase hook, the first frame and boot.c (BOOT_UNLOCKALL / BOOT_SKIPINTRO, which the registry exports).
static void apply_unlock_all(int v) { M8(UNLOCK_ALL_THINGS) = (uint8_t)(v != 0); }
static void apply_black_edition(int v) { if (ce_found >= 0) M8(IS_COLLECTORS_ED) = (uint8_t)(v ? 1 : ce_found); }
static void apply_heat(int v) { if (v) heat_apply_pending = 1; }
static void apply_open_map(int v) { barriers_pending = v; }
static void apply_skip_prologue(int v) { M8(SKIP_CAREER_INTRO) = M8(SKIP_CAREER_INTRO + 1) = (uint8_t)(v != 0); }

static Setting opts[] = {
    {.id = "gameplay.markers", .category = SET_GAMEPLAY, .order = 10, .label = "Markers per Rival",
     .help = "Reward cards you turn after beating a Blacklist rival (the original game: 2 of 6).", .type = SET_CHOICE,
     .choices = k_markers, .nchoices = 6, .def = 5, .env = "NFS_MARKERS"},
    {.id = "gameplay.heat", .category = SET_GAMEPLAY, .order = 20, .label = "Heat Level",
     .help = "Sets your heat and keeps it there in free roam, above the career's cap too (Game: the game decides).",
     .type = SET_CHOICE, .choices = k_heat, .nchoices = 7, .def = 0, .env = "NFS_HEAT", .apply = apply_heat},
    {.id = "gameplay.black_edition", .category = SET_GAMEPLAY, .order = 40, .label = "Black Edition Content",
     .help = "Black Edition bonus cars and Challenge events (Off: only if the game folder has them).",
     .type = SET_TOGGLE, .def = 1, .env = "NFS_BLACK_EDITION", .apply = apply_black_edition},
    {.id = "gameplay.starting_bonus", .category = SET_GAMEPLAY, .order = 50, .label = "Starting Bonus ($10,000)",
     .help = "The game's NFS Underground 2 bonus, paid once per career when you pick your first car.",
     .type = SET_TOGGLE, .def = 1, .env = "NFS_STARTING_BONUS"},
    {.id = "gameplay.unlock_all", .category = SET_GAMEPLAY, .order = 110, .label = "Unlock All (changes your save)",
     .help = "Unlocks every car, part and Challenge event, and switching it off relocks them though what you buy meanwhile stays in the save.",
     .type = SET_TOGGLE, .def = 0, .env = "BOOT_UNLOCKALL", .apply = apply_unlock_all},
    {.id = "gameplay.open_map", .category = SET_GAMEPLAY, .order = 120, .label = "Open Map (affects career progression)",
     .help = "Removes the career's region barriers, so every borough is open from the start.", .type = SET_TOGGLE,
     .def = 0, .env = "NFS_OPEN_MAP", .apply = apply_open_map},
    {.id = "gameplay.skip_movies", .category = SET_GAMEPLAY, .order = 70, .label = "Skip Intro Movies",
     .help = "Starts at the title screen without the EA and attract movies (story movies still play).",
     .type = SET_TOGGLE, .def = 0, .env = "NFS_SKIP_MOVIES"},
    {.id = "gameplay.skip_prologue", .category = SET_GAMEPLAY, .order = 60, .label = "Skip Career Prologue",
     .help = "A new career starts in the safe house with the career cars, without the prologue races.",
     .type = SET_TOGGLE, .def = 0, .env = "BOOT_SKIPINTRO", .apply = apply_skip_prologue},
    {.id = "gameplay.no_catchup", .category = SET_GAMEPLAY, .order = 30, .label = "No Catch-up (no rubber banding)",
     .help = "On makes rivals race at their own pace, in career races too, while Off keeps the original catch-up that speeds them up when behind.",
     .type = SET_TOGGLE, .def = 1, .env = "NFS_NO_CATCHUP"},
};
enum { O_MARKERS, O_HEAT_LEVEL, O_BLACK_EDITION, O_STARTING_BONUS, O_UNLOCK_ALL, O_OPEN_MAP, O_SKIP_MOVIES,
       O_SKIP_PROLOGUE, O_NO_CATCHUP };
static int markers_limit(void) { return opts[O_MARKERS].value + 1; }
static int heat_level(void) { return opts[O_HEAT_LEVEL].value; }          // 0 = the game's, else 1..6
static int in_race(void) { uint32_t gs = M32(GRACE_STATUS); return gs && M32(gs + 0x1968); }

__attribute__((constructor)) static void gameplay_register(void) {
    for (size_t i = 0; i < sizeof opts / sizeof opts[0]; ++i) settings_add(&opts[i]);
}
static void ensure_registered(void) {                     // diag: the values in effect, once
    static int done;
    if (done || !diag_on) return;
    done = 1;
    for (size_t i = 0; i < sizeof opts / sizeof opts[0]; ++i)
        if (opts[i].type != SET_ACTION)
            diag_event("gameplay: %s = %s%s", opts[i].id, settings_value_name(&opts[i], opts[i].value, (char[32]){0}, 32),
                       opts[i].locked ? " (environment)" : "");
}

static const char *fe_package_in_control(void) {
    uint32_t fe = M32(CFENG_INSTANCE) ? M32(M32(CFENG_INSTANCE)) : 0;
    if (!fe || fe == 0xFFFFFF28u) return NULL;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) if (M32(p + 0x20)) return gptr(M32(p + 0xC));
    return NULL;
}
static void set_world_heat(Cpu *c, float heat) {
    uint32_t esp = c->esp, a[1];
    memcpy(a, &heat, 4);
    call_guest(c, FN_SET_WORLD_HEAT, 1, a);
    c->esp = esp;
}

// Test only, NFS_TEST_MARKERS=1: once the safe house menu (MainMenu_Sub.fng) has had control for 2 s, deals 6
// reward cards and starts the post-rival flow at its marker step, as beating a rival does (uiRepSheetRivalFlow::Next
// step 1 = SafeHouseMarkers.fng), so the picking limit can be checked without winning a Blacklist race.
static void test_markers(Cpu *c, const char *pkg) {
    static int state;                                       // 0 waiting, 1 menu seen, 2 done
    static uint64_t since;
    if (state == 2 || !getenv("NFS_TEST_MARKERS")) return;
    int menu = pkg && !strcasecmp(pkg, "MainMenu_Sub.fng");
    if (!menu) { state = 0; return; }
    if (state == 0) { state = 1; since = host_ticks(); return; }
    if (host_ticks() - since < 2000000000ull || !M32(RIVAL_FLOW)) return;
    state = 2;
    // ePossibleMarker ids (the values GetMarkerSelectInfo looks up): 1, 2 visual; 8, 9 performance; 0x0D special;
    // 0x13 cash with an arbitrary test amount (the game's amounts come from each rival's attributes, not this table)
    static const uint32_t cards[6][2] = {{1, 0}, {2, 0}, {8, 0}, {9, 0}, {0x0D, 0}, {0x13, 5000}};
    uint32_t esp = c->esp, one = 1;
    c->ecx = FE_MARKER_MANAGER;
    call_guest(c, FN_CLEAR_MARKERS, 0, NULL);
    for (int i = 0; i < 6; ++i) { c->ecx = FE_MARKER_MANAGER; call_guest(c, FN_ADD_MARKER_LATER, 2, cards[i]); }
    c->ecx = M32(RIVAL_FLOW);
    call_guest(c, FN_RIVAL_FLOW_START, 1, &one);
    c->esp = esp;
    fprintf(stderr, "gameplay: test markers dealt (6 cards), rival flow started\n");
    if (diag_on) diag_event("gameplay: test markers dealt (6 cards), rival flow started");
}

// BOOT_DIAG: once the front end has a package in control, asks the game's own unlock checks about the Black Edition
// content with each combination of IsCollectorsEdition and UnlockAllThings (restored afterwards), so events.log shows
// what each switch gates: UnlockSystem::IsBonusCarAvailable (0058ADB0) and IsCarUnlocked (0058A9F0, quick race
// filter) for the 10 CE_ presets, UnlockSystem::IsEventAvailable (00576A20) for the CE-only events.
static void diag_gating_report(Cpu *c) {
    static const char *const presets[] = {"CE_997S", "CE_C6R", "CE_CAMARO", "CE_CORVETTE", "CE_ELISE", "CE_GT2",
                                          "CE_GTRSTREET", "CE_SL500", "CE_SL65", "CE_SUPRA"};
    static const char *const events[] = {"21.1.1", "21.2.1", "21.2.2", "19.9.70"};
    uint8_t ce = M8(IS_COLLECTORS_ED), uat = M8(UNLOCK_ALL_THINGS);
    uint32_t esp = c->esp;
    diag_event("gameplay: gating now: IsCollectorsEdition=%d UnlockAllThings=%d", ce, uat);
    for (int combo = 0; combo < 4; ++combo) {
        M8(IS_COLLECTORS_ED) = (uint8_t)(combo & 1);
        M8(UNLOCK_ALL_THINGS) = (uint8_t)(combo >> 1);
        char avail[16], unl[16], ev[8];
        for (int i = 0; i < 10; ++i) {
            uint32_t h = 0xFFFFFFFFu;
            for (const char *p = presets[i]; *p; ++p) h = h * 33 + (uint8_t)*p;     // bStringHash
            uint32_t a1[1] = {h}, a3[3] = {1, h, 0};
            avail[i] = (call_guest(c, 0x0058ADB0u, 1, a1) & 0xFF) ? 'Y' : '.';
            unl[i] = (call_guest(c, 0x0058A9F0u, 3, a3) & 0xFF) ? 'Y' : '.';
        }
        avail[10] = unl[10] = 0;
        for (int i = 0; i < 4; ++i) {
            uint32_t s[1] = {gstr(events[i])};
            uint32_t a[1] = {call_guest(c, 0x004519D0u, 1, s)};                    // Attrib::StringHash32
            ev[i] = (call_guest(c, 0x00576A20u, 1, a) & 0xFF) ? 'Y' : '.';
        }
        ev[4] = 0;
        diag_event("gameplay: gating CE=%d UnlockAll=%d: CE cars available %s, unlocked (quick race) %s; CE events %s",
                   combo & 1, combo >> 1, avail, unl, ev);
    }
    M8(IS_COLLECTORS_ED) = ce;
    M8(UNLOCK_ALL_THINGS) = uat;
    c->esp = esp;
}

// Per frame on the game thread (d3d9.m's Present): the heat action and lock.
void gameplay_frame(Cpu *c) {
    ensure_registered();
    const char *pkg = fe_package_in_control();
    static int gating_done;
    if (diag_on && !gating_done && fedb_ready && pkg && M32(GAME_FLOW_STATE) != 6) { gating_done = 1; diag_gating_report(c); }
    test_markers(c, pkg);
    // Skip Intro Movies: SkipMovies from the first frame until the title screen (or the first menu) has control.
    static int movies_state;                                // 0 not started, 1 skipping, 2 done
    if (movies_state == 0) {
        movies_state = 2;
        if (opts[O_SKIP_MOVIES].value && !M32(SKIP_MOVIES)) { M32(SKIP_MOVIES) = 1; movies_state = 1; }
    } else if (movies_state == 1 && pkg && (strstr(pkg, "LS_Splash") || !strcasecmp(pkg, "MC_Main.fng") ||
                                            !strcasecmp(pkg, "MainMenu.fng"))) {
        M32(SKIP_MOVIES) = 0;
        movies_state = 2;
        if (diag_on) diag_event("gameplay: intro movies skipped, SkipMovies off at %s", pkg);
    }
    // Starting Bonus with Skip Career Prologue: CareerSettings::StartNewCareer (005A3950) then gives the career cars
    // without the first car pick, so the game's check never runs. Once the safe house menu has control for 1 s in a
    // career (+4 bit 0) whose one-time bonus (+4 bit 1) is still unpaid, the game's own end of that check runs:
    // MemoryCard::EndListingOldSaveFiles (00518570) with +0x1C set shows the message and pays the $10,000.
    static uint64_t safehouse_since;
    static int refresh_menu;
    uint32_t db = M32(FE_DATABASE), prof = db ? M32(db + 0x10) : 0, mc = M32(MEMORY_CARD);
    // Test only: NFS_TEST_AUDIOSET=<off>=<hex>[,...] holds AudioSettings (UserProfile+0x4c) words every frame, so a
    // BOOT_RACE runs with a career's audio options (+0x74 interactive music, +0x78 EA Trax, +0x7c EA Trax order).
    static const char *audioset = (const char *)-1;
    if (audioset == (const char *)-1) audioset = getenv("NFS_TEST_AUDIOSET");
    if (audioset && prof) {
        for (const char *p = audioset; *p;) {
            char *e;
            unsigned long off = strtoul(p, &e, 16), v = *e == '=' ? strtoul(e + 1, &e, 16) : 0;
            if (off < 0x34) M32(prof + 0x4c + off) = (uint32_t)v;
            p = *e == ',' ? e + 1 : e + strlen(e);
        }
    }
    if (opts[O_STARTING_BONUS].value && M8(SKIP_CAREER_INTRO + 1) && prof && mc && pkg &&
        !strcasecmp(pkg, "MainMenu_Sub.fng") && (M32(prof + 0xAC) & 3) == 1) {
        if (!safehouse_since) safehouse_since = host_ticks();
        else if (host_ticks() - safehouse_since >= 1000000000ull) {
            int32_t cash = (int32_t)M32(prof + 0xB4);
            uint32_t esp = c->esp;
            M8(mc + 0x1C) = 1;
            c->ecx = mc;
            call_guest(c, 0x00518570u, 0, NULL);
            c->esp = esp;
            fprintf(stderr, "gameplay: starting bonus (skipped prologue): cash %d -> %d\n", cash, (int32_t)M32(prof + 0xB4));
            if (diag_on) diag_event("gameplay: starting bonus (skipped prologue): cash %d -> %d", cash, (int32_t)M32(prof + 0xB4));
            refresh_menu = 1;
        }
    } else safehouse_since = 0;
    // The safe house menu built its header (Cash) before the bonus: once the message is closed and the menu has
    // control again, it is rebuilt (cFEng::QueuePackageSwitch 00525940 to itself) so the header shows the new cash.
    int on_menu = pkg && !strcasecmp(pkg, "MainMenu_Sub.fng");
    if (refresh_menu == 1 && !on_menu) refresh_menu = 2;  // the message took control
    if (refresh_menu == 2 && on_menu && M32(CFENG_INSTANCE)) {
        static uint32_t name;
        if (!name) name = gstr("MainMenu_Sub.fng");
        uint32_t esp = c->esp, a[4] = {name, 0, 0, 0};
        c->ecx = M32(CFENG_INSTANCE);
        call_guest(c, 0x00525940u, 4, a);
        c->esp = esp;
        refresh_menu = 0;
        if (diag_on) diag_event("gameplay: safe house menu rebuilt for the new cash");
    }
    int ingame = M32(GAME_FLOW_STATE) == 6, roaming = ingame && !in_race();
    // Open Map changed in the menu: the bin's barriers off (DisableBarriers) or back (EnableBinBarriers), in free roam.
    if (barriers_pending >= 0 && roaming) {
        uint32_t esp = c->esp;
        if (barriers_pending) call_guest(c, 0x005DBF00u, 0, NULL);
        else { c->ecx = M32(GRACE_STATUS); call_guest(c, 0x005E3020u, 0, NULL); }
        c->esp = esp;
        if (diag_on) diag_event("gameplay: region barriers %s", barriers_pending ? "removed" : "restored");
        barriers_pending = -1;
    }
    // Heat Level: set when picked, then every second in free roam (pursuits: the OnTask hook).
    int level = heat_level();
    static uint64_t next_heat;
    if (level && ingame && (heat_apply_pending || host_ticks() >= next_heat)) {                                 
        if (heat_apply_pending && diag_on) diag_event("gameplay: heat set to %d", level);
        heat_apply_pending = 0;
        next_heat = host_ticks() + 1000000000ull;
        set_world_heat(c, (float)level);
    }
}

// FEMarkerSelection::GetNumSelected (fastcall, this in ecx): the screen's two "picked enough" checks compare it with 2.
void f_007A77C0_orig(Cpu *c);
void f_007A77C0(Cpu *c) {
    ensure_registered();
    f_007A77C0_orig(c);
    int n = (int)c->eax, want = markers_limit(), r = n >= want ? 2 : (n > 0 ? 1 : 0);
    if (want != 2) c->eax = (uint32_t)r;
    static int last = -1;
    if (diag_on && n != last) { last = n; diag_event("gameplay: markers turned %d of %d allowed", n, want); }
}

// FEMarkerSelection::Redraw (fastcall, this in ecx): it prints "Selections Remaining: %d" (object 0x38DEAC6B, string
// 0x5BB3A130) and the rival line (object 0xD6C0E097, string 0xAE5BC899 with the rival's AKA) with 2 - turned
// (007A7A3B, 007A7ABD). With another limit both are printed again with N - turned.
void f_007A77F0_orig(Cpu *c);
void f_007A77F0(Cpu *c) {
    uint32_t self = c->ecx;
    f_007A77F0_orig(c);
    int want = markers_limit();
    if (want == 2) return;
    int turned = 0, n = (int)M32(self + 0x2C);
    for (int i = 0; i < n && i < 6; ++i) turned += M32(self + 0x74 + 12 * i) && M8(self + 0x7C + 12 * i);
    uint32_t left = (uint32_t)(want - turned > 0 ? want - turned : 0), pkg = M32(self + 0x10), esp = c->esp;
    uint32_t h[1] = {0x5BB3A130u};
    uint32_t a[4] = {pkg, 0x38DEAC6Bu, call_guest(c, 0x0056BC10u, 1, h), left};          // GetLocalizedString
    call_guest(c, 0x005252D0u, 4, a);                                                      // FEPrintf
    static uint32_t buf, fmt;
    if (!buf) { buf = galloc(0x100); fmt = gstr("BLACKLIST_RIVAL_%02d_AKA"); }
    uint32_t cp[3] = {buf, 0x100, 0xAE5BC899u};
    call_guest(c, 0x0056BC30u, 3, cp);                                                     // localized string into buf
    uint32_t fe_db = M32(0x0091CF90u), bin = fe_db ? M8(M32(fe_db + 0x10) + 0xB0) : 0;
    uint32_t hh[2] = {fmt, bin + 1};
    uint32_t aka_hash[1] = {call_guest(c, 0x00573140u, 2, hh)};                            // FEngHashString
    uint32_t b[5] = {pkg, 0xD6C0E097u, buf, left, call_guest(c, 0x0056BC10u, 1, aka_hash)};
    call_guest(c, 0x005252D0u, 5, b);
    c->esp = esp;
}

// AIPursuit::OnTask(handle, dt) (thiscall): the heat range the tick clamps to, at this+0xDC (min) and this+0xE0 (max).
void f_00443BA0_orig(Cpu *c);
void f_00443BA0(Cpu *c) {
    uint32_t self = c->ecx;
    static uint32_t ours[16];                              // pursuits whose range this hook set (not the epic one)
    int level = heat_level(), mine = 0;
    for (int i = 0; i < 16; ++i) mine |= ours[i] == self;
    float lo, hi;
    memcpy(&lo, G + self + 0xDC, 4);
    memcpy(&hi, G + self + 0xE0, 4);
    if (level && (mine || !(lo == 6.0f && hi == 6.0f))) {   // the epic pursuit keeps its 6..6
        float n = (float)level;
        if (lo != n || hi != n) {
            memcpy(G + self + 0xDC, &n, 4);
            memcpy(G + self + 0xE0, &n, 4);
            if (!mine) { static int k; ours[k++ & 15] = self; }
            if (diag_on) diag_event("gameplay: pursuit %08X heat range %.1f..%.1f -> %.1f..%.1f", self, lo, hi, n, n);
        }
    }
    f_00443BA0_orig(c);
}

// GRaceStatus::ComputeCatchUpSkill (005FF990, thiscall, 5 stack arguments: GRacerInfo*, PidError*, float*, float*,
// bool; ret 0x14; one caller, AIPerpVehicle::Update 004293D0 at 00429644). No Catch-up (default on) answers false, the game's
// own path for a Quick Race with catch-up off (GRaceParameters::GetCatchUp false); the caller then zeroes the
// vehicle's catch-up skill (+0x7B8) and glue (+0x794 or its out pointer). The career otherwise always applies it.
void f_005FF990_orig(Cpu *c);
void f_005FF990(Cpu *c) {
    if (!opts[O_NO_CATCHUP].value) { f_005FF990_orig(c); return; }
    static uint32_t calls;
    if (diag_on && !(calls++ & 0xFFF)) diag_event("gameplay: no catch-up (%u calls)", calls);
    c->eax &= ~0xFFu;                                      // al = false
    c->esp += 4 + 0x14;                                    // ret 0x14
}

// GRaceBin::EnableBarriers (fastcall, the bin in ecx, no stack arguments): Open Map skips the bin's region barriers.
void f_005DD090_orig(Cpu *c);
void f_005DD090(Cpu *c) {
    if (!opts[O_OPEN_MAP].value) { f_005DD090_orig(c); return; }
    if (diag_on) diag_event("gameplay: region barriers skipped (Open Map)");
    c->esp += 4;                                           // ret
}

// MemoryCard::ListOldSaveFilesPC (fastcall): the NFSU2-save search behind the game's one-time $10,000.
void f_00518B40_orig(Cpu *c);
void f_00518B40(Cpu *c) {
    uint32_t mc = M32(MEMORY_CARD), db = M32(FE_DATABASE), prof = db ? M32(db + 0x10) : 0;
    int32_t cash = prof ? (int32_t)M32(prof + 0xB4) : 0;
    if (opts[O_STARTING_BONUS].value && mc) M8(mc + 0x1C) = 1;
    f_00518B40_orig(c);
    int32_t now = prof ? (int32_t)M32(prof + 0xB4) : 0;
    fprintf(stderr, "gameplay: starting bonus check: cash %d -> %d\n", cash, now);
    if (diag_on) diag_event("gameplay: starting bonus check (NFSU2 save %s): cash %d -> %d", mc && M8(mc + 0x1C) ? "yes" : "no",
                            cash, now);
}

// cFrontendDatabase::cFrontendDatabase (thiscall): its last step sets IsCollectorsEdition from the INGAMEC.BUN check.
void f_005992E0_orig(Cpu *c);
void f_005992E0(Cpu *c) {
    f_005992E0_orig(c);
    fedb_ready = 1;
    int found = M8(IS_COLLECTORS_ED);
    ensure_registered();
    ce_found = found;
    if (opts[O_BLACK_EDITION].value) M8(IS_COLLECTORS_ED) = 1;
    if (opts[O_UNLOCK_ALL].value) M8(UNLOCK_ALL_THINGS) = 1;
    fprintf(stderr, "gameplay: Black Edition content %s (INGAMEC.BUN %s)\n", M8(IS_COLLECTORS_ED) ? "on" : "off",
            found ? "found" : "missing");
    if (diag_on) diag_event("gameplay: Black Edition content %s (INGAMEC.BUN %s)", M8(IS_COLLECTORS_ED) ? "on" : "off",
                            found ? "found" : "missing");
}

// Test only: NFS_TEST_UPGRADE_INTERVAL=<n> makes UpgradeIntervals (004D5A10, cdecl int(int,int,int): the upgrade index
// EAXCar uses to pick a car's engineaudio / turbo sounds) return n, so a stock BOOT_RACE car loads its upgraded engine
// sounds (911turbo: engineaudio por_996 -> por_911_a, CAR_46 -> CAR_42 banks).
void f_004D5A10_orig(Cpu *c);
void f_004D5A10(Cpu *c) {
    f_004D5A10_orig(c);
    static int forced = -2;
    if (forced == -2) { const char *e = getenv("NFS_TEST_UPGRADE_INTERVAL"); forced = e && *e ? atoi(e) : -1; }
    if (forced >= 0) c->eax = (uint32_t)forced;
}
