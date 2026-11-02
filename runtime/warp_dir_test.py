#!/usr/bin/env python3
"""BOOT_DIAG_WARP facing field: the production parser from runtime/diag.c, compiled alone (no game).
Passes 9/9 checks; with the ':dir=' lookup removed from the parser (the pre-field behaviour) 3/9 checks fail.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / 'runtime/diag.c').read_text()
m = re.search(r'static int diag_warp_parse\(.*?\n\}\n', src, re.S)
if not m:
    print('FAIL: diag_warp_parse not found in runtime/diag.c'); sys.exit(1)
fn = m.group(0)
if len(sys.argv) > 1 and sys.argv[1] == '--broken':    # control: the parser without the facing field
    fn = fn.replace('strstr(e, ":dir=")', 'NULL')
prog = r'''
#include <stdio.h>
#include <string.h>
#include <math.h>
''' + fn + r'''
static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
#define EQ3(v, a, b, c) (fabsf((v)[0] - (a)) < 1e-6f && fabsf((v)[1] - (b)) < 1e-6f && fabsf((v)[2] - (c)) < 1e-6f)
int main(void) {
    float at, p[3], d[3];
    CHECK(diag_warp_parse("20:-373.3,100.8,1389.1", &at, p, d) && at == 20.0f && EQ3(p, -373.3f, 100.8f, 1389.1f));
    CHECK(EQ3(d, 1, 0, 0));
    CHECK(diag_warp_parse("20:-373.3,100.8,1389.1:dir=0,0,-1", &at, p, d) && EQ3(p, -373.3f, 100.8f, 1389.1f));
    CHECK(EQ3(d, 0, 0, -1));
    CHECK(diag_warp_parse("5:1,2,3:dir=-1,0,0:block", &at, p, d) && EQ3(d, -1, 0, 0));
    CHECK(diag_warp_parse("5:1,2,3:block", &at, p, d) && EQ3(d, 1, 0, 0));
    CHECK(!diag_warp_parse("5:1,2,3:dir=0,0", &at, p, d));
    CHECK(!diag_warp_parse("5:1,2", &at, p, d));
    CHECK(!diag_warp_parse(NULL, &at, p, d));
    printf("%d checks, %d failures\n", checks, fails);
    return fails != 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / 'w.c'; c.write_text(prog)
    r = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(c),
                        '-o', str(Path(t) / 'w')], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:]); print('FAIL: build'); sys.exit(1)
    r = subprocess.run([str(Path(t) / 'w')], capture_output=True, text=True)
    print(r.stdout, end='')
    print('PASS' if r.returncode == 0 else 'FAIL'); sys.exit(r.returncode)
