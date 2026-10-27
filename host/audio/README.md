# port/host/audio: DirectSound 8 → CoreAudio 5.1

This is the host side of `DSOUND.dll` for the native port. The boot runtime keeps the COM objects, vtables and guest pointers, and calls this C API. This library owns the device and buffer state, the play and write cursors, and a mixer. The mixer sums every playing buffer into interleaved 6-channel float (FL FR FC LFE BL BR) and writes it to `media::AudioOutput` in `../../media`, which plays it as PCM 5.1, a stereo fold-down, Dolby Digital, Dolby Digital Plus or lossless Dolby TrueHD. It's our own code and contains no third-party source.

| File | |
|---|---|
| `dsound_host.h` | The C API (plain C, `extern "C"`) |
| `dsound_host.cpp` | Implementation (C++17) |
| `dstest.cpp` | Headless tests: `build/dstest`, plus `build/dstest device` for the muted CoreAudio runs and `build/dstest fake` for the output-device logic on media's fake HDMI TV (no audio) |
| `ctest.c` | Links the library from plain C, the way the runtime will |
| `build.sh` | Builds `build/libhostaudio.a`, `build/libmedia.a` (`media.cpp` compiled unchanged), `build/dstest` and `build/ctest` |

## Build and link

```bash
port/host/audio/build.sh && port/host/audio/build/dstest device
```

The runtime (C, clang) links:

```
port/host/audio/build/libhostaudio.a port/host/audio/build/libmedia.a
port/third_party/ffmpeg/lib/{libavformat,libavcodec,libswscale,libswresample,libavutil}.a
-lc++ -lz -framework AudioToolbox -framework CoreAudio -framework CoreFoundation
```

`libmedia.a` is only `port/media/media.cpp` compiled. If the runtime later links `media.cpp` itself for movies, it should drop `libmedia.a` so there's one copy.

## What the game actually does (speed.exe, EA `Snd`)

`speed.exe` imports `DSOUND.dll` #1 `DirectSoundCreate` (`00860f92`) and #6 `DirectSoundCaptureCreate` (`00860f8c`, voice capture in `FUN_008201ff`). All of its DirectSound use goes through Snd:

- **Probe** (`FUN_008205c5`): `DirectSoundCreate`, `SetCooperativeLevel(hwnd, DSSCL_PRIORITY)`, `GetCaps` (dwSize 0x60), primary buffer (`DSBUFFERDESC` size 0x24, flags `PRIMARYBUFFER`). The rate is `min(dwMaxSecondarySampleRate, 44100)`, so **44100**. It then tries `FUN_0082007e(n, 44100)` for 1, 2, 4 and 6 channels.
- **`FUN_0082007e(ch, rate)`**: primary `SetFormat`. For 4 or 6 channels that's `WAVEFORMATEXTENSIBLE` (16-bit, mask 0x33 or 0x3F, `KSDATAFORMAT_SUBTYPE_PCM`); for 1–2 channels it's plain PCM. Then **one secondary buffer** with the same format, flags `0x18000` (`GLOBALFOCUS | GETCURRENTPOSITION2`, no volume, pan or frequency control), and a size of 0.5 s (`rate*500/1000 & ~15` frames).
- **Start** (`FUN_00820762`): locks the whole buffer, zero-fills it, unlocks, then `Play(0, 0, DSBPLAY_LOOPING)`.
- **Streamer** (`FUN_00820907`, a thread at `THREAD_PRIORITY_TIME_CRITICAL` polling every 10 ms): calls `GetStatus` and `Restore`s if `BUFFERLOST`. It calls `GetCurrentPosition(NULL, &write)` (**the write cursor only**), then fills from its last position up to `(write + (30 ms + max poll interval) * rate) & ~15` frames. Locks are split at the buffer end by the game, so they never pass a second region (`Lock(..., NULL, NULL, 0)`). **If the write cursor doesn't change for 100 consecutive polls, Snd tears down and recreates the whole device.**

So in mode 3 the whole game is one 6-channel, 16-bit, 44.1 kHz streaming buffer, and the API is built around that case. Forcing mode 3 (`SetSnd8RenderMode` / `Snd::System::SetOutputMode(3)`) is the runtime's job. `DirectSoundCaptureCreate` should return `DSERR_NODRIVER` (0x88780078), which Snd's capture path handles.

## API

All calls are thread-safe. Results are HRESULT values (`DS_OK`, `DSERR_*`). Structs passed as `void *` are the raw Win32 layouts, so `MEM(guest_ptr)` passes straight through.

| COM method (vtable offset) | C function | Notes |
|---|---|---|
| `DirectSoundCreate`/`8` | `ds_device_create(cfg, &dev)` | `cfg` NULL = `ds_config_default` (env `NFS_AUDIO`=pcm51\|stereo\|dd\|ddp\|truehd\|null, default auto: `media::preferred_output_mode()`, Dolby TrueHD passthrough (lossless) when the device takes MAT, else Dolby Digital passthrough when it takes AC-3, else PCM 5.1 when its speaker layout has surrounds, else stereo; Dolby Digital Plus (`ddp`, E-AC-3 passthrough) only when chosen; `NFS_AUDIO_RATE`, `NFS_AUDIO_LATENCY`). Never fails for want of an output device (see "Following the output device") |
| `IDirectSound::Release` (0x08), final | `ds_device_release(dev)` | Stops the mixer; live buffer handles then return `DSERR_BUFFERLOST` until released |
| `CreateSoundBuffer` (0x0C) | `ds_device_create_buffer(dev, flags, bytes, wfx, mem, &buf)` | From `DSBUFFERDESC`: flags, `dwBufferBytes`, `MEM(lpwfxFormat)`. **`mem` = guest memory the runtime allocated** (`bytes` long; NULL for the primary). On failure the runtime frees `mem` |
| `GetCaps` (0x10) | `ds_device_get_caps(dev, dscaps)` | Software device: no hardware buffers, rates 100–200000 |
| `DuplicateSoundBuffer` (0x14) | `ds_device_duplicate_buffer(dev, src, &buf)` | Shares `src`'s memory: keep the guest memory alive until every duplicate is released |
| `SetCooperativeLevel` (0x18) | `ds_device_set_cooperative_level(dev, level)` | The `HWND` is ignored |
| `Compact` (0x1C), `Initialize` (0x28) | — | Runtime returns `DS_OK` (Initialize: `DSERR_ALREADYINITIALIZED`) |
| `Get/SetSpeakerConfig` (0x20/0x24) | `ds_device_get/set_speaker_config` | Default `DSSPEAKER_5POINT1`, whatever the output mode, so Snd always mixes 5.1 and we fold down |
| `IDirectSoundBuffer::Release` (0x08), final | `ds_buffer_release(buf)` | After it returns the mixer no longer reads `mem` |
| `GetCaps` (0x0C) | `ds_buffer_get_caps(buf, dsbcaps)` | `dwBufferBytes` = size rounded down to the block |
| `GetCurrentPosition` (0x10) | `ds_buffer_get_current_position(buf, &play, &write)` | Byte offsets; either pointer may be NULL |
| `GetFormat` (0x14) | `ds_buffer_get_format(buf, wfx, cap, &written)` | Primary default: 22050 Hz, 8-bit stereo (the DirectSound default) |
| `GetVolume`/`GetPan`/`GetFrequency` (0x18/0x1C/0x20) | `ds_buffer_get_volume/pan/frequency` | |
| `GetStatus` (0x24) | `ds_buffer_get_status(buf, &status)` | `PLAYING`, `LOOPING`, `LOCSOFTWARE`, `BUFFERLOST` |
| `Initialize` (0x28) | — | Runtime returns `DSERR_ALREADYINITIALIZED` |
| `Lock` (0x2C) | `ds_buffer_lock(buf, off, bytes, flags, &o1, &l1, &o2, &l2)` | Returns **offsets**; the runtime writes `guest_mem + o1` / `guest_mem + o2` to `ppvAudioPtr1/2`. `o2`/`l2` NULL: a wrapping lock is truncated at the end (as on Windows) |
| `Play` (0x30) | `ds_buffer_play(buf, dwFlags)` | The third argument; `DSBPLAY_LOOPING` |
| `SetCurrentPosition` (0x34) | `ds_buffer_set_current_position(buf, off)` | |
| `SetFormat` (0x38) | `ds_buffer_set_format(buf, wfx)` | Primary only, needs `DSSCL_PRIORITY`. With the auto rate it also sets the mix rate (below) |
| `SetVolume`/`SetPan`/`SetFrequency` (0x3C/0x40/0x44) | `ds_buffer_set_volume/pan/frequency` | Each needs its `DSBCAPS_CTRL*` flag (`DSERR_CONTROLUNAVAIL` otherwise). Primary volume = master gain |
| `Stop` (0x48) | `ds_buffer_stop(buf)` | |
| `Unlock` (0x4C) | `ds_buffer_unlock(buf, o1, l1, o2, l2)` | The runtime passes offsets (pointer − guest_mem). Validation only, because the mixer reads the memory directly |
| `Restore` (0x50) | `ds_buffer_restore(buf)` | |
| `IDirectSoundNotify::SetNotificationPositions` (0x0C) | `ds_buffer_set_notification_positions(buf, n, offsets, events)` | Needs `CTRLPOSITIONNOTIFY` and a stopped buffer. Events come back through `cfg.notify(user, event)`; the runtime does `SetEvent` |
| — | `ds_device_render(dev, pcm51, frames)` | Manual sink only (tests): pulls mixed output |
| — | `ds_device_get_stats(dev, &stats)` | Frames written and played, underruns, per-channel peak and RMS: the "samples come through" check for phase 5 |
| — | `ds_set_log(fn)` | Every output change (device created/released, output opened, kept, reopened, closed, device changes, underruns at most once a second) is a `dsound: ...` line on stderr and also goes to `fn` (the runtime: `events.log` in diagnostic mode) |

`QueryInterface`: `IDirectSound8` and `IDirectSoundBuffer8` can reuse the same objects. `IDirectSoundNotify` maps to the call above. `IDirectSound3DBuffer`, `IDirectSound3DListener`, `IKsPropertySet` (EAX) and the FX interfaces should return `E_NOINTERFACE`; Snd mixes in software.

`DsConfig`: `sink` (`DS_SINK_DEVICE` the default; `DS_SINK_NULL` is a real-time mixer thread paced by the wall clock, with no device; `DS_SINK_MANUAL` is the test clock), `mode` (PCM 5.1 / stereo / Dolby Digital / Dolby Digital Plus / TrueHD), `mix_rate` (0 = auto), `latency_frames` (default 1024; Dolby Digital (Plus) 3072, TrueHD 4096: its encoder holds up to ~1,600 frames), `block_frames` (256), `mute_device` (the device gets silence and the tap gets the real mix), `speaker_config`, `notify` and `tap` callbacks. Callbacks run on the mixer thread with no lock held. They must not call back into `SetFormat` or `ds_device_release`.

## Behavior

- **Clock and cursors.** The mixer keeps `latency_frames` queued in `AudioOutput` and mixes in blocks of `block_frames`. The **write cursor** is how far the mixer has consumed the buffer: the data in [play, write) has been taken, and the game may write anywhere else. That's the DirectSound contract, and why the game's memory can be read directly with no copy at `Unlock`. The **play cursor** is what's being heard now: the consumed position minus the output still queued. The device's played-frame count comes from the queue depth, snapshotted after each write and interpolated with the wall clock. The play cursor never moves backwards and never moves before the position `Play` started from, so it holds still until the first sample reaches the device. Both cursors move in real time. Measured on the device: 48,005 frames/s at 48 kHz; write − play ≈ 870 frames (PCM) or ≈ 2,900 (Dolby Digital, which includes the AC-3 frame).
- **Mix rate.** With `mix_rate = 0` (the default), the mixer starts at 48 kHz and follows the primary buffer's `SetFormat` rate. Snd sets 44.1 kHz, so its 6-channel stream reaches CoreAudio **bit-exact** (16-bit → float, gain 1, no resampling): lossless PCM 5.1. CoreAudio, or the device itself, handles 44.1 kHz. Dolby Digital (Plus) and TrueHD always mix at 48 kHz. The rate switch restarts the output, drops the queued audio and rebases every cursor.
- **Mixing.** Each playing buffer is decoded from 8-bit unsigned, 16/24/32-bit integer or 32-bit float. If its rate equals the mix rate it's copied with no interpolation (the main path, about 0.44 ms per second of 6-channel audio). Otherwise it's resampled with 4-point Catmull-Rom, from a 32.32 fixed-point position. Buffers are then routed and summed, and clamped to ±1 like DirectSound's saturating mix.
- **Channel mapping.** Mono plays at full level on FL and FR (DirectSound's centre pan is not −3 dB). Stereo goes to FL/FR. 6-channel maps 1:1 (mask 0x3F, or the default for 6 channels). Quad (0x33) goes to FL FR BL BR. For `EXTENSIBLE` masks, FLC/FRC go to the fronts, SL/SR to the backs and BC to both backs at −3 dB; top speakers are dropped. 8-channel 7.1 folds its side speakers into the backs.
- **Volume and pan.** Gain is 10^(v/2000) and −10000 is exact silence. Pan only attenuates, like DirectSound: pan > 0 attenuates left by pan/100 dB, pan < 0 attenuates right, and ±10000 is silence on the other side. It applies to mono and stereo buffers only; DirectSound rejects `CTRLPAN` on multichannel buffers, and so does this. The primary buffer's volume is a master gain. Gain changes ramp over one block (about 5 ms) so they don't click.
- **One-shot buffers** stop when their last sample has been *heard*, not just mixed. `GetStatus` stays `PLAYING` until then, the position rewinds to 0 (as Wine does), and `DSBPN_OFFSETSTOP` fires. `Stop` keeps the position at the write cursor, so `Play` resumes without repeating audio.
- **Notifications** fire when the play cursor passes an offset (every lap when looping), checked once per mixed block.
- **Lifetime.** Releasing the device stops the mixer. Buffers released later are fine, and the device memory is freed with the last one. Snd recreates the device on stalls, which works. Its output outlives it for 2 s (next point), and every output still open is closed at exit (`atexit`), so a passthrough gives its device back in PCM.
- **Following the output device.** On Windows the device never goes away under the game; on a Mac the output can (a TV asleep or switched off drops its HDMI audio device; headphones come and go). Snd tears its device down when the write cursor stalls for 100 polls (about 1 s), and if `DirectSoundCreate` then fails it keeps calling a null buffer: the "indirect call to 00000000 (return address 00820961)" crash, seen after a TV's HDMI audio device went away and came back. So the mixer never lets the cursors stall: an output that stops asking for audio for 0.5 s (`AudioOutput::stalled`) is dropped and the mixer runs on the wall clock (like the null sink, the mix dropped), while a background thread opens an output on the current default device (passthrough can take a second to switch formats) and the mixer swaps it in, rebasing the clock if the rate differs. A change of default device (CoreAudio listeners, `media::output_devices_generation`, debounced 0.4 s) reopens too, in the mode that suits the new device when the mode is automatic; a Dolby Digital (Plus) passthrough output ignores default-device changes (holding its device in hog mode makes macOS move the default elsewhere) and reopens only when its device goes away (`media::device_alive`) or stalls. A refused passthrough open is retried each second and falls back to stereo after three failures (TrueHD and Dolby Digital Plus: to the automatic choice without TrueHD, i.e. Dolby Digital on a device that takes AC-3, else PCM 5.1 / stereo; automatic mode keeps skipping TrueHD until a passthrough opens again). `ds_device_create` never fails for want of an output: it starts on the wall clock and attaches once a device takes it. `NFS_AUDIO_TEST_DROP=<s>[/<n>],...` simulates it: at `s` seconds after the device's creation the output stops taking audio and the next `n` opens fail (`0/<n>`: the opens at creation). Checked in a `BOOT_DIAG` race with `NFS_AUDIO_TEST_DROP=0/2,10/3,25` on an HDMI TV with passthrough: the device starts on the wall clock and attaches to passthrough, a 3-second outage (falls back to stereo after the third refused passthrough open) and a second drop (back to passthrough); the game's mix never stops (`stats.log` `mixrms`), Snd never recreates its device (3 `DirectSoundCreate`, all from its start-up probe), no crash.

- **Snd re-creating its device keeps the output.** Snd creates its DirectSound device twice at start-up (the probe `FUN_008205c5` at `008205E9` releases its device, then the start path at `008207FE` creates the real one, in the same frame) and again at every `Snd::System::ReInit` (`0081B841`, from `LoadAemsInGame` on every in-game load: the career load, a race load). On Windows the hardware doesn't notice. Here the release closed the output and the create opened a new one milliseconds later, and with passthrough that went wrong (seen at a career start: garbled sound through the TV): (1) while a passthrough holds the TV in hog mode macOS moves its default output elsewhere, so the automatic choice for the new device saw the built-in speakers and opened stereo; (2) `close()` gave the stream its format back asynchronously and the next passthrough open, milliseconds later, saved the still-encoded MAT format as "the format to restore", so the next close left the TV's stream in MAT (768 kHz) with hog mode off: PCM from the system mixer then plays as noise at a quarter of the rate (the story movie after the career start: its mixer consumed 656,384 frames in 54.66 s, 12,000 frames/s instead of 48,000; `movies.log`). Now a released device's working output is **parked** for 2 s, still running (encoder, queue, hog mode and all), and the next device created takes it over when it would open the same thing: the same mode (automatic: chosen automatically before), its device still there (passthrough: alive; PCM: still the default) and taking audio, and the same rate when `NFS_AUDIO_RATE` fixes one. A PCM output keeps its rate too (the game's 44.1 kHz), so the new device's `SetFormat` doesn't reopen it. Otherwise the parked output is closed (and, when it was a passthrough in automatic mode, the new device waits up to 0.5 s for macOS to make that device the default again before choosing); with no new device it's closed after 2 s. In `media`: a passthrough never saves an encoded format as the one to restore (it takes the stream's best 2-channel 48 kHz PCM format instead), `close()` waits (<= 1 s) for the restore to take before giving the device back, and a PCM open first sets an unheld device's stream that was left encoded back to PCM. `NFS_AUDIO_FAKE_DEVICE=1` (tests) opens every output on a fake HDMI TV (takes MAT, AC-3, E-AC-3, drained at the device rate; while a passthrough holds it the default output is "fake speakers", as on macOS) and never touches CoreAudio; it turns `NFS_AUDIO=null` into the automatic choice, so `runtime/run_guarded.sh ... NFS_AUDIO_FAKE_DEVICE=1` runs the device logic in a real game unattended. Checked: `build/dstest fake` (start-up double create, a mid-session re-creation, PCM 5.1 kept at 44.1 kHz, another mode not taken, the 2 s close, a device that goes away and refuses two opens: 57 checks), and a guarded race 15.1.1 on the fake TV: one output open for the whole run, devices 2 and 3 (start-up, race load) kept it, mix live in `stats.log`, no underruns.

                
                                                                                                                 
                                                                                                                    
                           
                 

## Test results (2026-10-18, `build/dstest device`, Apple Silicon Mac, default output device, muted)

`8101 checks, 0 failed`; earlier full runs during development were clean too. The same tests under AddressSanitizer + UBSan are clean. Under ThreadSanitizer there are no race reports (only the speed check fails, from instrumentation).

| Test | Result |
|---|---|
| Formats, caps | PCM / EXTENSIBLE / float, 1–8 ch, 8/16/24/32-bit accepted; 12-bit, 9 ch, 50 Hz, ADPCM, block mismatch, pan on 6 ch rejected. DSCAPS/DSBCAPS sizes checked. Primary: default 22050/8/2, `SetFormat` needs PRIORITY, EXTENSIBLE 6-ch round-trips byte for byte |
| Lock/Unlock | Wraparound gives two regions (984+12 / 0+24), `ENTIREBUFFER`, `FROMWRITECURSOR`, out-of-range rejected, NULL second region truncated |
| 6-ch 48 kHz looping stream, manual clock | 480,225 frames × 6 channels **bit-exact**, through 1,234 locks (40 wrapping), with random render sizes |
| Volume / pan / mapping | −6 dB, pan ±1000/2500/10000, −100 dB = 0, stereo pan, 6-ch 1:1 exact and −20 dB, master −6 dB, sum, clamp, duplicate buffer, 8-bit, quad, one-block ramp |
| Resampling (1 kHz sine, 0.5 → 48 kHz) | 22050 Hz: SNR **71 dB**; 44100: **87 dB**; 32000: 82 dB; 11025: 53 dB. Amplitude within 0.1 %; cursor after 0.5 s exact (11025 / 22050 / 16000 frames). `SetFrequency(2×)` doubles the speed |
| One-shot, notifications | Content exact, then silence. Events fire in order (250, 700, stop; and every lap when looping). Rewinds to 0. Device released before its buffers gives `BUFFERLOST` |
| Auto rate | Primary EXTENSIBLE 6-ch 44.1 kHz → mix at 44.1 kHz, the stream bit-exact |
| Real time, null sink, 3 s | Game thread polls every 1–10 ms: cursor **48,000 frames/s**, never backwards, 144k frames bit-exact, 0 underruns |
| **Snd streamer replica** (the code above: write cursor only, 10 ms polls, 30 ms + interval lead, split locks), null sink and **CoreAudio device** | 44.1 kHz mix, about 112k frames bit-exact each, 0 underruns, write cursor unchanged for at most 1 poll in a row (Snd restarts at 100) |
| Real time on the device, muted | PCM 5.1: 48,005 frames/s, 145k frames bit-exact; stereo: 47,963 / 72k; Dolby Digital: 48,003 / 75k; 0 underruns |
| Thread stress | 4 threads create, play, lock, change volume/pan/frequency/position and release buffers at random for 1.5 s against the running mixer: no errors, no TSan reports |
| Cost | 1 s of output: 0.44 ms (6-ch, 48 kHz direct), 0.56 ms (6-ch resampled) |
| C link | `ctest.c` (plain C) links both archives and plays a 6-ch buffer |

"Bit-exact" means that the tap (the mixed output before the mode conversion) equals the 16-bit samples written by the game, divided by 32768. The device then gets those floats in PCM 5.1 mode, or silence in the muted runs.

## Known gaps

- **Nobody has listened to it.** All checks are numeric, as planned. The first listening test should be PCM 5.1 with the game in mode 3.
- **Stop and SetCurrentPosition don't flush** the output queue, so up to about 20 ms (1024 frames) keeps playing after `Stop`. That's inaudible for Snd, which never stops its stream.
- **No 3D, EAX or FX**: `CTRL3D`/`CTRLFX` flags are accepted and ignored, and the interfaces are left to `E_NOINTERFACE`. `LOCHARDWARE`/`LOCSOFTWARE`, `STATIC` and the focus flags are accepted with no effect: the sound is always audible, even when the window loses focus. `DSSCL_WRITEPRIMARY` (writing the primary buffer) isn't supported; `Lock` on the primary returns an error.
- The play cursor's precision is the device's callback chunk (about 10 ms), smoothed with the wall clock. Snd only reads the write cursor.
- Low-rate sources resample with Catmull-Rom: 53 dB SNR on 1 kHz from 11 kHz. Snd's path doesn't resample.
- At startup the mixer takes about 512 frames (12 ms) of the zero-filled buffer before Snd's first poll writes them, so the first 12 ms of the game's audio is silent. Windows does the same, since the write cursor jumps ahead right after `Play`.

## AudioOutput changes I'd like (port/media, not edited here)

1. **A real-time-safe queue.** The render callback takes a `std::mutex` that's shared with `write()`, and `write()` grows a `std::deque` (it allocates). A single-producer/single-consumer ring buffer of floats (and of bytes for Dolby Digital) would remove the priority inversion and allocation risk on the CoreAudio thread. This matters more now that the mixer writes every ~5 ms.
2. **`uint64_t played_frames()`** (frames the render callback has consumed, plus the callback's `AudioTimeStamp` host time). That would give exact play cursors instead of queue depth + wall clock, and let the mixer pace itself on the device clock.
3. **`flush()`** to drop queued audio. That would make `Stop`/`SetCurrentPosition` immediate, and let the rate switch keep the same unit instead of re-creating `AudioOutput`.
4. ~~Dolby Digital frames still in the encoder.~~ Done: the mixer adds `AudioOutput::pending_frames()` (the encoder's held frames: < 1,536 for AC-3 / E-AC-3, up to ~1,600 for TrueHD) instead of `written % 1536`, which was also wrong after a reopen.
5. (Already in media's README) Dolby Digital to a receiver needs the device's physical format set to `kAudioFormat60958AC3` in exclusive mode.
