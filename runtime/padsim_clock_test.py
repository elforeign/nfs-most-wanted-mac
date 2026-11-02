#!/usr/bin/env python3
"""Slow start-up: fake enumeration at process +8 s, smoke connect at enumeration +2 s.
Actual user32 pad reader + dinput enumeration, Foundation fakes, ASan/UBSan.
Proof numbers: fixed 0 failures; original @6 smoke 1 failure (already present
at enumeration). Original timeline parser also fails the @e reconnect checks.
No wall-clock sleeps, game, private assets or controller hardware. Autodiscovered
by offline_tests.sh; plain @T and untimed sim retain their original semantics.
"""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('padtwo_harness', R / 'padtwo_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

# Pinned original timeline block: reproduce the process-clock race without git history.
ORIGINAL = r'''        if (at) {                           // connected while an odd number of the times have passed
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
'''
MAIN = r'''
static void enumerate(uint32_t type) {
    enumerated = 0;
    CHECK(invoke(i_EnumDevices, 5, 0u, type, 1u, 0u, 0u) == DI_OK);
}
int main(int argc, char **argv) { @autoreleasepool {
    (void)argc;
    G = calloc(1, 0x1000000);
    CHECK(!real_opens && !real_writes);
    PadState ps;
    uint64_t boot = start_ns;
    const char *mode = argv[1];
    if (!strcmp(mode, "smoke")) {
        setenv("BOOT_PAD", argv[2], 1);
        fake_time = boot + 8000000000ull;  // loaded startup: old @6 has already connected
        enumerate(0x15);
        CHECK(!pad_read_n(0, &ps) && !ps.connected && ps.pov == -1);
        fake_time += 2000000000ull;
        CHECK(pad_read_n(0, &ps) && ps.connected);
    } else if (!strcmp(mode, "relative")) {
        setenv("BOOT_PAD", "sim:dualsense@e2-4-6", 1);
        setenv("BOOT_PAD2", "sim:ds4@e3-5-7", 1);
        fake_time = boot + 8000000000ull;
        CHECK(!pad_read_n(0, &ps)); CHECK(!pad_read_n(1, &ps));
        enumerate(3); // keyboard enumeration must not arm the timeline
        fake_time = boot + 12000000000ull;
        CHECK(!pad_read_n(0, &ps)); CHECK(!pad_read_n(1, &ps));
        enumerate(0x15);
        uint64_t epoch = fake_time;
        CHECK(!pad_read_n(0, &ps)); CHECK(!pad_read_n(1, &ps));
        fake_time = epoch + 1999999999ull;
        CHECK(!pad_read_n(0, &ps));
        fake_time = epoch + 2000000000ull;
        CHECK(pad_read_n(0, &ps) && ps.native); CHECK(!pad_read_n(1, &ps));
        enumerate(4); // a second scan must not reset either slot's epoch
        fake_time = epoch + 3000000000ull;
        CHECK(pad_read_n(0, &ps)); CHECK(pad_read_n(1, &ps) && ps.native);
        fake_time = epoch + 4000000000ull;
        CHECK(!pad_read_n(0, &ps)); CHECK(pad_read_n(1, &ps));
        fake_time = epoch + 5000000000ull;
        CHECK(!pad_read_n(1, &ps));
        fake_time = epoch + 6000000000ull;
        CHECK(pad_read_n(0, &ps)); CHECK(!pad_read_n(1, &ps));
        fake_time = epoch + 7000000000ull;
        CHECK(pad_read_n(1, &ps));
    } else if (!strcmp(mode, "zero")) {
        setenv("BOOT_PAD", "sim:dualsense@e0", 1);
        fake_time = boot + 8000000000ull;
        CHECK(!pad_read_n(0, &ps)); enumerate(0);
        CHECK(pad_read_n(0, &ps));
    } else if (!strcmp(mode, "second")) {
        setenv("BOOT_PAD2", "sim:ds4@e2", 1);
        fake_time = boot + 8000000000ull;
        enumerate(4); CHECK(!pad_read_n(1, &ps));
        fake_time += 2000000000ull; CHECK(pad_read_n(1, &ps));
    } else {
        setenv("BOOT_PAD", "sim:dualsense@6-10-12", 1);
        fake_time = boot + 5999999999ull; CHECK(!pad_read_n(0, &ps));
        fake_time = boot + 6000000000ull; CHECK(pad_read_n(0, &ps));
        fake_time = boot + 8000000000ull; enumerate(4); CHECK(pad_read_n(0, &ps));
        fake_time = boot + 10000000000ull; CHECK(!pad_read_n(0, &ps));
        fake_time = boot + 12000000000ull; CHECK(pad_read_n(0, &ps));
        setenv("BOOT_PAD", "sim", 1); CHECK(pad_read_n(0, &ps));
        setenv("BOOT_PAD", "sim:dualsense", 1); CHECK(pad_read_n(0, &ps) && ps.native);
    }
    printf("padsim %s: %d checks, %d failures\n", mode, checks, fails);
    free(G); return fails != 0;
} }
'''


def main():
    user = (R / 'user32.m').read_text()
    user = user[user.index('static uint32_t bit('):]
    a = user.index('        if (at) {')
    b = user.index('        if (!on)', a)
    old = user[:a] + ORIGINAL + user[b:]
    assert old != user, 'original parser mutation did not apply'
    di = (R / 'dinput.c').read_text()
    prefix = h.MAIN[:h.MAIN.index('static GCControllerButtonInput *button(')]
    smoke = re.search(r'\bBOOT_PAD=(\S+)', (R / 'smoke.sh').read_text().split('runtime/run_guarded.sh', 1)[1]).group(1)
    env = dict(os.environ, NFS_PADS='2', NFS_RUMBLE='off')
    env.pop('BOOT_PAD', None)
    env.pop('BOOT_PAD2', None)
    with tempfile.TemporaryDirectory(prefix='padsim-clock-') as td:
        td = Path(td)
        variants = [('fixed', user, di), ('original', old, di),
                    ('missing-hook', user, di.replace('if (want_pad) pad_sim_enumerated();', ''))]
        for label, sample, enumeration in variants:
            source = td / (label + '.m')
            exe = source.with_suffix('')
            source.write_text(h.FAKE + sample + enumeration + prefix + MAIN)
            h.run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                   '-Wno-deprecated-declarations', '-fsanitize=address,undefined',
                   '-I', str(R), '-I', str(R.parent / 'recomp'), str(source),
                   '-framework', 'Foundation', '-o', str(exe)])
            for mode in ('smoke', 'relative', 'zero', 'second', 'plain'):
                timeline = 'sim:dualsense@6' if label == 'original' else smoke
                result = subprocess.run([exe, mode, timeline], env=env, capture_output=True, text=True)
                expected = 0 if label == 'fixed' or mode == 'plain' else 1
                assert result.returncode == expected, result.stdout + result.stderr[-3000:]
                print(label + ': ' + result.stdout.strip().splitlines()[-1])
    print('padsim_clock_test: PASS (original startup race and missing enumeration hook rejected)')


if __name__ == '__main__':
    main()
