#!/usr/bin/env python3
"""Supplied game labels survive the actual SearchForString bridge.
Fixed: 8/8 German, English, missing-label and existing-swap cases pass.
Current branch before fix: 5/8 pass; German Beschleunigen is replaced in
both controller/keyboard Controls paths, and supplied English is replaced.
The pinned unconditional override repeats those 3 failures in every run.
"""
from pathlib import Path
import hashlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Reviewed unconditional override, kept as a small executable source fragment.
OLD_OVERRIDE = '''    uint32_t action_text = prompts_action_text(hash);
    // SearchForString's guest return consumes its 4-byte return address.
    if (action_text) { c->eax = action_text; c->esp += 4; return; }
'''
OLD_SHA256 = '6f743b10ca90945eff6c463cd828e58e893b6de3433dbce42a7b3bec7df44568'
HARNESS = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint32_t eax, edx, esp; } Cpu;
static int diag_on, original_calls, supplied, swap_found;
static void diag_event(const char *fmt, ...) { (void)fmt; }
static int x360_post_chain_on(void) { return 0; }
static uint32_t gstr(const char *s) { (void)s; return 4; }
static const char *strings[] = {"", "Beschleunigen", "Accelerate (game)", "Accelerate", "Saving %s."};
uint32_t prompts_action_text(uint32_t h) { return h == 0x619B23B0u ? 3 : 0; }
uint32_t underglow_menu_text(uint32_t h) { (void)h; return 0; }
void f_0056BB80_orig(Cpu *c) {
    ++original_calls;
    c->eax = c->edx == 0x619B23B0u ? (uint32_t)supplied :
             c->edx == 0xA71ED258u && swap_found ? 4 :
             c->edx == 0x50D23EBDu ? 5 : 0;
    c->esp += 4;
}
@SOURCE@
static int failures, cases;
static void check(const char *name, uint32_t hash, int label, int swap, uint32_t want, int calls) {
    ++cases; supplied = label; swap_found = swap; original_calls = 0;
    Cpu c = {.edx = hash, .esp = 0x1000}; fe_console_search(&c);
    if (c.eax != want || c.esp != 0x1004 || (calls >= 0 && original_calls != calls) ||
        c.edx != (swap ? 0xA71ED258u : hash)) {
        ++failures;
        printf("FAIL %s: text=%s, stack delta=%u, lookups=%d\n", name,
               c.eax < 5 ? strings[c.eax] : "original saving", c.esp - 0x1000, original_calls);
    }
}
int main(void) {
    check("German controller Controls", 0x619B23B0u, 1, 0, 1, 1);
    check("German keyboard Controls", 0x619B23B0u, 1, 0, 1, 1);
    check("supplied English", 0x619B23B0u, 2, 0, 2, 1);
    check("missing action", 0x619B23B0u, 0, 0, 3, -1);
    check("cached missing action", 0x619B23B0u, 0, 0, 3, -1);
    check("unknown label", 0x1234u, 0, 0, 0, 1);
    check("existing swap", 0x50D23EBDu, 0, 1, 4, 1);
    check("other unknown label", 0x5678u, 0, 0, 0, 1);
    printf("game label preservation: %d/%d pass; %d failures\n", cases - failures, cases, failures);
    return failures != 0;
}
'''


def run(work, source, name):
    path, exe = work / (name + '.c'), work / name
    path.write_text(HARNESS.replace('@SOURCE@', source))
    build = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(path), '-o', str(exe)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr[-3000:]
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    print(name + ': ' + result.stdout.strip())
    return result


def main():
    assert hashlib.sha256(OLD_OVERRIDE.encode()).hexdigest() == OLD_SHA256
    source = (ROOT / 'runtime/fe_console.c').read_text().split('static struct { uint32_t from, to;', 1)[1]
    source = 'static struct { uint32_t from, to;' + source
    with tempfile.TemporaryDirectory(prefix='fe-console-labels-') as tmp:
        work = Path(tmp)
        current = run(work, source, 'current')
        if current.returncode:
            return current.returncode
        needle = '    uint32_t hash = c->edx;\n'
        assert source.count(needle) == 1
        broken = run(work, source.replace(needle, needle + OLD_OVERRIDE), 'reviewed-override')
        assert broken.returncode == 1 and '5/8 pass; 3 failures' in broken.stdout, broken.stdout
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
