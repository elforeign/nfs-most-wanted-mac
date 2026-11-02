#!/usr/bin/env python3
"""Execute the Cocoa key dispatch case without a window or application.
Review proof: original Command guard 13 checks/5 failures; fixed 13/0.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'runtime'

COMMAND_BASELINE = '''                if ((e.modifierFlags & NSEventModifierFlagCommand) && e.type == NSEventTypeKeyDown) break;  // Cmd-Q etc.
                KeyMap k = e.keyCode < 128 ? mac_keys[e.keyCode] : (KeyMap){0, 0};
'''
assert hashlib.sha256(COMMAND_BASELINE.encode()).hexdigest() == '528b7570b3850051356a2bd96b7b8a56bb1160b3056d204933dbfa8b6a8259e0'

PRE = r'''
#import <Cocoa/Cocoa.h>
#include "text_fit.h"
typedef struct { uint8_t vk, scan; } KeyMap;
static const KeyMap mac_keys[128] = { [51]={8,14}, [12]={'Q',16}, [9]={'V',47}, [0]={'A',30} };
static int checks, failures, editing=1, is_open=1, text_row=1, key_calls, posts, forwarded;
static char text[64];
static int command;
typedef struct { int type; } Setting;
#define SET_TEXT 1
static Setting setting;
static Setting *current(void) { setting.type=text_row; return &setting; }
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; printf("FAIL %d: %s\n", __LINE__, #x); } } while(0)
static int portmenu_key(uint8_t vk, int down, int repeat) {
    (void)repeat; ++key_calls;
    if(is_open && editing && down) text_edit_key(text, vk, command, 0);
    return is_open;
}
static void prompts_input(int p) { (void)p; }
static void key_event(uint32_t hwnd, uint8_t vk, uint8_t scan, int down, int repeat) {
    (void)hwnd; (void)scan; portmenu_key(vk,down,repeat);
}
static int portmenu_char(unsigned ch) { (void)ch; return is_open; }
static void post(uint32_t h,unsigned m,unsigned c,unsigned l) { (void)h;(void)m;(void)c;(void)l; ++posts; }
'''

MAIN = r'''
static void send_key(unsigned code, NSEventModifierFlags mods, int down, int repeat) {
    command=(mods & NSEventModifierFlagCommand)!=0;
    NSEvent *e=[NSEvent keyEventWithType:down?NSEventTypeKeyDown:NSEventTypeKeyUp
      location:NSZeroPoint modifierFlags:mods timestamp:0 windowNumber:0 context:nil
      characters:@"\x7f" charactersIgnoringModifiers:@"\x7f" isARepeat:repeat keyCode:code];
    dispatch(e);
}
int main(void) { @autoreleasepool {
    strcpy(text,"hello"); send_key(51,NSEventModifierFlagCommand,1,0);
    CHECK(!text[0]); CHECK(key_calls==1); CHECK(posts==0); CHECK(forwarded==0);
    strcpy(text,"again"); send_key(51,NSEventModifierFlagCommand,1,1); CHECK(!text[0]);
    send_key(51,NSEventModifierFlagCommand,0,0); CHECK(key_calls==3);
    for(int k=0;k<3;++k) {
        int codes[]={12,9,0}, before=key_calls;
        send_key(codes[k],NSEventModifierFlagCommand,1,0); CHECK(key_calls==before);
    }
    strcpy(text,"keep"); editing=0; int before=key_calls;
    send_key(51,NSEventModifierFlagCommand,1,0); CHECK(key_calls==before && !strcmp(text,"keep"));
    editing=1; is_open=0; send_key(51,NSEventModifierFlagCommand,1,0); CHECK(key_calls==before);
    is_open=1; text_row=0; send_key(51,NSEventModifierFlagCommand,1,0); CHECK(key_calls==before);
    text_row=1; send_key(51,0,1,0); CHECK(!strcmp(text,"kee"));
    printf("shortcut: %d checks, %d failures\n",checks,failures); return failures?1:0;
} }
'''


def run(source, helper, expected_failure=False):
    # The complete production key case, including its early Command guard and fallthrough.
    start = source.index('            case NSEventTypeKeyDown:')
    end = source.index('            case NSEventTypeFlagsChanged:', start)
    case = source[start:end]
    dispatch = 'static void dispatch(NSEvent *e) { uint32_t hwnd=0; for(int once=0;once<1;++once) { switch(e.type) {\n' + case + '\n default: break; } ++forwarded; } }\n'
    with tempfile.TemporaryDirectory(prefix='text-shortcut-') as scratch:
        path = Path(scratch) / 'check.m'
        exe = Path(scratch) / 'check'
        path.write_text(PRE + helper + dispatch + MAIN)
        c = subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fobjc-arc',
                            '-fsanitize=address,undefined', '-I'+str(R), str(path), str(R/'text_fit.c'),
                            '-framework', 'Cocoa', '-o', str(exe)], capture_output=True, text=True)
        assert c.returncode == 0, c.stderr[-4000:]
        p = subprocess.run([str(exe)], capture_output=True, text=True)
        print(p.stdout.strip())
        assert bool(p.returncode) == expected_failure, p.stderr + p.stdout


def main():
    source = (R/'user32.m').read_text()
    menu = (R/'portmenu.m').read_text()
    found = re.search(r'int portmenu_command_key\([^\n]+\) \{.*?\n\}', menu, re.S)
    helper = found.group() if found else ''
    run(source, helper)
    start = source.index('                KeyMap k =', source.index('            case NSEventTypeKeyDown:'))
    end = source.index('                key_event(hwnd,', start)
    assert 'portmenu_command_key(k.vk, e.isARepeat)' in source[start:end]
    print('original guard:', end=' ', flush=True)
    run(source[:start] + COMMAND_BASELINE + source[end:], helper, expected_failure=True)


if __name__ == '__main__':
    main()
