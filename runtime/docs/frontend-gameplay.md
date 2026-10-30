# Pause-menu sliders and gameplay options

Part of the runtime documentation; the index and switch lookup hint are in `runtime/README.md`.

**Problem reports** (`report.m`): `<userdata>/reports/<YYYYMMDD-HHMMSS>/` (a `-2` suffix for two in one second; the saved path shows in the menu):
- `report.txt`: the summary first: when, category, note, reason (the F10 menu, or the crash / hang), build id (commit, `+` if built with local changes, build time) and the speed.exe hash, game state (game-flow, frame, seconds since start), display (screen, pixels, Hz, backing scale), render (back buffer, MSAA, post chain, shadows, car paint), audio (output format, device, underruns), controller and layout, the last minute in numbers (fps mean/min, worst p99, GPU mean), the settings that differ from their defaults or are set by a switch, and the file list;
- `screenshot.png`: the game frame when the menu opened (full size, without the menu);
- `stats.txt`: one line a second for the last 3 minutes: time since start, frame, fps, p50/p99/max frame ms, game-thread busy ms, GPU ms, game-flow state, mix RMS per channel;
- `events.txt`: the notable events of the run (game-flow changes, frames over 500 ms, audio output and default-device changes, underruns, setting changes, menu use, reports, hangs, crashes), then the last 400 lines of the diagnostic event ring;
- `log.txt`: the last 256 KB of this run's log (stderr, when it goes to a file: `NFSMW.log` in the app);
- `settings.txt`: every setting, its value and source (`default`, `app.txt`, `env NFS_X=value`);
- `crash.txt`: crash and hang reports only: the diagnostic report (every thread's backtrace, guest stacks, events).
The stats and events rings are always on, in normal play too (a few stores a frame, one small computation a second), so a report has the last minutes without `BOOT_DIAG`. A crash (`crash.txt` from the signal handler or a fatal runtime error) or a hang (diag mode's `hang_N.txt`; without `BOOT_DIAG` a watchdog reports no frame for 15 s while the game isn't idle on purpose) writes a report folder by itself, category Crash-freeze (at most 3 a run).

**Session log** (`report.m`, always on, no `BOOT_DIAG`): every run writes `~/Library/Logs/NFSMW-port/sessions/<YYYYMMDD-HHMMSS>/` (under `BOOT_USERDATA` when that is set, so launcher runs land in the scratch profile; the newest 20 are kept), so a session can be inspected without a test launch:
- `session.txt`: the setup (build, macOS, Mac model and chip, display pixels / Hz / scale, back buffer, MSAA, post, shadows, power source, thermal state, settings that differ from their defaults), then every `report_note` as it happens (game-flow, settings, display / power / thermal changes, audio output, device and underruns, menu use, stalls), a `HITCH` line per frame over budget, and a `SUMMARY` per game-flow state every 20 s, then every 60 s, and at a normal exit (a crash or hang writes one too; a SIGTERM does not, so the last periodic one stands).
- `perf.tsv`: a line a second (header first): fps, frame ms p50/p99/max, busy ms p50/p99 (game thread minus waits), mean idle ms (sleep, GIL, drawable wait, GPU wait), GPU ms mean/p99, draws and sim ticks per frame, frames over budget, RSS MB, player car x/y/z and speed (0 outside a race), whether the F10 menu is open, the presentation counters, then the device mix of that second: RMS per channel `rms_fl rms_fr rms_c rms_lfe rms_sl rms_sr` and peak per channel `peak_*` in dBFS (-120 = silent; empty while a movie plays, which reads the mixer itself), and `clip`: samples (all channels) at or over full scale (>= 32767/32768) before the mixer's clamp, then `dip_fl .. dip_sr`: per channel the RMS of the quietest 50 ms stretch of the second in dBFS (a cutout shorter than a second shows here, not in `rms_*`). Each stats reader (this pass, BOOT_DIAG's `mixrms`) folds the mixer's one window into its own accumulator (`dsound_mix_levels`, `runtime/dsound.c`), so both see the whole second. From build 1592 the last columns are `thermal` (0 nominal .. 3 critical, -1 unknown), `low_power`, `app_active`, `occluded`, `fullscreen` and `gpu_wall` (mean ms from the command buffer reaching the GPU queue to its end, against `gpu_mean` = GPU start to end); `session_report.py` lists "GPU slow while draws unchanged" stretches (gpu_mean > 2x the median at +-25% draws) with them.
- A hitch is a frame over the budget (1.5x the interval: the display's, or the sim rate when lower) and over 1.5x the previous second's median (so a GPU-bound run is not all hitches; the over-budget share still counts every such frame), after frame 600 and not while the game idles on purpose. Each `HITCH` line: frame ms, busy, idle split, GPU ms (the frame's, found by serial, the largest of three neighbours: approximate), ticks, state, the causes of this frame or the one before (`compile`: shaders / pipelines made or draws deferred, `flow`: game-flow change, `menu`, `audio`, `catchup`: more than one sim tick, else `unknown`), the shader / pipeline / deferred deltas and the car position. At most 5 lines a second, then a count; the SUMMARY counts each hitch under its first cause. Streaming / section loads are not attributed: the TrackStreamer has no counter that is cheap without `BOOT_DIAG`.
- Cost: per frame a few loads and counter increments (`report_frame`: state histograms, the render counters, one ring copy for a hitch); every write is in the once-a-second pass or a note, never per frame.
- Full-screen changes: `fullscreen: mode=Borderless|Native full=0|1 owns_space=0|1 gpu_mhz=... (cached)` is written once per completed window change to the runtime and session logs, with adaptive refresh requested/applicable. The nonblocking `gpuclients` sample is up to three seconds old; `-` means unavailable. `session.txt`'s `native_fullscreen` comes from the actual Cocoa style. Native entry/exit refreshes the existing display, VRR-request and frame-limit checks without enabling Variable refresh pacing itself. Repeated toggles during the Space animation are ignored. Offline proof: `nativefs_test.py` uses fake windows and the real presentation policy; missing Space requests and stale presentation flags fail its controls.
- GPU-client telemetry (`gpuclients.m`): appends `gpu_dev_util`, `gpu_self_ms`, `gpu_other_ms`, `gpu_top`, `gpu_mhz` after `gpu_wall`, preserving every earlier column. A utility-priority background sampler reads AGX via IOKit once a second; the frame path only copies a cached result without waiting. `gpu_dev_util` is Device Utilization %. Client times are deltas of summed AppUsage accumulatedGPUTime (ns), normalized to ms/s using the measured interval; self is this PID, other is every other PID. Baselines use registry entry IDs, so newly seen clients, PID changes and counter resets contribute no lifetime totals. `gpu_top` holds the top three other process names and ms/s (`name:ms,name:ms`, grouped by name); TSV/grammar separators in names become `_`. Missing counters, the first sample, and caches older than 3 s produce `-`.
  `gpu_mhz` uses optional dlopen/dlsym IOReport GPU Stats / GPU Performance States residency deltas: the residency-weighted mean of explicitly labelled MHz/GHz states, excluding idle/off states. Opaque names produce the dominant active `state=NAME` instead; missing library/symbols/subscription/data produce `-`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         
  Slow-stretch reports print mean other ms/s, top other names by total recorded ms (only each row's top three are available), and mean numeric MHz or median `state=Pn` vs the race median. Verdict precedence is `contention` when mean other ms/s is >=30% of the matched rows' mean `(gpu_mean - baseline) * fps` rise, then `GPU clock dropped (Pa -> Pb)` when the stretch's median P-state is below the race median, then `clock` when mean MHz is <=70% of the race median, else `undecided`. P-state ordinals are compared only with P-states, never treated as MHz; other labels and missing counters stay unknown. Offline proof: `session_report_test.py` reproduces a P13 race / P1 slowdown using 1,512 seconds (9.25 / 28.7 ms GPU); the old-parser control fails the same verdict assertion. `"$TOOLS_PY" -I runtime/gpuclients_test.py` covers 27 native fixtures, 3/3 report verdicts vs 2/3 for the ignores-other-ms mutant, 8 missing/legacy/state/threshold cases, and a lifetime-total mutant that fails check 8. The same test builds a standalone five-second Metal compute load: `"$TOOLS_PY" -I runtime/gpuclients_test.py --gpu` must print this Mac's top clients and MHz/state/`-`, self_ms >0 and sampler mean <2 ms.                                                                                                                                                              
- Read it: `"$TOOLS_PY" -I runtime/session_report.py [folder]` (default: the newest in `~/Library/Logs/NFSMW-port/sessions`), about 20-40 lines: setup, per-state frame-time table, worst 10 hitches, GPU- vs CPU-bound seconds, changes. Proof (launcher race, `BOOT_DIAG_HITCH=20:60`, 40 s): 39 perf lines, the injected frame listed at t=24.12 s as `ms=67.6 busy=67.2 cause unknown` at the car's position, session_report.py prints it; with the hitch switched off the list holds only the organic 13.6 ms frame.

**Pause > Video: Time Of Day** (`fe_console.c`, F29): the basic Video page in the
in-game pause menu has a third row, using the PC package's spare native slider
bindings and localized label 999D1383. Range 0..1, step .01, scale 100, initialized
from the current sun phase. Moving it previews through the game's SetTimeOfDay
007696B0; its in-game consumer clamps 0.05..0.95. Back/cancel restores the phase
from before this screen opened; Accept keeps it for the session. Opening the
page without moving the slider does not write phase. The backup and edited value
survive a basic/Advanced page switch. No saved setting or front-end Video row.

Scripted proof (`0127291`, capture `run_shots/pause_time_of_day_slider.jpg`): phase 0.43 -> 0.63 -> cancel restored 0.429298 exactly; gate `537eb23` passed on rerun (first hood reference 2.23 %, rain noise). Accept/reopen, Advanced round-trip and untouched-page cases below remain additional checks. Sequence with
`BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 BOOT_DIAG=1` and a scratch profile:

```text
BOOT_KEYS=seq:race:600,ESCAPE,wait:120,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:30,RIGHT,wait:60,RETURN,wait:180,RIGHT,wait:60,RETURN,wait:180,DOWN,wait:30,DOWN,wait:30,shot,RIGHT,wait:60,shot,ESCAPE,wait:120,shot
```

The eight Right presses stop at the last pause item, Options (the native icon
scroller does not wrap); Right on the Options page picks Video. Two Down presses
select Time Of Day, then Right moves it and Escape cancels. The shots are before
the move, after it, and after leaving. For Accept, replace the final
`ESCAPE,wait:120,shot` with `1,wait:120,shot`; reopen Video to check the retained
phase and that a later cancel restores that new baseline. Also check `2` to
Advanced and `2` back before cancelling, and opening/leaving without input.
`BOOT_DIAG` events.log lines emitted by this code (values/addresses vary):

```text
fe_console: Time Of Day row added screen=XXXXXXXX slot=3 value=0.500000 range=0..1 step=0.01 scale=100
fe_console: Time Of Day value 0.510000 (live preview)
fe_console: Time Of Day cancel restore 0.500000
fe_console: Time Of Day accept keep 0.510000
```

Only a changed slider emits a phase write/restore; the row-added line can repeat
after returning from Advanced. A world replacement skips restoration and logs
`fe_console: Time Of Day cancel restore skipped (world changed)`.

**Brightness** (`fe_console.c`, R35/F13): pause > Options > Video now has a
Brightness slider after Time Of Day. The PC executable has no Brightness item:
SetupVideo 005295F0 builds only LOD and resolution on the pause basic page;
the label-only Draw 0051B750 survives (`OPT_DO_GAMMA`, 6BD21B26). The port reuses
that localized label and the native slider, with a separate private vtable and
backup so Brightness and Time Of Day do not affect one another.

The row mirrors X360: position `s=0..1`, step .01, scale 100, exponent
`s*low+(1-s)*high` (the X360's endpoints, generated from the player's default.xex as `X360E_BRIGHT_LOW/HIGH`), ramp `round((i/255)^exponent*65535)` for each channel.
It previews immediately through `render_set_gamma` on the finished frame,
including the HUD. Back/cancel (including display rejection and unaccepted exits)
restores the opening value; Accept saves `graphics.brightness=<s>` in
`USERDATA/app.txt` when the screen closes. Boot applies that saved value before
presenting the first frame. Basic/Advanced rebuilds retain the edited value and
backup. **The default remains identity**, exponent 1.0 at the slider position
where the two endpoints meet (`BRIGHTNESS_IDENTITY`); X360's reset exponent would brighten the picture and is not the
port default. F10 > Graphics > Brightness uses the same saved preference,
shows whole percentages, and applies/saves immediately; its rounded default
12% selects exact identity, including Reset. The native slider retains its
full position precision. `NFS_TEST_GAMMA=<g>` remains test-only and overrides
boot preferences, slider previews and guest gamma calls for the entire run.

Offline proof (no game launch):
`"$TOOLS_PY" -I runtime/brightness_test.py`.
27,509 ASan/UBSan assertions cover endpoint exponents, identity ramp (all 768
entries exact), monotonic equal channels, preference file round-trip/boot,
F10/reset, separate vtable dispatch, Back/Accept/rejection, untouched open/close,
and Advanced rebuilds. A disabled Brightness Act guard fails the independence
assertion; renderer checks verify 3 x 1,536 LUT entries with override absent,
.6 and 1.6, including later gamma writes. This does not establish native FNG
layout/label appearance or on-screen behavior. On-screen check: from the
Time Of Day selection above, one further Down selects Brightness; verify live
preview, Back, Accept/reopen, Advanced round-trip, restart persistence and F10.
Diagnostic lines begin `fe_console: Brightness row added`, `Brightness value`,
`Brightness cancel restore`, and `Brightness accept keep ... (saved)`.

## Gameplay options (`gameplay_opts.c`)

The F10 menu's Gameplay category (registered with `settings_add`, `settings.h`) provides options in the spirit of the community "Extra Options" mod, built from the decomp.
Each option is saved per user (`app.txt`, only when changed), with an environment switch that overrides and
locks it (tests). Changes apply live (Black Edition Content and Unlock All on the next unlock check or screen):

| Setting (`app.txt` id) | Switch | Default | What it does |
|---|---|---|---|
| Markers per Rival (`gameplay.markers`) | `NFS_MARKERS=1..6` | 6 (                                                  the game: 2) | how many of the 6 reward cards you turn after beating a Blacklist rival. The screen (`SafeHouseMarkers.fng`, `FEMarkerSelection`) always deals up to 6 (the rival's marker awards from `gameplay.bin`, queued by `FEMarkerManager::AwardMarker` 005A62D0 in 6 slots) and stops accepting picks when `GetNumSelected` (007A77C0) reaches 2 (`cmp eax, 2` at 007B3D7B and 007B3E17); the hook reports N picks as 2, and `Redraw` (007A77F0, which prints "Selections Remaining" as 2 - turned) is followed by the same two prints with N - turned. Verified: 6 of 6 turned. |
| Heat Level (`gameplay.heat`; the earlier `gameplay.heat_level` key is ignored, so a saved level never turns into a lock) | `NFS_HEAT=game,1..6` | Game | picking 1-6 sets your car's heat through `Game_SetWorldHeat` (00612660) and keeps it there in free roam: re-applied each second, and every pursuit's heat range is N..N (above the career's cap too). Game leaves heat to the game. Races and the epic pursuit are untouched |
| Black Edition Content (`gameplay.black_edition`) | `NFS_BLACK_EDITION=0/1` | on | forces the game's `IsCollectorsEdition` (008F8718); off leaves the game's own check |
| Starting Bonus (`gameplay.starting_bonus`) | `NFS_STARTING_BONUS=0/1` | on | the game's one-time $10,000 for an NFS Underground 2 save, with its own message, once per career |
| Unlock All (`gameplay.unlock_all`) | `BOOT_UNLOCKALL=1` | off | the game's `UnlockAllThings` (00926124) |
| Open Map (`gameplay.open_map`) | `NFS_OPEN_MAP=1` | off | no career region barriers (affects career progression) |
| Skip Intro Movies (`gameplay.skip_movies`) | `NFS_SKIP_MOVIES=1` | off | the game's `SkipMovies` (00926144) until the title screen: no EA / PSA / attract movies; story movies still play |
| Skip Career Prologue (`gameplay.skip_prologue`) | `BOOT_SKIPINTRO=1` | off | the game's `SkipCareerIntro` (00926125/6): a new career starts in the safe house with the career cars |

- `NFS_X360_DATA=on|off` (user-facing; default on; restart): overlays X360 asphalt tyre-smoke emitter arrays and 32 extra vault collections; the 15 X360-only drag triggers are unreferenced on both platforms, so they add no traffic.
- `NFS_LIGHT_DATA=pc|x360` (F10 Graphics > Lighting data; default **PC**; restart required because attributes load at startup): independent of gameplay X360 Game Data. X360 overlays the active X360 TOD quartet's 11 changed colour vectors (176 B) and 32 billboard-flare fields (296 B), and skips PC's extra flare alpha doubling at `00505B4C`; the table retains raw X360 alpha. No extra shader x2. Quartet selection still follows `g_VisualTreatment` (nonzero selects `3DCFA673/B902CFAD/59C373C3/D835D0C8`); the alternate quartet is untouched. World lighting Neutral/Original remains separate. `BOOT_DIAG` emits one `lighting_data:` line per relevant vault load with option, records/fields patched per class and selected quartet. Generate/check with `"$TOOLS_PY" -I recomp/lighting_data.py [--check]` from the checkout root; offline runtime/instruction checks: `recomp/lighting_data_test.py`.                                                                                                          shader equations and PC flare artwork still limit retail X360 equivalence.

**Heat and the career cap:** each pursuit takes a heat range when it is created (`AIPursuit::AIPursuit` 00442D00):
outside the career 1..5; in the career the current Blacklist bin's `BaseOpenWorldHeat`..`MaxOpenWorldHeat`
(`GRaceBin`, 005DCEE0 / 005DCF00; 2 at Blacklist 15), lowered further by a race's `MaxHeatLevel`; the epic pursuit
6..6. `AIPursuit::OnTask` (00443BA0) clamps the heat into that range every tick (`bClamp(heat, this+0xDC, this+0xE0)`).
With a Heat Level picked, the OnTask hook makes the range N..N before each tick, which is what makes the choice stick
above the cap (no separate uncap needed). Heat 6 is the game's top level; its free-roam pursuit data is the epic
pursuit's (verified: heat 6 at Blacklist 15, HUD x6, bounty 30,000, two minutes of pursuit). Races (`GRaceStatus`
+0x1968 set) and the epic pursuit (a 6..6 range this hook didn't set) are left to the game. The heat you end a pursuit
with is the car's heat in the save (`FECareerRecord`), as in the game.

**Starting bonus:** the game pays $10,000 once per career to players with an NFS Underground 2 save. On the career's
first car pick after the prologue (`UIQRCarSelect::Setup` 007C2780, while `CareerSettings`+4 bit 1 is clear) it runs
`MemoryCard::ListOldSaveFilesPC` (00518B40: files in `<Local AppData>\NFS Underground 2`), which sets `MemoryCard`+0x1C;
`EndListingOldSaveFiles` (00518570) then shows "Thanks for having played Need for Speed Underground 2..."
(`DialogInterface::ShowOneButton`, text 0x34DC1BEC) and `CareerSettings::AwardOneTimeCashBonus` (0056D7C0) sets the
bit and adds 10000 when +0x1C is set. The hook sets +0x1C before the search, so the game's own path pays it. Skip
Career Prologue has no first car pick (`CareerSettings::StartNewCareer` 005A3950 gives the career cars), so there the
same `EndListingOldSaveFiles` runs once the safe-house menu has had control for 1 s in a new career, and the menu is
rebuilt after the message (`cFEng::QueuePackageSwitch` 00525940) so its header shows the cash (verified: 0 -> 10,000).
The first-car-pick path is the game's own and was not run here (it needs the prologue races).

**Open Map:** the career's region barriers come from the current Blacklist bin (`GRaceBin::EnableBarriers` 005DD090,
called by `GRaceStatus::EnterBin` and `GManager::BeginGameplay`); the hook skips it. Turned on in free roam, the
game's `DisableBarriers` script function (005DBF00) removes the current ones; turned off, `EnableBinBarriers` (005E3020)
puts the bin's back. A race's own route barriers (`GRaceStatus::EnableBarriers` 005FE330) are untouched.

**Skip Intro Movies:** `SkipMovies` makes `MoviePlayer::Play` skip every movie, so it is on only from the first frame
until the title screen (`WS_MW_LS_Splash.fng`) has control: the title came at frame 53 instead of ~1350.

**Black Edition content:** `cFrontendDatabase::cFrontendDatabase` (005992E0) sets `IsCollectorsEdition` when
`.\GLOBAL\INGAMEC.BUN` exists; nothing loads that file (one reference, the check). The flag gates the 10 `CE_` bonus
presets (`UnlockSystem::IsBonusCarCEOnly` 00576AD0: CE_997S, CE_C6R, CE_CAMARO, CE_CORVETTE, CE_ELISE, CE_GT2,
CE_GTRSTREET, CE_SL500, CE_SL65, CE_SUPRA; their data is in every v1.3 install's `GLOBALB.BUN`), the CE-only events
(`UnlockSystem::IsEventAvailable` 00576A20: 21.1.1, 21.2.1, 21.2.2, 19.9.70), unlockables 0x13D0C8-0x13D0CA
(`IsUnlockableAvailable` 00576B30) and a few front-end lists. The user's install has `INGAMEC.BUN`, so it was already on
there. The setting is applied after the game's check (hook on the constructor).

**Unlock All:** `UnlockAllThings` is not saved (written only by the game's own cheat code and its main menu, and by
this setting); while it is on every unlock check answers yes. Turning it off relocks everything, but cars and parts
bought and events raced meanwhile stay in the save.

With `BOOT_DIAG`, `events.log` has `gameplay:` lines: each setting's value, the markers turned of those allowed, each
pursuit's heat range before and after, the Black Edition check, and once at the front end the game's own unlock checks
for the CE presets and events under each combination of the two flags. Test only: `NFS_TEST_MARKERS=1` deals 6 reward
cards and starts the post-rival flow at its marker step once the safe-house menu (`MainMenu_Sub.fng`) has had control
for 2 s (`uiRepSheetRivalFlow::StartFlow(1)` 005485F0), so the pick limit can be checked without winning a rival race.
