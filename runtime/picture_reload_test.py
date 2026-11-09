#!/usr/bin/env python3
"""Production picture caches, linked against the live picture weak fallback ABI.
Offline suite autodiscovers this driver; no GPU, game or private assets.
Proof numbers: production 0 failures (exit 0); each no-op reload control
reports 8 failures (exit 1), including failure to observe off after reload.
Each cache: 1,000 warm reads without getenv/locks, 40,000 threaded reads
against 10,000 reloads. Environment changes must stay cached until reload;
unset defaults, off/on, case rules and diagnostic/physical gates are retained.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r"^(?:static )?(?:int|void) " + name + r"\([^\n]*\)\s*\{", source, re.M)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


HARNESS = r'''
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "runtime.h"
static int diag_on, physical = 1, reads, locks, variant;
static int postfx_look_variant(const char *v) {
    return variant && !strcmp(v, "softon");
}
static int look_var_is(const char *v) {
    return variant && !strcmp(v, "ssaon");
}
static int particles_physical_enabled(void) { return physical; }
static char *counted_getenv(const char *name) {
    __atomic_fetch_add(&reads, 1, __ATOMIC_RELAXED);
    return getenv(name);
}
static int counted_lock(pthread_mutex_t *mu) {
    __atomic_fetch_add(&locks, 1, __ATOMIC_RELAXED);
    return pthread_mutex_lock(mu);
}
#define getenv counted_getenv
#define pthread_mutex_lock counted_lock
PRODUCTION
#undef getenv
#undef pthread_mutex_lock
static int failures;
static void check(int ok, const char *why) {
    if (!ok) { ++failures; fprintf(stderr, "FAIL: %s\n", why); }
}
static int (*enabled)(void);
static void (*reload)(void);
static void *reader(void *unused) {
    (void)unused;
    int bad = 0;
    for (int i = 0; i < 10000; ++i) bad += enabled() != 1;
    return (void *)(long)bad;
}
static void *reloader(void *unused) {
    (void)unused;
    for (int i = 0; i < 10000; ++i) reload();
    return NULL;
}
static void exercise(const char *env, int (*get)(void), void (*invalidate)(void), int uppercase) {
    enabled = get; reload = invalidate;
    unsetenv(env); reload(); reads = 0;
    check(enabled() == 1 && reads == 1, "unset default/cold read");
    int warm_locks = locks;
    for (int i = 0; i < 1000; ++i) check(enabled() == 1, "warm value");
    check(reads == 1 && locks == warm_locks, "warm path has no getenv/lock");
    setenv(env, "off", 1);
    check(enabled() == 1 && reads == 1, "environment remains cached");
    reload(); check(reads == 1, "reload stays lazy");
    check(enabled() == 0 && reads == 2, "reload observes off");
    setenv(env, "on", 1);
    check(enabled() == 0 && reads == 2, "off remains cached");
    reload(); check(enabled() == 1 && reads == 3, "reload observes on");
    unsetenv(env); reload();
    check(enabled() == 1 && reads == 4, "reload observes unset");
    setenv(env, "OFF", 1); reload();
    check(enabled() == uppercase && reads == 5, "original case rule");
    setenv(env, "off", 1); reload(); diag_on = variant = 1;
    check(enabled() == 1 && reads == 5, "diagnostic override bypasses cache");
    diag_on = variant = 0;
    check(enabled() == 0 && reads == 6, "normal value after diagnostic");
    setenv(env, "on", 1); reload(); check(enabled() == 1, "on before stress");
    if (uppercase) {
        physical = 0; check(enabled() == 0, "physical particles gate"); physical = 1;
    }
    pthread_t workers[5];
    for (int i = 0; i < 5; ++i)
        if (pthread_create(&workers[i], NULL, i == 4 ? reloader : reader, NULL)) exit(2);
    for (int i = 0; i < 5; ++i) {
        void *bad;
        if (pthread_join(workers[i], &bad)) exit(2);
        check(bad == NULL, "concurrent read/reload value");
    }
    setenv(env, "off", 1); reload(); check(enabled() == 0, "off after stress");
    printf("%s: 1000 warm reads, 40000 threaded reads, 10000 reloads\n", env);
}
int main(void) {
    exercise("NFS_SOFT_SMOKE", soft_smoke_on, render_soft_smoke_reload, 1);
    exercise("NFS_SSAO", x360_post_ssao_enabled, x360_post_ssao_reload, 0);
    printf("failures=%d\n", failures);
    return failures != 0;
}
'''


def main():
    render = (ROOT / 'runtime/render.m').read_text()
    post = (ROOT / 'runtime/x360_post.m').read_text()
    smoke = render[render.index('static int soft_smoke_cached ='):render.index('int render_soft_depth_store(')]
    ssao = post[post.index('static int ssao_cached ='):post.index('int postfx_look_variant(')]
    production = smoke + ssao
    hooks = ('render_soft_smoke_reload', 'x360_post_ssao_reload')
    with tempfile.TemporaryDirectory(prefix='picture-reload-') as directory:
        tmp = Path(directory)
        # Separate TU reproduces the parallel branch's weak no-op definitions.
        weak = tmp / 'weak.c'
        weak.write_text('#include "runtime.h"\n' + '\n'.join(
            '__attribute__((weak)) void ' + hook + '(void) {}' for hook in hooks))
        for label in ('production', *hooks):
            body = production
            if label != 'production':
                body = body.replace(function(body, label), 'void ' + label + '(void) {}')
            source = tmp / 'cache.c'
            source.write_text(HARNESS.replace('PRODUCTION', body))
            binary = tmp / 'cache'
            subprocess.run(['clang', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-pthread', '-I', str(ROOT / 'recomp'),
                            str(source), str(weak), '-o', str(binary)], check=True,
                           capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            expected = int(label != 'production')
            assert result.returncode == expected, (label, result.returncode, result.stdout, result.stderr)
            assert ('failures=0' in result.stdout) == (label == 'production'), result.stdout
            print(f'{label}: exit {result.returncode}; ' + result.stdout.strip().replace('\n', '; '))


if __name__ == '__main__':
    main()
