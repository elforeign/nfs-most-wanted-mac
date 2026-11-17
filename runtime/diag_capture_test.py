"""Capture watchdog: injected 750 ms PNG encode, old stall_1=1 -> fixed=0.

Broken control: 750 ms sleep immediately AFTER capture still writes stall_1=1
(also after a nested capture, rejecting double subtraction).
Runs both production detectors and PNG writers, with real ImageIO and a delayed
finalizer. Background encoding must still allow stall_1; nested capture time is
charged once; 300+300 ms outside a capture must still report. Diag Off: zero
clock calls. No assets or GPU.
Short wall-clock polling fixture: registered in the serial offline batch.
"""
from pathlib import Path
import hashlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OLD = 'uint64_t last, late = framelate_ns(&last_frame_ns, host_ticks, &last);'
OLD_SHA = '2f3d47e41b4d710260c580b13918ee9a89961fcea748e8dcde94670f8c9bbae5'


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRE = r'''
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "framelate.h"
static int diag_on=1, stop, clock_calls;
static uint64_t frame_now=900, last_frame_ns;
static const char *diag_dir;
static uint64_t host_ticks(void) {
    __atomic_fetch_add(&clock_calls,1,__ATOMIC_RELAXED);
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static int diag_is_idle(void) { return 0; }
static void diag_report(const char *name,const char *why) {
    char p[1024]; snprintf(p,sizeof p,"%s/%s",diag_dir,name);
    FILE *f=fopen(p,"w"); if(!f)abort(); fprintf(f,"%s\n",why); fclose(f);
}
static void boot_write_call_counts(FILE *f) { (void)f; }
static void profile_wait_100ms(void) { usleep(10000); if(__atomic_load_n(&stop,__ATOMIC_ACQUIRE))pthread_exit(NULL); }
static int delayed_finalize(CGImageDestinationRef dst) {
    usleep(750000); return CGImageDestinationFinalize(dst);
}
#define CGImageDestinationFinalize delayed_finalize
'''
MAIN = r'''
static const char *png_path;
static void *encode(void *unused) {
    (void)unused; uint32_t pixel=0xffabcdef;
    diag_png(png_path,&pixel,1,1,4,0); return NULL;
}
int main(int argc,char **argv) {
    if(argc!=4)return 2; diag_dir=argv[1]; png_path=argv[2];
    const char *mode=argv[3]; pthread_t monitor;
    diag_on=0; diag_capture_begin(); diag_capture_end();
    if(clock_calls)return 2; diag_on=1;
    last_frame_ns=host_ticks();
    if(pthread_create(&monitor,NULL,hang_monitor,NULL))return 2;
    if(!strcmp(mode,"background")) {
        pthread_t capture_thread; pthread_create(&capture_thread,NULL,encode,NULL); pthread_join(capture_thread,NULL);
    } else {
        if(!strcmp(mode,"split"))usleep(300000);
        int nested=!strcmp(mode,"nested")||!strcmp(mode,"nested-after");
        if(nested)diag_capture_begin();
        encode(NULL);
        if(nested)diag_capture_end();
        if(!strcmp(mode,"after")||!strcmp(mode,"nested-after"))usleep(750000);
        if(!strcmp(mode,"split"))usleep(300000);
    }
    __atomic_store_n(&stop,1,__ATOMIC_RELEASE); pthread_join(monitor,NULL);
    char path[1024];snprintf(path,sizeof path,"%s/stall_1.txt",diag_dir);
    int got=access(path,F_OK)==0;
    int want=!strcmp(mode,"after")||!strcmp(mode,"nested-after")||!strcmp(mode,"split")||!strcmp(mode,"background");
    printf("%s stall_1=%d expected=%d\n",mode,got,want);
    return got!=want;
}
'''


def main():
    assert hashlib.sha256(OLD.encode()).hexdigest() == OLD_SHA
    diag = (ROOT / 'runtime/diag.c').read_text()
    helpers = diag.split('// Only synchronous diagnostic work', 1)[1]
    helpers = '// Only synchronous diagnostic work' + helpers
    # Verify the hooks bracket the synchronous readback, not the whole frame.
    selected = subprocess.run(['unifdef', '-DNFS_GAME=1', '-DNFS_GAME_MW=1',
                               '-DNFS_GAME_PRIVATE=2'],
                              input=(ROOT / 'runtime/render.m').read_text(),
                              capture_output=True, text=True)
    assert selected.returncode in (0, 1), selected.stderr
    render = selected.stdout
    for name in ('void texdump(uint32_t guest, id<MTLTexture> src) {', 'static void depthdump(uint32_t guest, id<MTLTexture> src) {', 'static void drawshot(void) {'):
        body = function(render, name)
        assert body.index('diag_capture_begin();') < body.index('waitUntilCompleted') < body.index('diag_capture_end();')
    presenter = function((ROOT / 'runtime/render_present.m').read_text(), 'static void png_after(')
    assert presenter.index('diag_capture_begin();') < presenter.index('dispatch_semaphore_wait(') < presenter.index('diag_capture_end();')
    for label, source in [('mw', diag), ('private', (ROOT / 'recomp/private_runtime/game_diagnostics.h').read_text())]:
        monitor = function(source, 'static void *hang_monitor(')
        # Keep the production stall/hang conditions and reporting calls intact.
        monitor = monitor.replace('usleep(100000);', 'profile_wait_100ms();')
        png = function(source, 'void diag_png(')
        assert 'diag_capture_begin();' in png and 'diag_capture_end();' in png
        assert monitor.count('diag_capture_late(&last)') == 2
        with tempfile.TemporaryDirectory(prefix='diag-capture-') as tmp:
            tmp = Path(tmp)
            for broken in (False, True):
                code = PRE + helpers + '\n' + png + '\n' + monitor + MAIN
                if broken:
                    # Original watchdog clock + original unmarked PNG body.
                    code = code.replace('uint64_t last, late = diag_capture_late(&last);', OLD)
                    code = code.replace('late = diag_capture_late(&last);', 'late = framelate_ns(&last_frame_ns, host_ticks, &last);')
                    code = code.replace('    diag_capture_begin();\n    size_t n', '    size_t n')
                    code = code.replace('    CGColorSpaceRelease(cs);\n    diag_capture_end();', '    CGColorSpaceRelease(cs);')
                c = tmp / 'fixture.c'; exe = tmp / 'fixture'
                c.write_text(code)
                subprocess.run(['clang', '-std=c11', '-Werror', '-I', str(ROOT / 'runtime'), str(c), '-framework', 'CoreGraphics', '-framework', 'ImageIO', '-framework', 'CoreFoundation', '-o', str(exe)], check=True, capture_output=True)
                for mode in (('capture',) if broken else ('capture', 'after', 'background', 'nested', 'nested-after', 'split')):
                    out = tmp / (('old-' if broken else '') + mode); out.mkdir()
                    result = subprocess.run([str(exe), str(out), str(out / 'frame.png'), mode], capture_output=True, text=True, timeout=10)
                    print(label, 'old' if broken else 'fixed', result.stdout.strip())
                    assert (out / 'frame.png').is_file()
                    assert result.returncode == int(broken), result.stderr
                    assert len(list(out.glob('stall_*.txt'))) == int(broken or mode in ('after', 'background', 'nested-after', 'split'))


if __name__ == '__main__':
    main()
