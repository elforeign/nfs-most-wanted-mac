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
or without diagnostics. The 30-second window is anchored to the last published
frame. Hooked MW cinematic calls can also appear after that frame, through
snapshot capture. The header gives the subsequent no-frame interval. Automatic
Carbon hang reports also create the ordinary problem-report folder. Its frame timestamp advances even
when diagnostics are off. The recorder does not change watchdog thresholds.

Storage is bounded: 16,384 frame slots (384 bytes), 65,536 presentation
slots (64 bytes), 4,096 state slots and 512 typed error slots (128 bytes each).
Publication gates and sequence numbers are inside these sizes: 10.5625 MiB plus
ring bookkeeping, eleven latest state context slots, and bounded label and adapter
state, below 11 MiB. A menu snapshot adds about 9.9 MiB until closing;
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

MW and Carbon provide FE, package and load observations. MW also provides the
active script on the fade object and movie/NIS observations. Both games provide
pass draws, asynchronous presentation, typed errors, and physical input-source,
connection and layout observations. Carbon includes movie requests/state and the
witnessed NIS class/clock below. Standalone sections contain only fixed labels
and numbers.

`blackbox_test.py` runs without assets through `offline_tests.sh`. It compiles the
production adapters and export hooks: 0 missing sentinel records across 12 exports
(two games, F10/hang/stall, diagnostics off/on), versus 12 missing with export hooks
removed. It advances 31 virtual seconds at 240 fps after wrap and preserves the
F10 window through another 80 virtual seconds. It also exercises concurrent
snapshot/publication, forced contention, capacity exhaustion and the off switch.
The report privacy fixture seeds names, paths and secrets into `blackbox.txt` and
checks both redaction and fail-closed deletion. Real in-game median CPU cost and
the reported fault's blocking condition require separate game measurements.

MW samples one FE/load/fade record per change. The latest record remains
available in exports even when its timestamp precedes the retained window. The
record includes the FE manager flags, eight controller-error bytes, requested
package, garage type, boot flag and pause request. The loading row identifies
native loading flows 4 and 5, continuation address, stage, loading package and
the screen-seen and tips-done bytes. Package names and stage text use fixed
retail labels. Unrecognized labels show as `<unknown>`.

The package row identifies the first package with control in native order, the
observed list count and a signature of its entries. The fade lookup follows
the native active-list search with an idle-list fallback. It reads the active
script hash for object `027FF2DC` in `FadeScreen.fng`. The script hash
`5079C8F8` means `fade_on=1`. Incomplete fade observations print
`fade_on=unknown`. Each package traversal visits at most 24 entries and an
object lookup visits at most 64 nodes, including children. The `limits` mask
marks an incomplete control list (1), incomplete fade package search (2),
incomplete fade object search (4) or invalid script pointer (8).

MW records movie player address, native state, next state, pause byte and a
bounded filename hash on change. Movie hooks record Play, Stop, destruction and
host playback end or timeout. Native playback exposes state and next state
through the samples. MW also samples the activity behind INIS,
its state, scene hash and scene pointer. Load, Play and Unload calls appear even
if they all occur between frame samples. Hook rows describe call entry, so the
state can be the one preceding the call. The latest sampled state remains
visible when older than the retained window. A zero address with valid state
indicates absence, and unknown hashes show as zero. Exports contain numeric
hashes and guest addresses. Movie filenames and requested scene names are never
copied as text.

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

Carbon samples the movie player at `*(00A97BB4)`: class, state `+120`, next
state `+124`, and a bounded hash of the inline filename `+1C`. Native playing
states are 3, 4 and 5. The existing movie-request provider records a play request
before calling its original function, including requests shorter than one frame.
Only the hash enters history; arbitrary filenames never enter reports. Movie
playback clocks are not decoded yet.

Carbon samples INIS at `*(00B4D964)`. Interface class `009EC6E8` has state
`+2E0` and track `+390`; track class `009C5FE0` has float elapsed `+20` and
length `+28`. These layouts are pinned by native image fragments in
`private_blackbox_cinematics_test.py`. Presence/class/state are read each frame;
clock values are sampled at most four times a second, plus identity changes.
Within the current object/state/track, `playback_unchanged_for` measures time
since the last observed clock change,
anchored to the last frame or the preserved F10 snapshot. It excludes subsequent
no-frame time, which the header reports separately. Historical clock samples
show age at their event time; the latest context shows age at the report anchor.
Invalid clocks report age unavailable. Other NIS classes retain their numeric
class and explicitly say `state=unavailable layout=not_decoded_yet`; no fields
from the witnessed layout are assumed for them.

The offline frozen-clock fixture advances 8,000 frames over 40 seconds and
preserves F10 evidence through an 80-second pause: inherited code 0/1 reports,
tap 1/1, removed feed 0/1. It checks hang output, clock progress, unsupported
classes, invalid clocks/pointers, short requests, zero guest writes, unchanged
native-call CPU results, and zero guest reads or added clocks with recording off.
Nine alternating pairs of 500,000 frames measure the adapter at 14.3 to 18.9
ns/frame idle (paired median increase 4.7 ns) and 14.3 to 20.5 ns/frame with
frozen movie/NIS state (increase 6.2 ns). These are offline measurements; aggregate
solo game A/B remains separate. No new switch or playback control is added.

Effect BeginPass/EndPass taps accumulate deltas from the existing cumulative D3D
attempt counter, including repeated invocations of a pass. The shared frame tap
copies at most 32 distinct pass IDs into the existing 384-byte frame slot. The
RENDER row has its own original render serial; the guest reporting serial can
be different. `attempted_d3d_draws` is independent of the previous PerfFrame
sample printed on FRAME. Deferred shader draws and failed shader/pipeline draws
are separate deltas. Draws outside a recognized pass remain `unattributed`;
`pass_overflow_draws` counts attempted draws beyond the 32-entry frame budget.
The 32-pass limit is proposed until representative FE/race runs in both games
show zero overflow. Nonzero draws do not establish that a picture was visible.

A 256-entry numeric dictionary identifies effect/technique/pass hashes, with
fixed retail labels for recognized effect categories. Raw effect, technique or
pass strings never enter reports. Dictionary saturation is counted and new
identities become unattributed. Definitions remain immutable after publication.
Host post, copy, resolve and final encoder IDs are separate observations with
their original serial, never counted as attempted D3D draws. Post detail uses
the existing HC stage IDs. Host encoder records consume presentation ring space:
coverage depends on actual encoder/stage frequency and can be shorter than the
four-presentation-records-per-frame estimate. The coverage row explicitly reports
wrap, publication losses and a short retained window.

Presentation records describe actual frame submission after render-thread replay,
frame command-buffer completion, final command-buffer completion and drawable
presentation. Each callback captures the original render serial, independently
of callback order or later frames. GPU rows include start/end timestamps, numeric
command-buffer status and signed error code. Shown rows contain the drawable
presentation timestamp and distinguish shown from dropped. A missing drawable is
explicit; `not_scheduled` means the existing present-copy policy did not schedule
a presentation. It does not infer a blocking cause. LATEST_PRESENT summarizes
observed versus missing-or-pending callbacks for the latest copied render serial.
Callbacks after the last frame remain visible through snapshot capture; F10
preserves that capture through later frames and callbacks. There are no GPU waits.
Per-encoder GPU time is unavailable in the recorder; the existing optional
`BOOT_DIAG_GPUPASS` log supplies that separate measurement without a new switch.

`blackbox_render_test.py` compiles the production effect-pass hooks: the frame
exports 7 attempted draws (3+4 across two named effect passes), deferred=2 and
original render serial 42. Removing BeginPass attribution exports 0 attributed
draws and fails the same check. Production presentation blocks run against fake
platform objects, with callbacks executed in reverse: 18/18 original serials
versus 0/18 when the captured serial is replaced. The fixture also checks frozen
F10 history, frame-crossing passes, overflow, unknown IDs, off-path counter reads
and an offline per-frame microbench. No game, GPU, audio or controller is used.

The ordinary final completion uses one shared block and 32 fixed numeric tickets,
so serial capture allocates no new block per frame. Ticket exhaustion emits an
explicit loss record. Added renderer state is bounded below 16 KiB; the original
frame/presentation slot sizes and permanent recorder storage cap remain unchanged.

Input sampling reads cached host values once per frame. `INPUT` records carry
`source=keyboard/mouse|controller`, `source_changed`, primary-pad `connected`,
`layout=unobserved|playstation|xbox|pc`, and source `unchanged_for` age at the
snapshot anchor. Layout is the last value resolved by ordinary controller/prompt
paths; it stays unobserved until those paths run. Carbon caches its three-choice
resolver separately from MW's two-choice layout. No preference lookup occurs in
sampling. `changes` bits are 1 source, 2 connection, 4 layout. The initial keyboard
value is the existing reader default, not evidence of a key press.

Connection-driven prompt defaults never change the physical source; disconnects
retain the last physical source even when prompts switch to keyboard. Mouse
movement retains the existing 16-point threshold. Only changes observed at frame
samples are journaled; multiple changes between samples may be missed. The latest
input record and source timestamp survive event-ring wrap as `INPUT_CONTEXT`.
No key values, text, device names or controller identifiers enter this feed.

`blackbox_input_test.py` compiles both production prompt providers, adapters,
layout caches and recorder. The pad → keyboard → pad sequence exports exactly
3 source transitions for each game with diagnostics off/on, including connection
and layout changes. On unchanged providers, MW observed 3 source transitions
and 0 connection-only transitions; Carbon observed 6 and 3 respectively. The
pinned Carbon prompt-state-only control exports 6 and fails the same check.
Frozen F10 history survives later keyboard activity and an 80-second pause;
unchanged source age survives 40 seconds and 4,097 events wrapping the journal.
The fixture also checks the mouse threshold and disabled collection. Its paired
microbench compares adapters with/without the input tap; live timing remains a
separate measurement.

Typed errors use the same `NFS_BLACKBOX` switch in both games, independently of
`BOOT_DIAG`. Failure-only taps cover shader translation and library compilation,
pipeline construction, failed COM HRESULTs, asset open/read failures, guest
breakpoints and runtime fatal exits. Frame and final GPU completion failures
enter the error ring through the existing presentation callback primitive;
their original render serial survives late or reordered completion. Command
status Error is recorded even without an NSError code. No extra callback or
GPU wait is added.

Each ERROR row contains class, operation, numeric code, numeric shader/method ID,
guest caller, first and last monotonic timestamps, first frame and occurrence
count (`repeats`, including the first occurrence). Identical retained keys are
counted together, with a bounded scan of the 512 slots only on failure. Counts
cover the retained key's lifetime, rather than just the 30-second window. A busy
producer drops once and increments the lifetime loss counter. Snapshots and
producers use the same slot protection; neither waits for the other. Export
coverage reports overwritten keys and lost or unavailable records. An error
after the last frame remains eligible through snapshot capture or live export.
If a repeat updates a key while capture is copying the ring, its known
pre-trigger first observation remains visible. `capture_overlap=1` marks that
the copied last time is later than the trigger. The exact pre-trigger count is
`repeats=unavailable`; `observed_repeats` gives the copied lifetime count instead.

COM filtering excludes handle getters, reference counts, scalar values and void
returns. In particular, high-bit D3DX effect handles are not HRESULT failures.
Unrecognized host interfaces remain unclassified. File paths, player names,
assertion text, compiler source and NSError descriptions never enter numeric
storage. Asset IDs are omitted; caller and error code identify the observation.
Legacy error messages in report/log attachments still pass through the existing
bundle privacy sanitizer before sharing.

The asset-free `blackbox_errors_test.py` fixture injects shader, D3D, Metal,
asset and assert failures: starting code misses all five classes, fixed exports
miss zero, and removing any feed misses one in each of F10/hang/stall/crash.
Seeded path/name/secret occurrences across the exported bundle are zero;
bypassing sanitization leaves 15. Concurrent error producers account for all
40,000 attempts as occurrences or explicit losses. Overflow retains 512 keys
and reports the loss of coverage. An alternating offline microbench includes
100 COM returns and three presentation records per frame; this is separate
from the complete solo game A/B required after integration.
