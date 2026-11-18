# Black box

Both games record numeric observations continuously, independently of `BOOT_DIAG`.
`NFS_BLACKBOX` defaults on; `0` disables recording until restart.

The shared `report_frame` entry samples every frame before the per-second stats
return. The game adapter receives the existing flow value (MW 00925E90, Carbon
00A99BBC), without additional guest calls. Records contain frame serial, observed
flow, frame/busy/wait milliseconds, attempted D3D draws and simulation ticks.
Flow changes and one-second state heartbeats preserve unknown numeric values and
the original transition time. Wait time is frame time minus game-thread busy time;
it is not a GPU measurement or proof of a blocking subsystem.

F10 opening records an edge and freezes the preceding history before menu/input
changes. An 80-second pause therefore retains the original fault window. Closing
releases that snapshot and records a close edge. Each F10 problem report contains
`blackbox.txt` and an observed-state summary in `report.txt`. The existing queued
writer saves and sanitizes every text attachment with `report_privacy.h`; failure
removes the attachment. Snapshot capture and text formatting use copied host
records; exporters never inspect guest memory.

Every hang/stall report appends the same numeric history before backtraces, with
or without diagnostics. Hang history ends at the last published frame, and the
header gives the subsequent no-frame interval. Automatic Carbon hang reports also
create the ordinary problem-report folder. Its frame timestamp advances even
when diagnostics are off. The recorder does not change watchdog thresholds.

Storage is bounded: 16,384 frame slots (384 bytes), 65,536 reserved presentation
slots (64 bytes), 4,096 state slots and 512 reserved error slots (128 bytes each).
Publication gates and sequence numbers are inside these sizes: 10.5625 MiB plus
ring bookkeeping and five latest FE context slots (11,076,416 bytes), plus bounded label and adapter state,
below 11 MiB. A menu snapshot
adds 10,384,096 bytes until closing;
export may temporarily add another snapshot and formatted text. No allocation,
formatting, file I/O, guest calls or waiting occurs in frame/event publication.
A producer tries its slot once and counts a loss if occupied. Readers acquire the
same gate before copying, avoiding unsynchronized payload reads. Capacity wraps,
producer losses, unavailable copies and insufficient retained duration are printed
explicitly. Producer losses are lifetime counters; unavailable copies include
missing publication sequences and contended reads, so these counts need not be
disjoint. Sparse state history uses heartbeats for context; records are grouped
by stream, chronologically within each stream. Startup may contain less than 30 s.
At 546 fps the frame ring covers approximately 30 s; higher rates can shorten it.

Carbon FE/package/load observations are available through the tap described below.
MW FE/package/load, movie/NIS, per-pass draws, asynchronous presentation, physical
input and error feeds are **unavailable**; their typed storage is reserved. An observed flow or long frame alone does not name
a cause. Standalone sections contain only fixed labels and numbers, never user
strings, paths, typed text or device identifiers.

`blackbox_test.py` runs without assets through `offline_tests.sh`. It compiles the
production adapters and export hooks: 0 missing sentinel records across 12 exports
(two games, F10/hang/stall, diagnostics off/on), versus 12 missing with export hooks
removed. It advances 31 virtual seconds at 240 fps after wrap and preserves the
F10 window through another 80 virtual seconds. It also exercises concurrent
snapshot/publication, forced contention, capacity exhaustion and the off switch.
The report privacy fixture seeds names, paths and secrets into `blackbox.txt` and
checks both redaction and fail-closed deletion. Real in-game median CPU cost and
the reported fault's blocking condition require separate game measurements.

An excerpt from the diagnostics-off forced-black-screen F10 fixture:

```text
blackbox v1 game=carbon window=30.000s trigger=F10 snapshot=menu snapshot_age=0.000s no_frame=0.000s
MENU_SNAPSHOT losses=0
OBSERVED f2700 flow=3 unchanged_for=53.000s
-30.000000 f1200 FRAME flow=3 valid=0 dt_ms=0.000 busy_ms=0.000 wait_ms=0.000 draws=0 ticks=0
-54.000000 f1 FE root valid=1 address=00001000 vt=00000000 state=4 start=0 prev=0 screen='<unknown>' next='<unknown>' pending=0 wait=0 child=00002000
-54.000000 f1 FE child valid=1 address=00002000 vt=00000000 state=5 start=0 prev=0 screen='<unknown>' next='<unknown>' pending=0 wait=0 child=00003000
-54.000000 f1 FE grand valid=1 address=00003000 vt=00000000 state=0 start=0 prev=8 screen='FeMainMenu_Sub.fng' next='<unknown>' pending=0 wait=0 child=00000000
-40.000000 f700 LOAD valid=7 mask=40 loader busy=1 want=4 have=2+1 garage_ready=0
-40.000000 f700 PACKAGE control='FeBlackScreen.fng' address=00006300 count=2 truncated=0
 0.000000 f2700 F10 open
```

Carbon's FE/load tap runs once per frame on the guest thread, independently of
`BOOT_DIAG` and the verbose probe. On change it publishes root/child/grandchild
manager address, vtable, state/start/previous, screen/next label IDs, pending/wait
and child pointer; loading-mask, car-loader busy/want/have/queued counts and garage
readiness; and the first controlling package, list count and truncation flag.
`LOAD valid` is a bit mask: 1 mask, 2 loader, 4 garage. Missing mask/garage reads
also retain the explicit `FFFFFFFF` value. Manager `valid=0` means unreadable or
absent. Package count zero means no readable entries, not proof the engine is idle.

Only fixed retail `.fng` basenames enter the numeric label dictionary. Matching
ignores case; unknown labels export as `<unknown>`, never arbitrary guest text.
The allowlist contains 129 retail labels with case variants merged. Strings retain
the probe's 31-character bound. The package walk stops at 24 entries,
including cycles, and reports `truncated=1` if another readable link remains.
The latest observation for each FE/load slot is included even when older than
30 seconds. When it is missing from event history, a `FE_CONTEXT` row supplies
the observation with its original timestamp.

Inferred worst-case reader cost: at most 1,039 guest loads (1,357 bytes), comprising
three managers with two bounded strings each, fixed loading globals and 24 packages
with one bounded string each. There are no guest calls/writes, allocations, text
formatting or I/O in the diagnostics-off reader. Label matching runs only on changed
observations. The saturated cyclic fixture reaches exactly 1,039 guest loads;
both feeds off perform zero. Byte totals are inferred from load widths. In-game frame-time cost
remains unmeasured.

`private_fe_probe_test.py` compiles the production reader, adapter and core exporter.
With diagnostics off, a persistent synthetic black screen names `FeBlackScreen.fng`,
all three managers, mask, loader counts and garage readiness in 1/1 F10 exports,
versus 0/1 with the tap removed. Stable state survives 40 virtual seconds without
republication. After 4,097 root changes wrap the event ring, a hang export still
includes all four unchanged context rows. The fixture also checks a 24-entry
cyclic walk, unknown-label rejection, zero guest-memory changes, probe off 0
events and probe on 600 draw lines even when `NFS_BLACKBOX=0` with diagnostics on.
