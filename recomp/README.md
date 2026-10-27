# port/recomp: static recompiler for speed.exe (x86-32 → C → arm64)

Run everything with `tools/.venv/bin/python` (capstone, unicorn).

| File | Purpose |
|---|---|
| `recomp.py` | Translator: one C function per guest function (`void f_XXXXXXXX(Cpu *c)`) |
| `runtime.h` | `Cpu` struct, guest memory macros (`M32(a)` = `G + a`), flag helpers, x87 emulation |
| `difftest.py` | Differential tests against the original code under Unicorn |
| `build_all.py` | Translates all of `speed.exe` and compiles it → `out/speed.a` |

## How it translates

- **Function bodies** come from recursive descent from each Ghidra function start, bounded by the next start. Ghidra's own body sizes are unreliable here; many are 1 byte.
- **Registers** live in `Cpu`. **Flags** are C locals written by every flag-setting instruction; clang drops the unused ones.
- **Stack:** the guest stack is real guest memory, and calls push the guest return address, so stack-passed arguments and `ret n` work unchanged.
- **Control flow:** branches become `goto`. Switch jump tables (`jmp [reg*4+table]`) become a C `switch`. Direct calls become C calls. Indirect calls/jumps go through `dispatch(c, addr)`. Tail jumps, conditional tail jumps and fall-through into the next function call the target and return. IAT calls go to `host_import(c, slot)`.
- **x87:** 8 `double` registers with TOP, status and control words. Precision control 00 rounds to `float`, like the original under Direct3D 9. Rounding control applies to `fist*`/`frndint`. Compares set C0/C2/C3, and `fnstsw ax` includes TOP. Invalid operations produce x87's *negative* default NaN. `fld/fstp tbyte` converts 80-bit ↔ double.

## Results (2026-10-18)

**Coverage** (`build_all.py`, `out/report.txt`): 52,656 functions (Ghidra's 52,043 plus 613 starts found as code pointers in data: static initializers, vtables, callbacks). **52,458 translate (99.6 %), 1,410,745 instructions.** The remaining 198 are stubs that call `untranslated()`: mostly MMX (`movq`/`pxor`/`movd`, the VP6 and audio codecs, which FFmpeg replaces anyway) and bytes that don't decode at some data-referenced starts.

**Build:** 69 generated C files compile with clang -O2 (`recomp.CFLAGS`), 0 errors, in about 35 s on 18 cores → `out/speed.a` (arm64).

**Registers in host registers:** every translated function takes `Cpu *restrict c` and starts with `uint8_t *const G = guest_base();`, a local copy of the guest base. Guest memory is accessed through `may_alias` pointers, so without `restrict` every guest store could have overwritten the `Cpu` struct (and the global `G`), and clang reloaded registers after each one. `restrict` only helps while LLVM can prove `c` isn't captured, and its capture tracking stops after 20 uses of a pointer (every register access is one), so `CFLAGS` raises `-capture-tracking-max-uses-to-explore`. Result: `speed.a` text 26.2 → 20.6 MB, x87 stack values stay in FP registers within a function, guest code ~35 % faster. The `Cpu` is host memory that guest addresses never reach, which is what makes `restrict` valid; host code called from translated code must reach the `Cpu` only through the pointer it is given. `difftest.py` compiles with the same flags.

**Differential tests** (`difftest.py`). Each state: random registers and stack arguments pointing into a random scratch area. The original runs under Unicorn (states where it faults on a random pointer, or runs more than 2M instructions, are skipped), the C runs in a forked child. Compared: all 8 GPRs, ST(0), and every byte of scratch, the ±1 KB stack window and `.data`.

| Run (log in this folder) | Functions with a valid state | Pass | Fail |
|---|---|---|---|
| all leaf functions × 4 states (`difftest_all_leaf.log`, earlier translator) | 18,804 | **18,795** | 9 |
| leaf, 3,000 sampled × 8 (`difftest_final_leaf.log`, 2026-10-19 translator) | 2,628 | **2,627** | 1 |
| non-leaf, callees compiled in (closure ≤ 40) × 8 (`difftest_final_calls.log`) | 238 | **237** | 1 |
| synthetic per-instruction cases × 48 (`--synth`, `difftest_synth.log`) | 336 | **336** | 0 |

`--synth` assembles short snippets with clang (every MMX helper, rcl/rcr, 8/16/32-bit mul/imul/div/idiv, bt/bts/btr/btc on registers and memory bit strings, string ops in both directions, loop*/jecxz, xlatb, pushfd/popfd including the ID-bit CPUID probe, the AC bit and DF round trips, lahf/sahf) and compares them under Unicorn with the flags each one defines. It found: `bt mem, reg` ignoring the bit-string offset (the CRT's `_strpbrk`/`_strcspn` got every character ≥ 32 wrong), unsigned 16-bit offsets in `bts/btr/btc word ptr`, DF lost by `pushfd`/`popfd` and ignored by `repe cmps`.

Open failures: `FUN_004aa830` takes its inputs on the x87 stack, which the harness starts empty (not a translator bug); `__fFSINH` differs in one low mantissa bit. Earlier runs also listed `__ftol2`/`_test_whether_TOS_is_int` and two float helpers (x87 control-word / integer-conversion corner cases on random input), `FUN_007bec10`, `FUN_005cb510`, `unknown_libname_653`. Known difference: `fsin`/`fcos` use libm, so for huge arguments x87's range reduction gives other results; the game feeds them angles in range.

Bugs found by the differential tests and fixed: `fxch` (capstone lists the implicit `st(0)` first), x87's negative default NaN, and functions that run on into the next Ghidra function. Harness bugs fixed along the way: Unicorn's physical FP registers, and stack/EFLAGS/FPU state carrying over between Unicorn runs.

**First boot** (`../runtime`): the translated game runs the MSVC C runtime start-up and the game's static constructors natively, about 640 Win32 calls (heap, TLS, code pages, environment, time…). It then spins inside the game's own allocator (`MemoryPool::AllocateMemory` ← `bWareMalloc` ← `FastMem::Alloc` ← `EmitterSystem::EmitterSystem`, a static constructor via `__initterm`). Next step: find whether that's a translation bug or a host-API gap.

## Replacing game functions

`hooks.txt` lists guest functions that host code replaces. `build_all.py` emits each one's translation as `f_XXXXXXXX_orig` and makes `f_XXXXXXXX` a weak wrapper around it; a strong `f_XXXXXXXX` in the runtime then receives every call (direct, tail jump, `dispatch()`), and can call `f_XXXXXXXX_orig`. The replacement follows the guest ABI: arguments on the guest stack, `this` in `c->ecx`, and it pops its return address (plus stdcall/thiscall arguments) as `ret n` would. Current hooks: the movie player (`runtime/movies.c`).

## Next

1. Debug the `MemoryPool::AllocateMemory` spin: run that path under Unicorn from the same guest state and compare (the harness can be pointed at it), or check the host heap/`GlobalMemoryStatusEx` assumptions.
2. Threads (`CreateThread` is only recorded), then the D3D9 → Metal, DirectSound → CoreAudio (`../media`) and DirectInput layers.
3. Close the coverage gaps (MMX), or route those functions to an interpreter; fix the ~0.1 % open differential failures.
