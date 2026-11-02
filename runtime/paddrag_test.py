#!/usr/bin/env python3
"""Live race binding selection through translated GameDevice update/evaluator/poll.
ASan/UBSan; fake guest memory and controller backend, no game or devices.
PASS: 3,780 checks / 0 failures (Speedbreaker on Square in fresh Drag defaults: old padbind.c 10 / 3,780 failures). Original 64bf1349c4d62a285ba63af6ab6fb3aa6daa5537
capture: 15 / 3,621 failures; seed: 4 / 3,627; combined: 19 / 3,621.
Capture using the live page instead of the edited page: 15 / 3,621 failures.
Stored job 2 source: 701 / 3,627 failures. Broken restore / pause guard /
premature seeding / indexed-only detection / preset over Drag / remapped Drag:
666 / 32 / 1 / 64 / 75 / 17 failures out of 3,627 (all behavioral rejections).
Both non-PS layouts: release, translated R1/L1/Cross capture, held suppression,
then matching physical drive/evaluation; seeded Speedbreaker (Square) and Default capture.
Historical C is reconstructed from stored text and byte-verified by SHA-256;
no Git history is consulted. Original mapping/policy/seed excerpts are pinned below.
"""
from pathlib import Path
import importlib.util
import hashlib
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent

def pinned_source(file, edits, digest):
    """Restore stored historical text; hashes pin both edited spans and full C."""
    source = (R/file).read_text()
    if file == 'controltype.c':
        # Restore only provenance comments in this historical baseline. Keep
        # every stored body/span digest unchanged; production uses authored data.
        source = source.replace('// GameAction IDs must match the guest evaluator and the Controls/Drag binding pages.\n', '')
        initializer = 'static const CtMap ct_buttons[4][CT_NBUTTONS] = ' + '{'
        source = source.replace(initializer + '\n', initializer +
                                '   // all-empty (every type keeps the player\'s buttons) with no X360 copy\n')
        source = source.replace('// port-authored action/control recipes, also in PC-only builds',
                                '// tools/gen_tables.py, from the player\'s X360 ATTRIBUTES.BIN')
    # Timing wrappers do not alter the stored binding baseline or its line offsets.
    if file == 'padbind.c':
        source = source.removeprefix('#include "save_timing.h"\n')
        source = source.replace('SAVE_TIME("stream.close", fclose(f))', 'fclose(f)')
        source = source.replace('SAVE_TIME("rename", rename(tmp, p))', 'rename(tmp, p)')
    lines = source.splitlines(keepends=True)
    for start, end, expected, original in reversed(edits):
        assert hashlib.sha256(''.join(lines[start:end]).encode()).hexdigest() == expected, file
        lines[start:end] = original.splitlines(keepends=True)
    source = ''.join(lines)
    assert hashlib.sha256(source.encode()).hexdigest() == digest, file
    return source


# 1743375058250fc00265fb9f0c5a8c5ad9306cbe:runtime/padbind.c; SHA-256 verifies the stored source.
PINNED_JOB2_PAD = pinned_source('padbind.c', (
    (46, 53, 'cc46e4663ce4af6db2072f46e7d03996b527396f8051e15575dfadd9d0611c4c', r'''// user32.m's P_* (the PlayStation pad's DirectInput buttons)
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
enum { A_GAS, A_BRAKE, A_GAMEBREAKER = 5, A_SHIFTDOWN = 7, A_SHIFTUP = 8, A_NEXTSONG = 24, A_FE_B0 = 37, A_FE_B3 = 39, A_FE_B4 = 40 };

static uint32_t rec(int a, int slot) { return GA + 0x34u * (uint32_t)a + 4 + 12u * (uint32_t)slot; }
static void set_rec(uint32_t r, uint32_t t, uint32_t d, uint32_t o) { M32(r) = t; M32(r + 4) = d; M32(r + 8) = o; }
'''),
    (67, 74, 'd0bbf823d4757efe62fc555869c05fe1740815920fd08afba0e0e8f19baa77a2', r'''static uint32_t legacy_defaults[GA_PAGE][2][3], nonps_defaults_page[GA_PAGE][2][3];
static int legacy_defaults_valid;
static int active_layout;
static int edited_set = PADBIND_DEFAULT;
static uint32_t joy(void) { uint32_t d = M32(JOY); return d && d != 0xFFFFFFFCu ? d : 0; }
static char *joy_name(uint32_t dev) { return (char *)G + dev + 0x114; }
'''),
    (235, 242, '1fd8aeda0e157fca543f39d9fcc2ef0ac09a346ab5be0ad4f09434d74e8f13fa', r'''void f_0063CD80(Cpu *c) {                      // cdecl (void): the joystick's defaults, by its name
    uint32_t dev = joy();
    if (!dev) { f_0063CD80_orig(c); return; }
    char name[64];
    snprintf(name, sizeof name, "%s", joy_name(dev));
    int ps = name_ps(name);
'''),
    (258, 265, 'dcad03c4f72f67c7bc90ec3e7e5c82bd28abca624a74eb5fa84a9b6bc1efda3a', r'''                nonps_defaults_page[a][k][i] = M32(rec(a, k + 2) + 4u * (uint32_t)i);
    }
    active_layout = layout_for(name);
    char why[96];
    snprintf(why, sizeof why, "defaults for %s", name);
    dump(why);
'''),
    (447, 480, 'a2545a68aa67158568063a5b06c5f2e32e83ce7702ba9b492a01a12cfa162bf5', r'''static void seed_drag(Set *row, const Set *base) {
    *row = *base;
    row->binding_set = PADBIND_DRAG;
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) {
            uint32_t *r = row->r[a][k];
            if (!r[1] && ((r[2] == 2 && (r[0] == T_AXIS_HI || r[0] == T_ROT_HI)) ||
                (r[0] == T_BUTTON && (r[2] == P_R1 || r[2] == P_L1 || r[2] == P_R2 || r[2] == P_L2))))
                memset(r, 0, 12);
        }
    const int actions[] = {A_GAS, A_BRAKE, A_SHIFTUP, A_SHIFTDOWN};
    const uint32_t bindings[][3] = {{T_ROT_HI, 0, 2}, {T_AXIS_HI, 0, 2},
                                   {T_BUTTON, 0, P_R1}, {T_BUTTON, 0, P_L1}};
    for (int i = 0; i < 4; ++i) memcpy(row->r[actions[i]][0], bindings[i], 12);
}
static Set *drag_for(const char *profile, const char *name, int layout) {
    uint32_t hash = name_hash(name);
'''),
    (510, 559, '5d6810fef3f4df21c02cfb559f74a078e709191475ef11155467ac0df504e9a5', r'''    edited_set = binding_set;
    return 1;
}
int padbind_input_set(void) { return PADBIND_DEFAULT; }
int padbind_effective_binding(int binding_set, int action, uint32_t out[6]) {
    if (!out || action < 0 || action >= GA_PAGE ||
        (binding_set != PADBIND_DEFAULT && binding_set != PADBIND_DRAG)) return 0;
'''),
    (571, 580, '5667f2c6feb6f309f30ffd537f9277c16d0d6f016e634abfd83f401ff44f6c94', r'''static uint32_t input_saved[GA_PAGE][6];
static int input_installed, input_previous_set;
int padbind_input_begin(int binding_set) {
    if (binding_set == edited_set || input_installed || !joy()) return 0;
    uint32_t page[GA_PAGE][6];
    for (int a = 0; a < GA_PAGE; ++a)
        if (!padbind_effective_binding(binding_set, a, page[a])) return 0;
'''),
    (582, 597, 'a20fc74f4ac0b16e79cfb46d8eb81ae265c39a1292707fefe000c373734daf11', r'''        memcpy(G + rec(a, 2), page[a], 24);
    }
    input_previous_set = edited_set; edited_set = binding_set; input_installed = 1;
    return 1;
}
void padbind_input_end(int swapped) {
    if (!swapped) return;
'''),
    (602, 624, '48e10ac742d60bc6f14817ae824e47cdf215d2701643b9dc4d2c775609c6a92a', r'''    call_guest(c, 0x0063CD80u, 0, NULL);
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
}

void f_0056E180_orig(Cpu *c);
// BEGIN offline bindings transaction: no disk writes until the career save task publishes its file.
'''),
    (776, 783, '0ddd7bc84cba12d5b09b421eaf5d0941118f3c3b16a8aa73fe6e6ddb692a675c', r'''    if (!fe || fe == 0xFFFFFF28u) return 0;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) {
        const char *n = gptr(M32(p + 0xC));
        if (n && strcasestr(n, "Options_PC_Controller")) return 1;
    }
    return 0;
}
'''),
    (829, 838, '0fa4b4aaef2a5339c298dbbf5e5dc9b99bad7a908d597a863fcdd3d9b0f72c3a', r'''static int swap_on;
void padbind_swap(int on) { swap_on = on; }
static void follow_pad(Cpu *c) {
    uint32_t dev = joy();
    char want[64];
    if (!dev || pad_identity(want, sizeof want) == PAD_KIND_NONE || !want[0]) return;
'''),
), '197299a0234c1a529ca7414b1e2bddfdfb0f392339f5f27a1c5361a3dda1b9bc')

# 1743375058250fc00265fb9f0c5a8c5ad9306cbe:runtime/controltype.c; SHA-256 verifies the stored source.
PINNED_JOB2_HOOK = pinned_source('controltype.c', (
    (92, 101, 'b06ec52740a1d33063ad5f584cb2ad6d56c9df883e06da0a094692ed682cac8e', r'''    .apply = ct_apply};
__attribute__((constructor)) static void controltype_register(void) { settings_add(&s_type); }

int controltype_passthrough(void) {             // user32.m: the driving buttons as the pad's own (variants 2-5)
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    return ct_type > 1 && ct_variant(ct_type) != 0;
}

// Split-screen producer, item 1: creation of the second logical device is separate.
'''),
    (151, 172, '4f27edd8db567b2ce6dcc8ea7a40e61dc52b1aa866ab17e424d08d96aabf18fa', r'''void f_006349B0(Cpu *c) {                       // fastcall (IOModule device): every GameAction's value, each frame
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    uint32_t dev = c->ecx;
    int port = pad_count() == 2 && M32(dev + 0x24) == 1;
    int port_swapped = port ? padbind_port_begin(1) : 0;
    int input_swapped = 0;
    if (!port && !M8(dev + 0x183C)) {
        if (ct_type >= 2) padbind_follow(c);
        input_swapped = padbind_input_begin(padbind_input_set());
    }
    if (ct_type < 2 || M8(dev + 0x183C)) {
        ct_update(c); padbind_input_end(input_swapped); padbind_port_end(port_swapped); return;
    }   // saved bindings, or a binding capture in progress
    // FUN_00628940 reads each non-empty binding and the largest wins: the controller slots of the actions this type
    // changes hold the preset's control for the call (slot 3 empty), then the user's own come back.
'''),
), '5a42b5bbffa12a2ec34847fe5e10a78e5ac733f54a2c4e41f2290495f256a662')
# Original 64bf1349c4d62a285ba63af6ab6fb3aa6daa5537:runtime/user32.m
ORIGINAL_LAYOUT = r'''static void pad_apply_layout(int idx, PadState *st, uint32_t phys) {
    uint32_t sim = M32(SIM_INSTANCE);
    // Driving: racing (game-flow 6), the sim not paused, and no menu package in control (the race results come up
    // while the sim still runs; the HUD packages are HUD_*).
    const char *ctl = fe_package_in_control();
    int menu = ctl && strncasecmp(ctl, "HUD_", 4);
    int driving = M32(GAMEFLOW_STATE) == 6 && !(sim && M32(sim + 0x2C) == 4) && !menu;
    int remap = pad_remap_listening();
    // A button held while re-mapping starts (the Cross that picked the field: Accept in menus) or ends (the button
    // just bound) is left out until released, so it is neither bound nor taken as a new menu press.
    static int last_remaps[2];
    static uint32_t remap_helds[2];
    int *last_remap = &last_remaps[idx];
    uint32_t *remap_held = &remap_helds[idx];
    if (remap != *last_remap) { *last_remap = remap; *remap_held = phys; }
    *remap_held &= phys;
    phys &= ~*remap_held;
    pad_mode = remap ? "remap" : driving ? "driving" : "menus"; pad_ctl = ctl;
    static const char *last_mode;
    if (diag_on && pad_mode != last_mode) {
        last_mode = pad_mode;
        diag_event("pad: %s (package in control %s)", pad_mode, ctl ? ctl : "none");
        pad_diag_bindings();
    }
    if (remap) driving = 1;
    // While re-mapping, the right stick is its own axis (Y Rotation) and isn't also sent as gas/brake buttons.
    int up = !remap && st->ry > 0.5f, down = !remap && st->ry < -0.5f;
    uint32_t gas, ebrake, brake, speedbreaker, lookback, nitrous, shift_up, shift_down, camera = 0;
    if (pad_layout_xbox()) {
        gas = brake = 0;                     // axes drive pedals; a button alias would dominate them at 1
        ebrake = bit(phys, P_CROSS); nitrous = bit(phys, P_CIRCLE);
        speedbreaker = bit(phys, P_SQUARE); lookback = bit(phys, P_TRIANGLE); camera = bit(phys, P_R1);
        shift_up = up; shift_down = down;
    } else {
        gas = bit(phys, P_CROSS) | up; brake = bit(phys, P_SQUARE) | down; ebrake = bit(phys, P_R1); nitrous = bit(phys, P_L1);
        speedbreaker = bit(phys, P_CIRCLE) | bit(phys, P_R3); lookback = bit(phys, P_TRIANGLE);
        shift_up = !remap && (bit(phys, P_R2) | (st->rt > 0.5f));
        shift_down = !remap && (bit(phys, P_L2) | (st->lt > 0.5f));
    }
    int xb = pad_layout_xbox();
    uint32_t back = bit(phys, xb ? P_CIRCLE : P_TRIANGLE), fourth = bit(phys, xb ? P_TRIANGLE : P_CIRCLE);
    uint32_t v = fourth << 0 | bit(phys, P_CROSS) << 1 | back << 2 | (phys & 0x30) | bit(phys, P_OPTIONS) << 7;   // menus
    // A button held from driving into a menu (Options pausing) is no key there.
    static uint32_t held_drivings[2];
    uint32_t *held_driving = &held_drivings[idx];
    uint32_t keys = bit(phys, P_SQUARE) | bit(phys, P_CREATE) << 1 | bit(phys, P_OPTIONS) << 2;
    *held_driving = driving ? keys : *held_driving & keys;
    if (idx == 0) pad_menu_keys(keys & ~*held_driving);
    if (driving)
        v = gas << 0 | ebrake << 1 | brake << 2 | speedbreaker << 3 | lookback << 4 | bit(phys, P_CREATE) << 5 | nitrous << 6 |
            bit(phys, P_OPTIONS) << 7;
    st->buttons = v | shift_up << 8 | shift_down << 9 | camera << 10 | bit(phys, P_L3) << 11;   // L3: next song (padbind.c XBOX_L3)
    // Control Types 2-5 / 7-10 (controltype.c) bind the physical buttons (P_* order) while driving; L3 stays next song.
    if (driving && !remap && controltype_passthrough()) st->buttons = (phys & 0x3FFu) | bit(phys, P_L3) << 11;
    // Keep physical pressures for Z/Rz, including while capturing a binding.
}'''
assert hashlib.sha256(ORIGINAL_LAYOUT.encode()).hexdigest() == 'df120eb994396e990eb8701c600bd1f74946e8689553a1556bd5a1b491d9b5c3'

# Original 64bf1349c4d62a285ba63af6ab6fb3aa6daa5537:runtime/controltype.c
ORIGINAL_POLICY = r'''int controltype_passthrough(void) {             // user32.m: physical buttons for Drag and variants 2-5
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    return padbind_input_set() == PADBIND_DRAG || (ct_type > 1 && ct_variant(ct_type) != 0);
}'''
assert hashlib.sha256(ORIGINAL_POLICY.encode()).hexdigest() == 'b74226a38c109901953a1f3c71b9fca208bf78cd2b877ef4207785912863bc0a'

# Original 64bf1349c4d62a285ba63af6ab6fb3aa6daa5537:runtime/padbind.c
ORIGINAL_SEED = r'''static void seed_drag(Set *row, const Set *base) {
    *row = *base;
    row->binding_set = PADBIND_DRAG;
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) {
            uint32_t *r = row->r[a][k];
            if (!r[1] && ((r[2] == 2 && (r[0] == T_AXIS_HI || r[0] == T_ROT_HI)) ||
                (r[0] == T_BUTTON && (r[2] == P_R1 || r[2] == P_L1 || r[2] == P_R2 || r[2] == P_L2 || r[2] == P_CROSS))))
                memset(r, 0, 12);
        }
    const int actions[] = {A_GAS, A_BRAKE, A_SHIFTUP, A_SHIFTDOWN, A_N2O};
    const uint32_t bindings[][3] = {{T_ROT_HI, 0, 2}, {T_AXIS_HI, 0, 2},
                                   {T_BUTTON, 0, P_R1}, {T_BUTTON, 0, P_L1}, {T_BUTTON, 0, P_CROSS}};
    for (int i = 0; i < 5; ++i) memcpy(row->r[actions[i]][0], bindings[i], 12);
}'''
assert hashlib.sha256(ORIGINAL_SEED.encode()).hexdigest() == 'bd3e6452ba17307ae3af2b5e099a9ef09baf4f0025dab0f79fb7db48c1a7c5ec'

spec = importlib.util.spec_from_file_location('padguest', R/'padguest_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

EXTRA = r'''
static const char *scratch;
const char *diag_dir="";
const char *userdata_dir(void) { return scratch; }
static int menu_open;
int portmenu_is_open(void) { return menu_open; }
int pad_count(void) { return 1; }
int user32_pad_layout_xbox(void) { return 1; }
int pad_identity(char *name,size_t n) { snprintf(name,n,"Controller (DualSense)"); return PAD_KIND_DUALSENSE; }
int pad_identity_n(int p,char *name,size_t n) { (void)p; return pad_identity(name,n); }
void pedals_trace_sample(unsigned s,float l,float r) { (void)s;(void)l;(void)r; }
void f_006280C0_orig(Cpu *c) { (void)c; }
void f_00634900_orig(Cpu *c) { ++capture_calls; c->esp+=4; }
void f_004F6EA0_orig(Cpu *c) { (void)c; }
void f_0056E180_orig(Cpu *c) { (void)c; }
void f_0056E200_orig(Cpu *c) { (void)c; }
static int defaults_probe;
void f_0063CD80_orig(Cpu *c) {
    if(defaults_probe) { defaults_probe=0; padbind_sync(c); }
    c->esp+=4;
}
void f_005FA780(Cpu *c) { c->esp+=8; }
void f_00454810(Cpu *c) { c->eax=c->ecx; c->esp+=12; }
void f_006269B0(Cpu *c) { c->eax=0x280000; c->esp+=4; }
void f_00460D20(Cpu *c) { c->eax=strcmp((char *)G+arg(c,0),(char *)G+arg(c,1)); c->esp+=4; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
    (void)n;(void)args;
    if(fn==0x0063CD80u) f_0063CD80(c);
    return 0;
}
'''

MAIN = r'''
static uint32_t defaults[19][6], drag[19][6];
static void physical(void) {
    memset(fake_pad,0,sizeof fake_pad);
    for(int a=0;a<3;a++) { put(0,4*a,32768); put(0,0x20c+4*a,32768); }
    put(0,0x218,32768); put(0,0x21c,32768); put(0,0x220,0xffffffff);
    put(0,8,49151); put(0,0x214,57343); // independent L2 ~.5, R2 ~.75
    for(int b=0;b<32;b++) put(0,0xc+4*b,b%3==0?128:0);
}
static void frame(int selected,int evaluate) {
    Cpu refresh=cpu(d[0]); padbind_race_update(&refresh);
    CHECK(padbind_input_set()==selected);
    uint32_t (*page)[6]=selected==PADBIND_DRAG?drag:defaults;
    float expected[19];
    for(int a=0;a<19;a++) {
        uint32_t resolved[6];
        CHECK(padbind_effective_binding(padbind_input_set(),a,resolved));
        CHECK(!memcmp(resolved,page[a],24));
        // Evaluate the expected page with the retail evaluator before restoring
        // Default. The hook's translated update must produce those same values.
        memcpy(G+rec(a,2),page[a],24);
        memcpy(G+0x121000,fake_pad[0],0x224);
        float v=0;
        for(int b=0;b<4;b++) { float w=eval(a,b,0); if(w>v) v=w; }
        expected[a]=v;
        memcpy(G+rec(a,2),defaults[a],24);
    }
    Cpu c=cpu(d[0]); f_006349B0(&c); abi(&c,0);
    for(int a=0;a<19;a++) {
        CHECK(!memcmp(G+rec(a,2),defaults[a],24));
        CHECK(M32(rec(a,0))==1 && M32(rec(a,0)+4)==200u+(unsigned)a);
        if(evaluate) CHECK(value(0,a)==expected[a]);
    }
    CHECK(!input_installed && !swap_on && edited_set==PADBIND_DEFAULT);
}

// Fresh Drag defaults: Speedbreaker on Square for every layout; no other Drag action keeps Square.
static void drag_speedbreaker(void) {
    const int layouts[]={L_NONPS_PS2,L_NONPS_XBOX};   // native PS is checked where its row is first seeded
    for(int li=0;li<2;li++) {
        active_layout=layouts[li];
        for(int a=0;a<19;a++) { set_rec(rec(a,2),0,0,0); set_rec(rec(a,3),0,0,0); }
        // Default: Speedbreaker on its own button, Square held by Handbrake-like action 9 (slot 0) and 10 (slot 1).
        set_rec(rec(A_GAMEBREAKER,2),T_BUTTON,0,li==0?P_CIRCLE:P_SQUARE);
        set_rec(rec(9,2),T_BUTTON,0,P_SQUARE);
        set_rec(rec(10,3),T_BUTTON,0,P_SQUARE);
        edited_set=PADBIND_DEFAULT; seed_active_pages();
        uint32_t dflt[19][6]; for(int a=0;a<19;a++) memcpy(dflt[a],G+rec(a,2),24);
        CHECK(padbind_edit_set(PADBIND_DRAG));
        CHECK(M32(rec(A_GAMEBREAKER,2))==T_BUTTON && M32(rec(A_GAMEBREAKER,2)+8)==(unsigned)P_SQUARE);
        for(int a=0;a<19;a++) for(int k=0;k<2;k++)
            if(a!=A_GAMEBREAKER) CHECK(!(M32(rec(a,2+k))==T_BUTTON && M32(rec(a,2+k)+8)==(unsigned)P_SQUARE));
        CHECK(padbind_edit_set(PADBIND_DEFAULT));
        for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),dflt[a],24));
    }
}

static void capture_drive(void) {
    drag_speedbreaker();
    const int buttons[]={P_R1,P_L1,P_CROSS}, actions[]={A_SHIFTUP,A_SHIFTDOWN,6};
    for(script_layout=0;script_layout<2;script_layout++) {
        active_layout=script_layout?L_NONPS_XBOX:L_NONPS_PS2;
        // Seed from the layout's Default records; Drag puts Speedbreaker on Square whatever Default holds.
        for(int a=0;a<19;a++) { set_rec(rec(a,2),0,0,0); set_rec(rec(a,3),0,0,0); }
        set_rec(rec(A_GAMEBREAKER,2),T_BUTTON,0,3);
        edited_set=PADBIND_DEFAULT; seed_active_pages();
        CHECK(padbind_edit_set(PADBIND_DRAG));
        CHECK(M32(rec(A_GAMEBREAKER,2)+8)==(unsigned)P_SQUARE);
        uint32_t saved[19][6]; for(int a=0;a<19;a++) memcpy(saved[a],G+rec(a,2),24);
        // Enter capture with Accept held; it cannot be rebound before release.
        for(int i=0;i<3;i++) {
            int a=actions[i], b=buttons[i];
            M32(0x920de8)=d[0]; M8(d[0]+0x183c)=1; M32(d[0]+0x1840)=3;
            M32(d[0]+0x1844)=3; M32(d[0]+0x1848)=a;
            CHECK(padbind_input_set()==PADBIND_DEFAULT && padbind_edited_set()==PADBIND_DRAG);
            PadState p={.lt=.25f,.rt=.75f}; pad_apply_layout(0,&p,1u<<P_CROSS);
            CHECK(p.buttons==0);
            p=(PadState){.lt=.25f,.rt=.75f}; pad_apply_layout(0,&p,0); CHECK(p.buttons==0);
            p=(PadState){.lt=.25f,.rt=.75f}; pad_apply_layout(0,&p,1u<<b);
            CHECK(p.buttons==(1u<<b)); CHECK(p.lt==.25f && p.rt==.75f);
            set_rec(rec(a,2),0,0,0); set_rec(rec(a,3),0,0,0);
            // A scalar event from the layout's DI button mask enters the translated capture/binder.
            // Interface: controller (3), device index 0; scalar: button (1), object index.
            uint32_t event=0x290000, iface=0x290100;
            M32(event+4)=iface; M32(iface+8)=3; M32(iface+0xc)=0;
            M32(event+0xc)=1;
            for(int o=0;o<32;o++) if(p.buttons&(1u<<o)) {
                M32(event+0x10)=o; Cpu q=cpu(d[0]); M32(q.esp+4)=event;
                f_00634490(&q); abi(&q,1); CHECK((q.eax&255)==1);
            }
            CHECK(M32(rec(a,2))==T_BUTTON && M32(rec(a,2)+8)==(unsigned)b);
            M8(d[0]+0x183c)=0;
            p=(PadState){0}; pad_apply_layout(0,&p,1u<<b); CHECK(p.buttons==0); // held on exit
            p=(PadState){0}; pad_apply_layout(0,&p,0);
            CHECK(padbind_edit_set(PADBIND_DEFAULT));
            memset(fake_pad,0,sizeof fake_pad);
            p=(PadState){0}; pad_apply_layout(0,&p,1u<<b);
            for(int o=0;o<32;o++) fake_put(0,0xc+4*o,(p.buttons&(1u<<o))?128:0);
            Cpu q=cpu(d[0]); f_006349B0(&q); abi(&q,0);
            CHECK(value(0,a)==1); // matching physical button actually drives the rebound action
            CHECK(padbind_edit_set(PADBIND_DRAG));
            for(int j=0;j<19;j++) memcpy(G+rec(j,2),saved[j],24);
        }
        // The seeded, otherwise unchanged Speedbreaker is evaluated on its original physical face button.
        CHECK(padbind_edit_set(PADBIND_DEFAULT));
        memset(fake_pad,0,sizeof fake_pad); PadState p={0};
        pad_apply_layout(0,&p,0); pad_apply_layout(0,&p,1u<<P_SQUARE);
        for(int o=0;o<32;o++) fake_put(0,0xc+4*o,(p.buttons&(1u<<o))?128:0);
        Cpu q=cpu(d[0]); f_006349B0(&q); CHECK(value(0,A_GAMEBREAKER)==1);
        // Default capture retains the layout output, even with a live Drag race and a preset selected.
        for(int type=1;type<=10;type++) {
            s_type.value=type-1; M8(d[0]+0x183c)=1;
            p=(PadState){0}; pad_apply_layout(0,&p,0);
            p=(PadState){0}; pad_apply_layout(0,&p,0);
            p=(PadState){0}; pad_apply_layout(0,&p,1u<<P_R1);
            CHECK(p.buttons==(1u<<(script_layout?10:1)));
            M8(d[0]+0x183c)=0; pad_apply_layout(0,&p,0);
        }
        s_type.value=0;
    }
}

int main(int argc,char **argv) {
    if(argc!=2) return 2;
    scratch=argv[1]; G=calloc(1,0x1000000);
    MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
    MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
    Cpu c=cpu(d[0]); M32(c.esp+4)=0; f_00634B50(&c); abi(&c,1);
    M32(IOMOD_DEVS)=d[0]; M32(0x982bec)=1;
    strcpy(joy_name(joy()),"Controller (DualSense)");
    // The retail defaults constructor polls DI before its final action page is
    // ready. It must not remember that empty page or seed Drag from it.
    defaults_probe=1; c=cpu(d[0]); f_0063CD80(&c); abi(&c,0);
    CHECK(nsets==0);
    free(sets); sets=NULL; nsets=sets_loaded=sets_dirty=0;
    memset(G+GA,0,76*52); active_layout=L_NATIVE_PS;
    M32(FEDB)=0x200000; M32(0x200010)=0x201000; strcpy((char *)G+0x201000,"Player");
    for(int a=0;a<19;a++) {
        set_rec(rec(a,0),1,200+a,0); set_rec(rec(a,2),T_BUTTON,0,a);
        set_rec(rec(a,3),T_BUTTON,0,31-a);
        memcpy(defaults[a],G+rec(a,2),24);
    }
    seed_active_pages();
    Set *row=set_find_kind("Player",name_hash(joy_name(joy())),L_NATIVE_PS,PADBIND_DRAG);
    CHECK(row!=NULL); if(!row) return 2;
    CHECK(row->r[0][0][0]==T_ROT_HI && row->r[0][0][2]==2);
    CHECK(row->r[1][0][0]==T_AXIS_HI && row->r[1][0][2]==2);
    CHECK(row->r[A_GAMEBREAKER][0][0]==T_BUTTON && row->r[A_GAMEBREAKER][0][2]==P_SQUARE);   // native PS: Square too
    for(int a=0;a<19;a++) for(int k=0;k<2;k++)
        if(a!=A_GAMEBREAKER) CHECK(!(row->r[a][k][0]==T_BUTTON && row->r[a][k][2]==P_SQUARE));
    for(int a=0;a<19;a++) {
        row->r[a][0][0]=row->r[a][1][0]=T_BUTTON;
        row->r[a][0][1]=row->r[a][1][1]=0;
        row->r[a][0][2]=19-a; row->r[a][1][2]=(a+7)%20;
    }
    row->r[0][0][0]=T_ROT_HI; row->r[0][0][2]=2;
    row->r[1][0][0]=T_AXIS_HI; row->r[1][0][2]=2;
    memcpy(drag,row->r,sizeof drag);
    physical();
    M32(0x0091E000)=0x210000; M32(0x211968)=0x220000;
    M32(0x220004)=0x230000; M8(0x23002b)=2;
    M32(0x00925E90)=6; M32(0x009885E0)=0x240000; M32(0x24002c)=1;
    // Drag before countdown, then racing; all Control Types must preserve both
    // Drag slots and analog pressures without preset substitution or ramps.
    for(int type=1;type<=10;type++) {
        s_type.value=type-1; frame(PADBIND_DRAG,1); CHECK(controltype_passthrough(0));
        CHECK(padbind_trigger_role(0)==PADBIND_ROLE_BRAKE);
        CHECK(padbind_trigger_role(1)==PADBIND_ROLE_GAS);
        CHECK(padbind_ps_label(12,0)==0x6AB80AFAu);
        CHECK(padbind_ps_label(13,0)==0x619B23B0u);
    }
    s_type.value=0; M32(0x24002c)=3; frame(PADBIND_DRAG,1);
    // Non-PS layouts normally remap face/shoulder buttons. Drag uses physical
    // objects like its seeded R1/L1 pairs, independently of Control Type.
    for(script_layout=0;script_layout<2;script_layout++) for(int type=1;type<=10;type++) {
        s_type.value=type-1;
        PadState p={.rt=.75f,.lt=.5f}; pad_apply_layout(0,&p,1u<<P_R1);
        CHECK(p.buttons==(1u<<P_R1)); CHECK(p.rt==.75f && p.lt==.5f);
        p=(PadState){0}; pad_apply_layout(0,&p,1u<<P_L1); CHECK(p.buttons==(1u<<P_L1));
    }
    s_type.value=0;
    M8(0x23002b)=0; frame(PADBIND_DEFAULT,1); // circuit
    M8(0x23002b)=2; M32(0x24002c)=4; frame(PADBIND_DEFAULT,1); // pause
    M32(0x24002c)=3; frame(PADBIND_DRAG,1); // resume
    menu_open=1; frame(PADBIND_DEFAULT,1); menu_open=0; frame(PADBIND_DRAG,1);
    M32(0x0091cadc)=0x250000; M32(0x250000)=0x251000; M32(0x2510e4)=0x252000;
    M32(0x252020)=1; M32(0x25200c)=0x253000;
    strcpy((char *)G+0x253000,"RaceResults.fng"); frame(PADBIND_DEFAULT,1);
    strcpy((char *)G+0x253000,"HUD_Drag.fng"); frame(PADBIND_DRAG,1);
    strcpy((char *)G+0x253000,"Pause_PC_Controller.fng"); frame(PADBIND_DEFAULT,1);
    CHECK(controls_screen_open()); M32(0x252020)=0; frame(PADBIND_DRAG,1);
    M8(d[0]+0x183c)=1; frame(PADBIND_DEFAULT,0); CHECK(capture_calls==1);
    M8(d[0]+0x183c)=0; frame(PADBIND_DRAG,1);
    M32(0x211968)=0; frame(PADBIND_DEFAULT,1); // restart teardown / roam
    M32(0x211968)=0x260000; M32(0x260004)=0x270000; M8(0x27002b)=2;
    frame(PADBIND_DRAG,1); // replacement parameters, no cached old race
    M8(0x27002b)=0; frame(PADBIND_DEFAULT,1); // restart into circuit
    M8(0x27002b)=2; frame(PADBIND_DRAG,1);
    M32(0x00925E90)=3; frame(PADBIND_DEFAULT,1); // quit, stale drag params
    M32(0x00925E90)=6; M32(0x211968)=0; frame(PADBIND_DEFAULT,1); // roaming
    M32(0x211968)=0x260000; M32(0x260004)=0; frame(PADBIND_DEFAULT,1); // unindexed
    // Actual retail fallback: an unindexed SkipFE attribute, then an in-place
    // race type change. Refresh every evaluation, even when parameters stay put.
    M32(0x260008)=0x280000; M32(0x280000)=0x281000;
    strcpy((char *)G+0x281000,"drag"); strcpy((char *)G+0x282000,"circuit");
    for(int i=0;i<11;i++) { M32(0x8f5cd8+8*i)=0x282000; M32(0x8f5cdc+8*i)=0; }
    M32(0x8f5cd8+16)=0x281000; M32(0x8f5cdc+16)=2;
    frame(PADBIND_DRAG,1);
    M32(0x280000)=0x282000; frame(PADBIND_DEFAULT,1);
    M32(0x280000)=0x281000; frame(PADBIND_DRAG,1);
    M32(0x0091E000)=0; frame(PADBIND_DEFAULT,1);
    M32(0x0091E000)=0x210000; M32(0x211968)=0x220000;
    M32(0x220004)=0x230000; M8(0x23002b)=2; M32(0x24002c)=3;
    capture_drive();
    free(sets); free(G);
    printf("paddrag: %d checks / %d failures\n",checks,failures);
    return failures!=0;
}
'''


def main():
    if not (R.parent/'recomp/out/speed_31.c').exists():
        print('SKIP: needs translated input functions')
        return 3
    pre = h.PRE
    for name in ('padbind_port_begin', 'padbind_port_end', 'padbind_input_set',
                 'padbind_input_begin', 'padbind_input_end', 'padbind_follow',
                 'padbind_swap', 'f_00634900', 'padbind_race_update'):
        pre, count = re.subn(r'^(?:int|void) '+name+r'\([^\n]*\n', '', pre, flags=re.M)
        assert count == 1, name
    pre = pre.replace('++failures; printf(', '++failures; if(failures<12) printf(')
    pre = pre.replace('++key_reads; c->eax=1;', '++key_reads; c->eax=0;')
    pre = re.sub(r'\bput\(', 'fake_put(', pre)
    bodies = '\n'.join(h.translated(file, name) for file, names in (
        ('speed_31.c', ('00634B50', '006349B0', '00634490')),
        ('speed_30.c', ('00628940', '006284A0', '006285F0')),
        ('speed_12.c', ('004D6150',)),
        ('speed_27.c', ('005FAA20',)),
    ) for name in names)
    pad = (R/'padbind.c').read_text()
    hook = (R/'controltype.c').read_text()
    def old(file):
        return PINNED_JOB2_PAD if file == 'padbind.c' else PINNED_JOB2_HOOK
    variants = {'normal': (pad, hook), 'job2': (old('padbind.c'), old('controltype.c'))}
    variants['original-seed'] = (re.sub(r'static void seed_drag\(.*?\n\}', lambda m: ORIGINAL_SEED, pad, count=1, flags=re.S), hook)
    variants['original-capture'] = (pad, hook)
    variants['original-64bf1349'] = (variants['original-seed'][0], re.sub(r'int controltype_passthrough\(.*?\n\}', lambda m: ORIGINAL_POLICY, hook, count=1, flags=re.S))
    variants['capture-live-page'] = (pad, hook)
    for name, before, after in (
        ('no-restore', 'if (!swapped) return;\n    for (int a = 0;', 'if (1) return;\n    for (int a = 0;'),
        ('no-pause', '(sim && M32(sim + 0x2C) == 4)', '(sim && 0)'),
        ('seed-before-defaults', 'if (!active_layout || defaults_installing) return;', 'if (0) return;'),
        ('indexed-only', 'params && params == input_race_params && input_race_drag', '0'),
    ):
        assert before in pad, name
        variants[name] = (pad.replace(before, after, 1), hook)
    variants['preset-over-drag'] = (pad, hook.replace('input_set == PADBIND_DRAG || ', ''))
    assert variants['preset-over-drag'][1] != hook
    variants['remap-drag-buttons'] = (pad, hook.replace('(idx == 0 && padbind_input_set() == PADBIND_DRAG) || ', ''))
    assert variants['remap-drag-buttons'][1] != hook
    user32 = (R/'user32.m').read_text()
    mapping = user32[user32.index('static uint32_t bit('):user32.index('// The pad\'s identity:')]
    original_mapping = re.sub(r'static void pad_apply_layout\(.*?\n\}', lambda m: ORIGINAL_LAYOUT.replace('controltype_passthrough()', 'controltype_passthrough(idx)'), mapping, count=1, flags=re.S)
    mapping_pre = '''
#define GAMEFLOW_STATE 0x00925E90u
#define SIM_INSTANCE 0x009885E0u
static int script_layout;
static int pad_layout_xbox(void) { return script_layout; }
static void di_event(uint8_t key,int down) { (void)key;(void)down; }
'''
    with tempfile.TemporaryDirectory(prefix='paddrag-') as tmp:
        tmp = Path(tmp)
        for name, (pb, ct) in variants.items():
            data = tmp/(name+'-data'); data.mkdir()
            src = tmp/(name+'.c'); exe = tmp/name
            compat = 'void padbind_race_update(Cpu *c) { (void)c; }\nint padbind_edited_set(void) { return edited_set; }\n' if name == 'job2' else ''
            ct = ct.replace('controltype_passthrough(void)', 'controltype_passthrough(int idx)')
            src.write_text(pre+EXTRA+bodies+'\n'+pb+compat+ct+mapping_pre+(original_mapping if name in ('original-capture', 'original-64bf1349') else mapping.replace('padbind_edited_set()', 'padbind_input_set()') if name == 'capture-live-page' else mapping)+re.sub(r'\bput\(', 'fake_put(', MAIN))
            h.run(['clang', '-std=c11', '-g', '-w', '-DRECOMP_ABICHECK', '-fsanitize=address,undefined',
                   '-fno-sanitize-recover=all', '-I', str(R.parent/'recomp'),
                   '-I', str(R.parent/'recomp/out'), '-I', str(R), str(src), '-o', str(exe)])
            result = subprocess.run([exe, data], capture_output=True, text=True)
            print(name+': '+result.stdout.strip())
            assert result.returncode == (0 if name == 'normal' else 1) and ('failures' in result.stdout), result.stdout[-2000:]+result.stderr[-3000:]
    print('paddrag_test: PASS')


if __name__ == '__main__':
    raise SystemExit(main())
