#!/usr/bin/env python3
"""Compile actual lifecycle paths against fake sinks and reject broken controls.
No real AppKit, HID, GameController or audio discovery/output is invoked.
Plan: invoke the close button with active effects; require cleanup before WM_CLOSE.
Also remove each transport safeguard in turn and require the fake checks to fail.
Proof: close cleanup PASS 1/1; original/broken callback FAIL 1/1 before WM_CLOSE.
Nine source controls are rejected: rumble detach/attach/cache/retry/epoch fail
2/10/16/14/2 of 43 checks; extended deadline fails 4/45; padfx debt/deferred
fail 17/2 of 291; feedback model fails 3/38. Fixed transports have 0 failures.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
FAKE = r'''
#import <Foundation/Foundation.h>
#include <stdint.h>
typedef NSObject NSWindow;
typedef struct { uint32_t hwnd; } Win;
static Win window = {0x10010};
static int motors, triggers, light, leds, messages, calls;
static Win *game_window(void) { return &window; }
static uint32_t hwnd_of(Win *w) { return w->hwnd; }
static void rumble_close(void) { motors=0; ++calls; }
static void padfx_close(void) { triggers=light=leds=0; ++calls; }
static void post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp) {
    if (hwnd!=window.hwnd || msg!=0x0010 || wp || lp) abort();
    ++messages;
    if (motors || triggers || light || leds || calls!=2) {
        fprintf(stderr,"FAIL: WM_CLOSE preceded cleanup (%d motor, %d trigger, %d light, %d LED; %d cleanup calls)\n",
                motors,triggers,light,leds,calls);
        exit(1);
    }
}
int user32_request_close(void);
@interface GameWindowDelegate : NSObject
@property uint32_t hwnd;
- (BOOL)windowShouldClose:(NSWindow *)sender;
@end
'''
MAIN = r'''
int main(void) { @autoreleasepool {
    GameWindowDelegate *delegate=[GameWindowDelegate new]; delegate.hwnd=window.hwnd;
    motors=triggers=light=leds=1;
    if ([delegate windowShouldClose:nil] || messages!=1 || calls!=2) return 1;
    printf("dsstops_test: PASS, cleanup precedes WM_CLOSE, 0 real opens/writes\n");
    return 0;
} }
'''


def transport_controls(tmp):
    roles = (ROOT / 'padbind.c').read_text()
    (tmp / 'padfx_roles.inc').write_text(re.search(
        r'^int padbind_trigger_role\(.*?^}', roles, re.M | re.S)[0] + '\n')
    controls = [
        ('rumble', 'detach', 'if (dev == pad) { invalidate_motors();', 'if (dev == pad) {'),
        ('rumble', 'attach', 'flush();                                           // neutral before the next feedback publication', '(void)0;'),
        ('rumble', 'cache', 'if (send(heavy, light))', 'if ((send(heavy, light), 1))'),
        ('rumble', 'retry', 'neutral_pending = 1; retry_clear();', 'neutral_pending = 1; if (0) retry_clear();'),
        ('rumble', 'deadline', 'now + 250000000ull', 'now + 2500000000ull'),
        ('rumble', 'epoch', '(request >> 32) != motor_generation', '0'),
        ('padfx', 'debt', 'if (gone && (trig_taken || light_taken))', 'if (0)'),
        ('padfx', 'deferred', 'if (is_detached(ctl)) {\n            if (owner == ctl)', 'if (0) {\n            if (owner == ctl)'),
        ('feedback', 'model', 'reset_invalid_model();', 'if (0) reset_invalid_model();'),
    ]
    for module, label, old, new in controls:
        suffix = 'c' if module == 'feedback' else 'm'
        original = (ROOT / f'{module}.{suffix}').read_text()
        assert old in original, f'{module}/{label}: mutation no longer applies'
        source = tmp / f'{module}-{label}.{suffix}'
        source.write_text(original.replace(old, new))
        binary = tmp / f'{module}-{label}'
        cmd = ['clang', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined',
               '-I', str(ROOT.parent / 'recomp'), '-I', str(ROOT), '-I', str(tmp),
               f'-D{module.upper()}_SOURCE="{source}"', str(ROOT / f'{module}_test.{suffix}')]
        if suffix == 'm':
            cmd += ['-fobjc-arc', '-framework', 'Foundation']
        if module == 'rumble':
            cmd += ['-framework', 'IOKit', '-lz']
        result = subprocess.run(cmd + ['-o', str(binary)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-3000:]
        result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and 'FAIL' in result.stdout, (label, result.stdout, result.stderr[-3000:])
        print(f'dsstops_test: broken {module}/{label} rejected: {result.stdout.strip()}')


def main():
    source = Path(os.environ.get('DSSTOPS_USER_SOURCE', ROOT / 'user32.m')).read_text()
    callback = re.search(r'- \(BOOL\)windowShouldClose:[^\n]+', source)[0]
    helper = re.search(r'int user32_request_close\(void\) \{.*?\n\}', source, re.S)[0]
    with tempfile.TemporaryDirectory(prefix='dsstops_') as tmp:
        tmp = Path(tmp)
        for broken in (False, True):
            method = callback
            if broken:
                method = '- (BOOL)windowShouldClose:(NSWindow *)sender { (void)sender; post(self.hwnd, 0x0010, 0, 0); return NO; }'
            path = tmp / 'close.m'
            path.write_text(FAKE + '\n@implementation GameWindowDelegate\n' + method +
                            '\n@end\n' + helper + MAIN)
            result = subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-g',
                                     '-fsanitize=address,undefined', '-fobjc-arc', str(path),
                                     '-framework', 'Foundation', '-o', str(tmp / 'close')],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-3000:])
            result = subprocess.run([tmp / 'close'], capture_output=True, text=True)
            if broken:
                assert result.returncode == 1 and 'WM_CLOSE preceded cleanup' in result.stderr, result.stderr
                print('dsstops_test: broken close callback rejected (1 ordering failure)')
            else:
                assert result.returncode == 0, result.stderr
                print(result.stdout.strip())
        transport_controls(tmp)


if __name__ == '__main__':
    main()
