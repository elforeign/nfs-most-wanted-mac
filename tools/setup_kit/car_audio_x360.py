"""Translate Carbon Xenon's interpreted AEMS modules to the checked MW guest ABI."""
import math
import re
import struct

from .car_convert import require, span, words

# Opcode handlers in the Xenon interpreter and their equivalent MW imports.
# The inline operators below have no import in the Windows AEMS compiler.
IMPORTS = {op: op for op in (0, 1, 3, 4, 7, 10, 11, 12, 13, 15, 17, 21, 25, 27, 28, 29, 30, 31, 35)}
BINARY = {
    23: b'\x2b\xc1',                    # subtract
    24: b'\x0f\xaf\xc1',                # multiply low word
    33: b'\x3b\xc1\x7c\x02\x89\xc8', # signed minimum
    34: b'\x3b\xc1\x7f\x02\x89\xc8', # signed maximum
    36: b'\x03\xc1',                    # add
}


def _uint(data, at):
    return words(data, at, '>I')[0]


def _pack(*values):
    return struct.pack(f'<{len(values)}I', *values)


def _bounded_words(data, at, fmt, end):
    require(at + struct.calcsize(fmt) <= end, 'Xenon field outside its record')
    return words(data, at, fmt)


def validate_samples(data, sounds, bank, end):
    """Check resident EA-wrapped XMA1 framing within each declared sound."""
    live = [s for s in sounds if not s.get('empty')]
    for i, sound in enumerate(live):
        at = bank + sound['offset'] + (8 if sound['loop_start'] is None else 12)
        stop = bank + live[i + 1]['offset'] if i + 1 < len(live) else end
        remaining = sound['frames']
        require(0 < remaining <= 10_000_000, 'Xenon sample frame limit')
        while remaining:
            size, frames, inner = _bounded_words(data, at, '>3I', stop)
            require(16 <= size <= stop - at and 0 < frames <= remaining and inner >> 2 == size - 8,
                    'Xenon XMA block extent or frame count')
            for pos in range(at + 12, at + size, 2048):
                require(pos + 4 <= at + size and data[pos] & 0xfc == 0x08 and data[pos + 3] == 0,
                        'Carbon Xenon mono XMA packet header')
            remaining -= frames
            at += size
        require(at == stop or (i + 1 == len(live) and stop - at <= 3 and not any(data[at:stop])),
                'Xenon XMA sound extent')


class _Module:
    def __init__(self, data, state, players, destroy, sounds, pointers, module_end, member_words=100,
                 selector_modes=(0x6400,)):
        self.data = data
        self.state = state
        self.template = bytearray(len(state))
        self.types = {}
        self.players = players
        self.seen_players = set()
        self.destroy = destroy
        self.sounds = sounds
        self.pointers = set(pointers)
        self.pointer_targets = {}
        self.tables = {}
        self.module_end = module_end
        self.member_words = member_words
        self.selector_modes = selector_modes
        self.code = bytearray(b'\x56\x8b\x74\x24\x08')  # ESI = state + 24
        self.calls = []
        self.links = []
        self.operations = []
        self._copy(0, '6I')

    def _copy(self, at, fmt):
        size = struct.calcsize('>' + fmt)
        span(self.state, at, size, 'Xenon module state extent')
        # Each requested group starts on a word boundary; remember its typed
        # layout so links can address numeric state only.
        require(at % 4 == size % 4 == 0, 'Xenon unaligned state')
        for repeat, item in re.findall(r'(\d*)([BHI])', fmt):
            count = int(repeat or '1')
            width = struct.calcsize('>' + item)
            for _ in range(count):
                previous = self.types.setdefault(at, item)
                require(previous == item, 'Xenon state used with conflicting types')
                value = words(self.state, at, '>' + item)[0]
                struct.pack_into('<' + item, self.template, at, value)
                at += width

    def _pointer(self, at, selector=False):
        target = _uint(self.state, at)
        require(at in self.pointers and target < self.module_end, 'Xenon undeclared pointer')
        previous = self.pointer_targets.setdefault(at, target)
        require(previous == target, 'Xenon inconsistent pointer')
        if target not in self.tables:
            if selector:
                count = _uint(self.data, target)
                require(0 <= count <= 256, 'Xenon selector count')
                rows = span(self.data, target + 4, count * 12, 'Xenon selector extent')
                out = bytearray(_pack(count))
                for first, slot, flags in struct.iter_unpack('>3I', rows):
                    sample, mode = first >> 16, first & 65535
                    if (first, slot, flags) == (0xffff0000, 0, 0):
                        out += _pack(65535, 0, 0)
                    else:
                        require(mode in self.selector_modes and slot == sample + 1 and flags == 0xffffffff
                                and sample < len(self.sounds) and not self.sounds[sample].get('empty'),
                                'Xenon selector sample identity')
                        out += _pack((mode << 8) | sample, slot, flags)
                value = bytes(out)
            else:
                width, reserved, count, lo, hi, scale = words(self.data, target, '>BBHiif')
                require(width in (1, 2) and reserved == 0 and 1 <= count <= 4096 and
                        lo <= hi and math.isfinite(scale) and 0 <= scale and
                        (scale * (hi - lo) < count if scale else hi - lo < count), 'Xenon lookup table shape')
                fmt = f'{count}' + ('b' if width == 1 else 'h')
                values = words(self.data, target + 16, '>' + fmt)
                value = struct.pack('<BBHiif' + fmt, width, reserved, count, lo, hi, scale, *values)
            require(target + len(value) <= self.module_end, 'Xenon table outside module')
            self.tables[target] = selector, value
        require(self.tables[target][0] == selector, 'Xenon conflicting pointer targets')

    def _player(self, at):
        require(at in self.players and at not in self.seen_players, 'Xenon player descriptor')
        self.seen_players.add(at)
        self._copy(at, '3I8B2I')
        options = self.state[at + 14]
        require(options <= 6 and self.state[at + 8:at + 20] ==
                bytes((0, 0, 0, 0, 0, 0, options, 0, 255, 0, 0, 0)), 'Xenon player profile')
        for option in range(options):
            pos = at + 28 + 12 * option
            self._copy(pos, '4B2I')
            require(self.state[pos] in (0, 2, 3, 5, 6, 8), 'Xenon player option')
        self._pointer(at + 4, selector=True)

    def _import(self, op, at):
        disp = struct.pack('<i', at - 24)
        if at != 24:
            self.code += b'\x81\xc6' + disp
        self.code += b'\x56\xe8'       # cdecl: push the operation state
        self.calls.append(len(self.code))
        self.code += _pack(IMPORTS[op])
        self.code += b'\x83\xc4\x04'  # discard the argument
        if at != 24:
            self.code += b'\x81\xc6' + struct.pack('<i', 24 - at)

    def _memory(self, instruction, at):
        self.code += instruction + struct.pack('<i', at - 24)

    def operation(self, op, at, edges):
        self.operations.append((op, at, edges))
        require(at % 4 == 0 and 24 <= at <= len(self.state) - 4, 'Xenon program cursor')
        if op in BINARY or op == 25:
            self._copy(at, '2I')
        elif op == 0:
            require(at == 24, 'Xenon destructor export offset')
            self._copy(at, '5I')
        elif op == 1:
            self._copy(at, f'4I4B{self.member_words}I')
            require(at == 44 and self.state[at + 16:at + 20] == bytes((self.member_words, 0, 0, 0)),
                    'Xenon export member layout')
        elif op == 3:
            self._copy(at, 'I')
            require(self.member_words != 100 or not edges, 'Xenon one-shot pulse has output edges')
        elif op == 4:
            self._copy(at, '4I')
            require(at == self.destroy and not edges, 'Xenon destructor offset')
        elif op in (7, 15, 28):
            self._copy(at, '4B3I' if op == 28 else '4I')
            if op == 15:
                self._pointer(at)
            if op == 28:
                require(self.state[at] in (0, 1, 3), 'Xenon oscillator waveform')
        elif op == 10:
            self._copy(at, '4I4BI')
        elif op == 11:
            # 829BA160 / MW 00819B49: float elapsed, byte pulse, reset, delay.
            self._copy(at, 'I4B2I')
        elif op == 12:
            # 829BA1E0 / MW 00819B90: first asserted input holds its value.
            offset, count, reserved = words(self.state, at, '>HBB')
            require(1 <= count <= 16 and offset == 8 + 4 * count and not reserved,
                    'Xenon latch input extent')
            self._copy(at, f'H2B{1 + 2 * count}I')
        elif op in (13, 17, 21, 30):
            count = self.state[at]
            require(1 <= count <= 16, 'Xenon arithmetic input count')
            self._copy(at, f'4B{count + (op != 13)}I')
        elif op == 27:
            self._player(at)
        elif op in (29, 31, 35):
            self._copy(at, '7I' if op == 29 else '3I')
        else:
            raise ValueError(f'unsupported Xenon AEMS opcode {op}')

        if op in IMPORTS and (op != 3 or self.member_words != 100):
            self._import(op, at)
        elif op == 3:
            self.code += b'\x2b\xc0'
            self._memory(b'\x89\x86', at)
        else:
            self._memory(b'\x8b\x86', at)
            self._memory(b'\x8b\x8e', at + 4)
            self.code += BINARY[op]
        for src, dst in edges:
            if src != -1:
                self._memory(b'\x8b\x8e', at + src)
            self._memory(b'\x89\x86' if src == -1 else b'\x89\x8e', at + dst)
            self.links.extend((at + dst,) if src == -1 else (at + src, at + dst))

    def compile(self, entry, end):
        at, cursor = entry, 24
        while at < end and self.data[at] != 255:
            op, count, reserved = _bounded_words(self.data, at, '>BBH', end)
            require(not reserved and count <= 100, 'Xenon program instruction shape')
            edges = _bounded_words(self.data, at + 4, f'>{2 * count}i', end) if count else ()
            step = _bounded_words(self.data, at + 4 + 8 * count, '>i', end)[0]
            self.operation(op, cursor, tuple(zip(edges[::2], edges[1::2])))
            at += 8 + 8 * count
            cursor += step
        require(at + 4 <= end and self.data[at:at+4] == b'\xff\0\0\0' and not any(self.data[at+4:end]),
                'Xenon program terminator/padding')
        require(all(self.types.get(at) == 'I' and at not in self.pointers and
                    not any(p <= at < p + 20 for p in self.players) for at in self.links),
                'Xenon program link outside numeric state')
        require(self.seen_players == set(self.players) and self.pointer_targets.keys() == self.pointers,
                'Xenon incomplete player or pointer closure')
        typed = set()
        for at, fmt in self.types.items():
            typed.update(range(at, at + struct.calcsize('>' + fmt)))
        require(all(not value or at in typed for at, value in enumerate(self.state)),
                'Xenon unclassified nonzero state')
        self.code += b'\x5e\xc3'


def translate(data, expected):
    """Keep the source bank intact and emit a small LE executable ABI image."""
    require(0x60 <= len(data) <= 16 << 20 and data[:4] == b'ABKC' and _uint(data, 0x14) == len(data) and
            words(data, 0x0a, '>H') == (1,), 'Xenon ABKC header')
    table, bank, extent = words(data, 0x1c, '>3I')
    f1, f2, f3 = words(data, 0x30, '>3I')
    require(table == 0x5c and bank == _uint(data, 0x18) and bank + extent == f1 and
            f1 + 4 == f2 < f3 < len(data) and _uint(data, f1) == 0,
            'Xenon module and relocation extents')
    validate_samples(data, expected['sounds'], bank, f1)
    require(data[table+32:table+40] == b'\0\0\0\0\x08\x01\x01\0',
            'Xenon module descriptor profile')
    entry, state, size, destroy = words(data, table + 40, '>4I')
    require(table + 92 <= entry < state <= 65536 and 16 <= size <= 65536 and state + size <= bank and
            size - 16 == destroy, 'Xenon code/template extents')
    players = words(data, table + 60, '>8I')
    require(tuple(sorted(set(players))) == players and players[-1] < destroy, 'Xenon player offsets')
    count = _uint(data, f2)
    require(0 < count <= 256 and f2 + 4 + count * 4 == f3, 'Xenon pointer fixup count')
    pointers = words(data, f2 + 4, f'>{count}I')
    require(len(set(pointers)) == count and all(state <= p <= state + size - 4 for p in pointers),
            'Xenon pointer outside state')
    require(_uint(data, f3) == 1, 'Xenon interface fixup count')
    site, name, kind = words(data, f3 + 4, '>3I')
    ident = data[name:]
    require(site == table + 4 and name == f3 + 16 and kind >> 24 == 1 and
            ident in (b'\x31\x1c\x0d\xd6CAR\0', b'\x31\x1c\x76\xefCAR_ENG\0'),
            'Xenon export interface')

    mod = _Module(data, data[state:state+size], players, destroy, expected['sounds'],
                  (p - state for p in pointers), bank)
    mod.compile(entry, state)
    new_state = (entry + len(mod.code) + 3) & ~3
    output = bytearray(entry)
    output[:4] = b'ABKC'
    struct.pack_into('<H', output, 0x0a, 1)
    struct.pack_into('<I', output, table, _uint(data, table))
    output[table+36:table+40] = data[table+36:table+40]
    struct.pack_into('<4I4x8I', output, table + 40, entry, new_state, size, destroy, *players)
    output += mod.code + bytes(new_state - entry - len(mod.code)) + mod.template
    offsets = {}
    end = state + size
    for target, (_, raw) in sorted(mod.tables.items()):
        require(end <= target, 'Xenon overlapping state/table extents')
        end = target + len(raw)
        offsets[target] = len(output)
        output += raw
        output += bytes((-len(output)) % 4)
    for at, target in mod.pointer_targets.items():
        struct.pack_into('<I', output, new_state + at, offsets[target])
    sample_bank = len(output)
    # Samples remain in the source image; the execution image uses only modules.
    output += b'S10A' + bytes(8)
    f1 = len(output)
    output += _pack(len(mod.calls), *(entry + at for at in mod.calls))
    f2 = len(output)
    output += _pack(count, *(new_state + at for at in sorted(mod.pointer_targets)))
    f3 = len(output)
    output += _pack(1, table + 4, f3 + 16, 1) + ident[:2][::-1] + ident[2:4][::-1] + ident[4:]
    struct.pack_into('<5I', output, 0x14, len(output), sample_bank, table, sample_bank, 12)
    struct.pack_into('<3I', output, 0x30, f1, f2, f3)
    return bytes(output)


def translate_undercover(data, expected, interface):
    """Translate a decoded player export, retaining its exact CSIS identity.

    The other exports stay in the immutable source bank; they are not silently
    admitted by resemblance to a Carbon interface. CAR_SWTN is the event player,
    FX_SHIFTING_01 the gear-change player, and CAR_WHINE the continuous loop.
    Their interpreter contracts are the 40-entry vector at 830DC8F0, notably
    timer 829BA160 and latch 829BA1E0. No native registry alias is installed.
    """
    profiles = {'CAR_SWTN': (0x6a16, 0x7859, 7),
                'CAR_WHINE': (0x6a16, 0x603f, 9),
                'FX_SHIFTING_01': (0x085b, 0x7937, 6)}
    require(interface in profiles, 'unsupported Undercover export')
    if words(data, 20, '<I')[0] == len(data):
        return _undercover_pc(data, expected, interface, profiles[interface])
    require(0x60 <= len(data) <= 16 << 20 and data[:4] == b'ABKC' and
            _uint(data, 20) == len(data), 'Undercover ABKC header')
    count = words(data, 10, '>H')[0]
    table, bank, extent = words(data, 28, '>3I')
    f1, f2, f3 = words(data, 48, '>3I')
    require(1 <= count <= 8 and table == 92 and bank == _uint(data, 24) and
            bank + extent == f1 and f1 + 4 == f2 < f3 < len(data) and not _uint(data, f1),
            'Undercover bank/fixup extents')
    from .car_audio_codec import parse_bank
    sounds = parse_bank(data, endian='>', codecs=(3,))
    require(len(sounds) == len(expected['sounds']), 'Undercover sample count')
    for sound, row in zip(sounds, expected['sounds']):
        require((sound is None) == bool(row.get('empty')), 'Undercover null sample identity')
        if sound is not None:
            require((row['offset'], row['frames'], row['rate'], row['loop_start']) ==
                    (sound.header_offset-bank, sound.frames, sound.rate, sound.loop_start),
                    'Undercover sample metadata identity')
    n = _uint(data, f2)
    require(0 < n <= 256 and f2 + 4 + n*4 == f3, 'Undercover pointer fixups')
    pointers = words(data, f2+4, f'>{n}I')
    require(len(set(pointers)) == n and all(table <= p <= bank-4 for p in pointers),
            'Undercover pointer sites')
    refs, kinds, selected = {}, {}, None
    n = _uint(data, f3)
    require(1 <= n <= 32 and f3+4+12*n <= len(data), 'Undercover interface fixups')
    for i in range(n):
        site, at, kind = words(data, f3+4+12*i, '>3I')
        require(site not in refs and table <= site <= bank-8 and kind >> 24 in (1, 2) and
                f3+4+12*n <= at <= len(data)-6, 'Undercover interface relocation')
        end = data.find(b'\0', at+4, min(at+260, len(data)))
        require(end > at+4, 'Undercover interface name')
        refs[site] = data[at:end+1]
        kinds[site] = kind >> 24
    cursor, bounds = table, []
    for _ in range(count):
        profile = span(data, cursor+32, 8)
        players, destructor, members, classes = profile[4:]
        entry, state, size, destroy = words(data, cursor+40, '>4I')
        bounds.extend((entry, state))
        require(cursor+60+4*(players+classes) <= entry < state < bank and
                24 <= size <= bank-state and destroy <= size-16 and
                cursor+4 in refs, 'Undercover module extents/export')
        if refs[cursor+4][4:] == interface.encode() + b'\0':
            require(selected is None and profile == bytes((0, 0, 0, 0, 1, 1, 1, 0)) and
                    destroy == size-16, 'Undercover player module profile')
            system, signature, members = profiles[interface]
            ident = struct.pack('>HH', system, signature) + interface.encode() + b'\0'
            require(kinds[cursor+4] == 1 and refs[cursor+4] == ident and
                    data[state+60:state+64] == bytes((members, 0, 0, 0)),
                    'Undercover export identity/member contract')
            player = _uint(data, cursor+60)
            require(64+4*members <= player < destroy and
                    not any(state <= site < state+size for site in refs),
                    'Undercover selected module imported class')
            selected = entry, state, size, destroy, player, members, ident
        cursor += 60 + 4*(players+classes)
    require(selected is not None, 'Undercover player export missing')
    entry, state, size, destroy, player, members, ident = selected
    local = [p-state for p in pointers if state <= p < state+size]
    mod = _Module(data, data[state:state+size], (player,), destroy, expected['sounds'],
                  local, bank, members, (0x6400, 0xff00))
    require(cursor <= min(bounds), 'Undercover descriptor/code overlap')
    mod.compile(entry, min(bound for bound in bounds if bound > entry))
    event_identity = 0xffffffff
    if interface == 'CAR_SWTN':
        # Member 1 is the source CarID, not a gear or donor-car identifier.
        # Its edge feeds the right operand of subtract_min(-1000, CarID, in).
        # Both signed trigger ranges then destroy a mismatched instance.
        exports = [(at, edges) for op, at, edges in mod.operations if op == 1]
        require(len(exports) == 1, 'Undercover event member export')
        at, edges = exports[0]
        targets = [at+dst for src, dst in edges if src == 24]
        guards = [(at, edges) for op, at, edges in mod.operations if op == 31 and at+8 in targets]
        require(len(guards) == 1, 'Undercover event identity guard')
        at, edges = guards[0]
        minimum, event_identity, initial = words(mod.state, at, '>3I')
        require(minimum == 0xfffffc18 and event_identity <= 200 and initial == 0 and len(edges) == 2,
                'Undercover event identity comparison')
    # A compact one-export ABI image. Source sample indices remain unchanged;
    # PCM is served from the original S10A slots, never from this empty bank.
    entry, table = 156, 92
    new_state = (entry+len(mod.code)+3) & ~3
    output = bytearray(entry)
    output[:4] = b'ABKC'
    struct.pack_into('<H', output, 10, 1)
    output[table+36:table+40] = bytes((1, 1, 1, 0))
    struct.pack_into('<5I', output, table+40, entry, new_state, size, destroy, 0)
    struct.pack_into('<I', output, table+60, player)
    output += mod.code + bytes(new_state-entry-len(mod.code)) + mod.template
    offsets, previous = {}, state+size
    for target, (_, raw) in sorted(mod.tables.items()):
        require(previous <= target, 'Undercover overlapping state/tables')
        previous = target + len(raw)
        offsets[target] = len(output)
        output += raw + bytes(-len(raw) % 4)
    for at, target in mod.pointer_targets.items():
        struct.pack_into('<I', output, new_state+at, offsets[target])
    sample_bank = len(output)
    output += b'S10A' + bytes(8)
    f1 = len(output); output += _pack(len(mod.calls), *(entry+at for at in mod.calls))
    f2 = len(output); output += _pack(len(local), *(new_state+at for at in sorted(mod.pointer_targets)))
    f3 = len(output); output += _pack(1, table+4, f3+16, 1) + ident[:2][::-1] + ident[2:4][::-1] + ident[4:]
    struct.pack_into('<5I', output, 20, len(output), sample_bank, table, sample_bank, 12)
    struct.pack_into('<3I', output, 48, f1, f2, f3)
    return bytes(output), event_identity


def _undercover_pc(data, expected, interface, profile):
    """Isolate the same three exports from the PC bank's native x86 modules.

    Preserve instructions, state, lookup tables and source slot indices. Only
    the descriptor and relocation sites move into the single-export ABI image.
    Unselected exports and their imports never enter the executable image.
    """
    from . import car_audio_abi as abi, car_audio_codec as codec
    require(0x60 <= len(data) <= 16 << 20 and data[:4] == b'ABKC', 'Undercover PC header')
    u = lambda at: words(data, at, '<I')[0]
    count = words(data, 10, '<H')[0]
    table, bank, extent = words(data, 28, '<3I')
    f1, f2, f3 = words(data, 48, '<3I')
    require(1 <= count <= 8 and table == 92 and bank == u(24) and
            bank + extent == f1 < f2 < f3 < len(data), 'Undercover PC extents')
    sounds = codec.parse_bank(data)
    metadata = [dict(empty=True) if s is None else dict(empty=False,
        offset=s.header_offset-bank, frames=s.frames, rate=s.rate, loop_start=s.loop_start) for s in sounds]
    require(metadata == expected['sounds'], 'Undercover PC sample metadata identity')
    n = u(f3)
    require(1 <= n <= 32 and f3+4+12*n <= len(data), 'Undercover PC interfaces')
    refs = {}
    for i in range(n):
        site, text, kind = words(data, f3+4+12*i, '<3I')
        require(site not in refs and table <= site <= bank-8 and kind & 255 in (1, 2) and
                f3+4+12*n <= text <= len(data)-6, 'Undercover PC interface extent')
        end = data.find(b'\0', text+4, min(text+260, len(data)))
        require(end > text+4, 'Undercover PC interface name')
        refs[site] = (data[text:end+1], kind & 255)
    system, signature, members = profile
    identity = struct.pack('<HH', system, signature) + interface.encode() + b'\0'
    cursor, selected, bounds = table, None, []
    for _ in range(count):
        players, destructor, exports, classes = span(data, cursor+36, 4)
        entry, state, size, destroy = words(data, cursor+40, '<4I')
        require(cursor+60+4*(players+classes) <= entry < state < bank and
                24 <= size <= bank-state and destroy <= size-16, 'Undercover PC module extent')
        bounds.extend((entry, state))
        if refs.get(cursor+4) == (identity, 1):
            require(selected is None and span(data, cursor+32, 8) == bytes((0,0,0,0,1,1,1,0)) and
                    destroy == size-16 and data[state+60:state+64] == bytes((members,0,0,0)),
                    'Undercover PC player profile')
            selected = entry, state, size, destroy, u(cursor+60)
        cursor += 60+4*(players+classes)
    require(selected is not None and cursor <= min(bounds), 'Undercover PC export missing/overlap')
    entry, state, size, destroy, player = selected
    stop = min(at for at in bounds if at > entry)
    require(64+4*members <= player < destroy and
            not any(state <= site < state+size for site in refs), 'Undercover PC imported class')
    ni, np = u(f1), u(f2)
    require(ni <= 4096 and f1+4+4*ni == f2 and np <= 256 and f2+4+4*np == f3,
            'Undercover PC relocation extent')
    imports = words(data, f1+4, '<' + str(ni) + 'I')
    pointers = words(data, f2+4, '<' + str(np) + 'I')
    require(all(table <= site <= bank-4 for site in imports + pointers) and
            len(set(imports)) == len(imports) and len(set(pointers)) == len(pointers),
            'Undercover PC relocation extent')
    calls = [site for site in imports if entry <= site <= stop-4]
    local = [site for site in pointers if state <= site <= state+size-4]
    require(calls and local and all(state+size <= u(site) < bank for site in local),
            'Undercover PC table closure')
    tail = min(u(site) for site in local)
    output = bytearray(156)
    output[:4] = b'ABKC'; struct.pack_into('<H', output, 10, 1)
    output[128:132] = bytes((1,1,1,0))
    output += data[entry:stop] + bytes(-(stop-entry) % 4)
    new_state = len(output)
    output += data[state:state+size]
    new_tail = len(output); output += data[tail:bank]
    struct.pack_into('<5I', output, 132, 156, new_state, size, destroy, 0)
    struct.pack_into('<I', output, 152, player)
    for site in local:
        struct.pack_into('<I', output, new_state+site-state, new_tail+u(site)-tail)
    sample_bank = len(output); output += b'S10A' + bytes(8)
    f1 = len(output); output += _pack(len(calls), *(156+site-entry for site in calls))
    f2 = len(output); output += _pack(len(local), *(new_state+site-state for site in local))
    f3 = len(output); output += _pack(1, 96, f3+16, 1) + identity
    struct.pack_into('<5I', output, 20, len(output), sample_bank, 92, sample_bank, 12)
    struct.pack_into('<3I', output, 48, f1, f2, f3)
    output = bytes(output)
    import hashlib
    report = abi.inspect_bank(output, dict(kind='abk', size=len(output),
        sha256=hashlib.sha256(output).hexdigest(), sounds=metadata, module_count=1,
        module_region_offset=92, module_region_size=sample_bank-92,
        module_region_sha256=hashlib.sha256(output[92:sample_bank]).hexdigest()))
    event_identity = 0xffffffff
    if interface == 'CAR_SWTN':
        guards = [i['instance_offset'] for i in report['imports'] if i['ordinal'] == 31 and
                  words(output, new_state+i['instance_offset'], '<I')[0] == 0xfffffc18]
        require(len(guards) == 1, 'Undercover PC event guard')
        _, event_identity, initial = words(output, new_state+guards[0], '<3I')
        require(event_identity <= 200 and initial == 0, 'Undercover PC event identity')
    return output, event_identity
