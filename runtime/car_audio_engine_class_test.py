#!/usr/bin/env python3
"""Dormant dual-export binding proof; synthetic inputs, no game or retail data.

Plan: execute SHA-256 pinned current MW resolver, control updater and CSIS
construction/update/destruction chains. Original uses the native CAR registry
(383F/198B,26); fixed adds only the two explicit source descriptors to this
isolated harness. No production registry/admission change or history baseline.
Independent expectations cover all 200 words, limits, updates and calibrations.
Measured: original 0/2 source exports bound (7 failing subchecks, exit 1);
fixed 2/2, initial 200/200 members and create/update 400/400. Six limit
sweeps cover 2,400 more member snapshots. Swap fails exact identities;
omitted primary layer / truncated tail / wrong w84 origin differ in 76/296/4
member snapshots. ASan/UBSan enabled throughout; all controls exit 1.
Aliased construction w0=17, mutated to 18: original rejects 0/2 swaps
(exit 1); fixed rejects 2/2 (exit 0), preserving both interface identities.
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
FROZEN = {'008311AA': 'c4a5b71f00aca0a960c62fce77a42c798e0b9ad7c1c2e6f978e56aeb556a19c9', '0083132B': '5b320977e80f09a18dd163540ed147d5c9c0926e945870784db2cdbd4ff4d679', '00831611': '62fa073f7d56a6bba1c452587c6851dc8aa15f5b367b1b67a5ab04c07d97ceba', '008313F3': '7c5a3f5b67ae8dd708db56d57eb8d2130e75ac1c5e1bad885c3307d96087045d', '00819875': 'f5b26159d5afc048d481cfb7b1f61d25ecea4901027ea079bb4683d2f88e89ce', '00831714': 'c02d1c61d6a87f3e71cdffbe4a6d31cfc4d7b598db0f9482cb2e45990b3ded87', '00830F0B': '3087164aab4dc4ae5337fea028041af8c66c78b11a3391832a30c352e97a5a19', '008311C6': 'a21da977139c6de6dc660174077d77c1dee848b0fbc3cf213911466ac1880f64', '00831782': 'c19565e6c5058b206bb51f735c3a3adc1a913170a864a5fed34dd6800b7e0c17', '00831182': 'e1359a1525b405610e08766a65da0dd5cde5c6797a7bfdfa4011ec800d31c888'}

HARNESS = r'''
#include "runtime.h"
#include "car_audio_engine_class.h"
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
uint8_t *G;
void f_00831611(Cpu *);
void f_00819875(Cpu *);
static CarAudioEngineClass objects[2];
static unsigned active, allocated, creates, updates, destroys, frees;
static int32_t values[100];
static void load_values(uint32_t at) {
    for(unsigned i=0;i<100;i++) {
        uint32_t n=M32(at+4*i);
        values[i]=n<=INT32_MAX?(int32_t)n:-1-(int32_t)(UINT32_MAX-n);
    }
}
static void snapshot(void) {
    printf("{\"kind\":%u,\"words\":[",active);
    for(unsigned i=0;i<100;i++) {
        uint32_t n=M32(0x500000+active*0x400+4*i);
        int32_t v=n<=INT32_MAX?(int32_t)n:-1-(int32_t)(UINT32_MAX-n);
        printf("%s%"PRId32,i?",":"",v);
    }
    puts("]}");
}
/* Allocation and mutex services alone are bounded host stand-ins; the CSIS
 * handle validation, callback lists, commands and cleanup execute pinned code.
 */
void f_00830FBA(Cpu *c) {
    assert(M32(c->esp+4)==16 && !allocated); allocated=1;
    memset(G+0x400000,0,16); c->eax=0x400000; c->esp+=4;
}
void f_00830EAB(Cpu *c) {
    assert(c->ecx==0x400000 && allocated && !objects[active].members);
    allocated=0; ++frees; c->esp+=4;
}
void f_00831029(Cpu *c) { c->esp+=4; }
void f_0083103A(Cpu *c) { c->esp+=4; }
void dispatch(Cpu *c,uint32_t target) {
    uint32_t arg=M32(c->esp+4), context=M32(c->esp+8);
    if(target==0xf000) {
        assert(arg==0x400000 && context==0x210018);
        unsigned kind=M32(c->esp+12); assert(kind==active);
        load_values(context);
        assert(car_audio_engine_class_construct(objects+active,kind,
            car_audio_engine_class_interface(kind),G+0x500000+kind*0x400,400,values,100)==0);
        M32(arg+8)=0x410000; M32(arg+12)=0x410020;
        M32(0x410008)=0xf001; M32(0x41000c)=kind;
        M32(0x410028)=0xf002; M32(0x41002c)=kind;
        ++creates;
    } else if(target==0xf001) {
        assert(context==active && arg==0x210018);
        load_values(arg);
        assert(car_audio_engine_class_update(objects+active,values,100)==0); ++updates;
    } else if(target==0xf002) {
        assert(arg==0x400000 && context==active);
        car_audio_engine_class_destroy(objects+active); ++destroys;
    } else abort();
    c->eax=0; c->esp+=4;
}
static Cpu cpu(void) {
    Cpu c={0}; c.esp=0x300000; c.esi=123; c.ebx=456; c.edi=789; c.ebp=1011;
    M32(c.esp)=0xffff; return c;
}
static void preserved(Cpu *c,uint32_t sp) {
    assert(c->esp==sp && c->esi==123 && c->ebx==456 && c->edi==789 && c->ebp==1011);
}
static void registry(void) {
    static const uint8_t native[]={0x3f,0x38,0x8b,0x19,'C','A','R',0};
    unsigned count=2;
#ifdef ORIGINAL
    count=1;
#endif
    M32(0x9c29e4)=0x100020;
    for(unsigned i=0;i<count;i++) {
        const CarAudioCsisInterface *t=car_audio_engine_class_interface(i);
        const uint8_t *id=t->id; size_t n=t->id_size;
#ifdef ORIGINAL
        id=native; n=sizeof(native);
#else
        (void)native;
#endif
        uint32_t b=0x100000+i*0x400,node=b+32,table=b+0x50;
        M32(node)=i+1<count?node+0x400:0;
        M16(node-16)=(uint16_t)(id[0]|id[1]<<8);
        M16(b+12)=1; M32(b+24)=table;
        M32(table+4)=b+0x100; M32(table+8)=id[2]|id[3]<<8;
        memcpy(G+b+0x100,id+4,n-4);
        /* Construct callback node; interface generation stays the signature. */
        M32(table)=b+0x200; M32(b+0x208)=0xf000; M32(b+0x20c)=i;
    }
}
static int resolve(unsigned kind) {
    const CarAudioCsisInterface *t=car_audio_engine_class_interface(kind);
    memcpy(G+0x120000,t->id+4,t->id_size-4);
    M32(0x121000)=0x120000; memcpy(G+0x121004,t->id,4);
    memset(G+0x210000,0,1024);
    Cpu c=cpu(); c.ecx=0x210000; M32(c.esp+4)=0x121000;
    f_00831611(&c); preserved(&c,0x300008);
    return (int32_t)c.eax==0 && M32(0x210000)!=0;
}
static void control(unsigned create,unsigned destroy) {
    M32(0x210010)=create; M32(0x210014)=destroy;
    Cpu c=cpu(); M32(c.esp+4)=0x210000;
    f_00819875(&c); preserved(&c,0x300004);
}
static void api(void) {
    uint8_t dest[408]; memset(dest,0xa5,sizeof(dest));
    CarAudioEngineClass p={0}; int32_t words[100];
    car_audio_engine_class_defaults(words);
    for(unsigned i=0;i<100;i++) assert(words[i]==(i==12));
    const CarAudioCsisInterface *t=car_audio_engine_class_interface(0);
    int identities=t->id_size==8 && !memcmp(t->id,"\x1c\x31\xd6\x0d" "CAR",8);
    const CarAudioCsisInterface *e=car_audio_engine_class_interface(1);
    identities += e->id_size==12 && !memcmp(e->id,"\x1c\x31\xef\x76" "CAR_ENG",12);
    if(identities!=2) { puts("{\"api\":0}"); return; }
    assert(!car_audio_engine_class_interface(2));
    assert(!car_audio_engine_class_matches(0,car_audio_engine_class_interface(1)));
    CarAudioCsisInterface bad=*t; bad.member_words=26;
    assert(!car_audio_engine_class_matches(0,&bad));
    bad=*t; bad.kind=0; assert(!car_audio_engine_class_matches(0,&bad));
    uint8_t id[8]; memcpy(id,t->id,8); bad=*t; bad.id=id;
    for(unsigned i=0;i<8;i++) { id[i]^=1; assert(!car_audio_engine_class_matches(0,&bad)); id[i]^=1; }
    for(size_t n=0;n<400;n++) assert(car_audio_engine_class_construct(&p,0,t,dest,n,words,100)<0);
    assert(car_audio_engine_class_construct(&p,0,t,dest,400,words,26)<0);
    for(unsigned i=0;i<408;i++) assert(dest[i]==0xa5);
    assert(car_audio_engine_class_construct(&p,0,t,dest+4,400,words,100)==0);
    assert(car_audio_engine_class_construct(&p,0,t,dest+4,400,words,100)<0);
    uint8_t before[408]; memcpy(before,dest,408);
    words[0]=1; assert(car_audio_engine_class_update(&p,words,100)<0);
    assert(!memcmp(before,dest,408)); words[0]=0;
    assert(car_audio_engine_class_update(&p,words,99)<0);
    car_audio_engine_class_destroy(&p); car_audio_engine_class_destroy(&p);
    assert(!p.members && car_audio_engine_class_update(&p,words,100)<0);
    words[0]=INT32_MIN;
    assert(car_audio_engine_class_construct(&p,0,t,dest+4,400,words,100)==0);
    assert(dest[4]==0 && dest[5]==0); car_audio_engine_class_destroy(&p);
    words[0]=INT32_MAX;
    assert(car_audio_engine_class_construct(&p,0,t,dest+4,400,words,100)==0);
    assert(dest[4]==99 && dest[5]==0); words[0]=99;
    assert(car_audio_engine_class_update(&p,words,100)==0);
    car_audio_engine_class_destroy(&p);
    uint8_t cal[180]={0};
    for(size_t n=0;n<180;n++) {
        assert(car_audio_engine_class_inputs(words,words,30,cal,n,cal,180)<0);
        assert(car_audio_engine_class_inputs(words,words,30,cal,180,cal,n)<0);
    }
    int32_t v=9;
    assert(car_audio_engine_class_rpm(NAN,0,1,&v)<0 && v==9);
    assert(car_audio_engine_class_rpm(1000,0,10000,&v)==0 && v==0);
    assert(car_audio_engine_class_rpm(5500,1000,9000,&v)==0 && v==5000);
    assert(car_audio_engine_class_rpm(100000,0,10000,&v)==0 && v==10000);
    assert(car_audio_engine_class_rpm(-100000,0,10000,&v)==0 && v==0);
    assert(car_audio_engine_class_pitch(1,&v)==0 && v==0);
    assert(car_audio_engine_class_pitch(0.5,&v)==0 && v==-8192);
    assert(car_audio_engine_class_pitch(-1,&v)==0 && v==-16383);
    assert(car_audio_engine_class_pitch(4,&v)==0 && v==16383);
    assert(car_audio_engine_class_pitch(NAN,&v)<0);
    puts("{\"api\":1}");
}
static void aliased_identity(void) {
    unsigned rejected=0;
    for(unsigned kind=0;kind<2;kind++) {
        CarAudioEngineClass p={0}; int32_t words[100];
        car_audio_engine_class_defaults(words); words[0]=17;
        assert(car_audio_engine_class_construct(&p,kind,
            car_audio_engine_class_interface(kind),(uint8_t *)words,
            sizeof(words),words,100)==0);
        assert(car_audio_engine_class_update(&p,words,100)==0);
        words[0]=18;
        int32_t before[100]; memcpy(before,words,sizeof(before));
        rejected += car_audio_engine_class_update(&p,words,100)<0;
        assert(!memcmp(before,words,sizeof(before)));
        car_audio_engine_class_destroy(&p);
        assert(car_audio_engine_class_construct(&p,kind,
            car_audio_engine_class_interface(kind),(uint8_t *)words,
            sizeof(words),words,100)==0);
        assert(car_audio_engine_class_update(&p,words,100)==0);
        car_audio_engine_class_destroy(&p);
    }
    printf("{\"aliased_swaps_rejected\":%u}\n",rejected);
}
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"api")) { api(); return 0; }
    if(argc==2 && !strcmp(argv[1],"alias")) { aliased_identity(); return 0; }
    G=calloc(1,0x1000000); assert(G); registry(); unsigned bound=0;
    for(active=0;active<2;active++) {
        if(!resolve(active)) continue;
        ++bound;
        for(unsigned step=0;step<2;step++) {
            uint8_t primary[180],secondary[180]; int32_t controls[30],words[100];
            assert(fread(controls,4,30,stdin)==30);
            assert(fread(primary,1,180,stdin)==180 && fread(secondary,1,180,stdin)==180);
            assert(car_audio_engine_class_inputs(words,controls,30,primary,180,secondary,180)==0);
            memcpy(G+0x210018,words,400);
            control(step==0,0); snapshot();
        }
        control(0,1); control(0,1); assert(!objects[active].members && !allocated);
    }
    printf("{\"bound\":%u,\"creates\":%u,\"updates\":%u,\"destroys\":%u,\"frees\":%u}\n",
           bound,creates,updates,destroys,frees);
    free(G); return 0;
}
'''


def pinned_functions():
    found = {}
    for filename in ('speed_55.c', 'speed_56.c'):
        path = ROOT / 'recomp/out' / filename
        if not path.is_file():
            raise unittest.SkipTest('pinned translated CSIS fragments absent')
        source = path.read_text()
        for name, digest in FROZEN.items():
            m = re.search(r'void f_' + name + r'\(Cpu \*restrict c\) \{.*?\n\}', source, re.S)
            if m:
                assert hashlib.sha256(m[0].encode()).hexdigest() == digest, name + ' fragment changed'
                found[name] = m[0]
    assert set(found) == set(FROZEN)
    names = set(found)
    for body in found.values():
        names.update(re.findall(r'f_([A-F0-9]{8})\(c\)', body))
    return '#include "runtime.h"\n' + ''.join('void f_' + n + '(Cpu *);\n' for n in sorted(names)) + '\n'.join(found.values())


def compile_proof(folder, original=False, mutant=None):
    folder.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'runtime/car_audio_engine_class.c').read_text()
    changes = {
        'swap': ('interfaces + kind : NULL', 'interfaces + (1-kind) : NULL'),
        'layer': ('read32(primary+0x28+4*n)', '(int32_t)0'),
        'tail': ('n < 100; ++n', 'n < 26; ++n'),
        'origin': ('(n ? secondary : primary)', '(secondary)'),
    }
    if mutant:
        old, new = changes[mutant]
        assert source.count(old) == 1
        source = source.replace(old, new)
    (folder / 'engine.c').write_text(source)
    (folder / 'harness.c').write_text(HARNESS)
    (folder / 'frozen.c').write_text(pinned_functions())
    command = [shutil.which('clang') or 'clang', '-std=c11', '-O1', '-g',
               '-Wall', '-Wextra', '-Werror', '-Wno-parentheses-equality', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all', '-I'+str(ROOT / 'runtime'),
               '-I'+str(ROOT / 'recomp'), str(folder / 'harness.c'),
               str(folder / 'engine.c'), str(folder / 'frozen.c'), '-o', str(folder / 'proof')]
    if original:
        command += ['-DORIGINAL']
    ran = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr[-4000:]
    return folder / 'proof'


def execute(binary, data=b'', mode=None):
    ran = subprocess.run([str(binary)] + ([mode] if mode else []), input=data,
                         capture_output=True, timeout=5)
    assert ran.returncode == 0, ran.stderr[-3000:].decode(errors='replace')
    return [json.loads(line) for line in ran.stdout.splitlines()]


def limits():
    # Decoded constructor ranges: source stores 0052712D..00527D59.
    result = [(0, 32767)] * 100
    def group(indices, lo, hi):
        for index in indices:
            result[index] = lo, hi
    group((0,), 0, 99)
    group((1, *range(30, 68)), 0, 10000)
    group((2, 3, *range(6, 10), 17, 23), 0, 1024)
    group((4, 5, 13, 26), -32767, 0)
    group((11,), 0, 4096)
    group((12,), 0, 2)
    group((15, 28), 0, 65536)
    group((16,), -16383, 16383)
    group((18, 19, 20), -10000, 10000)
    group((21, 22, 29), 0, 25000)
    group((24,), 0, 1)
    return result


def fixture(case=0):
    data = bytearray()
    expected = []
    for kind in range(2):
        for step in range(2):
            control = [((i+1)*193 + kind*701 + step*313) for i in range(30)]
            control[0] = 17 + kind
            # Both independent layer controls deliberately distinct, valid or
            # saturated; no member can pass by being silently left zero.
            for i in (4, 5, 13, 26):
                control[i] *= -1
            control[12] = 1
            primary = [1101 + i*137 + step*71 + kind*991 for i in range(45)]
            secondary = [7103 + i*157 + step*47 + kind*109 for i in range(45)]
            if case:
                # Sweep each member below/at/above its exact decoded limits.
                for i, (lo, hi) in enumerate(limits()[:30]):
                    control[i] = (lo-1, lo, hi, hi+1, -2147483648, 2147483647)[(case-1)%6]
                control[0] = 17 + kind  # retained identity during update
                for i in range(45):
                    primary[i] = (-1, 0, 10000, 32767, 2147483647, -2147483648)[(i+case)%6]
                    secondary[i] = (-1, 0, 10000, 32767, 2147483647, -2147483648)[(i+case+2)%6]
            # Independent source layout projection, by typed source fields.
            rpm_secondary = secondary[10:29]
            rpm_primary = primary[10:29]
            gain_secondary = secondary[37:45]
            gain_primary = primary[37:45]
            reference_secondary = [primary[29], *secondary[30:37]]
            reference_primary = primary[29:37]
            raw = control + rpm_secondary + rpm_primary + gain_secondary + gain_primary + reference_secondary + reference_primary
            assert len(raw) == 100
            expected.append({'kind': kind, 'words': [max(lo, min(hi, v)) for v, (lo, hi) in zip(raw, limits())]})
            data += struct.pack('<30i', *control) + struct.pack('<45i', *primary) + struct.pack('<45i', *secondary)
    return bytes(data), expected


class BindingProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='car-engine-class-')
        cls.folder = Path(cls.temp.name)
        cls.binary = compile_proof(cls.folder / 'selected', ARGS.original, ARGS.mutant)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_binding_and_complete_members(self):
        data, expected = fixture()
        got = execute(self.binary, data)
        total = got[-1]['bound']
        print(f'source exports bound: {total}/2', flush=True)
        self.assertEqual(total, 2)
        matched = sum(a == b for row, want in zip(got[:-1], expected)
                      if row['kind'] == want['kind']
                      for a, b in zip(row['words'], want['words']))
        initial = sum(a == b for index in (0, 2)
                      for a, b in zip(got[index]['words'], expected[index]['words']))
        print(f'initial members: {initial}/200; create/update members: {matched}/400', flush=True)
        self.assertEqual(got[:-1], expected)
        self.assertEqual(got[-1], dict(bound=2, creates=2, updates=4, destroys=2, frees=2))

    def test_limits_and_updates(self):
        for case in range(1, 7):
            with self.subTest(case=case):
                data, expected = fixture(case)
                self.assertEqual(execute(self.binary, data)[:-1], expected)

    def test_bounds_defaults_identity_and_encoding(self):
        self.assertEqual(execute(self.binary, mode='api'), [{'api': 1}])

    def test_aliased_construction_identity(self):
        got = execute(self.binary, mode='alias')
        print(f'aliased car swaps rejected: {got[0]["aliased_swaps_rejected"]}/2', flush=True)
        self.assertEqual(got, [{'aliased_swaps_rejected': 2}])

    def test_broken_controls(self):
        if ARGS.original or ARGS.mutant:
            self.skipTest('single selected control')
        data, expected = fixture()
        for mutant in ('swap', 'layer', 'tail', 'origin'):
            with self.subTest(mutant=mutant):
                binary = compile_proof(self.folder / mutant, mutant=mutant)
                got = execute(binary, data)
                differences = sum(a != b for row, want in zip(got[:-1], expected)
                                  for a, b in zip(row['words'], want['words']))
                # Swap must fail identity too; member values alone are not IDs.
                if mutant == 'swap':
                    self.assertNotEqual(execute(binary, mode='api'), [{'api': 1}])
                else:
                    self.assertGreater(differences, 0)
                print(f'{mutant} control: {differences} differing member snapshots', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--mutant', choices=('swap', 'layer', 'tail', 'origin'))
    ARGS, remaining = parser.parse_known_args()
    unittest.main(argv=[__file__, *remaining])
