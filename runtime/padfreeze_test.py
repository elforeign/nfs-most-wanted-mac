#!/usr/bin/env python3
"""Controller hot-plug through the production pad read (user32.m, padtwo_test.py's fakes), macOS only.

NFS_PADS=1: a second pad at rest never takes over, pressing on it does, the pad in use reconnecting after a
Bluetooth drop (listed last) gets the game back when pressed. The pad in use dropping, and the Mac going to sleep
or waking, ask the settings menu to freeze the race once (portmenu_open_if_racing); NFS_PAD_LOST_PAUSE=0 never
does, simulated pads only with NFS_PAD_LOST_PAUSE=1. NFS_PADS=2: a slot's pad dropping freezes once. Deliberately broken variants (the old
first-listed selection, each freeze call removed) must be rejected.
Proof: fixed 27/27 + 6/6; old production 9/27 + 1/6 failures (declaration-only sleep flag shim).
Broken selection/loss/sleep/slot calls fail 7/27, 3/27, 1/27, 1/6 respectively.
Real AppKit notifications/menu guard: 5/5; removed race guard/sleep observer/wake observer fail 2/5, 1/5, 1/5.
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import tempfile

R = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('padtwo_harness', R / 'padtwo_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

MAIN = r'''
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static GCControllerButtonInput *button(float v) {
    GCControllerButtonInput *b = [GCControllerButtonInput new]; b.value = v; b.pressed = v > .9f; return b;
}
static GCController *pad(int xbox) {                // every input at rest
    GCController *ctl = [GCController new];
    GCExtendedGamepad *g = xbox ? [GCExtendedGamepad new] : [GCDualShockGamepad new];
    ctl.extendedGamepad = g; ctl.vendorName = xbox ? @"Xbox" : @"DualShock 4"; ctl.productCategory = ctl.vendorName;
    g.leftThumbstick = [GCControllerDirectionPad new]; g.rightThumbstick = [GCControllerDirectionPad new];
    g.dpad = [GCControllerDirectionPad new];
    g.leftThumbstick.xAxis = button(0); g.leftThumbstick.yAxis = button(0);
    g.leftTrigger = button(0); g.rightTrigger = button(0); g.buttonA = button(0);
    return ctl;
}
static void notify(NSString *name, GCController *ctl) {
    [NSNotificationCenter.defaultCenter postNotificationName:name object:ctl];
}
int main(int argc, const char **argv) { @autoreleasepool {
    (void)argc; int count = atoi(argv[1]);
    (void)real_opens; (void)real_writes;
    G = calloc(1, 0x1000000);
    GCController *ds = pad(0), *xb = pad(1);
    PadState ps;
    int f;
    freeze_open = 1;
    CHECK(pad_count() == count);
    if (count == 2) {
        fake_source = @[ds, xb]; notify(GCControllerDidConnectNotification, ds); notify(GCControllerDidConnectNotification, xb);
        CHECK(pad_read_n(0, &ps) && pad_read_n(1, &ps));
        f = freeze_calls;
        fake_source = @[xb]; notify(GCControllerDidDisconnectNotification, ds);
        CHECK(!pad_read_n(0, &ps)); CHECK(!pad_read_n(0, &ps)); CHECK(pad_read_n(1, &ps));
        CHECK(freeze_calls == f + 1 && freeze_why && strstr(freeze_why, "controller 1"));
        printf("padfreeze_test NFS_PADS=2: %d checks, %d failures\n", checks, fails);
        return fails ? 1 : 0;
    }
    fake_source = @[ds]; notify(GCControllerDidConnectNotification, ds);
    CHECK(pad_read(&ps) && ps.native);
    // An Xbox pad switched on and left at rest, listed first: the DualShock stays in use.
    fake_source = @[xb, ds]; notify(GCControllerDidConnectNotification, xb);
    CHECK(pad_read(&ps) && ps.native);
    // The player picks it up and presses A: it takes over, and keeps the game once released.
    xb.extendedGamepad.buttonA = button(1); CHECK(pad_read(&ps) && !ps.native);
    xb.extendedGamepad.buttonA = button(0); CHECK(pad_read(&ps) && !ps.native);
    // Back to the DualShock by pressing Cross on it.
    ds.extendedGamepad.buttonA = button(1); CHECK(pad_read(&ps) && ps.native);
    ds.extendedGamepad.buttonA = button(0); CHECK(pad_read(&ps) && ps.native);
    // Its Bluetooth drops mid-race with the Xbox pad still on: the Xbox pad is read, the race frozen once.
    f = freeze_calls;
    fake_source = @[xb]; notify(GCControllerDidDisconnectNotification, ds);
    CHECK(pad_read(&ps) && !ps.native);
    CHECK(freeze_calls == f + 1 && freeze_why && strstr(freeze_why, "disconnected"));
    CHECK(pad_read(&ps) && freeze_calls == f + 1);
    // It reconnects, listed last: nothing pressed yet keeps the Xbox pad; Cross on it brings the game back.
    fake_source = @[xb, ds]; notify(GCControllerDidConnectNotification, ds);
    CHECK(pad_read(&ps) && !ps.native);
    ds.extendedGamepad.buttonA = button(1); CHECK(pad_read(&ps) && ps.native);
    ds.extendedGamepad.buttonA = button(0);
    // A pad that isn't in use going away freezes nothing; the only pad going away freezes once, not every read.
    f = freeze_calls;
    fake_source = @[ds]; notify(GCControllerDidDisconnectNotification, xb);
    CHECK(pad_read(&ps) && ps.native && freeze_calls == f);
    fake_source = @[]; notify(GCControllerDidDisconnectNotification, ds);
    CHECK(!pad_read(&ps)); CHECK(!pad_read(&ps)); CHECK(freeze_calls == f + 1);
    // The Mac going to sleep or waking: the next read freezes, once.
    f = freeze_calls; user32_sleep_freeze = 1;
    pad_read(&ps); pad_read(&ps);
    CHECK(freeze_calls == f + 1 && freeze_why && strstr(freeze_why, "sleep") && !user32_sleep_freeze);
    // NFS_PAD_LOST_PAUSE=0: the loss is logged, the menu is never asked.
    setenv("NFS_PAD_LOST_PAUSE", "0", 1);
    fake_source = @[ds]; notify(GCControllerDidConnectNotification, ds); CHECK(pad_read(&ps));
    f = freeze_calls;
    fake_source = @[]; notify(GCControllerDidDisconnectNotification, ds); CHECK(!pad_read(&ps));
    CHECK(freeze_calls == f);
    unsetenv("NFS_PAD_LOST_PAUSE");
    // Simulated pads (every unattended run) never freeze by default ...
    f = freeze_calls;
    setenv("BOOT_PAD", "sim:dualsense", 1); CHECK(pad_read(&ps));
    setenv("BOOT_PAD", "sim:dualsense@999", 1); CHECK(!pad_read(&ps));
    CHECK(freeze_calls == f);
    // ... unless a scripted check asks with NFS_PAD_LOST_PAUSE=1 (a still of the frozen race without a real pad).
    setenv("NFS_PAD_LOST_PAUSE", "1", 1);
    setenv("BOOT_PAD", "sim:dualsense", 1); CHECK(pad_read(&ps));
    setenv("BOOT_PAD", "sim:dualsense@999", 1); CHECK(!pad_read(&ps)); CHECK(!pad_read(&ps));
    CHECK(freeze_calls == f + 1 && freeze_why && strstr(freeze_why, "simulated"));
    unsetenv("NFS_PAD_LOST_PAUSE");
    unsetenv("BOOT_PAD");
    printf("padfreeze_test NFS_PADS=%s: %d checks, %d failures\n", getenv("NFS_PADS") ?: "unset", checks, fails);
    return fails ? 1 : 0;
} }
'''

PICK = 'ctl = !idx && pad_count() == 1 ? pad_pick_update(headless || NSApp.isActive, &pick) : pad_controller_n(idx);'
BROKEN = {
    # The old selection: the first listed extended gamepad (and so no loss to report).
    'first-listed': [(PICK, 'ctl = pad_controller_n(idx); (void)pad_pick_update;'),
                     ('for (GCController *ctl in all) if (ctl == pad_picked && ctl.extendedGamepad) return ctl;   // the pad in use\n', '')],
    'no-loss-freeze': [('if (pick & PADPICK_LOST) pad_freeze("the controller in use disconnected");', '')],
    'no-sleep-freeze': [('if (!idx && __atomic_exchange_n(&user32_sleep_freeze, 0, __ATOMIC_ACQ_REL)) pad_freeze("the Mac went to sleep or woke");', '')],
    'no-slot-freeze': [('if (pad_count() == 2 && !g && pad_presents[idx] == 1) pad_freeze(', 'if (0) pad_freeze(')],
}


def menu_and_workspace(td):
    """Exercise the real race guard and both AppKit notifications, without a game/window."""
    user = (R / 'user32.m').read_text()
    begin = user.index('        extern volatile int user32_sleep_freeze;')
    observers = user[begin:user.index('        // GameController.framework', begin)]
    menu = (R / 'portmenu.m').read_text()
    begin = menu.index('int portmenu_open_if_racing(')
    guard = menu[begin:menu.index('\n}', begin) + 2]
    source = r'''
#import <AppKit/AppKit.h>
#include <stdio.h>
volatile int user32_sleep_freeze;
static int is_open, racing, opens, notes;
static int portmenu_racing(void) { return racing; }
static void set_open(int value) { is_open = value; ++opens; }
static void report_note(const char *format, ...) { (void)format; ++notes; }
@GUARD@
int main(void) { @autoreleasepool {
    int checks = 0, fails = 0;
#define CHECK(x) do { ++checks; if (!(x)) ++fails; } while (0)
    CHECK(!portmenu_open_if_racing("outside race") && !is_open && !opens);
    racing = 1;
    CHECK(portmenu_open_if_racing("pad dropped") && is_open && opens == 1 && notes == 1);
    CHECK(!portmenu_open_if_racing("wake") && is_open && opens == 1 && notes == 1);
    @OBSERVERS@
    for (NSString *name in @[NSWorkspaceWillSleepNotification, NSWorkspaceDidWakeNotification]) {
        __atomic_store_n(&user32_sleep_freeze, 0, __ATOMIC_RELEASE);
        [NSWorkspace.sharedWorkspace.notificationCenter postNotificationName:name object:nil];
        CHECK(__atomic_load_n(&user32_sleep_freeze, __ATOMIC_ACQUIRE) == 1);
    }
    printf("menu/workspace: %d checks, %d failures\n", checks, fails);
    return fails ? 1 : 0;
} }
'''
    for broken in (None, 'no-race-guard', 'no-sleep-observer', 'no-wake-observer'):
        sample = source.replace('@GUARD@', guard).replace('@OBSERVERS@', observers)
        if broken == 'no-race-guard':
            sample = sample.replace('is_open || !portmenu_racing()', 'is_open')
        elif broken:
            old = 'NSWorkspaceWillSleepNotification' if broken == 'no-sleep-observer' else 'NSWorkspaceDidWakeNotification'
            new = 'NSWorkspaceDidWakeNotification' if broken == 'no-sleep-observer' else 'NSWorkspaceWillSleepNotification'
            # Only mutate the production registration, keeping the independent notification list.
            sample = sample.replace(old, new, 1)
        src = td / ((broken or 'workspace') + '.m')
        src.write_text(sample)
        exe = src.with_suffix('')
        h.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fobjc-arc',
               str(src), '-framework', 'AppKit', '-o', str(exe)])
        p = subprocess.run([exe], capture_output=True, text=True)
        expected = 1 if broken else 0
        if p.returncode != expected:
            raise RuntimeError(str((broken, p.returncode, p.stdout, p.stderr[-2000:])))
        print((broken or 'fixed') + ': ' + p.stdout.strip())


def main():
    user = (R / 'user32.m').read_text()
    user = user[user.index('static uint32_t bit('):]
    di = (R / 'dinput.c').read_text()
    with tempfile.TemporaryDirectory(prefix='padfreeze_') as td:
        td = Path(td)
        link_source = td / 'link.c'
        link_source.write_text((R / 'padlink.c').read_text())
        menu_and_workspace(td)
        for broken in [None] + list(BROKEN):
            sample = user
            for old, new in BROKEN.get(broken, []):
                assert sample.count(old) == 1, (broken, old)
                sample = sample.replace(old, new)
            src = td / ((broken or 'fixed') + '.m')
            src.write_text(h.FAKE + sample + '\n' + di + MAIN)
            exe = src.with_suffix('')
            h.run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-Wno-deprecated-declarations',
                   '-fsanitize=address,undefined', '-DPADLINK_SOURCE="' + str(link_source) + '"', '-I', str(R),
                   '-I', str(R.parent / 'recomp'), str(src), '-framework', 'Foundation', '-o', str(exe)])
            settings = ['2'] if broken == 'no-slot-freeze' else ['1'] if broken else ['1', '2']
            for setting in settings:
                env = dict(os.environ, NFS_RUMBLE='off', NFS_PADS=setting)
                for k in ('BOOT_PAD', 'BOOT_PAD2', 'NFS_PAD_LOST_PAUSE'):
                    env.pop(k, None)
                p = subprocess.run([exe, setting], env=env, capture_output=True, text=True)
                if broken:
                    if p.returncode != 1 or 'FAIL' not in p.stdout:
                        raise RuntimeError('deliberately broken ' + broken + ' was not rejected: ' + p.stdout + p.stderr[-2000:])
                    print('deliberately broken ' + broken + ': ' + p.stdout.strip().splitlines()[-1])
                elif p.returncode:
                    raise RuntimeError(p.stdout + p.stderr[-3000:])
                else:
                    print(p.stdout.strip())
    print('padfreeze_test: PASS (old selection and each missing freeze rejected)')


if __name__ == '__main__':
    main()
