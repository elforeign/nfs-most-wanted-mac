#!/usr/bin/env python3
"""Combined Controls capture -> resolver -> race -> save proof, ASan/UBSan.
Fake FE widgets, physical devices and scratch files only. Binding insertion,
selection, layout mapping and evaluation use production/translated functions.
PASS: 393,940 checks / 0 failures across native PS and both non-PS layouts,
front-end and pause. Reset-layout mutation with the production row/settings fixture: 48 / 393,940
failures (PS 28, Xbox 20, PS2 0). Original standalone fixture: 58 failures.
Reset checks both slots of all 19 actions and Speedbreaker's physical controls.
Pinned 64bf1349 functions: 764 / 393,914 failures in each capture/P2 variant
(capture phase 244; P2 phase 332), never a compile rejection.
Capture-remap / P2-leak / restore / cancel / premature-save mutants:
428 / 332 / 11,146 / 1,890 / 4,392 failures out of 393,914 / 393,940 /
393,940 / 393,940 / 393,940 checks. Earlier pinned standalone proofs remain.
Original function fragments are embedded and SHA-256 verified without Git.
Baseline isolation: 3 checks / 0 failures; pre-fix: 3 / 3 failures.
"""
from pathlib import Path
import ast
from types import SimpleNamespace
import hashlib
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent


def helpers(name, members):
    """Load only shared fixtures, without running another test's baselines."""
    path = R/(name+'_test.py')
    tree = ast.parse(path.read_text(), filename=str(path))
    selected = []
    found = set()
    for node in tree.body:
        names = {node.name} if isinstance(node, ast.FunctionDef) else {
            target.id for target in node.targets if isinstance(target, ast.Name)
        } if isinstance(node, ast.Assign) else set()
        if names & set(members):
            selected.append(node)
            found.update(names)
    assert found == set(members), (name, found)
    tree.body = selected
    namespace = {'__file__': str(path), 'Path': Path, 're': re}
    exec(compile(tree, str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


ui = helpers('controlpages', ('R', 'EXTRA', 'translated'))
store = helpers('padsets', ('R', 'translated'))
h = helpers('padguest', ('ROOT', 'PRE', 'translated'))


def function_span(source, name):
    """Locate a top-level function, independent of its line number or body."""
    matches = list(re.finditer(r'^(?:static )?(?:void|int) '+re.escape(name)+r'\([^\n]*', source, re.M))
    assert len(matches) == 1, name
    match = matches[0]
    end = match.end() if match[0].rstrip().endswith('}') else source.index('\n}', match.end()) + 2
    return match.start(), end + 1  # include the trailing newline


def replace_function(source, name, original):
    start, end = function_span(source, name)
    return source[:start] + original + source[end:]


def pinned(source, digest):
    """Verify embedded original C, never a surrounding production file."""
    assert hashlib.sha256(source.encode()).hexdigest() == digest
    return source


# Original function fragments from 64bf1349; hashes cover their exact stored text.
OLD_SEED = pinned(r'''static void seed_drag(Set *row, const Set *base) {
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
}
''', 'b3c39ef9f176c05ac35723ce3835f9312e7802df3d74bf8563a2924b21fba164')

OLD_DEFAULTS = pinned(r'''void padbind_drag_defaults(Cpu *c) {
    if (edited_set != PADBIND_DRAG || !joy()) return;
    // Generate true layout defaults without modifying Default, shared keyboard
    // bindings or menu actions. Only the Drag joystick page is replaced.
    uint8_t saved[GA_N * 0x34];
    memcpy(saved, G + GA, sizeof saved);
    install_defaults(c);
    Set base = {0}, drag;
    set_from_table(&base);
    seed_drag(&drag, &base);
    memcpy(G + GA, saved, sizeof saved);
    set_to_table(&drag);
    remember_page();
    dump("Drag defaults");
}
''', '84d3715e99ac569ec78392d83562280eba4aac3816f1317139d64de75cdcef49')

OLD_POLICY = pinned(r'''int controltype_passthrough(void) {             // user32.m: physical buttons for Drag and variants 2-5
    if (ct_type != s_type.value + 1) ct_apply(s_type.value);
    return padbind_input_set() == PADBIND_DRAG || (ct_type > 1 && ct_variant(ct_type) != 0);
}
''', '5aa1e6fafe56a5fbea2bfd416fa643b4d29559cf25cfa9ba8aabaee7bb42f106')

OLD_MAPPING = pinned(r'''static uint32_t bit(uint32_t phys, int b) { return (phys >> b) & 1; }
static int pad0_down;   // (defined after bit(): the pad harnesses compile user32.m from there)
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

''', 'e4c24a65ac7e18fbf5f7a4c187c1b91926c4d7a66d53dada07e10b4d751b4256')

def old_sources(pad, policy, user):
    pad = replace_function(pad, 'seed_drag', OLD_SEED)
    pad = replace_function(pad, 'padbind_drag_defaults', OLD_DEFAULTS)
    pad = replace_function(pad, 'padbind_edited_set', '')
    policy = replace_function(policy, 'controltype_passthrough', OLD_POLICY)
    # Adapt only the old declaration/call ABI, preserving historical behavior.
    policy = policy.replace('controltype_passthrough(void)', 'controltype_passthrough(int idx)')
    mapping = OLD_MAPPING.replace('controltype_passthrough(void)', 'controltype_passthrough(int idx)')
    mapping = mapping.replace('controltype_passthrough()', 'controltype_passthrough(idx)')
    return pad, policy, mapping


def baseline_isolation(sources, baseline):
    # Each file gets unrelated lines before AND after its tested functions.
    # The old full-file/line-index restoration rejects all three cases.
    prefix, suffix = '/* unrelated insertion */\n', '\n/* unrelated addition */\n'
    for i in range(3):
        changed = list(sources)
        changed[i] = prefix + changed[i] + suffix
        expected = list(baseline)
        if i < 2:
            expected[i] = prefix + expected[i] + suffix
        assert old_sources(*changed) == tuple(expected), i
    print('baseline isolation: 3 checks / 0 failures', flush=True)


EXTRA = r'''
#include <unistd.h>
#include <sys/stat.h>
static int script_layout, native_pad, second_ps;
static int pad_layout_xbox(void) { return script_layout; }
static void di_event(uint8_t key,int down) { (void)key;(void)down; }
static const char *scratch;
const char *diag_dir="";
const char *userdata_dir(void) { return scratch; }
int portmenu_is_open(void) { return 0; }
int pad_count(void) { return 2; }
int user32_pad_layout_xbox(void) { return script_layout; }
int pad_identity_n(int p,char *name,size_t n) {
 int ps=p?second_ps:native_pad;
 snprintf(name,n,"%s",ps?"Controller (DualSense)":"Controller (Xbox)");
 return ps?PAD_KIND_DUALSENSE:PAD_KIND_OTHER;
}
int pad_identity(char *name,size_t n) { return pad_identity_n(0,name,n); }
void pedals_trace_sample(unsigned s,float l,float r) { (void)s;(void)l;(void)r; }
void f_006280C0_orig(Cpu *c) { (void)c; }
void f_00634900_orig(Cpu *c) { ++capture_calls; c->esp+=4; }
void f_004F6EA0_orig(Cpu *c) { (void)c; }
void f_0063CD80_orig(Cpu *c) { c->esp+=4; }
void f_005FA780(Cpu *c) { c->esp+=8; }
void f_00454810(Cpu *c) { c->eax=c->ecx; c->esp+=12; }
void f_006269B0(Cpu *c) { c->eax=0x280000; c->esp+=4; }
void f_00460D20(Cpu *c) { c->eax=strcmp((char *)G+arg(c,0),(char *)G+arg(c,1)); c->esp+=4; }
void f_00634DA0(Cpu *c) { c->eax=name_hash(joy_name(joy())); c->esp+=8; }
void f_00628CC0(Cpu *c) { c->eax=0x30; c->esp+=4; }
'''

MAIN = r'''
static uint32_t base[19][6], captured[19][6];
static uint8_t keys[19][24], menus[(76-19)*52];
static void invoke(void (*fn)(Cpu *),uint32_t self) { Cpu c=cpu(self); fn(&c); abi(&c,0); }
static void page(uint32_t out[19][6]) { for(int a=0;a<19;a++) memcpy(out[a],G+rec(a,2),24); }
static void invariants(uint32_t other[19][6],int set) {
 for(int a=0;a<19;a++) {
  uint32_t resolved[6]; CHECK(padbind_effective_binding(set,a,resolved));
  CHECK(!memcmp(resolved,other[a],24));
  CHECK(!memcmp(keys[a],G+rec(a,0),24));
 }
 CHECK(!memcmp(menus,G+GA+19*52,sizeof menus));
}
static void samples(int port,uint32_t phys,float lt,float rt,float stick) {
 memset(fake_pad[port],0,0x224);
 for(int i=0;i<3;i++) { fake_put(port,4*i,32768); fake_put(port,0x20c+4*i,32768); }
 PadState p={.lt=lt,.rt=rt,.ry=-stick};
 // Native pads expose physical P_* directly; other pads use the actual layout.
 if((port?second_ps:native_pad)) p.buttons=phys;
 else pad_apply_layout(port,&p,phys);
 for(int b=0;b<32;b++) fake_put(port,0xc+4*b,(p.buttons&(1u<<b))?128:0);
 fake_put(port,8,(uint32_t)lroundf(32767.5f*(1+p.lt)));
 fake_put(port,0x214,(uint32_t)lroundf(32767.5f*(1+p.rt)));
 fake_put(port,0x210,(uint32_t)lroundf(32767.5f*(1+stick)));
 fake_put(port,0x220,0xffffffff);
 memcpy(G+0x121000+0x1000*port,fake_pad[port],0x224);
}
static void state(int drag,int paused,const char *package,int flow);
static void capture(int a,int slot,int kind,int object,uint32_t phys,float lt,float rt,float stick) {
 M32(0x920de8)=d[0]; M8(d[0]+0x183c)=1;
 M32(d[0]+0x1840)=3; M32(d[0]+0x1844)=slot; M32(d[0]+0x1848)=a;
 samples(0,0,0,0,0); samples(0,0,0,0,0); // release held Accept
 samples(0,phys,lt,rt,stick);
 uint32_t event=0x290000, iface=0x290100;
 M32(event+4)=iface; M32(iface)=0x111000; M32(iface+8)=3; M32(iface+12)=0;
 M32(event+12)=kind; M32(event+16)=kind==1?object:0;
 for(int i=0;i<3;i++) { M32(BASELINE+4*i)=32768; M32(0x91f3f4+4*i)=32768; }
 if(kind==1 && !native_pad) {
  // Choose the emitted DI object, never a fixture binding result.
  int found=-1;
  for(int b=0;b<32;b++) if(M32(0x12100c+4*b)) { CHECK(found==-1); found=b; }
  CHECK(found>=0); M32(event+16)=found;
 }
 Cpu c=cpu(d[0]); M32(c.esp+4)=event; f_00634490(&c); abi(&c,1); CHECK((c.eax&255)==1);
 if(kind==1) CHECK(eval(a,slot,0)==1);
 M8(d[0]+0x183c)=0; samples(0,0,0,0,0); samples(0,0,0,0,0);
 if(kind==1) {
  // Leave the page's captured records resident and take the same physical
  // control through the real driving update, before another capture can clear it.
  uint8_t resident[76*52]; memcpy(resident,G+GA,sizeof resident);
  state(edited_set==PADBIND_DRAG,0,"HUD_Drag.fng",6);
  samples(0,0,0,0,0); samples(0,phys,0,0,0);
  Cpu drive=cpu(d[0]); f_006349B0(&drive); abi(&drive,0);
  CHECK(value(0,a)==1); CHECK(!memcmp(resident,G+GA,sizeof resident));
  state(0,0,"HUD_Race.fng",3);
 }
}
static void save(int hooked,uint32_t buf) {
 Cpu c=cpu(0); M32(c.esp+4)=buf; M32(c.esp+8)=buf+0x394;
 M8(buf+0x394)=0xa5;
 if(hooked) f_0056E180(&c); else f_0056E180_orig(&c);
 abi(&c,0); CHECK(c.eax==buf+0x394 && M8(buf+0x394)==0xa5);
}
static void state(int drag,int paused,const char *package,int flow) {
 M32(0x91e000)=0x210000; M32(0x211968)=drag?0x220000:0;
 M32(0x220004)=0x230000; M8(0x23002b)=2;
 M32(0x925e90)=flow; M32(0x9885e0)=0x240000; M32(0x24002c)=paused?4:3;
 M32(0x91cadc)=0x250000; M32(0x250000)=0x251000; M32(0x2510e4)=0x252000;
 M32(0x252020)=1; M32(0x25200c)=0x253000;
 strcpy((char *)G+0x253000,package);
 Cpu c=cpu(d[0]); padbind_race_update(&c);
}
static void frame(int selected,uint32_t physical,float lt,float rt,float stick) {
 CHECK(padbind_input_set()==selected);
 samples(0,0,lt,rt,stick); samples(0,physical,lt,rt,stick);
 uint32_t (*expected)[6]=selected?captured:base;
 // Independent expected table + translated evaluator, then restore resident Default.
 float values[19];
 for(int a=0;a<19;a++) {
  uint32_t resolved[6]; CHECK(padbind_effective_binding(selected,a,resolved));
  CHECK(!memcmp(resolved,expected[a],24));
  memcpy(G+rec(a,2),expected[a],24); float v=0;
  for(int b=0;b<4;b++) { float w=eval(a,b,0); if(w>v) v=w; }
  values[a]=v; memcpy(G+rec(a,2),base[a],24);
 }
 Cpu c=cpu(d[0]); f_006349B0(&c); abi(&c,0);
 for(int a=0;a<19;a++) { CHECK(value(0,a)==values[a]); CHECK(!memcmp(G+rec(a,2),base[a],24)); }
 CHECK(!input_installed && !swap_on && edited_set==PADBIND_DEFAULT);
 if(selected && physical==(1u<<P_R1)) CHECK(value(0,A_SHIFTUP)==1);
 if(selected && physical==(1u<<P_L1)) CHECK(value(0,A_SHIFTDOWN)==1);
 if(selected && physical==(1u<<P_CROSS)) CHECK(value(0,A_N2O)==1);
 invariants(base,PADBIND_DEFAULT);
 if(native_pad) {
  CHECK(padbind_trigger_role(0)==(selected?PADBIND_ROLE_BRAKE:PADBIND_ROLE_SHIFT));
  CHECK(padbind_trigger_role(1)==(selected?PADBIND_ROLE_GAS:PADBIND_ROLE_SHIFT));
  CHECK(padbind_ps_label(12,0)==(selected?0x6AB80AFAu:0x56AFE190u));
  CHECK(padbind_ps_label(13,0)==(selected?0x619B23B0u:0xDC1CD97Du));
  CHECK(padbind_ps_label(1,0)==(selected?0x56AFE190u:0));
  CHECK(padbind_ps_label(3,0)==(selected?0xDC1CD97Du:0));
  CHECK(padbind_ps_label(6,0)==(selected?0x5E5C5F2Eu:0));
  CHECK(padbind_ps_label(15,0)==0x619B23B0u && padbind_ps_label(16,0)==0x6AB80AFAu);
 } else {
  CHECK(padbind_trigger_role(0)==PADBIND_ROLE_NONE && padbind_trigger_role(1)==PADBIND_ROLE_NONE);
  CHECK(padbind_ps_label(12,123)==123 && padbind_ps_label(13,456)==456);
 }
}
static void p2(void) {
 static float before_edits[12][19]; static int have_baseline;
 // Mixed layout: P1 native / P2 non-PS and the reverse. P1's actual Drag selection
 // must leave every evaluated P2 Default action identical to circuit.
 second_ps=!native_pad;
 for(int physical=0;physical<12;physical++) {
  float expected[19];
  for(int drag=0;drag<2;drag++) {
   state(drag,0,drag?"HUD_Drag.fng":"HUD_Race.fng",6);
   CHECK(padbind_input_set()==drag);
   samples(1,0,.25f,.75f,-.8f); samples(1,1u<<physical,.25f,.75f,-.8f);
   uint8_t resident[76*52]; memcpy(resident,G+GA,sizeof resident);
   Cpu c=cpu(d[1]); f_006349B0(&c); abi(&c,0);
   for(int a=0;a<19;a++) { if(!drag) {
    expected[a]=value(1,a);
    if(have_baseline) CHECK(value(1,a)==before_edits[physical][a]);
    else before_edits[physical][a]=value(1,a);
   } else CHECK(value(1,a)==expected[a]); }
   CHECK(!memcmp(resident,G+GA,sizeof resident)); CHECK(!input_installed && !swap_on);
  }
 }
 have_baseline=1;
}
int main(int argc,char **argv) {
 scratch=argv[1]; native_pad=atoi(argv[2])==0; script_layout=atoi(argv[2])==2;
 int pause=atoi(argv[3]); second_ps=!native_pad; G=calloc(1,0x1000000);
 MF32(0x890968)=0; MF32(0x89096c)=1; MF32(0x8a7a58)=32767.5f;
 MF32(0x8a7a5c)=.0078125f; MF32(0x8957bc)=3.0518044e-05f;
 for(int p=0;p<2;p++) { Cpu c=cpu(d[p]); M32(c.esp+4)=p; f_00634B50(&c); abi(&c,1); }
 M32(IOMOD_DEVS)=d[0]; M32(IOMOD_DEVS+4)=d[1]; M32(0x982bec)=1;
 pad_identity(joy_name(joy()),64); active_layout=native_pad?L_NATIVE_PS:script_layout?L_NONPS_XBOX:L_NONPS_PS2;
 M32(FEDB)=0x200000; M32(0x200010)=0x201000; strcpy((char *)G+0x201000,"Player");
 for(int a=0;a<76;a++) for(int k=0;k<4;k++) set_rec(rec(a,k),k<2?T_KEY:T_BUTTON,k<2?200+a:0,k<2?0:(a+k)%12);
 // Retail Speedbreaker: Xbox X; native defaults also add R3.
 set_rec(rec(A_GAMEBREAKER,2),T_BUTTON,0,3);
 set_rec(rec(A_GAMEBREAKER,3),0,0,0);
 memcpy(G+GA_EXE,G+GA,76*52);
 Cpu init=cpu(d[0]); f_0063CD80(&init);
 // Compare Reset Defaults with first-time Drag materialization from true defaults.
 seed_active_pages();
 uint32_t reset_expected[19][6];
 for(int a=0;a<19;a++) CHECK(padbind_effective_binding(PADBIND_DRAG,a,reset_expected[a]));
 free(sets); sets=NULL; nsets=0;
 for(int a=0;a<19;a++) for(int k=2;k<4;k++) set_rec(rec(a,k),0,0,0);
 set_rec(rec(7,2),T_AXIS_HI,0,2); set_rec(rec(8,2),T_ROT_HI,0,2);
 set_rec(rec(0,3),T_ROT_LO,0,1); set_rec(rec(1,3),T_ROT_HI,0,1);
 page(base); seed_active_pages();
 for(int a=0;a<19;a++) memcpy(keys[a],G+rec(a,0),24);
 memcpy(menus,G+GA+19*52,sizeof menus);
 save(0,0x750000); uint8_t retail[0x394]; memcpy(retail,G+0x750000,sizeof retail);
 int p2_before=failures; p2(); int p2_failures=failures-p2_before;
 state(0,0,"HUD_Race.fng",3);
 M32(SCREEN+0xe4)=1; M32(SCREEN+0x2c)=0x720000; M32(0x720008)=0x52ab90;
 M32(SCREEN+0x10)=0x730000; M8(SCREEN+0xe0)=pause;
 strcpy((char *)G+0x730000,pause?"Pause_PC_Controller.fng":"Options_PC_Controller.fng");
 invoke(f_0052AC70,SCREEN); invoke(f_0052AF20,SCREEN);
 CHECK(M32(SCREEN+0xe4)==3 && edited_set==PADBIND_DEFAULT);
 uint32_t seeded_drag[19][6];
 for(int a=0;a<19;a++) CHECK(padbind_effective_binding(PADBIND_DRAG,a,seeded_drag[a]));
 // Default capture uses its layout namespace and cannot edit stored Drag rows.
 int default_capture_before=failures;
 for(int a=0;a<19;a++) for(int slot=2;slot<4;slot++) {
  int button=(a+slot)%4; capture(a,slot,1,button,1u<<button,0,0,0);
  invariants(seeded_drag,PADBIND_DRAG);
 }
 invoke(f_0051D590,SCREEN);
 for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),base[a],24));
 invariants(seeded_drag,PADBIND_DRAG);
 int default_capture_failures=failures-default_capture_before;
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==3 && edited_set==PADBIND_DRAG);
 int capture_before=failures;
 uint32_t snapshot[19][6]; page(snapshot);
 // Both joystick slots of every action, including physical layout capture.
 for(int a=0;a<19;a++) for(int slot=2;slot<4;slot++) {
  int button=(a+slot)%8;
  capture(a,slot,1,button,1u<<button,0,0,0);
  CHECK(M32(rec(a,slot))==T_BUTTON && M32(rec(a,slot)+8)==(unsigned)button);
  invariants(base,PADBIND_DEFAULT);
 }
 int capture_failures=failures-capture_before+default_capture_failures;
 // Cancel restores all captured edits, without touching the opposite page.
 invoke(f_0051D590,SCREEN); page(captured);
 for(int a=0;a<19;a++) CHECK(!memcmp(captured[a],snapshot[a],24));
 invariants(base,PADBIND_DEFAULT);
 for(int a=0;a<19;a++) for(int slot=2;slot<4;slot++) {
  int button=(a+slot)%8; capture(a,slot,1,button,1u<<button,0,0,0);
 }
 invoke(f_0051D6E0,SCREEN);
 CHECK(M32(rec(0,2))==T_ROT_HI && M32(rec(1,2))==T_AXIS_HI);
 CHECK(M32(rec(7,2)+8)==P_L1 && M32(rec(8,2)+8)==P_R1);
 CHECK(M32(rec(6,2)+8)==P_CROSS); invariants(base,PADBIND_DEFAULT);
 // Every slot, including unaffected actions, keeps the first-time physical binding.
 for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),reset_expected[a],24));
 CHECK(M32(rec(A_GAMEBREAKER,2))==T_BUTTON);
 CHECK(M32(rec(A_GAMEBREAKER,2)+8)==P_SQUARE);
 if(native_pad) CHECK(M32(rec(A_GAMEBREAKER,3))==T_BUTTON && M32(rec(A_GAMEBREAKER,3)+8)==P_R3);
 // Retain right-stick secondary gas/brake, capture independent pressure primaries.
 capture(0,2,4,2,0,0,1,0); capture(1,2,3,2,0,1,0,0);
 capture(0,3,4,1,0,0,0,-1); capture(1,3,4,1,0,0,0,1);
 capture(7,2,1,P_L1,1u<<P_L1,0,0,0);
 capture(8,2,1,P_R1,1u<<P_R1,0,0,0);
 capture(6,2,1,P_CROSS,1u<<P_CROSS,0,0,0);
 page(captured);
 // Shared keyboard edits while Drag is selected go into the retail payload.
 set_rec(rec(18,0),T_KEY,77,88); memcpy(keys[18],G+rec(18,0),24);
 memcpy(retail+4+18*48,keys[18],24);
 char path[1100]; sets_path(path,sizeof path);
 save(1,0x760000); CHECK(!memcmp(retail,G+0x760000,sizeof retail));
 CHECK(access(path,F_OK)!=0); padbind_save_complete(0);
 CHECK(!pending_bindings_valid && access(path,F_OK)!=0);
 save(1,0x760000); char tmp[1110]; snprintf(tmp,sizeof tmp,"%s.tmp",path); mkdir(tmp,0700);
 padbind_save_complete(1); CHECK(sets_dirty && access(path,F_OK)!=0); rmdir(tmp);
 save(1,0x760000); padbind_save_complete(1); CHECK(access(path,F_OK)==0);
 // A newer translated capture during an in-flight save survives page publication.
 save(1,0x760000); capture(18,2,1,P_TRIANGLE,1u<<P_TRIANGLE,0,0,0);
 uint32_t newer[6]; memcpy(newer,G+rec(18,2),24);
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==1);
 padbind_save_complete(1); CHECK(sets_dirty);
 uint32_t resolved[6]; CHECK(padbind_effective_binding(PADBIND_DRAG,18,resolved)); CHECK(!memcmp(newer,resolved,24));
 // Reload the committed snapshot: both sets, then the actual retail profile hook.
 free(sets); sets=NULL; nsets=sets_loaded=sets_dirty=0; sets_load();
 for(int a=0;a<19;a++) { CHECK(padbind_effective_binding(PADBIND_DRAG,a,resolved)); CHECK(!memcmp(captured[a],resolved,24)); }
 Cpu c=cpu(0); M32(c.esp+4)=0x760000; M32(c.esp+8)=0x760394; f_0056E200(&c);
 invariants(base,PADBIND_DEFAULT);
 // Exit directly from Drag, after reloading its captured snapshot.
 invoke(f_0052AF20,SCREEN); invoke(f_0052AF20,SCREEN);
 CHECK(M32(SCREEN+0xe4)==3 && edited_set==PADBIND_DRAG);
 for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),captured[a],24));
 invoke(f_0052A780,SCREEN); CHECK(edited_set==PADBIND_DEFAULT);
 for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),base[a],24));
 // Countdown -> racing -> pause -> resume -> restart -> results/quit -> roam.
 for(int stage=0;stage<9;stage++) {
  int selected=stage==0||stage==1||stage==3||stage==5;
  state(stage!=4&&stage!=8,stage==2,stage==6?"RaceResults.fng":stage==2?"Pause.fng":"HUD_Drag.fng",stage==7?3:6);
  if(stage==0) M32(0x24002c)=1;
  for(int li=0;li<5;li++) for(int ri=0;ri<5;ri++) {
   frame(selected,0,li*.25f,ri*.25f,0);
   if(selected) { CHECK(fabsf(value(0,0)-ri*.25f)<.0001f); CHECK(fabsf(value(0,1)-li*.25f)<.0001f); }
  }
  for(int s=-4;s<=4;s++) {
   frame(selected,0,0,0,s*.25f);
   CHECK(fabsf(value(0,0)-fmaxf(0,-s*.25f))<.0001f);
   CHECK(fabsf(value(0,1)-fmaxf(0,s*.25f))<.0001f);
  }
  for(int b=0;b<10;b++) frame(selected,1u<<b,0,0,0);
 }
 int p2_after=failures; p2(); p2_failures+=failures-p2_after;
 printf("phases: capture=%d p2=%d\n",capture_failures,p2_failures);
 printf("integration: %d checks / %d failures; layout=%d pause=%d\n",checks,failures,atoi(argv[2]),pause);
 free(sets); free(G); return failures!=0;
}
'''


def main():
    if not (R.parent/'recomp/out/speed_31.c').exists():
        print('SKIP: needs translated input functions'); return 3
    pre = re.sub(r'void settings_add\(.*?\n', '', h.PRE)
    for name in ('padbind_port_begin', 'padbind_port_end', 'padbind_input_set',
                 'padbind_input_begin', 'padbind_input_end', 'padbind_follow',
                 'padbind_swap', 'f_00634900', 'padbind_race_update'):
        pre, count = re.subn(r'^(?:int|void) '+name+r'\([^\n]*\n', '', pre, flags=re.M)
        assert count == 1, name
    pre = pre.replace('++failures; printf(', '++failures; if(failures<12) printf(')
    pre = pre.replace('++key_reads; c->eax=1;', '++key_reads; c->eax=0;')
    pre = pre.replace('switch(t) {', 'switch(t) {\n case 0x52ab90: f_0052AB90(c); break;')
    pre = pre.replace('void dispatch(Cpu *c,uint32_t t)', 'void f_0052AB90(Cpu *c);\nvoid dispatch(Cpu *c,uint32_t t)')
    pre = re.sub(r'\bput\(', 'fake_put(', pre)
    hooks = ['0052AF20','0052AB90','0052AC70','0051D4B0','0051D590','0051D6E0','0052A780']
    decls = '\n'.join('void f_'+a+'(Cpu *c); void f_'+a+'_orig(Cpu *c);' for a in hooks)
    widgets = ui.EXTRA[ui.EXTRA.index('void f_00634370'):ui.EXTRA.index('void dispatch')]
    bodies = '\n'.join(h.translated(file, name) for file, names in (
        ('speed_31.c', ('00634B50','006349B0','00634490')),
        ('speed_30.c', ('00628940','006284A0','006285F0')),
        ('speed_12.c', ('004D6150',)), ('speed_27.c', ('005FAA20',)),
    ) for name in names)
    bodies += '\n'+store.translated('0056E180')+'\n'+store.translated('0056E200')
    bodies += '\n'+'\n'.join(ui.translated(a) for a in ('0052AF20','0051D4B0','0051D590','0051D6E0'))
    pad=(R/'padbind.c').read_text(); ct=(R/'controltype.c').read_text(); controls=(R/'controlpages.c').read_text()
    user=(R/'user32.m').read_text(); mapping=user[user.index('static uint32_t bit('):user.index("// The pad's identity:")]
    variants={'normal':(pad,ct,mapping,controls)}
    oldpad,oldct,oldmap=old_sources(pad,ct,user)
    baseline_isolation((pad,ct,user),(oldpad,oldct,oldmap))
    variants['64bf1349-capture']=(oldpad,oldct,oldmap,controls)
    variants['64bf1349-p2']=(oldpad,oldct,oldmap,controls)
    for name,which,before,after in (
        ('capture-remap',2,'padbind_edited_set()','padbind_input_set()'),
        ('p2-leak',1,'idx == 0 && padbind_input_set()','padbind_input_set()'),
        ('restore',0,'if (!swapped) return;\n    for (int a = 0;','if (1) return;\n    for (int a = 0;'),
        ('cancel',3,'f_0051D590_orig(c);','c->esp += 4;'),
        ('premature-save',0,'dump("profile staged");','padbind_save_complete(1); dump("profile staged");'),
        ('reset-layout',0,'Set base = {.layout = active_layout}, drag;','Set base = {0}, drag;'),
    ):
        sources=[pad,ct,mapping,controls]; assert before in sources[which],name
        sources[which]=sources[which].replace(before,after,1); variants[name]=tuple(sources)
    cache=R.parent/'.metal-cache'; cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='controls-integration-',dir=cache) as tmp:
        tmp=Path(tmp)
        for name,(pb,policy,layout,screen) in variants.items():
            source=pre+'\n#define SCREEN 0x400000u\nstatic int header_calls,snapshot_calls;\n'+pb+'\n'+decls+'\n'+EXTRA+'\n'+widgets+'\n'+bodies+'\n'+policy+'\n#define GAMEFLOW_STATE 0x00925E90u\n#define SIM_INSTANCE 0x009885E0u\n'+layout+'\n'+screen+'\n'+MAIN
            path=tmp/(name+'.c'); exe=tmp/name; path.write_text(source)
            result=subprocess.run(['clang','-std=c11','-g','-w','-DRECOMP_ABICHECK','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(R.parent/'recomp'),'-I',str(R.parent/'recomp/out'),'-I',str(R),str(path),'-o',str(exe)],capture_output=True,text=True)
            assert result.returncode==0,result.stderr[-5000:]
            total=0; total_checks=0; capture_failures=0; p2_failures=0
            for layout_id in range(3):
                for pause in (0,1):
                    data=tmp/(f'{name}-{layout_id}-{pause}'); data.mkdir()
                    result=subprocess.run([exe,data,str(layout_id),str(pause)],capture_output=True,text=True)
                    match=re.search(r'integration: (\d+) checks / (\d+) failures',result.stdout)
                    assert match and result.returncode==int(int(match[2])>0),result.stdout[-2500:]+result.stderr[-3000:]
                    if name=='normal' and int(match[2]): print(result.stdout,flush=True)
                    total_checks+=int(match[1]); total+=int(match[2])
                    print(f'{name} layout={layout_id} pause={pause}: {match[1]} checks / {match[2]} failures',flush=True)
                    phases=re.search(r'phases: capture=(\d+) p2=(\d+)',result.stdout)
                    assert phases,result.stdout[-1000:]
                    capture_failures+=int(phases[1]); p2_failures+=int(phases[2])
            assert (total==0) if name=='normal' else (total>0),name
            if name=='64bf1349-capture': assert capture_failures>0
            if name=='64bf1349-p2': assert p2_failures>0
            print(f'{name} total: {total_checks} checks / {total} failures; capture={capture_failures}, p2={p2_failures}',flush=True)
    print('controls_integration_test: PASS')


if __name__=='__main__':
    raise SystemExit(main())
