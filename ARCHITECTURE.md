# NFS:MW native Apple Silicon port: architecture

Goal: run the user's own PC `speed.exe` v1.3 natively on Apple Silicon with Metal rendering and CoreAudio output, then add X360 features from the player's own Xbox 360 copy when they provide one. This is our own codebase; it uses the CC0 symbol names for `speed.exe` and our own decompilation notes, no third-party source.

## Why static recompilation

A source port needs all 52,043 functions decompiled to compilable, correct C first. That is years of work, and only a fraction of the game is matched anywhere. Static recompilation translates the *machine code* of `speed.exe` to C mechanically, so the whole game runs from day one. Hand-written C++ can then replace functions one at a time (renderer, audio, X360 features) where it pays off.

Measured on `speed.exe` (Ghidra boundaries, capstone):

| | |
|---|---|
| Functions | 52,043 |
| Instructions | 1,192,442, 243 distinct mnemonics |
| x87 FPU | 85,875 (7 %) — the game's float math |
| MMX / SSE2 integer | 10,391 (codecs: VP6, audio) |
| SSE float | 231 |
| String ops (`rep movs`…) | 2,032 |
| Imports | 300 functions in 13 DLLs (KERNEL32 157, WS2_32 31, USER32 41, WINMM 24, D3DX9_26 13, GDI32 12, TAPI32 9, ADVAPI32 6, DSOUND 2, d3d9/DINPUT8/SHELL32/SHFOLDER/NETAPI32 1 each) |

## Layers

```
            speed.exe (user's own copy)                    X360 disc (user's own copy)
                 │ recomp (build time)                            │ Setup's converters (tools_snapshot/x360_tex.py ...)
                 ▼                                                ▼
   generated C: one function per x86 function          X360 movies / vinyls / HD textures / effects
                 │
   ┌─────────────┴───────────────────────────────────────────────────────────────┐
   │ runtime: guest memory (image at its PE addresses), CPU context, x87 emulation, │
   │          indirect-call dispatch, SEH/exceptions, threads, import thunks        │
   ├────────────────────────────────────────────────────────────────────────────────┤
   │ host APIs: Win32 subset (kernel32/user32/advapi32/winmm…), files (case-insens.)│
   │   d3d9 + d3dx9 effects → Metal      dsound → CoreAudio (6 ch)      dinput → GameController │
   │   movies: FFmpeg (VP6 + X360 WMV3/WMA Pro)     AC-3 encoder → IEC 61937 bitstream         │
   └────────────────────────────────────────────────────────────────────────────────┘
```

### Recompiler (`port/recomp`)
- Input: the PE, function boundaries (`decomp/functions.tsv`), and jump tables found by following `jmp [table + reg*4]`.
- Output: C. Registers live in a `Cpu` struct; guest memory is one flat 4 GB reservation with the image at its original addresses, so guest pointers are plain offsets (`MEM(a)` = `base + a`).
- Flags are computed lazily: only for the instructions whose flags are actually read (jcc/setcc/adc/sbb/cmov/pushf), inside one basic block.
- Control flow: labels and `goto` inside a function; direct calls become C calls; indirect calls and `jmp reg` go through a guest-address → host-function table.
- x87: an 8-entry stack of `double` with TOP, tag word and status/control words. Precision control matters: Direct3D 9 switches the FPU to 24-bit precision unless `D3DCREATE_FPU_PRESERVE` is set, so results are rounded to float when PC = 00, like the original. The port does not switch the precision (53-bit, CW 0x027F); `d_CreateDevice` only masks all exceptions like D3D does, since the CRT's start-up CW 0x0232 leaves invalid/divide-by-zero/overflow unmasked and a NaN in the sun-flare `pow` (hood camera, frame-timing dependent) then raised a hardware exception.
- MMX: `uint64` lanes in the `Cpu` struct.
- **Verification:** differential tests run each translated leaf function and the original code under Unicorn on the same random register and memory state, and compare registers, flags and written memory (`recomp/difftest.py`; it needs a developer x86 emulator that a public checkout does not include, and skips without it).

### Host layer (`port/host`)
- **Graphics:** D3D9 COM interfaces implemented on Metal. The fixed-function state the game sets plus D3DX effects (fx_2_0 binaries in `speed.exe` resources: 31 effects). Shaders are SM1–3 bytecode translated to MSL at load. The X360 renderer features are then implemented natively at this layer or as C++ replacements of the matching game functions.
- **Renderer file map** (`runtime/`; one translation unit per owner, shared state in `render_internal.h`, whose externs are the former file-scope statics of `render.m`; D3D9 COM side in `d3d9.m` / `d3d9_internal.h`, the public renderer API in `host.h`):
  - `render.m`: the draw core. Texture/buffer creation and renaming, pixel formats, setup/resize, command buffer and render passes (`begin_pass`, `end_pass`), shader translation cache and async compile, vertex input, pipeline/depth/sampler state, `render_draw` with the fixed-function shaders, copies (`StretchRect`, readback) and the diag draw/texture dumps, the upload ring and the frames-in-flight serials.
  - `render_target.m`: render targets and MSAA: sample counts, multisampled textures, `msaa_resolve`, multisampled attachments, depth/colour sample-count match.
  - `render_present.m`: Present (`render_present`, the presenter queue, drawable handling), frame timing and present histogram, diag capture encoding off the game thread.
  - `render_post.m`: the X360 post chain, mirror pass and lens raindrops as host Metal passes (`render_x360_post`, `render_x360_mirror`, `render_x360_raindrops`; driven by `x360_post.m`).
  - `render_query.m`: D3D9 occlusion queries on Metal visibility results.
  - `materials.m` (material-shader table, below) and `probe.m` (screen-reach probe) hook into the draw core and Present.
- **Material shaders** (`runtime/materials.m`): X360 material looks that need a different pixel shader. A table maps a game shader's bytecode hash (FNV-1a 64, the key `render.m` caches with) to hand-written MSL that keeps the translator's binding contract; `compile_entry` compiles it instead of the translation (falling back to the translation if it fails), and the draw binds it from the translation's reflection plus fragment `buffer(4)` with constants the game never uploads (filled per draw from hooks on the game's setters and the device's VS constants). Each payload has its own switch. A new payload = a table entry, its MSL, and a setter hook if needed. Payload 1: X360 car paint specular (`NFS_CAR_PAINT`, hook on the eLightMaterial setter FUN_006c76e0). An entry may instead patch the translation (vertex or pixel shader; anchors not found -> the translation) and ask for an extra texture at fragment texture(15). Payload 2: X360 rain ripples on wet roads (`NFS_RAIN_RIPPLES`, worldreflect VS t0/t1 + PS t0-t5 patched, RAINSPLASH frames from the X360 GLOBALB.BUN).
- **Audio:** DirectSound buffers mixed into a 6-channel CoreAudio output. The game's own Snd engine is put in its discrete 5.1 mode (`Snd::System::SetOutputMode(3)`), so the mix is the original 5.1. Output modes: PCM 5.1 (lossless, default), stereo fold-down, and Dolby Digital 5.1 (FFmpeg AC-3 → IEC 61937, macOS `kAudioFormat60958AC3`).
- **Movies:** the PC movie player (`MoviePlayer::*`) is replaced natively with FFmpeg decoding. It prefers the X360 720p WMVs with their 5.1 WMA Pro soundtracks, falling back to the PC VP6 files.
- **Input:** DirectInput 8 → GameController / HID.
- **Files:** game-relative paths resolved case-insensitively in the user's game folder; X360 assets in a separate folder.

## Status (4.0)

| Part | State |
|---|---|
| Decompilation, symbols, X360 analysis | done (kept outside this repository; the build needs only the committed function manifest) |
| Recompiler (`port/recomp`) | 53,664 functions, 2 stubs (junk bytes); MMX done, SSE2 traps in place if reached (cpuid hides it). Host replacements of game functions through `recomp/hooks.txt`. Difftests: 333/333 synthetic instruction cases, sampled leaves all pass where translatable |
| Runtime (`port/runtime`) | boots, plays the intro movies, runs the front end and races; `BOOT_RACE` starts a race directly. Real guest heap (`heap.c`), released D3D resources destroyed, memory flat in races. Settings persist in `USERDATA/registry.txt`; the first run detects performance level 4 |
| D3D9 → Metal | all 108 effect shaders translated; fixed-function draws get synthesized shaders; hardware shadow maps (depth textures + compare samplers) wired but not yet verified visually; full visual treatment / reflections / env maps / motion blur at level 4. ~3.5 ms of CPU and ~2.3 ms of GPU per race frame (perf branch); the sim ticks at the display refresh rate, one frame per tick (`pacing.c`). No-ops: `UpdateSurface`, `GetRenderTargetData`, `ColorFill` (the game doesn't call them) |
| Audio (`host/audio`, `runtime/dsound.c`) | DirectSound → 6-channel CoreAudio mixer; the game's Snd engine runs in its discrete 5.1 mode; PCM 5.1, stereo fold-down, Dolby Digital 5.1 and E-AC-3 / TrueHD passthrough outputs |
| Movies (`runtime/movies.c`, `media/`) | X360 720p WMV + 5.1 WMA Pro through the game's `MoviePlayer`, PC VP6 fallback |
| Input | DirectInput keyboard/mouse; game controllers through GameController (console-style button prompts, rumble, adaptive triggers) |
| App (`runtime/app.m`, `bundle.sh`) | `NFSMW.app`: game folder panel (checked against the translated `speed.exe`'s code fingerprint, `recomp/exe_fingerprint.py`), full screen / windowed, Quit through the game's exit path, pause on focus loss, logs in `~/Library/Logs/NFSMW-port`, audio output picked for the device |
| X360 assets | HD vinyls, HD textures, movies, audio mix and more from the player's own X360 copy (`runtime/README.md`, `HD_VINYLS.md`) |
