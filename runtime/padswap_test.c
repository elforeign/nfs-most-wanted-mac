// padswap_test.c - controller change during a Control Type 5 frame (review 2026-10-25, controltype.c x padbind.c).
// Includes padbind.c and controltype.c with stub guest memory: the user's own custom pad binding is in the table, a
// DualSense replaces the Xbox pad, and f_006349B0_orig (stub) does what the game's does, polls the joystick through
// padbind_sync (dinput.c d_GetDeviceState). Pass: the Xbox pad's saved set holds the user's binding (not Type 5's
// preset) and the new pad's layout is in the table afterwards. Old code: the preset is saved and the old slots restored
// over the new layout (FAIL, exit 1). Run: runtime/padswap_test.py (builds and runs this).
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>
#define PEDALS_NO_TRACE 1
#include "pedals.c"
void pedals_trace_sample(unsigned step,float lt,float rt) { (void)step;(void)lt;(void)rt; }
#include "controltype.c"
#include "padbind.c"

uint8_t *G;
int diag_on;
const char *diag_dir = "";
void diag_event(const char *fmt, ...) { (void)fmt; }
void settings_add(Setting *s) { (void)s; }
static char udir[64];
const char *userdata_dir(void) { return udir; }
int user32_pad_layout_xbox(void) { return 0; }
int pad_identity(char *name, size_t n) { snprintf(name, n, "Controller (DualSense)"); return PAD_KIND_DUALSENSE; }
int pad_identity_n(int idx, char *name, size_t n) { (void)idx; return pad_identity(name,n); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {   // 0063CD80: the new pad's default bindings
    (void)c; (void)n; (void)args;
    if (fn == 0x0063CD80u) set_rec(rec(CT_GAS, 2), T_BUTTON, 0, 77);
    return 0;
}
void f_006349B0_orig(Cpu *c) { padbind_sync(c); }   // the device poll inside the GameAction update
int pad_count(void) { return 1; }                   // one host pad: the split-screen paths stay off
void f_00628940(Cpu *c) { (void)c; }
void f_00634B50_orig(Cpu *c) { (void)c; }
void f_004F6EA0_orig(Cpu *c) { (void)c; }
uint32_t f_0056E180_orig(Cpu *c) { (void)c; return 0; }
void f_0056E200_orig(Cpu *c) { (void)c; }
void f_006280C0_orig(Cpu *c) { (void)c; }
void f_00634900_orig(Cpu *c) { (void)c; }
void f_0063CD80_orig(Cpu *c) { (void)c; }

static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); ++fails; } } while (0)
int main(void) {
    G = calloc(1, 0x1000000);
    snprintf(udir, sizeof udir, "/tmp/padswap_%d", (int)getpid());
    mkdir(udir, 0700);
    const uint32_t dev = 0x00A00000u, iomod = 0x00B00000u;
    M32(JOY) = dev;
    snprintf(joy_name(dev), 64, "%s", XBOX_NAME);
    set_rec(rec(CT_GAS, 2), T_ROT_HI, 0, 5);          // the user's own binding (Type 5 would put ARY_U there)
    s_type.value = 4;                                  // Control type 5
    ct_type = 5;
    Cpu c = {0};
    c.ecx = iomod; c.esp = 0x00C00000u;
    f_006349B0(&c);                                    // one frame: the pad changes while the update polls
    Set *old = set_find("", name_hash(XBOX_NAME), L_NONPS_PS2);
    CHECK(old, "old pad's set was not saved");
    if (old) CHECK(old->r[CT_GAS][0][0] == T_ROT_HI && old->r[CT_GAS][0][2] == 5,
                   "old pad's saved gas is %u,%u,%u, not the user's 7,0,5", old->r[CT_GAS][0][0], old->r[CT_GAS][0][1], old->r[CT_GAS][0][2]);
    CHECK(!strcmp(joy_name(dev), "Controller (DualSense)"), "joystick is %s", joy_name(dev));
    CHECK(M32(rec(CT_GAS, 2) + 8) == 77, "table gas obj %u after the frame, not the new pad's default 77", M32(rec(CT_GAS, 2) + 8));
    printf(fails ? "padswap_test: %d failed\n" : "padswap_test: ok\n", fails);
    return fails != 0;
}
