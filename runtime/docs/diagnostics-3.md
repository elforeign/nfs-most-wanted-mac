# Diagnostic mode, race starts, smoke and reference checks, game slots (part 3 of 3)

Part of the runtime documentation; the index and switch lookup hint are in `runtime/README.md`.

**Tries that did not hang (41 runs, 0 hang or stall reports):** 4-way concurrent starts x4, 6-way x2, 4-way under 18
busy cores, 4-way during a full `build_all.py` (18 cores), 3-way at 60 Hz and at 120 Hz, 3-way with
`BOOT_TEST_GILYIELD=1` (test only: hands the GIL over at every import, as an overloaded machine does after 2 ms; it would
expose a guest spin-wait or lock-order race that depends on where the GIL changes hands). Without the original reports
the cause stays open: a heavily loaded machine (several full recompiler builds, gates and soaks at 14:10-14:20) making
loads crawl past the 5 s hang limit is as consistent with the evidence as a race. Overlapping runs stay allowed.

If it happens again, the `hang_N.txt` now says it: each guest thread's blocked-on line (critical section and its owner,
or the waited handles), the file being opened in the event ring, and the second/third report 10 s later shows whether
the game thread loops (stack changes) or is blocked (same stack). Keep that diag dir.

                
                                                                                                                    
                                                                                                                     
                                                                                                                      
                                                                                                                   
                                                                                                                  
                                                                                                                 
                                                                                                                     
                                                                                                                   
                                                                                                                    
                                                                                                           
                                                                                                                       
                                                                                                                     
                                                                                                                
                                                                            
                 

Found on the way: the port's `CreateFileA` opened a folder (an empty file name opened the game folder, GetFileSize
0x3A0); Windows refuses it without FILE_FLAG_BACKUP_SEMANTICS (ERROR_ACCESS_DENIED), and so does the port now. Ruled
out: a full shared string pool (a race peaks at 66 strings, 2.3 of 32 KB; stats.log `sstr`, hang reports carry it).
Diag/test switches added (`diag.c`): every failed shared-string allocation is logged; in diag mode the car pack names'
allocations and frees with references and caller (`sstr alloc/free` in events.log); TEST `BOOT_TEST_SHAREDSTR_EMPTY=
<substring>` (that name allocated as ""), `BOOT_TEST_SHAREDSTR_FULL=<substring>` (pool filled at that name: the loading
screen never ends), `BOOT_TEST_SHAREDSTR_POISON=1` (a freed name keeps its length as '~'; uses of dead names logged:
none in a race).                                                               

## Matched sign mip-tail experiment

`BOOT_DIAG_SIGN_AB=1`: after a fresh `arm` file under BOOT_DIAG, matched A/B/C/D sign captures at 3840×2160/2×;
analyse with `sign_diag_test.py --analyze`. Protocol: [sign_diag.h](../sign_diag.h).

                
                               

                                                                         
                                                                              
                                                   
                                                                                
                                                                                 
                                                                 
                 

## Per-frame presentation timestamps

`BOOT_DIAG` writes `present-*` rows to `events.log` for each rendered frame. Join on the explicit `frame=` serial, since callbacks can arrive late and rows can interleave; the usual `f...` log prefix is the logger's current frame. All times are monotonic seconds in the Mach/Metal time domain. Zero means unavailable, never an actual presentation.

| Row | Fields |
| --- | --- |
| `present-frame` | `Present`: entry to render presentation, before its resolve/encoding; `screen_update`: that window's `NSScreen.lastDisplayUpdateTimestamp` sampled at entry |
| `present-submit` | `nextDrawable`: return time; `presentDrawable`: time immediately before the presentation request; `commit`: time immediately before the final present command-buffer commit; `minimum`: requested minimum duration in seconds. A nil drawable instead records `drawable=missing` |
| `present-commit` | `stage=frame observed`: real frame command-buffer commit observed via `rt_after_commit`, including render replay. This is an observation just after commit, not the proxy's earlier submit time |
| `present-gpu` | `stage=frame` or `stage=present`, and that buffer's `GPUEndTime`; the latter is the complete ready path including the final copy |
| `present-shown` | The drawable's actual `presentedTime`; zero is a drop |
| `present-loop` | Next frame's serial, chosen absolute `deadline`, actual sleep `wake` and guest-lock `acquired`. A pass without a pacing slot has deadline zero; no-sleep passes record the same wake/acquired time |

The first frame has no preceding loop row. A frame skipped before drawable acquisition has no submit/shown row. CPU submit, GPU completion and actual presentation remain separate measurements. Diagnostic clock/property reads, logging and additional completion handlers are guarded; Visible foreground presentation is required to measure TV cadence: background/covered windows may only report drops. The cutoff's lead before refresh remains unmeasured; these timestamps allow phase, ready latency and missed latches to be checked on screen.
