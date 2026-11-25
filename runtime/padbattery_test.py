#!/usr/bin/env python3
"""Low DualSense battery: production pad injection/selection, race note and F10 header.

Compiles the actual user32.m pad code with padtwo_test.py's fake controllers, and the
portmenu.m warning/header slices. No hardware or game. Broken controls remove the
10 % threshold or the header draw; both must fail.
Proof: fixed 27/27; missing threshold fails 3/27; missing header fails 2/27;
exclusive threshold (<10) fails 4/27.
"""
from pathlib import Path
import os
import runpy
import subprocess
import tempfile

R = Path(__file__).resolve().parent


def section(source, begin, end):
    return source.split(begin, 1)[1].split(end, 1)[0]


UI = r'''
static int notices, draws;
static char shown[128], note[128];
void overlay_notice(const char *s) { ++notices; snprintf(note, sizeof note, "%s", s); }
static int fnt(int which, double size) { (void)which; (void)size; return 0; }
static int c_amber;
static void text(void *ctx, const char *s, double x, double y, int font, int color, int align) {
    (void)ctx; (void)x; (void)y; (void)font; (void)color; (void)align;
    ++draws; snprintf(shown, sizeof shown, "%s", s);
}
@UPDATE@
static void header(void) {
    void *ctx = NULL;
    @HEADER@
    (void)ctx; (void)text; (void)fnt; (void)c_amber;
}
'''

MAIN = r'''
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static GCController *dual(float level) {
    GCController *p = [GCController new];
    p.extendedGamepad = [GCDualSenseGamepad new]; p.productCategory = @"DualSense";
    p.battery = [GCDeviceBattery new]; p.battery.batteryLevel = level; p.battery.batteryState = 0;
    return p;
}
int main(void) { @autoreleasepool {
    G = calloc(1, 0x1000000);
    CHECK(padbattery_percent(.08f) == 8);
    CHECK(padbattery_percent(.10f) == 10);
    CHECK(padbattery_percent(.1001f) == 11);
    CHECK(padbattery_percent(0) == 0 && padbattery_percent(1) == 100);
    CHECK(padbattery_percent(-1) == -1 && padbattery_percent(NAN) == -1 && padbattery_percent(1.1f) == -1);
    GCController *low = dual(.08f), *healthy = dual(.8f);
    fake_source = @[healthy, low]; pad_picked = low;
    CHECK(user32_pad_battery() == 8); // controller in use, even when listed last
    pad_picked = healthy; CHECK(user32_pad_battery() == 80);
    pad_picked = low; low.battery.batteryState = -1;
    CHECK(user32_pad_battery() == -1); // unknown framework default zero is not a flat battery
    low.battery.batteryState = 0; low.battery.batteryLevel = -1;
    CHECK(user32_pad_battery() == -1);
    low.battery = nil; CHECK(user32_pad_battery() == -1);
    fake_source = @[]; CHECK(user32_pad_battery() == -1);
    PadState st;
    setenv("BOOT_PAD", "sim:dualsense,battery=8", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == 8);
    padbattery_update(0, user32_pad_battery()); header();
    CHECK(draws == 1 && !strcmp(shown, "DualSense battery low: 8 % - connect USB") && notices == 0);
    padbattery_update(1, user32_pad_battery());
    CHECK(notices == 1 && !strcmp(note, shown));
    padbattery_update(1, 8); padbattery_update(1, 0);
    CHECK(notices == 1); // once this race, even at zero, no repeats every frame
    padbattery_update(0, 10); padbattery_update(1, 10);
    CHECK(notices == 2 && strstr(note, "10 %"));
    padbattery_update(0, 11); padbattery_update(1, 11); draws = 0; header();
    CHECK(notices == 2 && draws == 0 && !battery_line[0]);
    padbattery_update(0, -1); padbattery_update(1, -1); header();
    CHECK(notices == 2 && draws == 0);
    padbattery_update(0, 0); padbattery_update(1, 0);
    CHECK(notices == 3 && strstr(note, "0 %"));
    setenv("BOOT_PAD", "sim:edge,battery=10", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == 10);
    setenv("BOOT_PAD", "sim:dualsense,battery=10.01", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == 11);
    setenv("BOOT_PAD", "sim:dualsense,battery=8@999", 1);
    CHECK(!pad_read(&st) && user32_pad_battery() == -1);
    setenv("BOOT_PAD", "sim:dualsense", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == -1);
    setenv("BOOT_PAD", "sim:xbox,battery=8", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == -1);
    setenv("BOOT_PAD", "sim:ds4,battery=8", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == -1);
    setenv("BOOT_PAD", "sim:dualsense,battery=101", 1);
    CHECK(pad_read(&st) && user32_pad_battery() == -1);
    CHECK(real_opens == 0 && real_writes == 0);
    free(G);
    printf("padbattery_test: %d checks, %d failures\n", checks, fails);
    return fails ? 1 : 0;
} }
'''


def main():
    helper = runpy.run_path(str(R / 'padtwo_test.py'))
    user = (R / 'user32.m').read_text().split('static uint32_t bit(', 1)[1]
    user = 'static uint32_t bit(' + user
    menu = (R / 'portmenu.m').read_text()
    update = section(menu, '// ---- padbattery: one note on entering a race, one live header line (plain C) ----',
                     '// ---- end padbattery ----')
    header = section(menu, '// ---- padbattery header ----', '// ---- end padbattery header ----')
    # Exercise the shipped slices, and require their integration into the per-frame menu path.
    frame = menu.split('void portmenu_update(void) {', 1)[1]
    assert 'padbattery_update(portmenu_racing(), user32_pad_battery());' in frame
    assert 'if (strcmp(previous_battery, battery_line)) dirty = 1;' in frame
    env = dict(os.environ, NFS_RUMBLE='off', NFS_PADS='1')
    env.pop('BOOT_PAD', None)
    env.pop('BOOT_PAD2', None)
    with tempfile.TemporaryDirectory(prefix='padbattery_') as td:
        td = Path(td)
        for variant in ('threshold', 'exclusive', 'header', 'fixed'):
            body = update.replace(' && percent <= 10', '') if variant == 'threshold' else update
            if variant == 'exclusive':
                body = update.replace('percent <= 10', 'percent < 10')
            draw = '' if variant == 'header' else header
            ui = UI.replace('@UPDATE@', body).replace('@HEADER@', draw)
            src = td / (variant + '.m')
            src.write_text(helper['FAKE'] + user + ui + MAIN)
            exe = src.with_suffix('')
            helper['run'](['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                           '-Wno-deprecated-declarations', '-fsanitize=address,undefined',
                           '-I', str(R), '-I', str(R.parent / 'recomp'), str(src),
                           '-framework', 'Foundation', '-o', str(exe)])
            p = subprocess.run([str(exe)], env=env, capture_output=True, text=True)
            print(variant + ': ' + p.stdout.strip().splitlines()[-1])
            if variant == 'fixed':
                assert p.returncode == 0, p.stdout + p.stderr[-2000:]
            else:
                assert p.returncode == 1 and 'FAIL' in p.stdout, p.stdout + p.stderr[-2000:]
    print('padbattery_test: PASS (threshold and header controls rejected)')


if __name__ == '__main__':
    main()
