"""Offline: a reachable int3 continues to the next instruction, like a debugger's "continue" (recomp.py int3).

The recompiler translated every int3 as a bare `return;`, so a call to __break 0045CD20 (`int3; ret`, an assert such
as CarLoader::RemoveSomethingFromCarMemoryPool 00759FB1 with nothing to evict) came back with the guest esp 4 bytes
low: the split-screen race load passed the return address 00759FB6 as LoadStreamingEntry's entry count (154 MB
bMalloc). Inline asserts were worse: bFree's int3 at 00465760 skipped three pops, ADD ESP,20h and the RET.
Checks the translated C of: __break and bFree from the exe; and in-memory code for a prefixed two-byte int3 (F3 CC
continues after both bytes), an int3 that is a jump target (interior), and padding after a noreturn call (still
returns: nothing follows to continue to). Behaviour against the original x86: difftest.py --synth --only-synth int3.
Before (b3c8fe2 recomp.py): FAIL, 5 of 5 checks fail; after: PASS, 5 of 5. difftest int3 synth: 0/4 before, 4/4 after.
Run: tools/.venv/bin/python -I recomp/int3_ret_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recomp  # noqa: E402
from difftest import load_functions  # noqa: E402

BASE = 0x00300000          # an unused address range for the in-memory snippets (as difftest SYNTH_BASE)


class MemPE(recomp.PE):
    """The game's PE plus a few bytes of code at BASE."""
    def __init__(self, code):
        super().__init__()
        self.code = code

    def read(self, addr, n):
        if BASE <= addr < BASE + len(self.code):
            return self.code[addr - BASE:addr - BASE + n]
        return super().read(addr, n)


def body(r, addr, limit):
    c, err = r.translate(addr, limit)
    if c is None:
        sys.exit(f"FAIL: {addr:08X} did not translate: {err}")
    return c


fails = 0


def check(name, ok, detail):
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    fails += not ok


r = recomp.Recompiler(functions={a: l for a, l, _ in load_functions()})
r.calls = True                       # as build_all: bFree calls out and its asserts need the function map
c = body(r, 0x45CD20, r.functions[0x45CD20])
check("__break 0045CD20 int3; ret", "/* 0045CD21: ret  */" in c and "c->esp += 4; return;" in c and "padding" not in c,
      "continues to its ret (pops the return address)")
c = body(r, 0x4655F0, r.functions[0x4655F0])
tail = c[c.find("/* 00465760: int3"):]
want = ["00465761: pop edi", "00465762: pop esi", "00465763: pop ebx", "00465764: add esp, 0x20", "00465767: ret"]
check("bFree int3 00465760", all(w in tail for w in want) and "padding" not in tail.split("00465767")[0],
      "continues with three pops, ADD ESP,20h, RET")

code = bytes.fromhex(
    "F3CC" "40" "C3"                 # +0: rep int3 (2 bytes); inc eax; ret
    "85C9" "7401" "42" "CC" "40" "C3"  # +4: test ecx,ecx; jz +1; inc edx; L: int3; inc eax; ret
    "E8F3FFFFFF" "CC" "CC" "CC")     # +12: call +4 (taken as noreturn); int3 padding up to the limit
r = recomp.Recompiler(pe=MemPE(code), functions={BASE: BASE + 4, BASE + 4: BASE + 12, BASE + 12: BASE + 20})
r.calls = True
c = body(r, BASE, BASE + 4)
check("prefixed int3 F3 CC", "/* 00300002: inc eax */" in c and "/* 00300001:" not in c and "padding" not in c,
      "continues at +2 (i.size), not +1")
c = body(r, BASE + 4, BASE + 12)
check("interior int3 (jump target)", "L_00300009:;" in c and "/* 0030000A: inc eax */" in c and "padding" not in c,
      "the jump lands on the int3 and continues")
c = body(r, BASE + 12, BASE + 20)
check("padding after a call", c.count("padding int3") == 1 and "/* 00300012:" not in c,
      "one padding return, no decode past the int3 run")
print("PASS: reachable int3 continues" if not fails else f"FAIL: {fails} check(s)")
sys.exit(1 if fails else 0)
