#!/usr/bin/env python3
"""Career collision scan over the production GameAction record accessor.
76 rows x 4 slots x 3 checks = 912/912: matching key reserved, other key
unreserved, joystick button unreserved. Wrong key field, 19-row scan and
three-slot scan fail 304/228/76 checks respectively (fixed: 0 failed).
Game menu priority: original 5/6 states fail (10 saves, 5 swallowed keys/chords);
fixed 0/6 fail, with driving shortcuts still active.
Production BOOT_KEYS: F6/F7/F8 deliver 6 physical down/up events, 0 bad;
wrong F8 virtual key produces 2/6 bad events and exits 1.
"""
from pathlib import Path
import subprocess
import tempfile

RT=Path(__file__).resolve().parent
HARNESS=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
enum { GA=0x1000, GA_N=76, GA_PAGE=19, T_KEY=1 };
static uint32_t table[76*13];
#define M32(a) table[((a)-GA)/4]
@PRODUCTION@
static unsigned checks;
static void check(int ok) { ++checks; if(!ok) { fprintf(stderr,"FAIL binding scan at check %u\n",checks); } }
int main(void) {
    unsigned failed=0;
    for(unsigned row=0;row<76;++row) for(unsigned slot=0;slot<4;++slot) {
        memset(table,0,sizeof(table));
        unsigned offset=row*13+1+slot*3;
        table[offset]=1; table[offset+1]=0x42; table[offset+2]=0x99;
        int found=replay_key_in_use(0x42);
        check(found); if(!found) ++failed;
        int other=replay_key_in_use(0x41); check(!other); if(other) ++failed;
        table[offset]=2;
        int joystick=replay_key_in_use(0x42); check(!joystick); if(joystick) ++failed;
    }
    printf("career bindings: %u checks, %u failed\n",checks,failed);
    return failed!=0;
}
'''
KEY_HARNESS=r'''
#include <stdint.h>
#include <stdio.h>
#include <ctype.h>
#include <string.h>
static struct { uint8_t vk,scan; } mac_keys[128];
static uint32_t focus_hwnd;
static int diag_on, count, bad, characters;
static void key_event(uint32_t hwnd,uint8_t vk,uint8_t scan,int down,int repeat) {
    (void)hwnd;
    if(vk!=0x75+count/2 || scan!=0x40+count/2 || down!=!(count%2) || repeat) ++bad;
    ++count;
}
static int portmenu_char(unsigned c) { (void)c; ++characters; return 0; }
static void post(uint32_t h,unsigned m,unsigned c,unsigned l) { (void)h;(void)m;(void)c;(void)l; }
static void diag_event(const char *fmt,...) { (void)fmt; }
void diag_scripted_key(uint64_t frame) { (void)frame; }
@PRESS@
int main(void) {
    for(unsigned i=0;i<3;++i) {
        const char *keys[]={"F6","F7","F8"};
        press(keys[i],1,1); press(keys[i],0,5);
    }
    printf("replay BOOT_KEYS: %d events, %d bad, %d characters\n",count,bad,characters);
    return count!=6 || bad || characters;
}
'''


MENU_HARNESS=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include "replay_input.h"
static unsigned char memory[0xa00000];
#define M32(a) (*(uint32_t *)(memory+(a)))
#define M8(a) memory[(a)]
#define GAMEFLOW_STATE 0x00925E90u
static void *gptr(uint32_t a) { return memory+a; }
typedef int GuideShortcut;
static int is_open,saves;
static uint8_t swallowed[256];
static int stream_key_editing(void) { return 0; }
static ReplayInput replayInput;
static int settings_get(const char *s) { return !strcmp(s,"controls.replay_key_enabled") || !strcmp(s,"controls.replay_pad"); }
int replay_key_in_use(unsigned scan) { (void)scan; return 0; }
static void replay_save_action(int unused) { (void)unused; ++saves; }
static double host_ticks(void) { return 1e9; }
@HELPER@
@KEY@
@PAD@
int main(void) {
    unsigned failed=0;
    for(unsigned state=0;state<6;++state) {
        memset(&replayInput,0,sizeof replayInput); memset(memory,0,sizeof memory); saves=0;
        M32(GAMEFLOW_STATE)=state==0?3:6;
        M32(0x009885E0u)=0x1000; M32(0x102c)=state==1?4:0;
        M32(0x0091CADCu)=0x2000; M32(0x2000)=0x3000; M32(0x30e4)=0x4000;
        M32(0x4020)=1; M32(0x400c)=0x5000;
        strcpy(gptr(0x5000),state==2?"Options.fng":"HUD_Race.fng");
        if(state==3 || state==4) {
            M32(0x00920DE8u)=0x6000; M8(0x783c)=1; M32(0x7840)=state==3?1:3;
        }
        int taken=portmenu_key(0x77,1,0); taken+=portmenu_key(0x77,0,0);
        uint32_t inject=0;
        uint32_t hidden=portmenu_pad(16|32|256,0,0,0,0,0,0,0,0,0,&inject);
        int ok=state==5?(taken==2 && saves==2 && hidden==304 && !inject):(!taken && !saves && !hidden && !inject);
        failed+=!ok;
        printf("game state %u: key=%d saves=%d hidden=%u inject=%u\n",state,taken,saves,hidden,inject);
    }
    printf("game menu priority: 6 states, %u failed\n",failed);
    return failed!=0;
}
'''


def menu_check(d, broken=False):
    source=(RT/'portmenu.m').read_text()
    helper=''
    if 'static int replay_game_blocked(' in source:
        start=source.index('static int replay_game_blocked(')
        helper=source[start:source.index('\n}\n',start)+3]
    start=source.index('int portmenu_key(')
    key=source[start:source.index('    if (vk == 0x79)',start)]+'    return 0;\n}\n'
    start=source.index('uint32_t portmenu_pad(')
    pad=source[start:source.index('    // Consume the chord',start)]+'    (void)last_phys; (void)hold_after_close; *inject=replayPad.inject; return replayPad.hide;\n}\n'
    if broken:
        key=key.replace('replay_game_blocked()', 'is_open')
        pad=pad.replace('replay_game_blocked()', 'is_open')
        helper=''
    code=MENU_HARNESS.replace('@HELPER@',helper).replace('@KEY@',key).replace('@PAD@',pad)
    path=d/'menus.c'; path.write_text(code); exe=d/'menus'
    built=subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-variable','-I'+str(RT),
                          str(path),str(RT/'replay_input.c'),'-o',str(exe)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr[-2000:]
    result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
    print(result.stdout,flush=True)
    assert result.returncode==(1 if broken else 0),result.stderr


def main():
    source=(RT/'replay_bindings.c').read_text()
    start=source.index('static uint32_t rec(')
    production=source[start:]
    variants=[('fixed',production,0),
              ('field',production.replace('M32(r+4)==scan','M32(r+8)==scan'),1),
              ('rows',production.replace('a<GA_N','a<GA_PAGE'),1),
              ('slots',production.replace('slot<4','slot<3'),1)]
    with tempfile.TemporaryDirectory() as scratch:
        d=Path(scratch)
        for name,code,expected in variants:
            path=d/(name+'.c'); path.write_text(HARNESS.replace('@PRODUCTION@',code))
            exe=d/name
            built=subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                                  str(path),'-o',str(exe)],capture_output=True,text=True)
            assert built.returncode==0,built.stderr[-2000:]
            result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            print(name+': '+result.stdout.strip(),flush=True)
            assert result.returncode==expected,(result.returncode,result.stderr[-2000:])
        menu_check(d)
        menu_check(d,broken=True)
        user=(RT/'user32.m').read_text()
        start=user.index('static void press(')
        press=user[start:user.index('\n}\n',start)+3]
        for name,code,expected in [('keys',press,0),('bad-f8',press.replace("0x75+name[1]-'6'","0x75+name[1]-'6'+(name[1]=='8')"),1)]:
            path=d/(name+'.c'); path.write_text(KEY_HARNESS.replace('@PRESS@',code))
            exe=d/name
            built=subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror',str(path),'-o',str(exe)],capture_output=True,text=True)
            assert built.returncode==0,built.stderr[-2000:]
            result=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            print(name+': '+result.stdout.strip(),flush=True)
            assert result.returncode==expected,(result.returncode,result.stderr[-2000:])
    print('replay_bindings_test PASS: 912 binding checks, 6 physical key events; 5/5 broken controls rejected')


if __name__=='__main__': main()
