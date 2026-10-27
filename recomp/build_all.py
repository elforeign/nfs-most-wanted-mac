"""Translate every function of speed.exe to C and compile it for arm64 (one static library).

    tools/.venv/bin/python port/recomp/build_all.py [--jobs N] [--exe path/to/speed.exe]

Inputs: the exe (only the PC v1.3 code, checked by a code fingerprint: exe_fingerprint.py), its .text disassembled with capstone (listing.py) and the
committed function manifest functions_v13.tsv. RECOMP_LISTING=ghidra uses a Ghidra/objdump listing of the exe (developer tooling, not in a public checkout)
instead (same output; kept one release for A/B).

Output in port/recomp/out/: speed_NN.c (chunks), funcs.h (prototypes), table.c (guest address -> function, for
dispatch, and the code fingerprint of speed.exe), speed.a, and report.txt. Functions the translator can't handle yet become stubs that call
untranslated(c, address), so the library links completely.
"""
import argparse
import re
import collections
import concurrent.futures
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import listing  # noqa: E402
import recomp  # noqa: E402
from difftest import load_functions  # noqa: E402

OUT = os.path.join(HERE, "out")
CHUNK = 800


PADDING = {("nop", ""), ("int3", "")}


def plausible_start(t, starts, ends):
    """A real function start: an instruction boundary that ordinary code doesn't fall into. Walks back over
    padding (int3, nop, lea r,[r+0], mov edi,edi) to the last real instruction, which must end control flow."""
    if t not in starts:
        return False
    at, k = t, 0
    while k < 32:
        prev = ends.get(at)
        if prev is None:
            return True                       # data or alignment bytes before it
        a, kind = prev
        if kind == "pad":
            at, k = a, k + 1
            continue
        return kind == "end"
    return True


def add_data_referenced(funcs):
    """Function starts Ghidra missed: code addresses stored in data (static-initializer tables, vtables,
    callbacks). Taken when the pointer targets .text at a 16-byte boundary or right after padding/ret."""
    pe = recomp.PE()
    known = {a for a, _, _ in funcs}
    text_lo, text_hi = 0x401000, 0x401000 + 0x48E2A5
    extra = set()
    lst = listing.load(pe)
    starts, ends = lst.starts, lst.ends
    rejected = 0
    for name, vs, va, rs, raw in pe.sections:
        if name.startswith(b".text"):
            continue
        # Only the fingerprinted data (.rdata/.data), so the output can't depend on resources or extra sections.
        if not any(va <= r_va < va + max(vs, rs) for n, r_va, _ in recomp.exe_fingerprint.REGIONS if n != ".text"):
            continue
        blob = pe.data[raw:raw + rs]
        for k in range(0, len(blob) - 3, 4):
            t = int.from_bytes(blob[k:k + 4], "little")
            if text_lo <= t < text_hi and t not in known:
                prev = pe.read(t - 1, 1)
                before = pe.read(t - 5, 5)
                after_jmp_ret = before[0] == 0xE9 or before[3] == 0xEB or before[2] == 0xC2   # jmp rel32/rel8, ret n
                if t % 16 == 0 or prev in (b"\xcc", b"\xc3", b"\x90") or after_jmp_ret:
                    if plausible_start(t, starts, ends):
                        extra.add(t)
                    else:
                        rejected += 1
    # Every entry of the CRT initializer tables (`push end; push start; call __initterm`): C++ static
    # constructors and atexit registrations, which often sit right after a jmp thunk where the heuristic misses.
    names = {a: n for a, _, n in funcs}
    initterm = {a for a, n in names.items() if "initterm" in n}
    text = pe.read(text_lo, text_hi - text_lo)
    n_init = 0
    for k in range(len(text) - 15):
        if text[k] == 0x68 and text[k + 5] == 0x68 and text[k + 10] == 0xE8:
            call_at = text_lo + k + 10
            tgt = (call_at + 5 + int.from_bytes(text[k + 11:k + 15], "little", signed=True)) & 0xFFFFFFFF
            if tgt not in initterm:
                continue
            end, start = int.from_bytes(text[k + 1:k + 5], "little"), int.from_bytes(text[k + 6:k + 10], "little")
            for a in range(start, end, 4):
                t = int.from_bytes(pe.read(a, 4) or b"\0\0\0\0", "little")
                if text_lo <= t < text_hi and t not in known:
                    extra.add(t)
                    n_init += 1
    print(f"initializer-table entries added: {n_init}")
    # Code addresses used as immediates in code (push offset callback / mov reg, offset): callbacks registered at
    # run time. Taken at the same plausible starts as data pointers.
    n_imm = 0
    for t in lst.imms:
        if text_lo <= t < text_hi and t not in known and t not in extra:
            prev = pe.read(t - 1, 1)
            before = pe.read(t - 5, 5)
            if t % 16 == 0 or prev in (b"\xcc", b"\xc3", b"\x90") or before[2] == 0xC2:
                if plausible_start(t, starts, ends):
                    extra.add(t)
                    n_imm += 1
                else:
                    rejected += 1
    print(f"code-immediate starts added: {n_imm}; candidates rejected (code falls into them): {rejected}")
    starts = sorted([(a, n) for a, _, n in funcs] + [(t, f"data_ref_{t:08X}") for t in extra])
    out = []
    for k, (a, n) in enumerate(starts):
        nxt = starts[k + 1][0] if k + 1 < len(starts) else text_hi
        out.append((a, min(nxt, text_hi), n))
    print(f"added {len(extra)} function starts referenced from data")
    return out


def load_hooks():
    """hooks.txt: guest functions the runtime replaces (address, then a comment naming the owner)."""
    hooks = {}
    for line in open(os.path.join(HERE, "hooks.txt")):
        line = line.split("#", 1)[0].strip()
        if line:
            a, _, why = line.partition(" ")
            hooks[int(a, 16)] = why.strip()
    return hooks


def water_reflection_gates(addr, code):
    """R18: gate-only reads; never fake Rain.wetness for the rest of the frame.

    These tests are inline, so a whole-function hooks.txt wrapper cannot isolate
    them. Assert the exact instruction/operand to fail closed on translator drift.
    """
    sites = {
        0x006BFDD0: ((0x006BFED6, 'level', None), (0x006BFEE6, 'wetness', 'eax')),
        0x006DE300: ((0x006DE3FA, 'level', None), (0x006DE4E4, 'level', None),
                     (0x006DE4F7, 'wetness', 'edx'), (0x006DE59F, 'wetness', 'edx')),
    }
    for site, kind, reg in sites.get(addr, ()):
        marker = f'    /* {site:08X}: '
        assert code.count(marker) == 1, f'water gate instruction missing: {site:08X}'
        start = code.index(marker)
        end = code.find('    /* ', start + len(marker))
        assert end >= 0
        block = code[start:end]
        if kind == 'level':
            old = 'M32((uint32_t)(0x9017D4u))'
            new = 'x360_water_reflection_level()'
        else:
            old = f'MF32((uint32_t)(c->{reg} + 0x36A0u))'
            new = f'x360_water_reflection_wetness(0x{site:08X}u, {old})'
        assert block.count(old) == 1, f'water gate operand changed: {site:08X}'
        code = code[:start] + block.replace(old, new, 1) + code[end:]
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--exe", help="the player's speed.exe (default: $NFS_SPEED_EXE, else the owner's game folder)")
    args = ap.parse_args()
    if args.exe:
        recomp.EXE = args.exe
    os.makedirs(OUT, exist_ok=True)
    # A failed translation, compiler launch or archive must never leave an older
    # library looking like the output of this invocation.
    lib = os.path.join(OUT, "speed.a")
    if os.path.exists(lib):
        os.remove(lib)
    funcs = load_functions()
    funcs = add_data_referenced(funcs)
    rc = recomp.Recompiler(functions={a: l for a, l, _ in funcs})
    rc.calls = True
    bodies, reasons, insns = [], collections.Counter(), 0
    for a, limit, name in funcs:
        code, why = rc.translate(a, limit)
        if code is None:
            reasons[why] += 1
            code = (f"void f_{a:08X}(Cpu *c) {{ untranslated(c, 0x{a:08X}u); }} /* {name}: {why} */")
        else:
            insns += code.count("\n    /* ")
        bodies.append((a, name, code))
    hooks = load_hooks()
    for k, (a, name, code) in enumerate(bodies):
        code = water_reflection_gates(a, code)
        bodies[k] = (a, name, code)
        if a in hooks:
            # The runtime replaces this function: the translated body becomes f_X_orig, and f_X a weak wrapper that a
            # strong f_X in the runtime overrides (weak functions aren't inlined, so every caller reaches the hook).
            code = code.replace(f"void f_{a:08X}(Cpu *restrict c) {{", f"void f_{a:08X}_orig(Cpu *restrict c) {{", 1)
            code += f"\n__attribute__((weak)) void f_{a:08X}(Cpu *c) {{ f_{a:08X}_orig(c); }}"
            bodies[k] = (a, name, code)
    with open(os.path.join(OUT, "funcs.h"), "w") as h:
        h.write('#pragma once\n#include "runtime.h"\nvoid untranslated(Cpu *c, uint32_t addr);\n')
        h.write('uint32_t x360_water_reflection_level(void);\nfloat x360_water_reflection_wetness(uint32_t site, float wetness);\n')
        h.writelines(f"void f_{a:08X}(Cpu *c);\n" for a, _, _ in bodies)
        h.writelines(f"void f_{a:08X}_orig(Cpu *c);   /* hooked: {hooks[a]} */\n" for a, _, _ in bodies if a in hooks)
    with open(os.path.join(OUT, "table.c"), "w") as t:
        t.write('#include "funcs.h"\n')
        t.write("const uint32_t guest_func_addr[] = {" + ",".join(f"0x{a:08X}u" for a, _, _ in bodies) + "};\n")
        t.write("void (*const guest_func_ptr[])(Cpu *) = {" + ",".join(f"f_{a:08X}" for a, _, _ in bodies) + "};\n")
        t.write(f"const unsigned guest_func_count = {len(bodies)};\n")
        # The runtime only loads a speed.exe with the code these functions were translated from (the game folder
        # check): the code fingerprint and its per-region digests (to name the region that differs), see
        # exe_fingerprint.py. speed_exe_sha256 is the owner's whole-file hash, still accepted as an alias.
        fp = recomp.exe_fingerprint
        t.write(f'const char speed_exe_fingerprint[] = "{rc.pe.fingerprint}";\n')
        t.write("const char speed_exe_region_sha256[4][65] = {" + ",".join(f'"{fp.SUPPORTED[k]}"' for k in fp.NAMES) + "};\n")
        t.write(f'const char speed_exe_sha256[] = "{fp.WHOLE_FILE_SHA256}";\n')
    files = []
    for k in range(0, len(bodies), CHUNK):
        path = os.path.join(OUT, f"speed_{k // CHUNK:02d}.c")
        with open(path, "w") as f:
            f.write('#include "funcs.h"\n\n')
            f.write("\n\n".join(code for _, _, code in bodies[k:k + CHUNK]))
        files.append(path)
    files.append(os.path.join(OUT, "table.c"))

    def compile_one(path):
        obj = path[:-2] + ".o"
        r = subprocess.run(["clang", "-c"] + recomp.CFLAGS + os.environ.get("RECOMP_CFLAGS", "").split() + ["-I", HERE, "-I", OUT, path, "-o", obj],
                           capture_output=True, text=True)
        return path, obj, r.returncode, r.stderr[-2000:]

    objs, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        for path, obj, rc_, err in ex.map(compile_one, files):
            (objs if rc_ == 0 else errors).append(obj if rc_ == 0 else (path, err))
    if not errors:
        pending = lib + ".new"
        try:
            if os.path.exists(pending):
                os.remove(pending)
            subprocess.run(["ar", "rcs", pending] + objs, check=True)
            os.replace(pending, lib)
        finally:
            if os.path.exists(pending):
                os.remove(pending)
    translated = len(funcs) - sum(reasons.values())
    rep = [f"functions: {len(funcs)}, translated: {translated} ({100 * translated / len(funcs):.1f} %), "
           f"stubbed: {sum(reasons.values())}",
           f"instructions translated: {insns}",
           f"C files: {len(files)}, compile errors: {len(errors)}",
           "stub reasons:"] + [f"  {n:5d}  {w}" for w, n in reasons.most_common(30)]
    for path, err in errors[:3]:
        rep.append(f"ERROR {path}:\n{err}")
    with open(os.path.join(OUT, "report.txt"), "w") as report:
        report.write("\n".join(rep) + "\n")
    print("\n".join(rep[:6]))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
