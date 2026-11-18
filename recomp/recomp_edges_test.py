"""Offline instruction boundary cases and the MW _CIpow guest entry.

Baseline vs fixed: FSCALE 1/8 -> 8/8, PMADDWD 0/1 -> 1/1,
SHL 2/6 -> 6/6, _CIpow 1/7 -> 7/7. The baseline pow(2,+Inf)
reaches FNSAVE at 007C475D, and pow(+Inf,2) enters the wrong helper.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import recomp

INSTRUCTIONS = r'''
#include "runtime.h"
#include <stdlib.h>
uint8_t *G;

static int scale(int index, Cpu *c) {
    static const double values[] = {1, -1, -1, -1, 0, INFINITY, 1, 1};
    static const double scales[] = {0x1p31, -0x1.00000002p31, INFINITY, -INFINITY,
                                    INFINITY, -INFINITY, NAN, -2.75};
    static const double expected[] = {INFINITY, -0., -INFINITY, -0., NAN, NAN, NAN, 0.25};
    c->top = 7;
    ST(0) = values[index];
    ST(1) = scales[index];
    scale_instruction(c);
    if (c->top != 7 || ((c->sw & 1) != (index == 4 || index == 5))) return 1;
    if (isnan(expected[index])) return !isnan(ST(0));
    return ST(0) != expected[index] || !!signbit(ST(0)) != !!signbit(expected[index]);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    Cpu cpu = {0}, *c = &cpu;
    int index = atoi(argv[2]);
    switch (argv[1][0]) {
    case 'f': return scale(index, c);
    case 'p':
        c->mm[0] = 0xffff000280008000ull;
        c->mm[1] = 0x0003000480008000ull;
        madd_instruction(c);
        return c->mm[0] != 0x0000000580000000ull;
    case 's': {
        static const uint32_t counts[] = {10, 17, 8, 16, 33, 32};
        static const uint32_t result[] = {0x12345600, 0x12340000, 0x12345600,
                                          0x12340000, 0x12345602, 0x12345681};
        c->eax = 0x12345681;
        c->ecx = counts[index];
        int cf = index == 0 ? shift_immediate(c) :
                 (index == 1 || index == 3) ? shift_word(c) : shift_byte(c);
        return c->eax != result[index] || cf != (index >= 4);
    }
    default: return 2;
    }
}
'''

POW = r'''
#include "runtime.h"
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
uint8_t *G;
static jmp_buf failed;
static uint32_t site;
void untranslated(Cpu *c, uint32_t addr) {
    (void)c;
    site = addr;
    longjmp(failed, 1);
}
void f_007C45F0(Cpu *c);

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    static const double bases[] = {2, INFINITY, -INFINITY, 1, NAN, 0, 0.5};
    static const double exponents[] = {INFINITY, 2, -3, INFINITY, 0, 0, INFINITY};
    static const double expected[] = {INFINITY, INFINITY, -0., -NAN, NAN, 1, 0};
    unsigned i = (unsigned)atoi(argv[1]);
    G = calloc(1, 0xa00000);
    if (!G) return 2;
    M32(0x9ba9f0) = 1; /* The stock error hook returns the result without invoking the CRT error callback. */
    Cpu cpu = {.esp = 0x10000, .top = 5, .cw = 0x027f, .eflags = i == 5 ? 0 : 0x40};
    Cpu *c = &cpu;
    ST(0) = exponents[i];
    ST(1) = bases[i];
    ST(2) = 42;
    if (setjmp(failed)) {
        fprintf(stderr, "unexpected guest instruction or helper %08x\n", site);
        return 1;
    }
    f_007C45F0(c);
    double result = ST(0);
    int equal = isnan(expected[i]) ? isnan(result) : result == expected[i];
    return c->top != 6 || c->esp != 0x10004 || ST(1) != 42 ||
           (c->eflags & 0x40) || !equal || !!signbit(result) != !!signbit(expected[i]);
}
'''


def build(tmp, name, source, include):
    src = tmp / (name + ".c")
    exe = tmp / name
    src.write_text(source)
    command = ["clang", "-std=c11", "-O1", "-fsanitize=undefined,float-cast-overflow",
               "-fno-sanitize-recover=all", "-I", str(include), str(src), "-o", str(exe)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2400:]
    return exe


def cases(exe, name, args):
    results = [subprocess.run([str(exe), *arg], capture_output=True, text=True) for arg in args]
    print(f"{name}: {sum(r.returncode == 0 for r in results)}/{len(results)}", flush=True)
    for arg, result in zip(args, results):
        if result.returncode:
            print(f"  case {' '.join(arg)} failed: {result.stderr.splitlines()[0] if result.stderr else 'result or flags'}")
    return all(r.returncode == 0 for r in results)


def instructions(tmp, include):
    rc = recomp.Recompiler(pe=object())
    functions = []
    for name, encoding, carry in (
        ("scale_instruction", "d9fd", False), ("madd_instruction", "0ff5c1", False),
        ("shift_immediate", "c0e00a", True), ("shift_word", "66d3e0", True),
        ("shift_byte", "d2e0", True),
    ):
        insn, = rc.md.disasm(bytes.fromhex(encoding), 0x300000)
        emitted = "\n".join(rc.insn(insn, insn.address, insn.address + insn.size))
        initial = "int cf = 1, of, zf, sf, pf;" if carry else ""
        result = "return cf;" if carry else ""
        functions.append(f"static {'int' if carry else 'void'} {name}(Cpu *c) {{ {initial} {emitted} {result} }}")
    prefix, main = INSTRUCTIONS.split("static int scale", 1)
    exe = build(tmp, "insns", prefix + "\n".join(functions) + "\nstatic int scale" + main, include)
    return all([
        cases(exe, "FSCALE", [("f", str(i)) for i in range(8)]),
        cases(exe, "PMADDWD", [("p", "0")]),
        cases(exe, "SHL", [("s", str(i)) for i in range(6)]),
    ])


def pow_entry(tmp, include, generated, boot):
    source = generated.read_text()
    hooked = "void f_007C45F0_orig(" in source
    entry = "f_007C45F0_orig" if hooked else "f_007C45F0"
    names = [entry, "f_007C464D", "f_007CE405", "f_007CE45E"]
    declarations = "\n".join(f"void {fn}(Cpu *c);" for fn in names)
    translated = "\n".join(function_body(source, name) for name in names)
    missing = sorted(set(re.findall(r"\b(f_[0-9A-F]{8})\(c\)", translated)) - set(names))
    stubs = "\n".join(f"void {fn}(Cpu *c) {{ untranslated(c, 0x{fn[2:]}); }}" for fn in missing)
    hook = function_body(boot.read_text(), "f_007C45F0") if hooked else ""
    exe = build(tmp, "pow", "\n".join((POW, declarations, stubs, translated, hook)), include)
    return cases(exe, "_CIpow", [(str(i),) for i in range(7)])


def function_body(source, name):
    match = re.search(rf"^void {name}\(Cpu [^)]*\)\s*\{{", source, re.M)
    assert match, name
    return source[match.start():source.index("\n}", match.end()) + 2]


def main():
    generated = HERE / "out/speed_52.c"
    with tempfile.TemporaryDirectory(prefix="recomp-edges-") as scratch:
        tmp = Path(scratch)
        ok = instructions(tmp, HERE)
        if not generated.is_file():
            print("SKIP: _CIpow needs the generated MW sources")
            return 3 if ok else 1
        return 0 if pow_entry(tmp, HERE, generated, HERE.parent / "runtime/boot.c") and ok else 1


if __name__ == "__main__":
    sys.exit(main())
