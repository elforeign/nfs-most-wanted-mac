#!/usr/bin/env python3
"""clip_check.py <clip.mp4> <recorded seconds> <scripted seconds> - judge a recorded clip (runtime/clip_smoke.sh).

Reads the container with macOS avmediainfo (the bundled FFmpeg has no MP4 demuxer) and decodes the audio with
afconvert. PASS needs: video H.264 or HEVC; audio AAC 48 kHz stereo; container and each track duration within one 60 Hz frame of the
span the recorder reported (its `cliprec: saved` line) and within 1 s of the scripted window; audio and video
tracks starting within 20 ms of each other; decoded audio RMS >= 0.001 (non-silent).
`judge()` is pure (avmediainfo text + RMS in, verdict out) so clip_check_test.py pins it offline.
Duration proof: original catches 0/6 short/long/missing track spans; fixed catches 6/6 (27/27 total checks).
"""
import math, os, re, struct, subprocess, sys, tempfile

FRAME = 1 / 60
# AAC encoder delay: 2112 priming samples (44.0 ms at 48 kHz) are trimmed by the audio edit list (media start 0.044 s)
# and the encoder's last 2112 samples are not flushed at stop, so the sound track ends exactly that much early
# (train-0959 clip: audio 14.543 s vs video 14.587 s). Allowed on the short side only.
AAC_DELAY = 2112 / 48000
START_SKEW = 0.020
WINDOW_SLACK = 1.0   # clips measured 0.06-0.43 s short of the window (4 runs): the recorder prepares encoder and writer before frame 1
MIN_RMS = 0.001


def parse(text):
    """avmediainfo text -> {'duration': s, 'tracks': [{'kind', 'format', 'rate', 'channels', 'start', 'duration'}]}."""
    out = {'duration': None, 'tracks': []}
    m = re.search(r'^Duration: ([0-9.]+) seconds', text, re.M)
    if m: out['duration'] = float(m.group(1))
    for block in re.split(r'^Track \d+: ', text, flags=re.M)[1:]:
        kind = block.split()[0]
        t = {'kind': kind, 'format': None, 'rate': None, 'channels': None, 'start': None, 'duration': None}
        m = re.search(r'Format: (.+?) \'', block)
        if m: t['format'] = m.group(1).strip()
        m = re.search(r'Sample rate: ([0-9.]+)', block)
        if m: t['rate'] = float(m.group(1))
        m = re.search(r'Channels per frame: (\d+)', block)
        if m: t['channels'] = int(m.group(1))
        m = re.search(r'^\s+Duration: ([0-9.]+) seconds', block, re.M)
        if m: t['duration'] = float(m.group(1))
        # segment table: index, media start, media duration, track start, track duration
        t_ = r'(\d+:\d+:[0-9.]+)'
        m = re.search(rf'^[ \t]+1[ \t]+{t_}[ \t]+{t_}[ \t]+{t_}[ \t]+{t_}', block, re.M)
        if m:
            h, mi, s = m.group(3).split(':')
            t['start'] = int(h) * 3600 + int(mi) * 60 + float(s)
        out['tracks'].append(t)
    return out


def judge(text, rms, recorded, scripted, replay=False):
    """Returns (ok, [reasons], summary)."""
    info = parse(text)
    bad = []
    video = [t for t in info['tracks'] if t['kind'] == 'Video']
    audio = [t for t in info['tracks'] if t['kind'] == 'Sound']
    dur = info['duration']
    if len(video) != 1: bad.append(f'{len(video)} video tracks')
    elif video[0]['format'] not in ('HEVC', 'H.264'): bad.append(f"video format {video[0]['format']}")
    if len(audio) != 1: bad.append(f'{len(audio)} audio tracks')
    else:
        a = audio[0]
        if a['format'] != 'MPEG-4 AAC': bad.append(f"audio format {a['format']}")
        if a['rate'] != 48000: bad.append(f"audio rate {a['rate']}")
        if a['channels'] != 2: bad.append(f"audio channels {a['channels']}")
        if video and video[0]['start'] is not None and a['start'] is not None:
            skew = abs(a['start'] - video[0]['start'])
            if skew > START_SKEW: bad.append(f'track starts {skew * 1000:.1f} ms apart')
        else: bad.append('track start missing')
        if rms is None or rms < MIN_RMS: bad.append(f'audio RMS {rms}')
    if dur is None: bad.append('no duration')
    else:
        if abs(dur - recorded) > FRAME: bad.append(f'duration {dur:.3f} s vs recorded {recorded:.3f} s')
        if abs(dur - scripted) > (FRAME if replay else WINDOW_SLACK): bad.append(f'duration {dur:.3f} s vs scripted {scripted:.1f} s')
    for t in video + audio:
        span = t['duration']
        if span is None: bad.append(f"{t['kind']} duration missing")
        elif not (-FRAME - (AAC_DELAY if t in audio and not replay else 0) <= span - recorded <= FRAME):
            bad.append(f"{t['kind']} duration {span:.3f} s vs recorded {recorded:.3f} s")
        if replay and (t['start'] is None or abs(t['start']) > FRAME):
            bad.append(f"{t['kind']} replay start {t['start']}")
    v = video[0]['format'] if video else '-'
    summary = f"{dur} s {v} + {len(audio)} audio" + (f" (RMS {rms:.4f})" if rms is not None else "")
    return not bad, bad, summary


def audio_rms(clip):
    """Decoded RMS of the clip's audio (afconvert to 16-bit WAV), None when there is no audio to decode."""
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, 'a.wav')
        if subprocess.run(['/usr/bin/afconvert', '-f', 'WAVE', '-d', 'LEI16', clip, w],
                          capture_output=True).returncode: return None
        raw = open(w, 'rb').read()   # WAVE_FORMAT_EXTENSIBLE (stereo layout), which the wave module refuses
        at, data = 12, b''
        while at + 8 <= len(raw):
            tag, size = raw[at:at + 4], struct.unpack('<I', raw[at + 4:at + 8])[0]
            if tag == b'data': data = raw[at + 8:at + 8 + size]; break
            at += 8 + size + (size & 1)
        n = len(data) // 2
        if not n: return None
        s = struct.unpack(f'<{n}h', data[:2 * n])
        return math.sqrt(sum(x * x for x in s) / n) / 32768


def main():
    clip, recorded, scripted = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    text = subprocess.run(['/usr/bin/avmediainfo', clip], capture_output=True, text=True).stdout
    ok, bad, summary = judge(text, audio_rms(clip), recorded, scripted, '--replay' in sys.argv[4:])
    print(('clip PASS: ' if ok else 'clip FAIL: ' + '; '.join(bad) + ': ') + summary)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
