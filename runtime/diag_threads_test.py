"""Real signal delivery through both report writers, without game assets.

Original Carbon: 0 host thread sections; fixed Carbon and MW: 3 each.
The pinned reporting-only control must fail the same three-thread assertion.
Exit suppresses both hang/stall publication while retaining crash reports.
Event rings: original dumps emit 4 unavailable slots; guarded dumps emit 0.
Both versions still emit the 3 completed records used as controls.
Dependencies: diag_threads.h, diag.c, recomp/private_runtime/game_diagnostics.h.
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

RINGS = r'''
#define _DARWIN_C_SOURCE
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static pthread_mutex_t file_mu=PTHREAD_MUTEX_INITIALIZER;
static FILE *events_f;
static uint64_t frame_now=7;
static int dump_during_call, dump_during_note, unavailable;
static uint64_t host_ticks(void) { return 0; }
static uint64_t diag_current_frame(void);
static void session_note(const char *line, int audio);
'''

RINGS_MAIN = r'''
static int rows(int report) {
    char *text=NULL;
    size_t size=0;
    FILE *out=open_memstream(&text,&size);
    if(!out) exit(2);
    if(report) report_notes_dump(out);
    else dump_ring(out,1);
    fclose(out);
    int count=0;
    for(char *p=text; *p; ++p) if(*p=='\n' && p!=text && p[-1]!='\n') ++count;
    // The diagnostics header is also a line.
    free(text);
    return report ? count : count-1;
}
uint32_t diag_thread_id(void) {
    if(dump_during_call) unavailable+=rows(0);
    return 8;
}
static uint64_t diag_current_frame(void) {
    if(dump_during_note) unavailable+=rows(1);
    return frame_now;
}
static void session_note(const char *line, int audio) { (void)line; (void)audio; }
int main(void) {
    alarm(5);
    diag_event_ring("complete text");
    int completed=rows(0);
    ring_head=RING;
    dump_during_call=1;
    uint32_t arg=3;
    diag_call_ring("complete raw",1,&arg,4,5);
    dump_during_call=0;
    completed+=rows(0);
    ring_head=2*RING+1;
    unavailable+=rows(0);

    report_note("complete note");
    completed+=rows(1);
    if(!note_lock(0)) return 2;
    unavailable+=rows(1);
    note_unlock(0);
    notes_head=NOTES;
    dump_during_note=1;
    report_note("next note");
    printf("event rings: unavailable=%d completed=%d\n",unavailable,completed);
    return unavailable || completed!=3;
}
'''


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def event_rings(tmp, mw):
    ring = mw.split('// ---------------- event ring ----------------\n')[1].split('// Only synchronous diagnostic work')[0]
    report = (ROOT / 'runtime/report.m').read_text()
    notes = report.split('// ---------------- notable events ----------------\n')[1].split('// ---------------- the per-second ring')[0]
    assert 'report_notes_dump(f);' in report
    fixed = RINGS + '\n#include <unistd.h>\n' + ring + notes + RINGS_MAIN
    # Recreate the original readers' behavior while retaining the writer guards
    # so the same deterministic pauses are available to both binaries.
    old_diag = '''        if (!ring_lock(i)) continue;
        int valid = ring_slots[i].seq == k + 1;
        if (valid) ring_line(i, buf, sizeof buf);
        ring_unlock(i);
        if (valid) fprintf(f, "%s\\n", buf);'''
    old_note = '''        if (!note_lock(i)) continue;
        char line[NOTE_LEN];
        int valid = notes[i].seq == k + 1;
        if (valid) memcpy(line, notes[i].line, sizeof line);
        note_unlock(i);
        if (valid) fprintf(f, "%s\\n", line);'''
    assert old_diag in fixed and old_note in fixed
    original = fixed.replace(old_diag, '        ring_line(i, buf, sizeof buf); fprintf(f, "%s\\n", buf);')
    original = original.replace(old_note, '        fprintf(f, "%s\\n", notes[i].line);')
    for label, source, expected in [('guarded', fixed, 0), ('original', original, 4)]:
        fixture = tmp / 'ring.c'
        fixture.write_text(source)
        binary = tmp / 'ring'
        build = subprocess.run(['clang', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                                str(fixture), '-o', str(binary)], capture_output=True, text=True)
        assert build.returncode == 0, build.stderr[-3000:]
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=8)
        print(f'{label}: {run.stdout.strip()}')
        assert run.returncode == bool(expected) and f'unavailable={expected} completed=3' in run.stdout


PRE = r'''
#include <execinfo.h>
#include "blackbox.h"
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
    private = (ROOT / 'recomp/private_runtime/game_diagnostics.h').read_text()
    mw = (ROOT / 'runtime/diag.c').read_text()
    for source in (private, mw):
        assert '#include "diag_threads.h"' in source
    init = function(private, 'void diag_init(')
    assert init.index('if (!diag_dir) return;') < init.index('pthread_create(')
    with tempfile.TemporaryDirectory(prefix='diag-threads-') as tmp:
        tmp = Path(tmp)
        event_rings(tmp, mw)
        for name, source in [('carbon', private), ('mw', mw), ('original', None)]:
            report = function(source, 'void diag_report(') if source else OLD
            fixture = tmp / 'fixture.c'
            fixture.write_text(PRE + shared + '\n' + report + '\n' + MAIN)
            binary = tmp / 'fixture'
            build = subprocess.run(['clang', '-O1', '-g', '-fno-omit-frame-pointer',
                                    '-I'+str(ROOT / 'runtime'), str(fixture), str(ROOT / 'runtime/blackbox.c'), '-o', str(binary)], capture_output=True, text=True)
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
