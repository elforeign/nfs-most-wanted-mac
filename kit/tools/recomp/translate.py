#!/usr/bin/env python3
"""Static x86 -> C recompiler driven by games/<id>/game.toml.

Reads the game's Ghidra listings (game.toml [translate].listings/functions/*.asm)
plus the PE itself and emits one C function `void fn_XXXXXXXX(X86 *c)` per
original function into build/recomp/gen/chunk_NNN.c, together with funcs.h
(prototypes) and table.c (sorted address table + recomp_call dispatch).

Usage:
    .venv/bin/python tools/recomp/translate.py [--out build/recomp/gen]
    .venv/bin/python tools/recomp/translate.py --only 00401000 00586000
    .venv/bin/python tools/recomp/translate.py --check-flags   # liveness study

The semantics live in runtime/x86.h, which the generated code
includes. Instructions use general translations. The explicitly audited visual
clock reads below expose an identity-by-default runtime seam; timing changes
are enabled by the native host, never by the baseline/differential build.
"""

import argparse
import json
import os
import re
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path
from bisect import bisect_right

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import game_config  # noqa: E402

FUNCS_PER_CHUNK = 200

# Set by configure(): the game's listings, binary and curated symbols, and the
# audited reads of its animation counter (game.toml [translate].volatile_reads:
# reads that only select a visual phase or blink, never interpolation,
# simulation stamps, FPS measurement or input timing). Nothing is configured
# until configure() runs: main() does it from --game, tests do it themselves.
LISTINGS = FUNCS_TSV = BINARY = CURATED = None
ANIMATION_COUNTER = 0
VISUAL_ANIMATION_READS = frozenset()
EXTRA_ENTRY_POINTS = frozenset()
RESUMABLE_STACKS = False
FUNCTION_ALIGNMENT = 16

#: game.toml [translate] rewrites: an instruction's memory operand moved to a
#: free address, and the words the loader seeds before the game runs.
OPERAND_REDIRECTS = {}
INSTRUCTION_PATCHES = {}
#: [translate] poll_checkpoints: {at, to} branch edges of a title's verified busy-wait loops. When the branch
#: at `at` is taken toward `to`, the guest thread first passes a scheduler checkpoint, so a polling loop
#: with no import call in it still lets the baton reach the thread it is waiting for. Guest state is
#: untouched; the branch and every instruction of the next iteration are as the listing has them.
POLL_CHECKPOINTS = {}
POLL_CHECKPOINTS_APPLIED = set()
PATCHES_APPLIED = set()
DATA_SEEDS = []


def patch_instructions(insns):
    """Replace every patched instruction in `insns`, in place."""
    if not INSTRUCTION_PATCHES:
        return insns
    for k, ins in enumerate(insns):
        text = INSTRUCTION_PATCHES.get(ins.addr)
        if text is None:
            continue
        repl = parse_listing_text("%08x  %s\n" % (ins.addr, text))[0]
        insns[k] = repl
        PATCHES_APPLIED.add(ins.addr)
    return insns


#: Every global the generated tables define starts with this; a module
#: translation (--module) uses recomp_<key>_ so its tables link beside the
#: main image's and register themselves with the runtime (x86.h RecompModule).
SYMBOL_PREFIX = "recomp_"
AUX_MODULE = None


def configure_module(cfg, key):
    """Point the translator at one auxiliary module of the game instead of its
    executable: its own listings, binary and alignment; no hooks, no audited
    reads, no curated symbols (those describe the executable)."""
    global LISTINGS, FUNCS_TSV, BINARY, CURATED, ANIMATION_COUNTER, VISUAL_ANIMATION_READS
    global EXTRA_ENTRY_POINTS, FUNCTION_ALIGNMENT, SYMBOL_PREFIX, AUX_MODULE
    global RESUMABLE_STACKS
    RESUMABLE_STACKS = cfg["translate"].get("resumable_stacks", False)
    mods = {m["key"]: m for m in cfg.get("aux_modules", [])}
    if key not in mods:
        raise SystemExit("game.toml has no [modules.aux.%s]" % key)
    mod = mods[key]
    listings = str(mod["listings_path"])
    LISTINGS = os.path.join(listings, "functions")
    FUNCS_TSV = os.path.join(listings, "functions.tsv")
    BINARY = str(mod["path"])
    CURATED = None
    ANIMATION_COUNTER = 0
    VISUAL_ANIMATION_READS = frozenset()
    EXTRA_ENTRY_POINTS = frozenset(mod.get("entry_points", ()))
    FUNCTION_ALIGNMENT = mod["function_alignment"]
    SYMBOL_PREFIX = "recomp_%s_" % key
    AUX_MODULE = mod


def read_discovered(path):
    """Addresses a run reached that its translation did not carry, as
    runtime/discovery.cpp writes them: `<address> <call|jump> <from> <hits>`
    per line, `#` comments. Returns them in file order, which is address
    order, so a regeneration sees the same set whatever the run did."""
    found = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        try:
            found.append(int(line.split()[0], 16))
        except ValueError:
            raise TranslateError("%s: %r is not an address a run recorded" % (path, line))
    return found


def configure(cfg):
    """Point the translator at one game's listings, binary and audited reads."""
    global LISTINGS, FUNCS_TSV, BINARY, CURATED, ANIMATION_COUNTER, VISUAL_ANIMATION_READS
    listings = str(cfg["listings_path"])
    LISTINGS = os.path.join(listings, "functions")
    FUNCS_TSV = os.path.join(listings, "functions.tsv")
    BINARY = str(cfg["developer_exe_path"])
    CURATED = os.path.join(str(cfg["dir"]), cfg["translate"].get("globals", "globals.toml"))
    ANIMATION_COUNTER = cfg["translate"]["animation_counter"]
    VISUAL_ANIMATION_READS = frozenset(cfg["translate"].get("volatile_reads", ()))
    global EXTRA_ENTRY_POINTS, FUNCTION_ALIGNMENT
    global RESUMABLE_STACKS
    RESUMABLE_STACKS = cfg["translate"].get("resumable_stacks", False)
    EXTRA_ENTRY_POINTS = frozenset(int(a) for a in cfg["translate"].get("entry_points", ()))
    FUNCTION_ALIGNMENT = cfg["translate"].get("function_alignment", 16)
    global OPERAND_REDIRECTS, INSTRUCTION_PATCHES, DATA_SEEDS
    OPERAND_REDIRECTS = {int(r["at"]): (int(r["from"]), int(r["to"]))
                         for r in cfg["translate"].get("operand_redirects", ())}
    INSTRUCTION_PATCHES = {int(r["at"]): str(r["text"])
                           for r in cfg["translate"].get("instruction_patches", ())}
    global POLL_CHECKPOINTS
    POLL_CHECKPOINTS = {int(r["at"]): int(r["to"]) for r in cfg["translate"].get("poll_checkpoints", ())}
    DATA_SEEDS = []
    for r in cfg["translate"].get("data_seeds", ()):
        if "float" in r:
            value = struct.unpack("<I", struct.pack("<f", float(r["float"])))[0]
        else:
            value = int(r["value"]) & 0xFFFFFFFF
        DATA_SEEDS.append((int(r["addr"]), value))


def visual_animation_read(addr, body):
    if addr not in VISUAL_ANIMATION_READS:
        return body
    # Operate on the translated read, not the guest global: nested draws and
    # yielded guest threads must always see the original frame ID in memory.
    found = 0
    result = []
    pattern = r"rd(8|32)\(0x%xu\)" % ANIMATION_COUNTER
    replacement = "((uint%%s_t)recomp_visual_animation_tick(rd32(0x%xu)))" % ANIMATION_COUNTER
    def replace(match):
        return replacement % match[1]
    for line in body:
        line, count = re.subn(pattern, replace, line)
        found += count
        result.append(line)
    if found != 1:
        raise TranslateError("visual animation read %08x no longer matches its audited operand" % addr)
    return result

#: How a recovered block was found, and whether that is a structural fact or
#: a guess.
#:
#: A jump-table slot and an __initterm entry NAME the address: the program
#: itself will load and call it, so the block is established code and must
#: never be withdrawn.  If its callee cannot be resolved that is a translator
#: gap and has to fail the build. A configured entry is likewise established
#: code: its address has been explicitly verified by the port.
#:
#: A data pointer and an instruction immediate are guesses.  Any dword that
#: happens to look like an address is a candidate, and three of the four
#: blocks that reach nowhere - 00540360, 00540500, 00d18bd0 - are exactly
#: that: they pass only the weak "aligned and decodes" gate.  A guess whose
#: own dispatch goes nowhere may be withdrawn, and every withdrawal is
#: reported with its provenance and the target that failed, so a real callback
#: with an unresolvable callee is visible rather than silently dropped.
STRUCTURAL_PROVENANCE = ("table", "initterm", "config", "seh")

#: The subset of the above that the program itself will call by address: a
#: jump-table slot, an __initterm entry, and a configured entry point.  These
#: are function starts, not blocks some other body may absorb.  An SEH landing
#: is deliberately absent: attaching one to the frame that establishes it is
#: exactly what extend_finally_body exists to do.
NAMED_PROVENANCE = ("table", "initterm", "config")


def note_structural(provenance, owner, t, why):
    """Record that a jump table, `__initterm` or config names `t` outright.

    Being named by one of those makes an address code by construction, whatever
    route it arrived by and whether or not it was already known.  That has to be
    recorded at the point of discovery: a target the decoder accepts because it
    is already an instruction boundary never reaches `unlisted_targets` (see
    `want_target`, which returns at its first check), so a block first found by
    a guess would keep its guessed provenance and stay prunable even after a
    table had named it.

    When the named address is an alternate entry into a body, the body is
    promoted too: pruning is keyed on the body's own address, so protecting the
    entry alone would still let the block it lives in be withdrawn out from
    under the table."""
    if t is None or why not in STRUCTURAL_PROVENANCE:
        return
    if provenance.get(t) != "config":
        provenance[t] = why
    named = owner.get(t)
    if named is not None and provenance.get(named.addr) != "config":
        provenance[named.addr] = why


# What makes an alternate entry a named place a mod may hook, as opposed to an
# internal block of the function it sits in.  A dword that merely reads as an
# instruction address is not on this list: the byte-wise scan finds those by
# the thousand and a coincidence is indistinguishable from a pointer, so the
# scan feeds dispatch recovery only.
HOOK_EVIDENCE = ("initterm", "reloc", "immediate", "curated")


def read_curated(path):
    """The curated globals file. Line-oriented on purpose: Python 3.9 has
    no tomllib, and this file is written to be read by twenty lines."""
    out, section = {}, None
    if path is None:
        return out
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                out.setdefault(section, {})
                continue
            if "=" not in line or section is None:
                raise TranslateError("globals.toml: cannot parse %r" % raw)
            k, v = (t.strip() for t in line.split("=", 1))
            out[section][k] = v[1:-1] if v.startswith('"') else int(v, 0)
    return out


def hook_kind(addr, listed, alt_owner, provenance, evidence, intrinsics):
    """The symbol kind for `addr`, and whether a mod may hook it.

    Dispatch and eligibility are different questions.  Every entry in the
    three tables can be dispatched to, because the program reaches it; only a
    place the program *names* has a meaning stable enough to hook, and the
    exclusions come first:

      intrinsic     a substituted body (setjmp, longjmp).  Its address is an
                    implementation detail of this translator.
      block         a recovered block, or an alternate entry a jump table
                    names.  A jump-table target is an internal block of its
                    owner however else it is reached, so the table wins over
                    any pointer evidence.
      continuation  an alternate entry that only branch or fallthrough
                    discovery reached - a listing gap continued, not a name.

    Nothing here consults the order in which discovery ran: `evidence` is
    recorded wherever an address is named, whether or not that pass was the
    one that added it.
    """
    if addr in intrinsics:
        return "intrinsic", False
    # A recovered whole function can share a jump-table provenance. Explicit
    # entry curation requires a verified prologue/calling convention; ordinary
    # alternate curation still cannot promote internal jump-table blocks.
    if addr in listed or "curated_entry" in evidence.get(addr, ()):
        return "entry", True
    if addr not in alt_owner:
        return "block", False
    if provenance.get(addr) == "table":
        return "block", False
    if any(e in HOOK_EVIDENCE for e in evidence.get(addr, ())):
        return "alternate", True
    return "continuation", False


def prunable_blocks(recovered_addrs, provenance):
    """The recovered blocks the withdraw pass is allowed to drop: the guesses,
    never the ones a table, `__initterm` or config named."""
    return {a for a in recovered_addrs
            if provenance.get(a, "branch") not in STRUCTURAL_PROVENANCE}

#: The import-shim trampoline range from x86.h; a literal dispatch into it is
#: an import, not a missing function.
GUEST_SHIM_BASE = 0x0FF00000
GUEST_SHIM_END = 0x10000000

# Runtime intrinsics: guest addresses whose translated body is replaced by a
# call into runtime/intrinsics.h.
INTRINSIC_LONGJMP = 0x0055DB78          # _longjmp
INTRINSIC_SETJMP  = 0x0055DAFC          # __setjmp3, buffer at ESP+4
INTRINSIC_BODY = {
    INTRINSIC_LONGJMP: "recomp_longjmp(c);",
    INTRINSIC_SETJMP:  "recomp_setjmp(c);",   # single-call fallback, indirect only
}

# --------------------------------------------------------------- registers --

REG32 = ["EAX", "ECX", "EDX", "EBX", "ESP", "EBP", "ESI", "EDI"]
REG16 = ["AX", "CX", "DX", "BX", "SP", "BP", "SI", "DI"]
REG8L = ["AL", "CL", "DL", "BL"]
REG8H = ["AH", "CH", "DH", "BH"]

R_EAX, R_ECX, R_EDX, R_EBX, R_ESP, R_EBP, R_ESI, R_EDI = range(8)

#: MMX on the MMn registers, which the translator emits. The rest of the
#: vector set (SSE, SSE2, and the SSE extensions to MMX) stays a trap.
MMX_BINARY = {
    "PADDB": ("mmx_padd", 8), "PADDW": ("mmx_padd", 16), "PADDD": ("mmx_padd", 32),
    "PADDQ": ("mmx_padd", 64),
    "PSUBB": ("mmx_psub", 8), "PSUBW": ("mmx_psub", 16), "PSUBD": ("mmx_psub", 32),
    "PSUBQ": ("mmx_psub", 64),
    "PADDSB": ("mmx_padds", 8), "PADDSW": ("mmx_padds", 16),
    "PSUBSB": ("mmx_psubs", 8), "PSUBSW": ("mmx_psubs", 16),
    "PADDUSB": ("mmx_paddus", 8), "PADDUSW": ("mmx_paddus", 16),
    "PSUBUSB": ("mmx_psubus", 8), "PSUBUSW": ("mmx_psubus", 16),
    "PMULLW": ("mmx_pmullw", 0), "PMULHW": ("mmx_pmulhw", 0), "PMADDWD": ("mmx_pmaddwd", 0),
    "PCMPEQB": ("mmx_pcmpeq", 8), "PCMPEQW": ("mmx_pcmpeq", 16), "PCMPEQD": ("mmx_pcmpeq", 32),
    "PCMPGTB": ("mmx_pcmpgt", 8), "PCMPGTW": ("mmx_pcmpgt", 16), "PCMPGTD": ("mmx_pcmpgt", 32),
    "PACKSSWB": ("mmx_packsswb", 0), "PACKSSDW": ("mmx_packssdw", 0),
    "PACKUSWB": ("mmx_packuswb", 0),
    "PUNPCKLBW": ("mmx_punpckl", 8), "PUNPCKLWD": ("mmx_punpckl", 16),
    "PUNPCKLDQ": ("mmx_punpckl", 32),
    "PUNPCKHBW": ("mmx_punpckh", 8), "PUNPCKHWD": ("mmx_punpckh", 16),
    "PUNPCKHDQ": ("mmx_punpckh", 32),
    "PAND": ("mmx_pand", 0), "PANDN": ("mmx_pandn", 0), "POR": ("mmx_por", 0),
    "PXOR": ("mmx_pxor", 0),
}
MMX_SHIFT = {
    "PSRLW": ("mmx_psrl", 16), "PSRLD": ("mmx_psrl", 32), "PSRLQ": ("mmx_psrl", 64),
    "PSRAW": ("mmx_psra", 16), "PSRAD": ("mmx_psra", 32),
    "PSLLW": ("mmx_psll", 16), "PSLLD": ("mmx_psll", 32), "PSLLQ": ("mmx_psll", 64),
}
XMM_RE = re.compile(r"\b(?:XMM[0-7]|xmmword)\b")
MMN_RE = re.compile(r"\bMM[0-7]\b")




def is_mmx_insn(mnem, ops):
    """An instruction the translator emits as MMX: an MMX mnemonic whose
    operands name an MMn register and no XMM one."""
    if mnem not in MMX_BINARY and mnem not in MMX_SHIFT and mnem not in ("MOVQ", "MOVD"):
        return False
    return (any(MMN_RE.search(o) for o in ops) and not any(XMM_RE.search(o) for o in ops))


def apply_operand_redirects(parsed, redirects):
    """Rewrite the memory operand of each redirected instruction; every
    redirect has to find its instruction and its address, or the build stops."""
    missing = dict(redirects)
    for fn in parsed:
        for ins in fn.insns:
            if ins.addr not in redirects or not ins.ops:
                continue
            old, new = redirects[ins.addr]
            pattern = re.compile(r"\[0x0*%x\]" % old, re.IGNORECASE)
            ops = [pattern.sub("[0x%08x]" % new, o) for o in ins.ops]
            if ops != list(ins.ops):
                ins.ops = ops
                missing.pop(ins.addr, None)
    if missing:
        raise TranslateError("operand redirects matched nothing: " +
                             ", ".join("%08x" % a for a in sorted(missing)))


ALL_FLAGS = frozenset(("cf", "zf", "sf", "of", "pf", "af"))
NO_FLAGS = frozenset()


class TranslateError(Exception):
    pass


def hexlit(v):
    return "0x%xu" % (v & 0xFFFFFFFF)


def mask_of(size):
    return {8: 0xFF, 16: 0xFFFF, 32: 0xFFFFFFFF}[size]


def utype(size):
    return {8: "uint8_t", 16: "uint16_t", 32: "uint32_t"}[size]


def stype(size):
    return {8: "int8_t", 16: "int16_t", 32: "int32_t"}[size]


# ---------------------------------------------------------------- operands --

class Op(object):
    __slots__ = ("kind", "size", "reg", "part", "imm",
                 "base", "index", "scale", "disp", "seg", "sti", "addr_c")

    def __init__(self, kind, **kw):
        self.kind = kind
        self.size = kw.get("size")
        self.reg = kw.get("reg")
        self.part = kw.get("part")
        self.imm = kw.get("imm")
        self.base = kw.get("base")
        self.index = kw.get("index")
        self.scale = kw.get("scale", 1)
        self.disp = kw.get("disp", 0)
        self.seg = kw.get("seg")
        self.sti = kw.get("sti")
        self.addr_c = kw.get("addr_c")   # pre-computed address expression

    def __repr__(self):
        return "Op(%s,%s)" % (self.kind, self.size)


PTR_SIZE = {"byte": 8, "word": 16, "dword": 32, "qword": 64, "tword": 80,
            "float": 32, "double": 64, "extended double": 80, "xmmword": 128}

MEM_RE = re.compile(
    r"^(?:(byte|word|dword|qword|tword|xmmword|float|double|extended double) ptr )?"
    r"(?:([CDEFGS]S):)?"
    r"\[([^\]]*)\]$")

IMM_RE = re.compile(r"^-?0x[0-9a-fA-F]+$|^-?[0-9]+$")
#: Segment selectors as a 32-bit Windows process sees them: the flat code
#: and data selectors, FS for the TEB.  Read-only here; the game never
#: reloads a segment register.
SEGMENT_SELECTOR = {"CS": 0x1b, "DS": 0x23, "ES": 0x23, "SS": 0x23, "FS": 0x3b, "GS": 0x00}
ST_RE = re.compile(r"^ST([0-7])$")
MM_RE = re.compile(r"^MM([0-7])$")

#: A vector instruction this translator does not model. CPUID advertises
#: neither SSE nor SSE2, so a guest that checks (the CRT's own probe) never
#: runs one; each becomes a recomp_unmodelled trap rather than a translation
#: failure, which would lose the whole function. Anything naming an MMn,
#: XMMn or YMMn register counts, plus the extension instructions that name
#: none. AVX is included for the same reason: CPUID advertises it no more than
#: SSE, and a runtime's AVX path (Delphi's Move has one) is never taken.
VECTOR_REG_RE = re.compile(r"\b(?:[XY]?MM[0-7]|[xy]mmword)\b")
#: AVX names registers and an operand size this translator does not parse,
#: so it has to be recognised before the operands are.
AVX_OPERAND_RE = re.compile(r"\b(?:YMM[0-7]|ymmword)\b")
VECTOR_MNEM = frozenset((
    "STMXCSR", "LDMXCSR", "FXSAVE", "FXRSTOR", "SFENCE", "LFENCE", "MFENCE",
    "PREFETCHNTA", "PREFETCHT0", "PREFETCHT1", "PREFETCHT2", "MOVNTI", "CLFLUSH",
))


def is_vector_insn(mnem, ops):
    """True for an MMX/SSE/SSE2 instruction (see VECTOR_REG_RE).

    EMMS is not one of them here: it names no register and only marks every
    x87 register empty, which the x87 model can do. Codecs call a lone
    `emms; ret` helper unconditionally, whatever CPUID said, so trapping it
    stopped a game that never used MMX arithmetic at all.
    """
    if mnem == "EMMS":
        return False
    return mnem in VECTOR_MNEM or any(VECTOR_REG_RE.search(o) for o in ops)


XMM_RE = re.compile(r"^XMM([0-7])$")


def names_an_xmm(ins):
    """MOVSD and CMPSD name a string instruction and an SSE scalar one, and
    only the operands tell them apart: the string forms take ES:EDI and ESI,
    the SSE forms an XMM register."""
    return any(XMM_RE.match(o) for o in ins.ops)


def parse_reg(text):
    """Return (index, size, part) or None."""
    if text in REG32:
        return (REG32.index(text), 32, None)
    if text in REG16:
        return (REG16.index(text), 16, None)
    if text in REG8L:
        return (REG8L.index(text), 8, "l")
    if text in REG8H:
        return (REG8H.index(text), 8, "h")
    return None


def parse_imm(text):
    if text.startswith("-"):
        return -int(text[1:], 0)
    return int(text, 0)


def parse_mem_expr(expr):
    """`EAX*0x4 + 0x1234` -> (base, index, scale, disp)."""
    base = index = None
    scale = 1
    disp = 0
    for term in [t.strip() for t in expr.split("+")]:
        if not term:
            continue
        if "*" in term:
            rname, sc = term.split("*", 1)
            r = parse_reg(rname.strip())
            if r is None or r[1] != 32:
                raise TranslateError("bad index register %r" % term)
            index, scale = r[0], parse_imm(sc.strip())
        else:
            r = parse_reg(term)
            if r is not None:
                if r[1] != 32:
                    raise TranslateError("bad base register %r" % term)
                if base is None:
                    base = r[0]
                elif index is None:
                    index, scale = r[0], 1
                else:
                    raise TranslateError("too many registers in %r" % expr)
            elif IMM_RE.match(term):
                disp += parse_imm(term)
            else:
                raise TranslateError("bad memory term %r" % term)
    return base, index, scale, disp


def parse_operand(text):
    text = text.strip()
    r = parse_reg(text)
    if r is not None:
        return Op("reg", reg=r[0], size=r[1], part=r[2])
    m = XMM_RE.match(text)
    if m:
        return Op("xmm", reg=int(m.group(1)), size=128)
    m = ST_RE.match(text)
    if m:
        return Op("st", sti=int(m.group(1)))
    m = MM_RE.match(text)
    if m:
        return Op("mm", reg=int(m.group(1)), size=64)
    if text in SEGMENT_SELECTOR:
        return Op("sreg", size=16, imm=SEGMENT_SELECTOR[text])
    if IMM_RE.match(text):
        return Op("imm", imm=parse_imm(text))
    m = MEM_RE.match(text)
    if m:
        sz = PTR_SIZE[m.group(1)] if m.group(1) else None
        base, index, scale, disp = parse_mem_expr(m.group(3))
        return Op("mem", size=sz, base=base, index=index, scale=scale,
                  disp=disp, seg=m.group(2))
    raise TranslateError("unparsed operand %r" % text)


# ------------------------------------------------------------ C generation --

def addr_expr(op):
    if op.addr_c is not None:
        return op.addr_c
    terms = []
    if op.seg == "FS":
        terms.append("c->fs_base")
    if op.base is not None:
        terms.append("c->r[%d]" % op.base)
    if op.index is not None:
        if op.scale == 1:
            terms.append("c->r[%d]" % op.index)
        else:
            terms.append("c->r[%d] * %du" % (op.index, op.scale))
    if op.disp or not terms:
        terms.append(hexlit(op.disp))
    if len(terms) == 1:
        return terms[0]
    return "(" + " + ".join(terms) + ")"


def reg_read(idx, size, part):
    if size == 32:
        return "c->r[%d]" % idx
    if size == 16:
        return "(uint16_t)c->r[%d]" % idx
    if part == "h":
        return "(uint8_t)(c->r[%d] >> 8)" % idx
    return "(uint8_t)c->r[%d]" % idx


def reg_write(idx, size, part, value):
    if size == 32:
        return "c->r[%d] = %s;" % (idx, value)
    if size == 16:
        return "c->r[%d] = (c->r[%d] & 0xffff0000u) | ((%s) & 0xffffu);" % (idx, idx, value)
    if part == "h":
        return ("c->r[%d] = (c->r[%d] & 0xffff00ffu) | (((%s) & 0xffu) << 8);"
                % (idx, idx, value))
    return "c->r[%d] = (c->r[%d] & 0xffffff00u) | ((%s) & 0xffu);" % (idx, idx, value)


def read_op(op, size):
    if op.kind == "reg":
        return reg_read(op.reg, op.size, op.part)
    if op.kind == "imm":
        return hexlit(op.imm) if size == 32 else "0x%xu" % (op.imm & mask_of(size))
    if op.kind == "mem":
        return "rd%d(%s)" % (size, addr_expr(op))
    if op.kind == "sreg":
        return "0x%xu" % op.imm
    raise TranslateError("cannot read operand %r" % op.kind)


def write_op(op, size, value):
    if op.kind == "reg":
        return reg_write(op.reg, op.size, op.part, value)
    if op.kind == "mem":
        return "wr%d(%s, (%s)(%s));" % (size, addr_expr(op), utype(size), value)
    raise TranslateError("cannot write operand %r" % op.kind)


def operand_size(ops, hint=None):
    """Infer the operation width from a register operand or an explicit ptr."""
    for o in ops:
        if o.kind == "reg":
            return o.size
    for o in ops:
        if o.kind == "mem" and o.size:
            return o.size
    if hint:
        return hint
    raise TranslateError("ambiguous operand size")


# ----------------------------------------------------------------- parsing --

LINE_RE = re.compile(r"^([0-9a-f]{8})  (\S+)(?: (.*))?$")


class Insn(object):
    __slots__ = ("addr", "mnem", "rep", "ops", "text", "raw")

    def __init__(self, addr, mnem, rep, opstrs, raw):
        self.addr = addr
        self.mnem = mnem
        self.rep = rep
        self.ops = opstrs
        self.raw = raw

    def __repr__(self):
        return "%08x %s %s" % (self.addr, self.mnem, ",".join(self.ops))


def parse_listing(path):
    with open(path) as fh:
        return parse_listing_text(fh.read())


def parse_listing_text(text):
    """Parse listing text.  Callers that build a listing in memory use this
    directly so they never have to write a temporary file."""
    insns = []
    if True:
        for line in text.splitlines():
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = LINE_RE.match(line)
            if not m:
                raise TranslateError("bad listing line %r" % line)
            addr = int(m.group(1), 16)
            mnem = m.group(2)
            rep = None
            if "." in mnem:
                mnem, rep = mnem.split(".", 1)
            opstr = m.group(3) or ""
            ops = [o for o in (x.strip() for x in opstr.split(",")) if o]
            insns.append(Insn(addr, mnem, rep, ops, line))
    insns.sort(key=lambda i: i.addr)
    return insns


# ------------------------------------------------------------------- flags --

COND = {
    "Z":  ("c->eflags_zf", ("zf",)),
    "NZ": ("!c->eflags_zf", ("zf",)),
    "C":  ("c->eflags_cf", ("cf",)),
    "B":  ("c->eflags_cf", ("cf",)),
    "NC": ("!c->eflags_cf", ("cf",)),
    "AE": ("!c->eflags_cf", ("cf",)),
    "A":  ("(!c->eflags_cf && !c->eflags_zf)", ("cf", "zf")),
    "BE": ("(c->eflags_cf || c->eflags_zf)", ("cf", "zf")),
    "S":  ("c->eflags_sf", ("sf",)),
    "NS": ("!c->eflags_sf", ("sf",)),
    "O":  ("c->eflags_of", ("of",)),
    "NO": ("!c->eflags_of", ("of",)),
    "P":  ("c->eflags_pf", ("pf",)),
    "NP": ("!c->eflags_pf", ("pf",)),
    "G":  ("(!c->eflags_zf && c->eflags_sf == c->eflags_of)", ("zf", "sf", "of")),
    "GE": ("c->eflags_sf == c->eflags_of", ("sf", "of")),
    "L":  ("c->eflags_sf != c->eflags_of", ("sf", "of")),
    "LE": ("(c->eflags_zf || c->eflags_sf != c->eflags_of)", ("zf", "sf", "of")),
}
# capstone spells several conditions differently from Ghidra
for _dst, _src in (("E", "Z"), ("NE", "NZ"), ("NAE", "C"), ("NB", "NC"),
                   ("NBE", "A"), ("NA", "BE"), ("NG", "LE"), ("NGE", "L"),
                   ("NL", "GE"), ("NLE", "G"), ("PE", "P"), ("PO", "NP")):
    COND[_dst] = COND[_src]

JCC = {"J" + k: v for k, v in COND.items()}
JCC["JECXZ"] = ("c->r[1] == 0", ())
# LOOP decrements ECX and branches while it is not zero; the decrement is
# the condition, so it happens whichever way the branch goes.
JCC["LOOP"] = ("(c->r[1] = c->r[1] - 1u) != 0u", ())
SETCC = {"SET" + k: v for k, v in COND.items()}
CMOVCC = {"CMOV" + k: v for k, v in COND.items()}

ARITH_ALL = ALL_FLAGS
LOGIC_DEF = ALL_FLAGS           # AF is architecturally undefined; treat as killed
INCDEC_DEF = frozenset(("zf", "sf", "of", "pf", "af"))

#: A shift or rotate with a zero count leaves every flag untouched, so it only
#: kills flags when the count is a non-zero constant.  MAYDEF is what it writes
#: when it does shift, and drives the choice of the flag-setting helper.
SHIFT_MAYDEF = {
    "SHL": ALL_FLAGS, "SHR": ALL_FLAGS, "SAR": ALL_FLAGS,
    "SHLD": ALL_FLAGS, "SHRD": ALL_FLAGS,
    "ROL": frozenset(("cf", "of")), "ROR": frozenset(("cf", "of")),
    "RCL": frozenset(("cf", "of")), "RCR": frozenset(("cf", "of")),
}
SHIFT_COUNT_INDEX = {"SHL": 1, "SHR": 1, "SAR": 1, "ROL": 1, "ROR": 1,
                     "RCL": 1, "RCR": 1, "SHLD": 2, "SHRD": 2}

# mnemonic -> (defs, uses)
FLAG_EFFECT = {
    "ADD": (ARITH_ALL, NO_FLAGS),
    "SUB": (ARITH_ALL, NO_FLAGS),
    "CMP": (ARITH_ALL, NO_FLAGS),
    "CMPXCHG8B": (frozenset(("zf",)), NO_FLAGS),
    "NEG": (ARITH_ALL, NO_FLAGS),
    "ADC": (ARITH_ALL, frozenset(("cf",))),
    "SBB": (ARITH_ALL, frozenset(("cf",))),
    "AND": (LOGIC_DEF, NO_FLAGS),
    "OR": (LOGIC_DEF, NO_FLAGS),
    "XOR": (LOGIC_DEF, NO_FLAGS),
    "TEST": (LOGIC_DEF, NO_FLAGS),
    "INC": (INCDEC_DEF, NO_FLAGS),
    "DEC": (INCDEC_DEF, NO_FLAGS),
    "SHL": (ALL_FLAGS, NO_FLAGS),
    "SHR": (ALL_FLAGS, NO_FLAGS),
    "SAR": (ALL_FLAGS, NO_FLAGS),
    "SHLD": (ALL_FLAGS, NO_FLAGS),
    "SHRD": (ALL_FLAGS, NO_FLAGS),
    "ROL": (frozenset(("cf", "of")), NO_FLAGS),
    "ROR": (frozenset(("cf", "of")), NO_FLAGS),
    "RCL": (frozenset(("cf", "of")), frozenset(("cf",))),
    "RCR": (frozenset(("cf", "of")), frozenset(("cf",))),
    "MUL": (ALL_FLAGS, NO_FLAGS),
    "IMUL": (ALL_FLAGS, NO_FLAGS),
    "DIV": (ALL_FLAGS, NO_FLAGS),
    "IDIV": (ALL_FLAGS, NO_FLAGS),
    "BT": (frozenset(("cf",)), NO_FLAGS),
    "BTS": (frozenset(("cf",)), NO_FLAGS),
    "BSR": (frozenset(("zf",)), NO_FLAGS),
    "BSF": (frozenset(("zf",)), NO_FLAGS),
    "SAHF": (frozenset(("cf", "pf", "af", "zf", "sf")), NO_FLAGS),
    "POPFD": (ALL_FLAGS, NO_FLAGS),
    "PUSHFD": (NO_FLAGS, ALL_FLAGS),
    "POPF": (ALL_FLAGS, NO_FLAGS),
    "PUSHF": (NO_FLAGS, ALL_FLAGS),
    "SCASB": (ARITH_ALL, NO_FLAGS),
    "SCASW": (ARITH_ALL, NO_FLAGS),
    "SCASD": (ARITH_ALL, NO_FLAGS),
    "CMPSB": (ARITH_ALL, NO_FLAGS),
    "CMPSW": (ARITH_ALL, NO_FLAGS),
    "CMPSD": (ARITH_ALL, NO_FLAGS),
    "CLC": (frozenset(("cf",)), NO_FLAGS),
    "STC": (frozenset(("cf",)), NO_FLAGS),
    "NOT": (NO_FLAGS, NO_FLAGS),
}


def shift_count_const(insn):
    """The shift count if it is a constant, else None (a CL count is unknown)."""
    idx = SHIFT_COUNT_INDEX[insn.mnem]
    if len(insn.ops) <= idx:
        return 1                      # the shift-by-one encoding
    try:
        o = parse_operand(insn.ops[idx])
    except TranslateError:
        return None
    return (o.imm & 31) if o.kind == "imm" else None


def flag_effect(insn):
    """(defs, uses) of x86 arithmetic flags, ignoring DF which is tracked apart.

    `defs` is the kill set for liveness, so it only lists flags the
    instruction writes on every path.  A shift or rotate whose count might be
    zero, and a REP-prefixed compare whose count might be zero, write nothing
    and must therefore not kill the incoming flags."""
    m = insn.mnem
    if m in JCC:
        return (NO_FLAGS, frozenset(JCC[m][1]))
    if m in SETCC:
        return (NO_FLAGS, frozenset(SETCC[m][1]))
    if m in CMOVCC:
        return (NO_FLAGS, frozenset(CMOVCC[m][1]))
    if insn.rep and m in ("SCASB", "SCASW", "SCASD", "CMPSB", "CMPSW", "CMPSD"):
        return (NO_FLAGS, NO_FLAGS)
    if m in SHIFT_MAYDEF:
        uses = frozenset(("cf",)) if m in ("RCL", "RCR") else NO_FLAGS
        cnt = shift_count_const(insn)
        if cnt is None or cnt == 0:
            return (NO_FLAGS, uses)
        return (SHIFT_MAYDEF[m], uses)
    return FLAG_EFFECT.get(m, (NO_FLAGS, NO_FLAGS))


# ------------------------------------------------------------------- image --

class Image(object):
    def __init__(self, path):
        import pefile
        pe = pefile.PE(path, fast_load=True)
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.size = pe.OPTIONAL_HEADER.SizeOfImage
        data = pe.get_memory_mapped_image()
        # get_memory_mapped_image() stops at the last section's raw end, which
        # is short of SizeOfImage; the architectural image runs to
        # base + SizeOfImage (0xd4c000 here) and those bytes read as zero.
        if len(data) < self.size:
            data = data + bytes(self.size - len(data))
        self.data = data[:self.size]
        self.end = self.base + self.size
        rel = pe.OPTIONAL_HEADER.DATA_DIRECTORY[5]      # IMAGE_DIRECTORY_BASERELOC
        self.reloc_dir = (rel.VirtualAddress, rel.Size)
        #: Import address table slots by address, as the imported name.
        #: noreturn_callees_from reads it to see that a function ending in
        #: RaiseException - MSVC's _CxxThrowException - does not return, and
        #: so that what a compiler put after a throw, such as a switch table,
        #: is not decoded as code.
        self.iat_names = {}
        try:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
            for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
                for imp in entry.imports:
                    if imp.name:
                        self.iat_names[imp.address] = imp.name.decode("ascii", "replace")
        except Exception:                 # a malformed table only loses names
            pass
        pe.close()
        # Section map: executable ranges are where code can live, initialized
        # data is where a pointer to it can be stored.  .reloc is the
        # relocation table and .rsrc is resource blobs; neither is data the
        # program dereferences, and both produce nothing but false matches.
        self.recover_errors = []
        #: Callees that never return, as the listings show them: a function
        #: whose listing ends on `CALL x` was cut there by Ghidra because x
        #: does not come back (`__CxxThrowException`, `exit`).  Recovery ends a
        #: block at such a call instead of decoding the padding and jump table
        #: that follow it.  Filled in by the driver once the listings are read.
        self.noreturn_callees = set()
        self.string_candidates = set()
        self.thunk_candidates = set()
        self.weak_candidates = set()
        self.interior_candidates = set()
        self.exec_ranges = []
        self.data_ranges = []
        for sec in pe.sections:
            name = sec.Name.rstrip(b"\0").decode("ascii", "replace")
            lo = self.base + sec.VirtualAddress
            hi = lo + max(sec.Misc_VirtualSize, sec.SizeOfRawData)
            if sec.Characteristics & 0x20000000:
                self.exec_ranges.append((lo, hi, name))
            if (sec.Characteristics & 0x40) and name not in (".reloc", ".rsrc"):
                self.data_ranges.append((lo, hi, name))
        try:
            import capstone
            self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        except ImportError:            # length checking is optional
            self.md = None

    def relocated_pointers(self):
        """Every address named by a dword the loader rewrites, and where.

        A base relocation is the linker's own record that the four bytes at
        that location are an address, not data that happens to read as one.
        That is the difference between a pointer and a coincidence, and it is
        why this and not the byte-wise scan decides hook eligibility: the scan
        reads every offset in every data section, so any dword whose value
        lands on an instruction boundary passes it.

        HIGHLOW (type 3) is the only relocation a 32-bit x86 image uses for a
        full address; ABSOLUTE (type 0) is block padding and names nothing.
        """
        rva, size = self.reloc_dir
        out = {}
        if not rva or not size:
            return out
        off, end = rva, rva + size
        while off + 8 <= end:
            page = int.from_bytes(self.data[off:off + 4], "little")
            blk = int.from_bytes(self.data[off + 4:off + 8], "little")
            if blk < 8 or off + blk > end:
                break
            for k in range(off + 8, off + blk, 2):
                e = int.from_bytes(self.data[k:k + 2], "little")
                if e >> 12 != 3:
                    continue
                site = page + (e & 0xfff)
                if site + 4 > self.size:
                    continue
                va = int.from_bytes(self.data[site:site + 4], "little")
                out.setdefault(va, self.base + site)
            off += blk
        return out

    def is_exec(self, va):
        return any(lo <= va < hi for lo, hi, _ in self.exec_ranges)

    def seh_landings(self, stub):
        """Classify a Delphi handler stub by structure, never by its target's name.

        The five-byte JMP is code; what follows is either a landing block or
        a bounded count/type/handler table. Stubs and blocks may be absent
        from every exported listing.

        A caller that may be looking at another compiler's handler - MSVC
        pushes an ordinary CRT function, not a stub - asks through
        seh_landings_opt instead of testing the shape itself.
        """
        displacement = self.rd32(stub + 1)
        if (not self.is_exec(stub) or self.rd8(stub) != 0xe9
                or displacement is None
                or not self.is_exec((stub + 5 + displacement) & 0xffffffff)):
            raise TranslateError("SEH stub %08x is not a JMP rel32 to code" % stub)
        n = self.rd32(stub + 5)
        if n is not None and 1 <= n <= 64:
            landings = []
            for i in range(n):
                typ = self.rd32(stub + 9 + 8 * i)
                handler = self.rd32(stub + 13 + 8 * i)
                if (typ is None or handler is None or not self.is_exec(handler)
                        or not (typ == 0 or self.is_exec(typ) or any(
                            lo <= typ < hi for lo, hi, _ in self.data_ranges))):
                    break
                landings.append(handler)
            else:
                return landings, (stub + 5, stub + 9 + 8 * n)
        if not self.is_exec(stub + 5):
            raise TranslateError("SEH landing %08x is not in code" % (stub + 5))
        return [stub + 5], None

    def seh_landings_opt(self, stub):
        """seh_landings for a stub this pass may not be able to classify.

        The frame idiom - three PUSHes and the chain MOV - is the same in
        every compiler that establishes a TEB exception record, so a frame
        site can name a handler that is not a Delphi stub at all: MSVC's is
        an ordinary function reached through a scope table. There is no
        landing block to discover behind one, so discovery skips it; the
        frame itself is still modelled, because seh_frame_sites keeps it.
        """
        try:
            return self.seh_landings(stub)
        except TranslateError:
            return None

    def seh_constructor_helper(self, va):
        """Return the handler stored by Delphi's returning constructor helper.

        The helper saves EDX/ECX/EBX, optionally calls the allocator, then
        fills the caller's 16-byte registration at ESP+16 through ECX. Match
        the whole sequence, including zeroing the FS base and restoring the
        saved registers. The handler immediate is image data, never a kit
        address. setjmp must live in the caller AFTER this helper returns.
        """
        if not hasattr(self, "_seh_constructor_cache"):
            self._seh_constructor_cache = {}
        if va not in self._seh_constructor_cache:
            prefix = bytes.fromhex("52515384d27c03ff50f431d28d4c2410648b1a8919896908c74104")
            suffix = bytes.fromhex("89410c64890a5b595ac3")
            size = len(prefix) + 4 + len(suffix)
            code = self.data[va - self.base:va - self.base + size] if self.is_exec(va) else b""
            stub = None
            if (len(code) == size and code.startswith(prefix) and code.endswith(suffix)
                    and self.is_exec(va + size - 1)):
                candidate = self.rd32(va + len(prefix))
                if self.is_exec(candidate):
                    stub = candidate
            self._seh_constructor_cache[va] = stub
        return self._seh_constructor_cache[va]

    def looks_like_function(self, va):
        """Does `va` look like a function start for this game's compiler?

        Three signals, all of which the compiler's own output satisfies: the
        address has the configured alignment, the byte before it is padding
        or the end of the previous function, and it decodes to a real instruction."""
        if self.md is None or va % FUNCTION_ALIGNMENT or not self.is_exec(va):
            return False
        if va <= self.base or self.data[va - self.base - 1] not in (0xCC, 0x90, 0xC3):
            return False
        got = list(self.md.disasm(self.data[va - self.base:va - self.base + 16], va, count=1))
        return bool(got) and got[0].mnemonic not in ("int3", "hlt", "(bad)")

    def looks_like_code_start(self, va):
        """A weaker signal for an address something in the image points AT.

        The pointer is itself the evidence that the address matters; all this
        adds is that it could be the start of a function.  The padding test
        `looks_like_function` applies is too strict for a pointed-to address,
        because it only recognises `RET`, `NOP` and `int3` as the end of the
        previous function: `005781c0` follows a `ret 8` (`c2 08 00`) and so was
        rejected, even though a vtable slot at `005729d6` names it.  What keeps
        this honest is that every recovered block still has to translate.
        """
        if self.md is None or va % FUNCTION_ALIGNMENT or not self.is_exec(va):
            return False
        got = list(self.md.disasm(self.data[va - self.base:va - self.base + 16],
                                  va, count=1))
        return bool(got) and got[0].mnemonic not in ("int3", "hlt", "(bad)")

    #: A thunk is short and ends by transferring control somewhere else.
    THUNK_LIMIT = 8

    def plausible_immediate_target(self, va):
        """Could an instruction immediate naming `va` be a code address?

        The immediate is the evidence that something is stored there; this
        asks whether it could be entered.  Beyond the two function-start
        signals, a thunk qualifies: `atexit` is handed ten-byte
        `MOV ECX,obj / JMP dtor` stubs packed straight after their
        initializer's RET, unaligned and unpadded, and the CRT calls them at
        exit."""
        return (self.looks_like_function(va) or self.looks_like_code_start(va)
                or self.looks_like_thunk(va))

    def starts_with_utf16_run(self, va):
        """Four printable ASCII UTF-16 pairs in the first 16 bytes suggest data.

        This is only a scan-candidate filter. Explicit control-flow evidence
        must win even when instruction bytes happen to resemble text.
        """
        head = self.data[va - self.base:va - self.base + 16]
        # 6a 00 is PUSH 0; Delphi reserves locals with consecutive PUSHes.
        return any(all(0x20 <= head[i] <= 0x7e and head[i] != 0x6a and head[i + 1] == 0
                       for i in range(off, off + 8, 2))
                   for off in range(len(head) - 7))

    def undecodable_run(self, va, limit=32):
        """What a straight decode from `va` hits that no 32-bit compiler emits.

        Returns a description, or None when the run is ordinary code. Only the
        instructions before the first terminator are judged: past a RET or a
        JMP the bytes belong to whatever comes next.
        """
        if self.md is None or not (self.base <= va < self.end):
            return None
        self.md.detail = True
        at = va
        for _ in range(limit):
            got = list(self.md.disasm(self.data[at - self.base:at - self.base + 16], at, count=1))
            if not got:
                return "bytes that do not decode"
            ci = got[0]
            if ci.mnemonic in ("(bad)", "hlt"):
                return "a %s" % ci.mnemonic
            try:
                ins = self.to_insn(ci)
            except TranslateError as e:
                return "%s" % e
            if ins.mnem in TERMINATORS or ins.mnem.startswith("RET"):
                return None
            at = ci.address + ci.size
        return None

    def starts_with_ascii_run(self, va, least=8):
        """A NUL-terminated run of printable bytes long enough to be a name.

        The narrow-string counterpart of starts_with_utf16_run. GetProcAddress
        is handed these by the dozen and the program keeps them in .text
        beside its code, so a pointer to one is an argument, not an entry
        point. The length floor is what keeps ordinary instruction bytes out:
        most opcodes fall in the printable range, but eight of them in a row
        followed by a NUL is text, not code.
        """
        off = va - self.base
        if off < 0 or off >= len(self.data):
            return False
        end = off
        while end < len(self.data) and 0x20 <= self.data[end] < 0x7F:
            end += 1
        return end - off >= least and end < len(self.data) and self.data[end] == 0

    def is_utf16_constant(self, va):
        """Recognize a complete Delphi UnicodeString constant, including short text.

        The 12-byte header contains codepage and element-size words, a signed
        reference count, and a unit count. Validate the entire payload before
        treating the address as data; malformed headers are not evidence.
        """
        off = va - self.base
        if off < 12 or off > len(self.data) - 2:
            return False
        if (int.from_bytes(self.data[off - 10:off - 8], "little") != 2
                or self.data[off - 8:off - 4] != b"\xff" * 4):
            return False
        length = int.from_bytes(self.data[off - 4:off], "little")
        if not 1 <= length <= (len(self.data) - off - 2) // 2:
            return False
        end = off + length * 2
        return (self.data[end:end + 2] == b"\0\0"
                and all(self.data[i:i + 2] != b"\0\0" for i in range(off, end, 2)))

    def looks_like_thunk(self, va):
        """Does `va` look like a thunk?

        Thunks are neither 16-byte aligned nor preceded by padding - they are
        packed one after another - so the function-start signals miss them.
        What they do have is shape: a handful of instructions that all decode
        and convert, ending in a jump or a return."""
        if self.md is None or not self.is_exec(va):
            return False
        pos = va - self.base
        got = list(self.md.disasm(self.data[pos:pos + 16 * self.THUNK_LIMIT], va,
                                  count=self.THUNK_LIMIT))
        if not got or got[0].mnemonic in ("int3", "hlt", "(bad)"):
            return False
        for ci in got:
            if ci.mnemonic in ("int3", "hlt", "(bad)"):
                return False
            if ci.mnemonic in ("ret", "retn"):
                return True
            if ci.mnemonic in ("jmp", "call"):
                # A thunk jumps somewhere real.  Without this, the three-byte
                # NOP padding `8d 49 00` that MSVC puts between functions
                # decodes at its last byte as a lone JMP to a wild address and
                # is taken for a thunk: 0040fc44 jmp 0xf5413d44.
                if ci.op_str.startswith("0x"):
                    return self.is_exec(int(ci.op_str, 16))
                return True          # indirect: the target is not static
        return False

    @staticmethod
    def _looks_like_string_tail(blob, off):
        """Is the dword at `off` the last four bytes of a C string?

        Strings are the one systematic source of in-range values that is not a
        pointer at all, because a NUL terminator supplies the 0x00 top byte an
        image address needs.  But three printable bytes and a NUL is far too
        weak on its own: an address in this image has 0x00 on top by
        construction and 0x40..0x58 in the next byte, every one of which is
        printable, so the test fired for any pointer whose low two bytes
        happened to be printable as well - about one .text pointer in seven.
        It cost 21 of the 113 CRT static initializers.

        A real string tail has text running into it, so the byte before the
        dword has to be printable too.  A pointer stored after a NUL, a count
        or any other non-text byte is no longer mistaken for one."""
        if off < 1:
            return False
        quad = blob[off:off + 4]
        return (len(quad) == 4 and quad[3] == 0
                and all(0x20 <= b < 0x7F for b in quad[:3])
                and 0x20 <= blob[off - 1] < 0x7F)

    #: The body of the CRT's __initterm (0055d6a0 here): it walks an array of
    #: function pointers between its two arguments and calls each non-null
    #: one.  Matching the shape rather than the address keeps this from
    #: depending on one build's layout.
    INITTERM_BODY = ("CALL EAX", "ADD ESI,0x4")

    def initterm_tables(self, functions):
        """Ranges passed to __initterm, and every function pointer in them.

        These are the C++ static initializers.  Nothing points at the table
        entries except the table itself, and nothing points at the functions
        except those entries, so they are invisible to a cross-reference and
        Ghidra lists none of them.  The call site names the range outright -
        `PUSH end; PUSH start; CALL __initterm` - so the entries are entry
        points by construction and are taken ahead of every heuristic: no
        alignment, padding, string or shape test gets a say.
        """
        walkers = set()
        for fn in functions:
            # The shape, not one compiler's register allocation. __initterm
            # walks an array of function pointers and calls each non-null one,
            # which is an indirect CALL and a step of four however the
            # registers fell out. Populous's CRT keeps the cursor in ESI and
            # calls EAX; Need for Speed Most Wanted's keeps it on the stack
            # and calls through EDX, and matching the first spelling alone
            # left every one of its static initializers undiscovered - their
            # objects reached the game unconstructed, with null vtables.
            indirect_call = step = False
            for ins in fn.insns:
                if ins.mnem == "CALL" and ins.ops:
                    op = ins.ops[0]
                    try:
                        through_register = parse_operand(op).kind == "reg"
                    except TranslateError:
                        through_register = False
                    if op.startswith("dword ptr [") or through_register:
                        indirect_call = True
                if ins.mnem == "ADD" and ins.ops[1:] == ["0x4"]:
                    step = True
            if indirect_call and step:
                walkers.add(fn.addr)
        if not walkers:
            return [], set()

        ranges, entries = [], set()
        for fn in functions:
            for i, ins in enumerate(fn.insns):
                if ins.mnem != "CALL" or not ins.ops:
                    continue
                if not ins.ops[0].startswith("0x") or int(ins.ops[0], 16) not in walkers:
                    continue
                if i < 2 or fn.insns[i - 1].mnem != "PUSH" or fn.insns[i - 2].mnem != "PUSH":
                    continue
                try:            # cdecl: the push nearest the call is the first arg
                    start = parse_operand(fn.insns[i - 1].ops[0])
                    end = parse_operand(fn.insns[i - 2].ops[0])
                except TranslateError:
                    continue
                if start.kind != "imm" or end.kind != "imm":
                    continue
                lo, hi = start.imm, end.imm
                if not (lo < hi and hi - lo <= 0x100000 and lo % 4 == 0):
                    continue
                if not any(a <= lo and hi <= b for a, b, _ in self.data_ranges):
                    continue
                # The looser walker test buys a stricter table test: an
                # initializer array holds function pointers and nulls and
                # nothing else, so one slot that is neither disqualifies the
                # range rather than being quietly skipped.
                slots = [self.rd32(va) for va in range(lo, hi, 4)]
                named = [t for t in slots if t]
                if not named or not all(self.is_exec(t) for t in named):
                    continue
                ranges.append((lo, hi, ins.addr))
                entries.update(named)
        return ranges, entries

    def code_pointers(self, covered, interior_bytes=None, exclude=()):
        """Dwords stored anywhere in the image that name executable addresses.

        Returns (starts, interior).  `starts` are addresses nothing has
        translated that look like function entries or thunks; `interior` are
        addresses that already are instruction boundaries inside a translated
        function, which need an alternate entry so a call through the pointer
        can be dispatched rather than aborting.

        The order of the tests matters.  A known instruction boundary is
        accepted first, whatever its bytes look like: rejecting it for
        resembling text would throw away a pointer whose destination is
        already proven to be code.  An address that falls strictly inside a
        translated instruction is rejected outright, because it is not a place
        execution can begin.

        Ghidra's function list is not complete: `0049e800` is reached only
        through `CALL EAX` after the pointer is loaded from a packed struct in
        .data, so no cross-reference names it and no listing was produced.  The
        pointers are not 4-aligned - the array's stride is 0x42 - so the scan
        looks at every byte offset.
        """
        starts, interior = set(), set()
        skip = list(exclude)

        def excluded(va):
            return any(lo <= va < hi for lo, hi in skip)

        for lo, hi, _ in self.data_ranges + self.exec_ranges:
            blob = self.data[lo - self.base:hi - self.base]
            # len(blob) - 3 so the section's final dword is examined too.
            for off in range(len(blob) - 3):
                quad = blob[off:off + 4]
                va = int.from_bytes(quad, "little")
                if va in starts or va in interior:
                    continue
                # Skip both a pointer stored inside decoded table storage and
                # a candidate whose value points at table storage: 004784f0 is
                # the four-entry table behind `JMP [EAX*4 + 0x4784f0]` at
                # 004783e2, it is 16-aligned, and the byte before it is the
                # `RET` ending the previous function, so every static signal
                # says function start.
                if not self.is_exec(va):
                    continue
                if va in covered:
                    interior.add(va)          # a proven instruction boundary
                    continue
                if excluded(va) or excluded(lo + off):
                    continue
                if interior_bytes is not None and interior_bytes[va - self.base]:
                    self.interior_candidates.add(va)
                    continue                  # inside an instruction, not a start
                # Every signal of a function start - aligned, after padding,
                # decoding - outranks the text test: `PUSH 0x5b2370` spells
                # its address as 'p', '#', '[' and a terminator, and one
                # printable dword is a coincidence a padded entry is not.
                if self.looks_like_function(va):
                    starts.add(va)
                    continue
                if self._looks_like_string_tail(blob, off):
                    self.string_candidates.add(va)
                    continue
                if self.looks_like_thunk(va):
                    self.thunk_candidates.add(va)
                    starts.add(va)
                elif self.looks_like_code_start(va):
                    self.weak_candidates.add(va)
                    starts.add(va)
        return starts, interior


    def rd8(self, va):
        if not (self.base <= va < self.end):
            return None
        return self.data[va - self.base]

    def rd32(self, va):
        if not (self.base <= va and va + 4 <= self.end):
            return None
        o = va - self.base
        return int.from_bytes(self.data[o:o + 4], "little")

    #: x87 mnemonics whose memory operand is an integer or a control/status
    #: word rather than a float, so its width names itself (`word ptr`) instead
    #: of being spelled `float`/`double`/`extended double`.
    X87_INT = frozenset(("FILD", "FIST", "FISTP", "FIADD", "FISUB", "FISUBR",
                         "FIMUL", "FIDIV", "FIDIVR", "FICOM", "FICOMP",
                         "FNSTSW", "FSTSW", "FNSTCW", "FSTCW", "FLDCW",
                         "FNSTENV", "FLDENV", "FNSAVE", "FSAVE", "FRSTOR"))
    REP_PREFIX = {"rep": "REP", "repe": "REPE", "repz": "REPE",
                  "repne": "REPNE", "repnz": "REPNE"}
    #: Mnemonics capstone spells differently from the listing grammar.
    MNEM_ALIAS = {"POPAL": "POPAD", "PUSHAL": "PUSHAD",
                  "POPFL": "POPFD", "PUSHFL": "PUSHFD",
                  "CWTL": "CWDE", "CLTD": "CDQ", "CBTW": "CBW",
                  "IRETD": "IRET"}

    def _size_word(self, nbytes, mnem):
        if mnem.startswith("F") and mnem not in self.X87_INT:
            return {4: "float", 8: "double", 10: "extended double"}.get(nbytes)
        return {1: "byte", 2: "word", 4: "dword", 8: "qword",
                10: "extended double"}.get(nbytes)

    def _op_text(self, ci, op, mnem):
        import capstone.x86_const as X
        if op.type == X.X86_OP_REG:
            name = ci.reg_name(op.reg).upper()
            if name.startswith("ST(") and name.endswith(")"):
                return "ST" + name[3:-1]        # capstone st(1) -> ST1
            return name
        if op.type == X.X86_OP_IMM:
            return "-0x%x" % -op.imm if op.imm < 0 else "0x%x" % op.imm
        if op.type == X.X86_OP_MEM:
            word = self._size_word(op.size, mnem)
            if word is None:
                raise TranslateError("unknown operand width %d" % op.size)
            parts = []
            if op.mem.base:
                parts.append(ci.reg_name(op.mem.base).upper())
            if op.mem.index:
                parts.append("%s*0x%x" % (ci.reg_name(op.mem.index).upper(), op.mem.scale))
            if op.mem.disp or not parts:
                parts.append("-0x%x" % -op.mem.disp if op.mem.disp < 0
                             else "0x%x" % op.mem.disp)
            inner = " + ".join(parts)
            seg = ci.reg_name(op.mem.segment).upper() if op.mem.segment else ""
            return "%s ptr %s[%s]" % (word, seg + ":" if seg else "", inner)
        raise TranslateError("unknown capstone operand type %d" % op.type)

    def to_insn(self, ci):
        """Build a listing Insn from a capstone instruction."""
        words = ci.mnemonic.split()
        rep = None
        if words[0] in self.REP_PREFIX:
            rep = self.REP_PREFIX[words[0]]
            words = words[1:]
        mnem = words[-1].upper()
        mnem = self.MNEM_ALIAS.get(mnem, mnem)
        ops = [self._op_text(ci, o, mnem) for o in ci.operands]
        if rep or mnem in Translator.STRING_MNEM:
            ops = []            # implicit ES:EDI / ESI operands carry nothing
        text = "%08x  %s%s%s" % (ci.address, mnem, "." + rep if rep else "",
                                 (" " + ",".join(ops)) if ops else "")
        return Insn(ci.address, mnem, rep, ops, text)

    #: How far a linear sweep may run before giving up.  Large enough that no
    #: real function reaches it - the longest in this binary is 4,058
    #: instructions - so exhausting it is reported as an error rather than
    #: quietly deciding the address was data.
    RECOVER_LIMIT = 65536

    #: How far from the entry a recovered block may follow a branch.  The
    #: largest function in this binary is 14,816 bytes, so 32 KB covers a
    #: whole one without letting a far jump merge two unrelated regions:
    #: 00430ad2 was reaching back 60 KB to 0041fc70.
    RECOVER_WINDOW = 0x8000

    def recover(self, start, listed, limit=None, bounds=None, boundaries=(),
                may_pass=None):
        """Decode the block at `start`, following its branches.

        Recursive descent rather than a linear sweep.  A sweep stops at the
        first RET, which cuts a real function into pieces at every early
        return; each piece then has to be discovered and validated on its own,
        and one piece failing takes out everything that branches to it.  That
        is what dropped 0053fd30, a perfectly ordinary function, because a
        fragment several branches away decoded badly.

        Following the branches keeps a function whole: its internal targets
        become labels inside one block.  Targets outside the window, or into
        code already translated, are left for the discovery loop to resolve as
        separate entries, exactly as before. Optional bounds constrain an
        omitted continuation to its original function span and require a clean
        decode, rejecting padding or an instruction that crosses the bound.
        Candidate boundaries stop each linear path, including partial opcodes;
        a branch can still skip a separate stub to another block of this body.
        `may_pass` is asked about a stop before it is honoured, so a caller
        that has judged a claim worthless can rebuild the body it covers
        instead of stopping one instruction short of it.
        """
        if limit is None:
            limit = self.RECOVER_LIMIT
        if self.md is None or not (self.base <= start < self.end):
            return []
        lo, hi = bounds if bounds is not None else (self.base, self.end)
        boundaries = sorted(a for a in boundaries if lo <= a < hi and a != start)
        boundary_set = set(boundaries)
        self.md.detail = True
        seen = {}
        pending = [start]
        truncated = False
        while pending and len(seen) < limit:
            va = pending.pop()
            while len(seen) < limit:
                claimed = va in listed and not (may_pass is not None and may_pass(va))
                if va in seen or claimed or va in boundary_set or not (lo <= va < hi):
                    break
                got = list(self.md.disasm(self.data[va - self.base:va - self.base + 16],
                                          va, count=1))
                if not got or got[0].address + got[0].size > hi:
                    if bounds is not None:
                        self.md.detail = False
                        return []
                    break
                ci = got[0]
                index = bisect_right(boundaries, va)
                if index < len(boundaries) and ci.address + ci.size > boundaries[index]:
                    break  # Do not consume another candidate's first opcode byte.
                if ci.mnemonic in ("int3", "hlt", "(bad)"):
                    if bounds is not None:
                        self.md.detail = False
                        return []
                    break
                try:
                    seen[va] = self.to_insn(ci)
                except TranslateError as e:
                    self.recover_errors.append(
                        "%08x: recovery abandoned at %08x: %s" % (start, ci.address, e))
                    self.md.detail = False
                    return []
                nxt = ci.address + ci.size
                if ci.op_str.startswith("0x") and (
                        ci.mnemonic == "jmp" or ci.mnemonic.startswith("j")):
                    t = int(ci.op_str, 16)
                    if (abs(t - start) <= self.RECOVER_WINDOW and t not in listed
                            and lo <= t < hi and self.is_exec(t)):
                        pending.append(t)
                if ci.mnemonic in ("ret", "retn", "jmp"):
                    break
                if (ci.mnemonic == "call" and ci.op_str.startswith("0x")
                        and int(ci.op_str, 16) in self.noreturn_callees):
                    break
                va = nxt
            else:
                truncated = True
        self.md.detail = False
        if truncated or len(seen) >= limit:
            self.recover_errors.append(
                "%08x: recovery hit the %d-instruction budget; block not emitted"
                % (start, limit))
            return []
        return [seen[a] for a in sorted(seen)]

    def insn_end(self, va, mnem):
        """Address just past the instruction at `va`, or None if unknown.

        The Ghidra listing folds a WAIT prefix into the following x87
        mnemonic; capstone reports the two separately, so skip a leading
        WAIT when the listing calls the instruction something else.
        """
        if self.md is None or not (self.base <= va and va + 16 <= self.end):
            return None
        o = va - self.base
        got = list(self.md.disasm(self.data[o:o + 16], va, count=2))
        if not got:
            return None
        if (got[0].mnemonic in ("wait", "fwait") and mnem != "WAIT"
                and mnem.startswith("F")):
            return got[1].address + got[1].size if len(got) > 1 else None
        return got[0].address + got[0].size

    def instruction_at(self, va):
        """Decode one structural instruction without walking its successors."""
        if self.md is None or not self.is_exec(va):
            return None
        detail = self.md.detail
        self.md.detail = True
        try:
            ci = next(self.md.disasm(self.data[va - self.base:va - self.base + 16],
                                    va, count=1), None)
            return self.to_insn(ci) if ci is not None else None
        finally:
            self.md.detail = detail


# -------------------------------------------------------------- translator --

class Function(object):
    def __init__(self, addr, name, size, insns):
        self.addr = addr
        self.name = name
        self.size = size
        self.insns = patch_instructions(insns)
        self.addrs = {i.addr for i in insns}
        self.end = max(addr + size, insns[-1].addr + 1) if insns else addr
        # filled in by measure(): contiguous[i] is True when insn i+1 in the
        # listing really is insn i's fall-through successor.
        self.contiguous = [True] * len(insns)
        self.fallthrough = [None] * len(insns)

    def measure(self, image):
        """Find listing gaps: places where the next listed instruction is not
        where the current one actually ends."""
        for i, ins in enumerate(self.insns):
            e = image.insn_end(ins.addr, ins.mnem)
            self.fallthrough[i] = e
            nxt = self.insns[i + 1].addr if i + 1 < len(self.insns) else self.end
            if e is not None and e != nxt:
                self.contiguous[i] = False


TERMINATORS = frozenset(("RET", "JMP"))


def seh_chain_operand(op, zero_base=0):
    """Only the Delphi chain-head spellings, not other fields in the TEB."""
    return (op.kind == "mem" and op.size == 32 and op.seg == "FS"
            and op.base in (None, zero_base) and op.index is None and op.disp == 0)


def seh_zero_base(fn, i, reg):
    """Prove a zero FS base through a short straight-line register-preserving span."""
    if reg is None:
        return True
    if reg == R_ESP:
        return False
    targets = {Translator.branch_target(ins) for ins in fn.insns
               if ins.mnem in JCC or ins.mnem == "JMP"}
    for j in range(i - 1, max(-1, i - 16), -1):
        if not fn.contiguous[j] or fn.insns[j + 1].addr in targets:
            return False
        ins = fn.insns[j]
        if ins.mnem == "XOR" and len(ins.ops) == 2:
            a, b = [parse_operand(o) for o in ins.ops]
            if a.kind == b.kind == "reg" and a.reg == b.reg == reg and a.size == b.size == 32:
                return True
        if (ins.mnem not in ("MOV", "LEA", "POP", "PUSH", "ADD", "SUB", "AND", "OR",
                              "XOR", "TEST", "CMP", "NOP")
                or Translator.writes_reg32(ins, reg)):
            return False
    return False


def seh_restore_sites(fn):
    """Recognize unlink-only helpers as well as restores in establishing bodies."""
    result = set()
    for i, ins in enumerate(fn.insns):
        if ins.mnem not in ("POP", "MOV") or not ins.ops:
            continue
        dst = parse_operand(ins.ops[0])
        if not seh_chain_operand(dst, dst.base) or not seh_zero_base(fn, i, dst.base):
            continue
        if ins.mnem == "POP":
            result.add(i)
        elif len(ins.ops) == 2:
            src = parse_operand(ins.ops[1])
            if src.kind == "reg" and src.size == 32 and src.reg != R_ESP:
                result.add(i)
    return result


def seh_frame_sites(fn, image=None):
    """Map establishing MOV/helper CALL indices to their handler addresses."""
    sites = {}
    helper = getattr(image, "seh_constructor_helper", None)
    if helper is not None:
        stub = helper(fn.addr)
        if stub is not None:
            # The verified allocator helper publishes caller-reserved words
            # through ECX. Keep its checkpoint live until its return, then
            # let the caller adopt it; never leave setjmp in a dead helper.
            for i, ins in enumerate(fn.insns):
                if ins.mnem == "MOV" and len(ins.ops) == 2:
                    dst, src = [parse_operand(op) for op in ins.ops]
                    if (seh_chain_operand(dst, 2) and dst.base == 2
                            and src.kind == "reg" and src.reg == 1):
                        sites[i] = stub
        for i, ins in enumerate(fn.insns):
            if not i or ins.mnem != "CALL" or not fn.contiguous[i - 1]:
                continue
            target = Translator.branch_target(ins)
            reserve = fn.insns[i - 1]
            if target is None or reserve.mnem not in ("ADD", "SUB") or len(reserve.ops) != 2:
                continue
            dst, amount = [parse_operand(op) for op in reserve.ops]
            if (dst.kind != "reg" or dst.reg != 4 or dst.size != 32 or amount.kind != "imm"
                    or amount.imm & 0xffffffff != (0xfffffff0 if reserve.mnem == "ADD" else 16)):
                continue
            stub = helper(target)
            if stub is not None:
                sites[i] = stub
    for i in range(2, len(fn.insns)):
        mov, push = fn.insns[i], fn.insns[i - 1]
        if mov.mnem != "MOV" or push.mnem != "PUSH" or len(mov.ops) != 2:
            continue
        try:
            dst, src = [parse_operand(o) for o in mov.ops]
            # The same frame idiom can zero another register before its
            # three PUSHes. Prove that spelling locally; an arbitrary FS
            # register operand can address a different TEB field.
            zero_base = dst.base if seh_zero_base(fn, i, dst.base) else 0
            if (not seh_chain_operand(dst, zero_base) or src.kind != "reg" or src.reg != 4
                    or src.size != 32 or not seh_chain_operand(parse_operand(push.ops[0]), zero_base)
                    or not fn.contiguous[i - 1]):
                continue
            for j in range(i - 2, max(-1, i - 4), -1):
                prev = fn.insns[j]
                if not fn.contiguous[j]:
                    break
                if prev.mnem == "PUSH" and prev.ops:
                    imm = parse_operand(prev.ops[0])
                    if imm.kind == "imm":
                        sites[i] = imm.imm
                    break
        except TranslateError:
            continue
    return sites


NORETURN_IMPORTS = frozenset(("RaiseException", "ExitProcess", "TerminateProcess",
                              "ExitThread", "FatalAppExitA", "FatalExit"))
#: The ones a `CALL dword ptr [IAT slot]` may end its block on: RaiseException is left out, because a continuable
#: exception (dwExceptionFlags 0) returns when a handler continues execution, and code follows the call (NFSMW's
#: C runtime raises floating-point exceptions at 007da5bc and reads the record back after it; run 37).
NORETURN_IAT_IMPORTS = NORETURN_IMPORTS - {"RaiseException"}


def noreturn_callees_from(parsed, iat_names, image=None):
    """Callees that never return, as the listings show them.

    A listing that ends on a CALL was cut there because the callee never
    returns; recovery from the PE must stop at those calls too, or it walks
    into the padding and switch tables that follow them.  Ghidra also cuts
    listings at calls for other reasons - a C++ catch funclet ends where its
    rethrow begins - so a callee whose own listing returns is not taken on
    that evidence, unless it hands control to an import that does not come
    back (`_CxxThrowException` raises through RaiseException and has a RET
    after it that never runs).  Padding (INT3) right after the call is
    evidence of its own: the compiler put nothing there to return to."""
    def ends_process(fn):
        for i in fn.insns:
            if i.mnem != "CALL" or not i.ops:
                continue
            m = re.match(r"dword ptr \[(0x[0-9a-fA-F]+)\]$", i.ops[0].strip())
            if m and iat_names.get(int(m.group(1), 16)) in NORETURN_IMPORTS:
                return True
        return False

    returns = {fn.addr for fn in parsed
               if any(i.mnem == "RET" for i in fn.insns) and not ends_process(fn)}
    out = set()
    for fn in parsed:
        # A loop that cannot fall out of itself never returns either.
        if Translator.closed_noreturn_loop(fn):
            out.add(fn.addr)
        last = fn.insns[-1]
        if last.mnem == "CALL":
            target = Translator.branch_target(last)
            if target is None:
                continue
            padded = False
            if image is not None:
                nxt = last.addr + 5 - image.base
                padded = 0 <= nxt < len(image.data) and image.data[nxt] == 0xCC
            if target not in returns or padded:
                out.add(target)
    return out


class Translator(object):
    def __init__(self, image, func_addrs, opts):
        self.image = image
        self.func_addrs = func_addrs      # set of all known function entry points
        self.opts = opts
        self.stats = defaultdict(int)
        # Instructions replaced by a trap because this translator cannot model
        # them. Reported at the end of a run: a listing decoding data as code
        # should be visible, not silent.
        self.unmodelled = []
        # --allow-unmodelled: without a reason on the command line an
        # instruction this translator cannot model still refuses the image.
        self.allow_unmodelled = getattr(opts, "allow_unmodelled", None)
        self.notes = []
        self.jumptables = {}
        self.unlisted_targets = set()
        self.all_insn_addrs = set()
        #: byte ranges holding decoded jump tables; an address inside one is
        #: table storage, not code, however much it looks like a function.
        self.table_ranges = set()
        #: Every table base seen, so one table's extent can be capped by the
        #: next one's start even before that table has been decoded.
        self.table_bases = set()
        #: alternate entries that turned out not to be in their block
        self.stale_entries = set()
        #: (function, jmp address) -> table base, for every statically based
        #: table-shaped jump inside the image, decoded or not
        self.table_sites = {}
        #: sites whose extent is inferred rather than derived from a bound
        self.table_sites_inferred = set()
        self.jmp_index = 0
        self.strict = False
        #: Direct-call targets that never return (see Image.noreturn_callees).
        #: A CALL to one ends its block: the emitter leaves a trap in place of
        #: the fall-through instead of a jump onto the padding that follows.
        self.noreturn_callees = set()
        #: IAT slots of imports that never return (NORETURN_IAT_IMPORTS). A
        #: `CALL dword ptr [slot]` to one ends its block the same way, so a
        #: listing that stops on `CALL [ExitProcess]` does not fall through
        #: into the INT3 padding behind it (MSVC 7 ___crtExitProcess).
        self.noreturn_iat = set()
        self.seh_helpers = set()
        #: Does a pointer to this address name data rather than code? The
        #: driver installs its judgement before the first translation pass
        #: (see main); until then nothing is taken for a literal.
        self.points_at_a_literal = lambda target: None
        #: pushed addresses live_continuations refused as literals
        self.literal_continuations = set()
        #: instructions dead_after_noreturn kept out of any body
        self.dead_after_noreturn_addrs = set()

    def seh_escaping_returns(self, fn):
        """Return paths with an established chain record but no matching unlink.

        Begin at the normal entry, not at exception landing aliases. A helper
        can return by RET or a proven jump through its popped return register.
        Calls to already marked helpers propagate ownership to their caller.
        """
        if not fn.seh_sites and not any(ins.mnem == "CALL" and
                                       self.branch_target(ins) in self.seh_helpers
                                       for ins in fn.insns):
            return set()
        returns = self.popped_return_jumps(fn, ())
        work, seen, escapes = [(fn.index[fn.addr], False)], set(), set()
        while work:
            i, active = work.pop()
            if (i, active) in seen:
                continue
            seen.add((i, active))
            ins = fn.insns[i]
            if i in fn.seh_sites or (ins.mnem == "CALL" and
                                    self.branch_target(ins) in self.seh_helpers):
                active = True
            elif i in fn.seh_restores:
                active = False
            if i in returns or (ins.mnem == "RET" and self.push_ret_target(fn, i) is None):
                if active:
                    escapes.add(i)
                if i in returns:
                    continue
            work.extend((j, active) for j in self.successors(fn, i))
        return escapes

    def discover_seh_helpers(self, functions):
        """Propagate escaping helper calls after all normal bodies are indexed."""
        self.seh_helpers.clear()
        while True:
            before = len(self.seh_helpers)
            for fn in functions:
                try:
                    escaping = self.seh_escaping_returns(fn)
                except TranslateError:
                    continue  # a body this pass cannot read is not a helper
                if escaping:
                    self.seh_helpers.add(fn.addr)
            if len(self.seh_helpers) == before:
                break

    def never_returns(self, ins):
        """Is `ins` a CALL that never returns: a direct call to a callee the
        listings show never returning, or an IAT call to a no-return import?"""
        if ins.mnem != "CALL":
            return False
        if self.noreturn_iat and ins.ops:
            m = re.match(r"dword ptr \[(0x[0-9a-fA-F]+)\]$", ins.ops[0].strip())
            if m and int(m.group(1), 16) in self.noreturn_iat:
                return True
        t = self.branch_target(ins)
        return t is not None and t in self.noreturn_callees

    @staticmethod
    def closed_noreturn_loop(fn):
        """Prove no normal return when every non-call edge stays in the body.

        This deliberately rejects RETs, indirect/outward jumps and listing
        gaps. Calls may return to their successor or exit/unwind; neither can
        make a closed loop return to its caller. Shutdown routines use this
        shape even when their callers retain unreachable RET instructions.
        """
        for i, ins in enumerate(fn.insns):
            if ins.mnem.startswith("RET") or ins.mnem.startswith("IRET"):
                return False
            if ins.mnem == "JMP" or ins.mnem in JCC:
                if Translator.branch_target(ins) not in fn.addrs:
                    return False
                if ins.mnem == "JMP":
                    continue
            if fn.fallthrough[i] not in fn.addrs:
                return False
        return bool(fn.insns)

    # -- control flow ------------------------------------------------------

    @staticmethod
    def pushed_continuations(fn):
        """Instruction boundaries in this body that PUSH names as continuations."""
        targets = set()
        for ins in fn.insns:
            if ins.mnem != "PUSH" or not ins.ops:
                continue
            try:
                op = parse_operand(ins.ops[0])
            except TranslateError:
                continue  # not a PUSH imm32; it cannot name a continuation
            if op.kind == "imm" and operand_size([op], hint=32) == 32:
                target = op.imm & 0xffffffff
                if target in fn.addrs:
                    targets.add(target)
        return targets

    def live_continuations(self, fn):
        """Pushed continuations, less the pushed pointers that name a literal.

        A PUSH imm32 into this body is taken for a continuation because the
        body can leave by RET or POP reg; JMP reg. But Delphi keeps string
        literals in .text, and a pointer to one pushed as an argument is not a
        place anything returns to: Siege of Avalon pushes the mutex name
        L"DigitalTomeSiegeOfAvalon", which sits in the same listed body.
        """
        out = set()
        for t in self.pushed_continuations(fn):
            if self.points_at_a_literal(t):
                self.literal_continuations.add(t)
            else:
                out.add(t)
        return out

    def push_ret_target(self, fn, i):
        """An adjacent PUSH imm32 / RET is a jump, including Delphi epilogues.

        Lower it on the PUSH path only: another entry at the RET still returns
        to its own caller (finally handlers share that instruction).
        """
        ins = fn.insns[i]
        if (ins.mnem == "PUSH" and i + 1 < len(fn.insns) and fn.contiguous[i]
                and fn.insns[i + 1].mnem == "RET" and not fn.insns[i + 1].ops):
            try:
                op = parse_operand(ins.ops[0])
            except TranslateError:
                return None  # not a PUSH imm32, so not this pattern
            if op.kind == "imm" and operand_size([op], hint=32) == 32:
                return op.imm & 0xffffffff
        return None

    def popped_return_jumps(self, fn, entries):
        """Prove JMPs through the caller's return slot without runtime lookup.

        Facts are (ESP delta, EBP delta, registers popped at delta zero).
        Joins retain only agreeing facts; alternate entries start independent
        call frames. CALL is stack-neutral here, including cleanup calls after
        a saved return has been popped. Explicit/implicit register writes kill
        that register's fact. Unknown instructions or indirect destinations
        discard facts rather than guessing that a computed target is a return.
        """
        if not any(ins.mnem == "POP" for ins in fn.insns):
            return set()
        candidates = {i for i, ins in enumerate(fn.insns)
                      if ins.mnem == "JMP" and ins.ops and ins.ops[0] in REG32}
        if not candidates:
            return set()
        unknown = (None, None, frozenset())
        states, pending = {}, []

        def merge(i, state):
            old = states.get(i)
            if old is not None:
                state = (old[0] if old[0] == state[0] else None,
                         old[1] if old[1] == state[1] else None, old[2] & state[2])
            if state != old:
                states[i] = state
                pending.append(i)

        def transfer(ins, state):
            delta, frame, saved = state
            m = ins.mnem
            try:
                ops = ([parse_operand(o) for o in ins.ops]
                       if m not in self.STRING_MNEM or names_an_xmm(ins) else [])
            except TranslateError:
                # An operand this translator cannot even spell is the strongest
                # unmodelled form there is, and the rule above already covers
                # it: the proof does not survive one. The instruction itself
                # becomes a trap wherever it is emitted, so the only thing
                # raising here would change is which pass reports it - and in
                # an entry stub whose listing runs off into padding, that is
                # the difference between a translated image and no image.
                return unknown
            writes = set()
            # Read-only instructions and calls do not explicitly redefine a
            # saved return register. All unmodelled forms invalidate the proof.
            readonly = {"CMP", "TEST", "PUSH", "CALL", "JMP", "RET", "NOP", "WAIT",
                        "PAUSE", "CLC", "STC", "CMC", "CLD", "STD", "SAHF", "CLI", "STI"}
            dest_write = {"MOV", "MOVZX", "MOVSX", "LEA", "POP", "ADD", "ADC", "SUB",
                          "SBB", "AND", "OR", "XOR", "INC", "DEC", "NEG", "NOT", "SHL",
                          "SHR", "SAR", "ROL", "ROR", "RCL", "RCR", "SHLD", "SHRD",
                          "BSWAP", "BSF", "BSR", "BTS", "BTR", "BTC", "IMUL"}
            if m in readonly or m in JCC or m.startswith("F"):
                if m in ("FNSTSW", "FSTSW"):
                    writes.add(R_EAX)
            elif m in dest_write or m.startswith("SET") or m.startswith("CMOV"):
                if ops and ops[0].kind == "reg":
                    writes.add(ops[0].reg)
            elif m in ("XCHG", "XADD", "CMPXCHG"):
                writes.update(o.reg for o in ops if o.kind == "reg")
                if m == "CMPXCHG":
                    writes.add(R_EAX)
            elif m in ("PUSHAD", "PUSHFD", "PUSHF", "POPFD", "POPF"):
                writes.add(R_ESP)
            elif m == "LEAVE":
                writes.update((R_ESP, R_EBP))
            elif m in ("MUL", "DIV", "IDIV", "RDTSC"):
                writes.update((R_EAX, R_EDX))
            elif m in ("CDQ", "CWD"):
                writes.add(R_EDX)
            elif m in ("CBW", "CWDE", "LAHF", "XLAT"):
                writes.add(R_EAX)
            else:
                return unknown
            if m == "IMUL" and len(ops) == 1:
                writes.update((R_EAX, R_EDX))
            if m.startswith("LOOP"):
                writes.add(R_ECX)
            result = set(saved) - writes
            if (m == "POP" and delta == 0 and ops[0].kind == "reg"
                    and ops[0].size == 32 and ops[0].reg != R_ESP):
                result.add(ops[0].reg)
            adjustment = None
            if m in ("PUSH", "POP"):
                adjustment = operand_size(ops, hint=32) // 8 * (-1 if m == "PUSH" else 1)
            elif m in ("PUSHAD", "PUSHFD", "PUSHF", "POPFD", "POPF"):
                adjustment = {"PUSHAD": -32, "PUSHFD": -4, "PUSHF": -2,
                              "POPFD": 4, "POPF": 2}[m]
            elif m == "RET":
                adjustment = 4 + (parse_imm(ins.ops[0]) if ins.ops else 0)
            elif (m in ("ADD", "SUB") and ops[0].kind == "reg"
                  and ops[0].reg == R_ESP and ops[0].size == 32 and ops[1].kind == "imm"):
                immediate = ops[1].imm & 0xffffffff
                if immediate >= 0x80000000:
                    immediate -= 0x100000000
                adjustment = immediate * (-1 if m == "SUB" else 1)
            if m == "LEAVE":
                new_delta = frame + 4 if frame is not None else None
            elif m == "POP" and ops[0].kind == "reg" and ops[0].reg == R_ESP:
                new_delta = None
            elif adjustment is not None:
                new_delta = delta + adjustment if delta is not None else None
            else:
                new_delta = None if R_ESP in writes else delta
            new_frame = None if R_EBP in writes else frame
            if (m == "MOV" and ins.ops == ["EBP", "ESP"]):
                new_frame = delta
            return new_delta, new_frame, frozenset(result)

        for addr in (fn.addr, *entries):
            merge(fn.index[addr], (0, None, frozenset()))
        unknown_indirect_seen = False
        while pending:
            i = pending.pop()
            ins, state = fn.insns[i], states[i]
            if i in candidates and parse_operand(ins.ops[0]).reg in state[2]:
                continue
            out = transfer(ins, state)
            if ins.mnem == "JMP" and self.branch_target(ins) is None:
                # A non-return computed jump can enter anywhere. Seed unknown
                # facts once, avoiding a quadratic all-to-all propagation.
                if not unknown_indirect_seen:
                    for j in range(len(fn.insns)):
                        merge(j, unknown)
                    unknown_indirect_seen = True
                continue
            for j in self.successors(fn, i):
                merge(j, out)
        return {i for i in candidates if i in states
                and parse_operand(fn.insns[i].ops[0]).reg in states[i][2]}

    def successors(self, fn, i):
        """Indices reachable from insn i, and whether flags escape the function."""
        ins = fn.insns[i]
        m = ins.mnem
        t = self.push_ret_target(fn, i)
        if t is not None and t not in fn.pushed_continuations:
            return [fn.index[t]] if t in fn.index else []
        nxt = i + 1 if (i + 1 < len(fn.insns) and fn.contiguous[i]) else None
        if m == "INT3":
            # MSVC pads between functions with INT3, and a listing cut at a
            # call that never returns runs that padding into the next
            # function. Nothing falls out of the padding: the byte after it
            # belongs to whatever comes next, and is not this block's
            # successor. The instruction itself still traps at run time.
            return []
        if m == "RET":
            return [fn.index[t] for t in sorted(fn.pushed_continuations)]
        if m in JCC:
            out = [nxt] if nxt is not None else []
            t = self.branch_target(ins)
            if t is not None and t in fn.index:
                out.append(fn.index[t])
            return out
        if m == "JMP":
            if ins.ops and ins.ops[0].startswith("0x"):
                t = int(ins.ops[0], 16)
                return [fn.index[t]] if t in fn.index else []
            tgts = self.jumptables.get((fn.addr, ins.addr)) or fn.addrs
            return [fn.index[t] for t in tgts if t in fn.index]
        return [nxt] if nxt is not None else []

    @staticmethod
    def popped_jump(fn, i):
        """POP reg; JMP reg: Delphi leaving a finally block for a continuation
        it pushed, or a return through a popped return address."""
        ins = fn.insns[i]
        return (ins.mnem == "JMP" and bool(ins.ops) and ins.ops[0] in REG32
                and i > 0 and fn.contiguous[i - 1] and fn.insns[i - 1].mnem == "POP"
                and fn.insns[i - 1].ops == ins.ops)

    def dead_after_noreturn(self, fn, entries):
        """Instructions after a call that never returns that nothing reaches.

        Ghidra does not know _Halt0 or ExitProcess never return and lists on
        through the bytes behind the call, and Delphi keeps string literals
        there: Siege of Avalon's single-instance check ends CALL _Halt0 and is
        followed by L"DigitalTomeSiegeOfAvalon", whose 16-bit addressing the
        emitter refuses. Only such a tail is dropped - a run behind a call
        that never returns, reached from no entry, pushed continuation or
        branch. A computed jump other than POP reg; JMP reg may land anywhere
        (RTL fill routines compute addresses into unrolled code), so a body
        holding one keeps everything. POP reg; JMP reg goes where a
        continuation was pushed, or out of the function.
        """
        if not any(self.never_returns(ins) for ins in fn.insns):
            return set()
        live = self.reached(fn, {fn.addr} | set(entries))
        dead, after_noreturn = set(), False
        for i, ins in enumerate(fn.insns):
            if i in live:
                after_noreturn = self.never_returns(ins)
            elif after_noreturn:
                dead.add(i)
        return dead

    def reached(self, fn, roots):
        """Indices control flow reaches from `roots` and the pushed
        continuations; every index if the body has a computed jump that is
        not POP reg; JMP reg. A call that never returns ends a path."""
        if any(ins.mnem == "JMP" and self.branch_target(ins) is None
               and not self.jumptables.get((fn.addr, ins.addr))
               and not self.popped_jump(fn, i) for i, ins in enumerate(fn.insns)):
            return set(range(len(fn.insns)))
        roots = set(roots) | set(fn.pushed_continuations)
        work = [fn.index[a] for a in roots if a in fn.index]
        live = set()
        while work:
            i = work.pop()
            if i in live:
                continue
            live.add(i)
            if self.never_returns(fn.insns[i]):
                continue
            if self.popped_jump(fn, i):
                work.extend(fn.index[t] for t in fn.pushed_continuations)
                continue
            if fn.insns[i].mnem not in TERMINATORS and not fn.contiguous[i]:
                # A listing gap: emission jumps to the fall-through address.
                t = fn.fallthrough[i]
                if t in fn.index:
                    work.append(fn.index[t])
                continue
            work.extend(j for j in self.successors(fn, i) if j is not None)
        return live

    @staticmethod
    def branch_target(ins):
        if ins.ops and ins.ops[0].startswith("0x"):
            return int(ins.ops[0], 16)
        return None

    #: instructions that end a basic block; flags are live across all of them
    BOUNDARY = frozenset(("CALL", "RET", "JMP"))

    def liveness(self, fn):
        """Flag liveness within straight-line code only.

        Plan correction 6: flags are live at every control-flow boundary, so a
        CALL, RET, JMP or conditional jump has all six live on the way out and
        elimination only happens between them.  What this still removes is a
        flag write that a later instruction in the same run overwrites before
        anything reads it, which is most of them.
        """
        n = len(fn.insns)
        live_out = [ALL_FLAGS] * n
        for i in range(n - 2, -1, -1):
            ins = fn.insns[i]
            # A listing gap is a control-flow boundary too: emission inserts a
            # goto or a tail call there, so the next listed instruction is not
            # the fall-through successor.
            if (ins.mnem in self.BOUNDARY or ins.mnem in JCC
                    or not fn.contiguous[i]):
                live_out[i] = ALL_FLAGS
                continue
            d, u = flag_effect(fn.insns[i + 1])
            live_out[i] = (live_out[i + 1] - d) | u
        return live_out

    def liveness_cfg(self, fn, call_transparent=False):
        """CFG-wide liveness, used only by --check-flags to measure how often
        a flag would cross a call or return boundary."""
        n = len(fn.insns)
        live_in = [NO_FLAGS] * n
        live_out = [NO_FLAGS] * n
        succ = [self.successors(fn, i) for i in range(n)]
        changed = True
        while changed:
            changed = False
            for i in range(n - 1, -1, -1):
                ins = fn.insns[i]
                lo = NO_FLAGS
                for s in succ[i]:
                    lo = lo | live_in[s]
                if ins.mnem == "CALL" and not call_transparent:
                    li = NO_FLAGS
                elif ins.mnem == "CALL":
                    li = lo
                else:
                    d, u = flag_effect(ins)
                    li = (lo - d) | u
                if li != live_in[i] or lo != live_out[i]:
                    live_in[i], live_out[i] = li, lo
                    changed = True
        return live_in, live_out

    # -- jump tables -------------------------------------------------------

    def is_table_site(self, ins):
        """Does this jump index a dword table at a static image address?"""
        if ins.mnem != "JMP" or not ins.ops:
            return False
        try:
            op = parse_operand(ins.ops[0])
        except TranslateError:
            return False
        # An out-of-image displacement is an offset in a computed address,
        # not static table storage. Tables also live in executable sections,
        # so data_ranges alone would reject valid switches in .text.
        if not (op.kind == "mem" and op.disp
                and self.image.base <= op.disp < self.image.end):
            return False
        if op.index is not None and op.scale == 4:
            return True
        # Hand-written dispatchers can store byte offsets (0,4,8,...) in
        # their work records and JMP [reg + table]. Require consecutive code
        # pointers to distinguish this from a static object's field access.
        if op.base is not None and op.index is None:
            return all((target := self.image.rd32(op.disp + offset)) is not None
                       and self.image.is_exec(target) for offset in (0, 4))
        return False

    def decode_jumptable(self, fn, i):
        """Decode the switch behind `JMP dword ptr [reg*4 + base]`.

        Plan correction 8.  The bound comes from dataflow on the index
        register, which is not always the register the JMP indexes with: the
        common MSVC shape bounds one register with `CMP r,N` + `JA`, remaps it
        through a byte table, and indexes the dword table with the byte.  Both
        shapes are decoded exactly; only when neither is recognised does this
        fall back to reading entries until one stops being an instruction
        boundary.  In a bounded table an entry that is not an instruction
        boundary is an error, not a place to stop.
        """
        ins = fn.insns[i]
        if not self.is_table_site(ins):
            return None
        op = parse_operand(ins.ops[0])
        # Every table-shaped jump with an in-image displacement is a table read,
        # recorded whether or not the decode below succeeds: a site that
        # decodes nothing at all is the worst kind of gap and would otherwise
        # be invisible to the coverage check.
        self.table_sites[(fn.addr, ins.addr)] = op.disp
        byte_offset = op.base is not None and op.index is None
        if op.base is not None and not byte_offset:
            self.table_bases.add(op.disp)
            return self.two_index_table(fn, i, ins, op)
        dword_base = op.disp

        self.jmp_index = i
        kind, info = (None, None) if byte_offset else self.index_source(fn, i, op.index)
        if kind == "byte_table":
            byte_base, count = info
            targets = []
            for k in range(count):
                b = self.image.rd8(byte_base + k)
                if b is None:
                    raise TranslateError("jump table at %08x: byte table runs off "
                                         "the image at %08x" % (ins.addr, byte_base + k))
                t = self.image.rd32(dword_base + 4 * b)
                self.want_target(fn, ins, t, k)
                targets.append(t)
            self.table_ranges.add((byte_base, byte_base + count))
            self.table_ranges.add((dword_base, dword_base + 4 * (max(
                self.image.rd8(byte_base + k) for k in range(count)) + 1)))
            self.stats["_jmp_table_two_level"] += 1
            return targets
        if kind == "bounded":
            targets = []
            for k in range(info):
                t = self.image.rd32(dword_base + 4 * k)
                self.want_target(fn, ins, t, k)
                targets.append(t)
            self.table_ranges.add((dword_base, dword_base + 4 * info))
            self.stats["_jmp_table_bounded"] += 1
            return targets

        ranged = None if byte_offset else self.index_range(fn, i, op.index)
        if ranged is not None:
            lo, hi, holes = ranged
            targets = []
            for k in range(lo, hi + 1):
                t = self.image.rd32(dword_base + 4 * k)
                if holes and not (t is not None and (self.is_block_entry(fn, t)
                                                     or self.plausible_code(t))):
                    # A masked index the guards never produce: the slot
                    # holds whatever the compiler put there, usually code.
                    self.stats["_jmp_table_holes"] += 1
                    continue
                self.want_target(fn, ins, t, k)
                targets.append(t)
            self.table_ranges.add((dword_base + 4 * lo, dword_base + 4 * (hi + 1)))
            self.stats["_jmp_table_ranged"] += 1
            return targets

        # No bound recovered.  Read while the entries stay plausible code and
        # hand each one to the recovery machinery rather than stopping at the
        # first that is not yet a block entry: the FMV decoder's table at
        # 00d184b0 has an unrecovered address in slot 0, so stopping there
        # dropped the whole table.  The extent is capped by the next known
        # table's base.
        self.stats["_jmp_table_unbounded"] += 1
        self.table_sites_inferred.add((fn.addr, ins.addr))
        targets = []
        for k in range(4096):
            at = dword_base + 4 * k
            t = self.image.rd32(at)
            if t is None or t == 0:
                break
            if not self.is_block_entry(fn, t) and not self.plausible_code(t):
                break
            self.want_target(fn, ins, t, k, strict=False)
            targets.append(t)
        if targets:
            self.table_ranges.add((dword_base, dword_base + 4 * len(targets)))
        return targets or None

    #: Guards that branch TO a jump when the index is in range: `JC` after
    #: `CMP idx,N` leaves 0..N-1 at the target, after `SUB idx,N` -N..-1.
    RANGE_GUARDS = frozenset(("JC", "JB", "JNAE"))

    def index_range(self, fn, i, reg):
        """The signed range of `reg` at instruction i, or None.

        Visual C++ 6's hand-written `memcpy` bounds its table indices in ways
        the `CMP` + `JA` shape does not cover: a low-bit `AND` mask whose zero
        case an earlier test diverted (so slot 0 holds code, not an address),
        a `SUB idx,4` / `JC` pair whose taken branch reaches the jump with the
        index in -4..-1, a `CMP idx,N` / `JC` pair likewise leaving 0..N-1,
        and a `NEG` of a bounded index.  Returns (lo, hi, holes): `holes` is
        set for the mask shape, where slots the guards never produce may hold
        anything.  Fall-through dataflow is followed back to the nearest write
        of `reg`; a guard branching to the instruction contributes the range
        its compare or subtract leaves."""
        ins = fn.insns[i]
        ranges = []
        # Fall-through: the nearest earlier write of the register, provided
        # the path from it to here is straight-line code.
        for j in range(i - 1, max(-1, i - 16), -1):
            prev = fn.insns[j]
            if prev.mnem in TERMINATORS or not fn.contiguous[j]:
                break
            if prev.mnem == "CMP" and len(prev.ops) == 2:
                b = self.cmp_bound_here(fn, j, reg)
                if b is not None:
                    ranges.append((0, b - 1, False))
                break
            if not self.writes_reg32(prev, reg):
                continue
            try:
                ops = [parse_operand(o) for o in prev.ops]
            except TranslateError:
                break
            if prev.mnem == "AND" and len(ops) == 2 and ops[1].kind == "imm" \
                    and 0 < ops[1].imm < 256 and not (ops[1].imm & (ops[1].imm + 1)):
                ranges.append((0, ops[1].imm, True))
            elif prev.mnem == "NEG" and len(ops) == 1:
                inner = self.index_range(fn, j, reg)
                if inner is not None:
                    ranges.append((-inner[1], -inner[0], inner[2]))
            break
        # Branch-in: a guard whose taken edge lands here.
        for j, guard in enumerate(fn.insns):
            if guard.mnem not in self.RANGE_GUARDS or self.branch_target(guard) != ins.addr:
                continue
            for k in range(j - 1, max(-1, j - 6), -1):
                setter = fn.insns[k]
                defs, _uses = flag_effect(setter)
                if not defs:
                    continue
                if setter.mnem in ("CMP", "SUB") and len(setter.ops) == 2:
                    try:
                        a, b = parse_operand(setter.ops[0]), parse_operand(setter.ops[1])
                    except TranslateError:
                        break
                    if a.kind == "reg" and a.reg == reg and b.kind == "imm" and 0 < b.imm <= 0xFFFF:
                        if setter.mnem == "CMP":
                            ranges.append((0, b.imm - 1, False))
                        else:
                            ranges.append((-b.imm, -1, False))
                break
        if not ranges:
            return None
        return (min(r[0] for r in ranges), max(r[1] for r in ranges), any(r[2] for r in ranges))

    def plausible_code(self, va):
        """Could `va` be an address in this program's code?"""
        return self.image.is_exec(va) and self.image.looks_like_code_start(va) or (
            self.image.is_exec(va) and self.image.md is not None
            and bool(list(self.image.md.disasm(
                self.image.data[va - self.image.base:va - self.image.base + 16],
                va, count=1))))

    #: How many values a byte can take at the jump, given the guards in front
    #: of it.  `OR CL,CL; JS` sends the negative half elsewhere.
    BYTE_ROWS = 256
    BYTE_ROWS_SIGNED = 128

    def two_index_table(self, fn, i, ins, op):
        """`JMP dword ptr [row + col*4 + base]`: a two-dimensional table.

        The hand-written decoders at 00588917 and 0058ac4f dispatch on a byte
        and a two-bit phase at once:

            MOV CL,byte ptr [ESI] / OR CL,CL / JS ... / JE ...
            MOV EAX,ECX / SHL EAX,4 / MOV EBP,EDX / AND EBP,3
            JMP dword ptr [EAX + EBP*4 + 0x58d368]

        There is no bounding CMP, so the extent comes from the shapes instead:
        the `SHL` by k makes the row stride 1 << k bytes, its operand is a
        byte so there are at most 256 rows and the `JS` halves that, and the
        `AND` with m leaves m+1 columns.  A zero entry is a hole rather than
        the end - row 0 is all zeros here, because the `JE` diverts the zero
        byte before the jump is reached.
        """
        rows = self.shift_rows(fn, i, op.base)
        cols = self.and_columns(fn, i, op.index)
        if not rows or not cols:
            return None
        stride, count = rows
        # The extent is exactly what the geometry proves: rows the guard makes
        # reachable, times the masked column count.  The next table's base is
        # an upper bound on where this one could end, not evidence that the
        # space between is table storage, and treating it as evidence both
        # swallows intervening data as targets and hides it from the pointer
        # scan.
        targets, holes = [], 0
        for r in range(count):
            for col in range(cols):
                at = op.disp + r * stride + col * op.scale
                t = self.image.rd32(at)
                if t is None:
                    break
                if t == 0:
                    holes += 1
                    continue
                self.want_target(fn, ins, t, r * cols + col, strict=False)
                targets.append(t)
        if not targets:
            return None
        self.table_ranges.add((op.disp, op.disp + count * stride))
        self.table_sites_inferred.add((fn.addr, ins.addr))
        self.stats["_jmp_table_two_index"] += 1
        self.stats["_jmp_table_holes"] += holes
        return targets

    def shift_rows(self, fn, i, reg):
        """(row stride in bytes, row count) from the `SHL reg,k` feeding it."""
        signed_guard = False
        for j in range(i - 1, max(-1, i - 20), -1):
            ins = fn.insns[j]
            if ins.mnem == "JS":
                signed_guard = True
            if ins.mnem != "SHL" or len(ins.ops) != 2:
                continue
            try:
                d, k = parse_operand(ins.ops[0]), parse_operand(ins.ops[1])
            except TranslateError:
                return None
            if d.kind != "reg" or d.reg != reg or k.kind != "imm":
                continue
            if not (0 < k.imm < 16):
                return None
            # Look a little further back for the sign guard on the byte.
            for j2 in range(j - 1, max(-1, j - 12), -1):
                if fn.insns[j2].mnem == "JS":
                    signed_guard = True
            rows = self.BYTE_ROWS_SIGNED if signed_guard else self.BYTE_ROWS
            return (1 << k.imm, rows)
        return None

    def and_columns(self, fn, i, reg):
        """Column count from the `AND reg,m` masking the index."""
        for j in range(i - 1, max(-1, i - 20), -1):
            ins = fn.insns[j]
            if ins.mnem != "AND" or len(ins.ops) != 2:
                continue
            try:
                d, m = parse_operand(ins.ops[0]), parse_operand(ins.ops[1])
            except TranslateError:
                return None
            if d.kind != "reg" or d.reg != reg or m.kind != "imm":
                continue
            if not (0 < m.imm < 256) or (m.imm & (m.imm + 1)):
                return None          # not a low-bit mask
            return m.imm + 1
        return None

    def is_block_entry(self, fn, target):
        """A jump-table target is usable if it is an instruction in this
        function, a known entry point, or an instruction inside another
        function (which then gets its own alternate entry)."""
        return (target in fn.addrs or target in self.func_addrs
                or target in self.all_insn_addrs)

    def want_target(self, fn, ins, target, k, strict=None):
        """Validate a jump-table entry, deferring recovery on pass 1."""
        if target is not None and self.is_block_entry(fn, target):
            return
        if strict is False:
            if target is not None and self.image.is_exec(target):
                self.unlisted_targets.add(target)
            return
        # Ghidra's recorded function size is sometimes far short of the real
        # body, so bound recovery by a generous window past the entry rather
        # than by fn.end.
        if (not self.strict and target is not None
                and fn.addr <= target < fn.addr + 0x10000):
            self.unlisted_targets.add(target)
            return
        raise TranslateError(
            "jump table at %08x: entry %d -> %08x is not an instruction boundary"
            % (ins.addr, k, target or 0))

    def index_source(self, fn, i, idx_reg):
        """How the JMP's index register was produced.

        ("bounded", n)             `CMP idx,n-1` + `JA` guards it directly
        ("byte_table", (base, n))  idx was loaded from `[other + base]` as a
                                   byte, with `other` bounded to n values
        (None, None)               neither
        """
        for j in range(i - 1, max(-1, i - 16), -1):
            ins = fn.insns[j]
            if ins.mnem == "CMP" and len(ins.ops) == 2:
                b = self.cmp_bound_here(fn, j, idx_reg)
                return ("bounded", b) if b is not None else (None, None)
            if not self.writes_reg32(ins, idx_reg):
                continue
            if ins.mnem in ("MOV", "MOVZX", "MOVSX") and len(ins.ops) == 2:
                try:
                    dst, src = parse_operand(ins.ops[0]), parse_operand(ins.ops[1])
                except TranslateError:
                    return (None, None)
                if (dst.kind == "reg" and dst.reg == idx_reg and src.kind == "mem"
                        and src.size == 8 and src.index is None
                        and src.base is not None and src.disp):
                    inner = self.index_source(fn, j, src.base)
                    if inner[0] == "bounded":
                        return ("byte_table", (src.disp, inner[1]))
            return (None, None)
        return (None, None)

    #: How many index values reach the table, given `CMP idx,N` and the guard
    #: that follows it.  Only a guard that branches AWAY on out-of-range values
    #: bounds the fall-through path the JMP sits on: JA leaves 0..N (N+1
    #: values) and JAE/JNC leave 0..N-1 (N).  JBE/JB/JC/JNA branch away on
    #: in-range values, so reaching the JMP by falling through them means the
    #: index is out of range and the compare bounds nothing.
    GUARD_BOUND = {"JA": 1, "JNBE": 1, "JAE": 0, "JNC": 0, "JNB": 0}

    def cmp_bound_here(self, fn, j, idx_reg):
        """`CMP idx,N` guarded by an away-branching JA/JAE -> reachable count."""
        ins = fn.insns[j]
        try:
            a, b = parse_operand(ins.ops[0]), parse_operand(ins.ops[1])
        except TranslateError:
            return None
        if not (a.kind == "reg" and a.reg == idx_reg and b.kind == "imm"):
            return None
        if b.imm < 0 or b.imm > 0xFFFF:
            return None
        # The guard is the first later instruction that reads the flags, not
        # necessarily the next one: MSVC schedules unrelated moves in between.
        for k in range(j + 1, min(len(fn.insns), j + 10)):
            nxt = fn.insns[k]
            d, u = flag_effect(nxt)
            if u:
                delta = self.GUARD_BOUND.get(nxt.mnem)
                if delta is None:
                    return None
                # The guard has to leave the dispatch, not re-enter it: a
                # branch back into the compare-to-JMP range would mean the
                # out-of-range path runs the table lookup anyway.
                t = self.branch_target(nxt)
                if t is None or (ins.addr <= t <= fn.insns[self.jmp_index].addr):
                    return None
                n = b.imm + delta
                return n if n > 0 else None
            if d:
                return None
        return None

    @staticmethod
    def writes_reg32(ins, reg):
        if not ins.ops:
            return False
        if ins.mnem == "LOOP":
            return reg == 1                # ECX
        if ins.mnem in ("CMP", "TEST", "PUSH", "JMP") or ins.mnem in JCC:
            return False
        try:
            d = parse_operand(ins.ops[0])
        except TranslateError:
            return True
        return d.kind == "reg" and d.reg == reg

    # -- emission ----------------------------------------------------------

    def prepare(self, fn, strict=False):
        """Index the function and decode its jump tables (needed before both
        liveness and entry-point discovery).

        The first pass is lenient: a bounded table entry that is not a listed
        instruction goes into `unlisted_targets` for the driver to recover from
        the PE, because the Ghidra export drops instructions.  The second pass
        is strict and a target that is still not an instruction boundary is an
        error."""
        fn.index = {ins.addr: k for k, ins in enumerate(fn.insns)}
        fn.pushed_continuations = self.live_continuations(fn)
        fn.seh_sites = seh_frame_sites(fn, self.image)
        fn.seh_restores = seh_restore_sites(fn)
        self.strict = strict
        for i, ins in enumerate(fn.insns):
            if ins.mnem == "JMP" and ins.ops and not ins.ops[0].startswith("0x"):
                t = self.decode_jumptable(fn, i)
                if t:
                    self.jumptables[(fn.addr, ins.addr)] = t
        if self.seh_escaping_returns(fn):
            self.seh_helpers.add(fn.addr)
        else:
            self.seh_helpers.discard(fn.addr)

    def translate(self, fn, entries=()):
        """Emit fn_ADDR, plus one thin wrapper per alternate entry point.

        A function is entered anywhere its address appears in the block-entry
        table: its own start, a jump-table target inside it, or a jump from
        another function that Ghidra split at a different boundary.  With any
        of those, the body becomes `static void body_ADDR(X86 *, uint32_t)`
        preceded by a dispatch switch, and every entry is a three-line wrapper,
        so no code is duplicated."""
        entries = sorted(set(entries))
        # An alternate entry must be an instruction in THIS body.  A block can
        # be re-recovered between the discovery rounds and the final pass -
        # recursive descent makes them grow - so an entry registered against
        # an earlier, shorter version has to be dropped rather than emitted as
        # a goto to a label that was never placed.
        fn.index = {ins.addr: k for k, ins in enumerate(fn.insns)}
        fn.pushed_continuations = self.live_continuations(fn)
        dropped = [e for e in entries if e not in fn.index]
        if dropped:
            self.stale_entries.update(dropped)
            entries = [e for e in entries if e in fn.index]
        fn.return_jumps = self.popped_return_jumps(fn, entries)
        fn.seh_escapes = self.seh_escaping_returns(fn)
        if self.opts.eager_flags:
            live_out = [ALL_FLAGS] * len(fn.insns)
        else:
            live_out = self.liveness(fn)
        dead = self.dead_after_noreturn(fn, entries)
        fn.dead_addrs = {fn.insns[i].addr for i in dead}
        self.dead_after_noreturn_addrs.update(fn.dead_addrs)

        labels = set(fn.pushed_continuations)
        # RTL fill/move routines compute addresses within unrolled code rather
        # than loading a table. Only bodies with such jumps need every label.
        if any(ins.mnem == "JMP" and self.branch_target(ins) is None
               and not self.jumptables.get((fn.addr, ins.addr)) for ins in fn.insns):
            labels.update(fn.insns[i].addr for i in range(len(fn.insns)) if i not in dead)
        for i, ins in enumerate(fn.insns):
            if i in dead:
                continue
            t = self.push_ret_target(fn, i)
            if t is not None and t in fn.index:
                labels.add(t)
            if ins.mnem in JCC or ins.mnem == "JMP":
                t = self.branch_target(ins)
                if t is not None and t in fn.index:
                    labels.add(t)
            for t in self.jumptables.get((fn.addr, ins.addr), []):
                if t in fn.index:
                    labels.add(t)
            # A listing gap or an end-of-block fall-through emits a transfer
            # too, and its target needs a label just as much as a branch's.
            if ins.mnem not in TERMINATORS and not fn.contiguous[i]:
                t = fn.fallthrough[i]
                if t in fn.index:
                    labels.add(t)
        last = max(i for i in range(len(fn.insns)) if i not in dead)
        if fn.insns[last].mnem not in TERMINATORS:
            t = fn.fallthrough[last] or fn.end
            if t in fn.index:
                labels.add(t)

        for e in entries:
            labels.add(e)

        out = []
        if fn.addr in INTRINSIC_BODY:
            self.stats["_intrinsic_body"] += 1
            return ["/* runtime intrinsic */",
                    "void fn_%08x(X86 *c) { %s }" % (fn.addr, INTRINSIC_BODY[fn.addr])]
        # Following branches can pull in addresses BELOW the entry, so the
        # first instruction in address order is not necessarily where this
        # function starts.  Jump to the entry explicitly rather than falling
        # into whatever sorts first: fn_00565e1e began at 00565dd0, a POP EAX,
        # which ate the caller's return address.
        head = fn.insns[0].addr != fn.addr
        if head:
            labels.add(fn.addr)
        prologue = 0                 # lines before the first instruction
        if entries:
            out.append("static void body_%08x(X86 *c, uint32_t entry_) {" % fn.addr)
            if fn.seh_escapes:
                out.append("    uint64_t seh_mark_ = recomp_seh_frame_mark(c);")
            out.append("    switch (entry_) {")
            for e in entries:
                out.append("    case %s: goto L_%08x;" % (hexlit(e), e))
            out.append("    default: goto L_%08x;" % fn.addr if head else
                       "    default: break;")
            out.append("    }")
        else:
            out.append("void fn_%08x(X86 *c) {" % fn.addr)
            if fn.seh_escapes:
                out.append("    uint64_t seh_mark_ = recomp_seh_frame_mark(c);")
            if head:
                out.append("    goto L_%08x;" % fn.addr)
        prologue = len(out)          # everything emitted so far is dispatch
        for i, ins in enumerate(fn.insns):
            if i in dead:
                continue
            if ins.addr in labels:
                out.append("L_%08x: ;" % ins.addr)
            body = self.emit(fn, i, live_out[i])
            for line in body:
                out.append("    " + line)
            # An INT3 the listing ran into is MSVC's padding between
            # functions: the byte after it belongs to whatever comes next, so
            # this body ends here rather than falling through into it.
            if (not fn.contiguous[i] and ins.mnem not in TERMINATORS
                    and ins.mnem != "INT3" and not self.never_returns(ins)):
                t = fn.fallthrough[i]
                self.stats["_listing_gap"] += 1
                self.notes.append(
                    "%08x: listing gap, %s falls through to %08x" % (ins.addr, ins.mnem, t))
                for line in self.goto_target(fn, t, ins):
                    out.append("    " + line)
        # A function whose last listed instruction is not a terminator falls
        # through into the next function.
        last_i = max(i for i in range(len(fn.insns)) if i not in dead)
        last = fn.insns[last_i]
        if (last.mnem not in TERMINATORS and last.mnem != "INT3"
                and not self.never_returns(last)):
            t = fn.fallthrough[last_i] or fn.end
            self.stats["_fallthrough_exit"] += 1
            out.append("    " + " ".join(self.goto_target(fn, t, last)))
        # Invariant: the first thing fn_ADDR does is reach ADDR.  Checked
        # here rather than trusted, because getting it wrong corrupts the
        # guest stack silently and only shows up several calls away.  The
        # whole dispatch prologue is searched - `out[:prologue]` - not a fixed
        # number of lines: with four alternate entries the default arm sits
        # past any such window, and slicing rejected correct output.
        if fn.addr not in fn.index:
            raise TranslateError("fn_%08x does not contain its own entry" % fn.addr)
        if head:
            want = ("goto L_%08x;" % fn.addr, "default: goto L_%08x;" % fn.addr)
            if not any(line.strip() in want for line in out[:prologue]):
                raise TranslateError(
                    "fn_%08x starts at %08x, not at its own address; prologue "
                    "was %s" % (fn.addr, fn.insns[0].addr,
                                " | ".join(l.strip() for l in out[:prologue])))
        out.append("}")
        if entries:
            out.append("void fn_%08x(X86 *c) { body_%08x(c, 0u); }" % (fn.addr, fn.addr))
            for e in entries:
                out.append("void fn_%08x(X86 *c) { body_%08x(c, %s); }"
                           % (e, fn.addr, hexlit(e)))
        return out

    # ---- helpers used by emit -------------------------------------------

    def flags_arith(self, kind, size, live, a="a_", b="b_", r="r_", rf="rf_"):
        """Flag stores for ADD/ADC/SUB/SBB/CMP/INC/DEC."""
        bits = size
        lines = []
        if "cf" in live and kind in ("add", "sub"):
            lines.append("c->eflags_cf = (uint32_t)((%s >> %d) & 1u);" % (rf, bits))
        if "of" in live:
            if kind == "add":
                lines.append("c->eflags_of = (uint32_t)((~(%s ^ %s) & (%s ^ %s)) >> %d) & 1u;"
                             % (a, b, a, r, bits - 1))
            else:
                lines.append("c->eflags_of = (uint32_t)(((%s ^ %s) & (%s ^ %s)) >> %d) & 1u;"
                             % (a, b, a, r, bits - 1))
        if "af" in live:
            lines.append("c->eflags_af = ((%s ^ %s ^ %s) >> 4) & 1u;" % (a, b, r))
        if "zf" in live:
            lines.append("c->eflags_zf = (%s == 0);" % r)
        if "sf" in live:
            lines.append("c->eflags_sf = (%s >> %d) & 1u;" % (r, bits - 1))
        if "pf" in live:
            lines.append("c->eflags_pf = parity8(%s);" % r)
        return lines

    def flags_logic(self, size, live, r="r_"):
        lines = []
        if "cf" in live:
            lines.append("c->eflags_cf = 0;")
        if "of" in live:
            lines.append("c->eflags_of = 0;")
        if "zf" in live:
            lines.append("c->eflags_zf = (%s == 0);" % r)
        if "sf" in live:
            lines.append("c->eflags_sf = (%s >> %d) & 1u;" % (r, size - 1))
        if "pf" in live:
            lines.append("c->eflags_pf = parity8(%s);" % r)
        return lines

    def rmw(self, op, size, lines):
        """For a memory destination, hoist the address into ad_ and return a
        substitute operand that reuses it."""
        if op.kind != "mem":
            return op
        lines.append("uint32_t ad_ = %s;" % addr_expr(op))
        return Op("mem", size=op.size, addr_c="ad_")

    # ---- the instruction dispatcher --------------------------------------

    def emit(self, fn, i, live):
        # Instructions replaced by a trap, reported at the end of a run so a
        # listing that is decoding data as code is visible rather than silent.

        ins = fn.insns[i]
        m = ins.mnem
        # A recovered block can end on a CALL, with its last-byte estimate
        # four bytes short of the real rel32 continuation. Listing gaps can
        # also skip past it. The return address belongs to the instruction.
        nxt = fn.fallthrough[i] or (fn.insns[i + 1].addr if i + 1 < len(fn.insns) else fn.end)
        try:
            body = self._emit(fn, i, ins, m, nxt, live)
            body = visual_animation_read(ins.addr, body)
        except TranslateError as e:
            # An instruction this translator cannot model becomes a trap at its
            # own address rather than the end of the build. A listing routinely
            # decodes the data past a function's real last instruction as code
            # - sixteen-bit addressing and port instructions in a thirty-two-bit
            # user-mode image are the signature - and refusing the image over
            # bytes nothing executes helps nobody. Reaching one is still fatal,
            # loudly and with its address, which is the property that matters.
            if not self.allow_unmodelled:
                raise TranslateError("%08x %s: %s" % (ins.addr, ins.raw.split("  ", 1)[1], e))
            self.unmodelled.append((ins.addr, "%s: %s" % (ins.raw.split("  ", 1)[1], e)))
            body = ["recomp_unmodelled(c, 0x%08xu);" % ins.addr]
        self.stats[m] += 1
        declares = any(body[0].startswith(t) for t in
                       ("uint8_t ", "uint16_t ", "uint32_t ", "uint64_t ", "double "))
        if len(body) == 1 and not declares:
            return ["%s  /* %08x %s */" % (body[0], ins.addr, ins.raw.split("  ", 1)[1])]
        return (["{   /* %08x %s */" % (ins.addr, ins.raw.split("  ", 1)[1])]
                + ["    " + b for b in body] + ["}"])

    STRING_MNEM = frozenset(("STOSB", "STOSW", "STOSD", "MOVSB", "MOVSW", "MOVSD",
                             "LODSB", "LODSW", "LODSD", "SCASB", "SCASW", "SCASD",
                             "CMPSB", "CMPSW", "CMPSD",
                             # The port forms. A user-mode guest never reaches
                             # one; they turn up where a listing misdecodes
                             # data as code, and refusing them would fail a
                             # whole build over a byte nothing executes.
                             "INSB", "INSW", "INSD", "OUTSB", "OUTSW", "OUTSD"))

    def _emit(self, fn, i, ins, m, nxt, live):
        if any(AVX_OPERAND_RE.search(o) for o in ins.ops or ()):
            # Never modelled, and its operands do not parse: trap it here,
            # before parsing refuses the whole function over a path CPUID
            # keeps the guest from taking.
            return ["recomp_unmodelled(c, %s); return;" % hexlit(ins.addr)]
        if m in self.STRING_MNEM and not names_an_xmm(ins):
            # Ghidra prints the implicit ES:EDI / ESI operands; they carry no
            # information the mnemonic does not already imply.
            return self.emit_string(ins, m)
        ops = [parse_operand(o) for o in ins.ops] if ins.ops else []
        L = []

        # ---------------------------------------------------------- data --
        if m == "MOV":
            size = operand_size(ops)
            dst, src = ops
            if dst.kind == "sreg":
                # A segment load means nothing in the flat model the runtime
                # provides; a load of CS is an invalid opcode on the CPU too.
                # These come from data Ghidra decoded as code.
                return ["recomp_int(c, 6u);"] if dst.imm == SEGMENT_SELECTOR["CS"] else [";"]
            L.append(write_op(dst, size, read_op(src, size)))
            if (fn.seh_sites and (seh_chain_operand(dst) or i in fn.seh_sites)
                    and src.kind == "reg" and src.size == 32):
                L.append("c->eip = %s;" % hexlit(ins.addr))
                if i in fn.seh_sites:
                    L.append("{ jmp_buf *b_ = recomp_seh_frame_enter(c); "
                             "if (setjmp(*b_)) { recomp_seh_land(c); return; } }")
                else:
                    L.append("recomp_seh_frame_leave(c);")
            return L

        if m == "LEA":
            dst, src = ops
            if src.kind != "mem":
                raise TranslateError("LEA without memory operand")
            L.append(write_op(dst, 32, addr_expr(src)))
            return L

        if m in ("MOVSX", "MOVZX"):
            dst, src = ops
            ssize = src.size if src.kind == "mem" and src.size else (
                src.size if src.kind == "reg" else None)
            if ssize is None:
                raise TranslateError("%s: unknown source size" % m)
            if m == "MOVSX":
                val = "(uint32_t)(int32_t)(%s)(%s)" % (stype(ssize), read_op(src, ssize))
            else:
                val = "(uint32_t)(%s)" % read_op(src, ssize)
            L.append(write_op(dst, dst.size, val))
            return L

        if m == "XCHG":
            size = operand_size(ops)
            a, b = ops
            a = self.rmw(a, size, L)
            L.append("uint32_t t_ = %s;" % read_op(a, size))
            L.append(write_op(a, size, read_op(b, size)))
            L.append(write_op(b, size, "t_"))
            return L

        if m == "CMPXCHG":
            # The guest is single-threaded, so LOCK needs no host atomic.
            # Flags come from accumulator - destination on either path.
            size = operand_size(ops)
            dst, src = ops
            dst = self.rmw(dst, size, L)
            L.append("uint32_t a_ = c->r[0] & %s, b_ = %s;"
                     % (hexlit(mask_of(size)), read_op(dst, size)))
            L.append("uint64_t rf_ = (uint64_t)a_ - b_;")
            L.append("uint32_t r_ = (uint32_t)rf_ & %s;" % hexlit(mask_of(size)))
            L.append("if (a_ == b_) { %s }" % write_op(dst, size, read_op(src, size)))
            L.append("else { %s }" % write_op(Op("reg", reg=0, size=size, part=None), size, "b_"))
            L.extend(self.flags_arith("sub", size, live))
            return L

        if m == "CMPXCHG8B":
            # LOCK needs no host atomic under the cooperative guest scheduler.
            # Capture the guest address before the failure path changes EDX:EAX.
            L.append("uint32_t ad_ = %s;" % addr_expr(ops[0]))
            L.append("uint64_t dst_ = rd64(ad_);")
            L.append("uint64_t expected_ = ((uint64_t)c->r[2] << 32) | c->r[0];")
            L.append("c->eflags_zf = (expected_ == dst_);")
            L.append("if (c->eflags_zf) { wr64(ad_, ((uint64_t)c->r[1] << 32) | c->r[3]); }")
            L.append("else { c->r[0] = (uint32_t)dst_; c->r[2] = (uint32_t)(dst_ >> 32); }")
            return L

        if m == "XADD":
            size = operand_size(ops)
            dst, src = ops
            dst = self.rmw(dst, size, L)
            L.append("uint32_t a_ = %s, b_ = %s;" % (read_op(dst, size), read_op(src, size)))
            L.append("uint64_t rf_ = (uint64_t)a_ + b_;")
            L.append("uint32_t r_ = (uint32_t)rf_ & %s;" % hexlit(mask_of(size)))
            L.append(write_op(src, size, "a_"))
            L.append(write_op(dst, size, "r_"))
            L.extend(self.flags_arith("add", size, live))
            return L

        if m == "PUSH":
            size = operand_size(ops, hint=32)
            if size == 16:
                L.append("uint32_t v_ = %s;" % read_op(ops[0], 16))
                L.append("c->r[4] -= 2; wr16(c->r[4], (uint16_t)v_);")
                return L
            if size != 32:
                raise TranslateError("non-32-bit PUSH")
            L.append("uint32_t v_ = %s;" % read_op(ops[0], 32))
            L.append("c->r[4] -= 4; wr32(c->r[4], v_);")
            t = self.push_ret_target(fn, i)
            if t is not None and t not in fn.pushed_continuations:
                # Preserve the guest stack write even though the pair has no
                # net stack effect, then take the RET's guest continuation.
                L.append("c->eip = v_; c->r[4] += 4;")
                L.extend(self.goto_target(fn, t, ins))
            return L

        if m == "POP":
            size = operand_size(ops, hint=32)
            if size == 16:
                L.append("uint32_t v_ = rd16(c->r[4]); c->r[4] += 2;")
                L.append(write_op(ops[0], 16, "v_"))
                return L
            if size != 32:
                raise TranslateError("non-32-bit POP")
            L.append("uint32_t v_ = rd32(c->r[4]); c->r[4] += 4;")
            L.append(write_op(ops[0], 32, "v_"))
            if i in fn.seh_restores:
                L.append("c->eip = %s; recomp_seh_frame_leave(c);" % hexlit(ins.addr))
            return L

        if m == "PUSHAD":
            return ["x86_pushad(c);"]
        if m == "POPAD":
            return ["x86_popad(c);"]
        if m == "PUSHFD":
            return ["c->r[4] -= 4; wr32(c->r[4], x86_get_eflags(c));"]
        if m == "POPFD":
            return ["x86_set_eflags(c, rd32(c->r[4])); c->r[4] += 4;"]
        if m == "PUSHF":
            return ["c->r[4] -= 2; wr16(c->r[4], (uint16_t)x86_get_eflags(c));"]
        if m == "POPF":
            return ["x86_set_eflags(c, (x86_get_eflags(c) & 0xffff0000u) | rd16(c->r[4]));",
                    "c->r[4] += 2;"]
        if m == "LEAVE":
            return ["c->r[4] = c->r[5]; c->r[5] = rd32(c->r[4]); c->r[4] += 4;"]
        if m == "SAHF":
            return ["x86_sahf(c);"]
        if m == "LAHF":
            # AH = SF:ZF:0:AF:0:PF:1:CF
            return ["c->r[0] = (c->r[0] & 0xffff00ffu) | "
                    "(((x86_get_eflags(c) & 0xd5u) | 0x02u) << 8);"]
        if m == "XLAT":
            return ["xlat(c);"]
        if m == "BSWAP":
            return [write_op(ops[0], 32, "bswap32(%s)" % read_op(ops[0], 32))]

        if m == "CDQ":
            return ["c->r[2] = (uint32_t)((int32_t)c->r[0] >> 31);"]
        if m == "CWD":
            return ["c->r[2] = (c->r[2] & 0xffff0000u) | "
                    "((uint32_t)((int16_t)c->r[0] >> 15) & 0xffffu);"]
        if m == "CBW":
            return ["c->r[0] = (c->r[0] & 0xffff0000u) | "
                    "((uint32_t)(int32_t)(int8_t)c->r[0] & 0xffffu);"]
        if m == "CWDE":
            return ["c->r[0] = (uint32_t)(int32_t)(int16_t)c->r[0];"]
        if m in ("LDMXCSR", "STMXCSR"):
            # The SSE control word. This kit models one rounding mode and
            # masks every exception, which is what a guest sets it to; a store
            # hands back exactly that.
            if m == "STMXCSR":
                return [write_op(ops[0], 32, "0x1f80u")]
            return [";"]

        # ------------------------------------------------------- arith ----
        if m in ("ADD", "ADC", "SUB", "SBB", "CMP"):
            size = operand_size(ops)
            dst, src = ops
            dst = self.rmw(dst, size, L)
            L.append("uint32_t a_ = %s, b_ = %s;" % (read_op(dst, size), read_op(src, size)))
            if m in ("ADD", "ADC"):
                carry = " + c->eflags_cf" if m == "ADC" else ""
                L.append("uint64_t rf_ = (uint64_t)a_ + b_%s;" % carry)
                kind = "add"
            else:
                carry = " - c->eflags_cf" if m == "SBB" else ""
                L.append("uint64_t rf_ = (uint64_t)a_ - b_%s;" % carry)
                kind = "sub"
            L.append("uint32_t r_ = (uint32_t)rf_ & %s;" % hexlit(mask_of(size)))
            if m != "CMP":
                L.append(write_op(dst, size, "r_"))
            L += self.flags_arith(kind, size, live)
            return L

        if m in ("INC", "DEC"):
            size = operand_size(ops)
            dst = self.rmw(ops[0], size, L)
            L.append("uint32_t a_ = %s, b_ = 1u;" % read_op(dst, size))
            L.append("uint64_t rf_ = (uint64_t)a_ %s b_;" % ("+" if m == "INC" else "-"))
            L.append("uint32_t r_ = (uint32_t)rf_ & %s;" % hexlit(mask_of(size)))
            L.append(write_op(dst, size, "r_"))
            L += self.flags_arith("add" if m == "INC" else "sub", size, live - {"cf"})
            return L

        if m == "NEG":
            size = operand_size(ops)
            dst = self.rmw(ops[0], size, L)
            L.append("uint32_t b_ = %s, a_ = 0u;" % read_op(dst, size))
            L.append("uint64_t rf_ = (uint64_t)a_ - b_;")
            L.append("uint32_t r_ = (uint32_t)rf_ & %s;" % hexlit(mask_of(size)))
            L.append(write_op(dst, size, "r_"))
            L += self.flags_arith("sub", size, live)
            return L

        if m == "NOT":
            size = operand_size(ops)
            dst = self.rmw(ops[0], size, L)
            L.append(write_op(dst, size, "~%s" % read_op(dst, size)))
            return L

        if m in ("AND", "OR", "XOR", "TEST"):
            size = operand_size(ops)
            dst, src = ops
            cop = {"AND": "&", "OR": "|", "XOR": "^", "TEST": "&"}[m]
            dst2 = self.rmw(dst, size, L) if m != "TEST" else dst
            L.append("uint32_t r_ = (%s %s %s) & %s;"
                     % (read_op(dst2, size), cop, read_op(src, size), hexlit(mask_of(size))))
            if m != "TEST":
                L.append(write_op(dst2, size, "r_"))
            L += self.flags_logic(size, live)
            return L

        # ------------------------------------------------- shift / rotate --
        if m in ("SHL", "SHR", "SAR", "ROL", "ROR", "RCL", "RCR"):
            size = ops[0].size or operand_size(ops, hint=32)
            dst = self.rmw(ops[0], size, L)
            cnt = read_op(ops[1], 32) if len(ops) > 1 else "1u"
            if len(ops) > 1 and ops[1].kind == "reg":
                cnt = "(uint32_t)%s" % read_op(ops[1], ops[1].size)
            fn_base = m.lower() + str(size)
            wants_flags = bool(live & SHIFT_MAYDEF[m]) or m in ("RCL", "RCR")
            call = "%s%s(%s%s, %s)" % (fn_base, "_f" if wants_flags else "",
                                       "c, " if wants_flags else "",
                                       read_op(dst, size), cnt)
            L.append(write_op(dst, size, call))
            return L

        if m in ("SHLD", "SHRD"):
            size = operand_size(ops[:2])
            if size != 32:
                raise TranslateError("%s at width %d" % (m, size))
            dst = self.rmw(ops[0], 32, L)
            cnt = ("(uint32_t)%s" % read_op(ops[2], ops[2].size)
                   if ops[2].kind == "reg" else read_op(ops[2], 32))
            L.append(write_op(dst, 32, "%s32_f(c, %s, %s, %s)"
                              % (m.lower(), read_op(dst, 32), read_op(ops[1], 32), cnt)))
            return L

        # -------------------------------------------------- mul  /  div ---
        if m == "IMUL" and len(ops) >= 2:
            size = operand_size(ops[:2])
            if len(ops) == 2:
                a, b = read_op(ops[0], size), read_op(ops[1], size)
            else:
                a, b = read_op(ops[1], size), read_op(ops[2], size)
            wants = bool(live & frozenset(("cf", "of")))
            helper = "imul2_%d%s" % (size, "_f" if wants else "")
            args = ("c, " if wants else "") + "%s, %s" % (a, b)
            L.append(write_op(ops[0], size, "%s(%s)" % (helper, args)))
            return L

        if m in ("MUL", "IMUL", "DIV", "IDIV"):
            size = operand_size(ops)
            src = read_op(ops[0], size)
            base = m.lower() + str(size)
            if m in ("DIV", "IDIV"):
                L.append("%s(c, (%s)(%s), %s);" % (base, utype(size), src, hexlit(ins.addr)))
            else:
                L.append("%s(c, (%s)(%s));" % (base, utype(size), src))
            return L

        # ------------------------------------------------------- bit ops --
        if m in ("BT", "BTS", "BTR", "BTC"):
            size = operand_size(ops, hint=32)
            dst, src = ops
            idx = read_op(src, size)
            if dst.kind == "mem":
                L.append("uint32_t bi_ = %s;" % idx)
                L.append("uint32_t ad_ = %s + 4u * (bi_ >> 5);" % addr_expr(dst))
                dst = Op("mem", size=32, addr_c="ad_")
                bit = "(bi_ & 31u)"
            else:
                L.append("uint32_t bi_ = (%s) & %du;" % (idx, size - 1))
                bit = "bi_"
            L.append("uint32_t v_ = %s;" % read_op(dst, size))
            if "cf" in live or m != "BT":
                L.append("c->eflags_cf = (v_ >> %s) & 1u;" % bit)
            if m == "BTS":
                L.append(write_op(dst, size, "v_ | (1u << %s)" % bit))
            elif m == "BTR":
                L.append(write_op(dst, size, "v_ & ~(1u << %s)" % bit))
            elif m == "BTC":
                L.append(write_op(dst, size, "v_ ^ (1u << %s)" % bit))
            return L

        if m in ("BSR", "BSF"):
            # With a zero source the destination is architecturally undefined;
            # unicorn (and AMD) leave it unchanged, so pass the old value in.
            L.append(write_op(ops[0], 32, "%s32_f(c, %s, %s)"
                              % (m.lower(), read_op(ops[0], 32), read_op(ops[1], 32))))
            return L

        if m in SETCC:
            cond = SETCC[m][0]
            L.append(write_op(ops[0], 8, "(%s) ? 1u : 0u" % cond))
            return L

        if m in CMOVCC:
            size = operand_size(ops)
            cond = CMOVCC[m][0]
            L.append("if (%s) { %s }" % (cond, write_op(ops[0], size,
                                                        read_op(ops[1], size))))
            return L

        if m == "CLD":
            return ["c->eflags_df = 0;"]
        if m == "STD":
            return ["c->eflags_df = 1;"]
        if m == "CLC":
            return ["c->eflags_cf = 0;"]
        if m == "STC":
            return ["c->eflags_cf = 1;"]
        if m == "CMC":
            return ["c->eflags_cf = !c->eflags_cf;"]

        # ---------------------------------------------------- control flow --
        if m in JCC:
            cond = JCC[m][0]
            t = self.branch_target(ins)
            if t is None:
                raise TranslateError("indirect conditional jump")
            body = self.goto_target(fn, t, ins)
            if POLL_CHECKPOINTS.get(ins.addr) == t:
                POLL_CHECKPOINTS_APPLIED.add(ins.addr)
                body = ["recomp_poll_checkpoint(c);"] + body
            return ["if (%s) { %s }" % (cond, " ".join(body))]

        if m == "JMP":
            t = self.branch_target(ins)
            if t is not None:
                if POLL_CHECKPOINTS.get(ins.addr) == t:
                    POLL_CHECKPOINTS_APPLIED.add(ins.addr)
                    return ["recomp_poll_checkpoint(c);"] + self.goto_target(fn, t, ins)
                return self.goto_target(fn, t, ins)
            return self.emit_indirect_jump(fn, i, ins, ops[0])

        if m == "CALL":
            if ins.ops and ins.ops[0].startswith("0x"):
                t = int(ins.ops[0], 16)
                L.append("c->r[4] -= 4; wr32(c->r[4], %s);" % hexlit(nxt))
                if t == INTRINSIC_SETJMP:
                    # The host jmp_buf has to belong to a frame that is still
                    # live when _longjmp fires, so setjmp is taken here rather
                    # than inside the runtime (runtime/intrinsics.h).
                    self.stats["_intrinsic_setjmp"] += 1
                    # C11 7.13.2.1 allows setjmp only as a whole controlling
                    # expression, or compared against an integer constant in
                    # one; inside another call's argument list it is undefined.
                    L.append("jmp_buf *b_ = recomp_setjmp_prepare(c);")
                    L.append("if (setjmp(*b_) == 0) recomp_setjmp_return(c, 0);")
                    L.append("else recomp_setjmp_return(c, 1);")
                elif t in self.func_addrs:
                    L.append("CALL_FN(%08x);" % t)
                else:
                    self.reject_offimage_call(t)
                    self.stats["_call_unknown"] += 1
                    L.append("recomp_call(c, %s);" % hexlit(t))
                if RESUMABLE_STACKS:
                    L.append("if (c->eip != %s) return;" % hexlit(nxt))
                if t in self.seh_helpers:
                    L.append("c->eip = %s;" % hexlit(ins.addr))
                    L.append("{ jmp_buf *b_ = recomp_seh_frame_adopt(c); "
                             "if (b_) { if (setjmp(*b_)) { recomp_seh_land(c); return; } } }")
                if t in self.noreturn_callees:
                    # The callee throws or exits; what follows is padding and
                    # tables, never code.  Reaching this line means it came
                    # back after all, which the runtime then reports by address.
                    self.stats["_noreturn_trap"] += 1
                    L.append("recomp_unknown_call(c, %s); return;" % hexlit(nxt))
                return L
            L.append("uint32_t t_ = %s;" % read_op(ops[0], 32))
            L.append("c->r[4] -= 4; wr32(c->r[4], %s);" % hexlit(nxt))
            L.append("recomp_call(c, t_);")
            if RESUMABLE_STACKS:
                L.append("if (c->eip != %s) return;" % hexlit(nxt))
            self.stats["_call_indirect"] += 1
            return L

        if m == "RET":
            n = parse_imm(ins.ops[0]) if ins.ops else 0
            orphan = "recomp_seh_frame_orphan(c, seh_mark_); " if i in fn.seh_escapes else ""
            if fn.pushed_continuations:
                # Normal finally cleanup stays in the establishing C frame.
                # An alternate entry called by the exception dispatcher has
                # its own return address and takes the default arm instead.
                L = ["uint32_t r_ = rd32(c->r[4]); c->r[4] += %du;" % (4 + n),
                     "switch (r_) {"]
                L.extend("case %s: goto L_%08x;" % (hexlit(t), t)
                         for t in sorted(fn.pushed_continuations))
                L.extend(["default: c->eip = r_; " + orphan + "recomp_return(c); return;", "}"])
                return L
            return ["c->eip = rd32(c->r[4]); c->r[4] += %du; " % (4 + n) +
                    orphan + "recomp_return(c); return;"]

        # ------------------------------------------------------------ SSE --
        # Data movement only, in dword lanes. A Delphi runtime's FillChar and
        # Move reach for these unconditionally - SSE2 predates every CPU the
        # compiler supports, so there is no feature test to fail - while the
        # AVX forms beside them are gated on a CPUID bit this kit does not
        # set, and stay traps nobody reaches.
        # MMX first: is_mmx_insn only matches an MMn operand with no XMM one,
        # so the SSE2 path below still takes every xmm form of MOVQ/MOVD.
        if is_mmx_insn(m, ins.ops):
            return self.emit_mmx(ins, m)
        # Lane-wise integer, logical and unpack forms. Every one of them has
        # the same shape: both operands are read in full before either lane of
        # the destination is written, because a destination is commonly also
        # the source and these are not sequences of independent moves.
        SSE_LANE_OPS = {"PXOR": "^", "XORPD": "^", "XORPS": "^", "PAND": "&", "ANDPD": "&",
                        "ANDPS": "&", "POR": "|", "ORPD": "|", "ORPS": "|"}
        SSE_LANE_FORMS = ("PANDN", "ANDNPD", "ANDNPS", "PCMPEQD", "PUNPCKLDQ", "PUNPCKHDQ",
                          "PUNPCKLQDQ", "UNPCKLPD", "UNPCKHPD", "MOVDDUP", "MOVLPD", "MOVLPS",
                          "MOVHPD", "MOVHPS", "SHUFPS", "SHUFPD", "MOVAPD", "MOVUPD")
        if m in SSE_LANE_OPS or m in SSE_LANE_FORMS:
            dst, src = ops[0], ops[1]

            def lane(op, i):
                if op.kind == "xmm":
                    return "c->xmm[%d][%d]" % (op.reg, i)
                return "rd32(%s + %du)" % (addr_expr(op), 4 * i)

            def put(op, i, value):
                if op.kind == "xmm":
                    return "c->xmm[%d][%d] = %s;" % (op.reg, i, value)
                return "wr32(%s + %du, %s);" % (addr_expr(op), 4 * i, value)

            # d0_..d3_ and s0_..s3_ hold the operands as they were on entry.
            # A half-width form reads only the half it uses.
            halves = 2 if m in ("MOVLPD", "MOVLPS", "MOVHPD", "MOVHPS") else 4
            L.extend("uint32_t d%d_ = %s;" % (i, lane(dst, i)) for i in range(halves))
            L.extend("uint32_t s%d_ = %s;" % (i, lane(src, i)) for i in range(halves))
            d = lambda i: "d%d_" % i
            sv = lambda i: "s%d_" % i

            if m in SSE_LANE_OPS:
                L.extend(put(dst, i, "%s %s %s" % (d(i), SSE_LANE_OPS[m], sv(i)))
                         for i in range(4))
            elif m in ("PANDN", "ANDNPD", "ANDNPS"):
                L.extend(put(dst, i, "(~%s) & %s" % (d(i), sv(i))) for i in range(4))
            elif m == "PCMPEQD":
                L.extend(put(dst, i, "%s == %s ? 0xffffffffu : 0u" % (d(i), sv(i)))
                         for i in range(4))
            elif m in ("MOVAPD", "MOVUPD"):   # MOVAPS and MOVUPS under another name
                L.extend(put(dst, i, sv(i)) for i in range(4))
            elif m == "PUNPCKLDQ":            # d0 s0 d1 s1
                L.extend([put(dst, 0, d(0)), put(dst, 1, sv(0)),
                          put(dst, 2, d(1)), put(dst, 3, sv(1))])
            elif m == "PUNPCKHDQ":            # d2 s2 d3 s3
                L.extend([put(dst, 0, d(2)), put(dst, 1, sv(2)),
                          put(dst, 2, d(3)), put(dst, 3, sv(3))])
            elif m in ("PUNPCKLQDQ", "UNPCKLPD"):   # d0 d1 s0 s1
                L.extend([put(dst, 2, sv(0)), put(dst, 3, sv(1))])
            elif m == "UNPCKHPD":                   # d2 d3 s2 s3
                L.extend([put(dst, 0, d(2)), put(dst, 1, d(3)),
                          put(dst, 2, sv(2)), put(dst, 3, sv(3))])
            elif m == "MOVDDUP":                    # s0 s1 s0 s1
                L.extend([put(dst, 0, sv(0)), put(dst, 1, sv(1)),
                          put(dst, 2, sv(0)), put(dst, 3, sv(1))])
            elif m in ("MOVLPD", "MOVLPS", "MOVHPD", "MOVHPS"):
                # One 64-bit half moves; the other half keeps what it had.
                half = 0 if m in ("MOVLPD", "MOVLPS") else 2
                if dst.kind == "xmm":
                    L.extend(put(dst, half + i, sv(i)) for i in range(2))
                else:
                    L.extend(put(dst, i, "c->xmm[%d][%d]" % (src.reg, half + i))
                             for i in range(2))
            elif m == "SHUFPS":
                # Lanes 0 and 1 come from the destination, 2 and 3 from the source.
                sel = parse_imm(ins.ops[2])
                L.extend([put(dst, 0, d(sel & 3)), put(dst, 1, d((sel >> 2) & 3)),
                          put(dst, 2, sv((sel >> 4) & 3)), put(dst, 3, sv((sel >> 6) & 3))])
            else:  # SHUFPD: one double from each operand
                sel = parse_imm(ins.ops[2])
                lo, hi = 2 * (sel & 1), 2 * ((sel >> 1) & 1)
                L.extend([put(dst, 0, d(lo)), put(dst, 1, d(lo + 1)),
                          put(dst, 2, sv(hi)), put(dst, 3, sv(hi + 1))])
            return L

        # Scalar double and single forms. The host's double and float are the
        # same IEEE formats in the same rounding mode, and scalar SSE leaves
        # the lanes above its result alone, which is why nothing here clears
        # them except a load from memory, where the hardware does.
        SSE_SCALAR_MATH = {"ADDSD": "+", "SUBSD": "-", "MULSD": "*", "DIVSD": "/",
                           "ADDSS": "+", "SUBSS": "-", "MULSS": "*", "DIVSS": "/"}
        SSE_SCALAR_FORMS = ("MOVSD", "MOVSS", "SQRTSD", "SQRTSS", "MINSD", "MAXSD",
                            "MINSS", "MAXSS", "CVTSI2SD", "CVTSI2SS", "CVTTSD2SI",
                            "CVTTSS2SI", "CVTSD2SS", "CVTSS2SD", "COMISD", "UCOMISD",
                            "COMISS", "UCOMISS")
        # Only MOVSD and MOVSS are shared with another instruction; the rest
        # of these mnemonics are SSE whatever their operands are, and a
        # conversion's source or destination is often memory or a register.
        if (m in SSE_SCALAR_MATH
                or (m in SSE_SCALAR_FORMS
                    and (m not in ("MOVSD", "MOVSS") or names_an_xmm(ins)))):
            dst, src = ops[0], ops[1]
            # Which host type this form works in. The conversions name both,
            # so they spell their own operands out instead.
            single = m.endswith("SS") and m not in ("CVTSD2SS",)

            def scalar(op, as_single):
                if op.kind == "xmm":
                    return "xmm_f32(c, %d)" % op.reg if as_single else "xmm_f64(c, %d)" % op.reg
                return "rdf32(%s)" % addr_expr(op) if as_single else "rdf64(%s)" % addr_expr(op)

            def store_scalar(op, value, as_single):
                if op.kind == "xmm":
                    return ("xmm_set_f32(c, %d, %s);" if as_single else "xmm_set_f64(c, %d, %s);") \
                        % (op.reg, value)
                return ("wrf32(%s, %s);" if as_single else "wrf64(%s, %s);") \
                    % (addr_expr(op), value)

            if m in SSE_SCALAR_MATH:
                L.append(store_scalar(dst, "%s %s %s" % (scalar(dst, single), SSE_SCALAR_MATH[m],
                                                         scalar(src, single)), single))
            elif m in ("SQRTSD", "SQRTSS"):
                L.append(store_scalar(dst, "%s(%s)" % ("recomp_sse_sqrtf" if single
                                                       else "recomp_sse_sqrt",
                                                       scalar(src, single)), single))
            elif m in ("MINSD", "MAXSD", "MINSS", "MAXSS"):
                # The hardware returns its second operand when either is a NaN
                # or both are zero, which is what this spelling does.
                t = "float" if single else "double"
                keep = "<" if m in ("MINSD", "MINSS") else ">"
                L.append("{ %s a_ = %s, b_ = %s; %s }"
                         % (t, scalar(dst, single), scalar(src, single),
                            store_scalar(dst, "b_ %s a_ ? a_ : b_" % keep, single)))
            elif m in ("COMISD", "UCOMISD", "COMISS", "UCOMISS"):
                L.append("recomp_comis(c, %s, %s);" % (scalar(dst, single), scalar(src, single)))
            elif m in ("CVTSI2SD", "CVTSI2SS"):
                L.append(store_scalar(dst, "(%s)(int32_t)%s" % ("float" if single else "double",
                                                                read_op(src, 32)), single))
            elif m in ("CVTTSD2SI", "CVTTSS2SI"):
                L.append(write_op(dst, 32, "(uint32_t)(int32_t)%s" % scalar(src, single)))
            elif m == "CVTSD2SS":
                L.append(store_scalar(dst, "(float)%s" % scalar(src, False), True))
            elif m == "CVTSS2SD":
                L.append(store_scalar(dst, "(double)%s" % scalar(src, True), False))
            else:  # MOVSD, MOVSS between registers or memory
                L.append(store_scalar(dst, scalar(src, single), single))
                if dst.kind == "xmm" and src.kind != "xmm":
                    # A load clears what is above the value; a move between
                    # registers keeps it.
                    L.extend("c->xmm[%d][%d] = 0u;" % (dst.reg, i)
                             for i in range(1 if single else 2, 4))
            return L

        if m in ("MOVUPS", "MOVAPS", "MOVDQU", "MOVDQA", "MOVQ", "MOVD", "PSHUFD"):
            def lane(op, i):
                if op.kind == "xmm":
                    return "c->xmm[%d][%d]" % (op.reg, i)
                return "rd32(%s + %du)" % (addr_expr(op), 4 * i)

            def put(op, i, value):
                if op.kind == "xmm":
                    return "c->xmm[%d][%d] = %s;" % (op.reg, i, value)
                return "wr32(%s + %du, %s);" % (addr_expr(op), 4 * i, value)

            dst, src = ops[0], ops[1]
            if m == "PSHUFD":
                sel = parse_imm(ins.ops[2])
                # Read every lane before writing one: the destination is
                # commonly the source, and a shuffle is not a sequence of
                # independent moves.
                L.extend("uint32_t s%d_ = %s;" % (i, lane(src, i)) for i in range(4))
                L.extend(put(dst, i, "s%d_" % ((sel >> (2 * i)) & 3)) for i in range(4))
                return L
            if m == "MOVD":
                # A dword between an XMM lane and a general register or memory,
                # zeroing what is above it when the XMM side is written.
                if dst.kind == "xmm":
                    L.append(put(dst, 0, read_op(src, 32)))
                    L.extend(put(dst, i, "0u") for i in range(1, 4))
                    return L
                L.append(write_op(dst, 32, lane(src, 0)))
                return L
            lanes = 2 if m == "MOVQ" else 4
            L.extend("uint32_t s%d_ = %s;" % (i, lane(src, i)) for i in range(lanes))
            L.extend(put(dst, i, "s%d_" % i) for i in range(lanes))
            # MOVQ into a register clears the upper half; into memory it
            # writes eight bytes and stops.
            if m == "MOVQ" and dst.kind == "xmm":
                L.extend(put(dst, i, "0u") for i in range(2, 4))
            return L
        if m in ("SFENCE", "LFENCE", "MFENCE", "VZEROUPPER", "VZEROALL", "PREFETCHNTA",
                 "PREFETCHT0", "PREFETCHT1", "PREFETCHT2"):
            # Ordering and cache hints on a machine with one guest thread of
            # execution at a time, and no AVX state to clear.
            return [";"]

        # --------------------------------------------------------- system --
        if m == "RDTSC":
            return ["recomp_rdtsc(c);"]
        if m == "CPUID":
            return ["recomp_cpuid(c);"]
        if m == "CLI":
            return ["recomp_cli(c);"]
        if m == "STI":
            return ["recomp_sti(c);"]
        if m == "HLT":
            return ["recomp_hlt(c);"]
        if m == "INT":
            return ["recomp_int(c, %s);" % hexlit(parse_imm(ins.ops[0]))]
        if m == "INT3":
            return ["recomp_int(c, 3u);"]
        if m == "IN":
            size = operand_size(ops[:1], hint=32)
            port = read_op(ops[1], 32) if len(ops) > 1 else "0u"
            L.append(write_op(ops[0], size, "recomp_in(c, %s, %d)" % (port, size // 8)))
            return L
        if m == "OUT":
            size = operand_size(ops[1:], hint=8)
            port = read_op(ops[0], 32) if ops[0].kind == "reg" else read_op(ops[0], 32)
            L.append("recomp_out(c, %s, %s, %d);" % (port, read_op(ops[1], size), size // 8))
            return L
        if m in ("NOP", "WAIT", "PAUSE"):
            return [";"]
        if m == "EMMS":
            # Every x87 register empty; TOP and the values are left alone.
            # Codecs call a bare `emms; ret` whatever CPUID said.
            return ["c->fpu_tag = 0xffffu;"]
        if m == "STMXCSR":
            # No translated SSE arithmetic changes MXCSR, so expose its reset value.
            return ["wr32(%s, 0x1f80u);" % addr_expr(ops[0])]
        if m in ("FNCLEX", "FCLEX"):
            return ["c->fpu_sw &= (uint16_t)~0x80ffu;"]

        # ------------------------------------------------------------ x87 --
        if m.startswith("F"):
            return self.emit_x87(fn, ins, m, ops)

        if is_vector_insn(m, ins.ops or ()):
            # Not one of the forms modelled above: a trap says so if a guest
            # that skipped the CPUID check ever reaches it.
            return ["recomp_unmodelled(c, %s); return;" % hexlit(ins.addr)]
        raise TranslateError("unhandled mnemonic %s" % m)

    # ---- control-flow helpers -------------------------------------------

    def reject_offimage_call(self, t):
        """A direct CALL whose literal target is not in the image at all.

        No such instruction can be real: the bytes were decoded out of step
        with the stream, which is what a listing does to the padding and
        tables behind a function's last instruction. Under --allow-unmodelled
        it goes through the same door as any other instruction the translator
        cannot model - a trap at its own address - rather than surviving as a
        dispatch target that reaches no translated code and fails the build
        later, far from its cause. Without the switch the dangling-target
        check still reports it, which is the stricter reading and stays the
        default.

        Only a call. A conditional jump out of the image is how a bad
        speculative block gives itself away, and discovery already prunes it."""
        if not self.allow_unmodelled:
            return
        if self.image.end <= self.image.base:
            return  # a translator built without bytes, as the unit tests are
        if self.image.base <= t < self.image.end:
            return
        if GUEST_SHIM_BASE <= t < GUEST_SHIM_END or t == INTRINSIC_SETJMP:
            return
        raise TranslateError("call to %08x, which is outside the image" % t)

    def goto_target(self, fn, t, ins):
        if t in fn.index:
            return ["goto L_%08x;" % t]
        if t in self.func_addrs:
            self.stats["_tailcall"] += 1
            return ["CALL_FN(%08x); return;" % t]
        self.stats["_tailcall_unknown"] += 1
        self.notes.append("%08x: jump to %08x outside any known function" % (ins.addr, t))
        return ["c->eip = %s; recomp_jump(c, %s); return;" % (hexlit(ins.addr), hexlit(t))]

    def emit_indirect_jump(self, fn, i, ins, op):
        if i in fn.return_jumps:
            self.stats["_jmp_popped_return"] += 1
            orphan = ["recomp_seh_frame_orphan(c, seh_mark_);"] if i in fn.seh_escapes else []
            # A JMP through the entry stack slot is a RET written out longhand,
            # so it ends like one. Returning on the strength of the proof alone
            # assumes the slot held this host frame's return address, and the
            # proof only ever established where the value came from, not what
            # it is: a block recovered as its own function starts at delta zero
            # holding whatever its real caller pushed, and for Delphi's finally
            # idiom - PUSH resume; CALL cleanup; POP EAX; JMP EAX - that is a
            # continuation INTO the establishing body. Setting EIP and
            # returning drops it, and the establishing body's epilogue never
            # runs, so it never restores EBP; its caller then reads its own
            # locals through a frame pointer that moved, and the damage shows
            # up as a wrong value somewhere else entirely. recomp_return keeps
            # a genuine return as cheap as it was and dispatches the rest.
            return orphan + ["c->eip = %s; recomp_return(c); return;" % read_op(op, 32)]
        targets = self.jumptables.get((fn.addr, ins.addr))
        L = ["uint32_t t_ = %s;" % read_op(op, 32)]
        if not targets:
            # A table-less jump may enter any instruction of this body. Keep
            # that transfer in the current host frame; nonlocal targets and
            # holes in the listing retain the existing runtime dispatch.
            L.append("if (t_ >= %s && t_ < %s) {" % (hexlit(fn.insns[0].addr), hexlit(fn.end)))
            L.append("switch (t_) {")
            for t in sorted(fn.addrs - getattr(fn, "dead_addrs", set())):
                L.append("case %s: goto L_%08x;" % (hexlit(t), t))
            L.extend(["default: break;", "}", "}"])
            self.stats["_jmp_indirect_tail"] += 1
            L.append("c->eip = %s; recomp_jump(c, t_); return;" % hexlit(ins.addr))
            return L
        self.stats["_jmp_table"] += 1
        self.stats["_jmp_table_entries"] += len(targets)
        L.append("switch (t_) {")
        for t in sorted(set(targets)):
            if t in fn.index:
                L.append("case %s: goto L_%08x;" % (hexlit(t), t))
            else:
                L.append("case %s: CALL_FN(%08x); return;" % (hexlit(t), t))
        L.append("default: c->eip = %s; recomp_jump(c, t_); return;" % hexlit(ins.addr))
        L.append("}")
        return L

    def emit_string(self, ins, m):
        suf = {"B": "b", "W": "w", "D": "d"}[m[-1]]
        kind = m[:-1].lower()
        if kind in ("scas", "cmps"):
            if ins.rep == "REPE" or ins.rep == "REP":
                return ["repe_%s(c);" % m.lower()]
            if ins.rep == "REPNE":
                return ["repne_%s(c);" % m.lower()]
            if ins.rep:
                raise TranslateError("prefix %s on %s" % (ins.rep, m))
            return ["%s(c);" % m.lower()]
        if ins.rep in ("REP", "REPE"):
            return ["rep_%s%s(c);" % (kind, suf)]
        if ins.rep:
            raise TranslateError("prefix %s on %s" % (ins.rep, m))
        return ["%s%s(c);" % (kind, suf)]

    def cross_check_switches(self, funcs):
        """Plan correction 8: compare each decoded jump table against the case
        labels in Ghidra's own decompilation of the same function.  Ghidra
        merges cases that share a body, so its label count is a lower bound on
        the number of selector values; a decoded table with fewer distinct
        targets than Ghidra has distinct case labels means entries were missed."""
        case_re = re.compile(r"^\s*case (-?(?:0x[0-9a-fA-F]+|\d+)):", re.M)
        by_fn = defaultdict(list)
        for (fn_addr, at), targets in self.jumptables.items():
            by_fn[fn_addr].append((at, targets))
        for fn in funcs:
            if fn.addr not in by_fn:
                continue
            path = os.path.join(LISTINGS, "%08x.c" % fn.addr)
            if not os.path.exists(path):
                continue
            try:
                cases = set(case_re.findall(open(path).read()))
            except OSError:
                continue
            if not cases:
                continue
            entries = sum(len(t) for _at, t in by_fn[fn.addr])
            self.stats["_switch_checked"] += 1
            # Cases that share a body collapse to one target, so distinct
            # targets can legitimately be fewer than case labels; what cannot
            # happen is fewer table entries than selector values.
            if entries < len(cases):
                self.stats["_switch_short"] += 1
                self.notes.append(
                    "%08x: decoded %d jump-table entries but Ghidra's .c has %d "
                    "case labels" % (fn.addr, entries, len(cases)))

    # ---- x87 -------------------------------------------------------------

    X87_MEM_INT = {"FILD", "FISTP", "FIST", "FIADD", "FISUB", "FISUBR",
                   "FIMUL", "FIDIV", "FIDIVR", "FICOM", "FICOMP"}

    def x87_mem_value(self, op):
        """Value of an x87 memory source as a C double expression."""
        if op.size == 32:
            return "(double)rdf32(%s)" % addr_expr(op)
        if op.size == 64:
            return "rdf64(%s)" % addr_expr(op)
        if op.size == 80:
            return "rdf80(%s)" % addr_expr(op)
        raise TranslateError("bad x87 memory size %r" % op.size)

    def x87_int_value(self, op):
        return "(double)" + self.x87_signed_value(op)

    def x87_signed_value(self, op):
        if op.size in (16, 32, 64):
            return "(int%d_t)rd%d(%s)" % (op.size, op.size, addr_expr(op))
        raise TranslateError("bad x87 integer size %r" % op.size)

    def emit_mmx(self, ins, m):
        """MMX on the eight MMn registers, through runtime/x86.h's helpers."""
        ops = [parse_operand(o) for o in ins.ops]

        def src(op, width=64):
            if op.kind == "mm":
                return "c->mm[%d]" % op.reg
            if op.kind == "mem":
                return "rd64(%s)" % addr_expr(op) if width == 64 else "(uint64_t)rd32(%s)" % addr_expr(op)
            if op.kind == "imm":
                return "(uint64_t)0x%xu" % (op.imm & 0xff)
            if op.kind == "reg" and op.size == 32:
                return "(uint64_t)c->r[%d]" % op.reg
            raise TranslateError("MMX operand %r" % op.kind)

        if m == "MOVQ":
            dst, s = ops
            if dst.kind == "mm":
                return ["c->mm[%d] = %s;" % (dst.reg, src(s))]
            if dst.kind == "mem" and s.kind == "mm":
                return ["wr64(%s, c->mm[%d]);" % (addr_expr(dst), s.reg)]
            raise TranslateError("MOVQ form")
        if m == "MOVD":
            dst, s = ops
            if dst.kind == "mm":
                return ["c->mm[%d] = %s;" % (dst.reg, src(s, 32))]
            if s.kind == "mm":
                return [write_op(dst, 32, "(uint32_t)c->mm[%d]" % s.reg)]
            raise TranslateError("MOVD form")
        dst = ops[0]
        if dst.kind != "mm":
            raise TranslateError("%s destination is not an MMX register" % m)
        if m in MMX_SHIFT:
            fn, bits = MMX_SHIFT[m]
            return ["c->mm[%d] = %s(c->mm[%d], %s, %du);" % (dst.reg, fn, dst.reg, src(ops[1]), bits)]
        fn, bits = MMX_BINARY[m]
        if bits:
            return ["c->mm[%d] = %s(c->mm[%d], %s, %du);" % (dst.reg, fn, dst.reg, src(ops[1]), bits)]
        return ["c->mm[%d] = %s(c->mm[%d], %s);" % (dst.reg, fn, dst.reg, src(ops[1]))]

    def emit_x87(self, fn, ins, m, ops):
        L = []
        st = lambda n: "ST(c, %d)" % n
        # Writing a register goes through fset so its tag follows the value.
        setst = lambda n, v: "fset(c, %d, %s);" % (n, v)

        if m == "FLD":
            if ops[0].kind == "st":
                L.append("fpush_st(c, %d);" % ops[0].sti)
            else:
                L.append("fpush(c, %s);" % self.x87_mem_value(ops[0]))
            return L
        if m == "FLD1":
            return ["fpush(c, 1.0);"]
        if m == "FLDZ":
            return ["fpush(c, 0.0);"]
        if m == "FLDPI":
            return ["fpush(c, 3.14159265358979323846);"]
        if m == "FLDLN2":
            return ["fpush(c, 0.69314718055994530942);"]
        if m == "FLDL2E":
            return ["fpush(c, 1.44269504088896340736);"]
        if m == "FLDLG2":
            return ["fpush(c, 0.30102999566398119521);"]
        if m == "FLDL2T":
            return ["fpush(c, 3.32192809488736234787);"]
        if m == "FBSTP":
            return ["wrbcd80(%s, fpop(c));" % addr_expr(ops[0])]
        if m == "FILD":
            return ["fpush_int(c, %s);" % self.x87_signed_value(ops[0])]

        if m in ("FST", "FSTP"):
            if ops and ops[0].kind == "st":
                if ops[0].sti != 0:
                    L.append("fcopy(c, %d, 0);" % ops[0].sti)
            elif ops:
                op = ops[0]
                if op.size == 32:
                    L.append("wrf32(%s, fto_float(c, %s));" % (addr_expr(op), st(0)))
                elif op.size == 64:
                    L.append("wrf64(%s, %s);" % (addr_expr(op), st(0)))
                elif op.size == 80:
                    L.append("wrf80(%s, %s);" % (addr_expr(op), st(0)))
                else:
                    raise TranslateError("bad %s size" % m)
            if m == "FSTP":
                L.append("fdrop(c);")
            return L

        if m in ("FIST", "FISTP"):
            op = ops[0]
            if op.size == 16:
                L.append("wr16(%s, (uint16_t)fist_i16(c));" % addr_expr(op))
            elif op.size == 32:
                L.append("wr32(%s, (uint32_t)fist_i32(c));" % addr_expr(op))
            elif op.size == 64:
                L.append("wr64(%s, (uint64_t)fist_i64(c));" % addr_expr(op))
            else:
                raise TranslateError("bad %s size" % m)
            if m == "FISTP":
                L.append("fdrop(c);")
            return L

        # arithmetic ------------------------------------------------------
        ARITH = {"FADD": "+", "FSUB": "-", "FMUL": "*", "FDIV": "/",
                 "FSUBR": "-", "FDIVR": "/"}

        def combine(lhs, op, rhs):
            """`lhs op rhs`, routing division through the #Z check."""
            if op == "/":
                return "fdivz(c, %s, %s)" % (lhs, rhs)
            return "%s %s %s" % (lhs, op, rhs)
        base = m[:-1] if m.endswith("P") and m[:-1] in ARITH else None
        if m in ARITH:                       # non-popping
            rev = m in ("FSUBR", "FDIVR")
            o = ARITH[m]
            if not ops:
                raise TranslateError("%s without operand" % m)
            if len(ops) == 2:
                d, s = ops[0].sti, ops[1].sti
                lhs, rhs = (st(s), st(d)) if rev else (st(d), st(s))
                L.append(setst(d, "fx87(c, %s)" % combine(lhs, o, rhs)))
            elif ops[0].kind == "st":
                # Ghidra can print both D8 (ST0 destination) and DC (STi
                # destination) as a single STi operand. Recover the direction
                # from the pinned instruction bytes; the short text alone is
                # ambiguous. Legacy prefixes do not change that direction.
                at = ins.addr
                while self.image.rd8(at) in (0x26, 0x2e, 0x36, 0x3e, 0x64,
                                              0x65, 0x66, 0x67, 0x9b):
                    at += 1
                d, s = (ops[0].sti, 0) if self.image.rd8(at) == 0xdc else (0, ops[0].sti)
                lhs, rhs = (st(s), st(d)) if rev else (st(d), st(s))
                L.append(setst(d, "fx87(c, %s)" % combine(lhs, o, rhs)))
            else:
                v = (self.x87_mem_value(ops[0]))
                L.append("double v_ = %s;" % v)
                lhs, rhs = ("v_", st(0)) if rev else (st(0), "v_")
                L.append(setst(0, "fx87(c, %s)" % combine(lhs, o, rhs)))
            return L

        if base:                             # FADDP/FSUBP/FMULP/FDIVP/...
            rev = base in ("FSUBR", "FDIVR")
            o = ARITH[base]
            d = ops[0].sti if ops else 1
            lhs, rhs = (st(0), st(d)) if rev else (st(d), st(0))
            L.append(setst(d, "fx87(c, %s)" % combine(lhs, o, rhs)))
            L.append("fdrop(c);")
            return L

        IARITH = {"FIADD": "+", "FISUB": "-", "FIMUL": "*", "FIDIV": "/",
                  "FISUBR": "-", "FIDIVR": "/"}
        if m in IARITH:
            rev = m in ("FISUBR", "FIDIVR")
            o = IARITH[m]
            L.append("double v_ = %s;" % self.x87_int_value(ops[0]))
            lhs, rhs = ("v_", st(0)) if rev else (st(0), "v_")
            L.append(setst(0, "fx87(c, %s)" % combine(lhs, o, rhs)))
            return L

        # comparison ------------------------------------------------------
        if m in ("FCOM", "FCOMP", "FUCOM", "FUCOMP"):
            # ops[-1] is the source: a two-operand form is `FCOM ST0,STi`, so
            # taking ops[0] would compare ST(0) with itself.
            if not ops:
                other = st(1)
            elif ops[-1].kind == "st":
                other = st(ops[-1].sti)
            else:
                other = self.x87_mem_value(ops[-1])
            # FUCOM raises the invalid exception only for a signalling NaN.
            L.append("%s(c, %s, %s);" % ("fucom" if m.startswith("FU") else "fcom",
                                         st(0), other))
            if m.endswith("P"):
                L.append("fdrop(c);")
            return L
        if m in ("FCOMPP", "FUCOMPP"):
            L.append("%s(c, %s, %s);" % ("fucom" if m.startswith("FU") else "fcom",
                                         st(0), st(1)))
            L.append("fdrop(c); fdrop(c);")
            return L
        if m in ("FICOM", "FICOMP"):
            L.append("fcom(c, %s, %s);" % (st(0), self.x87_int_value(ops[0])))
            if m == "FICOMP":
                L.append("fdrop(c);")
            return L
        if m in ("FCOMI", "FCOMIP", "FUCOMI", "FUCOMIP"):
            other = st(ops[-1].sti) if ops else st(1)
            L.append("%s(c, %s, %s);" % ("fucomi" if m.startswith("FU") else "fcomi",
                                         st(0), other))
            if m.endswith("IP"):
                L.append("fdrop(c);")
            return L
        if m == "FTST":
            return ["fcom(c, %s, 0.0);" % st(0)]
        if m == "FXAM":
            return ["fxam(c);"]

        # transcendental / misc --------------------------------------------
        # Per the SDM, precision control affects only FADD/FSUB/FMUL/FDIV
        # (with their integer and popping forms) and FSQRT.  FRNDINT and the
        # transcendentals must keep the register's full precision, otherwise
        # PC=00 turns an exact 16777217 into 16777216.
        UNARY = {"FABS": "fabs(%s)", "FCHS": "-(%s)",
                 "FSQRT": "fx87(c, sqrt(%s))",
                 "FSIN": "fx87_exact(c, sin(%s))",
                 "FCOS": "fx87_exact(c, cos(%s))",
                 "FRNDINT": "fx87_exact(c, fround_cw(c, %s))"}
        if m in UNARY:
            return [setst(0, UNARY[m] % st(0))]
        if m == "FPTAN":
            L.append("double v_ = %s;" % st(0))
            L.append(setst(0, "fx87_exact(c, tan(v_))"))
            L.append("fpush(c, 1.0);")
            return L
        if m == "FSINCOS":
            L.append("double v_ = %s;" % st(0))
            L.append(setst(0, "fx87_exact(c, sin(v_))"))
            L.append("fpush(c, fx87_exact(c, cos(v_)));")
            return L
        if m == "FSCALE":
            return [setst(0, "fx87_exact(c, fscale(%s, %s))" % (st(0), st(1)))]
        if m == "FPATAN":
            L.append(setst(1, "fx87_exact(c, atan2(%s, %s))" % (st(1), st(0))))
            L.append("fdrop(c);")
            return L
        if m == "FYL2X":
            L.append(setst(1, "fx87_exact(c, %s * log2(%s))" % (st(1), st(0))))
            L.append("fdrop(c);")
            return L
        if m == "FYL2XP1":
            # log2(x+1) loses every significant bit for small x; log1p keeps
            # them.  With ST(0) = 2^-54 and ST(1) = 1 the naive form returns 0.
            L.append(setst(1, "fx87_exact(c, %s * log1p(%s) / M_LN2)"
                           % (st(1), st(0))))
            L.append("fdrop(c);")
            return L
        if m == "F2XM1":
            # Likewise 2^x - 1: expm1(x * ln 2) is the same value without the
            # cancellation.
            return [setst(0, "fx87_exact(c, expm1(%s * M_LN2))" % st(0))]
        if m in ("FPREM", "FPREM1"):
            return [setst(0, "fprem_common(c, %s, %s, %d)"
                          % (st(0), st(1), 1 if m == "FPREM1" else 0))]
        if m == "FXTRACT":
            L.append("double v_ = %s;" % st(0))
            L.append(setst(0, "fxtract_exponent(v_)"))
            L.append("fpush(c, fxtract_significand(v_));")
            return L
        if m == "FDECSTP":
            # Both rotate TOP and clear C1.
            return ["c->fpu_top = (c->fpu_top - 1u) & 7u;",
                    "c->fpu_sw &= (uint16_t)~0x0200u;"]
        if m == "FINCSTP":
            return ["c->fpu_top = (c->fpu_top + 1u) & 7u;",
                    "c->fpu_sw &= (uint16_t)~0x0200u;"]
        if m == "FNOP":
            return [";"]
        if m == "FXCH":
            # Capstone renders D9 C9 with the implicit ST(0) present, as
            # `FXCH ST0,ST1`, where the listing writes `FXCH ST1`.  Taking
            # ops[0] there selects ST(0) and swaps the register with itself.
            return ["fxch(c, %d);" % (ops[-1].sti if ops else 1)]
        if m in ("FNSTSW", "FSTSW"):
            if ops and ops[0].kind == "reg":
                return ["c->r[0] = (c->r[0] & 0xffff0000u) | fstsw(c);"]
            return ["wr16(%s, fstsw(c));" % addr_expr(ops[0])]
        if m in ("FSTCW", "FNSTCW"):
            return ["wr16(%s, c->fpu_cw);" % addr_expr(ops[0])]
        if m == "FLDCW":
            return ["x87_set_cw(c, rd16(%s));" % addr_expr(ops[0])]
        if m == "FFREE":
            return [";"]
        if m in ("FSAVE", "FNSAVE"):
            return ["x87_fnsave(c, %s);" % addr_expr(ops[0])]
        if m == "FRSTOR":
            return ["x87_frstor(c, %s);" % addr_expr(ops[0])]
        if m in ("FINIT", "FNINIT"):
            return ["x87_finit(c);"]
        if m in ("FNSTENV", "FSTENV"):
            return ["x87_fnstenv(c, %s);" % addr_expr(ops[0])]
        if m == "FLDENV":
            return ["x87_fldenv(c, %s);" % addr_expr(ops[0])]

        raise TranslateError("unhandled x87 mnemonic %s" % m)


# --------------------------------------------------------------- driver ----

WITHDRAWN_CALL_RE = re.compile(r"CALL_FN\(([0-9a-f]{8})\);( return;)?")
WITHDRAWN_JUMP_RE = re.compile(r"(?:c->eip = 0x[0-9a-f]{1,8}u; )?recomp_jump\(c, 0x([0-9a-f]{1,8})u\); return;")


def dispatch_outside(body, known):
    """Send every literal transfer to an address this translation does not
    carry through the runtime's own dispatch.

    A module of the image's own code (--as-module) carries a few functions out
    of thousands, and the calls and jumps between them and the rest of the
    image are ordinary. The image's table has those addresses, so a literal
    CALL_FN becomes recomp_call and a literal jump becomes recomp_jump. Only
    literal spellings change; nothing else in the body does."""
    out = []
    for line in body:
        def call(m):
            target = int(m.group(1), 16)
            if target in known:
                return m.group(0)
            tail = " return;" if m.group(2) else ""
            return "recomp_call(c, 0x%08xu);%s" % (target, tail)

        def jump(m):
            target = int(m.group(1), 16)
            if target in known:
                return m.group(0)
            return "recomp_jump(c, 0x%08xu); return;" % target
        out.append(WITHDRAWN_JUMP_RE.sub(jump, WITHDRAWN_CALL_RE.sub(call, line)))
    return out


def retarget_withdrawn(body, pruned):
    """Turn every literal transfer to a withdrawn block into a trap.

    A block the sweep recovered and then withdrew (its own dispatch went
    nowhere, so it was never code: padding after a call that does not
    return, a table read as instructions) may still be named by the
    function that fell through or jumped to it.  Real code never gets there
    - the call before it threw or exited - so the transfer becomes
    `recomp_unknown_call(c, addr); return;`, which the runtime reports if it
    is ever reached and which the entry-point gate does not count as a
    dispatch.  Only literal spellings change; nothing else in the body does."""
    if not pruned:
        return body
    out = []
    for line in body:
        def call(m):
            target = int(m.group(1), 16)
            if target not in pruned:
                return m.group(0)
            return "recomp_unknown_call(c, 0x%08xu); return;" % target

        def jump(m):
            target = int(m.group(1), 16)
            if target not in pruned:
                return m.group(0)
            return "recomp_unknown_call(c, 0x%08xu); return;" % target
        out.append(WITHDRAWN_JUMP_RE.sub(jump, WITHDRAWN_CALL_RE.sub(call, line)))
    return out


def load_functions(only=None):
    funcs = []
    with open(FUNCS_TSV) as fh:
        rows = [l.rstrip("\n").split("\t") for l in fh][1:]
    for row in rows:
        addr = int(row[0], 16)
        name, nbytes = row[1], int(row[2])
        if only and "%08x" % addr not in only:
            continue
        path = os.path.join(LISTINGS, "%08x.asm" % addr)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            continue
        funcs.append((addr, name, nbytes, path))
    funcs.sort()
    return funcs



def emit_call_return_lookup(fh, call_returns):
    """Emit exact, immutable membership; bound bitmap cost for sparse images."""
    if call_returns and call_returns[-1] - call_returns[0] < 8 * 1024 * 1024:
        base, end = call_returns[0], call_returns[-1]
        words = [0] * ((end - base) // 32 + 1)
        for address in call_returns:
            offset = address - base
            words[offset // 32] |= 1 << (offset % 32)
        fh.write("/* One bit per possible continuation address; no mutable cache. */\n")
        fh.write("static const uint32_t recomp_call_return_bits[] = {\n")
        for i in range(0, len(words), 8):
            fh.write("    " + ", ".join("0x%08xu" % v for v in words[i:i + 8]) + ",\n")
        fh.write("};\nint recomp_is_call_return(uint32_t target)\n{\n")
        fh.write("    uint32_t offset = target - 0x%08xu;\n" % base)
        fh.write("    if (offset > 0x%08xu) return 0;\n" % (end - base))
        fh.write("    return (recomp_call_return_bits[offset >> 5] >> (offset & 31u)) & 1u;\n}\n\n")
    else:
        # Avoid a huge bitmap for scattered auxiliary code or an empty image.
        fh.write("""int recomp_is_call_return(uint32_t target)
{
    uint32_t lo = 0, hi = recomp_call_return_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (recomp_call_returns[mid] < target) lo = mid + 1; else hi = mid;
    }
    return lo < recomp_call_return_count && recomp_call_returns[lo] == target;
}

""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="where the generated sources go (build/recomp/gen)")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--eager-flags", action="store_true",
                    help="compute every flag at every instruction (debug)")
    ap.add_argument("--check-flags", action="store_true",
                    help="report flags that would cross CALL/RET boundaries")
    ap.add_argument("--report", default=None, help="write a JSON stats file")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--allow-unmodelled", metavar="REASON", default=None,
                    help="Translate instructions this translator cannot model into a trap at "
                         "their own address instead of refusing the image. For a listing that "
                         "decodes the data past a function's last instruction as code; reaching "
                         "one at run time is still fatal.")
    ap.add_argument("--allow-table-gaps", metavar="REASON", default=None,
                    help="accept jump-table entries that dispatch nowhere, "
                         "recording the reason in the report")
    ap.add_argument("--game", required=True, help="the directory holding game.toml")
    ap.add_argument("--forget", default=None, metavar="ADDR[,ADDR...]",
                    help="translate the image as if the listing had never named these "
                         "functions: they are dropped and every literal call to one becomes "
                         "an undeliverable call, which is what a listing gap does. For "
                         "measuring what a run can rediscover; never for a real build.")
    ap.add_argument("--discovered", default=None, metavar="FILE",
                    help="a file RECOMP_DISCOVERY wrote: addresses a run reached that its "
                         "translation did not carry, adopted as entry points on top of "
                         "game.toml's own")
    ap.add_argument("--as-module", default=None, metavar="NAME",
                    help="emit this translation as a module the runtime loads rather than as "
                         "the image's own table: prefixed symbols, a RecompModule registered "
                         "from a constructor, and every literal call or jump to an address "
                         "outside it dispatched through recomp_call. With --only, this is how "
                         "code a run discovered is compiled and loaded without rebuilding the "
                         "game (tools/lazy_static.py).")
    ap.add_argument("--module", default=None, metavar="KEY",
                    help="translate the auxiliary module [modules.aux.KEY] instead of the executable")
    args = ap.parse_args()
    cfg = game_config.load(args.game)
    configure(cfg)
    if args.module:
        configure_module(cfg, args.module)
    if args.as_module:
        if args.module:
            raise SystemExit("--as-module translates the image; --module translates a module")
        global SYMBOL_PREFIX, AUX_MODULE
        SYMBOL_PREFIX = "recomp_%s_" % re.sub(r"[^A-Za-z0-9_]", "_", args.as_module)
        AUX_MODULE = {"name": args.as_module, "base": cfg["game"]["image_base"],
                      "size": 0}  # the image's extent, filled in once it is read
    image = Image(BINARY)
    discovered = []
    if args.discovered:
        global EXTRA_ENTRY_POINTS
        all_discovered = read_discovered(args.discovered)
        # One run can discover missing code in both the EXE and auxiliary DLLs.
        # Each translation adopts only its own image's addresses; in-image data
        # still reaches the executable-section validation below and is rejected.
        selected = [a for a in all_discovered if image.base <= a < image.end]
        discovered = [a for a in selected if a not in EXTRA_ENTRY_POINTS]
        EXTRA_ENTRY_POINTS = EXTRA_ENTRY_POINTS | frozenset(discovered)
        print("  --discovered %s: %d address%s from a run, %d already in game.toml, "
              "%d outside this image"
              % (args.discovered, len(discovered), "" if len(discovered) == 1 else "es",
                 len(selected) - len(discovered), len(all_discovered) - len(selected)),
              file=sys.stderr)

    # An address named as an entry point is not forgotten: that is what makes
    # --forget with --discovered a loop rather than a contradiction. A run
    # reports the gap, and the regeneration that reads its file closes it.
    forget = frozenset(int(a, 16) for a in args.forget.split(",")) if args.forget else frozenset()
    forget = forget - EXTRA_ENTRY_POINTS
    if forget:
        print("  --forget: %s dropped as if the listing had never named them"
              % ", ".join("%08x" % a for a in sorted(forget)), file=sys.stderr)

    t0 = time.time()
    entries = load_functions(set(args.only) if args.only else None)
    all_addrs = set()
    listed_starts = []
    with open(FUNCS_TSV) as fh:
        for l in list(fh)[1:]:
            a = int(l.split("\t")[0], 16)
            listed_starts.append(a)
            p = os.path.join(LISTINGS, "%08x.asm" % a)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                all_addrs.add(a)

    if AUX_MODULE is not None and args.as_module:
        # A module of the image's own code covers the image's addresses; the
        # image's table is consulted first, so this one answers only for what
        # it carries.
        AUX_MODULE["size"] = image.size
    curated = read_curated(CURATED)
    # Addresses named by a dword the loader relocates: a vtable slot, a
    # function-pointer table, a stored callback.  Read once, before discovery,
    # so the evidence does not depend on which pass reaches an address first.
    relocated = image.relocated_pointers()
    hook_evidence = defaultdict(set)
    for name, addr in sorted(curated.get("alternates", {}).items()):
        hook_evidence[addr].add("curated")
    for name, addr in sorted(curated.get("entries", {}).items()):
        hook_evidence[addr].add("curated_entry")
    parsed = []
    failures = []
    for addr, name, nbytes, path in entries:
        try:
            insns = parse_listing(path)
        except TranslateError as e:
            failures.append((addr, "parse: %s" % e))
            continue
        fn = Function(addr, name, nbytes, insns)
        fn.measure(image)
        parsed.append(fn)

    # A listing that ends on a CALL was cut there because the callee never
    # returns; recovery from the PE must stop at those calls too, or it walks
    # into the padding and switch tables that follow them.
    image.noreturn_callees = noreturn_callees_from(parsed, getattr(image, "iat_names", {}), image)
    if OPERAND_REDIRECTS:
        apply_operand_redirects(parsed, OPERAND_REDIRECTS)

    # The Ghidra export is not complete: control lands on addresses it never
    # listed, and functions get split at boundaries other code jumps past.
    # Resolve both to a fixpoint - an address inside another function becomes
    # an alternate entry, an address in no listing is decoded from the PE.
    owner = {}
    # One byte per image byte: set where a byte belongs to a translated
    # instruction but is not its first.  A pointer landing there names no
    # place execution can begin, so it is neither an entry nor a candidate.
    interior_bytes = bytearray(image.size)

    def register(fn):
        for k, ins in enumerate(fn.insns):
            owner.setdefault(ins.addr, fn)
            end = fn.fallthrough[k]
            if end is None:
                end = (fn.insns[k + 1].addr if k + 1 < len(fn.insns) else fn.end)
            if not (image.base <= ins.addr and end <= image.end):
                continue
            for b in range(ins.addr + 1, min(end, image.end)):
                interior_bytes[b - image.base] = 1

    for fn in parsed:
        register(fn)

    listed_functions = {fn.addr for fn in parsed}
    original_instructions = {fn: set(fn.addrs) for fn in parsed}
    span_guesses = {}
    withdrawn_span_guesses = set()
    span_guess_instructions = defaultdict(set)
    # Listing size ends at its last exported instruction, which may be an
    # early finally RET. Only the next original listing bounds its full span;
    # newly discovered entries must not shrink that bound during recovery.
    listed_starts.sort()
    span_ends = {}
    for i, addr in enumerate(listed_starts):
        section_end = next((hi for lo, hi, _ in image.exec_ranges if lo <= addr < hi), addr)
        span_ends[addr] = min(section_end, listed_starts[i + 1]
                              if i + 1 < len(listed_starts) else section_end)
    bodies = {fn.addr: fn for fn in parsed}
    extra = {}
    recovered = []
    discovered_by_scan = [0]
    provenance = {}
    # Entry evidence is independent of instructions swept into a body. In
    # particular, finding an SEH frame in a pointer guess does not establish
    # that guess's entry (which may precede the real function in data).
    protected_entries = set(listed_functions)
    # Entry evidence, strongest first: original listing (4), explicit seed
    # or structural table (3), direct edge from protected code (2), relocated
    # pointer or validated direct callee of one (1), bare scan guess (0).
    # Relocations establish pointers, not code: rank 1 remains speculative.
    entry_strength = {addr: 4 for addr in listed_functions}
    candidate_starts = sorted(listed_functions)
    candidate_entries = set(candidate_starts)
    scan_aliases = set()
    pointer_callees = set()

    def remember_candidate(target):
        if target not in candidate_entries:
            candidate_entries.add(target)
            candidate_starts.insert(bisect_right(candidate_starts, target), target)

    finally_owners = {}
    interior_entries = [0]
    initterm_found = [0]
    immediate_entries = [0]
    initterm_tables = []
    rejected = set()
    tr = Translator(image, all_addrs, args)
    tr.noreturn_callees = image.noreturn_callees
    tr.noreturn_iat = {slot for slot, name in getattr(image, "iat_names", {}).items()
                       if name in NORETURN_IAT_IMPORTS}

    if args.check_flags:
        for fn in parsed:
            tr.prepare(fn)
        check_flags(tr, parsed)
        return 0

    os.makedirs(args.out, exist_ok=True)
    chunks = []

    def accepts(fn, reached=False):
        """Can the emitter translate every instruction of this block?

        A candidate address that is really data decodes into instructions this
        compiler never emits - `POP ES`, `DAS`, `LJMP` - and the emitter says
        so.  Using it as the filter means the vocabulary check can never drift
        from what the translator actually supports.

        `reached` says an instruction of a body this translation already
        carries names this block, which is what lets it end on a fall-out
        into another such entry; see the loop below."""
        if fn.addr not in protected_entries:
            if image.data[fn.addr - image.base:fn.addr - image.base + 2] == b"\x00\x00":
                # ADD byte ptr [EAX],AL is data at a speculative function start.
                return False
            if image.starts_with_utf16_run(fn.addr) or image.is_utf16_constant(fn.addr):
                return False
            # A speculative path may not fall out of its recovered body.
            # In particular, meeting another candidate is not an implicit
            # tail call: only an actual JMP/RET/noreturn CALL terminates it,
            # because padding in front of a function decodes just as well.
            #
            # A block a branch reached is not that guess: the branch is the
            # program saying these bytes execute, and the entry it falls into
            # is one the emitter can dispatch to, so the edge costs nothing.
            # Watcom's exception dispatcher needs exactly this. Populous
            # lands at 00565cc0, whose tail jumps to the unlisted 00567f18
            # (FXCH; FSTP) two bytes before a shared RET that a relocation
            # also names, 00567f1c. Refusing 00567f18 leaves 00565cc0 with a
            # dangling target, the pruner withdraws the landing pad, and the
            # dispatcher's JMP at 00567f11 has no block to enter. `resolve`'s
            # retry cannot serve that case: it overrules only a boundary that
            # nothing names, and 00567f1c has a relocation, so it keeps its
            # claim - and it should, the cut there is right. What was wrong
            # was calling the edge into it a fall-out.
            for i, ins in enumerate(fn.insns):
                if ins.mnem in TERMINATORS or tr.never_returns(ins):
                    continue
                fall = fn.fallthrough[i]
                if fall in fn.addrs:
                    continue
                if reached and fall is not None and fall in all_addrs and fall in owner:
                    continue
                return False
        notes, stats = len(tr.notes), dict(tr.stats)
        try:
            tr.prepare(fn)
            tr.translate(fn)
            return True
        except TranslateError:
            return False
        except Exception:                       # noqa: BLE001
            return False
        finally:
            del tr.notes[notes:]
            tr.stats.clear()
            tr.stats.update(stats)

    def prefix_before(fn, target):
        """Cut a guess at a candidate boundary, without keeping a partial opcode."""
        insns = [ins for i, ins in enumerate(fn.insns)
                 if ins.addr < target and fn.fallthrough[i] is not None
                 and fn.fallthrough[i] <= target]
        if not insns:
            return None
        last_end = image.insn_end(insns[-1].addr, insns[-1].mnem)
        if (insns[-1].mnem not in TERMINATORS and not tr.never_returns(insns[-1])):
            return None
        prefix = Function(fn.addr, fn.name, last_end - fn.addr, insns)
        prefix.measure(image)
        return prefix if accepts(prefix) else None

    def truncate_span_guess(target):
        """A PUSH guess does not inherit the listed owner's entry evidence.

        An omitted fragment can precede a real method not discovered until a
        later callback pass. Apply the same boundary/terminator rule to that
        fragment while preserving the original listing and unrelated landings.
        """
        keys = set()
        for addr in range(max(image.base, target - 14), target + 1):
            if addr in span_guess_instructions and addr <= target < (image.insn_end(addr, "") or addr + 1):
                keys.update(span_guess_instructions[addr])
        changed = False
        for key in keys:
            fn, start = key
            fragment = span_guesses.get(key)
            if fragment is None or target <= start or target in original_instructions.get(fn, ()):
                continue
            if target in Translator.pushed_continuations(fn):
                continue  # The fragment owns this RET epilogue, even if a scan also names it.
            prefix = prefix_before(fragment, target)
            removed = fragment.addrs - (prefix.addrs if prefix is not None else set())
            for addr in fragment.addrs:
                span_guess_instructions[addr].discard(key)
            if prefix is None:
                del span_guesses[key]
                withdrawn_span_guesses.add(start)
            else:
                span_guesses[key] = prefix
                for addr in prefix.addrs:
                    span_guess_instructions[addr].add(key)
            retained = original_instructions.get(fn, set()).copy()
            for (body, _), other in span_guesses.items():
                if body is fn:
                    retained.update(other.addrs)
            removed -= retained
            if not removed:
                continue
            for i, ins in enumerate(fn.insns):
                if ins.addr not in removed:
                    continue
                if owner.get(ins.addr) is fn:
                    del owner[ins.addr]
                hi = min(image.end, fn.fallthrough[i] or ins.addr + 1)
                lo = max(image.base, ins.addr + 1)
                if lo < hi:
                    interior_bytes[lo - image.base:hi - image.base] = b"\0" * (hi - lo)
            trimmed = Function(fn.addr, fn.name, fn.size,
                               [ins for ins in fn.insns if ins.addr not in removed])
            trimmed.measure(image)
            fn.__dict__.update(trimmed.__dict__)
            for addr in removed:
                if extra.get(addr) is fn:
                    del extra[addr]
                    if addr not in bodies:
                        all_addrs.discard(addr)
                if finally_owners.get(addr) is fn:
                    del finally_owners[addr]
            for other in parsed:
                if other is not fn and other.addrs & removed:
                    register(other)
            changed = True
        return changed

    def truncate_speculative(target):
        """Remove speculative coverage at another candidate, regardless of rank.

        A sweep may have decoded through the target, including through its
        first instruction byte. Clear that old coverage as well as its entry
        aliases, then restore any overlapping owners. A terminated prefix
        survives; one cut off mid-flow is withdrawn.
        """
        # The common path has no overlap. x86 instructions are at most 15
        # bytes, so checking preceding boundaries also finds misaligned hits.
        covered = {owner[a] for a in range(max(image.base, target - 14), target + 1)
                   if a in owner and a <= target < (image.insn_end(a, "") or a + 1)}
        if not any(fn.addr < target < max(fn.end, fn.fallthrough[-1] or fn.end)
                   and fn.addr not in protected_entries for fn in covered):
            return False
        changed = False
        for fn in covered:
            end = max(fn.end, fn.fallthrough[-1] or fn.end)
            if not (fn.addr < target < end) or fn.addr in protected_entries:
                continue
            if provenance.get(fn.addr) in STRUCTURAL_PROVENANCE:
                continue
            if target in getattr(fn, "pushed_continuations", ()):
                continue  # This is the body's own RET continuation, not another entry.
            prefix = prefix_before(fn, target)
            # Retiring the body is only right when something names the
            # address that displaces it. An unnamed candidate - a bare scan
            # guess sitting on an interior instruction boundary - is this
            # body's interior, not a function start, and destroying a body
            # that already validated to make room for it loses real code.
            # resolve() records such an address as an alias instead.
            if (prefix is None and entry_strength.get(target, 0) == 0
                    and target not in relocated and target not in pointer_callees):
                continue
            for i, ins in enumerate(fn.insns):
                if owner.get(ins.addr) is fn:
                    del owner[ins.addr]
                hi = fn.fallthrough[i] or ins.addr + 1
                lo = max(image.base, ins.addr + 1)
                hi = min(image.end, hi)
                if lo < hi:
                    interior_bytes[lo - image.base:hi - image.base] = b"\0" * (hi - lo)
            for addr, prior in list(extra.items()):
                if prior is fn and (prefix is None or addr not in prefix.addrs):
                    del extra[addr]
                    if addr not in bodies:
                        all_addrs.discard(addr)
            for addr, prior in list(finally_owners.items()):
                if prior is fn and (prefix is None or addr not in prefix.addrs):
                    del finally_owners[addr]
            if prefix is None:
                parsed.remove(fn)
                recovered.remove(fn)
                bodies.pop(fn.addr, None)
                all_addrs.discard(fn.addr)
                retired_finally_bodies.add(fn)
                rejected.add(fn.addr)
            else:
                fn.__dict__.update(prefix.__dict__)
            # register() preserves explicit ownership choices made by SEH
            # adoption; only missing boundaries and cleared bytes are rebuilt.
            for other in parsed:
                if other.addr < end and other.end > fn.addr:
                    register(other)
            changed = True
        return changed

    def protected_source(home):
        return home is not None and home.addr in protected_entries

    def resolve(t, listed, home=None, validate=True, why="branch", continuation=True):
        """Make `t` an entry point.  Returns True if that changed anything."""
        if t is None:
            return False
        strength = (3 if why in STRUCTURAL_PROVENANCE else
                    2 if why == "branch" and home is not None
                    and home.addr in protected_entries else
                    1 if t in relocated or t in pointer_callees else 0)
        previous_strength = entry_strength.get(t, 0)
        strength = max(previous_strength, strength)
        # A relocated pointer can still name text. Reject its content before
        # it can displace another candidate or become a recovery boundary.
        established_boundary = t in owner and owner[t].addr in protected_entries
        if strength < 2 and t in withdrawn_span_guesses:
            return False
        if strength < 2 and not established_boundary and (
                image.starts_with_utf16_run(t) or image.is_utf16_constant(t)
                or image.data[t - image.base:t - image.base + 2] == b"\x00\x00"):
            rejected.add(t)
            return False
        # Branches already inside the current body are its own control flow,
        # not independently proposed function starts.
        own_interior = home is not None and t in home.addrs
        independent = why != "branch" or protected_source(home)
        if (t in scan_aliases and t in owner
                and entry_strength.get(owner[t].addr, 0) > strength):
            independent = False  # Weaker naming of an established instruction boundary.
        if not own_interior and independent:
            remember_candidate(t)
        entry_strength[t] = strength
        stronger = strength > previous_strength
        protected = strength >= 2
        if protected:
            protected_entries.add(t)
        if (why == "branch" and continuation and home is not None
                and home.addr not in listed_functions and provenance.get(home.addr) == "seh"):
            why = "seh"
        # SEH landings into an already adopted normal cleanup retain the
        # existing split-then-adopt path below. Cutting the establishing frame
        # from that cleanup would change RET semantics. This is ownership of
        # a continuation, not stronger evidence for the speculative entry.
        seh_cleanup = (why == "seh" and t in owner
                       and owner[t] in finally_owners.values())
        fragment_truncated = (truncate_span_guess(t)
                              if (independent or not continuation) and not own_interior
                              and t not in finally_owners and not seh_cleanup else False)
        if fragment_truncated:
            remember_candidate(t)
        truncated = (truncate_speculative(t)
                     if independent and not own_interior and t not in finally_owners
                     and not seh_cleanup else False) or fragment_truncated
        if truncated:
            listed = set(owner)
        if (strength == 1 and t not in owner and image.is_exec(t)
                and interior_bytes[t - image.base]):
            return truncated  # a relocation cannot split stronger/equal code
        # An SEH continuation can already belong to a speculative recovered
        # body. Its bad prefix may later withdraw that body. Recover the
        # structurally named suffix independently instead of promoting the
        # guess, or dropping the real landing along with its guessed owner.
        prior = owner.get(t)
        separate = (why == "seh" and prior is not None and prior.addr != t
                    and t not in bodies
                    and prior.addr not in listed_functions
                    and prior.addr not in protected_entries and t not in finally_owners
                    and provenance.get(prior.addr, "branch") not in STRUCTURAL_PROVENANCE)
        # Before any of the early returns below.  See note_structural.
        promote_owner = not separate and (why != "seh" or prior is None
                                          or prior.addr in protected_entries)
        note_structural(provenance, owner if promote_owner else {}, t, why)
        if t in all_addrs and not separate:
            return stronger or truncated
        if home is not None and t in home.addrs:
            return False
        if t in owner and not separate:
            extra[t] = owner[t]
            all_addrs.add(t)
            # Why this address is an entry, which symbols.json turns into hook
            # eligibility.  A data pointer or an __initterm table naming it is
            # a stronger statement than its merely falling out of branch
            # following, so it replaces that; a structural naming has already
            # been recorded above and is never weakened here.
            if why != "branch" and provenance.get(t, "branch") == "branch":
                provenance[t] = why
            return True
        bounds = None
        boundaries = set()
        recovery_stops = listed - prior.addrs if separate else listed
        if protected and why == "config":
            # A configured entry states that this address IS a function, but
            # recovery still needs a ceiling. Unbounded, it runs past the body
            # into whatever follows, fails the content check and is dropped -
            # and the address the run asked for is silently absent, which is
            # the one outcome a declared entry point must never have.
            section_end = next((end for lo, end, _ in image.exec_ranges if lo <= t < end), t)
            span_index = bisect_right(listed_starts, t) - 1
            span_end = span_ends[listed_starts[span_index]] if span_index >= 0 else section_end
            bounds = (t, min(section_end, span_end))
        if not protected:
            section_end = next((end for lo, end, _ in image.exec_ranges if lo <= t < end), t)
            span_index = bisect_right(listed_starts, t) - 1
            span_end = span_ends[listed_starts[span_index]] if span_index >= 0 else section_end
            bounds = (t, min(section_end, span_end))
            # PUSH-named continuations and SEH landings retain body ownership;
            # their callable aliases are not new function-start boundaries.
            # This probe earns no protection: the candidate still passes the
            # ordinary content/terminator checks and the final pruning gate.
            probe = image.recover(t, recovery_stops, bounds=bounds)
            continuations, attempted, owned = set(), set(), set()
            fragments = {ins.addr: ins for ins in probe}
            while fragments:
                probe_fn = Function(t, "candidate", 0, [fragments[a] for a in sorted(fragments)])
                probe_fn.measure(image)
                stubs = set(seh_frame_sites(probe_fn, image).values())
                for ins in probe_fn.insns:
                    if ins.mnem == "PUSH" and ins.ops:
                        op = parse_operand(ins.ops[0])
                        if op.kind == "imm" and t < op.imm < bounds[1]:
                            continuations.add(op.imm)
                for stub in stubs:
                    got = image.seh_landings_opt(stub)
                    if got is None:
                        continue
                    landings, _ = got
                    continuations.update(a for a in landings if t < a < bounds[1])
                pending = continuations - stubs - attempted
                if not pending:
                    break
                for target in sorted(pending):
                    attempted.add(target)
                    # An earlier SEH/data pass may already own the cleanup.
                    # Recover its clean span-bounded bytes before judging the
                    # establishing prefix's termination, then let normal
                    # adoption transfer ownership without promoting the prefix.
                    block = image.recover(target, stubs, bounds=bounds)
                    fragment = Function(target, "continuation", 0, block)
                    fragment.measure(image)
                    if not block or not accepts(fragment):
                        continue
                    owned.update(ins.addr for ins in block)
                    fragments.update((ins.addr, ins) for ins in block)
            if owned:
                recovery_stops = set(recovery_stops) - owned
            lo_index, hi_index = bisect_right(candidate_starts, t), bisect_right(candidate_starts, bounds[1])
            boundaries = {a for a in candidate_starts[lo_index:hi_index]
                          if a not in owned and a not in finally_owners}
        insns = image.recover(t, recovery_stops, bounds=bounds, boundaries=boundaries)
        if not insns:
            return False
        name = ("FUN_%08x" if why == "config" else "recovered_%08x") % t
        new_fn = Function(t, name, insns[-1].addr + 1 - t, insns)
        new_fn.measure(image)
        # A block reached from an established one is established too.
        inherited = provenance.get(
            home.addr if home is not None else None, "branch")
        # Landing provenance describes recovered continuations, not the
        # System dispatcher or the entire call graph of a listed owner.
        if inherited == "seh" and (not continuation or home.addr in listed_functions):
            inherited = "branch"
        # Config names only its explicit entries, not every branch they reach.
        provenance[t] = why if why != "branch" else (
            "branch" if inherited == "config" else inherited)
        # A branch reached this block if an instruction of a body the
        # translation already carries names it, which is what lets it end on
        # a fall-out into another entry; see `accepts`.
        reached = home is not None
        if validate and not accepts(new_fn, reached=reached):
            # Two different things can have refused the body, so there are two
            # ways back, on separate axes: which boundaries may cut a body, and
            # which fall-outs are acceptable. `reached` above answers the
            # second - the edge into a named entry is not a guess - and it
            # cannot answer the first, because a boundary that a relocation or
            # an owner names rightly keeps its claim and rightly cuts there.
            #
            # A candidate that nothing names - a bare scan guess whose shape
            # merely resembles a thunk - can sit on an interior instruction
            # boundary of a real function and truncate it into a fragment.
            # Retry without those, but only when every boundary that could
            # have cut this body is unnamed: a callee of a relocated pointer,
            # or anything already owned, keeps its claim and the fragment
            # stays rejected.
            retry_fn = None

            def worthless_claim(a):
                """Is `a` held only by a guess with nothing behind it?

                Being owned already is not evidence: a bare scan guess that
                happened to be resolved first owns its own address and every
                byte it swallowed, and that alone would let it keep a real
                function's body for no better reason than arriving earlier.
                A claim counts when something names it - an entry a listing
                or a table gives, a relocation, a callee of a stored pointer
                - and not when the owner is another strength-0 guess.
                """
                o = owner.get(a)
                return (o is not None and entry_strength.get(o.addr, 0) == 0
                        and provenance.get(o.addr, "branch") not in STRUCTURAL_PROVENANCE
                        and o.addr not in relocated and o.addr not in pointer_callees)

            retry = image.recover(t, recovery_stops, bounds=bounds,
                                  may_pass=worthless_claim)
            if retry:
                candidate = Function(t, name, retry[-1].addr + 1 - t, retry)
                candidate.measure(image)
                # Only what lies inside the body can have cut it, and only a
                # claim nothing stands behind may be overruled.
                #
                # The body's last instruction can straddle the boundary that
                # cut it: recover() will not consume another candidate's first
                # opcode byte, so a candidate landing inside that instruction
                # truncates the body one instruction early and then sits at
                # its end rather than inside it. MSVC's two-instruction EH
                # funclets are the shape that shows it - MOV EAX,<handler
                # data>; JMP <shared unwinder> - where a stray scan hit inside
                # the JMP's displacement dropped the JMP and left a fragment
                # that falls out of itself. Measure to the last instruction's
                # final byte so that boundary is judged like the rest.
                #
                # Nothing inside is not a reason to refuse: recovery may have
                # been cut by a worthless claim rather than by a boundary, and
                # may_pass has already limited what it could step over.
                end = image.insn_end(retry[-1].addr, retry[-1].mnem)
                inside = [a for a in boundaries if t < a < end]
                if all(entry_strength.get(a, 0) == 0
                       and a not in pointer_callees and a not in relocated
                       and (a not in owner or worthless_claim(a))
                       for a in inside):
                    retry_fn = candidate
            # The wider body is judged by the same rule as the first one: a
            # block a branch reached may still end in another entry.
            if retry_fn is not None and accepts(retry_fn, reached=reached):
                new_fn = retry_fn
            else:
                # Decoded into something this compiler never emits, so the
                # address is data.  Dropping it leaves any jump to it aborting
                # at runtime with the address printed, which beats nonsense.
                rejected.add(t)
                return False
        parsed.append(new_fn)
        bodies[t] = new_fn
        recovered.append(new_fn)
        all_addrs.add(t)
        register(new_fn)
        if separate:
            extra.pop(t, None)
            for ins in new_fn.insns:
                if owner.get(ins.addr) is prior:
                    owner[ins.addr] = new_fn
        return True

    retired_finally_bodies = set()

    def extend_finally_body(fn):
        """Keep normal cleanup and its pushed epilogue in the establishing body.

        A Delphi untyped stub's landing JMP names the shared cleanup entry.
        A normal edge into that entry belongs to this body, even when the
        listing ended before it. The PUSH immediately before that edge names
        its return continuation, not an unrelated callback. Recover these
        blocks before the SEH scan can give them separate host frames. For
        listed functions, also attach clean pushed continuations and handler
        landings throughout the span ending at the next listed function.
        """
        changed = False
        span_end = span_ends.get(fn.addr)
        stubs = set(seh_frame_sites(fn, image).values())

        def adopt(target, in_span=False, guessed=False):
            if guessed and target in withdrawn_span_guesses:
                return False
            # A PUSH of an address the program names itself is a callback
            # registration - Delphi hands a window procedure to
            # MakeObjectInstance exactly this way - not a pushed cleanup
            # continuation that happens to live in this span. Absorbing it
            # retires the entry into an alternate that emits no dispatch
            # entry, so the call the program makes finds nothing.
            if provenance.get(target) in NAMED_PROVENANCE:
                return False
            prior = owner.get(target)
            # A pointer guess may own a real suffix behind an invalid prefix.
            # Follow only the cleanup/epilogue's reachable instructions, not
            # every instruction the previous speculative owner happened to own.
            insns = []
            if target not in fn.addrs:
                if in_span:
                    if not fn.addr <= target < span_end or target in stubs:
                        return False
                    # Existing fragments in the span are not new function
                    # boundaries. Follow normal edges through them, while the
                    # handler stubs retain their separate dispatcher entries.
                    lo, hi = bisect_right(candidate_starts, target), bisect_right(candidate_starts, span_end)
                    boundaries = (set(candidate_starts[lo:hi]) - fn.addrs) if guessed else ()
                    insns = image.recover(target, stubs, bounds=(fn.addr, span_end),
                                          boundaries=boundaries)
                    candidate = Function(target, "continuation", 0, insns)
                    candidate.measure(image)
                    if not insns or not accepts(candidate):
                        return False
                    if guessed and fn in original_instructions:
                        key = (fn, target)
                        span_guesses[key] = candidate
                        for addr in candidate.addrs:
                            span_guess_instructions[addr].add(key)
                else:
                    blocked = set(owner) - prior.addrs if prior is not None else owner
                    insns = image.recover(target, blocked)
                    if not insns:
                        return False
                merged = {ins.addr: ins for ins in fn.insns}
                merged.update({ins.addr: ins for ins in insns})
                grown = Function(fn.addr, fn.name, fn.size, [merged[a] for a in sorted(merged)])
                grown.measure(image)
                fn.__dict__.update(grown.__dict__)
            adopted = bool(insns)
            # An overlapping body may have been independently recovered even
            # when these instructions are already in the establishing body.
            # Retire that definition as well as preserving its alternate entry.
            priors = {prior, bodies.get(target)}
            priors.update(owner.get(ins.addr) for ins in insns)
            for prior in priors - {None, fn}:
                # Preserve independently named entries as wrappers into the
                # establishing body, including the exception path's CALL.
                for addr, body in list(extra.items()):
                    if body is prior and addr in fn.addrs:
                        extra[addr] = fn
                if (prior.addrs <= fn.addrs
                        and provenance.get(prior.addr) not in NAMED_PROVENANCE):
                    parsed.remove(prior)
                    bodies.pop(prior.addr, None)
                    retired_finally_bodies.add(prior)
                    if prior in recovered:
                        recovered.remove(prior)
                    extra[prior.addr] = fn
                    finally_owners[prior.addr] = fn
                    adopted = True
                for addr in prior.addrs & fn.addrs:
                    if owner.get(addr) is prior:
                        owner[addr] = fn
            for ins in insns:
                owner[ins.addr] = fn
            if insns:
                register(fn)
            # Pushed epilogues share this ownership rule with cleanup entries.
            # A handler that also names the epilogue must not recreate the
            # standalone body we just retired on every discovery round.
            finally_owners[target] = fn
            return adopted

        if span_end is not None:
            # A pushed address in the original span can name an omitted
            # continuation. Repeated discovery rounds find further PUSHes in
            # the attached blocks, reaching the complete cleanup chain.
            targets = set()
            for ins in fn.insns:
                if ins.mnem == "PUSH" and ins.ops:
                    op = parse_operand(ins.ops[0])
                    if op.kind == "imm" and fn.addr <= op.imm < span_end:
                        targets.add(op.imm)
            for target in sorted(targets - stubs):
                changed |= adopt(target, in_span=True, guessed=True)
            # The normal path can join the suffix of an except landing before
            # reaching a finally cleanup. Seed those in-span landings into the
            # same owner too, retaining their callable alternate entries.
            for stub in sorted(stubs):
                got = image.seh_landings_opt(stub)
                if got is None:
                    continue
                landings, _ = got
                for landing in landings:
                    if fn.addr <= landing < span_end:
                        changed |= adopt(landing, in_span=True)
                        if landing in fn.addrs:
                            extra[landing] = fn
                            if landing not in all_addrs:
                                all_addrs.add(landing)
                                changed = True

        for stub in seh_frame_sites(fn, image).values():
            got = image.seh_landings_opt(stub)
            if got is None:
                continue
            landings, table_range = got
            if table_range or landings != [stub + 5]:
                continue
            landing = image.instruction_at(stub + 5)
            if landing is None or landing.mnem != "JMP":
                continue
            cleanup = Translator.branch_target(landing)
            if cleanup is None:
                continue
            # Require a normal fall-through or direct jump from this body;
            # exception-only handlers remain separately recovered blocks.
            normal = any((ins.mnem == "JMP" and Translator.branch_target(ins) == cleanup)
                         or (ins.mnem not in TERMINATORS and not tr.never_returns(ins)
                             and fn.fallthrough[i] == cleanup)
                         for i, ins in enumerate(fn.insns))
            if not normal:
                continue
            continuations = set()
            for i, ins in enumerate(fn.insns):
                if ins.mnem != "PUSH" or not ins.ops:
                    continue
                nxt = fn.fallthrough[i]
                following = next((x for x in fn.insns if x.addr == nxt), None)
                if nxt != cleanup and not (following is not None and following.mnem == "JMP"
                                           and Translator.branch_target(following) == cleanup):
                    continue
                op = parse_operand(ins.ops[0])
                if op.kind == "imm" and operand_size([op], hint=32) == 32:
                    continuations.add(op.imm & 0xffffffff)
            changed |= adopt(cleanup)
            for target in sorted(continuations):
                changed |= adopt(target)
            # Both normal flow and the handler stub establish this ownership.
            # Preserve it when the SEH resolver encounters the same address;
            # it must not split this body again as a speculative pointer guess.
            if cleanup in fn.addrs:
                # Keep the shared normal/exception entry in this body without
                # letting its SEH content protect a speculative body from
                # pruning. A later independently recovered owner can retain
                # the cleanup when an overlapping invalid prefix withdraws.
                finally_owners[cleanup] = fn
                owner[cleanup] = fn
                note_structural(provenance, {cleanup: fn} if fn.addr in protected_entries else {},
                                cleanup, "seh")
                extra[cleanup] = fn
                if cleanup not in all_addrs:
                    all_addrs.add(cleanup)
                    changed = True
        return changed

    # Seed before branch recovery can claim fragments of these functions.
    # The later __initterm pass uses the same resolver, but by then recovery
    # stops at owned instructions and cannot reconstruct a whole missing body.
    for addr in sorted(EXTRA_ENTRY_POINTS):
        if not image.is_exec(addr):
            raise TranslateError("[translate] entry_points: %08x is not in a code section" % addr)
        # Its whole body: fragments recovered elsewhere must not cut a named
        # function short. Entries are seeded in address order, so a thunk
        # named with its target validates against an entry that exists.
        if not resolve(addr, set(), why="config"):
            # Saying nothing here is how a declared entry point goes missing:
            # the run that needed it still calls an address the translation
            # does not carry, and the only symptom is a null call much later.
            print("  entry_points: %08x was NOT adopted as a function "
                  "(already owned, or its recovery did not validate)" % addr,
                  file=sys.stderr)

    scanned_pointers = False
    seh_stubs = set()
    converged = False
    for _round in range(64):
        tr.func_addrs = all_addrs
        tr.all_insn_addrs = set(owner)
        tr.jumptables.clear()
        tr.unlisted_targets.clear()
        for fn in parsed:
            try:
                tr.prepare(fn, strict=False)
            except TranslateError:
                pass                      # reported by the strict pass below
        listed = set(owner)
        changed = False
        for fn in list(parsed):
            if fn not in retired_finally_bodies:
                changed |= extend_finally_body(fn)
        listed = set(owner)
        # Seed omitted exception blocks before ordinary branch/immediate
        # discovery can claim their fragments. Use the same resolver as
        # explicit configuration entries, including alternate entries into a
        # listed body. Table bytes must stay out of heuristic pointer scans.
        for fn in list(parsed):
            if fn in retired_finally_bodies:
                continue
            for stub in seh_frame_sites(fn, image).values():
                if stub in seh_stubs:
                    continue
                seh_stubs.add(stub)
                # The handler is a function whatever its shape: MSVC's is an
                # ordinary routine the prologue pushes. Adopt it first, then
                # look for a Delphi landing block behind it.
                changed |= resolve(stub, set(owner), why="immediate")
                got = image.seh_landings_opt(stub)
                if got is None:
                    continue
                landings, table_range = got
                if table_range:
                    tr.table_ranges.add(table_range)
                for landing in landings:
                    changed |= resolve(landing, set(owner), why="seh")
        for fn in list(parsed):
            if fn in retired_finally_bodies:
                continue
            for i, ins in enumerate(fn.insns):
                # PUSH imm32 / RET names a continuation just as a direct JMP
                # does; do not depend on heuristic pointer discovery for it.
                changed |= resolve(tr.push_ret_target(fn, i), listed, fn)
                # Direct CALL targets are followed too.  Every one in the
                # Ghidra corpus was already a listed function, but code found
                # by the data-pointer scan calls functions Ghidra never listed
                # either: 0049e800 calls 004c3110 and 004c31e0.
                if ins.mnem in ("JMP", "CALL") or ins.mnem in JCC:
                    changed |= resolve(Translator.branch_target(ins), listed, fn,
                                       continuation=ins.mnem != "CALL")
                if fn in retired_finally_bodies or i >= len(fn.insns) or fn.insns[i] is not ins:
                    break  # a stronger target replaced this speculative suffix
                if ins.mnem in TERMINATORS or tr.never_returns(ins):
                    continue
                if not fn.contiguous[i]:
                    changed |= resolve(fn.fallthrough[i], listed, fn)
            if fn.insns[-1].mnem not in TERMINATORS and not tr.never_returns(fn.insns[-1]):
                changed |= resolve(fn.fallthrough[-1] or fn.end, listed, fn)
        for t in sorted(tr.unlisted_targets):
            changed |= resolve(t, listed, why="table")
        # And from the tables directly, which is the only way targets that were
        # already known get promoted: those never enter unlisted_targets.
        for _targets in tr.jumptables.values():
            for t in _targets:
                note_structural(provenance, owner, t, "table")
        if not changed and not scanned_pointers:
            # Ghidra's list misses functions that are only ever reached through
            # a pointer loaded from data.  Scan the image for those once the
            # listed code has settled, then let the same loop follow whatever
            # they call and fall into.
            scanned_pointers = True
            before = len(all_addrs)
            # The CRT's static initializers first, and unconditionally: the
            # call site to __initterm names the table's bounds outright, so
            # every non-null pointer in it is an entry point by construction
            # and no heuristic gets a say.  21 of these were being dropped by
            # the string-tail test, leaving their C++ globals zero.
            init_ranges, init_entries = image.initterm_tables(parsed)
            initterm_tables[:] = init_ranges
            for t in sorted(init_entries):
                # The evidence first, and unconditionally: the CRT's table
                # names this address whether or not this is the pass that adds
                # it, and eligibility must not depend on that.
                hook_evidence[t].add("initterm")
                # Through resolve, which handles an address already covered by
                # a known body and, either way, records the provenance that
                # keeps it out of the pruning pass.
                if resolve(t, listed, why="initterm"):
                    changed = True
                    initterm_found[0] += 1
            # Addresses inside a decoded jump table are table storage, not
            # code: 0x4422c0 is 16-aligned and follows a RET, so it passes
            # every static signal there is.
            # Addresses that only ever appear as an instruction's immediate.
            # `atexit(00567570)` pushes its handler and nothing else in the
            # image names it, so neither the data scan nor call following sees
            # it and the handler is skipped at shutdown.
            immediates = set()
            for fn in parsed:
                for ins in fn.insns:
                    if ins.mnem not in ("PUSH", "MOV") or not ins.ops:
                        continue
                    try:
                        src = parse_operand(ins.ops[-1])
                    except TranslateError:
                        continue
                    if src.kind != "imm" or not image.is_exec(src.imm):
                        continue
                    if ins.mnem == "MOV":
                        try:
                            dst = parse_operand(ins.ops[0])
                        except TranslateError:
                            continue
                        if dst.kind != "reg" or dst.size != 32:
                            continue
                    immediates.add(src.imm)
            # Collect all scan candidates against the settled protected code
            # before admitting any of them. Otherwise an earlier guess's
            # interior-byte coverage can suppress a later equal-rank entry.
            starts, interior = image.code_pointers(
                set(owner), interior_bytes=interior_bytes, exclude=tr.table_ranges)
            reloc_candidates = {t for t, slot in relocated.items()
                                if image.is_exec(t)
                                and not any(lo <= t < hi or lo <= slot < hi
                                            for lo, hi in tr.table_ranges)
                                and (t in owner or image.plausible_immediate_target(t))}
            immediate_candidates = {t for t in immediates
                                    if not any(lo <= t < hi for lo, hi in tr.table_ranges)
                                    and (t in owner or image.plausible_immediate_target(t))}
            # A raw dword hit inside a pointer-named routine's instruction
            # is weaker evidence than that routine and its direct callees.
            # Follow validated CALL edges before guesses become boundaries:
            # a callee's address need not have a relocation of its own.
            # These bodies stay speculative and can still be withdrawn.
            weaker_interiors = set()
            guesses = ((starts | interior | immediate_candidates)
                       - set(relocated) - protected_entries)
            pending_probes = sorted(reloc_candidates, reverse=True)
            probed = set()
            while pending_probes:
                t = pending_probes.pop()
                if t in probed:
                    continue
                probed.add(t)
                if (t in owner or image.starts_with_utf16_run(t) or image.is_utf16_constant(t)
                        or image.data[t - image.base:t - image.base + 2] == b"\0\0"):
                    continue
                probe = image.recover(t, owner)
                if not probe:
                    continue
                fn = Function(t, "relocated_candidate", 0, probe)
                fn.measure(image)
                if not accepts(fn):
                    continue
                if t not in reloc_candidates:
                    pointer_callees.add(t)
                for ins in probe:
                    target = Translator.branch_target(ins) if ins.mnem == "CALL" else None
                    if target is not None and image.is_exec(target) and target not in probed:
                        pending_probes.append(target)
                # A weaker hit at a real instruction boundary is callable,
                # but is an alias of this stronger body, not a new function
                # boundary. In particular, a bare pointer to its RET must not
                # withdraw the method by stopping it one instruction early.
                scan_aliases.update(fn.addrs & guesses)
                for i, ins in enumerate(probe):
                    end = fn.fallthrough[i] or ins.addr + 1
                    weaker_interiors.update(a for a in range(ins.addr + 1, end) if a in guesses)
            starts -= weaker_interiors
            interior -= weaker_interiors
            immediate_candidates -= weaker_interiors
            image.interior_candidates.update(weaker_interiors)
            for t in sorted((starts | interior | reloc_candidates | pointer_callees
                             | immediate_candidates) - scan_aliases):
                if (image.starts_with_utf16_run(t) or image.is_utf16_constant(t)
                        or image.data[t - image.base:t - image.base + 2] == b"\0\0"):
                    continue
                remember_candidate(t)
            # Establish stronger owners before processing their weaker aliases.
            for t in sorted(reloc_candidates):
                hook_evidence[t].add("reloc")
                changed |= resolve(t, owner, why="data")
            for t in sorted(pointer_callees):
                changed |= resolve(t, owner)
            for t in sorted(immediate_candidates):
                hook_evidence[t].add("immediate")
                if resolve(t, owner, why="immediate"):
                    changed = True
                    immediate_entries[0] += 1

            # A scan hit is evidence of nothing on its own.  It becomes
            # evidence when the same address is the value of a dword the
            # loader relocates, which is the linker saying that dword is a
            # pointer.  Recorded for both sets, before either is resolved.
            for t in starts | interior:
                if t in relocated:
                    hook_evidence[t].add("reloc")
            for t in sorted(starts):
                changed |= resolve(t, listed, why="data")
            # A pointer to an address that already is an instruction boundary
            # names a place the program can be entered, so it gets an
            # alternate entry rather than being dropped.  It came out of the
            # same data-pointer scan as `starts`, so it carries the same
            # provenance: the program names it, which is what makes it a
            # stable hook target rather than an internal block.
            for t in sorted(interior):
                if resolve(t, listed, why="data"):
                    changed = True
                    interior_entries[0] += 1
            discovered_by_scan[0] = (len(all_addrs) - before - interior_entries[0]
                                     - initterm_found[0] - immediate_entries[0])
            if not changed:
                converged = True
                break
        elif not changed:
            converged = True
            break
    if not converged:
        raise TranslateError(
            "entry-point discovery did not converge in 64 rounds; %d entry "
            "points and %d recovered blocks so far"
            % (len(all_addrs), len(recovered)))
    parsed.sort(key=lambda f: f.addr)

    # final, strict jump-table decode (stats from the discovery rounds are
    # discarded so the report counts each table once)
    for k in [k for k in tr.stats if k.startswith("_jmp_table")]:
        del tr.stats[k]
    tr.func_addrs = all_addrs
    tr.all_insn_addrs = set(owner)
    tr.jumptables.clear()
    # Discovery can truncate a speculative owner after recording its switch.
    # Rebuild both sides of the coverage check from the final function bodies.
    tr.table_sites.clear()
    tr.table_sites_inferred.clear()
    tr.unlisted_targets.clear()
    for fn in parsed:
        try:
            tr.prepare(fn, strict=True)
        except TranslateError as e:
            # The same tolerance the emit path has. A function whose jump
            # table cannot be decoded because one of its instructions cannot
            # be modelled is the padding case again: the instruction itself
            # already becomes a trap, so failing the build here only hides
            # that. The site stays in table_sites, so the coverage check still
            # reports the table nobody decoded.
            if tr.allow_unmodelled:
                tr.unmodelled.append((fn.addr, "jump table: %s" % e))
                continue
            failures.append((fn.addr, "jump table: %s" % e))
    tr.discover_seh_helpers(parsed)

    # Plan correction 9: every decoded jump-table target inside a function is a
    # block entry, so recomp_jump can reach it.
    for (fn_addr, _at), targets in tr.jumptables.items():
        for t in targets:
            # This is the last word on which addresses the tables name, and it
            # runs after discovery, so promote here too: the withdraw pass
            # below reads provenance and must not drop a live target.
            note_structural(provenance, owner, t, "table")
            if t not in all_addrs and t in owner:
                extra[t] = owner[t]
                all_addrs.add(t)

    def emitter_refuses(fn):
        """The vocabulary check alone, without `accepts`'s fall-out rule.

        An alternate is an entry into a body that continues past it, so a
        block decoded from one falls into the rest of that body by design and
        the fall-out rule would condemn every real alternate. What is still
        decisive is the emitter refusing the instructions themselves."""
        notes, stats, strict = len(tr.notes), dict(tr.stats), tr.strict
        try:
            tr.prepare(fn)
            tr.translate(fn)
            return False
        except TranslateError:
            return True
        except Exception:                       # noqa: BLE001
            return True
        finally:
            del tr.notes[notes:]
            tr.stats.clear()
            tr.stats.update(stats)
            tr.strict = strict

    def judge_literal(t):
        """Is the pointer at `t` naming data rather than a function?"""
        if image.is_utf16_constant(t) or image.starts_with_utf16_run(t):
            return "a UTF-16 literal"
        if image.starts_with_ascii_run(t):
            return "a narrow string literal"
        # What the decoder itself refuses: bytes that do not decode at all,
        # and the instructions no userland function opens with. This does NOT
        # catch 16-bit addressing - ADD byte ptr [BX + DI],CH decodes cleanly
        # and becomes an Insn without complaint, and it is the emitter below
        # that refuses the operand.
        bad = image.undecodable_run(t)
        if bad:
            return bad
        # No limit: recover() discards a recovery its limit truncated, so a
        # small one would report every alternate inside a long function as
        # undecodable and drop it. And nothing decoded is no opinion, not a
        # verdict - only positive evidence drops an entry.
        block = image.recover(t, set())
        if not block:
            return None
        fn = Function(t, "alternate_%08x" % t, block[-1].addr + 1 - t, block)
        fn.measure(image)
        if emitter_refuses(fn):
            return "instructions this compiler never emits"
        for ins in block:
            if ins.mnem not in ("CALL", "JMP") and not ins.mnem.startswith("J"):
                continue
            target = tr.branch_target(ins)
            if target is not None and not (image.base <= target < image.end):
                return "a direct transfer to %08x, outside the image" % target
        return None

    # Decided once per address, and before the first translation pass: a
    # failure there is final, so an answer that arrives later - as it did when
    # only the drop below asked - cannot take back what the literal's bytes
    # already did to the body that holds them. The first pass asks it too,
    # through Translator.live_continuations, and so does the span pass.
    literal_verdicts = {}

    def points_at_a_literal(t):
        if t not in literal_verdicts:
            # Judging translates a trial block, which can ask again; an
            # address still being judged has no verdict yet, and no verdict
            # is no opinion.
            literal_verdicts[t] = None
            literal_verdicts[t] = judge_literal(t)
        return literal_verdicts[t]

    tr.points_at_a_literal = points_at_a_literal

    # A pushed continuation is a block entry too. Delphi leaves a finally
    # block with PUSH continuation; ...; POP reg; JMP reg, and that jump
    # dispatches on a variable - not necessarily from the body that decoded
    # the continuation. A recovered block can share the same code: in Siege
    # of Avalon 1.19 body_0080da23 runs over FUN_0080d58c's grown tail, holds
    # the JMP at 0x0080e3a0, and has no label for 0x0080e3a9, so its switch
    # missed and recomp_jump found no entry. Registered here, the address is
    # reachable from every body that can execute that jump.
    for fn in parsed:
        insns = fn.insns
        if not any(insns[k].mnem == "POP" and insns[k + 1].mnem == "JMP"
                   and insns[k].ops and insns[k + 1].ops
                   and insns[k].ops[0] == insns[k + 1].ops[0]
                   for k in range(len(insns) - 1)):
            continue
        for k, ins in enumerate(insns):
            if ins.mnem != "PUSH" or not ins.ops:
                continue
            nxt = insns[k + 1] if k + 1 < len(insns) else None
            if nxt is not None and nxt.mnem == "PUSH" and nxt.ops and "FS:" in nxt.ops[0]:
                continue  # a try frame's handler, recovered as a landing
            try:
                op = parse_operand(ins.ops[0])
            except TranslateError:
                continue
            t = op.imm if op.kind == "imm" else None
            if (t is not None and t not in all_addrs and t in owner
                    and not points_at_a_literal(t)):
                extra[t] = owner[t]
                all_addrs.add(t)

    # An alternate entry named only by a stored pointer is not control flow.
    #
    # A relocated dword, or an instruction immediate that happens to hold an
    # address, says the program keeps a pointer there. It does not say the
    # pointer is to code, and Delphi keeps string literals and interface GUIDs
    # in .text among its code, so a .text pointer is as often a literal as a
    # callback. The withdraw pass below judges standalone guesses; an
    # alternate absorbed into a listed body never faced it at all, whatever
    # its provenance, so a pointer into the middle of a literal stayed an
    # entry point and kept the bytes behind it alive as code.
    #
    # Real control flow still wins, as starts_with_utf16_run says it must: a
    # direct call or jump, a jump table, an __initterm entry, a configured
    # entry and an SEH landing all name code, and none is dropped here. Only
    # an address with pointer evidence and nothing else is asked to look like
    # code as well as be pointed at.
    def flows_into(home, t):
        """Does the body reach `t` without being entered there?

        Then the bytes at `t` are code whatever they look like: the body runs
        through them. Siege of Avalon's literal is not reached - it sits behind
        a call to _Halt0 - and a listing that runs straight into text-like
        bytes is."""
        home.index = {ins.addr: k for k, ins in enumerate(home.insns)}
        home.pushed_continuations = tr.live_continuations(home)
        others = {a for a, f in extra.items() if f is home and a != t}
        return home.index.get(t) in tr.reached(home, {home.addr} | others)

    pointer_only = {"reloc", "immediate"}
    literals = []
    grown_literals = []
    for t in sorted(extra):
        marks = set(hook_evidence.get(t, ()))
        if not marks or not marks <= pointer_only:
            continue
        if (provenance.get(t, "branch") in STRUCTURAL_PROVENANCE
                or t in listed_functions or t in protected_entries):
            continue
        why_data = points_at_a_literal(t)
        if why_data and not flows_into(extra[t], t):
            literals.append((t, extra[t].addr, why_data))
            del extra[t]
    tr.stats["_alternate_literals_dropped"] = len(literals)
    if literals and not args.quiet:
        print("  dropped %d alternate entr%s a pointer named and nothing else: %s"
              % (len(literals), "y" if len(literals) == 1 else "ies",
                 ", ".join("%08x (in %08x, %s)" % row for row in literals[:12])))

    # A guest task may save ESP and RET into another task's suspended CALL.
    # Export each real continuation so the outer execution loop can resume it
    # after mismatched returns have unwound the native call stack.
    if RESUMABLE_STACKS:
        starts = {f.addr for f in parsed}
        for fn in parsed:
            for i, ins in enumerate(fn.insns):
                if ins.mnem == "CALL" and not tr.never_returns(ins):
                    nxt = fn.fallthrough[i]
                    if nxt in owner and nxt not in starts:
                        extra[nxt] = owner[nxt]

    entries_by_fn = defaultdict(set)
    for t, fn in extra.items():
        entries_by_fn[fn.addr].add(t)

    bodies = {}
    for fn in parsed:
        if fn.addr in [a for a, _ in failures]:
            continue
        try:
            bodies[fn.addr] = tr.translate(fn, entries_by_fn.get(fn.addr, ()))
        except TranslateError as e:
            failures.append((fn.addr, str(e)))
        except Exception as e:              # noqa: BLE001 - report, never crash the build
            failures.append((fn.addr, "%s: %s" % (type(e).__name__, e)))

    tr.cross_check_switches([fn for fn in parsed if fn.addr in bodies])

    # A block recovered by the sweep whose own dispatch targets do not exist
    # was never code: real code calls real code.  Dropping such a block can
    # orphan whoever called it, so this repeats until it settles.  Blocks that
    # came from Ghidra's listing are never dropped - a dangling target there is
    # a translator gap, and the invariant below turns it into a build failure.
    fn_ref = re.compile(r"FN\(([0-9a-f]{8})\)")

    def dangling_targets(body, known):
        """Literal dispatch targets in this body that are not entry points.

        Both spellings count: `recomp_call`/`recomp_jump` with a constant, and
        a direct `FN(ADDR)` reference, which is a link error rather than a
        runtime one if the entry has gone."""
        out = []
        for line in body:
            for kind in ("recomp_call", "recomp_jump"):
                at = line.find(kind + "(c, 0x")
                if at < 0:
                    continue
                lit = line[at + len(kind) + 6:].split("u")[0]
                try:
                    target = int(lit, 16)
                except ValueError:
                    continue
                if target not in known and not (GUEST_SHIM_BASE <= target
                                                < GUEST_SHIM_END):
                    out.append((target, line.strip()))
            for hit in fn_ref.findall(line):
                target = int(hit, 16)
                if target not in known:
                    out.append((target, line.strip()))
        return out

    # Entries the emitter had to drop are not entry points either.
    for t in list(extra):
        if t in tr.stale_entries:
            del extra[t]

    # Structural provenance is never withdrawn; a guess may be.  See
    # STRUCTURAL_PROVENANCE for why the line is drawn there.
    withdrawn = []
    recovered_addrs = prunable_blocks({f.addr for f in recovered}, provenance)
    pruned = []
    if forget:
        for a in sorted(forget):
            if a in bodies:
                del bodies[a]
            pruned.append(a)
        for t in [t for t, f in extra.items() if f.addr in forget]:
            del extra[t]
    while True:
        known = set(bodies) | set(t for t in extra if extra[t].addr in bodies)
        drop = [a for a in bodies
                if a in recovered_addrs and dangling_targets(bodies[a], known)]
        if not drop:
            break
        for a in drop:
            why = dangling_targets(bodies[a], known)
            withdrawn.append((a, provenance.get(a, "branch"),
                              why[0][0] if why else 0))
            del bodies[a]
            pruned.append(a)
        for t in [t for t, f in extra.items() if f.addr in drop]:
            del extra[t]


    # A speculative owner stays prunable even when it covers a real cleanup.
    # Its removal must not erase a dispatch still required by protected code.
    # Reclaim only the reachable suffix through the original listed span, not
    # the rejected owner's prefix. Newly attached code can expose another
    # cleanup, pushed continuation, or callee, so drain a worklist to a fixpoint.
    span_functions = {fn.addr: fn for fn in parsed
                      if fn.addr in listed_functions and fn.addr in bodies}
    known = set(bodies) | {t for t, fn in extra.items() if fn.addr in bodies}

    def span_owner(target):
        i = bisect_right(listed_starts, target) - 1
        if i < 0:
            return None
        start = listed_starts[i]
        return span_functions.get(start) if target < span_ends[start] else None

    def required_targets(fn):
        targets = {t for t, _ in dangling_targets(bodies[fn.addr], known)}
        home = span_owner(fn.addr)
        if home is not None:
            for ins in fn.insns:
                if ins.mnem == "PUSH" and ins.ops:
                    op = parse_operand(ins.ops[0])
                    if op.kind == "imm" and home.addr <= op.imm < span_ends[home.addr]:
                        if op.imm not in withdrawn_span_guesses:
                            targets.add(op.imm)
        for stub in seh_frame_sites(fn, image).values():
            targets.add(stub)
            got = image.seh_landings_opt(stub)
            if got is None:
                continue
            landings, table_range = got
            targets.update(landings)
            seh_stubs.add(stub)
            if table_range:
                tr.table_ranges.add(table_range)
        # A pushed continuation. Delphi leaves a finally block with
        # `PUSH continuation; ...; POP EAX; JMP EAX`, and the jump is emitted
        # as a dispatch on a variable, so the continuation is never a literal
        # dangling target - and when the listing stopped short of it, nothing
        # else names it either. Recursive descent cannot follow a push. An
        # in-window code address a body pushes and does not contain is that
        # body's own continuation, and it grows into it like any other.
        # Only the idiom, exactly: the body must consume a pushed address with
        # `POP reg; JMP reg`, and a push whose next instruction pushes FS:[..]
        # is a try frame's handler, which has its own recovery above.
        consumes = any(fn.insns[k].mnem == "POP" and fn.insns[k + 1].mnem == "JMP"
                       and fn.insns[k].ops and fn.insns[k + 1].ops
                       and fn.insns[k].ops[0] == fn.insns[k + 1].ops[0]
                       for k in range(len(fn.insns) - 1))
        window_end = span_ends.get(fn.addr, fn.end + 0x10000)
        for k, ins in enumerate(fn.insns):
            if not consumes or ins.mnem != "PUSH" or not ins.ops:
                continue
            nxt = fn.insns[k + 1] if k + 1 < len(fn.insns) else None
            if nxt is not None and nxt.mnem == "PUSH" and nxt.ops and "FS:" in nxt.ops[0]:
                continue
            try:
                op = parse_operand(ins.ops[0])
            except TranslateError:
                continue
            if (op.kind == "imm" and fn.addr < op.imm < window_end and image.is_exec(op.imm)
                    and op.imm not in fn.addrs and op.imm not in tr.all_insn_addrs):
                targets.add(op.imm)
        return targets - known

    pending = set()
    for fn in parsed:
        if fn.addr in bodies and (fn.addr in protected_entries
                                  or provenance.get(fn.addr) in STRUCTURAL_PROVENANCE):
            pending.update(required_targets(fn))
    attempted = set()
    span_recovered = set()
    while pending:
        target = min(pending)
        pending.remove(target)
        if target in known or target in attempted:
            continue
        attempted.add(target)
        home = span_owner(target)
        if home is None:
            continue  # keep the existing gate failure when there is no owner
        protected_entries.add(target)
        entry_strength[target] = max(entry_strength.get(target, 0), 2)
        if target in home.addrs:
            grown = home
        else:
            # Existing entries and handler stubs keep their dispatch identity;
            # withdrawn bodies and aliases are no longer sweep boundaries.
            blocked = (known | seh_stubs | home.addrs) - {target}
            insns = image.recover(target, blocked,
                                  bounds=(home.addr, span_ends[home.addr]))
            if not insns:
                continue
            candidate = Function(target, "span continuation", 0, insns)
            candidate.measure(image)
            if not accepts(candidate):
                continue
            # The same question the alternate rule above asks, asked again
            # here because this is where the answer is acted on: this pass
            # grows a listed body with the bytes at `target` and then makes
            # `target` an alternate entry of it. Dropping the entry earlier
            # achieves nothing if this puts it straight back and brings the
            # bytes with it - which is what happened to Siege of Avalon, where
            # a pointer into L"kernel32.dll" was dropped and re-adopted in the
            # same run.
            literal = points_at_a_literal(target)
            if literal:
                grown_literals.append((target, home.addr, literal))
                continue
            # A clean decode cannot replace bytes already owned by the listing.
            ends = {ins.addr: home.fallthrough[i] or ins.addr + 1
                    for i, ins in enumerate(home.insns)}
            starts = sorted(ends)
            overlap = False
            for i, ins in enumerate(insns):
                end = candidate.fallthrough[i] or ins.addr + 1
                pos = bisect_right(starts, ins.addr)
                if ((pos and ends[starts[pos - 1]] > ins.addr)
                        or (pos < len(starts) and starts[pos] < end)):
                    overlap = True
                    break
            if overlap:
                continue
            merged = {ins.addr: ins for ins in home.insns}
            merged.update({ins.addr: ins for ins in insns})
            grown = Function(home.addr, home.name, home.size,
                             [merged[a] for a in sorted(merged)])
            grown.measure(image)
        aliases = {a for a, fn in extra.items() if fn is home} | {target}
        tr.func_addrs.add(target)
        tr.all_insn_addrs.update(grown.addrs)
        try:
            tr.prepare(grown, strict=True)
            body = tr.translate(grown, aliases)
        except TranslateError:
            continue
        home.__dict__.update(grown.__dict__)
        extra[target] = home
        owner[target] = home
        finally_owners[target] = home
        register(home)
        bodies[home.addr] = body
        known.add(target)
        all_addrs.add(target)
        span_recovered.add(target)
        # Table entries introduced by the recovered suffix use the same gate
        # and span recovery as its explicit dispatches.
        for (addr, _), targets in tr.jumptables.items():
            if addr == home.addr:
                pending.update(set(targets) - known)
        pending.update(required_targets(home))
    tr.stats["_span_recovered_after_pruning"] = len(span_recovered)
    tr.stats["_span_continuations_into_data"] = len(grown_literals)
    tr.stats["_literal_judgements"] = len(literal_verdicts)
    tr.stats["_literal_verdicts"] = sum(1 for v in literal_verdicts.values() if v)
    tr.stats["_literal_continuations"] = len(tr.literal_continuations)
    tr.stats["_dead_after_noreturn"] = len(tr.dead_after_noreturn_addrs)
    if not args.quiet:
        print("  literal judgements: %d addresses asked, %d data; %d pushed continuation%s "
              "refused; %d instruction%s behind calls that never return dropped"
              % (len(literal_verdicts), tr.stats["_literal_verdicts"],
                 len(tr.literal_continuations), "" if len(tr.literal_continuations) == 1 else "s",
                 tr.stats["_dead_after_noreturn"],
                 "" if tr.stats["_dead_after_noreturn"] == 1 else "s"))
    if grown_literals and not args.quiet:
        print("  refused %d span continuation%s into data: %s"
              % (len(grown_literals), "" if len(grown_literals) == 1 else "s",
                 ", ".join("%08x (in %08x, %s)" % row for row in grown_literals[:12])))
    if pruned:
        gone = set(pruned) - known
        for a in bodies:
            bodies[a] = retarget_withdrawn(bodies[a], gone)
        tr.stats["_withdrawn_retargeted"] = len(gone)

    ok = [fn for fn in parsed if fn.addr in bodies]
    if args.as_module and (args.only or discovered):
        # Discovery ran over the whole image, as it must to recover a function
        # whole, but a module carries only the functions it was asked for:
        # --only, or the addresses a run found (--discovered), which is the
        # ordinary case - a listing does not name them, so only discovery
        # reaches them. What they call is the rest of the image, which the
        # image's own table answers; dispatch_outside() turns those literal
        # transfers into recomp_call and recomp_jump below.
        seeds = {int(a, 16) for a in args.only} if args.only else set(discovered)
        ok = [fn for fn in ok if fn.addr in seeds]
        bodies = {a: b for a, b in bodies.items() if a in seeds}
        extra = {t: f for t, f in extra.items() if f.addr in seeds}
    entry_names = sorted(set([fn.addr for fn in ok])
                         | set(t for t, f in extra.items() if f.addr in bodies))
    if args.as_module:
        # A module carries a few of the image's functions, not all of them.
        carried = set(entry_names)
        for a in bodies:
            bodies[a] = dispatch_outside(bodies[a], carried)

    # Table coverage, in two parts, both of them sound.  Every entry a table
    # decoded has to dispatch somewhere, and every statically based table site
    # inside the image has to decode a table at all - a site that decodes nothing
    # is the worst kind of gap and emits a `recomp_jump` on a computed target,
    # which no literal check can see.  Guessing at a table's real extent by
    # reading past what was decoded is not included: it flags whatever data
    # happens to follow a short table, and it disagreed with the decode's own
    # stopping rule on four sites.
    known_entries = set(bodies) | set(t for t in extra if extra[t].addr in bodies)
    table_gaps = []
    undecoded = [(f, a, b) for (f, a), b in sorted(tr.table_sites.items())
                 if f in bodies and (f, a) not in tr.jumptables]
    for fn_addr, at, base in undecoded:
        print("  jump table at %08x (in fn_%08x) reads %08x but decoded no "
              "entries at all" % (at, fn_addr, base), file=sys.stderr)
    for (fn_addr, at), targets in sorted(tr.jumptables.items()):
        if fn_addr not in bodies:
            continue
        missing = {t for t in targets if t not in known_entries}
        if missing:
            table_gaps.append((fn_addr, at, len(targets), sorted(missing)))
    for fn_addr, at, total, missing in table_gaps:
        print("  jump table at %08x (in fn_%08x): %d of %d entries have no "
              "block entry, first %s"
              % (at, fn_addr, len(missing), total,
                 " ".join("%08x" % m for m in missing[:6])), file=sys.stderr)
    if (table_gaps or undecoded) and not args.allow_table_gaps:
        raise TranslateError(
            "%d jump tables have entries that dispatch nowhere (%d slots in "
            "total) and %d table sites decoded nothing; pass "
            "--allow-table-gaps with a reason to accept them"
            % (len(table_gaps), sum(len(m) for _f, _a, _t, m in table_gaps),
               len(undecoded)))

    # Emitted-text invariant: every single-entry fn_ADDR must reach ADDR
    # before it does anything else.  Recursive descent can pull in addresses
    # below the entry, and falling into whichever sorts first made
    # fn_00565e1e start on a POP that ate its caller's return address.
    wrong_entry = []
    for fn in ok:
        body = bodies[fn.addr]
        if fn.addr in INTRINSIC_BODY:
            continue                      # a one-line call into the runtime
        if body and body[0].startswith("static void body_"):
            continue                      # multi-entry form, dispatched below
        lines = [l for l in body[1:] if l.strip()]
        # Host-only ownership bookkeeping does not execute a guest instruction
        # or modify its CPU. The first guest operation must still reach ADDR.
        if lines and lines[0].strip() == "uint64_t seh_mark_ = recomp_seh_frame_mark(c);":
            lines = lines[1:]
        if not lines:
            continue
        first = lines[0].strip()
        if (first.startswith("L_%08x:" % fn.addr)
                or first == "goto L_%08x;" % fn.addr
                or ("/* %08x " % fn.addr) in first):
            continue
        wrong_entry.append((fn.addr, first[:80]))
    if wrong_entry:
        for addr, first in wrong_entry[:20]:
            print("  fn_%08x does not begin at %08x; it begins with %s"
                  % (addr, addr, first), file=sys.stderr)
        raise TranslateError(
            "%d functions do not begin executing at their own address"
            % len(wrong_entry))

    # Build-time invariant, on what is left: a `recomp_call(c, <const>)` or
    # `recomp_jump(c, <const>)` whose target is not an entry point is a
    # translator gap, not a runtime condition.  It fails at run time deep in
    # whatever it was doing - the sprintf float path corrupted the x87 stack
    # and returned with EBP = 0 - so it has to fail here instead, by name.
    known = set(entry_names)
    dangling = []
    for fn in ok:
        for target, line in dangling_targets(bodies[fn.addr], known):
            dangling.append((fn.addr, target, line))
    if dangling and args.as_module:
        # A module carries a few of the image's functions, and the calls
        # between them and the rest of it are ordinary. dispatch_outside()
        # already turned those into recomp_call/recomp_jump, which the image's
        # own table answers at run time; they are the intended spelling here
        # rather than the gap this gate looks for.
        dangling = []
    if dangling and args.allow_unmodelled:
        # The last shape of the same listing defect. Once recovery has
        # settled, a literal target that is still not an entry point is one
        # no instruction boundary anywhere agrees with - a jump decoded out
        # of the padding behind a function, landing in the middle of a real
        # instruction. Under the switch it becomes the trap a withdrawn block
        # gets, at the site that names it, rather than the end of the build.
        by_caller = {}
        for caller, target, _line in dangling:
            by_caller.setdefault(caller, set()).add(target)
        for caller, targets in by_caller.items():
            bodies[caller] = retarget_withdrawn(bodies[caller], targets)
        for caller, target, line in sorted(dangling):
            tr.unmodelled.append(
                (caller, "dispatches to %08x, which is not an entry point: %s"
                 % (target, line[:110])))
        dangling = []
    if dangling:
        # A target that Ghidra listed but that failed to translate is the
        # usual cause, so name the failures first: they are what to fix.
        for a, why in failures[:40]:
            print("  fn_%08x failed: %s" % (a, why[:160]), file=sys.stderr)
        for caller, target, line in dangling[:20]:
            print("  fn_%08x dispatches to %08x, which is not an entry point: %s"
                  % (caller, target, line[:110]), file=sys.stderr)
        raise TranslateError(
            "%d literal dispatch targets are not entry points; every direct "
            "call and jump has to reach translated code" % len(dangling))

    missing_checkpoints = sorted(set(POLL_CHECKPOINTS) - POLL_CHECKPOINTS_APPLIED)
    if missing_checkpoints:
        raise TranslateError("[translate] poll_checkpoints: no branch %s found (address or target wrong for "
                             "this executable)" % ", ".join("%08x" % a for a in missing_checkpoints))
    missing_patches = sorted(set(INSTRUCTION_PATCHES) - PATCHES_APPLIED)
    if missing_patches:
        raise TranslateError("instruction patches matched no instruction: " +
                             ", ".join("%08x" % a for a in missing_patches))

    # funcs.h ---------------------------------------------------------------
    with open(os.path.join(args.out, "funcs.h"), "w") as fh:
        fh.write("/* generated by tools/recomp/translate.py -- do not edit */\n")
        fh.write("#ifndef RECOMP_FUNCS_H\n#define RECOMP_FUNCS_H\n")
        fh.write('#include "x86.h"\n#include "intrinsics.h"\n#include "seh.h"\n')
        fh.write("/* Task 8 replacements: define FN_<addr> to a native function in a\n"
                 " * header named by RECOMP_OVERRIDE_HEADER and every direct call site,\n"
                 " * tail call and jump-table case for that address is redirected. */\n")
        fh.write("#ifdef RECOMP_OVERRIDE_HEADER\n#include RECOMP_OVERRIDE_HEADER\n#endif\n")
        fh.write("#define FN_CAT_(a, b) a##b\n#define FN_CAT(a, b) FN_CAT_(a, b)\n")
        fh.write("#define FN(a) FN_CAT(FN_, a)\n")
        fh.write("#define FIDX(a) FN_CAT(FIDX_, a)\n")
        # An unhooked call costs one acquire byte-load and a not-taken branch.
        # The load is an acquire, not a plain read, because the installer
        # publishes the pointer BEFORE the flag with a release store: the
        # acquire is what makes "flag set" imply "pointer visible" on arm64.
        P = SYMBOL_PREFIX
        fh.write("extern const uint32_t %sfunc_addrs[];\nextern uint8_t %shooked[];\n"
                 "extern RecompHookFn %shook_ptrs[];\n" % (P, P, P))
        fh.write("#ifdef RECOMP_NO_HOOKS\n"
                 "#define CALL_FN(a) do { if (recomp_profile_enabled) recomp_call(c, %sfunc_addrs[FIDX(a)]); else FN(a)(c); } while (0)\n"
                 "#else\n"
                 "#define CALL_FN(a) do {\\\n"
                 "    uint32_t i_ = FIDX(a);\\\n"
                 "    uint32_t ebp_ = recomp_frame_watch ? c->r[5] : 0u;\\\n"
                 "    if (recomp_profile_enabled) { recomp_call(c, %sfunc_addrs[i_]); break; }\\\n"
                 "    if (__builtin_expect(__atomic_load_n(&%shooked[i_],"
                 " __ATOMIC_ACQUIRE) != 0u, 0))\\\n"
                 "        %shook_ptrs[i_](c, i_);\\\n"
                 "    else FN(a)(c);\\\n"
                 "    if (recomp_frame_watch && c->r[5] != ebp_)\\\n"
                 "        recomp_frame_changed(c, %sfunc_addrs[i_], ebp_, c->r[5]);\\\n"
                 "} while (0)\n"
                 "#endif\n" % (P, P, P, P, P))
        for i, a in enumerate(entry_names):
            fh.write("#define FIDX_%08x %du\n" % (a, i))
        # Both spellings are declared: fn_ADDR because the chunk defines it,
        # and FN(ADDR) so an override header only has to #define FN_<addr> and
        # its replacement is declared here.  In the default case the two are
        # the same declaration, which C allows to repeat.
        for a in entry_names:
            fh.write("#ifndef FN_%08x\n#define FN_%08x fn_%08x\n#endif\n" % (a, a, a))
        for a in entry_names:
            fh.write("void fn_%08x(X86 *c);\nvoid FN(%08x)(X86 *c);\n" % (a, a))
        fh.write("#endif\n")

    # chunks ----------------------------------------------------------------
    nchunk = 0
    for start in range(0, len(ok), FUNCS_PER_CHUNK):
        group = ok[start:start + FUNCS_PER_CHUNK]
        path = os.path.join(args.out, "chunk_%03d.c" % nchunk)
        with open(path, "w") as fh:
            fh.write("/* generated by tools/recomp/translate.py -- do not edit */\n")
            fh.write('#include "funcs.h"\n\n')
            for fn in group:
                alt = sorted(entries_by_fn.get(fn.addr, ()))
                # The range is where the instructions lie, not a code size: a
                # block that follows a far branch spans the gap without
                # occupying it.
                fh.write("/* %s  %d insns  %08x..%08x%s */\n"
                         % (fn.name, len(fn.insns), fn.insns[0].addr,
                            fn.insns[-1].addr,
                            ("  entries: " + " ".join("%08x" % a for a in alt)) if alt else ""))
                fh.write("\n".join(bodies[fn.addr]))
                fh.write("\n\n")
        chunks.append(path)
        nchunk += 1

    # Canonical records shared by dispatch profiling and symbols.json.
    # The listing's own functions.  `parsed` is not that list here: recovered
    # blocks are appended to it as they are found, and a recovered block is not
    # a listed function.  `entries` is the functions.tsv rows themselves.
    listed = {a: n for a, n, _nb, _p in entries}
    listed.update({a: "FUN_%08x" % a for a in EXTRA_ENTRY_POINTS if a not in listed})
    alt_owner = {t: fn.addr for t, fn in extra.items()}

    functions = []
    for i, a in enumerate(entry_names):
        kind, hookable = hook_kind(a, listed, alt_owner, provenance,
                                   hook_evidence, INTRINSIC_BODY)
        if a in listed:
            name = listed[a]
        else:
            owner_addr = alt_owner.get(a)
            base = listed.get(owner_addr, "sub_%08x" % (owner_addr or a))
            name = "%s.%s_%08x" % (base, "alt" if kind == "alternate" else "blk", a)
        functions.append({"addr": "%08x" % a, "index": i, "name": name,
                          "kind": kind, "provenance": provenance.get(a, "listed"),
                          # Why this address is eligible, so the decision can be
                          # audited rather than taken on trust.
                          "evidence": sorted(hook_evidence.get(a, ())),
                          "hookable": hookable, "aliases": []})

    by_addr = {int(f["addr"], 16): f for f in functions}
    for alias, addr in sorted({**curated.get("entries", {}), **curated.get("aliases", {})}.items()):
        if addr in by_addr:
            by_addr[addr]["aliases"].append(alias)     # secondary, never primary

    # A computed jump to a non-entry CALL continuation returns to the pending
    # host caller. The guest already removed its return address and arguments;
    # neither another pop nor dispatching the continuation is correct here.
    call_returns = sorted({
        fn.fallthrough[i] or (fn.insns[i + 1].addr if i + 1 < len(fn.insns) else fn.end)
        for fn in ok for i, ins in enumerate(fn.insns) if ins.mnem == "CALL"
    })

    # table.c ---------------------------------------------------------------
    P = SYMBOL_PREFIX
    with open(os.path.join(args.out, "table.c"), "w") as fh:
        fh.write("/* generated by tools/recomp/translate.py -- do not edit */\n")
        fh.write('#include <stdio.h>\n#include <stdlib.h>\n#include "funcs.h"\n#include "thunks.h"\n'
                 '#include "discovery.h"\n#include "interp.h"\n\n')
        if AUX_MODULE is None:
            fh.write("/* Baseline and differential hosts retain exact guest timing. */\n"
                     "__attribute__((weak)) uint32_t recomp_visual_animation_tick(uint32_t tick) { return tick; }\n\n")
        fh.write("const uint32_t %sfunc_addrs[] = {\n" % P)
        for a in entry_names:
            fh.write("    0x%08xu,\n" % a)
        fh.write("};\n")
        fh.write("const uint32_t %sfunc_count = %d;\n\n" % (P, len(entry_names)))
        fh.write("static const uint32_t %scall_returns[] = {\n" % P)
        for a in call_returns or [0]:  # valid C storage even for a call-free image
            fh.write("    0x%08xu,\n" % a)
        fh.write("};\nstatic const uint32_t %scall_return_count = %d;\n\n" % (P, len(call_returns)))
        # Redirected operands read a new address; the loader seeds it with the
        # original value so the game behaves unchanged until something writes
        # it. Only the image carries these: an auxiliary module has no config.
        if AUX_MODULE is None:
            pairs = sorted(set(OPERAND_REDIRECTS.values()))
            fh.write("const uint32_t recomp_operand_redirect_pairs[] = {%s};\n" %
                     (", ".join("0x%08xu, 0x%08xu" % q for q in pairs) if pairs else "0u, 0u"))
            fh.write("const uint32_t recomp_operand_redirect_count = %du;\n" % len(pairs))
            fh.write("const uint32_t recomp_data_seed_pairs[] = {%s};\n" %
                     (", ".join("0x%08xu, 0x%08xu" % q for q in DATA_SEEDS) if DATA_SEEDS else "0u, 0u"))
            fh.write("const uint32_t recomp_data_seed_count = %du;\n\n" % len(DATA_SEEDS))
        fh.write("static const char *const profile_names[] = {\n")
        for symbol in functions:
            fh.write("    %s,\n" % json.dumps(symbol["name"]))
        fh.write("};\n")
        fh.write("void (*const %sbase_ptrs[])(X86 *) = {\n" % P)
        for a in entry_names:
            fh.write("    FN(%08x),\n" % a)
        fh.write("};\n\n")
        fh.write("static void %srun_base(X86 *c, uint32_t i)\n"
                 "{\n    %sbase_ptrs[i](c);\n}\n\n" % (P, P))
        # The untouched translations, beside the possibly-overridden ones: the
        # two differ exactly where this build defined an FN_<addr> override, so
        # the override set becomes a fact the binary states about itself.
        fh.write("void (*const %sraw_ptrs[])(X86 *) = {\n" % P)
        for a in entry_names:
            fh.write("    fn_%08x,\n" % a)
        fh.write("};\n\n")
        fh.write("RecompHookFn %shook_ptrs[] = {\n" % P)
        for _a in entry_names:
            fh.write("    %srun_base,\n" % P)
        fh.write("};\n")
        fh.write("uint8_t %shooked[%d];\n\n" % (P, len(entry_names)))
        if AUX_MODULE is not None:
            # A module's tables register with the runtime (x86.h RecompModule);
            # the main image's recomp_call/recomp_jump fall back to them.
            fh.write("static const RecompModule %smodule = {\n"
                     "    %s, 0x%08xu, 0x%08xu,\n"
                     "    %sfunc_addrs, %d, %sbase_ptrs, %shook_ptrs, %shooked,\n"
                     "    %scall_returns, %d, profile_names,\n};\n\n"
                     % (P, json.dumps(AUX_MODULE["name"]), AUX_MODULE["base"],
                        AUX_MODULE["base"] + AUX_MODULE["size"], P, len(entry_names), P, P, P,
                        P, len(call_returns)))
            fh.write("__attribute__((constructor)) static void %sregister(void)\n"
                     "{\n    recomp_module_register(&%smodule);\n}\n" % (P, P))
        else:
            emit_call_return_lookup(fh, call_returns)
            fh.write("""const char *recomp_profile_name(uint32_t i) { return i < recomp_func_count ? profile_names[i] : 0; }

uint32_t recomp_override_count(void)
{
    uint32_t i, n = 0;
    for (i = 0; i < recomp_func_count; ++i)
        if (recomp_base_ptrs[i] != recomp_raw_ptrs[i]) ++n;
    return n;
}

uint64_t recomp_override_hash(void)
{
    uint64_t h = 1469598103934665603ull;
    uint32_t i, j;
    for (i = 0; i < recomp_func_count; ++i) {
        if (recomp_base_ptrs[i] == recomp_raw_ptrs[i]) continue;
        for (j = 0; j < 4; ++j) {
            h ^= (recomp_func_addrs[i] >> (8 * j)) & 0xffu;
            h *= 1099511628211ull;
        }
    }
    return h;
}

/* Indirect calls repeat the same few targets, so a direct-mapped cache sits
 * in front of the binary search. An entry is (target << 32) | (index + 2),
 * written as one word, so a racing reader sees an old or a new entry. */
static uint64_t recomp_lookup_cache[1u << 14];

static int32_t recomp_lookup_slow(uint32_t target);

static int32_t recomp_lookup(uint32_t target)
{
    uint32_t slot = (uint32_t)(target * 2654435761u) >> 18;
    uint64_t e = __atomic_load_n(&recomp_lookup_cache[slot], __ATOMIC_ACQUIRE);
    if ((uint32_t)(e >> 32) == target && (uint32_t)e)
        return (int32_t)((uint32_t)e - 2u); /* 1 caches "not a function" (a shim) */
    int32_t i = recomp_lookup_slow(target);
    __atomic_store_n(&recomp_lookup_cache[slot],
                     ((uint64_t)target << 32) | (uint64_t)(uint32_t)(i + 2), __ATOMIC_RELEASE);
    return i;
}

static int32_t recomp_lookup_slow(uint32_t target)
{
    uint32_t lo = 0, hi = recomp_func_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (recomp_func_addrs[mid] < target) lo = mid + 1; else hi = mid;
    }
    return (lo < recomp_func_count && recomp_func_addrs[lo] == target)
           ? (int32_t)lo : -1;
}

int32_t recomp_index_of(uint32_t addr) { return recomp_lookup(addr); }

int recomp_thunk_target_kind(uint32_t target)
{
    if (recomp_lookup(target) >= 0 || recomp_module_lookup(target) >= 0) return 1;
    return (recomp_is_call_return(target) || recomp_module_is_call_return(target)) ? 2 : 0;
}

static void recomp_call_inner(X86 *c, uint32_t target);
void recomp_call(X86 *c, uint32_t target)
{
    uint32_t ebp_ = recomp_frame_watch ? c->r[5] : 0u;
    recomp_call_inner(c, target);
    if (recomp_frame_watch && c->r[5] != ebp_)
        recomp_frame_changed(c, target, ebp_, c->r[5]);
}
static void recomp_call_inner(X86 *c, uint32_t target)
{
    int32_t i = recomp_lookup(target);
    if (i >= 0) {
        if (recomp_profile_enabled) recomp_profile_push((uint32_t)i);
#ifdef RECOMP_NO_HOOKS
        recomp_base_ptrs[i](c);
#else
        if (__atomic_load_n(&recomp_hooked[i], __ATOMIC_ACQUIRE))
            recomp_hook_ptrs[i](c, (uint32_t)i);
        else
            recomp_base_ptrs[i](c);
#endif
        if (recomp_profile_enabled) recomp_profile_pop();
        return;
    }
    if (recomp_module_call(c, target)) return;
    if (target >= GUEST_SHIM_BASE && target < GUEST_SHIM_END) {
        recomp_shim_call(c, target);
        return;
    }
    recomp_unknown_call(c, target);
}

/* Block-entry dispatch: the table holds every function start, every alternate
 * entry and every decoded jump-table target. */
void recomp_jump(X86 *c, uint32_t target)
{
    if (recomp_seh_pending_target()) recomp_seh_intercept(c, target);
    int32_t i = recomp_lookup(target);
    if (i >= 0) {
        if (recomp_profile_enabled) recomp_profile_push((uint32_t)i);
#ifdef RECOMP_NO_HOOKS
        recomp_base_ptrs[i](c);
#else
        if (__atomic_load_n(&recomp_hooked[i], __ATOMIC_ACQUIRE))
            recomp_hook_ptrs[i](c, (uint32_t)i);
        else
            recomp_base_ptrs[i](c);
#endif
        if (recomp_profile_enabled) recomp_profile_pop();
        return;
    }
    if (recomp_module_call(c, target)) return;
    if (target >= GUEST_SHIM_BASE && target < GUEST_SHIM_END) {
        recomp_shim_call(c, target);
        return;
    }
    if (recomp_is_call_return(target) || recomp_module_is_call_return(target)) { c->eip = target; return; }
    recomp_unknown_jump(c, target);
}

void recomp_unknown_jump(X86 *c, uint32_t target)
{
    if (recomp_run_thunk(c, target)) return;
    /* The address is code the translation does not carry: record it for
     * --discovered, then let the interpreter carry the run past the gap so
     * one run can report every gap it reaches rather than the first. */
    discovery_note("jump", target, c->eip);
    if (interp_call(c, target)) return;
    discovery_write();
    fprintf(stderr, "recomp: no block entry for indirect jump to 0x%08x from 0x%08x\\n",
            target, c->eip);
    abort();
}
""")
    chunks.append(os.path.join(args.out, "table.c"))

    # ---- symbols.json -----------------------------------------------------
    import hashlib

    sha = hashlib.sha256(open(BINARY, "rb").read()).hexdigest()

    globals_out = []
    for section, body in sorted(curated.items()):
        if not section.startswith("globals."):
            continue
        e = {"name": section.split(".", 1)[1], "addr": "%08x" % body["addr"]}
        for k in ("size", "stride", "count"):
            if k in body:
                e[k] = body[k]
        globals_out.append(e)

    events = {k: "%08x" % v for k, v in sorted(curated.get("events", {}).items())}
    for name, addr in events.items():
        f = by_addr.get(int(addr, 16))
        # A module carries a few functions; the game's events belong to the
        # image's own translation and are checked there.
        if (not f or not f["hookable"]) and not args.as_module:
            raise TranslateError("event %s (%s) is not a hookable entry symbol"
                                 % (name, addr))

    # Beside the generated sources, not straight into build/recomp: the build
    # publishes gen/ and the archive together only after the compile succeeds,
    # and the symbol index has to move with them or it will describe a build
    # that was never published.
    with open(os.path.join(args.out, "symbols.json"), "w") as fh:
        json.dump({"exe_sha256": sha, "image_base": "%08x" % image.base,
                   "functions": functions, "globals": globals_out,
                   "events": events}, fh, indent=1)
    if not args.quiet:
        kinds = defaultdict(int)
        for f in functions:
            kinds[f["kind"]] += 1
        print("  symbols.json: %d symbols, %d hookable (%s), %d globals, "
              "%d events"
              % (len(functions), sum(1 for f in functions if f["hookable"]),
                 ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())),
                 len(globals_out), len(events)), file=sys.stderr)

    dt = time.time() - t0
    if not args.quiet:
        print("translated %d/%d functions, %d entry points (+%d alternate, "
              "%d recovered from the PE of which %d found by the data-pointer "
              "scan, %d candidates rejected as data) in %.1fs -> %s (%d chunks)"
              % (len(ok), len(parsed), len(entry_names), len(extra),
                 len(recovered), discovered_by_scan[0], len(rejected),
                 dt, args.out, nchunk + 1))
        if withdrawn:
            print("  withdrew %d guessed blocks whose own dispatch went "
                  "nowhere: %s"
                  % (len(withdrawn),
                     ", ".join("%08x (from %s, wanted %08x)" % (a, w, t)
                               for a, w, t in sorted(withdrawn))))
        print("  jump tables: %d constant-displacement sites, %d decoded, %d "
              "entries, %d entries dispatch nowhere, %d sites decoded nothing"
              % (len(tr.table_sites), tr.stats.get("_jmp_table", 0),
                 tr.stats.get("_jmp_table_entries", 0),
                 sum(len(m) for _f, _a, _t, m in table_gaps), len(undecoded)))
        print("  discovery converged in %d rounds; %d CRT static initializers "
              "from %d __initterm tables, %d alternate entries from data "
              "pointers, %d entry points from instruction immediates, %d "
              "thunks and %d aligned targets accepted without the padding "
              "signal, %d string tails and %d instruction-interior addresses "
              "excluded, %d recovery errors"
              % (_round + 1, initterm_found[0], len(initterm_tables),
                 interior_entries[0], immediate_entries[0],
                 len(image.thunk_candidates), len(image.weak_candidates),
                 len(image.string_candidates), len(image.interior_candidates),
                 len(image.recover_errors)))
        if tr.unmodelled:
            print("%d instructions could not be modelled and trap if reached:" % len(tr.unmodelled))
            for a, why in tr.unmodelled[:20]:
                print("  %08x  %s" % (a, why))
            if len(tr.unmodelled) > 20:
                print("  ... and %d more" % (len(tr.unmodelled) - 20))
        if failures:
            print("FAILED %d functions:" % len(failures))
            for a, why in failures[:40]:
                print("  %08x  %s" % (a, why))
    if args.report:
        with open(args.report, "w") as fh:
            json.dump({
                "image_base": "%08x" % image.base,
                "functions_total": len(parsed),
                "functions_ok": len(ok),
                "entry_points": len(entry_names),
                "alternate_entries": ["%08x" % a for a in sorted(extra)],
                "entry_points_from_data_pointers": discovered_by_scan[0],
                "initterm_tables": [["%08x" % a, "%08x" % b, "%08x" % c]
                                    for a, b, c in initterm_tables],
                "initterm_entries": initterm_found[0],
                "entry_points_from_immediates": immediate_entries[0],
                "alternate_entries_from_data_pointers": interior_entries[0],
                "data_pointer_candidates_rejected": len(rejected),
                "blocks_withdrawn": [["%08x" % a, why, "%08x" % t]
                                     for a, why, t in sorted(withdrawn)],
                "provenance": {k: sum(1 for v in provenance.values() if v == k)
                               for k in ("table", "initterm", "config", "seh", "data",
                                         "immediate", "branch")},
                "table_gaps": [["%08x" % f, "%08x" % a, t,
                                ["%08x" % m for m in ms]]
                               for f, a, t, ms in table_gaps],
                "table_gaps_allowed": args.allow_table_gaps,
                "unmodelled_allowed": args.allow_unmodelled,
                "table_sites_undecoded": [["%08x" % f, "%08x" % a, "%08x" % b]
                                          for f, a, b in undecoded],
                "stale_alternate_entries": len(tr.stale_entries),
                "string_tail_candidates_excluded": len(image.string_candidates),
                "interior_candidates_excluded": len(image.interior_candidates),
                "thunk_candidates": len(image.thunk_candidates),
                "weak_candidates": len(image.weak_candidates),
                "recover_errors": image.recover_errors[:50],
                "discovery_rounds": _round + 1,
                "recovered_blocks": [["%08x" % f.addr, len(f.insns)]
                                     for f in sorted(recovered, key=lambda x: x.addr)],
                "seconds": dt,
                "failures": [["%08x" % a, w] for a, w in failures],
                "unmodelled": [["%08x" % a, w] for a, w in tr.unmodelled],
                "mnemonics": dict(tr.stats),
                "notes": tr.notes[:200],
            }, fh, indent=1)
    return 1 if failures else 0


def check_flags(tr, parsed):
    """Empirical check for the 'flags never cross a call' assumption."""
    entry_live = 0
    after_call = 0
    call_sites = 0
    examples = []
    for fn in parsed:
        li, lo = tr.liveness_cfg(fn, call_transparent=True)
        if li and li[0]:
            entry_live += 1
            if len(examples) < 10:
                examples.append("entry %08x live-in %s" % (fn.addr, sorted(li[0])))
        for i, ins in enumerate(fn.insns):
            if ins.mnem == "CALL":
                call_sites += 1
                if lo[i]:
                    after_call += 1
                    if len(examples) < 20:
                        examples.append("after call %08x live-out %s"
                                        % (ins.addr, sorted(lo[i])))
    print("functions with flags live at entry: %d / %d" % (entry_live, len(parsed)))
    print("call sites with flags live afterwards: %d / %d" % (after_call, call_sites))
    for e in examples:
        print("  " + e)


if __name__ == "__main__":
    sys.exit(main())
