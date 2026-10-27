# port/media: movies and surround audio

FFmpeg 7.1.1 (static build in `../third_party/ffmpeg`, source `../third_party/src`, tarball SHA-256 `733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1`) plus CoreAudio. Build: `./build.sh` → `mediatest`. FFmpeg itself: `./build_ffmpeg.sh` (the pinned configure line; since Dolby Digital Plus it includes the `eac3` encoder, decoder and demuxer, and since TrueHD the `truehd` encoder and decoder, which the first build lacked: rebuild `third_party/ffmpeg` with it, then `host/audio/build.sh` and `runtime/build.sh`; since 2026-10-25 also the `xma1` decoder (part of `wmaprodec.c`) for the X360 sound banks, `runtime/x360abk.c`).

| Class | What it does |
|---|---|
| `MovieDecoder` | X360 WMV3 + WMA Pro 5.1 or PC EA VP6 + EA ADPCM → BGRA frames + interleaved float audio, resampled to 48 kHz, in the order FL FR FC LFE BL BR (stereo sources fill FL/FR only) |
| `Ac3Spdif` | 6-ch float → FFmpeg AC-3 (640 kbit/s) → IEC 61937-3 bursts (6,144 bytes per 1,536 samples), or E-AC-3 (`open_eac3`, 1,024 kbit/s) → IEC 61937-3 E-AC-3 bursts (24,576 bytes per 1,536 samples, sent at 192 kHz) |
| `movie_api.h` | Plain C access to `MovieDecoder` for the runtime (`runtime/movies.c`), with per-stream handles: the X360 WMVs store audio ~1.4 s later in the file than its video, so a player opens one video-only and one audio-only handle (`media_movie_pump_audio`) |
| `TrueHdMat` | 6-ch float → 24-bit → FFmpeg TrueHD (lossless, experimental encoder) → MAT frames by FFmpeg's `spdif` muxer (61,440 bytes per 960 samples, sent at 768 kHz) |
| `AudioOutput` | CoreAudio default output: **PCM 5.1** (6-ch float, `kAudioChannelLayoutTag_MPEG_5_1_A`; lossless, default), **Stereo** (ITU fold-down), **Dolby Digital** (the IEC 61937 stream as 2-ch 16-bit), **Dolby Digital Plus** and **Dolby TrueHD** (passthrough only). `open_offline` / `render_offline`: the same queue and callback code with no device (tests) |

## Verified (2026-10-18, `mediatest`)

- `decode x360/Movies/storyfmv_bla134_english_ntsc.wmv`: 955 frames at 1280×720 and 33.66 s of 6-channel audio, with sound on every channel (RMS FL 0.057, FR 0.115, FC 0.142, LFE 0.020, BL/BR 0.018), so the soundtrack is discrete 5.1. The same scene from the PC VP6 is 1024×512, and its audio has FC/LFE/BL/BR at 0.
- `ac3` on the same movie: 1,051 bursts, which FFmpeg reads back (`ffprobe -f spdif`) as AC-3 5.1, 48 kHz, 640 kbit/s, 33.63 s, and decodes without errors.
- `play … pcm51|stereo|dd 1 mute`: all three output modes open the default device and run (silent test; not listened to).

## Dolby Digital Plus (E-AC-3), 2026-10-20 (verified offline only; not listened to yet)

- **Transport.** An HDMI device that supports E-AC-3 lists `cec3` at 192 kHz, 2 ch, 16-bit, 6,144 frames per packet (and `cac3` at 48 kHz for AC-3; also `cc+3` at 128-192 kHz and the MAT formats `mtc+` / `mtct` at 768 kHz, unused). IEC 61937-3 carries E-AC-3 at 4x the audio rate: one 24,576-byte burst per 1,536-sample frame, `Pc` = 0x0015 (no bsmod bits), `Pd` = payload length in **bytes** (AC-3: bits). This is what FFmpeg's `spdifenc.c` (`spdif_header_eac3`: `pkt_offset = 24576`, `length_code = hd_buf_filled`, frames gathered until 6 audio blocks) writes and its `spdifdec.c` reads (`IEC61937_EAC3`: offset 24576, Pd << 3 for bits). Passthrough works like Dolby Digital's (hog mode, mixing off, the stream switched to `cec3`, an IOProc) and follows device changes the same way; there's no mixer fallback (a 192 kHz bitstream through the mixer would be noise). Closing a passthrough gives the stream back a PCM format and waits (<= 1 s) for the switch before releasing hog mode; an encoded format found at open (left by an earlier passthrough) is never saved as the one to restore, and a PCM open sets an unheld device's stream that was left encoded back to PCM: a stream left in MAT plays the system mixer's PCM as noise at a quarter of the rate (2026-10-20).
- **Bit rate: 1,024 kbit/s**, the highest E-AC-3 rate with 6-block (1,536-sample) frames at 48 kHz: `frmsiz` is 11 bits, so a frame holds at most 2,048 words = 4,096 bytes per 32 ms. FFmpeg's encoder switches to shorter frames above it (`ac3enc.c`: `max_br = 2048 * sample_rate / frame_samples * 16`), which a burst would then have to gather. 60 % more than Dolby Digital's 640 kbit/s maximum, the same frame timing and encoder delay (256 samples); the burst still has 20,472 bytes of padding.
- **Checked offline** (`mediatest ... --out`, the bytes the IOProc would send; `mediatest spdifcheck` checks every burst and decodes through FFmpeg's `spdif` demuxer; `spdif_verify.py` compares with the source), Dolby Digital as the control:

| Stream | Bursts | Headers | Per channel |
|---|---|---|---|
| `speakers` DD | 488 × 6,144 B, gapless | Pc 0x0001, Pd 20,480 bits, 0 errors | names on their own channel only (others ≤ −100 dB), level ±0.00 dB, delay 256 for every segment, LFE 45.00 Hz: PASS |
| `speakers` DD+ | 488 × 24,576 B, gapless | Pc 0x0015, Pd 4,096 bytes, 0 errors | the same (others ≤ −104 dB): PASS |
| `tones` 20 s, DD / DD+ | 625 each (= 960,000 / 1,536) | 0 errors | levels ±0.00 dB, crosstalk ≤ −86 dB, phase continuous per frame (no lost or repeated frame), SNR 92-96 dB (LFE 76): PASS |
| X360 `attract_movie` 5.1 | 1,919 each | 0 errors | waveform SNR DD 29-36 dB → DD+ 38-45 dB |
| X360 `storyfmv_bla134` 5.1 | 1,051 each | 0 errors | DD 32-38 dB → DD+ 43-49 dB |

```sh
media/speaker_wavs.sh /tmp/spk                                  # the spoken names + 45 Hz LFE tone
media/mediatest speakers /tmp/spk ddp --out /tmp/s.spdif        # no device: the IOProc's byte stream to a file
media/mediatest spdifcheck /tmp/s.spdif /tmp/s.f32
tools/.venv/bin/python -I media/spdif_verify.py speakers /tmp/s.f32 /tmp/spk
media/mediatest speakers /tmp/spk ddp                           # on the real device (the user, listening)
```

## Dolby TrueHD 5.1, lossless (MAT), bit-exact offline

                                                                                                                                  

- **Transport.** The same device lists `mtct` at 768 kHz, 2 ch, 16-bit, 15,360 frames per packet: one MAT frame. MAT is the HDMI high-bit-rate transport for Dolby TrueHD (768 kHz = 4 x 192 kHz, the 8-channel 192 kHz link carried as one stream). FFmpeg's `spdifenc.c` (`spdif_header_truehd`) packs it: a 61,424-byte MAT frame per 61,440-byte burst (Pc 0x0016, Pd 61,424 bytes), start / middle / end codes at 0 / 30,708 / 61,408, each 40-sample access unit placed at 2,560 bytes per 40 samples of its input timing, zero padding between. `TrueHdMat` runs FFmpeg's muxer into memory rather than re-implementing that. Passthrough as for the other bitstreams, on `mtct`; no mixer fallback. (`mtc+` and `cc+3` are listed too; unexplored.)
- **Encoder.** FFmpeg's `truehd` encoder (flagged experimental) at 24 bits: the game's 16-bit audio and the mixer's float mix (clamped to ±1, rounded to 24 bits) reach the decoder bit for bit. Access units of 40 samples, a major sync every 16 (the encoder holds those 640 samples), 3.6-4.1 Mbit/s on the X360 movies (largest access unit 768 bytes; a MAT slot holds 2,560). 20 s of 5.1 encode in 0.2 s.
- **Checked offline** (`mediatest spdifcheck` unwraps MAT itself, since FFmpeg's `spdif` demuxer doesn't read it, checks the codes and that the access units' input timing steps by 40, and decodes with FFmpeg's `truehd`; `spdif_verify.py --truehd`):

| Stream | Bursts | Result |
|---|---|---|
| `speakers` | 780 × 61,440 B, 0 errors; 18,720 access units, continuous | **bit-exact**: 4,492,800 of 4,492,800 24-bit samples identical; each name only on its own channel; LFE 45.00 Hz; delay 0 |
| `tones` 20 s | 999 (+ 640 samples held by the encoder), 0 errors | **bit-exact**: 5,754,240 of 5,754,240 |
| X360 `attract_movie`, `storyfmv_bla134` | 3,070 / 1,682, 0 errors | 4.06 / 3.58 Mbit/s; levels ±0.00 dB against the 16-bit reference |

```sh
media/mediatest speakers /tmp/spk truehd --out /tmp/t.spdif && media/mediatest spdifcheck /tmp/t.spdif /tmp/t.f32
tools/.venv/bin/python -I media/spdif_verify.py speakers /tmp/t.f32 /tmp/spk --truehd
media/mediatest speakers /tmp/spk truehd                          # on the real device (the user, listening)
```

                                                                                                                                                                                                                                                                                                                                                                                 

Switching into TrueHD can take the HDMI receiver a few seconds to lock (the measured first switch exceeded a 15 s test). The `speakers` test leads TrueHD with 6 s of silence; `--lead <s>` changes it.

## Not done yet

- **Dolby Digital to a receiver**: the bitstream is correct, but bit-exact delivery needs the device's physical format set to `kAudioFormat60958AC3` (HAL, exclusive/hog mode). The default output unit used here may apply volume or resampling, which would corrupt the stream. PCM 5.1 is unaffected.
- DTS isn't included (the supported encoded formats are PCM, Dolby Digital, Dolby Digital Plus and TrueHD).
- Dolby Digital Plus is checked by tests only; TrueHD is confirmed by listening (channels and LFE correct). Left: a race with each, and which becomes the automatic choice.
                
                                                                                                                      
                 
