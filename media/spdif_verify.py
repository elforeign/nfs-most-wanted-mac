"""Checks a decoded IEC 61937 round trip channel by channel (no audio device involved).

    mediatest speakers <spk> dd|ddp --out s.spdif && mediatest spdifcheck s.spdif s.f32
    python -I media/spdif_verify.py speakers s.f32 <spk> [1-6]
    mediatest tones dd|ddp 20 --out t.spdif && mediatest spdifcheck t.spdif t.f32
    python -I media/spdif_verify.py tones t.f32 20
    python -I media/spdif_verify.py movie m.f32 <audio_51.wav from `mediatest decode`>
    add --truehd for a TrueHD (MAT) stream: 960-sample bursts, no encoder delay, and every sample must come back
    bit for bit (24-bit).

<x>.f32 is the decoder's interleaved float output in FL FR FC LFE BL BR order. The source signal is rebuilt the way
mediatest wrote it, so every sample can be compared: the encoder delay (FFmpeg's AC-3 / E-AC-3 encoders: 256 samples)
must be the same for every segment (a dropped or repeated 1536-sample frame would shift everything after it), each
name must land on its own channel only, at its own level, and the LFE file must come out at 45 Hz.
"""
import struct
import sys

import numpy as np

NAMES = ["FL", "FR", "FC", "LFE", "BL", "BR"]
RATE = 48000
FRAME = 1536       # samples per burst (AC-3 / E-AC-3); TrueHD: 960
HOLD = 0           # samples an encoder keeps back beyond the open burst (TrueHD: its 16-unit restart interval, 640)
DELAY = 256        # FFmpeg's AC-3 / E-AC-3 encoder delay; TrueHD: 0
LOSSLESS = False


def expected_frames(total):
    return (total - HOLD) // FRAME * FRAME


def exact(src, dec, quantise):
    """Samples that differ between the source quantised to 24 bits and the decoded stream (both as integers)."""
    a = np.rint(quantise(src) * 8388608.0).clip(-8388608, 8388607)
    b = np.rint(dec * 8388608.0)
    return int(np.count_nonzero(a != b)), int(np.max(np.abs(a - b))) if len(a) else 0


def load_f32(path):
    a = np.fromfile(path, dtype="<f4")
    return a.reshape(-1, 6).astype(np.float64)


def load_wav_mono16(path):
    b = open(path, "rb").read()
    p = 12
    while p + 8 <= len(b):
        tag, n = b[p:p + 4], struct.unpack("<I", b[p + 4:p + 8])[0]
        if tag == b"data":
            return np.frombuffer(b[p + 8:p + 8 + n], dtype="<i2").astype(np.float64) / 32768.0
        p += 8 + n + (n & 1)
    raise SystemExit(f"no data chunk in {path}")


def load_wav6(path):
    b = open(path, "rb").read()
    p = 12
    while p + 8 <= len(b):
        tag, n = b[p:p + 4], struct.unpack("<I", b[p + 4:p + 8])[0]
        if tag == b"data":
            return np.frombuffer(b[p + 8:p + 8 + n], dtype="<i2").astype(np.float64).reshape(-1, 6) / 32768.0
        p += 8 + n + (n & 1)
    raise SystemExit(f"no data chunk in {path}")


def db(x):
    return 20 * np.log10(max(x, 1e-12))


def rms(x):
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def fast_lag(src, dec, lo=-4096, hi=4096):
    """lag L in [lo, hi] maximising sum src[t] * dec[t + L] (dec is late by L samples), by FFT."""
    n = 1
    while n < len(src) + len(dec):
        n *= 2
    c = np.fft.irfft(np.fft.rfft(dec, n) * np.conj(np.fft.rfft(src, n)), n)
    lags = np.r_[np.arange(0, hi + 1), np.arange(lo, 0)]
    vals = np.r_[c[0:hi + 1], c[n + lo:n]]
    return int(lags[int(np.argmax(vals))])


def peak_hz(x):
    w = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * w, 1 << 20))
    return float(np.argmax(spec) * RATE / (1 << 20))


def speakers(dec, spk_dir, only):
    chans = [only - 1] * 3 if only else list(range(6))
    src = [np.zeros((RATE, 6))]
    segs = []                                       # (channel, start, length of the name/tone)
    at = RATE
    for ch in chans:
        mono = load_wav_mono16(f"{spk_dir}/{ch}.wav")
        n = len(mono) + RATE * 3 // 2
        block = np.zeros((n, 6))
        block[:len(mono), ch] = mono
        src.append(block)
        segs.append((ch, at, len(mono)))
        at += n
    src = np.concatenate(src)
    total = len(src)
    want = expected_frames(total)
    print(f"source {total} frames -> {want // FRAME} bursts of {FRAME} expected "
          f"(the last {total - want} stay in the encoder); decoded {len(dec)} = {len(dec) / FRAME:.2f} bursts")
    ok = len(dec) == want
    # One delay for the whole stream from the speech channels (not periodic, so unambiguous), then each segment's own
    # best lag within +-500 samples of it (less than half a 45 Hz period; a lost or repeated frame moves 1536).
    m = min(len(src), len(dec))
    speech = [c for c in set(chans) if c != 3] or [3]
    G = fast_lag(src[:m, speech].sum(axis=1), dec[:m, speech].sum(axis=1), -4096, 4096)
    lags = []
    print(f"stream delay {G} samples (expected {DELAY})")
    print("seg ch   lag  level(dB)  SNR(dB)  worst other channel (dB rel. target)  peak Hz")
    for i, (ch, start, n) in enumerate(segs):
        s = src[start:start + n, ch]
        win = dec[start + G - 500:start + G + n + 500, ch]
        L = G - 500 + fast_lag(s, win, 0, 1000)
        lags.append(L)
        d = dec[start + L:start + L + n]
        if len(d) < n:                              # the tail fell into the encoder's last partial frame
            n = len(d)
            s = s[:n]
        tgt = d[:, ch]
        level = db(rms(tgt)) - db(rms(s))
        snr = db(rms(s)) - db(rms(tgt - s))
        others = [(db(rms(d[:, c])) - db(rms(tgt)), NAMES[c]) for c in range(6) if c != ch]
        worst = max(others)
        hz = peak_hz(tgt) if ch == 3 else float("nan")
        print(f"{i:3d} {NAMES[ch]:3s} {L:5d}  {level:+8.2f}  {snr:7.1f}  {worst[0]:+8.1f} ({worst[1]})"
              + (f"  {hz:6.2f}" if ch == 3 else ""))
        ok &= abs(level) < 0.5 and worst[0] < -40 and (ch != 3 or abs(hz - 45) < 0.5)
    ok &= len(set(lags)) == 1 and G == DELAY
    print(f"encoder delay per segment: {sorted(set(lags))} (one value = no dropped or repeated frames)")
    if LOSSLESS:
        n = min(len(src), len(dec) - G)
        diff, worst = exact(src[:n], dec[G:G + n], lambda x: x)
        print(f"bit-exact: {6 * n - diff} of {6 * n} samples identical (24-bit), largest difference {worst} LSB")
        ok &= diff == 0
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


HZ = [440, 550, 660, 45, 770, 880]
AMP = [0.25, 0.25, 0.25, 0.5, 0.25, 0.25]


def tones(dec, seconds):
    total = int(seconds * RATE)
    want = expected_frames(total)
    t = np.arange(total)
    src = np.stack([AMP[c] * np.sin(2 * np.pi * HZ[c] * t / RATE) for c in range(6)], axis=1)
    # the six tones together repeat every 0.2 s (9,600 samples), so a 0..4096 search over their sum is unambiguous
    L = fast_lag(src[:RATE].sum(axis=1), dec[:RATE + 4096].sum(axis=1), 0, 4096)
    print(f"decoded {len(dec)} frames, expected {want} ({want // FRAME} bursts of {FRAME}); encoder delay {L}")
    ok = len(dec) == want and L == DELAY
    print("ch     Hz  level(dB)  SNR(dB)  crosstalk(dB)  block phase spread (deg)  worst block SNR")
    for c in range(6):
        d = dec[L:, c]
        s = src[:len(d), c]
        skip = FRAME                               # the first frame holds the encoder's start-up transient
        d, s = d[skip:], s[skip:]
        level = db(rms(d)) - db(rms(s))
        snr = db(rms(s)) - db(rms(d - s))
        tt = (np.arange(len(d)) + skip) / RATE
        # crosstalk: the strongest other channel's tone found in this channel, relative to this channel's own tone
        def amp(x, hz):
            w = 2 * np.pi * hz * tt[:len(x)]
            return 2 * np.hypot(np.dot(x, np.sin(w)), np.dot(x, np.cos(w))) / len(x)
        leak = max(db(amp(d, HZ[k])) - db(AMP[c]) for k in range(6) if k != c)
        # per 1536-sample block: phase of the fitted sine (a dropped / repeated frame jumps it)
        phases, block_snr = [], []
        B = 1536                                   # analysis block (independent of the codec's burst size)
        for b0 in range(0, len(d) - B + 1, B):
            seg = d[b0:b0 + B]
            w = 2 * np.pi * HZ[c] * tt[b0:b0 + B]
            sc, cc = np.dot(seg, np.sin(w)), np.dot(seg, np.cos(w))
            phases.append(np.degrees(np.arctan2(cc, sc)))
            block_snr.append(db(rms(s[b0:b0 + B])) - db(rms(seg - s[b0:b0 + B])))
        ph = np.unwrap(np.radians(phases))
        spread = float(np.degrees(np.ptp(ph)))
        print(f"{NAMES[c]:3s} {HZ[c]:6d}  {level:+8.2f}  {snr:7.1f}  {leak:+10.1f}  {spread:12.2f}  {min(block_snr):12.1f}")
        if not LOSSLESS:   # approximate checks for the lossy codecs; a lossless stream must instead be bit-exact (below)
            ok &= abs(level) < 0.5 and spread < 5 and min(block_snr) > 20 and leak < -60 and abs(peak_hz(d[:RATE * 2]) - HZ[c]) < 0.5
    if LOSSLESS:   # mediatest computes (float)(amp * sin(...)); the encoder rounds that float to 24 bits
        n = len(dec) - L
        diff, worst = exact(src[:n], dec[L:L + n], lambda x: x.astype(np.float32).astype(np.float64))
        print(f"bit-exact: {6 * n - diff} of {6 * n} samples identical (24-bit), largest difference {worst} LSB")
        ok &= diff == 0
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def movie(dec, wav):
    src = load_wav6(wav)
    L = fast_lag(src[RATE:RATE * 5].sum(axis=1), dec[RATE - 4096:RATE * 5 + 4096].sum(axis=1), 0, 8192) - 4096
    n = min(len(src), len(dec) - L) // FRAME * FRAME
    print(f"source {len(src)} frames, decoded {len(dec)}, delay {L}, comparing {n}")
    print("ch   src RMS(dBFS)  level(dB)  SNR(dB)")
    for c in range(6):
        s, d = src[:n, c], dec[L:L + n, c]
        print(f"{NAMES[c]:3s}  {db(rms(s)):10.1f}  {db(rms(d)) - db(rms(s)):+8.2f}  {db(rms(s)) - db(rms(d - s)):7.1f}")
    if LOSSLESS:   # the source WAV is the 16-bit copy of what mediatest encoded (24-bit), so not bit-exact here
        print("(TrueHD: the reference WAV is 16-bit, so the SNR is bounded by its own rounding)")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--truehd" in a:
        a.remove("--truehd")
        FRAME, HOLD, DELAY, LOSSLESS = 960, 640, 0, True
    if len(a) >= 3 and a[0] == "speakers":
        sys.exit(speakers(load_f32(a[1]), a[2], int(a[3]) if len(a) > 3 else 0))
    if len(a) == 3 and a[0] == "tones":
        sys.exit(tones(load_f32(a[1]), float(a[2])))
    if len(a) == 3 and a[0] == "movie":
        sys.exit(movie(load_f32(a[1]), a[2]))
    print(__doc__)
    sys.exit(2)
