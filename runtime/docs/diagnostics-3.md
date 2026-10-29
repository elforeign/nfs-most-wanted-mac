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
