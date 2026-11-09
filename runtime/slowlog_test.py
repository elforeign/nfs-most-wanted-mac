#!/usr/bin/env python3
"""Normal-session telemetry proof, no assets/game; discovered by offline_tests.sh.

Production integration: runtime/slowlog.h and runtime/slowlog_check.py, the
report.m header, render_post.m wait loop and build.sh sampler registration.

Production wait loop -> queue -> TSV: X360 PASS, PC zero PASS, off zero PASS.
Missing columns: 20/20 rejected (exit 1); zeroed X360 hook rejected (exit 1).
Queue overflow: 4096 retained / 1 dropped; 100000 concurrent writes accounted for.
Concurrent triple-buffer publication: 100000 snapshots, no torn sample.
Switch mid-second, 40 X360 waits then PC: writer/reader retain 800 ms / 20 ms.
8f181f3c reader and writer fragments each FAIL the same assertion: 0 / 0;
fixed writer + reader PASS: 800 / 20. Duplicate deltas sum, maxima take max.
The check's own header records the same good/broken results (slowlog_check.py).
"""
import csv
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent
# The evidence contract is independent of the checker and the production header.
EVIDENCE = set(('lum_wait_sum_ms lum_wait_max_ms lum_wait_count lum_wait_dropped '
                'power_source battery_pct thermal low_power gpu_dev_util gpu_self_ms '
                'gpu_other_ms gpu_top gpu_mhz gpu_pstates gpu_clock_status '
                'slowlog_enabled slowlog_sample slowlog_dt slowlog_age_ms post_path').split())


# Pinned regression fragments from 8f181f3c; offline proof needs no history.
OLD_READER = 'def slowlog_evidence(rows):\n    """Unique background intervals, so a repeated snapshot is never counted twice."""\n    if \'lum_wait_sum_ms\' not in rows[0]:\n        return \'; (no luminance/power-source columns in this log)\'\n    unique = {r.get(\'slowlog_sample\', str(i)): r for i, r in enumerate(rows)\n              if r.get(\'slowlog_sample\', \'1\') != \'0\'}\n    samples = list(unique.values())\n    def values(key):\n        return [v for r in samples if (v := gpu_number(r, key)) is not None]\n    sums, maxima, counts = values(\'lum_wait_sum_ms\'), values(\'lum_wait_max_ms\'), values(\'lum_wait_count\')\n    states = \',\'.join(sorted({r.get(\'gpu_pstates\', \'-\') for r in samples})) or \'-\'\n    status = \',\'.join(sorted({r.get(\'gpu_clock_status\', \'-\') for r in samples})) or \'-\'\n    paths = \',\'.join(sorted({r.get(\'post_path\', \'-\') for r in rows}))\n    enabled = \',\'.join(sorted({str(r.get(\'slowlog_enabled\', \'-\')) for r in rows}))\n    dt, age, drops = values(\'slowlog_dt\'), values(\'slowlog_age_ms\'), values(\'lum_wait_dropped\')\n    return \'; post_path %s, slowlog_enabled %s, slowlog_sample %d unique, slowlog_dt %.3f-%.3f s, slowlog_age_ms max %.1f; lum_wait_sum_ms total %.3f, per-second max %.3f, lum_wait_max_ms %.3f, lum_wait_count %.0f, lum_wait_dropped %.0f; gpu_pstates %s, gpu_clock_status %s\' % (\n        paths, enabled, len(samples), min(dt, default=0), max(dt, default=0), max(age, default=0),\n        sum(sums), max(sums, default=0), max(maxima, default=0), sum(counts), sum(drops), states, status)\n'
assert __import__("hashlib").sha256(OLD_READER.encode()).hexdigest() == '3daffc1f06f6318f6ff9b19a746adfee79f7beb1d3e83f6bda6e65fab6947848'
OLD_WRITER = 'void slowlog_print(FILE *f, const SlowlogSample *s, const char *post_path) {\n    int pc = !strcmp(post_path, "PC");\n    fprintf(f, "\\t%d\\t%llu\\t%.3f\\t", s->enabled, (unsigned long long)s->serial, s->interval);\n    if (s->serial) fprintf(f, "%.1f", (seconds() - s->time) * 1000); else fputc(\'-\', f);\n    fprintf(f, "\\t%s\\t%.6f\\t%.6f\\t%llu\\t%llu\\t%s\\t", post_path, pc ? 0 : s->lum_sum_ms, pc ? 0 : s->lum_max_ms,\n            (unsigned long long)(pc ? 0 : s->lum_count), (unsigned long long)s->lum_dropped, s->power);\n    if (isfinite(s->battery_pct)) fprintf(f, "%.1f", s->battery_pct); else fputc(\'-\', f);\n    fprintf(f, "\\t%s\\t%s", s->gpu.states, s->gpu.clock_status);\n}\n'
assert __import__("hashlib").sha256(OLD_WRITER.encode()).hexdigest() == '672f8a70da089c9922c42f5ac006fbadadd0bc1f8b7a7700b536c897c8ebb459'

HARNESS = r'''
#include "gpuclients.m"
#include "slowlog.m"
#include <assert.h>
#include <pthread.h>
#include <unistd.h>
static uint64_t host_ticks(void) { return (uint64_t)(seconds() * 1e9); }
static void wait_serial(uint64_t serial) { (void)serial; usleep(2000); }
static int x_lum_nowait(void) { return 0; }
#define X_LUM_SLOTS 3
#define IDLE_GPU 0
static uint64_t diag_idle_ns[1];
struct XLumSlot { int pending; uint64_t serial; };
struct FakeXR { int lum_ready; struct XLumSlot readback[3]; };
static void drive_wait(void) {
    struct FakeXR data = {.lum_ready=1, .readback={{1,1},{0,0},{0,0}}}, *xr=&data;
    int view=0;
    WAIT_LOOP
    (void)lum_wait_ns;
}
static uint64_t expected_sum, expected_count;
static int finished;
static void *produce_waits(void *unused) {
    (void)unused;
    for(uint64_t i=1;i<=100000;++i) {
        uint64_t before=__atomic_load_n(&wait_head,__ATOMIC_RELAXED);
        slowlog_lum_wait(i);
        if(__atomic_load_n(&wait_head,__ATOMIC_RELAXED)!=before) { expected_sum+=i; ++expected_count; }
    }
    __atomic_store_n(&finished,1,__ATOMIC_RELEASE); return NULL;
}
static void *produce_snapshots(void *unused) {
    (void)unused;
    for(uint64_t i=1;i<=100000;++i) {
        SlowlogSample s; empty(&s); s.serial=s.lum_count=i; s.lum_sum_ms=i*2;
        s.time=seconds(); snprintf(s.gpu.top,sizeof s.gpu.top,"%llu",(unsigned long long)i);
        publish(&s);
    }
    __atomic_store_n(&finished,1,__ATOMIC_RELEASE); return NULL;
}
static void stress(void) {
    enabled=1; wait_head=wait_tail=dropped=0;
    for(unsigned i=0;i<WAITS+1;++i) slowlog_lum_wait(1000);
    SlowlogSample s; empty(&s); drain_waits(&s);
    assert(s.lum_count==4096 && s.lum_dropped==1 && s.lum_sum_ms==4.096 && s.lum_max_ms==0.001);
    drain_waits(&s); assert(s.lum_count==0 && s.lum_sum_ms==0 && s.lum_dropped==0);
    pthread_t thread; assert(!pthread_create(&thread,NULL,produce_waits,NULL));
    uint64_t sum=0,count=0,lost=0;
    do {
        drain_waits(&s); sum+=(uint64_t)llround(s.lum_sum_ms*1e6); count+=s.lum_count; lost+=s.lum_dropped;
    } while(!__atomic_load_n(&finished,__ATOMIC_ACQUIRE));
    assert(!pthread_join(thread,NULL));
    drain_waits(&s); sum+=(uint64_t)llround(s.lum_sum_ms*1e6); count+=s.lum_count; lost+=s.lum_dropped;
    assert(count==expected_count && count+lost==100000 && sum==expected_sum);
    finished=0;
    assert(!pthread_create(&thread,NULL,produce_snapshots,NULL));
    unsigned reads=0;
    do {
        slowlog_cached(&s);
        if(s.serial) {
            assert(s.serial==s.lum_count && s.lum_sum_ms==s.serial*2);
            assert(strtoull(s.gpu.top,NULL,10)==s.serial); ++reads;
        }
    } while(!__atomic_load_n(&finished,__ATOMIC_ACQUIRE));
    assert(!pthread_join(thread,NULL)); assert(reads>0);
    slowlog_cached(&s); assert(s.serial==100000);
    slots[front].time=seconds()-4; slowlog_cached(&s); assert(s.serial==0);
    fprintf(stderr,"PASS overflow 4096/1; concurrent queue 100000 writes; snapshots 100000 publications / %u reads\n",reads);
}
static void emit(const char *path) {
    enabled=strcmp(path,"off")!=0;
    if (strcmp(path,"PC")) drive_wait(); // PC frames never enter the X360 wait loop
    SlowlogSample s; empty(&s); drain_waits(&s);
    if(enabled) {
        s.serial=1; s.interval=1; s.time=seconds(); s.thermal=0; s.low_power=0;
        strcpy(s.power,"battery"); s.battery_pct=72;
        s.gpu.other_ms=0; strcpy(s.gpu.mhz,"state=P1");
        strcpy(s.gpu.states,"IDLE:1.00,P1:99.00"); strcpy(s.gpu.clock_status,"decoded_residency");
    } else assert(s.lum_count==0 && s.lum_sum_ms==0 && s.lum_max_ms==0);
    const char *header=PRODUCTION_HEADER;
    puts(header);
    fputs(PRODUCTION_PREFIX,stdout);
    gpuclients_print(stdout,&s.gpu);
    slowlog_print(stdout,&s,!strcmp(path,"PC") ? "PC" : "X360"); putchar('\n');
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc>1 && !strcmp(argv[1],"stress")) { stress(); return 0; }
    if(argc>1 && !strcmp(argv[1],"background-off")) {
        setenv("NFS_SLOWLOG","0",1); slowlog_start();
        assert(!enabled && !sampler_timer);
        drive_wait(); SlowlogSample s; slowlog_cached(&s);
        assert(!s.enabled && !s.serial && !wait_head);
        puts("PASS NFS_SLOWLOG=0 starts no sampler and records no waits"); return 0;
    }
    if(argc>1 && !strcmp(argv[1],"background")) {
        slowlog_start();
        double deadline=seconds()+15; uint64_t last=0;
        for(int i=0;i<3;++i) { SlowlogSample s;
            do { usleep(10000); slowlog_cached(&s); } while(s.serial<=last && seconds()<deadline);
            assert(s.enabled && s.serial>0 && s.interval>0 && s.low_power>=0 && s.thermal>=0);
            assert(s.serial>last); last=s.serial;
            printf("background sample=%llu power=%s charge=%.1f thermal=%d low_power=%d",
                   (unsigned long long)s.serial,s.power,s.battery_pct,s.thermal,s.low_power);
            gpuclients_print(stdout,&s.gpu); printf("\t%s\t%s\n",s.gpu.states,s.gpu.clock_status);
        } return 0;
    }
    if(argc>1 && !strncmp(argv[1],"switch-",7)) {
        enabled=1;
        // X360 frames accumulated 40 waits before switching halfway through a second.
        for(int i=0;i<40;++i) slowlog_lum_wait(20000000);
        SlowlogSample s; empty(&s); drain_waits(&s);
        s.serial=1; s.interval=1; s.time=seconds();
        strcpy(s.gpu.clock_status,"not_decoded_yet:fixture");
        const char *header=PRODUCTION_HEADER;
        puts(header);
        for(int i=0;i<2;++i) {
            fputs(PRODUCTION_PREFIX,stdout); gpuclients_print(stdout,&s.gpu);
            slowlog_print(stdout,&s,i==0 ? argv[1]+7 : "PC"); putchar('\n');
        }
        return 0;
    }
    emit(argc>1 ? argv[1] : "X360"); return 0;
} }
'''


def run(args, expected=0):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=90)
    assert p.returncode == expected, p.stdout[-1500:] + p.stderr[-3000:]
    return p.stdout + (p.stderr if expected == 0 and not p.stdout else '')


def boundary_evidence(module, text):
    rows = list(csv.DictReader(text.splitlines(), delimiter='\t'))
    evidence = module['slowlog_evidence'](rows)
    for value in ('slowlog_sample 1 unique', 'lum_wait_sum_ms total 800.000',
                  'per-second max 800.000', 'lum_wait_max_ms 20.000', 'lum_wait_count 40'):
        assert value in evidence, evidence
    return rows


def main():
    report = (RT / 'report.m').read_text()
    post = (RT / 'render_post.m').read_text()
    header = report.split('if (perf_f)\n        fprintf(perf_f, ', 1)[1].split(');', 1)[0]
    assert 'SLOWLOG_COLUMNS' in header
    assert 'slowlog_start();' in report and 'gpuclients_start();' not in report
    assert 'slowlog_cached(&slow_sample);' in report
    assert 'gpuclients_print(perf_f, &slow_sample.gpu);' in report
    assert 'slowlog_print(perf_f, &slow_sample,' in report
    assert 'gpuclients_publish(&s.gpu);' in (RT / 'slowlog.m').read_text()
    assert 'IOPSCopy' not in report and 'lowPowerModeEnabled' not in report and '.thermalState' not in report
    assert 'slowlog.m \\' in (RT / 'build.sh').read_text()
    loop = post[post.index('    uint64_t lum_wait_ns=0;'):post.index('    uint64_t completed =', post.index('    uint64_t lum_wait_ns=0;'))]
    # Existing header prefix: append the production GPU and slowdown serializers.
    import re
    keys = ''.join(re.findall(r'"([^"\n]*)"', header.split('SLOWLOG_COLUMNS')[0])).encode().decode('unicode_escape').rstrip('\t').split('\t')
    assert keys[-1] == 'gpu_mhz'
    prefix = ['0'] * (len(keys) - 5)
    for key, value in {'t':'1', 'frame':'120', 'state':'race', 'fps':'120', 'draws':'3000'}.items():
        prefix[keys.index(key)] = value
    import json
    harness = HARNESS.replace('PRODUCTION_HEADER', header).replace('PRODUCTION_PREFIX', json.dumps('\t'.join(prefix))).replace('WAIT_LOOP', loop)
    checker = runpy.run_path(str(RT / 'slowlog_check.py'))
    assert len(EVIDENCE) == 20 and set(checker['REQUIRED']) == EVIDENCE
    with tempfile.TemporaryDirectory(prefix='slowlog-test-') as scratch:
        tmp = Path(scratch)
        def build(name, text):
            src, exe = tmp / (name + '_harness.m'), tmp / name
            src.write_text(text)
            run(['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                 '-fobjc-arc', '-fsanitize=address,undefined', '-I', RT, src,
                 '-framework', 'Foundation', '-framework', 'IOKit', '-o', exe])
            return exe
        exe = build('slowlog', harness)
        module = runpy.run_path(str(RT / 'session_report.py'))
        reader_before = dict(module)
        exec(OLD_READER, reader_before)
        source = (RT / 'slowlog.m').read_text()
        old_source = source[:source.index('void slowlog_print(')] + OLD_WRITER
        old_exe = build('old_writer', harness.replace('#include "slowlog.m"', old_source))
        for mode in ('switch-PC', 'switch-X360'):
            text = run([exe, mode])
            rows = boundary_evidence(module, text)
            assert rows[1]['lum_wait_sum_ms'] == '0.000000'
            assert rows[1]['lum_wait_max_ms'] == '0.000000'
            # Independent reader input: duplicate IDs can carry disjoint deltas.
            pieces = [dict(rows[0], lum_wait_sum_ms='300', lum_wait_count='15'),
                      dict(rows[1], lum_wait_sum_ms='500', lum_wait_max_ms='10', lum_wait_count='25')]
            assert 'lum_wait_sum_ms total 800.000' in module['slowlog_evidence'](pieces)
            assert 'lum_wait_max_ms 20.000' in module['slowlog_evidence'](pieces)
        for label, reader, text in (
                ('reader', reader_before, run([exe, 'switch-X360'])),
                ('writer', module, run([old_exe, 'switch-PC'])),
                ('reader+writer', reader_before, run([old_exe, 'switch-X360']))):
            try:
                boundary_evidence(reader, text)
            except AssertionError as failure:
                assert 'lum_wait_sum_ms total 0.000' in str(failure)
                assert 'lum_wait_max_ms 0.000' in str(failure)
            else:
                raise AssertionError(label + ' baseline unexpectedly passed')
            print('PASS 8f181f3c ' + label + ' control: FAIL (0 ms / 0 ms)')
        print('PASS X360 -> PC mid-second writer/reader: 800 ms total / 20 ms maximum; repeated snapshot adds zero')
        path = tmp / 'perf.tsv'
        path.write_text(run([exe, 'switch-PC']))
        assert 'boundary luminance waits retained' in checker['check'](path)
        good = run([exe, 'X360'])
        path.write_text(good)
        print(checker['check'](path))
        assert 'PASS' in run([sys.executable, '-I', RT / 'slowlog_check.py', path])
        for column in sorted(EVIDENCE):
            lines = good.splitlines()
            columns = lines[0].split('\t')
            index = columns.index(column)
            path.write_text('\n'.join('\t'.join(v for i, v in enumerate(line.split('\t')) if i != index) for line in lines) + '\n')
            output = run([sys.executable, '-I', RT / 'slowlog_check.py', path], expected=1)
            assert output == ''  # failures go to stderr; exit status is the proof
        print('PASS missing-column controls: 20/20 FAIL (exit 1)')
        mutant = build('zero_wait', harness.replace('slowlog_lum_wait(lum_wait_ns);', 'slowlog_lum_wait(0);'))
        path.write_text(run([mutant, 'X360']))
        run([sys.executable, '-I', RT / 'slowlog_check.py', path], expected=1)
        print('PASS zeroed X360 wait-hook control: FAIL (exit 1)')
        path.write_text(run([exe, 'PC']))
        assert 'PC rows, zero luminance waits' in checker['check'](path)
        off = run([exe, 'off'])
        row = next(csv.DictReader(off.splitlines(), delimiter='\t'))
        assert row['slowlog_enabled'] == '0' and row['lum_wait_count'] == '0'
        print('PASS PC zero / switch-off zero / diagnostics-off X360 positive')
        print(run([exe, 'background-off']), end='')
        print(run([exe, 'stress']), end='')
        if '--background' in sys.argv or os.environ.get('NFS_GPU_SANDBOX') == '1':
            output = run([exe, 'background'])
            dest = RT.parent / '.metal-cache' / 'slowlog-latest.txt'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(output)
            print(output, end='')


if __name__ == '__main__':
    main()
