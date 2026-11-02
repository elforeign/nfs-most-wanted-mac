#!/usr/bin/env python3
"""PLAYERSTATE lifecycle proof; no game, audio device or emulation.

Base: 13984ab7; dependency checkout: 900c7a0b66e56fc49262b3fc003fbd917b3f098f.
Plan: compare full callback/state/counter traces to independent source goldens;
execute the hash-pinned recompiled MW updater/reset on source-shaped fixtures;
break translation and stopped reset in compiled adapter copies.

Creation/option arguments are preserved as raw source units, NOT certified as
MW bank defaults. No concrete MW mixer backend is supplied: mode, initialization,
unclamped option units/defaults and resume restoration need target equivalence
proof before integration/admission. This module proves the lifecycle boundary.
The adapter covers ordinal-4 player release only, not the instance/class walk.

Standalone: normal, --original, --mutant translation, --mutant reset.
Measured: 13/13 normal checks; --original 20 failing subchecks, translation
mutant 17, reset mutant 20 (all controls exit 1). The original sample-1 trace
has one invalid dispatch and zero creates; a live source-shaped object with
type FF retains one voice/allocation across native pause/stop/player cleanup.
Private proof: 69 banks, 400 players, 26,164 snapshots, 4,489 creates/releases,
zero retained voices/allocations; three/five/six option layouts all exercised.
All executable fixtures run with ASan/UBSan. No dynamic main read.
Whole suite: OFFLINE PASS (215 tests, 524 seconds, five missing-input skips);
host audio/shaders and publication passed, zero publication findings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / '.metal-cache/car-audio-player'
BASELINE = '13984ab7'
DEPENDENCY = '900c7a0b66e56fc49262b3fc003fbd917b3f098f'
# Generated functions are executed unchanged. Hashes freeze their full bodies,
# including all selector/command branches; a changing checkout cannot bless them.
FROZEN_UPDATE_SHA = '510adb0312e76b4ba5dcc94b61239355dcd3a6b77c1ac299804c406e8f774faf'
FROZEN_RESET_SHA = 'd55b532cf9ec4022b087852fb652b26aa9f3ae22337282dc9d1e68480be77deb'

HARNESS = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "car_audio_player.h"
#ifdef ORIGINAL
#include "runtime.h"
uint8_t *G;
void f_0081A52C(Cpu *);
#endif
static CarAudioPlayer player;
static uint8_t *state, *rows;
static unsigned creates, releases, voices, allocations, invalid_dispatch;
static unsigned allocated_options;
static int fail_create, poll_status = 1;
static void *allocation;
static int32_t active_voice;
static uint32_t get(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static void put(uint8_t *p, uint32_t n) { for(unsigned i=0;i<4;i++) p[i]=(uint8_t)(n>>(8*i)); }
static void event(const char *name, int32_t voice) {
    printf("{\"event\":\"%s\",\"voice\":%"PRId32"}\n",name,voice);
}
static int create(void *context, const CarAudioPlayerCreate *r, int32_t *voice) {
    (void)context;
    ++creates;
    printf("{\"event\":\"create\",\"sample\":%d,\"mode\":%u,\"bank\":%"PRIu64
           ",\"offset\":%"PRIu32",\"initialization\":[",r->sample,r->mode,r->bank_identity,r->packed_offset);
    for(unsigned i=0;i<6;i++) printf("%s%"PRIu32,i?",":"",r->initialization[i]);
    printf("],\"options\":[");
    for(unsigned i=0;i<r->option_count;i++) {
        const uint8_t *o=r->options+i*12;
        printf("%s[%u,%"PRId32"]",i?",":"",o[0],(int32_t)get(o+8));
    }
    printf("]}\n");
    if(fail_create) return -9;
    assert(!voices && !allocation);
    allocation=malloc(1); assert(allocation);
    ++voices; ++allocations;
    *voice=active_voice; return 0;
}
static void release(void *context, int32_t voice) {
    (void)context; assert(voices==1 && voice==active_voice && allocation);
    event("release",voice); free(allocation); allocation=NULL;
    --voices; --allocations; ++releases;
}
static void pause_voice(void *context, int32_t voice) {
    (void)context; assert(voices==1 && voice==active_voice); event("pause",voice);
}
static void resume_voice(void *context, int32_t voice) {
    (void)context; assert(voices==1 && voice==active_voice); event("resume",voice);
}
static void option(void *context, int32_t voice, uint8_t code, int32_t value) {
    (void)context; assert(voices==1 && voice==active_voice);
    printf("{\"event\":\"option\",\"voice\":%"PRId32",\"code\":%u,\"value\":%"PRId32"}\n",voice,code,value);
}
static int status(void *context, int32_t voice) {
    (void)context; assert(voices==1 && voice==active_voice); event("status",voice); return poll_status;
}
static const CarAudioPlayerBackend backend={create,release,pause_voice,resume_voice,option,status};
#ifdef ORIGINAL
/* The unchanged generated updater calls this dispatch vector. An invalid/null
 * dispatch is recorded and returns failure instead of crashing the host. No
 * failed dispatch is treated as a successful source voice creation.
 */
void dispatch(Cpu *c, uint32_t target) {
    uint32_t p=M32(c->esp+4);
    if(target==0x81a330) {
        uint32_t r=M32(c->esp+8);
        CarAudioPlayerCreate request={.bank_identity=77,.sample=(int16_t)(M32(r+4)-1),
            .options=G+p+28,.option_count=M8(p+14)};
        int32_t voice=-1;
        int result=create(NULL,&request,&voice);
        c->eax=result<0?UINT32_MAX:(uint32_t)voice;
        if(result>=0) for(unsigned i=0;i<M8(p+14);i++) {
            uint8_t *o=G+p+28+i*12; option(NULL,voice,o[0],(int32_t)get(o+8)); put(o+4,get(o+8));
        }
    } else if(target==0x81a394) { release(NULL,(int32_t)M32(p+8));
    } else if(target==0x81a3a2) { pause_voice(NULL,(int32_t)p);
    } else if(target==0x81a3bc) { resume_voice(NULL,(int32_t)M32(p+8));
    } else if(target==0x81a451) {
        c->eax=(uint32_t)status(NULL,(int32_t)M32(p+8));
    } else { ++invalid_dispatch; c->eax=UINT32_MAX; event("invalid_dispatch",(int32_t)target); }
    c->esp+=4;
}
static int original_update(void) {
    Cpu c={0}; c.esp=0x30000; c.esi=123; c.ebx=456;
    M32(c.esp)=0xffff; M32(c.esp+4)=0x10000;
    f_0081A52C(&c);
    assert(c.esp==0x30004 && c.esi==123 && c.ebx==456);
    return (int32_t)c.eax;
}
#endif
static void snapshot(int result) {
    printf("{\"result\":%d,\"error\":%d,\"current\":%u,\"previous\":%u,\"type\":%u,"
           "\"handle\":%"PRIu32",\"voices\":%u,\"allocations\":%u,\"creates\":%u,"
           "\"releases\":%u,\"invalid\":%u,\"options\":[",result,player.last_error,
           state[12],state[13],state[16],get(state+8),voices,allocations,creates,releases,invalid_dispatch);
    for(unsigned i=0;i<allocated_options;i++) printf("%s%"PRIu32,i?",":"",get(state+28+i*12+4));
    printf("]}\n");
}
int main(int argc, char **argv) {
    (void)argc; (void)argv; (void)backend;
    unsigned count,noptions;
    if(scanf("%u %u %"SCNd32,&count,&noptions,&active_voice)!=3 || count>256 || noptions>255) return 2;
    allocated_options=noptions;
    size_t state_size=28+noptions*12, table_size=4+count*12;
#ifdef ORIGINAL
    G=calloc(1,0x1000000); assert(G); state=G+0x10000; rows=G+0x20000;
    M32(0x90bbf0)=0x81a330; M32(0x90bc00)=0x81a394;
    M32(0x90bc10)=0x81a3a2; M32(0x90bc20)=0x81a3bc; M32(0x90bc30)=0x81a451;
#else
    state=calloc(1,state_size); rows=calloc(1,table_size); assert(state && rows);
#endif
    state[14]=(uint8_t)noptions; state[16]=255; put(state,0xaabbccdd); put(state+4,0x20000);
    put(rows,count);
    for(unsigned i=0;i<count;i++) for(unsigned j=0;j<3;j++) {
        uint32_t v; if(scanf("%"SCNu32,&v)!=1) return 3; put(rows+4+i*12+j*4,v);
    }
    for(unsigned i=0;i<noptions;i++) {
        unsigned id; uint32_t old,new;
        if(scanf("%u %"SCNu32" %"SCNu32,&id,&old,&new)!=3) return 4;
        state[28+i*12]=(uint8_t)id; put(state+32+i*12,old); put(state+36+i*12,new);
    }
    char op;
    while(scanf(" %c",&op)==1) {
        int result=0;
        if(op=='A') {
#ifndef ORIGINAL
            result=car_audio_player_attach(&player,state,state_size,rows,table_size,77,&backend,NULL);
#endif
        } else if(op=='U') {
            int32_t command,selected;
            if(scanf("%"SCNd32" %"SCNd32" %d %d",&command,&selected,&poll_status,&fail_create)!=4) return 5;
            put(state+24,(uint32_t)command); put(state+20,(uint32_t)selected);
#ifdef ORIGINAL
            result=original_update();
#else
            result=car_audio_player_update(&player);
#endif
        } else if(op=='D' || op=='X') {
#ifdef ORIGINAL
            /* MW's player-only destroy branch uses signed +10, not +08.
             * Full instance unlink/class/free is outside this fixture.
             */
            if((int8_t)state[16]>=0 && voices) release(NULL,(int32_t)get(state+8));
            state[16]=255; put(state+8,0); state[12]=state[13]=0;
#else
            if(op=='X') car_audio_player_destroy(&player,1); else car_audio_player_detach(&player);
#endif
        } else if(op=='W') {
            unsigned offset; uint32_t value;
            if(scanf("%u %"SCNu32,&offset,&value)!=2 || offset>=state_size) return 6;
            state[offset]=(uint8_t)value;
        } else if(op=='O') {
            unsigned index; uint32_t value;
            if(scanf("%u %"SCNu32,&index,&value)!=2 || index>=noptions) return 7;
            put(state+36+index*12,value);
        } else if(op=='B') {
            unsigned offset; uint32_t value;
            if(scanf("%u %"SCNu32,&offset,&value)!=2 || offset>=table_size) return 8;
            rows[offset]=(uint8_t)value;
        } else if(op=='L') {
            unsigned n; if(scanf("%u",&n)!=1) return 9; state_size=n;
#ifdef ORIGINAL
        } else if(op=='S') {
            /* A live source object pointer with unused dispatch byte FF.
             * Its host lease is deliberately distinct from that guest word.
             */
            assert(!allocation && !voices);
            allocation=malloc(1); assert(allocation); ++creates; ++voices; ++allocations;
            put(state+8,0x40000); state[12]=1; state[16]=255;
#endif
        } else return 10;
        snapshot(result);
    }
#ifndef ORIGINAL
    car_audio_player_detach(&player); free(state); free(rows);
#else
    if(voices) release(NULL,active_voice); free(G);
#endif
    assert(!voices && !allocations && !allocation);
    return 0;
}
'''


def row(sample, mode=60):
    return (65535, 0, 0) if sample is None else (sample | mode << 16, sample + 1, 0xffffffff)


def update(command, index=0, status=1, failure=0):
    """Format a harness tick, with the ordinary live/success defaults."""
    return f'U {command} {index} {status} {failure}'


class SourceExpectation:
    """Independent semantic oracle from source 00898E58/00898BFD/008984B9.

    Owns a logical object, never interprets wire handle/type. Source object
    addresses are normalized to the backend voice only at snapshot comparison.
    """
    def __init__(self, samples, opts, voice):
        self.samples = samples
        self.opts = [list(o) for o in opts]
        self.voice = voice
        self.alive = False
        self.current = self.previous = self.creates = self.releases = self.error = 0
        self.trace = []

    def emit(self, name, **args):
        self.trace.append(dict(event=name, **args))

    def release(self):
        if self.alive:
            self.emit('release', voice=self.voice)
            self.releases += 1
            self.alive = False

    def options(self, all_options=False):
        for o in self.opts:
            code, old, value = o
            if all_options or old != value:
                self.emit('option', voice=self.voice, code=code,
                          value=value if value < 2**31 else value - 2**32)
                o[1] = value

    def run(self, operations):
        for op in operations:
            words = op.split()
            action = words[0]
            result = 0
            if action == 'U':
                command, index, status, failure = map(int, words[1:])
                self.error = 0
                command = max(0, min(2, command))
                if command != self.current:
                    if command == 0:
                        self.release()
                    elif command == 2:
                        if self.alive:
                            self.emit('pause', voice=self.voice)
                    elif self.alive:
                        self.emit('resume', voice=self.voice)
                    elif (self.current, self.previous) != (2, 1) and self.samples:
                        sample, mode = self.samples[max(0, min(len(self.samples)-1, index))]
                        if sample is not None:
                            self.creates += 1
                            # Explicit factory goldens for the accepted row shape:
                            # byte3=0; byte4=slot low; byte5=slot high;
                            # byte6=byte7=0; byte8=FF. Mode is an opaque byte.
                            slot = sample + 1
                            self.emit('create', sample=sample, mode=mode, bank=77,
                                      offset=4294967295,
                                      initialization=[0, (slot % 256)*256, (slot // 256)*256, 0, 0, 65280],
                                      options=[[o[0], o[2] if o[2] < 2**31 else o[2]-2**32] for o in self.opts])
                            self.alive = not failure
                            if failure:
                                self.error = -3
                            else:
                                self.options(True)
                    self.previous, self.current = self.current, command
                if command == 1 and self.alive:
                    self.options()
                    self.emit('status', voice=self.voice)
                    if status <= 0:
                        self.error = -3 if status < 0 else 0
                        self.release()
                    else:
                        result = 1
                elif self.alive:
                    result = command
            elif action in ('D', 'X'):
                self.release()
                self.current = self.previous = self.error = 0
            elif action == 'O':
                self.opts[int(words[1])][2] = int(words[2])
            elif action != 'A':
                raise AssertionError('unsupported oracle action ' + action)
            self.trace.append(dict(result=result, error=self.error, current=self.current,
                                   previous=self.previous, type=0 if self.alive else 255,
                                   handle=self.voice if self.alive else 0,
                                   voices=int(self.alive), allocations=int(self.alive),
                                   creates=self.creates, releases=self.releases, invalid=0,
                                   options=[o[1] for o in self.opts]))
        return self.trace


def compile_proof(folder, original=False, mutant=None):
    compiler = shutil.which('clang')
    if not compiler:
        raise unittest.SkipTest('clang required for executable lifecycle proof')
    source = folder / 'player.c'
    text = (ROOT / 'runtime/car_audio_player.c').read_text()
    if mutant == 'translation':
        old = '.sample = (int16_t)sample, .mode = row[2],'
        assert text.count(old) == 1
        text = text.replace(old, '.sample = (int8_t)row[0], .mode = 0,')
    elif mutant == 'reset':
        old = 'p->state[16] = 255;'
        assert text.count(old) == 1
        text = text.replace(old, 'p->state[16] = 0;')
    source.write_text(text)
    harness = folder / 'harness.c'
    harness.write_text(HARNESS)
    command = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
               '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'recomp'), str(harness), str(source)]
    if original:
        path = ROOT / 'recomp/out/speed_55.c'
        if not path.is_file():
            raise unittest.SkipTest('pinned generated MW functions absent')
        generated = path.read_text()
        functions = []
        for name, digest in [('0081A42F', FROZEN_RESET_SHA), ('0081A52C', FROZEN_UPDATE_SHA)]:
            match = re.search(r'void f_' + name + r'\(Cpu \*restrict c\) \{.*?\n\}', generated, re.S)
            if not match or hashlib.sha256(match[0].encode()).hexdigest() != digest:
                raise AssertionError('frozen original function mismatch: ' + name)
            functions.append(match[0])
        frozen = folder / 'frozen.c'
        frozen.write_text('#include "runtime.h"\nvoid dispatch(Cpu *, uint32_t);\n' + '\n'.join(functions))
        command += ['-DORIGINAL', '-Wno-parentheses-equality', str(frozen)]
    binary = folder / 'player'
    command += ['-o', str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr[-5000:])
    return binary


def execute(binary, samples, operations, opts=(), voice=0):
    lines = [f'{len(samples)} {len(opts)} {voice}']
    lines += [' '.join(map(str, row(sample, mode))) for sample, mode in samples]
    lines += [' '.join(map(str, o)) for o in opts]
    lines += operations
    result = subprocess.run([str(binary)], input='\n'.join(lines)+'\n',
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise AssertionError(f'execution exit {result.returncode}: {result.stderr[-3000:]}')
    return [json.loads(line) for line in result.stdout.splitlines()]


class PlayerProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='proof-', dir=SCRATCH)
        cls.folder = Path(cls.temp.name)
        cls.binary = compile_proof(cls.folder, CONFIG.original, CONFIG.mutant)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def compare(self, samples, operations, opts=(), voice=0):
        expected = SourceExpectation(samples, opts, voice).run(operations)
        actual = execute(self.binary, samples, operations, opts, voice)
        # A complete ordered comparison, with bounded diagnostics even for the
        # repeated-lifecycle control (large list diffs can dominate test time).
        for step, (found, wanted) in enumerate(zip(actual, expected)):
            self.assertEqual(found, wanted, f'trace item {step}; lengths {len(actual)}/{len(expected)}')
        self.assertEqual(len(actual), len(expected))
        self.assertEqual((actual[-1]['voices'], actual[-1]['allocations']), (0, 0))
        return actual

    def test_source_sample_one_complete_trace(self):
        self.compare([(1, 80)], ['A', 'U 1 0 1 0', 'U 1 0 1 0', 'U 2 0 1 0',
                                'U 1 0 1 0', 'U 0 0 1 0', 'X'])

    def test_index_and_command_clamps(self):
        for index in [-2147483648, -1, 0, 1, 2, 3, 2147483647]:
            with self.subTest(index=index):
                self.compare([(0, 60), (255, 100), (256, 80)],
                             ['A', f'U 1 {index} 1 0', f'U 9 {index} 1 0',
                              f'U 1 {index} 1 0', f'U -2147483648 {index} 1 0', 'D'], voice=19)

    def test_empty_rows_and_empty_table(self):
        for samples in [[], [(None, 60)], [(None, 60), (1, 100)]]:
            with self.subTest(samples=samples):
                self.compare(samples, ['A', 'U 1 -1 1 0', 'U 2 0 1 0',
                                       'U 1 0 1 0', 'U 0 0 1 0', 'D'])

    def test_stopped_reset_and_zero_voice(self):
        self.compare([(0, 60)], ['A', 'U 1 0 1 0', 'U 0 0 1 0',
                                'U 1 0 1 0', 'U 0 0 1 0', 'X', 'X'])

    def test_create_failure_and_no_false_retry(self):
        self.compare([(2, 100)], ['A', 'U 1 0 1 1', 'U 1 0 1 0', 'U 2 0 1 0',
                                 'U 1 0 1 0', 'U 0 0 1 0', 'U 1 0 1 0', 'D'])

    def test_end_and_status_error(self):
        for status in [0, -7]:
            with self.subTest(status=status):
                self.compare([(0, 80)], ['A', 'U 1 0 1 0', f'U 1 0 {status} 0',
                                        'U 1 0 1 0', 'U 2 0 1 0', 'U 1 0 1 0',
                                        'U 0 0 1 0', 'U 1 0 1 0', 'X'], voice=73)

    def test_exact_option_units_and_paused_updates(self):
        opts = [(0, 123, 0), (2, 0, 32768), (3, 0, 0xffffffff),
                (5, 0, 2147483647), (6, 0, 65536), (8, 0, 0x80000000)]
        ops = ['A', update(1), update(1), update(2), 'O 0 4294967295',
               'O 1 4096', update(2), update(1), 'O 2 65535', update(1), 'X']
        self.compare([(7, 100)], ops, opts, voice=0)

    def test_three_five_six_option_wire_layouts(self):
        for codes in [(0, 2, 3), (0, 2, 3, 5, 6), (0, 2, 3, 5, 6, 8)]:
            with self.subTest(option_count=len(codes)):
                opts = [(code, 0, (i+1)*4096) for i, code in enumerate(codes)]
                self.compare([(128, 60)], ['A', 'U 1 0 1 0', 'U 2 0 1 0',
                                          'U 1 0 1 0', 'X'], opts, voice=127)

    def test_repeated_attach_detach_destroy(self):
        ops = []
        for i in range(40):
            ops += ['A', f'U 1 {i%3} 1 0', 'U 2 0 1 0', 'X' if i%2 else 'D', 'D']
        trace = self.compare([(1, 60), (2, 80), (3, 100)], ops, voice=0)
        self.assertEqual((trace[-1]['creates'], trace[-1]['releases']), (40, 40))

    def test_reject_profiles_and_damaged_ownership(self):
        if CONFIG.original:
            self.skipTest('new boundary validation is not part of original native update')
        # New output profiles and IDs must fail before creating any resource.
        for op in ['W 15 1', 'W 17 1', 'W 28 7', 'L 27', 'B 0 255',
                   'B 6 42', 'B 8 99', 'W 8 42', 'W 16 0', 'W 12 1']:
            with self.subTest(op=op):
                trace = execute(self.binary, [(0, 60)], [op, 'A', 'D'], [(0, 0, 4096)])
                self.assertLess(trace[-2]['result'], 0)
                self.assertEqual(trace[-1]['creates'], 0)
        for op in ['W 8 42', 'W 16 255', 'W 15 1', 'W 14 2', 'B 0 255']:
            with self.subTest(damage=op):
                trace = execute(self.binary, [(0, 60)], ['A', 'U 1 0 1 0', op, 'U 0 0 1 0', 'X'],
                                [(0, 0, 4096)], voice=31)
                self.assertLess(trace[-3]['result'], 0)
                self.assertEqual((trace[-1]['voices'], trace[-1]['allocations'], trace[-1]['releases']), (0, 0, 1))

    def test_attach_twice_keeps_voice(self):
        if CONFIG.original:
            self.skipTest('sidecar attachment is a new boundary operation')
        trace = execute(self.binary, [(0, 60)], ['A', 'U 1 0 1 0', 'A', 'X'])
        self.assertEqual(trace[-3]['result'], -1)
        self.assertEqual(trace[-3]['voices'], 1)
        self.assertEqual((trace[-1]['voices'], trace[-1]['allocations'], trace[-1]['releases']), (0, 0, 1))

    def test_original_misbinding_is_observed(self):
        if CONFIG.original or CONFIG.mutant:
            self.skipTest('normal run separately executes the frozen native baseline')
        folder = self.folder / 'original'
        folder.mkdir()
        original = compile_proof(folder, original=True)
        trace = execute(original, [(1, 80)], ['A', 'U 1 0 1 0', 'U 2 0 1 0', 'U 1 0 1 0', 'X'])
        snapshots = [v for v in trace if 'result' in v]
        self.assertEqual([v['result'] for v in snapshots], [0, 0, 0, 0, 0])
        self.assertEqual((snapshots[-1]['invalid'], snapshots[-1]['creates']), (1, 0))
        self.assertEqual([v['current'] for v in snapshots], [0, 1, 2, 1, 0])
        self.assertEqual((snapshots[-1]['voices'], snapshots[-1]['allocations']), (0, 0))
        live = execute(original, [(0, 60)], ['S', 'U 2 0 1 0', 'U 0 0 1 0', 'X'])
        live_states = [v for v in live if 'result' in v]
        # Native pause, stop and player cleanup all skip the source-owned object
        # because its unused type byte is FF. The harness frees its seed only
        # AFTER recording this retained-voice/destruction failure.
        self.assertEqual([v['result'] for v in live_states], [0, 0, 0, 0])
        self.assertEqual((live_states[-1]['voices'], live_states[-1]['allocations'],
                          live_states[-1]['releases']), (1, 1, 0))

    def test_designated_mutants_fail(self):
        if CONFIG.original or CONFIG.mutant:
            self.skipTest('normal run checks both deliberately broken implementations')
        for mutant, samples, ops in [
            ('translation', [(256, 100)], ['A', 'U 1 0 1 0', 'X']),
            ('reset', [(0, 60)], ['A', 'U 1 0 1 0', 'U 0 0 1 0', 'U 1 0 1 0', 'X'])]:
            with self.subTest(mutant=mutant):
                folder = self.folder / mutant
                folder.mkdir()
                broken = compile_proof(folder, mutant=mutant)
                actual = execute(broken, samples, ops)
                expected = SourceExpectation(samples, (), 0).run(ops)
                self.assertNotEqual(actual, expected)
                if mutant == 'translation':
                    self.assertNotEqual(next(v for v in actual if v.get('event') == 'create')['sample'], 256)
                else:
                    self.assertEqual([v for v in actual if 'result' in v][-2]['result'], -1)


def private_proof():
    """Exercise every measured initial player and every authored selector row.

    Private inputs are optional and are never copied into the checked-in test.
    The source/target layout census comes from A4, while the expected transition
    traces are specified by SourceExpectation, not by its classification labels.
    """
    source = Path(os.environ.get('NFS_CARBON_PC', Path.home() / 'Downloads/NFSC'))
    roster = ROOT / 'tools_snapshot/private_cars.py'
    if not (source / 'GLOBAL/attributes.bin').is_file() or not roster.is_file():
        print('SKIP private source inputs absent; synthetic lifecycle proof completed')
        return False
    sys.path.insert(0, str(ROOT / 'tools'))
    from setup_kit import car_audio, car_audio_abi
    # Independently pin the factory chain and scalar constants used by the
    # source consumer. This establishes units, not equivalence of two mixers.
    executable = source / 'NFSC.exe'
    if executable.is_file():
        data = executable.read_bytes()
        assert hashlib.sha256(data).hexdigest() == 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
        pe = struct.unpack_from('<I', data, 60)[0]
        base = struct.unpack_from('<I', data, pe+52)[0]
        table = pe + 24 + struct.unpack_from('<H', data, pe+20)[0]
        sections = [struct.unpack_from('<4I', data, table+i*40+8)
                    for i in range(struct.unpack_from('<H', data, pe+6)[0])]
        def read(address, size):
            for _, rva, extent, offset in sections:
                if rva <= address-base and address-base+size <= rva+extent:
                    return data[offset+address-base-rva:offset+address-base-rva+size]
            raise AssertionError('source consumer evidence outside executable sections')
        assert struct.unpack('<I', read(0xa7d6b8, 4))[0] == 0xa7d760
        assert struct.unpack('<I', read(0xa7d760, 4))[0] == 0xa01dc8
        assert struct.unpack('<I', read(0xa01dc8, 4))[0] == 0x89db7c
        assert struct.unpack('<6I', read(0xa03ac0, 24)) == (0x89db31, 0x89daa3, 0x89dae3, 0x89d8dd, 0x89d98c, 0x89d9e8)
        for address, golden in [(0x9cbed0, 1/4096), (0x9c8804, 1/32767),
                                (0x9c75f8, 360/65536), (0x9c2938, 0.01)]:
            assert read(address, 4) == struct.pack('<f', golden)
        print('source consumer: factory/vtable chain and 4/4 float32 unit constants pinned')
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    resources = {p.relative_to(source).as_posix(): p.read_bytes()
                 for p in (source / 'SOUND/ENGINE').rglob('*')
                 if p.suffix.lower() in ('.abk', '.gin')}
    graphs = car_audio.inventory((source / 'GLOBAL/attributes.bin').read_bytes(), cars, resources)
    assert not graphs['unsupported']
    manifest = car_audio_abi.closure_manifest(graphs['supported'].values(), resources)
    counts = dict(banks=0, players=0, rows=0, snapshots=0, creates=0, releases=0,
                  retained_voices=0, retained_allocations=0)
    with tempfile.TemporaryDirectory(prefix='private-', dir=SCRATCH) as directory:
        binary = compile_proof(Path(directory))
        for bank in manifest['banks'].values():
            counts['banks'] += 1
            tables = {t['offset']: t for t in bank['selectors']}
            for module in bank['modules']:
                for state in module['players']:
                    table = tables[state['selector_offset']]
                    samples = [(i, r[0] >> 16) for i, r in zip(table['indices'], table['source_rows'])]
                    assert all(tuple(r) == row(i, mode) for r, (i, mode) in zip(table['source_rows'], samples))
                    opts = [(o['code'], o['previous'] & 0xffffffff, o['value'] & 0xffffffff)
                            for o in state['option_records']]
                    assert state['size'] == 28 + 12 * len(opts)
                    assert not state['initial_handle'] and state['source_type_byte'] == 255
                    ops = ['A']
                    for index in [-1, *range(len(samples)), len(samples)]:
                        ops += [f'U 1 {index} 1 0', f'U 2 {index} 1 0',
                                f'U 1 {index} 1 0', 'U 0 0 1 0']
                    ops += ['X', 'A', 'U 1 0 1 0', 'X']
                    actual = execute(binary, samples, ops, opts)
                    expected = SourceExpectation(samples, opts, 0).run(ops)
                    assert actual == expected, 'private source lifecycle trace mismatch'
                    final = actual[-1]
                    counts['players'] += 1
                    counts['rows'] += len(samples)
                    counts['snapshots'] += len(ops)
                    for name in ['creates', 'releases']:
                        counts[name] += final[name]
                    counts['retained_voices'] += final['voices']
                    counts['retained_allocations'] += final['allocations']
    assert counts['creates'] == counts['releases']
    assert counts['retained_voices'] == counts['retained_allocations'] == 0
    (SCRATCH / 'private-counts.json').write_text(json.dumps(counts, indent=2)+'\n')
    print('private player proof: ' + json.dumps(counts, sort_keys=True))
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--mutant', choices=('translation', 'reset'))
    parser.add_argument('--private', action='store_true')
    CONFIG = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PlayerProof))
    if not result.wasSuccessful():
        sys.exit(1)
    if CONFIG.private:
        sys.exit(0 if private_proof() else 3)
