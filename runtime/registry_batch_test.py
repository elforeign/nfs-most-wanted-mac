#!/usr/bin/env python3
"""The game's video-settings batch (19-20 RegSetValueEx, then RegCloseKey) saves registry.txt once, not per value.

Run: $TOOLS_PY -I runtime/registry_batch_test.py   (offline: no game, profile or game folder)
Each durable save of registry.txt (two F_FULLFSYNC and a directory sync) held the game thread ~11.5 ms; leaving the
Video options wrote 20 values, a 230 ms frame. Compiles runtime/boot.c's "registry batch" block in a scratch harness
with a counting reg_save, writes 20 values and closes the key.
Recorded: current code 1 save per batch (PASS); the pinned pre-fix reg_set 20 saves (FAIL, exit 1).
After a failed commit, the old flush retries 0/1 and the fixed flush retries 1/1.
Also checks that RegCloseKey and ExitProcess flush the batch in boot.c.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent

# Pre-fix reg_set (it saved on every value), pinned by hash.
OLD_REG_SET = '''static void reg_set(uint32_t h, const char *name, uint32_t type, uint32_t data, uint32_t size) {
    const char *key = reg_key(h);
    if (!key) return;
    RegValue *v = reg_find(h, name);
    if (!v) for (unsigned i = 0; i < 64; ++i) if (!reg_vals[i].key) { v = &reg_vals[i]; v->key = key; snprintf(v->name, sizeof v->name, "%s", name ? name : ""); break; }
    if (!v) return;
    v->type = type;
    if (type == 4) v->dw = M32(data);
    else { uint32_t n = size < sizeof v->sz ? size : sizeof v->sz - 1; memcpy(v->sz, G + data, n); v->sz[n] = 0; }
    if (!getenv("BOOT_REG")) reg_save();          // BOOT_REG runs don't overwrite the saved settings
}
static void reg_flush(void) {}
'''
OLD_SHA = '3137b5848066e1e082ed2b05df36204a8898d7b766d29064102caf9a609c5bd5'

PREFIX = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
%s
static const char *reg_keys[] = {NULL, "HKLM\\Software\\EA Games\\Game"};
static RegValue reg_vals[64];
static uint8_t G[4096];
#define M32(a) (*(uint32_t *)(G + (a)))
static int saves, attempts, fail_save;
static int reg_save(void) { ++attempts; if (fail_save) return 0; ++saves; return 1; }
static const char *reg_key(uint32_t h) { return h == 0x3004u ? reg_keys[1] : NULL; }
static RegValue *reg_find(uint32_t h, const char *name) {
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i)
        if (!strcasecmp(reg_vals[i].key, reg_key(h)) && !strcasecmp(reg_vals[i].name, name)) return &reg_vals[i];
    return NULL;
}
'''
MAIN = r'''
int main(int argc, char **argv) {
    unsetenv("BOOT_REG");
    if (argc>1) {
        M32(64)=42; reg_set(0x3004u,"g_Retry",4,64,4);
        fail_save=1; reg_flush();
        if (attempts!=1 || saves) return 2;
        fail_save=0; reg_flush(); reg_flush();
        printf("successful retries=%d/1 attempts=%d\n",saves,attempts);
        return !(saves==1 && attempts==2 && reg_find(0x3004u,"g_Retry")->dw==42);
    }
    for (uint32_t i = 0; i < 20; ++i) {                 // FUN_006C1F10 (Most Wanted) / 00711BFB..00711E08 (Carbon)
        char name[32]; snprintf(name, sizeof name, "g_Setting%u", i);
        M32(64) = i;
        reg_set(0x3004u, name, 4, 64, 4);
    }
    reg_flush();                                        // RegCloseKey
    int values = 0;
    for (unsigned i = 0; i < 64 && reg_vals[i].key; ++i) values += reg_vals[i].dw == (uint32_t)i;
    printf("saves per 20-value batch: %d (values kept %d/20)\n", saves, values);
    return saves == 1 && values == 20 ? 0 : 1;
}
'''


OLD_FLUSH = '''static void reg_flush(void) {
    if (!reg_dirty) return;
    reg_dirty = 0;
    reg_save();
}'''
assert hashlib.sha256(OLD_FLUSH.encode()).hexdigest() == '256281357e90220e8cf8f4e1cef0218e64438b8da8dc390041fc1ef77627ffe2'


def build_run(tmp, name, body, typedef, args=()):
    src = tmp / (name + '.c')
    src.write_text(PREFIX % typedef + body + MAIN)
    exe = tmp / name
    r = subprocess.run(['clang', '-O1', '-Wall', '-Wno-unused-function', str(src), '-o', str(exe)],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0, r.stdout
    p = subprocess.run([str(exe), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.strip()


def main():
    boot = (ROOT / 'runtime/boot.c').read_text()
    typedef = re.search(r'^typedef struct \{ const char \*key;.*\} RegValue;$', boot, re.M).group(0)
    block = boot.split('// BEGIN registry batch\n', 1)[1].split('// END registry batch', 1)[0]
    assert hashlib.sha256(OLD_REG_SET.split('static void reg_flush')[0].encode()).hexdigest() == OLD_SHA
    failures = 0
    hooks = [('RegCloseKey', r'"RegCloseKey"\)\) \{ reg_flush\(\);'),
             ('ExitProcess', r'ExitProcess\(%u\) after %u import calls\\n", arg\(c, 0\), import_calls\);\n\s*reg_flush\(\);')]
    for what, pattern in hooks:
        ok = re.search(pattern, boot) is not None
        failures += not ok
        print('%s flushes the batch: %s' % (what, 'PASS' if ok else 'FAIL'))
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        code, out = build_run(tmp, 'current', block, typedef)
        failures += code != 0
        print('current: %s  %s' % (out, 'PASS' if code == 0 else 'FAIL'))
        code, out = build_run(tmp, 'old', OLD_REG_SET, typedef)
        failures += code == 0
        print('pinned pre-fix control: %s  %s' % (out, 'FAIL as expected' if code else 'UNEXPECTED PASS'))
        old_flush = block.split('static void reg_flush(void)',1)[0]+OLD_FLUSH
        for name, body, expected in [('retry',block,0),('old-flush',old_flush,1)]:
            code, out = build_run(tmp,name,body,typedef,('retry',))
            print(f'{name}: {out}')
            failures += code!=expected or f'successful retries={1-expected}/1' not in out
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
