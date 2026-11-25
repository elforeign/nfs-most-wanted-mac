"""Physical pad -> keyboard -> pad, with connection changes between samples.
Baseline main: MW 3 source transitions / 0 connection-only, Carbon 6 / 3.
Fixed exports: exactly 3 transitions per game; Carbon prompt-state-only control: 6.
The prompt-state-only reader is a SHA-256-pinned original function fragment.
Covers runtime/blackbox_input.c, runtime/blackbox.h, runtime/blackbox.c,
runtime/blackbox_mw.c, recomp/private_runtime/carbon_blackbox.c,
runtime/padprompts.c, recomp/private_runtime/carbon_padprompts.m,
runtime/user32.m, recomp/private_runtime/carbon_pad_layout.c,
recomp/private_runtime/features/pad_layout.json, runtime/runtime_sources.json, runtime/docs/input.md,
runtime/docs/blackbox.md, recomp/private_runtime/features/padprompts.json.
"""
from pathlib import Path
import hashlib
import os
import re
import runpy
import statistics
import subprocess
import tempfile

R = Path(__file__).resolve().parent
ROOT = R.parent
function = runpy.run_path(str(R / 'blackbox_test.py'))['function']


def provider(game):
    path = R / 'padprompts.c' if game == 'mw' else ROOT / 'recomp/private_runtime/carbon_padprompts.m'
    text = path.read_text()
    if game == 'mw':
        section = text[text.index('static int pad_lost;'):text.index('// The game\'s own pointer')]
        # mode() needs this declaration; the art itself is outside this fixture.
        section = 'static int env_mode=-1;\n' + section
    else:
        section = text[text.index('static _Atomic int active_pad'):text.index('static int console_layout')]
    return section


PRE = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <strings.h>
#include <math.h>
static int diag_on;
static void diag_event(const char *fmt, ...) { (void)fmt; }
static void user32_hide_pointer(void) { }
static int have_art(void) { return 1; }
static int carbon_addons_host_osk_pad(int p,unsigned b,int u,int d,int l,int r,
                                    float x,float y,int c,int xb) {
    (void)p;(void)b;(void)u;(void)d;(void)l;(void)r;(void)x;(void)y;(void)c;(void)xb;
    return 0;
}
'''
# Original Carbon reader: the deliberate control must reintroduce its semantics.
PROMPT_ONLY = 'int prompts_last_input(void) { return atomic_load(&active_pad); }'
PROMPT_SHA = 'd4a23b3846363056f506d1f0f5f53d63e6b883c5164179ce454a40974c8ef7d7'


def observation(game):
    text = (R / 'user32.m').read_text()
    start = text.index('static int observed_pad_layout;')
    end = text.index('static int pad_layout_xbox(void)', start)
    common = r'''
static const char *preference="xbox";
static unsigned preference_reads;
static const char *app_pref(const char *key) { (void)key; ++preference_reads; return preference; }
'''
    if game == 'mw':
        return (common + 'static int script_layout=-1, pad_kinds[2];\n'
                + 'static int kind_native(int kind) { return kind==1; }\n'
                + text[start:end] + function(text, 'static int pad_layout_xbox(')
                + '\nstatic void observe_layout(void) { pad_layout_xbox(); }\n')
    carbon = (ROOT / 'recomp/private_runtime/carbon_pad_layout.c').read_text()
    setup = r'''
#include "carbon_pad_layout.h"
enum { PAD_KIND_DUALSENSE=1, PAD_KIND_DUALSHOCK4=2, PAD_KIND_EDGE=3, PAD_KIND_OTHER=4 };
typedef struct { int value; } Setting;
static int pad_identity_n(int pad,char *name,size_t size) { (void)pad;(void)name;(void)size;return PAD_KIND_OTHER; }
static Setting *settings_find(const char *name) { (void)name;return NULL; }
static void settings_dump(const char *path) { (void)path; }
static const char *diag_dir;
'''
    cache = carbon[carbon.index('static _Atomic int observed_layout;'):carbon.index('int carbon_pad_layout_parse(')]
    return (common + setup + cache + function(carbon, 'int carbon_pad_layout_parse(')
            + function(carbon, 'const char *carbon_pad_layout_origin(')
            + function(carbon, 'CarbonPadLayoutChoice carbon_pad_layout_resolve(')
            + function(carbon, 'int carbon_pad_layout_get(') + text[start:end]
            + '\nstatic void observe_layout(void) { carbon_pad_layout_get(); }\n')


RECORDER = r'''
#include "blackbox.h"
static uint64_t virtual_ns=100000000000ull;
static int fixture_clock(clockid_t id, struct timespec *t) {
    (void)id; t->tv_sec=virtual_ns/1000000000ull;t->tv_nsec=virtual_ns%1000000000ull;return 0;
}
#define clock_gettime fixture_clock
#include "blackbox.c"
#undef clock_gettime
void blackbox_mw_fe(uint64_t frame,uint32_t flow,uint64_t now) { (void)frame;(void)flow;(void)now; }
void blackbox_mw_cinematics(uint64_t frame,uint64_t now) { (void)frame;(void)now; }
void carbon_blackbox_fe(uint64_t frame,uint32_t flow,uint64_t now) { (void)frame;(void)flow;(void)now; }
void carbon_blackbox_cinematics(uint64_t frame,uint64_t now) { (void)frame;(void)now; }
#include "adapter.inc"
#ifdef BENCH
#define blackbox_game_frame frame_without_input
#include "adapter_without.inc"
#undef blackbox_game_frame
static double time_frames(void (*adapter)(uint64_t,uint32_t,const BlackboxTiming *)) {
    const unsigned frames=500000;
    uint64_t start=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    for(unsigned i=0;i<frames;++i) { virtual_ns+=4166667;adapter(i,3,NULL); }
    return (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)-start)/frames;
}
int main(void) {
    prompts_pad_seen(1);observe_layout();prompts_input(1);
    time_frames(frame_without_input);time_frames(blackbox_game_frame);
    for(int i=0;i<9;++i) {
        double before,after;
        if(i%2) {after=time_frames(blackbox_game_frame);before=time_frames(frame_without_input);}
        else {before=time_frames(frame_without_input);after=time_frames(blackbox_game_frame);}
        printf("%.3f %.3f\n",before,after);
    }
    return 0;
}
#else
static uint64_t frame_number;
static void step(void) {
    unsigned reads=preference_reads;
    virtual_ns+=100000000;
    blackbox_game_frame(++frame_number,3,NULL);
    assert(reads==preference_reads); /* Sampling never reads preferences. */
}
static void output(const char *path,BlackboxTrigger trigger) {
    FILE *f=fopen(path,"w");assert(f);blackbox_export_at(f,trigger,virtual_ns);fclose(f);
}
int main(int argc,char **argv) {
    diag_on=argc>1?atoi(argv[1]):0;
    step();
    prompts_pad_seen(1); observe_layout();step();
#ifdef CARBON
    assert(prompts_last_input()==1); /* UI still follows the connection default. */
#endif
    prompts_pad_seen(0);step();
#ifdef CARBON
    assert(prompts_last_input()==0);
#endif
    prompts_pad_seen(1);step();
    prompts_input(1);step();
    prompts_input(1);step(); /* Held pad activity is not a source transition. */
    prompts_input(0);step();
    prompts_input(1);step();
    /* Mouse jitter remains pad input until the existing 16-point threshold. */
    for(int i=0;i<15;++i)prompts_input(-1);
    assert(prompts_physical_input()==1);
    prompts_input(-1);assert(prompts_physical_input()==0);
    prompts_input(1); /* Restore before sampling: only observed source changes are recorded. */
    prompts_pad_seen(0);
#ifdef CARBON
    osk_pad(0,0,0,0,0,0,0,0,0,0);
    assert(prompts_last_input()==0); /* Disconnection still changes only UI state. */
#endif
    step();
    preference="playstation";observe_layout();step();
#ifdef CARBON
    preference="pc";observe_layout();step();
#endif
    blackbox_menu_at(1,virtual_ns);
    /* Later keyboard input cannot replace the history frozen at menu opening. */
    prompts_input(0);step();virtual_ns+=80000000000ull;
    output("f10.txt",BB_F10);
    blackbox_menu_at(0,virtual_ns);
    prompts_input(1);step();
    virtual_ns+=40000000000ull;step();
    for(unsigned i=0;i<4097;++i)blackbox_record_package(0,i,1,0,frame_number,virtual_ns);
    output("hang.txt",BB_HANG);
    if(!blackbox_enabled()) {
        assert(!atomic_load(&rings[0].head) && !atomic_load(&rings[2].head));
    }
    return 0;
}
#endif
'''


def compile_fixture(work, source, bench=False):
    binary = work / ('bench' if bench else 'fixture')
    path = work / 'fixture.c'
    path.write_text(source)
    command = ['clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-Wno-unused-variable', '-I'+str(ROOT / 'recomp/private_runtime'), '-I'+str(R), '-I'+str(ROOT / 'recomp'), '-I'+str(work), str(path)]
    if bench:
        command.append('-DBENCH')
    else:
        command.append('-fsanitize=address,undefined')
    command.extend([str(R / 'blackbox_input.c'), '-o', str(binary)])
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-4000:]
    return binary


def main():
    assert hashlib.sha256(PROMPT_ONLY.encode()).hexdigest() == PROMPT_SHA
    with tempfile.TemporaryDirectory(prefix='blackbox-input-') as directory:
        work = Path(directory)
        environment = dict(os.environ)
        for key in ('NFS_BLACKBOX', 'NFS_PAD_LAYOUT', 'NFS_BUTTON_PROMPTS'):
            environment.pop(key, None)
        for game in ('mw', 'carbon'):
            text = provider(game)
            adapter = (R / 'blackbox_mw.c' if game == 'mw' else ROOT / 'recomp/private_runtime/carbon_blackbox.c').read_text()
            hook = '    blackbox_input(frame, now);\n'
            assert adapter.count(hook) == 1
            (work / 'adapter.inc').write_text(adapter)
            (work / 'adapter_without.inc').write_text(adapter.replace(hook, ''))
            prefix = ('#define NFS_GAME ' + ('NFS_GAME_PRIVATE' if game == 'carbon' else 'NFS_GAME_MW')
                      + '\n#include \"game_select.h\"\n' + PRE + ('\n#define CARBON\n' if game == 'carbon' else ''))
            source = prefix + text + observation(game) + RECORDER
            for broken in ((False, True) if game == 'carbon' else (False,)):
                variant = source
                if broken:
                    variant = source.replace(function(text, 'int prompts_physical_input('),
                                             PROMPT_ONLY.replace('prompts_last_input', 'prompts_physical_input'))
                binary = compile_fixture(work, variant)
                for diagnostics in (0, 1):
                    result = subprocess.run([str(binary), str(diagnostics)], cwd=work, env=environment,
                                            capture_output=True, text=True, timeout=30)
                    assert result.returncode == 0, result.stderr[-3000:]
                    exported = (work / 'f10.txt').read_text()
                    sources = re.findall(r'INPUT source=(\S+) source_changed=1', exported)
                    print(f'{game} diag={diagnostics} control={int(broken)}: source transitions={len(sources)}')
                    try:
                        assert sources == ['controller', 'keyboard/mouse', 'controller']
                    except AssertionError:
                        assert broken, exported[-3000:]
                        print('prompt-state-only control: FAIL (expected exactly 3)')
                    else:
                        assert not broken, 'broken control passed the acceptance check'
                    if broken:
                        assert len(sources) == 6
                    else:
                        assert re.findall(r'f(\d+) INPUT .*source_changed=1', exported) == ['5', '7', '8']
                        assert 'snapshot=menu snapshot_age=80.100s' in exported
                        layout = 'pc' if game == 'carbon' else 'playstation'
                        assert 'connected=0 layout=' + layout in exported
                        hang = (work / 'hang.txt').read_text()
                        assert 'INPUT_CONTEXT INPUT source=controller source_changed=1 connected=0 layout=' + layout in hang
                        assert re.search(r'INPUT_CONTEXT.*unchanged_for=40\.100s', hang)
                if not broken:
                    result = subprocess.run([str(binary), '0'], cwd=work, env=dict(environment, NFS_BLACKBOX='0'),
                                            capture_output=True, text=True, timeout=30)
                    assert result.returncode == 0, result.stderr[-3000:]
                    assert 'disabled NFS_BLACKBOX=0' in (work / 'f10.txt').read_text()
            binary = compile_fixture(work, source, bench=True)
            result = subprocess.run([str(binary)], cwd=work, env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr[-3000:]
            pairs = [tuple(map(float, line.split())) for line in result.stdout.splitlines()]
            before, after = (statistics.median(pair[i] for pair in pairs) for i in (0, 1))
            delta = statistics.median(after - before for before, after in pairs)
            print(f'{game} paired microbench: without={before:.1f} with={after:.1f} delta={delta:.1f} ns/frame (9 x 500000)')
            # Job 1 measured 0.82 ms same-arm live spread; this is an offline cost bound.
            assert delta < 820000
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
