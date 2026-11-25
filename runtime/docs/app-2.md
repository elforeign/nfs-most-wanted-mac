# The app, the F10 menu, restart and problem reports (part 2 of 2)

Part of the runtime documentation; the index and switch lookup hint are in `runtime/README.md`.

Carbon speaker layout: `NFS_CARBON_POS_LAYOUT=itu` opts into home 5.1 angles (0, +/-30, +/-110 degrees, constant power, LFE 0, fixed stream defaults remapped) for positional sound. Unset or `game` retains the game's own 0, +/-45, +/-135. Read at start; restart required.
Carbon F10 Diagnostics: **Unlock everything (testing)** unlocks cars, parts, paint and vinyls live through the native developer flag; Off each launch, never saved (`NFS_CARBON_UNLOCKALL=1` for tests). Purchases remain in the save; re-enter shops to refresh lists. Story progression, crew and reward cards remain separate.
Carbon F10 Diagnostics also has **Report a problem**: choose a category, enter an optional note, then **Save report**. It saves the game frame as `screenshot.png` alongside settings, recent stats/events and the sanitized log in the profile's `reports/` folder. **Show reports in Finder** opens that folder for review and sharing; saving sends nothing. Recent stats and notable events are kept even with Full diagnostic logging Off.
In public builds, **Send to GitHub** previews the sanitized report before posting to Carbon's GitHub issues or opening the issue form. Cancel is selected by default and screenshot sharing starts Off.

Carbon car reflection: `NFS_CARBON_ENVCUBE=1` opts into a 1024-square capture with eleven levels, generated after capture before car draws and sampled trilinearly. Unset or `0` retains the retail 256-square, one-level cube; Off by default; F10 applies changes live through native capture recreation. The visual choice awaits the owner's Off/On comparison; matched bonnet stills remain pending.

Carbon contact shading: Depth ambient occlusion before the native post treatment is Off by default; `NFS_CARBON_AO=on` enables it. F10 Contact shadows applies On and Off live (Off retains retail output). Its own depth kernel (eight directions by six steps to a 0.7 m world reach, at most 0.15 of the frame height; strongest occluder per direction, faded to zero at the reach) with the shared bilateral filter and a composition that follows the occlusion down to 0.4 without a hard floor, so the shade ends softly; a road pixel beside the car's outline is shaded up to the outline; RGB only, preserves scene alpha and native treatment. Off returns before target access or GPU work. The final composition exempts native car paint/normal-map materials and blended overlays, preserving their lighting and lamp emission; the native prelit shadow decal leaves road eligible for AO; additive world decals (lit windows, event rings) are exempt only where they have alpha, so their transparent texels leave the surface eligible, and car depth still shades nearby ground. The shading belongs to the player's car: full within 3 m of it, none beyond 4.5 m, so distant buildings are untouched; other cars get none. Screen-space depth cannot shade hidden surfaces. Visual comparison and acceptance are tracked in VERSIONS.md.

Car reflections (`envcube.m`, included by `d3d9.m`): the receiver diagnostic confirmed the live cube created
at `006BD54B` (`00982A30`), 256², one mip; its sampler already requests linear min/mip filtering, but has no
lower mip to read. Sharp sets the shared edge `008F8FF4` before target creation, so the guest's cube
viewports/depth surfaces and `splitcapture.c`'s independent player banks use 1024² together. Mirror and
road target sizes remain controlled by their own descriptors. The cube's square projection follows those
descriptors. After the final selected face is unbound, 60 mip-face downsample passes run on the same
command buffer before receivers; this works with both direct encoding and the existing render-thread
recorder. The car sampler override is restored after each draw. Captures retain the game's chosen cadence:
mode 3 drew six faces and generated one complete chain per frame; mode 2 kept its three-face masks.

                
                                                                                                       
                                                                                                    
                                                                                                          
                                                                                                            
                                                                                                           
                                                                                                            
                                                                                                       
                                                                                                            
                                                                                                        

                                                                                                        
                                                                                                       
                                                                                                      
                                                                                                        
                                                                                                         
                                                                                         
                               
                                                                                                        
                                                                                                            
                                                                                                          
                                                                                                
                                                                                                               
                 

Car paint and rain ripples already have conditional restart semantics (`live_from_default`), rather than the 11
unconditional rows above: `materials.m:306` / `materials.m:353` update uniforms in shaders translated at startup.
Their help now also names Restart game for a run started with PC shaders. World streaming uses that existing
registry mechanism too: a PC boot retains "after a restart", while an Ahead boot can switch both ways live.
There are no deferred implementations among the unconditional live rows; the remaining resource reloads would
exceed the contained-change limit and/or require replacing guest data layouts.

The four render-resource changes only queue the game's existing `LoadedFlagMaybe` reset byte (`x360.m:53`).
At the beginning of `eDisplayFrame`, before drawing, the game releases old targets, resets the device and recreates
the targets (per the decompilation). This is the resolution-change path already used by
`widescreen.c:101`. Setters never recreate textures or pipelines inside an encoder. Pending target values are
consumed at recreation, so old dimensions, samples, border and culling stay consistent until then. Several changes
in one message-pump pass coalesce into the same reset. This is a full device reset; it can cause a short frame hitch.

                
                                                                        

                                                                                                                  
                                                                                    
                                                                                                          
                                                                                                      
                                                                                                                 
                                                                                                            
                                                                                                              
                                                                                                                    
                                                                                                           
                                                                                                              
                                                                                                            
                                                            
                                                                                                                
                                                                                                                    
                                                                                             
                                                                                                             
                                                                                                                   
                                                                                                              
                                                                                                               
                                                                                              
                                                                                                                   
                                                                                                                
                                                                            

                                                                                                 

                                                          
                   
                                                                                                                                                                                                                                                                     
                                                                                                                                                                                                                         
                                                                                                     

                                                                                                         
                                                                                                       
                                                                                                             
                                                                                                          
                 

## Focus loss and hidden windows

The window's key/resign-key changes reach the game as WM_ACTIVATEAPP / WM_ACTIVATE / WM_SETFOCUS / WM_KILLFOCUS, as on Windows (in NFSMW.app; `runtime/boot` from a terminal doesn't pass a real focus loss on, so background scripted runs keep going, but `unfocus:`/`hide:` steps do; `NFS_PAUSE_INACTIVE=0|1` overrides). Inactive, the game mutes its sound (`SetSoundControlState(..., "LostFocus")`) and parks its main loop in `GetMessageA` until a message arrives, so it pauses (a race clock doesn't advance); `GetMessageA` waits with the GIL released, so the sound and streaming threads keep running, and diag mode doesn't count the wait as a hang. Keys held at the moment of focus loss are released (their key-ups go to the other app). Presenting never blocks the game: the frame is copied and a presenter queue takes the drawable, so a covered or hidden window (whose layer hands out no drawables) doesn't freeze the game for the layer's one-second timeout; while the window is occluded nothing is presented. `BOOT_KEYS` steps `unfocus:N` and `hide:N` test this in scripted runs.

## Settings (registry)

The game's key (`HKLM\Software\EA Games\Need for Speed Most Wanted`) is kept in `USERDATA/registry.txt` and rewritten (one durable save) at the `RegCloseKey` that ends a batch of `RegSetValueEx` calls, or at exit: saving per value held the game thread ~11.5 ms each, a 230 ms frame when Video options wrote 20 values (`registry_batch_test.py`). With no `FirstTime` value the game runs its hardware detection on the first launch (the reported GeForce 7800 GTX and the 3 GHz CPU rate performance level 4: visual treatment, reflections, car environment maps, world LOD 2, 800x600) and saves it; the options menu changes persist. Delete the file to detect again. `BOOT_REG=name=value,...` overrides DWORD values for one run without saving (e.g. `BOOT_REG=g_RacingResolution=3` for 1920x1080; see Widescreen). On a first run (a fresh `BOOT_USERDATA`) the game skips its settings loader and detects the hardware twice, inline in WinMain before the window and device exist (performance level 4: `g_FSAALevel` 1, `g_RacingResolution` 1) and in `Default__13VideoSettings` (FUN_006c1990) once the device exists; both used to overwrite `BOOT_REG` values, so tests silently ran other settings. After each detection the game's loader (FUN_006c1c00) now reads the `BOOT_REG` values again (`d3d9.m` `first_run_settings`, hooks 006C27D0 in `widescreen.c` and 006C1990), and the port's first-run defaults (                                                               : 4x MSAA `g_FSAALevel=2`, `g_ShadowDetail=2`, the display's native resolution, at most 4K) replace what detection picked unless `BOOT_REG` sets that value; a saved setting always wins (the game's loader reads it); stderr has `boot: first run, WinMain|video defaults: detection picked ..., using ...`. `BOOT_USERDATA=<dir>` uses another folder for saves, settings and the shader cache (scripted runs that create aliases or change options).

## Movies (`movies.c`)

The game's `MoviePlayer::Play`/`UpdateFunction`/`Stop`/destructor are replaced through `recomp/hooks.txt`. When the X360 folder has the movie (same base name, any case, `.wmv`), FFmpeg (`media/movie_api.h`) decodes it on host threads: video into the game's own movie texture (a 4:3 1280x960 texture with the 16:9 frame letterboxed, like the PC files' layout), 5.1 audio as a 6-channel float buffer on the game's DirectSound device. Otherwise the game's VP6 player runs unchanged. Details in the comment at the top of `movies.c`.

**Asset-root manifest (`roots.h`, `<userdata>/roots.txt`, public-release M1b):** one file tells the game where the player's files are: `pc_root`, optional `x360_root` (raw extracted X360 files), `assets_root` (Setup-converted `hd_textures_mips_alpha`, `hd_textures_ui`, `nis_arrest`), `movies`, `caches`, `tmp` (`NFS_ROOTS=<file>` names another manifest; `NFS_*` switches still win). With **no** roots.txt (a development checkout) every lookup is the old sibling-folder one; the public build never does that. With one, nothing is searched beside the game or in the cwd; a missing X360 root hides the X360 rows (F10 list in `<diag>/settings_rows.txt`) and PC is in effect. Temp files go to `$TMPDIR`. `NFS_PUBLIC=1 runtime/build.sh` builds without Extra EA Trax (`extratrax_public.c` stands in; set `BOOT_OUT=<name>` for another binary name). `runtime/env.sh` gives the launchers `TOOLS_PY`, `NFS_GAME`, `NFS_RUNS` relative to the checkout (override by environment). Test: `runtime/roots_x360_test.py` (the feature gates with and without a manifest and an explicit `NFS_X360_FILES`).

Carbon street-lamp lighting: local coloured world pools are On by default; `NFS_CARBON_LAMPLIGHT=off` disables them (restart required); shares the day/night lamp level and car/overlay protection (light scaled by the receiver mask: cars 0, additive glows and flares by what they add, saturate(max(rgb) x alpha), so an opaque-alpha reflector or lamp glow protects its core and not its whole quad; smoke and flare-streak quads are receivers; held `BOOT_DIAG_LOOK_AB=aoglowoff` redraws with coverage by alpha alone, and its `lamplight_N_aoglowoff.lmp` sidecar holds that mask), unshadowed and limited to the nearest 24 loaded street lamps within 150 m. Diagnostic-only `BOOT_DIAG_LAMPLIGHT_SHIFT=1` shifts emission 40 m for the broken control. `NFS_CARBON_LAMP_WARM=on` (Off by default; acts only with `NFS_WORLD_LIGHTING=neutral-d65`, restart) gives the street-lamp pools a declared 2700 K warm white after the 15% D65 mix at each record's authored luminance (xy .432855,.395925); every other mode keeps the authored colour. Cars take the same white: hook 00746230 (`carbon_lamplight_cars.h`, only for the car-light builder call at 00746C3C) points each gathered street record at a shadow copy whose bytes are the 2700 K white scaled to 255/119/45 and multiplies its fade by authored Y / shadow Y (1.38-1.79), so hue and Rec.709 Y reach the car unclamped; the emitter caller and all other lights are untouched.
