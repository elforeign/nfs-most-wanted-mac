#!/usr/bin/env python3
"""Replay user32.m FlagsChanged events and focus cleanup without an AppKit window.
Physical modifiers left held after release / focus loss: old 1 / 1, fixed 0 / 0.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RT = Path(__file__).resolve().parent
PREAMBLE = r'''
#import <AppKit/AppKit.h>
#import <IOKit/hidsystem/IOLLEvent.h>
#include <stdio.h>
#include <string.h>

static uint8_t key_down[256], key_toggled[256], key_scan[256], di_keys[256], di_mouse_btn[8];
static unsigned messages, di_edges;
static int vk_extended(uint8_t vk) { (void)vk; return 0; }
static void di_event(uint8_t dik, int down) { di_keys[dik] = down; ++di_edges; }
static int portmenu_key(uint8_t vk, int down, int repeat) { (void)vk; (void)down; (void)repeat; return 0; }
static void prompts_input(int device) { (void)device; }
static void post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    (void)hwnd; (void)msg; (void)wp; (void)lp;
    ++messages;
}
'''
MAIN = r'''
static unsigned held(void) {
    unsigned n = 0;
    for (unsigned i = 0; i < sizeof di_keys; ++i) n += di_keys[i] != 0;
    return n;
}

int main(void) {
    flags(0x38, NSEventModifierFlagShift | NX_DEVICELSHIFTKEYMASK);
    flags(0x3C, NSEventModifierFlagShift | NX_DEVICELSHIFTKEYMASK | NX_DEVICERSHIFTKEYMASK);
    int both = di_keys[0x2A] && di_keys[0x36] && key_down[0x10] && messages == 1;
    flags(0x38, NSEventModifierFlagShift | NX_DEVICERSHIFTKEYMASK);
    int right_only = !di_keys[0x2A] && di_keys[0x36] && key_down[0x10] && messages == 1;
    flags(0x3C, 0);
    unsigned release = held();
    int shift_ok = !release && !key_down[0x10] && messages == 2 && di_edges == 4;
    memset(di_keys, 0, sizeof di_keys);

    flags(0x38, NSEventModifierFlagShift | NX_DEVICELSHIFTKEYMASK);
    flags(0x3C, NSEventModifierFlagShift | NX_DEVICELSHIFTKEYMASK | NX_DEVICERSHIFTKEYMASK);
    flags(0x3A, NSEventModifierFlagOption | NX_DEVICELALTKEYMASK);
    flags(0x3D, NSEventModifierFlagOption | NX_DEVICELALTKEYMASK | NX_DEVICERALTKEYMASK);
    flags(0x3B, NSEventModifierFlagControl | NX_DEVICELCTLKEYMASK);
    flags(0x3E, NSEventModifierFlagControl | NX_DEVICELCTLKEYMASK | NX_DEVICERCTLKEYMASK);
    int six = held() == 6;
    release_keys(1);
    unsigned focus_loss = held();
    printf("modifier keys still held: %u after Shift releases, %u after focus loss\n", release, focus_loss);
    return !(both && right_only && shift_ok && six && !focus_loss &&
             !key_down[0x10] && !key_down[0x11] && !key_down[0x12]);
}
'''


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else RT / 'user32.m'
    user = source.read_text()
    start = re.search(r'^static void key_(?:message|event)\(.*\{', user, re.M).start()
    keys = user[start:user.index('int user32_window_occluded', start)]
    table = re.search(r'^typedef struct \{ uint8_t vk, scan; \} KeyMap;\n.*?^\};', user, re.M | re.S)[0]
    flags = user.split('case NSEventTypeFlagsChanged: {', 1)[1].split('break;', 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / 'keys.m'
        exe = Path(tmp) / 'keys'
        m.write_text(PREAMBLE + table + '\n' + keys + '''
static void flags(uint16_t code, NSEventModifierFlags mask) {
    uint32_t hwnd = 1;
    struct { uint16_t keyCode; NSEventModifierFlags modifierFlags; } e = {code, mask};
''' + flags + '\n}\n' + MAIN)
        subprocess.run(['clang', '-Werror', m, '-o', exe], check=True)
        subprocess.run([exe], check=True)


if __name__ == '__main__':
    main()
