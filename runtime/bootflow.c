// bootflow.c - the X360's HD boot flow: the EA HD bumper first, the WS_ boot packages, and its title-screen prompt.
//
// BootFlowManager::BootFlowManager (PC 0058E9C0) builds the list of boot screens from one of four tables at
// 008F3C58..008F3CD0, picked by BuildRegion::IsPal and eIsWidescreen; the US PC runs the NTSC 4:3 one (008F3C58..74):
//   MC_Bootup, LS_EAlogo, LS_PSA, MW_LS_AttractFMV, MW_LS_Splash, MC_Main, MainMenu
// (widescreen.c keeps eIsWidescreen false for this caller, because the PC's NTSC widescreen table 008F3C94..B0 has no
// attract movie, and swaps the title for WS_MW_LS_Splash.fng). The X360's builder (822B7EB8) uses, on a widescreen
// output (DAT_828F4A8F), the table at 828FEBB4..D4:
//   WS_LS_EA_hidef, WS_LS_EAlogo, WS_LS_PSA, WS_MW_LS_AttractFMV, WS_MW_LS_Splash, MC_Bootup_GC, MC_Main_GC, MainMenu
// and leaves out WS_LS_EA_hidef.fng while DAT_828F4A8E ("not HD") is set: SetVideoMode 82440188 / FUN_824585E0 set it
// for 640x480 and 1024x576 and clear it for 1280x720. Its 4:3 table (828FEB7C..98) has no EA HD screen. The PC kept
// a dead bStrICmp(name, "WS_LS_EA_hidef.fng") in its own widescreen loop (0058EA18), and everything the screen needs:
// LS_EA_hidef / WS_LS_EA_hidef in the screen factory (MovieScreen), the movie table entry naming "eahd_bumper"
// (008F3CE8 / 008F3D38, unskippable like the EA logo: flag byte 0), and both packages in FRONTEND\FrontB.lzc
// (identical to the X360's; WS_LS_EA_hidef is the one HUFF-packed chunk). Only the movie is missing (no VP6):
// FEngMovieStarter::Callback (00559680) ends a movie whose VP6 bFileExists can't find, so movies.c answers bFileExists
// for MOVIES\*.vp6 the X360 folder has, and plays eahd_bumper_english_ntsc.wmv (720p, 4.0 s, WMA Pro 5.1; volume 127,
// the default for movies MovieVolumeArray doesn't list, as on the X360).
// So after the original builder runs: with the widescreen assets on and an HD mode, the WS_ names of the X360 list
// replace the PC's movie screens and WS_LS_EA_hidef.fng goes in before the EA logo, where the X360 has it (the PC's
// MC_Bootup stays first: on PC it runs before the movies, the X360's _GC memory-card screens come after the title).
// The bumper is left out if the X360 movie folder has no eahd_bumper WMV. NFS_BOOT_FLOW=pc keeps the PC list.
//
// Title screen: SplashScreen::SplashScreen (PC 005A3030, X360 in the ctor after 822B8798's vtable) sets up the same
// package on both (the FNGs are byte-identical). The PC shows its prompt C6CC55A4 with LS_TITLE_SCREEN_START_PROMPT_PC
// (9B580B7E); the X360 shows C4DF3FF2 with LS_TITLE_SCREEN_START_PROMPT_XBOX
// (D18D4C4C) and runs the "SHOW" script (001CA7C0) on the ESRB online-play notice BDE7FA72. Both hide the
// HD-compatibility notice group 534CC377 in HD (the X360 shows it only on SD outputs). WS_MW_LS_Splash.fng has
// no C6CC55A4, so the wide title had no prompt: C4DF3FF2 now carries the PC text there. The X360's title (START prompt and the ESRB
// notice, on either package) is the default; NFS_SPLASH_PROMPT=pc keeps the PC text, =none leaves the package as the game sets it.
// NFS_SPLASH_LAYOUT=x360 (default; =pc keeps CURRENT_GEN_WIDESCREEN) sends the wide package the X360's XENON_WS instead of CURRENT_GEN_WIDESCREEN: its backdrop
// WS_XENON_SPLASHBACK and title layout (widescreen.c).
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "host.h"

int widescreen_boot_assets(void);           // widescreen.c: 0 off, 1 widescreen assets, 2 and an HD mode
int movies_x360_has(const char *name);      // movies.c
void widescreen_send_xenon_ws(Cpu *c, int title, uint32_t pkg);   // widescreen.c

#define FN_malloc 0x00652AD0u               // the builder's node allocator (jmp _malloc)
#define FN_FEManager_Get 0x00516E10u
#define FN_FEngFindObject 0x00524850u       // cdecl (const char *pkg, uint hash)
#define FN_FEngSetScript 0x00524920u        // cdecl (const char *pkg, uint obj, uint script, bool)
#define FN_FEngSetLanguageHash 0x00525220u  // cdecl (const char *pkg, uint obj, uint string)
#define FN_FEngSetInvisible 0x00527DB0u     // cdecl (const char *pkg, uint obj)
#define FN_FEngSetVisible 0x00527DF0u
#define BOOTFLOWSCREEN_VTABLE 0x008A0F48u
// BootFlowManager: +4 list head (next, prev), +0xC current screen; a BootFlowScreen is {vtable, next, prev, name},
// linked through +4. FEManager +0xC: the screen the front end starts with.
#define OBJ_PROMPT_PC 0xC6CC55A4u
#define OBJ_PROMPT_X360 0xC4DF3FF2u
#define OBJ_ESRB_ONLINE 0xBDE7FA72u
#define STR_PROMPT_PC 0x9B580B7Eu
#define STR_PROMPT_X360 0xD18D4C4Cu
#define SCRIPT_SHOW 0x001CA7C0u

static int env_is(const char *name, const char *value) { const char *e = getenv(name); return e && !strcasecmp(e, value); }

static uint32_t name_str(const char *s) {   // guest copies of the names, made once
    static struct { const char *s; uint32_t g; } k[8];
    for (int i = 0; i < 8; ++i) {
        if (!k[i].s) { k[i].s = s; k[i].g = gstr(s); }
        if (!strcmp(k[i].s, s)) return k[i].g;
    }
    return gstr(s);
}

void f_0058E9C0_orig(Cpu *c);
void f_0058E9C0(Cpu *c) {                    // BootFlowManager::BootFlowManager (thiscall)
    uint32_t self = c->ecx;
    f_0058E9C0_orig(c);
    uint32_t head = self + 4, eax = c->eax;
    int ws = widescreen_boot_assets(), x360 = !env_is("NFS_BOOT_FLOW", "pc");
    if (x360 && ws) {
        static const char *const wide[][2] = {{"LS_EAlogo.fng", "WS_LS_EAlogo.fng"}, {"LS_PSA.fng", "WS_LS_PSA.fng"},
                                              {"MW_LS_AttractFMV.fng", "WS_MW_LS_AttractFMV.fng"}};
        uint32_t logo = 0;
        for (uint32_t n = M32(head); n && n != head; n = M32(n)) {
            const char *s = gptr(M32(n + 8));
            for (unsigned k = 0; s && k < sizeof wide / sizeof *wide; ++k)
                if (!strcasecmp(s, wide[k][0])) M32(n + 8) = name_str(wide[k][1]);
            if (!logo && s && strcasestr(s, "LS_EAlogo.fng")) logo = n;
        }
        if (logo && ws == 2 && movies_x360_has("eahd_bumper")) {
            uint32_t size = 0x10, a = call_guest(c, FN_malloc, 1, &size);
            if (a) {
                uint32_t p = a + 4, prev = M32(logo + 4);
                M32(a) = BOOTFLOWSCREEN_VTABLE;
                M32(a + 0xC) = name_str("WS_LS_EA_hidef.fng");
                M32(p) = logo; M32(p + 4) = prev; M32(prev) = p; M32(logo + 4) = p;
                if (M32(self + 0xC) == logo - 4) {   // the EA logo was the first screen: start with the bumper
                    M32(self + 0xC) = a;
                    M32(call_guest(c, FN_FEManager_Get, 0, NULL) + 0xC) = M32(a + 0xC);
                }
            }
        }
    }
    if (diag_on) {
        char list[512];
        int len = 0;
        for (uint32_t n = M32(head); n && n != head && len < (int)sizeof list - 80; n = M32(n))
            len += snprintf(list + len, sizeof list - len, "%s%s%s", len ? ", " : "", n - 4 == M32(self + 0xC) ? "*" : "", gptr(M32(n + 8)));
        diag_event("bootflow: %s list (widescreen assets %d, eahd_bumper WMV %d): %s", x360 ? "X360" : "PC", ws,
                   movies_x360_has("eahd_bumper"), list);
    }
    c->eax = eax;
}

void f_005A3030_orig(Cpu *c);
void f_005A3030(Cpu *c) {                    // SplashScreen::SplashScreen (thiscall, ScreenConstructorData *)
    uint32_t self = c->ecx;
    f_005A3030_orig(c);
    uint32_t eax = c->eax, pkg = M32(self + 0x10);
    widescreen_send_xenon_ws(c, 1, pkg);
    int x360 = !env_is("NFS_SPLASH_PROMPT", "pc");
    if (!pkg || env_is("NFS_SPLASH_PROMPT", "none")) return;
    uint32_t a[4] = {pkg, OBJ_PROMPT_PC};
    int has_pc_prompt = call_guest(c, FN_FEngFindObject, 2, a) != 0;
    if (x360 || !has_pc_prompt) {            // the X360's prompt object (the only one the wide package has)
        if (has_pc_prompt) { a[1] = OBJ_PROMPT_PC; call_guest(c, FN_FEngSetInvisible, 2, a); }
        a[1] = OBJ_PROMPT_X360; call_guest(c, FN_FEngSetVisible, 2, a);
        a[2] = x360 ? STR_PROMPT_X360 : STR_PROMPT_PC; call_guest(c, FN_FEngSetLanguageHash, 3, a);
    }
    if (x360) { a[1] = OBJ_ESRB_ONLINE; a[2] = SCRIPT_SHOW; a[3] = 1; call_guest(c, FN_FEngSetScript, 4, a); }
    if (diag_on) diag_event("bootflow: title %s, prompt %s", gptr(pkg), x360 ? "X360 (START, ESRB notice)" : has_pc_prompt ? "PC" : "PC text in the X360 prompt");
    c->eax = eax;
}
