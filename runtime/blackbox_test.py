"""Core numeric recorder and production F10/hang/stall hook proof (asset-free).
Fixed: 0 missing sentinel records / 12 exports; removed export hooks: 12 / 12.
31 virtual seconds at 240 fps, wrap, an 80-second F10 pause, diagnostics off/on,
concurrent snapshot/publication, deterministic contention and capacity loss.
The production snippets are compiled, not replaced by a second export implementation.
Offline auto-discovery: runtime/offline_tests.sh; selected by runtime/impact_tests.py.
Also covers runtime/blackbox_mw.c, runtime/report.m, runtime/portmenu.m, runtime/diag.c,
recomp/private_runtime/game_diagnostics.h, recomp/private_runtime/carbon_blackbox.c,
recomp/private_runtime/features/blackbox.json, runtime/runtime_sources.json,
runtime/switches.tsv and runtime/docs/blackbox.md. No private assets are required.
"""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'runtime'
WORK = ROOT / '.metal-cache' / 'blackbox-proof'


def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    level = 1
    i = brace + 1
    # Selected functions contain no braces in quoted strings/comments.
    while level:
        level += (text[i] == '{') - (text[i] == '}')
        i += 1
    return text[start:i]


PRELUDE = r'''
#include <assert.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "blackbox.h"
#include "game_select.h"
#include "present_image_diag.h"
void carbon_blackbox_fe(uint64_t frame, uint32_t flow, uint64_t now) { (void)frame; (void)flow; (void)now; }
void present_image_diag_tick(uint32_t ticks, uint32_t flow) { (void)ticks; (void)flow; }
static uint64_t virtual_ns = 100000000000ull;
static int fixture_clock(clockid_t id, struct timespec *t) {
    (void)id; t->tv_sec = virtual_ns / 1000000000ull;
    t->tv_nsec = virtual_ns % 1000000000ull; return 0;
}
#define clock_gettime fixture_clock
#include "blackbox.c"
#undef clock_gettime
#include "adapter.inc"
static int diag_on, exiting, idle_now, is_open, auto_reports;
static const char *diag_dir;
static uint64_t frame_now, last_frame_ns;
static pthread_t game_thread;                // diag_profile.h's, set by Carbon's diag_frame
static int have_game_thread;
static FILE *report_f;
typedef struct { int unused; } Cpu;
static uint64_t host_ticks(void) { return virtual_ns; }
static const char *userdata_dir(void) { return "."; }
static const char *log_dir(void) { return "."; }
static const char *log_name(void) { return "fixture"; }
static void sstr_pool_stats(uint32_t *a,uint32_t *b,uint32_t *c,uint32_t *d,uint32_t *e) { *a=*b=*c=*d=*e=0; }
static void write_backtrace(FILE *f) { (void)f; }
static int report_thread_backtraces(Cpu **c,uint32_t *t) { (void)c;(void)t;return 0; }
static void guest_state(FILE *f,Cpu *c,uint32_t t) { (void)f;(void)c;(void)t; }
static void dump_ring(FILE *f,int n) { (void)f;(void)n; }
static void diag_flush(void) {}
static void report_auto(const char *why,const char *path) { (void)why;(void)path;++auto_reports; }
static void diag_idle(int i) { idle_now=i; }
static void private_mix_frame(uint64_t f) { (void)f; }
#define PERF_RING 1024
static struct { float frame_ms,busy_ms;uint32_t draws,ticks; } perf_frames[PERF_RING];
#define PerfFrame __typeof__(perf_frames[0])
static uint64_t perf_nframes;
#include "hooks.inc"
static _Atomic int running;
static void *reader(void *unused) {
    (void)unused;
    while (atomic_load(&running)) {
        Snapshot *s=capture();assert(s);
        for(unsigned i=0;i<FRAMES;++i) if(s->frames[i].h.seq) {
            Frame *f=&s->frames[i];
            assert(f->valid==1 && f->timing.draws==132 && f->timing.ticks==1);
            assert(f->timing.busy_ms==0.75f && f->flow_since<=f->h.ns);
        }
        free(s);
    }
    return NULL;
}
static void frame(uint64_t n,uint32_t flow) {
    perf_frames[perf_nframes%PERF_RING]=(PerfFrame){4.166667f,0.75f,132,1};
    ++perf_nframes;frame_now=n;fixture_frame(n,flow);
}
int main(int argc,char **argv) {
    assert(argc==4);diag_on=atoi(argv[1]);int trigger=atoi(argv[2]);diag_dir=diag_on?argv[3]:NULL;
    if(!strcmp(argv[1],"off")) {
        frame(1,6);FILE *f=fopen("disabled.txt","w");blackbox_export(f,BB_F10);fclose(f);
        assert(atomic_load(&rings[0].head)==0);return 0;
    }
    /* Wrap both frame and state rings before the 31-second acceptance window. */
    for(unsigned i=0;i<18000;++i) { virtual_ns+=1000000;frame(i,3); }
    for(unsigned i=0;i<5000;++i) event(BB_HEARTBEAT,virtual_ns,17999,3,virtual_ns);
    for(unsigned i=0;i<7440;++i) {
        virtual_ns+=4166667;frame(18000+i,i>=480 && i<720 ? 77 : 6);
    }
    fixture_stamp(25439,132);assert(last_frame_ns==virtual_ns);
    fixture_menu(1);
    if(trigger==0) {
        for(unsigned i=0;i<19200;++i) { virtual_ns+=4166667;frame(25440+i,3); }
    } else virtual_ns+=80000000000ull;
    if(trigger==0) { FILE *f=fopen("blackbox.txt","w");assert(f);fixture_f10(f);fclose(f); }
    else { diag_report(trigger==1?"hang_1.txt":"stall_1.txt","fixture");assert(auto_reports==(trigger==1)); }
    fixture_menu(0);
    FILE *closed=fopen("closed.txt","w");blackbox_export(closed,BB_STALL);fclose(closed);
    /* A busy exporter cannot preserve a stale menu generation silently. */
    assert(!atomic_flag_test_and_set_explicit(&snapshot_gate,memory_order_acquire));
    fixture_menu(1);
    atomic_flag_clear_explicit(&snapshot_gate,memory_order_release);
    FILE *miss=fopen("snapshot-loss.txt","w");blackbox_export(miss,BB_F10);fclose(miss);
    fixture_menu(0);
    /* Publication while a snapshot reader copies slots: no producer waits. */
    pthread_t t;atomic_store(&running,1);assert(!pthread_create(&t,NULL,reader,NULL));
    for(unsigned i=0;i<40000;++i) { virtual_ns+=4166667;frame(50000+i,6); }
    atomic_store(&running,0);pthread_join(t,NULL);
    /* Force one busy slot: exactly one attempted publication must be counted. */
    uint64_t head=atomic_load(&rings[0].head),lost=atomic_load(&rings[0].lost);
    assert(acquire(&frames[head%FRAMES].gate));
    frame(90000,6);release(&frames[head%FRAMES].gate);
    assert(atomic_load(&rings[0].lost)==lost+1);
    virtual_ns+=4166667;frame(90001,6);
    FILE *f=fopen("loss.txt","w");blackbox_export_at(f,BB_STALL,virtual_ns);fclose(f);
    /* Frame rate beyond capacity must say coverage is short. */
    for(unsigned i=0;i<20000;++i) { virtual_ns+=1000000;frame(91000+i,6); }
    f=fopen("capacity.txt","w");blackbox_export_at(f,BB_STALL,virtual_ns);fclose(f);
    printf("storage=%zu snapshot=%zu lost=%llu\n",sizeof frames+sizeof presents+sizeof events+sizeof errors+sizeof rings+sizeof fe_context,
           sizeof(Snapshot),(unsigned long long)atomic_load(&rings[0].lost));
    return 0;
}
'''


def hooks(game, broken=False):
    report = (R / 'report.m').read_text()
    diag = (R / 'diag.c' if game == 'mw' else ROOT / 'recomp/private_runtime/game_diagnostics.h').read_text()
    capture = function(report, 'static ReportSnapshot *report_capture(')
    export = capture[capture.index('    BlackboxTrigger blackbox_trigger'):capture.index('    if (fclose(f))', capture.index('    BlackboxTrigger blackbox_trigger'))]
    frame_start = report.index('    uint64_t head =', report.index('void report_frame('))
    frame_end = report.index('    static uint32_t prev_causes', frame_start)
    menu = function((R / 'portmenu.m').read_text(), 'static void set_open(')
    menu = menu[menu.index('{') + 1:menu.index('    feedback_poll_pause();')]
    stamp = function((ROOT / 'recomp/private_runtime/game_diagnostics.h').read_text(), 'void diag_frame(')
    stamp = stamp[stamp.index('    __atomic_store_n(&frame_now'):stamp.rindex('}')]
    text = (function(diag, 'void diag_report(') + '\n'
            'static void fixture_f10(FILE *f) { const char *attach=NULL,*why=NULL;\n' + export + '\n}\n'
            'static void fixture_frame(uint64_t frame,uint32_t flow) {\n' + report[frame_start:frame_end] + '\n}\n'
            'static void fixture_menu(int o) {\n' + menu + 'is_open=o;\n}\n'
            'static void fixture_stamp(uint64_t frame,uint64_t draws) { (void)draws;\n' + stamp + '\n}\n')
    if broken:
        text = re.sub(r'blackbox_export\(f,.*?;', '(void)f;', text)
    return text


def main():
    if any(not (ROOT / p).is_file() for p in (
            'recomp/private_runtime/game_diagnostics.h', 'recomp/private_runtime/carbon_blackbox.c')):
        print('SKIP: private adapter source fixtures absent')
        sys.exit(3)
    WORK.mkdir(parents=True, exist_ok=True)
    # The ordinary native fixture separately exercises report_store and its sanitizer.
    source = (R / 'report.m').read_text()
    assert '"report", "blackbox"' in function(source, 'static int report_store(')
    assert source.index('ReportRedactBundle([NSString stringWithUTF8String:dir])') > source.index('"report", "blackbox"')
    assert 'blackbox.txt' in (R / 'report_privacy_test.m').read_text()
    missing = [0, 0]
    for game in ('mw', 'carbon'):
        adapter = R / 'blackbox_mw.c' if game == 'mw' else ROOT / 'recomp/private_runtime/carbon_blackbox.c'
        for broken in (False, True):
            build = WORK / (game + ('-broken' if broken else ''))
            build.mkdir(exist_ok=True)
            (build / 'adapter.inc').write_text(adapter.read_text())
            (build / 'hooks.inc').write_text(hooks(game, broken))
            (build / 'fixture.c').write_text(PRELUDE)
            cmd = ['clang', '-std=gnu11', '-O2', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                   '-DNFS_GAME=' + ('NFS_GAME_MW' if game == 'mw' else 'NFS_GAME_PRIVATE'),
                   '-Wno-unused-variable', '-fsanitize=address,undefined', '-I'+str(R), '-I'+str(build),
                   str(build / 'fixture.c'), '-o', str(build / 'fixture')]
            compiled = subprocess.run(cmd, capture_output=True, text=True)
            assert compiled.returncode == 0, compiled.stderr[-5000:]
            for diag_on in (0, 1):
                for trigger in range(3):
                    case = build / f'{diag_on}-{trigger}'
                    case.mkdir(exist_ok=True)
                    environment=dict(os.environ)
                    environment.pop('NFS_BLACKBOX',None)
                    if diag_on: environment['NFS_BLACKBOX']='1'
                    result = subprocess.run([str(build/'fixture'),str(diag_on),str(trigger),str(case)], cwd=case,
                                            capture_output=True,text=True,timeout=60,env=environment)
                    assert result.returncode == 0, result.stderr[-3000:]
                    name = ('blackbox.txt','hang_1.txt','stall_1.txt')[trigger]
                    if game=='mw' and not diag_on and trigger: name='fixture-'+name
                    text = (case/name).read_text()
                    sentinels = (f'game={game}', 'FLOW 6->77', 'FLOW 77->6',
                                 'f25439 FRAME flow=6', 'dt_ms=4.167 busy_ms=0.750', 'STATE flow=6')
                    missing[broken] += int('FLOW 6->77' not in text)
                    if not broken:
                        assert all(s in text for s in sentinels)
                        if diag_on==0 and trigger==0: print(game+': '+result.stdout.strip())
                        assert 'F10 open' in text
                        assert ('snapshot=menu' if trigger==0 else 'no_frame=80.000s') in text
                        assert ('snapshot_age=80.000s' if trigger==0 else 'snapshot=live') in text
                        assert 'f18000 FRAME' not in text
                    assert 'F10 close' in (case/'closed.txt').read_text()
                    snapshot_loss=(case/'snapshot-loss.txt').read_text()
                    assert 'menu_snapshot_unavailable=1' in snapshot_loss and 'MENU_SNAPSHOT losses=1' in snapshot_loss
                    loss=(case/'loss.txt').read_text()
                    assert re.search(r'COVERAGE frames .*lost=[1-9]\d*',loss)
                    assert re.search(r'COVERAGE frames .*overwritten=[1-9]\d*',loss)
                    assert re.search(r'COVERAGE frames .*capacity_short=1', (case/'capacity.txt').read_text())
            if not broken:
                off=build/'off';off.mkdir(exist_ok=True)
                result=subprocess.run([str(build/'fixture'),'off','0',str(off)],cwd=off,capture_output=True,text=True,
                                      env=dict(os.environ,NFS_BLACKBOX='0'))
                assert result.returncode==0,result.stderr
                assert 'disabled NFS_BLACKBOX=0' in (off/'disabled.txt').read_text()
    assert missing == [0,12],missing
    print('PASS blackbox: missing sentinel records fixed=0/12 removed export hooks=12/12; wrap, pause, concurrency, loss, off')


if __name__ == '__main__':
    main()
