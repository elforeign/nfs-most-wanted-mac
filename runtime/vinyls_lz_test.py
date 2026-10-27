#!/usr/bin/env python3
"""Offline proof for audit2 #2: a vinyl blob the native decoder rejects must not reach the game's unbounded
LZDecompress when its header's unpacked size (1024) exceeds the destination (table usize 156 + 64 = 220 bytes).
Compiles production load_x360 (runtime/vinyls.m) with stubs; call_guest emulates the game's decoder by writing the
header size of 0xAA bytes at dst; the destination is followed by a canary. A valid blob must still decode natively.
Fixed code: guest decoder never called for the oversized blob (calls=0), canary intact, valid blob ok. PASS.
Old code (--old, vinyls.m from OLD_REF default main): calls=1, canary overwritten by 804 bytes. FAIL.
Run: $TOOLS_PY -I runtime/vinyls_lz_test.py [--old]
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^static [^\n;{]+\b" + name + r"\([^\n]*\)\s*\{", source, re.M)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct Cpu { uint32_t esp, eax, ecx, edx; } Cpu;
typedef struct { uint32_t hash, file, off, csize, usize; } VEnt;
typedef uint16_t u16a; typedef uint32_t u32a;
static uint8_t *G;
#define M16(a) (*(uint16_t *)(G + (uint32_t)(a)))
#define M32(a) (*(uint32_t *)(G + (uint32_t)(a)))
#define X360_LIN_ARGB 0x18280086u
#define FN_LZDECOMPRESS 0x00650350u
static int diag_on;
static void diag_event(const char *f, ...) { (void)f; }
static uint64_t host_ticks(void) { return 0; }
static uint32_t bump = 0x10000;
static uint32_t gmalloc(uint32_t n) { uint32_t a = bump; bump += (n + 63 + 4096) & ~63u; return a; }
static void gfree(uint32_t a) { (void)a; }
static char **vfiles;
#include "jdlz.h"
#include "huff.h"
static int guest_calls, guest_dst_off;
static uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    (void)c; (void)fn; (void)n;
    uint32_t us; memcpy(&us, G + a[0] + 8, 4);
    memset(G + a[1], 0xAA, us);               /* the game's decoder: writes the header's unpacked size, unbounded */
    ++guest_calls;
    return us;
}
LOADTIMES
LZNATIVE
LOAD
int main(void) {
    G = calloc(1, 0x200000);
    char path[] = "/tmp/vinyls_lz_XXXXXX";
    int fd = mkstemp(path);
    uint8_t blob[32] = {'J', 'D', 'L', 'Z', 2, 0, 0, 0, 0, 4, 0, 0, 21, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14};
    write(fd, blob, sizeof blob); close(fd);
    vfiles = malloc(sizeof *vfiles); vfiles[0] = path;
    VEnt v = {0x1234, 0, 0, 21, 156};          /* table usize 156 -> dst 220 bytes; the blob says 1024 */
    uint32_t w = 0, h = 0; LoadTimes lt; Cpu cpu = {0};
    uint32_t next = bump;                       /* dst is the second gmalloc after src */
    uint32_t r = load_x360(&cpu, &v, &w, &h, &lt);
    uint32_t dst = next + ((21 + 16 + 63 + 4096) & ~63u);
    int over = 0;
    for (uint32_t i = 156 + 64; i < 1024; ++i) over += G[dst + i] == 0xAA;
    printf("oversized blob: guest decoder calls=%d, bytes written past dst=%d, result=%u\n", guest_calls, over, r);
    unlink(path);
    return guest_calls == 0 && over == 0 && r == 0 ? 0 : 1;
}
'''


def main():
    ref = os.environ.get("OLD_REF", "main")
    if "--old" in sys.argv:
        source = subprocess.run(["git", "show", ref + ":runtime/vinyls.m"], cwd=ROOT, text=True, capture_output=True, check=True).stdout
    else:
        source = (ROOT / "runtime/vinyls.m").read_text()
    lt = re.search(r"^typedef struct \{ double read[^\n]*\} LoadTimes;", source, re.M).group(0)
    code = HARNESS.replace("LOADTIMES", lt).replace("LZNATIVE", function(source, "lz_native")).replace(
        "LOAD", "static int vlz_mode_unused;\n" + function(source, "load_x360"))
    code = code.replace("static uint32_t lz_native", "static int vlz_mode = -1; static uint32_t vlz_verified[2], vlz_mismatch[2];\nstatic uint32_t lz_native", 1)
    with tempfile.TemporaryDirectory(prefix="vinyls_lz_") as tmp:
        t = Path(tmp) / "t.c"
        t.write_text("#include <unistd.h>\n" + code)
        b = Path(tmp) / "t"
        subprocess.run(["clang", "-O1", "-g", "-w", "-I", str(ROOT / "runtime"), str(t), "-o", str(b)], check=True)
        r = subprocess.run([str(b)], text=True, capture_output=True)
        print(r.stdout, end="")
        if r.stderr: print(r.stderr[-2000:], file=sys.stderr)
        if r.returncode < 0: print("crashed, signal", -r.returncode)
        print("PASS" if r.returncode == 0 else "FAIL")
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
