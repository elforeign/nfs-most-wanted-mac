#!/usr/bin/env python3
"""Held driving buttons must be released before becoming menu input.
Proof: fixed 1,234 checks / 0 failures; original guard 385 / 1,234 failures.
Compiles the MW production layout and key delivery, ASan/UBSan, no game/hardware.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent
# Original translation/guard fragment: only the three synthetic keys were masked.
ORIGINAL = '''    int xb = pad_layout_xbox();
    uint32_t back = bit(phys, xb ? P_CIRCLE : P_TRIANGLE), fourth = bit(phys, xb ? P_TRIANGLE : P_CIRCLE);
    uint32_t v = fourth << 0 | bit(phys, P_CROSS) << 1 | back << 2 | (phys & 0x30) | bit(phys, P_OPTIONS) << 7;   // menus
    // A button held from driving into a menu (Options pausing) is no key there.
    static uint32_t held_drivings[2];
    uint32_t *held_driving = &held_drivings[idx];
    uint32_t keys = bit(phys, P_SQUARE) | bit(phys, P_CREATE) << 1 | bit(phys, P_OPTIONS) << 2;
    *held_driving = driving ? keys : *held_driving & keys;
    if (idx == 0) pad_menu_keys(keys & ~*held_driving);
'''
ORIGINAL_SHA256 = 'd725b91e576fa1fc0259aa1cd8ff9e1f2a4b3dcc3244991c1ef1e3918c4887b4'
PRE = r'''
#include <stdint.h>
#include <stdio.h>
#include <strings.h>
#define NFS_GAME 1
#define NFS_GAME_MW 1
#define GAMEFLOW_STATE 0x00925E90u
#define SIM_INSTANCE 0x009885E0u
#define PADBIND_DRAG 1
typedef struct { float ry, rt, lt; uint32_t buttons; } PadState;
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS,
       P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD, P_LPADDLE, P_RPADDLE };
static uint32_t flow, sim_ptr, sim_state, key_down;
#define M32(a) ((a)==GAMEFLOW_STATE ? flow : (a)==SIM_INSTANCE ? sim_ptr : sim_state)
static int layout, listening, passthrough, menu, diag_on;
static const char *pad_mode, *pad_ctl;
static const char *fe_package_in_control(void) { return menu ? "RaceResults.fng" : "HUD_Race"; }
static int pad_remap_listening(void) { return listening; }
static int pad_layout_xbox(void) { return layout; }
static int padbind_edited_set(void) { return 0; }
static int controltype_passthrough(int idx) { (void)idx; return passthrough; }
static void pad_diag_bindings(void) {}
#define diag_event(...) ((void)0)
static void di_event(uint8_t key, int down) {
    if(down) key_down |= 1u << (key-3); else key_down &= ~(1u << (key-3));
}
'''
MAIN = r'''
static int checks, failures;
#define CHECK(x) do { checks++; if(!(x)) { failures++; printf("FAIL line %d check %d\n", __LINE__, checks); } } while(0)
static PadState read_pad(int idx, uint32_t phys) {
    PadState st = {0}; pad_apply_layout(idx, &st, phys); return st;
}
int main(void) {
    // Independently enumerate every mapped menu button: face, shoulders, Select/Start,
    // L3 and shift triggers (the latter remain DI buttons outside driving).
    const int buttons[] = {P_CROSS,P_CIRCLE,P_SQUARE,P_TRIANGLE,P_L1,P_R1,
                           P_CREATE,P_OPTIONS,P_L3,P_L2,P_R2};
    for(layout=0;layout<2;layout++) for(int idx=0;idx<2;idx++)
    for(int pass=0;pass<2;pass++) for(int pause=0;pause<2;pause++)
    for(unsigned b=0;b<sizeof(buttons)/sizeof(buttons[0]);b++) {
        passthrough=pass; flow=6; sim_ptr=1; sim_state=0; menu=0;
        uint32_t physical=1u<<buttons[b];
        read_pad(idx,0);
        PadState drive=read_pad(idx,physical);
        CHECK(read_pad(idx,physical).buttons==drive.buttons);
        CHECK(idx!=0 || key_down==0);
        // Results keep the sim running; pause uses the same release guard.
        menu=!pause; sim_state=pause?4:0;
        for(int tick=0;tick<3;tick++) {
            PadState held=read_pad(idx,physical);
            CHECK(held.buttons==0 && (idx!=0 || key_down==0));
        }
        read_pad(idx,0);
        PadState fresh=read_pad(idx,physical);
        const uint32_t ps_buttons[] = {2,1,0,4,16,32,0,128,2048,512,256};
        uint32_t expected = ps_buttons[b];
        if(layout && b==1) expected=4;
        if(layout && b==3) expected=1;
        if(layout && b==5) expected|=1024;
        if(layout && b>=9) expected=0;
        uint32_t expected_keys = idx ? 0 : b==2 ? 1 : b==6 ? 2 : b==7 ? 4 : 0;
        CHECK(fresh.buttons==expected && (idx!=0 || key_down==expected_keys));
        read_pad(idx,0);
        // A new button on the first menu sample is accepted immediately.
        menu=0; sim_state=0; read_pad(idx,0);
        menu=1; fresh=read_pad(idx,physical);
        CHECK(fresh.buttons==expected && (idx!=0 || key_down==expected_keys));
        read_pad(idx,0);
    }
    // One slot's driving hold must not suppress a fresh press on the other.
    layout=0; passthrough=0; menu=0; sim_state=0;
    read_pad(0,1u<<P_CROSS); read_pad(1,0); menu=1;
    CHECK(read_pad(0,1u<<P_CROSS).buttons==0);
    CHECK(read_pad(1,1u<<P_CROSS).buttons==(1u<<1));
    printf("padheld: %d checks, %d failures\n",checks,failures);
    return failures!=0;
}
'''

def main():
    user = (R/'user32.m').read_text()
    mapping = re.search(r'static void pad_apply_layout\(.*?\n}', user, re.S).group()
    keys = re.search(r'static uint32_t bit\(.*?(?=// The FE package)', user, re.S).group()
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA256
    old = mapping
    if '    if (!driving) phys &= ~*held_driving;' in mapping:
        guard_start = mapping.index('    // A button held from driving')
        guard_end = mapping.index('    // While re-mapping, the right stick', guard_start)
        old = mapping[:guard_start] + mapping[guard_end:]
    start = old.index('    int xb =')
    end = old.index('    if (driving)\n', start)
    old = old[:start] + ORIGINAL + old[end:]
    with tempfile.TemporaryDirectory(prefix='padheld-') as tmp:
        for name, body in [('current', mapping), ('original', old)]:
            src = Path(tmp)/(name+'.c'); exe = Path(tmp)/name
            src.write_text(PRE + keys + body + MAIN)
            subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror',
                            '-Wno-unused-variable','-fsanitize=address,undefined',
                            '-fno-sanitize-recover=all',str(src),'-o',str(exe)],check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(name+': '+result.stdout.strip().splitlines()[-1])
            expected = 0 if name=='current' else 1
            assert result.returncode == expected, result.stdout+result.stderr
    print('padheld_test: PASS')

if __name__ == '__main__':
    main()
