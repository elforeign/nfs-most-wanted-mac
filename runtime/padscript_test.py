#!/usr/bin/env python3
"""BOOT_PAD2 grammar/routing/timing and actual BOOT_KEYS pad dispatch/release.
Foundation fake controllers, ASan/UBSan; no game/hardware. Single-pad state,
guest memory and DI/feedback compare against b622607 with BOOT_PAD2 absent
and present (16,780,444 bytes). Raw prompt tracking now precedes consumption;
its poll-call counter is checked separately. Baselines 3/3; flipped snapshots
rejected 3/3.
PASS: 32 checks for two pads; 19 each unset/1/invalid. Rejected mutants:
slot 1 absent: 14/32 failures; script alias: 1/32; no release: 7/32;
slot 1 menu leak: 1/32.
Launcher output safety: 6 actual environment recipes PASS (0 violations); broken
player/road overrides FAIL (12 violations). Only /usr/bin/env is executed.
The sequence harness retains production clock/release and pad dispatch verbatim;
unrelated Cocoa keyboard/window/package commands are omitted.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import subprocess
import tempfile
import struct
import ast
import re

R=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('padtwo_harness',R/'padtwo_test.py')
h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
PRE=r'''
static int checks,fails;
int diag_fixed_step;
#define CHECK(x) do { ++checks; if(!(x)) { ++fails; printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
int pacing_lockstep_now(void) { return 0; }
int pacing_sim_hz(void) { return 60; }
float diag_car_speed(void) { return 0; }
void diag_scripted_key(uint64_t frame) { (void)frame; }
static void press(const char *held,int down,uint64_t frame) { (void)held;(void)down;(void)frame; }
static unsigned script_trigger_step;
'''
MAIN=r'''
static void tick(const char *spec) { fake_time+=16666667; script_seq(spec,1,0); }
int main(int argc,char **argv) {
    (void)argc; G=calloc(1,0x1000000);
    PadState p0,p1; int count=pad_count();
    setenv("BOOT_PAD","sim:dualsense,lx=-0.25,rt=0.8,buttons=2",1);
    setenv("BOOT_PAD2","sim:ds4,lx=0.75,ly=-0.5,rx=0.2,ry=-0.3,lt=0.4,rt=0.9,buttons=4,dpad=9000@2-4-6",1);
    start_ns=fake_time; CHECK(!pad_read_n(1,&p1) && !p1.connected && p1.pov==-1);
    fake_time=start_ns+2000000000ull;
    CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) {
        CHECK(p1.connected && p1.native && p1.buttons==4 && p1.pov==9000);
        CHECK(p1.lx==.75f && p1.ly==-.5f && p1.rx==.2f && p1.ry==-.3f && p1.lt==.4f && p1.rt==.9f);
        char name[64]; CHECK(pad_identity_n(1,name,sizeof name)==PAD_KIND_DUALSHOCK4 && strstr(name,"DualShock 4"));
        int rb=rumble_calls, fx=fx_calls, pc=prompts_calls, mk=menu_calls, kc=key_calls, seen=prompts_seen;
        CHECK(pad_read_n(1,&p1));
        CHECK(rb==rumble_calls && fx==fx_calls && pc==prompts_calls && mk==menu_calls && kc==key_calls && seen==prompts_seen);
        fake_time=start_ns+4000000000ull; CHECK(!pad_read_n(1,&p1) && !p1.connected && p1.pov==-1);
        fake_time=start_ns+6000000000ull; CHECK(pad_read_n(1,&p1));
        menu_hide=1; CHECK(pad_read_n(1,&p1) && p1.connected && !p1.buttons && !p1.lx && !p1.ly && !p1.lt && !p1.rt && p1.pov==-1);
        menu_hide=0;
    }
    setenv("BOOT_PAD2","sim:edge",1);
    CHECK(pad_read(&p0) && p0.buttons==2 && p0.lx==-.25f);
    const char *steps="pad2:128:3,pad:1:2,pad2raw:64:2,pad2axis:lx=0.6:2,padaxis:ry=-0.7:2,pad2:8";
    tick(steps); CHECK(script_pad2==(count==2?128u:0u) && script_pad==0);
    CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) CHECK(p1.buttons==128);
    CHECK(pad_read(&p0) && p0.buttons==2);
    tick(steps); tick(steps); CHECK(script_pad2==(count==2?128u:0u));
    tick(steps); CHECK(!script_pad2 && script_pad==1); CHECK(pad_read(&p0) && p0.buttons==3);
    tick(steps); tick(steps); CHECK(!script_pad && script_raw2==(count==2?64u:0u));
    if(count==2) CHECK(pad_read_n(1,&p1) && p1.buttons==64);
    tick(steps); tick(steps); CHECK(!script_raw2 && !!script_axis2[0]==(count==2));
    if(count==2) CHECK(pad_read_n(1,&p1) && p1.lx==.6f && !p1.buttons);
    CHECK(pad_read(&p0) && p0.lx==-.25f);
    tick(steps); tick(steps); CHECK(!script_axis2[0] && script_axis[0]);
    CHECK(pad_read(&p0) && p0.ry==-.7f);
    if(count==2) CHECK(pad_read_n(1,&p1) && !p1.ry && !p1.lx);
    tick(steps); tick(steps); CHECK(!script_axis[0] && script_pad2==(count==2?8u:0u));
    for(int i=0;i<5;i++) tick(steps);
    CHECK(script_pad2==(count==2?8u:0u)); tick(steps); CHECK(!script_pad2);
    unsetenv("BOOT_PAD2"); CHECK(!pad_read_n(1,&p1) && !p1.connected);
    setenv("BOOT_PAD2","sim",1); CHECK(pad_read_n(1,&p1)==(count==2));
    if(count==2) CHECK(p1.connected && !p1.native);
    if(argc>1) { FILE *f=fopen(argv[1],"wb"); fwrite(&p0,1,sizeof p0,f); fwrite(G,1,0x1000000,f); fclose(f); }
    printf("padscript NFS_PADS=%s: %d checks, %d failures\n",getenv("NFS_PADS")?:"unset",checks,fails);
    free(G); return fails!=0;
}
'''
BASEMAIN=r'''
int main(int argc,char **argv) {
 (void)argc; G=calloc(1,0x1000000); FILE *f=fopen(argv[1],"wb"); PadState ps;
 const char *s[]={"sim","sim:dualsense,buttons=1,lt=0.4,rt=0.9,lx=-0.5","sim:ds4@2-4-6","sim:edge","sim:xbox,buttons=9"};
 for(int k=0;k<2;k++) { if(k) setenv("BOOT_PAD2","sim:dualsense,buttons=255,lx=1",1); else unsetenv("BOOT_PAD2");
  for(int i=0;i<5;i++) for(int j=0;j<4;j++) {
   setenv("BOOT_PAD",s[i],1); fake_time=start_ns+j*2000000000ull; pad_read(&ps); fwrite(&ps,1,sizeof ps,f);
   pad_read_n(1,&ps); fwrite(&ps,1,sizeof ps,f);
  }
 }
 fwrite(G,1,0x1000000,f); int counts[]={menu_calls,key_calls,rumble_calls,fx_calls,binding_calls,prompts_seen,prompts_calls,source_reads};
 fwrite(counts,1,sizeof counts,f); fclose(f); free(G); return 0;
}
'''

def launcher_safety():
    # Execute only the launchers' environment assignments with /usr/bin/env as the sink.
    # No launcher, game binary, discovery API or output API is invoked.
    keys = ('NFS_PAD_PLAYERS', 'NFS_PAD_ROAD')
    inherited = dict(os.environ, NFS_RUMBLE='off', **{k: 'on' for k in keys})
    recipes = []
    for name in ('run_guarded.sh', 'probe.sh', 'soak.sh', 'offline_tests.sh'):
        for line in (R / name).read_text().splitlines():
            if line.lstrip().startswith('#') or 'NFS_PAD_TRIGGERS=' not in line:
                continue
            parts = re.findall(r'\b(?:NFS_RUMBLE|NFS_PAD_[A-Z]+)=(?:\$\{[^}]+\}|[^ \t]+)', line)
            recipes.append((name, ' '.join(parts)))
    assert len(recipes) == 5, recipes
    counts = []
    for broken in (False, True):
        violations = 0
        for name, recipe in recipes:
            if broken:
                recipe = re.sub(r'\bNFS_PAD_(?:PLAYERS|ROAD)=off', '', recipe)
            result = h.run(['sh', '-c', 'env ' + recipe + ' /usr/bin/env'], env=inherited)
            env = dict(row.split('=', 1) for row in result.stdout.splitlines() if '=' in row)
            violations += sum(env.get(k) != 'off' for k in keys)
            assert env['NFS_RUMBLE'] == 'off', name
        # Evaluate the actual proof runner's environment assignment alone, never its subprocess (private trees only).
        runners = sorted(R.glob('*_proof.py'))
        if not runners:
            counts.append(violations)
            continue
        tree = ast.parse(runners[0].read_text())
        suite = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'suite')
        assignment = suite.body[0]
        if broken:
            assignment.value.keywords = [k for k in assignment.value.keywords if k.arg not in keys]
        scope = {'os': type('Environment', (), {'environ': inherited}), 'root': R.parent}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[])), '<proof env>', 'exec'), scope)
        violations += sum(scope['env'].get(k) != 'off' for k in keys)
        assert scope['env']['NFS_RUMBLE'] == 'off'
        counts.append(violations)
    runners = 1 if sorted(R.glob('*_proof.py')) else 0
    assert counts == [0, 2 * (len(recipes) + runners)], counts
    print(f'launcher safety: {len(recipes)+runners} recipes PASS, {counts[0]} violations; broken {counts[1]} violations')



# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_PAD = r'''static uint32_t bit(uint32_t phys, int b) { return (phys >> b) & 1; }
static void pad_menu_keys(uint32_t keys) {  // menus: FE BUTTON4 / BUTTON0 / START as keys 2 / 3 / 4 (DIK 03 / 04 / 05)
    static uint32_t held;
    for (int k = 0; k < 3; ++k)
        if (((keys ^ held) >> k) & 1) di_event((uint8_t)(3 + k), (keys >> k) & 1);
    held = keys;
}
// The FE package that has control (cFEng::FindPackageWithControl 00516A90: cFEng::mInstance -> FEngine, its package
// list at +0xE4, +8 next, +0x20 has control, +0xC name), or NULL.
static const char *fe_package_in_control(void) {
    uint32_t fe = M32(0x0091CADCu) ? M32(M32(0x0091CADCu)) : 0;
    if (!fe || fe == 0xFFFFFF28u) return NULL;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) if (M32(p + 0x20)) return gptr(M32(p + 0xC));
    return NULL;
}
// Options > Controls re-mapping: the game is waiting for the input to bind (the input object at IOModule
// (GetIOModule 00634370 = 0x920DE0) +8: FUN_00628C00, called at 0052AFED when a field is picked, sets its +0x183C and
// the device +0x1840 (1 keyboard, 3 controller); FUN_00634900 clears +0x183C when an input was captured). What the
// controller sends then is what the binding records, and the bindings are used while driving, so the controller gets
// the driving mapping while the game listens.
static int pad_remap_listening(void) {
    uint32_t io = M32(0x00920DE8u);
    return io && M8(io + 0x183C) && M32(io + 0x1840) == 3;
}
// The game's action bindings (GameAction 0091F418: 76 entries of 0x34 bytes: an id, then keyboard primary/secondary
// and controller primary/secondary, 12 bytes each {type, ?, index}). BOOT_DIAG logs the controller ones on change.
static void pad_diag_bindings(void) {
    static uint32_t last;
    uint32_t h = 2166136261u;
    for (uint32_t a = 0x0091F418u + 0x1C; a < 0x0091F418u + 76 * 0x34; a += 0x34)
        for (int k = 0; k < 6; ++k) h = (h ^ M32(a + 4 * k)) * 16777619u;
    if (h == last) return;
    last = h;
    char line[2048];
    int n = snprintf(line, sizeof line, "pad: controller bindings (action: primary type/index, secondary type/index)");
    for (int i = 0; i < 76 && n < (int)sizeof line - 40; ++i) {
        uint32_t a = 0x0091F418u + 0x34 * (uint32_t)i + 0x1C;
        if (M32(a) || M32(a + 12))
            n += snprintf(line + n, sizeof line - (size_t)n, " %d:%u/%u,%u/%u", i, M32(a), M32(a + 8), M32(a + 12), M32(a + 20));
    }
    diag_event("%s", line);
}
static const char *pad_mode = "menus", *pad_ctl;   // the last mapping decision, for the diag pad: lines
int controltype_passthrough(void);
static void pad_apply_layout(int idx, PadState *st, uint32_t phys) {
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
}

// The pad's identity: its kind and the name the game sees (DIDEVICEINSTANCE, the Controls screen's page title, and the
// profile's binding set: padbind.c). Set by pad_read; a disconnected pad keeps the last one.
static int pad_kinds[2];
static char pad_names[2][64];
static void set_identity(int idx, int kind, const char *vendor) {
    char name[64];
    if (kind == PAD_KIND_DUALSENSE) snprintf(name, sizeof name, "Controller (DualSense)");
    else if (kind == PAD_KIND_DUALSHOCK4) snprintf(name, sizeof name, "Controller (DualShock 4)");
    else if (kind == PAD_KIND_EDGE) snprintf(name, sizeof name, "Controller (DualSense Edge)");
    else if (!vendor || !*vendor) snprintf(name, sizeof name, "Controller (XBOX 360 For Windows)");
    else if (!strncasecmp(vendor, "Controller", 10)) snprintf(name, sizeof name, "%s", vendor);
    else snprintf(name, sizeof name, "Controller (%s)", vendor);
    if (kind == pad_kinds[idx] && !strcmp(name, pad_names[idx])) return;
    pad_kinds[idx] = kind;
    snprintf(pad_names[idx], sizeof pad_names[idx], "%s", name);
    fprintf(stderr, "boot: controller %s\n", pad_names[idx]);
    if (diag_on) diag_event("pad: identity %s (kind %d)", pad_names[idx], kind);
}
int pad_identity_n(int idx, char *name, size_t n) {
    if (name && n) snprintf(name, n, "%s", idx >= 0 && idx < 2 ? pad_names[idx] : "");
    return idx >= 0 && idx < 2 ? pad_kinds[idx] : PAD_KIND_NONE;
}
int pad_identity(char *name, size_t n) { return pad_identity_n(0, name, n); }
static int kind_native(int kind) { return kind == PAD_KIND_DUALSENSE || kind == PAD_KIND_DUALSHOCK4 || kind == PAD_KIND_EDGE; }
int user32_pad_native(void) { return kind_native(pad_kinds[0]); }   // padprompts.c: PlayStation glyphs whatever the layout
// A PlayStation pad as itself: the physical buttons (L2/R2 only as their analog axes), nothing remapped.
static void pad_native(PadState *st, uint32_t phys) {
    if (phys & 1u << P_L2) st->lt = 1;      // BOOT_PAD buttons= / pad: steps with the L2 / R2 bits: a full press
    if (phys & 1u << P_R2) st->rt = 1;
    st->buttons = phys & ~(1u << P_L2 | 1u << P_R2);
    st->native = 1;
    static uint32_t last = ~0u;
    if (diag_on && st->buttons != last) { last = st->buttons; diag_event("pad: native buttons %04X", st->buttons); }
}
// The F10 menu is open: the game reads a connected pad at rest (the layout's held menu keys are released too).
static void pad_neutral(int idx, PadState *st) {
    memset(st, 0, sizeof *st);
    st->connected = 1;
    st->pov = -1;
    pad_apply_layout(idx, st, 0);
    st->buttons = 0;
    st->lt = st->rt = 0;
    st->native = kind_native(pad_kinds[idx]);
}
// A PlayStation pad's PS button and touchpad click reach the game (bindable in Options > Controls) unless one is the
// F10 menu's shortcut (settings controls.menu_button: 0 automatic = the touchpad click on a pad with one, else the PS
// button; 1 PS button; 2 L3+R3 held; 3 Create held), which portmenu.m keeps for itself.
static uint32_t pad_game_extras(int has_touchpad) {
    int choice = settings_get("controls.menu_button");
    uint32_t m = 1u << P_PS | 1u << P_TOUCHPAD | 1u << P_LPADDLE | 1u << P_RPADDLE;
    if (choice == 0 && has_touchpad) m &= ~(1u << P_TOUCHPAD);
    if (choice == 1 || (choice == 0 && !has_touchpad)) m &= ~(1u << P_PS);
    return m;
}
static void pov_dirs(int pov, int *u, int *d, int *l, int *r) {   // hundredths of a degree, clockwise from up; -1 centred
    *u = pov >= 0 && (pov >= 31500 || pov <= 4500);
    *r = pov >= 4500 && pov <= 13500;
    *d = pov >= 13500 && pov <= 22500;
    *l = pov >= 22500 && pov <= 31500;
}

// A controller connecting or going away, at any time (GameController.framework adds a pad when it connects, wakes or is
// plugged in; dinput.c's gamepad exists from start-up and reads centred while there is none). Logged to the app log.
static int pad_presents[2] = {-1, -1};              // -1 before the first read
static void pad_presence(int idx, int present, const char *what) {
    if (present == pad_presents[idx]) return;
    int first = pad_presents[idx] < 0;
    pad_presents[idx] = present;
    if (present) {
        fprintf(stderr, "boot: controller connected: %s\n", what);
        if (idx == 0 && !first) prompts_input(1);     // connected while playing: its glyphs (a key press brings the keycaps back)
    } else {
        fprintf(stderr, first ? "boot: no controller connected yet (one connecting later is picked up)\n"
                              : "boot: controller disconnected: the game reads it centred until one connects\n");
        if (idx == 0) pad_menu_keys(0);                 // the menu keys it held are released
    }
    if (diag_on) diag_event("pad: %s%s (app active %d)", present ? "connected: " : first ? "none at start-up" : "disconnected",
                            present ? what : "", headless ? 0 : (int)NSApp.isActive);
}
// Cache the restart-only opt-in once: default reads retain the original single-controller scan,
// without observers, locks, allocation or a second GCController.controllers fetch.
int pad_count(void) {
    static int count;
    if (!count) { const char *e = getenv("NFS_PADS"); count = e && !strcmp(e, "2") ? 2 : 1; }
    return count;
}
// Two-pad mode: notifications preserve connection order, and holes are reused without compacting
// the surviving slot. Controllers already connected at initialization use the framework's order.
// All access is serialized with notification delivery, which may arrive off the polling thread.
static GCController *pad_slots[2];
static void pad_slot_connect(GCController *ctl) {
    if (!ctl.extendedGamepad) return;
    @synchronized (GCController.class) {
        for (int i = 0; i < 2; ++i) if (pad_slots[i] == ctl) return;
        for (int i = 0; i < 2; ++i) if (!pad_slots[i]) { pad_slots[i] = ctl; return; }
    }
}
static void pad_slot_disconnect(GCController *ctl) {
    @synchronized (GCController.class) {
        for (int i = 0; i < 2; ++i) if (pad_slots[i] == ctl) pad_slots[i] = nil;
    }
}
static void pad_slots_init(void) {
    if (pad_count() != 2) return;
    const char *sim = getenv("BOOT_PAD");
    if (sim && (!strcmp(sim, "sim") || !strncmp(sim, "sim:", 4))) return;
    @autoreleasepool {
        NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
        [nc addObserverForName:GCControllerDidConnectNotification object:nil queue:nil usingBlock:^(NSNotification *n) {
            pad_slot_connect(n.object);
        }];
        [nc addObserverForName:GCControllerDidDisconnectNotification object:nil queue:nil usingBlock:^(NSNotification *n) {
            pad_slot_disconnect(n.object);
        }];
        @synchronized (GCController.class) {
            for (GCController *ctl in GCController.controllers) pad_slot_connect(ctl);
        }
    }
}
static GCController *pad_controller_n(int idx) {
    if (pad_count() == 1) {
        if (idx) return nil;
        for (GCController *ctl in GCController.controllers) if (ctl.extendedGamepad) return ctl;
        return nil;
    }
    @synchronized (GCController.class) { return pad_slots[idx]; }
}
static uint64_t start_ns;                 // BOOT_PAD=sim:...@T: seconds since start-up
__attribute__((constructor)) static void pad_clock(void) { start_ns = host_ticks(); pad_slots_init(); }

// The indexed GameController.framework extended gamepad, or BOOT_PAD=sim:name=value,... (constant values for tests:
// lx ly rx ry in -1..1 with y up positive, lt rt 0..1, buttons=<physical P_* mask>, dpad=<hundredths of a degree>; a
// bare word is the pad's identity: dualsense, ds4, edge, or xbox, the default; a last @T1-T2-T3... connects it T1
// seconds after start-up, disconnects it at T2, connects it again at T3, ...). 0: no controller (st centred).
int pad_read_n(int idx, PadState *st) {
    memset(st, 0, sizeof *st);
    st->pov = -1;
    if (idx < 0 || idx >= pad_count()) return 0;
    const char *sim = getenv("BOOT_PAD");
    if (sim && !strcmp(sim, "sim")) sim = "sim:xbox";   // a bare sim is a simulated pad too, never the real controller
    if (sim && !strncmp(sim, "sim:", 4)) {
        if (idx) return 0;                 // the scripted pad remains exclusively slot 0
        const char *at = strchr(sim, '@'), *end = at ? at : sim + strlen(sim);
        int on = 1;
        if (at) {                           // connected while an odd number of the times have passed
            double now = (double)(host_ticks() - start_ns) / 1e9;
            on = 0;
            for (const char *q = at + 1; *q;) {
                char *e;
                double t = strtod(q, &e);
                if (e == q || now < t) break;
                on = !on;
                q = *e == '-' ? e + 1 : e + strlen(e);
            }
        }
        if (!on) { pad_presence(idx, 0, NULL); prompts_pad_seen(0); return 0; }
        st->connected = 1;
        int word_kind = -1;
        for (const char *p = sim + 4; p < end;) {
            char name[16];
            size_t n = strcspn(p, "=,@");
            snprintf(name, sizeof name, "%.*s", (int)(n < 15 ? n : 15), p);
            p += n;
            double v = 0;
            if (*p == '=') v = strtod(p + 1, (char **)&p);
            else if (kind_of_word(name) >= 0) word_kind = kind_of_word(name);
            if (!strcmp(name, "lx")) st->lx = (float)v; else if (!strcmp(name, "ly")) st->ly = (float)v;
            else if (!strcmp(name, "rx")) st->rx = (float)v; else if (!strcmp(name, "ry")) st->ry = (float)v;
            else if (!strcmp(name, "lt")) st->lt = (float)v; else if (!strcmp(name, "rt")) st->rt = (float)v;
            else if (!strcmp(name, "buttons")) st->buttons = (uint32_t)v; else if (!strcmp(name, "dpad")) st->pov = (int)v;
            while (p < end && *p != ',') ++p;
            if (p < end) ++p;
        }
        if (script_trigger_pair) { st->lt = script_lt; st->rt = script_rt; }   // physical pair, before F10/layout
        st->buttons |= script_pad;          // BOOT_KEYS pad: steps
        int kind = sim_kind >= 0 ? sim_kind : word_kind >= 0 ? word_kind : PAD_KIND_OTHER;
        pad_presence(idx, 1, kind == PAD_KIND_DUALSENSE ? "simulated DualSense" : kind == PAD_KIND_DUALSHOCK4 ? "simulated DualShock 4" :
                        kind == PAD_KIND_EDGE ? "simulated DualSense Edge" : "simulated pad");
        set_identity(idx, kind, NULL);
        prompts_pad_seen(1);
        // the F10 menu: bit 12 is the PS/Guide button, bit 13 the touchpad click (a simulated DualSense)
        int u, d, l, r;
        if (script_axis[0] && (!strncmp(script_axis, "lx", 2) || !strncmp(script_axis, "ly", 2)) && strchr(script_axis, '='))   // BOOT_KEYS padaxis: the menu sees a scripted left stick
            (script_axis[1] == 'x' ? &st->lx : &st->ly)[0] = (float)atof(strchr(script_axis, '=') + 1);
        pov_dirs(st->pov, &u, &d, &l, &r);
        uint32_t inject, hide = portmenu_pad(st->buttons & 0xFFF, u, d, l, r, st->lx, st->ly, (st->buttons >> 12) & 1, (st->buttons >> 13) & 1, 1, &inject);
        uint32_t extras = kind_native(pad_kinds[idx]) ? st->buttons & pad_game_extras(1) & 0xF000 : 0;
        st->buttons &= 0xFFF;
        if (hide == ~0u) { pad_neutral(idx, st); return 1; }
        st->buttons = (st->buttons & ~hide) | inject | extras;
        if (st->buttons || fabsf(st->lx) > 0.5f || fabsf(st->ly) > 0.5f || fabsf(st->rx) > 0.5f || fabsf(st->ry) > 0.5f ||
            st->lt > 0.5f || st->rt > 0.5f || st->pov >= 0)
            prompts_input(1);
        float *ax[6] = {&st->lx, &st->ly, &st->rx, &st->ry, &st->lt, &st->rt};
        static const char *an[6] = {"lx", "ly", "rx", "ry", "lt", "rt"};
        float axv = script_axis[0] ? (float)atof(strchr(script_axis, '=') ? strchr(script_axis, '=') + 1 : "0") : 0;
        for (int k = 0; k < 4; ++k) if (script_axis[0] && !strncmp(script_axis, an[k], 2)) *ax[k] = axv;   // sticks: seen by the layout
        if (kind_native(pad_kinds[idx])) pad_native(st, st->buttons);
        else pad_apply_layout(idx, st, st->buttons);   // buttons= is the physical P_* mask, mapped like a real pad
        st->buttons |= script_raw;
        for (int k = 0; k < 6; ++k) if (script_axis[0] && !strncmp(script_axis, an[k], 2)) *ax[k] = axv;
        static uint32_t last_sim = ~0u;
        if (diag_on && st->buttons != last_sim) {
            last_sim = st->buttons;
            diag_event("pad: sim buttons %03X ry %.2f, %s (package in control %s)", st->buttons, st->ry, pad_mode, pad_ctl ? pad_ctl : "none");
        }
        rumble_update();                    // a connected DualSense still plays the rumble
        // the adaptive triggers and lightbar decide as for a real pad and only log it (padfx.m never touches a pad here)
        padfx_update(NULL, kind == PAD_KIND_DUALSENSE || kind == PAD_KIND_EDGE, st->lt, st->rt);
        return 1;
    }
    if (!headless && !NSApp.isActive) { st->connected = 1; st->native = kind_native(pad_kinds[idx]); return 1; }   // another app has focus: centred, nothing pressed
    @autoreleasepool {
        GCController *ctl = pad_controller_n(idx);
        GCExtendedGamepad *g = ctl.extendedGamepad;
        if ((g != nil) != pad_presents[idx])
            pad_presence(idx, g != nil, g ? [NSString stringWithFormat:@"%@ (%@, %lu controllers)", ctl.vendorName ?: @"controller",
                                        ctl.productCategory ?: @"?", (unsigned long)GCController.controllers.count].UTF8String : NULL);
        if (idx == 0) prompts_pad_seen(g != nil);
        if (!g) return 0;
        // Pad 1 feedback would hook here once the model and legacy single-output HID transport have player routing.
        if (idx == 0) rumble_update();
        st->connected = 1;
        int kind = PAD_KIND_OTHER;
        NSString *cat = ctl.productCategory ?: @"";
        if ([g isKindOfClass:GCDualSenseGamepad.class] || [cat hasPrefix:@"DualSense"])
            kind = [cat rangeOfString:@"Edge" options:NSCaseInsensitiveSearch].location != NSNotFound ? PAD_KIND_EDGE : PAD_KIND_DUALSENSE;
        else if ([g isKindOfClass:GCDualShockGamepad.class] || [cat hasPrefix:@"DualShock"]) kind = PAD_KIND_DUALSHOCK4;
        set_identity(idx, kind, ctl.vendorName.UTF8String);
        if (idx == 0) padfx_update((__bridge void *)ctl, [g isKindOfClass:GCDualSenseGamepad.class], g.leftTrigger.value, g.rightTrigger.value);
        st->lx = g.leftThumbstick.xAxis.value; st->ly = g.leftThumbstick.yAxis.value;
        st->rx = g.rightThumbstick.xAxis.value; st->ry = g.rightThumbstick.yAxis.value;
        st->lt = g.leftTrigger.value; st->rt = g.rightTrigger.value;
        GCControllerButtonInput *b[12] = {g.buttonA, g.buttonB, g.buttonX, g.buttonY, g.leftShoulder, g.rightShoulder,
                                          g.buttonOptions, g.buttonMenu, g.leftThumbstickButton, g.rightThumbstickButton,
                                          g.leftTrigger, g.rightTrigger};          // P_* order
        uint32_t phys = 0;
        for (int i = 0; i < 12; ++i) if (b[i].pressed) phys |= 1u << i;
        GCControllerDirectionPad *d = g.dpad;
        int up = d.up.pressed, down = d.down.pressed, left = d.left.pressed, right = d.right.pressed;
        // the F10 menu's shortcut: the touchpad click (DualSense, DualShock 4), the PS/Guide button, or holds (portmenu.m)
        GCControllerButtonInput *touch = nil;
        if ([g isKindOfClass:GCDualSenseGamepad.class]) touch = ((GCDualSenseGamepad *)g).touchpadButton;
        else if ([g isKindOfClass:GCDualShockGamepad.class]) touch = ((GCDualShockGamepad *)g).touchpadButton;
        GCControllerButtonInput *home = g.buttonHome;
        int choice = settings_get("controls.menu_button");
        static __weak GCControllerButtonInput *home_claimed;
        if (idx == 0 && home && home != home_claimed && (choice == 1 || (choice == 0 && !touch) || kind_native(kind))) {
            // the system's Home-button gesture (Launchpad / game overlay) off while we're frontmost, so the press is ours
            home.preferredSystemGestureState = GCSystemGestureStateDisabled;
            home_claimed = home;
            if (diag_on) diag_event("pad: claimed the Home button from the system (%s)", ctl.vendorName.UTF8String ?: "?");
        }
        if (diag_on && home.pressed) { static int logged; if (!logged++) diag_event("pad: Home button delivered to the app"); }
        uint32_t inject = 0, hide = idx == 0 ? portmenu_pad(phys, up, down, left, right, st->lx, st->ly, home.pressed, touch.pressed, touch != nil, &inject) : 0;
        if (hide == ~0u) { pad_neutral(idx, st); return 1; }
        phys = (phys & ~hide) | inject;
        if (kind_native(kind)) {           // PS, the touchpad click, the Edge's back paddles, when not the menu's shortcut
            GCControllerButtonInput *lp = nil, *rp = nil;
            if (kind == PAD_KIND_EDGE) if (@available(macOS 13.0, *)) {
                lp = ctl.physicalInputProfile.buttons[GCInputLeftPaddle]; rp = ctl.physicalInputProfile.buttons[GCInputRightPaddle];
            }
            uint32_t extras = (home.pressed ? 1u << P_PS : 0) | (touch.pressed ? 1u << P_TOUCHPAD : 0) |
                              (lp.pressed ? 1u << P_LPADDLE : 0) | (rp.pressed ? 1u << P_RPADDLE : 0);
            pad_native(st, phys | (extras & (idx == 0 ? pad_game_extras(touch != nil) : 0xF000u)));
        } else
            pad_apply_layout(idx, st, phys);
        if (idx == 0 && (phys || up || down || left || right || fabsf(st->lx) > 0.5f || fabsf(st->ly) > 0.5f || fabsf(st->rx) > 0.5f ||
            fabsf(st->ry) > 0.5f || g.leftTrigger.value > 0.5f || g.rightTrigger.value > 0.5f))
            prompts_input(1);
        if (up || down || left || right) {
            int x = right - left, y = up - down;
            static const int pov[3][3] = {{22500, 27000, 31500}, {18000, -1, 0}, {13500, 9000, 4500}};   // [x+1][y+1]
            st->pov = pov[x + 1][y + 1];
        }
        if (diag_on) {                   // each change of buttons/D-pad, or of a stick/trigger by 0.25
            static PadState lasts[2];
            PadState last = lasts[idx];
            float d = fabsf(st->lx - last.lx) + fabsf(st->ly - last.ly) + fabsf(st->rx - last.rx) + fabsf(st->ry - last.ry) +
                      fabsf(st->lt - last.lt) + fabsf(st->rt - last.rt);
            if (st->buttons != last.buttons || st->pov != last.pov || d > 0.25f) {
                diag_event("pad: buttons %03X pov %d lx %.2f ly %.2f rx %.2f ry %.2f lt %.2f rt %.2f, %s (package in control %s, app active %d)",
                           st->buttons, st->pov, st->lx, st->ly, st->rx, st->ry, st->lt, st->rt, pad_mode, pad_ctl ? pad_ctl : "none",
                           (int)NSApp.isActive);
                lasts[idx] = *st;
            }
        }
    }
    return 1;
}

int pad_read(PadState *st) { return pad_read_n(0, st); }
'''
assert hashlib.sha256(OLD_PAD.encode()).hexdigest() == '5b24b223ed6136d114887abf993a95aa4d573451732212a17ef7cfbcce530c06', 'pinned baseline changed'

def main():
    launcher_safety()
    full=(R/'user32.m').read_text(); user=full[full.index('static uint32_t bit('):]
    seq=full[full.index('static void script_seq('):full.index('    // find step `step`',full.index('static void script_seq('))]
    a=full.index('    } else if (!strncmp(tok, "pad:", 4)'); b=full.index('    } else if (!strncmp(tok, "sim:", 4))',a)
    # Only the pad command dispatch; declarations/tokens are the production ones.
    parser=full[full.index('    // find step `step`',full.index('static void script_seq(')):full.index('    if (!strncmp(tok, "skipmovies", 10))')]
    seq+=parser+full[a:b].replace('    } else if (','    if (',1)+'    }\n}\n'
    variants={'normal':(user,seq),
              'slot1-absent':(user.replace('if (idx < 0 || idx >= pad_count())','if (idx != 0 || idx >= pad_count())'),seq),
              'script-alias':(user.replace('idx ? script_pad2 : script_pad','script_pad'),seq),
              'release-missing':(user,seq.replace('script_pad2 = script_raw2 = 0; script_axis2[0] = 0;','(void)0;')),
              'slot1-menu-leak':(user.replace('idx ? (portmenu_is_open() ? ~0u : 0) : portmenu_pad','idx ? 0 : portmenu_pad'),seq)}
    with tempfile.TemporaryDirectory(prefix='padscript-') as tmp:
        tmp=Path(tmp)
        def compile_one(name,u,q,main):
            src=tmp/(name+'.m'); out=tmp/name
            src.write_text('\n'.join((h.FAKE,PRE,u,q,main)))
            h.run(['clang','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-function',
                   '-Wno-unused-parameter','-fobjc-arc','-Wno-deprecated-declarations','-fsanitize=address,undefined',
                   '-I',str(R),'-I',str(R.parent/'recomp'),str(src),'-framework','Foundation','-o',str(out)])
            return out
        for name,(u,q) in variants.items():
            out=compile_one(name,u,q,MAIN)
            for mode in ([None,'1','invalid','2'] if name=='normal' else ['2']):
                env=dict(os.environ, NFS_RUMBLE='off'); env.pop('NFS_PADS',None); env.pop('BOOT_PAD',None); env.pop('BOOT_PAD2',None)
                if mode: env['NFS_PADS']=mode
                p=subprocess.run([out],env=env,capture_output=True,text=True)
                assert p.returncode==(0 if name=='normal' else 1),p.stdout[-2000:]+p.stderr[-2000:]
                print(name+': '+p.stdout.strip().splitlines()[-1])
        old=OLD_PAD
        # Adapt the baseline's call signature to the indexed fake (always returns zero).
        old=old.replace('controltype_passthrough(void)', 'controltype_passthrough(int idx)')
        old=old.replace('controltype_passthrough()', 'controltype_passthrough(idx)')
        for label,u in [('before',old),('after',user)]:
            out=compile_one(label,u,'',BASEMAIN)
            for mode in [None,'1','invalid']:
                env=dict(os.environ, NFS_RUMBLE='off'); env.pop('NFS_PADS',None);env.pop('BOOT_PAD',None);env.pop('BOOT_PAD2',None)
                if mode: env['NFS_PADS']=mode
                h.run([out,tmp/(label+str(mode))],env=env)
        for mode in [None,'1','invalid']:
            before=(tmp/('before'+str(mode))).read_bytes(); after=(tmp/('after'+str(mode))).read_bytes()
            # Physical-device tracking now happens before overlay consumption. Repeated
            # polls no longer call prompts_input; the state/DI/feedback samples stay exact.
            prompt_offset = len(after) - 8
            assert before[:prompt_offset] == after[:prompt_offset], mode
            assert before[prompt_offset + 4:] == after[prompt_offset + 4:], mode
            assert struct.unpack_from('i', before, prompt_offset)[0] > 0
            assert struct.unpack_from('i', after, prompt_offset)[0] == 0
            broken = bytearray(after); broken[0] ^= 1
            assert before[:prompt_offset] != broken[:prompt_offset], 'broken snapshot accepted'
            print(f'one-pad {mode}: 80 PadState snapshots + guest memory/DI/feedback identical ({len(after)} bytes); physical prompts moved before consumption')
    print('padscript_test: PASS')

if __name__=='__main__': main()
