"""HDR F10 production row + registry + float-scene startup selection, no game/GPU.
Observed CPU proof: On/Off/On x32 = 96 requests, 0 premature mode changes;
96 fresh registry boots export the requested mode. Default Auto, saved Auto/On/Off,
environment locks, and split-screen fallback are checked. Broken restart row:
1 failure; broken HDR-implies-float: 2 failures (absent and Off float override).
Auto fake potential 1 -> SDR, 7 -> HDR: 0 failures; old decision 4 failures;
old two-choice default 96 failures, versus production 0.
Native layer/drawable formats and H=1 capture bytes: hdr_output_test.py.
Case agreement (private/public): 8 EDR letter-case variants, 0 registry/renderer/
float mismatches each. Case-sensitive shared-parser control: 3 failures for EDR
(renderer + absent/Off float overrides), normal 0; exit 1 versus 0.
"""
from pathlib import Path
import os
import itertools
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
port = (RT / 'settings_port.c').read_text()
row = port[port.index('static const char *const kHdr[]'):port.index('static void hdr_status(')]
assert 'REG(s_hdr);' in port
guard = (RT / 'hdrclip.m').read_text()
selection = guard[guard.index('void hdrclip_init(void) {'):guard.index('    const char *e=diag_on ?')]
selection = selection.replace('void hdrclip_init(void)', 'static void select_float_scene(void)') + '}\n'
presenter = (RT / 'render_present.m').read_text()
request = re.search(r'^    hdr_output_requested = .*;', presenter, re.M)[0]
request = 'static int renderer_request(void) { int hdr_output_requested;\n' + request + '\nreturn hdr_output_requested; }\n'
policy = (RT / 'hdr_output.h').read_text()
stubs = (RT / 'settings_look_test.c').read_text()
stubs = stubs[stubs.index('#include <stdio.h>'):stubs.index('static int failures;')]

HARNESS = r'''
#include "hdr_output.h"
static int float_scene_on;
static float potential=7;
float render_hdr_display_potential(void) { return potential; }
@ROW@
@SELECT@
@REQUEST@
static int failures;
#define CHECK(x) do { if(!(x)){fprintf(stderr,"FAIL: %s\n",#x);++failures;} }while(0)
int main(int argc, char **argv) {
    if(argc!=3)return 2;
    if(strcmp(argv[1],"-"))app_pref_set("graphics.hdr_output",argv[1]);
    if(strcmp(argv[2],"-"))setenv("NFS_HDR_OUTPUT",argv[2],1);
    settings_add(&s_hdr);settings_init();
    int initial=settings_get("graphics.hdr_output");
    const char *chosen=strcmp(argv[2],"-")?argv[2]:argv[1];
    int expected=!strcasecmp(chosen,"edr")?2:!strcasecmp(chosen,"off")?1:0;
    CHECK(initial==expected);
    int requested=initial!=1;
    CHECK(renderer_request()==requested);
    if(initial==0) {
        potential=1;CHECK(!renderer_request());
        potential=7;CHECK(renderer_request());
    }
    CHECK(s_hdr.category==SET_PICTURE && !strcmp(s_hdr.label,"HDR output"));
    CHECK(s_hdr.def==0 && s_hdr.restart==1 && !s_hdr.apply && !s_hdr.hidden);
    CHECK(s_hdr.nchoices==3 && !strcmp(s_hdr.choices[0],"Auto") && !strcmp(s_hdr.choices[1],"Off") && !strcmp(s_hdr.choices[2],"On"));
    const char *mode=getenv("NFS_HDR_OUTPUT");
    CHECK(initial==0 ? !mode || !strcasecmp(mode,"auto") : mode && !strcasecmp(mode,initial==2?"edr":"off"));
    char boot_mode[16];snprintf(boot_mode,sizeof boot_mode,"%s",mode?mode:"");
    // HDR must enable float scene even with no standalone switch or explicit Off.
    unsetenv("NFS_FLOAT_SCENE");select_float_scene();CHECK(float_scene_on==requested);
    setenv("NFS_FLOAT_SCENE","off",1);select_float_scene();CHECK(float_scene_on==requested);
    setenv("NFS_PADS","2",1);select_float_scene();CHECK(!float_scene_on);
    unsetenv("NFS_PADS");setenv("NFS_FLOAT_SCENE","on",1);select_float_scene();CHECK(float_scene_on);
    app_pref_set("graphics.hdr_output",initial==2?"edr":initial==1?"off":"auto");
    unsigned requests=0,premature=0;
    if(s_hdr.locked) {
        CHECK(!settings_set(&s_hdr,!initial) && settings_get("graphics.hdr_output")==initial);
    } else for(int n=0;n<32;++n)for(int p=0;p<3;++p) {
        int wanted=p;
        settings_set(&s_hdr,wanted);
        CHECK(settings_get("graphics.hdr_output")==wanted);
        CHECK(!strcmp(app_pref("graphics.hdr_output"),wanted==2?"edr":wanted==1?"off":"auto"));
        mode=getenv("NFS_HDR_OUTPUT");
        premature+=strcmp(mode?mode:"",boot_mode)!=0;
        ++requests;
    }
    CHECK(!premature);
    printf("hdr_settings: boot=%d requests=%u premature=%u failures=%d\n",initial,requests,premature,failures);
    return failures?1:0;
}
'''


def main():
    env = {k: v for k, v in os.environ.items() if k not in ('NFS_HDR_OUTPUT', 'NFS_FLOAT_SCENE', 'NFS_PADS')}
    with tempfile.TemporaryDirectory(prefix='hdr-settings-') as tmp:
        tmp = Path(tmp)
        source, exe = tmp / 'test.c', tmp / 'test'

        def build(row_source, float_source, public=False, policy_source=policy):
            (tmp / 'hdr_output.h').write_text(policy_source)
            source.write_text(stubs + HARNESS.replace('@ROW@', row_source).replace('@SELECT@', float_source).replace('@REQUEST@', request))
            result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-variable', *(['-DNFS_PUBLIC=1'] if public else []), '-I', str(RT), '-I', str(RT.parent / 'recomp'), str(source),
                str(RT / 'settings.c'), '-o', str(exe)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr[-4000:]

        def run(saved, override='-'):
            return subprocess.run([str(exe), saved, override], capture_output=True, text=True, env=env)

        build(row, selection)
        for saved, override in (('-', '-'), ('auto', '-'), ('off', '-'), ('edr', '-'), ('edr', 'off'), ('off', 'edr'), ('off', 'auto')):
            result = run(saved, override)
            assert result.returncode == 0, result.stdout + result.stderr[-2000:]
            print(result.stdout, end='')
        old_row=row.replace('{"Auto", "Off", "On"}', '{"Off", "On"}').replace('{"auto", "off", "edr"}', '{"off", "edr"}').replace('.nchoices = 3', '.nchoices = 2')
        build(old_row, selection)
        result=run('-')
        assert result.returncode==1, result.stdout+result.stderr
        print('hdr_settings: old default '+result.stdout.strip()+' rejected PASS')
        broken_policy=policy.replace('render_hdr_display_potential()>1', '0')
        assert broken_policy!=policy
        build(row, selection, policy_source=broken_policy)
        result=run('-')
        assert result.returncode==1, result.stdout+result.stderr
        print('hdr_settings: old Auto decision '+result.stdout.strip()+' rejected PASS')
        build(row, selection)
        # Fresh processes model the restart boundary: exported switches feed the
        # actual renderer initialization covered by the native presenter harness.
        for _ in range(32):
            for saved in ('edr', 'off', 'edr'):
                result = run(saved)
                assert result.returncode == 0, result.stdout + result.stderr[-2000:]
        print('hdr_settings: On/Off/On x32 fresh boots=96 mode_mismatches=0 PASS')
        broken = row.replace('.restart = 1', '.restart = 0')
        assert broken != row
        build(broken, selection)
        result = run('-')
        assert result.returncode == 1 and 'failures=1' in result.stdout, result.stdout + result.stderr[-2000:]
        print('hdr_settings: broken restart row failures=1 rejected PASS')
        broken = selection.replace('|| nfs_hdr_output_requested()', '|| (nfs_hdr_output_requested() && 0)')
        assert broken != selection
        build(row, broken)
        result = run('edr')
        assert result.returncode == 1 and 'failures=2' in result.stdout, result.stdout + result.stderr[-2000:]
        print('hdr_settings: broken HDR float requirement failures=2 rejected PASS')
        for public in (False, True):
            build(row, selection, public)
            for letters in itertools.product(*zip('edr', 'EDR')):
                result = run('off', ''.join(letters))
                assert result.returncode == 0, result.stdout + result.stderr[-2000:]
            print(f'hdr_settings: public={int(public)} case_variants=8 mismatches=0 PASS')
            broken_policy = policy.replace('strncasecmp(value,"edr",3)', 'strncmp(value,"edr",3)')
            assert broken_policy != policy
            build(row, selection, public, broken_policy)
            result = run('off', 'EDR')
            assert result.returncode == 1 and 'failures=' in result.stdout, result.stdout + result.stderr[-2000:]
            print(f'hdr_settings: public={int(public)} case-sensitive control {result.stdout.strip()} rejected PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
