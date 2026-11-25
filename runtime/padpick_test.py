#!/usr/bin/env python3
"""Which controller the game reads with NFS_PADS=1 (user32.m padpick_choose), compiled alone as plain C.

Scenarios: two pads listed, the pad in use dropping and coming back listed last (a Bluetooth reconnect), a pad
left on at rest, the loss reported once. The deliberately broken variant is the old rule (the first listed
extended gamepad, whatever is in use) and must be rejected.
Proof: fixed 19/19; old first-listed control fails 7/19.
"""
from pathlib import Path
import subprocess
import tempfile

R = Path(__file__).resolve().parent

MAIN = r'''
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
@SLICE@
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static int A, B, C;                     /* three controllers, by address */
static PadPick s;
static int ev;
static const void *read_with(const void *p0, int in0, const void *p1, int in1, int n) {
    const void *ids[2] = {p0, p1};
    int input[2] = {in0, in1};
    int k = padpick_choose(&s, ids, input, n, &ev);
    return k >= 0 ? ids[k] : NULL;
}
int main(void) {
    /* nothing connected, then one pad */
    CHECK(read_with(NULL, 0, NULL, 0, 0) == NULL && ev == 0);
    CHECK(read_with(&A, 0, NULL, 0, 1) == &A && ev == 0);
    /* a second pad switched on, at rest: A stays */
    CHECK(read_with(&A, 0, &B, 0, 2) == &A && ev == 0);
    /* someone presses on B while A is in use (A held too): A stays */
    CHECK(read_with(&A, 1, &B, 1, 2) == &A && ev == 0);
    /* the player picks up B and presses a button, A at rest: B takes over, and keeps it once released */
    CHECK(read_with(&A, 0, &B, 1, 2) == &B && ev == PADPICK_SWITCHED);
    CHECK(read_with(&A, 0, &B, 0, 2) == &B && ev == 0);
    /* back to A by pressing on it */
    CHECK(read_with(&A, 1, &B, 0, 2) == &A && ev == PADPICK_SWITCHED);
    /* Bluetooth drop of A with B left on: B takes over, the loss is reported once */
    CHECK(read_with(&B, 0, NULL, 0, 1) == &B && ev == PADPICK_LOST);
    CHECK(read_with(&B, 0, NULL, 0, 1) == &B && ev == 0);
    /* A reconnects, listed last, and the player presses Cross on it: A again (the old rule stayed on B) */
    CHECK(read_with(&B, 0, &A, 1, 2) == &A && ev == PADPICK_SWITCHED);
    /* A stays in use when the framework lists it last and B first */
    CHECK(read_with(&B, 0, &A, 0, 2) == &A && ev == 0);
    /* the only pad drops: nothing to read, one loss; nothing more reported while none is there */
    CHECK(read_with(&B, 0, NULL, 0, 1) == &B && ev == PADPICK_LOST);
    CHECK(read_with(NULL, 0, NULL, 0, 0) == NULL && ev == PADPICK_LOST);
    CHECK(read_with(NULL, 0, NULL, 0, 0) == NULL && ev == 0);
    /* two pads come back at once: the one with input wins, else the first listed */
    CHECK(read_with(&B, 0, &C, 1, 2) == &C && ev == 0);
    s.active = NULL;
    CHECK(read_with(&B, 0, &C, 0, 2) == &B && ev == 0);
    /* deliberate input: a button, or a stick / trigger past half way; resting drift is not */
    CHECK(!padpick_input(0, .2f, -.3f, .1f, 0, .2f, .4f));
    CHECK(padpick_input(1u << 3, 0, 0, 0, 0, 0, 0));
    CHECK(padpick_input(0, -.6f, 0, 0, 0, 0, 0) && padpick_input(0, 0, 0, 0, .7f, 0, 0) && padpick_input(0, 0, 0, 0, 0, 0, .6f));
    (void)A; (void)B; (void)C;
    printf("padpick_test: %d checks, %d failures\n", checks, fails);
    return fails ? 1 : 0;
}
'''

# The old rule, as pad_controller_n had it: the first listed extended gamepad.
OLD_RULE = r'''static int padpick_choose(PadPick *s, const void *const *ids, const int *input, int n, int *events) {
    (void)input;
    *events = 0;
    if (s->active) { int there = 0; for (int i = 0; i < n; ++i) there |= ids[i] == s->active; if (!there) *events |= PADPICK_LOST; }
    s->active = n > 0 ? ids[0] : NULL;
    return n > 0 ? 0 : -1;
}
'''


def section(user):
    start = user.index('// ---- padpick:')
    return user[start:user.index('// ---- end padpick ----', start)]


def main():
    sliced = section((R / 'user32.m').read_text())
    begin = sliced.index('static int padpick_choose(')
    end = sliced.index('static int padpick_input(')
    with tempfile.TemporaryDirectory(prefix='padpick_') as td:
        td = Path(td)
        for broken in (False, True):
            code = sliced[:begin] + OLD_RULE + sliced[end:] if broken else sliced
            src = td / ('broken.c' if broken else 'padpick.c')
            src.write_text(MAIN.replace('@SLICE@', code))
            exe = src.with_suffix('')
            subprocess.run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', str(src), '-lm', '-o', str(exe)],
                           check=True)
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            if broken:
                if p.returncode != 1 or 'FAIL' not in p.stdout:
                    raise RuntimeError('the old first-listed rule was not rejected:\n' + p.stdout)
                print('deliberately broken (first listed pad): ' + p.stdout.strip().splitlines()[-1])
            elif p.returncode:
                raise RuntimeError(p.stdout + p.stderr)
            else:
                print(p.stdout.strip())
    print('padpick_test: PASS (the old first-listed rule rejected)')


if __name__ == '__main__':
    main()
