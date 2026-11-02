#!/usr/bin/env python3
"""Regression checks against pinned reviewed text helpers, under ASan/UBSan.
Fixed: 175 checks/0 failures. Original window: 175/6; original repeat: 175/4.
The original repeat deletes 48/71/75 characters at 10/30/60 Hz over five seconds;
the fixed helper deletes 94/93/93 (30 vs 60 Hz differ by zero characters).
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'runtime'

WINDOW_BASELINE = '''void text_window(const char *s, int tail, double maxw, TextMeasure measure, void *ctx, char *out, size_t n) {
    if (!n) return;
    size_t len = strlen(s);
    char line[2 * (TEXT_FIELD_MAX + 8)];
    if (len + 4 > sizeof line) len = sizeof line - 4;
    memcpy(line, s, len); line[len] = 0;
    if (measure(line, ctx) <= maxw) { snprintf(out, n, "%s", line); return; }
    // The most characters that still fit with the ellipsis: binary search over cut points (width grows with length).
    size_t lo = 0, hi = len;                    // lo kept characters always fit (0 = just the ellipsis)
    while (lo < hi) {
        size_t mid = (lo + hi + 1) / 2, at = tail ? len - mid : mid;
        char t[sizeof line + 4];
        if (!boundary(line, at)) { hi = mid - 1; continue; }
        if (tail) snprintf(t, sizeof t, ELLIPSIS "%s", line + at);
        else { memcpy(t, line, at); strcpy(t + at, ELLIPSIS); }
        if (measure(t, ctx) <= maxw) lo = mid; else hi = mid - 1;
    }
    while (lo && !boundary(line, tail ? len - lo : lo)) --lo;
    if (tail) snprintf(out, n, ELLIPSIS "%s", line + len - lo);
    else { char t[sizeof line + 4]; memcpy(t, line, lo); strcpy(t + lo, ELLIPSIS); snprintf(out, n, "%s", t); }
}'''
REPEAT_BASELINE = '''int text_delete_repeat(double *next, int held, double now) {
    if (!held) { *next = 0; return 0; }
    if (*next == 0) { *next = now + .4; return 1; }
    if (now >= *next) { *next = now + .05; return 1; }
    return 0;
}'''

assert hashlib.sha256(WINDOW_BASELINE.encode()).hexdigest() == '32e9699d176510316345b3327ded5beb5539fd86e2c36ecfab2742b7c53a6a4b'
assert hashlib.sha256(REPEAT_BASELINE.encode()).hexdigest() == 'aad3e621e2a813d8017ca9541e38753cba2c086991762f8d89ccb4ce329f9ba5'


def main():
    source = (R/'text_fit.c').read_text()
    variants = {'fixed': (source, 0)}
    for name, baseline, failures in [('text_window', WINDOW_BASELINE, 6),
                                     ('text_delete_repeat', REPEAT_BASELINE, 4)]:
        pattern = r'(?:void|int) ' + name + r'\([^\n]+\) \{.*?\n\}'
        original = re.search(pattern, source, re.S)
        assert original and original.group() != baseline
        variants[name] = (source[:original.start()] + baseline + source[original.end():], failures)
    with tempfile.TemporaryDirectory(prefix='text-fit-review-') as scratch:
        for name, (code, failures) in variants.items():
            path = Path(scratch)/'text_fit.c'; exe = Path(scratch)/'check'
            path.write_text(code)
            c = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g',
                '-fsanitize=address,undefined', '-I'+str(R), '-I'+str(ROOT/'recomp'),
                str(R/'note_len_test.c'), str(path), '-o', str(exe)], capture_output=True, text=True)
            assert c.returncode == 0, c.stderr[-4000:]
            p = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True)
            count = re.search(r'(?:PASS|FAIL): (\d+) checks, (\d+) failures', p.stdout)
            assert count and tuple(map(int, count.groups())) == (175, failures), p.stdout + p.stderr
            assert p.returncode == int(failures != 0), p.stdout + p.stderr
            print(name + ': ' + p.stdout.splitlines()[-1])


if __name__ == '__main__':
    main()
