"""Compatibility proof against frozen loader/player behavior at 13984ab7.

Plan: execute generated x86 glue through the real bounded-process interpreter,
relocating ordinal 27 exactly as the original loader does. Its MW player branch
uses a source sample index as a player dispatch type. Compare exact signatures
and word counts, not names alone. Private proof inventories A1's full closure.
--original must fail on that behavior; --mutant import/csis/instruction must fail
the same acceptance assertions. No missing-module failure counts as a baseline.
Measured counts and full-suite result are in car_audio_abi_contract.md.
Review regression (eb845b37): A1-to-A4 backslash closure 0/1 before, 1/1
after; slash-path control 1/1 before and after, using generated valid bank bytes.
Ramp layout regression: target +18, cached duration +0C (008985D6/0081A707).
Measured: 0/1 before the inventory correction; 1/1 after; --mutant ramp fails 1/1.
"""
import copy
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

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / '.metal-cache/car-audio-abi'
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_abi as abi
from setup_kit import car_audio
from setup_kit import car_audio_codec

BASELINE = '13984ab7'
SOURCE_EXE_SHA = 'baaaaec33873d4ba7e4dbae6a5212049ffdcf9679a35d865d7625b4189952fd3'
TARGET_EXE_SHA = '80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c'


def put(data, at, *values):
    struct.pack_into('<' + 'I' * len(values), data, at, *values)


def fixture(name='CAR', signature=None, ordinal=27):
    """One generated module, one player, one selector pointer and one CSIS ID."""
    signature, member_count = abi.INTERFACES[name][:2] if signature is None else (signature, 100)
    descriptor = 0x60
    code = 0xa0
    # Save ESI; get instance; address PLAYERSTATE; cdecl import; restore stack.
    glue = (b'\x56\x8b\x74\x24\x08\x81\xc6' + struct.pack('<I', 512 - 24) +
            b'\x56\xe8' + struct.pack('<I', ordinal) + b'\x83\xc4\x04\x5e\xc3')
    state = code + len(glue)
    bank = (state + 1024 + 127) & ~127
    extent = 20 + 2 * (8 + 8 + 76)
    f1 = bank + extent
    f2 = f1 + 8
    f3 = f2 + 8
    tail = f3 + 16
    text = struct.pack('<2H', 0x311c, signature) + name.encode() + b'\0'
    data = bytearray(tail + len(text))
    data[:4] = b'ABKC'
    data[4:8] = b'\x01\x01\x02\x01'
    struct.pack_into('<H', data, 10, 1)
    put(data, 0x14, len(data), bank, descriptor, bank, extent)
    put(data, 0x30, f1, f2, f3)
    struct.pack_into('<H', data, descriptor + 30, 4)
    data[descriptor + 36:descriptor + 40] = bytes((1, 1, 1, 0))
    put(data, descriptor + 40, code, state, 1024, 1008, 0, 512)
    data[code:state] = glue
    data[state + 60] = member_count
    put(data, state + 512 + 4, state + 900)
    data[state + 512 + 16] = 255
    put(data, state + 512 + 20, 1, 1)
    put(data, state + 900, 2, 60 << 16, 1, 0xffffffff, 80 << 16 | 1, 2, 0xffffffff)
    data[bank:bank + 4] = b'S10A'
    struct.pack_into('>3I', data, bank + 8, 2, 20, 112)
    for offset in (20, 112):
        struct.pack_into('>4I', data, bank + offset, 4 << 24 | 44100, 1, 84, 1)
    put(data, f1, 1, code + 13)
    put(data, f2, 1, state + 516)
    # The upper three kind bytes are opaque padding, not a 32-bit enum.
    put(data, f3, 1, descriptor + 4, tail, 0xaabbcc01)
    data[tail:] = text
    return bytes(data)


def resource(data):
    return {'kind': 'abk', 'path': 'SOUND/ENGINE/fixture.abk', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), **car_audio.bank_inventory(data)}


def graph(row):
    return {'schema': 'source-car-audio', 'version': 1,
            'source': {'game': 'carbon', 'platform': 'pc'}, 'resources': [row]}


def frozen_relocate(data, target_base=0x10000):
    """13984ab7 / 0081AB36's ordinal and absolute relocation arithmetic.

    This bounded fixture deliberately retains its original ordinal-only policy.
    It does not pretend to resolve or execute unknown imports.
    """
    image = bytearray(data)
    f1, f2 = struct.unpack_from('<2I', data, 0x30)
    count = abi._u32(data, f1)
    assert count <= 65536 and f1 + 4 + 4 * count <= f2
    bound = []
    for i in range(count):
        site = abi._u32(data, f1 + 4 + 4 * i)
        ordinal = abi._u32(data, site)
        contract = abi.import_contract(ordinal)
        target = contract['target_pc']
        put(image, site, (target - (target_base + site + 4)) & 0xffffffff)
        bound.append((site, target))
    count = abi._u32(data, f2)
    for i in range(count):
        site = abi._u32(data, f2 + 4 + 4 * i)
        put(image, site, abi._u32(data, site) + target_base)
    return bytes(image), bound


def frozen_resolve(name, system, signature, target_interfaces):
    """0083132B: preferred system first, then ANY system; same name AND signature."""
    for preferred in (True, False):
        for n, s, sig in target_interfaces:
            if n == name and sig == signature and (not preferred or s == system):
                return n
    return None


HARNESS = r'''
#include <stdlib.h>
#include "host.h"
uint8_t *G;
void interp_run(Cpu *, uint32_t);
static uint32_t code_start, code_end;
static unsigned plays, invalid_player_dispatch, calls;
int code_region_of(uint32_t t) { return t>=code_start && t<code_end ? 0 : -1; }
void dispatch(Cpu *c, uint32_t target) {
    if(target!=0x81a52c) exit(10);
    uint32_t p=arg(c,0);
    if(p>0x3fffff-28) exit(11);
    /* Frozen 0081A52C's command=1 transition and 0090BBF0 play vector:
       entry zero is bank play, entries 1..3 are null. Source row+0 is
       sample index (short), not a MW dispatch type (signed byte). */
    int command=(int32_t)M32(p+24);
    if(command<0) command=0; else if(command>2) command=2;
    if(command!=M8(p+12) && command==1) {
        if((int8_t)M8(p+16)<0) {
            uint32_t selectors=M32(p+4); int index=(int32_t)M32(p+20);
            int count=(int32_t)M32(selectors);
            if(index>=count) index=count-1; else if(index<0) index=0;
            uint32_t row=selectors+4+(uint32_t)index*12;
            int8_t type=(int8_t)M8(row); M8(p+16)=(uint8_t)type;
            if(type==0) ++plays; else ++invalid_player_dispatch;
        }
        M8(p+13)=M8(p+12); M8(p+12)=(uint8_t)command;
    }
    ++calls; c->eax=1; c->esp+=4; /* cdecl callee removes return address only */
}
int main(int argc,char **argv) {
    if(argc!=5) return 2;
    FILE *f=fopen(argv[1],"rb"); if(!f) return 3;
    G=calloc(1,0x400000); if(!G) return 4;
    if(fread(G+0x10000,1,0x10000,f)==0) return 5; fclose(f);
    code_start=0x10000+(uint32_t)strtoul(argv[2],0,0);
    code_end=0x10000+(uint32_t)strtoul(argv[3],0,0);
    uint32_t template=0x10000+(uint32_t)strtoul(argv[4],0,0);
    memcpy(G+0x100000,G+template,1024);
    Cpu c={0}; c.esp=0x300000; c.esi=0x12345678;
    M32(c.esp)=0xffff; M32(c.esp+4)=0x100000+24;
    interp_run(&c,code_start);
    if(c.esp!=0x300004 || c.esi!=0x12345678 || calls!=1) return 6;
    printf("plays=%u invalid_player_dispatch=%u calls=%u stack=ok esi=ok\n",
           plays,invalid_player_dispatch,calls);
    free(G); return 0;
}
'''


def execute_fixture(data):
    compiler = shutil.which('clang')
    if not compiler:
        raise unittest.SkipTest('C compiler absent; loader execution needs compiler')
    with tempfile.TemporaryDirectory(prefix='loader-', dir=SCRATCH) as folder:
        folder = Path(folder)
        source = folder / 'loader.c'
        source.write_text(HARNESS)
        exe = folder / 'loader'
        command = [compiler, '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-I' + str(ROOT / 'runtime'),
                   '-I' + str(ROOT / 'recomp'), str(source), str(ROOT / 'runtime/interp.c'),
                   '-o', str(exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stderr[-4000:])
        image, bound = frozen_relocate(data)
        assert len(bound) == 1
        bank = folder / 'module.abk'
        bank.write_bytes(image)
        descriptor = abi._u32(data, 28)
        code, state = struct.unpack_from('<2I', data, descriptor + 40)
        ran = subprocess.run([str(exe), str(bank), str(code), str(state), str(state)],
                             capture_output=True, text=True, timeout=5)
        assert ran.returncode == 0, (ran.returncode, ran.stderr[-2000:])
        return ran.stdout.strip()


class Proof(unittest.TestCase):
    def test_original_loader_player_mismatch(self):
        data = fixture()
        self.assertEqual([s.frames for s in car_audio_codec.parse_bank(data)], [1, 1])
        before = data[:]
        actual = execute_fixture(data)
        self.assertEqual(actual, 'plays=0 invalid_player_dispatch=1 calls=1 stack=ok esi=ok')
        print(f'original {BASELINE}: source golden sample=1; MW null player dispatch=1; stack/ESI preserved')
        if '--original' in sys.argv:
            self.assertIn('plays=1 ', actual, 'original MW ordinal 27 misbinds source player state')
        report = abi.inspect_bank(data, resource(data))
        self.assertFalse(report['sound_admissible'])
        self.assertEqual(report['imports'][0]['classification'], abi.TRANSLATED)
        self.assertIn('A5-player', report['blockers'])
        with self.assertRaisesRegex(ValueError, 'sound admission blocked'):
            abi.require_sound_admission(data, resource(data))
        self.assertEqual(data, before)

    def test_manifest_exact_coverage(self):
        data = fixture()
        report = abi.inspect_bank(data, resource(data))
        self.assertEqual(len(report['imports']), 1)
        self.assertEqual(len(report['instructions']), 8)
        self.assertEqual(report['modules'][0]['member_words'], 100)
        self.assertEqual(report['modules'][0]['players'][0]['offset'], 512)
        self.assertEqual(report['interfaces'][0]['kind'], 1)
        self.assertEqual(report['interfaces'][0]['classification'], abi.TRANSLATED)
        self.assertEqual(report['pointers'][0]['target'], report['modules'][0]['state'] + 900)
        json.dumps(report)

    def test_import_contracts_and_unknown(self):
        expected = {0, 1, 4, 6, 7, 8, 10, 13, 15, 16, 17, 21, 22, 25, 27, 28, 29, 30, 31} | {35, 38}
        self.assertEqual(set(abi.ARITHMETIC) | set(abi.LIFECYCLE), expected)
        for ordinal in expected:
            row = abi.import_contract(ordinal)
            self.assertEqual(row['classification'], abi.TRANSLATED if ordinal in (4, 27) else abi.DIRECT)
            self.assertTrue(row['layout'])
        data = fixture(ordinal=999)
        self.assertEqual(abi.inspect_bank(data, resource(data))['imports'][0]['classification'], abi.UNSUPPORTED)
        self.assertFalse(abi.inspect_bank(data, resource(data))['sound_admissible'])

    def test_ramp_wire_offsets(self):
        row = abi.import_contract(29)
        self.assertEqual((row['source_pc'], row['target_pc']), (0x8985d6, 0x81a707))
        # Both decoded bodies load +18 first and compare +08 / +10:+0C.
        # Distinct words prevent the old cached-duration label passing as input.
        expected = {'old_target': 8, 'old_time': 12, 'time': 16, 'delta': 20, 'target': 24}
        fields = {name: int(offset) for name, offset in
                  re.findall(r'(\w+)@(\d+)', row['layout'])}
        for name, offset in expected.items():
            self.assertEqual(fields.get(name), offset, name)
        state = struct.pack('<2f5i', 1.5, 0.25, 91, 73, 101, 7, 997)
        self.assertEqual(struct.unpack_from('<i', state, fields['target'])[0], 997)
        self.assertEqual(struct.unpack_from('<i', state, fields['old_time'])[0], 73)

    def test_interface_resolver_and_layouts(self):
        target = [(name, 0x383f, profile[2]) for name, profile in abi.INTERFACES.items() if profile[2] is not None]
        resolved = 0
        for name, (sig, count, _, _, classification) in abi.INTERFACES.items():
            match = frozen_resolve(name, 0x311c, sig, target)
            resolved += match is not None
            self.assertEqual(match is not None, classification == abi.DIRECT)
            row = abi.interface_contract(name, 0x311c, sig, 1, count)
            self.assertEqual(row['classification'], classification)
            self.assertEqual(abi.interface_contract(name, 0x311c, sig ^ 1, 1, count)['classification'], abi.UNSUPPORTED)
            self.assertEqual(abi.interface_contract(name, 0x311c, sig, 1, count + 1)['classification'], abi.UNSUPPORTED)
        self.assertEqual(resolved, 4)
        print('CSIS: 4/6 signatures resolve through system fallback; CAR/CAR_ENG 2/6 unresolved; changed ID/count controls 12/12 rejected')

    def test_original_relocation_equation(self):
        data = fixture()
        image, bound = frozen_relocate(data)
        for site, target in bound:
            self.assertEqual((0x10000 + site + 4 + abi._u32(image, site)) & 0xffffffff, target)
        site = abi.inspect_bank(data, resource(data))['pointers'][0]['site']
        self.assertEqual(abi._u32(image, site), abi._u32(data, site) + 0x10000)

    def test_identity_changes_and_closure(self):
        data = fixture()
        expected = resource(data)
        altered = bytearray(data)
        altered[0xa0] = 0x90
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            abi.inspect_bank(bytes(altered), expected)
        graphs = [graph(expected), graph(copy.deepcopy(expected))]
        report = abi.closure_manifest(graphs, {expected['path']: data})
        self.assertEqual(len(report['banks']), 1)
        self.assertFalse(report['sound_admissible'])
        with self.assertRaisesRegex(ValueError, 'missing reachable'):
            abi.closure_manifest(graphs, {})
        graphs[1]['resources'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'conflicting closure'):
            abi.closure_manifest(graphs, {expected['path']: data})

    def test_a1_resource_path_normalization(self):
        data = fixture()
        for path in ('SOUND/ENGINE/fixture.abk', 'SOUND\\ENGINE\\fixture.abk'):
            with self.subTest(path=path):
                resources = {path: data}
                row = car_audio.Resources(resources).get('fixture.abk')
                report = abi.closure_manifest([graph(row)], resources)
                self.assertEqual(len(report['banks']), 1)
                self.assertFalse(report['sound_admissible'])

    def test_unknown_instruction_and_branch(self):
        data = bytearray(fixture())
        data[0xa0 + 11] = 0xf4  # hlt is NOT glue
        with self.assertRaisesRegex(ValueError, 'unsupported instruction'):
            abi.inspect_bank(bytes(data), resource(bytes(data)))
        data = bytearray(fixture())
        data[0xa0 + 11:0xa0 + 13] = b'\xeb\xfe'  # loop never reaches the import
        with self.assertRaisesRegex(ValueError, 'unreachable/unclassified'):
            abi.inspect_bank(bytes(data), resource(bytes(data)))
        data[0xa0 + 12] = 0x80  # outside code
        with self.assertRaisesRegex(ValueError, 'branch outside'):
            abi.inspect_bank(bytes(data), resource(bytes(data)))
        data = bytearray(fixture())
        data[0xa0 + 17:0xa0 + 20] = b'\x8b\x46\x08'
        with self.assertRaisesRegex(ValueError, 'private player'):
            abi.inspect_bank(bytes(data), resource(bytes(data)))

    def test_bounds_relocations_and_module_shapes(self):
        original = fixture()
        f1, f2, f3 = struct.unpack_from('<3I', original, 0x30)
        mutations = [(f1, 65537), (f1 + 4, 0xfffffffc), (f2 + 4, 0x60),
                     (f3 + 8, f3), (0x60 + 44, 0xffffffff), (0x60 + 52, 1020),
                     (0x60 + 60, 1020), (0xa0 + 7, 0x100000)]
        rejected = 0
        for at, value in mutations:
            data = bytearray(original)
            put(data, at, value)
            with self.assertRaises(ValueError):
                abi.inspect_bank(bytes(data), resource(bytes(data)))
            rejected += 1
        self.assertEqual(rejected, 8)


def pe_table(path, address, ordinals, digest):
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, 'unreviewed executable identity'
    pe = abi._u32(data, 60)
    base = abi._u32(data, pe + 52)
    sections = struct.unpack_from('<H', data, pe + 6)[0]
    table = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
    for i in range(sections):
        _, rva, size, offset = struct.unpack_from('<4I', data, table + 40 * i + 8)
        if rva <= address - base and address - base + 4 * (max(ordinals) + 1) <= rva + size:
            return {o: abi._u32(data, offset + address - base - rva + o * 4) for o in ordinals}
    raise AssertionError('table outside executable sections')


def target_csis(folder):
    """Independent native-bank reader for MW engine signature/member goldens."""
    identities = set()
    exports = set()
    for path in sorted(folder.glob('*.abk')):
        data = path.read_bytes()
        if data[:4] != b'ABKC':
            continue
        word = lambda at: struct.unpack_from('<I', data, at)[0]
        f3 = word(0x38)
        if not 0 < f3 <= len(data) - 4:
            continue  # console variants are outside the little endian proof
        handles = {}
        for j in range(word(f3)):
            site, name, kind = struct.unpack_from('<3I', data, f3 + 4 + 12 * j)
            system, signature = struct.unpack_from('<2H', data, name)
            text = data[name + 4:data.index(0, name + 4)].decode('ascii')
            if kind & 255 == 1:
                identities.add((text, system, signature))
                handles[site] = (text, system, signature)
        at = word(0x1c)
        for j in range(struct.unpack_from('<H', data, 10)[0]):
            state = word(at + 44)
            globals_ = struct.unpack_from('<H', data, at + 32)[0]
            member_at = 24 + 20 * data[at + 37] + 28 * globals_
            count = data[state + member_at + 16] if data[at + 38] else 0
            exports.add((*handles[at + 4], count))
            at += 60 + 4 * (data[at + 36] + data[at + 39])
    return identities, exports


def private_proof():
    source = Path(os.environ.get('NFS_CARBON_PC', Path.home() / 'Downloads/NFSC'))
    target = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    roster = ROOT / 'tools_snapshot/private_cars.py'
    if not (source / 'GLOBAL/attributes.bin').is_file() or not roster.is_file():
        print('SKIP private source inputs absent; synthetic loader proof completed')
        return False
    cars = re.findall(r'\|C/([^|\n]+)\|', roster.read_text())
    resources = {p.relative_to(source).as_posix(): p.read_bytes() for p in (source / 'SOUND/ENGINE').rglob('*')
                 if p.suffix.lower() in ('.abk', '.gin')}
    graphs = car_audio.inventory((source / 'GLOBAL/attributes.bin').read_bytes(), cars, resources)
    assert not graphs['unsupported']
    report = abi.closure_manifest(graphs['supported'].values(), resources)
    banks = report['banks']
    imports = [i for b in banks.values() for i in b['imports']]
    interfaces = [i for b in banks.values() for i in b['interfaces']]
    instructions = [i for b in banks.values() for i in b['instructions']]
    modules = [i for b in banks.values() for i in b['modules']]
    ordinals = {i['ordinal'] for i in imports}
    assert ordinals == set(abi.ARITHMETIC) | set(abi.LIFECYCLE)
    assert all(not b['sound_admissible'] and b['blockers'] for b in banks.values())
    assert not [i for i in imports + interfaces if i['classification'] == abi.UNSUPPORTED and i.get('name') != 'CAR_ENG']
    players = [p for m in modules for p in m['players']]
    assert all(p['initial_handle'] == 0 and p['source_type_byte'] == 255 and
               p['classification'] == abi.TRANSLATED and p['position'] == p['extra'] == 0 for p in players)
    assert all(m['player_private_accesses'] == 0 for m in modules)
    if (target / 'SOUND/ENGINE').is_dir():
        identities, exports = target_csis(target / 'SOUND/ENGINE')
        for name, (_, _, sig, count, classification) in abi.INTERFACES.items():
            if name == 'CAR_ENG':
                assert not [i for i in identities if i[0] == name]
            else:
                assert (name, 0x383f, sig) in identities
                if name != 'CAR_SputOutput':
                    assert (name, 0x383f, sig, count) in exports
        print('independent target CSIS: 5/5 identities, 4/4 exported member counts; CAR_ENG absent')
    # Independent decoder oracle: every reached instruction's length/mnemonic,
    # including all ModRM/SIB operands, is checked against Capstone when present.
    try:
        import capstone
    except ImportError:
        print('SKIP independent instruction oracle (Capstone absent)')
    else:
        decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        for bank in banks.values():
            for i in bank['instructions']:
                decoded = list(decoder.disasm(bytes.fromhex(i['bytes']), i['offset']))
                assert len(decoded) == 1 and decoded[0].size == i['size']
                assert decoded[0].mnemonic == ('call' if i['mnemonic'] == 'call_import' else i['mnemonic'])
        print(f'independent instruction oracle: {len(instructions)}/{len(instructions)} boundaries and opcodes match')
    if (source / 'NFSC.exe').is_file() and (target / 'speed.exe').is_file():
        source_table = pe_table(source / 'NFSC.exe', 0xa7d6c0, ordinals, SOURCE_EXE_SHA)
        target_table = pe_table(target / 'speed.exe', 0x90bc40, ordinals, TARGET_EXE_SHA)
        for i in report['imports']:
            assert source_table[i['ordinal']] == i['source_pc']
            assert target_table[i['ordinal']] == i['target_pc']
        assert pe_table(target / 'speed.exe', 0x90bbf0, {0, 1, 2, 3}, TARGET_EXE_SHA) == {0: 0x81a330, 1: 0, 2: 0, 3: 0}
        print(f'pinned executable import vectors: {len(ordinals)}/{len(ordinals)} entries on both sides')
    counts = {'cars': len(cars), 'banks': len(banks), 'modules': len(modules),
              'import_sites': len(imports), 'ordinals': len(ordinals),
              'interfaces': len(interfaces), 'interface_names': len({i['name'] for i in interfaces}),
              'instructions': len(instructions), 'players': sum(len(m['players']) for m in modules),
              'direct_imports': sum(i['classification'] == abi.DIRECT for i in imports),
              'translated_imports': sum(i['classification'] == abi.TRANSLATED for i in imports)}
    counts['selectors'] = sum(len(b['selectors']) for b in banks.values())
    counts['selector_rows'] = sum(len(s['indices']) for b in banks.values() for s in b['selectors'])
    counts['player_private_accesses'] = sum(m['player_private_accesses'] for m in modules)
    (SCRATCH / 'manifest.json').write_text(json.dumps(report, sort_keys=True, separators=(',', ':')))
    (SCRATCH / 'counts.json').write_text(json.dumps(counts, indent=2))
    print('private closure:', json.dumps(counts, sort_keys=True))
    print('sound admission: 0 accepted; every bank remains blocked for unresolved executable contracts')
    return True


def install_mutant(name):
    if name == 'ramp':
        row = abi.ARITHMETIC[29]
        abi.ARITHMETIC[29] = (*row[:3],
            'f32 value@0,slope@4; i32 old_target@8,target@12,time@16,enable@20')
    elif name == 'import':
        original = abi.import_contract
        def broken(ordinal):
            row = original(ordinal)
            if ordinal == 27:
                row['classification'] = abi.DIRECT
            return row
        abi.import_contract = broken
    elif name == 'csis':
        original = abi.interface_contract
        def broken(name, system, signature, kind, member_count=None):
            if name in abi.INTERFACES:
                signature, member_count = abi.INTERFACES[name][:2]
            return original(name, system, signature, kind, member_count)
        abi.interface_contract = broken
    elif name == 'instruction':
        original = abi._instruction
        def broken(data, pc, end):
            if data[pc] == 0xf4:
                return {'offset': pc, 'size': 1, 'bytes': 'f4', 'mnemonic': 'nop',
                        'branch': None, 'classification': abi.DIRECT,
                        'instance_memory': None, 'esi_delta': 0}
            return original(data, pc, end)
        abi._instruction = broken
    else:
        raise ValueError('unknown mutant')


if __name__ == '__main__':
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if '--mutant' in sys.argv:
        install_mutant(sys.argv[sys.argv.index('--mutant') + 1])
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Proof))
    if not result.wasSuccessful():
        sys.exit(1)
    sys.exit(0 if private_proof() else 3)
