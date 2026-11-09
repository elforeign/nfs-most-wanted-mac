#!/usr/bin/env python3
"""Old profile keys are tolerated by the production prefs and settings loaders.
Normal: 14 checks, 0 failures in each build; abort-on-retired-key control: 6 failures.
Scratch files only. HDR keeps its Auto default and a later saved row still loads.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
OLD_KEYS = ['graphics.upscale', 'graphics.' + 'shadow' + '_stable',
            'graphics.' + 'shadow' + '_near', 'graphics.' + 'frame' + '_phase_lock']
OLD_KEY = OLD_KEYS[0]  # Tolerant-load fixture: an existing profile may still carry this key.
PREFIX = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include "durable.h"
#include "settings.h"
static const char *root;
static const char *userdata_dir(void) { return root; }
const char *game_dir;
int diag_on;
void report_note(const char *fmt, ...) { (void)fmt; }
void diag_event(const char *fmt, ...) { (void)fmt; }
'''
MAIN = r'''
static Setting later = {.id="fixture.later", .type=SET_TOGGLE};
static int checks, failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; fprintf(stderr,"FAIL %s\n",#x); } } while(0)
int main(int argc, char **argv) {
    if(argc!=2)return 2;
    root=argv[1]; setenv("BOOT_USERDATA", root, 1);
    unsetenv("NFS_HDR_OUTPUT");
    char path[1100]; snprintf(path,sizeof path,"%s/app.txt",root);
    FILE *f=fopen(path,"w"); if(!f)return 2;
    fputs("OLD_KEY=spatial\nOLD_ROWSfixture.later=on\n",f); fclose(f);
    settings_add(&s_hdr); settings_add(&later); settings_init();
OLD_CHECKS
    CHECK(settings_count()==2);
    CHECK(settings_find("OLD_KEY")==NULL);
    CHECK(settings_get("graphics.hdr_output")==0);
    CHECK(s_hdr.boot_value==s_hdr.def && !s_hdr.saved);
    CHECK(!getenv("NFS_HDR_OUTPUT"));
    CHECK(settings_get("fixture.later")==1);
    CHECK(later.saved);
    CHECK(app_pref("OLD_KEY") && !strcmp(app_pref("OLD_KEY"),"spatial"));
    printf("retired key: checks=%d failures=%d\n",checks,failures);
    return failures ? 1 : 0;
}
'''.replace('OLD_KEY', OLD_KEY)
MAIN = MAIN.replace('OLD_ROWS', ''.join(key + '=on\\n' for key in OLD_KEYS[1:]))
MAIN = MAIN.replace('OLD_CHECKS', ''.join(
    f'    CHECK(settings_find("{key}")==NULL);\n'
    f'    CHECK(app_pref("{key}") && !strcmp(app_pref("{key}"),"on"));\n'
    for key in OLD_KEYS[1:]))


def main():
    app = (RT / 'app.m').read_text()
    prefs = app.split('// BEGIN offline prefs:', 1)[1].split('// END offline prefs', 1)[0]
    # Discard the marker's trailing description, leaving the exact production helpers.
    prefs = prefs.split('\n', 1)[1]
    port = (RT / 'settings_port.c').read_text()
    assert all(key not in port for key in OLD_KEYS)
    choices = re.search(r'static const char \*const kHdr\[\].*?;', port, re.S)[0]
    row = re.search(r'static Setting s_hdr = .*?};', port, re.S)[0]
    assert '.def = 0' in row and '"Auto", "Off", "On"' in choices
    anchor = '            snprintf(prefs[nprefs][0], sizeof prefs[0][0], "%s", pref_trim(line));'
    assert prefs.count(anchor) == 1
    broken = prefs.replace(anchor, f'            if (!strcmp(pref_trim(line), "{OLD_KEY}")) {{ fclose(f); return NULL; }}\n' + anchor)
    with tempfile.TemporaryDirectory(prefix='settings-retired-key-') as folder:
        tmp = Path(folder)
        for public in (False, True):
            for mutant in (False, True):
                src = tmp / 'test.c'
                src.write_text(PREFIX + (broken if mutant else prefs) + choices + row + MAIN)
                exe = tmp / 'test'
                cmd = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                       '-fsanitize=address,undefined', '-g', '-I', str(RT), '-I', str(RT.parent / 'recomp'),
                       str(src), str(RT / 'settings.c'), '-o', str(exe)]
                if public: cmd.insert(1, '-DNFS_PUBLIC=1')
                build = subprocess.run(cmd, capture_output=True, text=True)
                assert build.returncode == 0, build.stderr[-7000:]
                run = subprocess.run([str(exe), str(tmp)], capture_output=True, text=True, env=os.environ)
                assert run.returncode == int(mutant), run.stdout + run.stderr
                assert f'checks=14 failures={6 if mutant else 0}' in run.stdout, run.stdout + run.stderr
                print(f'public={int(public)} abort-control={int(mutant)}: {run.stdout.strip()}')
    print('settings_retired_key: PASS both builds; 2/2 abort controls rejected')


if __name__ == '__main__':
    main()
