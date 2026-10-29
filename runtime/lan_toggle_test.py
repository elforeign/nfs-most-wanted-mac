#!/usr/bin/env python3
"""




"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
port = (ROOT / "runtime/settings_port.c").read_text()
row = re.search(r'static Setting s_lan = \{.*?\};', port, re.S).group()
assert "REG(s_lan)" in port, "LAN row must be registered"
fe = (ROOT / "runtime/fe_console.c").read_text()
lan = re.search(r'static int lan_items\(void\) \{.*?\n\}', fe, re.S).group()
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"
int diag_on;
const char *game_dir;
static char saved[16];
const char *app_pref(const char *key) { return !strcmp(key, "gameplay.lan") ? saved : NULL; }
void app_pref_set(const char *key, const char *value) {
    if (!strcmp(key, "gameplay.lan")) snprintf(saved, sizeof saved, "%s", value);
}
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
@ROW@
@LAN@
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    snprintf(saved, sizeof saved, "%s", argv[1]);
    unsetenv("NFS_LAN");
    if (strcmp(argv[2], "unset")) setenv("NFS_LAN", argv[2], 1);
    settings_add(&s_lan);
    settings_init();
    if (s_lan.category != SET_GAMEPLAY || s_lan.order != 80 || s_lan.def != 0 ||
        !s_lan.restart || strcmp(s_lan.label, "LAN play (beta)")) return 3;
    int expected = atoi(argv[3]);
    if (lan_items() != expected) return 4;
    if (!strcmp(argv[2], "unset")) {
        int old = s_lan.boot_value;
        if (!settings_set(&s_lan, !old) || s_lan.value != !old ||
            s_lan.boot_value != old || lan_items() != expected ||
            strcmp(saved, old ? "off" : "on")) return 5;
    } else if (argv[2][0] && settings_set(&s_lan, !s_lan.value)) return 6;
    return 0;
}
'''
cases = [(saved, env, int(env == "1") if env != "unset" else int(saved == "on"))
         for saved in ("", "off", "on")
         for env in ("unset", "0", "1", "", "invalid", "on")]
with tempfile.TemporaryDirectory(prefix="lan-toggle-") as scratch:
    scratch = Path(scratch)
    counts = []
    for broken in (False, True):
        source = scratch / "test.c"
        binary = scratch / "test"
        body = lan.replace('return s && s->boot_value == 1;', '(void)s; return 0;') if broken else lan
        assert not broken or body != lan
        source.write_text(harness.replace("@ROW@", row).replace("@LAN@", body))
        build = subprocess.run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                "-fsanitize=address,undefined", "-g", "-I", str(ROOT / "runtime"),
                                "-I", str(ROOT / "recomp"), str(source),
                                str(ROOT / "runtime/settings.c"), "-o", str(binary)],
                               capture_output=True, text=True)
        if build.returncode:
            raise SystemExit(build.stderr[-5000:])
        failed = sum(subprocess.run([str(binary), saved, env, str(expected)],
                                    capture_output=True).returncode != 0
                     for saved, env, expected in cases)
        counts.append(failed)
        print(f"{'setting-ignored mutant' if broken else 'production'}: {len(cases)-failed}/{len(cases)} passed ({failed} failures)")
    if counts != [0, 1]:
        raise SystemExit(f"FAIL: expected production 0 failures and mutant 1 failure, got {counts}")
print("lan_toggle_test: PASS; setting-ignored mutant rejected")
