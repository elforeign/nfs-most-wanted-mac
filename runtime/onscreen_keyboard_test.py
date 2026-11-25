#!/usr/bin/env python3
"""Controller text entry against translated manager/filter/accept/cancel bodies.
ASan/UBSan, no game or GPU. Fixtures replace string packing (Latin-1), drawing,
allocation, FE flush and notifications; manager editing/validation are real.
PASS: 255 checks, 0 failures (256-character note, hold-repeat delete, R3 clear-all). Broken controls: limit 29/248 failures,
filter 5/248, backspace 21/248, empty 12/248, rollback 14/248,
stale 18/250, ownership 4/248, mapping 3/246, opening press 14/248,
held input 1/248, device switch 2/248, Note rollback 1/248,
hold repeat 1/255, clear all 1/255.
Physical-device tracker: 6/0 checks/failures; broken connection tracking 6/2.
Pointer hide proof: fixed 1/2/3 calls after pad/mouse/key batches of 1,000 inputs;
SHA-256-pinned original: 1,000 calls for 1,000 pad inputs.
R3 review proof: original press-to-clear 350 checks/25 failures; release-to-clear 350/0.
Note UTF-8: Backspace after Aé leaves 2 bytes in the original (partial UTF-8)
and 1 byte in the fix. Typing é again leaves the valid 3-byte Aé.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / 'runtime'

# Authentic reviewed press-to-clear fragment; only this action changes in the baseline.
R3_PRESS_BASELINE = '            if (edge & 512) osk_queue(s->generation, OSK_CLEAR, 0);          // R3: the whole field\n'
assert hashlib.sha256(R3_PRESS_BASELINE.encode()).hexdigest() == 'abb773d64d8162ee143319b2084ad9908924e2339189f94996e196a215ed2371'

# Original input handler: repeat pad input unconditionally hides the system pointer.
POINTER_BASELINE = 'void prompts_input(int pad) {              // user32.m: a key or mouse button (0), a controller button or stick (1)\n    static float moved;\n    if (pad < 0) { moved += 1; if (moved < 16) return; pad = 0; }   // mouse movement: about 16 points of it\n    moved = 0;\n    physical_input = pad;\n    if (pad == 1) user32_hide_pointer();   // F21: hidden until the mouse moves (a key press or click also hands the prompts back)\n    if (!pad) pad_lost = 0;                // keys or the mouse: the player has moved on from the lost controller\n    if (pad_active == pad) return;\n    pad_active = pad;\n    if (mode() == 2) fprintf(stderr, "boot: button prompts: %s\\n", pad ? "console" : "keyboard");\n    if (diag_on) diag_event("prompts: input from the %s", pad ? "controller" : "keyboard/mouse");\n}\n'
assert hashlib.sha256(POINTER_BASELINE.encode()).hexdigest() == '6f9af994e348051cdcf2edc1bba427a186081c320135d9657a12f94c3efc4bb6'

PRE = r'''
#include "funcs.h"
#include "host.h"
#include "onscreen_keyboard.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on;
static int checks, fails, physical_input=1, menu_open, accepted, cancelled, flushes, freed;
static uint64_t ticks;
static char notified[256], drawn[256];
static Cpu cpu(void) { return (Cpu){.esp=0x800000,.fs_base=0x700000,.cw=0x37f}; }
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL %d %s\n",__LINE__,#x); } } while(0)
int prompts_last_input(void) { return physical_input; }
void prompts_input(int p) { physical_input=p; }
int portmenu_is_open(void) { return menu_open; }
uint64_t host_ticks(void) { return ticks; }
void diag_event(const char *fmt,...) { (void)fmt; }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); exit(2); }
void abi_violation(Cpu *c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t sp) {
 (void)c;(void)b;(void)s;(void)i;(void)p;(void)sp; fprintf(stderr,"ABI %x\n",fn); exit(2);
}
void f_004611C0(Cpu *c) { unsigned d=arg(c,0),s=arg(c,1),i=0; do { M16(d+2*i)=M8(s+i); } while(M8(s+i++)); c->esp+=4; }
void f_0056BFC0(Cpu *c) { unsigned d=arg(c,0),s=arg(c,2),i=0; do { M16(d+2*i)=M8(s+i); } while(M8(s+i++)); c->esp+=16; }
void f_0056BE80(Cpu *c) { unsigned d=arg(c,0),cap=arg(c,1),s=arg(c,2),i=0; for(;i+1<cap && M16(s+2*i);++i) M8(d+i)=M16(s+2*i); M8(d+i)=0; c->esp+=16; }
void f_00460CA0(Cpu *c) { strncpy((char *)G+arg(c,0),(char *)G+arg(c,1),arg(c,2)); c->esp+=4; }
void f_00461160(Cpu *c) { unsigned s=arg(c,0),i=0; while(M16(s+2*i)) ++i; c->eax=i; c->esp+=4; }
void f_00460C50(Cpu *c) { c->eax=strlen((char *)G+arg(c,0)); c->esp+=4; }
void f_005247A0(Cpu *c) { ++flushes; c->esp+=4; }
void f_0058FB00(Cpu *c) { unsigned i=0; while(i<255 && M16(0x91D5B0+2*i)) { drawn[i]=M16(0x91D5B0+2*i); ++i; } drawn[i]=0; c->esp+=8; }
void f_00652B00(Cpu *c) { ++freed; memset(G+arg(c,0),0xDD,12); c->esp+=4; }
void f_0065D9E0(Cpu *c) { c->esp+=8; }
void f_00516C50(Cpu *c) { c->esp+=16; }
void dispatch(Cpu *c,uint32_t t) {
 CHECK(t==0x123456); unsigned msg=arg(c,0);
 CHECK(msg==0xDA5B8712 || msg==0xC9D30688);
 if(msg==0xDA5B8712) ++accepted; else ++cancelled;
 strcpy(notified,drawn); c->esp+=20;
}
void f_00516520_orig(Cpu *c) { c->esp+=20; }
void osk_pass(Cpu *c);
'''

MAIN = r'''
static void pass(void) { Cpu c=cpu(); osk_pass(&c); }
static void begin(unsigned type,unsigned limit,const char *initial) {
 strcpy((char *)G+0x210000,initial);
 M32(0x200000)=0x220000; M32(0x200004)=0x230000;
 M32(0x230000)=0x240000; M32(0x230024)=0x200000; M32(0x240004)=0x123456;
 Cpu c=cpu(); c.ecx=KM;
 M32(c.esp+4)=0x200000; M32(c.esp+8)=type; M32(c.esp+12)=0x210000; M32(c.esp+16)=limit;
 Cpu original=c; f_0057F1D0(&c);
 CHECK(c.esp==original.esp+20);
 pass();
}
static void event(int action,unsigned ch) { osk_queue(osk_capture()->generation,action,ch); pass(); }
static void tap(unsigned buttons) {
 ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0);
 ticks+=16666667; osk_pad(0,buttons,0,0,0,0,0,0,1,0); pass();
 ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0);
}
static void assert_text(const char *s) {
 CHECK(osk_capture()->length==strlen(s)); CHECK(!strcmp(osk_capture()->text,s));
}
int main(int argc,char **argv) {
 (void)argc;(void)argv; G=calloc(1,0x1000000); CHECK(G!=NULL);
 M32(0x91cad0)=0x250000; M32(0x91cf90)=0x260000; M8(0x26000c)=0; M8(0x26000d)=1;
 osk_pad(0,0,0,0,0,0,0,0,1,0); osk_pad(1,0,0,0,0,0,0,0,1,1);
 // Alias limits, guest character rejection, backspace and empty acceptance.
 begin(6,7,""); CHECK(osk_visible()); CHECK(osk_capture()->limit==7);
 int a=accepted; event(OSK_ACCEPT,0); CHECK(accepted==a); CHECK(osk_capture()->active);
 for(const char *p="PADTESTEXTRA";*p;++p) event(OSK_TYPE,*p);
 assert_text("PADTEST"); event(OSK_DELETE,0); assert_text("PADTES");
 for(const char *p=" @._-!";*p;++p) event(OSK_TYPE,*p);
 assert_text("PADTES"); event(OSK_TYPE,'T'); event(OSK_ACCEPT,0);
 CHECK(accepted==a+1); CHECK(!strcmp(notified,"PADTEST")); CHECK(!osk_capture()->active);
 // Guest cancel notification sees the original, not partially edited, value.
 begin(6,7,"OLD"); event(OSK_DELETE,0); event(OSK_TYPE,'X'); assert_text("OLX");
 event(OSK_CANCEL,0); CHECK(cancelled==1); CHECK(!strcmp(notified,"OLD")); CHECK(!osk_capture()->active);
 // The opening press and held input cannot type or leak through after a capture.
 begin(6,7,""); osk_pad(0,1,0,0,0,0,0,0,1,0); pass(); assert_text("");
 for(int i=0;i<10;++i) { osk_pad(0,1,0,0,0,0,0,0,1,0); pass(); }
 assert_text(""); osk_pad(0,0,0,0,0,0,0,0,1,0);
 osk_pad(0,1,0,0,0,0,0,0,1,0); pass(); assert_text("1");
 CHECK(!osk_pad(0,8192 | OSK_MENU_SHORTCUT,0,0,0,0,0,0,1,0));
 osk_pad(0,0,0,0,0,0,0,0,1,0); event(OSK_CANCEL,0);
 // Limits used by the shared widgets, and the hard StartCapture clamp.
 const unsigned limits[]={7,12,16,20,32,50,95,115,116,155,999};
 for(unsigned i=0;i<sizeof limits/sizeof *limits;++i) {
  begin(0,limits[i],""); unsigned cap=limits[i]>155?155:limits[i]; CHECK(osk_capture()->limit==cap);
  for(unsigned j=0;j<cap+3;++j) event(OSK_TYPE,'A');
  CHECK(osk_capture()->length==cap); event(OSK_DELETE,0); CHECK(osk_capture()->length==cap-1);
  event(OSK_CANCEL,0);
 }
 // The translated guest filter determines pages; password snapshots are masked.
 begin(4,50,""); event(OSK_TYPE,'@'); event(OSK_TYPE,'_'); event(OSK_TYPE,' '); assert_text("@_");
 CHECK(osk_capture()->allowed['@'] && !osk_capture()->allowed[' ']);
 event(OSK_CANCEL,0); begin(5,16,""); event(OSK_TYPE,'s'); event(OSK_TYPE,'e'); assert_text("**"); event(OSK_CANCEL,0);
 // A queued edit cannot survive capture replacement, even at the same address.
 begin(6,7,""); uint64_t old=osk_capture()->generation;
 osk_queue(old,OSK_TYPE,'X'); begin(6,7,""); assert_text("");
 osk_queue(old,OSK_ACCEPT,0); pass(); CHECK(osk_capture()->active);
 osk_queue(osk_capture()->generation,OSK_TYPE,'X'); physical_input=0; pass(); assert_text("");
 physical_input=1;
 // Destruction retires the generation before the allocation can be reused.
 old=osk_capture()->generation; Cpu c=cpu(); c.ecx=0x230000; f_00586580(&c);
 CHECK(!osk_capture()->active); CHECK(freed>0); begin(6,7,"");
 osk_queue(old,OSK_TYPE,'X'); pass(); assert_text("");
 // Physical input, opening press, held controls, mapping, and no Start accept.
 physical_input=0; CHECK(!osk_visible()); tap(1); CHECK(osk_visible()); assert_text("");
 tap(1); assert_text("1"); tap(2); assert_text("");
 tap(128); CHECK(osk_capture()->active); tap(4); CHECK(osk_capture()->active);
 tap(1); osk_pad(0,4,0,0,0,0,0,0,1,0); pass(); CHECK(!osk_capture()->active); CHECK(!strcmp(notified,"1"));
 CHECK(osk_pad(0,4,0,0,0,0,0,0,1,0));
 CHECK(!osk_pad(0,0,0,0,0,0,0,0,1,0));
 // Note adapter has 95 bytes, permits spaces, rolls back, and rejects stale events.
 char text[96]="prior"; menu_open=1; osk_note_begin(text,sizeof text,0); pass();
 CHECK(osk_visible()); CHECK(osk_capture()->limit==95);
 event(OSK_TYPE,' '); event(OSK_TYPE,'X'); CHECK(!strcmp(text,"prior X"));
 event(OSK_CANCEL,0); CHECK(!strcmp(text,"prior")); CHECK(!osk_note_active());
 osk_note_begin(text,sizeof text,0); for(unsigned i=0;i<100;++i) event(OSK_TYPE,'A');
 CHECK(strlen(text)==95); event(OSK_DELETE,0); CHECK(strlen(text)==94);
 old=osk_capture()->generation; osk_queue(old,OSK_TYPE,'X'); osk_note_end(0);
 osk_note_begin(text,sizeof text,0); pass(); CHECK(strlen(text)==94);
 event(OSK_ACCEPT,0); CHECK(!osk_note_active()); CHECK(strlen(text)==94); menu_open=0;
 strcpy(text,"A\xc3\xa9");osk_note_begin(text,sizeof text,0);
 CHECK(!strcmp(osk_capture()->text,text));
 event(OSK_DELETE,0);
 unsigned after=strlen(text);
 CHECK(after==1 && !strcmp(text,"A"));
 event(OSK_TYPE,0xe9);
 unsigned inserted=strlen(text);
 CHECK(inserted==3 && !strcmp(text,"A\xc3\xa9") && !strcmp(osk_capture()->text,text));
 printf("note UTF-8: bytes after delete=%u after insert=%u\n",after,inserted);
 osk_note_end(0);
 // A 256-character note: holding Circle repeats delete (once at the press, then after 0.4 s at 20 per second); R3 clears the field.
 char big[257]=""; osk_pad(0,0,0,0,0,0,0,0,1,0); osk_note_begin(big,sizeof big,0); pass(); CHECK(osk_capture()->limit==256);
 for(unsigned i=0;i<240;++i) event(OSK_TYPE,'A');
 CHECK(strlen(big)==240);
 for(unsigned i=0;i<20;++i) { ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0); pass(); }
 ticks+=16666667; osk_pad(0,2,0,0,0,0,0,0,1,0); pass(); CHECK(strlen(big)==239);
 for(unsigned i=0;i<18;++i) { ticks+=16666667; osk_pad(0,2,0,0,0,0,0,0,1,0); pass(); }   // 0.30 s held
 CHECK(strlen(big)==239);
 for(unsigned i=0;i<42;++i) { ticks+=16666667; osk_pad(0,2,0,0,0,0,0,0,1,0); pass(); }   // 1.0 s held
 CHECK(strlen(big)<=239-11 && strlen(big)>=239-15);
 ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0); pass(); size_t kept=strlen(big);
 for(unsigned i=0;i<10;++i) { ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0); pass(); }
 CHECK(strlen(big)==kept);                                                                   // released: no more deleting
 ticks+=16666667; osk_pad(0,512,0,0,0,0,0,0,1,0); pass(); CHECK(strlen(big)==kept);
 ticks+=16666667; osk_pad(0,0,0,0,0,0,0,0,1,0); pass(); CHECK(big[0]==0);
 event(OSK_CANCEL,0); menu_open=0;
 // R3 clear is committed on release, and cancelled if L3 joins the menu chord.
 // Exercise both adapters, both press orders, simultaneous presses and both release orders.
 for(int adapter=0;adapter<2;++adapter) for(int order=0;order<3;++order) for(int release=0;release<2;++release) {
  osk_pad(0,0,0,0,0,0,0,0,1,0);
  if(adapter) { strcpy(big,"KEEP"); menu_open=1; osk_note_begin(big,sizeof big,0); pass(); }
  else begin(6,7,"KEEP");
  osk_pad(0,0,0,0,0,0,0,0,1,0);
  if(order<2) { osk_pad(0,order?256:512,0,0,0,0,0,0,1,0); pass(); assert_text("KEEP"); }
  CHECK(!osk_pad(0,256|512|OSK_MENU_SHORTCUT,0,0,0,0,0,0,1,0)); pass(); assert_text("KEEP");
  // Also test while the menu suspends the guest capture.
  if(!adapter) menu_open=1;
  osk_pad(0,release?256:512,0,0,0,0,0,0,1,0); pass();
  osk_pad(0,0,0,0,0,0,0,0,1,0); pass();
  if(!adapter) menu_open=0;
  pass(); assert_text("KEEP"); event(OSK_CANCEL,0); menu_open=0;
 }
 // Guest suspension resumes only the still-live generation.
 begin(6,7,"P"); old=osk_capture()->generation; menu_open=1;
 osk_note_begin(text,sizeof text,0); event(OSK_TYPE,'B'); CHECK(M32(KM+0x300)==1);
 osk_note_end(0); menu_open=0; pass(); CHECK(osk_capture()->generation==old); assert_text("P");
 // Both pads are suppressed, but only the latched pad can edit or finish.
 osk_pad(0,0,0,0,0,0,0,0,1,0);
 for(unsigned b=1;b<=8;b*=2) {
  osk_pad(1,0,0,0,0,0,0,0,1,1); CHECK(osk_pad(1,b,0,0,0,0,0,0,1,1)); pass(); assert_text("P");
 }
 osk_pad(0,0,0,0,0,0,0,0,0,0); CHECK(!osk_visible());
 osk_pad(1,8,0,0,0,0,0,0,1,1); pass(); CHECK(osk_capture()->active); CHECK(osk_capture()->owner==0);
 osk_pad(0,0,0,0,0,0,0,0,1,0); tap(8); CHECK(!osk_capture()->active);
 // FE mask latches physical port 1 and resolves its player database assignment.
 c=cpu(); M32(c.esp+12)=2; f_00516520(&c); begin(6,7,"");
 CHECK(osk_capture()->owner==1 && osk_capture()->player==1); CHECK(osk_xbox());
 osk_pad(1,0,0,0,0,0,0,0,1,1); osk_pad(1,1,0,0,0,0,0,0,1,1); pass(); assert_text("1");
 // Layout keys, case, filter-aware symbols, and direction repeat.
 osk_toggle(0); osk_navigation(0,1); char label[16];
 CHECK(!strcmp(osk_key_label(osk_key(1,0),label),"q"));
 CHECK(!osk_key_enabled(OSK_SPACE) && !osk_key_enabled(OSK_SYMBOLS));
 event(OSK_CANCEL,0); begin(0,50,""); CHECK(osk_key_enabled(OSK_SYMBOLS));
 osk_toggle(1); for(int r=0;r<4;++r) for(int k=0;k<osk_key_count(r);++k) CHECK(osk_capture()->allowed[osk_key(r,k)]);
 printf("osk: %d checks, %d failures\n",checks,fails); free(G); return fails?1:0;
}
'''


def translated(name, chunk, original=False):
    path = ROOT / f'recomp/out/speed_{chunk}.c'
    if not path.exists():
        print('SKIP: needs translated guest output'); raise SystemExit(3)
    source = path.read_text()
    body = re.search(r'void f_' + name + r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', source, re.S).group()
    return re.sub(r'void f_' + name + r'(?:_orig)?\(', 'void f_' + name + ('_orig' if original else '') + '(', body, count=1)


def main():
    # Execute the physical-device tracker itself, including connection-only changes.
    prompts = (R / 'padprompts.c').read_text()
    tracker = '\n'.join(re.search(r'(?:static )?void ' + name + r'\([^\n]*\) \{.*?\n\}', prompts, re.S).group()
                        for name in ('prompts_pointer_visible', 'prompts_activity', 'prompts_input', 'prompts_mouse_moved', 'prompts_pad_seen'))
    tracker_pre = r'''
    #include <stdio.h>
    #include <stdatomic.h>
    static _Atomic int physical_input, pad_connected;
    static int pointer_hidden, pad_active=-1, pad_lost, failures, hide_calls;
    static int diag_on;
    static int mode(void) { return 2; }
    static void user32_hide_pointer(void) { ++hide_calls; }
    static void diag_event(const char *s,...) { (void)s; }
    #define CHECK(x) do { if(!(x)) ++failures; } while(0)
    '''
    tracker_main = r'''
    int main(void) {
      prompts_pad_seen(1); CHECK(physical_input==0 && pad_active==1);
      prompts_input(1); CHECK(physical_input==1);
      prompts_pad_seen(0); CHECK(physical_input==1 && pad_lost);
      prompts_input(0); CHECK(physical_input==0);
      prompts_input(1); for(int i=0;i<15;++i) prompts_input(-1); CHECK(physical_input==1);
      prompts_input(-1); CHECK(physical_input==0);
      printf("physical input: 6 checks, %d failures\n",failures); return failures?1:0;
    }
    '''
    with tempfile.TemporaryDirectory(prefix='osk-physical-') as d:
        for broken in (False, True):
            src = Path(d) / 'physical.c'; exe = Path(d) / 'physical'
            src.write_text(tracker_pre + (tracker.replace('prompts_activity(1, 0)', 'prompts_activity(1, 1)').replace('prompts_activity(0, 0)', 'prompts_activity(0, 1)') if broken else tracker) + tracker_main)
            subprocess.run(['clang', '-std=c11', str(src), '-o', str(exe)], check=True, capture_output=True)
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            print(('broken tracker: ' if broken else '') + p.stdout.strip())
            assert p.returncode == int(broken), p.stderr
    # Pointer visibility is independent of the 16-point prompt selection threshold.
    pointer_main = r'''
    int main(void) {
      for(int i=0;i<1000;++i) prompts_input(1);
      int pad_calls=hide_calls; CHECK(pad_calls==1);
      prompts_mouse_moved(1); CHECK(pad_active==1 && physical_input==1);
      for(int i=0;i<1000;++i) prompts_input(1);
      int mouse_calls=hide_calls; CHECK(mouse_calls==2);
      prompts_input(0);
      for(int i=0;i<1000;++i) prompts_input(1);
      int key_calls=hide_calls; CHECK(key_calls==3);
      printf("pointer hide: pad=%d mouse=%d key=%d, %d failures\n",
             pad_calls,mouse_calls,key_calls,failures);
      // A click uses the same keyboard/mouse notification as a key press.
      prompts_input(0); prompts_input(1); CHECK(hide_calls==4);
      prompts_mouse_moved(0); prompts_input(1); CHECK(hide_calls==4);
      prompts_mouse_moved(0.25f); prompts_input(1); CHECK(hide_calls==5);
      prompts_input(-1); prompts_input(1); CHECK(hide_calls==6);
      // Focus loss/return and the menu arrow invalidate without changing prompts.
      for(int event=0;event<3;++event) {
        prompts_pointer_visible(); CHECK(pad_active==1 && physical_input==1);
        for(int i=0;i<1000;++i) prompts_input(1);
        CHECK(hide_calls==7+event);
      }
      printf("pointer invalidation: %d failures\n",failures);
      return failures?1:0;
    }
    '''
    with tempfile.TemporaryDirectory(prefix='osk-pointer-') as d:
        original = re.search(r'void prompts_input\([^\n]*\) \{.*?\n\}', tracker, re.S).group() + '\n'
        for broken in (False, True):
            src = Path(d) / 'pointer.c'; exe = Path(d) / 'pointer'
            body = tracker.replace(original, POINTER_BASELINE) if broken else tracker
            assert not broken or body != tracker
            src.write_text(tracker_pre + body + pointer_main)
            subprocess.run(['clang', '-std=c11', '-fsanitize=address,undefined', str(src), '-o', str(exe)],
                           check=True, capture_output=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            print(('original pointer: ' if broken else '') + result.stdout.strip())
            assert result.returncode == int(broken), result.stderr
            assert ('pad=1000 mouse=2000 key=3000' if broken else 'pad=1 mouse=2 key=3') in result.stdout

    # Actual Cocoa hooks must invalidate even input consumed by a menu/editor.
    user32 = (R / 'user32.m').read_text()
    for hook in ('windowDidBecomeKey:', 'windowDidResignKey:', 'void user32_menu_cursor('):
        body = user32.split(hook, 1)[1].split('{', 1)[1]
        body = re.sub(r'//[^\n]*', '', body)
        assert body.lstrip().startswith('prompts_pointer_visible();'), hook
    invalidation = user32.split('// Even input consumed by an editor/menu', 1)[1].split('if(stream_key_route_event(e))', 1)[0]
    for event in ('MouseMoved', 'LeftMouseDragged', 'RightMouseDragged', 'OtherMouseDragged',
                  'KeyDown', 'LeftMouseDown', 'RightMouseDown', 'OtherMouseDown'):
        assert 'NSEventType' + event in invalidation, event
    assert 'prompts_pointer_visible();' in invalidation
    assert 'if (headless > 0) return;' in user32.split('void user32_hide_pointer(void) {', 1)[1].split('\n}', 1)[0]
    bodies = '\n'.join(translated(n, c, o) for n, c, o in [
        ('0057F1D0', 21, True), ('00586580', 21, True), ('00572FF0', 21, False),
        ('0056C820', 20, False), ('0056C7C0', 20, False), ('0056C7F0', 20, False),
        ('005A8700', 22, False), ('005A3790', 22, False), ('00599100', 22, False), ('005991B0', 22, False)])
    core = (R / 'onscreen_keyboard.c').read_text()
    old_note = core.replace('memcpy(note.text, note_text, note.length + 1);',
                            'size_t n = 0; note.text[0] = 0;\n'
                            '    for (unsigned i = 0; i < note.length; ++i) utf8(note.text, &n, (uint8_t)note_text[i]);')
    old_note = old_note.replace('text_append(note_text, note_capacity, e.ch);',
                                '{ size_t n = strlen(note_text); if (n < note.limit) { note_text[n] = (char)e.ch; note_text[n + 1] = 0; } }')
    old_note = old_note.replace('text_edit_key(note_text, 0x08, 0, 0);',
                                '{ size_t n = strlen(note_text); if (n) note_text[n - 1] = 0; }')
    variants = {
        'good': (core, bodies),
        'limit': (core, bodies.replace('if ((sf == of)) goto L_005A37D1;', 'if (0) goto L_005A37D1;')),
        'filter': (core, bodies.replace('f_0056C820(c);', 'c->eax=1; c->esp+=8;')),
        'backspace': (core.replace('e.action == OSK_DELETE || e.action == OSK_CLEAR ? 8 : e.ch', 'e.action == OSK_DELETE || e.action == OSK_CLEAR ? 0 : e.ch'), bodies),
        'empty': (core, bodies.replace('if (zf) goto L_005991A1;', 'if (0) goto L_005991A1;')),
        'rollback': (core, bodies.replace('f_004611C0(c);', 'c->esp+=4;')),
        'stale': (core.replace('e.generation != s->generation', '0'), bodies),
        'ownership': (core.replace('port == s->owner && connected', 'connected'), bodies),
        'mapping': (core.replace('edge & 4) osk_queue(s->generation, OSK_ACCEPT', 'edge & 128) osk_queue(s->generation, OSK_ACCEPT'), bodies),
        'opening': (core.replace('armed[port] = 0; }', 'armed[port] = 1; }'), bodies),
        'held': (core.replace('else if (held[port]) taking = 1;', 'else if (held[port]) taking = 0;'), bodies),
        'device switch': (core.replace('prompts_last_input() != 1) continue;', '0) continue;'), bodies),
        'hold repeat': (core.replace('text_delete_repeat(&delete_at[port], (buttons & 2) != 0, now)', '(edge & 2) != 0'), bodies),
        'clear all': (core.replace('edge & 512', 'edge & 1024'), bodies),
        'note rollback': (core.replace('if (cancel) memcpy(note_text, note_original, note_capacity);', '(void)cancel;'), bodies),
        'old note bytes': (old_note, bodies),
    }
    start = core.index('            // R3 alone clears on release;')
    end = core.index('            if (edge & 4)', start)
    variants['r3 press baseline'] = (core[:start] + R3_PRESS_BASELINE + core[end:], bodies)
    with tempfile.TemporaryDirectory(prefix='osk-test-') as d:
        for name, (bridge, guest) in variants.items():
            if name != 'good' and bridge == core and guest == bodies:
                raise AssertionError(f'{name}: mutant did not change code')
            source = Path(d) / 'test.c'; exe = Path(d) / 'test'
            source.write_text(PRE + guest + '\n' + bridge + MAIN)
            result = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                '-I', str(R), '-I', str(ROOT/'recomp'), '-I', str(ROOT/'recomp/out'), str(source), str(R/'text_fit.c'), '-o', str(exe)], capture_output=True, text=True)
            if result.returncode:
                print(result.stderr[-5000:]); raise SystemExit(1)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            lines = result.stdout.splitlines()
            print(name + ': ' + (lines[-1] if lines else result.stderr[-150:]))
            if name in ('good', 'old note bytes'):
                print(next(line for line in lines if line.startswith('note UTF-8:')))
            if (name == 'good') != (result.returncode == 0):
                print('\n'.join(lines[:20])); print(result.stderr[-1500:]); raise SystemExit(1)


if __name__ == '__main__':
    main()
