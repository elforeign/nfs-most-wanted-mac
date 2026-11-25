#!/usr/bin/env python3
"""Two-pad slot refill (NFS_PADS=2, user32.m padslot_fill), compiled alone as plain C.

Scenarios: a third pad left waiting takes a slot freed by a drop; a pad whose gamepad profile wasn't ready at its
connect notification is picked up once it is; the read reports the loss (the race freeze) before the refill; the
surviving slot never moves; a pad is never put in both slots. The harness models the pad_controller_n glue (refill
only when the slot is empty and its presence was already reported as 0); the glue itself is covered on macOS by
padtwo_test.py. The deliberately broken variant is the old rule (a slot is only filled by a connect notification)
and must be rejected.
Proof: fixed 16/16; old connect-only control fails 9/16.
"""
from pathlib import Path
import subprocess
import tempfile

R = Path(__file__).resolve().parent

MAIN = r'''
#include <stdio.h>
#include <stddef.h>
@SLICE@
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static int A, B, C, D;
static const void *slots[2];
static int presents[2] = {-1, -1}, freezes;
static const void *listed[4];           /* GCController.controllers, extended gamepads only */
static int nlisted;
static void list(const void *a, const void *b, const void *c) {
    nlisted = 0;
    if (a) listed[nlisted++] = a;
    if (b) listed[nlisted++] = b;
    if (c) listed[nlisted++] = c;
}
/* pad_slot_connect: the connect notification fills the first hole (a pad with a gamepad profile only) */
static void connect(const void *p, int has_profile) {
    if (!has_profile) return;
    for (int i = 0; i < 2; ++i) if (slots[i] == p) return;
    for (int i = 0; i < 2; ++i) if (!slots[i]) { slots[i] = p; return; }
}
static void disconnect(const void *p) { for (int i = 0; i < 2; ++i) if (slots[i] == p) slots[i] = NULL; }
/* one read of slot idx: pad_controller_n, then the presence / freeze step of the pad read */
static const void *read_slot(int idx) {
    if (!slots[idx] && presents[idx] == 0) padslot_fill(slots, idx, listed, nlisted);
    const void *p = slots[idx];
    if (!p && presents[idx] == 1) ++freezes;
    presents[idx] = p != NULL;
    return p;
}
int main(void) {
    /* A and B in the slots, C switched on as well: C waits */
    list(&A, &B, NULL); connect(&A, 1); connect(&B, 1);
    CHECK(read_slot(0) == &A && read_slot(1) == &B);
    list(&A, &B, &C); connect(&C, 1);
    CHECK(read_slot(0) == &A && read_slot(1) == &B);
    /* A drops: the first read reports the loss (race freeze) and reads nothing, then C takes slot 0; B stays */
    list(&B, &C, NULL); disconnect(&A);
    CHECK(read_slot(0) == NULL && freezes == 1);
    CHECK(read_slot(1) == &B);
    CHECK(read_slot(0) == &C && freezes == 1);
    CHECK(read_slot(0) == &C && read_slot(1) == &B);
    /* A comes back: both slots full, it waits; nothing moves */
    list(&B, &C, &A); connect(&A, 1);
    CHECK(read_slot(0) == &C && read_slot(1) == &B && freezes == 1);
    /* B drops with A waiting: one loss, then A takes slot 1, never C twice */
    list(&C, &A, NULL); disconnect(&B);
    CHECK(read_slot(1) == NULL && freezes == 2);
    CHECK(read_slot(1) == &A && read_slot(0) == &C);
    /* a hole with nothing waiting stays empty, with no second loss */
    list(&A, NULL, NULL); disconnect(&C);
    CHECK(read_slot(0) == NULL && freezes == 3);
    CHECK(read_slot(0) == NULL && freezes == 3 && read_slot(1) == &A);
    /* D connects before its gamepad profile is ready: the notification skips it; once listed, it takes the hole */
    connect(&D, 0);
    CHECK(read_slot(0) == NULL);
    list(&A, &D, NULL);
    CHECK(read_slot(0) == &D && read_slot(1) == &A && freezes == 3);
    /* the fill rule itself: a full slot is left alone, a pad already in the other slot is skipped */
    const void *s[2] = {&A, NULL}, *ids[2] = {&A, &B};
    CHECK(padslot_fill(s, 0, ids, 2) == -1 && s[0] == &A);
    CHECK(padslot_fill(s, 1, ids, 2) == 1 && s[1] == &B && s[0] == &A);
    const void *t[2] = {NULL, &A}, *only[1] = {&A};
    CHECK(padslot_fill(t, 0, only, 1) == -1 && t[0] == NULL);
    printf("padslot_test: %d checks, %d failures\n", checks, fails);
    return fails ? 1 : 0;
}
'''

# The old rule: a slot is only ever filled by a connect notification.
OLD_RULE = r'''static int padslot_fill(const void **slots, int hole, const void *const *ids, int n) {
    (void)slots; (void)hole; (void)ids; (void)n;
    return -1;
}
'''


def section(user):
    start = user.index('// ---- padslot:')
    return user[start:user.index('// ---- end padslot ----', start)]


def main():
    sliced = section((R / 'user32.m').read_text())
    begin = sliced.index('static int padslot_fill(')
    with tempfile.TemporaryDirectory(prefix='padslot_') as td:
        td = Path(td)
        for broken in (False, True):
            code = sliced[:begin] + OLD_RULE if broken else sliced
            src = td / ('broken.c' if broken else 'padslot.c')
            src.write_text(MAIN.replace('@SLICE@', code))
            exe = src.with_suffix('')
            subprocess.run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', str(src), '-o', str(exe)],
                           check=True)
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            if broken:
                if p.returncode != 1 or 'FAIL' not in p.stdout:
                    raise RuntimeError('the old connect-only rule was not rejected:\n' + p.stdout)
                print('deliberately broken (connect-only slots): ' + p.stdout.strip().splitlines()[-1])
            elif p.returncode:
                raise RuntimeError(p.stdout + p.stderr)
            else:
                print(p.stdout.strip())
    print('padslot_test: PASS (the old connect-only rule rejected)')


if __name__ == '__main__':
    main()
