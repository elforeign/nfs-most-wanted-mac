"""Production SimTask constructor hook + translated constructor/Run + callback recorder.

AIActionRace::BeginAction (0042EF90) requests rate=.25, delay=1, mode=0.
Before: first callbacks at 8/24/80 ticks (133.333/200/333.333 ms at 60/120/240 Hz),
11/26726 failed assertions (mode 0 timing: 2; mode 1 timing/dt: 4).
After: 8/16/32 ticks (133.333 ms), 0/26750 failed assertions.
The pinned predecessor below is compiled every run; no history lookup is needed.
Both modes, delivered dt, zero delay, full-rate tasks, pauses and byte-identical
60 Hz CPU/task/stack/callback traces are checked. No game process or emulation.
Requires generated recomp/out; constants 00890968/0089096C are retail 0/1.
Windows retains the translated original (hook-coverage: original), not pacing.c.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Pinned pre-fix scaling block from f_006ECCD0; diagnostics are inactive here.
PREDECESSOR = r'''
void predecessor(Cpu *c) {
    if (sim_hz && sim_hz != 60) {
        float k = 60.0f / (float)sim_hz, rate = MF32(c->esp + 8), delay = MF32(c->esp + 16);
        if (rate > 0.0f && rate < 1.0f) MF32(c->esp + 8) = rate * k;
        if (delay > 0.0f) MF32(c->esp + 16) = delay / k;
    }
    f_006ECCD0_orig(c);
}
'''

HARNESS = r'''
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>

#define IMAGE_SIZE 0xA00000
#define TASK 0x10000
#define STACK 0x20000
#define TASKABLE 0x30000
#define VTABLE 0x40000
#define CALLBACK 0x50000
#define TICKS 128
uint8_t *G;
static int sim_hz, diag_on;
static unsigned checks, failures, tick;
typedef void (*Constructor)(Cpu *);
typedef struct Event { unsigned tick; uint32_t handle, dt; } Event;
typedef struct State { Cpu cpu; uint8_t task[40], stack[64]; } State;
typedef struct Trace {
    State initial, states[TICKS];
    Event events[TICKS];
    unsigned count;
} Trace;
static Trace *recording;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; \
    fprintf(stderr, "line %d: %s\n", __LINE__, #x); } } while (0)
const char *diag_symbol(uint32_t a, uint32_t *off) { (void)a; (void)off; abort(); }
void diag_timeline(const char *fmt, ...) { (void)fmt; abort(); }
// Link only maintains the global task list; Run is invoked directly below.
void f_006E89A0(Cpu *c) { c->esp += 4; }
void dispatch(Cpu *c, uint32_t target) {
    assert(target == CALLBACK && c->ecx == TASKABLE);
    assert(recording->count < TICKS);
    recording->events[recording->count++] = (Event){tick, M32(c->esp+4), M32(c->esp+8)};
    c->esp += 12;  // ITaskable::OnTask(handle, dt), thiscall
}
TRANSLATED
LIVE_HOOK
PREDECESSOR
static void state(State *s, const Cpu *c) {
    s->cpu = *c;
    memcpy(s->task, G+TASK, sizeof s->task);
    memcpy(s->stack, G+STACK-32, sizeof s->stack);
}
static void run(Trace *out, Constructor ctor, int hz, float rate, float delay,
                unsigned mode, int vary, int pause) {
    memset(G, 0, IMAGE_SIZE);
    memset(out, 0, sizeof *out);
    recording = out;
    sim_hz = hz;
    MF32(0x890968) = 0;
    MF32(0x89096c) = 1;
    M32(TASKABLE) = VTABLE;
    M32(VTABLE+4) = CALLBACK;
    Cpu c = {.ecx=TASK, .esp=STACK, .cw=0x37f};
    M32(STACK) = 0x12345678;
    M32(STACK+4) = 7;
    MF32(STACK+8) = rate;
    M32(STACK+12) = TASKABLE;
    MF32(STACK+16) = delay;
    M32(STACK+20) = mode;
    ctor(&c);
    CHECK(c.esp == STACK+24 && c.top == 0);
    state(&out->initial, &c);
    for (tick=1; tick<=TICKS; ++tick) {
        c.ecx = TASK; c.esp = STACK;
        MF32(STACK+4) = 1.0f / hz;
        MF32(STACK+8) = pause && tick <= 8 ? 0 : (vary ? (tick&1 ? .5f : 1.5f) : 1.0f) / hz;
        f_006ECD90(&c);
        CHECK(c.esp == STACK+12 && c.top == 0);
        state(&out->states[tick-1], &c);
    }
}
static float seconds(uint32_t bits) { float f; memcpy(&f, &bits, 4); return f; }
static unsigned first(const Trace *t) { return t->count ? t->events[0].tick : 0; }
static void timing(Constructor ctor, const char *label) {
    static Trace t;
    for (unsigned mode=0; mode<2; ++mode) {
        unsigned before=failures;
        printf("%s mode %u first ticks:", label, mode);
        for (int hz=60; hz<=240; hz*=2) {
            run(&t, ctor, hz, .25f, 1, mode, 0, 0);
            unsigned want=8u*hz/60;
            printf(" %u", first(&t));
            CHECK(first(&t) == want);
            for (unsigned i=0; i<t.count; ++i) {
                // Mode 0 supplies step/rate; mode 1 includes initial delayed time.
                float dt = mode == 1 && i == 0 ? 8.0f/60 : 4.0f/60;
                CHECK(fabsf(seconds(t.events[i].dt)-dt) < 1e-6f);
                CHECK(t.events[i].handle == 0);
                if (i) CHECK(t.events[i].tick-t.events[i-1].tick == 4u*hz/60);
            }
        }
        printf("; failures %u\n", failures-before);
    }
}
static void unchanged60(void) {
    static Trace live, old;
    uint8_t *image = malloc(IMAGE_SIZE);
    assert(image);
    const float rates[] = {-.25f, 0, .25f, .5f, .75f, 1, 2};
    const float delays[] = {-1, 0, 1, 35};
    unsigned cases=0;
    for (unsigned mode=0; mode<2; ++mode)
        for (unsigned r=0; r<sizeof rates/sizeof *rates; ++r)
            for (unsigned d=0; d<sizeof delays/sizeof *delays; ++d) {
                run(&old, predecessor, 60, rates[r], delays[d], mode, 1, 1);
                memcpy(image, G, IMAGE_SIZE);
                run(&live, f_006ECCD0, 60, rates[r], delays[d], mode, 1, 1);
                CHECK(memcmp(&live, &old, sizeof live) == 0);
                CHECK(memcmp(image, G, IMAGE_SIZE) == 0);
                ++cases;
            }
    printf("60 Hz: %u byte-identical CPU/task/stack/callback traces and guest images\n", cases);
    free(image);
}
static void controls(void) {
    static Trace live, old;
    for (unsigned mode=0; mode<2; ++mode) {
        for (int hz=30; hz<=480; hz*=2) {
            // No-delay sub-rate callbacks keep cadence and delivered dt exactly.
            run(&live, f_006ECCD0, hz, .25f, 0, mode, 0, 0);
            run(&old, predecessor, hz, .25f, 0, mode, 0, 0);
            CHECK(memcmp(&live, &old, sizeof live) == 0);
            CHECK(first(&live) == (unsigned)hz/15);
            for (unsigned i=0; i<live.count; ++i)
                CHECK(fabsf(seconds(live.events[i].dt)-4.0f/60) < 1e-6f);
            // Full-rate tasks retain the existing delay scaling and dt.
            for (int delay=0; delay<=1; ++delay) {
                run(&live, f_006ECCD0, hz, 1, delay, mode, 0, 0);
                run(&old, predecessor, hz, 1, delay, mode, 0, 0);
                CHECK(memcmp(&live, &old, sizeof live) == 0);
                CHECK(first(&live) == 1u + (unsigned)ceilf(delay*hz/60.0f));
                for (unsigned i=0; i<live.count; ++i) {
                    float dt = (mode == 1 && i == 0 ? first(&live) : 1) / (float)hz;
                    CHECK(fabsf(seconds(live.events[i].dt)-dt) < 1e-6f);
                }
            }
        }
        // Mode 1 does not advance while its delivered time is zero.
        run(&live, f_006ECCD0, 120, .25f, 1, mode, 0, 1);
        CHECK(first(&live) == (mode ? 24u : 16u));
        // Variable mode-1 dt is accumulated by the translated Run, unchanged.
        run(&live, f_006ECCD0, 120, .25f, 1, mode, 1, 0);
        CHECK(first(&live) == 16);
        CHECK(fabsf(seconds(live.events[0].dt)-(mode ? 8.0f/60 : 4.0f/60)) < 1e-6f);
    }
}
int main(int argc, char **argv) {
    (void)argv;
    G = calloc(1, IMAGE_SIZE); assert(G);
    timing(argc > 1 ? predecessor : f_006ECCD0, argc > 1 ? "predecessor" : "production");
    if (argc == 1) { unchanged60(); controls(); }
    printf("%u checks, %u failures\n", checks, failures);
    free(G);
    return failures ? 1 : 0;
}
'''


def function(text, name):
    match = re.search(r'^void ' + name + r'\(Cpu \*(?:restrict )?c\) \{.*?\n}', text, re.M | re.S)
    assert match, name
    return match[0]


def main():
    path = ROOT / 'recomp/out/speed_41.c'
    if not path.exists():
        print('SKIP: needs generated recompiler output')
        return 3
    translated = path.read_text()
    guest = '\n'.join(function(translated, name) for name in ('f_006ECCD0_orig', 'f_006ECD90'))
    hook = function((ROOT / 'runtime/pacing.c').read_text(), 'f_006ECCD0')
    source = HARNESS.replace('TRANSLATED', guest).replace('LIVE_HOOK', hook).replace('PREDECESSOR', PREDECESSOR)
    with tempfile.TemporaryDirectory(prefix='taskdelay-') as tmp:
        src, binary = Path(tmp) / 'fixture.c', Path(tmp) / 'fixture'
        src.write_text(source)
        result = subprocess.run(['clang', '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                                 '-fsanitize=address,undefined', '-I', str(ROOT / 'recomp'),
                                 str(src), '-o', str(binary)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-3000:]
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        print(result.stdout, end='')
        if result.returncode:
            print(result.stderr[-3000:], file=sys.stderr)
            return 1
        broken = subprocess.run([str(binary), '--predecessor'], capture_output=True, text=True)
        print(broken.stdout, end='')
        assert broken.returncode == 1, broken.stderr[-3000:]
        assert 'first ticks: 8 24 80' in broken.stdout
        return 0


if __name__ == '__main__':
    sys.exit(main())
