"""Typed failure export and shareability, without assets, audio or controllers.
Starting main: 5/5 missing classes. Fixed: 0/5 in each of four export paths;
each of five removed feeds: 1/5. Personal path/name/secret occurrences: fixed 0,
sanitization bypassed 15. Success and handle returns produce 0 error records.
Repeat during capture: 1 missing pre-trigger shader class before, 0 after;
its interval count is explicitly unavailable, with observed lifetime count 2.
Covers runtime/blackbox_errors.c, runtime/blackbox_errors.h, runtime/blackbox.c,
runtime/boot.c, runtime/render.m, runtime/pipeline_cache.m, runtime/render_present.m,
runtime/runtime_sources.json, runtime/build.sh, runtime/report_privacy.h and
runtime/docs/blackbox.md.
"""
from pathlib import Path
import os
import subprocess
import sys
import re
import runpy
import statistics
import tempfile

R = Path(__file__).resolve().parent
CLASSES = ('shader', 'd3d', 'metal', 'asset', 'assert')

PRELUDE = r'''
#import <Foundation/Foundation.h>
#include "blackbox_errors.h"
#include "report_privacy.h"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "blackbox.c"
'''


def build(work, fixture, extra=(), sanitize=True):
    source = work / 'fixture.m'
    source.write_text(PRELUDE + fixture)
    binary = work / 'fixture'
    cmd = ['clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
           '-Wno-unused-function', '-Wno-unused-variable', '-I' + str(R),
           *(['-fsanitize=address,undefined'] if sanitize else []), '-fobjc-arc', '-framework', 'Foundation',
           '-framework', 'IOKit', '-framework', 'SystemConfiguration', str(source), *map(str, extra), '-o', str(binary)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-5000:]
    return binary


def main():
    env = dict(os.environ, NFS_BLACKBOX='1', BOOT_DIAG='0')
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        if '--premise' in sys.argv:
            # The starting tree has no error producers. Exercise its actual exporter.
            assert not (R / 'blackbox_errors.c').exists()
            binary = build(work, r'''
int main(void) {
    blackbox_record_frame(BLACKBOX_MW, 1, 3, NULL, blackbox_now_ns());
    blackbox_export(stdout, BB_STALL);
}
''')
            report = subprocess.check_output([binary], env=env, text=True)
            missing = sum(f'ERROR class={kind} ' not in report for kind in CLASSES)
            print(f'premise: missing_error_classes={missing}/5')
            assert missing == 5
            return
        check(work, env)



CORE = runpy.run_path(str(R / 'blackbox_test.py'))


def line(path, needle):
    matches = [row.strip() for row in (R / path).read_text().splitlines() if needle in row]
    assert len(matches) == 1, (path, needle, matches)
    return matches[0]


def hooks(removed=None):
    # Compile the actual producer statements, including their failure predicates.
    # Surrounding guest/Metal dependencies are synthetic; no game or device is opened.
    shader = line('render.m', 'blackbox_error(BB_ERR_SHADER, BB_OP_TRANSLATE')
    d3d = line('boot.c', 'if (c->eax & 0x80000000u) blackbox_error_hresult')
    asset = line('boot.c', 'if (h == 0xFFFFFFFFu) blackbox_error(')
    read = line('boot.c', 'if (r < 0) blackbox_error(')
    fatal = line('boot.c', 'blackbox_error(BB_ERR_FATAL')
    breakpoint = CORE['function']((R / 'boot.c').read_text(), 'void guest_breakpoint(')
    library = line('pipeline_cache.m', 'if (!library) blackbox_error(')
    pipeline = line('pipeline_cache.m', 'if (!p) blackbox_error(')
    if removed == 'shader':
        shader = '(void)rc; (void)e;'
    if removed == 'd3d':
        d3d = '(void)c; (void)hn; (void)ra;'
    if removed == 'asset':
        asset = '(void)h; (void)c;'
    if removed == 'assert':
        breakpoint = re.sub(r'blackbox_error\(BB_ERR_ASSERT[^;]+;', '(void)at;', breakpoint)
    return r'''
typedef struct { uint32_t eax, esp; } Cpu;
static int diag_on;
static int first_at_site(uint32_t *seen, uint32_t at) { (void)seen; (void)at; return 1; }
static void diag_event(const char *fmt, ...) { (void)fmt; }
#define M32(a) ((uint32_t)(a))
''' + breakpoint + r'''
static void shader(int rc) {
    struct { uint64_t key; } entry = {0x123456789ABCDEF0ull}, *e = &entry;
    if (rc != 0) {''' + shader + r''' }
}
static void com(const char *hn, uint32_t result) {
    Cpu cpu = {result, 0x70000000}, *c = &cpu;
    uint32_t ra = 0x00401234;
    ''' + d3d + r'''
}
static void asset(uint32_t h) {
    Cpu cpu = {0, 0x00402234}, *c = &cpu;
    ''' + asset + r'''
}
static void read_failure(ssize_t r) {
    Cpu cpu = {0, 0x00402234}, *c = &cpu;
    ''' + read + r'''
}
static void fatal(void) {''' + fatal + r''' }
static void library(int fail) {
    id library = fail ? nil : @1;
    NSError *value = [NSError errorWithDomain:@"SyntheticLibrary" code:17 userInfo:nil];
    NSError *__strong *error = &value;
    ''' + library + r'''
}
static void pipeline(int fail, int with_error) {
    id p = fail ? nil : @1;
    NSError *value = with_error ? [NSError errorWithDomain:@"SyntheticPipeline" code:18 userInfo:nil] : nil;
    NSError *__strong *error = with_error ? &value : NULL;
    ''' + pipeline + r'''
}
'''


FIXTURE = r'''
static volatile uint32_t successful_result;
static void bench(void) {
    const unsigned frames = 500000;
    uint64_t start = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    for (unsigned i = 0; i < frames; ++i) {
        for (unsigned j = 0; j < 50; ++j) {
            com("IDirect3DDevice9::DrawIndexedPrimitive", successful_result);
            com("ID3DXEffect::GetParameterByName", successful_result | 0xF2000000u);
        }
        blackbox_record_frame(BLACKBOX_MW, i, 6, NULL, start + i * 4166667ull);
        blackbox_record_present(i, BB_RENDER_SUBMIT, 0, 0, 0, 0);
        blackbox_record_present(i, BB_RENDER_GPU, 4, 0, 0, 0);
        blackbox_record_present(i, BB_PRESENT_GPU, 4, 0, 0, 0);
    }
    printf("%.3f\n", (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - start) / frames);
}
static _Atomic unsigned error_threads;
static void *error_storm(void *context) {
    for (unsigned i = 0; i < 10000; ++i)
        blackbox_error(BB_ERR_ASSERT, BB_OP_BREAKPOINT, 0, 0, 0xABC000 + (unsigned)(uintptr_t)context, 0);
    atomic_fetch_sub(&error_threads, 1);
    return NULL;
}
static void export_file(const char *root, const char *name, BlackboxTrigger trigger, uint64_t now) {
    char path[2048]; snprintf(path, sizeof path, "%s/%s", root, name);
    FILE *f = fopen(path, "w"); assert(f);
    blackbox_export_at(f, trigger, now); fclose(f);
}
static void inject(void) {
    Cpu cpu = {0};
    shader(-1);
    com("IDirect3DDevice9::CreateTexture", 0x8876086Cu);
    /* An asynchronous failure after the last frame, retaining its original serial. */
    blackbox_record_present(19, BB_RENDER_GPU, 5, 0, 0, 9);
    errno = ENOENT; asset(UINT32_MAX); assert(errno == ENOENT);
    guest_breakpoint(&cpu, 0x00403334);
}
int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 3);
    const char *root = argv[2];
    if (!strcmp(argv[1], "bench")) { bench(); return 0; }
    blackbox_record_frame(BLACKBOX_MW, 37, 3, NULL, blackbox_now_ns());
    if (!strcmp(argv[1], "capture-overlap")) {
        shader(-1);
        uint64_t at = errors[0].value.values[5];
        while (blackbox_now_ns() <= at) {} // cross the monotonic clock's resolution
        shader(-1);
        assert(errors[0].value.h.ns > at);
        /* Deterministic model of a repeat published after the trigger timestamp
         * but before the capture copies this slot. */
        blackbox_menu_at(1, at);
        export_file(root, "overlap.txt", BB_F10, blackbox_now_ns());
        return 0;
    }
    if (!strcmp(argv[1], "storm")) {
        pthread_t threads[4];
        atomic_store(&error_threads, 4);
        for (unsigned i = 0; i < 4; ++i)
            assert(!pthread_create(&threads[i], NULL, error_storm, (void *)(uintptr_t)i));
        while (atomic_load(&error_threads)) { Snapshot *s = capture(); assert(s); free(s); }
        for (unsigned i = 0; i < 4; ++i) pthread_join(threads[i], NULL);
        uint64_t count = atomic_load(&rings[3].lost);
        for (unsigned i = 0; i < ERRORS; ++i) if (errors[i].value.h.seq) count += errors[i].value.values[6];
        assert(count == 40000);
        printf("concurrent errors: accounted=%llu/40000\n", (unsigned long long)count);
        for (unsigned i = 0; i < 600; ++i) blackbox_error(BB_ERR_ASSET, BB_OP_OPEN, 2, 0, i, 0);
        export_file(root, "storm.txt", BB_STALL, blackbox_now_ns());
        return 0;
    }
    if (!strcmp(argv[1], "off")) {
        inject(); library(1); pipeline(1, 1); read_failure(-1); fatal();
        assert(!atomic_load(&rings[3].head)); return 0;
    }
    if (!strcmp(argv[1], "success")) {
        shader(0); library(0); pipeline(0, 1); asset(10); read_failure(10);
        com("IDirect3DDevice9::CreateTexture", 0);
        com("ID3DXEffect::SetValue", 1); // S_FALSE is success.
        const char *handles[] = {"GetParameter", "GetParameterByName", "GetParameterBySemantic",
            "GetParameterElement", "GetTechnique", "GetTechniqueByName", "GetPass", "GetPassByName",
            "GetFunction", "GetFunctionByName", "GetAnnotation", "GetAnnotationByName",
            "GetCurrentTechnique", "GetPool", "IsParameterUsed", "EndParameterBlock"};
        for (unsigned i = 0; i < sizeof handles / sizeof *handles; ++i) {
            char name[96]; snprintf(name, sizeof name, "ID3DXEffect::%s", handles[i]);
            com(name, 0xF2000000u); com(name, 0);
        }
        com("IDirect3D9::GetAdapterMonitor", 0xF2000000u);
        com("IDirect3DDevice9::GetAvailableTextureMem", UINT32_MAX);
        com("IDirect3DDevice9::GetNPatchMode", UINT32_MAX);
        com("IDirect3DDevice9::GetSoftwareVertexProcessing", UINT32_MAX);
        com("IDirect3DTexture9::SetPriority", UINT32_MAX);
        com("IDirect3DTexture9::SetLOD", UINT32_MAX);
        com("IDirect3DQuery9::GetDataSize", UINT32_MAX);
        com("unclassified::Get", UINT32_MAX);
        blackbox_record_present(19, BB_RENDER_GPU, 4, 0, 0, 0);
        blackbox_record_present(19, BB_PRESENT_GPU, 4, 0, 0, 0);
        blackbox_record_present(19, BB_PRESENT_SHOWN, 0, 0, 0, 1);
        assert(!atomic_load(&rings[3].head)); return 0;
    }
    inject();
    uint64_t freeze_time = blackbox_now_ns();
    blackbox_menu_at(1, freeze_time);
    /* Subsequent errors must not modify the frozen report. */
    shader(-1); shader(-1);
    export_file(root, "blackbox.txt", BB_F10, freeze_time + 80000000000ull);
    export_file(root, "hang.txt", BB_HANG, blackbox_now_ns());
    export_file(root, "stall.txt", BB_STALL, blackbox_now_ns());
    export_file(root, "crash.txt", BB_CRASH, blackbox_now_ns());
    // Legacy messages can contain personal text: bundle privacy must still cover them.
    NSString *home = [NSString pathWithComponents:@[@"/", @"Users", @"SyntheticErrorUser"]];
    NSString *payload = [NSString stringWithFormat:@"shader: %@/shaders/bad.metal\n"
        "D3D: Synthetic Error Person\nMetal: Bearer SyntheticErrorBearer\n"
        "asset: C:\\Users\\SyntheticErrorUser\\tracks\\missing.bin\n"
        "assert: password=SyntheticErrorPassword\n", home];
    NSString *folder = [NSString stringWithUTF8String:root];
    for (NSString *file in @[@"report.txt", @"events.txt", @"log.txt"])
        assert([payload writeToFile:[folder stringByAppendingPathComponent:file]
                        atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
#ifndef BYPASS_PRIVACY
    ReportPrivacyRedactBundle(folder, @{@"home": home,
        @"user": @[@"SyntheticErrorUser", @"Synthetic Error Person"]});
#endif
    if (!strcmp(argv[1], "details")) {
        blackbox_menu(0);
        library(1); pipeline(1, 1); pipeline(1, 0);
        errno = EBADF; read_failure(-1); assert(errno == EBADF);
        fatal();
        blackbox_record_present(17, BB_PRESENT_GPU, 5, 0, 0, 0);
        com("ID3DXEffect::GetTechniqueDesc", 0x80004005u);
        com("IDirectSoundBuffer::Lock", 0x80004005u);
        export_file(root, "details.txt", BB_STALL, blackbox_now_ns());
    }
    return 0;
} }
'''


def check(work, env):
    core = (R / 'blackbox.c').read_text()
    extra = (R / 'blackbox_errors.c',)
    # Every required class has its own removal control. Metal completion goes
    # through the existing common callback primitive, including the no-NSError case.
    for removed in (None, *CLASSES):
        source = core
        if removed == 'metal':
            source = source.replace('(status == 5 || c)', '(0)')
            assert source != core
        (work / 'blackbox.c').write_text(source)
        binary = build(work, hooks(removed) + FIXTURE, extra)
        folder = work / (removed or 'fixed')
        folder.mkdir()
        subprocess.run([binary, 'normal', folder], env=env, check=True, timeout=15)
        reports = [(folder / name).read_text() for name in ('blackbox.txt', 'hang.txt', 'stall.txt', 'crash.txt')]
        missing = [sum(f'ERROR class={kind} ' not in text for kind in CLASSES) for text in reports]
        print(f'{removed or "fixed"}: missing_error_classes={missing} (F10/hang/stall/crash)')
        assert missing == [int(removed is not None)] * 4
        if removed is None:
            baseline_reports = reports
            assert 'f19 ERROR class=metal operation=frame_command' in reports[0]
            assert 'snapshot=menu' in reports[0] and 'snapshot_age=80.' in reports[0]
            for text, count in ((reports[0], 1), (reports[1], 3)):
                shader_row = next(row for row in text.splitlines() if 'ERROR class=shader ' in row)
                assert f'repeats={count} ' in shader_row
                assert 'id=123456789ABCDEF0' in shader_row
                values = re.search(r'first_ns=(\d+) last_ns=(\d+)', shader_row)
                assert int(values[1]) <= int(values[2])
            leaks = privacy_leaks(folder)
            assert leaks == 0, leaks
            print(f'fixed: personal_path_name_secret_occurrences={leaks}')
    (work / 'blackbox.c').write_text(core)
    binary = build(work, hooks() + FIXTURE, (*extra, '-DBYPASS_PRIVACY'))
    folder = work / 'privacy-bypassed'; folder.mkdir()
    subprocess.run([binary, 'normal', folder], env=env, check=True, timeout=15)
    leaks = privacy_leaks(folder)
    print(f'privacy bypassed: personal_path_name_secret_occurrences={leaks}')
    assert leaks > 0
    binary = build(work, hooks() + FIXTURE, extra)
    subprocess.run([binary, 'off', work], env=dict(env, NFS_BLACKBOX='0'), check=True, timeout=15)
    subprocess.run([binary, 'success', work], env=env, check=True, timeout=15)
    folder = work / 'details'; folder.mkdir()
    subprocess.run([binary, 'details', folder], env=env, check=True, timeout=15)
    details = (folder / 'details.txt').read_text()
    for value in ('operation=library code=17', 'operation=pipeline code=18', 'operation=pipeline code=0',
                  'operation=read code=9', 'class=fatal operation=fatal',
                  'f17 ERROR class=metal operation=final_command code=0', 'class=com operation=hresult'):
        assert value in details, value
    print('success/handles/off-mode/errno/frozen repeats/late callbacks/pipeline/read/fatal: PASS')
    subprocess.run([binary, 'capture-overlap', folder], env=env, check=True, timeout=15)
    overlap = (folder / 'overlap.txt').read_text()
    missing = int('ERROR class=shader ' not in overlap)
    print(f'capture-overlap: missing_pretrigger_shader_class={missing}/1')
    assert missing == 0
    assert 'repeats=unavailable observed_repeats=2 capture_overlap=1' in overlap
    storm = subprocess.check_output([binary, 'storm', folder], env=env, text=True, timeout=15)
    assert 'accounted=40000/40000' in storm
    storm_report = (folder / 'storm.txt').read_text()
    assert len(re.findall(r'ERROR class=', storm_report)) == 512
    assert re.search(r'COVERAGE errors records=512 .*overwritten=[1-9]', storm_report)
    print(storm.strip() + '; ring overflow: retained=512 with explicit coverage loss')
    old_capture = core.replace(
        'int failure = k == 3 && ((Event *)h)->values[5] <=',
        'int failure = k == 3 && h->ns <=')
    assert old_capture != core
    (work / 'blackbox.c').write_text(old_capture)
    binary = build(work, hooks() + FIXTURE, extra)
    folder = work / 'capture-broken'; folder.mkdir()
    subprocess.run([binary, 'capture-overlap', folder], env=env, check=True, timeout=15)
    missing = int('ERROR class=shader ' not in (folder / 'overlap.txt').read_text())
    print(f'capture-overlap broken control: missing_pretrigger_shader_class={missing}/1')
    assert missing == 1
    benchmark(work, env, core)


def benchmark(work, env, core):
    begin = core.index('    /* Both frame and final completion paths')
    end = core.index('    Present p =', begin)
    baseline = core[:begin] + core[end:]
    binaries = []
    for name, source, removed in (('base', baseline, 'd3d'), ('tap', core, None)):
        (work / 'blackbox.c').write_text(source)
        binary = build(work, hooks(removed) + FIXTURE, (R / 'blackbox_errors.c',), sanitize=False)
        target = work / name
        binary.rename(target)
        binaries.append(target)
    pairs = []
    for i in range(9):
        row = [0, 0]
        for index in ((0, 1) if i % 2 == 0 else (1, 0)):
            row[index] = float(subprocess.check_output([binaries[index], 'bench', work], env=env, text=True, timeout=15))
        pairs.append(row)
    delta = [tap - base for base, tap in pairs]
    added = statistics.median(delta)
    print(f'ns/frame ({len(pairs)} alternating pairs, 100 COM returns including 50 handles + 3 presentation records): '
          f'base={statistics.median(x[0] for x in pairs):.1f} tap={statistics.median(x[1] for x in pairs):.1f} '
          f'added={added:+.1f} range={min(delta):+.1f}..{max(delta):+.1f}')
    assert added < 820000, 'exceeds job-1 same-arm A/B spread (820,000 ns/frame)'


def privacy_leaks(folder):
    seeds = ('SyntheticErrorUser', 'Synthetic Error Person', 'SyntheticErrorBearer', 'SyntheticErrorPassword')
    text = '\n'.join(path.read_text() for path in folder.glob('*.txt'))
    return sum(text.count(seed) for seed in seeds)


if __name__ == '__main__':
    main()

