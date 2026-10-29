"""Static recompiler prototype: x86-32 functions of speed.exe -> C (see ../ARCHITECTURE.md).

Each guest function becomes   void f_XXXXXXXX(Cpu *restrict c)   operating on guest memory G (runtime.h).
Registers live in the Cpu struct; flags are C locals computed by every flag-writing instruction (clang removes
the unused ones). Calls push the guest return address so stack-passed arguments keep their exact layout.

Python API:
    rc = Recompiler()
    code, why = rc.translate(addr, size)     # C source, or None and the reason it can't be translated yet

Run under tools/.venv (capstone).
"""
import hashlib
import os
import re
import struct
import sys

from capstone import CS_ARCH_X86, CS_MODE_32, CS_OP_IMM, CS_OP_MEM, CS_OP_REG, Cs
from capstone import x86_const as X

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
# The player's speed.exe: NFS_SPEED_EXE (build_all.py --exe sets it), else the owner's game folder next to port/.
EXE = os.environ.get("NFS_SPEED_EXE") or os.path.join(ROOT, "Need For Speed Most Wanted Black Edition", "speed.exe")
# The only supported executable code: PC v1.3. Hooks, function starts (functions_v13.tsv) and translation bounds are
# addresses in this code, so an exe whose .text/.rdata/.data differ (another version, a cracked or patched exe) is
# refused; headers, timestamp, resources and appended data don't matter (exe_fingerprint.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exe_fingerprint  # noqa: E402


def insn_fingerprint(data) -> str:
    """Short SHA-256 of guest instruction bytes. Address-specific patches check this fingerprint (and the mnemonic)
    so a different exe build fails loudly, without the game's instruction bytes being written into this source."""
    return hashlib.sha256(bytes(data)).hexdigest()[:16]


SPEED_EXE_SHA256 = exe_fingerprint.WHOLE_FILE_SHA256          # the owner's whole file: still accepted (alias)
SPEED_EXE_FINGERPRINT = exe_fingerprint.SUPPORTED_FINGERPRINT

# PC 1.3: g_pEAXSound (00911FA8) -> +24 AudioSettings -> +24 Audio Mode.
# Explicit read sites, traced through the two pointer loads; no stores intercepted.
POSITIONAL_MODE_READS = {
    0x4BA6A4, 0x4D1711, 0x4EB81F, 0x4EC448, 0x4EC59C, 0x4EC76F,
    0x4EC91E, 0x4ECAC5, 0x4ECCC8, 0x4ECE51, 0x4ED088, 0x4ED117,
    0x4ED299, 0x4ED39C, 0x4ED60B, 0x4ED86C, 0x4ED8AB, 0x4EDAD2,
    0x4EDF43, 0x4EE5A5, 0x4EE6FA, 0x4EE912, 0x4EEB20, 0x4EEC6B,
    0x4EED89, 0x4EEE69, 0x4EF0F7, 0x4EF221, 0x4EF839, 0x4EF9CB,
    0x4EFB36, 0x4EFD09, 0x4EFFD6, 0x4F006E, 0x4F0163, 0x4F01F0,
    0x4F0288, 0x4F04AE, 0x4F0892, 0x4F116E, 0x4F1266, 0x4F1421,
    0x4F15F6, 0x4F3C78, 0x4F3D5C, 0x4F3E28, 0x4F3F1F,
}
# Only assignments from the fixed source-default table (008C5CAA, mode 3:
# stereo 008C5D22, quad 008C5D2E, six 008C5D3A). Explicit 3D pans are untouched.
POSITIONAL_DEFAULT_STORES = {0x81C056, 0x81CEE9, 0x81CFB0, 0x821B83, 0x823B6B}

REG32 = {"eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"}
REG16 = {"ax": "eax", "cx": "ecx", "dx": "edx", "bx": "ebx", "sp": "esp", "bp": "ebp", "si": "esi", "di": "edi"}
REG8L = {"al": "eax", "cl": "ecx", "dl": "edx", "bl": "ebx"}
REG8H = {"ah": "eax", "ch": "ecx", "dh": "edx", "bh": "ebx"}
CTYPE = {1: "uint8_t", 2: "uint16_t", 4: "uint32_t", 8: "uint64_t"}
STYPE = {1: "int8_t", 2: "int16_t", 4: "int32_t", 8: "int64_t"}
MASK = {1: "0xFFu", 2: "0xFFFFu", 4: "0xFFFFFFFFu"}
BITS = {1: 8, 2: 16, 4: 32}

COND = {
    "o": "of", "no": "!of", "b": "cf", "ae": "!cf", "e": "zf", "ne": "!zf", "be": "(cf | zf)", "a": "!(cf | zf)",
    "s": "sf", "ns": "!sf", "p": "pf", "np": "!pf", "l": "(sf != of)", "ge": "(sf == of)",
    "le": "(zf | (sf != of))", "g": "(!zf & (sf == of))",
}


MMX_OPS = {"paddb", "paddw", "paddd", "paddsb", "paddsw", "paddusb", "paddusw", "psubb", "psubw", "psubd", "psubsb",
           "psubsw", "psubusb", "psubusw", "pmullw", "pmulhw", "pmulhuw", "pmaddwd", "pcmpeqb", "pcmpeqw", "pcmpeqd",
           "pcmpgtb", "pcmpgtw", "pcmpgtd", "pmaxsw", "pminsw", "pmaxub", "pminub", "pavgb", "pavgw", "psadbw",
           "punpcklbw", "punpckhbw", "punpcklwd", "punpckhwd", "punpckldq", "punpckhdq", "pand", "por", "pxor", "pandn",
           "packsswb", "packuswb", "packssdw", "psllw", "psrlw", "psraw", "pslld", "psrld", "psrad", "psllq", "psrlq"}


# Flags for compiling translated code (build_all.py, difftest.py). Translated functions take `Cpu *restrict c`, so
# guest stores can't clobber registers kept in host registers, but LLVM only proves that while `c` is not captured,
# and its capture tracking gives up after 20 uses of a pointer (every register access is one): without the higher
# limit, restrict has no effect.
CFLAGS = ["-O2", "-w", "-mllvm", "-capture-tracking-max-uses-to-explore=100000"]


class Unsupported(Exception):
    pass


class PE:
    def __init__(self, path=None):
        path = path or EXE
        self.data = open(path, "rb").read()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.fingerprint = exe_fingerprint.check(self.data, path)
        off = struct.unpack_from("<I", self.data, 0x3C)[0]
        n = struct.unpack_from("<H", self.data, off + 6)[0]
        s0 = off + 24 + struct.unpack_from("<H", self.data, off + 20)[0]
        self.sections = [struct.unpack_from("<8sIIII", self.data, s0 + i * 40) for i in range(n)]
        self.base = 0x400000
        self.imports = self._imports(off + 24)

    def read(self, addr, n):
        for _, vs, va, rs, raw in self.sections:
            a = addr - self.base
            if va <= a < va + max(vs, rs):
                return self.data[raw + a - va:raw + a - va + n]
        return None

    def _rva_off(self, r):
        for _, vs, va, rs, raw in self.sections:
            if va <= r < va + max(vs, rs):
                return raw + r - va

    def _imports(self, opt):
        out, p = {}, self._rva_off(struct.unpack_from("<I", self.data, opt + 96 + 8)[0])
        while True:
            oft, _, _, name, ft = struct.unpack_from("<IIIII", self.data, p)
            if name == 0:
                break
            dll = self.data[self._rva_off(name):].split(b"\0")[0].decode()
            q, slot = self._rva_off(oft or ft), ft
            while True:
                v = struct.unpack_from("<I", self.data, q)[0]
                if v == 0:
                    break
                fn = f"#{v & 0xFFFF}" if v & 0x80000000 else self.data[self._rva_off(v) + 2:].split(b"\0")[0].decode()
                out[self.base + slot] = f"{dll}!{fn}"
                q += 4
                slot += 4
            p += 20
        return out


def str_cmp(n, a, b, adv):
    """C for one scas/cmps step: compare a - b (n bytes), advance the pointers (adv), set the flags."""
    T = CTYPE[n]
    return (f"{T} a = {a}, b = {b}; {T} r = ({T})(a - b); {adv} "
            f"cf = a < b; zf = r == 0; sf = (r >> {8 * n - 1}) & 1; of = (((a ^ b) & (a ^ r)) >> {8 * n - 1}) & 1; af = ((a ^ b ^ r) >> 4) & 1; pf = parity8(r);")


STR_N = {"b": 1, "w": 2, "d": 4}
STR_A = {1: "(uint8_t)c->eax", 2: "(uint16_t)c->eax", 4: "c->eax"}


class Recompiler:
    def __init__(self, pe=None, functions=None):
        self.pe = pe or PE()
        self.md = Cs(CS_ARCH_X86, CS_MODE_32)
        self.md.detail = True
        self.functions = functions or {}      # guest address -> limit (for direct-call targets)
        self.calls = False                    # True: translate calls/tail jumps (needs the callees' C too)
        self.seen = {}
        self.ft_exits = {}
        self.int3_stops = set()               # int3s with nothing after them (padding): translated as a return
        self.tables = {}
        self.callees = set()

    # ---------- operands ----------
    def ea(self, insn, op):
        m = op.mem
        parts = []
        if m.base:
            parts.append(self.reg_read(insn.reg_name(m.base), 4))
        if m.index:
            idx = self.reg_read(insn.reg_name(m.index), 4)
            parts.append(f"{idx} * {m.scale}" if m.scale != 1 else idx)
        if m.disp or not parts:
            parts.append(f"0x{m.disp & 0xFFFFFFFF:X}u")
        e = " + ".join(parts)
        if m.segment == X.X86_REG_FS:
            e = f"c->fs_base + {e}"
        elif m.segment not in (0, X.X86_REG_DS, X.X86_REG_SS, X.X86_REG_ES):
            raise Unsupported("segment")
        return f"(uint32_t)({e})"

    def reg_read(self, r, size=None):
        if r in REG32:
            return f"c->{r}"
        if r in REG16:
            return f"(uint16_t)c->{REG16[r]}"
        if r in REG8L:
            return f"(uint8_t)c->{REG8L[r]}"
        if r in REG8H:
            return f"(uint8_t)(c->{REG8H[r]} >> 8)"
        raise Unsupported(f"reg {r}")

    def reg_write(self, r, v):
        if r in REG32:
            return f"c->{r} = (uint32_t)({v});"
        if r in REG16:
            return f"c->{REG16[r]} = (c->{REG16[r]} & 0xFFFF0000u) | (uint16_t)({v});"
        if r in REG8L:
            return f"c->{REG8L[r]} = (c->{REG8L[r]} & 0xFFFFFF00u) | (uint8_t)({v});"
        if r in REG8H:
            return f"c->{REG8H[r]} = (c->{REG8H[r]} & 0xFFFF00FFu) | ((uint32_t)(uint8_t)({v}) << 8);"
        raise Unsupported(f"reg {r}")

    def mem_ref(self, size, addr):
        return {1: "M8", 2: "M16", 4: "M32", 8: "M64"}[size] + f"({addr})"

    def rd(self, insn, op, size=None):
        size = size or op.size
        if op.type == CS_OP_REG:
            return self.reg_read(insn.reg_name(op.reg), size)
        if op.type == CS_OP_IMM:
            return f"({CTYPE[size]})0x{op.imm & ((1 << (8 * size)) - 1):X}u"
        if op.type == CS_OP_MEM:
            # Only StartLicensedMusic's explicit-event search grows with Extra EA Trax.
            # The career/default loops must keep reading the real g_MaxSongs (26).
            if self.calls and insn.address in (0x004F6D65, 0x004F6DC9):
                assert size == 4 and op.mem.disp == 0x008F42C4 and not op.mem.base and not op.mem.index, "licensed-song bound changed"
                return "extratrax_licensed_song_count()"
            # Port 5.1 correctness: only these traced AudioSettings+0x24 reads,
            # never profile serialization or the setting's memory. See posaudio.c.
            if self.calls and size == 4 and insn.address in POSITIONAL_MODE_READS:
                return f"posaudio_mode_read({self.ea(insn, op)})"
            return self.mem_ref(size, self.ea(insn, op))
        raise Unsupported("operand")

    def wr(self, insn, op, v):
        if self.calls and insn.address in POSITIONAL_DEFAULT_STORES:
            v = f"posaudio_default_angle({v})"
        if op.type == CS_OP_REG:
            return self.reg_write(insn.reg_name(op.reg), v)
        if op.type == CS_OP_MEM:
            return f"{self.mem_ref(op.size, self.ea(insn, op))} = ({CTYPE[op.size]})({v});"
        raise Unsupported("write operand")

    # ---------- flag helpers (emit C) ----------
    @staticmethod
    def szp(res, size):
        return (f"zf = ({res}) == 0; sf = (({res}) >> {BITS[size] - 1}) & 1; pf = parity8({res});")

    # ---------- translation ----------
    def jump_table(self, i, lo, hi, decoded=None):
        """Discover bounded switch tables, including holes; retain legacy memcpy tables."""
        mem = i.operands[0].mem
        if mem.base or not mem.index or mem.scale != 4:
            return None
        # {index: target}. MSVC's memcpy indexes some tables from 1 (entry 0 overlaps code) or with negative
        # indices, so valid entries are also taken just before the base and after an invalid entry 0.
        a = mem.disp & 0xFFFFFFFF

        def entry(k):
            b = self.pe.read((a + 4 * k) & 0xFFFFFFFF, 4)
            if not b or len(b) < 4:
                return None
            t = struct.unpack("<I", b)[0]
            return t if lo <= t < hi else None
        # A cmp index, bound; ja default immediately guarding this straight-line
        # dispatch proves the table extent. Never scan arbitrarily beyond a hole:
        # adjacent data/code can also look like pointers into this function.
        bound = None
        prev = sorted((x for x in (decoded or {}).values() if i.address - 64 <= x.address < i.address), key=lambda x:x.address)
        for n, cmp in enumerate(prev):
            if cmp.mnemonic != "cmp" or len(cmp.operands) != 2:
                continue
            dst, imm = cmp.operands
            if dst.type != CS_OP_REG or dst.reg != mem.index or imm.type != CS_OP_IMM or not 0 <= imm.imm < 1024:
                continue
            tail = prev[n+1:]
            if not tail or tail[0].mnemonic != "ja" or tail[0].address != cmp.address + cmp.size:
                continue
            chain = tail[0].address + tail[0].size
            valid = True
            for x in tail[1:]:
                _, writes = x.regs_access()
                if x.address != chain or mem.index in writes or x.group(X.X86_GRP_JUMP) or x.group(X.X86_GRP_CALL):
                    valid = False
                    break
                chain = x.address + x.size
            if valid and chain == i.address:
                bound = imm.imm
        if bound is not None:
            return {k:t for k in range(bound+1) if (t := entry(k)) is not None} or None
        tab = {}
        base_ok = entry(0) is not None
        k = 0 if base_ok else 1
        while k < 1024 and entry(k) is not None:
            tab[k] = entry(k)
            k += 1
        # `neg idx` right before the jump: the index is negative (memcpy's tail-copy tables)
        regno = {"eax": 0, "ecx": 1, "edx": 2, "ebx": 3, "esp": 4, "ebp": 5, "esi": 6, "edi": 7}.get(i.reg_name(mem.index), -1)
        negated = self.pe.read(i.address - 2, 2) == bytes([0xF7, 0xD8 | regno]) if regno >= 0 else False
        if negated:
            tab = {0: entry(0)} if base_ok else {}
        if not base_ok or negated:            # not a real table start, or negated index: negative indices
            k = -1
            while k >= -8 and entry(k) is not None:
                tab[k] = entry(k)
                k -= 1
        return tab or None

    def discover(self, addr, limit):
        """Recursive descent from the entry. Without calls enabled, stays inside [addr, limit). With calls enabled,
        follows branches anywhere that isn't another function's start (Ghidra splits SEH funclets out of the middle of
        functions), and records where execution falls through into another function (self.ft_exits)."""
        seen, work = {}, [addr]
        while work:
            pc = work.pop()
            while pc not in seen:
                if pc != addr and (pc >= limit if not self.calls else pc in self.functions):
                    raise Unsupported("falls into next function")   # only reachable from a branch target
                code = self.pe.read(pc, 16)
                i = next(self.md.disasm(code, pc), None) if code else None
                if i is None:
                    raise Unsupported("undecodable")
                seen[pc] = i
                m = i.mnemonic
                if (i.group(X.X86_GRP_JUMP) or m.startswith("loop")) and i.operands and i.operands[0].type == CS_OP_IMM:
                    t = i.operands[0].imm
                    if (addr <= t < limit) if not self.calls else (t == addr or t not in self.functions):
                        work.append(t)
                if m == "jmp" and i.operands and i.operands[0].type == CS_OP_MEM:
                    tab = self.jump_table(i, addr, limit, seen) if not self.calls else self.jump_table(i, max(addr - 0x100000, 0x401000), addr + 0x100000, seen)
                    if tab is None:
                        if not self.calls:
                            raise Unsupported("indirect jmp")
                    else:
                        self.tables[i.address] = tab
                        work.extend(t for t in set(tab.values()) if t == addr or t not in self.functions)
                if m in ("ret", "jmp", "hlt", "ud2") or m.startswith("ret"):
                    break
                nxt = pc + i.size
                if m == "int3" and (self.pe.read(nxt, 1) == b"\xCC" or (nxt >= limit if not self.calls else
                                                                       nxt != addr and nxt in self.functions)):
                    self.int3_stops.add(pc)          # padding (after a noreturn call): nothing follows to continue to
                    break
                if self.calls and nxt != addr and nxt in self.functions:
                    self.ft_exits[pc] = nxt          # falls through into the next function
                    break
                if not self.calls and nxt >= limit:
                    raise Unsupported("falls into next function")
                pc = nxt
        if not seen:
            raise Unsupported("empty")
        self.seen = seen
        return [seen[k] for k in sorted(seen)]

    _FLAG_TEST = (X.X86_EFLAGS_TEST_CF, X.X86_EFLAGS_TEST_PF, X.X86_EFLAGS_TEST_AF, X.X86_EFLAGS_TEST_ZF,
                  X.X86_EFLAGS_TEST_SF, X.X86_EFLAGS_TEST_OF)
    _FLAG_DEF = tuple(tuple(getattr(X, f"X86_EFLAGS_{k}_{f}") for k in ("MODIFY", "RESET", "SET", "UNDEFINED"))
                      for f in ("CF", "PF", "AF", "ZF", "SF", "OF"))

    def reads_flags_at_entry(self, addr):
        """True when a flag is tested on the straight-line path from addr before it is written (capstone eflags).
        Only such functions take flags from c->eflags (and clear it), and only jumps into them store it, so
        c->eflags is non-zero just between a fall-through/tail jump and its target's entry: a normal call or a
        host->guest call into the same function still starts with clear flags, as before audit 3."""
        cache = self.__dict__.setdefault("_entry_flags", {})
        if addr not in cache:
            code, hit, done = self.pe.read(addr, 96), False, [False] * 6
            for i in (self.md.disasm(code, addr) if code else ()):
                ef = i.eflags
                if any(ef & t and not done[k] for k, t in enumerate(self._FLAG_TEST)):
                    hit = True
                    break
                for k, bits in enumerate(self._FLAG_DEF):
                    if any(ef & b for b in bits):
                        done[k] = True
                if all(done) or i.mnemonic in ("ret", "jmp", "call") or i.group(X.X86_GRP_JUMP):
                    break
            cache[addr] = hit
        return cache[addr]

    def flags_to(self, t):
        return self.flags_save() if self.reads_flags_at_entry(t) else ""

    @staticmethod
    def flags_save():
        return "c->eflags = cf | pf << 2 | af << 4 | zf << 6 | sf << 7 | of << 11;"

    @staticmethod
    def flags_load(declare=False):
        prefix = "uint8_t " if declare else ""
        sep = ", " if declare else "; "
        fields = [("cf",0),("pf",2),("af",4),("zf",6),("sf",7),("of",11)]
        return prefix + sep.join(f"{n} = (c->eflags >> {b}) & 1" for n,b in fields) + ";"

    def translate(self, addr, limit, name=None):
        """limit: first address that belongs to the next function (an upper bound for discovery)."""
        self.callees = set()
        self.tables = {}
        self.ft_exits = {}
        self.int3_stops = set()
        try:
            insns = self.discover(addr, limit)
        except Unsupported as e:
            return None, str(e)
        # RC-aware arithmetic only where the function itself sets the control word: the game has ~30 FLDCW
        # sites, and a per-operation RC branch everywhere grew speed_09.o text 637 -> 787 KB.
        self.rc_aware = any(x.mnemonic == "fldcw" for x in insns)
        end = insns[-1].address + insns[-1].size
        targets = set()
        for i in insns:
            if (i.group(X.X86_GRP_JUMP) or i.mnemonic.startswith("loop")) and i.operands and i.operands[0].type == CS_OP_IMM:
                targets.add(i.operands[0].imm)
        for tab in self.tables.values():
            targets.update(tab.values())
        targets &= set(self.seen)
        body = []
        if insns[0].address != addr:
            # The body reaches code below the entry (e.g. a loop back into the code before a data-referenced start).
            # Blocks are emitted in address order, so jump to the real entry first.
            targets.add(addr)
            body.append(f"    goto L_{addr:08X};")
            self.entry_not_first = getattr(self, "entry_not_first", 0) + 1
        self.partial = getattr(self, "partial", 0)
        try:
            for i in insns:
                if i.address in targets:
                    body.append(f"L_{i.address:08X}:;")
                body.append(f"    /* {i.address:08X}: {i.mnemonic} {i.op_str} */")
                try:
                    code = self.insn(i, addr, end)
                except Unsupported as e:
                    # Only this instruction is missing (e.g. an SSE2 path the CPU flags never select): trap if reached.
                    if not self.calls:
                        raise
                    code = [f"untranslated(c, 0x{i.address:08X}u); return; /* {e} */"]
                    self.partial += 1
                body.extend("    " + s for s in code)
                if i.address in self.ft_exits:
                    t = self.ft_exits[i.address]
                    self.callees.add(t)
                    body.append(f"    {self.flags_to(t)} ABI_SKIP(); f_{t:08X}(c); return; /* falls into the next function */")
        except Unsupported as e:
            return None, str(e)
        fname = name or f"f_{addr:08X}"
        # `restrict`: the Cpu is host memory, never reached through a guest address, so guest stores don't force
        # registers kept in host registers to be reloaded. The local G (shadowing the global, which a guest store
        # could otherwise alias) keeps the guest base in a register for the whole function.
        head = [f"void {fname}(Cpu *restrict c) {{",
                "    uint8_t *const G = guest_base(); (void)G;",
                "    " + (self.flags_load(True) + " c->eflags = 0;" if self.reads_flags_at_entry(addr)
                         else "uint8_t cf = 0, pf = 0, af = 0, zf = 0, sf = 0, of = 0;") + " (void)cf; (void)pf; (void)af; (void)zf; (void)sf; (void)of;",
                "    ABI_SAVE();"]
        code = "\n".join(head + body + ["}"])
        if self.calls and addr == 0x006069C0:
            assert insn_fingerprint(self.pe.read(addr, 6)) == "2aab9ffc288165d6", "Lua protected frame changed"
            callees = self.callees.copy()
            resume, why = self.translate(0x006069E8, limit, name="lua_protected_resume")
            if resume is None:
                return None, "Lua continuation: " + why
            self.callees |= callees
            code = code.replace(f"void {fname}(", f"static void {fname}_body(", 1)
            code += "\n" + resume.replace("void lua_protected_resume(", "static void lua_protected_resume(", 1)
            code += f"\nvoid {fname}(Cpu *c) {{ host_lua_protected(c, {fname}_body, lua_protected_resume); }}"
        return code, None

    def insn(self, i, fstart, fend):
        m = i.mnemonic
        ops = i.operands
        # Asset-only name operands. Identity scans and ecar registration keep
        # CAR85; textures/vinyl masks/damage part names use the MW donor prefix.
        car85_names = {
            0x0075187D: ("0d36d59ea0d9dc43", "lea"),  # GetUsedCarTextureInfo: SKIN1, etc.
            0x00739DE8: ("5c62e091b8c0565f", "push"), # stock _CV texture
            0x00747FDE: ("5c62e091b8c0565f", "push"), # ride _CV texture
            0x007493F3: ("de5a6f78116eca62", "push"), # vinyl layer
            0x007494C8: ("de5a6f78116eca62", "push"), # vinyl mask
            0x00756F72: ("5c62e091b8c0565f", "push"), # damage KIT prefix
            0x007570E4: ("5c62e091b8c0565f", "push"), # rear KIT prefix
        }
        if self.calls and i.address in car85_names:
            fingerprint, mnemonic = car85_names[i.address]
            assert insn_fingerprint(i.bytes) == fingerprint and m == mnemonic, "car85 asset operand changed"
            if m == "lea":
                return [self.wr(i, ops[0], "car85_asset_name(" + self.ea(i, ops[1]) + ")")]
            return ["{ uint32_t v = car85_asset_name(" + self.rd(i, ops[0], 4) + "); c->esp -= 4; M32(c->esp) = v; }"]
        # Private add-one-car proof: exact PC v1.3 sites only. Two scans count
        # bytes; the remaining bounds, quantizer and invalid sentinel count types.
        car85_sites = {
            0x006690BB: ("be6aa05735bf582b", "cmp", "0x54u"),
            0x00734EE1: ("66b8476f465ef743", "push", "0x54u"),
            0x007398A6: ("ebef6f58a58540e9", "cmp", "0x54u"),
            0x007398FE: ("0a5583737be13bea", "cmp", "0x4440u"),
            0x00739908: ("1547aaa7f9e8b98e", "mov", "0x54u"),
            0x0073995B: ("be6aa05735bf582b", "cmp", "0x54u"),
            0x007515FB: ("be6aa05735bf582b", "cmp", "0x54u"),
            0x00759913: ("ebef6f58a58540e9", "cmp", "0x54u"),
            0x0075E6F4: ("fa1ca4c2ee59ade2", "cmp", "0x54u"),
            0x0075E7B4: ("fa1ca4c2ee59ade2", "cmp", "0x54u"),
            0x007B3877: ("110d70ee83ab00d8", "cmp", "0x4440u"),
        }
        if self.calls and i.address in car85_sites:
            fingerprint, mnemonic, literal = car85_sites[i.address]
            assert insn_fingerprint(i.bytes) == fingerprint and m == mnemonic, "car85 bound instruction changed"
            if m == "cmp":
                code = self.arith(i, m)
                if i.address in (0x006690BB, 0x007398A6, 0x0075E6F4, 0x0075E7B4):
                    # These consumers use signed JGE or exact-sentinel JE.
                    # Clamp only the comparison operand: every unsigned invalid
                    # type takes the invalid branch, with no guest register edit.
                    operand = self.rd(i, ops[0], ops[0].size)
                    code = [line.replace("a = " + operand + ",", "a = (" + operand + " >= car85_count() ? car85_count() : " + operand + "),") for line in code]
            elif m == "mov":
                code = [self.wr(i, ops[0], self.rd(i, ops[1], ops[0].size))]
            else:
                code = ["{ uint32_t v = 0x54u; c->esp -= 4; M32(c->esp) = v; }"]
            value = "(car85_count() * 208u)" if literal == "0x4440u" else "car85_count()"
            assert any(literal in line for line in code)
            return [line.replace(literal, value) for line in code]
        # Like the address-specific positional-audio read/store overrides, alter
        # only this guest instruction in the runtime build. Raw X360 flare alpha
        # must bypass PC's extra x2; no data-side half-alpha compensation.
        if self.calls and i.address == 0x00505B4C:
            assert insn_fingerprint(i.bytes) == "8db598bd71d9d6f8" and m == "fadd", "flare alpha instruction changed"
            return ["if (!attrib_x360_light_data()) { " + " ".join(self.farith(i, m)) + " }"]
        # CAnimPlayer::Load refuses a NIS needing over 3,000,000 bytes; the X360 arrest characters
        # (NFS_X360_ARREST=x360) make ~4.3 MB bundles, so the cap comes from the runtime.
        if self.calls and i.address == 0x0044CD0A:
            assert insn_fingerprint(i.bytes) == "a0f21a2231e812ca" and m == "cmp", "NIS size cap instruction changed"
            return [l.replace("0x2DC6C0u", "x360_arrest_nis_cap()") for l in self.arith(i, m)]
        # InitializeSoundDriver sizes the Audio Memory Pool 0x567000 (0x563000 + 0x4000). EAXAemsManager::InitiateLoad
        # (004CFFA0) refuses a bank that does not fit it (-4); the X360 sound mix serves 48 kHz PCM16 banks several times
        # the PC's EA-XA size, so the pool size comes from the runtime (x360audio.c: larger with the mix on).
        if self.calls and i.address == 0x004CC3A9:
            assert insn_fingerprint(i.bytes) == "450dfbe2ef758e55" and m == "and", "audio pool size instruction changed"
            return [f"{{ uint32_t r = (uint32_t)(c->eax & x360audio_pool_size()); cf = 0; of = 0; {self.szp('r', 4)}",
                    "  c->eax = r;", "}"]
        if m.startswith("rep "):
            return self.rep(i, m[4:])
        if m.startswith("repne "):
            return self.insn_repe(i, stop_if="zf")
        if m.startswith("repe "):
            return self.insn_repe(i, stop_if="!zf")
        if m == "int3":
            # A breakpoint (__break 0045CD20 and the inline asserts; SpeedScript errors reach it in single player).
            # Retail without a debugger would stop; the port acts like a debugger's "continue": log the site under
            # diag and run on to the next instruction (bFree 00465760 continues with three pops, ADD ESP,20h, RET).
            # A bare return here left the caller's esp 4+ bytes low (split-screen: a return address passed as an
            # entry count, 154 MB bMalloc). Only padding int3s, with nothing after them to continue to, return.
            log = f"guest_breakpoint(c, 0x{i.address:08X}u);"
            return [f"{log} return; /* padding int3 */" if i.address in self.int3_stops else log]
        if m in ("nop", "fnop", "wait", "fwait") or m.startswith("nop"):
            return []
        if m == "mov":
            return [self.wr(i, ops[0], self.rd(i, ops[1], ops[0].size))]
        if m in ("movzx", "movsx"):
            src = self.rd(i, ops[1])
            if m == "movsx":
                src = f"({CTYPE[ops[0].size]})({STYPE[ops[0].size]})({STYPE[ops[1].size]}){src}"
            return [self.wr(i, ops[0], src)]
        if m == "lea":
            return [self.wr(i, ops[0], self.ea(i, ops[1]))]
        if m == "push":
            v = self.rd(i, ops[0], 4) if ops[0].type != CS_OP_IMM else f"0x{ops[0].imm & 0xFFFFFFFF:X}u"
            if ops[0].size == 2:
                raise Unsupported("push16")
            return [f"{{ uint32_t v = {v}; c->esp -= 4; M32(c->esp) = v; }}"]
        if m == "pop":
            n = ops[0].size
            return [f"{{ {CTYPE[n]} v = M{8*n}(c->esp); c->esp += {n}; {self.wr(i, ops[0], 'v')} }}"]
        if m == "xchg":
            a, b = self.rd(i, ops[0]), self.rd(i, ops[1])
            t = CTYPE[ops[0].size]
            return [f"{{ {t} ta = {a}, tb = {b}; {self.wr(i, ops[0], 'tb')} {self.wr(i, ops[1], 'ta')} }}"]
        if m[:4] in ("lods", "scas", "cmps") and m[4:] in STR_N:
            n = STR_N[m[4:]]
            adv = f"c->df ? -{n} : {n}"
            if m.startswith("lods"):
                keep = {1: "0xFFFFFF00u", 2: "0xFFFF0000u", 4: "0"}[n]
                return [f"c->eax = (c->eax & {keep}) | M{8 * n}(c->esi); c->esi += {adv};"]
            if m.startswith("scas"):
                return ["{ " + str_cmp(n, STR_A[n], f"M{8 * n}(c->edi)", f"c->edi += {adv};") + " }"]
            return ["{ " + str_cmp(n, f"M{8 * n}(c->esi)", f"M{8 * n}(c->edi)", f"c->esi += {adv}; c->edi += {adv};") + " }"]
        if m in ("stosd", "stosw", "stosb"):
            n = {"stosd": 4, "stosw": 2, "stosb": 1}[m]
            val = {4: "c->eax", 2: "(uint16_t)c->eax", 1: "(uint8_t)c->eax"}[n]
            return [f"M{8 * n}(c->edi) = {val}; c->edi += c->df ? -{n} : {n};"]
        if m in ("movsd", "movsw", "movsb") and not ops or (m in ("movsd", "movsw", "movsb") and ops and ops[0].type == CS_OP_MEM and i.reg_name(ops[0].mem.base) == "edi"):
            n = {"movsd": 4, "movsw": 2, "movsb": 1}[m]
            return [f"M{8 * n}(c->edi) = M{8 * n}(c->esi); c->esi += c->df ? -{n} : {n}; c->edi += c->df ? -{n} : {n};"]
        if m in ("lock xadd", "xadd"):
            s_ = ops[0].size
            T = CTYPE[s_]
            a, b = self.rd(i, ops[0]), self.rd(i, ops[1])
            return [f"{{ {T} a = {a}, b = {b}; {T} r = ({T})(a + b); cf = r < a; of = (((a ^ r) & (b ^ r)) >> {BITS[s_] - 1}) & 1; "
                    f"af = ((a ^ b ^ r) >> 4) & 1; {self.szp('r', s_)} {self.wr(i, ops[1], 'a')} {self.wr(i, ops[0], 'r')} }}"]
        if m in ("lock cmpxchg", "cmpxchg"):
            if ops[0].size != 4:
                raise Unsupported("cmpxchg8/16")
            a = self.rd(i, ops[0])
            return [f"{{ uint32_t d = {a}; uint32_t r = c->eax - d; zf = r == 0; cf = c->eax < d; sf = r >> 31; pf = parity8(r); "
                    f"af = ((c->eax ^ d ^ r) >> 4) & 1; of = ((c->eax ^ d) & (c->eax ^ r)) >> 31; if (zf) {{ {self.wr(i, ops[0], self.rd(i, ops[1]))} }} else c->eax = d; }}"]
        if m in ("lock inc", "lock dec", "lock add", "lock sub", "lock or", "lock and", "lock xchg"):
            return self.insn(_Strip(i), fstart, fend)
        if m == "emms":
            return []
        if m == "pushfd":
            return ["{ uint32_t f = 0x202u | cf | pf << 2 | af << 4 | zf << 6 | sf << 7 | (c->df ? 0x400u : 0) | of << 11 | c->eflags_ext; c->esp -= 4; M32(c->esp) = f; }"]
        if m == "popfd":
            return ["{ uint32_t f = M32(c->esp); c->esp += 4; cf = f & 1; pf = f >> 2 & 1; af = f >> 4 & 1; zf = f >> 6 & 1; sf = f >> 7 & 1; of = f >> 11 & 1; "
                    "c->df = f >> 10 & 1; c->eflags_ext = f & 0x240000u; }"]
        if m == "pushal":
            return ["{ uint32_t s0 = c->esp; uint32_t v[8] = {c->eax, c->ecx, c->edx, c->ebx, s0, c->ebp, c->esi, c->edi}; "
                    "for (int k = 0; k < 8; ++k) { c->esp -= 4; M32(c->esp) = v[k]; } }"]
        if m == "popal":
            return ["{ c->edi = M32(c->esp); c->esi = M32(c->esp + 4); c->ebp = M32(c->esp + 8); c->ebx = M32(c->esp + 16); "
                    "c->edx = M32(c->esp + 20); c->ecx = M32(c->esp + 24); c->eax = M32(c->esp + 28); c->esp += 32; }"]
        if m == "cpuid":
            return ["host_cpuid(c);"]
        if m == "rdtsc":
            # a 3 GHz time-stamp counter: host_ticks() counts nanoseconds, and the game rates the CPU (and from it the
            # default video settings) by counting rdtsc ticks over 50 ms; anything that calibrates against it stays right
            return ["{ uint64_t t = host_ticks() * 3; c->eax = (uint32_t)t; c->edx = (uint32_t)(t >> 32); }"]
        if m == "leave":
            return ["c->esp = c->ebp; c->ebp = M32(c->esp); c->esp += 4;"]
        if m == "cdq":
            return ["c->edx = (c->eax & 0x80000000u) ? 0xFFFFFFFFu : 0;"]
        if m == "cwd":
            return ["c->edx = (c->edx & 0xFFFF0000u) | ((c->eax & 0x8000u) ? 0xFFFFu : 0);"]
        if m == "cwde":
            return ["c->eax = (uint32_t)(int32_t)(int16_t)c->eax;"]
        if m == "cbw":
            return ["c->eax = (c->eax & 0xFFFF0000u) | (uint16_t)(int16_t)(int8_t)c->eax;"]
        if m in ("add", "sub", "cmp", "adc", "sbb"):
            return self.arith(i, m)
        if m in ("and", "or", "xor", "test"):
            s = ops[0].size
            op = {"and": "&", "or": "|", "xor": "^", "test": "&"}[m]
            a, b = self.rd(i, ops[0]), self.rd(i, ops[1], s)
            out = [f"{{ {CTYPE[s]} r = ({CTYPE[s]})({a} {op} {b}); cf = 0; of = 0; {self.szp('r', s)}"]
            if m != "test":
                out.append("  " + self.wr(i, ops[0], "r"))
            out.append("}")
            return out
        if m in ("inc", "dec"):
            s = ops[0].size
            a = self.rd(i, ops[0])
            d = "+" if m == "inc" else "-"
            ovf = f"r == ({CTYPE[s]})0x{1 << (BITS[s] - 1):X}u" if m == "inc" else f"r == ({CTYPE[s]})0x{(1 << (BITS[s] - 1)) - 1:X}u"
            return [f"{{ {CTYPE[s]} a = {a}, r = ({CTYPE[s]})(a {d} 1); af = ((a ^ 1 ^ r) >> 4) & 1; of = {ovf}; {self.szp('r', s)} {self.wr(i, ops[0], 'r')} }}"]
        if m == "neg":
            s = ops[0].size
            a = self.rd(i, ops[0])
            return [f"{{ {CTYPE[s]} a = {a}; {CTYPE[s]} r = ({CTYPE[s]})(0 - a); cf = a != 0; "
                    f"af = ((a ^ r) >> 4) & 1; of = a == ({CTYPE[s]})0x{1 << (BITS[s] - 1):X}u; {self.szp('r', s)} {self.wr(i, ops[0], 'r')} }}"]
        if m == "not":
            return [self.wr(i, ops[0], f"~{self.rd(i, ops[0])}")]
        if m in ("shl", "sal", "shr", "sar", "rol", "ror"):
            return self.shift(i, m)
        if m in ("shld", "shrd"):
            return self.shiftd(i, m)
        if m == "imul":
            return self.imul(i)
        if m in ("mul",):
            s = ops[0].size
            if s == 1:
                return [f"{{ uint32_t r = (c->eax & 0xFF) * (uint32_t){self.rd(i, ops[0])}; c->eax = (c->eax & 0xFFFF0000u) | (r & 0xFFFF); cf = of = (r >> 8) != 0; }}"]
            if s == 2:
                return [f"{{ uint32_t r = (c->eax & 0xFFFF) * (uint32_t){self.rd(i, ops[0])}; c->eax = (c->eax & 0xFFFF0000u) | (r & 0xFFFF); "
                        "c->edx = (c->edx & 0xFFFF0000u) | (r >> 16); cf = of = (r >> 16) != 0; }"]
            return [f"{{ uint64_t r = (uint64_t)c->eax * {self.rd(i, ops[0])}; c->eax = (uint32_t)r; c->edx = (uint32_t)(r >> 32); cf = of = c->edx != 0; }}"]
        if m in ("div", "idiv"):
            s = ops[0].size
            bits = BITS[s]
            raw = {1: "(c->eax & 0xFFFFu)", 2: "((c->edx & 0xFFFFu) << 16 | (c->eax & 0xFFFFu))",
                   4: "((uint64_t)c->edx << 32 | c->eax)"}[s]
            fault = f"guest_divide_error(c, 0x{i.address:08X}u);"
            if m == "div":
                setup = f"uint64_t n = {raw}, d = {self.rd(i, ops[0])};"
                check = f"!d || n / d > 0x{(1 << bits)-1:X}ull"
                ty = "uint64_t"
            else:
                nt = {1:"int16_t",2:"int32_t",4:"int64_t"}[s]
                setup = f"int64_t n = ({nt})({raw}), d = ({STYPE[s]}){self.rd(i, ops[0])};"
                check = f"!d || (n == INT64_MIN && d == -1) || n / d < -{1 << (bits-1)}ll || n / d > {(1 << (bits-1))-1}ll"
                ty = "int64_t"
            if s == 1:
                write = "c->eax = (c->eax & 0xFFFF0000u) | ((uint32_t)q & 0xFFu) | (((uint32_t)r & 0xFFu) << 8);"
            elif s == 2:
                write = "c->eax = (c->eax & 0xFFFF0000u) | ((uint32_t)q & 0xFFFFu); c->edx = (c->edx & 0xFFFF0000u) | ((uint32_t)r & 0xFFFFu);"
            else:
                write = "c->eax = (uint32_t)q; c->edx = (uint32_t)r;"
            # On a fault: log, then the result an ARM divide gives (what builds before audit 3 produced):
            # q 0 / r n for a zero divisor, the truncated 64-bit quotient otherwise. No C division by zero
            # or INT64_MIN / -1 is ever evaluated.
            ovf = "" if m == "div" else " if (n == INT64_MIN && d == -1) { q = n; r = 0; } else"
            return [f"{{ {setup} {ty} q, r; if ({check}) {{ {fault} if (!d) {{ q = 0; r = n; }} else{ovf} {{ q = n / d; r = n % d; }} }} "
                    f"else {{ q = n / d; r = n % d; }} {write} }}"]
        if m.startswith("set"):
            return [self.wr(i, ops[0], f"({COND[m[3:]]}) ? 1 : 0")]
        if m.startswith("cmov"):
            return [f"if ({COND[m[4:]]}) {{ {self.wr(i, ops[0], self.rd(i, ops[1]))} }}"]
        if m == "bt":
            if ops[0].type == CS_OP_MEM and ops[1].type == CS_OP_REG:
                return self.bit_string(i, m)
            a, b = self.rd(i, ops[0]), self.rd(i, ops[1], ops[0].size)
            return [f"cf = ({a} >> ({b} & {BITS[ops[0].size] - 1})) & 1;"]
        if m in ("std", "cld", "clc", "stc", "cmc"):
            return {"std": ["c->df = 1;"], "cld": ["c->df = 0;"], "clc": ["cf = 0;"], "stc": ["cf = 1;"], "cmc": ["cf = !cf;"]}[m]
        if m in ("rcl", "rcr"):
            s = ops[0].size
            T, n = CTYPE[s], BITS[s]
            cnt = (f"{ops[1].imm & 31}" if len(ops) > 1 and ops[1].type == CS_OP_IMM else "(c->ecx & 31)" if len(ops) > 1 else "1")
            step = ("{ uint32_t nc = (v >> %d) & 1; v = (%s)((v << 1) | cf); cf = nc; }" % (n - 1, T)) if m == "rcl" else \
                   ("{ uint32_t nc = v & 1; v = (%s)((v >> 1) | ((uint32_t)cf << %d)); cf = nc; }" % (T, n - 1))
            ofx = f"of = ((v >> {n - 1}) & 1) ^ cf;" if m == "rcl" else f"of = ((v >> {n - 1}) ^ (v >> {n - 2})) & 1;"
            return [f"{{ {T} v = {self.rd(i, ops[0])}; uint32_t k = {cnt}; if (k) {{ while (k--) {step} {ofx} {self.wr(i, ops[0], 'v')} }} }}"]
        if m in ("bts", "btr", "btc"):
            s = ops[0].size
            hi = BITS[s] - 1
            b = self.rd(i, ops[1], s)
            if ops[0].type == CS_OP_MEM and ops[1].type == CS_OP_REG:
                return self.bit_string(i, m)
            T = CTYPE[s]
            upd = {"bts": f"({T})(w | (({T})1 << k))", "btr": f"({T})(w & ~(({T})1 << k))", "btc": f"({T})(w ^ (({T})1 << k))"}[m]
            return [f"{{ {T} w = {self.rd(i, ops[0])}; uint32_t k = ({b}) & {hi}; cf = (w >> k) & 1; {self.wr(i, ops[0], upd)} }}"]
        if m == "xlatb":
            return ["c->eax = (c->eax & 0xFFFFFF00u) | M8(c->ebx + (c->eax & 0xFF));"]
        if m == "stmxcsr":
            return [f"M32({self.ea(i, ops[0])}) = c->mxcsr ? c->mxcsr : 0x1F80u;"]
        if m == "ldmxcsr":
            return [f"c->mxcsr = M32({self.ea(i, ops[0])});"]
        if m == "lahf":
            return ["c->eax = (c->eax & 0xFFFF00FFu) | ((uint32_t)(sf << 7 | zf << 6 | af << 4 | pf << 2 | 2 | cf) << 8);"]
        if m == "sahf":
            return ["{ uint8_t ah = (uint8_t)(c->eax >> 8); sf = ah >> 7 & 1; zf = ah >> 6 & 1; pf = ah >> 2 & 1; af = ah >> 4 & 1; cf = ah & 1; }"]
        # ---- control flow ----
        if m == "jmp":
            if ops[0].type == CS_OP_IMM:
                t = ops[0].imm
                if t in self.seen and (t == fstart or t not in self.functions):
                    return [f"goto L_{t:08X};"]
                if self.calls and t in self.functions:
                    self.callees.add(t)
                    return [f"{self.flags_to(t)} ABI_SKIP(); f_{t:08X}(c); return; /* tail jump */"]
                raise Unsupported("tail jump")
            if i.address in self.tables:
                idx = i.reg_name(ops[0].mem.index)
                cases = " ".join(f"case 0x{k & 0xFFFFFFFF:X}u: goto L_{t:08X};" if t in self.seen else f"case 0x{k & 0xFFFFFFFF:X}u: {self.flags_to(t)} ABI_SKIP(); f_{t:08X}(c); return;"
                                 for k, t in sorted(self.tables[i.address].items()))
                self.callees.update(t for t in self.tables[i.address].values() if t not in self.seen)
                return [f"switch (c->{idx}) {{ {cases} default: bad_jump(c, 0x{i.address:08X}u); return; }}"]
            if self.calls:
                return [f"ABI_SKIP(); dispatch(c, {self.rd(i, ops[0], 4)}); return; /* indirect tail jump */"]
            raise Unsupported("indirect jmp")
        if m in ("loop", "loope", "loopne"):
            t = ops[0].imm
            if t not in self.seen:
                raise Unsupported(m + " out of function")
            cond = {"loop": "c->ecx", "loope": "c->ecx && zf", "loopne": "c->ecx && !zf"}[m]
            return [f"c->ecx--; if ({cond}) goto L_{t:08X};"]
        if m == "jecxz":
            if ops[0].imm not in self.seen:
                raise Unsupported("jecxz out of function")
            return [f"if (c->ecx == 0) goto L_{ops[0].imm:08X};"]
        if m.startswith("j") and m[1:] in COND:
            t = ops[0].imm
            if not (t in self.seen and (t == fstart or t not in self.functions)):
                if self.calls and t in self.functions:
                    self.callees.add(t)
                    return [f"if ({COND[m[1:]]}) {{ {self.flags_to(t)} ABI_SKIP(); f_{t:08X}(c); return; }} /* conditional tail jump */"]
                raise Unsupported("jcc out of function")
            return [f"if ({COND[m[1:]]}) goto L_{t:08X};"]
        if m == "call":
            if not self.calls:
                raise Unsupported("call")
            ret = i.address + i.size
            push = f"c->esp -= 4; M32(c->esp) = 0x{ret:08X}u;"
            op = ops[0]
            if op.type == CS_OP_IMM:
                t = op.imm
                if t not in self.functions:
                    raise Unsupported("call to unknown target")
                self.callees.add(t)
                return [f"{push} f_{t:08X}(c);"]
            if op.type == CS_OP_MEM and not op.mem.base and not op.mem.index and \
                    (op.mem.disp & 0xFFFFFFFF) in self.pe.imports:
                imp = self.pe.imports[op.mem.disp & 0xFFFFFFFF]
                return [f"{push} host_import(c, 0x{op.mem.disp & 0xFFFFFFFF:08X}u); /* {imp} */"]
            # the target operand is read before the return address is pushed (matters for [esp+n])
            return [f"{{ uint32_t t = {self.rd(i, op, 4)}; {push} dispatch(c, t); }}"]
        if m == "ret":
            n = ops[0].imm if ops else 0
            return [f"ABI_CHECK(0x{fstart:08X}u); c->esp += {4 + n}; return;"]
        if m in MMX_OPS or m in ("movq", "movd", "emms", "pextrw", "pinsrw", "pmovmskb", "pshufw"):
            return self.mmx(i, m)
        if m.startswith("f"):
            return self.x87(i, m)
        raise Unsupported(m)

    # ---------- MMX ----------
    def mmx_op(self, i, op, size=8):
        if op.type == CS_OP_REG:
            r = i.reg_name(op.reg)
            if r.startswith("mm"):
                return f"c->mm[{r[2]}]"
            if r.startswith("xmm"):
                raise Unsupported("sse2 " + i.mnemonic)
            return f"(uint64_t){self.reg_read(r, 4)}"
        if op.type == CS_OP_MEM:
            return self.mem_ref(size, self.ea(i, op)) if size == 8 else f"(uint64_t){self.mem_ref(size, self.ea(i, op))}"
        if op.type == CS_OP_IMM:
            return f"(uint64_t)0x{op.imm & 0xFF:X}u"
        raise Unsupported("mmx operand")

    def mmx(self, i, m):
        ops = i.operands
        if any(op.type == CS_OP_REG and i.reg_name(op.reg).startswith("xmm") for op in ops):
            raise Unsupported("sse2 " + m)
        if m == "emms":
            return []
        if m == "movq":
            src = self.mmx_op(i, ops[1])
            if ops[0].type == CS_OP_MEM:
                return [f"M64({self.ea(i, ops[0])}) = {src};"]
            return [f"{self.mmx_op(i, ops[0])} = {src};"]
        if m == "movd":
            d, s_ = ops
            if d.type == CS_OP_REG and i.reg_name(d.reg).startswith("mm"):
                return [f"c->mm[{i.reg_name(d.reg)[2]}] = (uint64_t)(uint32_t){self.rd(i, s_, 4)};"]
            return [self.wr(i, d, f"(uint32_t)c->mm[{i.reg_name(s_.reg)[2]}]")]
        if m == "pextrw":
            return [self.wr(i, ops[0], f"(uint32_t)((c->mm[{i.reg_name(ops[1].reg)[2]}] >> {16 * (ops[2].imm & 3)}) & 0xFFFF)")]
        if m == "pinsrw":
            k = 16 * (ops[2].imm & 3)
            n = i.reg_name(ops[0].reg)[2]
            return [f"c->mm[{n}] = (c->mm[{n}] & ~(0xFFFFull << {k})) | ((uint64_t)(uint16_t){self.rd(i, ops[1], 4 if ops[1].type == CS_OP_REG else 2)} << {k});"]
        if m == "pmovmskb":
            return [self.wr(i, ops[0], f"mmx_pmovmskb(c->mm[{i.reg_name(ops[1].reg)[2]}])")]
        if m == "pshufw":
            k = ops[2].imm
            src = self.mmx_op(i, ops[1])
            lanes = " | ".join(f"((({src}) >> {16 * ((k >> 2 * j) & 3)}) & 0xFFFFull) << {16 * j}" for j in range(4))
            return [f"{{ uint64_t t = {lanes}; {self.mmx_op(i, ops[0])} = t; }}"]
        dst = self.mmx_op(i, ops[0])
        src = self.mmx_op(i, ops[1])
        return [f"{dst} = mmx_{m}({dst}, {src});"]

    def bit_string(self, i, m):
        """bt/bts/btr/btc on memory with a register bit offset: the signed offset can reach outside the operand
        (a word or dword of the bit string at ea + (offset >> 4 or 5) * size)."""
        ops = i.operands
        s = ops[0].size
        T, sh = CTYPE[s], 5 if s == 4 else 4
        o = f"(int32_t)(int16_t){self.rd(i, ops[1], 2)}" if s == 2 else f"(int32_t){self.rd(i, ops[1], 4)}"
        upd = {"bt": None, "bts": f"({T})(w | (({T})1 << k))", "btr": f"({T})(w & ~(({T})1 << k))", "btc": f"({T})(w ^ (({T})1 << k))"}[m]
        wr = f" M{8 * s}(ad) = {upd};" if upd else ""
        return [f"{{ int32_t o = {o}; uint32_t ad = (uint32_t)({self.ea(i, ops[0])} + (uint32_t)((o >> {sh}) * {s})); uint32_t k = (uint32_t)o & {8 * s - 1}; "
                f"{T} w = M{8 * s}(ad); cf = (w >> k) & 1;{wr} }}"]

    def insn_repe(self, i, stop_if):
        """repe/repne scas and cmps, in the direction DF gives; stop_if: the zf test that ends the loop."""
        m = i.mnemonic.split()[1]
        if m[:4] not in ("scas", "cmps") or m[4:] not in STR_N:
            raise Unsupported(i.mnemonic)
        n = STR_N[m[4:]]
        st = f"int32_t st = c->df ? -{n} : {n};"
        if m.startswith("scas"):
            step = str_cmp(n, STR_A[n], f"M{8 * n}(c->edi)", "c->edi += st; c->ecx--;")
        else:
            step = str_cmp(n, f"M{8 * n}(c->esi)", f"M{8 * n}(c->edi)", "c->esi += st; c->edi += st; c->ecx--;")
        return [f"{{ {st} while (c->ecx) {{ {step} if ({stop_if}) break; }} }}"]

    def arith(self, i, m):
        ops = i.operands
        s = ops[0].size
        T = CTYPE[s]
        a, b = self.rd(i, ops[0]), self.rd(i, ops[1], s)
        hi = BITS[s] - 1
        if m in ("add", "adc"):
            carry = " + cf" if m == "adc" else ""
            out = [f"{{ {T} a = {a}, b = {b}; uint64_t w = (uint64_t)a + b{carry}; {T} r = ({T})w; "
                   f"cf = (w >> {BITS[s]}) & 1; of = (((a ^ r) & (b ^ r)) >> {hi}) & 1; af = ((a ^ b ^ r) >> 4) & 1; {self.szp('r', s)}"]
        else:
            borrow = " - cf" if m == "sbb" else ""
            out = [f"{{ {T} a = {a}, b = {b}; uint64_t w = (uint64_t)a - b{borrow}; {T} r = ({T})w; "
                   f"cf = (w >> 63) & 1; of = (((a ^ b) & (a ^ r)) >> {hi}) & 1; af = ((a ^ b ^ r) >> 4) & 1; {self.szp('r', s)}"]
        if m != "cmp":
            out.append("  " + self.wr(i, ops[0], "r"))
        out.append("}")
        return out

    def shift(self, i, m):
        ops = i.operands
        s = ops[0].size
        T = CTYPE[s]
        n = BITS[s]
        a = self.rd(i, ops[0])
        cnt = (f"{ops[1].imm & 31}" if len(ops) > 1 and ops[1].type == CS_OP_IMM else
               "(c->ecx & 31)" if len(ops) > 1 else "1")
        body = {
            "shl": f"r = ({T})(a << k); cf = (a >> ({n} - k)) & 1; of = ((r >> {n - 1}) & 1) ^ cf; {self.szp('r', s)}",
            "sal": f"r = ({T})(a << k); cf = (a >> ({n} - k)) & 1; of = ((r >> {n - 1}) & 1) ^ cf; {self.szp('r', s)}",
            "shr": f"r = ({T})(a >> k); cf = (a >> (k - 1)) & 1; of = (a >> {n - 1}) & 1; {self.szp('r', s)}",
            "sar": f"r = ({T})(({STYPE[s]})a >> k); cf = (({STYPE[s]})a >> (k - 1)) & 1; of = 0; {self.szp('r', s)}",
            "rol": f"k %= {n}; r = k ? ({T})((a << k) | (a >> ({n} - k))) : a; cf = r & 1; of = ((r >> {n - 1}) & 1) ^ cf;",
            "ror": f"k %= {n}; r = k ? ({T})((a >> k) | (a << ({n} - k))) : a; cf = (r >> {n - 1}) & 1; of = ((r >> {n - 1}) ^ (r >> {n - 2})) & 1;",
        }[m]
        if s < 4 and m in ("shl", "sal", "shr", "sar"):
            # for 8/16-bit operands the count can exceed the width; widen to avoid UB, keep x86 semantics
            body = body.replace("(a >> (", "((uint32_t)a >> (").replace("(a << k)", "((uint32_t)a << k)")
        return [f"{{ {T} a = {a}, r = a; uint32_t k = {cnt}; if (k) {{ {body} {self.wr(i, ops[0], 'r')} }} }}"]

    def shiftd(self, i, m):
        ops = i.operands
        if ops[0].size != 4:
            raise Unsupported("shld16")
        a, b = self.rd(i, ops[0]), self.rd(i, ops[1])
        cnt = f"{ops[2].imm & 31}" if ops[2].type == CS_OP_IMM else "(c->ecx & 31)"
        if m == "shld":
            body = "r = (a << k) | (b >> (32 - k)); cf = (a >> (32 - k)) & 1;"
        else:
            body = "r = (a >> k) | (b << (32 - k)); cf = (a >> (k - 1)) & 1;"
        return [f"{{ uint32_t a = {a}, b = {b}, r; uint32_t k = {cnt}; if (k) {{ {body} if (k == 1) of = ((a ^ r) >> 31) & 1; {self.szp('r', 4)} {self.wr(i, ops[0], 'r')} }} }}"]

    def imul(self, i):
        ops = i.operands
        if len(ops) == 1:
            if ops[0].size == 1:
                return [f"{{ int32_t r = (int32_t)(int8_t)c->eax * (int8_t){self.rd(i, ops[0])}; c->eax = (c->eax & 0xFFFF0000u) | ((uint32_t)r & 0xFFFF); cf = of = r != (int8_t)r; }}"]
            if ops[0].size == 2:
                return [f"{{ int32_t r = (int32_t)(int16_t)c->eax * (int16_t){self.rd(i, ops[0])}; c->eax = (c->eax & 0xFFFF0000u) | ((uint32_t)r & 0xFFFF); "
                        "c->edx = (c->edx & 0xFFFF0000u) | (((uint32_t)r >> 16) & 0xFFFF); cf = of = r != (int16_t)r; }"]
            return [f"{{ int64_t r = (int64_t)(int32_t)c->eax * (int32_t){self.rd(i, ops[0])}; c->eax = (uint32_t)r; "
                    "c->edx = (uint32_t)((uint64_t)r >> 32); cf = of = r != (int64_t)(int32_t)r; }"]
        s = ops[0].size
        if s == 2:
            a = self.rd(i, ops[0]) if len(ops) == 2 else self.rd(i, ops[1])
            b = self.rd(i, ops[1]) if len(ops) == 2 else f"(uint16_t){ops[2].imm & 0xFFFF}u"
            return [f"{{ int32_t r = (int32_t)(int16_t)({a}) * (int16_t)({b}); cf = of = r != (int16_t)r; {self.wr(i, ops[0], '(uint16_t)r')} }}"]
        a = self.rd(i, ops[0]) if len(ops) == 2 else self.rd(i, ops[1])
        b = self.rd(i, ops[1]) if len(ops) == 2 else f"(uint32_t){ops[2].imm & 0xFFFFFFFF}u"
        return [f"{{ int64_t r = (int64_t)(int32_t)({a}) * (int32_t)({b}); cf = of = r != (int64_t)(int32_t)r; "
                f"{self.wr(i, ops[0], '(uint32_t)r')} }}"]

    def rep(self, i, m):
        if m in ("movsd", "movsb", "movsw"):
            n = {"movsd": 4, "movsb": 1, "movsw": 2}[m]
            return [f"{{ int32_t st = c->df ? -{n} : {n}; while (c->ecx) {{ M{8 * n}(c->edi) = M{8 * n}(c->esi); c->esi += st; c->edi += st; c->ecx--; }} }}"]
        if m in ("stosd", "stosb", "stosw"):
            n = {"stosd": 4, "stosb": 1, "stosw": 2}[m]
            val = {4: "c->eax", 2: "(uint16_t)c->eax", 1: "(uint8_t)c->eax"}[n]
            return [f"{{ int32_t st = c->df ? -{n} : {n}; while (c->ecx) {{ M{8 * n}(c->edi) = {val}; c->edi += st; c->ecx--; }} }}"]
        raise Unsupported("rep " + m)

    # ---------- x87 ----------
    def fsrc(self, i, op):
        """value of an FPU source operand as C double"""
        if op.type == CS_OP_REG:
            r = i.reg_name(op.reg)
            if r.startswith("st"):
                return f"ST({int(r[3]) if len(r) > 2 else 0})"
            raise Unsupported("fpu reg " + r)
        if op.type == CS_OP_MEM:
            a = self.ea(i, op)
            if op.size == 4:
                return f"(double)MF32({a})"
            if op.size == 8:
                return f"MF64({a})"
            raise Unsupported(f"fpu mem{op.size}")
        raise Unsupported("fpu operand")

    def streg(self, i, op):
        r = i.reg_name(op.reg)
        return int(r[3]) if len(r) > 2 else 0

    def x87(self, i, m):
        return self._x87(i, m)

    def _x87(self, i, m):
        ops = i.operands
        R = "x87_round(c, {})".format
        if m == "fld":
            if ops[0].type == CS_OP_MEM and ops[0].size == 10:
                return [f"x87_push(c, x87_load80(G + {self.ea(i, ops[0])}));"]
            return [f"{{ double v = {self.fsrc(i, ops[0])}; x87_push(c, v); }}"]
        if m in ("fst", "fstp"):
            op = ops[0]
            pop = " x87_pop(c);" if m == "fstp" else ""
            if op.type == CS_OP_REG:
                return [f"ST({self.streg(i, op)}) = ST(0);{pop}"]
            a = self.ea(i, op)
            if op.size == 4:
                return [f"MF32({a}) = (float)ST(0);{pop}"]
            if op.size == 8:
                return [f"MF64({a}) = ST(0);{pop}"]
            return [f"x87_store80(G + {a}, ST(0));{pop}"]
        if m == "fild":
            a = self.ea(i, ops[0])
            src = {2: f"(int16_t)M16({a})", 4: f"(int32_t)M32({a})", 8: f"(int64_t)M64({a})"}[ops[0].size]
            return [f"x87_push(c, (double){src});"]
        if m in ("fist", "fistp"):
            a = self.ea(i, ops[0])
            pop = " x87_pop(c);" if m == "fistp" else ""
            conv = {2: f"M16({a}) = (uint16_t)x87_to_i16(c, ST(0));", 4: f"M32({a}) = (uint32_t)x87_to_i32(c, ST(0));",
                    8: f"M64({a}) = (uint64_t)x87_to_i64(c, ST(0));"}[ops[0].size]
            return [conv + pop]
        if m == "fldz":
            return ["x87_push(c, 0.0);"]
        if m == "fld1":
            return ["x87_push(c, 1.0);"]
        if m == "fchs":
            return ["ST(0) = -ST(0);"]
        if m == "fabs":
            return ["ST(0) = fabs(ST(0));"]
        if m == "fsqrt":
            return ["ST(0) = x87_round(c, x87_nan(sqrt(ST(0)), ST(0), 0.0));"]
        if m in ("fsin", "fcos"):
            return [f"if (x87_trig_range(c, ST(0))) {{ ST(0) = {m[1:]}(ST(0)); }}"]
        if m == "frndint":
            return ["ST(0) = x87_rint(c, ST(0));"]
        if m == "fxch":
            k = self.streg(i, ops[-1]) if ops else 1     # capstone lists the implicit st(0) first
            return [f"{{ double t = ST(0); ST(0) = ST({k}); ST({k}) = t; }}"]
        if m in ("fadd", "fsub", "fsubr", "fmul", "fdiv", "fdivr", "faddp", "fsubp", "fsubrp", "fmulp", "fdivp", "fdivrp",
                 "fiadd", "fisub", "fimul", "fidiv", "fisubr", "fidivr"):
            return self.farith(i, m)
        if m in ("fcom", "fcomp", "fucom", "fucomp", "ficom", "ficomp"):
            if not ops:
                src = "ST(1)"
            elif m.startswith("fi"):
                a = self.ea(i, ops[0])
                src = {2: f"(double)(int16_t)M16({a})", 4: f"(double)(int32_t)M32({a})"}[ops[0].size]
            else:
                src = self.fsrc(i, ops[0])
            pop = " x87_pop(c);" if m.endswith("p") else ""
            return [f"x87_compare(c, ST(0), {src}, {int(m.startswith('fu'))});{pop}"]
        if m in ("fcompp", "fucompp"):
            return [f"x87_compare(c, ST(0), ST(1), {int(m.startswith('fu'))}); x87_pop(c); x87_pop(c);"]
        if m in ("fcomi", "fcomip", "fucomi", "fucomip"):
            k = self.streg(i, ops[1]) if len(ops) > 1 else 1
            pop = " x87_pop(c);" if m.endswith("p") else ""
            return [f"{{ double a = ST(0), b = ST({k}); int un = isnan(a) || isnan(b); "
                    f"zf = un || a == b; pf = un; cf = un || a < b; of = sf = af = 0; }}{pop}"]
        if m == "ftst":
            return ["x87_compare(c, ST(0), 0.0, 0);"]
        if m == "fnstsw":
            if ops and ops[0].type == CS_OP_REG:
                return ["c->eax = (c->eax & 0xFFFF0000u) | x87_sw(c);"]
            return [f"M16({self.ea(i, ops[0])}) = x87_sw(c);"]
        if m == "fnstcw":
            return [f"M16({self.ea(i, ops[0])}) = c->cw;"]
        if m == "fldcw":
            return [f"c->cw = M16({self.ea(i, ops[0])});"]
        if m == "fstp" and not ops:
            return ["x87_pop(c);"]
        if m == "ffree":
            return []
        if m == "fnclex":
            return ["c->sw &= 0x7F00;"]
        if m == "fninit":
            return ["c->cw = 0x037F; c->sw = 0; c->top = 0;"]
        consts = {"fldpi": "3.14159265358979323846", "fldl2e": "1.44269504088896340736", "fldln2": "0.69314718055994530942",
                  "fldlg2": "0.30102999566398119521", "fldl2t": "3.32192809488736234787"}
        if m in consts:
            return [f"x87_push(c, {consts[m]});"]
        if m == "fpatan":
            return ["ST(1) = atan2(ST(1), ST(0)); x87_pop(c);"]
        if m == "fptan":
            return ["if (x87_trig_range(c, ST(0))) { ST(0) = tan(ST(0)); x87_push(c, 1.0); }"]
        if m == "fsincos":
            return ["if (x87_trig_range(c, ST(0))) { double v = ST(0); ST(0) = sin(v); x87_push(c, cos(v)); }"]
        if m in ("fprem", "fprem1"):
            return [f"x87_prem(c, {int(m == 'fprem1')});"]
        if m == "fscale":
            return ["ST(0) = ldexp(ST(0), (int)trunc(ST(1)));"]
        if m == "f2xm1":
            return ["ST(0) = exp2(ST(0)) - 1.0;"]   # expm1 form not shown: Unicorn computes pow(2,x)-1 too (audit 3 #10)
        if m == "fyl2x":
            return ["ST(1) = ST(1) * log2(ST(0)); x87_pop(c);"]
        if m == "fyl2xp1":
            return ["ST(1) = ST(1) * log2(ST(0) + 1.0); x87_pop(c);"]
        if m == "fxam":
            return ["{ double v = ST(0); c->sw &= ~0x4700; if (signbit(v)) c->sw |= 0x200; "
                    "if (isnan(v)) c->sw |= 0x100; else if (isinf(v)) c->sw |= 0x500; else if (v == 0) c->sw |= 0x4000; "
                    "else c->sw |= 0x400; }"]
        raise Unsupported(m)

    def farith(self, i, m):
        ops = i.operands
        base = m[1:].rstrip("p") if not m.startswith("fi") else m[2:]
        expr = {"add": "a + b", "sub": "a - b", "subr": "b - a", "mul": "a * b", "div": "a / b", "divr": "b / a"}[base]

        rc = f"(c->cw & 0xC00) ? x87_arith_rc(c, a, b, {dict(add=0,sub=1,subr=2,mul=3,div=4,divr=5)[base]}) : " if getattr(self, "rc_aware", True) else ""
        def emit(dst, a, b, pop=""):
            return [f"{{ double a = {a}, b = {b}; {dst} = x87_round(c, x87_nan({rc}({expr}), a, b)); }}{pop}"]
        if m.startswith("fi"):
            ea = self.ea(i, ops[0])
            src = {2: f"(double)(int16_t)M16({ea})", 4: f"(double)(int32_t)M32({ea})"}[ops[0].size]
            return emit("ST(0)", "ST(0)", src)
        if m.endswith("p"):
            k = self.streg(i, ops[0]) if ops else 1
            return emit(f"ST({k})", f"ST({k})", "ST(0)", " x87_pop(c);")
        if len(ops) == 1:
            return emit("ST(0)", "ST(0)", self.fsrc(i, ops[0]))
        d, s_ = self.streg(i, ops[0]), self.streg(i, ops[1])
        return emit(f"ST({d})", f"ST({d})", f"ST({s_})")


class _Strip:
    """An instruction viewed without its lock prefix (single-threaded semantics are the same here)."""
    def __init__(self, i):
        self._i = i
        self.mnemonic = i.mnemonic.split(" ", 1)[1]

    def __getattr__(self, k):
        return getattr(self._i, k)
