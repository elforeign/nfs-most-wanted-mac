// One-shot career awards. No preference, save-file writer or persistent switch.
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include "cash_cheat.h"
#include "settings.h"
#include "roam_online.h"

#define FE_DATABASE 0x0091CF90u
#define GAME_FLOW 0x00925E90u
#define RACE_STATUS 0x0091E000u
#define CFENG_INSTANCE 0x0091CADCu
#define ENTRY_MAX 9999999u
// GetCash (0050E300) and %$d (_bOutput, 00462A80) use signed 32-bit cash;
// '$' only adds digit grouping. Neither AwardCash (00566010) nor the cash
// marker add (007B3DF1) clamps it. No smaller display limit: leave 10,000,000
// reward headroom. Cheat ceiling = 2,137,483,647; never reduce existing cash.
#define CASH_MAX ((uint32_t)INT32_MAX - 10000000u)

static uint32_t pending_profile;
static uint64_t pending_cash;
static const char unavailable[] = "Load a profile and enter Career to add cash.";
void portmenu_status(const char *fmt, ...);

static const char *unavailable_reason(void) {
    return atomic_load_explicit(&roam_online_active, memory_order_relaxed) ?
        "Leave online free roam to add cash." : unavailable;
}

static uint32_t career_profile(void) {
    if (atomic_load_explicit(&roam_online_active, memory_order_relaxed)) return 0;
    uint32_t db = M32(FE_DATABASE), profile = db ? M32(db + 0x10) : 0;
    // IsProfileNamed (0056DB10), HasCareerStarted (0050E310), FE career bit.
    if (!profile || !M8(profile + 0x20) || !(M32(profile + 0xAC) & 1) || !(M32(db + 0x12C) & 1)) return 0;
    // Quick races can retain a career profile: Game_IsCareerMode (00605610)
    // uses GRaceStatus::mRaceMode == 2, rather than the front-end mode bit.
    if (M32(GAME_FLOW) == 6) {
        uint32_t race = M32(RACE_STATUS);
        if (!race || M32(race + 0x1964) != 2) return 0;
    } else if (M32(GAME_FLOW) != 3) return 0; // no awards during loading
    return profile;
}

static void queue_cash(uint32_t amount) {
    uint32_t profile = career_profile();
    if (!profile) { portmenu_status("%s", unavailable_reason()); return; }
    if (pending_profile != profile) pending_cash = 0;
    pending_profile = profile;
    // Several input events can arrive before Present. Keep every click, safely.
    pending_cash += amount;
    if (pending_cash > CASH_MAX) pending_cash = CASH_MAX;
}
void cash_cheat_20000(int value) { (void)value; queue_cash(20000); }
void cash_cheat_50000(int value) { (void)value; queue_cash(50000); }
void cash_cheat_100000(int value) { (void)value; queue_cash(100000); }

void cash_cheat_typed(int value) {
    (void)value;
    Setting *row = settings_find("gameplay.cash_amount");
    if (!row) return;
    const unsigned char *p = (const unsigned char *)row->text;
    uint32_t amount = 0;
    unsigned digits = 0;
    unsigned char grouping = 0;
    if (!*p) goto invalid;
    for (; *p; ++p) {
        if (*p == ',' || *p == ' ') {
            if (!digits || (grouping ? *p != grouping || digits != 3 : digits > 3)) goto invalid;
            grouping = *p;
            digits = 0;
            continue;
        }
        if (*p < '0' || *p > '9' || amount > (ENTRY_MAX - (*p - '0')) / 10) goto invalid;
        amount = amount * 10 + (*p - '0');
        ++digits;
    }
    if (!amount || !digits || (grouping && digits != 3)) goto invalid;
    queue_cash(amount);
    row->text[0] = 0; // a submitted entry cannot be replayed by pressing Enter
    return;
invalid:
    portmenu_status("Enter ASCII digits only, from 1 to 9,999,999 (commas or spaces may group thousands).");
}

void cash_cheat_status(char *buf, int n) {
    snprintf(buf, (size_t)n, "%s", career_profile() ? "" : unavailable_reason());
}

static int cash_header(uint32_t fn) {
    // RefreshHeader slots of the cash-bearing shop screens. Calling arbitrary
    // MenuScreen virtuals here would also run dialog/flow operations.
    switch (fn) {
    case 0x007B0620u: case 0x007B0E00u: case 0x007B1070u: case 0x007B1210u:
    case 0x007B1620u: case 0x007B1DB0u: case 0x007B1F30u: case 0x007B25E0u:
    case 0x007B26E0u: case 0x007B27E0u: case 0x007B2A80u: case 0x007B2FF0u:
    case 0x007B8870u: return 1;
    default: return 0;
    }
}

static void refresh_cash(Cpu *c, uint32_t cash) {
    uint32_t instance = M32(CFENG_INSTANCE), fe = instance ? M32(instance) : 0;
    if (!fe || fe == 0xFFFFFF28u) return;
    // Include parent packages: a dialog can own control over the shop's header.
    for (uint32_t package = M32(fe + 0xE4); package; package = M32(package + 8)) {
        uint32_t name = M32(package + 0xC);
        if (!name) continue;
        uint32_t screen = call_guest(c, 0x00591E90u, 1, &name); // FEngFindScreen
        uint32_t vtable = screen ? M32(screen) : 0, fn = vtable ? M32(vtable + 0xC) : 0;
        if (cash_header(fn)) {
            c->ecx = screen;
            call_guest(c, fn, 0, NULL); // recomputes cash, cart balance and prices
        }
        // Race sheet / milestones have a secondary ArrayScroller vtable at
        // +0x2C; 00547730 / 0052FB70 expect that adjusted this pointer. Their
        // cash label is identical: hash F91A59F6, localized CASH + "%s %$d".
        // Update just that label, without rerunning track/medal header work.
        if (vtable == 0x0089D7ACu || vtable == 0x0089EEDCu) {
            static uint32_t format;
            if (!format) format = gstr("%s %$d");
            uint32_t cash_label = 0x073B79E0u;
            uint32_t label = call_guest(c, 0x0056BC10u, 1, &cash_label); // GetLocalizedString
            uint32_t args[] = {name, 0xF91A59F6u, format, label, cash};
            call_guest(c, 0x005252D0u, 5, args); // FEPrintf
        }
        // uiCareerCrib::Setup (00532220) prints this only on construction.
        // Update its string without rebuilding the menu or resetting selection.
        if (!strcasecmp((const char *)gptr(name), "MainMenu_Sub.fng")) {
            static uint32_t format, key;
            if (!format) {
                format = gstr("%$d");
                uint32_t text = gstr("CASH_DATA");
                key = call_guest(c, 0x005AF1C0u, 1, &text); // FEHashUpper
            }
            uint32_t args[] = {name, key, format, cash};
            call_guest(c, 0x005252D0u, 4, args); // FEPrintf
        }
    }
}

// The single frame hook runs on the game thread, including while F10 holds sim.
void cash_cheat_frame(Cpu *c) {
    uint32_t profile = career_profile();
    static const char *const ids[] = {"gameplay.cash_20000", "gameplay.cash_50000", "gameplay.cash_100000", "gameplay.cash_amount"};
    for (unsigned i = 0; i < sizeof ids / sizeof *ids; ++i) {
        Setting *row = settings_find(ids[i]);
        if (!row) continue;
        // SET_INFO is gray and cannot be activated with keyboard, mouse or pad.
        row->type = !profile ? SET_INFO : i == 3 ? SET_TEXT : SET_ACTION;
        row->help = !profile ? unavailable_reason() : i == 3 ?
            "Type ASCII digits (1..9,999,999; commas or spaces may group thousands), then Enter. Saved with your career." :
            "Add cash once to this career. Saved with your profile as usual.";
    }
    uint64_t amount = pending_cash;
    uint32_t target = pending_profile;
    pending_cash = 0;
    pending_profile = 0;
    if (!amount) return;
    if (!profile || profile != target) { portmenu_status("Career unavailable or changed; cash was not added."); return; }
    uint32_t before = M32(profile + 0xB4);
    if (before > CASH_MAX) { portmenu_status("Cash balance is outside the supported range."); return; }
    if (amount > CASH_MAX - before) amount = CASH_MAX - before;
    if (amount) {
        Cpu saved = *c;
        uint32_t award = (uint32_t)amount;
        c->ecx = profile + 0xA8;
        call_guest(c, 0x00566010u, 1, &award); // CareerSettings::AwardCash
        refresh_cash(c, M32(profile + 0xB4));
        *c = saved;
    }
    portmenu_status("Added $%u. Cash: $%u%s", (unsigned)amount, M32(profile + 0xB4),
                    M32(profile + 0xB4) == CASH_MAX ? " (limit reached)" : "");
}
