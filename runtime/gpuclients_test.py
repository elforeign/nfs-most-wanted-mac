#!/usr/bin/env python3
"""







"""
from pathlib import Path
import csv
import os
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PY = sys.executable

HARNESS = r'''
#import <Metal/Metal.h>
#include "gpuclients.m"
#include <assert.h>
static int checks;
#define CHECK(c) do { ++checks; if (!(c)) { fprintf(stderr,"FAIL check %d: %s\n", checks, #c); return 1; } } while (0)
static NSDictionary *client(int pid, NSString *name, uint64_t ns) {
    return decode_client([NSString stringWithFormat:@"pid %d, %@",pid,name],
                         @[@{@"accumulatedGPUTime":@(ns), @"lastSubmittedTime":@123}]);
}
static NSDictionary *states;
static CFDictionaryRef fake_samples(CFTypeRef s, CFDictionaryRef c, CFTypeRef o) {
    (void)s;(void)c;(void)o; return CFBridgingRetain(states);
}
static CFDictionaryRef fake_delta(CFDictionaryRef a, CFDictionaryRef b, CFTypeRef o) {
    (void)a;(void)o; return CFRetain(b);
}
static int fake_iterate(CFDictionaryRef c, int (^block)(CFDictionaryRef)) { return block(c); }
static int fake_count(CFDictionaryRef c) { return (int)[(__bridge NSDictionary *)c count]; }
static CFStringRef fake_name(CFDictionaryRef c, int i) {
    return (__bridge CFStringRef)[(__bridge NSDictionary *)c objectForKey:@(i)][@"name"];
}
static int64_t fake_residency(CFDictionaryRef c, int i) {
    return [[(__bridge NSDictionary *)c objectForKey:@(i)][@"res"] longLongValue];
}
static int fixtures(void) {
    GPUClientsSample out;
    empty_sample(&out);
    CHECK(isnan(out.self_ms) && !strcmp(out.mhz,"-") && !strcmp(out.top,"-"));
    CHECK(!decode_client(@123, @[]));
    CHECK(!decode_client(@"pid 1, bad", @{}));
    CHECK(!decode_client(@"unrecognised", @[]));
    CHECK(!decode_client(@"pid 1, test", @[@{}]));
    CHECK(!decode_client(@"pid 1", @[@{@"accumulatedGPUTime":@10}]));
    NSDictionary *d = decode_client(@"pid 42, a:b,c\tx", @[@{@"accumulatedGPUTime":@12}, @{@"accumulatedGPUTime":@23}, @"bad"]);
    CHECK([d[@"ns"] unsignedLongLongValue] == 35 && [d[@"name"] isEqual:@"a_b_c_x"]);
    NSDictionary *a = @{@1:client(42,@"self",1000000000), @2:client(9,@"WindowServer",2000000000),
                        @3:client(10,@"Safari",5000000000), @4:client(11,@"reset",2000000),
                        @5:client(42,@"self",1000000000), @6:client(15,@"exited",9000000000),
                        @7:client(16,@"fourth",10000000), @8:client(17,@"third",10000000),
                        @9:client(18,@"pid-reused",10000000)};
    NSDictionary *b = @{@1:client(42,@"self",1020000000), @2:client(9,@"WindowServer",2200000000),
                        @3:client(10,@"Safari",5100000000), @4:client(11,@"reset",1000000),
                        @5:client(42,@"self",1010000000), @10:client(9,@"new",900000000000),
                        @7:client(16,@"fourth",12000000), @8:client(17,@"third",14000000),
                        @9:client(19,@"pid-reused",30000000)};
    diff_clients(a,b,2,42,&out);
    CHECK(fabs(out.self_ms-15) < 1e-6);
    CHECK(fabs(out.other_ms-153) < 1e-6);
    CHECK(!strcmp(out.top,"WindowServer:100.00,Safari:50.00,third:2.00"));
    empty_sample(&out); diff_clients(nil,b,1,42,&out); CHECK(isnan(out.self_ms));
    diff_clients(a,nil,1,42,&out); CHECK(isnan(out.other_ms));
    diff_clients(a,b,0,42,&out); CHECK(isnan(out.self_ms));
    diff_clients(b,@{},1,42,&out); CHECK(out.self_ms == 0 && out.other_ms == 0);
    CHECK(!strcmp(out.top,"-"));
    // Explicit frequency labels, mixed MHz/GHz; idle is excluded from active residency.
    GPUClients s = {0};
    s.subscription = (__bridge CFDictionaryRef)@{};
    s.samples=fake_samples; s.delta=fake_delta; s.iterate=fake_iterate;
    s.count=fake_count; s.name=fake_name; s.residency=fake_residency;
    states = @{@0:@{@"name":@"IDLE",@"res":@9000}, @1:@{@"name":@"500 MHz",@"res":@1}, @2:@{@"name":@"1 GHz",@"res":@3}};
    empty_sample(&out); clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"-"));
    clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"875.0"));
    states = @{@0:@{@"name":@"OFF",@"res":@100}, @1:@{@"name":@"P1",@"res":@1}, @2:@{@"name":@"P2",@"res":@3}};
    clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"state=P2"));
    states = @{@0:@{@"name":@"500",@"res":@100}};
    clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"state=500"));
    states = @{@0:@{@"name":@"IDLE",@"res":@100}};
    empty_sample(&out); clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"-"));
    if (s.before) CFRelease(s.before);
    s.before = NULL; s.subscription = NULL;
    clock_sample(&s,&out); CHECK(!strcmp(out.mhz,"-"));
    char *text=NULL; size_t length=0; FILE *f=open_memstream(&text,&length);
    gpuclients_print(f,&out); fclose(f);
    CHECK(!strcmp(text,"\t-\t-\t-\t-\t-")); free(text);
    gpuclients_cached(&out); CHECK(isnan(out.other_ms));
    CHECK(!pthread_mutex_lock(&cache_lock));
    gpuclients_cached(&out); CHECK(isnan(out.other_ms)); pthread_mutex_unlock(&cache_lock);
    empty_sample(&cache); cache.other_ms=123; cache_time=monotime();
    gpuclients_cached(&out); CHECK(out.other_ms == 123);
    cache_time=monotime()-4; gpuclients_cached(&out); CHECK(isnan(out.other_ms));
    printf("PASS native GPU-client fixtures: %d checks\n",checks);
    return 0;
}
static int registry(void) {
    GPUClients *s=gpuclients_create();
    CHECK(s);
    printf("IOReport lib=%d channels=%d subscription=%d\n", !!s->lib, !!s->channels, !!s->subscription);
    if(s->channels) printf("channels: %.2000s\n",[(__bridge id)s->channels description].UTF8String);
    double sum=0, max=0;
    for(int i=0;i<5;i++) {
        GPUClientsSample out; double start=monotime(); gpuclients_sample(s,&out);
        double ms=(monotime()-start)*1000; sum+=ms; max=fmax(max,ms);
        printf("registry sample_ms=%.3f",ms); gpuclients_print(stdout,&out); puts("");
        if(i<4) usleep(1000000);
    }
    printf("registry mean_ms=%.3f max_ms=%.3f (target mean <2 ms; no Metal device created)\n",sum/5,max);
    gpuclients_destroy(s); return 0;
}
static int live(void) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice(); CHECK(device);
    NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "kernel void burn(device float *out [[buffer(0)]], uint i [[thread_position_in_grid]]) {"
        "float x=float(i)*0.001f; for(uint n=0;n<2048;n++) x=sin(x)+1.01f; out[i]=x; }";
    id<MTLLibrary> lib=[device newLibraryWithSource:source options:nil error:&error];
    if(!lib) fprintf(stderr,"Metal compile: %s\n",error.description.UTF8String); CHECK(lib);
    id<MTLComputePipelineState> pipeline=[device newComputePipelineStateWithFunction:[lib newFunctionWithName:@"burn"] error:&error]; CHECK(pipeline);
    id<MTLCommandQueue> q=[device newCommandQueue]; CHECK(q);
    id<MTLBuffer> buffer=[device newBufferWithLength:16384*sizeof(float) options:MTLResourceStorageModeShared]; CHECK(buffer);
    GPUClients *s=gpuclients_create(); CHECK(s); GPUClientsSample out;
    gpuclients_sample(s,&out); // baseline after our Metal client exists
    double sum=0,max=0,total_self=0;
    for(int i=0;i<5;i++) {
        double until=monotime()+1;
        do { @autoreleasepool {
            id<MTLCommandBuffer> cb=[q commandBuffer];
            id<MTLComputeCommandEncoder> enc=[cb computeCommandEncoder];
            [enc setComputePipelineState:pipeline]; [enc setBuffer:buffer offset:0 atIndex:0];
            [enc dispatchThreads:MTLSizeMake(16384,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];
            [enc endEncoding]; [cb commit]; [cb waitUntilCompleted]; CHECK(cb.status==MTLCommandBufferStatusCompleted);
        } } while(monotime()<until);
        double start=monotime(); gpuclients_sample(s,&out);
        double ms=(monotime()-start)*1000; sum+=ms; max=fmax(max,ms);
        if(isfinite(out.self_ms)) total_self+=out.self_ms;
        printf("live sample_ms=%.3f",ms); gpuclients_print(stdout,&out); puts("");
    }
    printf("live self_ms_sum=%.3f sampler_mean_ms=%.3f max_ms=%.3f target_mean_ms=2.000\n",total_self,sum/5,max);
    gpuclients_destroy(s);
    CHECK(total_self>0); CHECK(sum/5<2);
    puts("PASS live GPU-client delta and sampler cost"); return 0;
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc>1 && !strcmp(argv[1],"--gpu")) return live();
    if(argc>1 && !strcmp(argv[1],"--registry")) return registry();
    return fixtures();
} }
'''


def run(args, expected=0, required_error=None):
    p = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=120)
    assert p.returncode == expected, f"{args[0]} rc={p.returncode}: {p.stdout[-2000:]}{p.stderr[-3000:]}"
    if required_error:
        assert required_error in p.stderr, p.stderr[-3000:]
    return p.stdout


def make_session(folder, scenario):
    folder.mkdir()
    (folder / "session.txt").write_text("")
    keys = ("t frame state fps p50 p99 max busy_p50 idle_sleep idle_drawable idle_gpu gpu_mean draws over rss_mb "
            "gpu_other_ms gpu_top gpu_mhz").split()
    if scenario == "legacy":
        keys = keys[:-3]
    with (folder / "perf.tsv").open("w") as out:
        writer = csv.DictWriter(out, keys, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for t in range(60):
            slow = 40 <= t < 46
            row = dict.fromkeys(keys, "0")
            row.update(t=t, frame=t * 60, state="race", fps=50 if slow else 120,
                       p50=20 if slow else 8, p99=22, max=25, busy_p50=3,
                       gpu_mean=18 if slow else 7, draws=3000, rss_mb=2000,
                       gpu_other_ms=200 if slow and scenario == "contention" else 0,
                       gpu_top="Safari:120.00,WindowServer:80.00" if slow and scenario == "contention" else "-",
                       gpu_mhz=500 if slow and scenario == "clock" else 1000)
            if scenario == "missing":
                row.update(gpu_other_ms="-", gpu_mhz="-")
            if scenario == "state":
                row.update(gpu_mhz="state=P2")
            if scenario == "legacy":
                for key in ("gpu_other_ms", "gpu_top", "gpu_mhz"):
                    del row[key]
            if slow and scenario in ("weak", "boundary_contention", "both"):
                row["gpu_other_ms"] = {"weak":100, "boundary_contention":165, "both":200}[scenario]
            if slow and scenario in ("boundary_clock", "above_clock", "both"):
                row["gpu_mhz"] = {"boundary_clock":700, "above_clock":701, "both":500}[scenario]
            writer.writerow(row)


def main():
    with tempfile.TemporaryDirectory(prefix="gpuclients-test-") as scratch:
        tmp = Path(scratch)
        report = (HERE / "session_report.py").read_text()
        mutant = tmp / "mutant_report.py"
        mutation = "others.append(other)"
        assert report.count(mutation) == 1
        mutant.write_text(report.replace(mutation, "others.append(0)"))
        fixed_count = broken_count = 0
        extra = {"weak":"undecided", "boundary_contention":"contention", "boundary_clock":"clock",
                 "above_clock":"undecided", "both":"contention"}
        for scenario in ("contention", "clock", "undecided", "missing", "state", "legacy", *extra):
            folder = tmp / scenario
            make_session(folder, scenario)
            output = run([PY, "-I", HERE / "session_report.py", folder])
            verdict = re.findall(r"verdict (\w+)", output)
            expected = extra.get(scenario, scenario if scenario in ("contention", "clock") else "undecided")
            assert verdict == [expected], output
            if scenario in ("contention", "clock", "undecided"):
                fixed_count += verdict == [expected]
                broken = run([PY, "-I", mutant, folder])
                broken_count += re.findall(r"verdict (\w+)", broken) == [expected]
            if scenario == "contention":
                assert "gpu_other_ms mean 200.0" in output and "Safari:720.0,WindowServer:480.0" in output, output
            if scenario == "clock":
                assert "gpu_mhz mean 500.0 vs race median 1000.0" in output, output
        assert fixed_count == 3 and broken_count == 2
        print(f"PASS report verdicts: fixed {fixed_count}/3; ignores-other_ms mutant {broken_count}/3 (rejected); 8 missing/legacy/state/threshold cases")
        # Build the very same sampler plus fixture/standalone Metal entry points.
        (tmp / "harness.m").write_text(HARNESS)
        source = (HERE / "gpuclients.m").read_text()
        (tmp / "gpuclients.m").write_text(source)
        (tmp / "gpuclients.h").write_text((HERE / "gpuclients.h").read_text())
        def build(name):
            run(["clang", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-fobjc-arc",
                 "-fsanitize=address,undefined", tmp / "harness.m", "-framework", "Foundation",
                 "-framework", "IOKit", "-framework", "Metal", "-o", tmp / name])
        build("sampler")
        print(run([tmp / "sampler"]), end="")
        mutation = "(y - x) / 1e6 / elapsed"
        assert source.count(mutation) == 1
        (tmp / "gpuclients.m").write_text(source.replace(mutation, "y / 1e6 / elapsed"))
        build("broken")
        run([tmp / "broken"], expected=1, required_error="FAIL check 8:")
        print("PASS lifetime-total mutant rejected at check 8 (exit 1)")
        if "--registry" in sys.argv:
            print(run([tmp / "sampler", "--registry"]), end="")
        if "--gpu" in sys.argv or os.environ.get("CODEX_GPU_CHECK") == "1":
            print(run([tmp / "sampler", "--gpu"]), end="")
        else:
            print("GPU PENDING: run on a Metal-capable host: CODEX_GPU_CHECK=1 runtime/offline_tests.sh gpuclients_test (standalone Metal self_ms > 0 and mean sampling <2 ms)")


if __name__ == "__main__":
    main()
