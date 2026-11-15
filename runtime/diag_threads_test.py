"""Real signal delivery through both report writers, without game assets.

Original Carbon: 0 host thread sections; fixed Carbon and MW: 3 each.
The pinned reporting-only control must fail the same three-thread assertion.
Exit suppresses both hang/stall publication while retaining crash reports.
Dependencies: diag_threads.h, diag.c, recomp/private_runtime/game_diagnostics.inc.
"""
from pathlib import Path
import hashlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD = '''void diag_report(const char *file, const char *why) {
    extern void diag_flush(void);
    char path[4096];
    snprintf(path, sizeof path, "%s/%s", diag_dir ? diag_dir : userdata_dir(), file);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "%s\\nframe: %llu\\nreporting thread backtrace (not the game thread):\\n",
            why, (unsigned long long)__atomic_load_n(&frame_now, __ATOMIC_ACQUIRE));
    write_backtrace(f);
    dump_ring(f, 4000);
    fclose(f);
    diag_flush();
}'''
OLD_SHA = '2e8bb1bb53ef7a81e9269b6dfcfc7a70d23918e1ea191bd06dd4446ab366ec2e'


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRE = r'''
#include <execinfo.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef struct { int unused; } Cpu;
static const char *diag_dir;
static uint64_t frame_now = 900;
static int exiting, done;
static pthread_t game_threads[3];
static const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; *off=0; return NULL; }
static const char *userdata_dir(void) { return diag_dir; }
static const char *log_dir(void) { return diag_dir; }
static const char *log_name(void) { return "test"; }
void diag_flush(void) {}
static void dump_ring(FILE *f, int n) { (void)n; fputs("events sentinel\n",f); }
static void report_auto(const char *why, const char *path) { (void)why; (void)path; }
void sstr_pool_stats(uint32_t *a,uint32_t *b,uint32_t *c,uint32_t *d,uint32_t *e) { *a=*b=*c=*d=*e=0; }
static void guest_state(FILE *f, Cpu *c, uint32_t tid) { (void)c; fprintf(f,"guest %u\n",tid); }
int threads_list(pthread_t *ths, Cpu **cpus, uint32_t *tids, int cap) {
    if(cap<3)abort();
    for(int i=0;i<3;++i) { ths[i]=game_threads[i]; cpus[i]=NULL; tids[i]=i+1; }
    return 3;
}
'''
MAIN = r'''
static void *game_loop(void *p) {
    (void)p;
    while(!__atomic_load_n(&done,__ATOMIC_ACQUIRE))usleep(1000);
    return NULL;
}
static void *reporter(void *p) {
    (void)p;
    diag_report("hang_1.txt","forced hang");
    __atomic_store_n(&exiting,1,__ATOMIC_RELEASE);
    diag_report("hang_exit.txt","exit");
    diag_report("stall_exit.txt","exit");
    diag_report("crash.txt","crash");
    __atomic_store_n(&done,1,__ATOMIC_RELEASE);
    return NULL;
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    alarm(30); diag_dir=argv[1]; game_threads[0]=pthread_self();
    for(int i=1;i<3;++i)if(pthread_create(&game_threads[i],NULL,game_loop,NULL))return 2;
    for(int i=0;i<3;++i)printf("target=%p\n",(void *)game_threads[i]);
    pthread_t monitor;
    if(pthread_create(&monitor,NULL,reporter,NULL))return 2;
    game_loop(NULL);
    pthread_join(monitor,NULL);
    for(int i=1;i<3;++i)pthread_join(game_threads[i],NULL);
    return 0;
}
'''


def main():
    assert hashlib.sha256(OLD.encode()).hexdigest() == OLD_SHA
    shared = (ROOT / 'runtime/diag_threads.h').read_text()
    private = (ROOT / 'recomp/private_runtime/game_diagnostics.inc').read_text()
    mw = (ROOT / 'runtime/diag.c').read_text()
    for source in (private, mw):
        assert '#include "diag_threads.h"' in source
    init = function(private, 'void diag_init(')
    assert init.index('if (!diag_dir) return;') < init.index('pthread_create(')
    with tempfile.TemporaryDirectory(prefix='diag-threads-') as tmp:
        tmp = Path(tmp)
        for name, source in [('carbon', private), ('mw', mw), ('original', None)]:
            report = function(source, 'void diag_report(') if source else OLD
            fixture = tmp / 'fixture.c'
            fixture.write_text(PRE + shared + '\n' + report + '\n' + MAIN)
            binary = tmp / 'fixture'
            build = subprocess.run(['clang', '-O1', '-g', '-fno-omit-frame-pointer',
                                    str(fixture), '-o', str(binary)], capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[:3000]
            out = tmp / name
            out.mkdir()
            run = subprocess.run([str(binary), str(out)], capture_output=True, text=True, timeout=40)
            assert run.returncode == 0, run.stderr[:3000]
            text = (out / 'hang_1.txt').read_text()
            sections = text.count('-- host thread ')
            print(f'{name}: host_sections={sections}, expected=3')
            assert sections == (0 if name == 'original' else 3)
            assert 'events sentinel' in text
            if name != 'original':
                targets = [line[len('target='):] for line in run.stdout.splitlines()]
                assert len(targets) == len(set(targets)) == 3
                for target in targets:
                    assert text.count('-- host thread ' + target + ' --') == 1
                assert text.count('-- reporting thread --') == 1
                assert not (out / 'hang_exit.txt').exists()
                assert not (out / 'stall_exit.txt').exists()
                assert (out / 'crash.txt').read_text().count('-- host thread ') == 3
            else:
                assert sections != 3, 'reporting-only control must fail completeness'
    print('DIAG THREADS PASS: Carbon/MW=3, original=0; exit hang/stall suppressed')


if __name__ == '__main__':
    main()
