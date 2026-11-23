"""Asset-free pass and presentation proof using production effect-pass hooks.
Premise: 7 total attempted draws, 0 attributed draws, 0 presentation records.
Fixed: exactly 7 attributed draws (3+4), deferred=2, original serial=42;
removed BeginPass attribution: 0 attributed draws (check exits 1).
Production callbacks reversed: original serials 18/18; wrong-serial control 0/18.
Reordered callbacks, diagnostics-off NULL tickets, frozen F10 history, overflow,
unattributed draws, repeated passes, dictionary saturation, disabled cost paths.
Covers runtime/blackbox_render.m, runtime/blackbox_render.h, runtime/blackbox.c,
runtime/d3dx_effect.c, runtime/render.m, runtime/render_internal.h,
runtime/render_post.m, runtime/render_target.m, runtime/render_present.m,
runtime/render_thread.m, runtime/runtime_sources.json and runtime/build.sh.
"""
import os
from pathlib import Path
import re
import runpy
import sys
import statistics
import subprocess
import tempfile

R = Path(__file__).resolve().parent


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    end, level = brace + 1, 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


PRE = r'''
#include "blackbox_render.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
/* A preallocated command buffer completes immediately. This measures the
 * production global callback/ticket path without driver scheduling or GPU work. */
@interface BenchBuffer : NSObject
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler;
@end
@implementation BenchBuffer
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler { handler((id)self); }
- (MTLCommandBufferStatus)status { return MTLCommandBufferStatusCompleted; }
- (NSError *)error { return nil; }
- (CFTimeInterval)GPUStartTime { return 1; }
- (CFTimeInterval)GPUEndTime { return 1.01; }
@end
static id<MTLCommandBuffer> bench_buffer;
static uint64_t draws, deferred;
uint64_t frame_serial=42;
static unsigned reads;
uint64_t d3d9_draw_count(void) { ++reads; return draws; }
uint64_t render_deferred_count(void) { ++reads; return deferred; }
uint64_t render_failure_count(void) { ++reads; return 1; }
typedef struct { uint32_t esp, index; } Cpu;
typedef struct { char name[64]; uint16_t blackbox_id; } Pass;
typedef struct { char name[64]; uint32_t npasses; Pass *passes; } Technique;
typedef struct { char name[64]; Technique *cur; Pass *pass; int in_pass; } Effect;
static Pass passes[]={{"World",0},{"HUD",0}};
static Technique tech={"Main",2,passes};
static Effect effect={"IDI_WORLD_FX",&tech,0,0};
static char diag_fx_pass[192];
#define FX (&effect)
#define D3D_OK 0
#define D3DERR_INVALIDCALL 1
#define RET(value,n) do { (void)c; return value; } while(0)
static uint32_t arg(Cpu *c, int n) { (void)n;return c->index; }
static void apply_pass(Cpu *c,Effect *fx,Pass *p,int update) { (void)c;(void)fx;(void)p;(void)update; }
'''
BODY = r'''
static void frame(uint64_t n) {
    BlackboxTiming t={.draws=(uint32_t)draws};
    blackbox_record_frame(BLACKBOX_MW,n,6,&t,blackbox_now_ns());
}
static void fixture(void) {
    Cpu c={0};
    assert(!e_BeginPass(&c));draws+=3;assert(!e_EndPass(&c));
    c.index=1;strcpy(effect.name,"IDI_CAR_FX");assert(!e_BeginPass(&c));draws+=4;deferred=2;assert(!e_EndPass(&c));
    frame(900);
    frame_serial=43;frame(901);
    // Complete frames and shown drawables out of order, after the last sample.
    blackbox_render_present(42,BB_PRESENT_SHOWN,0,0,0,1.25);
    blackbox_render_present(41,BB_RENDER_GPU,4,1.0,1.01,0);
    blackbox_render_present(42,BB_PRESENT_GPU,4,1.1,1.12,0);
    blackbox_render_present(42,BB_RENDER_GPU,4,1.0,1.02,0);
    blackbox_render_present(41,BB_RENDER_SUBMIT,0,0,0,0);
    blackbox_render_present(42,BB_RENDER_SUBMIT,0,0,0,0);
    blackbox_render_host(42,BB_HOST_POST);
    blackbox_render_host(42,BB_HOST_COPY);
    blackbox_render_present(43,BB_PRESENT_MISSING,0,0,0,0);
    blackbox_menu(1);
    frame_serial=99;frame(999);
    blackbox_render_present(99,BB_PRESENT_SHOWN,0,0,0,9);
    blackbox_export(stdout,BB_F10);
    blackbox_menu(0);
    blackbox_export(stdout,BB_HANG);
}
static void limits(void) {
    for(unsigned i=0;i<33;++i) {
        char name[32];snprintf(name,sizeof name,"p%u",i);
        blackbox_render_pass_begin(blackbox_render_pass_id("fx","t",name));++draws;
        blackbox_render_pass_end();
    }
    frame(1);
    ++draws;frame(2); // No active effect.
    uint16_t id=blackbox_render_pass_id("fx","t","p0");
    blackbox_render_pass_begin(id);++draws;blackbox_render_pass_end();
    blackbox_render_pass_begin(id);draws+=2; // Repeated pass; frame crossing.
    frame(3);draws+=2;frame(4);blackbox_render_pass_end();
    for(unsigned i=33;i<256;++i) {
        char name[32];snprintf(name,sizeof name,"p%u",i);
        assert(blackbox_render_pass_id("fx","t",name));
    }
    assert(!blackbox_render_pass_id("fx","t","saturated"));
    blackbox_render_pass_begin(0);++draws;blackbox_render_pass_end();frame(5);
    blackbox_export(stdout,BB_HANG);
}
static void off(void) {
    fixture();BlackboxRenderFrame out;
    assert(!blackbox_render_take(&out));assert(reads==0);
}
static double batch(int tap) {
    const unsigned n=500000;
    uint64_t start=clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
    for(unsigned i=0;i<n;++i) {
        if(tap)blackbox_render_pass_begin(1);
        draws+=3;
        if(tap)blackbox_render_pass_end();
        if(tap)blackbox_render_pass_begin(2);
        draws+=4;
        if(tap)blackbox_render_pass_end();
        // Baseline copies the same existing frame record and ring publication.
        frame(i);
        if(tap) {
            blackbox_render_host(frame_serial,BB_HOST_POST);
            blackbox_render_host(frame_serial,BB_HOST_COPY);
            blackbox_render_present(frame_serial,BB_RENDER_SUBMIT,0,0,0,0);
            blackbox_render_present(frame_serial,BB_RENDER_GPU,4,1,1.01,0);
            blackbox_render_final(bench_buffer,frame_serial);
            blackbox_render_present(frame_serial,BB_PRESENT_SHOWN,0,0,0,1);
        }
    }
    return (double)(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)-start)/n;
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"off"))off();
    else if(!strcmp(argv[1],"limits"))limits();
    else if(!strcmp(argv[1],"bench")) {
        bench_buffer=(id)[BenchBuffer new];
        batch(1);
        printf("%.3f\n",batch(1));
    } else fixture();
}
'''


def presentation_fixture(tmp):
    clip = runpy.run_path(str(R / 'clip_present_test.py'))
    pre = '#include "blackbox_render.h"\n#import' + clip['PRE'].split('#import', 1)[1]
    pre = pre.replace('static unsigned checks,', 'static NSMutableArray *pending_shown, *pending_done;\nstatic unsigned checks,')
    pre = pre.replace('@property id<MTLTexture> texture;', '@property id<MTLTexture> texture;\n- (CFTimeInterval)presentedTime;')
    pre = pre.replace('(void)handler; ++completions;', '[pending_shown addObject:@[[handler copy], self]]; ++completions;')
    pre = pre.replace('@implementation Drawable\n', '@implementation Drawable\n- (CFTimeInterval)presentedTime { return 1.25; }\n')
    pre = pre.replace('@implementation Buffer\n', '@implementation Buffer\n- (MTLCommandBufferStatus)status { return MTLCommandBufferStatusCompleted; }\n- (NSError *)error { return [NSError errorWithDomain:@"fixture" code:-17 userInfo:nil]; }\n- (CFTimeInterval)GPUStartTime { return 1; }\n- (CFTimeInterval)GPUEndTime { return 1.02; }\n')
    pre = pre.replace('(void)handler; }', '[pending_done addObject:@[[handler copy], self]]; }')
    pre += 'uint64_t frame_serial=1;\nuint64_t d3d9_draw_count(void){return 0;}\nuint64_t render_deferred_count(void){return 0;}\nuint64_t render_failure_count(void){return 0;}\n'
    post = clip['POST'].replace('unsigned cases=0;', 'unsigned cases=0;pending_shown=[NSMutableArray new];pending_done=[NSMutableArray new];')
    post = post.replace('present_show((PresentTiming){0});', 'BlackboxTiming bt={0};blackbox_record_frame(BLACKBOX_MW,cases+42,6,&bt,blackbox_now_ns());present_show((PresentTiming){.frame=cases+42});')
    post = post.replace(' printf("%u cases,', '''
 frame_serial=99999;
 for(NSArray *entry in [pending_done reverseObjectEnumerator]) {
  MTLCommandBufferHandler handler=entry[0];handler(entry[1]);
 }
 for(NSArray *entry in [pending_shown reverseObjectEnumerator]) {
  MTLDrawablePresentedHandler handler=entry[0];handler(entry[1]);
 }
 blackbox_export(stdout,BB_HANG);
 printf("%u cases,''')
    for variant in ('ticket', 'wrong-serial', 'ticket-full'):
        body = clip['present']
        if variant == 'wrong-serial':
            body = body.replace('duration, NULL, timing.frame)', 'duration, NULL, frame_serial)')
        src, exe = tmp / (variant + '.m'), tmp / variant
        extra = 'for(unsigned i=0;i<15;++i)blackbox_render_final((id)[Buffer new],2000+i);' if variant == 'ticket-full' else ''
        actual_post = post.replace(' frame_serial=99999;', extra + ' frame_serial=99999;')
        src.write_text(pre + body + actual_post)
        p = subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', '-Wno-unused-function', '-fobjc-arc', '-fblocks', '-I', str(R), str(src), str(R / 'blackbox.c'), str(R / 'blackbox_render.m'), '-framework', 'Cocoa', '-framework', 'Metal', '-framework', 'QuartzCore', '-o', str(exe)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-4000:]
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr[-2000:]
        assert 'error=-17' in out.stdout
        found = sum(f'f{serial} PRESENT stage=shown' in out.stdout and f'f{serial} PRESENT stage=final_gpu' in out.stdout for serial in range(42,78,2))
        assert found == (0 if variant == 'wrong-serial' else 18), (variant, found, out.stdout[-3000:])
        if variant == 'ticket-full':
            assert 'f2014 PRESENT stage=ticket_lost' in out.stdout
            assert 'f2013 PRESENT stage=final_gpu' in out.stdout
            assert out.stdout.count('PRESENT stage=final_gpu') == 32
        if variant != 'wrong-serial':
            assert 'f42 PRESENT stage=host_encoder status=0 a=4' in out.stdout
        print(f'production presentation callbacks reversed: {variant} original serials={found}/18')


def main():
    effect = (R / 'd3dx_effect.c').read_text()
    hooks = function(effect, 'static int e_BeginPass(') + '\n' + function(effect, 'static int e_EndPass(')
    assert 'blackbox_render_pass_begin(fx->pass->blackbox_id);' in hooks
    # Core-only baseline uses its weak renderer hook. The same frame ring remains.
    with tempfile.TemporaryDirectory(prefix='blackbox-render-') as tmp:
        tmp = Path(tmp)
        exes = {}
        for variant in ('fixed', 'broken', 'baseline'):
            body = hooks.replace('blackbox_render_pass_begin(fx->pass->blackbox_id);', '(void)fx->pass->blackbox_id;') if variant == 'broken' else hooks
            src, exe = tmp / (variant + '.m'), tmp / variant
            src.write_text(PRE + body + BODY)
            sources = [str(src), str(R / 'blackbox.c')]
            if variant != 'baseline':
                sources += [str(R / 'blackbox_render.m')]
            else:
                src.write_text(PRE + '''
uint16_t blackbox_render_pass_id(const char *a,const char *b,const char *c) { (void)a;(void)b;(void)c;return 0; }
void blackbox_render_pass_begin(uint16_t id) { (void)id; }
void blackbox_render_pass_end(void) {}
void blackbox_render_final(id<MTLCommandBuffer> b,uint64_t serial) { (void)b;(void)serial; }
void blackbox_render_host(uint64_t a,uint32_t b) { (void)a;(void)b; }
void blackbox_render_present(uint64_t a,uint32_t b,uint32_t c,double d,double e,double f) { (void)a;(void)b;(void)c;(void)d;(void)e;(void)f; }
''' + body + BODY)
            p = subprocess.run(['clang', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'Metal', '-DNFS_GAME=1', '-DNFS_GAME_PRIVATE=2', '-I', str(R), *sources, '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-4000:]
            exes[variant] = exe

        def run(variant, mode='fixture', **env):
            p = subprocess.run([str(exes[variant]), mode], env=dict(os.environ, **({"NFS_BLACKBOX":"1"} | env)), capture_output=True, text=True)
            assert p.returncode == 0, (variant, mode, p.stderr[-2000:])
            return p.stdout

        def attributed(text):
            row = re.search(r'RENDER serial=42 attempted_d3d_draws=7 deferred=2 unattributed=(\d+) pass_overflow_draws=0 passes=(\d+)([^\n]*)', text)
            if not row:
                return 0
            return sum(int(n) for n in re.findall(r'\d+:(\d+)', row[3]))

        for variant, expected in (('baseline', 0), ('broken', 0), ('fixed', 7)):
            out = run(variant)
            assert attributed(out) == expected, (variant, out[:3000])
            report = tmp / (variant + '.txt')
            report.write_text(out)
            check = subprocess.run([sys.executable, '-I', __file__, '--check', str(report)], capture_output=True, text=True)
            assert check.returncode == int(expected != 7), (variant, check.stdout, check.stderr)
            print(f'{variant}: attributed_draws={expected}/7 check_exit={check.returncode}')
            if variant != 'fixed':
                continue
            frozen, live = out.split('blackbox v1')[1:]
            assert 'OBSERVED f901' in frozen and 'f99 PRESENT' not in frozen
            assert 'LATEST_PRESENT serial=43 submit=missing_or_pending frame_gpu=missing_or_pending final_gpu=missing_or_pending shown=no_drawable' in frozen
            assert 'f99 PRESENT stage=shown' in live
            for section in (frozen, live):
                for stage in ('shown', 'final_gpu', 'frame_gpu', 'submit'):
                    assert f'f42 PRESENT stage={stage}' in section, (stage, section[-3000:])
                assert 'f41 PRESENT stage=frame_gpu' in section
                assert 'f43 PRESENT stage=missing' in section
                assert 'attempted_d3d_draws=7 deferred=2 unattributed=0 pass_overflow_draws=0 passes=2 1:3 2:4' in section
                assert 'f42 PRESENT stage=host_encoder status=0 a=1' in section
                assert 'f42 PRESENT stage=host_encoder status=0 a=2' in section
                assert 'COVERAGE present records=' in section
            # Hash witnesses pin the two named passes without exporting raw names.
            assert 'PASS_ID 1 effect=IDI_WORLD_FX ' in out and 'PASS_ID 2 effect=IDI_CAR_FX ' in out
            assert 'failed=1' in out
            assert 'PASS_GPU unavailable' in out
        out = run('fixed', 'limits')
        assert 'attempted_d3d_draws=33 deferred=0 unattributed=0 pass_overflow_draws=1 passes=32' in out
        assert 'attempted_d3d_draws=1 deferred=0 unattributed=1' in out
        assert 'attempted_d3d_draws=3 deferred=0 unattributed=0 pass_overflow_draws=0 passes=1 1:3' in out
        assert 'attempted_d3d_draws=2 deferred=0 unattributed=0 pass_overflow_draws=0 passes=1 1:2' in out
        assert 'disabled NFS_BLACKBOX=0' in run('fixed', 'off', NFS_BLACKBOX='0')
        print('limits, active-pass frame crossing, unknown IDs, disabled counter reads: PASS')
        presentation_fixture(tmp)
        # Compare whole existing frame publication against the tap with two passes.
        base, tap = [], []
        for pair in range(12):
            if pair & 1:
                tap.append(float(run('fixed', 'bench')))
                base.append(float(run('baseline', 'bench')))
            else:
                base.append(float(run('baseline', 'bench')))
                tap.append(float(run('fixed', 'bench')))
        delta = [b-a for a,b in zip(base,tap)]
        print(f'12 batches x 500000 frames: baseline={statistics.median(base):.1f} tap={statistics.median(tap):.1f} added={statistics.median(delta):.1f} ns/frame (job-1 same-arm spread 820000 ns/frame)')
        assert statistics.median(delta) < 820000

    present = (R / 'render_present.m').read_text()
    assert 'blackbox_render_complete(serial, BB_RENDER_GPU' in present
    assert 'blackbox_render_complete(serial, BB_PRESENT_GPU' in present
    assert present.count('blackbox_render_present(serial, BB_PRESENT_SHOWN') == 2
    assert 'blackbox_render_present(serial, BB_RENDER_SUBMIT' in present
    assert 'present_draw(l, src, clip_headroom, duration, NULL, timing.frame)' in present
    # Replay installs completion handlers on the real buffer before commit and
    # executes after-commit blocks afterward, preserving their captured tickets.
    thread = (R / 'render_thread.m').read_text()
    assert 'addCompletedHandler:' in thread and 'c->after' in thread


def check(path):
    text = Path(path).read_text()
    row = re.search(r'RENDER serial=42 attempted_d3d_draws=7 deferred=2 unattributed=0 pass_overflow_draws=0 passes=2 1:3 2:4', text)
    return 0 if row and all(f'f42 PRESENT stage={stage}' in text for stage in ('shown', 'final_gpu', 'frame_gpu', 'submit')) else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        sys.exit(check(sys.argv[2]))
    main()
