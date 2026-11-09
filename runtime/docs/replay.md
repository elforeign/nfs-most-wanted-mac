# Replay buffer

In F10 > Display, **Replay buffer: Arm replay** starts capture without writing
a continuous clip. Replay always starts Off and arming is never saved. Wait for
**ready**, then select **Save last 30 s**. It saves the thirty host-clock seconds
ending at the action, keeps the buffer armed, and displays saving, saved or error
status in the save row and a completion message on screen. Select Replay buffer again to disarm. The clip resolution
also applies to replay. Arming during a recording shares its encoder; disarming
detaches its packet consumer and leaves that recording running. Video clip attaches or stops a file independently; replay and live capture
continue while either remains active. A shared clip finishes its remaining sound
before another recording can start. `NFS_REPLAY=off|on` (default `off`) also retains compressed packets when a
recording attaches its packet sinks; this diagnostic switch does not start capture.
`replay::Ring::arm()` and `disarm()` explicitly control an individual ring;
disarm immediately releases its retention and rearm starts empty.

`NFS_LIVE_ENCODE=off|on` is an initially Off local encoder diagnostic, sampled
when recording or replay capture is explicitly started. It does not start capture.
On adds a separate hardware H.264 High session at the capture dimensions, with
60 fps expected input, no frame reordering, two-second IDRs and VideoToolbox's
explicit `ConstantBitRate` property. Targets are 17 Mb/s for 1920x1080 at 60 fps
and 50 Mb/s for 3840x2160 at 60 fps, interpolated by pixel count between them;
outside that range, the closest anchor's per-pixel rate applies, capped at
200 Mb/s. This follows the H.264 targets in the replay/stream design.
Average bitrate and data-rate limits are not substituted for CBR. A rejected
property or hardware query stops live setup; recording and replay keep their
original codec and settings. HEVC packets cannot enter the live output.

The two sessions share immutable NV12 surfaces only when width, height and
pixel format match. The live API rejects mismatches before submitting to VT;
there is no independent live resolution selection yet. Each VT session owns
its input while needed, and live additionally retains each surface until its
output callback, including errors and drops. At most four live submissions
can be outstanding, leaving four of the eight capture surfaces available for
recording and replay. Live overload fails its own session before consuming that
reserve. The game cost and capture drops require a game measurement. Live failure does
not fail the file or replay sinks. Stopping capture drains both encoders before
releasing the pool. The diagnostic records frame/byte totals and terminal
status at shutdown. Its packets are discarded unless a bounded packet sink is
provided through the live API; this step supplies no network transport.

Off creates no live session, sink, thread, surface reference or encoding work.
It leaves the inactive presenter assembly unchanged (zero calls and no stack
frame). During recording, an empty-pointer branch skips the live submission.
The original NV12 input and compressed bytes delivered to the file remain
unchanged. The switch is diagnostic only and has no saved preference or menu row.

Off attaches no replay queue, consumer thread or storage and adds no per-packet work.
An Off ring's direct consumer is one atomic branch, with no allocations or
calls on that path. The file sink receives the same immutable packet bytes.
Armed retention keeps the last 30 seconds plus the preceding video GOP and
2,112 frames of 48 kHz AAC decoder preroll, including the AAC packet crossing
that preroll boundary. Eviction removes whole video GOPs and corresponding old
audio. A video resolution/codec or AAC format-description change drops all old
retention and waits for a new video keyframe. Non-keyframe video cannot start
a ring or resume one after cap eviction; malformed timing is rejected.

The hard payload budget is 256 MiB: up to 240 MiB retained video/AAC and a
16 MiB independently bounded transfer queue (at most 4,096 queued packets,
including the packet being consumed). Byte counters include audio preroll.
When bitrate exceeds this budget, whole-GOP eviction shortens the available
duration; a single oversized GOP is discarded until the next keyframe. Transfer
overflow fails only the replay sink. Payload accounting excludes framework,
packet-header and codec allocations; resident memory and game cost still need
measurement. One export may additionally pin up to 256 MiB of immutable packet
references. While awaiting the watermark, its preceding GOP is protected from
time-based eviction; the hard byte limit still applies and insufficient history
is reported instead of publishing a shorter clip.

Save latches the action's host time and pins its preceding GOP immediately, then
awaits both video and AAC encoder watermarks through that endpoint for at most
two seconds. Stream changes and disarm cancel that wait. It snapshots references
under the ring lock and performs all file I/O on a separate thread. A UUID-named
hidden temporary MP4 is written in the same Movies/NFSMW folder as normal clips.
The preceding keyframe remains in the media data; the writer's source session
hides decoder preroll with the MP4 edit timing. The last video sample is bounded
to the endpoint. Container duration and both track edits are read back before
the temporary file is moved to a unique `Replay-<uuid>.mp4` destination. No
existing clip is overwritten. Writer readiness, appends and finalization each
have bounded waits; only one save is admitted at a time.

F10 > Controls has **Replay save key** (Off initially), **Replay key** (F8 by
default, also F7/F6), and **Replay save chord** (Disabled initially, then
L1+R1+L3 or L1+R1+R3). Their switches are `NFS_REPLAY_KEY_ENABLED=off|on`,
`NFS_REPLAY_KEY=f8|f7|f6` and `NFS_REPLAY_PAD=off|l1r1l3|l1r1r3`. Shortcuts save;
arming remains an explicit Display action. Shortcuts are inactive in game menus,
text entry and binding capture. The port menu and Guide take priority, as do
keyboard keys already assigned in the current career's game action table.
Pad chord components are reserved for 150 ms; an incomplete chord restores held
buttons and pulses released taps separately, preserving ordinary career actions.
A chord completed after the 150 ms deadline passes through without saving. View,
Home, touchpad and the menu's two-stick chord are never replay chords. A held
key/chord saves once, consumed releases stay swallowed after a setting change,
and focus loss or disconnect cancels pending chords without synthesizing taps.
Reconnect requires neutral input before saving again. Disabled input is unchanged.

`replay_test.py` runs a synthetic 90-second timeline through production
retention, independently decodes its first retained H.264 GOP, checks AAC
preroll, whole-GOP cap eviction and video/AAC generation changes, and verifies
Off allocations, optimized Off assembly and byte-identical file-sink payload.
Deliberately broken implementations must fail each control. AAC retention
fixtures use synthetic payload and do not claim AAC encode/decode validation.

`replay_export_test.py` uses real H.264/HEVC and locally software-encoded AAC. The
35.350–65.350 s non-keyframe cut produces 30.000000 s, 301 independently decoded
binary video markers with zero errors, and exactly 1,440,000 presented AAC samples.
Independent MP4 edit parsing distinguishes the 736 decoded codec-padding samples
from the presentation endpoint; decoded stereo waveform RMS error is 0.001057.
Exposing 1.350 s preroll, admitting a duplicate save, removing the immediate GOP
pin, shifting AAC by 100 ms, and accepting a cancelled stream generation are
separately compiled and rejected controls, together with a control blocking a
second explicit save. Two completed actions produce two unique MP4 files.
The software fixture builds only test codecs from the
shipped, SHA-256-checked FFmpeg source; it uses no network or host AAC service.
`replay_input_test.py` proves disabled byte identity (512 key events and 4,096
pad states), held/repeated input, consumed releases, focus/disconnect cancellation,
career tap restoration (including two rapid taps), chord deadlines and shortcut
priority, with twelve rejected controls.
`replay_capture_test.py` drives Arm/Save/Disarm through the production presenter,
Metal conversion, hardware encoder and ring, without a window or game.
Its checks include a 0.123333 s source edit after an artificial host-clock
advance, warm-up producing no file, Disarm retiring capture resources, and a
shared recording retaining two replay packets, then detaching its replay sink on
Disarm. Four compiled controls remove sink detachment, the latch, replay-only
Arm, and the shared-sink hook. `guide_test.py` also drives
the actual menu key/pad hooks (1,911 checks, 29 rejected controls), while
`replay_bindings_test.py` checks all 76 career action rows and four slots
(912 checks; wrong key field, shortened row scan and omitted slot rejected),
plus six game input states with the menu guard removed as a broken control.

For a game proof, `runtime/clip_smoke.sh --replay <scratch dir>` arms at sim 5
and saves at sim 45 using the `BOOT_KEYS` steps `replayarm` and `replaysave`.
The key names `F6`, `F7` and `F8` also work in `BOOT_KEYS` to exercise the
configured keyboard binding through the normal input path.
It requires exactly one replay under the scratch home and calls `clip_check.py`
with `--replay`: container/video/audio spans must each equal 30 seconds within
one frame, with no recorder startup or AAC tail allowance. Its fake-runner
control rejects duplicate exports; no game run is part of the offline tests.

`live_encode_test.py` decodes 361 live pictures and independently parses H.264
SPS/PPS and slice headers: High profile, matching dimensions/PPS, no B slices
or reorder window, and IDRs at 0/2/4/6 seconds. Real 1080p and 4K sessions
negotiate 17/50 Mb/s and decode at their requested dimensions. Ten rejected
properties, failed hardware query/creation, mismatched surfaces, delayed output
callbacks, submission errors, and HEVC routing all fail live independently.
A simultaneous HEVC recording completes four shared surfaces while live still
holds them; the pool cannot recycle them until the live callbacks retire.
Production capture keeps 181 recording/replay/live packets, while failed CBR
keeps 181 recording/replay packets and zero live packets. The input byte oracle
compares 3,110,400 NV12 bytes against a SHA-256-pinned original conversion
with the live hook removed; the
file reader compares every compressed packet against the recorder's source.
Compiled faults exercise these controls, including a four-second GOP, Main
instead of High SPS, average bitrate instead of CBR, borrowed surfaces, and
cross-routing HEVC. Results and measured fault numbers are in the test headers.

`NFS_LIVE_ENCODE=on` also feeds an in-memory MPEG-TS segmenter with an audio-only
AAC tap. Two-second GOPs begin with SPS/PPS and an IDR; both tracks retain their
capture timestamps plus one fixed second to avoid initial AAC priming wrap.
Pending samples are capped at 32 MiB, each segment at 16 MiB, and retained
callback outputs at two segments; overload stops live independently. The
default callback releases segments without writing files or sending data.
The diagnostic switch never starts a broadcast.

F10 > Display > Live streaming provides **Enter stream key**, **Forget stream
key**, **Go live**, **Stop live**, and a fixed status readout. The native secure
sheet accepts 1–512 UTF-8 bytes without truncation; control characters are
rejected. Save replaces only the in-memory key; Cancel preserves the previous
key. Stop keeps it for another explicit Go; Forget and quit clear the owned
buffer. There is no Keychain, preference, environment key, reveal control or
automatic restart/reconnect. Stop live before replacing the key. Secure entry
bypasses generic text settings, the controller keyboard and report notes.
Copy/cut/drag exports, services, contextual menus and undo are disabled. Cmd+V
inserts only plain text directly into the secure field editor, without an Edit
menu or clipboard writes; paste never clears the clipboard. A key already on the clipboard remains there.

Go live starts capture without a clip or replay and without the local diagnostic
switch. It can also attach to an existing capture; stopping a file, disarming
replay and stopping live release only that owner. The last owner stops capture.
Each explicit Go creates a fresh H.264/TS generation, beginning with an IDR;
late callbacks retain the old cancelled poster. Transport or encoder failure
stops live independently and requires another Go. Key entry and restart create
zero capture, encoder, poster or session resources.

Public strings contain only fixed status/error text. Report export redacts
labelled stream keys and ingest `cid`, including bounded percent/JSON escape
decoding, before truncation/export. Mutable key buffers are erased; Cocoa/URL
framework copies cannot be reliably zeroed. Full memory dumps, debuggers and
swap are outside this guarantee. The offline secure-control and loopback tests
use generated sentinels; native focus/VoiceOver and actual game capture remain
UI checks, and production ingest requires a real stream key.

`livestream.mm` POSTs each segment followed by an updated media playlist to
YouTube's HTTPS HLS ingest host. An ephemeral NSURLSession, one connection per
host and a serial request queue preserve order and connection reuse with system
TLS. Redirects and other hosts/schemes are refused. Only the query contains the
key (`cid`), alongside `copy=0` and `file`. Each Go creates a random UUID prefix
for segment filenames; `live.m3u8` is the media playlist name. Filenames need no
URL escaping. The User-Agent is `NFS / Mac / 1.0` (maker / model / version).

The playlist starts with media sequence zero and carries `EXT-X-VERSION:3`,
`EXT-X-TARGETDURATION:5`, `EXT-X-MEDIA-SEQUENCE` and an `EXTINF` from each
segment's actual 90 kHz interval. Its sliding window retains two previously
acknowledged entries plus the current segment. Segment/playlist serialization
leaves at most one uploaded segment awaiting playlist acknowledgement (below
YouTube's five-segment limit); queued plus in-flight work is also capped at five
segments and 16 MiB. Only small playlist metadata survives payload retirement.
HTTP 202 is accepted, including a segment that precedes its playlist. HTTP 500
gets at most two retries after 100/200 ms. One three-second absolute budget
covers a segment, its playlist and both backoffs; Foundation's backup timeout
is four seconds. Stop cancels promptly, without waiting for networking.

The encoder emits H.264 without reordered frames, with forced IDRs at two-second
intervals; capture admits at most 60 fps. Each TS segment starts with PAT/PMT,
then the optional SDT, and contains one stereo AAC-LC track. A delayed IDR cannot
emit a segment longer than five seconds; that error terminates live independently
of recording/replay. While no frame is presented (the game is inactive and parks in
`GetMessageA`, or its window is occluded), capture repeats the last live picture:
after 0.5 s without a frame, one held frame every 1/30 s keeps the two-second IDR
and segment cadence, so viewers see a still picture with the running audio
instead of the stream ending (the recording keeps its gap; `NFS_TEST_LIVE_HOLD=0`,
test only, turns the hold off for a control run). Segments that exceed the poster's byte/count limits are
counted as dropped, and the producer continues. HTTP bodies share immutable
segment allocations, which remain accounted until completion/cancellation.

The status row distinguishes key rejection (401), `YouTube <code>`, no response
(deadline), network, encoder and capture errors; an encoder error names its
VideoToolbox status (`Error: encoder -12902`) or the segmenter's reason
(`Error: encoder (no-idr-5s)`; also `ingest-backlog`, `timestamps`, `no-keyframe`,
`no-audio`, `mux`, `queue-full`, `format`, `ingest-closed`), and its session note
adds `reason=`. Terminal reasons survive capture
detachment until the next Go. The existing session-note feed writes Go, the first
accepted segment and playlist with their status codes, each error kind/code, and
Stop with accepted/posted/playlist/cancelled/dropped/failed/retry and memory/time
counts to `session.txt`. None of these notes contain keys, URLs, `cid` or bodies.
Unattended check: `runtime/live_fake.sh <dir>` (the gate's `live fake` stage) streams a guarded front-end run
for 330 s to `runtime/live_fake_ingest.py`, a 127.0.0.1 HTTP fake of the ingest (202 per segment, 200 per
playlist). `NFS_TEST_LIVE_FAKE=<port>` (test only, `live_fake.m`) arms the `BOOT_KEYS` step `livego` (diag runs), which
presses Go with a fixed internal key; only that key goes to the loopback URL, any other key to YouTube; the script takes the focus
away three times (unfocus 15 s, hide 15 s, unfocus 30 s). PASS needs no `live: error`, valid uploads, >= 80 % of
the 2 s segments the span holds and no gap over 6 s between segments.

Response bytes are discarded, with cancellation above a 4 KiB delivery budget
(one delegate chunk may cross it). Other HTTP errors, TLS/transport errors,
oversized responses and deadlines stop live independently of recording/replay.

No key creates zero sessions or sockets. Only test builds permit numeric
`127.0.0.1` HTTPS and a session-local certificate anchor. The loopback transport,
capture integration, segmenter and secure-editor tests exercise the production
paths and pinned original fragments: playlist ordering/tags/durations/windows,
fresh names across starts, 202/500/401, deadlines, error reasons, session notes,
plain paste, clipboard writes and sentinel privacy. Real YouTube acceptance
requires an explicit Go with a valid key.

Off creates zero segmenter allocations or sinks. `live_seg_test.py` probes six
synthetic segments and rejects build-list, non-IDR, reset-DTS and default-On
faults; capture completion logs segment counts and non-monotonic DTS totals.

For simultaneous replay/live game proof, use matched solo runs (Off/On/On/Off),
with distinct scratch homes and diagnostics disabled. Arm at sim 5, save at 45,
and disarm at 50 so both encoders report their terminal counts before exit:

```sh
out="$PWD/.metal-cache/live-proof"
for pair in off-A on-B on-C off-D; do
    mode=${pair%-*}
    mkdir -p "$out/$pair/home"
    NFS_SLOT=solo runtime/run_guarded.sh 110 "$out/$pair/run" \
        BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 NFS_AUDIO_FAKE_DEVICE=1 \
        NFS_TEST_CLIP_HOSTTIME=1 RUN_GUARDED_NODIAG=1 \
        CFFIXED_USER_HOME="$out/$pair/home" NFS_LIVE_ENCODE="$mode" \
        BOOT_KEYS=seq:sim:5,replayarm,sim:45,replaysave,sim:50,replayarm \
        > "$out/$pair/guard.log" 2>&1
done
```

Require a clean ledger (zero other games at both ends) for every run. Check one
30-second Replay MP4 per run with `runtime/clip_check.py <file> 30 30 --replay`,
no capture errors, and positive H.264 frame totals with pending/status 0/0 in
both On logs. Compare the same race interval while replay is armed (excluding
setup, export and shutdown) in each `run/perf.tsv`: solo FPS loss must be less
than 3%. Also record GPU time, capture drops, live byte rate and RSS; the design's
additional GPU budget is less than 1 ms/frame. These are game acceptance checks,
not offline performance claims.
