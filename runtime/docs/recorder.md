# Video clips

In F10 > Display, select **Video clip: Record clip** to start. Select **Stop
recording** to stop, then wait for **Saving...**. Each successful save shows
**Clip saved: <name>** for eight seconds. Select Video clip again to record
another clip. The separate **Show clips in Finder** row opens `~/Movies/NFSMW/`
only when selected. Recording always starts
Off and its recording state is never saved in preferences. The row shows elapsed time, actual video
resolution, codec and capture/encoder-or-writer/unshown/cadence drop counts.

Clips capture SDR video and stereo AAC game sound, with HDR output either On or Off. HDR frames are
converted to SDR during capture; the display keeps its HDR output. The recording
includes the final game picture, HUD, post-processing and brightness, with the
same scaling as the window. The F10 panel and separate performance overlay are
outside the video.

The linear RGBA16Float HDR source converts directly into the existing 8-bit
NV12 encoder surface, without an intermediate frame copy. With headroom 1, the
source contains decoded SDR bytes: capture recovers sRGB bytes before linear
scaling, matching HDR Off within 1 LSB in the pre-encode NV12 planes. With real
HDR headroom above 1, capture uses the same hue-preserving exponential shoulder
as SDR screenshots: for linear RGB peak p above 0.75, scale RGB by
`(0.75 + 0.25 * (1 - exp(-(min(p, H) - 0.75) / 0.25))) / p`, where H is the
captured frame's headroom, then sRGB encode. This curve has unit slope at the
knee and approaches SDR white smoothly. It compresses near-white SDR values
in real HDR frames as well as highlights; preserving every value through 1
would instead require clipping all higher values. H=1 bypasses this shoulder.
Unsupported source formats still produce a visible error.

**Clip resolution** (Display, immediately before Video clip) offers **1080p**,
**1440p** and **4K**, saved as `display.clip_resolution` in `app.txt`. The default
is 1440p (`CLIPREC_DEFAULT_RESOLUTION` in `cliprec.h`). Changes apply to the next
clip, including changes made while the current clip is recording. Output keeps
the presented aspect ratio, targets the chosen height, and never upscales past
the presented source size. A smaller source stays at its presented size; odd
dimensions round down to even NV12 dimensions. Rendering below the presented
size cannot add native detail. Ultrawide output retains its aspect and may be
wider than 3840 pixels at the selected height.

Files go to `~/Movies/NFSMW/Clip-YYYYMMDD-HHMMSS-<unique>.mp4`. A hidden temporary
MP4 is written in the same folder, finalized asynchronously, then moved to the
unique final name without overwriting an existing file. Errors appear in the
F10 row; select again to retry. An overload saves the valid prefix when possible
and displays the stop reason in the recording row. Normal quit drains capture
and finalizes the file, waiting at most ten seconds. Forced termination cannot
promise a finalized MP4; an interrupted temporary file is never advertised as a
saved clip.

The recorder requires a hardware video encoder: HEVC Main is preferred, with
hardware H.264 High as fallback. If neither works, recording stops with an error.
The target is the drawable aspect ratio at up to the selected height and 60
frames per second. Bitrate scales with the actual encoded pixel count from
3840 x 2160: HEVC targets 40 Mb/s and H.264 60 Mb/s at that size, with two-second
keyframes, real-time encoding and no frame reordering.

| 16:9 output | HEVC / H.264 target (Mb/s) | Approximate MB/min (HEVC / H.264) |
| --- | --- | --- |
| 1080p | 10 / 15 | 75 / 113 |
| 1440p | 17.8 / 26.7 | 133 / 200 |
| 4K | 40 / 60 | 300 / 450 |

These are variable-bitrate targets, not fixed file sizes; simple scenes can use
less. Smaller presented sources use proportionally lower targets, and wider
aspects use proportionally higher targets.

`cliprec_present(cb, src, drawable, headroom)` follows the final `quad_into` in
`present_show()`. The inactive path is one atomic state check (also verified in optimized assembly,
with an injected-retain negative control). No recorder queue,
encoder, pool or Metal pipeline exists before the first activation. While
recording, the presenter encodes a compute pass into its existing command buffer,
sampling the presentation source rather than the framebuffer-only drawable.
The pass scales with linear clamp sampling and writes IOSurface-backed NV12
planes through CVMetalTextureCache. It uses video-range BT.709 luma/chroma and
primaries, centred 2x2 chroma, and the sRGB transfer tag matching the SDR source.
It never maps pixels on the CPU or waits for GPU or encoder work in the presenter.

Eight prewarmed pixel buffers and eight outstanding presentation records bound
capture. Both successful GPU completion and a positive drawable `presentedTime`
are required before submission. The first accepted presentation anchors the
host-clock epoch. Later timestamps retain gaps when frames drop; cadence is
limited to 60 Hz. Unshown frames and failed command buffers are discarded. Each
surface remains alive through GPU completion and the encoder's retained use.

A separate serial queue owns VTCompressionSession and AVAssetWriter MP4
passthrough. Compressed callbacks reserve space before enqueueing; at most eight
packets may wait. Writer backpressure drops frames before compression, preserving
encoded reference chains. A packet-queue overflow or a two-second writer stall
stops and retains only the valid prefix. Capture or writer overload stops after
120 consecutive capture, encoder-reported or writer drops; a successful written
packet resets the streak. Stop waits up to two seconds for pending presentations,
then flushes the encoder, drains packets, finishes the writer and publishes the
file. Finalization invalidates and releases the encoder, pixel pool, texture
cache, writer and Metal conversion resources immediately; only terminal menu
metadata and the file path remain until the next recording.
Buffers for late GPU completions stay retained until those completions.

Audio subscribes to the timed post-limiter DirectSound mix at activation, before
video preparation. Queued output can place the earliest captured PCM ahead of
an early presentation: those video frames are discarded until PCM spans a
presentation, which then anchors both tracks. A two-second failure to find that
coverage surfaces an audio startup error. The six channels are folded to stereo with the existing
normalized ITU fold-down (centre and corresponding rear at -3 dB, LFE omitted),
then interpolated on the recorder queue to 48 kHz. AAC targets 192 kb/s. The
microphone, other applications and system sounds are outside this mix. Audio
capture or encoding failures stop recording and surface an error; an incomplete
sound capture never receives the normal successful-save notice.

The callback copies into a fixed 262,144-frame ring without allocating or
waiting. Before the first accepted presentation, the consumer retains one
second of preroll. Audio uses that presentation's host epoch, and its watermark
advances only when a compressed video sample is successfully written. Dropped
video frames leave gaps on this shared timeline. Device block stamps ("now +
queued frames") jitter by milliseconds, so within one stream/clock generation
samples are placed by sample position from one anchor: the mean of the
segment's first 32 stamps, then slewed at most 200 ppm when the smoothed stamp
error exceeds 1 ms; an error beyond 50 ms, a rate/device change or a new clock
generation starts a new segment (its first ~32 blocks may settle audibly).
Missing intervals remain on the timeline as silence, and a replacement clock
supersedes queued old-clock samples. While no frames are presented (hidden
window, long load) the consumer keeps only the newest half ring (~2.7 s); the
dropped span becomes silence and the clip continues in sync. Stop
flushes video, determines its final written end, quiesces the tap and drains
only audio through that end. Both writer inputs finish once, and the container
end trims codec padding. A stalled audio input gets at most two additional
seconds to drain; the existing ten-second quit bound still applies.

This clock describes the recorded mixer timeline. Playback position is estimated
from host time and software queue depth; receiver, Bluetooth and display latency
remain unmeasured. Native HDR clips, replay and streaming are deferred.

AAC encoding, decoder priming/padding and listening validation remain unverified
in the restricted offline environment: its audio-component registrar is denied,
and an AudioConverter probe returns format-unsupported (1718449215). The paired
`cliprec_audio_test` reports a skip for AAC rather than counting the PCM control
as an AAC pass. It must complete without that skip before sound validation is
complete.

The `cliprec` offline test is registered in `runtime/offline_tests.sh`. It uses
real synthetic Metal textures, the production conversion/VT/writer path and
AVAssetReader decoding. Synthetic drawables supply controlled presentation
notifications without opening a window. Known color bars, a moving marker and a
binary frame counter verify colors, orientation, frame identity and PTS. The
output folder is injected in the test translation unit; no recording environment
switch or automatic start exists. The test drains hardware between synthetic
frames so fidelity checks do not depend on an unpaced producer's speed.

The autodiscovered `clipcycle_test` drives the production menu callbacks through
start, stop, saving and restart with a fake folder-open counter. Restart produces
two recordings and zero folder requests; the pinned original callback produces
one recording and one folder request and fails. The separate Finder action is
also checked for its label, order and destination without opening a window.

Measured fidelity baseline: 36 decoded frames, 0.600000 s, maximum PTS error
0.000000 s, maximum channel error 2/255. Forced exhaustion counts one drop,
recovers all 36 accepted frames and extends duration to 0.616667 s. Ten deliberate
validator faults are rejected, including +100 ms duration/PTS errors, missing
frames, wrong colors, orientation, marker/counter, invalid media, missing drop
counts and incomplete finalization. Additional checks cover 120-drop capture and
writer overload, eight pending presentations, missing/unshown presentations,
hardware H.264 fallback, unavailable hardware, scaling, empty stop, filesystem
failure, quit, a paced asynchronous round trip, temporary-file publication and
refusal to overwrite an existing destination. The last successful proof is saved
under `.metal-cache/cliprec-proof.log` by the offline runner.

Resolution checks decode six frames at each of 1920 x 1080, 2560 x 1440 and
3840 x 2160 for both codecs, verify the hardware bitrate target within 1%
(measured error below 0.00001%), and cover wide aspect, no upscale and changes
applying only to the next clip. Container bitrates are reported separately:
synthetic color bars do not demand the full VBR target. Resource checks observe
zero pool/encoder handles after stop; the original code retains both. Injecting
119 encoder-drop callbacks advances the streak to 119 (original: 0); the 120th
stops and saves a decodable prefix. Negative runs restoring the old sizing and
bitrate separately fail at 3840 x 2160 versus 1920 x 1080 and 40 versus 10 Mb/s.

Still requires an in-game clip and solo timing runs: actual drawable callback
behavior, chase-camera visual fidelity, 4K60 throughput, mean/p99 frame time,
fps loss, memory, unexplained drop counts and finalization latency. No game
performance claim follows from the offline synthetic test.

The autodiscovered `cliprec_audio_test.py` pairs with `cliprec_audio_test.mm`.
It renders generated six-channel float PCM through the actual manual DirectSound
mixer and records its original PCM, host timestamps, rate and stream metadata as
the oracle. Synthetic drawables traverse `cliprec_present`, Metal conversion,
VideoToolbox and the production writer. Video and stereo audio are decoded
independently with AVAssetReader; markers at multiple video timestamps check
10 ms alignment, drift, channel order, sample rate, leading content and final
drain. The ten-second fixture checks the same timeline over a longer interval.
Relevant original recorder methods from `13984ab7` are pinned individually with
SHA-256 checks; that recorder produces 36 decoded video frames and zero audio
tracks against a requirement of one, with 32,896 generated mixer frames.

A diagnostic MOV/float-PCM writer control changes only the test's container and
audio output format so the timeline can be exercised when AAC services are
unavailable. It covers SDR/16F, HEVC/forced H.264, video drops, preroll before
video preparation, stop inside a PCM block, rate/device changes, empty/unshown
capture, quit, restart, stale callbacks and bounded queue pressure. This control
has decoded 600 video frames and 480,000 stereo samples over ten seconds, zero
measured marker offset, RMS error below 0.003 without clock changes, and no
container-end error. Under pressure a 36-frame valid prefix survives with an
explicit capture error; the observed queue peak was 262,113/262,144 frames.
Stamps jittered -5..+5 ms per block: normalized RMS 1.339511 and markers up to
147 ms with per-block stamps, 0.012352 (AAC 0.013138) and 0 ms placed by
position. An 8 s presentation pause overflowed the ring after 5.467 s before;
now the clip continues (peak 132,096/262,144, markers 0 ms after the pause).
Removed audio, +100 ms content skew, swapped channels, 44.1 kHz output and a
removed final drain are each compiled and executed as negative controls, then
repeated against AAC when that codec is available. The current PCM controls
reject all five: zero tracks, 100 ms marker offset, RMS 1.237, 44,100 Hz, and a
22.0 ms missing tail (tail RMS 0.654), respectively. These PCM results establish
neither AAC quality nor a real-device or listening result.

The startup control also presents a frame 50 ms before the first captured PCM.
The coverage guard leaves accepted frames and epoch at 0/0; removing it gives
1/1 and fails before the normal clip fixtures. This models queued playback
without altering the oracle's mixer timestamps or PCM.
