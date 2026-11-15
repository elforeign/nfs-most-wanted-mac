# Rain cover under roofs

F10 World **Rain stays out from under roofs**, `NFS_RAIN_COVER=off|on`, defaults
to **On** (owner's eye; unset or empty On). This port extra adds shelter where PC/X360 authored precipitation
zones are missing. The game still decides authored cover. The optional map can
only hide additional rain; it never makes a hidden particle visible. The
synchronous refresh had a measured game-thread cost; the sliced scheduler
still needs matched visual captures and a solo cost comparison.

`rain_cover.m` builds a 512×512 orthographic highest-surface map over 128 m
(25 cm texels) around the active player's camera. It walks all resident scenery
sections, independently of the camera visibility list and sun-shadow quality,
and rasterizes opaque LOD-A triangles using the resident vertex/index streams
and the decoded instance transform. Alpha-tested roofs use their original
texture alpha (including DXT cutouts), declared UVs and vertex alpha, with the
game's 95/255 rejection threshold. Glass, foliage, blended textures and vehicles
do not cast. Gaps remain open at texel
resolution. Heights more than 35 cm above a receiver count as shelter.

A completed front map keeps its own origin while a second map builds on the
game thread. There is no periodic refresh. Scheduling reserves 20 m for the
18.42 m rotated particle reach plus streak extent, leaving a camera-safe
44 m half-width. The time to that square's edge is predicted from the latest
per-frame motion. The first build starts immediately; later builds start
when the predicted edge is within 1.5 times the previous build duration
(or its work at the normal slice budget, whichever is longer). Motion-driven
starts are at least 30 m apart while the front still covers the camera and
the heading stays within 60 degrees of the last build's motion. A larger
turn can consume the old forward lead sooner and bypasses that guard.
Stationary cameras do no further work once covered.

The back origin leads motion by 0.6 seconds of travel, capped at 40 m and
snapped to the 25 cm world grid. Frame-counter gaps normalize displacement;
the monotonic frame interval converts that displacement to speed. A jump of
8 m or more per frame is treated as a teleport and gets no forward lead.
The old front remains available until a complete replacement is published.

Each frame gets at most one slice. The normal deadline is 0.15 ms; it rises
only when estimated remaining work would miss the front map's covered edge,
up to 0.475 ms, reserving 25 microseconds below the 0.5 ms target for the last
bounded batch. The work estimate reserves 50% beyond the previous build,
and at least as much additional work as the active build has already used.
It targets completion within two thirds of the predicted edge time, with
another two frames reserved. A missing front also uses this urgent budget. The clock is
checked every four steps. Clearing the entire map is one step; up to 128
rejected instances are skipped within one step. Other steps visit a section,
mesh or triangle, or rasterize 64 candidate texels. A 1,048,576-step guard
handles a stalled clock. These cooperative limits are scheduling targets,
not hard real-time guarantees. Large cutout triangles resume at their next
texel; interpolation, alpha rejection and maximum-height writes are unchanged.

The resident section list at `009AA478` is hashed once per frame, including
node links, model/instance addresses and counts. A change restarts the back
build but retains published heights, which contain no guest pointers.
View, camera, world-detail, invalid section chains and frame-counter resets
drop both maps. Completed builds swap the maps; partial maps are never sampled.
Missing maps and samples outside a completed footprint fail open.

Curtains differ from the bounded camera particles: `0074A320`/`0074A490`
spawn along an attached track-zone edge plus up to 10 m outwards;
`0074A5D0` advances those particles with wind. The edge endpoints
(`Rain+3830/3840`) have no fixed camera-distance limit. Curtains outside the
128 m footprint keep the existing fail-open rule.

With diagnostics enabled, the `rain cover:` line reports completed rebuilds,
slices, mean/max build milliseconds per rendered frame in each 60-frame
window (idle frames contribute zero), `uncovered` drawn-rain frames where
the front is missing or the camera's particle reach leaves it, and
`build frames` for the last completed build. A frame is counted once even
when multiple rain submissions occur. `reset uncovered` counts the missing
front during a view/camera/detail replacement; `edge uncovered` counts misses
with a valid front. A reset event names the new context. Statistic updates and their extra
clock reads are skipped when diagnostics are off.

Scenery variants follow the game's world-detail filter, `FUN_00723F70`
(`speed_007237d0.c:701–709`, also `StuffScenery` at lines 779–780): instances
with `0x80000` do not cast when `00982B9C` is nonzero; those with `0x40000`
do not cast when it is zero unless they also carry `0x100`. The render path
sets that value to `0x800` at world LOD 0–2 and zero at LOD 3
(`speed_006d6820.c:6933–6936,13989–14005`). The cached map tracks this value
and invalidates before sampling on a change, so a live Options change cannot
leave shelter from an invisible variant while its replacement builds.

The sole guest hook, `Rain::UpdateAndRender` (00758100), scopes precipitation.
The existing `Rain::Render` wrapper temporarily masks sheltered active streaks
only during submission, then restores their states and count. Curtain streaks
are sampled individually at their world-space centre and lowest corner when
their immediate ePoly draw reaches the renderer. Lens drops are omitted while
the camera is sheltered, including authored cover. Road ripples use the same
height comparison at each surface pixel, plus the game's authored-cover flag.
Both PC and X360 lens-drop choices follow shelter. Their simulated state and
the rain intensity remain intact. Road wetness persistence (`Rain+0x36A0`,
the smoothing at PC `speed_0074d050.c:9246–9255`) is untouched. Off submits no
map work, allocates no map or texture, and selects the original ripple shaders.

`rain_cover_test` is discovered by `runtime/offline_tests.sh`. Its synthetic
resident roof box, open sky, roof gap, receiver above the roof and moving-map
fixture run the production caster decode, raster and precipitation wrapper.
Off allocation/submission counts are zero, including while world detail changes.
The CPU fixture checks variant eligibility, sliced publication, streaming-list
changes during a build, repeated calls within a frame, teleports and frame
resets. In `RAIN_COVER_CORE_ONLY`, 17 fixed triangles (opaque, cutout,
overlapping, degenerate and nonfinite) produce byte-equal height fields with
one-shot rasterization and slice budgets of 1, 7, 64 and 257 texels. The
production scheduler also follows straight and 180 m-radius curved paths
for 60 seconds each at 30, 60 and 100 m/s, at 90 Hz. A deterministic clock
charges 5.5 ms per 8,700 decoded triangles including the whole-map clear;
clock instrumentation exists only in temporary test source copies. All six
paths have zero uncovered frames after the first completed map, with every
start at least 30 m from the previous start. Straight-path starts are
42/83/200 and curved-path starts 38/76/187 (limits 61/121/201). The pinned
round-one scheduling control, with its 256-step cap, 16 m lead and 32 m
margin, produces 32/1310/3008 uncovered straight-path frames and
125/1316/2896 curved-path frames. Deliberately broken refresh, resume,
slice-budget, height, streaming-signature and world-detail controls must fail.
A stopped-camera fixture resumes at 60 m/s across a map led in another
direction: zero uncovered frames, versus 11 with the turn bypass disabled.
GPU numeric checks retain 0/24 channel
failures versus 15/24 for a reversed comparison; film dry/wet retains 0/48
versus 18/48 with the film patch omitted. The upload fixture also checks that
the front texture stays stable during a build, a swap uploads once, old
textures remain unchanged, and invalid maps fail open.

                
                                  

                                                                        
                                                                              
                                                                              
                                                                        
                                                                            
                                                                           

                                                                           
                                                                         
                                                                            
                                                            

                       

                                                                             
                                                                                
                                                                            
                                                                         
                                                                      
                                                                        
                                                                            
                                                                        
                         

                                                                        
                                                                         
                        

   
                                                                                                                                                                                                     
   

                                                                            
                                                                          
                                                                             
                                                                             
                                       

     
                                                                                                                                                                                                                                                                                                                                                                      
                                                                                                                                                                                                                                                                                        
   

                                                  
                                                                         
                                                                            
                                                               
                                                                              
                                                                      
                                                                   

                                                                           
                                                                             
                                                                             
                                                                             
                                                                      

                                                                             
                                                                            
                                                                           
                                                                                
                                                                             
                                                                         
                                                                          
                                                          
                                                                           
                                                                            
                 
