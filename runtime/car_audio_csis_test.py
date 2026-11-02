#!/usr/bin/env python3
"""



























"""
import argparse
import hashlib
import importlib.util
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
SCRATCH = ROOT / '.metal-cache/car-audio-csis'
BASELINE = '13984ab7'
DEPENDENCY = 'a697c41b002e9936844881dafa1ae685478474bb'
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_abi as abi

# Complete translated bodies are pinned, including all failed-resolution and
# command branches. Never refresh these from a moving branch during a test.
FROZEN = {
    '0081AB36': '1940619c051695bbd5cf5ba4a75744c2c0dd0b4cfcdf6737cbf00a0940d6dbae',
    '00819875': 'f5b26159d5afc048d481cfb7b1f61d25ecea4901027ea079bb4683d2f88e89ce',
    '008196C2': '11aaa950d36289b4b1346358e030a4f37a0aecda50c7cb6db3a908eddc2fc563',
    '008196CE': 'f66a409b99eb6653d8e0816f138fadcfbcdf7b88340c413016a3459dbe4262b8',
    '0083132B': '5b320977e80f09a18dd163540ed147d5c9c0926e945870784db2cdbd4ff4d679',
    '00831611': '62fa073f7d56a6bba1c452587c6851dc8aa15f5b367b1b67a5ab04c07d97ceba',
    '008313F3': '7c5a3f5b67ae8dd708db56d57eb8d2130e75ac1c5e1bad885c3307d96087045d',
    '008317BC': '0142a9fa305fb185bd0fc1af3aea1f96b5fab5e1911b647da33c08873fdd4b68',
    '0081962E': '044c63fde11de74a2ed32c80c1ace5cb2a10cdcbf90eed89aa616fb83db1d1ba',
    '008311AA': 'c4a5b71f00aca0a960c62fce77a42c798e0b9ad7c1c2e6f978e56aeb556a19c9',
}

HARNESS = r'''
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "host.h"
#include "car_audio_csis.h"
uint8_t *G;
void interp_run(Cpu *,uint32_t);
void f_0081AB36(Cpu *);
void f_00819875(Cpu *);
void f_008196C2(Cpu *);
void f_008196CE(Cpu *);
void f_00831611(Cpu *);
void f_008313F3(Cpu *);
static CarAudioCsisInterface sources[64], targets[64];
static uint8_t ids[128][260];
static uint32_t ns, nt;
static unsigned unresolved, protects, creates, updates, releases, live, value;
static unsigned argcalls, ordinal;
static void *allocation;
static uint32_t get32(FILE *f) {
    uint8_t b[4]; assert(fread(b,1,4,f)==4);
    return (uint32_t)b[0] | (uint32_t)b[1]<<8 | (uint32_t)b[2]<<16 | (uint32_t)b[3]<<24;
}
static void read_refs(void) {
    ns=get32(stdin); nt=get32(stdin); assert(ns<=64 && nt<=64);
    for(uint32_t i=0;i<ns+nt;i++) {
        CarAudioCsisInterface *r=i<ns?sources+i:targets+i-ns;
        r->id_size=get32(stdin); r->member_words=(uint16_t)get32(stdin);
        r->kind=(uint8_t)get32(stdin); assert(r->id_size<=260);
        assert(fread(ids[i],1,r->id_size,stdin)==r->id_size); r->id=ids[i];
    }
}
static void registry(void) {
    M32(0x9c29e4)=nt?0x100020:0;
    for(uint32_t i=0;i<nt;i++) {
        uint32_t b=0x100000+i*0x400, node=b+32, table=b+0x50;
        M32(node)=i+1<nt?node+0x400:0;
        M16(node-16)=(uint16_t)(targets[i].id[0] | targets[i].id[1]<<8);
        M16(b+12)=1; M32(b+24)=table;
        M32(table+4)=b+0x100;
        M32(table+8)=(uint32_t)(targets[i].id[2] | targets[i].id[3]<<8);
        memcpy(G+b+0x100,targets[i].id+4,targets[i].id_size-4);
    }
}
static Cpu cpu(void) {
    Cpu c={0}; c.esp=0x300000; c.esi=0x12345678; c.ebx=0x76543210;
    c.edi=0x11223344; c.ebp=0x55667788; M32(c.esp)=0xffff; return c;
}
static void preserved(Cpu *c,uint32_t stack) {
    assert(c->esp==stack && c->esi==0x12345678 && c->ebx==0x76543210 &&
           c->edi==0x11223344 && c->ebp==0x55667788);
}
void host_import(Cpu *c,uint32_t site) {
    assert(site==0x890294); ++protects; c->eax=1; c->esp+=20;
}
void f_0081BCCB(Cpu *c) { c->esp+=4; }
void f_0081BCD9(Cpu *c) { c->esp+=4; }
void f_0081E9E7(Cpu *c) { (void)c; abort(); }
void f_008315E6(Cpu *c) { (void)c; abort(); }
void f_0083163C(Cpu *c) { (void)c; abort(); }
/* The shared control-class backend is bounded to one word/one object. It
 * observes real native ordinal-38 arguments, without installing game hooks.
 * State command/clamp/handle writes are exclusively the frozen MW routine.
 */
void f_00831714(Cpu *c) {
    assert(M32(c->esp+4)==0x210000 && !live && !allocation);
    /* Validate the real resolved handle/generation before the bounded class
     * backend allocates anything. A missing import cannot become a fake play.
     */
    Cpu validation=*c; validation.esp-=8;
    M32(validation.esp)=0xffff; M32(validation.esp+4)=0x210000;
    f_008313F3(&validation);
    assert(validation.eax==0);
    uint32_t parameters=M32(c->esp+8), out=M32(c->esp+12);
    assert(out==0x210008);
    assert(parameters==0x210018+(M8(0x21000c)?8*M8(0x21000d):0));
    value=M32(parameters); ++creates; live=1;
    allocation=malloc(1); assert(allocation);
    M32(0x400004)=1; M32(out)=0x400000; c->eax=0; c->esp+=4;
}
void f_008311C6(Cpu *c) {
    assert(c->ecx==0x400000 && live==1);
    assert(M32(c->esp+4)==0x210018+(M8(0x21000c)?8*M8(0x21000d):0));
    value=M32(M32(c->esp+4)); ++updates; c->eax=0; c->esp+=8;
}
void f_00831782(Cpu *c) {
    assert(c->ecx==0x400000 && live==1 && allocation);
    free(allocation); allocation=NULL; live=0; ++releases; c->eax=0; c->esp+=4;
}
int code_region_of(uint32_t t) { return t>=0x200000 && t<0x200018?0:-1; }
void dispatch(Cpu *c,uint32_t target) {
    assert(M32(c->esp+4)==0x210000); ++argcalls;
    if(target==0x819875 && ordinal==38) f_00819875(c);
    else if(target==0x8196c2 && ordinal==0) f_008196C2(c);
    else if(target==0x8196ce && ordinal==1) f_008196CE(c);
    else abort();
}
static void replay(void) {
    ordinal=get32(stdin); uint32_t n=get32(stdin); assert(n>=24 && n<=4096);
    assert(fread(G+0x210000,1,n,stdin)==n);
    if(ordinal==38) {
        static const uint8_t source_id[]={0x1c,0x31,0x01,0x19,
            'C','A','R','_','S','p','u','t','O','u','t','p','u','t',0};
        uint8_t target_id[sizeof(source_id)]; memcpy(target_id,source_id,sizeof(source_id));
        target_id[0]=0x3f; target_id[1]=0x38;
        CarAudioCsisInterface source={source_id,sizeof(source_id),1,1};
        targets[0]=(CarAudioCsisInterface){target_id,sizeof(target_id),1,1}; nt=1;
        assert(car_audio_csis_preflight(&source,1,targets,1)==0); registry();
        memcpy(G+0x120000,source_id+4,sizeof(source_id)-4);
        M32(0x121000)=0x120000; memcpy(G+0x121004,source_id,4);
        Cpu c=cpu(); c.ecx=0x210000; M32(c.esp+4)=0x121000; f_00831611(&c);
        preserved(&c,0x300008); assert(c.eax==0);
        /* registry no longer needs the borrowed stack-local ID. */
        targets[0]=(CarAudioCsisInterface){0};
    }
    /* Exact reached call shape: save ESI, load callback context, cursor -24,
     * push state, cdecl import, caller cleanup, restore ESI and return.
     * The context itself is state+24, matching the module loader's contract.
     */
    const uint8_t code[]={0x56,0x8b,0x74,0x24,0x08,0x83,0xc6,0xe8,
                          0x56,0xe8,0,0,0,0,0x83,0xc4,0x04,0x5e,0xc3};
    memcpy(G+0x200000,code,sizeof(code));
    uint32_t target=ordinal==38?0x819875:ordinal==0?0x8196c2:0x8196ce;
    M32(0x20000a)=target-0x20000e;
    unsigned steps=get32(stdin); assert(steps<=1000);
    for(unsigned i=0;i<steps;i++) {
        uint32_t field=get32(stdin), v=get32(stdin); assert(field<=n-4);
        M32(0x210000+field)=v;
        Cpu c=cpu(); M32(c.esp+4)=0x210018; interp_run(&c,0x200000);
        preserved(&c,0x300004);
        printf("{\"result\":%"PRIu32",\"handle\":%"PRIu32",\"pulse\":%"PRIu32
               ",\"value\":%u,\"wire_value\":%"PRIu32",\"creates\":%u,\"updates\":%u,"
               "\"releases\":%u,\"live\":%u,\"calls\":%u,\"state\":\"",c.eax,M32(0x210008),
               M32(0x210010),value,M32(0x210028),creates,updates,releases,live,argcalls);
        for(unsigned j=0;j<n;j++) printf("%02x",M8(0x210000+j));
        printf("\"}\n");
    }
    assert(!live && !allocation);
}
int main(int argc,char **argv) {
    assert(argc>=2); G=calloc(1,0x1000000); assert(G);
    if(!strcmp(argv[1],"replay")) { replay(); free(G); return 0; }
    if(!strcmp(argv[1],"api")) {
        assert(car_audio_csis_check(NULL)==CAR_AUDIO_CSIS_INVALID);
        assert(car_audio_csis_preflight(NULL,0,NULL,0)==CAR_AUDIO_CSIS_INVALID);
        assert(car_audio_csis_preflight(sources,65537,NULL,0)==CAR_AUDIO_CSIS_INVALID);
        CarAudioCsisInterface r={0};
        assert(car_audio_csis_check(&r)==CAR_AUDIO_CSIS_INVALID);
        static const uint8_t id[]={0x1c,0x31,0x59,0x78,'C','A','R','_','S','W','T','N',0};
        r=(CarAudioCsisInterface){id,sizeof(id),7,1};
        assert(car_audio_csis_resolve(&r,NULL,1,NULL)==CAR_AUDIO_CSIS_INVALID);
        assert(car_audio_csis_resolve(&r,targets,65537,NULL)==CAR_AUDIO_CSIS_INVALID);
        puts("{\"invalid_calls\":6}"); free(G); return 0;
    }
    read_refs(); registry();
    if(!strcmp(argv[1],"preflight")) {
        uint8_t before[sizeof(ids)]; memcpy(before,ids,sizeof(ids));
        int result=car_audio_csis_preflight(sources,ns,targets,nt);
        assert(!memcmp(before,ids,sizeof(ids)));
        printf("{\"guard\":%d}\n",result);
    } else if(!strcmp(argv[1],"execution")) {
        for(uint32_t i=0;i<ns;i++) {
            size_t index=12345;
            int result=car_audio_csis_execution_resolve(sources+i,targets,nt,&index);
            printf("{\"guard\":%d,\"index\":%zu}\n",result,index);
        }
    } else if(!strcmp(argv[1],"resolve")) {
        for(uint32_t i=0;i<ns;i++) {
            size_t index=12345; int result=car_audio_csis_resolve(sources+i,targets,nt,&index);
            /* Translate the wire ID into the local InterfaceId used by MW. */
            uint32_t native_result=UINT32_MAX, native_handle=0;
            if(sources[i].id_size>=6 && sources[i].id[sources[i].id_size-1]==0) {
            memcpy(G+0x120000,sources[i].id+4,sources[i].id_size-4);
            M32(0x121000)=0x120000; memcpy(G+0x121004,sources[i].id,4);
            M32(0x122000)=0; M32(0x122004)=UINT32_MAX;
            Cpu c=cpu(); c.ecx=0x122000; M32(c.esp+4)=0x121000; f_00831611(&c);
            preserved(&c,0x300008);
            native_result=c.eax; native_handle=M32(0x122000);
            }
            printf("{\"guard\":%d,\"index\":%zu,\"native\":%"PRIu32",\"handle\":%"PRIu32"}\n",
                   result,index,native_result,native_handle);
        }
    } else if(!strcmp(argv[1],"load")) {
        assert(argc==3); FILE *f=fopen(argv[2],"rb"); assert(f);
        size_t n=fread(G+0x10000,1,0x10000,f); assert(n>=0x60 && feof(f)); fclose(f);
        uint8_t *before=malloc(n); assert(before); memcpy(before,G+0x10000,n);
        int result=car_audio_csis_preflight(sources,ns,targets,nt);
#ifdef ORIGINAL
        result=0;
#endif
        if(!result) {
            Cpu c=cpu(); M32(c.esp+4)=0x10000; M32(c.esp+8)=0x10000+M32(0x10030);
            M32(c.esp+12)=0; M32(c.esp+16)=0; f_0081AB36(&c); preserved(&c,0x300004);
            uint32_t d=0x10000+M32(0x1001c);
            for(unsigned i=0;i<M16(0x1000a);i++) {
                if(!M32(d+4)) ++unresolved;
                d+=60+4*(M8(d+36)+M8(d+39));
            }
        }
        unsigned changed=0; for(size_t i=0;i<n;i++) changed+=before[i]!=G[0x10000+i];
        printf("{\"guard\":%d,\"changed_bytes\":%u,\"protects\":%u,\"unresolved\":%u}\n",
               result,changed,protects,unresolved); free(before);
    } else abort();
    free(G); return 0;
}
'''


def ref(name, system=0x311c, signature=None, members=None, kind=1):
    profile = abi.INTERFACES.get(name)
    if signature is None:
        signature = profile[0] if profile else 123
    if members is None:
        members = profile[1] if profile else 1
    return (struct.pack('<HH', system, signature) + name.encode('ascii') + b'\0', members, kind)


def registry():
    return [ref(name, 0x383f, signature=p[2], members=p[3])
            for name, p in abi.INTERFACES.items() if p[2] is not None]


def refs_bytes(sources, targets):
    result = struct.pack('<II', len(sources), len(targets))
    for identity, members, kind in [*sources, *targets]:
        result += struct.pack('<III', len(identity), members, kind) + identity
    return result


def compile_proof(folder, original=False, mutant=None):
    folder.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang')
    if not compiler:
        raise unittest.SkipTest('clang required for translated CSIS replay')
    source = (ROOT / 'runtime/car_audio_csis.c').read_text()
    if mutant == 'signature':
        old = 'word(source->id + 2) != signature'
        assert source.count(old) == 1
        source = source.replace(old, '(word(source->id + 2) != signature && 0)')
    elif mutant == 'layout':
        old = 'source->member_words != members'
        assert source.count(old) == 1
        source = source.replace(old, '(source->member_words != members && 0)')
    elif mutant == 'preflight':
        old = 'if (result) return result;'
        assert source.count(old) == 2
        # Keep single-ID checking intact; break only the module-wide barrier.
        at = source.rindex(old)
        source = source[:at] + source[at:].replace(old, '(void)result;', 1)
    (folder / 'adapter.c').write_text(source)
    (folder / 'harness.c').write_text(HARNESS)
    generated = {}
    for name in ('speed_55.c', 'speed_56.c'):
        path = ROOT / 'recomp/out' / name
        if not path.is_file():
            raise unittest.SkipTest('pinned translated MW bodies absent')
        text = path.read_text()
        for key, digest in FROZEN.items():
            match = re.search(r'void f_' + key + r'\(Cpu \*restrict c\) \{.*?\n\}', text, re.S)
            if match:
                assert hashlib.sha256(match[0].encode()).hexdigest() == digest, key + ' baseline changed'
                generated[key] = match[0]
    assert set(generated) == set(FROZEN)
    names = set(generated)
    for body in generated.values():
        names.update(re.findall(r'f_([A-F0-9]{8})\(c\)', body))
    frozen = '#include "runtime.h"\n' + ''.join('void f_' + n + '(Cpu *);\n' for n in sorted(names))
    (folder / 'frozen.c').write_text(frozen + '\n'.join(generated.values()))
    command = [compiler, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-parentheses-equality', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=all', '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp'),
               str(folder / 'harness.c'), str(folder / 'adapter.c'), str(folder / 'frozen.c'),
               str(ROOT / 'runtime/interp.c'), '-o', str(folder / 'proof')]
    if original:
        command += ['-DORIGINAL']
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr[-4000:]
    return folder / 'proof'


def execute(binary, mode, data, *args):
    ran = subprocess.run([str(binary), mode, *map(str, args)], input=data,
                         capture_output=True, timeout=5)
    assert ran.returncode == 0, f'exit {ran.returncode}: {ran.stderr[-2000:].decode(errors="replace")}'
    return [json.loads(line) for line in ran.stdout.splitlines()]


def load_fixture():
    spec = importlib.util.spec_from_file_location('abi_fixture', ROOT / 'tests/setup/test_car_audio_abi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay(binary, ordinal, state, steps):
    data = struct.pack('<II', ordinal, len(state)) + state + struct.pack('<I', len(steps))
    data += b''.join(struct.pack('<II', field, value & 0xffffffff) for field, value in steps)
    return execute(binary, 'replay', data)


def control_expectation(initial, steps):
    """Independent decoded source command contract, including full state writes."""
    state = bytearray(initial)
    # The normal resolver binds the imported ClassHandle before code runs.
    # The instance pointer at +8 remains independently owned by ordinal 38.
    struct.pack_into('<II', state, 0, 0x100050, 0x1901)
    clamp, members = state[12:14]
    at = 16 + (8*members if clamp else 0)
    alive = False
    snapshots = []
    for field, value in steps:
        struct.pack_into('<I', state, field, value & 0xffffffff)
        create, destroy = struct.unpack_from('<ii', state, at)
        if destroy:
            alive = False
        elif (create and not alive) or (not create and alive):
            if clamp:
                for member in range(members):
                    lo, hi = struct.unpack_from('<ii', state, 16 + 8*member)
                    val = struct.unpack_from('<i', state, at+8+4*member)[0]
                    struct.pack_into('<i', state, at+8+4*member, max(lo, min(hi, val)))
            alive = True
        struct.pack_into('<I', state, 8, 0x400000 if alive else 0)
        snapshots.append(state.hex())
    return snapshots


class CsisProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='proof-', dir=SCRATCH)
        cls.folder = Path(cls.temp.name)
        cls.binary = compile_proof(cls.folder, CONFIG.original, CONFIG.mutant)
        cls.original = compile_proof(cls.folder / 'original', True)
        cls.fixtures = load_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_all_manifest_contracts_and_real_resolver(self):
        names = list(abi.INTERFACES)
        result = execute(self.binary, 'resolve', refs_bytes([ref(n) for n in names], registry()))
        self.assertEqual([r['guard'] for r in result], [-4, -5, 0, 0, 0, 0])
        self.assertEqual([r['native'] for r in result], [4294967291]*2 + [0]*4)
        self.assertEqual([r['index'] for r in result], [12345]*2 + [1, 2, 3, 4])
        self.assertTrue(all(r['handle'] for r in result[2:]))

    def test_explicit_execution_registry_keeps_generic_admission_closed(self):
        targets = [ref('CAR'), ref('CAR_ENG'), *registry()]
        sources = [ref(name) for name in abi.INTERFACES]
        actual = execute(self.binary, 'execution', refs_bytes(sources, targets))
        self.assertEqual([row['guard'] for row in actual], [0]*6)
        self.assertEqual([row['index'] for row in actual], [0, 1, 3, 4, 5, 6])
        for name in ('CAR', 'CAR_ENG'):
            source = ref(name)
            cases = [([], -6), ([ref(name, system=0x383f)], -6),
                     ([ref(name, signature=source[0][2] ^ 1)], -6),
                     ([ref(name, members=26), source], -3),
                     ([ref(name, kind=0), source], -1),
                     ([source], 0)]
            for targets, error in cases:
                row = execute(self.binary, 'execution', refs_bytes([source], targets))[0]
                self.assertEqual(row, dict(guard=error, index=0 if error == 0 else 12345))
            # An explicit execution registry cannot enable generic preflight.
            generic = execute(self.binary, 'preflight', refs_bytes([source], [source]))[0]
            self.assertEqual(generic['guard'], -4 if name == 'CAR' else -5)
        for name, profile in abi.INTERFACES.items():
            for bad in (ref(name, system=0x383f), ref(name, signature=profile[0] ^ 1),
                        ref(name, members=profile[1]+1), ref(name, kind=0)):
                row = execute(self.binary, 'execution', refs_bytes([bad], [ref(name)]))[0]
                self.assertNotEqual(row['guard'], 0)
                self.assertEqual(row['index'], 12345)

    def test_original_failed_binding_still_writes_module(self):
        before = []
        for name, error in [('CAR', -4), ('CAR_ENG', -5)]:
            with self.subTest(name=name):
                bank = self.fixtures.fixture(name=name)
                report = abi.inspect_bank(bank, self.fixtures.resource(bank))
                self.assertEqual(report['modules'][0]['member_words'], 100)
                path = self.folder / (name + '.abk')
                path.write_bytes(bank)
                data = refs_bytes([ref(name)], registry())
                original = execute(self.original, 'load', data, path)[0]
                self.assertEqual(original['unresolved'], 1)
                self.assertEqual(original['protects'], 1)
                self.assertEqual(original['changed_bytes'], 13)
                before.append(original['changed_bytes'])
                actual = execute(self.binary, 'load', data, path)[0]
                self.assertEqual(actual, dict(guard=error, changed_bytes=0, protects=0, unresolved=0))
                self.assertEqual(path.read_bytes(), bank)
        print(f'original {BASELINE}: CAR/CAR_ENG unresolved=2; changed bytes={before}; guarded=0 writes')

    def test_identical_interface_passes_original_loader(self):
        for name in ('CAR_SWTN', 'CAR_WHINE', 'CAR_Sputter'):
            with self.subTest(name=name):
                bank = self.fixtures.fixture(name=name)
                abi.inspect_bank(bank, self.fixtures.resource(bank))
                path = self.folder / (name + '.abk')
                path.write_bytes(bank)
                actual = execute(self.binary, 'load', refs_bytes([ref(name)], registry()), path)[0]
                self.assertEqual(actual['guard'], 0)
                self.assertEqual((actual['unresolved'], actual['protects']), (0, 1))

    def test_wrong_ids_counts_and_kind_rejected(self):
        for name, profile in abi.INTERFACES.items():
            for source, error in [(ref(name, signature=profile[0] ^ 1), -2),
                                  (ref(name, members=profile[1] + 1), -3),
                                  (ref(name, system=0x383f), -2),
                                  (ref(name, kind=0), -1)]:
                with self.subTest(name=name, source=source):
                    actual = execute(self.binary, 'resolve', refs_bytes([source], registry()))[0]
                    self.assertEqual(actual['guard'], error)
                    self.assertEqual(actual['index'], 12345)

    def test_unknown_and_malformed_ids(self):
        good = ref('CAR_SWTN')
        for source, error in [(ref('UNKNOWN'), -2), ((b'\x1c\x31', 7, 1), -1),
                              ((good[0][:-1], 7, 1), -1),
                              ((good[0]+b'extra\0', 7, 1), -1),
                              ((good[0][:4]+b'\xff\0', 7, 1), -1)]:
            with self.subTest(source=source):
                actual = execute(self.binary, 'resolve', refs_bytes([source], []))[0]
                self.assertEqual(actual['guard'], error)
                self.assertEqual(actual['index'], 12345)

    def test_preference_fallback_and_unsafe_first_match(self):
        source = ref('CAR_SWTN')
        for targets, index, error in [([], 12345, -6),
                                     ([ref('CAR_SWTN', 0x383f)], 0, 0),
                                     ([ref('CAR_SWTN', 0x383f), source], 1, 0),
                                     ([ref('CAR_WHINE', 0x311c), ref('CAR_SWTN', 0x383f)], 1, 0),
                                     ([ref('CAR_SWTN', 0x383f, members=100), ref('CAR_SWTN', 0x383f)], 12345, -3),
                                     ([ref('CAR_SWTN', 0x383f), ref('CAR_SWTN', members=100)], 12345, -3)]:
            with self.subTest(targets=targets):
                actual = execute(self.binary, 'resolve', refs_bytes([source], targets))[0]
                self.assertEqual((actual['guard'], actual['index']), (error, index))
                if not error:
                    self.assertEqual(actual['native'], 0)
                    self.assertEqual(actual['handle'], 0x100050 + index*0x400)

    def test_late_unimplemented_row_prevents_all_loader_writes(self):
        path = self.folder / 'late.abk'
        path.write_bytes(self.fixtures.fixture(name='CAR_SWTN'))
        sources = [ref('CAR_SWTN'), ref('CAR_SputOutput'), ref('CAR_ENG')]
        actual = execute(self.binary, 'load', refs_bytes(sources, registry()), path)[0]
        self.assertEqual(actual, dict(guard=-5, changed_bytes=0, protects=0, unresolved=0))

    def test_bounded_api_and_empty_manifest(self):
        self.assertEqual(execute(self.binary, 'api', b''), [dict(invalid_calls=6)])
        self.assertEqual(execute(self.binary, 'preflight', refs_bytes([], registry())), [dict(guard=-1)])

    def test_member_and_pulse_interpreter_calls(self):
        for value in [0, 1, 0x7fffffff, 0x80000000, 0xffffffff]:
            state = bytes(64)
            member = replay(self.binary, 1, state, [(20, value), (20, value)])[0]
            self.assertEqual((member['result'], member['calls']), (value, 1))
            pulse = replay(self.binary, 0, state, [(16, value), (20, 123)])
            self.assertEqual([r['result'] for r in pulse], [value, 0])
            self.assertEqual([r['pulse'] for r in pulse], [0, 0])

    def test_shared_control_class_lifetime_and_clamp(self):
        for clamp in (False, True):
            state = bytearray(64)
            # One shared CAR_SputOutput member, optional signed i32 bounds.
            state[12:14] = bytes((int(clamp), 1))
            at = 24 if clamp else 16
            if clamp:
                struct.pack_into('<ii', state, 16, -3, 7)
            struct.pack_into('<iii', state, at, 1, 0, -9)
            steps = [(at+8, -9), (at+8, 99),
                            (at, 0), (at+4, 1), (at+4, 1), (at, 1),
                            (at+4, 0), (at+4, 1)]
            actual = replay(self.binary, 38, state, steps)
            self.assertEqual([r['result'] for r in actual], [1, 1, 1, 0, 0, 0, 1, 0])
            self.assertEqual([r['handle'] for r in actual], [0x400000]*3 + [0]*3 + [0x400000, 0])
            # Source 00897D56 and target 008198BF both suppress update while
            # create remains asserted on a live instance. The third tick clears
            # create and sends the changed value; no invented per-tick update.
            self.assertEqual([r['value'] for r in actual], ([0xfffffffd]*2 + [7]*6
                                                           if clamp else [0xfffffff7]*2 + [99]*6))
            self.assertEqual((actual[-1]['creates'], actual[-1]['releases'], actual[-1]['live']), (2, 2, 0))
            self.assertEqual([r['calls'] for r in actual], list(range(1, 9)))
            expected = control_expectation(state, steps)
            for found, wanted in zip(actual, expected):
                self.assertEqual(found['state'], wanted)


def manifest_refs(data, manifest):
    """Consume all A4 identities; derive both exported and imported counts."""
    exports = {m['descriptor']+4: m for m in manifest['modules']}
    controls = {m['state']+off: m for m in manifest['modules'] for off in m['class_offsets']}
    refs = []
    for row in manifest['interfaces']:
        offset, site = row['name_offset'], row['site']
        end = data.index(0, offset+4) + 1
        identity = data[offset:end]
        assert identity == ref(row['name'], row['source_system'], row['source_signature'])[0]
        if site in exports:
            words = exports[site]['member_words']
        else:
            module = controls[site]
            assert row['name'] == 'CAR_SputOutput'
            assert data[site+12] in (0, 1)
            # +13 is the generic control input/clamp count, NOT the class's
            # member count. Real unclamped states carry 3 here while this
            # class's consumer reads one word. Preserve the opaque extra input
            # words; derive the exported contract from A4's decoded signature.
            words = row['source_member_words']
            inputs = data[site+13]
            need = 16 + (8*inputs if data[site+12] else 0) + 8 + 4*max(inputs, words)
            assert site + need <= module['state'] + module['state_size']
        refs.append((identity, words, row['kind']))
    assert len(exports) + len(controls) == len(refs)
    return refs


def private_proof():
    """Real reachable state calls in bounded wrappers, not whole-bank execution.

    Every interface row is passed to the C guard AND original MW resolver.
    A4's reached E8 sites/arguments select real template bytes for ordinal 0/1/38
    replay; generated wrappers isolate these primitives from unproved players
    and engine controllers. No claim of complete program execution is made.
    """
    source = Path(os.environ.get('NFS_CARBON_PC', Path.home() / 'Downloads/NFSC'))
    roster = ROOT / 'tools_snapshot/private_cars.py'
    if not (source / 'GLOBAL/attributes.bin').is_file() or not roster.is_file():
        print('SKIP private source inputs absent; standalone CSIS proof completed')
        return False
    from setup_kit import car_audio
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    resources = {p.relative_to(source).as_posix(): p.read_bytes()
                 for p in (source / 'SOUND/ENGINE').rglob('*')
                 if p.suffix.lower() in ('.abk', '.gin')}
    graphs = car_audio.inventory((source / 'GLOBAL/attributes.bin').read_bytes(), cars, resources)
    assert not graphs['unsupported']
    manifest = abi.closure_manifest(graphs['supported'].values(), resources)
    counts = dict(graphs=len(graphs['supported']), banks=0, interfaces=0,
                  native_unresolved=0, guard_rejected=0, guard_accepted=0,
                  unresolved_accepted=0, csis_only_banks=0, rejected_banks=0,
                  member_calls=0, pulse_calls=0, control_calls=0, snapshots=0,
                  creates=0, releases=0, live=0)
    errors = {}
    with tempfile.TemporaryDirectory(prefix='private-', dir=SCRATCH) as directory:
        binary = compile_proof(Path(directory))
        for path, bank in manifest['banks'].items():
            data = resources[path]
            refs = manifest_refs(data, bank)
            rows = execute(binary, 'resolve', refs_bytes(refs, registry()))
            counts['banks'] += 1
            for row in rows:
                counts['interfaces'] += 1
                counts['native_unresolved'] += row['native'] != 0
                counts['guard_rejected'] += row['guard'] != 0
                counts['guard_accepted'] += row['guard'] == 0
                counts['unresolved_accepted'] += row['guard'] == 0 and row['native'] != 0
                if row['guard']:
                    error = str(row['guard'])
                    errors[error] = errors.get(error, 0) + 1
            result = execute(binary, 'preflight', refs_bytes(refs, registry()))[0]['guard']
            assert (result == 0) == all(r['guard'] == 0 for r in rows)
            counts['csis_only_banks' if result == 0 else 'rejected_banks'] += 1
            instructions = {r['offset']: r for r in bank['instructions']}
            for module in bank['modules']:
                template = data[module['state']:module['state']+module['state_size']]
                # Template bounds can overlap another module's code. Walk the
                # classified CFG rather than assigning calls by linear extent.
                reached, todo = set(), [module['entry']]
                while todo:
                    pc = todo.pop()
                    if pc in reached:
                        continue
                    reached.add(pc)
                    instruction = instructions[pc]
                    if instruction['mnemonic'] == 'ret':
                        continue
                    if instruction['branch'] is not None:
                        todo.append(instruction['branch'])
                    if instruction['mnemonic'] != 'jmp':
                        todo.append(pc + instruction['size'])
                assert len(reached) == module['instruction_count']
                calls = [r for r in bank['imports'] if r['site']-1 in reached and r['ordinal'] in (0, 1, 38)]
                for call in calls:
                    ordinal, offset = call['ordinal'], call['instance_offset']
                    assert data[call['site']-1] == 0xe8
                    assert struct.unpack_from('<I', data, call['site'])[0] == ordinal
                    if ordinal in (0, 1):
                        state = template[offset:offset+24]
                        assert len(state) == 24
                        field = 16 if ordinal == 0 else 20
                        value = struct.unpack_from('<I', state, field)[0]
                        steps = [(field, value), (0, struct.unpack_from('<I', state)[0])]
                        actual = replay(binary, ordinal, state, steps)
                        expected = bytearray(state)
                        if ordinal == 0:
                            struct.pack_into('<I', expected, 16, 0)
                        assert [r['result'] for r in actual] == [value, 0 if ordinal == 0 else value]
                        assert all(r['state'] == expected.hex() for r in actual)
                        counts['pulse_calls' if ordinal == 0 else 'member_calls'] += 1
                    else:
                        assert offset in module['class_offsets']
                        clamp = template[offset+12]
                        inputs = template[offset+13]
                        size = 24 + (8*inputs if clamp else 0) + 4*max(1, inputs)
                        state = template[offset:offset+size]
                        assert len(state) == size and not struct.unpack_from('<I', state, 8)[0]
                        at = 16 + (8*inputs if clamp else 0)
                        # First observe the authored template commands unchanged,
                        # then replay a complete update/destroy/recreate lifetime.
                        steps = [(at, struct.unpack_from('<I', state, at)[0]),
                                 (at, 1), (at, 0), (at+8, 0x7fffffff),
                                 (at+4, 1), (at+4, 0), (at, 1), (at+4, 1)]
                        actual = replay(binary, ordinal, state, steps)
                        assert [r['state'] for r in actual] == control_expectation(state, steps)
                        assert actual[-1]['creates'] == actual[-1]['releases']
                        counts['control_calls'] += 1
                        counts['creates'] += actual[-1]['creates']
                        counts['releases'] += actual[-1]['releases']
                        counts['live'] += actual[-1]['live']
                    counts['snapshots'] += len(steps)
    assert counts['unresolved_accepted'] == counts['live'] == 0
    assert counts['creates'] == counts['releases']
    (SCRATCH / 'private-counts.json').write_text(json.dumps(dict(counts=counts, errors=errors), indent=2)+'\n')
    print('private CSIS proof: ' + json.dumps(dict(counts=counts, errors=errors), sort_keys=True))
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', action='store_true')
    parser.add_argument('--mutant', choices=('signature', 'layout', 'preflight'))
    parser.add_argument('--private', action='store_true')
    CONFIG = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CsisProof))
    if not result.wasSuccessful():
        sys.exit(1)
    if not result.testsRun:
        sys.exit(3)
    if CONFIG.private:
        sys.exit(0 if private_proof() else 3)
