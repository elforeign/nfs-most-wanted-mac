"""What build_all.py needs from a disassembly of speed.exe's .text, from the exe itself (capstone).

    lst = listing.load(pe)       # Listing(starts, ends, imms)

starts: every instruction start of a linear sweep. ends: for each instruction end, (start, kind) of the instruction
that ends there, kind "pad" (int3, nop, lea r,[r], mov edi,edi, xchg ax,ax), "end" (ret*, jmp, hlt, ud2, iretd:
control flow doesn't continue) or "other". imms: code addresses (6 hex digits) that push/mov use as immediates.

RECOMP_LISTING=ghidra reads the same from an llvm-objdump listing of the exe (developer tooling) instead
(kept one release for A/B). Both must give the same result: the capstone sweep omits 10-byte instructions from
starts/ends because the objdump parser never saw them (llvm-objdump writes no space after the 10th byte, which the
line regex needs); their immediates count, as they did there.
"""
import collections
import os
import re
import sys

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recomp import SPEED_EXE_FINGERPRINT  # noqa: E402

TEXT_LO, TEXT_HI = 0x401000, 0x401000 + 0x48E2A5
ASM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "decomp", "asm", "speed_text.asm")

Listing = collections.namedtuple("Listing", "starts ends imms")
_LEA_SELF = re.compile(r"^(\w+), \[(\w+)( \+ 0(x0)?)?\]$")
_IMM = re.compile(r"^(?:.*, )?0x([0-9a-f]{6})$")


def kind(mn, ops):
    if mn in ("int3", "nop") or (mn == "lea" and _LEA_SELF.match(ops) and ops.split(",")[0] in ops.split("[")[1]) \
            or (mn == "mov" and ops == "edi, edi") or (mn == "xchg" and ops == "ax, ax"):
        return "pad"
    if mn.startswith("ret") or mn in ("jmp", "hlt", "ud2", "iretd"):
        return "end"
    return "other"


def _imm(mn, ops):
    m = _IMM.match(ops) if mn in ("push", "mov") else None
    return int(m.group(1), 16) if m else None


def from_exe(pe):
    text = pe.read(TEXT_LO, TEXT_HI - TEXT_LO)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    starts, ends, imms = set(), {}, []
    off = 0
    while off < len(text):
        for a, n, mn, ops in md.disasm_lite(text[off:], TEXT_LO + off):
            off = a + n - TEXT_LO
            t = _imm(mn, ops)
            if t is not None:
                imms.append(t)
            if n != 10:
                starts.add(a)
                ends[a + n] = (a, kind(mn, ops))
        if off < len(text):              # undecodable byte: step over it
            starts.add(TEXT_LO + off)
            ends[TEXT_LO + off + 1] = (TEXT_LO + off, "other")
            off += 1
    return Listing(starts, ends, imms)


def from_objdump(path=ASM):
    rx = re.compile(r"^\s+([0-9a-f]+):\s((?:[0-9a-f]{2} )+)\s*\t(\S+)\t?(.*)$")
    rx_imm = re.compile(r"\t(push|mov)\t(?:.*, )?0x([0-9a-f]{6})$")
    starts, ends, imms = set(), {}, []
    with open(path) as f:
        for line in f:
            m = rx_imm.search(line.rstrip())
            if m:
                imms.append(int(m.group(2), 16))
            m = rx.match(line)
            if not m:
                continue
            a = int(m.group(1), 16)
            starts.add(a)
            ends[a + len(m.group(2).split())] = (a, kind(m.group(3), m.group(4).strip()))
    return Listing(starts, ends, imms)


def load(pe):
    return from_objdump() if os.environ.get("RECOMP_LISTING") == "ghidra" else from_exe(pe)


# ---------------- function manifest (replaces the Ghidra function list) ----------------
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "functions_v13.tsv")
GHIDRA_TSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "decomp", "functions.tsv")
KEEP_NAMES = ("initterm",)          # names build_all.py tests (the CRT initializer-table walker)


def read_starts(path=None):
    """[(address, name)] of the function starts; names are FUN_XXXXXXXX except the ones the build needs."""
    if path is None:
        path = GHIDRA_TSV if os.environ.get("RECOMP_LISTING") == "ghidra" else MANIFEST
    starts = []
    for line in open(path):
        if path == MANIFEST:
            if line.startswith("#"):
                m = re.match(r"# code-fingerprint ([0-9a-f]{64})", line)
                if m and m.group(1) != SPEED_EXE_FINGERPRINT:
                    raise SystemExit(f"{path} is for speed.exe code {m.group(1)}, not the supported {SPEED_EXE_FINGERPRINT}")
                continue
            a, _, name = line.rstrip("\n").partition("\t")
            starts.append((int(a, 16), name or f"FUN_{a.upper()}"))
            continue
        f = line.split("\t")
        if len(f) > 3 and len(f[0]) == 8:
            try:
                starts.append((int(f[0], 16), f[1]))
            except ValueError:
                pass
    return starts


def write_manifest(src=GHIDRA_TSV, out=MANIFEST):
    starts = sorted(set(read_starts(src)))
    with open(out, "w") as f:
        f.write("# Function starts of speed.exe PC v1.3 (from a Ghidra analysis of the exe): addresses only, plus the\n"
                "# names recomp/build_all.py tests. No code or bytes. Regenerate: python recomp/listing.py --manifest\n"
                f"# code-fingerprint {SPEED_EXE_FINGERPRINT}\n")
        for a, name in starts:
            f.write(f"{a:08x}\t{name}\n" if any(k in name for k in KEEP_NAMES) else f"{a:08x}\n")
    print(f"{out}: {len(starts)} functions")


if __name__ == "__main__":
    if sys.argv[1:] == ["--manifest"]:
        write_manifest()
