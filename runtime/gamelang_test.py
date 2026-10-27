#!/usr/bin/env python3
"""Offline test of the game's text language (old public issues #7, #12): runtime/settings_port.c's "offline gamelang" block
(pack scan, the pick, the F10 Language row) with settings.c, on scratch game-folder fixtures. Never starts the game; the
player's game folder is only listed (read-only) when NFS_GAME is set.
Run: $TOOLS_PY -I runtime/gamelang_test.py
Checks: German.bin + English.bin with the Mac in German -> German; English only with the Mac in German -> English US and no
row; English only / Auto on an English Mac -> English US; a German-only folder -> German; the F10 choice wins over the Mac;
a saved choice whose pack is gone -> Auto; case-insensitive folder and file names; empty .bin files don't count; Japanese
Mac (no such text in this exe) falls through to the next preferred language; the owner's folder with an English Mac ->
English US with the row listed.
Result (2026-10-26): PASS, 0 failures. Broken control (the pick hard-coded to English US, i.e. 4.0's boot.c:647): FAIL,
5 failures."""
from pathlib import Path
import os, re, subprocess, sys, tempfile
R = Path(__file__).resolve().parent
src = (R / 'settings_port.c').read_text()
block = re.search(r'// BEGIN offline gamelang.*?\n(.*?)// END offline gamelang', src, re.S)[1]

HARNESS = r'''
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include "settings.h"
int diag_on = 0;
const char *game_dir;
static const char *saved_choice;
const char *app_pref(const char *key) { return !strcmp(key, "game_language") ? saved_choice : NULL; }
void app_pref_set(const char *key, const char *value) { (void)key; (void)value; }
void app_pref_batch_begin(void) {}
void app_pref_batch_end(void) {}
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
@BLOCK@
static int failures;
#define CHECK(x, ...) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s: ", __LINE__, #x); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); ++failures; } } while (0)
static const char *pick(const char *dir, const char *m0, const char *m1, const char *choice) {
    const char *mac[2] = {m0, m1};
    return kGameLangs[gamelang_pick(gamelang_packs(dir), mac, m1 ? 2 : 1, choice)].reg;
}
static int listed(void) {
    Setting *rows[64];
    int n = settings_rows(SET_GAMEPLAY, rows, 64);
    for (int i = 0; i < n; ++i) if (!strcmp(rows[i]->id, "gameplay.language")) return 1;
    return 0;
}
int main(int argc, char **argv) {
    const char *de_en = argv[1], *en = argv[2], *de = argv[3], *mixed = argv[4], *owner = argc > 5 ? argv[5] : NULL;
    const char *got;
    got = pick(de_en, "de-DE", "en-US", "auto");
    CHECK(!strcmp(got, "German"), "German.bin present, Mac de -> %s", got);
    got = pick(en, "de-DE", NULL, "auto");
    CHECK(!strcmp(got, "English US"), "English only, Mac de -> %s", got);
    got = pick(en, "en-US", NULL, "auto");
    CHECK(!strcmp(got, "English US"), "English only, Mac en -> %s", got);
    got = pick(de_en, "en-GB", "de-DE", "auto");
    CHECK(!strcmp(got, "English US"), "both, Mac en-GB first -> %s", got);
    got = pick(de_en, "ja-JP", "de-DE", "auto");
    CHECK(!strcmp(got, "German"), "both, Mac ja then de -> %s", got);
    got = pick(de, "en-US", NULL, "auto");
    CHECK(!strcmp(got, "German"), "German only, Mac en -> %s", got);
    got = pick(de_en, "en-US", NULL, "German");
    CHECK(!strcmp(got, "German"), "F10 German, Mac en -> %s", got);
    got = pick(en, "en-US", NULL, "German");
    CHECK(!strcmp(got, "English US"), "F10 German saved, pack gone -> %s", got);
    got = pick(mixed, "fr-FR", NULL, "auto");
    CHECK(!strcmp(got, "French"), "lower-case folder / upper-case FRENCH.BIN, Mac fr -> %s", got);
    got = pick(mixed, "it-IT", NULL, "auto");
    CHECK(!strcmp(got, "English US"), "empty Italian.bin doesn't count, Mac it -> %s", got);
    got = pick("/nonexistent", "de-DE", NULL, "auto");
    CHECK(!strcmp(got, "English US"), "no LANGUAGES folder -> %s", got);
    // The row: English-only lists nothing; two packs list Auto + both, and a saved choice loads.
    settings_init();
    settings_language_setup(en);
    CHECK(!listed(), "English-only install lists the Language row");
    settings_language_setup(de_en);
    CHECK(listed() && s_language.nchoices == 3 && s_language.category == SET_GAMEPLAY && s_language.restart,
          "two packs: row listed %d, %d choices", listed(), s_language.nchoices);
    CHECK(s_language.value == 0 && !strcmp(lang_values[s_language.value], "auto"), "default is Auto (%d)", s_language.value);
    if (owner) {
        unsigned p = gamelang_packs(owner);
        got = pick(owner, "en-US", NULL, "auto");
        CHECK(!strcmp(got, "English US"), "owner's folder (packs %#x), Mac en-US -> %s", p, got);
        printf("owner's folder: packs %#x, Mac en-US Auto -> %s\n", p, got);
    }
    printf("%d failures\n", failures);
    return failures != 0;
}
'''

def fixture(root, files):
    for rel, size in files:
        f = Path(root) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'\0' * size)
    return str(root)

def run(d, body, tag):
    c = Path(d) / f'{tag}.c'
    c.write_text(HARNESS.replace('@BLOCK@', body))
    exe = Path(d) / tag
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-I', R, '-I', R.parent / 'recomp',
                    '-o', exe, c, R / 'settings.c'], check=True)
    args = [fixture(Path(d) / 'de_en', [('LANGUAGES/German.bin', 64), ('LANGUAGES/English.bin', 64), ('LANGUAGES/Labels.bin', 64)]),
            fixture(Path(d) / 'en', [('LANGUAGES/English.bin', 64), ('LANGUAGES/Labels.bin', 64), ('SOUND/SPEECH/copspeech.big', 64)]),
            fixture(Path(d) / 'de', [('LANGUAGES/German.bin', 64)]),
            fixture(Path(d) / 'mixed', [('languages/FRENCH.BIN', 64), ('languages/english.bin', 64), ('languages/Italian.bin', 0)])]
    g = os.environ.get('NFS_GAME')
    if g and Path(g, 'LANGUAGES').is_dir(): args.append(g)
    return subprocess.run([exe] + args, capture_output=True, text=True)

with tempfile.TemporaryDirectory() as d:
    r = run(d, block, 't')
    print(r.stdout.strip()); print(r.stderr.strip())
    if r.returncode != 0: sys.exit('gamelang_test: FAIL')
    # Broken control: the language hard-coded to English US, as 4.0 shipped (boot.c's fixed registry value).
    broken = re.sub(r'(static int gamelang_pick\([^)]*\) \{)', r'\1\n    if (packs != 0xdead) return 0;', block, count=1)
    assert broken != block
    b = run(d, broken, 'b')
    nb = re.search(r'(\d+) failures', b.stdout)
    print(f'broken control (hard-coded English US): {nb[1] if nb else "?"} failures')
    if b.returncode == 0: sys.exit('gamelang_test: FAIL (the broken control passed)')
print('gamelang_test: PASS')
