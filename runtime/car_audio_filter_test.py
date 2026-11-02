#!/usr/bin/env python3
"""Dormant mono biquad proof; compiled PCM, no device or emulation.

Plan: execute hash-pinned current MW 00824F3A/00826710 against source
impulse/step goldens FIRST; then test bounds, cutoff changes and bypass/reset.
No test reads git history. --original runs only the two source golden checks.
--mutant one-pole/reset/bypass/bounds/rounded-reset runs compiled acceptance defects.
Measured: normal 15/15 pass; pinned MW original 0/2 pass (exit 1).
Before the numerator fix: near-bypass regression fails by 5.96e-8
(b0 0.5000080466 versus source 0.5000081062; tolerance 5.01e-8).
Fixed numerator-store goldens also reject the original rounded b0/b2.
Compiled one-pole/reset/bypass/bounds/rounded-reset controls fail 2/1/1/12/1 numeric checks
respectively (exit 1), without compiler errors or sanitizer failures.
First impulse/step sample: source .004730428569, MW .0453514755;
delta .04062104693. All fixtures use ASan/UBSan and disable FP contraction.
Whole suite: OFFLINE PASS (268 tests, 607 seconds, six missing-input skips);
host audio/shaders pass; whole-tree publication scan has zero findings.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / '.metal-cache/car-audio-filter'
FROZEN = {
    '00824F3A': '9c792a3dec99302f4b957412a8b218d0aeb191a83d74a7ec0dba6f95acb4425c',
    '00826710': '9e4ee253735ca3a0f7e1da48e5e1c2129e1c3016a60a22c1b9f47be9c704a879',
}


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


TAU = f32(2 * math.pi)
MIN_OMEGA = f32(0.003141593)
BYPASS_OMEGA = f32(3.1384511)
BIAS = f32(1e-18)
# Fixed 1000 Hz / 44100 Hz source response, rounded float32 stores. These
# independent expected samples must not change when the implementation does.
IMPULSE_GOLDEN = (0.004730428569, 0.01820503920, 0.03427912667, 0.04757346958,
                  0.05820506066, 0.06632576138, 0.07211481035, 0.07577180862)
STEP_GOLDEN = (0.004730428569, 0.02293546870, 0.05721459538, 0.10478806496,
               0.1629931182, 0.2293188721, 0.3014336824, 0.3772054911)


def coefficients(omega):
    """Decoded coefficient stores; independent Python arithmetic oracle."""
    cosine = f32(math.cos(omega))
    half_sine = 0.5 * math.sin(omega)
    reciprocal = 1 / (1 + half_sine)
    numerator = 1 - cosine
    b0 = f32(numerator / (2 * f32(1 + half_sine)))
    return (f32(-2 * cosine * reciprocal),
            f32((1 - f32(half_sine)) * reciprocal),
            b0, f32(f32(numerator) * reciprocal), b0)


def source_trace(operations):
    """Source recurrence/state transitions, separate from the compiled helper."""
    rate = 0
    omega = BYPASS_OMEGA
    coeff = (0,) * 5
    x1 = x2 = y1 = y2 = 0.0
    active = False
    result = []
    for op in operations:
        if op[0] == 'I':
            rate = f32(op[1])
            omega = f32(1e6 / rate * TAU)
            active = omega < BYPASS_OMEGA
            if active:
                omega = max(omega, MIN_OMEGA)
                coeff = coefficients(omega)
            else:
                coeff = (0,) * 5
            x1 = x2 = y1 = y2 = 0.0
            result.append([0])
        elif op[0] == 'C':
            angle = f32(op[1]) / rate * TAU
            new = f32(angle) if abs(angle) <= 3.4028234663852886e38 else math.copysign(math.inf, angle)
            if angle >= BYPASS_OMEGA:
                if omega < BYPASS_OMEGA:
                    x1 = x2 = y1 = y2 = 0.0
                active = False
            else:
                new = max(new, MIN_OMEGA)
                if new != omega:
                    coeff = coefficients(new)
                active = True
            omega = new
            result.append([0])
        elif op[0] == 'R':
            x1 = x2 = y1 = y2 = 0.0
            result.append([0])
        elif op[0] == 'D':
            result.append([int(active), omega, *coeff, x1, x2, y1, y2])
        elif op[0] == 'P':
            samples = []
            a1, a2, b0, b1, b2 = coeff
            for x in map(f32, op[2]):
                if active:
                    y = f32(b0*x + b1*x1 + b2*x2 + BIAS - a1*y1 - a2*y2)
                    x2, x1, y2, y1 = x1, x, y1, y
                else:
                    y = x
                samples.append(y)
            result.append([0, *samples])
        else:
            raise AssertionError(op)
    return result


COMMON = r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
'''

ORIGINAL = r'''
#include "runtime.h"
uint8_t *G;
void f_00824F3A(Cpu *);
void f_00826710(Cpu *);
static float rate;
static int active;
static void call(void (*fn)(Cpu *)) {
    Cpu c={0}; c.esp=0x1000; c.cw=0x37f;
    c.ebx=17; c.esi=19; c.edi=23; c.ebp=29;
    fn(&c);
    assert(c.esp==0x1004 && c.ebx==17 && c.esi==19 && c.edi==23 && c.ebp==29);
    assert(c.top==0);
}
static int initialize(float value) {
    if(!G) G=calloc(1,0x900000);
    assert(G); rate=value; active=0; MF32(0x201c)=0;
    MF32(0x8ae820)=0.00390625f; return 0;
}
static int configure(float cutoff) {
    active=cutoff<rate*0.5f;
    if(!active) { MF32(0x201c)=0; return 0; }
    /* Direct decoded coefficient body: no approximation of its PCM loop.
       The outer voice setter's integer quantization is outside this fixture. */
    M32(0x3000)=(uint32_t)(int32_t)cutoff;
    M32(0x3004)=(uint32_t)(int32_t)rate; M32(0x3008)=256;
    M32(0x1004)=0x2000; M32(0x1008)=0x3000;
    call(f_00824F3A); return 0;
}
static void reset(void) { MF32(0x201c)=0; }
static int process(const float *src,float *dst,size_t count) {
    if(!count) return 0;
    if(!active) { memcpy(dst,src,count*sizeof(float)); return 0; }
    memcpy(G+0x4000,src,count*sizeof(float));
    M32(0x1004)=0x201c; M32(0x1008)=(uint32_t)count;
    M32(0x100c)=0x4000; M32(0x1010)=0x8000;
    call(f_00826710); memcpy(dst,G+0x8000,count*sizeof(float)); return 0;
}
static void dump(void) { puts("[]"); }
'''

HELPER = r'''
#include "car_audio_filter.h"
static CarAudioFilter filter;
static int initialize(float rate) { return car_audio_filter_init(&filter,rate); }
static int configure(float cutoff) { return car_audio_filter_set_cutoff(&filter,cutoff); }
static void reset(void) { car_audio_filter_reset(&filter); }
static int process(const float *src,float *dst,size_t n) {
    return car_audio_filter_process(&filter,src,dst,n);
}
static void dump(void) {
    printf("[%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g]\n",
        filter.active,filter.omega,filter.a1,filter.a2,filter.b0,filter.b1,filter.b2,
        filter.x1,filter.x2,filter.y1,filter.y2);
}
static void validate(void) {
    CarAudioFilter saved=filter;
    float in[2]={1,2},out[2]={13,17};
    assert(car_audio_filter_init(NULL,44100)==-1);
    assert(car_audio_filter_set_cutoff(NULL,1000)==-1);
    assert(car_audio_filter_process(NULL,in,out,2)==-1);
    assert(car_audio_filter_process(&filter,NULL,out,2)==-1);
    assert(car_audio_filter_process(&filter,in,NULL,2)==-1);
    assert(car_audio_filter_process(&filter,NULL,NULL,0)==0);
    assert(out[0]==13 && out[1]==17);
    float bad[]={0,-1,NAN,INFINITY,-INFINITY};
    for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);i++) {
        assert(car_audio_filter_init(&filter,bad[i])==-1);
        assert(memcmp(&filter,&saved,sizeof(filter))==0);
    }
    for(unsigned i=2;i<sizeof(bad)/sizeof(*bad);i++) {
        assert(car_audio_filter_set_cutoff(&filter,bad[i])==-1);
        assert(memcmp(&filter,&saved,sizeof(filter))==0);
    }
    CarAudioFilter empty={0};
    assert(car_audio_filter_set_cutoff(&empty,1000)==-1);
    assert(car_audio_filter_process(&empty,in,out,2)==-1);
    car_audio_filter_reset(NULL);
    assert(memcmp(&filter,&saved,sizeof(filter))==0);
    CarAudioFilter separate;
    assert(car_audio_filter_init(&separate,48000)==0);
    float transparent[]={-0.0f,0.125f,-2.5f};
    float copied[3];
    assert(car_audio_filter_process(&separate,transparent,copied,3)==0);
    assert(memcmp(transparent,copied,sizeof(copied))==0);
    assert(car_audio_filter_set_cutoff(&separate,2000)==0);
    assert(car_audio_filter_process(&separate,transparent,copied,3)==0);
    assert(memcmp(&filter,&saved,sizeof(filter))==0);
    puts("[0]");
}
'''

MAIN = r'''
int main(void) {
    char op;
    while(scanf(" %c",&op)==1) {
        float value;
        if(op=='I' || op=='C') {
            assert(scanf("%f",&value)==1);
            printf("[%d]\n",op=='I'?initialize(value):configure(value));
        } else if(op=='R') { reset(); puts("[0]");
        } else if(op=='D') { dump();
#ifndef ORIGINAL
        } else if(op=='V') { validate();
#endif
        } else if(op=='P') {
            unsigned n, inplace;
            assert(scanf("%u%u",&n,&inplace)==2 && n<=4096);
            float *src=malloc((n?n:1)*sizeof(float));
            float *dst=malloc((n?n:1)*sizeof(float)); assert(src && dst);
            for(unsigned i=0;i<n;i++) assert(scanf("%f",src+i)==1);
            int rc=process(src,inplace?src:dst,n);
            printf("[%d",rc);
            for(unsigned i=0;i<n;i++) printf(",%.9g",(inplace?src:dst)[i]);
            puts("]"); free(src); free(dst);
        } else { return 2; }
    }
#ifdef ORIGINAL
    free(G);
#endif
    return 0;
}
'''


def compile_proof(folder, original=False, mutant=None):
    compiler = shutil.which('clang')
    if not compiler:
        raise unittest.SkipTest('clang required')
    harness = folder / 'harness.c'
    harness.write_text(COMMON + (ORIGINAL if original else HELPER) + MAIN)
    command = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-ffp-contract=off', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all', '-I', str(ROOT / 'runtime'),
               '-I', str(ROOT / 'recomp'), str(harness)]
    if original:
        generated_path = ROOT / 'recomp/out/speed_55.c'
        if not generated_path.exists():
            raise unittest.SkipTest('pinned generated MW filter bodies absent')
        generated = generated_path.read_text()
        bodies = []
        for name, digest in FROZEN.items():
            match = re.search(r'void f_' + name + r'\(Cpu \*restrict c\) \{.*?\n\}', generated, re.S)
            if not match or hashlib.sha256(match[0].encode()).hexdigest() != digest:
                raise AssertionError('pinned MW filter body changed: ' + name)
            bodies.append(match[0])
        frozen = folder / 'frozen.c'
        frozen.write_text('#include "runtime.h"\n' + '\n'.join(bodies))
        command += ['-DORIGINAL', str(frozen)]
    else:
        text = (ROOT / 'runtime/car_audio_filter.c').read_text()
        edits = {
            'reset': ('/* Retain history across active coefficient changes. */',
                      'car_audio_filter_reset(f);'),
            'bypass': ('if (f->omega < BYPASS_OMEGA) car_audio_filter_reset(f);', '(void)f;'),
            'bounds': ('if (omega < MIN_OMEGA) omega = MIN_OMEGA;', '(void)MIN_OMEGA;'),
            'rounded-reset': ('if (f->omega < BYPASS_OMEGA) car_audio_filter_reset(f);',
                              'if (f->active) car_audio_filter_reset(f);'),
        }
        if mutant == 'one-pole':
            pattern = r'double y = .*?;'
            text, count = re.subn(pattern, 'double y = (2.0 * f->cutoff_hz / f->sample_rate) * x + '
                                 '(1.0 - 2.0 * f->cutoff_hz / f->sample_rate) * f->y1;', text, flags=re.S)
            assert count == 1
        elif mutant:
            before, after = edits[mutant]
            assert text.count(before) == 1
            text = text.replace(before, after)
        source = folder / 'filter.c'
        source.write_text(text)
        command.append(str(source))
    binary = folder / 'proof'
    command += ['-o', str(binary)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise AssertionError(done.stderr[-4000:])
    return binary


def execute(binary, operations):
    lines = []
    for op in operations:
        if op[0] == 'P':
            lines.append(f'P {len(op[2])} {op[1]} ' + ' '.join(map(str, op[2])))
        else:
            lines.append(' '.join(map(str, op)))
    done = subprocess.run([str(binary)], input='\n'.join(lines)+'\n',
                          capture_output=True, text=True, timeout=10)
    if done.returncode:
        raise AssertionError(f'PCM harness exit {done.returncode}: {done.stderr[-2000:]}')
    return [json.loads(line) for line in done.stdout.splitlines()]


def rounded_boundary_ops():
    return [('I', 44100), ('C', 22027.94921875),
            ('P', 0, [0.25*(i-2) for i in range(8)]), ('D',),
            ('C', 1000000), ('D',), ('P', 0, [4]*3),
            ('C', 1000), ('D',), ('P', 0, [0]*16)]


class FilterProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='proof-', dir=SCRATCH)
        cls.folder = Path(cls.temp.name)
        cls.binary = compile_proof(cls.folder, CONFIG.original, CONFIG.mutant)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def compare(self, operations, binary=None):
        expected = source_trace(operations)
        actual = execute(binary or self.binary, operations)
        self.assertEqual(len(actual), len(expected))
        for step, (found, wanted) in enumerate(zip(actual, expected)):
            self.assertEqual(len(found), len(wanted), f'row {step}')
            for sample, (a, b) in enumerate(zip(found, wanted)):
                self.assertTrue(math.isfinite(a))
                self.assertAlmostEqual(a, b, delta=2e-6 + 2e-5*abs(b),
                                       msg=f'row {step} sample {sample}: {a} != {b}')
        return actual

    def test_impulse_source_golden(self):
        rows = self.compare([('I', 44100), ('C', 1000), ('P', 0, [1]+[0]*255)])
        for found, expected in zip(rows[-1][1:], IMPULSE_GOLDEN):
            self.assertAlmostEqual(found, expected, delta=1e-7)

    def test_step_source_golden(self):
        rows = self.compare([('I', 44100), ('C', 1000), ('P', 0, [1]*256)])
        for found, expected in zip(rows[-1][1:], STEP_GOLDEN):
            self.assertAlmostEqual(found, expected, delta=1e-7)

    def test_independent_analytic_response(self):
        # A transfer-function oracle without the helper's coefficient-store
        # structure anchors the decoded recurrence at ordinary cutoffs.
        for rate in (8000, 44100, 48000, 96000):
            cutoff = rate / 16
            omega = 2*math.pi*cutoff/rate
            d = 1 + math.sin(omega)/2
            b0 = (1-math.cos(omega))/(2*d)
            a1, a2 = -2*math.cos(omega)/d, (1-math.sin(omega)/2)/d
            x1 = x2 = y1 = y2 = 0.0
            samples = [math.sin(i*0.13)*0.7 for i in range(513)]
            expected = []
            for x in samples:
                y = b0*(x+2*x1+x2) - a1*y1 - a2*y2
                expected.append(y)
                x2, x1, y2, y1 = x1, x, y1, y
            actual = self.compare([('I', rate), ('C', cutoff), ('P', 0, samples)])[-1][1:]
            self.assertLess(max(abs(a-b) for a, b in zip(actual, expected)), 3e-6)

    def test_cutoff_changes_retain_history(self):
        ops = [('I', 44100), ('C', 1000), ('P', 0, [1]*17), ('D',),
               ('C', 4000), ('D',), ('P', 1, [0]*31), ('D',),
               ('C', 4000), ('D',), ('P', 0, [-0.5]*33)]
        rows = self.compare(ops)
        self.assertEqual(rows[3][-4:], rows[5][-4:])
        self.assertEqual(rows[7], rows[9])

    def test_near_bypass_coefficients_keep_full_numerator(self):
        # Rounding before b0/b2 loses one ULP here; see the before/after proof
        # in the module header. Keep the existing coefficient tolerance.
        rows = self.compare([('I', 48000), ('C', 15542.166015625), ('D',)])
        expected = source_trace([('I', 48000), ('C', 15542.166015625), ('D',)])[-1]
        for a, b in zip(rows[2][2:7], expected[2:7]):
            self.assertAlmostEqual(a, b, delta=1e-10+1e-7*abs(b))

    def test_numerator_store_goldens(self):
        # 008AC8B6 FST m32 retains ST(0); B9-BE divide that full numerator
        # by twice the stored denominator. C6 reloads m32 for b1. These fixed
        # float32 results distinguish both stores without using the oracle.
        for rate, hz, expected in (
                (8000, 3995.999755859375,
                 (0.9984290599822998, 1.9968581199645996, 0.9984290599822998)),
                (44100, 22027.94921875,
                 (0.9984291791915894, 1.9968583583831787, 0.9984291791915894))):
            with self.subTest(rate=rate):
                row = execute(self.binary, [('I', rate), ('C', hz), ('D',)])[-1]
                self.assertEqual(tuple(map(f32, row[4:7])), expected)

    def test_minimum_bound_and_coefficient_stores(self):
        for rate in (8000, 44100, 96000):
            cutoff = f32(MIN_OMEGA * rate / TAU)
            below = struct.unpack('<f', struct.pack('<I', struct.unpack('<I', struct.pack('<f', cutoff))[0]-1))[0]
            for hz in (-3.4028234663852886e38, -1000, 0, below, cutoff, cutoff*1.01):
                with self.subTest(rate=rate, cutoff=hz):
                    rows = self.compare([('I', rate), ('C', hz), ('D',),
                                         ('P', 0, [1]+[0]*127)])
                    expected = source_trace([('I', rate), ('C', hz), ('D',)])[-1]
                    self.assertGreaterEqual(rows[2][1], MIN_OMEGA)
                    for a, b in zip(rows[2][2:7], expected[2:7]):
                        self.assertAlmostEqual(a, b, delta=1e-10+1e-7*abs(b))

    def test_nyquist_and_upper_boundary(self):
        for rate in (8000, 44100, 48000, 96000):
            edge = f32(BYPASS_OMEGA * rate / TAU)
            bits = struct.unpack('<I', struct.pack('<f', edge))[0]
            for hz in (struct.unpack('<f', struct.pack('<I', bits-1))[0], edge,
                       struct.unpack('<f', struct.pack('<I', bits+1))[0], rate/2, rate):
                with self.subTest(rate=rate, cutoff=hz):
                    rows = self.compare([('I', rate), ('C', hz), ('D',),
                                         ('P', 0, [1]+[0]*63)])
                    self.assertEqual(rows[2][0], int(f32(hz)/f32(rate)*TAU < BYPASS_OMEGA))

    def test_default_bypass_and_reentry_reset(self):
        rows = self.compare([('I', 44100), ('D',), ('P', 1, [-1, 0, 0.25, 2]),
                             ('C', 1000), ('P', 0, [1]*19), ('C', 1000000),
                             ('D',), ('P', 0, [0.75]*8), ('C', 65535), ('D',),
                             ('C', 1000), ('P', 0, [1]+[0]*63)])
        self.assertEqual(rows[1][0], 0)
        self.assertEqual(rows[2], [0, -1, 0, 0.25, 2])
        self.assertEqual(rows[6][-4:], [0, 0, 0, 0])
        self.assertEqual(rows[9][-4:], [0, 0, 0, 0])

    def test_rounded_upper_bound_reset_predicate(self):
        # 008AC8DD tests the previous STORED angle before resetting, not the
        # previous branch taken. A just-below angle rounds up to the bound.
        rows = self.compare(rounded_boundary_ops())
        self.assertEqual(rows[3][0], 1)
        self.assertEqual(f32(rows[3][1]), BYPASS_OMEGA)
        self.assertEqual(rows[3][-4:], rows[5][-4:])
        self.assertEqual(rows[5][-4:], rows[8][-4:])

    def test_explicit_reset_and_reinitialize(self):
        rows = self.compare([('I', 44100), ('C', 1000), ('P', 0, [1]*23),
                             ('D',), ('R',), ('D',), ('P', 0, [1]+[0]*15),
                             ('I', 48000), ('D',), ('C', 2000), ('P', 0, [1]+[0]*63)])
        self.assertEqual(rows[3][:7], rows[5][:7])
        self.assertEqual(rows[5][-4:], [0, 0, 0, 0])
        self.assertEqual(rows[8][-4:], [0, 0, 0, 0])

    def test_block_splits_and_inplace(self):
        samples = [f32(math.sin(i*0.37)*1.5) for i in range(513)]
        whole = self.compare([('I', 48000), ('C', 3300), ('P', 0, samples), ('D',)])
        for inplace in (0, 1):
            ops = [('I', 48000), ('C', 3300)]
            start = 0
            for count in (0, 1, 2, 7, 31, 128, 0, 344):
                ops.append(('P', inplace, samples[start:start+count]))
                start += count
            self.assertEqual(start, len(samples))
            ops.append(('D',))
            rows = self.compare(ops)
            self.assertEqual([x for row in rows[2:-1] for x in row[1:]], whole[2][1:])
            self.assertEqual(rows[-1], whole[-1])

    def test_bias_and_stability(self):
        for hz in (0, 1000, 22027):
            rows = self.compare([('I', 44100), ('C', hz), ('P', 0, [0]*4096)])
            self.assertAlmostEqual(rows[2][1], BIAS, delta=1e-25)
            self.assertLess(max(abs(x) for x in rows[2][1:]), 1e-10)

    def test_invalid_arguments_and_separate_voices(self):
        rows = execute(self.binary, [('I', 44100), ('C', 1000), ('P', 0, [1]*8), ('V',)])
        self.assertEqual(rows[-1], [0])

    def test_original_and_mutation_controls(self):
        if CONFIG.mutant:
            self.skipTest('controls run by the unmodified proof only')
        golden = [('I', 44100), ('C', 1000), ('P', 0, [1]+[0]*63)]
        cases = {
            'one-pole': golden,
            'reset': [('I', 44100), ('C', 1000), ('P', 0, [1]*17),
                      ('C', 4000), ('P', 0, [0]*31)],
            'bypass': [('I', 44100), ('C', 1000), ('P', 0, [1]*17),
                       ('C', 1000000), ('C', 1000), ('P', 0, [0]*31)],
            'bounds': [('I', 44100), ('C', 0), ('D',), ('P', 0, [1]*31)],
            'rounded-reset': rounded_boundary_ops(),
        }
        for name, ops in [('original', golden), *cases.items()]:
            folder = self.folder / name
            folder.mkdir()
            binary = compile_proof(folder, original=name == 'original',
                                   mutant=None if name == 'original' else name)
            with self.assertRaises(AssertionError, msg=name+' must fail source acceptance'):
                self.compare(ops, binary)
            print(name+': source acceptance rejected (compiled numeric mismatch)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--mutant', choices=['one-pole', 'reset', 'bypass', 'bounds', 'rounded-reset'])
    CONFIG = parser.parse_args()
    if CONFIG.original:
        suite = unittest.TestSuite(FilterProof(name) for name in
                                   ('test_impulse_source_golden', 'test_step_source_golden'))
    elif CONFIG.mutant:
        selected = {
            'one-pole': ('test_impulse_source_golden', 'test_step_source_golden'),
            'reset': ('test_cutoff_changes_retain_history',),
            'bypass': ('test_default_bypass_and_reentry_reset',),
            'bounds': ('test_minimum_bound_and_coefficient_stores',),
            'rounded-reset': ('test_rounded_upper_bound_reset_predicate',),
        }
        suite = unittest.TestSuite(FilterProof(name) for name in selected[CONFIG.mutant])
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(FilterProof)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 3 if result.wasSuccessful() else 1)
