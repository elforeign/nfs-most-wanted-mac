# port/runtime: the native runtime

`boot.c` loads the user's `speed.exe` into guest memory at its own addresses and builds a TEB (`fs:`), a 1 MB stack and a bump heap. It points every IAT slot at a synthetic thunk, then calls the translated entry point (`007C4040`) from `../recomp/out/speed.a`. `imports.c` (generated) maps IAT slots to `DLL!function`.

```sh
python3 ../recomp/build_all.py   # with tools/.venv/bin/python: translate + compile the game
./build.sh                       # link boot
BOOT_TRACE=1 BOOT_TIMEOUT=10 ./boot "/path/to/Need For Speed Most Wanted Black Edition"
```

                
                                                                                       
                                                               
                                                                                                                                                                                                                                                                                                                                                                                                                                                            
                                                                         
                 

Win32 subset: version, heap (`heap.c`: size-class slabs plus page runs, bookkeeping host-side; process heap at 0x10000000-0x30000000, resource heap for `gmalloc` at 0x50000000-0x7F000000; standalone test: `cc -O2 -DHEAP_TEST runtime/heap_test.c runtime/heap.c`) and virtual memory, TLS, critical sections, startup info, std handles, command line, environment, code pages and string mapping (Latin-1), locale stubs, time/performance counters, interlocked ops, events, mutexes and semaphores, threads (pthreads under a guest lock, `threads.c`), the game's registry key (below), files (paths resolved case-insensitively under the game folder; `USERDATA` is `~/Library/Application Support/NFSMW-port`), `cpuid` (a generic Pentium 4 class CPU; `rdtsc` ticks at 3 GHz). D3D9 + D3DX effects on Metal (`d3d9.m`, `render.m`, `d3dx_effect.c`), DirectSound on the host mixer (`dsound.c`), DirectInput keyboard/mouse, USER32 on Cocoa (`user32.m`), X360 movies (`movies.c`).

The game boots, plays the intro movies, runs the front end and races (2026-10-19: one frame per sim tick at the display's refresh rate, 120 fps on ProMotion, at performance level 4 with diagnostics on; ~3 ms of CPU per frame; see Frame rate and timing).

Examples using `TOOLS_PY` or `NFS_RUNS` assume `runtime/env.sh` has been sourced from a runtime script; from the checkout root, set `TOOLS_PY="$(pwd)/../tools/.venv/bin/python"` and `NFS_RUNS="$(pwd)/../.runs"` (or use your configured paths).


## Area docs

The detailed notes live in `runtime/docs/`, one file per area (each at most 25 KB; a budget test enforces it). Text was moved
verbatim; a part number (`render-2.md`) continues the file before it. To find a switch or a setting by name:
`grep -rn NAME runtime/docs` (and `VERSIONS.md` for the PC/X360 choice and who decided).

- `docs/app.md`, `docs/app-2.md`: the app and `bundle.sh`; the F10 settings menu; Live changes and Restart game (switch class table, offline proof); problem reports and the session log; focus loss; registry settings; movies; the asset-root manifest.
- `docs/frontend-gameplay.md`: pause-menu Time Of Day and Brightness sliders; gameplay options (heat cap, starting bonus, Open Map, skip movies, Black Edition content, Unlock All).
- `docs/online-lan.md`: online race stage A1 (`ws2_32.c`), LAN and loopback.
- `docs/perf.md`: frame rate and timing (`pacing.c`), CPU cost of a frame, shader compiles.
- `docs/input.md`, `docs/input-2.md`: game controller (PlayStation pads, button prompts, rumble, adaptive triggers, chase camera, `BOOT_PAD` simulation).
- `docs/audio.md`: audio mode, engine sound and soundstage, positional audio, audio-loop watchdog, Extra EA Trax.
- `docs/render.md` to `docs/render-6.md`: X360 renderer features in order (shadows, HD textures, X360 sound mix and music, car types, arrest scenes, post-processing, mirror, water, glass, lights, rain, bloom, HDR, look defaults), then widescreen, antialiasing and display changes mid-run (render-6).
- `docs/switches.md`: the switch registry (`runtime/switches.tsv`, its test) and the switches documented there.
- `docs/diagnostics.md`, `-2`, `-3`: starting a race directly, save commits and offline regression checks, game slots and the launch ledger, Diagnostic mode (`BOOT_DIAG`, probes, reference frames, audio and pad smoke, the single-launch gate), the CarLoader startup-hang notes.
