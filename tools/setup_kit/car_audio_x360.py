"""Translate Carbon Xenon's interpreted AEMS modules to the checked MW guest ABI."""
import math
import re
import struct

from .car_convert import require, span, words

# Opcode handlers in the Xenon interpreter and their equivalent MW imports.
# The inline operators below have no import in the Windows AEMS compiler.
IMPORTS = {op: op for op in (0, 1, 4, 7, 10, 13, 15, 17, 21, 25, 27, 28, 29, 30, 31, 35)}
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
    def __init__(self, data, state, players, destroy, sounds, pointers, module_end):
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
        self.code = bytearray(b'\x56\x8b\x74\x24\x08')  # ESI = state + 24
        self.calls = []
        self.links = []
        self._copy(0, '6I')

    def _copy(self, at, fmt):
        size = struct.calcsize('>' + fmt)
        span(self.state, at, size, 'Xenon module state extent')
        # Each requested group starts on a word boundary; remember its typed
        # layout so links can address numeric state only.
        require(at % 4 == size % 4 == 0, 'Xenon unaligned state')
        for repeat, item in re.findall(r'(\d*)([BI])', fmt):
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
                require(0 < count <= 256, 'Xenon selector count')
                rows = span(self.data, target + 4, count * 12, 'Xenon selector extent')
                out = bytearray(_pack(count))
                for first, slot, flags in struct.iter_unpack('>3I', rows):
                    sample, mode = first >> 16, first & 65535
                    if (first, slot, flags) == (0xffff0000, 0, 0):
                        out += _pack(65535, 0, 0)
                    else:
                        require(mode == 0x6400 and slot == sample + 1 and flags == 0xffffffff
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
        require(at % 4 == 0 and 24 <= at <= len(self.state) - 4, 'Xenon program cursor')
        if op in BINARY or op == 25:
            self._copy(at, '2I')
        elif op == 0:
            require(at == 24, 'Xenon destructor export offset')
            self._copy(at, '5I')
        elif op == 1:
            self._copy(at, '4I4B100I')
            require(at == 44 and self.state[at + 16:at + 20] == bytes((100, 0, 0, 0)),
                    'Xenon export member layout')
        elif op == 3:
            self._copy(at, 'I')
            require(not edges, 'Xenon one-shot pulse has output edges')
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

        if op in IMPORTS:
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
