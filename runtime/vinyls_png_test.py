#!/usr/bin/env python3
"""Compile the production vinyls.m diagnostic block with counting stubs; no assets/GPU.

64 HD composites: unset/0/invalid -> 0 diag_composite calls; 1 -> 35
(first 30 plus 32/40/48/56/64). Each keeps all 64 composite timing events.
Diagnostics off or width 512 -> 0 PNG calls. Pinned old default (--old):
unset -> 35 calls instead of 0, FAIL (exit 1); fixed default -> 0, PASS.
Autodiscovered and run by runtime/offline_tests.sh.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent
OLD = '        if (png < 0) { const char *e = getenv("NFS_VINYL_PNG"); png = !(e && !strcmp(e, "0")); }'
OLD_SHA = '09746655dc44ef02ad68f0f1bd8f21a80e703f048ce6dcb38ecf89e0a9f98bbd'
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { unsigned w, h, levels; } Obj;
typedef struct { unsigned w, remap, sw[4]; } Layer;
static unsigned ncomposites, calls, timings, image_timings, seen[65];
static uint64_t host_ticks(void) { return 0; }
static uint32_t M32(uint32_t a) { return a; }
static void diag_event(const char *fmt, ...) {
    if (strstr(fmt, "diag images")) ++image_timings;
    else if (strstr(fmt, "vinyls: composite")) ++timings;
}
static void diag_composite(Obj *t, uint32_t target, uint32_t base,
                           const uint32_t *regions, const Layer *l,
                           const uint32_t *lh, const uint32_t *mh, int n) {
    (void)t; (void)target; (void)base; (void)regions; (void)l;
    (void)lh; (void)mh; (void)n;
    ++calls; ++seen[ncomposites];
}
static void diagnostic(int diag_on, Obj *T) {
    uint64_t t0 = 0;
    uint32_t target = 0, p = 0, regions[1] = {0}, lhash[1] = {0}, mhash[1] = {0};
    Layer L[1] = {{0}};
    int n = 0;
BLOCK
}
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    int enabled = atoi(argv[1]), diag_on = atoi(argv[2]);
    Obj t = {(unsigned)atoi(argv[3]), 1024, 1};
    for (ncomposites = 1; ncomposites <= 64; ++ncomposites) diagnostic(diag_on, &t);
    unsigned expected[65] = {0};
    if (enabled && diag_on && t.w > 512) {
        for (unsigned i = 1; i <= 30; ++i) expected[i] = 1;
        const unsigned later[] = {32, 40, 48, 56, 64};
        for (unsigned i = 0; i < sizeof later / sizeof *later; ++i) expected[later[i]] = 1;
    }
    unsigned wanted = enabled && diag_on && t.w > 512 ? 35 : 0;
    printf("PNG calls=%u expected=%u; composite timing events=%u expected=%u\n",
           calls, wanted, timings, diag_on ? 64 : 0);
    return calls != wanted || image_timings != wanted || timings != (diag_on ? 64u : 0u)
        || memcmp(seen, expected, sizeof seen) != 0;
}
'''


def main():
    assert hashlib.sha256(OLD.encode()).hexdigest() == OLD_SHA
    source = (RT / 'vinyls.m').read_text()
    # The final diagnostics block in CompositeSkin32, including its timing event.
    end = source.index('\n    c->eax = 1;', source.index('    build_mips(T);'))
    start = source.rindex('    if (diag_on) {', 0, end)
    block = source[start:end]
    assert 'diag_composite(' in block and 'vinyls: composite %u into' in block
    if '--old' in sys.argv:
        block, count = re.subn(r'^.*if \(png < 0\).*$', lambda _: OLD, block, flags=re.M)
        assert count == 1
    with tempfile.TemporaryDirectory(prefix='vinyl-png-') as temp:
        cfile = Path(temp) / 'test.c'
        binary = Path(temp) / 'test'
        cfile.write_text(HARNESS.replace('BLOCK', block))
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', str(cfile), '-o', str(binary)], check=True)
        def check(value, enabled=0, diag_on=1, width=1024):
            env = dict(os.environ)
            env.pop('NFS_VINYL_PNG', None)
            if value is not None:
                env['NFS_VINYL_PNG'] = value
            result = subprocess.run([str(binary), str(enabled), str(diag_on), str(width)],
                                    env=env, capture_output=True, text=True, check=False)
            print(f'env={value!r} diag={diag_on} width={width}: {result.stdout.strip()}', flush=True)
            if result.returncode:
                raise SystemExit(1)
        check(None)
        check('0')
        check('1', enabled=1)
        check('2')
        check('')
        check('1', enabled=1, diag_on=0)
        check('1', enabled=1, width=512)
    print('PASS vinyl PNG opt-in, sampling and composite timing')
    return 0


if __name__ == '__main__':
    sys.exit(main())
