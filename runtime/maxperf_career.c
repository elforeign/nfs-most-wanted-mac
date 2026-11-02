// Career Max Performance uses the game's confirmation, locked-part selection and paid cart.
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "host.h"
#include "settings.h"

static int enabled = -1;
static int maxperf_on(void) {
    if (enabled < 0) {
        const char *e = getenv("NFS_MAXPERF_CAREER");
        enabled = !e || strcasecmp(e, "retail");
    }
    return enabled;
}
static void maxperf_apply(int v) { enabled = v == 0; }
static const char *const choices[] = {"On", "Retail"};
static const char *const values[] = {"on", "retail"};
static Setting option = {.id = "gameplay.maxperf_career", .category = SET_GAMEPLAY, .order = 55,
    .label = "Max Performance in career", .help = "Add the best unlocked performance parts to your cart. Checkout still charges. Retail hides this action in career.",
    .type = SET_CHOICE, .choices = choices, .values = values, .nchoices = 2, .def = 0,
    .env = "NFS_MAXPERF_CAREER", .apply = maxperf_apply};
__attribute__((constructor)) static void maxperf_register(void) { settings_add(&option); }

#define MAXPERF_PAD_Y 30.0f // console group: its stripe sits 23 below the group origin
#define MAXPERF_PC_Y 53.0f  // keyboard group: its stripe is centred on the group origin

static int career_shop(uint32_t self) {
    uint32_t db = M32(0x0091CF90u);
    return maxperf_on() && M32(self + 0x150) == 0x802 &&
        ((db && (M8(db + 0x12C) & 1)) || M8(0x009B9E8Du));
}

// Moves a prompt group (data = FEObject data block) to row y by a relative Y step, which also shifts every
// script track's Y and leaves each X/Z animation alone, so SHOW/HIDE cannot put it back.
static void place_row(Cpu *c, uint32_t obj, float want_y) {
    uint32_t data = obj ? M32(obj + 0x2C) : 0;
    if (!data) return;
    float current_y;
    memcpy(&current_y, G + data + 0x20, 4);
    float y = want_y - current_y;
    if (y == 0.0f) return;
    static uint32_t vector;
    if (!vector) vector = galloc(12);
    M32(vector) = 0;
    memcpy(G + vector + 4, &y, 4);
    M32(vector + 8) = 0;
    uint32_t pos[2] = {vector, 1};
    c->ecx = obj;
    call_guest(c, 0x005B8190u, 2, pos);
}

// RefreshHeader still owns the retail answer. Override only the newly enabled career case.
void maxperf_career_header(Cpu *c, uint32_t self) {
    Cpu saved = *c;
    uint32_t a[2] = {M32(self + 0x10), 0x8805E665u};
    int show = career_shop(self);
    uint32_t pc = call_guest(c, 0x00524850u, 2, a);
    if (show) call_guest(c, 0x00514CC0u, 1, &pc);
    a[1] = 0x5AEC8D91u;
    uint32_t pad = call_guest(c, 0x00524850u, 2, a);
    // CustomizeCategory.fng is byte-identical on PC and console. Retail leaves the console prompt group at
    // y=117 (over Cash/Total, root y=125, box y 74..156) and the keyboard one at y=156 (over Showcase's
    // row, y 159..189 on console). Career shows the prompt on its own row above Cash/Total: icon and
    // stripe end at y=69 (console) / 68 (keyboard), clear of the box. runtime/maxperf_layout_test.py checks it.
    place_row(c, pad, show ? MAXPERF_PAD_Y : 117.0f);
    place_row(c, pc, show ? MAXPERF_PC_Y : 156.0f);
    *c = saved;
}

void f_007BB870_orig(Cpu *c);
void f_007BB870(Cpu *c) {
    uint32_t self = c->ecx;
    int open = arg(c, 0) == 0xC519BFC3u && career_shop(self);
    // Includes the category screen's notification; the retail career gate returns without a dialog.
    f_007BB870_orig(c);
    if (!open) return;
    Cpu saved = *c;
    uint32_t a[] = {M32(self + 0x10), 0x00890978u, 3, 0x70E01038u, 0x417B25E4u,
                    0x6820E23Eu, 0xB4EDEB6Du, 0, 0x892CB612u};
    call_guest(c, 0x00598DD0u, 9, a);
    c->ecx = self;
    call_guest(c, M32(M32(self) + 0xC), 0, NULL);
    *c = saved;
}
