"""Bounded, dormant compatibility inventory for source PC audio modules.

This is evidence for adapters, not an executable bank converter. In particular,
structurally decoded instructions do not certify arbitrary native programs.
No source bytecode, bank allowlist or runtime admission bypass is embedded.
"""
from __future__ import annotations

import hashlib
import json
import struct
from bisect import bisect_right

from .car_audio import Resources
from .car_audio_bank import source_selectors

VERSION = 1
MAX_BYTES = 256 * 1024 * 1024
MAX_INSTRUCTIONS = 262144
DIRECT = 'direct_match'
TRANSLATED = 'translated_abi'
UNSUPPORTED = 'unsupported'


def _need(condition, reason):
    if not condition:
        raise ValueError('audio ABI: ' + reason)


def _span(data, offset, size, end=None):
    end = len(data) if end is None else end
    _need(0 <= offset <= offset + size <= end <= len(data), 'range outside extent')
    return data[offset:offset + size]


def _words(data, offset, fmt, end=None):
    return struct.unpack('<' + fmt, _span(data, offset, struct.calcsize('<' + fmt), end))


def _u32(data, offset, end=None):
    return _words(data, offset, 'I', end)[0]


def _digest(data):
    return hashlib.sha256(data).hexdigest()


# Each entry records both implementations and the wire state actually read.
# Offsets are bytes; i32/u16/u8 are little endian, p32 is a guest pointer.
# All these entry points are cdecl(state*), returning a 32-bit value in EAX.
# Matching wire arithmetic does not promise the same random sequence/tick time.
ARITHMETIC = {
    6: (0x897e88, 0x8199d1, 'counter', 'i32 min@0,max@4,current@8; i8 step@12; i32 gate@16,input@20'),
    7: (0x897eca, 0x819a0f, 'random', 'i32 base@0; u32 range@4; i32 held@8,gate@12'),
    8: (0x897ef0, 0x819a32, 'shuffle', 'u16 gate_offset@0; u8 width@2,exclude@3; i32 base@4; u16 next@8,count@10; i32 held@12; values@16'),
    10: (0x897fda, 0x819b13, 'range_trigger', 'i32 lo@0,hi@4,reset_lo@8,reset_hi@12; u8 latch@16,pulse@17; i32 input@20'),
    13: (0x898090, 0x819bbe, 'merge', 'u8 count@0; i32 inputs@4[count]'),
    15: (0x89819f, 0x819cc6, 'table', 'p32 table@0; i32 old@4,held@8,input@12; table: u8 width@0,u16 count@2,i32 lo@4,hi@8,f32 scale@12,values@16'),
    16: (0x89829c, 0x819dc6, 'delay_line', 'u16 input_offset@0,count@2,write@4,read@6; i32 old_delay@8; i32 ring@12[count]'),
    17: (0x89832d, 0x819e57, 'mux', 'u8 count@0; i32 index@4; i32 values@8[count]; index 1..count else zero'),
    21: (0x8983f4, 0x819f0b, 'scale', 'u8 count@0; f32 scale@4; i32 values@8[count]; rounded float product'),
    22: (0x898440, 0x819f57, 'add', 'u8 count@0; i32 values@4[count]; wrapping sum'),
    25: (0x898487, 0x819f92, 'divide', 'i32 numerator@0,denominator@4; truncation toward zero, zero denominator returns zero'),
    28: (0x8984ec, 0x81a61d, 'oscillator', 'u8 waveform@0; f32 phase@4; i32 period@8,amplitude@12; i32 table[]@16 for waveform 4'),
    29: (0x8985d6, 0x81a707, 'ramp', 'f32 value@0,slope@4; i32 old_target@8,old_time@12,time@16,delta@20,target@24'),
    30: (0x898675, 0x81a7a6, 'add_max', 'u8 count@0; i32 maximum@4; i32 values@8[count]; min(sum,maximum)'),
    31: (0x89869f, 0x81a7cd, 'subtract_min', 'i32 minimum@0,left@4,right@8; max(left-right,minimum)'),
    35: (0x898423, 0x819f3a, 'scale2', 'f32 scale@0; i32 left@4,right@8; rounded float product'),
}
LIFECYCLE = {
    0: (0x897ba8, 0x8196c2, 'class_destructor', 'u32 pulse@16; return pulse and clear it'),
    1: (0x897bb7, 0x8196ce, 'class_data', 'u32 value@20; return value'),
    4: (0x897bda, 0x8196e8, 'destroy', 'p32 module@0,instance@4; i32 requested@12; player offsets from module@60'),
    27: (0x898e58, 0x81a52c, 'player', 'p32 bank@0,selectors@4,handle@8; u8 current@12,previous@13,options@14,position@15,type@16,extra@17; i32 index@20,command@24; options@28 stride12'),
    38: (0x897d56, 0x819875, 'control_class', 'handle@0:8; p32 instance@8; u8 clamp@12,count@13; bounds@16 stride8; then i32 create,destroy,members[]'),
}


def import_contract(ordinal):
    item = ARITHMETIC.get(ordinal) or LIFECYCLE.get(ordinal)
    if item is None:
        return {'ordinal': ordinal, 'classification': UNSUPPORTED,
                'implemented': False, 'reason': 'ordinal has no decoded source/target contract'}
    source, target, name, layout = item
    adapter = {4: 'A5-player-destruction', 27: 'A5-player'}.get(ordinal)
    return {'ordinal': ordinal, 'source_pc': source, 'target_pc': target,
            'name': name, 'arguments': 'cdecl: state pointer at esp+4',
            'result': 'EAX u32/i32; caller removes argument', 'layout': layout,
            'classification': TRANSLATED if adapter else DIRECT,
            'adapter': adapter, 'implemented': adapter is None}


# Interface IDs are (preferred system, signature), followed by a bounded name.
# System preference has a fallback search; signature and name both must match.
INTERFACES = {
    'CAR': (0x0dd6, 100, 0x198b, 26, TRANSLATED),
    'CAR_ENG': (0x76ef, 100, None, None, UNSUPPORTED),
    'CAR_SWTN': (0x7859, 7, 0x7859, 7, DIRECT),
    'CAR_WHINE': (0x603f, 9, 0x603f, 9, DIRECT),
    'CAR_Sputter': (0x3686, 11, 0x3686, 11, DIRECT),
    'CAR_SputOutput': (0x1901, 1, 0x1901, 1, DIRECT),
}


def interface_contract(name, system, signature, kind, member_count=None):
    profile = INTERFACES.get(name)
    if (profile is None or system != 0x311c or kind != 1 or
            signature != profile[0] or member_count not in (None, profile[1])):
        return {'name': name, 'classification': UNSUPPORTED,
                'implemented': False, 'adapter': None,
                'reason': 'unreviewed interface identity/kind/member layout'}
    _, count, target, target_count, classification = profile
    return {'name': name, 'classification': classification, 'kind': kind,
            'source_system': system, 'source_signature': signature,
            'source_member_words': count, 'target_system': 0x383f,
            'target_signature': target, 'target_member_words': target_count,
            'member_layout': 'packed little endian i32 words, member data excludes class handle',
            'adapter': 'A6-engine-class' if name in ('CAR', 'CAR_ENG') else None,
            'implemented': classification == DIRECT}


def _instruction(data, pc, end):
    """Decode only the audited glue subset; unsupported forms fail closed.

    ModRM/SIB lengths are decoded independently of disassembler packages.
    No prefixes, floating point, indirect control flow or privileged operations
    are accepted. The manifest includes raw bytes and every relative branch.
    """
    op = _span(data, pc, 1, end)[0]
    cursor = pc + 1
    branch = None
    modrm = None
    mnemonic = None
    memory = None
    esi_delta = 0
    if op in (0x56, 0x5e, 0xc3, 0x90):
        mnemonic = {0x56: 'push', 0x5e: 'pop', 0xc3: 'ret', 0x90: 'nop'}[op]
    elif op == 0xe8:
        cursor += 4
        mnemonic = 'call_import'
    elif op in (0x7c, 0x7f, 0x74, 0x75, 0xeb):
        displacement = _words(data, cursor, 'b', end)[0]
        cursor += 1
        branch = cursor + displacement
        mnemonic = {0x7c: 'jl', 0x7f: 'jg', 0x74: 'je', 0x75: 'jne', 0xeb: 'jmp'}[op]
    elif op in (0x89, 0x8b, 0x03, 0x2b, 0x3b, 0x81, 0x83, 0xc6, 0x0f):
        if op == 0x0f:
            _need(_span(data, cursor, 1, end) == b'\xaf', 'unsupported extended instruction')
            cursor += 1
        modrm = _span(data, cursor, 1, end)[0]
        cursor += 1
        mod, group, rm = modrm >> 6, modrm >> 3 & 7, modrm & 7
        writes_esi = ((op == 0x89 and mod == 3 and rm == 6) or
                      (op in (0x8b, 0x03, 0x2b, 0x0f) and group == 6))
        _need(not writes_esi or _span(data, pc, 4, end) == b'\x8b\x74\x24\x08',
              'unreviewed instance cursor assignment')
        base = rm
        displacement = 0
        if mod != 3:
            if rm == 4:
                sib = _span(data, cursor, 1, end)[0]
                cursor += 1
                base = sib & 7
                _need(base != 6 or sib >> 3 & 7 == 4, 'indexed instance access not reviewed')
                if mod == 0 and sib & 7 == 5:
                    cursor += 4
            elif mod == 0 and rm == 5:
                cursor += 4
            _need(base in (4, 6), 'unreviewed memory base')
            _need(base != 4 or _span(data, pc, 4, end) == b'\x8b\x74\x24\x08',
                  'unreviewed stack memory access')
            if mod in (1, 2):
                displacement = _words(data, cursor, 'b' if mod == 1 else 'i', end)[0]
            cursor += 1 if mod == 1 else 4 if mod == 2 else 0
            if base == 6:
                memory = {'displacement': displacement, 'size': 1 if op == 0xc6 else 4,
                          'access': 'write' if op in (0x89, 0xc6) else 'read'}
        if op in (0x81, 0x83):
            _need(group == 0, 'unreviewed arithmetic group')
            if modrm == 0xc6:
                esi_delta = _words(data, cursor, 'i' if op == 0x81 else 'b', end)[0]
            cursor += 4 if op == 0x81 else 1
        elif op == 0xc6:
            _need(group == 0, 'invalid byte move group')
            cursor += 1
        mnemonic = {0x89: 'mov', 0x8b: 'mov', 0x03: 'add', 0x2b: 'sub',
                    0x3b: 'cmp', 0x81: 'add', 0x83: 'add', 0xc6: 'mov', 0x0f: 'imul'}[op]
    else:
        raise ValueError(f'audio ABI: unsupported instruction at {pc:#x}: {op:02x}')
    raw = _span(data, pc, cursor - pc, end)
    return {'offset': pc, 'size': len(raw), 'bytes': raw.hex(),
            'mnemonic': mnemonic, 'branch': branch, 'classification': DIRECT,
            'instance_memory': memory, 'esi_delta': esi_delta}


def _table(data, offset, end, stride):
    count = _u32(data, offset, end)
    _need(count <= 65536, 'relocation count limit')
    _span(data, offset + 4, count * stride, end)
    return [offset + 4 + i * stride for i in range(count)]


def inspect_bank(data, expected):
    """Produce a JSON manifest, verifying the exact A1 resource identity first.

    `expected` is the A1 bank resource row, not an ordinal/interface allowlist.
    Invalid structure/unknown instructions raise ValueError. Unknown imports or
    interfaces are enumerated as unsupported. Every such result blocks sound.
    Bank bytes are never changed. This API does not assemble or load a bank.
    """
    _need(isinstance(data, bytes) and 0x60 <= len(data) <= MAX_BYTES, 'bank size/type')
    sha = _digest(data)
    _need(expected.get('kind') == 'abk' and expected.get('size') == len(data) and
          expected.get('sha256') == sha, 'bank identity differs from graph closure')
    _need(data[:4] == b'ABKC' and _u32(data, 0x14) == len(data), 'ABKC header/size')
    modules = _words(data, 10, 'H')[0]
    table, bank, extent = _words(data, 0x1c, '3I')
    f1, f2, f3 = _words(data, 0x30, '3I')
    _need(0 < modules <= 1024 and 0x5c <= table < bank and
          bank == _u32(data, 0x18) and bank + extent == f1 and
          f1 < f2 < f3 <= len(data) - 4, 'bank/module/fixup extents')
    region = data[table:bank]
    _need(expected.get('module_count') == modules and
          expected.get('module_region_offset') == table and
          expected.get('module_region_size') == len(region) and
          expected.get('module_region_sha256') == _digest(region), 'module identity differs from graph')
    _need(data[bank:bank + 4] == b'S10A', 'unsupported source sample bank')
    imports, pointers, csis = [], [], []
    used = set()
    for row in _table(data, f1, f2, 4):
        site = _u32(data, row)
        ordinal = _u32(data, site, bank)
        _need(table <= site < bank - 3 and site not in used, 'duplicate/outside import site')
        used.add(site)
        imports.append({'site': site, **import_contract(ordinal)})
    for row in _table(data, f2, f3, 4):
        site = _u32(data, row)
        _need(table <= site <= bank - 4 and site not in used, 'overlapping/outside pointer site')
        used.add(site)
        target = _u32(data, site, bank)
        _need(table <= target < bank, 'pointer target outside module region')
        pointers.append({'site': site, 'target': target, 'classification': DIRECT})
    rows = _table(data, f3, len(data), 12)
    name_start = f3 + 4 + len(rows) * 12
    for row in rows:
        site, name, kind = _words(data, row, '3I')
        _need(table <= site <= bank - 8 and site not in used, 'overlapping/outside CSIS site')
        used.add(site)
        _need(name_start <= name <= len(data) - 5, 'interface name outside tail')
        system, signature = _words(data, name, '2H')
        last = data.find(b'\0', name + 4, min(name + 260, len(data)))
        _need(last > name + 4, 'unterminated/empty interface name')
        try:
            text = data[name + 4:last].decode('ascii')
        except UnicodeDecodeError as exc:
            raise ValueError('audio ABI: interface name is not ASCII') from exc
        csis.append({'site': site, 'name_offset': name,
                     **interface_contract(text, system, signature, kind & 255),
                     'source_system': system, 'source_signature': signature, 'kind': kind & 255})
    relocations = sorted([(i['site'], 4) for i in imports + pointers] + [(c['site'], 8) for c in csis])
    for left, right in zip(relocations, relocations[1:]):
        _need(left[0] + left[1] <= right[0], 'overlapping relocation words')
    definitions = []
    cursor = table
    for index in range(modules):
        _span(data, cursor, 60, bank)
        players, destructor, members, classes = data[cursor + 36:cursor + 40]
        _need(destructor in (0, 1) and members in (0, 1), 'module state flags')
        n = players + classes
        _span(data, cursor + 60, n * 4, bank)
        start, state, size, destroy = _words(data, cursor + 40, '4I')
        _need(cursor + 60 + n * 4 <= start < state <= bank and 24 <= size <= bank - state,
              'code/template extents')
        _need(0 <= destroy <= size - 16, 'destroy state outside template')
        globals_, functions = _words(data, cursor + 32, '2H')
        member_at = 24 + 20 * destructor + 28 * globals_
        member_count = 0
        if members:
            _span(data, state + member_at, 20, state + size)
            member_count = data[state + member_at + 16]
            _span(data, state + member_at + 20, member_count * 4, state + size)
        player_states = []
        offsets = _words(data, cursor + 60, str(n) + 'I') if n else ()
        _need(len(set(offsets)) == len(offsets), 'duplicate player/class state offset')
        for off in offsets[:players]:
            at = state + off
            _span(data, at, 28, state + size)
            options, position, extra = data[at + 14], data[at + 15], data[at + 17]
            _need(position in (0, 1) and extra in (0, 1), 'player output flags')
            count = 28 + options * 12 + position * 8 + extra * 32
            _span(data, at, count, state + size)
            player_states.append({'offset': off, 'size': count, 'options': options,
                                  'position': position, 'extra': extra,
                                  'initial_handle': _u32(data, at + 8),
                                  'source_type_byte': data[at + 16]})
            player_states[-1]['option_records'] = [
                {'code': data[at + 28 + i * 12],
                 'previous': _words(data, at + 32 + i * 12, 'i')[0],
                 'value': _words(data, at + 36 + i * 12, 'i')[0]}
                for i in range(options)]
            player_states[-1]['selector_offset'] = _u32(data, at + 4)
            profile_ok = (position == extra == 0 and _u32(data, at + 8) == 0 and data[at + 16] == 255 and
                          all(o['code'] in (0, 2, 3, 5, 6, 8) for o in player_states[-1]['option_records']))
            player_states[-1]['classification'] = TRANSLATED if profile_ok else UNSUPPORTED
        for off in offsets[players:]:
            _span(data, state + off, 16, state + size)
        definition = next((c for c in csis if c['site'] == cursor + 4), None)
        _need(definition is not None, 'module without exported CSIS class')
        contract = interface_contract(definition['name'], definition['source_system'],
                                      definition['source_signature'], definition['kind'], member_count)
        definition.update(contract)
        definitions.append({'index': index, 'descriptor': cursor, 'entry': start,
                            'code_bound': state, 'state': state, 'state_size': size,
                            'destroy_offset': destroy, 'member_offset': member_at + 20,
                            'member_words': member_count, 'players': player_states,
                            'class_offsets': list(offsets[players:]),
                            'global_count': globals_, 'function_count': functions,
                            'interface': definition['name']})
        cursor += 60 + 4 * n
    _need(cursor <= min(d['entry'] for d in definitions), 'descriptor/code overlap')
    selector_offsets = sorted({p['selector_offset'] for d in definitions for p in d['players']})
    sounds = [None if s.get('empty') else s for s in expected['sounds']]
    selectors = source_selectors(data, sounds, table_offsets=selector_offsets)
    by_site = {i['site']: i for i in imports}
    instructions = {}
    reached_imports = set()
    for definition in definitions:
        _need(_span(data, definition['entry'], 5) == b'\x56\x8b\x74\x24\x08',
              'unreviewed module entry convention')
        # The loader callback context is instance+24, not the instance base.
        todo = [(definition['entry'], 24)]
        local_seen = {}
        while todo:
            pc, esi = todo.pop()
            if pc in local_seen:
                _need(local_seen[pc] == esi, 'path-dependent instance cursor')
                continue
            _need(definition['entry'] <= pc < definition['code_bound'], 'branch outside module code')
            _need(len(instructions) < MAX_INSTRUCTIONS, 'instruction count limit')
            ins = _instruction(data, pc, definition['code_bound'])
            _need(ins['bytes'] != '5e' or _span(data, pc + 1, 1) == b'\xc3',
                  'instance cursor restored before function end')
            _need(ins['bytes'] != '8b742408' or pc == definition['entry'] + 1,
                  'instance cursor reloaded inside function')
            local_seen[pc] = esi
            instructions[pc] = ins
            memory = ins['instance_memory']
            if memory:
                at = esi + memory['displacement']
                _need(0 <= at <= definition['state_size'] - memory['size'], 'instruction state access outside template')
                for player in definition['players']:
                    _need(at + memory['size'] <= player['offset'] + 8 or at >= player['offset'] + 20,
                          'program accesses private player handle/control fields')
            if ins['mnemonic'] == 'call_import':
                site = pc + 1
                _need(site in by_site, 'call without classified import relocation')
                ins['ordinal'] = by_site[site]['ordinal']
                _need(0 <= esi < definition['state_size'], 'import state argument outside template')
                by_site[site]['instance_offset'] = esi
                if ins['ordinal'] == 27:
                    _need(esi in {p['offset'] for p in definition['players']}, 'player import argument not in descriptor')
                reached_imports.add(site)
            if ins['mnemonic'] == 'ret':
                continue
            if ins['branch'] is not None:
                todo.append((ins['branch'], esi + ins['esi_delta']))
                if ins['mnemonic'] == 'jmp':
                    continue
            todo.append((pc + ins['size'], esi + ins['esi_delta']))
        definition['instruction_count'] = len(local_seen)
        definition['player_private_accesses'] = 0
    _need(reached_imports == set(by_site), 'unreachable/unclassified import sites')
    ordered = sorted(instructions.values(), key=lambda x: x['offset'])
    for left, right in zip(ordered, ordered[1:]):
        _need(left['offset'] + left['size'] <= right['offset'], 'overlapping instruction boundaries')
    code_start = min(d['entry'] for d in definitions)
    code_end = min(d['state'] for d in definitions)
    covered = bytearray(code_end - code_start)
    for ins in ordered:
        _need(code_start <= ins['offset'] <= code_end - ins['size'], 'instruction overlaps state template')
        at = ins['offset'] - code_start
        covered[at:at + ins['size']] = b'\x01' * ins['size']
    _need(all(flag or value == 0 for flag, value in zip(covered, data[code_start:code_end])),
          'unclassified nonzero bytes in code region')
    starts = [i['offset'] for i in ordered]
    for pointer in pointers:
        index = bisect_right(starts, pointer['site'] + 3) - 1
        _need(index < 0 or ordered[index]['offset'] + ordered[index]['size'] <= pointer['site'],
              'pointer relocation changes an instruction')
    blockers = sorted({(i.get('adapter') or i.get('reason') or 'unresolved ABI')
                       for i in imports + csis if i['classification'] != DIRECT})
    if any(p['classification'] == UNSUPPORTED for d in definitions for p in d['players']):
        blockers.append('unreviewed player options/output/initial-state profile')
    # No bank content is admitted merely because it has familiar instructions.
    # This evidence-only version has no adapter implementation/certification API.
    blockers.append('native program semantics and adapters require separate admission proof')
    return {'format': 'source-audio-abi', 'version': VERSION, 'sha256': sha,
            'size': len(data), 'module_region_sha256': _digest(region),
            'imports': imports, 'interfaces': csis, 'pointers': pointers,
            'modules': definitions, 'instructions': ordered,
            'selectors': [{'offset': s.source_offset, 'pointer_sites': list(s.pointer_sites),
                           'indices': list(s.indices), 'source_rows': [list(r) for r in s.source_rows],
                           'classification': TRANSLATED} for s in selectors],
            'code_padding_bytes': covered.count(0),
            'sound_admissible': False, 'blockers': blockers}


def require_sound_admission(data, expected):
    """Always reject until executable adaptation proofs exist, after validation."""
    report = inspect_bank(data, expected)
    raise ValueError('audio ABI: sound admission blocked: ' + '; '.join(report['blockers']))


def closure_manifest(graphs, resources):
    """Union ALL A1-reachable bank identities; GIN is a separate synthesis ABI."""
    resources = dict(Resources(resources).files.values())
    banks = {}
    for graph in graphs:
        _need(graph.get('schema') == 'source-car-audio' and graph.get('version') == 1 and
              graph['source']['game'] == 'carbon' and graph['source']['platform'] == 'pc',
              'unsupported graph source/version')
        for row in graph['resources']:
            if row['kind'] != 'abk':
                _need(row['kind'] == 'gin', 'unknown graph resource kind')
                continue
            path = row['path']
            _need(path in resources, 'missing reachable bank: ' + path)
            if path in banks:
                _need(banks[path]['sha256'] == row['sha256'], 'conflicting closure identity')
            else:
                banks[path] = inspect_bank(resources[path], row)
    _need(bool(banks), 'empty bank closure')
    ordinals = sorted({i['ordinal'] for b in banks.values() for i in b['imports']})
    return {'format': 'source-audio-abi-closure', 'version': VERSION,
            'banks': banks, 'imports': [import_contract(i) for i in ordinals],
            'sound_admissible': False,
            'manifest_sha256': _digest(json.dumps(banks, sort_keys=True, separators=(',', ':')).encode())}
