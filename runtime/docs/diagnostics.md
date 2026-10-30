# Diagnostic mode, race starts, smoke and reference checks, game slots (part 1 of 3)

Part of the runtime documentation; the index and switch lookup hint are in `runtime/README.md`.

## Car render rejection trace

With `BOOT_DIAG`, `car-reject:` lines in `events.log` sample each car/view/reflection
combination every `BOOT_DIAG_CAR_EVERY` frames (default 120; positive integer).
The diagnostic observes the original Render decisions: adjusted pixel size at
0074E213, visibility at 0074E34B, selected body/tire LODs at 0074E5A2, and the
camera bounding-box result at 0074E694. The outer 0074E160 hook can read bounds,
radius, view threshold and the connected part table, but cannot see these locals;
the four inline observation points and taken false edges supply them. The
diagnostic does not repeat visibility, pixel-size or camera tests.

The line format (after the standard frame/thread prefix) is:

```text
car-reject: frame=N self=PTR car=NAME type=ID view=ID refl=FLAG result=BOOL reason=REASON site=PC bounds=(X,Y,Z)..(X,Y,Z) radius=R pixel=P minpixel=P lod=MIN..MAX chosen=BODY/TIRE visible=STATE camera_inside=BOOL models=L0[SLOT:HASH->found@MODEL/SOLID,SLOT:HASH->null@MODEL/SOLID];L1[...];L2[...];L3[...];L4[...]
```

`reason` is the **first taken false edge**, not a predicted condition:
`pixel-size` (0074E223), `visibility` (0074E34D), `lod` (0074E5B1),
`camera-inside` (0074E696), or `frame-allocation` (0074E2D4/2DC/2E8/2F0).
`none` means Render returned true; `other-false` identifies any uninstrumented
false return. `na` means the original code never reached that observation;
camera-inside is also `na` when the selected LOD, distance, view or NIS condition
skips the bounding-box test. Chosen LODs include the original forced overrides.

Bounds are the instance's local min/max at +90/+A0; radius is +17A8. Pixel size
includes the original special-view and view-mode adjustments. Slots are hexadecimal
part-table indices (body is 00). Each of the five LOD lanes shows nonzero requested
hashes and live connections; an empty lane has neither. `found` requires a live
solid at eModel+0C, not merely an allocated eModel. Null slots recover their
requested hash using the original part-name and special-LOD-range queries, with
the added car's model namespace temporarily restored. These read-only queries
use a private Cpu after Render returns. A lane outside the instance's min/max
can therefore show a requested hash with a null connection; this is not evidence
that the solid is absent from loaded packs. No extra solid lookup is performed.

Sampling state is independent per instance/ride/type/view/reflection on each
render thread, so shadow/reflection calls cannot hide the main view's reason.
The wrapper starts with `if (!diag_on) { f_0074E160_orig(c); return; }`, and all
inline observers/rejection hooks use `if (diag_on)`: off makes no diagnostic
helper calls or guest queries. `BOOT_DIAG_DRAWS` remains independent.

With the installed XK enabled, collect one guarded run:

```sh
runtime/run_guarded.sh 60 /tmp/xkdiag NFS_TEST_PRIVATE_CARS_ROOT="$HOME/Library/Application Support/NFSMW-port/private-cars" BOOT_RACE=15.1.1 BOOT_RACE_CAR=JAGUARXK BOOT_RACE_AI=0 BOOT_AUTODRIVE=1 BOOT_DIAG_CAR_EVERY=120
```

The launcher sets `BOOT_DIAG=/tmp/xkdiag`; inspect `/tmp/xkdiag/events.log` for
`car-reject:` rows for JAGUARXK in view 1. Registration and `cars1` totals remain
in the existing logs. The root override reads the installed approved catalog;
without it a fresh scratch userdata directory has no added cars.
The offline suite autodiscovers `runtime/diag_car_test.py`: it executes the actual
translated Render and production diagnostic wrapper against synthetic car/view
data, with external renderer services replaced. It checks all four rejection
paths, first-false ordering, allocation failure, view exemptions, adjusted pixels,
forced LODs, model connection/hash pairs, namespace restoration, sampling, original
Cpu results and zero calls when off. Swapped-reason, missing-namespace and removed
off-guard controls each exit 1: normal 616/616 checks, swapped reason 2 failures,
missing namespace 151, removed off guard 1 (2 helper entries/11 guest queries/1
log while off, against 0/0/0 normally). This establishes diagnostic correctness;
the XK's live rejection still requires the guarded run.

## Starting a race directly

`BOOT_RACE=1` (or a race ID from `GLOBAL/gameplay.bin`, e.g. `15.1.1`; IDs `N.1.M` are circuits, `N.2.M` sprints) uses the game's own developer switches: `boot.c` writes `SkipFE`, `SkipMovies`, `SkipFERaceID`, `SkipFEPlayerCar`, laps and opponents before the entry point, and `main()` then calls `RaceStarter::StartSkipFERace` instead of loading the front end (retail never reads `online.cfg`). The race is running (game-flow state 6) about 4 s after launch. `BOOT_RACE_CAR` (default `bmwm3gtre46`), `BOOT_RACE_LAPS` (10), `BOOT_RACE_AI` (3 opponents). Add `BOOT_AUTODRIVE=1` for the game's AI to drive the player's car.

Into free roam from a fresh profile (checked 2026-10-20, with `BOOT_SKIPINTRO=1 BOOT_AUTODRIVE=1` and a scratch `BOOT_USERDATA`): `seq:skipmovies,wait:450,RETURN,wait:400,RETURN,wait:400,RETURN,wait:300,RETURN,wait:300,LEFT,wait:60,RETURN,wait:300,RETURN,wait:300,RETURN,wait:300,BACK,BACK,BACK,BACK,T,E,S,T,RETURN,wait:300,LEFT,wait:60,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:400,RETURN,wait:120,LEFT,wait:60,RETURN` (alias TEST, the autosave notice, the safe house's Resume Free Roam, then Left to Yes on "leave the Safe House?"). With `BOOT_AUTODRIVE=1` (and not `BOOT_RACE`) the runtime also dismisses the result popups, so long free-roam runs don't stall: when the FEng package that has control (`cFEng::FindPackageWithControl`, the game's own state) is `PostRace_*` (pursuit result "Bounty achieved", race results, milestone rewards), it presses RETURN once, 1.5 s after the package took control (input focus), once per appearance. After a bust it also dismisses, in order, the first-time "settle your infractions" `Dialog.fng`, `Infractions.fng` (the default choice) and `Car_Select.fng`; the game then lands in the safe house menu (`MainMenu_Sub.fng`), which is a menu and is never driven (continue with `BOOT_KEYS`). Nothing is pressed in any other package; each dismissal logs `autodrive dismissed <package>` (`BOOT_DIAG` also logs every change of the controlling package, `autodrive: package in control`). Test only: `NFS_TEST_BUST=1` (with `BOOT_AUTODRIVE=1 BOOT_PURSUIT=2`) stops the AI driving, so the car sits still until the cops bust it (about 50 s of sim time after the pursuit starts at heat 2; no clean single entry point exists for forcing the busted state), to exercise the busted flow; setting the heat to 5 is a faster alternative.

Career runs: `BOOT_SKIPINTRO=1` sets the game's own `SkipCareerIntro` switch, so a new career skips the prologue (the D-Day races) and starts in the safe house with the career cars; set it on every run of that profile (the game checks it again when free roam starts). `BOOT_UNLOCKALL=1` sets `UnlockAllThings` (every car, part and Challenge Series event). `BOOT_PURSUIT=<heat>` starts a pursuit 10 s into free roam or a race through the game's script functions (`Game_SetWorldHeat`, `Game_ForcePursuitStart`), and again every minute; `BOOT_RACE_COPS=1` (test only, inert without it) lets cops exist in a `BOOT_RACE` (clears SkipFEDisableCops 008F86C0 and calls `Game_SetCopsEnabled(true)` 00604F40 before the pursuit starts; a plain `BOOT_RACE` has none, so `BOOT_PURSUIT` there shows the heat but no cop cars); the career caps the heat (2 at Blacklist 15). Scripted runs (`BOOT_KEYS`) ignore the physical keyboard and mouse.

                
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       
                 

## Save commits and offline regression checks

Guest save/profile writes under `USERDATA` (`BOOT_USERDATA` in tests) use a unique sibling
`<filename>.nfs-save-*` file. The destination changes only when the last writable guest handle closes:
fsync the staged data, close, then rename over the old file. FlushFileBuffers really fsyncs;
SetEndOfFile truncates at the current position. Non-truncating opens preserve the old bytes, and
compatible shared opens see the staged file with independent positions. Other guest paths keep direct writes.
A failed write/flush/commit preserves the old destination. An interrupted process can leave a sibling temp file.

                
                                                                              
                                                                                                             
                                                                                                         
                                                                                                              
                                                                                                                                                                                                                                                                                                                                                                                                                                                                  
                 

                
                                                                                                            
                                                                                                   
                                                                                                          
                                                                                                            
                                                                                               
                                                                                                        
                                                                                                                    
                                                                                             
                 

                
                                                    



                                                                                                                        
                                                                                                                     
                                                                                                                    
                                                                                                                   
                                                                                                                      
                                                                                                                        
                                                             


                                                                                                                    
                                                                                                                     
                                                                                                                   
                                                                                                                     
                 

## Diagnostic mode

- `BOOT_BACKGROUND=1` (test-only; default unset/off): hides the Dock icon, keeps the window behind other apps without taking focus, and prevents App Nap; any set value enables it.
- `BOOT_DUMP_CODE=<existing-directory>` (test-only; default unset/off): dumps discovered guest runtime-code regions as `code_<hash>_<address>.bin`.
- `BOOT_DIAG_WATCH=<hex-address>,...` (test-only; default unset/off): watches up to 16 guest dwords at every import call and logs changes, the import and guest return address.
- `BOOT_WATCH=<hex-address>,...` (test-only; default unset/off): alias for `BOOT_DIAG_WATCH`; the latter takes precedence when set.
- `BOOT_TEXLOG=1` (test-only; default unset/off): logs each CreateTexture request's size, levels, usage, format and pool to stderr; any set value enables it.

One run records everything needed to diagnose it afterwards (prefer this to adding a log and rerunning):

```sh
BOOT_DIAG=/tmp/diag BOOT_DIAG_DRAWS=5600,6400 NFS_AUDIO=null ./boot "/path/to/game"
```

| File | Contents |
|---|---|
| `events.log` | imports and COM calls: frame, thread, name, arguments, result, call site; resource frees; game-flow changes; calls to D3D methods that are no-ops here and clears that don't cover the whole target. Per-frame calls (D3DX math, sync, heap, most COM methods) go to the in-memory ring only (`BOOT_DIAG_EVENTS=all` writes them too) |
| `frames.log` | one line per frame: draw count, time, bump heap, the game's sim tick counter (`sim`: a frame that adds no tick shows the same picture again) a hash of the view matrix, `simtime` (Sim::GetTime), `simspeed` (the sim time scale) and `camrate` (the view camera's SimTimeMultiplier, one input of the scale); game-flow state changes (3 front end, 4/5 loading, 6 racing) |
| `overlay.log`, `overlay_NNNNN_{compact,full}.png` | with the overlay on: its panel text once a second; for `BOOT_DIAG_SHOTS` frames, both panels as drawn (PNG with alpha) |
| `stats.log` | once a second: fps, frame time p50/p99/max, `busy` (the game thread's ms per frame without its sleeps, GIL hand-overs and drawable/GPU waits, i.e. the CPU cost of a frame; then those waits), `shown` (intervals between frames the display actually showed, from drawable presented times: <12 ms / 12-20.5 / 20.5-29 / longer / not shown), process RSS, bump / resource (`gmalloc`) / guest heap use and peaks, live GPU textures and buffers, how long Present waits for the GPU (the previous frame) and the GPU time per frame, `car` (the player car's speed in m/s, -1 without one: `diag_car_speed`, the sound engine's copy of it, SndCamera::m_PLayerCars 009142D0 -> EAXCar +0x34 -> EAX_CarState velocity +0x50, 004AB890; set whoever drives), game-flow state. `mixrms`: the device mix RMS per channel FL FR C LFE BL BR over the second (its own window, `dsound_mix_levels`) |
| `mem.log` | every 10 s, where the process memory is (2026-10-21                                                          ): `rss`, `footprint` (phys_footprint, what Activity Monitor shows; includes the GPU memory the process owns; rss is not a growth measure: the compressor takes idle guest pages under memory pressure), `malloc` (host heap, all zones), `metal` (MTLDevice.currentAllocatedSize), the guest heaps; render.m's Metal copies of guest textures (`tex` count MB), render targets (`rt`), `msaa`, static buffers (`buf`), renames/uploads and its caches (shaders, pipelines, depth-stencil and sampler states); the guest's D3D objects by kind as live/retired/with a Metal copy and their guest MB (`hd`: X360 HD swaps among the live textures), vinyls and skins. "GPU textures" in stats.log (`gputex`) are the Metal copies of guest textures and render targets: made at a texture's first draw (`gpu_tex`, render.m), freed when the guest's count reaches 0 (`render_release` from `obj_release`, d3d9.m), so at most one per live guest texture |
| `fake_tv.log` | with `NFS_AUDIO_FAKE_DEVICE=1`: media's fake TV receiver (`media.cpp` FakeRx), per output: `open` (mode, rate, device), one `sec` line a second and a `close` total: IEC 61937 bursts, decoded frames, junk between bursts, wrong data types, decode / TrueHD timing errors, frames decoded bit-exact to what the mixer wrote vs mismatched (TrueHD), samples at full scale and not finite, per channel RMS and peak (FL FR C LFE SL SR) |
| `heap.log` | once a second (while anything allocates): RSS, and for the process heap and the resource heap: live MB and blocks, pages used, peak, high-water mark, allocs/frees/reallocs, bad frees |
| `heap_callers.txt` | every 30 s, the top 25 allocation sites by bytes: `galloc`/`gmalloc` by host function, `HeapAlloc`/`VirtualAlloc` by game function (cumulative: compare two snapshots to see what grows) |
| `events.log` `gchar:` | race characters (the drag trafficspawntrigger cars) as GCharacter::Spawn asks for them: position, speed, state, and the spawn list's states as they change (diag.c `gchar_watch`; AITrafficManager::SpawnTraffic is the ambient traffic, `attrib_x360: traffic car spawned`) |
| `events.log` `nis:` | BOOT_DIAG only: NIS Load/Play scene name/hash, then one sample per animated actor at least 0.25 s of scene time apart (512/actor/play, 16384/run; 64 metadata lines/run). Includes vehicle/control, animation sample/rate and local native pose, supplied dt and interval call/dt totals, original rigorous ground query/result/height, initialization flag/return/counter, placement target, actual rigid-body position/vertical velocity, grounded wheels, car drivable zone/scenery loaded state, and actual collision-grid cell's referenced packs as `section:resident`. Also `sim`, `simtime`, `simspeed`, `camrate`, wall `t` for frames/timeline correlation. No extra ground query or streaming request; no NIS trace work when diagnostics are off |
| `calls.txt` | calls so far per import and per COM method (rewritten every 30 s) |
| `frame_NNNNN.jpg`, `frame_NNNNN.png` | overview shots every `BOOT_DIAG_SHOT_EVERY` frames (default 3600, 0 = none): JPEG at most 960 wide (~150 KB); frames asked for with `BOOT_DIAG_SHOTS` are full-size PNGs, encoded on a background queue after the frame's GPU work (no game-thread stall) |
| `hd_compare/` | only with `BOOT_DIAG_HDCOMPARE=1`: an image per HD texture verdict (PC / X360 colour and alpha), ~300 per track |
| `draws_NNNNN.txt`, `tex_NNNNN/` | for the frames in `BOOT_DIAG_DRAWS` (frame numbers, `race+N`: N frames after the race starts, `key+N`: N frames after the last scripted key or test switch, or `probe`: the screen-reach probe's base and signature passes): every draw (effect/technique/pass, shaders, vertex layout, streams, textures, samplers, states, every VS/PS constant the shaders read with its parameter name; fixed-function draws and their state) interleaved with every COM call of the frame (`call` lines: render targets, copies, clears, effect calls), and every sampled texture as PNG; `car:` lines name every CarRenderInfo::Render (view, flags, reflection flag, LOD range) and RenderPart (part slot, LOD, model, matrix up axis and position) before the flush that draws them |
| `seq_NNNNN.png` | `BOOT_DIAG_SEQ=<frame or race+N>:<count>` (up to 64): consecutive back buffers, kept in memory and written after the last one so the frames themselves aren't slowed down (frame pacing, what changes between frames) |
| `frame_NNNNN.png` with `NFS_TEST_OCCLUSION=ab` | test only: same-camera sun occlusion A/B in one run: 12 frames with every occlusion query answering the old placeholder (1000 pixels, sun always visible), then 12 with the real counts, a full-size shot at the end of each; once on the open road (race frame ~1500) and 20 frames into each of the first 4 tunnels/underpasses (the game's flag 0x982BA4). `events.log` has `render: occlusion queries` totals every 300 frames with the sun test quad's clip-space position, `render: occlusion query hidden/partial/visible` on each change, and the tunnel entries (`run_shots/sun_occlusion_queries_ab.jpg`) |
| `shots_NNNNN/` | in those frames, render target 0 after every `BOOT_DIAG_DRAWSHOT`-th draw (default 25, 0 = off): find the draw that changes the picture |
| `stall_N.txt` | a frame later than `BOOT_DIAG_STALL` ms (default 500; after frame 600; up to 5): the same contents as a hang report, taken while the stall lasts |
| `hang_N.txt` | no frame for `BOOT_DIAG_HANG` s (default 5): every thread's host backtrace with the game's function names (from `decomp/functions.tsv`), each guest thread's registers and stack and what it is blocked on (`EnterCriticalSection(addr)` and the owning thread, or the handles it waits for with their state), the last 4000 events (file opens with their names: `CreateFileA(name, access, disp) -> ok/error`). A hang that lasts is reported again 10 s and 20 s later: a thread whose stack changes between them loops, one whose stack stays is blocked |
| `crash.txt` | the same on a crash signal, an unimplemented import/method, or a runtime error (without `BOOT_DIAG`: `~/Library/Logs/NFSMW-port/NFSMW-crash.txt`) |
| `movies.log`, `movie_<name>_*.png` | each movie: X360 WMV or VP6 fallback (and why), decoders, texture size and layout; at close: video frames decoded/shown/dropped, audio frames decoded/played/silence/skipped, per-channel RMS of the movie audio and of the device mix. One PNG of the movie texture per WMV movie, ~3 s in |

`BOOT_DIAG_POSTFX_AB=1` (with `BOOT_DIAG_SIMSHOTS`): each simshot also as `simshot_<t>_x360.png`, the same picture through the X360 post chain (the pass after the shot is held: no tick, no real time; `=2` keeps the held pass on PC's chain, to check the hold; `=3` adds `_x360look` (`NFS_VISUALLOOK=x360`), `_x360input`, `_x360t` and `_x360magenta` debug passes); `NFS_VISUALLOOK=x360`: the X360 chain with the X360's own visuallook data (tint, curve, intensities); `postfx.log` has the X360 chain's per-frame luminance, adaptation and exposure (x360 or A/B runs). `=4` (post audit                                                          ): after each shot's `_x360` pass, ten more held passes of the same frame with one X360 stage off each, `_x360_no<stage>.png` for speedblur, dof, bloom, curve, curvelocal, vignette, adapt, all, smoothtaps and scaledglow (below); full-size `BOOT_DIAG_SHOTS` frames (`frame_NNNNN.png`, the front end has no sim time) get the same set. One race launch at 4K with six simshots writes ~60 PNGs (~0.6 GB). **`NFS_TEST_POST_SKIP=<stage>[,<stage>...]`** (test only, `x360_post.m`): the X360 chain with stages off: `speedblur` (radial speed blur), `dof`, `bloom`, `curve` (tone curve and desaturation: identity grade), `curvelocal` (the curve driven by the pixel's own luminance instead of the quarter-res 16x16-pixel blurred one), `vignette` (brightness and additive vignette; the texture's blur masks stay), `adapt` (exposure fixed at the adaptation's equilibrium, x1.99), `all` (the composite outputs its input scene), `smoothtaps` (the speed blur in the X360's 7 taps at any resolution), `scaledglow` (the tone curve's luminance at the fixed 16 pixels, `NFS_CURVE_GLOW=tight`); with `NFS_POSTFX=pc`, `motionblur` skips PC's motion blur (FUN_006dbb20). With `BOOT_DIAG`, `events.log` has `x360 post: input WxH (N samples) -> output ...; chain ...` (the chain's target sizes, when they change), `samplers:` every 600 frames (mipmapped texture binds by min filter: anisotropic and how many at 16x, linear, point; mip filter below linear; binds with a MIPMAPLODBIAS set), `lod bias:` (the first 16 distinct sampler / bias / caller / effect pass that set one), `x360 post: speed-blur taps N, tone-curve luminance WxH (glow G)` when they change, and the vignette texture `UVESVIGNETTE` once in `tex_NNNNN/` (the PNG's colour channels come out black where its alpha, the speed-effect mask w, is 0, so the x/y/z masks can only be read where w is high).

                
                 
