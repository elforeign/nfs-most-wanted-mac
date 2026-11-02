#!/usr/bin/env python3
"""Offline proof for audit2 #2: a vinyl blob the native decoder rejects must not reach the game's unbounded
LZDecompress when its header's unpacked size (1024) exceeds the destination (table usize 156 + 64 = 220 bytes).
Compiles production load_x360 (runtime/vinyls.m) with stubs; call_guest emulates the game's decoder by writing the
header size of 0xAA bytes at dst; the destination is followed by a canary. A valid blob must still decode natively.
Fixed code: guest decoder never called for the oversized blob (calls=0), canary intact, valid blob ok. PASS.
Old code (--old, pinned pre-bounds-check load_x360): calls=1, canary overwritten by 804 bytes. FAIL.
Run: $TOOLS_PY -I runtime/vinyls_lz_test.py [--old]
"""
from pathlib import Path
import hashlib
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



# Baseline integrity proof: 1/1 authentic fragments accepted; 1/1 one-byte corruptions rejected.
# Reviewed baseline fragment; its digest prevents accidental control drift.
OLD_VINYLS = r'''typedef struct { double read, lz, lz_guest, swz; uint32_t magic; int native; } LoadTimes;
static uint32_t lz_native(uint32_t magic, const uint8_t *in, uint32_t inlen, uint8_t *out, uint32_t cap) {
    return magic == 0x5a4c444au ? jdlz_native(in, inlen, out, cap) : magic == 0x46465548u ? huff_native(in, inlen, out, cap) : 0;
}
static uint32_t load_x360(Cpu *c, const VEnt *v, uint32_t *w, uint32_t *h, LoadTimes *lt) {
    if (vlz_mode < 0) {
        const char *e = getenv("NFS_VINYL_LZ");
        vlz_mode = e && !strcmp(e, "guest") ? 0 : e && !strcmp(e, "verify") ? 2 : 1;
        if (e && !strcmp(e, "native")) vlz_verified[0] = vlz_verified[1] = 40;   // explicit: no diag-mode check either (timing runs)
    }
    memset(lt, 0, sizeof *lt);
    uint64_t t0 = host_ticks();
    FILE *f = fopen(vfiles[v->file], "rb");
    if (!f) return 0;
    uint32_t src = gmalloc(v->csize + 16), dst = gmalloc(v->usize + 64), ok = 0;
    if (fseek(f, (long)v->off, SEEK_SET) == 0 && fread(G + src, 1, v->csize, f) == v->csize) {
        uint64_t t1 = host_ticks();
        lt->read = (t1 - t0) / 1e6;
        memcpy(&lt->magic, G + src, 4);
        int k = lt->magic == 0x5a4c444au ? 0 : lt->magic == 0x46465548u ? 1 : -1;
        int verify = k >= 0 && (vlz_mode == 2 || (diag_on && vlz_mode == 1 && vlz_verified[k] < 40));
        uint32_t n = 0;
        if (k >= 0 && vlz_mode && !verify) {
            n = lz_native(lt->magic, G + src, v->csize, G + dst, v->usize + 64);
            lt->native = n != 0;
            if (!n && diag_on) diag_event("vinyls: %08X: native %s decoder rejected the blob; using the game's", v->hash, k ? "HUFF" : "JDLZ");
        }
        if (!lt->native) {
            uint32_t a[2] = {src, dst}, esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx;
            n = call_guest(c, FN_LZDECOMPRESS, 2, a);       // __cdecl; ecx is still Init's TextureInfo
            c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
        }
        uint64_t t2 = host_ticks();
        lt->lz = (t2 - t1) / 1e6;
        if (verify) {
            uint8_t *mine = malloc(v->usize + 64);
            uint32_t m = lz_native(lt->magic, G + src, v->csize, mine, v->usize + 64);
            uint64_t t3 = host_ticks();
            lt->lz_guest = lt->lz; lt->lz = (t3 - t2) / 1e6;
            ++vlz_verified[k];
            int same = m == n && !memcmp(mine, G + dst, n);
            if (!same) ++vlz_mismatch[k];
            if (diag_on) diag_event("vinyls: %s check %08X: native %u bytes in %.2f ms, game LZDecompress %u bytes in %.2f ms: %s (%u checked, %u differ)",
                                    k ? "HUFF" : "JDLZ", v->hash, m, lt->lz, n, lt->lz_guest, same ? "identical" : "DIFFERENT", vlz_verified[k], vlz_mismatch[k]);
            free(mine);
            t2 = t3;
        }
        uint32_t tail = dst + v->usize - 156;
        *w = M16(tail + 0x44); *h = M16(tail + 0x46);
        uint32_t fmt = M32(tail + 124 + 0x14), pitch = (*w * 4 + 255) & ~255u;
        if (n == v->usize && fmt == X360_LIN_ARGB && *w >= 64 && *h >= 64 && *w <= 2048 && *h <= 2048 && pitch * *h + 156 <= v->usize) {
            uint32_t *p = (uint32_t *)(G + dst);            // A R G B -> B G R A (rows are tightly packed for w >= 64)
            for (uint32_t i = 0; i < *w * *h; ++i) p[i] = __builtin_bswap32(p[i]);
            ok = 1;
        } else if (diag_on)
            diag_event("vinyls: %08X: X360 blob unusable (LZDecompress %u of %u bytes, %ux%u, format %08X)", v->hash, n, v->usize, *w, *h, fmt);
        lt->swz = (host_ticks() - t2) / 1e6;
    }
    fclose(f);
    gfree(src);
    if (!ok) { gfree(dst); return 0; }
    return dst;
}'''
assert hashlib.sha256(OLD_VINYLS.encode()).hexdigest() == '04785857a5a76ef89c754dc92947f36fb198e8ed5e0aa74d39cacd276ebe6915', 'pinned baseline changed'

def main():
    if "--old" in sys.argv:
        source = OLD_VINYLS
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
