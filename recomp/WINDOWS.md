# Windows host and first D3D12 image

`build_windows.py --host-layer` compiles the generated C, the generated
`runtime/imports.c` metadata, and only `runtime/win/*.c`. It does not select any
Mac host implementation or change Mac build inputs. Without `--host-layer`, the
job 1 generated-code-only build remains available. Pass the resulting objects
directly to the linker: archive extraction of fallback-only hooks is unproved.

Controller metadata on Mac is authored in `tools_snapshot/pad_metadata.py` and
emitted by `controltype_gen.py` without game inputs. The current Windows host
selects only `runtime/win/*.c`; it uses translated PC originals through forwarding
hooks for controller prompts and Control Types. It does not compile the Mac
`padprompts.c` / `controltype.c` consumers or consume their three metadata includes.

The host reserves a 4 GiB guest offset space using VirtualAlloc, then commits the
PE image, the 1 MiB main stack, TEB/PEB pages and explicitly allocated guest-thread
stacks. This is reserved address space, not a 4 GiB commit charge. Arbitrary
uncommitted guest accesses still fault; demand paging and guest VirtualAlloc
imports are future work. All guest pointers remain 32-bit offsets from `G`.

Each native guest thread has its own Cpu, stack, TEB and native thread-local Lua
protected-frame chain. Guest FS:[0] holds the translated SEH chain; the native
Windows TEB/SEH chain is never overwritten. The known Lua CRT continuation uses
native setjmp/longjmp after the translated CRT restores guest registers and SEH
frames. Native access violations are not converted into guest SEH exceptions.
Windows CRT unwind behavior still requires native verification.

The GIL is a fair ticket lock built from SRWLOCK/CONDITION_VARIABLE; host sleeps,
event waits and joins release it. Threads have a 64 MiB native stack reservation. There are
32 guest thread slots; completed threads must be joined before reuse. Guest stack
allocations are currently retained in the bounded guest bump arena. Suspension,
termination and guest thread-handle waits are not yet implemented.
TlsAlloc/Free/Get/Set use the guest TEB's 64 TLS slots; last-error
values are per guest TEB. QPC is converted to monotonic nanoseconds without
multiplying the full counter first. Guest QPC frequency is 1 GHz, like the Mac
runtime; host CPUID advertises the same translated instruction surface.

`runtime/win/sync.c` registers `CreateEventA`, `SetEvent`, `ResetEvent`,
`CloseHandle`, `WaitForSingleObject` and `WaitForMultipleObjects` through the
startup adapter seam. This slice supports 128 unnamed events, auto/manual reset,
initial state, zero/finite/infinite timeouts and up to 64 distinct event handles
per wait. Native Win32 events provide atomic wait-all consumption and lowest-index
wait-any results. Guest results are `WAIT_OBJECT_0 + index`, `WAIT_TIMEOUT` or
`WAIT_FAILED`, with per-TEB errors for invalid handles (6), invalid wait arguments
(87), capacity exhaustion (8), and unsupported names/security attributes (120).
Success preserves LastError. Handle generations reject stale references and
retire rather than wrap; blocked waits pin native objects through GIL handovers.
Closing a pinned event invalidates its guest handle immediately and defers native
closure until its pending waits finish. Only event handles are accepted by these
wait/close adapters; the existing internal thread join remains separate.

`recomp/windows_sync_test.py` proves the original kernel handled 0/5 event/wait
imports (regression exit 1), then exercises the registered path, exact stdcall
cleanup, reset behavior, wait-any/all, timeouts, invalid/stale handles, table
exhaustion/reuse, native errors, concurrent waiters and close-during-wait lifetime.
A waiter deliberately retaining the GIL fails its bounded progress check:
progress 0 / result 258 / exit 1; fixed progress 1 / result 0 / exit 0. The fake
native event API uses pthreads on Mac; native SDK execution remains required.

Follow-ons, not implemented by this slice: `CreateMutexA` / `ReleaseMutex`;
`CreateSemaphoreA` / `ReleaseSemaphore`; thread-handle waits and `CloseHandle`,
`GetCurrentThread`, `GetExitCodeThread`, `ResumeThread`, `SuspendThread`,
`TerminateThread`, `ExitThread`, `GetThreadPriority`, `SetThreadPriority`,
`SetThreadAffinityMask`; `DuplicateHandle` and `QueueUserAPC`;
`WaitForSingleObjectEx`, `PulseEvent`, `CreateWaitableTimerA` / `SetWaitableTimer`,
and named/security-aware events. The Mac reference import handlers are in
`runtime/threads.c` (events at 627, mutexes at 643, semaphores at 656, waits at
691, thread controls at 708, duplication at 737), `runtime/heap.c` (heap lifecycle
at 498) and `runtime/boot.c` (VM imports at 1009, handle closure at 1288).

All 300 generated import slots bind to headless thunks. By default, the executable
logs and stops at the first import with exit **10**, before returning into guest
code. Unsupported imports set guest eax=0 / last error=120 and stop with exit 2;
no stub guesses stack cleanup or forwards guest pointers to native DLLs. A small
kernel/time adapter surface exists for offline testing and later boot work.
Generated inline feature dependencies have explicit fail-stop definitions until
their real host implementations are ported. No rendering, audio or game loop is
claimed by this smoke.

## Cross-compile proof on Mac

Use the repository's configured Python interpreter:

```sh
python -I recomp/build_windows.py --host-layer --jobs 6
```

Without `--sdk-headers`, `recomp/windows_compile.h` and `runtime/win/api.h` supply
only declarations. These objects are **compile/symbol proof only**; do not ship
or run them. A runnable build must rebuild everything with real SDK/CRT headers.
The SDK-free x64 jmp_buf declaration is never the authority for native execution.

The build writes `.metal-cache/windows/report.json`, `objects.rsp` and
`symbol-closure.json`. The audit uses llvm-nm on every direct object and decodes
all emitted `/alternatename` directives. It fails unknown symbols and removes
the response file on failure. The exact native symbol allowlist and library
owners are in `windows_symbols.py`; no wildcard permits missing host code.
The SDK-free pre-check also models `_dsign` (UCRT math.h inline lowering) and
`puts` (hosted printf lowering). The real SDK desktop build is authoritative;
the Mac build is a pre-check only. The current Mac freestanding audit compiles
83/83 units with 59 generated / 54 combined external names; hosted O2 compiles
83/83 with 58 / 55. Including the two modeled SDK names, both classify 56 desktop
names and report **0 unknown**. Compiler versions can change counts and object sizes.

Both hosts use `hook_coverage.py`: 273 hooks from hooks.txt/generated prototypes
and 36 inline dependencies from generated calls/header declarations. Host bodies
are discovered in selected source files (including function-generating macros).
The generated `hook-coverage.json` records both hosts' source owners and whether
each hook uses a host body or its original. Windows stages concrete forwarding
bodies for every hook without a Windows implementation; host hooks retain the
strong-definition/alternatename contract. Windows checks actual object symbols;
the Mac runtime build checks actual linked symbols before caching its result.
Missing inline bodies fail either build. No parallel handwritten hook list exists.

All 273 Windows hooks currently use originals. Known gaps include positional
sound/controller geometry (`posaudio.c`, including 004B6030), diagnostic loading
and timeline hooks (`diag.c`, including 006626B0 and 0075B7A0), music input
(`padbind.c`, 004F6EA0), extra music UI (`extratrax.c`, 00511260), and pedal input
identity (`pedals.c`, 006A8870). Inline Windows feature bodies remain explicit
fail-stop implementations, except the no-op diagnostic observers and `traffic_detail_on`, which explicitly
returns Off for this Mac renderer option. New inline dependencies still require
a concrete host body on each platform.

The earlier whole-object regex could read past the end of .drectve, consuming
adjacent binary bytes as part of the last alias target. The six reported names
are exactly the final aliases in their respective objects; their `_orig` bodies
and directives were already present. The audit now reads only the bounded COFF
.drectve section. The offline regression places ASCII data immediately after six
valid directive sections and models the two SDK externals: old rules report eight
unknowns, corrected rules report zero. This tests the layout failure without
requiring the desktop's object files.
Applied to saved 5237ab9d objects, that model reproduces the desktop counts
exactly: external 331 -> 324, after fallbacks 64 -> 57, desktop 49, unknown 8;
the bounded parser and CRT classification give 58 -> 51, desktop 51, unknown 0.

`runtime/offline_tests.sh` discovers `recomp/windows_host_test.py`. It compiles
synthetic guest fixtures with fake native Windows APIs and ASan/UBSan, including
4 GiB boundary commits, invalid PE sections, IAT lookup/binding, stop codes,
QPC conversion, TLS isolation/reuse, Lua unwind and three guest threads doing
6,000 serialized updates. Deliberately wrapping address math and accepting an
unsorted IAT each exit 1; an injected missing COFF host symbol is rejected.

The discovered `recomp/pe_layout_test.py` uses only synthetic headers and zero
padding for the repack layout: unnamed fifth section RVA `0x638000`, virtual
size `0x40e4e`, raw size `0x41000`, SizeOfImage `0x678e4e`, SectionAlignment
`0x1000`. The Windows loader rounds the image to `0x679000`; removing that
rounding fails the fixture. The actual Mac `boot.c` mapping block passes: it
maps 4 GiB and copies raw sections without consulting SizeOfImage. `app.m`
fingerprints code regions without using SizeOfImage. No Mac loader change is
needed. Truncating the Mac raw copy to virtual size fails the same fixture.

## Desktop build and smoke

Use a Command Prompt with VS 2022 Build Tools (C++ tools and Windows SDK/UCRT),
LLVM clang and llvm-nm on PATH, and Python **3.12**. The wrapper calls
`%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat`
once per invocation when `VCToolsInstallDir` is unset, and fails clearly if
the tools are absent. An existing x64 VS tools environment is reused.
The checkout lives at
`C:\nfsmw\port`; copy `recomp\win_build.bat` to `C:\nfsmw\win_build.bat` whenever
it changes. All desktop commands below go through that wrapper. It invokes the
compiler, linker, Python bootstrap and git internally. The `venv` step creates
`C:\nfsmw\venv` with `py -3.12` and installs capstone **5.0.9** and numpy **2.0.2**.
Read the installed Capstone version with `importlib.metadata.version("capstone")`;
the pinned 5.0.9 wheel's `capstone.__version__` reports 5.0.7.
Clone the source repository into `C:\nfsmw\port` using its published Git URL,
or use an existing checkout at that path. To update from a configured remote,
use `fetch` for the configured `mac` remote. Pass another remote name or URL
as argument 2, or set `NFS_FETCH_REMOTE`, to override that default.
The supported executable's section fingerprint is `92079ca1...447f` (v1.3);
generation checks the full fingerprint before translating. Supply the executable
as an argument, or set `NFS_SPEED_EXE`; no machine-specific game location is coded
into the wrapper. Example (quote paths containing spaces):

```bat
C:\nfsmw\win_build.bat venv
C:\nfsmw\win_build.bat fetch
C:\nfsmw\win_build.bat generate "C:\Program Files (x86)\EA GAMES\Need for Speed Most Wanted\speed.exe"
C:\nfsmw\win_build.bat build
C:\nfsmw\win_build.bat link
C:\nfsmw\win_build.bat smoke "C:\Program Files (x86)\EA GAMES\Need for Speed Most Wanted\speed.exe"
```

`apply` runs `git am` and prints HEAD; patch files must be under `C:\nfsmw`.
`fetch [remote]` fetches `main` from `mac` by default (argument takes precedence
over `NFS_FETCH_REMOTE`, which takes precedence over `mac`), fast-forwards the
checkout to `FETCH_HEAD`, and prints HEAD; divergent history fails rather than creating a merge commit.
The wrapper also accepts `boot-prefix`, `check-boot` and `image`, propagates
failures, sets
`RECOMP_CFLAGS=-fms-runtime-lib=static` for the SDK build, passes every object
directly to link.exe, and checks the smoke's output and exit 10. A smoke that
passes returns wrapper exit 0. It never uses `/FORCE:UNRESOLVED`.
After applying a patch, regenerate and rebuild before linking.
For updates that leave translator inputs unchanged, copy the updated wrapper,
then run `fetch` (or `fetch remote` for another remote), `build`, `link`, and `smoke`
with the same executable path. Regenerate whenever translator inputs change.

`check-boot` runs `recomp\windows_boot_test.py --desktop` with the native
Windows SDK/CRT and returns its exit code. Its synthetic fixture exercises the
guest adapters, critical-section contention, aggregate fixed-heap limits and
LastError preservation; it needs no game executable.

The adapter contracts follow the documented Win32 behavior:
[EnterCriticalSection](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-entercriticalsection)
waits for other owners and permits recursive acquisition by the owning thread;
[HeapCreate](https://learn.microsoft.com/en-us/windows/win32/api/heapapi/nf-heapapi-heapcreate)
rounds a fixed maximum to pages and bounds aggregate heap growth;
[HeapAlloc](https://learn.microsoft.com/en-us/windows/win32/api/heapapi/nf-heapapi-heapalloc)
failure leaves LastError unchanged. The bounded startup allocator charges
8-byte guest allocation units against that maximum; it retains allocations
and does not model Windows heap metadata overhead or fragmentation.

`boot-prefix [game-exe]` runs the linked `nfsmw-headless.exe --boot-prefix`
with the same executable argument / `NFS_SPEED_EXE` selection as `smoke`.
It accepts only guest exit **12** together with the exact line
`BOOT PREFIX PASS: 007C4144`, then returns wrapper exit 0. The log is
`.metal-cache\windows\boot-prefix.log`. This reaches the CRT GetCommandLineA
checkpoint before environment/argv initialization or game main. The default
`smoke` retains its first-import stop at exit 10. After rebuilding and linking:

```bat
C:\nfsmw\win_build.bat check-boot
C:\nfsmw\win_build.bat boot-prefix "C:\Program Files (x86)\EA GAMES\Need for Speed Most Wanted\speed.exe"
```

`generate` invokes `build_all.py --generate-only --skip-server --game-exe PATH`.
This mode requires only Python and the pinned decoder packages, with no clang,
xcrun, SDK or emulator. It generates speed C sources, funcs.h and report.txt;
it neither compiles nor builds the optional server DLL. `runtime/imports.c` is
tracked in the same patch/checkout. Native libraries, objects and build-cache
stamps are not portable generation outputs. `--output DIR` allows a separate
comparison directory. On Mac, all **71/71** generated files (69 C files, funcs.h,
report.txt) match the saved pre-change output hashes, including with no tools on
PATH. Generated writes explicitly use UTF-8 and LF on either platform;
successful generation embeds no absolute source paths
or toolchain metadata. Never mix outputs from another commit or executable.

When packaging on macOS, create archives with `COPYFILE_DISABLE=1`, for example
`COPYFILE_DISABLE=1 tar -czf sources.tgz ...`. Source discovery in the shared
build inputs and Windows build ignores names starting with `.`, including
AppleDouble `._x.c` files copied through exFAT or zip. The Mac runtime build uses
an explicit source list, with no source glob.

The exact SDK build routed by `build` is:
`C:\nfsmw\venv\Scripts\python.exe -I recomp\build_windows.py --host-layer --sdk-headers --compiler clang --jobs 6`.
Expected: **COMPILED 79/79; unknown 0**, then a successful native link and smoke.
The loader checks PE32/i386 layout, image base, entry and section bounds;
fingerprint verification during generation remains required.

Expected first output for the current generated table:

```text
win: image loaded, 53636 translated functions, entry 007C4040, 300 imports
win: first import KERNEL32.dll!GetVersionExA slot=008900E0 esp=... (headless stop)
```

The first import is traced statically from entry 007C4040 through its stack probe
to the IAT call at 007C4089. The desktop must confirm this output, exit 10, the
native link, and SDK/CRT ABI and rounding behavior. Do not use `/FORCE:UNRESOLVED`
or add names to the allowlist just to pass the link. No native Windows execution
was performed during the Mac compile proof.

## Standalone translated-shader image

The `image` step builds a separate executable from `runtime/win/image/image.c`.
It creates a fixed 640x480 Win32 client window, a hardware D3D12 device (feature
level 11_0), a direct queue and a two-buffer flip-discard swap chain. One triangle
uses the retail **fe / t0_world_p0_vs_1_1 + t0_world_p0_ps_1_1** pair (412/60
bytes). The original test texture has red/green/blue/yellow quadrants; the
background is dark blue. This exercises position transformation, color and UV
interpolation, an SRV/static sampler, and the pixel shader's multiply/doubled RGB.

`image-mask` builds the same proof from the masked front-end pair (`host/shaders/testdata/fe_mask`: second texture
t1/s1, TEXCOORD1) into `.metal-cache\windows-image-mask` and writes `C:\nfsmw\shots\win6-fe-mask.bmp`.

`host/shaders/sm_hlsl.cpp` consumes the existing `sm::Shader` IR from
`sm_parse.cpp`; it supports this SM1.1 path and rejects unsupported instructions,
registers, modifiers and unsafe coissue dependencies. `runtime/win/image/hlsl.h` documents its
binding contract: VS constants at b0, PS texture/sampler at t0/s0. The build-time
`translate.cpp` tool produces `fe_vs.hlsl` / `fe_ps.hlsl` from the bytecode under
`host/shaders/testdata/fe`. `--image-shader-dir DIR` selects another location for
these same named bytecode files. Missing bytecode fails the build. Golden HLSL
files live beside the private fixtures and are excluded from publication with
the rest of `testdata`; no game program is embedded in the renderer.

On Windows, D3DCompile compiles entry `sm_main` as vs_5_0/ps_5_0 at startup.
D3D12 accepts these shader model 5 programs; this step does not require a DXIL compiler.
The harness uploads an identity WorldViewProj and zero TextureOffset, draws,
copies the rendered back buffer to a readback buffer, waits for its GPU fence,
saves a top-down 32-bit BMP, and presents. The window remains for eight seconds
(Escape/close ends the wait). Capture must contain 20,001..153,599 foreground
pixels; clear-only output fails. Device, shader, pipeline, fence, file and capture
failures return nonzero. There is no guest execution in this executable.

Mac pre-check, using the configured Python interpreter:

```sh
python -I recomp/build_windows.py --image
python -I recomp/windows_image_test.py
```

Outputs go to `.metal-cache/windows-image/`, separately from the headless build.
The build runs the shared parser/HLSL emitter as a native build tool and compiles
all three new C/C++ implementation units to x86_64-pc-windows-msvc COFF. The
SDK-free declarations in `image/sdk_compile.h` and `image/compile/` establish
syntax/type coverage only, **not SDK structure/vtable or STL ABI correctness**.
The two tool COFF objects are never linked into the image. A desktop rebuild
uses the real SDK/CRT/STL for both the tool and image; that build is authoritative.
The image's `objects.rsp` selects only `image.obj`. Its `symbol-closure.json`
rejects unknown native names; `hook-coverage.json` lists each external with its
explicit library owner from `windows_symbols.py`. The standalone program adds
no generated guest hooks or inline dependencies. Mac runtime build inputs and
the parser/MSL emitter are unchanged.

`windows_image_test.py` is discovered by the offline suite. It compares both
retail translations byte-for-byte with their goldens (2/2); a DP4-to-MUL emitter
mutation passes only 1/2 and the same check exits 1. It also exercises the IR
rejection paths, compiles all three units, rejects an injected unknown native
dependency, and checks that a failed rebuild removes stale runnable outputs.
Without private bytecode it exits 3.

On the desktop, copy the updated wrapper to `C:\nfsmw\win_build.bat`, then use
the same x64 VS tools prompt and tool prerequisites as the headless smoke:

```bat
C:\nfsmw\venv\Scripts\python.exe -I -c "import shutil; shutil.copyfile(r'C:\nfsmw\port\recomp\win_build.bat', r'C:\nfsmw\win_build.bat')"
C:\nfsmw\win_build.bat image
```

This step rebuilds with
`build_windows.py --image --sdk-headers --compiler clang`, links the image with
kernel32/user32/d3d12/dxgi/dxguid/d3dcompiler and the static CRT, creates
`C:\nfsmw\shots`, then runs the image harness. Expected: a dark-blue window
showing the colored, textured triangle, exit 0, and
`C:\nfsmw\shots\win3-fe.bmp` (1,228,854 bytes). It must print:

```text
IMAGE PASS: D3D12 retail fe VS+PS; window 640x480; capture complete
```

The log is `.metal-cache\windows-image\image.log`. On any SDK compile/link,
shader compile, device, capture or visual failure, stop and report the first
error and the log; do not relax the symbol audit. A native Windows run and visual
inspection remain pending after Mac checks. Continuing beyond the CRT prefix
still requires environment/argv adapters and integrating the D3D12 backend into the guest D3D9 interfaces.
